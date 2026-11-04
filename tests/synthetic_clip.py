"""A synthetic clip manifest (10 fps, 20 s by default) for tests that need frames but no video."""

from __future__ import annotations

from datetime import UTC, date, datetime

from cairoflow_contracts.evidence import (
    ClipLicense,
    FrameRef,
    Provenance,
    SourceClip,
    TrimSpec,
    VideoStreamInfo,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
FPS, FRAMES = 10, 200
CLIP_SHA = "a" * 64
CLIP_ID = CLIP_SHA[:16]
PROVENANCE = Provenance(kind="approximated", produced_by="test", produced_at_utc=NOW)


def frame(index: int, clip_id: str = CLIP_ID) -> FrameRef:
    return FrameRef(clip_id=clip_id, frame_index=index, timestamp_s=index / FPS)


def clip(frames: int = FRAMES) -> SourceClip:
    stream = VideoStreamInfo(
        codec="h264",
        pixel_format="yuv420p",
        width_px=1280,
        height_px=720,
        fps=FPS,
        frame_count=frames,
        duration_s=frames / FPS,
    )
    licence = ClipLicense(
        source_id="synthetic",
        source_url="https://example.invalid/synthetic",
        licence_name="none (generated in code)",
        attribution="synthetic test clip",
        accessed_on=date(2026, 10, 27),
        register_ref="tests",
        permits_commercial_use=True,
        permits_cloud_processing=True,
        permits_redistribution=True,
        share_alike=False,
        synthetic=True,
    )
    return SourceClip(
        clip_id=CLIP_ID,
        licence=licence,
        source_file_name="synthetic.mp4",
        source_sha256=CLIP_SHA,
        source_stream=stream,
        trim=TrimSpec(),
        target_fps=FPS,
        output_file="clip.mp4",
        output_sha256=CLIP_SHA,
        output_stream=stream,
        ffmpeg_command=("ffmpeg",),
        frames=tuple(frame(i) for i in range(frames)),
        provenance=PROVENANCE,
    )
