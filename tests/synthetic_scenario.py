"""A synthetic scenario on the synthetic junction (synthetic_junction.py), shared by tests.

The S lanes are relabelled `approximated` to check that labels carry through. Signal timeline
(150 s, starting 10 s into a 60 s cycle): E-W green 25 s, yellow 3 s, all-red 2 s, then N-S green
25 s, yellow 3 s, all-red 2 s. E-W turns green at 22 s and again at 82 s, so the cut cycle is
[22 s, 82 s). The world model holds counts per movement over a 60 s window and nothing else.
"""

from __future__ import annotations

import hashlib

from synthetic_clip import CLIP_ID, PROVENANCE
from synthetic_junction import LANES, geometry

from cairoflow_contracts.evidence import (
    IntersectionGeometry,
    MovementCount,
    MovementTrack,
    SignalInterval,
    SignalTimeline,
    TrafficWorldModel,
)
from cairoflow_perception.world_model import poisson_interval, turn_for

WINDOW_S = 60.0  # the world model's clip duration
COUNTS = {  # vehicles counted per (approach, exit) in the 60 s window, by class
    ("S", "N"): ["car"] * 7 + ["bus"],
    ("S", "E"): ["car"] * 2,
    ("S", "W"): ["car"] * 2,
    ("N", "S"): ["car"] * 5,
    ("N", "E"): ["car"],
    ("N", "W"): ["car"],
    ("W", "E"): ["car"] * 4,
    ("W", "S"): ["bus"],
}
NS = [("green", 0, 17), ("yellow", 17, 20), ("red", 20, 52), ("green", 52, 77)]
NS += [("yellow", 77, 80), ("red", 80, 112), ("green", 112, 137), ("yellow", 137, 140)]
NS += [("red", 140, 150)]
EW = [("red", 0, 22), ("green", 22, 47), ("yellow", 47, 50), ("red", 50, 82), ("green", 82, 107)]
EW += [("yellow", 107, 110), ("red", 110, 142), ("green", 142, 150)]


def timeline(per_arm: dict[str, list[tuple[str, float, float]]], window: float) -> SignalTimeline:
    return SignalTimeline(
        timeline_id="synthetic-cycle",
        clip_id=CLIP_ID,
        label="assumed",
        source="test plan",
        window_s=window,
        intervals=tuple(
            SignalInterval(arm=arm, state=state, start_s=a, end_s=b)
            for arm in ("N", "E", "S", "W")
            if arm in per_arm
            for state, a, b in per_arm[arm]
        ),
    )


SIGNALS = timeline({"N": NS, "E": EW, "S": NS, "W": EW}, 150.0)


def scenario_geometry() -> IntersectionGeometry:
    lanes = tuple(
        lane.model_copy(update={"provenance": "approximated"}) if lane.arm == "S" else lane
        for lane in LANES
    )
    return geometry("c" * 64, CLIP_ID, lanes=lanes)


def world_model(geo: IntersectionGeometry, counts=COUNTS) -> TrafficWorldModel:
    movements, n = [], 0
    scale = 3600 / WINDOW_S
    for (approach, exit_arm), classes in counts.items():
        tracks = []
        for name in classes:
            n += 1
            tracks.append(
                MovementTrack(
                    track_id=f"{name}:t{n}",
                    lane_id=None,
                    stop_line_time_s=1.0,
                    exit_line_time_s=2.0,
                    follows_markings=None,
                )
            )
        low, high = poisson_interval(len(tracks))
        movements.append(
            MovementCount(
                approach=approach,
                exit_arm=exit_arm,
                turn=turn_for(approach, exit_arm),
                count_veh=len(tracks),
                flow_veh_per_h=len(tracks) * scale,
                flow_ci95_veh_per_h=(low * scale, high * scale),
                marked_lane_ids=(),
                tracks=tuple(tracks),
            )
        )
    return TrafficWorldModel(
        clip_id=CLIP_ID,
        geometry_id=geo.geometry_id,
        geometry_sha256=hashlib.sha256(geo.model_dump_json().encode("utf-8")).hexdigest(),
        calibration_spec_sha256=geo.calibration_spec_sha256,
        tracks_sha256="b" * 64,
        window_s=WINDOW_S,
        ground_tracks=(),
        movements=tuple(movements),
        ambiguous=(),
        queues=(),
        signal_timeline=None,
        signal_per_second=(),
        end_of_red_queues=(),
        signal_notes=(),
        provenance=PROVENANCE,
    )
