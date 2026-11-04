"""WorldView: a read-only projection of a TrafficWorldModel into entities (schema 0.1.0, ADR-033).

The TrafficWorldModel is the measurement record: tracks, counts, queue samples, a signal timeline,
hashed by its canonical JSON and cited by that hash everywhere. A WorldView does not replace or
change it. It is derived from it (cairoflow_perception.worldview), names what is in it as stable
entities with typed relations, and says for every entity what kind of evidence it rests on, how
sure it is and which source value it came from, so a reviewer can follow a decision back to the
numbers and an inspector can draw them. Nothing in a WorldView is simulated or model-generated.

Identity. `entity_id` is `<type>:<natural key>` from the source records (`movement:SN`,
`lane:S1`, `queue:S1`, `signal_head:N:left`), never a hash or a running number, so it survives
rebuilding and agrees with the evidence ids the model cites (`mv.SN`, `q.S1`). The view binds to
its snapshot by `world_model_sha256`, the canonical SHA-256 (`model_dump_json()`), the value
`EvidenceBundle.world_model_sha256` already holds, and records the geometry, the compiled network
and the evidence bundle it was built with, so a stored view can be rebuilt from its inputs.

Certainty. A relation is no more certain than the less certain of its two ends (observed, then
approximated, then assumed); the view refuses one that claims more.

Absence is data. A layer the snapshot lacks (no camera layer, no signal timeline), a movement the
conflict rule could not use and a signal note are listed in `absent`, an evidence item that maps to
no entity has an empty `entity_ids`, and a track that crossed only one gate is an `ambiguity`;
none of them is dropped or filled in.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from cairoflow_contracts.evidence import (
    Arm,
    EvidenceKind,
    EvidenceModel,
    GeometryProvenance,
    Sha256,
    _check_sha256,
)
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.reasoning import EVIDENCE_ID

VIEW_VERSION = "0.1.0"
ViewKind = Literal["observed", "approximated", "assumed"]
CERTAINTY: dict[ViewKind, int] = {"observed": 0, "approximated": 1, "assumed": 2}
KIND_OF_GEOMETRY: dict[GeometryProvenance, ViewKind] = {
    "measured": "observed",
    "approximated": "approximated",
    "synthetic": "assumed",
}
KIND_OF_SIGNAL_LABEL: dict[str, ViewKind] = {
    "observed-annotated": "observed",
    "published": "observed",
    "trajectory-derived": "approximated",
    "assumed": "assumed",
}
EntityType = Literal[
    "intersection",
    "approach",
    "lane",
    "movement",
    "signal_head",
    "signal_program",
    "conflict_zone",
    "queue",
    "camera_layer",
    "camera_track",
]
Relation = Literal["contains", "allows", "controlled_by", "involves", "queues_on", "observed_in"]
EntityStatus = Literal["present", "unobserved", "censored", "derived_rule"]
SourceModel = Literal[
    "TrafficWorldModel", "IntersectionGeometry", "SignalTimeline", "CameraLayer", "CompiledNetwork"
]
ENTITY_ID = r"^[a-z_]+:[A-Za-z0-9][A-Za-z0-9_.@|:-]*$"
Attribute = str | int | float | bool | None


def weakest(*kinds: ViewKind) -> ViewKind:
    """The least certain of the given evidence kinds."""
    return max(kinds, key=CERTAINTY.__getitem__)


class Uncertainty(EvidenceModel):
    """How far a value can be trusted, in the value's own unit."""

    interval95: tuple[float, float] | None = Field(None, description="95% interval, if known")
    unit: str | None = None
    lower_bound: bool = Field(False, description="the true value is at least this: censored data")
    note: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Uncertainty:
        if self.interval95 is not None and self.interval95[0] > self.interval95[1]:
            raise ValueError("an interval runs from low to high")
        return self


class SourceRef(EvidenceModel):
    """Where in the source records an entity's values come from."""

    model: SourceModel
    path: str = Field(min_length=1, description="e.g. 'movements[SN]' or 'lanes[S1]'")


class WorldEntity(EvidenceModel):
    entity_id: str = Field(pattern=ENTITY_ID)
    entity_type: EntityType
    label: str = Field(min_length=1)
    evidence_kind: ViewKind = Field(description="observed, approximated or assumed, as recorded")
    status: EntityStatus
    attributes: dict[str, Attribute] = Field(default_factory=dict, description="units in the key")
    uncertainty: Uncertainty | None = None
    source: SourceRef

    @model_validator(mode="after")
    def _typed_id(self) -> WorldEntity:
        if not self.entity_id.startswith(f"{self.entity_type}:"):
            raise ValueError("entity_id must start with its entity type")
        return self


class WorldRelation(EvidenceModel):
    source_id: str
    relation: Relation
    target_id: str
    evidence_kind: ViewKind = Field(description="the less certain of the two ends")
    basis: str = Field(min_length=1, description="why the relation holds, in a sentence")


class EvidenceRef(EvidenceModel):
    """One evidence item of a bundle and the entities it describes (none: said so, not hidden)."""

    evidence_id: str = Field(pattern=EVIDENCE_ID)
    kind: EvidenceKind
    entity_ids: tuple[str, ...]
    note: str | None = Field(None, description="why it maps to no entity, or to several")


class Ambiguity(EvidenceModel):
    """A track that crossed only one of a movement's two gates: listed, never counted."""

    track_id: str
    approach: Arm | None
    exit_arm: Arm | None
    reason: str = Field(min_length=1)


class Absence(EvidenceModel):
    layer: Literal["camera", "signal_timeline", "signal_program", "queues", "conflict_zones"]
    reason: str = Field(min_length=1)


class WorldView(EvidenceModel):
    view_version: Literal["0.1.0"] = VIEW_VERSION
    clip_id: str
    geometry_id: str
    geometry_sha256: Sha256 = Field(description="canonical, as TrafficWorldModel.geometry_sha256")
    world_model_sha256: Sha256 = Field(description="canonical: sha256 of model_dump_json()")
    net_sha256: Sha256 | None = Field(None, description="the compiled network, if one was given")
    bundle_id: str | None = Field(None, description="the evidence bundle, if one was given")
    entities: tuple[WorldEntity, ...]
    relations: tuple[WorldRelation, ...]
    evidence: tuple[EvidenceRef, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    absent: tuple[Absence, ...] = ()
    view_sha256: Sha256

    @staticmethod
    def digest_of(fields: dict[str, Any]) -> str:
        """view_sha256 of the other fields, in their JSON form."""
        return canonical_sha256({k: v for k, v in fields.items() if k != "view_sha256"})

    @field_validator("geometry_sha256", "world_model_sha256", "net_sha256", "view_sha256")
    @classmethod
    def _hash(cls, value: str | None) -> str | None:
        return None if value is None else _check_sha256(value)

    @model_validator(mode="after")
    def _consistent(self) -> WorldView:
        by_id = {e.entity_id: e for e in self.entities}
        if len(by_id) != len(self.entities):
            raise ValueError("entity ids must be unique")
        keys = [(r.source_id, r.relation, r.target_id) for r in self.relations]
        if len(set(keys)) != len(keys):
            raise ValueError("a relation may be stated once")
        for r in self.relations:
            if r.source_id not in by_id or r.target_id not in by_id:
                raise ValueError(f"relation {r.source_id} {r.relation} {r.target_id}: unknown end")
            ends = weakest(by_id[r.source_id].evidence_kind, by_id[r.target_id].evidence_kind)
            if r.evidence_kind != ends:
                raise ValueError(
                    f"relation {r.source_id} {r.relation} {r.target_id} is {r.evidence_kind} "
                    f"but its less certain end is {ends}"
                )
        for item in self.evidence:
            if not set(item.entity_ids) <= by_id.keys():
                raise ValueError(f"evidence {item.evidence_id} names an unknown entity")
        if len({e.evidence_id for e in self.evidence}) != len(self.evidence):
            raise ValueError("evidence ids must be unique")
        if self.view_sha256 != self.digest_of(self.model_dump(mode="json")):
            raise ValueError("view_sha256 does not hash the view's content")
        return self

    def entity(self, entity_id: str) -> WorldEntity:
        for found in self.entities:
            if found.entity_id == entity_id:
                return found
        raise KeyError(entity_id)

    def of_type(self, entity_type: EntityType) -> tuple[WorldEntity, ...]:
        return tuple(e for e in self.entities if e.entity_type == entity_type)

    def related(self, entity_id: str, relation: Relation) -> tuple[str, ...]:
        """Ids of the entities `entity_id` points to by `relation`."""
        return tuple(
            r.target_id
            for r in self.relations
            if r.source_id == entity_id and r.relation == relation
        )

    @property
    def unmapped_evidence_ids(self) -> tuple[str, ...]:
        return tuple(e.evidence_id for e in self.evidence if not e.entity_ids)
