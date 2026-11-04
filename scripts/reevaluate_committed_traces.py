"""Trace every committed record with an empty trace again under the stricter check.

    uv run --frozen python scripts/reevaluate_committed_traces.py [--record] [--out PATH]

Each committed record in experiments/records whose report trace was empty (`trace_problems: []`)
is traced again by `cairoflow_evaluation.report.retrace_record` from its run directory: metrics
recomputed from the SUMO files, the report it names hashed and its selection, recommendation,
eligibility, comparisons and Holm tests re-derived, the report traced again from its inputs, and
the pairs rebuilt. Each record gets verified, failed (with every problem) or unverifiable (with
why); none is dropped and no record is edited. A committed record's `trace_problems: []` stands
qualified by this outcome.

The outcome is printed. With `--record` it is also written as a new committed record,
experiments/records/retrace-<stamp>.json; with `--out` to the path given. Neither overwrites. Run
directories and data/processed are Git-ignored, so on a fresh clone every record is
unverifiable until its experiment is run again. Exit status: 0 when no record failed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from cairoflow_evaluation.eligibility import RULES_VERSION
from cairoflow_evaluation.metrics import CALCULATION_VERSION
from cairoflow_evaluation.report import empty_trace_records, retrace_records

REPO = Path(__file__).resolve().parents[1]
RECORDS_DIR = REPO / "experiments/records"
SCRIPT = "scripts/reevaluate_committed_traces.py"


def write_new(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _git_head(root: Path) -> str | None:
    try:
        found = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                               capture_output=True, text=True)  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return None
    return found.stdout.strip()


def outcome(records_dir: Path, root: Path, now: datetime) -> dict:
    """The retrace of every record in `records_dir` with an empty trace, as it is recorded."""
    paths = empty_trace_records(records_dir)
    results = retrace_records(paths, root)
    return {
        "script": SCRIPT,
        "created_at_utc": now.isoformat(),
        "label": "re-derived by code from the recorded SUMO outputs and inputs",
        "code": {
            "git_head": _git_head(root),
            "rules_version": RULES_VERSION,
            "calculation_version": CALCULATION_VERSION,
        },  # fmt: skip
        "records_traced": len(results),
        "statuses": dict(sorted(Counter(r["status"] for r in results).items())),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true", help="write a new committed record")
    parser.add_argument("--out", type=Path, help="also write the outcome to this new file")
    parser.add_argument("--records-dir", type=Path, default=RECORDS_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    now = datetime.now(UTC).replace(microsecond=0)
    found = outcome(args.records_dir, args.root, now)
    text = json.dumps(found, indent=2) + "\n"
    if args.out:
        write_new(args.out, text)
    if args.record:
        write_new(RECORDS_DIR / f"retrace-{now:%Y%m%dT%H%M%SZ}.json", text)
    for result in found["results"]:
        print(f"{result['status'].upper():13} {result['record']}")
        for line in (*result["problems"], *result["missing"], *result["notes"]):
            print(f"    {line}")
    print(f"{found['records_traced']} records: {found['statuses']}")
    return 1 if found["statuses"].get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
