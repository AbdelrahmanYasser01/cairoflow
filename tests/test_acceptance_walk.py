"""The acceptance walk: nine steps a reviewer takes, over real HTTP routes (G3a ratchet).

Each step calls the app through the test client, as test_flow.py does. Steps 1, 6, 7 and 8 pass
through /flow; steps 2-5 and 9 through the pages (/world, /world/{entity_id},
/problem, /decision/trace). They were `xfail(strict=True)` until those pages landed, and the marks
were removed in the change that merged them; a step that is not built again would fail. Steps
that read Git-ignored inputs (the WorldView export, the demo run) skip without them.
Numbers asserted here are the ones the committed records state (see test_problem_statement.py).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import quote, unquote

import pytest
from fastapi.testclient import TestClient

from cairoflow_api.app import create_app
from cairoflow_contracts.worldview import WorldView
from cairoflow_evaluation.problem_statement import problem_statement_from_records

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "data/demo/ngsim-int2-no-footage.json"
SNAPSHOT = REPO / "data/demo/ngsim-int2-worldview.json"
NGSIM_SHA256 = "c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e"
KIND_BADGES = ("dataset-derived", "World Model", "camera probe", "simulated", "AI-generated")
REPLAY_LABEL = "n = 20 seeds on one vehicle realisation; conditional on it"

DEMO_RUN = json.loads(MANIFEST.read_text(encoding="utf-8"))["run_dir"]
DEMO_RECORD = REPO / f"experiments/records/{DEMO_RUN}.json"
WORLD_MODEL = REPO / "data/processed/ngsim-int2/20261028T074304Z/world_model.json"
CSV = REPO / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
BREAK_CODES = r"\b(missing_link|unknown_record|wrong_entity_type|hash_mismatch|wrong_stage|cycle|not_accepted)\b"  # noqa: E501

needs_snapshot = pytest.mark.skipif(
    not SNAPSHOT.is_file(),
    reason="no WorldView export here: it is held out of Git under ADR-033 decision 9",
)
needs_runs = pytest.mark.skipif(
    not all(p.is_file() for p in (REPO / "experiments/runs" / DEMO_RUN / "report.json",
                                  WORLD_MODEL, CSV)),
    reason="the demo run directory, the world model and the NGSIM file are Git-ignored",
)  # fmt: skip


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path, MANIFEST, REPO))


def get(client: TestClient, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200, f"{url}: {response.status_code}"
    assert "content-security-policy" in response.headers
    return response.text


def text_of(page: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def section(page: str, name: str) -> str:
    found = re.search(rf'<section class="step" id="{name}"(.*?)</section>', page, re.S)
    assert found, f"/flow has no {name} section"
    return text_of(found.group(1))


def snapshot() -> WorldView:
    return WorldView.model_validate_json(SNAPSHOT.read_bytes())


# --- 1. open the intersection --------------------------------------------------------------------


def test_step_1_open_the_intersection(client: TestClient) -> None:
    shown = section(get(client, "/flow"), "intersection")
    assert "Lankershim Boulevard at Universal Hollywood Drive" in shown
    assert "NGSIM" in shown and "CC BY-SA 3.0" in shown
    assert "Decision support only" in shown


# --- 2. navigate entities and relationships ------------------------------------------------------


@needs_snapshot
def test_step_2_navigate_entities_and_relationships(client: TestClient) -> None:
    page = get(client, "/world")
    linked = {unquote(link) for link in re.findall(r'href="/world/([^"#?]+)"', page)}
    view = snapshot()
    assert linked == {e.entity_id for e in view.entities}
    shown = text_of(page)
    assert all(relation in shown for relation in {r.relation for r in view.relations})


# --- 3. select an entity and inspect details -----------------------------------------------------


@needs_snapshot
def test_step_3_select_an_entity_and_inspect_its_details(client: TestClient) -> None:
    shown = text_of(get(client, f"/world/{quote('lane:S1')}"))
    entity = next(e for e in snapshot().entities if e.entity_id == "lane:S1")
    assert "lane:S1" in shown
    assert any(badge in shown for badge in KIND_BADGES)
    # this entity's own labels, not page boilerplate: its recorded kind and where it comes from
    assert entity.evidence_kind in shown and entity.source.path in shown
    assert "missing information" in shown.lower()
    assert snapshot().world_model_sha256 in shown  # the source hash, in full


# --- 4. trace a measurement to evidence ----------------------------------------------------------


@needs_snapshot
def test_step_4_trace_a_measurement_to_its_evidence(client: TestClient) -> None:
    shown = text_of(get(client, f"/world/{quote('movement:SN')}"))
    assert "movement:SN" in shown
    # the chain walk from this entity ends at the NGSIM file's hash, or shows a typed break:
    # its code and the named link (provenance_chain.TypedBreak), not a legend
    assert NGSIM_SHA256 in shown or (
        re.search(BREAK_CODES, shown) and re.search(r"\w+ <- \w+", shown)
    )


# --- 5. inspect a real problem with its measurements ---------------------------------------------


def test_step_5_inspect_a_real_problem_with_its_measurements(client: TestClient) -> None:
    shown = text_of(get(client, "/problem"))
    problem = problem_statement_from_records(REPO)
    assert problem.statement in shown
    assert all(condition in shown for condition in problem.failure_conditions)
    assert all(source.sha256 in shown for source in problem.sources)
    assert all(q.key in shown for q in problem.observed)
    if problem.hypotheses:  # each one shown, as model text
        assert "AI-generated" in shown
        assert all(h.statement in shown for h in problem.hypotheses)
    else:  # and when the reply is absent, the page says so
        assert problem.hypotheses_note in shown


# --- 6. inspect an intervention with its constraints ---------------------------------------------


def test_step_6_inspect_an_intervention_with_its_constraints(client: TestClient) -> None:
    shown = section(get(client, "/flow"), "interventions")
    assert "Only signal-timing changes are allowed (ADR-015)" in shown
    for candidate in ("I1", "I2", "I3"):
        assert candidate in shown
    assert shown.count("accepted") >= 3 and "cycle 99 s" in shown  # the validator's verdicts
    assert "model-generated" in shown  # the proposal is labelled as model output


# --- 7. compare baseline vs candidate experiment -------------------------------------------------


def test_step_7_compare_baseline_and_candidate(client: TestClient) -> None:
    shown = section(get(client, "/flow"), "results")
    assert "Paired difference from the baseline over 20 seeds" in shown
    assert "Baseline range over seeds" in shown
    # I1 on mean_queue_m.S under timeline v2: -3.912817 m in the ensemble record, Holm-confirmed
    assert "-3.91 m [-4.09, -3.74] improves · Holm ✓" in shown
    assert "simulated (SUMO)" in shown


# --- 8. read the decision, trade-offs, uncertainty and failure conditions ------------------------


def test_step_8_read_the_decision_tradeoffs_uncertainty_and_failure_conditions(client) -> None:
    page = get(client, "/flow")
    decision, results = section(page, "decision"), section(page, "results")
    assert "Selected: none." in decision and "Recommended: none." in decision  # as recorded
    report = json.loads(DEMO_RECORD.read_text(encoding="utf-8"))["report"]
    assert report["selected_intervention_id"] is None
    assert report["selection_reason"] in decision  # the recorded per-metric trade-offs, verbatim
    assert "+5.40 s" in results and "-0.75 s" in results  # I1 delay: timeline v1 vs v2
    assert "partially validated" in decision and "not assessed" in results  # T3, T4


# --- 9. trace the decision to experiment, inputs and evidence ------------------------------------


@needs_runs
def test_step_9_trace_the_decision_to_experiment_inputs_and_evidence(client: TestClient) -> None:
    shown = text_of(get(client, "/decision/trace"))
    assert NGSIM_SHA256 in shown  # the chain reaches the source hash through the inputs
    assert " <- " in shown  # and names every link it follows or that breaks
    assert REPLAY_LABEL in shown  # the unit of inference of the pairs
    pairs = set(re.findall(r"\b[0-9a-f]{64}\b", shown))
    assert len(pairs) >= 20  # pair, demand and record hashes, listed in full
    assert "seed" in shown
