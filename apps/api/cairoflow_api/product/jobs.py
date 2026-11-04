"""Video-analysis jobs: a real subprocess worker, one job at a time, the others queued.

Nothing here does vision work or invents progress: every figure a job shows is a line its worker
wrote. A job that has not reported yet has no progress at all.

The worker protocol (cairoflow-job/1)
-------------------------------------
The API runs `<CAIROFLOW_VISION_PYTHON> scripts/cv_run.py --job-dir <dir>` with the repository root
as working directory, no shell and no stdin, in its own process group; stdout and stderr go to
`<dir>/worker.log`.

Input, `<dir>/job.json`, written before the worker starts:

    protocol         "cairoflow-job/1"
    job_id           the job's id, also the directory name
    created_at_utc   ISO 8601
    source_id        the catalogue id (camera-2, clip-<name>, upload-<name>)
    source_kind      camera_source, clip or upload
    source_title     for display
    video_path       absolute path of the video to read
    calibrated       true only when the camera 2 calibration applies to this video
    source_start_s   where second 0 of this file sits in the camera 2 source: 0 for the camera 2
                     file, the trim start for a clip whose source hash proves it was cut from it,
                     null otherwise. Not null makes the held-out block rule apply to the window.
    start_s          first second to analyse, in seconds of this file
    duration_s       seconds to analyse
    frame_stride     analyse every Nth frame (1 = every frame)
    analyses_dir     absolute path of data/analyses
    repo_root        absolute path of the repository

Output, all inside `<dir>` unless stated:

    progress.jsonl   appended, one JSON object per line, flushed per line, fields: stage (string),
                     frame, frames_total, fps, detections, tracks, elapsed_s, message. A field the
                     worker does not know is left out, never guessed.
    preview.jpg      the latest annotated frame, replaced atomically (write a temporary file in
                     the same directory, then os.replace).

On success the worker writes the analysis to `<analyses_dir>/<analysis_id>/` with
cairoflow_perception.analysis_store.write_analysis, appends
`{"stage": "done", "analysis_id": "<analysis_id>"}` and exits 0. Writing to a temporary directory
beside it and renaming keeps a killed worker from leaving half an analysis.

On failure it appends `{"stage": "failed", "error": "<reason>"}` and exits non-zero.

SIGTERM means stop: exit promptly. After `cancel_grace_s` the process group gets SIGKILL.

How a job ends (the API decides, from what is on disk):

    succeeded    a done line and an analysis that passes verify_analysis, with exit 0 or, when the
                 API had asked the worker to stop, any exit: the work was already finished
    failed       a failed line; or the worker exited without a done or failed line; or a done line
                 with a non-zero exit nobody asked for; or an analysis that does not verify; or a
                 fault in the API itself, which stops the worker and carries on with the queue
    cancelled    DELETE /jobs/{id} stopped it before it reported done, or it was still queued
    interrupted  the API stopped or restarted while it ran, before it reported done

The API writes `<dir>/result.json` when a job ends and `<dir>/worker.pid` when it starts, so jobs
survive the loss of the database; on boot a job still marked running is interrupted, and if its
worker outlived the API it is stopped first.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cairoflow_api.product.index import ProductIndex
from cairoflow_api.product.settings import ProductSettings
from cairoflow_api.product.sources import Source

LOGGER = logging.getLogger(__name__)
JOB_PROTOCOL = "cairoflow-job/1"
JOB_FILE = "job.json"
PROGRESS_FILE = "progress.jsonl"
PREVIEW_FILE = "preview.jpg"
RESULT_FILE = "result.json"
PID_FILE = "worker.pid"
LOG_FILE = "worker.log"
TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")
ANALYSIS_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
INTERRUPTED = "The server stopped while this job was running. No analysis was recorded for it."
TAIL_BYTES = 16384
HEARTBEAT_S = 15.0


class JobRequest(BaseModel):
    """POST /jobs. Every field is required: an analysis window is never assumed."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=300)
    start_s: float = Field(ge=0, allow_inf_nan=False)
    duration_s: float = Field(gt=0, allow_inf_nan=False)
    frame_stride: int = Field(ge=1, le=1000)


class JobConflict(ValueError):
    """The job is not in a state that allows the request."""


def worker_problem(settings: ProductSettings) -> str | None:
    """What stops a job from running, as instructions a person can follow; None when nothing."""
    python, worker = settings.vision_python, settings.worker_script
    if not python.is_file() or not os.access(python, os.X_OK):
        return (
            f"The vision environment is missing: there is no Python at {python}. Create it with "
            f"`python3.12 -m venv {python.parent.parent} && {python.parent}/pip install "
            "'pydantic>=2' numpy opencv-python-headless`, plus what the detector in "
            "data/cv/detector.json needs, or set CAIROFLOW_VISION_PYTHON to an interpreter that "
            "has them. Then retry."
        )
    if not worker.is_file():
        return (
            f"The vision worker is missing: {worker} does not exist. Restore it, or set "
            "CAIROFLOW_VISION_WORKER to the worker script. Then retry."
        )
    return None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    temporary.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _no_constant(name: str) -> None:
    raise ValueError(f"{name} is not JSON")


def parse_line(text: str) -> dict[str, Any] | None:
    """One progress line, or None when it is not a JSON object with a stage."""
    try:
        value = json.loads(text, parse_constant=_no_constant)
    except ValueError:
        return None
    if isinstance(value, dict) and isinstance(value.get("stage"), str):
        return value
    return None


class ProgressReader:
    """Reads the complete lines appended to progress.jsonl since the last call. A line number
    counts every complete line, so it can resume a Server-Sent Events stream."""

    def __init__(self, path: Path, after_line: int = 0) -> None:
        self._path = path
        self._offset = 0
        self._line = 0
        self._after = after_line

    def read(self) -> list[tuple[int, dict[str, Any]]]:
        try:
            with self._path.open("rb") as handle:
                handle.seek(self._offset)
                data = handle.read()
        except OSError:
            return []
        complete = data[: data.rfind(b"\n") + 1]
        self._offset += len(complete)
        found = []
        for raw in complete.splitlines():
            self._line += 1
            parsed = parse_line(raw.decode("utf-8", "replace"))
            if parsed is not None and self._line > self._after:
                found.append((self._line, parsed))
        return found


def last_progress(path: Path) -> dict[str, Any] | None:
    """The latest complete progress line, from the file's tail."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - TAIL_BYTES))
            data = handle.read()
    except OSError:
        return None
    for raw in reversed(data[: data.rfind(b"\n") + 1].splitlines()):
        parsed = parse_line(raw.decode("utf-8", "replace"))
        if parsed is not None:
            return parsed
    return None


def _exit_text(code: int) -> str:
    return f"signal {-code}" if code < 0 else f"code {code}"


def _terminate(process: subprocess.Popen, grace_s: float) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


class JobManager:
    def __init__(self, settings: ProductSettings, index: ProductIndex) -> None:
        self._settings = settings
        self._index = index
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._cancelling: set[str] = set()
        self._thread: threading.Thread | None = None

    def job_dir(self, job_id: str) -> Path:
        return self._settings.jobs_dir / job_id

    # --- lifecycle ----------------------------------------------------------------------------

    def recover(self) -> None:
        """Bring the table back in line with var/jobs, then close what was running."""
        self._settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        for directory in sorted(p for p in self._settings.jobs_dir.iterdir() if p.is_dir()):
            if self._index.job(directory.name) is None:
                self._adopt(directory)
        for job in self._index.jobs("running"):
            self._stop_orphan(job["job_id"])
            self._close(job["job_id"], "running", "interrupted", error=INTERRUPTED)

    def _adopt(self, directory: Path) -> None:
        """Rebuild one job's row from its files; without them the directory is not a job."""
        spec = _read_json(directory / JOB_FILE)
        if spec is None or spec.get("job_id") != directory.name:
            return
        result = _read_json(directory / RESULT_FILE) or {}
        started = (directory / PID_FILE).is_file()
        self._index.insert_job(
            {
                "job_id": directory.name,
                "source_id": spec["source_id"],
                "start_s": spec["start_s"],
                "duration_s": spec["duration_s"],
                "frame_stride": spec["frame_stride"],
                "status": result.get("status") or ("running" if started else "queued"),
                "created_at_utc": spec["created_at_utc"],
                "started_at_utc": result.get("started_at_utc"),
                "finished_at_utc": result.get("finished_at_utc"),
                "analysis_id": result.get("analysis_id"),
                "error": result.get("error"),
            }
        )

    def _stop_orphan(self, job_id: str) -> None:
        """A worker that outlived the server would write into a job now marked interrupted. Only
        a process whose command line names this job directory is touched."""
        directory = self.job_dir(job_id)
        try:
            pid = int((directory / PID_FILE).read_text("utf-8").strip())
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
        except (OSError, ValueError):
            return
        if str(directory).encode() not in command:
            return
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + self._settings.cancel_grace_s
            while time.monotonic() < deadline and Path(f"/proc/{pid}").exists():
                time.sleep(0.05)
            if Path(f"/proc/{pid}").exists():
                os.killpg(pid, signal.SIGKILL)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="cairoflow-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=self._settings.cancel_grace_s + 5)

    # --- requests -----------------------------------------------------------------------------

    def submit(self, request: JobRequest, source: Source, video: Path) -> dict[str, Any]:
        settings = self._settings
        created = datetime.now(UTC)
        job_id = f"job-{created:%Y%m%dt%H%M%Sz}-{uuid.uuid4().hex[:8]}"
        created_at = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        directory = self.job_dir(job_id)
        directory.mkdir(parents=True)
        (directory / PROGRESS_FILE).touch()
        _write_json(
            directory / JOB_FILE,
            {
                "protocol": JOB_PROTOCOL,
                "job_id": job_id,
                "created_at_utc": created_at,
                "source_id": source.source_id,
                "source_kind": source.kind,
                "source_title": source.title,
                "video_path": str(video),
                "calibrated": source.calibrated,
                "source_start_s": source.source_start_s,
                "start_s": request.start_s,
                "duration_s": request.duration_s,
                "frame_stride": request.frame_stride,
                "analyses_dir": str(settings.analyses_dir),
                "repo_root": str(settings.repo_root),
            },
        )
        self._index.insert_job(
            {
                "job_id": job_id,
                "source_id": source.source_id,
                "start_s": request.start_s,
                "duration_s": request.duration_s,
                "frame_stride": request.frame_stride,
                "status": "queued",
                "created_at_utc": created_at,
            }
        )
        self._wake.set()
        return self._index.job(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a queued job now, or ask the worker thread to stop a running one."""
        job = self._index.job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"] == "queued" and self._close(job_id, "queued", "cancelled"):
            return self._index.job(job_id)
        job = self._index.job(job_id)
        if job["status"] != "running":
            raise JobConflict(f"the job is already {job['status']}")
        with self._lock:
            self._cancelling.add(job_id)
        return job

    def describe(self, job: dict[str, Any]) -> dict[str, Any]:
        directory = self.job_dir(job["job_id"])
        with self._lock:
            cancelling = job["job_id"] in self._cancelling
        return {
            "id": job["job_id"],
            "source_id": job["source_id"],
            "start_s": job["start_s"],
            "duration_s": job["duration_s"],
            "frame_stride": job["frame_stride"],
            "status": job["status"],
            "cancelling": cancelling and job["status"] == "running",
            "created_at": job["created_at_utc"],
            "started_at": job["started_at_utc"],
            "finished_at": job["finished_at_utc"],
            "analysis_id": job["analysis_id"],
            "error": job["error"],
            "progress": last_progress(directory / PROGRESS_FILE),
            "has_preview": (directory / PREVIEW_FILE).is_file(),
        }

    async def events(self, job_id: str, after_line: int = 0) -> AsyncIterator[str]:
        """Server-Sent Events: `status` when the state changes, `progress` for each worker line
        in order, then the final `status` and an `end` carrying the finished job."""
        reader = ProgressReader(self.job_dir(job_id) / PROGRESS_FILE, after_line)
        sent = None
        beat = time.monotonic()
        while True:
            job = await asyncio.to_thread(self._index.job, job_id)
            lines = await asyncio.to_thread(reader.read)  # after the row: an ended job has no more
            finished = job["status"] in TERMINAL
            if not finished and job["status"] != sent:
                sent = job["status"]
                yield _event("status", {"status": sent})
            for number, line in lines:
                yield _event("progress", line, event_id=number)
            if finished:
                yield _event("status", {"status": job["status"]})
                described = await asyncio.to_thread(self.describe, job)
                yield _event("end", described)
                return
            if time.monotonic() - beat >= HEARTBEAT_S:
                beat = time.monotonic()
                yield ": keep-alive\n\n"
            await asyncio.sleep(self._settings.job_poll_s)

    # --- the worker thread --------------------------------------------------------------------

    def _loop(self) -> None:
        """The only thread that runs jobs, so no fault may end it: what it could not finish is
        closed as failed, and the jobs behind that one still run."""
        fault: str | None = None
        while not self._stopping.is_set():
            try:
                if fault is not None:
                    self._close_stranded(fault)
                    fault = None
                job = self._index.next_queued_job()
                if job is not None:
                    self._run(job["job_id"])
                    continue
            except Exception as error:
                LOGGER.exception("The job thread hit a fault and carries on")
                fault = self._fault_text(error)
            self._wake.wait(timeout=1.0)
            self._wake.clear()

    def _fault_text(self, error: Exception) -> str:
        detail = str(error).replace(f"{self._settings.repo_root}/", "")
        return f"The server could not finish this job: {type(error).__name__}: {detail}"

    def _close_stranded(self, reason: str) -> None:
        """After a fault, a job still marked running has no one watching it: `_run` has returned
        and nothing else runs jobs. Retried from the loop until the database takes it."""
        for job in self._index.jobs("running"):
            self._close(job["job_id"], "running", "failed", error=reason)

    def _run(self, job_id: str) -> None:
        settings = self._settings
        problem = worker_problem(settings)
        if problem:  # it was there when the job was accepted
            self._close(job_id, "queued", "failed", error=problem)
            return
        if not self._index.move_job(
            job_id, expected="queued", status="running", started_at_utc=_now()
        ):
            return  # cancelled while it was the next in line
        directory = self.job_dir(job_id)
        try:
            with (directory / LOG_FILE).open("ab") as log:
                process = subprocess.Popen(
                    [
                        str(settings.vision_python),
                        str(settings.worker_script),
                        "--job-dir",
                        str(directory),
                    ],
                    cwd=settings.repo_root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as error:
            self._close(job_id, "running", "failed", error=f"The worker did not start: {error}")
            return
        try:
            (directory / PID_FILE).write_text(f"{process.pid}\n", encoding="utf-8")
            stopped_as = self._watch(job_id, process)
            self._settle(job_id, process.returncode, stopped_as)
        finally:
            if process.poll() is None:  # a fault got here first; nothing would watch this worker
                _terminate(process, settings.cancel_grace_s)
            with self._lock:
                self._cancelling.discard(job_id)

    def _watch(self, job_id: str, process: subprocess.Popen) -> str | None:
        """Wait for the worker. Returns what stopped it, when it did not stop by itself."""
        while True:
            try:
                process.wait(timeout=self._settings.job_poll_s)
            except subprocess.TimeoutExpired:
                pass
            with self._lock:
                cancelling = job_id in self._cancelling
            stopped_as = (
                "cancelled" if cancelling else "interrupted" if self._stopping.is_set() else None
            )
            if process.returncode is not None:
                return stopped_as
            if stopped_as:
                _terminate(process, self._settings.cancel_grace_s)
                return stopped_as

    def _settle(self, job_id: str, code: int, stopped_as: str | None) -> None:
        """Decide how a job ended from its exit code and what its worker wrote."""
        directory = self.job_dir(job_id)
        reader = ProgressReader(directory / PROGRESS_FILE)
        ended = [line for _, line in reader.read() if line["stage"] in ("done", "failed")]
        final = ended[-1] if ended else None
        reported_done = final is not None and final["stage"] == "done"
        if stopped_as and not reported_done:
            error = INTERRUPTED if stopped_as == "interrupted" else None
            self._close(job_id, "running", stopped_as, error=error)
        elif final is None:
            log = directory.relative_to(self._settings.repo_root) / LOG_FILE
            self._close(
                job_id,
                "running",
                "failed",
                error=(
                    f"The worker exited with {_exit_text(code)} without reporting done or failed. "
                    f"See {log}."
                ),
            )
        elif final["stage"] == "failed":
            reason = final.get("error")
            reason = reason if isinstance(reason, str) and reason else "no reason was given"
            self._close(job_id, "running", "failed", error=f"The worker failed: {reason}")
        elif code != 0 and not stopped_as:  # after a stop, the exit code is only our own signal
            self._close(
                job_id,
                "running",
                "failed",
                error=f"The worker reported done but exited with {_exit_text(code)}.",
            )
        else:
            self._accept(job_id, final.get("analysis_id"))

    def _accept(self, job_id: str, analysis_id: Any) -> None:
        """A done line is a claim: the analysis must exist and verify."""
        if not isinstance(analysis_id, str) or not ANALYSIS_ID.fullmatch(analysis_id):
            self._close(
                job_id, "running", "failed", error="The worker reported done with no valid id."
            )
            return
        directory = self._settings.analyses_dir / analysis_id
        if not directory.is_dir():
            self._close(
                job_id,
                "running",
                "failed",
                error=f"The worker reported {analysis_id}, which does not exist.",
            )
            return
        row = self._index.add_analysis(directory)
        if not row.valid:
            self._close(
                job_id,
                "running",
                "failed",
                error=f"Analysis {analysis_id} failed verification: {row.invalid_reason}",
            )
            return
        self._close(job_id, "running", "succeeded", analysis_id=analysis_id)

    def _close(
        self,
        job_id: str,
        expected: str,
        status: str,
        *,
        error: str | None = None,
        analysis_id: str | None = None,
    ) -> bool:
        finished_at = _now()
        if not self._index.move_job(
            job_id,
            expected=expected,
            status=status,
            finished_at_utc=finished_at,
            error=error,
            analysis_id=analysis_id,
        ):
            return False
        job = self._index.job(job_id)
        _write_json(
            self.job_dir(job_id) / RESULT_FILE,
            {
                "status": status,
                "started_at_utc": job["started_at_utc"],
                "finished_at_utc": finished_at,
                "analysis_id": analysis_id,
                "error": error,
            },
        )
        return True


def _event(name: str, data: dict[str, Any], *, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {name}\ndata: {json.dumps(data)}\n\n"
