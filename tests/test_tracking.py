"""Tracking on synthetic observations: continuity, gap bridging, duplicates, determinism."""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cairoflow_contracts.evidence import FrameRef, Observation, Provenance, Track, TrackPoint
from cairoflow_perception.tracking import TrackingPolicy, build_tracks, contact_point

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
PROVENANCE = Provenance(kind="model_generated", produced_by="sam-3.1", produced_at_utc=NOW)


def obs(
    label: str, sam_id: int, frame: int, x: float, y: float = 100.0, size: float = 40.0,
    confidence: float | None = 0.9, clip_id: str = "c1",
) -> Observation:  # fmt: skip
    namespace = label.replace(" ", "_")
    return Observation(
        observation_id=f"{clip_id}:{namespace}:{sam_id}:{frame}",
        frame=FrameRef(clip_id=clip_id, frame_index=frame, timestamp_s=frame / 10),
        class_label=label,
        object_id=f"{namespace}:{sam_id}",
        bbox_xyxy_px=(x, y, x + size, y + size),
        confidence=confidence,
        provenance=PROVENANCE,
    )


def moving(label: str, sam_id: int, frames: range, speed: float = 5.0, start: float = 10.0):
    """An object moving right at `speed` px per frame, positioned by absolute frame index."""
    return [obs(label, sam_id, f, start + speed * f) for f in frames]


def test_one_visible_object_is_one_track() -> None:
    result = build_tracks(moving("car", 3, range(10)), now=NOW)
    (track,) = result.tracks
    assert track.track_id == "car:t1" and track.sam_object_ids == ("car:3",)
    assert [p.frame.frame_index for p in track.points] == list(range(10))
    assert track.points[0].contact_point_px == (30.0, 140.0)  # bottom-centre of (10,100,50,140)
    assert track.bridged_gaps == 0 and track.provenance.kind == "approximated"
    assert track.provenance.parameters["max_gap_frames"] == 5


def test_short_occlusion_with_new_id_is_bridged() -> None:
    before = moving("car", 3, range(0, 10))
    after = moving("car", 8, range(13, 20))  # 3 frames missing, motion continues
    result = build_tracks(before + after, now=NOW)
    (track,) = result.tracks
    assert track.sam_object_ids == ("car:3", "car:8")
    assert track.bridged_gaps == 1 and len(track.points) == 17
    assert result.links and "car:3" in result.links[0]


def test_new_id_far_from_the_prediction_is_not_bridged() -> None:
    before = moving("car", 3, range(0, 10))
    elsewhere = [obs("car", 8, f, 400.0, y=300.0) for f in range(12, 20)]
    result = build_tracks(before + elsewhere, now=NOW)
    assert [t.sam_object_ids for t in result.tracks] == [("car:3",), ("car:8",)]
    assert result.links == ()


def test_a_long_gap_splits_even_the_same_id() -> None:
    first = moving("car", 3, range(0, 10))
    reused = moving("car", 3, range(30, 40))  # same SAM id, 20 frames later: not the same evidence
    result = build_tracks(first + reused, now=NOW)
    assert [len(t.points) for t in result.tracks] == [10, 10]
    assert [t.track_id for t in result.tracks] == ["car:t1", "car:t2"]


def test_classes_are_never_bridged_into_each_other() -> None:
    before = moving("car", 3, range(0, 10))
    after = moving("bus", 1, range(12, 20))
    result = build_tracks(before + after, now=NOW)
    assert {t.track_id for t in result.tracks} == {"car:t1", "bus:t1"}


def test_cross_class_duplicate_keeps_the_more_confident_observation() -> None:
    cars = [obs("car", 1, f, 10.0 + 5 * f, confidence=0.9) for f in range(5)]
    trucks = [obs("truck", 2, f, 10.0 + 5 * f, confidence=0.6) for f in range(5)]
    separate = [obs("truck", 4, f, 300.0) for f in range(5)]  # no overlap: kept
    result = build_tracks(cars + trucks + separate, now=NOW)
    assert {t.track_id: t.sam_object_ids for t in result.tracks} == {
        "car:t1": ("car:1",),
        "truck:t1": ("truck:4",),
    }
    assert len(result.duplicates) == 5
    assert "truck:2 removed, duplicate of car:1" in result.duplicates[0]


def test_unknown_confidence_loses_a_duplicate_tie() -> None:
    known = [obs("car", 1, f, 10.0, confidence=0.2) for f in range(3)]
    unknown = [obs("bus", 1, f, 10.0, confidence=None) for f in range(3)]
    result = build_tracks(known + unknown, now=NOW)
    assert [t.track_id for t in result.tracks] == ["car:t1"]


def test_short_chains_are_dropped_and_reported() -> None:
    result = build_tracks(moving("car", 1, range(10)) + moving("car", 2, range(50, 52)), now=NOW)
    assert [t.sam_object_ids for t in result.tracks] == [("car:1",)]
    assert result.short_tracks == ("car:2: 2 observation(s), fewer than 3",)


def test_competing_successors_go_to_the_better_prediction() -> None:
    before = moving("car", 1, range(0, 10))  # predicted x at frame 12: 10 + 5*12 = 70
    good = [obs("car", 5, f, 10.0 + 5 * f) for f in range(12, 18)]
    worse = [obs("car", 6, f, 10.0 + 5 * f + 12.0) for f in range(12, 18)]
    result = build_tracks(before + good + worse, now=NOW)
    linked = next(t for t in result.tracks if len(t.sam_object_ids) == 2)
    assert linked.sam_object_ids == ("car:1", "car:5")
    assert len(result.tracks) == 2


def test_input_order_does_not_change_the_result() -> None:
    data = (
        moving("car", 3, range(0, 10))
        + moving("car", 8, range(13, 20))
        + [obs("truck", 2, f, 10.0 + 5 * f, confidence=0.5) for f in range(4)]
        + moving("bus", 1, range(5, 25), speed=-3.0, start=600.0)
    )
    expected = build_tracks(data, now=NOW)
    for seed in range(5):
        shuffled = data[:]
        random.Random(seed).shuffle(shuffled)
        assert build_tracks(shuffled, now=NOW) == expected


def test_policy_thresholds_are_respected() -> None:
    before = moving("car", 3, range(0, 10))
    after = moving("car", 8, range(13, 20))
    strict = TrackingPolicy(max_gap_frames=1)
    assert len(build_tracks(before + after, strict, now=NOW).tracks) == 2


def test_invalid_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="more than one clip"):
        build_tracks([obs("car", 1, 0, 10.0), obs("car", 2, 0, 90.0, clip_id="c2")])
    duplicate = obs("car", 1, 0, 10.0)
    with pytest.raises(ValueError, match="not unique"):
        build_tracks([duplicate, duplicate])
    assert build_tracks([], now=NOW).tracks == ()


def test_track_contract_enforces_order_and_namespace() -> None:
    point = TrackPoint(
        observation_id="o",
        frame=FrameRef(clip_id="c1", frame_index=2, timestamp_s=0.2),
        bbox_xyxy_px=(0.0, 0.0, 10.0, 10.0),
        contact_point_px=contact_point((0.0, 0.0, 10.0, 10.0)),
    )
    earlier_frame = FrameRef(clip_id="c1", frame_index=1, timestamp_s=0.1)
    earlier = point.model_copy(update={"frame": earlier_frame})
    values = {
        "track_id": "car:t1", "clip_id": "c1", "class_label": "car",
        "sam_object_ids": ("car:1",), "bridged_gaps": 0, "provenance": PROVENANCE,
    }  # fmt: skip
    Track(points=(earlier, point), **values)
    with pytest.raises(ValidationError, match="strictly increasing"):
        Track(points=(point, earlier), **values)
    with pytest.raises(ValidationError, match="namespaced"):
        Track(points=(point,), **(values | {"track_id": "bus:t1"}))
