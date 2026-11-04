"""Pair identity and the vehicle-population hash (ADR-033).

The route files written here hold two invented vehicles, only to exercise the hash. They are not
traffic evidence and resemble no recorded demand. The last test reads the four recorded timeline
members' baselines (Git-ignored; skipped where they are absent).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cairoflow_contracts.experiment import RunSettings, SignalPhaseSpec, SignalProgramSpec
from cairoflow_contracts.pairing import (
    PairIdentity,
    PairingError,
    RunIdentity,
    pairing_problems,
    plan_sha256,
    program_structure_sha256,
    settings_pairing_sha256,
)
from cairoflow_simulation.population import (
    PopulationError,
    population_sha256,
    speed_factors_sha256,
)

REPO = Path(__file__).resolve().parents[1]

ROUTE = """<?xml version="1.0" encoding="UTF-8"?>
<routes>
    <vType id="car" length="4.5"/>
    <vehicle id="v1" type="car" depart="1.0" departLane="0" speedFactor="{first}">
        <route edges="a_in b_out"/>
    </vehicle>
    <vehicle id="v2" type="car" depart="2.5" departLane="1" speedFactor="0.950">
        <route edges="a_in c_out"/>
    </vehicle>
</routes>
"""


def route(tmp_path: Path, text: str | None = None, first: str = "1.000", name: str = "r") -> Path:
    path = tmp_path / f"{name}.rou.xml"
    path.write_text((text or ROUTE).format(first=first), encoding="utf-8")
    return path


# --- the population hash -----------------------------------------------------------------------


def test_the_population_is_the_vehicles_without_their_speed_factors(tmp_path) -> None:
    """Pinned by spelling the canonical object out: types, then vehicles sorted by (depart, id),
    each without speedFactor, hashed as JSON with sorted keys and no spaces."""
    types = [[["id", "car"], ["length", "4.5"]]]
    vehicles = [
        [1.0, "v1", sorted([["depart", "1.0"], ["departLane", "0"], ["id", "v1"],
                            ["type", "car"]]), "a_in b_out"],
        [2.5, "v2", sorted([["depart", "2.5"], ["departLane", "1"], ["id", "v2"],
                            ["type", "car"]]), "a_in c_out"],
    ]  # fmt: skip
    expected = hashlib.sha256(
        json.dumps([types, vehicles], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert population_sha256(route(tmp_path)) == expected


def test_speed_factors_change_the_demand_but_not_the_population(tmp_path) -> None:
    one = route(tmp_path, first="1.000", name="one")
    other = route(tmp_path, first="1.120", name="other")
    assert population_sha256(one) == population_sha256(other)
    assert speed_factors_sha256(one) != speed_factors_sha256(other)
    assert hashlib.sha256(one.read_bytes()).digest() != hashlib.sha256(other.read_bytes()).digest()


@pytest.mark.parametrize(
    "old, new",
    [
        ('depart="1.0"', 'depart="1.5"'),
        ('departLane="0"', 'departLane="1"'),
        ("a_in b_out", "a_in d_out"),
        ('id="v1"', 'id="v9"'),
        ('type="car" depart="2.5"', 'type="bus" depart="2.5"'),
        ('length="4.5"', 'length="5.0"'),
    ],
)
def test_any_other_change_to_the_vehicles_changes_the_population(tmp_path, old, new) -> None:
    assert old in ROUTE
    changed = route(tmp_path, ROUTE.replace(old, new), name="changed")
    assert population_sha256(changed) != population_sha256(route(tmp_path))


def test_the_order_of_vehicles_in_the_file_does_not_matter(tmp_path) -> None:
    head, rest = ROUTE.split("    <vehicle", 1)
    first, second = ("    <vehicle" + rest).split("</vehicle>\n")[:2]
    swapped = head + second + "</vehicle>\n" + first + "</vehicle>\n</routes>\n"
    assert swapped != ROUTE
    reordered = route(tmp_path, swapped, name="swapped")
    assert population_sha256(reordered) == population_sha256(route(tmp_path))


@pytest.mark.parametrize(
    "text, message",
    [
        ("<routes><vType id='car'/></routes>", "no <vehicle> element"),
        ("<routes><trip id='t' from='a' to='b'/></routes>", "<trip>"),
        ("<routes><vehicle id='a' depart='0'/><vehicle id='a' depart='1'/></routes>", "unique"),
        ("<routes><vehicle depart='0'/></routes>", "no id"),
    ],
)
def test_a_file_without_an_identifiable_population_is_refused(tmp_path, text, message) -> None:
    path = tmp_path / "bad.rou.xml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PopulationError, match=message):
        population_sha256(path)


VEHICLE = '<vehicle id="v1" depart="0">{inside}</vehicle>'
EDGES = '<route edges="a b"/>'


@pytest.mark.parametrize(
    "text, message",
    [
        ('<routes><route id="r1" edges="a b"/><vehicle id="v1" route="r1" depart="0"/></routes>',
         "<route> element has no population identity"),
        (f'<routes><routeDistribution id="d"/>{VEHICLE.format(inside=EDGES)}</routes>',
         "<routeDistribution>"),
        ('<routes><vehicle id="v1" depart="0" route="r1"/></routes>', "exactly one <route> child"),
        ('<routes><vehicle id="v1" depart="0"/></routes>', "exactly one <route> child"),
        ("<routes>" + VEHICLE.format(inside=EDGES + '<stop lane="a_0"/>') + "</routes>",
         "exactly one <route> child"),
        ("<routes>" + VEHICLE.format(inside='<route edges="a b" repeat="3"/>') + "</routes>",
         "may only have edges"),
        (f'<routes><vType id="car"><param key="k" value="v"/></vType>'
         f"{VEHICLE.format(inside=EDGES)}</routes>", "has child elements"),
        (f'<routes><vehicle id="v1" depart="triggered">{EDGES}</vehicle></routes>',
         "depart 'triggered'"),
        ("<routes><vehicle", "not well-formed"),
    ],
)  # fmt: skip
def test_features_the_hash_would_ignore_are_refused_rather_than_skipped(
    tmp_path, text, message
) -> None:
    path = tmp_path / "odd.rou.xml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PopulationError, match=message):
        population_sha256(path)
    if "depart" not in message:  # the speed-factor hash does not read depart
        with pytest.raises(PopulationError):
            speed_factors_sha256(path)


def test_the_depart_text_is_hashed_as_written(tmp_path) -> None:
    """`depart` is parsed to order the vehicles, but "1.0" and "1.00" are different files."""
    other = route(tmp_path, ROUTE.replace('depart="1.0"', 'depart="1.00"'), name="other")
    assert population_sha256(other) != population_sha256(route(tmp_path))


# --- the pair ----------------------------------------------------------------------------------


def identity(arm: str = "baseline", **changes) -> RunIdentity:
    fields = dict(
        member_sha256="1" * 64,
        timeline_id="timeline-v2",
        arm=arm,
        seed=3,
        demand_sha256="2" * 64,
        population_sha256="3" * 64,
        world_model_sha256="4" * 64,
        geometry_sha256="5" * 64,
        network_sha256="b" * 64,
        program_structure_sha256="c" * 64,
        run_config_sha256="e" * 64,
        settings_sha256="6" * 64,
        plan_sha256="7" * 64 if arm == "baseline" else "8" * 64,
    )
    return RunIdentity(**(fields | changes))


def test_a_valid_pair_is_named_by_its_content() -> None:
    pair = PairIdentity.of(identity(), identity("I1"))
    assert pair.population_verified
    assert PairIdentity.model_validate_json(pair.model_dump_json()) == pair
    other_plan = PairIdentity.of(identity(), identity("I1", plan_sha256="9" * 64))
    assert other_plan.pair_sha256 != pair.pair_sha256  # the treatment is part of the pair
    assert PairIdentity.of(identity(), identity("I1")).pair_sha256 == pair.pair_sha256


def test_two_candidates_of_one_member_can_be_paired_with_each_other() -> None:
    """The ladder compares arms; neither side has to be the baseline."""
    assert PairIdentity.of(identity("I1"), identity("D1")).reference.arm == "I1"


@pytest.mark.parametrize(
    "changes, label",
    [
        ({"member_sha256": "9" * 64}, "timeline member"),
        ({"timeline_id": "timeline-v1"}, "signal timeline"),
        ({"seed": 4}, "seed"),
        ({"demand_sha256": "a" * 64}, "demand file"),
        ({"world_model_sha256": "a" * 64}, "world model"),
        ({"geometry_sha256": "a" * 64}, "geometry"),
        ({"settings_sha256": "a" * 64}, "run settings"),
        ({"network_sha256": "a" * 64}, "network apart from its signal program"),
        ({"program_structure_sha256": "a" * 64}, "signal program structure"),
        ({"run_config_sha256": "a" * 64}, "SUMO run configuration"),
    ],
)
def test_runs_that_differ_in_anything_they_must_share_are_not_a_pair(changes, label) -> None:
    with pytest.raises(PairingError, match=f"{label} differs"):
        PairIdentity.of(identity(), identity("I1", **changes))


def test_a_candidate_of_another_timeline_member_is_refused_even_with_the_same_population() -> None:
    """T2 and T3 replay the same vehicles under different timelines: the population matches, the
    pair is still not a pair (ADR-033: strictly within a member)."""
    other_member = identity("I1", member_sha256="9" * 64, timeline_id="timeline-v2a")
    problems = pairing_problems(identity(), other_member)
    assert any("timeline member differs" in p for p in problems)
    assert any("signal timeline differs" in p for p in problems)
    assert identity().population_sha256 == other_member.population_sha256


def test_a_mismatched_population_is_refused_even_when_the_demand_hashes_agree() -> None:
    other = identity("I1", population_sha256="a" * 64)
    assert pairing_problems(identity(), other) == (
        f"vehicle population differs: {'3' * 12} vs {'a' * 12}",
    )


def test_the_same_vehicles_with_other_speed_factors_are_not_this_seeds_demand() -> None:
    other = identity("I1", demand_sha256="a" * 64)
    problems = pairing_problems(identity(), other)
    assert len(problems) == 2 and "speedFactor" in problems[1]


def test_an_unread_population_cannot_hide_a_demand_mismatch() -> None:
    unread = identity("I1", population_sha256=None, demand_sha256="a" * 64)
    with pytest.raises(PairingError, match="demand file differs"):
        PairIdentity.of(identity(), unread)
    pair = PairIdentity.of(identity(), identity("I1", population_sha256=None))
    assert not pair.population_verified  # allowed, and said so


def test_a_run_is_not_paired_with_itself() -> None:
    with pytest.raises(PairingError, match="not a pair of different arms"):
        PairIdentity.of(identity(), identity())


def test_every_broken_rule_is_reported_at_once() -> None:
    other = identity("I1", seed=9, geometry_sha256="a" * 64, settings_sha256="b" * 64)
    assert len(pairing_problems(identity(), other)) == 3


def test_a_pair_cannot_be_built_or_edited_into_an_invalid_one() -> None:
    reference, compared = identity(), identity("I1")
    good = PairIdentity.of(reference, compared)
    with pytest.raises(ValidationError, match="not a valid pair"):
        PairIdentity(reference=reference, compared=identity("I1", seed=4), pair_sha256="0" * 64)
    with pytest.raises(ValidationError, match="does not hash"):
        PairIdentity(reference=reference, compared=compared, pair_sha256="0" * 64)
    with pytest.raises(ValidationError):
        identity(member_sha256="not-a-digest")
    assert good.pair_sha256 == PairIdentity.digest_of(reference, compared)


# --- what the identity hashes ------------------------------------------------------------------


def program(
    program_id: str = "p", durations: tuple[float, ...] = (20.0, 10.0)
) -> SignalProgramSpec:
    arms = {"N": "green", "S": "green", "E": "red", "W": "red"}
    return SignalProgramSpec(
        program_id=program_id,
        timeline_id="t",
        label="assumed",
        annotation_sha256=None,
        cycle_start_s=0,
        cycle_s=sum(durations),
        phases=tuple(
            SignalPhaseSpec(name=f"p{i}", duration_s=d, state="GGrr", arm_states=arms)
            for i, d in enumerate(durations)
        ),
    )


def test_a_plan_is_its_phases_as_run_whatever_the_program_is_called() -> None:
    assert plan_sha256(program("a")) == plan_sha256(program("b"))
    assert plan_sha256(program()) != plan_sha256(program(durations=(21.0, 10.0)))


def test_the_phase_structure_ignores_durations_but_not_states_or_names() -> None:
    base = program()
    longer = program(durations=(21.0, 10.0))
    assert program_structure_sha256(base) == program_structure_sha256(longer)
    assert plan_sha256(base) != plan_sha256(longer)
    first = base.phases[0]
    restated = base.model_copy(
        update={"phases": (first.model_copy(update={"state": "GrGr"}), *base.phases[1:])}
    )
    renamed = base.model_copy(
        update={"phases": (first.model_copy(update={"name": "other"}), *base.phases[1:])}
    )
    assert program_structure_sha256(base) != program_structure_sha256(restated)
    assert program_structure_sha256(base) != program_structure_sha256(renamed)


def test_settings_are_compared_without_their_seeds() -> None:
    base = RunSettings(seeds=(1, 2, 3))
    assert settings_pairing_sha256(base) == settings_pairing_sha256(RunSettings(seeds=(101, 102)))
    longer = RunSettings(seeds=(1,), warmup_s=1)
    assert settings_pairing_sha256(base) != settings_pairing_sha256(longer)


# --- the recorded timeline members -------------------------------------------------------------

MEMBER_RECORD = REPO / "experiments/records/ngsim-int2-timeline-ensemble-20261029T123045Z.json"
# Population hashes of the four members' replay baselines, computed with the definition G0 used.
# T2 and T3 replay the same observed vehicles; T1 and T4 differ because cross-street entries are
# anchored to each member's own greens (trace_demand).
MEMBER_POPULATIONS = {
    "T1": "7bddb1377f22088d43fbdefea65d4fb81e8a42bcc81a16c714d6a8b03cb28d8a",
    "T2": "491de28dc88a4c06e5c3081c8ac4c9658634264d5b04ba40b86a2dc6e378ef00",
    "T3": "491de28dc88a4c06e5c3081c8ac4c9658634264d5b04ba40b86a2dc6e378ef00",
    "T4": "c8ec465f974c8f26f0e8c9d71e034775a3429769f5d75cce3ba42bfcebdf327b",
}


def _members() -> dict[str, Path]:
    if not MEMBER_RECORD.is_file():
        return {}
    record = json.loads(MEMBER_RECORD.read_text(encoding="utf-8"))
    found = {m["id"]: REPO / m["baseline_dir"] / "demand" for m in record["members"]}
    return found if all((d / "seed_20.rou.xml").is_file() for d in found.values()) else {}


@pytest.mark.skipif(not _members(), reason="the timeline-member baselines are Git-ignored")
def test_each_recorded_member_replays_one_population_over_all_twenty_seeds() -> None:
    """Needs the Git-ignored baselines, so a fresh clone relies on the pinned canonical object in
    `test_the_population_is_the_vehicles_without_their_speed_factors` instead."""
    found = {}
    for member, demand in _members().items():
        files = sorted(demand.glob("seed_*.rou.xml"))
        assert len(files) == 20
        hashes = {population_sha256(f) for f in files}
        assert len(hashes) == 1  # one population for all twenty seeds
        assert len({speed_factors_sha256(f) for f in files}) == 20  # only the speed draws differ
        found[member] = hashes.pop()
    assert found == MEMBER_POPULATIONS  # the values G0 recorded, from the code, not the literals
    assert found["T2"] == found["T3"] and len({found[m] for m in ("T1", "T2", "T4")}) == 3
