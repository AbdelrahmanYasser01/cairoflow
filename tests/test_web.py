"""The read-only FastAPI view on synthetic pipeline runs (synthetic_pipeline.py).

Two runs: one from an ingested grey video, so the pipeline renders the overlay, and one from a
manifest-only clip, which has none. The Muse stub's rationale carries markup, to check that model
text is escaped. Copies of a run with an edited record or report stand in for a run from another
data source (the NGSIM trajectories) and for a candidate that was not simulated. Nothing leaves
the machine.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from html import escape

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from synthetic_pipeline import FAKE_KEY, MuseStub, refuse, write_inputs

from cairoflow_api.app import create_app
from cairoflow_contracts.config import Settings
from cairoflow_contracts.report import DecisionReport
from cairoflow_evaluation.pipeline import CAMERA_SOURCE, RECORD_FILE, PipelineRecord, run_pipeline

HOSTILE = "<script>alert(1)</script> shift green & see"


def _run(root, runs, name: str, *, with_video: bool) -> PipelineRecord:
    (root / name).mkdir()
    inputs = write_inputs(root / name, with_video=with_video)
    return run_pipeline(
        inputs,
        runs / name,
        mode="live",
        settings=Settings(model_api_key=SecretStr(FAKE_KEY)),
        spend_limit_usd=1.0,
        sam_transport=httpx.MockTransport(refuse),
        muse_transport=httpx.MockTransport(MuseStub(rationale=HOSTILE)),
    )


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("web")
    runs = root / "runs"
    video = _run(root, runs, "synthetic-video", with_video=True)
    manifest_only = _run(root, runs, "synthetic-manifest", with_video=False)
    (runs / "not-a-run").mkdir()  # a directory without a record is never served
    return root, runs, TestClient(create_app(runs)), video, manifest_only


def test_the_pipeline_renders_a_reproducible_overlay(site) -> None:
    root, runs, _, video, _ = site
    assert set(video.review_artifacts) == {"overlay.mp4", "overlay.json"}
    assert video.notes == () and video.clip_synthetic
    inputs = root / "synthetic-video" / "inputs" / "pipeline_inputs.json"
    again = run_pipeline(inputs, root / "replayed", mode="replay")
    assert again.review_artifacts == video.review_artifacts  # bit-exact overlay


def test_index_lists_runs_only(site) -> None:
    _, _, client, _, _ = site
    response = client.get("/")
    assert response.status_code == 200
    assert "/runs/synthetic-video/" in response.text
    assert "/runs/synthetic-manifest/" in response.text
    assert "not-a-run" not in response.text
    assert "SYNTHETIC DATA" in response.text


def test_report_page_shows_provenance_on_every_number(site) -> None:
    _, runs, client, _, _ = site
    response = client.get("/runs/synthetic-video/")
    assert response.status_code == 200
    page = response.text
    report = DecisionReport.model_validate_json((runs / "synthetic-video/report.json").read_text())
    for number in report.numbers:
        assert f"{number.number_id}: {len(number.refs)} reference" in page
    for label in ("simulated (SUMO)", "approximated (our procedure)", "assumed"):
        assert label in page
    assert "paired comparison of SUMO metrics" in page
    assert '<span class="source">world model</span>' in page
    assert page.count("model-generated text, not evidence") >= 2 + 2  # 2 hypotheses, I1 and I2
    assert "decision support only" in page and "SYNTHETIC DATA" in page
    assert f"<h2>Data source</h2>\n<p>{escape(CAMERA_SOURCE)}</p>" in page  # the default
    assert "CairoFlow synthetic grey clip" in page  # licence attribution above the pixels
    assert '<video controls preload="metadata" src="/runs/synthetic-video/overlay.mp4">' in page
    assert "not ground truth" in page
    # Every table scrolls in its own box, so the page fits a phone (checked at 375 px by eye).
    assert page.count('<div class="table-wrap"><table>') == page.count("<table") > 0


def test_model_text_is_escaped_and_the_page_has_no_scripts(site) -> None:
    _, _, client, _, _ = site
    page = client.get("/runs/synthetic-video/").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; shift green &amp; see" in page
    assert not re.search(r"<script", page, re.IGNORECASE)


def test_security_headers_are_set(site) -> None:
    _, _, client, _, _ = site
    headers = client.get("/runs/synthetic-video/").headers
    assert headers["content-security-policy"].startswith("default-src 'none'")
    assert "script-src" not in headers["content-security-policy"]  # falls back to 'none'
    assert headers["x-content-type-options"] == "nosniff"


def test_overlay_and_raw_reports_are_served_unchanged(site) -> None:
    _, runs, client, video, _ = site
    response = client.get("/runs/synthetic-video/overlay.mp4")
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content[4:8] == b"ftyp"
    assert hashlib.sha256(response.content).hexdigest() == video.review_artifacts["overlay.mp4"]

    report = client.get("/runs/synthetic-video/report.json")
    assert report.content == (runs / "synthetic-video/report.json").read_bytes()
    markdown = client.get("/runs/synthetic-video/report.md")
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert markdown.content == (runs / "synthetic-video/report.md").read_bytes()


def test_a_run_without_video_says_so(site) -> None:
    _, _, client, _, _ = site
    page = client.get("/runs/synthetic-manifest/").text
    assert "<li>no overlay video: clip.mp4 is not next to the clip manifest</li>" in page
    assert "No overlay video for this run" in page
    assert "<video" not in page
    assert client.get("/runs/synthetic-manifest/overlay.mp4").status_code == 404


def test_a_run_from_trajectories_names_its_data_source(site, tmp_path) -> None:
    """A run whose world model is not from camera tracks (the NGSIM intersection 2 experiment)
    says where its evidence comes from, and nothing on the page claims camera tracks."""
    _, runs, _, _, manifest_only = site
    other = tmp_path / "runs"
    shutil.copytree(runs / "synthetic-manifest", other / "trajectories")
    record = manifest_only.model_copy(
        update={
            "data_source": "Published <b>trajectories</b>; no camera input.",
            "clip_attribution": "Derived from dataset 8ect-6jqj; CC BY-SA 3.0 Unported",
            "clip_synthetic": False,
            "notes": ("Camera 2 is not used: its calibration was not accepted (ADR-018).",),
        }
    )
    PipelineRecord.model_validate_json(record.model_dump_json())  # still a valid record
    (other / "trajectories" / RECORD_FILE).write_text(record.model_dump_json(indent=2) + "\n")
    client = TestClient(create_app(other))

    index = client.get("/").text
    assert "<td>Published &lt;b&gt;trajectories&lt;/b&gt;; no camera input.</td>" in index
    assert "SYNTHETIC DATA" not in index
    page = client.get("/runs/trajectories/").text
    assert "<p>Published &lt;b&gt;trajectories&lt;/b&gt;; no camera input.</p>" in page
    assert "Attribution: Derived from dataset 8ect-6jqj; CC BY-SA 3.0 Unported" in page
    assert "<li>Camera 2 is not used: its calibration was not accepted (ADR-018).</li>" in page
    assert "No overlay video for this run" in page and "<video" not in page
    assert escape(CAMERA_SOURCE) not in page and "from tracks" not in page
    assert "SYNTHETIC DATA" not in page


def test_an_accepted_candidate_without_verdicts_points_to_the_reason(site, tmp_path) -> None:
    """Accepted by the validator is not the same as simulated: a candidate whose SUMO runs were
    invalid (I1 and I3 on intersection 2) has no verdicts, and the page says so."""
    _, runs, client, _, _ = site
    report = DecisionReport.model_validate_json((runs / "synthetic-video/report.json").read_text())
    page = client.get("/runs/synthetic-video/").text
    for c in report.candidates:
        assert f"<h3>{c.intervention_id} ({c.status} by the validator)</h3>" in page
    assert "Not compared in simulation" not in page  # every accepted candidate was compared

    other = tmp_path / "runs"
    shutil.copytree(runs / "synthetic-manifest", other / "unsimulated")
    path = other / "unsimulated/report.json"
    report = DecisionReport.model_validate_json(path.read_text())
    accepted = [i for i, c in enumerate(report.candidates) if c.status == "accepted"]
    candidates = list(report.candidates)
    candidates[accepted[0]] = candidates[accepted[0]].model_copy(update={"verdicts": {}})
    path.write_text(report.model_copy(update={"candidates": tuple(candidates)}).model_dump_json())
    page = TestClient(create_app(other)).get("/runs/unsimulated/").text
    assert page.count("Not compared in simulation: no verdicts.") == 1


@pytest.mark.parametrize(
    "path",
    [
        "/runs/not-a-run/",
        "/runs/unknown/",
        "/runs/..%2f..%2fetc/report.json",
        "/runs/%2e%2e/report.json",
        "/runs/.hidden/",
        "/runs/synthetic-video/pipeline_record.json",
        "/runs/synthetic-video/overlay.json",
        "/docs",
    ],
)
def test_nothing_outside_the_fixed_files_is_served(site, path: str) -> None:
    _, _, client, _, _ = site
    assert client.get(path).status_code == 404
