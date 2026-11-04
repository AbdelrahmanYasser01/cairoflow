"""Baseline validation: run identity, intervals, the simulated side's timeline and geometry (with
censoring alike), FCD series, discharge headways, the fixed segment, output refusal, and the
replay branch (detection, windows, clipping, pairing)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from pydantic import ValidationError
from synthetic_junction import GATES, LANES

from cairoflow_contracts.evidence import (
    FrameRef,
    IntersectionGeometry,
    MovementCount,
    MovementTrack,
    QueueSample,
    SignalInterval,
    SignalTimeline,
)
from cairoflow_evaluation.baseline_validation import (
    Side,
    SimLane,
    arm_queue_series,
    block_bootstrap_interval,
    centred_speed,
    clip_series,
    clip_timeline,
    cluster_bootstrap_interval,
    compare,
    comparison_windows,
    count_stops,
    discharge_records,
    gate_distance_m,
    headways_from,
    known_throughout,
    new_output_dir,
    observed_depth_m,
    paired_lags,
    pooled_heads,
    program_timeline,
    queue_spacings_m,
    replay_window,
    segment_records,
    series_from_fcd,
    service_windows,
    sim_geometry,
    spread,
    stop_gate_rule,
    tripinfo_sha256_without_device,
    wilson_interval,
)
from cairoflow_perception.ngsim_junction import arm_coords
from cairoflow_perception.world_model import GroundSeries, build_world_model_from_ground
from cairoflow_simulation.network import content_sha256

SOURCE = "a" * 64
WINDOW = "window"


def _frames(n: int, step: float = 1.0) -> tuple[FrameRef, ...]:
    return tuple(
        FrameRef(clip_id=WINDOW, frame_index=i, timestamp_s=round(i * step, 6)) for i in range(n)
    )


def _series(track_id: str, points, step: float = 1.0) -> GroundSeries:
    return GroundSeries(
        track_id, "car", _frames(len(points), step), tuple(points), (0.0,) * len(points)
    )


def _observed() -> IntersectionGeometry:
    return IntersectionGeometry(
        geometry_id="synthetic",
        clip_id=WINDOW,
        ground_source_sha256=SOURCE,
        lanes=LANES,
        gates=GATES,
    )


def _movement(approach, exit_arm, turn, tracks) -> MovementCount:
    return MovementCount(
        approach=approach,
        exit_arm=exit_arm,
        turn=turn,
        count_veh=len(tracks),
        flow_veh_per_h=len(tracks) * 120.0,
        flow_ci95_veh_per_h=(0.0, 10_000.0),
        marked_lane_ids=(),
        tracks=tuple(tracks),
    )


def _track(track_id, stop_s, exit_s, lane="N1") -> MovementTrack:
    return MovementTrack(
        track_id=track_id,
        lane_id=lane,
        stop_line_time_s=stop_s,
        exit_line_time_s=exit_s,
        follows_markings=True,
    )


def _queue(t, arm, lane, tracks, length=0.0, censored=False) -> QueueSample:
    return QueueSample(
        frame=FrameRef(clip_id=WINDOW, frame_index=round(t * 10), timestamp_s=t),
        arm=arm,
        lane_id=lane,
        queued_veh=len(tracks),
        length_m=length,
        length_error_m=0.0,
        track_ids=tuple(tracks),
        unknown_speed_track_ids=(),
        censored=censored,
    )


# --- run identity and output ---------------------------------------------------------------


TRIPINFO = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<!-- generated on {stamp} -->\n<tripinfos>\n'
    '    <tripinfo id="NS.0" depart="2.00" duration="30.00" devices="tripinfo_NS.0{a}" '
    'vType="passenger"/>\n'
    '    <tripinfo id="SN.1" depart="3.00" duration="31.00" devices="tripinfo_SN.1{b}" '
    'vType="truck"/>\n</tripinfos>\n'
)


def test_tripinfo_hash_ignores_only_the_added_fcd_device(tmp_path) -> None:
    recorded = tmp_path / "tripinfo.xml"
    recorded.write_text(TRIPINFO.format(stamp="then", a="", b=""), encoding="utf-8")
    want = content_sha256(recorded, strip_comments=True)
    rerun = TRIPINFO.format(stamp="now", a=" fcd_NS.0", b=" fcd_SN.1")
    assert tripinfo_sha256_without_device(rerun) == (want, 2)
    assert tripinfo_sha256_without_device(rerun.replace("\n", "\r\n")) == (want, 2)
    assert tripinfo_sha256_without_device(TRIPINFO.format(stamp="x", a="", b="")) == (want, 0)
    # any other difference still shows, and only a trip's own device entry is removed
    assert tripinfo_sha256_without_device(rerun.replace("31.00", "32.00"))[0] != want
    other = TRIPINFO.format(stamp="now", a=" fcd_NS.0", b=" fcd_XX.9")
    assert tripinfo_sha256_without_device(other)[1] == 1
    assert tripinfo_sha256_without_device(other)[0] != want


def test_an_output_directory_is_never_overwritten(tmp_path) -> None:
    out = new_output_dir(tmp_path / "run" / "one")
    assert out.is_dir()
    with pytest.raises(FileExistsError):
        new_output_dir(out)
    (tmp_path / "file").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        new_output_dir(tmp_path / "file")


# --- intervals and summaries --------------------------------------------------------------------


def test_wilson_interval_known_values() -> None:
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(1.959964**2 / (10 + 1.959964**2), abs=1e-6)
    low, high = wilson_interval(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-4) and high == pytest.approx(0.7634, abs=1e-4)
    with pytest.raises(ValueError):
        wilson_interval(3, 2)
    with pytest.raises(ValueError):
        wilson_interval(0, 0)


def test_spread_is_over_seeds_and_drops_missing_values() -> None:
    s = spread([1.0, 2.0, 3.0, None, math.nan])
    assert (s.n, s.mean, s.sd, s.min, s.max) == (3, 2.0, 1.0, 1.0, 3.0)
    assert spread([4.0]).sd is None
    empty = spread([])
    assert (empty.n, empty.mean, empty.min) == (0, None, None)


def test_cluster_bootstrap_resamples_whole_clusters() -> None:
    clusters = [[1.0, 1.0], [3.0, 3.0]]
    low, high = cluster_bootstrap_interval(clusters, resamples=500, seed=1)
    # two equal-sized clusters: every resampled pooled mean is 1, 2 or 3
    assert {low, high} <= {1.0, 2.0, 3.0} and low < high
    assert cluster_bootstrap_interval(clusters, resamples=500, seed=1) == (low, high)
    assert cluster_bootstrap_interval([[2.0, 4.0], []]) is None  # one non-empty cluster


def test_block_bootstrap_keeps_a_constant_series_constant() -> None:
    assert block_bootstrap_interval([5.0] * 300, 99, resamples=200) == (5.0, 5.0)
    assert block_bootstrap_interval([1.0] * 150, 99) is None  # under two blocks
    low, high = block_bootstrap_interval(list(np.arange(300.0)), 50, resamples=500, seed=2)
    assert low < 149.5 < high


def test_compare_fills_the_discrepancy_and_overlap_checks() -> None:
    observed = Side(label="derived", value=10.0, unit="s", window="w", interval=(8.0, 12.0))
    sp = spread([10.5, 11.5])
    simulated = Side(label="simulated", value=sp.mean, unit="s", window="w", spread=sp)
    c = compare("k", "q", "s", "m", observed, simulated, "comparable", ["l"])
    assert c.discrepancy == pytest.approx(1.0)
    assert c.simulated_inside_observed_interval is True
    assert c.observed_inside_seed_range is False
    missing = compare(
        "k",
        "q",
        "s",
        "m",
        observed.model_copy(update={"value": None}),
        simulated,
        "not comparable",
        [],
    )
    assert missing.discrepancy is None and missing.observed_inside_seed_range is None
    with pytest.raises(ValidationError):
        Side(label="derived", value=1.0, unit="", window="w")  # every value carries a unit


# --- the simulated side's timeline and geometry ------------------------------------------------

PHASES = [
    (3.0, {"N": "green", "E": "red"}, {}),
    (1.0, {"N": "yellow", "E": "red"}, {}),
    (1.0, {"N": "red", "E": "red"}, {}),
    (3.0, {"N": "red", "E": "green"}, {}),
    (1.0, {"N": "red", "E": "yellow"}, {}),
    (1.0, {"N": "red", "E": "red"}, {}),
]


def test_program_timeline_repeats_the_cycle_and_shifts_the_window() -> None:
    timeline = program_timeline(PHASES, 2.0, 10.0, timeline_id="t", clip_id=WINDOW, source="s")
    rows = [(i.arm, i.state, i.start_s, i.end_s) for i in timeline.intervals]
    assert rows == [
        ("N", "green", 0.0, 1.0),
        ("N", "yellow", 1.0, 2.0),
        ("N", "red", 2.0, 8.0),
        ("N", "green", 8.0, 10.0),
        ("E", "red", 0.0, 3.0),
        ("E", "green", 3.0, 6.0),
        ("E", "yellow", 6.0, 7.0),
        ("E", "red", 7.0, 10.0),
    ]
    assert timeline.label == "assumed"
    assert service_windows(timeline, "E") == [(3.0, 7.0)]
    assert service_windows(timeline, "N") == []  # its last green has no yellow inside
    with pytest.raises(ValueError):
        program_timeline(
            [(2.5, {"N": "green"}, {})], 0, 10, timeline_id="t", clip_id="c", source="s"
        )


def test_stop_gate_rule_on_pooled_heads() -> None:
    heads = pooled_heads(
        [15.0, 22.0, 30.0, 16.0, 23.0, 14.5],
        ["A", "A", "A", "A", "A", "B"],
        [0.0, 0.0, 0.0, 1.0, 1.0, 0.0],
    )
    assert sorted(heads.tolist()) == [14.5, 15.0, 16.0]  # nearest stopped vehicle per lane-step
    # the 10% quantile of 14.5, 15, 16 is 14.6 (linear), less the 0.5 m margin
    assert stop_gate_rule(heads, 0.9, 0.5, min_samples=3) == pytest.approx(14.1)
    with pytest.raises(ValueError):
        stop_gate_rule(heads, min_samples=4)


def _sim(stop: float = 14.0, depth: dict | None = None) -> IntersectionGeometry:
    lanes = [
        SimLane("S1", "S", "approach", 0.0, 3.0),
        SimLane("S2", "S", "approach", 3.0, 6.0),
        SimLane("N_out_0", "N", "exit", -6.0, -3.0),
    ]
    return sim_geometry(
        _observed(),
        lanes,
        {"S": stop},
        {"N": 12.0},
        geometry_id="sim",
        clip_id=WINDOW,
        ground_source_sha256=SOURCE,
        depth_m=depth,
    )


def test_sim_geometry_keeps_the_observed_depth_and_turns() -> None:
    assert observed_depth_m(_observed())["S1"] == pytest.approx(25.0)
    geometry = _sim()
    s1 = next(x for x in geometry.lanes if x.lane_id == "S1")
    along, offset = arm_coords([p[0] for p in s1.polygon_m], [p[1] for p in s1.polygon_m], "S")
    assert (min(along), max(along)) == pytest.approx((14.0, 39.0))
    assert (min(offset), max(offset)) == pytest.approx((0.0, 3.0))
    assert s1.allowed_turns == ("left", "through")
    gates = {(g.arm, g.role): g for g in geometry.gates}
    assert gate_distance_m(gates[("S", "stop_line")]) == pytest.approx(14.0)
    assert gate_distance_m(gates[("N", "exit_line")]) == pytest.approx(12.0)
    with pytest.raises(ValueError):
        sim_geometry(
            _observed(),
            [SimLane("S9", "S", "approach", 0, 3)],
            {"S": 14.0},
            {},
            geometry_id="x",
            clip_id=WINDOW,
            ground_source_sha256=SOURCE,
        )


def _queue_lengths(geometry: IntersectionGeometry, stop: float) -> QueueSample:
    # three stopped cars 2, 9 and 16 m behind the stop line in lane S1 (x = 1.5)
    series = [_series(f"car:{k}", [(1.5, -(stop + d))] * 3) for k, d in enumerate((2, 9, 16))]
    model = build_world_model_from_ground(
        WINDOW, _frames(3), 3.0, series, geometry, SOURCE, ground_track_interval_s=3.0
    )
    return next(q for q in model.queues if q.lane_id == "S1" and q.frame.timestamp_s == 1.0)


def test_queues_are_censored_alike_on_both_sides() -> None:
    observed = _queue_lengths(_observed(), 10.0)  # observed stop line at 10 m, lane 25 m deep
    simulated = _queue_lengths(_sim(14.0), 14.0)  # simulated stop line at 14 m, same depth
    for q in (observed, simulated):
        assert (q.queued_veh, q.length_m, q.censored) == (3, pytest.approx(16.0), True)
    deeper = _queue_lengths(_sim(14.0, {"S1": 60.0, "S2": 60.0}), 14.0)
    assert (deeper.queued_veh, deeper.censored) == (3, False)


def test_series_from_fcd_shifts_clips_and_splits_runs() -> None:
    samples = [
        (299.0, "a", "passenger", 0.0, 50.0),  # before the window
        (300.0, "a", "passenger", 0.0, 40.0),
        (301.0, "a", "passenger", 0.0, 30.0),
        (302.0, "a", "passenger", 150.0, 0.0),  # outside the box
        (300.0, "b", "truck", 5.0, -60.0),
        (301.0, "b", "truck", 5.0, -50.0),
        (303.0, "b", "truck", 5.0, -30.0),  # a missing step: a second run
        (1200.0, "b", "truck", 5.0, -20.0),  # after the window
    ]
    series = series_from_fcd(samples, begin_s=300, window_s=900, clip_id=WINDOW, box_half_m=110)
    got = {s.track_id: ([f.timestamp_s for f in s.frames], s.points_m) for s in series}
    assert got == {
        "car:a": ([0.0, 1.0], ((0.0, 40.0), (0.0, 30.0))),
        "truck:b": ([0.0, 1.0], ((5.0, -60.0), (5.0, -50.0))),
        "truck:b.1": ([3.0], ((5.0, -30.0),)),
    }
    assert {s.class_label for s in series} == {"car", "truck"}
    assert all(e == 0.0 for s in series for e in s.errors_m)


# --- measurements -------------------------------------------------------------------------------


def _timeline() -> SignalTimeline:
    rows = [("red", 0, 10), ("green", 10, 20), ("yellow", 20, 24), ("red", 24, 30)]
    return SignalTimeline(
        timeline_id="t",
        clip_id=WINDOW,
        label="assumed",
        source="test",
        window_s=30.0,
        intervals=tuple(SignalInterval(arm="N", state=s, start_s=a, end_s=b) for s, a, b in rows),
    )


def test_discharge_headways_follow_crossing_order_inside_the_service() -> None:
    queue = _queue(9.9, "N", "N1", ["car:a", "car:b", "car:c", "car:d"])
    tracks = [
        _track("car:b", 12.6, 15.0),
        _track("car:a", 10.5, 13.0),
        _track("car:c", 14.4, 17.0),
        _track("car:d", 25.0, 27.0),  # after the yellow: residual
    ]
    records = discharge_records(
        [queue], [_movement("N", "S", "through", tracks)], _timeline(), _observed()
    )
    (r,) = records
    assert (r.green_s, r.service_end_s, r.head) == (10.0, 24.0, "all")
    assert r.crossings_s == (10.5, 12.6, 14.4)
    assert [k for k, _ in r.headways_s] == [2, 3]
    assert [h for _, h in r.headways_s] == pytest.approx([2.1, 1.8])  # seconds
    assert r.residual_veh == 1
    assert headways_from(records, 3) == [[pytest.approx(1.8)]]
    assert headways_from(records, 2, 2) == [[pytest.approx(2.1)]]
    # a sample taken long before the green belongs to no service window
    early = _queue(5.0, "N", "N1", ["car:a"])
    assert (
        discharge_records(
            [early], [_movement("N", "S", "through", tracks)], _timeline(), _observed()
        )
        == []
    )


def test_arm_queue_series_takes_the_longest_lane_each_second() -> None:
    queues = [
        _queue(0.0, "S", "S1", ["a"], 10.0),
        _queue(0.0, "S", "S2", ["b", "c"], 10.0, censored=True),  # a tie: the later lane wins
        _queue(1.0, "S", "S1", ["a", "d"], 12.0),
        _queue(1.0, "S", "S2", [], 0.0),
    ]
    assert arm_queue_series(queues) == {"S": [(0.0, 10.0, 2, True), (1.0, 12.0, 2, False)]}


def test_queue_spacing_is_front_to_front_along_the_arm() -> None:
    series = [_series(f"car:{k}", [(1.5, -d)] * 3) for k, d in enumerate((12.0, 19.5, 27.0))]
    sample = _queue(1.0, "S", "S1", ["car:0", "car:1", "car:2"], 17.0)
    assert queue_spacings_m([sample, _queue(1.0, "S", "S2", ["car:0"])], series) == [
        [pytest.approx(7.5), pytest.approx(7.5)]
    ]


def test_centred_speed_and_stops() -> None:
    times, points = [0.0, 1.0, 2.0], [(0.0, 0.0), (0.0, 2.0), (0.0, 4.0)]
    assert centred_speed(times, points, 1.0, 0.5) == pytest.approx(2.0)  # m/s
    assert centred_speed(times, points, 0.0, 0.5) == pytest.approx(2.0)  # clamped to [0, 0.5]
    assert centred_speed([0.0], [(0.0, 0.0)], 0.0, 0.5) is None  # too short
    # creeping below 2 m/s is one stop; a second needs a restart above 2 m/s
    assert count_stops([5, 0.5, 0.8, 1.5, 0.2, 3, 0.1]) == 2
    assert count_stops([0.5, 0.5]) == 1 and count_stops([]) == 0


def test_segment_from_the_entry_line_to_the_exit_line() -> None:
    along = [105, 95, 85, 85, 85, 85, 75, 65, 55, 45, 35]  # metres from the centre, 1 s apart
    s = _series("car:x", [(1.5, -d) for d in along])
    late = _series("car:y", [(1.5, -d) for d in (90, 80, 70)])  # already inside the entry line
    movements = [
        _movement(
            "S", "N", "through", [_track("car:x", 7.0, 9.0, "S1"), _track("car:y", 2.0, 2.5, "S1")]
        )
    ]
    records, skipped = segment_records([s, late], movements, ["S"], 100.0)
    (r,) = records
    assert (r.entry_s, r.travel_time_s) == (pytest.approx(0.5), pytest.approx(8.5))
    # whole seconds 1-9: 10, 5, 0, 0, 5, 10, ... m/s: one stop, two seconds below 1 m/s
    assert (r.stops, r.stopped_s, r.samples) == (1, 2.0, 9)
    assert skipped == {"S through: no inbound crossing at 100 m": 1}


# --- the replay branch ------------------------------------------------------------------------


def test_a_replay_baseline_is_detected_from_its_window_parameters() -> None:
    assert replay_window({"speed_limit": 13.89}) is None  # a repeated-cycle baseline
    window = {"signal.replay_start": 118.0, "signal.replay_end": 751.0}
    assert replay_window(window) == (118.0, 751.0)
    for broken in (
        {"signal.replay_start": 118.0},
        {"signal.replay_start": 9, "signal.replay_end": 9},
    ):
        with pytest.raises(ValueError):
            replay_window(broken)


def test_comparison_windows_are_equally_long_on_both_sides() -> None:
    # repeated cycle: the whole observation against [warm-up, warm-up + horizon), as before
    assert comparison_windows(None, 300.0, 900.0, 900.0) == ((0.0, 900.0), (300.0, 1200.0))
    # replay: the observed window against the simulated replay from 0 s
    assert comparison_windows((118.0, 751.0), 0.0, 633.0, 900.0) == ((118.0, 751.0), (0.0, 633.0))
    with pytest.raises(ValueError):
        comparison_windows((118.0, 751.0), 300.0, 633.0, 900.0)  # a warm-up
    with pytest.raises(ValueError):
        comparison_windows((118.0, 751.0), 0.0, 900.0, 900.0)  # horizon longer than the window
    with pytest.raises(ValueError):
        comparison_windows((118.0, 951.0), 0.0, 833.0, 900.0)  # past the observation
    with pytest.raises(ValueError):
        comparison_windows(None, 300.0, 600.0, 900.0)  # repeated cycle of another length


def _partly_known() -> SignalTimeline:
    rows = [
        ("unknown", 0, 5),
        ("red", 5, 10),
        ("green", 10, 20),
        ("yellow", 20, 24),
        ("red", 24, 30),
    ]
    return SignalTimeline(
        timeline_id="t",
        clip_id=WINDOW,
        label="trajectory-derived",
        source="test",
        window_s=30.0,
        intervals=tuple(SignalInterval(arm="N", state=s, start_s=a, end_s=b) for s, a, b in rows),
    )


def test_replay_window_must_have_every_head_known() -> None:
    timeline = _partly_known()
    assert known_throughout(timeline, 5.0, 30.0)
    assert not known_throughout(timeline, 4.0, 30.0)  # one second still unknown
    assert not known_throughout(timeline, 5.0, 31.0)  # beyond the timeline


def test_clip_timeline_shifts_the_window_to_zero() -> None:
    clipped = clip_timeline(_partly_known(), 8.0, 22.0)
    rows = [(i.state, i.start_s, i.end_s) for i in clipped.intervals]
    assert rows == [("red", 0.0, 2.0), ("green", 2.0, 12.0), ("yellow", 12.0, 14.0)]
    assert (clipped.window_s, clipped.clip_id, clipped.label) == (
        14.0,
        WINDOW,
        "trajectory-derived",
    )
    # a yellow cut by the window end closes the service there (alike on both sides)
    assert service_windows(clipped, "N") == [(2.0, 14.0)]
    with pytest.raises(ValueError):
        clip_timeline(_partly_known(), 20.0, 40.0)


def test_clip_series_keeps_the_window_alone_shifted_to_zero() -> None:
    inside = _series("car:a", [(0.0, float(y)) for y in range(30)], step=0.1)  # 0.0-2.9 s
    before = _series("car:b", [(0.0, 1.0)] * 5, step=0.1)  # 0.0-0.4 s
    (clipped,) = clip_series([inside, before], 1.0, 2.0, 0.1)
    assert clipped.track_id == "car:a"
    assert [f.timestamp_s for f in clipped.frames] == pytest.approx([0.1 * k for k in range(10)])
    assert [f.frame_index for f in clipped.frames] == list(range(10))
    assert clipped.points_m[0] == (0.0, 10.0) and clipped.points_m[-1] == (0.0, 19.0)
    assert len(clipped.errors_m) == 10


def test_paired_lags_match_vehicles_to_the_tracks_they_replay() -> None:
    observed = [_movement("N", "S", "through", [_track("car:277", 10.0, 12.0)])]
    simulated = [
        _movement(
            "N",
            "S",
            "through",
            [_track("car:NS.4", 12.5, 15.0), _track("car:NS.9", 3.0, 5.0)],  # NS.9: no pair
        )
    ]
    lags = paired_lags(observed, simulated, {"NS.4": "car:277", "NS.9": "car:999"})
    assert lags == {"N through": [pytest.approx(2.5)]}  # seconds, simulated less observed
    assert paired_lags(observed, simulated, {}) == {}
