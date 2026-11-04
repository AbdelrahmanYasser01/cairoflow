"""The live analysis worker: one job directory in, one analysis directory out.

    <CAIROFLOW_VISION_PYTHON> scripts/cv_run.py --job-dir DIR [--detector NAME] \
        [--detector-arg name=value]

Requirements of the vision environment: Python 3.12 with pydantic>=2, numpy and
opencv-python-headless, plus what the detector in data/cv/detector.json needs (for instance torch
and its weights). The repository's packages are found from this file's location, so nothing of ours
has to be installed; ffmpeg is only needed to render a video, not by this worker.

Protocol: cairoflow-job/1, as written in apps/api/cairoflow_api/product/jobs.py.

  in   DIR/job.json: protocol, job_id, video_path, calibrated, source_start_s (where second 0 of
       this file sits in the camera 2 source; null when unknown), start_s (seconds of this file),
       duration_s, frame_stride, analyses_dir. One optional field beyond it: analysis_id (default
       cv-<job_id>). A job never reads held-out footage: allow_held_out is refused, because a gate
       read goes through scripts/cv_analyse.py --held-out-gate, which logs it first.
  out  DIR/progress.jsonl: one JSON object per line, appended and flushed, with stage, frame,
       frames_total, fps, detections, tracks, elapsed_s, message; a field not known is left out.
       DIR/preview.jpg: the latest annotated frame, replaced atomically.
       On success: <analyses_dir>/<analysis_id>/, then {"stage": "done", "analysis_id": ...},
       exit 0.
       On failure: {"stage": "failed", "error": ...}, exit 1.
       On SIGTERM: stops at the next frame, writes no analysis, reports
       {"stage": "failed", "error": "cancelled"}, exit 143.

The detector comes from data/cv/detector.json (see `DetectorSettings`) or --detector. A detector
that needs CUDA falls back to CPU when CUDA is absent, with the stride raised to the settings'
`cpu_min_frame_stride`, and the record says so. torch is only imported through the detector or the
CUDA check, at run time.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import traceback
from pathlib import Path
from threading import Event
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for package in ("perception", "contracts", "evaluation"):
    path = str(ROOT / "packages" / package)
    if path not in sys.path:
        sys.path.insert(0, path)

from cairoflow_perception.local import pipeline as P  # noqa: E402
from cairoflow_perception.local.tracker import TrackerParams  # noqa: E402
from cairoflow_perception.site_int2 import load_site  # noqa: E402

SETTINGS = ROOT / "data/cv/detector.json"
PROTOCOL = "cairoflow-job/1"
EXIT_FAILED, EXIT_CANCELLED = 1, 143
REQUIRED = ("job_id", "video_path", "start_s", "duration_s", "frame_stride", "analyses_dir")


class Reporter:
    """Appends one JSON line per update, flushed so a reader sees it at once."""

    def __init__(self, path: Path) -> None:
        self._file = path.open("a", encoding="utf-8")

    def write(self, payload: dict[str, Any]) -> None:
        self._file.write(json.dumps(payload) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def read_job(job_dir: Path) -> dict[str, Any]:
    job = json.loads((job_dir / "job.json").read_text("utf-8"))
    if job.get("protocol") != PROTOCOL:
        raise ValueError(f"job.json speaks {job.get('protocol')!r}, this worker {PROTOCOL!r}")
    missing = [name for name in REQUIRED if name not in job]
    if missing:
        raise ValueError(f"job.json is missing {', '.join(missing)}")
    if job.get("allow_held_out"):
        raise ValueError("a job cannot read held-out footage: use cv_analyse.py --held-out-gate")
    return job


def settings_for(args: argparse.Namespace) -> P.DetectorSettings:
    extra = {}
    for item in args.detector_arg:
        key, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"--detector-arg wants name=value, got {item!r}")
        extra[key] = value
    if args.detector:
        return P.DetectorSettings(detector=args.detector, args=extra)
    if not SETTINGS.is_file():
        raise FileNotFoundError(f"{SETTINGS} is missing and no --detector was given")
    settings = P.DetectorSettings.load(SETTINGS)
    settings.args.update(extra)
    return settings


def run_job(args: argparse.Namespace, reporter: Reporter, stop: Event) -> str:
    job = read_job(args.job_dir)
    video = Path(job["video_path"])
    settings = settings_for(args)
    plan = P.plan_device(settings, int(job["frame_stride"]))
    site = load_site(ROOT) if job.get("calibrated") else None
    reporter.write(
        {"stage": "load", "message": f"loading detector {settings.detector} on {plan.device}"}
    )
    offset = job.get("source_start_s")
    offset = None if offset is None else float(offset)
    detector = P.build_detector(settings.detector, settings.args, video, source_offset_s=offset)
    if stop.is_set():
        raise P.AnalysisCancelled("cancelled")
    params = settings.tracker_params() if settings.tracker else TrackerParams()
    record = P.run_analysis(
        video,
        float(job["start_s"]),
        float(job["duration_s"]),
        plan.frame_stride,
        detector,
        site,
        Path(job["analyses_dir"]),
        str(job.get("analysis_id") or f"cv-{job['job_id']}"),
        lambda update: reporter.write(update.as_dict()),
        stop.is_set,
        mode="live",
        preview_path=args.job_dir / "preview.jpg",
        tracker_params=params,
        source_offset_s=offset,
        notes=plan.notes,
        device=plan.device,
    )
    return record.analysis_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--detector", help="overrides data/cv/detector.json")
    parser.add_argument("--detector-arg", action="append", default=[], help="name=value")
    args = parser.parse_args(argv)

    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    reporter = Reporter(args.job_dir / "progress.jsonl")
    try:
        analysis_id = run_job(args, reporter, stop)
    except P.AnalysisCancelled:
        reporter.write({"stage": "failed", "error": "cancelled"})
        return EXIT_CANCELLED
    except Exception as error:  # noqa: BLE001 - every failure must reach the job's reader
        traceback.print_exc()
        reporter.write({"stage": "failed", "error": f"{type(error).__name__}: {error}"})
        return EXIT_FAILED
    else:
        reporter.write({"stage": "done", "analysis_id": analysis_id})
        return 0
    finally:
        reporter.close()


if __name__ == "__main__":
    raise SystemExit(main())
