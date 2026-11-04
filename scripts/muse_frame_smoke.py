"""One Muse Spark request on one frame of the demo's SAM review overlay, in a strict schema.

    uv run --frozen python scripts/muse_frame_smoke.py --spend-limit USD [--frame-index N]
    uv run --frozen python scripts/muse_frame_smoke.py --replay [--frame-index N]

The overlay is the one the demo manifest pins (`sam_overlay`, checked against its SHA-256). One
frame (the middle one unless --frame-index) is cut out as JPEG with FFmpeg and sent with the
instructions and schema of cairoflow_reasoning.frame_prompt, through the cache-first adapter
(`MuseClient.fetch_body`): a cached reply is never paid for twice; otherwise ONE request, no
retries, refused if its upper-bound cost does not fit --spend-limit (this process only).

Outcome, always a new record, experiments/records/muse-frame-annotation-*.json:
- schema-valid reply: status "ok" and the annotation, model-generated text that no figure,
  metric or label uses; the frame and its CC BY-SA 4.0 NOTICE go to the folder of the same name;
- unusable reply (HTTP error, incomplete, refused, empty, off-schema): status "failed" and why;
  the raw reply stays under cache/muse/failed/; exit 1.
A request refused before sending (no key, over the limit, a cache miss in replay) writes nothing.
The API key is never printed or written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from cairoflow_contracts.config import Settings, load_settings
from cairoflow_contracts.evidence import Provenance
from cairoflow_contracts.meta_depth import AnnotatedFrame, FrameAnnotationRecord
from cairoflow_reasoning.frame_prompt import (
    FRAME_PROMPT_VERSION,
    build_frame_request_body,
    parse_frame_reply,
    validate_frame_reply,
)
from cairoflow_reasoning.muse_adapter import (
    MuseCacheMiss,
    MuseClient,
    MuseRequestError,
    MuseRequestRecord,
    MuseResponseError,
    MuseSpendRefused,
    flat_usage,
    request_key,
)

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "data/demo/ngsim-int2-no-footage.json"
LICENCE = "data/licences/ngsim-lankershim-videos.json"
LABEL = (
    "model-generated annotation (Muse Spark) of one SAM review-overlay frame; not ground truth, "
    "not a measurement; no figure, metric or label uses it"
)
EFFORT, MAX_OUTPUT_TOKENS = "low", 6000


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
         "stream=nb_read_frames,r_frame_rate,width,height", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    return json.loads(out.stdout)["streams"][0]


def extract_frame(video: Path, index: int, out: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{index})",
         "-frames:v", "1", "-q:v", "3", str(out)],
        check=True, capture_output=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip


def notice(licence: dict, source: str, index: int, image_sha256: str) -> str:
    return (
        "# One frame of the SAM 3.1 review overlay on NGSIM camera 2 (CC BY-SA 4.0)\n\n"
        f"`frame.jpg` (SHA-256 `{image_sha256}`) is frame {index} of `{source}`, itself an "
        "adaptation of the NGSIM Lankershim camera 2 raw video (see that folder's NOTICE.md).\n\n"
        f"**Source and attribution.** {licence['attribution']}\n\n"
        f"**Licence.** {licence['licence_name']}, {licence['licence_url']}. Shared under the same "
        "licence.\n\n"
        "**Changes.** One frame cut out and re-encoded as JPEG by scripts/muse_frame_smoke.py. "
        "The boxes and captions on it are SAM 3.1 output drawn by CairoFlow, not ground truth.\n"
    )


def failed_record(cache_dir: Path, key: str) -> MuseRequestRecord | None:
    """The adapter's audit copy of an unusable reply, if it wrote one."""
    found = sorted((cache_dir / "failed").glob(f"{key}_*.json"))
    return MuseRequestRecord.model_validate_json(found[-1].read_text("utf-8")) if found else None


def shown(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path)


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
    repo: Path = REPO,
    manifest: Path = MANIFEST,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--spend-limit", type=float, help="USD cap for this process")
    mode.add_argument("--replay", action="store_true", help="cache only; no connection")
    parser.add_argument("--frame-index", type=int, default=None, help="default: the middle")
    args = parser.parse_args(argv)

    pin = json.loads(manifest.read_text("utf-8"))["files"]["sam_overlay"]
    overlay = repo / pin["path"]
    if sha256_bytes(overlay.read_bytes()) != pin["sha256"]:
        print(f"{pin['path']} does not match the demo manifest pin", file=sys.stderr)
        return 2
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    records = repo / "experiments/records"
    out_json = records / f"muse-frame-annotation-{now:%Y%m%dT%H%M%SZ}.json"
    out_dir = out_json.with_suffix("")
    if out_json.exists() or out_dir.exists():
        print(f"{shown(out_json, repo)} exists; records are never overwritten", file=sys.stderr)
        return 2

    stream = probe(overlay)
    frames = int(stream["nb_read_frames"])
    num, den = (int(v) for v in stream["r_frame_rate"].split("/"))
    index = frames // 2 if args.frame_index is None else args.frame_index
    if not 0 <= index < frames:
        print(f"frame {index} is outside the overlay's {frames} frames", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as work:
        jpeg_path = Path(work) / "frame.jpg"
        extract_frame(overlay, index, jpeg_path)
        jpeg = jpeg_path.read_bytes()
    image_sha256 = sha256_bytes(jpeg)

    settings = settings or load_settings(repo / ".env")
    cache_dir = repo / "cache/muse"
    body = build_frame_request_body(
        jpeg, settings.muse_model, effort=EFFORT, max_output_tokens=MAX_OUTPUT_TOKENS
    )
    key = request_key(body)
    client = MuseClient(settings, cache_dir, "replay" if args.replay else "live",
                        spend_limit_usd=args.spend_limit or 0.0, transport=transport,
                        prompt_version=FRAME_PROMPT_VERSION)  # fmt: skip
    label = f"frame:{image_sha256[:16]}"
    failure: str | None = None
    reply: MuseRequestRecord | None = None
    from_cache = False
    try:
        reply, from_cache = client.fetch_body(body, label, validate_frame_reply)
    except (MuseSpendRefused, MuseCacheMiss) as error:
        print(f"not sent: {error}", file=sys.stderr)
        return 1
    except (MuseRequestError, MuseResponseError) as error:
        failure = str(error)
        reply = failed_record(cache_dir, key)

    out_dir.mkdir(parents=True)
    (out_dir / "frame.jpg").write_bytes(jpeg)
    licence = json.loads((repo / LICENCE).read_text("utf-8"))
    with (out_dir / "NOTICE.md").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(notice(licence, pin["path"], index, image_sha256))
    response = json.loads(reply.response_text) if reply and failure is None else {}
    found = sorted((cache_dir / "failed").glob(f"{key}_*.json"))
    cache_path = client.cache_path(key) if failure is None else (found[-1] if found else None)
    sent_at = reply.sent_at_utc if reply else None
    record = FrameAnnotationRecord(
        label=LABEL,
        status="ok" if failure is None else "failed",
        failure=failure,
        model=settings.muse_model,
        prompt_version=FRAME_PROMPT_VERSION,
        endpoint=settings.meta_base_url.rstrip("/") + "/responses",
        request_key=key,
        request_id=reply.request_id if reply else None,
        response_id=response.get("id"),
        sent_at_utc=sent_at,
        elapsed_s=reply.elapsed_s if reply else None,
        recorded_cost_usd=reply.estimated_cost_usd if reply else 0.0,
        usage=flat_usage(response.get("usage") or {}) if response else {},
        cache_path=shown(cache_path, repo) if cache_path else None,
        frame=AnnotatedFrame(
            source_path=pin["path"],
            source_sha256=pin["sha256"],
            frame_index=index,
            time_s=round(index * den / num, 3),
            image_path=shown(out_dir / "frame.jpg", repo),
            image_sha256=image_sha256,
            width_px=int(stream["width"]),
            height_px=int(stream["height"]),
        ),
        annotation=parse_frame_reply(response) if failure is None else None,
        provenance=Provenance(
            kind="model_generated",
            produced_by=settings.muse_model,
            produced_at_utc=sent_at or now,
            input_sha256=(image_sha256, key),
            parameters={
                "prompt_version": FRAME_PROMPT_VERSION,
                "effort": EFFORT,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "store": False,
            },  # fmt: skip
            cached=from_cache,
        ),
    )
    with out_json.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(record.model_dump_json(indent=2) + "\n")

    print(f"model: {record.model}; endpoint: {record.endpoint}; from cache: {from_cache}")
    print(f"frame {index} of {pin['path']} ({record.frame.width_px}x{record.frame.height_px})")
    print(f"status: {record.status}" + (f" ({failure})" if failure else ""))
    if record.annotation is not None:
        print(f"objects: {len(record.annotation.objects)}, overlay issues: "
              f"{len(record.annotation.overlay_issues)}, image quality "
              f"{record.annotation.image_quality}")  # fmt: skip
    print(f"recorded cost: ${record.recorded_cost_usd:.4f} (published prices x reported usage)")
    print(f"record: {shown(out_json, repo)}")
    return 0 if failure is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
