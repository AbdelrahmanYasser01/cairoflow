"""Pin the meta-depth records the /demo page shows: write data/demo/meta-depth-pins.json.

    uv run --frozen python scripts/pin_meta_depth.py [--frame REC.json] [--recurrence REC.json]

--frame takes a muse-frame-annotation record whose status is "ok"; its frame.jpg and NOTICE.md
(the folder of the same name) are pinned with it. --recurrence takes a muse-recurrence record.
Every file is pinned by SHA-256, and the result is loaded exactly as the app loads it
(cairoflow_api.meta_depth.load_meta_depth) before anything is written. The pins file is the
page's configuration, not an experiment record: rewriting it changes which records the page
shows, and Git keeps the earlier versions. No network access and no model calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from cairoflow_api.meta_depth import PINS_FILE, load_meta_depth
from cairoflow_contracts.meta_depth import FrameAnnotationRecord

REPO = Path(__file__).resolve().parents[1]


def pin(path: Path, repo: Path) -> dict[str, str]:
    rel = path.resolve().relative_to(repo.resolve()).as_posix()
    return {"path": rel, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main(argv: list[str] | None = None, *, repo: Path = REPO) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--frame", type=Path)
    parser.add_argument("--recurrence", type=Path)
    args = parser.parse_args(argv)
    files: dict[str, dict[str, str]] = {}
    if args.frame:
        frame = (repo / args.frame).resolve() if not args.frame.is_absolute() else args.frame
        record = FrameAnnotationRecord.model_validate_json(frame.read_text("utf-8"))
        if record.status != "ok":
            print(
                f"{args.frame} is a failed request; only a usable reply is shown", file=sys.stderr
            )
            return 2
        folder = frame.with_suffix("")
        files["frame_annotation"] = pin(frame, repo)
        files["frame_image"] = pin(folder / "frame.jpg", repo)
        files["frame_notice"] = pin(folder / "NOTICE.md", repo)
    if args.recurrence:
        rec = (
            (repo / args.recurrence).resolve()
            if not args.recurrence.is_absolute()
            else args.recurrence
        )
        files["muse_recurrence"] = pin(rec, repo)
    if not files:
        parser.error("nothing to pin")
    text = json.dumps({"schema_version": "0.1.0", "files": files}, indent=2) + "\n"

    # load the pins the way the app will, from a scratch copy, before writing the real file
    with tempfile.TemporaryDirectory() as scratch:
        trial = Path(scratch) / PINS_FILE
        trial.parent.mkdir(parents=True)
        trial.write_text(text, encoding="utf-8")
        for spec in files.values():
            target = Path(scratch) / spec["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((repo / spec["path"]).read_bytes())
        meta = load_meta_depth(Path(scratch))
    if meta.problem:
        print(f"not written: {meta.problem}", file=sys.stderr)
        return 2
    out = repo / PINS_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(
        f"wrote {PINS_FILE}: " + ", ".join(f"{role} {spec['path']}" for role, spec in files.items())
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
