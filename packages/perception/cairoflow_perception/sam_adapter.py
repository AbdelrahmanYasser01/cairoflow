"""SAM 3.1 video adapter: one cache-first request per (clip, prompt), with live and replay modes.

- `replay` never opens a connection; a cache miss is an error (ADR-009, offline judge demo).
- `live` returns the cached stream when there is one, so nothing is paid for twice. Otherwise it
  sends exactly ONE request, with no retries and no redirects, and only if the estimated cost
  fits the client's spend limit (default $0: live calls are refused until a limit is given).
- The API key is read from Settings, used only in the Authorization header, and scrubbed from
  everything written. Completed streams are cached under `cache/sam/<request_key>.json`;
  failed attempts are kept for audit under `cache/sam/failed/` and are never replayed.

Request shape (verified by the `sam-video` smoke test, 2026-10-27): POST /v1/responses with one
user message holding `input_text` (one noun phrase) and `input_video` (base64 MP4 data URL),
`stream: true`, string-valued `metadata`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.config import Settings
from cairoflow_contracts.evidence import SourceClip
from cairoflow_perception.ingest import sha256_file
from cairoflow_perception.sam_parse import (
    ParsedSegmentation,
    SamStreamError,
    class_namespace,
    decode_sse,
    frames_billed,
    parse_sam_stream,
    terminal_event,
)

# USD, https://dev.meta.ai/docs/pricing-rate-limits, accessed 2026-10-27.
SAM_VIDEO_USD_PER_FRAME = 0.20 / 1000
SAM_VIDEO_MIN_BILLED_FRAMES = 50
RECORD_VERSION = "0.1.0"
STREAM_DEADLINE_S = 600.0
DEFAULT_METADATA = {"include_confidence": "true"}  # metadata values are strings per the docs

Mode = Literal["live", "replay"]


class SamAdapterError(RuntimeError):
    """Base class for adapter failures."""


class SamCacheMiss(SamAdapterError):
    """Replay mode found no cached stream for the request."""


class SamSpendRefused(SamAdapterError):
    """A live request was refused before sending: no key, unsafe URL, or over the spend limit."""


class SamRequestError(SamAdapterError):
    """The API answered with an error status or an unusable stream. Nothing was cached as a hit."""

    def __init__(self, message: str, *, status: int, error_class: str, request_id: str | None):
        super().__init__(message)
        self.status = status
        self.error_class = error_class
        self.request_id = request_id


class SamRequestRecord(BaseModel):
    """One sent request and its raw stream, exactly as received (key scrubbed)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_version: Literal["0.1.0"] = RECORD_VERSION
    request_key: str
    model: str
    prompt: str
    clip_id: str
    clip_sha256: str
    metadata: dict[str, str]
    sent_at_utc: datetime
    http_status: int
    error_class: str
    request_id: str | None
    rate_limit_headers: dict[str, str] = Field(default_factory=dict)
    elapsed_s: float
    frames_billed: int | None
    estimated_cost_usd: float
    lines: tuple[str, ...]


def video_cost_usd(frames: int) -> float:
    return max(frames, SAM_VIDEO_MIN_BILLED_FRAMES) * SAM_VIDEO_USD_PER_FRAME


def classify_status(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "billing"
    if status == 429:
        return "rate_limited"
    if status in (400, 413, 415, 422):
        return "bad_request"
    if status >= 500:
        return "server"
    return "ok" if status < 400 else "client"


def request_key(model: str, prompt: str, clip_sha256: str, metadata: dict[str, str]) -> str:
    """Identity of a request: same model, prompt, clip bytes and metadata -> same cached stream."""
    canonical = json.dumps(
        {"model": model, "prompt": prompt, "clip_sha256": clip_sha256, "metadata": metadata},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SamClient:
    def __init__(
        self,
        settings: Settings,
        cache_dir: Path,
        mode: Mode,
        *,
        spend_limit_usd: float = 0.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._cache_dir = cache_dir
        self._mode = mode
        self._spend_limit_usd = spend_limit_usd
        self._transport = transport
        self.spent_usd = 0.0  # estimated, from published prices

    def cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def segment_video(
        self,
        clip: SourceClip,
        clip_path: Path,
        prompt: str,
        metadata: dict[str, str] | None = None,
    ) -> tuple[SamRequestRecord, bool]:
        """Return the raw stream record for (clip, prompt) and whether it came from the cache."""
        class_namespace(prompt)  # validates the prompt before anything else
        return self.segment_prompted(clip, clip_path, prompt, metadata)

    def segment_prompted(
        self,
        clip: SourceClip,
        clip_path: Path,
        prompt: str,
        metadata: dict[str, str] | None = None,
    ) -> tuple[SamRequestRecord, bool]:
        """As `segment_video`, for prompt text that is not a noun phrase (box or point prompts)."""
        metadata = dict(DEFAULT_METADATA if metadata is None else metadata)
        key = request_key(self._settings.sam_model, prompt, clip.output_sha256, metadata)
        cached = self.cache_path(key)
        if cached.is_file():
            record = SamRequestRecord.model_validate_json(cached.read_text(encoding="utf-8"))
            if record.request_key != key:
                raise SamAdapterError(f"cache file {cached.name} holds a different request")
            return record, True
        if self._mode == "replay":
            raise SamCacheMiss(f"no cached SAM stream for prompt {prompt!r} on clip {clip.clip_id}")
        return self._send(key, clip, clip_path, prompt, metadata), False

    def segment_and_parse(
        self,
        clip: SourceClip,
        clip_path: Path,
        prompt: str,
        metadata: dict[str, str] | None = None,
    ) -> ParsedSegmentation:
        record, from_cache = self.segment_video(clip, clip_path, prompt, metadata)
        return parse_sam_stream(
            record.lines,
            clip,
            prompt=record.prompt,
            model=record.model,
            request_key=record.request_key,
            sent_at_utc=record.sent_at_utc,
            cached=from_cache,
        )

    def _send(
        self, key: str, clip: SourceClip, clip_path: Path, prompt: str, metadata: dict[str, str]
    ) -> SamRequestRecord:
        settings = self._settings
        if not settings.has_api_key or settings.model_api_key is None:
            raise SamSpendRefused("no API key is configured")
        if not settings.meta_base_url.startswith("https://"):
            raise SamSpendRefused("refusing to send credentials to a non-HTTPS base URL")
        estimate = video_cost_usd(clip.output_stream.frame_count)
        if self.spent_usd + estimate > self._spend_limit_usd + 1e-12:
            raise SamSpendRefused(
                f"estimated ${estimate:.4f} would exceed the spend limit "
                f"${self._spend_limit_usd:.4f} (already spent ${self.spent_usd:.4f})"
            )
        if sha256_file(clip_path) != clip.output_sha256:
            raise SamAdapterError(f"{clip_path.name} does not match clip {clip.clip_id}")

        secret = settings.model_api_key.get_secret_value()
        data_url = "data:video/mp4;base64," + base64.b64encode(clip_path.read_bytes()).decode()
        body: dict[str, Any] = {
            "model": settings.sam_model,
            "stream": True,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_video", "video_url": data_url},
                    ],
                }
            ],
            "metadata": metadata,
        }
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        url = settings.meta_base_url.rstrip("/") + "/responses"
        timeout = httpx.Timeout(connect=10.0, read=300.0, write=120.0, pool=10.0)
        sent_at = datetime.now(UTC)
        started = time.monotonic()
        lines: list[str] = []
        with (
            httpx.Client(
                timeout=timeout, follow_redirects=False, transport=self._transport
            ) as http,
            http.stream("POST", url, headers=headers, json=body) as response,
        ):
            status = response.status_code
            response_headers = response.headers
            if status >= 400:
                lines = response.read().decode("utf-8", errors="replace").splitlines()
            else:
                for line in response.iter_lines():
                    lines.append(line)
                    if time.monotonic() - started > STREAM_DEADLINE_S:
                        break
        self.spent_usd += estimate if status < 400 else 0.0

        scrub = (lambda text: text.replace(secret, "[REDACTED]")) if secret else (lambda t: t)
        events: list[dict[str, Any]] = []
        problem: str | None = None
        if status >= 400:
            problem = f"HTTP {status}"
        else:
            try:
                events = decode_sse(lines)
            except SamStreamError as error:  # keep the raw lines for audit
                problem = str(error)
            else:
                terminal = terminal_event(events)
                kind = None if terminal is None else terminal.get("type")
                if kind != "response.completed":
                    problem = f"stream ended with {kind or 'no terminal event'}"
        record = SamRequestRecord(
            request_key=key,
            model=settings.sam_model,
            prompt=prompt,
            clip_id=clip.clip_id,
            clip_sha256=clip.output_sha256,
            metadata=metadata,
            sent_at_utc=sent_at,
            http_status=status,
            error_class=classify_status(status)
            if status >= 400
            else ("stream" if problem else "ok"),
            request_id=response_headers.get("x-request-id"),
            rate_limit_headers={
                name: value
                for name, value in response_headers.items()
                if name.lower().startswith("x-ratelimit") or name.lower() == "retry-after"
            },
            elapsed_s=round(time.monotonic() - started, 3),
            frames_billed=frames_billed(events) if events else None,
            estimated_cost_usd=estimate if status < 400 else 0.0,
            lines=tuple(scrub(line) for line in lines),
        )
        if problem is not None:
            failed_dir = self._cache_dir / "failed"
            failed_dir.mkdir(parents=True, exist_ok=True)
            stamp = sent_at.strftime("%Y%m%dT%H%M%S%fZ")
            (failed_dir / f"{key}_{stamp}.json").write_text(
                scrub(record.model_dump_json(indent=2)) + "\n", encoding="utf-8"
            )
            raise SamRequestError(
                f"SAM request failed: {problem}",
                status=status,
                error_class=record.error_class,
                request_id=record.request_id,
            )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        with self.cache_path(key).open("x", encoding="utf-8") as handle:
            handle.write(scrub(record.model_dump_json(indent=2)) + "\n")
        return record
