"""Derive version 2 of the NGSIM intersection 2 signal timeline (DATA_REGISTER.md §22).

    uv run --frozen python scripts/build_ngsim_int2_timeline_v2.py [--dry-run]

Version 1 (`signal_timeline.json`, the committed record's) is left as it is. Version 2 applies
`ngsim_junction.SignalRevision`: an arm with no left head adds its permissive left turners to its
green evidence, and each window's yellow starts at its last crossing instead of ending there.
Both rules were fixed before any version 2 result was seen.

Inputs: the committed scenario record and its processed outputs, each hash-checked. The stop-line
crossings come from the processed world model; with version 1's rules they must reproduce the
committed timeline exactly, or nothing is written (exit 2). Output, never overwritten:
`signal_timeline_v2.json` and `signal_estimate_v2.json` (heads, greens, dropped windows,
adjustments, per-movement consistency against version 1, and a conflict check) beside the
record. No network access and no model calls; the timeline is `trajectory-derived`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path

from cairoflow_contracts.evidence import SignalTimeline, TrafficWorldModel
from cairoflow_perception.ngsim_junction import (
    SignalPolicy,
    SignalRevision,
    estimate_signal_timeline,
    heads_conflict,
)
from cairoflow_simulation.geometry_compiler import known_window

REPO = Path(__file__).resolve().parents[1]
RECORD_DIR = REPO / "data/geometry/ngsim-lankershim-int2"
TIMELINE_FILE = "signal_timeline_v2.json"
ESTIMATE_FILE = "signal_estimate_v2.json"
V2_SOURCE = (
    "; version 2 rules (ngsim_junction.SignalRevision, DATA_REGISTER.md §22): an arm with no left "
    "head counts its permissive left turners as green evidence, and each window's yellow starts "
    "at its last crossing"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def greens(timeline: SignalTimeline) -> dict[str, dict[str, float | int]]:
    """Per head: green intervals and their lengths (s)."""
    out: dict[str, dict[str, float | int]] = {}
    for arm, head in timeline.heads():
        lengths = [i.end_s - i.start_s for i in timeline.intervals
                   if i.arm == arm and i.head == head and i.state == "green"]  # fmt: skip
        out[f"{arm} {head}"] = {
            "greens": len(lengths),
            "median_green_s": statistics.median(lengths),
            "min_green_s": min(lengths),
            "max_green_s": max(lengths),
        }
    return out


def conflicting_seconds(timeline: SignalTimeline) -> list[str]:
    """Seconds of the grid where two conflicting heads both show green or yellow."""
    found = []
    for a, b in combinations(timeline.heads(), 2):
        if not heads_conflict(a, b):
            continue
        for t in range(int(timeline.window_s)):
            moving = [timeline.state_at(arm, t + 0.5, head) in ("green", "yellow")
                      for arm, head in (a, b)]  # fmt: skip
            if all(moving):
                found.append(f"{a[0]} {a[1]} and {b[0]} {b[1]} at {t} s")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the summary, write nothing")
    args = parser.parse_args(argv)
    scenario_path = RECORD_DIR / "scenario.json"
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    processed = REPO / scenario["processed_outputs"]["dir"]
    for name in ("world_model.json", "signal_timeline.json"):
        if sha256_file(processed / name) != scenario["processed_outputs"]["sha256"][name]:
            print(f"{processed / name} does not match the scenario record", file=sys.stderr)
            return 2
    v1_path = RECORD_DIR / "signal_timeline.json"
    committed = SignalTimeline.model_validate_json(v1_path.read_text(encoding="utf-8"))
    world = TrafficWorldModel.model_validate_json(
        (processed / "world_model.json").read_text(encoding="utf-8")
    )
    policy = SignalPolicy(**scenario["policies"]["signal"])
    window_id, window_s = scenario["window"]["id"], float(scenario["window"]["duration_s"])
    v1 = estimate_signal_timeline(world.movements, window_id, window_s, committed.timeline_id,
                                  committed.source, policy)  # fmt: skip
    if v1.timeline != committed:
        print("version 1 rules do not reproduce the committed timeline", file=sys.stderr)
        return 2
    revision = SignalRevision()
    v2 = estimate_signal_timeline(world.movements, window_id, window_s,
                                  f"{committed.timeline_id}-v2", committed.source + V2_SOURCE,
                                  policy, revision)  # fmt: skip
    conflicts = conflicting_seconds(v2.timeline)
    shares = {(c["approach"], c["turn"]): c for c in v1.consistency}
    consistency = [
        {**{k: c[k] for k in ("approach", "turn", "head", "crossings")},
         "v1_share": shares[(c["approach"], c["turn"])]["share"], "v2_share": c["share"]}
        for c in v2.consistency
    ]  # fmt: skip
    text = v2.timeline.model_dump_json(indent=2) + "\n"
    record = {
        "script": "scripts/build_ngsim_int2_timeline_v2.py",
        "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "label": "trajectory-derived",
        "inputs": {
            "scenario_record_sha256": sha256_file(scenario_path),
            "world_model_sha256": scenario["processed_outputs"]["sha256"]["world_model.json"],
            "signal_timeline_v1_sha256": sha256_file(v1_path),
        },
        "reproduces_v1": True,
        "policy": policy.model_dump(),
        "revision": revision.model_dump(),
        "timeline_id": v2.timeline.timeline_id,
        "timeline_file": TIMELINE_FILE,
        "timeline_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "known_window_s": {
            "v1": list(known_window(committed)),
            "v2": list(known_window(v2.timeline)),
        },
        "conflicting_seconds": conflicts,
        "heads": v2.heads,
        "greens": {"v1": greens(committed), "v2": greens(v2.timeline)},
        "dropped": list(v2.dropped),
        "adjustments": list(v2.adjustments),
        "consistency": consistency,
        "representative_cycle": v2.representative_cycle,
    }
    shown = ("known_window_s", "conflicting_seconds", "heads", "greens", "dropped", "consistency")
    summary = json.dumps({k: record[k] for k in shown}, indent=1)
    if args.dry_run:
        print(summary)
        return 0
    if conflicts:
        print(f"conflicting heads: {conflicts[:5]}", file=sys.stderr)
        return 1
    with (RECORD_DIR / TIMELINE_FILE).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    with (RECORD_DIR / ESTIMATE_FILE).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, indent=2) + "\n")
    print(f"wrote {TIMELINE_FILE} ({record['timeline_sha256']}) and {ESTIMATE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
