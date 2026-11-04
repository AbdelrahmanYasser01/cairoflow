"""A weighted summary J of each candidate's paired differences (P1-4). It never selects.

J = w_T * dT/T0 + w_Q * dQ/Q0 + w_D * dD/D0, each term a candidate's paired mean difference from
the baseline divided by the baseline mean, so terms carry no unit and a negative J is better:
- T, travel time: mean tripinfo `duration` of vehicles departing in the measurement window;
- Q, queue burden: the mean over approach arms of `mean_queue_m.<arm>` (metrics.py);
- D, delay: `mean_delay_s` (metrics.py).
C, a conflict or safety proxy, has no value: SUMO runs here without a surrogate safety device,
so its term is left out and said to be missing, never set to zero. J is computed from the same
SUMO outputs as the evaluation, stands beside each metric's own verdict, and does not replace
it: a candidate that worsens any metric is flagged however good its J, and the selection rule
(report.py) is unchanged. Weights are configurable; the sensitivity table shows where the order
of the candidates changes with them.
"""

from __future__ import annotations

import statistics
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

from cairoflow_contracts.experiment import BaselineManifest, EvaluationResult, ExperimentManifest
from cairoflow_evaluation.compare import EXPERIMENT_FILE
from cairoflow_simulation.runner import MANIFEST_FILE

DEFAULT_WEIGHTS = {"T": 1.0, "Q": 1.0, "D": 1.0}
SENSITIVITY = ({"T": 1, "Q": 1, "D": 1}, {"T": 2, "Q": 1, "D": 1}, {"T": 1, "Q": 2, "D": 1},
               {"T": 1, "Q": 1, "D": 2}, {"T": 1, "Q": 0, "D": 0}, {"T": 0, "Q": 1, "D": 0},
               {"T": 0, "Q": 0, "D": 1})  # fmt: skip
C_MISSING = (
    "C (conflict or safety proxy) is not computed: SUMO ran without a surrogate safety device, "
    "so its term is left out of J, not set to zero"
)


def travel_time_s(tripinfo: Path, start: float, end: float) -> float:
    """Mean `duration` of the arrived trips that departed in [start, end)."""
    trips = ET.parse(tripinfo).getroot().findall("tripinfo")
    found = [
        float(t.get("duration", 0))
        for t in trips
        if start <= float(t.get("depart", -1)) < end and float(t.get("arrival", -1)) >= 0
    ]
    return statistics.fmean(found) if found else float("nan")


def objective(
    experiment_dir: Path, evaluation: EvaluationResult, weights: Mapping[str, float] | None = None
) -> dict:
    """J for every compared candidate, with its terms, the metrics it worsens and a sensitivity
    table. Incomplete candidates get no J (their T and D are lower bounds)."""
    weights = dict(weights or DEFAULT_WEIGHTS)
    experiment = ExperimentManifest.model_validate_json(
        (experiment_dir / EXPERIMENT_FILE).read_text(encoding="utf-8")
    )
    baseline_dir = (experiment_dir / experiment.baseline_dir).resolve()
    baseline = BaselineManifest.model_validate_json(
        (baseline_dir / MANIFEST_FILE).read_text(encoding="utf-8")
    )
    start = experiment.settings.warmup_s
    end = start + experiment.settings.horizon_s
    seeds = sorted(r.seed for r in baseline.runs)
    status = {e.candidate: e.status for e in evaluation.eligibility}

    def per_seed(arm: str, runs_dir: Path, runs) -> dict[str, dict[int, float]]:
        by_run = {r.seed: r for r in runs}
        values: dict[str, dict[int, float]] = {"T": {}, "Q": {}, "D": {}}
        for seed in seeds:
            values["T"][seed] = travel_time_s(runs_dir / by_run[seed].run_dir / "tripinfo.xml",
                                              start, end)  # fmt: skip
            mine = [m for m in evaluation.metrics if m.arm == arm and m.seed == seed]
            queues = [m.value for m in mine if m.metric_name.startswith("mean_queue_m.")]
            values["Q"][seed] = statistics.fmean(queues) if queues else float("nan")
            delay = [m.value for m in mine if m.metric_name == "mean_delay_s"]
            values["D"][seed] = delay[0] if delay else float("nan")
        return values

    base = per_seed("baseline", baseline_dir / "runs", baseline.runs)
    candidates: dict[str, dict] = {}
    for record in experiment.candidates:
        iid = record.intervention_id
        worsens = sorted(c.metric_name for c in evaluation.comparisons
                         if c.candidate == iid and c.verdict == "worsens")  # fmt: skip
        if status.get(iid) != "compared":
            candidates[iid] = {"J": None, "worsens": worsens,
                               "note": f"{status.get(iid, 'not simulated')}: no J"}  # fmt: skip
            continue
        own = per_seed(iid, experiment_dir / "candidates" / iid / "runs", record.runs)
        terms = {}
        for name in ("T", "Q", "D"):
            diffs = [own[name][s] - base[name][s] for s in seeds]
            scale = statistics.fmean(base[name].values())
            terms[name] = {
                "baseline_mean": round(scale, 6),
                "mean_difference": round(statistics.fmean(diffs), 6),
                "relative": round(statistics.fmean(diffs) / scale, 6) if scale else None,
            }
        j = sum(weights[n] * (terms[n]["relative"] or 0.0) for n in ("T", "Q", "D"))
        candidates[iid] = {"terms": terms, "J": round(j, 6), "worsens": worsens,
                           "better_J_but_worsens": j < 0 and bool(worsens)}  # fmt: skip

    def order(w: Mapping[str, float]) -> list[str]:
        scored = {iid: sum(w[n] * (c["terms"][n]["relative"] or 0.0) for n in ("T", "Q", "D"))
                  for iid, c in candidates.items() if c.get("J") is not None}  # fmt: skip
        return sorted(scored, key=lambda iid: (scored[iid], iid))

    return {
        "definition": "J = w_T*dT/T0 + w_Q*dQ/Q0 + w_D*dD/D0 (paired mean differences over the "
        "baseline means; negative is better); it does not select",
        "weights": weights,
        "C": C_MISSING,
        "seeds": seeds,
        "candidates": candidates,
        "sensitivity": [{"weights": w, "order_best_first": order(w)} for w in SENSITIVITY],
    }
