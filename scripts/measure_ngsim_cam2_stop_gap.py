"""Visible stop gap on the Lankershim approaches of camera 2: lead-vehicle fronts read by hand
against the painted limit lines (DATA_REGISTER.md §14.6). No time sync is needed.

    uv run --frozen python scripts/measure_ngsim_cam2_stop_gap.py select
    uv run --frozen python scripts/measure_ngsim_cam2_stop_gap.py report

`select` scans the source video every 2 s for frames in which at least two through lanes of an
approach hold a stationary vehicle within ZONE_PX of the limit line, keeps up to FRAMES_PER_ARM
of them at least MIN_SEPARATION_S apart, and writes 5x crops with a pixel grid (no markers that
could anchor the reading) plus an automatic front estimate per lane, used only as a cross-check.

`report` reads the hand clicks (data/geometry/ngsim-lankershim-cam2/v2/stop_gap_clicks.json:
per frame and lane, the front and rear of the lead vehicle in source pixels) and computes:
- gap in pixels: front to the limit line, along the lane (positive = short of the line);
- gap in metres with two along-road scales: (a) the clicked lead-vehicle lengths against the
  NGSIM median length of 15.8 ft (4.82 m), independent of the registration; (b) the ECC
  registration's local scale (analysis.json), whose along-road scale is what §14.6 questions;
- the NGSIM queue-head gaps from analysis.json for the same lanes, for comparison.

Shadows (sun from the east-north-east) fall ahead of vehicles on the north approach; fronts are
read on the vehicle body, not its shadow. Outputs go to data/processed/ngsim-cam2-stop-gap/
(Git-ignored; crops contain CC BY-SA 4.0 video pixels). Never overwrites.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.evidence import ImageGeometry, ImageLane

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "data/raw/ngsim-lankershim/camera2/lankershim-camera2-0830am-0845am.avi"
VIDEO_SHA256 = "04ef7435be3a107e7321900c384bb1ee7703ce753a122559cd2937fef341f530"
BACKGROUND = ROOT / "data/processed/ngsim-cam2-analysis/background.png"
BACKGROUND_SHA256 = "4a1668e4aa243e771d5242aae62561966ead8ee804878146d39e6009dc2a5e10"
ANALYSIS = ROOT / "data/processed/ngsim-cam2-analysis/analysis.json"
GEOMETRY = ROOT / "data/geometry/ngsim-lankershim-cam2/v2/image_geometry.json"
CLICKS = ROOT / "data/geometry/ngsim-lankershim-cam2/v2/stop_gap_clicks.json"
OUT = ROOT / "data/processed/ngsim-cam2-stop-gap"
FPS = 10000000 / 999999  # ffprobe, DATA_REGISTER.md §10

STEP_FRAMES = 20  # scan every 2 s
ZONE_PX = 40  # look for the lead vehicle within this distance of the limit line
DIFF = 35  # grey levels from the background that count as a vehicle (as in the analysis)
OCCUPIED, STILL = 0.4, 0.05  # zone share differing from background; share changing over 1 s
FRAMES_PER_ARM, MIN_SEPARATION_S = 6, 60.0
STILL_PATCH = 0.1  # share of a clicked vehicle's front patch allowed to change over 1 s
NGSIM_LENGTH_M = 15.8 * 1200 / 3937  # median v_length of the 08:30-08:45 rows (§14.2)
THROUGH = {"N": ("N_SB3", "N_SB2", "N_SB1"), "S": ("S_NB1", "S_NB2", "S_NB3")}
CROP = {"N": (340, 88, 480, 205), "S": (40, 180, 175, 292)}  # u0, v0, u1, v1
ZOOM = 5


class LaneClick(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    front_px: tuple[float, float]
    rear_px: tuple[float, float] | None = Field(None, description="None if hidden")
    note: str = ""


class FrameClicks(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    frame: int = Field(ge=0)
    arm: str = Field(pattern="^[NS]$")
    lanes: dict[str, LaneClick]


class Clicks(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reader: str
    date: str
    method: str
    source_video_sha256: str
    frames: tuple[FrameClicks, ...]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lane_axis(lane: ImageLane) -> tuple[np.ndarray, np.ndarray]:
    """Lane centre at the limit line and the unit direction of travel (towards the junction).

    v2 lane polygons are [a x limit, b x limit, b x outer, a x outer]."""
    c = np.asarray(lane.polygon_px, float)
    at_limit, at_outer = (c[0] + c[1]) / 2, (c[2] + c[3]) / 2
    direction = at_limit - at_outer
    return at_limit, direction / np.linalg.norm(direction)


def gap_px(point, limit_segment, direction) -> float:
    """Distance from a point to the limit line along the lane (positive = short of the line)."""
    a, b = (np.asarray(p, float) for p in limit_segment)
    m = np.column_stack([direction, a - b])
    return float(np.linalg.solve(m, a - np.asarray(point, float))[0])


def zone_mask(lane: ImageLane, shape) -> np.ndarray:
    c = np.asarray(lane.polygon_px, float)
    _, d = lane_axis(lane)
    quad = np.array([c[0], c[1], c[1] - d * ZONE_PX, c[0] - d * ZONE_PX])
    mask = np.zeros(shape, np.uint8)
    cv2.fillPoly(mask, [np.round(quad).astype(np.int32)], 1)
    return mask.astype(bool)


def auto_front(frame, background, lane: ImageLane) -> float | None:
    """First point upstream of the limit line where the lane's middle third differs from the
    background for 3 px running: a cross-check, not a measurement (it also finds shadows)."""
    centre, d = lane_axis(lane)
    c = np.asarray(lane.polygon_px, float)
    half = np.linalg.norm(c[1] - c[0]) / 6
    n = np.array([-d[1], d[0]])
    diff = np.abs(frame.astype(np.int16) - background.astype(np.int16)).max(axis=2) > DIFF
    run = 0
    for s in np.arange(-5.0, ZONE_PX + 20.0, 1.0):
        pts = [centre - d * s + n * k for k in np.linspace(-half, half, 5)]
        hits = [diff[int(round(p[1])), int(round(p[0]))] for p in pts
                if 0 <= p[0] < diff.shape[1] and 0 <= p[1] < diff.shape[0]]  # fmt: skip
        run = run + 1 if hits and np.mean(hits) >= 0.6 else 0
        if run == 3:
            return float(s - 2)
    return None


def frames_of(video: Path):
    cap = cv2.VideoCapture(str(video))
    index = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield index, frame
            index += 1
    finally:
        cap.release()


def draw_grid(crop: np.ndarray, u0: int, v0: int, u1: int, v1: int) -> None:
    for u in range(u0 - u0 % 5, u1, 5):
        if u >= u0:
            x = (u - u0) * ZOOM
            colour = (0, 140, 255) if u % 10 == 0 else (90, 90, 90)
            cv2.line(crop, (x, 0), (x, crop.shape[0]), colour, 1)
            if u % 10 == 0:
                cv2.putText(crop, str(u), (x + 1, 10), cv2.FONT_HERSHEY_SIMPLEX, 0.3,
                            (0, 255, 255), 1)  # fmt: skip
    for v in range(v0 - v0 % 5, v1, 5):
        if v >= v0:
            y = (v - v0) * ZOOM
            colour = (0, 140, 255) if v % 10 == 0 else (90, 90, 90)
            cv2.line(crop, (0, y), (crop.shape[1], y), colour, 1)
            if v % 10 == 0:
                cv2.putText(crop, str(v), (1, y - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.3,
                            (0, 255, 255), 1)  # fmt: skip


def select(args) -> int:
    if OUT.exists():
        print(f"refusing to overwrite {OUT}", file=sys.stderr)
        return 2
    for path, expected in ((VIDEO, VIDEO_SHA256), (BACKGROUND, BACKGROUND_SHA256)):
        if sha256(path) != expected:
            print(f"{path} does not match its recorded SHA-256", file=sys.stderr)
            return 2
    geometry = ImageGeometry.model_validate_json(GEOMETRY.read_text("utf-8"))
    lanes = {lane.lane_id: lane for lane in geometry.lanes}
    background = cv2.imread(str(BACKGROUND))
    shape = background.shape[:2]
    masks = {lid: zone_mask(lanes[lid], shape) for ids in THROUGH.values() for lid in ids}
    bg = background.astype(np.int16)
    scores: dict[str, list[tuple[int, int]]] = {"N": [], "S": []}
    pending: dict[int, np.ndarray] = {}
    for index, frame in frames_of(VIDEO):
        if index % STEP_FRAMES == 0:
            pending[index] = frame
        if index - 10 in pending:  # compare with the frame 1 s later
            earlier = pending.pop(index - 10)
            occ = np.abs(earlier.astype(np.int16) - bg).max(axis=2) > DIFF
            moved = np.abs(earlier.astype(np.int16) - frame.astype(np.int16)).max(axis=2) > 25
            for arm, ids in THROUGH.items():
                stopped = sum(occ[masks[i]].mean() > OCCUPIED and moved[masks[i]].mean() < STILL
                              for i in ids)  # fmt: skip
                scores[arm].append((index - 10, int(stopped)))
    chosen: dict[str, list[int]] = {}
    for arm, rows in scores.items():
        picks: list[int] = []
        for index, score in sorted(rows, key=lambda r: (-r[1], r[0])):
            if score < 2 or len(picks) == FRAMES_PER_ARM:
                break
            if all(abs(index - p) / FPS >= MIN_SEPARATION_S for p in picks):
                picks.append(index)
        chosen[arm] = sorted(picks)
    OUT.mkdir(parents=True)
    wanted = {i: arm for arm, picks in chosen.items() for i in picks}
    auto: dict[str, dict] = {}
    for index, frame in frames_of(VIDEO):
        if index not in wanted:
            continue
        arm = wanted[index]
        u0, v0, u1, v1 = CROP[arm]
        crop = cv2.resize(frame[v0:v1, u0:u1], None, fx=ZOOM, fy=ZOOM,
                          interpolation=cv2.INTER_CUBIC)  # fmt: skip
        draw_grid(crop, u0, v0, u1, v1)
        cv2.imwrite(str(OUT / f"crop_{arm}_{index:05d}.png"), crop)
        auto[str(index)] = {lid: auto_front(frame, background, lanes[lid])
                            for lid in lanes if lid.startswith(f"{arm}_")}  # fmt: skip
    record = {
        "video_sha256": VIDEO_SHA256, "step_frames": STEP_FRAMES, "zone_px": ZONE_PX,
        "frames": chosen, "time_s": {a: [round(i / FPS, 1) for i in p] for a, p in chosen.items()},
        "auto_front_px_upstream_of_limit": auto,
        "candidate_frames_scanned": {a: len(r) for a, r in scores.items()},
        "candidates_with_two_or_more_stopped": {a: sum(s >= 2 for _, s in r)
                                                for a, r in scores.items()},
    }  # fmt: skip
    (OUT / "frames.json").write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    print(
        json.dumps(
            {k: record[k] for k in ("frames", "time_s", "candidates_with_two_or_more_stopped")},
            indent=2,
        )
    )
    return 0


def registration_px_per_m(h_i2g: np.ndarray, point, direction) -> float:
    def ground(p):
        q = h_i2g @ np.array([p[0], p[1], 1.0])
        return q[:2] / q[2]

    p = np.asarray(point, float)
    return 10.0 / float(np.linalg.norm(ground(p + 5 * direction) - ground(p - 5 * direction)))


def is_still(frame, later, lane: ImageLane, front) -> bool:
    """The patch just behind a clicked front (8 px long, half a lane wide) barely changes in 1 s,
    so the vehicle is stopped; decided from the video, not by the reader."""
    _, d = lane_axis(lane)
    c = np.asarray(lane.polygon_px, float)
    half = np.linalg.norm(c[1] - c[0]) / 4
    n = np.array([-d[1], d[0]])
    f = np.asarray(front, float)
    quad = np.array([f + n * half, f - n * half, f - n * half - d * 8, f + n * half - d * 8])
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.fillPoly(mask, [np.round(quad).astype(np.int32)], 1)
    changed = np.abs(frame.astype(np.int16) - later.astype(np.int16)).max(axis=2) > 25
    return bool(changed[mask.astype(bool)].mean() < STILL_PATCH)


def summarise(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = np.asarray(values, float)
    return {"n": len(v), "median": float(np.median(v)), "min": float(v.min()),
            "max": float(v.max())}  # fmt: skip


def report(args) -> int:
    target = OUT / "stop_gap.json"
    if target.exists():
        print(f"refusing to overwrite {target}", file=sys.stderr)
        return 2
    clicks = Clicks.model_validate_json(CLICKS.read_text("utf-8"))
    geometry = ImageGeometry.model_validate_json(GEOMETRY.read_text("utf-8"))
    lanes = {lane.lane_id: lane for lane in geometry.lanes}
    limits = {line.arm: line.segment_px for line in geometry.lines if line.role == "limit_line"}
    analysis = json.loads(ANALYSIS.read_text("utf-8"))
    h_i2g = np.array(analysis["registration"]["image_to_ground"])
    ngsim = {
        "N": analysis["limit_lines_vs_queue_heads"]["north"],
        "S": analysis["limit_lines_vs_queue_heads"]["south"],
    }
    auto = json.loads((OUT / "frames.json").read_text("utf-8"))["auto_front_px_upstream_of_limit"]
    if sha256(VIDEO) != VIDEO_SHA256 or clicks.source_video_sha256 != VIDEO_SHA256:
        print("the video or the clicks do not match the recorded video SHA-256", file=sys.stderr)
        return 2
    needed = {f for fc in clicks.frames for f in (fc.frame, fc.frame + 10)}
    decoded = {i: frame for i, frame in frames_of(VIDEO) if i in needed}
    rows = []
    for fc in clicks.frames:
        for lid, click in fc.lanes.items():
            _, d = lane_axis(lanes[lid])
            length = None
            if click.rear_px is not None:
                length = float((np.asarray(click.front_px) - np.asarray(click.rear_px)) @ d)
            rows.append({
                "frame": fc.frame, "arm": fc.arm, "lane": lid,
                "gap_px": gap_px(click.front_px, limits[fc.arm], d), "length_px": length,
                "stopped": is_still(decoded[fc.frame], decoded[fc.frame + 10], lanes[lid],
                                    click.front_px),
                "registration_px_per_m": registration_px_per_m(h_i2g, click.front_px, d),
                "auto_gap_px": auto.get(str(fc.frame), {}).get(lid), "note": click.note,
            })  # fmt: skip
    result: dict = {"clicks": str(CLICKS.relative_to(ROOT)), "reader": clicks.reader,
                    "ngsim_length_m": NGSIM_LENGTH_M, "rows": rows, "by_arm": {}}  # fmt: skip
    for arm in ("N", "S"):
        lengths = [r["length_px"] for r in rows if r["arm"] == arm and r["length_px"]]
        car_scale = float(np.median(lengths)) / NGSIM_LENGTH_M if lengths else math.nan
        arm_rows = [r for r in rows if r["arm"] == arm and r["stopped"]]  # gaps: stopped only
        per_lane = {}
        for lid in sorted({r["lane"] for r in arm_rows}):
            lr = [r for r in arm_rows if r["lane"] == lid]
            ngsim_id = lanes[lid].reference_lane.rsplit(" ", 1)[1]
            per_lane[lid] = {
                "gap_px": summarise([r["gap_px"] for r in lr]),
                "gap_m_car_scale": summarise([r["gap_px"] / car_scale for r in lr]),
                "gap_m_registration": summarise(
                    [r["gap_px"] / r["registration_px_per_m"] for r in lr]),
                "auto_gap_px": summarise([r["auto_gap_px"] for r in lr
                                          if r["auto_gap_px"] is not None]),
                "ngsim_gap_median_m": ngsim[arm]["gap_limit_line_to_head_median_m"].get(ngsim_id),
                "ngsim_gap_q90_m": ngsim[arm]["gap_limit_line_to_head_q90_m"].get(ngsim_id),
            }  # fmt: skip
        result["by_arm"][arm] = {
            "clicked": sum(r["arm"] == arm for r in rows),
            "stopped": len(arm_rows),
            "car_length_px": summarise(lengths),
            "px_per_m_from_car_lengths": car_scale,
            "px_per_m_registration_at_fronts": summarise(
                [r["registration_px_per_m"] for r in arm_rows]),
            "gap_px_all": summarise([r["gap_px"] for r in arm_rows]),
            "gap_m_car_scale_all": summarise([r["gap_px"] / car_scale for r in arm_rows]),
            "gap_m_registration_all": summarise(
                [r["gap_px"] / r["registration_px_per_m"] for r in arm_rows]),
            "lanes": per_lane,
        }  # fmt: skip
    target.write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    for arm, s in result["by_arm"].items():
        print(arm, f"clicked {s['clicked']}, stopped {s['stopped']};", "px/m from car lengths",
              round(s["px_per_m_from_car_lengths"], 2),
              "| registration", s["px_per_m_registration_at_fronts"])  # fmt: skip
        print("   all lanes: gap px", s["gap_px_all"], "| m (car scale)", s["gap_m_car_scale_all"])
        for lid, lane in s["lanes"].items():
            print(
                f"   {lid:8s} px {lane['gap_px']} | m(car) {lane['gap_m_car_scale']} | "
                f"NGSIM median {lane['ngsim_gap_median_m']} | auto px {lane['auto_gap_px']}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("select").set_defaults(run=select)
    sub.add_parser("report").set_defaults(run=report)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
