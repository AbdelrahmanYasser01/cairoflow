"""Synthetic SAM 3.1 event streams in the shape the API returned on 2026-10-27, shared by tests."""

from __future__ import annotations

import json

# One-bit mask of a full 48x48 square, copied from SAM 3.1's reply to our own synthetic clip
# (sam-video smoke test, 2026-10-27). The parser rejects a box record without its mask.
FULL_48_MASK = "!!!!!(y:URV.K:"


def token(frame: int, obj: int, x1: int, y1: int, w: int = 256, h: int = 256) -> str:
    """One 48x48 box record with its mask; `w` and `h` are the frame size."""
    box = f"x1={x1};y1={y1};x2={x1 + 47};y2={y1 + 47};w={w};h={h};c=0.9"
    return f"<{frame}f>{obj}<|box;{box}|><|mask;x=0;y=0;c=0.9;data=48,48,{FULL_48_MASK}|>\n"


def sse(tokens: list[str], terminal: str | None = "completed") -> list[str]:
    """An SSE stream in the shape SAM 3.1 returned on 2026-10-27."""
    text = "".join(tokens)
    part = {"type": "output_text", "text": "", "annotations": [], "logprobs": []}
    where = {"item_id": "msg_1", "output_index": 0, "content_index": 0}
    events: list[dict] = [
        {"type": "response.created", "response": {"status": "in_progress", "model": "sam-3.1"}},
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "message", "role": "assistant", "status": "in_progress",
                  "content": [part]}},
        {"type": "response.content_part.added", **where, "part": part},
        *({"type": "response.output_text.delta", **where, "delta": t} for t in tokens),
        {"type": "response.output_text.done", **where, "text": text},
        {"type": "response.content_part.done", **where, "part": part | {"text": text}},
    ]  # fmt: skip
    if terminal == "completed":
        usage = {"total_tokens": 0, "video_frames_processed": len(tokens)}
        events.append(
            {"type": "response.completed", "response": {"status": "completed", "usage": usage}}
        )
    elif terminal == "incomplete":
        details = {"reason": "max_output_tokens"}
        events.append(
            {
                "type": "response.incomplete",
                "response": {"status": "incomplete", "incomplete_details": details},
            }
        )
    elif terminal == "failed":
        error = {"message": "internal error"}
        events.append({"type": "response.failed", "response": {"status": "failed", "error": error}})
    lines: list[str] = []
    for event in events:
        lines += [f"event: {event['type']}", f"data: {json.dumps(event)}", ""]
    return lines + ["data: [DONE]"]
