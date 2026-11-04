"""The D2 move family (ADR-033 decision 8): one-step changes to the cut cycle's greens.

Approved 2026-11-02 as a rule; this module is its first part in tracked code, the menu of moves
the search may try. It runs no simulation. Definition ("d2-moves-v1", the planning prototype's):

- for every ordered pair (a, b) of the cut program's adjustable phases, a != b, and every step d
  in 2, 4, 6, 8 s: a loses d s and b gains d s, so the cycle is kept ("pair");
- for every adjustable phase a and step d: a gains d s ("increase") or loses d s ("decrease"),
  so the cycle changes by d s.
With n adjustable phases that is 4 n (n - 1) + 8 n moves, in the program's phase order.

Each move is validated as a proposal against the cut network (`validation.validate_proposal`,
citing `sig.program`) with a fixed `now`, so the outcome does not depend on the clock. Accepted
moves that give the same durations as an earlier one count once (the earlier label stands). A
move is "mapped" on a timeline member when `replay_candidates.map_onto_replay` can carry it onto
the member's replay program; it may then still be clamped at the minimum green on some cycles,
which is counted, not hidden.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from cairoflow_contracts.experiment import SignalProgramSpec
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.reasoning import (
    EvidenceBundle,
    InterventionProposal,
    PhaseChange,
    ValidationOutcome,
)
from cairoflow_simulation.candidates import CandidateError
from cairoflow_simulation.geometry_compiler import CompiledNetwork
from cairoflow_simulation.replay_candidates import (
    MIN_GREEN_S,
    changes,
    map_onto_replay,
    signature,
)
from cairoflow_simulation.validation import validate_proposal

MOVES_VERSION = "d2-moves-v1"
STEPS_S = (2, 4, 6, 8)
EVIDENCE_ID = "sig.program"
VALIDATED_AT = datetime(2026, 11, 2, tzinfo=UTC)  # fixed: outcomes must not carry the clock
MoveKind = Literal["pair", "increase", "decrease"]
Validator = Callable[[InterventionProposal, CompiledNetwork, EvidenceBundle], ValidationOutcome]


@dataclass(frozen=True)
class Move:
    label: str
    kind: MoveKind
    changes: tuple[tuple[str, float], ...]  # (phase name, new green s)

    def proposal(self, program_id: str, intervention_id: str = "I1") -> InterventionProposal:
        return InterventionProposal(
            intervention_id=intervention_id, type="signal_timing", target_program=program_id,
            phase_changes=tuple(PhaseChange(phase_name=n, green_s=g) for n, g in self.changes),
            rationale=f"D2 move {self.label} ({MOVES_VERSION})", expected_tradeoffs=(),
            preconditions=(), addresses_hypothesis_ids=(), evidence_ids=(EVIDENCE_ID,),
        )  # fmt: skip


def generate_moves(program: SignalProgramSpec) -> tuple[Move, ...]:
    """Every move of the family on `program`, in a fixed order (see the module)."""
    adjustable = [p for p in program.phases if p.adjustable]
    moves: list[Move] = []
    for a in adjustable:
        for b in adjustable:
            if a.name != b.name:
                for d in STEPS_S:
                    moves.append(
                        Move(f"{a.name}->{b.name}:{d}", "pair",
                             ((a.name, a.duration_s - d), (b.name, b.duration_s + d)))
                    )  # fmt: skip
        for d in STEPS_S:
            moves.append(Move(f"{a.name}+{d}", "increase", ((a.name, a.duration_s + d),)))
            moves.append(Move(f"{a.name}-{d}", "decrease", ((a.name, a.duration_s - d),)))
    return tuple(moves)


def _validate(
    proposal: InterventionProposal, network: CompiledNetwork, bundle: EvidenceBundle
) -> ValidationOutcome:
    return validate_proposal(proposal, network, bundle, now=VALIDATED_AT)


@dataclass(frozen=True)
class AcceptedMove:
    move: Move
    outcome: ValidationOutcome

    @property
    def durations(self) -> tuple[float, ...]:
        assert self.outcome.phase_durations_s is not None
        return self.outcome.phase_durations_s


def kind_counts(moves: Sequence[Move]) -> dict[str, int]:
    found = Counter(m.kind for m in moves)
    return {kind: found.get(kind, 0) for kind in ("pair", "increase", "decrease")}


@dataclass(frozen=True)
class MoveScreen:
    generated: tuple[Move, ...]
    accepted: tuple[AcceptedMove, ...]  # distinct durations, first label wins
    duplicates: tuple[tuple[str, str], ...]  # (label, the earlier label with the same durations)
    rejected: tuple[tuple[str, int], ...]  # (reason, number of moves it rejected), by reason


def screen_moves(
    network: CompiledNetwork, bundle: EvidenceBundle, validator: Validator = _validate
) -> MoveScreen:
    """Validate every move of the family against the cut network."""
    program = network.manifest.signal_program
    moves = generate_moves(program)
    accepted: list[AcceptedMove] = []
    first: dict[tuple[float, ...], str] = {}
    duplicates: list[tuple[str, str]] = []
    reasons: Counter[str] = Counter()
    for move in moves:
        outcome = validator(move.proposal(program.program_id), network, bundle)
        if outcome.status != "accepted":
            reasons.update(set(outcome.reasons))
            continue
        durations = outcome.phase_durations_s
        assert durations is not None
        if durations in first:
            duplicates.append((move.label, first[durations]))
            continue
        first[durations] = move.label
        accepted.append(AcceptedMove(move, outcome))
    return MoveScreen(
        generated=moves,
        accepted=tuple(accepted),
        duplicates=tuple(duplicates),
        rejected=tuple(sorted(reasons.items())),
    )


@dataclass(frozen=True)
class MemberFit:
    member: str
    mapped: tuple[str, ...]  # labels the member's replay can carry
    clamped: tuple[str, ...]  # of those, labels stopped at the minimum green somewhere
    unmapped: tuple[str, ...]


def _clamped(outcome: ValidationOutcome, cut: SignalProgramSpec, replay: SignalProgramSpec) -> bool:
    """True when map_onto_replay stops some replay phase at the minimum green (its own rule)."""
    delta = changes(outcome, cut)
    return any(
        p.adjustable
        and signature(p) in delta
        and p.duration_s >= MIN_GREEN_S
        and p.duration_s + delta[signature(p)] < MIN_GREEN_S
        for p in replay.phases
    )


def fit_members(
    screen: MoveScreen, cut: SignalProgramSpec, replays: Mapping[str, SignalProgramSpec]
) -> tuple[tuple[MemberFit, ...], tuple[AcceptedMove, ...]]:
    """Each member's fit, and the accepted moves every member can carry."""
    fits = []
    everywhere = {m.move.label for m in screen.accepted}
    for member in sorted(replays):
        mapped, clamped, unmapped = [], [], []
        for accepted in screen.accepted:
            try:
                map_onto_replay(accepted.outcome, cut, replays[member])
            except CandidateError:
                unmapped.append(accepted.move.label)
                continue
            mapped.append(accepted.move.label)
            if _clamped(accepted.outcome, cut, replays[member]):
                clamped.append(accepted.move.label)
        everywhere &= set(mapped)
        fits.append(MemberFit(member, tuple(mapped), tuple(clamped), tuple(unmapped)))
    common = tuple(m for m in screen.accepted if m.move.label in everywhere)
    return tuple(fits), common


def menu_sha256(moves: Sequence[AcceptedMove]) -> str:
    """Hash of a menu: each move's label and the durations it validates to, in menu order."""
    return canonical_sha256([[m.move.label, list(m.durations)] for m in moves])
