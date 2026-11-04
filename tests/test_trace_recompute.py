"""The trace recomputes what it certifies: pairs, Holm family, reserved seeds, records.

tests/test_pipeline.py already holds the audit's cases: the consistent forgery of I1's mean delay
(baseline minus 25 s on every seed, comparisons and Holm rebuilt, cited files unchanged), the seven
report edits (kind, label and unit, appended number, every verdict 'improves', limitation,
objective, borrowed count tracks) and the honest run tracing with no problem. This file adds what
it lacks:

- the comparison output a run script records lists, per comparison, the PairIdentity of every
  paired seed and the unit of inference those pairs give, with the view that names them;
- the Holm family is fixed by the design (the two manifests) before any candidate result is read,
  and the trace refuses improvement tests over another family;
- the trace rebuilds the pairs with their populations required, so a demand file that is gone
  fails it (the recomputed evaluation only logs that population as unverified);
- seeds 301-320 are refused by the report and by run_candidates unless confirmation=True (nothing
  is simulated on them: the refusal comes before any run);
- every committed record whose trace was empty is traced again under the stricter check, and none
  is dropped: each gets verified, failed (with its problems) or unverifiable (with why);
- a retrace certifies what the record says, not only its metrics: the report it names is hashed,
  its selection, recommendation, eligibility, comparisons (in either recorded form) and Holm tests
  are compared with the recomputation, and the report is traced again from the inputs it was
  assembled with. Each forgery the review made fails; a record whose inputs are gone is
  unverifiable, never verified; the outcome of every committed record is pinned.

Everything runs offline: Muse answers through httpx.MockTransport with a fake key, once.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, TypeAdapter
from synthetic_pipeline import FAKE_KEY, MuseStub, refuse, write_inputs

from cairoflow_contracts.config import Settings
from cairoflow_contracts.evidence import TrafficWorldModel
from cairoflow_contracts.experiment import BaselineManifest, EvaluationResult, ExperimentManifest
from cairoflow_contracts.pair_identity import ReservedSeedError, unit_of_inference
from cairoflow_contracts.reasoning import EvidenceBundle, ReasoningResult, ValidationOutcome
from cairoflow_contracts.report import DecisionReport
from cairoflow_evaluation.compare import EvaluationError, improvement_plan
from cairoflow_evaluation.known_answer import comparison_rows
from cairoflow_evaluation.pair_identity import experiment_pair_view
from cairoflow_evaluation.pipeline import run_pipeline
from cairoflow_evaluation.report import (
    REPRODUCIBILITY_KEYS,
    RETRACE_STATUSES,
    assemble_report,
    comparison_output,
    comparison_record,
    empty_trace_records,
    holm_family,
    retrace_record,
    retrace_records,
    trace_report,
)
from cairoflow_simulation.candidates import run_candidates

REPO = Path(__file__).resolve().parents[1]
RECORDS = REPO / "experiments/records"


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("trace")
    inputs = write_inputs(root)
    run_pipeline(
        inputs,
        root / "live",
        mode="live",
        settings=Settings(model_api_key=SecretStr(FAKE_KEY)),
        spend_limit_usd=1.0,
        sam_transport=httpx.MockTransport(refuse),
        muse_transport=httpx.MockTransport(MuseStub()),
    )
    run_pipeline(inputs, root / "reference", mode="replay")
    return root / "reference"


def _inputs(run_dir: Path) -> dict:
    return {
        "world_model": TrafficWorldModel.model_validate_json(
            (run_dir / "world_model.json").read_text()
        ),
        "outcomes": TypeAdapter(tuple[ValidationOutcome, ...]).validate_json(
            (run_dir / "outcomes.json").read_text()
        ),
        "experiment": ExperimentManifest.model_validate_json(
            (run_dir / "experiment/experiment_manifest.json").read_text()
        ),
        "evaluation": EvaluationResult.model_validate_json(
            (run_dir / "experiment/evaluation.json").read_text()
        ),
        "bundle": EvidenceBundle.model_validate_json((run_dir / "bundle.json").read_text()),
        "reasoning": ReasoningResult.model_validate_json((run_dir / "reasoning.json").read_text()),
    }


def _trace(run_dir: Path, **overrides) -> tuple[str, ...]:
    report = DecisionReport.model_validate_json((run_dir / "report.json").read_text())
    values = _inputs(run_dir) | {"experiment_dir": run_dir / "experiment"} | overrides
    return trace_report(report, **values)


def _baseline_dir(run_dir: Path) -> Path:
    experiment = _inputs(run_dir)["experiment"]
    return (run_dir / "experiment" / experiment.baseline_dir).resolve()


# --- the comparison output names its pairs and its unit of inference ---------------------------


def test_the_comparison_output_lists_the_pair_of_every_compared_seed(reference: Path) -> None:
    experiment_dir = reference / "experiment"
    evaluation = _inputs(reference)["evaluation"]
    view = experiment_pair_view(experiment_dir)
    out = comparison_output(experiment_dir, evaluation)

    assert out["pair_identity"]["view_sha256"] == view.view_sha256
    assert out["pair_identity"]["unit_of_inference"] == view.unit_of_inference
    assert out["pair_identity"]["sumo_binary_version"] == view.sumo_binary_version
    listed = out["pair_identity"]["pairs"]
    assert [p["pair_sha256"] for p in listed] == list(view.pair_ids())
    assert {(p["arm"], p["seed"]) for p in listed} == {
        (c.intervention_id, seed.seed) for c in _inputs(reference)["experiment"].candidates
        for seed in view.seeds
    }  # fmt: skip
    by_key = {(p.compared.arm, p.compared.seed): p.pair_sha256 for p in view.pairs}
    population = {s.seed: s.population_sha256 for s in view.seeds}
    assert len(out["comparisons"]) == len(evaluation.comparisons) > 0
    for entry, c in zip(out["comparisons"], evaluation.comparisons, strict=True):
        assert entry["pair_sha256"] == [by_key[(c.candidate, s)] for s in c.seeds]
        assert entry["unit_of_inference"] == unit_of_inference([population[s] for s in c.seeds])
        recorded = {k: v for k, v in entry.items() if k not in ("pair_sha256", "unit_of_inference")}
        assert recorded == c.model_dump(mode="json")  # the comparison itself is unchanged


def test_the_synthetic_seeds_are_one_realisation_each(reference: Path) -> None:
    out = comparison_output(reference / "experiment", _inputs(reference)["evaluation"])
    assert out["pair_identity"]["unit_of_inference"] == "n = 5 seeds, one vehicle realisation each"
    assert {c["unit_of_inference"] for c in out["comparisons"]} == {
        "n = 5 seeds, one vehicle realisation each"
    }


# --- the Holm family is frozen by the design ----------------------------------------------------


def test_the_holm_family_is_fixed_before_any_candidate_result_is_read(
    reference: Path, tmp_path: Path
) -> None:
    evaluation = _inputs(reference)["evaluation"]
    family = holm_family(reference / "experiment")
    assert family == improvement_plan(evaluation)
    assert sorted(family) == sorted((t.candidate, t.metric_name)
                                    for t in evaluation.improvement_tests)  # fmt: skip
    # no candidate output is needed: the family comes from the manifests alone
    copy = tmp_path / "run"
    shutil.copytree(reference, copy)
    shutil.rmtree(copy / "experiment" / "candidates")
    assert holm_family(copy / "experiment") == family
    out = comparison_output(reference / "experiment", evaluation)
    assert out["holm_family"] == [list(k) for k in family]


def test_the_comparison_output_holds_the_family_frozen_before_the_results(
    reference: Path,
) -> None:
    """A run script reads the family from the manifests before it evaluates (`family=`); the
    comparison output records that family and refuses improvement tests over any other."""
    experiment_dir = reference / "experiment"
    evaluation = _inputs(reference)["evaluation"]
    frozen = holm_family(experiment_dir)
    out = comparison_output(experiment_dir, evaluation, family=frozen)
    assert out["holm_family"] == [list(k) for k in frozen]
    with pytest.raises(EvaluationError, match="Holm family"):
        comparison_output(experiment_dir, evaluation, family=frozen[1:])
    dropped = evaluation.model_copy(update={"improvement_tests": evaluation.improvement_tests[1:]})
    with pytest.raises(EvaluationError, match="Holm family"):
        comparison_output(experiment_dir, dropped, family=frozen)


def test_a_run_records_why_its_comparisons_have_no_pairs_instead_of_unpaired_ones(
    reference: Path, tmp_path: Path
) -> None:
    experiment_dir = reference / "experiment"
    evaluation = _inputs(reference)["evaluation"]
    frozen = holm_family(experiment_dir)
    assert comparison_record(experiment_dir, evaluation, family=frozen) == comparison_output(
        experiment_dir, evaluation, family=frozen
    )
    copy = tmp_path / "run"
    shutil.copytree(reference, copy)
    (_baseline_dir(copy) / "demand" / "seed_1.rou.xml").unlink()
    record = comparison_record(copy / "experiment", evaluation, family=frozen)
    assert record["comparisons"] == [] and record["pair_identity"] is None
    assert "seed 1" in record["comparison_error"] and "missing" in record["comparison_error"]
    assert record["holm_family"] == [list(k) for k in frozen]
    wrong = comparison_record(experiment_dir, evaluation, family=frozen[1:])
    assert wrong["comparisons"] == [] and "Holm family" in wrong["comparison_error"]


def test_improvement_tests_over_another_family_fail_the_trace(reference: Path) -> None:
    evaluation = _inputs(reference)["evaluation"]
    dropped = evaluation.model_copy(update={"improvement_tests": evaluation.improvement_tests[1:]})
    problems = _trace(reference, evaluation=dropped)
    assert any("Holm family" in p for p in problems), problems
    assert _trace(reference) == ()


# --- the trace rebuilds the pairs ---------------------------------------------------------------


def test_a_demand_file_that_is_gone_fails_the_trace(reference: Path, tmp_path: Path) -> None:
    """compute_evaluation only logs a missing route file as an unverified population (pairing.py,
    for records older than the module), so the recomputed evaluation still matched and the trace
    passed. The pairs are rebuilt with their populations required: the trace now fails."""
    copy = tmp_path / "run"
    shutil.copytree(reference, copy)
    (_baseline_dir(copy) / "demand" / "seed_1.rou.xml").unlink()
    problems = _trace(copy)
    assert any(p.startswith("pair identity:") and "seed 1" in p and "missing" in p
               for p in problems), problems  # fmt: skip


def test_a_demand_file_that_changed_still_fails_the_recomputation(
    reference: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "run"
    shutil.copytree(reference, copy)
    demand = _baseline_dir(copy) / "demand" / "seed_1.rou.xml"
    demand.write_text(demand.read_text("utf-8") + "<!-- edited -->\n", "utf-8")
    problems = _trace(copy)
    assert len(problems) == 1 and "seed 1: demand file seed_1.rou.xml changed" in problems[0]


# --- the reserved confirmation seeds ------------------------------------------------------------


def test_the_report_refuses_reserved_seeds_unless_confirmed(reference: Path) -> None:
    values = _inputs(reference)
    experiment = values["experiment"]
    reserved = experiment.model_copy(
        update={"settings": experiment.settings.model_copy(update={"seeds": (301, 302, 303)})}
    )
    report = DecisionReport.model_validate_json((reference / "report.json").read_text())
    kwargs = dict(
        world_model=values["world_model"], bundle=values["bundle"],
        reasoning=values["reasoning"], outcomes=values["outcomes"], experiment=reserved,
        evaluation=values["evaluation"], now=report.created_at_utc,
        artifacts={k: v for k, v in report.reproducibility.items()
                   if k not in REPRODUCIBILITY_KEYS},
    )  # fmt: skip
    with pytest.raises(ReservedSeedError, match="confirmation"):
        assemble_report(**kwargs)
    assert assemble_report(**kwargs, confirmation=True).experiment_id == experiment.experiment_id
    problems = _trace(reference, experiment=reserved)
    assert any("reserved confirmation seeds" in p for p in problems), problems


def test_run_candidates_refuses_reserved_seeds_before_running_anything(
    reference: Path, tmp_path: Path
) -> None:
    baseline = tmp_path / "baseline"
    shutil.copytree(_baseline_dir(reference), baseline)
    path = baseline / "baseline_manifest.json"
    manifest = BaselineManifest.model_validate_json(path.read_text("utf-8"))
    edited = json.loads(manifest.model_dump_json())
    edited["settings"]["seeds"] = [301, 302, 303, 304, 305]
    path.write_text(json.dumps(edited), "utf-8")
    out = tmp_path / "experiment"
    with pytest.raises(ReservedSeedError, match="confirmation"):
        run_candidates(baseline, _inputs(reference)["outcomes"], out)
    assert not out.exists()  # refused before anything was simulated


# --- every committed record with an empty trace, traced again -----------------------------------


def _empty_trace_records() -> list[Path]:
    return empty_trace_records(RECORDS)


def test_twenty_committed_records_carry_an_empty_trace() -> None:
    """The brief counted 18; the committed records hold 20, and all 20 are traced again."""
    assert len(_empty_trace_records()) == 20


def test_a_record_without_its_run_directory_is_unverifiable_never_dropped(tmp_path: Path) -> None:
    results = retrace_records(_empty_trace_records(), tmp_path)  # tmp_path holds no run dirs
    assert [r["record"] for r in results] == [p.name for p in _empty_trace_records()]
    assert {r["status"] for r in results} == {"unverifiable"}
    assert all(r["missing"] for r in results)


def _runs_present() -> bool:
    return all((REPO / json.loads(p.read_text("utf-8"))["run_dir"]).is_dir()
               for p in _empty_trace_records())  # fmt: skip


def _inputs_present() -> bool:
    """The run directories and the intersection 2 world model the realism reports were assembled
    with (data/processed is Git-ignored)."""
    scenario = json.loads((REPO / "data/geometry/ngsim-lankershim-int2/scenario.json").read_text())
    world = REPO / scenario["processed_outputs"]["dir"] / "world_model.json"
    return _runs_present() and world.is_file()


# What the m2b retrace finds for each committed record with an empty trace, with every run and
# input present. Verified: under rules 0.3.1 the record's report, selection, recommendation,
# eligibility, comparisons and Holm tests were all re-derived and the report traced again.
# Unverifiable: evaluated under older rules, so only its metrics and pairs could be re-derived.
EXPECTED_RETRACE = {
    "cairoflow-20261030T213806Z-r1.json": "verified",
    "cairoflow-20261030T213929Z-r2.json": "verified",
    "cairoflow-20261030T214027Z-r3.json": "verified",
    "cairoflow-20261030T235016Z-r1.json": "verified",
    "ngsim-int2-experiment-20261028T075803Z.json": "unverifiable",
    "ngsim-int2-realism-experiment-20261028T130511Z.json": "unverifiable",
    "ngsim-int2-realism-experiment-20261028T132818Z.json": "unverifiable",
    "ngsim-int2-realism-experiment-20261028T135556Z.json": "unverifiable",
    "ngsim-int2-realism-experiment-20261028T142330Z.json": "unverifiable",
    "ngsim-int2-realism-experiment-20261029T115716Z.json": "unverifiable",
    "ngsim-int2-realism-experiment-20261029T115735Z.json": "unverifiable",
    "ngsim-int2-realism-experiment-20261029T115755Z.json": "unverifiable",
    "ngsim-int2-realism-experiment-20261029T115813Z.json": "unverifiable",
    "ngsim-int2-realism-experiment-20261029T122910Z.json": "verified",
    "ngsim-int2-realism-experiment-20261029T122930Z.json": "verified",
    "ngsim-int2-realism-experiment-20261029T122948Z.json": "verified",
    "ngsim-int2-realism-experiment-20261029T123007Z.json": "verified",
    "ngsim-int2-reevaluation-20261028T122501Z.json": "unverifiable",
    "synthetic-known-answer-long-cycle-20261029T123354Z.json": "verified",
    "synthetic-known-answer-reallocation-20261029T123315Z.json": "verified",
}


@pytest.mark.skipif(not _inputs_present(), reason="the recorded runs and inputs are Git-ignored")
def test_every_committed_record_is_traced_again_and_its_failures_are_kept() -> None:
    results = retrace_records(_empty_trace_records(), REPO)
    assert len(results) == 20
    assert {r["record"]: r["status"] for r in results} == EXPECTED_RETRACE
    for result in results:
        assert result["status"] in RETRACE_STATUSES
        if result["status"] == "failed":
            assert result["problems"], result["record"]
        if result["status"] == "unverifiable":
            assert result["notes"] or result["missing"], result["record"]
            assert result["checks"]["report traced"] is not True, result["record"]
        if result["status"] == "verified":
            assert not result["problems"] and result["pair_identity"] is not None
            assert result["checks"]["report traced"] is True, result["record"]
            assert result["checks"]["comparisons re-derived"] is True, result["record"]


@pytest.mark.skipif(not _inputs_present(), reason="the recorded runs and inputs are Git-ignored")
def test_a_realism_record_under_the_current_rules_is_verified_with_its_pairs() -> None:
    result = retrace_record(RECORDS / "ngsim-int2-realism-experiment-20261029T123007Z.json", REPO)
    assert result["status"] == "verified", result["problems"]
    assert result["pair_identity"]["unit_of_inference"] == (
        "n = 20 seeds on one vehicle realisation; conditional on it"
    )
    assert result["pair_identity"]["pairs"] == 3 * 20


@pytest.mark.skipif(not _runs_present(), reason="the recorded run directories are Git-ignored")
def test_a_record_under_older_rules_has_its_metrics_checked_and_says_what_is_not() -> None:
    result = retrace_record(RECORDS / "ngsim-int2-realism-experiment-20261028T142330Z.json", REPO)
    assert result["status"] in ("unverifiable", "failed")
    assert any("rules 0.2.0" in note for note in result["notes"]), result
    assert result["checks"]["metrics recomputed"] is not None


# --- a retrace certifies what the record says ---------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_of(run_dir: Path, root: Path, *, rows: bool = False) -> dict:
    """A record of `run_dir` (under `root`) as run_ngsim_int2_realism_experiment.py writes one;
    with `rows`, in the known-answer form: the comparisons as the rows the demo displays, the Holm
    tests and the eligibility nested."""
    evaluation = _inputs(run_dir)["evaluation"]
    report = DecisionReport.model_validate_json((run_dir / "report.json").read_text())
    eligibility = [e.model_dump(mode="json") for e in evaluation.eligibility]
    tests = [t.model_dump(mode="json") for t in evaluation.improvement_tests]
    record: dict = {
        "script": "tests/test_trace_recompute.py",
        "run_dir": run_dir.relative_to(root).as_posix(),
        "report": {
            "selected_intervention_id": report.selected_intervention_id,
            "selection_reason": report.selection_reason,
            "recommended_intervention_id": report.recommended_intervention_id,
            "recommendation": report.recommendation,
            "trace_problems": [],
            "report_json_sha256": _sha256(run_dir / "report.json"),
            "report_md_sha256": _sha256(run_dir / "report.md"),
        },
    }
    if rows:
        record |= {
            "comparisons": {"kind": "simulated", "rows": comparison_rows(evaluation)},
            "holm": {"kind": "simulated", "tests": tests},
            "eligibility": {
                "kind": "simulated",
                "candidates": eligibility,
                "baseline_censored_lanes": evaluation.baseline_censored_lanes,
                "baseline_entry_blocked": evaluation.baseline_entry_blocked,
            },
        }
    else:
        family = holm_family(run_dir / "experiment")
        record |= {
            "eligibility": eligibility,
            **comparison_record(run_dir / "experiment", evaluation, family=family),
            "improvement_tests": tests,
        }
    return json.loads(json.dumps(record))


def _retrace(record: dict, root: Path, where: Path) -> dict:
    path = where / "record.json"
    path.write_text(json.dumps(record), "utf-8")
    return retrace_record(path, root)


def _other(candidate: str | None) -> str:
    return "I2" if candidate == "I1" else "I1"


@pytest.mark.parametrize("rows", [False, True], ids=["evaluation form", "known-answer rows"])
def test_an_honest_record_is_verified_once_its_report_is_traced_again(
    reference: Path, tmp_path: Path, rows: bool
) -> None:
    result = _retrace(_record_of(reference, reference.parent, rows=rows), reference.parent,
                      tmp_path)  # fmt: skip
    assert result["status"] == "verified", result
    assert result["checks"]["report traced"] is True
    assert result["checks"]["comparisons re-derived"] is True
    assert not any("another form" in note for note in result["notes"])


FORGERIES: dict[str, tuple[Callable[[dict], dict], str]] = {
    "selection": (
        lambda s: s | {"selected_intervention_id": _other(s["selected_intervention_id"])},
        "the record's selected_intervention_id is not the report's",
    ),
    "selection reason": (
        lambda s: s | {"selection_reason": "I3 ranks first."},
        "the record's selection_reason is not the report's",
    ),
    "recommendation": (
        lambda s: (
            s
            | {
                "recommended_intervention_id": "I3",
                "recommendation": "I3 is recommended: selected under a validated baseline.",
            }
        ),
        "the record's recommended_intervention_id is not the report's",
    ),  # fmt: skip
    "report hash": (
        lambda s: s | {"report_json_sha256": "0" * 64},
        "report.json does not hash as the record says",
    ),
    "assessment": (
        lambda s: s | {"baseline_assessment": {"status": "validated"}},
        "the record's baseline assessment status is not the report's",
    ),
}


@pytest.mark.parametrize("forgery", sorted(FORGERIES))
def test_a_record_that_misstates_its_report_fails(
    reference: Path, tmp_path: Path, forgery: str
) -> None:
    edit, problem = FORGERIES[forgery]
    record = _record_of(reference, reference.parent)
    record["report"] = edit(record["report"])
    result = _retrace(record, reference.parent, tmp_path)
    assert result["status"] == "failed"
    assert problem in result["problems"], result["problems"]


def test_a_report_edited_with_its_record_fails_when_traced_again(
    reference: Path, tmp_path: Path
) -> None:
    """The record agrees with the edited report and hashes it: only re-deriving the selection
    and recommendation, and tracing the report again, can show the forgery."""
    copy = tmp_path / "run"
    shutil.copytree(reference, copy)
    path = copy / "report.json"
    report = json.loads(path.read_text("utf-8"))
    forged = _other(report["selected_intervention_id"])
    report |= {
        "selected_intervention_id": forged,
        "recommended_intervention_id": forged,
        "recommendation": f"{forged} is recommended: selected under a validated baseline.",
    }
    path.write_text(json.dumps(report, indent=2) + "\n", "utf-8")  # fmt: skip
    result = _retrace(_record_of(copy, tmp_path), tmp_path, tmp_path)
    assert result["status"] == "failed"
    assert "the selection does not follow from the evaluation" in result["problems"]
    assert "the recommendation does not follow from the baseline assessment" in result["problems"]


@pytest.mark.parametrize("rows", [False, True], ids=["evaluation form", "known-answer rows"])
def test_a_record_whose_eligibility_or_holm_tests_were_edited_fails(
    reference: Path, tmp_path: Path, rows: bool
) -> None:
    record = _record_of(reference, reference.parent, rows=rows)
    eligibility = record["eligibility"]["candidates"] if rows else record["eligibility"]
    assert eligibility
    first = eligibility[0]
    first["status"] = "incomplete" if first["status"] == "compared" else "compared"
    result = _retrace(record, reference.parent, tmp_path)
    assert result["status"] == "failed"
    assert "the record's eligibility is not the one recomputed" in result["problems"]

    record = _record_of(reference, reference.parent, rows=rows)
    tests = record["holm"]["tests"] if rows else record["improvement_tests"]
    assert tests
    tests[0]["confirmed"] = not tests[0]["confirmed"]
    result = _retrace(record, reference.parent, tmp_path)
    assert result["status"] == "failed"
    assert "the record's improvement tests are not the ones recomputed" in result["problems"]


def test_the_rows_a_known_answer_record_displays_are_rederived(
    reference: Path, tmp_path: Path
) -> None:
    record = _record_of(reference, reference.parent, rows=True)
    row = next(r for r in record["comparisons"]["rows"] if r["metric"] == "mean_delay_s")
    row |= {"mean_difference": -99.0, "ci95_difference": [-120.0, -80.0]}
    result = _retrace(record, reference.parent, tmp_path)
    assert result["status"] == "failed"
    assert (
        "the record's comparisons are not the ones recomputed from the SUMO outputs"
        in (result["problems"])
    )

    record["comparisons"] = {"kind": "simulated", "table": "a form this code cannot read"}
    result = _retrace(record, reference.parent, tmp_path)
    assert result["status"] == "unverifiable"  # an unread claim is never certified
    assert result["checks"]["comparisons re-derived"] is False
    assert any("cannot be read" in note for note in result["notes"]), result["notes"]


def test_a_record_whose_report_inputs_are_gone_is_unverifiable_never_verified(
    reference: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "run"
    shutil.copytree(reference, copy)
    record = _record_of(copy, tmp_path)
    (copy / "world_model.json").unlink()
    result = _retrace(record, tmp_path, tmp_path)
    assert result["status"] == "unverifiable"
    assert result["problems"] == []
    assert result["checks"]["report traced"] is False
    assert any("world model" in item for item in result["missing"]), result["missing"]
    # what can still be re-derived is: a forged selection fails without the world model
    record["report"] |= {"selected_intervention_id": _other(record["report"][
        "selected_intervention_id"])}  # fmt: skip
    assert _retrace(record, tmp_path, tmp_path)["status"] == "failed"


def _forged_copy(name: str, edit: Callable[[dict], None], where: Path) -> Path:
    record = json.loads((RECORDS / name).read_text("utf-8"))
    edit(record)
    path = where / name  # the scratch copy; the committed record is never touched
    path.write_text(json.dumps(record), "utf-8")
    return path


def _forge_cairoflow(record: dict) -> None:
    record["report"] |= {"selected": "I3", "recommended": "I3", "report_json_sha256": "0" * 64,
                         "recommendation": "I3 is recommended: selected under a validated "
                         "baseline."}  # fmt: skip


def _forge_realism(record: dict) -> None:
    record["report"] |= {"selected_intervention_id": "I1", "recommended_intervention_id": "I1"}


def _forge_rows(record: dict) -> None:
    row = next(r for r in record["comparisons"]["rows"]
               if r["candidate"] == "I5" and r["metric"] == "mean_delay_s")  # fmt: skip
    row |= {"mean_difference": -99.0, "ci95_difference": [-120.0, -80.0]}
    record["report"] |= {"recommended_intervention_id": "I5",
                         "recommendation": "I5 is recommended: selected under a validated "
                         "baseline."}  # fmt: skip


@pytest.mark.skipif(not _inputs_present(), reason="the recorded runs and inputs are Git-ignored")
@pytest.mark.parametrize(
    ("name", "edit"),
    [
        ("cairoflow-20261030T213806Z-r1.json", _forge_cairoflow),
        ("ngsim-int2-realism-experiment-20261029T123007Z.json", _forge_realism),
        ("synthetic-known-answer-long-cycle-20261029T123354Z.json", _forge_rows),
    ],
    ids=["pipeline", "realism", "known-answer rows"],
)
def test_the_reviews_forged_copies_of_committed_records_fail(
    tmp_path: Path, name: str, edit: Callable[[dict], None]
) -> None:
    result = retrace_record(_forged_copy(name, edit, tmp_path), REPO)
    assert result["status"] == "failed", result
    assert result["problems"]


# --- the retrace outcome is recorded ------------------------------------------------------------


def test_the_retrace_outcome_is_written_as_a_new_record_and_keeps_every_failure(
    reference: Path, tmp_path: Path
) -> None:
    """scripts/reevaluate_committed_traces.py records what the retrace found for every record with
    an empty trace (the committed records' `trace_problems: []` stand qualified by it): a failed
    record is kept with its problems, and an existing outcome file is never overwritten."""
    path = REPO / "scripts" / "reevaluate_committed_traces.py"
    spec = importlib.util.spec_from_file_location("reevaluate_committed_traces", path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    records = tmp_path / "records"
    records.mkdir()
    honest = _record_of(reference, reference.parent)
    forged = json.loads(json.dumps(honest))
    forged["report"]["selected_intervention_id"] = _other(
        honest["report"]["selected_intervention_id"]
    )
    (records / "a-honest.json").write_text(json.dumps(honest), "utf-8")
    (records / "b-forged.json").write_text(json.dumps(forged), "utf-8")
    (records / "c-unrelated.json").write_text(json.dumps({"report": {"trace_problems": ["x"]}}))
    out = tmp_path / "retrace.json"
    argv = ["--out", str(out), "--records-dir", str(records), "--root", str(reference.parent)]
    assert script.main(argv) == 1  # a record failed
    written = json.loads(out.read_text("utf-8"))
    assert written["script"] == "scripts/reevaluate_committed_traces.py"
    assert written["records_traced"] == 2
    assert written["statuses"] == {"failed": 1, "verified": 1}
    assert [(r["record"], r["status"]) for r in written["results"]] == [
        ("a-honest.json", "verified"),
        ("b-forged.json", "failed"),
    ]
    assert written["results"][1]["problems"]
    assert written["code"]["rules_version"]
    with pytest.raises(FileExistsError):
        script.main(argv)
