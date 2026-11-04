"""Derive the two new members of the intersection 2 signal-timeline ensemble (ADR-025).

    uv run --frozen python scripts/build_ngsim_int2_timeline_ensemble.py [--dry-run]

The committed versions 1 and 2 differ by two rules (DATA_REGISTER.md §22): whether an arm with no
left head counts its permissive left turners as green evidence (version 1 no, version 2 yes), and
where each window's yellow sits (version 1 ends it at the last crossing, version 2 starts it
there). The other two corners of that 2 x 2 are the new members, fixed in
`timeline_ensemble.json` before any candidate is simulated on them:
- v2a: permissive lefts counted, yellow ending at the last crossing;
- v2b: permissive lefts not counted (as version 1), yellow starting at the last crossing.
Both are `ngsim_junction.SignalRevision` with its two flags set independently; the estimator, the
policy and the world model are the committed ones.

Gate (timeline_ensemble.json); nothing is written unless every member passes (exit 2):
- G1: the same code and inputs reproduce the committed version 1 and version 2 timelines byte for
  byte (`signal_timeline.json`; `signal_timeline_v2.json` against its estimate's hash);
- G2: no second in which two conflicting heads both show green or yellow;
- G3, timeline part: the known window equals version 1's (the same observed vehicles are
  replayed), and the recorded cut-cycle start still compiles (it drives the drain);
- G4: in that window, no through or left movement has more crossings shown red than version 1.
The compiled-program parts of G2 and G3, and G5 (a valid replay baseline on seeds 1-20), are
checked when the baseline is built (scripts/build_ngsim_int2_realism.py --timeline v2a|v2b).

Inputs, each hash-checked: the scenario record, its processed world model and timeline, the
committed version 1 timeline and the version 2 estimate and timeline. Output, never overwritten:
`signal_timeline_<member>.json` and `signal_estimate_<member>.json` beside the record (the version
2 estimate's format, plus the gate's results). No network access, no model call and no
simulation; the timelines are `trajectory-derived`.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from cairoflow_contracts.evidence import SignalTimeline, TrafficWorldModel
from cairoflow_evaluation.timeline_gate import conflicting_seconds, more_red_than, red_crossings
from cairoflow_perception.ngsim_junction import (
    SignalPolicy,
    SignalRevision,
    estimate_signal_timeline,
)
from cairoflow_simulation.geometry_compiler import CompileError, known_window, signal_cycle

REPO = Path(__file__).resolve().parents[1]
RECORD_DIR = REPO / "data/geometry/ngsim-lankershim-int2"
SCRIPT = "scripts/build_ngsim_int2_timeline_ensemble.py"
V2_ESTIMATE = "signal_estimate_v2.json"
MEMBERS: dict[str, tuple[SignalRevision, str]] = {
    "v2a": (
        SignalRevision(
            version="2a", permissive_left_evidence=True, yellow_from_last_crossing=False
        ),
        "; ensemble member v2a (ngsim_junction.SignalRevision, timeline_ensemble.json): an arm "
        "with no left head counts its permissive left turners as green evidence (as version 2), "
        "and each window's yellow ends at its last crossing (as version 1)",
    ),
    "v2b": (
        SignalRevision(
            version="2b", permissive_left_evidence=False, yellow_from_last_crossing=True
        ),
        "; ensemble member v2b (ngsim_junction.SignalRevision, timeline_ensemble.json): permissive "
        "left turners count only where an arm's throughs are too few (as version 1), and each "
        "window's yellow starts at its last crossing (as version 2)",
    ),
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def timeline_text(timeline: SignalTimeline) -> str:
    """A timeline file's exact bytes, as the committed versions were written."""
    return timeline.model_dump_json(indent=2) + "\n"


def version_2_script() -> ModuleType:
    """scripts/build_ngsim_int2_timeline_v2.py, for its source text and summaries (one path)."""
    path = REPO / "scripts" / "build_ngsim_int2_timeline_v2.py"
    spec = importlib.util.spec_from_file_location("build_ngsim_int2_timeline_v2", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the gate, write nothing")
    args = parser.parse_args(argv)
    v2_script = version_2_script()
    scenario_path = RECORD_DIR / "scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    processed = REPO / scenario["processed_outputs"]["dir"]
    for name in ("world_model.json", "signal_timeline.json"):
        if sha256_file(processed / name) != scenario["processed_outputs"]["sha256"][name]:
            print(f"{processed / name} does not match the scenario record", file=sys.stderr)
            return 2
    v1_path = RECORD_DIR / "signal_timeline.json"
    committed = SignalTimeline.model_validate_json(v1_path.read_text(encoding="utf-8"))
    v2_estimate_path = RECORD_DIR / V2_ESTIMATE
    v2_estimate = json.loads(v2_estimate_path.read_text(encoding="utf-8"))
    v2_path = RECORD_DIR / v2_estimate["timeline_file"]
    world_sha256 = scenario["processed_outputs"]["sha256"]["world_model.json"]
    if v2_estimate["inputs"]["world_model_sha256"] != world_sha256 or v2_estimate["inputs"][
        "signal_timeline_v1_sha256"
    ] != sha256_file(v1_path):
        print(f"{V2_ESTIMATE} was derived from other inputs", file=sys.stderr)
        return 2
    world = TrafficWorldModel.model_validate_json(
        (processed / "world_model.json").read_text(encoding="utf-8")
    )
    policy = SignalPolicy(**scenario["policies"]["signal"])
    window_id, window_s = scenario["window"]["id"], float(scenario["window"]["duration_s"])

    def estimate(timeline_id: str, source: str, revision: SignalRevision | None):
        return estimate_signal_timeline(world.movements, window_id, window_s, timeline_id, source,
                                        policy, revision)  # fmt: skip

    # G1: the committed versions, byte for byte
    v1 = estimate(committed.timeline_id, committed.source, None)
    v2 = estimate(f"{committed.timeline_id}-v2", committed.source + v2_script.V2_SOURCE,
                  SignalRevision())  # fmt: skip
    reproduces_v1 = timeline_text(v1.timeline).encode("utf-8") == v1_path.read_bytes()
    reproduces_v2 = (
        sha256_text(timeline_text(v2.timeline))
        == v2_estimate["timeline_sha256"]
        == sha256_file(v2_path)
    )
    if not (reproduces_v1 and reproduces_v2):
        print(f"G1 failed: reproduces version 1 {reproduces_v1}, version 2 {reproduces_v2}",
              file=sys.stderr)  # fmt: skip
        return 2

    reference_window = known_window(committed)
    reference_red = red_crossings(committed, world.movements, reference_window)
    cycle_start = scenario["baseline"]["compile_options"]["cycle_start_s"]
    now = datetime.now(UTC).replace(microsecond=0)
    outputs: dict[Path, str] = {}
    failed: list[str] = []
    gates: dict[str, dict[str, object]] = {}
    for member, (revision, extra_source) in MEMBERS.items():
        found = estimate(f"{committed.timeline_id}-{member}", committed.source + extra_source,
                         revision)  # fmt: skip
        timeline = found.timeline
        conflicts = conflicting_seconds(timeline)
        window = known_window(timeline)
        try:
            signal_cycle(timeline, cycle_start)
            cut = "compiles"
        except CompileError as error:
            cut = f"fails: {error}"
        red = red_crossings(timeline, world.movements, reference_window)
        worse = more_red_than(red, reference_red)
        gate = {
            "G1_reproduces_committed_v1_and_v2": True,
            "G2_conflicting_seconds": conflicts,
            "G3_known_window_s": list(window),
            "G3_reference_window_s": list(reference_window),
            "G3_cut_cycle_start_s": cycle_start,
            "G3_cut_cycle": cut,
            "G4_red_crossings_in_window": {k: list(v) for k, v in red.items()},
            "G4_v1_red_crossings_in_window": {k: list(v) for k, v in reference_red.items()},
            "G4_more_red_than_v1": worse,
            "passed": not conflicts and window == reference_window and cut == "compiles"
            and not worse,
            "not_checked_here": "G2 and G3 on the compiled replay program, and G5 (a valid "
            "replay baseline), at the baseline build",
        }  # fmt: skip
        gates[member] = gate
        if not gate["passed"]:
            failed.append(member)
        text = timeline_text(timeline)
        timeline_file = f"signal_timeline_{member}.json"
        shares = {(c["approach"], c["turn"]): c for c in v1.consistency}
        record = {
            "script": SCRIPT,
            "created_at_utc": now.isoformat(),
            "label": "trajectory-derived",
            "inputs": {
                "scenario_record_sha256": sha256_file(scenario_path),
                "world_model_sha256": world_sha256,
                "signal_timeline_v1_sha256": sha256_file(v1_path),
                "signal_timeline_v2_sha256": sha256_file(v2_path),
                "signal_estimate_v2_sha256": sha256_file(v2_estimate_path),
            },
            "reproduces_v1": True,
            "reproduces_v2": True,
            "ensemble_record": "timeline_ensemble.json",
            "policy": policy.model_dump(),
            "revision": revision.model_dump(),
            "timeline_id": timeline.timeline_id,
            "timeline_file": timeline_file,
            "timeline_sha256": sha256_text(text),
            "known_window_s": {"v1": list(reference_window), member: list(window)},
            "conflicting_seconds": conflicts,
            "heads": found.heads,
            "greens": {"v1": v2_script.greens(committed), member: v2_script.greens(timeline)},
            "dropped": list(found.dropped),
            "adjustments": list(found.adjustments),
            "consistency": [
                {
                    **{k: c[k] for k in ("approach", "turn", "head", "crossings")},
                    "v1_share": shares[(c["approach"], c["turn"])]["share"],
                    f"{member}_share": c["share"],
                }
                for c in found.consistency
            ],  # fmt: skip
            "representative_cycle": found.representative_cycle,
            "gate": gate,
        }
        outputs[RECORD_DIR / timeline_file] = text
        outputs[RECORD_DIR / f"signal_estimate_{member}.json"] = json.dumps(record, indent=2) + "\n"

    print("G1: the committed version 1 and version 2 timelines are reproduced byte for byte")
    for member, gate in gates.items():
        text = outputs[RECORD_DIR / f"signal_timeline_{member}.json"]
        print(f"{member}: sha256 {sha256_text(text)}; gate passed {gate['passed']} (G2 "
              f"{len(gate['G2_conflicting_seconds'])} conflicting seconds; G3 window "
              f"{gate['G3_known_window_s']}, cut {gate['G3_cut_cycle']}; G4 more red than v1: "
              f"{gate['G4_more_red_than_v1'] or 'none'})")  # fmt: skip
    if failed:
        print(f"gate failed for {', '.join(failed)}; nothing written", file=sys.stderr)
        return 2
    if args.dry_run:
        return 0
    existing = [path.name for path in outputs if path.exists()]
    if existing:
        print(f"refusing to overwrite {', '.join(existing)}", file=sys.stderr)
        return 2
    for path, text in outputs.items():
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    print("wrote " + ", ".join(path.name for path in outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
