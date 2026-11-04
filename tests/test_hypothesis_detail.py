"""Each hypothesis, wherever a page shows one: the evidence ids it cites for and against, what it
assumes, its alternatives, and the citation check stored with its reply (cairoflow_api.hypotheses).

The hypothesis is AI-generated text (Muse Spark) and its box says so. The stored citation check is
code's: when the reply was parsed, `muse_adapter.citation_problems` listed the cited evidence ids
the bundle lacks and the addressed hypothesis ids the reply lacks, and that list was stored with the
reply (the experiment record's `muse_request.citation_problems`, a run's `reasoning.json`
`problems`). The pages read it back below the box, labelled "computed by code". An empty stored
list reads "none"; a list that was never stored reads "not recorded"; neither is ever a zero.
Nothing here recomputes the check (the recomputed evidence check is tests/test_demo.py's).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shutil
from datetime import UTC, datetime
from html import escape, unescape
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from synthetic_pipeline import FAKE_KEY, MuseStub, refuse, write_inputs

from cairoflow_api.app import create_app
from cairoflow_api.demo import load_demo, read_manifest
from cairoflow_api.flow import load_world_view
from cairoflow_api.flow_html import render_flow_page
from cairoflow_api.html import AI_BADGE, render_demo_page
from cairoflow_api.hypotheses import StoredCitations, attached_details, stored_citations_of_demo
from cairoflow_api.world_html import render_problem
from cairoflow_contracts.config import Settings
from cairoflow_contracts.evidence import Provenance
from cairoflow_contracts.problem_statement import AttachedHypothesis, SourceFile
from cairoflow_contracts.reasoning import Hypothesis, ReasoningResult
from cairoflow_contracts.report import DecisionReport
from cairoflow_evaluation.pipeline import run_pipeline
from cairoflow_evaluation.problem_statement import problem_statement_from_records

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "data/demo/ngsim-int2-no-footage.json"
PROPOSALS = "experiments/records/ngsim-int2-experiment-20261028T075803Z.json"
KEY = "muse_request.citation_problems"
BOX = re.compile(r'<div class="model">.*?</div>', re.S)
STORED = re.compile(r'<div class="stored">(.*?)</div>', re.S)
INJECTED = (
    "H1 cites unknown evidence 'q.made_up'",
    "I1 addresses unknown hypothesis 'H9'",
    "I2 cites unknown evidence 'mv.XX'",
)
HOSTILE = "H1 cites unknown evidence '<script>alert(1)</script>'"


@pytest.fixture(scope="module")
def demo():
    return load_demo(read_manifest(MANIFEST), REPO)


@pytest.fixture(scope="module")
def view(demo):
    return load_world_view(demo, REPO)  # absent or present: the hypotheses do not depend on it


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    return TestClient(create_app(tmp_path_factory.mktemp("runs"), MANIFEST, REPO))


def text_of(page: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", page))).strip()


def box_of(page: str, statement: str) -> str:
    return next(box for box in BOX.findall(page) if escape(statement) in box)


def stored_after(page: str, statement: str) -> str:
    """The stored-citation block that follows a hypothesis's box, before the next box."""
    box = box_of(page, statement)
    start = page.index(box) + len(box)
    found, following = STORED.search(page, start), BOX.search(page, start)
    assert found, statement
    assert following is None or found.start() < following.start(), statement
    return found.group(1)


def proposals_block(page: str) -> str:
    return next(block for block in STORED.findall(page) if "of the proposals" in block)


def four_lists(h: Hypothesis) -> tuple[str, ...]:
    return (
        *h.supporting_evidence_ids,
        *h.contradicting_evidence_ids,
        *h.assumptions,
        *h.alternative_explanations,
    )


def with_stored(demo, problems):
    """The demo with another stored list (None: the key removed), as if its record held it."""
    request = dict(demo.records["proposals"]["muse_request"])
    if problems is None:
        request.pop("citation_problems")
    else:
        request["citation_problems"] = list(problems)
    proposals = {**demo.records["proposals"], "muse_request": request}
    return dataclasses.replace(demo, records={**demo.records, "proposals": proposals})


def pages(demo, view) -> dict[str, str]:
    return {
        "/demo": render_demo_page(demo, run_url=None),
        "/flow": render_flow_page(demo, view, None),
    }


# --- /demo and /flow: the pinned reply --------------------------------------------------------


@pytest.mark.parametrize("url", ["/demo", "/flow"])
def test_each_hypothesis_box_is_labelled_ai_generated(client: TestClient, demo, url: str) -> None:
    page = client.get(url).text
    assert demo.reasoning.hypotheses
    for h in demo.reasoning.hypotheses:
        box = box_of(page, h.statement)
        assert AI_BADGE in box and "model-generated text, not evidence" in box
        assert all(escape(value) in box for value in h.supporting_evidence_ids), h.hypothesis_id


def test_on_the_flow_page_each_box_lists_what_the_hypothesis_cites_and_assumes(
    client: TestClient, demo
) -> None:
    page = client.get("/flow").text
    for h in demo.reasoning.hypotheses:
        box = box_of(page, h.statement)
        for label in (
            "Supporting evidence cited",
            "Contradicting evidence cited",
            "Assumptions (model-stated)",
            "Alternative explanations (model-stated)",
        ):
            assert label in box, (h.hypothesis_id, label)
        assert all(escape(value) in box for value in four_lists(h)), h.hypothesis_id


def test_the_stored_check_is_the_pinned_records_list_shown_under_each_box(
    client: TestClient, demo
) -> None:
    stored = stored_citations_of_demo(demo)
    assert (stored.source, stored.key, stored.problems) == (PROPOSALS, KEY, ())  # committed: []
    for url in ("/demo", "/flow"):
        page = client.get(url).text
        for h in demo.reasoning.hypotheses:
            block = text_of(stored_after(page, h.statement))
            assert block.startswith("Stored citation check"), (url, block)
            assert "computed by code" in block and f"none for {h.hypothesis_id}" in block
            assert KEY in block and PROPOSALS in block, (url, block)
        rest = text_of(proposals_block(page))
        assert "computed by code" in rest and "none" in rest and PROPOSALS in rest, url
        assert not re.search(r"\b0 (?:citation|problem|entr)", text_of(page)), url  # never a zero


def test_a_stored_entry_sits_under_its_own_hypothesis_and_proposal_entries_apart(
    demo, view
) -> None:
    edited = with_stored(demo, INJECTED)
    first, second = edited.reasoning.hypotheses[:2]
    for url, page in pages(edited, view).items():
        mine = text_of(stored_after(page, first.statement))
        assert INJECTED[0] in mine and not any(entry in mine for entry in INJECTED[1:]), url
        theirs = text_of(stored_after(page, second.statement))
        assert f"none for {second.hypothesis_id}" in theirs, url
        assert not any(entry in theirs for entry in INJECTED), url
        rest = text_of(proposals_block(page))
        assert all(entry in rest for entry in INJECTED[1:]) and INJECTED[0] not in rest, url
        assert page.count(escape(INJECTED[0])) == 1, url


def test_a_list_that_was_never_stored_reads_not_recorded_not_none(demo, view) -> None:
    for url, page in pages(with_stored(demo, None), view).items():
        for h in demo.reasoning.hypotheses:
            block = text_of(stored_after(page, h.statement))
            assert "not recorded" in block and "none" not in block, (url, block)
        assert "not recorded" in text_of(proposals_block(page)), url


def test_stored_entries_are_escaped(demo, view) -> None:
    for url, page in pages(with_stored(demo, [HOSTILE]), view).items():
        assert "<script" not in page.lower() and escape(HOSTILE) in page, url


def test_entries_belong_to_a_hypothesis_by_its_whole_id() -> None:
    stored = StoredCitations(
        "r.json", "problems",
        ("H1 cites unknown evidence 'a'", "H10 cites unknown evidence 'b'",
         "I1 addresses unknown hypothesis 'H1'"),
    )  # fmt: skip
    assert stored.of("H1") == ("H1 cites unknown evidence 'a'",)
    assert stored.of("H10") == ("H10 cites unknown evidence 'b'",)
    assert stored.of("H2") == ()
    assert stored.rest(["H1", "H10"]) == ("I1 addresses unknown hypothesis 'H1'",)
    unrecorded = StoredCitations("r.json", "problems", None)
    assert unrecorded.of("H1") is None and unrecorded.rest(["H1"]) is None


# --- /problem: the attached hypotheses, read back from the reply the record pins ----------------


def stored_reply(demo, root: Path, problems: tuple[str, ...]) -> tuple[str, bytes]:
    """A reasoning.json holding the pinned reply's hypotheses, under `root`; its path and bytes."""
    result = ReasoningResult(
        bundle_id="0123456789abcdef", request_key="test", response_id=None, request_id=None,
        model="test", prompt_version="0.1.0", output=demo.reasoning, problems=problems,
        usage={}, estimated_cost_usd=0.0,
        provenance=Provenance(kind="model_generated", produced_by="test",
                              produced_at_utc=datetime(2026, 11, 3, tzinfo=UTC)),
    )  # fmt: skip
    path = "experiments/runs/test-run/reasoning.json"
    data = result.model_dump_json(indent=2).encode("utf-8")
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_bytes(data)
    return path, data


def attached_to(demo, path: str, data: bytes) -> list[AttachedHypothesis]:
    source = SourceFile(path=path, sha256=hashlib.sha256(data).hexdigest())
    return [
        AttachedHypothesis(hypothesis_id=h.hypothesis_id, statement=h.statement, source=source)
        for h in demo.reasoning.hypotheses
    ]


def test_attached_details_read_the_pinned_reply_and_refuse_a_changed_one(
    demo, tmp_path: Path
) -> None:
    path, data = stored_reply(demo, tmp_path, INJECTED)
    attached = attached_to(demo, path, data)
    first, second = demo.reasoning.hypotheses[:2]
    details = attached_details(attached, tmp_path)
    found = details[(path, first.hypothesis_id)]
    assert found.hypothesis == first and found.refusal is None
    assert found.stored is not None and found.stored.of(first.hypothesis_id) == INJECTED[:1]
    assert (found.stored.source, found.stored.key) == (path, "problems")

    other = AttachedHypothesis(
        hypothesis_id=second.hypothesis_id,
        statement="Another statement.",
        source=attached[1].source,
    )
    refused = attached_details([other], tmp_path)[(path, second.hypothesis_id)]
    assert refused.hypothesis is None and "with this statement" in refused.refusal

    (tmp_path / path).write_bytes(data + b"\n")
    changed = attached_details(attached, tmp_path)[(path, first.hypothesis_id)]
    assert changed.hypothesis is None and "no longer has the SHA-256" in changed.refusal
    (tmp_path / path).unlink()
    gone = attached_details(attached, tmp_path)[(path, first.hypothesis_id)]
    assert gone.hypothesis is None and "not present" in gone.refusal


def test_the_problem_page_shows_each_attached_hypothesis_in_full_or_says_why_not(
    demo, tmp_path: Path
) -> None:
    problem = problem_statement_from_records(REPO)
    path, data = stored_reply(demo, tmp_path, INJECTED)
    attached = attached_to(demo, path, data)
    shown = problem.model_copy(update={"hypotheses": tuple(attached)})
    page = render_problem(shown, None, attached_details(attached, tmp_path))
    section = re.search(r'<section id="hypotheses"(.*?)</section>', page, re.S).group(1)
    for h in demo.reasoning.hypotheses:
        box = box_of(section, h.statement)  # the same box /demo and /flow draw
        assert AI_BADGE in box and f"{h.hypothesis_id}, {h.confidence_label} confidence" in box
        assert all(escape(value) in box for value in four_lists(h)), h.hypothesis_id
        block = text_of(stored_after(section, h.statement))
        assert "computed by code" in block and path in block  # the reply it was read from
    first, second = demo.reasoning.hypotheses[:2]
    assert INJECTED[0] in text_of(stored_after(section, first.statement))
    assert f"none for {second.hypothesis_id}" in text_of(stored_after(section, second.statement))
    assert "AI-generated" not in page.split('id="hypotheses"')[0]  # model text in its section only

    (tmp_path / path).write_bytes(data + b"\n")  # the reply changed after the record pinned it
    page = render_problem(shown, None, attached_details(attached, tmp_path))
    section = re.search(r'<section id="hypotheses"(.*?)</section>', page, re.S).group(1)
    assert "Details not shown" in section and "no longer has the SHA-256" in section
    assert STORED.search(section) is None
    for h in demo.reasoning.hypotheses:
        box = box_of(section, h.statement)  # the statement still shows, boxed and labelled
        assert not any(escape(value) in box for value in h.assumptions), h.hypothesis_id


def test_the_problem_route_shows_the_local_replys_details(client: TestClient) -> None:
    problem = problem_statement_from_records(REPO)
    if not problem.hypotheses:
        pytest.skip("the Muse reply the member records pin is a Git-ignored run file")
    section = re.search(
        r'<section id="hypotheses"(.*?)</section>', client.get("/problem").text, re.S
    ).group(1)
    for attached in problem.hypotheses:
        stored = ReasoningResult.model_validate_json((REPO / attached.source.path).read_bytes())
        h = next(x for x in stored.output.hypotheses if x.hypothesis_id == attached.hypothesis_id)
        assert all(escape(value) in box_of(section, h.statement) for value in four_lists(h))
        block = text_of(stored_after(section, h.statement))
        mine = [p for p in stored.problems if p.startswith(f"{h.hypothesis_id} ")]
        assert attached.source.path in block and all(p in block for p in mine)
        assert (f"none for {h.hypothesis_id}" in block) is (not mine)


# --- /runs/<name>/: a pipeline run's reasoning.json ----------------------------------------------


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory):
    """One synthetic pipeline run, and copies of it whose reasoning.json is missing, unpinned,
    edited without and with a new pin in the pipeline record, or another reply's."""
    root = tmp_path_factory.mktemp("hypothesis-runs")
    runs = root / "runs"
    (root / "inputs").mkdir()
    run_pipeline(
        write_inputs(root / "inputs", with_video=False),
        runs / "synthetic",
        mode="live",
        settings=Settings(model_api_key=SecretStr(FAKE_KEY)),
        spend_limit_usd=1.0,
        sam_transport=httpx.MockTransport(refuse),
        muse_transport=httpx.MockTransport(MuseStub()),
    )

    def copy(name: str, edit=None, *, repin: bool = False, unpin: bool = False) -> None:
        shutil.copytree(runs / "synthetic", runs / name)
        reasoning, record = runs / name / "reasoning.json", runs / name / "pipeline_record.json"
        if edit is None:
            reasoning.unlink()
        else:
            data = json.loads(reasoning.read_text("utf-8"))
            edit(data)
            reasoning.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if repin or unpin:
            pins = json.loads(record.read_text("utf-8"))
            if unpin:
                del pins["artifacts"]["reasoning.json"]
            else:
                digest = hashlib.sha256(reasoning.read_bytes()).hexdigest()
                pins["artifacts"]["reasoning.json"] = digest
            record.write_text(json.dumps(pins, indent=2), encoding="utf-8")

    def inject(data: dict) -> None:
        data.update(problems=[INJECTED[0], INJECTED[1]])

    def another(data: dict) -> None:
        data["output"]["hypotheses"][1].update(statement="Not this one.")

    copy("missing")
    copy("unpinned", unpin=True)
    copy("tampered", inject)
    copy("edited", inject, repin=True)
    copy("other-reply", another, repin=True)
    report = DecisionReport.model_validate_json((runs / "synthetic/report.json").read_text())
    return TestClient(create_app(runs)), report


def test_a_run_page_shows_the_stored_check_of_the_reasoning_file_its_record_pins(site) -> None:
    client, report = site
    assert report.hypotheses
    page = client.get("/runs/synthetic/").text
    for h in report.hypotheses:
        assert AI_BADGE in box_of(page, h.statement)
        block = text_of(stored_after(page, h.statement))
        assert f"none for {h.hypothesis_id}" in block and "reasoning.json" in block
    edited = client.get("/runs/edited/").text  # rewritten, and pinned again in the record
    first = report.hypotheses[0]
    assert INJECTED[0] in text_of(stored_after(edited, first.statement))
    assert INJECTED[1] in text_of(proposals_block(edited))


@pytest.mark.parametrize(
    ("run", "words"),
    [
        ("missing", "which this run directory does not hold"),
        ("unpinned", "not recorded for this reply"),
        ("tampered", "is not the file the record pins"),
        ("other-reply", "holds other hypotheses than this report"),
    ],
)
def test_a_run_page_names_why_it_shows_no_stored_check(site, run: str, words: str) -> None:
    client, report = site
    page = client.get(f"/runs/{run}/").text
    for h in report.hypotheses:
        block = text_of(stored_after(page, h.statement))
        assert words in block and "none" not in block, (run, block)
        assert not any(entry in block for entry in INJECTED), (run, block)


def test_the_demo_run_page_says_its_pinned_reasoning_file_is_kept_elsewhere() -> None:
    """The replay experiment pins source/reasoning.json, which its run directory never held."""
    run = json.loads(MANIFEST.read_text("utf-8"))["run_dir"]
    where = REPO / "experiments/runs" / run
    if not (where / "report.json").is_file():
        pytest.skip("the demo's run directory is Git-ignored")
    # the app serves only real directories under its root; in a linked checkout the run directory
    # is a link into the main checkout, so serve the directory it resolves to
    client = TestClient(create_app(where.resolve().parent))
    page = client.get(f"/runs/{run}/").text
    report = DecisionReport.model_validate_json((where / "report.json").read_text("utf-8"))
    assert report.hypotheses
    for h in report.hypotheses:
        block = text_of(stored_after(page, h.statement))
        assert "source/reasoning.json" in block and "does not hold" in block, block
