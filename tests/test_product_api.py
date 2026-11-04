"""The product server: reading analyses, sources, uploads, the web app and the legacy mount."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import importlib.util
import json
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_product_support import (
    REPO,
    SAMPLE,
    SAMPLE_ID,
    client_for,
    make_repo,
    make_settings,
    make_video,
    write_test_analysis,
)

from cairoflow_api.product import sources as sources_module
from cairoflow_api.product.index import ProductIndex
from cairoflow_api.product.routes import accepts_gzip
from cairoflow_api.product.sources import (
    SourceCatalog,
    UploadRefused,
    VideoProbe,
    sanitise_upload_name,
)
from cairoflow_api.serve import PRODUCT_CSP, create_product_app
from cairoflow_contracts.analysis_record import ArtifactRef
from cairoflow_contracts.cv_diagnosis import (
    CvDiagnosis,
    CvDiagnosisRecord,
    CvDiagnosisReplicate,
    CvEvidenceItem,
    CvHypothesis,
)
from cairoflow_contracts.decision_summary import DecisionSummary
from cairoflow_contracts.evidence import Provenance
from cairoflow_perception.ngsim import COLUMNS

CLIP_BYTES = bytes(range(256)) * 40


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo")


@pytest.fixture
def client(repo: Path):
    with client_for(make_settings(repo)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_video(tmp_path_factory.mktemp("video") / "clip.mp4")


# --- analyses ---------------------------------------------------------------------------------


def test_health_says_whether_the_worker_can_run(repo: Path) -> None:
    with client_for(make_settings(repo)) as client:
        body = client.get("/api/v1/health").json()
    assert body["ok"] is True and body["version"] and body["vision_worker_available"] is True
    missing = make_settings(repo, worker_script=repo / "nope.py")
    with client_for(missing) as client:
        body = client.get("/api/v1/health").json()
    assert body["ok"] is True and body["vision_worker_available"] is False
    assert "nope.py" in body["vision_worker_problem"]


def test_analyses_list_newest_first_with_their_figures(repo: Path) -> None:
    newest = datetime(2030, 1, 2, tzinfo=UTC)
    write_test_analysis(repo / "data/analyses", "newest-one", created_at=newest)
    with client_for(make_settings(repo)) as client:
        listed = client.get("/api/v1/analyses").json()["analyses"]
    record = json.loads((SAMPLE / "record.json").read_text("utf-8"))
    tracks = json.loads((SAMPLE / "tracks.json").read_text("utf-8"))
    assert [item["id"] for item in listed] == ["newest-one", SAMPLE_ID]
    sample = listed[1]
    assert sample["title"] == record["title"] and sample["mode"] == record["mode"]
    assert sample["data_origin"] == "reference_dataset"
    assert sample["frame_count"] == record["frame_count"]
    assert sample["n_tracks"] == len(tracks["tracks"])
    assert sample["created_at"].startswith("2026-") and sample["valid"] is True


def test_record_tracks_and_trajectories_are_the_stored_bytes(client) -> None:
    identity = {"Accept-Encoding": "identity"}
    record = client.get(f"/api/v1/analyses/{SAMPLE_ID}", headers=identity)
    tracks = client.get(f"/api/v1/analyses/{SAMPLE_ID}/tracks", headers=identity)
    binary = client.get(f"/api/v1/analyses/{SAMPLE_ID}/trajectories", headers=identity)
    assert record.content == (SAMPLE / "record.json").read_bytes()
    assert tracks.content == (SAMPLE / "tracks.json").read_bytes()
    assert binary.content == (SAMPLE / "trajectories.bin").read_bytes()
    assert binary.content[:4] == b"CFT1"
    assert binary.headers["content-type"] == "application/octet-stream"
    assert "content-encoding" not in binary.headers


def test_json_and_binary_are_gzipped_only_for_a_client_that_accepts_it(client) -> None:
    url = f"/api/v1/analyses/{SAMPLE_ID}/trajectories"
    packed = client.get(url, headers={"Accept-Encoding": "gzip"})
    assert packed.headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in packed.headers["vary"]
    assert packed.content == (SAMPLE / "trajectories.bin").read_bytes()
    refused = client.get(url, headers={"Accept-Encoding": "gzip;q=0, identity"})
    assert "content-encoding" not in refused.headers


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("gzip", True),
        ("deflate, gzip;q=0.5", True),
        ("GZIP", True),
        ("gzip;q=0", False),
        ("identity", False),
        ("", False),
        ("x-gzip", False),
    ],
)
def test_accept_encoding_is_parsed(header: str, expected: bool) -> None:
    assert accepts_gzip(header) is expected


def test_an_artifact_has_http_range_and_is_never_compressed(repo: Path, video: Path) -> None:
    data = video.read_bytes()
    artifact = ArtifactRef(role="annotated_video", path="annotated.mp4", media_type="video/mp4")
    write_test_analysis(repo / "data/analyses", "with-video", extra=[(artifact, data)])
    with client_for(make_settings(repo)) as client:
        url = "/api/v1/analyses/with-video/artifacts/annotated_video"
        full = client.get(url, headers={"Accept-Encoding": "gzip"})
        part = client.get(url, headers={"Range": "bytes=10-109", "Accept-Encoding": "gzip"})
    assert full.status_code == 200 and full.content == data
    assert "content-encoding" not in full.headers
    assert part.status_code == 206 and part.content == data[10:110]
    assert part.headers["content-range"] == f"bytes 10-109/{len(data)}"
    assert "content-encoding" not in part.headers


def test_an_unbundled_artifact_answers_404_with_its_rebuild_hint(repo: Path) -> None:
    hint = "Rebuild it with scripts/render_overlay.py from the source video."
    artifact = ArtifactRef(
        role="source_video",
        path="source.mp4",
        media_type="video/mp4",
        bundled=False,
        licence_note="CC BY-SA 4.0, not distributed here",
        rebuild_hint=hint,
    )
    write_test_analysis(repo / "data/analyses", "no-video", extra=[(artifact, None)])
    with client_for(make_settings(repo)) as client:
        response = client.get("/api/v1/analyses/no-video/artifacts/source_video")
        unlisted = client.get("/api/v1/analyses/no-video/artifacts/report")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["rebuild_hint"] == hint and body["bundled"] is False and body["role"]
    assert unlisted.status_code == 404 and "rebuild_hint" not in unlisted.json()


def test_a_tampered_analysis_is_listed_invalid_and_never_served(repo: Path) -> None:
    target = write_test_analysis(repo / "data/analyses", "tampered")
    (target / "trajectories.bin").write_bytes(b"CFT1 changed after it was written")
    with client_for(make_settings(repo)) as client:
        listed = {a["id"]: a for a in client.get("/api/v1/analyses").json()["analyses"]}
        record = client.get("/api/v1/analyses/tampered")
        tracks = client.get("/api/v1/analyses/tampered/tracks")
        binary = client.get("/api/v1/analyses/tampered/trajectories")
        artifact = client.get("/api/v1/analyses/tampered/artifacts/trajectories")
    bad = listed["tampered"]
    assert bad["valid"] is False and "changed" in bad["invalid_reason"]
    assert bad["frame_count"] is None and bad["n_tracks"] is None and bad["title"] is None
    assert listed[SAMPLE_ID]["valid"] is True
    for response in (record, tracks, binary, artifact):
        assert response.status_code == 409
        assert "changed" in response.json()["invalid_reason"]


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        (lambda d: (d / "record.json").write_text("not json"), "JSON"),
        (lambda d: (d / "record.json").unlink(), "no record.json"),
        (lambda d: d.rename(d.with_name("other-name")), "directory"),
    ],
)
def test_a_broken_analysis_directory_is_invalid_with_its_reason(repo, damage, reason) -> None:
    target = write_test_analysis(repo / "data/analyses", "broken")
    damage(target)
    with client_for(make_settings(repo)) as client:
        listed = client.get("/api/v1/analyses").json()["analyses"]
    bad = [item for item in listed if not item["valid"]]
    assert len(bad) == 1 and reason in bad[0]["invalid_reason"]
    assert str(repo) not in bad[0]["invalid_reason"]  # no absolute paths in a response
    assert listed[0]["id"] == SAMPLE_ID  # invalid ones sort last


def test_an_artifact_path_leaving_the_analysis_makes_it_invalid(repo: Path) -> None:
    target = write_test_analysis(repo / "data/analyses", "escapes")
    record = json.loads((target / "record.json").read_text("utf-8"))
    record["artifacts"].append(
        {"role": "report", "path": "../../../secret.txt", "media_type": "text/plain"}
    )
    (target / "record.json").write_text(json.dumps(record))
    (repo / "secret.txt").write_text("private")
    with client_for(make_settings(repo)) as client:
        response = client.get("/api/v1/analyses/escapes/artifacts/report")
    assert response.status_code == 409 and "private" not in response.text


def test_a_symlink_artifact_cannot_reach_outside_the_analysis(repo: Path) -> None:
    artifact = ArtifactRef(role="report", path="report.txt", media_type="text/plain")
    target = write_test_analysis(repo / "data/analyses", "linked", extra=[(artifact, None)])
    (repo / "secret.txt").write_text("private")
    (target / "report.txt").symlink_to(repo / "secret.txt")
    with client_for(make_settings(repo)) as client:
        response = client.get("/api/v1/analyses/linked/artifacts/report")
    assert response.status_code == 404 and "private" not in response.text


def test_unknown_ids_and_routes_answer_json_404(client) -> None:
    for path in ("/api/v1/analyses/nope", "/api/v1/analyses/nope/tracks", "/api/v1/nowhere"):
        response = client.get(path)
        assert response.status_code == 404 and "detail" in response.json()


def test_read_routes_answer_head_with_the_get_headers_and_no_body(client) -> None:
    identity = {"Accept-Encoding": "identity"}
    for path in (
        "/api/v1/health",
        "/api/v1/analyses",
        f"/api/v1/analyses/{SAMPLE_ID}",
        f"/api/v1/analyses/{SAMPLE_ID}/tracks",
        f"/api/v1/analyses/{SAMPLE_ID}/trajectories",
        "/api/v1/site",
        "/api/v1/intersections",
        "/api/v1/decision",
        "/api/v1/sources",
        "/api/v1/jobs",
    ):
        full = client.get(path, headers=identity)
        head = client.head(path, headers=identity)
        assert head.status_code == 200, path
        assert head.content == b"", path
        assert head.headers["content-length"] == str(len(full.content)), path
        assert head.headers["content-type"] == full.headers["content-type"], path
    assert client.head("/api/v1/analyses/nope").status_code == 404
    assert client.head("/api/v1/nowhere").status_code == 404


def test_head_sizes_a_video_without_sending_it(client, video: Path) -> None:
    assert client.put("/api/v1/uploads/sized.mp4", content=video.read_bytes()).status_code == 201
    head = client.head("/api/v1/sources/upload-sized.mp4/video")
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-length"] == str(video.stat().st_size)


@pytest.mark.parametrize(
    ("method", "path", "allow"),
    [
        ("POST", "/api/v1/health", "GET, HEAD"),
        ("DELETE", f"/api/v1/analyses/{SAMPLE_ID}", "GET, HEAD"),
        ("PUT", "/api/v1/jobs", "GET, HEAD, POST"),
        ("PATCH", "/api/v1/jobs/any-id", "DELETE, GET, HEAD"),
        ("GET", "/api/v1/uploads/clip.mp4", "PUT"),
        ("HEAD", "/api/v1/uploads/clip.mp4", "PUT"),
        ("HEAD", "/api/v1/jobs/any-id/events", "GET"),  # a stream has no headers to give ahead
    ],
)
def test_a_known_path_with_the_wrong_method_answers_405(client, method, path, allow) -> None:
    response = client.request(method, path)
    assert response.status_code == 405
    assert response.headers["allow"] == allow
    assert response.headers["content-security-policy"] == PRODUCT_CSP
    if method != "HEAD":
        assert method in response.json()["detail"]


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
def test_a_path_that_is_not_a_route_answers_404_to_every_method(client, method: str) -> None:
    response = client.request(method, "/api/v1/nowhere/at/all")
    assert response.status_code == 404 and "allow" not in response.headers


# --- the index --------------------------------------------------------------------------------


def test_the_database_can_be_deleted_and_is_rebuilt_from_files(repo: Path) -> None:
    settings = make_settings(repo)
    with client_for(settings) as client:
        before = client.get("/api/v1/analyses").json()
    assert settings.db_path.is_file()
    settings.db_path.unlink()
    with client_for(settings) as client:
        assert client.get("/api/v1/analyses").json() == before
    settings.db_path.write_bytes(b"this is not a sqlite database at all, not even close")
    with client_for(settings) as client:
        assert client.get("/api/v1/analyses").json() == before


def test_series_bins_are_stored_per_bin_and_a_missing_bin_stays_null(repo: Path) -> None:
    target = write_test_analysis(repo / "data/analyses", "gappy")
    record = json.loads((target / "record.json").read_text("utf-8"))
    settings = make_settings(repo)
    with client_for(settings):
        pass
    index = ProductIndex(settings.db_path, (), repo)
    series = record["series"][0]
    bins = index.series_bins("gappy", series["series_id"])
    assert [b["value"] for b in bins] == series["values"]
    assert bins[1]["bin_start_s"] == series["start_s"] + series["bin_s"]
    assert bins[0]["unit"] == series["unit"] and bins[0]["evidence_kind"] == "observed"

    index_rows = ProductIndex(settings.db_path, (repo / "data/analyses",), repo)
    series["values"][0] = None
    (target / "record.json").write_text(json.dumps(record))
    index_rows.add_analysis(target)
    assert index.series_bins("gappy", series["series_id"])[0]["value"] is None


def test_a_workers_hidden_staging_directory_is_not_an_analysis(repo: Path) -> None:
    (repo / "data/analyses/.staging-abc").mkdir(parents=True)
    with client_for(make_settings(repo)) as client:
        listed = client.get("/api/v1/analyses").json()["analyses"]
    assert [item["id"] for item in listed] == [SAMPLE_ID]


def test_a_second_server_on_the_same_state_refuses_to_start(repo: Path) -> None:
    settings = make_settings(repo)
    with client_for(settings) as first:
        with pytest.raises(RuntimeError, match="Another CairoFlow server"):
            with client_for(settings):
                pass
        assert first.get("/api/v1/health").status_code == 200
    with client_for(settings) as again:  # the lock goes with the server
        assert again.get("/api/v1/health").status_code == 200


def test_the_same_analysis_id_in_two_places_is_refused(repo: Path) -> None:
    write_test_analysis(repo / "data/analyses", SAMPLE_ID)
    with client_for(make_settings(repo)) as client:
        listed = client.get("/api/v1/analyses").json()["analyses"]
        response = client.get(f"/api/v1/analyses/{SAMPLE_ID}")
    assert [item["valid"] for item in listed] == [False]
    assert "two directories" in listed[0]["invalid_reason"]
    assert response.status_code == 409


# --- site, corridor, decision -----------------------------------------------------------------


def test_site_gives_geometry_and_the_calibration_a_record_holds(client) -> None:
    body = client.get("/api/v1/site").json()
    geometry = json.loads(
        (REPO / "data/geometry/ngsim-lankershim-int2/geometry.json").read_text("utf-8")
    )
    record = json.loads((SAMPLE / "record.json").read_text("utf-8"))
    assert body["lanes"] == geometry["lanes"] and body["gates"] == geometry["gates"]
    assert body["site_name"] == record["source"]["site_name"]
    assert body["site_name_status"] == "supported_hypothesis"
    assert body["calibration"]["spec_sha256"] == record["calibration"]["spec_sha256"]
    assert body["calibration"]["source_analysis_id"] == SAMPLE_ID
    image = client.get("/api/v1/site/image-geometry").json()
    assert image["image_width_px"] == 640 and image["lanes"]


def test_site_without_a_record_has_no_calibration_rather_than_a_default(repo: Path) -> None:
    (repo / "data/sample/analyses" / SAMPLE_ID / "tracks.json").write_text("{}")
    with client_for(make_settings(repo)) as client:
        body = client.get("/api/v1/site").json()
    assert body["calibration"] is None and body["site_name"] is None and body["lanes"]


def test_intersections_and_decision_are_the_stored_files(client, repo: Path) -> None:
    corridor = client.get("/api/v1/intersections").json()
    assert corridor == json.loads((repo / "data/sample/corridor_summary.json").read_text("utf-8"))
    assert corridor["evidence_label"] == "reference_data"
    assert [i["int_id"] for i in corridor["intersections"]] == [1, 2, 3, 4]
    assert [i["has_video_analysis"] for i in corridor["intersections"]] == [
        False, True, False, False
    ]  # fmt: skip
    assert [i["name"] is None for i in corridor["intersections"]] == [True, False, True, True]
    decision = DecisionSummary.model_validate(client.get("/api/v1/decision").json())
    assert decision.outcome.outcome == "none_selected"


def write_diagnosis(repo: Path, analysis_id: str = SAMPLE_ID, record_sha256: str = "") -> Path:
    """A diagnosis file as scripts/muse_cv_diagnosis.py writes it, about the stored record."""
    record = repo / "data/sample/analyses" / SAMPLE_ID / "record.json"
    digest = record_sha256 or hashlib.sha256(record.read_bytes()).hexdigest()
    now = datetime(2026, 11, 4, tzinfo=UTC)
    hypothesis = CvHypothesis(
        hypothesis_id="H1",
        statement="Southbound arrivals may be undercounted.",
        supporting_evidence_ids=("E1",),
        contradicting_evidence_ids=(),
        confidence="low",
        how_to_check="Compare with the published trajectories.",
    )
    diagnosis = CvDiagnosisRecord(
        label="model text",
        analysis_id=analysis_id,
        analysis_record_sha256=digest,
        model="test-model",
        prompt_version="test",
        created_at_utc=now,
        evidence=(
            CvEvidenceItem(
                evidence_id="E1",
                statement="Vehicles in view",
                value=112.0,
                unit="veh",
                evidence_kind="observed",
                status="ungated",
            ),
        ),
        replicates=(
            CvDiagnosisReplicate(
                replicate=1,
                status="ok",
                request_key=digest,
                from_cache=True,
                diagnosis=CvDiagnosis(summary="A", hypotheses=(hypothesis,), limitations=()),
            ),
        ),
        provenance=Provenance(kind="model_generated", produced_by="test", produced_at_utc=now),
    )
    path = repo / "data/sample/diagnoses" / f"{SAMPLE_ID}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(diagnosis.model_dump_json(indent=1) + "\n", encoding="utf-8")
    return path


def test_a_diagnosis_is_the_stored_file_and_stays_model_generated(client, repo: Path) -> None:
    path = write_diagnosis(repo)
    answer = client.get(f"/api/v1/analyses/{SAMPLE_ID}/diagnosis")
    assert answer.status_code == 200
    assert answer.json() == json.loads(path.read_text("utf-8"))
    assert answer.json()["provenance"]["kind"] == "model_generated"


def test_an_analysis_with_no_diagnosis_answers_404_and_says_how_to_make_one(client) -> None:
    answer = client.get(f"/api/v1/analyses/{SAMPLE_ID}/diagnosis")
    assert answer.status_code == 404
    assert "muse_cv_diagnosis.py" in answer.json()["rebuild_hint"]


def test_a_diagnosis_for_an_unknown_analysis_never_reads_a_client_path(client, repo: Path) -> None:
    write_diagnosis(repo)
    assert client.get("/api/v1/analyses/not-listed/diagnosis").status_code == 404
    escape = f"/api/v1/analyses/..%2F{SAMPLE_ID}/diagnosis"
    assert client.get(escape).status_code == 404


def test_a_diagnosis_that_breaks_its_contract_is_not_served(client, repo: Path) -> None:
    write_diagnosis(repo).write_text('{"analysis_id": "x"}', encoding="utf-8")
    answer = client.get(f"/api/v1/analyses/{SAMPLE_ID}/diagnosis")
    assert answer.status_code == 409
    assert "contract" in answer.json()["detail"]


@pytest.mark.parametrize(
    ("analysis_id", "record_sha256"), [("cv-other", ""), (SAMPLE_ID, "0" * 64)]
)
def test_a_diagnosis_of_another_record_is_not_served(
    client, repo: Path, analysis_id: str, record_sha256: str
) -> None:
    write_diagnosis(repo, analysis_id, record_sha256)
    answer = client.get(f"/api/v1/analyses/{SAMPLE_ID}/diagnosis")
    assert answer.status_code == 409
    assert "another record" in answer.json()["detail"]


def test_a_missing_summary_says_how_to_build_it(repo: Path) -> None:
    (repo / "data/sample/corridor_summary.json").unlink()
    (repo / "data/sample/decision_summary.json").write_text('{"record_format": "wrong"}')
    with client_for(make_settings(repo)) as client:
        corridor = client.get("/api/v1/intersections")
        decision = client.get("/api/v1/decision")
    assert corridor.status_code == 404 and "build_corridor_summary" in corridor.json()["detail"]
    assert decision.status_code == 409 and "build_decision_summary" in decision.json()["detail"]


def test_the_decision_summary_is_copied_from_its_records_and_reproducible() -> None:
    module = load_script("build_decision_summary")
    built = module.build_summary(REPO)
    stored = (REPO / "data/sample/decision_summary.json").read_text("utf-8")
    assert built.model_dump_json(indent=1) + "\n" == stored
    manifest = json.loads((REPO / "data/demo/ngsim-int2-ensemble-control.json").read_text("utf-8"))
    results = json.loads((REPO / manifest["files"]["results"]["path"]).read_text("utf-8"))
    assert built.outcome.selection_reason == results["report"]["selection_reason"]
    by_key = {(m.candidate, m.metric_name): m for m in built.metrics}
    assert len(by_key) == len(results["comparisons"])
    for comparison in results["comparisons"]:
        metric = by_key[(comparison["candidate"], comparison["metric_name"])]
        assert metric.mean_difference == comparison["mean_difference"]
        assert list(metric.ci95_difference) == comparison["ci95_difference"]
        assert metric.unit == comparison["unit"] and metric.evidence_kind == "simulated"
    assert all(h.evidence_kind == "model_generated" for h in built.hypotheses)
    assert all(c.evidence_kind == "model_generated" for c in built.candidates)
    assert all(h.recurrence and h.recurrence.n_replies == 4 for h in built.hypotheses)
    assert all(h.citations and h.citations[0].summary for h in built.hypotheses)
    assert built.ensemble and built.ensemble.evidence_kind == "simulated"
    assert any("northbound" in text and "southbound" in text for text in built.limitations)
    assert all(c.validator and c.validator.status == "accepted" for c in built.candidates)
    paths = [source.path for source in built.sources]
    assert manifest["files"]["results"]["path"] in paths
    assert not [p for p in paths if p.endswith((".mp4", ".jpg", ".png"))]  # no pixels needed


def test_the_corridor_summary_counts_from_the_trajectories(tmp_path: Path) -> None:
    module = load_script("build_corridor_summary")
    rows = []
    for int_id in (1, 2, 3, 4):
        for frame, offset in ((1201, 0.0), (1202, 3.28084), (1203, 3.28084)):  # 10 m/s, then 0
            rows.append(
                {
                    "vehicle_id": 10 + int_id,
                    "frame_id": frame,
                    "global_time": 1118935800000 + (frame - 1201) * 100,
                    "global_x": 6452083.0 + int_id * 100 + offset,
                    "global_y": 1873176.0,
                    "v_length": 15,
                    "v_width": 6,
                    "lane_id": 1,
                    "int_id": int_id,
                    "section_id": 0,
                    "direction": 2,
                    "movement": 1,
                }  # fmt: skip
            )
    trajectories = tmp_path / "t.csv"
    with trajectories.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({"input": {"licence": "L", "attribution": "A"}}))
    summary = module.summarise(trajectories, scenario)
    first = summary["intersections"][0]
    assert first["rows"] == 3 and first["unique_vehicles"] == 1
    assert first["vehicles_by_direction"] == {"EB": 0, "NB": 1, "WB": 0, "SB": 0}
    assert first["mean_moving_speed_m_s"] == pytest.approx(10.0, abs=0.01)
    assert first["moving_rows"] == 1  # the stopped row and the first row have no moving speed
    assert 30 < first["location"]["lat_deg"] < 40 and first["name"] is None
    assert summary["evidence_label"] == "reference_data"
    assert summary["source"]["attribution"] == "A"


def test_the_stored_corridor_summary_carries_the_recorded_attribution() -> None:
    stored = json.loads((REPO / "data/sample/corridor_summary.json").read_text("utf-8"))
    scenario = json.loads(
        (REPO / "data/geometry/ngsim-lankershim-int2/scenario.json").read_text("utf-8")
    )
    assert stored["source"]["attribution"] == scenario["input"]["attribution"]
    assert stored["source"]["licence_name"] == scenario["input"]["licence"]


# --- sources and uploads ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("clip.mp4", "clip.mp4"),
        ("My Clip (1).MP4", "My_Clip_1.mp4"),
        (".hidden.mov", "hidden.mov"),
        ("nile cam.webm", "nile_cam.webm"),
    ],
)
def test_upload_names_are_sanitised(name: str, expected: str) -> None:
    assert sanitise_upload_name(name) == expected


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("../../etc/passwd.mp4", 400),
        ("..\\..\\windows.mp4", 400),
        ("a/b.mp4", 400),
        ("x..mp4", 400),
        ("nul\x00.mp4", 400),
        ("___.mp4", 400),
        ("clip.mp4.exe", 415),
        ("clip", 415),
        ("page.html", 415),
    ],
)
def test_upload_names_are_refused(name: str, status: int) -> None:
    with pytest.raises(UploadRefused) as refused:
        sanitise_upload_name(name)
    assert refused.value.status_code == status


def test_an_upload_is_stored_probed_and_listed(client, repo: Path, video: Path) -> None:
    data = video.read_bytes()
    response = client.put("/api/v1/uploads/my clip.mp4", content=data)
    assert response.status_code == 201
    body = response.json()
    assert body["source_id"] == "upload-my_clip.mp4" and body["kind"] == "upload"
    assert body["duration_s"] == pytest.approx(2.0, abs=0.2)
    assert (body["width_px"], body["height_px"]) == (64, 48)
    assert body["calibrated"] is False and body["playable"] is True
    assert (repo / "data/uploads/my_clip.mp4").read_bytes() == data
    assert sorted(p.name for p in (repo / "data/uploads").iterdir()) == [
        ".gitignore", "my_clip.mp4"
    ]  # fmt: skip
    sources = client.get("/api/v1/sources").json()["sources"]
    assert [s["source_id"] for s in sources] == ["upload-my_clip.mp4"]
    assert "my_clip.mp4" == sources[0]["file_name"] and str(repo) not in json.dumps(sources)
    playable = client.get(sources[0]["video_url"], headers={"Range": "bytes=0-9"})
    assert playable.status_code == 206 and playable.content == data[:10]


@pytest.mark.parametrize(
    "path", ["..%2F..%2Fescape.mp4", "..%5C..%5Cescape.mp4", "sub%2Fescape.mp4", "%2Fetc%2Fx.mp4"]
)
def test_an_upload_cannot_leave_the_uploads_directory(client, repo: Path, path: str) -> None:
    before = sorted(str(p) for p in repo.parent.rglob("*"))
    response = client.put(f"/api/v1/uploads/{path}", content=b"data")
    assert response.status_code == 400
    assert sorted(str(p) for p in repo.parent.rglob("*")) == before


def test_a_file_that_is_not_video_is_refused_and_removed(client, repo: Path) -> None:
    response = client.put("/api/v1/uploads/fake.mp4", content=b"not a video at all\n" * 100)
    assert response.status_code == 415 and "video" in response.json()["detail"]
    assert [p.name for p in (repo / "data/uploads").iterdir()] == [".gitignore"]


def test_a_playlist_named_as_video_is_refused(client, repo: Path) -> None:
    playlist = b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:10.0,\nfile:///etc/passwd\n"
    response = client.put("/api/v1/uploads/playlist.mp4", content=playlist)
    assert response.status_code == 415
    assert [p.name for p in (repo / "data/uploads").iterdir()] == [".gitignore"]


def test_an_upload_is_capped_while_it_streams(repo: Path, video: Path) -> None:
    settings = make_settings(repo, max_upload_bytes=100)

    def body():
        yield b"x" * 80
        yield b"x" * 80

    with client_for(settings) as client:
        declared = client.put("/api/v1/uploads/big.mp4", content=b"x" * 101)
        streamed = client.put("/api/v1/uploads/big.mp4", content=body())
    assert declared.status_code == 413 and streamed.status_code == 413
    assert [p.name for p in (repo / "data/uploads").iterdir()] == [".gitignore"]


def test_an_upload_never_overwrites_and_an_empty_one_is_refused(client, video: Path) -> None:
    assert client.put("/api/v1/uploads/one.mp4", content=video.read_bytes()).status_code == 201
    assert client.put("/api/v1/uploads/one.mp4", content=video.read_bytes()).status_code == 409
    assert client.put("/api/v1/uploads/none.mp4", content=b"").status_code == 400


def test_clips_and_the_camera_source_are_found_and_calibration_is_proved(
    repo: Path, video: Path
) -> None:
    settings = make_settings(repo)
    camera = settings.camera_source
    camera.parent.mkdir(parents=True)
    camera.write_bytes(video.read_bytes())  # stands in for the camera 2 file
    digest = hashlib.sha256(camera.read_bytes()).hexdigest()
    calibration = settings.camera_calibration
    calibration.parent.mkdir(parents=True, exist_ok=True)
    calibration.write_text(json.dumps({"source_video_sha256": digest}))
    for name, source_sha256 in (("from-camera", digest), ("elsewhere", "0" * 64)):
        clip = repo / "data/processed/clips" / name
        clip.mkdir(parents=True)
        (clip / "clip.mp4").write_bytes(video.read_bytes())
        facts = {"source_sha256": source_sha256, "trim": {"start_s": 60.0}}
        (clip / "source_clip.json").write_text(json.dumps(facts))
    with client_for(settings) as client:
        sources = {s["source_id"]: s for s in client.get("/api/v1/sources").json()["sources"]}
    assert sources["camera-2"]["calibrated"] is True
    assert sources["clip-from-camera"]["calibrated"] is True
    assert sources["clip-from-camera"]["source_start_s"] == 60.0
    assert sources["clip-elsewhere"]["calibrated"] is False
    assert sources["clip-elsewhere"]["source_start_s"] is None  # never claimed as camera 2 footage
    assert sources["clip-from-camera"]["calibration_note"]
    assert sources["clip-elsewhere"]["calibration_note"] is None


def test_a_source_ffprobe_cannot_read_reports_the_problem_with_no_figures(repo: Path) -> None:
    clip = repo / "data/processed/clips/broken"
    clip.mkdir(parents=True)
    (clip / "clip.mp4").write_bytes(b"not a video")
    with client_for(make_settings(repo)) as client:
        (source,) = client.get("/api/v1/sources").json()["sources"]
        video = client.get(f"/api/v1/sources/{source['source_id']}/video")
    assert source["problem"] and source["duration_s"] is None and source["playable"] is False
    assert source["video_url"] is None and video.status_code == 409


@pytest.mark.parametrize(
    "facts",
    [
        {},
        {"trim": None},
        {"trim": {}},
        {"trim": {"start_s": None}},
        {"trim": {"start_s": "60"}},
        {"trim": {"start_s": True}},
        {"trim": {"start_s": -1.0}},
        {"trim": {"start_s": float("nan")}},
        {"trim": {"start_s": float("inf")}},
        {"trim": {"start_s": 10**400}},
        {"trim": 60.0},
        {"trim": "60"},
        {"trim": [60.0]},
    ],
)
def test_a_camera_clip_with_no_usable_trim_start_is_refused_not_calibrated(
    repo: Path, video: Path, facts: dict
) -> None:
    """Its place in camera 2 is unknown, so the held-out rule could not be applied to it."""
    settings = make_settings(repo)
    digest = "a" * 64
    settings.camera_calibration.parent.mkdir(parents=True, exist_ok=True)
    settings.camera_calibration.write_text(json.dumps({"source_video_sha256": digest}))
    clip = repo / "data/processed/clips/no-trim"
    clip.mkdir(parents=True)
    (clip / "clip.mp4").write_bytes(video.read_bytes())
    (clip / "source_clip.json").write_text(json.dumps({"source_sha256": digest, **facts}))
    body = {"source_id": "clip-no-trim", "start_s": 0, "duration_s": 1.0, "frame_stride": 1}
    with client_for(settings) as client:
        listing = client.get("/api/v1/sources")
        job = client.post("/api/v1/jobs", json=body)
        jobs = client.get("/api/v1/jobs").json()["jobs"]
    assert listing.status_code == 200
    (source,) = listing.json()["sources"]
    assert source["calibrated"] is False and source["source_start_s"] is None
    assert source["calibration_note"] is None and "held-out" in source["problem"]
    assert source["duration_s"] and source["playable"] is True  # it can still be watched
    assert job.status_code == 422 and "held-out" in job.json()["detail"] and jobs == []


def test_a_clip_from_elsewhere_with_no_trim_is_an_ordinary_clip(repo: Path, video: Path) -> None:
    settings = make_settings(repo)
    settings.camera_calibration.parent.mkdir(parents=True, exist_ok=True)
    settings.camera_calibration.write_text(json.dumps({"source_video_sha256": "a" * 64}))
    clip = repo / "data/processed/clips/elsewhere"
    clip.mkdir(parents=True)
    (clip / "clip.mp4").write_bytes(video.read_bytes())
    (clip / "source_clip.json").write_text(json.dumps({"source_sha256": "b" * 64, "trim": "?"}))
    with client_for(settings) as client:
        (source,) = client.get("/api/v1/sources").json()["sources"]
    assert source["problem"] is None and source["calibrated"] is False


def test_an_upload_is_written_away_from_the_event_loop(repo: Path, monkeypatch) -> None:
    """A slow disk must not freeze the handlers that share the loop, such as job streams."""
    probe = VideoProbe(1.0, 64, 48, 10.0, "h264", sources_module.BROWSER_CONTAINERS[0])
    monkeypatch.setattr(sources_module, "probe_video", lambda *_: probe)
    writers: set[int] = set()
    real_open = Path.open

    class RecordedFile:
        def __init__(self, handle) -> None:
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            self._handle.close()

        def write(self, chunk: bytes) -> int:
            writers.add(threading.get_ident())
            return self._handle.write(chunk)

    def open_part(path: Path, *args: object, **kwargs: object):
        handle = real_open(path, *args, **kwargs)
        return RecordedFile(handle) if path.name.startswith(sources_module.PART_PREFIX) else handle

    monkeypatch.setattr(Path, "open", open_part)

    async def chunks():
        for _ in range(3):
            yield b"x" * 1024

    async def upload() -> tuple[int, int]:
        catalog = SourceCatalog(make_settings(repo))
        await catalog.save_upload("slow.mp4", chunks(), None)
        return threading.get_ident(), (repo / "data/uploads/slow.mp4").stat().st_size

    loop_thread, size = asyncio.run(upload())
    assert size == 3 * 1024 and writers and loop_thread not in writers


# --- the web app and the legacy app -----------------------------------------------------------


def build_dist(repo: Path) -> Path:
    dist = repo / "apps/web/dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SPA</title>")
    (dist / "assets/app-1.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    return dist


@pytest.mark.parametrize(
    "path", ["/", "/intersections", "/vision", "/twin", "/analytics", "/evidence", "/_design"]
)
def test_the_web_app_owns_its_paths_with_the_strict_csp(repo: Path, path: str) -> None:
    build_dist(repo)
    with client_for(make_settings(repo)) as client:
        response = client.get(path)
    assert response.status_code == 200 and "<title>SPA</title>" in response.text
    assert response.headers["content-security-policy"] == PRODUCT_CSP


def test_assets_are_static_and_cannot_be_escaped(repo: Path) -> None:
    build_dist(repo)
    (repo / "apps/web/secret.txt").write_text("private")
    with client_for(make_settings(repo)) as client:
        asset = client.get("/assets/app-1.js")
        icon = client.get("/favicon.svg")
        escape = client.get("/assets/%2E%2E/%2E%2E/secret.txt")
    assert asset.status_code == 200 and asset.text == "console.log(1)"
    assert asset.headers["content-security-policy"] == PRODUCT_CSP
    assert icon.status_code == 200
    assert escape.status_code == 404 and "private" not in escape.text


def test_without_a_build_the_home_page_says_how_to_build_it(repo: Path) -> None:
    with client_for(make_settings(repo)) as client:
        response = client.get("/")
        missing_asset = client.get("/assets/app-1.js")
    assert response.status_code == 200 and "npm run build" in response.text
    assert "text/html" in response.headers["content-type"]
    assert response.headers["content-security-policy"] == PRODUCT_CSP
    assert "<script" not in response.text
    assert missing_asset.status_code == 404


def test_api_responses_carry_the_strict_csp(client) -> None:
    for path in ("/api/v1/health", "/api/v1/nowhere"):
        assert client.get(path).headers["content-security-policy"] == PRODUCT_CSP


def test_the_legacy_app_keeps_its_paths_and_its_own_csp_on_a_fresh_clone(repo: Path) -> None:
    """No demo manifest, no runs directory, no build: its pages answer, and do not crash."""
    with client_for(make_settings(repo)) as client:
        demo = client.get("/demo")
        run = client.get("/runs/nothing/")
    assert demo.status_code == 404 and run.status_code == 404
    for response in (demo, run):
        assert response.headers["content-security-policy"].startswith("default-src 'none'")


@pytest.mark.parametrize("path", ["/demo", "/flow", "/world", "/problem", "/decision/trace"])
def test_the_legacy_pages_answer_through_the_outer_app(path: str) -> None:
    """The real repository, without booting: the lifespan is what writes var/. A fresh clone lacks
    some pinned files, which those pages report with a 409."""
    app = create_product_app(make_settings(REPO))
    response = TestClient(app).get(path)
    assert response.status_code in (200, 409)
    assert "text/html" in response.headers["content-type"]
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
