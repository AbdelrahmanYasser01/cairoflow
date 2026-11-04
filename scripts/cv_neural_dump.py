"""Run one bake-off candidate over every dev frame the scorer needs and keep its boxes.

    ~/.venvs/cv-bakeoff/bin/python scripts/cv_neural_dump.py \
        --candidate dfine-l --variants native,tiled2x

Boxes go to <scratch>/bakeoff/dumps as .npz (numbers, no pixels) down to the score floor, with a
JSON sidecar of settings, timing and memory; scripts/cv_neural_score.py sweeps confidence from
them. Frames are the union of the harness's dev sample and the three SAM windows, read through
`FrameSampler`, so an odd block cannot be reached. One process per candidate keeps peak memory
honest. `detect` time covers painting, scaling, tiling, the model, class mapping and NMS, one
frame at a time as the pipeline calls it; decoding is timed apart.
"""

from __future__ import annotations

import argparse
import importlib
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for package in ("perception", "contracts"):
    path = str(ROOT / "packages" / package)
    if path not in sys.path:
        sys.path.insert(0, path)

from cairoflow_perception.local import median_blob as B  # noqa: E402
from cairoflow_perception.local import neural as N  # noqa: E402
from cairoflow_perception.local import scoring as S  # noqa: E402

SEED = 20261103
WARMUP_FRAMES = 8
REPEAT_FRAMES = 12
EXTERNAL = {"visdrone-yolo26s": "cv_yolo_backend:make_backend"}  # scratch only


def all_times() -> list[float]:
    dev = S.dev_sample_times()
    return sorted({*dev, *(t for k in S.WINDOWS for t in S.sam_window_times(k))})


def gpu_state() -> dict[str, Any] | None:
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    free, total = torch.cuda.mem_get_info()
    return {
        "name": torch.cuda.get_device_name(0),
        "cuda_runtime": torch.version.cuda,
        "device_used_mib": round((total - free) / 2**20, 1),
        "device_total_mib": round(total / 2**20, 1),
        "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1),
        "peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 2**20, 1),
    }


def reset_peak() -> None:
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()


def seed_everything() -> None:
    random.seed(SEED)
    np.random.seed(SEED)
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return
    torch.manual_seed(SEED)


def run_variant(
    detector: Any, sampler: S.FrameSampler, times: list[float], neural: bool
) -> dict[str, Any]:
    """Boxes for every frame, with timing."""
    for t in S.dev_sample_times()[:WARMUP_FRAMES]:
        image = sampler.read(t)
        detector.raw(image) if neural else detector.detect(image)
    reset_peak()
    rows: dict[str, list[Any]] = {k: [] for k in ("frame", "box", "score", "label")}
    others: dict[str, list[Any]] = {k: [] for k in ("frame", "box", "score", "label")}
    decode_s = detect_s = 0.0
    wall = time.perf_counter()
    for i, t in enumerate(times):
        started = time.perf_counter()
        image = sampler.read(t)
        mid = time.perf_counter()
        if neural:
            vehicles, rest = detector.raw(image)
        else:
            found = detector.detect(image)
            vehicles = N.RawBoxes(
                np.array([d[:4] for d in found], np.float32).reshape(-1, 4),
                np.array([d[4] for d in found], np.float32),
                [d[5] for d in found],
            )
            rest = N.RawBoxes(np.zeros((0, 4), np.float32), np.zeros(0, np.float32), [])
        done = time.perf_counter()
        decode_s += mid - started
        detect_s += done - mid
        frame = S.tenths(t)
        for store, raw in ((rows, vehicles), (others, rest)):
            store["frame"] += [frame] * len(raw.scores)
            store["box"].append(raw.boxes)
            store["score"].append(raw.scores)
            store["label"] += list(raw.names)
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(times)}  {detect_s / (i + 1) * 1000:.1f} ms/frame", flush=True)
    wall = time.perf_counter() - wall
    return {
        "rows": rows,
        "others": others,
        "memory": gpu_state(),
        "timing": {
            "frames": len(times),
            "detect_s": round(detect_s, 3),
            "decode_s": round(decode_s, 3),
            "wall_s": round(wall, 3),
            "detect_ms_per_frame": round(detect_s / len(times) * 1000, 2),
            "detect_fps": round(len(times) / detect_s, 2),
            "end_to_end_fps": round(len(times) / wall, 2),
            "warmup_frames_excluded": WARMUP_FRAMES,
            "batching": "one frame per call; a tiled frame is one batch of its tiles",
        },
    }


def repeat_check(detector: Any, sampler: S.FrameSampler, neural: bool) -> dict[str, Any]:
    """The same frames twice: are the boxes identical? Inference is not seeded by anything but the
    kernels, so this is what `seed` can and cannot promise."""
    worst, same = 0.0, True
    for t in S.dev_sample_times()[:: len(S.dev_sample_times()) // REPEAT_FRAMES][:REPEAT_FRAMES]:
        image = sampler.read(t)
        if neural:
            a, b = detector.raw(image)[0], detector.raw(image)[0]
            if a.boxes.shape != b.boxes.shape:
                same = False
                continue
            if a.boxes.size:
                worst = max(worst, float(np.abs(a.boxes - b.boxes).max()))
                same &= bool(np.array_equal(a.boxes, b.boxes))
        else:
            same &= detector.detect(image) == detector.detect(image)
    return {"frames": REPEAT_FRAMES, "bit_identical": same, "max_box_difference_px": worst}


def save(out: Path, name: str, result: dict[str, Any], meta: dict[str, Any]) -> None:
    def pack(store: dict[str, list[Any]]) -> dict[str, np.ndarray]:
        boxes = store["box"]
        return {
            "frame": np.array(store["frame"], dtype=np.int64),
            "box": np.concatenate(boxes) if boxes else np.zeros((0, 4), np.float32),
            "score": np.concatenate(store["score"]) if boxes else np.zeros(0, np.float32),
            "label": np.array(store["label"], dtype="U24"),
        }

    v, o = pack(result["rows"]), pack(result["others"])
    np.savez_compressed(out / f"{name}.npz", **v, **{f"other_{k}": a for k, a in o.items()})
    (out / f"{name}.json").write_text(json.dumps(meta, indent=2, sort_keys=True), "utf-8")
    print(f"-> {out / name}.npz: {len(v['score'])} vehicle boxes, {len(o['score'])} others")


def load_candidate(name: str) -> Any:
    if name in EXTERNAL:
        module, _, attr = EXTERNAL[name].partition(":")
        return getattr(importlib.import_module(module), attr)()
    return N.build_backend(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--variants", default="native,tiled2x")
    parser.add_argument("--scratch", type=Path, default=Path.home() / "scratch-cv")
    parser.add_argument("--limit", type=int, default=0, help="first n frames only (a smoke test)")
    parser.add_argument("--tag", default="", help="suffix for the dump's name")
    args = parser.parse_args(argv)
    out = args.scratch / "bakeoff/dumps"
    out.mkdir(parents=True, exist_ok=True)
    times = all_times()
    if args.limit:
        times = times[:: max(len(times) // args.limit, 1)][: args.limit]
    seed_everything()
    before = gpu_state()
    neural = args.candidate != "median-blob"
    started = time.time()
    with S.FrameSampler(ROOT / S.VIDEO) as sampler:
        backend = None
        if neural:
            backend = load_candidate(args.candidate)
        load_s = round(time.time() - started, 2)
        for variant in args.variants.split(","):
            if neural:
                detector = N.NeuralDetector(backend, N.ViewParams(variant=variant))
            else:
                fit = [t for t in S.dev_sample_times() if not S.in_sam_window(t)]
                detector = B.MedianBlobDetector(B.median_background(sampler, fit))
            print(f"{args.candidate} {variant}: {len(times)} frames", flush=True)
            result = run_variant(detector, sampler, times, neural)
            meta = {
                "candidate": args.candidate,
                "variant": variant,
                "seed": SEED,
                "score_floor": N.SCORE_FLOOR if neural else None,
                "settings": detector.describe(),
                "load_s": load_s,
                "timing": result["timing"],
                "gpu_before_load": before,
                "gpu_after_run": result["memory"],
                "repeatability": repeat_check(detector, sampler, neural),
                "frame_count": len(times),
                "frames_min_max_tenths": [S.tenths(times[0]), S.tenths(times[-1])],
                "harness_version": S.HARNESS_VERSION,
                "limited_smoke_run": bool(args.limit),
            }
            print(json.dumps({k: meta[k] for k in ("timing", "repeatability")}))
            print(json.dumps(meta["gpu_after_run"]))
            save(out, f"{args.candidate}__{variant}{args.tag}", result, meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
