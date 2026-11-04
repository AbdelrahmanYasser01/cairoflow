"""Diagnose NGSIM intersection 2 with Muse Spark and simulate the validated interventions.

Inputs are the committed scenario record (DATA_REGISTER.md §16) and the processed world model
and SUMO baseline it names; every one is checked against the hash the record holds.

    uv run --frozen python scripts/run_ngsim_int2_experiment.py --dry-run
    uv run --frozen python scripts/run_ngsim_int2_experiment.py --live --spend-limit 0.05 --record
    uv run --frozen python scripts/run_ngsim_int2_experiment.py      # replay from the cache
    uv run --frozen python scripts/run_ngsim_int2_experiment.py --prompt-version 0.1.0 \
        --max-output-tokens 7000      # replay the 2026-10-28 record (prompt 0.1.0)

`--dry-run` builds the evidence bundle and prints the request size and the adapter's upper-bound
cost; nothing is sent. `--live` sends at most one request (MuseClient: cache first, no retries,
refused above the spend limit). Without `--live` the reply must already be cached (replay, no
connection).

The run writes `experiments/runs/ngsim-int2-experiment-<stamp>/` (Git-ignored): bundle.json,
reasoning.json, outcomes.json, experiment/ (SUMO candidates on the baseline's seeds and demand
files), experiment/evaluation.json, report.json and report.md, and, when every report number
traces (A9), pipeline_record.json, which lists the run in the read-only report app
(`python -m cairoflow_api.app experiments/runs`). With `--record`, a summary goes to
`experiments/records/` (committed). It holds ids, numbers, hashes and the validator's and
simulator's results, but no model-written text: that stays in the run directory and the cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from cairoflow_contracts.config import load_settings
from cairoflow_contracts.evidence import TrafficWorldModel
from cairoflow_contracts.reasoning import ValidationOutcome
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
from cairoflow_reasoning.evidence import ARM_CONVENTIONS, build_evidence_bundle
from cairoflow_reasoning.muse_adapter import MuseClient, request_key, upper_bound_cost_usd
from cairoflow_reasoning.prompts import PROMPT_VERSION, PROMPTS
from cairoflow_simulation.candidates import CandidateError, load_baseline, run_candidates
from cairoflow_simulation.network import content_sha256
from cairoflow_simulation.validation import validate_proposals

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
MUSE_CACHE = REPO / "cache/muse"
RECORDS_DIR = REPO / "experiments/records"
ASSESSMENT = REPO / "data/geometry/ngsim-lankershim-int2/baseline_assessment.json"
LIMITATIONS = (
    "The evidence comes from published NGSIM vehicle trajectories (Lankershim Boulevard, "
    "2005-06-16 08:30-08:45), not from camera tracking; no camera calibration is used.",
    "The signal timeline and program are estimated from stop-line discharges: green onsets "
    "and ends are approximated; yellow 4 s, start-up 2 s, clearance 1 s and minimum green 5 s "
    "are assumed. The real control appears actuated (onset spacing 80-201 s); the simulation "
    "repeats one observed 99 s cycle.",
    "Queue lengths marked 'at least' reach the end of the observed lane; cross-street queues "
    "are observable only near the limit line (east) or not at all (west).",
    "In the simulation right turns may go on red after stopping (assumed).",
)
# The prompt versions whose requests this runner builds. 0.3.1 names the corrected limitations of
# scripts/run_cairoflow.py (ADR-032); sent from here it would carry the camera-free ones above.
PROMPT_VERSIONS = ("0.1.0", "0.2.0", "0.3.0")
DATA_SOURCE = (
    "Published NGSIM vehicle trajectories: Lankershim Boulevard, intersection 2, 2005-06-16 "
    "08:30-08:45 (data.transportation.gov dataset 8ect-6jqj). No camera input."
)
RUN_NOTES = (
    "Camera 2 is not used: its calibration was not accepted (ADR-018), so the world model, "
    "signal timeline and SUMO baseline come from the trajectories (ADR-019).",
    "Hypotheses, rationales and trade-offs are Muse Spark text, labelled model-generated; every "
    "number is a world-model value, a validated input, a setting or a SUMO result.",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, text: str) -> str:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="build the request; send nothing")
    parser.add_argument("--live", action="store_true", help="send at most one Muse request")
    parser.add_argument("--spend-limit", type=float, default=0.0, help="USD, for --live")
    parser.add_argument(
        "--max-output-tokens", type=int, default=8000, help="reasoning and answer, together"
    )
    parser.add_argument("--record", action="store_true", help="write the committed summary")
    parser.add_argument("--dump-bundle", help="with --dry-run: write the bundle JSON here (new)")
    parser.add_argument(
        "--prompt-version",
        choices=sorted(PROMPTS),
        default=PROMPT_VERSION,
        help="0.1.0 rebuilds the request of the 2026-10-28 record (no arm conventions); "
        "0.3.1 is scripts/run_cairoflow.py's",
    )
    args = parser.parse_args(argv)
    if args.prompt_version not in PROMPT_VERSIONS:  # refused before anything is read or built
        parser.error(
            f"prompt {args.prompt_version} is not built by this runner: it names the corrected "
            "evidence limitations of scripts/run_cairoflow.py (ADR-032), and this runner would "
            "send the camera-free limitations of prompts 0.1.0-0.3.0 under it. Use "
            "scripts/run_cairoflow.py for prompt 0.3.1."
        )

    scenario = json.loads(RECORD.read_text(encoding="utf-8"))
    processed = REPO / scenario["processed_outputs"]["dir"]
    world_path = processed / "world_model.json"
    run_dir = REPO / scenario["baseline"]["run_dir"]
    missing = [p for p in (world_path, run_dir / "baseline_manifest.json") if not p.is_file()]
    if missing:  # Git-ignored outputs: a fresh clone rebuilds them; nothing is substituted
        print("missing inputs: " + ", ".join(str(p.relative_to(REPO)) for p in missing)
              + "; make them with scripts/fetch_ngsim_data.py (the trajectories), then "
              "scripts/build_ngsim_int2_scenario.py", file=sys.stderr)  # fmt: skip
        return 2
    if sha256_file(world_path) != scenario["processed_outputs"]["sha256"]["world_model.json"]:
        print(f"{world_path} does not match the record", file=sys.stderr)
        return 2
    if sha256_file(run_dir / "baseline_manifest.json") != scenario["baseline"]["manifest_sha256"]:
        print(f"{run_dir} does not match the record", file=sys.stderr)
        return 2
    world = TrafficWorldModel.model_validate_json(world_path.read_text(encoding="utf-8"))
    baseline, network = load_baseline(run_dir)
    try:
        assessment = load_baseline_assessment(
            ASSESSMENT, scenario["baseline"]["manifest_sha256"], REPO
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    bundle = build_evidence_bundle(
        world,
        network.manifest,
        LIMITATIONS,
        end_of_red_per_lane=True,
        conventions=() if args.prompt_version == "0.1.0" else ARM_CONVENTIONS,
    )

    settings = load_settings(REPO / ".env")
    mode = "live" if args.live else "replay"
    client = MuseClient(
        settings,
        MUSE_CACHE,
        mode,
        spend_limit_usd=args.spend_limit,
        max_output_tokens=args.max_output_tokens,
        prompt_version=args.prompt_version,
    )
    body = client.request_body(bundle)
    key = request_key(body)
    size = len(json.dumps(body).encode("utf-8"))
    print(
        f"bundle {bundle.bundle_id}: {len(bundle.items)} evidence items, {len(bundle.phases)} "
        f"phases; request {size} bytes, key {key[:16]}, model {settings.muse_model}; "
        f"upper-bound cost ${upper_bound_cost_usd(body):.4f}; cached: "
        f"{client.cache_path(key).is_file()}; API key configured: {settings.has_api_key}"
    )
    if args.dry_run:
        if args.dump_bundle:
            write_new(Path(args.dump_bundle), bundle.model_dump_json(indent=2))
        return 0

    now = datetime.now(UTC).replace(microsecond=0)
    out = REPO / "experiments/runs" / f"ngsim-int2-experiment-{now:%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True)
    artifacts: dict[str, str] = {
        "baseline/baseline_manifest.json": scenario["baseline"]["manifest_sha256"]
    }
    artifacts["bundle.json"] = write_new(out / "bundle.json", bundle.model_dump_json(indent=2))
    reasoning = client.diagnose(bundle)  # live: one request at most; replay: cache only
    artifacts["reasoning.json"] = write_new(
        out / "reasoning.json", reasoning.model_dump_json(indent=2)
    )
    outcomes = validate_proposals(reasoning.output.interventions, network, bundle, now=now)
    artifacts["outcomes.json"] = write_new(
        out / "outcomes.json",
        TypeAdapter(tuple[ValidationOutcome, ...]).dump_json(outcomes, indent=2).decode(),
    )
    summary: dict[str, object] = {
        "script": "scripts/run_ngsim_int2_experiment.py",
        "created_at_utc": now.isoformat(),
        "run_dir": str(out.relative_to(REPO)),
        "inputs": {
            "scenario_record_sha256": sha256_file(RECORD),
            "world_model_sha256": scenario["processed_outputs"]["sha256"]["world_model.json"],
            "baseline_manifest_sha256": scenario["baseline"]["manifest_sha256"],
            "baseline_experiment_id": baseline.experiment_id,
        },
        "evidence_bundle": {
            "bundle_id": bundle.bundle_id,
            "items": len(bundle.items),
            "phases": [
                {"phase": p.phase_name, "duration_s": p.duration_s, "adjustable": p.adjustable}
                for p in bundle.phases
            ],
            "extra_limitations": list(LIMITATIONS),
            "conventions": list(bundle.conventions),
        },
        "muse_request": {
            "label": "model_generated",
            "model": reasoning.model,
            "prompt_version": reasoning.prompt_version,
            "request_key": reasoning.request_key,
            "request_id": reasoning.request_id,
            "response_id": reasoning.response_id,
            "replayed_from_cache": reasoning.provenance.cached,
            "request_bytes": size,
            "upper_bound_cost_usd": round(upper_bound_cost_usd(body), 6),
            "usage": reasoning.usage,
            "cost_usd_at_published_rates": reasoning.estimated_cost_usd,
            "citation_problems": list(reasoning.problems),
            "text": "kept in reasoning.json (run directory) and the cache; not committed",
        },
        "hypotheses": [
            {
                "hypothesis_id": h.hypothesis_id,
                "confidence_label": h.confidence_label,
                "supporting_evidence_ids": list(h.supporting_evidence_ids),
                "contradicting_evidence_ids": list(h.contradicting_evidence_ids),
            }
            for h in reasoning.output.hypotheses
        ],
        "proposals": [
            {
                "intervention_id": p.intervention_id,
                "type": p.type,
                "target_program": p.target_program,
                "phase_changes": [
                    {"phase_name": c.phase_name, "green_s": c.green_s} for c in p.phase_changes
                ],
                "addresses_hypothesis_ids": list(p.addresses_hypothesis_ids),
                "evidence_ids": list(p.evidence_ids),
            }
            for p in reasoning.output.interventions
        ],
        "validation": [o.model_dump(mode="json") for o in outcomes],
    }
    try:
        experiment = run_candidates(run_dir, outcomes, out / "experiment", now=now)
    except CandidateError as error:
        summary["experiment"] = {"error": str(error)}
        _finish(summary, args.record, now)
        print(f"no simulation: {error}")
        return 1
    artifacts["experiment/experiment_manifest.json"] = sha256_file(
        out / "experiment/experiment_manifest.json"
    )
    family = holm_family(out / "experiment")  # from the manifests, before any result is read
    evaluation = evaluate_experiment(out / "experiment")
    artifacts["experiment/evaluation.json"] = sha256_file(out / "experiment/evaluation.json")
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
        experiment_dir=out / "experiment",
        bundle=bundle,
        reasoning=reasoning,
        evidence_root=REPO,
    )
    json_path, md_path = write_report(report, out)
    artifacts[json_path.name] = sha256_file(json_path)
    artifacts[md_path.name] = sha256_file(md_path)
    if not problems:  # like run_pipeline: the app shows only reports whose numbers trace
        record = PipelineRecord(
            run_name=out.name,
            mode=mode,
            as_of_utc=now,
            inputs_sha256=sha256_file(RECORD),
            artifacts=dict(sorted(artifacts.items())),
            tripinfo={
                path.relative_to(out).as_posix(): content_sha256(path, strip_comments=True)
                for path in sorted(out.rglob("tripinfo.xml"))
            },
            clip_attribution=f"{scenario['input']['attribution']}; {scenario['input']['licence']}",
            notes=RUN_NOTES,
            selected_intervention_id=report.selected_intervention_id,
            data_source=DATA_SOURCE,
        )
        write_new(out / RECORD_FILE, record.model_dump_json(indent=2) + "\n")
    summary["experiment"] = {
        "label": "simulated",
        "experiment_id": experiment.experiment_id,
        "valid": experiment.valid,
        "reasons": list(experiment.reasons),
        "skipped": list(experiment.skipped),
        "candidates": [
            {
                "intervention_id": c.intervention_id,
                "valid": c.valid,
                "cycle_s": c.network.signal_program.cycle_s,
                "runs": [
                    {
                        "seed": r.seed,
                        "loaded": r.loaded,
                        "arrived": r.arrived,
                        "teleports": r.teleports,
                        "collisions": r.collisions,
                        "valid": r.valid,
                        "tripinfo_sha256": r.tripinfo_sha256,
                    }
                    for r in c.runs
                ],
            }
            for c in experiment.candidates
        ],
        "manifest_sha256": artifacts["experiment/experiment_manifest.json"],
    }
    summary["evaluation"] = evaluation.model_dump(mode="json")
    summary.update(comparison_record(out / "experiment", evaluation, family=family))
    summary["report"] = {
        "selected_intervention_id": report.selected_intervention_id,
        "recommended_intervention_id": report.recommended_intervention_id,
        "baseline_assessment": {
            "status": assessment.status if assessment else "not_assessed",
            "file_sha256": sha256_file(ASSESSMENT) if assessment else None,
        },
        "trace_problems": list(problems),
        "report_json_sha256": sha256_file(json_path),
        "report_md_sha256": sha256_file(md_path),
        "text": "report.md holds model text, labelled; it stays in the run directory",
    }
    _finish(summary, args.record, now)
    print(json.dumps(summary["experiment"], indent=1)[:1500])
    print("selected:", report.selected_intervention_id, "trace problems:", len(problems))
    return 0 if not problems else 1


def _finish(summary: dict[str, object], record: bool, now: datetime) -> None:
    run_dir = REPO / str(summary["run_dir"])
    text = json.dumps(summary, indent=2) + "\n"
    write_new(run_dir / "summary.json", text)
    if record:
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)
        write_new(RECORDS_DIR / f"ngsim-int2-experiment-{now:%Y%m%dT%H%M%SZ}.json", text)


if __name__ == "__main__":
    raise SystemExit(main())
