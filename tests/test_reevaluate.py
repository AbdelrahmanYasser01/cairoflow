"""Re-evaluation inputs (scripts/reevaluate_ngsim_int2_experiment.py) and the baseline assessment
loader (cairoflow_evaluation.report.load_baseline_assessment). Nothing here writes under
experiments/runs: the recorded run's small files are copied into a temporary directory."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from cairoflow_contracts.report import BaselineAssessment
from cairoflow_evaluation.report import load_baseline_assessment

REPO = Path(__file__).resolve().parents[1]
RUN = REPO / "experiments/runs/ngsim-int2-experiment-20261028T084336Z"
SCENARIO = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
ASSESSMENT = REPO / "data/geometry/ngsim-lankershim-int2/baseline_assessment.json"
REEVALUATIONS = sorted((REPO / "experiments/records").glob("ngsim-int2-reevaluation-*.json"))
EXPERIMENT_RECORD = REPO / "experiments/records/ngsim-int2-experiment-20261028T075803Z.json"
MODEL_TEXT_KEYS = {"statement", "rationale", "expected_tradeoffs", "assumptions",
                   "alternative_explanations", "preconditions"}  # fmt: skip


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


@pytest.mark.skipif(not ASSESSMENT.exists(), reason="no committed baseline assessment")
def test_the_committed_assessment_is_about_the_recorded_baseline() -> None:
    """It loads, names the scenario record's baseline, its evidence hashes as committed, and it
    does not call the baseline validated (DATA_REGISTER.md §17)."""
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    assessment = load_baseline_assessment(ASSESSMENT, scenario["baseline"]["manifest_sha256"], REPO)
    assert assessment is not None and assessment.status == "not_validated"
    assert len(assessment.evidence) >= 6 and assessment.findings


@pytest.mark.skipif(not REEVALUATIONS, reason="no committed re-evaluation record")
def test_the_committed_reevaluation_uses_the_recorded_runs_and_selects_nothing() -> None:
    record = json.loads(REEVALUATIONS[-1].read_text("utf-8"))
    assert not (_keys(record) & MODEL_TEXT_KEYS)  # no model-written text is committed
    assert record["rules_version"] == "0.2.0" and record["label"] == "simulated"
    report = record["report"]
    assert report["trace_problems"] == []
    assert report["selected_intervention_id"] is None
    assert report["recommended_intervention_id"] is None
    assert report["baseline_assessment_status"] == "not_validated"
    statuses = {e["candidate"]: e["status"] for e in record["eligibility"]}
    assert statuses == {"I1": "incomplete", "I2": "compared", "I3": "incomplete"}
    # the same SUMO runs as the committed experiment record, seed by seed
    recorded = json.loads(EXPERIMENT_RECORD.read_text("utf-8"))
    expected = sorted(r["tripinfo_sha256"] for c in recorded["experiment"]["candidates"]
                      for r in c["runs"])  # fmt: skip
    found = sorted(digest for name, digest in record["tripinfo"].items() if "/candidates/" in name)
    assert found == expected


def _script():
    path = REPO / "scripts" / "reevaluate_ngsim_int2_experiment.py"
    spec = importlib.util.spec_from_file_location("reevaluate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["reevaluate"] = module
    spec.loader.exec_module(module)
    return module


def _assessment(baseline: str, evidence: dict[str, str]) -> str:
    return BaselineAssessment(
        baseline_manifest_sha256=baseline, status="not_validated", summary="s", findings=("f",),
        evidence=evidence, assessed_on="2026-10-28", documented_in="docs/DATA_REGISTER.md",
    ).model_dump_json()  # fmt: skip


def test_an_assessment_must_be_about_this_baseline_and_its_evidence(tmp_path: Path) -> None:
    (tmp_path / "evidence.json").write_text("{}", encoding="utf-8")
    digest = hashlib.sha256(b"{}").hexdigest()
    path = tmp_path / "assessment.json"
    assert load_baseline_assessment(path, "b" * 64, tmp_path) is None  # none written yet

    path.write_text(_assessment("b" * 64, {"evidence.json": digest}), encoding="utf-8")
    assert load_baseline_assessment(path, "b" * 64, tmp_path).status == "not_validated"
    with pytest.raises(ValueError, match="assesses another baseline"):
        load_baseline_assessment(path, "c" * 64, tmp_path)
    (tmp_path / "evidence.json").write_text('{"edited": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="evidence evidence.json is missing or changed"):
        load_baseline_assessment(path, "b" * 64, tmp_path)


@pytest.mark.skipif(not RUN.is_dir(), reason="recorded experiment run not present (Git-ignored)")
def test_the_recorded_run_is_checked_against_its_report(tmp_path: Path) -> None:
    s = _script()
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    copy = tmp_path / "run"
    (copy / "experiment").mkdir(parents=True)
    for name in (*s.CHECKED, "report.json"):
        shutil.copyfile(RUN / name, copy / name)
    found = s.check_run(copy, scenario)
    assert set(found) == set(s.CHECKED)

    outcomes = copy / "outcomes.json"
    outcomes.write_text(outcomes.read_text("utf-8").replace("accepted", "rejected", 1), "utf-8")
    with pytest.raises(s.InputError, match="outcomes.json does not match"):
        s.check_run(copy, scenario)
    other = scenario | {"baseline": scenario["baseline"] | {"manifest_sha256": "0" * 64}}
    shutil.copyfile(RUN / "outcomes.json", outcomes)
    with pytest.raises(s.InputError, match="another baseline"):
        s.check_run(copy, other)
