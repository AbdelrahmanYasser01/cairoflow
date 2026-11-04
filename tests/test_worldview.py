"""WorldView: the projection of a world model into entities (ADR-033).

Built on the synthetic junction (synthetic_junction.py, synthetic_scenario.py): made-up counts on
made-up lanes, only to check the projection. Nothing here is traffic evidence. Two tests attach the
committed camera layer to the committed geometry it was measured on. The last tests read the real
snapshots and are skipped where those files are absent (the snapshots are Git-ignored).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from synthetic_clip import frame
from synthetic_junction import lane
from synthetic_scenario import SIGNALS, scenario_geometry, world_model

from cairoflow_contracts.evidence import (
    AmbiguousMovement,
    CameraLayer,
    IntersectionGeometry,
    QueueSample,
    SignalInterval,
    SignalTimeline,
    TrafficWorldModel,
)
from cairoflow_contracts.experiment import (
    CompiledNetworkManifest,
    SignalPhaseSpec,
    SignalProgramSpec,
)
from cairoflow_contracts.reasoning import EvidenceBundle
from cairoflow_contracts.worldview import (
    KIND_OF_GEOMETRY,
    KIND_OF_SIGNAL_LABEL,
    WorldView,
    weakest,
)
from cairoflow_perception.worldview import WorldViewError, build_world_view
from cairoflow_reasoning import evidence as reasoning_evidence
from cairoflow_reasoning.evidence import build_evidence_bundle

REPO = Path(__file__).resolve().parents[1]
GEO = scenario_geometry()
GEOMETRY_FILE = REPO / "data/geometry/ngsim-lankershim-int2/geometry.json"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def queue_sample(
    index: int, queued: int, censored: bool = False, lane_id: str = "S1"
) -> QueueSample:
    return QueueSample(
        frame=frame(index), arm="S", lane_id=lane_id, queued_veh=queued, length_m=queued * 7.0,
        length_error_m=0.5, track_ids=tuple(f"car:t{n}" for n in range(queued)),
        unknown_speed_track_ids=(), censored=censored,
    )  # fmt: skip


def network(geometry=GEO, net_sha: str = "d" * 64, program_id: str = "test_cycle"):
    arms = {"N": "green", "S": "green", "E": "red", "W": "red"}
    program = SignalProgramSpec(
        program_id=program_id, timeline_id="synthetic-cycle", label="assumed",
        annotation_sha256=None, cycle_start_s=0, cycle_s=30,
        phases=(SignalPhaseSpec(name="p0", duration_s=30, state="GGrr", arm_states=arms),),
    )  # fmt: skip
    return CompiledNetworkManifest(
        geometry_id=geometry.geometry_id, geometry_sha256=sha(geometry.model_dump_json()),
        sumo_version="test", plain_xml_sha256={}, net_sha256=net_sha, link_count=1,
        parameters=(), signal_program=program, netconvert_warnings=(), notes=(),
    )  # fmt: skip


def full_model(geometry=GEO):
    """Synthetic counts plus queues (one censored), a red-end sample and the signal timeline."""
    samples = (queue_sample(1, 2), queue_sample(2, 4, censored=True), queue_sample(3, 0))
    return world_model(geometry).model_copy(
        update={
            "queues": samples,
            "end_of_red_queues": (queue_sample(12, 3),),
            "signal_timeline": SIGNALS,
        }  # fmt: skip
    )


def left_head_timeline(arms: str = "NESW") -> SignalTimeline:
    """60 s: N and S through, S has a protected-left head, then E and W (when `arms` has them)."""

    def cycle(arm, head, spans):
        return [
            SignalInterval(arm=arm, head=head, state=s, start_s=a, end_s=b) for s, a, b in spans
        ]

    through = [("green", 0, 20), ("yellow", 20, 23), ("red", 23, 60)]
    cross = [("red", 0, 45), ("green", 45, 55), ("yellow", 55, 58), ("red", 58, 60)]
    left = [("red", 0, 30), ("green", 30, 40), ("yellow", 40, 43), ("red", 43, 60)]
    intervals = [
        *(cycle("N", "all", through) if "N" in arms else []),
        *(cycle("E", "all", cross) if "E" in arms else []),
        *cycle("S", "all", through), *cycle("S", "left", left),
        *(cycle("W", "all", cross) if "W" in arms else []),
    ]  # fmt: skip
    return SignalTimeline(
        timeline_id="synthetic-left", clip_id=SIGNALS.clip_id, label="assumed", source="test plan",
        window_s=60.0, intervals=tuple(intervals),
    )  # fmt: skip


# --- what the view holds -----------------------------------------------------------------------


def test_the_view_names_the_entities_of_the_snapshot() -> None:
    view = build_world_view(full_model(), GEO, network=network())
    counts = {t: len(view.of_type(t)) for t in
              ("intersection", "approach", "lane", "movement", "signal_head", "signal_program",
               "queue", "camera_layer", "camera_track")}  # fmt: skip
    assert counts == {
        "intersection": 1, "approach": 4, "lane": 4, "movement": 8, "signal_head": 4,
        "signal_program": 1, "queue": 1, "camera_layer": 0, "camera_track": 0,
    }  # fmt: skip
    assert view.of_type("conflict_zone")  # derived by rule, below
    assert view.related("approach:S", "contains") == ("lane:S1", "lane:S2", "signal_head:S")
    assert view.related("movement:SN", "controlled_by") == ("signal_head:S",)
    assert view.related("queue:S1", "queues_on") == ("lane:S1",)
    assert (view.geometry_sha256, view.net_sha256, view.bundle_id) == (
        sha(GEO.model_dump_json()), "d" * 64, None,
    )  # fmt: skip


def test_ids_come_from_the_sources_and_survive_a_rebuild() -> None:
    first = build_world_view(full_model(), GEO, network=network())
    again = build_world_view(full_model(), GEO, network=network())
    assert first == again and first.view_sha256 == again.view_sha256
    assert {e.entity_id for e in first.entities} >= {
        "intersection:synthetic-4arm", "approach:E", "lane:S1", "movement:SN", "queue:S1",
        "signal_head:N", "signal_program:test_cycle",
    }  # fmt: skip
    order = ["intersection", "approach", "lane", "movement", "signal_head", "signal_program",
             "conflict_zone", "queue", "camera_layer", "camera_track"]  # fmt: skip
    assert [(order.index(e.entity_type), e.entity_id) for e in first.entities] == sorted(
        (order.index(e.entity_type), e.entity_id) for e in first.entities
    )  # a stable order: by type, then id
    assert WorldView.model_validate_json(first.model_dump_json()) == first


def test_the_projection_leaves_the_canonical_models_untouched() -> None:
    """Hash-stable models are cited by their canonical JSON everywhere; the view changes none."""
    model, manifest = full_model(), network()
    bundle = build_evidence_bundle(model, manifest)
    before = (sha(model.model_dump_json()), sha(GEO.model_dump_json()),
              sha(manifest.model_dump_json()), sha(bundle.model_dump_json()))  # fmt: skip
    view = build_world_view(model, GEO, network=manifest, bundle=bundle)
    after = (sha(model.model_dump_json()), sha(GEO.model_dump_json()),
             sha(manifest.model_dump_json()), sha(bundle.model_dump_json()))  # fmt: skip
    assert before == after
    assert view.world_model_sha256 == before[0] == bundle.world_model_sha256
    assert (view.net_sha256, view.bundle_id) == (manifest.net_sha256, bundle.bundle_id)


def test_the_kind_maps_agree_with_the_ones_the_evidence_bundle_uses() -> None:
    assert KIND_OF_GEOMETRY == reasoning_evidence.KIND_OF_GEOMETRY
    assert KIND_OF_SIGNAL_LABEL == reasoning_evidence.KIND_OF_SIGNAL_LABEL


def test_evidence_kinds_follow_the_source_labels() -> None:
    view = build_world_view(full_model(), GEO, network=network())
    kinds = {e.entity_id: e.evidence_kind for e in view.entities}
    assert kinds["lane:S1"] == "approximated"  # the fixture relabels the S lanes
    assert kinds["lane:N1"] == "assumed"  # synthetic geometry
    assert kinds["movement:SN"] == "approximated"  # the world model's own provenance
    assert kinds["signal_head:N"] == "assumed" and kinds["signal_program:test_cycle"] == "assumed"
    assert {e.evidence_kind for e in view.of_type("conflict_zone")} == {"assumed"}
    assert {e.status for e in view.of_type("conflict_zone")} == {"derived_rule"}


def test_a_relation_is_no_more_certain_than_its_less_certain_end() -> None:
    view = build_world_view(full_model(), GEO, network=network())
    kinds = {e.entity_id: e.evidence_kind for e in view.entities}
    assert view.relations
    for r in view.relations:
        assert r.evidence_kind == weakest(kinds[r.source_id], kinds[r.target_id]), r
    # the case the first version got wrong: an assumed arm inside an approximated junction
    contains = next(r for r in view.relations if r.target_id == "approach:E")
    assert (kinds["approach:E"], kinds[view.entities[0].entity_id], contains.evidence_kind) == (
        "assumed", "approximated", "assumed",
    )  # fmt: skip
    fields = view.model_dump(mode="json")
    fields["relations"][0]["evidence_kind"] = "observed"
    fields["view_sha256"] = WorldView.digest_of(fields)
    with pytest.raises(ValidationError, match="less certain end"):
        WorldView.model_validate(fields)


def test_uncertainty_travels_with_the_value() -> None:
    model = full_model()
    view = build_world_view(model, GEO)
    low, high = model.movements[0].flow_ci95_veh_per_h
    sn = view.entity("movement:SN")
    assert sn.uncertainty is not None and sn.uncertainty.interval95 == (low, high)
    queue = view.entity("queue:S1")
    assert queue.status == "censored" and queue.uncertainty is not None
    assert queue.uncertainty.lower_bound and queue.attributes["censored_samples"] == 1
    assert queue.attributes["max_queued_veh"] == 4 and queue.attributes["end_of_red_samples"] == 1
    calm = model.model_copy(update={"queues": (queue_sample(1, 2),), "end_of_red_queues": ()})
    quiet = build_world_view(calm, GEO).entity("queue:S1")
    assert quiet.status == "present" and quiet.uncertainty is not None
    assert not quiet.uncertainty.lower_bound


def test_a_lane_seen_only_at_the_end_of_red_still_has_a_queue_entity() -> None:
    model = full_model().model_copy(
        update={"end_of_red_queues": (queue_sample(12, 3, lane_id="S2"),)}
    )
    view = build_world_view(model, GEO)
    assert view.entity("queue:S2").attributes["samples"] == 0
    assert view.entity("queue:S2").attributes["end_of_red_samples"] == 1
    empty = world_model(GEO)
    reasons = [a.reason for a in build_world_view(empty, GEO).absent if a.layer == "queues"]
    assert reasons == ["the snapshot holds no queue samples"]


# --- absence, ambiguity, caveats ---------------------------------------------------------------


def test_a_layer_the_snapshot_lacks_is_listed_not_dropped() -> None:
    view = build_world_view(world_model(GEO), GEO)
    assert {a.layer for a in view.absent} == {
        "camera",
        "signal_timeline",
        "signal_program",
        "queues",
    }
    assert not view.of_type("signal_head") and not view.of_type("queue")
    assert not [r for r in view.relations if r.relation == "controlled_by"]
    full = build_world_view(full_model(), GEO, network=network())
    assert {a.layer for a in full.absent} == {"camera"}


def test_a_movement_nobody_took_is_unobserved_not_absent() -> None:
    model = world_model(GEO, counts={("S", "N"): ["car"], ("N", "S"): []})
    view = build_world_view(model, GEO)
    assert view.entity("movement:NS").status == "unobserved"
    assert view.entity("movement:NS").attributes["count_veh"] == 0
    assert view.entity("movement:SN").status == "present"


def test_tracks_that_crossed_one_gate_are_ambiguities_not_counts() -> None:
    odd = AmbiguousMovement(track_id="car:t99", approach="S", exit_arm=None, reason="one gate only")
    model = world_model(GEO).model_copy(update={"ambiguous": (odd,)})
    view = build_world_view(model, GEO)
    assert [(a.track_id, a.exit_arm) for a in view.ambiguities] == [("car:t99", None)]
    assert view.entity("intersection:synthetic-4arm").attributes["ambiguous_tracks"] == 1
    assert sum(e.attributes["count_veh"] for e in view.of_type("movement")) == sum(
        m.count_veh for m in world_model(GEO).movements
    )


def test_signal_notes_survive_as_absences() -> None:
    model = full_model().model_copy(
        update={"signal_notes": ("red end at 52 s could not be sampled",)}
    )
    reasons = [
        a.reason for a in build_world_view(model, GEO).absent if a.layer == "signal_timeline"
    ]
    assert reasons == ["note: red end at 52 s could not be sampled"]


# --- signals: left heads, missing heads, u-turns -----------------------------------------------


def test_a_protected_left_head_controls_the_left_turn_and_only_it() -> None:
    model = full_model().model_copy(update={"signal_timeline": left_head_timeline()})
    view = build_world_view(model, GEO)
    assert "signal_head:S:left" in {e.entity_id for e in view.entities}
    assert view.related("movement:SW", "controlled_by") == ("signal_head:S:left",)  # S to W: left
    assert view.related("movement:SN", "controlled_by") == ("signal_head:S",)
    assert view.related("movement:SE", "controlled_by") == ("signal_head:S",)
    assert view.related("movement:NW", "controlled_by") == ("signal_head:N",)  # N has no left head
    assert view.entity("signal_head:S:left").attributes["green_s"] == 10.0


def test_a_movement_from_an_arm_with_no_head_is_uncontrolled_and_said_so() -> None:
    model = full_model().model_copy(update={"signal_timeline": left_head_timeline("NE")})
    view = build_world_view(model, GEO)
    assert not view.related("movement:WE", "controlled_by")
    assert "the timeline has no head for arm W" in [a.reason for a in view.absent]
    assert sum(a.reason == "the timeline has no head for arm W" for a in view.absent) == 1


def test_a_u_turn_gets_no_head_and_no_conflict_zone_and_the_view_says_so() -> None:
    base = world_model(GEO)
    u_turn = base.movements[0].model_copy(update={"exit_arm": "S", "turn": "u_turn"})
    model = full_model().model_copy(update={"movements": (*base.movements, u_turn)})
    view = build_world_view(model, GEO)
    assert not view.related("movement:SS", "controlled_by")
    assert not [
        r for r in view.relations if r.target_id == "movement:SS" and r.relation == "involves"
    ]
    reasons = {(a.layer, a.reason) for a in view.absent}
    assert ("conflict_zones", "movement SS: a u-turn has no chord") in reasons
    assert ("signal_timeline", "movement SS is a u-turn: no head is assigned") in reasons


def test_conflict_zones_are_derived_by_a_stated_rule_and_say_so() -> None:
    view = build_world_view(world_model(GEO), GEO)
    crossing = view.entity("conflict_zone:SN|WE")
    assert crossing.attributes == {"conflict_type": "crossing", "x_m": 3.5, "y_m": -3.5}
    assert view.related("conflict_zone:SN|WE", "involves") == ("movement:SN", "movement:WE")
    merging = view.entity("conflict_zone:NE|SE")
    assert merging.attributes["conflict_type"] == "merging"
    assert (merging.attributes["x_m"], merging.attributes["y_m"]) == (10.0, -3.5)
    assert crossing.uncertainty is not None and "not a surveyed" in (
        crossing.uncertainty.note or ""
    )
    ids = [e.entity_id for e in view.of_type("conflict_zone")]
    assert "conflict_zone:NS|SN" not in ids and "conflict_zone:NS|WE" in ids  # parallel: none
    assert all(a.split(":")[1].split("|")[0] < a.split("|")[1] for a in ids)


def test_lanes_allow_the_movements_their_markings_name() -> None:
    base = world_model(GEO)
    marked = base.movements[0].model_copy(update={"marked_lane_ids": ("S1",)})
    view = build_world_view(
        base.model_copy(update={"movements": (marked, *base.movements[1:])}), GEO
    )
    assert view.related("lane:S1", "allows") == ("movement:SN",)
    ghost = base.movements[0].model_copy(update={"marked_lane_ids": ("S9",)})
    with pytest.raises(WorldViewError, match="names lane S9, not in the geometry"):
        build_world_view(base.model_copy(update={"movements": (ghost, *base.movements[1:])}), GEO)


def test_an_arm_with_only_exit_lanes_and_no_gate_still_projects() -> None:
    exit_lane = lane("Ex1", "E", [(10, -7), (30, -7), (30, 0), (10, 0)], (), "exit")
    gates = tuple(g for g in GEO.gates if g.arm != "E")
    geometry = GEO.model_copy(update={"lanes": (*GEO.lanes, exit_lane), "gates": gates})
    view = build_world_view(world_model(geometry), geometry)
    arm = view.entity("approach:E")
    assert (arm.evidence_kind, arm.attributes["has_exit_line"]) == ("assumed", False)
    assert ("conflict_zones", "movement SE: arm E has no exit line") in {
        (a.layer, a.reason) for a in view.absent
    }


# --- evidence ----------------------------------------------------------------------------------


def test_evidence_items_map_to_the_entities_their_ids_name() -> None:
    model, manifest = full_model(), network()
    bundle = build_evidence_bundle(model, manifest)
    view = build_world_view(model, GEO, network=manifest, bundle=bundle)
    mapped = {e.evidence_id: e.entity_ids for e in view.evidence}
    assert mapped["mv.SN"] == ("movement:SN",)
    assert mapped["q.S1"] == ("queue:S1",)
    assert mapped["qr.S1.f12"] == ("queue:S1",)
    assert mapped["sig.program"] == ("signal_program:test_cycle",)
    assert mapped["amb.count"] == ("intersection:synthetic-4arm",)
    assert view.unmapped_evidence_ids == ()
    assert view.bundle_id == bundle.bundle_id
    bare = build_world_view(model, GEO, bundle=bundle)  # no network: its program is not in the view
    assert dict((e.evidence_id, e.entity_ids) for e in bare.evidence)["sig.program"] == ()
    assert bare.unmapped_evidence_ids == ("sig.program",)
    note = next(e.note for e in bare.evidence if e.evidence_id == "sig.program")
    assert note and "no compiled network" in note


def test_an_id_that_could_name_two_lanes_maps_to_both_and_says_so() -> None:
    """`qr.S1.f12` is lane S1 at frame 12, or lane S1.f12 itself; both lanes have queues here."""
    extra = lane("S1.f12", "S", [(7, -35), (10.5, -35), (10.5, -10), (7, -10)], ("through",))
    geometry = GEO.model_copy(update={"lanes": (*GEO.lanes, extra)})
    model = full_model(geometry).model_copy(
        update={"queues": (queue_sample(1, 2), queue_sample(1, 1, lane_id="S1.f12"))}
    )
    bundle = build_evidence_bundle(model, network(geometry))
    refs = {e.evidence_id: e for e in build_world_view(model, geometry, bundle=bundle).evidence}
    assert set(refs["qr.S1.f12"].entity_ids) == {"queue:S1", "queue:S1.f12"}
    assert "ambiguous id" in (refs["qr.S1.f12"].note or "")
    assert refs["q.S1.f12"].entity_ids == ("queue:S1.f12",)


# --- records that must agree, and ids that must be valid ---------------------------------------


def test_records_that_do_not_describe_one_snapshot_are_refused() -> None:
    model, manifest = full_model(), network()
    bundle = build_evidence_bundle(model, manifest)
    other = model.model_copy(update={"window_s": 61.0})
    with pytest.raises(WorldViewError, match="another world model snapshot"):
        build_world_view(other, GEO, bundle=bundle)
    foreign = manifest.model_copy(update={"geometry_sha256": "e" * 64})
    with pytest.raises(WorldViewError, match="not compiled from"):
        build_world_view(model, GEO, network=foreign)
    simulated = model.model_copy(
        update={"provenance": model.provenance.model_copy(update={"kind": "simulated"})}
    )
    with pytest.raises(WorldViewError, match="no simulated evidence"):
        build_world_view(simulated, GEO)


def test_the_geometry_must_match_in_content_not_only_in_name() -> None:
    model = full_model()
    lanes = (GEO.lanes[0].model_copy(update={"provenance": "measured"}), *GEO.lanes[1:])
    edited = GEO.model_copy(update={"lanes": lanes})  # same geometry_id, other content
    assert edited.geometry_id == GEO.geometry_id
    with pytest.raises(WorldViewError, match="not measured on this geometry"):
        build_world_view(model, edited)
    renamed = GEO.model_copy(update={"geometry_id": "other"})
    with pytest.raises(WorldViewError, match="not measured on this geometry"):
        build_world_view(model, renamed)


def test_a_bundle_must_be_about_this_network_and_this_clip() -> None:
    model, manifest = full_model(), network()
    bundle = build_evidence_bundle(model, manifest)
    with pytest.raises(WorldViewError, match="built from another network"):
        build_world_view(model, GEO, network=network(net_sha="e" * 64), bundle=bundle)
    with pytest.raises(WorldViewError, match="built from another network"):
        build_world_view(model, GEO, network=network(program_id="other_prog"), bundle=bundle)
    stale = bundle.model_copy(update={"clip_id": "elsewhere"})
    with pytest.raises(WorldViewError, match="another world model snapshot"):
        build_world_view(model, GEO, bundle=stale)


def test_an_id_that_is_not_a_valid_stable_id_is_a_world_view_error() -> None:
    spaced = GEO.model_copy(update={"geometry_id": "my junction"})
    with pytest.raises(WorldViewError, match="cannot be a stable id"):
        build_world_view(world_model(spaced), spaced)
    bad_lane = full_model().model_copy(update={"queues": (queue_sample(1, 1, lane_id="S 1"),)})
    with pytest.raises(WorldViewError, match="queue samples on lanes the geometry lacks"):
        build_world_view(bad_lane, GEO)


def test_the_same_entity_twice_in_the_sources_is_refused_not_merged() -> None:
    model = world_model(GEO)
    twice = model.model_copy(update={"movements": (*model.movements, model.movements[0])})
    with pytest.raises(WorldViewError, match="movement:SN appears twice"):
        build_world_view(twice, GEO)


def test_a_queue_on_a_lane_the_geometry_lacks_is_refused() -> None:
    ghost = full_model().model_copy(update={"queues": (queue_sample(1, 1, lane_id="ZZ9"),)})
    with pytest.raises(WorldViewError, match=r"\['ZZ9'\]"):
        build_world_view(ghost, GEO)


def test_a_view_cannot_be_edited_into_an_inconsistent_one() -> None:
    view = build_world_view(full_model(), GEO)
    fields = view.model_dump(mode="json")
    stale = {**fields, "clip_id": "someone-else"}
    with pytest.raises(ValidationError, match="does not hash"):
        WorldView.model_validate(stale)
    dangling = {**fields, "relations": [*fields["relations"],
                {**fields["relations"][0], "target_id": "lane:nowhere"}]}  # fmt: skip
    dangling["view_sha256"] = WorldView.digest_of(dangling)
    with pytest.raises(ValidationError, match="unknown end"):
        WorldView.model_validate(dangling)
    with pytest.raises(KeyError):
        view.entity("lane:nope")


# --- the committed camera layer on the geometry it was measured on -----------------------------

CAMERA_LAYER = (
    REPO / "experiments/records/ngsim-cam2-world-model-v3-20261030T230506Z/camera_layer.json"
)


@pytest.mark.skipif(
    not (CAMERA_LAYER.is_file() and GEOMETRY_FILE.is_file()), reason="no committed camera layer"
)
def test_a_camera_layer_projects_with_its_caveats_and_its_lane_links() -> None:
    """The committed layer (NGSIM camera 2) on the committed geometry it was measured on, with
    made-up counts, so this runs on a fresh clone."""
    layer = CameraLayer.model_validate_json(CAMERA_LAYER.read_text(encoding="utf-8"))
    geometry = IntersectionGeometry.model_validate_json(GEOMETRY_FILE.read_text(encoding="utf-8"))
    model = world_model(GEO).model_copy(
        update={  # the made-up counts, anchored as this geometry is (a ground dataset)
            "geometry_id": geometry.geometry_id,
            "geometry_sha256": sha(geometry.model_dump_json()),
            "calibration_spec_sha256": None,
            "ground_source_sha256": geometry.ground_source_sha256,
            "camera": layer,
        }
    )
    bundle = build_evidence_bundle(model, network(geometry))
    view = build_world_view(model, geometry, bundle=bundle)
    (cam,) = view.of_type("camera_layer")
    assert cam.attributes["calibration_accepted"] is layer.calibration_accepted
    assert (
        cam.attributes["gate_outcome"] == "go" and cam.uncertainty and cam.uncertainty.lower_bound
    )
    tracks = view.of_type("camera_track")
    assert len(tracks) == len(layer.tracks) > 0
    assert all(t.uncertainty and t.uncertainty.lower_bound for t in tracks)
    placed = [t for t in layer.tracks if t.lane_id]
    assert len([r for r in view.relations if r.relation == "observed_in"]) == len(placed) > 0
    assert len(view.related(cam.entity_id, "contains")) == len(tracks)
    mapped = {e.evidence_id: e.entity_ids for e in view.evidence}
    lane_items = [i for i in mapped if i.startswith("cam.lane.")]
    assert lane_items and all(mapped[i] == (f"lane:{i[len('cam.lane.') :]}",) for i in lane_items)
    assert mapped["cam.gate"] == (cam.entity_id,)
    assert "camera" not in {a.layer for a in view.absent}


@pytest.mark.skipif(not CAMERA_LAYER.is_file(), reason="no committed camera layer")
def test_a_camera_layer_of_another_geometry_is_refused_not_linked_by_name() -> None:
    """Some of the layer's lane ids (S1, S2) are the synthetic junction's too; linking by name
    alone would draw relations to lanes the camera never saw, so any lane the geometry lacks
    refuses the layer."""
    layer = CameraLayer.model_validate_json(CAMERA_LAYER.read_text(encoding="utf-8"))
    attached = world_model(GEO).model_copy(update={"camera": layer})
    with pytest.raises(WorldViewError, match="camera tracks name lanes the geometry lacks"):
        build_world_view(attached, GEO)


# --- the real snapshots ------------------------------------------------------------------------

SNAPSHOT = REPO / "data/processed/ngsim-int2/20261028T074304Z/world_model.json"
CAMERA_SNAPSHOT = REPO / "experiments/runs/cairoflow-20261030T235016Z-r1/world_model.json"
MUSE_REQUEST = (
    REPO / "cache/muse/ddfb8029b1e8810e65b2ad986e0da99be630b86e6d7c86669007e2a197ef47ad.json"
)


def _load(path: Path) -> tuple[TrafficWorldModel, IntersectionGeometry]:
    model = TrafficWorldModel.model_validate_json(path.read_text(encoding="utf-8"))
    geometry = IntersectionGeometry.model_validate_json(GEOMETRY_FILE.read_text(encoding="utf-8"))
    return model, geometry


@pytest.mark.skipif(not (SNAPSHOT.is_file() and GEOMETRY_FILE.is_file()), reason="Git-ignored")
def test_the_real_snapshot_projects_without_touching_its_hash() -> None:
    model, geometry = _load(SNAPSHOT)
    before = sha(model.model_dump_json())
    view = build_world_view(model, geometry)
    assert sha(model.model_dump_json()) == before == view.world_model_sha256
    assert len(view.of_type("movement")) == len(model.movements) == 12
    assert {a.layer for a in view.absent} >= {"camera", "signal_program"}
    assert view.of_type("queue") and view.of_type("signal_head") and view.of_type("conflict_zone")
    assert {"signal_head:N:left", "signal_head:S:left"} <= {e.entity_id for e in view.entities}
    censored = [q for q in view.of_type("queue") if q.status == "censored"]
    assert censored and all(q.uncertainty and q.uncertainty.lower_bound for q in censored)


@pytest.mark.skipif(
    not (CAMERA_SNAPSHOT.is_file() and GEOMETRY_FILE.is_file()),
    reason="run directory is Git-ignored",
)
def test_the_camera_aware_snapshot_keeps_its_camera_tracks_as_lower_bounds() -> None:
    model, geometry = _load(CAMERA_SNAPSHOT)
    assert model.camera is not None
    view = build_world_view(model, geometry)
    tracks = view.of_type("camera_track")
    assert len(tracks) == len(model.camera.tracks) > 0
    assert all(t.uncertainty and t.uncertainty.lower_bound for t in tracks)
    assert len(view.of_type("camera_layer")) == 1 and "camera" not in {a.layer for a in view.absent}


@pytest.mark.skipif(
    not (SNAPSHOT.is_file() and GEOMETRY_FILE.is_file() and MUSE_REQUEST.is_file()),
    reason="snapshot or the committed Muse request is absent",
)
def test_the_committed_muse_bundle_maps_onto_the_snapshot_it_was_built_from() -> None:
    record = json.loads(MUSE_REQUEST.read_text(encoding="utf-8"))
    _, _, evidence = record["body"]["input"].partition("\nEVIDENCE (JSON):\n")
    bundle = EvidenceBundle.model_validate_json(evidence)
    model, geometry = _load(SNAPSHOT)
    view = build_world_view(model, geometry, bundle=bundle)  # the tie is checked, not assumed
    assert len(view.evidence) == len(bundle.items) and view.bundle_id == bundle.bundle_id
    by_id = {e.evidence_id: e for e in view.evidence}
    movement_items = [i for i in by_id if i.startswith("mv.")]
    assert len(movement_items) == 12 and all(by_id[i].entity_ids for i in movement_items)
    unmapped = set(view.unmapped_evidence_ids)
    assert "sig.program" in unmapped and all(i.startswith(("net.", "sig.")) for i in unmapped)
    assert len(view.evidence) - len(unmapped) > 12
