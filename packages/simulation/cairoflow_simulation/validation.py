"""Deterministic validation of proposed interventions against the compiled network (ADR-015).

A model proposes; this module decides what may be simulated. A `signal_timing` proposal is
accepted only if every check passes; otherwise every failed check is listed:

1. Type in the allowlist (`signal_timing` only); nothing else is checked for other types.
2. Target is the compiled program; at least one phase change; no phase named twice.
3. Each named phase exists and is a green phase with no yellow on any head: clearance
   intervals (any yellow, or all-red) and the phase order stay as compiled.
4. Each new green is whole seconds within [MIN_GREEN_S, MAX_GREEN_S].
5. The timing differs from the baseline, and the cycle stays within [MIN_CYCLE_S, MAX_CYCLE_S].
6. Per signal head, over the cycle as SUMO repeats it (validator 0.2.0): every
   run of green lasts [MIN_GREEN_S, MAX_GREEN_S], a protected left's [LEFT_GREEN_S], every run
   of yellow [YELLOW_S], and every all-red interval [ALL_RED_S]. A head's yellow may be spread
   over several phases (one head clears while another stays green), so runs are summed.
7. At least one cited evidence id, and every cited id exists in the bundle.
8. No phase gives priority green ('G') to two conflicting links (read from the .net.xml). Here it
   reads the baseline program, whose states a proposal cannot change, so it cannot fire for a
   proposal; the check that can is `compiled_conflicts`, on the tlLogic of the compiled candidate
   SUMO will run (candidates.embed_candidate_program), and `compiled_problems` repeats checks 5
   and 6 on that file's durations (safety.py).

`validate_proposals` also rejects a proposal whose timing repeats an earlier accepted one, so
accepted candidates are distinct; given the reply's grounding report (cairoflow_reasoning.
grounding), it rejects a proposal that addresses no supported hypothesis. Every outcome names the
validator version and the SHA-256 of the proposal it judged. Building candidate networks belongs
to the experiment step.

`check_proposals` is the validator's full result: each outcome with the proposal's preconditions
as unverified assumptions. They are the model's statements about the world (the controller, lane
use, demand); no check here can confirm them, so they never accept or reject, and they are listed
whatever the verdict. `ValidationOutcome` (a frozen contract) has no field for them.
"""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import sumolib
from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.experiment import SignalPhaseSpec
from cairoflow_contracts.reasoning import (
    ALLOWED_INTERVENTIONS,
    MAX_CYCLE_S,
    MAX_GREEN_S,
    MIN_CYCLE_S,
    MIN_GREEN_S,
    EvidenceBundle,
    GroundingReport,
    InterventionProposal,
    ValidationOutcome,
)
from cairoflow_simulation.geometry_compiler import CompiledNetwork
from cairoflow_simulation.network import JUNCTION_ID

VALIDATOR_VERSION = "0.2.0"
# Bounds per head and cycle; like the green bounds, practical assumptions.
LEFT_GREEN_S = (5.0, 40.0)
YELLOW_S = (3.0, 6.0)
ALL_RED_S = (1.0, 6.0)
UNVERIFIED_PRECONDITION = "unverified precondition (model-stated; no code checks it)"


class ValidationInputError(ValueError):
    """The bundle was not built from this network, so proposals cannot be checked against it."""


class CheckedProposal(BaseModel):
    """The validator's result on one proposal: its verdict and what it could not verify."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ValidationOutcome
    unverified_assumptions: tuple[str, ...] = Field(
        description="the proposal's preconditions, model-stated and unchecked; never reasons"
    )


def proposal_sha256(proposal: InterventionProposal) -> str:
    canonical = json.dumps(proposal.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _conflicts(net_path: Path, phases: Iterable[tuple[str, str]]) -> list[str]:
    """(name, state) phases that give priority green ('G') to two links the junction says are
    foes."""
    node = sumolib.net.readNet(str(net_path)).getNode(JUNCTION_ID)
    found = []
    for name, state in phases:
        priority = [i for i, char in enumerate(state) if char == "G"]
        for position, first in enumerate(priority):
            for second in priority[position + 1 :]:
                if node.areFoes(first, second):
                    found.append(
                        f"phase {name} gives priority green to conflicting links "
                        f"{first} and {second}"
                    )
    return found


def priority_conflicts(net_path: Path, phases: Sequence[SignalPhaseSpec]) -> list[str]:
    """Phases of a program spec that give priority green ('G') to two foes of the junction."""
    return _conflicts(net_path, ((phase.name, phase.state) for phase in phases))


def compiled_phases(net_path: Path, program_id: str) -> tuple[tuple[str, float, str], ...]:
    """(name, duration s, state) of each phase of `program_id` as the compiled .net.xml holds it:
    the program SUMO will run, not the spec it was written from."""
    programs = [
        logic
        for logic in ET.parse(net_path).getroot().iter("tlLogic")
        if logic.get("id") == JUNCTION_ID and logic.get("programID") == program_id
    ]
    if len(programs) != 1:
        raise ValidationInputError(
            f"{net_path.name} holds {len(programs)} programs {program_id!r} for {JUNCTION_ID}"
        )
    return tuple(
        (p.get("name", ""), float(p.get("duration", "nan")), p.get("state", ""))
        for p in programs[0].findall("phase")
    )


def compiled_conflicts(net_path: Path, program_id: str) -> list[str]:
    """Check 8 on the compiled candidate: its own tlLogic states against its junction's foes."""
    return _conflicts(net_path, ((name, state) for name, _, state in compiled_phases(
        net_path, program_id)))  # fmt: skip


def compiled_problems(
    net_path: Path,
    program_id: str,
    phases: Sequence[SignalPhaseSpec],
    durations: Sequence[float],
) -> list[str]:
    """Checks 5, 6 and 8 on the compiled candidate: the file SUMO will run shows the validated
    phases, states and durations, its cycle and head runs are within bounds, and no phase gives
    priority green to two foes. `phases` name the heads; `durations` are the validated ones."""
    compiled = compiled_phases(net_path, program_id)
    problems: list[str] = []
    shown = [(name, state) for name, _, state in compiled]
    if shown != [(phase.name, phase.state) for phase in phases]:
        problems.append(f"the compiled program {program_id} does not show the validated phases "
                        "and states")  # fmt: skip
    seconds = [duration for _, duration, _ in compiled]
    if seconds != [float(d) for d in durations]:
        problems.append(f"the compiled durations {[f'{s:g}' for s in seconds]} are not the "
                        f"validated durations {[f'{float(d):g}' for d in durations]}")  # fmt: skip
    problems += compiled_conflicts(net_path, program_id)
    if len(seconds) == len(phases):
        cycle = sum(seconds)
        if not MIN_CYCLE_S <= cycle <= MAX_CYCLE_S:
            problems.append(f"compiled cycle {cycle:g} s is outside {MIN_CYCLE_S:g}-"
                            f"{MAX_CYCLE_S:g} s")  # fmt: skip
        problems += timing_problems(phases, seconds)
    return problems


def _priority_conflicts(network: CompiledNetwork) -> list[str]:
    return priority_conflicts(network.net_path, network.manifest.signal_program.phases)


def head_runs(
    phases: Sequence[SignalPhaseSpec], durations: Sequence[float]
) -> dict[str, list[tuple[str, float]]]:
    """Head ('N' or 'N-left') -> its runs of one state, (state, seconds), through one cycle as
    SUMO repeats it: equal neighbours merged, and the last run joined to the first when the cycle
    wraps round in the same state."""
    heads: dict[str, list[tuple[str, float]]] = {}
    for phase, duration in zip(phases, durations, strict=True):
        states = {**phase.arm_states, **{f"{arm}-left": s for arm, s in phase.left_states.items()}}
        for head, state in states.items():
            runs = heads.setdefault(head, [])
            if runs and runs[-1][0] == state:
                runs[-1] = (state, runs[-1][1] + duration)
            else:
                runs.append((state, duration))
    for runs in heads.values():
        if len(runs) > 1 and runs[0][0] == runs[-1][0]:
            state, last = runs.pop()
            runs[0] = (state, runs[0][1] + last)
    return heads


def timing_problems(phases: Sequence[SignalPhaseSpec], durations: Sequence[float]) -> list[str]:
    """Check 6 of the module notes on a whole cycle."""
    problems: list[str] = []
    for head, runs in sorted(head_runs(phases, durations).items()):
        green = LEFT_GREEN_S if head.endswith("-left") else (MIN_GREEN_S, MAX_GREEN_S)
        for state, seconds in runs:
            bounds = {"green": green, "yellow": YELLOW_S}.get(state)
            if bounds and not bounds[0] <= seconds <= bounds[1]:
                problems.append(f"head {head} would show {state} for {seconds:g} s in a run, "
                                f"outside {bounds[0]:g}-{bounds[1]:g} s")  # fmt: skip
    for phase, duration in zip(phases, durations, strict=True):
        states = (*phase.arm_states.values(), *phase.left_states.values())
        if all(s == "red" for s in states) and not ALL_RED_S[0] <= duration <= ALL_RED_S[1]:
            problems.append(f"all-red phase {phase.name} lasts {duration:g} s, outside "
                            f"{ALL_RED_S[0]:g}-{ALL_RED_S[1]:g} s")  # fmt: skip
    return problems


def validate_proposal(
    proposal: InterventionProposal,
    network: CompiledNetwork,
    bundle: EvidenceBundle,
    *,
    now: datetime | None = None,
) -> ValidationOutcome:
    """Accept or reject one proposal, with every reason."""
    if bundle.net_sha256 != network.manifest.net_sha256:
        raise ValidationInputError("the evidence bundle was built from a different network")
    program = network.manifest.signal_program
    baseline = [phase.duration_s for phase in program.phases]
    reasons: list[str] = []

    def outcome(durations: tuple[float, ...] | None = None) -> ValidationOutcome:
        accepted = not reasons and durations is not None
        return ValidationOutcome(
            intervention_id=proposal.intervention_id,
            status="accepted" if accepted else "rejected",
            reasons=tuple(reasons),
            program_id=program.program_id,
            phase_durations_s=durations if accepted else None,
            cycle_s=sum(durations) if accepted and durations else None,
            baseline_cycle_s=sum(baseline),
            validated_at_utc=now or datetime.now(UTC),
            validator_version=VALIDATOR_VERSION,
            proposal_sha256=proposal_sha256(proposal),
        )

    if proposal.type not in ALLOWED_INTERVENTIONS:
        reasons.append(
            f"type {proposal.type!r} is not in the MVP allowlist (ADR-015: signal_timing only)"
        )
        return outcome()

    if proposal.target_program != program.program_id:
        reasons.append(
            f"target {proposal.target_program!r} is not the compiled program {program.program_id!r}"
        )
    names = [change.phase_name for change in proposal.phase_changes]
    if not names:
        reasons.append("no phase changes")
    for name in sorted({n for n in names if names.count(n) > 1}):
        reasons.append(f"phase {name} is changed more than once")

    index = {phase.name: i for i, phase in enumerate(program.phases)}
    durations = list(baseline)
    for change in proposal.phase_changes:
        position = index.get(change.phase_name)
        if position is None:
            reasons.append(f"phase {change.phase_name} does not exist in {program.program_id}")
            continue
        phase = program.phases[position]
        if not phase.adjustable:
            reasons.append(
                f"phase {phase.name} is a clearance interval (yellow or all-red) and is fixed"
            )
            continue
        green = change.green_s
        if not math.isfinite(green) or green != round(green):
            reasons.append(f"phase {phase.name}: green {green} s is not whole seconds")
        elif not MIN_GREEN_S <= green <= MAX_GREEN_S:
            reasons.append(
                f"phase {phase.name}: green {green:g} s is outside "
                f"{MIN_GREEN_S:g}-{MAX_GREEN_S:g} s"
            )
        else:
            durations[position] = float(green)

    if not reasons:
        cycle = sum(durations)
        if durations == baseline:
            reasons.append("the timing is the same as the baseline")
        if not MIN_CYCLE_S <= cycle <= MAX_CYCLE_S:
            reasons.append(f"cycle {cycle:g} s is outside {MIN_CYCLE_S:g}-{MAX_CYCLE_S:g} s")
        reasons += timing_problems(program.phases, durations)

    known = {item.evidence_id for item in bundle.items}
    if not proposal.evidence_ids:
        reasons.append("no evidence is cited")
    for eid in proposal.evidence_ids:
        if eid not in known:
            reasons.append(f"cited evidence {eid!r} is not in bundle {bundle.bundle_id}")
    reasons += _priority_conflicts(network)
    return outcome(tuple(durations))


def validate_proposals(
    proposals: Sequence[InterventionProposal],
    network: CompiledNetwork,
    bundle: EvidenceBundle,
    *,
    now: datetime | None = None,
    grounding: GroundingReport | None = None,
) -> tuple[ValidationOutcome, ...]:
    """Validate each proposal; a timing that repeats an accepted one is rejected as a duplicate.

    With `grounding`, a proposal the grounding report does not make eligible (it addresses no
    supported hypothesis, or cites evidence the bundle lacks) is rejected first, with its reasons.
    """
    now = now or datetime.now(UTC)
    eligible = {g.intervention_id: g for g in grounding.proposals} if grounding else {}
    outcomes: list[ValidationOutcome] = []
    accepted: dict[tuple[float, ...], str] = {}
    for proposal in proposals:
        result = validate_proposal(proposal, network, bundle, now=now)
        gate = eligible.get(proposal.intervention_id)
        if grounding is not None and (gate is None or not gate.eligible):
            found = gate.reasons if gate else ("the grounding report has no verdict on it",)
            result = result.model_copy(update={
                "status": "rejected",
                "reasons": (*(f"not grounded: {r}" for r in found), *result.reasons),
                "phase_durations_s": None,
                "cycle_s": None,
            })  # fmt: skip
        timing = result.phase_durations_s
        if timing is not None and timing in accepted:
            result = result.model_copy(
                update={
                    "status": "rejected",
                    "reasons": (f"same timing as {accepted[timing]}",),
                    "phase_durations_s": None,
                    "cycle_s": None,
                }
            )
        elif timing is not None:
            accepted[timing] = proposal.intervention_id
        outcomes.append(result)
    return tuple(outcomes)


def unverified_assumptions(proposal: InterventionProposal) -> tuple[str, ...]:
    """The proposal's preconditions, labelled as what they are: model-stated and unchecked."""
    return tuple(f"{UNVERIFIED_PRECONDITION}: {text}" for text in proposal.preconditions)


def check_proposals(
    proposals: Sequence[InterventionProposal],
    network: CompiledNetwork,
    bundle: EvidenceBundle,
    *,
    now: datetime | None = None,
    grounding: GroundingReport | None = None,
) -> tuple[CheckedProposal, ...]:
    """`validate_proposals`, each outcome with the proposal's unverified assumptions."""
    outcomes = validate_proposals(proposals, network, bundle, now=now, grounding=grounding)
    return tuple(
        CheckedProposal(outcome=outcome, unverified_assumptions=unverified_assumptions(proposal))
        for proposal, outcome in zip(proposals, outcomes, strict=True)
    )
