"""Camera calibration: an image-to-ground homography fitted to hand-picked control points.

Coordinate spaces: image pixels (origin top-left, x right, y down) and local
ground metres (origin at the junction centre, x east, y north). The road is treated as a plane;
that is an assumption, and its error shows up in the held-out check points.

The fit is plain least squares over every `fit` point (`cv2.findHomography` method 0), so the
same spec always gives the same matrix; RANSAC would make it depend on a random draw. Accuracy
is judged only on held-out `check` points. A calibration is accepted when there is at least one
check point and none is off by more than the target (default 1.0 m, to be confirmed against the
real clip).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from cairoflow_contracts.evidence import (
    CalibrationReport,
    CalibrationSpec,
    ControlPoint,
    Provenance,
)

DEFAULT_TARGET_MAX_CHECK_ERROR_M = 1.0
_HORIZON_EPS = 1e-9


class CalibrationError(ValueError):
    """The control points cannot define a usable homography, or a point cannot be mapped."""


class Homography:
    """Maps image pixels to ground metres and back, refusing points beyond the horizon.

    `image_to_ground` must already be scaled so its last entry is 1; `side` is the sign of the
    projective coordinate w for image points on the visible ground.
    """

    def __init__(self, image_to_ground: np.ndarray, side: float) -> None:
        if image_to_ground.shape != (3, 3) or not np.all(np.isfinite(image_to_ground)):
            raise CalibrationError("a homography is a finite 3x3 matrix")
        self.matrix = image_to_ground
        self.inverse = np.linalg.inv(image_to_ground)
        self._side = math.copysign(1.0, side)

    @classmethod
    def from_report(cls, report: CalibrationReport, spec: CalibrationSpec) -> Homography:
        if report.spec_sha256 != spec_sha256(spec):
            raise CalibrationError("the report was fitted to a different spec")
        matrix = np.array(report.image_to_ground, dtype=np.float64).reshape(3, 3)
        first = next(p for p in spec.points if p.point_id == report.fit_point_ids[0])
        return cls(matrix, _w(matrix, first.image_px))

    def to_ground(self, image_px: Sequence[float]) -> tuple[float, float]:
        w = _w(self.matrix, image_px)
        if w * self._side <= _HORIZON_EPS:
            raise CalibrationError(f"image point {tuple(image_px)} is at or beyond the horizon")
        u = self.matrix @ np.array([image_px[0], image_px[1], 1.0])
        return float(u[0] / w), float(u[1] / w)

    def to_image(self, ground_m: Sequence[float]) -> tuple[float, float]:
        u = self.inverse @ np.array([ground_m[0], ground_m[1], 1.0])
        if abs(u[2]) <= _HORIZON_EPS:
            raise CalibrationError(f"ground point {tuple(ground_m)} does not project to the image")
        return float(u[0] / u[2]), float(u[1] / u[2])

    def ground_error_at(self, image_px: Sequence[float], sigma_px: float = 1.0) -> float:
        """Ground error (m, worst direction) caused by `sigma_px` of image error at a point.

        Grows with distance from the camera; used to state measurement uncertainty.
        """
        p = np.array([image_px[0], image_px[1], 1.0])
        w = float(self.matrix[2] @ p)
        if w * self._side <= _HORIZON_EPS:
            raise CalibrationError(f"image point {tuple(image_px)} is at or beyond the horizon")
        jacobian = np.empty((2, 2))
        for i in range(2):
            u = float(self.matrix[i] @ p)
            for j in range(2):
                jacobian[i, j] = (self.matrix[i, j] * w - u * self.matrix[2, j]) / w**2
        return float(sigma_px * math.sqrt(np.linalg.eigvalsh(jacobian @ jacobian.T).max()))


def _w(matrix: np.ndarray, image_px: Sequence[float]) -> float:
    return float(matrix[2] @ np.array([image_px[0], image_px[1], 1.0]))


def _check_spread(points: np.ndarray, label: str) -> None:
    centred = points - points.mean(axis=0)
    singular = np.linalg.svd(centred, compute_uv=False)
    if singular[0] == 0 or singular[-1] / singular[0] < 1e-6:
        raise CalibrationError(f"the {label} fit points are collinear")


def spec_sha256(spec: CalibrationSpec) -> str:
    return hashlib.sha256(spec.model_dump_json().encode("utf-8")).hexdigest()


def fit_homography(
    spec: CalibrationSpec,
    *,
    target_max_check_error_m: float = DEFAULT_TARGET_MAX_CHECK_ERROR_M,
    now: datetime | None = None,
) -> tuple[Homography, CalibrationReport]:
    """Fit the homography to the `fit` points and score it on the `check` points."""
    fit = sorted((p for p in spec.points if p.role == "fit"), key=lambda p: p.point_id)
    check = sorted((p for p in spec.points if p.role == "check"), key=lambda p: p.point_id)
    image = np.array([p.image_px for p in fit], dtype=np.float64)
    ground = np.array([p.ground_m for p in fit], dtype=np.float64)
    _check_spread(image, "image")
    _check_spread(ground, "ground")

    raw, _ = cv2.findHomography(image, ground, method=0)
    if raw is None or not np.all(np.isfinite(raw)) or abs(raw[2, 2]) < 1e-12:
        raise CalibrationError("the fit points do not define a homography (degenerate layout)")
    matrix = raw / raw[2, 2]
    if abs(np.linalg.det(matrix)) < 1e-12:
        raise CalibrationError("the fit points do not define a homography (singular matrix)")
    sides = [_w(matrix, p.image_px) for p in fit]
    if min(sides) * max(sides) <= 0:
        raise CalibrationError("the fit points lie on both sides of the horizon")
    homography = Homography(matrix, sides[0])

    def ground_error(point: ControlPoint) -> float:
        x, y = homography.to_ground(point.image_px)
        return math.hypot(x - point.ground_m[0], y - point.ground_m[1])

    def image_error(point: ControlPoint) -> float:
        u, v = homography.to_image(point.ground_m)
        return math.hypot(u - point.image_px[0], v - point.image_px[1])

    fit_errors = [ground_error(p) for p in fit]
    check_m: dict[str, float] = {}
    check_px: dict[str, float] = {}
    reasons: list[str] = []
    for point in check:
        try:
            check_m[point.point_id] = round(ground_error(point), 6)
            check_px[point.point_id] = round(image_error(point), 6)
        except CalibrationError as error:
            reasons.append(f"check point {point.point_id}: {error}")
    worst = max(check_m.values()) if check_m else None
    if not check:
        reasons.append("no held-out check points, so accuracy is unknown")
    elif worst is not None and worst > target_max_check_error_m:
        worst_id = max(check_m, key=lambda key: check_m[key])
        reasons.append(
            f"check point {worst_id} is off by {worst:.2f} m (target {target_max_check_error_m} m)"
        )

    report = CalibrationReport(
        clip_id=spec.clip_id,
        spec_sha256=spec_sha256(spec),
        image_to_ground=tuple(float(v) for v in matrix.ravel()),
        fit_point_ids=tuple(p.point_id for p in fit),
        check_point_ids=tuple(p.point_id for p in check),
        fit_rms_m=round(math.sqrt(sum(e * e for e in fit_errors) / len(fit_errors)), 6),
        fit_max_m=round(max(fit_errors), 6),
        check_errors_m=check_m,
        check_errors_px=check_px,
        max_check_error_m=worst,
        target_max_check_error_m=target_max_check_error_m,
        accepted=not reasons,
        reasons=tuple(reasons),
        provenance=Provenance(
            kind="approximated",
            produced_by="cairoflow_perception.calibration",
            produced_at_utc=now or datetime.now(UTC),
            parameters={"method": "least_squares", "fit_points": len(fit)},
            software={"opencv": cv2.__version__},
        ),
    )
    return homography, report


def save_report(report: CalibrationReport, path: Path) -> None:
    """Write the report as JSON; never overwrites."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(report.model_dump_json(indent=2) + "\n")
