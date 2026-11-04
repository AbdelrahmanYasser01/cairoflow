"""Static scene measurements from a fixed camera: empty-road background, vehicle activity, and the
registration of a ground occupancy raster to the image without any time synchronisation.

- Background: per-pixel median over frames sampled every `step` frames. Paint and kerbs survive;
  moving vehicles vanish. A vehicle parked for more than half the period stays in it.
- Activity: the share of sampled frames in which a pixel differs from the background by more than
  `threshold` (any channel, 0-255). Queued and moving vehicles both count.
- Registration: OpenCV's ECC maximises the correlation between a ground raster (where vehicles
  were, e.g. from published trajectories) and the activity map, over an affine and then a
  homography warp, from a stated initial guess. It uses only where vehicles were, never when, so a
  match is evidence of the same place, not of synchronised clocks. The result is a hypothesis to
  be checked against features the registration did not use (paint, kerbs).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


def _frames(video: Path, step: int):
    if step < 1:
        raise ValueError("step must be at least 1")
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError(f"cannot open {video}")
    index = 0
    try:
        while cap.grab():
            if index % step == 0:
                ok, frame = cap.retrieve()
                if ok:
                    yield frame
            index += 1
    finally:
        cap.release()


def median_background(video: Path, step: int) -> tuple[np.ndarray, int]:
    """Per-pixel median of every `step`-th frame; returns the image and the frame count used."""
    frames = list(_frames(video, step))
    if not frames:
        raise ValueError(f"no frames decoded from {video}")
    return np.median(np.stack(frames), axis=0).astype(np.uint8), len(frames)


def activity_map(
    video: Path, background: np.ndarray, step: int, threshold: int = 35
) -> tuple[np.ndarray, int]:
    """Share of every `step`-th frame in which each pixel differs from the background."""
    reference = background.astype(np.int16)
    total = np.zeros(background.shape[:2], np.float64)
    count = 0
    for frame in _frames(video, step):
        total += np.abs(frame.astype(np.int16) - reference).max(axis=2) > threshold
        count += 1
    if count == 0:
        raise ValueError(f"no frames decoded from {video}")
    return (total / count).astype(np.float32), count


def normalise(values: np.ndarray, top_percentile: float = 99.9) -> np.ndarray:
    """Square-root compressed and scaled to [0, 1], so a few saturated queues do not dominate."""
    top = float(np.percentile(values, top_percentile))
    if top <= 0:
        raise ValueError("nothing to normalise: the map is empty")
    return np.clip(np.sqrt(np.clip(values, 0, None) / top), 0, 1).astype(np.float32)


@dataclass(frozen=True)
class Registration:
    raster_to_image: np.ndarray  # 3x3, raster (col, row) -> image (u, v); last entry 1
    correlation: float  # Pearson r of the blurred maps over the image mask, after the fit
    initial_correlation: float
    converged: bool
    note: str


def correlation(
    raster: np.ndarray, image_map: np.ndarray, raster_to_image: np.ndarray, mask: np.ndarray,
    sigma: float = 2.0,
) -> float:  # fmt: skip
    """Pearson correlation of the image map and the raster warped into the image, over `mask`."""
    height, width = image_map.shape
    warped = cv2.warpPerspective(
        cv2.GaussianBlur(raster, (0, 0), sigma), raster_to_image.astype(np.float64),
        (width, height), flags=cv2.INTER_LINEAR,
    )  # fmt: skip
    blurred = cv2.GaussianBlur(image_map, (0, 0), sigma)
    m = mask > 0
    if warped[m].std() == 0 or blurred[m].std() == 0:
        return float("nan")
    return float(np.corrcoef(blurred[m], warped[m])[0, 1])


ECC_SCHEDULE = (
    (6.0, cv2.MOTION_AFFINE),
    (3.0, cv2.MOTION_AFFINE),
    (3.0, cv2.MOTION_HOMOGRAPHY),
    (1.5, cv2.MOTION_HOMOGRAPHY),
)


def register(
    raster: np.ndarray,
    image_map: np.ndarray,
    initial_raster_to_image: np.ndarray,
    mask: np.ndarray,
    schedule: Sequence[tuple[float, int]] = ECC_SCHEDULE,
    iterations: int = 300,
) -> Registration:
    """Refine a raster-to-image warp by ECC, coarse to fine (`schedule`: blur sigma, motion)."""
    warp = np.asarray(initial_raster_to_image, np.float64)
    warp = (warp / warp[2, 2]).astype(np.float32)
    start = correlation(raster, image_map, warp, mask)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iterations, 1e-6)
    mask8 = (mask > 0).astype(np.uint8) * 255
    try:
        for sigma, motion in schedule:
            template = cv2.GaussianBlur(raster.astype(np.float32), (0, 0), sigma)
            target = cv2.GaussianBlur(image_map.astype(np.float32), (0, 0), sigma)
            current = warp[:2].copy() if motion == cv2.MOTION_AFFINE else warp.copy()
            _, current = cv2.findTransformECC(template, target, current, motion, criteria, mask8, 5)
            warp = np.vstack([current, [0, 0, 1]]) if motion == cv2.MOTION_AFFINE else current
            warp = (warp / warp[2, 2]).astype(np.float32)
    except cv2.error as exc:
        return Registration(warp.astype(np.float64), float("nan"), start, False, f"ECC: {exc.err}")
    return Registration(
        warp.astype(np.float64), correlation(raster, image_map, warp, mask), start, True, "ok"
    )


@dataclass(frozen=True)
class Peak:
    position: float  # sub-sample position on the profile's axis
    height: float
    width: float  # full width at half height, same unit as position


def profile_peaks(
    coords: np.ndarray, profile: np.ndarray, min_height: float, min_separation: float
) -> list[Peak]:
    """Local maxima of a 1-D profile above `min_height`, refined by a parabola through 3 samples.

    Kept greedily from the highest, at least `min_separation` apart (same unit as `coords`).
    """
    coords, profile = np.asarray(coords, float), np.asarray(profile, float)
    candidates = [
        i
        for i in range(1, len(profile) - 1)
        if profile[i] >= min_height and profile[i] >= profile[i - 1] and profile[i] > profile[i + 1]
    ]
    step = float(coords[1] - coords[0]) if len(coords) > 1 else 1.0
    peaks: list[Peak] = []
    for i in sorted(candidates, key=lambda j: -profile[j]):
        a, b, c = profile[i - 1], profile[i], profile[i + 1]
        denom = a - 2 * b + c
        offset = 0.5 * (a - c) / denom if denom != 0 else 0.0
        position = float(coords[i] + offset * step)
        if any(abs(position - p.position) < min_separation for p in peaks):
            continue
        half = b / 2
        lo = i
        while lo > 0 and profile[lo] > half:
            lo -= 1
        hi = i
        while hi < len(profile) - 1 and profile[hi] > half:
            hi += 1
        peaks.append(Peak(position, float(b), float((hi - lo) * step)))
    return sorted(peaks, key=lambda p: p.position)


def paint_strength(image_bgr: np.ndarray, kind: str = "white") -> np.ndarray:
    """Per-pixel paint evidence: white = darkest channel; yellow = mean(R, G) minus B."""
    img = image_bgr.astype(np.float32)
    if kind == "white":
        return img.min(axis=2)
    if kind == "yellow":
        return (img[..., 2] + img[..., 1]) / 2 - img[..., 0]
    raise ValueError(f"unknown paint kind {kind!r}")
