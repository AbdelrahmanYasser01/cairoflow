"""Provenance links, SourceRecord to DecisionReport (ADR-033 addendum).

Synthetic records build a complete chain; each test then removes or spoils one thing and checks
that `walk` names the break instead of filling it. The camera branch uses the committed camera
layer, its clip and its tracks file, so it runs on a fresh clone.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from synthetic_scenario import scenario_geometry, world_model

from cairoflow_contracts.evidence import CameraLayer, SourceClip, TrafficWorldModel
from cairoflow_contracts.experiment import (
    CandidateRecord,
    CompiledNetworkManifest,
    ExperimentManifest,
    RunSettings,
    SignalPhaseSpec,
    SignalProgramSpec,
)
from cairoflow_contracts.provenance import (
    MEASURED,
    RECORD_TYPES,
    STAGES,
    LinkEnd,
    NodeRef,
    ProblemStatement,
    ProvenanceGraph,
    ProvenanceLink,
    walk,
)
from cairoflow_contracts.reasoning import InterventionProposal, PhaseChange, ValidationOutcome
from cairoflow_contracts.report import DecisionReport
from cairoflow_contracts.worldview import WorldView
from cairoflow_evaluation import provenance as ev
from cairoflow_perception import provenance as pv
from cairoflow_perception.worldview import build_world_view
from cairoflow_simulation.validation import proposal_sha256

REPO = Path(__file__).resolve().parents[1]
CAMERA_DIR = REPO / "experiments/records/ngsim-cam2-world-model-v3-20261030T230506Z"
GEO = scenario_geometry()
WHEN = datetime(2026, 11, 2, tzinfo=UTC)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ground_model(dataset: Path) -> TrafficWorldModel:
    """The synthetic world model, re-anchored on a dataset file that is also its tracks input."""
    digest = sha(dataset.read_bytes())
    data = world_model(GEO).model_dump(mode="json")
    data |= {"calibration_spec_sha256": None, "ground_source_sha256": digest,
             "tracks_sha256": digest}  # fmt: skip
    return TrafficWorldModel.model_validate(data)


def proposal() -> InterventionProposal:
    return InterventionProposal(
        intervention_id="I1", type="signal_timing", target_program="test_cycle",
        phase_changes=(PhaseChange(phase_name="p0", green_s=25),), rationale="model text",
        expected_tradeoffs=(), preconditions=(), addresses_hypothesis_ids=("H1",),
        evidence_ids=("mv.SN",),
    )  # fmt: skip


def manifest_bytes(digest: str | None) -> bytes:
    program = SignalProgramSpec(
        program_id="test_cycle", timeline_id="synthetic-cycle", label="assumed",
        annotation_sha256=None, cycle_start_s=0, cycle_s=30,
        phases=(SignalPhaseSpec(name="p0", duration_s=30, state="GGrr",
                                arm_states={"N": "green", "S": "green", "E": "red", "W": "red"}),),
    )  # fmt: skip
    network = CompiledNetworkManifest(
        geometry_id=GEO.geometry_id, geometry_sha256=sha(GEO.model_dump_json().encode()),
        sumo_version="test", plain_xml_sha256={}, net_sha256="d" * 64, link_count=1,
        parameters=(), signal_program=program, netconvert_warnings=(), notes=(),
    )  # fmt: skip
    outcome = ValidationOutcome(
        intervention_id="I1", status="accepted", reasons=(), program_id="test_cycle",
        phase_durations_s=(25.0,), cycle_s=25.0, baseline_cycle_s=30.0, validated_at_utc=WHEN,
        validator_version="0.2.0", proposal_sha256=digest,
    )  # fmt: skip
    manifest = ExperimentManifest(
        experiment_id="exp-1", created_at_utc=WHEN, baseline_dir="../baseline",
        baseline_experiment_id="base-1", baseline_manifest_sha256="e" * 64,
        settings=RunSettings(),
        candidates=(CandidateRecord(intervention_id="I1", outcome=outcome, network=network,
                                    runs=(), valid=True, reasons=()),),
        skipped=(), valid=True, reasons=(),
    )  # fmt: skip
    return manifest.model_dump_json().encode("utf-8")


def report(experiment_id: str = "exp-1") -> DecisionReport:
    return DecisionReport(
        report_id="r" * 16, experiment_id=experiment_id, created_at_utc=WHEN, objective="o",
        selection_rule="s", selected_intervention_id=None, selection_reason="none selected",
        hypotheses=(), candidates=(), numbers=(), limitations=(), reproducibility={},
    )  # fmt: skip


def problem(view: WorldView, entity_ids: tuple[str, ...] | None = None) -> ProblemStatement:
    ids = entity_ids or tuple(sorted(e.entity_id for e in view.of_type("movement")[:2]))
    return ProblemStatement(
        problem_id="stops.majority", rule_id="stop-majority", rule_version="0.1.0",
        statement="most vehicles stop at least once on these movements",
        world_model_sha256=view.world_model_sha256, view_sha256=view.view_sha256,
        entity_ids=ids,
    )  # fmt: skip


def chain(tmp_path: Path, *, problem_to_proposal: bool = True) -> dict:
    """Every record of a complete chain, the nodes and the links the builders make from them."""
    dataset = tmp_path / "trajectories.csv"
    dataset.write_bytes(b"Vehicle_ID,Frame_ID,Local_X,Local_Y\n1,1,0.0,0.0\n")
    wm = ground_model(dataset)
    view = build_world_view(wm, GEO)
    ps = problem(view)
    entities, entity_links = pv.world_view_links(view, ps.entity_ids)
    prop = proposal()
    experiment, manifest = ev.experiment_node(manifest_bytes(proposal_sha256(prop)))
    rep = report()
    nodes = [
        pv.dataset_file_node(dataset),
        pv.track_file_node(dataset, "dataset_derived", "observed"),
        pv.world_model_node(wm), *entities, pv.problem_node(ps), ev.proposal_node(prop),
        experiment, ev.report_node(rep),
    ]  # fmt: skip
    links = [
        *pv.world_model_links(wm), *entity_links, *pv.problem_links(ps, view),
        *ev.experiment_links(experiment, manifest), *ev.report_links(rep, experiment),
    ]  # fmt: skip
    if problem_to_proposal:  # no record states this link yet: stated here, as a future one would
        problem_end = pv.problem_node(ps)
        links.append(
            ProvenanceLink(
                child=LinkEnd(record_type="InterventionProposal", record_id=proposal_sha256(prop),
                              stated_sha256=proposal_sha256(prop)),
                parent=LinkEnd(record_type="ProblemStatement", record_id=ps.problem_id,
                               stated_sha256=problem_end.content_sha256),
                basis="test: a proposal record that names its problem",
            )
        )  # fmt: skip
    return {"wm": wm, "view": view, "problem": ps, "nodes": nodes, "links": links,
            "terminal": ev.report_node(rep).key, "dataset": dataset}  # fmt: skip


def graph(parts: dict) -> ProvenanceGraph:
    return ProvenanceGraph.of(parts["nodes"], parts["links"])


# --- complete chain ---------------------------------------------------------------------------


def test_a_complete_chain_returns_the_whole_path_from_source_to_report(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    trace = walk(graph(parts), parts["terminal"])
    assert trace.complete and trace.breaks == ()
    stages = [n.stage for n in trace.nodes]
    assert stages == sorted(stages, key=STAGES.index)
    assert set(stages) == set(STAGES)
    assert trace.nodes[0].record_type == "DatasetFile"
    assert trace.nodes[-1].record_type == "DecisionReport"
    assert trace.categories == ("dataset_derived", "world_model", "simulated", "ai_generated")
    # the report names its experiment by id only: matched, and said to be matched by id only
    assert trace.unhashed_links == (
        f"{parts['terminal']} <- ExperimentManifest:exp-1 (DecisionReport.experiment_id)",
    )


def test_the_trace_is_deterministic_whatever_the_input_order(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    first = walk(graph(parts), parts["terminal"]).model_dump_json()
    shuffled = dict(parts)
    rng = random.Random(7)
    shuffled["nodes"] = rng.sample(parts["nodes"], len(parts["nodes"]))
    shuffled["links"] = rng.sample(parts["links"], len(parts["links"]))
    assert graph(shuffled).graph_sha256 == graph(parts).graph_sha256
    assert walk(graph(shuffled), parts["terminal"]).model_dump_json() == first
    assert walk(graph(chain(tmp_path)), parts["terminal"]).model_dump_json() == first


def test_the_canonical_models_are_left_untouched(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    before = (parts["wm"].model_dump_json(), parts["view"].model_dump_json())
    walk(graph(parts), parts["terminal"])
    assert (parts["wm"].model_dump_json(), parts["view"].model_dump_json()) == before
    assert pv.world_model_sha256(parts["wm"]) == parts["view"].world_model_sha256


# --- breaks -------------------------------------------------------------------------------------


def test_without_a_record_tying_proposal_to_problem_the_chain_breaks_there(tmp_path: Path) -> None:
    """The real state: proposals cite hypotheses, so the builders make no such link."""
    parts = chain(tmp_path, problem_to_proposal=False)
    trace = walk(graph(parts), parts["terminal"])
    assert not trace.complete
    [only] = trace.breaks
    assert only.code == "missing_link"
    assert only.at.startswith("InterventionProposal:")
    assert only.missing == "intervention <- problem_statement"
    assert {n.stage for n in trace.nodes} == {"intervention", "experiment", "decision_report"}


def test_a_candidate_without_a_proposal_hash_links_to_nothing(tmp_path: Path) -> None:
    experiment, manifest = ev.experiment_node(manifest_bytes(None))
    assert ev.experiment_links(experiment, manifest) == []
    trace = walk(ProvenanceGraph.of([experiment], []), experiment.key)
    assert [(b.code, b.missing) for b in trace.breaks] == [
        ("missing_link", "experiment <- intervention")
    ]


def test_an_absent_record_is_an_unknown_node_not_a_gap_filled(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    parts["nodes"] = [n for n in parts["nodes"] if n.record_type != "DatasetFile"]
    trace = walk(graph(parts), parts["terminal"])
    [only] = trace.breaks
    assert only.code == "wrong_entity_type"  # the dataset file's id is the track file's here
    assert "DatasetFile" in only.reason and "TrackFile" in only.reason
    parts["nodes"] = [n for n in parts["nodes"] if n.record_type != "TrackFile"]
    trace = walk(graph(parts), parts["terminal"])
    assert [b.code for b in trace.breaks] == ["unknown_node"]
    assert trace.breaks[0].at.startswith("TrafficWorldModel:")


def test_a_changed_record_is_a_hash_mismatch(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    target = next(n for n in parts["nodes"] if n.record_type == "WorldEntity")
    tampered = target.model_copy(update={"content_sha256": "0" * 64})
    parts["nodes"] = [tampered if n is target else n for n in parts["nodes"]]
    trace = walk(graph(parts), parts["terminal"])
    codes = {b.code for b in trace.breaks}
    assert codes == {"hash_mismatch"}
    assert all(target.key in b.reason for b in trace.breaks)


def test_a_dataset_file_with_other_bytes_does_not_stand_in(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    other = tmp_path / "other.csv"
    other.write_bytes(b"Vehicle_ID\n2\n")
    parts["nodes"] = [
        pv.dataset_file_node(other) if n.record_type == "DatasetFile" else n for n in parts["nodes"]
    ]
    trace = walk(graph(parts), parts["terminal"])
    assert [b.code for b in trace.breaks] == ["wrong_entity_type"]


def test_a_link_that_skips_a_stage_is_refused(tmp_path: Path) -> None:
    parts = chain(tmp_path, problem_to_proposal=False)
    entity = next(n for n in parts["nodes"] if n.record_type == "WorldEntity")
    prop = next(n for n in parts["nodes"] if n.record_type == "InterventionProposal")
    parts["links"].append(
        ProvenanceLink(
            child=LinkEnd(record_type=prop.record_type, record_id=prop.record_id),
            parent=LinkEnd(record_type=entity.record_type, record_id=entity.record_id),
            basis="test: skips problem_statement",
        )
    )
    trace = walk(graph(parts), parts["terminal"])
    assert [b.code for b in trace.breaks] == ["wrong_stage"]
    assert trace.breaks[0].missing == "intervention <- world_entity"


def test_a_cycle_is_a_break(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    a, b = [n for n in parts["nodes"] if n.record_type == "WorldEntity"]
    for child, parent in ((a, b), (b, a)):
        parts["links"].append(
            ProvenanceLink(
                child=LinkEnd(record_type=child.record_type, record_id=child.record_id),
                parent=LinkEnd(record_type=parent.record_type, record_id=parent.record_id),
                basis="test: circular",
            )
        )
    trace = walk(graph(parts), parts["terminal"])
    assert [x.code for x in trace.breaks] == ["cycle"]


def test_an_unknown_terminal_is_reported(tmp_path: Path) -> None:
    trace = walk(graph(chain(tmp_path)), "DecisionReport:nope")
    assert [(b.code, b.missing) for b in trace.breaks] == [("unknown_node", "the record itself")]


# --- graph integrity ----------------------------------------------------------------------------


def test_duplicate_records_and_links_are_refused(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    with pytest.raises(ValidationError, match="duplicate node"):
        ProvenanceGraph.of([*parts["nodes"], parts["nodes"][0]], parts["links"])
    with pytest.raises(ValidationError, match="a link may be stated once"):
        ProvenanceGraph.of(parts["nodes"], [*parts["links"], parts["links"][0]])


def test_a_graph_out_of_order_or_with_a_wrong_hash_is_refused(tmp_path: Path) -> None:
    good = graph(chain(tmp_path))
    data = json.loads(good.model_dump_json())
    data["nodes"] = list(reversed(data["nodes"]))
    with pytest.raises(ValidationError, match="canonical"):
        ProvenanceGraph.model_validate(data)
    data = json.loads(good.model_dump_json()) | {"graph_sha256": "0" * 64}
    with pytest.raises(ValidationError, match="does not hash"):
        ProvenanceGraph.model_validate(data)
    assert ProvenanceGraph.model_validate_json(good.model_dump_json()) == good


# --- categories ---------------------------------------------------------------------------------


def test_categories_belong_to_their_record_types_and_measured_ones_come_first() -> None:
    first_unmeasured = min(
        STAGES.index(stage) for stage, cats in RECORD_TYPES.values() if not cats <= MEASURED
    )
    for stage, cats in RECORD_TYPES.values():
        assert cats <= MEASURED or not cats & MEASURED  # no type is both
        if cats <= MEASURED:
            assert STAGES.index(stage) < first_unmeasured


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"record_type": "InterventionProposal", "stage": "intervention",
          "category": "world_model"}, "cannot be world_model"),
        ({"record_type": "InterventionProposal", "stage": "intervention",
          "category": "ai_generated", "evidence_kind": "assumed"}, "ai_generated record"),
        ({"record_type": "WorldEntity", "stage": "world_entity", "category": "world_model",
          "evidence_kind": "simulated"}, "simulated"),
        ({"record_type": "TrackFile", "stage": "observation", "category": "dataset_derived",
          "evidence_kind": "model_generated"}, "model output"),
        ({"record_type": "ExperimentManifest", "stage": "observation", "category": "simulated",
          "evidence_kind": "simulated"}, "experiment record"),
    ],
)  # fmt: skip
def test_a_record_cannot_be_relabelled(fields: dict, message: str) -> None:
    base = {"record_id": "x", "content_sha256": "a" * 64, "hash_basis": "file_bytes",
            "evidence_kind": "observed"}  # fmt: skip
    with pytest.raises(ValidationError, match=message):
        NodeRef(**(base | fields))


def test_model_outputs_stay_labelled_as_model_outputs(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    prop = next(n for n in parts["nodes"] if n.record_type == "InterventionProposal")
    assert (prop.category, prop.evidence_kind, prop.model_output) == (
        "ai_generated", "model_generated", True
    )  # fmt: skip
    sam = pv.sam_response_node("f" * 64)
    assert (sam.category, sam.evidence_kind, sam.model_output) == (
        "camera_derived", "model_generated", True
    )  # fmt: skip
    trace = walk(graph(parts), parts["terminal"])
    by_key = {n.key: n for n in parts["nodes"]}
    assert all(n == by_key[n.key] for n in trace.nodes)  # nothing on the path is relabelled
    simulated = [n for n in trace.nodes if n.category == "simulated"]
    assert {n.record_type for n in simulated} == {"ExperimentManifest", "DecisionReport"}
    assert all(n.category in MEASURED for n in trace.nodes if n.stage in STAGES[:4])


def test_the_committed_camera_layer_traces_to_its_clip_through_sam_output() -> None:
    clip = SourceClip.model_validate_json((CAMERA_DIR / "source_clip.json").read_text("utf-8"))
    layer = CameraLayer.model_validate_json((CAMERA_DIR / "camera_layer.json").read_text("utf-8"))
    data = world_model(GEO).model_dump(mode="json") | {"camera": layer.model_dump(mode="json")}
    wm = TrafficWorldModel.model_validate(data)
    nodes = [
        pv.source_clip_node(clip),
        *(pv.sam_response_node(k) for k in layer.sam_request_keys),
        pv.track_file_node(CAMERA_DIR / "tracks.json", "camera_derived", "approximated"),
        pv.world_model_node(wm),
    ]
    trace = walk(ProvenanceGraph.of(nodes, pv.world_model_links(wm)), pv.world_model_node(wm).key)
    camera_breaks = [b for b in trace.breaks if "SamResponse" in b.at or "camera" in b.reason]
    assert camera_breaks == []
    reached = {n.record_type for n in trace.nodes}
    assert {"SourceClip", "SamResponse", "TrackFile"} <= reached
    # the synthetic model's own tracks ("b" * 64) are a file nobody supplied: said, not filled
    assert [b.code for b in trace.breaks] == ["unknown_node"]
    assert "TrackFile:" + "b" * 64 in trace.breaks[0].reason


# --- builders refuse records that are not tied ----------------------------------------------------


def test_a_problem_about_another_view_gets_no_link(tmp_path: Path) -> None:
    parts = chain(tmp_path)
    other = build_world_view(world_model(GEO), GEO)
    with pytest.raises(pv.ProvenanceError, match="another WorldView"):
        pv.problem_links(parts["problem"], other)
    with pytest.raises(pv.ProvenanceError, match="no entity"):
        pv.world_view_links(parts["view"], ["lane:NOPE"])


def test_a_report_about_another_experiment_gets_no_link() -> None:
    experiment, _ = ev.experiment_node(manifest_bytes("a" * 64))
    with pytest.raises(ev.ProvenanceError, match="exp-2"):
        ev.report_links(report("exp-2"), experiment)


def _script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("trace", REPO / "scripts/trace_provenance.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_trace_script_refuses_a_directory_without_records(tmp_path: Path) -> None:
    assert _script().main([str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []


RUN = REPO / "experiments/runs/cairoflow-20261030T235016Z-r1"
CSV = REPO / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"


@pytest.mark.skipif(
    not ((RUN / "report.json").is_file() and CSV.is_file()),
    reason="the run directory and the NGSIM file are Git-ignored",
)
def test_the_recorded_camera_fed_run_breaks_only_between_problem_and_proposal(capsys) -> None:
    script = _script()
    assert script.main([str(RUN)]) == 1
    trace = json.loads(capsys.readouterr().out)
    assert {(b["code"], b["missing"]) for b in trace["breaks"]} == {
        ("missing_link", "intervention <- problem_statement")
    }
    assert script.main([str(RUN), "--start", "world_model"]) == 0
    below = json.loads(capsys.readouterr().out)
    assert below["categories"] == ["camera_derived", "dataset_derived", "world_model"]


def test_a_problem_statement_names_sorted_entities_and_holds_no_number(tmp_path: Path) -> None:
    view = chain(tmp_path)["view"]
    with pytest.raises(ValidationError, match="sorted and unique"):
        problem(view, ("movement:SN", "movement:NS"))
    with pytest.raises(ValidationError, match="entity id"):
        problem(view, ("not an id",))
    numeric = {
        name for name, field in ProblemStatement.model_fields.items()
        if field.annotation in (int, float)
    }  # fmt: skip
    assert numeric == set()
    assert problem(view).produced_by == "rule"
