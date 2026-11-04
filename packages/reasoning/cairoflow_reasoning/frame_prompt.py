"""The frame-annotation request: one overlay frame, fixed instructions, a strict JSON schema.

Request shape from https://dev.meta.ai/docs/image-understanding (read 2026-11-03): an `input_image`
block inside a `user` message, `image_url` a plain string that may be a base64 `data:` URL; JPEG
is a supported type. The docs do not say whether `text.format` json_schema with `strict: true`
works together with image input; the one smoke request (scripts/muse_frame_smoke.py) is what tests
it. The schema uses only the plain keywords the diagnosis schema already uses
(prompts.SCHEMA_KEYWORDS), has no numeric field, and is parsed into
`cairoflow_contracts.meta_depth.FrameAnnotation`, model-generated text that nothing measures with.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import ValidationError

from cairoflow_contracts.meta_depth import (
    FRAME_REGIONS,
    IMAGE_QUALITY,
    OBJECT_CATEGORIES,
    OVERLAY_BOX_STATES,
    FrameAnnotation,
)
from cairoflow_reasoning.muse_adapter import MuseResponseError, extract_output
from cairoflow_reasoning.prompts import _object

FRAME_PROMPT_VERSION = "frame-0.1.0"
FRAME_SCHEMA_NAME = "cairoflow_frame_annotation"
FRAME_INSTRUCTIONS = (
    "The image is one frame of a review video of a signalised road junction, filmed from above "
    "by a fixed camera. Coloured boxes, track ids and captions are drawn over it by software, "
    "from SAM 3.1 segmentation output; they may be wrong. Describe only what is visible in this "
    "frame. For each road user you can see, give its category, the ninth of the image it is in "
    "(top_left, top, top_right, left, center, right, bottom_left, bottom, bottom_right), whether "
    "a coloured overlay box is drawn on it (boxed, not_boxed, unclear) and a short note. In "
    "overlay_issues, list boxes or captions that look wrong: a box on no road user, a road user "
    "without a box, two boxes on one road user. Do not estimate speeds, queue lengths, counts "
    "over time or any other measurement, and do not guess what is outside the frame. Give the "
    "image quality (clear, partly_obscured, poor) and, in limitations, what one frame cannot "
    "show. Answer only with JSON in the given schema."
)
FRAME_SCHEMA: dict[str, Any] = _object(
    {
        "scene_summary": {"type": "string"},
        "objects": {
            "type": "array",
            "items": _object(
                {
                    "category": {"type": "string", "enum": list(OBJECT_CATEGORIES)},
                    "region": {"type": "string", "enum": list(FRAME_REGIONS)},
                    "overlay_box": {"type": "string", "enum": list(OVERLAY_BOX_STATES)},
                    "note": {"type": "string"},
                }
            ),
        },
        "overlay_issues": {"type": "array", "items": {"type": "string"}},
        "image_quality": {"type": "string", "enum": list(IMAGE_QUALITY)},
        "limitations": {"type": "array", "items": {"type": "string"}},
    }
)


def build_frame_request_body(
    image_jpeg: bytes,
    model: str,
    *,
    effort: str = "low",
    max_output_tokens: int = 6000,
    store: bool = False,
) -> dict[str, Any]:
    """Responses API body: the instructions and the JPEG in one user message, strict schema."""
    data_url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode("ascii")
    return {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": FRAME_INSTRUCTIONS},
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": FRAME_SCHEMA_NAME,
                "schema": FRAME_SCHEMA,
                "strict": True,
            }
        },
        "store": store,
    }


def parse_frame_reply(response: dict[str, Any]) -> FrameAnnotation:
    """The annotation in a completed reply, or MuseResponseError saying why there is none."""
    text = extract_output(response)
    try:
        return FrameAnnotation.model_validate_json(text)
    except (ValidationError, json.JSONDecodeError) as error:
        count = error.error_count() if isinstance(error, ValidationError) else 1
        raise MuseResponseError(
            f"the reply does not match the frame schema ({count} errors)"
        ) from error


def validate_frame_reply(response: dict[str, Any]) -> None:
    """The validator handed to MuseClient.fetch_body."""
    parse_frame_reply(response)
