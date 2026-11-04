"""Camera tracks placed in a world model's frame and lanes, and the lane gate (P1-1).

Synthetic: a linear image-to-ground map, two north-arm lanes and reference vehicles placed by
hand, so every measure has a known value. The real run is scripts/build_camera_world_model.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from cairoflow_contracts.evidence import FrameRef, LanePolygon, Provenance, Track, TrackPoint
from cairoflow_perception.calibration import Homography
from cairoflow_perception.camera_world import (
    Placement,
    Reference,
    apply_gate,
    compare,
    place_track,
    reference_at,
)
from cairoflow_perception.ngsim import US_SURVEY_FOOT_M, Trajectories
from cairoflow_perception.ngsim_junction import JunctionFrame

NOW = datetime(2026, 10, 30, tzinfo=UTC)
# image (u, v) -> ground (u/10 - 10, 80 - v/10) metres; a 0 deg frame at the origin keeps them
PLACEMENT = Placement(Homography(np.array([[0.1, 0, -10], [0, -0.1, 80], [0, 0, 1.0]]), 1.0),
                      JunctionFrame(0.0, 0.0, 0.0), check_median_m=0.3)  # fmt: skip


def _lane(lane_id: str, x0: float, x1: float) -> LanePolygon:
    return LanePolygon(
        lane_id=lane_id, arm="N", direction="approach",
        polygon_m=((x0, 20.0), (x1, 20.0), (x1, 60.0), (x0, 60.0)),
        allowed_turns=("through",), provenance="approximated", source="test",
    )  # fmt: skip


LANES = (_lane("N1", -6.0, -3.0), _lane("N2", -3.0, 0.0))


def _track(n: int, x: float, frames: int = 15) -> Track:
    points = []
    for k in range(frames):
        y = 50.0 - k  # southbound at 10 m/s
        u, v = (x + 10) * 10, (80 - y) * 10
        points.append(TrackPoint(observation_id=f"c:car:{n}:{k}",
                                 frame=FrameRef(clip_id="c", frame_index=k, timestamp_s=k / 10),
                                 bbox_xyxy_px=(u - 8, v - 20, u + 8, v + 20),
                                 contact_point_px=(u, v + 20)))  # fmt: skip
    return Track(track_id=f"car:t{n}", clip_id="c", class_label="car",
                 sam_object_ids=(f"car:{n}",), points=tuple(points), bridged_gaps=0,
                 provenance=Provenance(kind="approximated", produced_by="test",
                                       produced_at_utc=NOW))  # fmt: skip


def _placed(tracks):
    return [place_track(t, PLACEMENT, LANES, camera="cam2", window_offset_s=250.3,
                        confidence={})[0] for t in tracks]  # fmt: skip


def _references(tracks, x_of: dict[int, float]) -> dict[int, list[Reference]]:
    """Each camera track's twin, `x_of[n]` metres across, at the same instants."""
    refs: dict[int, list[Reference]] = {}
    for n, track in enumerate(tracks, start=1):
        for p in track.points:
            centre = (x_of[n], p.position_m[1] + 0.4)
            within = [lane for lane in LANES
                      if lane.polygon_m[0][0] <= centre[0] < lane.polygon_m[1][0]]  # fmt: skip
            lane = within[0] if within else None
            refs.setdefault(p.frame.frame_index, []).append(Reference(100 + n, centre, lane))
    return refs


def test_a_track_is_placed_in_its_lane_with_its_error_and_speed() -> None:
    (track,) = _placed([_track(1, -4.5)])
    assert track.track_id == "cam2:car:t1" and track.lane_id == "N1" and track.lane_share == 1.0
    first = track.points[0]
    assert first.position_m == pytest.approx((-4.5, 50.0))
    assert first.window_time_s == 250.3 and first.lane_status == "in_lane"
    # 2 px at 0.1 m/px, with the 0.3 m calibration term; stored to 0.1 mm
    assert first.position_error_m == pytest.approx(np.hypot(0.2, 0.3), abs=1e-4)
    assert track.points[7].speed_m_s == pytest.approx(10.0)
    assert not first.near_lane_boundary  # 1.5 m from either edge, error 0.36 m
    (edge,) = _placed([_track(2, -3.2)])
    assert edge.points[0].near_lane_boundary  # 0.2 m from the N1/N2 edge
    (inside,) = _placed([_track(3, 5.0)])  # east of both lanes
    assert inside.lane_id is None and {p.lane_status for p in inside.points} == {"outside_lanes"}


def test_the_gate_is_go_when_camera_and_reference_agree() -> None:
    tracks = _placed([_track(n, -4.5 if n % 2 else -1.5) for n in range(1, 10)])
    metrics = compare(tracks, _references(tracks, {n: -4.4 if n % 2 else -1.6
                                                   for n in range(1, 10)}), LANES)  # fmt: skip
    assert metrics["A_track_match_share"] == 1.0 and metrics["B_points"] == 135
    assert metrics["B_point_lane_agreement"] == 1.0 and metrics["C_tracks"] == 9
    assert metrics["D_lateral_p90_m"] == pytest.approx(0.1)
    assert metrics["along_median_m"] == pytest.approx(0.4)
    gate = apply_gate(metrics, "test-gate", "0" * 64)
    assert gate.outcome == "go" and gate.reasons == () and all(gate.checks.values())


def test_the_gate_is_no_go_when_the_reference_is_in_the_next_lane() -> None:
    tracks = _placed([_track(n, -4.5) for n in range(1, 10)])
    metrics = compare(tracks, _references(tracks, dict.fromkeys(range(1, 10), -2.5)), LANES)
    assert metrics["matched_tracks"] == 9 and metrics["B_point_lane_agreement"] == 0.0
    gate = apply_gate(metrics, "test-gate", "0" * 64)
    assert gate.outcome == "no_go"
    assert not gate.checks["B_point_lane_agreement"] and not gate.checks["D_lateral_p90_m"]
    assert gate.reasons[0].startswith("B_point_lane_agreement is 0.0, the rule needs >= 0.85")


def test_too_few_tracks_or_far_references_are_no_go_not_go() -> None:
    tracks = _placed([_track(1, -4.5)])
    far = compare(tracks, _references(tracks, {1: 2.0}), LANES)  # 6.5 m away: never paired
    assert far["matched_tracks"] == 0 and far["A_track_match_share"] == 0.0
    assert apply_gate(far, "g", "0" * 64).outcome == "no_go"
    near = compare(tracks, _references(tracks, {1: -4.4}), LANES)
    gate = apply_gate(near, "g", "0" * 64)
    assert gate.outcome == "no_go" and not gate.checks["C_track_lane_agreement"]  # 1 of 8 tracks


def test_a_reference_centre_is_its_front_moved_back_half_its_length() -> None:
    length_ft = 15.0
    traj = Trajectories(*(np.array(v) for v in (
        [7], [1], [1000], [10.0 / US_SURVEY_FOOT_M], [20.0 / US_SURVEY_FOOT_M], [length_ft],
        [6.0], [1], [2], [0], [4], [1])))  # fmt: skip
    (ref,), missing = reference_at(traj, np.array([180.0]), JunctionFrame(0.0, 0.0, 0.0), LANES,
                                   1000)  # fmt: skip
    # heading 180 deg (southbound): the centre is north of the front by half the length
    assert ref.centre == pytest.approx((10.0, 20.0 + length_ft * US_SURVEY_FOOT_M / 2))
    assert missing == 0
    _, missing = reference_at(traj, np.array([np.nan]), JunctionFrame(0.0, 0.0, 0.0), LANES, 1000)
    assert missing == 1
