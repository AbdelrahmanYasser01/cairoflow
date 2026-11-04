"""The comparator has one implementation of each calculation (ADR-033).

`compare.paired_comparison` owns the paired 95% interval, but `report.held_off_arms` and the
signal-sensitivity script had copied its formula. They now call `compare.t95_half_width`. These
tests pin that the shared function is the old expression, that the comparison really uses it, and
that no copy comes back.
"""

from __future__ import annotations

import math
import re
import statistics
from pathlib import Path

import pytest

from cairoflow_contracts.experiment import MetricResult
from cairoflow_evaluation.compare import T95, paired_comparison, t95_half_width

REPO = Path(__file__).resolve().parents[1]
COMPARE = "packages/evaluation/cairoflow_evaluation/compare.py"


def test_the_half_width_is_exactly_the_expression_it_replaced() -> None:
    for n in range(2, 80):  # past 31 values the last t quantile is used, as before
        for sd in (0.0, 0.25, 3.7, 118.4):
            replaced = T95[min(n - 1, len(T95)) - 1] * sd / math.sqrt(n)
            assert t95_half_width(n, sd) == replaced


@pytest.mark.parametrize("n", [1, 0, -3])
def test_an_interval_needs_at_least_two_pairs(n: int) -> None:
    with pytest.raises(ValueError, match="at least two pairs"):
        t95_half_width(n, 5.0)


def _metric(seed: int, value: float, arm: str) -> MetricResult:
    return MetricResult(
        experiment_id="e", arm=arm, seed=seed, metric_name="mean_delay_s", value=value, unit="s",
        window_s=(0.0, 100.0), sample_size=10, source_output_file="x", source_sha256="a" * 64,
        calculation_version="0.1.0",
    )  # fmt: skip


def test_the_comparison_interval_is_the_shared_half_width_round_the_mean() -> None:
    base = [10.0, 12.0, 11.0, 13.0, 9.0, 10.5]
    other = [9.0, 11.5, 10.2, 12.0, 8.4, 10.1]
    comparison = paired_comparison(
        "I1",
        [_metric(s, v, "baseline") for s, v in enumerate(base, 1)],
        [_metric(s, v, "I1") for s, v in enumerate(other, 1)],
    )
    diffs = [round(c - b, 6) for b, c in zip(base, other, strict=True)]
    mean, sd = statistics.fmean(diffs), statistics.stdev(diffs)
    half = t95_half_width(len(diffs), sd)
    assert comparison.ci95_difference == (round(mean - half, 6), round(mean + half, 6))


def test_the_interval_formula_lives_in_one_place() -> None:
    """A second copy would drift when the quantile table or the rule changes."""
    sources = [
        *REPO.glob("packages/**/*.py"),
        *REPO.glob("apps/**/*.py"),
        *REPO.glob("scripts/**/*.py"),
    ]
    copies = re.compile(r"T95\s*\[")
    counts = {
        p.relative_to(REPO).as_posix(): len(copies.findall(p.read_text(encoding="utf-8")))
        for p in sources
    }
    assert {name: n for name, n in counts.items() if n} == {COMPARE: 1}
