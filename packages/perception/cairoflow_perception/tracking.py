"""Stitch SAM observations into vehicle tracks, deterministically.

SAM 3.1 object ids are stable only while an object stays visible; after occlusion, entry or exit
an object can come back under a new id, and an id can be reused (meta-model-api.md). This module
turns per-frame observations into tracks in four fixed steps:

1. Cross-class duplicates: when two prompts (e.g. "car" and "truck") box the same vehicle in the
   same frame (IoU >= `duplicate_iou`), keep the higher-confidence observation.
2. Segments: each SAM id becomes runs of observations, split wherever it is missing for more
   than `max_gap_frames` frames, so a reused id never joins two vehicles.
3. Bridging: a segment that ends may continue as a different id of the same class that starts
   within `max_gap_frames`, if a constant-velocity prediction lands within `max_link_residual`
   box diagonals of it and the box area changes by at most `max_link_area_ratio`. Candidate
   links are taken greedily from the smallest residual; ties break on frame and id.
4. Tracks shorter than `min_track_observations` are dropped and listed.

Same input, same policy: same tracks, regardless of input order. Everything dropped or linked is
reported. Tracks are derived by our procedure from model output (`kind="approximated"`); they are
in image pixels until a calibration projects their contact points to the ground.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.evidence import Observation, Provenance, Track, TrackPoint

Box = tuple[float, float, float, float]


class TrackingPolicy(BaseModel):
    """Every threshold of the stitching procedure; recorded in each track's provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    duplicate_iou: float = Field(0.7, gt=0, le=1)
    max_gap_frames: int = Field(5, ge=0, description="missing frames tolerated inside a track")
    max_link_residual: float = Field(
        0.5, gt=0, description="prediction error allowed, in box diagonals"
    )
    max_link_area_ratio: float = Field(2.0, ge=1)
    min_track_observations: int = Field(3, ge=1)


class TrackingResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy: TrackingPolicy
    tracks: tuple[Track, ...]
    duplicates: tuple[str, ...]  # observations removed in step 1, with the reason
    links: tuple[str, ...]  # gaps bridged in step 3
    short_tracks: tuple[str, ...]  # chains removed in step 4


@dataclass
class _Segment:
    class_label: str
    object_id: str
    observations: list[Observation]
    successor: _Segment | None = field(default=None, repr=False)
    has_predecessor: bool = False

    @property
    def first(self) -> int:
        return self.observations[0].frame.frame_index

    @property
    def last(self) -> int:
        return self.observations[-1].frame.frame_index


def iou(a: Box, b: Box) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    intersection = width * height
    return intersection / (_area(a) + _area(b) - intersection)


def _area(box: Box) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def _centre(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _diagonal(box: Box) -> float:
    return math.hypot(box[2] - box[0], box[3] - box[1])


def contact_point(box: Box) -> tuple[float, float]:
    """Bottom-centre of the box, the image point where the vehicle meets the road."""
    return (box[0] + box[2]) / 2, box[3]


def _sort_key(observation: Observation) -> tuple[int, str, str]:
    return observation.frame.frame_index, observation.class_label, observation.object_id


def _check_input(observations: Sequence[Observation]) -> None:
    if len({o.frame.clip_id for o in observations}) > 1:
        raise ValueError("observations come from more than one clip")
    if len({o.observation_id for o in observations}) != len(observations):
        raise ValueError("observation ids are not unique")
    if len({(o.object_id, o.frame.frame_index) for o in observations}) != len(observations):
        raise ValueError("an object id appears twice in one frame")


def _remove_duplicates(
    observations: list[Observation], threshold: float
) -> tuple[list[Observation], list[str]]:
    by_frame: dict[int, list[Observation]] = defaultdict(list)
    for observation in observations:
        by_frame[observation.frame.frame_index].append(observation)
    kept: list[Observation] = []
    removed: list[str] = []
    for index in sorted(by_frame):
        ranked = sorted(
            by_frame[index],
            key=lambda o: (-(o.confidence if o.confidence is not None else -1.0), o.object_id),
        )
        frame_kept: list[Observation] = []
        for candidate in ranked:
            match = next(
                (
                    k
                    for k in frame_kept
                    if k.class_label != candidate.class_label
                    and iou(k.bbox_xyxy_px, candidate.bbox_xyxy_px) >= threshold
                ),
                None,
            )
            if match is None:
                frame_kept.append(candidate)
            else:
                overlap = iou(match.bbox_xyxy_px, candidate.bbox_xyxy_px)
                removed.append(
                    f"frame {index}: {candidate.object_id} removed, duplicate of "
                    f"{match.object_id} (IoU {overlap:.2f})"
                )
        kept.extend(frame_kept)
    return sorted(kept, key=_sort_key), removed


def _segments(observations: list[Observation], max_gap: int) -> list[_Segment]:
    by_id: dict[str, list[Observation]] = defaultdict(list)
    for observation in observations:  # already sorted by frame
        by_id[observation.object_id].append(observation)
    segments: list[_Segment] = []
    for object_id in sorted(by_id):
        run: list[Observation] = []
        for observation in by_id[object_id]:
            if run and observation.frame.frame_index - run[-1].frame.frame_index - 1 > max_gap:
                segments.append(_Segment(run[0].class_label, object_id, run))
                run = []
            run.append(observation)
        segments.append(_Segment(run[0].class_label, object_id, run))
    return segments


def _link_cost(a: _Segment, b: _Segment, policy: TrackingPolicy) -> float | None:
    """Prediction residual in box diagonals, or None if the link is not allowed."""
    if a.class_label != b.class_label or b.first <= a.last:
        return None
    if b.first - a.last - 1 > policy.max_gap_frames:
        return None
    last_box = a.observations[-1].bbox_xyxy_px
    first_box = b.observations[0].bbox_xyxy_px
    ratio = max(_area(last_box), _area(first_box)) / min(_area(last_box), _area(first_box))
    if ratio > policy.max_link_area_ratio:
        return None
    cx, cy = _centre(last_box)
    if len(a.observations) >= 2:
        previous = a.observations[-2]
        px, py = _centre(previous.bbox_xyxy_px)
        step = a.last - previous.frame.frame_index
        vx, vy = (cx - px) / step, (cy - py) / step
    else:
        vx = vy = 0.0
    ahead = b.first - a.last
    bx, by = _centre(first_box)
    error = math.hypot(cx + vx * ahead - bx, cy + vy * ahead - by)
    residual = error / ((_diagonal(last_box) + _diagonal(first_box)) / 2)
    return residual if residual <= policy.max_link_residual else None


def _bridge(segments: list[_Segment], policy: TrackingPolicy) -> list[str]:
    candidates = []
    for a in segments:
        for b in segments:
            cost = _link_cost(a, b, policy)
            if cost is not None:
                candidates.append((cost, a.last, a.object_id, b.first, b.object_id, a, b))
    candidates.sort(key=lambda c: c[:5])
    links: list[str] = []
    for cost, _, _, _, _, a, b in candidates:
        if a.successor is not None or b.has_predecessor:
            continue
        a.successor, b.has_predecessor = b, True
        links.append(
            f"{a.object_id} (to frame {a.last}) -> {b.object_id} (from frame {b.first}), "
            f"residual {cost:.2f} box diagonals"
        )
    return links


def build_tracks(
    observations: Sequence[Observation],
    policy: TrackingPolicy | None = None,
    *,
    now: datetime | None = None,
) -> TrackingResult:
    """Stitch observations from one clip into tracks."""
    policy = policy or TrackingPolicy()
    _check_input(observations)
    kept, duplicates = _remove_duplicates(sorted(observations, key=_sort_key), policy.duplicate_iou)
    segments = _segments(kept, policy.max_gap_frames)
    links = _bridge(segments, policy)

    chains: list[list[_Segment]] = []
    for start in segments:
        if start.has_predecessor:
            continue
        chain, current = [], start
        while current is not None:
            chain.append(current)
            current = current.successor
        chains.append(chain)
    chains.sort(key=lambda c: (c[0].first, c[0].class_label, c[0].object_id))

    provenance = Provenance(
        kind="approximated",
        produced_by="cairoflow_perception.tracking",
        produced_at_utc=now or datetime.now(UTC),
        parameters=policy.model_dump(),
    )
    tracks: list[Track] = []
    short: list[str] = []
    numbers: dict[str, int] = defaultdict(int)
    for chain in chains:
        members = [o for segment in chain for o in segment.observations]
        ids = " -> ".join(segment.object_id for segment in chain)
        if len(members) < policy.min_track_observations:
            short.append(
                f"{ids}: {len(members)} observation(s), fewer than {policy.min_track_observations}"
            )
            continue
        namespace = chain[0].object_id.split(":", 1)[0]
        numbers[namespace] += 1
        tracks.append(
            Track(
                track_id=f"{namespace}:t{numbers[namespace]}",
                clip_id=members[0].frame.clip_id,
                class_label=chain[0].class_label,
                sam_object_ids=tuple(segment.object_id for segment in chain),
                points=tuple(
                    TrackPoint(
                        observation_id=o.observation_id,
                        frame=o.frame,
                        bbox_xyxy_px=o.bbox_xyxy_px,
                        contact_point_px=contact_point(o.bbox_xyxy_px),
                    )
                    for o in members
                ),
                bridged_gaps=len(chain) - 1,
                provenance=provenance,
            )
        )
    return TrackingResult(
        policy=policy,
        tracks=tuple(tracks),
        duplicates=tuple(duplicates),
        links=tuple(links),
        short_tracks=tuple(short),
    )
