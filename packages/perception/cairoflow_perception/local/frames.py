"""Stream frames of a video by source time, with OpenCV.

`iter_frames` yields (analysis_frame_index, source_s, frame_bgr). The analysis frame index is tied
to time, not to what could be decoded: frame k of a run is second start_s + k * stride / fps of the
file whatever happened to the frames before it, so an unreadable frame leaves a hole, never a
shift. A video shorter than asked ends the stream early and `FrameStats` says so.

`start_s` counts in seconds of the file being read; `source_s` adds `source_offset_s`, where the
file's second 0 sits in the camera 2 source (0 for the AVI, the trim start for a clip cut from it).

Camera 2 only (`frame_lag`, `held_out_frames`). The image behind source second s is AVI frame
round(10 s) - 1, not round(10 s) (local/scoring.py), so a run on the AVI passes
`frame_lag=AVI_FRAME_LAG` and sees the image SAM saw at that second. A clip needs no lag: frame k of
a clip cut at trim start t is AVI frame 10 t + k - 1, so it already is the image behind second
t + k/10 (test_cv_pipeline.py cuts one with `ingest.transcode_command` and compares). The cut lands
one frame early because the AVI's header says 10.00001 fps, so a video made at exactly 10 fps cannot
show this. Odd 60 s blocks are held out: `held_out_frames` refuses a window that reaches one, before
any pixel is read, and names the single frame at the start of an even block whose image is the last
frame of the odd block before it.

The one way into an odd block is a `HeldOutGrant`, which `check_grant` holds against the read log:
a committed gate has to have spent its one read on this analysis first. `camera2_offset_s` says
what a file can show of its own place in the source: the AVI by its name or, for a renamed copy, its
bytes, and a clip by the output hash in its manifest under data/processed/clips, wherever the file
now is. Any other transcode has other bytes and no manifest, so it is only camera 2 when its place
in the source is declared; without that it is an uncalibrated upload.
"""

from __future__ import annotations

import json
import math
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cairoflow_perception.ingest import sha256_file
from cairoflow_perception.local.scoring import (
    AVI_FRAME_LAG,
    COVERED_END_S,
    COVERED_START_S,
    VIDEO,
    HeldOutError,
    avi_index,
    block_of,
    require_dev,
)

CAMERA2_SHA256 = "04ef7435be3a107e7321900c384bb1ee7703ce753a122559cd2937fef341f530"
CAMERA2_BYTES = 449163264  # checked first, so only a file of this size is ever hashed
CLIP_MANIFESTS = Path(__file__).resolve().parents[4] / "data/processed/clips"
MAX_BAD_IN_A_ROW = 8  # this many unreadable frames in a row is the end of the usable video
SEEK_RATHER_THAN_GRAB = 16  # a jump longer than this seeks; a shorter one reads through
__all__ = [
    "AVI_FRAME_LAG",
    "FrameStats",
    "FrameStreamError",
    "HeldOutGrant",
    "PlannedFrame",
    "VideoInfo",
    "camera2_offset_s",
    "check_grant",
    "check_scored",
    "held_out_frames",
    "is_camera2",
    "is_held_out_second",
    "iter_frames",
    "plan_frames",
    "probe_video",
]


class FrameStreamError(RuntimeError):
    """The video cannot be opened or holds no frame in the asked window."""


@dataclass(frozen=True)
class VideoInfo:
    path: Path
    fps: float
    frame_count: int  # as the container reports it; can be wrong, so reads decide the real end
    width_px: int
    height_px: int

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.fps


@dataclass(frozen=True)
class PlannedFrame:
    frame: int  # analysis frame index
    source_s: float
    video_index: int  # the decoder's frame number


@dataclass
class FrameStats:
    """What actually happened to the frames a run asked for."""

    requested: int = 0
    read: int = 0
    unreadable: int = 0
    withheld: int = 0  # refused by the caller, for instance a block-edge frame
    ended_early: bool = False  # the video held fewer frames than asked
    last_frame: int | None = None  # the last analysis frame that was read

    @property
    def missing(self) -> int:
        return self.requested - self.read - self.withheld


def probe_video(path: Path) -> VideoInfo:
    import cv2  # noqa: PLC0415 - optional at import time, as in local/scoring.py

    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise FrameStreamError(f"cannot open {path}")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if not math.isfinite(fps) or fps <= 0:
            raise FrameStreamError(f"{path} reports no frame rate")
        # The camera 2 AVI says 10000000/999999: a container artefact that would add a phantom frame
        # to a whole-second window and move source seconds off the tenths the rest of the code keys
        # on. A thousandth of a frame per second is under one frame in a quarter of an hour.
        fps = round(fps, 3)
        return VideoInfo(
            path,
            fps,
            int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
    finally:
        capture.release()


def plan_frames(
    fps: float,
    start_s: float,
    duration_s: float,
    frame_stride: int = 1,
    frame_lag: int = 0,
    source_offset_s: float = 0.0,
) -> list[PlannedFrame]:
    """The frames a window asks for. Frame k is second start_s + k * stride / fps of the file."""
    if start_s < 0 or duration_s <= 0 or frame_stride < 1:
        raise ValueError("need start_s >= 0, duration_s > 0 and frame_stride >= 1")
    step_s = frame_stride / fps
    count = math.ceil(round(duration_s / step_s, 6))
    plan = []
    for k in range(count):
        file_s = start_s + k * step_s
        plan.append(
            PlannedFrame(k, round(source_offset_s + file_s, 6), round(file_s * fps) - frame_lag)
        )
    return plan


def is_camera2(path: Path) -> bool:
    """Whether this is the NGSIM camera 2 file, whose odd blocks are held out: by name, or by its
    bytes for a renamed copy."""
    if path.name == Path(VIDEO).name:
        return True
    try:
        same_size = path.stat().st_size == CAMERA2_BYTES
    except OSError:
        return False
    return same_size and sha256_file(path) == CAMERA2_SHA256


def camera2_offset_s(path: Path) -> float | None:
    """Where second 0 of this file sits in the camera 2 source, as far as the file itself shows: 0
    for the AVI, the trim start for a clip a manifest says was cut from it, None for anything
    else."""
    if is_camera2(path):
        return 0.0
    manifests = sorted(CLIP_MANIFESTS.glob("*/source_clip.json"))
    digest = sha256_file(path) if manifests else None
    for manifest in manifests:
        clip = json.loads(manifest.read_text("utf-8"))
        if clip["output_sha256"] == digest and clip["source_sha256"] == CAMERA2_SHA256:
            return float(clip["trim"]["start_s"])
    return None


def is_held_out_second(source_s: float) -> bool:
    """Whether this camera 2 second is in an odd block by either reading of its time."""
    return any(
        COVERED_START_S <= reading < COVERED_END_S and block_of(reading) % 2 == 1
        for reading in (source_s, avi_index(source_s) / 10)
    )


@dataclass(frozen=True)
class HeldOutGrant:
    """A gate's one read of held-out pixels, as claimed in the read log."""

    gate_id: str
    gate_sha256: str
    analysis_id: str
    read_log: Path


def _logged(read_log: Path) -> list[dict]:
    if not read_log.is_file():
        return []
    return [json.loads(line) for line in read_log.read_text("utf-8").splitlines() if line.strip()]


def check_grant(grant: HeldOutGrant, analysis_id: str) -> None:
    """Raises `HeldOutError` unless the read log holds this gate's read of this analysis."""
    if grant.analysis_id != analysis_id:
        raise HeldOutError(f"this grant is for {grant.analysis_id}, not {analysis_id}")
    for entry in _logged(grant.read_log):
        if (
            entry.get("gate_id") == grant.gate_id
            and entry.get("gate_sha256") == grant.gate_sha256
            and entry.get("analysis_id") == analysis_id
            and entry.get("stage", "read") == "read"
        ):
            return
    raise HeldOutError(f"{grant.read_log} holds no read of gate {grant.gate_id} for {analysis_id}")


def check_scored(read_log: Path, gate_id: str, analysis_id: str) -> None:
    """Raises `HeldOutError` unless this gate has already scored this analysis, which is when its
    held-out pixels can no longer affect the result."""
    for entry in _logged(read_log):
        if (
            entry.get("gate_id") == gate_id
            and entry.get("analysis_id") == analysis_id
            and entry.get("stage") == "score"
        ):
            return
    raise HeldOutError(f"gate {gate_id} has not scored {analysis_id}: its pixels stay held out")


def held_out_frames(plan: Sequence[PlannedFrame]) -> set[int]:
    """Check a camera 2 window against the block rule before any pixel is read.

    Raises `HeldOutError` when any frame is in an odd block. Returns the analysis frames to leave
    unread: those in an even block whose image, by the AVI's own clock, sits in the odd block
    before it (only ever the first frame of an even block). Seconds outside [120, 1020) have no
    block and no published trajectories, so nothing is held out there.
    """
    skip = set()
    for planned in plan:
        if not COVERED_START_S <= planned.source_s < COVERED_END_S:
            continue
        require_dev(planned.source_s)
        native = avi_index(planned.source_s) / 10
        if COVERED_START_S <= native < COVERED_END_S:
            try:
                require_dev(native)
            except HeldOutError:
                skip.add(planned.frame)
    return skip


def iter_frames(
    path: Path,
    start_s: float,
    duration_s: float,
    frame_stride: int = 1,
    *,
    frame_lag: int = 0,
    source_offset_s: float = 0.0,
    skip: Collection[int] = (),
    stats: FrameStats | None = None,
) -> Iterator[tuple[int, float, np.ndarray]]:
    """Yield (analysis_frame_index, source_s, frame_bgr) for a window, in time order.

    An unreadable frame is counted and skipped; the indices that follow keep their time. The stream
    stops early when the video ends. Raises `FrameStreamError` when nothing in the window can be
    read. Pass `stats` to see what happened.
    """
    import cv2  # noqa: PLC0415

    info = probe_video(path)
    stats = stats if stats is not None else FrameStats()
    plan = plan_frames(info.fps, start_s, duration_s, frame_stride, frame_lag, source_offset_s)
    stats.requested = len(plan)
    capture = cv2.VideoCapture(str(path))
    position: int | None = None  # the frame the next read returns, when known
    bad_in_a_row = 0
    try:
        for planned in plan:
            if planned.frame in skip:
                stats.withheld += 1
                continue
            index = planned.video_index
            if index < 0:
                stats.unreadable += 1
                continue
            if info.frame_count and index >= info.frame_count:
                stats.ended_early = True
                break
            if position is None or not 0 <= index - position <= SEEK_RATHER_THAN_GRAB:
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            else:
                for _ in range(index - position):
                    capture.grab()
            ok, image = capture.read()
            if not ok or image is None or image.size == 0:
                stats.unreadable += 1
                bad_in_a_row += 1
                position = None
                if bad_in_a_row >= MAX_BAD_IN_A_ROW:
                    stats.ended_early = True
                    break
                continue
            bad_in_a_row = 0
            position = index + 1
            stats.read += 1
            stats.last_frame = planned.frame
            yield planned.frame, planned.source_s, image
    finally:
        capture.release()
    if stats.read == 0:
        raise FrameStreamError(
            f"{path} holds no readable frame from {start_s} s for {duration_s} s"
        )
