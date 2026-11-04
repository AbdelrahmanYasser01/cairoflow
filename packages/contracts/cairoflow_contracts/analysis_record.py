"""The shared analysis record (schema 0.1.0, record format analysis/1).

One analysis is one immutable directory read by every consumer: the video viewer, the analytics
views, the 3D twin and the evidence pages. It holds `record.json` (an `AnalysisRecord`),
`tracks.json` (a `TrackIndex`) and `trajectories.bin` (columnar per-point data, format in
cairoflow_perception.analysis_store).

Coordinates: image pixels use the evidence.py convention. Ground values are metres in the junction
frame of data/geometry/ngsim-lankershim-int2/geometry.json (`local_enu_junction_m`). Times are
seconds from the first analysed frame unless a field says otherwise.

Every number a screen shows is a `Metric` or `MetricSeries`, so it always says what kind of evidence
it is and whether a gate covered it. A value withheld by a failed gate is absent, not zero: a
`Metric` with status gate_fail or not_available cannot carry a value. Status is not the
ADR-014 geometry label: camera-derived geometry stays "approximated" whatever a gate says.

`data_origin` keeps two things apart that must never be confused: `camera_cv` was produced from
video by this pipeline, `reference_dataset` was read from the published NGSIM trajectories.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from cairoflow_contracts.evidence import (
    Arm,
    EvidenceKind,
    EvidenceModel,
    Provenance,
    Sha256,
    Turn,
    _check_sha256,
)

RECORD_FORMAT = "analysis/1"
TRAJECTORY_MAGIC = "CFT1"

AnalysisMode = Literal["precomputed", "live"]
DataOrigin = Literal["camera_cv", "reference_dataset"]
MetricStatus = Literal["gate_pass", "gate_fail", "ungated", "not_available"]
CalibrationMode = Literal["calibrated", "uncalibrated"]
ArtifactRole = Literal[
    "trajectories", "tracks", "density", "annotated_video", "source_video", "report"
]
VehicleClass = Literal["car", "truck", "bus", "motorcycle", "vehicle", "unknown"]
ClassSource = Literal["detector", "size", "sam", "reference_dataset", "none"]
TrackStatus = Literal["complete", "partial", "fragment"]
GateSplit = Literal["dev", "held_out"]
GateComparator = Literal["<=", ">="]
DecisionOutcome = Literal["none_selected", "selected", "not_run"]


class Metric(EvidenceModel):
    """One number shown to a person, with what it is and how far it can be trusted."""

    metric_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]*$")
    label: str = Field(min_length=1)
    value: float | None = Field(None, allow_inf_nan=False)
    unit: str = Field(min_length=1, description="'veh', 'veh/h', 'm', 'm/s', 'share', ...")
    scope: str = Field(min_length=1, description="what it covers, e.g. 'site' or 'arm:N'")
    evidence_kind: EvidenceKind
    status: MetricStatus
    reason: str | None = Field(None, description="required unless status is gate_pass")
    gate_id: str | None = None
    ci95: tuple[float, float] | None = None

    @model_validator(mode="after")
    def _honest(self) -> Metric:
        if self.status in ("gate_fail", "not_available") and self.value is not None:
            raise ValueError(f"a metric with status {self.status} cannot carry a value")
        if self.status in ("gate_pass", "ungated") and self.value is None:
            raise ValueError(f"a metric with status {self.status} needs a value")
        if self.status != "gate_pass" and not self.reason:
            raise ValueError(f"status {self.status} needs a reason")
        if self.status in ("gate_pass", "gate_fail") and not self.gate_id:
            raise ValueError(f"status {self.status} needs the gate_id that decided it")
        return self


class MetricSeries(EvidenceModel):
    """Equal-width time bins of one measure; a bin with no value is None, never zero."""

    series_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]*$")
    label: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    bin_s: float = Field(gt=0)
    start_s: float = Field(ge=0, description="start of the first bin")
    values: tuple[float | None, ...]
    evidence_kind: EvidenceKind
    status: MetricStatus
    reason: str | None = None
    gate_id: str | None = None

    @model_validator(mode="after")
    def _honest(self) -> MetricSeries:
        if self.status in ("gate_fail", "not_available") and any(
            v is not None for v in self.values
        ):
            raise ValueError(f"a series with status {self.status} cannot carry values")
        if self.status != "gate_pass" and not self.reason:
            raise ValueError(f"status {self.status} needs a reason")
        return self


class MovementCell(EvidenceModel):
    """One cell of the turning-movement matrix for an analysed window."""

    approach: Arm
    exit_arm: Arm
    turn: Turn
    count: int | None = Field(None, ge=0)
    status: MetricStatus
    reason: str | None = None
    gate_id: str | None = None
    reference_count: int | None = Field(
        None, ge=0, description="same cell counted from the NGSIM trajectories, for comparison"
    )

    @model_validator(mode="after")
    def _honest(self) -> MovementCell:
        if self.status in ("gate_fail", "not_available") and self.count is not None:
            raise ValueError(f"a cell with status {self.status} cannot carry a count")
        if self.status != "gate_pass" and not self.reason:
            raise ValueError(f"status {self.status} needs a reason")
        return self


class StageReceipt(EvidenceModel):
    """What one pipeline stage actually did: the tool, its inputs and its output count."""

    stage: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    version: str = Field(min_length=1)
    licence: str | None = None
    weights_sha256: Sha256 | None = None
    device: str | None = None
    frames_in: int | None = Field(None, ge=0)
    items_out: int | None = Field(None, ge=0)
    elapsed_s: float | None = Field(None, ge=0)
    cached: bool = False
    cost_usd: float | None = Field(None, ge=0)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("weights_sha256")
    @classmethod
    def _hash(cls, value: str | None) -> str | None:
        return None if value is None else _check_sha256(value)


class CalibrationState(EvidenceModel):
    """How image pixels reach the ground, and how far that is trusted."""

    mode: CalibrationMode
    ground_frame: str | None = Field(None, description="'local_enu_junction_m' when calibrated")
    image_to_ground: tuple[tuple[float, float, float], ...] | None = Field(
        None, description="3x3 homography, image pixels to ground metres, row-major"
    )
    spec_sha256: Sha256 | None = None
    accepted_along_road: bool = Field(
        False, description="False: along-road distances are not accepted (ADR-018)"
    )
    valid_windows_s: tuple[tuple[float, float], ...] = Field(
        (), description="source-second windows where a gate measured placement"
    )
    valid_below_row_px: int | None = Field(
        None, description="rows at or past this one are outside the measured region"
    )
    origin_lat_deg: float | None = None
    origin_lon_deg: float | None = None
    bearing_deg: float | None = Field(None, description="junction +y axis, degrees from grid north")
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> CalibrationState:
        if self.mode == "calibrated" and (not self.ground_frame or not self.image_to_ground):
            raise ValueError("a calibrated analysis names its ground frame and homography")
        if self.mode == "uncalibrated" and self.image_to_ground is not None:
            raise ValueError("an uncalibrated analysis has no homography")
        if self.image_to_ground is not None and len(self.image_to_ground) != 3:
            raise ValueError("image_to_ground must be 3x3")
        return self


class GateOutcome(EvidenceModel):
    """One registered check on one metric over one split of the footage."""

    gate_id: str = Field(min_length=1)
    gate_sha256: Sha256 = Field(description="the registered gate file, committed before the read")
    metric: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    split: GateSplit
    measured: float | None = Field(None, allow_inf_nan=False)
    comparator: GateComparator
    threshold: float = Field(allow_inf_nan=False)
    n: int = Field(ge=0)
    passed: bool
    note: str | None = None


class VideoSource(EvidenceModel):
    """Which footage this analysis came from. Pixels may be absent; the identity is not."""

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    site_name: str = Field(min_length=1)
    captured: str | None = Field(None, description="when it was filmed, as published")
    licence_name: str = Field(min_length=1)
    licence_url: str | None = None
    attribution: str = Field(min_length=1, description="exact text to show wherever pixels appear")
    source_file: str = Field(min_length=1)
    source_sha256: Sha256 | None = None
    uploaded_by_user: bool = False


class ArtifactRef(EvidenceModel):
    """One file of an analysis. `bundled` False means it is rebuilt locally, not distributed."""

    role: ArtifactRole
    path: str = Field(min_length=1, description="relative to the analysis directory")
    sha256: Sha256 | None = None
    bytes: int | None = Field(None, ge=0)
    media_type: str = Field(min_length=1)
    bundled: bool = True
    licence_note: str | None = None
    rebuild_hint: str | None = Field(None, description="how to get it when it is not bundled")


class DecisionRef(EvidenceModel):
    """The decision record shown beside this analysis, and whether this analysis fed it."""

    run_id: str = Field(min_length=1)
    report_sha256: Sha256 | None = None
    outcome: DecisionOutcome
    summary: str = Field(min_length=1)
    linked_to_cv: bool = Field(
        description="False: the decision used the published trajectories, not this CV output"
    )


class AnalysisRecord(EvidenceModel):
    """Everything one analysis claims, what backs it, and what it cannot say."""

    record_format: Literal["analysis/1"] = RECORD_FORMAT
    analysis_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    mode: AnalysisMode
    data_origin: DataOrigin
    site_id: str = Field(min_length=1)
    created_at_utc: datetime
    source: VideoSource
    fps: float = Field(gt=0)
    frame_count: int = Field(gt=0)
    frame_stride: int = Field(1, ge=1)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    source_start_s: float = Field(ge=0, description="first analysed second of the source video")
    window_offset_s: float = Field(
        description="source second minus this gives the NGSIM window second"
    )
    calibration: CalibrationState
    stages: tuple[StageReceipt, ...]
    gates: tuple[GateOutcome, ...] = ()
    metrics: tuple[Metric, ...]
    series: tuple[MetricSeries, ...] = ()
    movements: tuple[MovementCell, ...] = ()
    artifacts: tuple[ArtifactRef, ...]
    decision_ref: DecisionRef | None = None
    disclosures: tuple[str, ...] = Field(description="limits a reader must see beside the numbers")
    provenance: Provenance

    @model_validator(mode="after")
    def _consistent(self) -> AnalysisRecord:
        ids = [metric.metric_id for metric in self.metrics]
        if len(ids) != len(set(ids)):
            raise ValueError("metric ids must be unique within an analysis")
        series_ids = [series.series_id for series in self.series]
        if len(series_ids) != len(set(series_ids)):
            raise ValueError("series ids must be unique within an analysis")
        roles = {artifact.role for artifact in self.artifacts}
        if not {"trajectories", "tracks"} <= roles:
            raise ValueError("an analysis must list its trajectories and tracks artifacts")
        if self.data_origin == "reference_dataset" and self.mode != "precomputed":
            raise ValueError("a reference analysis is read from a dataset, never run live")
        return self


class TrackEntry(EvidenceModel):
    """One vehicle: where its rows sit in trajectories.bin and what it did."""

    track_id: int = Field(ge=0)
    first_row: int = Field(ge=0, description="row of its first point; row = first_row + i")
    start_frame: int = Field(ge=0, description="analysis frame of its first point")
    n: int = Field(gt=0, description="points, one per frame; gaps are interpolated and flagged")
    vehicle_class: VehicleClass
    class_source: ClassSource
    class_confidence: float | None = Field(None, ge=0, le=1)
    length_m: float | None = Field(None, gt=0)
    width_m: float | None = Field(None, gt=0)
    approach: Arm | None = None
    exit_arm: Arm | None = None
    turn: Turn | None = None
    status: TrackStatus
    sam_refined: bool = False
    reference_vehicle_id: int | None = Field(
        None, description="the NGSIM vehicle this is, for a reference analysis or a paired track"
    )


class TrackIndex(EvidenceModel):
    """The vehicles of one analysis, in trajectories.bin row order."""

    analysis_id: str = Field(min_length=1)
    trajectories_sha256: Sha256
    tracks: tuple[TrackEntry, ...]

    @model_validator(mode="after")
    def _contiguous(self) -> TrackIndex:
        expected = 0
        for track in self.tracks:
            if track.first_row != expected:
                raise ValueError("tracks must be contiguous in row order")
            expected += track.n
        return self
