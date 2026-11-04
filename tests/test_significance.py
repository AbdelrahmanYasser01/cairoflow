"""Improvement tests (Holm's step-down correction over paired t tests) and the selection rule."""

from __future__ import annotations

import math

import pytest

from cairoflow_contracts.experiment import (
    CandidateEligibility,
    EvaluationResult,
    MetricResult,
    PairedComparison,
)
from cairoflow_contracts.report import BaselineAssessment
from cairoflow_evaluation.compare import T95, paired_comparison
from cairoflow_evaluation.report import recommend, select_candidate
from cairoflow_evaluation.significance import (
    improvement_tests,
    regularized_beta,
    t_two_sided_p,
)

NOISE = (-0.2, -0.1, 0.0, 0.1, 0.2)  # mean 0, SD sqrt(0.025): SD / sqrt(5) = 0.0707107
SE = math.sqrt(0.025) / math.sqrt(5)


def _comparison(candidate: str, name: str, differences: tuple[float, ...]) -> PairedComparison:
    unit = "veh/h" if name.startswith("throughput") else "s" if name.endswith("_s") else "m"

    def metric(arm: str, seed: int, value: float) -> MetricResult:
        return MetricResult(
            experiment_id="e", arm=arm, seed=seed, metric_name=name, value=value, unit=unit,
            window_s=(300.0, 1200.0), sample_size=10, source_output_file="x",
            source_sha256="a" * 64, calculation_version="0.1.0",
        )  # fmt: skip

    seeds = range(1, len(differences) + 1)
    return paired_comparison(
        candidate,
        [metric("baseline", s, 50.0) for s in seeds],
        [metric(candidate, s, 50.0 + d) for s, d in zip(seeds, differences, strict=True)],
    )


def _shifted(t: float, better: str = "lower") -> tuple[float, ...]:
    """Differences whose paired t (signed so that positive is better) is `t`."""
    mean = -t * SE if better == "lower" else t * SE
    return tuple(round(mean + e, 6) for e in NOISE)


@pytest.mark.parametrize("t", [0.0, 0.3, 1.0, 2.5, 7.0, -3.0])
def test_the_t_distribution_matches_closed_forms(t: float) -> None:
    a = abs(t)
    assert t_two_sided_p(t, 1) == pytest.approx(1 - 2 * math.atan(a) / math.pi, abs=1e-12)
    assert t_two_sided_p(t, 2) == pytest.approx(1 - a / math.sqrt(2 + a * a), abs=1e-12)
    x = a / math.sqrt(1 + a * a / 4)
    assert t_two_sided_p(t, 4) == pytest.approx(1 - 2 * (3 / 8) * x * (1 - x * x / 12), abs=1e-12)


def test_the_t_distribution_agrees_with_the_interval_table() -> None:
    for df, quantile in enumerate(T95, start=1):  # 0.975 quantiles rounded to 3 decimals
        assert t_two_sided_p(quantile, df) == pytest.approx(0.05, abs=2e-4)
    assert t_two_sided_p(math.inf, 4) == 0.0
    assert regularized_beta(0.0, 2, 0.5) == 0.0 and regularized_beta(1.0, 2, 0.5) == 1.0
    with pytest.raises(ValueError):
        t_two_sided_p(1.0, 0)


def test_holm_confirms_only_what_survives_step_down_in_the_better_direction() -> None:
    comparisons = [
        _comparison("I1", "mean_delay_s", _shifted(10.0)),  # far better: p about 6e-4
        _comparison("I2", "mean_delay_s", _shifted(-10.0)),  # far worse: rejected, not better
        _comparison("I1", "mean_queue_m.N", _shifted(3.0)),  # p = 0.040: < 5%, but not Holm
        _comparison("I2", "mean_queue_m.S", _shifted(0.5)),
        _comparison("I1", "mean_queue_m.E", (-1.0,) * 5),  # zero SD: cannot be tested
        _comparison("I1", "throughput_veh_per_h", _shifted(20.0, "higher")),  # never counted
        _comparison("I1", "max_queue_m.N", _shifted(20.0)),  # never counted
    ]
    plan = [(c.candidate, c.metric_name) for c in comparisons]
    tests = {(t.candidate, t.metric_name): t for t in improvement_tests(comparisons, plan)}
    assert set(tests) == {  # throughput and the maximum queue are never planned
        ("I1", "mean_delay_s"),
        ("I1", "mean_queue_m.E"),
        ("I1", "mean_queue_m.N"),
        ("I2", "mean_delay_s"),
        ("I2", "mean_queue_m.S"),
    }
    assert [k for k, t in tests.items() if t.confirmed] == [("I1", "mean_delay_s")]
    worse = tests[("I2", "mean_delay_s")]
    assert worse.t_statistic == pytest.approx(-10.0, abs=1e-4) and not worse.confirmed
    queue = tests[("I1", "mean_queue_m.N")]
    assert queue.p_value == pytest.approx(0.0400, abs=1e-3) and queue.p_value < 0.05
    # rank 3 among 5 planned tests: the untested zero-SD one still counts in the family
    assert queue.holm_threshold == pytest.approx(0.05 / 3)
    untested = tests[("I1", "mean_queue_m.E")]
    assert (untested.t_statistic, untested.p_value, untested.holm_threshold) == (None, None, None)
    assert not untested.confirmed
    assert improvement_tests(list(reversed(comparisons)), plan[::-1]) == improvement_tests(
        comparisons, plan
    )


def test_the_family_is_planned_in_advance_and_untested_claims_still_count() -> None:
    better = _comparison("I1", "mean_delay_s", _shifted(6.0))
    p = t_two_sided_p(6.0, 4)
    assert 0.05 / 17 < p < 0.05 / 5
    planned = [("I1", "mean_delay_s")]
    alone = improvement_tests([better], planned)
    assert alone[0].confirmed and alone[0].holm_threshold == pytest.approx(0.05)

    def first(plan: list[tuple[str, str]]):
        return next(t for t in improvement_tests([better], plan) if t.candidate == "I1")

    # broken candidates' planned claims have no comparison, but they make the family larger
    wider = [*planned, *(("I2", f"mean_queue_m.{arm}") for arm in "NESW")]
    assert first(wider).holm_threshold == pytest.approx(0.05 / 5) and first(wider).confirmed
    widest = [*planned, *((c, f"mean_queue_m.{arm}") for c in ("I2", "I3", "I4", "I5")
                          for arm in "NESW")]  # fmt: skip
    assert first(widest).holm_threshold == pytest.approx(0.05 / 17)
    assert not first(widest).confirmed

    # a claim the data cannot support (a censored queue) is not tested
    key = ("I1", "mean_queue_m.W")
    tests = improvement_tests([_comparison(*key, _shifted(20.0))], [key], untestable={key})
    assert tests[0].p_value is None and not tests[0].confirmed


def _evaluation(comparisons, eligibility=()) -> EvaluationResult:
    return EvaluationResult(
        experiment_id="e", calculation_version="0.1.0", metrics=(), comparisons=tuple(comparisons),
        excluded=(), eligibility=tuple(eligibility), rules_version="0.2.0",
    )  # fmt: skip


def _status(candidate: str, status: str) -> CandidateEligibility:
    return CandidateEligibility(
        candidate=candidate, status=status, run_classes={1: "complete"}, not_inserted={1: 0},
        unfinished=(), reasons=(),
    )  # fmt: skip


def test_the_selection_needs_no_worsening_and_a_confirmed_improvement() -> None:
    strong = _comparison("I1", "mean_delay_s", _shifted(10.0))
    # an uncorrected 'improves' that does not survive Holm is not enough
    weak = _comparison("I2", "mean_delay_s", _shifted(3.0))
    assert weak.verdict == "improves"
    other = _comparison("I2", "mean_queue_m.N", _shifted(0.2))
    evaluation = _evaluation(
        [strong, weak, other], [_status("I1", "compared"), _status("I2", "compared")]
    )
    selected, reason = select_candidate(evaluation)
    assert selected == "I1"
    assert reason == (
        "I1 worsens no metric and its improvement in mean_delay_s survives Holm's correction; it "
        "ranks first."
    )
    assert select_candidate(_evaluation([weak, other], [_status("I2", "compared")])) == (
        None,
        "None selected: I2 has no improvement that survives Holm's correction.",
    )


def _assessment(status: str) -> BaselineAssessment:
    return BaselineAssessment(
        baseline_manifest_sha256="b" * 64, status=status, summary="s", findings=(), evidence={},
        assessed_on="2026-10-28", documented_in="docs",
    )  # fmt: skip


@pytest.mark.parametrize("status", ["partially_validated", "not_validated"])
def test_a_selection_is_a_recommendation_only_under_a_validated_baseline(status: str) -> None:
    assert recommend("I1", _assessment("validated")) == (
        "I1",
        "I1 is recommended: selected under a validated baseline.",
    )
    assert recommend("I1", _assessment(status)) == (
        None,
        f"Nothing is recommended: I1 was selected in simulation, but the baseline is "
        f"{status.replace('_', ' ')}, so the result is not a recommendation.",
    )
    assert recommend("I1", None)[0] is None  # not assessed
    assert recommend(None, _assessment("validated")) == (
        None,
        "Nothing is recommended: no candidate was selected.",
    )


def test_an_incomplete_or_worsening_candidate_is_never_selected() -> None:
    # I1 would be the strongest candidate, but it is incomplete; its window harm is still named
    i1 = [
        _comparison("I1", "mean_queue_m.N", _shifted(10.0)),
        _comparison("I1", "mean_queue_m.W", _shifted(-10.0)),
    ]
    i2 = [
        _comparison("I2", "mean_delay_s", _shifted(10.0)),
        _comparison("I2", "mean_queue_m.S", _shifted(-10.0)),
    ]
    statuses = [
        _status("I1", "incomplete"),
        _status("I2", "compared"),
        _status("I3", "invalid_simulation"),
    ]
    evaluation = _evaluation([*i1, *i2], statuses)
    assert select_candidate(evaluation) == (
        None,
        "None selected: I1 is incomplete and worsens mean_queue_m.W; I2 worsens mean_queue_m.S; "
        "I3 is invalid simulation.",
    )
