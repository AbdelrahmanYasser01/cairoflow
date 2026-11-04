"""Video ingestion: turn a licensed source file into the clip that is uploaded to SAM 3.1.

The output is H.264 MP4 at a fixed frame rate (10 fps by default, to keep the per-frame SAM bill
down) with the index at the front (`+faststart`), as the SAM docs recommend. Metadata is stripped
and bit-exact flags are set, so the same source, trim and settings give a byte-identical file;
`clip_id` is derived from that file's hash.

Ingestion refuses a clip whose licence does not allow upload to a hosted model API, and it never
overwrites: a directory that already holds a clip or manifest is an error.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from fractions import Fraction
from importlib import metadata
from pathlib import Path

from cairoflow_contracts.evidence import (
    ClipLicense,
    FrameRef,
    Provenance,
    SourceClip,
    TrimSpec,
    VideoStreamInfo,
)

MANIFEST_FILE = "source_clip.json"
OUTPUT_FILE = "clip.mp4"
DEFAULT_TARGET_FPS = 10
X264_PRESET = "medium"
X264_CRF = 18
SOURCE_PLACEHOLDER = "{source}"
OUTPUT_PLACEHOLDER = "{output}"


class IngestError(RuntimeError):
    """Raised when a clip cannot be ingested; nothing is left behind in the output directory."""


def _run(
    command: Sequence[str], cwd: Path | None = None, timeout: float = 600.0
) -> subprocess.CompletedProcess[str]:
    # stdin is closed because ffmpeg otherwise reads (and swallows) the caller's stdin.
    return subprocess.run(
        list(command),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ffmpeg_version() -> str:
    result = _run(["ffmpeg", "-version"], timeout=30)
    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    parts = first_line.split()
    return parts[2] if len(parts) > 2 and parts[1] == "version" else "unknown"


def probe_video(path: Path) -> VideoStreamInfo:
    """Read the first video stream with ffprobe, counting frames by decoding them."""
    entries = "stream=codec_name,pix_fmt,width,height,avg_frame_rate,nb_read_frames"
    result = _run(
        [
            *("ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames"),
            *("-show_entries", entries, "-show_entries", "format=duration"),
            *("-of", "json", str(path)),
        ],
        timeout=300,
    )
    if result.returncode != 0:
        raise IngestError(f"ffprobe failed on {path.name}: {result.stderr.strip()[:300]}")
    probe = json.loads(result.stdout)
    streams = probe.get("streams") or []
    if not streams:
        raise IngestError(f"{path.name} has no video stream")
    stream = streams[0]
    fps = Fraction(stream.get("avg_frame_rate") or "0/1")
    if fps <= 0:
        raise IngestError(f"{path.name} reports no average frame rate")
    return VideoStreamInfo(
        codec=stream["codec_name"],
        pixel_format=stream.get("pix_fmt", "unknown"),
        width_px=int(stream["width"]),
        height_px=int(stream["height"]),
        fps=float(fps),
        frame_count=int(stream["nb_read_frames"]),
        duration_s=float(probe["format"]["duration"]),
    )


def frame_timestamps(path: Path) -> list[float]:
    """Presentation time of every video frame, in seconds from the first frame."""
    result = _run(
        [
            *("ffprobe", "-v", "error", "-select_streams", "v:0"),
            *("-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", str(path)),
        ],
        timeout=300,
    )
    if result.returncode != 0:
        raise IngestError(f"ffprobe could not list frames of {path.name}")
    times = [float(line.strip().rstrip(",")) for line in result.stdout.splitlines() if line.strip()]
    if not times:
        raise IngestError(f"{path.name} has no decodable frames")
    return [round(t - times[0], 6) for t in times]


def transcode_command(trim: TrimSpec, target_fps: int) -> list[str]:
    """The ffmpeg command with {source} and {output} placeholders, as recorded in the manifest."""
    duration = ["-t", f"{trim.duration_s:g}"] if trim.duration_s is not None else []
    return [
        *("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"),
        *("-ss", f"{trim.start_s:g}", *duration, "-i", SOURCE_PLACEHOLDER),
        *("-map", "0:v:0", "-an", "-sn", "-dn"),
        # yuv420p needs even dimensions; the scale is a no-op for even-sized sources.
        *("-vf", f"fps={target_fps},scale=trunc(iw/2)*2:trunc(ih/2)*2"),
        *("-c:v", "libx264", "-preset", X264_PRESET, "-crf", str(X264_CRF), "-pix_fmt", "yuv420p"),
        *("-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact"),
        *("-movflags", "+faststart", OUTPUT_PLACEHOLDER),
    ]


def _check_trim(trim: TrimSpec, source: VideoStreamInfo) -> None:
    if trim.start_s >= source.duration_s:
        raise IngestError(
            f"trim starts at {trim.start_s:g}s, after the end ({source.duration_s:g}s)"
        )
    if trim.duration_s is not None and trim.start_s + trim.duration_s > source.duration_s + 1e-6:
        raise IngestError(
            f"trim ends at {trim.start_s + trim.duration_s:g}s, after the end "
            f"({source.duration_s:g}s)"
        )


def ingest_clip(
    source: Path,
    licence: ClipLicense,
    out_dir: Path,
    *,
    trim: TrimSpec | None = None,
    target_fps: int = DEFAULT_TARGET_FPS,
    now: datetime | None = None,
) -> SourceClip:
    """Trim and transcode `source` into `out_dir`, write its manifest, and return it."""
    trim = trim or TrimSpec()
    if not licence.usable_for_upload:
        raise IngestError(
            f"licence of '{licence.source_id}' does not permit commercial use and upload to a "
            "hosted model API; see docs/DATA_REGISTER.md"
        )
    if not source.is_file():
        raise IngestError(f"source file not found: {source}")
    manifest_path, output_path = out_dir / MANIFEST_FILE, out_dir / OUTPUT_FILE
    if manifest_path.exists() or output_path.exists():
        raise IngestError(f"refusing to overwrite an existing ingest in {out_dir}")

    source_stream = probe_video(source)
    _check_trim(trim, source_stream)
    source_sha256 = sha256_file(source)

    template = transcode_command(trim, target_fps)
    substitutions = {SOURCE_PLACEHOLDER: str(source.resolve()), OUTPUT_PLACEHOLDER: OUTPUT_FILE}
    command = [substitutions.get(arg, arg) for arg in template]
    out_dir.mkdir(parents=True, exist_ok=True)
    result = _run(command, cwd=out_dir)  # relative output name: no absolute path in the file
    try:
        if result.returncode != 0:
            raise IngestError(f"ffmpeg failed: {result.stderr.strip()[:300]}")
        output_stream = probe_video(output_path)
        if output_stream.codec != "h264" or abs(output_stream.fps - target_fps) > 1e-6:
            raise IngestError(
                f"unexpected output: {output_stream.codec} at {output_stream.fps:g} fps"
            )
        timestamps = frame_timestamps(output_path)
        if len(timestamps) != output_stream.frame_count:
            raise IngestError("frame list and frame count disagree")
        output_sha256 = sha256_file(output_path)
        clip_id = output_sha256[:16]
        clip = SourceClip(
            clip_id=clip_id,
            licence=licence,
            source_file_name=source.name,
            source_sha256=source_sha256,
            source_stream=source_stream,
            trim=trim,
            target_fps=target_fps,
            output_file=OUTPUT_FILE,
            output_sha256=output_sha256,
            output_stream=output_stream,
            ffmpeg_command=tuple(template),
            frames=tuple(
                FrameRef(clip_id=clip_id, frame_index=index, timestamp_s=timestamp)
                for index, timestamp in enumerate(timestamps)
            ),
            provenance=Provenance(
                kind="observed",
                produced_by="cairoflow_perception.ingest",
                produced_at_utc=now or datetime.now(UTC),
                input_sha256=(source_sha256,),
                parameters={"target_fps": target_fps, "preset": X264_PRESET, "crf": X264_CRF},
                software={"ffmpeg": ffmpeg_version(), "cairoflow": metadata.version("cairoflow")},
            ),
        )
        with manifest_path.open("x", encoding="utf-8") as handle:
            handle.write(clip.model_dump_json(indent=2) + "\n")
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    return clip


def load_source_clip(manifest_path: Path) -> SourceClip:
    """Read a manifest and check that the clip next to it is the one it describes."""
    clip = SourceClip.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    output = manifest_path.parent / clip.output_file
    if not output.is_file():
        raise IngestError(f"clip file missing: {output}")
    if sha256_file(output) != clip.output_sha256:
        raise IngestError(f"{output.name} does not match its manifest (SHA-256 differs)")
    return clip


def new_run_dir(
    root: Path = Path("experiments/runs"), stage: str = "ingest", now: datetime | None = None
) -> Path:
    """Create `<root>/<UTC stamp>_<random>/<stage>`; fails rather than reuse a directory."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{stamp}_{uuid.uuid4().hex[:6]}" / stage
    path.mkdir(parents=True, exist_ok=False)
    return path
