"""A track's approach, exit and turn, by the reference analysis's rule.

The rule and its constants live in scripts/build_reference_analysis.py and are loaded from there, as
site_int2 loads the check points script, so a camera count and a reference count mean the same thing
by construction: `arm_of` of the first and last ground points, each only when it is at least
ARM_MIN_RADIUS_M from the junction centre, and `turn_of` on the pair. A track that leaves by the arm
it came in on has no exit and no turn: the window cut it off, it is not a U-turn.

An arm is a quadrant of the junction, so metres of placement error rarely change it. That is what
keeps counts usable where fine placement is not (ADR-018 does not accept along-road distances; a
quadrant does not need them). Without a site there is no ground, so an uncalibrated track has no
arms at all.

A camera track is not a published vehicle, and that decides what counts as entering. The reference
gives a vehicle a first point only where it enters the published region or at the window start. A
camera track can also begin in the middle of the view: a track that restarted after a dropout, or a
queued car the detector only saw once it moved. So a track enters only if it is first seen near the
edge of the usable image or at the start of the window (`enters_view`). Any other track keeps its
arms and its turn, because where it was and where it went are still what was seen, but it does not
add to an entering count. The reference has no such rule, so the two definitions differ.
"""

from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from cairoflow_perception.ngsim_junction import arm_of

REPO = Path(__file__).resolve().parents[4]
REFERENCE_SCRIPT = REPO / "scripts/build_reference_analysis.py"
ENTRY_MARGIN_PX = 40.0  # two car lengths: a car is first detected this close to the image's edge
ENTRY_START_S = 0.3  # a track first seen this soon after the window opens was already in view
ENTRY_START_FRAMES = 2  # or this many frames, at a stride: frame 0 of a dev block is withheld


@cache
def reference_rules() -> ModuleType:
    """scripts/build_reference_analysis.py, imported once for its constants and rules."""
    spec = importlib.util.spec_from_file_location("build_reference_analysis", REFERENCE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def __getattr__(name: str) -> Any:
    """ARM_MIN_RADIUS_M, STOPPED_M_S, turn_of, vehicle_class, headings_deg: the reference's own."""
    if name in ("ARM_MIN_RADIUS_M", "STOPPED_M_S", "turn_of", "vehicle_class", "headings_deg"):
        return getattr(reference_rules(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass(frozen=True)
class Movement:
    approach: str | None = None
    exit_arm: str | None = None
    turn: str | None = None
    entered: bool = True  # False: first seen mid-view, so it is no new arrival

    @property
    def complete(self) -> bool:
        return self.turn is not None

    @property
    def entering(self) -> str | None:
        """The arm this track adds to an entering count, if it adds to one."""
        return self.approach if self.entered else None


NO_MOVEMENT = Movement()


def _arm_at(u: float, v: float, min_radius_m: float) -> str | None:
    if not math.isfinite(u) or not math.isfinite(v) or math.hypot(u, v) < min_radius_m:
        return None
    return str(arm_of(np.array([u]), np.array([v]))[0])


def entry_opening_s(frame_dt_s: float) -> float:
    """How long after a window's first frame a track still counts as already in view."""
    return max(ENTRY_START_S, ENTRY_START_FRAMES * frame_dt_s)


def enters_view(
    x_px: float,
    y_px: float,
    since_start_s: float,
    frame_dt_s: float,
    width_px: float,
    bottom_px: float,
) -> bool:
    """Whether a track's first usable point is an arrival: near the edge of the usable image, whose
    bottom edge is `bottom_px`, or at the start of the window. `since_start_s` is seconds from the
    window's first frame to that point, and `frame_dt_s` the time between analysed frames."""
    margin = min(x_px, y_px, width_px - x_px, bottom_px - y_px)
    return margin <= ENTRY_MARGIN_PX or since_start_s <= entry_opening_s(frame_dt_s)


def classify(u_m: np.ndarray | None, v_m: np.ndarray | None, *, entered: bool = True) -> Movement:
    """Approach, exit and turn of one ground path. `u_m`, `v_m` are junction metres in time order,
    holding only the points that count; None or empty is an uncalibrated or unplaced track.
    `entered` is False for a track that does not count as arriving (`enters_view`)."""
    if u_m is None or v_m is None or len(u_m) == 0:
        return NO_MOVEMENT
    rules = reference_rules()
    radius = rules.ARM_MIN_RADIUS_M
    approach = _arm_at(float(u_m[0]), float(v_m[0]), radius)
    exit_arm = _arm_at(float(u_m[-1]), float(v_m[-1]), radius)
    turn = rules.turn_of(approach, exit_arm) if approach and exit_arm else None
    return Movement(approach, exit_arm if turn else None, turn, entered)
