"""Simulate the recorded, validated candidates on the replay baseline (ADR-022); no model call.

    uv run --frozen python scripts/run_ngsim_int2_realism_experiment.py REALISM_BASELINE [--record]

REALISM_BASELINE is a directory written by scripts/build_ngsim_int2_realism.py. The candidates are
the three outcomes the validator accepted on 2026-10-28 (the recorded run's outcomes.json, with
its bundle and reasoning, each checked against the hash its report.json lists). They were
validated against the recorded cut cycle, so each change is carried onto every observed cycle of
the replay by phase identity (`cairoflow_simulation.replay_candidates`). No new candidate is
generated and no Muse client is built.

The evaluation applies rules 0.2.0 (ADR-021) on the baseline's 20 pre-registered seeds. A replay
baseline is assessed by `replay_assessments/<baseline directory name>.json` beside the scenario
record, checked against the baseline's manifest and its evidence hashes; without one it counts as
not assessed. Only a validated baseline lets a selection become a recommendation. Output: a new
directory
experiments/runs/ngsim-int2-realism-experiment-<stamp>/ (Git-ignored) with experiment/,
evaluation, report, summary.json and pipeline_record.json; with `--record`, the summary also goes
to experiments/records/, without model-written text.
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
from cairoflow_contracts.experiment import BaselineManifest
from cairoflow_contracts.reasoning import EvidenceBundle, ReasoningResult, ValidationOutcome
from cairoflow_contracts.report import DecisionReport
from cairoflow_evaluation.compare import evaluate_experiment
from cairoflow_evaluation.pipeline import RECORD_FILE, PipelineRecord
from cairoflow_evaluation.report import (
    assemble_report,
    comparison_record,
    holm_family,
    load_baseline_assessment,
    trace_report,
    write_report,
)
from cairoflow_simulation.candidates import run_candidates
from cairoflow_simulation.network import content_sha256
from cairoflow_simulation.replay_candidates import replay_program_for
from cairoflow_simulation.runner import MANIFEST_FILE

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
RECORDS_DIR = REPO / "experiments/records"
RECORDED_RUN = REPO / "experiments/runs/ngsim-int2-experiment-20261028T084336Z"
ASSESSMENTS = REPO / "data/geometry/ngsim-lankershim-int2/replay_assessments"
CHECKED = ("bundle.json", "reasoning.json", "outcomes.json")
DATA_SOURCE = (
    "Published NGSIM vehicle trajectories: Lankershim Boulevard, intersection 2, 2005-06-16 "
    "08:30-08:45 (data.transportation.gov dataset 8ect-6jqj). No camera input."
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, text: str) -> str:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assessment_path(baseline_dir: Path) -> Path:
    return ASSESSMENTS / f"{baseline_dir.name}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("baseline", type=Path, help="a realism baseline directory")
    parser.add_argument("--record", action="store_true", help="write the committed summary")
    parser.add_argument("--source-run", type=Path, default=RECORDED_RUN,
                        help="the Muse run whose bundle, reply and outcomes are simulated "
                        "(default: the recorded replay of 2026-10-28)")  # fmt: skip
    args = parser.parse_args(argv)
    baseline_dir = args.baseline.resolve()
    source = args.source_run.resolve()
    scenario = json.loads(RECORD.read_text(encoding="utf-8"))
    needed = [source / name for name in ("report.json", *CHECKED)] + [baseline_dir / MANIFEST_FILE]
    missing = [p for p in needed if not p.is_file()]
    if missing:  # Git-ignored run directories: rebuild them, or name others; none is invented
        print(
            "missing inputs: " + ", ".join(str(p) for p in missing) + "; replay the source run "
            "with scripts/run_ngsim_int2_experiment.py --prompt-version 0.1.0 "
            "--max-output-tokens 7000 and build the baseline with "
            "scripts/build_ngsim_int2_realism.py, or pass --source-run",
            file=sys.stderr,
        )
        return 2

    recorded = DecisionReport.model_validate_json((source / "report.json").read_text())
    for name in CHECKED:
        if recorded.reproducibility.get(name) != sha256_file(source / name):
            print(f"{source / name} does not match its report.json", file=sys.stderr)
            return 2
    cut_dir = REPO / scenario["baseline"]["run_dir"]
    if sha256_file(cut_dir / MANIFEST_FILE) != scenario["baseline"]["manifest_sha256"]:
        print(f"{cut_dir} does not match the scenario record", file=sys.stderr)
        return 2
    cut = BaselineManifest.model_validate_json((cut_dir / MANIFEST_FILE).read_text())
    replay = BaselineManifest.model_validate_json((baseline_dir / MANIFEST_FILE).read_text())
    params = {p.name: p.value for p in replay.network.parameters}
    if "signal.replay_start" not in params or not replay.valid:
        print(f"{baseline_dir} is not a valid replay baseline", file=sys.stderr)
        return 2
    world_path = REPO / scenario["processed_outputs"]["dir"] / "world_model.json"
    if sha256_file(world_path) != scenario["processed_outputs"]["sha256"]["world_model.json"]:
        print(f"{world_path} does not match the scenario record", file=sys.stderr)
        return 2
    world = TrafficWorldModel.model_validate_json(world_path.read_text(encoding="utf-8"))
    assessed = assessment_path(baseline_dir)
    try:
        assessment = load_baseline_assessment(
            assessed, sha256_file(baseline_dir / MANIFEST_FILE), REPO
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    bundle = EvidenceBundle.model_validate_json((source / "bundle.json").read_text())
    reasoning = ReasoningResult.model_validate_json((source / "reasoning.json").read_text())
    outcomes = TypeAdapter(tuple[ValidationOutcome, ...]).validate_json(
        (source / "outcomes.json").read_text()
    )

    now = datetime.now(UTC).replace(microsecond=0)
    out = REPO / "experiments/runs" / f"ngsim-int2-realism-experiment-{now:%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True)
    program_for = replay_program_for(cut.network.signal_program)
    experiment = run_candidates(
        baseline_dir, outcomes, out / "experiment", now=now, program_for=program_for
    )
    family = holm_family(out / "experiment")  # from the manifests, before any result is read
    evaluation = evaluate_experiment(out / "experiment")
    window = (params["signal.replay_start"], params["signal.replay_end"])
    limitations = (
        f"This simulation runs on the replay baseline (ADR-022): the signals replay the "
        f"trajectory-derived timeline over clip [{window[0]:g}, {window[1]:g}) s and the demand is "
        "the observed vehicles; the bundle's note that SUMO repeats one 99 s cycle describes the "
        "diagnosis, not this simulation.",
        "Every seed has the same vehicles; seed spread is the model's microscopic noise for one "
        "observed realisation, not the uncertainty of the demand.",
        "Each candidate's change to the cut cycle is applied to every observed cycle with the "
        "same phase; see the candidate network notes for how often and where it was clamped.",
    )
    artifacts = {f"source/{name}": recorded.reproducibility[name] for name in CHECKED}
    artifacts["baseline/baseline_manifest.json"] = sha256_file(baseline_dir / MANIFEST_FILE)
    artifacts["experiment/experiment_manifest.json"] = sha256_file(
        out / "experiment/experiment_manifest.json"
    )
    artifacts["experiment/evaluation.json"] = sha256_file(out / "experiment/evaluation.json")
    if assessment is not None:
        artifacts["baseline_assessment.json"] = sha256_file(assessed)
    report = assemble_report(world_model=world, bundle=bundle, reasoning=reasoning,
                             outcomes=outcomes, experiment=experiment, evaluation=evaluation,
                             artifacts=dict(sorted(artifacts.items())), now=now,
                             baseline_assessment=assessment,
                             extra_limitations=limitations)  # fmt: skip
    problems = trace_report(report, world_model=world, outcomes=outcomes, experiment=experiment,
                            evaluation=evaluation, experiment_dir=out / "experiment",
                            bundle=bundle, reasoning=reasoning, extra_limitations=limitations,
                            evidence_root=REPO)  # fmt: skip
    json_path, md_path = write_report(report, out)
    artifacts[json_path.name] = sha256_file(json_path)
    artifacts[md_path.name] = sha256_file(md_path)
    tripinfo = {
        path.relative_to(out).as_posix(): content_sha256(path, strip_comments=True)
        for path in sorted((out / "experiment").rglob("tripinfo.xml"))
    }
    if not problems:
        record = PipelineRecord(
            run_name=out.name, mode="replay", as_of_utc=now, inputs_sha256=sha256_file(RECORD),
            artifacts=dict(sorted(artifacts.items())), tripinfo=tripinfo,
            clip_attribution=f"{scenario['input']['attribution']}; {scenario['input']['licence']}",
            notes=(f"Recorded validated candidates on the replay baseline {baseline_dir.name} "
                   "(ADR-022); no model call.",),
            selected_intervention_id=report.selected_intervention_id, data_source=DATA_SOURCE,
        )  # fmt: skip
        write_new(out / RECORD_FILE, record.model_dump_json(indent=2) + "\n")
    summary = {
        "script": "scripts/run_ngsim_int2_realism_experiment.py",
        "created_at_utc": now.isoformat(),
        "run_dir": str(out.relative_to(REPO)),
        "label": "simulated",
        "baseline_dir": str(baseline_dir.relative_to(REPO)),
        "baseline_manifest_sha256": artifacts["baseline/baseline_manifest.json"],
        "replay_window_s": list(window),
        "seeds": list(experiment.settings.seeds),
        "source_hashes": {name: recorded.reproducibility[name] for name in CHECKED},
        "candidate_notes": {
            c.intervention_id: [n for n in c.network.notes if "on the replay" in n]
            for c in experiment.candidates
        },  # fmt: skip
        "eligibility": [e.model_dump(mode="json") for e in evaluation.eligibility],
        **comparison_record(out / "experiment", evaluation, family=family),
        "improvement_tests": [t.model_dump(mode="json") for t in evaluation.improvement_tests],
        "report": {
            "selected_intervention_id": report.selected_intervention_id,
            "selection_reason": report.selection_reason,
            "recommended_intervention_id": report.recommended_intervention_id,
            "recommendation": report.recommendation,
            "baseline_assessment": {
                "status": assessment.status if assessment else "not_assessed",
                "path": str(assessed.relative_to(REPO)) if assessment else None,
                "file_sha256": sha256_file(assessed) if assessment else None,
            },
            "trace_problems": list(problems),
            "report_json_sha256": artifacts[json_path.name],
        },
        "tripinfo": tripinfo,
    }
    text = json.dumps(summary, indent=2) + "\n"
    write_new(out / "summary.json", text)
    if args.record:
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)
        write_new(RECORDS_DIR / f"ngsim-int2-realism-experiment-{now:%Y%m%dT%H%M%SZ}.json", text)
    print(f"wrote {out.relative_to(REPO)}; trace problems: {len(problems)}")
    print(f"selected: {report.selected_intervention_id}. {report.selection_reason}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
