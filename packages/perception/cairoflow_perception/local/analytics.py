"""Tracks to the numbers a screen shows: the same metric, series and movement ids as the reference
analysis (scripts/build_reference_analysis.py), so a camera analysis and a reference analysis can
be read side by side.

Every value here is camera-derived, so `evidence_kind` is always "approximated", and its status is
"ungated" with a reason until a registered gate applies (`apply_gate_outcomes`). What cannot be
computed is status "not_available" with a reason and no value; nothing falls back to a default.

Speed. Ground positions come from a homography whose along-road distances are not accepted
(ADR-018). A speed is along-road distance over time, so `speed.mean_moving` and `stopped.share` are
withheld, and no per-point speed is written: every trajectory row stores NO_SPEED, so no screen can
show one. `PlacedTrack.speed_m_s` exists only inside a run, to pick the moving rows a size is read
from. A gate that tests speed recomputes it from the ground positions with `centred_speed`.

Counts are of tracks that are not fragments. A fragment is a track too short to be a vehicle's
passage (`MIN_TRACK_S`); it stays in the files, flagged, and out of the totals. Entering counts also
leave out a track first seen mid-view (local/zones.py): it stays in `vehicles.total` and its turn
stays in the movements, and `mid_view_starts` gives how many there were on each arm.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from cairoflow_contracts.analysis_record import (
    GateOutcome,
    Metric,
    MetricSeries,
    MovementCell,
)
from cairoflow_perception.analysis_store import (
    POINT_INTERPOLATED,
    POINT_OUTSIDE_VALID_REGION,
    TrajectoryColumns,
    quantise,
)
from cairoflow_perception.local import zones
from cairoflow_perception.local.scoring import IMAGE_H_PX, IMAGE_W_PX
from cairoflow_perception.local.tracker import FilledTrack
from cairoflow_perception.ngsim_junction import ARMS
from cairoflow_perception.site_int2 import VALID_BELOW_ROW_PX, Site

EVIDENCE = "approximated"
UNGATED = (
    "camera CV output with approximated geometry; no registered gate has checked this value, so "
    "it is agreement-unknown, not measured truth"
)
WITHHELD_SPEED = (
    "withheld: speed needs along-road distance, which is not accepted from this homography "
    "(ADR-018), and no registered gate has passed it"
)
NO_GROUND = "not available: this analysis is uncalibrated, so it has no ground positions"
NO_ARMS = "not available: this analysis is uncalibrated, so tracks have no arms"
SPEED_WINDOW_S = 1.0  # a point's speed is the ground displacement over this span, centred on it
MIN_TRACK_S = 1.0  # shorter than this is a fragment, not a vehicle's passage
MIN_SIZE_POINTS = 3  # a placed size needs this many observed points
DETECTOR_CLASSES = {
    "car": "car",
    "truck": "truck",
    "bus": "bus",
    "motorcycle": "motorcycle",
    "motorbike": "motorcycle",
}
GROUND_CELL_M = 2.0
IMAGE_CELL_PX = 16.0
# a density grid covers the study area; ground points beyond it are not binned
GROUND_HALF_M = 200.0


@dataclass(frozen=True)
class PlacedTrack:
    """A filled track with what calibration adds. Ground arrays are None when uncalibrated."""

    track: FilledTrack
    flags: np.ndarray  # POINT_* bits per row
    ground_u_m: np.ndarray | None
    ground_v_m: np.ndarray | None
    speed_m_s: np.ndarray | None
    heading_deg: np.ndarray | None
    movement: zones.Movement
    vehicle_class: str
    class_source: str
    class_confidence: float | None
    length_m: float | None
    width_m: float | None
    status: str

    @property
    def counted(self) -> bool:
        return self.status != "fragment"

    @property
    def usable(self) -> np.ndarray:
        """Rows that are measurements inside the valid image region."""
        return (self.flags & (POINT_INTERPOLATED | POINT_OUTSIDE_VALID_REGION)) == 0


def centred_speed(
    u_m: np.ndarray, v_m: np.ndarray, frame_dt_s: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(speed m/s, velocity u, velocity v) from ground positions over a centred window. A row near
    the end of a track uses the part of the window that exists; a one-row track has no speed."""
    n = len(u_m)
    if n < 2:
        nothing = np.full(n, np.nan)
        return nothing, nothing.copy(), nothing.copy()
    half = max(1, round(SPEED_WINDOW_S / frame_dt_s / 2))
    index = np.arange(n)
    low = np.maximum(index - half, 0)
    high = np.minimum(index + half, n - 1)
    span_s = (high - low) * frame_dt_s
    vel_u = (u_m[high] - u_m[low]) / span_s
    vel_v = (v_m[high] - v_m[low]) / span_s
    return np.hypot(vel_u, vel_v), vel_u, vel_v


def placed_size(
    site: Site, track: FilledTrack, usable: np.ndarray, heading_deg: np.ndarray, speed: np.ndarray
) -> tuple[float | None, float | None]:
    """Length and width in metres: the image box's ground extent along and across the heading, the
    median over observed moving rows. Rough by nature: an axis-aligned box of a car on a diagonal
    is bigger than the car, and it is only used for a coarse class."""
    rows = np.flatnonzero(usable & (speed > zones.STOPPED_M_S))
    if len(rows) < MIN_SIZE_POINTS:
        return None, None
    x, y, w, h = track.x[rows], track.y[rows], track.w[rows], track.h[rows]
    corners = np.stack(
        [
            site.project_to_junction(x + sx * w / 2, y + sy * h / 2)
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        ]
    )  # (4, n, 2)
    b = np.radians(heading_deg[rows])
    along = corners[..., 0] * np.sin(b) + corners[..., 1] * np.cos(b)
    across = corners[..., 0] * np.cos(b) - corners[..., 1] * np.sin(b)
    length = float(np.median(along.max(axis=0) - along.min(axis=0)))
    width = float(np.median(across.max(axis=0) - across.min(axis=0)))
    return (round(length, 2) if length > 0 else None, round(width, 2) if width > 0 else None)


def track_class(
    labels: Mapping[str, float], length_m: float | None
) -> tuple[str, str, float | None]:
    """(class, class_source, confidence). The detector's class when it gave one, else the placed
    size by the reference's length bands, else nothing claimed."""
    votes: Counter[str] = Counter()
    for label, score in labels.items():
        name = DETECTOR_CLASSES.get(label.lower())
        if name:
            votes[name] += score
    total = sum(labels.values())
    if votes and total > 0:
        name, score = min(votes.items(), key=lambda item: (-item[1], item[0]))
        return name, "detector", round(score / total, 3)
    if length_m is not None:
        return zones.vehicle_class(length_m), "size", None
    return "vehicle", "none", None


def place_track(
    track: FilledTrack,
    site: Site | None,
    frame_dt_s: float,
    *,
    valid_below_row_px: int | None = VALID_BELOW_ROW_PX,
    image_size_px: tuple[int, int] = (IMAGE_W_PX, IMAGE_H_PX),
) -> PlacedTrack:
    """Flags, ground path, speed, heading, movement, class and status of one track. Rows at or past
    `valid_below_row_px` are flagged; None means the whole image is valid. `image_size_px` is
    (width, height), and with that row bounds the region a track can arrive from."""
    flags = np.where(track.interpolated, POINT_INTERPOLATED, 0).astype("u1")
    if valid_below_row_px is not None:
        outside = np.where(track.y >= valid_below_row_px, POINT_OUTSIDE_VALID_REGION, 0)
        flags = flags | outside.astype("u1")
    short = len(track) * frame_dt_s < MIN_TRACK_S
    usable = (flags & (POINT_INTERPOLATED | POINT_OUTSIDE_VALID_REGION)) == 0
    if site is None:
        name, source, confidence = track_class(track.labels, None)
        return PlacedTrack(
            track, flags, None, None, None, None, zones.NO_MOVEMENT, name, source, confidence,
            None, None, "fragment" if short else "partial",
        )  # fmt: skip
    ground = site.project_to_junction(track.x, track.y)
    u, v = ground[:, 0], ground[:, 1]
    speed, vel_u, vel_v = centred_speed(u, v, frame_dt_s)
    heading = zones.headings_deg(vel_u, vel_v, np.nan_to_num(speed))
    movement = zones.classify(
        u[usable],
        v[usable],
        entered=_entered(track, usable, frame_dt_s, image_size_px, valid_below_row_px),
    )
    length_m, width_m = placed_size(site, track, usable, heading, np.nan_to_num(speed))
    name, source, confidence = track_class(track.labels, length_m)
    status = "fragment" if short else ("complete" if movement.complete else "partial")
    return PlacedTrack(
        track, flags, u, v, speed, heading, movement, name, source, confidence,
        length_m, width_m, status,
    )  # fmt: skip


def _entered(
    track: FilledTrack,
    usable: np.ndarray,
    frame_dt_s: float,
    image_size_px: tuple[int, int],
    valid_below_row_px: int | None,
) -> bool:
    """Whether this track's first usable point makes it an arrival (zones.enters_view)."""
    rows = np.flatnonzero(usable)
    if not len(rows):
        return False
    first = int(rows[0])
    width_px, height_px = image_size_px
    bottom_px = height_px if valid_below_row_px is None else min(height_px, valid_below_row_px)
    since_start_s = (track.start_frame + first) * frame_dt_s
    return zones.enters_view(
        float(track.x[first]), float(track.y[first]), since_start_s, frame_dt_s, width_px, bottom_px
    )


def mid_view_starts(placed: Sequence[PlacedTrack]) -> Counter[str]:
    """Counted tracks per arm that have an arm but are left out of entering counts."""
    return Counter(
        p.movement.approach
        for p in placed
        if p.counted and p.movement.approach and not p.movement.entering
    )


def columns_of(placed: Sequence[PlacedTrack]) -> TrajectoryColumns:
    """The trajectories.bin columns. No ground is a stored 'no value', never zero, and speed is
    always one: the module docstring says why."""
    if not placed:
        empty = np.zeros(0)
        return quantise(x_px=empty, y_px=empty, w_px=empty, h_px=empty)

    def joined(name: str) -> np.ndarray:
        return np.concatenate([getattr(p.track, name) for p in placed])

    calibrated = placed[0].ground_u_m is not None
    return quantise(
        x_px=joined("x"),
        y_px=joined("y"),
        w_px=joined("w"),
        h_px=joined("h"),
        ground_x_m=np.concatenate([p.ground_u_m for p in placed]) if calibrated else None,
        ground_y_m=np.concatenate([p.ground_v_m for p in placed]) if calibrated else None,
        heading_deg=np.concatenate([p.heading_deg for p in placed]) if calibrated else None,
        confidence=joined("conf"),
        point_flags=np.concatenate([p.flags for p in placed]),
    )


def _metric(metric_id: str, label: str, value: float, unit: str, scope: str = "site") -> Metric:
    return Metric(
        metric_id=metric_id, label=label, value=value, unit=unit, scope=scope,
        evidence_kind=EVIDENCE, status="ungated", reason=UNGATED,
    )  # fmt: skip


def _absent(metric_id: str, label: str, unit: str, reason: str, scope: str = "site") -> Metric:
    return Metric(
        metric_id=metric_id, label=label, value=None, unit=unit, scope=scope,
        evidence_kind=EVIDENCE, status="not_available", reason=reason,
    )  # fmt: skip


def compute_metrics(
    placed: Sequence[PlacedTrack], *, calibrated: bool, seconds: float
) -> tuple[Metric, ...]:
    counted = [p for p in placed if p.counted]
    entering = [p for p in counted if p.movement.entering]
    metrics = [_metric("vehicles.total", "Vehicles in view", float(len(counted)), "veh")]
    if calibrated:
        metrics.append(
            _metric(
                "flow.entering", "Entering flow", round(len(entering) / seconds * 3600), "veh/h"
            )
        )
        metrics.append(_absent("speed.mean_moving", "Mean moving speed", "m/s", WITHHELD_SPEED))
        metrics.append(_absent("stopped.share", "Share of time stopped", "share", WITHHELD_SPEED))
    else:
        metrics.append(_absent("flow.entering", "Entering flow", "veh/h", NO_ARMS))
        metrics.append(_absent("speed.mean_moving", "Mean moving speed", "m/s", NO_GROUND))
        metrics.append(_absent("stopped.share", "Share of time stopped", "share", NO_GROUND))
    arms = Counter(p.movement.entering for p in entering)
    for arm in ARMS:
        metric_id, label, scope = (
            f"vehicles.arm.{arm.lower()}",
            f"Entering from {arm}",
            f"arm:{arm}",
        )
        if calibrated:
            metrics.append(_metric(metric_id, label, float(arms.get(arm, 0)), "veh", scope))
        else:
            metrics.append(_absent(metric_id, label, "veh", NO_ARMS, scope))
    classes = Counter(p.vehicle_class for p in counted)
    metrics += [
        _metric(f"vehicles.class.{name}", name.capitalize(), float(count), "veh", f"class:{name}")
        for name, count in sorted(classes.items())
    ]
    return tuple(metrics)


def compute_series(
    placed: Sequence[PlacedTrack], *, calibrated: bool, seconds: float, fps: float
) -> tuple[MetricSeries, ...]:
    bin_s = 10.0 if seconds <= 120 else 60.0
    n_bins = math.ceil(seconds / bin_s)
    series = []
    for arm in ARMS:
        common = {
            "series_id": f"entering.arm.{arm.lower()}",
            "label": f"Entering from {arm}",
            "unit": "veh",
            "scope": f"arm:{arm}",
            "bin_s": bin_s,
            "start_s": 0.0,
            "evidence_kind": EVIDENCE,
        }
        if not calibrated:
            series.append(
                MetricSeries(
                    **common, values=(None,) * n_bins, status="not_available", reason=NO_ARMS
                )
            )
            continue
        counts = [0.0] * n_bins
        for p in placed:
            if p.counted and p.movement.entering == arm:
                start_s = p.track.start_frame / fps
                counts[min(int(start_s // bin_s), n_bins - 1)] += 1
        series.append(
            MetricSeries(**common, values=tuple(counts), status="ungated", reason=UNGATED)
        )
    return tuple(series)


def compute_movements(placed: Sequence[PlacedTrack]) -> tuple[MovementCell, ...]:
    """Observed cells only, as the reference writes them. `reference_count` stays None here: the
    comparison belongs to cairoflow_evaluation.cv_agreement."""
    cells = Counter(
        (p.movement.approach, p.movement.exit_arm, p.movement.turn)
        for p in placed
        if p.counted and p.movement.complete
    )
    return tuple(
        MovementCell(
            approach=approach, exit_arm=exit_arm, turn=turn, count=count, status="ungated",
            reason=UNGATED,
        )
        for (approach, exit_arm, turn), count in sorted(cells.items())
    )  # fmt: skip


def density_grid(placed: Sequence[PlacedTrack], *, width_px: int, height_px: int) -> dict:
    """Vehicle-frames per cell: observed rows inside the valid region of counted tracks. On the
    ground frame when calibrated, on the image otherwise. The cell of a point is
    floor((value - origin) / cell_size); counts are row-major, y first."""
    calibrated = bool(placed) and placed[0].ground_u_m is not None
    points = []
    for p in placed:
        if not p.counted:
            continue
        keep = p.usable
        if calibrated:
            points.append(np.stack([p.ground_u_m[keep], p.ground_v_m[keep]], axis=1))
        else:
            points.append(np.stack([p.track.x[keep], p.track.y[keep]], axis=1))
    stack = np.concatenate(points) if points else np.zeros((0, 2))
    if calibrated:
        cell, origin = GROUND_CELL_M, (-GROUND_HALF_M, -GROUND_HALF_M)
        nx = ny = round(2 * GROUND_HALF_M / GROUND_CELL_M)
        frame, unit = "local_enu_junction_m", "m"
    else:
        cell, origin = IMAGE_CELL_PX, (0.0, 0.0)
        nx, ny = math.ceil(width_px / cell), math.ceil(height_px / cell)
        frame, unit = "image_px", "px"
    ix = np.floor((stack[:, 0] - origin[0]) / cell).astype(int)
    iy = np.floor((stack[:, 1] - origin[1]) / cell).astype(int)
    inside = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    counts = np.zeros((ny, nx), dtype=int)
    np.add.at(counts, (iy[inside], ix[inside]), 1)
    return {
        "format": "density/1",
        "frame": frame,
        "unit": unit,
        "cell_size": cell,
        "origin": list(origin),
        "nx": nx,
        "ny": ny,
        "value": "vehicle-frames per cell",
        "evidence_kind": EVIDENCE,
        "points_outside_grid": int((~inside).sum()),
        "counts": counts.tolist(),
    }


def density_bytes(grid: dict) -> bytes:
    return (json.dumps(grid, separators=(",", ":")) + "\n").encode()


def apply_gate_outcomes(
    metrics: Sequence[Metric], outcomes: Sequence[GateOutcome], mapping: Mapping[str, str]
) -> tuple[Metric, ...]:
    """Metrics after a registered gate. `mapping` names which gate check covers which metric id.

    A passed check makes a value gate_pass; a failed one removes it (gate_fail carries no value).
    Evidence stays approximated: a gate says the value agrees with a correlated reference, not that
    the geometry was measured. A withheld metric has no value to release here; whoever holds the
    gate result recomputes it from the ground positions with `centred_speed`.
    """
    by_metric = {o.metric: o for o in outcomes}
    out = []
    for metric in metrics:
        check = by_metric.get(mapping.get(metric.metric_id, ""))
        if check is None or metric.value is None:
            out.append(metric)
        elif check.passed:
            out.append(
                metric.model_copy(
                    update={"status": "gate_pass", "reason": None, "gate_id": check.gate_id}
                )
            )
        else:
            out.append(
                metric.model_copy(
                    update={
                        "status": "gate_fail",
                        "value": None,
                        "gate_id": check.gate_id,
                        "reason": f"failed {check.gate_id} on {check.metric}: "
                        f"{check.measured} against {check.comparator} {check.threshold}",
                    }
                )
            )
    return tuple(out)
