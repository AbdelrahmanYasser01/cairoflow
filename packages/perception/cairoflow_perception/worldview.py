"""Project a TrafficWorldModel into a WorldView (ADR-033): a pure function, nothing is written.

Sources: the world model (movements, queues, ambiguous tracks, signal timeline, camera layer), the
geometry it was measured on (lanes, gates) and, when given, the compiled network (signal program)
and the evidence bundle the reasoning stage read. Every tie the projection relies on is checked
and a disagreement is an error, not a guess:

- the geometry must hash to `world_model.geometry_sha256` (canonical JSON, as world_model.py);
- the network must have been compiled from that geometry;
- the bundle must be about this snapshot (`world_model_sha256`, `clip_id`) and, when a network is
  given, about this network (`net_sha256`, `program_id`);
- every lane a movement, queue sample, camera track or timeline head names must exist, and every
  entity id must be a valid, unique stable id.

Evidence kinds follow the source labels (contracts.worldview.KIND_OF_GEOMETRY and
KIND_OF_SIGNAL_LABEL). A world model's own provenance kind is used for what it counted. A relation
takes the less certain kind of its two ends.

Conflict zones are not in any source record. They are derived here by a stated rule, labelled
`assumed` and status `derived_rule`: a movement's path is the straight chord from its approach's
stop-line midpoint to its exit arm's exit-line midpoint; two movements of different approaches
conflict where their chords cross (`crossing`) or end at the same exit (`merging`). The chords are
not surveyed paths and the zones carry no measurement. A movement the rule cannot use (a u-turn,
an arm without the gate it needs) is listed in `absent`, so no zone is not read as no conflict.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any

from cairoflow_contracts.evidence import (
    ARM_ORDER,
    EvidenceKind,
    IntersectionGeometry,
    MovementCount,
    TrafficWorldModel,
)
from cairoflow_contracts.experiment import CompiledNetworkManifest
from cairoflow_contracts.reasoning import EvidenceBundle
from cairoflow_contracts.worldview import (
    ENTITY_ID,
    KIND_OF_GEOMETRY,
    KIND_OF_SIGNAL_LABEL,
    VIEW_VERSION,
    Absence,
    Ambiguity,
    EntityStatus,
    EntityType,
    EvidenceRef,
    Relation,
    SourceRef,
    Uncertainty,
    ViewKind,
    WorldEntity,
    WorldRelation,
    WorldView,
    weakest,
)

_TYPE_ORDER: tuple[EntityType, ...] = (
    "intersection", "approach", "lane", "movement", "signal_head", "signal_program",
    "conflict_zone", "queue", "camera_layer", "camera_track",
)  # fmt: skip
_ID = re.compile(ENTITY_ID)
_LOWER_BOUND = "camera counts are lower bounds: the layer sees only some of the vehicles in view"
_CHORD_NOTE = "chord approximation of the paths; not a surveyed conflict point"
Point = tuple[float, float]


class WorldViewError(ValueError):
    """The records do not describe the same snapshot, or one cannot be projected."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _view_kind(kind: EvidenceKind) -> ViewKind:
    if kind in ("model_generated", "simulated"):
        raise WorldViewError(f"a world view holds no {kind} evidence")
    return kind


def _crossing(p: Point, p2: Point, q: Point, q2: Point) -> Point | None:
    """Where segments p-p2 and q-q2 cross strictly inside both; None if parallel or apart."""
    r, s = (p2[0] - p[0], p2[1] - p[1]), (q2[0] - q[0], q2[1] - q[1])
    denominator = r[0] * s[1] - r[1] * s[0]
    if abs(denominator) < 1e-9:
        return None
    t = ((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / denominator
    u = ((q[0] - p[0]) * r[1] - (q[1] - p[1]) * r[0]) / denominator
    return (p[0] + t * r[0], p[1] + t * r[1]) if 0 < t < 1 and 0 < u < 1 else None


class _Builder:
    def __init__(
        self,
        world_model: TrafficWorldModel,
        geometry: IntersectionGeometry,
        network: CompiledNetworkManifest | None,
        bundle: EvidenceBundle | None,
    ) -> None:
        self.wm, self.geo, self.network, self.bundle = world_model, geometry, network, bundle
        self.entities: dict[str, WorldEntity] = {}
        self.relations: dict[tuple[str, str, str], WorldRelation] = {}
        self.absent: list[Absence] = []
        self.root = f"intersection:{geometry.geometry_id}"
        self.movements: dict[str, MovementCount] = {}
        self.stops = {g.arm: g for g in geometry.gates if g.role == "stop_line"}
        self.exits = {g.arm: g for g in geometry.gates if g.role == "exit_line"}
        self.program_id: str | None = None
        self.snapshot = _sha256(world_model.model_dump_json())

    # --- tools ---------------------------------------------------------------------------------

    def ties(self) -> None:
        wm, bundle, network = self.wm, self.bundle, self.network
        if (
            self.geo.geometry_id != wm.geometry_id
            or _sha256(self.geo.model_dump_json()) != wm.geometry_sha256
        ):
            raise WorldViewError("the world model was not measured on this geometry")
        if network is not None and network.geometry_sha256 != wm.geometry_sha256:
            raise WorldViewError("the network was not compiled from the world model's geometry")
        if bundle is None:
            return
        if bundle.world_model_sha256 != self.snapshot or bundle.clip_id != wm.clip_id:
            raise WorldViewError("the evidence bundle is about another world model snapshot")
        if network is not None and (
            bundle.net_sha256 != network.net_sha256
            or bundle.program_id != network.signal_program.program_id
        ):
            raise WorldViewError("the evidence bundle was built from another network")

    def add(
        self,
        entity_id: str,
        entity_type: EntityType,
        label: str,
        kind: ViewKind,
        status: EntityStatus,
        source: SourceRef,
        attributes: dict[str, Any] | None = None,
        uncertainty: Uncertainty | None = None,
    ) -> None:
        if not _ID.match(entity_id):
            raise WorldViewError(f"{source.path!r} cannot be a stable id: {entity_id!r}")
        if entity_id in self.entities:
            raise WorldViewError(f"{entity_id} appears twice in the sources ({source.path})")
        self.entities[entity_id] = WorldEntity(
            entity_id=entity_id, entity_type=entity_type, label=label, evidence_kind=kind,
            status=status, attributes=attributes or {}, uncertainty=uncertainty, source=source,
        )  # fmt: skip

    def link(self, source: str, relation: Relation, target: str, basis: str) -> None:
        key = (source, relation, target)
        if key in self.relations:
            raise WorldViewError(f"{source} {relation} {target} is stated twice")
        kind = weakest(self.entities[source].evidence_kind, self.entities[target].evidence_kind)
        self.relations[key] = WorldRelation(
            source_id=source, relation=relation, target_id=target, evidence_kind=kind, basis=basis
        )

    def wm_ref(self, path: str) -> SourceRef:
        return SourceRef(model="TrafficWorldModel", path=path)

    def geo_ref(self, path: str) -> SourceRef:
        return SourceRef(model="IntersectionGeometry", path=path)

    # --- layers --------------------------------------------------------------------------------

    def geometry_layer(self) -> None:
        lanes_of = defaultdict(list)
        for lane in self.geo.lanes:
            lanes_of[lane.arm].append(lane)
        self.add(
            self.root, "intersection", f"intersection {self.geo.geometry_id}",
            _view_kind(self.wm.provenance.kind), "present", self.wm_ref("."),
            {"clip_id": self.wm.clip_id, "window_s": self.wm.window_s,
             "ground_frame": self.geo.ground_frame, "drives_on": self.geo.drives_on,
             "ambiguous_tracks": len(self.wm.ambiguous)},
        )  # fmt: skip
        for arm in ARM_ORDER:
            if arm not in {*lanes_of, *self.stops, *self.exits}:
                continue
            gate = self.stops.get(arm) or self.exits.get(arm)
            if gate is not None:
                kind, path = KIND_OF_GEOMETRY[gate.provenance], f"gates[{arm}]"
            else:  # lanes only: the arm has no gate to take its label from
                lane_kinds = [KIND_OF_GEOMETRY[lane.provenance] for lane in lanes_of[arm]]
                kind, path = weakest(*lane_kinds), f"lanes[arm {arm}]"
            approach_lanes = [lane for lane in lanes_of[arm] if lane.direction == "approach"]
            self.add(
                f"approach:{arm}", "approach", f"arm {arm}", kind, "present", self.geo_ref(path),
                {"approach_lanes": len(approach_lanes), "has_stop_line": arm in self.stops,
                 "has_exit_line": arm in self.exits},
            )  # fmt: skip
            self.link(self.root, "contains", f"approach:{arm}", "arm of the junction geometry")
        for lane in self.geo.lanes:
            lane_id = f"lane:{lane.lane_id}"
            self.add(
                lane_id, "lane", f"lane {lane.lane_id}", KIND_OF_GEOMETRY[lane.provenance],
                "present", self.geo_ref(f"lanes[{lane.lane_id}]"),
                {"arm": lane.arm, "direction": lane.direction,
                 "allowed_turns": ",".join(lane.allowed_turns) or None},
            )  # fmt: skip
            self.link(f"approach:{lane.arm}", "contains", lane_id, "lane of the arm")

    def movement_layer(self) -> None:
        for m in self.wm.movements:
            key = f"{m.approach}{m.exit_arm}"
            low, high = m.flow_ci95_veh_per_h
            self.add(
                f"movement:{key}", "movement", f"{m.approach} to {m.exit_arm} ({m.turn})",
                _view_kind(self.wm.provenance.kind), "present" if m.count_veh else "unobserved",
                self.wm_ref(f"movements[{key}]"),
                {"turn": m.turn, "count_veh": m.count_veh, "flow_veh_per_h": m.flow_veh_per_h},
                Uncertainty(interval95=(low, high), unit="veh/h",
                            note="exact Poisson 95% interval of the stop-line crossings"),
            )  # fmt: skip
            self.movements[key] = m
            for lane_id in m.marked_lane_ids:
                if f"lane:{lane_id}" not in self.entities:
                    raise WorldViewError(
                        f"movement {key} names lane {lane_id}, not in the geometry"
                    )
                self.link(f"lane:{lane_id}", "allows", f"movement:{key}",
                          "the lane's markings allow this turn")  # fmt: skip

    def queue_layer(self) -> None:
        samples, reds = defaultdict(list), defaultdict(list)
        for sample in self.wm.queues:
            samples[sample.lane_id].append(sample)
        for sample in self.wm.end_of_red_queues:
            reds[sample.lane_id].append(sample)
        lanes = sorted({*samples, *reds})
        strangers = [lane for lane in lanes if f"lane:{lane}" not in self.entities]
        if strangers:
            raise WorldViewError(f"queue samples on lanes the geometry lacks: {strangers}")
        if not lanes:
            self.absent.append(
                Absence(layer="queues", reason="the snapshot holds no queue samples")
            )
        for lane in lanes:
            own, red = samples[lane], reds[lane]
            censored = sum(s.censored for s in own)
            red_censored = sum(s.censored for s in red)
            every = [*own, *red]
            cut = bool(censored or red_censored)
            self.add(
                f"queue:{lane}", "queue", f"queue on lane {lane}", "approximated",
                "censored" if cut else "present", self.wm_ref(f"queues[{lane}]"),
                {"samples": len(own), "max_queued_veh": max(s.queued_veh for s in every),
                 "max_length_m": max(s.length_m for s in every),
                 "max_length_error_m": max(s.length_error_m for s in every),
                 "censored_samples": censored, "end_of_red_samples": len(red),
                 "end_of_red_censored": red_censored},
                Uncertainty(unit="m", lower_bound=cut,
                            note=f"{censored + red_censored} samples reach the lane's far end"
                            if cut else None),
            )  # fmt: skip
            self.link(f"queue:{lane}", "queues_on", f"lane:{lane}",
                      "stopped vehicles chained back from the stop line in this lane")  # fmt: skip

    def signal_layer(self) -> None:
        timeline = self.wm.signal_timeline
        for note in self.wm.signal_notes:
            self.absent.append(Absence(layer="signal_timeline", reason=f"note: {note}"))
        if timeline is None:
            self.absent.append(
                Absence(layer="signal_timeline", reason="the snapshot has no signal timeline")
            )
            return
        kind = KIND_OF_SIGNAL_LABEL[timeline.label]
        heads = timeline.heads()
        for arm, head in heads:
            if f"approach:{arm}" not in self.entities:
                raise WorldViewError(
                    f"the signal timeline has a head for arm {arm}, not in the geometry"
                )
            seconds: Counter[str] = Counter()
            for i in timeline.intervals:
                if i.arm == arm and i.head == head:
                    seconds[i.state] += i.end_s - i.start_s
            suffix = "" if head == "all" else f":{head}"
            self.add(
                f"signal_head:{arm}{suffix}", "signal_head", f"signal head {arm} {head}", kind,
                "unobserved" if seconds["unknown"] >= timeline.window_s - 1e-6 else "present",
                SourceRef(model="SignalTimeline", path=f"intervals[{arm},{head}]"),
                {"arm": arm, "head": head, "timeline_id": timeline.timeline_id,
                 "timeline_label": timeline.label,
                 **{f"{s}_s": round(seconds.get(s, 0.0), 3)
                    for s in ("green", "yellow", "red", "unknown")}},
                Uncertainty(note="estimated from vehicle trajectories, not read from the signal"
                            if timeline.label == "trajectory-derived" else None),
            )  # fmt: skip
            self.link(f"approach:{arm}", "contains", f"signal_head:{arm}{suffix}",
                      "head of the arm in the signal timeline")  # fmt: skip
        missing: set[str] = set()
        for key, m in self.movements.items():
            if m.turn == "u_turn":
                reason = f"movement {key} is a u-turn: no head is assigned"
                self.absent.append(Absence(layer="signal_timeline", reason=reason))
                continue
            head = (
                (m.approach, "left")
                if m.turn == "left" and (m.approach, "left") in heads
                else ((m.approach, "all"))
            )
            if head in heads:
                suffix = "" if head[1] == "all" else ":left"
                self.link(f"movement:{key}", "controlled_by", f"signal_head:{head[0]}{suffix}",
                          "left head for left turns, the other head for the rest")  # fmt: skip
            elif m.approach not in missing:
                missing.add(m.approach)
                self.absent.append(Absence(
                    layer="signal_timeline", reason=f"the timeline has no head for arm {m.approach}"
                ))  # fmt: skip

    def program_layer(self) -> None:
        if self.network is None:
            self.absent.append(
                Absence(layer="signal_program", reason="no compiled network was supplied")
            )
            return
        program = self.network.signal_program
        self.program_id = program.program_id
        self.add(
            f"signal_program:{program.program_id}", "signal_program",
            f"signal program {program.program_id}", KIND_OF_SIGNAL_LABEL[program.label], "present",
            SourceRef(model="CompiledNetwork", path=f"signal_program[{program.program_id}]"),
            {"timeline_id": program.timeline_id, "label": program.label, "cycle_s": program.cycle_s,
             "phases": len(program.phases),
             "adjustable_phases": sum(p.adjustable for p in program.phases)},
        )  # fmt: skip
        self.link(self.root, "contains", f"signal_program:{program.program_id}",
                  "the program the simulator runs for this junction")  # fmt: skip

    def conflict_layer(self) -> None:
        chords: dict[str, tuple[Point, Point]] = {}
        for key, m in self.movements.items():
            why = None
            if m.turn == "u_turn":
                why = "a u-turn has no chord"
            elif m.approach not in self.stops:
                why = f"arm {m.approach} has no stop line"
            elif m.exit_arm not in self.exits:
                why = f"arm {m.exit_arm} has no exit line"
            if why is not None:
                self.absent.append(Absence(layer="conflict_zones", reason=f"movement {key}: {why}"))
                continue
            (ax, ay), (bx, by) = self.stops[m.approach].segment_m
            (cx, cy), (dx, dy) = self.exits[m.exit_arm].segment_m
            chords[key] = (((ax + bx) / 2, (ay + by) / 2), ((cx + dx) / 2, (cy + dy) / 2))
        for first in sorted(chords):
            for second in sorted(chords):
                a, b = self.movements[first], self.movements[second]
                if first >= second or a.approach == b.approach:
                    continue
                if a.exit_arm == b.exit_arm:
                    kind, point = "merging", chords[first][1]
                elif point := _crossing(*chords[first], *chords[second]):
                    kind = "crossing"
                else:
                    continue
                zone = f"conflict_zone:{first}|{second}"
                self.add(
                    zone, "conflict_zone", f"conflict of {first} and {second}", "assumed",
                    "derived_rule", self.wm_ref(f"movements[{first}],movements[{second}]"),
                    {"conflict_type": kind, "x_m": round(point[0], 3), "y_m": round(point[1], 3)},
                    Uncertainty(note=_CHORD_NOTE),
                )  # fmt: skip
                for key in (first, second):
                    self.link(zone, "involves", f"movement:{key}",
                              "stop-line to exit-line chords of the two movements")  # fmt: skip

    def camera_layer(self) -> None:
        camera = self.wm.camera
        if camera is None:
            self.absent.append(
                Absence(layer="camera", reason="no camera layer is attached to this snapshot")
            )
            return
        strangers = sorted(
            {
                t.lane_id
                for t in camera.tracks
                if t.lane_id and f"lane:{t.lane_id}" not in self.entities
            }
        )
        if strangers:  # lanes are linked by id, so a layer of another geometry must not link
            raise WorldViewError(f"camera tracks name lanes the geometry lacks: {strangers}")
        layer = f"camera_layer:{camera.camera_clip_id}"
        caveat = (
            ""
            if camera.calibration_accepted
            else "; calibration not accepted for along-road distances"
        )
        self.add(
            layer, "camera_layer", f"camera layer {camera.label}", "approximated", "present",
            SourceRef(model="CameraLayer", path="."),
            {"sam_model": camera.sam_model, "window_start_s": camera.window_s[0],
             "window_end_s": camera.window_s[1],
             "calibration_accepted": camera.calibration_accepted,
             "gate_id": camera.gate.gate_id, "gate_outcome": camera.gate.outcome,
             "tracks": len(camera.tracks), "derived": "; ".join(camera.derived),
             "not_derived": "; ".join(camera.not_derived)},
            Uncertainty(lower_bound=True, note=_LOWER_BOUND + caveat),
        )  # fmt: skip
        self.link(self.root, "contains", layer, "camera-derived entities beside the trajectories'")
        for track in camera.tracks:
            track_id = f"camera_track:{track.track_id}"
            self.add(
                track_id, "camera_track", f"camera track {track.track_id}", "approximated",
                "present", SourceRef(model="CameraLayer", path=track.track_id),
                {"sam_track_id": track.sam_track_id, "points": len(track.points),
                 "lane_id": track.lane_id, "lane_share": track.lane_share},
                Uncertainty(lower_bound=True, note=_LOWER_BOUND),
            )  # fmt: skip
            self.link(layer, "contains", track_id, "track of the camera layer")
            if track.lane_id is not None:
                self.link(track_id, "observed_in", f"lane:{track.lane_id}",
                          "the lane holding most of the track's lane-placed points")  # fmt: skip

    def evidence_layer(self) -> tuple[EvidenceRef, ...]:
        if self.bundle is None:
            return ()
        queue_lanes = {i[len("queue:") :] for i in self.entities if i.startswith("queue:")}
        camera_layers = [i for i in self.entities if i.startswith("camera_layer:")]
        refs = []
        for item in self.bundle.items:
            prefix, _, rest = item.evidence_id.partition(".")
            found: list[str] = []
            note: str | None = None
            if prefix == "mv" and f"movement:{rest}" in self.entities:
                found = [f"movement:{rest}"]
            elif prefix in ("q", "qr"):
                # `q.<lane>` names a lane; `qr.<lane>` or `qr.<lane>.f<frame>` also the red ends
                stem, _, frame = rest.rpartition(".f")
                readings = (rest, stem) if prefix == "qr" and frame.isdecimal() else (rest,)
                reads = [lane for lane in readings if lane in queue_lanes]
                found = [f"queue:{lane}" for lane in dict.fromkeys(reads)]
                if len(found) > 1:
                    note = (
                        f"ambiguous id: {rest!r} reads as lane {rest} or as lane {stem} at a frame"
                    )
                elif not found:
                    note = f"no lane in {rest!r} has queue samples in this snapshot"
            elif (
                prefix == "cam" and rest.startswith("lane.") and f"lane:{rest[5:]}" in self.entities
            ):
                found = [f"lane:{rest[5:]}"]
            elif item.evidence_id == "cam.gate" and camera_layers:
                found, note = camera_layers[:1], "the camera layer's gate result"
            elif item.evidence_id == "sig.program":
                if self.program_id is not None:
                    found = [f"signal_program:{self.program_id}"]
                else:
                    note = "the signal program is not in the view: no compiled network was supplied"
            elif item.evidence_id == "amb.count" or prefix == "sig":
                found, note = [self.root], "describes the snapshot as a whole"
            else:
                note = "no entity in the view corresponds to this item"
            refs.append(EvidenceRef(evidence_id=item.evidence_id, kind=item.kind,
                                    entity_ids=tuple(found), note=note))  # fmt: skip
        return tuple(sorted(refs, key=lambda r: r.evidence_id))


def build_world_view(
    world_model: TrafficWorldModel,
    geometry: IntersectionGeometry,
    *,
    network: CompiledNetworkManifest | None = None,
    bundle: EvidenceBundle | None = None,
) -> WorldView:
    """The view of `world_model`; it leaves the model, the geometry, the network and the bundle
    untouched (they are frozen) and hashes to the same values afterwards."""
    builder = _Builder(world_model, geometry, network, bundle)
    builder.ties()
    builder.geometry_layer()
    builder.movement_layer()
    builder.queue_layer()
    builder.signal_layer()
    builder.program_layer()
    builder.conflict_layer()
    builder.camera_layer()
    fields: dict[str, Any] = {
        "schema_version": "0.1.0",
        "view_version": VIEW_VERSION,
        "clip_id": world_model.clip_id,
        "geometry_id": geometry.geometry_id,
        "geometry_sha256": world_model.geometry_sha256,
        "world_model_sha256": builder.snapshot,
        "net_sha256": None if network is None else network.net_sha256,
        "bundle_id": None if bundle is None else bundle.bundle_id,
        "entities": tuple(sorted(
            builder.entities.values(),
            key=lambda e: (_TYPE_ORDER.index(e.entity_type), e.entity_id),
        )),
        "relations": tuple(sorted(
            builder.relations.values(), key=lambda r: (r.source_id, r.relation, r.target_id)
        )),
        "evidence": builder.evidence_layer(),
        "ambiguities": tuple(
            Ambiguity(
                track_id=a.track_id, approach=a.approach, exit_arm=a.exit_arm, reason=a.reason
            )
            for a in world_model.ambiguous
        ),
        "absent": tuple(builder.absent),
    }  # fmt: skip
    unsigned = WorldView.model_construct(**fields, view_sha256="0" * 64)
    return WorldView(**fields, view_sha256=WorldView.digest_of(unsigned.model_dump(mode="json")))
