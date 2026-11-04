"""Overlay rendering on a synthetic grey clip: what is drawn, where, and only there."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from cairoflow_contracts.evidence import ClipLicense, FrameRef, Observation, Provenance, SourceClip
from cairoflow_perception.ingest import OUTPUT_FILE, ingest_clip, probe_video, sha256_file
from cairoflow_perception.overlay import OverlayError, render_overlay, tracks_sha256
from cairoflow_perception.tracking import build_tracks

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
GREY = 128
BOX = (40.0, 40.0, 80.0, 80.0)  # contact point (60, 80); the dot reaches y = 83
TRACK_FRAMES = range(5, 15)


@pytest.fixture(scope="module")
def clip_and_path(tmp_path_factory: pytest.TempPathFactory) -> tuple[SourceClip, Path]:
    """20 frames of flat grey at 10 fps, 160x120, so anything drawn stands out."""
    work = tmp_path_factory.mktemp("grey")
    source = work / "grey.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"),
            *("-f", "lavfi", "-i", "color=c=0x808080:size=160x120:rate=10", "-t", "2"),
            *("-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)),
        ],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    licence = ClipLicense(
        source_id="synthetic-grey",
        source_url="https://ffmpeg.org/ffmpeg-filters.html#color",
        licence_name="generated in code",
        attribution="Synthetic grey test clip",
        accessed_on=date(2026, 10, 27),
        register_ref="tests/test_overlay.py",
        permits_commercial_use=True,
        permits_cloud_processing=True,
        permits_redistribution=True,
        share_alike=False,
        synthetic=True,
    )
    clip = ingest_clip(source, licence, work / "ingest", now=NOW)
    return clip, work / "ingest" / OUTPUT_FILE


def _tracks(clip: SourceClip, clip_id: str | None = None):
    provenance = Provenance(kind="model_generated", produced_by="sam-3.1", produced_at_utc=NOW)
    cid = clip_id or clip.clip_id
    observations = [
        Observation(
            observation_id=f"{cid}:car:1:{f}",
            frame=FrameRef(clip_id=cid, frame_index=f, timestamp_s=clip.frames[f].timestamp_s),
            class_label="car",
            object_id="car:1",
            bbox_xyxy_px=BOX,
            confidence=0.9,
            provenance=provenance,
        )
        for f in TRACK_FRAMES
    ]
    return build_tracks(observations, now=NOW).tracks


def _frame(path: Path, index: int) -> np.ndarray:
    capture = cv2.VideoCapture(str(path))
    for _ in range(index + 1):
        ok, frame = capture.read()
        assert ok
    capture.release()
    return frame.astype(int)


def _distance_from_grey(pixel: np.ndarray) -> int:
    return int(np.abs(pixel - GREY).max())


def test_overlay_video_matches_the_clip_and_its_manifest(
    clip_and_path: tuple[SourceClip, Path], tmp_path: Path
) -> None:
    clip, path = clip_and_path
    out = tmp_path / "overlay.mp4"
    manifest = render_overlay(clip, path, _tracks(clip), out, now=NOW)

    stream = probe_video(out)
    assert (stream.codec, stream.width_px, stream.height_px) == ("h264", 160, 120)
    assert stream.frame_count == manifest.frame_count == len(clip.frames) == 20
    assert stream.fps == pytest.approx(10)
    assert manifest.drawn_boxes == len(TRACK_FRAMES) and manifest.track_count == 1
    assert manifest.output_sha256 == sha256_file(out)
    saved = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert saved["clip_sha256"] == clip.output_sha256


def test_boxes_and_contact_points_appear_only_where_the_track_is(
    clip_and_path: tuple[SourceClip, Path], tmp_path: Path
) -> None:
    clip, path = clip_and_path
    out = tmp_path / "overlay.mp4"
    render_overlay(clip, path, _tracks(clip), out, now=NOW)

    inside = _frame(out, 10)
    assert _distance_from_grey(inside[60, 40]) > 60  # left box edge
    assert _distance_from_grey(inside[82, 60]) > 60  # contact dot, below the box edge
    assert _distance_from_grey(inside[60, 60]) < 20  # box interior is not filled

    outside = _frame(out, 2)  # the track starts at frame 5
    assert _distance_from_grey(outside[60, 40]) < 20 and _distance_from_grey(outside[82, 60]) < 20

    for frame in (inside, outside):  # caption banners on every frame
        assert frame[2, 155].max() < 30 and frame[117, 155].max() < 30


def test_same_inputs_give_a_byte_identical_overlay(
    clip_and_path: tuple[SourceClip, Path], tmp_path: Path
) -> None:
    clip, path = clip_and_path
    first = render_overlay(clip, path, _tracks(clip), tmp_path / "a" / "o.mp4", now=NOW)
    second = render_overlay(clip, path, _tracks(clip), tmp_path / "b" / "o.mp4", now=NOW)
    assert first.output_sha256 == second.output_sha256


def test_no_tracks_still_renders_the_labelled_clip(
    clip_and_path: tuple[SourceClip, Path], tmp_path: Path
) -> None:
    clip, path = clip_and_path
    manifest = render_overlay(clip, path, (), tmp_path / "empty.mp4", now=NOW)
    assert manifest.drawn_boxes == 0 and manifest.frame_count == 20


def test_existing_output_is_never_overwritten(
    clip_and_path: tuple[SourceClip, Path], tmp_path: Path
) -> None:
    clip, path = clip_and_path
    out = tmp_path / "overlay.mp4"
    render_overlay(clip, path, _tracks(clip), out, now=NOW)
    before = out.read_bytes()
    with pytest.raises(OverlayError, match="overwrite"):
        render_overlay(clip, path, (), out, now=NOW)
    assert out.read_bytes() == before


def test_mismatched_inputs_are_refused_without_output(
    clip_and_path: tuple[SourceClip, Path], tmp_path: Path
) -> None:
    clip, path = clip_and_path
    with pytest.raises(OverlayError, match="belongs to clip"):
        render_overlay(clip, path, _tracks(clip, clip_id="other"), tmp_path / "x.mp4")
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"not the clip")
    with pytest.raises(OverlayError, match="does not match"):
        render_overlay(clip, fake, _tracks(clip), tmp_path / "y.mp4")
    assert not (tmp_path / "x.mp4").exists() and not (tmp_path / "y.mp4").exists()


def test_track_hash_ignores_order(clip_and_path: tuple[SourceClip, Path]) -> None:
    clip, _ = clip_and_path
    tracks = list(_tracks(clip))
    other = tracks[0].model_copy(update={"track_id": "car:t2"})
    assert tracks_sha256([tracks[0], other]) == tracks_sha256([other, tracks[0]])
