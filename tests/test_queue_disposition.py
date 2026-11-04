"""Rules 0.3.0 queue handling (ADR-024): a maximum set by a state both runs share is
uninformative, and vehicles held off the network at their entry point censor that arm's queues.

Found in the intersection 2 replay, where every run's north and west maximum queue was the
constructed start queue at t = 1 s (DATA_REGISTER.md §24)."""

from __future__ import annotations

import math
from pathlib import Path

from cairoflow_contracts.experiment import (
    CandidateEligibility,
    EvaluationResult,
    MetricResult,
    RunSettings,
)
from cairoflow_evaluation.compare import (
    first_difference_s,
    paired_comparison,
    shared_state_seeds,
    signal_states,
    untestable_claims,
)
from cairoflow_evaluation.eligibility import entry_blocking, lane_queues
from cairoflow_evaluation.report import candidate_standings, select_candidate
from cairoflow_evaluation.significance import improvement_tests

SETTINGS = RunSettings(seeds=(1,), warmup_s=0.0, horizon_s=20.0, drain_s=10.0)
NOISE = (-0.2, -0.1, 0.0, 0.1, 0.2)


def _metric(arm: str, seed: int, name: str, value: float) -> MetricResult:
    unit = "s" if name.endswith("_s") else "m"
    return MetricResult(
        experiment_id="e", arm=arm, seed=seed, metric_name=name, value=value, unit=unit,
        window_s=(0.0, 20.0), sample_size=20, source_output_file="x", source_sha256="a" * 64,
        calculation_version="0.1.0",
    )  # fmt: skip


def _pair(name: str, differences: tuple[float, ...], shared: tuple[int, ...] = ()):
    seeds = range(1, len(differences) + 1)
    return paired_comparison(
        "I1",
        [_metric("baseline", s, name, 100.0) for s in seeds],
        [_metric("I1", s, name, 100.0 + d) for s, d in zip(seeds, differences, strict=True)],
        shared,
    )


def test_a_maximum_shared_on_every_seed_is_uninformative_and_keeps_its_raw_values() -> None:
    c = _pair("max_queue_m.N", (0.0,) * 5, shared=(1, 2, 3, 4, 5))
    assert c.verdict == "uninformative" and c.shared_state_seeds == (1, 2, 3, 4, 5)
    assert c.baseline_values == (100.0,) * 5 and c.ci95_difference == (0.0, 0.0)
    # without the evidence it would read as an ordinary 'no change'
    assert _pair("max_queue_m.N", (0.0,) * 5).verdict == "inconclusive"


def test_a_worsening_on_the_other_seeds_stands_but_nothing_else_does() -> None:
    worse = (5.0, 5.1, 4.9, 5.2, 4.8, 5.0, 0.0, 0.0)  # six seeds peak after the plans differ
    assert _pair("max_queue_m.W", worse, shared=(7, 8)).verdict == "worsens"
    mixed = (1.0, -1.0, 0.5, 0.0, 0.0)
    assert _pair("max_queue_m.W", mixed, shared=(4, 5)).verdict == "uninformative"


def test_the_first_difference_comes_from_the_signal_programs(tmp_path: Path) -> None:
    def net(name: str, phases: list[tuple[int, str]]) -> Path:
        body = "".join(f'<phase duration="{d}" state="{s}"/>' for d, s in phases)
        path = tmp_path / name
        path.write_text(f'<net><tlLogic id="C" type="static" offset="0">{body}</tlLogic></net>')
        return path

    base = signal_states(net("a.xml", [(10, "Gr"), (5, "rG")]), 30)
    same_start = signal_states(net("b.xml", [(12, "Gr"), (3, "rG")]), 30)
    assert len(base) == 30 and base[9] == "Gr" and base[10] == "rG" and base[15] == "Gr"
    assert first_difference_s(base, same_start) == 10.0
    assert first_difference_s(base, base) is None


def _steps(values: dict[float, float], lane: str = "N_in_0") -> dict[float, dict[str, float]]:
    return {t: {lane: v} for t, v in values.items()}


def test_shared_seeds_need_both_peaks_before_the_programs_differ_and_equal_rows() -> None:
    early = _steps({1.0: 50.0, 2.0: 10.0, 3.0: 5.0, 4.0: 8.0})
    later = _steps({1.0: 50.0, 2.0: 10.0, 3.0: 60.0, 4.0: 8.0})
    assert shared_state_seeds({1: early}, {1: early}, ["N"], 3.0) == {"N": (1,)}
    assert shared_state_seeds({1: early}, {1: later}, ["N"], 3.0) == {}  # candidate peaks later
    noisy = _steps({1.0: 51.0, 2.0: 10.0, 3.0: 5.0, 4.0: 8.0})
    assert shared_state_seeds({1: early}, {1: noisy}, ["N"], 3.0) == {}  # rows disagree early
    assert shared_state_seeds({1: early}, {1: early}, ["N"], None) == {}  # plan changed nothing


def _write_run(tmp_path: Path, trips: list[str], queues: dict[float, float]) -> tuple[Path, Path]:
    tripinfo = tmp_path / "tripinfo.xml"
    tripinfo.write_text("<tripinfos>" + "".join(trips) + "</tripinfos>")
    queue = tmp_path / "queue.xml"
    rows = "".join(
        f'<data timestep="{t:.2f}"><lanes><lane id="N_in_0" queueing_length="{v}"/></lanes></data>'
        for t, v in queues.items()
    )
    queue.write_text(f"<queue-export>{rows}</queue-export>")
    return tripinfo, queue


def _trip(vid: str, depart: float, delay: float, pos: float) -> str:
    return (f'<tripinfo id="{vid}" depart="{depart}" departLane="N_in_0" departPos="{pos}" '
            f'departDelay="{delay}" arrival="{depart + 30}"/>')  # fmt: skip


def test_entry_waits_come_from_sumo_insertion_delays_whatever_stopped_them(
    tmp_path: Path,
) -> None:
    """Rules 0.3.1: queue.xml's queueing_length counts standing vehicles only, so a wait for a
    gap in moving traffic left no trace there; 0.3.0 missed about half of the waiting."""
    lengths = {"N_in_0": 186.0}
    trips = [
        _trip("SN.1", 8.0, 3.0, 100.0),  # waited 5-8 s while a queue stood at its entry
        _trip("SN.2", 15.0, 3.0, 100.0),  # waited 12-15 s for a gap, no standing queue: counted
        _trip("SN.3", 8.6, 0.6, 100.0),  # a fractional-second delay: never counted
        _trip("SN.4", 25.0, 9.0, 100.0),  # waited 16-25 s: only the 4 s inside the window count
    ]
    queues = {float(t): (85.0 if t < 10 else 0.0) for t in range(20)}
    tripinfo, queue = _write_run(tmp_path, trips, queues)
    assert lane_queues(queue, 0.0, 20.0)[5.0] == {"N_in_0": 85.0}
    assert entry_blocking(tripinfo, lengths, SETTINGS) == {"N": 10.0}
    assert entry_blocking(tripinfo, {"S_in_0": 186.0}, SETTINGS) == {}  # approach lanes only


def _evaluation(blocked_candidate: dict, blocked_baseline: dict) -> EvaluationResult:
    seeds = range(1, 6)
    delay = [_metric("baseline", s, "mean_delay_s", 30.0) for s in seeds]
    better = [_metric("I1", s, "mean_delay_s", 20.0 + e) for s, e in zip(seeds, NOISE, strict=True)]
    queue_base = [_metric("baseline", s, "mean_queue_m.N", 30.0) for s in seeds]
    queue_better = [_metric("I1", s, "mean_queue_m.N", 20.0 + e)
                    for s, e in zip(seeds, NOISE, strict=True)]  # fmt: skip
    comparisons = (
        paired_comparison("I1", delay, better),
        paired_comparison("I1", queue_base, queue_better),
    )
    eligibility = CandidateEligibility(
        candidate="I1", status="compared", run_classes=dict.fromkeys(seeds, "complete"),
        not_inserted=dict.fromkeys(seeds, 0), unfinished=(), reasons=(),
        entry_blocked=blocked_candidate,
    )  # fmt: skip
    result = EvaluationResult(
        experiment_id="e", calculation_version="0.1.0", metrics=(), comparisons=comparisons,
        excluded=(), eligibility=(eligibility,), rules_version="0.3.0",
        baseline_entry_blocked=blocked_baseline,
    )  # fmt: skip
    return result


def test_a_queue_held_off_the_network_is_never_confirmed_as_an_improvement() -> None:
    evaluation = _evaluation({1: {"N": 12.0}}, {})
    assert ("I1", "mean_queue_m.N") in untestable_claims(evaluation)
    tests = improvement_tests(
        evaluation.comparisons,
        [("I1", "mean_delay_s"), ("I1", "mean_queue_m.N")],
        untestable=untestable_claims(evaluation),
    )
    by_metric = {t.metric_name: t for t in tests}
    assert by_metric["mean_delay_s"].confirmed
    assert not by_metric["mean_queue_m.N"].confirmed and by_metric["mean_queue_m.N"].p_value is None


def test_holding_vehicles_off_the_network_longer_than_the_baseline_blocks_selection() -> None:
    """Rules 0.3.1: the paired 95% interval of the per-seed waiting difference decides, as for any
    other harm; one seed's blip is not enough, a consistent excess is."""
    held = _evaluation({s: {"N": 12.0 + s} for s in range(1, 6)}, {1: {"N": 10.0}})
    standing = candidate_standings(held)["I1"]
    assert not standing.eligible and standing.held_off == ("N",)
    assert "harm there cannot be excluded" in select_candidate(held)[1]
    assert select_candidate(held)[0] is None
    steady = _evaluation({s: {"N": 10.0} for s in range(1, 6)}, {})  # no spread, all worse
    assert candidate_standings(steady)["I1"].held_off == ("N",)
    # one seed's 10.8 s (a review finding under 0.3.0) and equal waiting do not block
    blip = _evaluation({1: {"N": 10.8}}, {})
    assert select_candidate(blip)[0] == "I1"
    equal = _evaluation({1: {"N": 10.0}}, {1: {"N": 10.0}})
    assert select_candidate(equal)[0] == "I1"
    assert math.isclose(candidate_standings(equal)["I1"].delay_difference, -10.0)


def test_ties_are_broken_by_intervention_number_not_text() -> None:
    from cairoflow_evaluation.report import id_order

    assert sorted(["I10", "I2", "I1"], key=id_order) == ["I1", "I2", "I10"]
