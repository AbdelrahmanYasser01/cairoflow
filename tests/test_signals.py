"""Signal annotation loading and the SignalTimeline contract, on the synthetic 20 s clip."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from synthetic_clip import CLIP_ID, clip

from cairoflow_contracts.evidence import SignalInterval, SignalTimeline
from cairoflow_perception.signals import SignalAnnotationError, load_signal_annotation

PLAN = """# synthetic two-phase plan
time_s,arm,state,note
0,N,red,
0,S,red,
0,E,green,
0,W,green,
10,E,yellow,
10,W,yellow,
13,E,red,
13,W,red,all-red
14,N,green,
14,S,green,
"""


def _load(tmp_path: Path, text: str, label: str = "observed-annotated") -> SignalTimeline:
    path = tmp_path / "signals.csv"
    path.write_text(text, encoding="utf-8")
    return load_signal_annotation(path, clip(), label=label, source="test annotator")


def test_annotation_becomes_contiguous_intervals_per_arm(tmp_path: Path) -> None:
    timeline = _load(tmp_path, PLAN)
    assert (timeline.clip_id, timeline.label, timeline.window_s) == (
        CLIP_ID,
        "observed-annotated",
        20.0,
    )
    assert timeline.annotation_sha256 == hashlib.sha256(PLAN.encode("utf-8")).hexdigest()
    east = [(i.state, i.start_s, i.end_s) for i in timeline.intervals if i.arm == "E"]
    assert east == [("green", 0, 10), ("yellow", 10, 13), ("red", 13, 20)]
    assert timeline.state_at("N", 13.9) == "red"
    assert timeline.state_at("N", 14.0) == "green"
    assert timeline.state_at("N", 20.0) == "green"  # the clip end belongs to the last interval


def test_phase_per_second(tmp_path: Path) -> None:
    seconds = _load(tmp_path, PLAN).per_second()
    assert [s.time_s for s in seconds] == [float(t) for t in range(20)]
    assert seconds[0].green_arms == ("E", "W")
    assert seconds[11].green_arms == () and seconds[11].states["E"] == "yellow"
    assert seconds[13].states == {"N": "red", "E": "red", "S": "red", "W": "red"}  # all-red
    assert seconds[14].green_arms == ("N", "S")


def test_arms_start_unknown_and_repeated_states_merge(tmp_path: Path) -> None:
    timeline = _load(tmp_path, "time_s,arm,state\n2.5,N,red\n5,N,red\n8,N,green\n", "assumed")
    assert [(i.state, i.start_s, i.end_s) for i in timeline.intervals] == [
        ("unknown", 0, 2.5),
        ("red", 2.5, 8),
        ("green", 8, 20),
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "no header and no rows"),
        ("t,arm,state\n0,N,red\n", "line 1: header must be"),
        ("time_s,arm,state\n", "no signal changes"),
        ("time_s,arm,state\nsoon,N,red\n", "line 2: time_s is not a number"),
        ("time_s,arm,state\n20,N,red\n", "line 2: 20.0 s is outside the clip"),
        ("time_s,arm,state\n0,NE,red\n", "line 2: arm must be"),
        ("time_s,arm,state\n0,N,amber\n", "line 2: state must be"),
        (
            "time_s,arm,state\n0,N,red\n0,N,green\n",
            "line 3: arm N already changes at 0.0 s (line 2)",
        ),
        ("time_s,arm,state\n0,N,green\n5,N,red\n", "arm N: green cannot change to red at 5.0 s"),
        ("time_s,arm,state\n0,N,green\n0,E,green\n", "conflict: N green and E green"),
    ],
)
def test_bad_annotations_are_refused_with_a_reason(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(SignalAnnotationError, match=re.escape(message)):
        _load(tmp_path, text)


def test_timeline_contract_rejects_gaps_order_and_unknown_labels() -> None:
    def interval(arm, state, start, end) -> SignalInterval:
        return SignalInterval(arm=arm, state=state, start_s=start, end_s=end)

    common = {"timeline_id": "t", "clip_id": CLIP_ID, "source": "x", "window_s": 20.0}
    red, green = interval("N", "red", 0, 10), interval("N", "green", 11, 20)
    with pytest.raises(ValidationError, match="gap or overlap"):
        SignalTimeline(**common, label="published", intervals=(red, green))
    with pytest.raises(ValidationError, match="cover 0 to"):
        SignalTimeline(**common, label="published", intervals=(red,))
    with pytest.raises(ValidationError, match="sorted"):
        SignalTimeline(
            **common,
            label="published",
            intervals=(interval("S", "red", 0, 20), interval("N", "red", 0, 20)),
        )
    with pytest.raises(ValidationError):
        SignalTimeline(**common, label="model_inferred", intervals=(interval("N", "red", 0, 20),))
    with pytest.raises(ValidationError, match="positive length"):
        interval("N", "red", 5, 5)
