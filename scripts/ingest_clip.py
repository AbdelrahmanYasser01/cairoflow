"""Ingest one excerpt of a licensed video with `ingest_clip` (local only, no network).

    uv run --frozen python scripts/ingest_clip.py SOURCE --licence LICENCE.json --out OUT_DIR \
        --start SECONDS [--duration SECONDS] [--fps 10]

Writes OUT_DIR/clip.mp4 and OUT_DIR/source_clip.json (bit-exact H.264, SHA-256, every frame's
timestamp, the exact FFmpeg command). Refuses an unusable licence, a window outside the source
and an existing output. Makes no network request: uploading a clip is a separate, approved step.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cairoflow_contracts.evidence import ClipLicense, TrimSpec
from cairoflow_perception.ingest import MANIFEST_FILE, ingest_clip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--licence", type=Path, required=True, help="ClipLicense JSON")
    parser.add_argument("--out", type=Path, required=True, help="new directory for the clip")
    parser.add_argument("--start", type=float, required=True, help="source seconds")
    parser.add_argument("--duration", type=float, default=None, help="seconds; default: to end")
    parser.add_argument("--fps", type=int, default=10)
    args = parser.parse_args(argv)

    licence = ClipLicense.model_validate_json(args.licence.read_text(encoding="utf-8"))
    clip = ingest_clip(
        args.source,
        licence,
        args.out,
        trim=TrimSpec(start_s=args.start, duration_s=args.duration),
        target_fps=args.fps,
    )
    stream = clip.output_stream
    print(
        f"clip {clip.clip_id}: {stream.frame_count} frames, {stream.duration_s:g} s, "
        f"{stream.width_px}x{stream.height_px} at {stream.fps:g} fps; "
        f"sha256 {clip.output_sha256}; manifest {args.out / MANIFEST_FILE}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
