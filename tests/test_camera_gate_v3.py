"""Camera gate v3's measurement (ADR-031): heading-relative lateral error and track pairing.

Synthetic, as tests/test_camera_world.py: a linear image-to-ground map, two north-arm lanes and
reference vehicles placed by hand, so every measure has a known value. The cases are the two
mechanisms the gate v2 diagnosis found (an along-road offset handing a track to the next lane;
along-road error counted as lateral inside the junction), and the rules around them.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from cairoflow_contracts.evidence import FrameRef, LanePolygon, Provenance, Track, TrackPoint
from cairoflow_perception.calibration import Homography
from cairoflow_perception.camera_gate_v3 import (
    PARAMETERS,
    THRESHOLDS,
    HeadedReference,
    apply_gate_v3,
    excluded_by_window,
    heading_components,
    measure,
    pair_tracks,
    predictions,
    summarise,
)
from cairoflow_perception.camera_world import Placement, place_track
from cairoflow_perception.ngsim_junction import JunctionFrame

NOW = datetime(2026, 10, 31, tzinfo=UTC)
# image (u, v) -> ground (u/10 - 10, 80 - v/10) metres; a 0 deg frame at the origin keeps them
PLACEMENT = Placement(Homography(np.array([[0.1, 0, -10], [0, -0.1, 80], [0, 0, 1.0]]), 1.0),
                      JunctionFrame(0.0, 0.0, 0.0), check_median_m=0.3)  # fmt: skip
BOX = {"N": 20.0, "S": -20.0, "E": 13.0, "W": -16.0}
SOUTH = 180.0  # junction-frame bearing of a southbound vehicle


def _lane(lane_id: str, x0: float, x1: float) -> LanePolygon:
    return LanePolygon(
        lane_id=lane_id, arm="N", direction="approach",
        polygon_m=((x0, 20.0), (x1, 20.0), (x1, 60.0), (x0, 60.0)),
        allowed_turns=("through",), provenance="approximated", source="test",
    )  # fmt: skip


LANES = (_lane("N1", -6.0, -3.0), _lane("N2", -3.0, 0.0))


def _track(n: int, ground: list[tuple[float, float]], first_frame: int = 0):
    points = []
    for k, (x, y) in enumerate(ground):
        u, v = (x + 10) * 10, (80 - y) * 10
        f = first_frame + k
        points.append(TrackPoint(observation_id=f"c:car:{n}:{f}",
                                 frame=FrameRef(clip_id="c", frame_index=f, timestamp_s=f / 10),
                                 bbox_xyxy_px=(u - 8, v - 20, u + 8, v + 20),
                                 contact_point_px=(u, v + 20)))  # fmt: skip
    track = Track(track_id=f"car:t{n}", clip_id="c", class_label="car",
                  sam_object_ids=(f"car:{n}",), points=tuple(points), bridged_gaps=0,
                  provenance=Provenance(kind="approximated", produced_by="test",
                                        produced_at_utc=NOW))  # fmt: skip
    placed, _ = place_track(track, PLACEMENT, LANES, camera="cam2", window_offset_s=0.0,
                            confidence={})  # fmt: skip
    return placed


def _southbound(x: float, y0: float, frames: int) -> list[tuple[float, float]]:
    return [(x, y0 - k) for k in range(frames)]


def _ref(vehicle: int, x: float, y: float, *, speed: float = 10.0, lane=None) -> HeadedReference:
    return HeadedReference(vehicle_id=vehicle, centre=(x, y), lane=lane, heading_deg=SOUTH,
                           speed_m_s=speed, direction="SB", movement="through")  # fmt: skip


def test_heading_components_follow_the_bearing_convention() -> None:
    assert heading_components(0.5, 2.0, 0.0) == pytest.approx((2.0, 0.5))
    assert heading_components(2.0, 0.5, 90.0) == pytest.approx((2.0, -0.5))
    # southbound: a point 1 m north of the reference is 1 m behind it, 1 m east is to its left
    assert heading_components(1.0, 1.0, SOUTH) == pytest.approx((-1.0, -1.0))


def test_an_along_road_offset_does_not_hand_a_track_to_the_next_lane() -> None:
    # the camera sees a car in N1; its NGSIM twin (101, N1) is 3.9 m behind the camera point
    # along the road, and a car in N2 (102) is 2.2 m across and 1.0 m behind: nearer, but in
    # the wrong lane
    ground = _southbound(-4.5, 50.0, 12)
    track = _track(1, ground)
    refs = {k: [_ref(101, -4.5, y + 3.9, lane=LANES[0]), _ref(102, -2.3, y + 1.0, lane=LANES[1])]
            for k, (_, y) in enumerate(ground)}  # fmt: skip
    pairing = pair_tracks([track], refs)
    assert pairing[track.track_id].vehicle_id == 101
    rows = measure([track], refs, pairing, LANES, BOX)
    metrics = summarise(rows, [track], pairing)
    assert metrics["D_across_heading_p90_m"] == pytest.approx(0.0, abs=1e-3)
    assert metrics["B_point_lane_agreement"] == 1.0
    assert all(r["along_m"] == pytest.approx(3.9, abs=1e-3) for r in rows)  # camera ahead


def test_inside_the_junction_lateral_is_across_the_vehicle_not_an_arm_axis() -> None:
    # southbound across the junction box near its centre line: |x| > |y|, where v1/v2's fallback
    # takes the east-west axis and counts the 2.5 m along error as lateral
    ground = [(-9.6, 8.0 - k) for k in range(12)]
    track = _track(1, ground)
    refs = {k: [_ref(201, -9.6 - 0.3, y + 2.5)] for k, (_, y) in enumerate(ground)}
    pairing = pair_tracks([track], refs)
    rows = measure([track], refs, pairing, LANES, BOX)
    assert {r["region"] for r in rows} == {"junction_interior"}
    metrics = summarise(rows, [track], pairing)
    assert metrics["D_across_heading_p90_m"] == pytest.approx(0.3, abs=1e-3)
    late = [r for r in rows if r["frame"] >= 9]  # reference |y| < 9.6 there
    assert late and all(abs(r["v2_axis_lateral_m"]) == pytest.approx(2.5, abs=1e-3) for r in late)


def test_a_vehicle_takes_the_pieces_of_a_split_track_but_never_two_tracks_at_once() -> None:
    first = _southbound(-4.5, 50.0, 8)
    second = [(-4.5, 42.0 - k) for k in range(8)]
    overlap = [(-4.6, 50.0 - k) for k in range(8)]  # a second track on the same car, same frames
    tracks = [_track(1, first), _track(2, second, first_frame=8), _track(3, overlap)]
    refs = {k: [_ref(301, -4.5, 50.0 - k, lane=LANES[0])] for k in range(16)}
    pairing = pair_tracks(tracks, refs)
    assert pairing["cam2:car:t1"].vehicle_id == 301 and pairing["cam2:car:t2"].vehicle_id == 301
    assert "cam2:car:t3" not in pairing  # costlier (0.1 m across) and on frames 301 already gave


def test_a_track_split_between_two_vehicles_is_not_paired() -> None:
    ground = _southbound(-4.5, 50.0, 10)
    track = _track(1, ground)
    refs = {k: [_ref(401 if k < 5 else 402, -4.5, y)] for k, (_, y) in enumerate(ground)}
    assert pair_tracks([track], refs) == {}  # 5 of 10 frames each: below the 70 % share


def test_frames_beyond_the_windows_are_excluded_and_counted() -> None:
    ground = _southbound(-4.5, 50.0, 10)
    track = _track(1, ground)
    refs = {k: [_ref(501, -4.5 + (3.5 if k >= 8 else 0.0), y, lane=LANES[0])]
            for k, (_, y) in enumerate(ground)}  # fmt: skip
    pairing = pair_tracks([track], refs)
    assert len(pairing[track.track_id].frames) == 8
    assert excluded_by_window([track], refs, pairing) == 2


def _metrics(**changes) -> dict:
    base = {"camera_tracks": 10, "matched_tracks": 9, "A_track_match_share": 0.9,
            "matched_points": 400, "B_points": 300, "B_point_lane_agreement": 0.95,
            "C_tracks": 9, "C_track_lane_agreement": 1.0, "D_across_heading_p90_m": 0.7,
            "J_points": 60, "J_tracks": 4, "J_across_heading_p90_m": 0.9}  # fmt: skip
    return base | changes


def test_the_junction_is_judged_only_with_enough_points_and_tracks() -> None:
    assert apply_gate_v3(_metrics(), "g", "0" * 64).outcome == "go"
    bad_junction = apply_gate_v3(_metrics(J_across_heading_p90_m=1.8), "g", "0" * 64)
    assert bad_junction.outcome == "no_go"
    assert bad_junction.reasons[0].startswith("J_across_heading_p90_m is 1.8")
    few = apply_gate_v3(_metrics(J_points=49, J_across_heading_p90_m=1.8), "g", "0" * 64)
    assert few.outcome == "go" and few.metrics["J_judged"] == 0


def test_the_overall_measures_still_decide() -> None:
    result = apply_gate_v3(_metrics(D_across_heading_p90_m=1.51, C_tracks=7), "g", "0" * 64)
    assert result.outcome == "no_go"
    assert [r.split(" is ")[0] for r in result.reasons] == [
        "C_track_lane_agreement", "D_across_heading_p90_m"]  # fmt: skip


def _rows(direction: str, along: float, n: int, speed: float = 8.0) -> list[dict]:
    return [{"direction": direction, "speed_m_s": speed, "along_m": along}] * n


def test_predictions_are_tested_only_where_enough_moving_points_exist() -> None:
    registered = _rows("SB", 0.2, 40) + _rows("NB", -0.5, 31) + _rows("EB", 3.0, 5)
    other = _rows("SB", -2.0, 40) + _rows("NB", -2.4, 31)
    result = predictions(registered, other)
    assert set(result["1_tested_directions"]) == {"NB", "SB"}  # EB has 5 points
    assert result["1_holds"] is True and result["2_holds"] is True
    assert predictions(_rows("SB", 1.0, 40), other)["1_holds"] is False
    assert predictions(_rows("SB", 0.0, 40), _rows("SB", -0.4, 40))["2_holds"] is False
    stopped = predictions(_rows("SB", 0.0, 40, speed=0.0), _rows("SB", 0.0, 10))
    assert stopped["1_holds"] is None and stopped["2_holds"] is None


# The gate file, the runner and the committed explorations.
REPO = Path(__file__).resolve().parents[1]
GATES = REPO / "data/geometry/ngsim-lankershim-cam2/v2"
GATE_V3, GATE_V2 = (
    GATES / "camera_world_model_gate_v3.json",
    GATES / "camera_world_model_gate_v2.json",
)
SAM_RUN_V2 = REPO / "experiments/runs/ngsim-cam2-sam-20261030T220322Z"
TRAJECTORIES = REPO / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"


def _runner():
    path = REPO / "scripts/build_camera_world_model_v3.py"
    spec = importlib.util.spec_from_file_location("build_camera_world_model_v3", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_gate_file_holds_this_code_s_parameters_and_sync() -> None:
    gate = json.loads(GATE_V3.read_text("utf-8"))
    assert gate["parameters"] == PARAMETERS and gate["thresholds"] == THRESHOLDS
    assert gate["time_sync"]["registered_source_second_at_0830"] == 120.0
    assert gate["time_sync"]["other_candidate_source_second_at_0830"] == 119.7
    assert gate["inputs"]["sam_tracks"]["sha256"] is None  # the run's record pins it


def test_the_committed_explorations_ran_on_this_gate_file() -> None:
    sha = hashlib.sha256(GATE_V3.read_bytes()).hexdigest()
    found = sorted((REPO / "experiments/diagnostics").glob("camera-gate-v3-procedure-on-*"))
    assert len(found) == 2
    for folder in found:
        comparison = json.loads((folder / "comparison.json").read_text("utf-8"))
        assert comparison["mode"] == "exploration" and comparison["gate_file"]["sha256"] == sha


def test_the_runner_refuses_a_changed_gate_and_a_recorded_exploration(tmp_path) -> None:
    runner = _runner()
    gate = json.loads(GATE_V3.read_text("utf-8"))
    gate["parameters"]["across_window_m"] = 4.0
    changed = tmp_path / "gate.json"
    changed.write_text(json.dumps(gate), "utf-8")
    assert runner.main(["--gate", str(changed), "--sam-record", "x.json"]) == 2
    assert runner.main(["--gate", str(GATE_V3), "--explore-inputs-of", str(GATE_V2),
                        "--record"]) == 2  # fmt: skip
    assert runner.main(["--gate", str(GATE_V3)]) == 2  # a gate run names its SAM record


@pytest.mark.skipif(not (SAM_RUN_V2.is_dir() and TRAJECTORIES.exists()),
                    reason="gate v2's SAM run and the trajectories are Git-ignored")  # fmt: skip
def test_exploring_v2_data_reproduces_gate_v2_at_its_own_sync(tmp_path) -> None:
    args = ["--gate", str(GATE_V3), "--explore-inputs-of", str(GATE_V2),
            "--sam-run", str(SAM_RUN_V2), "--out-root", str(tmp_path)]  # fmt: skip
    assert _runner().main(args) == 0
    (out,) = tmp_path.iterdir()
    comparison = json.loads((out / "comparison.json").read_text("utf-8"))
    assert comparison["mode"] == "exploration" and "gate" not in comparison
    v2 = comparison["reported_not_gated"]["sensitivity"]["v1_v2_procedure_at_other_sync"]
    assert v2["D_lateral_p90_m"] == 2.206 and v2["matched_points"] == 870
    assert not (out / "camera_layer.json").exists() and (out / "NOTICE.md").exists()
