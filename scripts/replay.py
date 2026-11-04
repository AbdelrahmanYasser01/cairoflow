"""Rerun the whole CairoFlow flow offline from recorded inputs and cached model output (A10).

    uv run --frozen python scripts/replay.py INPUTS.json OUT_DIR [--reference RECORD.json]

Replay mode never opens a network connection and needs no API key; a missing SAM or Muse cache
entry is an error. OUT_DIR must not exist. With --reference, every artifact and SUMO tripinfo
hash is compared with the recorded run (which must itself be a replay-mode record); the exit
status is 1 on any mismatch and 0 when the rerun is identical.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cairoflow_evaluation.pipeline import PipelineRecord, compare_records, run_pipeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("inputs", type=Path, help="pipeline_inputs.json")
    parser.add_argument("out_dir", type=Path, help="new directory for this run")
    parser.add_argument("--reference", type=Path, help="pipeline_record.json to compare with")
    args = parser.parse_args(argv)

    reference = (
        PipelineRecord.model_validate_json(args.reference.read_text(encoding="utf-8"))
        if args.reference
        else None
    )
    record = run_pipeline(args.inputs, args.out_dir, mode="replay")
    print(
        f"run {record.run_name}: {len(record.artifacts)} artifacts, "
        f"{len(record.tripinfo)} SUMO runs, selected {record.selected_intervention_id}"
    )
    if reference is None:
        print(f"record written to {args.out_dir / 'pipeline_record.json'}")
        return 0
    mismatches = compare_records(reference, record)
    for mismatch in mismatches:
        print(f"MISMATCH {mismatch}")
    print("identical to the reference" if not mismatches else f"{len(mismatches)} mismatches")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
