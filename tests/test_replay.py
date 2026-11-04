"""Replay baseline (ADR-022): the observed signal timeline replayed and the observed vehicles as
demand, on the synthetic junction (synthetic_junction.py, synthetic_scenario.py)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import sumolib
from synthetic_clip import CLIP_ID
from synthetic_scenario import EW, NS, SIGNALS, scenario_geometry, timeline, world_model

from cairoflow_contracts.evidence import (
    FrameRef,
    GroundTrack,
    GroundTrackPoint,
    MovementCount,
    MovementTrack,
)
from cairoflow_contracts.experiment import RunSettings
from cairoflow_simulation.demand import DemandError
from cairoflow_simulation.geometry_compiler import (
    CompileError,
    ReplayOptions,
    compile_network,
    drain_segments,
    known_window,
    replay_segments,
    signal_cycle,
)
from cairoflow_simulation.runner import SimulationError, run_baseline
from cairoflow_simulation.trace_demand import (
    VEHICLES_FILE,
    TraceOptions,
    TraceVehicle,
    anchor_to_discharge,
    generate_trace_demand,
    repack,
    serving_green,
    trace_vehicles,
)

WINDOW = (22.0, 142.0)  # E-W onset to E-W onset two cycles later
SPEED_FACTOR = re.compile(r'speedFactor="[0-9.]+"')


def test_the_known_window_skips_unknown_edges_and_refuses_unknown_inside() -> None:
    assert known_window(SIGNALS) == (0.0, 150.0)
    late = [("unknown", 0, 22), *EW[1:]]
    edges = timeline({"N": NS, "E": late, "S": NS, "W": late}, 150.0)
    assert known_window(edges) == (22.0, 150.0)
    inside = [*NS[:3], ("unknown", 52, 77), *NS[4:]]
    with pytest.raises(CompileError):
        known_window(timeline({"N": inside, "E": EW, "S": inside, "W": EW}, 150.0))


def test_replay_segments_follow_the_timeline_second_by_second() -> None:
    segments = replay_segments(SIGNALS, *WINDOW)
    assert sum(d for d, _, _ in segments) == WINDOW[1] - WINDOW[0]
    t = WINDOW[0]
    for duration, states, _ in segments:
        for second in range(int(duration)):
            assert {arm: SIGNALS.state_at(arm, t + second) for arm in states} == states
        t += duration


def test_the_drain_clears_then_repeats_the_cycle_legally() -> None:
    _, _, cycle = signal_cycle(SIGNALS)
    replayed = replay_segments(SIGNALS, 22.0, 30.0)  # ends with E-W green
    drain = drain_segments(replayed[-1], cycle, ReplayOptions(window_s=(22.0, 30.0), drain_s=100))
    assert drain[0][0] == 4.0 and drain[0][1]["E"] == "yellow" and drain[0][1]["N"] == "red"
    assert drain[1][0] == 1.0 and set(drain[1][1].values()) == {"red"}
    assert sum(d for d, _, _ in drain) >= 100 + 60  # covers the drain, one cycle spare
    yellow_first = [(3.0, {"N": "yellow", "E": "red", "S": "yellow", "W": "red"}, {})]
    with pytest.raises(CompileError, match="from red to yellow"):
        drain_segments(replayed[-1], yellow_first, ReplayOptions(window_s=(22.0, 30.0)))


def test_a_replay_network_embeds_the_observed_sequence(tmp_path: Path) -> None:
    replay = ReplayOptions(window_s=WINDOW, drain_s=100)
    network = compile_network(scenario_geometry(), SIGNALS, tmp_path / "net", replay=replay)
    program = network.manifest.signal_program
    assert program.cycle_start_s == WINDOW[0]
    t = 0.0
    for phase in program.phases:  # the first 120 s are the timeline itself, second by second
        if t >= WINDOW[1] - WINDOW[0]:
            break
        for second in range(int(phase.duration_s)):
            observed = {arm: SIGNALS.state_at(arm, WINDOW[0] + t + second) for arm in "NESW"}
            assert phase.arm_states == observed
        t += phase.duration_s
    assert t == WINDOW[1] - WINDOW[0]
    params = {p.name: p for p in network.manifest.parameters}
    assert params["signal.replay_start"].value == 22 and params["signal.replay_end"].value == 142
    assert params["signal.drain_transition"].provenance == "synthetic"
    assert "replay of timeline" in params["signal.cycle"].source
    plain = compile_network(scenario_geometry(), SIGNALS, tmp_path / "plain")
    assert plain.manifest.signal_program.cycle_s == 60  # the default path still cuts one cycle


def _point(t: float, x: float, y: float, speed: float, lane: str | None = None):
    return GroundTrackPoint(frame=FrameRef(clip_id=CLIP_ID, frame_index=int(t), timestamp_s=t),
                            position_m=(x, y), position_error_m=0.3, speed_m_s=speed,
                            lane_id=lane)  # fmt: skip


def _trace_world():
    """South-to-north vehicles: two queued at 22 s (too close for SUMO), one seen far upstream,
    one first seen near the stop line, one gone before the window, one seen only after it."""
    rows = {  # track id: (stop-line lane, stop-line time, points)
        "car:a": ("S1", 30.0, [_point(22, 2, -12, 0.0, "S1"), _point(23, 2, -12, 0.0, "S1")]),
        "car:b": ("S1", 32.0, [_point(22, 2, -17, 0.2, "S1")]),
        "car:c": ("S2", 40.0, [_point(30, 5, -60, 10.0)]),
        "truck:d": ("S1", 50.0, [_point(40, 2, -25, 3.0, "S1")]),
        "car:e": ("S1", 10.0, [_point(5, 2, -20, 5.0, "S1")]),
        "car:f": ("S2", 149.0, [_point(145, 5, -60, 10.0)]),
    }
    tracks = tuple(MovementTrack(track_id=tid, lane_id=lane, stop_line_time_s=t,
                                 exit_line_time_s=t + 2, follows_markings=None)
                   for tid, (lane, t, _) in rows.items())  # fmt: skip
    movement = MovementCount(
        approach="S", exit_arm="N", turn="through", count_veh=len(tracks), flow_veh_per_h=360.0,
        flow_ci95_veh_per_h=(130.0, 780.0), marked_lane_ids=(), tracks=tracks,
    )  # fmt: skip
    ground = tuple(
        GroundTrack(track_id=tid, class_label=tid.split(":")[0], points=tuple(points),
                    unprojected_frames=())
        for tid, (_, _, points) in rows.items()
    )  # fmt: skip
    base = world_model(scenario_geometry())
    return base.model_copy(update={"movements": (movement,), "ground_tracks": ground})


@pytest.fixture(scope="module")
def replay_network(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("replay")
    return compile_network(scenario_geometry(), SIGNALS, root / "net",
                           replay=ReplayOptions(window_s=WINDOW, drain_s=100))  # fmt: skip


def test_observed_vehicles_enter_by_the_three_rules(replay_network) -> None:
    world = _trace_world()
    vehicles, excluded = trace_vehicles(world, replay_network, WINDOW)
    by = {v.track_id: v for v in vehicles}
    assert excluded == {"crossed the stop line before the window": 1,
                        "first observed at or after the window end": 1}  # fmt: skip
    assert [by[t].entry for t in ("car:a", "car:b", "car:c", "truck:d")] == [
        "initial", "initial", "observed", "upstream"]  # fmt: skip
    net = sumolib.net.readNet(str(replay_network.net_path))
    length = net.getEdge("S_in").getLane(0).getLength()
    params = {p.name: p.value for p in replay_network.manifest.parameters}
    s1 = str(int(params["S1.sumo_lane_index"]))
    # a: 2 m before the drawn stop line, so 2 m before SUMO's; at rest, in its observed lane
    a = by["car:a"]
    assert (a.depart_s, a.depart_pos_m, a.depart_lane, a.depart_speed) == (
        0.0, round(length - 2, 2), s1, "0")  # fmt: skip
    # b was 5 m behind a (tighter than SUMO's 7.5 m): moved back to 7.5 m behind
    assert by["car:b"].depart_pos_m == round(a.depart_pos_m - 7.5, 2)
    # c: seen 50 m out at 30 s: enters there and then, at SUMO's safe speed
    c = by["car:c"]
    assert (c.depart_s, c.depart_pos_m, c.depart_speed) == (8.0, round(length - 50, 2), "max")
    # d: first seen 15 m from the stop line: enters at the start, timed for the speed limit
    d = by["truck:d"]
    expected = round(max(18.0 - (length - 15) / params["speed_limit"], 0.0), 1)
    assert (d.depart_pos_m, d.depart_s, d.vehicle_type) == (0.0, expected, "truck")
    assert [v.depart_s for v in vehicles] == sorted(v.depart_s for v in vehicles)


def test_trace_demand_files_differ_only_in_speed_factors(replay_network, tmp_path: Path) -> None:
    world = _trace_world()
    settings = RunSettings(seeds=(1, 2), warmup_s=0, horizon_s=120, drain_s=100)
    demand = generate_trace_demand(world, replay_network, settings, tmp_path / "d", WINDOW)
    assert (tmp_path / "d" / VEHICLES_FILE).is_file()
    one, two = ((tmp_path / "d" / f"seed_{s}.rou.xml").read_text("utf-8") for s in (1, 2))
    assert one != two and SPEED_FACTOR.sub("", one) == SPEED_FACTOR.sub("", two)
    assert [f.vehicles for f in demand.files] == [4, 4]
    assert "entries: initial 2, observed 1, upstream 1" in demand.notes
    with pytest.raises(DemandError, match="warm-up 0"):
        generate_trace_demand(world, replay_network, RunSettings(seeds=(1,)), tmp_path / "x",
                              WINDOW)  # fmt: skip
    # without the upstream rule, d enters where it was first observed, 15 m out
    vehicles, _ = trace_vehicles(world, replay_network, WINDOW, TraceOptions(upstream_within_m=0))
    d = {v.track_id: v for v in vehicles}["truck:d"]
    assert (d.entry, d.depart_s, d.depart_speed) == ("observed", 18.0, "max")


def test_cross_street_entries_join_the_queue_of_the_red_before_their_green(replay_network) -> None:
    """d crosses at 54 s, in the N-S green from 52 s (red from 20 s): it must arrive at the stop
    line one gap before 52 s; a crossing outside every green keeps the plain upstream timing."""
    world = _trace_world()
    tracks = list(world.movements[0].tracks)
    tracks[3] = tracks[3].model_copy(update={"stop_line_time_s": 54.0})
    moved = world.model_copy(update={"movements": (world.movements[0].model_copy(
        update={"tracks": tuple(tracks)}),)})  # fmt: skip
    vehicles, _ = trace_vehicles(moved, replay_network, WINDOW, timeline=SIGNALS)
    d = {v.track_id: v for v in vehicles}["truck:d"]
    params = {p.name: p.value for p in replay_network.manifest.parameters}
    length = sumolib.net.readNet(str(replay_network.net_path)).getEdge("S_in").getLane(0)
    expected = round(52.0 - 2.0 - length.getLength() / params["speed_limit"] - WINDOW[0], 1)
    assert (d.entry, d.depart_pos_m, d.depart_s) == ("discharge", 0.0, max(expected, 0.0))
    # crossing at 50 s (red): no green serves it, so it stays an upstream entry
    plain, _ = trace_vehicles(world, replay_network, WINDOW, timeline=SIGNALS)
    assert {v.track_id: v.entry for v in plain}["truck:d"] == "upstream"


def _entry(vid: str, entry: str, lane: str, pos: float, crossing: float) -> TraceVehicle:
    return TraceVehicle(vehicle_id=vid, track_id=f"car:{vid}", approach="S", exit_arm="N",
                        vehicle_type="passenger", entry=entry, depart_s=0.0, depart_pos_m=pos,
                        depart_lane=lane, depart_speed="0", observed_time_s=52.0,
                        observed_to_stop_m=2.0, observed_speed_m_s=0.0, observed_lane_id="S1",
                        stop_line_time_s=crossing)  # fmt: skip


def test_cross_street_arrivals_before_the_window_start_wait_at_the_stop_line() -> None:
    """A window opening at the onset of the green they used leaves no time to arrive: they were
    queued, so they enter at rest at 0 s behind the lane's initial vehicle, in crossing order
    (DATA_REGISTER.md §21). One with no known lane keeps the discharge timing; the switch off
    restores §19."""
    assert serving_green(SIGNALS, "S", 54.0) == (52.0, 20.0)
    length, limit = 100.0, 13.89
    vehicles = [
        _entry("a", "initial", "1", length - 2.0, 53.0),
        _entry("d2", "upstream", "1", 0.0, 57.0),
        _entry("d1", "upstream", "1", 0.0, 55.0),
        _entry("x", "upstream", "best", 0.0, 56.0),
    ]
    by = {v.vehicle_id: v for v in repack(anchor_to_discharge(
        vehicles, SIGNALS, 52.0, limit, {"S": length}, TraceOptions()))}  # fmt: skip
    assert by["a"].depart_pos_m == length - 2.0
    assert [(by[k].entry, by[k].depart_s, by[k].depart_speed, by[k].depart_pos_m)
            for k in ("d1", "d2")] == [("queued", 0.0, "0", length - 2.0 - 7.5),
                                       ("queued", 0.0, "0", length - 2.0 - 15.0)]  # fmt: skip
    assert (by["x"].entry, by["x"].depart_s, by["x"].depart_pos_m) == ("discharge", 0.0, 0.0)
    off = anchor_to_discharge(vehicles, SIGNALS, 52.0, limit, {"S": length},
                              TraceOptions(queued_at_start=False))  # fmt: skip
    assert {v.entry for v in off} == {"initial", "discharge"}
    # a window opening earlier leaves time to arrive: the same crossings stay discharge entries
    early = anchor_to_discharge(vehicles, SIGNALS, 22.0, limit, {"S": length}, TraceOptions())
    assert {v.entry for v in early} == {"initial", "discharge"}


def test_a_replay_baseline_runs_valid_and_identical(tmp_path: Path) -> None:
    geo = scenario_geometry()
    world = _trace_world()
    settings = RunSettings(seeds=(1, 2), warmup_s=0, horizon_s=120, drain_s=100)
    manifest = run_baseline(world, geo, SIGNALS, tmp_path / "b", settings,
                            replay=ReplayOptions(window_s=WINDOW, drain_s=100))  # fmt: skip
    assert manifest.valid and manifest.reruns_identical
    assert all(r.loaded == r.arrived == 4 for r in manifest.runs)
    longer = settings.model_copy(update={"drain_s": 200})
    with pytest.raises(SimulationError, match="drain is shorter"):
        run_baseline(world, geo, SIGNALS, tmp_path / "c", longer,
                     replay=ReplayOptions(window_s=WINDOW, drain_s=100))  # fmt: skip
