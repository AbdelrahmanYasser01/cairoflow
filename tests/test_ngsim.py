"""NGSIM trajectory helpers on synthetic rows and on the published projection's defining points."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from cairoflow_perception.ngsim import (
    CA_ZONE3_FTUS,
    CA_ZONE5_FTUS,
    COLUMNS,
    US_SURVEY_FOOT_M,
    LocalFrame,
    Trajectories,
    along_across,
    boundaries,
    circular_mean_deg,
    footprint_occupancy,
    kinematics,
    lane_offsets,
    load_trajectories,
    queue_heads,
)


def test_zone5_false_origin_maps_to_the_latitude_and_meridian_of_origin() -> None:
    lat, lon = CA_ZONE5_FTUS.to_geographic(6561666.667, 1640416.667)
    assert float(lat) == pytest.approx(33.5, abs=1e-9)
    assert float(lon) == pytest.approx(-118.0, abs=1e-9)


def test_projection_round_trip_and_defining_properties() -> None:
    lat = np.linspace(34.10, 34.18, 9)
    lon = np.linspace(-118.40, -118.30, 9)
    x, y = CA_ZONE5_FTUS.from_geographic(lat, lon)
    back_lat, back_lon = CA_ZONE5_FTUS.to_geographic(x, y)
    assert np.allclose(back_lat, lat, atol=1e-10) and np.allclose(back_lon, lon, atol=1e-10)
    for parallel in (35.4666666666667, 34.0333333333333):  # the cone touches the ellipsoid there
        assert CA_ZONE5_FTUS.scale_factor(parallel) == pytest.approx(1.0, abs=1e-12)
    assert CA_ZONE5_FTUS.convergence_deg(-118.0) == 0.0
    assert abs(CA_ZONE5_FTUS.convergence_deg(-118.362)) < 0.25


def test_lankershim_coordinates_fit_zone5_and_not_zone3() -> None:
    # a point inside the metadata bounding box, projected and read back in each zone
    x, y = CA_ZONE5_FTUS.from_geographic(34.139, -118.362)
    assert 6.45e6 < float(x) < 6.46e6 and 1.87e6 < float(y) < 1.88e6  # the published magnitudes
    lat3, lon3 = CA_ZONE3_FTUS.to_geographic(x, y)
    assert not (34.137 <= float(lat3) <= 34.143 and -118.363 <= float(lon3) <= -118.360)


def _write_csv(path: Path, rows: list[list[str]], header: tuple[str, ...] = COLUMNS) -> Path:
    lines = [",".join(f'"{h}"' for h in header)]
    lines += [",".join(f'"{v}"' for v in row) for row in rows]
    path.write_text("\n".join(lines) + "\n", "utf-8")
    return path


ROW = [
    "7",
    "1201",
    "1118935800000",
    "6452000.5",
    "1873000.25",
    "15.0",
    "6.0",
    "2",
    "2",
    "0",
    "2",
    "1",
]


def test_load_reads_types_and_refuses_bad_files(tmp_path: Path) -> None:
    traj = load_trajectories(_write_csv(tmp_path / "ok.csv", [ROW, ROW]))
    assert len(traj) == 2
    assert traj.global_time_ms.dtype == np.int64 and traj.x_ft.dtype == np.float64
    assert traj.x_ft[0] == 6452000.5 and traj.direction[0] == 2
    with pytest.raises(ValueError, match="unexpected header"):
        load_trajectories(_write_csv(tmp_path / "h.csv", [ROW], header=COLUMNS[::-1]))
    with pytest.raises(ValueError, match="no data rows"):
        load_trajectories(_write_csv(tmp_path / "e.csv", []))
    bad = list(ROW)
    bad[3] = "nan"
    with pytest.raises(ValueError, match="non-finite"):
        load_trajectories(_write_csv(tmp_path / "n.csv", [bad]))


def _traj(rows: list[tuple[int, int, float, float]]) -> Trajectories:
    """rows: (vehicle_id, time_ms, x_m, y_m) in a frame whose origin is (0, 0) ft."""
    n = len(rows)
    vid, t, x, y = (np.array(c) for c in zip(*rows, strict=True))
    ones = np.ones(n, np.int64)
    return Trajectories(
        vehicle_id=vid.astype(np.int64), frame_id=(t // 100).astype(np.int64),
        global_time_ms=t.astype(np.int64), x_ft=x / US_SURVEY_FOOT_M, y_ft=y / US_SURVEY_FOOT_M,
        length_ft=np.full(n, 15.0), width_ft=np.full(n, 6.0), lane_id=ones, int_id=ones,
        section_id=ones * 0, direction=ones * 2, movement=ones,
    )  # fmt: skip


def test_kinematics_links_only_consecutive_frames_and_keeps_heading_while_stopped() -> None:
    rows = [
        (1, 0, 0.0, 0.0), (1, 100, 0.0, 1.0), (1, 200, 0.0, 2.0),  # north at 10 m/s
        (1, 400, 0.0, 3.0),  # a 200 ms gap: no speed
        (2, 0, 0.0, 0.0), (2, 100, 0.5, 0.0), (2, 200, 0.5, 0.0), (2, 300, 0.5, 0.0),  # east, stops
    ]  # fmt: skip
    speed, heading = kinematics(_traj(rows), LocalFrame(0.0, 0.0))
    assert (
        np.isnan(speed[0]) and speed[1] == pytest.approx(10.0) and speed[2] == pytest.approx(10.0)
    )
    assert np.isnan(speed[3])
    assert heading[0] == pytest.approx(0.0)  # back-filled from the first move
    assert (
        speed[6] == 0.0 and heading[6] == pytest.approx(90.0) and heading[7] == pytest.approx(90.0)
    )


def test_frames_bearings_and_lane_statistics() -> None:
    frame = LocalFrame(1000.0, 2000.0)
    x, y = frame.to_local(1000.0 + 3937 / 1200, 2000.0)
    assert float(x) == pytest.approx(1.0) and float(y) == 0.0
    assert frame.to_state_plane(x, y)[0] == pytest.approx(1000.0 + 3937 / 1200)
    along, across = along_across(np.array([1.0, 0.0]), np.array([0.0, -1.0]), 90.0)
    assert along[0] == pytest.approx(1.0) and across[1] == pytest.approx(
        1.0
    )  # south is right of east
    assert circular_mean_deg(np.array([350.0, 10.0])) == pytest.approx(0.0, abs=1e-9)
    stats = lane_offsets(
        np.array([3.0] * 40 + [0.0] * 40 + [9.0] * 5), np.array([2] * 40 + [0] * 40 + [3] * 5)
    )
    assert [s.lane_id for s in stats] == [2] and stats[0].median_m == 3.0
    assert boundaries([3.0, -1.0, 6.5]) == [1.0, 4.75]


def test_queue_heads_take_the_most_advanced_stopped_vehicle_per_frame() -> None:
    t = np.repeat(np.arange(60) * 100, 3)
    along = np.tile([-2.0, -9.0, 5.0], 60)  # head, follower, a vehicle already through (moving)
    speed = np.tile([0.0, 0.1, 8.0], 60)
    lane = np.ones(180, np.int64)
    heads = queue_heads(along, lane, t, speed, stopped_m_s=0.3, min_samples=50)
    assert len(heads) == 1 and heads[0].median_m == -2.0 and heads[0].samples == 60
    assert queue_heads(along, lane, t, speed, min_samples=61) == []


def test_footprint_lies_behind_the_front_centre() -> None:
    grid = footprint_occupancy(np.array([0.0]), np.array([0.0]), np.array([0.0]), np.array([4.0]),
                               np.array([2.0]), (0.0, 0.0), 5.0, 0.5)  # fmt: skip
    rows, cols = np.nonzero(grid)
    centre = grid.shape[0] // 2
    assert rows.min() >= centre and rows.max() <= centre + 8  # south of the front, up to 4 m
    assert abs(cols.mean() - centre) < 1.0 and math.isclose(grid.sum(), 35.0)
