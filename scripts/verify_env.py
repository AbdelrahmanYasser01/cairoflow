"""Verify the CairoFlow development environment: Python, packages, SUMO, FFmpeg, OpenCV, GPU.

Run from the repository root:  uv run python scripts/verify_env.py

This is a verification tool, not the video-ingestion module. It uses a synthetic clip so no
licensed footage or network access is needed, and it never prints the API key. GPU video
acceleration is optional: when it is unavailable that check is SKIP and CPU paths apply.
The exit code is non-zero if any required check fails.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from fractions import Fraction
from importlib import import_module, metadata
from pathlib import Path
from typing import Any, NamedTuple

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"

SAMPLE_FPS = 2.0  # initial sampling rate; raised towards 5 only if tracking needs it
BATCH_SIZE = 4  # frames per batch (plan: 4-8); only one batch is held in memory
FRAME_SIZE = (640, 360)  # (width, height) in pixels after resizing
CLIP_SECONDS = 6
MAX_SIM_SECONDS = 600
WSL_NVIDIA_SMI = Path("/usr/lib/wsl/lib/nvidia-smi")

# importable module -> distribution that provides it
MODULES: tuple[tuple[str, str], ...] = (
    ("sumo", "eclipse-sumo"),
    ("sumolib", "sumolib"),
    ("traci", "traci"),
    ("libsumo", "libsumo"),
    ("meta_sam_parser", "meta-sam-parser"),
    ("cv2", "opencv-python-headless"),
    ("numpy", "numpy"),
    ("pydantic", "pydantic"),
    ("httpx", "httpx"),
    ("openai", "openai"),
    ("cairoflow_contracts", "cairoflow"),
    ("cairoflow_perception", "cairoflow"),
    ("cairoflow_reasoning", "cairoflow"),
    ("cairoflow_simulation", "cairoflow"),
    ("cairoflow_evaluation", "cairoflow"),
)


class Outcome(NamedTuple):
    status: str
    detail: str


def _run(command: Sequence[str], timeout: float = 180.0) -> subprocess.CompletedProcess[str]:
    # stdin is closed because ffmpeg otherwise reads (and swallows) the caller's stdin.
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _ffmpeg(*args: str) -> subprocess.CompletedProcess[str]:
    return _run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args])


def _distribution_installed(name: str) -> bool:
    try:
        metadata.version(name)
    except metadata.PackageNotFoundError:
        return False
    return True


def _sumo_bin(name: str) -> Path:
    import sumo

    return Path(sumo.SUMO_HOME) / "bin" / name


def _ensure_network(work: Path) -> Path:
    from cairoflow_simulation.network import NET_FILE, build_network

    net_path = work / "net" / NET_FILE
    if not net_path.exists():
        build_network(work / "net")
    return net_path


def _ensure_clip(work: Path) -> Path:
    clip = work / "clip.mp4"
    if not clip.exists():
        result = _ffmpeg(
            *("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", str(CLIP_SECONDS)),
            *("-pix_fmt", "yuv420p", "-c:v", "libx264", "-movflags", "+faststart", str(clip)),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"could not create the synthetic clip: {result.stderr.strip()[:300]}"
            )
    return clip


def _sumo_command(work: Path) -> list[str]:
    net_path = _ensure_network(work)
    routes = work / "routes.rou.xml"
    routes.write_text(
        '<routes>\n  <vehicle id="v0" depart="0" departLane="best">'
        '<route edges="N_in S_out"/></vehicle>\n</routes>\n',
        encoding="utf-8",
    )
    return [
        str(_sumo_bin("sumo")), "-n", str(net_path), "-r", str(routes),
        "--seed", "1", "--no-step-log", "true", "--no-warnings", "true",
    ]  # fmt: skip


def _drain(api: Any) -> tuple[int, float]:
    """Step a started SUMO (traci or libsumo) until empty; return (arrived, end time in s)."""
    arrived = 0
    while api.simulation.getMinExpectedNumber() > 0 and api.simulation.getTime() < MAX_SIM_SECONDS:
        api.simulationStep()
        arrived += api.simulation.getArrivedNumber()
    return arrived, api.simulation.getTime()


def check_python(work: Path) -> Outcome:
    ok = sys.version_info[:2] == (3, 12)
    return Outcome(PASS if ok else FAIL, f"Python {sys.version.split()[0]} at {sys.executable}")


def check_imports(work: Path) -> Outcome:
    versions: dict[str, str] = {}
    problems: list[str] = []
    for module, distribution in MODULES:
        try:
            import_module(module)
            versions[distribution] = metadata.version(distribution)
        except Exception as exc:  # report every import failure, not just the first
            problems.append(f"{module}: {type(exc).__name__}: {exc}")
    if _distribution_installed("opencv-python"):
        problems.append("opencv-python (GUI build) is installed and would shadow the headless one")
    if problems:
        return Outcome(FAIL, "; ".join(problems))
    return Outcome(PASS, ", ".join(f"{name}=={version}" for name, version in versions.items()))


def check_sumo_binary(work: Path) -> Outcome:
    result = _run([str(_sumo_bin("sumo")), "--version"], timeout=60)
    if result.returncode != 0:
        hint = ""
        if "libatomic" in result.stderr:
            hint = " Fix: sudo apt-get install -y libatomic1"
        return Outcome(
            FAIL, f"sumo exited {result.returncode}: {result.stderr.strip()[:160]}{hint}"
        )
    first_line = result.stdout.splitlines()[0]
    expected = metadata.version("eclipse-sumo")
    return Outcome(PASS if expected in first_line else FAIL, first_line)


def check_network(work: Path) -> Outcome:
    from cairoflow_simulation.network import build_network

    build = build_network(work / "net")
    manifest = build.manifest
    ok = manifest.link_count == 12
    return Outcome(
        PASS if ok else FAIL,
        f"{manifest.link_count} signal links, net sha256 {manifest.net_sha256[:12]}, "
        f"{len(manifest.netconvert_warnings)} netconvert warnings",
    )


def check_traci_run(work: Path) -> Outcome:
    import traci

    traci.start(_sumo_command(work), label="verify_env")
    try:
        arrived, end_time = _drain(traci)
    finally:
        traci.close()
    return Outcome(
        PASS if arrived == 1 else FAIL, f"{arrived}/1 vehicle arrived by t={end_time:g}s"
    )


def check_libsumo_run(work: Path) -> Outcome:
    import libsumo

    libsumo.start(_sumo_command(work))
    try:
        arrived, end_time = _drain(libsumo)
    finally:
        libsumo.close()
    return Outcome(
        PASS if arrived == 1 else FAIL, f"{arrived}/1 vehicle arrived by t={end_time:g}s"
    )


def check_ffmpeg(work: Path) -> Outcome:
    versions = []
    for tool in ("ffmpeg", "ffprobe"):
        result = _run([tool, "-version"], timeout=30)
        if result.returncode != 0:
            return Outcome(FAIL, f"{tool} not runnable: {result.stderr.strip()[:160]}")
        versions.append(result.stdout.splitlines()[0].split(" Copyright")[0])
    encoders = _run(["ffmpeg", "-hide_banner", "-encoders"], timeout=30).stdout
    if " libx264 " not in encoders:
        return Outcome(FAIL, "ffmpeg has no libx264 encoder")
    return Outcome(PASS, "; ".join(versions) + "; libx264 present")


def check_ffprobe_metadata(work: Path) -> Outcome:
    clip = _ensure_clip(work)
    probe = _run(
        [
            *("ffprobe", "-v", "error", "-select_streams", "v:0"),
            *("-show_entries", "stream=codec_name,width,height,avg_frame_rate,nb_frames,duration"),
            *("-of", "json", str(clip)),
        ],
        timeout=60,
    )
    if probe.returncode != 0:
        return Outcome(FAIL, probe.stderr.strip()[:200])
    stream = json.loads(probe.stdout)["streams"][0]
    fps = Fraction(stream["avg_frame_rate"])
    ok = (stream["codec_name"], stream["width"], stream["height"], fps) == (
        "h264", 1280, 720, 30,
    )  # fmt: skip
    return Outcome(
        PASS if ok else FAIL,
        f"{stream['codec_name']} {stream['width']}x{stream['height']} @ {float(fps):g} fps, "
        f"{stream['nb_frames']} frames, {float(stream['duration']):g}s",
    )


def check_opencv_sampling(work: Path) -> Outcome:
    """Sequential decode, ~2 fps sampling, resize, timestamps, batches; never the whole video."""
    import cv2

    capture = cv2.VideoCapture(str(_ensure_clip(work)))
    if not capture.isOpened():
        return Outcome(FAIL, "OpenCV could not open the clip")
    step = round(capture.get(cv2.CAP_PROP_FPS) / SAMPLE_FPS)
    timestamps: list[float] = []
    batch: list[Any] = []
    batches = 0
    shape: tuple[int, ...] = ()
    index = -1
    try:
        while capture.grab():  # grab() advances without converting skipped frames
            index += 1
            if index % step:
                continue
            ok, frame = capture.retrieve()
            if not ok:
                return Outcome(FAIL, f"retrieve failed at source frame {index}")
            timestamps.append(capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
            batch.append(cv2.resize(frame, FRAME_SIZE, interpolation=cv2.INTER_AREA))
            shape = batch[-1].shape
            if len(batch) == BATCH_SIZE:
                batches, batch = batches + 1, []
        batches += bool(batch)
    finally:
        capture.release()
    gaps = [later - earlier for earlier, later in zip(timestamps, timestamps[1:], strict=False)]
    expected = int(CLIP_SECONDS * SAMPLE_FPS)
    ok = (
        len(timestamps) == expected
        and shape == (FRAME_SIZE[1], FRAME_SIZE[0], 3)
        and all(abs(gap - 1 / SAMPLE_FPS) < 0.05 for gap in gaps)
    )
    return Outcome(
        PASS if ok else FAIL,
        f"{len(timestamps)} frames in {batches} batches of <= {BATCH_SIZE}, shape {shape}, "
        f"timestamps {timestamps[0]:.2f}s..{timestamps[-1]:.2f}s",
    )


def check_gpu_visible(work: Path) -> Outcome:
    smi = shutil.which("nvidia-smi") or (str(WSL_NVIDIA_SMI) if WSL_NVIDIA_SMI.exists() else None)
    if smi is None:
        return Outcome(SKIP, "nvidia-smi not found; GPU not visible, CPU paths only")
    query = ["--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]
    result = _run([smi, *query], timeout=30)
    if result.returncode != 0:
        return Outcome(SKIP, f"nvidia-smi failed: {result.stderr.strip()[:160]}")
    return Outcome(PASS, result.stdout.strip())


def check_gpu_video(work: Path) -> Outcome:
    clip = _ensure_clip(work)
    encode = _ffmpeg(
        *("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30", "-t", "1"),
        *("-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", str(work / "nvenc.mp4")),
    )
    decode = _ffmpeg(
        *("-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", str(clip)),
        *("-f", "null", "-"),
    )
    nvenc, nvdec = encode.returncode == 0, decode.returncode == 0
    detail = f"NVENC {'ok' if nvenc else 'unavailable'}, NVDEC {'ok' if nvdec else 'unavailable'}"
    if nvenc and nvdec:
        return Outcome(PASS, detail)
    return Outcome(SKIP, f"{detail}; CPU fallback (libx264 / software decode) applies")


def check_settings(work: Path) -> Outcome:
    from cairoflow_contracts.config import load_settings

    settings = load_settings()
    summary = (
        f"base_url={settings.meta_base_url}, muse={settings.muse_model}, sam={settings.sam_model}"
    )
    if not settings.has_api_key:
        return Outcome(SKIP, f"no API key configured (live model calls disabled); {summary}")
    return Outcome(PASS, f"API key configured (value not shown); {summary}")


CHECKS: tuple[tuple[str, Callable[[Path], Outcome]], ...] = (
    ("python", check_python),
    ("imports", check_imports),
    ("sumo-binary", check_sumo_binary),
    ("sumo-network", check_network),
    ("sumo-traci-run", check_traci_run),
    ("sumo-libsumo-run", check_libsumo_run),
    ("ffmpeg", check_ffmpeg),
    ("ffprobe-metadata", check_ffprobe_metadata),
    ("opencv-sampling", check_opencv_sampling),
    ("gpu-visible", check_gpu_visible),
    ("gpu-video", check_gpu_video),
    ("settings", check_settings),
)


def main() -> int:
    counts = {PASS: 0, FAIL: 0, SKIP: 0}
    with tempfile.TemporaryDirectory(prefix="cairoflow_verify_") as scratch:
        work = Path(scratch)
        for name, check in CHECKS:
            try:
                outcome = check(work)
            except Exception as exc:  # a crashing check is a failed check, not a crashed script
                outcome = Outcome(FAIL, f"{type(exc).__name__}: {exc}")
            counts[outcome.status] += 1
            print(f"{outcome.status:<5} {name:<18} {outcome.detail}", flush=True)
    print(f"\n{counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    sys.exit(main())
