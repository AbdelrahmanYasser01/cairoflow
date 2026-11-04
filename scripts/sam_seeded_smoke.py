"""One small box-prompted SAM request: does seeding work, including objects that start late?

Seeds come from the reference analysis (published NGSIM positions projected into the image), so
this tests the API path, not any detector. Costs $0.01 (the 50-frame billing minimum). Refuses to
send past the project cap in scripts/meta_spend.py. --replay opens no connection.

    uv run --frozen python scripts/sam_seeded_smoke.py --send
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meta_spend  # noqa: E402

from cairoflow_contracts.config import load_settings  # noqa: E402
from cairoflow_contracts.evidence import ClipLicense, SourceClip, TrimSpec  # noqa: E402
from cairoflow_perception.analysis_store import (  # noqa: E402
    PX_SCALE,
    decode_trajectories,
    load_tracks,
)
from cairoflow_perception.ingest import ingest_clip  # noqa: E402
from cairoflow_perception.sam_adapter import (  # noqa: E402
    SamAdapterError,
    SamClient,
    video_cost_usd,
)
from cairoflow_perception.sam_seeded import Seed, clamp_box, segment_seeded  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "data/raw/ngsim-lankershim/camera2/lankershim-camera2-0830am-0845am.avi"
LICENCE = REPO / "data/licences/ngsim-lankershim-videos.json"
REFERENCE = REPO / "data/sample/analyses/ref-ngsim-int2-960-1020"
CLIP_DIR = REPO / "data/processed/clips/ngsim-lankershim-cam2-0830-t970-d005"
START_S, DURATION_S = 970.0, 5.0
REFERENCE_FIRST_S = 960.0
WIDTH, HEIGHT, VALID_ROWS = 640, 480, 410


def clip_for_smoke() -> SourceClip:
    if not (CLIP_DIR / "source_clip.json").is_file():
        licence = ClipLicense.model_validate_json(LICENCE.read_text("utf-8"))
        return ingest_clip(
            SOURCE, licence, CLIP_DIR, trim=TrimSpec(start_s=START_S, duration_s=DURATION_S)
        )
    return SourceClip.model_validate_json((CLIP_DIR / "source_clip.json").read_text("utf-8"))


def reference_boxes() -> dict[int, dict[int, tuple[float, float, float, float]]]:
    """Reference boxes by clip frame then track id, inside the measured part of the image."""
    index = load_tracks(REFERENCE)
    _, c = decode_trajectories((REFERENCE / "trajectories.bin").read_bytes())
    offset = round((START_S - REFERENCE_FIRST_S) * 10)
    frames = round(DURATION_S * 10)
    boxes: dict[int, dict[int, tuple[float, float, float, float]]] = defaultdict(dict)
    for track in index.tracks:
        for i in range(track.n):
            frame = track.start_frame + i - offset
            if not 0 <= frame < frames:
                continue
            row = track.first_row + i
            x, y = c.x[row] * PX_SCALE, c.y[row] * PX_SCALE
            w, h = c.w[row] * PX_SCALE, c.h[row] * PX_SCALE
            if 0 < x < WIDTH and y + h / 2 < VALID_ROWS:
                boxes[frame][track.track_id] = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
    return boxes


def choose_seeds(boxes: dict) -> list[Seed]:
    """Up to 6 vehicles on frame 0 and up to 4 that first appear later: the case SAM missed."""
    first = sorted(boxes[0])[:6]
    late: dict[int, int] = {}
    for frame in sorted(boxes):
        for track_id in boxes[frame]:
            if frame > 5 and track_id not in boxes[0] and track_id not in late:
                late[track_id] = frame
    chosen = [(t, 0) for t in first] + list(late.items())[:4]
    seeds = []
    for object_id, (track_id, frame) in enumerate(chosen, start=1):
        box = clamp_box(boxes[frame][track_id], WIDTH, HEIGHT)
        if box:
            seeds.append(Seed(object_id, frame, box))
    return seeds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--send", action="store_true", help="one paid request, about $0.01")
    mode.add_argument("--replay", action="store_true", help="cache only; no connection")
    args = parser.parse_args(argv)

    clip = clip_for_smoke()
    clip_path = CLIP_DIR / clip.output_file
    seeds = choose_seeds(reference_boxes())
    cost = video_cost_usd(clip.output_stream.frame_count)
    print(f"{len(seeds)} seeds, {clip.output_stream.frame_count} frames, estimate ${cost:.4f}")
    if args.send and cost > meta_spend.remaining_usd():
        print("refused: over the project cap", file=sys.stderr)
        return 2

    settings = load_settings(REPO / ".env")
    client = SamClient(
        settings,
        REPO / "cache/sam",
        "live" if args.send else "replay",
        spend_limit_usd=cost if args.send else 0.0,
    )
    try:
        record, parsed, cached = segment_seeded(client, clip, clip_path, seeds)
    except SamAdapterError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    if not cached:
        meta_spend.record("sam-seeded-smoke", client.spent_usd, record.request_key, "box prompts")

    by_object: dict[str, list[int]] = defaultdict(list)
    for observation in parsed.observations:
        by_object[observation.object_id].append(observation.frame.frame_index)
    summary = {
        "script": "scripts/sam_seeded_smoke.py",
        "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "label": "model_generated; seeds are projected reference positions, not detections",
        "request_key": record.request_key,
        "from_cache": cached,
        "http_status": record.http_status,
        "frames_billed": record.frames_billed,
        "outcome": parsed.outcome,
        "observations": len(parsed.observations),
        "parser_diagnostics": list(parsed.parser_diagnostics),
        "dropped": len(parsed.dropped),
        "seeds": [
            {"object_id": s.object_id, "frame": s.frame, "box_xyxy_px": list(s.box_xyxy_px)}
            for s in seeds
        ],
        "per_object": {
            key: {"first": min(v), "last": max(v), "n": len(v)} for key, v in by_object.items()
        },
        "mean_confidence": float(
            np.mean([o.confidence for o in parsed.observations if o.confidence is not None] or [0])
        ),
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = REPO / f"experiments/records/sam-seeded-smoke-{stamp}.json"
    out.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "seeds"}, indent=1))
    print(f"spent ${meta_spend.spent_usd():.4f}, remaining ${meta_spend.remaining_usd():.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
