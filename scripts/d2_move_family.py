"""Recompute the D2 move family on the recorded inputs and print it as JSON (read-only).

The approved D2 rule (ADR-033 decision 8) searches a menu of one-step timing moves. This prints
that menu as tracked code computes it (cairoflow_simulation.d2_moves): moves generated, accepted
by the validator, distinct, mapped onto each timeline member, and mapped onto all of them, with
`menu_sha256` over the final menu. Inputs: the cut baseline of scenario.json, the evidence bundle
of the committed Muse request (the bundle the validator checks citations against) and the four
members of the timeline ensemble record. No simulation, no API call, nothing written.

    uv run --frozen python scripts/d2_move_family.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from cairoflow_contracts.reasoning import EvidenceBundle
from cairoflow_reasoning.muse_adapter import MuseRequestRecord
from cairoflow_simulation.candidates import load_baseline
from cairoflow_simulation.d2_moves import (
    MOVES_VERSION,
    STEPS_S,
    fit_members,
    kind_counts,
    menu_sha256,
    screen_moves,
)

REPO = Path(__file__).resolve().parents[1]
SCENARIO = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
MUSE = REPO / "cache/muse/ddfb8029b1e8810e65b2ad986e0da99be630b86e6d7c86669007e2a197ef47ad.json"
ENSEMBLE = REPO / "experiments/records/ngsim-int2-timeline-ensemble-20261029T123045Z.json"
SEPARATOR = "\nEVIDENCE (JSON):\n"


def bundle_of(record: Path) -> EvidenceBundle:
    """The evidence bundle a recorded Muse request carried."""
    reply = MuseRequestRecord.model_validate_json(record.read_text(encoding="utf-8"))
    _, found, evidence = str(reply.body["input"]).partition(SEPARATOR)
    if not found:
        raise ValueError(f"{record.name}: the request holds no evidence bundle")
    return EvidenceBundle.model_validate_json(evidence)


def member_baselines(ensemble: Path) -> dict[str, Path]:
    """Member id -> its baseline directory, as the member's experiment manifest names it."""
    found = {}
    for member in json.loads(ensemble.read_text(encoding="utf-8"))["members"]:
        base = REPO / member["experiment_dir"]
        paths = [base / "experiment/experiment_manifest.json", base / "experiment_manifest.json"]
        manifest = next((p for p in paths if p.is_file()), None)
        if manifest is None:
            raise FileNotFoundError(f"member {member['id']}: no experiment manifest under {base}")
        baseline = json.loads(manifest.read_text(encoding="utf-8"))["baseline_dir"]
        # not resolved: in a linked checkout the run directories are symlinks into the main checkout
        found[member["id"]] = Path(os.path.abspath(manifest.parent / baseline))
    return found


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute(scenario: Path, muse: Path, ensemble: Path) -> dict:
    cut_dir = REPO / json.loads(scenario.read_text(encoding="utf-8"))["baseline"]["run_dir"]
    _, network = load_baseline(cut_dir)
    bundle = bundle_of(muse)
    cut = network.manifest.signal_program
    screen = screen_moves(network, bundle)
    baselines = member_baselines(ensemble)
    replays = {m: load_baseline(d)[1].manifest.signal_program for m, d in baselines.items()}
    fits, menu = fit_members(screen, cut, replays)
    return {
        "moves_version": MOVES_VERSION,
        "steps_s": list(STEPS_S),
        "inputs": {
            "cut_baseline": cut_dir.relative_to(REPO).as_posix(),
            "cut_program_id": cut.program_id,
            "cut_cycle_s": cut.cycle_s,
            "net_sha256": network.manifest.net_sha256,
            "bundle_id": bundle.bundle_id,
            "ensemble_record_sha256": sha256_file(ensemble),
            "member_baselines": {m: d.relative_to(REPO).as_posix() for m, d in baselines.items()},
        },
        "adjustable_phases": [p.name for p in cut.phases if p.adjustable],
        "generated": kind_counts(screen.generated),
        "accepted_distinct": kind_counts([m.move for m in screen.accepted]),
        "duplicates": len(screen.duplicates),
        "rejected_by_reason": dict(screen.rejected),
        "members": {
            f.member: {
                "mapped": len(f.mapped),
                "clamped": len(f.clamped),
                "unmapped": len(f.unmapped),
            }
            for f in fits
        },  # fmt: skip
        "mapped_on_all_members": kind_counts([m.move for m in menu]),
        "menu": [m.move.label for m in menu],
        "menu_sha256": menu_sha256(menu),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", type=Path, default=SCENARIO)
    parser.add_argument("--muse-record", type=Path, default=MUSE)
    parser.add_argument("--ensemble", type=Path, default=ENSEMBLE)
    args = parser.parse_args(argv)
    try:
        result = compute(args.scenario, args.muse_record, args.ensemble)
    except (OSError, ValueError, KeyError) as error:
        print(f"cannot compute the D2 move family: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
