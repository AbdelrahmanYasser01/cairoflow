"""The read-only camera gate diagnosis: its geometry helpers, and that it reproduces gate v2's
recorded result before explaining it (and refuses inputs that differ from the record)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from cairoflow_contracts.evidence import IntersectionGeometry
from cairoflow_perception.camera_world import lane_hits

REPO = Path(__file__).resolve().parents[1]
GEOMETRY = REPO / "data/geometry/ngsim-lankershim-int2/geometry.json"
RECORD = REPO / "experiments/records/ngsim-cam2-world-model-20261030T220419Z.json"
SAM_RUN = REPO / "experiments/runs/ngsim-cam2-sam-20261030T220322Z"
TRAJECTORIES = REPO / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
INPUTS_PRESENT = SAM_RUN.is_dir() and TRAJECTORIES.exists()


def _script():
    path = REPO / "scripts" / "diagnose_camera_gate.py"
    spec = importlib.util.spec_from_file_location("diagnose_camera_gate", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["diagnose_camera_gate"] = module
    spec.loader.exec_module(module)
    return module


def test_heading_components_follow_the_bearing_convention():
    d = _script()
    # heading north (bearing 0): along is +y, across (to the right) is +x
    assert d.heading_components(0.5, 2.0, 0.0) == pytest.approx((2.0, 0.5))
    # heading east (bearing 90): along is +x, the right of travel is -y
    assert d.heading_components(2.0, 0.5, 90.0) == pytest.approx((2.0, -0.5))
    # heading south: a point 1 m north of the reference is 1 m behind it
    assert d.heading_components(0.0, 1.0, 180.0) == pytest.approx((-1.0, 0.0), abs=1e-12)


def test_fold_deg_is_the_angle_between_undirected_lines():
    d = _script()
    angles = [d.fold_deg(a) for a in (0, 180, 90, -90, 30, 150, 210, 359)]
    assert angles == pytest.approx([0, 0, 90, 90, 30, 30, 30, 1])


def test_regions_follow_the_world_model_geometry():
    d = _script()
    geometry = IntersectionGeometry.model_validate_json(GEOMETRY.read_text("utf-8"))
    lanes, box = list(geometry.lanes), d.stop_box(geometry)
    assert box["S"] < 0 < box["N"] and box["W"] < 0 < box["E"]
    assert d.region_of((0.0, 0.0), lanes, box) == "junction_interior"
    assert d.region_of((200.0, 200.0), lanes, box) == "unknown"
    checked = 0
    for lane in lanes:
        xs, ys = zip(*lane.polygon_m, strict=True)
        centroid = (sum(xs) / len(xs), sum(ys) / len(ys))
        if lane_hits(lanes, centroid) == [lane]:
            expected = "approach" if lane.direction == "approach" else "departure"
            assert d.region_of(centroid, lanes, box) == expected
            checked += 1
    assert checked >= len(lanes) // 2


@pytest.mark.skipif(not INPUTS_PRESENT, reason="gate v2's SAM run and trajectories are Git-ignored")
def test_reproduces_gate_v2_before_explaining_it(tmp_path):
    d = _script()
    args = ["--sam-run", str(SAM_RUN), "--gate-record", str(RECORD), "--out-root", str(tmp_path)]
    assert d.main(args) == 0
    (out,) = tmp_path.iterdir()
    diagnosis = json.loads((out / "diagnosis.json").read_text("utf-8"))
    recorded = json.loads(RECORD.read_text("utf-8"))
    reproduced = diagnosis["reproduced_gate_metrics"]
    assert reproduced["D_lateral_p90_m"] == recorded["metrics"]["D_lateral_p90_m"] == 2.206
    assert diagnosis["all_points"]["p90_m"] == 2.206
    assert "result unchanged" in diagnosis["label"]
    points = json.loads((out / "points.json").read_text("utf-8"))
    assert len(points) == recorded["metrics"]["matched_points"]
    assert sum(abs(p["lateral_m"]) > 1.5 for p in points) == 126
    assert {p.name for p in out.iterdir()} >= {"frame_errors.png", "ground_errors.png"}


@pytest.mark.skipif(not INPUTS_PRESENT, reason="gate v2's SAM run and trajectories are Git-ignored")
def test_refuses_inputs_that_differ_from_the_record(tmp_path):
    d = _script()
    recorded = json.loads(RECORD.read_text("utf-8"))
    recorded["inputs"]["sam_tracks"] = "0" * 64
    tampered = tmp_path / "record.json"
    tampered.write_text(json.dumps(recorded), "utf-8")
    args = ["--sam-run", str(SAM_RUN), "--gate-record", str(tampered),
            "--out-root", str(tmp_path / "out")]  # fmt: skip
    assert d.main(args) == 2
    assert not (tmp_path / "out").exists()
