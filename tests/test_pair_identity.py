"""PairIdentity as a derived view over recorded runs (ADR-033).

tests/test_pairing.py already covers the rules between two RunIdentity records and the population
hash itself. This file covers what the view adds on top: rebuilding the per-seed demand hashes
from a committed record, the view's own consistency (one timeline member, listed seeds, the
recorded demand of each seed), the unit-of-inference label, the reserved confirmation seeds and
the SUMO binary version. The identities below are invented hashes, not traffic evidence. The
recorded run directories are Git-ignored; the tests that read them skip where they are absent.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cairoflow_contracts.experiment import BaselineManifest
from cairoflow_contracts.pair_identity import (
    CONFIRMATION_SEEDS,
    POPULATION_EXCLUDES,
    ArmRuns,
    PairIdentityView,
    ReservedSeedError,
    SeedDemand,
    build_view,
    check_seeds,
    unit_of_inference,
)
from cairoflow_contracts.pairing import PairIdentity, PairingError, RunIdentity
from cairoflow_evaluation.pair_identity import pair_identity_view_from_record, sumo_binary_version
from cairoflow_simulation.population import (
    EXCLUDED_ATTRIBUTE,
    population_sha256,
    speed_factors_sha256,
)

REPO = Path(__file__).resolve().parents[1]
# Timeline member T2 (signal timeline v2): the realism experiment the ensemble record cites.
RECORD = REPO / "experiments/records/ngsim-int2-realism-experiment-20261028T142330Z.json"
REPLAY_LABEL = "n = 20 seeds on one vehicle realisation; conditional on it"
MEMBER = "1" * 64


def _recorded_dirs() -> tuple[Path, Path] | None:
    if not RECORD.is_file():
        return None
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    experiment = REPO / record["run_dir"] / "experiment"
    baseline = REPO / record["baseline_dir"]
    if not (experiment / "experiment_manifest.json").is_file():
        return None
    if not (baseline / "demand" / "seed_20.rou.xml").is_file():
        return None
    return experiment, baseline


recorded = pytest.mark.skipif(
    _recorded_dirs() is None, reason="the realism run directories are Git-ignored"
)


# --- invented identities -----------------------------------------------------------------------


def run(arm: str, seed: int, **changes) -> RunIdentity:
    fields = dict(
        member_sha256=MEMBER,
        timeline_id="timeline-v2",
        arm=arm,
        seed=seed,
        demand_sha256=f"{seed:064x}",
        population_sha256="3" * 64,
        world_model_sha256="4" * 64,
        geometry_sha256="5" * 64,
        network_sha256="b" * 64,
        program_structure_sha256="c" * 64,
        run_config_sha256="e" * 64,
        settings_sha256="6" * 64,
        plan_sha256="7" * 64 if arm == "baseline" else "8" * 64,
        intervention_sha256=None if arm == "baseline" else "9" * 64,
    )
    return RunIdentity(**(fields | changes))


def seed_demand(seed: int, population: str | None = "3" * 64) -> SeedDemand:
    return SeedDemand(
        seed=seed,
        demand_file=f"seed_{seed}.rou.xml",
        demand_sha256=f"{seed:064x}",
        population_sha256=population,
        speed_factors_sha256=None if population is None else f"{seed + 1000:064x}",
    )


def arm(name: str, seeds: tuple[int, ...], version: str | None = "1.28.0") -> ArmRuns:
    return ArmRuns(
        arm=name,
        plan_sha256="7" * 64 if name == "baseline" else "8" * 64,
        intervention_sha256=None if name == "baseline" else "9" * 64,
        network_sha256="b" * 64,
        sumo_binary_version=version,
        seeds=seeds,
    )


def view(numbers: tuple[int, ...] = (1, 2, 3), pairs=None, *, confirmation: bool = False, **kw):
    seeds = numbers
    pairs = (
        [PairIdentity.of(run("baseline", s), run("I1", s)) for s in seeds]
        if pairs is None
        else pairs
    )
    fields = dict(
        experiment_id="experiment-x",
        baseline_experiment_id="baseline-x",
        member_sha256=MEMBER,
        timeline_id="timeline-v2",
        world_model_sha256="4" * 64,
        network_sha256="b" * 64,
        horizon_s=633.0,
        sumo_wheel_version="1.28.0",
        seeds=[seed_demand(s) for s in seeds],
        arms=[arm("baseline", seeds), arm("I1", seeds)],
        pairs=pairs,
        confirmation=confirmation,
    )
    return build_view(**(fields | kw))


# --- (a) the committed realism record ----------------------------------------------------------


@recorded
def test_rebuilding_from_the_realism_record_reproduces_its_twenty_recorded_demand_hashes() -> None:
    experiment_dir, baseline_dir = _recorded_dirs()
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    raw = (baseline_dir / "baseline_manifest.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == record["baseline_manifest_sha256"]
    baseline = BaselineManifest.model_validate_json(raw)
    recorded_files = {f.seed: f.sha256 for f in baseline.demand.files}
    recorded_runs = {r.seed: r.demand_sha256 for r in baseline.runs}

    rebuilt = pair_identity_view_from_record(RECORD, REPO)

    demand = {s.seed: s.demand_sha256 for s in rebuilt.seeds}
    assert sorted(demand) == record["seeds"] == list(range(1, 21))
    assert demand == recorded_files == recorded_runs
    for s in rebuilt.seeds:  # and the files on disk still hash to what was recorded
        path = baseline_dir / "demand" / s.demand_file
        assert hashlib.sha256(path.read_bytes()).hexdigest() == s.demand_sha256
    assert rebuilt.member_sha256 == record["baseline_manifest_sha256"]
    assert rebuilt.horizon_s == baseline.settings.horizon_s == 633.0
    assert {a.arm for a in rebuilt.arms} == {"baseline", "I1", "I2", "I3"}
    assert len(rebuilt.pairs) == 3 * 20 and all(p.population_verified for p in rebuilt.pairs)


@recorded
def test_the_realism_replay_is_one_vehicle_realisation_and_speed_factors_stay_out() -> None:
    """SPEED decision, pinned on the record: the speed draws differ per seed and the demand file's
    hash covers them, the population hash leaves them out."""
    rebuilt = pair_identity_view_from_record(RECORD, REPO)
    assert rebuilt.unit_of_inference == REPLAY_LABEL
    assert len({s.population_sha256 for s in rebuilt.seeds}) == 1
    assert len({s.speed_factors_sha256 for s in rebuilt.seeds}) == 20
    assert len({s.demand_sha256 for s in rebuilt.seeds}) == 20


@recorded
def test_the_view_names_the_sumo_binary_that_wrote_the_outputs() -> None:
    experiment_dir, baseline_dir = _recorded_dirs()
    rebuilt = pair_identity_view_from_record(RECORD, REPO)
    header = sumo_binary_version(baseline_dir / "runs" / "seed_1" / "tripinfo.xml")
    assert header == rebuilt.sumo_binary_version == "1.28.0"
    assert {a.sumo_binary_version for a in rebuilt.arms} == {header}
    baseline = BaselineManifest.model_validate_json(
        (baseline_dir / "baseline_manifest.json").read_bytes()
    )
    assert rebuilt.sumo_wheel_version == baseline.network.sumo_version  # kept apart, labelled


@recorded
def test_the_rebuilt_view_is_deterministic_and_named_by_its_content() -> None:
    first = pair_identity_view_from_record(RECORD, REPO)
    second = pair_identity_view_from_record(RECORD, REPO)
    assert first.model_dump_json() == second.model_dump_json()
    assert PairIdentityView.model_validate_json(first.model_dump_json()) == first


def test_a_record_whose_baseline_manifest_changed_is_refused(tmp_path) -> None:
    dirs = _recorded_dirs()
    if dirs is None:
        pytest.skip("the realism run directories are Git-ignored")
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    record["baseline_manifest_sha256"] = "0" * 64
    edited = tmp_path / "record.json"
    edited.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(PairingError, match="baseline manifest"):
        pair_identity_view_from_record(edited, REPO)


# --- the SUMO binary version -------------------------------------------------------------------


@pytest.mark.parametrize(
    "header, version",
    [
        ("<!-- generated on 2026-10-28T17:19:20+02:00 by Eclipse SUMO sumo 1.28.0\n", "1.28.0"),
        ("<!-- generated on 2024-01-01 12:00:00 by Eclipse SUMO sumo Version 1.19.0\n", "1.19.0"),
        ("<tripinfos>\n", None),
    ],
)
def test_the_binary_version_is_read_from_the_output_header(tmp_path, header, version) -> None:
    path = tmp_path / "tripinfo.xml"
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n\n' + header + "<x/>\n", "utf-8")
    assert sumo_binary_version(path) == version


def test_runs_of_different_sumo_binaries_do_not_form_one_view() -> None:
    seeds = (1, 2)
    with pytest.raises(PairingError, match="SUMO binar"):
        view(seeds, arms=[arm("baseline", seeds), arm("I1", seeds, version="1.27.0")])


# --- (b) one timeline member, listed seeds -----------------------------------------------------


def test_a_pair_from_another_timeline_member_is_not_part_of_the_view() -> None:
    elsewhere = PairIdentity.of(
        run("baseline", 1, member_sha256="9" * 64), run("I1", 1, member_sha256="9" * 64)
    )
    with pytest.raises(PairingError, match="timeline member"):
        view((1,), pairs=[elsewhere])


def test_a_pair_under_another_signal_timeline_is_not_part_of_the_view() -> None:
    elsewhere = PairIdentity.of(
        run("baseline", 1, timeline_id="timeline-v1"), run("I1", 1, timeline_id="timeline-v1")
    )
    with pytest.raises(PairingError, match="signal timeline"):
        view((1,), pairs=[elsewhere])


def test_a_pair_on_a_seed_the_view_does_not_list_is_refused() -> None:
    stray = PairIdentity.of(run("baseline", 7), run("I1", 7))
    with pytest.raises(PairingError, match="seed 7"):
        view((1, 2), pairs=[stray])


def test_every_arm_must_have_run_every_seed_of_the_view() -> None:
    with pytest.raises(PairingError, match="I1"):
        view((1, 2), arms=[arm("baseline", (1, 2)), arm("I1", (1,))])


# --- (c) the seed's recorded demand ------------------------------------------------------------


def test_a_pair_whose_demand_is_not_the_seeds_recorded_demand_is_rejected() -> None:
    other = PairIdentity.of(
        run("baseline", 1, demand_sha256="a" * 64), run("I1", 1, demand_sha256="a" * 64)
    )
    with pytest.raises(PairingError, match="demand"):
        view((1,), pairs=[other])


def test_a_pair_whose_population_is_not_the_seeds_population_is_rejected() -> None:
    other = PairIdentity.of(
        run("baseline", 1, population_sha256="a" * 64), run("I1", 1, population_sha256="a" * 64)
    )
    with pytest.raises(PairingError, match="population"):
        view((1,), pairs=[other])


def test_a_view_cannot_be_edited_into_an_inconsistent_one() -> None:
    good = view((1, 2))
    data = json.loads(good.model_dump_json())
    data["seeds"][0]["demand_sha256"] = "a" * 64
    with pytest.raises(ValidationError, match="demand"):
        PairIdentityView.model_validate(data)
    data = json.loads(good.model_dump_json())
    data["horizon_s"] = 900.0
    with pytest.raises(ValidationError, match="does not hash"):
        PairIdentityView.model_validate(data)


# --- (d) the unit of inference -----------------------------------------------------------------


def test_the_replay_arm_label_is_exact() -> None:
    assert unit_of_inference(["3" * 64] * 20) == REPLAY_LABEL
    assert view(tuple(range(1, 21))).unit_of_inference == REPLAY_LABEL


def test_per_seed_populations_and_unread_ones_get_other_labels() -> None:
    assert unit_of_inference([f"{i:064x}" for i in range(5)]) == (
        "n = 5 seeds, one vehicle realisation each"
    )
    unread = unit_of_inference(["3" * 64, None])
    assert unread.startswith("n = 2 seeds") and "not verified" in unread
    assert REPLAY_LABEL not in {unread}


def test_a_view_with_a_rewritten_label_is_refused() -> None:
    data = json.loads(view((1, 2)).model_dump_json())
    data["unit_of_inference"] = "n = 2 independent samples"
    with pytest.raises(ValidationError, match="unit of inference"):
        PairIdentityView.model_validate(data)


# --- (e) the reserved confirmation seeds -------------------------------------------------------


def test_the_confirmation_seeds_are_301_to_320() -> None:
    assert CONFIRMATION_SEEDS == frozenset(range(301, 321))


@pytest.mark.parametrize("seeds", [(301,), (1, 2, 320), (305, 306)])
def test_confirmation_seeds_are_rejected_unless_confirmation_is_passed(seeds) -> None:
    with pytest.raises(ReservedSeedError, match="confirmation"):
        check_seeds(seeds)
    check_seeds(seeds, confirmation=True)
    check_seeds((1, 2, 300, 321))  # the neighbours are not reserved


def test_a_view_over_confirmation_seeds_needs_confirmation() -> None:
    with pytest.raises(ReservedSeedError):
        view((301, 302))
    assert view((301, 302), confirmation=True).confirmation
    data = json.loads(view((301, 302), confirmation=True).model_dump_json())
    data["confirmation"] = False
    with pytest.raises(ValidationError, match="reserved"):  # refused before the hash is checked
        PairIdentityView.model_validate(data)


def test_reserved_seed_errors_are_pairing_errors() -> None:
    assert issubclass(ReservedSeedError, PairingError)


# --- (f) speedFactor stays out of the population -----------------------------------------------


def test_the_population_leaves_out_exactly_the_speed_factor() -> None:
    assert POPULATION_EXCLUDES == (EXCLUDED_ATTRIBUTE,) == ("speedFactor",)


def test_seeds_that_differ_only_in_speed_factors_are_one_realisation(tmp_path) -> None:
    """Two route files whose vehicles differ only in speedFactor, hashed by population-v1: two
    demand files (their bytes carry the draws), one population, so the view is a replay."""
    route = (
        '<routes><vType id="car" length="4.5"/>'
        '<vehicle id="v1" type="car" depart="1.0" departLane="0" speedFactor="{f}">'
        '<route edges="a b"/></vehicle></routes>'
    )
    seeds, pairs = [], []
    for seed, factor in ((1, "1.000"), (2, "1.120")):
        path = tmp_path / f"seed_{seed}.rou.xml"
        path.write_text(route.format(f=factor), encoding="utf-8")
        demand, population = hashlib.sha256(path.read_bytes()).hexdigest(), population_sha256(path)
        seeds.append(SeedDemand(seed=seed, demand_file=path.name, demand_sha256=demand,
                                population_sha256=population,
                                speed_factors_sha256=speed_factors_sha256(path)))  # fmt: skip
        same = dict(demand_sha256=demand, population_sha256=population)
        pairs.append(PairIdentity.of(run("baseline", seed, **same), run("I1", seed, **same)))
    built = view((1, 2), pairs=pairs, seeds=seeds)
    assert len({s.demand_sha256 for s in built.seeds}) == 2
    assert len({s.speed_factors_sha256 for s in built.seeds}) == 2
    assert len({s.population_sha256 for s in built.seeds}) == 1
    assert built.unit_of_inference == "n = 2 seeds on one vehicle realisation; conditional on it"


# --- what the review found the validator must also refuse --------------------------------------


@pytest.mark.parametrize(
    "arm_changes, view_changes, message",
    [
        ({"plan_sha256": "f" * 64}, {}, "plan is not arm 'I1'"),
        ({"network_sha256": "e" * 64}, {}, "network is not arm 'I1'"),
        ({"intervention_sha256": "a" * 64}, {}, "intervention is not arm 'I1'"),
        ({}, {"world_model_sha256": "d" * 64}, "world model is not the view's"),
        ({}, {"network_sha256": "d" * 64}, "view's network is not its baseline arm's"),
    ],
)
def test_pair_sides_must_carry_their_arms_and_the_views_hashes(
    arm_changes, view_changes, message
) -> None:
    seeds = (1, 2)
    candidate = arm("I1", seeds).model_copy(update=arm_changes)
    with pytest.raises(PairingError, match=message):
        view(seeds, arms=[arm("baseline", seeds), candidate], **view_changes)


def test_the_baseline_arm_names_no_intervention() -> None:
    seeds = (1,)
    baseline = arm("baseline", seeds).model_copy(update={"intervention_sha256": "a" * 64})
    with pytest.raises(PairingError, match="baseline arm names an intervention"):
        view(seeds, arms=[baseline, arm("I1", seeds)])


@pytest.mark.parametrize(
    "pairs, message",
    [
        ([], "seed 1: 0 pairs"),
        ([PairIdentity.of(run("baseline", 1), run("I1", 1))], "seed 2: 0 pairs"),
        ([PairIdentity.of(run("I1", 1), run("baseline", 1)),
          PairIdentity.of(run("baseline", 2), run("I1", 2))], "reference is 'I1'"),
        ([PairIdentity.of(run("baseline", 1), run("I1", 1)),
          PairIdentity.of(run("baseline", 1, run_config_sha256="d" * 64),
                          run("I1", 1, run_config_sha256="d" * 64)),
          PairIdentity.of(run("baseline", 2), run("I1", 2))], "seed 1: 2 pairs"),
    ],
)  # fmt: skip
def test_every_candidate_seed_has_exactly_one_pair_with_the_baseline(pairs, message) -> None:
    with pytest.raises(PairingError, match=message):
        view((1, 2), pairs=pairs)


def test_pair_populations_must_be_the_seeds_population_even_when_unread() -> None:
    unread = [PairIdentity.of(run("baseline", 1, population_sha256=None),
                              run("I1", 1, population_sha256=None))]  # fmt: skip
    with pytest.raises(PairingError, match="population"):
        view((1,), pairs=unread)  # the seeds claim a population no pair verified
    with pytest.raises(PairingError, match="population"):
        view((1,), seeds=[seed_demand(1, population=None)])  # pairs claim one the seeds lack


def test_a_reordered_view_is_refused_so_one_content_has_one_hash() -> None:
    data = json.loads(view((1, 2)).model_dump_json())
    data["arms"] = list(reversed(data["arms"]))
    with pytest.raises(ValidationError, match="canonical order"):
        PairIdentityView.model_validate(data)
    data = json.loads(view((1, 2)).model_dump_json())
    data["pairs"] = list(reversed(data["pairs"]))
    with pytest.raises(ValidationError, match="canonical order"):
        PairIdentityView.model_validate(data)
