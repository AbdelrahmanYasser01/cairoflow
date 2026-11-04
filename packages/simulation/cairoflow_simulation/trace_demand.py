"""Demand from the observed vehicles themselves, for a replay baseline (ADR-022).

`demand.generate_demand` draws Poisson departures at the observed flows, so it loses how vehicles
actually arrive (platoons from the upstream signal, DATA_REGISTER.md §17.4). Here every vehicle the
world model counted enters SUMO from an observed state inside the replay window [t0, t1) (clip
seconds; simulation time is clip time - t0). Positions keep the vehicle's distance to the drawn
stop line, measured from SUMO's stop line (SUMO puts its stop lines 5-6.5 m from the drawn ones,
§16). Lanes are the observed lane just before the stop line when SUMO connects it to the
vehicle's exit, otherwise SUMO picks (`best`). Three entries:

- `initial`: on its approach at t0. It enters at 0 where it was. Each lane's initial vehicles are
  re-packed, in their observed order, so no two are closer than SUMO's length + minGap (the
  observed jam spacing, 7.4 m, is tighter than SUMO's 7.5 m for a car). A stopped one
  (below `stopped_below_m_s`) enters at rest, a moving one at SUMO's safe speed (`max`).
- `observed`: first observed later and at least `upstream_within_m` from the stop line
  (Lankershim, about 105-110 m out). It enters there and then, at SUMO's safe speed.
- `upstream`: first observed later but closer than that (the cross streets, 10-25 m out, inside
  their queues: the observed time is close to a discharge from an unobserved queue, not an
  arrival). It enters at the start of its approach, timed to reach the observed spot at the
  speed limit if nothing stopped it.

- `discharge` (with the signal timeline, `discharge_anchored`): an `upstream` entry whose stop-line
  crossing falls in a green of its arm joins the queue of the red before that green instead:
  per green, in observed crossing order, `arrival_gap_s` apart, so SUMO discharges it in the
  green it really used (DATA_REGISTER.md §19).
- `queued` (with `queued_at_start`): a `discharge` arrival at or before t0 (a green already on
  when the window opens leaves no red to queue in) was in the queue then. It enters at rest at 0
  at the stop line, re-packed behind its lane's initial vehicles in crossing order (§21).

Plain `upstream` timing lags west vehicles by about a cycle (median +86 to +168 s in the paired
validation, §18), because their first observation is already near their discharge; entering
them where first seen (`upstream_within_m` = 0) blocks their insertion for up to 202 s.

Excluded and counted in the notes: vehicles that crossed the stop line before t0, and vehicles
first observed at or after t1. Every seed has the same vehicles at the same times; only the
speed factor (as in demand.py) and SUMO's own randomness differ, so seed spread measures the
model's microscopic noise for one observed realisation, not the uncertainty of the demand.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

import numpy as np
import sumolib
from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.evidence import SignalTimeline, TrafficWorldModel
from cairoflow_contracts.experiment import DemandFile, DemandManifest, MovementDemand, RunSettings
from cairoflow_simulation.demand import (
    VEHICLE_TYPE_OF_CLASS,
    VEHICLE_TYPES,
    DemandError,
    DemandOptions,
)
from cairoflow_simulation.geometry_compiler import ARM_VECTORS, CompiledNetwork

VEHICLES_FILE = "trace_vehicles.json"
STOP_MARGIN_M = 0.1  # a vehicle first seen past the stop line enters this far before SUMO's
Entry = Literal["initial", "observed", "upstream", "discharge", "queued"]
QUEUED_NOTE = (
    "assumed: one whose queue arrival falls at or before the window start was queued then: it "
    "enters at rest at 0 s at the stop line, behind its lane's initial vehicles in crossing order "
    "(`queued` entries)"
)


class TraceOptions(BaseModel):
    """The two thresholds of the entry rules (both analysis choices, recorded in the notes)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    upstream_within_m: float = Field(
        40.0, ge=0, description="a later first observation closer to the stop line enters upstream"
    )
    discharge_anchored: bool = Field(
        True, description="upstream entries join the queue of the red before their observed green"
    )
    arrival_gap_s: float = Field(
        2.0, gt=0, description="spacing of those arrivals, in observed crossing order"
    )
    queued_at_start: bool = Field(
        True, description="an anchored arrival at or before the window start enters queued at 0"
    )
    stopped_below_m_s: float = Field(
        1.0, ge=0, description="an initial vehicle this slow is at rest"
    )


@dataclass(frozen=True)
class TraceVehicle:
    """One observed vehicle as it enters SUMO."""

    vehicle_id: str
    track_id: str
    approach: str
    exit_arm: str
    vehicle_type: str
    entry: Entry
    depart_s: float  # simulation time
    depart_pos_m: float  # along the SUMO approach lane
    depart_lane: str  # SUMO lane index or 'best'
    depart_speed: str  # '0' (at rest) or 'max' (SUMO's safe speed)
    observed_time_s: float  # clip time of the observed state it comes from
    observed_to_stop_m: float
    observed_speed_m_s: float | None
    observed_lane_id: str | None
    stop_line_time_s: float  # clip time it crossed the stop line (world model)


def _parameters(network: CompiledNetwork) -> dict[str, float]:
    return {p.name: p.value for p in network.manifest.parameters}


def _vehicle_type(track_id: str) -> str:
    return VEHICLE_TYPE_OF_CLASS.get(track_id.split(":", 1)[0], "passenger")


def _type_mix(track_ids: list[str]) -> dict[str, float]:
    """SUMO vType -> share of the tracks, as in demand.py."""
    counts = Counter(_vehicle_type(t) for t in track_ids)
    return {name: round(count / len(track_ids), 6) for name, count in sorted(counts.items())}


def _space(vehicle_type: str) -> float:
    """Length + minGap (m) of a SUMO vType in demand.VEHICLE_TYPES."""
    found = dict(re.findall(r'(length|minGap)="([0-9.]+)"', VEHICLE_TYPES[vehicle_type]))
    return float(found["length"]) + float(found["minGap"])


def _queue_order(v: TraceVehicle) -> tuple[bool, float]:
    """Initial vehicles in their observed order from the stop line, then queued ones by crossing."""
    return v.entry == "queued", -v.depart_pos_m if v.entry == "initial" else v.stop_line_time_s


def repack(vehicles: list[TraceVehicle]) -> list[TraceVehicle]:
    """Initial and queued vehicles moved back, lane by lane in `_queue_order`, until each is at
    least its leader's length + minGap behind it; others unchanged."""
    lanes: dict[tuple[str, str], list[TraceVehicle]] = defaultdict(list)
    for v in vehicles:
        if v.entry in ("initial", "queued") and v.depart_lane != "best":
            lanes[(v.approach, v.depart_lane)].append(v)
    moved: dict[str, TraceVehicle] = {}
    for queue in lanes.values():
        leader: TraceVehicle | None = None
        for v in sorted(queue, key=_queue_order):
            if leader is not None:
                limit = round(leader.depart_pos_m - _space(leader.vehicle_type), 2)
                if v.depart_pos_m > limit:
                    v = replace(v, depart_pos_m=max(limit, 0.0))
                    moved[v.vehicle_id] = v
            leader = v
    return [moved.get(v.vehicle_id, v) for v in vehicles]


def serving_green(
    timeline: SignalTimeline, arm: str, time_s: float
) -> tuple[float, float | None] | None:
    """(onset, start of the red before it) of the arm's `all` green, with its yellow, that
    `time_s` falls in; None outside every green and yellow (e.g. a right turn on red)."""
    own = [i for i in timeline.intervals if i.arm == arm and i.head == "all"]
    for k, interval in enumerate(own):
        if interval.state != "green":
            continue
        after = own[k + 1] if k + 1 < len(own) else None
        end = after.end_s if after is not None and after.state == "yellow" else interval.end_s
        if interval.start_s <= time_s < end:
            red = own[k - 1].start_s if k and own[k - 1].state == "red" else None
            return interval.start_s, red
    return None


def anchor_to_discharge(
    vehicles: list[TraceVehicle],
    timeline: SignalTimeline,
    t0: float,
    limit: float,
    lengths: dict[str, float],
    options: TraceOptions,
) -> list[TraceVehicle]:
    """`upstream` entries moved so that they join the queue of the red before the green they
    crossed in: per arm and green, in observed crossing order, `arrival_gap_s` apart and ending
    one gap before the onset (squeezed into the red when it is shorter). Each enters at the start
    of its approach, timed to reach the stop line then at the speed limit. With `queued_at_start`,
    an arrival at or before t0 in a known lane is `queued` instead: at rest at 0 at the stop line,
    for `repack` to place. Entries whose crossing falls in no green stay `upstream`."""
    groups: dict[tuple[str, float, float | None], list[TraceVehicle]] = defaultdict(list)
    for v in vehicles:
        if v.entry == "upstream":
            served = serving_green(timeline, v.approach, v.stop_line_time_s)
            if served is not None:
                groups[(v.approach, *served)].append(v)
    moved: dict[str, TraceVehicle] = {}
    for (arm, onset, red), queue in groups.items():
        queue.sort(key=lambda v: (v.stop_line_time_s, v.vehicle_id))
        n = len(queue)
        earliest = red if red is not None else onset - n * options.arrival_gap_s
        for k, v in enumerate(queue):
            arrival = onset - (n - k) * options.arrival_gap_s
            if arrival < earliest:  # the red is too short for the gaps: spread them over it
                arrival = earliest + (k + 1) * (onset - earliest) / (n + 1)
            if options.queued_at_start and arrival <= t0 and v.depart_lane != "best":
                moved[v.vehicle_id] = replace(
                    v, entry="queued", depart_s=0.0, depart_speed="0",
                    depart_pos_m=round(lengths[arm] - STOP_MARGIN_M, 2),
                )  # fmt: skip
                continue
            depart = max(arrival - lengths[arm] / limit - t0, 0.0)
            moved[v.vehicle_id] = replace(
                v, entry="discharge", depart_s=round(depart, 1), depart_pos_m=0.0
            )
    return [moved.get(v.vehicle_id, v) for v in vehicles]


def trace_vehicles(
    world_model: TrafficWorldModel,
    network: CompiledNetwork,
    window_s: tuple[float, float],
    options: TraceOptions | None = None,
    timeline: SignalTimeline | None = None,
) -> tuple[list[TraceVehicle], Counter[str]]:
    """The vehicles that enter SUMO, in departure order, and the excluded ones by reason."""
    options = options or TraceOptions()
    t0, t1 = window_s
    params = _parameters(network)
    limit = params["speed_limit"]
    net = sumolib.net.readNet(str(network.net_path))
    tracks = {t.track_id: t for t in world_model.ground_tracks}
    excluded: Counter[str] = Counter()
    vehicles: list[TraceVehicle] = []
    lengths: dict[str, float] = {}
    for movement in world_model.movements:
        arm, exit_arm = movement.approach, movement.exit_arm
        if movement.turn == "u_turn":
            excluded["u-turn (not built)"] += len(movement.tracks)
            continue
        edge = net.getEdge(f"{arm}_in")
        length = edge.getLane(0).getLength()
        lengths[arm] = length
        drawn = params[f"{arm}.stop_line_distance_drawn"]
        serving = {
            lane.getIndex()
            for lane in edge.getLanes()
            for c in lane.getOutgoing()
            if c.getToLane().getEdge().getID() == f"{exit_arm}_out"
        }
        for n, counted in enumerate(movement.tracks):
            if counted.stop_line_time_s < t0:
                excluded["crossed the stop line before the window"] += 1
                continue
            points = [p for p in tracks[counted.track_id].points if p.frame.timestamp_s >= t0]
            if not points or points[0].frame.timestamp_s >= t1:
                excluded["first observed at or after the window end"] += 1
                continue
            point = points[0]
            seen = point.frame.timestamp_s
            distance = point.position_m[0] * ARM_VECTORS[arm][0] + (
                point.position_m[1] * ARM_VECTORS[arm][1]
            )
            to_stop = max(distance - drawn, STOP_MARGIN_M)
            pos = max(length - to_stop, 0.0)
            speed = point.speed_m_s
            entry: Entry = (
                "initial" if seen == t0
                else "observed" if to_stop >= options.upstream_within_m
                else "upstream"
            )  # fmt: skip
            depart = seen - t0
            if entry == "upstream":  # reach the observed spot at the speed limit if unimpeded
                depart, pos = max(depart - pos / limit, 0.0), 0.0
            at_rest = entry == "initial" and speed is not None and speed < options.stopped_below_m_s
            index = params.get(f"{counted.lane_id}.sumo_lane_index")
            vehicles.append(
                TraceVehicle(
                    vehicle_id=f"{arm}{exit_arm}.{n}",
                    track_id=counted.track_id,
                    approach=arm,
                    exit_arm=exit_arm,
                    vehicle_type=_vehicle_type(counted.track_id),
                    entry=entry,
                    depart_s=round(depart, 1),
                    depart_pos_m=round(pos, 2),
                    depart_lane=(
                        str(int(index)) if index is not None and int(index) in serving else "best"
                    ),
                    depart_speed="0" if at_rest else "max",
                    observed_time_s=seen,
                    observed_to_stop_m=round(to_stop, 2),
                    observed_speed_m_s=speed,
                    observed_lane_id=counted.lane_id,
                    stop_line_time_s=counted.stop_line_time_s,
                )
            )
    if timeline is not None and options.discharge_anchored:
        vehicles = anchor_to_discharge(vehicles, timeline, t0, limit, lengths, options)
    vehicles = repack(vehicles)
    vehicles.sort(key=lambda v: (v.depart_s, v.vehicle_id))
    return vehicles, excluded


def generate_trace_demand(
    world_model: TrafficWorldModel,
    network: CompiledNetwork,
    settings: RunSettings,
    out_dir: Path,
    window_s: tuple[float, float],
    options: DemandOptions | None = None,
    trace: TraceOptions | None = None,
    timeline: SignalTimeline | None = None,
) -> DemandManifest:
    """Write one route file per seed with the observed vehicles (only speed factors differ)."""
    options = options or DemandOptions()
    trace = trace or TraceOptions()
    if network.manifest.geometry_sha256 != world_model.geometry_sha256:
        raise DemandError("the world model was measured on a different geometry")
    t0, t1 = window_s
    if settings.warmup_s != 0 or abs(settings.horizon_s - (t1 - t0)) > 1e-6:
        raise DemandError("a trace baseline measures the replay window itself: warm-up 0, "
                          f"horizon {t1 - t0:g} s")  # fmt: skip
    vehicles, excluded = trace_vehicles(world_model, network, window_s, trace, timeline)
    if not vehicles:
        raise DemandError("no counted vehicle is observed inside the window")
    out_dir.mkdir(parents=True, exist_ok=False)
    table = {"window_s": list(window_s), "options": trace.model_dump(),
             "vehicles": [asdict(v) for v in vehicles]}  # fmt: skip
    text = json.dumps(table, indent=1) + "\n"
    with (out_dir / VEHICLES_FILE).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    types_used = sorted({v.vehicle_type for v in vehicles})
    files: list[DemandFile] = []
    for seed in settings.seeds:
        rows = []
        for position, v in enumerate(vehicles):
            rng = np.random.default_rng([seed, position])
            factor = float(np.clip(rng.normal(1.0, options.speed_factor_sd),
                                   options.speed_factor_min, options.speed_factor_max))  # fmt: skip
            rows.append(
                f'    <vehicle id="{v.vehicle_id}" type="{v.vehicle_type}" '
                f'depart="{v.depart_s:.1f}" departLane="{v.depart_lane}" '
                f'departPos="{v.depart_pos_m:.2f}" departSpeed="{v.depart_speed}" '
                f'speedFactor="{factor:.3f}">\n'
                f'        <route edges="{v.approach}_in {v.exit_arm}_out"/>\n    </vehicle>'
            )
        route = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<routes>\n'
            + "".join(f'    <vType id="{t}" {VEHICLE_TYPES[t]}/>\n' for t in types_used)
            + "\n".join(rows)
            + "\n</routes>\n"
        )
        name = f"seed_{seed}.rou.xml"
        with (out_dir / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(route)
        files.append(
            DemandFile(
                seed=seed,
                file=name,
                sha256=hashlib.sha256(route.encode("utf-8")).hexdigest(),
                vehicles=len(vehicles),
            )
        )
    cohort = Counter(f"{v.approach}{v.exit_arm}" for v in vehicles)
    entries = Counter(v.entry for v in vehicles)
    movements = tuple(
        MovementDemand(
            approach=m.approach,
            exit_arm=m.exit_arm,
            turn=m.turn,
            observed_count_veh=m.count_veh,
            flow_veh_per_h=m.flow_veh_per_h,
            flow_ci95_veh_per_h=m.flow_ci95_veh_per_h,
            vehicle_type_mix=_type_mix([t.track_id for t in m.tracks]),
        )
        for m in world_model.movements
        if m.count_veh and m.turn != "u_turn"
    )
    notes = (
        f"trace demand (ADR-022): the observed vehicles over clip [{t0:g}, {t1:g}) s, simulation "
        "time = clip time - window start; the movements list the world model's whole-clip "
        "counts and flows",
        "vehicles entering per movement: "
        + ", ".join(f"{key} {count}" for key, count in sorted(cohort.items())),
        "entries: " + ", ".join(f"{key} {count}" for key, count in sorted(entries.items())),
        "assumed: "
        + (
            f"a later first observation within {trace.upstream_within_m:g} m of the stop line "
            "enters at the start of its approach, timed to reach that spot at the speed limit; "
            if trace.upstream_within_m
            else "a later vehicle enters where it was first observed (on the cross streets that "
            "is inside the queue, near its discharge); "
        )
        + f"initial vehicles below {trace.stopped_below_m_s:g} m/s enter at rest, re-packed at "
        "SUMO's length + minGap; all others enter at SUMO's safe speed",
        *(
            (
                "assumed: an upstream entry whose crossing falls in a green instead joins the "
                f"queue of the red before it, {trace.arrival_gap_s:g} s apart in observed "
                "crossing order (`discharge` entries)",
                *((QUEUED_NOTE,) if trace.queued_at_start else ()),
            )
            if timeline is not None and trace.discharge_anchored
            else ()
        ),  # fmt: skip
        *(f"excluded: {count} {reason}" for reason, count in sorted(excluded.items())),
        f"{VEHICLES_FILE}: SHA-256 {hashlib.sha256(text.encode('utf-8')).hexdigest()} (vehicle "
        "id -> track id and entry state)",
        "every seed has the same vehicles and entry states; speed factors differ by seed",
    )
    return DemandManifest(
        world_model_sha256=hashlib.sha256(
            world_model.model_dump_json().encode("utf-8")
        ).hexdigest(),
        movements=movements,
        generation_end_s=t1 - t0,
        speed_factor_sd=options.speed_factor_sd,
        files=tuple(files),
        notes=notes,
    )
