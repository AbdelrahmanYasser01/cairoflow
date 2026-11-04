"""Write data/sample/checksums.json: each sample analysis's decoder checksum.

The web tests compare their TypeScript decoder with these values, so a difference between the two
decoders fails a test instead of drawing vehicles in the wrong place.
"""

from __future__ import annotations

import json
from pathlib import Path

from cairoflow_perception.analysis_store import decode_trajectories, trajectory_checksum

SAMPLES = Path(__file__).resolve().parents[1] / "data/sample"


def main() -> int:
    checksums = {}
    for directory in sorted((SAMPLES / "analyses").iterdir()):
        data = (directory / "trajectories.bin").read_bytes()
        checksums[directory.name] = trajectory_checksum(decode_trajectories(data)[1])
    (SAMPLES / "checksums.json").write_text(json.dumps(checksums, indent=1) + "\n", "utf-8")
    print(checksums)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
