"""Paired comparison of candidates against the baseline, seed by seed, with spread.

For each candidate and metric, the difference candidate - baseline is taken per seed (same
demand file, same SUMO seed). Reported: both means, the mean difference, its sample standard
deviation, n, and a two-sided 95% t interval. The verdict is `improves` or `worsens` only when
the interval excludes zero in that metric's better or worse direction; otherwise `inconclusive`.
Metrics are never combined into one score (no published objective weights yet).

That verdict is uncorrected, so a worsening is flagged readily. An improvement counts towards the
selection only after Holm's correction (significance.py), over every planned improvement test:
each simulated candidate with each improvement metric. A queue claim on an arm whose queue filled
a lane, or held vehicles off the network at their entry point, in the candidate or the baseline,
is not tested.

A maximum queue that both paired runs reached before their signal programs first differ measures
a state both runs share (the replay's constructed start queue, for one), not the plan: that
seed's difference is zero by construction. When that holds on every seed, or on some seeds
without the rest showing a worsening, the comparison is `uninformative` (rules 0.3.0, ADR-024):
it counts neither for nor against the candidate, and its raw values are kept. A worsening shown
by the other seeds stands: a maximum reached after the programs differ is the plan's.

`compute_evaluation` reads the experiment and baseline manifests, checks every run's tripinfo.xml
against its record, and classifies every candidate (eligibility.py). A broken candidate is not
compared. An incomplete one is compared only on the metrics that do not need its trips to
finish, so its harm stays visible, and it is never selectable. `evaluate_experiment` also writes
`evaluation.json` once.
"""

from __future__ import annotations

import hashlib
import logging
import math
import statistics
import xml.etree.ElementTree as ET
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from cairoflow_contracts.experiment import (
    BaselineManifest,
    CandidateEligibility,
    EvaluationResult,
    ExperimentManifest,
    ImprovementTest,
    MetricResult,
    PairedComparison,
    RunRecord,
)
from cairoflow_contracts.pairing import PairingError
from cairoflow_evaluation.eligibility import (
    COMPLETION_METRICS,
    RULES_VERSION,
    approach_lane_lengths,
    candidate_eligibility,
    lane_queues,
    run_censored_lanes,
    run_entry_blocked,
)
from cairoflow_evaluation.metrics import (
    CALCULATION_VERSION,
    better_direction,
    metric_names,
    run_metrics,
)
from cairoflow_evaluation.pairing import experiment_pairs
from cairoflow_evaluation.significance import improvement_tests, is_improvement_metric
from cairoflow_simulation.geometry_compiler import NET_FILE

LOGGER = logging.getLogger(__name__)
EVALUATION_FILE = "evaluation.json"
EXPERIMENT_FILE = "experiment_manifest.json"
BASELINE_FILE = "baseline_manifest.json"
# Two-sided 95% Student t critical values by degrees of freedom; beyond 30, 30's value is used,
# which is slightly conservative.
T95 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)  # fmt: skip


class EvaluationError(ValueError):
    """The experiment cannot be evaluated as recorded."""


def t95_half_width(n: int, sd: float) -> float:
    """Half-width of the two-sided 95% t interval of a mean of `n` paired differences whose sample
    standard deviation is `sd` (n >= 2). The one place it is computed: the interval of a metric
    (`paired_comparison`), of an entry-blocking excess (`report.held_off_arms`) and of a
    sensitivity analysis all call it, so they cannot drift apart."""
    if n < 2:
        raise ValueError("a t interval needs at least two pairs")
    return T95[min(n - 1, len(T95)) - 1] * sd / math.sqrt(n)


def paired_comparison(
    candidate: str,
    baseline: Sequence[MetricResult],
    candidates: Sequence[MetricResult],
    shared_seeds: Collection[int] = (),
) -> PairedComparison:
    """Compare one metric over paired seeds; both sequences hold that metric for every seed.

    `shared_seeds` (maximum queues only, `shared_state_seeds`) are seeds whose value measured a
    state both runs share; they make the comparison `uninformative` unless the verdict is a
    worsening on a subset of seeds (see the module notes)."""
    by_seed = {m.seed: m for m in baseline}
    other = {m.seed: m for m in candidates}
    seeds = tuple(sorted(by_seed))
    if set(other) != set(seeds) or not seeds:
        raise EvaluationError(f"{candidate}: seeds do not pair with the baseline")
    name = baseline[0].metric_name
    base = tuple(by_seed[s].value for s in seeds)
    cand = tuple(other[s].value for s in seeds)
    diffs = tuple(round(c - b, 6) for b, c in zip(base, cand, strict=True))
    mean = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if len(diffs) > 1 else None
    interval = None
    verdict = "inconclusive"
    if sd is not None:
        half = t95_half_width(len(diffs), sd)
        interval = (round(mean - half, 6), round(mean + half, 6))
        better = better_direction(name)
        if interval[1] < 0:
            verdict = "improves" if better == "lower" else "worsens"
        elif interval[0] > 0:
            verdict = "improves" if better == "higher" else "worsens"
    shared = tuple(sorted(set(shared_seeds) & set(seeds)))
    if shared and (len(shared) == len(seeds) or verdict != "worsens"):
        verdict = "uninformative"
    return PairedComparison(
        candidate=candidate,
        metric_name=name,
        unit=baseline[0].unit,
        better=better_direction(name),
        seeds=seeds,
        baseline_values=base,
        candidate_values=cand,
        differences=diffs,
        baseline_mean=round(statistics.fmean(base), 6),
        candidate_mean=round(statistics.fmean(cand), 6),
        mean_difference=round(mean, 6),
        sd_difference=None if sd is None else round(sd, 6),
        ci95_difference=interval,
        verdict=verdict,
        shared_state_seeds=shared,
    )


def peak_steps(steps: Mapping[float, Mapping[str, float]], arms: Sequence[str]) -> dict[str, float]:
    """Arm -> first step at which its longest approach-lane queue (metrics.py) is at its maximum."""
    peak: dict[str, tuple[float, float]] = {}
    for t in sorted(steps):
        for arm in arms:
            value = max((length for lane, length in steps[t].items()
                         if lane.startswith(f"{arm}_in_")), default=0.0)  # fmt: skip
            if arm not in peak or value > peak[arm][1]:
                peak[arm] = (t, value)
    return {arm: t for arm, (t, _) in peak.items()}


def signal_states(net_path: Path, end: float) -> tuple[str, ...]:
    """Every traffic light's state at each whole second of [0, end) in a compiled network: static
    programs with offset 0, as the geometry compiler writes them, repeated from time 0."""
    logics = sorted(ET.parse(net_path).getroot().iter("tlLogic"), key=lambda e: e.get("id", ""))
    columns: list[list[str]] = []
    for logic in logics:
        if logic.get("type", "static") != "static" or float(logic.get("offset", 0)) != 0:
            raise EvaluationError(f"{net_path}: only static programs with offset 0 can be read")
        phases = [(float(p.get("duration", 0)), p.get("state", "")) for p in logic.iter("phase")]
        cycle = sum(duration for duration, _ in phases)
        column = []
        for t in range(math.ceil(end)):
            x = t % cycle
            for duration, state in phases:
                if x < duration:
                    column.append(state)
                    break
                x -= duration
        columns.append(column)
    return tuple("|".join(states) for states in zip(*columns, strict=True))


def first_difference_s(baseline: Sequence[str], candidate: Sequence[str]) -> float | None:
    """The first second at which two signal-state sequences differ; None if they never do."""
    pairs = enumerate(zip(baseline, candidate, strict=True))
    return next((float(t) for t, (a, b) in pairs if a != b), None)


def shared_state_seeds(
    baseline: Mapping[int, Mapping[float, Mapping[str, float]]],
    candidate: Mapping[int, Mapping[float, Mapping[str, float]]],
    arms: Sequence[str],
    differ_s: float | None,
) -> dict[str, tuple[int, ...]]:
    """Arm -> seeds on which both runs reached that arm's maximum queue (eligibility.lane_queues
    per seed) before `differ_s`, the first second at which their signal programs differ.

    Before that second the two runs are the same simulation (same demand, same seed, same
    signals), so their queue.xml must agree there; a seed where it does not is never counted.
    Programs that never differ share nothing: the plan changed nothing, and a zero difference
    then says so."""
    if differ_s is None:
        return {}
    found: dict[str, list[int]] = {arm: [] for arm in arms}
    for seed in sorted(set(baseline) & set(candidate)):
        a, b = baseline[seed], candidate[seed]
        if any(a.get(t) != b.get(t) for t in set(a) | set(b) if t < differ_s):
            continue
        peaks_a, peaks_b = peak_steps(a, arms), peak_steps(b, arms)
        for arm in arms:
            if peaks_a.get(arm, differ_s) < differ_s and peaks_b.get(arm, differ_s) < differ_s:
                found[arm].append(seed)
    return {arm: tuple(seeds) for arm, seeds in found.items() if seeds}


def compute_evaluation(experiment_dir: Path) -> EvaluationResult:
    """Metrics of the baseline and every valid candidate, and their paired comparisons."""
    experiment = ExperimentManifest.model_validate_json(
        (experiment_dir / EXPERIMENT_FILE).read_text(encoding="utf-8")
    )
    baseline_dir = (experiment_dir / experiment.baseline_dir).resolve()
    raw = (baseline_dir / BASELINE_FILE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != experiment.baseline_manifest_sha256:
        raise EvaluationError("the baseline manifest changed after the experiment was run")
    baseline = BaselineManifest.model_validate_json(raw)
    if not baseline.valid:
        raise EvaluationError("the baseline is not valid")
    try:  # ADR-033: runs that did not share demand, network, SUMO setup or settings cannot pair
        pairing = experiment_pairs(
            experiment, baseline, baseline_dir, experiment.baseline_manifest_sha256
        )
    except PairingError as error:
        raise EvaluationError(f"candidates not paired with the baseline: {error}") from error
    for note in pairing.unverified:  # the evaluation's output cannot carry this, so the log does
        LOGGER.warning("%s: %s", experiment.experiment_id, note)
    arms = [
        p.name.split(".", 1)[0]
        for p in baseline.network.parameters
        if p.name.endswith(".approach_lanes")
    ]
    names = metric_names(arms)
    root = experiment_dir.resolve()

    def metrics_of(arm: str, runs, runs_dir: Path) -> list[MetricResult]:
        found: list[MetricResult] = []
        for run in runs:
            found += run_metrics(
                runs_dir / run.run_dir,
                experiment_id=experiment.experiment_id,
                arm=arm,
                seed=run.seed,
                settings=experiment.settings,
                approach_arms=arms,
                root=root,
            )
        return found

    metrics = metrics_of("baseline", baseline.runs, baseline_dir / "runs")
    _check_tripinfo(metrics, baseline.runs)
    lengths = approach_lane_lengths(baseline_dir / "network" / NET_FILE)
    start = experiment.settings.warmup_s
    end = start + experiment.settings.horizon_s
    base_queues = {run.seed: lane_queues(baseline_dir / "runs" / run.run_dir / "queue.xml",
                                         start, end) for run in baseline.runs}  # fmt: skip
    base_states = signal_states(baseline_dir / "network" / NET_FILE, end)
    excluded: list[str] = []
    comparisons: list[PairedComparison] = []
    eligibility: list[CandidateEligibility] = []
    for record in experiment.candidates:
        runs_dir = root / "candidates" / record.intervention_id / "runs"
        status = candidate_eligibility(record, runs_dir, experiment.settings, lengths)
        eligibility.append(status)
        if status.status != "compared":
            excluded.append(
                f"{record.intervention_id} ({status.status.replace('_', ' ')}): "
                + "; ".join(status.reasons)
            )
        if status.status == "invalid_simulation":
            continue
        # An incomplete candidate is compared only on what does not need its trips to finish.
        skip = COMPLETION_METRICS if status.status == "incomplete" else ()
        found = [
            m
            for m in metrics_of(record.intervention_id, record.runs, runs_dir)
            if m.metric_name not in skip
        ]
        metrics += found
        net = root / "candidates" / record.intervention_id / "network" / NET_FILE
        shared = shared_state_seeds(
            base_queues,
            {run.seed: lane_queues(runs_dir / run.run_dir / "queue.xml", start, end)
             for run in record.runs},
            arms,
            first_difference_s(base_states, signal_states(net, end)),
        )  # fmt: skip
        for name in names:
            if name in skip:
                continue
            kind, _, arm = name.partition(".")
            comparisons.append(
                paired_comparison(
                    record.intervention_id,
                    [m for m in metrics if m.arm == "baseline" and m.metric_name == name],
                    [m for m in found if m.metric_name == name],
                    shared.get(arm, ()) if kind == "max_queue_m" else (),
                )
            )
    result = EvaluationResult(
        experiment_id=experiment.experiment_id,
        calculation_version=CALCULATION_VERSION,
        metrics=tuple(metrics),
        comparisons=tuple(comparisons),
        excluded=tuple(excluded),
        eligibility=tuple(eligibility),
        rules_version=RULES_VERSION,
        baseline_censored_lanes=run_censored_lanes(
            baseline.runs, baseline_dir / "runs", lengths, experiment.settings
        ),
        baseline_entry_blocked=run_entry_blocked(
            baseline.runs, baseline_dir / "runs", lengths, experiment.settings
        ),
    )
    return result.model_copy(update={"improvement_tests": planned_improvement_tests(result)})


def _check_tripinfo(metrics: Sequence[MetricResult], runs: Sequence[RunRecord]) -> None:
    """Every run's tripinfo.xml, as read for its metrics, must hash as its RunRecord says."""
    read = {m.seed: m.source_sha256 for m in metrics if m.metric_name == "throughput_veh_per_h"}
    for run in runs:
        if read.get(run.seed) != run.tripinfo_sha256:
            raise EvaluationError(f"seed {run.seed}: tripinfo.xml changed after the run")


def improvement_plan(evaluation: EvaluationResult) -> tuple[tuple[str, str], ...]:
    """Every (candidate, metric) the experiment could claim as an improvement, fixed by its design:
    each simulated candidate with each improvement metric the baseline or a comparison has.
    Records before rules 0.2.0 list no eligibility; their compared candidates are used."""
    names = sorted(
        name
        for name in {m.metric_name for m in evaluation.metrics if m.arm == "baseline"}
        | {c.metric_name for c in evaluation.comparisons}
        if is_improvement_metric(name)
    )
    candidates = [e.candidate for e in evaluation.eligibility] or sorted(
        {c.candidate for c in evaluation.comparisons}
    )
    return tuple((candidate, name) for candidate in candidates for name in names)


def censored_arms(
    lanes: Mapping[int, tuple[str, ...]], blocked: Mapping[int, Mapping[str, float]]
) -> set[str]:
    """Arms with a lane that filled, or with vehicles held off the network at their entry."""
    return {lane.split("_", 1)[0] for found in lanes.values() for lane in found} | {
        arm for found in blocked.values() for arm in found
    }


def untestable_claims(evaluation: EvaluationResult) -> frozenset[tuple[str, str]]:
    """Queue claims on an arm whose queue was censored in the candidate or in the baseline."""
    shared = censored_arms(evaluation.baseline_censored_lanes, evaluation.baseline_entry_blocked)
    return frozenset(
        (e.candidate, f"{kind}_queue_m.{arm}")
        for e in evaluation.eligibility
        for arm in shared | censored_arms(e.censored_lanes, e.entry_blocked)
        for kind in ("mean", "max")
    )


def planned_improvement_tests(evaluation: EvaluationResult) -> tuple[ImprovementTest, ...]:
    """Holm's procedure over the evaluation's planned improvement tests (significance.py)."""
    return improvement_tests(
        evaluation.comparisons,
        improvement_plan(evaluation),
        untestable=untestable_claims(evaluation),
    )


def evaluate_experiment(experiment_dir: Path) -> EvaluationResult:
    """`compute_evaluation`, written to `evaluation.json` in the experiment directory once."""
    result = compute_evaluation(experiment_dir)
    with (experiment_dir / EVALUATION_FILE).open("x", encoding="utf-8") as handle:
        handle.write(result.model_dump_json(indent=2) + "\n")
    return result
