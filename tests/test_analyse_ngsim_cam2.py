"""Helpers of scripts/analyse_ngsim_cam2.py, and the committed v2 correspondence record."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from cairoflow_perception.ngsim import US_SURVEY_FOOT_M, Trajectories

REPO = Path(__file__).resolve().parents[1]
V2 = REPO / "data" / "geometry" / "ngsim-lankershim-cam2" / "v2"


def _script():
    path = REPO / "scripts" / "analyse_ngsim_cam2.py"
    spec = importlib.util.spec_from_file_location("analyse_ngsim_cam2", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_seed_warp_handedness_scale_and_centre() -> None:
    a = _script()
    proper = a.seed_raster_to_image(25.0, mirrored=False)
    mirrored = a.seed_raster_to_image(25.0, mirrored=True)
    det_p, scale, aniso = a.local_scale(proper)
    assert det_p > 0 and a.local_scale(mirrored)[0] < 0
    assert scale == pytest.approx(a.SEED_PX_PER_M * a.RASTER_RES_M) and aniso == pytest.approx(1.0)
    assert a.local_scale(a.seed_raster_to_image(25.0, False, 6.2))[1] == pytest.approx(
        6.2 * a.RASTER_RES_M
    )
    centre = a.RASTER_HALF_M / a.RASTER_RES_M
    p = proper @ np.array([centre, centre, 1.0])
    assert p[:2] / p[2] == pytest.approx(np.array(a.SEED_CENTRE_PX))
    # the image +u axis points along the given bearing: a step north-north-east moves right
    ground_to_image = proper @ a.raster_geometry((0.0, 0.0))
    u0, u1 = a.ground_to_image(
        ground_to_image, np.array([[0.0, 0.0], [np.sin(0.436), np.cos(0.436)]])
    )
    assert u1[0] - u0[0] > 5.0 and abs(u1[1] - u0[1]) < 0.1


def test_boundary_matching_and_lane_assignment() -> None:
    a = _script()
    peaks = [{"position_m": p, "height": 20.0, "kind": "white"} for p in (-3.1, 0.2, 4.0, 9.0)]
    pairs, unmatched = a.match([-3.0, 0.0, 3.0, 6.0], peaks)
    assert [p["paint_m"] for p in pairs] == [-3.1, 0.2, None, None]  # 4.0 is beyond MATCH_TOL_M
    assert pairs[0]["residual_m"] == pytest.approx(-0.1)
    assert [p["position_m"] for p in unmatched] == [4.0, 9.0]
    centres = [{"lane": "SB lane 12", "across_m": -3.6}, {"lane": "SB lane 11", "across_m": -0.5}]
    lanes = a.assign_lanes(np.array([-3.0, -0.9, 2.0, -6.0]), centres)
    assert lanes.tolist() == [12, 11, 0, 0]  # beyond LANE_ASSIGN_M of any centre: no lane


def test_entry_direction_is_the_code_of_the_first_row() -> None:
    a = _script()
    n = 4
    ones = np.ones(n, np.int64)
    traj = Trajectories(
        vehicle_id=np.array([5, 5, 9, 9]), frame_id=np.array([2, 1, 1, 2]),
        global_time_ms=np.array([200, 100, 100, 200]), x_ft=np.zeros(n) / US_SURVEY_FOOT_M,
        y_ft=np.zeros(n), length_ft=np.full(n, 15.0), width_ft=np.full(n, 6.0), lane_id=ones,
        int_id=ones, section_id=ones * 0, direction=np.array([4, 1, 2, 2]), movement=ones,
    )  # fmt: skip
    assert a.entry_directions(traj) == {5: 1, 9: 2}  # vehicle 5 entered eastbound, then turned


@pytest.mark.skipif(not V2.exists(), reason="v2 record not generated")
def test_committed_v2_record_is_consistent_and_has_no_calibration() -> None:
    record = json.loads((V2 / "control_points.json").read_text("utf-8"))
    assert not list(V2.glob("calibration*.json"))  # no calibration spec for camera 2 (ADR-017)
    assert record["inputs"]["trajectories"]["sha256"].startswith("c74ef7cc")
    assert "CC BY-SA 3.0" in record["licence"]
    assert record["identification"]["best"].startswith("int2|image +u = NB bearing + 0|proper")
    assert record["category_C_correspondences"]["points"] == []
    assert record["calibration_files"] == {}
    for name, landmark in record["category_A_image"]["landmarks"].items():
        u, v = landmark["image_px"]
        assert 0 <= u <= 640 and 0 <= v < 410, name  # above the window-frame band
    for arm in ("north", "south"):  # lane-level lateral agreement on the Lankershim arms
        pairs = record["category_C_correspondences"]["line_pairs"][arm]
        residuals = [abs(p["residual_m"]) for p in pairs if p["residual_m"] is not None]
        assert len(residuals) >= 6 and max(residuals) < 0.6
        assert not any(p["inside_painted_median"] and p["residual_m"] is not None for p in pairs)
        lines = record["category_A_image"]["measured_lines"][arm]
        assert len({line["id"] for line in lines}) == len(lines) >= 5
