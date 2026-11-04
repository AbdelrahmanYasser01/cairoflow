"""The decision chain: one walk from a decision back to its source hash (ADR-033).

tests/test_provenance.py covers the frozen stage-by-stage layer (cairoflow_contracts.provenance)
and its builders; its synthetic `chain()` fixture is reused here, not its cases. This file covers
what the link layer adds: walking by decision id, the experiment's baseline inputs, the NGSIM
observation adapter (track points, movement counts and queue samples are not SAM observations),
and refused links (the camera layer is not connected to the World Model: calibration not
accepted, ADR-017/018). The real-experiment test reads Git-ignored run directories and the NGSIM
file and skips where they are absent; the camera test uses the committed camera layer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from synthetic_scenario import scenario_geometry, world_model
from test_provenance import CAMERA_DIR, chain

from cairoflow_contracts.evidence import (
    CameraLayer,
    IntersectionGeometry,
    SourceClip,
    TrafficWorldModel,
)
from cairoflow_contracts.provenance import STAGES, ProvenanceGraph
from cairoflow_contracts.provenance_chain import (
    CAMERA_NOT_ACCEPTED,
    ChainEnd,
    ChainGraph,
    ChainLink,
    ChainRecord,
    RefusedLink,
    assemble,
    baseline_input,
    camera_links,
    ngsim_observations,
    record_of,
    walk,
    walk_from,
)
from cairoflow_evaluation.provenance_chain import experiment_chain_graph
from cairoflow_perception import provenance as pv
from cairoflow_perception.worldview import build_world_view

REPO = Path(__file__).resolve().parents[1]
GEO = scenario_geometry()
RUN = REPO / "experiments/runs/ngsim-int2-realism-experiment-20261028T142330Z"
WORLD_MODEL = REPO / "data/processed/ngsim-int2/20261028T074304Z/world_model.json"
CSV = REPO / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
NGSIM_SHA256 = "c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e"
REAL_REPORT_ID = "ef995ff590426b18"

real = pytest.mark.skipif(
    not all(p.is_file() for p in (RUN / "report.json", WORLD_MODEL, CSV)),
    reason="the run directory, the world model and the NGSIM file are Git-ignored",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def synthetic(tmp_path: Path, **kw) -> tuple[dict, ChainGraph]:
    parts = chain(tmp_path, **kw)
    graph = assemble(ProvenanceGraph.of(parts["nodes"], parts["links"]))
    return parts, graph


def report_id(parts: dict) -> str:
    return parts["terminal"].split(":", 1)[1]


# --- the real experiment -----------------------------------------------------------------------


@real
def test_a_real_experiment_report_walks_back_to_the_ngsim_source_hash() -> None:
    graph = experiment_chain_graph(RUN, world_models=[WORLD_MODEL], datasets=[CSV])
    found = walk(graph, REAL_REPORT_ID)
    assert found.reaches_source
    assert found.path[0].key == f"DecisionReport:{REAL_REPORT_ID}"
    assert found.path[-1].record_type == "DatasetFile"
    assert found.source_sha256 == found.path[-1].content_sha256 == sha(CSV.read_bytes())
    assert found.source_sha256 == NGSIM_SHA256
    assert [r.stage for r in found.path] == [
        "decision_report", "experiment", "experiment", "world_entity", "observation",
        "source_record",
    ]  # fmt: skip
    types = [r.record_type for r in found.path]
    assert types[:4] == ["DecisionReport", "ExperimentManifest", "BaselineManifest",
                         "TrafficWorldModel"]  # fmt: skip
    record = json.loads(
        (
            REPO / "experiments/records/ngsim-int2-realism-experiment-20261028T142330Z.json"
        ).read_text(encoding="utf-8")
    )
    baseline = found.path[2]
    assert baseline.content_sha256 == record["baseline_manifest_sha256"]
    assert found.path[3].content_sha256 == sha(WORLD_MODEL.read_bytes())
    assert not any(b.code == "hash_mismatch" for b in found.breaks)


@real
def test_the_real_chain_says_where_it_is_not_a_full_chain() -> None:
    """The candidates record no proposal hash, so the intervention branch is a typed break and the
    path reaches the source through the baseline's inputs, not through a problem."""
    found = walk(experiment_chain_graph(RUN, world_models=[WORLD_MODEL], datasets=[CSV]),
                 REAL_REPORT_ID)  # fmt: skip
    assert not found.full_chain
    assert ("missing_link", "experiment <- intervention") in {
        (b.code, b.link) for b in found.breaks
    }
    assert "problem_statement" not in {r.stage for r in found.path}


def test_a_report_that_is_not_in_the_graph_is_a_typed_break(tmp_path: Path) -> None:
    _, graph = synthetic(tmp_path)
    found = walk(graph, "0" * 16)
    assert not found.reaches_source and found.path == ()
    assert [(b.code, b.at) for b in found.breaks] == [
        ("unknown_record", "DecisionReport:" + "0" * 16)
    ]


# --- the synthetic chain through every stage ---------------------------------------------------


def test_a_complete_chain_walks_from_the_decision_through_all_seven_stages(tmp_path: Path) -> None:
    parts, graph = synthetic(tmp_path)
    found = walk(graph, report_id(parts))
    assert found.reaches_source and found.full_chain and found.breaks == ()
    stages = list(dict.fromkeys(r.stage for r in found.path))
    assert stages == list(reversed(STAGES))
    assert found.source_sha256 == sha(parts["dataset"].read_bytes())
    assert len(found.path_links) == len(found.path) - 1


def test_without_a_proposal_to_problem_record_the_decision_chain_names_that_link(tmp_path) -> None:
    parts, graph = synthetic(tmp_path, problem_to_proposal=False)
    found = walk(graph, report_id(parts))
    assert not found.reaches_source and not found.full_chain
    assert {(b.code, b.link) for b in found.breaks} == {
        ("missing_link", "intervention <- problem_statement")
    }


def test_the_walk_is_deterministic(tmp_path: Path) -> None:
    parts, graph = synthetic(tmp_path)
    again = assemble(ProvenanceGraph.of(list(reversed(parts["nodes"])), parts["links"]))
    assert again.graph_sha256 == graph.graph_sha256
    assert walk(again, report_id(parts)) == walk(graph, report_id(parts))
    assert ChainGraph.model_validate_json(graph.model_dump_json()) == graph


# --- the experiment's baseline inputs ----------------------------------------------------------


def _experiment(graph: ChainGraph) -> ChainRecord:
    return next(r for r in graph.records if r.record_type == "ExperimentManifest")


def test_only_a_baseline_may_link_an_experiment_to_its_world_model(tmp_path: Path) -> None:
    parts, graph = synthetic(tmp_path, problem_to_proposal=False)
    experiment = _experiment(graph)
    world = next(r for r in graph.records if r.record_type == "TrafficWorldModel")
    skip = ChainLink(
        child=ChainEnd.of(experiment), parent=ChainEnd.of(world), basis="test", kind="input"
    )
    found = walk(
        ChainGraph.of(graph.records, (*graph.links, skip), graph.refused), report_id(parts)
    )
    assert any(b.code == "wrong_stage" and "input" in b.reason for b in found.breaks)
    assert not found.reaches_source


def test_a_baseline_input_reaches_the_source_when_no_problem_is_recorded(tmp_path: Path) -> None:
    parts, graph = synthetic(tmp_path, problem_to_proposal=False)
    experiment = _experiment(graph)
    wm_hash = pv.world_model_sha256(parts["wm"])
    raw = json.dumps({"experiment_id": "base-1", "world_model_sha256": wm_hash}).encode()
    records, links = baseline_input(experiment, "base-1", sha(raw), wm_hash, raw)
    found = walk(ChainGraph.of((*graph.records, *records), (*graph.links, *links)),
                 report_id(parts))  # fmt: skip
    assert found.reaches_source and not found.full_chain
    assert [r.record_type for r in found.path][:3] == [
        "DecisionReport", "ExperimentManifest", "BaselineManifest"]  # fmt: skip
    edited = baseline_input(experiment, "base-1", "a" * 64, wm_hash, raw)
    bad = walk(ChainGraph.of((*graph.records, *edited[0]), (*graph.links, *edited[1])),
               report_id(parts))  # fmt: skip
    assert any(b.code == "hash_mismatch" for b in bad.breaks)


# --- NGSIM observations ------------------------------------------------------------------------


def test_ngsim_measurements_map_to_dataset_observations_not_sam_ones(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    wm = parts["wm"]
    records, links = ngsim_observations(wm, pv.world_model_sha256(wm))
    types = {r.record_type for r in records}
    queued = {"NgsimQueueSample"} if wm.queues else set()
    assert types == {"NgsimTrackPoints", "NgsimMovementCount"} | queued
    assert all(r.stage == "observation" and r.category == "dataset_derived" for r in records)
    assert not any(r.model_output for r in records)  # never SAM output
    # positions are NGSIM's; counts and queues are CairoFlow's rules, labelled as the model is
    kinds = {r.record_type: r.evidence_kind for r in records}
    assert kinds["NgsimTrackPoints"] == "observed"
    assert kinds["NgsimMovementCount"] == wm.provenance.kind
    at = "@" + pv.world_model_sha256(wm)[:12]  # named by what it is and whose snapshot
    counts = [r for r in records if r.record_type == "NgsimMovementCount"]
    assert sorted(r.record_id for r in counts) == sorted(
        f"mv.{m.approach}{m.exit_arm}{at}" for m in wm.movements
    )
    lanes = {q.lane_id for q in wm.queues}
    assert {r.record_id for r in records if r.record_type == "NgsimQueueSample"} == {
        f"queue.{lane}{at}" for lane in lanes
    }
    dataset = [link for link in links if link.parent.record_type == "DatasetFile"]
    assert [link.parent.stated_sha256 for link in dataset] == [wm.ground_source_sha256]
    assert records == ngsim_observations(wm, pv.world_model_sha256(wm))[0]  # deterministic


def _assembled_view(tmp_path: Path) -> tuple[dict, ChainGraph]:
    """The synthetic chain with every entity of its view, assembled with view and model."""
    parts = chain(tmp_path)
    entities, entity_links = pv.world_view_links(parts["view"])
    nodes = {n.key: n for n in (*parts["nodes"], *entities)}
    links = {link.key: link for link in (*parts["links"], *entity_links)}
    base = ProvenanceGraph.of(list(nodes.values()), list(links.values()))
    return parts, assemble(base, world_model=parts["wm"], view=parts["view"])


def test_a_movement_entity_walks_through_its_ngsim_count_to_the_dataset(tmp_path: Path) -> None:
    parts, graph = _assembled_view(tmp_path)
    entity = next(r for r in graph.records if r.record_id.startswith("movement:"))
    parents = {link.parent.record_type for link in graph.links if link.child.key == entity.key}
    assert parents == {"NgsimMovementCount"}  # not the snapshot as a whole
    found = walk_from(graph, entity.key)
    assert found.reaches_source
    assert "NgsimMovementCount" in [r.record_type for r in found.path]
    assert found.source_sha256 == sha(parts["dataset"].read_bytes())


def test_a_geometry_entity_is_not_traced_to_the_trajectories(tmp_path: Path) -> None:
    _, graph = _assembled_view(tmp_path)
    lane = next(r for r in graph.records if r.record_id.startswith("lane:"))
    found = walk_from(graph, lane.key)
    assert not found.reaches_source
    assert [(b.code, b.link) for b in found.breaks] == [
        ("missing_link", "world_entity <- observation")
    ]


def test_a_view_needs_its_own_world_model(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    base = ProvenanceGraph.of(parts["nodes"], parts["links"])
    with pytest.raises(ValueError, match="with its world model"):
        assemble(base, view=parts["view"])
    other = world_model(GEO)
    with pytest.raises(ValueError, match="another world model"):
        assemble(base, world_model=other, view=parts["view"])


def test_two_different_records_under_one_key_are_an_error(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    wm = parts["wm"]
    own = next(r for r in ngsim_observations(wm, pv.world_model_sha256(wm))[0]
               if r.record_type == "NgsimMovementCount")  # fmt: skip
    foreign = own.model_copy(update={"content_sha256": "a" * 64})
    with pytest.raises(ValueError, match="two different records"):
        assemble(ProvenanceGraph.of(parts["nodes"], parts["links"]), world_model=wm,
                 records=[foreign])  # fmt: skip


def test_every_broken_link_from_one_record_is_its_own_break(tmp_path: Path) -> None:
    parts, graph = synthetic(tmp_path)
    experiment = _experiment(graph)
    worlds = [
        ChainRecord(stage="world_entity", record_type="TrafficWorldModel", record_id=d * 64,
                    content_sha256=d * 64, hash_basis="model_dump_json", category="world_model",
                    evidence_kind="observed")
        for d in ("1", "2")
    ]  # fmt: skip
    skips = [ChainLink(child=ChainEnd.of(experiment), parent=ChainEnd.of(w), basis="test",
                       kind="input") for w in worlds]  # fmt: skip
    found = walk_from(
        ChainGraph.of((*graph.records, *worlds), (*graph.links, *skips)), experiment.key
    )
    assert len([b for b in found.breaks if b.code == "wrong_stage"]) == 2


def test_a_calibration_anchored_model_has_no_ngsim_observations() -> None:
    wm = world_model(GEO)
    assert wm.ground_source_sha256 is None
    assert ngsim_observations(wm, pv.world_model_sha256(wm)) == ([], [])


# --- the camera layer --------------------------------------------------------------------------


def _camera(accepted: bool) -> tuple[TrafficWorldModel, ProvenanceGraph]:
    """The synthetic model with the committed camera layer attached (no WorldView: the int2
    geometry the layer's lanes belong to is Git-ignored)."""
    clip = SourceClip.model_validate_json((CAMERA_DIR / "source_clip.json").read_text("utf-8"))
    layer = CameraLayer.model_validate_json((CAMERA_DIR / "camera_layer.json").read_text("utf-8"))
    layer = layer.model_copy(update={"calibration_accepted": accepted})
    data = world_model(GEO).model_dump(mode="json") | {"camera": layer.model_dump(mode="json")}
    wm = TrafficWorldModel.model_validate(data)
    nodes = [
        pv.source_clip_node(clip),
        *(pv.sam_response_node(k) for k in layer.sam_request_keys),
        pv.track_file_node(CAMERA_DIR / "tracks.json", "camera_derived", "approximated"),
        pv.world_model_node(wm),
    ]
    return wm, ProvenanceGraph.of(nodes, pv.world_model_links(wm))


def test_the_committed_camera_layer_is_not_accepted_as_recorded() -> None:
    layer = CameraLayer.model_validate_json((CAMERA_DIR / "camera_layer.json").read_text("utf-8"))
    assert layer.calibration_accepted is False


def test_camera_tracks_into_the_world_model_are_a_typed_break_citing_the_calibration() -> None:
    wm, base = _camera(accepted=False)
    graph = assemble(base, world_model=wm)
    found = walk_from(graph, pv.world_model_node(wm).key)
    refused = [b for b in found.breaks if b.code == "not_accepted"]
    assert len(refused) == 1 and refused[0].link == "world_entity <- observation"
    assert CAMERA_NOT_ACCEPTED == "calibration not accepted (ADR-017/018)"
    assert CAMERA_NOT_ACCEPTED in refused[0].reason
    assert "camera_derived" not in {r.category for r in found.path}  # refused, never a path


def test_an_accepted_calibration_would_connect_the_camera_layer() -> None:
    wm, base = _camera(accepted=True)
    graph = assemble(base, world_model=wm)
    assert graph.refused == ()
    found = walk_from(graph, pv.world_model_node(wm).key)
    assert found.reaches_source and found.path[-1].record_type == "SourceClip"
    assert not any(b.code == "not_accepted" for b in found.breaks)


def test_camera_links_are_refused_exactly_when_the_calibration_is_not_accepted() -> None:
    wm, _ = _camera(accepted=False)
    links, refused = camera_links(wm)
    assert links == [] and len(refused) == 1
    assert all(isinstance(r, RefusedLink) and CAMERA_NOT_ACCEPTED in r.reason for r in refused)
    assert {r.parent.record_type for r in refused} == {"TrackFile"}
    accepted_links, none = camera_links(_camera(accepted=True)[0])
    assert none == [] and len(accepted_links) == len(refused)
    assert camera_links(world_model(GEO)) == ([], [])  # no camera layer, nothing to refuse


CAMERA_RUN = REPO / "experiments/runs/cairoflow-20261030T235016Z-r1"
INT2_GEOMETRY = REPO / "data/geometry/ngsim-lankershim-int2/geometry.json"


@pytest.mark.skipif(
    not all(p.is_file() for p in (CAMERA_RUN / "world_model.json", INT2_GEOMETRY, CSV)),
    reason="the camera-fed run, the int2 geometry and the NGSIM file are Git-ignored",
)
def test_a_camera_world_entity_is_a_typed_break_and_an_ngsim_one_reaches_the_source() -> None:
    wm = TrafficWorldModel.model_validate_json((CAMERA_RUN / "world_model.json").read_bytes())
    assert wm.camera is not None and not wm.camera.calibration_accepted
    view = build_world_view(
        wm, IntersectionGeometry.model_validate_json(INT2_GEOMETRY.read_bytes())
    )
    entities, entity_links = pv.world_view_links(view)
    nodes = [pv.world_model_node(wm), *entities, pv.dataset_file_node(CSV),
             pv.track_file_node(CSV, "dataset_derived", "observed")]  # fmt: skip
    graph = assemble(ProvenanceGraph.of(nodes, [*pv.world_model_links(wm), *entity_links]),
                     world_model=wm, view=view)  # fmt: skip
    track = next(r for r in graph.records if r.record_id.startswith("camera_track:"))
    found = walk_from(graph, track.key)
    assert not found.reaches_source
    assert [(b.code, b.link) for b in found.breaks] == [
        ("not_accepted", "world_entity <- observation")
    ]
    assert CAMERA_NOT_ACCEPTED in found.breaks[0].reason
    movement = next(r for r in graph.records if r.record_id.startswith("movement:"))
    counted = walk_from(graph, movement.key)
    assert counted.reaches_source and counted.source_sha256 == NGSIM_SHA256
    assert "NgsimMovementCount" in [r.record_type for r in counted.path]


def test_a_refused_link_cannot_also_be_a_link() -> None:
    wm, base = _camera(accepted=False)
    graph = assemble(base, world_model=wm)
    refused = graph.refused[0]
    as_link = ChainLink(child=refused.child, parent=refused.parent, basis=refused.basis)
    with pytest.raises(ValidationError, match="refused"):
        ChainGraph.of(graph.records, (*graph.links, as_link), graph.refused)


def test_record_types_keep_their_stage_and_category() -> None:
    node = pv.sam_response_node("a" * 64)
    assert record_of(node).model_output and record_of(node).stage == "observation"
    with pytest.raises(ValidationError):
        ChainRecord(
            stage="observation", record_type="NgsimMovementCount", record_id="mv.SN",
            content_sha256="a" * 64, category="camera_derived", evidence_kind="observed",
        )  # fmt: skip
