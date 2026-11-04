"""CairoFlow end to end in one command (P1-4): perception, world model, reasoning, validated
planning, a controlled SUMO experiment and a verified decision report.

    uv run --frozen python scripts/run_cairoflow.py                     # Muse from the cache
    uv run --frozen python scripts/run_cairoflow.py --muse live --spend-limit 0.06 --replicate 1
    uv run --frozen python scripts/run_cairoflow.py ... --record

The stages reuse the existing modules, and each is recorded in pipeline_stages.json with the kind
of data it handled: recorded real API output, replayed published dataset, model output, code,
simulated. None is synthetic or mocked.
 1 perception: the recorded SAM 3.1 run on camera 2 and the committed result of the
   pre-registered camera-to-world-model gate that judged it (P1-1; by default gate v3, go,
   scripts/build_camera_world_model_v3.py; --camera-record and --sam-record pick another);
 2 world model: built from the published NGSIM trajectories; the camera layer is attached only
   when that gate is go, and the run says so either way;
 3 world-model checks: frame anchor, provenance, inputs and the camera rule;
 4 evidence bundle, 5 Muse Spark (prompt 0.3.1; --prompt-version 0.3.0 rebuilds the requests of
   the runs recorded under it; store false: replayed from the cache, or one live request within
   --spend-limit), 6 grounding (P0-3), 7 validation (P1-3), so only grounded and valid proposals
   continue;
 8 SUMO: candidates on the replay baseline (ADR-022) with its 20 paired seeds, demand and settings;
 9 evaluation from the SUMO outputs, 10 the objective J (a summary that never selects),
 11 decision report, 12 identity and verification (P0-2: recomputation, rebuild, one rerun seed).
A missing input stops the run with the command that makes it; a failed Muse call, a reply
without a grounded proposal or a failed simulation is recorded and ends the run without a
recommendation. Output: experiments/runs/cairoflow-<stamp>/ (Git-ignored); with --record a
summary without model text in experiments/records/.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from cairoflow_contracts.config import load_settings
from cairoflow_contracts.evidence import CameraLayer, TrafficWorldModel
from cairoflow_contracts.experiment import BaselineManifest
from cairoflow_contracts.reasoning import ValidationOutcome
from cairoflow_evaluation.compare import evaluate_experiment
from cairoflow_evaluation.integrity import build_identity, verify_run, write_identity
from cairoflow_evaluation.objective import objective
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
from cairoflow_reasoning.grounding import ground
from cairoflow_reasoning.muse_adapter import MuseAdapterError, MuseCacheMiss, MuseClient
from cairoflow_simulation.candidates import CandidateError, load_baseline, run_candidates
from cairoflow_simulation.network import content_sha256
from cairoflow_simulation.replay_candidates import replay_program_for
from cairoflow_simulation.runner import MANIFEST_FILE, SimulationError
from cairoflow_simulation.validation import validate_proposals

REPO = Path(__file__).resolve().parents[1]
SCENARIO = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
ASSESSMENTS = REPO / "data/geometry/ngsim-lankershim-int2/replay_assessments"
REPLAY_BASELINE = REPO / "experiments/runs/ngsim-int2-realism-baseline-20261028T141919Z"
# Camera gate v3 (go, ADR-031) and the SAM run it judged; gates v1 and v2's records still run
# with --camera-record and --sam-record (their SAM runs: 20261028T151050Z, 20261030T220322Z).
SAM_RECORD = REPO / "experiments/records/ngsim-cam2-sam-20261030T230408Z.json"
CAMERA_GATE = REPO / "experiments/records/ngsim-cam2-world-model-v3-20261030T230506Z.json"
MUSE_CACHE = REPO / "cache/muse"
# The prompt version also picks the evidence limitations (evidence_limitations), so it is part of
# each recorded request. 0.3.0 rebuilds the four runs recorded under it; new runs send 0.3.1.
PROMPT_VERSION = "0.3.1"
PROMPT_VERSIONS = ("0.3.0", "0.3.1")
NGSIM_ITEMS_0_3_1 = (
    "Items other than cam.* come from published NGSIM vehicle trajectories (Lankershim "
    "Boulevard, 2005-06-16 08:30-08:45); they use no camera tracking and no camera calibration."
)
NGSIM_ONLY_0_3_1 = (
    "Every item comes from published NGSIM vehicle trajectories (Lankershim Boulevard, "
    "2005-06-16 08:30-08:45): the camera 2 lane placement did not pass its pre-registered gate, "
    "so no camera-derived evidence is included."
)
NGSIM_SOURCE = (
    "World model: published NGSIM vehicle trajectories, Lankershim Boulevard intersection 2, "
    "2005-06-16 08:30-08:45 (data.transportation.gov 8ect-6jqj, CC BY-SA 3.0), aggregated by "
    "cairoflow_perception.ngsim_junction: a replayed published dataset, not live data and "
    "not Cairo."
)
REPLAY_LIMITATIONS = (
    "The simulation runs on the replay baseline (ADR-022): its signals replay the trajectory-"
    "derived timeline v2 and its demand is the vehicles of the window (600 observed plus those "
    "the replay rules place); the bundle's note that SUMO repeats one 99 s cycle describes the "
    "cut cycle the proposals were validated against, not this simulation.",
    "Every seed has the same vehicles; seed spread is SUMO's microscopic noise for one observed "
    "realisation, not the uncertainty of the demand, so the intervals are conditional on it.",
    "Each candidate's change to the cut cycle is applied to every observed cycle with the same "
    "phase (cairoflow_simulation.replay_candidates).",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, text: str) -> str:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return sha256_file(path)


def _script_limitations() -> tuple[str, ...]:
    """The evidence limitations the first intersection 2 diagnosis used (one source of text)."""
    path = REPO / "scripts/run_ngsim_int2_experiment.py"
    spec = importlib.util.spec_from_file_location("run_ngsim_int2_experiment", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.LIMITATIONS


def _camera_limitations_0_3_1(layer: CameraLayer) -> tuple[str, ...]:
    """What camera items can and cannot show (investigation camera-to-muse-20261031)."""
    start, end = layer.window_s
    return (
        f"Camera-derived items (cam.*) come from one camera ({layer.sam_model} boxes placed "
        f"through its calibration) and cover only its field of view during world-model seconds "
        f"{start:g}-{end:g}: lane-level presence and speed, not intersection-wide traffic "
        "measurements. They may show only some of the vehicles and movements in view: SAM boxes "
        "only part of the vehicles, one vehicle can appear as two tracks or in several lanes, and "
        "a lane without a cam.* item had no tracked vehicle, whether it was out of view, empty or "
        "missed.",
        "Camera-derived items are not independent of the trajectory items: the camera's ground "
        "frame and lanes come from the same trajectories (ADR-017), its lane placement was "
        "checked against them, and it may show the same vehicles. Its calibration is not "
        "accepted for along-road distances (ADR-018): it gives no along-road positions, queue "
        "lengths or stop-line crossings, and its speeds, which rest on that calibration, were "
        "not checked by the camera gate.",
        "Whether a cam.* item bears on a hypothesis depends on whether its lanes and seconds "
        "overlap what the hypothesis is about; the presence of camera items does not mean they "
        "support any hypothesis.",
    )


def evidence_limitations(prompt_version: str, layer: CameraLayer | None) -> tuple[str, ...]:
    """The fixed limitations sent beside the evidence, chosen by prompt version, so a run
    recorded under a version rebuilds its request exactly.

    0.3.0 sends the first intersection 2 diagnosis' limitations and a camera note. Their first
    line says the evidence is not from camera tracking, which is false beside cam.* items; it
    stays as sent, so the runs recorded under 0.3.0 still replay. 0.3.1 corrects that line and
    says what camera items cannot show. Any other version is refused.
    """
    first, *shared = _script_limitations()
    if prompt_version == "0.3.0":
        camera_note = (
            "Camera-derived items (cam.*) cover world-model seconds "
            f"{layer.window_s[0]:g}-{layer.window_s[1]:g} of one camera: lane-level only (lane, "
            "presence, speed), and lower bounds, since SAM boxes about a quarter of the vehicles "
            "in view; along-road quantities are not derived from the camera (ADR-018).",
        ) if layer else (
            "No camera-derived evidence: the camera 2 lane placement did not pass its "
            "pre-registered gate, so every item comes from the published trajectories.",
        )  # fmt: skip
        return (first, *shared, *camera_note)
    if prompt_version == "0.3.1":
        if layer is None:
            return (NGSIM_ONLY_0_3_1, *shared)
        return (NGSIM_ITEMS_0_3_1, *shared, *_camera_limitations_0_3_1(layer))
    raise ValueError(f"no evidence limitations for prompt version {prompt_version!r}")


def perception_detail(sam: dict) -> str:
    """What the recorded SAM run was, read from its own record."""
    start, end = sam["clip"]["source_window_s"]
    requests = len(sam["requests"])
    return (f"SAM 3.1 on {end - start:g} s of NGSIM camera 2 (source seconds {start:g}-{end:g}; "
            f"{requests} live request{'s' if requests != 1 else ''} on "
            f"{sam['created_at_utc'][:10]}, replayed from their record): "
            f"{sam['tracking']['tracks']} tracks")  # fmt: skip


def layer_matches_sam(layer: CameraLayer, sam: dict) -> bool:
    """Whether a camera layer was built from this SAM run: its request keys and its clip."""
    keys = {r["request_key"] for r in sam["requests"]}
    return set(layer.sam_request_keys) <= keys and layer.camera_clip_id == sam["clip"]["clip_id"]


def missing_inputs(
    scenario: dict, baseline_dir: Path, camera_gate: Path, sam_record: Path
) -> list[str]:
    needs = {
        REPO / scenario["processed_outputs"]["dir"] / "world_model.json":
            "scripts/fetch_ngsim_data.py, then scripts/build_ngsim_int2_scenario.py",
        REPO / scenario["baseline"]["run_dir"] / MANIFEST_FILE:
            "scripts/build_ngsim_int2_scenario.py",
        baseline_dir / MANIFEST_FILE: "scripts/build_ngsim_int2_realism.py --timeline v2",
        sam_record: "the committed SAM record (git checkout)",
        camera_gate: "scripts/build_camera_world_model_v3.py --record (gates v1/v2: "
        "scripts/build_camera_world_model.py --record)",
    }  # fmt: skip
    return [f"{path.relative_to(REPO)} is missing; make it with {how}"
            for path, how in needs.items() if not path.is_file()]  # fmt: skip


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--muse", choices=("replay", "live"), default="replay")
    parser.add_argument("--spend-limit", type=float, default=0.0, help="USD, live only")
    parser.add_argument("--replicate", type=int, default=1, help="independent sample number")
    parser.add_argument("--baseline", type=Path, default=REPLAY_BASELINE)
    parser.add_argument(
        "--camera-record", type=Path, default=CAMERA_GATE, help="a camera gate record"
    )
    parser.add_argument("--sam-record", type=Path, default=SAM_RECORD,
                        help="the SAM record that gate judged")  # fmt: skip
    parser.add_argument("--rerun-seeds", type=int, default=1, help="SUMO reruns to verify")
    parser.add_argument("--max-output-tokens", type=int, default=16000,
                        help="Muse output cap, reasoning included; part of the cache key: the "
                        "camera-fed run used 16000, the three earlier runs 8000")  # fmt: skip
    parser.add_argument("--prompt-version", choices=PROMPT_VERSIONS, default=PROMPT_VERSION,
                        help="part of the cache key: 0.3.0 rebuilds the requests of the runs "
                        "recorded under it; 0.3.1 sends the corrected limitations")  # fmt: skip
    parser.add_argument("--record", action="store_true", help="write the committed summary")
    return parser


def main(argv: list[str] | None = None) -> int:  # noqa: C901, PLR0915 - one stage after another
    args = build_parser().parse_args(argv)
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    baseline_dir = args.baseline.resolve()
    camera_gate, sam_record = args.camera_record.resolve(), args.sam_record.resolve()
    missing = missing_inputs(scenario, baseline_dir, camera_gate, sam_record)
    if missing:
        print("cannot run:", *missing, sep="\n  ", file=sys.stderr)
        return 2
    now = datetime.now(UTC).replace(microsecond=0)
    out = REPO / "experiments/runs" / f"cairoflow-{now:%Y%m%dT%H%M%SZ}-r{args.replicate}"
    out.mkdir(parents=True)
    stages: list[dict] = []

    def stage(name: str, kind: str, detail: str, **hashes: str) -> None:
        stages.append({"stage": name, "kind": kind, "detail": detail, "sha256": hashes})

    # 1 perception: the recorded SAM run and the gate's committed result
    gate = json.loads(camera_gate.read_text(encoding="utf-8"))
    sam = json.loads(sam_record.read_text(encoding="utf-8"))
    stage(
        "perception",
        "recorded real API output",
        perception_detail(sam),
        sam_record=sha256_file(sam_record),
    )
    layer = None
    folder = camera_gate.with_suffix("")
    if gate["gate"]["outcome"] == "go":
        layer = CameraLayer.model_validate_json((folder / "camera_layer.json").read_text("utf-8"))
        if not layer_matches_sam(layer, sam):
            print(f"the camera layer of {camera_gate.name} was not built from {sam_record.name}",
                  file=sys.stderr)  # fmt: skip
            return 2
    stage("camera to world model", "code",
          f"pre-registered gate {gate['gate']['gate_id']}: {gate['gate']['outcome']}"
          + ("" if layer else " - no camera entity enters the world model ("
             + "; ".join(gate["gate"]["reasons"]) + ")"),
          gate_record=sha256_file(camera_gate))  # fmt: skip

    # 2-3 world model and its checks
    world_path = REPO / scenario["processed_outputs"]["dir"] / "world_model.json"
    if sha256_file(world_path) != scenario["processed_outputs"]["sha256"]["world_model.json"]:
        print(f"{world_path} does not match the scenario record", file=sys.stderr)
        return 2
    world = TrafficWorldModel.model_validate_json(world_path.read_text(encoding="utf-8"))
    if layer is not None:
        world = TrafficWorldModel.model_validate(world.model_dump() | {"camera": layer})
        write_new(out / "world_model.json", world.model_dump_json(indent=2) + "\n")
    else:
        shutil.copyfile(world_path, out / "world_model.json")
    checks = {
        "anchored on the published dataset": world.ground_source_sha256
        == scenario["input"]["sha256"],
        "provenance is approximated (aggregated by code)": world.provenance.kind == "approximated",
        "the camera layer, if any, passed its gate": world.camera is None
        or world.camera.gate.outcome == "go",
        "movements and a signal timeline are present": bool(world.movements)
        and world.signal_timeline is not None,
    }
    failed = [name for name, ok in checks.items() if not ok]
    stage("world model", "replayed published dataset" + (" + camera layer" if layer else ""),
          "checks: " + ("all pass" if not failed else "FAILED " + "; ".join(failed)),
          world_model=sha256_file(out / "world_model.json"))  # fmt: skip
    if failed:
        print("the world model fails its checks: " + "; ".join(failed), file=sys.stderr)
        return 2
    camera_source = (
        "Camera 2 (SAM 3.1 tracks through the camera calibration): in the world model as a "
        f"camera layer, gate {gate['gate']['gate_id']} go; lane-level only (lane, presence, "
        "speed), along-road quantities are not derived from the camera (ADR-018)."
        if layer else
        "Camera 2 (SAM 3.1, recorded real output): NOT in the world model; the pre-registered "
        f"lane gate {gate['gate']['gate_id']} was no-go ({'; '.join(gate['gate']['reasons'])})."
    )  # fmt: skip
    data_sources = (NGSIM_SOURCE, camera_source)

    # 4-7 bundle, Muse, grounding, validation
    cut, network = load_baseline(REPO / scenario["baseline"]["run_dir"])
    bundle = build_evidence_bundle(
        world,
        network.manifest,
        evidence_limitations(args.prompt_version, layer),
        end_of_red_per_lane=True,
        conventions=ARM_CONVENTIONS,
    )
    write_new(out / "bundle.json", bundle.model_dump_json(indent=2) + "\n")
    stage("evidence bundle", "code", f"{len(bundle.items)} items, bundle {bundle.bundle_id}, "
          f"limitations of prompt {args.prompt_version}",
          bundle=sha256_file(out / "bundle.json"))  # fmt: skip
    settings = load_settings(REPO / ".env")
    client = MuseClient(settings, MUSE_CACHE, args.muse, spend_limit_usd=args.spend_limit,
                        max_output_tokens=args.max_output_tokens, store=False,
                        prompt_version=args.prompt_version,
                        replicate=args.replicate)  # fmt: skip
    try:
        reasoning = client.diagnose(bundle)
    except MuseCacheMiss:
        missing_reply = (f"no cached Muse reply for bundle {bundle.bundle_id}, prompt "
                         f"{args.prompt_version}, replicate {args.replicate}")  # fmt: skip
        stage("reasoning", "model output", f"NOT RUN: {missing_reply}; a live request is paid")
        write_new(out / "pipeline_stages.json", json.dumps(stages, indent=2) + "\n")
        print(f"{missing_reply}; run with --muse live --spend-limit USD (a paid request)",
              file=sys.stderr)  # fmt: skip
        return 3
    except MuseAdapterError as error:
        stage("reasoning", "model output", f"FAILED: {error}")
        write_new(out / "pipeline_stages.json", json.dumps(stages, indent=2) + "\n")
        print(f"Muse failed: {error}", file=sys.stderr)
        return 1
    write_new(out / "reasoning.json", reasoning.model_dump_json(indent=2) + "\n")
    stage(
        "reasoning",
        "model output (Muse Spark)",
        f"{reasoning.model}, prompt {reasoning.prompt_version}, replicate {args.replicate}, "
        f"{'replayed from the cache' if reasoning.provenance.cached else 'live'}; "
        f"{len(reasoning.output.hypotheses)} hypotheses, "
        f"{len(reasoning.output.interventions)} proposals",
        reasoning=sha256_file(out / "reasoning.json"),
        request_key=reasoning.request_key,
    )
    grounding = ground(reasoning.output, bundle, reasoning.request_key)
    write_new(out / "grounding.json", grounding.model_dump_json(indent=2) + "\n")
    supported = sum(g.status == "supported" for g in grounding.hypotheses)
    eligible = sum(p.eligible for p in grounding.proposals)
    stage("grounding", "code", f"{supported} of {len(grounding.hypotheses)} hypotheses supported; "
          f"{eligible} of {len(grounding.proposals)} proposals eligible for planning",
          grounding=sha256_file(out / "grounding.json"))  # fmt: skip
    outcomes = validate_proposals(reasoning.output.interventions, network, bundle, now=now,
                                  grounding=grounding)  # fmt: skip
    write_new(out / "outcomes.json",
              TypeAdapter(tuple[ValidationOutcome, ...]).dump_json(outcomes, indent=2).decode()
              + "\n")  # fmt: skip
    accepted = [o.intervention_id for o in outcomes if o.status == "accepted"]
    stage(
        "validation",
        "code",
        f"validator {outcomes[0].validator_version}: accepted {', '.join(accepted) or 'none'}",
        outcomes=sha256_file(out / "outcomes.json"),
    )

    # 8-9 SUMO and evaluation
    try:
        experiment = run_candidates(
            baseline_dir,
            outcomes,
            out / "experiment",
            now=now,
            program_for=replay_program_for(cut.network.signal_program),
        )
    except (CandidateError, SimulationError) as error:
        stage("simulation", "simulated", f"NOT RUN: {error}")
        write_new(out / "pipeline_stages.json", json.dumps(stages, indent=2) + "\n")
        print(f"no simulation: {error}; nothing is recommended")
        return 0 if isinstance(error, CandidateError) else 1
    family = holm_family(out / "experiment")  # from the manifests, before any result is read
    evaluation = evaluate_experiment(out / "experiment")
    stage("simulation", "simulated (SUMO)",
          f"{len(experiment.candidates)} candidates x {len(experiment.settings.seeds)} paired "
          f"seeds on {baseline_dir.name}",
          experiment_manifest=sha256_file(out / "experiment/experiment_manifest.json"))  # fmt: skip
    stage("metrics", "simulated (SUMO output, computed by code)",
          f"{len(evaluation.metrics)} metrics, {len(evaluation.comparisons)} paired comparisons",
          evaluation=sha256_file(out / "experiment/evaluation.json"))  # fmt: skip
    summary_j = objective(out / "experiment", evaluation)
    write_new(out / "objective.json", json.dumps(summary_j, indent=2) + "\n")

    # 10-12 report, identity, verification
    baseline_sha = sha256_file(baseline_dir / MANIFEST_FILE)
    assessment = load_baseline_assessment(ASSESSMENTS / f"{baseline_dir.name}.json",
                                          baseline_sha, REPO)  # fmt: skip
    limitations = (*REPLAY_LIMITATIONS, summary_j["C"] + " (objective.json).")
    artifacts = {name: sha256_file(out / name) for name in
                 ("world_model.json", "bundle.json", "reasoning.json", "grounding.json",
                  "outcomes.json", "objective.json")}  # fmt: skip
    artifacts["baseline/baseline_manifest.json"] = baseline_sha
    artifacts["experiment/experiment_manifest.json"] = sha256_file(
        out / "experiment/experiment_manifest.json"
    )
    artifacts["experiment/evaluation.json"] = sha256_file(out / "experiment/evaluation.json")
    report = assemble_report(world_model=world, bundle=bundle, reasoning=reasoning,
                             outcomes=outcomes, experiment=experiment, evaluation=evaluation,
                             artifacts=dict(sorted(artifacts.items())), now=now,
                             baseline_assessment=assessment, extra_limitations=limitations,
                             grounding=grounding, data_sources=data_sources)  # fmt: skip
    problems = trace_report(report, world_model=world, outcomes=outcomes, experiment=experiment,
                            evaluation=evaluation, experiment_dir=out / "experiment",
                            bundle=bundle, reasoning=reasoning, extra_limitations=limitations,
                            grounding=grounding, data_sources=data_sources,
                            evidence_root=REPO)  # fmt: skip
    json_path, md_path = write_report(report, out)
    stage("decision report", "code", f"selected {report.selected_intervention_id or 'none'}; "
          f"recommended {report.recommended_intervention_id or 'none'}; trace problems "
          f"{len(problems)}", report=sha256_file(json_path))  # fmt: skip
    identity = build_identity(
        out, experiment_dir=out / "experiment",
        inputs={name: out / f"{name}.json" for name in
                ("world_model", "bundle", "reasoning", "grounding", "outcomes")},
        report_files={"report_json": json_path, "report_md": md_path,
                      "objective": out / "objective.json"},
        extra_limitations=limitations, data_sources=data_sources,
    )  # fmt: skip
    write_identity(out, identity)
    verdict = verify_run(out, rerun_seeds=args.rerun_seeds, evidence_root=REPO)
    write_new(out / "integrity_verdict.json", verdict.model_dump_json(indent=2) + "\n")
    stage("verification", "code", f"{verdict.status}: " + "; ".join(
        f"{c.name} {'pass' if c.passed else 'FAIL'}" for c in verdict.checks),
        identity=identity.identity_sha256)  # fmt: skip
    write_new(out / "pipeline_stages.json", json.dumps(stages, indent=2) + "\n")
    if not problems and verdict.status == "verified":  # the app lists only verified runs
        record = PipelineRecord(
            run_name=out.name, mode="live" if not reasoning.provenance.cached else "replay",
            as_of_utc=now, inputs_sha256=sha256_file(SCENARIO),
            artifacts=dict(sorted(artifacts.items())),
            tripinfo={p.relative_to(out).as_posix(): content_sha256(p, strip_comments=True)
                      for p in sorted((out / "experiment").rglob("tripinfo.xml"))},
            clip_attribution=f"{scenario['input']['attribution']}; {scenario['input']['licence']}",
            notes=tuple(f"{s['stage']}: {s['kind']} - {s['detail']}" for s in stages),
            selected_intervention_id=report.selected_intervention_id,
            data_source=" ".join(data_sources),
        )  # fmt: skip
        write_new(out / RECORD_FILE, record.model_dump_json(indent=2) + "\n")
    if args.record:
        baseline = BaselineManifest.model_validate_json((baseline_dir / MANIFEST_FILE).read_text())
        summary = {
            "script": "scripts/run_cairoflow.py", "created_at_utc": now.isoformat(),
            "run_dir": str(out.relative_to(REPO)), "stages": stages,
            "data_sources": list(data_sources), "baseline": baseline.experiment_id,
            "muse": {"model": reasoning.model, "prompt_version": reasoning.prompt_version,
                     "replicate": args.replicate, "request_key": reasoning.request_key,
                     "request_id": reasoning.request_id, "cached": reasoning.provenance.cached,
                     "usage": reasoning.usage, "cost_usd": reasoning.estimated_cost_usd,
                     "text": "model text stays in the run directory and the cache"},
            "grounding": {"hypotheses": [g.model_dump(mode="json") for g in grounding.hypotheses],
                          "proposals": [p.model_dump(mode="json") for p in grounding.proposals],
                          "limiting_factors": [h.limiting_factor for h in
                                               reasoning.output.hypotheses]},
            "validation": [o.model_dump(mode="json") for o in outcomes],
            "eligibility": [e.model_dump(mode="json") for e in evaluation.eligibility],
            **comparison_record(out / "experiment", evaluation, family=family),
            "objective": summary_j,
            "report": {"selected": report.selected_intervention_id,
                       "selection_reason": report.selection_reason,
                       "recommended": report.recommended_intervention_id,
                       "recommendation": report.recommendation,
                       "report_json_sha256": sha256_file(json_path),
                       "trace_problems": list(problems)},
            "integrity": {"identity_sha256": identity.identity_sha256, "status": verdict.status,
                          "checks": [c.model_dump(mode="json") for c in verdict.checks],
                          "problems": list(verdict.problems)},
        }  # fmt: skip
        write_new(REPO / "experiments/records" / f"{out.name}.json",
                  json.dumps(summary, indent=2) + "\n")  # fmt: skip
    print(f"wrote {out.relative_to(REPO)}")
    for s in stages:
        print(f"  {s['stage']:<22} [{s['kind']}] {s['detail']}")
    return 0 if not problems and verdict.status == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
