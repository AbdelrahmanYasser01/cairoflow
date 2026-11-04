"""Admissibility checks for a member of the intersection 2 signal-timeline ensemble (ADR-025).

The gate is pre-registered in `data/geometry/ngsim-lankershim-int2/timeline_ensemble.json`, before
any candidate is simulated on a new member. These functions compute its checks; the scripts
decide and record them. A member that fails is reported, never replaced by a tuned variant.

- G1 (the same code reproduces the committed versions) needs the committed files and is checked
  by `scripts/build_ngsim_int2_timeline_ensemble.py`.
- G2: no second in which two conflicting heads both show green or yellow (`conflicting_seconds`,
  the rule of `scripts/build_ngsim_int2_timeline_v2.py`); and no phase of the compiled replay
  program that gives priority green to two foe links (`priority_conflicts`: the validator's check
  7, cairoflow_simulation.validation, applied to the replay program).
- G3: the replay window is the reference one (the same observed vehicles), and the compiled
  replay program shows the timeline second by second (`program_seconds_differing`, the check of
  `scripts/validate_ngsim_int2_baseline.py`).
- G4: in the window, no through or left movement has more crossings shown red than on the
  reference timeline (`red_crossings`, `more_red_than`). Right turns are left out: right turn on
  red is legal and modelled.
- G5: the replay baseline is valid on every pre-registered seed (`baseline_problems`).

Observed crossing times are `derived` from the published trajectories; the timelines are
`trajectory-derived`; nothing here is simulated except what G5 reads from SUMO's run records.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations

from cairoflow_contracts.evidence import MovementCount, SignalTimeline
from cairoflow_contracts.experiment import BaselineManifest, SignalProgramSpec
from cairoflow_evaluation.baseline_validation import clip_timeline, program_timeline
from cairoflow_perception.ngsim_junction import heads_conflict
from cairoflow_simulation.geometry_compiler import CompiledNetwork
from cairoflow_simulation.validation import _priority_conflicts

GATED_TURNS = ("left", "through")


def conflicting_seconds(timeline: SignalTimeline) -> list[str]:
    """Seconds of the grid where two conflicting heads both show green or yellow (G2)."""
    found = []
    for a, b in combinations(timeline.heads(), 2):
        if not heads_conflict(a, b):
            continue
        for t in range(int(timeline.window_s)):
            moving = [
                timeline.state_at(arm, t + 0.5, head) in ("green", "yellow") for arm, head in (a, b)
            ]
            if all(moving):
                found.append(f"{a[0]} {a[1]} and {b[0]} {b[1]} at {t} s")
    return found


def priority_conflicts(network: CompiledNetwork) -> list[str]:
    """Phases of the compiled program giving priority green to two foe links (G2)."""
    return _priority_conflicts(network)


def program_seconds_differing(
    program: SignalProgramSpec, timeline: SignalTimeline, window: tuple[float, float]
) -> int:
    """Seconds of the replay window in which the compiled program shows another state than the
    timeline on any head; every second counts if their heads differ (G3)."""
    length = window[1] - window[0]
    replayed = program_timeline(
        [(p.duration_s, p.arm_states, p.left_states) for p in program.phases],
        0.0,
        length,
        timeline_id="replay-program",
        clip_id=timeline.clip_id,
        source="the replay baseline's signal program",
    )
    want = clip_timeline(timeline, *window)
    if replayed.heads() != want.heads():
        return int(length)
    return sum(
        any(replayed.state_at(a, t, h) != want.state_at(a, t, h) for a, h in want.heads())
        for t in range(int(length))
    )


def red_crossings(
    timeline: SignalTimeline, movements: Sequence[MovementCount], window: tuple[float, float]
) -> dict[str, tuple[int, int]]:
    """'<arm> <turn>' -> (crossings shown red, crossings) in [start, end), for through and left
    movements; a left turn uses its arm's left head where it has one (as `consistency`)."""
    heads = set(timeline.heads())
    out: dict[str, tuple[int, int]] = {}
    for movement in movements:
        if movement.turn not in GATED_TURNS or not movement.tracks:
            continue
        head = "left" if movement.turn == "left" and (movement.approach, "left") in heads else "all"
        times = [
            t.stop_line_time_s
            for t in movement.tracks
            if window[0] <= t.stop_line_time_s < window[1]
        ]
        red = sum(timeline.state_at(movement.approach, t, head) == "red" for t in times)
        key = f"{movement.approach} {movement.turn}"
        before = out.get(key, (0, 0))
        out[key] = (before[0] + red, before[1] + len(times))
    return dict(sorted(out.items()))


def more_red_than(
    member: dict[str, tuple[int, int]], reference: dict[str, tuple[int, int]]
) -> list[str]:
    """Movements the member shows red to more often than the reference does (G4)."""
    return [
        f"{key}: {red} of {n} shown red, against {reference.get(key, (0, n))[0]} on the reference"
        for key, (red, n) in member.items()
        if red > reference.get(key, (0, n))[0]
    ]


def baseline_problems(manifest: BaselineManifest, seeds: Sequence[int]) -> list[str]:
    """Why a replay baseline fails G5: not valid, reruns not identical, other seeds than the
    pre-registered ones, or any run with a teleport, a collision or a vehicle not arrived."""
    problems = [f"not valid: {reason}" for reason in manifest.reasons]
    if not manifest.valid and not problems:
        problems.append("not valid")
    if manifest.reruns_identical is not True:
        problems.append(f"reruns identical: {manifest.reruns_identical}")
    if tuple(run.seed for run in manifest.runs) != tuple(seeds):
        problems.append(
            f"seeds {[run.seed for run in manifest.runs]}, pre-registered {list(seeds)}"
        )
    for run in manifest.runs:
        if run.teleports or run.collisions:
            problems.append(
                f"seed {run.seed}: {run.teleports} teleports, {run.collisions} collisions"
            )
        if run.unfinished or not run.loaded == run.inserted == run.arrived:
            problems.append(
                f"seed {run.seed}: loaded {run.loaded}, inserted {run.inserted}, arrived "
                f"{run.arrived}, unfinished {run.unfinished}"
            )
    return problems
