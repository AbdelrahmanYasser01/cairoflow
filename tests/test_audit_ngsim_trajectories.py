"""The NGSIM trajectory integrity audit (scripts/audit_ngsim_trajectories.py) on synthetic CSVs.

None of these tests needs the real file: each writes a small all-quoted CSV to tmp_path and
uses a page size of 4 (or 3) so that download-page boundaries fall inside a few dozen rows.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
T0 = 1_118_935_800_000  # 08:30:00.0 PDT
F0 = 1201


def _load():
    name = "audit_ngsim_trajectories"
    spec = importlib.util.spec_from_file_location(
        name, REPO / "scripts" / "audit_ngsim_trajectories.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


audit = _load()


def row(vid: int, f: int, *, x: float | None = None, length: str = "15.5", **codes: str):
    """One trajectory row at frame offset f; moves 3 ft per frame (30 ft/s) along x."""
    x = 6_452_000.0 + 3.0 * f if x is None else x
    y = 1_873_000.0 + 20.0 * vid
    c = {"lane": "2", "int_id": "0", "section": "3", "direction": "2", "movement": "1"} | codes
    return [
        str(vid),
        str(F0 + f),
        str(T0 + 100 * f),
        f"{x:.3f}",
        f"{y:.3f}",
        length,
        "6.1",
        c["lane"],
        c["int_id"],
        c["section"],
        c["direction"],
        c["movement"],
    ]


def grid(n_vehicles: int = 3, n_frames: int = 6) -> list[list[str]]:
    return [row(v, f) for f in range(n_frames) for v in range(1, n_vehicles + 1)]


def write_csv(path: Path, rows, header=audit.COLUMNS, eol: str = "\n", final: bool = True):
    lines = [",".join(f'"{v}"' for v in header)] + [",".join(f'"{v}"' for v in r) for r in rows]
    path.write_bytes((eol.join(lines) + (eol if final else "")).encode("utf-8"))
    return path


def expect(path: Path, n_rows: int, n_vehicles: int = 3, n_frames: int = 6, page: int = 4):
    data = path.read_bytes()
    return audit.Expectations(
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        n_rows=n_rows,
        n_vehicle_ids=n_vehicles,
        time_lo_ms=T0,
        time_hi_ms=T0 + 100 * n_frames,
        page_size=page,
    )


def run(tmp_path: Path, rows, *, server_rows: int | None = None, **kw):
    """Audit `rows`; `server_rows` is the expected (server) count, default the file's count."""
    write_kw = {k: kw.pop(k) for k in ("header", "eol", "final") if k in kw}
    path = write_csv(tmp_path / "t.csv", rows, **write_kw)
    exp = expect(path, len(rows) if server_rows is None else server_rows, **kw)
    result = audit.run_audit(path, exp)
    return {c.name: c for c in result.checks}, result


# ---------------------------------------------------------------- clean data


def test_a_clean_file_passes_every_local_check(tmp_path: Path) -> None:
    checks, result = run(tmp_path, grid())
    statuses = {name: c.status for name, c in checks.items()}
    assert statuses.pop("server_completeness") == audit.NOT_LOCAL
    assert set(statuses.values()) == {audit.PASS}, statuses
    pb = checks["pagination_boundaries"].evidence
    assert pb["boundaries"] == 4 and pb["pages"] == 5 and pb["rows_in_last_page"] == 2
    assert checks["frame_time_consistency"].evidence["formula"] == (
        f"frame_id = (global_time - {T0}) / 100 + {F0}"
    )
    assert result.answers["a_unique_total_order"].startswith("Yes.")
    assert "ruled out by local evidence alone" in result.answers["b_pages_complete"]["verdict"]


def test_helpers_parse_strictly() -> None:
    assert audit.split_record('"1","2.5",""') == (["1", "2.5", ""], True)
    assert audit.split_record('"1",2') == (["1", "2"], False)
    assert audit.split_record('"a""b","c"')[1] is False
    vals, ok, bad = audit.convert_column(["7", "1.5", "", "x"], is_int=True)
    assert vals[0] == 7 and ok.tolist() == [True, False, False, False]
    assert [r for _, r in bad] == [
        "non-integer number in an integer column",
        "empty",
        "not a number",
    ]
    _, ok, bad = audit.convert_column(["1e3", "inf", "nan"], is_int=False)
    assert ok.tolist() == [True, False, False]
    assert {r for _, r in bad} == {"non-finite literal"}
    assert audit.clock_pdt(T0) == "08:30:00.0"
    assert audit.clock_pdt(T0 + 899_900) == "08:44:59.9"


# ---------------------------------------------------------------- cases that must FAIL


def test_a_duplicated_row_fails(tmp_path: Path) -> None:
    rows = grid()
    rows.insert(7, list(rows[6]))
    checks, result = run(tmp_path, rows, server_rows=18)
    assert checks["exact_duplicate_rows"].status == audit.FAIL
    assert checks["exact_duplicate_rows"].evidence["extra_copies"] == 1
    assert checks["exact_duplicate_rows"].evidence["examples"][0]["rows"] == [6, 7]
    assert checks["duplicate_keys"].status == audit.FAIL
    assert checks["global_ordering"].status == audit.FAIL
    assert checks["global_ordering"].evidence["ties"] == 1
    assert checks["per_vehicle_continuity"].status == audit.FAIL
    assert checks["record_count"].status == audit.FAIL
    assert result.answers["a_unique_total_order"].startswith("No.")


def test_out_of_order_time_fails(tmp_path: Path) -> None:
    rows = grid()
    rows[3], rows[6] = rows[6], rows[3]
    checks, _ = run(tmp_path, rows)
    assert checks["global_ordering"].status == audit.FAIL
    assert checks["global_ordering"].evidence["time_inversions"] == 1
    assert checks["duplicate_keys"].status == audit.PASS


@pytest.mark.parametrize(
    "header",
    [
        tuple(c if c != "vehicle_id" else "Vehicle_ID" for c in audit.COLUMNS),
        audit.COLUMNS[:3] + ("global_y", "global_x") + audit.COLUMNS[5:],
        audit.COLUMNS[:-1],
    ],
    ids=["renamed", "reordered", "missing"],
)
def test_a_bad_header_fails(tmp_path: Path, header: tuple[str, ...]) -> None:
    checks, _ = run(tmp_path, grid(), header=header)
    assert checks["header"].status == audit.FAIL


def test_a_gap_in_a_vehicles_frames_fails(tmp_path: Path) -> None:
    rows = grid(3, 12)
    del rows[16]  # vehicle 2 at frame offset 5, away from the boundary frames (3, 4 and 8)
    checks, _ = run(tmp_path, rows, server_rows=36, n_frames=12, page=12)
    cont = checks["per_vehicle_continuity"]
    assert cont.status == audit.FAIL
    dropped = cont.evidence["gap_classes"]["dropped"]
    assert dropped["count"] == 1
    gap = dropped["examples"][0]
    assert (gap["vehicle_id"], gap["frame_before"], gap["frame_after"]) == (2, F0 + 4, F0 + 6)
    assert gap["missing_frames"] == 1
    assert checks["record_count"].status == audit.FAIL
    assert checks["pagination_boundaries"].status == audit.PASS  # the gap is mid-page


@pytest.mark.parametrize(
    ("col", "value", "reason"),
    [
        (3, "abc", "not a number"),
        (5, "NaN", "non-finite literal"),
        (7, "2.0", "non-integer number in an integer column"),
        (0, "", "empty"),
    ],
)
def test_a_non_numeric_value_fails(tmp_path: Path, col: int, value: str, reason: str) -> None:
    rows = grid()
    rows[5][col] = value
    checks, _ = run(tmp_path, rows)
    types = checks["column_types"]
    assert types.status == audit.FAIL
    name = audit.COLUMNS[col]
    assert types.evidence["columns"][name]["invalid_reasons"] == {reason: 1}
    assert types.evidence["columns"][name]["examples"][0]["row"] == 5
    if reason == "non-finite literal":
        assert checks["invalid_numerics"].status == audit.FAIL
    if reason == "empty":
        assert checks["missing_values"].status == audit.FAIL


def test_a_duplicate_key_straddling_a_page_boundary_fails(tmp_path: Path) -> None:
    rows = grid()
    copy = list(rows[3])  # last row of page 1: vehicle 1, frame offset 1
    copy[3] = "6452099.000"  # same key, different position: not an exact duplicate
    rows.insert(4, copy)  # now the first row of page 2
    checks, _ = run(tmp_path, rows, server_rows=18)
    assert checks["exact_duplicate_rows"].status == audit.PASS
    assert checks["duplicate_keys"].status == audit.FAIL
    pb = checks["pagination_boundaries"]
    assert pb.status == audit.FAIL
    first = pb.evidence["per_boundary"][0]
    assert first["key_tie_across_boundary"] is True
    assert first["keys_on_both_sides_within_500_rows"] == 1
    assert first["status"] == audit.FAIL
    assert pb.evidence["total_key_ties_across_boundaries"] == 1


def test_a_row_lost_at_a_page_boundary_is_detected(tmp_path: Path) -> None:
    rows = grid()
    del rows[4]  # first row of page 2: vehicle 2 at frame offset 1
    checks, result = run(tmp_path, rows, server_rows=18)
    pb = checks["pagination_boundaries"]
    assert pb.status == audit.FAIL
    test = pb.evidence["per_boundary"][0]["omission_test"]
    assert test["vehicle_id_open_interval"] == [1, 3]
    assert test["one_frame_holes"] == [2]
    assert "problem" in result.answers["b_pages_complete"]["verdict"]


def test_mixed_line_endings_fail_and_a_missing_final_newline_is_flagged(tmp_path: Path) -> None:
    checks, _ = run(tmp_path, grid(), eol="\r\n")
    assert checks["line_endings"].status == audit.PASS
    assert checks["line_endings"].evidence["crlf"] == 19
    checks, _ = run(tmp_path, grid(), final=False)
    assert checks["line_endings"].status == audit.INVESTIGATE
    path = write_csv(tmp_path / "mixed.csv", grid())
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n", 3))
    result = audit.run_audit(path, expect(path, 18))
    assert {c.name: c for c in result.checks}["line_endings"].status == audit.FAIL


def test_unquoted_or_short_rows_fail_field_structure(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "t.csv", grid())
    lines = path.read_text("utf-8").splitlines()
    lines[3] = lines[3].replace('"6.1"', "6.1")
    lines[6] = lines[6].rsplit(",", 1)[0]  # 11 fields
    path.write_text("\n".join(lines) + "\n", "utf-8")
    result = audit.run_audit(path, expect(path, 18))
    checks = {c.name: c for c in result.checks}
    fs = checks["field_structure"]
    assert fs.status == audit.FAIL
    assert fs.evidence["fields_per_row_histogram"] == {11: 1, 12: 17}
    assert fs.evidence["rows_not_strictly_quoted"] == 1
    assert checks["column_types"].status == audit.PASS  # the unquoted value still parses


def test_frame_id_not_affine_in_time_fails(tmp_path: Path) -> None:
    rows = grid()
    rows[5][1] = str(F0 + 9)
    checks, _ = run(tmp_path, rows)
    assert checks["frame_time_consistency"].status == audit.FAIL


def test_times_outside_the_window_or_off_grid_fail(tmp_path: Path) -> None:
    rows = grid()
    rows[0][2] = str(T0 - 100)
    rows[2][2] = str(T0 + 50)
    checks, _ = run(tmp_path, rows)
    assert checks["time_window"].status == audit.FAIL
    assert checks["time_window"].evidence["rows_outside_window"] == 1
    assert checks["time_granularity"].status == audit.FAIL
    assert checks["time_granularity"].evidence["remainders_seen"] == [50]


def test_a_vehicle_count_mismatch_and_a_checksum_mismatch_fail(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "t.csv", grid())
    (tmp_path / "SHA256SUMS").write_text(f"{'0' * 64}  data/x/t.csv\n", "utf-8")
    exp = replace(expect(path, 18), n_vehicle_ids=4)
    checks = {c.name: c for c in audit.run_audit(path, exp, tmp_path / "SHA256SUMS").checks}
    assert checks["vehicle_ids"].status == audit.FAIL
    assert checks["sha256"].status == audit.FAIL
    assert checks["sha256"].evidence["sha256sums_entry"] == "0" * 64


# ---------------------------------------------------------------- characterisation


def test_id_reuse_is_told_apart_from_a_dropped_frame(tmp_path: Path) -> None:
    rows = grid(3, 40)
    rows += [row(4, f, length="40.0") for f in range(3)]
    rows += [row(4, f, x=6_453_000.0 + 3.0 * f, length="12.0") for f in range(30, 36)]
    rows.sort(key=lambda r: (int(r[2]), int(r[0])))
    checks, _ = run(tmp_path, rows, n_vehicles=4, n_frames=40)
    cont = checks["per_vehicle_continuity"]
    assert cont.status == audit.PASS
    assert cont.evidence["gap_classes"]["reuse"]["count"] == 1
    assert cont.evidence["gap_classes"]["dropped"]["count"] == 0
    ids = checks["vehicle_ids"]
    assert ids.status == audit.PASS
    assert ids.evidence["ids_with_more_than_one_segment"] == 1
    assert ids.evidence["ids_with_dimension_change_only_between_segments"] == 1


def test_a_lost_first_row_at_a_boundary_is_only_an_endpoint_candidate(tmp_path: Path) -> None:
    rows = [r for r in grid() if not (r[0] == "2" and int(r[1]) < F0 + 2)]
    checks, _ = run(tmp_path, rows, page=3)
    pb = checks["pagination_boundaries"]
    first = pb.evidence["per_boundary"][0]["omission_test"]
    assert first["vehicle_id_open_interval"] == [1, 3]
    assert first["one_frame_holes"] == [] and first["endpoint_adjacent_ids"] == [2]
    assert pb.status == audit.PASS
    assert pb.evidence["total_endpoint_adjacent_candidates"] == 1


def test_unusually_many_trajectory_ends_at_a_boundary_need_investigation(tmp_path: Path) -> None:
    rows = [r for r in grid() if r[0] == "1" or int(r[1]) < F0 + 2]  # ids 2, 3 end at offset 1
    checks, _ = run(tmp_path, rows)
    pb = checks["pagination_boundaries"]
    first = pb.evidence["per_boundary"][0]
    assert first["continuation_at_frame_before"]["end_here"] == 2
    assert first["continuation_at_frame_before"]["end_count_above_max_elsewhere"] is True
    assert first["status"] == audit.INVESTIGATE
    assert pb.status == audit.INVESTIGATE


def test_unexpected_codes_and_dimension_changes_need_investigation(tmp_path: Path) -> None:
    rows = grid()
    rows[4] = row(2, 1, lane="0", int_id="0", section="0")
    rows[7] = row(2, 2, length="16.0")
    rows[8] = row(3, 2, direction="7")
    checks, _ = run(tmp_path, rows)
    assert checks["codes_lane_id"].status == audit.INVESTIGATE
    assert checks["codes_lane_id"].evidence["unexpected_codes"] == {"0": 1}
    assert checks["codes_direction"].status == audit.INVESTIGATE
    loc = checks["codes_location_consistency"]
    assert loc.status == audit.INVESTIGATE
    assert loc.evidence["int_id_0_and_section_id_0"] == 1
    assert checks["vehicle_ids"].status == audit.INVESTIGATE
    assert checks["vehicle_ids"].evidence["ids_with_dimension_change_inside_a_segment"] == 1


def test_a_position_jump_needs_investigation(tmp_path: Path) -> None:
    rows = grid()
    rows[7] = row(2, 2, x=6_452_006.0 + 50.0)
    checks, _ = run(tmp_path, rows)
    sp = checks["implied_speed"]
    assert sp.status == audit.INVESTIGATE
    assert sp.evidence["pairs_above_threshold"] == 2
    assert sp.evidence["vehicle_ids_above_threshold"] == 1


# ---------------------------------------------------------------- CLI


def test_the_cli_writes_a_report_and_refuses_to_overwrite(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "t.csv", grid())
    exp = expect(path, 18)
    out = tmp_path / "out" / "integrity.json"
    argv = ["--input", str(path), "--output", str(out)]
    assert audit.main(argv, exp) == 0
    report = json.loads(out.read_text("utf-8"))
    assert report["schema"] == audit.REPORT_SCHEMA
    assert report["script"].endswith("scripts/audit_ngsim_trajectories.py")
    assert report["input"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert {c["status"] for c in report["checks"]} <= set(audit.STATUSES)
    assert set(report["answers"]) == {"a_unique_total_order", "b_pages_complete"}
    before = out.read_bytes()
    assert audit.main(argv, exp) == 2
    assert out.read_bytes() == before
    assert audit.main([*argv, "--force"], exp) == 0
    assert audit.main(["--input", str(path), "--output", str(tmp_path / "x.json")]) == 1
