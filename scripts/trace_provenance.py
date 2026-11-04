"""Trace a recorded pipeline run from its decision report back to its sources (read-only).

Builds the provenance graph (cairoflow_contracts.provenance) from the records a run directory
holds: report.json, experiment/experiment_manifest.json, reasoning.json, world_model.json, the
geometry, the dataset file and the camera records, if present. Then walks it from the report and
prints the trace as JSON: the path it could follow and every break, typed. It writes nothing,
runs no simulation and calls no API. A record that is missing is left out, and the trace says so.

    uv run --frozen python scripts/trace_provenance.py experiments/runs/<run>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cairoflow_contracts.evidence import IntersectionGeometry, SourceClip, TrafficWorldModel
from cairoflow_contracts.provenance import ProvenanceGraph, walk
from cairoflow_contracts.reasoning import ReasoningResult
from cairoflow_contracts.report import DecisionReport
from cairoflow_evaluation import provenance as ev
from cairoflow_perception import provenance as pv
from cairoflow_perception.worldview import WorldViewError, build_world_view

REPO = Path(__file__).resolve().parents[1]
GEOMETRY = REPO / "data/geometry/ngsim-lankershim-int2/geometry.json"
DATASET = REPO / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
CAMERA = REPO / "experiments/records/ngsim-cam2-world-model-v3-20261030T230506Z"


def build(
    run: Path, geometry: Path, dataset: Path, camera: Path
) -> tuple[ProvenanceGraph, dict[str, str]]:
    """The graph of the run's records and the node keys a walk can start from."""
    world = TrafficWorldModel.model_validate_json((run / "world_model.json").read_bytes())
    report = DecisionReport.model_validate_json((run / "report.json").read_bytes())
    experiment, manifest = ev.experiment_node(
        (run / "experiment" / "experiment_manifest.json").read_bytes()
    )
    nodes = [pv.world_model_node(world), experiment, ev.report_node(report)]
    links = [
        *pv.world_model_links(world),
        *ev.experiment_links(experiment, manifest),
        *ev.report_links(report, experiment),
    ]
    reasoning = run / "reasoning.json"
    if reasoning.is_file():
        result = ReasoningResult.model_validate_json(reasoning.read_bytes())
        nodes += [ev.proposal_node(p) for p in result.output.interventions]
    if dataset.is_file():
        nodes += [
            pv.dataset_file_node(dataset),
            pv.track_file_node(dataset, "dataset_derived", "observed"),
        ]
    if world.camera is not None and camera.is_dir():
        clip = SourceClip.model_validate_json((camera / "source_clip.json").read_bytes())
        nodes += [
            pv.source_clip_node(clip),
            pv.track_file_node(camera / "tracks.json", "camera_derived", "approximated"),
            *(pv.sam_response_node(k) for k in world.camera.sam_request_keys),
        ]
    try:
        geo = IntersectionGeometry.model_validate_json(geometry.read_bytes())
        view = build_world_view(world, geo)
        entity_nodes, entity_links = pv.world_view_links(view)
        nodes += entity_nodes
        links += entity_links
    except (OSError, WorldViewError) as error:
        print(f"no WorldView: {error}", file=sys.stderr)
    unique = {n.key: n for n in nodes}  # a file can be both the dataset and the tracks input
    starts = {"report": ev.report_node(report).key, "world_model": pv.world_model_node(world).key}
    return ProvenanceGraph.of(list(unique.values()), links), starts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="a pipeline run directory")
    parser.add_argument("--geometry", type=Path, default=GEOMETRY)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--camera-dir", type=Path, default=CAMERA)
    parser.add_argument("--start", choices=("report", "world_model"), default="report")
    args = parser.parse_args(argv)
    try:
        graph, starts = build(args.run, args.geometry, args.dataset, args.camera_dir)
    except (OSError, ValueError) as error:
        print(f"cannot trace {args.run}: {error}", file=sys.stderr)
        return 2
    trace = walk(graph, starts[args.start])
    print(trace.model_dump_json(indent=2, exclude={"nodes", "links"}))
    print(f"{len(trace.nodes)} records reached, {len(trace.breaks)} breaks", file=sys.stderr)
    return 0 if trace.complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
