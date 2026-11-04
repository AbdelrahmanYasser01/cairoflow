"""Candidates on a replay baseline: a validated cut-cycle change applied to every observed cycle.

The validator checks proposals against the cut cycle (cairoflow_simulation.validation). A replay
baseline (ADR-022) shows the observed sequence instead, so a validated outcome is carried over
by phase identity: the outcome's change to a cut phase (new green minus the cut's) is applied to
every adjustable phase of the replay program that shows the same states on every head. That is
each observed cycle where the phase occurs, and the drain, which repeats the cut. A change that
would bring a green below `min_green_s` stops there (clamped), and a phase that is already shorter
is left as it was; both are counted. Clearance and all-red phases never change.
"""

from __future__ import annotations

from collections import Counter

from cairoflow_contracts.experiment import SignalPhaseSpec, SignalProgramSpec
from cairoflow_contracts.reasoning import ValidationOutcome
from cairoflow_simulation.candidates import CandidateError, ProgramFor
from cairoflow_simulation.geometry_compiler import CompiledNetwork

MIN_GREEN_S = 5.0  # the timeline's assumed minimum green (DATA_REGISTER.md §16)


def signature(phase: SignalPhaseSpec) -> tuple[tuple[str, str], ...]:
    """The states a phase shows, head by head: what identifies it across cycles."""
    return (*sorted(phase.arm_states.items()), *((f"{a} left", s)
            for a, s in sorted(phase.left_states.items())))  # fmt: skip


def _label(key: tuple[tuple[str, str], ...]) -> str:
    return " ".join(f"{head}={state}" for head, state in key)


def changes(outcome: ValidationOutcome, cut: SignalProgramSpec) -> dict[tuple, float]:
    """Signature -> change of green (s) the outcome makes to the cut."""
    durations = outcome.phase_durations_s
    if outcome.status != "accepted" or durations is None:
        raise CandidateError(f"{outcome.intervention_id} was not accepted by the validator")
    if outcome.program_id != cut.program_id or len(durations) != len(cut.phases):
        raise CandidateError(f"{outcome.intervention_id} was validated against another program")
    found: dict[tuple, float] = {}
    for phase, new in zip(cut.phases, durations, strict=True):
        if new == phase.duration_s:
            continue
        if not phase.adjustable:
            raise CandidateError(f"{outcome.intervention_id} changes the clearance {phase.name}")
        found[signature(phase)] = new - phase.duration_s
    return found


def map_onto_replay(
    outcome: ValidationOutcome, cut: SignalProgramSpec, replay: SignalProgramSpec,
    min_green_s: float = MIN_GREEN_S,
) -> tuple[SignalProgramSpec, tuple[str, ...]]:  # fmt: skip
    """The replay program with the outcome's changes, and notes on how they applied."""
    delta = changes(outcome, cut)
    applied: Counter[tuple] = Counter()
    clamped: Counter[tuple] = Counter()
    short: Counter[tuple] = Counter()
    phases = []
    for phase in replay.phases:
        key = signature(phase)
        duration = phase.duration_s
        if phase.adjustable and key in delta:
            if duration < min_green_s:
                short[key] += 1
            else:
                wanted = duration + delta[key]
                if wanted < min_green_s:
                    clamped[key] += 1
                duration = max(wanted, min_green_s)
                applied[key] += 1
        phases.append(phase.model_copy(update={"duration_s": round(duration, 6)}))
    if not applied:
        raise CandidateError(f"no replay phase shows the states {outcome.intervention_id} changes")
    program = replay.model_copy(
        update={
            "program_id": f"{replay.program_id}_{outcome.intervention_id}",
            "cycle_s": round(sum(p.duration_s for p in phases), 6),
            "phases": tuple(phases),
        }
    )
    notes = tuple(
        f"{outcome.intervention_id} on the replay: {_label(key)}: {change:+g} s applied to "
        f"{applied[key]} phases ({clamped[key]} stopped at the {min_green_s:g} s minimum green; "
        f"{short[key]} already shorter, unchanged)"
        for key, change in delta.items()
    )
    return program, notes


def replay_program_for(cut: SignalProgramSpec, min_green_s: float = MIN_GREEN_S) -> ProgramFor:
    """A `program_for` for `candidates.run_candidates` on a replay baseline."""

    def build(
        outcome: ValidationOutcome, baseline: CompiledNetwork
    ) -> tuple[SignalProgramSpec, tuple[str, ...]]:
        return map_onto_replay(outcome, cut, baseline.manifest.signal_program, min_green_s)

    return build
