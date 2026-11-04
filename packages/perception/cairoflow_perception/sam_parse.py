"""Turn a raw SAM 3.1 Responses event stream into class-namespaced Observations.

Decoding of the segmentation grammar is left entirely to the official `meta-sam-parser`
(`parse_responses_stream` + `video_segmentation_format`); this module only splits the SSE text
into JSON events, maps SAM frame indices to the ingested clip's frames, and builds Observations.

Observations are model output (`kind="model_generated"`), not ground truth. SAM object ids are
only stable while an object stays visible; stitching ids across gaps belongs to tracking.
Masks stay in the cached stream and are referenced, not copied:
`cache://sam/<request_key>/<record order>`.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterable, Mapping
from datetime import datetime
from importlib import metadata
from typing import Any, Literal

from meta_sam_parser import (
    ResponsesStreamError,
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    parse_responses_stream,
    video_segmentation_format,
)
from pydantic import BaseModel, ConfigDict

from cairoflow_contracts.evidence import Observation, Provenance, SourceClip

PROMPT_PATTERN = re.compile(r"^[a-z][a-z0-9]*( [a-z0-9]+)*$")
TERMINAL_EVENTS = frozenset(
    {"response.completed", "response.incomplete", "response.failed", "error"}
)


class SamStreamError(RuntimeError):
    """The stream cannot be turned into observations: malformed, failed, refused or truncated."""


class ParsedSegmentation(BaseModel):
    """Observations from one SAM request plus everything that was dropped, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    clip_id: str
    prompt: str
    model: str
    request_key: str
    outcome: Literal["completed", "incomplete"]
    incomplete_detail: str | None = None
    observations: tuple[Observation, ...]
    parser_diagnostics: tuple[str, ...]  # "code: message" from meta-sam-parser
    dropped: tuple[str, ...]  # our own rejections, one line per dropped record


def class_namespace(prompt: str) -> str:
    """'traffic light' -> 'traffic_light'; the prefix of every object id from that prompt."""
    if not PROMPT_PATTERN.match(prompt):
        raise ValueError(
            f"prompt {prompt!r} must be lowercase words (one SAM noun phrase), e.g. 'car'"
        )
    return prompt.replace(" ", "_")


def decode_sse(lines: Iterable[str]) -> list[dict[str, Any]]:
    """JSON events from the `data:` lines of an SSE stream. Only `[DONE]` may be non-JSON."""
    events: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        if not line.startswith("data:"):
            continue  # `event:` names repeat the JSON `type`; blank lines separate events
        payload = line.removeprefix("data:").strip()
        if payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as error:
            raise SamStreamError(f"line {number} is not a JSON event") from error
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise SamStreamError(f"line {number} has no event type")
        events.append(event)
    return events


def terminal_event(events: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    found = None
    for event in events:
        if event.get("type") in TERMINAL_EVENTS:
            found = event
    return found


def frames_billed(events: Iterable[Mapping[str, Any]]) -> int | None:
    """`video_frames_processed` from the completed event's usage, if reported."""
    terminal = terminal_event(events)
    usage = ((terminal or {}).get("response") or {}).get("usage") or {}
    value = usage.get("video_frames_processed")
    return value if isinstance(value, int) else None


async def _official_parse(events: list[dict[str, Any]]) -> Any:
    async def source() -> AsyncIterator[dict[str, Any]]:
        for event in events:
            yield event

    return await parse_responses_stream(source(), video_segmentation_format()).final_result()


def mask_records(lines: Iterable[str]) -> dict[tuple[str, int], SegmentationMaskRecord]:
    """The latest mask of each (object id, frame index) in a stream, as the parser gives them."""
    result = asyncio.run(_official_parse(decode_sse(lines)))
    return {
        (record.object_id, record.frame.frame_index): record
        for record in result.records
        if isinstance(record, SegmentationMaskRecord) and record.frame is not None
    }


def parse_sam_stream(
    lines: Iterable[str],
    clip: SourceClip,
    *,
    prompt: str,
    model: str,
    request_key: str,
    sent_at_utc: datetime,
    cached: bool,
) -> ParsedSegmentation:
    """Parse one SAM video stream for `clip` into Observations."""
    namespace = class_namespace(prompt)
    events = decode_sse(lines)
    if terminal_event(events) is None:
        raise SamStreamError("the stream ended without a terminal event (truncated)")
    try:
        result = asyncio.run(_official_parse(events))
    except ResponsesStreamError as error:  # refusal, failure, error event, lane problems
        raise SamStreamError(f"{type(error).__name__}: {error}") from error
    if result.media != "video":
        raise SamStreamError(f"expected video output, got {result.media!r}")

    width, height = clip.output_stream.width_px, clip.output_stream.height_px
    mask_order = {
        (record.object_id, record.frame.frame_index): record.order
        for record in result.records
        if isinstance(record, SegmentationMaskRecord) and record.frame is not None
    }
    provenance = Provenance(
        kind="model_generated",
        produced_by=model,
        produced_at_utc=sent_at_utc,
        input_sha256=(clip.output_sha256,),
        parameters={"prompt": prompt, "request_key": request_key},
        software={"meta-sam-parser": metadata.version("meta-sam-parser")},
        cached=cached,
    )
    latest: dict[tuple[str, int], SegmentationBoxRecord] = {}
    dropped: list[str] = []
    for record in result.records:
        if not isinstance(record, SegmentationBoxRecord):
            continue
        if record.frame is None:
            dropped.append(f"record {record.order}: no frame index")
            continue
        index = record.frame.frame_index
        if not 0 <= index < len(clip.frames):
            dropped.append(f"record {record.order}: frame {index} is not in clip {clip.clip_id}")
            continue
        if record.right > width or record.bottom > height:
            dropped.append(f"record {record.order}: box exceeds the {width}x{height} frame")
            continue
        key = (record.object_id, index)
        if key in latest:  # a later revision replaces the earlier record
            dropped.append(f"record {latest[key].order}: superseded by record {record.order}")
        latest[key] = record

    observations = []
    for (object_id, index), record in sorted(latest.items(), key=lambda item: item[1].order):
        order = mask_order.get((object_id, index))
        observations.append(
            Observation(
                observation_id=f"{clip.clip_id}:{namespace}:{object_id}:{index}",
                frame=clip.frames[index],
                class_label=prompt,
                object_id=f"{namespace}:{object_id}",
                bbox_xyxy_px=(record.left, record.top, record.right, record.bottom),
                mask_artifact_uri=None if order is None else f"cache://sam/{request_key}/{order}",
                confidence=record.confidence,
                provenance=provenance,
            )
        )
    outcome = result.outcome
    return ParsedSegmentation(
        clip_id=clip.clip_id,
        prompt=prompt,
        model=model,
        request_key=request_key,
        outcome=outcome.status,
        incomplete_detail=getattr(outcome, "detail", None),
        observations=tuple(observations),
        parser_diagnostics=tuple(f"{d.code}: {d.message}" for d in result.diagnostics),
        dropped=tuple(dropped),
    )
