"""Integrity audit of the NGSIM Lankershim trajectory extract (08:30-08:45 PDT).

    uv run --frozen python scripts/audit_ngsim_trajectories.py [--force]

Reads data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv read-only (it is never
modified, moved or re-downloaded; no network is used) and writes
data/processed/ngsim-trajectories/integrity.json, refusing to overwrite an existing report unless
--force is given. Every check gets a status (PASS, FAIL, NEEDS INVESTIGATION, NOT VERIFIABLE
LOCALLY), evidence numbers and a one-line explanation; a summary table is printed. Every number
is computed from the file here, except the expected values recorded at download time
(docs/DATA_REGISTER.md section 13: byte size, SHA-256, and the server's row and distinct-vehicle
counts from count-only queries), which are labelled "expected".

Units and conventions: global_x/global_y are the vehicle front centre in CA State Plane (NAD83) US
survey feet; v_length/v_width feet; global_time epoch milliseconds; frame_id steps of 0.1 s.
"row" is a 0-based data-row index (file line = row + 2). Download page k (1-based) held rows
[(k-1) * page_size, k * page_size).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import platform
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
DEFAULT_OUTPUT = ROOT / "data/processed/ngsim-trajectories/integrity.json"
REPORT_SCHEMA = "cairoflow.ngsim-trajectory-integrity/v1"

PASS = "PASS"
FAIL = "FAIL"
INVESTIGATE = "NEEDS INVESTIGATION"
NOT_LOCAL = "NOT VERIFIABLE LOCALLY"
STATUSES = (PASS, FAIL, INVESTIGATE, NOT_LOCAL)
SEVERITY = {PASS: 0, NOT_LOCAL: 1, INVESTIGATE: 2, FAIL: 3}

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
INT_COLUMNS = frozenset(COLUMNS) - {"global_x", "global_y", "v_length", "v_width"}

FRAME_MS = 100  # codebook: frame_id ascends in 0.1 s steps
FRAME_S = 0.1
MAX_SPEED_FT_S = 120.0  # ~82 mph: faster implied motion between frames is implausible here
SHORT_GAP_FRAMES = 10  # gaps up to 1 s with consistent motion look like dropped frames
LENGTH_RANGE_FT = (4.0, 80.0)  # plausibility band, not a codebook limit
WIDTH_RANGE_FT = (2.0, 10.0)
BOUNDARY_WINDOW_ROWS = 500  # rows each side of a page boundary searched for repeated keys
MAX_EXAMPLES = 10
PDT = timezone(timedelta(hours=-7), "PDT")

_INT_TOKEN = r"-?[0-9]+"
_FLOAT_TOKEN = r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
_INT_ONE = re.compile(_INT_TOKEN)
_FLOAT_ONE = re.compile(_FLOAT_TOKEN)
_INT_ALL = re.compile(rf"(?:{_INT_TOKEN}\n)*{_INT_TOKEN}")
_FLOAT_ALL = re.compile(rf"(?:{_FLOAT_TOKEN}\n)*{_FLOAT_TOKEN}")
_NONFINITE = {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}


@dataclass(frozen=True)
class Expectations:
    """Reference values the file is checked against (None = no reference available)."""

    size_bytes: int | None
    sha256: str | None
    n_rows: int | None
    n_vehicle_ids: int | None
    time_lo_ms: int  # inclusive
    time_hi_ms: int  # exclusive
    page_size: int
    header: tuple[str, ...] = COLUMNS


NGSIM_0830 = Expectations(
    size_bytes=54_219_834,
    sha256="c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e",
    n_rows=616_749,
    n_vehicle_ids=1_125,
    time_lo_ms=1_118_935_800_000,
    time_hi_ms=1_118_936_700_000,
    page_size=50_000,
)


@dataclass(frozen=True)
class CodeDomain:
    """Codebook values for one code column; inclusive integer ranges keyed by meaning."""

    column: str
    documented: dict[str, tuple[int, int]]
    inferred: dict[str, tuple[int, int]] = field(default_factory=dict)


CODEBOOK = (
    CodeDomain(
        "lane_id",
        {
            "through lane, numbered from the left-most lane (1-10)": (1, 10),
            "left-turn bay (codebook '11+'; read as 11-30)": (11, 30),
            "right-turn bay (codebook '31+'; read as 31-100)": (31, 100),
            "mid-block left bay between intersections 3 and 4 (101)": (101, 101),
        },
    ),
    CodeDomain(
        "int_id",
        {"intersection 1-4, south to north": (1, 4)},
        {"0 = not at an intersection (inferred from truncated codebook text)": (0, 0)},
    ),
    CodeDomain(
        "section_id",
        {"section 1-5": (1, 5)},
        {"0 = at an intersection (inferred from truncated codebook text)": (0, 0)},
    ),
    CodeDomain("direction", {"1 EB, 2 NB, 3 WB, 4 SB": (1, 4)}),
    CodeDomain("movement", {"1 TH, 2 LT, 3 RT": (1, 3)}),
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    explanation: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "explanation": self.explanation,
            "evidence": to_jsonable(self.evidence),
        }


@dataclass(frozen=True)
class ByteScan:
    size_bytes: int
    sha256: str
    n_lf: int
    n_crlf: int
    n_lone_cr: int
    ends_with_newline: bool
    has_bom: bool


@dataclass
class Table:
    """Parsed CSV: one numpy array per column, aligned on the 0-based data-row index."""

    header: list[str]
    header_strictly_quoted: bool
    n_rows: int
    n_blank_lines: int
    field_counts: dict[int, int]
    n_irregular_quoting: int
    irregular_examples: list[dict[str, Any]]
    values: dict[str, np.ndarray]
    ok: dict[str, np.ndarray]
    well_formed: np.ndarray
    bad_reasons: dict[str, dict[str, int]]
    bad_examples: dict[str, list[dict[str, Any]]]

    @property
    def valid(self) -> np.ndarray:
        mask = self.well_formed.copy()
        for name in COLUMNS:
            mask &= self.ok[name]
        return mask


@dataclass(frozen=True)
class Rows:
    """The fully parsed rows only; `row` maps each back to its 0-based data-row index."""

    row: np.ndarray
    cols: dict[str, np.ndarray]

    def __getitem__(self, name: str) -> np.ndarray:
        return self.cols[name]

    def __len__(self) -> int:
        return int(self.row.size)


@dataclass(frozen=True)
class Continuity:
    """Per-vehicle_id sequence analysis (rows sorted by vehicle_id, then frame_id)."""

    n_pairs: int
    n_step1: int
    n_step0: int
    step0_examples: list[dict[str, Any]]
    gaps: dict[str, np.ndarray]
    speed_ft_s: np.ndarray
    speed_vid: np.ndarray
    speed_frame: np.ndarray
    speed_rows: np.ndarray
    seg_vid: np.ndarray
    seg_start: np.ndarray
    seg_end: np.ndarray
    seg_dims_const: np.ndarray
    frame_min: int
    frame_max: int
    present: np.ndarray
    starts: np.ndarray
    ends: np.ndarray
    codes: np.ndarray
    vid_min: int
    vid_span: int

    def ids_in_frame(self, frame: int, lo_excl: int, hi_excl: int) -> np.ndarray:
        """vehicle_ids present at `frame` with lo_excl < id < hi_excl."""
        if self.codes.size == 0 or not self.frame_min <= frame <= self.frame_max:
            return np.empty(0, np.int64)
        lo = max(lo_excl + 1, self.vid_min)
        hi = min(hi_excl - 1, self.vid_min + self.vid_span - 1)
        if lo > hi:
            return np.empty(0, np.int64)
        base = (frame - self.frame_min) * self.vid_span
        i = np.searchsorted(self.codes, base + lo - self.vid_min, side="left")
        j = np.searchsorted(self.codes, base + hi - self.vid_min, side="right")
        return np.unique(self.codes[i:j] - base + self.vid_min)

    def is_present(self, frame: int, vids: np.ndarray) -> np.ndarray:
        vids = np.asarray(vids, np.int64)
        if self.codes.size == 0 or not self.frame_min <= frame <= self.frame_max:
            return np.zeros(vids.size, bool)
        inside = (vids >= self.vid_min) & (vids < self.vid_min + self.vid_span)
        codes = (frame - self.frame_min) * self.vid_span + (vids - self.vid_min)
        idx = np.clip(np.searchsorted(self.codes, codes), 0, self.codes.size - 1)
        return inside & (self.codes[idx] == codes)


# ---------------------------------------------------------------- helpers


def to_jsonable(obj: Any) -> Any:
    """Convert numpy scalars/arrays and tuples into plain JSON types."""
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return to_jsonable(obj.tolist())
    if isinstance(obj, np.generic):
        return to_jsonable(obj.item())
    if isinstance(obj, float):
        return round(obj, 4) if math.isfinite(obj) else str(obj)
    return obj


def clock_pdt(ms: int) -> str:
    """Epoch milliseconds as PDT wall-clock time to 0.1 s, e.g. '08:30:00.0'."""
    ms = int(ms)
    return datetime.fromtimestamp(ms // 1000, PDT).strftime("%H:%M:%S.") + str(ms % 1000 // 100)


def _stats(a: np.ndarray) -> dict[str, Any]:
    if a.size == 0:
        return {"n": 0}
    q = np.percentile(a, [50, 99, 99.9])
    return {
        "n": int(a.size),
        "min": float(a.min()),
        "p50": float(q[0]),
        "p99": float(q[1]),
        "p99_9": float(q[2]),
        "max": float(a.max()),
    }


def _worst(statuses: Sequence[str]) -> str:
    return max(statuses, key=SEVERITY.__getitem__) if statuses else PASS


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def split_record(line: str) -> tuple[list[str], bool]:
    """Split one CSV line; the flag says whether it is strictly `"v1","v2",...` form."""
    if len(line) >= 2 and line[0] == '"' and line[-1] == '"':
        fields = line[1:-1].split('","')
        if all('"' not in f for f in fields):
            return fields, True
    return next(csv.reader([line])), False


def _reason(value: str, is_int: bool) -> str:
    if value == "":
        return "empty"
    if value.strip().lower() in _NONFINITE:
        return "non-finite literal"
    if is_int and _FLOAT_ONE.fullmatch(value):
        return "non-integer number in an integer column"
    if value != value.strip():
        return "surrounding whitespace"
    return "not a number"


def convert_column(
    strs: Sequence[str], is_int: bool
) -> tuple[np.ndarray, np.ndarray, list[tuple[int, str]]]:
    """Strictly parse decimal strings; returns values, ok mask and (index, reason) failures."""
    dtype = np.int64 if is_int else np.float64
    n = len(strs)
    if n == 0:
        return np.empty(0, dtype), np.empty(0, bool), []
    every, one = (_INT_ALL, _INT_ONE) if is_int else (_FLOAT_ALL, _FLOAT_ONE)
    if every.fullmatch("\n".join(strs)):
        try:
            return np.array(strs, dtype=dtype), np.ones(n, bool), []
        except (ValueError, OverflowError):
            pass
    out = np.zeros(n, dtype)
    ok = np.zeros(n, bool)
    bad: list[tuple[int, str]] = []
    for i, s in enumerate(strs):
        if one.fullmatch(s):
            try:
                out[i] = int(s) if is_int else float(s)
                ok[i] = True
                continue
            except OverflowError:
                bad.append((i, "out of int64 range"))
                continue
        bad.append((i, _reason(s, is_int)))
    return out, ok, bad


# ---------------------------------------------------------------- reading


def scan_bytes(path: Path) -> ByteScan:
    """One streaming pass: size, SHA-256 and line-ending counts."""
    digest = hashlib.sha256()
    size = n_lf = n_cr = n_crlf = 0
    prev_cr = False
    last = b""
    has_bom = False
    with path.open("rb") as fh:
        first = True
        while chunk := fh.read(1 << 20):
            if first:
                has_bom = chunk.startswith(b"\xef\xbb\xbf")
                first = False
            digest.update(chunk)
            size += len(chunk)
            n_lf += chunk.count(b"\n")
            n_cr += chunk.count(b"\r")
            n_crlf += chunk.count(b"\r\n") + int(prev_cr and chunk[:1] == b"\n")
            prev_cr = chunk.endswith(b"\r")
            last = chunk[-1:]
    return ByteScan(
        size_bytes=size,
        sha256=digest.hexdigest(),
        n_lf=n_lf,
        n_crlf=n_crlf,
        n_lone_cr=n_cr - n_crlf,
        ends_with_newline=last == b"\n",
        has_bom=has_bom,
    )


def read_table(path: Path, chunk_rows: int = 50_000) -> Table:
    """Parse the CSV in chunks into per-column numpy arrays (strings are not kept)."""
    n_cols = len(COLUMNS)
    header: list[str] = []
    header_strict = False
    n_rows = n_blank = n_irregular = 0
    field_counts: Counter[int] = Counter()
    irregular: list[dict[str, Any]] = []
    malformed: list[int] = []
    parts: dict[str, list[np.ndarray]] = {c: [] for c in COLUMNS}
    oks: dict[str, list[np.ndarray]] = {c: [] for c in COLUMNS}
    reasons: dict[str, Counter[str]] = {c: Counter() for c in COLUMNS}
    examples: dict[str, list[dict[str, Any]]] = {c: [] for c in COLUMNS}
    chunk: list[list[str]] = []
    chunk_start = 0

    def flush() -> None:
        nonlocal chunk, chunk_start
        if not chunk:
            return
        for j, name in enumerate(COLUMNS):
            strs = [r[j] for r in chunk]
            vals, ok, bad = convert_column(strs, name in INT_COLUMNS)
            parts[name].append(vals)
            oks[name].append(ok)
            for i, why in bad:
                reasons[name][why] += 1
                if len(examples[name]) < MAX_EXAMPLES:
                    examples[name].append(
                        {"row": chunk_start + i, "value": strs[i][:40], "reason": why}
                    )
        chunk_start += len(chunk)
        chunk = []

    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        first = fh.readline()
        if first:
            header, header_strict = split_record(first.rstrip("\r\n"))
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line.strip():
                n_blank += 1
                continue
            fields, strict = split_record(line)
            field_counts[len(fields)] += 1
            if not strict:
                n_irregular += 1
                if len(irregular) < MAX_EXAMPLES:
                    irregular.append({"row": n_rows, "line": line[:120]})
            if len(fields) != n_cols:
                malformed.append(n_rows)
                fields = ["0"] * n_cols  # placeholder; the row is marked not well formed
            chunk.append(fields)
            n_rows += 1
            if len(chunk) >= chunk_rows:
                flush()
        flush()

    values = {c: np.concatenate(parts[c]) if parts[c] else np.empty(0, np.int64) for c in COLUMNS}
    ok = {c: np.concatenate(oks[c]) if oks[c] else np.empty(0, bool) for c in COLUMNS}
    well_formed = np.ones(n_rows, bool)
    well_formed[np.asarray(malformed, np.int64)] = False
    return Table(
        header=header,
        header_strictly_quoted=header_strict,
        n_rows=n_rows,
        n_blank_lines=n_blank,
        field_counts=dict(sorted(field_counts.items())),
        n_irregular_quoting=n_irregular,
        irregular_examples=irregular,
        values=values,
        ok=ok,
        well_formed=well_formed,
        bad_reasons={c: dict(reasons[c]) for c in COLUMNS},
        bad_examples=examples,
    )


def valid_rows(table: Table) -> Rows:
    mask = table.valid
    return Rows(row=np.flatnonzero(mask), cols={c: table.values[c][mask] for c in COLUMNS})


# ---------------------------------------------------------------- file-level checks


def check_file_exists(path: Path) -> Check:
    if path.is_file():
        return Check("file_exists", PASS, "Input file exists.", {"path": _display_path(path)})
    return Check("file_exists", FAIL, "Input file is missing.", {"path": _display_path(path)})


def check_byte_size(scan: ByteScan, expected: int | None) -> Check:
    ev = {"size_bytes": scan.size_bytes, "expected_size_bytes": expected}
    if expected is None:
        return Check("byte_size", INVESTIGATE, "No recorded size to compare against.", ev)
    if scan.size_bytes == expected:
        return Check("byte_size", PASS, f"{scan.size_bytes:,} bytes, as recorded.", ev)
    return Check("byte_size", FAIL, f"{scan.size_bytes:,} bytes, recorded {expected:,}.", ev)


def read_sums_entry(sums_file: Path | None, target: Path) -> str | None:
    """The SHA-256 listed for `target` (matched by file name) in a sha256sum-style file."""
    if sums_file is None or not sums_file.is_file():
        return None
    for line in sums_file.read_text("utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and Path(parts[-1].lstrip("*")).name == target.name:
            return parts[0].lower()
    return None


def check_sha256(scan: ByteScan, expected: str | None, sums_entry: str | None) -> Check:
    ev = {
        "sha256": scan.sha256,
        "expected_sha256": expected,
        "sha256sums_entry": sums_entry,
    }
    refs = [r for r in (expected, sums_entry) if r is not None]
    if not refs:
        return Check("sha256", INVESTIGATE, "No recorded SHA-256 to compare against.", ev)
    if all(r == scan.sha256 for r in refs):
        return Check(
            "sha256",
            PASS,
            f"SHA-256 matches {len(refs)} recorded reference(s) (register and/or SHA256SUMS).",
            ev,
        )
    return Check("sha256", FAIL, "SHA-256 differs from a recorded reference.", ev)


def check_line_endings(scan: ByteScan) -> Check:
    ev = {
        "lf": scan.n_lf,
        "crlf": scan.n_crlf,
        "lone_cr": scan.n_lone_cr,
        "ends_with_newline": scan.ends_with_newline,
        "utf8_bom": scan.has_bom,
    }
    if scan.n_lone_cr or (scan.n_crlf and scan.n_crlf != scan.n_lf):
        return Check("line_endings", FAIL, "Mixed or bare-CR line endings.", ev)
    if not scan.ends_with_newline or scan.has_bom:
        return Check(
            "line_endings",
            INVESTIGATE,
            "File lacks a final newline (possible truncation) or starts with a BOM.",
            ev,
        )
    style = "CRLF" if scan.n_crlf else "LF"
    return Check(
        "line_endings", PASS, f"{scan.n_lf:,} {style} line endings, final newline present.", ev
    )


def check_record_count(table: Table, scan: ByteScan, expected: int | None) -> Check:
    lines_expected = table.n_rows + table.n_blank_lines + (1 if table.header else 0)
    ev = {
        "data_rows": table.n_rows,
        "blank_lines": table.n_blank_lines,
        "newlines": scan.n_lf,
        "newlines_consistent_with_rows": scan.n_lf == lines_expected,
        "expected_rows_server_count": expected,
    }
    if expected is None:
        return Check("record_count", INVESTIGATE, "No recorded row count to compare.", ev)
    if table.n_rows == expected and table.n_blank_lines == 0:
        return Check(
            "record_count",
            PASS,
            f"{table.n_rows:,} data rows = server count recorded before download; no blank lines.",
            ev,
        )
    return Check(
        "record_count",
        FAIL,
        f"{table.n_rows:,} data rows ({table.n_blank_lines} blank lines); expected {expected:,}.",
        ev,
    )


def check_field_structure(table: Table) -> Check:
    n_cols = len(COLUMNS)
    wrong = sum(c for k, c in table.field_counts.items() if k != n_cols)
    ev = {
        "fields_per_row_histogram": table.field_counts,
        "rows_with_wrong_field_count": wrong,
        "rows_not_strictly_quoted": table.n_irregular_quoting,
        "header_fields": len(table.header),
        "header_strictly_quoted": table.header_strictly_quoted,
        "examples": table.irregular_examples,
    }
    if wrong or table.n_irregular_quoting or not table.header_strictly_quoted:
        return Check(
            "field_structure",
            FAIL,
            f"{wrong} rows without {n_cols} fields; {table.n_irregular_quoting} rows not in "
            '"v","v",... form.',
            ev,
        )
    return Check(
        "field_structure",
        PASS,
        f"All {table.n_rows:,} rows have {n_cols} fields, every value double-quoted, no embedded "
        "quotes.",
        ev,
    )


def check_header(table: Table, expected: tuple[str, ...]) -> Check:
    found = tuple(table.header)
    ev = {
        "found": list(found),
        "expected": list(expected),
        "missing": [c for c in expected if c not in found],
        "unexpected": [c for c in found if c not in expected],
    }
    if found == expected:
        return Check("header", PASS, f"Header is exactly the {len(expected)} approved columns.", ev)
    return Check("header", FAIL, "Header differs from the approved columns (names or order).", ev)


def check_column_types(table: Table) -> Check:
    per_col: dict[str, Any] = {}
    total_bad = 0
    mask_wf = table.well_formed
    for name in COLUMNS:
        ok = table.ok[name] & mask_wf
        vals = table.values[name][ok]
        n_bad = int((~table.ok[name] & mask_wf).sum())
        total_bad += n_bad
        per_col[name] = {
            "type": "int" if name in INT_COLUMNS else "float",
            "n_invalid": n_bad,
            "min": vals.min() if vals.size else None,
            "max": vals.max() if vals.size else None,
            "invalid_reasons": table.bad_reasons[name],
            "examples": table.bad_examples[name],
        }
    ev = {"columns": per_col}
    if total_bad:
        cols = [c for c in COLUMNS if per_col[c]["n_invalid"]]
        return Check(
            "column_types",
            FAIL,
            f"{total_bad} values fail strict int/float parsing (columns: {', '.join(cols)}).",
            ev,
        )
    return Check(
        "column_types",
        PASS,
        "All values parse: integer columns as plain integers, float columns as finite decimals.",
        ev,
    )


def check_missing_values(table: Table) -> Check:
    empty = {c: int(table.bad_reasons[c].get("empty", 0)) for c in COLUMNS}
    total = sum(empty.values())
    ev = {"empty_values_per_column": empty}
    if total:
        return Check("missing_values", FAIL, f"{total} empty values.", ev)
    return Check("missing_values", PASS, "No empty values in any column.", ev)


def check_invalid_numerics(table: Table, rows: Rows) -> Check:
    nonfinite = {
        c: int(table.bad_reasons[c].get("non-finite literal", 0))
        for c in COLUMNS
        if c not in INT_COLUMNS
    }
    length, width, vid = rows["v_length"], rows["v_width"], rows["vehicle_id"]
    out_len = (length < LENGTH_RANGE_FT[0]) | (length > LENGTH_RANGE_FT[1])
    out_wid = (width < WIDTH_RANGE_FT[0]) | (width > WIDTH_RANGE_FT[1])
    x, y = rows["global_x"], rows["global_y"]
    ev = {
        "nonfinite_literals": nonfinite,
        "v_length_nonpositive_rows": int((length <= 0).sum()),
        "v_width_nonpositive_rows": int((width <= 0).sum()),
        "v_length_plausible_range_ft": LENGTH_RANGE_FT,
        "v_width_plausible_range_ft": WIDTH_RANGE_FT,
        "v_length_out_of_range_rows": int(out_len.sum()),
        "v_length_out_of_range_vehicle_ids": int(np.unique(vid[out_len]).size),
        "v_width_out_of_range_rows": int(out_wid.sum()),
        "v_width_out_of_range_vehicle_ids": int(np.unique(vid[out_wid]).size),
        "v_length_ft_range": [length.min(), length.max()] if len(rows) else None,
        "v_width_ft_range": [width.min(), width.max()] if len(rows) else None,
        "global_x_extent_ft": float(x.max() - x.min()) if len(rows) else None,
        "global_y_extent_ft": float(y.max() - y.min()) if len(rows) else None,
    }
    if sum(nonfinite.values()) or ev["v_length_nonpositive_rows"] or ev["v_width_nonpositive_rows"]:
        return Check(
            "invalid_numerics", FAIL, "NaN/inf values or non-positive vehicle dimensions.", ev
        )
    if out_len.any() or out_wid.any():
        return Check(
            "invalid_numerics",
            INVESTIGATE,
            f"No NaN/inf/non-positive values, but {int(out_len.sum())} rows have v_length and "
            f"{int(out_wid.sum())} rows v_width outside the plausibility band.",
            ev,
        )
    return Check(
        "invalid_numerics",
        PASS,
        "No NaN/inf, dimensions positive and within the plausibility bands.",
        ev,
    )


# ---------------------------------------------------------------- time and codes


def check_time_window(rows: Rows, lo_ms: int, hi_ms: int) -> Check:
    gt = rows["global_time"]
    if gt.size == 0:
        return Check("time_window", FAIL, "No parsable rows.", {})
    outside = (gt < lo_ms) | (gt >= hi_ms)
    tmin, tmax = int(gt.min()), int(gt.max())
    ev = {
        "min_global_time": tmin,
        "max_global_time": tmax,
        "min_clock_pdt": clock_pdt(tmin),
        "max_clock_pdt": clock_pdt(tmax),
        "query_window_ms": [lo_ms, hi_ms],
        "query_window_pdt": [clock_pdt(lo_ms), clock_pdt(hi_ms)],
        "rows_outside_window": int(outside.sum()),
        "span_matches_server_report": tmin == lo_ms and tmax == hi_ms - FRAME_MS,
    }
    if outside.any():
        return Check(
            "time_window", FAIL, f"{int(outside.sum())} rows lie outside the query window.", ev
        )
    return Check(
        "time_window",
        PASS,
        f"All times in [{clock_pdt(lo_ms)}, {clock_pdt(hi_ms)}) PDT; observed "
        f"{clock_pdt(tmin)}-{clock_pdt(tmax)}.",
        ev,
    )


def check_time_granularity(rows: Rows) -> Check:
    gt = rows["global_time"]
    rem = gt % FRAME_MS
    bad = rem != 0
    ev = {
        "rows_not_multiple_of_100ms": int(bad.sum()),
        "remainders_seen": np.unique(rem[bad]).tolist()[:MAX_EXAMPLES],
    }
    if bad.any():
        return Check(
            "time_granularity",
            FAIL,
            f"{int(bad.sum())} global_time values are not multiples of 100 ms.",
            ev,
        )
    return Check("time_granularity", PASS, "Every global_time is a multiple of 100 ms.", ev)


def check_frame_time(rows: Rows) -> Check:
    gt, fr = rows["global_time"], rows["frame_id"]
    if gt.size == 0:
        return Check("frame_time_consistency", FAIL, "No parsable rows.", {})
    offset = gt // FRAME_MS - fr
    offs, counts = np.unique(offset, return_counts=True)
    frames, per_frame = np.unique(fr, return_counts=True)
    n_times = int(np.unique(gt).size)
    span = int(frames.max() - frames.min() + 1)
    t0 = int(gt.min())
    f0 = t0 // FRAME_MS - int(offs[0])
    ev = {
        "distinct_offsets_frames": {str(int(o)): int(c) for o, c in zip(offs, counts, strict=True)},
        "formula": f"frame_id = (global_time - {t0}) / {FRAME_MS} + {f0}",
        "frame_id_range": [int(frames.min()), int(frames.max())],
        "distinct_frames": int(frames.size),
        "distinct_times": n_times,
        "frames_missing_inside_range": span - int(frames.size),
        "rows_per_frame": _stats(per_frame.astype(float)),
    }
    if offs.size != 1 or n_times != frames.size:
        return Check(
            "frame_time_consistency",
            FAIL,
            f"frame_id is not one affine function of global_time ({offs.size} offsets).",
            ev,
        )
    if span != frames.size:
        return Check(
            "frame_time_consistency",
            INVESTIGATE,
            f"Affine with 100 ms per frame, but {span - frames.size} whole frames have no rows.",
            ev,
        )
    return Check(
        "frame_time_consistency",
        PASS,
        f"{ev['formula']} on every row; {frames.size:,} contiguous frames, none empty.",
        ev,
    )


def _code_label(value: int, domain: CodeDomain) -> tuple[str, str]:
    for kind, table in (("documented", domain.documented), ("inferred", domain.inferred)):
        for label, (lo, hi) in table.items():
            if lo <= value <= hi:
                return kind, label
    return "unexpected", "not in the codebook"


def check_code_domain(rows: Rows, domain: CodeDomain) -> Check:
    vals = rows[domain.column]
    uniq, counts = np.unique(vals, return_counts=True)
    by_label: Counter[str] = Counter()
    unexpected: dict[str, int] = {}
    inferred_used: dict[str, int] = {}
    for u, c in zip(uniq.tolist(), counts.tolist(), strict=True):
        kind, label = _code_label(u, domain)
        by_label[f"{kind}: {label}"] += c
        if kind == "unexpected":
            unexpected[str(u)] = c
        elif kind == "inferred":
            inferred_used[str(u)] = c
    ev = {
        "value_counts": {str(u): c for u, c in zip(uniq.tolist(), counts.tolist(), strict=True)},
        "rows_by_meaning": dict(by_label),
        "unexpected_codes": unexpected,
        "inferred_codes_used": inferred_used,
    }
    name = f"codes_{domain.column}"
    if unexpected:
        n = sum(unexpected.values())
        return Check(
            name,
            INVESTIGATE,
            f"{n:,} rows carry codes not in the codebook: {', '.join(unexpected)}.",
            ev,
        )
    note = f" (inferred code(s) {', '.join(inferred_used)} used)" if inferred_used else ""
    return Check(name, PASS, f"All {uniq.size} distinct codes are in the codebook{note}.", ev)


def check_location_codes(rows: Rows) -> Check:
    """Cross-tab int_id vs section_id (tests the inferred meaning of 0) and lane 0 / 101 use."""
    i0 = rows["int_id"] == 0
    s0 = rows["section_id"] == 0
    lane = rows["lane_id"]
    lane0 = lane == 0
    l101 = lane == 101
    neither = i0 & s0

    def counts(column: str, mask: np.ndarray) -> dict[int, int]:
        u, c = np.unique(rows[column][mask], return_counts=True)
        return dict(zip(u.tolist(), c.tolist(), strict=True))

    ev = {
        "int_id_0_and_section_id_0": int(neither.sum()),
        "int_id_nonzero_and_section_id_nonzero": int((~i0 & ~s0).sum()),
        "int_id_0_only (on a section)": int((i0 & ~s0).sum()),
        "section_id_0_only (at an intersection)": int((~i0 & s0).sum()),
        "both_zero_breakdown": {
            "vehicle_ids": int(np.unique(rows["vehicle_id"][neither]).size),
            "lane_id": counts("lane_id", neither),
            "direction": counts("direction", neither),
            "movement": counts("movement", neither),
        },
        "lane_id_0_rows": int(lane0.sum()),
        "lane_id_0_rows_at_an_intersection": int((lane0 & ~i0).sum()),
        "lane_id_0_rows_on_a_section": int((lane0 & ~s0).sum()),
        "lane_id_0_rows_with_both_ids_0": int((lane0 & neither).sum()),
        "lane_id_0_by_int_id": counts("int_id", lane0),
        "lane_id_0_by_section_id": counts("section_id", lane0),
        "lane_101_section_id_counts": counts("section_id", l101),
        "lane_101_int_id_counts": counts("int_id", l101),
    }
    n_neither = ev["int_id_0_and_section_id_0"]
    n_both = ev["int_id_nonzero_and_section_id_nonzero"]
    if n_neither or n_both:
        return Check(
            "codes_location_consistency",
            INVESTIGATE,
            f"{n_neither:,} rows ({ev['both_zero_breakdown']['vehicle_ids']} vehicles) have "
            f"int_id = section_id = 0 and {n_both:,} have both non-zero; lane_id 0 occurs on "
            f"{ev['lane_id_0_rows_at_an_intersection']:,} intersection rows and "
            f"{ev['lane_id_0_rows_on_a_section'] + ev['lane_id_0_rows_with_both_ids_0']:,} others.",
            ev,
        )
    return Check(
        "codes_location_consistency",
        PASS,
        "Every row has exactly one of int_id, section_id non-zero, which supports the inferred "
        "meaning of 0.",
        ev,
    )


# ---------------------------------------------------------------- keys, order, duplicates


def check_exact_duplicates(rows: Rows) -> Check:
    n = len(rows)
    if n < 2:
        return Check("exact_duplicate_rows", PASS, "Fewer than two rows.", {"extra_copies": 0})
    order = np.lexsort([rows[c] for c in reversed(COLUMNS)])
    eq = np.ones(n - 1, bool)
    for c in COLUMNS:
        a = rows[c][order]
        eq &= a[1:] == a[:-1]
    idx = np.flatnonzero(eq)
    examples = [
        {"rows": [int(rows.row[order[i]]), int(rows.row[order[i + 1]])]} for i in idx[:MAX_EXAMPLES]
    ]
    ev = {"extra_copies": int(idx.size), "examples": examples}
    if idx.size:
        return Check(
            "exact_duplicate_rows", FAIL, f"{idx.size} rows are exact copies of another row.", ev
        )
    return Check("exact_duplicate_rows", PASS, "No two rows are identical in all 12 fields.", ev)


def _duplicate_pairs(rows: Rows, first: str, second: str) -> tuple[int, int, list[Any]]:
    a, b = rows[first], rows[second]
    order = np.lexsort((b, a))
    sa, sb = a[order], b[order]
    eq = (sa[1:] == sa[:-1]) & (sb[1:] == sb[:-1])
    idx = np.flatnonzero(eq)
    groups = int(np.unique(np.stack([sa[idx], sb[idx]]), axis=1).shape[1]) if idx.size else 0
    ex = [
        {first: sa[i], second: sb[i], "rows": [rows.row[order[i]], rows.row[order[i + 1]]]}
        for i in idx[:MAX_EXAMPLES]
    ]
    return int(idx.size), groups, ex


def check_duplicate_keys(rows: Rows) -> Check:
    extra, groups, ex = _duplicate_pairs(rows, "global_time", "vehicle_id")
    f_extra, f_groups, f_ex = _duplicate_pairs(rows, "frame_id", "vehicle_id")
    ev = {
        "duplicate_key_extra_rows": extra,
        "duplicate_key_groups": groups,
        "distinct_keys": len(rows) - extra,
        "examples": ex,
        "frame_vehicle_duplicate_extra_rows": f_extra,
        "frame_vehicle_duplicate_groups": f_groups,
        "frame_vehicle_examples": f_ex,
    }
    if extra or f_extra:
        return Check(
            "duplicate_keys",
            FAIL,
            f"{extra} rows repeat a (global_time, vehicle_id) key; {f_extra} repeat "
            "(frame_id, vehicle_id).",
            ev,
        )
    return Check(
        "duplicate_keys",
        PASS,
        f"All {len(rows):,} (global_time, vehicle_id) keys are distinct (and so are "
        "(frame_id, vehicle_id)).",
        ev,
    )


def check_ordering(rows: Rows) -> Check:
    gt, vid = rows["global_time"], rows["vehicle_id"]
    n = len(rows)
    if n < 2:
        return Check("global_ordering", PASS, "Fewer than two rows.", {"adjacent_pairs": 0})
    dt = np.diff(gt)
    dv = np.diff(vid)
    inv = (dt < 0) | ((dt == 0) & (dv < 0))
    tie = (dt == 0) & (dv == 0)
    ev: dict[str, Any] = {
        "adjacent_pairs": n - 1,
        "inversions": int(inv.sum()),
        "time_inversions": int((dt < 0).sum()),
        "ties": int(tie.sum()),
        "vehicle_id_comparison": "integer",
        "examples": [
            {
                "rows": [rows.row[i], rows.row[i + 1]],
                "keys": [[gt[i], vid[i]], [gt[i + 1], vid[i + 1]]],
            }
            for i in np.flatnonzero(inv | tie)[:MAX_EXAMPLES]
        ],
    }
    if inv.any() and not (dt < 0).any():
        vs = vid.astype(str)
        text_inv = (dt == 0) & (vs[1:] < vs[:-1])
        ev["inversions_if_vehicle_id_compared_as_text"] = int(text_inv.sum())
    if inv.any() or tie.any():
        return Check(
            "global_ordering",
            FAIL,
            f"{int(inv.sum())} inversions and {int(tie.sum())} ties in (global_time, vehicle_id) "
            "order.",
            ev,
        )
    return Check(
        "global_ordering",
        PASS,
        f"Strictly increasing in (global_time, integer vehicle_id) over all {n - 1:,} adjacent "
        "pairs.",
        ev,
    )


# ---------------------------------------------------------------- per-vehicle continuity


def _empty_continuity() -> Continuity:
    e = np.empty(0, np.int64)
    return Continuity(
        n_pairs=0,
        n_step1=0,
        n_step0=0,
        step0_examples=[],
        gaps={},
        speed_ft_s=np.empty(0),
        speed_vid=e,
        speed_frame=e,
        speed_rows=e,
        seg_vid=e,
        seg_start=e,
        seg_end=e,
        seg_dims_const=np.empty(0, bool),
        frame_min=0,
        frame_max=-1,
        present=e,
        starts=e,
        ends=e,
        codes=e,
        vid_min=0,
        vid_span=1,
    )


def analyse_continuity(rows: Rows) -> Continuity:
    """Sort by (vehicle_id, frame_id); measure steps, gaps, speeds and trajectory segments."""
    vid0, fr0 = rows["vehicle_id"], rows["frame_id"]
    order = np.lexsort((fr0, vid0))
    v, f = vid0[order], fr0[order]
    x, y = rows["global_x"][order], rows["global_y"][order]
    length, width = rows["v_length"][order], rows["v_width"][order]
    r = rows.row[order]
    n = v.size
    if n == 0:
        return _empty_continuity()
    same = v[1:] == v[:-1]
    step = np.diff(f)
    p = np.flatnonzero(same)
    s = step[p]
    dist = np.hypot(x[p + 1] - x[p], y[p + 1] - y[p])
    dims_same = (length[p + 1] == length[p]) & (width[p + 1] == width[p])
    one, zero, gap = s == 1, s == 0, s >= 2
    g = p[gap]
    gstep = s[gap]
    gdist = dist[gap]
    gspeed = gdist / (gstep * FRAME_S)
    gdims = dims_same[gap]
    cls = np.where(
        ~gdims | (gspeed > MAX_SPEED_FT_S),
        "reuse",
        np.where(gstep <= SHORT_GAP_FRAMES, "dropped", "ambiguous"),
    )
    gaps = {
        "vehicle_id": v[g],
        "frame_before": f[g],
        "frame_after": f[g + 1],
        "missing_frames": gstep - 1,
        "distance_ft": gdist,
        "implied_speed_ft_s": gspeed,
        "dims_same": gdims,
        "row_before": r[g],
        "row_after": r[g + 1],
        "class": cls,
    }
    z = p[zero]
    step0_examples = [
        {"vehicle_id": v[i], "frame_id": f[i], "rows": [r[i], r[i + 1]]} for i in z[:MAX_EXAMPLES]
    ]
    q = p[one]
    brk = np.ones(n, bool)
    brk[1:] = ~same | (step >= 2)
    st = np.flatnonzero(brk)
    en = np.r_[st[1:] - 1, n - 1]
    seg_dims_const = (np.minimum.reduceat(length, st) == np.maximum.reduceat(length, st)) & (
        np.minimum.reduceat(width, st) == np.maximum.reduceat(width, st)
    )
    fmin, fmax = int(f.min()), int(f.max())
    nf = fmax - fmin + 1
    vmin = int(v.min())
    span = int(v.max()) - vmin + 1
    codes = np.sort((f - fmin) * span + (v - vmin))
    return Continuity(
        n_pairs=int(p.size),
        n_step1=int(one.sum()),
        n_step0=int(zero.sum()),
        step0_examples=step0_examples,
        gaps=gaps,
        speed_ft_s=dist[one] / FRAME_S,
        speed_vid=v[q],
        speed_frame=f[q],
        speed_rows=np.stack([r[q], r[q + 1]], axis=1),
        seg_vid=v[st],
        seg_start=f[st],
        seg_end=f[en],
        seg_dims_const=seg_dims_const,
        frame_min=fmin,
        frame_max=fmax,
        present=np.bincount(f - fmin, minlength=nf),
        starts=np.bincount(f[st] - fmin, minlength=nf),
        ends=np.bincount(f[en] - fmin, minlength=nf),
        codes=codes,
        vid_min=vmin,
        vid_span=span,
    )


def _gap_examples(cont: Continuity, mask: np.ndarray) -> list[dict[str, Any]]:
    idx = np.flatnonzero(mask)[:MAX_EXAMPLES]
    keys = (
        "vehicle_id",
        "frame_before",
        "frame_after",
        "missing_frames",
        "distance_ft",
        "implied_speed_ft_s",
        "dims_same",
    )
    return [
        {k: cont.gaps[k][i] for k in keys}
        | {"rows": [cont.gaps["row_before"][i], cont.gaps["row_after"][i]]}
        for i in idx
    ]


def check_continuity(cont: Continuity) -> Check:
    cls = cont.gaps.get("class", np.empty(0, str))
    miss = cont.gaps.get("missing_frames", np.empty(0, np.int64))
    by_class = {}
    for c in ("dropped", "ambiguous", "reuse"):
        m = cls == c
        by_class[c] = {
            "count": int(m.sum()),
            "missing_frames": _stats(miss[m].astype(float)),
            "examples": _gap_examples(cont, m),
        }
    n_seg = int(cont.seg_vid.size)
    ev = {
        "consecutive_same_id_pairs": cont.n_pairs,
        "step_1_pairs": cont.n_step1,
        "step_0_pairs (same frame twice)": cont.n_step0,
        "step_0_examples": cont.step0_examples,
        "gaps_total": int(cls.size),
        "gap_classes": by_class,
        "classification_rule": (
            f"reuse: dimensions change or implied speed across the gap > {MAX_SPEED_FT_S} ft/s; "
            f"dropped: same dimensions, plausible speed, <= {SHORT_GAP_FRAMES} frames apart; "
            "ambiguous: same dimensions, plausible speed, longer gap"
        ),
        "trajectory_segments": n_seg,
        "segments_starting_at_first_frame (window truncation)": int(cont.starts[0]) if n_seg else 0,
        "segments_ending_at_last_frame (window truncation)": int(cont.ends[-1]) if n_seg else 0,
    }
    nd, na, nr = (by_class[c]["count"] for c in ("dropped", "ambiguous", "reuse"))
    text = (
        f"{cont.n_step1:,} of {cont.n_pairs:,} same-id steps are +1 frame; gaps: {nd} dropped-"
        f"frame-like, {na} ambiguous, {nr} id-reuse-like; {cont.n_step0} repeated frames."
    )
    if cont.n_step0 or nd:
        return Check("per_vehicle_continuity", FAIL, text, ev)
    if na:
        return Check("per_vehicle_continuity", INVESTIGATE, text, ev)
    return Check("per_vehicle_continuity", PASS, text, ev)


def check_speeds(cont: Continuity) -> Check:
    sp = cont.speed_ft_s
    fast = sp > MAX_SPEED_FT_S
    top = np.argsort(-sp, kind="stable")[: min(MAX_EXAMPLES, int(fast.sum()))]
    ev = {
        "threshold_ft_s": MAX_SPEED_FT_S,
        "speed_ft_s": _stats(sp),
        "pairs_above_threshold": int(fast.sum()),
        "vehicle_ids_above_threshold": int(np.unique(cont.speed_vid[fast]).size),
        "fastest_examples": [
            {
                "vehicle_id": cont.speed_vid[i],
                "frame_id": cont.speed_frame[i],
                "speed_ft_s": sp[i],
                "rows": cont.speed_rows[i],
            }
            for i in top
        ],
    }
    if fast.any():
        return Check(
            "implied_speed",
            INVESTIGATE,
            f"{int(fast.sum())} consecutive-frame steps imply > {MAX_SPEED_FT_S:g} ft/s "
            f"(max {sp.max():.1f} ft/s).",
            ev,
        )
    mx = f"{sp.max():.1f}" if sp.size else "n/a"
    return Check(
        "implied_speed",
        PASS,
        f"No consecutive-frame step implies > {MAX_SPEED_FT_S:g} ft/s (max {mx} ft/s).",
        ev,
    )


def check_vehicle_ids(rows: Rows, cont: Continuity, expected: int | None) -> Check:
    vid = rows["vehicle_id"]
    ids = np.unique(vid)
    triples = np.unique(np.stack([vid, rows["v_length"], rows["v_width"]]), axis=1)
    dims_per_id = np.unique(triples[0].astype(np.int64), return_counts=True)
    changing = dims_per_id[0][dims_per_id[1] > 1]
    seg_ids, seg_counts = np.unique(cont.seg_vid, return_counts=True)
    multi = seg_ids[seg_counts > 1]
    within = np.unique(cont.seg_vid[~cont.seg_dims_const])
    across_only = np.setdiff1d(changing, within)
    reuse_gap_ids = (
        np.unique(cont.gaps["vehicle_id"][cont.gaps["class"] == "reuse"])
        if cont.gaps
        else np.empty(0, np.int64)
    )
    ev = {
        "distinct_vehicle_ids": int(ids.size),
        "expected_distinct_server_count": expected,
        "ids_with_more_than_one_segment": int(multi.size),
        "ids_with_changing_dimensions": int(changing.size),
        "ids_with_dimension_change_inside_a_segment": int(within.size),
        "ids_with_dimension_change_only_between_segments": int(across_only.size),
        "ids_with_a_reuse_like_gap": int(reuse_gap_ids.size),
        "examples_changing_dimensions": changing[:MAX_EXAMPLES],
        "examples_change_inside_segment": within[:MAX_EXAMPLES],
    }
    if expected is not None and ids.size != expected:
        return Check(
            "vehicle_ids",
            FAIL,
            f"{ids.size:,} distinct vehicle_id values; server reported {expected:,}.",
            ev,
        )
    server = f"{expected:,}" if expected is not None else "n/a"
    text = (
        f"{ids.size:,} distinct ids (server {server}); {multi.size} ids have >1 trajectory "
        f"segment; dimensions change for {changing.size} ids ({within.size} inside a segment)."
    )
    if within.size:
        return Check("vehicle_ids", INVESTIGATE, text, ev)
    return Check("vehicle_ids", PASS, text, ev)


# ---------------------------------------------------------------- pagination


def _boundary(
    table: Table, cont: Continuity, b: int, k: int, end_max_elsewhere: int
) -> dict[str, Any]:
    v = table.values
    out: dict[str, Any] = {"k": k, "row_before": b - 1, "row_after": b}
    if not (table.valid[b - 1] and table.valid[b]):
        out |= {"status": INVESTIGATE, "note": "a row next to the boundary did not parse"}
        return out

    def key(i: int) -> dict[str, Any]:
        return {
            "global_time": v["global_time"][i],
            "clock_pdt": clock_pdt(v["global_time"][i]),
            "vehicle_id": v["vehicle_id"][i],
            "frame_id": v["frame_id"][i],
        }

    t1, t2 = int(v["global_time"][b - 1]), int(v["global_time"][b])
    v1, v2 = int(v["vehicle_id"][b - 1]), int(v["vehicle_id"][b])
    f1, f2 = int(v["frame_id"][b - 1]), int(v["frame_id"][b])
    ordered = (t1, v1) < (t2, v2)
    lo, hi = max(0, b - BOUNDARY_WINDOW_ROWS), min(table.n_rows, b + BOUNDARY_WINDOW_ROWS)
    ok = table.valid

    def keyset(a: int, z: int) -> set[tuple[int, int]]:
        m = ok[a:z]
        return set(
            zip(v["global_time"][a:z][m].tolist(), v["vehicle_id"][a:z][m].tolist(), strict=True)
        )

    straddle = len(keyset(lo, b) & keyset(b, hi))
    holes: list[int] = []
    endpoint: list[int] = []
    candidates = 0
    if ordered and f1 <= f2:
        # Any row lost between these two adjacent rows has a key strictly between them: frame f
        # in [f1, f2] with an id in the open interval below (offset paging, strict order).
        for f in range(f1, f2 + 1):
            lo_id = v1 if f == f1 else cont.vid_min - 1
            hi_id = v2 if f == f2 else cont.vid_min + cont.vid_span
            prev = cont.ids_in_frame(f - 1, lo_id, hi_id)
            nxt = cont.ids_in_frame(f + 1, lo_id, hi_id)
            ids = np.union1d(prev, nxt)
            candidates += int(ids.size)
            both = np.intersect1d(prev, nxt)
            both = both[~cont.is_present(f, both)]
            holes += both.tolist()
            endpoint += np.setxor1d(prev, nxt).tolist()
    spans: Counter[str] = Counter()
    if cont.gaps:
        for f in {f1, f2}:
            m = (cont.gaps["frame_before"] < f) & (cont.gaps["frame_after"] > f)
            spans.update(cont.gaps["class"][m].tolist())
    i0 = f1 - cont.frame_min
    at = int(cont.present[i0]) if 0 <= i0 < cont.present.size else 0
    ends = int(cont.ends[i0]) if 0 <= i0 < cont.ends.size else 0
    # On the last frame every trajectory ends (window truncation), so no comparison there.
    unusual_ends = f1 != cont.frame_max and ends > end_max_elsewhere
    out |= {
        "before": key(b - 1),
        "after": key(b),
        "splits_a_frame": t1 == t2,
        "key_tie_across_boundary": (t1, v1) == (t2, v2),
        "strictly_ordered_across_boundary": ordered,
        f"keys_on_both_sides_within_{BOUNDARY_WINDOW_ROWS}_rows": straddle,
        "omission_test": {
            "frames": list(range(f1, f2 + 1)) if f1 <= f2 else [],
            "vehicle_id_open_interval": [v1, v2],
            "ids_in_interval_at_neighbouring_frames": candidates,
            "one_frame_holes": holes,
            "endpoint_adjacent_ids": endpoint,
        },
        "continuation_at_frame_before": {
            "frame_id": f1,
            "vehicles": at,
            "continue_next_frame": at - ends,
            "end_here": ends,
            "end_count_above_max_elsewhere": unusual_ends,
        },
        "gaps_spanning_boundary_frames": dict(spans),
    }
    bad_gap = spans.get("dropped", 0) + spans.get("ambiguous", 0)
    if not ordered or straddle or holes or bad_gap:
        out["status"] = FAIL
    elif unusual_ends:
        out["status"] = INVESTIGATE
    else:
        out["status"] = PASS
    return out


def check_page_boundaries(table: Table, cont: Continuity, page_size: int) -> Check:
    n = table.n_rows
    bounds = list(range(page_size, n, page_size))
    n_pages = -(-n // page_size) if n else 0
    boundary_frames = {
        int(table.values["frame_id"][i]) for b in bounds for i in (b - 1, b) if table.valid[i]
    }
    if cont.ends.size > 1:
        idx = np.arange(cont.ends.size - 1)  # the last frame ends every trajectory
        keep = ~np.isin(idx + cont.frame_min, list(boundary_frames))
        elsewhere = cont.ends[idx[keep]]
    else:
        elsewhere = np.empty(0, np.int64)
    end_max = int(elsewhere.max()) if elsewhere.size else 0
    entries = [_boundary(table, cont, b, k, end_max) for k, b in enumerate(bounds, start=1)]
    statuses = [e["status"] for e in entries]
    n_holes = sum(len(e.get("omission_test", {}).get("one_frame_holes", [])) for e in entries)
    n_end = sum(len(e.get("omission_test", {}).get("endpoint_adjacent_ids", [])) for e in entries)
    n_cand = sum(
        e.get("omission_test", {}).get("ids_in_interval_at_neighbouring_frames", 0) for e in entries
    )
    n_tie = sum(bool(e.get("key_tie_across_boundary")) for e in entries)
    straddle_key = f"keys_on_both_sides_within_{BOUNDARY_WINDOW_ROWS}_rows"
    n_straddle = sum(e.get(straddle_key, 0) for e in entries)
    ev = {
        "page_size": page_size,
        "pages": n_pages,
        "rows_in_last_page": n - (n_pages - 1) * page_size if n_pages else 0,
        "boundaries": len(bounds),
        "boundaries_splitting_a_frame": sum(bool(e.get("splits_a_frame")) for e in entries),
        "total_key_ties_across_boundaries": n_tie,
        "total_keys_on_both_sides": n_straddle,
        "total_ids_in_boundary_key_intervals": n_cand,
        "total_one_frame_holes_at_boundaries": n_holes,
        "total_endpoint_adjacent_candidates": n_end,
        "trajectory_ends_per_frame_elsewhere": _stats(elsewhere.astype(float)),
        "per_boundary": entries,
    }
    status = _worst(statuses)
    text = (
        f"{len(bounds)} boundaries: {n_tie} key ties, {n_straddle} keys on both sides, "
        f"{n_holes} one-frame holes; {n_end} endpoint-adjacent ids where a lost row would not "
        "show."
    )
    return Check("pagination_boundaries", status, text, ev)


def check_server_completeness(by: dict[str, Check]) -> Check:
    pb = by["pagination_boundaries"].evidence
    inv = by["invalid_numerics"].evidence
    ev = {
        "not_checkable_here": [
            "row-for-row equality with the server's result set (no server-side checksum or "
            "per-page fingerprint was recorded)",
            "omission of rows that are trajectory end/start points next to a page boundary "
            f"({pb.get('total_endpoint_adjacent_candidates')} candidate ids in total)",
            f"that the dataset did not change between the {pb.get('pages')} page requests (one "
            "retried after a connection reset); the server counts were taken before the "
            "download, not per page",
            "the location='lankershim' filter (the location column was not downloaded)",
        ],
        "indirect_local_evidence": {
            "global_x_extent_ft": inv.get("global_x_extent_ft"),
            "global_y_extent_ft": inv.get("global_y_extent_ft"),
        },
    }
    return Check(
        "server_completeness",
        NOT_LOCAL,
        "Whether the file is exactly the server's filtered result set needs the server.",
        ev,
    )


# ---------------------------------------------------------------- answers


def compose_answers(by: dict[str, Check]) -> dict[str, Any]:
    """Answers (a) and (b), built only from numbers in the checks."""
    rc = by["record_count"].evidence
    keys = by["duplicate_keys"].evidence
    exact = by["exact_duplicate_rows"].evidence
    order = by["global_ordering"].evidence
    pb = by["pagination_boundaries"].evidence
    tw = by["time_window"].evidence
    cont = by["per_vehicle_continuity"].evidence
    vids = by["vehicle_ids"].evidence
    inv_ev = by["invalid_numerics"].evidence
    n = rc["data_rows"]
    exp_rows = rc["expected_rows_server_count"]
    dup = keys["duplicate_key_extra_rows"]
    inv, ties = order.get("inversions", 0), order.get("ties", 0)
    n_ids, exp_ids = vids["distinct_vehicle_ids"], vids["expected_distinct_server_count"]
    if vids["ids_with_more_than_one_segment"] == 0:
        reuse = (
            f"Within this file each of the {n_ids:,} vehicle_ids is one contiguous trajectory "
            "(no reuse observed); the codebook's warning that ids repeat concerns the full "
            "dataset, and reuse across time would not break (global_time, vehicle_id) "
            "uniqueness unless two vehicles shared an id at the same instant."
        )
    else:
        reuse = (
            f"{vids['ids_with_more_than_one_segment']} vehicle_ids have more than one "
            "trajectory segment (possible reuse), so vehicle_id alone is not a vehicle identity."
        )
    if dup == 0 and inv == 0 and ties == 0:
        a = (
            f"Yes. The {n:,} rows carry {keys['distinct_keys']:,} distinct (global_time, "
            "vehicle_id) keys (0 repeats) and the file is strictly increasing on that key with "
            "vehicle_id compared as an integer (0 ties, 0 inversions over "
            f"{order['adjacent_pairs']:,} adjacent pairs). So the key is a unique total order "
            "for this data and the server's $order had no ties it could break differently "
            "between page requests. This is shown for the rows in the file; it carries over to "
            f"the server's result set as far as (b) shows the file is that set. {reuse}"
        )
    else:
        a = (
            f"No. {dup} rows repeat a (global_time, vehicle_id) key, and the file has {inv} "
            f"inversions and {ties} ties on it, so the key is not a unique total order here."
        )
    dropped = cont["gap_classes"]["dropped"]["count"]
    gaps_total = cont["gaps_total"]
    cand = pb["total_ids_in_boundary_key_intervals"]
    holes = pb["total_one_frame_holes_at_boundaries"]
    endp = pb["total_endpoint_adjacent_candidates"]
    page, pages = pb["page_size"], pb["pages"]
    established = [
        f"No duplication: {exact['extra_copies']} exact duplicate rows and {dup} repeated "
        f"(global_time, vehicle_id) keys in the whole file; {pb['total_keys_on_both_sides']} "
        f"keys appear on both sides of any of the {pb['boundaries']} page boundaries (within "
        f"{BOUNDARY_WINDOW_ROWS} rows).",
        f"Boundaries: the two rows at each boundary are in strict key order "
        f"({pb['total_key_ties_across_boundaries']} ties), so any row lost there has a key "
        "strictly between them (that frame, a vehicle_id between the two ids). Vehicles with "
        f"such ids at the neighbouring frames: {cand} in total; {holes} present on both sides "
        f"but missing at the boundary (a one-frame hole = a detected loss); {endp} with a "
        "trajectory start/end next to the boundary (where a lost row would not show).",
        f"Per-vehicle continuity: {gaps_total} frame gaps in "
        f"{cont['consecutive_same_id_pairs']:,} same-id steps ({dropped} dropped-frame-like), so "
        "no mid-trajectory row is missing anywhere in the file.",
        f"{tw.get('rows_outside_window')} rows lie outside the query window.",
    ]

    def num(x: Any) -> str:
        return f"{x:,}" if isinstance(x, int) else "n/a"

    with_server_counts = [
        f"Rows: {n:,} in the file vs {num(exp_rows)} counted by the server before download. A "
        "matching count alone does not prove completeness: a lost row plus a duplicated row "
        "elsewhere keeps it (duplicates are ruled out above), and so would a lost row plus a "
        "foreign or altered row.",
    ]
    if exp_rows == n and pages > 1:
        with_server_counts.append(
            f"Every request was capped at $limit = {page:,} and page {pages} started at offset "
            f"{(pages - 1) * page:,}, so {n:,} rows is reachable only if pages 1-{pages - 1} "
            f"each returned exactly {page:,} rows: the page boundaries are exactly at rows "
            f"{page:,} x k, as tested."
        )
    with_server_counts.append(
        f"Distinct vehicle_ids: {n_ids:,} in the file vs {num(exp_ids)} on the server, so "
        f"{'no vehicle was' if n_ids == exp_ids else 'it is not shown that no vehicle was'} "
        f"lost entirely. Given {cand} "
        "candidate ids at the boundaries, a lost boundary row would have to be a vehicle's "
        "only row (excluded by this count when it matches) or a row cut off from its "
        "trajectory by a source frame gap exactly at the boundary (the file has "
        f"{gaps_total} frame gaps anywhere)."
    )
    not_verifiable = [
        "Row-for-row equality with the server: no server-side checksum or per-page "
        "fingerprint was recorded, so a lost row offset by a foreign or altered row is not "
        "detectable here.",
        f"That the server data did not change between the count queries and the {pages} page "
        "requests (one retried after a connection reset).",
        "The location='lankershim' filter: the location column was not downloaded; the "
        f"global_x/global_y extent ({inv_ev.get('global_x_extent_ft') or 0:.0f} x "
        f"{inv_ev.get('global_y_extent_ft') or 0:.0f} ft) is indirect evidence only.",
    ]
    no_dups = exact["extra_copies"] == 0 and dup == 0 and pb["total_keys_on_both_sides"] == 0
    no_holes = (
        holes == 0
        and pb["total_key_ties_across_boundaries"] == 0
        and by["pagination_boundaries"].status != FAIL
        and dropped == 0
    )
    counts_ok = n == exp_rows and n_ids == exp_ids
    if no_dups and no_holes and counts_ok:
        verdict = (
            "Duplication between pages: ruled out by local evidence alone. Omission between "
            "pages: not provable from the file alone, and none is detectable; together with the "
            "server's pre-download counts, every page was full and a boundary row can only be "
            f"missing if the server data changed between requests, a loss was offset by a "
            f"foreign or altered row, or a source frame gap coincides with a boundary ({endp} "
            "endpoint-adjacent candidates). Those residual cases are NOT VERIFIABLE LOCALLY."
        )
    else:
        verdict = (
            "Local evidence shows a problem between pages or in the counts: duplicates "
            f"{'absent' if no_dups else 'present'}, holes/ties/dropped frames "
            f"{'absent' if no_holes else 'present'}, server counts "
            f"{'match' if counts_ok else 'do not match or have no reference'}."
        )
    return {
        "a_unique_total_order": a,
        "b_pages_complete": {
            "verdict": verdict,
            "established_locally": established,
            "established_with_recorded_server_counts": with_server_counts,
            "not_verifiable_locally": not_verifiable,
        },
    }


# ---------------------------------------------------------------- orchestration


@dataclass(frozen=True)
class Audit:
    checks: list[Check]
    answers: dict[str, Any]
    scan: ByteScan | None


def run_audit(path: Path, exp: Expectations, sums_file: Path | None = None) -> Audit:
    """Run every check on `path` (read-only) against `exp`."""
    exists = check_file_exists(path)
    if exists.status != PASS:
        return Audit([exists], {}, None)
    scan = scan_bytes(path)
    table = read_table(path)
    rows = valid_rows(table)
    cont = analyse_continuity(rows)
    checks = [
        exists,
        check_byte_size(scan, exp.size_bytes),
        check_sha256(scan, exp.sha256, read_sums_entry(sums_file, path)),
        check_line_endings(scan),
        check_record_count(table, scan, exp.n_rows),
        check_field_structure(table),
        check_header(table, exp.header),
        check_column_types(table),
        check_missing_values(table),
        check_invalid_numerics(table, rows),
        check_time_window(rows, exp.time_lo_ms, exp.time_hi_ms),
        check_time_granularity(rows),
        check_frame_time(rows),
        *(check_code_domain(rows, d) for d in CODEBOOK),
        check_location_codes(rows),
        check_vehicle_ids(rows, cont, exp.n_vehicle_ids),
        check_exact_duplicates(rows),
        check_duplicate_keys(rows),
        check_ordering(rows),
        check_continuity(cont),
        check_speeds(cont),
        check_page_boundaries(table, cont, exp.page_size),
    ]
    by = {c.name: c for c in checks}
    checks.append(check_server_completeness(by))
    by["server_completeness"] = checks[-1]
    return Audit(checks, compose_answers(by), scan)


def build_report(audit: Audit, path: Path, exp: Expectations) -> dict[str, Any]:
    script = Path(__file__).resolve()
    return to_jsonable(
        {
            "schema": REPORT_SCHEMA,
            "script": _display_path(script),
            "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
            "run": {
                "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "python": platform.python_version(),
                "numpy": np.__version__,
            },
            "input": {
                "path": _display_path(path),
                "size_bytes": audit.scan.size_bytes if audit.scan else None,
                "sha256": audit.scan.sha256 if audit.scan else None,
            },
            "expected": {
                "size_bytes": exp.size_bytes,
                "sha256": exp.sha256,
                "rows": exp.n_rows,
                "distinct_vehicle_ids": exp.n_vehicle_ids,
                "time_window_ms": [exp.time_lo_ms, exp.time_hi_ms],
                "page_size": exp.page_size,
                "source": "docs/DATA_REGISTER.md section 13 (recorded at download; the server "
                "counts are count-only queries made before the download)",
            },
            "conventions": {
                "row": "0-based data-row index; file line = row + 2",
                "page": "page k (1-based) held rows [(k-1)*page_size, k*page_size)",
                "units": "global_x/global_y US survey ft (CA State Plane, front centre); "
                "v_length/v_width ft; global_time epoch ms; frame_id 0.1 s; speeds ft/s",
                "clock": "PDT (UTC-7)",
            },
            "thresholds": {
                "max_speed_ft_s": MAX_SPEED_FT_S,
                "short_gap_frames": SHORT_GAP_FRAMES,
                "v_length_plausible_ft": LENGTH_RANGE_FT,
                "v_width_plausible_ft": WIDTH_RANGE_FT,
                "boundary_window_rows": BOUNDARY_WINDOW_ROWS,
            },
            "status_counts": dict(Counter(c.status for c in audit.checks)),
            "checks": [c.as_json() for c in audit.checks],
            "answers": audit.answers,
        }
    )


def format_table(checks: Sequence[Check]) -> str:
    w = max(len(c.name) for c in checks)
    lines = [f"{'check':<{w}}  {'status':<23}  explanation", "-" * (w + 2 + 23 + 2 + 60)]
    lines += [f"{c.name:<{w}}  {c.status:<23}  {c.explanation}" for c in checks]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, exp: Expectations = NGSIM_0830) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--sums", type=Path, default=None, help="default: SHA256SUMS beside input")
    parser.add_argument("--page-size", type=int, default=exp.page_size)
    parser.add_argument("--force", action="store_true", help="overwrite an existing report")
    args = parser.parse_args(argv)
    if args.output.exists() and not args.force:
        print(f"refusing to overwrite {args.output} (use --force)", file=sys.stderr)
        return 2
    exp = replace(exp, page_size=args.page_size)
    sums = args.sums if args.sums is not None else args.input.parent / "SHA256SUMS"
    audit = run_audit(args.input, exp, sums)
    report = build_report(audit, args.input, exp)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    print(format_table(audit.checks))
    print(f"\nstatus counts: {report['status_counts']}")
    print(f"report: {_display_path(args.output)}")
    return 1 if any(c.status == FAIL for c in audit.checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
