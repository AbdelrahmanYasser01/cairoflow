"""SAM stream parsing and the SAM adapter, without network access.

Streams come from two places: the real `sam-video` smoke response cached on 2026-10-27
(git-ignored; those tests skip when it is absent) and synthetic event streams built here in the
same shape. HTTP goes to `httpx.MockTransport`; a transport that fails on use guards the paths
that must not send anything.
"""

from __future__ import annotations

import base64
import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from synthetic_sam import sse as _sse
from synthetic_sam import token as _token

from cairoflow_contracts.config import Settings
from cairoflow_contracts.evidence import ClipLicense, SourceClip
from cairoflow_perception.ingest import OUTPUT_FILE, ingest_clip
from cairoflow_perception.sam_adapter import (
    SamAdapterError,
    SamCacheMiss,
    SamClient,
    SamRequestError,
    SamSpendRefused,
    video_cost_usd,
)
from cairoflow_perception.sam_parse import SamStreamError, decode_sse, parse_sam_stream
from cairoflow_perception.tracking import build_tracks

REPO = Path(__file__).resolve().parents[1]
SMOKE_VIDEO = REPO / "cache" / "smoke" / "20261027T125802Z_sam-video.json"
FAKE_KEY = "fake-test-key-0123456789"
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


# ---------- fixtures ----------


@pytest.fixture(scope="module")
def clip_and_path(tmp_path_factory: pytest.TempPathFactory) -> tuple[SourceClip, Path]:
    """A 60-frame, 10 fps, 256x256 ingested clip: the same frame grid as the smoke clip."""
    work = tmp_path_factory.mktemp("clip")
    source = work / "source.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"),
            *("-f", "lavfi", "-i", "testsrc2=size=256x256:rate=10", "-t", "6"),
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
        accessed_on=date(2026, 10, 27),
        register_ref="tests/test_sam.py",
        permits_commercial_use=True,
        permits_cloud_processing=True,
        permits_redistribution=True,
        share_alike=False,
        synthetic=True,
    )
    clip = ingest_clip(source, licence, work / "ingest", now=NOW)
    return clip, work / "ingest" / OUTPUT_FILE


@pytest.fixture()
def settings() -> Settings:
    return Settings(model_api_key=SecretStr(FAKE_KEY))


def _parse(lines: list[str], clip: SourceClip, prompt: str = "car"):
    return parse_sam_stream(
        lines, clip, prompt=prompt, model="sam-3.1", request_key="k" * 64, sent_at_utc=NOW,
        cached=True,
    )  # fmt: skip


def _no_network() -> httpx.MockTransport:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("this path must not send a request")

    return httpx.MockTransport(refuse)


# ---------- parsing: the real cached stream ----------


@pytest.mark.skipif(not SMOKE_VIDEO.is_file(), reason="cached sam-video response not present")
def test_cached_real_stream_parses_into_one_stable_object(
    clip_and_path: tuple[SourceClip, Path],
) -> None:
    clip, _ = clip_and_path
    lines = json.loads(SMOKE_VIDEO.read_text(encoding="utf-8"))["response"]["lines"]
    parsed = _parse(lines, clip, prompt="red square")

    assert parsed.outcome == "completed"
    assert parsed.parser_diagnostics == () and parsed.dropped == ()
    assert len(parsed.observations) == 60
    assert {o.object_id for o in parsed.observations} == {"red_square:0"}
    assert [o.frame.frame_index for o in parsed.observations] == list(range(60))
    for obs in parsed.observations:
        index = obs.frame.frame_index
        left = 16 + 2 * index  # the square drawn by scripts/api_smoke_test.py
        assert obs.bbox_xyxy_px == (left, 104, left + 48, 152)
        assert obs.confidence == pytest.approx(0.9416, abs=1e-3)
        assert obs.mask_artifact_uri and obs.mask_artifact_uri.startswith("cache://sam/")
        assert obs.provenance.kind == "model_generated" and obs.provenance.cached


@pytest.mark.skipif(not SMOKE_VIDEO.is_file(), reason="cached sam-video response not present")
def test_cached_real_stream_becomes_one_track(clip_and_path: tuple[SourceClip, Path]) -> None:
    clip, _ = clip_and_path
    lines = json.loads(SMOKE_VIDEO.read_text(encoding="utf-8"))["response"]["lines"]
    result = build_tracks(_parse(lines, clip, prompt="red square").observations, now=NOW)
    (track,) = result.tracks
    assert track.track_id == "red_square:t1" and track.sam_object_ids == ("red_square:0",)
    assert len(track.points) == 60 and track.bridged_gaps == 0
    assert result.duplicates == result.links == result.short_tracks == ()


# ---------- parsing: synthetic streams ----------


def test_synthetic_stream_maps_frames_and_namespaces_ids(
    clip_and_path: tuple[SourceClip, Path],
) -> None:
    clip, _ = clip_and_path
    tokens = [_token(f, 0, 10 + f, 20) for f in range(5)] + [_token(f, 7, 100, 120) for f in (2, 3)]
    parsed = _parse(_sse(tokens), clip)
    assert {o.object_id for o in parsed.observations} == {"car:0", "car:7"}
    for obs in parsed.observations:
        assert obs.frame == clip.frames[obs.frame.frame_index]  # timestamps come from the clip
        assert obs.class_label == "car"
        assert obs.provenance.input_sha256 == (clip.output_sha256,)


def test_records_outside_the_clip_are_dropped_with_a_reason(
    clip_and_path: tuple[SourceClip, Path],
) -> None:
    clip, _ = clip_and_path
    tokens = [_token(1, 0, 10, 10), _token(99, 0, 10, 10), _token(2, 1, 300, 300, w=512, h=512)]
    parsed = _parse(_sse(tokens), clip)
    assert [o.frame.frame_index for o in parsed.observations] == [1]
    assert any("frame 99 is not in clip" in reason for reason in parsed.dropped)
    assert any("exceeds the 256x256 frame" in reason for reason in parsed.dropped)


def test_incomplete_stream_is_kept_but_marked(clip_and_path: tuple[SourceClip, Path]) -> None:
    clip, _ = clip_and_path
    parsed = _parse(_sse([_token(0, 0, 10, 10)], terminal="incomplete"), clip)
    assert parsed.outcome == "incomplete"
    assert parsed.incomplete_detail == "max_output_tokens"


@pytest.mark.parametrize("terminal", ["failed", None])
def test_failed_or_truncated_stream_raises(
    clip_and_path: tuple[SourceClip, Path], terminal: str | None
) -> None:
    clip, _ = clip_and_path
    with pytest.raises(SamStreamError):
        _parse(_sse([_token(0, 0, 10, 10)], terminal=terminal), clip)


def test_empty_result_is_valid_and_yields_no_observations(
    clip_and_path: tuple[SourceClip, Path],
) -> None:
    clip, _ = clip_and_path
    parsed = _parse(_sse([]), clip)
    assert parsed.outcome == "completed" and parsed.observations == ()


def test_non_json_event_and_bad_prompt_are_rejected(
    clip_and_path: tuple[SourceClip, Path],
) -> None:
    clip, _ = clip_and_path
    with pytest.raises(SamStreamError, match="not a JSON event"):
        decode_sse(["data: {not json"])
    with pytest.raises(ValueError, match="lowercase"):
        _parse(_sse([]), clip, prompt="Car")


# ---------- adapter ----------


def test_replay_miss_raises_without_network(
    settings: Settings, tmp_path: Path, clip_and_path: tuple[SourceClip, Path]
) -> None:
    clip, path = clip_and_path
    client = SamClient(settings, tmp_path, "replay", transport=_no_network())
    with pytest.raises(SamCacheMiss):
        client.segment_video(clip, path, "car")


def test_live_without_a_spend_limit_is_refused(
    settings: Settings, tmp_path: Path, clip_and_path: tuple[SourceClip, Path]
) -> None:
    clip, path = clip_and_path
    client = SamClient(settings, tmp_path, "live", transport=_no_network())
    with pytest.raises(SamSpendRefused, match="spend limit"):
        client.segment_video(clip, path, "car")
    keyless = SamClient(Settings(), tmp_path, "live", spend_limit_usd=1.0, transport=_no_network())
    with pytest.raises(SamSpendRefused, match="no API key"):
        keyless.segment_video(clip, path, "car")


def test_live_sends_once_caches_and_then_replays(
    settings: Settings, tmp_path: Path, clip_and_path: tuple[SourceClip, Path]
) -> None:
    clip, path = clip_and_path
    calls: list[httpx.Request] = []
    stream = "\n".join(_sse([_token(f, 0, 10 + f, 20) for f in range(3)])) + "\n"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text=stream, headers={"x-request-id": "req-1"})

    limit = video_cost_usd(clip.output_stream.frame_count)
    transport = httpx.MockTransport(handler)
    live = SamClient(settings, tmp_path, "live", spend_limit_usd=limit, transport=transport)
    record, from_cache = live.segment_video(clip, path, "car")
    assert not from_cache and len(calls) == 1
    assert record.request_id == "req-1" and record.frames_billed == 3
    assert live.spent_usd == pytest.approx(0.012)

    body = json.loads(calls[0].content)
    video = body["input"][0]["content"][1]
    assert video["type"] == "input_video"
    assert base64.b64decode(video["video_url"].split(",", 1)[1]) == path.read_bytes()
    assert body["metadata"] == {"include_confidence": "true"} and body["stream"] is True
    assert calls[0].headers["authorization"] == f"Bearer {FAKE_KEY}"

    cache_file = live.cache_path(record.request_key)
    assert FAKE_KEY not in cache_file.read_text(encoding="utf-8")

    again, from_cache = live.segment_video(clip, path, "car")  # cache hit, no second payment
    assert from_cache and again == record and len(calls) == 1

    replay = SamClient(settings, tmp_path, "replay", transport=_no_network())
    parsed = replay.segment_and_parse(clip, path, "car")
    assert len(parsed.observations) == 3 and parsed.observations[0].provenance.cached


@pytest.mark.parametrize(
    ("status", "kind"), [(401, "auth"), (402, "billing"), (429, "rate_limited"), (503, "server")]
)
def test_http_errors_are_classified_audited_and_not_cached(
    settings: Settings,
    tmp_path: Path,
    clip_and_path: tuple[SourceClip, Path],
    status: int,
    kind: str,
) -> None:
    clip, path = clip_and_path
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, text=f"bad key {FAKE_KEY}", headers={"x-request-id": "req-9"})

    transport = httpx.MockTransport(handler)
    client = SamClient(settings, tmp_path, "live", spend_limit_usd=1.0, transport=transport)
    with pytest.raises(SamRequestError) as raised:
        client.segment_video(clip, path, "car")
    assert raised.value.error_class == kind and raised.value.request_id == "req-9"
    assert len(calls) == 1 and client.spent_usd == 0.0  # no retry; nothing counted as spent
    failed = list((tmp_path / "failed").glob("*.json"))
    assert len(failed) == 1 and FAKE_KEY not in failed[0].read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.json"))  # nothing cached as a replayable hit


def test_stream_that_does_not_complete_is_not_cached(
    settings: Settings, tmp_path: Path, clip_and_path: tuple[SourceClip, Path]
) -> None:
    clip, path = clip_and_path
    stream = "\n".join(_sse([_token(0, 0, 10, 10)], terminal="failed")) + "\n"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=stream))
    client = SamClient(settings, tmp_path, "live", spend_limit_usd=1.0, transport=transport)
    with pytest.raises(SamRequestError, match="response.failed"):
        client.segment_video(clip, path, "car")
    assert not list(tmp_path.glob("*.json")) and list((tmp_path / "failed").glob("*.json"))


def test_tampered_clip_is_refused_before_sending(
    settings: Settings, tmp_path: Path, clip_and_path: tuple[SourceClip, Path]
) -> None:
    clip, _ = clip_and_path
    other = tmp_path / "other.mp4"
    other.write_bytes(b"not the ingested clip")
    client = SamClient(settings, tmp_path, "live", spend_limit_usd=1.0, transport=_no_network())
    with pytest.raises(SamAdapterError, match="does not match clip"):
        client.segment_video(clip, other, "car")
