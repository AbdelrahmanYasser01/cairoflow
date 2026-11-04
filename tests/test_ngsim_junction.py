"""NGSIM intersection 2 scenario: frame anchors, protected-left heads, ground world model,
signal estimate, lane layout, compiler support and the committed record."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from synthetic_junction import GATES, LANES, gate, lane

from cairoflow_contracts.evidence import (
    FrameRef,
    IntersectionGeometry,
    MovementCount,
    MovementTrack,
    SignalInterval,
    SignalTimeline,
)
from cairoflow_contracts.experiment import RunSettings
from cairoflow_perception.ngsim_junction import (
    SignalRevision,
    estimate_signal_timeline,
    heads_conflict,
    lane_layout,
)
from cairoflow_perception.world_model import (
    GroundSeries,
    WorldModelError,
    build_world_model_from_ground,
    poisson_interval,
)
from cairoflow_simulation.demand import generate_demand
from cairoflow_simulation.geometry_compiler import (
    CompiledNetwork,
    CompileError,
    CompileOptions,
    _phase_state,
    compile_network,
    signal_cycle,
)
from cairoflow_simulation.network import LinkInfo
from cairoflow_simulation.runner import SimulationError, run_seed

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "data" / "geometry" / "ngsim-lankershim-int2"
CSV_SHA256 = "c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e"
SOURCE = "a" * 64
WINDOW = "window"


def _interval(arm, state, a, b, head="all") -> SignalInterval:
    return SignalInterval(arm=arm, head=head, state=state, start_s=a, end_s=b)


# --- contracts --------------------------------------------------------------------------


def test_geometry_needs_exactly_one_frame_anchor() -> None:
    common = {"geometry_id": "g", "clip_id": "c", "lanes": LANES, "gates": GATES}
    IntersectionGeometry(**common, ground_source_sha256=SOURCE)
    IntersectionGeometry(**common, calibration_spec_sha256=SOURCE)
    for anchors in ({}, {"ground_source_sha256": SOURCE, "calibration_spec_sha256": SOURCE}):
        with pytest.raises(ValidationError, match="exactly one frame anchor"):
            IntersectionGeometry(**common, **anchors)


def test_protected_left_head_rules() -> None:
    common = {"timeline_id": "t", "clip_id": "c", "label": "trajectory-derived", "source": "s"}
    n_all = _interval("N", "red", 0, 20)
    s_all = _interval("S", "red", 0, 20)
    n_left = _interval("N", "green", 0, 20, "left")
    s_left = _interval("S", "green", 0, 20, "left")
    ok = SignalTimeline(**common, window_s=20, intervals=(n_all, n_left, s_all, s_left))
    assert ok.heads() == [("N", "all"), ("N", "left"), ("S", "all"), ("S", "left")]
    assert ok.per_second()[0].left_states == {"N": "green", "S": "green"}
    assert ok.state_at("N", 5, "left") == "green" and ok.state_at("N", 5) == "red"
    with pytest.raises(ValidationError, match="conflict: N left green and S green"):
        SignalTimeline(
            **common, window_s=20, intervals=(n_all, n_left, _interval("S", "green", 0, 20))
        )
    with pytest.raises(ValidationError, match="needs an 'all' head"):
        SignalTimeline(**common, window_s=20, intervals=(n_left, s_all))
    assert heads_conflict(("N", "left"), ("S", "all"))
    assert not heads_conflict(("N", "left"), ("S", "left"))
    assert not heads_conflict(("N", "left"), ("N", "all"))
    assert heads_conflict(("E", "all"), ("N", "left"))


# --- world model from published ground positions ---------------------------------------


def _frames(n: int) -> tuple[FrameRef, ...]:
    return tuple(FrameRef(clip_id=WINDOW, frame_index=i, timestamp_s=i / 10) for i in range(n))


def _series(track_id: str, points: list[tuple[float, float]]) -> GroundSeries:
    return GroundSeries(
        track_id,
        track_id.split(":")[0],
        _frames(len(points)),
        tuple(points),
        (1.2,) * len(points),
    )


def _ground_geometry() -> IntersectionGeometry:
    return IntersectionGeometry(
        geometry_id="ground", clip_id=WINDOW, ground_source_sha256=SOURCE, lanes=LANES, gates=GATES
    )


def _u_turn() -> GroundSeries:
    out_and_back = [(2.0, -15.0 + i) for i in range(10)] + [(-2.0, -6.0 - i) for i in range(10)]
    return _series("car:3", out_and_back)


def test_queue_reaching_the_end_of_the_observed_lane_is_censored() -> None:
    # S1 is observed from its stop line (y = -10) to y = -35: 25 m
    near = _series("car:1", [(1.5, -12.0)] * 50)
    far = _series("car:2", [(1.5, -20.0)] * 50)
    edge = _series("car:3", [(1.5, -28.0)] * 50)  # within 12 m of the lane's far end
    model = build_world_model_from_ground(
        WINDOW, _frames(50), 5.0, [near, far, edge], _ground_geometry(), SOURCE
    )
    queue = next(q for q in model.queues if q.lane_id == "S1")
    assert queue.track_ids == ("car:1", "car:2", "car:3") and queue.length_m == pytest.approx(18)
    assert queue.censored


def test_world_model_from_ground_counts_and_queues() -> None:
    through = _series("car:1", [(2.0, -30.0 + i) for i in range(61)])  # 10 m/s, S to N
    queued = _series("car:2", [(1.5, -12.0)] * 100)  # stopped 2 m behind the S stop line
    model = build_world_model_from_ground(
        WINDOW,
        _frames(100),
        10.0,
        [through, queued, _u_turn()],
        _ground_geometry(),
        SOURCE,
        ground_track_interval_s=1.0,
    )
    counts = {(m.approach, m.exit_arm): m.count_veh for m in model.movements}
    assert counts[("S", "N")] == 1 and counts[("S", "S")] == 1
    s_to_n = next(m for m in model.movements if (m.approach, m.exit_arm) == ("S", "N"))
    assert s_to_n.tracks[0].lane_id == "S1"
    assert s_to_n.tracks[0].stop_line_time_s == pytest.approx(2.0)
    assert s_to_n.flow_veh_per_h == pytest.approx(360.0)
    queue = next(q for q in model.queues if q.lane_id == "S1" and q.frame.timestamp_s == 5.0)
    assert queue.track_ids == ("car:2",)
    assert queue.length_m == pytest.approx(2.0) and queue.length_error_m == pytest.approx(1.2)
    assert not queue.censored  # 2 m into a lane observed for 25 m
    assert model.ground_source_sha256 == SOURCE and model.calibration_spec_sha256 is None
    assert len(model.ground_tracks[0].points) == 7  # one point per second, of 61
    with pytest.raises(WorldModelError, match="not derived from this dataset"):
        build_world_model_from_ground(
            WINDOW, _frames(100), 10.0, [through], _ground_geometry(), "b" * 64
        )
    camera = IntersectionGeometry(
        geometry_id="cam", clip_id=WINDOW, calibration_spec_sha256=SOURCE, lanes=LANES, gates=GATES
    )
    with pytest.raises(WorldModelError, match="not derived from this dataset"):
        build_world_model_from_ground(WINDOW, _frames(100), 10.0, [through], camera, SOURCE)


# --- signal estimate ---------------------------------------------------------------------


def _movement(approach, exit_arm, turn, times, window_s=300.0) -> MovementCount:
    low, high = poisson_interval(len(times))
    scale = 3600.0 / window_s
    return MovementCount(
        approach=approach,
        exit_arm=exit_arm,
        turn=turn,
        count_veh=len(times),
        flow_veh_per_h=len(times) * scale,
        flow_ci95_veh_per_h=(low * scale, high * scale),
        marked_lane_ids=(),
        tracks=tuple(
            MovementTrack(
                track_id=f"car:{approach}{turn}{i}",
                lane_id=None,
                stop_line_time_s=float(t),
                exit_line_time_s=float(t) + 2.0,
                follows_markings=None,
            )
            for i, t in enumerate(times)
        ),
    )


def _plan_movements() -> list[MovementCount]:
    """Three 100 s cycles: E (sparse), W, then N and S protected lefts, then N and S through."""
    cycles = (0.0, 100.0, 200.0)
    through = [o + t for o in cycles for t in np.arange(52.0, 91.0, 2.0)]
    n_left = [o + t for o in cycles for t in np.arange(27.0, 42.0, 2.0)] + [153.0]  # straggler
    w_through = [o + t for o in cycles for t in np.arange(7.0, 18.0, 2.0)]
    return [
        _movement("N", "S", "through", through),
        _movement("S", "N", "through", through),
        _movement("N", "E", "left", n_left),
        _movement("S", "W", "left", [o + t for o in cycles for t in (28.0, 30.0)]),
        _movement("N", "W", "right", [60.0, 95.0, 160.0]),
        _movement("W", "E", "through", w_through),
        _movement("W", "N", "left", [o + t for o in cycles for t in (8.0, 12.0)]),
        _movement("E", "W", "through", [o + 2.0 for o in cycles]),
    ]


def test_signal_estimate_recovers_a_known_plan() -> None:
    estimate = estimate_signal_timeline(_plan_movements(), "c", 300.0, "t", "synthetic plan")
    timeline = estimate.timeline
    assert timeline.label == "trajectory-derived"
    assert estimate.heads["N left"].startswith("own")
    assert estimate.heads["S left"].startswith("follows N left")
    assert estimate.heads["W all"].startswith("own")
    assert estimate.heads["E all"].startswith("own, sparse")  # crosses before W, not with it
    # on the 1 s grid: green from the first crossing less 2 s (down); the 4 s yellow ends at the
    # first whole second at or after 0.1 s past the last crossing (up)
    assert timeline.state_at("N", 149.9) == "red" and timeline.state_at("N", 150.0) == "green"
    assert timeline.state_at("N", 186.9) == "green" and timeline.state_at("N", 187.0) == "yellow"
    assert timeline.state_at("N", 190.9) == "yellow" and timeline.state_at("N", 191.0) == "red"
    assert timeline.state_at("W", 105.0) == "green" and timeline.state_at("W", 114.0) == "yellow"
    assert all(float(i.start_s).is_integer() for i in timeline.intervals)
    # with a protected left, only throughs are green evidence; W (18 throughs, too few) adds its
    # permissive lefts
    assert estimate.heads["N all"] == "own: 60 through crossings"
    assert estimate.heads["W all"] == "own: 24 through and left crossings"
    # the straggling left at 153 s runs into S through; the left window ends 1 s before it
    assert any("ended at 149" in a for a in estimate.adjustments)
    assert timeline.state_at("N", 149.5, "left") == "red"
    shares = {(c["approach"], c["turn"]): c["share"] for c in estimate.consistency}
    for key in (("N", "through"), ("S", "through"), ("W", "through"), ("E", "through")):
        assert shares[key] == 1.0
    assert shares[("N", "left")] == pytest.approx(24 / 25)  # the straggler is outside
    cycle = estimate.representative_cycle
    assert cycle["length_s"] == 100.0 and cycle["median_length_s"] == 100.0
    assert cycle["qualifying_cuts"] <= cycle["candidate_cuts"]
    assert cycle["physical_cycles_in_window"] <= 3  # three 100 s cycles in 300 s
    # later cuts do not qualify: E and S left are unknown after their last estimated yellow
    assert cycle["start_s"] == 100.0 and cycle["qualifying_cuts"] >= 1
    assert cycle["distance_from_medians_s"] > 0  # this cycle holds the straggler's longer left


def test_small_counts_do_not_decide_heads() -> None:
    cycles = (0.0, 100.0, 200.0)
    through = [o + t for o in cycles for t in np.arange(52.0, 91.0, 2.0)]
    n_left = [o + t for o in cycles for t in np.arange(27.0, 42.0, 2.0)]
    movements = [
        _movement("N", "S", "through", through),
        _movement("S", "N", "through", through),
        _movement("N", "E", "left", n_left),
        _movement("S", "W", "left", [28.0, 128.0]),  # 2 lefts: too few to follow N's left
        _movement("W", "E", "through", [o + t for o in cycles for t in np.arange(7.0, 18.0, 2.0)]),
        _movement("E", "N", "right", [o + 3.0 for o in cycles]),  # rights only (go on red)
    ]
    estimate = estimate_signal_timeline(movements, "c", 300.0, "t", "synthetic")
    assert estimate.heads["S left"].startswith("none: 2 left crossings, too few")
    assert ("S", "left") not in estimate.timeline.heads()
    # S has no left head, but 60 throughs: its permissive lefts are not used as green evidence
    assert estimate.heads["S all"] == "own: 60 through crossings"
    assert estimate.heads["E all"].startswith("follows W all, assumed: no through and left")


def _moving(timeline: SignalTimeline, head: tuple[str, str], t: float) -> bool:
    return timeline.state_at(head[0], t, head[1]) in ("green", "yellow")


def _plan_v2_movements() -> list[MovementCount]:
    """`_plan_movements` over four cycles, with 22 W throughs (enough for version 1 to use them
    alone); the third cycle's W green serves one through and three permissive lefts."""
    cycles = (0.0, 100.0, 200.0, 300.0)
    through = [o + t for o in cycles for t in np.arange(52.0, 91.0, 2.0)]
    full = (0.0, 100.0, 300.0)
    w_through = [o + t for o in full for t in np.arange(7.0, 14.0, 1.0)] + [207.0]
    w_left = [o + t for o in full for t in (8.0, 12.0)] + [208.0, 210.0, 212.0]
    return [
        _movement("N", "S", "through", through, 400.0),
        _movement("S", "N", "through", through, 400.0),
        _movement("N", "E", "left", [o + t for o in cycles for t in np.arange(27.0, 42.0, 2.0)],
                  400.0),
        _movement("S", "W", "left", [o + t for o in cycles for t in (28.0, 30.0)], 400.0),
        _movement("W", "E", "through", w_through, 400.0),
        _movement("W", "N", "left", w_left, 400.0),
        _movement("E", "W", "through", [o + 2.0 for o in cycles], 400.0),
    ]  # fmt: skip


def test_version_2_counts_permissive_lefts_and_starts_yellow_at_the_last_crossing() -> None:
    """W has 22 throughs, so version 1 uses them alone and drops the third cycle's W green (one
    through, three lefts); version 2 keeps it, and every head's yellow starts at its last
    crossing instead of ending there (DATA_REGISTER.md §22)."""
    movements = _plan_v2_movements()
    v1 = estimate_signal_timeline(movements, "c", 400.0, "t", "synthetic plan")
    v2 = estimate_signal_timeline(movements, "c", 400.0, "t2", "synthetic plan", None,
                                  SignalRevision())  # fmt: skip
    assert v1.heads["W all"] == "own: 22 through crossings"
    assert "W all: 1 crossing at 207 s, outside any window" in v1.dropped
    assert v1.timeline.state_at("W", 209.0) == "red"
    assert v2.heads["W all"] == "own: 31 through and left crossings"
    assert not any(d.startswith("W all") for d in v2.dropped)
    # green from 2 s before the first crossing (205 s); the yellow starts at 213 s, the first
    # whole second 0.1 s past the last crossing (212 s), and ends 4 s later
    w = v2.timeline
    assert (w.state_at("W", 204.9), w.state_at("W", 205.0)) == ("red", "green")
    assert (w.state_at("W", 212.9), w.state_at("W", 213.0)) == ("green", "yellow")
    assert (w.state_at("W", 216.9), w.state_at("W", 217.0)) == ("yellow", "red")
    # N through: last crossing at 190 s; version 1 ends its yellow at 191 s, version 2 starts it
    one = v1.timeline
    assert one.state_at("N", 187.0) == "yellow" and one.state_at("N", 191.0) == "red"
    assert w.state_at("N", 190.9) == "green" and w.state_at("N", 191.0) == "yellow"
    assert w.state_at("N", 195.0) == "red"
    heads = w.heads()
    for a in heads:  # the longer greens still never overlap a conflicting head
        for b in heads:
            if a < b and heads_conflict(a, b):
                assert not any(_moving(w, a, t + 0.5) and _moving(w, b, t + 0.5)
                               for t in range(400))  # fmt: skip
    shares = {(c["approach"], c["turn"]): c["share"] for c in v2.consistency}
    assert shares[("W", "left")] == 1.0 and shares[("W", "through")] == 1.0


# --- lane layout ---------------------------------------------------------------------------


def test_lane_layout_widths_merges_and_caps() -> None:
    rng = np.random.default_rng(3)
    centres = {1: 1.75, 2: 5.25, 3: 8.75, 4: 9.6}  # lane 4 is too close to lane 3: merged
    sizes = {1: 200, 2: 200, 3: 200, 4: 120}
    offsets = np.concatenate([c + rng.normal(0, 0.2, sizes[k]) for k, c in centres.items()])
    ids = np.concatenate([[k] * sizes[k] for k in centres])
    layout, notes = lane_layout(offsets, ids, 100, 2.0)
    assert [x.lane_id for x in layout] == [1, 2, 3]
    assert "lane_id 4" in notes[0] and "merged into lane_id 3" in notes[0]
    assert [x.outer_m - x.inner_m for x in layout] == pytest.approx([3.5, 3.5, 3.5], abs=0.1)
    apart = np.r_[rng.normal(0, 0.1, 200), rng.normal(9, 0.1, 200)]
    wide, _ = lane_layout(apart, np.r_[[1] * 200, [2] * 200], 100, 2.0)
    assert [round(x.outer_m - x.inner_m, 3) for x in wide] == [4.0, 4.0]  # the gap stays out
    single, notes = lane_layout(rng.normal(3, 0.1, 200), np.ones(200, int), 100, 2.0)
    assert len(single) == 1 and "single lane" in notes[0]


# --- compiler: protected left, parallel lefts, right turn on red, U-turns -----------------


def _left_geometry() -> IntersectionGeometry:
    def strip(lane_id, x0, x1, turns):
        return lane(lane_id, "N", [(x0, 10), (x1, 10), (x1, 60), (x0, 60)], turns)

    lanes = (
        strip("N1", -14, -10.5, ("through", "right")),
        strip("N2", -10.5, -7, ("through",)),
        strip("N3", -7, -3.5, ("left",)),
        strip("N4", -3.5, 0, ("left",)),
        lane("S1", "S", [(0, -60), (3.5, -60), (3.5, -10), (0, -10)], ("left", "through")),
    )
    gates = (
        gate("N", "stop_line", (-14, 10), (0, 10)),
        gate("S", "stop_line", (0, -10), (3.5, -10)),
        gate("S", "exit_line", (-7, -10), (0, -10)),
        gate("N", "exit_line", (0, 10), (7, 10)),
    )
    return IntersectionGeometry(
        geometry_id="lefts", clip_id=WINDOW, ground_source_sha256=SOURCE, lanes=lanes, gates=gates
    )


def _left_timeline() -> SignalTimeline:
    through = [("red", 0, 25), ("green", 25, 60), ("yellow", 60, 64), ("red", 64, 125),
               ("green", 125, 160), ("yellow", 160, 164), ("red", 164, 200)]  # fmt: skip
    left = [("green", 0, 20), ("yellow", 20, 24), ("red", 24, 100), ("green", 100, 120),
            ("yellow", 120, 124), ("red", 124, 200)]  # fmt: skip
    intervals = [_interval("N", s, a, b) for s, a, b in through]
    intervals += [_interval("N", s, a, b, "left") for s, a, b in left]
    intervals += [_interval("S", s, a, b) for s, a, b in through]
    return SignalTimeline(
        timeline_id="t",
        clip_id=WINDOW,
        label="trajectory-derived",
        source="test",
        window_s=200,
        intervals=tuple(intervals),
    )


def test_compiler_protected_left_parallel_lefts_and_right_on_red(tmp_path: Path) -> None:
    options = CompileOptions(right_turn_on_red=True)
    network = compile_network(_left_geometry(), _left_timeline(), tmp_path / "net", options)
    connections = (tmp_path / "net" / "connections.con.xml").read_text("utf-8")
    assert 'from="N_in" to="E_out" fromLane="3" toLane="1"' in connections  # innermost: leftmost
    assert 'from="N_in" to="E_out" fromLane="2" toLane="0"' in connections
    nodes = (tmp_path / "net" / "nodes.nod.xml").read_text("utf-8")
    assert 'type="traffic_light_right_on_red"' in nodes
    program = network.manifest.signal_program
    assert program.cycle_start_s == 25 and program.cycle_s == 100
    protected = next(p for p in program.phases if p.left_states.get("N") == "green")
    through = next(p for p in program.phases if p.arm_states.get("N") == "green")
    seen = set()
    for link in network.links:
        seen.add((link.approach, link.turn))
        if link.approach == "N" and link.turn == "l":
            assert protected.state[link.index] == "G"  # the parallel lefts are not foes
            assert through.state[link.index] == "r"  # protected only
        if link.approach == "N" and link.turn == "s":
            assert protected.state[link.index] == "r" and through.state[link.index] == "G"
        if link.approach == "N" and link.turn == "r":
            assert protected.state[link.index] == "s"  # right turn on red, after stopping
        if link.approach == "S" and link.turn == "l":
            assert through.state[link.index] == "g"  # permissive against N through
    assert {("N", "l"), ("N", "s"), ("N", "r"), ("S", "l")} <= seen
    assert protected.adjustable and protected.arm_states.get("N") != "green"
    assert any(p.name == "signal.right_turn_on_red" for p in network.manifest.parameters)
    # a phase with a yellow on any head is a clearance interval and is fixed
    clearance = next(p for p in program.phases if "yellow" in p.left_states.values())
    assert protected.adjustable and through.adjustable and not clearance.adjustable


def test_cycle_start_tolerance_and_closure() -> None:
    timeline = _left_timeline()
    start, end, _ = signal_cycle(timeline, 25.0000001)
    assert (start, end) == (25.0, 125.0)
    with pytest.raises(CompileError, match="no arm turns green at 30"):
        signal_cycle(timeline, 30.0)
    # a cut whose last state cannot lead to its first does not close
    intervals = [
        _interval("N", "red", 0, 10),
        _interval("N", "green", 10, 40),
        _interval("N", "yellow", 40, 44),
        _interval("N", "red", 44, 50),
        _interval("N", "green", 50, 80),
        _interval("N", "yellow", 80, 84),
        _interval("N", "red", 84, 100),
        _interval("S", "red", 0, 30),
        _interval("S", "green", 30, 60),  # red at the cut's start, green at its end (50 s)
        _interval("S", "yellow", 60, 64),
        _interval("S", "red", 64, 100),
    ]
    odd = SignalTimeline(
        timeline_id="t", clip_id="c", label="assumed", source="test", window_s=100,
        intervals=tuple(intervals),
    )  # fmt: skip
    with pytest.raises(CompileError, match="does not close: S goes from green to red"):
        signal_cycle(odd, 10.0)


def test_yellow_foe_makes_a_turn_permissive() -> None:
    links = [
        LinkInfo(index=0, approach="N", turn="s"),
        LinkInfo(index=1, approach="S", turn="l"),
    ]
    state = _phase_state(links, {"N": "yellow", "S": "green"}, {}, lambda a, b: True)
    assert state == "yg"  # the S left yields to N traffic still crossing on yellow


def test_runner_refuses_durations_off_the_step(tmp_path: Path) -> None:
    network = compile_network(_left_geometry(), _left_timeline(), tmp_path / "net")
    phase = network.manifest.signal_program.phases[0]
    odd = network.manifest.model_copy(
        update={
            "signal_program": network.manifest.signal_program.model_copy(
                update={
                    "phases": (
                        phase.model_copy(update={"duration_s": phase.duration_s + 0.5}),
                        *network.manifest.signal_program.phases[1:],
                    ),
                    "cycle_s": network.manifest.signal_program.cycle_s + 0.5,
                }
            )
        }
    )
    bad = CompiledNetwork(net_path=network.net_path, links=network.links, manifest=odd)
    with pytest.raises(SimulationError, match="not a multiple of the 1 s step"):
        run_seed(bad, tmp_path / "missing.rou.xml", 1, RunSettings(), tmp_path / "run")
    assert not (tmp_path / "run").exists()


def test_demand_lists_u_turns_instead_of_failing(tmp_path: Path) -> None:
    geometry = _left_geometry()
    through = _series("car:1", [(2.0, -30.0 + i) for i in range(61)])
    model = build_world_model_from_ground(
        WINDOW, _frames(2000), 200.0, [through, _u_turn()], geometry, SOURCE
    )
    network = compile_network(geometry, _left_timeline(), tmp_path / "net")
    settings = RunSettings(seeds=(1,), warmup_s=0, horizon_s=60)
    demand = generate_demand(model, network, settings, tmp_path / "demand")
    assert [(m.approach, m.turn) for m in demand.movements] == [("S", "through")]
    assert any("u_turn: 1 counted vehicles not simulated" in n for n in demand.notes)


# --- the committed record ------------------------------------------------------------------


@pytest.mark.skipif(not RECORD.exists(), reason="intersection 2 record not generated")
def test_committed_int2_record() -> None:
    geometry = IntersectionGeometry.model_validate_json((RECORD / "geometry.json").read_text())
    timeline = SignalTimeline.model_validate_json((RECORD / "signal_timeline.json").read_text())
    scenario = json.loads((RECORD / "scenario.json").read_text("utf-8"))
    assert geometry.ground_source_sha256 == CSV_SHA256 and geometry.calibration_spec_sha256 is None
    assert {g.arm for g in geometry.gates if g.role == "stop_line"} == {"N", "E", "S", "W"}
    assert all(x.provenance in ("approximated", "synthetic") for x in geometry.lanes)
    assert timeline.label == "trajectory-derived" and ("N", "left") in timeline.heads()
    assert all(float(i.start_s).is_integer() for i in timeline.intervals)
    assert scenario["camera_input"].startswith("none")
    assert scenario["input"]["sha256"] == CSV_SHA256
    assert scenario["input"]["licence"] == "CC BY-SA 3.0 Unported"
    # completeness against NGSIM's own codes (first int_id 2 row per vehicle), per cell
    for cell, n in scenario["code_census"]["cells"].items():
        assert abs(n["counted"] - n["ngsim"]) <= max(3, 0.1 * n["ngsim"]), cell
    # no lane polygon overlaps another (all are rectangles aligned with the frame's axes)
    boxes = [
        (min(x for x, _ in lane_.polygon_m), max(x for x, _ in lane_.polygon_m),
         min(y for _, y in lane_.polygon_m), max(y for _, y in lane_.polygon_m))
        for lane_ in geometry.lanes
    ]  # fmt: skip
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            overlap = max(0, min(a[1], b[1]) - max(a[0], b[0])) * max(
                0, min(a[3], b[3]) - max(a[2], b[2])
            )
            assert overlap < 0.1
    assert sum(1 for x in geometry.lanes if x.arm == "S" and x.direction == "exit") == 3
    cycle = scenario["signal_estimate"]["representative_cycle"]
    assert cycle["qualifying_cuts"] >= 2
    assert cycle["physical_cycles_in_window"] <= 900 / 80  # no more than the window can hold
    assert all(
        x >= 0 for c in scenario["signal_estimate"]["onset_check"].values()
        for x in c["departure_minus_onset_s"]
    )  # fmt: skip
    baseline = scenario["baseline"]
    assert baseline["valid"] and baseline["reruns_identical"]
    assert len(baseline["runs"]) == 5
    assert all(r["teleports"] == 0 and r["collisions"] == 0 for r in baseline["runs"])
    assert all(r["arrived"] == r["loaded"] for r in baseline["runs"])
    assert not baseline["right_turn_on_red"]["ablation_without_it"]["valid"]
    assert all(float(p["duration_s"]).is_integer() for p in baseline["phases"])
    assert "demand-derived" in " ".join(baseline["metrics"]["notes"])
    # camera 2 stays uncalibrated (ADR-018): no calibration file in its v2 record
    cam2 = REPO / "data" / "geometry" / "ngsim-lankershim-cam2" / "v2"
    assert not list(cam2.glob("calibration*.json"))


EXPERIMENT_RECORDS = sorted((REPO / "experiments" / "records").glob("ngsim-int2-experiment-*.json"))
MODEL_TEXT_KEYS = {
    "statement", "rationale", "expected_tradeoffs", "assumptions", "alternative_explanations",
    "preconditions",
}  # fmt: skip


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


@pytest.mark.skipif(not EXPERIMENT_RECORDS, reason="no intersection 2 experiment record")
def test_committed_experiment_record() -> None:
    """The live Muse diagnosis and its simulated candidates: spend, labels, consistency."""
    record = json.loads(EXPERIMENT_RECORDS[0].read_text("utf-8"))
    muse = record["muse_request"]
    assert muse["label"] == "model_generated" and not muse["replayed_from_cache"]
    assert muse["upper_bound_cost_usd"] <= 0.05 and muse["cost_usd_at_published_rates"] <= 0.05
    assert not muse["citation_problems"]
    assert not (_keys(record) & MODEL_TEXT_KEYS)  # no model-written text is committed
    assert len(record["hypotheses"]) >= 2 and len(record["proposals"]) >= 2
    accepted = {o["intervention_id"] for o in record["validation"] if o["status"] == "accepted"}
    experiment = record["experiment"]
    assert experiment["label"] == "simulated"
    assert {c["intervention_id"] for c in experiment["candidates"]} == accepted
    valid = {c["intervention_id"] for c in experiment["candidates"] if c["valid"]}
    for c in experiment["candidates"]:
        assert c["valid"] == all(r["valid"] for r in c["runs"])
        assert all(r["teleports"] == 0 and r["collisions"] == 0 for r in c["runs"])
    # only valid candidates are compared, and the selection follows the published rule
    compared = {c["candidate"] for c in record["evaluation"]["comparisons"]}
    assert compared == valid
    selected = record["report"]["selected_intervention_id"]
    if selected is not None:
        assert all(
            c["verdict"] != "worsens"
            for c in record["evaluation"]["comparisons"]
            if c["candidate"] == selected
        )
    assert record["report"]["trace_problems"] == []


@pytest.mark.skipif(not RECORD.exists(), reason="intersection 2 record not generated")
def test_committed_record_rebuilds_its_network(tmp_path: Path) -> None:
    """The committed geometry and timeline, compiled with the recorded options, give the
    recorded network and signal program."""
    geometry = IntersectionGeometry.model_validate_json((RECORD / "geometry.json").read_text())
    timeline = SignalTimeline.model_validate_json((RECORD / "signal_timeline.json").read_text())
    baseline = json.loads((RECORD / "scenario.json").read_text("utf-8"))["baseline"]
    options = CompileOptions.model_validate(baseline["compile_options"])
    network = compile_network(geometry, timeline, tmp_path / "net", options)
    assert network.manifest.net_sha256 == baseline["net_sha256"]
    assert [(p.name, p.duration_s, p.state) for p in network.manifest.signal_program.phases] == [
        (p["name"], p["duration_s"], p["state"]) for p in baseline["phases"]
    ]
