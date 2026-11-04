"""Draw CV output on its video: an annotated preview during a run and a browser-playable H.264 MP4
from a finished analysis directory.

The video is a review artifact, not evidence. Every frame says this is CairoFlow CV output and not
ground truth, and carries the source's licence attribution in full (CC BY-SA footage must be
credited wherever it appears; on camera 2 the bottom band it covers is the window frame, not road).

Frames go to ffmpeg as raw BGR. `h264_nvenc` is used when a one-frame encode works, otherwise
`libx264`; both give yuv420p with +faststart. The MP4 is written beside the analysis, is Git-ignored
and is not bundled: the record lists it with a rebuild hint and no hash.

A held-out analysis renders only once its gate has scored it, by the read log; until then a held-out
frame is refused like any other held-out read.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import cv2
import numpy as np

from cairoflow_contracts.analysis_record import AnalysisRecord
from cairoflow_perception.analysis_store import (
    POINT_INTERPOLATED,
    POINT_OUTSIDE_VALID_REGION,
    PX_SCALE,
    TRAJECTORIES_FILE,
    decode_trajectories,
    load_tracks,
    verify_analysis,
)
from cairoflow_perception.ingest import sha256_file
from cairoflow_perception.local.frames import (
    AVI_FRAME_LAG,
    check_scored,
    held_out_frames,
    is_camera2,
    iter_frames,
    plan_frames,
)

ANNOTATED_VIDEO = "annotated.mp4"
CAPTION = "CairoFlow CV output, NOT ground truth"
TRAIL_FRAMES = 12
FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_SCALE = 0.34
LINE_PX = 12
# BGR, kept apart on grey asphalt
CLASS_COLOURS: dict[str, tuple[int, int, int]] = {
    "car": (0, 220, 255),
    "truck": (0, 130, 255),
    "bus": (255, 110, 40),
    "motorcycle": (255, 0, 255),
    "vehicle": (80, 255, 80),
    "unknown": (210, 210, 210),
}
ENCODERS: dict[str, tuple[str, ...]] = {
    "h264_nvenc": ("-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23", "-b:v", "0"),
    "libx264": ("-c:v", "libx264", "-preset", "veryfast", "-crf", "20"),
}


class RenderError(RuntimeError):
    """The video cannot be rendered; no partial file is left behind."""


@dataclass(frozen=True)
class RenderResult:
    path: Path
    frames: int
    boxes: int
    encoder: str


def wrap_text(text: str, width_px: int) -> list[str]:
    """Greedy word wrap to the pixel width of the caption font."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if cv2.getTextSize(trial, FONT, FONT_SCALE, 1)[0][0] <= width_px or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    return [*lines, current] if current else lines


def draw_banners(frame: np.ndarray, top: str, bottom: str) -> None:
    """A one-line banner at the top and the full `bottom` text, wrapped, at the bottom."""
    height, width = frame.shape[:2]
    top_lines = wrap_text(top, width - 8)
    bottom_lines = wrap_text(bottom, width - 8)
    top_h = LINE_PX * len(top_lines) + 4
    bottom_h = LINE_PX * len(bottom_lines) + 4
    cv2.rectangle(frame, (0, 0), (width - 1, top_h - 1), (0, 0, 0), thickness=-1)
    cv2.rectangle(frame, (0, height - bottom_h), (width - 1, height - 1), (0, 0, 0), thickness=-1)
    for lines, y0 in ((top_lines, 0), (bottom_lines, height - bottom_h)):
        for i, line in enumerate(lines):
            cv2.putText(
                frame, line, (4, y0 + LINE_PX * (i + 1) - 1), FONT, FONT_SCALE, (255, 255, 255), 1,
                cv2.LINE_AA,
            )  # fmt: skip


def draw_box(
    frame: np.ndarray,
    box_xyxy: Sequence[float],
    label: str,
    colour: tuple[int, int, int],
    *,
    thickness: int = 1,
) -> None:
    x1, y1, x2, y2 = (round(v) for v in box_xyxy)
    cv2.rectangle(frame, (x1, y1), (x2, y2), colour, thickness)
    cv2.putText(frame, label, (x1, max(y1 - 2, 10)), FONT, 0.32, colour, 1, cv2.LINE_AA)


def draw_trail(frame: np.ndarray, points: np.ndarray, colour: tuple[int, int, int]) -> None:
    if len(points) >= 2:
        path = np.rint(points).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(frame, [path], False, colour, 1, cv2.LINE_AA)


def write_jpeg_atomic(path: Path, frame: np.ndarray, quality: int = 80) -> None:
    """A reader of `path` always sees a whole file."""
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RenderError("could not encode the preview")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(handle, 0o644)  # mkstemp's 0600 would hide it from a server under another user
        with os.fdopen(handle, "wb") as out:
            out.write(encoded.tobytes())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


@cache
def encoder_works(name: str) -> bool:
    command = [
        *("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"),
        *("-f", "lavfi", "-i", "color=c=black:s=256x256:r=10:d=0.2"),
        *ENCODERS[name], *("-pix_fmt", "yuv420p", "-f", "null", "-"),
    ]  # fmt: skip
    try:
        done = subprocess.run(command, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def pick_encoder() -> str:
    for name in ENCODERS:
        if encoder_works(name):
            return name
    raise RenderError("ffmpeg has no working H.264 encoder")


@dataclass(frozen=True)
class _FileWindow:
    start_s: float  # first analysed second of the file being read
    frame_lag: int
    source_offset_s: float
    camera_source: bool  # the file is camera 2 footage, so the block rule applies


def _file_window(record: AnalysisRecord, video_path: Path) -> _FileWindow:
    """Where a record's frames sit in the file, from what the pipeline recorded."""
    parameters = record.provenance.parameters
    offset = parameters.get("source_offset_s")
    return _FileWindow(
        float(parameters.get("file_start_s", record.source_start_s)),
        int(parameters.get("frame_lag", AVI_FRAME_LAG if is_camera2(video_path) else 0)),
        float(offset or 0.0),
        offset is not None,
    )


def _frames_to_skip(record: AnalysisRecord, window: _FileWindow, read_log: Path | None) -> set[int]:
    """Frames left unread by the block rule. Raises `HeldOutError` for a held-out analysis whose
    gate has not scored it."""
    if not window.camera_source:
        return set()
    gate_id = record.provenance.parameters.get("held_out_gate_id")
    if gate_id is not None and read_log is not None:
        check_scored(read_log, str(gate_id), record.analysis_id)
        return set()
    return held_out_frames(
        plan_frames(
            record.fps * record.frame_stride,
            window.start_s,
            record.frame_count / record.fps,
            record.frame_stride,
            window.frame_lag,
            window.source_offset_s,
        )
    )


def _boxes_by_frame(directory: Path) -> dict[int, list[tuple]]:
    """Per analysis frame: (track entry, row in the columns) of each point."""
    index = load_tracks(directory)
    by_frame: dict[int, list[tuple]] = defaultdict(list)
    for track in index.tracks:
        for i in range(track.n):
            by_frame[track.start_frame + i].append((track, track.first_row + i))
    return by_frame


def _annotated_frames(
    record: AnalysisRecord, directory: Path, video_path: Path, trail_frames: int, skip: set[int]
) -> Iterator[tuple[np.ndarray, int]]:
    """Every analysis frame in order, annotated, with its box count. A frame that could not be
    read is black and says so, so frame k of the video stays at second k / fps."""
    data = (directory / TRAJECTORIES_FILE).read_bytes()
    _, columns = decode_trajectories(data)
    x, y = columns.x * PX_SCALE, columns.y * PX_SCALE
    w, h = columns.w * PX_SCALE, columns.h * PX_SCALE
    flags = columns.point_flags
    by_frame = _boxes_by_frame(directory)
    window = _file_window(record, video_path)
    attribution = record.source.attribution

    def blank(frame_index: int) -> np.ndarray:
        image = np.zeros((record.height_px, record.width_px, 3), np.uint8)
        draw_banners(image, f"frame {frame_index} was not read. {CAPTION}", attribution)
        return image

    expected = 0
    for frame_index, source_s, image in iter_frames(
        video_path,
        window.start_s,
        record.frame_count / record.fps,
        record.frame_stride,
        frame_lag=window.frame_lag,
        source_offset_s=window.source_offset_s,
        skip=skip,
    ):
        while expected < frame_index:
            yield blank(expected), 0
            expected += 1
        if image.shape[:2] != (record.height_px, record.width_px):
            raise RenderError("the video's frame size differs from the record's")
        boxes = 0
        for track, row in by_frame.get(frame_index, ()):
            if flags[row] & POINT_OUTSIDE_VALID_REGION:
                continue
            colour = CLASS_COLOURS.get(track.vehicle_class, CLASS_COLOURS["unknown"])
            first = max(track.first_row, row - trail_frames)
            draw_trail(image, np.stack([x[first : row + 1], y[first : row + 1]], axis=1), colour)
            half_w, half_h = w[row] / 2, h[row] / 2
            box = (x[row] - half_w, y[row] - half_h, x[row] + half_w, y[row] + half_h)
            filled = bool(flags[row] & POINT_INTERPOLATED)
            draw_box(image, box, f"#{track.track_id}", colour, thickness=1 if filled else 2)
            boxes += 1
        draw_banners(image, f"source {source_s:.1f}s frame {frame_index}  {CAPTION}", attribution)
        yield image, boxes
        expected = frame_index + 1
    while expected < record.frame_count:
        yield blank(expected), 0
        expected += 1


def _encode(
    frames: Iterator[tuple[np.ndarray, int]],
    out_path: Path,
    encoder: str,
    width: int,
    height: int,
    fps: float,
    on_frame: Callable[[int], None] | None,
) -> tuple[int, int]:
    command = [
        *("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"),
        *("-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}"),
        *("-r", f"{fps:g}", "-i", "pipe:0"),
        *("-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"),
        *ENCODERS[encoder], *("-pix_fmt", "yuv420p", "-movflags", "+faststart"),
        *("-map_metadata", "-1", "-f", "mp4", str(out_path)),
    ]  # fmt: skip
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=errors
        )
        count = boxes = 0
        try:
            assert process.stdin is not None
            for image, drawn in frames:
                process.stdin.write(np.ascontiguousarray(image).tobytes())
                count += 1
                boxes += drawn
                if on_frame:
                    on_frame(count)
            process.stdin.close()
            code = process.wait(timeout=600)
        except BaseException:
            process.kill()
            process.wait()
            raise
        if code != 0:
            errors.seek(0)
            message = errors.read().decode("utf-8", errors="replace").strip()[:300]
            raise RenderError(f"ffmpeg ({encoder}) failed: {message}")
    return count, boxes


def render_annotated_video(
    directory: Path,
    video_path: Path,
    *,
    trail_frames: int = TRAIL_FRAMES,
    overwrite: bool = False,
    read_log: Path | None = None,
    on_frame: Callable[[int], None] | None = None,
) -> RenderResult:
    """Write `directory`/annotated.mp4 from an analysis and its source video. `read_log` is the
    held-out read log, needed only to render a held-out analysis its gate has scored."""
    record = verify_analysis(directory)
    out_path = directory / ANNOTATED_VIDEO
    if out_path.exists() and not overwrite:
        raise RenderError(f"{out_path} exists; pass overwrite to replace it")
    expected = record.source.source_sha256
    if expected and sha256_file(video_path) != expected:
        raise RenderError(f"{video_path.name} is not the video this analysis came from")
    skip = _frames_to_skip(record, _file_window(record, video_path), read_log)
    encoder = pick_encoder()
    partial = out_path.with_suffix(".partial.mp4")
    try:
        frames = _annotated_frames(record, directory, video_path, trail_frames, skip)
        try:
            count, boxes = _encode(
                frames, partial, encoder, record.width_px, record.height_px, record.fps, on_frame
            )
        except RenderError:
            if encoder == "libx264":
                raise
            encoder = "libx264"
            frames = _annotated_frames(record, directory, video_path, trail_frames, skip)
            count, boxes = _encode(
                frames, partial, encoder, record.width_px, record.height_px, record.fps, on_frame
            )
        os.replace(partial, out_path)
    finally:
        partial.unlink(missing_ok=True)
    return RenderResult(out_path, count, boxes, encoder)
