"""Run the CV pipeline on a stretch of video and write a precomputed analysis.

    .venv/bin/python scripts/cv_analyse.py --start-s 960 --duration-s 60 --id cv-int2-960-1020

The detector comes from data/cv/detector.json, or --detector (a name or package.module:factory).
--start-s counts in seconds of the file. The camera 2 AVI, or a clip of it (known by its manifest
or named by --source-offset-s), is calibrated unless --uncalibrated; any other video is
uncalibrated. Odd blocks of camera 2 are held out: such a window is refused, exit 2, unless
--held-out-gate names a committed, clean gate file, whose one read this run then spends in
data/gates/held_out_reads.jsonl before any pixel is read. Analyses are immutable, so an existing
--id is refused.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("perception", "contracts", "evaluation"):
    path = str(ROOT / "packages" / package)
    if path not in sys.path:
        sys.path.insert(0, path)

from cairoflow_evaluation import cv_agreement as A  # noqa: E402
from cairoflow_perception.local import pipeline as P  # noqa: E402
from cairoflow_perception.local.frames import camera2_offset_s  # noqa: E402
from cairoflow_perception.local.scoring import VIDEO, HeldOutError  # noqa: E402
from cairoflow_perception.local.tracker import TrackerParams  # noqa: E402
from cairoflow_perception.site_int2 import load_site  # noqa: E402

SETTINGS = ROOT / "data/cv/detector.json"


def key_values(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--detector-arg wants key=value, got {item!r}")
        out[key] = value
    return out


def settings_from(args: argparse.Namespace) -> P.DetectorSettings:
    if args.detector:
        return P.DetectorSettings(detector=args.detector, args=key_values(args.detector_arg))
    if not SETTINGS.is_file():
        raise SystemExit(f"{SETTINGS} is missing; pass --detector")
    settings = P.DetectorSettings.load(SETTINGS)
    settings.args.update(key_values(args.detector_arg))
    return settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--video", type=Path, default=ROOT / VIDEO)
    parser.add_argument("--start-s", type=float, required=True, help="first source second")
    parser.add_argument("--duration-s", type=float, required=True)
    parser.add_argument("--stride", type=int, default=1, help="analyse every Nth source frame")
    parser.add_argument("--id", required=True, help="analysis id")
    parser.add_argument("--out", type=Path, default=ROOT / "data/analyses")
    parser.add_argument("--detector", help="overrides data/cv/detector.json")
    parser.add_argument("--detector-arg", action="append", default=[], help="name=value")
    parser.add_argument(
        "--source-offset-s", type=float, help="where a clip's second 0 sits in the camera 2 source"
    )
    parser.add_argument("--uncalibrated", action="store_true")
    parser.add_argument("--held-out-gate", type=Path, help="committed gate file: spends its read")
    parser.add_argument("--read-log", type=Path, default=ROOT / A.READ_LOG)
    parser.add_argument("--progress-every", type=int, default=100, help="frames between lines")
    args = parser.parse_args(argv)

    settings = settings_from(args)
    plan = P.plan_device(settings, args.stride)
    offset = args.source_offset_s
    if offset is None:
        offset = camera2_offset_s(args.video)
    site = load_site(ROOT) if offset is not None and not args.uncalibrated else None
    detector = P.build_detector(
        settings.detector, settings.args, args.video, source_offset_s=offset
    )
    params = settings.tracker_params() if settings.tracker else TrackerParams()
    grant = None
    if args.held_out_gate:
        if (args.out / args.id).exists():
            raise SystemExit(f"{args.out / args.id} already exists; a gate's read was not spent")
        try:
            grant = A.grant_held_out_pixels(ROOT, args.held_out_gate, args.read_log, args.id)
        except A.GateRefused as refused:
            print(f"REFUSED: {refused}", file=sys.stderr)
            return 2
    last = [0]

    def progress(update: P.Progress) -> None:
        frame = update.frame or 0
        if update.stage != "detect" or frame - last[0] >= args.progress_every:
            last[0] = frame
            print(
                f"{update.stage}: {update.frame}/{update.frames_total} {update.fps} fps, "
                f"{update.tracks} tracks, {update.elapsed_s} s: {update.message}",
                flush=True,
            )

    try:
        record = P.run_analysis(
            args.video,
            args.start_s,
            args.duration_s,
            plan.frame_stride,
            detector,
            site,
            args.out,
            args.id,
            progress,
            mode="precomputed",
            tracker_params=params,
            held_out_grant=grant,
            source_offset_s=offset,
            notes=plan.notes,
            device=plan.device,
        )
    except HeldOutError as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return 2
    for metric in record.metrics:
        print(f"{metric.metric_id}: {metric.value} {metric.unit} [{metric.status}]")
    print(args.out / record.analysis_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
