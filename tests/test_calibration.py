"""Calibration against the synthetic pinhole camera in synthetic_camera.py, whose
ground-to-image mapping is known exactly (horizon at v = 32.4 px).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from synthetic_camera import FIT_GROUND, WIDTH, project
from synthetic_camera import calibration_spec as _spec

from cairoflow_contracts.evidence import CalibrationReport, CalibrationSpec
from cairoflow_perception.calibration import (
    CalibrationError,
    Homography,
    fit_homography,
    save_report,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def test_exact_points_recover_the_true_mapping() -> None:
    homography, report = fit_homography(_spec(), now=NOW)
    assert report.accepted and report.reasons == ()
    # "Exact" up to floating-point least squares: well under 0.1 mm and 0.001 px.
    assert report.fit_rms_m < 1e-4 and report.max_check_error_m < 1e-4
    assert max(report.check_errors_px.values()) < 1e-3
    for ground in [(3.0, -12.0), (-7.5, 18.0), (0.0, 40.0)]:
        x, y = homography.to_ground(project(*ground))
        assert (x, y) == pytest.approx(ground, abs=1e-4)
        assert homography.to_image(ground) == pytest.approx(project(*ground), abs=1e-3)
    assert report.provenance.kind == "approximated"
    assert report.fit_point_ids == tuple(f"f{i}" for i in range(9))


def test_pixel_noise_gives_small_errors_that_are_reported() -> None:
    _, report = fit_homography(_spec(noise_px=0.5), now=NOW)
    assert report.accepted
    assert 0 < report.max_check_error_m < 1.0
    assert set(report.check_errors_m) == {"c0", "c1", "c2", "c3"}


def test_ground_error_grows_with_distance_and_matches_a_pixel_shift() -> None:
    homography, _ = fit_homography(_spec(), now=NOW)
    near, far = project(0.0, -35.0), project(0.0, 30.0)
    assert homography.ground_error_at(far) > 3 * homography.ground_error_at(near)

    # Shifting the pixel by 1 px in every direction never moves the ground point much further
    # than the predicted worst case, and some direction comes close to it.
    predicted = homography.ground_error_at(far, sigma_px=1.0)
    base = np.array(homography.to_ground(far))
    shifts = []
    for angle in np.linspace(0, 2 * math.pi, 72, endpoint=False):
        moved = (far[0] + math.cos(angle), far[1] + math.sin(angle))
        shifts.append(np.linalg.norm(np.array(homography.to_ground(moved)) - base))
    assert predicted * 0.95 <= max(shifts) <= predicted * 1.05


def test_points_beyond_the_horizon_are_refused() -> None:
    homography, _ = fit_homography(_spec(), now=NOW)
    with pytest.raises(CalibrationError, match="horizon"):
        homography.to_ground((640.0, 10.0))  # above v = 32.4
    with pytest.raises(CalibrationError, match="horizon"):
        homography.ground_error_at((640.0, 10.0))


def test_a_wrong_check_point_fails_acceptance_with_its_id() -> None:
    spec = _spec()
    wrong = spec.points[-1].model_copy(update={"ground_m": (-12.0, -30.0)})  # 5 m off
    spec = spec.model_copy(update={"points": (*spec.points[:-1], wrong)})
    _, report = fit_homography(spec, now=NOW)
    assert not report.accepted
    assert report.max_check_error_m == pytest.approx(5.0, abs=1e-3)
    assert "c3" in report.reasons[0]


def test_no_check_points_means_not_accepted() -> None:
    _, report = fit_homography(_spec(check=[]), now=NOW)
    assert not report.accepted and report.max_check_error_m is None
    assert "no held-out check points" in report.reasons[0]


def test_degenerate_or_invalid_specs_are_rejected() -> None:
    collinear = [(0.0, y) for y in (-30.0, -10.0, 0.0, 10.0, 20.0)]
    with pytest.raises(CalibrationError, match="collinear"):
        fit_homography(_spec(fit=collinear, check=[]))
    with pytest.raises(ValidationError, match="at least 4 fit points"):
        _spec(fit=FIT_GROUND[:3])
    spec = _spec()
    with pytest.raises(ValidationError, match="unique"):
        CalibrationSpec.model_validate(
            spec.model_dump() | {"points": [*spec.points, spec.points[0]]}
        )
    outside = spec.points[0].model_copy(update={"image_px": (WIDTH + 5.0, 10.0)})
    with pytest.raises(ValidationError, match="outside the image"):
        CalibrationSpec.model_validate(spec.model_dump() | {"points": [outside, *spec.points[1:]]})


def test_fit_is_deterministic_and_reloadable(tmp_path: Path) -> None:
    spec = _spec(noise_px=0.5)
    first_h, first = fit_homography(spec, now=NOW)
    _, second = fit_homography(spec, now=NOW)
    assert first == second

    path = tmp_path / "calibration_report.json"
    save_report(first, path)
    with pytest.raises(FileExistsError):
        save_report(first, path)
    loaded = CalibrationReport.model_validate_json(path.read_text(encoding="utf-8"))
    reloaded = Homography.from_report(loaded, spec)
    probe = project(4.0, 5.0)
    assert reloaded.to_ground(probe) == pytest.approx(first_h.to_ground(probe), abs=1e-9)
    with pytest.raises(CalibrationError, match="different spec"):
        Homography.from_report(first, _spec(noise_px=0.5, seed=8))
