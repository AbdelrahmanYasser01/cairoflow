"""The demo: the intersection 2 story on one page, read from committed records only.

A demo manifest (`data/demo/<demo id>.json`, written by scripts/build_demo_manifest.py) pins
every file the page reads by repository-relative path and SHA-256. Every pinned file is
committed, so the page works on a fresh clone; if any of them has changed, nothing is shown
(`DemoError`). A manifest may name pixel-bearing roles in `absent_allowed`: a tree that does not
distribute footage serves the page without them and says so (`FOOTAGE_NOTE`), and a file that is
there must still match its pin. This module checks the pins and the links between the records,
then picks figures out of them. It computes nothing new: every number on the page is a value a
record already holds. Model text comes only from the committed Muse reply and is kept apart
(`Demo.reasoning`), so the page can show it as model-generated and never as evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cairoflow_contracts.reasoning import EvidenceBundle, GroundingReport, ReasoningOutput
from cairoflow_contracts.report import BaselineAssessment
from cairoflow_reasoning.grounding import ground
from cairoflow_reasoning.muse_adapter import MuseRequestRecord, extract_output, parse_output

EVIDENCE_MARK = "\nEVIDENCE (JSON):\n"  # where prompts.build_input puts the bundle

MANIFEST_VERSION = "0.1.0"
REQUIRED_FILES = (
    "scenario",  # the scenario record: data source, counts
    "proposals",  # the experiment record of the Muse call: structured proposals, validator
    "muse_reply",  # the committed Muse reply: the only source of model text
    "timeline_estimate",  # the signal timeline version the baseline replays
    "baseline",  # the replay baseline record
    "validation",  # its validation against the observations
    "assessment",  # its baseline assessment
    "results",  # the candidates on it
    "results_v1",  # the same candidates on the version 1 replay (timeline sensitivity)
)
# Optional step 0, SAM 3.1 on the real footage: pinned together or not at all.
SAM_RECORD, SAM_OVERLAY, SAM_NOTICE = "sam_record", "sam_overlay", "sam_notice"
SAM_FILES = (SAM_RECORD, SAM_OVERLAY, SAM_NOTICE)
# The only roles a manifest may let be absent: both are pixels, and no figure is read from them.
# `frame_image` is a pin of the meta-depth sidecar (cairoflow_api.meta_depth).
PIXEL_ROLES = (SAM_OVERLAY, "frame_image")
FOOTAGE_NOTE = (
    "Footage isn't distributed here; run scripts/fetch_ngsim_data.py --download camera2 "
    "for the source video."
)
SAM_REVIEW = "sam_review"  # optional: the scored A3 hand check of that SAM run
# Optional: the pre-registered signal-timeline ensemble (ADR-025), whose T1 and T2 members must be
# the pinned results, and the synthetic known-answer control of the evaluation and selection
# stage (ADR-026), one record per scenario, never linked to the real junction's records.
ENSEMBLE = "ensemble"
CONTROLS = ("control_reallocation", "control_long_cycle")
DECISION_METRICS = (
    "mean_delay_s",
    "throughput_veh_per_h",
    "mean_queue_m.N",
    "mean_queue_m.E",
    "mean_queue_m.S",
    "mean_queue_m.W",
)
VALIDATION_KEYS = (
    "travel_time.N_through",
    "stopped_share.N_through",
    "travel_time.S_through",
    "stopped_share.S_through",
    "travel_time.N_left",
    "travel_time.S_left",
    "stopped_share.N_right",
    "stopped_share.S_right",
)
LAG_MOVEMENTS = ("N through", "S through", "N left", "W through", "W left", "W right")


class PinnedFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(description="repository-relative POSIX path")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def _inside_the_repository(cls, value: str) -> str:
        parts = PurePosixPath(value)
        if not value or parts.is_absolute() or ".." in parts.parts or "\\" in value:
            raise ValueError("a pinned path must be relative and inside the repository")
        return value


class DemoManifest(BaseModel):
    """Which committed files the demo page shows, each pinned by SHA-256."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = MANIFEST_VERSION
    demo_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    title: str = Field(min_length=1)
    run_dir: str | None = Field(
        None, description="the full run report under the app's runs root, linked if present"
    )
    files: dict[str, PinnedFile]
    absent_allowed: tuple[str, ...] = Field(
        (),
        description="pixel-bearing roles this tree may lack; left out of the JSON when empty",
        exclude_if=lambda roles: not roles,
    )

    @field_validator("files")
    @classmethod
    def _every_role_pinned(cls, value: dict[str, PinnedFile]) -> dict[str, PinnedFile]:
        missing = [role for role in REQUIRED_FILES if role not in value]
        if missing:
            raise ValueError(f"missing pinned files: {', '.join(missing)}")
        return value

    @model_validator(mode="after")
    def _only_pixels_may_be_absent(self) -> DemoManifest:
        refused = [role for role in self.absent_allowed if role not in PIXEL_ROLES]
        if refused:
            raise ValueError(f"only pixel-bearing roles may be absent: {', '.join(refused)}")
        return self


class DemoError(RuntimeError):
    """A pinned file is missing, differs from the manifest, or does not link to the others."""


@dataclass(frozen=True)
class Demo:
    manifest: DemoManifest
    records: dict[str, dict]  # every pinned JSON file but the Muse reply, parsed, by role
    reasoning: ReasoningOutput  # model-generated: hypotheses and proposals with their text
    assessment: BaselineAssessment
    texts: dict[str, str] = field(default_factory=dict)  # pinned Markdown notices, by role
    grounding: GroundingReport | None = None  # code's evidence checks on the reply, recomputed
    bundle: EvidenceBundle | None = None  # the evidence the pinned request sent, as sent
    absent: tuple[str, ...] = ()  # roles the manifest lets be absent that are not in this tree

    @property
    def has_sam(self) -> bool:
        return SAM_RECORD in self.records

    @property
    def has_overlay(self) -> bool:
        """Step 0's video is pinned and in this tree."""
        return SAM_OVERLAY in self.manifest.files and SAM_OVERLAY not in self.absent

    @property
    def has_ensemble(self) -> bool:
        return ENSEMBLE in self.records

    @property
    def controls(self) -> tuple[str, ...]:
        """The pinned synthetic control roles, in CONTROLS order."""
        return tuple(role for role in CONTROLS if role in self.records)


def read_manifest(path: Path) -> DemoManifest:
    return DemoManifest.model_validate_json(path.read_text(encoding="utf-8"))


def load_demo(manifest: DemoManifest, repo_root: Path) -> Demo:
    """Check every pin and the links between the records, then parse them."""
    raw: dict[str, bytes] = {}
    absent: list[str] = []
    for role, pin in manifest.files.items():
        path = repo_root / pin.path
        if not path.is_file():
            if role in manifest.absent_allowed:
                absent.append(role)
                continue
            raise DemoError(f"{pin.path} is missing")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != pin.sha256:
            raise DemoError(f"{pin.path} differs from the demo manifest")
        raw[role] = data
    reply = MuseRequestRecord.model_validate_json(raw.pop("muse_reply"))
    kinds = {role: PurePosixPath(manifest.files[role].path).suffix for role in raw}
    records = {role: json.loads(data) for role, data in raw.items() if kinds[role] == ".json"}
    texts = {role: data.decode("utf-8") for role, data in raw.items() if kinds[role] == ".md"}
    assessment = BaselineAssessment.model_validate(records["assessment"])
    results, baseline = records["results"], records["baseline"]
    links = {
        "the results were simulated on the pinned baseline": (
            results["baseline_manifest_sha256"] == baseline["baseline_manifest_sha256"]
        ),
        "the assessment is about the pinned baseline": (
            assessment.baseline_manifest_sha256 == baseline["baseline_manifest_sha256"]
        ),
        "the results' report loaded the pinned assessment": (
            results["report"]["baseline_assessment"]["file_sha256"]
            == manifest.files["assessment"].sha256
        ),
        "the baseline replays the pinned timeline": (
            baseline["inputs"].get("signal_timeline_id")
            == records["timeline_estimate"]["timeline_id"]
        ),
        "the proposals came from the pinned Muse reply": (
            records["proposals"]["muse_request"]["request_key"] == reply.request_key
        ),
    }
    if SAM_RECORD in manifest.files:  # step 0: SAM 3.1 on the real footage
        missing = [role for role in SAM_FILES if role not in manifest.files]
        if missing:
            raise DemoError(f"SAM step pinned without {', '.join(missing)}")
        links["the SAM record names the pinned overlay"] = (
            records[SAM_RECORD]["overlay"]["output_sha256"] == manifest.files[SAM_OVERLAY].sha256
        )
    if SAM_REVIEW in manifest.files:  # the A3 hand check of step 0's boxes
        if SAM_RECORD not in manifest.files:
            raise DemoError("SAM review pinned without sam_record")
        links["the A3 review marked the pinned SAM clip"] = (
            records[SAM_REVIEW]["clip_id"] == records[SAM_RECORD]["clip"]["clip_id"]
        )
    if ENSEMBLE in manifest.files:  # its T1 and T2 members are the pinned results
        members = {m["id"]: m for m in records[ENSEMBLE]["members"]}
        for member, role in (("T1", "results_v1"), ("T2", "results")):
            links[f"the ensemble's {member} member is the pinned {role}"] = (
                members.get(member, {}).get("experiment_summary_sha256")
                == manifest.files[role].sha256
            )
    for role in CONTROLS:
        if role in manifest.files:  # synthetic, of the scenario its role names
            record = records[role]
            links[f"{role} is a synthetic known-answer record of its scenario"] = record.get(
                "label", ""
            ).startswith("SYNTHETIC") and role == f"control_{record.get('scenario', '')}".replace(
                "-", "_"
            )
    broken = [name for name, ok in links.items() if not ok]
    if broken:
        raise DemoError("the pinned records do not link: " + "; ".join(broken))
    reasoning = parse_output(extract_output(json.loads(reply.response_text)))
    bundle = bundle_of(reply)
    grounding = ground(reasoning, bundle, reply.request_key)
    return Demo(manifest, records, reasoning, assessment, texts, grounding, bundle, tuple(absent))


def bundle_of(reply: MuseRequestRecord) -> EvidenceBundle:
    """The evidence bundle exactly as the pinned request sent it (prompts.build_input)."""
    _, mark, evidence = reply.body["input"].partition(EVIDENCE_MARK)
    if not mark:
        raise DemoError("the pinned Muse request holds no evidence bundle")
    return EvidenceBundle.model_validate_json(evidence)


# --- Figures, as the records hold them -------------------------------------------------------


@dataclass(frozen=True)
class Difference:
    """A candidate's paired difference from the baseline on one metric (simulated)."""

    value: float
    interval: tuple[float, float]
    unit: str
    verdict: str
    confirmed: bool  # the improvement also survives Holm


def differences(record: dict) -> dict[str, dict[str, Difference]]:
    """Candidate -> metric -> its paired difference, for the decision metrics."""
    confirmed = {
        (t["candidate"], t["metric_name"]) for t in record["improvement_tests"] if t["confirmed"]
    }
    out: dict[str, dict[str, Difference]] = {}
    for c in record["comparisons"]:
        if c["metric_name"] not in DECISION_METRICS:
            continue
        out.setdefault(c["candidate"], {})[c["metric_name"]] = Difference(
            value=c["mean_difference"],
            interval=(c["ci95_difference"][0], c["ci95_difference"][1]),
            unit=c["unit"],
            verdict=c["verdict"],
            confirmed=(c["candidate"], c["metric_name"]) in confirmed,
        )
    return out


def baseline_ranges(record: dict) -> dict[str, tuple[float, float, str]]:
    """Metric -> (lowest, highest, unit) of the baseline's recorded per-seed values: the scale
    the paired differences are read against."""
    out: dict[str, tuple[float, float, str]] = {}
    for c in record["comparisons"]:
        if c["metric_name"] in DECISION_METRICS and c["metric_name"] not in out:
            values = c["baseline_values"]
            out[c["metric_name"]] = (min(values), max(values), c["unit"])
    return out


@dataclass(frozen=True)
class Lag:
    """Paired stop-line lag of one movement (simulated less observed), over the seeds."""

    movement: str
    paired_veh: int
    median_low_s: float
    median_high_s: float
    share_within_5_s: float


def paired_lags(validation: dict) -> list[Lag]:
    by = validation["details"]["paired_stop_line_lag_s"]["by_movement"]
    return [
        Lag(
            movement=name,
            paired_veh=max(by[name]["paired_veh_per_seed"]),
            median_low_s=min(by[name]["median_lag_s_per_seed"]),
            median_high_s=max(by[name]["median_lag_s_per_seed"]),
            share_within_5_s=by[name]["share_within_5_s_pooled"],
        )
        for name in LAG_MOVEMENTS
        if name in by
    ]


def validation_rows(validation: dict) -> list[dict]:
    """The selected observed-against-simulated comparisons, in VALIDATION_KEYS order."""
    by_key = {c["key"]: c for c in validation["comparisons"]}
    return [by_key[key] for key in VALIDATION_KEYS if key in by_key]
