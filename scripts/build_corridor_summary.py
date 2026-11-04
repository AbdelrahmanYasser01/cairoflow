"""Write data/sample/corridor_summary.json: the four NGSIM intersections, from the published file.

    uv run --frozen python scripts/build_corridor_summary.py

Reference data, read from the published Lankershim trajectories: not video analysis, not simulated.
For each `int_id` 1-4 it counts rows and vehicles, gives the centroid of those rows as latitude and
longitude, and the mean speed of rows above the stopped threshold. The output has no wall-clock
field, so the same CSV always gives the same bytes.

Names: only intersection 2 has one in the repository, and docs/DATA_REGISTER.md §14.3 holds it as a
supported hypothesis, not a verified mapping. The others stay unnamed: the dataset's own int_id
to name table is not available.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from cairoflow_perception.ngsim import (
    CA_ZONE5_FTUS,
    DIRECTIONS,
    LocalFrame,
    kinematics,
    load_trajectories,
)
from cairoflow_perception.site_int2 import SITE_ID, SITE_NAME

REPO = Path(__file__).resolve().parents[1]
TRAJECTORIES = "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
OUT_FILE = "data/sample/corridor_summary.json"
INT_IDS = (1, 2, 3, 4)
VIDEO_ANALYSIS_INT_IDS = (2,)  # camera 2 shows intersection 2 only (DATA_REGISTER §14.3)
MOVING_M_S = 0.5  # the stopped threshold of scripts/build_reference_analysis.py
NAME_SOURCE = "docs/DATA_REGISTER.md §14.3"
SCENARIO = "data/geometry/ngsim-lankershim-int2/scenario.json"  # holds the licence text


def direction_counts(vehicle_id: np.ndarray, direction: np.ndarray) -> dict[str, int]:
    """Distinct vehicles seen with each direction code. One vehicle can appear under two."""
    counts = {
        name: int(np.unique(vehicle_id[direction == code]).size)
        for code, name in DIRECTIONS.items()
    }
    other = ~np.isin(direction, list(DIRECTIONS))
    if other.any():
        counts["other"] = int(np.unique(vehicle_id[other]).size)
    return counts


def summarise(path: Path, scenario: Path) -> dict:
    traj = load_trajectories(path)
    recorded = json.loads(scenario.read_text("utf-8"))["input"]
    origin = LocalFrame(float(traj.x_ft.mean()), float(traj.y_ft.mean()))
    speed_m_s, _ = kinematics(traj, origin)
    intersections = []
    for int_id in INT_IDS:
        rows = traj.int_id == int_id
        if not rows.any():
            raise ValueError(f"no rows with int_id {int_id} in {path}")
        lat, lon = CA_ZONE5_FTUS.to_geographic(traj.x_ft[rows].mean(), traj.y_ft[rows].mean())
        speeds = speed_m_s[rows]
        moving = speeds[np.isfinite(speeds) & (speeds >= MOVING_M_S)]
        named = int_id == 2
        intersections.append(
            {
                "int_id": int_id,
                "site_id": SITE_ID if named else None,
                "name": SITE_NAME if named else None,
                "name_status": "supported_hypothesis" if named else "not_recorded",
                "name_source": NAME_SOURCE if named else None,
                "location": {
                    "lat_deg": round(float(lat), 6),
                    "lon_deg": round(float(lon), 6),
                    "method": "centroid of this int_id's rows; not a surveyed point",
                },
                "unique_vehicles": int(np.unique(traj.vehicle_id[rows]).size),
                "rows": int(rows.sum()),
                "mean_moving_speed_m_s": round(float(moving.mean()), 3) if moving.size else None,
                "moving_rows": int(moving.size),
                "vehicles_by_direction": direction_counts(
                    traj.vehicle_id[rows], traj.direction[rows]
                ),
                "has_video_analysis": int_id in VIDEO_ANALYSIS_INT_IDS,
            }
        )
    start_ms, end_ms = int(traj.global_time_ms.min()), int(traj.global_time_ms.max())
    return {
        "record_format": "corridor_summary/1",
        "evidence_label": "reference_data",
        "evidence_kind": "observed",
        "evidence_note": (
            "Counted from the published NGSIM trajectories. Not video analysis and not simulated."
        ),
        "definitions": {
            "rows": "trajectory rows (one vehicle, one 0.1 s frame) with this int_id",
            "unique_vehicles": "distinct vehicle_id values among those rows",
            "mean_moving_speed_m_s": (
                f"mean of per-row speeds at or above {MOVING_M_S} m/s; a speed is the distance "
                "from the same vehicle's row one frame earlier"
            ),
            "vehicles_by_direction": (
                "distinct vehicles per direction code; one vehicle can count under two codes"
            ),
            "has_video_analysis": "camera footage of this intersection has been analysed",
        },
        "source": {
            "path": TRAJECTORIES,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "rows": int(traj.int_id.size),
            "rows_outside_int_1_to_4": int((~np.isin(traj.int_id, INT_IDS)).sum()),
            "first_global_time_ms": start_ms,
            "last_global_time_ms": end_ms,
            "licence_name": recorded["licence"],
            "attribution": recorded["attribution"],
        },
        "intersections": intersections,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trajectories", type=Path, default=REPO / TRAJECTORIES)
    parser.add_argument("--out", type=Path, default=REPO / OUT_FILE)
    args = parser.parse_args(argv)
    summary = summarise(args.trajectories, REPO / SCENARIO)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, indent=1, ensure_ascii=False) + "\n"
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out}: {len(summary['intersections'])} intersections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
