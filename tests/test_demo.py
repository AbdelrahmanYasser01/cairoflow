"""The demo page (M7, cairoflow_api.demo), built from the committed records its manifest pins.

Nothing here writes into the repository: changed manifests and runs live in a temporary
directory. Nothing leaves the machine.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from html import escape
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cairoflow_api.app import create_app
from cairoflow_api.demo import (
    ENSEMBLE,
    FOOTAGE_NOTE,
    SAM_NOTICE,
    SAM_OVERLAY,
    SAM_RECORD,
    SAM_REVIEW,
    DemoError,
    DemoManifest,
    PinnedFile,
    baseline_ranges,
    differences,
    load_demo,
    read_manifest,
)
from cairoflow_api.html import render_demo_page
from cairoflow_api.meta_depth import PINS_FILE, MetaDepthPins, load_meta_depth

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "data/demo/ngsim-int2-no-footage.json"  # the README serves it
WITH_A3 = REPO / "data/demo/ngsim-int2-replay-timeline-v2-sam-a3.json"  # before the ensemble
BEFORE_A3 = REPO / "data/demo/ngsim-int2-replay-timeline-v2-sam.json"  # step 0, before the review
V2_RECORD_0_2 = "experiments/records/ngsim-int2-realism-experiment-20261028T142330Z.json"
EARLIER = REPO / "data/demo/ngsim-int2-replay-timeline-v2.json"  # pinned before step 0 existed
FULL = REPO / "data/demo" / "ngsim-int2-ensemble-control.json"  # every pin must be there
NO_FOOTAGE = REPO / "data/demo/ngsim-int2-no-footage.json"  # the same pins, footage may be absent
OVERLAY_PIN = read_manifest(FULL).files[SAM_OVERLAY]
needs_footage = pytest.mark.skipif(
    not (REPO / OVERLAY_PIN.path).is_file(), reason="this tree does not distribute the footage"
)
HOSTILE = "<script>alert(1)</script> & more"


@pytest.fixture(scope="module")
def demo():
    return load_demo(read_manifest(MANIFEST), REPO)


def test_step_0_shows_sam_on_the_real_footage_apart_from_the_rest(demo) -> None:
    sam = demo.records[SAM_RECORD]
    assert demo.has_sam and sam["requests"][0]["prompt"] == "car"
    page = render_demo_page(demo, run_url=None, sam_video_url="/demo/sam-overlay.mp4")
    step = page.split('id="sam"')[1].split("</section>")[0]
    assert page.index('id="sam"') < page.index('id="evidence"')
    assert '<video controls preload="metadata" src="/demo/sam-overlay.mp4">' in step
    assert "Not used downstream" in step and "not ground truth" in step
    rows = re.findall(r'<tr class="fig">.*?</tr>', step, flags=re.S)
    assert len([row for row in rows if "kind-reviewed" not in row]) == len(sam["requests"]) + 1
    assert all('class="badge kind-sam"' in row or "kind-reviewed" in row for row in rows)
    # the cost is our estimate (frames billed x the published per-frame price), not SAM's output
    # and not a bill: its column says so and its rows carry "computed by code"
    head = re.findall(r"<th>(.*?)</th>", step.split("<table>")[1].split("</thead>")[0])
    assert "Cost" not in head and any(h.startswith("Estimated cost") for h in head)
    for row, request in zip(rows, sam["requests"], strict=False):
        assert f"${request['estimated_cost_usd']:.2f}" in row
        assert 'class="badge kind-checked"' in row
    assert escape(demo.texts[SAM_NOTICE]) in step and "CC BY-SA 4.0" in step


@needs_footage
def test_step_0_needs_all_of_its_pins(demo) -> None:
    earlier = load_demo(read_manifest(EARLIER), REPO)  # the earlier pins still load, without it
    assert not earlier.has_sam and 'id="sam"' not in render_demo_page(earlier, run_url=None)
    alone = {k: v for k, v in demo.manifest.files.items() if k != SAM_OVERLAY}
    with pytest.raises(DemoError, match="SAM step pinned without sam_overlay"):
        load_demo(demo.manifest.model_copy(update={"files": alone}), REPO)


def test_step_0_shows_the_a3_check_only_once_its_scored_record_is_pinned(demo) -> None:
    scores = demo.records[SAM_REVIEW]["scores"]
    step = render_demo_page(demo, run_url=None).split('id="sam"')[1].split("</section>")[0]
    rows = re.findall(r'<tr class="fig">.*?</tr>', step, flags=re.S)
    reviewed = [row for row in rows if 'class="badge kind-reviewed"' in row]
    assert len(reviewed) == 3 + len(scores["per_frame"])  # three measures, then one row a frame
    low, high = scores["recall"]["ci95"]
    assert f"{scores['recall']['value']:.2f} [{low:.2f}, {high:.2f}]" in step
    assert "too narrow" in step and "is still to do" not in step
    if BEFORE_A3.is_file():  # the step 0 pins from before the review
        before = load_demo(read_manifest(BEFORE_A3), REPO)
        assert before.has_sam and SAM_REVIEW not in before.records
        assert "is still to do" in render_demo_page(before, run_url=None)  # nothing claimed early
    alone = {k: v for k, v in demo.manifest.files.items() if not k.startswith("sam_") or
             k == SAM_REVIEW}  # fmt: skip
    with pytest.raises(DemoError, match="SAM review pinned without sam_record"):
        load_demo(demo.manifest.model_copy(update={"files": alone}), REPO)


def test_the_committed_demo_loads_its_pinned_and_linked_records(demo) -> None:
    assert demo.assessment.status == "partially_validated"
    report = demo.records["results"]["report"]
    assert report["selected_intervention_id"] is None
    assert report["recommended_intervention_id"] is None
    recorded = [h["hypothesis_id"] for h in demo.records["proposals"]["hypotheses"]]
    assert [h.hypothesis_id for h in demo.reasoning.hypotheses] == recorded


def test_the_page_tells_the_story_with_a_kind_on_every_figure(demo) -> None:
    page = render_demo_page(demo, run_url=None)
    for n in range(1, 9):
        assert f"<h2>{n} · " in page
    assert "partially validated" in page and "Nothing is recommended" in page
    # the page reruns the evidence check and sums and subtracts as it renders: it says so
    assert "nothing on this page is computed" not in page
    assert "computed from those records by this page's code as it renders" in page
    rows = re.findall(r'<tr class="fig">.*?</tr>', page, flags=re.S)
    assert len(rows) > 30 and all('class="badge kind-' in row for row in rows)
    assert "<script" not in page.lower()
    for pin in demo.manifest.files.values():
        assert pin.sha256 in page
    # the timeline sensitivity: I1's mean delay on both timeline versions, side by side
    one = differences(demo.records["results_v1"])["I1"]["mean_delay_s"]
    two = differences(demo.records["results"])["I1"]["mean_delay_s"]
    assert (one.verdict, two.verdict) == ("worsens", "improves")
    sensitivity = page.split('id="sensitivity"')[1].split("</section>")[0]
    assert f"{one.value:+.2f} s" in sensitivity and f"{two.value:+.2f} s" in sensitivity
    assert "Full run report" not in page
    # what a reader needs not to over-read: the replay's span, Muse's input, the delay's scale
    assert "10.6 of the 15 minutes" in page and "never saw the replay baseline" in page
    low, high, _ = baseline_ranges(demo.records["results"])["mean_delay_s"]
    results = page.split('id="results"')[1].split("</section>")[0]
    assert f"{low:.1f}–{high:.1f} s" in results and "no observed counterpart" in results


def test_model_text_appears_only_boxed_and_escaped(demo) -> None:
    page = render_demo_page(demo, run_url=None)
    boxes = re.findall(r'<div class="model">.*?</div>', page, flags=re.S)
    texts = [h.statement for h in demo.reasoning.hypotheses]
    texts += [p.rationale for p in demo.reasoning.interventions]
    for text in texts:
        shown = escape(text)
        assert page.count(shown) == sum(box.count(shown) for box in boxes) >= 1
    first = demo.reasoning.hypotheses[0].model_copy(update={"statement": HOSTILE})
    hostile = dataclasses.replace(demo, reasoning=demo.reasoning.model_copy(
        update={"hypotheses": (first, *demo.reasoning.hypotheses[1:])}))  # fmt: skip
    page = render_demo_page(hostile, run_url=None)
    assert "<script" not in page.lower() and escape(HOSTILE) in page


def test_each_hypothesis_shows_its_evidence_assumptions_and_the_code_check(demo) -> None:
    """P0-3: contradicting evidence, assumptions and alternatives stand beside each hypothesis,
    and the evidence check is recomputed by code from the bundle the pinned request sent."""
    page = render_demo_page(demo, run_url=None)
    assert demo.grounding is not None and len(demo.grounding.hypotheses) == 2
    for h in demo.reasoning.hypotheses:
        box = next(b for b in re.findall(r'<div class="model">.*?</div>', page, flags=re.S)
                   if escape(h.statement) in b)  # fmt: skip
        for label, values in (("Contradicting evidence cited", h.contradicting_evidence_ids),
                              ("Assumptions (model-stated)", h.assumptions),
                              ("Alternative explanations (model-stated)",
                               h.alternative_explanations)):  # fmt: skip
            assert label in box and all(escape(v) in box for v in values)
    assert page.count("Evidence check") == 2
    # the recorded 0.1.0 reply cites only ids its bundle holds, so both are supported
    assert [g.status for g in demo.grounding.hypotheses] == ["supported", "supported"]
    assert "predates prompt 0.3.0" in demo.grounding.notes[-1]


def test_a_changed_missing_or_unlinked_pin_is_refused(demo) -> None:
    files = dict(demo.manifest.files)
    changed = demo.manifest.model_copy(
        update={
            "files": {
                **files,
                "assessment": PinnedFile(path=files["assessment"].path, sha256="0" * 64),
            }
        }
    )
    with pytest.raises(DemoError, match="differs from the demo manifest"):
        load_demo(changed, REPO)
    missing = demo.manifest.model_copy(
        update={
            "files": {
                **files,
                "scenario": PinnedFile(path="data/no-such-file.json", sha256="0" * 64),
            }
        }
    )
    with pytest.raises(DemoError, match="is missing"):
        load_demo(missing, REPO)
    swapped = demo.manifest.model_copy(update={"files": {**files, "results": files["results_v1"]}})
    with pytest.raises(DemoError, match="do not link: the results were simulated on"):
        load_demo(swapped, REPO)  # fmt: skip


def test_step_7_needs_a_verdict_on_every_pre_registered_timeline(demo) -> None:
    ensemble = demo.records[ENSEMBLE]
    page = render_demo_page(demo, run_url=None)
    step = page.split('id="sensitivity"')[1].split("</section>")[0]
    for member in ensemble["members"]:
        assert f"{member['id']} (" in step
        i1 = differences(member)["I1"]["mean_delay_s"]
        assert f"{i1.value:+.2f} s" in step  # each timeline's own result, side by side
    assert step.count("no support") == len(ensemble["verdict"]["candidates"]) == 3
    assert "Nothing is pooled" in step and "pre-registered 0.3.0, used 0.3.1" in step
    assert "not assessed" in step  # T3 and T4 have no assessment against observations
    # its T2 member must be the pinned results: an older version 2 record is refused
    files = dict(demo.manifest.files)
    older = PinnedFile(
        path=V2_RECORD_0_2, sha256=hashlib.sha256((REPO / V2_RECORD_0_2).read_bytes()).hexdigest()
    )
    moved = demo.manifest.model_copy(update={"files": {**files, "results": older}})
    with pytest.raises(DemoError, match="the ensemble's T2 member is the pinned results"):
        load_demo(moved, REPO)  # fmt: skip


def test_step_8_shows_the_synthetic_control_apart_and_labelled(demo) -> None:
    page = render_demo_page(demo, run_url=None)
    order = [page.index(f'id="{a}"') for a in ("sensitivity", "control", "provenance")]
    assert order == sorted(order)
    step = page.split('id="control"')[1].split("</section>")[0]
    assert step.index("Synthetic.") < step.index("<table")
    assert "says nothing about the Los Angeles junction" in step and "no model proposes" in step
    assert demo.records["control_long_cycle"]["outcome"]["known_correction"]["selected"]
    assert "<strong>selected</strong>" in step and "<strong>not selected</strong>" in step
    rows = re.findall(r'<tr class="fig">.*?</tr>', step, flags=re.S)
    assert len(rows) == 4 and all("kind-simulated" in row for row in rows)
    # a control role must hold a synthetic record of its own scenario
    files = dict(demo.manifest.files)
    swapped = demo.manifest.model_copy(
        update={"files": {**files, "control_long_cycle": files["control_reallocation"]}}
    )
    with pytest.raises(DemoError, match="control_long_cycle is a synthetic known-answer"):
        load_demo(swapped, REPO)
    if WITH_A3.is_file():  # the earlier pins still render the two-version step 7, and no control
        older = render_demo_page(load_demo(read_manifest(WITH_A3), REPO), run_url=None)
        assert 'id="control"' not in older and "Timeline version 1" in older


@pytest.mark.parametrize("path", ["../outside.json", "/etc/passwd", "data\\x.json", ""])
def test_pinned_paths_stay_inside_the_repository(path: str) -> None:
    with pytest.raises(ValidationError):
        PinnedFile(path=path, sha256="0" * 64)


def test_the_app_serves_the_demo_only_when_asked(demo, tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    plain = TestClient(create_app(runs))
    assert plain.get("/demo").status_code == 404 and 'href="/demo"' not in plain.get("/").text
    client = TestClient(create_app(runs, MANIFEST, REPO))
    response = client.get("/demo")
    assert response.status_code == 200
    assert "script-src" not in response.headers["content-security-policy"]
    assert 'href="/demo"' in client.get("/").text
    # the full run report is linked once its (Git-ignored) run directory is served
    run = runs / demo.manifest.run_dir
    run.mkdir()
    (run / "pipeline_record.json").write_text("{}", encoding="utf-8")
    assert f'href="/runs/{demo.manifest.run_dir}/"' in client.get("/demo").text
    bad = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bad["files"]["results"]["sha256"] = "0" * 64
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    refused = TestClient(create_app(runs, tmp_path / "bad.json", REPO)).get("/demo")
    assert refused.status_code == 409 and "differs from the demo manifest" in refused.text
    assert plain.get("/demo/sam-overlay.mp4").status_code == 404


@needs_footage
def test_step_0_video_is_the_pinned_overlay_and_only_when_the_manifest_pins_one(tmp_path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    video = TestClient(create_app(runs, MANIFEST, REPO)).get("/demo/sam-overlay.mp4")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert video.content == (REPO / OVERLAY_PIN.path).read_bytes()
    assert (
        TestClient(create_app(runs, EARLIER, REPO)).get("/demo/sam-overlay.mp4").status_code == 404
    )


# ---------- a tree that does not distribute footage ----------


def tree_without_footage(root: Path) -> Path:
    """Every pinned file of the demo and the meta-depth pins, but no pixel-bearing one."""
    manifest = read_manifest(NO_FOOTAGE)
    pins = MetaDepthPins.model_validate_json((REPO / PINS_FILE).read_text("utf-8"))
    wanted = {**manifest.files, **pins.files}
    names = [p.path for r, p in wanted.items() if r not in manifest.absent_allowed]
    for name in [*names, PINS_FILE]:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).symlink_to(REPO / name)
    return root


def test_the_footage_free_manifest_pins_the_same_files_and_marks_only_pixels() -> None:
    manifest = read_manifest(NO_FOOTAGE)
    assert manifest.files == read_manifest(FULL).files
    assert manifest.absent_allowed == (SAM_OVERLAY, "frame_image")
    assert manifest.demo_id == read_manifest(FULL).demo_id


def test_a_manifest_that_marks_nothing_is_written_as_before() -> None:
    manifest = read_manifest(FULL)
    assert manifest.absent_allowed == () and "absent_allowed" not in manifest.model_dump()
    assert manifest.model_dump_json(indent=2) + "\n" == FULL.read_text("utf-8")


def test_without_the_overlay_the_page_loads_and_says_the_footage_is_not_here(tmp_path) -> None:
    demo = load_demo(read_manifest(NO_FOOTAGE), tree_without_footage(tmp_path))
    assert demo.absent == (SAM_OVERLAY,) and demo.has_sam and not demo.has_overlay
    assert demo.manifest.files[SAM_OVERLAY] == OVERLAY_PIN  # still pinned, just not here
    step = render_demo_page(demo, run_url=None).split('id="sam"')[1].split("</section>")[0]
    assert "<video" not in step and escape(FOOTAGE_NOTE) in step
    assert "scripts/fetch_ngsim_data.py --download camera2" in FOOTAGE_NOTE


def test_the_app_serves_every_page_without_footage_and_no_video(tmp_path: Path) -> None:
    root, runs = tree_without_footage(tmp_path / "repo"), tmp_path / "runs"
    runs.mkdir()
    client = TestClient(create_app(runs, NO_FOOTAGE, root))
    demo_page, flow_page = client.get("/demo"), client.get("/flow")
    assert demo_page.status_code == 200 and flow_page.status_code == 200
    assert "<video" not in demo_page.text and "<video" not in flow_page.text
    assert escape(FOOTAGE_NOTE) in demo_page.text  # /flow draws its camera box only with a view
    assert 'id="frame-annotation"' in demo_page.text  # its text is a record, not pixels
    assert 'id="recurrence"' in demo_page.text and "is missing" not in demo_page.text
    assert client.get("/demo/sam-overlay.mp4").status_code == 404


def test_a_manifest_without_the_marking_still_refuses_a_missing_overlay(tmp_path: Path) -> None:
    root = tree_without_footage(tmp_path)
    with pytest.raises(DemoError, match="overlay.mp4 is missing"):
        load_demo(read_manifest(FULL), root)
    assert "is missing" in (load_meta_depth(root).problem or "")  # the same for the sidecar pins
    runs = tmp_path / "runs"
    runs.mkdir()
    refused = TestClient(create_app(runs, FULL, root)).get("/demo")
    assert refused.status_code == 409 and "overlay.mp4 is missing" in refused.text


def test_a_marked_file_that_is_there_must_still_match_its_pin(tmp_path: Path) -> None:
    root = tree_without_footage(tmp_path)
    (root / OVERLAY_PIN.path).write_bytes(b"not the pinned overlay")
    with pytest.raises(DemoError, match="differs from the demo manifest"):
        load_demo(read_manifest(NO_FOOTAGE), root)


@pytest.mark.parametrize("role", ["results", "muse_reply", "sam_record", "no-such-role"])
def test_only_pixel_bearing_roles_may_be_marked_absent(role: str) -> None:
    fields = read_manifest(FULL).model_dump()
    with pytest.raises(ValidationError, match="only pixel-bearing roles may be absent"):
        DemoManifest.model_validate({**fields, "absent_allowed": [role]})
