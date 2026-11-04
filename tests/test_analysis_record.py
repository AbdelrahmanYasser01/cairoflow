"""The shared analysis record: honesty rules, the binary format and immutability."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from pydantic import ValidationError

from cairoflow_contracts.analysis_record import (
    AnalysisRecord,
    ArtifactRef,
    CalibrationState,
    Metric,
    MetricSeries,
    MovementCell,
    StageReceipt,
    TrackEntry,
    TrackIndex,
    VideoSource,
)
from cairoflow_contracts.evidence import Provenance
from cairoflow_perception.analysis_store import (
    HEADER,
    NO_GROUND,
    NO_SPEED,
    AnalysisStoreError,
    decode_trajectories,
    encode_trajectories,
    load_tracks,
    quantise,
    trajectory_checksum,
    verify_analysis,
    write_analysis,
)

NOW = datetime(2026, 11, 4, tzinfo=UTC)
SHA = "a" * 64


def metric(**overrides) -> Metric:
    fields = dict(
        metric_id="vehicles.total", label="Vehicles", value=10.0, unit="veh", scope="site",
        evidence_kind="observed", status="ungated", reason="no gate covers this",
    )  # fmt: skip
    return Metric(**{**fields, **overrides})


def record(**overrides) -> AnalysisRecord:
    fields = dict(
        analysis_id="a-1",
        title="t",
        mode="precomputed",
        data_origin="camera_cv",
        site_id="s",
        created_at_utc=NOW,
        source=VideoSource(
            source_id="v", title="t", site_name="s", licence_name="CC BY-SA 4.0",
            attribution="a", source_file="f.avi",
        ),
        fps=10.0,
        frame_count=3,
        width_px=640,
        height_px=480,
        source_start_s=0.0,
        window_offset_s=120.0,
        calibration=CalibrationState(mode="uncalibrated"),
        stages=(StageReceipt(stage="detect", tool="d", version="1"),),
        metrics=(metric(),),
        artifacts=(
            ArtifactRef(role="trajectories", path="trajectories.bin", media_type="x/y"),
            ArtifactRef(role="tracks", path="tracks.json", media_type="x/y"),
        ),
        disclosures=("d",),
        provenance=Provenance(kind="observed", produced_by="test", produced_at_utc=NOW),
    )  # fmt: skip
    return AnalysisRecord(**{**fields, **overrides})


def track(track_id: int = 0, first_row: int = 0, n: int = 3) -> TrackEntry:
    return TrackEntry(
        track_id=track_id, first_row=first_row, start_frame=0, n=n, vehicle_class="car",
        class_source="detector", status="complete",
    )  # fmt: skip


def columns(n: int = 3):
    i = np.arange(n, dtype=float)
    return quantise(
        x_px=10 + i, y_px=20 + i, w_px=np.full(n, 8.0), h_px=np.full(n, 16.0),
        ground_x_m=i, ground_y_m=-i, speed_m_s=i, heading_deg=i * 90,
        confidence=np.full(n, 0.5),
    )  # fmt: skip


@pytest.mark.parametrize("status", ["gate_fail", "not_available"])
def test_a_withheld_metric_cannot_carry_a_value(status: str) -> None:
    with pytest.raises(ValidationError, match="cannot carry a value"):
        metric(status=status, gate_id="g")
    assert metric(status=status, value=None, gate_id="g").value is None


def test_a_metric_needs_a_reason_unless_it_passed_a_gate() -> None:
    with pytest.raises(ValidationError, match="needs a reason"):
        metric(reason=None)
    assert metric(status="gate_pass", gate_id="g", reason=None).reason is None


def test_a_gate_status_must_name_its_gate() -> None:
    with pytest.raises(ValidationError, match="gate_id"):
        metric(status="gate_pass", reason=None)


def test_a_failed_movement_cell_has_no_count() -> None:
    with pytest.raises(ValidationError, match="cannot carry a count"):
        MovementCell(approach="N", exit_arm="S", turn="through", count=4, status="gate_fail",
                     reason="r", gate_id="g")  # fmt: skip


def test_a_reference_analysis_is_never_live() -> None:
    with pytest.raises(ValidationError, match="never run live"):
        record(mode="live", data_origin="reference_dataset")


def test_metric_ids_are_unique_within_an_analysis() -> None:
    with pytest.raises(ValidationError, match="unique"):
        record(metrics=(metric(), metric()))


def test_series_ids_are_unique_within_an_analysis() -> None:
    series = MetricSeries(
        series_id="entering.arm.n", label="N", unit="veh", scope="arm:N", bin_s=60.0, start_s=0.0,
        values=(1.0,), evidence_kind="observed", status="ungated", reason="r",
    )  # fmt: skip
    with pytest.raises(ValidationError, match="series ids"):
        record(series=(series, series))


def test_calibrated_needs_a_homography() -> None:
    with pytest.raises(ValidationError, match="homography"):
        CalibrationState(mode="calibrated")


def test_tracks_must_be_contiguous() -> None:
    with pytest.raises(ValidationError, match="contiguous"):
        TrackIndex(analysis_id="a", trajectories_sha256=SHA, tracks=(track(first_row=1),))


def test_trajectories_round_trip_exactly() -> None:
    original = columns(5)
    data = encode_trajectories(original, n_tracks=1, n_frames=5, fps=10.0)
    header, decoded = decode_trajectories(data)
    assert header["n_points"] == 5 and header["fps"] == 10.0
    for name in ("x", "y", "w", "h", "gx", "gy", "speed_cm_s", "heading", "conf", "point_flags"):
        assert np.array_equal(getattr(decoded, name), getattr(original, name))
    assert trajectory_checksum(decoded) == trajectory_checksum(original)


def test_each_column_starts_on_a_four_byte_boundary() -> None:
    data = encode_trajectories(columns(3), n_tracks=1, n_frames=3, fps=10.0)
    assert (len(data) - HEADER.size) % 4 == 0
    assert decode_trajectories(data)[1].x[0] == 160  # 10 px at 1/16 px per step


def test_units_survive_quantising() -> None:
    stored = columns(3)
    assert stored.gx[1] == 50  # 1 m at 0.02 m per step
    assert stored.gy[1] == -50
    assert stored.speed_cm_s[2] == 200
    assert stored.heading[1] == 64  # 90 degrees is a quarter of 256


def test_missing_values_have_their_own_sentinels() -> None:
    stored = quantise(
        x_px=np.zeros(2), y_px=np.zeros(2), w_px=np.ones(2), h_px=np.ones(2),
        ground_x_m=np.array([np.nan, 1.0]), speed_m_s=np.array([np.nan, 0.0]),
    )  # fmt: skip
    assert stored.gx[0] == NO_GROUND and stored.gy[0] == NO_GROUND
    assert stored.speed_cm_s[0] == NO_SPEED and stored.speed_cm_s[1] == 0


def test_a_truncated_file_is_refused() -> None:
    data = encode_trajectories(columns(5), n_tracks=1, n_frames=5, fps=10.0)
    with pytest.raises(AnalysisStoreError, match="truncated"):
        decode_trajectories(data[:40])
    with pytest.raises(AnalysisStoreError, match="magic"):
        decode_trajectories(b"XXXX" + data[4:])


def test_write_then_verify_then_refuse_to_overwrite(tmp_path) -> None:
    directory = tmp_path / "a-1"
    final = write_analysis(directory, record(), [track()], columns())
    assert {a.role for a in final.artifacts} == {"trajectories", "tracks"}
    assert all(a.sha256 for a in final.artifacts)
    assert verify_analysis(directory).analysis_id == "a-1"
    assert load_tracks(directory).tracks[0].n == 3
    with pytest.raises(AnalysisStoreError, match="immutable"):
        write_analysis(directory, record(), [track()], columns())


def test_verify_notices_a_changed_artifact(tmp_path) -> None:
    directory = tmp_path / "a-1"
    write_analysis(directory, record(), [track()], columns())
    (directory / "trajectories.bin").write_bytes(b"CFT1 changed")
    with pytest.raises(AnalysisStoreError, match="has changed"):
        verify_analysis(directory)


def test_rows_must_match_the_track_index(tmp_path) -> None:
    with pytest.raises(AnalysisStoreError, match="rows"):
        write_analysis(tmp_path / "x", record(), [track(n=9)], columns())


def test_an_unbundled_artifact_is_listed_but_not_required(tmp_path) -> None:
    video = ArtifactRef(
        role="annotated_video", path="annotated.mp4", media_type="video/mp4", bundled=False,
        rebuild_hint="fetch the source video, then render it",
    )  # fmt: skip
    directory = tmp_path / "a-1"
    write_analysis(directory, record(), [track()], columns(), [(video, None)])
    verified = verify_analysis(directory)
    assert any(a.role == "annotated_video" and not a.bundled for a in verified.artifacts)
    assert not (directory / "annotated.mp4").exists()
