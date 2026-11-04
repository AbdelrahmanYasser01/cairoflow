"""Validate the recorded NGSIM intersection 2 SUMO baseline against the published trajectories.

    uv run --frozen python scripts/validate_ngsim_int2_baseline.py --out <new directory> \
        [--baseline experiments/runs/ngsim-int2-baseline-20261028T074304Z] \
        [--record data/geometry/ngsim-lankershim-int2] \
        [--timeline data/geometry/ngsim-lankershim-int2/signal_timeline_v2.json]

Inputs, each checked against a recorded SHA-256 before use (exit 2 on any difference):
- the committed record (`scenario.json`; its `geometry.json` and `signal_timeline.json` against
  the record's processed-output hashes);
- with `--timeline`, the timeline a replay baseline replays instead of the record's: version 2 or
  an ensemble member (v2a, v2b; ADR-025), beside the record (its hash against the one
  `signal_estimate_*.json` naming it, its id against the baseline's signal program). The record's
  timeline still rebuilds the record's world model; the replay window's observed side and every
  observed signal state then use the replayed one, so both sides share one timeline;
- the trajectories (`scenario.json` `input`, and the `SHA256SUMS` beside them);
- the baseline directory (its `baseline_manifest.json` against the record, or, for another
  baseline, its world model against the record's; the network, demand files and every recorded
  tripinfo against the manifest).

The observed world model is rebuilt from the trajectories exactly as the record was, and must hash
to the recorded `world_model.json`. Each recorded seed is rerun into `<out>/sumo/seed_<n>/` with its
recorded command plus `--fcd-output`; with only the added 'fcd' device entries removed, the rerun's
tripinfo must hash to the recorded `tripinfo_sha256` (exit 1 otherwise). The same world-model code
then measures the SUMO floating-car data (cairoflow_evaluation.baseline_validation).

Writes into `--out`, which must not exist: `validation.json`, `validation.md`, `charts/*.svg` and
the reruns. No network access and no model calls. Observed values here are `derived` (our fixed
rules over the published positions); simulated values come from SUMO output files only. Each
quantity gets a status, not a score.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import numpy as np
import sumo
import sumolib

from cairoflow_contracts.evidence import (
    FrameRef,
    IntersectionGeometry,
    SignalTimeline,
    TrafficWorldModel,
)
from cairoflow_evaluation.baseline_validation import (
    Comparison,
    DischargeRecord,
    SegmentRecord,
    Side,
    SimLane,
    Source,
    arm_queue_series,
    block_bootstrap_interval,
    clip_series,
    clip_timeline,
    cluster_bootstrap_interval,
    compare,
    comparison_windows,
    describe,
    discharge_per_green,
    discharge_records,
    follows_head,
    gate_distance_m,
    headways_from,
    known_throughout,
    lane_use,
    movement_counts,
    new_output_dir,
    paired_lags,
    pooled_heads,
    present_at,
    program_timeline,
    queue_spacings_m,
    replay_window,
    segment_records,
    series_from_fcd,
    sim_geometry,
    spread,
    stop_gate_rule,
    tripinfo_sha256_without_device,
    wilson_interval,
)
from cairoflow_perception.ngsim import LocalFrame, kinematics, load_trajectories
from cairoflow_perception.ngsim_junction import (
    JunctionPolicy,
    arm_coords,
    consistency,
    ground_series,
    junction_frame,
    junction_rows,
    window_frames,
)
from cairoflow_perception.world_model import (
    GroundSeries,
    WorldModelPolicy,
    build_world_model_from_ground,
    poisson_interval,
    turn_for,
)
from cairoflow_simulation.network import content_sha256

REPO = Path(__file__).resolve().parents[1]
RECORD_DIR = REPO / "data/geometry/ngsim-lankershim-int2"
BASELINE_DIR = REPO / "experiments/runs/ngsim-int2-baseline-20261028T074304Z"
SCHEMA = "cairoflow-baseline-validation/0.1.0"
SIM_CLIP = "sumo-baseline-measurement-window"
ARMS = ("N", "E", "S", "W")
# Assumed analysis constants (not from the data); recorded in validation.json.
ENTRY_M = 100.0  # segment entry: inside where N and S vehicles are first tracked (p5 103-109 m)
SATURATION_FROM = 5  # HCM convention: headways from the 5th queued vehicle on
POSITIONS = tuple(range(2, 11))  # queue positions shown in the headway table
RESTART_M_S = 2.0  # a new stop needs this speed first (the record's onset check threshold)
BLOCK_S = 99  # bootstrap block and cluster length: the simulated cycle
RESAMPLES, BOOT_SEED = 2000, 20261028
GATE_PAD_M = 0.5
TRIM_M = 0.5  # full-depth simulated lanes stop this short of the arm's far end
FRAME_S = 0.1  # NGSIM time step
TRACE_FILE = "trace_vehicles.json"  # a replay baseline's vehicle table (trace_demand.py)

W_OBS = "observed 2005-06-16 08:30:00-08:45:00 PDT: window [0, 900) s"
W_SIM = "simulated [300, 1200) s of each seed (after 300 s warm-up), shifted to [0, 900) s"
CIRCULAR = (
    "circular: SUMO demand is generated from these observed counts, so agreement checks the "
    "demand generator and the network's capacity to serve it, not the model"
)
STOP_REF = (
    "each side counts at its own stop line: observed, the NGSIM counting gate; simulated, the "
    "same rule on SUMO's stopped vehicles (SUMO stop lines lie 5-6.5 m from the gates)"
)
GEOMETRY = (
    "SUMO arms are straight, 200 m long and exactly N/E/S/W (synthetic); the real cross street "
    "is about 5.6 deg off perpendicular"
)
FIXED_CYCLE = (
    "SUMO repeats one 99 s cycle cut from the estimate; observed onset spacing ranged 80-201 s "
    "(apparently actuated control)"
)
SIGNAL_EST = "observed signal states are estimated from the same crossings (no head was read)"
CENSORED = (
    "observed lanes end where tracking ends; a queue reaching within 12 m of the end is censored "
    "(a lower bound); simulated lanes are cut to the same depth, so both are censored alike"
)
RESOLUTION = (
    "observed positions every 0.1 s, simulated every 1 s (SUMO's step; positions interpolated "
    "linearly, exact for SUMO's constant speed within a step)"
)
POSITION_ERROR = "NGSIM positions carry about 4 ft (1.2 m) along-road error (stated, not verified)"
POISSON = (
    "SUMO demand departs as a Poisson process 200 m out; real arrivals come from the upstream "
    "signals, outside the study box"
)
# A replay baseline (ADR-022) replays the observed timeline and vehicles over a clip window.
# Its vehicles enter at their first whole-second observed state (up to 1 s, about 14 m, inside
# where they were first tracked), at their distance from the drawn stop line measured from
# SUMO's, which lies 5-6.5 m nearer the centre on Lankershim: so from about 84 m out. The
# segment entry moves to 80 m, where both sides have nearly every vehicle.
REPLAY_ENTRY_M = 80.0
REPLAY_SIGNALS = (
    "SUMO replays the estimated timeline second by second over the window instead of repeating "
    "one cut (no head was read; yellow 4 s, start-up 2 s and clearance 1 s are synthetic); after "
    "it, 4 s yellow, 1 s all-red and the 99 s cut drain the network"
)
REPLAY_CIRCULAR = (
    "circular by construction, one for one: every vehicle counted in the observed window enters "
    "SUMO as itself (trace demand); a difference only shows vehicles SUMO moves across the "
    "window's ends or onto another movement"
)
REPLAY_ARRIVALS = (
    "simulated vehicles enter where and when they were first observed (Lankershim about 105-110 m "
    "out, at SUMO's safe speed, not the observed one), so arrivals at the entry line are largely "
    "an input check, not a test"
)
REPLAY_LANES = (
    "near-circular: each simulated vehicle enters in the lane it used at the observed stop line "
    "where SUMO connects that lane to its exit ('best' otherwise), so this tests SUMO's lane "
    "changes over about 100 m"
)
REPLAY_REALISATION = (
    "observed vehicles, not Poisson arrivals: every seed has the same vehicles at the same entry "
    "states (only speed factors and SUMO's randomness differ), so the seed spread is SUMO's "
    "microscopic noise for one observed realisation, not demand uncertainty"
)
REPLAY_INITIAL = (
    "vehicles on the approaches at the window start enter in place at simulated 0 s, re-packed "
    "per lane at SUMO's spacing (7.5 m per car)"
)


def cross_street_caveat(options: dict[str, object]) -> str:
    """The cross-street entry rule in the replay's vehicle table (trace_demand.TraceOptions)."""
    within = options.get("upstream_within_m", 40.0)
    timing = (
        "; one whose observed crossing falls in a green joins the queue of the red before it, "
        f"{options.get('arrival_gap_s', 2.0):g} s apart in crossing order, otherwise it is timed "
        "to reach that spot at the speed limit"
        if options.get("discharge_anchored")
        else ", timed to reach that spot at the speed limit"
    )
    if options.get("discharge_anchored") and options.get("queued_at_start"):
        timing += (
            "; one whose queue arrival falls at or before the window start enters at rest at 0 s "
            "at the stop line instead, behind its lane's initial vehicles"
        )
    return (
        f"assumed: cross-street vehicles first seen within {within:g} m of the stop line (inside "
        f"their queues) enter at the arm start{timing}; their arrival times are not observed"
    )


@dataclass(frozen=True)
class Variant:
    """What kind of baseline is validated, and the windows and labels that follow from it."""

    replay: tuple[float, float] | None  # clip seconds; None: one cut cycle repeated
    observed: tuple[float, float]  # clip seconds
    simulated: tuple[float, float]  # simulation seconds
    cross_streets: str = ""  # a replay's cross-street entry rule (cross_street_caveat)

    @property
    def length(self) -> float:
        return self.observed[1] - self.observed[0]

    @property
    def kind(self) -> str:
        return "repeated-cycle" if self.replay is None else "replay"

    @property
    def entry_m(self) -> float:
        return ENTRY_M if self.replay is None else REPLAY_ENTRY_M

    @property
    def w_obs(self) -> str:
        a, b = self.observed
        return (
            W_OBS
            if self.replay is None
            else (
                f"observed 2005-06-16 08:30:00 PDT + [{a:g}, {b:g}) s (clip seconds), shifted to "
                f"[0, {self.length:g}) s"
            )
        )

    @property
    def w_sim(self) -> str:
        return (
            W_SIM
            if self.replay is None
            else (f"simulated [0, {self.length:g}) s of each seed: the replay itself (no warm-up)")
        )

    @property
    def signals(self) -> str:
        return FIXED_CYCLE if self.replay is None else REPLAY_SIGNALS

    @property
    def circular(self) -> str:
        return CIRCULAR if self.replay is None else REPLAY_CIRCULAR

    @property
    def arrivals(self) -> str:
        return POISSON if self.replay is None else REPLAY_ARRIVALS

    @property
    def reds(self) -> str:
        return "the queue at the end of red grows with the red's length; observed reds vary, " + (
            "simulated reds are fixed" if self.replay is None else "simulated ones replay them"
        )

    @property
    def everywhere(self) -> tuple[str, ...]:
        if self.replay is None:
            return ()
        return REPLAY_REALISATION, self.cross_streets or cross_street_caveat({}), REPLAY_INITIAL

    def header(self) -> list[str]:
        """What the validated baseline is, for the report's header."""
        if self.replay is None:
            return [
                "Baseline: **repeated cycle** (one 99 s cut of the estimated timeline, repeated; "
                "Poisson demand at the observed counts, 300 s warm-up)."
            ]
        a, b = self.replay
        return [
            f"Baseline: **replay** (ADR-022) over clip [{a:g}, {b:g}) s = simulated "
            f"[0, {self.length:g}) s. What changed against the repeated-cycle baseline:",
            "",
            "- signals: " + REPLAY_SIGNALS + ";",
            *(f"- {text};" for text in self.everywhere),
            "- movement counts are " + REPLAY_CIRCULAR + ";",
            f"- the segment entry is {REPLAY_ENTRY_M:g} m from the centre (100 m for the "
            "repeated-cycle baseline): simulated vehicles enter at their first whole-second "
            "observed state, up to 14 m inside where they were first tracked, and 5-6.5 m "
            "nearer the centre on Lankershim (distance kept to SUMO's stop line).",
        ]


class InputError(RuntimeError):
    """An input does not match its recorded hash or the record."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def rel(path: Path, root: Path = REPO) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


# --- Inputs ------------------------------------------------------------------------------------


@dataclass
class Inputs:
    record_dir: Path
    scenario: dict
    geometry: IntersectionGeometry
    timeline: SignalTimeline
    csv: Path
    csv_sha256: str
    baseline_dir: Path
    manifest: dict
    is_recorded_baseline: bool
    variant: Variant | None = None
    replayed: SignalTimeline | None = None  # the timeline a replay baseline replays
    checks: list[dict] = field(default_factory=list)
    sources: dict[str, Source] = field(default_factory=dict)

    def check(self, name: str, expected: object, got: object, fatal: bool = True) -> None:
        ok = expected == got
        self.checks.append({"check": name, "expected": expected, "got": got, "ok": ok})
        if not ok and fatal:
            raise InputError(f"{name}: expected {expected}, got {got}")


def load_inputs(record_dir: Path, baseline_dir: Path, timeline_path: Path | None = None) -> Inputs:
    scenario_path = record_dir / "scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    csv = REPO / scenario["input"]["path"]
    manifest_path = baseline_dir / "baseline_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded = scenario["processed_outputs"]["sha256"]
    raw = {
        name: (record_dir / name).read_bytes() for name in ("geometry.json", "signal_timeline.json")
    }
    inputs = Inputs(
        record_dir=record_dir,
        scenario=scenario,
        geometry=IntersectionGeometry.model_validate_json(raw["geometry.json"]),
        timeline=SignalTimeline.model_validate_json(raw["signal_timeline.json"]),
        csv=csv,
        csv_sha256="",
        baseline_dir=baseline_dir,
        manifest=manifest,
        is_recorded_baseline=sha256_file(manifest_path) == scenario["baseline"]["manifest_sha256"],
    )
    inputs.sources["scenario.json"] = Source(
        path=rel(scenario_path), sha256=sha256_file(scenario_path)
    )
    for name, data in raw.items():
        # the committed record is the processed output plus a final newline
        inputs.check(
            f"record {name} = processed {name}",
            recorded[name],
            hashlib.sha256(data.removesuffix(b"\n")).hexdigest(),
        )
        inputs.sources[name] = Source(
            path=rel(record_dir / name), sha256=hashlib.sha256(data).hexdigest()
        )
    inputs.csv_sha256 = sha256_file(csv)
    inputs.check(
        "trajectories SHA-256 = record input", scenario["input"]["sha256"], inputs.csv_sha256
    )
    sums = csv.parent / "SHA256SUMS"
    if sums.is_file():
        listed = {
            line.split()[1]: line.split()[0]
            for line in sums.read_text(encoding="utf-8").splitlines()
            if len(line.split()) == 2
        }
        inputs.check(
            "trajectories SHA-256 = SHA256SUMS",
            listed.get(scenario["input"]["path"]),
            inputs.csv_sha256,
        )
    inputs.sources["trajectories"] = Source(
        path=scenario["input"]["path"], sha256=inputs.csv_sha256
    )

    inputs.check(
        "baseline world model = record world model",
        recorded["world_model.json"],
        manifest["world_model_sha256"],
    )
    if baseline_dir.resolve() == BASELINE_DIR.resolve() or inputs.is_recorded_baseline:
        inputs.check(
            "baseline_manifest.json = record",
            scenario["baseline"]["manifest_sha256"],
            sha256_file(manifest_path),
        )
    inputs.sources["baseline_manifest.json"] = Source(
        path=rel(manifest_path), sha256=sha256_file(manifest_path)
    )
    net = baseline_dir / "network" / "network.net.xml"
    inputs.check(
        "network.net.xml = manifest",
        manifest["network"]["net_sha256"],
        content_sha256(net, strip_comments=True),
    )
    inputs.sources["network.net.xml"] = Source(
        path=rel(net), sha256=manifest["network"]["net_sha256"]
    )
    for demand in manifest["demand"]["files"]:
        path = baseline_dir / "demand" / demand["file"]
        inputs.check(f"demand {demand['file']} = manifest", demand["sha256"], sha256_file(path))
    for run in manifest["runs"]:
        path = baseline_dir / "runs" / run["run_dir"] / "tripinfo.xml"
        inputs.check(
            f"recorded seed {run['seed']} tripinfo = manifest",
            run["tripinfo_sha256"],
            content_sha256(path, strip_comments=True),
        )
    offsets = [e.get("offset") for e in ET.parse(net).getroot().iter("tlLogic")]
    inputs.check("signal program offset (phase 0 starts at 0 s)", ["0"], offsets)
    params = {p["name"]: p["value"] for p in manifest["network"]["parameters"]}
    settings = manifest["settings"]
    try:
        replay = replay_window(params)
        observed, simulated = comparison_windows(
            replay, settings["warmup_s"], settings["horizon_s"], scenario["window"]["duration_s"]
        )
    except ValueError as error:
        raise InputError(f"baseline windows: {error}") from error
    inputs.variant = Variant(replay, observed, simulated)
    inputs.replayed = inputs.timeline
    if timeline_path is not None:
        inputs.replayed = alternative_timeline(inputs, record_dir, timeline_path)
    inputs.check(
        "baseline signal program timeline = the timeline compared",
        inputs.replayed.timeline_id,
        manifest["network"]["signal_program"]["timeline_id"],
    )
    if replay is not None:
        replay_checks(inputs, replay)
    return inputs


def estimate_record_for(record_dir: Path, path: Path) -> Path:
    """The one estimate record beside the scenario record (`signal_estimate_*.json`: version 2,
    DATA_REGISTER.md §22, or an ensemble member, ADR-025) whose `timeline_file` is `path`'s name."""
    found = [
        candidate
        for candidate in sorted(record_dir.glob("signal_estimate_*.json"))
        if json.loads(candidate.read_text(encoding="utf-8")).get("timeline_file") == path.name
    ]
    if len(found) != 1 or path.resolve().parent != record_dir.resolve():
        raise InputError(
            f"{path} is not the timeline of exactly one estimate record in {record_dir}"
        )
    return found[0]


def alternative_timeline(inputs: Inputs, record_dir: Path, path: Path) -> SignalTimeline:
    """A committed timeline version other than the record's (version 2 or an ensemble member),
    checked against its estimate record, which must name the record's world model."""
    estimate_path = estimate_record_for(record_dir, path)
    estimate = json.loads(estimate_path.read_text(encoding="utf-8"))
    inputs.check(f"{path.name} = {estimate_path.name}", estimate["timeline_sha256"],
                 sha256_file(path))  # fmt: skip
    inputs.check(
        f"{estimate_path.name} world model = record world model",
        inputs.scenario["processed_outputs"]["sha256"]["world_model.json"],
        estimate["inputs"]["world_model_sha256"],
    )
    inputs.sources["replayed timeline"] = Source(path=rel(path), sha256=sha256_file(path))
    return SignalTimeline.model_validate_json(path.read_text(encoding="utf-8"))


def replay_checks(inputs: Inputs, replay: tuple[float, float]) -> None:
    """A replay baseline: its window against the record, its program against the record's
    timeline, and its vehicle table against the hash in its demand notes."""
    inputs.check(
        f"replay window [{replay[0]:g}, {replay[1]:g}) s: every head of the replayed timeline "
        "known",
        True,
        known_throughout(inputs.replayed, *replay),
    )
    program = inputs.manifest["network"]["signal_program"]["phases"]
    length = replay[1] - replay[0]
    replayed = program_timeline(
        [(p["duration_s"], p["arm_states"], p["left_states"]) for p in program],
        0.0,
        length,
        timeline_id="replay-program",
        clip_id=inputs.replayed.clip_id,
        source="the replay baseline's signal program",
    )
    want = clip_timeline(inputs.replayed, *replay)
    differing = sum(
        any(replayed.state_at(a, t, h) != want.state_at(a, t, h) for a, h in want.heads())
        for t in range(int(length))
    ) + (0 if replayed.heads() == want.heads() else int(length))
    inputs.check(
        "seconds where the replay program differs from the record's timeline", 0, differing
    )
    notes = " ".join(inputs.manifest["demand"]["notes"])
    listed = re.search(rf"{re.escape(TRACE_FILE)}: SHA-256 ([0-9a-f]{{64}})", notes)
    table = inputs.baseline_dir / "demand" / TRACE_FILE
    inputs.check(
        f"{TRACE_FILE} = demand notes",
        listed.group(1) if listed else None,
        sha256_file(table) if table.is_file() else None,
    )
    inputs.sources[TRACE_FILE] = Source(path=rel(table), sha256=sha256_file(table))
    options = json.loads(table.read_text(encoding="utf-8")).get("options", {})
    inputs.variant = replace(inputs.variant, cross_streets=cross_street_caveat(options))


# --- Observed side -------------------------------------------------------------------------------


@dataclass
class Observed:
    series: list[GroundSeries]
    world_model: TrafficWorldModel
    junction: JunctionPolicy
    policy: WorldModelPolicy
    timeline: SignalTimeline  # over the compared window, starting at 0


def observed_window(obs: Observed, inputs: Inputs, window: tuple[float, float]) -> Observed:
    """The observation over clip seconds `window` alone, shifted to start at 0: the same series,
    timeline and world-model rules, so the simulated replay window meets identical boundaries."""
    start, end = window
    s = inputs.scenario
    timeline = clip_timeline(inputs.replayed, start, end)
    series = clip_series(obs.series, start, end, FRAME_S)
    world_model = build_world_model_from_ground(
        s["window"]["id"],
        window_frames(s["window"]["id"], end - start),
        end - start,
        series,
        inputs.geometry,
        inputs.csv_sha256,
        obs.policy,
        signals=timeline,
        ground_track_interval_s=end - start,  # stored tracks thinned to one frame (unused)
        now=datetime.fromisoformat(s["created_at_utc"]),
    )
    return Observed(series, world_model, obs.junction, obs.policy, timeline)


def observed_side(inputs: Inputs) -> Observed:
    """The record's world model, rebuilt from the trajectories as the build script does."""
    s = inputs.scenario
    origin = tuple(s["frame"]["origin_state_plane_ft"])
    junction = JunctionPolicy(**s["policies"]["junction"])
    policy = WorldModelPolicy(**s["policies"]["world_model"])
    window_id, window_s = s["window"]["id"], float(s["window"]["duration_s"])
    traj = load_trajectories(inputs.csv)
    speed, heading = kinematics(traj, LocalFrame(*origin))
    frame, _ = junction_frame(traj, speed, heading, origin, junction)
    inputs.check("frame bearing = record", s["frame"]["bearing_deg"], frame.bearing_deg)
    rows = junction_rows(
        traj, frame, speed, s["window"]["start_global_time_ms"], window_s, junction
    )
    series = ground_series(rows, window_id, s["window"]["start_global_time_ms"], junction)
    inputs.check("vehicle runs in the study box = record", s["series"], len(series))
    world_model = build_world_model_from_ground(
        window_id,
        window_frames(window_id, window_s),
        window_s,
        series,
        inputs.geometry,
        inputs.csv_sha256,
        policy,
        signals=inputs.timeline,
        ground_track_interval_s=1.0,
        parameters={"position": "NGSIM front centre", "frame_bearing_deg": frame.bearing_deg},
        now=datetime.fromisoformat(s["created_at_utc"]),
    )
    inputs.check(
        "rebuilt world model = record world_model.json",
        s["processed_outputs"]["sha256"]["world_model.json"],
        hashlib.sha256(world_model.model_dump_json().encode("utf-8")).hexdigest(),
    )
    return Observed(series, world_model, junction, policy, inputs.timeline)


# --- Simulated side ------------------------------------------------------------------------------


def rerun_seed(inputs: Inputs, run: dict, out_dir: Path) -> dict:
    """The recorded command plus --fcd-output, in a new directory; proven the same run."""
    recorded = inputs.baseline_dir / "runs" / run["run_dir"]
    out_dir.mkdir(parents=True, exist_ok=False)
    command = list(run["command"])
    for flag in ("--net-file", "--route-files"):
        i = command.index(flag)
        command[i + 1] = os.path.relpath((recorded / command[i + 1]).resolve(), out_dir)
    command += ["--fcd-output", "fcd.xml"]
    binary = Path(sumo.SUMO_HOME) / "bin" / ("sumo.exe" if os.name == "nt" else "sumo")
    result = subprocess.run(
        [str(binary), *command[1:]], cwd=out_dir, capture_output=True, text=True, check=False
    )
    tripinfo = (
        (out_dir / "tripinfo.xml").read_text(encoding="utf-8") if result.returncode == 0 else ""
    )
    got, removed = tripinfo_sha256_without_device(tripinfo, "fcd")
    trips = tripinfo.count("<tripinfo ")
    return {
        "seed": run["seed"],
        "command": command,
        "returncode": result.returncode,
        "recorded_tripinfo_sha256": run["tripinfo_sha256"],
        "rerun_tripinfo_sha256_without_fcd_device": got,
        "fcd_device_entries_removed": removed,
        "trips": trips,
        "identical": result.returncode == 0 and got == run["tripinfo_sha256"] and removed == trips,
        "fcd": Source(
            path=rel(out_dir / "fcd.xml", out_dir.parents[1]),
            sha256=sha256_file(out_dir / "fcd.xml"),
        )
        if result.returncode == 0
        else None,
        "stderr_tail": result.stderr[-400:],
    }


@dataclass
class Fcd:
    t: np.ndarray
    vehicle: np.ndarray
    vtype: np.ndarray
    x: np.ndarray
    y: np.ndarray
    speed: np.ndarray
    lane: np.ndarray

    def samples(self) -> list[tuple[float, str, str, float, float]]:
        return list(
            zip(
                self.t.tolist(),
                self.vehicle.tolist(),
                self.vtype.tolist(),
                self.x.tolist(),
                self.y.tolist(),
                strict=True,
            )
        )


def read_fcd(path: Path) -> Fcd:
    cols: dict[str, list] = defaultdict(list)
    for _, element in ET.iterparse(path):
        if element.tag != "timestep":
            continue
        t = float(element.get("time", "nan"))
        for v in element.iter("vehicle"):
            cols["t"].append(t)
            cols["vehicle"].append(v.get("id"))
            cols["vtype"].append(v.get("type"))
            cols["x"].append(float(v.get("x")))
            cols["y"].append(float(v.get("y")))
            cols["speed"].append(float(v.get("speed")))
            cols["lane"].append(v.get("lane"))
        element.clear()
    return Fcd(**{k: np.asarray(v) for k, v in cols.items()})


def sim_lanes(net_path: Path, manifest: dict) -> tuple[list[SimLane], dict[str, str]]:
    """SUMO's approach and exit lanes across their arms, approach lanes under the NGSIM lane id
    they were compiled from (manifest `<lane>.sumo_lane_index`); and SUMO lane id -> NGSIM id."""
    net = sumolib.net.readNet(str(net_path))
    index = {
        p["name"].split(".")[0]: int(p["value"])
        for p in manifest["network"]["parameters"]
        if p["name"].endswith(".sumo_lane_index")
    }
    lanes, names = [], {}
    for lane_id, i in index.items():
        arm = lane_id[0]
        lane = net.getEdge(f"{arm}_in").getLane(i)
        shape = np.asarray(lane.getShape())
        _, k = arm_coords(shape[:, 0], shape[:, 1], arm)
        centre, half = float(np.mean(k)), lane.getWidth() / 2
        lanes.append(SimLane(lane_id, arm, "approach", centre - half, centre + half))
        names[lane.getID()] = lane_id
    for arm in ARMS:
        if not net.hasEdge(f"{arm}_out"):
            continue
        for lane in net.getEdge(f"{arm}_out").getLanes():
            shape = np.asarray(lane.getShape())
            _, k = arm_coords(shape[:, 0], shape[:, 1], arm)
            centre, half = float(np.mean(k)), lane.getWidth() / 2
            lanes.append(SimLane(lane.getID(), arm, "exit", centre - half, centre + half))
    return lanes, names


def sim_stop_gates(
    fcds: dict[int, Fcd], arms: list[str], junction: JunctionPolicy, begin: float, end: float
) -> dict[str, dict]:
    """The observed counting-line rule on SUMO's stopped vehicles, pooled over seeds."""
    out = {}
    for arm in arms:
        d_all, lane_all, t_all = [], [], []
        for seed, f in fcds.items():
            on = np.char.startswith(f.lane.astype(str), f"{arm}_in_")
            keep = on & (f.t >= begin) & (f.t < end) & (f.speed < junction.stopped_m_s)
            d, _ = arm_coords(f.x[keep], f.y[keep], arm)
            near = (d > junction.arm_min_distance_m) & (d < junction.stopped_zone_max_m)
            d_all.append(d[near])
            lane_all.append(np.char.add(f"{seed}:", f.lane[keep][near].astype(str)))
            t_all.append(f.t[keep][near])
        heads = pooled_heads(np.concatenate(d_all), np.concatenate(lane_all), np.concatenate(t_all))
        out[arm] = {
            "gate_m": stop_gate_rule(
                heads, junction.head_quantile, junction.gate_margin_m, junction.min_head_samples
            ),
            "pooled_heads": len(heads),
            "median_head_m": round(float(np.median(heads)), 3),
        }
    return out


@dataclass
class SimSeed:
    seed: int
    fcd: Source
    series: list[GroundSeries]  # inside the study box, as the observed tracks
    unclipped: list[GroundSeries]  # the whole network (segment entry; see compare_segment)
    world_model: TrafficWorldModel
    full_depth: TrafficWorldModel


def simulated_side(
    inputs: Inputs, observed: Observed, reruns: list[dict], sumo_dir: Path
) -> tuple[list[SimSeed], dict]:
    manifest, settings = inputs.manifest, inputs.manifest["settings"]
    begin, window = float(settings["warmup_s"]), float(settings["horizon_s"])
    fcds = {r["seed"]: read_fcd(sumo_dir / f"seed_{r['seed']}" / "fcd.xml") for r in reruns}
    net = inputs.baseline_dir / "network" / "network.net.xml"
    lanes, sumo_to_ngsim = sim_lanes(net, manifest)
    approach_arms = sorted({x.arm for x in lanes if x.direction == "approach"}, key=ARMS.index)
    gates = sim_stop_gates(fcds, approach_arms, observed.junction, begin, begin + window)
    exits = {g.arm: gate_distance_m(g) for g in inputs.geometry.gates if g.role == "exit_line"}
    params = {p["name"]: p["value"] for p in manifest["network"]["parameters"]}
    anchor = manifest["network"]["net_sha256"]
    stop = {arm: gates[arm]["gate_m"] for arm in approach_arms}
    geometry = sim_geometry(
        inputs.geometry,
        lanes,
        stop,
        exits,
        geometry_id="sumo-as-observed",
        clip_id=SIM_CLIP,
        ground_source_sha256=anchor,
        pad_m=GATE_PAD_M,
    )
    full = sim_geometry(
        inputs.geometry,
        lanes,
        stop,
        exits,
        geometry_id="sumo-full-depth",
        clip_id=SIM_CLIP,
        ground_source_sha256=anchor,
        pad_m=GATE_PAD_M,
        depth_m={
            x.lane_id: params[f"{x.arm}.arm_length"] - stop[x.arm] - TRIM_M
            for x in lanes
            if x.direction == "approach"
        },
    )
    program = manifest["network"]["signal_program"]
    # SUMO switches signals at the start of step t and then moves vehicles to the positions it
    # reports at t: the state of step t governs the motion over (t - 1, t]. So the state over
    # window second [s, s + 1) is the program's state at begin + s + 1 (one step later).
    step = float(settings["step_length_s"])
    timeline = program_timeline(
        [(p["duration_s"], p["arm_states"], p["left_states"]) for p in program["phases"]],
        begin + step,
        window,
        timeline_id=f"sumo-{program['program_id']}",
        clip_id=SIM_CLIP,
        source=(
            f"the SUMO program {program['program_id']} of the recorded network (simulated: what "
            f"SUMO ran, from 0 s with offset 0) over [{begin:g}, {begin + window:g}) s, each "
            f"second taking the state of the step that ends it ({step:g} s later)"
        ),
    )
    frames = [
        FrameRef(clip_id=SIM_CLIP, frame_index=i, timestamp_s=float(i)) for i in range(int(window))
    ]
    created = datetime.fromisoformat(inputs.scenario["created_at_utc"])

    def measure(series: list[GroundSeries], geo: IntersectionGeometry) -> TrafficWorldModel:
        # the record's world-model policy; stored ground tracks thinned to one frame (unused)
        return build_world_model_from_ground(
            SIM_CLIP,
            frames,
            window,
            series,
            geo,
            anchor,
            observed.policy,
            signals=timeline,
            ground_track_interval_s=window,
            now=created,
        )

    seeds = []
    for run in reruns:
        samples = fcds[run["seed"]].samples()
        boxed = series_from_fcd(
            samples,
            begin_s=begin,
            window_s=window,
            clip_id=SIM_CLIP,
            box_half_m=observed.junction.box_half_size_m,
        )
        everywhere = series_from_fcd(samples, begin_s=begin, window_s=window, clip_id=SIM_CLIP)
        seeds.append(
            SimSeed(
                run["seed"],
                run["fcd"],
                boxed,
                everywhere,
                measure(boxed, geometry),
                measure(everywhere, full),
            )
        )
    context = {
        "geometry": geometry,
        "timeline": timeline,
        "stop_gates": gates,
        "sumo_stop_lines_m": {a: params[f"{a}.stop_line_distance_sumo"] for a in approach_arms},
        "observed_gates_m": {
            g.arm: round(gate_distance_m(g), 3)
            for g in inputs.geometry.gates
            if g.role == "stop_line"
        },
        "exit_lines_m": {a: round(d, 3) for a, d in exits.items()},
        "lane_map": sumo_to_ngsim,
        "fcds": fcds,
    }
    return seeds, context


# --- Comparisons ---------------------------------------------------------------------------------


@dataclass
class Builder:
    """Collects comparisons and details, with the sources every value cites."""

    obs_sources: tuple[Source, ...]
    sim_sources: tuple[Source, ...]
    variant: Variant
    comparisons: list[Comparison] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)

    def obs(self, value, unit, **kw) -> Side:
        return Side(
            label="derived",
            value=_num(value),
            unit=unit,
            window=self.variant.w_obs,
            sources=self.obs_sources,
            **kw,
        )

    def sim(self, values, unit, **kw) -> Side:
        sp = spread(values)
        return Side(
            label="simulated",
            value=sp.mean,
            unit=unit,
            window=self.variant.w_sim,
            n=sp.n,
            interval=(sp.min, sp.max) if sp.n else None,
            interval_method="range over seeds (mean and SD in `spread`)",
            spread=sp,
            sources=self.sim_sources,
            **kw,
        )

    def add(self, *args, **kw) -> None:
        c = compare(*args, **kw)
        extra = tuple(x for x in self.variant.everywhere if x not in c.limitations)
        self.comparisons.append(c.model_copy(update={"limitations": c.limitations + extra}))


def _num(x) -> float | None:
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else float(x)


def _mean(x) -> float | None:
    return float(np.mean(x)) if len(x) else None


def boot(clusters, statistic=None) -> tuple[float, float] | None:
    kw = {"statistic": statistic} if statistic else {}
    return cluster_bootstrap_interval(clusters, resamples=RESAMPLES, seed=BOOT_SEED, **kw)


def demand_in_window(
    baseline_dir: Path, manifest: dict, begin: float, end: float
) -> dict[int, Counter]:
    out = {}
    for demand in manifest["demand"]["files"]:
        counts: Counter[str] = Counter()
        for _, v in ET.iterparse(baseline_dir / "demand" / demand["file"]):
            if v.tag == "vehicle" and begin <= float(v.get("depart")) < end:
                route = v.find("route").get("edges").split()
                a, b = route[0].split("_")[0], route[-1].split("_")[0]
                counts[f"{a} {turn_for(a, b)}"] += 1
        out[demand["seed"]] = counts
    return out


def compare_counts(
    b: Builder, obs: Observed, sims: list[SimSeed], demand: dict[int, Counter]
) -> None:
    observed = movement_counts(obs.world_model.movements)
    simulated = [movement_counts(s.world_model.movements) for s in sims]
    keys = [k for k in observed if observed[k] or any(c.get(k) for c in simulated)]
    method = (
        "vehicles whose first stop-line crossing and a later exit-line crossing both fall in the "
        "window (world_model.count_movements rule, identical code on both sides)"
    )
    for key in [*keys, "total"]:
        value = sum(observed.values()) if key == "total" else observed[key]
        sims_ = [sum(c.values()) if key == "total" else c.get(key, 0) for c in simulated]
        made = [sum(c.values()) if key == "total" else c.get(key, 0) for c in demand.values()]
        b.add(
            f"count.{key.replace(' ', '_')}",
            f"Counted vehicles, {key}",
            f"veh per {b.variant.length:g} s",
            method,
            b.obs(
                value,
                f"veh per {b.variant.length:g} s",
                n=value,
                interval=poisson_interval(value),
                interval_method="exact Poisson 95% (counting noise only)",
            ),
            b.sim(
                sims_,
                f"veh per {b.variant.length:g} s",
                note=f"generated with departure in the window: mean {np.mean(made):.1f} veh",
            ),
            "comparable",
            [b.variant.circular, STOP_REF],
        )


def compare_lane_shares(
    b: Builder, obs: Observed, sims: list[SimSeed], geometry: IntersectionGeometry
) -> None:
    method = (
        "share of the approach's counted vehicles by the lane polygon holding their last position "
        "within 2 s before the stop-line crossing (world_model rule); simulated lanes are SUMO's, "
        "named after the NGSIM lane they were compiled from"
    )
    observed = lane_use(obs.world_model.movements)
    simulated = [lane_use(s.world_model.movements) for s in sims]
    table = {}
    for arm in ARMS:
        lanes = [x.lane_id for x in geometry.lanes if x.arm == arm and x.direction == "approach"]
        total = sum(observed.get(arm, Counter()).values())
        if not total:
            continue
        rows, gap = [], []
        keys = lanes + (
            ["unknown"]
            if any(c.get(arm, Counter())["unknown"] for c in [observed, *simulated])
            else []
        )
        for lane in keys:
            k = observed[arm][lane]
            sim_counts = [c.get(arm, Counter()) for c in simulated]
            shares = [c[lane] / sum(c.values()) if sum(c.values()) else None for c in sim_counts]
            sim_side = b.sim(shares, "share")
            gap.append(abs((sim_side.value or 0.0) - k / total))
            b.add(
                f"lane_share.{arm}.{lane}",
                f"Lane share at the stop line, {arm} approach, lane {lane}",
                "share",
                method,
                b.obs(
                    k / total,
                    "share",
                    n=total,
                    interval=wilson_interval(k, total),
                    interval_method="Wilson 95% over vehicles (counting noise only)",
                ),
                sim_side,
                "comparable-with-caveats",
                [
                    "SUMO chooses lanes itself (departLane 'best', its lane-change model); the "
                    "observed choice also reflects upstream conditions outside the study box",
                    "simulated lanes sit up to about 1 m across from the NGSIM lane centres",
                    "vehicles within a platoon are not independent; the interval is too narrow",
                    *([] if b.variant.replay is None else [REPLAY_LANES]),
                ],
            )
            rows.append({"lane": lane, "observed_veh": k, "simulated_share_mean": sim_side.value})
        table[arm] = {
            "observed_total_veh": total,
            "lanes": rows,
            "total_variation_distance": round(0.5 * sum(gap), 4),
        }
    b.details["lane_shares"] = {
        "label": "derived (observed) and simulated",
        "unit": "share",
        "by_arm": table,
        "by_turn_observed_veh": {
            k: dict(v) for k, v in lane_use(obs.world_model.movements, by_turn=True).items()
        },
        "by_turn_simulated_veh_per_seed": [
            {k: dict(v) for k, v in lane_use(s.world_model.movements, by_turn=True).items()}
            for s in sims
        ],
    }


def through_lanes(geometry: IntersectionGeometry) -> set[str]:
    return {
        x.lane_id
        for x in geometry.lanes
        if x.direction == "approach" and set(x.allowed_turns) == {"through"}
    }


def _records_in(records: list[DischargeRecord], arm: str, lanes: set[str]) -> list[DischargeRecord]:
    return [r for r in records if r.arm == arm and r.lane_id in lanes]


def _by_block(records: list[SegmentRecord], value) -> list[list[float]]:
    """Values grouped by the block of entry time they fall in (clusters for the bootstrap)."""
    groups: dict[int, list[float]] = defaultdict(list)
    for r in records:
        groups[int(r.entry_s // BLOCK_S)].append(value(r))
    return list(groups.values())


def _queued_total(queues, arm: str, pick: int) -> float:
    """Queued vehicles summed over the arm's lanes in its first (0) or last (-1) sampled second."""
    times = sorted({q.frame.timestamp_s for q in queues if q.arm == arm})
    t = times[pick]
    return float(sum(q.queued_veh for q in queues if q.arm == arm and q.frame.timestamp_s == t))


def compare_discharge(
    b: Builder, inputs: Inputs, obs: Observed, sims: list[SimSeed], context: dict
) -> dict[str, object]:
    obs_records = discharge_records(
        obs.world_model.end_of_red_queues,
        obs.world_model.movements,
        obs.timeline,
        inputs.geometry,
    )
    sim_records = [
        discharge_records(
            s.world_model.end_of_red_queues,
            s.world_model.movements,
            context["timeline"],
            context["geometry"],
        )
        for s in sims
    ]
    through = through_lanes(inputs.geometry)
    method = (
        "per end-of-red queue in a through-only lane: its queued vehicles' stop-line crossings in "
        "the green and yellow that follow, in crossing order; headway k = crossing k - crossing "
        "k-1 (k >= 2); position 1 (start-up) is left out: the observed green onsets are estimated "
        "from these crossings"
    )
    limits = [
        STOP_REF,
        RESOLUTION,
        POSITION_ERROR,
        "only vehicles queued at the end of red; vehicles joining the moving queue are left out",
        "simulated discharge follows SUMO's Krauss defaults (accel 2.6 m/s2, minGap 2.5 m, "
        "tau 1 s): uncalibrated",
    ]
    by_position: dict[str, list] = {}
    for arm in ARMS:
        obs_arm = _records_in(obs_records, arm, through)
        sim_arm = [_records_in(r, arm, through) for r in sim_records]
        for name, low, high in (
            ("saturation", SATURATION_FROM, None),
            ("start", 2, SATURATION_FROM - 1),
        ):
            clusters = headways_from(obs_arm, low, high)
            pooled = [h for c in clusters for h in c]
            per_seed = [_mean([h for c in headways_from(r, low, high) for h in c]) for r in sim_arm]
            label = f"k >= {low}" if high is None else f"k = {low}-{high}"
            enough = len(pooled) >= 10 and spread(per_seed).n == len(sims)
            b.add(
                f"headway.{name}.{arm}",
                f"Mean discharge headway, {arm} through lanes, queue positions {label}",
                "s",
                method,
                b.obs(
                    _mean(pooled),
                    "s",
                    n=len(pooled),
                    interval=boot(clusters),
                    interval_method="cluster bootstrap 95% over lane red ends",
                    note=f"{sum(1 for c in clusters if c)} lane red ends contribute",
                ),
                b.sim(per_seed, "s", note="per seed: mean of pooled headways"),
                "comparable-with-caveats" if enough else "not comparable",
                limits
                if enough
                else [
                    *limits,
                    "too few observed queues this long (fewer than "
                    "10 headways, or a seed without any)",
                ],
                None if enough else "longer observed queues on these lanes, or a longer window",
            )
        rows = []
        for k in POSITIONS:
            o_clusters = headways_from(obs_arm, k, k)
            o = [h for c in o_clusters for h in c]
            s_ = [_mean([h for c in headways_from(rs, k, k) for h in c]) for rs in sim_arm]
            rows.append(
                {
                    "position": k,
                    "observed_n": len(o),
                    "observed_mean_s": _mean(o),
                    "observed_ci95_s": boot(o_clusters),
                    "simulated": spread(s_).model_dump(),
                }
            )
        by_position[arm] = rows
    # discharge per green (all heads)
    obs_greens = discharge_per_green(obs.world_model.movements, obs.timeline)
    sim_greens = [discharge_per_green(s.world_model.movements, context["timeline"]) for s in sims]
    greens_detail = {}
    for arm in ARMS:
        counts = [n for _, _, n in obs_greens.get(arm, [])]
        if not counts:
            continue
        lengths = [b_ - a for a, b_, _ in obs_greens[arm]]
        b.add(
            f"discharge_per_green.{arm}",
            f"Stop-line crossings per green (green + yellow), {arm} all head",
            "veh per green",
            "crossings by the movements the arm's 'all' head controls, inside each green that "
            "follows a red, up to the end of its yellow",
            b.obs(
                _mean(counts),
                "veh per green",
                n=len(counts),
                interval=boot([[c] for c in counts]),
                interval_method="bootstrap 95% over greens",
            ),
            b.sim([_mean([n for _, _, n in g.get(arm, [])]) for g in sim_greens], "veh per green"),
            "comparable-with-caveats" if arm in OBSERVABLE else "not comparable",
            [
                b.variant.signals,
                SIGNAL_EST,
                "observed green windows are fitted to these crossings (start "
                "2 s before the first, yellow ends just after the last)",
                b.variant.circular,
                *(
                    []
                    if arm in OBSERVABLE
                    else [
                        "an observed green exists only where at least 2 vehicles crossed "
                        "(sparser windows were dropped), so on this low-volume arm the observed "
                        "mean is biased upwards by construction"
                    ]
                ),
            ],
            None if arm in OBSERVABLE else "the signal states of this arm (controller logs)",
        )
        greens_detail[arm] = {
            "observed_service_s": describe(lengths),
            "simulated_service_s": describe(
                [b_ - a for g in sim_greens for a, b_, _ in g.get(arm, [])]
            ),
        }
    residual = {}
    for arm in ARMS:
        for side, records in (("observed", [obs_records]), ("simulated", sim_records)):
            shares = []
            for rs in records:
                use = [r for r in rs if r.arm == arm and r.queued_veh and not r.censored]
                shares.append(sum(r.residual_veh > 0 for r in use) / len(use) if use else None)
            residual.setdefault(arm, {})[side] = spread(shares).model_dump()
    b.details["discharge"] = {
        "label": "derived (observed) and simulated",
        "unit": "s",
        "headway_by_position": by_position,
        "service_windows": greens_detail,
        "residual_share_not_compared": {
            "note": "share of uncensored lane red ends with a queued vehicle still waiting when "
            "the "
            "following yellow ends; not compared: observed greens end just after their last "
            "crossing by construction, so the observed share is near 0 by definition",
            "by_arm": residual,
        },
        "records": {
            "observed": len(obs_records),
            "simulated_per_seed": [len(r) for r in sim_records],
        },
    }
    return {"obs": obs_records, "sim": sim_records}


def end_of_red_totals(
    samples, geometry: IntersectionGeometry, timeline: SignalTimeline
) -> dict[tuple[str, str], list[tuple[float, int, float, bool]]]:
    """Per (arm, head), per red end: (time, queued vehicles summed over the lanes following that
    head, longest lane queue m, any lane censored)."""
    lanes = {x.lane_id: x for x in geometry.lanes if x.direction == "approach"}
    left = {arm for arm, head in timeline.heads() if head == "left"}
    groups: dict[tuple[str, str, float], list] = defaultdict(list)
    for q in samples:
        groups[(q.arm, follows_head(lanes[q.lane_id], left), q.frame.timestamp_s)].append(q)
    out: dict[tuple[str, str], list] = defaultdict(list)
    for (arm, head, t), qs in sorted(groups.items()):
        out[(arm, head)].append(
            (
                t,
                sum(q.queued_veh for q in qs),
                max(q.length_m for q in qs),
                any(q.censored for q in qs),
            )
        )
    return out


def red_lengths(timeline: SignalTimeline, arm: str, head: str) -> list[float]:
    own = [i for i in timeline.intervals if i.arm == arm and i.head == head]
    return [
        cur.end_s - cur.start_s
        for prev, cur, nxt in zip(own, own[1:], own[2:], strict=False)
        if cur.state == "red" and nxt.state == "green" and prev.state != "unknown"
    ]


OBSERVABLE = ("N", "S")


def compare_queues(
    b: Builder, inputs: Inputs, obs: Observed, sims: list[SimSeed], context: dict
) -> None:
    depth = {
        arm: min(
            float(
                np.max(arm_coords([p[0] for p in x.polygon_m], [p[1] for p in x.polygon_m], arm)[0])
            )
            for x in inputs.geometry.lanes
            if x.arm == arm and x.direction == "approach"
        )
        - context["observed_gates_m"][arm]
        for arm in ARMS
    }
    obs_red = end_of_red_totals(obs.world_model.end_of_red_queues, inputs.geometry, obs.timeline)
    sim_red = [
        end_of_red_totals(s.world_model.end_of_red_queues, context["geometry"], context["timeline"])
        for s in sims
    ]
    full_red = [
        end_of_red_totals(s.full_depth.end_of_red_queues, context["geometry"], context["timeline"])
        for s in sims
    ]
    red_detail = {}
    method = (
        "per red end, vehicles queued in the lanes following that head, summed (world_model rule: "
        "below 1 m/s on a centred 1 s window, chained back from the stop line while each is within "
        "12 m of the previous), in the last sample before green"
    )
    for (arm, head), rows in obs_red.items():
        values = [n for _, n, _, _ in rows]
        censored = sum(c for *_, c in rows)
        ok = arm in OBSERVABLE
        per_seed = [_mean([n for _, n, _, _ in r.get((arm, head), [])]) for r in sim_red]
        full = [_mean([n for _, n, _, _ in r.get((arm, head), [])]) for r in full_red]
        sim_censored = sum(c for r in sim_red for *_, c in r.get((arm, head), []))
        b.add(
            f"end_of_red_queue.{arm}_{head}",
            f"Queued vehicles at the end of red, {arm} {head} head (mean over red ends)",
            "veh",
            method,
            b.obs(
                _mean(values),
                "veh",
                n=len(values),
                interval=boot([[v] for v in values]),
                interval_method="bootstrap 95% over red ends",
                lower_bound=bool(censored),
                note=f"{censored} of {len(values)} red ends censored; median {np.median(values):g}",
            ),
            b.sim(
                per_seed,
                "veh",
                lower_bound=bool(sim_censored),
                note=f"cut to the observed depth; {sim_censored} censored red ends over all seeds; "
                f"full simulated depth: mean {spread(full).mean:.2f} veh",
            ),
            "comparable-with-caveats" if ok else "not comparable",
            [
                STOP_REF,
                CENSORED,
                b.variant.signals,
                SIGNAL_EST,
                b.variant.reds,
            ]
            if ok
            else [
                f"the {arm} approach is observed only {depth[arm]:.1f} m beyond its gate "
                "(one or two vehicles): queues there are not observable"
            ],
            None if ok else f"trajectories or video covering the {arm} approach upstream",
        )
        red_detail[f"{arm} {head}"] = {
            "observed_red_s": describe(red_lengths(obs.timeline, arm, head)),
            "simulated_red_s": describe(red_lengths(context["timeline"], arm, head)),
            "observed_queued_veh": describe(values),
            "observed_longest_lane_m": describe([m for _, _, m, _ in rows]),
            "simulated_longest_lane_m_per_seed": [
                _mean([m for _, _, m, _ in r.get((arm, head), [])]) for r in sim_red
            ],
        }
    for arm in OBSERVABLE:
        clusters = queue_spacings_m(
            [q for q in obs.world_model.end_of_red_queues if q.arm == arm], obs.series
        )
        per_seed = [
            _mean(
                [
                    g
                    for c in queue_spacings_m(
                        [q for q in s.world_model.end_of_red_queues if q.arm == arm], s.series
                    )
                    for g in c
                ]
            )
            for s in sims
        ]
        gaps = [g for c in clusters for g in c]
        b.add(
            f"jam_spacing.{arm}",
            f"Front-to-front spacing of consecutive queued vehicles at the end of red, {arm}",
            "m per veh",
            "in every end-of-red lane queue of two or more vehicles, the distance along the arm "
            "between consecutive queued vehicles' fronts (vehicle length plus gap)",
            b.obs(
                _mean(gaps),
                "m per veh",
                n=len(gaps),
                interval=boot(clusters),
                interval_method="cluster bootstrap 95% over lane red ends",
            ),
            b.sim(per_seed, "m per veh", note="per seed: mean of pooled spacings"),
            "comparable-with-caveats",
            [
                POSITION_ERROR,
                "SUMO's spacing is its vehicle length plus minGap (passenger 5.0 + 2.5 m, truck "
                "10.0 + 2.5 m: synthetic defaults); it converts a queue in vehicles to metres",
                "queued means below 1 m/s: a slowly rolling queue counts too",
            ],
        )
    # per-second series, the shape of metrics.py's queue metrics, with the observed definition
    obs_series = arm_queue_series(obs.world_model.queues)
    sim_series = [arm_queue_series(s.world_model.queues) for s in sims]
    full_series = [arm_queue_series(s.full_depth.queues) for s in sims]
    recorded = inputs.scenario["baseline"]["metrics"]["simulated"]
    series_detail = {}
    for arm in ARMS:
        rows = obs_series.get(arm, [])
        if not rows:
            continue
        lengths = [m for _, m, _, _ in rows]
        longest = max(rows, key=lambda r: r[1])
        ok = arm in OBSERVABLE
        note = (
            f"metrics.py on SUMO queue.xml (queueing_length from SUMO's stop line, uncut): "
            f"max {recorded[f'max_queue_m.{arm}']['mean']:.1f}, mean "
            f"{recorded[f'mean_queue_m.{arm}']['mean']:.1f} m (scenario.json)"
        )
        limits = (
            [
                STOP_REF,
                CENSORED,
                b.variant.signals,
                POSITION_ERROR,
                "length runs to the front of the last queued vehicle (one vehicle length short of "
                "its rear) and excludes the 2-4 m from the gate to where the first vehicle stands",
            ]
            if ok
            else [f"the {arm} approach is observed only {depth[arm]:.1f} m beyond its gate"]
        )
        for name, obs_value, sim_values, full_values, interval in (
            (
                "max",
                longest[1],
                [max(m for _, m, _, _ in s[arm]) for s in sim_series],
                [max(m for _, m, _, _ in s[arm]) for s in full_series],
                None,
            ),
            (
                "mean",
                float(np.mean(lengths)),
                [float(np.mean([m for _, m, _, _ in s[arm]])) for s in sim_series],
                [float(np.mean([m for _, m, _, _ in s[arm]])) for s in full_series],
                block_bootstrap_interval(lengths, BLOCK_S, resamples=RESAMPLES, seed=BOOT_SEED),
            ),
        ):
            lower = longest[3] if name == "max" else any(c for *_, c in rows)
            b.add(
                f"queue_{name}.{arm}",
                f"{name.capitalize()} over the window of the arm's longest lane queue each second, "
                f"{arm}",
                "m",
                "per second, the longest approach-lane queue of the arm (world_model rule, as the "
                "record's observed queue metrics); the max or mean over the window's seconds",
                b.obs(
                    obs_value,
                    "m",
                    n=len(lengths),
                    interval=interval,
                    lower_bound=bool(lower),
                    interval_method=None
                    if interval is None
                    else f"moving-block bootstrap 95% ({BLOCK_S} s blocks)",
                    note=f"{sum(c for *_, c in rows)} of {len(rows)} seconds censored",
                ),
                b.sim(
                    sim_values,
                    "m",
                    lower_bound=any(c for s in sim_series for *_, c in s[arm]),
                    note=f"cut to the observed depth; full simulated depth: mean "
                    f"{spread(full_values).mean:.1f} m. {note}",
                ),
                "comparable-with-caveats" if ok else "not comparable",
                limits,
                None if ok else f"trajectories or video covering the {arm} approach upstream",
            )
        series_detail[arm] = {
            "observed_depth_m": round(depth[arm], 2),
            "observed_queued_veh": describe([n for _, _, n, _ in rows]),
            "simulated_queued_veh_mean_per_seed": [
                float(np.mean([n for _, _, n, _ in s[arm]])) for s in sim_series
            ],
        }
    b.details["queues"] = {
        "label": "derived (observed) and simulated",
        "unit": "veh, m, s",
        "end_of_red": red_detail,
        "per_second": series_detail,
    }
    b.details["_series"] = {"observed": obs_series, "simulated": sim_series}


def compare_segment(
    b: Builder, obs: Observed, sims: list[SimSeed], timelines: tuple[SignalTimeline, SignalTimeline]
) -> None:
    """Travel, stops and arrivals on green over the fixed segment; `timelines`: (observed,
    simulated)."""
    kw = {
        "half_window_s": obs.policy.speed_half_window_s,
        "stopped_m_s": obs.policy.stopped_speed_m_s,
        "restart_m_s": RESTART_M_S,
    }
    observed, skipped = segment_records(
        obs.series, obs.world_model.movements, OBSERVABLE, b.variant.entry_m, **kw
    )
    # The simulated entry crossing comes from the unclipped positions: at a 1 s step, a vehicle's
    # first position inside the box can already be past the entry line (14 m per step at speed),
    # whereas the observed tracks start within about 1.4 m of the box edge (0.1 s).
    simulated = [
        segment_records(s.unclipped, s.world_model.movements, OBSERVABLE, b.variant.entry_m, **kw)
        for s in sims
    ]
    method = (
        f"from the inbound crossing {b.variant.entry_m:g} m from the centre on the approach arm "
        "to the "
        "exit-line crossing (both interpolated); speeds at every whole second inside (centred 1 s "
        "window); a stop begins below 1 m/s and another only after exceeding 2 m/s; vehicles must "
        "enter and leave inside the window on both sides"
    )
    limits = [
        GEOMETRY,
        RESOLUTION,
        POSITION_ERROR,
        "SUMO speed limit 13.89 m/s and speed factors N(1, 0.1) are synthetic; the observed free "
        "speeds are not",
        "queues reaching past the entry line hide part of the delay on both sides",
        "vehicles inside the entry line at the window start, or not across the exit line at its "
        "end, are left out on both sides",
    ]
    table = {}
    for arm in OBSERVABLE:
        for turn in ("left", "through", "right"):
            o = [r for r in observed if r.approach == arm and r.turn == turn]
            s = [[r for r in recs if r.approach == arm and r.turn == turn] for recs, _ in simulated]
            if not o:
                continue
            tag = f"{arm}_{turn}"
            for key, name, unit, f in (
                ("travel_time", "Mean segment travel time", "s", lambda r: r.travel_time_s),
                (
                    "stopped_time",
                    "Mean time below 1 m/s in the segment",
                    "s",
                    lambda r: r.stopped_s,
                ),
                (
                    "stops",
                    "Stops per vehicle in the segment",
                    "stops per veh",
                    lambda r: float(r.stops),
                ),
            ):
                b.add(
                    f"{key}.{tag}",
                    f"{name}, {arm} {turn}",
                    unit,
                    method,
                    b.obs(
                        _mean([f(r) for r in o]),
                        unit,
                        n=len(o),
                        interval=boot(_by_block(o, f)),
                        interval_method=f"cluster bootstrap 95% over {BLOCK_S} s entry blocks",
                    ),
                    b.sim([_mean([f(r) for r in rs]) for rs in s], unit),
                    "comparable-with-caveats",
                    limits,
                )
            stopped = sum(r.stops > 0 for r in o)
            b.add(
                f"stopped_share.{tag}",
                f"Share of vehicles stopping at least once in the segment, {arm} {turn}",
                "share",
                method,
                b.obs(
                    stopped / len(o),
                    "share",
                    n=len(o),
                    interval=wilson_interval(stopped, len(o)),
                    interval_method="Wilson 95% over vehicles",
                ),
                b.sim(
                    [sum(r.stops > 0 for r in rs) / len(rs) if rs else None for rs in s], "share"
                ),
                "comparable-with-caveats",
                limits,
            )
            table[tag] = {
                "observed_travel_time_s": describe([r.travel_time_s for r in o]),
                "simulated_travel_time_s_pooled": describe(
                    [r.travel_time_s for rs in s for r in rs]
                ),
            }
    arrivals = {}
    for arm in OBSERVABLE:
        green, known = _entries_on_green(observed, timelines[0], arm)
        per_seed = [_entries_on_green(recs, timelines[1], arm) for recs, _ in simulated]
        shown = (_green_share(timelines[0], arm), _green_share(timelines[1], arm))
        b.add(
            f"arrival_on_green.{arm}",
            f"Share of {arm} through vehicles crossing the entry line while their head is green",
            "share",
            f"through vehicles' inbound crossings of the {b.variant.entry_m:g} m entry line "
            "(as in the "
            "segment) during their arm's 'all' green, over those with a known state",
            b.obs(
                green / known,
                "share",
                n=known,
                interval=wilson_interval(green, known),
                interval_method="Wilson 95% over vehicles",
                note=f"green shown {shown[0]:.1%} of the window",
            ),
            b.sim(
                [g / k if k else None for g, k in per_seed],
                "share",
                note=f"green shown {shown[1]:.1%} of the window",
            ),
            "comparable-with-caveats",
            [
                SIGNAL_EST,
                b.variant.signals,
                "the entry line is 6-8 s of free driving upstream of the "
                "stop line, so this describes the arrival pattern, not arrival at the stop line",
                b.variant.arrivals,
            ],
        )
        arrivals[arm] = {
            "green_time_share_observed": shown[0],
            "green_time_share_simulated": shown[1],
            "platoon_ratio_observed": green / known / shown[0],
            "platoon_ratio_simulated_per_seed": [g / k / shown[1] for g, k in per_seed if k],
        }
    b.details["segment"] = {
        "label": "derived (observed) and simulated",
        "unit": "s",
        "entry_m": b.variant.entry_m,
        "movements": table,
        "left_out_observed": dict(skipped),
        "left_out_simulated_per_seed": [dict(sk) for _, sk in simulated],
        "arrivals_on_green": arrivals,
    }


def _entries_on_green(
    records: list[SegmentRecord], timeline: SignalTimeline, arm: str
) -> tuple[int, int]:
    """(through entries while the arm's 'all' head is green, entries with a known state)."""
    states = [
        timeline.state_at(arm, r.entry_s)
        for r in records
        if r.approach == arm and r.turn == "through"
    ]
    known = [s for s in states if s not in (None, "unknown")]
    return sum(s == "green" for s in known), len(known)


def _green_share(timeline: SignalTimeline, arm: str) -> float:
    """Share of the window during which the arm's 'all' head shows green."""
    green = sum(
        i.end_s - i.start_s
        for i in timeline.intervals
        if i.arm == arm and i.head == "all" and i.state == "green"
    )
    return green / timeline.window_s


def compare_right_on_red(
    b: Builder, inputs: Inputs, obs: Observed, sims: list[SimSeed], context: dict
) -> None:
    def on_red(rows) -> dict[str, tuple[int, int]]:
        return {
            r["approach"]: (
                r["during_known_state"] - r["during_green_or_yellow"],
                r["during_known_state"],
            )
            for r in rows
            if r["turn"] == "right" and r["during_known_state"]
        }

    observed = on_red(consistency(obs.timeline, obs.world_model.movements))
    simulated = [on_red(consistency(context["timeline"], s.world_model.movements)) for s in sims]
    for arm, (red, known) in observed.items():
        b.add(
            f"right_on_red.{arm}",
            f"Share of right turners crossing the stop line on red, {arm}",
            "share",
            "right turners' stop-line crossings while their arm's 'all' head shows red, over those "
            "with a known state (ngsim_junction.consistency on both sides)",
            b.obs(
                red / known,
                "share",
                n=known,
                interval=wilson_interval(red, known),
                interval_method="Wilson 95% over vehicles",
            ),
            b.sim([s[arm][0] / s[arm][1] if arm in s else None for s in simulated], "share"),
            "comparable-with-caveats",
            [
                SIGNAL_EST,
                "right turn on red is a synthetic switch in SUMO; its gap acceptance is SUMO's",
                "no pedestrians are simulated",
                STOP_REF,
            ],
        )
    through = [
        (r["during_green_or_yellow"], r["during_known_state"])
        for s in sims
        for r in consistency(context["timeline"], s.world_model.movements)
        if r["turn"] == "through"
    ]
    served, known = sum(x for x, _ in through), sum(y for _, y in through)
    inputs.check(
        "simulated through crossings during green or yellow of the rebuilt SUMO program "
        "(share >= 0.99)",
        True,
        bool(known) and served / known >= 0.99,
        fatal=False,
    )
    b.details["program_check"] = {
        "simulated_through_crossings": known,
        "during_green_or_yellow": served,
        "share": round(served / known, 4) if known else None,
    }


def compare_boundary(b: Builder, obs: Observed, sims: list[SimSeed]) -> None:
    limits = [
        "the observed window starts and ends in live traffic; "
        + (
            "SUMO starts empty at 0 s and is measured after a 300 s warm-up, with demand at "
            "the observed rate throughout"
            if b.variant.replay is None
            else "SUMO starts with the vehicles observed on the approaches placed in their "
            "lanes (re-packed at its spacing)"
        ),
        "a single observed window: no interval",
    ]
    b.details["boundary"] = {
        "label": "derived (observed) and simulated",
        "unit": "veh",
        "note": "not compared: the observed cross streets are tracked only from about 25 m, the "
        "simulated ones across the whole ±110 m box",
        "in_box_at_start": {
            "observed": present_at(obs.series, 0.0, 0.05),
            "simulated_per_seed": [present_at(s.series, 0.0, 0.5) for s in sims],
        },
        "in_box_at_end": {
            "observed": present_at(obs.series, b.variant.length - 0.1, 0.05),
            "simulated_per_seed": [present_at(s.series, b.variant.length - 1.0, 0.5) for s in sims],
        },
    }
    for arm in OBSERVABLE:
        for name, pick in (("start", 0), ("end", -1)):
            b.add(
                f"boundary.queued_{name}.{arm}",
                f"Queued vehicles on the {arm} approach at the window {name}",
                "veh",
                "queued vehicles summed over the arm's lanes in the first or last sampled second",
                b.obs(_queued_total(obs.world_model.queues, arm, pick), "veh"),
                b.sim([_queued_total(s.world_model.queues, arm, pick) for s in sims], "veh"),
                "comparable-with-caveats",
                [*limits, CENSORED, "one instant of a cyclic process: depends on the signal phase"],
            )


def compare_paired(b: Builder, inputs: Inputs, obs: Observed, sims: list[SimSeed]) -> None:
    """A replay's vehicles against the tracks they replay: each vehicle's stop-line crossing time,
    simulated less observed, both in window time (details: a per-vehicle check of SUMO's
    approach and discharge, given the observed entry)."""
    table = json.loads((inputs.baseline_dir / "demand" / TRACE_FILE).read_text(encoding="utf-8"))
    track_of = {v["vehicle_id"]: v["track_id"] for v in table["vehicles"]}
    per_seed = [
        paired_lags(obs.world_model.movements, s.world_model.movements, track_of) for s in sims
    ]
    rows = {}
    for key in sorted({k for p in per_seed for k in p}):
        lags = [p.get(key, []) for p in per_seed]
        pooled = [x for seed in lags for x in seed]
        rows[key] = {
            "paired_veh_per_seed": [len(x) for x in lags],
            "median_lag_s_per_seed": [float(np.median(x)) if x else None for x in lags],
            "share_within_5_s_pooled": sum(abs(x) <= 5 for x in pooled) / len(pooled),
            "pooled_s": describe(pooled),
        }
    b.details["paired_stop_line_lag_s"] = {
        "label": "simulated less derived (observed), per vehicle",
        "unit": "s",
        "method": f"vehicles counted in the window on both sides, paired through {TRACE_FILE} "
        "(SUMO vehicle id -> observed track id); lag = simulated stop-line crossing time less "
        "the observed one, both from the window start",
        "observed_counted_veh": sum(m.count_veh for m in obs.world_model.movements),
        "by_movement": rows,
    }


NOT_VALIDATED_REPEATED = [
    {
        "quantity": "mean delay (decision metric: timeLoss + departDelay, metrics.py)",
        "reason": "no observed counterpart: delay needs each vehicle's free-flow travel time over "
        "its whole route, but the observed tracks cover only the ±110 m box (the cross streets "
        "only from about their limit lines) and SUMO's free-flow speed is synthetic",
        "evidence_needed": "observed free-flow speeds and travel times over a longer corridor "
        "(upstream and downstream detectors or tracks); then delay can be defined on both sides",
    },
    {
        "quantity": "queues on the E and W approaches (decision metrics max/mean_queue_m.E/W)",
        "reason": "the E and W approaches are tracked only from about their limit lines "
        "(observed lane depth about 10-12 m beyond the gates); W's stop position is not observable",
        "evidence_needed": "trajectories or video covering the cross-street approaches upstream",
    },
    {
        "quantity": "queue length beyond the observed lane ends on N and S",
        "reason": "observed queues are censored at the end of the observed lanes (about 81-86 m "
        "beyond the gates); any part beyond is unseen",
        "evidence_needed": "upstream coverage beyond the ±110 m box (detectors or tracks)",
    },
    {
        "quantity": "signal timing: green onsets, start-up lost time, cycle lengths",
        "reason": "the observed timeline is estimated from the same stop-line crossings (no head "
        "was read), and the simulated program is cut from that estimate: an input, not an output",
        "evidence_needed": "the controller's timing plan or signal-state logs for 08:30-08:45",
    },
    {
        "quantity": "residual queues after green (cycle failures)",
        "reason": "observed greens are fitted to end just after their last discharge, so the "
        "observed residual share is near 0 by construction (values in details, not compared)",
        "evidence_needed": "observed signal states (see signal timing)",
    },
    {
        "quantity": "arrival flows and throughput as validation (decision metric throughput)",
        "reason": "SUMO's demand is generated from the observed discharge counts, so throughput "
        "agreement is circular; arrivals into the box were not counted separately",
        "evidence_needed": "independent counts (another day or upstream detectors) to test the "
        "model out of sample",
    },
    {
        "quantity": "travel time and stops on the E and W approaches",
        "reason": "not tracked from the segment entry (about 25 m only)",
        "evidence_needed": "upstream coverage of the cross streets",
    },
    {
        "quantity": "pedestrian interaction, vehicle performance and driver parameters",
        "reason": "no pedestrians simulated; accel, decel, minGap and sigma are SUMO defaults, "
        "visible here only through headways and travel times",
        "evidence_needed": "pedestrian counts and phase data; a calibration study",
    },
]


def not_validated(variant: Variant) -> list[dict[str, str]]:
    """What cannot be validated; for a replay, the signal and throughput items say why anew."""
    if variant.replay is None:
        return NOT_VALIDATED_REPEATED
    replay = {
        "signal timing: green onsets, start-up lost time, cycle lengths": (
            "the observed timeline is estimated from the same stop-line crossings (no head was "
            "read), and the replay program is that estimate itself: an input, not an output"
        ),
        "arrival flows and throughput as validation (decision metric throughput)": (
            "the replay's vehicles are the observed ones, entering at their observed states, so "
            "counts and throughput agree one for one by construction"
        ),
    }
    return [{**x, "reason": replay.get(x["quantity"], x["reason"])} for x in NOT_VALIDATED_REPEATED]


# --- Charts (plain SVG; the dataviz reference palette, validated for both modes) -------------

STYLE = (
    "<style>"
    "svg{--surface:#fcfcfb;--text:#0b0b0b;--text2:#52514e;--muted:#898781;--grid:#e1e0d9;"
    "--axis:#c3c2b7;--obs:#2a78d6;--sim:#eb6834}"
    "@media (prefers-color-scheme:dark){svg{--surface:#1a1a19;--text:#f0efec;--text2:#c3c2b7;"
    "--grid:#2c2c2a;--axis:#383835;--obs:#3987e5;--sim:#d95926}}"
    ".bg{fill:var(--surface)}.t{fill:var(--text);font:600 14px system-ui,sans-serif}"
    ".l{fill:var(--text2);font:12px system-ui,sans-serif}"
    ".m{fill:var(--muted);font:11px system-ui,sans-serif}"
    ".g{stroke:var(--grid)}.a{stroke:var(--axis)}.c{stroke:var(--muted)}"
    ".o{fill:var(--obs);stroke:var(--obs)}.s{fill:var(--sim);stroke:var(--sim)}"
    ".ol{stroke:var(--obs);fill:none;stroke-width:2}.sl{stroke:var(--sim);fill:none;opacity:.7}"
    "</style>"
)


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _el(tag: str, body: str = "", **attrs: object) -> str:
    """One SVG element: `cls` is its class, '_' in other names becomes '-'; `body` is markup."""
    text = " ".join(
        f'{"class" if k == "cls" else k.replace("_", "-")}="'
        f'{f"{v:.1f}" if isinstance(v, float) else _esc(str(v))}"'
        for k, v in attrs.items()
    )
    return f"<{tag} {text}>{body}</{tag}>" if body else f"<{tag} {text}/>"


def _open(width: int, height: int, title: str, subtitle: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="{_esc(title)}">',
        STYLE,
        _el("rect", cls="bg", width=width, height=height),
        _el("text", _esc(title), cls="t", x=16, y=24),
        _el("text", _esc(subtitle), cls="m", x=16, y=42),
    ]


def _legend(y: float, observed: str, simulated: str, lines: bool = False) -> list[str]:
    if lines:
        marks = (
            _el("line", cls="ol", x1=16, y1=y - 4, x2=36, y2=y - 4),
            _el("line", cls="sl", x1=376, y1=y - 4, x2=396, y2=y - 4, style="opacity:1"),
        )
    else:
        marks = (
            _el("circle", cls="o", cx=26, cy=y - 4, r=4.5),
            _el("rect", cls="s", x=381.5, y=y - 8.5, width=9, height=9, rx=1),
        )
    return [
        marks[0],
        _el("text", _esc(observed), cls="l", x=42, y=y),
        marks[1],
        _el("text", _esc(simulated), cls="l", x=402, y=y),
    ]


def _ticks(low: float, high: float, n: int = 5) -> list[float]:
    raw = max(high - low, 1e-9) / n
    step = min(
        (m * 10 ** math.floor(math.log10(raw)) for m in (1, 2, 5, 10)), key=lambda s: abs(s - raw)
    )
    return [round(k * step, 10) for k in range(math.floor(low / step), math.ceil(high / step) + 1)]


def dot_chart(title: str, subtitle: str, unit: str, rows: list[dict], scale: float = 1.0) -> str:
    """Estimates with intervals, a row per quantity: observed (circle, 95% interval) above
    simulated (square, seed range). Rows: {label, obs: (value, low, high), lower_bound,
    sim: (mean, min, max)}; values are multiplied by `scale` for display."""
    width, left, right, top, row_h = 760, 230, 40, 84, 30
    bottom = top + row_h * len(rows)
    values = [
        v * scale
        for r in rows
        for t in (r.get("obs"), r.get("sim"))
        if t
        for v in t
        if v is not None
    ]
    ticks = _ticks(min([0.0, *values]), max(values, default=1.0) * 1.05)
    low, high = ticks[0], ticks[-1]

    def x(v: float, scaled: bool = False) -> float:
        return left + ((v if scaled else v * scale) - low) / (high - low) * (width - left - right)

    parts = _open(width, bottom + 46, title, subtitle)
    parts += _legend(64, "Observed (NGSIM, derived; 95% interval)", "Simulated (SUMO; seed range)")
    for t in ticks:
        parts.append(_el("line", cls="g", x1=x(t, True), y1=top - 6, x2=x(t, True), y2=bottom))
        parts.append(
            _el("text", f"{t:g}", cls="m", x=x(t, True), y=bottom + 16, text_anchor="middle")
        )
    parts.append(
        _el("text", _esc(unit), cls="m", x=width - right, y=bottom + 34, text_anchor="end")
    )
    for i, r in enumerate(rows):
        yc = top + row_h * i + row_h / 2
        parts.append(
            _el("text", _esc(r["label"]), cls="l", x=left - 12, y=yc + 4, text_anchor="end")
        )
        for side, y in (("obs", yc - 5), ("sim", yc + 5)):
            if not r.get(side) or r[side][0] is None:
                continue
            v, lo, hi = r[side]
            cls, what = ("o", "observed") if side == "obs" else ("s", "simulated mean")
            tip = f"{r['label']}: {what} {v * scale:.3g}"
            if lo is not None:
                parts.append(_el("line", cls=cls, x1=x(lo), y1=y, x2=x(hi), y2=y, stroke_width=2))
                tip += f", from {lo * scale:.3g} to {hi * scale:.3g}"
            if side == "obs":
                mark = _el("circle", cls="o", cx=x(v), cy=y, r=4.5)
                if r.get("lower_bound"):
                    tip += " (censored: a lower bound)"
                    parts.append(
                        _el("text", "&#8805;", cls="m", x=x(v) - 9, y=y + 4, text_anchor="end")
                    )
            else:
                mark = _el("rect", cls="s", x=x(v) - 4.5, y=y - 4.5, width=9, height=9, rx=1)
            parts.append(f"<g><title>{_esc(tip)}</title>{mark}</g>")
    parts += [_el("line", cls="a", x1=left, y1=bottom, x2=width - right, y2=bottom), "</svg>"]
    return "\n".join(parts) + "\n"


def series_chart(
    title: str, subtitle: str, panels: list[tuple[str, list, list[list]]], window_s: float = 900.0
) -> str:
    """Small multiples over the window, one panel per arm: (name, observed rows, simulated
    rows per seed); rows are (time s, metres, vehicles, censored)."""
    width, left, right, panel_h, top, gap = 760, 56, 24, 170, 96, 44
    span = width - left - right
    height = top + len(panels) * (panel_h + gap) + 10
    parts = _open(width, height, title, subtitle)
    parts += _legend(64, "Observed (NGSIM, derived)", "Simulated (SUMO, a line per seed)", True)
    for p, (name, obs_rows, sim_rows) in enumerate(panels):
        base = top + p * (panel_h + gap) + panel_h
        peak = max(m for rows in (obs_rows, *sim_rows) for _, m, _, _ in rows)
        ticks = _ticks(0, max(peak, 1.0) * 1.05, 4)
        per_m = panel_h / ticks[-1]
        parts.append(_el("text", _esc(name), cls="l", x=left, y=base - panel_h - 8))
        for t in ticks:
            parts.append(
                _el(
                    "line",
                    cls="g",
                    x1=left,
                    y1=base - t * per_m,
                    x2=width - right,
                    y2=base - t * per_m,
                )
            )
            parts.append(
                _el(
                    "text", f"{t:g}", cls="m", x=left - 6, y=base - t * per_m + 4, text_anchor="end"
                )
            )
        for rows, cls, what in (
            *((s, "sl", "simulated seed") for s in sim_rows),
            (obs_rows, "ol", "observed"),
        ):
            points = " ".join(
                f"{left + t / window_s * span:.1f},{base - m * per_m:.1f}" for t, m, _, _ in rows
            )
            parts.append(_el("polyline", f"<title>{what}</title>", cls=cls, points=points))
        for t, _, _, censored in obs_rows:
            if censored:
                xx = left + t / window_s * span
                parts.append(_el("line", cls="c", x1=xx, y1=base + 3, x2=xx, y2=base + 8))
        parts.append(_el("line", cls="a", x1=left, y1=base, x2=width - right, y2=base))
        for t in range(0, int(window_s) + 1, 150):
            parts.append(
                _el(
                    "text",
                    str(t),
                    cls="m",
                    x=left + t / window_s * span,
                    y=base + 22,
                    text_anchor="middle",
                )
            )
    parts.append(
        _el(
            "text",
            "window time, s; ticks under an axis: observed queue censored",
            cls="m",
            x=width - right,
            y=height - 6,
            text_anchor="end",
        )
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def charts(b: Builder) -> dict[str, str]:
    by_key = {c.key: c for c in b.comparisons}

    def row(key: str, label: str) -> dict:
        c = by_key[key]
        o, s = c.observed, c.simulated
        return {
            "label": label + (" (not comparable)" if c.status == "not comparable" else ""),
            "obs": (o.value, *(o.interval or (None, None))),
            "lower_bound": o.lower_bound,
            "sim": (s.value, s.spread.min, s.spread.max) if s.spread and s.spread.n else None,
        }

    out = {}
    out["end_of_red_queue.svg"] = dot_chart(
        "Queue at the end of red",
        "Vehicles queued in the lanes following each head, mean over red ends; "
        "E and W are observed only 10-12 m deep",
        "vehicles",
        [
            row(k, k.split(".", 1)[1].replace("_", " "))
            for k in by_key
            if k.startswith("end_of_red_queue.")
        ],
    )
    out["headways.svg"] = dot_chart(
        "Discharge headways, through lanes",
        "Mean crossing gap by queue position after green (positions observed 3 or more times); "
        "position 1 is left out: observed onsets are estimated",
        "seconds",
        [
            {
                "label": f"{arm} position {r['position']} (n={r['observed_n']})",
                "obs": (r["observed_mean_s"], *(r["observed_ci95_s"] or (None, None))),
                "sim": (r["simulated"]["mean"], r["simulated"]["min"], r["simulated"]["max"])
                if r["simulated"]["n"]
                else None,
            }
            for arm in OBSERVABLE
            for r in b.details["discharge"]["headway_by_position"][arm]
            if r["observed_n"] >= 3
        ],
    )
    out["lane_shares.svg"] = dot_chart(
        "Lane use at the stop line",
        "Share of each approach's counted vehicles by lane (NGSIM lane ids)",
        "percent of the approach",
        [
            row(k, lane if lane != "unknown" else f"{arm} unknown lane")
            for k in by_key
            if k.startswith("lane_share.")
            for _, arm, lane in [k.split(".")]
        ],
        scale=100.0,
    )
    out["travel_time.svg"] = dot_chart(
        "Segment travel time, N and S approaches",
        f"From {b.variant.entry_m:g} m out on the approach to the exit line; mean per movement",
        "seconds",
        [
            row(k, k.split(".", 1)[1].replace("_", " "))
            for k in by_key
            if k.startswith("travel_time.")
        ],
    )
    series = b.details["_series"]
    out["queue_series.svg"] = series_chart(
        "Longest lane queue each second",
        "Observed definition on both sides, simulated lanes cut to "
        "the observed depth; metres from each side's stop line",
        [
            (f"{arm} approach (m)", series["observed"][arm], [s[arm] for s in series["simulated"]])
            for arm in OBSERVABLE
        ],
        b.variant.length,
    )
    return out


# --- Report ---------------------------------------------------------------------------------------


def _fmt(v: float | None, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "share":
        return f"{100 * v:.1f}%"
    return f"{v:,.2f}" if unit == "s" or abs(v) < 10 else f"{v:,.1f}"


def _obs_text(s: Side, unit: str) -> str:
    text = ("≥ " if s.lower_bound else "") + _fmt(s.value, unit)
    if s.interval:
        text += f" [{_fmt(s.interval[0], unit)}, {_fmt(s.interval[1], unit)}]"
    return text + (f" (n={s.n})" if s.n is not None else "")


def _sim_text(s: Side, unit: str) -> str:
    sp = s.spread
    if sp is None or not sp.n:
        return "n/a"
    sd = f" ± {_fmt(sp.sd, unit)}" if sp.sd is not None else ""
    return (
        "≥ " if s.lower_bound else ""
    ) + f"{_fmt(sp.mean, unit)}{sd} ({_fmt(sp.min, unit)}–{_fmt(sp.max, unit)})"


def _disc(c: Comparison) -> str:
    if c.discrepancy is None:
        return "n/a"
    if c.unit == "share":
        return f"{100 * c.discrepancy:+.1f} pp"
    return f"{c.discrepancy:+.2f}"


GROUPS = (
    ("count.", "Movement counts (demand-generator sanity check)"),
    ("lane_share.", "Lane use at the stop line"),
    ("headway.", "Discharge headways"),
    ("discharge_per_green.", "Discharge per green"),
    ("end_of_red_queue.", "Queue at the end of red"),
    ("queue_", "Queue each second (observed definition)"),
    ("travel_time.", "Segment travel time"),
    ("arrival_on_green.", "Arrivals on green at the segment entry"),
    ("stopped_time.", "Stopped time in the segment"),
    ("stops.", "Stops per vehicle"),
    ("stopped_share.", "Stopped fraction"),
    ("right_on_red.", "Right turn on red"),
    ("jam_spacing.", "Jam spacing"),
    ("boundary.", "Window boundaries"),
)


def _paired_lines(paired: dict | None) -> list[str]:
    """The replay's per-vehicle stop-line lags as a markdown section (none for other baselines)."""
    if not paired:
        return []
    lines = [
        "### Same vehicles: stop-line crossing, simulated less observed",
        "",
        paired["method"] + ".",
        "",
        "| Movement | Paired vehicles per seed | Median lag per seed (s) | Within ±5 s (pooled) |",
        "|---|---|---|---|",
    ]
    for key, row in paired["by_movement"].items():
        medians = ", ".join(
            "n/a" if m is None else f"{m:+.1f}" for m in row["median_lag_s_per_seed"]
        )
        lines.append(
            f"| {key} | {', '.join(map(str, row['paired_veh_per_seed']))} | {medians} | "
            f"{100 * row['share_within_5_s_pooled']:.0f}% |"
        )
    return [*lines, ""]


def render_markdown(report: dict, comparisons: list[Comparison]) -> str:
    lines = [
        "# NGSIM intersection 2: SUMO baseline against the observations",
        "",
        f"Created {report['created_at_utc']} by `{report['script']}`. Schema `{SCHEMA}`.",
        "",
        "Decision support only. Observed values are **derived**: CairoFlow's fixed rules over the "
        "published NGSIM positions (CC BY-SA 3.0, U.S. DOT FHWA NGSIM `8ect-6jqj`). Simulated "
        "values come from SUMO output files of the validated baseline, rerun with FCD. Each "
        "quantity has a **status**, not a pass/fail score: *comparable* (same definition and "
        "meaning), *comparable-with-caveats*, or *not comparable*.",
        "",
        *report["variant"]["description"],
        "",
        f"**Summary.** {report['summary']}",
        "",
        "## Inputs and identity checks",
        "",
        "| Check | OK |",
        "|---|---|",
        *[f"| {c['check']} | {'yes' if c['ok'] else '**NO**'} |" for c in report["checks"]],
        "",
        "| Seed | Recorded tripinfo SHA-256 | Rerun (fcd device entries removed) "
        "| Entries removed / trips | Same run |",
        "|---|---|---|---|---|",
        *[
            f"| {r['seed']} | `{r['recorded_tripinfo_sha256'][:16]}…` | "
            f"`{r['rerun_tripinfo_sha256_without_fcd_device'][:16]}…` | "
            f"{r['fcd_device_entries_removed']} / {r['trips']} | "
            f"{'yes' if r['identical'] else '**NO**'} |"
            for r in report["run_identity"]
        ],
        "",
        f"Windows: {report['windows']['observed']}; {report['windows']['simulated']}.",
        "",
        "Simulated stop lines (observed rule on SUMO's stopped vehicles) against SUMO's lane ends "
        "and the observed gates, metres from the centre:",
        "",
        "| Arm | Observed gate | SUMO lane end | Simulated counting line | Pooled heads |",
        "|---|---|---|---|---|",
        *[
            f"| {arm} | {report['geometry']['observed_gates_m'][arm]} | "
            f"{report['geometry']['sumo_stop_lines_m'][arm]} | {g['gate_m']} | "
            f"{g['pooled_heads']} |"
            for arm, g in report["geometry"]["stop_gates"].items()
        ],
        "",
        "## Comparisons",
        "",
        "Observed: estimate [95% interval] (n). Simulated: mean ± SD over seeds (range). "
        "Discrepancy: simulated minus observed. ≥ marks a censored lower bound.",
    ]
    for prefix, heading in GROUPS:
        rows = [c for c in comparisons if c.key.startswith(prefix)]
        if not rows:
            continue
        lines += [
            "",
            f"### {heading}",
            "",
            "| Quantity | Unit | Observed | Simulated | Discrepancy | Status |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {c.quantity} | {c.unit} | {_obs_text(c.observed, c.unit)} | "
            f"{_sim_text(c.simulated, c.unit)} | {_disc(c)} | {c.status} |"
            for c in rows
        ]
        limits = dict.fromkeys(x for c in rows for x in c.limitations)
        lines += ["", "Method: " + rows[0].method + ".", "", "Limitations:"]
        lines += [f"- {x}" for x in limits]
        needed = dict.fromkeys(c.evidence_needed for c in rows if c.evidence_needed)
        lines += [f"- Evidence needed where not comparable: {x}" for x in needed]
    lines += [
        "",
        *_paired_lines(report["details"].get("paired_stop_line_lag_s")),
        "## What cannot be validated",
        "",
        *[
            f"- **{x['quantity']}**: {x['reason']}. *Evidence needed:* {x['evidence_needed']}."
            for x in report["not_validated"]
        ],
        "",
        "## Decision metrics",
        "",
        *[f"- **{k}**: {v}" for k, v in report["decision_metrics"].items()],
        "",
        "## Charts",
        "",
        *[f"![{name}](charts/{name})" for name in report["charts"]],
        "",
        "Every number above is in `validation.json` with its unit, window, method, label and "
        "source files with SHA-256.",
        "",
    ]
    return "\n".join(lines)


def decision_metrics(comparisons: list[Comparison], variant: Variant) -> dict[str, str]:
    status = {c.key: c.status for c in comparisons}
    queue = {arm: status.get(f"queue_max.{arm}", "not comparable") for arm in ARMS}
    return {
        "mean_delay_s": "no observed counterpart. Segment travel time, stopped time and stops on N "
        "and S (same definitions both sides) are partial proxies, comparable with caveats",
        "throughput_veh_per_h": "an observed counterpart exists (stop-line crossings in the "
        "window) "
        + (
            "but it is circular: the demand is generated from it"
            if variant.replay is None
            else "but it is circular one for one: the demand is the observed vehicles"
        ),
        "max_queue_m / mean_queue_m": "no observed counterpart with metrics.py's definition (SUMO "
        "queueing_length from SUMO's stop line). Recomputed with the observed definition: "
        + ", ".join(f"{arm} {queue[arm]}" for arm in ARMS)
        + " (N and S as censored lower bounds on both sides)",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", required=True, type=Path, help="new directory (refused if it exists)"
    )
    parser.add_argument("--baseline", type=Path, default=BASELINE_DIR, help="baseline directory")
    parser.add_argument("--record", type=Path, default=RECORD_DIR, help="committed scenario record")
    parser.add_argument(
        "--timeline",
        type=Path,
        default=None,
        help="timeline the baseline replays (default: record's)",
    )
    args = parser.parse_args(argv)
    out = args.out.expanduser().resolve()
    try:
        new_output_dir(out)
    except FileExistsError as error:
        print(error, file=sys.stderr)
        return 2
    try:
        timeline = args.timeline.resolve() if args.timeline else None
        inputs = load_inputs(args.record.resolve(), args.baseline.resolve(), timeline)
        observed = observed_side(inputs)
    except InputError as error:
        print(f"input check failed: {error}", file=sys.stderr)
        return 2
    print("inputs checked; observed world model rebuilt and identical to the record")
    variant = inputs.variant
    assert variant is not None
    if variant.replay is not None:  # the replay window alone, on the observed side too
        observed = observed_window(observed, inputs, variant.observed)
        print(
            f"replay baseline: comparing clip {variant.observed} s with simulated "
            f"{variant.simulated} s"
        )
    reruns = [
        rerun_seed(inputs, run, out / "sumo" / f"seed_{run['seed']}")
        for run in inputs.manifest["runs"]
    ]
    for r in reruns:
        print(f"seed {r['seed']}: rerun identical to the recorded run: {r['identical']}")
    if not all(r["identical"] for r in reruns):
        (out / "run_identity.json").write_text(
            json.dumps(
                [
                    {k: (v.model_dump() if isinstance(v, Source) else v) for k, v in r.items()}
                    for r in reruns
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        print("a rerun differs from its recorded run; nothing compared", file=sys.stderr)
        return 1
    sims, context = simulated_side(inputs, observed, reruns, out / "sumo")
    print("simulated world models measured")
    b = Builder(
        obs_sources=(
            inputs.sources["trajectories"],
            inputs.sources["geometry.json"],
            inputs.sources["signal_timeline.json"],
            *(
                (inputs.sources["replayed timeline"],)
                if "replayed timeline" in inputs.sources
                else ()
            ),
        ),  # fmt: skip
        sim_sources=(
            *(s.fcd for s in sims),
            inputs.sources["network.net.xml"],
            inputs.sources["baseline_manifest.json"],
        ),
        variant=variant,
    )
    settings = inputs.manifest["settings"]
    begin, end = settings["warmup_s"], settings["warmup_s"] + settings["horizon_s"]
    compare_counts(
        b, observed, sims, demand_in_window(inputs.baseline_dir, inputs.manifest, begin, end)
    )
    compare_lane_shares(b, observed, sims, inputs.geometry)
    compare_discharge(b, inputs, observed, sims, context)
    compare_queues(b, inputs, observed, sims, context)
    compare_segment(b, observed, sims, (observed.timeline, context["timeline"]))
    compare_right_on_red(b, inputs, observed, sims, context)
    compare_boundary(b, observed, sims)
    if variant.replay is not None:
        compare_paired(b, inputs, observed, sims)
    figures = charts(b)
    b.details.pop("_series")
    statuses = Counter(c.status for c in b.comparisons)
    metrics = decision_metrics(b.comparisons, variant)
    summary = (
        f"{len(b.comparisons)} quantities compared with identical definitions: "
        + ", ".join(f"{n} {s}" for s, n in sorted(statuses.items()))
        + f"; {len(not_validated(variant))} listed as not validatable. Decision metrics with "
        "any observed "
        "counterpart: throughput (circular) and the N and S queues (recomputed with the observed "
        "definition, censored lower bounds); mean delay and the E and W queues have none."
    )
    report = {
        "schema": SCHEMA,
        "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "script": "scripts/validate_ngsim_int2_baseline.py",
        "software": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "eclipse-sumo": metadata.version("eclipse-sumo"),
        },
        "summary": summary,
        "labels": {
            "observed": "a value published in the dataset",
            "derived": "CairoFlow's fixed rules over observed values (all NGSIM-side numbers)",
            "simulated": "computed from SUMO output files of the validated baseline",
            "assumed": "an analysis constant chosen here",
        },
        "inputs": {k: v.model_dump() for k, v in inputs.sources.items()},
        "baseline_dir": rel(inputs.baseline_dir),
        "is_recorded_baseline": inputs.is_recorded_baseline,
        "checks": inputs.checks,
        "run_identity": [
            {k: (v.model_dump() if isinstance(v, Source) else v) for k, v in r.items()}
            for r in reruns
        ],
        "assumed_constants": {
            "segment_entry_m": variant.entry_m,
            "saturation_from_position": SATURATION_FROM,
            "restart_speed_m_s": RESTART_M_S,
            "bootstrap_block_s": BLOCK_S,
            "bootstrap_resamples": RESAMPLES,
            "bootstrap_seed": BOOT_SEED,
            "simulated_gate_pad_m": GATE_PAD_M,
            "full_depth_trim_m": TRIM_M,
        },
        "policies": inputs.scenario["policies"],
        "variant": {
            "kind": variant.kind,
            "replay_window_clip_s": variant.replay,
            "observed_window_clip_s": variant.observed,
            "simulated_window_s": variant.simulated,
            "description": variant.header(),
        },
        "windows": {"observed": variant.w_obs, "simulated": variant.w_sim},
        "geometry": {
            "observed_gates_m": context["observed_gates_m"],
            "sumo_stop_lines_m": context["sumo_stop_lines_m"],
            "stop_gates": context["stop_gates"],
            "exit_lines_m": context["exit_lines_m"],
            "sumo_lane_to_ngsim_lane": context["lane_map"],
            "simulated_geometry": json.loads(context["geometry"].model_dump_json()),
        },
        "comparisons": [json.loads(c.model_dump_json()) for c in b.comparisons],
        "details": b.details,
        "not_validated": not_validated(variant),
        "decision_metrics": metrics,
        "charts": sorted(figures),
    }
    (out / "charts").mkdir()
    for name, svg in figures.items():
        (out / "charts" / name).write_text(svg, encoding="utf-8")
    with (out / "validation.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=_json_default)
        handle.write("\n")
    with (out / "validation.md").open("x", encoding="utf-8") as handle:
        handle.write(render_markdown(report, b.comparisons))
    print(summary)
    for c in b.comparisons:
        print(
            f"  {c.key}: observed {_obs_text(c.observed, c.unit)}; simulated "
            f"{_sim_text(c.simulated, c.unit)}; {c.status}"
        )
    print(f"wrote {out / 'validation.json'} and {out / 'validation.md'}")
    return 0


def _json_default(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    if hasattr(value, "model_dump"):
        return value.model_dump()  # type: ignore[union-attr]
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


if __name__ == "__main__":
    raise SystemExit(main())
