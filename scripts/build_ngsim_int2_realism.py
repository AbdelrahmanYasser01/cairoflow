"""Build the replay baseline of NGSIM intersection 2: observed signals and observed vehicles.

    uv run --frozen python scripts/build_ngsim_int2_realism.py [--timeline v2|v2a|v2b] [--record]

The repeated-cycle baseline (DATA_REGISTER.md §16) misses the observed north platoons and repeats
one cut of what looks like actuated control (§17). This one (ADR-022) replays the trajectory-
derived signal timeline over the span where every head is known (`known_window`), and drives
SUMO with the counted vehicles themselves (`cairoflow_simulation.trace_demand`), on the same
committed geometry and compile options. Measurement window = the replay span (warm-up 0); then a
600 s drain. Seeds 1-20 are fixed here, before any result, from the power analysis of §17.3.

Inputs are checked against the committed scenario record (geometry, timeline, processed world
model). `--timeline v2` replays `signal_timeline_v2.json` instead (DATA_REGISTER.md §22), checked
against the hash in `signal_estimate_v2.json`, which must name the record's world model and
version 1 timeline. `--timeline v2a|v2b` replays an ensemble member (ADR-025,
`timeline_ensemble.json`) the same way; its estimate must also name the committed version 2
timeline and record a passed gate (scripts/build_ngsim_int2_timeline_ensemble.py).

Before any run, every version passes the compiled parts of the ensemble's admissibility gate on a
throwaway compile (exit 2 otherwise): no conflicting heads (G2: none of the timeline's seconds,
and no phase giving priority green to two foe links), and the record's replay window, replayed
second by second (G3). These checks change no output. Output: a new directory
experiments/runs/ngsim-int2-realism-baseline-<stamp>/ (Git-ignored, never overwritten) with the
baseline manifest, network, demand, runs and summary.json; with `--record`, the summary also goes
to experiments/records/ (committed). No API call is made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from cairoflow_contracts.evidence import IntersectionGeometry, SignalTimeline, TrafficWorldModel
from cairoflow_contracts.experiment import RunSettings
from cairoflow_evaluation.timeline_gate import (
    conflicting_seconds,
    priority_conflicts,
    program_seconds_differing,
)
from cairoflow_simulation.geometry_compiler import (
    CompileOptions,
    ReplayOptions,
    compile_network,
    known_window,
)
from cairoflow_simulation.runner import MANIFEST_FILE, run_baseline
from cairoflow_simulation.trace_demand import VEHICLES_FILE

REPO = Path(__file__).resolve().parents[1]
RECORD_DIR = REPO / "data/geometry/ngsim-lankershim-int2"
RECORDS_DIR = REPO / "experiments/records"
SEEDS = tuple(range(1, 21))  # pre-registered (DATA_REGISTER.md §17.3): about 20 needed
DRAIN_S = 600.0
VERSIONS = ("v1", "v2", "v2a", "v2b")  # the record's timeline, version 2, ensemble members


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inputs() -> tuple[IntersectionGeometry, SignalTimeline, TrafficWorldModel, dict]:
    """The committed geometry and timeline and the processed world model, hash-checked."""
    scenario = json.loads((RECORD_DIR / "scenario.json").read_text(encoding="utf-8"))
    processed = REPO / scenario["processed_outputs"]["dir"]
    for name, digest in scenario["processed_outputs"]["sha256"].items():
        if sha256_file(processed / name) != digest:
            raise ValueError(f"{processed / name} does not match the scenario record")
    loaded = []
    for name, model in (("geometry.json", IntersectionGeometry),
                        ("signal_timeline.json", SignalTimeline)):  # fmt: skip
        committed = model.model_validate_json((RECORD_DIR / name).read_text(encoding="utf-8"))
        if committed != model.model_validate_json((processed / name).read_text(encoding="utf-8")):
            raise ValueError(f"the committed {name} differs from the processed one")
        loaded.append(committed)  # the files differ in layout only
    world = TrafficWorldModel.model_validate_json(
        (processed / "world_model.json").read_text(encoding="utf-8")
    )
    return loaded[0], loaded[1], world, scenario


def load_timeline_version(scenario: dict, version: str) -> tuple[SignalTimeline, Path]:
    """A committed timeline version other than the record's (`signal_estimate_<version>.json`),
    checked against its estimate record and that record against the scenario record's world model
    and version 1 timeline. An ensemble member (v2a, v2b) must also have been derived beside the
    committed version 2 timeline and have passed its gate."""
    name = f"signal_estimate_{version}.json"
    estimate = json.loads((RECORD_DIR / name).read_text(encoding="utf-8"))
    path = RECORD_DIR / estimate["timeline_file"]
    if sha256_file(path) != estimate["timeline_sha256"]:
        raise ValueError(f"{path} does not match {name}")
    inputs = estimate["inputs"]
    if inputs["world_model_sha256"] != scenario["processed_outputs"]["sha256"]["world_model.json"]:
        raise ValueError(f"{name} was derived from another world model")
    if inputs["signal_timeline_v1_sha256"] != sha256_file(RECORD_DIR / "signal_timeline.json"):
        raise ValueError(f"{name} was derived beside another version 1 timeline")
    if version != "v2":
        if inputs["signal_timeline_v2_sha256"] != sha256_file(
            RECORD_DIR / "signal_timeline_v2.json"
        ):
            raise ValueError(f"{name} was derived beside another version 2 timeline")
        if estimate["gate"]["passed"] is not True:
            raise ValueError(f"{name} records a failed admissibility gate")
    return SignalTimeline.model_validate_json(path.read_text(encoding="utf-8")), path


def load_timeline_v2(scenario: dict) -> tuple[SignalTimeline, Path]:
    """The committed version 2 timeline (DATA_REGISTER.md §22), checked as above."""
    return load_timeline_version(scenario, "v2")


def admissibility(
    geometry: IntersectionGeometry,
    timeline: SignalTimeline,
    reference: SignalTimeline,
    options: CompileOptions,
    replay: ReplayOptions,
) -> list[str]:
    """Why the timeline may not be replayed: the compiled parts of the ensemble's gate (ADR-025),
    on a throwaway compile. G2: conflicting heads in the timeline, or a phase of the replay program
    giving priority green to two foe links. G3: another replay window than the record timeline's
    (other observed vehicles), or a replay program that differs from the timeline."""
    problems = [f"conflicting heads: {found}" for found in conflicting_seconds(timeline)[:5]]
    if replay.window_s != known_window(reference):
        problems.append(
            f"replay window {replay.window_s} differs from the record's {known_window(reference)}"
        )
    with tempfile.TemporaryDirectory(prefix="cairoflow-gate-") as tmp:
        network = compile_network(geometry, timeline, Path(tmp) / "network", options, replay=replay)
        problems += priority_conflicts(network)
        differing = program_seconds_differing(
            network.manifest.signal_program, timeline, replay.window_s
        )
    if differing:
        problems.append(f"the replay program differs from the timeline in {differing} s")
    return problems


def summarise(out: Path, manifest) -> dict[str, object]:
    """Validity and insertion per seed, from the manifest and each run's tripinfo.xml."""
    runs = []
    for run in manifest.runs:
        trips = ET.parse(out / "runs" / run.run_dir / "tripinfo.xml").getroot().iter("tripinfo")
        delays = sorted(float(t.get("departDelay", 0)) for t in trips)
        runs.append({
            "seed": run.seed, "valid": run.valid, "loaded": run.loaded, "arrived": run.arrived,
            "teleports": run.teleports, "collisions": run.collisions,
            "insertion_delay_s": {"median": statistics.median(delays), "max": delays[-1],
                                  "over_5_s": sum(d > 5 for d in delays)},
            "tripinfo_sha256": run.tripinfo_sha256,
        })  # fmt: skip
    table = json.loads((out / "demand" / VEHICLES_FILE).read_text(encoding="utf-8"))
    return {
        "valid": manifest.valid,
        "reasons": list(manifest.reasons),
        "reruns_identical": manifest.reruns_identical,
        "entries": dict(sorted(Counter(v["entry"] for v in table["vehicles"]).items())),
        "demand_notes": list(manifest.demand.notes),
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true", help="write the committed summary")
    parser.add_argument(
        "--timeline", choices=VERSIONS, default="v1", help="signal timeline version to replay"
    )
    args = parser.parse_args(argv)
    try:
        geometry, timeline, world, scenario = load_inputs()
        reference = timeline
        timeline_path = RECORD_DIR / "signal_timeline.json"
        if args.timeline != "v1":
            timeline, timeline_path = load_timeline_version(scenario, args.timeline)
    except (ValueError, FileNotFoundError, KeyError) as error:
        print(f"input check failed: {error}", file=sys.stderr)
        return 2
    window = known_window(timeline)
    settings = RunSettings(seeds=SEEDS, warmup_s=0, horizon_s=window[1] - window[0],
                           drain_s=DRAIN_S)  # fmt: skip
    replay = ReplayOptions(window_s=window, drain_s=DRAIN_S)
    options = CompileOptions.model_validate(scenario["baseline"]["compile_options"])
    problems = admissibility(geometry, timeline, reference, options, replay)
    if problems:
        print("admissibility gate failed: " + "; ".join(problems), file=sys.stderr)
        return 2
    print(f"admissibility (G2, G3): timeline {timeline.timeline_id} passes")
    now = datetime.now(UTC).replace(microsecond=0)
    out = REPO / "experiments/runs" / f"ngsim-int2-realism-baseline-{now:%Y%m%dT%H%M%SZ}"
    manifest = run_baseline(world, geometry, timeline, out, settings, options, replay=replay,
                            now=now)  # fmt: skip
    summary = {
        "script": "scripts/build_ngsim_int2_realism.py",
        "created_at_utc": now.isoformat(),
        "run_dir": str(out.relative_to(REPO)),
        "label": "simulated",
        "inputs": {
            "scenario_record_sha256": sha256_file(RECORD_DIR / "scenario.json"),
            "world_model_sha256": scenario["processed_outputs"]["sha256"]["world_model.json"],
            "signal_timeline": str(timeline_path.relative_to(REPO)),
            "signal_timeline_id": timeline.timeline_id,
            "signal_timeline_sha256": sha256_file(timeline_path),
        },
        "replay": replay.model_dump(),
        "settings": settings.model_dump(),
        "compile_options": options.model_dump(),
        "baseline_manifest_sha256": sha256_file(out / MANIFEST_FILE),
        "experiment_id": manifest.experiment_id,
        **summarise(out, manifest),
    }
    text = json.dumps(summary, indent=2) + "\n"
    with (out / "summary.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    if args.record:
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)
        path = RECORDS_DIR / f"ngsim-int2-realism-baseline-{now:%Y%m%dT%H%M%SZ}.json"
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    print(f"wrote {out.relative_to(REPO)}: valid {manifest.valid}, reruns identical "
          f"{manifest.reruns_identical}, window {window}")  # fmt: skip
    return 0 if manifest.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
