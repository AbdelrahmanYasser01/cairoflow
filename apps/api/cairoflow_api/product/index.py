"""The SQLite index (stdlib sqlite3, var/cairoflow.db): a rebuildable view of files on disk.

Files stay the source of truth. Deleting the database loses nothing: analyses are re-read from
their directories on every boot, and jobs are re-read from var/jobs (cairoflow_api.product.jobs).

- `analyses`: one row per analysis directory. A directory that fails `verify_analysis` keeps a row
  with `valid` 0 and its reason, and holds no figure: nothing from a record that failed is shown.
- `analysis_minutes`: every bin of every `MetricSeries`, for querying. A bin with no value stays
  NULL, never zero.
- `jobs`: one row per job; see cairoflow_api.product.jobs for what the fields mean.

One connection per call, so the worker thread and request threads never share one.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from cairoflow_contracts.analysis_record import AnalysisRecord
from cairoflow_perception.analysis_store import load_tracks, verify_analysis

SCHEMA_VERSION = 1
MAX_REASON_CHARS = 400
SCHEMA = """
CREATE TABLE analyses (
    analysis_id TEXT PRIMARY KEY,
    directory TEXT NOT NULL,
    valid INTEGER NOT NULL,
    invalid_reason TEXT,
    title TEXT,
    mode TEXT,
    data_origin TEXT,
    site_id TEXT,
    frame_count INTEGER,
    n_tracks INTEGER,
    created_at_utc TEXT
);
CREATE TABLE analysis_minutes (
    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
    series_id TEXT NOT NULL,
    bin_index INTEGER NOT NULL,
    bin_start_s REAL NOT NULL,
    bin_s REAL NOT NULL,
    value REAL,
    unit TEXT NOT NULL,
    scope TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (analysis_id, series_id, bin_index)
);
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    start_s REAL NOT NULL,
    duration_s REAL NOT NULL,
    frame_stride INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    started_at_utc TEXT,
    finished_at_utc TEXT,
    analysis_id TEXT,
    error TEXT
);
"""


@dataclass(frozen=True)
class AnalysisRow:
    analysis_id: str
    directory: Path
    valid: bool
    invalid_reason: str | None = None
    title: str | None = None
    mode: str | None = None
    data_origin: str | None = None
    site_id: str | None = None
    frame_count: int | None = None
    n_tracks: int | None = None
    created_at_utc: str | None = None


class ProductIndex:
    def __init__(self, db_path: Path, analysis_roots: Sequence[Path], repo_root: Path) -> None:
        self._db_path = db_path
        self._roots = tuple(analysis_roots)
        self._repo_root = repo_root

    # --- connections and schema ---------------------------------------------------------------

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._db_path, timeout=30)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:  # commits on success, rolls back on an exception
                yield connection

    def open(self) -> None:
        """Make the schema current. A database of another version, or a broken one, is dropped:
        everything in it can be rebuilt from files."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connection() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = connection.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'table'"
                ).fetchone()[0]
        except sqlite3.DatabaseError:
            self._db_path.unlink(missing_ok=True)
            version, tables = 0, 0
        if version == SCHEMA_VERSION and tables:
            return
        self._db_path.unlink(missing_ok=True)
        with self._connection() as connection:
            connection.executescript(SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # --- analyses -----------------------------------------------------------------------------

    def rebuild_analyses(self) -> None:
        """Forget every analysis and read them again from their directories."""
        with self._connection() as connection:
            connection.execute("DELETE FROM analyses")
        for root in self._roots:
            if not root.is_dir():
                continue
            for directory in sorted(path for path in root.iterdir() if path.is_dir()):
                if not directory.name.startswith("."):  # a worker's unfinished staging directory
                    self.add_analysis(directory)

    def add_analysis(self, directory: Path) -> AnalysisRow:
        """Verify one directory and record it. The same id in two places is refused for both:
        the index cannot say which one a request means."""
        row, record = self._read(directory)
        with self._connection() as connection:
            found = connection.execute(
                "SELECT directory FROM analyses WHERE analysis_id = ?", (row.analysis_id,)
            ).fetchone()
            if found is not None and found["directory"] != str(directory):
                row = AnalysisRow(
                    row.analysis_id,
                    directory,
                    valid=False,
                    invalid_reason=f"analysis id {row.analysis_id} exists in two directories",
                )
                record = None
            connection.execute("DELETE FROM analyses WHERE analysis_id = ?", (row.analysis_id,))
            connection.execute(
                "INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row.analysis_id, str(row.directory), int(row.valid), row.invalid_reason,
                    row.title, row.mode, row.data_origin, row.site_id, row.frame_count,
                    row.n_tracks, row.created_at_utc,
                ),
            )  # fmt: skip
            if record is not None:
                connection.executemany(
                    "INSERT INTO analysis_minutes VALUES (?,?,?,?,?,?,?,?,?,?)",
                    list(_series_rows(record)),
                )
        return row

    def _read(self, directory: Path) -> tuple[AnalysisRow, AnalysisRecord | None]:
        try:
            record = verify_analysis(directory)
            if record.analysis_id != directory.name:
                raise ValueError(f"record says {record.analysis_id}, directory {directory.name}")
            for artifact in record.artifacts:
                parts = PurePosixPath(artifact.path)
                if parts.is_absolute() or ".." in parts.parts or "\\" in artifact.path:
                    raise ValueError(f"artifact path {artifact.path} leaves the analysis")
            tracks = load_tracks(directory)
            if tracks.analysis_id != record.analysis_id:
                raise ValueError(f"tracks.json belongs to {tracks.analysis_id}")
            binary = next(a for a in record.artifacts if a.role == "trajectories")
            if binary.sha256 != tracks.trajectories_sha256:
                raise ValueError("tracks.json was written for other trajectories")
        except json.JSONDecodeError as error:
            return self._invalid(directory, f"malformed JSON: {error}"), None
        except (ValueError, OSError) as error:  # pydantic's errors are ValueErrors
            return self._invalid(directory, str(error)), None
        row = AnalysisRow(
            analysis_id=record.analysis_id,
            directory=directory,
            valid=True,
            title=record.title,
            mode=record.mode,
            data_origin=record.data_origin,
            site_id=record.site_id,
            frame_count=record.frame_count,
            n_tracks=len(tracks.tracks),
            created_at_utc=record.created_at_utc.isoformat(),
        )
        return row, record

    def _invalid(self, directory: Path, reason: str) -> AnalysisRow:
        shown = reason.replace(f"{self._repo_root}/", "")[:MAX_REASON_CHARS]
        return AnalysisRow(directory.name, directory, valid=False, invalid_reason=shown)

    def analyses(self) -> list[AnalysisRow]:
        """Newest first; invalid analyses, which have no date, after them."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM analyses ORDER BY valid DESC, created_at_utc DESC, analysis_id"
            ).fetchall()
        return [_analysis_row(row) for row in rows]

    def analysis(self, analysis_id: str) -> AnalysisRow | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)
            ).fetchone()
        return None if row is None else _analysis_row(row)

    def series_bins(self, analysis_id: str, series_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM analysis_minutes WHERE analysis_id = ? AND series_id = ? "
                "ORDER BY bin_index",
                (analysis_id, series_id),
            ).fetchall()
        return [dict(row) for row in rows]

    # --- jobs ---------------------------------------------------------------------------------

    def insert_job(self, job: dict[str, Any]) -> None:
        """`seq` is worked out inside the INSERT: a writing statement takes the write lock before
        it reads, so two jobs submitted at the same moment cannot get the same place."""
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO jobs (job_id, seq, source_id, start_s, duration_s, frame_stride, "
                "status, created_at_utc, started_at_utc, finished_at_utc, analysis_id, error) "
                "VALUES (:job_id, (SELECT coalesce(max(seq), 0) + 1 FROM jobs), :source_id, "
                ":start_s, :duration_s, :frame_stride, :status, :created_at_utc, "
                ":started_at_utc, :finished_at_utc, :analysis_id, :error)",
                {
                    "started_at_utc": None, "finished_at_utc": None, "analysis_id": None,
                    "error": None, **job,
                },
            )  # fmt: skip

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return None if row is None else dict(row)

    def jobs(self, status: str | None = None) -> list[dict[str, Any]]:
        """Newest first, or in queue order for one status."""
        with self._connection() as connection:
            if status is None:
                rows = connection.execute("SELECT * FROM jobs ORDER BY seq DESC").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY seq", (status,)
                ).fetchall()
        return [dict(row) for row in rows]

    def next_queued_job(self) -> dict[str, Any] | None:
        queued = self.jobs("queued")
        return queued[0] if queued else None

    def move_job(self, job_id: str, *, expected: str, **fields: Any) -> bool:
        """Update a job only if it is still in `expected`: the one place a status changes, so a
        cancel and the worker thread cannot both win."""
        assignments = ", ".join(f"{name} = :{name}" for name in fields)
        with self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = :job_id AND status = :expected",
                {**fields, "job_id": job_id, "expected": expected},
            )
        return cursor.rowcount == 1


def _analysis_row(row: sqlite3.Row) -> AnalysisRow:
    return AnalysisRow(
        analysis_id=row["analysis_id"],
        directory=Path(row["directory"]),
        valid=bool(row["valid"]),
        invalid_reason=row["invalid_reason"],
        title=row["title"],
        mode=row["mode"],
        data_origin=row["data_origin"],
        site_id=row["site_id"],
        frame_count=row["frame_count"],
        n_tracks=row["n_tracks"],
        created_at_utc=row["created_at_utc"],
    )


def _series_rows(record: AnalysisRecord) -> Iterator[tuple]:
    for series in record.series:
        for bin_index, value in enumerate(series.values):
            yield (
                record.analysis_id, series.series_id, bin_index,
                series.start_s + bin_index * series.bin_s, series.bin_s, value, series.unit,
                series.scope, series.evidence_kind, series.status,
            )  # fmt: skip
