"""Median-background blob detector for camera 2: the floor any learned detector has to beat.

The camera is fixed (phase correlation against the median stays under 0.1 px across the dev
blocks) and looks almost straight down, so a vehicle is a compact region that differs from an
empty road. The background comes from dev frames only. Thresholds in `BlobParams` were tuned on
dev frames outside the three SAM windows (`scripts/cv_tune_blob.py`).

A queue that stands still in one lane for more than half of the sampled frames would enter the
median; the samples are spread over eight blocks of a 15-minute run to keep that rare.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np

from cairoflow_perception.local.scoring import (
    USABLE_V_PX,
    Detection,
    DetectorContext,
    FrameSampler,
)

LABEL = "vehicle"


@dataclass(frozen=True)
class BlobParams:
    """Lengths in image pixels; a car's footprint is about 25 x 13 px here. The values are the
    ones scripts/cv_tune_blob.py chose (blob-tuning.json in the cv-bakeoff record)."""

    diff_threshold: int = 35  # largest channel difference from the background, 0..255
    blur_px: int = 3  # box blur before differencing, against MPEG-4 block noise; 1 is off
    close_px: int = 7  # closing joins a car's bright and dark halves
    open_px: int = 5  # opening removes noise and thin bridges between neighbours
    min_area_px: int = 90
    max_area_px: int = 700  # a larger region is a queue or a bus, and is split
    min_side_px: int = 5
    max_side_px: int = 42  # likewise for a longer one
    max_region_px: int = 12000  # larger still is a lighting change, not traffic
    split_core_px: int = 3  # a region is cut where it narrows to under twice this
    car_long_px: int = 30  # what a split falls back on: a car plus the halo morphology leaves
    car_wide_px: int = 16
    min_tile_fill_pct: int = 40  # a tile less filled than this is road, not a vehicle
    top_v_px: int = 0  # rows above this are ignored; a static strip cancels in the difference
    bottom_v_px: int = USABLE_V_PX  # rows from here down are the glass band


def median_background(sampler: FrameSampler, times: Iterable[float]) -> np.ndarray:
    """Per-pixel, per-channel median of the given dev frames (uint8 BGR)."""
    stack = np.stack([sampler.read(t) for t in times])
    return np.median(stack, axis=0).astype(np.uint8)


class MedianBlobDetector:
    """Threshold the difference from the background, clean up, keep vehicle-sized regions."""

    def __init__(self, background_bgr: np.ndarray, params: BlobParams | None = None) -> None:
        import cv2  # noqa: PLC0415 - optional at import time

        self._cv2 = cv2
        self.params = params or BlobParams()
        self._background = self._smooth(background_bgr).astype(np.int16)

    def _smooth(self, image: np.ndarray) -> np.ndarray:
        k = self.params.blur_px
        return self._cv2.blur(image, (k, k)) if k > 1 else image

    def foreground(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(difference image, cleaned 0/255 mask)."""
        cv2, p = self._cv2, self.params
        diff = np.abs(self._smooth(frame_bgr).astype(np.int16) - self._background)
        diff = diff.max(axis=2).astype(np.uint8)
        mask = np.where(diff >= p.diff_threshold, 255, 0).astype(np.uint8)
        mask[: p.top_v_px] = 0
        mask[p.bottom_v_px :] = 0
        if p.close_px > 1:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (p.close_px, p.close_px))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        if p.open_px > 1:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (p.open_px, p.open_px))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        return diff, mask

    def _fits(self, w: int, h: int, area: int) -> bool:
        p = self.params
        return area <= p.max_area_px and max(w, h) <= p.max_side_px

    def _tiles(self, region: np.ndarray) -> list[tuple[int, int, int, int]]:
        """A region too big for one vehicle, cut into vehicle-sized tiles along its long side and
        across it: the last resort for a queue with no visible gap, where a lane's cars touch."""
        p = self.params
        h, w = region.shape
        along, across = (w, h) if w >= h else (h, w)
        n_along = max(1, round(along / p.car_long_px))
        n_across = max(1, round(across / p.car_wide_px))
        nx, ny = (n_along, n_across) if w >= h else (n_across, n_along)
        xs = np.linspace(0, w, nx + 1).round().astype(int)
        ys = np.linspace(0, h, ny + 1).round().astype(int)
        out = []
        for i in range(ny):
            for j in range(nx):
                tile = region[ys[i] : ys[i + 1], xs[j] : xs[j + 1]]
                if tile.size and tile.mean() * 100 >= p.min_tile_fill_pct:
                    out.append((int(xs[j]), int(ys[i]), int(xs[j + 1]), int(ys[i + 1])))
        return out

    def _split(self, region: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Boxes (x1, y1, x2, y2, relative to the region) for one oversized region: cut it at its
        narrow necks, then tile whatever is still too big."""
        cv2, p = self._cv2, self.params
        padded = np.pad(region.astype(np.uint8), 1)
        distance = cv2.distanceTransform(padded, cv2.DIST_L2, 3)[1:-1, 1:-1]
        cores = (distance >= p.split_core_px).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(cores, connectivity=8)
        if count <= 2:  # no neck to cut at
            return self._tiles(region)
        # every pixel of the region goes to its nearest core, so no part of a vehicle is lost
        _, nearest = cv2.distanceTransformWithLabels(
            (labels == 0).astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_CCOMP
        )
        core_of = np.zeros(int(nearest.max()) + 1, np.int32)
        core_of[nearest[labels > 0]] = labels[labels > 0]
        owner = core_of[nearest]
        out = []
        for i in range(1, count):
            part = region & (owner == i)
            ys, xs = np.nonzero(part)
            if xs.size == 0:
                continue
            x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
            if self._fits(x2 - x1, y2 - y1, int(xs.size)):
                out.append((x1, y1, x2, y2))
            else:
                out += [
                    (x1 + a, y1 + b, x1 + c, y1 + d)
                    for a, b, c, d in self._tiles(part[y1:y2, x1:x2])
                ]
        return out

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        cv2, p = self._cv2, self.params
        diff, mask = self.foreground(frame_bgr)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        out = []
        for i in range(1, count):
            x, y, w, h, area = (int(a) for a in stats[i])
            if not p.min_area_px <= area <= p.max_region_px or min(w, h) < p.min_side_px:
                continue
            region = labels[y : y + h, x : x + w] == i
            boxes = [(0, 0, w, h)] if self._fits(w, h, area) else self._split(region)
            for x1, y1, x2, y2 in boxes:
                inside = diff[y + y1 : y + y2, x + x1 : x + x2][region[y1:y2, x1:x2]]
                if inside.size < p.min_area_px // 2:
                    continue
                score = min(float(inside.mean()) / 128.0, 1.0)
                out.append(
                    Detection(float(x + x1), float(y + y1), float(x + x2), float(y + y2), score,
                              LABEL)
                )  # fmt: skip
        return out

    def describe(self) -> dict[str, Any]:
        return {"kind": "median background, connected components, queue split",
                **asdict(self.params)}  # fmt: skip


def make_detector(context: DetectorContext) -> MedianBlobDetector:
    """Factory for scripts/cv_bakeoff.py. --detector-arg name=value overrides a `BlobParams`
    field."""
    params = replace(BlobParams(), **{k: int(v) for k, v in context.args.items()})
    return MedianBlobDetector(median_background(context.sampler, context.dev_times), params)
