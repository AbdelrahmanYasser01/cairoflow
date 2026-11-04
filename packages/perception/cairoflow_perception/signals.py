"""Load a hand annotation of signal changes into a `SignalTimeline`.

Annotation file: UTF-8 CSV, versioned next to the clip's calibration. Blank lines and lines
starting with '#' are ignored. Header `time_s,arm,state` with an optional `note` column; one row
per change seen in the clip: from `time_s` (clip seconds) the signal facing traffic on `arm`
(N, E, S or W) shows `state` (green, yellow, red or unknown). Before an arm's first row its state
is unknown. A row repeating the arm's current state is merged into it. Example:

    # NGSIM Lankershim, annotated frame by frame
    time_s,arm,state,note
    0.0,N,red,
    0.0,E,green,
    12.4,E,yellow,
    15.6,E,red,
    16.6,N,green,all-red ends

The label is the caller's statement of what the file is: `observed-annotated` (read from visible
signal heads), `published` (copied from published timing for the clip's date) or `assumed`.
"""

from __future__ import annotations

import csv
import hashlib
import math
from collections import defaultdict
from pathlib import Path

from pydantic import ValidationError

from cairoflow_contracts.evidence import (
    ARM_ORDER,
    SignalInterval,
    SignalLabel,
    SignalState,
    SignalTimeline,
    SourceClip,
)

STATES: tuple[SignalState, ...] = ("green", "yellow", "red", "unknown")


class SignalAnnotationError(ValueError):
    """The annotation file is malformed or describes an impossible signal sequence."""


def load_signal_annotation(
    path: Path,
    clip: SourceClip,
    *,
    label: SignalLabel,
    source: str,
    timeline_id: str | None = None,
) -> SignalTimeline:
    """Read an annotation file and check it against the clip's duration."""
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    window = clip.output_stream.duration_s
    lines = [
        (number, line)
        for number, line in enumerate(data.decode("utf-8").splitlines(), start=1)
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise SignalAnnotationError(f"{path.name}: no header and no rows")
    rows = list(zip((n for n, _ in lines), csv.reader(line for _, line in lines), strict=True))
    header = [field.strip() for field in rows[0][1]]
    if header not in (["time_s", "arm", "state"], ["time_s", "arm", "state", "note"]):
        raise SignalAnnotationError(f"line {rows[0][0]}: header must be time_s,arm,state[,note]")

    changes: dict[str, dict[float, tuple[str, int]]] = defaultdict(dict)
    for number, fields in rows[1:]:
        if not 3 <= len(fields) <= len(header):
            raise SignalAnnotationError(f"line {number}: expected {','.join(header)}")
        try:
            time_s = float(fields[0])
        except ValueError:
            raise SignalAnnotationError(f"line {number}: time_s is not a number") from None
        arm, state = fields[1].strip(), fields[2].strip()
        if not math.isfinite(time_s) or not 0 <= time_s < window:
            raise SignalAnnotationError(
                f"line {number}: {time_s} s is outside the clip (0 to {window} s)"
            )
        if arm not in ARM_ORDER:
            raise SignalAnnotationError(f"line {number}: arm must be one of N, E, S, W")
        if state not in STATES:
            raise SignalAnnotationError(f"line {number}: state must be one of {', '.join(STATES)}")
        if time_s in changes[arm]:
            earlier = changes[arm][time_s][1]
            raise SignalAnnotationError(
                f"line {number}: arm {arm} already changes at {time_s} s (line {earlier})"
            )
        changes[arm][time_s] = (state, number)
    if not changes:
        raise SignalAnnotationError(f"{path.name}: no signal changes")

    intervals: list[SignalInterval] = []
    for arm in ARM_ORDER:
        if arm not in changes:
            continue
        ordered = sorted((t, state) for t, (state, _) in changes[arm].items())
        if ordered[0][0] > 0:
            ordered.insert(0, (0.0, "unknown"))
        merged: list[tuple[float, str]] = []
        for time_s, state in ordered:
            if not merged or merged[-1][1] != state:
                merged.append((time_s, state))
        ends = [t for t, _ in merged[1:]] + [window]
        intervals += [
            SignalInterval(arm=arm, state=state, start_s=start, end_s=end)
            for (start, state), end in zip(merged, ends, strict=True)
        ]
    try:
        return SignalTimeline(
            timeline_id=timeline_id or f"{clip.clip_id}-signals-{sha[:8]}",
            clip_id=clip.clip_id,
            label=label,
            source=source,
            annotation_sha256=sha,
            window_s=window,
            intervals=tuple(intervals),
        )
    except ValidationError as error:
        reasons = "; ".join(e["msg"].removeprefix("Value error, ") for e in error.errors())
        raise SignalAnnotationError(f"{path.name}: {reasons}") from error
