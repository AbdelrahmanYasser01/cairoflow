"""A synthetic pinhole camera whose ground-to-image mapping is known exactly, shared by tests.

Camera: 1280x720, focal length 900 px, 20 m above the ground at (0, -60), looking north and
pitched 20 degrees down. The horizon is at v = 360 - 900 tan(20 deg) = 32.4 px; the near edge
of the image sees the ground at about y = -37.6 m.
"""

from __future__ import annotations

import math

import numpy as np

from cairoflow_contracts.evidence import CalibrationSpec, ControlPoint

WIDTH, HEIGHT, FOCAL = 1280, 720, 900.0
CAMERA = np.array([0.0, -60.0, 20.0])
PITCH = math.radians(20)
FIT_GROUND = [(x, y) for x in (-10.0, 0.0, 10.0) for y in (-30.0, 0.0, 20.0)]
CHECK_GROUND = [(-5.0, -20.0), (5.0, 10.0), (12.0, 25.0), (-12.0, -35.0)]


def _rotation() -> np.ndarray:
    forward = np.array([0.0, math.cos(PITCH), -math.sin(PITCH)])  # camera z
    right = np.array([1.0, 0.0, 0.0])  # camera x
    down = np.cross(forward, right)  # camera y (image down)
    return np.vstack([right, down, forward])


def project(x: float, y: float) -> tuple[float, float]:
    """Exact image position of a ground point."""
    pc = _rotation() @ (np.array([x, y, 0.0]) - CAMERA)
    assert pc[2] > 0, "point is behind the camera"
    return FOCAL * pc[0] / pc[2] + WIDTH / 2, FOCAL * pc[1] / pc[2] + HEIGHT / 2


def _point(pid: str, ground: tuple[float, float], role: str, noise=(0.0, 0.0)) -> ControlPoint:
    u, v = project(*ground)
    return ControlPoint(
        point_id=pid,
        image_px=(u + noise[0], v + noise[1]),
        ground_m=ground,
        role=role,
        ground_provenance="synthetic",
        source="synthetic pinhole camera",
    )


def calibration_spec(
    noise_px: float = 0.0, seed: int = 7, check=CHECK_GROUND, fit=FIT_GROUND, clip_id: str = "c1"
) -> CalibrationSpec:
    rng = np.random.default_rng(seed)

    def jitter():
        return tuple(rng.normal(0.0, noise_px, 2)) if noise_px else (0.0, 0.0)

    points = [_point(f"f{i}", g, "fit", jitter()) for i, g in enumerate(fit)]
    points += [_point(f"c{i}", g, "check", jitter()) for i, g in enumerate(check)]
    return CalibrationSpec(
        clip_id=clip_id, image_width_px=WIDTH, image_height_px=HEIGHT, points=points
    )
