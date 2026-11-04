"""Verify a run directory or a committed experiment record; nothing in a report is trusted (P0-2).

    uv run --frozen python scripts/verify_run.py RUN_DIR [--rerun-seeds N]
    uv run --frozen python scripts/verify_run.py --record experiments/records/<record>.json

A run directory written by scripts/run_cairoflow.py holds integrity.json, its identity.
`cairoflow_evaluation.integrity.verify_run` hashes every artifact again, recomputes the evaluation
from the SUMO outputs, assembles the report again and, with --rerun-seeds, runs SUMO again.

A committed realism-experiment record (scripts/run_ngsim_int2_realism_experiment.py, made before
identities existed) is checked against the hashes Git holds for it instead. Its run directory's
report.json and every tripinfo.xml must hash as the record says, and each stored metric is
recomputed from its SUMO file. When the record was evaluated under the current rules, the report
is also traced and assembled again; otherwise its comparisons cannot be re-derived by this code,
and the verdict is `unverifiable`, not `verified`. Run directories are Git-ignored, so on a fresh
clone every legacy verdict is `unverifiable` until the experiment is run again.

Exit status: 0 verified, 1 failed, 3 unverifiable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from cairoflow_contracts.evidence import TrafficWorldModel
from cairoflow_contracts.experiment import EvaluationResult, ExperimentManifest
from cairoflow_contracts.integrity import IntegrityCheck, IntegrityVerdict
from cairoflow_contracts.reasoning import EvidenceBundle, ReasoningResult, ValidationOutcome
from cairoflow_contracts.report import DecisionReport
from cairoflow_evaluation.eligibility import RULES_VERSION
from cairoflow_evaluation.integrity import metric_problems, verify_run
from cairoflow_evaluation.metrics import CALCULATION_VERSION
from cairoflow_evaluation.report import REPRODUCIBILITY_KEYS, assemble_report, trace_report
from cairoflow_simulation.network import content_sha256

REPO = Path(__file__).resolve().parents[1]
SCENARIO = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
EXIT = {"verified": 0, "failed": 1, "unverifiable": 3}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_run(digest: str) -> Path | None:
    """The recorded Muse run whose bundle.json hashes to `digest`."""
    for path in sorted((REPO / "experiments/runs").glob("ngsim-int2-experiment-*/bundle.json")):
        if sha256_file(path) == digest:
            return path.parent
    return None


def verify_record(record_path: Path) -> IntegrityVerdict:
    record = json.loads(record_path.read_text(encoding="utf-8"))
    run_dir = REPO / record["run_dir"]
    checks: list[IntegrityCheck] = []
    problems: list[str] = []
    missing: list[str] = []

    def verdict(status: str) -> IntegrityVerdict:
        return IntegrityVerdict(status=status, run_dir=record["run_dir"], identity_sha256=None,
                                checked_at_utc=datetime.now(UTC), checks=tuple(checks),
                                missing=tuple(missing), problems=tuple(problems))  # fmt: skip

    if not run_dir.is_dir():
        missing.append(f"{record['run_dir']} (Git-ignored; run the experiment again)")
        return verdict("unverifiable")
    anchors = {"report.json": record["report"]["report_json_sha256"], **record["tripinfo"]}
    for name, digest in anchors.items():
        path = run_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        found = (
            sha256_file(path)
            if name == "report.json"
            else content_sha256(path, strip_comments=True)
        )
        if found != digest:
            problems.append(f"{name} does not hash as the committed record says")
    checks.append(IntegrityCheck(name="committed hashes", passed=not problems and not missing,
                                 detail=f"report.json and {len(record['tripinfo'])} tripinfo.xml "
                                 "against the record Git holds"))  # fmt: skip
    if missing:
        return verdict("failed" if problems else "unverifiable")
    report = DecisionReport.model_validate_json((run_dir / "report.json").read_text("utf-8"))
    experiment_dir = run_dir / "experiment"
    evaluation_path = experiment_dir / "evaluation.json"
    if sha256_file(evaluation_path) != report.reproducibility.get("experiment/evaluation.json"):
        problems.append("evaluation.json does not hash as the report lists it")
    evaluation = EvaluationResult.model_validate_json(evaluation_path.read_text("utf-8"))
    found = metric_problems(evaluation, experiment_dir)
    problems += found
    checks.append(IntegrityCheck(name="metrics recomputed from SUMO outputs", passed=not found,
                                 detail=f"{len(evaluation.metrics)} metrics: {len(found)} "
                                 "differ"))  # fmt: skip
    if (evaluation.rules_version, evaluation.calculation_version) != (
        RULES_VERSION, CALCULATION_VERSION
    ):  # fmt: skip
        checks.append(IntegrityCheck(
            name="report rebuilt", passed=False,
            detail=f"evaluated under rules {evaluation.rules_version}; this code implements "
            f"{RULES_VERSION}, so the comparisons and the selection are not re-derived",
        ))  # fmt: skip
        return verdict("failed" if problems else "unverifiable")

    source = _source_run(report.reproducibility.get("source/bundle.json", ""))
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    world_path = REPO / scenario["processed_outputs"]["dir"] / "world_model.json"
    if source is None or not world_path.is_file():
        missing.append("the source Muse run or the world model (Git-ignored)")
        return verdict("failed" if problems else "unverifiable")
    world = TrafficWorldModel.model_validate_json(world_path.read_text("utf-8"))
    bundle = EvidenceBundle.model_validate_json((source / "bundle.json").read_text("utf-8"))
    reasoning = ReasoningResult.model_validate_json((source / "reasoning.json").read_text("utf-8"))
    outcomes = TypeAdapter(tuple[ValidationOutcome, ...]).validate_json(
        (source / "outcomes.json").read_text("utf-8")
    )
    experiment = ExperimentManifest.model_validate_json(
        (experiment_dir / "experiment_manifest.json").read_text("utf-8")
    )
    # The script's own limitation lines sit after the first two; report.json is anchored by the
    # committed hash above, so reading them from it reads the record, not an unchecked copy.
    plain = assemble_report(
        world_model=world, bundle=bundle, reasoning=reasoning, outcomes=outcomes,
        experiment=experiment, evaluation=evaluation, now=report.created_at_utc,
        artifacts={k: v for k, v in report.reproducibility.items()
                   if k not in REPRODUCIBILITY_KEYS},
        baseline_assessment=report.baseline_assessment,
    )  # fmt: skip
    extra = report.limitations[2 : 2 + len(report.limitations) - len(plain.limitations)]
    traced = trace_report(report, world_model=world, outcomes=outcomes, experiment=experiment,
                          evaluation=evaluation, experiment_dir=experiment_dir, bundle=bundle,
                          reasoning=reasoning, extra_limitations=extra,
                          evidence_root=REPO)  # fmt: skip
    problems += traced
    checks.append(IntegrityCheck(name="recomputed evaluation and rebuilt report",
                                 passed=not traced, detail=f"{len(traced)} problems"))  # fmt: skip
    return verdict("failed" if problems else "verified")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("run_dir", nargs="?", type=Path, help="a run with integrity.json")
    target.add_argument("--record", type=Path, help="a committed realism-experiment record")
    parser.add_argument("--rerun-seeds", type=int, default=0, help="run SUMO again for N seeds")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    args = parser.parse_args(argv)
    if args.record:
        verdict = verify_record(args.record.resolve())
    else:
        verdict = verify_run(args.run_dir.resolve(), rerun_seeds=args.rerun_seeds,
                             evidence_root=REPO)  # fmt: skip
    if args.json:
        print(verdict.model_dump_json(indent=2))
    else:
        print(f"{verdict.status.upper()}: {verdict.run_dir}")
        for check in verdict.checks:
            print(f"  [{'pass' if check.passed else 'FAIL'}] {check.name}: {check.detail}")
        for item in verdict.missing:
            print(f"  missing: {item}")
        for problem in verdict.problems[:40]:
            print(f"  problem: {problem}")
    return EXIT[verdict.status]


if __name__ == "__main__":
    sys.exit(main())
