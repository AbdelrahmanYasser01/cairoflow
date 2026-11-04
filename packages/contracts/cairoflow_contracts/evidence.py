"""Evidence schemas shared by every pipeline stage (schema version 0.1.0).

Conventions, fixed here so no stage has to guess:
- Image pixels (`*_px`): origin at the top-left of the frame, x to the right, y down. Boxes are
  half-open `[x1, x2) x [y1, y2)`, matching `meta-sam-parser`.
- Time (`*_s`): seconds from the first frame of the ingested clip, not wall-clock time.
- Ground metres (`*_m`) and SUMO metres are separate spaces; nothing here converts between them.

Every value says what kind of evidence it is: observed, approximated,
assumed, model-generated or simulated. Geometry values also carry the ADR-014 label.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

SCHEMA_VERSION = "0.1.0"

EvidenceKind = Literal["observed", "approximated", "assumed", "model_generated", "simulated"]
GeometryProvenance = Literal["measured", "approximated", "synthetic"]  # ADR-014
GeometryUnit = Literal["m", "m/s", "s", "deg", "count"]
Sha256 = str


class EvidenceModel(BaseModel):
    """Base for all evidence: immutable, no unknown fields, versioned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["0.1.0"] = SCHEMA_VERSION


def _check_sha256(value: str) -> str:
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("expected a lowercase hex SHA-256 digest")
    return value


class Provenance(EvidenceModel):
    """Who or what produced a piece of evidence, from which inputs, with which settings."""

    kind: EvidenceKind
    produced_by: str = Field(min_length=1, description="module path or model id")
    produced_at_utc: datetime
    input_sha256: tuple[Sha256, ...] = ()
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    software: dict[str, str] = Field(default_factory=dict, description="name -> version")
    cached: bool = Field(False, description="True when replayed from cache, not produced live")

    @field_validator("input_sha256")
    @classmethod
    def _hashes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_check_sha256(value) for value in values)

    @field_validator("produced_at_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("produced_at_utc must be timezone-aware UTC")
        return value


class ClipLicense(EvidenceModel):
    """The licence record of one source clip; mirrors its row in docs/DATA_REGISTER.md."""

    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    source_url: str = Field(pattern=r"^https?://\S+$")
    licence_name: str = Field(min_length=1)
    licence_url: str | None = Field(None, pattern=r"^https?://\S+$")
    attribution: str = Field(min_length=1, description="exact text to show wherever pixels appear")
    accessed_on: date
    register_ref: str = Field(min_length=1, description="where the register row lives")
    permits_commercial_use: bool
    permits_cloud_processing: bool = Field(description="may be uploaded to a hosted model API")
    permits_redistribution: bool
    share_alike: bool
    synthetic: bool = Field(False, description="generated in code; no third-party content")

    @property
    def usable_for_upload(self) -> bool:
        """The MVP uploads clips to SAM 3.1, so a clip is only usable if both are allowed."""
        return self.permits_cloud_processing and self.permits_commercial_use


class VideoStreamInfo(EvidenceModel):
    """ffprobe facts about the first video stream of a file. Observed, not assumed."""

    codec: str
    pixel_format: str
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    fps: float = Field(gt=0, description="average frame rate")
    frame_count: int = Field(gt=0)
    duration_s: float = Field(gt=0)


class TrimSpec(EvidenceModel):
    """Which part of the source to keep, in source seconds."""

    start_s: float = Field(0.0, ge=0)
    duration_s: float | None = Field(None, gt=0, description="None keeps the rest of the clip")


class FrameRef(EvidenceModel):
    """One frame of an ingested clip, or one time step of a ground dataset's observation window
    (then `clip_id` names the window and `timestamp_s` counts from its start)."""

    clip_id: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    timestamp_s: float = Field(ge=0, description="presentation time from the clip's first frame")


class SourceClip(EvidenceModel):
    """Manifest of one ingested clip: what came in, what was produced, and how."""

    clip_id: str = Field(min_length=1, description="first 16 hex digits of output_sha256")
    licence: ClipLicense
    source_file_name: str = Field(min_length=1)
    source_sha256: Sha256
    source_stream: VideoStreamInfo
    trim: TrimSpec
    target_fps: int = Field(gt=0)
    output_file: str = Field(min_length=1, description="file name, relative to the manifest")
    output_sha256: Sha256
    output_stream: VideoStreamInfo
    ffmpeg_command: tuple[str, ...] = Field(description="with {source} and {output} placeholders")
    frames: tuple[FrameRef, ...]
    provenance: Provenance

    @field_validator("source_sha256", "output_sha256")
    @classmethod
    def _hashes(cls, value: str) -> str:
        return _check_sha256(value)

    @model_validator(mode="after")
    def _consistent(self) -> SourceClip:
        if self.clip_id != self.output_sha256[:16]:
            raise ValueError("clip_id must be the first 16 hex digits of output_sha256")
        if len(self.frames) != self.output_stream.frame_count:
            raise ValueError("frames must list every frame of the output stream")
        for expected, frame in enumerate(self.frames):
            if frame.frame_index != expected or frame.clip_id != self.clip_id:
                raise ValueError("frames must be indexed 0..n-1 and belong to this clip")
        times = [frame.timestamp_s for frame in self.frames]
        if any(later <= earlier for earlier, later in zip(times, times[1:], strict=False)):
            raise ValueError("frame timestamps must increase strictly")
        return self


class Observation(EvidenceModel):
    """One detected object in one frame (perception output, not ground truth)."""

    observation_id: str = Field(min_length=1)
    frame: FrameRef
    class_label: str = Field(min_length=1, description="the noun phrase sent to SAM")
    object_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*:\S+$", description="class-namespaced, e.g. 'car:12'"
    )
    bbox_xyxy_px: tuple[float, float, float, float] = Field(description="half-open image pixels")
    mask_artifact_uri: str | None = None
    confidence: float | None = Field(None, ge=0, le=1, description="None when not supplied")
    provenance: Provenance

    @field_validator("bbox_xyxy_px")
    @classmethod
    def _box(cls, box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = box
        if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
            raise ValueError("bbox must satisfy 0 <= x1 < x2 and 0 <= y1 < y2")
        return box

    @model_validator(mode="after")
    def _namespace(self) -> Observation:
        if self.object_id.split(":", 1)[0] != self.class_label.replace(" ", "_"):
            raise ValueError("object_id must be namespaced by class_label")
        return self


class TrackPoint(EvidenceModel):
    """One observation as a point of a track, in image pixels."""

    observation_id: str = Field(min_length=1)
    frame: FrameRef
    bbox_xyxy_px: tuple[float, float, float, float]
    contact_point_px: tuple[float, float] = Field(
        description="bottom-centre of the box: where the vehicle meets the road, image pixels"
    )


class Track(EvidenceModel):
    """One vehicle followed through the clip, stitched from SAM object ids by our procedure."""

    track_id: str = Field(pattern=r"^[a-z][a-z0-9_]*:t[1-9][0-9]*$", description="e.g. 'car:t3'")
    clip_id: str = Field(min_length=1)
    class_label: str = Field(min_length=1)
    sam_object_ids: tuple[str, ...] = Field(min_length=1, description="in order of appearance")
    points: tuple[TrackPoint, ...] = Field(min_length=1)
    bridged_gaps: int = Field(ge=0, description="gaps joined between different SAM ids")
    provenance: Provenance

    @model_validator(mode="after")
    def _consistent(self) -> Track:
        namespace = self.class_label.replace(" ", "_")
        if self.track_id.split(":", 1)[0] != namespace:
            raise ValueError("track_id must be namespaced by class_label")
        if any(not sam_id.startswith(namespace + ":") for sam_id in self.sam_object_ids):
            raise ValueError("every SAM object id must belong to the track's class")
        frames = [point.frame.frame_index for point in self.points]
        if any(later <= earlier for earlier, later in zip(frames, frames[1:], strict=False)):
            raise ValueError("track points must have strictly increasing frame indices")
        if any(point.frame.clip_id != self.clip_id for point in self.points):
            raise ValueError("every point must belong to the track's clip")
        return self


class ControlPoint(EvidenceModel):
    """One image point with its known ground position, used to fit or to check a calibration."""

    point_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    image_px: tuple[float, float]
    ground_m: tuple[float, float] = Field(
        description="local ground metres: origin at the junction centre, x east, y north"
    )
    role: Literal["fit", "check"] = Field(description="'check' points are held out of the fit")
    ground_provenance: GeometryProvenance
    source: str = Field(min_length=1, description="lane-marking corner, drawing reference, ...")


class CalibrationSpec(EvidenceModel):
    """Hand-picked control points for one clip (the versioned `calibration.json`)."""

    clip_id: str = Field(min_length=1)
    image_width_px: int = Field(gt=0)
    image_height_px: int = Field(gt=0)
    ground_frame: Literal["local_enu_junction_m"] = "local_enu_junction_m"
    points: tuple[ControlPoint, ...]

    @model_validator(mode="after")
    def _valid(self) -> CalibrationSpec:
        ids = [point.point_id for point in self.points]
        if len(set(ids)) != len(ids):
            raise ValueError("control point ids must be unique")
        if sum(point.role == "fit" for point in self.points) < 4:
            raise ValueError("a homography needs at least 4 fit points")
        for point in self.points:
            x, y = point.image_px
            if not (0 <= x <= self.image_width_px and 0 <= y <= self.image_height_px):
                raise ValueError(f"control point {point.point_id} is outside the image")
        return self


class CalibrationReport(EvidenceModel):
    """A fitted image-to-ground homography and how well it reproduces the control points."""

    clip_id: str
    spec_sha256: Sha256
    image_to_ground: tuple[float, ...] = Field(
        min_length=9, max_length=9, description="3x3 row-major, scaled so the last entry is 1"
    )
    fit_point_ids: tuple[str, ...]
    check_point_ids: tuple[str, ...]
    fit_rms_m: float = Field(ge=0)
    fit_max_m: float = Field(ge=0)
    check_errors_m: dict[str, float]
    check_errors_px: dict[str, float]
    max_check_error_m: float | None
    target_max_check_error_m: float = Field(gt=0)
    accepted: bool
    reasons: tuple[str, ...] = Field(description="why the calibration is not accepted, if not")
    provenance: Provenance

    @field_validator("spec_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)


class GeometryValue(EvidenceModel):
    """One intersection-geometry or signal-timing number with its unit and ADR-014 label."""

    value: float
    unit: GeometryUnit
    provenance: GeometryProvenance
    source: str = Field(min_length=1, description="drawing, procedure or reason for the value")
    error: float | None = Field(None, ge=0, description="1-sigma error in the same unit, if known")

    @model_validator(mode="after")
    def _synthetic_has_no_error(self) -> GeometryValue:
        if self.provenance == "synthetic" and self.error is not None:
            raise ValueError("a synthetic value has no measurement error")
        return self


# --- Intersection geometry (inputs drawn by hand in local ground metres) ----------------------

Arm = Literal["N", "E", "S", "W"]  # the junction arm, by compass direction from the centre
Turn = Literal["left", "through", "right", "u_turn"]
GroundPoint = tuple[float, float]  # local ground metres: origin at the junction centre, x east


def _orient(a: GroundPoint, b: GroundPoint, p: GroundPoint) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _segments_touch(p1: GroundPoint, p2: GroundPoint, q1: GroundPoint, q2: GroundPoint) -> bool:
    d1, d2 = _orient(q1, q2, p1), _orient(q1, q2, p2)
    d3, d4 = _orient(p1, p2, q1), _orient(p1, p2, q2)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return True

    def on(a: GroundPoint, b: GroundPoint, p: GroundPoint) -> bool:
        return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(
            a[1], b[1]
        )

    return (
        (d1 == 0 and on(q1, q2, p1))
        or (d2 == 0 and on(q1, q2, p2))
        or (d3 == 0 and on(p1, p2, q1))
        or (d4 == 0 and on(p1, p2, q2))
    )


class LanePolygon(EvidenceModel):
    """One lane drawn by hand in ground metres, with what its markings allow."""

    lane_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    arm: Arm
    direction: Literal["approach", "exit"] = Field(description="towards or away from the centre")
    polygon_m: tuple[GroundPoint, ...] = Field(
        min_length=3, description="local ground metres, vertices in order, not closed"
    )
    allowed_turns: tuple[Turn, ...] = Field(description="from markings; approach lanes only")
    provenance: GeometryProvenance
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> LanePolygon:
        if (self.direction == "approach") != bool(self.allowed_turns):
            raise ValueError("approach lanes list their allowed turns; exit lanes list none")
        points, n = self.polygon_m, len(self.polygon_m)
        area = sum(_orient((0.0, 0.0), points[i], points[(i + 1) % n]) for i in range(n)) / 2
        if abs(area) < 1e-6:
            raise ValueError(f"lane {self.lane_id} has no area")
        for i in range(n):
            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue  # adjacent through the closing edge
                if _segments_touch(points[i], points[i + 1], points[j], points[(j + 1) % n]):
                    raise ValueError(f"lane {self.lane_id} polygon must be simple")
        return self


class Gate(EvidenceModel):
    """A stop line or exit line: a ground segment whose crossing defines a movement.

    Direction is fixed by the ground frame: a stop line is crossed towards the junction centre
    (the origin), an exit line away from it.
    """

    arm: Arm
    role: Literal["stop_line", "exit_line"]
    segment_m: tuple[GroundPoint, GroundPoint]
    provenance: GeometryProvenance
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> Gate:
        a, b = self.segment_m
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length < 0.5:
            raise ValueError("a gate must be at least 0.5 m long")
        if abs(_orient(a, b, (0.0, 0.0))) / length < 0.5:
            raise ValueError("a gate line must pass at least 0.5 m from the junction centre")
        return self


class IntersectionGeometry(EvidenceModel):
    """Lanes and gates of one junction in local ground metres, versioned with their frame anchor.

    The ground frame has exactly one anchor: the camera calibration the geometry was drawn against
    (`calibration_spec_sha256`), or the published dataset of ground positions it was derived from
    (`ground_source_sha256`, e.g. the NGSIM trajectory file). A geometry anchored on a dataset
    cannot be used to measure camera tracks: the world model checks the calibration hash.
    """

    geometry_id: str = Field(min_length=1)
    clip_id: str = Field(
        min_length=1, description="the ingested clip, or the observation window of a ground dataset"
    )
    calibration_spec_sha256: Sha256 | None = None
    ground_source_sha256: Sha256 | None = Field(
        None, description="SHA-256 of the published ground dataset the geometry is derived from"
    )
    ground_frame: Literal["local_enu_junction_m"] = "local_enu_junction_m"
    drives_on: Literal["right"] = Field("right", description="fixes which turn is left or right")
    lanes: tuple[LanePolygon, ...]
    gates: tuple[Gate, ...]

    @field_validator("calibration_spec_sha256", "ground_source_sha256")
    @classmethod
    def _hash(cls, value: str | None) -> str | None:
        return None if value is None else _check_sha256(value)

    @model_validator(mode="after")
    def _valid(self) -> IntersectionGeometry:
        if (self.calibration_spec_sha256 is None) == (self.ground_source_sha256 is None):
            raise ValueError("give exactly one frame anchor: a calibration or a ground dataset")
        ids = [lane.lane_id for lane in self.lanes]
        if len(set(ids)) != len(ids):
            raise ValueError("lane ids must be unique")
        keys = [(gate.arm, gate.role) for gate in self.gates]
        if len(set(keys)) != len(keys):
            raise ValueError("at most one stop line and one exit line per arm")
        for lane in self.lanes:
            if lane.direction == "approach" and (lane.arm, "stop_line") not in keys:
                raise ValueError(f"approach lane {lane.lane_id}: arm {lane.arm} has no stop line")
        return self


# --- Image-side geometry (pixels; needs no calibration) ---------------------------------------

ImagePoint = tuple[float, float]  # image pixels: origin top-left, u right, v down
ImageLineRole = Literal["limit_line", "exit_line", "lane_line", "centreline", "kerb_edge"]


def _simple_polygon(points: tuple[ImagePoint, ...], name: str) -> None:
    n = len(points)
    area = sum(_orient((0.0, 0.0), points[i], points[(i + 1) % n]) for i in range(n)) / 2
    if abs(area) < 1e-6:
        raise ValueError(f"{name} has no area")
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_touch(points[i], points[i + 1], points[j], points[(j + 1) % n]):
                raise ValueError(f"{name} polygon must be simple")


class ImageLane(EvidenceModel):
    """One lane outlined in image pixels, with the evidence for what it allows.

    Unlike `LanePolygon`, an approach lane may list no turns: at 640x480 most arrows are hidden
    or unreadable, and an unknown turn is recorded as unknown (`turns_source` says why).
    """

    lane_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    arm: Arm = Field(description="compass leg from the junction (nearest of N/E/S/W)")
    direction: Literal["approach", "exit"]
    polygon_px: tuple[ImagePoint, ...] = Field(min_length=3, description="vertices in order")
    allowed_turns: tuple[Turn, ...]
    turns_source: str = Field(min_length=1, description="painted arrow, published lane code, ...")
    reference_lane: str | None = Field(None, description="matching published lane, if any")
    provenance: GeometryProvenance
    error_px: float | None = Field(None, ge=0, description="1-sigma boundary error, pixels")
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ImageLane:
        if self.direction == "exit" and self.allowed_turns:
            raise ValueError("exit lanes list no turns")
        _simple_polygon(self.polygon_px, f"lane {self.lane_id}")
        return self


class ImageLine(EvidenceModel):
    """A painted line or a kerb edge in image pixels."""

    line_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    arm: Arm
    role: ImageLineRole
    segment_px: tuple[ImagePoint, ImagePoint]
    painted: bool = Field(description="paint seen in the image (False: kerb or edge)")
    provenance: GeometryProvenance
    error_px: float | None = Field(None, ge=0)
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _valid(self) -> ImageLine:
        (u0, v0), (u1, v1) = self.segment_px
        if math.hypot(u1 - u0, v1 - v0) < 3.0:
            raise ValueError(f"line {self.line_id} is shorter than 3 px")
        return self


class ImageGeometry(EvidenceModel):
    """Lanes and lines of one junction in image pixels, measured on a stated source image."""

    geometry_id: str = Field(min_length=1)
    clip_ids: tuple[str, ...] = Field(min_length=1, description="clips sharing this camera view")
    source_image_sha256: Sha256
    image_width_px: int = Field(gt=0)
    image_height_px: int = Field(gt=0)
    usable_v_max_px: float = Field(gt=0, description="rows from here down are not road")
    drives_on: Literal["right"] = "right"
    lanes: tuple[ImageLane, ...]
    lines: tuple[ImageLine, ...]
    supersedes: str | None = Field(None, description="geometry_id of the version it replaces")
    notes: tuple[str, ...] = ()

    @field_validator("source_image_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)

    @model_validator(mode="after")
    def _valid(self) -> ImageGeometry:
        for kind, ids in (("lane", [x.lane_id for x in self.lanes]),
                          ("line", [x.line_id for x in self.lines])):  # fmt: skip
            if len(set(ids)) != len(ids):
                raise ValueError(f"{kind} ids must be unique")
        points = [p for lane in self.lanes for p in lane.polygon_px]
        points += [p for line in self.lines for p in line.segment_px]
        for u, v in points:
            if not (0 <= u <= self.image_width_px and 0 <= v <= self.usable_v_max_px):
                raise ValueError(f"point ({u}, {v}) is outside the usable image")
        for role in ("limit_line", "exit_line"):
            arms = [line.arm for line in self.lines if line.role == role]
            if len(set(arms)) != len(arms):
                raise ValueError(f"at most one {role} per arm")
        limits = {line.arm for line in self.lines if line.role == "limit_line"}
        for lane in self.lanes:
            if lane.direction == "approach" and lane.arm not in limits:
                raise ValueError(f"approach lane {lane.lane_id}: arm {lane.arm} has no limit line")
        return self


# --- Signal timeline (annotated, published or assumed; never inferred by a model) -----------

ARM_ORDER: tuple[Arm, ...] = ("N", "E", "S", "W")  # clockwise
SignalState = Literal["green", "yellow", "red", "unknown"]
# `trajectory-derived`: estimated by deterministic code from published vehicle trajectories
# (stop-line discharge times), not read from signal heads or a timing sheet.
SignalLabel = Literal["observed-annotated", "published", "trajectory-derived", "assumed"]
SignalHead = Literal["all", "left"]
HEAD_ORDER: tuple[SignalHead, ...] = ("all", "left")
_NEXT_SIGNAL_STATE = {"green": "yellow", "yellow": "red", "red": "green"}
_CROSSING_ARMS = (("N", "E"), ("N", "W"), ("S", "E"), ("S", "W"))
_OPPOSITE: dict[str, str] = {"N": "S", "S": "N", "E": "W", "W": "E"}


class SignalInterval(EvidenceModel):
    """One signal head of an arm showing one state over [start_s, end_s), in clip seconds.

    Head `all` controls the arm's movements; head `left`, where an arm has one, is a protected
    left-turn head and then controls the arm's left turns instead.
    """

    arm: Arm
    head: SignalHead = "all"
    state: SignalState
    start_s: float = Field(ge=0)
    end_s: float

    @model_validator(mode="after")
    def _positive(self) -> SignalInterval:
        if self.end_s <= self.start_s:
            raise ValueError("a signal interval must have positive length")
        return self


class SignalSecond(EvidenceModel):
    """The signal state of every timed arm at one whole second of the clip."""

    time_s: float = Field(ge=0)
    states: dict[Arm, SignalState]
    green_arms: tuple[Arm, ...] = Field(description="the phase: arms showing green")
    left_states: dict[Arm, SignalState] = Field(
        default_factory=dict, description="protected-left heads, for arms that have one"
    )


class SignalTimeline(EvidenceModel):
    """Signal states per arm over one clip.

    Each timed arm has an `all` head; it may also have a protected `left` head, which then controls
    its left turns. Arms at right angles may never show green or yellow at the same time, and a
    left head may not show green or yellow while the opposite arm's `all` head does. Per head,
    states cycle green -> yellow -> red -> green, except to or from `unknown` (head not visible,
    not annotated, or not estimable).
    """

    timeline_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    label: SignalLabel
    source: str = Field(min_length=1, description="who annotated it, or the publication")
    annotation_sha256: Sha256 | None = Field(None, description="SHA-256 of the annotation file")
    window_s: float = Field(gt=0, description="clip duration the intervals cover")
    intervals: tuple[SignalInterval, ...] = Field(min_length=1)

    @field_validator("annotation_sha256")
    @classmethod
    def _hash(cls, value: str | None) -> str | None:
        return None if value is None else _check_sha256(value)

    @model_validator(mode="after")
    def _valid(self) -> SignalTimeline:
        canonical = sorted(
            self.intervals,
            key=lambda i: (ARM_ORDER.index(i.arm), HEAD_ORDER.index(i.head), i.start_s),
        )
        if list(self.intervals) != canonical:
            raise ValueError(
                "intervals must be sorted by arm (N, E, S, W), then head (all, left), then start"
            )
        for arm, head in self.heads():
            own = [i for i in self.intervals if i.arm == arm and i.head == head]
            name = f"arm {arm}" if head == "all" else f"arm {arm} {head} head"
            if own[0].start_s != 0 or abs(own[-1].end_s - self.window_s) > 1e-6:
                raise ValueError(f"{name}: intervals must cover 0 to {self.window_s} s")
            for a, b in zip(own, own[1:], strict=False):
                if b.start_s != a.end_s:
                    raise ValueError(f"{name}: gap or overlap at {a.end_s} s")
                if a.state == b.state:
                    raise ValueError(f"{name}: {a.state} repeated at {a.end_s} s")
                known = "unknown" not in (a.state, b.state)
                if known and _NEXT_SIGNAL_STATE[a.state] != b.state:
                    raise ValueError(f"{name}: {a.state} cannot change to {b.state} at {a.end_s} s")
            if head == "left" and (arm, "all") not in self.heads():
                raise ValueError(f"{name}: an arm with a left head needs an 'all' head")
        active = [i for i in self.intervals if i.state in ("green", "yellow")]

        def label(i: SignalInterval) -> str:
            return i.arm if i.head == "all" else f"{i.arm} {i.head}"

        def check(first: list[SignalInterval], second: list[SignalInterval]) -> None:
            for a in first:
                for b in second:
                    if min(a.end_s, b.end_s) > max(a.start_s, b.start_s):
                        raise ValueError(
                            f"conflict: {label(a)} {a.state} and {label(b)} {b.state} overlap "
                            f"from {max(a.start_s, b.start_s)} s"
                        )

        for first, second in _CROSSING_ARMS:
            check([i for i in active if i.arm == first], [i for i in active if i.arm == second])
        for arm in ARM_ORDER:
            check(
                [i for i in active if i.arm == arm and i.head == "left"],
                [i for i in active if i.arm == _OPPOSITE[arm] and i.head == "all"],
            )
        return self

    def heads(self) -> list[tuple[Arm, SignalHead]]:
        """The (arm, head) pairs in the timeline, in canonical order."""
        return sorted(
            {(i.arm, i.head) for i in self.intervals},
            key=lambda k: (ARM_ORDER.index(k[0]), HEAD_ORDER.index(k[1])),
        )

    def state_at(self, arm: Arm, time_s: float, head: SignalHead = "all") -> SignalState | None:
        """A head's state at a clip time, or None if the timeline has no such head."""
        own = [i for i in self.intervals if i.arm == arm and i.head == head]
        for interval in own:
            if interval.start_s <= time_s < interval.end_s:
                return interval.state
        return own[-1].state if own and time_s == own[-1].end_s else None

    def per_second(self) -> tuple[SignalSecond, ...]:
        """The phase at every whole second of the clip."""
        heads = self.heads()
        arms = [arm for arm, head in heads if head == "all"]
        left_arms = [arm for arm, head in heads if head == "left"]
        seconds: list[SignalSecond] = []
        for second in range(math.ceil(self.window_s - 1e-9)):
            t = float(second)
            states = {arm: self.state_at(arm, t) for arm in arms}
            seconds.append(
                SignalSecond(
                    time_s=t,
                    states=states,
                    green_arms=tuple(arm for arm in arms if states[arm] == "green"),
                    left_states={arm: self.state_at(arm, t, "left") for arm in left_arms},
                )
            )
        return tuple(seconds)


# --- Traffic World Model (derived by cairoflow_perception.world_model) -----------------------


class GroundTrackPoint(EvidenceModel):
    """One track point on the ground: its contact point through the homography."""

    frame: FrameRef
    position_m: GroundPoint
    position_error_m: float = Field(ge=0, description="ground error of the assumed pixel error")
    speed_m_s: float | None = Field(None, ge=0, description="None when the track is too short")
    lane_id: str | None = Field(None, description="the one lane polygon containing the point")


class GroundTrack(EvidenceModel):
    track_id: str
    class_label: str
    points: tuple[GroundTrackPoint, ...]
    unprojected_frames: tuple[int, ...] = Field(description="points at or beyond the horizon")


class MovementTrack(EvidenceModel):
    """One vehicle counted in a movement, with the evidence for it."""

    track_id: str
    lane_id: str | None = Field(description="approach lane just before the stop line, if any")
    stop_line_time_s: float = Field(ge=0)
    exit_line_time_s: float = Field(ge=0)
    follows_markings: bool | None = Field(description="None when the lane is unknown")


class MovementCount(EvidenceModel):
    """Turning-movement count and stop-line flow for one approach and exit, over the clip."""

    approach: Arm
    exit_arm: Arm
    turn: Turn
    count_veh: int = Field(ge=0)
    flow_veh_per_h: float = Field(ge=0, description="stop-line crossings over the clip duration")
    flow_ci95_veh_per_h: tuple[float, float] = Field(description="exact Poisson 95% interval")
    marked_lane_ids: tuple[str, ...] = Field(
        description="approach lanes whose markings allow it; empty if only observed"
    )
    tracks: tuple[MovementTrack, ...]

    @model_validator(mode="after")
    def _consistent(self) -> MovementCount:
        if self.count_veh != len(self.tracks):
            raise ValueError("count_veh must equal the number of tracks")
        low, high = self.flow_ci95_veh_per_h
        if not low <= self.flow_veh_per_h <= high:
            raise ValueError("the flow must lie inside its interval")
        return self


class AmbiguousMovement(EvidenceModel):
    """A track that crossed only one of the two gates: listed, never counted."""

    track_id: str
    approach: Arm | None
    exit_arm: Arm | None
    reason: str = Field(min_length=1)


class QueueSample(EvidenceModel):
    """Stopped vehicles chained back from one stop line in one lane, at one frame."""

    frame: FrameRef
    arm: Arm
    lane_id: str
    queued_veh: int = Field(ge=0)
    length_m: float = Field(ge=0, description="stop line to the last queued contact point")
    length_error_m: float = Field(ge=0, description="ground error at the last queued vehicle")
    track_ids: tuple[str, ...] = Field(description="queued vehicles, nearest the stop line first")
    unknown_speed_track_ids: tuple[str, ...] = Field(description="in the lane, speed unknown")
    censored: bool = Field(
        False,
        description="the chain reaches the far end of the observed lane, so the queue may go on "
        "beyond it: the length and count are lower bounds",
    )

    @model_validator(mode="after")
    def _consistent(self) -> QueueSample:
        if self.queued_veh != len(self.track_ids):
            raise ValueError("queued_veh must equal the number of track ids")
        return self


class CameraTrackPoint(EvidenceModel):
    """One camera track point placed in the world model's frame (P1-1)."""

    frame: FrameRef = Field(description="the camera clip's frame")
    window_time_s: float = Field(description="the same instant in the world model's window")
    observation_id: str
    image_point_px: tuple[float, float] = Field(description="the SAM box centre, image pixels")
    position_m: GroundPoint = Field(description="the world model's junction frame")
    position_error_m: float = Field(ge=0, description="1-sigma ground error of the point")
    speed_m_s: float | None = Field(None, ge=0)
    lane_id: str | None = Field(description="the one lane polygon containing it, else None")
    lane_status: Literal["in_lane", "outside_lanes", "ambiguous"]
    near_lane_boundary: bool = Field(description="closer to its lane's edge than its error")
    confidence: float | None = Field(None, description="SAM's confidence, when supplied")


class CameraTrack(EvidenceModel):
    """One SAM-tracked vehicle on the ground: camera-derived, never merged with NGSIM ones."""

    track_id: str = Field(pattern=r"^cam[0-9]+:", description="e.g. 'cam2:car:t3'")
    sam_track_id: str
    sam_object_ids: tuple[str, ...] = Field(min_length=1)
    points: tuple[CameraTrackPoint, ...] = Field(min_length=1)
    lane_id: str | None = Field(description="the lane most of its lane points lie in, else None")
    lane_share: float | None = Field(description="share of its lane points in that lane")


class CameraGate(EvidenceModel):
    """The pre-registered gate's result for one camera layer."""

    gate_id: str
    gate_sha256: Sha256
    outcome: Literal["go", "no_go"]
    metrics: dict[str, float | int | None]
    checks: dict[str, bool]
    reasons: tuple[str, ...] = Field(description="why it is no-go; empty for go")


class CameraLayer(EvidenceModel):
    """Camera-derived entities of the world model (P1-1), kept apart from the trajectories'.

    Only lane-level quantities are derived from the camera (lane, presence, speed). Along-road
    ones (stop-line crossings, queue lengths) are not: the calibration is not accepted for
    along-road distances (ADR-018). Every count is a lower bound (SAM recall, hand check A3).
    """

    source: Literal["camera"] = "camera"
    label: str = Field(min_length=1)
    camera_clip_id: str
    camera_clip_sha256: Sha256
    sam_model: str
    sam_request_keys: tuple[Sha256, ...] = Field(min_length=1)
    tracks_sha256: Sha256
    calibration_spec_sha256: Sha256
    calibration_accepted: bool = Field(description="under ADR-018, for along-road distances")
    calibration_notes: tuple[str, ...]
    frame: str = Field(description="how image pixels reach the junction frame")
    time_sync: str
    window_s: tuple[float, float] = Field(description="the world model's seconds it covers")
    tracks: tuple[CameraTrack, ...]
    counts: dict[str, int] = Field(description="observations in, projected, placed, rejected")
    lane_presence: dict[str, int] = Field(description="lane -> distinct camera tracks seen in it")
    derived: tuple[str, ...]
    not_derived: tuple[str, ...]
    gate: CameraGate
    provenance: Provenance

    @model_validator(mode="after")
    def _go_only(self) -> CameraLayer:
        if self.gate.outcome != "go" or self.gate.reasons:
            raise ValueError("only a camera layer whose pre-registered gate is go can enter")
        if self.provenance.kind != "approximated":
            raise ValueError("camera-derived entities are approximated evidence")
        return self


class TrafficWorldModel(EvidenceModel):
    """Measurements derived from tracks and geometry; every value traceable.

    Tracks reach the ground either through a camera calibration (`calibration_spec_sha256`) or
    already as published ground positions (`ground_source_sha256`, the dataset file, which is
    then also the tracks input). Exactly one of the two is set. `camera`, when set, holds a
    second, camera-derived source (P1-1) beside a trajectory-built model; it is left out of the
    JSON when unset, so models made before it serialise and hash as they did.
    """

    clip_id: str
    geometry_id: str
    geometry_sha256: Sha256
    calibration_spec_sha256: Sha256 | None = None
    ground_source_sha256: Sha256 | None = None
    tracks_sha256: Sha256
    window_s: float = Field(gt=0, description="clip duration the flows are computed over")
    ground_tracks: tuple[GroundTrack, ...]
    movements: tuple[MovementCount, ...]
    ambiguous: tuple[AmbiguousMovement, ...]
    queues: tuple[QueueSample, ...] = Field(description="every approach lane, at a fixed interval")
    signal_timeline: SignalTimeline | None
    signal_per_second: tuple[SignalSecond, ...]
    end_of_red_queues: tuple[QueueSample, ...] = Field(
        description="each approach lane in the last frame before its red turns green"
    )
    signal_notes: tuple[str, ...] = Field(description="red ends that could not be sampled, and why")
    provenance: Provenance
    camera: CameraLayer | None = Field(None, description="camera-derived entities (P1-1)")

    @model_serializer(mode="wrap")
    def _omit_unset_camera(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if self.camera is None:
            data.pop("camera", None)
        return data

    @field_validator(
        "geometry_sha256", "calibration_spec_sha256", "ground_source_sha256", "tracks_sha256"
    )
    @classmethod
    def _hashes(cls, value: str | None) -> str | None:
        return None if value is None else _check_sha256(value)

    @model_validator(mode="after")
    def _one_anchor(self) -> TrafficWorldModel:
        if (self.calibration_spec_sha256 is None) == (self.ground_source_sha256 is None):
            raise ValueError("give exactly one frame anchor: a calibration or a ground dataset")
        return self
