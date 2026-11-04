"""The decision chain: one walk from a decision back to its source hash (schema 0.1.0, M1-b).

`cairoflow_contracts.provenance` (frozen) links records stage by stage, SourceRecord to
DecisionReport, and reports every gap as a typed break. This module is the link layer the
decision flow reads: ids and hashes only, over the same records. It reuses that layer's nodes and
links (`record_of`, `link_of`) and adds only what the layer has no name or rule for:

1. Record types: the BaselineManifest a simulated experiment ran against, and the NGSIM
   observations a world model holds: the NGSIM track points (observed positions) and the
   movement counts and queue samples CairoFlow derives from them by fixed rules (labelled as the
   world model labels itself). They are dataset-derived, not SAM observations
   (`ngsim_observations`); each is named by what it is and the snapshot it belongs to.
2. Input links: a baseline is compiled from a world model snapshot (BaselineManifest.
   world_model_sha256), not from a problem or an intervention. `INPUT_LINKS` is the only place a
   link may go back more than one stage; any other skip is `wrong_stage`.
3. Refused links: a link a record states that a decision does not accept. The camera layer's
   tracks are tied to the world model by `CameraLayer.tracks_sha256`, but its calibration is not
   accepted (ADR-017/018), so the link is refused and `walk` reports `not_accepted`, never a path.
4. `walk(graph, decision_id)`: the best path from the DecisionReport to a source record (through
   the most stages, then the shortest, then by key), every typed break met on the way (the named
   link and the reason), and whether the path is the full seven-stage chain.

Given the view and its world model, `assemble` re-links each WorldEntity to what its `SourceRef`
names, in place of the frozen layer's link to the snapshot as a whole: a movement or queue entity
to its NGSIM count or queue samples; a camera entity to the camera tracks file (refused unless
the calibration is accepted); an entity drawn from the geometry, the signal timeline or the
compiled network to nothing, because the chain holds no record of those sources, so `walk`
reports a `missing_link` rather than a path through the trajectories. Other entities of the
snapshot keep their link to it. A graph with camera entities is refused without the view and
world model, so the camera refusal cannot be skipped. Nothing is inferred: a link no record
states is absent, and `walk` names it as a `missing_link`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from cairoflow_contracts.evidence import (
    EvidenceKind,
    EvidenceModel,
    Sha256,
    TrafficWorldModel,
    _check_sha256,
)
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.provenance import (
    RECORD_TYPES,
    STAGES,
    Category,
    HashBasis,
    NodeRef,
    ProvenanceGraph,
    ProvenanceLink,
    Stage,
)
from cairoflow_contracts.worldview import WorldView

CHAIN_VERSION = "0.1.0"
CAMERA_NOT_ACCEPTED = "calibration not accepted (ADR-017/018)"

ChainRecordType = Literal[
    "DatasetFile",
    "SourceClip",
    "SamResponse",
    "TrackFile",
    "NgsimTrackPoints",
    "NgsimMovementCount",
    "NgsimQueueSample",
    "TrafficWorldModel",
    "WorldEntity",
    "ProblemStatement",
    "InterventionProposal",
    "BaselineManifest",
    "ExperimentManifest",
    "DecisionReport",
]
# record type -> (its stage, the categories it may have): the frozen table and four new types
CHAIN_RECORD_TYPES: dict[str, tuple[Stage, frozenset[Category]]] = {
    **RECORD_TYPES,
    "NgsimTrackPoints": ("observation", frozenset({"dataset_derived"})),
    "NgsimMovementCount": ("observation", frozenset({"dataset_derived"})),
    "NgsimQueueSample": ("observation", frozenset({"dataset_derived"})),
    "BaselineManifest": ("experiment", frozenset({"simulated"})),
}
# record type -> the record types it is derived from (one is enough); none named is a break
EXPECTED_PARENTS: dict[str, frozenset[str]] = {
    "SamResponse": frozenset({"SourceClip"}),
    "TrackFile": frozenset({"DatasetFile", "SourceClip", "SamResponse"}),
    "NgsimTrackPoints": frozenset({"DatasetFile"}),
    "NgsimMovementCount": frozenset({"NgsimTrackPoints"}),
    "NgsimQueueSample": frozenset({"NgsimTrackPoints"}),
    "TrafficWorldModel": frozenset({"TrackFile", "NgsimTrackPoints"}),
    "WorldEntity": frozenset(
        {"TrafficWorldModel", "NgsimMovementCount", "NgsimQueueSample", "TrackFile"}
    ),
    "ProblemStatement": frozenset({"WorldEntity"}),
    "InterventionProposal": frozenset({"ProblemStatement"}),
    "ExperimentManifest": frozenset({"InterventionProposal"}),
    "BaselineManifest": frozenset({"TrafficWorldModel"}),
    "DecisionReport": frozenset({"ExperimentManifest"}),
}
# (child type, parent type) pairs that may be input links: an experiment's baseline, and the
# world model snapshot the baseline was compiled from
INPUT_LINKS: frozenset[tuple[str, str]] = frozenset(
    {("ExperimentManifest", "BaselineManifest"), ("BaselineManifest", "TrafficWorldModel")}
)
LinkKind = Literal["derived", "input"]
ChainBreakCode = Literal[
    "missing_link",  # no link names a record of the type this one is derived from
    "unknown_record",  # a link (or the walk's start) names a record the graph does not hold
    "wrong_entity_type",  # a link names a record by id under another record type
    "hash_mismatch",  # a stated hash is not the record's content hash
    "wrong_stage",  # a link skips a stage or runs forward, outside INPUT_LINKS
    "cycle",  # a record is its own ancestor
    "not_accepted",  # a record states the link, a decision refuses it
]
_MOVEMENT = re.compile(r"movements\[([A-Za-z]+)\]")
_QUEUE = re.compile(r"queues\[([^\]]+)\]")


def _previous(stage: Stage) -> str:
    index = STAGES.index(stage)
    return STAGES[index - 1] if index else "nothing"


class ChainRecord(EvidenceModel):
    """One existing record, by its existing id and content hash (as provenance.NodeRef)."""

    stage: Stage
    record_type: ChainRecordType
    record_id: str = Field(pattern=r"^\S+$", description="the id the record already has")
    content_sha256: Sha256
    hash_basis: HashBasis
    category: Category
    evidence_kind: EvidenceKind = Field(description="as the record states it, not re-judged")
    model_output: bool = Field(False, description="the record is a model's output (SAM, Muse)")

    @field_validator("content_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)

    @model_validator(mode="after")
    def _consistent(self) -> ChainRecord:
        stage, categories = CHAIN_RECORD_TYPES[self.record_type]
        if self.stage != stage:
            raise ValueError(f"a {self.record_type} is a {stage} record, not {self.stage}")
        if self.category not in categories:
            raise ValueError(f"a {self.record_type} cannot be {self.category}")
        if self.model_output != (self.evidence_kind == "model_generated"):
            raise ValueError("model output, and only model output, is model_generated evidence")
        if self.category == "ai_generated" and not self.model_output:
            raise ValueError("an ai_generated record is model output")
        if (self.category == "simulated") != (self.evidence_kind == "simulated"):
            raise ValueError("simulated records, and only they, are simulated evidence")
        return self

    @property
    def key(self) -> str:
        return f"{self.record_type}:{self.record_id}"


class ChainEnd(EvidenceModel):
    """A record a link names, and the hash the stating record gives for it (None: none given)."""

    record_type: ChainRecordType
    record_id: str = Field(pattern=r"^\S+$")
    stated_sha256: Sha256 | None = None

    @field_validator("stated_sha256")
    @classmethod
    def _hash(cls, value: str | None) -> str | None:
        return None if value is None else _check_sha256(value)

    @classmethod
    def of(cls, record: ChainRecord) -> ChainEnd:
        return cls(
            record_type=record.record_type, record_id=record.record_id,
            stated_sha256=record.content_sha256,
        )  # fmt: skip

    @property
    def key(self) -> str:
        return f"{self.record_type}:{self.record_id}"


class ChainLink(EvidenceModel):
    """`child` was derived from (kind "derived") or compiled from (kind "input") `parent`, as
    `basis`, a field of an existing record, states."""

    child: ChainEnd
    parent: ChainEnd
    basis: str = Field(min_length=1)
    kind: LinkKind = "derived"

    @property
    def key(self) -> tuple[str, str]:
        return (self.child.key, self.parent.key)


class RefusedLink(EvidenceModel):
    """A link a record states that a decision does not accept, with the decision's reason."""

    child: ChainEnd
    parent: ChainEnd
    basis: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @property
    def key(self) -> tuple[str, str]:
        return (self.child.key, self.parent.key)


class ChainGraph(EvidenceModel):
    chain_version: Literal["0.1.0"] = CHAIN_VERSION
    records: tuple[ChainRecord, ...]
    links: tuple[ChainLink, ...]
    refused: tuple[RefusedLink, ...] = ()
    graph_sha256: Sha256

    def digest(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"graph_sha256"}))

    @classmethod
    def of(
        cls,
        records: Iterable[ChainRecord],
        links: Iterable[ChainLink],
        refused: Iterable[RefusedLink] = (),
    ) -> ChainGraph:
        """The graph in canonical order; a duplicate record or link is an error, not a merge."""
        fields: dict[str, Any] = {
            "records": tuple(sorted(records, key=lambda r: r.key)),
            "links": tuple(sorted(links, key=lambda link: link.key)),
            "refused": tuple(sorted(refused, key=lambda r: r.key)),
        }
        draft = cls.model_construct(**fields, graph_sha256="0" * 64)
        return cls(**fields, graph_sha256=draft.digest())

    @field_validator("graph_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)

    @model_validator(mode="after")
    def _canonical(self) -> ChainGraph:
        for name, keys in (
            ("record", [r.key for r in self.records]),
            ("link", [link.key for link in self.links]),
            ("refused link", [r.key for r in self.refused]),
        ):
            if len(set(keys)) != len(keys):
                raise ValueError(f"a {name} may appear once")
            if keys != sorted(keys):
                raise ValueError(f"{name}s must be in canonical (sorted) order; use ChainGraph.of")
        both = {link.key for link in self.links} & {r.key for r in self.refused}
        if both:
            raise ValueError(f"a refused link cannot also be a link: {sorted(both)[0]}")
        if self.graph_sha256 != self.digest():
            raise ValueError("graph_sha256 does not hash the graph's content")
        return self


class TypedBreak(EvidenceModel):
    code: ChainBreakCode
    link: str = Field(description="the relationship, e.g. 'intervention <- problem_statement'")
    at: str = Field(description="the record whose derivation could not be followed")
    reason: str = Field(min_length=1)


class DecisionChain(EvidenceModel):
    """What `walk` found from one record back: the best path to a source record (start first),
    the links on it, and every typed break met anywhere on the way."""

    start: str
    graph_sha256: Sha256
    reaches_source: bool = Field(description="a break-free path reaches a source record")
    full_chain: bool = Field(description="that path passes through all seven stages")
    path: tuple[ChainRecord, ...]
    path_links: tuple[ChainLink, ...]
    source_sha256: Sha256 | None = Field(description="the content hash the path ends at")
    unhashed_links: tuple[str, ...] = Field(description="followed links matched by id only")
    breaks: tuple[TypedBreak, ...]

    @model_validator(mode="after")
    def _consistent(self) -> DecisionChain:
        if self.reaches_source != bool(self.path):
            raise ValueError("a chain reaches its source exactly when it has a path")
        if self.path and len(self.path_links) != len(self.path) - 1:
            raise ValueError("a path of n records has n - 1 links")
        end = self.path[-1].content_sha256 if self.path else None
        if self.source_sha256 != end:
            raise ValueError("source_sha256 is the hash of the path's last record")
        if self.full_chain and {r.stage for r in self.path} != set(STAGES):
            raise ValueError("a full chain passes through every stage")
        return self


# --- the walk ----------------------------------------------------------------------------------

_Found = tuple[tuple[str, ...], tuple[ChainLink, ...]]


def _link_problem(
    link: ChainLink, record: ChainRecord, records: dict[str, ChainRecord], stack: tuple[str, ...]
) -> TypedBreak | None:
    parent = records.get(link.parent.key)
    parent_stage = parent.stage if parent else CHAIN_RECORD_TYPES[link.parent.record_type][0]
    rel, at = f"{record.stage} <- {parent_stage}", record.key
    if parent is None:
        others = sorted(k for k, r in records.items() if r.record_id == link.parent.record_id)
        if others:
            return TypedBreak(
                code="wrong_entity_type", link=rel, at=at,
                reason=f"the link names {link.parent.key}, but the record with that id is "
                f"{others[0]}",
            )  # fmt: skip
        return TypedBreak(
            code="unknown_record", link=rel, at=at,
            reason=f"the link names {link.parent.key}, which the graph does not hold",
        )  # fmt: skip
    for end, held in ((link.child, record), (link.parent, parent)):
        if end.stated_sha256 is not None and end.stated_sha256 != held.content_sha256:
            return TypedBreak(
                code="hash_mismatch", link=rel, at=at,
                reason=f"{end.key} is stated as {end.stated_sha256[:12]} ({link.basis}, the link "
                f"to {link.parent.key}) but hashes to {held.content_sha256[:12]} "
                f"({held.hash_basis})",
            )  # fmt: skip
    step = STAGES.index(record.stage) - STAGES.index(parent.stage)
    pair = (record.record_type, parent.record_type)
    if link.kind == "input" and (pair not in INPUT_LINKS or step < 0):
        return TypedBreak(
            code="wrong_stage", link=rel, at=at,
            reason="an input link ties an experiment to its baseline or a baseline to its world "
            f"model; {record.record_type} <- {link.parent.key} is not one",
        )  # fmt: skip
    if link.kind == "derived" and step not in (0, 1):
        return TypedBreak(
            code="wrong_stage", link=rel, at=at,
            reason=f"a {record.stage} record is derived from {_previous(record.stage)} or its own "
            f"stage; {link.parent.key} is {parent.stage}",
        )  # fmt: skip
    if link.parent.key in stack or link.parent.key == record.key:
        return TypedBreak(
            code="cycle", link=rel, at=at, reason=f"{link.parent.key} is already on this path"
        )
    return None


def walk_from(graph: ChainGraph, start: str) -> DecisionChain:
    """Follow `start` (a record key) back to source records; never fills a gap."""
    records = {r.key: r for r in graph.records}
    parents: dict[str, list[ChainLink]] = {}
    for link in graph.links:
        parents.setdefault(link.child.key, []).append(link)
    refusals: dict[str, list[RefusedLink]] = {}
    for refused in graph.refused:
        refusals.setdefault(refused.child.key, []).append(refused)
    breaks: dict[tuple[str, ...], TypedBreak] = {}
    unhashed: set[str] = set()
    best: dict[str, _Found | None] = {}

    def add(found: TypedBreak) -> None:
        breaks.setdefault((found.at, found.code, found.link, found.reason), found)

    def score(found: _Found) -> tuple[int, int, tuple[str, ...]]:
        keys = found[0]
        return (-len({records[k].stage for k in keys}), len(keys), keys)

    def visit(key: str, stack: tuple[str, ...]) -> _Found | None:
        if key in best:
            return best[key]
        record = records[key]
        own, refused = parents.get(key, []), refusals.get(key, [])
        for item in refused:
            stage = CHAIN_RECORD_TYPES[item.parent.record_type][0]
            add(TypedBreak(
                code="not_accepted", link=f"{record.stage} <- {stage}", at=key,
                reason=f"{item.parent.key} ({item.basis}): {item.reason}",
            ))  # fmt: skip
        expected = EXPECTED_PARENTS.get(record.record_type, frozenset())
        named = {link.parent.record_type for link in own} | {r.parent.record_type for r in refused}
        if expected and not named & expected:
            add(TypedBreak(
                code="missing_link", link=f"{record.stage} <- {_previous(record.stage)}", at=key,
                reason=f"no link names the {' or '.join(sorted(expected))} this "
                f"{record.record_type} was derived from",
            ))  # fmt: skip
        options: list[_Found] = [((key,), ())] if record.stage == "source_record" else []
        for link in sorted(own, key=lambda item: item.key):
            problem = _link_problem(link, record, records, stack)
            if problem is not None:
                add(problem)
                continue
            if link.parent.stated_sha256 is None:
                unhashed.add(f"{link.child.key} <- {link.parent.key} ({link.basis})")
            found = visit(link.parent.key, (*stack, key))
            if found is not None:
                options.append(((key, *found[0]), (link, *found[1])))
        best[key] = min(options, key=score) if options else None
        return best[key]

    if start not in records:
        add(TypedBreak(code="unknown_record", link="the record itself", at=start,
                       reason=f"{start} is not in the graph"))  # fmt: skip
        found = None
    else:
        found = visit(start, ())
    path = tuple(records[k] for k in found[0]) if found else ()
    return DecisionChain(
        start=start,
        graph_sha256=graph.graph_sha256,
        reaches_source=bool(path),
        full_chain=bool(path) and {r.stage for r in path} == set(STAGES),
        path=path,
        path_links=found[1] if found else (),
        source_sha256=path[-1].content_sha256 if path else None,
        unhashed_links=tuple(sorted(unhashed)),
        breaks=tuple(breaks[k] for k in sorted(breaks)),
    )


def walk(graph: ChainGraph, decision_id: str) -> DecisionChain:
    """The chain of the DecisionReport whose `report_id` is `decision_id`."""
    return walk_from(graph, f"DecisionReport:{decision_id}")


# --- adapters onto the existing records --------------------------------------------------------


def record_of(node: NodeRef) -> ChainRecord:
    """A frozen-layer node as a chain record (same id, hash, stage and labels)."""
    return ChainRecord(**node.model_dump(exclude={"schema_version"}))


def link_of(link: ProvenanceLink) -> ChainLink:
    """A frozen-layer link as a derived chain link."""
    return ChainLink(
        child=ChainEnd(**link.child.model_dump(exclude={"schema_version"})),
        parent=ChainEnd(**link.parent.model_dump(exclude={"schema_version"})),
        basis=link.basis,
    )


def _world_model_sha256(world_model: TrafficWorldModel) -> str:
    """As every record cites a world model: sha256 of model_dump_json()."""
    return hashlib.sha256(world_model.model_dump_json().encode("utf-8")).hexdigest()


def _observation(
    record_type: ChainRecordType, record_id: str, content: object, kind: EvidenceKind
) -> ChainRecord:
    return ChainRecord(
        stage="observation", record_type=record_type, record_id=record_id,
        content_sha256=canonical_sha256(content), hash_basis="canonical_json",
        category="dataset_derived", evidence_kind=kind,
    )  # fmt: skip


def ngsim_observations(
    world_model: TrafficWorldModel, world_model_sha256: str
) -> tuple[list[ChainRecord], list[ChainLink]]:
    """The model's NGSIM track points (observed positions) and the movement counts and queue
    samples derived from them (labelled with the model's own evidence kind), each named by the
    movement or lane and the snapshot (`@` its hash's first 12 characters) and hashed by its
    content, derived from the dataset file the model states and linked into the model. A
    calibration-anchored model (no `ground_source_sha256`) has none."""
    dataset = world_model.ground_source_sha256
    if dataset is None:
        return [], []
    at, derived = f"@{world_model_sha256[:12]}", world_model.provenance.kind
    model = ChainEnd(
        record_type="TrafficWorldModel", record_id=world_model_sha256,
        stated_sha256=world_model_sha256,
    )  # fmt: skip
    points = _observation(
        "NgsimTrackPoints", f"ngsim-tracks.{world_model.clip_id}{at}",
        [t.model_dump(mode="json") for t in world_model.ground_tracks], "observed",
    )  # fmt: skip
    source = ChainEnd(record_type="DatasetFile", record_id=dataset, stated_sha256=dataset)
    records = [points]
    links = [
        ChainLink(child=ChainEnd.of(points), parent=source,
                  basis="TrafficWorldModel.ground_source_sha256"),
        ChainLink(child=model, parent=ChainEnd.of(points), basis="TrafficWorldModel.ground_tracks"),
    ]  # fmt: skip
    for movement in sorted(world_model.movements, key=lambda m: (m.approach, m.exit_arm)):
        code = f"{movement.approach}{movement.exit_arm}"
        count = _observation(
            "NgsimMovementCount", f"mv.{code}{at}", movement.model_dump(mode="json"), derived
        )
        records.append(count)
        links += [
            ChainLink(child=ChainEnd.of(count), parent=ChainEnd.of(points),
                      basis="MovementCount.tracks (ground track ids)"),
            ChainLink(child=model, parent=ChainEnd.of(count),
                      basis=f"TrafficWorldModel.movements[{code}]"),
        ]  # fmt: skip
    lanes: dict[str, list[Any]] = {}
    for sample in world_model.queues:
        lanes.setdefault(sample.lane_id, []).append(sample.model_dump(mode="json"))
    for lane in sorted(lanes):
        queue = _observation("NgsimQueueSample", f"queue.{lane}{at}", lanes[lane], derived)
        records.append(queue)
        links += [
            ChainLink(child=ChainEnd.of(queue), parent=ChainEnd.of(points),
                      basis="QueueSample.track_ids (ground track ids)"),
            ChainLink(child=model, parent=ChainEnd.of(queue),
                      basis=f"TrafficWorldModel.queues[{lane}]"),
        ]  # fmt: skip
    return records, links


def _entity_end(entity: Any) -> ChainEnd:
    return ChainEnd(
        record_type="WorldEntity", record_id=entity.entity_id,
        stated_sha256=canonical_sha256(entity.model_dump(mode="json")),
    )  # fmt: skip


def camera_links(
    world_model: TrafficWorldModel, view: WorldView | None = None
) -> tuple[list[ChainLink], list[RefusedLink]]:
    """The camera tracks file as the parent of the world model and of each camera entity: links
    when the calibration is accepted, refused links citing ADR-017/018 when it is not."""
    camera = world_model.camera
    if camera is None:
        return [], []
    tracks = ChainEnd(
        record_type="TrackFile", record_id=camera.tracks_sha256, stated_sha256=camera.tracks_sha256
    )
    digest = _world_model_sha256(world_model)
    children = [
        (ChainEnd(record_type="TrafficWorldModel", record_id=digest, stated_sha256=digest),
         "TrafficWorldModel.camera.tracks_sha256"),
    ]  # fmt: skip
    if view is not None:
        children += [
            (_entity_end(e), "WorldEntity.source (CameraLayer), CameraLayer.tracks_sha256")
            for e in view.entities
            if e.source.model == "CameraLayer"
        ]
    if camera.calibration_accepted:
        return [ChainLink(child=c, parent=tracks, basis=b) for c, b in children], []
    reason = f"{CAMERA_NOT_ACCEPTED}: the camera layer is not connected to the World Model"
    return [], [RefusedLink(child=c, parent=tracks, basis=b, reason=reason) for c, b in children]


def baseline_input(
    experiment: ChainRecord, baseline_id: str, stated_sha256: str, world_model_sha256: str,
    raw: bytes,
) -> tuple[list[ChainRecord], list[ChainLink]]:  # fmt: skip
    """The baseline manifest an experiment ran against (by the hash of its bytes, `raw`) and the
    two input links: experiment <- baseline (ExperimentManifest.baseline_manifest_sha256, as
    `stated_sha256`) and baseline <- world model (BaselineManifest.world_model_sha256)."""
    record = ChainRecord(
        stage="experiment", record_type="BaselineManifest", record_id=baseline_id,
        content_sha256=hashlib.sha256(raw).hexdigest(), hash_basis="file_bytes",
        category="simulated", evidence_kind="simulated",
    )  # fmt: skip
    links = [
        ChainLink(
            child=ChainEnd.of(experiment),
            parent=ChainEnd(record_type="BaselineManifest", record_id=baseline_id,
                            stated_sha256=stated_sha256),
            basis="ExperimentManifest.baseline_manifest_sha256", kind="input",
        ),
        ChainLink(
            child=ChainEnd.of(record),
            parent=ChainEnd(record_type="TrafficWorldModel", record_id=world_model_sha256,
                            stated_sha256=world_model_sha256),
            basis="BaselineManifest.world_model_sha256", kind="input",
        ),
    ]  # fmt: skip
    return [record], links


def assemble(
    graph: ProvenanceGraph,
    *,
    world_model: TrafficWorldModel | None = None,
    view: WorldView | None = None,
    records: Sequence[ChainRecord] = (),
    links: Sequence[ChainLink] = (),
    refused: Sequence[RefusedLink] = (),
) -> ChainGraph:
    """The chain graph of a frozen-layer graph, plus the given records and links, the world
    model's NGSIM observations and its camera links or refusals. With `view` (which needs its
    `world_model`), entities are re-linked to what their SourceRef names (module docstring). A
    refused link replaces the frozen layer's link of the same key. Two different records under
    one key, a view of another snapshot, or camera entities without their view and world model
    are errors, not merges."""
    held: dict[str, ChainRecord] = {}

    def hold(record: ChainRecord) -> None:
        if held.setdefault(record.key, record) != record:
            raise ValueError(f"two different records are called {record.key}")

    for record in (*map(record_of, graph.nodes), *records):
        hold(record)
    stated: dict[tuple[str, str], ChainLink] = {}
    for link in (*map(link_of, graph.links), *links):
        stated.setdefault(link.key, link)
    refusing = {r.key: r for r in refused}
    if view is not None and world_model is None:
        raise ValueError("a view is assembled with its world model (its camera calibration)")
    camera_ids = ("camera_layer:", "camera_track:")
    cameras = [r.key for r in held.values() if r.record_id.startswith(camera_ids)]
    if cameras and (world_model is None or view is None):
        raise ValueError(f"{cameras[0]} needs the view and world model to be assembled")
    if world_model is not None:
        digest = _world_model_sha256(world_model)
        if view is not None and view.world_model_sha256 != digest:
            raise ValueError("the view is of another world model snapshot")
        found, found_links = ngsim_observations(world_model, digest)
        for record in found:
            hold(record)
        for link in found_links:
            stated.setdefault(link.key, link)
        observed = {r.record_id for r in found}
        snapshot = f"TrafficWorldModel:{digest}"
        for entity in view.entities if view is not None else ():
            end = _entity_end(entity)
            source = entity.source
            names = [f"mv.{m}@{digest[:12]}" for m in _MOVEMENT.findall(source.path)]
            names += [f"queue.{q}@{digest[:12]}" for q in _QUEUE.findall(source.path)]
            names = [n for n in names if n in observed]
            if source.model != "TrafficWorldModel" or names:  # its source is not the snapshot
                stated.pop((end.key, snapshot), None)
            for name in names if source.model == "TrafficWorldModel" else ():
                kind = "NgsimMovementCount" if name.startswith("mv.") else "NgsimQueueSample"
                basis = f"WorldEntity.source ({source.path})"
                link = ChainLink(child=end, parent=ChainEnd.of(held[f"{kind}:{name}"]), basis=basis)
                stated.setdefault(link.key, link)
        camera, camera_refused = camera_links(world_model, view)
        for link in camera:
            stated.setdefault(link.key, link)
        refusing |= {r.key: r for r in camera_refused}
    for key in refusing:
        stated.pop(key, None)
    return ChainGraph.of(held.values(), stated.values(), refusing.values())
