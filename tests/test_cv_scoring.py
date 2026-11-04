"""The local-detector scoring harness: blocks, time map, matching, recall and precision proxy.

Synthetic fixtures only, except one comparison of the plain-number ground map with the
repository's Placement, which reads the committed v2 check points. Nothing here opens the video
or the trajectory table, and nothing imports torch.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from cairoflow_perception.local import scoring as S
from cairoflow_perception.local.median_blob import BlobParams, MedianBlobDetector

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
FLAT = S.GroundMap(IDENTITY, 1.0, 0.0)


def ref(frame=1, vid=1, x=0.0, y=0.0, heading=0.0, in_view=True):
    return S.Ref(frame, vid, x, y, heading, in_view)


def det(frame=1, name="a", x=0.0, y=0.0, group="g"):
    return S.Det(frame, name, group, x, y)


# ---- blocks -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_s", "block", "dev"),
    [
        (120.0, 0, True),
        (179.9, 0, True),
        (180.0, 1, False),
        (239.9, 1, False),
        (240.0, 2, True),
        (370.0, 4, True),
        (419.9, 4, True),
        (420.0, 5, False),
        (960.0, 14, True),
        (1019.9, 14, True),
    ],
)
def test_block_classification(source_s, block, dev):
    assert S.block_of(source_s) == block
    assert S.is_dev(source_s) is dev


@pytest.mark.parametrize("source_s", [0.0, 119.9, 1020.0, 1121.0])
def test_outside_the_covered_range_has_no_block(source_s):
    with pytest.raises(ValueError, match="no block"):
        S.block_of(source_s)


def test_there_are_eight_dev_and_seven_held_out_blocks():
    assert S.dev_blocks() == [0, 2, 4, 6, 8, 10, 12, 14]


def test_every_sam_window_frame_is_in_a_dev_block():
    for key in S.WINDOWS:
        assert all(S.is_dev(t) for t in S.sam_window_times(key))


def test_require_dev_raises_on_a_held_out_block():
    S.require_dev(370.0)
    with pytest.raises(S.HeldOutError):
        S.require_dev(200.0)


def test_dev_sample_is_every_2_s_through_even_blocks_only():
    times = S.dev_sample_times()
    assert len(times) == 240
    assert all(S.is_dev(t) for t in times)
    assert {S.block_of(t) for t in times} == set(S.dev_blocks())
    assert times[:3] == [121.0, 123.0, 125.0]
    assert times[29:31] == [179.0, 241.0]


def test_dev_sample_images_clear_both_readings_of_a_frames_time():
    for t in S.dev_sample_times():
        S.require_dev_image(t)


def test_the_first_frame_of_an_even_block_is_refused_as_an_image():
    # published source second 240.0 is even, but its image is AVI frame 2399 = 239.9 s, block 1
    assert S.is_dev(240.0)
    with pytest.raises(S.HeldOutError):
        S.require_dev_image(240.0)


def test_sam_window_frame_counts_match_the_published_excerpts():
    assert [len(S.sam_window_times(k)) for k in ("w1", "w2", "w3")] == [100, 300, 300]
    assert len(S.sam_window_times("w2", 5)) == 60


# ---- time map -----------------------------------------------------------------------------


def test_window_second_uses_the_governing_sync():
    assert S.window_s(370.0) == 250.0
    assert S.window_s(970.1) == 850.1


def test_global_time_of_a_source_second():
    assert S.global_time_ms(120.0) == S.T0_MS
    assert S.global_time_ms(370.0) == S.T0_MS + 2500 * 100
    assert S.global_time_ms(970.3) == 1118935800000 + 8503 * 100


def test_the_stale_sync_moves_the_tick_by_three_frames():
    assert S.global_time_ms(370.0, S.SYNC_OTHER_S) - S.global_time_ms(370.0) == 300


def test_tick_and_source_second_round_trip():
    for t in (120.0, 370.4, 1019.9):
        assert S.source_s_of_tick(S.global_time_ms(t)) == pytest.approx(t)


def test_a_held_out_tick_is_recognised():
    assert S.tick_is_dev(S.global_time_ms(370.0))
    assert not S.tick_is_dev(S.global_time_ms(420.0))
    assert not S.tick_is_dev(S.T0_MS + 900_000)  # source second 1020: past the covered range


def test_the_image_of_a_source_second_is_the_avi_frame_before_it():
    assert S.avi_index(370.0) == 3699
    assert S.avi_index(999.9) == 9998


# ---- the sampler refuses before it touches the video --------------------------------------


def test_sampler_refuses_a_held_out_frame(tmp_path):
    cv2 = pytest.importorskip("cv2")
    path = tmp_path / "tiny.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    for _ in range(3):
        writer.write(np.zeros((48, 64, 3), np.uint8))
    writer.release()
    with S.FrameSampler(path) as sampler:
        with pytest.raises(S.HeldOutError):
            sampler.read(200.0)
        with pytest.raises(ValueError, match="no block"):
            sampler.read(1100.0)


# ---- matching, recall, precision ----------------------------------------------------------


def test_a_detection_on_a_vehicle_matches_it():
    r = S.associate_frame(1, [det()], [ref()])
    assert [m.vehicle_id for m in r.matches] == [1]
    assert (r.n_det, r.n_ref_in_view) == (1, 1)


def test_the_window_is_8_m_along_and_3_m_across_the_heading():
    assert S.associate_frame(1, [det(y=7.9)], [ref()]).matches
    assert not S.associate_frame(1, [det(y=8.1)], [ref()]).matches
    assert S.associate_frame(1, [det(x=2.9)], [ref()]).matches
    assert not S.associate_frame(1, [det(x=3.1)], [ref()]).matches
    # heading 90: the road runs along x, so the same offsets swap roles
    assert S.associate_frame(1, [det(x=7.9)], [ref(heading=90.0)]).matches
    assert not S.associate_frame(1, [det(y=3.1)], [ref(heading=90.0)]).matches


def test_matching_is_one_to_one_and_prefers_the_lower_cost():
    r = S.associate_frame(1, [det(name="far", x=2.0), det(name="near", x=0.5)], [ref()])
    assert [m.det_id for m in r.matches] == ["near"]
    assert r.unmatched_dets == (("far", "g"),)


def test_recall_counts_only_reference_vehicles_in_view():
    refs = {1: [ref(vid=1, x=0.0), ref(vid=2, x=50.0, in_view=False), ref(vid=3, x=100.0)]}
    dets = {1: [det(name="a", x=0.0), det(name="b", x=50.0)]}
    res = S.associate(dets, refs, [1])
    assert S.vehicle_frame_recall(res) == (1, 2)


def test_precision_proxy_counts_a_match_to_a_reference_out_of_view():
    refs = {1: [ref(vid=2, x=50.0, in_view=False)]}
    res = S.associate({1: [det(x=50.0), det(name="b", x=-90.0)]}, refs, [1])
    assert S.precision_proxy(res) == (1, 2)
    assert S.vehicle_frame_recall(res) == (0, 0)


def test_a_detection_without_a_ground_position_is_outside_the_denominator():
    res = S.associate({1: [det(), S.Det(1, "sky", "g", None, None)]}, {1: [ref()]}, [1])
    assert S.precision_proxy(res) == (1, 1)
    assert res[1].n_det_unprojected == 1


def test_a_frame_without_a_reference_is_unscorable_not_empty():
    res = S.associate({1: [det()], 2: [det(frame=2)]}, {1: [ref()]}, [1, 2])
    assert sorted(res) == [1]


def test_a_frame_with_no_detections_counts_against_recall():
    assert S.vehicle_frame_recall(S.associate({}, {1: [ref()]}, [1])) == (0, 1)


def test_summary_carries_counts_and_values():
    refs = {f: [ref(frame=f, vid=1), ref(frame=f, vid=2, x=50.0)] for f in (1, 2)}
    dets = {1: [det(frame=1), det(frame=1, name="b", x=90.0)], 2: [det(frame=2)]}
    out = S.summarise(S.associate(dets, refs, [1, 2]), intervals=False)
    recall = out["vehicle_frame_recall"]
    assert (recall["numerator"], recall["denominator"], recall["value"]) == (2, 4, 0.5)
    precision = out["precision_proxy"]
    assert (precision["numerator"], precision["denominator"]) == (2, 3)
    assert precision["value"] == pytest.approx(0.6667)
    vehicles = out["vehicle_recall_any_exposure"]
    assert (vehicles["numerator"], vehicles["denominator"]) == (1, 2)


def test_summary_of_nothing_has_no_ratios():
    out = S.summarise({}, intervals=False)
    assert out["vehicle_frame_recall"]["value"] is None
    assert out["precision_proxy"]["value"] is None
    assert out["mean_detections_per_frame"] is None


def test_the_bootstrap_is_deterministic_and_brackets_the_ratio():
    clusters = [(1.0, 2.0), (3.0, 4.0), (0.0, 5.0), (5.0, 5.0)]
    a = S.cluster_bootstrap_ratio(clusters)
    assert a == S.cluster_bootstrap_ratio(clusters)
    assert a[0] <= 9 / 16 <= a[1]
    assert S.cluster_bootstrap_ratio([(1.0, 2.0)]) is None


# ---- geometry -----------------------------------------------------------------------------


def test_ground_map_rotates_into_the_junction_frame():
    gm = S.GroundMap(IDENTITY, 1.0, 90.0)
    x, y = gm.to_junction(1.0, 0.0)
    assert (x, y) == pytest.approx((0.0, 1.0))
    assert gm.to_image(x, y) == pytest.approx((1.0, 0.0))


def test_a_point_beyond_the_horizon_has_no_ground_position():
    gm = S.GroundMap(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, -0.01, 1.0)), 1.0, 0.0)
    assert gm.to_junction(5.0, 50.0) is not None
    assert gm.to_junction(5.0, 100.0) is None
    assert gm.to_junction(5.0, 200.0) is None


def test_in_view_stops_at_the_glass_band():
    assert FLAT.visible(100.0, 409.0)
    assert not FLAT.visible(100.0, 410.0)
    assert not FLAT.visible(640.0, 100.0)
    assert not FLAT.visible(-1.0, 100.0)


def test_footprint_runs_along_the_heading():
    box = S.footprint_box_px(FLAT, 100.0, 100.0, 0.0, 4.0, 2.0)
    assert box == pytest.approx((99.0, 98.0, 101.0, 102.0))
    box = S.footprint_box_px(FLAT, 100.0, 100.0, 90.0, 4.0, 2.0)
    assert box == pytest.approx((98.0, 99.0, 102.0, 101.0))


def test_a_box_lands_on_the_ground_at_its_centre():
    dets = S.detections_to_dets(7, [(10, 20, 30, 60, 0.9, "vehicle")], FLAT)
    assert (dets[0].x_m, dets[0].y_m, dets[0].frame) == (20.0, 40.0, 7)


def test_the_plain_ground_map_equals_the_repository_placement():
    from cairoflow_perception.calibration import fit_homography  # noqa: F401
    from cairoflow_perception.camera_world import Placement
    from cairoflow_perception.ngsim_junction import JunctionFrame

    gm, _ = S.build_ground_map(ROOT)
    import json

    scenario = json.loads((ROOT / S.SCENARIO).read_text("utf-8"))
    frame = JunctionFrame(
        *scenario["frame"]["origin_state_plane_ft"], scenario["frame"]["bearing_deg"]
    )
    from cairoflow_perception.calibration import Homography

    placement = Placement(Homography(np.array(gm.image_to_ground), gm.side), frame, 0.0)
    for u, v in ((100.0, 120.0), (320.0, 240.0), (600.0, 400.0)):
        assert gm.to_junction(u, v) == pytest.approx(placement.to_junction((u, v)), abs=1e-6)
        assert gm.to_image(*gm.to_junction(u, v)) == pytest.approx((u, v), abs=1e-6)


# ---- reference sets and records -----------------------------------------------------------


def test_a_reference_set_survives_saving(tmp_path):
    original = S.ReferenceSet(
        120.0,
        0.0,
        FLAT,
        {
            10: [
                S.Ref(10, 3, 1.5, 2.5, 45.0, True, 4.5, 1.8, "N-SB", 3.0, (1.0, 2.0, 3.0, 4.0)),
                S.Ref(10, 4, 9.0, 8.0, 90.0, False),
            ],
            20: [],
        },
        {"note": "synthetic"},
    )
    path = tmp_path / "set.npz"
    original.save(path)
    loaded = S.ReferenceSet.load(path)
    assert sorted(loaded.refs) == [10, 20]
    first = loaded.refs[10][0]
    assert first.vehicle_id == 3 and first.in_view and first.footprint_px == (1.0, 2.0, 3.0, 4.0)
    assert loaded.refs[10][1].footprint_px is None and not loaded.refs[10][1].in_view
    assert math.isnan(loaded.refs[10][1].speed_m_s)
    assert loaded.ground == FLAT and loaded.meta == {"note": "synthetic"}


def test_a_record_is_never_overwritten(tmp_path):
    S.write_record(tmp_path, "a.json", {"x": 1})
    with pytest.raises(FileExistsError):
        S.write_record(tmp_path, "a.json", {"x": 2})
    with pytest.raises(ValueError, match="JSON"):
        S.write_record(tmp_path, "a.png", {})


# ---- the detector interface ---------------------------------------------------------------


def road_with_a_car(v=100):
    pytest.importorskip("cv2")
    background = np.full((480, 640, 3), 90, np.uint8)
    frame = background.copy()
    frame[v : v + 14, 200:226] = 200
    return background, frame


def test_blob_detector_finds_a_car_sized_region():
    background, frame = road_with_a_car()
    detector = MedianBlobDetector(background)
    assert isinstance(detector, S.Detector)
    found = detector.detect(frame)
    assert len(found) == 1
    x1, y1, x2, y2, score, label = found[0]
    assert (x1, y1) == pytest.approx((200, 100), abs=3)
    assert (x2, y2) == pytest.approx((226, 114), abs=3)
    assert 0 < score <= 1 and label == "vehicle"


def test_blob_detector_sees_nothing_on_an_empty_road():
    background, _ = road_with_a_car()
    assert MedianBlobDetector(background).detect(background) == []


def test_blob_detector_ignores_the_glass_band():
    background, frame = road_with_a_car(v=430)
    assert MedianBlobDetector(background).detect(frame) == []


def test_blob_detector_splits_a_queue_into_vehicle_sized_boxes():
    background = np.full((480, 640, 3), 90, np.uint8)
    frame = background.copy()
    frame[100:114, 200:290] = 200  # three cars bumper to bumper, no gap to see
    found = MedianBlobDetector(background).detect(frame)
    assert len(found) == 3
    assert all(d.x2 - d.x1 <= BlobParams().max_side_px for d in found)


def test_blob_detector_size_gates_reject_noise_and_slabs():
    background = np.full((480, 640, 3), 90, np.uint8)
    frame = background.copy()
    frame[50:53, 50:53] = 220  # a speck
    frame[100:300, 300:500] = 220  # far larger than a vehicle
    assert MedianBlobDetector(background, BlobParams()).detect(frame) == []


# ---- imports ------------------------------------------------------------------------------


def run_python(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=60
    )


def test_the_harness_imports_without_torch_cv2_or_the_contracts():
    done = run_python(
        "import sys\n"
        "import cairoflow_perception.local.scoring\n"
        "import cairoflow_perception.local.median_blob\n"
        "bad = {'torch', 'torchvision', 'cv2', 'cairoflow_contracts', 'pydantic'}\n"
        "assert not bad & set(sys.modules), bad & set(sys.modules)\n"
    )
    assert done.returncode == 0, done.stderr


def test_the_script_starts_without_torch():
    done = run_python(
        "import runpy, sys\n"
        "sys.argv = ['cv_bakeoff.py', '--help']\n"
        "try:\n"
        "    runpy.run_path('scripts/cv_bakeoff.py', run_name='__main__')\n"
        "except SystemExit as stop:\n"
        "    assert stop.code == 0\n"
        "assert 'torch' not in sys.modules\n"
    )
    assert done.returncode == 0, done.stderr
