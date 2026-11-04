"""Export the WorldView of the decision-driving NGSIM snapshot, so a fresh clone can inspect it.

The snapshot itself (`world_model.json`, 14 MB, per-vehicle tracks) is Git-ignored. Its WorldView
(70 KB: entities, relations, evidence links, absences) is a pure projection of it (ADR-033), and
together with the committed geometry and the committed Muse reply it is all the inspector page
(`/flow`) needs.

    uv run --frozen python scripts/export_world_view.py [--demo DEMO_MANIFEST] [--out FILE]

The snapshot is found by the SHA-256 the demo's baseline records for its world model, among
`data/processed/ngsim-int2/*/world_model.json`. The view is built with the evidence bundle the
pinned Muse request carried, so every evidence item the model read is tied to its entity. Nothing
is overwritten: an existing file must already hold the same bytes. Values derived from the
trajectories carry CC BY-SA 3.0 (attribution in the scenario record).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from cairoflow_api.demo import bundle_of, read_manifest
from cairoflow_contracts.evidence import IntersectionGeometry, TrafficWorldModel
from cairoflow_perception.worldview import build_world_view
from cairoflow_reasoning.muse_adapter import MuseRequestRecord

REPO = Path(__file__).resolve().parents[1]
GEOMETRY = "data/geometry/ngsim-lankershim-int2/geometry.json"
DEFAULT_DEMO = REPO / "data/demo/ngsim-int2-no-footage.json"
DEFAULT_OUT = REPO / "data/demo/ngsim-int2-worldview.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--demo", type=Path, default=DEFAULT_DEMO)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    manifest = read_manifest(args.demo)
    baseline = json.loads((REPO / manifest.files["baseline"].path).read_text("utf-8"))
    wanted = baseline["inputs"]["world_model_sha256"]
    found = [
        path
        for path in sorted(REPO.glob("data/processed/ngsim-int2/*/world_model.json"))
        if hashlib.sha256(path.read_bytes()).hexdigest() == wanted
    ]
    if not found:
        print(
            f"no snapshot with SHA-256 {wanted} under data/processed/ngsim-int2/", file=sys.stderr
        )
        print("rebuild it: scripts/build_ngsim_int2_scenario.py (see its --check-record)",
              file=sys.stderr)  # fmt: skip
        return 1
    model = TrafficWorldModel.model_validate_json(found[0].read_text("utf-8"))
    geometry = IntersectionGeometry.model_validate_json((REPO / GEOMETRY).read_text("utf-8"))
    reply = MuseRequestRecord.model_validate_json(
        (REPO / manifest.files["muse_reply"].path).read_text("utf-8")
    )
    view = build_world_view(model, geometry, bundle=bundle_of(reply))
    text = view.model_dump_json(indent=1) + "\n"
    if args.out.exists():
        if args.out.read_text("utf-8") != text:
            print(f"{args.out} exists and differs; it is not overwritten", file=sys.stderr)
            return 1
        print(f"{args.out} already holds this view")
        return 0
    args.out.write_text(text, encoding="utf-8")
    print(
        f"wrote {args.out.relative_to(REPO)}: {len(view.entities)} entities, "
        f"{len(view.relations)} relations, {len(view.evidence)} evidence items, "
        f"view {view.view_sha256[:12]}, from {found[0].relative_to(REPO)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
