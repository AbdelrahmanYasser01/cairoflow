"""The /demo panels from the meta-depth records (cairoflow_api.meta_depth) and their pins."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cairoflow_api.app import create_app
from cairoflow_api.demo import read_manifest
from cairoflow_api.meta_depth import (
    PINS_FILE,
    MetaDepth,
    MetaDepthPins,
    load_meta_depth,
    render_frame_panel,
    render_recurrence_panel,
)
from cairoflow_contracts.evidence import Provenance
from cairoflow_contracts.meta_depth import (
    AnnotatedFrame,
    EvidenceRecurrence,
    FrameAnnotation,
    FrameAnnotationRecord,
    HypothesisCluster,
    HypothesisMember,
    MuseRecurrence,
    ReplyRef,
    TypeRecurrence,
)

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "data/demo/ngsim-int2-no-footage.json"
IMMEDIACY = re.compile(r"\breal[- ]?time\b|\blive\b|\bstream(?:ing|ed)\b", re.I)
HOSTILE = "<script>alert(1)</script> & more"
SHA = "a" * 64


def _section(page: str, anchor: str) -> str:
    return page.split(f'id="{anchor}"')[1].split("</section>")[0]


@pytest.fixture(scope="module")
def page(tmp_path_factory: pytest.TempPathFactory) -> str:
    client = TestClient(create_app(tmp_path_factory.mktemp("runs"), MANIFEST, REPO))
    response = client.get("/demo")
    assert response.status_code == 200
    return response.text


def _record(annotation: FrameAnnotation) -> FrameAnnotationRecord:
    now = datetime(2026, 11, 3, 21, 0, tzinfo=UTC)
    return FrameAnnotationRecord(
        label="model-generated", status="ok", model="muse-spark-1.3", prompt_version="frame-0.1.0",
        endpoint="https://api.meta.ai/v1/responses", request_key=SHA, request_id=None,
        response_id=None, sent_at_utc=now, elapsed_s=1.0, recorded_cost_usd=0.01,
        frame=AnnotatedFrame(source_path="v.mp4", source_sha256=SHA, frame_index=50, time_s=5.0,
                             image_path="f.jpg", image_sha256=SHA, width_px=640, height_px=480),
        annotation=annotation,
        provenance=Provenance(kind="model_generated", produced_by="muse-spark-1.3",
                              produced_at_utc=now),
    )  # fmt: skip


def _meta(**kwargs) -> MetaDepth:
    pins = MetaDepthPins(files={})
    return MetaDepth(pins=pins, **kwargs)


# ---------- the committed pins, served ----------


@pytest.mark.skipif(not (REPO / PINS_FILE).is_file(), reason="no meta-depth pins committed")
def test_the_demo_shows_the_pinned_frame_annotation_after_step_0_as_model_text(page) -> None:
    meta = load_meta_depth(REPO, read_manifest(MANIFEST).absent_allowed)
    assert meta.problem is None and meta.frame is not None and meta.frame.status == "ok"
    panel = _section(page, "frame-annotation")
    assert (
        page.index('id="sam"') < page.index('id="frame-annotation"') < page.index('id="evidence"')
    )
    assert "kind-model" in panel and "not ground truth" in panel and "not a measurement" in panel
    assert f"{len(meta.frame.annotation.objects)} road users" in panel
    assert meta.frame.request_key[:16] in panel and PINS_FILE in panel
    assert not IMMEDIACY.search(panel)


@pytest.mark.skipif(not (REPO / PINS_FILE).is_file(), reason="no meta-depth pins committed")
def test_the_recurrence_panel_follows_step_4_when_it_is_pinned(page) -> None:
    meta = load_meta_depth(REPO, read_manifest(MANIFEST).absent_allowed)
    if meta.recurrence is None:
        assert 'id="recurrence"' not in page
        return
    panel = _section(page, "recurrence")
    assert page.index('id="proposals"') < page.index('id="recurrence"') < page.index('id="results"')
    assert f"{meta.recurrence.n_replies} replies" in panel and not IMMEDIACY.search(panel)


# ---------- the pins are checked ----------


def _copy_pins(tmp_path: Path) -> Path:
    pins = MetaDepthPins.model_validate_json((REPO / PINS_FILE).read_text("utf-8"))
    for pin in [*pins.files.values()]:
        target = tmp_path / pin.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if (REPO / pin.path).is_file():  # a tree without footage has no frame image
            shutil.copyfile(REPO / pin.path, target)
    (tmp_path / PINS_FILE).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / PINS_FILE, tmp_path / PINS_FILE)
    return tmp_path


def test_no_pins_file_shows_nothing(tmp_path: Path) -> None:
    meta = load_meta_depth(tmp_path)
    assert meta == MetaDepth() and render_frame_panel(meta) == render_recurrence_panel(meta) == ""


@pytest.mark.skipif(not (REPO / PINS_FILE).is_file(), reason="no meta-depth pins committed")
def test_a_changed_or_missing_pinned_file_is_named_and_its_content_not_shown(tmp_path) -> None:
    repo = _copy_pins(tmp_path)
    pins = MetaDepthPins.model_validate_json((repo / PINS_FILE).read_text("utf-8"))
    image = repo / pins.files["frame_image"].path
    image.write_bytes(b"changed")
    meta = load_meta_depth(repo)
    assert meta.frame is None and "differs" in (meta.problem or "")
    image.unlink()
    assert "missing" in (load_meta_depth(repo).problem or "")


@pytest.mark.skipif(not (REPO / PINS_FILE).is_file(), reason="no meta-depth pins committed")
def test_the_pin_script_refuses_a_failed_record(tmp_path) -> None:
    repo = _copy_pins(tmp_path)
    pins = MetaDepthPins.model_validate_json((repo / PINS_FILE).read_text("utf-8"))
    path = repo / pins.files["frame_annotation"].path
    data = json.loads(path.read_text("utf-8")) | {"status": "failed", "annotation": None}
    failed = path.with_name("failed.json")
    failed.write_text(json.dumps(data))
    spec = importlib.util.spec_from_file_location("pin", REPO / "scripts/pin_meta_depth.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = hashlib.sha256((repo / PINS_FILE).read_bytes()).hexdigest()
    assert module.main(["--frame", str(failed.relative_to(repo))], repo=repo) == 2
    assert hashlib.sha256((repo / PINS_FILE).read_bytes()).hexdigest() == before


# ---------- rendering ----------


def test_model_text_is_escaped_and_boxed_in_the_frame_panel() -> None:
    annotation = FrameAnnotation(
        scene_summary=HOSTILE,
        objects=(
            {"category": "car", "region": "top_left", "overlay_box": "boxed", "note": HOSTILE},
        ),  # fmt: skip
        overlay_issues=(HOSTILE,),
        image_quality="poor",
        limitations=("one frame",),
    )
    panel = render_frame_panel(_meta(frame=_record(annotation)))
    assert "<script>" not in panel and "&lt;script&gt;" in panel
    assert "1 road users" in panel and "reads 1 as boxed" in panel


def test_the_recurrence_panel_shows_k_of_n_and_the_statements_as_model_text() -> None:
    member = HypothesisMember(reply_id="r2", hypothesis_id="H1", statement=HOSTILE,
                              confidence_label="medium",
                              supporting_evidence_ids=("mv.SN",))  # fmt: skip
    rec = MuseRecurrence(
        label="computed by code", request_key=SHA, prompt_version="0.1.0", bundle_id="b",
        n_replies=4,
        replies=tuple(ReplyRef(reply_id=f"r{i}", replicate=i, cache_path="c", cache_sha256=SHA,
                               response_id=None) for i in range(1, 5)),
        rule="Jaccard", jaccard_threshold=0.5,
        evidence=(EvidenceRecurrence(evidence_id="mv.SN", supporting_in=3, contradicting_in=1,
                                     cited_in=4, replies=("r1", "r2", "r3", "r4")),),
        hypotheses=(HypothesisCluster(cluster_id="C1", in_replies=1, members=(member,),
                                      shared_evidence_ids=("mv.SN",)),),
        interventions=(TypeRecurrence(intervention_type="signal_timing", in_replies=4),),
    )  # fmt: skip
    panel = render_recurrence_panel(_meta(recurrence=rec))
    assert "4 of 4" in panel and "3 of 4" in panel and "C1: in 1 of 4 replies" in panel
    assert "<script>" not in panel and 'class="model"' in panel and "kind-model" in panel
