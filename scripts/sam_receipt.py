"""One SAM 3.1 call on an ingested clip, and its receipt: model, endpoint, frames, cost.

    uv run --frozen python scripts/sam_receipt.py CLIP_DIR --spend-limit USD [--prompt car]
    uv run --frozen python scripts/sam_receipt.py CLIP_DIR --replay [--prompt car]

CLIP_DIR is an excerpt ingested by scripts/ingest_clip.py. The call goes through the cache-first
adapter (`cairoflow_perception.sam_adapter.SamClient`): a cached stream is returned and never paid
for twice; otherwise ONE request is sent, with no retries, and only if its estimated cost fits
--spend-limit (this process only; nothing is persisted). --replay opens no connection.

The adapter stores the stream under cache/sam/<request_key>.json (failed attempts under
cache/sam/failed/). The receipt goes to a NEW experiments/records/sam-receipt-<stamp>.json, which
is never overwritten. What SAM returns is model output, not ground truth. Costs are the published
price applied to the request ($0.20 per 1,000 frames, at least 50 frames billed), not an invoice.
The API key is never printed or written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from cairoflow_contracts.config import Settings, load_settings
from cairoflow_contracts.evidence import SourceClip
from cairoflow_perception.sam_adapter import SamAdapterError, SamClient, SamRequestError
from cairoflow_perception.sam_parse import parse_sam_stream

REPO = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO / "cache/sam"
RECORDS_DIR = REPO / "experiments/records"
LABEL = (
    "model output (SAM 3.1) on one excerpt: a receipt of one request; not ground truth, not "
    "tracked, not used by the world model (ADR-018, ADR-019)"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def shown(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def endpoint_of(settings: Settings) -> str:
    """The URL SamClient posts to (sam_adapter.SamClient._send)."""
    return settings.meta_base_url.rstrip("/") + "/responses"


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
    cache_dir: Path = CACHE_DIR,
    records_dir: Path = RECORDS_DIR,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clip_dir", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--spend-limit", type=float, help="USD cap for this process")
    mode.add_argument("--replay", action="store_true", help="cache only; no connection")
    parser.add_argument("--prompt", default="car", help="one noun phrase")
    args = parser.parse_args(argv)

    clip_dir = args.clip_dir.resolve()
    clip = SourceClip.model_validate_json((clip_dir / "source_clip.json").read_text("utf-8"))
    clip_path = clip_dir / clip.output_file
    if sha256_file(clip_path) != clip.output_sha256:
        print(f"{clip_path} does not match its manifest", file=sys.stderr)
        return 2
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    out = records_dir / f"sam-receipt-{now:%Y%m%dT%H%M%SZ}.json"
    if out.exists():  # checked before anything is sent
        print(f"{shown(out)} exists; records are never overwritten", file=sys.stderr)
        return 2

    settings = settings or load_settings(REPO / ".env")
    sam = SamClient(settings, cache_dir, "replay" if args.replay else "live",
                    spend_limit_usd=args.spend_limit or 0.0, transport=transport)  # fmt: skip
    try:
        record, from_cache = sam.segment_video(clip, clip_path, args.prompt)
    except SamRequestError as error:
        kept = shown(cache_dir / "failed")
        print(f"{error} (error class {error.error_class}, request id {error.request_id}); "
              f"the attempt is kept under {kept}", file=sys.stderr)  # fmt: skip
        return 1
    except SamAdapterError as error:
        print(f"not sent: {error}", file=sys.stderr)
        return 1
    parsed = parse_sam_stream(record.lines, clip, prompt=record.prompt, model=record.model,
                              request_key=record.request_key, sent_at_utc=record.sent_at_utc,
                              cached=from_cache)  # fmt: skip
    stream = sam.cache_path(record.request_key)
    start = clip.trim.start_s
    receipt = {
        "script": "scripts/sam_receipt.py",
        "created_at_utc": now.isoformat(),
        "label": LABEL,
        "model": record.model,
        "endpoint": endpoint_of(settings),
        "prompt": record.prompt,
        "request_key": record.request_key,
        "request_id": record.request_id,
        "sent_at_utc": record.sent_at_utc.isoformat(),
        "from_cache": from_cache,
        "http_status": record.http_status,
        "elapsed_s": record.elapsed_s,
        "clip": {
            "clip_id": clip.clip_id,
            "output_sha256": clip.output_sha256,
            "frames": len(clip.frames),
            "fps": clip.output_stream.fps,
            "duration_s": clip.output_stream.duration_s,
            "source_window_s": [start, start + clip.output_stream.duration_s],
        },
        "frames_billed": record.frames_billed,
        "outcome": parsed.outcome,
        "observations": len(parsed.observations),
        "frames_with_an_observation": len({o.frame.frame_index for o in parsed.observations}),
        "distinct_sam_object_ids": len({o.object_id for o in parsed.observations}),
        "parser_diagnostics": list(parsed.parser_diagnostics),
        "dropped": len(parsed.dropped),
        "recorded_cost_usd": record.estimated_cost_usd,
        "spent_this_run_usd": round(sam.spent_usd, 6),
        "cost_basis": "published price, $0.20 per 1,000 frames, at least 50 frames billed "
        "(https://dev.meta.ai/docs/pricing-rate-limits, accessed 2026-10-27); not an invoice",
        "stream": {
            "path": shown(stream),
            "sha256": sha256_file(stream),
            "lines": len(record.lines),
        },  # fmt: skip
    }
    records_dir.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(receipt, indent=2) + "\n")

    fps, duration = clip.output_stream.fps, clip.output_stream.duration_s
    print(f"model: {receipt['model']}")
    print(f"endpoint: {receipt['endpoint']}")
    print(f"prompt: {record.prompt!r}; from cache: {from_cache}; request id: {record.request_id}")
    print(f"frames: {len(clip.frames)} in the clip ({fps:g} fps, {duration:g} s), "
          f"{record.frames_billed} billed by the API")  # fmt: skip
    print(f"observations: {receipt['observations']} in {receipt['frames_with_an_observation']} "
          f"frames, {receipt['distinct_sam_object_ids']} SAM object ids, outcome "
          f"{parsed.outcome}, {len(parsed.parser_diagnostics)} parser diagnostics")  # fmt: skip
    print(f"recorded cost: ${record.estimated_cost_usd:.4f} (published price, not an invoice); "
          f"spent by this run: ${sam.spent_usd:.4f}")  # fmt: skip
    print(f"stream: {receipt['stream']['path']} (sha256 {receipt['stream']['sha256'][:16]}...)")
    print(f"record: {shown(out)}")
    return 0 if parsed.outcome == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
