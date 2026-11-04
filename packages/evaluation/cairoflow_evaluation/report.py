"""Assemble the decision report, check that every number traces to its source (A9), render it.

Numbers enter the report only as `ReportNumber`s built here from the world model, the validated
outcomes, the experiment settings and the evaluation; model output contributes text only.

`trace_report` takes nothing the report or the stored evaluation says about a result on trust
(P0-2). It recomputes the evaluation from the SUMO output files (compare.compute_evaluation
re-parses every tripinfo.xml and queue.xml), resolves every reference against that recomputation,
checks each number's label, unit and kind against the definition of its source, re-derives the
verdicts, confirmed improvements, selection and recommendation, and finally assembles the report
again from the recorded inputs and the recomputed evaluation: any difference is a problem.

Prose written by code (limitations, selection reason) may repeat a recorded value such as the
clip length; those strings are copied from the evidence bundle or fixed templates.

The trace also rebuilds the experiment's pairs (ADR-033): `pair_problems` re-hashes every
seed's demand file and population through `pair_identity.experiment_pair_view` and checks that
every compared seed is one recorded pair, which the SUMO outputs alone cannot show. The Holm family
is fixed by the design, not by the results: `holm_family` reads only the two manifests (each
simulated candidate with each improvement metric of the baseline's approaches), and the trace
refuses improvement tests over any other family. `comparison_output` is what a run script records
about its comparisons: each with the PairIdentity of every paired seed and the unit of inference
they give. Seeds 301-320 are reserved confirmation seeds: the report refuses them unless the
caller passes confirmation=True. `retrace_record` traces a committed record again under these
checks: the report it names must hash as it says and say what it says, its own comparisons (in
either recorded form), Holm tests and eligibility are re-derived, and the report is traced again
from the inputs it was assembled with. It says verified, failed (with every problem) or
unverifiable (with why); nothing is suppressed and no record is edited.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from cairoflow_contracts.evidence import MovementCount, TrafficWorldModel
from cairoflow_contracts.experiment import (
    BaselineManifest,
    EvaluationResult,
    ExperimentManifest,
    PairedComparison,
)
from cairoflow_contracts.pair_identity import ReservedSeedError, check_seeds, unit_of_inference
from cairoflow_contracts.pairing import PairingError
from cairoflow_contracts.reasoning import (
    EvidenceBundle,
    GroundingReport,
    ReasoningResult,
    ValidationOutcome,
)
from cairoflow_contracts.report import (
    BaselineAssessment,
    CandidateFinding,
    DecisionReport,
    ReportNumber,
)
from cairoflow_evaluation.compare import (
    BASELINE_FILE,
    EVALUATION_FILE,
    EXPERIMENT_FILE,
    EvaluationError,
    compute_evaluation,
    planned_improvement_tests,
    t95_half_width,
)
from cairoflow_evaluation.eligibility import RULES_VERSION
from cairoflow_evaluation.metrics import CALCULATION_VERSION, MetricError, metric_names
from cairoflow_evaluation.pair_identity import experiment_pair_view
from cairoflow_evaluation.significance import is_improvement_metric
from cairoflow_reasoning.grounding import ground

OBJECTIVE = (
    "Each metric is judged on its own; no weighted score is used because no weights have been "
    "agreed. A metric worsens when the paired 95% interval of its difference from the baseline "
    "excludes zero on the worse side, with no correction, so harm is flagged readily. An "
    "improvement counts only when its paired t test also survives Holm's step-down correction at "
    "a family-wise 5% over every planned improvement test: each simulated candidate with each "
    "improvement metric, untested ones included. Only mean delay and the mean queues can count as "
    "improvements. Window throughput counts arrivals of a fixed demand, so a rise does not show "
    "more vehicles served, and the maximum queue moves in whole vehicles, so both can only worsen. "
    "A queue that filled its lane, or that held vehicles off the network at their entry point, is "
    "a lower bound and is not tested for improvement. A maximum queue that both runs reached "
    "before they first differed measures their shared state, not the plan: it is uninformative "
    "and counts neither for nor against a candidate (rules 0.3.x)."
)
SELECTION_RULE = (
    "Only a candidate whose every seed is complete (nothing teleported or collided, every vehicle "
    "arrived by the end) can be selected. It is eligible when no metric worsens, at least one "
    "improvement is confirmed, and it did not hold vehicles off the network at any arm's entry "
    "longer than the baseline (a paired 95% interval of the waiting vehicle-seconds above zero), "
    "since harm on that arm could then not be excluded. The eligible "
    "candidate with the most confirmed improvements is selected; ties go to the larger reduction "
    "in mean delay, then to the lower intervention number. Without an eligible candidate, none is "
    "selected: an inconclusive result is never a recommendation."
)
RECOMMENDATION_RULE = (
    "A selected candidate is recommended only when the baseline it was compared with is assessed "
    "as validated against observations. Otherwise the selection is a simulated result under an "
    "unvalidated baseline and is not a recommendation."
)
SIMULATION_TEXT = {
    "incomplete": "incomplete. Vehicles were still in the network after the drain, so its demand "
    "did not clear within the drain, and it can never be selected. Throughput and queues are "
    "compared; mean delay is not, because the delays of unfinished trips are only lower bounds. "
    "Queues that filled their lane are lower bounds too.",
    "invalid_simulation": "invalid. A run teleported or collided, so it is not a valid simulation "
    "of the plan: a teleport after a long standstill is often a jam the plan caused, so the "
    "outputs may hide harm. It is not compared and can never be selected; its runs need "
    "inspecting.",
}
FIELD_LABELS = {
    "baseline_mean": "baseline mean",
    "candidate_mean": "candidate mean",
    "mean_difference": "mean difference (candidate - baseline)",
    "sd_difference": "standard deviation of the difference",
    "ci95_low": "95% interval of the difference, low",
    "ci95_high": "95% interval of the difference, high",
}
MOVEMENT_FIELDS = {
    "count_veh": ("vehicles counted", "veh"),
    "flow_veh_per_h": ("flow", "veh/h"),
    "ci95_low": ("flow 95% interval, low", "veh/h"),
    "ci95_high": ("flow 95% interval, high", "veh/h"),
}
SETTINGS_FIELDS = {  # RunSettings field -> (label, unit), in report order
    "seeds": ("simulation seeds per arm", "count"),
    "warmup_s": ("warm-up", "s"),
    "horizon_s": ("measurement window", "s"),
}
# reproducibility entries assemble_report writes itself; the rest are the caller's artifact hashes
REPRODUCIBILITY_KEYS = (
    "world_model_sha256",
    "bundle_id",
    "reasoning_request_key",
    "experiment_id",
    "baseline_experiment_id",
    "baseline_manifest_sha256",
    "calculation_version",
)
RETRACE_STATUSES = ("verified", "failed", "unverifiable")
PAIR_FIELDS = ("pair_sha256", "unit_of_inference")  # what comparison_output adds to a comparison


def _comparison_field(comparison: PairedComparison, field: str) -> float | None:
    if field == "ci95_low":
        return None if comparison.ci95_difference is None else comparison.ci95_difference[0]
    if field == "ci95_high":
        return None if comparison.ci95_difference is None else comparison.ci95_difference[1]
    return getattr(comparison, field)


def _movement(world_model: TrafficWorldModel, key: str) -> MovementCount:
    movement = next((m for m in world_model.movements if f"{m.approach}{m.exit_arm}" == key), None)
    if movement is None:
        raise LookupError(f"no movement {key}")
    return movement


def _movement_field(world_model: TrafficWorldModel, key: str, field: str) -> float:
    movement = _movement(world_model, key)
    if field == "ci95_low":
        return movement.flow_ci95_veh_per_h[0]
    if field == "ci95_high":
        return movement.flow_ci95_veh_per_h[1]
    return float(getattr(movement, field))


def _settings_value(experiment: ExperimentManifest, name: str) -> float:
    value = getattr(experiment.settings, name)
    return float(len(value)) if isinstance(value, tuple) else float(value)


def confirmed_improvements(evaluation: EvaluationResult) -> dict[str, tuple[str, ...]]:
    """Candidate -> metrics whose improvement survives Holm's correction (recomputed)."""
    found: dict[str, list[str]] = defaultdict(list)
    for test in planned_improvement_tests(evaluation):
        if test.confirmed:
            found[test.candidate].append(test.metric_name)
    return {candidate: tuple(names) for candidate, names in found.items()}


@dataclass(frozen=True)
class CandidateStanding:
    """One candidate under SELECTION_RULE, before ranking: what makes it eligible or not."""

    candidate: str
    status: str  # eligibility status: compared, incomplete, invalid_simulation, not_compared
    worsens: tuple[str, ...]
    confirmed: tuple[str, ...]  # Holm-confirmed improvements
    held_off: tuple[str, ...]  # arms where it held more vehicles off the network than the baseline
    delay_difference: float  # mean delay, candidate - baseline (s); 0 when not compared
    note: str  # why it is not eligible; empty when it is

    @property
    def eligible(self) -> bool:
        return not self.note


def id_order(candidate: str) -> tuple[str, int, str]:
    """Order intervention ids by their number, so that I2 comes before I10."""
    prefix = candidate.rstrip("0123456789")
    digits = candidate[len(prefix) :]
    return prefix, int(digits) if digits else -1, candidate


def held_off_arms(evaluation: EvaluationResult, candidate: str) -> tuple[str, ...]:
    """Arms where the candidate held vehicles off the network longer than the baseline (rules
    0.3.1): the per-seed difference in waiting vehicle-seconds (candidate - baseline, 0 where a
    run waited nothing) has a paired 95% interval wholly above zero, the same uncorrected test that
    flags any other harm (compare.paired_comparison). Equal positive differences on every seed are
    an excess without spread, and count; a single seed counts when its difference is positive."""
    status = next((e for e in evaluation.eligibility if e.candidate == candidate), None)
    if status is None:
        return ()
    own, base = status.entry_blocked, evaluation.baseline_entry_blocked
    seeds = sorted(status.run_classes)
    arms = sorted({a for found in (own, base) for arms in found.values() for a in arms})
    held = []
    for arm in arms:
        diffs = [own.get(s, {}).get(arm, 0.0) - base.get(s, {}).get(arm, 0.0) for s in seeds]
        mean = statistics.fmean(diffs) if diffs else 0.0
        if len(diffs) < 2:
            worse = mean > 0
        else:
            sd = statistics.stdev(diffs)
            half = t95_half_width(len(diffs), sd)
            worse = mean - half > 0 if sd else mean > 0
        if worse:
            held.append(arm)
    return tuple(held)


def candidate_standings(evaluation: EvaluationResult) -> dict[str, CandidateStanding]:
    """Every candidate's standing under SELECTION_RULE (eligibility, uncorrected harm verdicts,
    Holm-confirmed improvements, entry blocking); select_candidate ranks the eligible ones."""
    grouped: dict[str, list[PairedComparison]] = defaultdict(list)
    for comparison in evaluation.comparisons:
        grouped[comparison.candidate].append(comparison)
    confirmed = confirmed_improvements(evaluation)
    status = {e.candidate: e.status for e in evaluation.eligibility}
    notes = {e.candidate: f"{e.candidate} is {e.status.replace('_', ' ')}"
             for e in evaluation.eligibility if e.status != "compared"}  # fmt: skip
    if not evaluation.eligibility:  # records before rules 0.2.0 kept reasons as text only
        notes |= {entry.split(":", 1)[0]: f"{entry.split(':', 1)[0]} was not compared"
                  for entry in evaluation.excluded}  # fmt: skip
    standings: dict[str, CandidateStanding] = {}
    for candidate in sorted(set(grouped) | set(notes), key=id_order):
        items = grouped.get(candidate, [])
        worse = tuple(c.metric_name for c in items if c.verdict == "worsens")
        held = held_off_arms(evaluation, candidate)
        note = notes.get(candidate, "")
        if note:
            if worse:
                note += f" and worsens {', '.join(worse)}"
        elif worse:
            note = f"{candidate} worsens {', '.join(worse)}"
        elif held:
            note = (
                f"{candidate} held more vehicles off the network at the entry of arm "
                f"{', '.join(held)} than the baseline, so harm there cannot be excluded"
            )
        elif candidate not in confirmed:
            note = f"{candidate} has no improvement that survives Holm's correction"
        delay = next((c.mean_difference for c in items if c.metric_name == "mean_delay_s"), 0.0)
        standings[candidate] = CandidateStanding(
            candidate=candidate,
            status=status.get(candidate, "compared" if items else "not_compared"),
            worsens=worse,
            confirmed=confirmed.get(candidate, ()),
            held_off=held,
            delay_difference=delay,
            note=note,
        )
    return standings


def select_candidate(evaluation: EvaluationResult) -> tuple[str | None, str]:
    """Apply SELECTION_RULE: eligibility, uncorrected harm verdicts, Holm-confirmed improvements."""
    standings = candidate_standings(evaluation)
    eligible = [s for s in standings.values() if s.eligible]
    if not eligible:
        found = "; ".join(s.note for s in standings.values())
        return None, f"None selected: {found}." if found else "None selected: nothing compared."
    chosen = min(eligible, key=lambda s: (-len(s.confirmed), s.delay_difference,
                                          id_order(s.candidate))).candidate  # fmt: skip
    return chosen, (
        f"{chosen} worsens no metric and its improvement in "
        f"{', '.join(standings[chosen].confirmed)} survives Holm's correction; it ranks first."
    )


def _per_arm(blocked: dict[int, dict[str, float]]) -> dict[str, list[float]]:
    """Arm -> its blocked vehicle-seconds, one value per seed that has any."""
    found: dict[str, list[float]] = defaultdict(list)
    for arms in blocked.values():
        for arm, seconds in arms.items():
            found[arm].append(seconds)
    return dict(sorted(found.items()))


def load_baseline_assessment(
    path: Path, baseline_manifest_sha256: str, root: Path
) -> BaselineAssessment | None:
    """The assessment at `path` (None if there is none), checked against the baseline it must be
    about and against every evidence file it names (paths relative to `root`)."""
    if not path.is_file():
        return None
    assessment = BaselineAssessment.model_validate_json(path.read_text(encoding="utf-8"))
    if assessment.baseline_manifest_sha256 != baseline_manifest_sha256:
        raise ValueError(f"{path} assesses another baseline")
    for name, digest in assessment.evidence.items():
        evidence = root / name
        if not evidence.is_file() or hashlib.sha256(evidence.read_bytes()).hexdigest() != digest:
            raise ValueError(f"{path}: evidence {name} is missing or changed")
    return assessment


def recommend(
    selected: str | None, assessment: BaselineAssessment | None
) -> tuple[str | None, str]:
    """Apply RECOMMENDATION_RULE to a selection."""
    status = assessment.status if assessment else "not_assessed"
    if selected is None:
        return None, "Nothing is recommended: no candidate was selected."
    if status == "validated":
        return selected, f"{selected} is recommended: selected under a validated baseline."
    return None, (
        f"Nothing is recommended: {selected} was selected in simulation, but the baseline is "
        f"{status.replace('_', ' ')}, so the result is not a recommendation."
    )


def holm_family(experiment_dir: Path) -> tuple[tuple[str, str], ...]:
    """Every (candidate, metric) improvement test the design fixes, read from the experiment and
    baseline manifests alone, before any candidate result: each candidate the experiment
    simulates with each improvement metric of the baseline network's approaches."""
    experiment = ExperimentManifest.model_validate_json(
        (experiment_dir / EXPERIMENT_FILE).read_text(encoding="utf-8")
    )
    raw = ((experiment_dir / experiment.baseline_dir) / BASELINE_FILE).read_bytes()
    if hashlib.sha256(raw).hexdigest() != experiment.baseline_manifest_sha256:
        raise EvaluationError("the baseline manifest is not the one the experiment cites")
    baseline = BaselineManifest.model_validate_json(raw)
    arms = [p.name.split(".", 1)[0] for p in baseline.network.parameters
            if p.name.endswith(".approach_lanes")]  # fmt: skip
    names = sorted(name for name in metric_names(arms) if is_improvement_metric(name))
    return tuple((c.intervention_id, name) for c in experiment.candidates for name in names)


def pair_problems(
    experiment_dir: Path, evaluation: EvaluationResult, *, confirmation: bool = False
) -> list[str]:
    """Why the evaluation's comparisons do not rest on the experiment's recorded pairs: the pair
    identity view cannot be rebuilt (a demand file or population that no longer hashes as
    recorded, unread populations, reserved seeds), or a compared seed forms no pair."""
    try:
        view = experiment_pair_view(experiment_dir, confirmation=confirmation)
    except (PairingError, OSError, ValueError) as error:
        return [f"pair identity: {error}"]
    paired = {(p.compared.arm, p.compared.seed) for p in view.pairs}
    found = []
    for c in evaluation.comparisons:
        loose = [seed for seed in c.seeds if (c.candidate, seed) not in paired]
        if loose:
            found.append(f"pair identity: {c.candidate} {c.metric_name} compares seeds {loose} "
                         "that form no recorded pair")  # fmt: skip
    return found


def comparison_output(
    experiment_dir: Path,
    evaluation: EvaluationResult,
    *,
    confirmation: bool = False,
    family: Sequence[tuple[str, str]] | None = None,
) -> dict:
    """The comparisons as a run script records them: each comparison unchanged, plus the
    PairIdentity hash of every paired seed and the unit of inference those pairs give; the
    experiment's pair identity view (its hash names it, `experiment_pair_view` rebuilds it); and
    the Holm family the design fixed. A PairingError (ReservedSeedError for seeds 301-320 without
    confirmation) when the pairs cannot be rebuilt: no comparison is recorded without them.

    `family` is the Holm family a run script read (`holm_family`) before it evaluated any
    candidate result: it must still be the manifests' family, and the evaluation's improvement
    tests must cover exactly it (EvaluationError otherwise)."""
    fixed = holm_family(experiment_dir)
    if family is not None and sorted(map(tuple, family)) != sorted(fixed):
        raise EvaluationError("the Holm family frozen before the results is not the manifests'")
    tested = sorted((t.candidate, t.metric_name) for t in evaluation.improvement_tests)
    if family is not None and tested != sorted(fixed):
        raise EvaluationError(
            f"the improvement tests are not the Holm family frozen before the results "
            f"({len(tested)} tests, {len(fixed)} in the family)"
        )
    view = experiment_pair_view(experiment_dir, confirmation=confirmation)
    by_key = {(p.compared.arm, p.compared.seed): p.pair_sha256 for p in view.pairs}
    population = {s.seed: s.population_sha256 for s in view.seeds}
    comparisons = []
    for c in evaluation.comparisons:
        loose = [seed for seed in c.seeds if (c.candidate, seed) not in by_key]
        if loose:
            raise PairingError(f"{c.candidate} {c.metric_name}: seeds {loose} form no pair")
        comparisons.append(
            c.model_dump(mode="json")
            | {
                "pair_sha256": [by_key[(c.candidate, seed)] for seed in c.seeds],
                "unit_of_inference": unit_of_inference([population[seed] for seed in c.seeds]),
            }
        )
    return {
        "pair_identity": {
            "view_version": view.view_version,
            "view_sha256": view.view_sha256,
            "member_sha256": view.member_sha256,
            "timeline_id": view.timeline_id,
            "sumo_binary_version": view.sumo_binary_version,
            "sumo_wheel_version": view.sumo_wheel_version,
            "confirmation": view.confirmation,
            "unit_of_inference": view.unit_of_inference,
            "pairs": [
                {
                    "pair_sha256": p.pair_sha256,
                    "arm": p.compared.arm,
                    "seed": p.compared.seed,
                    "demand_sha256": p.compared.demand_sha256,
                    "population_sha256": p.compared.population_sha256,
                }
                for p in view.pairs
            ],
        },
        "holm_family": [list(key) for key in fixed],
        "comparisons": comparisons,
    }


def comparison_record(
    experiment_dir: Path,
    evaluation: EvaluationResult,
    *,
    family: Sequence[tuple[str, str]],
    confirmation: bool = False,
) -> dict:
    """What a run script records about its comparisons: `comparison_output`, or, when the pairs
    cannot be rebuilt or the family frozen before the results does not hold, the reason and no
    comparison (an unpaired comparison is never recorded). The run's trace names it too."""
    try:
        return comparison_output(experiment_dir, evaluation, confirmation=confirmation,
                                 family=family)  # fmt: skip
    except (PairingError, EvaluationError, OSError, ValueError) as error:
        return {
            "pair_identity": None,
            "holm_family": [list(key) for key in family],
            "comparisons": [],
            "comparison_error": str(error),
        }


def assemble_report(
    *,
    world_model: TrafficWorldModel,
    bundle: EvidenceBundle,
    reasoning: ReasoningResult | None,
    outcomes: Sequence[ValidationOutcome],
    experiment: ExperimentManifest,
    evaluation: EvaluationResult,
    artifacts: dict[str, str] | None = None,
    now: datetime | None = None,
    baseline_assessment: BaselineAssessment | None = None,
    extra_limitations: Sequence[str] = (),
    grounding: GroundingReport | None = None,
    data_sources: Sequence[str] = (),
    confirmation: bool = False,
) -> DecisionReport:
    """The decision report. With `grounding` (the reply's evidence checks, P0-3), each candidate
    also carries its proposal's preconditions as unverified assumptions and the reasons the
    grounding gate kept it from planning; `data_sources` names where the evidence came from.
    An experiment on reserved seeds 301-320 is refused (ReservedSeedError) unless
    `confirmation` is set."""
    check_seeds(experiment.settings.seeds, confirmation=confirmation)
    if (
        baseline_assessment is not None
        and baseline_assessment.baseline_manifest_sha256 != experiment.baseline_manifest_sha256
    ):
        raise ValueError("the baseline assessment is about another baseline")
    gates = {g.intervention_id: g for g in grounding.proposals} if grounding else {}
    numbers: list[ReportNumber] = []

    def add(label: str, value: float, unit: str, kind: str, source: str, refs: tuple) -> str:
        number_id = f"n{len(numbers) + 1}"
        numbers.append(
            ReportNumber(
                number_id=number_id,
                label=label,
                value=value,
                unit=unit,
                kind=kind,
                source=source,
                refs=refs,
            )
        )
        return number_id

    for name, (label, unit) in SETTINGS_FIELDS.items():
        add(label, _settings_value(experiment, name), unit, "assumed", "settings",
            (f"settings:{name}",))  # fmt: skip
    for m in world_model.movements:
        if m.count_veh == 0:
            continue
        key = f"{m.approach}{m.exit_arm}"
        tracks = tuple(f"track:{t.track_id}" for t in m.tracks)
        for field, (label, unit) in MOVEMENT_FIELDS.items():
            add(
                f"{m.approach} to {m.exit_arm} ({m.turn}): {label}",
                _movement_field(world_model, key, field),
                unit,
                world_model.provenance.kind,  # approximated when built from tracks
                "world_model",
                (f"world_model:movement:{key}:{field}", *tracks),
            )

    by_candidate: dict[str, list[PairedComparison]] = defaultdict(list)
    for comparison in evaluation.comparisons:
        by_candidate[comparison.candidate].append(comparison)
    eligibility = {e.candidate: e for e in evaluation.eligibility}
    confirmed = confirmed_improvements(evaluation)
    proposals = {p.intervention_id: p for p in reasoning.output.interventions} if reasoning else {}
    findings: list[CandidateFinding] = []
    for outcome in outcomes:
        iid = outcome.intervention_id
        ids: list[str] = []
        if outcome.status == "accepted" and outcome.phase_durations_s is not None:
            for index, (duration, phase) in enumerate(
                zip(outcome.phase_durations_s, bundle.phases, strict=True)
            ):
                if duration != phase.duration_s:
                    ids.append(
                        add(
                            f"{iid}: green of {phase.phase_name}",
                            duration,
                            "s",
                            "assumed",
                            "validated_input",
                            (f"outcome:{iid}:phase:{index}",),
                        )  # fmt: skip
                    )
            ids.append(
                add(
                    f"{iid}: cycle",
                    float(outcome.cycle_s or 0),
                    "s",
                    "assumed",
                    "validated_input",
                    (f"outcome:{iid}:cycle_s",),
                )  # fmt: skip
            )
        for c in by_candidate.get(iid, []):
            evidence = tuple(
                f"metric:{arm}:{seed}:{c.metric_name}"
                for seed in c.seeds
                for arm in ("baseline", iid)
            )
            for field, label in FIELD_LABELS.items():
                value = _comparison_field(c, field)
                if value is not None:
                    ids.append(
                        add(f"{iid}: {c.metric_name}, {label}", value, c.unit, "simulated",
                            "comparison", (f"comparison:{iid}:{c.metric_name}:{field}", *evidence))
                    )  # fmt: skip
        proposal = proposals.get(iid)
        simulated = eligibility.get(iid)
        findings.append(
            CandidateFinding(
                intervention_id=iid,
                status=outcome.status,
                validator_reasons=outcome.reasons,
                rationale=proposal.rationale if proposal else None,
                expected_tradeoffs=proposal.expected_tradeoffs if proposal else (),
                addresses_hypothesis_ids=proposal.addresses_hypothesis_ids if proposal else (),
                verdicts={c.metric_name: c.verdict for c in by_candidate.get(iid, [])},
                number_ids=tuple(ids),
                simulation_status=(
                    "not_simulated"
                    if outcome.status != "accepted"
                    else simulated.status
                    if simulated
                    else None
                ),
                simulation_reasons=simulated.reasons if simulated else (),
                confirmed_improvements=confirmed.get(iid, ()),
                unverified_assumptions=(
                    proposal.preconditions if proposal is not None and grounding else ()
                ),
                grounding_reasons=gates[iid].reasons if iid in gates else (),
            )
        )

    selected, reason = select_candidate(evaluation)
    recommended, recommendation = recommend(selected, baseline_assessment)
    synthetic = sorted(
        item.evidence_id.removeprefix("net.")
        for item in bundle.items
        if item.evidence_id.startswith("net.") and item.kind == "assumed"
    )
    status = baseline_assessment.status if baseline_assessment else "not_assessed"
    limitations = (
        "All outcome numbers are simulated by SUMO under the recorded settings; none is observed.",
        f"Baseline assessment: {status.replace('_', ' ')}"
        + (f". {baseline_assessment.summary}" if baseline_assessment else "."),
        *extra_limitations,
        *bundle.limitations,
        *(f"Assumed network value: {name}." for name in synthetic),
        *world_model.signal_notes,
        *(f"Not simulated: {entry}" for entry in experiment.skipped),
        *(f"Never selectable: {entry}" for entry in evaluation.excluded),
        *(
            f"Queue filled its lane ({arm_name}, seed {seed}: {', '.join(lanes)}): that arm's "
            "queue values are lower bounds and are not tested for improvement."
            for arm_name, censored in (
                ("baseline", evaluation.baseline_censored_lanes),
                *((e.candidate, e.censored_lanes) for e in evaluation.eligibility),
            )
            for seed, lanes in sorted(censored.items())
        ),
        *(
            f"Vehicles held off the network at their entry point ({arm_name}, arm {arm}: "
            f"{len(seeds)} of {len(experiment.settings.seeds)} seeds, {sum(seeds):.1f} "
            "vehicle-seconds in the window): that arm's queue values are lower bounds and are not "
            "tested for improvement."
            for arm_name, blocked in (
                ("baseline", evaluation.baseline_entry_blocked),
                *((e.candidate, e.entry_blocked) for e in evaluation.eligibility),
            )
            for arm, seeds in _per_arm(blocked).items()
        ),
        *(
            f"Uninformative ({c.candidate}, {c.metric_name}): on {len(c.shared_state_seeds)} of "
            f"{len(c.seeds)} seeds both runs reached this maximum before their signal programs "
            "first differed, so it measures their shared state, not the plan; it counts neither "
            "way."
            for c in evaluation.comparisons
            if c.verdict == "uninformative"
        ),
    )
    reproducibility = {
        "world_model_sha256": hashlib.sha256(world_model.model_dump_json().encode()).hexdigest(),
        "bundle_id": bundle.bundle_id,
        "reasoning_request_key": reasoning.request_key if reasoning else "none",
        "experiment_id": experiment.experiment_id,
        "baseline_experiment_id": experiment.baseline_experiment_id,
        "baseline_manifest_sha256": experiment.baseline_manifest_sha256,
        "calculation_version": evaluation.calculation_version,
        **(artifacts or {}),
    }
    draft = DecisionReport(
        report_id="pending",
        experiment_id=experiment.experiment_id,
        created_at_utc=now or datetime.now(UTC),
        objective=OBJECTIVE,
        selection_rule=SELECTION_RULE,
        selected_intervention_id=selected,
        selection_reason=reason,
        hypotheses=reasoning.output.hypotheses if reasoning else (),
        candidates=tuple(findings),
        numbers=tuple(numbers),
        limitations=limitations,
        reproducibility=reproducibility,
        baseline_assessment=baseline_assessment,
        recommended_intervention_id=recommended,
        recommendation=recommendation,
        grounding=grounding,
        data_sources=tuple(data_sources),
    )
    report_id = hashlib.sha256(draft.model_dump_json(exclude={"report_id"}).encode()).hexdigest()
    return draft.model_copy(update={"report_id": report_id[:16]})


def _definition(
    ref: str,
    *,
    world_model: TrafficWorldModel,
    comparisons: dict[tuple[str, str], PairedComparison],
    bundle: EvidenceBundle,
) -> tuple[str, str, str, str]:
    """(label, unit, kind, source) that assemble_report gives a number whose first reference is
    `ref`: a number may say only what its source defines."""
    kind, _, rest = ref.partition(":")
    parts = rest.split(":")
    if kind == "settings" and len(parts) == 1 and parts[0] in SETTINGS_FIELDS:
        label, unit = SETTINGS_FIELDS[parts[0]]
        return label, unit, "assumed", "settings"
    if kind == "world_model" and len(parts) == 3 and parts[2] in MOVEMENT_FIELDS:
        m = _movement(world_model, parts[1])
        label, unit = MOVEMENT_FIELDS[parts[2]]
        return (f"{m.approach} to {m.exit_arm} ({m.turn}): {label}", unit,
                world_model.provenance.kind, "world_model")  # fmt: skip
    if kind == "outcome" and parts[1:] == ["cycle_s"]:
        return f"{parts[0]}: cycle", "s", "assumed", "validated_input"
    if kind == "outcome" and len(parts) == 3 and parts[1] == "phase":
        phase = bundle.phases[int(parts[2])]
        return f"{parts[0]}: green of {phase.phase_name}", "s", "assumed", "validated_input"
    if kind == "comparison" and len(parts) == 3 and parts[2] in FIELD_LABELS:
        c = comparisons[(parts[0], parts[1])]
        return (
            f"{parts[0]}: {parts[1]}, {FIELD_LABELS[parts[2]]}",
            c.unit,
            "simulated",
            "comparison",
        )
    raise LookupError(f"no report number is defined by {ref!r}")


def _evaluation_differences(stored: EvaluationResult, fresh: EvaluationResult) -> list[str]:
    """Where the stored evaluation differs from one recomputed from the SUMO outputs."""
    if stored == fresh:
        return []
    found: list[str] = []
    for name in ("experiment_id", "calculation_version", "rules_version"):
        if getattr(stored, name) != getattr(fresh, name):
            found.append(f"the stored evaluation's {name} is {getattr(stored, name)!r}, the "
                         f"recomputation's {getattr(fresh, name)!r}")  # fmt: skip
    for label, key, ours, theirs in (
        ("metric", lambda m: (m.arm, m.seed, m.metric_name), stored.metrics, fresh.metrics),
        ("comparison", lambda c: (c.candidate, c.metric_name), stored.comparisons,
         fresh.comparisons),
        ("eligibility", lambda e: (e.candidate,), stored.eligibility, fresh.eligibility),
        ("improvement test", lambda t: (t.candidate, t.metric_name), stored.improvement_tests,
         fresh.improvement_tests),
    ):  # fmt: skip
        a, b = {key(x): x for x in ours}, {key(x): x for x in theirs}
        for k in sorted(set(a) | set(b), key=str):
            where = "/".join(str(part) for part in k)
            if k not in b:
                found.append(f"{label} {where} is stored but not in the recomputation")
            elif k not in a:
                found.append(f"{label} {where} is recomputed but not stored")
            elif a[k] != b[k]:
                detail = (f": stored {a[k].value}, recomputed {b[k].value} from "
                          f"{b[k].source_output_file}" if label == "metric" else "")  # fmt: skip
                found.append(f"{label} {where} changed{detail}")
    for name in ("excluded", "baseline_censored_lanes", "baseline_entry_blocked"):
        if getattr(stored, name) != getattr(fresh, name):
            found.append(f"the stored evaluation's {name} differs from the recomputation")
    if len(found) > 25:
        found = [*found[:25], f"... and {len(found) - 25} more differences"]
    return [f"evaluation: {p}" for p in found] or [
        "evaluation: the stored evaluation differs from the recomputation"
    ]


def trace_report(
    report: DecisionReport,
    *,
    world_model: TrafficWorldModel,
    outcomes: Sequence[ValidationOutcome],
    experiment: ExperimentManifest,
    evaluation: EvaluationResult,
    experiment_dir: Path,
    bundle: EvidenceBundle,
    reasoning: ReasoningResult | None = None,
    extra_limitations: Sequence[str] = (),
    grounding: GroundingReport | None = None,
    data_sources: Sequence[str] = (),
    tolerance: float = 1e-9,
    evidence_root: Path | None = None,
    confirmation: bool = False,
) -> tuple[str, ...]:
    """Every problem found walking each number back to its source (empty means A9 holds).

    The evaluation is recomputed from the SUMO outputs in `experiment_dir` (and the baseline
    directory its manifest names); a missing or unreadable output is a problem, never a pass.
    The pairs are rebuilt from the recorded demand files (`pair_problems`), and the improvement
    tests must cover exactly the Holm family the manifests fix (`holm_family`).
    `bundle`, `reasoning` and `extra_limitations` are the inputs the report was assembled with:
    the report is assembled again from them and the recomputed evaluation, and must be identical.
    With `evidence_root`, the baseline assessment's evidence files are re-hashed from there.
    Reserved seeds 301-320 are a problem unless `confirmation` is set.
    """
    problems: list[str] = []
    try:
        on_disk = ExperimentManifest.model_validate_json(
            (experiment_dir / EXPERIMENT_FILE).read_text(encoding="utf-8")
        )
        fresh = compute_evaluation(experiment_dir)
    except (EvaluationError, MetricError, OSError, ValidationError, ET.ParseError) as error:
        return (f"the evaluation cannot be recomputed from the SUMO outputs: {error}",)
    if on_disk != experiment:
        problems.append("the experiment manifest given is not the one in the experiment directory")
    for manifest in (experiment, on_disk):
        try:
            check_seeds(manifest.settings.seeds, confirmation=confirmation)
        except ReservedSeedError as error:
            problems.append(f"the experiment: {error}")
            break
    problems += _evaluation_differences(evaluation, fresh)
    problems += pair_problems(experiment_dir, fresh, confirmation=confirmation)

    metrics = {(m.arm, str(m.seed), m.metric_name): m for m in fresh.metrics}
    comparisons = {(c.candidate, c.metric_name): c for c in fresh.comparisons}
    by_outcome = {o.intervention_id: o for o in outcomes}
    if report.experiment_id != experiment.experiment_id:
        problems.append("the report belongs to another experiment")
    assessment = report.baseline_assessment
    if assessment is not None:
        if assessment.baseline_manifest_sha256 != experiment.baseline_manifest_sha256:
            problems.append("the baseline assessment is about another baseline")
        for name, digest in assessment.evidence.items() if evidence_root else ():
            path = evidence_root / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                problems.append(f"baseline assessment evidence {name} is missing or changed")
    if evaluation.rules_version is None:  # a record from before the rules (0.2.0)
        if report.recommended_intervention_id is not None:
            problems.append("a recommendation from an evaluation without recommendation rules")
    else:
        if evaluation.improvement_tests != planned_improvement_tests(evaluation):
            problems.append("the improvement tests do not follow from the comparisons")
        try:
            family = sorted(holm_family(experiment_dir))
        except (EvaluationError, OSError, ValidationError) as error:
            problems.append(f"the Holm family cannot be read from the manifests: {error}")
        else:
            for label, tests in (("stored", evaluation.improvement_tests),
                                 ("recomputed", fresh.improvement_tests)):  # fmt: skip
                if sorted((t.candidate, t.metric_name) for t in tests) != family:
                    problems.append(f"the {label} improvement tests are not the Holm family the "
                                    f"manifests fix ({len(family)} tests)")  # fmt: skip
        if (report.selected_intervention_id, report.selection_reason) != select_candidate(fresh):
            problems.append("the selection does not follow from the evaluation")
        if (report.recommended_intervention_id, report.recommendation) != recommend(
            report.selected_intervention_id, report.baseline_assessment
        ):
            problems.append("the recommendation does not follow from the baseline assessment")
    if report.objective != OBJECTIVE:
        problems.append("the objective is not the one the rules state")
    if report.selection_rule != SELECTION_RULE:
        problems.append("the selection rule is not the one the rules state")

    def resolve(ref: str) -> float | None:
        kind, _, rest = ref.partition(":")
        parts = rest.split(":")
        if kind == "metric":  # recomputed from its SUMO output file
            return metrics[tuple(parts)].value
        if kind == "comparison":  # recomputed from the recomputed metrics
            candidate, name, field = parts
            return _comparison_field(comparisons[(candidate, name)], field)
        if kind == "world_model":
            _, key, field = parts
            return _movement_field(world_model, key, field)
        if kind == "track":
            return None  # checked as a set below
        if kind == "outcome":
            outcome = by_outcome[parts[0]]
            if outcome.phase_durations_s is None:
                raise LookupError(f"{parts[0]} was not accepted")
            if parts[1] == "cycle_s":
                return outcome.cycle_s
            return outcome.phase_durations_s[int(parts[2])]
        if kind == "settings":
            return _settings_value(experiment, parts[0])
        raise LookupError(f"unknown reference kind {kind!r}")

    for number in report.numbers:
        nid, refs = number.number_id, number.refs
        try:
            defined = _definition(refs[0], world_model=world_model, comparisons=comparisons,
                                  bundle=bundle)  # fmt: skip
        except (LookupError, KeyError, IndexError, ValueError) as error:
            problems.append(f"{nid}: {error}")
            continue
        said = (number.label, number.unit, number.kind, number.source)
        if said != defined:
            problems.append(f"{nid}: says {said}, but {refs[0]} defines {defined}")
        for position, ref in enumerate(refs):
            try:
                value = resolve(ref)
            except (LookupError, KeyError, IndexError, ValueError, TypeError) as error:
                problems.append(f"{nid}: {ref}: {error}")
                continue
            if position == 0 and (
                value is None or abs(value - number.value) > tolerance * max(1.0, abs(value))
            ):
                problems.append(f"{nid}: {ref} gives {value}, the report says {number.value}")
        kind, _, rest = refs[0].partition(":")
        if kind == "comparison":
            candidate, name, _ = rest.split(":")
            c = comparisons[(candidate, name)]
            expected = tuple(f"metric:{arm}:{seed}:{name}" for seed in c.seeds
                             for arm in ("baseline", candidate))  # fmt: skip
            if refs[1:] != expected:
                problems.append(f"{nid}: its evidence is not the metrics of every paired seed")
        if kind == "world_model":
            movement = _movement(world_model, rest.split(":")[1])
            if refs[1:] != tuple(f"track:{t.track_id}" for t in movement.tracks):
                problems.append(f"{nid}: its evidence is not the tracks counted in the movement")

    numbers = {n.number_id: n for n in report.numbers}
    verdicts: dict[str, dict[str, str]] = defaultdict(dict)
    for c in fresh.comparisons:
        verdicts[c.candidate][c.metric_name] = c.verdict
    confirmed = confirmed_improvements(fresh)
    for finding in report.candidates:
        iid = finding.intervention_id
        if finding.verdicts != verdicts.get(iid, {}):
            problems.append(f"{iid}: its verdicts do not follow from the recomputed comparisons")
        if finding.confirmed_improvements != confirmed.get(iid, ()):
            problems.append(f"{iid}: its confirmed improvements do not follow from Holm's test")
        for number_id in finding.number_ids:
            ref = numbers[number_id].refs[0]
            if not ref.startswith((f"comparison:{iid}:", f"outcome:{iid}:")):
                problems.append(f"{iid}: number {number_id} belongs to another source ({ref})")

    # The grounding verdict is recomputed from the reply, never copied from the report.
    expected_grounding = grounding
    if reasoning is not None and (grounding is not None or report.grounding is not None):
        expected_grounding = ground(reasoning.output, bundle, reasoning.request_key)
        if grounding is not None and grounding != expected_grounding:
            problems.append("the grounding report given does not follow from the reply")
    try:
        rebuilt = assemble_report(
            world_model=world_model, bundle=bundle, reasoning=reasoning, outcomes=outcomes,
            experiment=experiment, evaluation=fresh,
            artifacts={k: v for k, v in report.reproducibility.items()
                       if k not in REPRODUCIBILITY_KEYS},
            now=report.created_at_utc, baseline_assessment=report.baseline_assessment,
            extra_limitations=extra_limitations, grounding=expected_grounding,
            data_sources=data_sources, confirmation=confirmation,
        )  # fmt: skip
    except (ValueError, LookupError) as error:
        problems.append(f"the report cannot be assembled again from its inputs: {error}")
    else:
        for name in DecisionReport.model_fields:
            if name != "report_id" and getattr(rebuilt, name) != getattr(report, name):
                problems.append(
                    f"the report's {name} differs from the report assembled again from the "
                    "recorded inputs and the recomputed evaluation"
                )
        if rebuilt.report_id != report.report_id:
            problems.append("the report id does not hash the report's content")
    return tuple(problems)


def comparison_rows(evaluation: EvaluationResult) -> list[dict[str, Any]]:
    """The comparisons as the rows a known-answer record keeps (and the demo displays)."""
    return [
        {
            "candidate": c.candidate,
            "metric": c.metric_name,
            "unit": c.unit,
            "better": c.better,
            "n": len(c.seeds),
            "baseline_mean": c.baseline_mean,
            "candidate_mean": c.candidate_mean,
            "mean_difference": c.mean_difference,
            "sd_difference": c.sd_difference,
            "ci95_difference": c.ci95_difference,
            "verdict": c.verdict,
            "shared_state_seeds": getattr(c, "shared_state_seeds", ()),
        }
        for c in evaluation.comparisons
    ]


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _as_json(value: object) -> object:
    """`value` as a committed record holds it after json.dumps (tuples as lists, keys as text)."""
    return json.loads(json.dumps(value))


def _record_paths(record: dict, root: Path) -> tuple[Path | None, Path | None]:
    """(experiment directory, stored evaluation) of a committed record's run under `root`. A
    re-evaluation keeps its own evaluation.json beside the source run's experiment."""
    run_dir = root / str(record.get("run_dir", ""))
    places = [run_dir / "experiment"]
    if record.get("source_run"):
        places.append(root / str(record["source_run"]) / "experiment")
    experiment_dir = next((p for p in places if (p / EXPERIMENT_FILE).is_file()), None)
    if experiment_dir is None:
        return None, None
    own = run_dir / EVALUATION_FILE
    return experiment_dir, own if own.is_file() else experiment_dir / EVALUATION_FILE


def _record_claims(record: dict, fresh: EvaluationResult) -> tuple[list[str], list[str], bool]:
    """(problems, notes, all read): what the record itself says about its comparisons, Holm tests
    and eligibility, against the recomputed evaluation, in whichever form the record keeps them
    (the evaluation's own, or a known-answer record's displayed rows and nested sections). A claim
    kept in a form this code cannot read gets a note and is not certified."""
    problems: list[str] = []
    notes: list[str] = []
    read = True
    found = record.get("comparisons")
    if record.get("comparison_error"):
        notes.append(f"the record holds no comparisons: {record['comparison_error']}")
        read = False
    elif isinstance(found, list) and all(
        isinstance(c, dict) and {"candidate", "metric_name"} <= set(c) for c in found
    ):
        if [{k: v for k, v in c.items() if k not in PAIR_FIELDS} for c in found] != _as_json(
            [c.model_dump(mode="json") for c in fresh.comparisons]
        ):
            problems.append("the record's comparisons are not the ones recomputed from the SUMO "
                            "outputs")  # fmt: skip
    elif isinstance(found, dict) and isinstance(found.get("rows"), list):
        if found["rows"] != _as_json(comparison_rows(fresh)):
            problems.append("the record's comparisons are not the ones recomputed from the SUMO "
                            "outputs")  # fmt: skip
    elif found is not None:
        notes.append("the record's comparisons cannot be read by this code, so they are not "
                     "certified")  # fmt: skip
        read = False

    holm = record.get("holm") if isinstance(record.get("holm"), dict) else {}
    recomputed = _as_json([t.model_dump(mode="json") for t in fresh.improvement_tests])
    for tests in (record.get("improvement_tests"), holm.get("tests")):
        if tests is not None and tests != recomputed:
            problems.append("the record's improvement tests are not the ones recomputed")
    if "planned_tests" in holm and holm["planned_tests"] != len(fresh.improvement_tests):
        problems.append("the record's count of planned Holm tests is not the recomputed one")

    eligibility = record.get("eligibility")
    nested = isinstance(eligibility, dict)
    entries = eligibility.get("candidates") if nested else eligibility
    if eligibility is not None:
        if not isinstance(entries, list):
            notes.append("the record's eligibility cannot be read by this code, so it is not "
                         "certified")  # fmt: skip
            read = False
        elif entries != _as_json([e.model_dump(mode="json") for e in fresh.eligibility]):
            problems.append("the record's eligibility is not the one recomputed")
    for name in ("baseline_censored_lanes", "baseline_entry_blocked") if nested else ():
        if name in eligibility and eligibility[name] != _as_json(getattr(fresh, name)):
            problems.append(f"the record's {name} is not the one recomputed")
    return problems, notes, read


# DecisionReport field -> the keys the run scripts' records have used for it
RECORD_REPORT_FIELDS = {
    "selected_intervention_id": ("selected_intervention_id", "selected"),
    "recommended_intervention_id": ("recommended_intervention_id", "recommended"),
    "selection_reason": ("selection_reason",),
    "recommendation": ("recommendation",),
}


def _summary_problems(summary: dict, report: DecisionReport, root: Path) -> list[str]:
    """Where the record's account of its report differs from the report itself."""
    problems = []
    if not any(key in summary for key in RECORD_REPORT_FIELDS["selected_intervention_id"]):
        problems.append("the record names no selection")
    for field, keys in RECORD_REPORT_FIELDS.items():
        for key in keys:
            if key in summary and summary[key] != getattr(report, field):
                problems.append(f"the record's {key} is not the report's")
    status = report.baseline_assessment.status if report.baseline_assessment else "not_assessed"
    stated = summary.get("baseline_assessment")
    stated = stated if isinstance(stated, dict) else {}
    if stated.get("status", summary.get("baseline_assessment_status", status)) != status:
        problems.append("the record's baseline assessment status is not the report's")
    if stated.get("path") and stated.get("file_sha256"):
        path = root / str(stated["path"])
        if not path.is_file() or _file_sha256(path) != stated["file_sha256"]:
            problems.append(f"the baseline assessment {stated['path']} is missing or changed")
    return problems


def _decision_problems(
    report: DecisionReport, experiment: ExperimentManifest, fresh: EvaluationResult, root: Path
) -> list[str]:
    """What trace_report re-derives without the report's other inputs: the experiment, the
    baseline assessment, the selection and the recommendation."""
    problems = []
    if report.experiment_id != experiment.experiment_id:
        problems.append("the report belongs to another experiment")
    assessment = report.baseline_assessment
    if assessment is not None:
        if assessment.baseline_manifest_sha256 != experiment.baseline_manifest_sha256:
            problems.append("the baseline assessment is about another baseline")
        for name, digest in assessment.evidence.items():
            path = root / name
            if not path.is_file() or _file_sha256(path) != digest:
                problems.append(f"baseline assessment evidence {name} is missing or changed")
    if (report.selected_intervention_id, report.selection_reason) != select_candidate(fresh):
        problems.append("the selection does not follow from the evaluation")
    if (report.recommended_intervention_id, report.recommendation) != recommend(
        report.selected_intervention_id, report.baseline_assessment
    ):
        problems.append("the recommendation does not follow from the baseline assessment")
    return problems


def _report_inputs(
    report: DecisionReport, run_dir: Path, root: Path
) -> tuple[dict, list[str], list[str]]:
    """(inputs, missing, problems): what `report` was assembled with, found by the hashes it
    records. The bundle, outcomes and reply are looked for in the run directory, then in the
    source run whose bundle.json the report lists (`source/bundle.json`); each must hash as the
    report lists it. The world model is the one whose content hashes to `world_model_sha256`, in
    the run, the source run or data/processed under `root`. The run script's own limitation lines
    are read from the report (they sit after the first two); every other line is rebuilt."""
    listed = report.reproducibility
    places = [run_dir]
    if digest := listed.get("source/bundle.json"):
        places += [p.parent for p in sorted((root / "experiments/runs").glob("*/bundle.json"))
                   if p.parent != run_dir and _file_sha256(p) == digest]  # fmt: skip
    missing: list[str] = []
    problems: list[str] = []

    def find(name: str) -> Path | None:
        digest = listed.get(name) or listed.get(f"source/{name}")
        present = [p / name for p in places if (p / name).is_file()]
        match = next((p for p in present if digest is None or _file_sha256(p) == digest), None)
        if match is None and present:
            problems.append(f"{name} does not hash as the report lists it")
        elif match is None:
            missing.append(f"{name} (the input the report was assembled with)")
        return match

    found = {name: find(name) for name in ("bundle.json", "outcomes.json")}
    if listed.get("reasoning_request_key", "none") != "none":
        found["reasoning.json"] = find("reasoning.json")
    processed = root / "data/processed"
    worlds = [p / "world_model.json" for p in places]
    worlds += sorted(processed.glob("*/world_model.json")) + sorted(
        processed.glob("*/*/world_model.json")
    )
    world = None
    for path in (p for p in worlds if p.is_file()):
        model = TrafficWorldModel.model_validate_json(path.read_text(encoding="utf-8"))
        if hashlib.sha256(model.model_dump_json().encode()).hexdigest() == listed.get(
            "world_model_sha256"
        ):
            world = model
            break
    if world is None:
        missing.append("the world model whose content hashes to the report's world_model_sha256 "
                       "(data/processed is Git-ignored)")  # fmt: skip
    if missing or problems:
        return {}, missing, problems
    reasoning_path = found.get("reasoning.json")
    return (
        {
            "world_model": world,
            "bundle": EvidenceBundle.model_validate_json(
                found["bundle.json"].read_text(encoding="utf-8")  # type: ignore[union-attr]
            ),
            "outcomes": TypeAdapter(tuple[ValidationOutcome, ...]).validate_json(
                found["outcomes.json"].read_text(encoding="utf-8")  # type: ignore[union-attr]
            ),
            "reasoning": None
            if reasoning_path is None
            else ReasoningResult.model_validate_json(reasoning_path.read_text(encoding="utf-8")),
        },
        [],
        [],
    )


def _report_trace(
    record: dict,
    run_dir: Path,
    experiment_dir: Path,
    stored: EvaluationResult,
    fresh: EvaluationResult,
    root: Path,
    *,
    current: bool,
) -> tuple[list[str], list[str], list[str], bool]:
    """(problems, notes, missing, traced) for the report the record names: report.json (and
    report.md) must hash as the record says and the record must say what the report says; under
    the current rules the report is then traced again (trace_report) from the inputs it was
    assembled with, or, when they are gone, its selection and recommendation are re-derived."""
    summary = record.get("report")
    if not isinstance(summary, dict):
        return ["the record names no report"], [], [], False
    path = run_dir / "report.json"
    if not path.is_file():
        return [], [], [f"{record.get('run_dir')}/report.json, the report the record names"], False
    problems: list[str] = []
    for name, key in (("report.json", "report_json_sha256"), ("report.md", "report_md_sha256")):
        if key not in summary:
            if name == "report.json":
                problems.append("the record does not hash its report (no report_json_sha256)")
        elif not (run_dir / name).is_file() or _file_sha256(run_dir / name) != summary[key]:
            problems.append(f"{name} does not hash as the record says")
    try:
        report = DecisionReport.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        if current:
            return [*problems, f"report.json is not a decision report: {error}"], [], [], False
        return problems, ["its report.json predates the current report contract; what the "
                          "record says of it is not compared"], [], False  # fmt: skip
    problems += _summary_problems(summary, report, root)
    if not current:
        return problems, [], [], False
    experiment = ExperimentManifest.model_validate_json(
        (experiment_dir / EXPERIMENT_FILE).read_text(encoding="utf-8")
    )
    try:
        inputs, missing, unread = _report_inputs(report, run_dir, root)
    except (OSError, ValidationError) as error:
        inputs, missing, unread = {}, [], [f"the report's inputs cannot be read: {error}"]
    if not inputs:
        return [*problems, *unread, *_decision_problems(report, experiment, fresh, root)], [], (
            missing
        ), False  # fmt: skip
    try:
        plain = assemble_report(
            **inputs, experiment=experiment, evaluation=fresh, now=report.created_at_utc,
            artifacts={k: v for k, v in report.reproducibility.items()
                       if k not in REPRODUCIBILITY_KEYS},
            baseline_assessment=report.baseline_assessment, data_sources=report.data_sources,
        )  # fmt: skip
    except (ValueError, LookupError) as error:
        return [*problems, f"the report cannot be assembled again from its inputs: {error}"], (
            []
        ), [], False  # fmt: skip
    extra = report.limitations[2 : 2 + max(0, len(report.limitations) - len(plain.limitations))]
    problems += trace_report(
        report, **inputs, experiment=experiment, evaluation=stored,
        experiment_dir=experiment_dir, extra_limitations=extra,
        data_sources=report.data_sources, evidence_root=root,
    )  # fmt: skip
    return problems, [], [], True


def retrace_record(record_path: Path, root: Path) -> dict:
    """A committed record traced again under the current checks, from its run under `root`.

    Every stored metric is recomputed from its SUMO file, whatever rules the record used, and
    report.json (and report.md) must hash as the record says, with the record's selection,
    recommendation and baseline assessment status the report's own. Under the current rules the
    stored evaluation, the Holm family and everything the record says about its comparisons (in
    either recorded form), Holm tests and eligibility are re-derived, and the report is traced
    again (`trace_report`) from the inputs it was assembled with. Under older rules none of that
    can be re-derived, and the result says so. The pairs are rebuilt from the recorded demand files
    either way. Status: `failed` when anything differs (every problem is kept); `unverifiable`
    when the run or the report's inputs are absent, the rules are older, or a claim cannot be
    read; `verified` only when everything was re-derived and traced. Nothing is written."""
    record = json.loads(record_path.read_text(encoding="utf-8"))
    result: dict = {
        "record": record_path.name,
        "script": record.get("script"),
        "run_dir": record.get("run_dir"),
        "status": "unverifiable",
        "checks": {
            "metrics recomputed": None,
            "comparisons re-derived": None,
            "report traced": None,
            "pairs rebuilt": None,
        },
        "problems": [],
        "notes": [],
        "missing": [],
        "pair_identity": None,
    }
    problems, notes, checks = result["problems"], result["notes"], result["checks"]
    experiment_dir, stored_path = _record_paths(record, root)
    if experiment_dir is None or stored_path is None:
        result["missing"].append(f"{record.get('run_dir')}: no experiment directory under {root} "
                                 "(Git-ignored; run the experiment again)")  # fmt: skip
        return result
    try:
        stored = EvaluationResult.model_validate_json(stored_path.read_text(encoding="utf-8"))
        fresh = compute_evaluation(experiment_dir)
    except (EvaluationError, MetricError, OSError, ValidationError, ET.ParseError) as error:
        problems.append(f"the evaluation cannot be read or recomputed from the SUMO outputs: "
                        f"{error}")  # fmt: skip
        result["status"] = "failed"
        return result

    ours = {(m.arm, m.seed, m.metric_name): m.value for m in stored.metrics}
    theirs = {(m.arm, m.seed, m.metric_name): m.value for m in fresh.metrics}
    changed = sorted(
        k
        for k in ours
        if k in theirs and abs(ours[k] - theirs[k]) > 1e-9 * max(1.0, abs(theirs[k]))
    )
    lost = sorted(k for k in ours if k not in theirs)
    problems += [f"metric {a}/{s}/{n}: stored {ours[(a, s, n)]}, recomputed {theirs[(a, s, n)]}"
                 for a, s, n in changed[:25]]  # fmt: skip
    problems += [f"metric {a}/{s}/{n} is stored but not recomputed" for a, s, n in lost[:25]]
    checks["metrics recomputed"] = (f"{len(ours)} stored metrics: {len(changed)} differ, "
                                    f"{len(lost)} not recomputed")  # fmt: skip

    current = (stored.rules_version, stored.calculation_version) == (
        RULES_VERSION, CALCULATION_VERSION
    )  # fmt: skip
    read = False
    if current:
        problems += _evaluation_differences(stored, fresh)
        found, unread, read = _record_claims(record, fresh)
        problems += found
        notes += unread
        try:
            family = sorted(holm_family(experiment_dir))
        except (EvaluationError, OSError, ValidationError) as error:
            problems.append(f"the Holm family cannot be read from the manifests: {error}")
        else:
            if sorted((t.candidate, t.metric_name) for t in stored.improvement_tests) != family:
                problems.append("the stored improvement tests are not the Holm family the "
                                "manifests fix")  # fmt: skip
        checks["comparisons re-derived"] = read
    else:
        notes.append(
            f"evaluated under rules {stored.rules_version or 'before 0.2.0'} and calculation "
            f"{stored.calculation_version}; this code implements rules {RULES_VERSION} and "
            f"calculation {CALCULATION_VERSION}, so its comparisons and selection are not "
            "re-derived, only its metrics"
        )
        checks["comparisons re-derived"] = False

    run_dir = root / str(record.get("run_dir", ""))
    found, unread, absent, traced = _report_trace(record, run_dir, experiment_dir, stored, fresh,
                                                  root, current=current)  # fmt: skip
    problems += found
    notes += unread
    result["missing"] += absent
    checks["report traced"] = traced

    paired = pair_problems(experiment_dir, fresh)
    problems += paired
    checks["pairs rebuilt"] = not paired
    if not paired:
        view = experiment_pair_view(experiment_dir)
        result["pair_identity"] = {"view_sha256": view.view_sha256, "pairs": len(view.pairs),
                                   "unit_of_inference": view.unit_of_inference,
                                   "sumo_binary_version": view.sumo_binary_version}  # fmt: skip
    result["status"] = (
        "failed" if problems else "verified" if current and read and traced else "unverifiable"
    )
    return result


def retrace_records(record_paths: Sequence[Path], root: Path) -> list[dict]:
    """`retrace_record` for every record, in the order given; none is skipped."""
    return [retrace_record(path, root) for path in record_paths]


def empty_trace_records(records_dir: Path) -> list[Path]:
    """Every record in `records_dir` whose report trace was empty (`trace_problems: []`), in name
    order: the records whose traces the stricter check takes again."""
    found = []
    for path in sorted(records_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        report = record.get("report") if isinstance(record, dict) else None
        if isinstance(report, dict) and report.get("trace_problems") == []:
            found.append(path)
    return found


def confirmation(finding: CandidateFinding, metric_name: str) -> str:
    """'yes' or 'not confirmed', or why the metric cannot count for this candidate."""
    if not is_improvement_metric(metric_name):
        return "never counted (can only worsen)"
    if finding.simulation_status in ("incomplete", "invalid_simulation"):
        return "not selectable"
    return "yes" if metric_name in finding.confirmed_improvements else "not confirmed"


def render_markdown(report: DecisionReport) -> str:
    """A readable report; every number appears with its id, e.g. `12.3 s [n7]`."""
    numbers = {n.number_id: n for n in report.numbers}

    def show(number_id: str) -> str:
        n = numbers[number_id]
        return f"{n.value:.6g} {n.unit} [{n.number_id}]"

    lines = [
        f"# CairoFlow decision report {report.report_id}",
        "",
        f"Experiment `{report.experiment_id}`. Decision support only: nothing here controls "
        "real traffic. Outcome numbers are simulated by SUMO.",
        "",
        *(
            ["## Data sources", "", *(f"- {source}" for source in report.data_sources), ""]
            if report.data_sources
            else []
        ),  # fmt: skip
        "## Selection",
        "",
        f"Selected: **{report.selected_intervention_id or 'none'}**. {report.selection_reason}",
        "",
        f"Recommended: **{report.recommended_intervention_id or 'none'}**. {report.recommendation}",
        "",
        f"Rule: {report.selection_rule} {RECOMMENDATION_RULE}",
        "",
        f"Objective: {report.objective}",
        "",
    ]
    assessment = report.baseline_assessment
    lines += ["## Baseline assessment", ""]
    if assessment is None:
        lines += ["Not assessed: the baseline has not been checked against observations.", ""]
    else:
        lines += [f"Status: **{assessment.status.replace('_', ' ')}**. {assessment.summary}", ""]
        lines += [f"- {finding}" for finding in assessment.findings]
        lines += ["", f"Assessed on {assessment.assessed_on}; see {assessment.documented_in}.", ""]
        lines += [f"- `{path}`: `{digest}`" for path, digest in assessment.evidence.items()]
        lines.append("")
    for source, title in (
        ("settings", "Experiment settings"),
        ("world_model", "Demand (world model; each row gives its kind)"),
    ):
        lines += [f"## {title}", "", "| Number | Value | Kind |", "|---|---|---|"]
        lines += [
            f"| {n.label} | {show(n.number_id)} | {n.kind} |"
            for n in report.numbers
            if n.source == source
        ]
        lines.append("")
    lines += ["## Candidates", ""]
    for c in report.candidates:
        lines += [f"### {c.intervention_id} ({c.status})", ""]
        if c.validator_reasons:
            lines += [f"Validator: {'; '.join(c.validator_reasons)}", ""]
        if c.simulation_status in SIMULATION_TEXT:
            lines += [f"Simulation: {SIMULATION_TEXT[c.simulation_status]}", ""]
            lines += [f"- {reason}" for reason in c.simulation_reasons]
            lines += [""] if c.simulation_reasons else []
        if c.rationale:
            lines += [f"Model rationale (model-generated text, not evidence): {c.rationale}", ""]
        if c.expected_tradeoffs:
            tradeoffs = "; ".join(c.expected_tradeoffs)
            lines += [f"Model-stated trade-offs (not evidence): {tradeoffs}", ""]
        if c.unverified_assumptions:
            assumed = "; ".join(c.unverified_assumptions)
            lines += [f"Model-stated preconditions (unverified assumptions): {assumed}", ""]
        if c.grounding_reasons:
            lines += [f"Kept from planning by the evidence check: {'; '.join(c.grounding_reasons)}",
                      ""]  # fmt: skip
        if c.verdicts:
            lines += [
                "| Metric | Verdict (uncorrected 95%) | Improvement confirmed (Holm) |",
                "|---|---|---|",
            ]
            lines += [
                f"| {name} | {verdict} | {confirmation(c, name)} |"
                for name, verdict in c.verdicts.items()
            ]
            lines.append("")
        if c.number_ids:
            lines += ["| Number | Value | Kind |", "|---|---|---|"]
            lines += [
                f"| {numbers[i].label} | {show(i)} | {numbers[i].kind} |" for i in c.number_ids
            ]
            lines.append("")
    lines += ["## Hypotheses (model-generated text, not evidence)", ""]
    checked = {g.hypothesis_id: g for g in report.grounding.hypotheses} if report.grounding else {}
    for h in report.hypotheses:
        lines.append(
            f"- **{h.hypothesis_id}** ({h.confidence_label} confidence): {h.statement} "
            f"Supporting evidence: {', '.join(h.supporting_evidence_ids) or 'none'}."
        )
        details = [
            ("Contradicting evidence", ", ".join(h.contradicting_evidence_ids)),
            ("Assumptions", "; ".join(h.assumptions)),
            ("Alternative explanations", "; ".join(h.alternative_explanations)),
            ("Limiting factor", h.limiting_factor or ""),
            ("Claim type", h.claim_type or ""),
            ("Restated evidence", "; ".join(f"{o.evidence_id}: {o.statement}"
                                            for o in h.observations)),
            ("Inference", h.inference or ""),
            ("Missing evidence", "; ".join(h.missing_evidence)),
        ]  # fmt: skip
        lines += [f"  - {label}: {text}" for label, text in details if text]
        if (g := checked.get(h.hypothesis_id)) is not None:
            verdict = g.status + (f" ({'; '.join(g.reasons)})" if g.reasons else "")
            lines.append(f"  - Evidence check (code): {verdict}"
                         + (f"; flags: {'; '.join(g.flags)}" if g.flags else ""))  # fmt: skip
    if report.grounding is not None:
        competing = "yes" if report.grounding.competing_hypotheses else "no"
        lines += ["", f"Evidence checks: rules {report.grounding.rules_version}; competing "
                  f"hypotheses supported: {competing}."]  # fmt: skip
        lines += [f"- {note}" for note in report.grounding.notes]
    lines += ["", "## Limitations", ""]
    lines += [f"- {item}" for item in report.limitations]
    lines += ["", "## Reproducibility", ""]
    lines += [f"- `{name}`: `{value}`" for name, value in report.reproducibility.items()]
    return "\n".join(lines) + "\n"


def write_report(report: DecisionReport, out_dir: Path) -> tuple[Path, Path]:
    """report.json and report.md in `out_dir`; never overwrites."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / "report.json", out_dir / "report.md"
    with json_path.open("x", encoding="utf-8") as handle:
        handle.write(report.model_dump_json(indent=2) + "\n")
    with md_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(render_markdown(report))
    return json_path, md_path
