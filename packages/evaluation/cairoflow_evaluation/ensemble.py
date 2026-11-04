"""Cross-timeline verdicts over the intersection 2 signal-timeline ensemble (ADR-025).

The observed junction has no signal log, so its timeline is estimated, and the estimate has
choices the evidence does not settle. The ensemble (`timeline_ensemble.json`) replays the same
observed vehicles under several defensible timelines (members). Each member is an ordinary replay
experiment, evaluated on its own under the experiment rules; this module only combines the
members' verdicts. It never pools, averages or weights values across members: a candidate's
effect is judged member by member.

The rule, pre-registered in `timeline_ensemble.json`:

- A candidate is *supported* on a member when all three hold:
  1. it is the same plan there: each phase it changes (a cut-cycle phase, mapped onto the replay
     by phase identity, `replay_candidates`) applies to at least one replayed phase;
  2. every one of its comparisons has exactly the member's planned paired seeds, and it has a
     comparison for every metric the baseline measured;
  3. it is eligible under `report.candidate_standings`: compared (every seed complete), no metric
     worsens (uncorrected paired 95% interval), no more vehicles held off the network at any
     entry than the baseline, and at least one improvement confirmed by Holm's correction over
     that member's planned family.
- `robust_improvement`: supported on every required member. The improvements it claims are those
  confirmed on every required member; if they have none in common, that is said.
- `timeline_dependent`: supported on some required members but not on all.
- `no_support`: supported on no required member.
- Robust harm: a metric that worsens on every required member.
- Direction disagreements: metrics whose verdict differs between members, flagged `opposite`
  when one member says it improves and another that it worsens.

Holm's family stays per member, as each member's experiment fixed it. Requiring a confirmed
improvement on every required member is an intersection-union test: the probability of a false
robust claim is no larger than one member's family-wise 5%, with no further correction.
Among robust candidates, the selection prefers the most improvements confirmed on its worst
member, then the smallest worst-member mean-delay difference, then the lower intervention number.
A robust selection is a recommendation only when every required member's baseline is assessed as
validated. All values are `simulated`; the timelines behind them are `trajectory-derived`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.experiment import EvaluationResult
from cairoflow_evaluation.report import CandidateStanding, candidate_standings, id_order

ENSEMBLE_RULE_VERSION = "0.1.0"
ENSEMBLE_RULE = (
    "Each member is evaluated on its own under the experiment rules; nothing is pooled, averaged "
    "or weighted across members. A candidate is supported on a member when its every phase change "
    "applies to at least one replayed phase (the same plan), every comparison has the member's "
    "planned paired seeds, and it is eligible there (compared, no metric worsens, no more vehicles "
    "held off at an entry than the baseline, at least one Holm-confirmed improvement). It is a "
    "robust improvement only when supported on every required member; timeline-dependent when "
    "supported on some but not all; without support when supported on none. A metric that "
    "worsens on every required member is robust harm."
)
_NOTE = re.compile(
    r"^(?P<candidate>\S+) on the replay: (?P<phases>.+): (?P<change>[+-]\S+) s applied to "
    r"(?P<applied>\d+) phases \((?P<clamped>\d+) stopped at the \S+ s minimum green; "
    r"(?P<shorter>\d+) already shorter, unchanged\)$"
)
Verdict = Literal["robust_improvement", "timeline_dependent", "no_support"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PhaseChange(_Model):
    """One phase change of a candidate as it applied to a member's replay program."""

    phases: str = Field(description="the head states that identify the changed phase")
    change_s: float = Field(description="green change validated on the cut cycle (s)")
    applied: int = Field(ge=0, description="replayed phases it was applied to")
    clamped: int = Field(ge=0, description="of those, stopped at the minimum green")
    already_shorter: int = Field(ge=0, description="phases shorter than the minimum, unchanged")


class MemberEvidence(_Model):
    """What one member contributes: its evaluation, as its experiment wrote it, and context."""

    member: str
    required: bool
    planned_seeds: tuple[int, ...]
    evaluation: EvaluationResult
    phase_changes: dict[str, tuple[PhaseChange, ...]] = Field(
        description="candidate -> its phase changes as mapped onto this member's replay"
    )
    baseline_assessment: str = Field(
        description="validated, partially_validated, not_validated or not_assessed"
    )


class MemberStanding(_Model):
    """One candidate on one member."""

    member: str
    required: bool
    supported: bool
    blocked_by: tuple[str, ...] = Field(description="why it is not supported here; empty if it is")
    status: str
    worsens: tuple[str, ...]
    confirmed: tuple[str, ...] = Field(description="Holm-confirmed improvements on this member")
    held_off: tuple[str, ...]
    mean_delay_difference_s: float | None
    mean_delay_ci95_s: tuple[float, float] | None
    phase_changes: tuple[PhaseChange, ...]


class MetricAcrossMembers(_Model):
    """One metric of one candidate whose verdict is not the same on every member."""

    metric_name: str
    verdicts: dict[str, str] = Field(description="member -> verdict; 'not compared' if absent")
    mean_difference: dict[str, float | None]
    ci95_difference: dict[str, tuple[float, float] | None]
    opposite: bool = Field(description="one member says improves and another worsens")


class CandidateVerdict(_Model):
    candidate: str
    verdict: Verdict
    reason: str
    supported_on: tuple[str, ...]
    not_supported_on: tuple[str, ...]
    common_confirmed: tuple[str, ...] = Field(
        description="improvements confirmed on every required member"
    )
    robust_harm: tuple[str, ...] = Field(description="metrics that worsen on every required member")
    direction_disagreements: tuple[MetricAcrossMembers, ...]
    members: tuple[MemberStanding, ...]


class EnsembleVerdict(_Model):
    rule_version: str
    rule: str
    members: tuple[str, ...]
    required_members: tuple[str, ...]
    planned_seeds: tuple[int, ...]
    candidates: tuple[CandidateVerdict, ...]
    robust_selection: str | None
    selection_reason: str
    recommended: str | None
    recommendation: str
    label: Literal["simulated"] = "simulated"


def phase_changes_from_notes(notes: Sequence[str]) -> dict[str, tuple[PhaseChange, ...]]:
    """Candidate -> phase changes, from the 'on the replay' notes `replay_candidates` writes into
    each candidate network's manifest. Other notes are ignored."""
    found: dict[str, list[PhaseChange]] = {}
    for note in notes:
        if " on the replay: " not in note:
            continue
        match = _NOTE.match(note)
        if match is None:
            raise ValueError(f"unreadable replay note: {note!r}")
        found.setdefault(match["candidate"], []).append(
            PhaseChange(
                phases=match["phases"],
                change_s=float(match["change"]),
                applied=int(match["applied"]),
                clamped=int(match["clamped"]),
                already_shorter=int(match["shorter"]),
            )
        )
    return {candidate: tuple(changes) for candidate, changes in found.items()}


def member_standing(
    candidate: str, evidence: MemberEvidence, standings: Mapping[str, CandidateStanding]
) -> MemberStanding:
    """The candidate on one member, under the rule's three conditions."""
    evaluation = evidence.evaluation
    standing = standings.get(candidate)
    comparisons = [c for c in evaluation.comparisons if c.candidate == candidate]
    changes = evidence.phase_changes.get(candidate)
    blocked: list[str] = []
    if changes is None:
        blocked.append("its phase changes on this member are not recorded")
    else:
        blocked += [
            f"not the same plan: its change to {c.phases} ({c.change_s:+g} s) applies to no "
            "replayed phase"
            for c in changes
            if c.applied == 0
        ]
    measured = sorted({m.metric_name for m in evaluation.metrics if m.arm == "baseline"})
    compared = {c.metric_name for c in comparisons}
    if standing is None:
        blocked.append("not simulated on this member")
    elif standing.status == "compared":
        blocked += [f"no comparison of {name}" for name in measured if name not in compared]
    blocked += [
        f"{c.metric_name}: paired seeds {list(c.seeds)}, planned {list(evidence.planned_seeds)}"
        for c in comparisons
        if tuple(c.seeds) != tuple(evidence.planned_seeds)
    ]
    if standing is not None and not standing.eligible:
        blocked.append(standing.note)
    delay = next((c for c in comparisons if c.metric_name == "mean_delay_s"), None)
    return MemberStanding(
        member=evidence.member,
        required=evidence.required,
        supported=not blocked,
        blocked_by=tuple(blocked),
        status=standing.status if standing else "not_simulated",
        worsens=standing.worsens if standing else (),
        confirmed=standing.confirmed if standing else (),
        held_off=standing.held_off if standing else (),
        mean_delay_difference_s=delay.mean_difference if delay else None,
        mean_delay_ci95_s=delay.ci95_difference if delay else None,
        phase_changes=changes or (),
    )


def direction_disagreements(
    candidate: str, evidences: Sequence[MemberEvidence]
) -> tuple[MetricAcrossMembers, ...]:
    """The candidate's metrics whose verdict is not the same on every member."""
    by_member = {
        e.member: {c.metric_name: c for c in e.evaluation.comparisons if c.candidate == candidate}
        for e in evidences
    }
    names = sorted({name for found in by_member.values() for name in found})
    out = []
    for name in names:
        verdicts = {
            member: found[name].verdict if name in found else "not compared"
            for member, found in by_member.items()
        }
        if len(set(verdicts.values())) < 2:
            continue
        out.append(
            MetricAcrossMembers(
                metric_name=name,
                verdicts=verdicts,
                mean_difference={
                    m: found[name].mean_difference if name in found else None
                    for m, found in by_member.items()
                },
                ci95_difference={
                    m: found[name].ci95_difference if name in found else None
                    for m, found in by_member.items()
                },
                opposite={"improves", "worsens"} <= set(verdicts.values()),
            )
        )
    return tuple(out)


def _candidates(evidences: Sequence[MemberEvidence]) -> list[str]:
    found: set[str] = set()
    for e in evidences:
        found |= {x.candidate for x in e.evaluation.eligibility}
        found |= {c.candidate for c in e.evaluation.comparisons}
        found |= set(e.phase_changes)
    return sorted(found, key=id_order)


def candidate_verdict(
    candidate: str,
    evidences: Sequence[MemberEvidence],
    standings: Mapping[str, Mapping[str, CandidateStanding]],
) -> CandidateVerdict:
    """One candidate across the members (see the module notes)."""
    members = tuple(member_standing(candidate, e, standings[e.member]) for e in evidences)
    required = [m for m in members if m.required]
    supported = tuple(m.member for m in required if m.supported)
    missing = tuple(m.member for m in required if not m.supported)
    common = tuple(
        name for name in required[0].confirmed if all(name in m.confirmed for m in required[1:])
    )
    harm = tuple(
        name for name in required[0].worsens if all(name in m.worsens for m in required[1:])
    )
    why = "; ".join(f"on {m.member}: {', '.join(m.blocked_by)}" for m in required if m.blocked_by)
    if not missing:
        verdict: Verdict = "robust_improvement"
        reason = f"{candidate} is eligible on every required member ({', '.join(supported)}); " + (
            f"improvement confirmed on every one: {', '.join(common)}."
            if common
            else "no single metric's improvement is confirmed on every one."
        )
    elif supported:
        verdict = "timeline_dependent"
        reason = (
            f"{candidate} is eligible on {', '.join(supported)} but not on {', '.join(missing)}: "
            f"{why}."
        )
    else:
        verdict = "no_support"
        reason = f"{candidate} is eligible on no required member: {why}."
    return CandidateVerdict(
        candidate=candidate,
        verdict=verdict,
        reason=reason,
        supported_on=supported,
        not_supported_on=missing,
        common_confirmed=common,
        robust_harm=harm,
        direction_disagreements=direction_disagreements(candidate, evidences),
        members=members,
    )


def ensemble_verdict(evidences: Sequence[MemberEvidence]) -> EnsembleVerdict:
    """Every candidate's cross-timeline verdict, the robust selection and its recommendation."""
    names = [e.member for e in evidences]
    if len(set(names)) != len(names):
        raise ValueError("member names must be unique")
    required = [e for e in evidences if e.required]
    if not required:
        raise ValueError("the ensemble has no required member")
    planned = {e.planned_seeds for e in evidences}
    if len(planned) != 1 or not next(iter(planned)):
        raise ValueError("every member must plan the same, non-empty seeds")
    standings = {e.member: candidate_standings(e.evaluation) for e in evidences}
    verdicts = tuple(candidate_verdict(c, evidences, standings) for c in _candidates(evidences))
    robust = [v for v in verdicts if v.verdict == "robust_improvement"]
    selection, selection_reason = None, "No robust selection: no candidate is a robust improvement."
    if robust:

        def worst(v: CandidateVerdict) -> tuple[int, float, tuple[str, int, str]]:
            req = [m for m in v.members if m.required]
            return (
                -min(len(m.confirmed) for m in req),
                max(m.mean_delay_difference_s or 0.0 for m in req),
                id_order(v.candidate),
            )

        selection = min(robust, key=worst).candidate
        selection_reason = (
            f"{selection} is a robust improvement and ranks first on its worst member (most "
            "confirmed improvements, then the smallest mean-delay difference)."
        )
    unvalidated = [e.member for e in required if e.baseline_assessment != "validated"]
    if selection is None:
        recommended, recommendation = None, "Nothing is recommended: no robust selection."
    elif unvalidated:
        recommended = None
        recommendation = (
            f"Nothing is recommended: {selection} is a robust simulated result, but the baselines "
            f"of {', '.join(unvalidated)} are not assessed as validated."
        )
    else:
        recommended = selection
        recommendation = f"{selection} is recommended: robust under validated baselines."
    return EnsembleVerdict(
        rule_version=ENSEMBLE_RULE_VERSION,
        rule=ENSEMBLE_RULE,
        members=tuple(names),
        required_members=tuple(e.member for e in required),
        planned_seeds=next(iter(planned)),
        candidates=verdicts,
        robust_selection=selection,
        selection_reason=selection_reason,
        recommended=recommended,
        recommendation=recommendation,
    )
