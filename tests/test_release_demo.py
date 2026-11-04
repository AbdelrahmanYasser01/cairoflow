"""The demo package: every URL of a walkthrough works in the server the launcher starts.

The URLs are scripts/demo_urls.txt, in order; scripts/demo_run.sh serves them all from
cairoflow_api.serve (the same demo manifest, the repository as root). Each report page is opened
twice: on this checkout, and on a tree of the Git-tracked files only, which is what a fresh clone
has (the Git-ignored WorldView snapshot is absent there, ADR-033 #9). The report pages are an
offline replay of committed records: nothing here calls a model, runs a simulation or writes into
the repository. The web app's pages and API are opened on that same tree.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from cairoflow_api.app import create_app
from cairoflow_api.product.settings import ProductSettings
from cairoflow_api.serve import create_product_app

REPO = Path(__file__).resolve().parents[1]
URLS_FILE = REPO / "scripts/demo_urls.txt"
LAUNCHER = REPO / "scripts/demo_run.sh"
MANIFEST_PATH = "data/demo/ngsim-int2-no-footage.json"  # the manifest the launcher serves
SNAPSHOT = "data/demo/ngsim-int2-worldview.json"  # Git-ignored (ADR-033 #9)
ABSENT_LINE = "WorldView snapshot not present - run scripts/build_worldview_snapshot.py"
EXISTING_PAGES = ("/demo", "/flow")
WEB_PAGES = ("/", "/intersections", "/vision", "/twin", "/analytics", "/evidence")
REFERENCE_ANALYSES = ("ref-ngsim-int2-960-1020", "ref-ngsim-int2-full")
INSPECTOR = ("/world", "/world/lane%3AS1", "/world/queue%3AS1")  # the WorldView inspector
# Any claim of immediacy: the demo is an offline replay, never real-time, live or streaming.
CLAIMS = re.compile(r"\breal[- ]?time\b|\blive\b|\bstream(?:ing|ed)\b", re.I)
FAILURES = ("Traceback (most recent call last)", "Internal Server Error")


def demo_urls() -> list[str]:
    """The URL paths of scripts/demo_urls.txt, in order ('#' starts a comment)."""
    lines = (raw.split("#", 1)[0].strip() for raw in URLS_FILE.read_text("utf-8").splitlines())
    return [line for line in lines if line]


URLS = demo_urls()
PRODUCT_URLS = [url for url in URLS if url in WEB_PAGES or url.startswith("/api/")]
REPORT_URLS = [url for url in URLS if url not in PRODUCT_URLS]


def text_of(page: str) -> str:
    """The visible text of a page: no style, no tags, whitespace collapsed."""
    body = re.sub(r"<style.*?</style>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", body))).strip()


def pinned(role: str) -> dict:
    return json.loads((REPO / MANIFEST_PATH).read_text("utf-8"))["files"][role]


PANEL = re.compile(r'<div class="panel"><h3>[^<]*</h3>\s*<p><code>([^<]*)</code>')
ZOOM_ON = re.compile(r'<a class="on" href="[^"]*">([^<]*)</a>')


def shown(page: str) -> dict[str, str]:
    """What a /flow page shows: the entity of its panel ('e') and the zoom marked on."""
    found = {}
    if panel := PANEL.search(page):
        found["e"] = unescape(panel.group(1))
    if (controls := re.search(r"View: (.*?)</p>", page, re.S)) and (
        zoom := ZOOM_ON.search(controls.group(1))
    ):
        found["zoom"] = unescape(zoom.group(1))
    return found


def view_problem(url: str, page: str, snapshot_present: bool) -> str | None:
    """Why the page does not show the entity and zoom its URL asks for, or None.

    The app answers 200 for any `e` or `zoom` and falls back to the default panel, so a typo in
    a recording URL is caught only here. Without the snapshot no panel is drawn at all.
    """
    asked = {key: values[-1] for key, values in parse_qs(urlsplit(url).query).items()}
    got = shown(page)
    for key in ("e", "zoom"):
        if key not in asked or (key not in got and not snapshot_present):
            continue
        if got.get(key) != asked[key]:
            return f"asked for {key}={asked[key]}, the page shows {got.get(key, 'no view')}"
    return None


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    runs = tmp_path_factory.mktemp("runs")
    return TestClient(create_app(runs, REPO / MANIFEST_PATH, REPO))


@pytest.fixture(scope="module")
def tracked_tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The Git-tracked files only, as links: what a fresh clone has."""
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a Git checkout: the tracked files are unknown")
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
    ).stdout.decode("utf-8")
    root = tmp_path_factory.mktemp("clone")
    for name in filter(None, listed.split("\0")):
        source = REPO / name
        if source.is_file():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).symlink_to(source)
    assert not (root / SNAPSHOT).exists()
    return root


@pytest.fixture(scope="module")
def fresh_clone(tracked_tree: Path, tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    """The report pages on the Git-tracked files only."""
    runs = tmp_path_factory.mktemp("runs")
    return TestClient(create_app(runs, tracked_tree / MANIFEST_PATH, tracked_tree))


@pytest.fixture(scope="module")
def product(tracked_tree: Path, tmp_path_factory: pytest.TempPathFactory):
    """The whole server on the Git-tracked files only; it boots, so var/ lands in that tree."""
    settings = ProductSettings.from_env(tracked_tree)
    runs = tmp_path_factory.mktemp("runs")
    app = create_product_app(settings, runs, tracked_tree / MANIFEST_PATH)
    with TestClient(app) as client:
        yield client


def test_the_url_list_is_local_paths_in_recording_order_with_the_existing_pages() -> None:
    assert len(URLS) == len(set(URLS)) >= 2
    assert all(url.startswith("/") and "://" not in url and " " not in url for url in URLS)
    assert all(page in URLS for page in (*EXISTING_PAGES, *WEB_PAGES, "/api/v1/health"))


IMMEDIACY = (
    "a live view of the junction", "Live demo", "streaming the junction now", "live traffic",
    "Live: camera 2", "in real time", "a real-time view", "realtime",
)  # fmt: skip
HONEST = (
    "an offline replay", "delivered", "alive", "olive", "now inside the observed interval",
    "single instants", "livestock",
)  # fmt: skip


def launcher_pattern() -> str:
    """The ERE `demo_run.sh --check` greps every page for (its BANNED variable)."""
    found = re.search(r"^BANNED='([^']+)'$", LAUNCHER.read_text("utf-8"), re.M)
    assert found, "demo_run.sh has no BANNED='...' pattern"
    return found.group(1)


@pytest.mark.parametrize(
    ("phrase", "banned"), [(p, True) for p in IMMEDIACY] + [(p, False) for p in HONEST]
)
def test_any_claim_of_immediacy_is_caught_by_the_test_and_the_launcher(phrase, banned) -> None:
    assert bool(CLAIMS.search(phrase)) is banned
    grep = subprocess.run(["grep", "-qiE", launcher_pattern()], input=phrase, text=True)
    assert (grep.returncode == 0) is banned


def launcher_pin() -> str:
    """The SHA-256 `demo_run.sh` requires of the Git-ignored World Model view."""
    found = re.search(r'^SNAPSHOT_SHA256="([0-9a-f]{64})"', LAUNCHER.read_text("utf-8"), re.M)
    assert found, "demo_run.sh pins no SNAPSHOT_SHA256"
    return found.group(1)


def test_the_world_model_view_here_is_the_one_the_launcher_pins() -> None:
    """The view is Git-ignored and the app checks only the hashes it declares: the pin is here."""
    if not (REPO / SNAPSHOT).exists():
        pytest.skip("the Git-ignored WorldView snapshot is not present")
    assert hashlib.sha256((REPO / SNAPSHOT).read_bytes()).hexdigest() == launcher_pin()


@pytest.mark.parametrize("mode", [["--check"], []])
def test_the_launcher_refuses_a_world_model_view_that_is_not_its_pin(tmp_path: Path, mode) -> None:
    """The World Model panels show the view's numbers: an edited view must not be served."""
    for name in (LAUNCHER.relative_to(REPO).as_posix(), MANIFEST_PATH, "scripts/demo_urls.txt"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).symlink_to(REPO / name)
    (tmp_path / SNAPSHOT).write_text('{"edited": "count_veh 999"}\n', encoding="utf-8")
    env = {**os.environ, "CF_DEMO_RUNS": str(tmp_path / "runs"), "CF_DEMO_PORT": "1"}
    out = subprocess.run(
        ["bash", str(tmp_path / "scripts/demo_run.sh"), *mode],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )  # fmt: skip
    assert out.returncode == 1, out.stdout + out.stderr
    assert f"{SNAPSHOT} has SHA-256" in out.stderr and launcher_pin() in out.stderr
    assert "ok" not in out.stdout


def test_the_fresh_clone_check_runs_each_readme_command_inside_the_clone() -> None:
    """README: `fresh_clone_check.sh` checks all three commands on a clone of the HEAD."""
    script = (REPO / "scripts/fresh_clone_check.sh").read_text("utf-8")
    readme = (REPO / "README.md").read_text("utf-8")
    runs = {
        "uv sync --frozen": "in_clone uv sync --frozen",
        "uv run --frozen pytest -q": "py -m pytest -q",
        "bash scripts/demo_run.sh": "in_clone bash scripts/demo_run.sh --check",
    }
    for command, run in runs.items():
        assert command in readme and run in script, command


def test_the_launcher_serves_this_manifest_and_opens_these_urls() -> None:
    script = LAUNCHER.read_text("utf-8")
    assert MANIFEST_PATH in script and "scripts/demo_urls.txt" in script
    assert "--check" in script and "offline replay" in script
    assert not CLAIMS.search(script)


@pytest.mark.parametrize("url", REPORT_URLS)
def test_every_report_page_is_served(client: TestClient, url: str) -> None:
    response = client.get(url)
    assert response.status_code == 200, (url, response.text[:300])
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    assert not any(failure in page for failure in FAILURES)
    assert "<script" not in page.lower()
    assert "decision support only" in text_of(page).lower()
    assert not CLAIMS.search(text_of(page)), url
    assert view_problem(url, page, (REPO / SNAPSHOT).exists()) is None, url


TYPOS = ("/flow?e=movment:NE", "/flow?e=movement:XX", "/flow?e=queue:N99", "/flow?zoom=fulll")


@pytest.mark.parametrize("url", TYPOS)
def test_a_url_whose_entity_or_zoom_is_not_shown_fails_the_check(client: TestClient, url) -> None:
    if not (REPO / SNAPSHOT).exists():
        pytest.skip("needs the Git-ignored WorldView snapshot: without it no entity is shown")
    response = client.get(url)
    assert response.status_code == 200  # the app falls back to the default panel quietly
    assert view_problem(url, response.text, snapshot_present=True) is not None


def a_server_runs_here() -> bool:
    """The server holds var/server.lock while it runs, and a second one on this tree refuses."""
    lock = REPO / "var/server.lock"
    if not lock.is_file():
        return False
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


@pytest.mark.skipif(shutil.which("curl") is None, reason="demo_run.sh --check needs curl")
def test_the_launcher_check_fails_on_a_url_whose_entity_is_not_shown(tmp_path: Path) -> None:
    """`bash scripts/demo_run.sh --check` runs the app it serves and opens each URL with curl."""
    if not (REPO / SNAPSHOT).exists():
        pytest.skip("needs the Git-ignored WorldView snapshot: without it no entity is shown")
    if a_server_runs_here():
        pytest.skip("a server is already running on this checkout, and only one may")
    urls = tmp_path / "urls.txt"
    good = ("/flow", "/flow?e=movement:NE", "/flow?e=queue%3AN12", "/flow?zoom=full")
    urls.write_text("\n".join([*good, *TYPOS[::3]]) + "\n", encoding="utf-8")
    env = {**os.environ, "CF_DEMO_URLS": str(urls), "CF_DEMO_RUNS": str(tmp_path / "runs")}
    out = subprocess.run(
        ["bash", str(LAUNCHER), "--check"], cwd=REPO, env=env, capture_output=True, text=True
    )
    assert out.returncode == 1, out.stdout + out.stderr
    assert all(f"ok   {url} " in out.stdout for url in good), out.stdout
    assert "FAIL /flow?e=movment:NE: asked for e=movment:NE" in out.stdout
    assert "FAIL /flow?zoom=fulll: asked for zoom=fulll, the page shows core" in out.stdout


@pytest.mark.parametrize("url", [url for url in URLS if url.split("?")[0] in EXISTING_PAGES])
def test_decision_pages_show_exactly_what_the_report_records(client: TestClient, url: str) -> None:
    report = json.loads((REPO / pinned("results")["path"]).read_text("utf-8"))["report"]
    text = text_of(client.get(url).text)
    selected = report["selected_intervention_id"] or "none"
    recommended = report["recommended_intervention_id"] or "none"
    assert f"Selected: {selected}" in text
    assert f"Recommended: {recommended}" in text
    assert text_of(report["recommendation"]) in text  # its reason, as recorded


@pytest.mark.parametrize("url", REPORT_URLS)
def test_every_demo_url_works_on_a_fresh_clone(fresh_clone: TestClient, url: str) -> None:
    """Without the Git-ignored view a /flow URL draws no World Model and says why."""
    response = fresh_clone.get(url)
    assert response.status_code == 200, (url, response.text[:300])
    if response.headers["content-type"].startswith("text/html"):
        page = text_of(response.text)
        assert not any(failure in response.text for failure in FAILURES)
        assert not re.search(r"\b0 (?:entities|relations|vehicles)\b", page)  # never zeros
        if url.startswith("/flow"):
            assert shown(response.text) == {}, url  # no entity panel, no zoom: no view
            assert "World Model view" in page and "snapshot" in page, url


@pytest.mark.parametrize("url", PRODUCT_URLS)
def test_the_web_app_and_its_api_answer_on_a_fresh_clone(product: TestClient, url: str) -> None:
    """Unbuilt, the web app's pages answer with their build instructions, not an error."""
    response = product.get(url)
    assert response.status_code == 200, (url, response.text[:300])
    assert not any(failure in response.text for failure in FAILURES)


def test_the_two_reference_analyses_load_on_a_fresh_clone(product: TestClient) -> None:
    listed = product.get("/api/v1/analyses").json()["analyses"]
    assert set(REFERENCE_ANALYSES) <= {row["id"] for row in listed if row["valid"]}
    for analysis_id in REFERENCE_ANALYSES:
        record = product.get(f"/api/v1/analyses/{analysis_id}")
        assert record.status_code == 200 and record.json()["analysis_id"] == analysis_id
        assert record.json()["data_origin"] == "reference_dataset"


def test_the_immediacy_check_is_scoped_to_the_report_pages() -> None:
    """The web app has an analysis mode that runs a real job while you watch; docs/CLAIMS.md
    says so. The launcher skips only its pages, and the scan here covers every report page."""
    script = LAUNCHER.read_text("utf-8")
    assert 'report_page "$url" && grep -q -i -E "$BANNED"' in script
    assert set(REPORT_URLS) >= set(EXISTING_PAGES) and not set(REPORT_URLS) & set(WEB_PAGES)


@pytest.mark.parametrize("url", INSPECTOR)
def test_on_a_fresh_clone_the_inspector_says_the_snapshot_is_not_present(
    fresh_clone: TestClient, url: str
) -> None:
    """Without the Git-ignored snapshot the inspector loads and says so, in one line."""
    response = fresh_clone.get(url)
    assert response.status_code == 200, (url, response.status_code)
    assert not any(failure in response.text for failure in FAILURES)
    page = text_of(response.text)
    assert page.count(ABSENT_LINE) == 1, url
    assert not re.search(r"\b0 (?:entities|relations|vehicles|lanes)\b", page)  # never zeros
