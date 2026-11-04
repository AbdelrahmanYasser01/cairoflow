"""Agreement measures on small hand-made cases, and the rule that guards the held-out blocks.

The analysis below is built in a temporary directory from numbers written in this file; it is test
data for the comparison code and never reaches a repository analysis directory. No test here reads
the published trajectories, and none reads an odd block of anything.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from cairoflow_contracts.analysis_record import (
    AnalysisRecord,
    ArtifactRef,
    CalibrationState,
    Metric,
    TrackEntry,
    VideoSource,
)
from cairoflow_contracts.evidence import Provenance
from cairoflow_evaluation import cv_agreement as A
from cairoflow_perception.analysis_store import quantise, write_analysis
from cairoflow_perception.camera_gate_v3 import HeadedReference
from cairoflow_perception.local import scoring as S
from cairoflow_perception.local.frames import HeldOutGrant, check_grant, check_scored
from cairoflow_perception.local.scoring import HeldOutError

REPO = Path(__file__).resolve().parents[1]
GATE_SCRIPT = REPO / "scripts/cv_gate.py"
SHA = "a" * 64


def gate_payload(gate_id: str = "cv-test-v1", **metrics) -> dict:
    return {
        "gate_id": gate_id,
        "created_at": "2026-11-04T12:00:00Z",
        "blocks": {"formula": A.BLOCK_FORMULA, "dev": "even", "held_out": "odd"},
        "metrics": metrics
        or {"vehicle_recall": {"comparator": ">=", "threshold": 0.5, "min_n": 10}},
    }


def gate(**metrics) -> A.GateFile:
    return A.GateFile.model_validate(gate_payload(**metrics))


# ---- the gate file ---------------------------------------------------------------------------


def test_a_gate_file_parses():
    parsed = gate()
    assert parsed.gate_id == "cv-test-v1"
    assert parsed.metrics["vehicle_recall"].min_n == 10


def test_a_gate_file_names_only_known_metrics():
    with pytest.raises(ValidationError):
        gate(accuracy={"comparator": ">=", "threshold": 0.5, "min_n": 1})


def test_a_gate_file_cannot_change_the_block_rule():
    payload = gate_payload()
    payload["blocks"]["held_out"] = "even"
    with pytest.raises(ValidationError):
        A.GateFile.model_validate(payload)
    payload = gate_payload()
    payload["blocks"]["formula"] = "floor(source_s / 60)"
    with pytest.raises(ValidationError):
        A.GateFile.model_validate(payload)


def test_a_gate_file_needs_a_check():
    payload = gate_payload()
    payload["metrics"] = {}
    with pytest.raises(ValidationError):
        A.GateFile.model_validate(payload)


def test_a_gate_file_refuses_a_comparator_it_cannot_apply():
    with pytest.raises(ValidationError):
        gate(vehicle_recall={"comparator": "==", "threshold": 0.5, "min_n": 1})


def test_loading_hashes_the_exact_bytes(tmp_path):
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(gate_payload()), "utf-8")
    _, first = A.load_gate(path)
    path.write_text(json.dumps(gate_payload()) + "\n", "utf-8")
    _, second = A.load_gate(path)
    assert first != second


@pytest.mark.parametrize(
    ("value", "n", "comparator", "passed", "note"),
    [
        (0.8, 100, ">=", True, None),
        (0.3, 100, ">=", False, None),
        (0.3, 100, "<=", True, None),
        (0.5, 100, ">=", True, None),  # the threshold itself meets it
        (0.8, 5, ">=", False, "below the required"),
        (None, 100, ">=", False, "could not be computed"),
    ],
)
def test_a_check_needs_a_value_enough_n_and_the_comparator(value, n, comparator, passed, note):
    parsed = gate(vehicle_recall={"comparator": comparator, "threshold": 0.5, "min_n": 10})
    (outcome,) = A.evaluate_gate(parsed, SHA, {"vehicle_recall": {"value": value, "n": n}}, "dev")
    assert outcome.passed is passed
    assert (outcome.note is None) == (note is None)
    if note:
        assert note in outcome.note
    assert (outcome.split, outcome.n, outcome.measured) == ("dev", n, value)


def test_a_metric_that_was_not_measured_fails():
    (outcome,) = A.evaluate_gate(gate(), SHA, {}, "dev")
    assert not outcome.passed


# ---- the held-out rule -----------------------------------------------------------------------


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True,
    )  # fmt: skip


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "data/gates").mkdir(parents=True)
    git(root, "init", "-q")
    (root / "README").write_text("x", "utf-8")
    git(root, "add", "README")
    git(root, "commit", "-qm", "start")
    return root


def write_gate(repo: Path, name: str = "g.json", gate_id: str = "cv-test-v1") -> Path:
    path = repo / "data/gates" / name
    path.write_text(json.dumps(gate_payload(gate_id)), "utf-8")
    return path


def test_an_untracked_gate_is_refused(repo):
    path = write_gate(repo)
    with pytest.raises(A.GateRefused, match="not committed"):
        A.require_committed(repo, path)


def test_a_staged_gate_is_refused(repo):
    path = write_gate(repo)
    git(repo, "add", str(path))
    with pytest.raises(A.GateRefused, match="staged"):
        A.require_committed(repo, path)


def test_a_gate_changed_after_its_commit_is_refused(repo):
    path = write_gate(repo)
    git(repo, "add", str(path))
    git(repo, "commit", "-qm", "gate")
    path.write_text(json.dumps(gate_payload("cv-other")), "utf-8")
    with pytest.raises(A.GateRefused, match="differs"):
        A.require_committed(repo, path)


def test_a_committed_clean_gate_gives_its_commit(repo):
    path = write_gate(repo)
    git(repo, "add", str(path))
    git(repo, "commit", "-qm", "gate")
    commit = A.require_committed(repo, path)
    assert len(commit) == 40


def test_a_gate_outside_the_repository_is_refused(repo, tmp_path):
    outside = tmp_path / "g.json"
    outside.write_text("{}", "utf-8")
    with pytest.raises(A.GateRefused, match="outside the repository"):
        A.require_committed(repo, outside)


def test_the_first_read_is_logged(tmp_path):
    log = tmp_path / "gates" / "reads.jsonl"
    at = datetime(2026, 11, 4, 12, tzinfo=UTC)
    entry = A.claim_held_out_read(log, gate(), SHA, "c0ffee", "cv-1", now=at)
    assert entry["read_at_utc"] == "2026-11-04T12:00:00Z"
    assert A.held_out_reads(log) == [entry]


def test_a_second_read_of_a_gate_is_refused_and_not_logged(tmp_path):
    log = tmp_path / "reads.jsonl"
    A.claim_held_out_read(log, gate(), SHA, "c0ffee", "cv-1")
    with pytest.raises(A.GateRefused, match="new gate version"):
        A.claim_held_out_read(log, gate(), "b" * 64, "c0ffee", "cv-2")  # same id, other bytes
    renamed = A.GateFile.model_validate(gate_payload("cv-test-v2"))
    with pytest.raises(A.GateRefused, match="new gate version"):
        A.claim_held_out_read(log, renamed, SHA, "c0ffee", "cv-2")  # other id, same bytes
    assert len(A.held_out_reads(log)) == 1


def test_a_new_gate_version_can_read(tmp_path):
    log = tmp_path / "reads.jsonl"
    A.claim_held_out_read(log, gate(), SHA, "c0ffee", "cv-1")
    renamed = A.GateFile.model_validate(gate_payload("cv-test-v2"))
    A.claim_held_out_read(log, renamed, "b" * 64, "d00d", "cv-1")
    assert len(A.held_out_reads(log)) == 2


def test_no_log_means_no_reads(tmp_path):
    assert A.held_out_reads(tmp_path / "none.jsonl") == []


def committed_gate(repo: Path) -> Path:
    path = write_gate(repo)
    git(repo, "add", str(path))
    git(repo, "commit", "-qm", "gate")
    return path


def test_pixels_open_only_for_a_committed_gate(repo, tmp_path):
    log = tmp_path / "reads.jsonl"
    with pytest.raises(A.GateRefused, match="not committed"):
        A.grant_held_out_pixels(repo, write_gate(repo), log, "cv-1")
    assert not log.exists()


def test_a_grant_is_a_logged_read_that_cannot_be_spent_twice(repo, tmp_path):
    log = tmp_path / "reads.jsonl"
    gate_path = committed_gate(repo)
    grant = A.grant_held_out_pixels(repo, gate_path, log, "cv-1")
    assert (grant.gate_id, grant.analysis_id, grant.read_log) == ("cv-test-v1", "cv-1", log)
    check_grant(grant, "cv-1")  # the log holds it
    with pytest.raises(A.GateRefused, match="new gate version"):
        A.grant_held_out_pixels(repo, gate_path, log, "cv-2")
    assert len(A.held_out_reads(log)) == 1


def test_a_grant_opens_nothing_but_the_analysis_it_was_spent_on(repo, tmp_path):
    log = tmp_path / "reads.jsonl"
    grant = A.grant_held_out_pixels(repo, committed_gate(repo), log, "cv-1")
    with pytest.raises(HeldOutError, match="not cv-2"):
        check_grant(grant, "cv-2")


def test_a_grant_nobody_logged_opens_nothing(tmp_path):
    forged = HeldOutGrant("cv-test-v1", SHA, "cv-1", tmp_path / "reads.jsonl")
    with pytest.raises(HeldOutError, match="no read"):
        check_grant(forged, "cv-1")


def test_a_score_needs_the_read_of_that_analysis_and_happens_once(tmp_path):
    log = tmp_path / "reads.jsonl"
    with pytest.raises(A.GateRefused, match="no read"):
        A.claim_held_out_score(log, gate(), SHA, "cv-1")
    A.claim_held_out_read(log, gate(), SHA, "c0ffee", "cv-1")
    with pytest.raises(HeldOutError, match="not scored"):
        check_scored(log, "cv-test-v1", "cv-1")  # until it is scored, its pixels stay held out
    with pytest.raises(A.GateRefused, match="read cv-1, not cv-2"):
        A.claim_held_out_score(log, gate(), SHA, "cv-2")
    A.claim_held_out_score(log, gate(), SHA, "cv-1")
    check_scored(log, "cv-test-v1", "cv-1")
    with pytest.raises(A.GateRefused, match="already scored"):
        A.claim_held_out_score(log, gate(), SHA, "cv-1")
    assert [e["stage"] for e in A.held_out_reads(log)] == ["read", "score"]


# ---- a hand-made analysis --------------------------------------------------------------------


def make_record(analysis_id: str, start_s: float, frames: int, mode="calibrated") -> AnalysisRecord:
    now = datetime(2026, 11, 4, tzinfo=UTC)
    calibration = (
        CalibrationState(
            mode="calibrated", ground_frame="local_enu_junction_m",
            image_to_ground=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        )
        if mode == "calibrated"
        else CalibrationState(mode="uncalibrated")
    )  # fmt: skip
    return AnalysisRecord(
        analysis_id=analysis_id,
        title="test",
        mode="precomputed",
        data_origin="camera_cv",
        site_id="ngsim-lankershim-int2",
        created_at_utc=now,
        source=VideoSource(
            source_id="s", title="t", site_name="n", licence_name="l", attribution="a",
            source_file="f",
        ),
        fps=10.0,
        frame_count=frames,
        width_px=640,
        height_px=480,
        source_start_s=start_s,
        window_offset_s=120.0,
        calibration=calibration,
        stages=(),
        metrics=(
            Metric(
                metric_id="vehicles.total", label="v", value=1.0, unit="veh", scope="site",
                evidence_kind="approximated", status="ungated", reason="r",
            ),
        ),
        artifacts=(
            ArtifactRef(role="trajectories", path="trajectories.bin", media_type="x/y"),
            ArtifactRef(role="tracks", path="tracks.json", media_type="x/y"),
        ),
        disclosures=("d",),
        provenance=Provenance(kind="approximated", produced_by="t", produced_at_utc=now),
    )  # fmt: skip


def write_run(directory: Path, paths: list[tuple[np.ndarray, np.ndarray]], start_s=120.0, **kw):
    """One track per (u, v) path, starting on frame `start_frame` at image point `at_px`. As in a
    real camera analysis, no speed is stored."""
    start_frame = kw.get("start_frame", 0)
    at_x, at_y = kw.get("at_px", (10.0, 10.0))
    entries, columns, first = [], {k: [] for k in ("u", "v", "flags")}, 0
    for i, (u, v) in enumerate(paths):
        n = len(u)
        entries.append(
            TrackEntry(
                track_id=i, first_row=first, start_frame=start_frame, n=n, vehicle_class="car",
                class_source="size", status=kw.get("status", "partial"),
            )
        )  # fmt: skip
        first += n
        columns["u"].append(u)
        columns["v"].append(v)
        columns["flags"].append(kw.get("flags", np.zeros(n, "u1")))
    u, v = np.concatenate(columns["u"]), np.concatenate(columns["v"])
    n = len(u)
    data = quantise(
        x_px=np.full(n, at_x), y_px=np.full(n, at_y), w_px=np.full(n, 5.0), h_px=np.full(n, 5.0),
        ground_x_m=u, ground_y_m=v, heading_deg=np.zeros(n), confidence=np.ones(n),
        point_flags=np.concatenate(columns["flags"]),
    )  # fmt: skip
    frames = start_frame + max(len(p[0]) for p in paths)
    record = make_record(directory.name, start_s, frames, kw.get("mode", "calibrated"))
    write_analysis(directory, record, entries, data)
    return directory


def reference_for(frames_by_vehicle: dict[int, tuple[np.ndarray, np.ndarray]], start_s=120.0, *,
                  speed=10.0, in_view=True) -> A.Reference:  # fmt: skip
    refs, headed = {}, {}
    n = max(len(p[0]) for p in frames_by_vehicle.values())
    for k in range(n):
        key = S.tenths(start_s + k / 10)
        refs[key], headed[key] = [], []
        for vehicle, (u, v) in frames_by_vehicle.items():
            if k >= len(u):
                continue
            refs[key].append(
                S.Ref(key, vehicle, float(u[k]), float(v[k]), 180.0, in_view, speed_m_s=speed)
            )
            headed[key].append(
                HeadedReference(vehicle, (float(u[k]), float(v[k])), None, 180.0, speed, "SB", "TH")
            )
    return A.Reference(refs, headed, (), {"N": 15.0, "S": -15.0, "E": 15.0, "W": -15.0})


ROAD_SPEED_M_S = 12.0  # 1.2 m per 0.1 s frame


def down_the_road(n: int = 100, u: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    return np.full(n, u), 60 - 1.2 * np.arange(n)


def test_split_frames_follow_the_block_rule():
    record = make_record("r", 170.0, 200)  # 170.0 to 189.9: blocks 0 then 1
    dev = A.split_frames(record, "dev")
    held = A.split_frames(record, "held_out")
    assert set(dev.values()) == {0}
    assert set(held.values()) == {1}
    assert len(dev) == 100 and len(held) == 100
    assert not set(dev) & set(held)
    assert max(dev) == 99  # 179.9
    assert min(held) == 100  # 180.0


def test_seconds_with_no_published_rows_are_in_no_split():
    record = make_record("r", 100.0, 100)
    assert A.split_frames(record, "dev") == {}
    assert A.split_frames(record, "held_out") == {}


def test_frame_keys_are_tenths_of_a_source_second():
    record = make_record("r", 960.0, 100)
    assert A.frame_key(record, 0) == 9600
    assert A.frame_key(record, 3) == 9603


def compare(tmp_path, camera_paths, vehicles, *, reference_speed=ROAD_SPEED_M_S, **kw):
    directory = write_run(tmp_path / "a", camera_paths, **kw)
    run = A.load_run(directory)
    frames = A.split_frames(run.record, "dev")
    return A.compare(run, reference_for(vehicles, speed=reference_speed), frames)


def test_a_perfect_track_agrees_on_everything(tmp_path):
    path = down_the_road()
    result = compare(tmp_path, [path], {1: path}, status="complete")
    m = result["metrics"]
    assert m["vehicle_recall"]["value"] == 1.0
    assert m["vehicle_recall"]["n"] == 100
    assert m["precision_proxy"]["value"] == 1.0
    assert m["across_heading_p90_m"]["value"] == pytest.approx(0.0, abs=0.03)
    assert m["speed_abs_error_median_m_s"]["value"] == pytest.approx(0.0, abs=0.01)
    assert m["entering_count_error_share"]["value"] == 0.0
    assert m["entering_count_error_share"]["n"] == 1
    assert m["turning_matrix_agreement"]["value"] == 1.0
    assert result["tracks"]["paired"] == 1
    assert result["frames_scored"] == 100
    assert result["by_block"]["0"]["vehicle_recall"]["value"] == 1.0


def test_a_track_two_metres_across_shows_in_the_placement_error(tmp_path):
    u, v = down_the_road()
    result = compare(tmp_path, [(u + 2.0, v)], {1: (u, v)})
    assert result["metrics"]["across_heading_p90_m"]["value"] == pytest.approx(2.0, abs=0.03)
    assert result["metrics"]["vehicle_recall"]["value"] == 1.0  # still inside the 3 m window


def test_a_track_beyond_the_window_is_no_match(tmp_path):
    u, v = down_the_road()
    result = compare(tmp_path, [(u + 5.0, v)], {1: (u, v)})
    assert result["metrics"]["vehicle_recall"]["value"] == 0.0
    assert result["metrics"]["precision_proxy"]["value"] == 0.0
    assert result["metrics"]["across_heading_p90_m"]["value"] is None
    assert result["metrics"]["across_heading_p90_m"]["n"] == 0


def test_a_missed_vehicle_lowers_recall_but_not_precision(tmp_path):
    path = down_the_road()
    other = down_the_road(u=-30.0)
    m = compare(tmp_path, [path], {1: path, 2: other})["metrics"]
    assert m["vehicle_recall"]["value"] == 0.5
    assert m["precision_proxy"]["value"] == 1.0


def test_a_phantom_track_lowers_the_precision_proxy_but_not_recall(tmp_path):
    path = down_the_road()
    ghost = down_the_road(u=-30.0)
    m = compare(tmp_path, [path, ghost], {1: path})["metrics"]
    assert m["vehicle_recall"]["value"] == 1.0
    assert m["precision_proxy"]["value"] == 0.5


def test_entering_error_counts_a_surplus_track(tmp_path):
    path = down_the_road()
    ghost = down_the_road(u=-30.0)
    result = compare(tmp_path, [path, ghost], {1: path})
    assert result["metrics"]["entering_count_error_share"]["value"] == 1.0
    assert result["entering_by_arm"]["N"] == {"camera": 2, "reference": 1}


def test_a_fragment_track_is_left_out_of_counts_not_out_of_recall(tmp_path):
    path = down_the_road()
    result = compare(tmp_path, [path], {1: path}, status="fragment")
    assert result["entering_by_arm"]["N"]["camera"] == 0
    assert result["metrics"]["vehicle_recall"]["value"] == 1.0


def test_turning_agreement_compares_cells_not_only_totals(tmp_path):
    u, v = down_the_road()  # north to south
    turning = (
        np.concatenate([np.full(50, 0.0), np.linspace(0, 60, 50)]),
        np.concatenate([np.linspace(60, 0, 50), np.full(50, 0.0)]),
    )  # north to east: same total, other cell
    result = compare(tmp_path, [turning], {1: (u, v)})
    assert result["metrics"]["turning_matrix_agreement"]["value"] == 0.0
    assert result["metrics"]["turning_matrix_agreement"]["n"] == 1


def test_interpolated_rows_do_not_count(tmp_path):
    u, v = down_the_road()
    flags = np.zeros(100, "u1")
    flags[40:] = 1
    m = compare(tmp_path, [(u, v)], {1: (u, v)}, flags=flags)["metrics"]
    assert m["vehicle_recall"]["value"] == 0.4
    assert m["precision_proxy"]["n"] == 40


def test_rows_in_the_window_frame_are_not_used_for_placement(tmp_path):
    u, v = down_the_road()
    flags = np.full(100, 8, "u1")
    result = compare(tmp_path, [(u, v)], {1: (u, v)}, flags=flags)
    assert result["tracks"]["with_points"] == 0


def test_speed_error_compares_with_paired_moving_reference(tmp_path):
    """The camera side comes from the ground positions, because the analysis stores no speed."""
    path = down_the_road()
    m = compare(tmp_path, [path], {1: path}, reference_speed=9.0)["metrics"]
    speed = m["speed_abs_error_median_m_s"]
    assert speed["value"] == pytest.approx(3.0, abs=0.01)
    assert speed["median_signed_m_s"] == pytest.approx(3.0, abs=0.01)
    assert speed["n"] > 0


def test_a_dev_speed_comes_from_dev_rows_alone(tmp_path):
    """A track crosses from block 0 into block 1. Whatever it does in the held-out block, the speed
    scored on the dev split is the same, right up to the edge."""
    steady = 60 - 0.5 * np.arange(700)
    jumps = steady.copy()
    jumps[600:] += 40.0  # nothing in dev may depend on these rows
    u = np.full(700, 3.0)
    reference = reference_for({1: (u[:600], steady[:600])}, speed=5.0)
    results = []
    for name, v in (("a", steady), ("b", jumps)):
        run = A.load_run(write_run(tmp_path / name, [(u, v)]))
        results.append(A.compare(run, reference, A.split_frames(run.record, "dev")))
    assert results[0]["metrics"] == results[1]["metrics"]
    assert results[0]["metrics"]["speed_abs_error_median_m_s"]["value"] == pytest.approx(
        0, abs=0.01
    )


def test_a_track_first_seen_mid_view_is_reported_and_not_counted_as_entering(tmp_path):
    path = down_the_road()
    result = compare(tmp_path, [path], {1: path}, at_px=(320.0, 200.0), start_frame=50)
    assert result["entering_by_arm"]["N"] == {"camera": 0, "reference": 1}
    assert result["mid_view_by_arm"]["N"] == 1
    assert result["by_block"]["0"]["mid_view_by_arm"]["N"] == 1
    assert result["metrics"]["entering_count_error_share"]["value"] == 1.0
    assert "reference" in result["entering_definition"]  # the two definitions differ, and it says


def test_a_track_at_the_edge_of_the_image_enters_whenever_it_appears(tmp_path):
    path = down_the_road()
    result = compare(tmp_path, [path], {1: path}, at_px=(320.0, 20.0), start_frame=50)
    assert result["entering_by_arm"]["N"]["camera"] == 1
    assert result["mid_view_by_arm"]["N"] == 0


def test_a_track_already_running_when_the_block_opens_enters_it(tmp_path):
    """Mid-view on its first row of the block, but that row is where the block begins."""
    path = down_the_road()
    result = compare(tmp_path, [path], {1: path}, at_px=(320.0, 200.0), start_frame=0)
    assert result["entering_by_arm"]["N"]["camera"] == 1
    assert result["mid_view_by_arm"]["N"] == 0


def test_a_stopped_reference_says_nothing_about_speed(tmp_path):
    path = down_the_road()
    directory = write_run(tmp_path / "a", [path])
    run = A.load_run(directory)
    reference = reference_for({1: path}, speed=0.2)
    result = A.compare(run, reference, A.split_frames(run.record, "dev"))
    assert result["metrics"]["speed_abs_error_median_m_s"] == {"value": None, "n": 0}


def test_an_out_of_view_reference_is_not_a_miss(tmp_path):
    path = down_the_road()
    directory = write_run(tmp_path / "a", [path])
    run = A.load_run(directory)
    reference = reference_for({1: path, 2: down_the_road(u=-30.0)}, in_view=False)
    result = A.compare(run, reference, A.split_frames(run.record, "dev"))
    assert result["metrics"]["vehicle_recall"]["n"] == 0
    assert result["metrics"]["vehicle_recall"]["value"] is None


def test_a_dev_comparison_never_touches_the_held_out_block(tmp_path):
    """Rows after 180.0 s are in block 1. A comparison of the dev split must ignore them even when
    the analysis holds them: the reference is not even given those frames."""
    n = 700  # 120.0 to 189.9
    path = np.full(n, 3.0), np.linspace(60, -60, n)
    directory = write_run(tmp_path / "a", [path])
    run = A.load_run(directory)
    frames = A.split_frames(run.record, "dev")
    assert max(frames) == 599  # 179.9
    reference = reference_for({1: (path[0][:600], path[1][:600])})
    result = A.compare(run, reference, frames)
    assert result["frames_scored"] == 600
    assert result["metrics"]["vehicle_recall"]["n"] == 600
    assert set(result["by_block"]) == {"0"}


# ---- the command -----------------------------------------------------------------------------


def run_gate(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE_SCRIPT), *args], cwd=REPO, capture_output=True, text=True,
        timeout=120, stdin=subprocess.DEVNULL,
    )  # fmt: skip


def gate_in(tmp_path: Path) -> Path:
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(gate_payload()), "utf-8")
    return path


def test_the_command_refuses_an_uncommitted_gate_and_logs_nothing(tmp_path):
    n = 100  # 180.0 to 189.9: block 1, so held out
    directory = write_run(tmp_path / "held", [down_the_road(n)], start_s=180.0)
    log = tmp_path / "reads.jsonl"
    done = run_gate(
        "--gate", str(gate_in(tmp_path)), "--analysis", str(directory), "--split", "held_out",
        "--read-log", str(log), "--out", str(tmp_path / "records"),
    )  # fmt: skip
    assert done.returncode == 2
    assert "REFUSED" in done.stderr
    assert not log.exists()
    assert not (tmp_path / "records").exists()


def test_the_command_refuses_a_split_the_analysis_does_not_have(tmp_path):
    directory = write_run(tmp_path / "dev", [down_the_road()], start_s=120.0)
    done = run_gate(
        "--gate", str(gate_in(tmp_path)), "--analysis", str(directory), "--split", "held_out",
        "--read-log", str(tmp_path / "reads.jsonl"), "--out", str(tmp_path / "records"),
    )  # fmt: skip
    assert done.returncode == 2
    assert "no frame in the held_out blocks" in done.stderr
    assert not (tmp_path / "reads.jsonl").exists()


def test_the_command_refuses_an_uncalibrated_analysis(tmp_path):
    directory = write_run(tmp_path / "u", [down_the_road()], mode="uncalibrated")
    done = run_gate(
        "--gate", str(gate_in(tmp_path)), "--analysis", str(directory), "--split", "dev",
        "--out", str(tmp_path / "records"),
    )  # fmt: skip
    assert done.returncode == 2
    assert "uncalibrated" in done.stderr
