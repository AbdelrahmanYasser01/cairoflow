"""Render an analysis onto its source video as a browser-playable H.264 MP4.

    .venv/bin/python scripts/render_annotated_video.py --analysis data/analyses/<id>

Writes <analysis>/annotated.mp4 (yuv420p, +faststart): boxes, track ids, short trails, class
colours, a caption saying this is CairoFlow CV output and not ground truth, and the source's
attribution. *.mp4 is Git-ignored and the record lists the file as not bundled, so it never enters
the repository. --video defaults to camera 2. A held-out analysis renders only after its gate has
scored it, by --read-log.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("perception", "contracts"):
    path = str(ROOT / "packages" / package)
    if path not in sys.path:
        sys.path.insert(0, path)

from cairoflow_perception.local.render import render_annotated_video  # noqa: E402
from cairoflow_perception.local.scoring import VIDEO, HeldOutError  # noqa: E402

READ_LOG = ROOT / "data/gates/held_out_reads.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--video", type=Path, default=ROOT / VIDEO)
    parser.add_argument("--trail-frames", type=int, default=12)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--read-log", type=Path, default=READ_LOG)
    args = parser.parse_args(argv)
    try:
        result = render_annotated_video(
            args.analysis,
            args.video,
            trail_frames=args.trail_frames,
            overwrite=args.overwrite,
            read_log=args.read_log,
        )
    except HeldOutError as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return 2
    print(f"{result.path}: {result.frames} frames, {result.boxes} boxes, {result.encoder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
