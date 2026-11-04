"""The one calibrated site: NGSIM intersection 2, seen by camera 2.

Bundles what every consumer needs to go between image pixels and the junction frame, built the same
way the camera gates build it: the v2 homography refit from sync_checkpoints.json, then the world
model's rotation. `image_to_junction` composes the two into one 3x3 matrix, so a batch of points is
one matrix product. Along-road distances from this homography are not accepted (ADR-018).
"""

from __future__ import annotations

import importlib.util
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cairoflow_perception.calibration import fit_homography, spec_sha256
from cairoflow_perception.camera_world import Placement
from cairoflow_perception.ngsim import CA_ZONE5_FTUS
from cairoflow_perception.ngsim_junction import JunctionFrame

SITE_ID = "ngsim-lankershim-int2"
SITE_NAME = "Lankershim Blvd at Universal Hollywood Dr, Los Angeles"
SYNC_SOURCE_S = 120.0  # source second at 08:30:00, ADR-031
VALID_BELOW_ROW_PX = 410  # rows from here down are the window frame, not the road
SCENARIO = Path("data/geometry/ngsim-lankershim-int2/scenario.json")
SYNC_CHECKPOINTS = Path("data/geometry/ngsim-lankershim-cam2/v2/sync_checkpoints.json")
CHECKPOINTS_SCRIPT = Path("scripts/checkpoints_ngsim_cam2.py")


@dataclass(frozen=True)
class Site:
    """The calibrated camera at intersection 2."""

    placement: Placement
    frame: JunctionFrame
    image_to_junction: np.ndarray
    junction_to_image: np.ndarray
    spec_sha256: str
    origin_lat_deg: float
    origin_lon_deg: float

    def project_to_junction(self, x_px: np.ndarray, y_px: np.ndarray) -> np.ndarray:
        """Image pixels to junction metres, shape (n, 2)."""
        return _apply(self.image_to_junction, x_px, y_px)

    def project_to_image(self, u_m: np.ndarray, v_m: np.ndarray) -> np.ndarray:
        """Junction metres to image pixels, shape (n, 2)."""
        return _apply(self.junction_to_image, u_m, v_m)


def _apply(matrix: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    points = np.stack([np.asarray(a, float), np.asarray(b, float), np.ones(len(a))])
    mapped = matrix @ points
    return (mapped[:2] / mapped[2]).T


def _checkpoints_module(repo: Path):
    spec = importlib.util.spec_from_file_location(
        "checkpoints_ngsim_cam2", repo / CHECKPOINTS_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_site(repo: Path) -> Site:
    scenario = json.loads((repo / SCENARIO).read_text("utf-8"))
    frame = JunctionFrame(
        *scenario["frame"]["origin_state_plane_ft"], scenario["frame"]["bearing_deg"]
    )
    checkpoints = _checkpoints_module(repo)
    pairs = json.loads((repo / SYNC_CHECKPOINTS).read_text("utf-8"))
    spec = checkpoints.spec_for(pairs["pairs"], checkpoints.CLIP_IDS["d300"])
    homography, calibration = fit_homography(spec, target_max_check_error_m=1.25)
    placement = Placement(homography, frame, statistics.median(calibration.check_errors_m.values()))
    bearing = math.radians(frame.bearing_deg)
    rotation = np.array(
        [
            [math.cos(bearing), -math.sin(bearing), 0.0],
            [math.sin(bearing), math.cos(bearing), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    image_to_junction = rotation @ homography.matrix
    image_to_junction = image_to_junction / image_to_junction[2, 2]
    lat, lon = CA_ZONE5_FTUS.to_geographic(frame.origin_x_ft, frame.origin_y_ft)
    return Site(
        placement=placement,
        frame=frame,
        image_to_junction=image_to_junction,
        junction_to_image=np.linalg.inv(image_to_junction),
        spec_sha256=spec_sha256(spec),
        origin_lat_deg=float(lat),
        origin_lon_deg=float(lon),
    )
