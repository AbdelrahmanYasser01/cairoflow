"""Build the WorldView snapshot the inspector pages read (/world, /flow), from the real world model.

    uv run --frozen python scripts/build_worldview_snapshot.py [--demo DEMO_MANIFEST] [--out FILE]

The world model (`world_model.json`, Git-ignored) is the one whose SHA-256 the demo's baseline
records, found among `data/processed/ngsim-int2/*/world_model.json`. The view is the M1-a
projection (cairoflow_perception.worldview.build_world_view) over the committed geometry and the
evidence bundle the pinned Muse request carried. It is deterministic: no clock, no randomness, so
two builds are byte-identical. The file records its source model hash (`world_model_sha256`) and
its projection version (`view_version`); both are printed. The output stays out of Git
(ADR-033 decision 9). Nothing is overwritten: an existing file must already hold the same bytes.
Values derived from the NGSIM trajectories carry CC BY-SA 3.0 (attribution in the scenario record).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from cairoflow_api.demo import bundle_of, read_manifest
from cairoflow_contracts.evidence import IntersectionGeometry, TrafficWorldModel
from cairoflow_contracts.worldview import WorldView
from cairoflow_perception.worldview import build_world_view
from cairoflow_reasoning.muse_adapter import MuseRequestRecord

REPO = Path(__file__).resolve().parents[1]
GEOMETRY = "data/geometry/ngsim-lankershim-int2/geometry.json"
WORLD_MODELS = "data/processed/ngsim-int2/*/world_model.json"
DEFAULT_DEMO = REPO / "data/demo/ngsim-int2-no-footage.json"
DEFAULT_OUT = REPO / "data/demo/ngsim-int2-worldview.json"


class SnapshotError(RuntimeError):
    """The inputs the snapshot is built from are not here."""


def world_model_path(repo: Path, digest: str) -> Path:
    """The world model file whose bytes hash to `digest` (the baseline's citation)."""
    for path in sorted(repo.glob(WORLD_MODELS)):
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            return path
    raise SnapshotError(
        f"no world model with SHA-256 {digest} under {WORLD_MODELS}; rebuild it with "
        "scripts/build_ngsim_int2_scenario.py (see its --check-record)"
    )


def build(repo: Path, demo_manifest: Path) -> WorldView:
    """The WorldView of the world model the demo's baseline replays."""
    manifest = read_manifest(demo_manifest)
    baseline = json.loads((repo / manifest.files["baseline"].path).read_text("utf-8"))
    path = world_model_path(repo, baseline["inputs"]["world_model_sha256"])
    model = TrafficWorldModel.model_validate_json(path.read_bytes())
    geometry = IntersectionGeometry.model_validate_json((repo / GEOMETRY).read_text("utf-8"))
    reply = MuseRequestRecord.model_validate_json(
        (repo / manifest.files["muse_reply"].path).read_text("utf-8")
    )
    return build_world_view(model, geometry, bundle=bundle_of(reply))


def serialise(view: WorldView) -> str:
    return view.model_dump_json(indent=1) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--demo", type=Path, default=DEFAULT_DEMO)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        view = build(REPO, args.demo)
    except SnapshotError as error:
        print(error, file=sys.stderr)
        return 1
    text = serialise(view)
    summary = (
        f"source model {view.world_model_sha256}, projection {view.view_version}, "
        f"view {view.view_sha256}: {len(view.entities)} entities, {len(view.relations)} relations, "
        f"{len(view.evidence)} evidence items"
    )
    if args.out.exists():
        if args.out.read_text("utf-8") != text:
            print(f"{args.out} exists and differs; it is not overwritten", file=sys.stderr)
            return 1
        print(f"{args.out} already holds this snapshot: {summary}")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
