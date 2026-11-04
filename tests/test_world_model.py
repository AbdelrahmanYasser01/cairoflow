"""World model on synthetic tracks seen by the synthetic camera (synthetic_camera.py).

Junction, right-hand traffic, ground metres with the origin at the centre. The south arm has two
approach lanes between y = -35 and its stop line at y = -10: S1 (x 0 to 3.5, left or through) and
S2 (x 3.5 to 7, through or right). The north and west arms have one approach lane each. Exit
lines lie on the outgoing side of each arm, so a movement is a stop line and then an exit line.
Vehicles drive at 10 m/s and the clip is 20 s at 10 fps.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from pydantic import ValidationError
from synthetic_camera import calibration_spec, project
from synthetic_clip import CLIP_ID, CLIP_SHA, FPS, FRAMES, NOW, PROVENANCE
from synthetic_clip import clip as _clip
from synthetic_clip import frame as _frame
from synthetic_junction import GATES, LANES
from synthetic_junction import gate as _gate
from synthetic_junction import geometry as junction
from synthetic_junction import lane as _lane

from cairoflow_contracts.evidence import (
    IntersectionGeometry,
    MovementCount,
    Track,
    TrackPoint,
    TrafficWorldModel,
)
from cairoflow_perception.calibration import fit_homography
from cairoflow_perception.signals import load_signal_annotation
from cairoflow_perception.world_model import (
    ARMS,
    WorldModelError,
    build_world_model,
    exit_for,
    poisson_interval,
    turn_for,
)

THROUGH = [(5.25, -30.0), (5.25, 30.0)]  # S2, south to north
LEFT = [(1.75, -30.0), (1.75, 3.5), (-25.0, 3.5)]  # S1, south to west
RIGHT = [(5.25, -30.0), (5.25, -3.5), (25.0, -3.5)]  # S2, south to east

# Two-phase plan: E-W green to 10 s, yellow to 13 s, then red to the end of the clip;
# all-red 13-14 s; N-S red until 14 s, then green.
TWO_PHASE = """# synthetic two-phase plan
time_s,arm,state,note
0,N,red,
0,S,red,
0,E,green,
0,W,green,
10,E,yellow,
10,W,yellow,
13,E,red,
13,W,red,all-red
14,N,green,
14,S,green,
"""


@pytest.fixture(scope="module")
def setup():
    spec = calibration_spec(clip_id=CLIP_ID)
    homography, report = fit_homography(spec, now=NOW)
    return spec, report, homography, junction(report.spec_sha256, CLIP_ID), _clip()


def _build(setup, tracks, **kwargs) -> TrafficWorldModel:
    spec, report, _, geometry, clip = setup
    return build_world_model(clip, tracks, geometry, spec, report, now=NOW, **kwargs)


def _drive(waypoints, start_frame: int = 0, speed: float = 10.0) -> dict[int, tuple[float, float]]:
    """Positions along a polyline at constant speed, one per frame, until it ends or the clip."""
    legs = list(zip(waypoints, waypoints[1:], strict=False))
    total = sum(math.dist(a, b) for a, b in legs)
    positions = {}
    for frame in range(start_frame, FRAMES):
        s = (frame - start_frame) / FPS * speed
        if s > total + 1e-9:
            break
        for a, b in legs:
            leg = math.dist(a, b)
            if s <= leg + 1e-9:
                f = s / leg
                positions[frame] = (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))
                break
            s -= leg
    return positions


def _parked(point) -> dict[int, tuple[float, float]]:
    return dict.fromkeys(range(FRAMES), point)


def _track(n: int, positions, extra_px=None, clip_id: str = CLIP_ID) -> Track:
    pixels = {frame: project(*p) for frame, p in positions.items()} | (extra_px or {})
    points = tuple(
        TrackPoint(
            observation_id=f"car:{n}:{frame}",
            frame=_frame(frame, clip_id),
            bbox_xyxy_px=(u - 20, v - 30, u + 20, v),
            contact_point_px=(u, v),
        )
        for frame, (u, v) in sorted(pixels.items())
    )
    return Track(
        track_id=f"car:t{n}",
        clip_id=clip_id,
        class_label="car",
        sam_object_ids=(f"car:{n}",),
        points=points,
        bridged_gaps=0,
        provenance=PROVENANCE,
    )


def _movement(model: TrafficWorldModel, approach: str, exit_arm: str) -> MovementCount:
    return next(m for m in model.movements if (m.approach, m.exit_arm) == (approach, exit_arm))


def test_turning_movements_are_counted_with_their_evidence(setup) -> None:
    tracks = [_track(1, _drive(THROUGH)), _track(2, _drive(LEFT, 20)), _track(3, _drive(RIGHT, 40))]
    model = _build(setup, tracks)

    through = _movement(model, "S", "N")
    assert (through.turn, through.count_veh) == ("through", 1)
    (vehicle,) = through.tracks
    assert (vehicle.track_id, vehicle.lane_id, vehicle.follows_markings) == ("car:t1", "S2", True)
    assert vehicle.stop_line_time_s == pytest.approx(2.0, abs=1e-3)  # 20 m at 10 m/s
    assert vehicle.exit_line_time_s == pytest.approx(4.0, abs=1e-3)

    left = _movement(model, "S", "W")
    assert (left.turn, left.count_veh, left.tracks[0].lane_id) == ("left", 1, "S1")
    right = _movement(model, "S", "E")
    assert (right.turn, right.count_veh, right.tracks[0].lane_id) == ("right", 1, "S2")

    # Every movement the markings allow is listed, observed or not, in a fixed order.
    assert [(m.approach, m.exit_arm, m.turn) for m in model.movements] == [
        ("N", "E", "left"),
        ("N", "S", "through"),
        ("N", "W", "right"),
        ("S", "W", "left"),
        ("S", "N", "through"),
        ("S", "E", "right"),
        ("W", "E", "through"),
        ("W", "S", "right"),
    ]
    assert _movement(model, "N", "S").count_veh == 0
    assert _movement(model, "N", "S").marked_lane_ids == ("N1",)
    assert model.ambiguous == ()


def test_flows_carry_exact_poisson_intervals(setup) -> None:
    assert poisson_interval(0) == pytest.approx((0.0, -math.log(0.025)), rel=1e-9)
    assert poisson_interval(1) == pytest.approx((0.025318, 5.571643), abs=1e-6)
    assert poisson_interval(10) == pytest.approx((4.795389, 18.390356), abs=1e-5)
    with pytest.raises(ValueError):
        poisson_interval(-1)

    model = _build(setup, [_track(1, _drive(THROUGH))])
    scale = 3600 / 20.0  # one vehicle in a 20 s clip
    through = _movement(model, "S", "N")
    assert through.flow_veh_per_h == pytest.approx(180.0)
    low, high = poisson_interval(1)
    assert through.flow_ci95_veh_per_h == pytest.approx((low * scale, high * scale), rel=1e-6)
    unobserved = _movement(model, "W", "E")
    assert unobserved.flow_veh_per_h == 0
    assert unobserved.flow_ci95_veh_per_h[1] == pytest.approx(-math.log(0.025) * scale, rel=1e-6)


def test_a_turn_the_lane_markings_do_not_allow_is_flagged(setup) -> None:
    left_from_s2 = [(5.25, -30.0), (5.25, 3.5), (-25.0, 3.5)]
    model = _build(setup, [_track(1, _drive(left_from_s2))])
    (vehicle,) = _movement(model, "S", "W").tracks
    assert (vehicle.lane_id, vehicle.follows_markings) == ("S2", False)


def test_tracks_with_one_crossing_are_listed_not_counted(setup) -> None:
    entered = _track(1, _drive(THROUGH, start_frame=170))  # clip ends inside the junction
    exited = _track(2, _drive([(5.25, 0.0), (5.25, 30.0)]))  # already past the stop line
    parked = _track(3, _parked((1.75, -20.0)))  # crosses nothing
    model = _build(setup, [entered, exited, parked])

    assert all(m.count_veh == 0 for m in model.movements)
    assert [(a.track_id, a.approach, a.exit_arm) for a in model.ambiguous] == [
        ("car:t1", "S", None),
        ("car:t2", None, "N"),
    ]
    assert "no exit line" in model.ambiguous[0].reason
    assert "without a stop-line crossing" in model.ambiguous[1].reason


def test_queues_chain_stopped_vehicles_back_from_the_stop_line(setup) -> None:
    _, _, homography, _, _ = setup
    tracks = [
        _track(1, _parked((5.25, -12.0))),  # 2 m behind the S stop line
        _track(2, _parked((5.25, -19.0))),  # 7 m behind that one
        _track(3, _parked((5.25, -33.0))),  # 14 m further: beyond the 12 m spacing
        _track(4, _drive([(1.75, -30.0), (1.75, 30.0)])),  # moving in S1
    ]
    model = _build(setup, tracks)

    s2 = [q for q in model.queues if q.lane_id == "S2"]
    assert [q.frame.frame_index for q in s2] == list(range(0, FRAMES, FPS))  # one per second
    expected_error = homography.ground_error_at(project(5.25, -19.0), 2.0)
    for sample in s2:
        assert (sample.arm, sample.queued_veh, sample.track_ids) == ("S", 2, ("car:t1", "car:t2"))
        assert sample.length_m == pytest.approx(9.0, abs=1e-3)
        assert sample.length_error_m == pytest.approx(expected_error, rel=1e-3)
    assert all(q.queued_veh == 0 and q.length_m == 0 for q in model.queues if q.lane_id == "S1")


def test_ground_points_carry_error_speed_and_lane(setup) -> None:
    beyond_horizon = {60: (640.0, 10.0)}  # v = 10 px is above the horizon at 32.4 px
    near_and_far = {f: p for f, p in _drive(THROUGH).items() if f <= 55}
    model = _build(setup, [_track(1, near_and_far, extra_px=beyond_horizon)])
    (ground,) = model.ground_tracks
    assert ground.unprojected_frames == (60,)

    by_frame = {p.frame.frame_index: p for p in ground.points}
    first = by_frame[0]
    assert first.position_m == pytest.approx((5.25, -30.0), abs=1e-3)
    assert first.speed_m_s == pytest.approx(10.0, abs=1e-3)
    assert first.lane_id == "S2"
    assert by_frame[30].lane_id is None  # inside the junction box
    assert 0 < first.position_error_m < by_frame[55].position_error_m  # farther, less certain


def test_inputs_that_do_not_belong_together_are_refused(setup) -> None:
    spec, report, _, geometry, clip = setup
    other = geometry.model_copy(update={"calibration_spec_sha256": "0" * 64})
    with pytest.raises(WorldModelError, match="different calibration"):
        build_world_model(clip, [], other, spec, report)

    unchecked = calibration_spec(clip_id=CLIP_ID, check=[])
    _, unaccepted = fit_homography(unchecked)
    drawn = geometry.model_copy(update={"calibration_spec_sha256": unaccepted.spec_sha256})
    with pytest.raises(WorldModelError, match="not accepted"):
        build_world_model(clip, [], drawn, unchecked, unaccepted)

    foreign = _track(1, _drive(THROUGH), clip_id="other")
    with pytest.raises(WorldModelError, match="belong to the clip"):
        _build(setup, [foreign])
    with pytest.raises(WorldModelError, match="unique"):
        _build(setup, [_track(1, _drive(THROUGH)), _track(1, _drive(LEFT))])


def test_geometry_contracts_reject_bad_drawings() -> None:
    with pytest.raises(ValidationError, match="simple"):
        _lane("X", "S", [(0, -35), (3.5, -10), (3.5, -30), (0, -10)], ("through",))  # bow tie
    with pytest.raises(ValidationError, match="allowed turns"):
        _lane("X", "S", [(0, -35), (3.5, -35), (3.5, -10)])
    with pytest.raises(ValidationError, match="allowed turns"):
        _lane("X", "S", [(0, -35), (3.5, -35), (3.5, -10)], ("left",), direction="exit")
    with pytest.raises(ValidationError, match="junction centre"):
        _gate("S", "stop_line", (-5, 0), (5, 0))
    common = {"geometry_id": "g", "clip_id": CLIP_ID, "calibration_spec_sha256": CLIP_SHA}
    with pytest.raises(ValidationError, match="no stop line"):
        IntersectionGeometry(**common, lanes=LANES, gates=GATES[1:])
    with pytest.raises(ValidationError, match="at most one"):
        IntersectionGeometry(**common, lanes=LANES, gates=(*GATES, GATES[0]))


def test_turns_follow_right_hand_traffic() -> None:
    assert turn_for("N", "S") == "through"
    assert turn_for("N", "E") == "left"  # heading south, east is on the driver's left
    assert turn_for("N", "W") == "right"
    assert turn_for("S", "W") == "left"
    assert turn_for("W", "W") == "u_turn"
    for approach in ARMS:
        for exit_arm in ARMS:
            assert exit_for(approach, turn_for(approach, exit_arm)) == exit_arm


def test_same_inputs_give_the_same_model_in_any_order(setup) -> None:
    tracks = [
        _track(1, _drive(THROUGH)),
        _track(2, _drive(LEFT, 20)),
        _track(3, _parked((5.25, -12.0))),
        _track(4, _drive(THROUGH, start_frame=170)),
    ]
    first = _build(setup, tracks)
    assert _build(setup, list(reversed(tracks))) == first
    assert TrafficWorldModel.model_validate_json(first.model_dump_json()) == first


def _signals(tmp_path: Path, text: str = TWO_PHASE):
    path = tmp_path / "signals.csv"
    path.write_text(text, encoding="utf-8")
    return load_signal_annotation(path, _clip(), label="assumed", source="synthetic test plan")


def _wait_then_go(n: int, wait_y: float, go_frame: int) -> Track:
    """Waits in S2 at y = wait_y, then drives north from `go_frame`."""
    positions = {f: (5.25, wait_y) for f in range(go_frame)}
    return _track(n, positions | _drive([(5.25, wait_y), (5.25, 30.0)], start_frame=go_frame))


def test_queues_are_sampled_at_the_end_of_each_red(setup, tmp_path: Path) -> None:
    _, _, homography, _, _ = setup
    # Start-up lost time: N-S turns green at 14 s; the cars leave at 15 s and 16 s.
    tracks = [_wait_then_go(1, -12.0, 150), _wait_then_go(2, -19.0, 160)]
    model = _build(setup, tracks, signals=_signals(tmp_path))

    # Only N and S have a red that turns green inside the clip; E and W are red at the end.
    assert [(q.frame.frame_index, q.lane_id) for q in model.end_of_red_queues] == [
        (139, "N1"),
        (139, "S1"),
        (139, "S2"),
    ]
    s2 = model.end_of_red_queues[-1]
    assert (s2.queued_veh, s2.track_ids) == (2, ("car:t1", "car:t2"))
    assert s2.length_m == pytest.approx(9.0, abs=1e-3)
    expected_error = homography.ground_error_at(project(5.25, -19.0), 2.0)
    assert s2.length_error_m == pytest.approx(expected_error, rel=1e-3)
    assert model.signal_notes == ()

    # Both cars then cross the stop line on green and are counted.
    assert _movement(model, "S", "N").count_veh == 2
    assert model.signal_timeline is not None and model.signal_timeline.label == "assumed"
    assert model.signal_per_second[14].green_arms == ("N", "S")
    assert model.signal_timeline.annotation_sha256 in model.provenance.input_sha256


def test_red_ends_that_cannot_be_sampled_are_noted(setup, tmp_path: Path) -> None:
    without = _build(setup, [])
    assert without.signal_timeline is None
    assert (without.end_of_red_queues, without.signal_per_second) == ((), ())
    assert "no signal timeline" in without.signal_notes[0]

    hidden = TWO_PHASE.replace("14,N,green,", "14,N,unknown,head hidden by a bus")
    model = _build(setup, [], signals=_signals(tmp_path, hidden))
    assert [q.lane_id for q in model.end_of_red_queues] == ["S1", "S2"]
    assert model.signal_notes == (
        "N red ending at 14.0 s: next state is unknown, so the end of red is unknown",
    )

    other_clip = _signals(tmp_path).model_copy(update={"clip_id": "other"})
    with pytest.raises(WorldModelError, match="another clip"):
        _build(setup, [], signals=other_clip)
