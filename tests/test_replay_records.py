"""Replay-baseline records (ADR-022): each committed assessment is about the baseline its file is
named after (scripts/run_ngsim_int2_realism_experiment.py), with its evidence as committed; and
the validation report states the cross-street entry rule the baseline really used; and the
committed version 2 timeline is the one its estimate record describes (DATA_REGISTER.md §22)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from cairoflow_contracts.evidence import SignalTimeline
from cairoflow_evaluation.report import load_baseline_assessment

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "data/geometry/ngsim-lankershim-int2"
ASSESSMENTS = sorted((RECORD / "replay_assessments").glob("*.json"))
ESTIMATE_V2 = RECORD / "signal_estimate_v2.json"


@pytest.mark.skipif(not ESTIMATE_V2.exists(), reason="no version 2 timeline committed")
def test_the_version_2_timeline_matches_its_estimate_and_the_record() -> None:
    estimate = json.loads(ESTIMATE_V2.read_text(encoding="utf-8"))
    path = RECORD / estimate["timeline_file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == estimate["timeline_sha256"]
    timeline = SignalTimeline.model_validate_json(path.read_text(encoding="utf-8"))
    v1 = RECORD / "signal_timeline.json"
    scenario = json.loads((RECORD / "scenario.json").read_text(encoding="utf-8"))
    assert estimate["inputs"] == {
        "scenario_record_sha256": hashlib.sha256((RECORD / "scenario.json").read_bytes())
        .hexdigest(),
        "world_model_sha256": scenario["processed_outputs"]["sha256"]["world_model.json"],
        "signal_timeline_v1_sha256": hashlib.sha256(v1.read_bytes()).hexdigest(),
    }  # fmt: skip
    one = SignalTimeline.model_validate_json(v1.read_text(encoding="utf-8"))
    assert estimate["reproduces_v1"] and estimate["conflicting_seconds"] == []
    assert timeline.timeline_id == f"{one.timeline_id}-v2" and timeline.label == one.label
    assert timeline.heads() == one.heads() and timeline.window_s == one.window_s
    assert estimate["revision"]["version"] == "2"


@pytest.mark.parametrize("path", ASSESSMENTS, ids=lambda p: p.stem)
def test_each_replay_assessment_is_about_its_recorded_baseline(path: Path) -> None:
    record_name = f"experiments/records/{path.name}"
    record = json.loads((REPO / record_name).read_text(encoding="utf-8"))
    assessment = load_baseline_assessment(path, record["baseline_manifest_sha256"], REPO)
    assert assessment is not None and assessment.findings
    assert record_name in assessment.evidence


def _validation_script():
    path = REPO / "scripts" / "validate_ngsim_int2_baseline.py"
    spec = importlib.util.spec_from_file_location("validate_int2", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["validate_int2"] = module
    spec.loader.exec_module(module)
    return module


def test_the_cross_street_caveat_follows_the_vehicle_tables_options() -> None:
    caveat = _validation_script().cross_street_caveat
    plain = caveat({"upstream_within_m": 40.0})
    anchored = caveat({"upstream_within_m": 40.0, "discharge_anchored": True, "arrival_gap_s": 2})
    assert "timed to reach that spot at the speed limit" in plain
    assert "joins the queue" not in plain
    assert "joins the queue of the red before it, 2 s apart in crossing order" in anchored
    assert "window start" not in anchored
    queued = caveat({"discharge_anchored": True, "queued_at_start": True})
    assert queued.startswith(anchored.removesuffix("; their arrival times are not observed"))
    assert "at or before the window start enters at rest at 0 s at the stop line" in queued
    assert caveat({}) == plain  # tables written before the options existed: the §18 rule
