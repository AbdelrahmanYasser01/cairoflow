"""Time sync and synced check points for camera 2: helpers and the committed record."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "data" / "geometry" / "ngsim-lankershim-cam2" / "v2" / "sync_checkpoints.json"


def _load(name: str):
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_correlation_recovers_a_known_lag() -> None:
    s = _load("sync_ngsim_cam2")
    rng = np.random.default_rng(7)
    traj = np.convolve((rng.random(9000) < 0.02).astype(float), np.ones(30), mode="same")
    video = rng.normal(0, 0.3, 11220)
    video[1197 : 1197 + 9000] += traj  # video index = trajectory index + 1197
    lags = np.arange(1100, 1301)
    curve = s.correlate(video, traj, lags)
    found = s.peaks(curve, lags / 10.0)
    assert found["lag_s"] == pytest.approx(119.7)
    assert found["peak"] > 2 * found["next_peak"]
    assert np.isnan(s.correlate(video[:5000], traj, np.array([0]))[0])  # under 600 s of overlap


def test_matching_window() -> None:
    c = _load("checkpoints_ngsim_cam2")
    cands = np.array([[0.0, 3.0], [1.0, 0.5], [0.0, 20.0]])  # bearing 0: along = y, across = x
    assert c.match(cands, np.array([0.0, 0.0]), 0.0) == 1
    assert c.match(cands, np.array([5.0, 0.0]), 0.0) is None  # too far across
    assert c.match(np.zeros((0, 2)), np.array([0.0, 0.0]), 0.0) is None


@pytest.mark.skipif(not RECORD.exists(), reason="sync record not generated")
def test_committed_sync_record() -> None:
    r = json.loads(RECORD.read_text("utf-8"))
    offset = r["sync"]["offset_s"]
    assert 119.0 <= offset <= 121.0  # the video starts at about 08:28:00
    assert all(abs(z["lag_s"] - offset) <= 1.0 for z in r["sync"]["per_zone"].values())
    assert len(r["pairs"]) >= 20
    for arm in ("N", "S"):
        assert abs(r["summary"][arm]["across_median_m"]) < 0.5
    fit = r["along_scale_fit"]
    assert 1.05 < fit["k_ngsim_over_registered"] < 1.25
    assert fit["residual_after_scale_m"]["rms"] < 0.6
    assert set(r["summary"]) == {"N", "S", "W", "E"}  # all four approaches have check points
    assessment = r["calibration_assessment"]
    assert assessment["lane_line_check_passes"] is True
    # The alternate-frame split passes at 1.25 m, the swapped split does not (§15, ADR-018)
    assert assessment["accepted_by_repo_rule"] is True
    assert assessment["swapped_split"]["accepted_by_repo_rule"] is False
    assert assessment["accepted"] is False
    assert not list(RECORD.parent.glob("calibration*.json"))
