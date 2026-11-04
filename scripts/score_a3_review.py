"""Score the A3 review: precision, recall and id switches from a reviewer's marks.

    uv run --frozen python scripts/score_a3_review.py SHEET MARKS --out RECORD

SHEET is `a3_sheet.json` (scripts/build_a3_review_sheet.py). MARKS is the review page's
`marks` collection exported as JSON files (one per mark, as `ArtifactData` writes them with
`out_dir`; a directory or a single JSON list). Every sampled box, frame and track must carry
exactly one mark made on this sheet (`sheet_id`), or nothing is scored (exit 2).

- precision = boxes marked `vehicle` / all sampled boxes;
- recall = `vehicle` boxes whose bottom edge lies above the row-410 line / (those + the
  vehicles marked missed above it);
- id switches: their total, and the tracks with at least one.

Intervals are Wilson 95%. They treat every box and every vehicle as independent, but boxes in
one frame and frames of one clip are not, so they are too narrow. One reviewer marked one 10 s
clip by eye: this measures that clip, not SAM in general. Writes RECORD (never overwritten) with
the sheet's id and hash, every mark, the counts and the scores. No network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

Z95 = 1.959964
VERDICTS = ("vehicle", "not_vehicle", "partial")


class ReviewError(ValueError):
    """The marks do not cover the sheet exactly once."""


def wilson(k: int, n: int) -> tuple[float, float] | None:
    """Wilson score 95% interval for k successes in n trials; None when n is 0."""
    if n == 0:
        return None
    p = k / n
    centre = (p + Z95**2 / (2 * n)) / (1 + Z95**2 / n)
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95**2 / (4 * n * n)) / (1 + Z95**2 / n)
    return round(max(centre - half, 0.0), 4), round(min(centre + half, 1.0), 4)


def load_marks(path: Path) -> list[dict]:
    """Marks from a directory of per-document JSON files or from one JSON list."""
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    out = []
    for file in files:
        value = json.loads(file.read_text(encoding="utf-8"))
        for item in value if isinstance(value, list) else [value]:
            out.append(item.get("data", item))
    return out


def score(sheet: dict, marks: list[dict]) -> dict:
    """The A3 scores; ReviewError unless every sampled item has exactly one mark."""
    own = [m for m in marks if m.get("sheet_id") == sheet["sheet_id"]]
    boxes = {(b["frame"], b["box"]): b for b in sheet["boxes"]}
    frames = set(sheet["protocol"]["sample_frames"])
    tracks = {s["track_id"] for s in sheet["strips"]}
    seen = Counter()
    verdict, missed, switches = {}, {}, {}
    for m in own:
        kind = m.get("kind")
        if kind == "box" and (m["frame"], m["box"]) in boxes and m.get("verdict") in VERDICTS:
            key = ("box", m["frame"], m["box"])
            verdict[(m["frame"], m["box"])] = m["verdict"]
        elif kind == "frame" and m.get("frame") in frames and _count(m.get("missed")):
            key = ("frame", m["frame"])
            missed[m["frame"]] = m["missed"]
        elif kind == "track" and m.get("track_id") in tracks and _count(m.get("switches")):
            key = ("track", m["track_id"])
            switches[m["track_id"]] = m["switches"]
        else:
            raise ReviewError(f"a mark this sheet does not define: {m}")
        seen[key] += 1
    problems = [f"{k} marked {n} times" for k, n in seen.items() if n > 1]
    problems += [f"box {b} in frame {f} not marked" for f, b in boxes if (f, b) not in verdict]
    problems += [f"frame {f}: missed vehicles not marked" for f in sorted(frames - set(missed))]
    problems += [f"track {t}: switches not marked" for t in sorted(tracks - set(switches))]
    if problems:
        raise ReviewError("; ".join(problems))

    counts = Counter(verdict.values())
    true_above = sum(1 for key, v in verdict.items()
                     if v == "vehicle" and boxes[key]["contact_above_roi"])  # fmt: skip
    total_missed = sum(missed.values())
    per_frame = [
        {"frame": f, "boxes": sum(1 for k in boxes if k[0] == f),
         "vehicle": sum(1 for k, v in verdict.items() if k[0] == f and v == "vehicle"),
         "missed": missed[f]}
        for f in sorted(frames)
    ]  # fmt: skip
    return {
        "boxes": len(boxes),
        "verdicts": {v: counts.get(v, 0) for v in VERDICTS},
        "precision": {"value": round(counts.get("vehicle", 0) / len(boxes), 4),
                      "ci95": wilson(counts.get("vehicle", 0), len(boxes)),
                      "definition": "boxes marked vehicle / all sampled boxes"},
        "recall": {"value": round(true_above / (true_above + total_missed), 4)
                   if true_above + total_missed else None,
                   "ci95": wilson(true_above, true_above + total_missed),
                   "found": true_above, "missed": total_missed,
                   "definition": "vehicle boxes above row 410 / (those + vehicles missed there)"},
        "id_switches": {"total": sum(switches.values()),
                        "tracks_with_a_switch": sum(1 for n in switches.values() if n > 0),
                        "tracks": len(tracks)},
        "per_frame": per_frame,
        "caveats": [
            "Wilson intervals treat boxes and vehicles as independent; boxes in one frame are "
            "not, so the intervals are too narrow",
            "one reviewer, by eye, on one 10 s clip of one camera: it measures this clip only",
        ],
    }  # fmt: skip


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sheet", type=Path)
    parser.add_argument("marks", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    sheet = json.loads(args.sheet.read_text(encoding="utf-8"))
    marks = load_marks(args.marks)
    try:
        scores = score(sheet, marks)
    except ReviewError as error:
        print(f"not scored: {error}", file=sys.stderr)
        return 2
    record = {
        "schema": "cairoflow-a3-review/0.1.0",
        "label": "observed by a person (the reviewer), by eye, on SAM 3.1 output",
        "sheet_id": sheet["sheet_id"],
        "sheet_sha256": hashlib.sha256(args.sheet.read_bytes()).hexdigest(),
        "clip_id": sheet["clip_id"],
        "protocol": sheet["protocol"],
        "scores": scores,
        "marks": sorted((m for m in marks if m.get("sheet_id") == sheet["sheet_id"]),
                        key=lambda m: json.dumps(m, sort_keys=True)),
    }  # fmt: skip
    with args.out.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, indent=2) + "\n")
    p, r, s = scores["precision"], scores["recall"], scores["id_switches"]
    switched = f"{s['tracks_with_a_switch']}/{s['tracks']}"
    print(f"precision {p['value']:.2f} {p['ci95']}, recall {r['value']} {r['ci95']}, "
          f"{s['total']} id switches in {switched} tracks")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
