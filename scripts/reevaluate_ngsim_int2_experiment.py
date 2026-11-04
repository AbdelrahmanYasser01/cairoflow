"""Re-evaluate a recorded intersection 2 experiment under the current rules, with no model call.

    uv run --frozen python scripts/reevaluate_ngsim_int2_experiment.py [RUN_DIR] [--record]

RUN_DIR defaults to the recorded replay of the 2026-10-28 experiment. The script reads that run's
own files: bundle.json, reasoning.json and outcomes.json (the cached Muse reply, already checked by
the validator) and experiment/ (the SUMO runs). Each is checked against the hash the run's
report.json lists, and the world model and baseline against the scenario record. It then
recomputes the evaluation and the report with the current eligibility, Holm and recommendation
rules (ADR-021) and the committed baseline assessment, and traces every number (A9).

There is no Muse client, no connection and no key. Nothing in RUN_DIR changes. Output goes to a
new directory, experiments/runs/ngsim-int2-reevaluation-<stamp>/ (Git-ignored), holding
evaluation.json, report.json, report.md, summary.json and pipeline_record.json, so the read-only
report app lists it. Its numbers trace to the SUMO files that stay in RUN_DIR. With `--record`,
the summary also goes to experiments/records/ (committed). Like the experiment record, the
summary holds ids, numbers and hashes but no model-written text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from cairoflow_contracts.evidence import TrafficWorldModel
from cairoflow_contracts.experiment import EvaluationResult, ExperimentManifest
from cairoflow_contracts.reasoning import EvidenceBundle, ReasoningResult, ValidationOutcome
from cairoflow_contracts.report import DecisionReport
from cairoflow_evaluation.compare import compute_evaluation
from cairoflow_evaluation.pipeline import RECORD_FILE, PipelineRecord
from cairoflow_evaluation.report import (
    assemble_report,
    comparison_record,
    holm_family,
    load_baseline_assessment,
    trace_report,
    write_report,
)
from cairoflow_simulation.network import content_sha256

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
ASSESSMENT = REPO / "data/geometry/ngsim-lankershim-int2/baseline_assessment.json"
RECORDS_DIR = REPO / "experiments/records"
DEFAULT_RUN = REPO / "experiments/runs/ngsim-int2-experiment-20261028T084336Z"
CHECKED = (
    "bundle.json",
    "reasoning.json",
    "outcomes.json",
    "experiment/experiment_manifest.json",
    "experiment/evaluation.json",
)
DATA_SOURCE = (
    "Published NGSIM vehicle trajectories: Lankershim Boulevard, intersection 2, 2005-06-16 "
    "08:30-08:45 (data.transportation.gov dataset 8ect-6jqj). No camera input."
)


class InputError(ValueError):
    """A recorded input does not match the hash recorded for it."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, text: str) -> str:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_run(run: Path, scenario: dict) -> dict[str, str]:
    """The recorded run's input hashes, each checked against its report and the scenario record."""
    recorded = DecisionReport.model_validate_json((run / "report.json").read_text("utf-8"))
    hashes = recorded.reproducibility
    found: dict[str, str] = {}
    for name in CHECKED:
        digest = sha256_file(run / name)
        if hashes.get(name) != digest:
            raise InputError(f"{run / name} does not match the hash in its report.json")
        found[name] = digest
    if hashes.get("baseline_manifest_sha256") != scenario["baseline"]["manifest_sha256"]:
        raise InputError("the run was made on another baseline than the scenario record's")
    world = REPO / scenario["processed_outputs"]["dir"] / "world_model.json"
    if sha256_file(world) != scenario["processed_outputs"]["sha256"]["world_model.json"]:
        raise InputError(f"{world} does not match the scenario record")
    return found


def check_outputs(run: Path) -> tuple[list[str], list[str]]:
    """SUMO output files checked against the hashes the run's own evaluation recorded, and the
    queue.xml files it holds no hash for (verified, unverified). Every tripinfo.xml is checked
    against its RunRecord when it is evaluated (compare.py and eligibility.py)."""
    recorded = EvaluationResult.model_validate_json(
        (run / "experiment/evaluation.json").read_text("utf-8")
    )
    expected = {m.source_output_file: m.source_sha256 for m in recorded.metrics}
    for name, digest in expected.items():
        path = run / "experiment" / name
        if not path.is_file() or content_sha256(path, strip_comments=True) != digest:
            raise InputError(f"{path} does not match the hash its evaluation recorded")
    root = (run / "experiment").resolve()
    queues = sorted(
        os.path.relpath(path.resolve(), root) for path in root.glob("candidates/*/runs/*/queue.xml")
    )
    return sorted(expected), [name for name in queues if name not in expected]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", nargs="?", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--record", action="store_true", help="write the committed summary")
    args = parser.parse_args(argv)
    run = args.run_dir.resolve()

    scenario = json.loads(RECORD.read_text(encoding="utf-8"))
    try:
        inputs = check_run(run, scenario)
        verified, unverified = check_outputs(run)
        assessment = load_baseline_assessment(
            ASSESSMENT, scenario["baseline"]["manifest_sha256"], REPO
        )
    except (InputError, ValueError, FileNotFoundError) as error:
        print(f"input check failed: {error}", file=sys.stderr)
        return 2
    world_path = REPO / scenario["processed_outputs"]["dir"] / "world_model.json"
    world = TrafficWorldModel.model_validate_json(world_path.read_text(encoding="utf-8"))
    bundle = EvidenceBundle.model_validate_json((run / "bundle.json").read_text("utf-8"))
    reasoning = ReasoningResult.model_validate_json((run / "reasoning.json").read_text("utf-8"))
    outcomes = TypeAdapter(tuple[ValidationOutcome, ...]).validate_json(
        (run / "outcomes.json").read_text("utf-8")
    )
    experiment = ExperimentManifest.model_validate_json(
        (run / "experiment/experiment_manifest.json").read_text("utf-8")
    )

    now = datetime.now(UTC).replace(microsecond=0)
    out = REPO / "experiments/runs" / f"ngsim-int2-reevaluation-{now:%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True)
    family = holm_family(run / "experiment")  # from the manifests, before any result is read
    evaluation = compute_evaluation(run / "experiment")  # in memory; RUN_DIR is not written
    artifacts = {f"source/{name}": digest for name, digest in inputs.items()}
    artifacts["evaluation.json"] = write_new(
        out / "evaluation.json", evaluation.model_dump_json(indent=2) + "\n"
    )
    if assessment is not None:
        artifacts["baseline_assessment.json"] = sha256_file(ASSESSMENT)
    report = assemble_report(
        world_model=world,
        bundle=bundle,
        reasoning=reasoning,
        outcomes=outcomes,
        experiment=experiment,
        evaluation=evaluation,
        artifacts=dict(sorted(artifacts.items())),
        now=now,
        baseline_assessment=assessment,
    )
    problems = trace_report(
        report,
        world_model=world,
        outcomes=outcomes,
        experiment=experiment,
        evaluation=evaluation,
        experiment_dir=run / "experiment",
        bundle=bundle,
        reasoning=reasoning,
        evidence_root=REPO,
    )
    json_path, md_path = write_report(report, out)
    artifacts[json_path.name] = sha256_file(json_path)
    artifacts[md_path.name] = sha256_file(md_path)
    tripinfo = {
        f"source/{path.relative_to(run).as_posix()}": content_sha256(path, strip_comments=True)
        for path in sorted((run / "experiment").rglob("tripinfo.xml"))
    }
    if not problems:  # the app shows only reports whose numbers trace
        record = PipelineRecord(
            run_name=out.name,
            mode="replay",
            as_of_utc=now,
            inputs_sha256=sha256_file(RECORD),
            artifacts=dict(sorted(artifacts.items())),
            tripinfo=tripinfo,
            clip_attribution=f"{scenario['input']['attribution']}; {scenario['input']['licence']}",
            notes=(
                f"Re-evaluation of {run.name} under rules {evaluation.rules_version} (ADR-021), "
                "from its own recorded files: no model call. The SUMO runs are that run's.",
                "Camera 2 is not used (ADR-018/019); the evidence is the published trajectories.",
            ),
            selected_intervention_id=report.selected_intervention_id,
            data_source=DATA_SOURCE,
        )
        write_new(out / RECORD_FILE, record.model_dump_json(indent=2) + "\n")
    summary = {
        "script": "scripts/reevaluate_ngsim_int2_experiment.py",
        "created_at_utc": now.isoformat(),
        "run_dir": str(out.relative_to(REPO)),
        "source_run": str(run.relative_to(REPO)) if run.is_relative_to(REPO) else str(run),
        "source_hashes": inputs,
        "sumo_outputs": {
            "checked_against_recorded_evaluation": len(verified),
            "tripinfo_checked_against_run_records": "all",
            "queue_xml_without_a_recorded_hash": unverified,
            "note": "queue.xml of runs the recorded evaluation did not compare (I1, I3) has no "
            "recorded hash anywhere, so it is read as found; tripinfo.xml of every run is "
            "checked against its RunRecord",
        },
        "rules_version": evaluation.rules_version,
        "label": "simulated",
        "eligibility": [e.model_dump(mode="json") for e in evaluation.eligibility],
        **comparison_record(run / "experiment", evaluation, family=family),
        "improvement_tests": [t.model_dump(mode="json") for t in evaluation.improvement_tests],
        "report": {
            "selected_intervention_id": report.selected_intervention_id,
            "selection_reason": report.selection_reason,
            "recommended_intervention_id": report.recommended_intervention_id,
            "recommendation": report.recommendation,
            "baseline_assessment_status": assessment.status if assessment else "not_assessed",
            "trace_problems": list(problems),
            "report_json_sha256": artifacts[json_path.name],
            "report_md_sha256": artifacts[md_path.name],
            "text": "report.md holds model text, labelled; it stays in the run directory",
        },
        "tripinfo": tripinfo,
    }
    text = json.dumps(summary, indent=2) + "\n"
    write_new(out / "summary.json", text)
    if args.record:
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)
        write_new(RECORDS_DIR / f"ngsim-int2-reevaluation-{now:%Y%m%dT%H%M%SZ}.json", text)
    print(f"wrote {out.relative_to(REPO)}; trace problems: {len(problems)}")
    print(f"selected: {report.selected_intervention_id}. {report.selection_reason}")
    print(f"recommended: {report.recommended_intervention_id}. {report.recommendation}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
