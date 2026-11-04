"""EQ-1 v2 (ADR-033 decision 3, approved 2026-11-03): eq1-v1 with p16 fixed beside p13.

The eq1-v1 evaluation found its p16 change maps onto no replayed phase on members T2 and T4.
eq1-v2 changes only the free phases (p0, p5, p8, p11, p14). eq1-v1 must keep giving its answer.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import pytest

from cairoflow_contracts.evidence import IntersectionGeometry
from cairoflow_contracts.experiment import SignalProgramSpec
from cairoflow_simulation import eq1
from cairoflow_simulation.validation import timing_problems

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/eq1_cut_program.json"
SCENARIO = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
GEOMETRY = REPO / "data/geometry/ngsim-lankershim-int2/geometry.json"
ENSEMBLE = REPO / "experiments/records/ngsim-int2-timeline-ensemble-20261029T123045Z.json"


@cache
def inputs(version: str) -> eq1.Eq1Inputs:
    program = SignalProgramSpec.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    geometry = IntersectionGeometry.model_validate_json(GEOMETRY.read_text(encoding="utf-8"))
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    return eq1.inputs_from_records(program, scenario, geometry, version=version)


@cache
def result(version: str) -> eq1.Eq1Result:
    return eq1.allocate(inputs(version))


def test_the_versions_differ_only_in_the_free_phases() -> None:
    assert eq1.VERSIONS["eq1-v1"] == ("p0", "p5", "p8", "p11", "p14", "p16")
    assert eq1.VERSIONS["eq1-v2"] == ("p0", "p5", "p8", "p11", "p14")
    assert inputs("eq1-v2").version == "eq1-v2" and inputs("eq1-v1").version == "eq1-v1"


def test_v2_keeps_p13_and_p16_and_the_cycle() -> None:
    v2 = result("eq1-v2")
    by_tag = dict(zip(v2.reported_phases, v2.vector, strict=True))
    assert (by_tag["p13"], by_tag["p16"]) == (1, 8)  # their baseline durations
    assert sum(v2.durations) == 99
    assert timing_problems(inputs("eq1-v2").program.phases, v2.durations_s) == []
    assert sum(by_tag[t] for t in ("p0", "p5", "p8", "p11", "p14")) == 31 + 5 + 12 + 10 + 2


def test_v2_gives_the_vector_it_gave_when_first_computed() -> None:
    """Pinned when eq1-v2 was first run (2026-11-03), so a change to the allocator or its inputs
    shows. Unlike eq1-v1's, this is not an independent known answer."""
    assert result("eq1-v2").vector == (27, 5, 10, 7, 1, 11, 8)
    assert result("eq1-v2").plan_sha256.startswith("789635426ea6")


def test_v1_still_gives_its_known_answer_and_v2_another() -> None:
    assert result("eq1-v1").vector == (29, 5, 10, 7, 1, 12, 5)
    assert result("eq1-v2").vector != result("eq1-v1").vector
    assert result("eq1-v2").plan_sha256 != result("eq1-v1").plan_sha256


def test_the_record_names_the_version() -> None:
    record = eq1.record(inputs("eq1-v2"), result("eq1-v2"))
    assert record["algorithm"] == "eq1-v2"
    free = [n.split("_")[0] for n in record["inputs"]["free_phases"]]
    assert free == list(eq1.VERSIONS["eq1-v2"])
    assert record["input_sha256"] != eq1.input_sha256(inputs("eq1-v1"))


def test_an_unknown_version_is_refused_and_other_phases_are_custom() -> None:
    program = SignalProgramSpec.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    geometry = IntersectionGeometry.model_validate_json(GEOMETRY.read_text(encoding="utf-8"))
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    with pytest.raises(eq1.Eq1Error, match="version"):
        eq1.inputs_from_records(program, scenario, geometry, version="eq1-v9")
    v1 = inputs("eq1-v1")
    custom = eq1.Eq1Inputs(
        program=v1.program, flows=v1.flows, lanes_through=v1.lanes_through,
        lanes_left=v1.lanes_left, free_phases=("p0", "p5"),
    )  # fmt: skip
    assert custom.version == "eq1-custom"


@pytest.mark.skipif(not ENSEMBLE.is_file(), reason="ensemble record absent")
def test_every_v2_free_phase_changes_a_replayed_phase_on_every_member() -> None:
    from cairoflow_simulation.candidates import load_baseline
    from cairoflow_simulation.replay_candidates import MIN_GREEN_S, signature

    members = json.loads(ENSEMBLE.read_text(encoding="utf-8"))["members"]
    if not all((REPO / m["baseline_dir"]).is_dir() for m in members):
        pytest.skip("member baselines are Git-ignored")
    cut = inputs("eq1-v2").program
    usable = {}
    for phase in cut.phases:
        if not phase.adjustable:
            continue
        tag = phase.name.split("_")[0]
        for m in members:
            replay = load_baseline(REPO / m["baseline_dir"])[1].manifest.signal_program
            usable[tag, m["id"]] = sum(
                r.adjustable and signature(r) == signature(phase) and r.duration_s >= MIN_GREEN_S
                for r in replay.phases
            )
    for tag in eq1.VERSIONS["eq1-v2"]:
        assert all(usable[tag, m["id"]] > 0 for m in members), tag
    assert usable["p16", "T2"] == usable["p16", "T4"] == 0  # why eq1-v1 failed there
    assert all(usable["p13", m["id"]] == 0 for m in members)  # why p13 was always fixed
