"""Detector-seeded SAM 3.1: hand SAM the vehicles a local detector found, as box prompts.

A noun-phrase request lets SAM choose objects from the first frame, and on this footage it stops at
16 ids with none born later, so vehicles entering after frame 0 are lost. A box prompt names the
object and the frame it starts on, so a request can follow up to 16 vehicles that appear at
different times. SAM then contributes what a box detector cannot: a mask and a tracked identity.

Prompt text comes from the official builder (`meta_sam_parser.build_object_prompt`). The response
reports each object under the id the request gave it. Output is model-generated evidence, not
ground truth; ids are only reliable while an object stays in frame.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from meta_sam_parser import FramePrompt, PromptBox, PromptObject, build_object_prompt

from cairoflow_contracts.evidence import SourceClip
from cairoflow_perception.sam_adapter import DEFAULT_METADATA, SamClient, SamRequestRecord
from cairoflow_perception.sam_parse import ParsedSegmentation, parse_sam_stream

MAX_OBJECTS_PER_REQUEST = 16  # API limit; a 17th object is refused
SEED_NAMESPACE = "vehicle"


class SeedError(ValueError):
    """The seeds cannot form one valid request."""


@dataclass(frozen=True)
class Seed:
    """One vehicle to follow: its id, the clip frame it starts on, and its box there."""

    object_id: int
    frame: int
    box_xyxy_px: tuple[int, int, int, int]


def clamp_box(
    box_xyxy_px: Sequence[float], width: int, height: int
) -> tuple[int, int, int, int] | None:
    """A box as whole pixels inside the frame (half-open), or None if nothing is left of it."""
    x1, y1, x2, y2 = box_xyxy_px
    left, top = max(0, int(x1)), max(0, int(y1))
    right, bottom = min(width, int(round(x2))), min(height, int(round(y2)))
    return (left, top, right, bottom) if right > left and bottom > top else None


def seeded_prompt(seeds: Sequence[Seed], width: int, height: int) -> str:
    """Object-prompt text for one request."""
    if not seeds:
        raise SeedError("a request needs at least one seed")
    if len(seeds) > MAX_OBJECTS_PER_REQUEST:
        raise SeedError(f"{len(seeds)} seeds exceed the limit of {MAX_OBJECTS_PER_REQUEST}")
    objects = [
        PromptObject(
            id=seed.object_id,
            prompts=[FramePrompt(frame=seed.frame, box=PromptBox(*seed.box_xyxy_px))],
        )
        for seed in seeds
    ]
    return build_object_prompt(size=(width, height), objects=objects)


def seeded_metadata() -> dict[str, str]:
    return {**DEFAULT_METADATA, "propagation_direction": "forward", "include_confidence": "true"}


def segment_seeded(
    client: SamClient, clip: SourceClip, clip_path: Path, seeds: Sequence[Seed]
) -> tuple[SamRequestRecord, ParsedSegmentation, bool]:
    """Send (or replay) one box-prompted request and parse it. Cache-first, like every SAM call."""
    width, height = clip.output_stream.width_px, clip.output_stream.height_px
    prompt = seeded_prompt(seeds, width, height)
    record, from_cache = client.segment_prompted(clip, clip_path, prompt, seeded_metadata())
    parsed = parse_sam_stream(
        record.lines,
        clip,
        prompt=SEED_NAMESPACE,
        model=record.model,
        request_key=record.request_key,
        sent_at_utc=record.sent_at_utc,
        cached=from_cache,
    )
    return record, parsed, from_cache
