"""D2 scoring and selection (ADR-033 decision 8), on SYNTHETIC standings only.

Nothing here is SUMO evidence: the standings are made up to exercise the rule. Rule: per move,
score = the largest over members of the mean delay difference (candidate minus baseline, s);
admissible if on every member the standing is `compared`, with no uncorrected worsening and no
entry holding, and the score is below zero; choose the lowest score, ties by fewer changed
phases, smaller L1 change, then lexicographic durations; none admissible = no_feasible_plan,
reported not_testable, never a no-op.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest

from cairoflow_contracts.experiment import SignalProgramSpec
from cairoflow_contracts.pairing import plan_sha256
from cairoflow_contracts.reasoning import ValidationOutcome
from cairoflow_evaluation import d2_selection as d2
from cairoflow_evaluation.report import CandidateStanding
from cairoflow_simulation.d2_moves import VALIDATED_AT, AcceptedMove, generate_moves

REPO = Path(__file__).resolve().parents[1]
CUT = SignalProgramSpec.model_validate_json(
    (REPO / "tests/fixtures/eq1_cut_program.json").read_text(encoding="utf-8")
)
MEMBERS = ("T1", "T2", "T3", "T4")


def _accepted(move) -> AcceptedMove | None:
    durations = [p.duration_s for p in CUT.phases]
    names = [p.name for p in CUT.phases]
    for name, green in move.changes:
        durations[names.index(name)] = green
    if min(durations[names.index(n)] for n, _ in move.changes) < 5:
        return None
    outcome = ValidationOutcome(
        intervention_id="I1", status="accepted", reasons=(), program_id=CUT.program_id,
        phase_durations_s=tuple(durations), cycle_s=sum(durations),
        baseline_cycle_s=CUT.cycle_s, validated_at_utc=VALIDATED_AT,
    )  # fmt: skip
    return AcceptedMove(move, outcome)


MENU = tuple(m for m in (_accepted(x) for x in generate_moves(CUT)) if m is not None)[:80]


def standing(label: str, delay: float, status: str = "compared", worsens=(), held=()):
    return CandidateStanding(
        candidate=label, status=status, worsens=tuple(worsens), confirmed=(),
        held_off=tuple(held), delay_difference=delay, note="",
    )  # fmt: skip


def all_standings(delays: dict[str, dict[str, float]]) -> dict[str, dict[str, CandidateStanding]]:
    return {m: {label: standing(label, d) for label, d in row.items()} for m, row in delays.items()}


def random_delays(seed: int, menu=MENU) -> dict[str, dict[str, float]]:
    rng = random.Random(seed)
    return {m: {a.move.label: round(rng.uniform(-6, 3), 3) for a in menu} for m in MEMBERS}


def test_the_synthetic_menu_has_80_moves_on_the_real_cut_program() -> None:
    assert len(MENU) == 80 and len({a.move.label for a in MENU}) == 80


def test_selection_over_all_80_moves_and_four_members_follows_the_rule() -> None:
    for seed in range(5):
        delays = random_delays(seed)
        selection = d2.select_plan(MENU, CUT, all_standings(delays))
        expected = None
        for accepted in MENU:
            label = accepted.move.label
            score = max(delays[m][label] for m in MEMBERS)
            if score < 0:
                changed, l1 = d2.change_of(CUT, accepted.durations)
                key = (score, changed, l1, accepted.durations)
                expected = min(expected, (key, label)) if expected else (key, label)
        assert selection.outcome == "selected"
        assert selection.selected.label == expected[1]
        assert selection.selected.score == expected[0][0]
        assert len(selection.scores) == 80


def test_the_score_is_the_worst_member_not_the_mean() -> None:
    a, b = MENU[0].move.label, MENU[1].move.label
    delays = {m: {x.move.label: 1.0 for x in MENU} for m in MEMBERS}
    # a: mean -2.5 but worst -1.0; b: mean -2.0 but worst -2.0 -> b wins on the worst member
    for m, (da, db) in zip(MEMBERS, ((-1.0, -2.0), (-3.0, -2.0), (-3.0, -2.0), (-3.0, -2.0)),
                           strict=True):  # fmt: skip
        delays[m][a], delays[m][b] = da, db
    selection = d2.select_plan(MENU, CUT, all_standings(delays))
    assert selection.selected.label == b
    scores = {s.label: s for s in selection.scores}
    assert scores[a].worst_member == "T1" and scores[a].score == -1.0


def test_the_worst_member_can_change_from_move_to_move() -> None:
    a, b = MENU[0].move.label, MENU[1].move.label
    delays = {m: {x.move.label: 1.0 for x in MENU} for m in MEMBERS}
    delays["T1"] |= {a: -5.0, b: -0.5}
    delays["T2"] |= {a: -4.0, b: -5.0}
    delays["T3"] |= {a: -3.0, b: -5.0}
    delays["T4"] |= {a: -0.2, b: -5.0}
    scores = {s.label: s for s in d2.select_plan(MENU, CUT, all_standings(delays)).scores}
    assert (scores[a].worst_member, scores[a].score) == ("T4", -0.2)
    assert (scores[b].worst_member, scores[b].score) == ("T1", -0.5)


@pytest.mark.parametrize(
    "spoil, reason",
    [
        ({"status": "incomplete"}, "not compared"),
        ({"worsens": ("mean_queue_m.N",)}, "worsens"),
        ({"held": ("E",)}, "held"),
    ],
)
def test_one_member_failing_the_admissibility_test_excludes_the_move(spoil, reason) -> None:
    target = MENU[5].move.label
    delays = {m: {x.move.label: 1.0 for x in MENU} for m in MEMBERS}
    for m in MEMBERS:
        delays[m][target] = -3.0
    standings = all_standings(delays)
    standings["T3"][target] = standing(target, -3.0, **spoil)
    selection = d2.select_plan(MENU, CUT, standings)
    assert selection.outcome == "no_feasible_plan"
    scored = {s.label: s for s in selection.scores}[target]
    assert not scored.admissible and any(reason in r and "T3" in r for r in scored.reasons)


def test_a_score_of_zero_or_more_is_not_admissible() -> None:
    delays = {m: {x.move.label: 0.0 for x in MENU} for m in MEMBERS}
    selection = d2.select_plan(MENU, CUT, all_standings(delays))
    assert selection.outcome == "no_feasible_plan"
    assert all("not below zero" in " ".join(s.reasons) for s in selection.scores)


def test_no_feasible_plan_is_not_testable_and_never_a_no_op() -> None:
    selection = d2.select_plan(MENU, CUT, all_standings(random_delays(1) | {
        m: {x.move.label: 2.0 for x in MENU} for m in MEMBERS}))  # fmt: skip
    assert selection.outcome == "no_feasible_plan"
    assert selection.reported == "not_testable"
    assert selection.selected is None and selection.plan_sha256 is None
    record = d2.selection_record(selection, MENU, CUT)
    assert record["selected"] is None and record["reported"] == "not_testable"


def _by_label(prefix_changes: int, l1: float | None = None) -> list[AcceptedMove]:
    return [
        a for a in MENU
        if d2.change_of(CUT, a.durations)[0] == prefix_changes
        and (l1 is None or d2.change_of(CUT, a.durations)[1] == l1)
    ]  # fmt: skip


def test_ties_go_to_fewer_changed_phases_then_smaller_l1_then_lexicographic() -> None:
    # fewer changed phases wins even with the larger L1: a single 8 s move beats a 2 s pair move
    pair, single = _by_label(2, 4.0)[0], _by_label(1, 8.0)[0]
    delays = {m: {x.move.label: 1.0 for x in MENU} for m in MEMBERS}
    for m in MEMBERS:
        delays[m][pair.move.label] = delays[m][single.move.label] = -2.0
    assert d2.select_plan(MENU, CUT, all_standings(delays)).selected.label == single.move.label
    small, large = _by_label(1, 2.0)[0], _by_label(1, 4.0)[0]
    delays = {m: {x.move.label: 1.0 for x in MENU} for m in MEMBERS}
    for m in MEMBERS:
        delays[m][small.move.label] = delays[m][large.move.label] = -2.0
    assert d2.select_plan(MENU, CUT, all_standings(delays)).selected.label == small.move.label
    same = _by_label(1, 2.0)[:2]
    delays = {m: {x.move.label: 1.0 for x in MENU} for m in MEMBERS}
    for m in MEMBERS:
        for a in same:
            delays[m][a.move.label] = -2.0
    winner = min(same, key=lambda a: a.durations)
    assert d2.select_plan(MENU, CUT, all_standings(delays)).selected.label == winner.move.label


def test_invalid_inputs_are_refused() -> None:
    standings = all_standings(random_delays(2))
    with pytest.raises(d2.D2Error, match="empty"):
        d2.select_plan((), CUT, standings)
    with pytest.raises(d2.D2Error, match="twice"):
        d2.select_plan((*MENU, MENU[0]), CUT, standings)
    with pytest.raises(d2.D2Error, match="no member"):
        d2.select_plan(MENU, CUT, {})
    three = {m: row for m, row in standings.items() if m != "T4"}
    with pytest.raises(d2.D2Error, match="expected"):
        d2.select_plan(MENU, CUT, three, expected_members=MEMBERS)
    assert d2.select_plan(MENU, CUT, standings, expected_members=MEMBERS).members == MEMBERS
    missing = {m: dict(row) for m, row in standings.items()}
    del missing["T2"][MENU[3].move.label]
    with pytest.raises(d2.D2Error, match="T2 has no standing"):
        d2.select_plan(MENU, CUT, missing)
    extra = {m: dict(row) for m, row in standings.items()}
    extra["T1"]["I99"] = standing("I99", -9.0)
    with pytest.raises(d2.D2Error, match="not in the menu"):
        d2.select_plan(MENU, CUT, extra)
    broken = {m: dict(row) for m, row in standings.items()}
    broken["T4"][MENU[0].move.label] = standing(MENU[0].move.label, math.nan)
    with pytest.raises(d2.D2Error, match="finite"):
        d2.select_plan(MENU, CUT, broken)
    other = CUT.model_copy(update={"program_id": "other"})
    with pytest.raises(d2.D2Error, match="program"):
        d2.select_plan(MENU, other, standings)


def test_the_selection_and_its_record_are_deterministic() -> None:
    delays = random_delays(3)
    first = d2.select_plan(MENU, CUT, all_standings(delays))
    reordered = {
        m: dict(reversed(list(all_standings(delays)[m].items()))) for m in reversed(MEMBERS)
    }
    again = d2.select_plan(tuple(MENU), CUT, reordered)
    assert again == first
    a = json.dumps(d2.selection_record(first, MENU, CUT), sort_keys=True)
    b = json.dumps(d2.selection_record(again, MENU, CUT), sort_keys=True)
    assert a == b


def test_the_record_names_the_rule_menu_plan_and_hashes_itself() -> None:
    selection = d2.select_plan(MENU, CUT, all_standings(random_delays(4)))
    record = d2.selection_record(selection, MENU, CUT)
    assert record["rule"] == d2.D2_RULE_VERSION
    assert record["members"] == list(MEMBERS)
    assert len(record["moves"]) == 80
    chosen = selection.selected
    program = CUT.model_copy(update={"phases": tuple(
        p.model_copy(update={"duration_s": d}) for p, d in zip(CUT.phases, chosen.durations,
                                                               strict=True))})  # fmt: skip
    assert record["selected"]["plan_sha256"] == plan_sha256(program) == selection.plan_sha256
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    from cairoflow_contracts.integrity import canonical_sha256

    assert record["record_sha256"] == canonical_sha256(body)
