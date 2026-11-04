"""Run SAM 3.1 on a 10 s NGSIM camera 2 excerpt and render the review overlay (perception only).

    uv run --frozen python scripts/run_sam_cam2_excerpt.py CLIP_DIR --spend-limit USD [--record]
    uv run --frozen python scripts/run_sam_cam2_excerpt.py CLIP_DIR --replay [--record]

CLIP_DIR is an ingested excerpt (`scripts/ingest_clip.py`) of NGSIM camera 2 (CC BY-SA 4.0,
DATA_REGISTER.md §§10-11). Prompts: `car`, then `bus`. What comes out is SAM 3.1 output turned
into tracks by our procedure: not ground truth, and **not used by the world model**. The camera 2
calibration was not accepted (ADR-018), so the intersection 2 world model is built from the
published trajectories (ADR-019). This shows what SAM sees on the real footage, nothing more.

Live mode is cache-first (a cached stream is never paid for twice) and sends at most one request
per prompt, each refused if it would take the estimated spend past --spend-limit
(`cairoflow_perception.sam_adapter.SamClient`; no retries; the API key is never printed or
written). --replay uses the cache only and opens no connection.

Output: experiments/runs/ngsim-cam2-sam-<stamp>/ (Git-ignored, never overwritten) with
observations.json, tracks.json, overlay.mp4, overlay.json and summary.json. With --record, the
summary also goes to experiments/records/ngsim-cam2-sam-<stamp>.json, and the overlay, its
manifest and a NOTICE (CC BY-SA 4.0: attribution and changes) to
experiments/records/ngsim-cam2-sam-<stamp>/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from cairoflow_contracts.config import load_settings
from cairoflow_contracts.evidence import Observation, SourceClip
from cairoflow_perception.overlay import render_overlay
from cairoflow_perception.sam_adapter import SamAdapterError, SamClient
from cairoflow_perception.sam_parse import parse_sam_stream
from cairoflow_perception.tracking import build_tracks

REPO = Path(__file__).resolve().parents[1]
PROMPTS = ("car", "bus")
CACHE_DIR = REPO / "cache/sam"
SOURCE_AT_0830_S = 119.7  # source video second of 08:30:00.0 (DATA_REGISTER.md §15)
LICENCE = REPO / "data/licences/ngsim-lankershim-videos.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def notice(clip: SourceClip, licence: dict, overlay_sha256: str) -> str:
    start = clip.trim.start_s
    return (
        "# SAM 3.1 review overlay on NGSIM camera 2 (CC BY-SA 4.0)\n\n"
        f"`overlay.mp4` (SHA-256 `{overlay_sha256}`) is an adaptation of the NGSIM Lankershim "
        f"camera 2 raw video, 08:30-08:45, source seconds {start:g}-"
        f"{start + clip.output_stream.duration_s:g}.\n\n"
        f"**Source and attribution.** {licence['attribution']}\n\n"
        f"**Licence.** {licence['licence_name']}, {licence['licence_url']}. This overlay is "
        "shared under the same licence, as the project owner decided on 2026-10-28 (D3).\n\n"
        "**Changes.** Trimmed to 10 s, re-encoded to H.264 at 10 fps (scripts/ingest_clip.py), "
        "then drawn over: boxes, track ids, trails and captions from SAM 3.1 output turned "
        "into tracks by CairoFlow's procedure (scripts/run_sam_cam2_excerpt.py). The drawing is "
        "model output, not ground truth, and it is not used by the intersection 2 world model "
        "(ADR-018, ADR-019).\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clip_dir", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--spend-limit", type=float, help="live mode: total USD cap for this run")
    mode.add_argument("--replay", action="store_true", help="cache only; no connection")
    parser.add_argument("--record", action="store_true", help="write the committed record")
    parser.add_argument(
        "--prompts", default=",".join(PROMPTS), help="comma-separated, sent in order"
    )
    args = parser.parse_args(argv)
    clip_dir = args.clip_dir.resolve()
    clip = SourceClip.model_validate_json((clip_dir / "source_clip.json").read_text("utf-8"))
    clip_path = clip_dir / clip.output_file
    if sha256_file(clip_path) != clip.output_sha256:
        print(f"{clip_path} does not match its manifest", file=sys.stderr)
        return 2
    sam = SamClient(load_settings(), CACHE_DIR, "replay" if args.replay else "live",
                    spend_limit_usd=args.spend_limit or 0.0)  # fmt: skip

    now = datetime.now(UTC).replace(microsecond=0)
    out = REPO / "experiments/runs" / f"ngsim-cam2-sam-{now:%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True)
    requests, observations = [], []
    for prompt in args.prompts.split(","):
        try:
            record, cached = sam.segment_video(clip, clip_path, prompt)
        except SamAdapterError as error:
            print(f"{prompt!r}: {error}", file=sys.stderr)
            return 1
        parsed = parse_sam_stream(record.lines, clip, prompt=record.prompt, model=record.model,
                                  request_key=record.request_key, sent_at_utc=record.sent_at_utc,
                                  cached=cached)  # fmt: skip
        frames = Counter(o.frame.frame_index for o in parsed.observations)
        requests.append({
            "prompt": prompt, "model": record.model, "request_key": record.request_key,
            "request_id": record.request_id, "sent_at_utc": record.sent_at_utc.isoformat(),
            "from_cache": cached, "http_status": record.http_status,
            "elapsed_s": record.elapsed_s, "frames_billed": record.frames_billed,
            "estimated_cost_usd": record.estimated_cost_usd, "outcome": parsed.outcome,
            "observations": len(parsed.observations),
            "frames_with_an_observation": len(frames),
            "distinct_sam_object_ids": len({o.object_id for o in parsed.observations}),
            "parser_diagnostics": list(parsed.parser_diagnostics),
            "dropped": len(parsed.dropped),
        })  # fmt: skip
        if parsed.outcome != "completed":
            print(f"{prompt!r}: SAM output is {parsed.outcome}", file=sys.stderr)
            return 1
        observations += parsed.observations
    write_new(out / "observations.json", TypeAdapter(tuple[Observation, ...]).dump_json(
        tuple(observations), indent=2).decode() + "\n")  # fmt: skip
    tracking = build_tracks(observations, now=now)
    write_new(out / "tracks.json", tracking.model_dump_json(indent=2) + "\n")
    overlay = render_overlay(clip, clip_path, tracking.tracks, out / "overlay.mp4", now=now)
    per_frame = Counter(p.frame.frame_index for t in tracking.tracks for p in t.points)
    counts = [per_frame.get(i, 0) for i in range(len(clip.frames))]
    start, length = clip.trim.start_s, clip.output_stream.duration_s
    summary = {
        "script": "scripts/run_sam_cam2_excerpt.py",
        "created_at_utc": now.isoformat(),
        "run_dir": str(out.relative_to(REPO)),
        "label": "model output (SAM 3.1), tracked by our procedure; not ground truth; "
                 "not used by the world model (ADR-018, ADR-019)",
        "clip": {
            "clip_id": clip.clip_id, "output_sha256": clip.output_sha256,
            "frames": len(clip.frames), "fps": clip.output_stream.fps,
            "source_window_s": [start, start + length],
            "trajectory_clip_window_s": [round(start - SOURCE_AT_0830_S, 1),
                                         round(start - SOURCE_AT_0830_S + length, 1)],
            "time_sync": "source second 119.7 = 08:30:00.0 (DATA_REGISTER.md §15)",
        },
        "requests": requests,
        "spent_usd_estimated": round(sam.spent_usd, 6),
        "tracking": {
            "policy": tracking.policy.model_dump(),
            "tracks": len(tracking.tracks),
            "by_class": dict(Counter(t.class_label for t in tracking.tracks)),
            "duplicates_removed": len(tracking.duplicates),
            "gaps_bridged": len(tracking.links),
            "short_tracks_removed": len(tracking.short_tracks),
            "tracked_objects_per_frame": {"min": min(counts), "median": statistics.median(counts),
                                          "max": max(counts)},
        },
        "overlay": overlay.model_dump(mode="json"),
        "not_compared": "no comparison with the NGSIM trajectories: the camera's footprint on the "
                        "ground would need the calibration that was not accepted (ADR-018)",
    }  # fmt: skip
    text = json.dumps(summary, indent=2) + "\n"
    write_new(out / "summary.json", text)
    if args.record:
        records = REPO / "experiments/records"
        write_new(records / f"{out.name}.json", text)
        evidence = records / out.name
        evidence.mkdir()
        for name in ("overlay.mp4", "overlay.json"):
            shutil.copyfile(out / name, evidence / name)
        licence = json.loads(LICENCE.read_text(encoding="utf-8"))
        write_new(evidence / "NOTICE.md", notice(clip, licence, sha256_file(out / "overlay.mp4")))
    print(f"wrote {out.relative_to(REPO)}: {len(tracking.tracks)} tracks, "
          f"spent about ${sam.spent_usd:.4f}")  # fmt: skip
    for r in requests:
        print(f"  {r['prompt']}: {r['outcome']}, {r['observations']} observations in "
              f"{r['frames_with_an_observation']} frames, {len(r['parser_diagnostics'])} "
              f"diagnostics, from cache {r['from_cache']}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
