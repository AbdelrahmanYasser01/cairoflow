"""Seeded SAM prompts: the text the API is sent, and the limits on one request."""

from __future__ import annotations

import pytest

from cairoflow_perception.sam_seeded import (
    MAX_OBJECTS_PER_REQUEST,
    Seed,
    SeedError,
    clamp_box,
    seeded_metadata,
    seeded_prompt,
)


def seed(object_id: int = 1, frame: int = 0) -> Seed:
    return Seed(object_id, frame, (10, 20, 30, 40))


def test_prompt_uses_the_documented_key_value_grammar_with_inclusive_corners() -> None:
    text = seeded_prompt([seed()], 640, 480)
    assert text == "<0f>1<|box;x1=10;y1=20;x2=29;y2=39;w=640;h=480|>"


def test_objects_may_start_on_different_frames() -> None:
    text = seeded_prompt([seed(1, 0), seed(2, 34)], 640, 480)
    assert text.index("<0f>") < text.index("<34f>")
    assert "<34f>2<|box;" in text


def test_a_request_holds_at_most_sixteen_objects() -> None:
    seeds = [seed(i) for i in range(1, MAX_OBJECTS_PER_REQUEST + 2)]
    with pytest.raises(SeedError, match="exceed"):
        seeded_prompt(seeds, 640, 480)
    assert seeded_prompt(seeds[:-1], 640, 480).count("<|box;") == MAX_OBJECTS_PER_REQUEST


def test_a_request_needs_a_seed() -> None:
    with pytest.raises(SeedError, match="at least one"):
        seeded_prompt([], 640, 480)


def test_tracking_runs_forward_from_the_prompts() -> None:
    assert seeded_metadata()["propagation_direction"] == "forward"
    assert seeded_metadata()["include_confidence"] == "true"


@pytest.mark.parametrize(
    ("box", "expected"),
    [
        ((-5.0, -5.0, 10.2, 10.7), (0, 0, 10, 11)),
        ((630.0, 470.0, 700.0, 500.0), (630, 470, 640, 480)),
        ((700.0, 10.0, 720.0, 20.0), None),
        ((10.0, 10.0, 10.0, 20.0), None),
    ],
)
def test_boxes_are_clamped_to_the_frame(box, expected) -> None:
    assert clamp_box(box, 640, 480) == expected
