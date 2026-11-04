"""Compare a camera analysis with the published trajectories, against a registered gate.

    # reads the trajectory table: about 30 s and 1 GB
    .venv/bin/python scripts/cv_gate.py \
        --gate data/gates/<file>.json --analysis data/analyses/<id> --split dev

--split dev scores the even blocks of the analysis and can be repeated freely. --split held_out
scores the odd blocks and is refused unless the gate file is committed and clean and the analysis is
the one that gate's read named (scripts/cv_analyse.py --held-out-gate); it logs the score to
data/gates/held_out_reads.jsonl before any held-out row is touched, and a second one needs a new
gate version. The gate file's schema is in cairoflow_evaluation.cv_agreement.

Writes experiments/records/cv-gate-<gate_id>-<analysis_id>-<split>-<UTC stamp>/results.json, never
overwriting. Exit 0 when every check passes, 1 when one fails, 2 when the read is refused.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("perception", "contracts", "evaluation"):
    path = str(ROOT / "packages" / package)
    if path not in sys.path:
        sys.path.insert(0, path)

from cairoflow_contracts.analysis_record import GateOutcome  # noqa: E402
from cairoflow_evaluation import cv_agreement as A  # noqa: E402
from cairoflow_perception.analysis_store import verify_analysis  # noqa: E402
from cairoflow_perception.local import scoring as S  # noqa: E402

EXIT_FAILED, EXIT_REFUSED = 1, 2


def run(args: argparse.Namespace) -> int:
    gate, gate_sha256 = A.load_gate(args.gate)
    record = verify_analysis(args.analysis)
    if record.data_origin != "camera_cv":
        raise A.GateRefused(f"{record.analysis_id} is not camera output: nothing to compare")
    if record.calibration.mode != "calibrated":
        raise A.GateRefused(f"{record.analysis_id} is uncalibrated: it has no ground to compare")
    frames = A.split_frames(record, args.split)
    if not frames:
        raise A.GateRefused(f"{record.analysis_id} has no frame in the {args.split} blocks")
    read = None
    commit = None
    if args.split == "held_out":
        commit = A.require_committed(ROOT, args.gate)
        read = A.claim_held_out_score(args.read_log, gate, gate_sha256, record.analysis_id)
    keys = sorted(A.frame_key(record, frame) for frame in frames)
    compared = A.compare(A.load_run(args.analysis), A.load_reference(ROOT, keys), frames)
    outcomes = A.evaluate_gate(gate, gate_sha256, compared["metrics"], args.split)
    passed = all(outcome.passed for outcome in outcomes)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = args.out / f"cv-gate-{gate.gate_id}-{record.analysis_id}-{args.split}-{stamp}"
    payload = {
        "gate_id": gate.gate_id,
        "gate_sha256": gate_sha256,
        "gate_commit": commit,
        "analysis_id": record.analysis_id,
        "split": args.split,
        "block_formula": A.BLOCK_FORMULA,
        "blocks": sorted({int(block) for block in frames.values()}),
        "evidence_class": A.EVIDENCE_CLASS,
        "held_out_read": read,
        "passed": passed,
        "outcomes": [outcome.model_dump(mode="json") for outcome in outcomes],
        **compared,
    }
    path = S.write_record(directory, "results.json", payload)
    print_summary(outcomes, compared)
    print(("PASSED" if passed else "FAILED"), "->", path)
    return 0 if passed else EXIT_FAILED


def print_summary(outcomes: tuple[GateOutcome, ...], compared: dict) -> None:
    for name, metric in compared["metrics"].items():
        print(f"{name}: {metric['value']} (n {metric['n']})")
    for outcome in outcomes:
        verdict = "pass" if outcome.passed else "FAIL"
        note = f" ({outcome.note})" if outcome.note else ""
        print(
            f"{verdict} {outcome.metric}: {outcome.measured} {outcome.comparator} "
            f"{outcome.threshold}{note}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "held_out"), required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "experiments/records")
    parser.add_argument("--read-log", type=Path, default=ROOT / A.READ_LOG)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except A.GateRefused as refused:
        print(f"REFUSED: {refused}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
