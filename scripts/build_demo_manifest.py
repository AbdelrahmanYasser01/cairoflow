"""Pin the committed records the demo page shows: write data/demo/<demo id>.json.

    uv run --frozen python scripts/build_demo_manifest.py

Each file below is pinned by its SHA-256. Before writing, the manifest is loaded exactly as the
app will load it (`cairoflow_api.demo.load_demo`): every pin and every link between the records
must hold, or nothing is written (exit 2). An existing manifest is never overwritten; pinning a
newer set of records means a new demo id. No network access and no model calls.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from cairoflow_api.demo import DemoError, DemoManifest, PinnedFile, load_demo

REPO = Path(__file__).resolve().parents[1]
DEMO_ID = "ngsim-int2-ensemble-control"  # earlier: ...-replay-timeline-v2(-sam(-a3))
TITLE = (
    "Lankershim Boulevard at Universal Hollywood Drive (NGSIM intersection 2), "
    "16 June 2005, 08:30-08:45"
)
RUN_DIR = "ngsim-int2-realism-experiment-20261029T122930Z"  # ensemble member T2, rules 0.3.1
RECORD = "data/geometry/ngsim-lankershim-int2"
MUSE_KEY = "ddfb8029b1e8810e65b2ad986e0da99be630b86e6d7c86669007e2a197ef47ad"
SAM_RUN = "ngsim-cam2-sam-20261028T151050Z"
FILES = {
    "scenario": f"{RECORD}/scenario.json",
    "proposals": "experiments/records/ngsim-int2-experiment-20261028T075803Z.json",
    "muse_reply": f"cache/muse/{MUSE_KEY}.json",
    "timeline_estimate": f"{RECORD}/signal_estimate_v2.json",
    "baseline": "experiments/records/ngsim-int2-realism-baseline-20261028T141919Z.json",
    "validation": (
        "experiments/records/ngsim-int2-realism-validity-20261028T141919Z/validation/validation.json"
    ),
    "assessment": f"{RECORD}/replay_assessments/ngsim-int2-realism-baseline-20261028T141919Z.json",
    "results": f"experiments/records/{RUN_DIR}.json",
    "results_v1": "experiments/records/ngsim-int2-realism-experiment-20261029T122910Z.json",  # T1
    "assessment_v1": (
        f"{RECORD}/replay_assessments/ngsim-int2-realism-baseline-20261028T135241Z.json"
    ),
    # step 0: SAM 3.1 on 10 s of the real camera 2 footage (D3: overlay under CC BY-SA 4.0)
    "sam_record": f"experiments/records/{SAM_RUN}.json",
    "sam_overlay": f"experiments/records/{SAM_RUN}/overlay.mp4",
    "sam_notice": f"experiments/records/{SAM_RUN}/NOTICE.md",
    "sam_review": f"experiments/records/{SAM_RUN}/a3_review.json",  # A3: the scored hand check
    # step 7: the pre-registered four-timeline ensemble (ADR-025), rules 0.3.1
    "ensemble": "experiments/records/ngsim-int2-timeline-ensemble-20261029T123045Z.json",
    # step 8: the synthetic known-answer control (ADR-026); not about the real junction
    "control_reallocation": (
        "experiments/records/synthetic-known-answer-reallocation-20261029T123315Z.json"
    ),
    "control_long_cycle": (
        "experiments/records/synthetic-known-answer-long-cycle-20261029T123354Z.json"
    ),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    out = REPO / "data/demo" / f"{DEMO_ID}.json"
    manifest = DemoManifest(
        demo_id=DEMO_ID,
        title=TITLE,
        run_dir=RUN_DIR,
        files={
            role: PinnedFile(
                path=path, sha256=hashlib.sha256((REPO / path).read_bytes()).hexdigest()
            )
            for role, path in FILES.items()
        },
    )
    try:
        load_demo(manifest, REPO)
    except DemoError as error:
        print(f"not written: {error}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(manifest.model_dump_json(indent=2) + "\n")
    print(f"wrote {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
