"""Turn rules, and that the numbers from tracks are honest about what they are."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from cairoflow_contracts.analysis_record import GateOutcome, Metric
from cairoflow_perception.analysis_store import (
    NO_GROUND,
    NO_SPEED,
    POINT_INTERPOLATED,
    POINT_OUTSIDE_VALID_REGION,
    load_record,
)
from cairoflow_perception.local import analytics as AN
from cairoflow_perception.local import zones
from cairoflow_perception.local.tracker import FilledTrack

REPO = Path(__file__).resolve().parents[1]
REFERENCE = REPO / "data/sample/analyses/ref-ngsim-int2-960-1020"
DT_S = 0.1
M_PER_PX = 0.2


def arr(*values: float) -> np.ndarray:
    return np.asarray(values, float)


def test_the_rules_are_the_reference_scripts_own():
    rules = zones.reference_rules()
    assert zones.ARM_MIN_RADIUS_M == rules.ARM_MIN_RADIUS_M == 20.0
    assert zones.turn_of is rules.turn_of
    assert zones.vehicle_class is rules.vehicle_class


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ((0, 60), (0, -60), ("N", "S", "through")),
        ((0, 60), (60, 0), ("N", "E", "left")),
        ((0, 60), (-60, 0), ("N", "W", "right")),
        ((60, 0), (-60, 0), ("E", "W", "through")),
        ((60, 0), (0, -60), ("E", "S", "left")),
        ((0, -60), (60, 0), ("S", "E", "right")),
    ],
)
def test_a_movement_uses_first_and_last_arms(start, end, expected):
    movement = zones.classify(arr(start[0], end[0]), arr(start[1], end[1]))
    assert (movement.approach, movement.exit_arm, movement.turn) == expected
    assert movement.complete


def test_same_arm_means_the_window_cut_it_off_never_a_u_turn():
    movement = zones.classify(arr(2, 2, 2), arr(60, 40, 25))
    assert movement.approach == "N"
    assert movement.exit_arm is None
    assert movement.turn is None
    assert not movement.complete


def test_a_point_inside_the_radius_names_no_arm():
    starts_inside = zones.classify(arr(0, 0), arr(10, -60))
    assert starts_inside.approach is None
    assert starts_inside.exit_arm is None
    assert starts_inside.turn is None
    ends_inside = zones.classify(arr(0, 0), arr(60, 10))
    assert ends_inside.approach == "N"
    assert ends_inside.exit_arm is None


def test_the_radius_is_inclusive():
    assert zones.classify(arr(0.0), arr(zones.ARM_MIN_RADIUS_M)).approach == "N"
    assert zones.classify(arr(0.0), arr(zones.ARM_MIN_RADIUS_M - 0.01)).approach is None


def test_no_ground_means_no_movement():
    assert zones.classify(None, None) == zones.NO_MOVEMENT
    assert zones.classify(arr(), arr()) == zones.NO_MOVEMENT


def scaled_site():
    """A stand-in with a plain 0.2 m/px map centred on pixel (320, 240), north up."""

    def project(x, y):
        return np.stack(
            [(np.asarray(x, float) - 320) * M_PER_PX, (240 - np.asarray(y, float)) * M_PER_PX],
            axis=1,
        )

    return SimpleNamespace(project_to_junction=project)


def track(
    track_id=0,
    *,
    n=30,
    start_frame=0,
    x0=320.0,
    y0=20.0,
    vx=0.0,
    vy=14.0,
    labels=None,
    size=(10.0, 25.0),
    interpolated=(),
):
    x = x0 + vx * np.arange(n)
    y = y0 + vy * np.arange(n)
    flags = np.zeros(n, bool)
    flags[list(interpolated)] = True
    conf = np.where(flags, 0.0, 0.8)
    w, h = np.full(n, size[0]), np.full(n, size[1])
    return FilledTrack(track_id, start_frame, x, y, w, h, conf, flags, labels or {"vehicle": 5.0})


def place(t, site=None):
    return AN.place_track(t, site or scaled_site(), DT_S, valid_below_row_px=None)


def test_a_calibrated_track_gets_its_movement_and_speed():
    placed = place(track())  # 44 m in from north, 2.8 m per frame south
    assert (placed.movement.approach, placed.movement.exit_arm) == ("N", "S")
    assert placed.status == "complete"
    assert placed.speed_m_s[10] == pytest.approx(14 * M_PER_PX / DT_S)
    assert placed.heading_deg[10] == pytest.approx(180.0)


def test_speed_is_centred_and_a_one_point_track_has_none():
    u = np.arange(21) * 1.0
    speed, vu, vv = AN.centred_speed(u, np.zeros(21), DT_S)
    assert speed[10] == pytest.approx(10.0)
    assert speed[0] == pytest.approx(10.0)
    assert np.all(vv == 0)
    nothing, _, _ = AN.centred_speed(np.array([3.0]), np.array([4.0]), DT_S)
    assert np.isnan(nothing[0])


def test_a_short_track_is_a_fragment_and_left_out_of_counts():
    short = place(track(n=5))
    assert short.status == "fragment"
    metrics = {
        m.metric_id: m
        for m in AN.compute_metrics([short, place(track(1))], calibrated=True, seconds=60.0)
    }
    assert metrics["vehicles.total"].value == 1.0


def test_flags_mark_interpolated_rows_and_the_window_frame():
    t = track(n=30, y0=390, vy=2.0, interpolated=(3, 4))
    placed = AN.place_track(t, scaled_site(), DT_S, valid_below_row_px=410)
    assert placed.flags[3] & POINT_INTERPOLATED
    assert not placed.flags[0] & POINT_OUTSIDE_VALID_REGION
    assert placed.flags[10] & POINT_OUTSIDE_VALID_REGION  # y = 410: from this row down
    assert placed.usable.sum() == 30 - 2 - 20


def test_flagged_rows_do_not_decide_an_arm():
    # first rows are in the window frame on the south arm, the usable ones are inside the radius
    x = np.full(20, 320.0)
    y = np.concatenate([np.full(10, 420.0), np.linspace(240, 250, 10)])
    t = FilledTrack(
        0, 0, x, y, np.full(20, 10.0), np.full(20, 20.0), np.full(20, 0.9), np.zeros(20, bool),
        {"vehicle": 1.0},
    )  # fmt: skip
    placed = AN.place_track(t, scaled_site(), DT_S, valid_below_row_px=410)
    assert placed.movement.approach is None


def test_every_calibrated_value_is_approximated_and_ungated_with_a_reason():
    placed = [place(track(i)) for i in range(4)]
    metrics = AN.compute_metrics(placed, calibrated=True, seconds=60.0)
    series = AN.compute_series(placed, calibrated=True, seconds=60.0, fps=10.0)
    movements = AN.compute_movements(placed)
    for item in (*metrics, *series):
        assert item.evidence_kind == "approximated"
        assert item.status in ("ungated", "not_available")
        assert item.reason
    assert movements
    assert all(cell.status == "ungated" and cell.reason for cell in movements)
    assert all(cell.reference_count is None for cell in movements)  # no comparison was made


def test_speed_is_withheld_in_the_record_and_in_the_columns():
    """ADR-018 does not accept along-road distances, so no screen may be able to show a speed: a
    run keeps one only to choose the rows a size is read from, and stores none."""
    placed = [place(track(i)) for i in range(3)]
    metrics = {m.metric_id: m for m in AN.compute_metrics(placed, calibrated=True, seconds=60.0)}
    for metric_id in ("speed.mean_moving", "stopped.share"):
        assert metrics[metric_id].status == "not_available"
        assert metrics[metric_id].value is None
        assert "ADR-018" in metrics[metric_id].reason
    assert np.isfinite(placed[0].speed_m_s).all()
    columns = AN.columns_of(placed)
    assert len(columns) == 90
    assert np.all(columns.speed_cm_s == NO_SPEED)
    assert np.all(columns.gx != NO_GROUND)  # the rest of the row is still there


def mid_view(track_id=0, *, x0=320.0, y0=60.0, start_frame=50, n=30):
    """First seen well inside the image, after the window opened: a restart, not an arrival."""
    return track(track_id, n=n, start_frame=start_frame, x0=x0, y0=y0)


def test_a_track_first_seen_mid_view_keeps_its_arm_but_enters_nothing():
    placed = place(mid_view())
    assert placed.status == "complete"
    assert (placed.movement.approach, placed.movement.exit_arm) == ("N", "S")
    assert placed.movement.entering is None


@pytest.mark.parametrize(
    ("x0", "y0", "start_frame"),
    [
        (320.0, 20.0, 50),  # the top edge
        (20.0, 200.0, 50),  # the left edge
        (620.0, 200.0, 50),  # the right edge
        (320.0, 445.0, 50),  # the bottom edge
        (320.0, 60.0, 0),  # mid-view, but in view when the window opened
        (320.0, 60.0, 2),  # likewise, first seen on the third frame
    ],
)
def test_an_arrival_is_first_seen_at_an_edge_or_at_the_start(x0, y0, start_frame):
    t = track(n=30, start_frame=start_frame, x0=x0, y0=y0, vy=-14.0 if y0 > 240 else 14.0)
    placed = AN.place_track(t, scaled_site(), DT_S, valid_below_row_px=None)
    assert placed.movement.entered


def test_the_usable_image_ends_at_the_window_frame():
    """Rows from 410 down are not road, so a car coming up out of them arrives at row 410."""
    t = track(n=30, start_frame=50, y0=380.0, vy=-5.0)
    placed = AN.place_track(t, scaled_site(), DT_S, valid_below_row_px=410)
    assert placed.movement.entered
    assert not AN.place_track(t, scaled_site(), DT_S, valid_below_row_px=None).movement.entered


def test_a_flagged_first_row_does_not_decide_where_a_track_arrived():
    y = np.concatenate([np.full(5, 420.0), np.linspace(200, 100, 25)])
    t = FilledTrack(
        0, 50, np.full(30, 320.0), y, np.full(30, 10.0), np.full(30, 20.0), np.full(30, 0.9),
        np.zeros(30, bool), {"vehicle": 1.0},
    )  # fmt: skip
    placed = AN.place_track(t, scaled_site(), DT_S, valid_below_row_px=410)
    assert not placed.movement.entered  # row 200 is where it was first really seen


def test_at_a_stride_the_window_still_opens_over_two_frames():
    """Frame 0 of a dev block is withheld, so at stride 5 the first frame read is 0.5 s in. A car
    standing there was in view when the window opened."""
    t = track(n=30, start_frame=1, y0=60.0)
    assert AN.place_track(t, scaled_site(), 0.5, valid_below_row_px=None).movement.entered
    late = track(n=30, start_frame=3, y0=60.0)
    assert not AN.place_track(late, scaled_site(), 0.5, valid_below_row_px=None).movement.entered


def test_entering_counts_leave_out_a_mid_view_start_and_say_how_many():
    arrival, restart = place(track(0)), place(mid_view(1))
    placed = [arrival, restart]
    metrics = {m.metric_id: m for m in AN.compute_metrics(placed, calibrated=True, seconds=60.0)}
    assert metrics["vehicles.total"].value == 2.0  # it stays a vehicle seen
    assert metrics["vehicles.arm.n"].value == 1.0
    assert metrics["flow.entering"].value == 60.0  # one in 60 s, as veh/h
    series = {
        s.series_id: s for s in AN.compute_series(placed, calibrated=True, seconds=60.0, fps=10)
    }
    assert sum(series["entering.arm.n"].values) == 1.0
    assert AN.mid_view_starts(placed) == {"N": 1}
    assert sum(cell.count for cell in AN.compute_movements(placed)) == 2  # its turn was still seen


def test_a_fragment_is_not_a_mid_view_start():
    assert AN.mid_view_starts([place(mid_view(n=5))]) == {}


def test_metric_and_series_ids_match_the_reference():
    placed = [place(track(i, labels={"car": 1.0})) for i in range(3)]
    ours = {m.metric_id for m in AN.compute_metrics(placed, calibrated=True, seconds=60.0)}
    reference = load_record(REFERENCE)
    theirs = {m.metric_id for m in reference.metrics}
    fixed = {i for i in theirs if not i.startswith("vehicles.class.")}
    assert fixed <= ours
    assert {i for i in ours if not i.startswith("vehicles.class.")} == fixed
    series = AN.compute_series(placed, calibrated=True, seconds=60.0, fps=10.0)
    assert {s.series_id for s in series} == {s.series_id for s in reference.series}
    assert {s.bin_s for s in series} == {s.bin_s for s in reference.series}


def test_series_bins_follow_the_reference_rule():
    placed = [place(track(i, start_frame=605)) for i in range(2)]
    series = AN.compute_series(placed, calibrated=True, seconds=120.0, fps=10.0)
    north = next(s for s in series if s.series_id == "entering.arm.n")
    assert north.bin_s == 10.0
    assert north.values[6] == 2.0
    assert sum(north.values) == 2.0
    long = AN.compute_series(placed, calibrated=True, seconds=180.0, fps=10.0)
    assert {s.bin_s for s in long} == {60.0}


def test_uncalibrated_claims_no_ground_no_arms_no_flow_no_speed():
    t = track(labels={"car": 3.0})
    placed = [AN.place_track(t, None, DT_S, valid_below_row_px=None)]
    assert placed[0].ground_u_m is None
    assert placed[0].movement == zones.NO_MOVEMENT
    assert placed[0].length_m is None
    metrics = {m.metric_id: m for m in AN.compute_metrics(placed, calibrated=False, seconds=60.0)}
    assert metrics["vehicles.total"].value == 1.0
    assert metrics["vehicles.total"].status == "ungated"
    for metric_id in ("flow.entering", "speed.mean_moving", "stopped.share", "vehicles.arm.n"):
        assert metrics[metric_id].status == "not_available"
        assert metrics[metric_id].value is None
        assert "uncalibrated" in metrics[metric_id].reason
    series = AN.compute_series(placed, calibrated=False, seconds=60.0, fps=10.0)
    assert all(s.status == "not_available" and set(s.values) == {None} for s in series)
    assert AN.compute_movements(placed) == ()
    columns = AN.columns_of(placed)
    assert np.all(columns.gx == NO_GROUND)
    assert np.all(columns.gy == NO_GROUND)
    assert np.all(columns.speed_cm_s == NO_SPEED)


def test_no_tracks_is_an_honest_zero_not_an_invented_number():
    metrics = {m.metric_id: m for m in AN.compute_metrics([], calibrated=True, seconds=60.0)}
    assert metrics["vehicles.total"].value == 0.0
    assert metrics["flow.entering"].value == 0
    assert metrics["speed.mean_moving"].value is None
    assert AN.compute_movements([]) == ()
    assert len(AN.columns_of([])) == 0


def test_a_detector_class_beats_size_and_is_recorded():
    assert AN.track_class({"car": 8.0, "truck": 2.0}, 11.0) == ("car", "detector", 0.8)
    assert AN.track_class({"Motorbike": 3.0}, None) == ("motorcycle", "detector", 1.0)


def test_no_detector_class_falls_back_to_placed_size_then_to_nothing():
    assert AN.track_class({"vehicle": 5.0}, 4.5) == ("car", "size", None)
    assert AN.track_class({"vehicle": 5.0}, 9.0) == ("truck", "size", None)
    assert AN.track_class({"vehicle": 5.0}, None) == ("vehicle", "none", None)


def test_placed_size_reads_the_box_along_and_across_the_heading():
    placed = place(track(size=(10.0, 25.0)))
    assert placed.length_m == pytest.approx(25 * M_PER_PX)
    assert placed.width_m == pytest.approx(10 * M_PER_PX)
    assert placed.class_source == "size"


def test_a_stopped_track_claims_no_size():
    placed = place(track(vy=0.0, y0=20.0))
    assert placed.length_m is None
    assert placed.vehicle_class == "vehicle"
    assert placed.class_source == "none"


def test_density_on_the_ground_when_calibrated():
    placed = [place(track(i, n=20)) for i in range(2)]
    grid = AN.density_grid(placed, width_px=640, height_px=480)
    assert grid["frame"] == "local_enu_junction_m"
    assert grid["unit"] == "m"
    assert grid["cell_size"] == AN.GROUND_CELL_M
    assert grid["origin"] == [-AN.GROUND_HALF_M, -AN.GROUND_HALF_M]
    assert sum(map(sum, grid["counts"])) == 40
    assert grid["evidence_kind"] == "approximated"
    assert json.loads(AN.density_bytes(grid)) == grid


def test_density_on_the_image_when_uncalibrated():
    placed = [AN.place_track(track(n=20), None, DT_S, valid_below_row_px=None)]
    grid = AN.density_grid(placed, width_px=640, height_px=480)
    assert grid["frame"] == "image_px"
    assert (grid["nx"], grid["ny"]) == (40, 30)
    assert grid["origin"] == [0.0, 0.0]
    assert sum(map(sum, grid["counts"])) == 20


def test_density_skips_interpolated_rows_and_fragments():
    placed = [place(track(n=20, interpolated=(1, 2))), place(track(1, n=3))]
    assert sum(map(sum, AN.density_grid(placed, width_px=640, height_px=480)["counts"])) == 18


def test_density_counts_only_what_exists():
    grid = AN.density_grid([], width_px=64, height_px=32)
    assert sum(map(sum, grid["counts"])) == 0


def outcome(passed: bool, measured: float = 0.1) -> GateOutcome:
    return GateOutcome(
        gate_id="g1", gate_sha256="a" * 64, metric="entering_count_error_share", scope="site",
        split="held_out", measured=measured, comparator="<=", threshold=0.2, n=100, passed=passed,
    )  # fmt: skip


def test_a_passed_gate_marks_a_value_and_keeps_it_approximated():
    metric = Metric(
        metric_id="vehicles.arm.n", label="N", value=7.0, unit="veh", scope="arm:N",
        evidence_kind="approximated", status="ungated", reason="x",
    )  # fmt: skip
    mapping = {"vehicles.arm.n": "entering_count_error_share"}
    (passed,) = AN.apply_gate_outcomes([metric], [outcome(True)], mapping)
    assert (passed.status, passed.value, passed.gate_id) == ("gate_pass", 7.0, "g1")
    assert passed.evidence_kind == "approximated"


def test_a_failed_gate_removes_the_value():
    metric = Metric(
        metric_id="vehicles.arm.n", label="N", value=7.0, unit="veh", scope="arm:N",
        evidence_kind="approximated", status="ungated", reason="x",
    )  # fmt: skip
    mapping = {"vehicles.arm.n": "entering_count_error_share"}
    (failed,) = AN.apply_gate_outcomes([metric], [outcome(False, 0.5)], mapping)
    assert failed.status == "gate_fail"
    assert failed.value is None
    assert "0.5" in failed.reason


def test_a_gate_touches_only_what_it_covers():
    metric = Metric(
        metric_id="vehicles.total", label="T", value=7.0, unit="veh", scope="site",
        evidence_kind="approximated", status="ungated", reason="x",
    )  # fmt: skip
    assert AN.apply_gate_outcomes([metric], [outcome(True)], {}) == (metric,)
