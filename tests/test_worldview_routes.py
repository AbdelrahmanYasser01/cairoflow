"""The WorldView inspector routes: /world, /world/{entity_id}, /problem and /decision/trace.

test_flow.py covers the /flow page and test_acceptance_walk.py the reviewer's nine steps (every
entity linked from /world, a lane's details, a movement traced to the NGSIM hash, the problem's
statement and sources, the decision trace's hashes); neither is repeated here. This file covers
what they lack: the kind badge each entity gets from its provenance, confidence shown only where
recorded, "not available" never drawn as zero, the queue series and the reconstructed signal
timeline drawn from the world model, the camera layer's typed break, the one line a fresh clone
sees without the Git-ignored snapshot, hostile ids, the snapshot builder's determinism, and the
links from /flow. Every page reads the real snapshot (data/demo/ngsim-int2-worldview.json, written
by scripts/build_worldview_snapshot.py); a test that needs it, the world model or a run directory
skips where those Git-ignored files are absent. A test that edits the snapshot writes its copy to
tmp_path and points the app at it; nothing in the repository is written.
"""

from __future__ import annotations

import html
import importlib.util
import json
import re
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from cairoflow_api import flow, world
from cairoflow_api.app import create_app
from cairoflow_api.demo import load_demo, read_manifest
from cairoflow_contracts.evidence import CameraLayer, TrafficWorldModel
from cairoflow_contracts.worldview import WorldView
from cairoflow_evaluation.problem_statement import problem_statement_from_records

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "data/demo/ngsim-int2-no-footage.json"
SNAPSHOT = REPO / "data/demo/ngsim-int2-worldview.json"
WORLD_MODEL = REPO / "data/processed/ngsim-int2/20261028T074304Z/world_model.json"
CSV = REPO / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
NGSIM_SHA256 = "c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e"
MISSING_LINE = "WorldView snapshot not present - run scripts/build_worldview_snapshot.py"
CAMERA_BREAK = "not connected to the World Model (calibration not accepted, ADR-017/018)"
BADGES = ("dataset-derived", "World Model", "camera probe", "simulated", "AI-generated")
DEMO_RUN = json.loads(MANIFEST.read_text(encoding="utf-8"))["run_dir"]

needs_snapshot = pytest.mark.skipif(
    not SNAPSHOT.is_file(), reason="the WorldView snapshot is Git-ignored (ADR-033 decision 9)"
)
needs_world_model = pytest.mark.skipif(
    not (SNAPSHOT.is_file() and WORLD_MODEL.is_file() and CSV.is_file()),
    reason="the world model snapshot and the NGSIM file are Git-ignored",
)
needs_run = pytest.mark.skipif(
    not (REPO / "experiments/runs" / DEMO_RUN / "report.json").is_file(),
    reason="the demo's run directory is Git-ignored",
)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path, MANIFEST, REPO))


def get(client: TestClient, url: str, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status, f"{url}: {response.status_code}"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    page = response.text
    assert "<script" not in page.lower() and "javascript:" not in page.lower()
    assert "Traceback" not in page
    return page


def text_of(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def block(page: str, name: str) -> str:
    """The text of one `<section id=name>` of an inspector page."""
    found = re.search(rf'<section id="{name}"(.*?)</section>', page, re.S)
    assert found, f"no {name} section"
    return text_of(found.group(1))


def entity_url(entity_id: str) -> str:
    return f"/world/{quote(entity_id, safe='')}"


# the kind each entity type of this snapshot has, from what its SourceRef names (ADR-033): NGSIM
# movement counts and queue samples are dataset-derived; the geometry (lanes, gates), the
# reconstructed signal timeline, the chord-rule conflict zones and the junction itself are the
# World Model's own constructs. Written out here, not read from world.entity_badge.
KIND_OF_TYPE = {
    "movement": "dataset-derived",
    "queue": "dataset-derived",
    "lane": "World Model",
    "approach": "World Model",
    "signal_head": "World Model",
    "conflict_zone": "World Model",
    "intersection": "World Model",
}


def panel_row(page: str, name: str) -> str:
    """The raw HTML of one row of the entity panel."""
    found = re.search(rf"<dt>{re.escape(name)}</dt><dd>(.*?)</dd>", page, re.S)
    assert found, f"no {name} row"
    return found.group(1)


def kind_row(page: str) -> str:
    """The entity panel's Kind row: the badge this entity is given."""
    return panel_row(page, "Kind")


def badge_html(name: str) -> str:
    return f'<span class="badge kind-{name.lower().replace(" ", "-")}">{name}</span>'


def snapshot() -> WorldView:
    return WorldView.model_validate_json(SNAPSHOT.read_bytes())


def edited_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edit) -> WorldView:
    """The real snapshot with `edit` applied and re-hashed, served from tmp_path."""
    data = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    edit(data)
    data["view_sha256"] = WorldView.digest_of(data)
    path = tmp_path / "edited-worldview.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(flow, "WORLD_VIEW_FILE", str(path))
    return WorldView.model_validate(data)


# --- /world ------------------------------------------------------------------------------------


@needs_snapshot
def test_the_index_names_its_snapshot_and_says_it_is_an_offline_replay(client) -> None:
    page = get(client, "/world")
    shown = text_of(page)
    view = snapshot()
    assert "offline replay" in shown.lower()
    assert not re.search(r"\b(live|real-time)\b", shown.lower())
    assert view.view_sha256 in shown and view.geometry_sha256 in shown
    assert f"projection version {view.view_version}" in shown
    for badge in BADGES:  # the legend names all five kinds
        assert badge in shown
    for kind in {e.entity_type for e in view.entities}:
        assert f"{kind.replace('_', ' ')} ({len(view.of_type(kind))})" in shown
    assert '<svg class="wm"' in page  # the junction map, its links go to the inspector
    assert 'href="/flow?e=' not in page


@needs_snapshot
def test_the_camera_layer_is_a_typed_break_not_a_link(client) -> None:
    page = get(client, "/world")
    camera = block(page, "camera")
    layer_file = REPO / world.CAMERA_LAYER_FILE
    layer = CameraLayer.model_validate_json(layer_file.read_bytes())
    assert layer.calibration_accepted is False  # as committed
    assert "camera probe" in camera and "not_accepted" in camera
    assert CAMERA_BREAK in camera
    assert "world_entity <- observation" in camera
    assert layer.tracks_sha256 in camera and world.CAMERA_LAYER_FILE in camera
    assert "/world/camera" not in page  # no entity is made up for it


def test_without_the_snapshot_every_inspector_page_is_one_line(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow, "WORLD_VIEW_FILE", "data/demo/does-not-exist.json")
    for url in ("/world", entity_url("lane:S1"), entity_url("queue:S1")):
        page = get(client, url)
        main = page.split("<main>", 1)[1].split("</main>", 1)[0]
        assert text_of(main).strip() == MISSING_LINE
        assert "<svg" not in page


# --- /world/{entity_id} --------------------------------------------------------------------------


@needs_snapshot
def test_a_lane_is_a_world_model_entity_whose_trace_breaks_at_the_geometry(client) -> None:
    page = get(client, entity_url("lane:S1"))
    shown = text_of(page)
    assert kind_row(page) == badge_html("World Model")
    assert "IntersectionGeometry lanes[S1]" in shown
    # the hash beside the source is the hash of the record the source row names: the geometry
    view = snapshot()
    assert view.geometry_sha256 in panel_row(page, "Source hash")
    assert view.world_model_sha256 not in panel_row(page, "Source hash")
    assert view.world_model_sha256 in panel_row(page, "Projection")  # what the view projects
    assert "NGSIM lane_id" in shown  # where the geometry record says it was drawn from
    missing = block(page, "missing")
    assert "confidence: not recorded" in missing  # no uncertainty was recorded for a lane
    assert "Confidence" not in block(page, "entity")
    assert "camera" in missing  # the snapshot's own absences are listed too
    trace = block(page, "trace")
    if WORLD_MODEL.is_file():  # the walk from the lane stops at the geometry: no source record
        assert "Reaches a source record: no" in trace
        assert "missing_link · world_entity <- observation" in trace
        assert "DatasetFile" not in trace and NGSIM_SHA256 not in trace
        assert "evidence trace: missing_link at world_entity <- observation" in missing
    else:  # without the Git-ignored world model the walk is said to be unavailable
        assert "Not available here" in trace and "Reaches a source record" not in trace


@needs_world_model
def test_a_movement_is_dataset_derived_and_walks_to_the_ngsim_file(client) -> None:
    page = get(client, entity_url("movement:SN"))
    entity = block(page, "entity")
    recorded = snapshot().entity("movement:SN").uncertainty
    assert kind_row(page) == badge_html("dataset-derived")  # the entity's own row, not the trace
    assert snapshot().world_model_sha256 in panel_row(page, "Source hash")  # TrafficWorldModel
    assert f"95% interval [{recorded.interval95[0]:g}, {recorded.interval95[1]:g}]" in entity
    trace = block(page, "trace")
    assert "WorldEntity movement:SN" in trace
    assert "NgsimMovementCount mv.SN@" in trace and "NgsimTrackPoints" in trace
    assert f"DatasetFile {NGSIM_SHA256}" in trace
    assert "world_entity <- observation" in trace and "observation <- source_record" in trace
    assert "MovementCount.tracks (ground track ids)" in trace  # the basis of each link


@needs_world_model
def test_a_queue_page_draws_its_recorded_series(client) -> None:
    page = get(client, entity_url("queue:S1"))
    entity = snapshot().entity("queue:S1")
    series = re.search(r'<svg class="series"(.*?)</svg>', page, re.S)
    assert series, "no queue series"
    points = re.search(r'<polyline class="q" points="([^"]+)"', series.group(1)).group(1)
    assert len(points.split()) == entity.attributes["samples"]
    censored = series.group(1).count('class="cens"')
    assert censored == entity.attributes["censored_samples"]
    shown = text_of(block(page, "series"))
    assert f"{entity.attributes['samples']} per-second samples" in shown and "at least" in shown


@needs_world_model
def test_a_censored_queue_says_at_least_and_names_what_each_count_counts(client) -> None:
    """Where the longest sample reached the lane's far end, the series and its axis say "at
    least", like the /flow map. Coverage names the population of each count it gives, so the
    Confidence note's total (per-second and end-of-red samples together) can be checked on it."""
    model = TrafficWorldModel.model_validate_json(WORLD_MODEL.read_bytes())
    seen_cut = False
    for entity in snapshot().of_type("queue"):
        samples = world.queue_series(model, entity.entity_id.split(":", 1)[1])
        if not samples:
            continue
        page = get(client, entity_url(entity.entity_id))
        top = max(q.length_m for q in samples)
        cut = any(q.censored for q in samples if q.length_m == top)
        seen_cut |= cut
        series = block(page, "series")
        axis = re.search(r'<svg class="series".*?</svg>', page, re.S).group(0)
        bound = "&#8805; " if cut else ""
        assert f"the longest is {'at least ' if cut else ''}{top:.1f} m" in series
        assert f">{bound}{top:.1f} m</text>" in axis
        a = entity.attributes
        coverage = text_of(panel_row(page, "Coverage"))
        assert f"{a['samples']} per-second samples in the" in coverage
        if a["censored_samples"]:
            assert f"{a['censored_samples']} of them reach the lane's far end" in coverage
        if a["end_of_red_samples"]:
            assert f"{a['end_of_red_samples']} end-of-red samples" in coverage
        note = entity.uncertainty.note if entity.uncertainty else None
        if note:  # "227 samples reach the lane's far end": counted over both populations
            n = a["censored_samples"] + a["end_of_red_censored"]
            assert note.startswith(f"{n} samples")
            total = a["samples"] + a["end_of_red_samples"]
            assert f"{n} of all {total} samples (both kinds) reach the lane's far end" in coverage
    assert seen_cut  # E1 and others: the longest sample is itself censored


@needs_world_model
def test_a_signal_head_shows_its_reconstructed_timeline(client) -> None:
    page = get(client, entity_url("signal_head:N:left"))
    head = snapshot().entity("signal_head:N:left")
    timeline = re.search(r'<svg class="signal"(.*?)</svg>', page, re.S)
    assert timeline, "no signal timeline"
    shown = text_of(block(page, "series"))
    assert "reconstructed" in shown and "no signal log" in shown
    for state in ("green", "yellow", "red", "unknown"):
        seconds = head.attributes[f"{state}_s"]
        assert f"{state} {seconds:g} s" in shown
        assert (f'class="st {state}"' in timeline.group(1)) == (seconds > 0)


def test_without_the_world_model_the_series_and_the_trace_are_listed_missing(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not SNAPSHOT.is_file():
        pytest.skip("the WorldView snapshot is Git-ignored")
    monkeypatch.setattr(flow, "SNAPSHOT_GLOB", "data/demo/no-such-dir/*/world_model.json")
    page = get(client, entity_url("queue:S1"))
    assert '<svg class="series"' not in page and "<polyline" not in page
    missing = block(page, "missing")
    assert "queue series: not available" in missing
    assert "evidence trace: not available" in missing
    assert "world_model.json" in missing  # names what would supply it


@needs_snapshot
def test_not_available_is_never_rendered_as_zero(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def drop_flow(data: dict) -> None:
        for e in data["entities"]:
            if e["entity_id"] == "movement:SN":
                e["attributes"]["flow_veh_per_h"] = None

    edited_snapshot(tmp_path, monkeypatch, drop_flow)
    page = get(client, entity_url("movement:SN"))
    entity = block(page, "entity")
    assert "flow_veh_per_h not available" in entity
    assert not re.search(r"flow_veh_per_h (0|None)\b", entity)
    assert "flow_veh_per_h: not recorded in the snapshot" in block(page, "missing")


@needs_snapshot
def test_an_unknown_or_hostile_entity_id_is_a_404_that_echoes_nothing_raw(client) -> None:
    for hostile in ("<script>alert(1)</script>", "lane:NOPE", "x" * 500, "lane:S1\x00"):
        page = get(client, entity_url(hostile), status=404)
        assert "<script>alert(1)</script>" not in page
        assert 'href="/world"' in page


@needs_snapshot
def test_every_entity_page_renders(client) -> None:
    for entity in snapshot().entities:
        page = get(client, entity_url(entity.entity_id))
        shown = text_of(page)
        assert entity.entity_id in shown and "missing information" in shown.lower()
        # exactly one badge in the Kind row, the one its type has; the nav link "World Model
        # inspector" and the trace's badges do not count
        assert kind_row(page) == badge_html(KIND_OF_TYPE[entity.entity_type]), entity.entity_id


# --- the flow pages ------------------------------------------------------------------------------


@needs_snapshot
def test_the_offline_banner_does_not_call_git_ignored_inputs_committed(client) -> None:
    """The inspector reads the Git-ignored snapshot, world model and NGSIM file, and /problem and
    /decision/trace read a Git-ignored run directory: the banner says so."""
    for url in ("/world", entity_url("lane:S1"), "/problem", "/decision/trace"):
        page = get(client, url)
        banner = text_of(re.search(r'<p class="say">(.*?)</p>', page, re.S).group(1))
        assert banner.lower().startswith("offline replay"), url
        assert "of committed records" not in banner and "Git-ignored" in banner, url


def test_the_flow_and_the_index_link_to_the_new_pages(client) -> None:
    page = get(client, "/flow")
    for url in ("/world", "/problem", "/decision/trace"):
        assert f'href="{url}"' in page
    assert 'href="/world"' in get(client, "/")


def test_the_problem_page_labels_each_kind_and_names_its_entity(client) -> None:
    page = get(client, "/problem")
    problem = problem_statement_from_records(REPO)
    if problem.hypotheses:  # the pinned Muse reply is a Git-ignored run file
        assert "AI-generated" in block(page, "hypotheses")
    else:  # a clean clone: the page says why none is attached, and labels nothing as model text
        assert problem.hypotheses_note in text_of(page)
    assert "AI-generated" not in block(page, "observed") + block(page, "members")
    assert "dataset-derived" in block(page, "observed")
    assert "simulated" in block(page, "members")
    # each Holm-confirmed difference is a simulated result (ConfirmedImprovement.label), and its
    # own cell says so: the Baseline cell's badge on the same row does not count for it
    members = re.search(r'<section id="members"(.*?)</section>', page, re.S).group(1)
    holm_cells = [c for c in re.findall(r"<td>(.*?)</td>", members, re.S) if "Holm" in c]
    confirmed = [i for m in problem.members for i in m.improvements]
    assert confirmed and all(i.label == "simulated" for i in confirmed)
    assert len(holm_cells) == sum(1 for m in problem.members if m.improvements)
    for cell in holm_cells:
        assert cell.count('class="badge kind-simulated"') == cell.count("Holm"), cell
    assert 'href="/world/approach%3AS"' in page
    assert "offline replay" in text_of(page).lower()


@needs_run
def test_the_decision_trace_shows_the_decision_as_recorded_and_where_the_chain_breaks(
    client,
) -> None:
    page = get(client, "/decision/trace")
    record = json.loads((REPO / f"experiments/records/{DEMO_RUN}.json").read_text(encoding="utf-8"))
    report = record["report"]
    decision = block(page, "decision")
    assert f"Selected: {report['selected_intervention_id'] or 'none'}" in decision
    assert report["selection_reason"] in decision
    assessment = report.get("baseline_assessment")
    if assessment:  # the decision as recorded includes the baseline's assessment
        assert f"Baseline assessment, as recorded: {assessment['status']}" in decision
        assert assessment["file_sha256"] in decision
    chain = block(page, "chain")
    assert "missing_link" in chain and "experiment <- intervention" in chain
    pairs = block(page, "pairs")
    assert "consistency check only" in pairs
    # only the baseline manifest hash, the baseline directory and the seeds are compared with
    # the record (pair_identity_view_from_record); the pair hashes are not
    assert "checked against the hashes the record cites" not in pairs
    assert "baseline manifest hash, baseline directory and seed list" in pairs
    assert "not compared with any hash the record cites" in pairs


@needs_run
@needs_world_model
def test_the_decision_chain_is_rebuilt_when_the_world_model_and_ngsim_file_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chain is assembled from the world-model and NGSIM globs, not report.json alone: a
    running server must not keep showing a walk to a source that is no longer there."""
    demo = load_demo(read_manifest(MANIFEST), REPO)
    before = world.load_decision_trace(demo, REPO)
    assert before.chain is not None and before.chain.reaches_source
    assert before.chain.source_sha256 == NGSIM_SHA256
    real = world._files
    gone = (flow.SNAPSHOT_GLOB, world.DATASET_GLOB)
    monkeypatch.setattr(world, "_files", lambda repo, p: [] if p in gone else real(repo, p))
    after = world.load_decision_trace(demo, REPO)
    assert after.chain is not None and not after.chain.reaches_source
    assert after.chain.source_sha256 != NGSIM_SHA256


@needs_run
def test_the_pairs_are_rebuilt_when_the_record_they_are_checked_against_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    demo = load_demo(read_manifest(MANIFEST), REPO)
    assert world.load_decision_trace(demo, REPO).pairs is not None
    record = json.loads((REPO / world.RECORDS_DIR / f"{DEMO_RUN}.json").read_text("utf-8"))
    record["seeds"] = record["seeds"][:-1]  # the record no longer cites the run's seeds
    (tmp_path / f"{DEMO_RUN}.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(world, "RECORDS_DIR", str(tmp_path))
    after = world.load_decision_trace(demo, REPO)
    assert after.pairs is None
    assert any("the pairs cannot be rebuilt" in p and "seeds" in p for p in after.problems)


def test_the_decision_trace_without_its_run_says_so(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(world, "RUNS_DIR", "experiments/no-such-runs")
    shown = text_of(get(client, "/decision/trace"))
    assert f"experiments/no-such-runs/{DEMO_RUN}" in shown and "not present" in shown


# --- the snapshot builder ------------------------------------------------------------------------


def _builder():
    path = REPO / "scripts/build_worldview_snapshot.py"
    spec = importlib.util.spec_from_file_location("build_worldview_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@needs_world_model
def test_the_builder_is_deterministic_and_reproduces_the_snapshot(tmp_path: Path, capsys) -> None:
    builder = _builder()
    first, second = builder.build(REPO, MANIFEST), builder.build(REPO, MANIFEST)
    assert builder.serialise(first) == builder.serialise(second)
    assert builder.serialise(first) == SNAPSHOT.read_text(encoding="utf-8")
    out = tmp_path / "view.json"
    assert builder.main(["--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert first.world_model_sha256 in printed and f"projection {first.view_version}" in printed
    assert out.read_text(encoding="utf-8") == builder.serialise(first)
    out.write_text("{}", encoding="utf-8")
    assert builder.main(["--out", str(out)]) == 1  # a different file is never overwritten
    assert out.read_text(encoding="utf-8") == "{}"
