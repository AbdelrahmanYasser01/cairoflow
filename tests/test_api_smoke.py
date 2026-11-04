"""Offline tests of the spend-guarded API smoke script. No test opens a network connection:
requests go to httpx.MockTransport, and the dry-run test fails if a client is ever created."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import cv2
import httpx
import pytest
from pydantic import SecretStr

from cairoflow_contracts.config import Settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "api_smoke_test.py"
FAKE_KEY = "fake-test-key-0123456789"
URL = "https://api.example.test/v1/responses"


def _load_script():
    spec = importlib.util.spec_from_file_location("api_smoke_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


smoke = _load_script()


@pytest.fixture()
def settings() -> Settings:
    return Settings(model_api_key=SecretStr(FAKE_KEY))


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(smoke, "CACHE_DIR", tmp_path / "smoke")
    return tmp_path / "smoke"


def _client(handler, calls: list[httpx.Request]) -> httpx.Client:
    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(record))


def test_dry_run_creates_no_client(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(smoke, "load_settings", lambda: settings)

    def forbidden(*args, **kwargs):
        raise AssertionError("a dry run must not create an HTTP client")

    monkeypatch.setattr(smoke.httpx, "Client", forbidden)
    assert smoke.main(["--case", "muse-structured", "--case", "sam-video"]) == 0


def test_default_cases_are_the_recorded_pair() -> None:
    assert smoke.DEFAULT_CASES == ("muse-text", "sam-image")


def test_video_cost_applies_the_50_frame_minimum() -> None:
    assert smoke.sam_video_cost_usd(10) == pytest.approx(50 * 0.20 / 1000)
    assert smoke.sam_video_cost_usd(60) == pytest.approx(60 * 0.20 / 1000)


def test_synthetic_clip_decodes_with_expected_shape(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    path.write_bytes(smoke.make_test_mp4())
    capture = cv2.VideoCapture(str(path))
    frames = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames += 1
        assert frame.shape == (smoke.VIDEO_SIZE_PX, smoke.VIDEO_SIZE_PX, 3)
    capture.release()
    assert frames == smoke.VIDEO_FRAMES


def test_structured_schema_follows_the_documented_strict_subset(settings: Settings) -> None:
    body = smoke.muse_structured_body(settings)
    schema = body["text"]["format"]["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert body["max_output_tokens"] == smoke.MUSE_STRUCTURED_MAX_OUTPUT_TOKENS


@pytest.mark.parametrize(
    ("status", "kind"), [(401, "auth"), (402, "billing"), (429, "rate_limited"), (503, "server")]
)
def test_error_is_classified_sent_once_and_scrubbed(
    settings: Settings, cache_in_tmp: Path, status: int, kind: str
) -> None:
    calls: list[httpx.Request] = []
    echo_key = json.dumps({"error": {"message": f"bad key {FAKE_KEY}"}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=echo_key, headers={"x-request-id": "req-123"})

    case = smoke.build_case("muse-structured", settings)
    with _client(handler, calls) as client:
        ok, cost = smoke.run_one(case, client, URL, settings, smoke.make_scrubber(FAKE_KEY))
    assert not ok and cost is None
    assert len(calls) == 1  # no retry, whatever the status
    assert smoke.classify_status(status) == kind
    cached = next(cache_in_tmp.glob("*.json")).read_text(encoding="utf-8")
    assert FAKE_KEY not in cached
    assert "req-123" in cached  # the request id is kept for support tickets


def test_structured_success_is_validated_and_costed(settings: Settings) -> None:
    reply = {"longest_queue_approach": "east", "confidence_label": "high"}
    payload = {
        "id": "resp_1",
        "model": "muse-spark-1.3",
        "status": "completed",
        "output": [{"content": [{"type": "output_text", "text": json.dumps(reply)}]}],
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }
    calls: list[httpx.Request] = []
    case = smoke.build_case("muse-structured", settings)
    with _client(lambda request: httpx.Response(200, json=payload), calls) as client:
        ok, cost = smoke.run_one(case, client, URL, settings, smoke.make_scrubber(FAKE_KEY))
    assert ok
    assert cost == pytest.approx(100 * 1.25e-6 + 50 * 4.25e-6)
    sent = json.loads(calls[0].content)
    assert sent["text"]["format"]["strict"] is True


def test_incomplete_muse_response_is_a_failure(settings: Settings) -> None:
    payload = {"status": "incomplete", "output": [], "usage": {}}
    calls: list[httpx.Request] = []
    case = smoke.build_case("muse-text", settings)
    with _client(lambda request: httpx.Response(200, json=payload), calls) as client:
        ok, _ = smoke.run_one(case, client, URL, settings, smoke.make_scrubber(FAKE_KEY))
    assert not ok


@pytest.mark.parametrize(("reported", "passes"), [(False, True), (True, False), (None, False)])
def test_store_false_passes_only_when_the_response_says_so(
    settings: Settings, tmp_path, monkeypatch, reported, passes
) -> None:
    """D9: one tiny request, under the approved $0.001, that asks Muse not to store it."""
    monkeypatch.setattr(smoke, "CACHE_DIR", tmp_path)
    case = smoke.build_case("muse-store-false", settings)
    assert case.body["store"] is False and case.worst_case_usd < 0.001
    assert "store" not in smoke.build_case("muse-text", settings).body  # unchanged
    payload = {"status": "completed", "store": reported, "usage": {"input_tokens": 10,
               "output_tokens": 5}, "output": [{"content": [{"type": "output_text",
                                                             "text": "ok"}]}]}  # fmt: skip
    calls: list[httpx.Request] = []
    with _client(lambda request: httpx.Response(200, json=payload), calls) as client:
        ok, _ = smoke.run_one(case, client, URL, settings, smoke.make_scrubber(FAKE_KEY))
    assert ok is passes and json.loads(calls[0].content)["store"] is False


def test_structured_validation_rejects_extra_keys() -> None:
    text = '{"longest_queue_approach": "east", "confidence_label": "high", "x": 1}'
    assert smoke.validate_muse_structured(text)["schema_valid"] is False


def test_video_scoring_accepts_exact_boxes_and_rejects_shifted_ones() -> None:
    exact = []
    shifted = []
    for index in range(smoke.VIDEO_FRAMES):
        x1, y1, x2, y2 = smoke.square_box(index)
        exact.append((index, "0", x1, y1, x2 + 1, y2 + 1))
        shifted.append((index, "0", x1 + 30, y1, x2 + 31, y2 + 1))
    result = smoke.score_video_boxes(exact)
    assert result["frames_with_box"] == smoke.VIDEO_FRAMES
    assert result["distinct_object_ids"] == 1
    assert result["mean_iou"] == pytest.approx(1.0)
    assert result["passed"]
    assert not smoke.score_video_boxes(shifted)["passed"]
    assert not smoke.score_video_boxes([])["passed"]


def test_video_validation_fails_closed_on_malformed_records() -> None:
    # The official parser rejects a box record without its mask; the check must not pass.
    text = "<0f>0<|box;x1=16;y1=104;x2=63;y2=151;w=256;h=256|>\n"
    result = smoke.validate_sam_video(text)
    assert result["diagnostics"] == 1
    assert result["passed"] is False
