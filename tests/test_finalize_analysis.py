"""Finalizing an analysis: what a gate result is allowed to change."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from cairoflow_contracts.analysis_record import (
    AnalysisRecord,
    ArtifactRef,
    CalibrationState,
    DecisionRef,
    Metric,
    MetricSeries,
    MovementCell,
    StageReceipt,
    TrackEntry,
    VideoSource,
)
from cairoflow_contracts.evidence import Provenance
from cairoflow_perception.analysis_store import (
    NO_SPEED,
    decode_trajectories,
    load_record,
    load_tracks,
    quantise,
    verify_analysis,
    write_analysis,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("finalize", ROOT / "scripts/finalize_analysis.py")
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)

NOW = datetime(2026, 11, 4, tzinfo=UTC)
SHA = "b" * 64
UNGATED = "no registered gate has checked this value"
WITHHELD = "withheld by ADR-018"


def metric(metric_id: str, value: float | None, unit: str = "veh") -> Metric:
    return Metric(
        metric_id=metric_id, label=metric_id, value=value, unit=unit, scope="site",
        evidence_kind="approximated", status="ungated" if value is not None else "not_available",
        reason=UNGATED if value is not None else WITHHELD,
    )  # fmt: skip


def record(data_origin: str = "camera_cv") -> AnalysisRecord:
    return AnalysisRecord(
        analysis_id="run",
        title="Run",
        mode="precomputed",
        data_origin=data_origin,
        site_id="s",
        created_at_utc=NOW,
        source=VideoSource(
            source_id="v", title="t", site_name="s", licence_name="CC BY-SA 4.0",
            attribution="a", source_file="f.avi",
        ),
        fps=10.0,
        frame_count=40,
        width_px=640,
        height_px=480,
        source_start_s=120.0,
        window_offset_s=120.0,
        calibration=CalibrationState(
            mode="calibrated", ground_frame="local_enu_junction_m",
            image_to_ground=((1, 0, 0), (0, 1, 0), (0, 0, 1)), valid_windows_s=((970.0, 1000.0),),
        ),
        stages=(StageReceipt(stage="detect", tool="d", version="1", licence="AGPL-3.0"),),
        metrics=(
            metric("vehicles.total", 10.0),
            metric("flow.entering", 600.0, "veh/h"),
            metric("speed.mean_moving", None, "m/s"),
            metric("stopped.share", None, "share"),
            metric("vehicles.arm.n", 6.0),
            metric("vehicles.class.car", 9.0),
        ),
        series=(
            MetricSeries(
                series_id="entering.arm.n", label="N", unit="veh", scope="arm:N", bin_s=60.0,
                start_s=0.0, values=(1.0, 2.0), evidence_kind="approximated", status="ungated",
                reason=UNGATED,
            ),
        ),
        movements=(
            MovementCell(approach="N", exit_arm="S", turn="through", count=4, status="ungated",
                         reason=UNGATED),
        ),
        artifacts=(
            ArtifactRef(role="trajectories", path="trajectories.bin", media_type="x/y"),
            ArtifactRef(role="tracks", path="tracks.json", media_type="x/y"),
        ),
        disclosures=(
            "Every value is ungated: no registered gate has compared it.",
            "Ground positions are approximated through a camera homography; speed is withheld.",
            "Part of this run is outside the validated window: 30 s of 60 s.",
            "Footage is from Los Angeles in 2005, not Cairo.",
        ),
        provenance=Provenance(kind="approximated", produced_by="test", produced_at_utc=NOW),
    )  # fmt: skip


def write_run(tmp_path: Path, data_origin: str = "camera_cv") -> Path:
    n = 40
    i = np.arange(n, dtype=float)
    columns = quantise(
        x_px=10 + i, y_px=20 + i, w_px=np.full(n, 8.0), h_px=np.full(n, 16.0),
        ground_x_m=i * 0.5, ground_y_m=np.zeros(n), confidence=np.full(n, 0.5),
    )  # fmt: skip
    track = TrackEntry(
        track_id=0, first_row=0, start_frame=0, n=n, vehicle_class="car", class_source="detector",
        status="complete",
    )  # fmt: skip
    directory = tmp_path / "run"
    write_analysis(directory, record(data_origin), [track], columns)
    return directory


def write_reference(tmp_path: Path) -> Path:
    directory = tmp_path / "reference"
    reference = record().model_copy(
        update={
            "analysis_id": "reference",
            "movements": (
                MovementCell(approach="N", exit_arm="S", turn="through", count=7, status="ungated",
                             reason=UNGATED),
            ),
        }
    )  # fmt: skip
    i = np.arange(2, dtype=float)
    track = TrackEntry(
        track_id=0, first_row=0, start_frame=0, n=2, vehicle_class="car", class_source="size",
        status="complete",
    )  # fmt: skip
    columns = quantise(x_px=i, y_px=i, w_px=i + 1, h_px=i + 1)
    write_analysis(directory, reference, [track], columns)
    return directory


def result(failing: tuple[str, ...] = (), split: str = "held_out", analysis_id: str = "run"):
    names = (
        ("vehicle_recall", 0.8, ">=", 0.7),
        ("precision_proxy", 0.9, ">=", 0.8),
        ("across_heading_p90_m", 0.8, "<=", 1.5),
        ("lane_agreement", 0.9, ">=", 0.85),
        ("entering_count_error_share", 0.1, "<=", 0.2),
        ("turning_matrix_agreement", 0.9, ">=", 0.8),
        ("speed_abs_error_median_m_s", 0.5, "<=", 1.0),
    )
    return {
        "analysis_id": analysis_id,
        "split": split,
        "blocks": [1, 3],
        "outcomes": [
            {
                "gate_id": "cv-int2-v2", "gate_sha256": SHA, "metric": name, "scope": "site",
                "split": split, "measured": measured, "comparator": comparator,
                "threshold": threshold, "n": 5000, "passed": name not in failing,
            }
            for name, measured, comparator, threshold in names
        ],
    }  # fmt: skip


def run(tmp_path: Path, payload: dict, data_origin: str = "camera_cv") -> AnalysisRecord:
    return F.finalize(
        write_run(tmp_path, data_origin), payload, SHA, "final", tmp_path / "out",
        write_reference(tmp_path),
    )  # fmt: skip


def by_id(final: AnalysisRecord) -> dict[str, Metric]:
    return {m.metric_id: m for m in final.metrics}


def test_a_passed_check_marks_the_value_and_keeps_it(tmp_path) -> None:
    metrics = by_id(run(tmp_path, result()))
    assert metrics["vehicles.arm.n"].status == "gate_pass"
    assert metrics["vehicles.arm.n"].value == 6.0
    assert metrics["vehicles.arm.n"].gate_id == "cv-int2-v2"


def test_no_gate_is_claimed_for_the_vehicle_total(tmp_path) -> None:
    total = by_id(run(tmp_path, result()))["vehicles.total"]
    assert total.status == "ungated" and total.value == 10.0
    assert "restarted mid-view" in total.reason
    assert total.label == "Counted tracks" and total.unit == "tracks"


def test_a_failed_check_removes_the_value_and_says_why(tmp_path) -> None:
    final = run(tmp_path, result(failing=("entering_count_error_share",)))
    flow = by_id(final)["flow.entering"]
    assert flow.status == "gate_fail" and flow.value is None
    assert "cv-int2-v2" in flow.reason and "entering_count_error_share" in flow.reason
    assert final.series[0].status == "gate_fail"
    assert all(value is None for value in final.series[0].values)
    assert by_id(final)["speed.mean_moving"].status == "gate_pass"
    text = " ".join(final.disclosures)
    assert "entering_count_error_share failed: 0.1 against a limit of <= 0.2" in text
    assert "was not moved" in text


def test_a_failed_turning_check_removes_counts_but_keeps_the_reference(tmp_path) -> None:
    cell = run(tmp_path, result(failing=("turning_matrix_agreement",))).movements[0]
    assert cell.status == "gate_fail" and cell.count is None
    assert cell.reference_count == 7


def test_speed_is_computed_and_stored_only_when_its_check_passes(tmp_path) -> None:
    final = run(tmp_path, result())
    metrics = by_id(final)
    assert metrics["speed.mean_moving"].status == "gate_pass"
    assert metrics["speed.mean_moving"].value == pytest.approx(5.0)  # 0.5 m per 0.1 s
    _, stored = decode_trajectories((tmp_path / "out/final/trajectories.bin").read_bytes())
    assert stored.speed_cm_s[20] == 500


def test_stopped_share_is_released_with_speed_but_no_gate_is_claimed(tmp_path) -> None:
    stopped = by_id(run(tmp_path, result()))["stopped.share"]
    assert stopped.value == 0.0 and stopped.status == "ungated" and stopped.gate_id is None
    assert "no gate covers stopped time" in stopped.reason and "2 m/s" in stopped.reason


def test_a_failed_speed_check_stores_no_speed(tmp_path) -> None:
    final = run(tmp_path, result(failing=("speed_abs_error_median_m_s",)))
    metric_ = by_id(final)["speed.mean_moving"]
    assert metric_.status == "gate_fail" and metric_.value is None
    stopped = by_id(final)["stopped.share"]
    assert stopped.status == "not_available" and stopped.value is None
    _, stored = decode_trajectories((tmp_path / "out/final/trajectories.bin").read_bytes())
    assert np.all(stored.speed_cm_s == NO_SPEED)


def test_vehicle_classes_stay_ungated_with_the_reason_no_gate_covers_them(tmp_path) -> None:
    car = by_id(run(tmp_path, result()))["vehicles.class.car"]
    assert car.status == "ungated" and car.value == 9.0
    assert "v_class" in car.reason and "weren't compared" in car.reason
    assert "no class column" not in car.reason


def test_evidence_stays_approximated_whatever_the_gate_says(tmp_path) -> None:
    final = run(tmp_path, result())
    assert all(m.evidence_kind == "approximated" for m in final.metrics)


def test_only_a_held_out_result_counts(tmp_path) -> None:
    with pytest.raises(F.FinalizeError, match="held-out"):
        run(tmp_path, result(split="dev"))


def test_a_result_for_another_analysis_is_refused(tmp_path) -> None:
    with pytest.raises(F.FinalizeError, match="other"):
        run(tmp_path, result(analysis_id="other"))


def test_reference_data_is_refused(tmp_path) -> None:
    with pytest.raises(F.FinalizeError, match="not camera output"):
        run(tmp_path, result(), data_origin="reference_dataset")


def test_disclosures_say_what_the_gate_decided(tmp_path) -> None:
    text = " ".join(run(tmp_path, result()).disclosures)
    assert "7 of 7 checks passed" in text and "and not accuracy" in text
    assert "placement was not held out" in text and "not independent of that fit" in text
    assert "points Claude read from video frames" in text and "hand-read" not in text
    assert "Every value is ungated" not in text
    assert "calibrated speed measurement" in text and "constant offset" not in text
    assert "2 m/s or more" in text
    assert "pooled over the held-out blocks" in text
    assert "AGPL-3.0" in text and "not Cairo" in text


def test_a_failed_turning_check_says_nothing_about_a_pooled_pass(tmp_path) -> None:
    final = run(tmp_path, result(failing=("turning_matrix_agreement",)))
    assert "pooled over the held-out blocks" not in " ".join(final.disclosures)


def test_the_inherited_decision_says_no_baseline_is_validated(tmp_path) -> None:
    decision = DecisionRef(
        run_id="r", outcome="none_selected", summary="the baseline is only partially validated",
        linked_to_cv=False,
    )  # fmt: skip
    source, reference = write_run(tmp_path), write_reference(tmp_path)
    final = F.finalize(source, result(), SHA, "final", tmp_path / "out", reference, decision)
    assert final.decision_ref == decision  # one passed in is taken as given
    stored = load_record(reference).model_copy(update={"decision_ref": decision})
    write_analysis(
        tmp_path / "with-decision", stored, load_tracks(reference).tracks,
        decode_trajectories((reference / "trajectories.bin").read_bytes())[1],
    )  # fmt: skip
    inherited = F.finalize(
        source, result(), SHA, "again", tmp_path / "out", tmp_path / "with-decision"
    ).decision_ref
    assert inherited.run_id == "r" and "no baseline is validated" in inherited.summary


def test_a_failed_placement_keeps_the_window_warning(tmp_path) -> None:
    final = run(tmp_path, result(failing=("lane_agreement",)))
    assert any(d.startswith("Part of this run is outside") for d in final.disclosures)
    assert final.calibration.valid_windows_s == ((970.0, 1000.0),)


def test_passed_placement_records_the_windows_it_measured(tmp_path) -> None:
    windows = run(tmp_path, result()).calibration.valid_windows_s
    assert (180.0, 240.0) in windows and (300.0, 360.0) in windows and (970.0, 1000.0) in windows


def test_the_new_analysis_verifies_and_keeps_every_track(tmp_path) -> None:
    final = run(tmp_path, result())
    assert verify_analysis(tmp_path / "out/final").analysis_id == "final"
    assert len(load_tracks(tmp_path / "out/final").tracks) == 1
    assert final.stages[-1].stage == "gate" and len(final.gates) == 7
    assert final.provenance.input_sha256[1] == SHA


def test_the_source_analysis_is_left_alone(tmp_path) -> None:
    run(tmp_path, result())
    untouched = verify_analysis(tmp_path / "run")
    assert untouched.gates == () and untouched.analysis_id == "run"
