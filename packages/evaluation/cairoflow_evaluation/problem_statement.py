"""The S-approach ProblemStatement, projected from committed records (G3a).

Reads, never writes, with paths relative to `repo`:
- the timeline-ensemble record: each member's comparisons and Holm tests on the subject metric,
  its baseline assessment and the record's limitations;
- each member's realism experiment record (named after the member's experiment directory): the
  assessment it cites and the Muse reply it pins;
- each member's baseline record (named after its baseline directory): the demand note the unit of
  inference rests on ("every seed has the same vehicles ...");
- the replay assessment file each experiment record cites, re-hashed against the citation, its
  status checked against it, its summary copied;
- the committed validation record of a member's baseline, when there is one, re-hashed against the
  hash its assessment pins: the NGSIM-derived queue and flow quantities on the subject approach,
  and what it says of the decision metric's definition;
- the pinned Muse reply (reasoning.json), when its Git-ignored run file is present: its
  hypotheses attach as AI-generated text; when it is absent, `hypotheses_note` says so. The first
  copy in sorted path order is the one listed, so `sources` depends on which run files are here.
Nothing is computed beyond selecting and copying; contracts.problem_statement writes the text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from cairoflow_contracts.evidence import Arm
from cairoflow_contracts.pair_identity import unit_of_inference
from cairoflow_contracts.problem_statement import (
    AttachedHypothesis,
    ConfirmedImprovement,
    MemberBaseline,
    ObservedQuantity,
    ProblemStatement,
    SourceFile,
    build_problem_statement,
)
from cairoflow_contracts.reasoning import ReasoningResult

ENSEMBLE_RECORD = "experiments/records/ngsim-int2-timeline-ensemble-20261029T123045Z.json"
VALIDATION_GLOB = "experiments/records/ngsim-int2-realism-validity-*/validation/validation.json"
REASONING_GLOB = "experiments/runs/*/reasoning.json"
OBSERVED_KINDS = ("count", "queue_mean", "queue_max", "end_of_red_queue", "discharge_per_green")
ONE_REALISATION = "every seed has the same vehicles"  # the baseline records' demand note
DEFINITION_KEY = "max_queue_m / mean_queue_m"  # validation.decision_metrics on the queue metrics


class ProblemStatementError(ValueError):
    """The records do not support the statement (a missing comparison, a changed file)."""


def _read(repo: Path, relative: str) -> tuple[SourceFile, bytes]:
    data = (repo / relative).read_bytes()
    return SourceFile(path=relative, sha256=hashlib.sha256(data).hexdigest()), data


def _observed(
    validation: dict[str, Any], source: SourceFile, member: str, approach: Arm
) -> list[ObservedQuantity]:
    found = []
    for comparison in validation["comparisons"]:
        kind, _, rest = comparison["key"].partition(".")
        if kind not in OBSERVED_KINDS or not rest.startswith(approach):
            continue
        observed, simulated = comparison["observed"], comparison["simulated"]
        interval = observed.get("interval")
        value = simulated.get("value")
        found.append(
            ObservedQuantity(
                key=comparison["key"],
                quantity=comparison["quantity"],
                unit=comparison["unit"],
                label=observed["label"],
                value=observed.get("value"),
                interval95=tuple(interval) if interval else None,
                lower_bound=bool(observed.get("lower_bound", False)),
                simulated_value=value if isinstance(value, int | float) else None,
                simulated_lower_bound=bool(simulated.get("lower_bound", False)),
                simulated_inside_observed_interval=comparison.get(
                    "simulated_inside_observed_interval"
                ),
                discrepancy=comparison.get("discrepancy"),
                comparability=comparison["status"],
                limitations=tuple(comparison.get("limitations", ())),
                member=member,
                source=source,
            )  # fmt: skip
        )
    return found


def _hypotheses(
    repo: Path, pinned: Iterable[str]
) -> tuple[list[AttachedHypothesis], list[SourceFile], str | None]:
    wanted = sorted(set(pinned))
    if not wanted:
        return [], [], "no Muse reply is pinned by the member records"
    found: dict[str, tuple[SourceFile, bytes]] = {}
    for path in sorted(repo.glob(REASONING_GLOB)):
        relative = path.relative_to(repo).as_posix()
        source, data = _read(repo, relative)
        if source.sha256 in wanted and source.sha256 not in found:
            found[source.sha256] = (source, data)
    attached = [
        AttachedHypothesis(hypothesis_id=h.hypothesis_id, statement=h.statement, source=source)
        for source, data in found.values()
        for h in ReasoningResult.model_validate_json(data).output.hypotheses
    ]
    absent = [digest[:12] for digest in wanted if digest not in found]
    note = (
        f"Muse hypotheses not present: the member records pin reasoning.json {', '.join(absent)}, "
        "a Git-ignored run file"
        if absent
        else None
    )
    return attached, [source for source, _ in found.values()], note


def _unit(repo: Path, member: dict[str, Any], n: int) -> tuple[str, str, SourceFile]:
    """The unit of inference, from the member's baseline record's own demand note."""
    relative = f"experiments/records/{Path(member['baseline_dir']).name}.json"
    source, raw = _read(repo, relative)
    notes = [note for note in json.loads(raw).get("demand_notes", ()) if ONE_REALISATION in note]
    if notes:
        return unit_of_inference(["one realisation"] * n), f"{relative}: {notes[0]}", source
    return unit_of_inference([None] * n), f"{relative}: no one-realisation note", source


def problem_statement_from_records(
    repo: Path, ensemble_record: str = ENSEMBLE_RECORD, approach: Arm = "S"
) -> ProblemStatement:
    """The problem on `approach`'s mean queue, from the committed records under `repo`."""
    metric = f"mean_queue_m.{approach}"
    ensemble_source, raw = _read(repo, ensemble_record)
    ensemble = json.loads(raw)
    sources = [ensemble_source]
    validations: dict[str, tuple[dict[str, Any], SourceFile]] = {}
    for path in sorted(repo.glob(VALIDATION_GLOB)):
        source, data = _read(repo, path.relative_to(repo).as_posix())
        record = json.loads(data)
        validations[record["baseline_dir"]] = (record, source)
    members: list[MemberBaseline] = []
    observed: list[ObservedQuantity] = []
    pinned: set[str] = set()
    definition_notes: set[str] = set()
    for member in ensemble["members"]:
        name = member["id"]
        comparisons = {
            c["candidate"]: c for c in member["comparisons"] if c["metric_name"] == metric
        }
        if not comparisons:
            raise ProblemStatementError(f"{name}: the ensemble record has no {metric} comparison")
        first = next(iter(comparisons.values()))
        if any(
            (c["baseline_mean"], c["n"], c["unit"], c["better"])
            != (first["baseline_mean"], first["n"], first["unit"], first["better"])
            for c in comparisons.values()
        ):
            raise ProblemStatementError(
                f"{name}: the {metric} comparisons disagree on the baseline"
            )
        improvements = [
            ConfirmedImprovement(
                candidate=test["candidate"],
                mean_difference=comparisons[test["candidate"]]["mean_difference"],
                ci95_difference=tuple(comparisons[test["candidate"]]["ci95_difference"]),
                t_statistic=test["t_statistic"],
                p_value=test["p_value"],
                holm_threshold=test["holm_threshold"],
            )  # fmt: skip
            for test in sorted(member["improvement_tests"], key=lambda t: t["candidate"])
            if test["metric_name"] == metric and test["confirmed"]
        ]
        record_source, record_raw = _read(
            repo, f"experiments/records/{Path(member['experiment_dir']).name}.json"
        )
        record = json.loads(record_raw)
        sources.append(record_source)
        cited = record["report"]["baseline_assessment"]
        if cited["status"] != member["baseline_assessment"]:
            raise ProblemStatementError(f"{name}: the experiment record cites another assessment")
        assessment_source, summary, pins = None, None, {}
        if cited.get("path"):
            assessment_source, assessment_raw = _read(repo, cited["path"])
            if assessment_source.sha256 != cited["file_sha256"]:
                raise ProblemStatementError(
                    f"{name}: {cited['path']} changed after the experiment cited it"
                )
            assessment = json.loads(assessment_raw)
            if assessment.get("status") != cited["status"]:
                raise ProblemStatementError(f"{name}: the assessment file states another status")
            summary, pins = assessment.get("summary"), assessment.get("evidence", {})
            sources.append(assessment_source)
        reply = record.get("source_hashes", {}).get("reasoning.json")
        if reply:
            pinned.add(reply)
        validation = validations.get(member["baseline_dir"])
        if validation is not None:
            record_path = validation[1].path
            if record_path in pins and pins[record_path] != validation[1].sha256:
                raise ProblemStatementError(
                    f"{name}: {record_path} is not the validation record its assessment pins"
                )
            sources.append(validation[1])
            observed += _observed(validation[0], validation[1], name, approach)
            note = validation[0].get("decision_metrics", {}).get(DEFINITION_KEY)
            if note:
                definition_notes.add(note)
        unit, basis, baseline_source = _unit(repo, member, first["n"])
        sources.append(baseline_source)
        members.append(
            MemberBaseline(
                member=name,
                timeline_id=member["timeline"]["timeline_id"],
                required=member["required"],
                baseline_manifest_sha256=member["baseline_manifest_sha256"],
                metric_name=metric,
                unit=first["unit"],
                better=first["better"],
                n=first["n"],
                baseline_mean=first["baseline_mean"],
                unit_of_inference=unit,
                unit_basis=basis,
                assessment=member["baseline_assessment"],
                assessment_source=assessment_source,
                assessment_summary=summary,
                validation=validation[1] if validation else None,
                improvements=improvements,
            )  # fmt: skip
        )
    if len(definition_notes) > 1:
        raise ProblemStatementError("the validation records disagree on the metric's definition")
    hypotheses, reply_sources, note = _hypotheses(repo, pinned)
    return build_problem_statement(
        approach=approach, metric_name=metric, members=members, observed=observed,
        definition_note=next(iter(definition_notes), None), hypotheses=hypotheses,
        hypotheses_note=note, limitations=ensemble.get("limitations", ()),
        sources=[*sources, *reply_sources],
    )  # fmt: skip
