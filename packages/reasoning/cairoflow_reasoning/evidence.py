"""Build the evidence bundle Muse Spark reads: facts with ids, written by code, never by a model.

Every item summarises one world-model or network value with its numbers and its evidence kind,
so a hypothesis can cite it and a reviewer can trace it back. Kinds follow the source labels:
geometry `measured` -> observed, `approximated` -> approximated, `synthetic` -> assumed; signal
labels `observed-annotated` and `published` -> observed, `assumed` -> assumed.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from cairoflow_contracts.evidence import EvidenceKind, GeometryProvenance, TrafficWorldModel
from cairoflow_contracts.experiment import CompiledNetworkManifest
from cairoflow_contracts.reasoning import (
    MAX_GREEN_S,
    MIN_GREEN_S,
    EvidenceBundle,
    EvidenceItem,
    PhaseOption,
)

KIND_OF_GEOMETRY: dict[GeometryProvenance, EvidenceKind] = {
    "measured": "observed",
    "approximated": "approximated",
    "synthetic": "assumed",
}
KIND_OF_SIGNAL_LABEL: dict[str, EvidenceKind] = {
    "observed-annotated": "observed",
    "published": "observed",
    "trajectory-derived": "approximated",
    "assumed": "assumed",
}
LIMITATIONS = (
    "Flows are stop-line discharge counts; they equal arrival flows only without a queue at the "
    "start or end of the clip.",
    "Network values labelled assumed are not measured.",
    "No pedestrians are modelled.",
    "Proposals are simulated in SUMO before any claim; state expected effects in words, not "
    "numbers.",
)
# How every bundle names arms (since prompt 0.2.0): an arm is a side of the junction, not a
# heading. The first real diagnosis called arm N's traffic "northbound".
ARM_CONVENTIONS = (
    "Arms N, E, S and W are the sides of the junction. Arm N holds the vehicles arriving from "
    "the north, which travel southbound, and the exit towards the north; likewise E, S and W.",
    "A movement 'N to E (left)' starts on arm N (arriving from the north) and leaves by arm E. "
    "Lanes such as N12 are approach lanes of arm N. Name movements by the arm they come from "
    "(for example 'arriving from the north'), never as 'northbound' for arm N.",
)


class EvidenceError(ValueError):
    """The world model and the network do not describe the same junction."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_evidence_bundle(
    world_model: TrafficWorldModel,
    network: CompiledNetworkManifest,
    limitations: tuple[str, ...] = (),
    *,
    end_of_red_per_lane: bool = False,
    conventions: tuple[str, ...] = ARM_CONVENTIONS,
) -> EvidenceBundle:
    """Summarise the world model and the compiled network as citable evidence items.

    `limitations` are added to the standard ones: what the model must know about where this
    evidence comes from (for example, a signal timeline estimated from trajectories). With
    `end_of_red_per_lane`, the queues at the end of red are one item per lane (median and
    maximum) instead of one per sample, for long windows with many cycles. `conventions` state
    how names are to be read (the arm convention by default); `()` rebuilds a bundle as made
    before conventions existed (prompt 0.1.0).
    """
    if world_model.geometry_sha256 != network.geometry_sha256:
        raise EvidenceError("the world model and the network were built from different geometry")
    window = world_model.window_s
    items: list[EvidenceItem] = []
    for m in world_model.movements:
        low, high = m.flow_ci95_veh_per_h
        lanes = ", ".join(m.marked_lane_ids) or "none (observed only)"
        items.append(
            EvidenceItem(
                evidence_id=f"mv.{m.approach}{m.exit_arm}",
                kind=world_model.provenance.kind,  # approximated when built from tracks
                summary=(
                    f"{m.approach} to {m.exit_arm} ({m.turn}): {m.count_veh} vehicles in "
                    f"{window:g} s = {m.flow_veh_per_h:.0f} veh/h, 95% interval "
                    f"{low:.0f}-{high:.0f} veh/h; lanes marked for it: {lanes}"
                ),
            )
        )
    items.append(
        EvidenceItem(
            evidence_id="amb.count",
            kind="approximated",
            summary=f"{len(world_model.ambiguous)} tracks crossed only one gate and were not "
            "counted in any movement",
        )
    )
    by_lane = defaultdict(list)
    for sample in world_model.queues:
        by_lane[sample.lane_id].append(sample)
    for lane_id in sorted(by_lane):
        samples = by_lane[lane_id]
        longest = max(samples, key=lambda q: (q.length_m, -q.frame.frame_index))
        counts = [q.queued_veh for q in samples]
        censored = sum(q.censored for q in samples)
        items.append(
            EvidenceItem(
                evidence_id=f"q.{lane_id}",
                kind="approximated",
                summary=(
                    f"lane {lane_id}: {min(counts)}-{max(counts)} queued vehicles over "
                    f"{len(samples)} samples (mean {sum(counts) / len(counts):.1f}); longest "
                    f"{longest.length_m:.1f} +/- {longest.length_error_m:.1f} m at "
                    f"{longest.frame.timestamp_s:g} s"
                    + (
                        " (at least: it reaches the end of the observed lane)"
                        if longest.censored
                        else ""
                    )
                    + (
                        f"; {censored} samples reach the end of the observed lane"
                        if censored
                        else ""
                    )
                ),
            )
        )
    if end_of_red_per_lane:
        reds = defaultdict(list)
        for q in world_model.end_of_red_queues:
            reds[q.lane_id].append(q)
        for lane_id in sorted(reds):
            samples = reds[lane_id]
            counts = sorted(q.queued_veh for q in samples)
            lengths = sorted(q.length_m for q in samples)
            censored = sum(q.censored for q in samples)
            items.append(
                EvidenceItem(
                    evidence_id=f"qr.{lane_id}",
                    kind="approximated",
                    summary=(
                        f"lane {lane_id} at the end of each red ({len(samples)} reds): median "
                        f"{counts[len(counts) // 2]} vehicles, max {counts[-1]}; median "
                        f"{lengths[len(lengths) // 2]:.1f} m, max {lengths[-1]:.1f} m"
                        + (
                            f"; {censored} reach the end of the observed lane (at least)"
                            if censored
                            else ""
                        )
                    ),
                )
            )
    else:
        for q in world_model.end_of_red_queues:
            items.append(
                EvidenceItem(
                    evidence_id=f"qr.{q.lane_id}.f{q.frame.frame_index}",
                    kind="approximated",
                    summary=(
                        f"lane {q.lane_id} at the end of red ({q.frame.timestamp_s:g} s): "
                        f"{q.queued_veh} vehicles, {q.length_m:.1f} +/- {q.length_error_m:.1f} m"
                        + (
                            " (at least: it reaches the end of the observed lane)"
                            if q.censored
                            else ""
                        )
                    ),
                )
            )
    for i, note in enumerate(world_model.signal_notes, start=1):
        items.append(EvidenceItem(evidence_id=f"sig.note.{i}", kind="approximated", summary=note))
    if (camera := world_model.camera) is not None:  # only a go gate attaches one (P1-1)
        start, end = camera.window_s
        for lane_id, tracks in camera.lane_presence.items():
            speeds = [p.speed_m_s for t in camera.tracks for p in t.points
                      if p.lane_id == lane_id and p.speed_m_s is not None]  # fmt: skip
            speed = f", mean speed {sum(speeds) / len(speeds):.1f} m/s" if speeds else ""
            items.append(EvidenceItem(
                evidence_id=f"cam.lane.{lane_id}", kind="approximated",
                summary=f"camera-derived ({camera.sam_model} boxes through the camera "
                f"calibration), seconds {start:g}-{end:g}: {tracks} tracked vehicles seen in "
                f"lane {lane_id}{speed}; SAM boxes about a quarter of the vehicles in view, so "
                "this is a lower bound",
            ))  # fmt: skip
        gate = camera.gate
        items.append(EvidenceItem(
            evidence_id="cam.gate", kind="approximated",
            summary=f"camera lane placement against the NGSIM trajectories (gate {gate.gate_id}): "
            + ", ".join(f"{k} {v}" for k, v in gate.metrics.items()
                        if k[:2] in ("A_", "B_", "C_", "D_", "J_")),
        ))  # fmt: skip

    program = network.signal_program
    phases: list[PhaseOption] = []
    for phase in program.phases:
        green = tuple(arm for arm, state in phase.arm_states.items() if state == "green")
        left = tuple(arm for arm, state in phase.left_states.items() if state == "green")
        phases.append(
            PhaseOption(
                phase_name=phase.name,
                duration_s=phase.duration_s,
                green_arms=green,
                green_left_arms=left,
                adjustable=phase.adjustable,
                min_green_s=MIN_GREEN_S if phase.adjustable else None,
                max_green_s=MAX_GREEN_S if phase.adjustable else None,
            )
        )

    def greens(p: PhaseOption) -> str:
        names = [*p.green_arms, *(f"{arm}-left" for arm in p.green_left_arms)]
        if not p.adjustable:
            return "clearance (fixed)" + (f", green {','.join(names)}" if names else "")
        return f"green {','.join(names)}"

    described = "; ".join(f"{p.phase_name} {p.duration_s:g} s {greens(p)}" for p in phases)
    items.append(
        EvidenceItem(
            evidence_id="sig.program",
            kind=KIND_OF_SIGNAL_LABEL[program.label],
            summary=(
                f"simulated cycle {program.cycle_s:g} s, cut from timeline {program.timeline_id} "
                f"({program.label}) at {program.cycle_start_s:g} s: {described}"
            ),
        )
    )
    for p in network.parameters:
        items.append(
            EvidenceItem(
                evidence_id=f"net.{p.name}",
                kind=KIND_OF_GEOMETRY[p.provenance],
                summary=f"{p.name} = {p.value:g} {p.unit} ({p.provenance}: {p.source})",
            )
        )

    draft = EvidenceBundle(
        bundle_id="pending",
        clip_id=world_model.clip_id,
        world_model_sha256=_sha256(world_model.model_dump_json()),
        net_sha256=network.net_sha256,
        program_id=program.program_id,
        phases=tuple(phases),
        items=tuple(items),
        limitations=(
            f"Counts come from a {window:g} s clip; flow intervals are wide.",
            *LIMITATIONS,
            *limitations,
        ),
        conventions=conventions,
    )
    bundle_id = _sha256(draft.model_dump_json(exclude={"bundle_id"}))[:16]
    return draft.model_copy(update={"bundle_id": bundle_id})
