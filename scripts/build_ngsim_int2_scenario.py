"""Build the NGSIM intersection 2 scenario from the published trajectories and run its baseline.

Input: the NGSIM Lankershim trajectories, 08:30-08:45 (DATA_REGISTER.md §13; SHA-256 checked).
No camera input of any kind: no pixels, no calibration, no image registration (ADR-019). Camera 2
stays uncalibrated and outside this world model (ADR-018).

    uv run --frozen python scripts/build_ngsim_int2_scenario.py [--record]
    uv run --frozen python scripts/build_ngsim_int2_scenario.py \
        --as-of 2026-10-28T07:43:04Z --check-record      # rebuild the committed record

`--as-of` sets the time written into every output and used in the two directory names (default:
now). The world model carries that time, and the evidence bundle carries the world model's hash,
so only a build at the recorded time reproduces the recorded request key and can replay the cached
Muse reply. `--check-record` then compares the outputs with the committed record's hashes and
directory names, and exits 1 on any difference.

Writes, never overwriting:
- `data/processed/ngsim-int2/<stamp>/` (Git-ignored): geometry.json, signal_timeline.json,
  world_model.json, derivation.json;
- `experiments/runs/ngsim-int2-baseline-<stamp>/`: the SUMO baseline (network, per-seed demand,
  runs and reruns, baseline_manifest.json);
- with `--record`, the curated record `data/geometry/ngsim-lankershim-int2/` (committed):
  geometry.json, signal_timeline.json and scenario.json. Values derived from the trajectories
  carry CC BY-SA 3.0 (attribution in each file).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import numpy as np

from cairoflow_contracts.experiment import RunSettings
from cairoflow_evaluation.metrics import run_metrics
from cairoflow_perception.ngsim import MOVEMENTS, LocalFrame, kinematics, load_trajectories
from cairoflow_perception.ngsim_junction import (
    WINDOW_START_MS,
    JunctionPolicy,
    SignalPolicy,
    allowed_turns,
    arm_coords,
    arm_statistics,
    build_geometry,
    estimate_signal_timeline,
    ground_series,
    junction_frame,
    junction_rows,
    window_frames,
)
from cairoflow_perception.world_model import (
    WorldModelPolicy,
    build_world_model_from_ground,
    count_movements,
)
from cairoflow_simulation.demand import VEHICLE_TYPES, DemandOptions
from cairoflow_simulation.geometry_compiler import CompileOptions
from cairoflow_simulation.runner import run_baseline

REPO = Path(__file__).resolve().parents[1]
CSV = REPO / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
CSV_SHA256 = "c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e"
ORIGIN_FT = (6452083.0, 1873176.0)  # ADR-017: rounded centroid of the int_id 2 rows
WINDOW_ID = "ngsim-lankershim-int2-0830-0845"
WINDOW_S = 900.0
RECORD_DIR = REPO / "data/geometry/ngsim-lankershim-int2"
LICENCE = {
    "licence": "CC BY-SA 3.0 Unported",
    "attribution": (
        "Derived from U.S. Department of Transportation, Federal Highway Administration, Next "
        "Generation Simulation (NGSIM) Lankershim Boulevard vehicle trajectories, "
        "data.transportation.gov dataset 8ect-6jqj (DOI 10.21949/1504477)"
    ),
    "changes": "aggregated by CairoFlow's fixed rules (cairoflow_perception.ngsim_junction)",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new(path: Path, text: str) -> str:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_as_of(text: str) -> datetime:
    """An ISO 8601 time with a UTC offset (`Z` allowed), as UTC; a time without one is refused."""
    try:
        value = datetime.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 time: {text!r}") from error
    if value.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{text!r} has no UTC offset; add Z or +00:00")
    return value.astimezone(UTC)


def check_record(
    record: dict, root: Path, processed_dir: Path, baseline_dir: Path | None
) -> list[str]:
    """Every committed hash or directory name the new outputs do not reproduce (empty: none)."""
    hint = f"build with --as-of {record['created_at_utc']}"
    problems: list[str] = []
    recorded = record["processed_outputs"]
    if processed_dir.relative_to(root).as_posix() != recorded["dir"]:
        problems.append(
            f"{processed_dir.relative_to(root).as_posix()} is not the recorded {recorded['dir']} "
            f"({hint})"
        )
    for name, want in sorted(recorded["sha256"].items()):
        path = processed_dir / name
        got = sha256_file(path) if path.is_file() else None
        if got != want:
            problems.append(f"{name}: {(got or 'missing')[:12]}, recorded {want[:12]}")
    if baseline_dir is None:
        problems.append("no baseline was built (--no-baseline)")
        return problems
    if baseline_dir.relative_to(root).as_posix() != record["baseline"]["run_dir"]:
        problems.append(
            f"{baseline_dir.relative_to(root).as_posix()} is not the recorded "
            f"{record['baseline']['run_dir']} ({hint})"
        )
    manifest = baseline_dir / "baseline_manifest.json"
    got = sha256_file(manifest) if manifest.is_file() else None
    want = record["baseline"]["manifest_sha256"]
    if got != want:
        problems.append(f"baseline_manifest.json: {(got or 'missing')[:12]}, recorded {want[:12]}")
    return problems


def code_agreement(rows, series, movements) -> dict[str, object]:
    """Our gate-based turn against NGSIM's published movement code at the stop-line crossing."""
    by_track = {s.track_id: s for s in series}
    starts, ends = rows.run_bounds()
    run_of = {
        (int(rows.vehicle_id[s]), int(rows.time_ms[s])): (s, e)
        for s, e in zip(starts, ends, strict=True)
    }
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for movement in movements:
        if movement.turn == "u_turn":
            continue
        for track in movement.tracks:
            first = by_track[track.track_id].frames[0].timestamp_s
            vid = int(track.track_id.split(":", 1)[1].split(".", 1)[0])
            s, e = run_of[(vid, WINDOW_START_MS + round(first * 1000))]
            t_ms = WINDOW_START_MS + track.stop_line_time_s * 1000
            i = s + int(np.argmin(np.abs(rows.time_ms[s:e] - t_ms)))
            code = int(rows.movement_code[i])
            table[movement.turn][MOVEMENTS.get(code, f"code {code}")] += 1
    agree = sum(table[t][t] for t in table)
    total = sum(sum(c.values()) for c in table.values())
    return {
        "rule": "NGSIM movement code of the row nearest each counted vehicle's stop-line crossing",
        "ours_vs_ngsim": {turn: dict(counts) for turn, counts in sorted(table.items())},
        "agreeing": agree,
        "total": total,
        "share": round(agree / total, 4) if total else None,
    }


ARM_OF_DIRECTION = {1: "W", 2: "S", 3: "E", 4: "N"}  # EB enters from the west arm, and so on


def code_census(traj, movements) -> dict[str, object]:
    """Counted movements against NGSIM's own codes: per vehicle, the direction and movement of
    its first row inside intersection 2 (int_id 2) in the window. An independent count of what
    the gates should find; it shows vehicles the gates missed, which agreement cannot."""
    in_window = (traj.global_time_ms >= WINDOW_START_MS) & (
        traj.global_time_ms < WINDOW_START_MS + round(WINDOW_S * 1000)
    )
    rows = np.flatnonzero(in_window & (traj.int_id == 2))
    order = rows[np.lexsort((traj.global_time_ms[rows], traj.vehicle_id[rows]))]
    first = order[np.r_[True, traj.vehicle_id[order][1:] != traj.vehicle_id[order][:-1]]]
    census: Counter[str] = Counter()
    for i in first:
        arm = ARM_OF_DIRECTION.get(int(traj.direction[i]))
        turn = MOVEMENTS.get(int(traj.movement[i]))
        if arm and turn:
            census[f"{arm} {turn}"] += 1
    counted = Counter(
        {f"{m.approach} {m.turn}": m.count_veh for m in movements if m.turn != "u_turn"}
    )
    cells = sorted(set(census) | set(counted))
    return {
        "rule": "NGSIM direction and movement of each vehicle's first int_id 2 row in the window",
        "cells": {c: {"ngsim": census[c], "counted": counted[c]} for c in cells},
        "ngsim_total": sum(census.values()),
        "counted_total": sum(counted.values()),
    }


def onset_check(rows, stats, timeline, geometry) -> dict[str, object]:
    """Estimated green onsets against the first move of the queue's lead vehicle.

    At each red-to-green change of an arm's `all` head, take the lead vehicle: the one nearest
    the gate of those standing (below 0.5 m/s) within 15 m behind it 1 s before, in lanes that
    allow through traffic (turn-only lanes follow other heads or go on red). The first time it
    exceeds 2 m/s (above queue creep), within 5 s before to 15 s after the onset, is the
    departure. Built from
    positions and speeds, not crossing times; the start-up time it implies is checked against
    the 2 s assumed.
    """
    through_lanes = {lane.lane_id for lane in geometry.lanes if "through" in lane.allowed_turns}
    out: dict[str, object] = {}
    for arm, head in timeline.heads():
        if head != "all" or stats[arm].stop_gate_m is None:
            continue
        gate = stats[arm].stop_gate_m
        d, k = arm_coords(rows.u, rows.v, arm)
        kept = [int(x[1:]) for x in through_lanes if x.startswith(arm) and x[1:].isdigit()]
        zone = (k > 0) & (d >= gate) & (d <= gate + 15.0) & np.isin(rows.lane_id, kept)
        own = [i for i in timeline.intervals if i.arm == arm and i.head == "all"]
        deltas = []
        for prev, cur in zip(own, own[1:], strict=False):
            if prev.state != "red" or cur.state != "green":
                continue
            t0 = WINDOW_START_MS + round(cur.start_s * 1000)
            waiting = np.flatnonzero(zone & (rows.time_ms == t0 - 1000) & (rows.speed_m_s < 0.5))
            if not len(waiting):
                continue
            lead = rows.vehicle_id[waiting[np.argmin(d[waiting])]]  # nearest the gate
            moving = (
                (rows.vehicle_id == lead)
                & (rows.time_ms >= t0 - 5000)
                & (rows.time_ms <= t0 + 15000)
                & (rows.speed_m_s > 2.0)
            )
            if moving.any():
                deltas.append(round(float(rows.time_ms[moving].min() - t0) / 1000, 1))
        out[arm] = {
            "onsets_with_a_waiting_vehicle": len(deltas),
            "departure_minus_onset_s": deltas,
            "departures_before_onset": int(sum(x < 0 for x in deltas)),
        }
    return out


def queue_summary(world_model) -> list[dict[str, object]]:
    per_lane: dict[str, list] = defaultdict(list)
    for q in world_model.queues:
        per_lane[q.lane_id].append(q)
    end_red: dict[str, list] = defaultdict(list)
    for q in world_model.end_of_red_queues:
        end_red[q.lane_id].append(q)
    out = []
    for lane_id in sorted(per_lane):
        samples = per_lane[lane_id]
        longest = max(samples, key=lambda q: (q.length_m, q.queued_veh))
        reds = end_red.get(lane_id, [])
        out.append(
            {
                "lane_id": lane_id,
                "samples_1s": len(samples),
                "max_queued_veh": max(q.queued_veh for q in samples),
                "max_length_m": round(longest.length_m, 2),
                "max_is_lower_bound": longest.censored,
                "censored_samples": sum(q.censored for q in samples),
                "max_at_s": longest.frame.timestamp_s,
                "end_of_red_samples": len(reds),
                "end_of_red_median_veh": (
                    float(np.median([q.queued_veh for q in reds])) if reds else None
                ),
                "end_of_red_median_length_m": (
                    round(float(np.median([q.length_m for q in reds])), 2) if reds else None
                ),
            }
        )
    return out


def green_summary(timeline) -> dict[str, dict[str, float | int]]:
    out = {}
    for arm, head in timeline.heads():
        greens = [
            i.end_s - i.start_s
            for i in timeline.intervals
            if i.arm == arm and i.head == head and i.state == "green"
        ]
        out[f"{arm} {head}"] = {
            "greens": len(greens),
            "median_green_s": round(float(np.median(greens)), 1) if greens else 0.0,
            "min_green_s": round(min(greens), 1) if greens else 0.0,
            "max_green_s": round(max(greens), 1) if greens else 0.0,
        }
    return out


def baseline_metrics(
    manifest, run_dir: Path, world_model, arms: list[str], observable: list[str]
) -> dict[str, object]:
    """Simulated metrics over the seeds (from SUMO output files only) beside what NGSIM shows.

    `simulated` values come from cairoflow_evaluation.metrics on each seed's tripinfo and queue
    output (each with its source file hash); `observed` values come from the published
    trajectories, only for arms whose queues the trajectories can show (`observable`). They are
    measured differently: a plausibility check, not a calibration.
    """
    values: dict[str, list[float]] = defaultdict(list)
    units: dict[str, str] = {}
    per_seed = []
    for run in manifest.runs:
        for m in run_metrics(
            run_dir / "runs" / run.run_dir,
            experiment_id=manifest.experiment_id,
            arm="baseline",
            seed=run.seed,
            settings=manifest.settings,
            approach_arms=arms,
            root=REPO,
        ):
            values[m.metric_name].append(m.value)
            units[m.metric_name] = m.unit
            per_seed.append(m.model_dump(mode="json"))
    simulated = {
        name: {
            "mean": round(float(np.mean(v)), 3),
            "sd": round(float(np.std(v, ddof=1)), 3),
            "seeds": len(v),
            "unit": units[name],
        }
        for name, v in values.items()
    }
    per_second: dict[str, dict[float, tuple[float, bool]]] = defaultdict(dict)
    for q in world_model.queues:
        t = q.frame.timestamp_s
        if q.length_m >= per_second[q.arm].get(t, (0.0, False))[0]:
            per_second[q.arm][t] = (q.length_m, q.censored)
    counted = sum(m.count_veh for m in world_model.movements if m.turn != "u_turn")
    observed: dict[str, object] = {"throughput_veh_per_h": round(counted * 3600 / WINDOW_S, 1)}
    for arm in observable:
        samples = list(per_second[arm].values())
        longest = max(samples)
        observed[f"max_queue_m.{arm}"] = {
            "value": round(longest[0], 2),
            "lower_bound": longest[1],
            "censored_seconds": sum(c for _, c in samples),
            "seconds": len(samples),
        }
        observed[f"mean_queue_m.{arm}"] = {
            "value": round(float(np.mean([x for x, _ in samples])), 2),
            "lower_bound": any(c for _, c in samples),
        }
    return {
        "simulated": simulated,
        "simulated_per_seed": per_seed,
        "observed": observed,
        "not_observable": [f"queues on arm {a}" for a in arms if a not in observable],
        "notes": [
            "simulated: window [warm-up, warm-up + horizon) of each seed; observed: the 900 s "
            "trajectory window",
            "throughput is demand-derived: demand is generated at the observed counts, so its "
            "agreement shows the demand was served, not that the signal model is right",
            "simulated queue: SUMO queueing_length of the longest approach lane per step, from "
            "SUMO's stop line; observed queue: counting gate to the front of the last queued "
            "vehicle (one vehicle length shorter), within the observed lane length",
            "an observed value marked lower_bound includes seconds when the queue reached the end "
            "of the observed lane (censored)",
            "cross-street queues are not observable: those arms are tracked only from about "
            "their limit lines",
            "delay is not observed here; no observed counterpart is given",
        ],
    }


def arm_record(stat) -> dict[str, object]:
    record = asdict(stat)
    record["queue_heads"] = {
        str(lane): {
            "most_advanced_decile_m": a,
            "median_m": b,
            "least_advanced_decile_m": c,
            "samples": n,
        }
        for lane, (a, b, c, n) in stat.queue_heads.items()
    }
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true", help="also write the committed record")
    parser.add_argument("--no-baseline", action="store_true", help="derive only; run no SUMO")
    parser.add_argument(
        "--as-of",
        type=parse_as_of,
        help="UTC time for every timestamp and directory name (default: now)",
    )
    parser.add_argument(
        "--check-record",
        action="store_true",
        help="compare the outputs with the committed record; exit 1 on any difference",
    )
    args = parser.parse_args(argv)
    if args.record and RECORD_DIR.exists():
        print(f"{RECORD_DIR} exists; the record is never overwritten", file=sys.stderr)
        return 2
    if args.check_record and not (RECORD_DIR / "scenario.json").is_file():
        print(f"{RECORD_DIR / 'scenario.json'} is missing; nothing to check", file=sys.stderr)
        return 2

    now = args.as_of or datetime.now(UTC).replace(microsecond=0)
    stamp = f"{now:%Y%m%dT%H%M%SZ}"
    sha = sha256_file(CSV)
    if sha != CSV_SHA256:
        print(f"{CSV} has SHA-256 {sha}, expected {CSV_SHA256}", file=sys.stderr)
        return 2
    jpolicy, spolicy, wpolicy = JunctionPolicy(), SignalPolicy(), WorldModelPolicy()

    traj = load_trajectories(CSV)
    speed, heading = kinematics(traj, LocalFrame(*ORIGIN_FT))
    frame, frame_rows = junction_frame(traj, speed, heading, ORIGIN_FT, jpolicy)
    rows = junction_rows(traj, frame, speed, WINDOW_START_MS, WINDOW_S, jpolicy)
    stats = arm_statistics(rows, jpolicy)
    series = ground_series(rows, WINDOW_ID, WINDOW_START_MS, jpolicy)
    provisional = build_geometry(stats, "provisional", WINDOW_ID, sha, policy=jpolicy)
    first_pass, _ = count_movements(series, provisional, WINDOW_S, wpolicy)
    turns, turn_sources, turn_notes = allowed_turns(first_pass, stats, jpolicy)
    geometry = build_geometry(
        stats, f"ngsim-int2-{sha[:12]}", WINDOW_ID, sha, turns, turn_sources, jpolicy
    )
    movements, ambiguous = count_movements(series, geometry, WINDOW_S, wpolicy)
    estimate = estimate_signal_timeline(
        movements,
        WINDOW_ID,
        WINDOW_S,
        timeline_id=f"ngsim-int2-signals-{sha[:12]}",
        source=(
            "trajectory-derived: stop-line discharge times of the published NGSIM trajectories "
            f"(SHA-256 {sha}) by cairoflow_perception.ngsim_junction; no signal head was read. "
            f"Synthetic constants inside: yellow {spolicy.yellow_s:g} s, start-up "
            f"{spolicy.startup_lost_s:g} s, clearance {spolicy.all_red_s:g} s, minimum green "
            f"{spolicy.min_green_s:g} s; red gaps include time when nobody crossed"
        ),
        policy=spolicy,
    )
    world_model = build_world_model_from_ground(
        WINDOW_ID,
        window_frames(WINDOW_ID, WINDOW_S),
        WINDOW_S,
        series,
        geometry,
        sha,
        wpolicy,
        signals=estimate.timeline,
        ground_track_interval_s=1.0,
        parameters={
            "position": "NGSIM front centre",
            "frame_bearing_deg": frame.bearing_deg,
        },
        now=now,
    )

    out = REPO / "data/processed/ngsim-int2" / stamp
    out.mkdir(parents=True, exist_ok=False)
    hashes = {
        "geometry.json": write_new(out / "geometry.json", geometry.model_dump_json(indent=2)),
        "signal_timeline.json": write_new(
            out / "signal_timeline.json", estimate.timeline.model_dump_json(indent=2)
        ),
        "world_model.json": write_new(out / "world_model.json", world_model.model_dump_json()),
    }
    derivation: dict[str, object] = {
        "script": "scripts/build_ngsim_int2_scenario.py",
        "created_at_utc": now.isoformat(),
        "input": {"path": str(CSV.relative_to(REPO)), "sha256": sha, **LICENCE},
        "camera_input": "none: no pixels, no calibration, no image registration (ADR-019)",
        "software": {"numpy": np.__version__, "eclipse-sumo": metadata.version("eclipse-sumo")},
        "window": {
            "id": WINDOW_ID,
            "start": "2005-06-16 08:30:00.0 PDT",
            "start_global_time_ms": WINDOW_START_MS,
            "duration_s": WINDOW_S,
        },
        "frame": {
            "origin_state_plane_ft": list(ORIGIN_FT),
            "crs": "EPSG:2229 NAD83 / California zone 5 (ftUS)",
            "rotation": "+y = Lankershim northbound (mean grid bearing of NB through rows)",
            "bearing_deg": frame.bearing_deg,
            "rows": frame_rows,
        },
        "policies": {
            "junction": jpolicy.model_dump(),
            "signal": spolicy.model_dump(),
            "world_model": wpolicy.model_dump(),
        },
        "rows_in_study_box": len(rows),
        "series": len(series),
        "heavy_series": sum(s.class_label == "truck" for s in series),
        "arms": {arm: arm_record(stat) for arm, stat in stats.items()},
        "turns": {"per_lane": turn_sources, "notes": turn_notes},
        "movements": [
            {
                "approach": m.approach,
                "exit": m.exit_arm,
                "turn": m.turn,
                "count_veh": m.count_veh,
                "flow_veh_per_h": m.flow_veh_per_h,
                "flow_ci95_veh_per_h": list(m.flow_ci95_veh_per_h),
                "lane_unknown": sum(t.lane_id is None for t in m.tracks),
            }
            for m in movements
            if m.count_veh
        ],
        "ambiguous": {
            "total": len(ambiguous),
            "by_reason": dict(Counter(a.reason.split(" but ")[0] for a in ambiguous)),
        },
        "turn_code_agreement": code_agreement(rows, series, movements),
        "code_census": code_census(traj, movements),
        "signal_estimate": {
            "heads": estimate.heads,
            "greens": green_summary(estimate.timeline),
            "dropped": list(estimate.dropped),
            "adjustments": list(estimate.adjustments),
            "consistency": list(estimate.consistency),
            "consistency_note": (
                "windows are built from the through (and protected-left) crossings, so those "
                "shares are near 100% by construction; right turns and permissive lefts are the "
                "informative rows"
            ),
            "onset_check": onset_check(rows, stats, estimate.timeline, geometry),
            "representative_cycle": estimate.representative_cycle,
        },
        "queues": queue_summary(world_model),
        "signal_notes": list(world_model.signal_notes),
    }
    baseline = None
    if not args.no_baseline:
        run_dir = REPO / "experiments/runs" / f"ngsim-int2-baseline-{stamp}"
        options = CompileOptions(
            program_id="ngsim_int2_cycle",
            cycle_start_s=float(estimate.representative_cycle["start_s"]),
            right_turn_on_red=True,  # right turners often cross on red; see "right_turn_on_red"
        )
        manifest = run_baseline(
            world_model, geometry, estimate.timeline, run_dir, RunSettings(), options, now=now
        )
        # the same baseline without right turn on red, to record why it is switched on
        ablation = run_baseline(
            world_model,
            geometry,
            estimate.timeline,
            run_dir.with_name(run_dir.name + "-no-rtor"),
            RunSettings(),
            options.model_copy(update={"right_turn_on_red": False}),
            verify_reruns=False,
            now=now,
        )
        on_red = {
            c["approach"]: {
                "crossings_with_known_state": c["during_known_state"],
                "share_on_red": round(1 - c["share"], 4) if c["share"] is not None else None,
            }
            for c in estimate.consistency
            if c["turn"] == "right"
        }
        baseline = {
            "run_dir": str(run_dir.relative_to(REPO)),
            "experiment_id": manifest.experiment_id,
            "valid": manifest.valid,
            "valid_means": "run validity: no teleports or collisions, every vehicle arrived; "
            "not model validity",
            "reasons": list(manifest.reasons),
            "reruns_identical": manifest.reruns_identical,
            "settings": manifest.settings.model_dump(),
            "compile_options": options.model_dump(),
            "demand_options": DemandOptions().model_dump(),
            "vehicle_types": VEHICLE_TYPES,
            "right_turn_on_red": {
                "label": "synthetic (a modelling choice)",
                "observed_right_turners_on_red_by_approach": on_red,
                "ablation_without_it": {
                    "valid": ablation.valid,
                    "reasons": list(ablation.reasons),
                },
            },
            "net_sha256": manifest.network.net_sha256,
            "link_count": manifest.network.link_count,
            "cycle_s": manifest.network.signal_program.cycle_s,
            "cycle_start_s": manifest.network.signal_program.cycle_start_s,
            "phases": [
                {"name": p.name, "duration_s": p.duration_s, "state": p.state}
                for p in manifest.network.signal_program.phases
            ],
            "parameter_labels": dict(Counter(p.provenance for p in manifest.network.parameters)),
            "demand_notes": list(manifest.demand.notes),
            "runs": [
                {
                    "seed": r.seed,
                    "loaded": r.loaded,
                    "arrived": r.arrived,
                    "teleports": r.teleports,
                    "collisions": r.collisions,
                    "emergency_braking": _safety(run_dir / "runs" / r.run_dir, "emergencyBraking"),
                    "valid": r.valid,
                    "tripinfo_sha256": r.tripinfo_sha256,
                }
                for r in manifest.runs
            ],
            "manifest_sha256": sha256_file(run_dir / "baseline_manifest.json"),
            "metrics": baseline_metrics(
                manifest,
                run_dir,
                world_model,
                [a for a in "NESW" if any(x.arm == a for x in geometry.lanes)],
                [a for a in "NESW" if stats[a].stop_position_m is not None],
            ),
        }
        derivation["baseline"] = baseline
    write_new(out / "derivation.json", json.dumps(derivation, indent=2) + "\n")

    if args.record:
        RECORD_DIR.mkdir(parents=True)
        write_new(RECORD_DIR / "geometry.json", geometry.model_dump_json(indent=2) + "\n")
        write_new(
            RECORD_DIR / "signal_timeline.json", estimate.timeline.model_dump_json(indent=2) + "\n"
        )
        scenario = dict(derivation)
        scenario["processed_outputs"] = {"dir": str(out.relative_to(REPO)), "sha256": hashes}
        write_new(RECORD_DIR / "scenario.json", json.dumps(scenario, indent=2) + "\n")

    print_summary(derivation)
    status = 0 if baseline is None or baseline["valid"] else 1
    if args.check_record:
        record = json.loads((RECORD_DIR / "scenario.json").read_text(encoding="utf-8"))
        baseline_dir = None if baseline is None else REPO / baseline["run_dir"]
        problems = check_record(record, REPO, out, baseline_dir)
        for problem in problems:
            print(f"record check: {problem}")
        print(
            "record check: every output matches the committed record"
            if not problems
            else f"record check: {len(problems)} difference(s)"
        )
        status = status or (1 if problems else 0)
    return status


def _safety(seed_dir: Path, attribute: str) -> int:
    safety = ET.parse(seed_dir / "statistics.xml").getroot().find("safety")
    return int(safety.get(attribute, 0)) if safety is not None else 0


def print_summary(derivation: dict) -> None:
    print(f"frame bearing {derivation['frame']['bearing_deg']} deg; series {derivation['series']}")
    for arm, rec in derivation["arms"].items():
        lanes = [lane["lane_id"] for lane in rec["approach"]]
        print(
            f"{arm}: stop gate {rec['stop_gate_m']} m, stop position {rec['stop_position_m']} m, "
            f"exit gate {rec['exit_gate_m']} m, approach lane_ids {lanes}"
        )
    for m in derivation["movements"]:
        print(f"  {m['approach']}->{m['exit']} {m['turn']}: {m['count_veh']}")
    print("ambiguous:", derivation["ambiguous"])
    print("turn code agreement:", derivation["turn_code_agreement"]["share"])
    estimate = derivation["signal_estimate"]
    print("heads:", json.dumps(estimate["heads"], indent=1))
    print("greens:", json.dumps(estimate["greens"]))
    print("cycle:", estimate["representative_cycle"])
    print("adjustments:", len(estimate["adjustments"]), "dropped:", len(estimate["dropped"]))
    for c in estimate["consistency"]:
        print(f"  {c['approach']} {c['turn']} ({c['head']}): {c['share']}")
    baseline = derivation.get("baseline")
    if baseline:
        print("baseline valid:", baseline["valid"], baseline["reasons"])
        print("reruns identical:", baseline["reruns_identical"], "cycle", baseline["cycle_s"])
        for r in baseline["runs"]:
            print(
                f"  seed {r['seed']}: loaded {r['loaded']} arrived {r['arrived']} "
                f"teleports {r['teleports']} collisions {r['collisions']}"
            )
        metrics = baseline["metrics"]
        for name, value in metrics["simulated"].items():
            seen = metrics["observed"].get(name)
            print(
                f"  {name}: simulated {value['mean']} +/- {value['sd']} {value['unit']} "
                f"(n={value['seeds']}); observed {seen if seen is not None else 'n/a'}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
