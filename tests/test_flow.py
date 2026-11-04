"""The product flow page (/flow, cairoflow_api.flow): intersection to decision on pinned records.

Reads the committed records and, for the inspector, the exported World Model view (or the local
snapshot). Nothing is written into the repository, no model is called and nothing is simulated.
The synthetic dicts below test the problem rule only; they are never shown by the app.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cairoflow_api import flow
from cairoflow_api.app import create_app
from cairoflow_api.demo import load_demo, read_manifest
from cairoflow_api.flow import find_problem, load_world_view
from cairoflow_contracts.worldview import WorldView

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "data/demo/ngsim-int2-no-footage.json"
SECTIONS = ("intersection", "model", "state", "problem", "interventions", "results", "decision")


@pytest.fixture(scope="module")
def demo():
    return load_demo(read_manifest(MANIFEST), REPO)


@pytest.fixture(scope="module")
def view_loaded(demo):
    load = load_world_view(demo, REPO)
    if load.view is None:
        pytest.skip(f"no World Model view here: {load.problem}")
    return load


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path, MANIFEST, REPO))


def test_the_page_walks_the_seven_steps_without_scripts(client: TestClient, view_loaded) -> None:
    response = client.get("/flow")
    assert response.status_code == 200
    page = response.text
    ids = re.findall(r'<section class="step" id="(\w+)"', page)
    assert ids == list(SECTIONS)
    assert "<script" not in page.lower() and "javascript:" not in page.lower()
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert "Decision support only" in page


def test_the_page_says_the_world_model_view_is_not_pinned(client: TestClient, view_loaded) -> None:
    """The pinned records are checked against the manifest; the WorldView export is Git-ignored
    and no manifest pins it, so the page does not call what it shows committed records only."""
    page = client.get("/flow").text
    intro = page.split('id="intersection"')[1].split('id="model"')[0]
    assert "committed records only" not in intro
    if view_loaded.origin.startswith("exported view"):
        assert flow.WORLD_VIEW_FILE in intro and "Git-ignored" in intro
        assert "no manifest pins" in intro
    state = page.split('id="state"')[1].split('id="problem"')[0]
    assert view_loaded.origin in state  # the traffic state names where its numbers come from


def test_the_index_links_to_the_flow(client: TestClient) -> None:
    assert 'href="/flow"' in client.get("/").text


def test_an_entity_shows_its_source_relations_and_the_evidence_the_model_read(
    client: TestClient, view_loaded
) -> None:
    page = client.get("/flow", params={"e": "movement:NE"}).text
    panel = page.split('<div class="panel">')[1].split("</div></div>")[0]
    assert "N to E (left)" in panel and "movement:NE" in panel
    assert "TrafficWorldModel" in panel and "movements[NE]" in panel
    assert "mv.NE" in panel and "133 vehicles in 900 s" in panel  # the item the model was given
    assert "model-generated, not evidence" in panel  # what cites it is labelled
    assert "Poisson" in panel  # the scenario record's interval, as recorded


def test_a_lane_shows_where_its_geometry_was_recorded(client: TestClient, view_loaded) -> None:
    page = client.get("/flow", params={"e": "lane:S1"}).text
    panel = page.split('<div class="panel">')[1]
    assert "NGSIM lane_id" in panel and "approximated" in panel


def test_an_unknown_or_hostile_entity_falls_back_to_the_intersection(
    client: TestClient, view_loaded
) -> None:
    for hostile in ("<script>alert(1)</script>", "lane:NOPE", "x" * 500, "../../etc/passwd"):
        response = client.get("/flow", params={"e": hostile})
        assert response.status_code == 200
        assert "<script>alert(1)</script>" not in response.text
        assert "intersection:ngsim-int2" in response.text


def model_step(page: str) -> str:
    return page.split('id="model"')[1].split('id="state"')[0]


def test_a_requested_entity_or_zoom_that_is_not_in_the_view_is_named(
    client: TestClient, view_loaded
) -> None:
    """A typo in a recording URL must not pass for the page it meant: the page still answers
    (200, the intersection and the core view), and says what it could not show."""
    for typo in ("movment:NE", "movement:XX", "queue:N99", "<script>alert(1)</script>"):
        response = client.get("/flow", params={"e": typo})
        assert response.status_code == 200
        model = model_step(response.text)
        assert 'class="abstain notfound"' in model, typo
        assert html.escape(typo) in model and "is not in this view" in model
        assert "<script>alert(1)</script>" not in response.text
    long = client.get("/flow", params={"e": "x" * 500}).text
    assert 'class="abstain notfound"' in long and "x" * 81 not in long  # named, cut short
    zoom = model_step(client.get("/flow", params={"zoom": "fulll"}).text)
    assert 'class="abstain notfound"' in zoom and "fulll" in zoom and "core" in zoom
    for good in ({"e": "movement:NE"}, {"zoom": "full"}, {"e": "lane:S1", "zoom": "core"}, {}):
        assert "notfound" not in client.get("/flow", params=good).text, good


def test_layers_and_zoom_are_sanitised_and_change_the_picture(
    client: TestClient, view_loaded
) -> None:
    full = client.get("/flow", params={"layers": "lqmgsc", "zoom": "full"}).text
    plain = client.get("/flow", params={"layers": "zz<>", "zoom": "<x>"}).text
    assert 'class="cz' in full and 'class="cz' not in plain
    assert 'class="lane' not in plain and 'class="queue' not in plain
    assert "<x>" not in plain
    core = client.get("/flow").text
    assert 'viewBox="-34 -50 70 100"' in core and 'viewBox="-34 -50 70 100"' not in full


def test_queues_are_drawn_as_bounds_where_the_data_end(client: TestClient, view_loaded) -> None:
    page = client.get("/flow").text
    assert 'class="queue censored' in page  # N and S queues reach the end of the observed lane
    assert "at least" in page


def test_the_camera_is_said_to_play_no_part_in_the_decision(
    client: TestClient, view_loaded
) -> None:
    page = client.get("/flow").text
    assert "Camera evidence is not part of this World Model" in page
    assert "no camera track feeds the simulation" in page
    assert "No camera evidence: the World Model snapshot has no camera layer" in page


def test_the_decision_abstains_and_says_why(client: TestClient, view_loaded) -> None:
    page = client.get("/flow").text
    decision = page.split('id="decision"')[1]
    assert "Selected: none. Recommended: none." in decision
    assert "Nothing is recommended" in decision
    assert "no support" in decision  # the ensemble's verdict for each candidate
    assert "does not rest on" in decision
    assert "partially validated" in decision  # the baseline's own assessment, quoted
    assert "needs &#8220;validated&#8221;" in decision


def test_the_problem_is_a_rule_on_observed_values_and_the_model_text_is_boxed(
    client: TestClient, view_loaded
) -> None:
    problem = client.get("/flow").text.split('id="problem"')[1].split('id="interventions"')[0]
    assert "In the observed 15 minutes" in problem and "most vehicles stop at least once" in problem
    assert "does not say why" in problem
    assert "model-generated text, not evidence" in problem


def test_the_problem_tables_carry_the_label_the_validation_record_gives(
    client: TestClient, demo
) -> None:
    """Every observed value of the validation record is labelled "derived" (aggregated by our
    fixed rules, ADR-019): the problem's tables and its sentence say approximated, as the queue
    table and /demo do, never "observed (NGSIM positions)"."""
    problem = find_problem(demo.records["validation"])
    readings = [*problem.stopping, *problem.end_of_red, *problem.queues]
    by_key = {c["key"]: c["observed"]["label"] for c in demo.records["validation"]["comparisons"]}
    assert {by_key[r.key] for r in readings} == {"derived"}  # as committed
    assert all(r.label == by_key[r.key] for r in readings)
    page = client.get("/flow").text
    section = page.split('id="problem"')[1].split('id="interventions"')[0]
    assert 'class="badge kind-observed"' not in section
    rows = re.findall(r'<tr class="fig">(.*?)</tr>', section, re.S)
    assert len(rows) == len(readings)
    assert all('class="badge kind-derived"' in row for row in rows)
    sentence = section.split('<div class="decision">')[1].split("</div>")[0]
    assert 'class="badge kind-derived"' in sentence


def test_without_a_snapshot_or_an_export_the_page_says_so_and_fills_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(flow, "WORLD_VIEW_FILE", "data/demo/does-not-exist.json")
    monkeypatch.setattr(flow, "SNAPSHOT_GLOB", "data/demo/no-such-dir/*/world_model.json")
    response = client.get("/flow")
    assert response.status_code == 200
    page = response.text
    assert "The World Model view cannot be shown" in page
    assert "scripts/export_world_view.py" in page
    assert 'class="wm"' not in page and "<svg" not in page
    assert [s for s in SECTIONS if f'id="{s}"' not in page] == []  # the other steps still show


def test_a_view_of_another_world_model_is_refused(
    client: TestClient, view_loaded, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = json.loads(view_loaded.view.model_dump_json())
    data["world_model_sha256"] = "0" * 64
    data["view_sha256"] = WorldView.digest_of(data)
    other = tmp_path / "other-view.json"
    other.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(flow, "WORLD_VIEW_FILE", str(other))  # absolute: replaces the repo root
    page = client.get("/flow").text
    assert "another world model than the baseline replays" in page
    assert "<svg" not in page


def test_a_view_that_does_not_hash_its_content_is_refused(
    client: TestClient, view_loaded, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = json.loads(view_loaded.view.model_dump_json())
    data["entities"][0]["label"] = "tampered"
    other = tmp_path / "tampered-view.json"
    other.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(flow, "WORLD_VIEW_FILE", str(other))
    page = client.get("/flow").text
    assert "is invalid" in page and "view_sha256" in page
    assert "<svg" not in page


def test_a_changed_pinned_record_stops_the_flow_like_the_demo(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["files"]["scenario"]["sha256"] = "0" * 64
    changed = tmp_path / "manifest.json"
    changed.write_text(json.dumps(manifest), encoding="utf-8")
    response = TestClient(create_app(tmp_path, changed, REPO)).get("/flow")
    assert response.status_code == 409 and "differs from the demo manifest" in response.text


def test_the_flow_needs_a_demo_manifest(tmp_path: Path) -> None:
    assert TestClient(create_app(tmp_path, None, REPO)).get("/flow").status_code == 404


# --- the problem rule ----------------------------------------------------------------------------


def _validation(stop_low: float, queue: float, censored: bool) -> dict:
    def row(key: str, value: float, interval, lower_bound: bool = False) -> dict:
        return {
            "key": key, "quantity": key, "unit": "share", "status": "comparable",
            "observed": {"value": value, "interval": interval, "lower_bound": lower_bound},
            "simulated": {"value": value},
        }  # fmt: skip

    return {
        "comparisons": [
            row("stopped_share.A_through", 0.7, [stop_low, 0.8]),
            row("stopped_share.B_left", 0.3, [0.2, 0.4]),
            row("end_of_red_queue.A_all", 5.0, [3.0, 7.0]),
            row("queue_max.A", queue, None, censored),
        ],
        "not_validated": [{"quantity": "delay", "reason": "no observed counterpart"}],
    }


def test_a_movement_is_flagged_only_when_the_whole_interval_is_above_one_half() -> None:
    flagged = find_problem(_validation(0.55, 10.0, False))
    assert [r.key for r in flagged.stopping if r.flagged] == ["stopped_share.A_through"]
    assert "A through" in flagged.sentence and "70%" in flagged.sentence
    straddling = find_problem(_validation(0.45, 10.0, False))
    assert not [r for r in straddling.stopping if r.flagged]
    assert straddling.sentence.startswith("No movement or approach meets")


def test_a_queue_is_flagged_only_when_it_is_long_and_censored() -> None:
    assert [r.flagged for r in find_problem(_validation(0.1, 80.0, True)).queues] == [True]
    assert [r.flagged for r in find_problem(_validation(0.1, 80.0, False)).queues] == [False]
    assert [r.flagged for r in find_problem(_validation(0.1, 20.0, True)).queues] == [False]
    assert "outrun the observed lanes" in find_problem(_validation(0.1, 80.0, True)).sentence


def test_the_problem_on_the_real_record_names_what_the_data_show(demo) -> None:
    problem = find_problem(demo.records["validation"])
    names = {r.key for r in problem.stopping if r.flagged}
    assert {"stopped_share.N_left", "stopped_share.S_through"} <= names
    assert {r.key for r in problem.queues if r.flagged} == {"queue_max.N", "queue_max.S"}
    assert all(r.status for r in problem.stopping)
