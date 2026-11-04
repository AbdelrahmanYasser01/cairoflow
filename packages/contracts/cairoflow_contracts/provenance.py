"""Provenance links from a source record to a decision report (schema 0.1.0, ADR-033 addendum).

The records that make up a decision already exist and are already hashed: a dataset file or a
video clip, the tracks taken from it, the TrafficWorldModel, the entities of its WorldView, the
model's InterventionProposal, the ExperimentManifest that simulated it and the DecisionReport.
This module adds no copy of any of them. A `NodeRef` names one record by its existing id and its
content hash; a `ProvenanceLink` says that one record was derived from another, which field of
which record says so (`basis`) and, when that field holds one, the hash it states for the parent.
A `ProvenanceGraph` is a set of both; `walk` follows it back from a record and returns the whole
path, or every place it breaks, as a typed `ChainBreak`. Nothing is inferred: a relationship no
record states is absent, and `walk` reports it as `missing_link`.

Stages, in order: source_record, observation, world_entity, problem_statement, intervention,
experiment, decision_report. A link goes back one stage, or stays in its stage (a WorldEntity is
derived from the TrafficWorldModel it projects; tracks from a SAM response); it never skips one.

Categories (what kind of thing a record is, kept as recorded and never relabelled):
- camera_derived: from video pixels, including segmentation output (SAM), which is
  `model_output` with evidence kind model_generated: model output, not ground truth;
- dataset_derived: from a published dataset (NGSIM trajectories);
- world_model: the World Model representation, its entities, and a problem named on them by a
  deterministic rule;
- simulated: produced by SUMO runs and computed from them;
- ai_generated: written by a reasoning model (proposals): a proposal, never a measurement.
`RECORD_TYPES` fixes each record type's stage and categories, and the first three occur only
before the intervention stage. Since a link never runs forward, a measured record cannot be
derived from a simulated or ai_generated one: a proposal or a simulator output cannot pass for an
observation.

ProblemStatement. No traffic problem record existed before this module: `Hypothesis` is model
text, and the `problems` fields elsewhere list citation, grounding or validation faults. A
`ProblemStatement` is the smallest record that can stand in the chain: a code-written sentence, the
rule that produced it and the WorldView entities it is about. It holds no number. No producer writes one yet, so on real records the chain breaks at it.

Determinism: no clock, no random id; nodes and links are kept sorted and unique, and
`graph_sha256` hashes the content.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from cairoflow_contracts.evidence import EvidenceKind, EvidenceModel, Sha256, _check_sha256
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.worldview import ENTITY_ID

PROVENANCE_VERSION = "0.1.0"
PROBLEM_VERSION = "0.1.0"

Stage = Literal[
    "source_record",
    "observation",
    "world_entity",
    "problem_statement",
    "intervention",
    "experiment",
    "decision_report",
]
STAGES: tuple[Stage, ...] = (
    "source_record",
    "observation",
    "world_entity",
    "problem_statement",
    "intervention",
    "experiment",
    "decision_report",
)
Category = Literal["camera_derived", "dataset_derived", "world_model", "simulated", "ai_generated"]
CATEGORIES: tuple[Category, ...] = (
    "camera_derived",
    "dataset_derived",
    "world_model",
    "simulated",
    "ai_generated",
)
MEASURED: frozenset[Category] = frozenset({"camera_derived", "dataset_derived", "world_model"})
RecordType = Literal[
    "DatasetFile",
    "SourceClip",
    "SamResponse",
    "TrackFile",
    "TrafficWorldModel",
    "WorldEntity",
    "ProblemStatement",
    "InterventionProposal",
    "ExperimentManifest",
    "DecisionReport",
]
HashBasis = Literal[
    "file_bytes",  # sha256 of the file as written
    "model_dump_json",  # sha256 of model_dump_json(), as world_model_sha256 everywhere
    "canonical_json",  # integrity.canonical_sha256 of model_dump(mode="json")
    "proposal_sha256",  # simulation.validation.proposal_sha256, as ValidationOutcome holds it
    "request_key",  # a cached model request's key: names the request, not the reply's bytes
]

# record type -> (its stage, the categories it may have)
RECORD_TYPES: dict[str, tuple[Stage, frozenset[Category]]] = {
    "DatasetFile": ("source_record", frozenset({"dataset_derived"})),
    "SourceClip": ("source_record", frozenset({"camera_derived"})),
    "SamResponse": ("observation", frozenset({"camera_derived"})),
    "TrackFile": ("observation", frozenset({"camera_derived", "dataset_derived"})),
    "TrafficWorldModel": ("world_entity", frozenset({"world_model"})),
    "WorldEntity": ("world_entity", frozenset({"world_model"})),
    "ProblemStatement": ("problem_statement", frozenset({"world_model"})),
    "InterventionProposal": ("intervention", frozenset({"ai_generated"})),
    "ExperimentManifest": ("experiment", frozenset({"simulated"})),
    "DecisionReport": ("decision_report", frozenset({"simulated"})),
}

BreakCode = Literal[
    "missing_link",  # no link names what a record was derived from
    "unknown_node",  # a link names a record the graph does not hold
    "wrong_entity_type",  # a link names a record by id under another record type
    "hash_mismatch",  # a stated hash is not the record's content hash
    "wrong_stage",  # a link skips a stage or runs forward
    "cycle",  # a record is its own ancestor
]


class NodeRef(EvidenceModel):
    """One existing record, by its existing id and content hash; never a copy of it."""

    stage: Stage
    record_type: RecordType
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
    def _consistent(self) -> NodeRef:
        stage, categories = RECORD_TYPES[self.record_type]
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


class LinkEnd(EvidenceModel):
    """A record a link names, and the hash the stating record gives for it (None: none given)."""

    record_type: RecordType
    record_id: str = Field(pattern=r"^\S+$")
    stated_sha256: Sha256 | None = None

    @field_validator("stated_sha256")
    @classmethod
    def _hash(cls, value: str | None) -> str | None:
        return None if value is None else _check_sha256(value)

    @property
    def key(self) -> str:
        return f"{self.record_type}:{self.record_id}"


class ProvenanceLink(EvidenceModel):
    """`child` was derived from `parent`, as `basis` (a field of an existing record) states."""

    child: LinkEnd
    parent: LinkEnd
    basis: str = Field(min_length=1, description="e.g. 'TrafficWorldModel.tracks_sha256'")

    @property
    def key(self) -> tuple[str, str]:
        return (self.child.key, self.parent.key)


class ProvenanceGraph(EvidenceModel):
    provenance_version: Literal["0.1.0"] = PROVENANCE_VERSION
    nodes: tuple[NodeRef, ...]
    links: tuple[ProvenanceLink, ...]
    graph_sha256: Sha256

    @staticmethod
    def digest_of(fields: dict[str, Any]) -> str:
        return canonical_sha256({k: v for k, v in fields.items() if k != "graph_sha256"})

    @classmethod
    def of(cls, nodes: Sequence[NodeRef], links: Sequence[ProvenanceLink]) -> ProvenanceGraph:
        """The graph in canonical order; duplicate records or links are an error, not a merge."""
        ordered_nodes = tuple(sorted(nodes, key=lambda n: n.key))
        ordered_links = tuple(sorted(links, key=lambda link: link.key))
        fields = {
            "schema_version": "0.1.0",
            "provenance_version": PROVENANCE_VERSION,
            "nodes": [n.model_dump(mode="json") for n in ordered_nodes],
            "links": [link.model_dump(mode="json") for link in ordered_links],
        }
        return cls(nodes=ordered_nodes, links=ordered_links, graph_sha256=cls.digest_of(fields))

    @field_validator("graph_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)

    @model_validator(mode="after")
    def _canonical(self) -> ProvenanceGraph:
        keys = [n.key for n in self.nodes]
        if len(set(keys)) != len(keys):
            raise ValueError("a record may appear once: duplicate node " + _first_repeat(keys))
        if keys != sorted(keys):
            raise ValueError("nodes must be in canonical (sorted) order; use ProvenanceGraph.of")
        links = [link.key for link in self.links]
        if len(set(links)) != len(links):
            raise ValueError("a link may be stated once")
        if links != sorted(links):
            raise ValueError("links must be in canonical (sorted) order; use ProvenanceGraph.of")
        if self.graph_sha256 != self.digest_of(self.model_dump(mode="json")):
            raise ValueError("graph_sha256 does not hash the graph's content")
        return self

    def node(self, key: str) -> NodeRef | None:
        for found in self.nodes:
            if found.key == key:
                return found
        return None


def _first_repeat(values: list[str]) -> str:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return ""


class ChainBreak(EvidenceModel):
    code: BreakCode
    at: str = Field(description="the record whose derivation could not be followed")
    missing: str = Field(description="the relationship, e.g. 'problem_statement <- world_entity'")
    reason: str = Field(min_length=1)


class ProvenanceTrace(EvidenceModel):
    """What `walk` found from one record back: every ancestor reached, every link followed and
    every break. `complete` only when there is no break, so every path ends at a source record."""

    terminal: str
    graph_sha256: Sha256
    complete: bool
    nodes: tuple[NodeRef, ...] = Field(description="by stage, then key: the path, source first")
    links: tuple[ProvenanceLink, ...]
    breaks: tuple[ChainBreak, ...]
    unhashed_links: tuple[str, ...] = Field(
        description="links whose stating record gives no hash: matched by id only"
    )
    categories: tuple[Category, ...] = Field(description="the categories on the path, in order")

    @model_validator(mode="after")
    def _complete(self) -> ProvenanceTrace:
        if self.complete == bool(self.breaks):
            raise ValueError("a trace is complete exactly when it has no break")
        return self


def _previous(stage: Stage) -> str:
    index = STAGES.index(stage)
    return STAGES[index - 1] if index else "nothing"


def _end_problem(end: LinkEnd, graph: ProvenanceGraph, at: str, rel: str) -> ChainBreak | None:
    node = graph.node(end.key)
    if node is None:
        others = [n.key for n in graph.nodes if n.record_id == end.record_id]
        if others:
            return ChainBreak(
                code="wrong_entity_type", at=at, missing=rel,
                reason=f"the link names {end.key}, but the record with that id is {others[0]}",
            )  # fmt: skip
        return ChainBreak(
            code="unknown_node", at=at, missing=rel,
            reason=f"the link names {end.key}, which the graph does not hold",
        )  # fmt: skip
    if end.stated_sha256 is not None and end.stated_sha256 != node.content_sha256:
        return ChainBreak(
            code="hash_mismatch", at=at, missing=rel,
            reason=f"{end.key} is stated as {end.stated_sha256[:12]} but hashes to "
            f"{node.content_sha256[:12]} ({node.hash_basis})",
        )  # fmt: skip
    return None


def _link_problem(
    link: ProvenanceLink, node: NodeRef, graph: ProvenanceGraph, stack: tuple[str, ...]
) -> ChainBreak | None:
    parent = graph.node(link.parent.key)
    rel = f"{node.stage} <- {parent.stage if parent else '?'}"
    found = _end_problem(link.child, graph, node.key, rel)
    found = found or _end_problem(link.parent, graph, node.key, rel)
    if found is not None or parent is None:
        return found
    step = STAGES.index(node.stage) - STAGES.index(parent.stage)
    if step not in (0, 1):
        return ChainBreak(
            code="wrong_stage", at=node.key, missing=rel,
            reason=f"a {node.stage} record is derived from {_previous(node.stage)} or its own "
            f"stage; {link.parent.key} is {parent.stage}",
        )  # fmt: skip
    if link.parent.key in stack or link.parent.key == node.key:
        return ChainBreak(
            code="cycle", at=node.key, missing=rel,
            reason=f"{link.parent.key} is already on this path",
        )  # fmt: skip
    return None


def walk(graph: ProvenanceGraph, terminal: str) -> ProvenanceTrace:
    """Follow `terminal` (a node key) back to its source records; never fills a gap."""
    reached: dict[str, NodeRef] = {}
    used: dict[tuple[str, str], ProvenanceLink] = {}
    breaks: list[ChainBreak] = []
    unhashed: set[str] = set()

    def visit(key: str, stack: tuple[str, ...]) -> None:
        node = graph.node(key)
        if node is None:
            breaks.append(
                ChainBreak(
                    code="unknown_node",
                    at=key,
                    missing="the record itself",
                    reason=f"{key} is not in the graph",
                )  # fmt: skip
            )
            return
        if key in reached:
            return
        reached[key] = node
        parents = [link for link in graph.links if link.child.key == key]
        if not parents and node.stage != "source_record":
            breaks.append(
                ChainBreak(
                    code="missing_link",
                    at=key,
                    missing=f"{node.stage} <- {_previous(node.stage)}",
                    reason=f"no link names the record this {node.record_type} was derived from",
                )  # fmt: skip
            )
        for link in parents:
            found = _link_problem(link, node, graph, stack)
            if found is not None:
                breaks.append(found)
                continue
            used[link.key] = link
            if link.parent.stated_sha256 is None:
                unhashed.add(f"{link.child.key} <- {link.parent.key} ({link.basis})")
            visit(link.parent.key, (*stack, key))

    visit(terminal, ())
    nodes = sorted(reached.values(), key=lambda n: (STAGES.index(n.stage), n.key))
    present = {n.category for n in nodes}
    return ProvenanceTrace(
        terminal=terminal,
        graph_sha256=graph.graph_sha256,
        complete=not breaks,
        nodes=tuple(nodes),
        links=tuple(used[k] for k in sorted(used)),
        breaks=tuple(sorted(breaks, key=lambda b: (b.at, b.code, b.missing, b.reason))),
        unhashed_links=tuple(sorted(unhashed)),
        categories=tuple(c for c in CATEGORIES if c in present),
    )


class ProblemStatement(EvidenceModel):
    """A traffic problem named by a deterministic rule on WorldView entities; no number, no model
    text. `statement` is written by the rule's code."""

    problem_version: Literal["0.1.0"] = PROBLEM_VERSION
    problem_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    rule_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    rule_version: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    produced_by: Literal["rule"] = "rule"
    world_model_sha256: Sha256 = Field(description="canonical: sha256 of model_dump_json()")
    view_sha256: Sha256 = Field(description="the WorldView whose entities it names")
    entity_ids: tuple[str, ...] = Field(min_length=1, description="sorted, unique")

    @field_validator("world_model_sha256", "view_sha256")
    @classmethod
    def _hashes(cls, value: str) -> str:
        return _check_sha256(value)

    @field_validator("entity_ids")
    @classmethod
    def _entities(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if not re.fullmatch(ENTITY_ID, value):
                raise ValueError(f"{value!r} is not a WorldView entity id")
        if list(values) != sorted(set(values)):
            raise ValueError("entity ids must be sorted and unique")
        return values
