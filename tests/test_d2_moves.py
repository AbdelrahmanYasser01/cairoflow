"""The D2 move family (ADR-033 decision 8): generation, screening, member fit, recorded counts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from cairoflow_contracts.experiment import SignalPhaseSpec, SignalProgramSpec
from cairoflow_contracts.reasoning import ValidationOutcome
from cairoflow_simulation.d2_moves import (
    VALIDATED_AT,
    AcceptedMove,
    Move,
    MoveScreen,
    fit_members,
    generate_moves,
    kind_counts,
    menu_sha256,
    screen_moves,
)

REPO = Path(__file__).resolve().parents[1]
RED = {"N": "red", "E": "red", "S": "red", "W": "red"}


def phase(name: str, duration: float, green: str = "", yellow: str = "") -> SignalPhaseSpec:
    states = RED | {a: "green" for a in green} | {a: "yellow" for a in yellow}
    return SignalPhaseSpec(name=name, duration_s=duration, state="r", arm_states=states)


def program(*phases: SignalPhaseSpec, program_id: str = "cut") -> SignalProgramSpec:
    return SignalProgramSpec(
        program_id=program_id, timeline_id="t", label="assumed", annotation_sha256=None,
        cycle_start_s=0, cycle_s=sum(p.duration_s for p in phases), phases=phases,
    )  # fmt: skip


CUT = program(phase("pN", 20, "N"), phase("yN", 3, yellow="N"), phase("pE", 12, "E"),
              phase("pW", 6, "W"), phase("ar", 2))  # fmt: skip


def test_the_family_is_every_one_step_move_on_the_adjustable_phases() -> None:
    moves = generate_moves(CUT)
    n = 3  # pN, pE, pW; the yellow and the all-red are fixed
    assert len(moves) == 4 * n * (n - 1) + 8 * n
    assert kind_counts(moves) == {"pair": 24, "increase": 12, "decrease": 12}
    assert len({m.label for m in moves}) == len(moves)
    assert {name for m in moves for name, _ in m.changes} == {"pN", "pE", "pW"}
    durations = {p.name: p.duration_s for p in CUT.phases}
    for m in moves:
        delta = sum(g - durations[name] for name, g in m.changes)
        expected = {"pair": 0}.get(m.kind)
        assert expected is None or delta == expected
        assert m.kind == "pair" or abs(delta) in (2, 4, 6, 8)
    assert moves[0] == Move("pN->pE:2", "pair", (("pN", 18), ("pE", 14)))
    assert generate_moves(CUT) == moves  # fixed order


def test_the_proposal_cites_the_signal_program_and_names_the_move() -> None:
    proposal = generate_moves(CUT)[0].proposal("cut")
    assert proposal.evidence_ids == ("sig.program",)
    assert proposal.type == "signal_timing" and proposal.target_program == "cut"
    assert "pN->pE:2" in proposal.rationale


def _outcome(
    durations: tuple[float, ...] | None, reasons: tuple[str, ...] = ()
) -> ValidationOutcome:
    return ValidationOutcome(
        intervention_id="I1", status="accepted" if durations else "rejected", reasons=reasons,
        program_id="cut", phase_durations_s=durations,
        cycle_s=sum(durations) if durations else None, baseline_cycle_s=CUT.cycle_s,
        validated_at_utc=VALIDATED_AT,
    )  # fmt: skip


class _Network:
    class manifest:  # noqa: N801  (the attribute path screen_moves reads)
        signal_program = CUT


def test_screening_counts_rejections_by_reason_and_keeps_the_first_of_equal_timings() -> None:
    names = [p.name for p in CUT.phases]
    base = [p.duration_s for p in CUT.phases]

    def validator(proposal, network, bundle):
        durations = list(base)
        for change in proposal.phase_changes:
            if change.green_s < 5:
                return _outcome(None, (f"{change.phase_name} below 5 s",))
            durations[names.index(change.phase_name)] = change.green_s
        if any(c.phase_name == "pW" for c in proposal.phase_changes):
            durations = list(base)  # every accepted pW move validates to one timing here
            durations[3] = 99
        return _outcome(tuple(durations))

    screen = screen_moves(_Network(), bundle=None, validator=validator)
    assert len(screen.generated) == 48
    accepted_pw = [
        m for m in screen.generated
        if any(n == "pW" for n, _ in m.changes) and all(g >= 5 for _, g in m.changes)
    ]  # fmt: skip
    labels = [m.move.label for m in screen.accepted]
    assert accepted_pw[0].label in labels
    assert not set(labels) & {m.label for m in accepted_pw[1:]}
    assert screen.duplicates == tuple((m.label, accepted_pw[0].label) for m in accepted_pw[1:])
    # pW is 6 s: no step can come off it (3 moves x 4 steps); pE is 12 s: only 8 s cannot
    # (pE->pN:8, pE->pW:8, pE-8)
    assert dict(screen.rejected) == {"pE below 5 s": 3, "pW below 5 s": 12}


def _accepted(label: str, durations: tuple[float, ...]) -> AcceptedMove:
    return AcceptedMove(Move(label, "pair", ()), _outcome(durations))


SCREEN = MoveScreen(
    generated=(),
    accepted=(
        _accepted("east", (20, 3, 16, 2, 2)),  # pE +4, pW -4
        _accepted("north", (24, 3, 8, 6, 2)),  # pN +4, pE -4
    ),
    duplicates=(),
    rejected=(),
)


def test_member_fit_names_mapped_clamped_and_unmapped_moves() -> None:
    both = program(phase("pN", 20, "N"), phase("pE", 6, "E"), phase("pW", 6, "W"),
                   program_id="T1")  # fmt: skip
    no_east = program(phase("pN", 20, "N"), phase("pW", 6, "W"), program_id="T2")
    fits, common = fit_members(SCREEN, CUT, {"T2": no_east, "T1": both})
    by = {f.member: f for f in fits}
    assert [f.member for f in fits] == ["T1", "T2"]
    assert by["T1"].mapped == ("east", "north")
    assert by["T1"].clamped == ("east", "north")  # pW 6 - 4 and pE 6 - 4 fall below 5 s
    assert by["T2"].mapped == ("east", "north") and by["T2"].clamped == ("east",)
    assert [m.move.label for m in common] == ["east", "north"]


def test_a_move_no_member_phase_can_carry_is_unmapped_and_leaves_the_common_menu() -> None:
    only_east = program(phase("pE", 20, "E"), program_id="T3")
    nothing = program(phase("x", 20, "S"), program_id="T4")
    fits, common = fit_members(SCREEN, CUT, {"T3": only_east, "T4": nothing})
    assert fits[0].mapped == ("east", "north") and fits[0].clamped == ()
    assert fits[1].unmapped == ("east", "north")
    assert common == ()


def test_the_menu_hash_covers_labels_durations_and_order() -> None:
    a, b = _accepted("a", (1.0, 2.0)), _accepted("b", (2.0, 1.0))
    assert menu_sha256([a, b]) != menu_sha256([b, a])
    assert menu_sha256([a]) != menu_sha256([_accepted("a", (1.0, 3.0))])
    assert menu_sha256([a, b]) == menu_sha256([_accepted("a", (1, 2)), _accepted("b", (2, 1))])


def _script():
    spec = importlib.util.spec_from_file_location("d2", REPO / "scripts/d2_move_family.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_refuses_missing_inputs(tmp_path: Path, capsys) -> None:
    assert _script().main(["--scenario", str(tmp_path / "none.json")]) == 2
    assert "cannot compute" in capsys.readouterr().err


CUT_BASELINE = REPO / "experiments/runs/ngsim-int2-baseline-20261028T074304Z"
MEMBER = REPO / "experiments/runs/ngsim-int2-realism-experiment-20261029T123007Z"


@pytest.mark.skipif(
    not (CUT_BASELINE.is_dir() and MEMBER.is_dir()),
    reason="the cut baseline and the timeline members are Git-ignored",
)
def test_the_recorded_inputs_give_the_prototype_counts(capsys) -> None:
    """224 moves, 88 distinct accepted, 80 mapped on all four members (ADR-033 decision 8)."""
    script = _script()
    assert script.main([]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["generated"] == {"pair": 168, "increase": 28, "decrease": 28}
    assert sum(first["accepted_distinct"].values()) == 88
    assert first["duplicates"] == 0
    assert sum(first["mapped_on_all_members"].values()) == len(first["menu"]) == 80
    assert first["menu_sha256"] == (
        "709bc2cfcc6ebe3f56ad264c3c65608b34561c8f86989e873ff2e612c09ff10e"
    )
    assert script.main([]) == 0
    assert json.loads(capsys.readouterr().out) == first
