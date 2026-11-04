"""Static-scene measurements on synthetic images and a synthetic video."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from cairoflow_perception.scene import (
    activity_map,
    correlation,
    median_background,
    normalise,
    paint_strength,
    profile_peaks,
    register,
)


def test_profile_peaks_are_sub_sample_and_separated() -> None:
    x = np.arange(0, 10, 0.1)
    y = 50 * np.exp(-0.5 * ((x - 2.33) / 0.2) ** 2) + 30 * np.exp(-0.5 * ((x - 2.9) / 0.2) ** 2)
    y += 40 * np.exp(-0.5 * ((x - 7.0) / 0.2) ** 2)
    peaks = profile_peaks(x, y, min_height=10, min_separation=1.0)
    assert [round(p.position, 1) for p in peaks] == [2.3, 7.0]  # 2.9 is within 1.0 of 2.33
    assert peaks[1].position == pytest.approx(7.0, abs=0.02)
    assert peaks[1].width == pytest.approx(0.47, abs=0.15)
    assert profile_peaks(x, y, min_height=100, min_separation=0.5) == []


def test_paint_strength() -> None:
    img = np.zeros((1, 3, 3), np.uint8)
    img[0, 0] = (250, 250, 250)  # white (BGR)
    img[0, 1] = (40, 220, 230)  # yellow
    img[0, 2] = (60, 60, 60)  # asphalt
    white, yellow = paint_strength(img, "white"), paint_strength(img, "yellow")
    assert white.argmax() == 0 and yellow.argmax() == 1
    with pytest.raises(ValueError, match="unknown paint"):
        paint_strength(img, "red")


def _scene() -> np.ndarray:
    rng = np.random.default_rng(3)
    raster = np.zeros((300, 300), np.float32)
    for _ in range(25):
        x0, y0 = rng.integers(20, 260, 2)
        cv2.rectangle(raster, (int(x0), int(y0)), (int(x0) + 25, int(y0) + 8), 1.0, -1)
    cv2.line(raster, (0, 150), (299, 140), 0.7, 6)
    return raster


def test_register_recovers_a_known_homography() -> None:
    raster = _scene()
    truth = np.array([[0.9, 0.15, 30.0], [-0.12, 0.95, 25.0], [2e-4, -1e-4, 1.0]])
    image = cv2.warpPerspective(raster, truth, (320, 300))
    mask = np.full(image.shape, 255, np.uint8)
    start = truth.copy()
    start[:2, 2] += (4.0, -3.0)
    reg = register(raster, image, start, mask)
    assert reg.converged and reg.correlation > 0.95 > reg.initial_correlation
    grid = np.array([[x, y, 1.0] for x in range(40, 260, 40) for y in range(40, 260, 40)]).T
    a, b = truth @ grid, reg.raster_to_image @ grid
    error = np.hypot(*(a[:2] / a[2] - b[:2] / b[2]))
    assert error.max() < 0.5
    assert correlation(raster, image, truth, mask) > 0.99


def test_normalise_refuses_an_empty_map() -> None:
    with pytest.raises(ValueError, match="empty"):
        normalise(np.zeros((4, 4)))
    assert normalise(np.array([[0.0, 4.0]]), 100.0).tolist() == [[0.0, 1.0]]


def test_background_removes_a_moving_block_and_activity_follows_it(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
    assert writer.isOpened()
    base = np.full((48, 64, 3), 90, np.uint8)
    base[20:24, :] = 230  # a painted line
    for i in range(40):
        frame = base.copy()
        x = (i * 3) % 56
        frame[30:38, x : x + 8] = 20  # a dark vehicle moving right
        writer.write(frame)
    writer.release()
    background, used = median_background(path, 2)
    assert used == 20
    assert abs(int(background[34, 30, 0]) - 90) < 12  # the vehicle is gone
    assert background[22, 30, 0] > 200  # the paint stays
    activity, n = activity_map(path, background, 1, 35)
    assert n == 40
    assert activity[34, 10:50].mean() > 0.08 and activity[5, :].max() < 0.05
    with pytest.raises(ValueError, match="step"):
        median_background(path, 0)
