"""Selectable videos and uploads.

A source id is resolved against this catalogue, never against a client path: the catalogue walks
three known places (the camera 2 source, ingested clips, data/uploads) and hands out ids, so no
request can name a file outside them. `calibrated` is true only for footage a committed calibration
record is about: the camera 2 video, proved by its SHA-256, and clips cut from it whose record says
where they start. A clip cut from it that does not say has a `problem` and takes no jobs: the
held-out rule needs that offset. `playable` says a browser can play the file, which is not the same
as analysable: the camera 2 source is an AVI.

Uploads are streamed to a hidden part file, probed, and only then given their name; a file ffprobe
cannot read as video, or reads as a container not on the list, is refused and deleted. The container
check matters: ffprobe follows a playlist named .mp4, so the format is taken from the bytes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import uuid
from collections.abc import AsyncIterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from cairoflow_api.product.settings import ProductSettings, ignored_directory

VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm"})
PROBE_CONTAINERS = ("mov,mp4,m4a,3gp,3g2,mj2", "avi", "matroska,webm")
BROWSER_CONTAINERS = ("mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm")
BROWSER_CODECS = frozenset({"h264", "vp8", "vp9", "av1"})
PROBE_TIMEOUT_S = 30
PART_PREFIX = ".part-"
MAX_NAME_CHARS = 120
CAMERA_SOURCE_ID = "camera-2"
CALIBRATION_NOTE = (
    "Camera 2 calibration: ground positions are approximate, and a gate measured placement only "
    "in the windows the analysis record lists."
)
NO_TRIM_START = (
    "This clip was cut from the camera 2 video, but its source_clip.json gives no usable trim "
    "start, so the held-out rule cannot be applied to it. Cut it again to analyse it."
)


class VideoProbeError(ValueError):
    """ffprobe could not read the file as a video."""


class UploadRefused(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class VideoProbe:
    duration_s: float
    width_px: int
    height_px: int
    fps: float | None
    codec: str
    container: str

    @property
    def playable(self) -> bool:
        return self.container in BROWSER_CONTAINERS and self.codec in BROWSER_CODECS


class Source(BaseModel):
    """One selectable video, as the API shows it. `problem` says why it cannot be analysed."""

    source_id: str
    kind: str
    title: str
    file_name: str
    bytes: int
    duration_s: float | None
    width_px: int | None
    height_px: int | None
    fps: float | None
    codec: str | None
    calibrated: bool
    calibration_note: str | None
    playable: bool
    source_start_s: float | None
    problem: str | None


def _rate(text: str | None) -> float | None:
    numerator, _, denominator = (text or "").partition("/")
    try:
        value = float(numerator) / float(denominator or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def probe_video(path: Path, ffprobe: str = "ffprobe") -> VideoProbe:
    """Read a video's stream facts with ffprobe. Files only: no network, no shell."""
    command = [
        ffprobe, "-v", "error", "-protocol_whitelist", "file", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]  # fmt: skip
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S, check=False
        )
    except FileNotFoundError as error:
        raise VideoProbeError("ffprobe is not installed") from error
    except subprocess.TimeoutExpired as error:
        raise VideoProbeError(f"ffprobe took longer than {PROBE_TIMEOUT_S} s") from error
    if done.returncode != 0:
        raise VideoProbeError("ffprobe could not read the file as a video")
    try:
        facts = json.loads(done.stdout)
    except json.JSONDecodeError as error:
        raise VideoProbeError("ffprobe gave no usable answer") from error
    container = facts.get("format", {}).get("format_name", "")
    if container not in PROBE_CONTAINERS:
        raise VideoProbeError(f"container {container or 'unknown'} is not accepted")
    stream = next((s for s in facts.get("streams", []) if s.get("codec_type") == "video"), None)
    if stream is None:
        raise VideoProbeError("the file has no video stream")
    duration = facts.get("format", {}).get("duration") or stream.get("duration")
    try:
        duration_s = float(duration)
        width_px, height_px = int(stream["width"]), int(stream["height"])
    except (KeyError, TypeError, ValueError) as error:
        raise VideoProbeError("the video stream has no duration or size") from error
    if duration_s <= 0 or width_px <= 0 or height_px <= 0:
        raise VideoProbeError("the video stream has no duration or size")
    return VideoProbe(
        duration_s=duration_s,
        width_px=width_px,
        height_px=height_px,
        fps=_rate(stream.get("avg_frame_rate")),
        codec=str(stream.get("codec_name", "unknown")),
        container=container,
    )


def sanitise_upload_name(name: str) -> str:
    """A safe file name for data/uploads. Anything that tries to be a path is refused outright;
    other unusual characters become underscores."""
    if "/" in name or "\\" in name or "\x00" in name or ".." in name:
        raise UploadRefused(400, "the name must be a plain file name, not a path")
    pure = PurePosixPath(name)
    suffix = pure.suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        allowed = ", ".join(sorted(VIDEO_EXTENSIONS))
        raise UploadRefused(415, f"only video files are accepted ({allowed})")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", pure.stem).strip("._-")[:MAX_NAME_CHARS]
    if not stem:
        raise UploadRefused(400, "the name has no usable characters")
    return f"{stem}{suffix}"


class SourceCatalog:
    def __init__(self, settings: ProductSettings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._probes: dict[tuple[str, int, int], VideoProbe | str] = {}
        self._digests: dict[tuple[str, int, int], str] = {}

    def clean_parts(self) -> None:
        """Drop part files an interrupted upload left behind."""
        directory = self._settings.uploads_dir
        if directory.is_dir():
            for path in directory.glob(f"{PART_PREFIX}*"):
                path.unlink(missing_ok=True)

    def _key(self, path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return str(path), stat.st_mtime_ns, stat.st_size

    def _probe(self, path: Path) -> VideoProbe | str:
        key = self._key(path)
        with self._lock:
            found = self._probes.get(key)
        if found is not None:
            return found
        try:
            result: VideoProbe | str = probe_video(path, self._settings.ffprobe)
        except VideoProbeError as error:
            result = str(error)
        with self._lock:
            self._probes[key] = result
        return result

    def _camera_sha256(self) -> str | None:
        """The video the committed calibration work names; None when the record is absent."""
        path = self._settings.camera_calibration
        if not path.is_file():
            return None
        return json.loads(path.read_text("utf-8")).get("source_video_sha256")

    def _digest(self, path: Path) -> str:
        key = self._key(path)
        with self._lock:
            found = self._digests.get(key)
        if found is not None:
            return found
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
        with self._lock:
            self._digests[key] = digest.hexdigest()
        return digest.hexdigest()

    def _source(
        self,
        path: Path,
        *,
        source_id: str,
        kind: str,
        title: str,
        calibrated: bool,
        source_start_s: float | None = None,
        problem: str | None = None,
    ) -> Source:
        probe = self._probe(path)
        known = isinstance(probe, VideoProbe)
        return Source(
            source_id=source_id,
            kind=kind,
            title=title,
            file_name=path.name,
            bytes=path.stat().st_size,
            duration_s=probe.duration_s if known else None,
            width_px=probe.width_px if known else None,
            height_px=probe.height_px if known else None,
            fps=probe.fps if known else None,
            codec=probe.codec if known else None,
            calibrated=calibrated,
            calibration_note=CALIBRATION_NOTE if calibrated else None,
            playable=probe.playable if known else False,
            source_start_s=source_start_s,
            problem=problem if known else probe,
        )

    def _entries(self) -> list[tuple[Source, Path]]:
        settings = self._settings
        camera_sha256 = self._camera_sha256()
        entries: list[tuple[Source, Path]] = []
        camera = settings.camera_source
        if camera.is_file():
            entries.append(
                (
                    self._source(
                        camera,
                        source_id=CAMERA_SOURCE_ID,
                        kind="camera_source",
                        title="NGSIM Lankershim camera 2, 08:30-08:45 (source file)",
                        calibrated=camera_sha256 == self._digest(camera),
                        source_start_s=0.0,
                    ),
                    camera,
                )
            )
        if settings.clips_dir.is_dir():
            for directory in sorted(p for p in settings.clips_dir.iterdir() if p.is_dir()):
                clip = directory / "clip.mp4"
                if not clip.is_file():
                    continue
                facts = _clip_facts(directory / "source_clip.json")
                from_camera = bool(camera_sha256 and facts.get("source_sha256") == camera_sha256)
                start_s = _trim_start_s(facts) if from_camera else None
                entries.append(
                    (
                        self._source(
                            clip,
                            source_id=f"clip-{directory.name}",
                            kind="clip",
                            title=directory.name,
                            calibrated=start_s is not None,
                            source_start_s=start_s,
                            problem=NO_TRIM_START if from_camera and start_s is None else None,
                        ),
                        clip,
                    )
                )
        if settings.uploads_dir.is_dir():
            for path in sorted(settings.uploads_dir.iterdir()):
                usable = path.suffix.lower() in VIDEO_EXTENSIONS and not path.name.startswith(".")
                if path.is_file() and usable:
                    entries.append((self._upload_source(path), path))
        return entries

    def _upload_source(self, path: Path) -> Source:
        return self._source(
            path,
            source_id=f"upload-{path.name}",
            kind="upload",
            title=path.name,
            calibrated=False,
        )

    def sources(self) -> list[Source]:
        return [source for source, _ in self._entries()]

    def resolve(self, source_id: str) -> tuple[Source, Path] | None:
        return next((entry for entry in self._entries() if entry[0].source_id == source_id), None)

    async def save_upload(
        self, name: str, body: AsyncIterable[bytes], declared_bytes: int | None
    ) -> Source:
        """Stream a request body into data/uploads under a sanitised name."""
        safe = sanitise_upload_name(name)
        limit = self._settings.max_upload_bytes
        if declared_bytes is not None and declared_bytes > limit:
            raise UploadRefused(413, f"uploads are limited to {limit} bytes")
        directory = ignored_directory(self._settings.uploads_dir)
        target = directory / safe
        if target.exists():
            raise UploadRefused(409, f"{safe} already exists; uploads are never overwritten")
        part = directory / f"{PART_PREFIX}{uuid.uuid4().hex}{target.suffix}"
        try:
            written = 0
            with part.open("wb") as handle:
                async for chunk in body:
                    written += len(chunk)
                    if written > limit:
                        raise UploadRefused(413, f"uploads are limited to {limit} bytes")
                    await asyncio.to_thread(handle.write, chunk)  # a slow disk stalls no stream
            if written == 0:
                raise UploadRefused(400, "the request body is empty")
            try:
                await asyncio.to_thread(probe_video, part, self._settings.ffprobe)
            except VideoProbeError as error:
                raise UploadRefused(415, f"not a usable video: {error}") from error
            try:
                os.link(part, target)  # fails if the name was taken meanwhile
            except FileExistsError as error:
                raise UploadRefused(409, f"{safe} already exists") from error
        finally:
            part.unlink(missing_ok=True)
        return await asyncio.to_thread(self._upload_source, target)


def _clip_facts(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        facts = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return facts if isinstance(facts, dict) else {}


def _trim_start_s(facts: dict) -> float | None:
    """Where a clip starts in its source, in seconds; None unless its record says so as a finite,
    non-negative number."""
    trim = facts.get("trim")
    start_s = trim.get("start_s") if isinstance(trim, dict) else None
    if isinstance(start_s, bool) or not isinstance(start_s, int | float):
        return None
    try:
        value = float(start_s)
    except OverflowError:  # JSON integers have no limit
        return None
    return value if math.isfinite(value) and value >= 0 else None
