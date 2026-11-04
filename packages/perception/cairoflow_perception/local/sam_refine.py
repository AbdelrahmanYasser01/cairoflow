"""Refine a finished analysis with detector-seeded SAM 3.1, as a new analysis.

A detector and a tracker give each vehicle a path, with gaps filled by interpolation. SAM is handed
those tracks as box prompts and follows them with a mask. Three things come out: a measured box for
rows that were only interpolated, a check on rows that were observed, and an outline a detector box
cannot give. This module plans the requests, judges what comes back and rebuilds the track rows;
scripts/cv_sam_refine.py sends them and writes the analysis.

Planning. A request follows at most 16 objects on one clip, and a clip is billed per frame, so
tracks that overlap in time share a short clip. A track longer than `segment_frames` is followed in
overlapping parts, each from its own seed, so an id has less time to drift. A seed is a row that is
observed, confident, fully inside the usable rows and clear of its neighbours.

Time. `analysis_frame_of_clip` maps a clip frame to an analysis frame by the clip's trim start. A
clip needs no lag and neither does the analysis's frame number: analysis frame k is the image behind
second start + k / fps, which is AVI frame `avi_index` of that second, and clip frame j of a clip
cut at t is AVI frame 10 t + j - 1, the image behind second t + j / 10 (local/frames.py). Both are
the one rule, `AVI_FRAME_LAG`; `frame_map_offset` measures it on pixels before anything is sent.

The held-out block before a window reaches into it by one image: the first frame of an even block
is, by the AVI's own clock, the last frame of an odd one. The analysis leaves that frame unread, but
a clip is uploaded whole, so `check_clip_window` refuses a clip that holds it.

Seed-forward rule. Tracking runs from the earliest prompt of a request for every object in it, so an
object can appear before its own seed. Nothing before an object's seed frame is ever used.

Judging. A SAM box is compared with the track's row at the same frame (`judge_row`). A box too far
from the track, of a very different size, or closer to another track is rejected and counted, and a
run of rejected observed rows means the id has left the vehicle, so the rest of that part is
rejected too. Observed detector rows are never moved: SAM can only confirm them. An interpolated row
that passes becomes SAM's box and stops being interpolated. A track may be extended past its last
row only within `extend_frames`, the time the tracker itself would coast a confirmed track, and only
while the box keeps to the track's own end velocity, keeps its size, stays in the usable image and
does not reach another track.

Output is model-generated evidence, not ground truth; ids are only reliable while an object stays in
frame.

Imports: numpy and the contract packages; OpenCV only inside the functions that draw on masks.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import numpy as np
from meta_sam_parser import SegmentationMaskRecord, decode_mask_to_raster

from cairoflow_contracts.analysis_record import (
    AnalysisRecord,
    ArtifactRef,
    StageReceipt,
    TrackEntry,
    TrackIndex,
)
from cairoflow_contracts.evidence import Observation, Provenance
from cairoflow_perception.analysis_store import (
    POINT_INTERPOLATED,
    POINT_SAM_REFINED,
    PX_SCALE,
    TrajectoryColumns,
)
from cairoflow_perception.ingest import DEFAULT_TARGET_FPS
from cairoflow_perception.local import analytics
from cairoflow_perception.local.analytics import PlacedTrack
from cairoflow_perception.local.frames import held_out_frames, plan_frames
from cairoflow_perception.local.scoring import AVI_FRAME_LAG, HeldOutError, avi_index, tenths
from cairoflow_perception.local.tracker import FilledTrack
from cairoflow_perception.sam_adapter import video_cost_usd
from cairoflow_perception.sam_seeded import MAX_OBJECTS_PER_REQUEST, SEED_NAMESPACE, Seed, clamp_box
from cairoflow_perception.site_int2 import Site

OUTLINES_FILE = "sam_outlines.json"
OUTLINES_FORMAT = "sam_outlines/1"
STAGE = "sam_refine"
TOOL = "sam-3.1"
Box = tuple[float, float, float, float]


class RefineError(RuntimeError):
    """The refinement cannot go on: an unsafe clip, or a plan that does not fit its bounds."""


@dataclass(frozen=True)
class RefineParams:
    """Pixels and frames of camera 2 at 10 fps. Set before any reply was read, except where the
    measurement record says otherwise."""

    segment_frames: int = 150  # a track longer than this is followed in parts
    segment_overlap_frames: int = 30  # parts overlap by this, more than the longest gap in the hero
    clip_max_frames: int = 200  # a longer clip gives an id more time to drift
    seed_min_conf: float = 0.5  # a seed is a row the detector was sure of
    seed_fallback_conf: float = 0.27  # the tracker's own high score: the last resort
    seed_edge_margin_px: float = 2.0  # a box this close to the edge is a cut-off vehicle
    seed_max_iou: float = 0.2  # a seed overlapping a neighbour more than this is ambiguous
    gate_px: float = 10.0  # under half a car length, and under a queue spacing
    gate_interpolated_px: float = 15.0  # an interpolated row is itself a guess
    size_ratio: float = 3.0  # box area against the track's, either way
    lost_after_misses: int = 5  # this many rejected observed rows in a row: the id has left
    extend_frames: int = 30  # the tracker's max_miss_s at 10 fps
    extend_min_conf: float = 0.5
    extend_max_step_px: float = 10.0  # the tracker's 6.6 px per frame, with room for box jitter
    extend_position_px: float = 6.0  # miss allowed against the track's own end velocity
    extend_accel_px_frame2: float = 0.25  # and how it widens: the tracker's 25 px/s^2
    extend_size_ratio: float = 2.0  # box area against the track's median, either way
    extend_fit_rows: int = 8  # end velocity is fitted on this many observed rows at most
    outline_every_frames: int = 5
    outline_epsilon_px: float = 0.8
    footprint_every_frames: int = 3
    footprint_min_masks: int = 5

    def as_parameters(self) -> dict[str, int | float]:
        return dict(vars(self))


# ---- the time map ------------------------------------------------------------------------------


def analysis_frame_of_clip(
    clip_frame: int, trim_start_s: float, source_start_s: float, fps: float
) -> int:
    """The analysis frame a clip frame shows: both count from their own start at the same rate."""
    return clip_frame + round((trim_start_s - source_start_s) * fps)


def clip_frame_of_analysis(
    frame: int, trim_start_s: float, source_start_s: float, fps: float
) -> int:
    return frame - round((trim_start_s - source_start_s) * fps)


def avi_frame_of_clip(clip_frame: int, trim_start_s: float) -> int:
    """The AVI frame behind a clip frame: the cut lands `AVI_FRAME_LAG` frames early."""
    return tenths(trim_start_s) + clip_frame - AVI_FRAME_LAG


def avi_frame_of_analysis(frame: int, source_start_s: float, fps: float) -> int:
    return avi_index(source_start_s + frame / fps)


def frame_map_offset(difference_of: Callable[[int], float]) -> int:
    """Which of -1, 0, +1 AVI frames from the expected one a clip's frames really are.
    `difference_of(offset)` gives the mean pixel difference at that offset, over sampled frames."""
    return min((-1, 0, 1), key=difference_of)


def check_clip_window(
    trim_start_s: float, duration_s: float, fps: float, source_start_s: float, end_s: float
) -> None:
    """Raises unless every image of this clip is inside the analysis and readable under the block
    rule. A window's first frame is left unread by an analysis, but a clip is sent whole."""
    if trim_start_s < source_start_s or trim_start_s + duration_s > end_s + 1e-9:
        raise RefineError(
            f"clip {trim_start_s:g}+{duration_s:g} s is outside {source_start_s:g}-{end_s:g} s"
        )
    edge = held_out_frames(plan_frames(fps, trim_start_s, duration_s))  # raises on an odd block
    if edge:
        raise HeldOutError(
            f"clip {trim_start_s:g}+{duration_s:g} s would send {len(edge)} held-out image(s)"
        )


def first_clear_second(source_start_s: float, end_s: float, fps: float) -> float:
    """The first whole second a clip may start on. A whole second keeps to the cut that was checked
    against the AVI frame by frame (scripts/cv_bakeoff.py --check-frame-map)."""
    t = float(math.ceil(source_start_s))
    while t < end_s:
        try:
            check_clip_window(t, 1.0, fps, source_start_s, end_s)
        except HeldOutError:
            t += 1.0
            continue
        return t
    raise RefineError(f"no clip can start inside {source_start_s:g}-{end_s:g} s")


# ---- tracks as rows ----------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackRows:
    """One track's rows in real units; row i is analysis frame start_frame + i."""

    track_id: int
    start_frame: int
    x: np.ndarray
    y: np.ndarray
    w: np.ndarray
    h: np.ndarray
    conf: np.ndarray
    interpolated: np.ndarray

    def __len__(self) -> int:
        return len(self.x)

    @property
    def end_frame(self) -> int:
        return self.start_frame + len(self) - 1

    def row(self, frame: int) -> int:
        return frame - self.start_frame

    def box(self, frame: int) -> Box:
        i = self.row(frame)
        half_w, half_h = float(self.w[i]) / 2, float(self.h[i]) / 2
        cx, cy = float(self.x[i]), float(self.y[i])
        return cx - half_w, cy - half_h, cx + half_w, cy + half_h


def rows_of(index: TrackIndex, columns: TrajectoryColumns) -> list[TrackRows]:
    """The tracks of a stored analysis. Stored values are already on the 1/16 px grid."""
    out = []
    for entry in index.tracks:
        rows = slice(entry.first_row, entry.first_row + entry.n)
        out.append(
            TrackRows(
                entry.track_id,
                entry.start_frame,
                columns.x[rows].astype(float) * PX_SCALE,
                columns.y[rows].astype(float) * PX_SCALE,
                columns.w[rows].astype(float) * PX_SCALE,
                columns.h[rows].astype(float) * PX_SCALE,
                columns.conf[rows].astype(float) / 255,
                (columns.point_flags[rows] & POINT_INTERPOLATED) != 0,
            )
        )
    return out


def boxes_by_frame(tracks: Iterable[TrackRows]) -> dict[int, list[tuple[int, Box, bool]]]:
    """(track id, box, observed) of every row, by frame: what a row's neighbours are."""
    out: dict[int, list[tuple[int, Box, bool]]] = defaultdict(list)
    for track in tracks:
        for i in range(len(track)):
            frame = track.start_frame + i
            out[frame].append((track.track_id, track.box(frame), not bool(track.interpolated[i])))
    return out


def iou(a: Box, b: Box) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    overlap = max(0.0, right - left) * max(0.0, bottom - top)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap
    return overlap / union if union > 0 else 0.0


def centre(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---- planning ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    """A part of one track, followed from its seed. Frames are analysis frames, inclusive."""

    track_id: int
    seed_frame: int
    end_frame: int
    box_xyxy_px: tuple[int, int, int, int]
    seed_conf: float
    fallback: bool  # no row reached seed_min_conf, so the best row was used
    tail: bool  # this part reaches the track's last row


@dataclass(frozen=True)
class Cohort:
    """One request: one clip and its objects, numbered from 1 in `segments` order."""

    index: int
    trim_start_s: float
    duration_s: float
    segments: tuple[Segment, ...]

    @property
    def frames(self) -> int:
        return round(self.duration_s * DEFAULT_TARGET_FPS)

    @property
    def cost_usd(self) -> float:
        return video_cost_usd(self.frames)

    def seeds(self, source_start_s: float, fps: float) -> list[Seed]:
        return [
            Seed(
                number,
                clip_frame_of_analysis(s.seed_frame, self.trim_start_s, source_start_s, fps),
                s.box_xyxy_px,
            )
            for number, s in enumerate(self.segments, start=1)
        ]


@dataclass(frozen=True)
class RefinePlan:
    cohorts: tuple[Cohort, ...]
    unseeded: tuple[tuple[int, str], ...]  # (track id, why), parts with no usable seed
    dropped: tuple[Cohort, ...] = ()  # left out to stay inside the budget

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.cohorts)

    @property
    def frames(self) -> int:
        return sum(max(c.frames, 50) for c in self.cohorts)


def segment_bounds(first: int, last: int, length: int, overlap: int) -> list[tuple[int, int]]:
    """Parts of [first, last], inclusive, each at most `length` long, each overlapping the last."""
    out = []
    start = first
    while True:
        end = min(start + length - 1, last)
        out.append((start, end))
        if end == last:
            return out
        start = end - overlap + 1


def _clear_of_neighbours(
    track_id: int, box: Box, frame: int, neighbours: Mapping[int, Sequence], limit: float
) -> bool:
    return all(
        other == track_id or iou(box, other_box) <= limit
        for other, other_box, _ in neighbours.get(frame, ())
    )


def choose_seed(
    track: TrackRows,
    first: int,
    last: int,
    neighbours: Mapping[int, Sequence],
    *,
    earliest_frame: int,
    valid_below_row_px: int,
    width_px: int,
    height_px: int,
    params: RefineParams,
) -> tuple[int, float, bool] | None:
    """(frame, confidence, fallback) of the earliest good row of [first, last], else None. Earliest
    because nothing before a seed can be refined. The fallback is the most confident row left, which
    may touch the image's side: a queued car cut off by the frame is still a vehicle to follow."""
    margin = params.seed_edge_margin_px
    usable = []
    for frame in range(max(first, earliest_frame), last + 1):
        i = track.row(frame)
        if track.interpolated[i]:
            continue
        x1, y1, x2, y2 = box = track.box(frame)
        if y1 < margin or y2 >= valid_below_row_px or y2 > height_px - margin:
            continue
        if not _clear_of_neighbours(track.track_id, box, frame, neighbours, params.seed_max_iou):
            continue
        conf = float(track.conf[i])
        whole = x1 >= margin and x2 <= width_px - margin  # a car at the side edge is cut off
        if conf >= params.seed_min_conf and whole:
            return frame, conf, False
        if conf >= params.seed_fallback_conf:
            usable.append((conf, -frame))
    if not usable:
        return None
    conf, frame = max(usable)
    return -frame, conf, True


def plan_segments(
    tracks: Sequence[TrackRows],
    *,
    earliest_frame: int,
    valid_below_row_px: int,
    width_px: int,
    height_px: int,
    params: RefineParams,
) -> tuple[list[Segment], list[tuple[int, str]]]:
    neighbours = boxes_by_frame(tracks)
    segments: list[Segment] = []
    unseeded: list[tuple[int, str]] = []
    for track in tracks:
        for first, last in segment_bounds(
            track.start_frame, track.end_frame, params.segment_frames,
            params.segment_overlap_frames,
        ):  # fmt: skip
            found = choose_seed(
                track, first, last, neighbours, earliest_frame=earliest_frame,
                valid_below_row_px=valid_below_row_px, width_px=width_px, height_px=height_px,
                params=params,
            )  # fmt: skip
            if found is None:
                unseeded.append((track.track_id, f"no usable seed in frames {first}-{last}"))
                continue
            frame, conf, fallback = found
            box = clamp_box(track.box(frame), width_px, height_px)
            if box is None:
                unseeded.append((track.track_id, f"seed box at frame {frame} is empty"))
                continue
            segments.append(
                Segment(track.track_id, frame, last, box, conf, fallback, last == track.end_frame)
            )
    return segments, unseeded


def _clip_for(
    first_frame: int,
    last_frame: int,
    *,
    source_start_s: float,
    fps: float,
    first_second: float,
    end_s: float,
) -> tuple[float, float]:
    """Whole-second trim start and duration covering these frames, at least the 5 s billed."""
    start = max(first_second, math.floor(source_start_s + first_frame / fps))
    end = min(end_s, math.ceil(source_start_s + (last_frame + 1) / fps))
    if end - start < 5.0:
        end = min(end_s, start + 5.0)
        start = max(first_second, end - 5.0)
    return start, end - start


def pack_cohorts(
    segments: Sequence[Segment],
    *,
    source_start_s: float,
    fps: float,
    first_second: float,
    end_s: float,
    frame_count: int,
    params: RefineParams,
) -> list[Cohort]:
    """First fit in seed order: a part joins the first open request with room, whose clip stays
    short enough, that does not already follow the same track (one vehicle, one object)."""
    open_cohorts: list[list[Segment]] = []

    def reach(segment: Segment) -> int:
        return min(
            frame_count - 1, segment.end_frame + (params.extend_frames if segment.tail else 0)
        )

    for segment in sorted(segments, key=lambda s: (s.seed_frame, s.track_id)):
        for group in open_cohorts:
            start = min(s.seed_frame for s in group)
            stop = max(reach(segment), *(reach(s) for s in group))
            fits = stop - start + 1 <= params.clip_max_frames
            if (
                len(group) < MAX_OBJECTS_PER_REQUEST
                and fits
                and all(s.track_id != segment.track_id for s in group)
            ):
                group.append(segment)
                break
        else:
            open_cohorts.append([segment])

    cohorts = []
    for number, group in enumerate(open_cohorts):
        first = min(s.seed_frame for s in group)
        last = max(reach(s) for s in group)
        start, duration = _clip_for(
            first, last, source_start_s=source_start_s, fps=fps, first_second=first_second,
            end_s=end_s,
        )  # fmt: skip
        check_clip_window(start, duration, fps, source_start_s, end_s)
        cohorts.append(Cohort(number, start, duration, tuple(group)))
    return cohorts


def rows_in(cohort: Cohort, frames: range) -> int:
    """How many track rows of `frames` this request can reach: the priority of a cohort."""
    return sum(
        max(0, min(s.end_frame, frames[-1]) - max(s.seed_frame, frames[0]) + 1)
        for s in cohort.segments
    )


def fit_budget(cohorts: Sequence[Cohort], budget_usd: float, priority: range) -> RefinePlan:
    """Keep the requests that reach most rows of `priority` while they fit `budget_usd`."""
    ranked = sorted(cohorts, key=lambda c: (-rows_in(c, priority), c.index))
    kept: list[Cohort] = []
    dropped: list[Cohort] = []
    spent = 0.0
    for cohort in ranked:
        if spent + cohort.cost_usd <= budget_usd + 1e-9:
            kept.append(cohort)
            spent += cohort.cost_usd
        else:
            dropped.append(cohort)
    return RefinePlan(tuple(kept), (), tuple(dropped))


def plan_refinement(
    tracks: Sequence[TrackRows],
    *,
    source_start_s: float,
    fps: float,
    frame_count: int,
    valid_below_row_px: int,
    width_px: int,
    height_px: int,
    budget_usd: float,
    priority: range,
    params: RefineParams,
) -> RefinePlan:
    end_s = source_start_s + frame_count / fps
    first_second = first_clear_second(source_start_s, end_s, fps)
    earliest = clip_offset(first_second, source_start_s, fps)
    segments, unseeded = plan_segments(
        tracks, earliest_frame=earliest, valid_below_row_px=valid_below_row_px,
        width_px=width_px, height_px=height_px, params=params,
    )  # fmt: skip
    cohorts = pack_cohorts(
        segments, source_start_s=source_start_s, fps=fps, first_second=first_second, end_s=end_s,
        frame_count=frame_count, params=params,
    )  # fmt: skip
    fitted = fit_budget(cohorts, budget_usd, priority)
    return replace(fitted, unseeded=tuple(unseeded))


def clip_offset(trim_start_s: float, source_start_s: float, fps: float) -> int:
    return analysis_frame_of_clip(0, trim_start_s, source_start_s, fps)


# ---- what SAM returned -------------------------------------------------------------------------


@dataclass(frozen=True)
class SamRow:
    """One SAM box, on analysis frames, from one object of one request."""

    frame: int
    box: Box
    confidence: float | None


@dataclass(frozen=True)
class Followed:
    """One part of a track and what SAM returned for it, from its seed frame on."""

    cohort: int
    object_id: str  # as the stream reports it, without the namespace
    segment: Segment
    rows: Mapping[int, SamRow]


def sam_rows(
    observations: Iterable[Observation],
    cohort: Cohort,
    *,
    source_start_s: float,
    fps: float,
) -> list[Followed]:
    """Split a reply by object. The seed-forward rule is applied here, once, for everything after:
    tracking starts from the earliest prompt, so a late seed's object has boxes before its seed."""
    prefix = f"{SEED_NAMESPACE}:"
    found: dict[str, dict[int, SamRow]] = defaultdict(dict)
    for observation in observations:
        object_id = observation.object_id.removeprefix(prefix)
        frame = analysis_frame_of_clip(
            observation.frame.frame_index, cohort.trim_start_s, source_start_s, fps
        )
        found[object_id][frame] = SamRow(
            frame, tuple(float(v) for v in observation.bbox_xyxy_px), observation.confidence
        )
    out = []
    for number, segment in enumerate(cohort.segments, start=1):
        object_id = str(number)
        rows = {
            frame: row
            for frame, row in found.get(object_id, {}).items()
            if frame >= segment.seed_frame
        }
        out.append(Followed(cohort.index, object_id, segment, rows))
    return out


# ---- judging -----------------------------------------------------------------------------------

OK = "ok"
FAR = "far"
SIZE = "size"
NEIGHBOUR = "neighbour"
LOST = "lost"


def judge_row(
    track: TrackRows,
    frame: int,
    sam: Box,
    neighbours: Mapping[int, Sequence],
    params: RefineParams,
) -> str:
    """OK, or why this box does not belong to this track at this frame."""
    i = track.row(frame)
    interpolated = bool(track.interpolated[i])
    here = centre(track.box(frame))
    there = centre(sam)
    away = distance(here, there)
    if away > (params.gate_interpolated_px if interpolated else params.gate_px):
        return FAR
    area = max(float(track.w[i] * track.h[i]), 1.0)
    sam_area = max((sam[2] - sam[0]) * (sam[3] - sam[1]), 1.0)
    if max(sam_area / area, area / sam_area) > params.size_ratio:
        return SIZE
    for other, box, observed in neighbours.get(frame, ()):
        if other != track.track_id and observed and distance(centre(box), there) < away:
            return NEIGHBOUR
    return OK


def judge_part(
    track: TrackRows,
    followed: Followed,
    neighbours: Mapping[int, Sequence],
    params: RefineParams,
) -> dict[int, str]:
    """Verdict on each of this part's rows inside the track. Once `lost_after_misses` observed rows
    in a row fail, the id has left the vehicle and the rest of the part is LOST."""
    verdicts: dict[int, str] = {}
    misses = 0
    lost = False
    for frame in sorted(followed.rows):
        if frame < followed.segment.seed_frame or frame > track.end_frame:
            continue
        if frame < track.start_frame:
            continue
        if lost:
            verdicts[frame] = LOST
            continue
        verdict = judge_row(track, frame, followed.rows[frame].box, neighbours, params)
        verdicts[frame] = verdict
        if not track.interpolated[track.row(frame)]:
            misses = misses + 1 if verdict != OK else 0
            lost = misses >= params.lost_after_misses
    return verdicts


def end_velocity(track: TrackRows, rows: int) -> tuple[float, float]:
    """Pixels per frame over the track's last observed rows; (0, 0) with fewer than two."""
    observed = np.flatnonzero(~track.interpolated)[-rows:]
    if len(observed) < 2:
        return 0.0, 0.0
    first, last = int(observed[0]), int(observed[-1])
    span = last - first
    return (
        float(track.x[last] - track.x[first]) / span,
        float(track.y[last] - track.y[first]) / span,
    )


def extension_rows(
    track: TrackRows,
    followed: Followed,
    neighbours: Mapping[int, Sequence],
    *,
    frame_count: int,
    valid_below_row_px: int,
    width_px: int,
    params: RefineParams,
) -> tuple[list[SamRow], str]:
    """Rows past the track's last, and why they stopped. Only a part whose own last row was accepted
    may extend, and every row must look like the same vehicle moving on: where the track's own end
    velocity says, within what a car can brake or pull away, and about its size. A mask left behind
    on a tree when the car went under it stops moving at once, and that ends the extension."""
    last = track.end_frame
    out: list[SamRow] = []
    origin = previous = centre(track.box(last))
    vx, vy = end_velocity(track, params.extend_fit_rows)
    observed = ~track.interpolated
    area = float(np.median((track.w * track.h)[observed])) if observed.any() else 0.0
    for frame in range(last + 1, min(last + params.extend_frames, frame_count - 1) + 1):
        row = followed.rows.get(frame)
        if row is None:
            return out, "sam_gone"
        here = centre(row.box)
        if row.confidence is None or row.confidence < params.extend_min_conf:
            return out, "low_confidence"
        if distance(here, previous) > params.extend_max_step_px:
            return out, "jump"
        k = frame - last
        allowed = params.extend_position_px + 0.5 * params.extend_accel_px_frame2 * k * k
        if distance(here, (origin[0] + vx * k, origin[1] + vy * k)) > allowed:
            return out, "motion"
        sam_area = max((row.box[2] - row.box[0]) * (row.box[3] - row.box[1]), 1.0)
        if max(sam_area / max(area, 1.0), max(area, 1.0) / sam_area) > params.extend_size_ratio:
            return out, "size"
        margin = params.seed_edge_margin_px
        x1, _, x2, y2 = row.box
        if x1 < margin or x2 > width_px - margin or y2 >= valid_below_row_px:
            return out, "left_usable_image"
        reached = [
            other
            for other, box, _ in neighbours.get(frame, ())
            if other != track.track_id and distance(centre(box), here) <= params.gate_px
        ]
        if reached:
            return out, "reached_another_track"
        out.append(row)
        previous = here
    return out, "limit" if out else "none"


@dataclass(frozen=True)
class RefinedTrack:
    """A track after SAM: new rows, which rows carry SAM's box, and where each accepted mask is."""

    track: TrackRows
    refined: np.ndarray  # bool: this row's box is SAM's
    accepted: Mapping[int, tuple[int, str]]  # frame -> (cohort, object id)
    counts: Counter
    distances_px: tuple[float, ...]  # SAM to detector, on observed rows that were judged


def refine_track(
    track: TrackRows,
    parts: Sequence[Followed],
    neighbours: Mapping[int, Sequence],
    *,
    frame_count: int,
    valid_below_row_px: int,
    width_px: int,
    params: RefineParams,
    allow_extension: bool = True,
) -> RefinedTrack:
    """Apply what SAM returned for this track's parts. The most recently seeded part that accepts a
    row decides it, because it has had the least time to drift."""
    counts: Counter = Counter()
    ordered = sorted(parts, key=lambda p: p.segment.seed_frame, reverse=True)
    verdicts = [judge_part(track, part, neighbours, params) for part in ordered]
    x, y, w, h = track.x.copy(), track.y.copy(), track.w.copy(), track.h.copy()
    conf, interpolated = track.conf.copy(), track.interpolated.copy()
    refined = np.zeros(len(track), bool)
    accepted: dict[int, tuple[int, str]] = {}
    distances = []
    for i in range(len(track)):
        frame = track.start_frame + i
        was_interpolated = bool(track.interpolated[i])
        seen = [
            (part, verdict[frame])
            for part, verdict in zip(ordered, verdicts, strict=True)
            if frame in verdict
        ]
        if not seen:
            counts["unreached_interpolated" if was_interpolated else "unreached_observed"] += 1
            continue
        chosen = next((part for part, verdict in seen if verdict == OK), None)
        if not was_interpolated:
            distances.append(distance(centre(seen[0][0].rows[frame].box), centre(track.box(frame))))
        if chosen is None:
            counts["rejected"] += 1
            counts[f"rejected_{seen[0][1]}"] += 1
            counts["rejected_interpolated" if was_interpolated else "rejected_observed"] += 1
            continue
        accepted[frame] = (chosen.cohort, chosen.object_id)
        if not was_interpolated:
            counts["confirmed"] += 1
            continue
        row = chosen.rows[frame]
        x[i], y[i] = centre(row.box)
        w[i], h[i] = row.box[2] - row.box[0], row.box[3] - row.box[1]
        conf[i] = row.confidence if row.confidence is not None else conf[i]
        interpolated[i] = False
        refined[i] = True
        counts["replaced"] += 1

    extra: list[SamRow] = []
    last = track.end_frame
    tail = next((p for p in ordered if p.segment.tail), None)
    if allow_extension and tail is not None and last in accepted:
        extra, why = extension_rows(
            track, tail, neighbours, frame_count=frame_count,
            valid_below_row_px=valid_below_row_px, width_px=width_px, params=params,
        )  # fmt: skip
        counts[f"extension_stopped_{why}"] += 1
    if extra:
        boxes = np.array([row.box for row in extra])
        x = np.concatenate([x, (boxes[:, 0] + boxes[:, 2]) / 2])
        y = np.concatenate([y, (boxes[:, 1] + boxes[:, 3]) / 2])
        w = np.concatenate([w, boxes[:, 2] - boxes[:, 0]])
        h = np.concatenate([h, boxes[:, 3] - boxes[:, 1]])
        conf = np.concatenate([conf, [float(row.confidence or 0) for row in extra]])
        interpolated = np.concatenate([interpolated, np.zeros(len(extra), bool)])
        refined = np.concatenate([refined, np.ones(len(extra), bool)])
        accepted.update({row.frame: (tail.cohort, tail.object_id) for row in extra})
        counts["extended"] = len(extra)
    new = TrackRows(track.track_id, track.start_frame, x, y, w, h, conf, interpolated)
    return RefinedTrack(new, refined, accepted, counts, tuple(distances))


def refine_all(
    tracks: Sequence[TrackRows],
    followed: Sequence[Followed],
    *,
    frame_count: int,
    valid_below_row_px: int,
    width_px: int,
    params: RefineParams,
    allow_extension: bool = True,
) -> list[RefinedTrack]:
    """Every track, in order. A track no reply reached comes back with its rows unchanged."""
    neighbours = boxes_by_frame(tracks)
    by_track: dict[int, list[Followed]] = defaultdict(list)
    for part in followed:
        by_track[part.segment.track_id].append(part)
    return [
        refine_track(
            track, by_track.get(track.track_id, ()), neighbours, frame_count=frame_count,
            valid_below_row_px=valid_below_row_px, width_px=width_px, params=params,
            allow_extension=allow_extension,
        )
        for track in tracks
    ]  # fmt: skip


# ---- masks -------------------------------------------------------------------------------------


def mask_array(record: SegmentationMaskRecord) -> np.ndarray:
    """A mask's box-local raster as a bool array, rows first."""
    raster = decode_mask_to_raster(record.mask)
    shape = (record.mask.height, record.mask.width)
    return np.frombuffer(raster, dtype=np.uint8).reshape(shape) > 0


def mask_outline(
    record: SegmentationMaskRecord, epsilon_px: float
) -> tuple[np.ndarray, np.ndarray] | None:
    """(simplified polygon, full contour) of the mask's largest part, in image pixels with (0, 0)
    the top-left corner of the top-left pixel. None for an empty mask."""
    import cv2  # noqa: PLC0415 - only what draws on masks needs OpenCV

    mask = mask_array(record)
    if not mask.any():
        return None
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    largest = max(contours, key=cv2.contourArea)
    bounds = record.bounds
    scale_x = (bounds.right - bounds.left) / record.mask.width
    scale_y = (bounds.bottom - bounds.top) / record.mask.height

    def to_image(points: np.ndarray) -> np.ndarray:
        flat = points.reshape(-1, 2).astype(float) + 0.5
        return flat * (scale_x, scale_y) + (bounds.left, bounds.top)

    simplified = cv2.approxPolyDP(largest, epsilon_px, True)
    return to_image(simplified), to_image(largest)


def footprint_m(contour_px: np.ndarray, site: Site) -> tuple[float, float] | None:
    """Length and width in metres of the smallest rectangle round a mask's ground outline. Pixel
    corners, not centres, so a 20 px car is not a pixel short. Approximated, like every ground value
    here: the homography assumes a flat road, and along-road distances from it are not accepted."""
    import cv2  # noqa: PLC0415

    corners = np.concatenate(
        [contour_px + offset for offset in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))]
    )
    ground = site.project_to_junction(corners[:, 0], corners[:, 1])
    if not np.isfinite(ground).all():
        return None
    (_, _), (a, b), _ = cv2.minAreaRect(ground.astype(np.float32))
    return (float(max(a, b)), float(min(a, b))) if min(a, b) > 0 else None


def touches_edge(record: SegmentationMaskRecord, width_px: int, valid_below_row_px: int) -> bool:
    """A mask cut off by the image shows part of a vehicle: no use for its size."""
    bounds = record.bounds
    return (
        bounds.left <= 1 or bounds.top <= 1 or bounds.right >= width_px - 1
        or bounds.bottom >= valid_below_row_px
    )  # fmt: skip


@dataclass(frozen=True)
class MaskIndex:
    """Mask records by (cohort, object id, analysis frame)."""

    masks: Mapping[tuple[int, str, int], SegmentationMaskRecord]

    def of(self, cohort: int, object_id: str, frame: int) -> SegmentationMaskRecord | None:
        return self.masks.get((cohort, object_id, frame))


def index_masks(
    cohort: Cohort,
    records: Mapping[tuple[str, int], SegmentationMaskRecord],
    *,
    source_start_s: float,
    fps: float,
) -> dict[tuple[int, str, int], SegmentationMaskRecord]:
    return {
        (
            cohort.index,
            object_id,
            analysis_frame_of_clip(clip_frame, cohort.trim_start_s, source_start_s, fps),
        ): record
        for (object_id, clip_frame), record in records.items()
    }


def outlines_payload(
    refined: Sequence[RefinedTrack],
    masks: MaskIndex,
    *,
    analysis_id: str,
    params: RefineParams,
) -> dict[str, Any]:
    """Simplified outlines of accepted masks every `outline_every_frames` frames: derived geometry,
    no pixels. A rejected mask has no outline here: it was not this vehicle."""
    entries = []
    for item in refined:
        for frame in sorted(item.accepted):
            if frame % params.outline_every_frames:
                continue
            cohort, object_id = item.accepted[frame]
            record = masks.of(cohort, object_id, frame)
            found = None if record is None else mask_outline(record, params.outline_epsilon_px)
            if found is None or len(found[0]) < 3:
                continue
            polygon = [[round(float(a), 1), round(float(b), 1)] for a, b in found[0]]
            entries.append({"track_id": item.track.track_id, "frame": frame, "polygon": polygon})
    return {
        "format": OUTLINES_FORMAT,
        "analysis_id": analysis_id,
        "label": "model_generated: SAM 3.1 masks, simplified; not ground truth",
        "coordinates": "image pixels, (0, 0) the top-left corner of the image",
        "frame": "analysis frame",
        "every_frames": params.outline_every_frames,
        "epsilon_px": params.outline_epsilon_px,
        "entries": entries,
    }


def outlines_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode()


@dataclass(frozen=True)
class Footprint:
    """A track's size from its masks: medians over the masks that were not cut off."""

    track_id: int
    length_m: float
    width_m: float
    masks: int


def track_footprint(
    item: RefinedTrack,
    masks: MaskIndex,
    site: Site,
    *,
    width_px: int,
    valid_below_row_px: int,
    params: RefineParams,
) -> Footprint | None:
    sizes = []
    for frame in sorted(item.accepted):
        if frame % params.footprint_every_frames:
            continue
        cohort, object_id = item.accepted[frame]
        record = masks.of(cohort, object_id, frame)
        if record is None or touches_edge(record, width_px, valid_below_row_px):
            continue
        found = mask_outline(record, params.outline_epsilon_px)
        size = None if found is None else footprint_m(found[1], site)
        if size is not None:
            sizes.append(size)
    if len(sizes) < params.footprint_min_masks:
        return None
    lengths, widths = zip(*sizes, strict=True)
    return Footprint(
        item.track.track_id,
        round(float(np.median(lengths)), 2),
        round(float(np.median(widths)), 2),
        len(sizes),
    )


# ---- the new analysis --------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestOutcome:
    """What became of one planned request."""

    cohort: Cohort
    status: str  # "ok", "failed", "not_sent"
    reason: str | None = None
    request_key: str | None = None
    frames_billed: int | None = None
    cost_usd: float = 0.0
    from_cache: bool = False
    elapsed_s: float | None = None
    observations: int = 0
    tracks: tuple[int, ...] = ()


def filled(track: TrackRows) -> FilledTrack:
    """A `TrackRows` as the analytics read it. Labels are not stored, so a class is carried over
    from the parent's entry rather than voted again (`carry_class`)."""
    return FilledTrack(
        track.track_id, track.start_frame, track.x, track.y, track.w, track.h, track.conf,
        track.interpolated, {},
    )  # fmt: skip


def carry_class(placed: PlacedTrack, entry: TrackEntry) -> PlacedTrack:
    return replace(
        placed,
        vehicle_class=entry.vehicle_class,
        class_source=entry.class_source,
        class_confidence=entry.class_confidence,
    )


def place_all(
    refined: Sequence[RefinedTrack],
    entries: Sequence[TrackEntry],
    site: Site | None,
    *,
    frame_dt_s: float,
    valid_below_row_px: int,
    image_size_px: tuple[int, int],
) -> list[PlacedTrack]:
    """Run the existing placement on the refined rows, then mark SAM's rows."""
    placed = []
    for item, entry in zip(refined, entries, strict=True):
        p = analytics.place_track(
            filled(item.track), site, frame_dt_s, valid_below_row_px=valid_below_row_px,
            image_size_px=image_size_px,
        )  # fmt: skip
        flags = p.flags | np.where(item.refined, POINT_SAM_REFINED, 0).astype("u1")
        placed.append(carry_class(replace(p, flags=flags), entry))
    return placed


def track_entries(
    placed: Sequence[PlacedTrack],
    refined: Sequence[RefinedTrack],
    footprints: Mapping[int, Footprint],
) -> list[TrackEntry]:
    """`sam_refined` is a track with at least one row carrying SAM's box: one it only confirmed was
    not changed. A mask footprint replaces a placed size only where the caller passes one."""
    out = []
    first_row = 0
    for p, item in zip(placed, refined, strict=True):
        size = footprints.get(p.track.track_id)
        out.append(
            TrackEntry(
                track_id=p.track.track_id,
                first_row=first_row,
                start_frame=p.track.start_frame,
                n=len(p.track),
                vehicle_class=p.vehicle_class,
                class_source=p.class_source,
                class_confidence=p.class_confidence,
                length_m=size.length_m if size else p.length_m,
                width_m=size.width_m if size else p.width_m,
                approach=p.movement.approach,
                exit_arm=p.movement.exit_arm,
                turn=p.movement.turn,
                status=p.status,
                sam_refined=bool(item.refined.any()),
            )
        )
        first_row += len(p.track)
    return out


def total_counts(refined: Iterable[RefinedTrack]) -> Counter:
    total: Counter = Counter()
    for item in refined:
        total.update(item.counts)
    return total


@dataclass(frozen=True)
class RefinedAnalysis:
    record: AnalysisRecord
    entries: list[TrackEntry]
    columns: TrajectoryColumns
    extras: list[tuple[ArtifactRef, bytes | None]]


def _sam_disclosure(
    counts: Mapping[str, int],
    outcomes: Sequence[RequestOutcome],
    followed_tracks: int,
    refined_tracks: int,
    total_tracks: int,
    class_note: str | None,
) -> str:
    sent = [o for o in outcomes if o.status == "ok"]
    failed = [o for o in outcomes if o.status != "ok"]
    text = (
        f"SAM 3.1 was given this analysis's own tracks as box prompts in {len(sent)} requests and "
        f"followed {followed_tracks} of {total_tracks} tracks. It confirmed "
        f"{counts.get('confirmed', 0)} observed rows; on {refined_tracks} tracks it replaced "
        f"{counts.get('replaced', 0)} interpolated rows with its own box and added "
        f"{counts.get('extended', 0)} rows past a track's end; {counts.get('rejected', 0)} of its "
        "rows were rejected for disagreeing with the track. Observed detector rows were not moved. "
        "Masks, boxes and object ids are model-generated, not ground truth; ids are only reliable "
        "while a vehicle stays in frame. Rows carrying a SAM box are flagged, and their confidence "
        "is SAM's, not the detector's. A vehicle no track reached is still absent: SAM follows "
        "only what it was given."
    )
    if failed:
        tracks = sorted({t for o in failed for t in o.tracks})
        text += (
            f" {len(failed)} planned requests did not complete: tracks {tracks} may be unrefined."
        )
    if class_note:
        text += f" {class_note}"
    return text


def build_record(
    parent: AnalysisRecord,
    *,
    analysis_id: str,
    placed: Sequence[PlacedTrack],
    refined: Sequence[RefinedTrack],
    outcomes: Sequence[RequestOutcome],
    params: RefineParams,
    created_at_utc: datetime,
    model: str,
    parser_version: str,
    repo_version: str,
    input_sha256: Sequence[str],
    class_note: str | None = None,
) -> AnalysisRecord:
    """The parent's record with everything downstream of the rows recomputed by the same code."""
    seconds = parent.frame_count / parent.fps
    calibrated = parent.calibration.mode == "calibrated"
    metrics = analytics.compute_metrics(placed, calibrated=calibrated, seconds=seconds)
    series = analytics.compute_series(
        placed, calibrated=calibrated, seconds=seconds, fps=parent.fps
    )
    movements = analytics.compute_movements(placed)
    counts = total_counts(refined)
    rows = sum(len(p.track) for p in placed)
    interpolated = sum(int(p.track.interpolated.sum()) for p in placed)
    done = [o for o in outcomes if o.status == "ok"]
    failed = [o for o in outcomes if o.status == "failed"]
    tracks_refined = sum(bool(item.refined.any()) for item in refined)
    tracks_followed = sum(bool(item.accepted) for item in refined)
    receipt = StageReceipt(
        stage=STAGE,
        tool=TOOL,
        version=model,
        device="hosted",
        frames_in=sum(o.frames_billed or o.cohort.frames for o in done),
        items_out=counts["replaced"] + counts["extended"],
        elapsed_s=round(sum(o.elapsed_s or 0.0 for o in done), 3),
        cached=all(o.from_cache for o in done),
        cost_usd=round(sum(o.cost_usd for o in outcomes), 6),
        parameters={
            "requests": len(done),
            "requests_failed": len(failed),
            "requests_not_sent": sum(o.status == "not_sent" for o in outcomes),
            "tracks_followed": tracks_followed,
            "tracks_refined": tracks_refined,
            "rows_confirmed": counts["confirmed"],
            "rows_replaced": counts["replaced"],
            "rows_extended": counts["extended"],
            "rows_rejected": counts["rejected"],
            "rows_interpolated_left": interpolated,
            "parent_analysis": parent.analysis_id,
            "parser": parser_version,
            **{f"param_{key}": value for key, value in params.as_parameters().items()},
        },
    )
    share = interpolated / rows if rows else 0.0
    disclosures = []
    for line in parent.disclosures:
        if "of track points are interpolated" in line:
            line = (
                f"{share:.1%} of track points are still interpolated across missed detections and "
                "flagged; the parent analysis was refined by SAM 3.1, so some rows that were "
                "interpolated now carry SAM's box."
            )
        disclosures.append(line)
    disclosures.append(
        _sam_disclosure(
            counts, outcomes, tracks_followed, tracks_refined, len(refined), class_note
        )
    )
    return AnalysisRecord.model_validate(
        {
            **parent.model_dump(mode="json"),
            "analysis_id": analysis_id,
            "title": f"{parent.title}, refined with SAM 3.1",
            "created_at_utc": created_at_utc,
            "stages": [*(s.model_dump(mode="json") for s in parent.stages),
                       receipt.model_dump(mode="json")],
            "gates": [],
            "metrics": [m.model_dump(mode="json") for m in metrics],
            "series": [s.model_dump(mode="json") for s in series],
            "movements": [m.model_dump(mode="json") for m in movements],
            "disclosures": disclosures,
            "artifacts": [
                {"role": "trajectories", "path": "trajectories.bin",
                 "media_type": "application/octet-stream"},
                {"role": "tracks", "path": "tracks.json", "media_type": "application/json"},
            ],
            "provenance": Provenance(
                kind="approximated",
                produced_by="cairoflow_perception.local.sam_refine",
                produced_at_utc=created_at_utc,
                input_sha256=tuple(input_sha256),
                parameters={
                    "parent_analysis": parent.analysis_id,
                    "sam_model": model,
                    "repo_version": repo_version,
                },
                software={"meta-sam-parser": parser_version},
                cached=receipt.cached,
            ).model_dump(mode="json"),
        }
    )  # fmt: skip


@dataclass(frozen=True)
class SizeRow:
    """One track with a reference vehicle: what each method says about whether it is long."""

    track_id: int
    reference_length_m: float
    detector_long: bool
    mask_length_m: float | None
    placed_length_m: float | None


@dataclass
class Separation:
    """Long-vehicle agreement with the reference for one method."""

    true_long: int = 0
    false_long: int = 0
    missed_long: int = 0
    true_short: int = 0
    rows: list[int] = field(default_factory=list)

    def add(self, said_long: bool, is_long: bool) -> None:
        if said_long and is_long:
            self.true_long += 1
        elif said_long:
            self.false_long += 1
        elif is_long:
            self.missed_long += 1
        else:
            self.true_short += 1

    def as_dict(self) -> dict[str, int | float | None]:
        said, real = self.true_long + self.false_long, self.true_long + self.missed_long
        return {
            "true_long": self.true_long,
            "false_long": self.false_long,
            "missed_long": self.missed_long,
            "true_short": self.true_short,
            "precision": round(self.true_long / said, 4) if said else None,
            "recall": round(self.true_long / real, 4) if real else None,
        }


def separation(rows: Sequence[SizeRow], long_from_m: float) -> dict[str, dict]:
    """How well each method picks out the reference's long vehicles, on the same tracks."""
    detector, mask, placed = Separation(), Separation(), Separation()
    usable = [r for r in rows if r.mask_length_m is not None]
    for row in usable:
        real = row.reference_length_m >= long_from_m
        detector.add(row.detector_long, real)
        mask.add(row.mask_length_m >= long_from_m, real)
        placed.add(row.placed_length_m is not None and row.placed_length_m >= long_from_m, real)
    return {
        "tracks": {"with_reference": len(rows), "with_mask_size": len(usable)},
        "detector_label": detector.as_dict(),
        "mask_length": mask.as_dict(),
        "placed_length": placed.as_dict(),
    }
