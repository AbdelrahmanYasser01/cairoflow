"""Run SUMO baselines: one run per seed, checked for validity and rerun identity.

Each run is the `sumo` binary started in its own new directory with relative paths (SUMO echoes
paths into XML comments; see network.py). A run is valid when nothing teleported or collided
(collisions inside the junction included) and every loaded vehicle was inserted and arrived
before the end (warm-up + horizon + drain). The
statistics file holds wall-clock timings, so reruns are compared on the tripinfo output (XML
comments stripped), which holds every simulated trip.

`run_baseline` compiles the network, writes the demand, runs every seed (and, by default, every
seed again) under one new directory and writes `baseline_manifest.json` there, never overwriting.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import sumo

from cairoflow_contracts.evidence import IntersectionGeometry, SignalTimeline, TrafficWorldModel
from cairoflow_contracts.experiment import BaselineManifest, RunRecord, RunSettings
from cairoflow_simulation.demand import DemandOptions, generate_demand
from cairoflow_simulation.geometry_compiler import (
    CompiledNetwork,
    CompileOptions,
    ReplayOptions,
    compile_network,
)
from cairoflow_simulation.network import content_sha256
from cairoflow_simulation.trace_demand import generate_trace_demand

MANIFEST_FILE = "baseline_manifest.json"


class SimulationError(RuntimeError):
    """SUMO failed to run, or a baseline is not valid."""


def _sumo() -> Path:
    path = Path(sumo.SUMO_HOME) / "bin" / ("sumo.exe" if os.name == "nt" else "sumo")
    if not path.exists():
        raise SimulationError(f"SUMO executable not found: {path}")
    return path


def run_seed(
    network: CompiledNetwork,
    demand_path: Path,
    seed: int,
    settings: RunSettings,
    run_dir: Path,
) -> RunRecord:
    """Run one seed into `run_dir` (new) and check the result.

    Refuses a signal program whose phase durations are not whole multiples of the step length:
    SUMO would round them, and the run would not execute the recorded timing.
    """
    step = settings.step_length_s
    for phase in network.manifest.signal_program.phases:
        if abs(phase.duration_s / step - round(phase.duration_s / step)) > 1e-6:
            raise SimulationError(
                f"phase {phase.name} lasts {phase.duration_s:g} s, not a multiple of the "
                f"{step:g} s step; SUMO would not run the recorded timing"
            )
    run_dir.mkdir(parents=True, exist_ok=False)
    args = (
        "--net-file", os.path.relpath(network.net_path, run_dir),
        "--route-files", os.path.relpath(demand_path, run_dir),
        "--seed", str(seed),
        "--begin", "0",
        "--end", f"{settings.end_s:g}",
        "--step-length", f"{settings.step_length_s:g}",
        "--time-to-teleport", f"{settings.time_to_teleport_s:g}",
        "--tripinfo-output", "tripinfo.xml",
        "--tripinfo-output.write-unfinished", "true",
        "--statistic-output", "statistics.xml",
        "--queue-output", "queue.xml",
        "--collision.action", "warn",
        "--collision.check-junctions", "true",
        "--no-step-log", "true",
        "--duration-log.disable", "true",
    )  # fmt: skip
    result = subprocess.run(
        [str(_sumo()), *args], cwd=run_dir, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SimulationError(f"sumo failed (rc={result.returncode}): {result.stderr[-800:]}")

    stats = ET.parse(run_dir / "statistics.xml").getroot()
    vehicles = stats.find("vehicles")
    teleports = stats.find("teleports")
    safety = stats.find("safety")
    if vehicles is None or teleports is None or safety is None:
        raise SimulationError("statistics.xml lacks vehicles, teleports or safety")
    trips = ET.parse(run_dir / "tripinfo.xml").getroot().findall("tripinfo")
    loaded, inserted = int(vehicles.get("loaded", 0)), int(vehicles.get("inserted", 0))
    arrived = sum(1 for trip in trips if float(trip.get("arrival", -1)) >= 0)
    unfinished = int(vehicles.get("running", 0)) + int(vehicles.get("waiting", 0))
    record = {
        "teleports": int(teleports.get("total", 0)),
        "collisions": int(safety.get("collisions", 0)),
    }
    reasons = [f"{count} {name}" for name, count in record.items() if count]
    if unfinished:
        reasons.append(f"{unfinished} vehicles unfinished at {settings.end_s:g} s")
    if inserted != loaded or arrived != loaded:
        reasons.append(f"loaded {loaded}, inserted {inserted}, arrived {arrived}")
    return RunRecord(
        seed=seed,
        run_dir=run_dir.name,
        command=("sumo", *args),
        demand_sha256=hashlib.sha256(demand_path.read_bytes()).hexdigest(),
        tripinfo_sha256=content_sha256(run_dir / "tripinfo.xml", strip_comments=True),
        loaded=loaded,
        inserted=inserted,
        arrived=arrived,
        unfinished=unfinished,
        valid=not reasons,
        reasons=tuple(reasons),
        **record,
    )


def run_baseline(
    world_model: TrafficWorldModel,
    geometry: IntersectionGeometry,
    signals: SignalTimeline,
    out_dir: Path,
    settings: RunSettings | None = None,
    compile_options: CompileOptions | None = None,
    demand_options: DemandOptions | None = None,
    *,
    verify_reruns: bool = True,
    now: datetime | None = None,
    replay: ReplayOptions | None = None,
) -> BaselineManifest:
    """Compile, generate demand, run every seed and record it all under `out_dir` (new).

    With `replay`, the signals replay the timeline over `replay.window_s` and the demand is the
    observed vehicles over that window (trace_demand.py, ADR-022); `settings` must then measure
    the window itself (warm-up 0, horizon = its length).
    """
    settings = settings or RunSettings()
    now = now or datetime.now(UTC)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists; a baseline is never overwritten")
    if world_model.clip_id != geometry.clip_id or world_model.geometry_id != geometry.geometry_id:
        raise SimulationError("the world model was not measured on this geometry")
    if replay is None:
        network = compile_network(geometry, signals, out_dir / "network", compile_options)
        demand = generate_demand(world_model, network, settings, out_dir / "demand", demand_options)
    else:
        if settings.drain_s > replay.drain_s:
            raise SimulationError("the replay program's drain is shorter than the run's")
        network = compile_network(
            geometry, signals, out_dir / "network", compile_options, replay=replay
        )
        demand = generate_trace_demand(
            world_model, network, settings, out_dir / "demand", replay.window_s, demand_options,
            timeline=signals,
        )  # fmt: skip

    def run_all(suffix: str) -> tuple[RunRecord, ...]:
        return tuple(
            run_seed(
                network,
                out_dir / "demand" / file.file,
                file.seed,
                settings,
                out_dir / "runs" / f"seed_{file.seed}{suffix}",
            )
            for file in demand.files
        )

    runs = run_all("")
    reruns = run_all("_rerun") if verify_reruns else ()
    identical = (
        all(a.tripinfo_sha256 == b.tripinfo_sha256 for a, b in zip(runs, reruns, strict=True))
        if verify_reruns
        else None
    )
    reasons = [f"seed {run.seed}: {'; '.join(run.reasons)}" for run in runs if not run.valid]
    if identical is False:
        reasons.append("a rerun produced different tripinfo output")
    manifest = BaselineManifest(
        experiment_id=f"baseline-{world_model.clip_id}-{now:%Y%m%dT%H%M%SZ}",
        created_at_utc=now,
        world_model_sha256=demand.world_model_sha256,
        settings=settings,
        network=network.manifest,
        demand=demand,
        runs=runs,
        reruns=reruns,
        reruns_identical=identical,
        valid=not reasons,
        reasons=tuple(reasons),
    )
    with (out_dir / MANIFEST_FILE).open("x", encoding="utf-8") as handle:
        handle.write(manifest.model_dump_json(indent=2) + "\n")
    return manifest
