"""Records of the meta-depth runs (schema 0.1.0): one Muse frame annotation, and reply recurrence.

`FrameAnnotation` is what Muse Spark answers about ONE frame of the SAM review overlay, in the
strict schema of cairoflow_reasoning.frame_prompt. It is model-generated text: never evidence,
never ground truth, never a measurement, and no figure, metric or label reads it. It has no
numeric field on purpose. `FrameAnnotationRecord` keeps the request and its
outcome; a failed reply is recorded too (status "failed", no annotation).

`MuseRecurrence` says, across n independent replies to the same Muse request, which evidence ids
are cited and which hypotheses recur. It is computed by code (cairoflow_evaluation.muse_recurrence)
from stored replies; the hypothesis statements it carries are model text, kept verbatim.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from cairoflow_contracts.evidence import EvidenceModel, Provenance, Sha256

OBJECT_CATEGORIES = ("car", "bus", "truck", "motorcycle", "bicycle", "pedestrian", "other")
FRAME_REGIONS = (
    "top_left", "top", "top_right", "left", "center", "right", "bottom_left", "bottom",
    "bottom_right",
)  # fmt: skip
OVERLAY_BOX_STATES = ("boxed", "not_boxed", "unclear")
IMAGE_QUALITY = ("clear", "partly_obscured", "poor")


class AnnotatedObject(EvidenceModel):
    """One road user the model says it sees, where, and whether the SAM overlay boxes it."""

    category: Literal["car", "bus", "truck", "motorcycle", "bicycle", "pedestrian", "other"]
    region: Literal[
        "top_left", "top", "top_right", "left", "center", "right", "bottom_left", "bottom",
        "bottom_right",
    ] = Field(description="ninth of the image the object is in, image coordinates")  # fmt: skip
    overlay_box: Literal["boxed", "not_boxed", "unclear"] = Field(
        description="whether a SAM overlay box is drawn on it, as the model reads the frame"
    )
    note: str


class FrameAnnotation(EvidenceModel):
    """The model's reply about one frame (model-generated; not ground truth)."""

    scene_summary: str = Field(min_length=1)
    objects: tuple[AnnotatedObject, ...]
    overlay_issues: tuple[str, ...] = Field(description="boxes or captions the model doubts")
    image_quality: Literal["clear", "partly_obscured", "poor"]
    limitations: tuple[str, ...]


class AnnotatedFrame(EvidenceModel):
    """Which frame was sent: the pinned overlay it was cut from, and the image bytes."""

    source_path: str = Field(description="repository-relative path of the overlay video")
    source_sha256: Sha256
    frame_index: int = Field(ge=0, description="0-based frame of the overlay video")
    time_s: float = Field(ge=0, description="seconds from the start of the overlay video")
    image_path: str = Field(description="repository-relative path of the JPEG sent")
    image_sha256: Sha256
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)


class FrameAnnotationRecord(EvidenceModel):
    """One Muse Spark request on one overlay frame and its outcome."""

    label: str
    status: Literal["ok", "failed"]
    failure: str | None = Field(None, description="why the reply is unusable, when it is")
    model: str
    prompt_version: str
    endpoint: str
    request_key: Sha256
    request_id: str | None
    response_id: str | None
    sent_at_utc: datetime | None
    elapsed_s: float | None
    recorded_cost_usd: float = Field(ge=0, description="published prices x reported usage")
    usage: dict[str, int] = Field(default_factory=dict)
    cache_path: str | None = Field(None, description="the stored request and raw reply")
    frame: AnnotatedFrame
    annotation: FrameAnnotation | None
    provenance: Provenance


class ReplyRef(EvidenceModel):
    """One stored reply counted in a recurrence summary."""

    reply_id: str = Field(pattern=r"^r[1-9][0-9]*$")
    replicate: int = Field(ge=1)
    cache_path: str
    cache_sha256: Sha256
    response_id: str | None


class EvidenceRecurrence(EvidenceModel):
    evidence_id: str
    supporting_in: int = Field(ge=0, description="replies citing it in support of a hypothesis")
    contradicting_in: int = Field(ge=0, description="replies citing it against a hypothesis")
    cited_in: int = Field(ge=0, description="replies citing it at all")
    replies: tuple[str, ...]


class HypothesisMember(EvidenceModel):
    reply_id: str
    hypothesis_id: str
    statement: str = Field(description="model text, verbatim")
    confidence_label: str
    supporting_evidence_ids: tuple[str, ...]


class HypothesisCluster(EvidenceModel):
    """Hypotheses of different replies linked by overlapping supporting evidence."""

    cluster_id: str
    in_replies: int = Field(ge=1)
    members: tuple[HypothesisMember, ...]
    shared_evidence_ids: tuple[str, ...] = Field(description="cited by every member")


class TypeRecurrence(EvidenceModel):
    intervention_type: str
    in_replies: int = Field(ge=0)


class MuseRecurrence(EvidenceModel):
    """What recurs across n replies to one Muse request (computed by code)."""

    label: str
    request_key: Sha256
    prompt_version: str
    bundle_id: str
    n_replies: int = Field(ge=1)
    replies: tuple[ReplyRef, ...]
    rule: str = Field(description="how hypotheses are linked, in words")
    jaccard_threshold: float = Field(gt=0, le=1)
    evidence: tuple[EvidenceRecurrence, ...]
    hypotheses: tuple[HypothesisCluster, ...]
    interventions: tuple[TypeRecurrence, ...]
