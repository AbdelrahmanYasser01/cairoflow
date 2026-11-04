"""The Muse frame-annotation request (frame_prompt) and scripts/muse_frame_smoke.py, no network.

The overlay is a synthetic 10-frame clip in a temporary repository with its own minimal demo
manifest pin and licence file; HTTP goes to `httpx.MockTransport`.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import httpx
import pytest
from pydantic import SecretStr

from cairoflow_contracts.config import Settings
from cairoflow_contracts.meta_depth import AnnotatedObject, FrameAnnotation, FrameAnnotationRecord
from cairoflow_reasoning.frame_prompt import (
    FRAME_SCHEMA,
    build_frame_request_body,
    parse_frame_reply,
)
from cairoflow_reasoning.muse_adapter import MuseResponseError
from cairoflow_reasoning.prompts import SCHEMA_KEYWORDS, schema_keywords

REPO = Path(__file__).resolve().parents[1]
FAKE_KEY = "fake-test-key-0123456789"
NOW = datetime(2026, 11, 3, 21, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 3, 21, 5, tzinfo=UTC)
OVERLAY = "experiments/records/ngsim-cam2-sam-test/overlay.mp4"
GOOD = {
    "scene_summary": "A junction seen from above; three cars wait at the stop line.",
    "objects": [
        {"category": "car", "region": "center", "overlay_box": "boxed", "note": "white car"},
        {"category": "bus", "region": "top_left", "overlay_box": "not_boxed", "note": "no box"},
    ],
    "overlay_issues": ["the bus at top left has no box"],
    "image_quality": "clear",
    "limitations": ["one frame shows no motion"],
}


def _script():
    path = REPO / "scripts/muse_frame_smoke.py"
    spec = importlib.util.spec_from_file_location("muse_frame_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reply(text: str, status: str = "completed") -> dict:
    return {
        "id": "resp_frame_1",
        "status": status,
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 1400, "output_tokens": 600},
    }


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    overlay = tmp_path / OVERLAY
    overlay.parent.mkdir(parents=True)
    subprocess.run(
        [
            *("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"),
            *("-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10", "-t", "1"),
            *("-c:v", "libx264", "-pix_fmt", "yuv420p", str(overlay)),
        ],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    sha = hashlib.sha256(overlay.read_bytes()).hexdigest()
    manifest = tmp_path / "data/demo/manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"files": {"sam_overlay": {"path": OVERLAY, "sha256": sha}}}))
    licence = tmp_path / "data/licences/ngsim-lankershim-videos.json"
    licence.parent.mkdir(parents=True)
    terms = {
        "attribution": "Test attribution",
        "licence_name": "CC BY-SA 4.0",
        "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
    }
    licence.write_text(json.dumps(terms))
    return tmp_path


@pytest.fixture()
def settings() -> Settings:
    return Settings(model_api_key=SecretStr(FAKE_KEY))


def _run(repo: Path, args: list[str], **kwargs) -> int:
    return _script().main(args, repo=repo, manifest=repo / "data/demo/manifest.json", **kwargs)


def _no_network() -> httpx.MockTransport:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("this path must not send a request")

    return httpx.MockTransport(refuse)


# ---------- the request ----------


def test_the_schema_is_strict_and_matches_the_contract() -> None:
    assert schema_keywords(FRAME_SCHEMA) <= SCHEMA_KEYWORDS
    assert FRAME_SCHEMA["additionalProperties"] is False
    assert set(FRAME_SCHEMA["required"]) == set(FRAME_SCHEMA["properties"])
    item = FRAME_SCHEMA["properties"]["objects"]["items"]
    for name in ("category", "region", "overlay_box"):
        literal = AnnotatedObject.model_fields[name].annotation
        assert item["properties"][name]["enum"] == list(get_args(literal)), name
    numeric = json.dumps(FRAME_SCHEMA)
    assert '"number"' not in numeric and '"integer"' not in numeric  # no model-made numbers


def test_the_body_carries_the_image_as_documented_and_asks_for_strict_json() -> None:
    body = build_frame_request_body(b"\xff\xd8jpeg-bytes", "muse-spark-1.3")
    message = body["input"][0]
    assert message["type"] == "message" and message["role"] == "user"
    text, image = message["content"]
    assert text["type"] == "input_text" and image["type"] == "input_image"
    head, data = image["image_url"].split(",", 1)
    assert head == "data:image/jpeg;base64" and base64.b64decode(data) == b"\xff\xd8jpeg-bytes"
    assert body["text"]["format"]["strict"] is True and body["store"] is False


def test_a_reply_off_the_schema_is_refused() -> None:
    assert parse_frame_reply(_reply(json.dumps(GOOD))).objects[0].category == "car"
    for bad in ({**GOOD, "image_quality": "great"}, {**GOOD, "extra": 1}, "not json"):
        with pytest.raises(MuseResponseError):
            parse_frame_reply(_reply(bad if isinstance(bad, str) else json.dumps(bad)))
    with pytest.raises(MuseResponseError, match="incomplete"):
        parse_frame_reply(_reply(json.dumps(GOOD), status="incomplete"))


# ---------- the script ----------


def test_a_valid_reply_becomes_an_ok_record_with_the_frame_and_its_notice(
    repo: Path, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_reply(json.dumps(GOOD)), headers={"x-request-id": "r-1"})

    code = _run(repo, ["--spend-limit", "0.20"], settings=settings,
                transport=httpx.MockTransport(handler), now=NOW)  # fmt: skip
    assert code == 0 and len(calls) == 1
    out = repo / "experiments/records/muse-frame-annotation-20261103T210000Z.json"
    record = FrameAnnotationRecord.model_validate_json(out.read_text("utf-8"))
    assert record.status == "ok" and record.annotation == FrameAnnotation.model_validate(GOOD)
    assert record.provenance.kind == "model_generated" and "not ground truth" in record.label
    assert record.frame.frame_index == 5 and record.frame.source_path == OVERLAY
    frame = repo / record.frame.image_path
    assert hashlib.sha256(frame.read_bytes()).hexdigest() == record.frame.image_sha256
    sent = json.loads(calls[0].content)["input"][0]["content"][1]["image_url"]
    assert base64.b64decode(sent.split(",", 1)[1]) == frame.read_bytes()
    assert "CC BY-SA 4.0" in (frame.parent / "NOTICE.md").read_text("utf-8")
    # 1,400 input tokens at $1.25/M + 600 output tokens at $4.25/M
    assert record.recorded_cost_usd == pytest.approx(0.00430)
    assert record.request_id == "r-1" and record.response_id == "resp_frame_1"
    assert FAKE_KEY not in out.read_text("utf-8") and FAKE_KEY not in capsys.readouterr().out

    # the same request again comes from the cache, opens no connection, and is a new record
    assert _run(repo, ["--replay"], settings=settings, transport=_no_network(), now=LATER) == 0
    again = repo / "experiments/records/muse-frame-annotation-20261103T210500Z.json"
    assert FrameAnnotationRecord.model_validate_json(again.read_text()).provenance.cached is True


def test_an_off_schema_reply_is_recorded_as_a_failure_and_kept_for_audit(
    repo: Path, settings: Settings
) -> None:
    bad = json.dumps({**GOOD, "image_quality": "great"})
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=_reply(bad)))
    assert _run(repo, ["--spend-limit", "0.20"], settings=settings,
                transport=transport, now=NOW) == 1  # fmt: skip
    out = repo / "experiments/records/muse-frame-annotation-20261103T210000Z.json"
    record = FrameAnnotationRecord.model_validate_json(out.read_text("utf-8"))
    assert record.status == "failed" and record.annotation is None
    assert "frame schema" in (record.failure or "")
    assert record.cache_path and "failed/" in record.cache_path
    assert record.recorded_cost_usd > 0  # the failed reply was still paid for
    assert not list((repo / "cache/muse").glob("*.json"))  # never cached as a usable reply


def test_an_http_error_is_recorded_as_a_failure(repo: Path, settings: Settings) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "x"}))
    assert _run(repo, ["--spend-limit", "0.20"], settings=settings,
                transport=transport, now=NOW) == 1  # fmt: skip
    out = repo / "experiments/records/muse-frame-annotation-20261103T210000Z.json"
    record = FrameAnnotationRecord.model_validate_json(out.read_text("utf-8"))
    assert record.status == "failed" and "HTTP 400" in (record.failure or "")


def test_a_refused_request_writes_nothing(repo: Path, settings: Settings) -> None:
    code = _run(repo, ["--spend-limit", "0"], settings=settings, transport=_no_network(), now=NOW)
    assert code == 1
    assert not list((repo / "experiments/records").glob("muse-frame-annotation-*"))


def test_an_overlay_that_does_not_match_its_pin_is_refused(repo: Path, settings: Settings) -> None:
    (repo / OVERLAY).write_bytes(b"changed")
    code = _run(repo, ["--spend-limit", "0.20"], settings=settings,
                transport=_no_network(), now=NOW)  # fmt: skip
    assert code == 2
