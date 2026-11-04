"""One-shot, spend-guarded smoke tests of the Meta Model API: SAM 3.1 and Muse Spark.

Run from the repository root:  uv run python scripts/api_smoke_test.py [--case NAME ...] [--send]

Cases (each independently selectable with --case; the default is the original pair):
  muse-text        one-word Muse Spark reply                      (sent once on 2026-10-27)
  sam-image        one tiny synthetic image through SAM 3.1       (sent once on 2026-10-27)
  sam-video        a 60-frame synthetic clip through SAM 3.1      (sent once on 2026-10-27)
  muse-structured  Muse Spark JSON-schema output via text.format  (sent once on 2026-10-27)
  muse-store-false the muse-text request with `store: false`; passes only if the response
                   reports store false (worst case under $0.001)

Without --send this is a dry run: it prints what would be sent and the worst-case cost, and
sends nothing. With --send it makes exactly ONE request per selected case, with no retries,
and stops after the first failure. The API key is read through cairoflow_contracts.config (a
SecretStr), is used only in the Authorization header, and is scrubbed from everything printed
or cached. Raw responses are written to cache/smoke/ (git-ignored, ADR-009).

Request shapes come from the official docs, accessed 2026-10-27:
  https://dev.meta.ai/docs/sam/segmenting.md   (SAM 3.1, input_image / input_video + stream)
  https://dev.meta.ai/docs/reasoning           (Muse Spark, reasoning.effort, max_output_tokens)
  https://dev.meta.ai/docs/protocols/responses (text.format; its inner fields are not documented;
                                                muse-structured confirmed them on 2026-10-27)
Prices are from https://dev.meta.ai/docs/pricing-rate-limits, accessed 2026-10-27.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import cv2
import httpx
import numpy as np
from pydantic import BaseModel, ConfigDict, ValidationError

from cairoflow_contracts.config import Settings, load_settings

CACHE_DIR = Path("cache/smoke")
SAM_PROMPT = "red square"
MUSE_PROMPT = "Reply with the single word: ok"
MUSE_MAX_OUTPUT_TOKENS = 300  # a cap on reasoning plus visible tokens, not a spend target
MUSE_PROMPT_TOKEN_ALLOWANCE = 40  # generous upper bound for the prompt, for the worst case
MUSE_STORE_FALSE_MAX_OUTPUT_TOKENS = 150  # keeps the worst case under the approved $0.001

# Synthetic clip for sam-video: a red square moving right on white. No licensed media involved.
VIDEO_FRAMES = 60  # above the 50-frame billing minimum, so the bill equals the frame count
VIDEO_FPS = 10
VIDEO_SIZE_PX = 256
VIDEO_SQUARE_PX = 48
VIDEO_MIN_MEAN_IOU = 0.8  # acceptance threshold for the parsed boxes against the drawn square

MUSE_STRUCTURED_PROMPT = (
    "Synthetic test data, queue lengths in vehicles: north 2, south 3, east 11, west 4. "
    "Which approach has the longest queue? Answer with the required JSON object."
)
MUSE_STRUCTURED_MAX_OUTPUT_TOKENS = 1000
MUSE_STRUCTURED_PROMPT_TOKEN_ALLOWANCE = 200  # prompt plus schema, generous upper bound
MUSE_STRUCTURED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "longest_queue_approach": {"type": "string", "enum": ["north", "south", "east", "west"]},
        "confidence_label": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["longest_queue_approach", "confidence_label"],
    "additionalProperties": False,
}

# USD, official pricing page, accessed 2026-10-27.
SAM_IMAGE_USD = 2.50 / 1000
SAM_VIDEO_USD_PER_FRAME = 0.20 / 1000
SAM_VIDEO_MIN_BILLED_FRAMES = 50
MUSE_INPUT_USD_PER_TOKEN = 1.25 / 1_000_000
MUSE_OUTPUT_USD_PER_TOKEN = 4.25 / 1_000_000

SAFE_HEADER_NAMES = ("request-id", "x-request-id", "retry-after")
TERMINAL_EVENTS = {"response.completed", "response.incomplete", "response.failed", "error"}
STREAM_DEADLINE_S = 120.0


def make_scrubber(secret: str) -> Callable[[str], str]:
    def scrub(text: str) -> str:
        return text.replace(secret, "[REDACTED]") if secret else text

    return scrub


def make_test_png() -> bytes:
    """A 128x128 white image with one red square. Synthetic: no licensed media is involved."""
    image = np.full((128, 128, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (32, 32), (96, 96), (0, 0, 255), thickness=-1)  # BGR red
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("could not encode the test image")
    return encoded.tobytes()


def sam_body(settings: Settings, png: bytes) -> dict[str, Any]:
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {
        "model": settings.sam_model,
        "stream": True,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": SAM_PROMPT},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
    }


def muse_body(
    settings: Settings,
    *,
    store: bool | None = None,
    max_output_tokens: int = MUSE_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": settings.muse_model,
        "input": MUSE_PROMPT,
        "reasoning": {"effort": "minimal"},  # "none" returns HTTP 400 on Muse Spark
        "max_output_tokens": max_output_tokens,
    }
    if store is not None:
        body["store"] = store
    return body


def square_box(frame_index: int) -> tuple[int, int, int, int]:
    """Inclusive pixel box (x1, y1, x2, y2) of the drawn square in a given clip frame."""
    left = 16 + 2 * frame_index
    top = (VIDEO_SIZE_PX - VIDEO_SQUARE_PX) // 2
    return left, top, left + VIDEO_SQUARE_PX - 1, top + VIDEO_SQUARE_PX - 1


def make_test_mp4() -> bytes:
    """Encode the synthetic clip as H.264 MP4 with faststart, the format the SAM docs ask for."""
    frames = bytearray()
    for index in range(VIDEO_FRAMES):
        image = np.full((VIDEO_SIZE_PX, VIDEO_SIZE_PX, 3), 255, dtype=np.uint8)
        x1, y1, x2, y2 = square_box(index)
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 0, 255), thickness=-1)  # BGR red
        frames += image.tobytes()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "square.mp4"
        command = [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{VIDEO_SIZE_PX}x{VIDEO_SIZE_PX}", "-r", str(VIDEO_FPS), "-i", "pipe:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
        ]  # fmt: skip
        subprocess.run(command, input=bytes(frames), check=True, capture_output=True)
        return out.read_bytes()


def sam_video_body(settings: Settings, mp4: bytes) -> dict[str, Any]:
    data_url = "data:video/mp4;base64," + base64.b64encode(mp4).decode("ascii")
    return {
        "model": settings.sam_model,
        "stream": True,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": SAM_PROMPT},
                    {"type": "input_video", "video_url": data_url},
                ],
            }
        ],
        "metadata": {"include_confidence": "true"},  # metadata values are strings per the docs
    }


def muse_structured_body(settings: Settings) -> dict[str, Any]:
    return {
        "model": settings.muse_model,
        "input": MUSE_STRUCTURED_PROMPT,
        "reasoning": {"effort": "minimal"},
        "max_output_tokens": MUSE_STRUCTURED_MAX_OUTPUT_TOKENS,
        # Inner field names follow the Chat Completions json_schema object, which IS documented;
        # the Responses page only says "text.format". A 400 here is a finding, not a bug.
        "text": {
            "format": {
                "type": "json_schema",
                "name": "queue_check",
                "schema": MUSE_STRUCTURED_SCHEMA,
                "strict": True,
            }
        },
    }


class QueueCheck(BaseModel):
    """Local mirror of MUSE_STRUCTURED_SCHEMA; the reply is validated whatever the server did."""

    model_config = ConfigDict(extra="forbid")

    longest_queue_approach: Literal["north", "south", "east", "west"]
    confidence_label: Literal["low", "medium", "high"]


def validate_muse_structured(output_text: str) -> dict[str, Any]:
    try:
        parsed = QueueCheck.model_validate_json(output_text)
    except ValidationError as error:
        return {"schema_valid": False, "errors": error.error_count()}
    return {
        "schema_valid": True,
        "answer_matches_synthetic_data": parsed.longest_queue_approach == "east",
    }


def _iou_half_open(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    inter = width * height
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union


BoxObservation = tuple[int, str, float, float, float, float]  # frame, object id, half-open box


def score_video_boxes(boxes: list[BoxObservation], diagnostics: int = 0) -> dict[str, Any]:
    """Compare parsed boxes with the square that was drawn in each frame."""
    ious: list[float] = []
    object_ids: set[str] = set()
    frames: set[int] = set()
    for index, object_id, left, top, right, bottom in boxes:
        frames.add(index)
        object_ids.add(object_id)
        if 0 <= index < VIDEO_FRAMES:
            x1, y1, x2, y2 = square_box(index)
            expected = (x1, y1, x2 + 1, y2 + 1)  # the parser's boxes are half-open
            ious.append(_iou_half_open((left, top, right, bottom), expected))
    mean_iou = sum(ious) / len(ious) if ious else 0.0
    return {
        "diagnostics": diagnostics,
        "frames_with_box": len(frames),
        "frames_expected": VIDEO_FRAMES,
        "distinct_object_ids": len(object_ids),
        "mean_iou": round(mean_iou, 3),
        "passed": bool(ious) and mean_iou >= VIDEO_MIN_MEAN_IOU,
    }


def validate_sam_video(output_text: str) -> dict[str, Any]:
    """Parse with the official parser (never by hand), then score the boxes."""
    from meta_sam_parser import CompletedOutcome, SegmentationBoxRecord, video_segmentation_format

    parser = video_segmentation_format().create_parser()
    parser.push(output_text)
    result = parser.finish(CompletedOutcome()).result
    records = getattr(result, "records", ()) if result is not None else ()
    boxes = [
        (r.frame.frame_index, r.object_id, r.left, r.top, r.right, r.bottom)
        for r in records
        if isinstance(r, SegmentationBoxRecord) and r.frame is not None
    ]
    return score_video_boxes(boxes, len(getattr(result, "diagnostics", ()) or ()))


def body_sha256(body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def redact_media(value: Any) -> Any:
    """Replace inline data URLs with their size and hash so bodies can be printed and cached."""
    if isinstance(value, str) and value.startswith("data:"):
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        return f"<data URL, {len(value)} chars, sha256={digest}>"
    if isinstance(value, dict):
        return {key: redact_media(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_media(item) for item in value]
    return value


def safe_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() in SAFE_HEADER_NAMES or name.lower().startswith("x-ratelimit")
    }


def classify_status(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "billing"
    if status == 429:
        return "rate_limited"
    if status in (400, 422):
        return "bad_request"
    if status >= 500:
        return "server"
    return "ok" if status < 400 else "client"


def request_headers(settings: Settings, stream: bool) -> dict[str, str]:
    assert settings.model_api_key is not None  # checked in main()
    return {
        "Authorization": f"Bearer {settings.model_api_key.get_secret_value()}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
    }


def post_once(
    client: httpx.Client, url: str, headers: dict[str, str], body: dict[str, Any], stream: bool
) -> dict[str, Any]:
    """Send exactly one request. No retry, no redirect following."""
    started = time.monotonic()
    result: dict[str, Any] = {"lines": []}
    if stream:
        with client.stream("POST", url, headers=headers, json=body) as response:
            result["status"] = response.status_code
            result["header_names"] = sorted(response.headers.keys())
            result["safe_headers"] = safe_headers(response.headers)
            if response.status_code >= 400:
                result["text"] = response.read().decode("utf-8", errors="replace")
            else:
                for line in response.iter_lines():
                    result["lines"].append(line)
                    if time.monotonic() - started > STREAM_DEADLINE_S:
                        result["truncated_by_deadline"] = True
                        break
    else:
        response = client.post(url, headers=headers, json=body)
        result["status"] = response.status_code
        result["header_names"] = sorted(response.headers.keys())
        result["safe_headers"] = safe_headers(response.headers)
        result["text"] = response.text
    result["elapsed_s"] = round(time.monotonic() - started, 2)
    return result


def summarize_sse(lines: list[str]) -> dict[str, Any]:
    event_counts: dict[str, int] = {}
    deltas: list[str] = []
    terminal: str | None = None
    usage: Any = None
    for line in lines:
        if not line.startswith("data: "):
            continue
        payload = line.removeprefix("data: ")
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            event_counts["<unparseable>"] = event_counts.get("<unparseable>", 0) + 1
            continue
        kind = str(event.get("type", "<none>"))
        event_counts[kind] = event_counts.get(kind, 0) + 1
        if kind == "response.output_text.delta":
            deltas.append(str(event.get("delta", "")))
        if kind in TERMINAL_EVENTS:
            terminal = kind
            usage = (event.get("response") or {}).get("usage", usage)
    return {
        "event_counts": event_counts,
        "terminal_event": terminal,
        "output_text": "".join(deltas),
        "usage": usage,
    }


def summarize_muse(text: str) -> dict[str, Any]:
    data = json.loads(text)
    pieces: list[str] = []
    for item in data.get("output") or []:
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                pieces.append(str(part.get("text", "")))
    usage = data.get("usage") or {}
    return {
        "response_id": data.get("id"),
        "model": data.get("model"),
        "status": data.get("status"),
        "incomplete_details": data.get("incomplete_details"),
        "store": data.get("store"),
        "output_text": "".join(pieces),
        "usage": usage,
    }


def muse_cost_usd(usage: dict[str, Any]) -> float | None:
    tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
    if not isinstance(tokens_in, int) or not isinstance(tokens_out, int):
        return None
    return tokens_in * MUSE_INPUT_USD_PER_TOKEN + tokens_out * MUSE_OUTPUT_USD_PER_TOKEN


def sam_video_cost_usd(frames: int) -> float:
    return max(frames, SAM_VIDEO_MIN_BILLED_FRAMES) * SAM_VIDEO_USD_PER_FRAME


def muse_worst_case_usd(prompt_tokens: int, max_output_tokens: int) -> float:
    return prompt_tokens * MUSE_INPUT_USD_PER_TOKEN + max_output_tokens * MUSE_OUTPUT_USD_PER_TOKEN


@dataclass(frozen=True)
class SmokeCase:
    """One request: its body, whether it streams, its worst-case cost and its acceptance check."""

    name: str
    body: dict[str, Any]
    stream: bool
    worst_case_usd: float
    cost: Callable[[dict[str, Any]], float | None]  # from the summary of a successful response
    validate: Callable[[str], dict[str, Any]] | None = None  # applied to the output text
    check: Callable[[dict[str, Any]], dict[str, Any]] | None = None  # applied to the summary


def check_store_false(summary: dict[str, Any]) -> dict[str, Any]:
    """D9: the response must say it was not stored."""
    return {"store_reported": summary.get("store"), "passed": summary.get("store") is False}


CASE_NAMES = ("muse-text", "sam-image", "sam-video", "muse-structured", "muse-store-false")
DEFAULT_CASES = ("muse-text", "sam-image")  # the pair sent on 2026-10-27


def build_case(name: str, settings: Settings) -> SmokeCase:
    if name == "muse-text":
        return SmokeCase(
            name,
            muse_body(settings),
            stream=False,
            worst_case_usd=muse_worst_case_usd(MUSE_PROMPT_TOKEN_ALLOWANCE, MUSE_MAX_OUTPUT_TOKENS),
            cost=lambda summary: muse_cost_usd(summary.get("usage") or {}),
        )
    if name == "sam-image":
        # published per-image price; the stream is not known to report billing
        return SmokeCase(
            name,
            sam_body(settings, make_test_png()),
            stream=True,
            worst_case_usd=SAM_IMAGE_USD,
            cost=lambda summary: SAM_IMAGE_USD,
        )
    if name == "sam-video":
        return SmokeCase(
            name,
            sam_video_body(settings, make_test_mp4()),
            stream=True,
            worst_case_usd=sam_video_cost_usd(VIDEO_FRAMES),
            cost=lambda summary: sam_video_cost_usd(VIDEO_FRAMES),
            validate=validate_sam_video,
        )
    if name == "muse-structured":
        return SmokeCase(
            name,
            muse_structured_body(settings),
            stream=False,
            worst_case_usd=muse_worst_case_usd(
                MUSE_STRUCTURED_PROMPT_TOKEN_ALLOWANCE, MUSE_STRUCTURED_MAX_OUTPUT_TOKENS
            ),
            cost=lambda summary: muse_cost_usd(summary.get("usage") or {}),
            validate=validate_muse_structured,
        )
    if name == "muse-store-false":
        return SmokeCase(
            name,
            muse_body(settings, store=False, max_output_tokens=MUSE_STORE_FALSE_MAX_OUTPUT_TOKENS),
            stream=False,
            worst_case_usd=muse_worst_case_usd(
                MUSE_PROMPT_TOKEN_ALLOWANCE, MUSE_STORE_FALSE_MAX_OUTPUT_TOKENS
            ),
            cost=lambda summary: muse_cost_usd(summary.get("usage") or {}),
            check=check_store_false,
        )
    raise ValueError(f"unknown smoke case: {name}")


def write_cache(label: str, record: dict[str, Any], scrub: Callable[[str], str]) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = CACHE_DIR / f"{stamp}_{label}.json"
    path.write_text(scrub(json.dumps(record, indent=2, default=str)) + "\n", encoding="utf-8")
    return path


def run_one(
    case: SmokeCase,
    client: httpx.Client,
    url: str,
    settings: Settings,
    scrub: Callable[[str], str],
) -> tuple[bool, float | None]:
    """Send one request, print a scrubbed summary, cache the raw response. Returns (ok, cost)."""
    label, body, stream = case.name, case.body, case.stream
    sent_utc = datetime.now(UTC).isoformat()
    raw = post_once(client, url, request_headers(settings, stream), body, stream)
    status = raw["status"]
    ok = status < 400
    summary: dict[str, Any] = {}
    if ok and stream:
        summary = summarize_sse(raw["lines"])
        ok = summary["terminal_event"] == "response.completed"
    elif ok:
        summary = summarize_muse(raw["text"])
        ok = summary["status"] == "completed"
    cost = case.cost(summary) if ok else None
    if ok and case.validate is not None:
        summary["validation"] = case.validate(summary.get("output_text", ""))
        ok = bool(summary["validation"].get("passed", summary["validation"].get("schema_valid")))
    if ok and case.check is not None:
        summary["check"] = case.check(summary)
        ok = bool(summary["check"]["passed"])
    record = {
        "label": label,
        "sent_utc": sent_utc,
        "url": url,
        "request": {"body_redacted": redact_media(body), "body_sha256": body_sha256(body)},
        "response": raw,
        "summary": summary,
        "cost_usd": cost,
        "provenance": {"client": f"httpx {httpx.__version__}", "script": Path(__file__).name},
    }
    cached = write_cache(label, record, scrub)
    print(f"\n[{label}] HTTP {status} ({classify_status(status)}) in {raw['elapsed_s']}s")
    print(f"  request-id headers: {raw['safe_headers'] or 'none of the known names present'}")
    if status >= 400:
        print(f"  error body (scrubbed, truncated): {scrub(raw.get('text', ''))[:400]!r}")
    else:
        shown = {key: value for key, value in summary.items() if key != "output_text"}
        print(f"  summary: {scrub(json.dumps(shown, default=str))[:600]}")
        print(f"  output_text: {scrub(summary.get('output_text', ''))[:300]!r}")
    print(f"  cached raw response: {cached}")
    return ok, cost


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--send", action="store_true", help="send ONE request per case (spends)")
    parser.add_argument(
        "--case",
        action="append",
        choices=CASE_NAMES,
        help=f"case to run; repeatable (default: {' '.join(DEFAULT_CASES)})",
    )
    args = parser.parse_args(argv)
    selected = tuple(dict.fromkeys(args.case or DEFAULT_CASES))

    settings = load_settings()
    if not settings.has_api_key:
        print("No API key is configured; nothing to do.")
        return 2
    if not settings.meta_base_url.startswith("https://"):
        print("Refusing to send credentials to a non-HTTPS base URL.")
        return 2
    assert settings.model_api_key is not None
    scrub = make_scrubber(settings.model_api_key.get_secret_value())
    url = settings.meta_base_url.rstrip("/") + "/responses"

    plan = [build_case(name, settings) for name in selected]
    print(f"Endpoint: POST {url}  (one request per case, no retries)")
    for case in plan:
        digest = body_sha256(case.body)[:16]
        print(f"\n[{case.name}] request body (media redacted), sha256 {digest}...:")
        print(json.dumps(redact_media(case.body), indent=2))
    print("\nWorst-case cost at published prices:")
    for case in plan:
        print(f"  {case.name:<16} ${case.worst_case_usd:.6f}")
    print(f"  {'total':<16} ${sum(case.worst_case_usd for case in plan):.6f}")
    if not args.send:
        print("\nDry run: nothing was sent. Re-run with --send to spend.")
        return 0

    timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
    total = 0.0
    all_ok = True
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for case in plan:
            ok, cost = run_one(case, client, url, settings, scrub)
            total += cost or 0.0
            if not ok:
                all_ok = False
                print(
                    f"\nStopping after the first failure ({case.name}); no further requests sent."
                )
                break
    print(f"\nEstimated cost of this run: ${total:.6f} (from usage and published prices)")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
