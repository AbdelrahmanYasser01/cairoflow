"""Run a SYNTHETIC known-answer test of the evaluation and selection stage, with positive and
negative controls (cairoflow_evaluation.known_answer).

    uv run --frozen python scripts/run_synthetic_known_answer.py --scenario reallocation --record
    uv run --frozen python scripts/run_synthetic_known_answer.py --scenario long-cycle --record

What is tested: the deterministic validator, paired SUMO runs, eligibility, paired comparisons,
Holm's correction and the selection rule, on candidates whose answer is known in advance. What is
not tested: discovery. No proposer runs and Muse is not exercised; the correction is the
pre-registered answer itself, written by code.

Scenarios (each separately pre-registered; see SCENARIO_TEXT in the module):
- `reallocation` (default): green allocated the wrong way round at a fixed cycle. The correction
  is Webster's split at that cycle, a trade-off between the two axes.
- `long-cycle`: the right split on a cycle far longer than the demand needs. The correction is
  Webster's optimum cycle and split, which shortens every approach's red time. Designed after
  seeing the reallocation result, to separate "does the stage select a known Pareto improvement"
  from "the selection rule refuses trade-offs".

Candidates in each scenario, all through the same validator and pipeline, with shuffled ids:
- the correction (positive control);
- a half-way plan;
- the exact null (the baseline plan; the validator is expected to reject it);
- the mirror of the correction (negative control);
- 8 small random perturbations (+/-5 s per green) of the deliberately bad plan, which are not
  nulls.

Pre-registration and provenance: everything is written to `preregistration.json` and hashed
before the first SUMO run. The run records:
- the git commit, and whether the files it imports are clean against it, at its start and end;
- the pre-registration's file hash before SUMO starts and after the run.

Output: `experiments/runs/synthetic-known-answer-<scenario>-<stamp>/` (Git-ignored; no
pipeline_record.json, so the report app does not list it next to the NGSIM runs) and, with
`--record`, `experiments/records/synthetic-known-answer-<scenario>-<stamp>.json`. Nothing is
overwritten. No network access, no model request, no `.env`. SYNTHETIC: it says nothing about the
NGSIM baseline or any real junction.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from cairoflow_contracts.experiment import RunSettings
from cairoflow_evaluation.known_answer import (
    DEFAULT_CONTROL_SEED,
    DEFAULT_SETTINGS,
    NAME,
    SCENARIOS,
    run_known_answer,
)

REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="reallocation")
    parser.add_argument("--seeds", type=int, default=len(DEFAULT_SETTINGS.seeds),
                        help="paired seeds 1..N (default 20)")  # fmt: skip
    parser.add_argument("--controls", type=int, default=8,
                        help="small random perturbations of the bad plan (default 8)")  # fmt: skip
    parser.add_argument("--control-seed", type=int, default=DEFAULT_CONTROL_SEED)
    parser.add_argument("--no-halfway", action="store_true", help="leave out the half-way plan")
    parser.add_argument("--record", action="store_true", help="write the record (new file)")
    args = parser.parse_args(argv)

    now = datetime.now(UTC).replace(microsecond=0)
    stamp = f"{NAME}-{args.scenario}-{now:%Y%m%dT%H%M%SZ}"
    settings = RunSettings.model_validate(
        DEFAULT_SETTINGS.model_dump() | {"seeds": tuple(range(1, args.seeds + 1))}
    )
    summary = run_known_answer(
        REPO / "experiments/runs" / stamp,
        design=SCENARIOS[args.scenario],
        settings=settings,
        controls=args.controls,
        control_seed=args.control_seed,
        include_halfway=not args.no_halfway,
        now=now,
        record_path=REPO / "experiments/records" / f"{stamp}.json" if args.record else None,
        repo_root=REPO,
    )
    prereg, provenance = summary["preregistration"], summary["provenance"]
    print(f"run directory: {summary['run_dir']}")
    print(f"commit {provenance['git_at_start'].get('commit')}; imported files clean at start and "
          f"end: {provenance['clean_against_commit_at_start_and_end']}")  # fmt: skip
    print(f"pre-registration file sha256 {prereg['file_sha256']}, content sha256 "
          f"{prereg['content_sha256']}")  # fmt: skip
    print(f"pre-registration unchanged after the run: {prereg['unchanged_after_run']}")
    if "error" in summary:
        print(f"stopped: {summary['error']}")
        return 1
    print(summary["outcome"]["conclusion"])
    print(f"trace problems: {len(summary['report']['trace_problems'])}; "
          f"wall clock {summary['runtime']['wall_clock_s']} s")  # fmt: skip
    if args.record:
        print(f"record: experiments/records/{stamp}.json")
    return 0 if not summary["report"]["trace_problems"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
