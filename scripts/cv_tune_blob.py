"""Tune the median-blob detector on dev frames outside the SAM windows, and record the search.

    .venv/bin/python scripts/cv_tune_blob.py --record-dir experiments/records/cv-bakeoff-<stamp>

Frames: `dev_excl_sam` (every 2 s through the even blocks, minus the three SAM windows), for the
background and the thresholds alike, so the per-window figures reported afterwards are on frames
neither saw. The selection rule is fixed here, before the search is read: the highest F1 of
vehicle-frame recall and precision proxy on those frames, one stage at a time in the order of
`STAGES`, each stage starting from the best of the one before. The chosen values go into
`BlobParams` by hand; this script changes nothing. Needs a built reference set
(scripts/cv_bakeoff.py --build-references).
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "perception"))

from cairoflow_perception.local import scoring as S  # noqa: E402
from cairoflow_perception.local.median_blob import (  # noqa: E402
    BlobParams,
    MedianBlobDetector,
    median_background,
)

STAGES = (
    {"diff_threshold": (20, 25, 30, 35, 40, 50), "close_px": (3, 5, 7), "open_px": (1, 3, 5)},
    {"min_area_px": (60, 90, 130, 180), "min_side_px": (5, 7, 9)},
    {"max_area_px": (500, 700, 900), "max_side_px": (36, 42, 50), "split_core_px": (3, 4, 5)},
    {"car_long_px": (22, 26, 30), "car_wide_px": (12, 14, 16), "min_tile_fill_pct": (40, 50, 65)},
)


def score(detector, images, ref) -> dict:
    dets = {
        f: S.detections_to_dets(f, detector.detect(image), ref.ground)
        for f, image in images.items()
    }
    results = S.associate(dets, ref.refs, sorted(images))
    tp, n_ref = S.vehicle_frame_recall(results)
    matched, n_det = S.precision_proxy(results)
    recall = tp / n_ref if n_ref else 0.0
    precision = matched / n_det if n_det else 0.0
    f1 = 2 * recall * precision / (recall + precision) if recall + precision else 0.0
    return {
        "recall": round(recall, 4),
        "precision_proxy": round(precision, 4),
        "f1": round(f1, 4),
        "detections_per_frame": round(n_det / len(images), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scratch", type=Path, default=Path.home() / "scratch-cv")
    parser.add_argument("--record-dir", type=Path, required=True)
    args = parser.parse_args()
    ref = S.ReferenceSet.load(args.scratch / "reference" / "sync120.npz")
    tuning = [t for t in S.dev_sample_times() if not S.in_sam_window(t)]
    started = time.time()
    with S.FrameSampler(ROOT / S.VIDEO) as sampler:
        background = median_background(sampler, tuning)
        images = {S.tenths(t): sampler.read(t) for t in tuning}
    current = BlobParams()
    stages = []
    for stage in STAGES:
        keys = list(stage)
        rows = []
        for values in itertools.product(*(stage[k] for k in keys)):
            change = dict(zip(keys, values, strict=True))
            params = replace(current, **change)
            rows.append({**change, **score(MedianBlobDetector(background, params), images, ref)})
        rows.sort(key=lambda r: -r["f1"])
        current = replace(current, **{k: rows[0][k] for k in keys})
        stages.append({"grid": stage, "best": rows[0], "top": rows[:10]})
        print(f"{time.time() - started:.0f} s", rows[0], flush=True)
    path = S.write_record(
        args.record_dir,
        "blob-tuning.json",
        {
            "harness_version": S.HARNESS_VERSION,
            "frames": "dev_excl_sam",
            "frame_count": len(tuning),
            "selection_rule": "highest F1 of vehicle-frame recall and precision proxy, by stage",
            "start": asdict(BlobParams()),
            "stages": stages,
            "chosen": asdict(current),
            "seconds": round(time.time() - started, 1),
        },
    )
    print("chosen", asdict(current), "->", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
