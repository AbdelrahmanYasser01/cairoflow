"""The whole flow from recorded inputs to the decision report, and its offline replay (A10).

Stages, each writing one artifact into the run directory:

clip manifest -> SAM 3.1 (cache) -> observations.json -> tracks.json -> calibration_report.json
-> signals.json -> world_model.json -> baseline/ (SUMO) -> bundle.json -> Muse Spark (cache)
-> reasoning.json -> outcomes.json -> experiment/ (SUMO) -> experiment/evaluation.json
-> report.json, report.md -> pipeline_record.json

Every timestamp written is `as_of_utc` from the inputs, so a replay reproduces every artifact
byte for byte. `mode="replay"` never opens a connection: a missing SAM or Muse cache entry is an
error, and no API key is needed. The video itself is not needed once SAM output is cached; when
it is next to the clip manifest (and matches its hash), the run also renders overlay.mp4 for
review. Review artifacts are compared only when both runs have them.

`compare_records` checks a replay against a reference record, which must itself have been made in
replay mode (a live run records `cached: false` in the reasoning provenance, a replay `true`).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from cairoflow_contracts.config import Settings
from cairoflow_contracts.evidence import (
    CalibrationSpec,
    IntersectionGeometry,
    Observation,
    SignalLabel,
    SourceClip,
)
from cairoflow_contracts.experiment import RunSettings
from cairoflow_contracts.reasoning import ValidationOutcome
from cairoflow_evaluation.compare import evaluate_experiment
from cairoflow_evaluation.report import assemble_report, trace_report, write_report
from cairoflow_perception.calibration import fit_homography
from cairoflow_perception.ingest import sha256_file
from cairoflow_perception.overlay import render_overlay
from cairoflow_perception.sam_adapter import SamClient
from cairoflow_perception.signals import load_signal_annotation
from cairoflow_perception.tracking import build_tracks
from cairoflow_perception.world_model import build_world_model
from cairoflow_reasoning.evidence import build_evidence_bundle
from cairoflow_reasoning.muse_adapter import MuseClient
from cairoflow_simulation.candidates import load_baseline, run_candidates
from cairoflow_simulation.network import content_sha256
from cairoflow_simulation.runner import run_baseline
from cairoflow_simulation.validation import validate_proposals

RECORD_FILE = "pipeline_record.json"
CAMERA_SOURCE = "camera tracks: SAM 3.1 output mapped to the ground through the clip's calibration"
OVERLAY_FILE = "overlay.mp4"
OVERLAY_MANIFEST = "overlay.json"  # written by render_overlay next to the video
Mode = Literal["live", "replay"]


class PipelineError(RuntimeError):
    """A stage produced something the next one must not use."""


class PipelineInputs(BaseModel):
    """Everything a run needs; paths are relative to the inputs file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$")
    as_of_utc: datetime = Field(description="every timestamp the run writes")
    clip_manifest: str
    sam_prompts: tuple[str, ...] = Field(("car",), min_length=1)
    sam_cache_dir: str
    calibration_spec: str
    geometry: str
    signal_annotation: str
    signal_label: SignalLabel
    signal_source: str
    muse_cache_dir: str
    settings: RunSettings = RunSettings()


class PipelineRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_name: str
    mode: Mode
    as_of_utc: datetime
    inputs_sha256: str
    artifacts: dict[str, str] = Field(description="path in the run directory -> SHA-256")
    tripinfo: dict[str, str] = Field(description="every SUMO tripinfo.xml, comments stripped")
    review_artifacts: dict[str, str] = Field(
        default_factory=dict,
        description="overlay video and manifest; made only when the clip video is present",
    )
    clip_attribution: str = Field("", description="the licence attribution shown with pixels")
    clip_synthetic: bool = False
    notes: tuple[str, ...] = ()
    selected_intervention_id: str | None
    data_source: str = Field(
        CAMERA_SOURCE, description="where the world model's evidence comes from, for the reader"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_pipeline(
    inputs_path: Path,
    out_dir: Path,
    *,
    mode: Mode = "replay",
    settings: Settings | None = None,
    spend_limit_usd: float = 0.0,
    sam_transport: httpx.BaseTransport | None = None,
    muse_transport: httpx.BaseTransport | None = None,
) -> PipelineRecord:
    """Run every stage into `out_dir` (new) and record the hash of every artifact."""
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists; a run is never overwritten")
    base = inputs_path.parent
    inputs = PipelineInputs.model_validate_json(inputs_path.read_text(encoding="utf-8"))
    settings = settings or Settings()
    now = inputs.as_of_utc
    out_dir.mkdir(parents=True)
    artifacts: dict[str, str] = {}

    def write(name: str, text: str | bytes) -> None:
        data = text.encode("utf-8") if isinstance(text, str) else text
        with (out_dir / name).open("xb") as handle:
            handle.write(data + b"\n")
        artifacts[name] = _sha256(out_dir / name)

    clip_manifest = base / inputs.clip_manifest
    clip = SourceClip.model_validate_json(clip_manifest.read_text(encoding="utf-8"))
    sam = SamClient(
        settings,
        base / inputs.sam_cache_dir,
        mode,
        spend_limit_usd=spend_limit_usd,
        transport=sam_transport,
    )
    observations: list[Observation] = []
    for prompt in inputs.sam_prompts:
        parsed = sam.segment_and_parse(clip, clip_manifest.parent / clip.output_file, prompt)
        if parsed.outcome != "completed":
            raise PipelineError(f"SAM output for {prompt!r} is {parsed.outcome}")
        observations += parsed.observations
    write(
        "observations.json",
        TypeAdapter(tuple[Observation, ...]).dump_json(tuple(observations), indent=2),
    )
    tracking = build_tracks(observations, now=now)
    write("tracks.json", tracking.model_dump_json(indent=2))
    review: dict[str, str] = {}
    notes: list[str] = []
    clip_path = clip_manifest.parent / clip.output_file
    if not clip_path.is_file():
        notes.append(f"no overlay video: {clip.output_file} is not next to the clip manifest")
    elif sha256_file(clip_path) != clip.output_sha256:
        notes.append(f"no overlay video: {clip.output_file} does not match the clip manifest")
    else:
        render_overlay(clip, clip_path, tracking.tracks, out_dir / OVERLAY_FILE, now=now)
        for name in (OVERLAY_FILE, OVERLAY_MANIFEST):
            review[name] = _sha256(out_dir / name)

    spec = CalibrationSpec.model_validate_json((base / inputs.calibration_spec).read_text("utf-8"))
    _, calibration = fit_homography(spec, now=now)
    write("calibration_report.json", calibration.model_dump_json(indent=2))
    geometry = IntersectionGeometry.model_validate_json(
        (base / inputs.geometry).read_text(encoding="utf-8")
    )
    signals = load_signal_annotation(
        base / inputs.signal_annotation,
        clip,
        label=inputs.signal_label,
        source=inputs.signal_source,
    )
    write("signals.json", signals.model_dump_json(indent=2))
    world = build_world_model(
        clip, tracking.tracks, geometry, spec, calibration, signals=signals, now=now
    )
    write("world_model.json", world.model_dump_json(indent=2))

    baseline = run_baseline(
        world, geometry, signals, out_dir / "baseline", inputs.settings, now=now
    )
    if not baseline.valid:
        raise PipelineError("the baseline is not valid: " + "; ".join(baseline.reasons))
    artifacts["baseline/baseline_manifest.json"] = _sha256(
        out_dir / "baseline/baseline_manifest.json"
    )
    _, network = load_baseline(out_dir / "baseline")
    bundle = build_evidence_bundle(world, network.manifest)
    write("bundle.json", bundle.model_dump_json(indent=2))

    muse = MuseClient(
        settings,
        base / inputs.muse_cache_dir,
        mode,
        spend_limit_usd=spend_limit_usd,
        transport=muse_transport,
    )
    reasoning = muse.diagnose(bundle)
    write("reasoning.json", reasoning.model_dump_json(indent=2))
    outcomes = validate_proposals(reasoning.output.interventions, network, bundle, now=now)
    write(
        "outcomes.json",
        TypeAdapter(tuple[ValidationOutcome, ...]).dump_json(outcomes, indent=2),
    )

    experiment = run_candidates(out_dir / "baseline", outcomes, out_dir / "experiment", now=now)
    artifacts["experiment/experiment_manifest.json"] = _sha256(
        out_dir / "experiment/experiment_manifest.json"
    )
    evaluation = evaluate_experiment(out_dir / "experiment")
    artifacts["experiment/evaluation.json"] = _sha256(out_dir / "experiment/evaluation.json")

    report = assemble_report(
        world_model=world,
        bundle=bundle,
        reasoning=reasoning,
        outcomes=outcomes,
        experiment=experiment,
        evaluation=evaluation,
        artifacts=dict(sorted(artifacts.items())),
        now=now,
    )
    problems = trace_report(
        report,
        world_model=world,
        outcomes=outcomes,
        experiment=experiment,
        evaluation=evaluation,
        experiment_dir=out_dir / "experiment",
        bundle=bundle,
        reasoning=reasoning,
    )
    if problems:
        raise PipelineError("A9: report numbers do not trace: " + "; ".join(problems[:5]))
    json_path, md_path = write_report(report, out_dir)
    artifacts[json_path.name] = _sha256(json_path)
    artifacts[md_path.name] = _sha256(md_path)

    record = PipelineRecord(
        run_name=inputs.run_name,
        mode=mode,
        as_of_utc=now,
        inputs_sha256=_sha256(inputs_path),
        artifacts=dict(sorted(artifacts.items())),
        tripinfo={
            path.relative_to(out_dir).as_posix(): content_sha256(path, strip_comments=True)
            for path in sorted(out_dir.rglob("tripinfo.xml"))
        },
        review_artifacts=review,
        clip_attribution=clip.licence.attribution,
        clip_synthetic=clip.licence.synthetic,
        notes=tuple(notes),
        selected_intervention_id=report.selected_intervention_id,
    )
    with (out_dir / RECORD_FILE).open("x", encoding="utf-8") as handle:
        handle.write(record.model_dump_json(indent=2) + "\n")
    return record


def compare_records(reference: PipelineRecord, replayed: PipelineRecord) -> tuple[str, ...]:
    """Every artifact or tripinfo whose hash differs, is missing or is extra (empty: identical)."""
    if reference.mode != "replay":
        raise PipelineError("compare against a record made in replay mode")
    mismatches: list[str] = []
    if reference.inputs_sha256 != replayed.inputs_sha256:
        mismatches.append("inputs file differs")
    for label, ours, theirs in (
        ("artifact", reference.artifacts, replayed.artifacts),
        ("tripinfo", reference.tripinfo, replayed.tripinfo),
    ):
        for name in sorted(set(ours) | set(theirs)):
            if name not in theirs:
                mismatches.append(f"{label} {name} is missing from the replay")
            elif name not in ours:
                mismatches.append(f"{label} {name} is not in the reference")
            elif ours[name] != theirs[name]:
                mismatches.append(f"{label} {name} differs")
    for name in sorted(set(reference.review_artifacts) & set(replayed.review_artifacts)):
        if reference.review_artifacts[name] != replayed.review_artifacts[name]:
            mismatches.append(f"review artifact {name} differs")
    return tuple(mismatches)


def replay(
    inputs_path: Path, reference_path: Path, out_dir: Path
) -> tuple[PipelineRecord, tuple[str, ...]]:
    """Rerun offline into `out_dir` and compare with the reference record."""
    reference = PipelineRecord.model_validate_json(reference_path.read_text(encoding="utf-8"))
    record = run_pipeline(inputs_path, out_dir, mode="replay")
    return record, compare_records(reference, record)
