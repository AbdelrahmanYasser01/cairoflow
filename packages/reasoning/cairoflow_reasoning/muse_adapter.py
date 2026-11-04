"""Muse Spark adapter: one cache-first structured request per evidence bundle, live or replay.

Same rules as the SAM adapter (ADR-016):
- `replay` never opens a connection; a cache miss is an error (ADR-009, offline judge demo).
- `live` returns the cached reply when there is one. Otherwise it sends exactly ONE request, with
  no retries and no redirects, and only if an upper-bound cost estimate fits the spend limit
  (default $0: live calls are refused until a limit is given).
- The API key is used only in the Authorization header and scrubbed from everything written.
  Replies that completed and passed validation are cached under `<cache_dir>/<request_key>.json`;
  anything else is kept under `failed/` for audit and never replayed.

Request shape (verified by the `muse-structured` smoke case, 2026-10-27): POST /v1/responses,
plain-string `input`, `reasoning.effort`, `max_output_tokens`, `text.format` json_schema strict,
not streamed. The reply text is the `output_text` part of the `message` output item.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cairoflow_contracts.config import Settings
from cairoflow_contracts.evidence import Provenance
from cairoflow_contracts.reasoning import EvidenceBundle, ReasoningOutput, ReasoningResult
from cairoflow_reasoning.prompts import PROMPT_VERSION, build_request_body

# USD per token, https://dev.meta.ai/docs/pricing-rate-limits, accessed 2026-10-27.
MUSE_USD_PER_INPUT_TOKEN = 1.25 / 1e6
MUSE_USD_PER_CACHED_INPUT_TOKEN = 0.15 / 1e6
MUSE_USD_PER_OUTPUT_TOKEN = 4.25 / 1e6  # reasoning tokens are counted as output
RECORD_VERSION = "0.1.0"

Mode = Literal["live", "replay"]


class MuseAdapterError(RuntimeError):
    """Base class for adapter failures."""


class MuseCacheMiss(MuseAdapterError):
    """Replay mode found no cached reply for the request."""


class MuseSpendRefused(MuseAdapterError):
    """A live request was refused before sending: no key, unsafe URL, or over the spend limit."""


class MuseResponseError(MuseAdapterError):
    """The reply was incomplete, refused, empty, or not valid against the schema."""


class MuseRequestError(MuseAdapterError):
    """The API answered with an error status."""

    def __init__(self, message: str, *, status: int, error_class: str, request_id: str | None):
        super().__init__(message)
        self.status = status
        self.error_class = error_class
        self.request_id = request_id


class MuseRequestRecord(BaseModel):
    """One sent request and the raw reply body, exactly as received (key scrubbed)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_version: Literal["0.1.0"] = RECORD_VERSION
    request_key: str
    replicate: int = Field(
        1, ge=1, description="which independent sample of the same request this reply is"
    )
    model: str
    prompt_version: str
    bundle_id: str
    body: dict[str, Any] = Field(description="the request body; it holds no credentials")
    sent_at_utc: datetime
    http_status: int
    error_class: str
    request_id: str | None
    rate_limit_headers: dict[str, str] = Field(default_factory=dict)
    elapsed_s: float
    estimated_cost_usd: float
    response_text: str


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


def request_key(body: dict[str, Any]) -> str:
    """Identity of a request: the same body always maps to the same cached reply."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upper_bound_cost_usd(body: dict[str, Any]) -> float:
    """Before sending: at most one token per two bytes of body, plus every output token."""
    input_bound = len(json.dumps(body).encode("utf-8")) / 2
    return input_bound * MUSE_USD_PER_INPUT_TOKEN + body["max_output_tokens"] * (
        MUSE_USD_PER_OUTPUT_TOKEN
    )


def usage_cost_usd(usage: dict[str, Any]) -> float:
    """After the reply: the published rates applied to the reported usage."""
    cached = int((usage.get("input_tokens_details") or {}).get("cached_tokens", 0))
    fresh = int(usage.get("input_tokens", 0)) - cached
    return (
        fresh * MUSE_USD_PER_INPUT_TOKEN
        + cached * MUSE_USD_PER_CACHED_INPUT_TOKEN
        + int(usage.get("output_tokens", 0)) * MUSE_USD_PER_OUTPUT_TOKEN
    )


def flat_usage(usage: dict[str, Any]) -> dict[str, int]:
    flat = {k: int(v) for k, v in usage.items() if isinstance(v, int)}
    for group in ("input_tokens_details", "output_tokens_details"):
        for key, value in (usage.get(group) or {}).items():
            if isinstance(value, int):
                flat[key] = value
    return flat


def extract_output(response: dict[str, Any]) -> str:
    """The reply text of a completed Responses object, or a MuseResponseError saying why not."""
    status = response.get("status")
    if status != "completed":
        details = response.get("incomplete_details") or response.get("error")
        raise MuseResponseError(f"response status {status!r}: {details}")
    texts: list[str] = []
    for item in response.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "refusal":
                raise MuseResponseError(f"the model refused: {part.get('refusal', '')[:200]}")
            if part.get("type") == "output_text":
                texts.append(part.get("text", ""))
    text = "".join(texts).strip()
    if not text:
        raise MuseResponseError("the response holds no output text")
    return text


def parse_output(text: str) -> ReasoningOutput:
    try:
        return ReasoningOutput.model_validate_json(text)
    except ValidationError as error:
        raise MuseResponseError(
            f"the reply does not match the schema ({error.error_count()} errors)"
        ) from error


def validate_reasoning_reply(response: dict[str, Any]) -> None:
    """The default check of a reply: completed, and valid against the diagnosis schema."""
    parse_output(extract_output(response))


def citation_problems(output: ReasoningOutput, bundle: EvidenceBundle) -> tuple[str, ...]:
    """Unknown evidence ids and hypothesis ids the model cited (kept, flagged, not dropped)."""
    known = {item.evidence_id for item in bundle.items}
    hypotheses = {h.hypothesis_id for h in output.hypotheses}
    problems: list[str] = []
    for h in output.hypotheses:
        for eid in (*h.supporting_evidence_ids, *h.contradicting_evidence_ids):
            if eid not in known:
                problems.append(f"{h.hypothesis_id} cites unknown evidence {eid!r}")
    for i in output.interventions:
        for eid in i.evidence_ids:
            if eid not in known:
                problems.append(f"{i.intervention_id} cites unknown evidence {eid!r}")
        for hid in i.addresses_hypothesis_ids:
            if hid not in hypotheses:
                problems.append(f"{i.intervention_id} addresses unknown hypothesis {hid!r}")
    return tuple(problems)


class MuseClient:
    def __init__(
        self,
        settings: Settings,
        cache_dir: Path,
        mode: Mode,
        *,
        spend_limit_usd: float = 0.0,
        effort: str = "medium",
        max_output_tokens: int = 8000,
        store: bool | None = None,
        transport: httpx.BaseTransport | None = None,
        prompt_version: str = PROMPT_VERSION,
        replicate: int = 1,
        allow_contributor: bool = False,
    ) -> None:
        if replicate < 1:
            raise ValueError("replicate counts from 1")
        self._replicate = replicate  # independent samples of one request each have a cache file
        self._allow_contributor = allow_contributor
        self._prompt_version = prompt_version
        self._settings = settings
        self._cache_dir = cache_dir
        self._mode = mode
        self._spend_limit_usd = spend_limit_usd
        self._effort = effort
        self._max_output_tokens = max_output_tokens
        self._store = store
        self._transport = transport
        self.spent_usd = 0.0  # from reported usage at published prices

    def cache_path(self, key: str) -> Path:
        """`<key>.json` for the first sample of a request, `<key>.r<n>.json` for sample n."""
        suffix = "" if self._replicate == 1 else f".r{self._replicate}"
        return self._cache_dir / f"{key}{suffix}.json"

    def request_body(self, bundle: EvidenceBundle) -> dict[str, Any]:
        return build_request_body(
            bundle,
            self._settings.muse_model,
            effort=self._effort,
            max_output_tokens=self._max_output_tokens,
            store=self._store,
            prompt_version=self._prompt_version,
        )

    def fetch(self, bundle: EvidenceBundle) -> tuple[MuseRequestRecord, bool]:
        """The raw reply record for a bundle and whether it came from the cache."""
        return self.fetch_body(self.request_body(bundle), bundle.bundle_id)

    def fetch_body(
        self,
        body: dict[str, Any],
        label: str,
        validate: Callable[[dict[str, Any]], object] = validate_reasoning_reply,
    ) -> tuple[MuseRequestRecord, bool]:
        """`fetch` for a body built elsewhere (e.g. one carrying an image): the same cache, the
        same single request, the same scrubbing. `label` is stored as the record's `bundle_id`;
        `validate` must raise MuseResponseError for a reply that is not usable."""
        key = request_key(body)
        cached = self.cache_path(key)
        if cached.is_file():
            record = MuseRequestRecord.model_validate_json(cached.read_text(encoding="utf-8"))
            # the stored body must hash to the key too, so an edited request is never replayed
            if record.request_key != key or request_key(record.body) != key:
                raise MuseAdapterError(f"cache file {cached.name} holds a different request")
            if record.replicate != self._replicate:
                raise MuseAdapterError(f"cache file {cached.name} holds another replicate")
            # versions that share their instructions (0.3.0, 0.3.1) must not share replies
            if record.prompt_version != self._prompt_version:
                raise MuseAdapterError(f"cache file {cached.name} holds another prompt version")
            return record, True
        if self._mode == "replay":
            raise MuseCacheMiss(f"no cached Muse reply for bundle {label}")
        return self._send(key, body, label, validate), False

    def diagnose(self, bundle: EvidenceBundle) -> ReasoningResult:
        """Hypotheses and proposed interventions for a bundle, schema-validated."""
        record, from_cache = self.fetch(bundle)
        response = json.loads(record.response_text)
        output = parse_output(extract_output(response))
        usage = response.get("usage") or {}
        return ReasoningResult(
            bundle_id=bundle.bundle_id,
            request_key=record.request_key,
            response_id=response.get("id"),
            request_id=record.request_id,
            model=record.model,
            prompt_version=record.prompt_version,
            output=output,
            problems=citation_problems(output, bundle),
            usage=flat_usage(usage),
            estimated_cost_usd=record.estimated_cost_usd,
            provenance=Provenance(
                kind="model_generated",
                produced_by=record.model,
                produced_at_utc=record.sent_at_utc,
                input_sha256=(record.request_key,),
                parameters={
                    "prompt_version": record.prompt_version,
                    "effort": record.body["reasoning"]["effort"],
                    "max_output_tokens": record.body["max_output_tokens"],
                    **({"replicate": record.replicate} if record.replicate > 1 else {}),
                },
                cached=from_cache,
            ),
        )

    def _send(
        self,
        key: str,
        body: dict[str, Any],
        bundle_id: str,
        validate: Callable[[dict[str, Any]], object],
    ) -> MuseRequestRecord:
        settings = self._settings
        if settings.muse_model.endswith("-contributor") and not self._allow_contributor:
            raise MuseSpendRefused(
                f"model {settings.muse_model!r} is a contributor variant, which Meta's terms let "
                "train on the content sent; pass allow_contributor=True to use it"
            )
        if not settings.has_api_key or settings.model_api_key is None:
            raise MuseSpendRefused("no API key is configured")
        if not settings.meta_base_url.startswith("https://"):
            raise MuseSpendRefused("refusing to send credentials to a non-HTTPS base URL")
        bound = upper_bound_cost_usd(body)
        if self.spent_usd + bound > self._spend_limit_usd + 1e-12:
            raise MuseSpendRefused(
                f"up to ${bound:.4f} would exceed the spend limit ${self._spend_limit_usd:.4f} "
                f"(already spent ${self.spent_usd:.4f})"
            )

        secret = settings.model_api_key.get_secret_value()
        headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
        url = settings.meta_base_url.rstrip("/") + "/responses"
        timeout = httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0)
        sent_at = datetime.now(UTC)
        started = time.monotonic()
        with httpx.Client(
            timeout=timeout, follow_redirects=False, transport=self._transport
        ) as http:
            response = http.post(url, headers=headers, json=body)
        status = response.status_code
        text = response.text

        problem: str | None = None
        cost = 0.0
        if status >= 400:
            problem = f"HTTP {status}"
        else:
            try:
                parsed = json.loads(text)
                cost = usage_cost_usd(parsed.get("usage") or {})
                validate(parsed)
            except (json.JSONDecodeError, MuseResponseError) as error:
                problem = str(error)
        self.spent_usd += cost

        scrub = (lambda value: value.replace(secret, "[REDACTED]")) if secret else (lambda v: v)
        record = MuseRequestRecord(
            request_key=key,
            replicate=self._replicate,
            model=settings.muse_model,
            prompt_version=self._prompt_version,
            bundle_id=bundle_id,
            body=body,
            sent_at_utc=sent_at,
            http_status=status,
            error_class=classify_status(status)
            if status >= 400
            else ("reply" if problem else "ok"),
            request_id=response.headers.get("x-request-id"),
            rate_limit_headers={
                name: value
                for name, value in response.headers.items()
                if name.lower().startswith("x-ratelimit") or name.lower() == "retry-after"
            },
            elapsed_s=round(time.monotonic() - started, 3),
            estimated_cost_usd=round(cost, 8),
            response_text=scrub(text),
        )
        if problem is not None:
            failed_dir = self._cache_dir / "failed"
            failed_dir.mkdir(parents=True, exist_ok=True)
            stamp = sent_at.strftime("%Y%m%dT%H%M%S%fZ")
            (failed_dir / f"{key}_{stamp}.json").write_text(
                scrub(record.model_dump_json(indent=2)) + "\n", encoding="utf-8"
            )
            if status >= 400:
                raise MuseRequestError(
                    f"Muse request failed: {problem}",
                    status=status,
                    error_class=record.error_class,
                    request_id=record.request_id,
                )
            raise MuseResponseError(f"Muse reply unusable: {problem}")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        with self.cache_path(key).open("x", encoding="utf-8") as handle:
            handle.write(scrub(record.model_dump_json(indent=2)) + "\n")
        return record
