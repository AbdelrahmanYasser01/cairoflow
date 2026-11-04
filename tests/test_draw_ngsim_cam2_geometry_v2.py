"""Image-side geometry v2: the pixel contract, the line refinement, and the committed file."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from cairoflow_contracts.evidence import ImageGeometry, ImageLane, ImageLine

REPO = Path(__file__).resolve().parents[1]
V2 = REPO / "data" / "geometry" / "ngsim-lankershim-cam2" / "v2" / "image_geometry.json"
SHA = "0" * 64


def _script():
    path = REPO / "scripts" / "draw_ngsim_cam2_geometry_v2.py"
    spec = importlib.util.spec_from_file_location("draw_v2", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["draw_v2"] = module  # its dataclass resolves annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _lane(lane_id="A1", arm="N", direction="approach", turns=("through",), poly=None):
    return ImageLane(
        lane_id=lane_id, arm=arm, direction=direction,
        polygon_px=poly or ((10.0, 10.0), (30.0, 10.0), (30.0, 20.0), (10.0, 20.0)),
        allowed_turns=turns, turns_source="test", provenance="approximated", source="test",
    )  # fmt: skip


def _line(line_id="L1", arm="N", role="limit_line", seg=((10.0, 5.0), (10.0, 25.0))):
    return ImageLine(line_id=line_id, arm=arm, role=role, segment_px=seg, painted=True,
                     provenance="approximated", source="test")  # fmt: skip


def _geometry(lanes, lines, usable=100.0):
    return ImageGeometry(geometry_id="g", clip_ids=("c",), source_image_sha256=SHA,
                         image_width_px=64, image_height_px=48, usable_v_max_px=usable,
                         lanes=tuple(lanes), lines=tuple(lines))  # fmt: skip


def test_image_geometry_contract_rules() -> None:
    assert len(_geometry([_lane()], [_line()]).lanes) == 1
    assert _lane(turns=()).allowed_turns == ()  # an unknown turn is allowed for pixels
    with pytest.raises(ValidationError, match="no limit line"):
        _geometry([_lane()], [_line(role="exit_line")])
    with pytest.raises(ValidationError, match="outside the usable image"):
        _geometry([_lane()], [_line()], usable=15.0)
    with pytest.raises(ValidationError, match="unique"):
        _geometry([_lane(), _lane()], [_line()])
    with pytest.raises(ValidationError, match="at most one limit_line"):
        _geometry([_lane()], [_line(), _line(line_id="L2")])
    with pytest.raises(ValidationError, match="exit lanes list no turns"):
        _lane(direction="exit")
    with pytest.raises(ValidationError, match="simple"):
        _lane(poly=((10.0, 10.0), (40.0, 20.0), (40.0, 10.0), (10.0, 22.0)))  # a bow tie
    with pytest.raises(ValidationError, match="shorter than 3 px"):
        _line(seg=((10.0, 5.0), (11.0, 6.0)))


def test_measure_recovers_a_painted_line_from_an_offset_seed() -> None:
    s = _script()
    rows, cols = np.mgrid[0:120, 0:200].astype(np.float32)
    centre = 50.0 + 0.05 * cols  # a blurred white line, like paint at 640x480
    img = (80.0 + 150.0 * np.exp(-0.5 * ((rows - centre) / 0.8) ** 2)).astype(np.float32)
    maps = {"white": img, "yellow": img, "gray": img}
    m = s.measure(maps, "white", (20.0, 53.5), (180.0, 61.5))  # seed 2 px below the line
    assert m.refined and m.rms_px < 0.25  # parabolic peak on a bilinear profile: ~0.2 px jitter
    for u in (20.0, 180.0):
        t = (u - m.point[0]) / m.direction[0]
        assert m.point[1] + t * m.direction[1] == pytest.approx(50.0 + 0.05 * u, abs=0.1)
    blank = {"white": np.full((120, 200), 80.0, np.float32)}
    assert not s.measure(blank, "white", (20.0, 50.0), (180.0, 58.0)).refined
    assert s.crossing(np.zeros(2), np.array([1.0, 0.0]), np.array([5.0, -3.0]),
                      np.array([0.0, 1.0])) == pytest.approx(np.array([5.0, 0.0]))  # fmt: skip


@pytest.mark.skipif(not V2.exists(), reason="v2 image geometry not generated")
def test_committed_v2_image_geometry() -> None:
    geo = ImageGeometry.model_validate_json(V2.read_text("utf-8"))
    counts = {arm: sum(lane.arm == arm for lane in geo.lanes) for arm in "NSWE"}
    assert counts == {"N": 6, "S": 5, "W": 3, "E": 3}
    assert all(lane.direction == "approach" and lane.provenance == "approximated"
               for lane in geo.lanes)  # fmt: skip
    limits = {line.arm for line in geo.lines if line.role == "limit_line"}
    assert limits == {"N", "S", "W", "E"}
    assert all(line.error_px is not None and line.error_px <= 3.0 for line in geo.lines)
    refs = [lane.reference_lane for lane in geo.lanes if lane.arm in "NS"]
    assert all(r and r.startswith("NGSIM") for r in refs)  # Lankershim lanes match published ids
    assert {lane.allowed_turns for lane in geo.lanes if lane.arm == "E"} == {
        ("left",),
        ("through",),
        ("right",),
    }
    assert geo.source_image_sha256.startswith("4a1668e4")
    # neighbouring lanes of an arm share their boundary corner at the limit line
    north = [lane for lane in geo.lanes if lane.arm == "N"]
    for upper, lower in zip(north, north[1:], strict=False):
        assert upper.polygon_px[1] == pytest.approx(lower.polygon_px[0], abs=1e-6)
