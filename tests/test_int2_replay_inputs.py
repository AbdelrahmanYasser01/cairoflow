"""Fresh-clone replay of the intersection 2 experiment (ADR-020).

A clone rebuilds the scenario with `build_ngsim_int2_scenario.py --as-of <recorded time>
--check-record` and replays the committed Muse reply. These tests cover the build's time option
and record check, and that the committed reply is the one the committed experiment record used,
with only the rate-limit values removed. The rebuild itself needs the trajectories (Git-ignored)
and is checked by hand (DATA_REGISTER.md §16).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cairoflow_reasoning.muse_adapter import (
    MuseRequestRecord,
    extract_output,
    parse_output,
    request_key,
)

REPO = Path(__file__).resolve().parents[1]
SCENARIO = REPO / "data" / "geometry" / "ngsim-lankershim-int2" / "scenario.json"
EXPERIMENT = REPO / "experiments" / "records" / "ngsim-int2-experiment-20261028T075803Z.json"
REPLY = (
    REPO
    / "cache"
    / "muse"
    / ("ddfb8029b1e8810e65b2ad986e0da99be630b86e6d7c86669007e2a197ef47ad.json")
)


def _load(name: str):
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build():
    return _load("build_ngsim_int2_scenario")


# --- --as-of -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-28T07:43:04Z", datetime(2026, 10, 28, 7, 43, 4, tzinfo=UTC)),
        ("2026-10-28T07:43:04+00:00", datetime(2026, 10, 28, 7, 43, 4, tzinfo=UTC)),
        ("2026-10-28T09:43:04+02:00", datetime(2026, 10, 28, 7, 43, 4, tzinfo=UTC)),
    ],
)
def test_as_of_is_read_as_utc(build, text: str, expected: datetime) -> None:
    value = build.parse_as_of(text)
    assert value == expected and value.utcoffset().total_seconds() == 0


@pytest.mark.parametrize("text", ["2026-10-28T07:43:04", "yesterday"])
def test_as_of_without_an_offset_or_unreadable_is_refused(build, text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        build.parse_as_of(text)


def test_the_recorded_time_names_the_recorded_directories(build) -> None:
    """`--as-of <created_at_utc>` lands on the paths the experiment script reads."""
    record = json.loads(SCENARIO.read_text("utf-8"))
    stamp = f"{build.parse_as_of(record['created_at_utc']):%Y%m%dT%H%M%SZ}"
    assert record["processed_outputs"]["dir"] == f"data/processed/ngsim-int2/{stamp}"
    assert record["baseline"]["run_dir"] == f"experiments/runs/ngsim-int2-baseline-{stamp}"


# --- --check-record -----------------------------------------------------------------------------


def _outputs(root: Path, stamp: str) -> tuple[dict, Path, Path]:
    processed = root / "data/processed/ngsim-int2" / stamp
    baseline = root / "experiments/runs" / f"ngsim-int2-baseline-{stamp}"
    processed.mkdir(parents=True)
    baseline.mkdir(parents=True)
    hashes = {}
    for name in ("geometry.json", "signal_timeline.json", "world_model.json"):
        (processed / name).write_text(f"{name}\n", encoding="utf-8")
        hashes[name] = hashlib.sha256(f"{name}\n".encode()).hexdigest()
    (baseline / "baseline_manifest.json").write_text("manifest\n", encoding="utf-8")
    record = {
        "created_at_utc": "2026-10-28T07:43:04+00:00",
        "processed_outputs": {"dir": f"data/processed/ngsim-int2/{stamp}", "sha256": hashes},
        "baseline": {
            "run_dir": f"experiments/runs/ngsim-int2-baseline-{stamp}",
            "manifest_sha256": hashlib.sha256(b"manifest\n").hexdigest(),
        },
    }
    return record, processed, baseline


def test_check_record_passes_identical_outputs(build, tmp_path: Path) -> None:
    record, processed, baseline = _outputs(tmp_path, "20261028T074304Z")
    assert build.check_record(record, tmp_path, processed, baseline) == []


def test_check_record_names_every_difference(build, tmp_path: Path) -> None:
    record, processed, baseline = _outputs(tmp_path, "20261028T074304Z")
    (processed / "world_model.json").write_text("rebuilt at another time\n", encoding="utf-8")
    (processed / "geometry.json").unlink()
    (baseline / "baseline_manifest.json").write_text("other\n", encoding="utf-8")
    problems = build.check_record(record, tmp_path, processed, baseline)
    assert [p.split(":")[0] for p in problems] == [
        "geometry.json",
        "world_model.json",
        "baseline_manifest.json",
    ]
    assert "missing" in problems[0]
    assert build.check_record(record, tmp_path, processed, None)[-1].startswith("no baseline")


def test_check_record_names_a_build_at_another_time(build, tmp_path: Path) -> None:
    record, _, _ = _outputs(tmp_path, "20261028T074304Z")
    _, processed, baseline = _outputs(tmp_path, "20261101T120000Z")
    problems = build.check_record(record, tmp_path, processed, baseline)
    assert sum("--as-of 2026-10-28T07:43:04+00:00" in p for p in problems) == 2


# --- the committed reply ------------------------------------------------------------------------


def test_the_committed_reply_is_the_recorded_request() -> None:
    raw = REPLY.read_text("utf-8")
    reply = MuseRequestRecord.model_validate_json(raw)
    experiment = json.loads(EXPERIMENT.read_text("utf-8"))
    request = experiment["muse_request"]

    # the replay key: file name, stored key and stored body agree with the committed record
    assert REPLY.stem == reply.request_key == request_key(reply.body) == request["request_key"]
    assert (reply.model, reply.prompt_version) == (request["model"], request["prompt_version"])
    assert reply.bundle_id == experiment["evidence_bundle"]["bundle_id"]
    assert reply.request_id == request["request_id"]
    assert len(json.dumps(reply.body).encode("utf-8")) == request["request_bytes"]

    # the reply gives the hypotheses and proposals the record lists
    response = json.loads(reply.response_text)
    assert response["id"] == request["response_id"]
    output = parse_output(extract_output(response))
    assert [h.hypothesis_id for h in output.hypotheses] == [
        h["hypothesis_id"] for h in experiment["hypotheses"]
    ]
    assert [p.intervention_id for p in output.interventions] == [
        p["intervention_id"] for p in experiment["proposals"]
    ]

    # option B: only the rate-limit values are removed; no credentials anywhere
    assert reply.rate_limit_headers and set(reply.rate_limit_headers.values()) == {"[removed]"}
    for marker in ("Bearer ", "Authorization", "[REDACTED]"):
        assert marker not in raw

    # the notice names this file's hash
    notice = (REPLY.parent / "NOTICE.md").read_text("utf-8")
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() in notice
    assert "CC BY-SA 3.0 Unported" in notice and "8ect-6jqj" in notice
