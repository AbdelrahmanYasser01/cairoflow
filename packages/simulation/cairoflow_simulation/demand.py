"""Per-seed demand files from the world model's movement flows (ADR-008).

For each seed and each movement with a non-zero flow, departures are a Poisson process at the
world model's point estimate (veh/h) over [0, warm-up + horizon). Each vehicle's type is drawn
from the classes of the tracks counted in that movement, and its `speedFactor` from a normal
distribution clipped to [0.8, 1.2]. Every draw comes from a generator seeded with (seed,
movement position), so a seed always gives the same file and baseline and candidates share it.

The point estimate is used as is; its interval is recorded so a stress test can use its ends.
Movements counted zero times get no vehicles; counted U-turns are listed in the notes and not
simulated, because the compiled network has no U-turns.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.evidence import TrafficWorldModel
from cairoflow_contracts.experiment import (
    DemandFile,
    DemandManifest,
    MovementDemand,
    RunSettings,
)
from cairoflow_simulation.geometry_compiler import TURN_OF_LETTER, CompiledNetwork

VEHICLE_TYPE_OF_CLASS = {
    "car": "passenger",
    "bus": "bus",
    "truck": "truck",
    "motorcycle": "motorcycle",
}
# SUMO Krauss defaults per class, written out so the file does not depend on SUMO's defaults.
VEHICLE_TYPES = {
    "passenger": 'vClass="passenger" length="5.0" minGap="2.5" accel="2.6" decel="4.5" '
    'sigma="0.5" maxSpeed="55.56"',
    "bus": 'vClass="bus" length="12.0" minGap="2.5" accel="1.2" decel="4.0" sigma="0.5" '
    'maxSpeed="27.78"',
    "truck": 'vClass="truck" length="10.0" minGap="2.5" accel="1.3" decel="4.0" sigma="0.5" '
    'maxSpeed="36.11"',
    "motorcycle": 'vClass="motorcycle" length="2.2" minGap="2.0" accel="3.0" decel="7.0" '
    'sigma="0.5" maxSpeed="55.56"',
}


class DemandError(ValueError):
    """The world model's movements cannot be served by the network."""


class DemandOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    speed_factor_sd: float = Field(0.1, ge=0, le=0.3)
    speed_factor_min: float = Field(0.8, gt=0)
    speed_factor_max: float = Field(1.2, gt=0)


def generate_demand(
    world_model: TrafficWorldModel,
    network: CompiledNetwork,
    settings: RunSettings,
    out_dir: Path,
    options: DemandOptions | None = None,
) -> DemandManifest:
    """Write one route file per seed into `out_dir` (new) and describe them."""
    options = options or DemandOptions()
    if network.manifest.geometry_sha256 != world_model.geometry_sha256:
        raise DemandError("the world model was measured on a different geometry")
    served = {(link.approach, TURN_OF_LETTER[link.turn]) for link in network.links}
    notes: list[str] = []
    movements: list[MovementDemand] = []
    for movement in world_model.movements:
        if movement.count_veh == 0:
            continue
        if movement.turn == "u_turn":
            notes.append(
                f"{movement.approach} u_turn: {movement.count_veh} counted vehicles not simulated "
                "(the network builds no U-turns)"
            )
            continue
        if (movement.approach, movement.turn) not in served:
            raise DemandError(
                f"{movement.approach} {movement.turn} was counted {movement.count_veh} times "
                "but the network has no such movement"
            )
        classes = Counter(track.track_id.split(":", 1)[0] for track in movement.tracks)
        types: Counter[str] = Counter()
        for name, count in classes.items():
            vehicle_type = VEHICLE_TYPE_OF_CLASS.get(name)
            if vehicle_type is None:
                notes.append(f"class {name!r} has no SUMO type; simulated as passenger")
                vehicle_type = "passenger"
            types[vehicle_type] += count
        movements.append(
            MovementDemand(
                approach=movement.approach,
                exit_arm=movement.exit_arm,
                turn=movement.turn,
                observed_count_veh=movement.count_veh,
                flow_veh_per_h=movement.flow_veh_per_h,
                flow_ci95_veh_per_h=movement.flow_ci95_veh_per_h,
                vehicle_type_mix={
                    name: round(types[name] / movement.count_veh, 6) for name in sorted(types)
                },
            )
        )
    if not movements:
        raise DemandError("no movement was counted; there is no demand to simulate")

    out_dir.mkdir(parents=True, exist_ok=False)
    files: list[DemandFile] = []
    end = settings.generation_end_s
    for seed in settings.seeds:
        vehicles: list[tuple[float, str, str]] = []
        for position, movement in enumerate(movements):
            rng = np.random.default_rng([seed, position])
            names = sorted(movement.vehicle_type_mix)
            shares = np.array([movement.vehicle_type_mix[n] for n in names])
            shares = shares / shares.sum()
            mean_headway = 3600.0 / movement.flow_veh_per_h
            route = f"{movement.approach}_in {movement.exit_arm}_out"
            t, n = 0.0, 0
            while True:
                t += float(rng.exponential(mean_headway))
                if round(t, 1) >= end:
                    break
                vehicle_type = names[int(rng.choice(len(names), p=shares))]
                speed_factor = float(
                    np.clip(
                        rng.normal(1.0, options.speed_factor_sd),
                        options.speed_factor_min,
                        options.speed_factor_max,
                    )
                )
                vehicle_id = f"{movement.approach}{movement.exit_arm}.{n}"
                n += 1
                vehicles.append(
                    (
                        round(t, 1),
                        vehicle_id,
                        f'    <vehicle id="{vehicle_id}" type="{vehicle_type}" '
                        f'depart="{round(t, 1):.1f}" departLane="best" departSpeed="max" '
                        f'speedFactor="{speed_factor:.3f}">\n'
                        f'        <route edges="{route}"/>\n    </vehicle>',
                    )
                )
        vehicles.sort(key=lambda v: (v[0], v[1]))
        types_used = sorted({t for m in movements for t in m.vehicle_type_mix})
        text = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<routes>\n'
            + "".join(f'    <vType id="{t}" {VEHICLE_TYPES[t]}/>\n' for t in types_used)
            + "\n".join(v[2] for v in vehicles)
            + "\n</routes>\n"
        )
        name = f"seed_{seed}.rou.xml"
        with (out_dir / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        files.append(
            DemandFile(
                seed=seed,
                file=name,
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                vehicles=len(vehicles),
            )
        )
    return DemandManifest(
        world_model_sha256=hashlib.sha256(
            world_model.model_dump_json().encode("utf-8")
        ).hexdigest(),
        movements=tuple(movements),
        generation_end_s=end,
        speed_factor_sd=options.speed_factor_sd,
        files=tuple(files),
        notes=tuple(dict.fromkeys(notes)),
    )
