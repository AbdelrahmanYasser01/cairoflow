"""The product server: /api/v1, the built web app, then the legacy report pages.

    python -m cairoflow_api.serve --port 8787 [--host 127.0.0.1] [--repo-root .]

Routes are tried in order: /api/v1, the web app's own paths and /assets, and last the legacy app
from cairoflow_api.app, mounted at "/" so /demo, /flow, /world, /problem, /decision/trace and /runs
keep their paths. Its own index at "/" is the one thing the web app takes over.

The web app and the API get a strict Content-Security-Policy. The legacy app sets its own, stricter
one; headers here are only ever added when a response has none, so that one survives. The legacy
app's demo manifest and runs directory are optional: on a fresh clone its pages say what is missing.
"""

from __future__ import annotations

import argparse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cairoflow_api.app import create_app
from cairoflow_api.product.routes import ProductServices, include_product_api
from cairoflow_api.product.settings import ProductSettings

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PORT = 8787
DEFAULT_DEMO = "data/demo/ngsim-int2-no-footage.json"
DEFAULT_RUNS = "experiments/runs"
SHUTDOWN_GRACE_S = 5
SPA_PATHS = ("/", "/intersections", "/vision", "/twin", "/analytics", "/evidence", "/_design")
PRODUCT_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; "
    "worker-src 'self' blob:"
)
PRODUCT_HEADERS = {
    "Content-Security-Policy": PRODUCT_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
NOT_BUILT = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>CairoFlow: web app not built</title></head>
<body>
<h1>The CairoFlow web app has not been built</h1>
<p>Build it once, then reload this page:</p>
<pre>cd apps/web
npm install
npm run build</pre>
<p>The API is already running at <a href="/api/v1/health">/api/v1/health</a>, and the report pages
at <a href="/demo">/demo</a>.</p>
</body>
</html>
"""


class BuiltAssets(StaticFiles):
    """/assets of the web build. A fresh clone has none: its requests answer 404, not 500."""

    async def check_config(self) -> None:
        return None


class ProductHeaders:
    """Pure ASGI, so it never buffers a stream: adds a header only where a response has none."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in PRODUCT_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_product_app(
    settings: ProductSettings,
    runs_root: Path | None = None,
    demo_manifest: Path | None = None,
) -> FastAPI:
    services = ProductServices.create(settings)
    repo = settings.repo_root
    dist = settings.dist_dir

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        services.boot()
        yield
        services.shutdown()

    app = FastAPI(
        title="CairoFlow", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.services = services
    app.add_middleware(ProductHeaders)
    include_product_api(app, services)

    def spa_page() -> Response:
        page = dist / "index.html"
        if not page.is_file():
            return HTMLResponse(NOT_BUILT)
        return FileResponse(page, media_type="text/html", headers={"Cache-Control": "no-cache"})

    def favicon() -> Response:
        icon = dist / "favicon.svg"
        if not icon.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(icon, media_type="image/svg+xml")

    for path in SPA_PATHS:
        app.add_api_route(path, spa_page, methods=["GET", "HEAD"], include_in_schema=False)
    app.add_api_route("/favicon.svg", favicon, methods=["GET", "HEAD"], include_in_schema=False)
    app.mount("/assets", BuiltAssets(directory=dist / "assets", check_dir=False), name="assets")
    manifest = demo_manifest or repo / DEFAULT_DEMO
    legacy = create_app(
        runs_root or repo / DEFAULT_RUNS, manifest if manifest.is_file() else None, repo
    )
    app.mount("/", legacy, name="legacy")  # last: everything above keeps its paths
    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Serve the CairoFlow product.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--runs-root", type=Path, default=None, help="legacy report runs")
    parser.add_argument("--demo", type=Path, default=None, help="legacy demo manifest")
    args = parser.parse_args(argv)
    settings = ProductSettings.from_env(args.repo_root)
    app = create_product_app(settings, args.runs_root, args.demo)
    uvicorn.run(app, host=args.host, port=args.port, timeout_graceful_shutdown=SHUTDOWN_GRACE_S)


if __name__ == "__main__":
    main()
