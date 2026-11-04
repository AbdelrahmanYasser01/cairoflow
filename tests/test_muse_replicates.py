"""scripts/run_muse_replicates.py: the committed request sent again unchanged (no network)."""

from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from cairoflow_contracts.config import Settings
from cairoflow_reasoning.muse_adapter import MuseRequestRecord

REPO = Path(__file__).resolve().parents[1]
KEY = "ddfb8029b1e8810e65b2ad986e0da99be630b86e6d7c86669007e2a197ef47ad"
COMMITTED = REPO / "cache/muse" / f"{KEY}.json"
FAKE_KEY = "fake-test-key-0123456789"
NOW = datetime(2026, 11, 3, 22, 0, tzinfo=UTC)

pytestmark = pytest.mark.skipif(not COMMITTED.is_file(), reason="committed Muse reply absent")


def _script():
    path = REPO / "scripts/run_muse_replicates.py"
    spec = importlib.util.spec_from_file_location("run_muse_replicates", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "cache/muse").mkdir(parents=True)
    shutil.copyfile(COMMITTED, tmp_path / "cache/muse" / COMMITTED.name)
    return tmp_path


def _settings() -> Settings:
    return Settings(model_api_key=SecretStr(FAKE_KEY))


def test_each_replicate_resends_the_identical_body_and_gets_its_own_record(repo: Path) -> None:
    committed = MuseRequestRecord.model_validate_json(COMMITTED.read_text())
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, text=committed.response_text)

    code = _script().main(["--replicates", "2", "3", "--spend-limit", "0.20"],
                          settings=_settings(), transport=httpx.MockTransport(handler),
                          repo=repo, now=NOW)  # fmt: skip
    assert code == 0 and len(sent) == 2
    assert all(body == committed.body for body in sent)  # no prompt or parameter changed
    for n in (2, 3):
        record = json.loads((repo / f"experiments/records/muse-0.1.0-r{n}-20261103T220000Z.json")
                            .read_text())  # fmt: skip
        assert record["status"] == "ok" and record["replicate"] == n
        assert record["request_key"] == KEY and record["prompt_version"] == "0.1.0"
        assert (repo / record["cache"]["path"]).name == f"{KEY}.r{n}.json"
        assert FAKE_KEY not in (repo / record["cache"]["path"]).read_text()


def test_a_reply_off_the_schema_is_a_failure_record_and_is_never_cached(repo: Path) -> None:
    bad = {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": "{}"}]}], "usage": {"input_tokens": 10}}  # fmt: skip
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=bad))
    code = _script().main(["--replicates", "2", "--spend-limit", "0.20"], settings=_settings(),
                          transport=transport, repo=repo, now=NOW)  # fmt: skip
    assert code == 1
    record = json.loads((repo / "experiments/records/muse-0.1.0-r2-20261103T220000Z.json")
                        .read_text())  # fmt: skip
    assert record["status"] == "failed" and "schema" in record["failure"]
    assert not (repo / "cache/muse" / f"{KEY}.r2.json").exists()


def test_without_a_spend_limit_nothing_is_sent_or_written(repo: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not send")

    code = _script().main(["--replicates", "2", "--spend-limit", "0"], settings=_settings(),
                          transport=httpx.MockTransport(refuse), repo=repo, now=NOW)  # fmt: skip
    assert code == 1 and not (repo / "experiments/records").exists()


def test_replicate_1_is_the_committed_reply_and_cannot_be_resent(repo: Path) -> None:
    with pytest.raises(SystemExit):
        _script().main(["--replicates", "1", "--replay"], settings=_settings(), repo=repo)
