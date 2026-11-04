"""Experiment identity and verification (P0-2) on the synthetic whole-flow run.

The run comes from synthetic_pipeline.py through run_pipeline (Muse answered by a mock transport,
no network). An honest run verifies, SUMO rerun included. Forged metrics, an edited
configuration, a missing artifact and a report without its sources do not.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from synthetic_pipeline import FAKE_KEY, MuseStub, refuse, write_inputs

from cairoflow_contracts.config import Settings
from cairoflow_contracts.experiment import EvaluationResult
from cairoflow_contracts.integrity import ExperimentIdentity
from cairoflow_evaluation.integrity import (
    IDENTITY_FILE,
    build_identity,
    metric_problems,
    verify_run,
    write_identity,
)
from cairoflow_evaluation.pipeline import run_pipeline


def _identify(run: Path) -> ExperimentIdentity:
    names = ("world_model", "bundle", "reasoning", "outcomes")
    identity = build_identity(
        run,
        experiment_dir=run / "experiment",
        inputs={name: run / f"{name}.json" for name in names},
        report_files={"report_json": run / "report.json", "report_md": run / "report.md"},
    )
    write_identity(run, identity)
    return identity


@pytest.fixture(scope="module")
def honest(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("integrity")
    run = root / "run"
    run_pipeline(write_inputs(root), run, mode="live",
                 settings=Settings(model_api_key=SecretStr(FAKE_KEY)), spend_limit_usd=1.0,
                 sam_transport=httpx.MockTransport(refuse),
                 muse_transport=httpx.MockTransport(MuseStub()))  # fmt: skip
    _identify(run)
    return run


def _copy(honest: Path, tmp_path: Path) -> Path:
    copy = tmp_path / "run"
    shutil.copytree(honest, copy)
    return copy


def test_an_honest_run_verifies_with_a_simulator_rerun(honest: Path) -> None:
    verdict = verify_run(honest, rerun_seeds=1)
    assert verdict.status == "verified", verdict.problems
    assert [c.name for c in verdict.checks] == [
        "artifact hashes", "recomputed evaluation and rebuilt report", "simulator rerun"
    ]  # fmt: skip
    identity = ExperimentIdentity.model_validate_json((honest / IDENTITY_FILE).read_text())
    roles = {a.role for a in identity.artifacts}
    assert roles == {"config", "input", "simulator_output", "metrics", "report"}
    assert identity.simulator["sumo"].startswith("Eclipse SUMO sumo")


def test_a_forged_metric_fails(honest: Path, tmp_path: Path) -> None:
    run = _copy(honest, tmp_path)
    path = run / "experiment/evaluation.json"
    data = json.loads(path.read_text())
    data["metrics"][0]["value"] -= 25.0  # the SUMO file it cites is untouched
    path.write_text(json.dumps(data, indent=2))
    verdict = verify_run(run)
    assert verdict.status == "failed"
    assert any("evaluation (experiment/evaluation.json) changed" in p for p in verdict.problems)
    assert any(p.startswith("evaluation: metric baseline/") for p in verdict.problems)
    assert metric_problems(EvaluationResult.model_validate_json(path.read_text()),
                           run / "experiment")  # fmt: skip


def test_an_edited_configuration_fails_even_with_every_hash_rewritten(
    honest: Path, tmp_path: Path
) -> None:
    run = _copy(honest, tmp_path)
    net = run / "experiment/candidates/I1/network/network.net.xml"
    text = net.read_text()
    assert 'duration="' in text
    net.write_text(text.replace('duration="', 'duration="1', 1))
    assert verify_run(run).status == "failed"  # its recorded hash no longer matches

    # Rewrite the identity so that every hash matches the edited file: the identity validates,
    # but the simulator, run again on the edited network, no longer gives the recorded output.
    (run / IDENTITY_FILE).unlink()
    _identify(run)
    verdict = verify_run(run, rerun_seeds=1)
    assert verdict.status == "failed"
    assert any(p.startswith("I1 seed") and "rerun" in p for p in verdict.problems)


def test_a_hand_edited_identity_is_rejected(honest: Path, tmp_path: Path) -> None:
    run = _copy(honest, tmp_path)
    data = json.loads((run / IDENTITY_FILE).read_text())
    data["settings"]["horizon_s"] += 1  # the identity hash is not recomputed
    (run / IDENTITY_FILE).write_text(json.dumps(data))
    verdict = verify_run(run)
    assert verdict.status == "failed"
    assert "identity_sha256 does not hash" in verdict.problems[0]


def test_missing_sources_make_a_report_unverifiable(honest: Path, tmp_path: Path) -> None:
    run = _copy(honest, tmp_path)
    next(run.rglob("candidates/I2/runs/seed_3/queue.xml")).unlink()
    verdict = verify_run(run)
    assert verdict.status == "unverifiable"
    assert verdict.missing == ("queue:I2:3 (experiment/candidates/I2/runs/seed_3/queue.xml)",)

    alone = tmp_path / "alone"
    alone.mkdir()
    shutil.copy(honest / "report.json", alone / "report.json")  # a report and nothing else
    verdict = verify_run(alone)
    assert verdict.status == "unverifiable" and verdict.missing == (IDENTITY_FILE,)
