"""Build and verify an experiment's identity (P0-2): nothing a report says is taken on trust.

`build_identity` lists every file that defines or came out of an experiment, with its SHA-256,
and hashes the list (cairoflow_contracts.integrity). `verify_run` checks a run directory that
holds one, in three independent steps:

1. Every artifact is hashed again from disk. A missing one makes the verdict `unverifiable`; a
   changed one makes it `failed`.
2. The decision report is traced (report.trace_report): the evaluation is recomputed from the
   SUMO outputs, every number is resolved against that recomputation, and the report is
   assembled again from the recorded inputs and must match the stored one exactly.
3. Optionally, the first `rerun_seeds` seeds of the baseline and of every candidate are run again
   with the recorded network, demand, seed and settings, and their tripinfo.xml and queue.xml
   must hash as recorded: the simulator execution itself is reproduced.

Run directory layout (artifact paths are relative to it): world_model.json, bundle.json,
reasoning.json (optional), outcomes.json, report.json, report.md and integrity.json, and the
experiment directory, whose manifest names the baseline directory.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from cairoflow_contracts.evidence import TrafficWorldModel
from cairoflow_contracts.experiment import BaselineManifest, EvaluationResult, ExperimentManifest
from cairoflow_contracts.integrity import (
    ArtifactHash,
    ArtifactRole,
    ExperimentIdentity,
    IntegrityCheck,
    IntegrityVerdict,
)
from cairoflow_contracts.reasoning import (
    EvidenceBundle,
    GroundingReport,
    ReasoningResult,
    ValidationOutcome,
)
from cairoflow_contracts.report import DecisionReport
from cairoflow_evaluation.compare import EVALUATION_FILE, EXPERIMENT_FILE
from cairoflow_evaluation.metrics import MetricError, run_metrics
from cairoflow_evaluation.report import trace_report
from cairoflow_simulation.geometry_compiler import NET_FILE, CompiledNetwork
from cairoflow_simulation.network import content_sha256, read_links
from cairoflow_simulation.runner import MANIFEST_FILE, SimulationError, _sumo, run_seed

IDENTITY_FILE = "integrity.json"
REQUIRED_INPUTS = ("world_model", "bundle", "outcomes")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sumo_version() -> str:
    """The first line `sumo --version` prints: the binary that ran, not the wheel's metadata."""
    result = subprocess.run([str(_sumo()), "--version"], capture_output=True, text=True,
                            check=False)  # fmt: skip
    return next((line.strip() for line in result.stdout.splitlines() if line.strip()), "unknown")


def simulator_versions() -> dict[str, str]:
    return {"sumo": sumo_version(), "eclipse-sumo wheel": metadata.version("eclipse-sumo")}


def _relative(path: Path, run_dir: Path) -> str:
    return Path(os.path.relpath(path.resolve(), run_dir.resolve())).as_posix()


def _manifests(experiment_dir: Path) -> tuple[ExperimentManifest, Path, BaselineManifest]:
    experiment = ExperimentManifest.model_validate_json(
        (experiment_dir / EXPERIMENT_FILE).read_text(encoding="utf-8")
    )
    baseline_dir = (experiment_dir / experiment.baseline_dir).resolve()
    baseline = BaselineManifest.model_validate_json(
        (baseline_dir / MANIFEST_FILE).read_text(encoding="utf-8")
    )
    return experiment, baseline_dir, baseline


def experiment_artifacts(
    run_dir: Path,
    experiment_dir: Path,
    inputs: Mapping[str, Path],
    report_files: Mapping[str, Path],
) -> tuple[ArtifactHash, ...]:
    """Every file that defines or came out of the experiment in `experiment_dir`, hashed now."""
    experiment, baseline_dir, baseline = _manifests(experiment_dir)
    found: list[ArtifactHash] = []

    def add(name: str, path: Path, role: ArtifactRole, strip: bool = False) -> None:
        digest = content_sha256(path, strip_comments=True) if strip else _sha256_file(path)
        found.append(ArtifactHash(name=name, path=_relative(path, run_dir), role=role,
                                  sha256=digest, comments_stripped=strip))  # fmt: skip

    for name, path in inputs.items():
        add(name, path, "input")
    add("experiment_manifest", experiment_dir / EXPERIMENT_FILE, "config")
    add("baseline_manifest", baseline_dir / MANIFEST_FILE, "config")
    add("net:baseline", baseline_dir / "network" / NET_FILE, "config", strip=True)
    for file in baseline.demand.files:
        add(f"demand:seed_{file.seed}", baseline_dir / "demand" / file.file, "input")
    for run in baseline.runs:
        for kind in ("tripinfo", "queue"):
            add(f"{kind}:baseline:{run.seed}", baseline_dir / "runs" / run.run_dir / f"{kind}.xml",
                "simulator_output", strip=True)  # fmt: skip
    for record in experiment.candidates:
        iid = record.intervention_id
        candidate_dir = experiment_dir / "candidates" / iid
        add(f"net:{iid}", candidate_dir / "network" / NET_FILE, "config", strip=True)
        for run in record.runs:
            run_dir_path = candidate_dir / "runs" / run.run_dir
            for kind in ("tripinfo", "queue"):
                add(f"{kind}:{iid}:{run.seed}", run_dir_path / f"{kind}.xml", "simulator_output",
                    strip=True)  # fmt: skip
    add("evaluation", experiment_dir / EVALUATION_FILE, "metrics")
    for name, path in report_files.items():
        add(name, path, "report")
    return tuple(found)


def build_identity(
    run_dir: Path,
    *,
    experiment_dir: Path,
    inputs: Mapping[str, Path],
    report_files: Mapping[str, Path],
    extra_limitations: Sequence[str] = (),
    data_sources: Sequence[str] = (),
    simulator: Mapping[str, str] | None = None,
) -> ExperimentIdentity:
    """The identity of a finished run; `inputs` must name world_model, bundle and outcomes (and
    reasoning, when the report was assembled with one), `report_files` report_json at least."""
    if not set(REQUIRED_INPUTS) <= set(inputs) or "report_json" not in report_files:
        raise ValueError(f"inputs need {', '.join(REQUIRED_INPUTS)} and report_files report_json")
    experiment, _, _ = _manifests(experiment_dir)
    report = DecisionReport.model_validate_json(report_files["report_json"].read_text("utf-8"))
    fields = {
        "experiment_id": experiment.experiment_id,
        "report_id": report.report_id,
        "settings": experiment.settings.model_dump(mode="json"),
        "simulator": dict(simulator or simulator_versions()),
        "extra_limitations": list(extra_limitations),
        "data_sources": list(data_sources),
        "artifacts": [
            a.model_dump(mode="json")
            for a in experiment_artifacts(run_dir, experiment_dir, inputs, report_files)
        ],
        "identity_version": "0.1.0",
        "schema_version": "0.1.0",
    }
    return ExperimentIdentity.model_validate(
        fields | {"identity_sha256": ExperimentIdentity.digest_of(fields)}
    )


def write_identity(run_dir: Path, identity: ExperimentIdentity) -> Path:
    path = run_dir / IDENTITY_FILE
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(identity.model_dump_json(indent=2) + "\n")
    return path


def verify_artifacts(identity: ExperimentIdentity, run_dir: Path) -> tuple[list[str], list[str]]:
    """(changed, missing): every artifact hashed again from disk."""
    changed: list[str] = []
    missing: list[str] = []
    for artifact in identity.artifacts:
        path = run_dir / artifact.path
        if not path.is_file():
            missing.append(f"{artifact.name} ({artifact.path})")
            continue
        digest = (content_sha256(path, strip_comments=True) if artifact.comments_stripped
                  else _sha256_file(path))  # fmt: skip
        if digest != artifact.sha256:
            changed.append(f"{artifact.name} ({artifact.path}) changed after the run")
    return changed, missing


def metric_problems(evaluation: EvaluationResult, experiment_dir: Path) -> list[str]:
    """Every stored metric compared with its value recomputed from the SUMO file it cites, by the
    calculation alone (metrics.run_metrics): this holds whatever eligibility rules the record was
    evaluated under, so it can check records made before the current rules."""
    experiment, _, baseline = _manifests(experiment_dir)
    arms = [p.name.split(".", 1)[0] for p in baseline.network.parameters
            if p.name.endswith(".approach_lanes")]  # fmt: skip
    root = experiment_dir.resolve()
    by_run: dict[tuple[Path, str, int], list] = {}
    for m in evaluation.metrics:
        by_run.setdefault(((root / m.source_output_file).parent, m.arm, m.seed), []).append(m)
    problems: list[str] = []
    for (run_dir, arm, seed), stored in sorted(by_run.items(), key=lambda item: str(item[0])):
        try:
            fresh = {m.metric_name: m for m in run_metrics(
                run_dir, experiment_id=evaluation.experiment_id, arm=arm, seed=seed,
                settings=experiment.settings, approach_arms=arms, root=root,
            )}  # fmt: skip
        except MetricError as error:
            problems.append(f"{arm} seed {seed}: cannot recompute: {error}")
            continue
        for m in stored:
            if fresh.get(m.metric_name) != m:
                found = fresh.get(m.metric_name)
                problems.append(f"{arm} seed {seed} {m.metric_name}: stored {m.value}, recomputed "
                                f"{found.value if found else 'nothing'}")  # fmt: skip
    return problems


def rerun_check(experiment_dir: Path, seeds: int, scratch: Path) -> list[str]:
    """Run the first `seeds` seeds of the baseline and of every candidate again, with their
    recorded networks, demand files and settings, into `scratch`; any tripinfo.xml or queue.xml
    that does not hash as recorded is a problem."""
    experiment, baseline_dir, baseline = _manifests(experiment_dir)
    demand = {f.seed: baseline_dir / "demand" / f.file for f in baseline.demand.files}
    arms = [("baseline", baseline_dir / "network" / NET_FILE, baseline.network, baseline.runs,
             baseline_dir / "runs")]  # fmt: skip
    for record in experiment.candidates:
        candidate_dir = experiment_dir / "candidates" / record.intervention_id
        arms.append((record.intervention_id, candidate_dir / "network" / NET_FILE, record.network,
                     record.runs, candidate_dir / "runs"))  # fmt: skip
    problems: list[str] = []
    for arm, net_path, manifest, runs, runs_dir in arms:
        network = CompiledNetwork(net_path=net_path, links=tuple(read_links(net_path)),
                                  manifest=manifest)  # fmt: skip
        for run in sorted(runs, key=lambda r: r.seed)[:seeds]:
            out = scratch / arm / f"seed_{run.seed}"
            try:
                run_seed(network, demand[run.seed], run.seed, experiment.settings, out)
            except (SimulationError, OSError, KeyError) as error:
                problems.append(f"{arm} seed {run.seed}: the rerun failed: {error}")
                continue
            for kind in ("tripinfo", "queue"):
                recorded = runs_dir / run.run_dir / f"{kind}.xml"
                same = recorded.is_file() and content_sha256(
                    recorded, strip_comments=True
                ) == content_sha256(out / f"{kind}.xml", strip_comments=True)
                if not same:
                    problems.append(f"{arm} seed {run.seed}: the rerun's {kind}.xml differs from "
                                    "the recorded one")  # fmt: skip
    return problems


def verify_run(
    run_dir: Path,
    *,
    rerun_seeds: int = 0,
    evidence_root: Path | None = None,
    now: datetime | None = None,
) -> IntegrityVerdict:
    """The integrity verdict on one run directory (see the module notes)."""
    checks: list[IntegrityCheck] = []
    problems: list[str] = []

    def verdict(status, identity: ExperimentIdentity | None, missing=()) -> IntegrityVerdict:
        return IntegrityVerdict(
            status=status, run_dir=str(run_dir), checked_at_utc=now or datetime.now(UTC),
            identity_sha256=identity.identity_sha256 if identity else None, checks=tuple(checks),
            missing=tuple(missing), problems=tuple(problems),
        )  # fmt: skip

    try:
        identity = ExperimentIdentity.model_validate_json(
            (run_dir / IDENTITY_FILE).read_text(encoding="utf-8")
        )
    except FileNotFoundError:
        return verdict("unverifiable", None, (IDENTITY_FILE,))
    except ValidationError as error:
        problems.append(f"{IDENTITY_FILE} is not a valid identity: {error.errors()[0]['msg']}")
        return verdict("failed", None)
    changed, missing = verify_artifacts(identity, run_dir)
    problems += changed
    checks.append(IntegrityCheck(
        name="artifact hashes", passed=not changed and not missing,
        detail=f"{len(identity.artifacts)} artifacts: {len(changed)} changed, "
        f"{len(missing)} missing",
    ))  # fmt: skip
    if missing:
        return verdict("failed" if changed else "unverifiable", identity, missing)

    paths = {a.name: run_dir / a.path for a in identity.artifacts}
    try:
        world = TrafficWorldModel.model_validate_json(paths["world_model"].read_text("utf-8"))
        bundle = EvidenceBundle.model_validate_json(paths["bundle"].read_text("utf-8"))
        reasoning = (ReasoningResult.model_validate_json(paths["reasoning"].read_text("utf-8"))
                     if "reasoning" in paths else None)  # fmt: skip
        outcomes = TypeAdapter(tuple[ValidationOutcome, ...]).validate_json(
            paths["outcomes"].read_text("utf-8")
        )
        report = DecisionReport.model_validate_json(paths["report_json"].read_text("utf-8"))
        evaluation = EvaluationResult.model_validate_json(paths["evaluation"].read_text("utf-8"))
        experiment_dir = paths["experiment_manifest"].parent
        experiment = ExperimentManifest.model_validate_json(
            paths["experiment_manifest"].read_text("utf-8")
        )
    except (KeyError, OSError, ValidationError) as error:
        problems.append(f"a recorded input cannot be read: {error}")
        return verdict("failed", identity)
    for label, ours, theirs in (
        ("report", identity.report_id, report.report_id),
        ("experiment", identity.experiment_id, experiment.experiment_id),
    ):
        if ours != theirs:
            problems.append(f"the identity names another {label} ({ours}, found {theirs})")
    if identity.settings != experiment.settings:
        problems.append("the experiment ran with other settings than the identity records")

    grounding = None
    if "grounding" in paths:  # recorded by the producer: the report must carry its verdict
        try:
            grounding = GroundingReport.model_validate_json(paths["grounding"].read_text("utf-8"))
        except (OSError, ValidationError) as error:
            problems.append(f"grounding.json cannot be read: {error}")
            return verdict("failed", identity)
    traced = trace_report(
        report, world_model=world, outcomes=outcomes, experiment=experiment,
        evaluation=evaluation, experiment_dir=experiment_dir, bundle=bundle, reasoning=reasoning,
        extra_limitations=identity.extra_limitations, grounding=grounding,
        data_sources=identity.data_sources, evidence_root=evidence_root,
    )  # fmt: skip
    problems += traced
    checks.append(IntegrityCheck(
        name="recomputed evaluation and rebuilt report", passed=not traced,
        detail="evaluation recomputed from every tripinfo.xml and queue.xml; report assembled "
        f"again from the recorded inputs: {len(traced)} problems",
    ))  # fmt: skip

    if rerun_seeds:
        current = sumo_version()
        if current != identity.simulator.get("sumo"):
            problems.append(f"a rerun would use {current!r}, the run used "
                            f"{identity.simulator.get('sumo')!r}")  # fmt: skip
        else:
            with tempfile.TemporaryDirectory(prefix="cairoflow-rerun-") as scratch:
                reran = rerun_check(experiment_dir, rerun_seeds, Path(scratch))
            problems += reran
            checks.append(IntegrityCheck(
                name="simulator rerun", passed=not reran,
                detail=f"first {rerun_seeds} seed(s) of the baseline and of every candidate run "
                f"again with {current}: {len(reran)} problems",
            ))  # fmt: skip
    return verdict("failed" if problems else "verified", identity)
