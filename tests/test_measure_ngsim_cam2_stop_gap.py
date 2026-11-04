"""Stop-gap measurement helpers and the committed hand clicks."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from cairoflow_contracts.evidence import ImageGeometry, ImageLane

REPO = Path(__file__).resolve().parents[1]
V2 = REPO / "data" / "geometry" / "ngsim-lankershim-cam2" / "v2"


def _script():
    path = REPO / "scripts" / "measure_ngsim_cam2_stop_gap.py"
    spec = importlib.util.spec_from_file_location("stop_gap", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["stop_gap"] = module
    spec.loader.exec_module(module)
    return module


# A lane travelling left (towards smaller u), limit line at u = 100: [a x limit, b x limit,
# b x outer, a x outer], like the v2 north arm.
LANE = ImageLane(
    lane_id="N_SB3", arm="N", direction="approach",
    polygon_px=((100.0, 20.0), (100.0, 36.0), (190.0, 36.0), (190.0, 20.0)),
    allowed_turns=("through",), turns_source="test", provenance="approximated", source="test",
)  # fmt: skip


def test_lane_axis_and_gap_sign() -> None:
    s = _script()
    centre, d = s.lane_axis(LANE)
    assert centre == pytest.approx(np.array([100.0, 28.0]))
    assert d == pytest.approx(np.array([-1.0, 0.0]))  # towards the junction
    limit = ((100.0, 10.0), (102.0, 50.0))  # leaning slightly: u = 100.9 at v = 28
    assert s.gap_px((108.0, 28.0), limit, d) == pytest.approx(108.0 - 100.9)  # short of the line
    assert s.gap_px((98.0, 28.0), limit, d) < 0  # over the line


def test_stillness_and_auto_front() -> None:
    s = _script()
    bg = np.full((60, 220, 3), 90, np.uint8)
    frame = bg.copy()
    frame[22:34, 110:135] = 230  # a vehicle whose front is 10 px short of the line
    assert s.is_still(frame, frame.copy(), LANE, (110.0, 28.0))
    moved = bg.copy()
    moved[22:34, 85:110] = 230  # one second later it has moved a car length on
    assert not s.is_still(frame, moved, LANE, (110.0, 28.0))
    assert s.auto_front(frame, bg, LANE) == pytest.approx(10.0, abs=1.5)
    assert s.auto_front(bg, bg, LANE) is None


@pytest.mark.skipif(not (V2 / "stop_gap_clicks.json").exists(), reason="no clicks")
def test_committed_clicks_match_the_geometry() -> None:
    s = _script()
    clicks = s.Clicks.model_validate_json((V2 / "stop_gap_clicks.json").read_text("utf-8"))
    geo = ImageGeometry.model_validate_json((V2 / "image_geometry.json").read_text("utf-8"))
    lanes = {lane.lane_id: lane for lane in geo.lanes}
    assert clicks.source_video_sha256 == s.VIDEO_SHA256
    assert len({fc.frame for fc in clicks.frames}) == len(clicks.frames) >= 10
    for fc in clicks.frames:
        assert 0 <= fc.frame < 11220 - 10  # the video has 11,220 frames; the report needs +1 s
        for lane_id, click in fc.lanes.items():
            lane = lanes[lane_id]
            assert lane.arm == fc.arm
            _, d = s.lane_axis(lane)
            if click.rear_px is not None:  # the rear is upstream of the front
                assert (np.asarray(click.front_px) - np.asarray(click.rear_px)) @ d > 10
            u, v = click.front_px
            assert 0 <= u <= 640 and 0 <= v < geo.usable_v_max_px
