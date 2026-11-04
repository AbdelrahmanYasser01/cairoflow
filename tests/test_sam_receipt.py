"""scripts/sam_receipt.py: one SAM 3.1 call through the cache-first adapter, and its receipt.

No network: HTTP goes to `httpx.MockTransport`, a transport that fails on use guards the paths that
must not send, and the clip is a 3 s synthetic one (30 frames at 10 fps, like the camera excerpt).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from synthetic_sam import sse, token

from cairoflow_contracts.config import Settings
from cairoflow_contracts.evidence import ClipLicense
from cairoflow_perception.ingest import ingest_clip

REPO = Path(__file__).resolve().parents[1]
FAKE_KEY = "fake-test-key-0123456789"
NOW = datetime(2026, 11, 3, 20, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 3, 20, 5, tzinfo=UTC)


def _script():
    spec = importlib.util.spec_from_file_location("sam_receipt", REPO / "scripts/sam_receipt.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def clip_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("clip")
    source = work / "source.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"),
            *("-f", "lavfi", "-i", "testsrc2=size=256x256:rate=10", "-t", "3"),
            *("-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)),
        ],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    licence = ClipLicense(
        source_id="synthetic-testsrc2",
        source_url="https://ffmpeg.org/ffmpeg-filters.html#testsrc2",
        licence_name="generated in code",
        attribution="CairoFlow synthetic test clip",
        accessed_on=date(2026, 11, 3),
        register_ref="tests/test_sam_receipt.py",
        permits_commercial_use=True,
        permits_cloud_processing=True,
        permits_redistribution=True,
        share_alike=False,
        synthetic=True,
    )
    ingest_clip(source, licence, work / "ingest", now=NOW)
    return work / "ingest"


@pytest.fixture()
def settings() -> Settings:
    return Settings(model_api_key=SecretStr(FAKE_KEY))


def _no_network() -> httpx.MockTransport:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("this path must not send a request")

    return httpx.MockTransport(refuse)


def _stream(frames: int = 30) -> str:
    return "\n".join(sse([token(f, 0, 10 + f, 20) for f in range(frames)])) + "\n"


def _run(clip_dir: Path, tmp_path: Path, args: list[str], **kwargs) -> int:
    return _script().main(
        [str(clip_dir), *args],
        cache_dir=tmp_path / "cache",
        records_dir=tmp_path / "records",
        **kwargs,
    )


def _records(tmp_path: Path) -> list[Path]:
    folder = tmp_path / "records"
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def test_one_call_prints_the_receipt_and_writes_a_new_record(
    clip_dir: Path, tmp_path: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text=_stream(), headers={"x-request-id": "req-7"})

    code = _run(clip_dir, tmp_path, ["--spend-limit", "0.05"], settings=settings,
                transport=httpx.MockTransport(handler), now=NOW)  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0 and len(calls) == 1
    assert json.loads(calls[0].content)["input"][0]["content"][0]["text"] == "car"

    for line in ("model: sam-3.1", f"endpoint: {calls[0].url}", "30 in the clip", "30 billed",
                 "observations: 30", "recorded cost: $0.0100"):  # fmt: skip
        assert line in out, line
    records = _records(tmp_path)
    assert [p.name for p in records] == ["sam-receipt-20261103T200000Z.json"]
    receipt = json.loads(records[0].read_text("utf-8"))
    assert receipt["model"] == "sam-3.1" and receipt["endpoint"] == str(calls[0].url)
    assert receipt["request_id"] == "req-7" and receipt["from_cache"] is False
    assert receipt["clip"]["frames"] == 30 and receipt["frames_billed"] == 30
    assert receipt["observations"] == 30 and receipt["outcome"] == "completed"
    # 30 frames, billed at the 50-frame minimum: $0.20 per 1,000 frames
    assert receipt["recorded_cost_usd"] == pytest.approx(0.01)
    assert receipt["spent_this_run_usd"] == pytest.approx(0.01)
    stream = tmp_path / "cache" / f"{receipt['request_key']}.json"
    assert stream.is_file()
    assert receipt["stream"]["sha256"] == hashlib.sha256(stream.read_bytes()).hexdigest()
    assert FAKE_KEY not in out and FAKE_KEY not in records[0].read_text("utf-8")
    assert FAKE_KEY not in stream.read_text("utf-8")


def test_a_second_run_replays_the_cached_stream_and_pays_nothing(
    clip_dir: Path, tmp_path: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=_stream()))
    assert _run(clip_dir, tmp_path, ["--spend-limit", "0.05"], settings=settings,
                transport=transport, now=NOW) == 0  # fmt: skip
    assert _run(clip_dir, tmp_path, ["--replay"], settings=settings,
                transport=_no_network(), now=LATER) == 0  # fmt: skip
    second = json.loads((tmp_path / "records/sam-receipt-20261103T200500Z.json").read_text())
    assert second["from_cache"] is True and second["spent_this_run_usd"] == 0.0
    assert second["recorded_cost_usd"] == pytest.approx(0.01)  # what the cached request cost
    assert "from cache: True" in capsys.readouterr().out


def test_a_zero_spend_limit_refuses_before_sending_and_writes_nothing(
    clip_dir: Path, tmp_path: Path, settings: Settings
) -> None:
    code = _run(clip_dir, tmp_path, ["--spend-limit", "0"], settings=settings,
                transport=_no_network(), now=NOW)  # fmt: skip
    assert code == 1 and _records(tmp_path) == []


def test_an_existing_record_is_never_overwritten_and_nothing_is_sent(
    clip_dir: Path, tmp_path: Path, settings: Settings
) -> None:
    taken = tmp_path / "records/sam-receipt-20261103T200000Z.json"
    taken.parent.mkdir(parents=True)
    taken.write_text("{}\n", encoding="utf-8")
    code = _run(clip_dir, tmp_path, ["--spend-limit", "0.05"], settings=settings,
                transport=_no_network(), now=NOW)  # fmt: skip
    assert code == 2 and taken.read_text(encoding="utf-8") == "{}\n"


def test_an_http_error_writes_no_receipt_and_keeps_the_failure_for_audit(
    clip_dir: Path, tmp_path: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(503, text="busy"))
    code = _run(clip_dir, tmp_path, ["--spend-limit", "0.05"], settings=settings,
                transport=transport, now=NOW)  # fmt: skip
    assert code == 1 and "server" in capsys.readouterr().err
    assert _records(tmp_path) == []
    assert list((tmp_path / "cache/failed").glob("*.json"))
