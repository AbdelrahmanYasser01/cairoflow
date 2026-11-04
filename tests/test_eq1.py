"""EQ-1 (= D1): the equal-saturation allocation of ADR-033 decision 3, on committed inputs.

The known answer (p0, p5, p8, p11, p13, p14, p16) = (29, 5, 10, 7, 1, 12, 5) is checked on the
committed cut-program fixture, scenario.json and geometry.json. Further tests show it comes from
the search and not from a constant: a tiny program checked against an independent brute force,
local optimality of the answer, invariance to scaling every flow, a changed flow giving another
answer, and every refusal. Assumption (e), equal saturation flow and no lost time, is an
experimental assumption and is carried in every record.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from fractions import Fraction
from functools import cache
from pathlib import Path

import pytest

from cairoflow_contracts.evidence import IntersectionGeometry
from cairoflow_contracts.experiment import SignalPhaseSpec, SignalProgramSpec
from cairoflow_simulation import eq1
from cairoflow_simulation.validation import timing_problems

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/eq1_cut_program.json"
FIXTURE_SHA256 = "917710b5dace619f17b019fdfaae1139fefd3e281d863989ec6782bd6b7f377a"
SCENARIO = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
GEOMETRY = REPO / "data/geometry/ngsim-lankershim-int2/geometry.json"
BASELINE = REPO / "experiments/runs/ngsim-int2-baseline-20261028T074304Z"
KNOWN_ANSWER = (29, 5, 10, 7, 1, 12, 5)


@cache
def committed_inputs() -> eq1.Eq1Inputs:
    program = SignalProgramSpec.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    geometry = IntersectionGeometry.model_validate_json(GEOMETRY.read_text(encoding="utf-8"))
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    return eq1.inputs_from_records(program, scenario, geometry)


@cache
def committed_result() -> eq1.Eq1Result:
    return eq1.allocate(committed_inputs())


def test_the_fixture_is_the_one_pinned() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


@pytest.mark.skipif(not BASELINE.is_dir(), reason="the cut baseline is Git-ignored")
def test_the_fixture_is_the_cut_baseline_program() -> None:
    from cairoflow_simulation.candidates import load_baseline

    _, network = load_baseline(BASELINE)
    fixture = SignalProgramSpec.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    assert network.manifest.signal_program == fixture


# --- the known answer -----------------------------------------------------------------------


def test_the_committed_inputs_give_the_known_answer() -> None:
    result = committed_result()
    assert result.vector == KNOWN_ANSWER
    assert result.reported_phases == ("p0", "p5", "p8", "p11", "p13", "p14", "p16")
    assert sum(result.durations) == 99  # the cycle is kept
    assert timing_problems(committed_inputs().program.phases, result.durations_s) == []
    base = [int(p.duration_s) for p in committed_inputs().program.phases]
    changed = {i for i, (a, b) in enumerate(zip(base, result.durations, strict=True)) if a != b}
    assert changed <= set(committed_inputs().free_indices)  # only free phases move; p13 stays


def test_the_critical_flows_follow_the_head_rule() -> None:
    y = committed_inputs().critical_flow
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    q = {
        (m["approach"], m["turn"]): Fraction(str(m["flow_veh_per_h"]))
        for m in scenario["movements"]
    }
    through, left = committed_inputs().lanes_through, committed_inputs().lanes_left
    assert y["N"] == q["N", "through"] / through["N"]
    assert y["S"] == q["S", "through"] / through["S"]
    assert y["E"] == max(q["E", "through"] / through["E"], q["E", "left"] / left["E"])
    assert y["W"] == max(q["W", "through"] / through["W"], q["W", "left"] / left["W"])
    assert y["NL"] == q["N", "left"] / left["N"] and y["SL"] == q["S", "left"] / left["S"]
    assert set(y) == set(eq1.HEADS)  # right turns are not a head


def test_the_answer_is_locally_optimal_under_the_ordering() -> None:
    """Moving one second between any two free phases never gives a smaller key that passes."""
    inputs, result = committed_inputs(), committed_result()
    best = eq1.order_key(inputs, result.durations)
    for a, b in itertools.permutations(inputs.free_indices, 2):
        durations = list(result.durations)
        durations[a] -= 1
        durations[b] += 1
        if durations[a] < inputs.min_green_s:
            continue
        if timing_problems(inputs.program.phases, [float(d) for d in durations]):
            continue
        assert eq1.order_key(inputs, tuple(durations)) > best


def test_scaling_every_flow_keeps_the_answer_and_a_changed_flow_moves_it() -> None:
    inputs = committed_inputs()
    tripled = eq1.allocate(inputs.with_flows({k: v * 3 for k, v in inputs.flows.items()}))
    assert tripled.vector == KNOWN_ANSWER
    heavier_west = dict(inputs.flows)
    heavier_west["W", "through"] *= 4
    assert eq1.allocate(inputs.with_flows(heavier_west)).vector != KNOWN_ANSWER


def test_the_record_carries_inputs_assumption_steps_and_hashes() -> None:
    record = eq1.record(committed_inputs(), committed_result())
    assert record["algorithm"] == eq1.EQ1_VERSION
    assert "equal saturation flow" in record["assumption"]
    assert record["assumption_status"].startswith("experimental assumption")
    assert record["input_sha256"] == eq1.input_sha256(committed_inputs())
    assert record["result"]["vector"] == list(KNOWN_ANSWER)
    steps = record["steps"]
    assert steps["enumerated"] > 100_000 and steps["rank_of_selected"] >= 1
    assert steps["rejected_by_timing_before_selected"] == steps["rank_of_selected"] - 1
    assert set(record["result"]["loads"]) == set(eq1.HEADS)
    assert record["result"]["plan_sha256"] == committed_result().plan_sha256
    assert len(record["record_sha256"]) == 64


def test_two_runs_give_byte_identical_records() -> None:
    first = json.dumps(eq1.record(committed_inputs(), committed_result()), sort_keys=True)
    again = eq1.allocate(committed_inputs())
    assert json.dumps(eq1.record(committed_inputs(), again), sort_keys=True) == first


# --- a small program against an independent brute force ----------------------------------------


RED = {"N": "red", "E": "red", "S": "red", "W": "red"}


def phase(name: str, duration: float, green: str = "", yellow: str = "", left: dict | None = None):
    arms = RED | {a: "green" for a in green} | {a: "yellow" for a in yellow}
    return SignalPhaseSpec(
        name=name, duration_s=duration, state="r", arm_states=arms, left_states=left or {}
    )


def small_program() -> SignalProgramSpec:
    phases = (
        phase("p0_ns", 20, "NS"), phase("p1_y", 3, yellow="NS"), phase("p2_ar", 2),
        phase("p3_ew", 12, "EW"), phase("p4_y", 3, yellow="EW"),
        phase("p5_left", 8, left={"N": "green", "S": "green"}),
        phase("p6_ly", 3, left={"N": "yellow", "S": "yellow"}),
    )  # fmt: skip
    return SignalProgramSpec(
        program_id="small", timeline_id="t", label="assumed", annotation_sha256=None,
        cycle_start_s=0, cycle_s=sum(p.duration_s for p in phases), phases=phases,
    )  # fmt: skip


SMALL_FLOWS = {
    ("N", "through"): Fraction(600), ("S", "through"): Fraction(500),
    ("E", "through"): Fraction(200), ("E", "left"): Fraction(90),
    ("W", "through"): Fraction(150), ("W", "left"): Fraction(120),
    ("N", "left"): Fraction(180), ("S", "left"): Fraction(60),
}  # fmt: skip
ONE_LANE = {"N": 1, "S": 1, "E": 1, "W": 1}


def small_inputs(**overrides) -> eq1.Eq1Inputs:
    fields = {
        "program": small_program(), "flows": SMALL_FLOWS, "lanes_through": {"N": 2, "S": 2,
        "E": 1, "W": 1}, "lanes_left": ONE_LANE, "free_phases": ("p0", "p3", "p5"),
    }  # fmt: skip
    return eq1.Eq1Inputs(**(fields | overrides))


def brute_force(inputs: eq1.Eq1Inputs) -> tuple[int, ...]:
    """Independent: every vector, loads as Fractions, sorted by the ADR ordering, first legal."""
    base = [int(p.duration_s) for p in inputs.program.phases]
    free = inputs.free_indices
    pool = sum(base[i] for i in free)
    y = inputs.critical_flow
    candidates = []
    for values in itertools.product(range(int(inputs.min_green_s), pool + 1), repeat=len(free)):
        if sum(values) != pool:
            continue
        durations = list(base)
        for i, v in zip(free, values, strict=True):
            durations[i] = v
        green = {h: sum(durations[i] for i in inputs.head_phases[h]) for h in y}
        loads = tuple(sorted((y[h] / green[h] for h in y), reverse=True))
        l1 = sum(abs(durations[i] - base[i]) for i in free)
        vector = tuple(durations[i] for i in inputs.reported_indices)
        candidates.append((loads, l1, vector, tuple(durations)))
    for _, _, _, durations in sorted(candidates):
        if not timing_problems(inputs.program.phases, [float(d) for d in durations]):
            return durations
    raise AssertionError("no legal vector")


def test_a_small_program_matches_an_independent_brute_force() -> None:
    inputs = small_inputs()
    assert eq1.allocate(inputs).durations == brute_force(inputs)
    shifted = small_inputs(flows=SMALL_FLOWS | {("E", "through"): Fraction(700)})
    assert eq1.allocate(shifted).durations == brute_force(shifted)
    assert eq1.allocate(shifted).durations != eq1.allocate(inputs).durations


def test_the_first_vector_that_passes_the_head_run_bounds_is_taken_not_the_first_overall() -> None:
    """Heavy left demand would give the protected-left phase more than its 40 s bound
    (validation.LEFT_GREEN_S): the leximin optimum is refused and a later vector is chosen."""
    program = small_program()
    longer = program.model_copy(
        update={"phases": (program.phases[0].model_copy(update={"duration_s": 50.0}),
                           *program.phases[1:]), "cycle_s": program.cycle_s + 30}
    )  # fmt: skip
    flows = {k: Fraction(10) for k in SMALL_FLOWS} | {
        ("N", "left"): Fraction(900), ("S", "left"): Fraction(900),
    }  # fmt: skip
    inputs = small_inputs(program=longer, flows=flows)
    result = eq1.allocate(inputs)
    assert result.rank > 1  # vectors earlier in the ordering failed timing_problems
    assert result.durations == brute_force(inputs)
    assert result.durations[5] == 40  # the left phase stops at its bound
    unbounded = min(
        (eq1.order_key(inputs, d), d)
        for d in (
            (p0, 3, 2, p3, 3, 70 - p0 - p3, 3) for p0 in range(5, 61) for p3 in range(5, 66 - p0)
        )
    )[1]
    assert unbounded[5] > 40 and timing_problems(longer.phases, [float(x) for x in unbounded])


def test_ties_on_loads_go_to_the_smaller_change_then_lexicographic() -> None:
    """E, W, NL and SL carry 100 veh/h/lane each, N and S nothing: p0 drops to 5 s and the 35 s
    left go to p3 (E, W) and p5 (NL, SL). 17/18 and 18/17 give the same sorted load vector and
    the same L1 change (30 s), so the lexicographic rule picks (p0, p3, p5) = (5, 17, 18)."""
    flows = {k: Fraction(0) for k in SMALL_FLOWS} | {
        ("E", "through"): Fraction(100), ("W", "through"): Fraction(100),
        ("N", "left"): Fraction(100), ("S", "left"): Fraction(100),
    }  # fmt: skip
    inputs = small_inputs(flows=flows)
    result = eq1.allocate(inputs)
    assert result.vector == (5, 17, 18)
    assert result.l1_change_s == 30
    assert result.tied_on_loads == 2
    assert result.durations == brute_force(inputs)


# --- refusals -------------------------------------------------------------------------------------


def test_invalid_inputs_are_refused() -> None:
    with pytest.raises(eq1.Eq1Error, match="not adjustable"):
        eq1.allocate(small_inputs(free_phases=("p0", "p1")))
    with pytest.raises(eq1.Eq1Error, match="no phase"):
        eq1.allocate(small_inputs(free_phases=("p0", "p9")))
    missing = {k: v for k, v in SMALL_FLOWS.items() if k != ("W", "left")}
    with pytest.raises(eq1.Eq1Error, match="movement"):
        eq1.allocate(small_inputs(flows=missing))
    with pytest.raises(eq1.Eq1Error, match="negative"):
        eq1.allocate(small_inputs(flows=SMALL_FLOWS | {("N", "left"): Fraction(-1)}))
    with pytest.raises(eq1.Eq1Error, match="lane"):
        eq1.allocate(small_inputs(lanes_left=ONE_LANE | {"S": 0}))
    with pytest.raises(eq1.Eq1Error, match="no allocation"):
        eq1.allocate(small_inputs(min_green_s=20))
    whole = small_program()
    first = whole.phases[0].model_copy(update={"duration_s": 20.5})
    half = whole.model_copy(update={"phases": (first, *whole.phases[1:]), "cycle_s": 51.5})
    with pytest.raises(eq1.Eq1Error, match="whole seconds"):
        eq1.allocate(small_inputs(program=half))


def test_a_head_without_green_is_refused() -> None:
    program = small_program()
    no_left = program.model_copy(
        update={"phases": tuple(p.model_copy(update={"left_states": {}}) for p in program.phases)}
    )
    with pytest.raises(eq1.Eq1Error, match="never green"):
        eq1.allocate(small_inputs(program=no_left, free_phases=("p0", "p3")))
