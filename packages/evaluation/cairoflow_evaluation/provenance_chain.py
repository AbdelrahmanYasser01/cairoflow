"""The decision chain of a recorded experiment run, read from its files (ADR-033).

`experiment_chain_graph` builds the chain graph (cairoflow_contracts.provenance_chain) of a run
directory that holds report.json and experiment/experiment_manifest.json: the report, the
experiment and the proposals its candidates name (the frozen layer's builders), the baseline
manifest the experiment cites and the world model snapshot that baseline was compiled from (by
the hash the manifest states; the first of `world_models` with that hash), the snapshot's NGSIM
observations, and the dataset files given. A record that is not supplied stays absent, and the
walk names the gap. It reads, and writes nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from cairoflow_contracts.evidence import IntersectionGeometry, TrafficWorldModel
from cairoflow_contracts.experiment import BaselineManifest
from cairoflow_contracts.provenance import NodeRef, ProvenanceGraph, ProvenanceLink
from cairoflow_contracts.provenance_chain import ChainGraph, assemble, baseline_input, record_of
from cairoflow_contracts.reasoning import ReasoningResult
from cairoflow_contracts.report import DecisionReport
from cairoflow_evaluation import provenance as ev
from cairoflow_evaluation.compare import BASELINE_FILE, EXPERIMENT_FILE
from cairoflow_perception import provenance as pv
from cairoflow_perception.worldview import build_world_view

REPORT_FILE = "report.json"
REASONING_FILE = "reasoning.json"


def _matching_world_model(paths: Iterable[Path], digest: str) -> TrafficWorldModel | None:
    for path in paths:
        if not path.is_file():
            continue
        world = TrafficWorldModel.model_validate_json(path.read_bytes())
        if pv.world_model_sha256(world) == digest:
            return world
    return None


def experiment_chain_graph(
    run_dir: Path,
    *,
    world_models: Iterable[Path] = (),
    datasets: Iterable[Path] = (),
    geometry: Path | None = None,
) -> ChainGraph:
    """The chain graph of the run in `run_dir` (report.json, experiment/ and its baseline)."""
    report = DecisionReport.model_validate_json((run_dir / REPORT_FILE).read_bytes())
    experiment_dir = run_dir / "experiment"
    experiment, manifest = ev.experiment_node((experiment_dir / EXPERIMENT_FILE).read_bytes())
    nodes: list[NodeRef] = [experiment, ev.report_node(report)]
    links: list[ProvenanceLink] = [
        *ev.experiment_links(experiment, manifest),
        *ev.report_links(report, experiment),
    ]
    if (run_dir / REASONING_FILE).is_file():
        result = ReasoningResult.model_validate_json((run_dir / REASONING_FILE).read_bytes())
        nodes += [ev.proposal_node(p) for p in result.output.interventions]
    baseline_dir = (experiment_dir / manifest.baseline_dir).resolve()
    raw = (baseline_dir / BASELINE_FILE).read_bytes()
    baseline = BaselineManifest.model_validate_json(raw)
    world = _matching_world_model(world_models, baseline.world_model_sha256)
    view = None
    if world is not None:
        nodes.append(pv.world_model_node(world))
        links += pv.world_model_links(world)
        if geometry is not None and geometry.is_file():
            view = build_world_view(
                world, IntersectionGeometry.model_validate_json(geometry.read_bytes())
            )
            entity_nodes, entity_links = pv.world_view_links(view)
            nodes += entity_nodes
            links += entity_links
    for dataset in datasets:
        if dataset.is_file():
            nodes += [
                pv.dataset_file_node(dataset),
                pv.track_file_node(dataset, "dataset_derived", "observed"),
            ]
    unique = {node.key: node for node in nodes}  # a file can be both the dataset and the tracks
    records, inputs = baseline_input(
        record_of(experiment), baseline.experiment_id, manifest.baseline_manifest_sha256,
        baseline.world_model_sha256, raw,
    )  # fmt: skip
    return assemble(
        ProvenanceGraph.of(list(unique.values()), links),
        world_model=world, view=view, records=records, links=inputs,
    )  # fmt: skip
