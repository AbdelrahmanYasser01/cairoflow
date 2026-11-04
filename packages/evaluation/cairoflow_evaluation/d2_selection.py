"""D2 scoring and selection over the move menu (ADR-033 decision 8, approved 2026-11-02).

Input: the menu (cairoflow_simulation.d2_moves, moves mapped on every member), the cut program it
was validated on, and for each timeline member the `report.candidate_standings` of an experiment
that ran every menu move on the search seeds, keyed by move label. Nothing here simulates or
computes a statistic: the delay difference, the worsening verdicts and the entry holding are the
standings' own (rules of `compare` and `report`).

Rule:
- score of a move = the largest, over members, of its mean delay difference (candidate minus
  baseline, s; below zero is better), and the member where it is largest;
- admissible = on every member the standing is `compared`, worsens nothing (uncorrected) and
  holds no entry, and the score is below zero;
- among admissible moves the lowest score wins; ties by fewer changed phases, then smaller L1
  change (s) from the cut program, then the lexicographically smaller duration vector;
- none admissible: `no_feasible_plan`, reported `not_testable`. The baseline is never returned
  in place of a plan.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from cairoflow_contracts.experiment import SignalProgramSpec
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.pairing import plan_sha256
from cairoflow_evaluation.report import CandidateStanding
from cairoflow_simulation.d2_moves import AcceptedMove, menu_sha256

D2_RULE_VERSION = "d2-rule-v1"
SOURCE = "ADR-033 decision 8 (approved 2026-11-02)"


class D2Error(ValueError):
    """The inputs do not define a D2 selection."""


def change_of(cut: SignalProgramSpec, durations: Sequence[float]) -> tuple[int, float]:
    """(number of phases changed, L1 change in seconds) of `durations` from the cut program."""
    base = [p.duration_s for p in cut.phases]
    if len(durations) != len(base):
        raise D2Error(f"{len(durations)} durations for a {len(base)}-phase program")
    changed = sum(a != b for a, b in zip(base, durations, strict=True))
    return changed, float(sum(abs(a - b) for a, b in zip(base, durations, strict=True)))


def program_with(cut: SignalProgramSpec, durations: Sequence[float]) -> SignalProgramSpec:
    phases = tuple(
        p.model_copy(update={"duration_s": float(d)})
        for p, d in zip(cut.phases, durations, strict=True)
    )
    return cut.model_copy(update={"phases": phases, "cycle_s": float(sum(durations))})


@dataclass(frozen=True)
class MoveScore:
    label: str
    durations: tuple[float, ...]
    per_member: tuple[tuple[str, CandidateStanding], ...]  # member order
    score: float
    worst_member: str
    changed_phases: int
    l1_change_s: float
    admissible: bool
    reasons: tuple[str, ...]  # why not admissible; empty when admissible

    @property
    def order(self) -> tuple:
        return (self.score, self.changed_phases, self.l1_change_s, self.durations)


@dataclass(frozen=True)
class D2Selection:
    outcome: Literal["selected", "no_feasible_plan"]
    reported: Literal["selected", "not_testable"]
    members: tuple[str, ...]
    scores: tuple[MoveScore, ...]  # menu order
    selected: MoveScore | None
    plan_sha256: str | None


def _check(
    menu: Sequence[AcceptedMove],
    cut: SignalProgramSpec,
    standings: Mapping[str, Mapping[str, CandidateStanding]],
    expected_members: Sequence[str] | None,
) -> tuple[str, ...]:
    if not menu:
        raise D2Error("the menu is empty")
    labels = [a.move.label for a in menu]
    if len(set(labels)) != len(labels):
        raise D2Error("a move appears twice in the menu")
    for accepted in menu:
        if accepted.outcome.program_id != cut.program_id:
            raise D2Error(f"{accepted.move.label} was validated on another program")
    if not standings:
        raise D2Error("no member standings")
    members = tuple(sorted(standings))
    if expected_members is not None and members != tuple(sorted(expected_members)):
        raise D2Error(
            f"standings for members {members}, expected {tuple(sorted(expected_members))}"
        )
    for member in members:
        known = set(standings[member])
        for label in labels:
            if label not in known:
                raise D2Error(f"member {member} has no standing for {label}")
        extra = sorted(known - set(labels))
        if extra:
            raise D2Error(f"member {member} has standings not in the menu: {', '.join(extra)}")
        for label in labels:
            if not math.isfinite(standings[member][label].delay_difference):
                raise D2Error(f"member {member}, {label}: the delay difference is not finite")
    return members


def score_move(
    accepted: AcceptedMove,
    cut: SignalProgramSpec,
    members: Sequence[str],
    standings: Mapping[str, Mapping[str, CandidateStanding]],
) -> MoveScore:
    label = accepted.move.label
    rows = tuple((m, standings[m][label]) for m in members)
    worst_member, worst = max(
        rows, key=lambda row: (row[1].delay_difference, -members.index(row[0]))
    )
    reasons = []
    for member, row in rows:
        if row.status != "compared":
            reasons.append(f"{member}: not compared ({row.status})")
        if row.worsens:
            reasons.append(f"{member}: worsens {', '.join(row.worsens)}")
        if row.held_off:
            reasons.append(f"{member}: held vehicles off at the entry of {', '.join(row.held_off)}")
    score = worst.delay_difference
    if score >= 0:
        reasons.append(f"score {score:g} s (worst member {worst_member}) is not below zero")
    changed, l1 = change_of(cut, accepted.durations)
    return MoveScore(
        label=label,
        durations=tuple(float(d) for d in accepted.durations),
        per_member=rows,
        score=score,
        worst_member=worst_member,
        changed_phases=changed,
        l1_change_s=l1,
        admissible=not reasons,
        reasons=tuple(reasons),
    )


def select_plan(
    menu: Sequence[AcceptedMove],
    cut: SignalProgramSpec,
    standings: Mapping[str, Mapping[str, CandidateStanding]],
    expected_members: Sequence[str] | None = None,
) -> D2Selection:
    """Apply the rule (see the module) to the members' standings of every menu move.
    `expected_members`, when given, must be exactly the members the standings cover."""
    members = _check(menu, cut, standings, expected_members)
    scores = tuple(score_move(a, cut, members, standings) for a in menu)
    admissible = [s for s in scores if s.admissible]
    if not admissible:
        return D2Selection("no_feasible_plan", "not_testable", members, scores, None, None)
    chosen = min(admissible, key=lambda s: s.order)
    digest = plan_sha256(program_with(cut, chosen.durations))
    return D2Selection("selected", "selected", members, scores, chosen, digest)


def _standing_json(row: CandidateStanding) -> dict[str, Any]:
    return {
        "candidate": row.candidate,
        "status": row.status,
        "delay_difference_s": row.delay_difference,
        "worsens": list(row.worsens),
        "held_off": list(row.held_off),
        "confirmed": list(row.confirmed),
    }


def selection_record(
    selection: D2Selection, menu: Sequence[AcceptedMove], cut: SignalProgramSpec
) -> dict[str, Any]:
    """The selection as a deterministic record (no clock), hashed by `record_sha256`."""
    chosen = selection.selected
    body: dict[str, Any] = {
        "rule": D2_RULE_VERSION,
        "source": SOURCE,
        "cut_program_id": cut.program_id,
        "cut_durations_s": [p.duration_s for p in cut.phases],
        "menu_sha256": menu_sha256(menu),
        "members": list(selection.members),
        "outcome": selection.outcome,
        "reported": selection.reported,
        "moves": [
            {
                "label": s.label,
                "durations_s": list(s.durations),
                "score_s": s.score,
                "worst_member": s.worst_member,
                "changed_phases": s.changed_phases,
                "l1_change_s": s.l1_change_s,
                "admissible": s.admissible,
                "reasons": list(s.reasons),
                "members": {m: _standing_json(row) for m, row in s.per_member},
            }
            for s in selection.scores
        ],
        "selected": None
        if chosen is None
        else {
            "label": chosen.label,
            "durations_s": list(chosen.durations),
            "score_s": chosen.score,
            "worst_member": chosen.worst_member,
            "plan_sha256": selection.plan_sha256,
        },
    }
    return body | {"record_sha256": canonical_sha256(body)}
