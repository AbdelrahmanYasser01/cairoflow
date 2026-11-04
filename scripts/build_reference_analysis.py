"""Build a reference analysis from the published NGSIM trajectories (no camera, no model).

The result has `data_origin: reference_dataset`: real vehicles from the published file, written in
the same shape as a camera analysis, so the twin and analytics can compare the two. It is never
presented as computer-vision output.

    uv run --frozen python scripts/build_reference_analysis.py --start-s 960 --duration-s 60 \
        --id ref-ngsim-int2-960-1020 --out data/sample/analyses
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from cairoflow_contracts.analysis_record import (
    AnalysisRecord,
    ArtifactRef,
    CalibrationState,
    DecisionRef,
    Metric,
    MetricSeries,
    MovementCell,
    StageReceipt,
    TrackEntry,
    VideoSource,
)
from cairoflow_contracts.evidence import Provenance
from cairoflow_perception.analysis_store import quantise, write_analysis
from cairoflow_perception.ngsim import LocalFrame, kinematics, load_trajectories
from cairoflow_perception.ngsim_junction import (
    ARMS,
    OPPOSITE,
    WINDOW_START_MS,
    arm_of,
    junction_rows,
)
from cairoflow_perception.site_int2 import (
    SITE_ID,
    SITE_NAME,
    SYNC_SOURCE_S,
    VALID_BELOW_ROW_PX,
    load_site,
)

REPO = Path(__file__).resolve().parents[1]
TRAJECTORIES = REPO / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
DEMO_MANIFEST = REPO / "data/demo/ngsim-int2-ensemble-control.json"
FPS = 10.0
WIDTH_PX, HEIGHT_PX = 640, 480
ARM_MIN_RADIUS_M = 20.0  # closer than this a position can't name an arm
STOPPED_M_S = 0.5
DEFAULT_WIDTH_M = 1.8
FT_TO_M = 1200 / 3937
# right-hand traffic: the arm you leave by, from the arm you came in on and the turn
LEFT_OF = {"N": "E", "E": "S", "S": "W", "W": "N"}
RIGHT_OF = {"N": "W", "W": "S", "S": "E", "E": "N"}


def vehicle_class(length_m: float) -> str:
    """Class from the published vehicle length. NGSIM's v_class field isn't among the columns the
    fetch keeps, and it has no bus class."""
    if length_m < 3.0:
        return "motorcycle"
    if length_m < 7.5:
        return "car"
    return "truck" if length_m < 10.5 else "bus"


def turn_of(approach: str, exit_arm: str) -> str | None:
    if exit_arm == OPPOSITE[approach]:
        return "through"
    if exit_arm == LEFT_OF[approach]:
        return "left"
    if exit_arm == RIGHT_OF[approach]:
        return "right"
    return None  # same arm: the window cut the vehicle off, it is not a U-turn


def headings_deg(vu: np.ndarray, vv: np.ndarray, speed: np.ndarray) -> np.ndarray:
    """Heading clockwise from junction +y; a stopped row keeps the last moving heading."""
    moving = speed > STOPPED_M_S
    heading = np.degrees(np.arctan2(vu, vv)) % 360
    if not moving.any():
        return np.zeros(len(vu))
    index = np.where(moving, np.arange(len(vu)), -1)
    index = np.maximum.accumulate(index)
    index[index < 0] = int(np.argmax(moving))
    return heading[index]


def footprint_boxes(site, u, v, heading_deg, length_m, width_m):
    """Axis-aligned image box of each vehicle's oriented footprint: centre x, y and size w, h."""
    b = np.radians(heading_deg)
    fx, fy = np.sin(b), np.cos(b)  # forward unit vector in the junction frame
    lx, ly = fy, -fx  # right-hand unit vector
    corners = []
    for sf, sl in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        cu = u + sf * fx * length_m / 2 + sl * lx * width_m / 2
        cv = v + sf * fy * length_m / 2 + sl * ly * width_m / 2
        corners.append(site.project_to_image(cu, cv))
    stack = np.stack(corners)
    low, high = stack.min(axis=0), stack.max(axis=0)
    centre, size = (low + high) / 2, high - low
    return centre[:, 0], centre[:, 1], size[:, 0], size[:, 1]


def repo_version() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
        )
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start-s", type=float, required=True, help="first source second")
    parser.add_argument("--duration-s", type=float, required=True)
    parser.add_argument("--id", required=True, help="analysis id")
    parser.add_argument("--out", type=Path, default=REPO / "data/sample/analyses")
    args = parser.parse_args(argv)
    if args.start_s < SYNC_SOURCE_S or args.start_s + args.duration_s > SYNC_SOURCE_S + 900:
        parser.error("the published trajectories cover source seconds 120 to 1020")

    site = load_site(REPO)
    traj = load_trajectories(TRAJECTORIES)
    speed, _ = kinematics(traj, LocalFrame(site.frame.origin_x_ft, site.frame.origin_y_ft))
    start_ms = WINDOW_START_MS + round((args.start_s - SYNC_SOURCE_S) * 1000)
    rows = junction_rows(traj, site.frame, speed, start_ms, args.duration_s)
    if not len(rows):
        raise SystemExit("no published rows in that window")
    widths = dict(zip(traj.vehicle_id.tolist(), (traj.width_ft * FT_TO_M).tolist(), strict=True))

    frame_count = round(args.duration_s * FPS)
    starts, ends = rows.run_bounds()
    entries: list[TrackEntry] = []
    cols: dict[str, list[np.ndarray]] = {
        name: [] for name in ("x", "y", "w", "h", "gx", "gy", "speed", "heading", "flags")
    }
    first_row = 0
    for track_id, (a, b) in enumerate(zip(starts, ends, strict=True)):
        u, v = rows.u[a:b], rows.v[a:b]
        run_speed = np.nan_to_num(rows.speed_m_s[a:b])
        length_m = float(rows.length_m[a])
        vid = int(rows.vehicle_id[a])
        width_m = widths.get(vid) or DEFAULT_WIDTH_M
        heading = headings_deg(rows.vu[a:b], rows.vv[a:b], run_speed)
        x, y, w, h = footprint_boxes(site, u, v, heading, length_m, width_m)
        flags = np.where(y >= VALID_BELOW_ROW_PX, 8, 0).astype("u1")
        approach = (
            str(arm_of(u[:1], v[:1])[0]) if math.hypot(u[0], v[0]) >= ARM_MIN_RADIUS_M else None
        )
        exit_arm = (
            str(arm_of(u[-1:], v[-1:])[0]) if math.hypot(u[-1], v[-1]) >= ARM_MIN_RADIUS_M else None
        )
        turn = turn_of(approach, exit_arm) if approach and exit_arm else None
        if turn is None:
            exit_arm = None
        n = int(b - a)
        entries.append(
            TrackEntry(
                track_id=track_id,
                first_row=first_row,
                start_frame=int((rows.time_ms[a] - start_ms) // 100),
                n=n,
                vehicle_class=vehicle_class(length_m),
                class_source="size",
                length_m=round(length_m, 2),
                width_m=round(width_m, 2),
                approach=approach,
                exit_arm=exit_arm,
                turn=turn,
                status="complete" if turn else "partial",
                reference_vehicle_id=vid,
            )
        )
        first_row += n
        for name, values in (
            ("x", x), ("y", y), ("w", w), ("h", h), ("gx", u), ("gy", v),
            ("speed", run_speed), ("heading", heading), ("flags", flags),
        ):  # fmt: skip
            cols[name].append(values)

    joined = {name: np.concatenate(values) for name, values in cols.items()}
    columns = quantise(
        x_px=np.clip(joined["x"], 0, 4000),
        y_px=np.clip(joined["y"], 0, 4000),
        w_px=np.clip(joined["w"], 0, 4000),
        h_px=np.clip(joined["h"], 0, 4000),
        ground_x_m=joined["gx"],
        ground_y_m=joined["gy"],
        speed_m_s=joined["speed"],
        heading_deg=joined["heading"],
        confidence=np.ones(len(joined["x"])),
        point_flags=joined["flags"],
    )

    record = build_record(args, site, entries, joined, frame_count)
    directory = args.out / args.id
    final = write_analysis(directory, record, entries, columns)
    size = sum(a.bytes or 0 for a in final.artifacts)
    print(f"{directory}: {len(entries)} tracks, {len(columns)} points, {size / 1e6:.2f} MB")
    return 0


def build_record(args, site, entries, joined, frame_count) -> AnalysisRecord:
    reason = "read from the published NGSIM trajectories; not a camera measurement"
    seconds = args.duration_s

    def metric(metric_id, label, value, unit, scope="site", kind="observed"):
        return Metric(
            metric_id=metric_id, label=label, value=value, unit=unit, scope=scope,
            evidence_kind=kind, status="ungated", reason=reason,
        )  # fmt: skip

    entering = [e for e in entries if e.approach]
    metrics = [
        metric("vehicles.total", "Vehicles in view", float(len(entries)), "veh"),
        metric("flow.entering", "Entering flow", round(len(entering) / seconds * 3600), "veh/h"),
        metric(
            "speed.mean_moving", "Mean moving speed",
            round(float(joined["speed"][joined["speed"] > STOPPED_M_S].mean()), 2), "m/s",
        ),
        metric(
            "stopped.share", "Share of time stopped",
            round(float((joined["speed"] <= STOPPED_M_S).mean()), 3), "share",
        ),
    ]  # fmt: skip
    arms = Counter(e.approach for e in entering)
    metrics += [
        metric(f"vehicles.arm.{arm.lower()}", f"Entering from {arm}", float(arms.get(arm, 0)),
               "veh", f"arm:{arm}")
        for arm in ARMS
    ]  # fmt: skip
    classes = Counter(e.vehicle_class for e in entries)
    metrics += [
        metric(f"vehicles.class.{name}", name.capitalize(), float(count), "veh", f"class:{name}",
               "approximated")
        for name, count in sorted(classes.items())
    ]  # fmt: skip

    bin_s = 10.0 if seconds <= 120 else 60.0
    n_bins = math.ceil(seconds / bin_s)
    series = []
    for arm in ARMS:
        counts = [0.0] * n_bins
        for entry in entering:
            if entry.approach == arm:
                counts[min(int(entry.start_frame / FPS // bin_s), n_bins - 1)] += 1
        series.append(
            MetricSeries(
                series_id=f"entering.arm.{arm.lower()}", label=f"Entering from {arm}", unit="veh",
                scope=f"arm:{arm}", bin_s=bin_s, start_s=0.0, values=tuple(counts),
                evidence_kind="observed", status="ungated", reason=reason,
            )
        )  # fmt: skip

    movements = []
    turning = Counter(
        (e.approach, e.exit_arm, e.turn) for e in entries if e.approach and e.exit_arm and e.turn
    )
    for (approach, exit_arm, turn), count in sorted(turning.items()):
        movements.append(
            MovementCell(
                approach=approach, exit_arm=exit_arm, turn=turn, count=count, status="ungated",
                reason=reason, reference_count=count,
            )
        )  # fmt: skip

    manifest = json.loads(DEMO_MANIFEST.read_text("utf-8"))
    now = datetime.now(UTC).replace(microsecond=0)
    licence = json.loads((REPO / "data/licences/ngsim-lankershim-videos.json").read_text("utf-8"))
    return AnalysisRecord(
        analysis_id=args.id,
        title=f"NGSIM reference, source {args.start_s:.0f}-{args.start_s + seconds:.0f} s",
        mode="precomputed",
        data_origin="reference_dataset",
        site_id=SITE_ID,
        created_at_utc=now,
        source=VideoSource(
            source_id="ngsim-lankershim-trajectories",
            title="NGSIM Lankershim Boulevard vehicle trajectories, 08:30-08:45",
            site_name=SITE_NAME,
            captured="16 June 2005",
            licence_name="CC BY-SA 3.0 (this derived data: CC BY-SA 4.0)",
            licence_url="https://creativecommons.org/licenses/by-sa/4.0/legalcode",
            attribution=licence["attribution"],
            source_file=str(TRAJECTORIES.relative_to(REPO)),
        ),
        fps=FPS,
        frame_count=frame_count,
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        source_start_s=args.start_s,
        window_offset_s=SYNC_SOURCE_S,
        calibration=CalibrationState(
            mode="calibrated",
            ground_frame="local_enu_junction_m",
            image_to_ground=tuple(tuple(float(c) for c in row) for row in site.image_to_junction),
            spec_sha256=site.spec_sha256,
            accepted_along_road=False,
            valid_windows_s=((970.0, 1000.0),),
            valid_below_row_px=VALID_BELOW_ROW_PX,
            origin_lat_deg=site.origin_lat_deg,
            origin_lon_deg=site.origin_lon_deg,
            bearing_deg=site.frame.bearing_deg,
            notes=(
                "Ground positions are published by NGSIM; the image boxes are those positions "
                "projected through the camera homography, so they are approximate.",
            ),
        ),
        stages=(
            StageReceipt(
                stage="read_reference",
                tool="cairoflow_perception.ngsim",
                version=repo_version(),
                licence="MIT (code); data CC BY-SA",
                items_out=len(entries),
            ),
        ),
        metrics=tuple(metrics),
        series=tuple(series),
        movements=tuple(movements),
        artifacts=(
            ArtifactRef(
                role="trajectories", path="trajectories.bin", media_type="application/octet-stream"
            ),
            ArtifactRef(role="tracks", path="tracks.json", media_type="application/json"),
        ),  # fmt: skip
        decision_ref=DecisionRef(
            run_id=manifest["run_dir"],
            outcome="none_selected",
            summary="No intervention selected or recommended: every candidate worsens at least "
            "one metric, and the baseline is only partially validated.",
            linked_to_cv=False,
        ),
        disclosures=(
            "Reference data: these vehicles come from the published NGSIM file, not from video "
            "analysis by this system.",
            "Vehicle classes are inferred from published lengths: NGSIM's v_class field isn't "
            "among the columns fetched here.",
            "Footage and trajectories are from Los Angeles in 2005, not Cairo.",
        ),
        provenance=Provenance(
            kind="observed",
            produced_by="scripts/build_reference_analysis.py",
            produced_at_utc=now,
            parameters={"start_s": args.start_s, "duration_s": seconds},
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
