"""Exploratory diagnosis of camera gate v2's lateral-error failure (P1-1). Read-only: no model
call, and no change to a gate, a threshold, a record or the world model.

    uv run --frozen python scripts/diagnose_camera_gate.py \\
        --sam-run experiments/runs/ngsim-cam2-sam-20261030T220322Z \\
        --gate-record experiments/records/ngsim-cam2-world-model-20261030T220419Z.json
    uv run --frozen python scripts/diagnose_camera_gate.py \\
        --gate data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate.json \\
        --gate-record experiments/records/ngsim-cam2-world-model-20261030T213226Z.json

Output: experiments/diagnostics/camera-gate-<v1|v2>-<stamp>/ (a new folder; never overwritten).

It rebuilds the gate's comparison with the gate's own code (cairoflow_perception.camera_world and
scripts/build_camera_world_model.py) and stops unless the inputs hash as the record says and the
comparison reproduces the recorded metrics exactly. Then it splits every matched point's error
by where the NGSIM vehicle was (approach lane, exit lane, junction box between the stop lines,
elsewhere), by movement, heading, speed, image position and track; resolves each error along and
across the vehicle's own heading and along the image's vertical; and repeats the comparison with
the reference shifted in time. All of it is exploratory, on the data the gate already judged: it
explains that result and replaces none of it (gate v2: D_lateral_p90_m 2.206, no-go).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from pydantic import TypeAdapter

from cairoflow_contracts.evidence import IntersectionGeometry, LanePolygon, Observation, SourceClip
from cairoflow_perception.calibration import CalibrationError, fit_homography
from cairoflow_perception.camera_world import (
    MIN_MATCH_SHARE,
    MIN_MATCHED_POINTS,
    Placement,
    _axis_is_y,
    _pairs,
    compare,
    lane_hits,
    place_track,
    reference_at,
)
from cairoflow_perception.ngsim import (
    DIRECTIONS,
    MOVEMENTS,
    LocalFrame,
    kinematics,
    load_trajectories,
)
from cairoflow_perception.ngsim_junction import JunctionFrame
from cairoflow_perception.tracking import TrackingResult

REPO = Path(__file__).resolve().parents[1]
GATE_V2 = REPO / "data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate_v2.json"
LIMIT_M = 1.5  # gate v2's D threshold, shown for reference; nothing here applies it
SHIFTS_S = tuple(round(-1.0 + 0.1 * i, 1) for i in range(21))
# Chart colours (BGR): dataviz reference slots 1 and 2 (#2a78d6, #eb6834), text and surface.
BLUE, ORANGE = (214, 120, 42), (52, 104, 235)
INK, INK2, SURFACE, RULE = (11, 11, 11), (78, 81, 82), (251, 252, 252), (205, 205, 202)


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def stop_box(geometry: IntersectionGeometry) -> dict[str, float]:
    """Each arm's stop line as one coordinate: y for N and S, x for E and W."""
    box = {}
    for gate in geometry.gates:
        if gate.role == "stop_line":
            (ax, ay), (bx, by) = gate.segment_m
            box[gate.arm] = (ay + by) / 2 if gate.arm in ("N", "S") else (ax + bx) / 2
    return box


def region_of(p: tuple[float, float], lanes: list[LanePolygon], box: dict[str, float]) -> str:
    """approach / departure (inside exactly one approach or exit lane), junction_interior (in no
    lane, inside the box the four stop lines bound) or unknown (anything else)."""
    hits = lane_hits(lanes, p)
    if len(hits) == 1:
        return "approach" if hits[0].direction == "approach" else "departure"
    if not hits and box["W"] < p[0] < box["E"] and box["S"] < p[1] < box["N"]:
        return "junction_interior"
    return "unknown"


def heading_components(dx: float, dy: float, heading_deg: float) -> tuple[float, float]:
    """An error vector along and across a heading (bearing from +y, clockwise): along > 0 puts
    the camera point ahead of the reference, across > 0 to its right."""
    h = math.radians(heading_deg)
    return dx * math.sin(h) + dy * math.cos(h), dx * math.cos(h) - dy * math.sin(h)


def fold_deg(angle: float) -> float:
    """The angle between two undirected lines, 0-90 degrees."""
    return abs((angle + 90) % 180 - 90)


def summary(rows: list[dict], key: str = "lateral_m") -> dict:
    values = sorted(abs(r[key]) for r in rows)
    if not values:
        return {"points": 0}
    both = [r for r in rows if r["camera_lane"] and r["reference_lane"]]
    return {
        "points": len(values), "tracks": len({r["track"] for r in rows}),
        "median_m": round(statistics.median(values), 3),
        "p75_m": round(float(np.percentile(values, 75)), 3),
        "p90_m": round(float(np.percentile(values, 90)), 3), "max_m": round(values[-1], 3),
        "share_over_limit": round(sum(v > LIMIT_M for v in values) / len(values), 3),
        "lane_points": len(both),
        "lane_agreement": round(sum(r["camera_lane"] == r["reference_lane"] for r in both)
                                / len(both), 3) if both else None,
    }  # fmt: skip


def grouped(rows: list[dict], key, order: tuple[str, ...] = ()) -> dict:
    found: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        found[str(key(r))].append(r)
    names = [n for n in order if n in found] + sorted(n for n in found if n not in order)
    return {name: summary(found[name]) for name in names}


def strip_plot(groups: list[tuple[str, list[float]]], path: Path, title: str, note: str) -> None:
    """One row of dots per group (|error| in m), its median and p90 ticks, and the gate limit."""
    left, right, top, row_h = 330, 40, 96, 30
    width, height = 1200, top + row_h * len(groups) + 70
    xmax = max(4.0, math.ceil(max(max(v) for _, v in groups if v)))
    image = np.full((height, width, 3), SURFACE, np.uint8)

    def x_of(value: float) -> int:
        return int(left + min(value, xmax) / xmax * (width - left - right))

    text = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(image, title, (16, 30), text, 0.62, INK, 1, cv2.LINE_AA)
    cv2.putText(image, note, (16, 54), text, 0.45, INK2, 1, cv2.LINE_AA)
    bottom = top + row_h * len(groups)
    for tick in range(int(xmax) + 1):
        cv2.line(image, (x_of(tick), top - 8), (x_of(tick), bottom), RULE, 1)
        cv2.putText(image, f"{tick} m", (x_of(tick) - 10, bottom + 20), text, 0.42, INK2, 1,
                    cv2.LINE_AA)  # fmt: skip
    for y in range(top - 8, bottom, 8):
        cv2.line(image, (x_of(LIMIT_M), y), (x_of(LIMIT_M), y + 4), INK2, 1)
    cv2.putText(image, "gate limit 1.5 m", (x_of(LIMIT_M) + 4, top - 12), text, 0.4, INK2, 1,
                cv2.LINE_AA)  # fmt: skip
    rng = np.random.default_rng(0)  # jitter only
    for i, (label, values) in enumerate(groups):
        cy = top + row_h * i + row_h // 2
        cv2.putText(image, label, (16, cy + 5), text, 0.45, INK, 1, cv2.LINE_AA)
        for value in values:
            jitter = int(rng.uniform(-8, 8))
            colour = ORANGE if value > LIMIT_M else BLUE
            cv2.circle(image, (x_of(value), cy + jitter), 3, colour, -1, cv2.LINE_AA)
        if values:
            p90, med = float(np.percentile(values, 90)), statistics.median(values)
            cv2.line(image, (x_of(med), cy - 12), (x_of(med), cy + 12), INK2, 2)
            cv2.line(image, (x_of(p90), cy - 13), (x_of(p90), cy + 13), INK, 3)
            cv2.putText(image, f"p90 {p90:.2f}", (x_of(p90) + 5, cy - 4), text, 0.4, INK, 1,
                        cv2.LINE_AA)  # fmt: skip
    legend = [(BLUE, "|error| <= 1.5 m"), (ORANGE, "|error| > 1.5 m")]
    for j, (colour, label) in enumerate(legend):
        cv2.circle(image, (16 + 190 * j + 6, bottom + 46), 4, colour, -1, cv2.LINE_AA)
        cv2.putText(image, label, (16 + 190 * j + 16, bottom + 51), text, 0.42, INK, 1,
                    cv2.LINE_AA)  # fmt: skip
    cv2.putText(image, "thick tick: p90; thin tick: median", (400, bottom + 51), text, 0.42, INK,
                1, cv2.LINE_AA)  # fmt: skip
    cv2.imwrite(str(path), image)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--gate", type=Path, default=GATE_V2, help="the gate whose result to explain"
    )
    parser.add_argument("--sam-run", type=Path, help="the SAM run a gate's <SAM_RUN> names")
    parser.add_argument("--gate-record", type=Path, required=True, help="that gate's record")
    parser.add_argument("--out-root", type=Path, default=REPO / "experiments/diagnostics")
    args = parser.parse_args(argv)
    builder = _module("build_camera_world_model")
    gate_path = args.gate.resolve()
    gate = json.loads(gate_path.read_text("utf-8"))
    tag = gate["gate_id"].rsplit("-", 1)[-1]
    out = args.out_root / f"camera-gate-{tag}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    paths = builder.input_paths(gate, Path(os.path.abspath(args.sam_run)) if args.sam_run else None)
    recorded = json.loads(args.gate_record.read_text("utf-8"))
    hashes = {name: builder.sha256_file(path) for name, path in paths.items() if path.is_file()}
    if (builder.check_inputs(gate, paths) or hashes != recorded["inputs"]
            or recorded["gate_file"]["sha256"] != builder.sha256_file(gate_path)):  # fmt: skip
        print("the inputs or the gate file differ from the gate's record", file=sys.stderr)
        return 2
    verdict = (f"{gate['gate_id']} result unchanged: D p90 {recorded['metrics']['D_lateral_p90_m']}"
               f" m, {recorded['gate']['outcome'].replace('_', '-')}")  # fmt: skip

    # the gate's comparison, built exactly as build_camera_world_model.py builds it
    scenario = json.loads(builder.SCENARIO.read_text("utf-8"))
    frame = JunctionFrame(*scenario["frame"]["origin_state_plane_ft"],
                          scenario["frame"]["bearing_deg"])  # fmt: skip
    geometry = IntersectionGeometry.model_validate_json(
        paths["world_model_geometry"].read_text("utf-8"))  # fmt: skip
    lanes = list(geometry.lanes)
    checkpoints = builder._checkpoints_module()
    sync = json.loads(paths["calibration_pairs"].read_text("utf-8"))
    spec = checkpoints.spec_for(sync["pairs"], checkpoints.CLIP_IDS["d300"])
    homography, calibration = fit_homography(spec, target_max_check_error_m=1.25)
    placement = Placement(homography, frame,
                          statistics.median(calibration.check_errors_m.values()))  # fmt: skip
    clip = SourceClip.model_validate_json(paths["camera_clip_manifest"].read_text("utf-8"))
    offset = round(clip.trim.start_s - builder.SOURCE_AT_0830_S, 6)
    tracking = TrackingResult.model_validate_json(paths["sam_tracks"].read_text("utf-8"))
    observations = TypeAdapter(tuple[Observation, ...]).validate_json(
        paths["sam_observations"].read_text("utf-8"))  # fmt: skip
    confidence = {o.observation_id: o.confidence for o in observations}
    boxes = {o.observation_id: o.bbox_xyxy_px for o in observations}
    tracks = []
    for track in sorted(tracking.tracks, key=lambda t: t.track_id):
        placed, _ = place_track(track, placement, lanes, camera=builder.CAMERA,
                                window_offset_s=offset, confidence=confidence)  # fmt: skip
        if placed is not None:
            tracks.append(placed)
    traj = load_trajectories(paths["reference_trajectories"])
    speed, heading = kinematics(traj, LocalFrame(frame.origin_x_ft, frame.origin_y_ft))

    def references(shift_s: float) -> tuple[dict, dict]:
        refs, rows = {}, {}
        for f in clip.frames:
            tick = builder.T0_MS + round((offset + f.timestamp_s + shift_s) * 10) * 100
            refs[f.frame_index], _ = reference_at(traj, heading, frame, lanes, tick)
            for row in np.flatnonzero(traj.global_time_ms == tick):
                rows[(f.frame_index, int(traj.vehicle_id[row]))] = int(row)
        return refs, rows

    refs, rows_at = references(0.0)
    metrics = compare(tracks, refs, lanes)
    if json.loads(json.dumps(metrics)) != recorded["metrics"]:
        print("the rebuilt comparison does not reproduce the gate's recorded metrics",
              file=sys.stderr)  # fmt: skip
        return 1

    # the points compare() measures, selected as it selects them
    def gate_points(references_by_frame: dict) -> list[tuple]:
        paired = _pairs(tracks, references_by_frame)
        found = []
        for track in tracks:
            pairs = [(p, paired[(track.track_id, p.frame.frame_index)]) for p in track.points
                     if (track.track_id, p.frame.frame_index) in paired]  # fmt: skip
            votes = Counter(r.vehicle_id for _, r in pairs)
            vehicle, count = votes.most_common(1)[0] if votes else (None, 0)
            if len(pairs) < MIN_MATCHED_POINTS or count / len(pairs) < MIN_MATCH_SHARE:
                continue
            found += [(track, p, r) for p, r in pairs if r.vehicle_id == vehicle]
        return found

    b = math.radians(frame.bearing_deg)

    def to_junction_rot(x: float, y: float) -> tuple[float, float]:  # grid metres to junction
        return x * math.cos(b) - y * math.sin(b), x * math.sin(b) + y * math.cos(b)

    def junction_to_image(p) -> tuple[float, float]:
        x, y = p
        return homography.to_image((x * math.cos(b) + y * math.sin(b),
                                    -x * math.sin(b) + y * math.cos(b)))  # fmt: skip

    probe = placement.to_junction((320.0, 240.0))
    assert math.dist(placement.to_junction(junction_to_image(probe)), probe) < 1e-6
    box = stop_box(geometry)
    by_lane = {lane.lane_id: lane for lane in lanes}
    width, height = clip.output_stream.width_px, clip.output_stream.height_px
    controls = [(p, to_junction_rot(*p.ground_m)) for p in spec.points]
    control_residual = [math.dist(placement.to_junction(p.image_px), g) for p, g in controls]
    frames_of = {t.track_id: sorted(p.frame.frame_index for p in t.points) for t in tracks}
    area_of = {t.track_id: statistics.median(
        (boxes[p.observation_id][2] - boxes[p.observation_id][0])
        * (boxes[p.observation_id][3] - boxes[p.observation_id][1]) for p in t.points)
        for t in tracks}  # fmt: skip

    def describe(track, point, ref, row: int) -> dict:
        camera_lane = by_lane.get(point.lane_id) if point.lane_id else None
        dx = point.position_m[0] - ref.centre[0]
        dy = point.position_m[1] - ref.centre[1]
        axis_y = _axis_is_y(ref, camera_lane)
        lateral, along = (dx, dy) if axis_y else (dy, dx)
        hj = float(heading[row]) - frame.bearing_deg  # junction-frame bearing
        along_h, across_h = heading_components(dx, dy, hj)
        u, v = point.image_point_px
        g0, g1 = placement.to_junction((u, v)), placement.to_junction((u, v - 1.0))
        n = math.hypot(g1[0] - g0[0], g1[1] - g0[1])
        up = ((g1[0] - g0[0]) / n, (g1[1] - g0[1]) / n)  # ground direction of image-up
        x1, y1, x2, y2 = boxes[point.observation_id]
        first, last = frames_of[track.track_id][0], frames_of[track.track_id][-1]
        s = float(speed[row])
        return {
            "track": track.track_id, "vehicle": ref.vehicle_id, "frame": point.frame.frame_index,
            "image_px": [u, v], "bbox_px": [x1, y1, x2, y2],
            "camera_m": list(point.position_m), "reference_m": [round(c, 4) for c in ref.centre],
            "camera_lane": point.lane_id,
            "reference_lane": ref.lane.lane_id if ref.lane else None,
            "region": region_of(ref.centre, lanes, box),
            "camera_region": region_of(tuple(point.position_m), lanes, box),
            "movement": MOVEMENTS.get(int(traj.movement[row]), str(traj.movement[row])),
            "direction": DIRECTIONS.get(int(traj.direction[row]), str(traj.direction[row])),
            "ngsim_int_section": f"{int(traj.int_id[row])}/{int(traj.section_id[row])}",
            "gate_axis": "y" if axis_y else "x",
            "heading_junction_deg": round(hj % 360, 1),
            "axis_misalignment_deg": round(fold_deg(hj - (0.0 if axis_y else 90.0)), 1),
            "speed_m_s": None if math.isnan(s) else round(s, 2),
            "lateral_m": round(lateral, 4), "along_m": round(along, 4),
            "along_heading_m": round(along_h, 4), "across_heading_m": round(across_h, 4),
            "image_up_m": round(dx * up[0] + dy * up[1], 4),
            "image_side_m": round(dx * up[1] - dy * up[0], 4),
            "image_radius_px": round(math.hypot(u - width / 2, v - height / 2), 1),
            "nearest_control_m": round(min(math.dist(ref.centre, g) for _, g in controls), 2),
            "box_truncated": x1 <= 1 or y1 <= 1 or x2 >= width - 1 or y2 >= height - 1,
            "box_area_ratio": round((x2 - x1) * (y2 - y1) / area_of[track.track_id], 3),
            "track_edge": point.frame.frame_index - first < 5 or last - point.frame.frame_index < 5,
            "confidence": point.confidence, "near_boundary": point.near_lane_boundary,
        }  # fmt: skip

    found = gate_points(refs)
    rows = [describe(t, p, r, rows_at[(p.frame.frame_index, r.vehicle_id)]) for t, p, r in found]
    lateral_p90 = round(float(np.percentile([abs(r["lateral_m"]) for r in rows], 90)), 4)
    assert len(rows) == metrics["matched_points"] and lateral_p90 == metrics["D_lateral_p90_m"]

    # time shifts: the gate's own pairs with the reference read earlier or later, and the whole
    # gate procedure re-run on shifted references
    def is_moving(r: dict) -> bool:
        return r["speed_m_s"] is not None and r["speed_m_s"] >= 2.0

    def is_stopped(r: dict) -> bool:
        return r["speed_m_s"] is not None and r["speed_m_s"] < 0.5

    def by_direction(group: list[dict], key: str) -> dict:
        return {d: round(statistics.median(r[key] for r in group if r["direction"] == d), 3)
                for d in sorted({r["direction"] for r in group})}  # fmt: skip

    shifts, moving_median = {}, {}
    for shift in SHIFTS_S:
        refs_s, rows_s = references(shift) if shift else (refs, rows_at)
        lookup = {(i, r.vehicle_id): r for i, found_s in refs_s.items() for r in found_s}
        moved = [describe(t, p, lookup[(p.frame.frame_index, r.vehicle_id)],
                          rows_s[(p.frame.frame_index, r.vehicle_id)])
                 for t, p, r in found if (p.frame.frame_index, r.vehicle_id) in lookup]  # fmt: skip
        moving_s = [r for r in moved if is_moving(r)]
        moving_median[shift] = (statistics.median(r["along_heading_m"] for r in moving_s)
                                if moving_s else math.inf)  # fmt: skip
        rerun = compare(tracks, refs_s, lanes)
        shifts[f"{shift:+.1f}"] = {
            "fixed_pairs_points": len(moved),
            "fixed_pairs_lateral_p90_m": summary(moved)["p90_m"],
            "fixed_pairs_along_heading_median_signed_m": round(
                statistics.median(r["along_heading_m"] for r in moved), 3),
            "fixed_pairs_moving_along_heading_median_signed_m": round(moving_median[shift], 3),
            "fixed_pairs_moving_along_heading_median_by_direction_m": by_direction(
                moving_s, "along_heading_m"),
            "fixed_pairs_across_heading_p90_m": summary(moved, "across_heading_m")["p90_m"],
            "gate_rerun_matched_points": rerun["matched_points"],
            "gate_rerun_matched_tracks": rerun["matched_tracks"],
            "gate_rerun_D_lateral_p90_m": rerun["D_lateral_p90_m"],
            "gate_rerun_along_median_m": rerun["along_median_m"],
        }  # fmt: skip
    moving = [r for r in rows if is_moving(r)]
    stopped = [r for r in rows if is_stopped(r)]

    def speed_fit(group: list[dict]) -> dict | None:
        if len(group) < 10:
            return None
        slope, intercept = np.polyfit([r["speed_m_s"] for r in group],
                                      [r["along_heading_m"] for r in group], 1)  # fmt: skip
        return {"points": len(group), "slope_s": round(float(slope), 3),
                "intercept_m": round(float(intercept), 3)}  # fmt: skip

    # the shift, of those tried, at which moving vehicles' median along-heading error is nearest
    # zero; the gate procedure re-run there (exploration on the same data, not a new sync)
    best = min(SHIFTS_S, key=lambda s: abs(moving_median[s]))
    refs_b, rows_b = references(best)
    found_b = gate_points(refs_b)
    at_best = [describe(t, p, r, rows_b[(p.frame.frame_index, r.vehicle_id)])
               for t, p, r in found_b]  # fmt: skip

    # for over-limit points measured on the right axis: was a reference vehicle in the camera
    # point's own lane nearby, which the greedy one-to-one pairing passed over?
    alternatives: dict[str, list] = defaultdict(list)
    for (track, point, ref), row in zip(found, rows, strict=True):
        if abs(row["lateral_m"]) <= LIMIT_M or row["axis_misalignment_deg"] > 30:
            continue
        same = [o for o in refs[point.frame.frame_index]
                if point.lane_id and o.lane and o.lane.lane_id == point.lane_id]  # fmt: skip
        if not same:
            alternatives[track.track_id].append(None)
            continue
        o = min(same, key=lambda o: math.dist(o.centre, point.position_m))
        hj = float(heading[rows_at[(point.frame.frame_index, o.vehicle_id)]]) - frame.bearing_deg
        along_h, across_h = heading_components(point.position_m[0] - o.centre[0],
                                               point.position_m[1] - o.centre[1], hj)  # fmt: skip
        alternatives[track.track_id].append({
            "vehicle": o.vehicle_id, "distance_m": math.dist(o.centre, point.position_m),
            "paired_distance_m": math.dist(ref.centre, point.position_m),
            "along_heading_m": along_h, "across_heading_m": across_h})  # fmt: skip

    def alternative_summary(found_alt: list) -> dict:
        present = [a for a in found_alt if a]
        out = {"points": len(found_alt), "with_same_lane_vehicle": len(present)}
        if present:
            out |= {
                "vehicles": dict(Counter(a["vehicle"] for a in present)),
                **{f"median_{k}": round(statistics.median(a[k] for a in present), 3)
                   for k in ("distance_m", "paired_distance_m", "along_heading_m",
                             "across_heading_m")}}  # fmt: skip
        return out

    # coverage: matched points per reference vehicle-frame whose centre falls inside the image
    in_view: Counter = Counter()
    for found_f in refs.values():
        for r in found_f:
            try:
                u, v = junction_to_image(r.centre)
            except CalibrationError:
                continue
            if 0 <= u < width and 0 <= v < height:
                in_view[region_of(r.centre, lanes, box)] += 1
    regions = ("approach", "departure", "junction_interior", "unknown")
    by_region = grouped(rows, lambda r: r["region"], regions)
    for name, stats in by_region.items():
        stats["reference_vehicle_frames_in_view"] = in_view[name]
        stats["coverage"] = round(stats["points"] / in_view[name], 3) if in_view[name] else None
    over = [r for r in rows if abs(r["lateral_m"]) > LIMIT_M]
    signed = defaultdict(list)
    for r in rows:
        signed[f"{r['region']} / {r['direction']} / {r['movement']}"].append(r)

    def signed_medians(group: list[dict]) -> dict:
        keys = ("lateral_m", "along_heading_m", "across_heading_m", "image_up_m", "image_side_m")
        return {k: round(statistics.median(r[k] for r in group), 3) for k in keys} | {
            "points": len(group)}  # fmt: skip

    control_regions = Counter(region_of(g, lanes, box) for _, g in controls)
    diagnosis = {
        "label": f"exploratory diagnosis of {gate['gate_id']} on the data it judged; {verdict}. "
        "Not confirmation of any explanation: a future gate must test one on new data.",
        "inputs": recorded["inputs"], "gate_file": recorded["gate_file"],
        "reproduced_gate_metrics": {k: v for k, v in metrics.items()
                                    if k != "matched_vehicle_of_track"},
        "lateral_definition": "camera box centre minus NGSIM centre (front centre moved back "
        "half the vehicle length along its heading), in the junction frame; lateral is dx when "
        "the reference's lane arm (else the camera lane's, else the nearer axis) is N or S, dy "
        "otherwise; D is numpy's linear-interpolation 90th percentile of |lateral| over all "
        "matched points, in or out of lanes",
        "all_points": summary(rows),
        "by_reference_region": by_region,
        "by_movement": grouped(rows, lambda r: r["movement"], ("through", "left", "right")),
        "by_region_and_movement": grouped(rows, lambda r: f"{r['region']} / {r['movement']}"),
        "by_gate_axis_misalignment": grouped(
            rows, lambda r: "<= 10 deg" if r["axis_misalignment_deg"] <= 10 else (
                "10-30 deg" if r["axis_misalignment_deg"] <= 30 else "> 30 deg"),
            ("<= 10 deg", "10-30 deg", "> 30 deg")),
        "by_speed": grouped(rows, lambda r: "unknown" if r["speed_m_s"] is None else (
            "< 0.5 m/s" if r["speed_m_s"] < 0.5 else (
                "0.5-5 m/s" if r["speed_m_s"] < 5 else ">= 5 m/s")),
            ("< 0.5 m/s", "0.5-5 m/s", ">= 5 m/s")),
        "by_image_radius": grouped(rows, lambda r: f"{int(r['image_radius_px'] // 100) * 100}-"
                                   f"{int(r['image_radius_px'] // 100) * 100 + 99} px"),
        "by_nearest_control_point": grouped(
            rows, lambda r: "< 10 m" if r["nearest_control_m"] < 10 else (
                "10-20 m" if r["nearest_control_m"] < 20 else ">= 20 m"),
            ("< 10 m", "10-20 m", ">= 20 m")),
        "by_camera_region": grouped(rows, lambda r: r["camera_region"], regions),
        "by_ngsim_int_section": grouped(rows, lambda r: r["ngsim_int_section"]),
        "by_box_truncated": grouped(rows, lambda r: r["box_truncated"]),
        "by_track_edge": grouped(rows, lambda r: r["track_edge"]),
        "by_box_area_ratio": grouped(rows, lambda r: "< 0.7" if r["box_area_ratio"] < 0.7 else (
            "> 1.3" if r["box_area_ratio"] > 1.3 else "0.7-1.3"), ("< 0.7", "0.7-1.3", "> 1.3")),
        "by_reference_lane": grouped(rows, lambda r: r["reference_lane"] or "none"),
        "error_components_all_points": {
            key: summary(rows, key) for key in ("lateral_m", "along_m", "across_heading_m",
                                                "along_heading_m", "image_up_m", "image_side_m")},
        "signed_medians_by_region_direction_movement": {
            k: signed_medians(v) for k, v in sorted(signed.items())},
        "signed_medians_stopped_vehicles": signed_medians(stopped) if stopped else None,
        "stopped_along_heading_median_by_direction_m": by_direction(stopped, "along_heading_m"),
        "moving_along_heading_median_by_direction_m": by_direction(moving, "along_heading_m"),
        "along_heading_vs_speed_moving": {
            "all": speed_fit(moving),
            **{d: speed_fit([r for r in moving if r["direction"] == d])
               for d in sorted({r["direction"] for r in moving})},
            "reading": "slope is the time offset that would explain the along-heading error; "
            "intercept is a fixed offset along the heading"},
        "same_lane_alternatives_for_over_limit_points_on_the_right_axis": {
            t: alternative_summary(a) for t, a in sorted(alternatives.items())},
        "exploratory_rerun_at_best_shift": {
            "shift_s": best,
            "chosen_by": "of the shifts tried, the one whose moving vehicles' median "
            "along-heading error is nearest zero, on these same data: exploration only, not a "
            "new time sync and not a gate result",
            "all_points": summary(at_best),
            "across_heading": summary(at_best, "across_heading_m"),
            "by_reference_region": grouped(at_best, lambda r: r["region"], regions),
            "by_gate_axis_misalignment": grouped(
                at_best, lambda r: "<= 30 deg" if r["axis_misalignment_deg"] <= 30
                else "> 30 deg", ("<= 30 deg", "> 30 deg")),
            "per_track": {
                t: summary([r for r in at_best if r["track"] == t]) | {
                    "vehicle": Counter(r["vehicle"] for r in at_best
                                       if r["track"] == t).most_common(1)[0][0],
                    "lanes_camera_reference": [list(k) + [n] for k, n in Counter(
                        (r["camera_lane"], r["reference_lane"]) for r in at_best
                        if r["track"] == t).most_common(3)]}
                for t in sorted({r["track"] for r in at_best})}},
        "per_track": {t: summary([r for r in rows if r["track"] == t])
                      | {"regions": dict(Counter(r["region"] for r in rows if r["track"] == t)),
                         "movement": Counter(r["movement"] for r in rows
                                             if r["track"] == t).most_common(1)[0][0]}
                      for t in sorted({r["track"] for r in rows})},
        "points_over_limit": {
            "count": len(over), "tracks": dict(Counter(r["track"] for r in over).most_common()),
            "regions": dict(Counter(r["region"] for r in over).most_common()),
            "movements": dict(Counter(r["movement"] for r in over).most_common())},
        "vehicles_matched_by_several_tracks": {
            str(v): sorted(ts) for v, ts in _group_tracks(metrics).items() if len(ts) > 1},
        "sync_shifts_s": shifts,
        "calibration": {
            "fit_points": len(calibration.fit_point_ids),
            "check_median_m": round(placement.check_median_m, 4),
            "check_max_m": round(max(calibration.check_errors_m.values()), 4),
            "assessment": sync["calibration_assessment"],
            "control_points_by_region": dict(control_regions),
            "control_point_residual_median_m": round(statistics.median(control_residual), 3)},
    }  # fmt: skip
    out.mkdir(parents=True, exist_ok=False)
    (out / "diagnosis.json").write_text(json.dumps(diagnosis, indent=2) + "\n", "utf-8")
    (out / "points.json").write_text(json.dumps(rows, indent=1) + "\n", "utf-8")
    draw(out, rows, lanes, controls, junction_to_image, paths, clip, box, verdict)
    print(f"wrote {out}")
    print(json.dumps({k: diagnosis[k] for k in (
        "stopped_along_heading_median_by_direction_m", "moving_along_heading_median_by_direction_m",
        "along_heading_vs_speed_moving",
        "same_lane_alternatives_for_over_limit_points_on_the_right_axis",
        "exploratory_rerun_at_best_shift")}, indent=1))  # fmt: skip
    return 0


def _group_tracks(metrics: dict) -> dict[int, list[str]]:
    tracks_of: dict[int, list[str]] = defaultdict(list)
    for track, vehicle in metrics["matched_vehicle_of_track"].items():
        tracks_of[vehicle].append(track)
    return tracks_of


def draw(out: Path, rows, lanes, controls, junction_to_image, paths, clip, box, verdict) -> None:
    """The figures: the camera frame and the ground plane with every matched point, and the
    error by region and by track. All from the recorded points; nothing is simulated."""
    text = cv2.FONT_HERSHEY_SIMPLEX
    capture = cv2.VideoCapture(str(paths["camera_clip_manifest"].parent / clip.output_file))
    capture.set(cv2.CAP_PROP_POS_FRAMES, len(clip.frames) // 2)
    ok, image = capture.read()
    capture.release()
    if ok:
        for lane in lanes:
            try:
                poly = np.array([junction_to_image(p) for p in lane.polygon_m], np.int32)
            except CalibrationError:
                continue
            cv2.polylines(image, [poly], True, (235, 235, 235), 1, cv2.LINE_AA)
        for r in sorted(rows, key=lambda r: abs(r["lateral_m"])):
            bad = abs(r["lateral_m"]) > LIMIT_M
            cam = (int(r["image_px"][0]), int(r["image_px"][1]))
            if bad:
                ref = tuple(int(c) for c in junction_to_image(r["reference_m"]))
                cv2.line(image, cam, ref, ORANGE, 1, cv2.LINE_AA)
            cv2.circle(image, cam, 3, (20, 20, 20), -1, cv2.LINE_AA)
            cv2.circle(image, cam, 2, ORANGE if bad else BLUE, -1, cv2.LINE_AA)
        for point, _ in controls:
            u, v = (int(c) for c in point.image_px)
            cv2.drawMarker(image, (u, v), (255, 255, 255), cv2.MARKER_CROSS, 7, 1)
        cv2.rectangle(image, (0, 0), (640, 22), (20, 20, 20), -1)
        cv2.putText(image, "matched points: blue |lateral| <= 1.5 m, orange > 1.5 m (line to "
                    "NGSIM centre); + control points", (6, 15), text, 0.38, (255, 255, 255), 1,
                    cv2.LINE_AA)  # fmt: skip
        cv2.imwrite(str(out / "frame_errors.png"), image)

    xs = [p[0] for r in rows for p in (r["camera_m"], r["reference_m"])]
    ys = [p[1] for r in rows for p in (r["camera_m"], r["reference_m"])]
    x0, x1 = math.floor(min(xs)) - 5, math.ceil(max(xs)) + 5
    y0, y1 = math.floor(min(ys)) - 5, math.ceil(max(ys)) + 5
    scale = 9
    caption = ("junction frame, +y = Lankershim NB", "grey dot: NGSIM centre; line to camera",
               "point: blue |lateral| <= 1.5 m, orange", "> 1.5 m; +: control points;",
               "rectangle: the four stop lines")  # fmt: skip
    head = 18 * len(caption) + 12
    ground = np.full(((y1 - y0) * scale + head, (x1 - x0) * scale, 3), SURFACE, np.uint8)

    def gp(p) -> tuple[int, int]:
        return int((p[0] - x0) * scale), int((y1 - p[1]) * scale) + head

    for lane in lanes:
        cv2.polylines(ground, [np.array([gp(p) for p in lane.polygon_m], np.int32)], True,
                      RULE, 1, cv2.LINE_AA)  # fmt: skip
    cv2.rectangle(ground, gp((box["W"], box["N"])), gp((box["E"], box["S"])), INK2, 1)
    for r in sorted(rows, key=lambda r: abs(r["lateral_m"])):
        colour = ORANGE if abs(r["lateral_m"]) > LIMIT_M else BLUE
        cv2.line(ground, gp(r["reference_m"]), gp(r["camera_m"]), colour, 1, cv2.LINE_AA)
        cv2.circle(ground, gp(r["reference_m"]), 2, INK2, -1, cv2.LINE_AA)
    for _, g in controls:
        cv2.drawMarker(ground, gp(g), INK, cv2.MARKER_CROSS, 9, 1)
    for i, line in enumerate(caption):
        cv2.putText(ground, line, (8, 20 + 18 * i), text, 0.42, INK, 1, cv2.LINE_AA)
    cv2.line(ground, (12, ground.shape[0] - 14), (12 + 10 * scale, ground.shape[0] - 14), INK, 2)
    cv2.putText(ground, "10 m", (16 + 10 * scale, ground.shape[0] - 10), text, 0.4, INK, 1,
                cv2.LINE_AA)  # fmt: skip
    cv2.imwrite(str(out / "ground_errors.png"), ground)

    note = f"|lateral| as the gate computes it; {verdict}"
    regions = ["all", "approach", "departure", "junction_interior", "unknown"]
    groups = []
    for name in regions:
        values = [abs(r["lateral_m"]) for r in rows if name == "all" or r["region"] == name]
        if values:
            groups.append((f"{name} (n {len(values)})", values))
    for movement in ("through", "left", "right"):
        values = [abs(r["lateral_m"]) for r in rows if r["movement"] == movement]
        if values:
            groups.append((f"movement {movement} (n {len(values)})", values))
    strip_plot(groups, out / "error_by_region.png",
               "|lateral error| by where the NGSIM vehicle was, and by movement",
               note)  # fmt: skip
    per_track = defaultdict(list)
    for r in rows:
        per_track[r["track"]].append(abs(r["lateral_m"]))
    order = sorted(per_track, key=lambda t: float(np.percentile(per_track[t], 90)))
    strip_plot(
        [(f"{t} (n {len(per_track[t])})", per_track[t]) for t in order],
        out / "error_by_track.png",
        "|lateral error| by camera track",
        note,
    )
    components = [("lateral (gate axis)", "lateral_m"), ("along (gate axis)", "along_m"),
                  ("across own heading", "across_heading_m"),
                  ("along own heading", "along_heading_m"), ("along image vertical", "image_up_m"),
                  ("across image vertical", "image_side_m")]  # fmt: skip
    strip_plot(
        [(label, [abs(r[key]) for r in rows]) for label, key in components],
        out / "error_components.png",
        f"The same {len(rows)} error vectors resolved three ways",
        "exploratory decompositions; only the first row is the gate's measure",
    )


if __name__ == "__main__":
    raise SystemExit(main())
