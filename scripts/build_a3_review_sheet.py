"""Build the A3 review sheet for a SAM run on a real clip.

    uv run --frozen python scripts/build_a3_review_sheet.py RUN_DIR CLIP_DIR --out DIR

RUN_DIR is written by scripts/run_sam_cam2_excerpt.py (its `tracks.json`); CLIP_DIR is the
ingested clip it ran on. The protocol below is fixed before anyone marks anything:

- Sample: every tracked box in frames 0, 20, 40, 60, 80 and 99 (one every 2 s, plus the last).
- Each box gets one verdict: `vehicle` (covers most of exactly one motor vehicle),
  `not_vehicle` (no motor vehicle under most of the box) or `partial` (covers less than about
  half of one vehicle, or covers two or more).
- Per frame: the number of motor vehicles at least half visible above image row 410 (the
  window frame and reflections lie below it) that no box covers most of (`missed`).
- Per track: a strip of crops, one every 10 frames where the track exists; the reviewer counts
  how often the crop shows a different vehicle from the one before (`switches`).

Writes into --out (a new directory): `a3_sheet.json` (the protocol, the sampled boxes and the
strips, no pixels; its `sheet_id` is the SHA-256 of that content) and the review images
(`frame_<f>.jpg` with numbered boxes and the row-410 line, `box_<f>_<n>.jpg`,
`strip_<track>_<f>.jpg`). The images hold clip pixels (CC BY-SA 4.0, DATA_REGISTER.md §23).
Scoring the marks is scripts/score_a3_review.py. No network access and no model calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from cairoflow_contracts.evidence import SourceClip
from cairoflow_perception.tracking import TrackingResult

SAMPLE_FRAMES = (0, 20, 40, 60, 80, 99)
STRIP_STEP = 10
ROI_BOTTOM_PX = 410  # usable view ends here (DATA_REGISTER.md §13)
VERDICTS = {
    "vehicle": "covers most of exactly one motor vehicle",
    "not_vehicle": "no motor vehicle under most of the box",
    "partial": "covers less than about half of one vehicle, or covers two or more",
}
MISSED = (
    "motor vehicles at least half visible above image row 410 that no box covers most of "
    "(parked or moving, on or off the road)"
)
SWITCHES = "how often a crop shows a different vehicle from the crop before it"


def frames_of(clip_path: Path, wanted: set[int]) -> dict[int, np.ndarray]:
    capture = cv2.VideoCapture(str(clip_path))
    frames, index = {}, 0
    while True:
        ok, image = capture.read()
        if not ok:
            break
        if index in wanted:
            frames[index] = image
        index += 1
    capture.release()
    return frames


def crop(image: np.ndarray, box: tuple[float, ...], size: int, pad: float) -> np.ndarray:
    """A square crop around `box` with `pad` box-sizes of context, scaled to `size` px."""
    x1, y1, x2, y2 = box
    side = max(x2 - x1, y2 - y1) * (1 + 2 * pad)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    h, w = image.shape[:2]
    a, b = int(max(cx - side / 2, 0)), int(max(cy - side / 2, 0))
    c, d = int(min(cx + side / 2, w)), int(min(cy + side / 2, h))
    return cv2.resize(image[b:d, a:c], (size, size), interpolation=cv2.INTER_CUBIC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("clip_dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        print(f"{args.out} exists; choose a new directory", file=sys.stderr)
        return 2
    tracking = TrackingResult.model_validate_json((args.run_dir / "tracks.json").read_text())
    clip = SourceClip.model_validate_json((args.clip_dir / "source_clip.json").read_text())
    last = len(clip.frames) - 1
    strip_frames = sorted({*range(0, last + 1, STRIP_STEP), last})
    images = frames_of(args.clip_dir / clip.output_file, {*SAMPLE_FRAMES, *strip_frames})

    boxes, strips = [], []
    for frame in SAMPLE_FRAMES:
        here = [(p.bbox_xyxy_px, t.track_id) for t in tracking.tracks for p in t.points
                if p.frame.frame_index == frame]  # fmt: skip
        here.sort(key=lambda b: (b[0][0], b[0][1]))  # numbered left to right
        for number, (box, track) in enumerate(here, start=1):
            boxes.append({"frame": frame, "box": number, "track_id": track,
                          "bbox_xyxy_px": [round(v, 1) for v in box],
                          "contact_above_roi": box[3] < ROI_BOTTOM_PX})  # fmt: skip
    for t in sorted(tracking.tracks, key=lambda t: int(t.track_id.split(":t")[1])):
        at = {p.frame.frame_index: p.bbox_xyxy_px for p in t.points}
        strips.append({"track_id": t.track_id, "frames": [f for f in strip_frames if f in at]})

    sheet = {
        "schema": "cairoflow-a3-review-sheet/0.1.0",
        "clip_id": clip.clip_id,
        "clip_sha256": clip.output_sha256,
        "tracks_sha256": hashlib.sha256((args.run_dir / "tracks.json").read_bytes()).hexdigest(),
        "protocol": {
            "sample_frames": list(SAMPLE_FRAMES),
            "roi_bottom_px": ROI_BOTTOM_PX,
            "verdicts": VERDICTS,
            "missed": MISSED,
            "switches": SWITCHES,
            "strip_step_frames": STRIP_STEP,
        },  # fmt: skip
        "boxes": boxes,
        "strips": strips,
    }
    sheet["sheet_id"] = hashlib.sha256(json.dumps(sheet, sort_keys=True).encode()).hexdigest()
    args.out.mkdir(parents=True)
    (args.out / "a3_sheet.json").write_text(json.dumps(sheet, indent=2) + "\n", encoding="utf-8")

    for frame in SAMPLE_FRAMES:
        image = images[frame].copy()
        cv2.line(image, (0, ROI_BOTTOM_PX), (image.shape[1], ROI_BOTTOM_PX), (0, 200, 255), 1)
        for b in (b for b in boxes if b["frame"] == frame):
            x1, y1, x2, y2 = (int(round(v)) for v in b["bbox_xyxy_px"])
            cv2.rectangle(image, (x1, y1), (x2, y2), (255, 0, 255), 1)
            cv2.putText(image, str(b["box"]), (x1, max(y1 - 3, 10)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.4, (255, 0, 255), 1, cv2.LINE_AA)  # fmt: skip
            tile = crop(images[frame], tuple(b["bbox_xyxy_px"]), 112, 0.6)
            cv2.imwrite(str(args.out / f"box_{frame}_{b['box']}.jpg"), tile,
                        [cv2.IMWRITE_JPEG_QUALITY, 85])  # fmt: skip
        cv2.imwrite(str(args.out / f"frame_{frame}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    for s in strips:
        track = next(t for t in tracking.tracks if t.track_id == s["track_id"])
        at = {p.frame.frame_index: p.bbox_xyxy_px for p in track.points}
        name = s["track_id"].replace(":", "_")
        for f in s["frames"]:
            cv2.imwrite(str(args.out / f"strip_{name}_{f}.jpg"), crop(images[f], at[f], 80, 0.6),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])  # fmt: skip
    print(f"wrote {args.out}: {len(boxes)} boxes in {len(SAMPLE_FRAMES)} frames, "
          f"{len(strips)} track strips; sheet {sheet['sheet_id'][:12]}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
