"""Read-only FastAPI app serving pipeline runs: report pages, raw reports, overlay videos.

    uv run --frozen python -m cairoflow_api.app RUNS_ROOT [--host 127.0.0.1] [--port 8000] \
        [--demo data/demo/ngsim-int2-replay-timeline-v2.json] [--repo-root .]

A run is a directory directly under RUNS_ROOT that holds pipeline_record.json. Run names are
allow-listed (letters, digits, '.', '_', '-') and must resolve to a direct child of RUNS_ROOT,
so no request can reach another path. Only fixed file names inside a run are served. Responses
carry a strict Content-Security-Policy (no scripts) and no business logic runs here: the page is
a view of files the pipeline already wrote.

With `--demo`, `/demo` shows the demo page (cairoflow_api.demo): the files its manifest pins,
under the repository root, each checked against its SHA-256 on every request; a mismatch shows
an error page (409) instead. The pinned paths come from the manifest, never from a request.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from cairoflow_api.demo import SAM_OVERLAY, DemoError, load_demo, read_manifest
from cairoflow_api.flow import DEFAULT_LAYERS, ENTITY, ZOOMS, clean_layers, load_world_view
from cairoflow_api.flow_html import render_flow_error, render_flow_page
from cairoflow_api.html import render_demo_error, render_demo_page, render_index, render_run_page
from cairoflow_api.hypotheses import attached_details, stored_citations_of_run
from cairoflow_api.meta_depth import (
    load_meta_depth,
    render_frame_panel,
    render_pin_problem,
    render_recurrence_panel,
)
from cairoflow_api.world import (
    camera_record,
    load_decision_trace,
    load_inspector,
    load_problem,
)
from cairoflow_api.world_html import (
    render_decision_trace,
    render_problem,
    render_snapshot_missing,
    render_world_entity,
    render_world_index,
    render_world_not_found,
    render_world_problem,
)
from cairoflow_contracts.report import DecisionReport
from cairoflow_evaluation.pipeline import OVERLAY_FILE, RECORD_FILE, PipelineRecord

RUN_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
SAM_VIDEO = "sam-overlay.mp4"  # the demo's step 0 video, served from its pin only
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; media-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def create_app(
    runs_root: Path, demo_manifest: Path | None = None, repo_root: Path | None = None
) -> FastAPI:
    root = runs_root.resolve()
    repo = (repo_root or Path.cwd()).resolve()
    app = FastAPI(title="CairoFlow", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    def run_dir(name: str) -> Path:
        if not RUN_NAME.fullmatch(name):
            raise HTTPException(status_code=404)
        path = (root / name).resolve()
        if path.parent != root or not (path / RECORD_FILE).is_file():
            raise HTTPException(status_code=404)
        return path

    def record_of(path: Path) -> PipelineRecord:
        return PipelineRecord.model_validate_json((path / RECORD_FILE).read_text("utf-8"))

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        runs = [
            (path.name, record_of(path))
            for path in sorted(root.iterdir())
            if path.is_dir() and RUN_NAME.fullmatch(path.name) and (path / RECORD_FILE).is_file()
        ]
        return render_index(
            runs,
            demo_url="/demo" if demo_manifest else None,
            flow_url="/flow" if demo_manifest else None,
        )

    @app.get("/demo", response_class=HTMLResponse)
    def demo_page() -> HTMLResponse:
        if demo_manifest is None:
            raise HTTPException(status_code=404)
        try:
            demo = load_demo(read_manifest(demo_manifest), repo)
        except DemoError as error:
            return HTMLResponse(render_demo_error(str(error)), status_code=409)
        video = f"/demo/{SAM_VIDEO}" if demo.has_overlay else None
        run = demo.manifest.run_dir
        served = bool(run and RUN_NAME.fullmatch(run) and (root / run / RECORD_FILE).is_file())
        # sidecar pins, checked on every request like the manifest
        meta = load_meta_depth(repo, demo.manifest.absent_allowed)
        after_sam = render_pin_problem(meta) if meta.problem else render_frame_panel(meta)
        page = render_demo_page(
            demo,
            run_url=f"/runs/{run}/" if served else None,
            sam_video_url=video,
            after_sam=after_sam,
            after_proposals="" if meta.problem else render_recurrence_panel(meta),
        )
        return HTMLResponse(page)

    @app.get(f"/demo/{SAM_VIDEO}")
    def demo_sam_video() -> FileResponse:
        """The pinned SAM overlay (CC BY-SA 4.0), served only after every pin checks out."""
        if demo_manifest is None:
            raise HTTPException(status_code=404)
        try:
            demo = load_demo(read_manifest(demo_manifest), repo)
        except DemoError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not demo.has_overlay:
            raise HTTPException(status_code=404)
        return FileResponse(repo / demo.manifest.files[SAM_OVERLAY].path, media_type="video/mp4")

    @app.get("/flow", response_class=HTMLResponse)
    def flow_page(
        e: str | None = None, layers: str = DEFAULT_LAYERS, zoom: str = "core"
    ) -> HTMLResponse:
        """Intersection to decision on the demo's pinned records; `e` selects an entity."""
        if demo_manifest is None:
            raise HTTPException(status_code=404)
        try:
            demo = load_demo(read_manifest(demo_manifest), repo)
        except DemoError as error:
            return HTMLResponse(render_flow_error(str(error)), status_code=409)
        selected = e if e is not None and len(e) < 200 and ENTITY.fullmatch(e) else None
        video = f"/demo/{SAM_VIDEO}" if demo.has_overlay else None
        page = render_flow_page(
            demo,
            load_world_view(demo, repo),
            selected,
            layers=clean_layers(layers),
            zoom=zoom if zoom in ZOOMS else "core",
            video_url=video,
            requested_entity=e,  # shown back (escaped, cut short) when it is not in the view
            requested_zoom=zoom,
        )
        return HTMLResponse(page)

    def pinned_demo():
        """The demo's pinned records, or the 409 page naming the pin that does not match."""
        if demo_manifest is None:
            raise HTTPException(status_code=404)
        try:
            return load_demo(read_manifest(demo_manifest), repo), None
        except DemoError as error:
            return None, HTMLResponse(render_flow_error(str(error)), status_code=409)

    @app.get("/world", response_class=HTMLResponse)
    def world_index() -> HTMLResponse:
        """Every WorldView entity, one link each, with the snapshot's hashes and the camera's
        typed break; without the snapshot, one line."""
        demo, refused = pinned_demo()
        if refused is not None:
            return refused
        inspector = load_inspector(demo, repo)
        if inspector.missing_snapshot:
            return HTMLResponse(render_snapshot_missing("CairoFlow World Model"))
        if inspector.view is None:
            return HTMLResponse(render_world_problem("CairoFlow World Model", inspector.problem))
        return HTMLResponse(render_world_index(demo, inspector, camera_record(repo)))

    @app.get("/world/{entity_id:path}", response_class=HTMLResponse)
    def world_entity(entity_id: str) -> HTMLResponse:
        """One entity of the snapshot, by its id (`/world/lane%3AS1`)."""
        demo, refused = pinned_demo()
        if refused is not None:
            return refused
        inspector = load_inspector(demo, repo)
        if inspector.missing_snapshot:
            return HTMLResponse(render_snapshot_missing("CairoFlow World Model"))
        if inspector.view is None:
            return HTMLResponse(render_world_problem("CairoFlow World Model", inspector.problem))
        wanted = entity_id if len(entity_id) < 200 and ENTITY.fullmatch(entity_id) else None
        entity = next((e for e in inspector.view.entities if e.entity_id == wanted), None)
        if entity is None:
            return HTMLResponse(render_world_not_found(), status_code=404)
        return HTMLResponse(render_world_entity(demo, inspector, entity))

    @app.get("/problem", response_class=HTMLResponse)
    def problem_page() -> HTMLResponse:
        """The S-approach ProblemStatement, projected from its records (world.load_problem)."""
        _, refused = pinned_demo()
        if refused is not None:
            return refused
        problem, error = load_problem(repo)
        details = attached_details(problem.hypotheses, repo) if problem else {}
        return HTMLResponse(
            render_problem(problem, error, details), status_code=200 if problem else 409
        )

    @app.get("/decision/trace", response_class=HTMLResponse)
    def decision_trace() -> HTMLResponse:
        """The recorded decision, walked back through its experiment to the source hash."""
        demo, refused = pinned_demo()
        if refused is not None:
            return refused
        return HTMLResponse(render_decision_trace(load_decision_trace(demo, repo)))

    @app.get("/runs/{name}/", response_class=HTMLResponse)
    def run_page(name: str) -> str:
        path = run_dir(name)
        record = record_of(path)
        report = DecisionReport.model_validate_json((path / "report.json").read_text("utf-8"))
        has_overlay = OVERLAY_FILE in record.review_artifacts and (path / OVERLAY_FILE).is_file()
        return render_run_page(
            name,
            report,
            record,
            overlay_url=f"/runs/{name}/{OVERLAY_FILE}" if has_overlay else None,
            stored=stored_citations_of_run(path, record.artifacts, report.hypotheses),
        )

    @app.get("/runs/{name}/report.json")
    def report_json(name: str) -> FileResponse:
        return FileResponse(run_dir(name) / "report.json", media_type="application/json")

    @app.get("/runs/{name}/report.md")
    def report_md(name: str) -> FileResponse:
        return FileResponse(run_dir(name) / "report.md", media_type="text/markdown; charset=utf-8")

    @app.get(f"/runs/{{name}}/{OVERLAY_FILE}")
    def overlay(name: str) -> FileResponse:
        path = run_dir(name)
        if (
            OVERLAY_FILE not in record_of(path).review_artifacts
            or not (path / OVERLAY_FILE).is_file()
        ):
            raise HTTPException(status_code=404)
        return FileResponse(path / OVERLAY_FILE, media_type="video/mp4")

    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Serve CairoFlow pipeline runs (read-only).")
    parser.add_argument("runs_root", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo", type=Path, default=None, help="demo manifest to serve at /demo")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="where pins resolve")
    args = parser.parse_args(argv)
    app = create_app(args.runs_root, args.demo, args.repo_root)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
