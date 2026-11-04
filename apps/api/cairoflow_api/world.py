"""What the WorldView inspector (/world, /world/{entity_id}), the problem page (/problem) and the
decision trace (/decision/trace) read. This module selects; it computes no value of its own.

- The WorldView snapshot (`flow.WORLD_VIEW_FILE`, Git-ignored, written by
  scripts/build_worldview_snapshot.py), tied by `flow.load_world_view` to the world model the
  baseline replays, the committed geometry and the Muse request's bundle. Without the file the
  pages show one line, `SNAPSHOT_MISSING`, and nothing else: no view is rebuilt behind the reader.
- The world model the snapshot projects (`flow.SNAPSHOT_GLOB`, matched by its SHA-256) and the
  NGSIM file it cites (`DATASET_GLOB`): when present they give the evidence trace (the chain
  walk, `cairoflow_contracts.provenance_chain`), a lane's queue series and a signal head's
  reconstructed intervals. When absent, the page lists them as missing information.
- The committed camera layer record (`CAMERA_LAYER_FILE`): its calibration is not accepted, so the
  camera is shown as a typed break, never as a link into the World Model.
- The records the ProblemStatement is projected from (committed ones, and the run's Git-ignored
  reasoning.json when present), and the demo's run directory (`RUNS_DIR`, Git-ignored) for the
  decision's chain walk and its pairs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from cairoflow_api import flow
from cairoflow_api.demo import Demo
from cairoflow_contracts.evidence import (
    CameraLayer,
    IntersectionGeometry,
    QueueSample,
    SignalInterval,
    TrafficWorldModel,
)
from cairoflow_contracts.pair_identity import PairIdentityView
from cairoflow_contracts.problem_statement import ProblemStatement
from cairoflow_contracts.provenance import ProvenanceGraph
from cairoflow_contracts.provenance_chain import (
    ChainGraph,
    DecisionChain,
    assemble,
    walk,
    walk_from,
)
from cairoflow_contracts.report import DecisionReport
from cairoflow_contracts.worldview import WorldEntity, WorldView
from cairoflow_evaluation.pair_identity import pair_identity_view_from_record
from cairoflow_evaluation.problem_statement import problem_statement_from_records
from cairoflow_evaluation.provenance_chain import experiment_chain_graph
from cairoflow_perception import provenance as pv

SNAPSHOT_MISSING = "WorldView snapshot not present - run scripts/build_worldview_snapshot.py"
DATASET_GLOB = "data/raw/ngsim-lankershim/trajectories/*.csv"
CAMERA_LAYER_FILE = (
    "experiments/records/ngsim-cam2-world-model-v3-20261030T230506Z/camera_layer.json"
)
RUNS_DIR = "experiments/runs"
RECORDS_DIR = "experiments/records"
# provenance_chain.CAMERA_NOT_ACCEPTED ("calibration not accepted (ADR-017/018)"), as one line
CAMERA_BREAK = "not connected to the World Model (calibration not accepted, ADR-017/018)"
# the five kinds a figure can be, named from the chain's record categories
BADGE_OF_CATEGORY = {
    "dataset_derived": "dataset-derived",
    "world_model": "World Model",
    "camera_derived": "camera probe",
    "simulated": "simulated",
    "ai_generated": "AI-generated",
}
BADGES = tuple(BADGE_OF_CATEGORY.values())
_CACHE: dict[tuple, object] = {}
_CACHE_LIMIT = 16


def _cached(key: tuple, make):
    if key not in _CACHE:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.clear()
        _CACHE[key] = make()
    return _CACHE[key]


def _stamp(path: Path) -> tuple[str, int, int]:
    status = path.stat()
    return (str(path.resolve()), status.st_size, status.st_mtime_ns)


def _tree(path: Path) -> tuple[tuple[str, int, int], ...]:
    """A stamp of `path`, or of every file under it: a value cached on it is rebuilt when any
    of them changes, appears or goes."""
    if path.is_file():
        return (_stamp(path),)
    if not path.is_dir():
        return ((str(path), -1, -1),)
    return tuple(_stamp(p) for p in sorted(path.rglob("*")) if p.is_file())


def _cited_baseline(path: Path) -> str | None:
    try:
        cited = json.loads(path.read_text("utf-8")).get("baseline_dir")
    except (OSError, ValueError, AttributeError):
        return None
    return cited if isinstance(cited, str) else None


def entity_badge(entity: WorldEntity) -> str:
    """The kind of an entity, from what its SourceRef names: a camera layer is a camera probe;
    an NGSIM movement count or queue samples (not a rule over them) are dataset-derived; the
    geometry, the signal timeline, the network and the model's own rules are the World Model."""
    source = entity.source
    if source.model == "CameraLayer":
        return "camera probe"
    counted = source.model == "TrafficWorldModel" and (
        "movements[" in source.path or "queues[" in source.path
    )
    if counted and entity.evidence_kind != "assumed":
        return "dataset-derived"
    return "World Model"


# --- the inspector -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Inspector:
    """The snapshot and what came with it, or why there is none (`problem`)."""

    view: WorldView | None
    geometry: IntersectionGeometry | None
    problem: str | None
    origin: str = ""
    world_model_file: str | None = None
    world_model: TrafficWorldModel | None = None
    dataset_file: str | None = None
    chain: ChainGraph | None = None

    @property
    def missing_snapshot(self) -> bool:
        return self.problem == SNAPSHOT_MISSING


def _relative(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path)


def _world_model(repo: Path, digest: str) -> tuple[Path, TrafficWorldModel] | None:
    for path in sorted(repo.glob(flow.SNAPSHOT_GLOB)):

        def read(path: Path = path) -> TrafficWorldModel | None:
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                return None
            return TrafficWorldModel.model_validate_json(data)

        try:
            model = _cached(("world_model", digest, *_stamp(path)), read)
        except (OSError, ValidationError):
            continue
        if model is not None:
            return path, model
    return None


def _dataset(repo: Path, digest: str | None) -> Path | None:
    if digest is None:
        return None
    for path in sorted(repo.glob(DATASET_GLOB)):
        found = _cached(
            ("dataset", *_stamp(path)), lambda p=path: hashlib.sha256(p.read_bytes()).hexdigest()
        )
        if found == digest:
            return path
    return None


def _chain(view: WorldView, model: TrafficWorldModel, dataset: Path | None) -> ChainGraph:
    nodes = [pv.world_model_node(model)]
    entities, links = pv.world_view_links(view)
    nodes += entities
    if dataset is not None:
        nodes += [
            pv.dataset_file_node(dataset),
            pv.track_file_node(dataset, "dataset_derived", "observed"),
        ]
    unique = {node.key: node for node in nodes}
    graph = ProvenanceGraph.of(list(unique.values()), [*pv.world_model_links(model), *links])
    return assemble(graph, world_model=model, view=view)


def load_inspector(demo: Demo, repo: Path) -> Inspector:
    """The snapshot, tied to the demo's records, and the world model and dataset if present."""
    if not (repo / flow.WORLD_VIEW_FILE).is_file():
        return Inspector(None, None, SNAPSHOT_MISSING)
    load = flow.load_world_view(demo, repo)
    if load.view is None:
        return Inspector(None, load.geometry, load.problem or SNAPSHOT_MISSING, load.origin)
    view = load.view
    found = _world_model(repo, view.world_model_sha256)
    if found is None:
        return Inspector(view, load.geometry, None, load.origin)
    path, model = found
    dataset = _dataset(repo, model.ground_source_sha256)
    key = ("chain", view.view_sha256, *_stamp(path), *(_stamp(dataset) if dataset else ()))
    chain = _cached(key, lambda: _chain(view, model, dataset))
    return Inspector(
        view, load.geometry, None, load.origin, _relative(path, repo), model,
        _relative(dataset, repo) if dataset else None, chain,
    )  # fmt: skip


def entity_trace(inspector: Inspector, entity_id: str) -> DecisionChain | None:
    """The chain walk from one entity back to its source, if the world model is here."""
    if inspector.chain is None:
        return None
    return walk_from(inspector.chain, f"WorldEntity:{entity_id}")


def queue_series(model: TrafficWorldModel, lane_id: str) -> tuple[QueueSample, ...]:
    """The lane's queue samples, in time order, as the world model records them."""
    found = [q for q in model.queues if q.lane_id == lane_id]
    return tuple(sorted(found, key=lambda q: q.frame.timestamp_s))


def signal_intervals(model: TrafficWorldModel, arm: str, head: str) -> tuple[SignalInterval, ...]:
    """One head's intervals of the reconstructed signal timeline, in time order."""
    if model.signal_timeline is None:
        return ()
    found = [i for i in model.signal_timeline.intervals if i.arm == arm and i.head == head]
    return tuple(sorted(found, key=lambda i: i.start_s))


@dataclass(frozen=True)
class CameraRecord:
    path: str
    sha256: str | None
    layer: CameraLayer | None
    problem: str | None


def camera_record(repo: Path) -> CameraRecord:
    """The committed camera layer, by the hash of its bytes, or why it cannot be read."""
    try:
        data = (repo / CAMERA_LAYER_FILE).read_bytes()
        layer = CameraLayer.model_validate_json(data)
    except (OSError, ValidationError) as error:
        return CameraRecord(CAMERA_LAYER_FILE, None, None, f"cannot be read: {error}")
    return CameraRecord(CAMERA_LAYER_FILE, hashlib.sha256(data).hexdigest(), layer, None)


# --- the problem -------------------------------------------------------------------------------


def load_problem(repo: Path) -> tuple[ProblemStatement | None, str | None]:
    """The S-approach ProblemStatement from its records (committed, plus the Git-ignored Muse reply
    when present: the page lists each with its SHA-256), or why it cannot be built."""
    try:
        return problem_statement_from_records(repo), None
    except (OSError, KeyError, ValueError) as error:
        return None, f"{type(error).__name__}: {error}"


# --- the decision trace ------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionTrace:
    run: str
    record: str
    report: dict
    run_report_sha256: str | None
    chain: DecisionChain | None
    pairs: PairIdentityView | None
    problems: tuple[str, ...]


def _files(repo: Path, pattern: str) -> Iterable[Path]:
    return sorted(repo.glob(pattern))


def load_decision_trace(demo: Demo, repo: Path) -> DecisionTrace:
    """The decision the demo's experiment record holds, its chain walk over the run directory,
    and the run's pairs. What is not here is said, never filled in."""
    run = f"{RUNS_DIR}/{demo.manifest.run_dir}"
    record = f"{RECORDS_DIR}/{demo.manifest.run_dir}.json"
    report = demo.records["results"]["report"]
    run_dir = repo / run
    if not (run_dir / "report.json").is_file():
        return DecisionTrace(run, record, report, None, None, None, (
            f"The run directory {run} is not present (Git-ignored): the chain walk and the pairs "
            "need it.",
        ))  # fmt: skip
    problems: list[str] = []
    raw = (run_dir / "report.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != report.get("report_json_sha256"):
        problems.append(
            f"{run}/report.json hashes to {digest}, not the {report.get('report_json_sha256')} "
            f"the record {record} states"
        )
    # every cached value below is keyed on every file it is built from, so a running server
    # rebuilds it when one changes, appears or goes (the run's and baseline's files, the world
    # models and NGSIM files the globs match, the geometry and the committed record)
    baseline = _cited_baseline(repo / record)
    runs = (*_tree(run_dir), *(_tree(repo / baseline) if baseline else ()))
    chain = None
    try:
        report_id = DecisionReport.model_validate_json(raw).report_id
        world_models = list(_files(repo, flow.SNAPSHOT_GLOB))
        datasets = list(_files(repo, DATASET_GLOB))
        geometry = repo / flow.GEOMETRY_FILE
        key = (
            "decision", run, *runs, *(_stamp(p) for p in world_models),
            *(_stamp(p) for p in datasets), *_tree(geometry),
        )  # fmt: skip

        def build() -> ChainGraph:
            return experiment_chain_graph(
                run_dir, world_models=world_models, datasets=datasets, geometry=geometry
            )

        chain = walk(_cached(key, build), report_id)
    except (OSError, ValueError) as error:
        problems.append(f"the chain cannot be assembled: {type(error).__name__}: {error}")
    pairs = None
    try:
        pairs = _cached(
            ("pairs", run, *_tree(repo / record), *runs),
            lambda: pair_identity_view_from_record(repo / record, repo),
        )
    except (OSError, KeyError, ValueError) as error:
        problems.append(f"the pairs cannot be rebuilt: {type(error).__name__}: {error}")
    return DecisionTrace(run, record, report, digest, chain, pairs, tuple(problems))
