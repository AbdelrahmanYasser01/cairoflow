"""The D2 search and held-out evaluation runners refuse what the protocol forbids (no SUMO here)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from cairoflow_contracts.experiment import BaselineManifest
from cairoflow_contracts.integrity import canonical_sha256

REPO = Path(__file__).resolve().parents[1]
MEMBER = REPO / "experiments/runs/ngsim-int2-realism-baseline-20261028T135241Z"
OTHER = REPO / "experiments/runs/ngsim-int2-realism-baseline-20261028T141919Z"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


search = _script("run_d2_search_experiment")
ladder = _script("run_ladder_evaluation_experiment")


@pytest.mark.parametrize("seeds", [(1,), (201,), (301,), (100, 101), ()])
def test_the_search_takes_only_its_seeds(seeds) -> None:
    with pytest.raises(ValueError, match="101-108"):
        search.check_seeds(seeds, search.SEARCH_SEEDS, "D2 search")
    search.check_seeds((101, 108), search.SEARCH_SEEDS, "D2 search")


@pytest.mark.parametrize("seeds", [(101,), (301,), (200, 201), (221,)])
def test_the_evaluation_takes_only_the_held_out_seeds(seeds) -> None:
    with pytest.raises(ValueError, match="201-220"):
        search.check_seeds(seeds, ladder.HELD_OUT_SEEDS, "held-out")


def test_the_search_main_refuses_other_seeds_before_any_work(capsys) -> None:
    assert search.main(["--seeds", "201", "--dry-run"]) == 2
    assert "101-108" in capsys.readouterr().err


def _record(tmp_path: Path, **values) -> Path:
    body = {"arms": {"D1": {"plan_sha256": "a" * 64}}, "seeds": [201]} | values
    path = tmp_path / "record.json"
    path.write_text(json.dumps(body | {"record_sha256": canonical_sha256(body)}), "utf-8")
    return path


def test_a_record_is_read_only_while_its_hash_holds(tmp_path: Path) -> None:
    path = _record(tmp_path)
    assert ladder.checked_record(path)["seeds"] == [201]
    tampered = json.loads(path.read_text("utf-8"))
    tampered["seeds"] = [201, 202]
    path.write_text(json.dumps(tampered), "utf-8")
    with pytest.raises(ValueError, match="does not hash"):
        ladder.checked_record(path)


def test_a_pre_registration_outside_the_repository_or_uncommitted_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError):
        ladder.committed_unchanged(_record(tmp_path))
    assert ladder.main(["run", str(_record(tmp_path))]) == 2


def test_a_committed_file_that_is_not_a_record_is_refused(capsys) -> None:
    assert ladder.main(["run", str(REPO / "tests/fixtures/eq1_cut_program.json")]) == 2
    assert "record_sha256" in capsys.readouterr().err


FROZEN = {  # committed records of the D2 search and the pre-registration: any edit fails here
    "d2-search-20261102T232051Z.json": (
        "f731ca0d7a4c94d4fad0e609478de596475ac96fb3c0380556ff57ff91e14ce4"
    ),
    "d2-plan-20261102T232051Z.json": (
        "799261df95d0fd319e5262c6d23458d464956f66cc845ed111801b3727516f88"
    ),
    "ladder-preregistration-20261102T233412Z.json": (
        "3dd8da2488aa48588896af7f3fefaa739723a8e9db0f6dd11b353f8538da1d17"
    ),
    "ladder-evaluation-20261102T233511Z.json": (
        "59907b34a884f83eb74529ef509de1e97fac2641fc7923c0eddc11351d1b4ad7"
    ),
    "ladder-preregistration-20261103T001537Z.json": (  # eq1-v2
        "c0e4a769b050b204b83a6b431969ec33f9702b9ec6159f52ff3c7a7aa76d0401"
    ),
    "ladder-evaluation-20261103T001603Z.json": (  # eq1-v2
        "dbd840104e33a98cd40638da57d089871f2d5470f2792469675909478cb48984"
    ),
}


@pytest.mark.parametrize("name", sorted(FROZEN))
def test_the_frozen_records_still_hash_to_the_pinned_value(name: str) -> None:
    record = ladder.checked_record(REPO / "experiments/records" / name)
    assert record["record_sha256"] == FROZEN[name]


def test_the_plan_record_names_the_search_record_it_came_from() -> None:
    records = REPO / "experiments/records"
    plan = ladder.checked_record(records / "d2-plan-20261102T232051Z.json")
    assert plan["search_record_sha256"] == search.sha256_file(records / plan["search_record"])
    assert (plan["outcome"], plan["reported"], plan["plan_sha256"]) == (
        "no_feasible_plan", "not_testable", None
    )  # fmt: skip
    prereg = ladder.checked_record(records / "ladder-preregistration-20261102T233412Z.json")
    assert prereg["arms"]["D2"] == {"reported": "not_testable", "source": plan_name()}
    assert prereg["arms"]["D1"]["plan_sha256"].startswith("60bc41d3")
    assert prereg["seeds"] == list(range(201, 221))


def plan_name() -> str:
    return "d2-plan-20261102T232051Z.json"


@pytest.mark.skipif(not (MEMBER.is_dir() and OTHER.is_dir()), reason="member baselines Git-ignored")
def test_a_baseline_is_its_member_only_with_the_same_network_and_settings() -> None:
    recorded = BaselineManifest.model_validate_json(
        (MEMBER / "baseline_manifest.json").read_bytes()
    )
    assert search.same_member(recorded, MEMBER) == []
    reseeded = recorded.model_copy(
        update={"settings": recorded.settings.model_copy(update={"seeds": (101, 102)})}
    )
    assert search.same_member(reseeded, MEMBER) == []  # seeds are allowed to differ
    other = BaselineManifest.model_validate_json((OTHER / "baseline_manifest.json").read_bytes())
    assert search.same_member(other, MEMBER) != []
