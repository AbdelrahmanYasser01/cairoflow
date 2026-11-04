"""Render tracks onto their clip: boxes, track ids, contact points and short trails.

The overlay is a review artifact, not evidence. Every frame carries a caption
saying the boxes are model output turned into tracks by our procedure, the frame's timestamp,
and the clip's licence attribution (CC BY-SA footage must be credited wherever it appears).

Frames are read with OpenCV and piped to ffmpeg as raw BGR, encoded to H.264 with bit-exact flags,
so the same clip and tracks give a byte-identical video. Nothing is overwritten.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict

from cairoflow_contracts.evidence import SourceClip, Track
from cairoflow_perception.ingest import sha256_file

# Timestamp first and the warning early, so both survive on narrow frames.
CAPTION = "NOT GROUND TRUTH: SAM 3.1 output -> tracks (our procedure)"
# BGR, chosen to stay distinguishable on grey roads and on each other.
PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 255), (0, 200, 255), (0, 255, 0), (255, 128, 0), (255, 0, 255), (255, 255, 0),
    (0, 128, 255), (128, 0, 255), (0, 255, 160), (255, 0, 128), (160, 255, 0), (40, 40, 200),
)  # fmt: skip
FONT = cv2.FONT_HERSHEY_SIMPLEX
BANNER_PX = 18


class OverlayError(RuntimeError):
    """The overlay cannot be rendered; no partial video is left behind."""


class OverlayManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    clip_id: str
    clip_sha256: str
    tracks_sha256: str
    track_count: int
    drawn_boxes: int
    trail_frames: int
    output_file: str
    output_sha256: str
    frame_count: int
    fps: int
    produced_at_utc: datetime


def track_colour(track_id: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(track_id.encode("utf-8")).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def tracks_sha256(tracks: Sequence[Track]) -> str:
    canonical = json.dumps(
        [track.model_dump(mode="json") for track in sorted(tracks, key=lambda t: t.track_id)],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _draw_caption(frame: np.ndarray, top: str, bottom: str) -> None:
    height, width = frame.shape[:2]
    for text, y0 in ((top, 0), (bottom, height - BANNER_PX)):
        cv2.rectangle(frame, (0, y0), (width - 1, y0 + BANNER_PX - 1), (0, 0, 0), thickness=-1)
        cv2.putText(frame, text, (4, y0 + 13), FONT, 0.38, (255, 255, 255), 1, cv2.LINE_AA)


def render_overlay(
    clip: SourceClip,
    clip_path: Path,
    tracks: Sequence[Track],
    out_path: Path,
    *,
    trail_frames: int = 10,
    now: datetime | None = None,
) -> OverlayManifest:
    """Write `out_path` (H.264 MP4) and `out_path.with_suffix('.json')` (manifest)."""
    manifest_path = out_path.with_suffix(".json")
    if out_path.exists() or manifest_path.exists():
        raise OverlayError(f"refusing to overwrite {out_path.name}")
    if sha256_file(clip_path) != clip.output_sha256:
        raise OverlayError(f"{clip_path.name} does not match clip {clip.clip_id}")
    by_frame: dict[int, list[tuple[Track, int]]] = defaultdict(list)
    for track in sorted(tracks, key=lambda t: t.track_id):
        if track.clip_id != clip.clip_id:
            raise OverlayError(f"track {track.track_id} belongs to clip {track.clip_id}")
        for position, point in enumerate(track.points):
            if point.frame.frame_index >= len(clip.frames):
                raise OverlayError(f"track {track.track_id} has a frame outside the clip")
            by_frame[point.frame.frame_index].append((track, position))

    width, height = clip.output_stream.width_px, clip.output_stream.height_px
    command = [
        *("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"),
        *("-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}"),
        *("-r", str(clip.target_fps), "-i", "pipe:0"),
        *("-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"),
        *("-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact"),
        *("-movflags", "+faststart", out_path.name),
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(clip_path))
    encoder = subprocess.Popen(
        command,
        cwd=out_path.parent,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    drawn = 0
    index = 0
    try:
        assert encoder.stdin is not None
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index >= len(clip.frames) or frame.shape[:2] != (height, width):
                raise OverlayError("decoded frames do not match the clip manifest")
            for track, position in by_frame.get(index, []):
                colour = track_colour(track.track_id)
                point = track.points[position]
                trail = [
                    p.contact_point_px
                    for p in track.points[max(0, position - trail_frames) : position + 1]
                    if index - p.frame.frame_index <= trail_frames
                ]
                for a, b in zip(trail, trail[1:], strict=False):
                    pa = (round(a[0]), round(a[1]))
                    pb = (round(b[0]), round(b[1]))
                    cv2.line(frame, pa, pb, colour, 1, cv2.LINE_AA)
                x1, y1, x2, y2 = (round(v) for v in point.bbox_xyxy_px)
                cv2.rectangle(frame, (x1, y1), (x2 - 1, y2 - 1), colour, 2)  # half-open box
                cx, cy = (round(v) for v in point.contact_point_px)
                cv2.circle(frame, (cx, cy), 3, colour, thickness=-1)
                cv2.putText(frame, track.track_id, (x1, max(y1 - 3, BANNER_PX + 10)), FONT, 0.4,
                            colour, 1, cv2.LINE_AA)  # fmt: skip
                drawn += 1
            timestamp = clip.frames[index].timestamp_s
            _draw_caption(
                frame,
                f"t={timestamp:.1f}s f{index}  {CAPTION}",
                clip.licence.attribution,
            )
            encoder.stdin.write(frame.tobytes())
            index += 1
        encoder.stdin.close()
        stderr = encoder.stderr.read().decode("utf-8", errors="replace") if encoder.stderr else ""
        if encoder.wait(timeout=600) != 0:
            raise OverlayError(f"ffmpeg failed: {stderr.strip()[:300]}")
        if index != len(clip.frames):
            raise OverlayError(f"decoded {index} frames, the manifest lists {len(clip.frames)}")
        manifest = OverlayManifest(
            clip_id=clip.clip_id,
            clip_sha256=clip.output_sha256,
            tracks_sha256=tracks_sha256(tracks),
            track_count=len(tracks),
            drawn_boxes=drawn,
            trail_frames=trail_frames,
            output_file=out_path.name,
            output_sha256=sha256_file(out_path),
            frame_count=index,
            fps=clip.target_fps,
            produced_at_utc=now or datetime.now(UTC),
        )
        with manifest_path.open("x", encoding="utf-8") as handle:
            handle.write(manifest.model_dump_json(indent=2) + "\n")
    except BaseException:
        if encoder.poll() is None:
            encoder.kill()
            encoder.wait()
        out_path.unlink(missing_ok=True)
        raise
    finally:
        capture.release()
    return manifest
