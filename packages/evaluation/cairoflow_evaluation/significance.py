"""Which improvements an experiment may claim: paired t tests with Holm's step-down correction.

Each metric's own verdict (compare.paired_comparison) uses an uncorrected paired 95% interval, so
harm is flagged readily: one worsening metric makes a candidate ineligible. An improvement needs
more. With ten metrics and several candidates judged at 95% each, a candidate with no effect at
all would often show some "improvement" by chance. An improvement therefore counts only when its
paired t test survives Holm's (1979) step-down procedure at a family-wise alpha of 5%, and points
in the better direction.

The family is fixed before any result is known: every simulated candidate with every improvement
metric (`plan`). A planned test that cannot be run still counts in the family and is never
rejected, which makes the others stricter. That covers four cases: no comparison (a broken
candidate, or an incomplete candidate's mean delay), fewer than two seeds, a zero standard
deviation, and a queue censored at its lane's end. Selection also requires the candidate to be
selectable (eligibility.py).

Only mean delay and the mean queues are improvement metrics. Window throughput counts the
arrivals of a fixed demand, so a plan mostly moves arrivals across the window's edges: a drop can
show vehicles held back, but a rise does not show more vehicles served. The maximum queue moves
in whole vehicles at one 1 s step. Both may still worsen; neither counts as an improvement.

The t distribution comes from the regularized incomplete beta function (continued fraction,
Numerical Recipes 6.4), so no statistics package is needed. Decisions use the unrounded p-value;
the record keeps it to 6 significant digits.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence

from cairoflow_contracts.experiment import ImprovementTest, PairedComparison

FAMILY_ALPHA = 0.05
IMPROVEMENT_METRICS = ("mean_delay_s", "mean_queue_m")


def is_improvement_metric(metric_name: str) -> bool:
    return metric_name.split(".", 1)[0] in IMPROVEMENT_METRICS


def _beta_fraction(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 500):
        for numerator in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + numerator / c
            c = c if abs(c) > tiny else tiny
            h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            return h
    raise ArithmeticError(f"incomplete beta did not converge (a={a}, b={b}, x={x})")


def regularized_beta(x: float, a: float, b: float) -> float:
    """I_x(a, b) for 0 <= x <= 1 and a, b > 0."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = (
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(log_front) * _beta_fraction(a, b, x) / a
    return 1.0 - math.exp(log_front) * _beta_fraction(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: int) -> float:
    """P(|T| >= |t|) for Student's t with `df` degrees of freedom."""
    if df < 1:
        raise ValueError("a t test needs at least one degree of freedom")
    if math.isinf(t):
        return 0.0
    return regularized_beta(df / (df + t * t), df / 2.0, 0.5)


def improvement_tests(
    comparisons: Sequence[PairedComparison],
    plan: Sequence[tuple[str, str]],
    alpha: float = FAMILY_ALPHA,
    untestable: Collection[tuple[str, str]] = (),
) -> tuple[ImprovementTest, ...]:
    """Holm's procedure over the planned (candidate, metric) improvement tests, in a fixed order.

    `untestable` names planned tests whose data cannot support the claim (a censored queue).
    """
    by_key = {(c.candidate, c.metric_name): c for c in comparisons}
    rows: list[tuple[tuple[str, str], int, float | None, float | None]] = []
    for key in sorted({k for k in plan if is_improvement_metric(k[1])}):
        c = by_key.get(key)
        n = len(c.seeds) if c else 0
        if c is None or key in untestable or n < 2 or not c.sd_difference:
            rows.append((key, n, None, None))
            continue
        sign = -1.0 if c.better == "lower" else 1.0
        t = sign * c.mean_difference / (c.sd_difference / math.sqrt(n))
        rows.append((key, n, t, t_two_sided_p(t, n - 1)))

    tested = sorted((r for r in rows if r[3] is not None), key=lambda r: (r[3], r[0]))
    decided: dict[tuple[str, str], tuple[float, bool]] = {}
    rejecting = True
    for rank, (key, _, t, p) in enumerate(tested, start=1):
        threshold = alpha / (len(rows) - rank + 1)
        rejecting = rejecting and p is not None and p <= threshold
        decided[key] = (threshold, rejecting and t is not None and t > 0)
    return tuple(
        ImprovementTest(
            candidate=key[0],
            metric_name=key[1],
            n=n,
            t_statistic=None if t is None else round(t, 6),
            p_value=None if p is None else float(f"{p:.6g}"),
            holm_threshold=decided[key][0] if key in decided else None,
            confirmed=decided[key][1] if key in decided else False,
        )
        for key, n, t, p in rows
    )
