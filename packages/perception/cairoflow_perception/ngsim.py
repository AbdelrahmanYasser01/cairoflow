"""NGSIM Lankershim trajectories: loading, coordinates and per-lane statistics.

Source: table `8ect-6jqj` on data.transportation.gov (CC BY-SA 3.0 Unported), filtered to
`location = 'lankershim'`; the columns used are listed in `COLUMNS` (DATA_REGISTER.md §13).

Coordinates (verified, not assumed; DATA_REGISTER.md §14):
- `global_x`, `global_y` are the front centre of the vehicle in NAD83 California State Plane
  zone 5 (EPSG:2229), US survey feet. The metadata PDF says zone 5 in its text and "State Plane
  III" in the column table; only zone 5 puts the data inside the published bounding box.
- Local ground frame: metres from a stated State Plane origin, x along grid east, y along grid
  north. Grid north differs from true north by the meridian convergence (about 0.2 degrees at
  Lankershim, `LambertConic2SP.convergence_deg`); the grid scale factor is within 1e-4 of 1.
- Bearings are degrees clockwise from grid north.

Kinematics come from consecutive rows of the same `vehicle_id` exactly one frame (100 ms) apart;
a gap or a reused id leaves the value undefined (NaN) rather than inventing a step.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np

COLUMNS = (
    "vehicle_id",
    "frame_id",
    "global_time",
    "global_x",
    "global_y",
    "v_length",
    "v_width",
    "lane_id",
    "int_id",
    "section_id",
    "direction",
    "movement",
)
US_SURVEY_FOOT_M = 1200 / 3937
FRAME_MS = 100
DIRECTIONS = {1: "EB", 2: "NB", 3: "WB", 4: "SB"}  # metadata PDF, column "Direction"
MOVEMENTS = {1: "through", 2: "left", 3: "right"}  # metadata PDF, column "Movement"
LEFT_BAY_FIRST, RIGHT_BAY_FIRST = 11, 31  # lane_id numbering of turn bays (metadata PDF)

_GRS80_A = 6378137.0
_GRS80_F = 1 / 298.257222101


@dataclass(frozen=True)
class LambertConic2SP:
    """Lambert conformal conic, two standard parallels, on GRS80 (Snyder 1987, ch. 15)."""

    name: str
    lat0_deg: float
    lon0_deg: float
    parallel1_deg: float
    parallel2_deg: float
    false_easting: float
    false_northing: float
    unit_m: float

    def _constants(self) -> tuple[float, float, float, float]:
        e = math.sqrt(2 * _GRS80_F - _GRS80_F**2)
        p1, p2 = math.radians(self.parallel1_deg), math.radians(self.parallel2_deg)
        n = (math.log(_m(p1, e)) - math.log(_m(p2, e))) / (
            math.log(_t(p1, e)) - math.log(_t(p2, e))
        )
        big_f = _m(p1, e) / (n * _t(p1, e) ** n)
        rho0 = _GRS80_A * big_f * _t(math.radians(self.lat0_deg), e) ** n
        return e, n, big_f, rho0

    def to_geographic(self, easting, northing) -> tuple[np.ndarray, np.ndarray]:
        """Projected coordinates (in the CRS unit) to latitude and longitude in degrees."""
        e, n, big_f, rho0 = self._constants()
        x = (np.asarray(easting, dtype=np.float64) - self.false_easting) * self.unit_m
        y = (np.asarray(northing, dtype=np.float64) - self.false_northing) * self.unit_m
        rho = math.copysign(1.0, n) * np.hypot(x, rho0 - y)
        theta = np.arctan2(x, rho0 - y)
        t = (rho / (_GRS80_A * big_f)) ** (1 / n)
        phi = np.pi / 2 - 2 * np.arctan(t)
        for _ in range(12):
            esin = e * np.sin(phi)
            phi = np.pi / 2 - 2 * np.arctan(t * ((1 - esin) / (1 + esin)) ** (e / 2))
        return np.degrees(phi), np.degrees(theta / n) + self.lon0_deg

    def from_geographic(self, lat_deg, lon_deg) -> tuple[np.ndarray, np.ndarray]:
        """Latitude and longitude in degrees to projected coordinates (in the CRS unit)."""
        e, n, big_f, rho0 = self._constants()
        phi = np.radians(np.asarray(lat_deg, dtype=np.float64))
        rho = _GRS80_A * big_f * _t(phi, e) ** n
        theta = n * np.radians(np.asarray(lon_deg, dtype=np.float64) - self.lon0_deg)
        x = rho * np.sin(theta) / self.unit_m + self.false_easting
        y = (rho0 - rho * np.cos(theta)) / self.unit_m + self.false_northing
        return x, y

    def convergence_deg(self, lon_deg: float) -> float:
        """Angle from true north to grid north at a longitude (positive east of the meridian)."""
        _, n, _, _ = self._constants()
        return n * (lon_deg - self.lon0_deg)

    def scale_factor(self, lat_deg: float) -> float:
        """Grid distance over ellipsoid distance at a latitude."""
        e, n, big_f, _ = self._constants()
        phi = math.radians(lat_deg)
        rho = _GRS80_A * big_f * _t(phi, e) ** n
        return rho * n / (_GRS80_A * _m(phi, e))


def _m(phi, e):
    return np.cos(phi) / np.sqrt(1 - (e * np.sin(phi)) ** 2)


def _t(phi, e):
    esin = e * np.sin(phi)
    return np.tan(np.pi / 4 - phi / 2) / ((1 - esin) / (1 + esin)) ** (e / 2)


# Parameters from the EPSG registry (epsg.io/2229.wkt and /2227.wkt, read 2026-10-27).
CA_ZONE5_FTUS = LambertConic2SP(
    "EPSG:2229 NAD83 / California zone 5 (ftUS)",
    33.5, -118.0, 35.4666666666667, 34.0333333333333, 6561666.667, 1640416.667, US_SURVEY_FOOT_M,
)  # fmt: skip
CA_ZONE3_FTUS = LambertConic2SP(
    "EPSG:2227 NAD83 / California zone 3 (ftUS)",
    36.5, -120.5, 38.4333333333333, 37.0666666666667, 6561666.667, 1640416.667, US_SURVEY_FOOT_M,
)  # fmt: skip


@dataclass(frozen=True)
class Trajectories:
    """One row per vehicle and 0.1 s frame, as published (codes in DIRECTIONS / MOVEMENTS)."""

    vehicle_id: np.ndarray
    frame_id: np.ndarray
    global_time_ms: np.ndarray
    x_ft: np.ndarray
    y_ft: np.ndarray
    length_ft: np.ndarray
    width_ft: np.ndarray
    lane_id: np.ndarray
    int_id: np.ndarray
    section_id: np.ndarray
    direction: np.ndarray
    movement: np.ndarray

    def __len__(self) -> int:
        return len(self.vehicle_id)

    def subset(self, mask: np.ndarray) -> Trajectories:
        return Trajectories(**{f.name: getattr(self, f.name)[mask] for f in fields(self)})


def load_trajectories(path: Path) -> Trajectories:
    """Read the CSV written by the Socrata export; refuses any other header."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = tuple(next(reader))
        if header != COLUMNS:
            raise ValueError(f"unexpected header {header}; expected {COLUMNS}")
        rows = np.array(list(reader), dtype=object)
    if rows.size == 0:
        raise ValueError(f"{path} has no data rows")
    col = {name: rows[:, i] for i, name in enumerate(COLUMNS)}

    def ints(name: str) -> np.ndarray:
        return col[name].astype(np.int64)

    def floats(name: str) -> np.ndarray:
        values = col[name].astype(np.float64)
        if not np.all(np.isfinite(values)):
            raise ValueError(f"non-finite values in {name}")
        return values

    return Trajectories(
        vehicle_id=ints("vehicle_id"),
        frame_id=ints("frame_id"),
        global_time_ms=ints("global_time"),
        x_ft=floats("global_x"),
        y_ft=floats("global_y"),
        length_ft=floats("v_length"),
        width_ft=floats("v_width"),
        lane_id=ints("lane_id"),
        int_id=ints("int_id"),
        section_id=ints("section_id"),
        direction=ints("direction"),
        movement=ints("movement"),
    )


@dataclass(frozen=True)
class LocalFrame:
    """Metres from a State Plane origin (US survey feet); x grid east, y grid north."""

    origin_x_ft: float
    origin_y_ft: float

    def to_local(self, x_ft, y_ft) -> tuple[np.ndarray, np.ndarray]:
        return (
            (np.asarray(x_ft) - self.origin_x_ft) * US_SURVEY_FOOT_M,
            (np.asarray(y_ft) - self.origin_y_ft) * US_SURVEY_FOOT_M,
        )

    def to_state_plane(self, x_m, y_m) -> tuple[np.ndarray, np.ndarray]:
        return (
            np.asarray(x_m) / US_SURVEY_FOOT_M + self.origin_x_ft,
            np.asarray(y_m) / US_SURVEY_FOOT_M + self.origin_y_ft,
        )


def kinematics(traj: Trajectories, frame: LocalFrame) -> tuple[np.ndarray, np.ndarray]:
    """Speed (m/s) and heading (grid bearing, degrees) per row, from the previous frame.

    Speed is NaN where the same vehicle_id has no row exactly FRAME_MS earlier. A row where the
    vehicle did not move keeps its last heading (or, before its first move, its first heading),
    so queued vehicles keep their orientation; a vehicle that never moves has NaN heading.
    """
    x, y = frame.to_local(traj.x_ft, traj.y_ft)
    order = np.lexsort((traj.global_time_ms, traj.vehicle_id))
    vid, t = traj.vehicle_id[order], traj.global_time_ms[order]
    xs, ys = x[order], y[order]
    linked = (vid[1:] == vid[:-1]) & (t[1:] - t[:-1] == FRAME_MS)
    dx, dy = np.diff(xs), np.diff(ys)
    step = np.hypot(dx, dy)
    speed = np.full(len(xs), np.nan)
    heading = np.full(len(xs), np.nan)
    speed[1:][linked] = step[linked] / (FRAME_MS / 1000)
    moving = linked & (step > 0)
    heading[1:][moving] = np.degrees(np.arctan2(dx[moving], dy[moving])) % 360
    heading = _fill_within_vehicle(heading, vid)
    out_speed, out_heading = np.empty_like(speed), np.empty_like(heading)
    out_speed[order], out_heading[order] = speed, heading
    return out_speed, out_heading


def _fill_within_vehicle(values: np.ndarray, vehicle_id: np.ndarray) -> np.ndarray:
    """Forward-fill NaNs from the last value of the same vehicle, then back-fill the leading NaNs
    from its first value. Rows must be sorted by vehicle (then time)."""

    def forward(v: np.ndarray, starts: np.ndarray) -> np.ndarray:
        idx = np.where(np.isfinite(v) | starts, np.arange(len(v)), 0)
        np.maximum.accumulate(idx, out=idx)
        return v[idx]

    starts = np.r_[True, vehicle_id[1:] != vehicle_id[:-1]]
    filled = forward(values, starts)
    ends = np.r_[vehicle_id[:-1] != vehicle_id[1:], True]
    return forward(filled[::-1], ends[::-1])[::-1]


def circular_mean_deg(angles_deg: np.ndarray) -> float:
    a = np.radians(np.asarray(angles_deg, dtype=np.float64))
    if a.size == 0:
        raise ValueError("no angles")
    value = float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean()))) % 360.0
    return 0.0 if math.isclose(value, 360.0) else value  # a tiny negative angle wraps to 360


def along_across(x_m, y_m, bearing_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Coordinates along a bearing and across it (positive to the right of the bearing)."""
    b = math.radians(bearing_deg)
    x, y = np.asarray(x_m), np.asarray(y_m)
    return x * math.sin(b) + y * math.cos(b), x * math.cos(b) - y * math.sin(b)


@dataclass(frozen=True)
class LaneStat:
    lane_id: int
    rows: int
    median_m: float
    q25_m: float
    q75_m: float


def lane_offsets(across_m: np.ndarray, lane_id: np.ndarray, min_rows: int = 30) -> list[LaneStat]:
    """Median lateral position per lane_id (sorted by position), ignoring lane 0 (no lane)."""
    stats = []
    for lane in np.unique(lane_id):
        values = across_m[lane_id == lane]
        if lane == 0 or len(values) < min_rows:
            continue
        q25, q50, q75 = np.percentile(values, [25, 50, 75])
        stats.append(LaneStat(int(lane), len(values), float(q50), float(q25), float(q75)))
    return sorted(stats, key=lambda s: s.median_m)


def boundaries(centres_m: Sequence[float]) -> list[float]:
    """Midpoints between adjacent lane centres (sorted), i.e. the implied lane lines."""
    c = sorted(centres_m)
    return [(a + b) / 2 for a, b in zip(c, c[1:], strict=False)]


@dataclass(frozen=True)
class QueueHead:
    lane_id: int
    samples: int
    median_m: float
    q10_m: float
    q90_m: float


def queue_heads(
    along_m: np.ndarray,
    lane_id: np.ndarray,
    time_ms: np.ndarray,
    speed_m_s: np.ndarray,
    stopped_m_s: float = 0.3,
    min_samples: int = 50,
) -> list[QueueHead]:
    """Where the front of the first stopped vehicle stands, per lane.

    `along_m` must increase towards the stop line. At every frame, the most advanced stopped
    vehicle of a lane is its queue head; the distribution of those positions over the period
    locates the stop position (front bumper) without any signal timing or video time.
    """
    stopped = np.isfinite(speed_m_s) & (speed_m_s < stopped_m_s) & (lane_id != 0)
    out = []
    for lane in np.unique(lane_id[stopped]):
        m = stopped & (lane_id == lane)
        t, a = time_ms[m], along_m[m]
        order = np.lexsort((-a, t))
        t, a = t[order], a[order]
        first = np.ones(len(t), bool)
        first[1:] = t[1:] != t[:-1]
        heads = a[first]
        if len(heads) < min_samples:
            continue
        q10, q50, q90 = np.percentile(heads, [10, 50, 90])
        out.append(QueueHead(int(lane), len(heads), float(q50), float(q10), float(q90)))
    return out


def footprint_occupancy(
    x_m: np.ndarray,
    y_m: np.ndarray,
    heading_deg: np.ndarray,
    length_m: np.ndarray,
    width_m: np.ndarray,
    centre_m: tuple[float, float],
    half_size_m: float,
    resolution_m: float,
) -> np.ndarray:
    """How often each ground cell lies under a vehicle body (rows run north to south).

    Each row's body is the rectangle behind its front centre along its heading, sampled on a
    7 x 5 grid; rows without a heading are skipped. Returned as samples per cell (not normalised).
    """
    size = int(round(2 * half_size_m / resolution_m))
    grid = np.zeros((size, size), np.float64)
    ok = np.isfinite(heading_deg)
    h = np.radians(heading_deg[ok])
    x, y, length, width = x_m[ok], y_m[ok], length_m[ok], width_m[ok]
    fx, fy = np.sin(h), np.cos(h)
    x0, y0 = centre_m[0] - half_size_m, centre_m[1] + half_size_m
    for a in np.linspace(0.05, 0.95, 7):
        for b in np.linspace(-0.4, 0.4, 5):
            px = x - a * length * fx + b * width * fy
            py = y - a * length * fy - b * width * fx
            col = np.floor((px - x0) / resolution_m).astype(np.int64)
            row = np.floor((y0 - py) / resolution_m).astype(np.int64)
            inside = (col >= 0) & (col < size) & (row >= 0) & (row < size)
            np.add.at(grid, (row[inside], col[inside]), 1.0)
    return grid
