"""Provenance nodes and links for perception's records: source to problem (ADR-033 addendum).

A node is made from a record this code holds: a file's bytes are hashed, a model is hashed the way
the repository already cites it. A link is made only from a field that states it, and carries the
hash that field states, so `walk` can check it against the node. Nothing here reads a file it was
not given or writes one; a record that is not supplied stays absent and `walk` reports the gap.

What each field establishes:
- `TrafficWorldModel.ground_source_sha256`: the dataset file its tracks came from;
- `TrafficWorldModel.tracks_sha256`: the tracks file it was built from;
- `TrafficWorldModel.clip_id` (calibration-anchored models): the clip, by id only;
- `CameraLayer.camera_clip_sha256`, `sam_request_keys`, `tracks_sha256`: the clip, the SAM
  requests (model output) and the camera tracks of the camera layer;
- `WorldView.world_model_sha256`: the snapshot each entity was projected from;
- `ProblemStatement.entity_ids` with `view_sha256`: the entities a problem is about.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from cairoflow_contracts.evidence import EvidenceKind, SourceClip, TrafficWorldModel
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.provenance import (
    Category,
    LinkEnd,
    NodeRef,
    ProblemStatement,
    ProvenanceLink,
)
from cairoflow_contracts.worldview import WorldEntity, WorldView


class ProvenanceError(ValueError):
    """Two records that should be tied are not; no link is made from them."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def world_model_sha256(world_model: TrafficWorldModel) -> str:
    """The canonical hash every record cites a world model by: sha256 of model_dump_json()."""
    return _sha256(world_model.model_dump_json().encode("utf-8"))


def dataset_file_node(path: Path) -> NodeRef:
    """A published dataset file (NGSIM trajectories), by the hash of its bytes."""
    digest = _sha256(path.read_bytes())
    return NodeRef(
        stage="source_record", record_type="DatasetFile", record_id=digest,
        content_sha256=digest, hash_basis="file_bytes", category="dataset_derived",
        evidence_kind="observed",
    )  # fmt: skip


def track_file_node(path: Path, category: Category, evidence_kind: EvidenceKind) -> NodeRef:
    """A tracks file, by the hash of its bytes: dataset tracks are observed; tracks derived from
    SAM output are approximated (tracking.py)."""
    digest = _sha256(path.read_bytes())
    return NodeRef(
        stage="observation", record_type="TrackFile", record_id=digest, content_sha256=digest,
        hash_basis="file_bytes", category=category, evidence_kind=evidence_kind,
    )  # fmt: skip


def source_clip_node(clip: SourceClip) -> NodeRef:
    """An ingested clip, by its `clip_id` and the hash of its output file (SourceClip)."""
    return NodeRef(
        stage="source_record", record_type="SourceClip", record_id=clip.clip_id,
        content_sha256=clip.output_sha256, hash_basis="file_bytes", category="camera_derived",
        evidence_kind="observed",
    )  # fmt: skip


def sam_response_node(request_key: str) -> NodeRef:
    """A cached SAM response, by its request key: model output, never ground truth."""
    return NodeRef(
        stage="observation", record_type="SamResponse", record_id=request_key,
        content_sha256=request_key, hash_basis="request_key", category="camera_derived",
        evidence_kind="model_generated", model_output=True,
    )  # fmt: skip


def world_model_node(world_model: TrafficWorldModel) -> NodeRef:
    digest = world_model_sha256(world_model)
    return NodeRef(
        stage="world_entity", record_type="TrafficWorldModel", record_id=digest,
        content_sha256=digest, hash_basis="model_dump_json", category="world_model",
        evidence_kind=world_model.provenance.kind,
    )  # fmt: skip


def world_entity_node(entity: WorldEntity) -> NodeRef:
    return NodeRef(
        stage="world_entity", record_type="WorldEntity", record_id=entity.entity_id,
        content_sha256=canonical_sha256(entity.model_dump(mode="json")),
        hash_basis="canonical_json", category="world_model", evidence_kind=entity.evidence_kind,
    )  # fmt: skip


def problem_node(problem: ProblemStatement) -> NodeRef:
    return NodeRef(
        stage="problem_statement", record_type="ProblemStatement", record_id=problem.problem_id,
        content_sha256=canonical_sha256(problem.model_dump(mode="json")),
        hash_basis="canonical_json", category="world_model", evidence_kind="observed",
    )  # fmt: skip


def _link(child: NodeRef, parent: LinkEnd, basis: str) -> ProvenanceLink:
    end = LinkEnd(
        record_type=child.record_type, record_id=child.record_id,
        stated_sha256=child.content_sha256,
    )  # fmt: skip
    return ProvenanceLink(child=end, parent=parent, basis=basis)


def world_model_links(world_model: TrafficWorldModel) -> list[ProvenanceLink]:
    """The links the world model's own fields state: its tracks, their dataset or clip, and its
    camera layer's tracks, SAM requests and clip. Tracks and clips are named by the hashes the
    model states; the files themselves are nodes only if the caller hashed them."""
    node = world_model_node(world_model)
    tracks = LinkEnd(
        record_type="TrackFile", record_id=world_model.tracks_sha256,
        stated_sha256=world_model.tracks_sha256,
    )  # fmt: skip
    links = [_link(node, tracks, "TrafficWorldModel.tracks_sha256")]
    if world_model.ground_source_sha256 is not None:
        source = world_model.ground_source_sha256
        dataset = LinkEnd(record_type="DatasetFile", record_id=source, stated_sha256=source)
        links.append(
            ProvenanceLink(
                child=tracks, parent=dataset, basis="TrafficWorldModel.ground_source_sha256"
            )
        )
    else:  # calibration-anchored: the model names its clip by id, with no hash
        clip = LinkEnd(record_type="SourceClip", record_id=world_model.clip_id)
        links.append(ProvenanceLink(child=tracks, parent=clip, basis="TrafficWorldModel.clip_id"))
    camera = world_model.camera
    if camera is not None:
        camera_tracks = LinkEnd(
            record_type="TrackFile", record_id=camera.tracks_sha256,
            stated_sha256=camera.tracks_sha256,
        )  # fmt: skip
        links.append(_link(node, camera_tracks, "TrafficWorldModel.camera.tracks_sha256"))
        clip = LinkEnd(
            record_type="SourceClip", record_id=camera.camera_clip_id,
            stated_sha256=camera.camera_clip_sha256,
        )  # fmt: skip
        for key in camera.sam_request_keys:
            response = LinkEnd(record_type="SamResponse", record_id=key, stated_sha256=key)
            links.append(
                ProvenanceLink(
                    child=camera_tracks, parent=response, basis="CameraLayer.sam_request_keys"
                )
            )
            links.append(
                ProvenanceLink(child=response, parent=clip, basis="CameraLayer.camera_clip_sha256")
            )
    return links


def world_view_links(
    view: WorldView, entity_ids: Iterable[str] | None = None
) -> tuple[list[NodeRef], list[ProvenanceLink]]:
    """Entity nodes of the view (all, or those named) and their link to the snapshot."""
    wanted = None if entity_ids is None else set(entity_ids)
    entities = [e for e in view.entities if wanted is None or e.entity_id in wanted]
    if wanted is not None and len(entities) != len(wanted):
        missing = sorted(wanted - {e.entity_id for e in entities})
        raise ProvenanceError(f"the view has no entity {', '.join(missing)}")
    snapshot = LinkEnd(
        record_type="TrafficWorldModel", record_id=view.world_model_sha256,
        stated_sha256=view.world_model_sha256,
    )  # fmt: skip
    nodes = [world_entity_node(e) for e in entities]
    return nodes, [_link(n, snapshot, "WorldView.world_model_sha256") for n in nodes]


def problem_links(problem: ProblemStatement, view: WorldView) -> list[ProvenanceLink]:
    """The problem's links to the entities it names, once it is known to be about this view."""
    if problem.view_sha256 != view.view_sha256:
        raise ProvenanceError("the problem names entities of another WorldView")
    if problem.world_model_sha256 != view.world_model_sha256:
        raise ProvenanceError("the problem is about another world model snapshot")
    nodes, _ = world_view_links(view, problem.entity_ids)
    child = problem_node(problem)
    return [
        _link(
            child,
            LinkEnd(
                record_type="WorldEntity", record_id=n.record_id, stated_sha256=n.content_sha256
            ),
            "ProblemStatement.entity_ids (bound by view_sha256)",
        )
        for n in nodes
    ]
