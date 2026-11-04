"""The /api/v1 routes: a view of files on disk, and the entry points for uploads and jobs.

Client input never becomes a path. An analysis id, a job id and a source id are looked up in the
index or the catalogue, which hold the paths; an artifact role is looked up in the record, and the
path that gives must stay inside the analysis directory. Business rules live in the modules this
one calls; every figure in a response comes from a stored record or from a worker's own report.
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import importlib.metadata
import json
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from cairoflow_api.product.index import AnalysisRow, ProductIndex
from cairoflow_api.product.jobs import JobConflict, JobManager, JobRequest, worker_problem
from cairoflow_api.product.settings import SITE_ID, ProductSettings, ignored_directory
from cairoflow_api.product.sources import Source, SourceCatalog, UploadRefused
from cairoflow_contracts.cv_diagnosis import CvDiagnosisRecord
from cairoflow_contracts.decision_summary import DecisionSummary
from cairoflow_perception.analysis_store import (
    RECORD_FILE,
    TRACKS_FILE,
    TRAJECTORIES_FILE,
    load_record,
)

JSON = "application/json"
BINARY = "application/octet-stream"
VIDEO_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".webm": "video/webm"}
SITE_NAME_STATUS = "supported_hypothesis"
SITE_NAME_SOURCE = "docs/DATA_REGISTER.md §14.3"
DIAGNOSES_DIR = "data/sample/diagnoses"
GZIP_CACHE_ENTRIES = 16
GZIP_LEVEL = 6


class ApiProblem(Exception):
    """An error response with fields beside `detail`, such as a rebuild hint."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
        **fields: Any,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.headers = headers
        self.body = {"detail": detail, **fields}


class GzipFiles:
    """Gzipped copies of files that do not change, kept for the next request."""

    def __init__(self, max_entries: int = GZIP_CACHE_ENTRIES) -> None:
        self._entries: OrderedDict[tuple[str, int, int], bytes] = OrderedDict()
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def read(self, path: Path) -> bytes:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key]
        data = gzip.compress(path.read_bytes(), compresslevel=GZIP_LEVEL, mtime=0)
        with self._lock:
            self._entries[key] = data
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
        return data


def accepts_gzip(accept_encoding: str) -> bool:
    for part in accept_encoding.split(","):
        coding, _, parameter = part.partition(";")
        if coding.strip().lower() != "gzip":
            continue
        name, _, value = parameter.partition("=")
        if name.strip().lower() != "q":
            return True
        try:
            return float(value) > 0
        except ValueError:
            return False
    return False


class ServerLock:
    """One server per var/. A second one would close the first one's running job as interrupted,
    stop its worker, and break the rule of one job at a time."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: TextIO | None = None

    def acquire(self) -> None:
        handle = self._path.open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise RuntimeError(
                f"Another CairoFlow server is using {self._path.parent}. Stop it first, or give "
                "this one another --repo-root."
            ) from error
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


@dataclass(frozen=True)
class ProductServices:
    settings: ProductSettings
    index: ProductIndex
    jobs: JobManager
    catalog: SourceCatalog
    gzip_files: GzipFiles
    lock: ServerLock

    @classmethod
    def create(cls, settings: ProductSettings) -> ProductServices:
        roots = (settings.sample_analyses_dir, settings.analyses_dir)
        index = ProductIndex(settings.db_path, roots, settings.repo_root)
        return cls(
            settings=settings,
            index=index,
            jobs=JobManager(settings, index),
            catalog=SourceCatalog(settings),
            gzip_files=GzipFiles(),
            lock=ServerLock(settings.var_dir / "server.lock"),
        )

    def boot(self) -> None:
        ignored_directory(self.settings.var_dir)
        self.lock.acquire()
        self.index.open()
        self.index.rebuild_analyses()
        self.catalog.clean_parts()
        self.jobs.recover()
        self.jobs.start()

    def shutdown(self) -> None:
        self.jobs.stop()
        self.lock.release()


def app_version() -> str:
    try:
        return importlib.metadata.version("cairoflow")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def analysis_summary(row: AnalysisRow) -> dict[str, Any]:
    return {
        "id": row.analysis_id,
        "title": row.title,
        "mode": row.mode,
        "data_origin": row.data_origin,
        "site_id": row.site_id,
        "frame_count": row.frame_count,
        "n_tracks": row.n_tracks,
        "created_at": row.created_at_utc,
        "valid": row.valid,
        "invalid_reason": row.invalid_reason,
    }


def source_view(source: Source) -> dict[str, Any]:
    url = f"/api/v1/sources/{source.source_id}/video" if source.playable else None
    return {**source.model_dump(), "video_url": url}


def create_router(services: ProductServices) -> APIRouter:
    router = APIRouter()
    settings, index, jobs = services.settings, services.index, services.jobs
    catalog = services.catalog

    def read(path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """A route that answers GET, and HEAD with the same headers and no body."""
        return router.api_route(path, methods=["GET", "HEAD"])

    def negotiated(request: Request, path: Path, media_type: str) -> Response:
        """A file's bytes, gzipped only for a client that accepts it."""
        headers = {"Vary": "Accept-Encoding"}
        if accepts_gzip(request.headers.get("accept-encoding", "")):
            headers["Content-Encoding"] = "gzip"
            return Response(services.gzip_files.read(path), media_type=media_type, headers=headers)
        return Response(path.read_bytes(), media_type=media_type, headers=headers)

    def valid_analysis(analysis_id: str) -> AnalysisRow:
        row = index.analysis(analysis_id)
        if row is None:
            raise ApiProblem(404, f"no analysis {analysis_id}")
        if not row.valid:
            raise ApiProblem(
                409,
                f"analysis {analysis_id} failed verification and is not served",
                invalid_reason=row.invalid_reason,
            )
        return row

    def known_job(job_id: str) -> dict[str, Any]:
        job = index.job(job_id)
        if job is None:
            raise ApiProblem(404, f"no job {job_id}")
        return job

    def stored_summary(path: Path, script: str) -> Path:
        if not path.is_file():
            raise ApiProblem(404, f"{path.name} has not been built; run {script}")
        return path

    @read("/health")
    def health() -> dict[str, Any]:
        problem = worker_problem(settings)
        return {
            "ok": True,
            "version": app_version(),
            "vision_worker_available": problem is None,
            "vision_worker_problem": problem,
        }

    @read("/analyses")
    def analyses() -> dict[str, Any]:
        return {"analyses": [analysis_summary(row) for row in index.analyses()]}

    @read("/analyses/{analysis_id}")
    def analysis_record(analysis_id: str, request: Request) -> Response:
        row = valid_analysis(analysis_id)
        return negotiated(request, row.directory / RECORD_FILE, JSON)

    @read("/analyses/{analysis_id}/tracks")
    def analysis_tracks(analysis_id: str, request: Request) -> Response:
        row = valid_analysis(analysis_id)
        return negotiated(request, row.directory / TRACKS_FILE, JSON)

    @read("/analyses/{analysis_id}/trajectories")
    def analysis_trajectories(analysis_id: str, request: Request) -> Response:
        row = valid_analysis(analysis_id)
        return negotiated(request, row.directory / TRAJECTORIES_FILE, BINARY)

    @read("/analyses/{analysis_id}/artifacts/{role}")
    def analysis_artifact(analysis_id: str, role: str) -> Response:
        row = valid_analysis(analysis_id)
        record = load_record(row.directory)
        artifact = next((a for a in record.artifacts if a.role == role), None)
        if artifact is None:
            raise ApiProblem(404, f"analysis {analysis_id} lists no {role} artifact")
        root = row.directory.resolve()
        path = (root / artifact.path).resolve()
        if not path.is_relative_to(root):
            raise ApiProblem(404, f"analysis {analysis_id} lists no {role} artifact")
        if not path.is_file():
            raise ApiProblem(
                404,
                f"the {role} artifact of {analysis_id} is not on this machine",
                role=role,
                bundled=artifact.bundled,
                rebuild_hint=artifact.rebuild_hint,
                licence_note=artifact.licence_note,
            )
        return FileResponse(path, media_type=artifact.media_type)

    @read("/analyses/{analysis_id}/diagnosis")
    def analysis_diagnosis(analysis_id: str, request: Request) -> Response:
        """Model text about one analysis, served only beside the exact record it read."""
        row = valid_analysis(analysis_id)
        path = settings.repo_root / DIAGNOSES_DIR / f"{row.analysis_id}.json"
        if not path.is_file():
            raise ApiProblem(
                404,
                f"no diagnosis of {row.analysis_id} is stored",
                rebuild_hint=f"scripts/muse_cv_diagnosis.py <dir>/{row.analysis_id} --send",
            )
        try:
            diagnosis = CvDiagnosisRecord.model_validate_json(path.read_bytes())
        except ValidationError as error:
            raise ApiProblem(
                409,
                f"{path.name} does not match its contract; rebuild it with "
                "scripts/muse_cv_diagnosis.py",
                errors=error.error_count(),
            ) from error
        record_sha256 = hashlib.sha256((row.directory / RECORD_FILE).read_bytes()).hexdigest()
        if (diagnosis.analysis_id, diagnosis.analysis_record_sha256) != (
            row.analysis_id,
            record_sha256,
        ):
            raise ApiProblem(409, f"{path.name} was written about another record")
        return negotiated(request, path, JSON)

    @read("/site")
    def site() -> dict[str, Any]:
        path = stored_summary(settings.site_geometry, "scripts/build_ngsim_int2_scenario.py")
        geometry = json.loads(path.read_text("utf-8"))
        row = next((r for r in index.analyses() if r.valid and r.site_id == SITE_ID), None)
        record = load_record(row.directory) if row else None
        return {
            "site_id": SITE_ID,
            "site_name": record.source.site_name if record else None,
            "site_name_status": SITE_NAME_STATUS if record else None,
            "site_name_source": SITE_NAME_SOURCE if record else None,
            "geometry_id": geometry["geometry_id"],
            "ground_frame": geometry["ground_frame"],
            "drives_on": geometry["drives_on"],
            "lanes": geometry["lanes"],
            "gates": geometry["gates"],
            "calibration": (
                {
                    **record.calibration.model_dump(mode="json"),
                    "source_analysis_id": record.analysis_id,
                }
                if record
                else None
            ),
        }

    @read("/site/image-geometry")
    def image_geometry(request: Request) -> Response:
        path = stored_summary(settings.image_geometry, "the camera 2 geometry scripts")
        return negotiated(request, path, JSON)

    @read("/intersections")
    def intersections(request: Request) -> Response:
        path = stored_summary(settings.corridor_summary, "scripts/build_corridor_summary.py")
        return negotiated(request, path, JSON)

    @read("/decision")
    def decision(request: Request) -> Response:
        path = stored_summary(settings.decision_summary, "scripts/build_decision_summary.py")
        try:
            DecisionSummary.model_validate_json(path.read_bytes())
        except ValidationError as error:
            raise ApiProblem(
                409,
                "decision_summary.json does not match its contract; rebuild it with "
                "scripts/build_decision_summary.py",
                errors=error.error_count(),
            ) from error
        return negotiated(request, path, JSON)

    @read("/sources")
    def sources() -> dict[str, Any]:
        return {"sources": [source_view(source) for source in catalog.sources()]}

    @read("/sources/{source_id}/video")
    def source_video(source_id: str) -> Response:
        entry = catalog.resolve(source_id)
        if entry is None:
            raise ApiProblem(404, f"no source {source_id}")
        source, path = entry
        if not source.playable:
            raise ApiProblem(409, f"{source.file_name} cannot be played in a browser")
        return FileResponse(path, media_type=VIDEO_TYPES.get(path.suffix.lower(), BINARY))

    @router.put("/uploads/{name:path}", status_code=201)
    async def upload(name: str, request: Request) -> dict[str, Any]:
        declared = request.headers.get("content-length", "")
        try:
            source = await catalog.save_upload(
                name, request.stream(), int(declared) if declared.isdigit() else None
            )
        except UploadRefused as refused:
            raise ApiProblem(refused.status_code, refused.detail) from refused
        except ClientDisconnect as error:
            raise ApiProblem(400, "the client disconnected during the upload") from error
        return source_view(source)

    @router.post("/jobs", status_code=202)
    def create_job(body: JobRequest) -> dict[str, Any]:
        problem = worker_problem(settings)
        if problem:
            raise ApiProblem(503, problem)
        entry = catalog.resolve(body.source_id)
        if entry is None:
            raise ApiProblem(404, f"no source {body.source_id}")
        source, path = entry
        if source.problem is not None or source.duration_s is None:
            raise ApiProblem(422, f"{source.title} cannot be analysed: {source.problem}")
        if body.start_s + body.duration_s > source.duration_s:
            raise ApiProblem(
                422,
                f"the window ends at {body.start_s + body.duration_s} s; "
                f"{source.file_name} lasts {source.duration_s} s",
            )
        return jobs.describe(jobs.submit(body, source, path))

    @read("/jobs")
    def list_jobs() -> dict[str, Any]:
        return {"jobs": [jobs.describe(job) for job in index.jobs()]}

    @read("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        return jobs.describe(known_job(job_id))

    @router.delete("/jobs/{job_id}", status_code=202)
    def cancel_job(job_id: str) -> dict[str, Any]:
        known_job(job_id)
        try:
            return jobs.describe(jobs.cancel(job_id))
        except JobConflict as conflict:
            raise ApiProblem(409, str(conflict)) from conflict

    @router.get("/jobs/{job_id}/events")  # no HEAD: it would run the stream to its end
    def job_events(
        job_id: str, last_event_id: str | None = Header(default=None)
    ) -> StreamingResponse:
        known_job(job_id)
        after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
        return StreamingResponse(
            jobs.events(job_id, after),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @read("/jobs/{job_id}/preview.jpg")
    def job_preview(job_id: str) -> Response:
        known_job(job_id)
        path = jobs.job_dir(job_id) / "preview.jpg"
        if not path.is_file():
            raise ApiProblem(404, "this job has no preview yet")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @router.api_route(
        "/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"], status_code=404
    )
    def unknown(rest: str, request: Request) -> None:
        """Reached by what no route took: a path nothing serves, or the wrong method for one."""
        allowed = {
            method
            for route in router.routes
            if isinstance(route, APIRoute)
            and route.endpoint is not unknown
            and route.path_regex.match(f"/{rest}")
            for method in route.methods
        }
        if allowed:
            raise ApiProblem(
                405,
                f"/api/v1/{rest} does not answer {request.method}",
                headers={"Allow": ", ".join(sorted(allowed))},
            )
        raise ApiProblem(404, f"no route /api/v1/{rest}")

    return router


def include_product_api(app: FastAPI, services: ProductServices) -> None:
    """The routes under /api/v1, with their error shape. Register before anything mounted at /."""

    async def problem_response(_: Request, problem: Exception) -> JSONResponse:
        assert isinstance(problem, ApiProblem)
        return JSONResponse(
            status_code=problem.status_code, content=problem.body, headers=problem.headers
        )

    app.add_exception_handler(ApiProblem, problem_response)
    app.include_router(create_router(services), prefix="/api/v1")
