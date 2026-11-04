"""Score a local detector on NGSIM camera 2 with the SAM baseline's definitions.

    # once, in the repository's venv (reads the trajectory table; about 30 s, under 250 MB)
    .venv/bin/python scripts/cv_bakeoff.py --build-references

    # the proof the harness is right: recorded SAM boxes must give the published numbers
    .venv/bin/python scripts/cv_bakeoff.py --reproduce-sam
    .venv/bin/python scripts/cv_bakeoff.py --check-frame-map

    # any detector, from any venv that has numpy and OpenCV (torch only if the detector needs it)
    <venv>/bin/python scripts/cv_bakeoff.py --detector median-blob

A detector name resolves to `cairoflow_perception.local.<name, - as _>:make_detector`, or give
`package.module:factory`. The factory takes a `DetectorContext` and returns an object with
`detect(frame_bgr) -> [(x1, y1, x2, y2, score, label), ...]`; it does its own heavy imports, so
nothing here imports torch. Only even blocks are read. Records are JSON numbers under
experiments/records/cv-bakeoff-<UTC stamp>/, never overwritten; pixels stay under --scratch.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for package in ("perception", "contracts"):
    path = str(ROOT / "packages" / package)
    if path not in sys.path:
        sys.path.insert(0, path)

from cairoflow_perception.local import scoring as S  # noqa: E402

DEFAULT_SCRATCH = Path.home() / "scratch-cv"
SET_NAMES = ("dev", "dev_excl_sam", "w1", "w2", "w3")
CHANCE_SHIFT_S = 120.0  # two blocks: a dev frame's shifted tick stays in a dev block
SAM_BASELINE = {
    "source": "baseline metrics report of the offline scorer, 2026-10-31",
    **{
        k: {
            "vehicle_frame_recall": round(v["recall"][0] / v["recall"][1], 4),
            "precision_proxy": round(v["precision"][0] / v["precision"][1], 4),
        }
        for k, v in S.PUBLISHED_SAM.items()
    },
}


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head() -> dict[str, str | None]:
    """The branch and commit, read from .git so that no process is run."""
    try:
        head = (ROOT / ".git/HEAD").read_text("utf-8").strip()
        if not head.startswith("ref: "):
            return {"branch": None, "commit": head}
        ref = head[5:]
        file = ROOT / ".git" / ref
        commit = file.read_text("utf-8").strip() if file.is_file() else None
        return {"branch": ref.removeprefix("refs/heads/"), "commit": commit}
    except OSError:
        return {"branch": None, "commit": None}


def frame_sets(sam_step: int) -> dict[str, list[float]]:
    dev = S.dev_sample_times()
    return {
        "dev": dev,
        "dev_excl_sam": [t for t in dev if not S.in_sam_window(t)],
        **{k: S.sam_window_times(k, sam_step) for k in S.WINDOWS},
    }


def build_references(scratch: Path) -> int:
    """All reference sets the other commands need, as plain .npz files under `scratch`."""
    cache = scratch / "reference"
    cache.mkdir(parents=True, exist_ok=True)
    start = time.time()
    data = S._Trajectories(ROOT)
    sets = frame_sets(1)
    all_times = sorted({t for name in SET_NAMES for t in sets[name]})
    sam_times = sorted({t for k in S.WINDOWS for t in sets[k]})
    main = S.build_reference_set(ROOT, all_times, trajectories=data)
    other = S.build_reference_set(ROOT, sam_times, sync_s=S.SYNC_OTHER_S, trajectories=data)
    late = [t for t in all_times if t + CHANCE_SHIFT_S < S.COVERED_END_S]
    early = [t for t in all_times if t + CHANCE_SHIFT_S >= S.COVERED_END_S]
    chance = S.build_reference_set(ROOT, late, shift_s=CHANCE_SHIFT_S, trajectories=data)
    chance.refs.update(
        S.build_reference_set(ROOT, early, shift_s=-CHANCE_SHIFT_S, trajectories=data).refs
    )
    inputs = {
        "video_sha256": sha256_file(ROOT / S.VIDEO),
        "trajectory_sha256": sha256_file(ROOT / S.TRAJECTORY_CSV),
    }
    for name, ref in (("sync120", main), ("sync119p7", other), ("chance", chance)):
        ref.meta.update(inputs)
        ref.save(cache / f"{name}.npz")
        print(f"{name}: {len(ref.refs)} frames -> {cache / name}.npz")
    print(
        f"rows {data.rows_dev} of {data.rows_total} kept (even blocks); {time.time() - start:.1f} s"
    )
    return 0


def load_reference(scratch: Path, name: str) -> S.ReferenceSet:
    path = scratch / "reference" / f"{name}.npz"
    if not path.is_file():
        raise SystemExit(
            f"{path} is missing; run, in the repository's venv:\n"
            "  .venv/bin/python scripts/cv_bakeoff.py "
            "--build-references"
        )
    return S.ReferenceSet.load(path)


def score_set(
    dets: dict[int, list[S.Det]],
    ref: S.ReferenceSet,
    times: list[float],
    params: S.AssocParams = S.DEFAULT_PARAMS,
    *,
    intervals: bool = True,
) -> dict[str, Any]:
    frames = [S.tenths(t) for t in times]
    return S.summarise(S.associate(dets, ref.refs, frames, params), intervals=intervals)


def reproduce_sam(scratch: Path, record_dir: Path) -> int:
    """Recorded SAM boxes through this harness against the published counts. Non-zero on any gap
    at the governing sync."""
    ref = load_reference(scratch, "sync120")
    other = load_reference(scratch, "sync119p7")
    chance = load_reference(scratch, "chance")
    rows, failed = {}, False
    for key in S.WINDOWS:
        dets = S.load_sam_dets(ROOT, key, ref.ground)
        times = S.sam_window_times(key)
        got = score_set(dets, ref, times)
        want = S.PUBLISHED_SAM[key]
        counts = {
            "recall": (
                got["vehicle_frame_recall"]["numerator"],
                got["vehicle_frame_recall"]["denominator"],
            ),
            "precision": (
                got["precision_proxy"]["numerator"],
                got["precision_proxy"]["denominator"],
            ),
            "vehicles": (
                got["vehicle_recall_any_exposure"]["numerator"],
                got["vehicle_recall_any_exposure"]["denominator"],
            ),
        }
        same = {name: counts[name] == want[name] for name in want}
        failed |= not all(same.values())
        rows[key] = {
            "harness": got,
            "published_counts": want,
            "equal_to_published": same,
            "every_5th_frame": score_set(dets, ref, S.sam_window_times(key, 5), intervals=False),
            "sensitivity": {
                "windows_x0.5": score_set(
                    dets, ref, times, S.DEFAULT_PARAMS.scaled(0.5), intervals=False
                ),
                "windows_x1.5": score_set(
                    dets, ref, times, S.DEFAULT_PARAMS.scaled(1.5), intervals=False
                ),
                "sync_119.7": score_set(dets, other, times, intervals=False),
            },
            "chance_control": score_set(dets, chance, times, intervals=False),
        }
        r, p = got["vehicle_frame_recall"], got["precision_proxy"]
        s = rows[key]["sensitivity"]
        c = rows[key]["chance_control"]
        print(
            f"{key}: recall {r['value']} ({r['numerator']}/{r['denominator']}) "
            f"{r['ci95_cluster_bootstrap_vehicles']}  precision {p['value']} "
            f"({p['numerator']}/{p['denominator']})  equal {same}\n"
            f"    x0.5 {s['windows_x0.5']['vehicle_frame_recall']['value']} "
            f"x1.5 {s['windows_x1.5']['vehicle_frame_recall']['value']} "
            f"119.7 {s['sync_119.7']['vehicle_frame_recall']['value']} "
            f"(frames {s['sync_119.7']['frames_scored']}) "
            f"chance recall {c['vehicle_frame_recall']['value']} "
            f"precision {c['precision_proxy']['value']}"
        )
    path = S.write_record(
        record_dir,
        "sam-reproduction.json",
        {
            "harness_version": S.HARNESS_VERSION,
            "repo_head": git_head(),
            "sync_s": S.SYNC_S,
            "definition_source": "offline_scorer association.py, metrics.py, reference.py",
            "evidence_class": "agreement with a correlated reference, not accuracy",
            "calibration": ref.meta["calibration"],
            "windows": rows,
            "reproduced": not failed,
        },
    )
    print(("REPRODUCED" if not failed else "NOT REPRODUCED"), "->", path)
    return 1 if failed else 0


def psnr(a: Any, b: Any) -> float:
    import numpy as np  # noqa: PLC0415

    d = a[: S.USABLE_V_PX].astype(np.float64) - b[: S.USABLE_V_PX].astype(np.float64)
    mse = float((d * d).mean())
    return 99.0 if mse == 0 else 10 * float(np.log10(255**2 / mse))


def check_frame_map(record_dir: Path) -> int:
    """Every clip frame against the AVI frame `avi_index` names and the one after it: which is the
    image SAM saw? No pixel leaves this function; the record holds PSNR numbers."""
    import cv2  # noqa: PLC0415

    out, ok = {}, True
    with S.FrameSampler(ROOT / S.VIDEO) as sampler:
        for key, window in S.WINDOWS.items():
            clip = cv2.VideoCapture(
                str(ROOT / "data/processed/clips" / window["clip"] / "clip.mp4")
            )
            wins = compared = 0
            at_index: list[float] = []
            at_next: list[float] = []
            for t in S.sam_window_times(key):
                good, image = clip.read()
                if not good:
                    raise OSError(f"{key}: clip ended before source second {t}")
                a = psnr(image, sampler.read(t))
                at_index.append(a)
                try:
                    b = psnr(image, sampler.read(round(t + 0.1, 1)))
                except S.HeldOutError:
                    continue  # the next frame is held out: not compared
                at_next.append(b)
                compared += 1
                wins += a > b
            clip.release()
            out[key] = {
                "clip_frames": len(at_index),
                "psnr_db_at_avi_index_min": round(min(at_index), 2),
                "psnr_db_at_avi_index_mean": round(sum(at_index) / len(at_index), 2),
                "psnr_db_at_next_frame_mean": round(sum(at_next) / len(at_next), 2),
                "avi_index_better_than_next": wins,
                "compared": compared,
            }
            ok &= wins == compared
            print(key, out[key])
    path = S.write_record(
        record_dir,
        "frame-map.json",
        {
            "rule": "clip frame at source second s is AVI frame round(10 s) - 1",
            "avi_frame_lag": S.AVI_FRAME_LAG,
            "windows": out,
            "holds_on_every_frame": ok,
        },
    )
    print("FRAME MAP", "HOLDS" if ok else "FAILS", "->", path)
    return 0 if ok else 1


def resolve_factory(name: str) -> Any:
    module, _, attr = name.partition(":")
    if not attr:
        module, attr = f"cairoflow_perception.local.{name.replace('-', '_')}", "make_detector"
    return getattr(importlib.import_module(module), attr)


def parse_args_kv(items: list[str]) -> dict[str, str]:
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"--detector-arg wants key=value, got {item!r}")
        out[key] = value
    return out


def run_detector(args: argparse.Namespace, record_dir: Path) -> int:
    ref = load_reference(args.scratch, "sync120")
    chance = load_reference(args.scratch, "chance")
    sets = frame_sets(args.sam_step)
    sets_all = frame_sets(1)
    wanted = [s for s in args.sets.split(",") if s]
    unknown = set(wanted) - set(SET_NAMES)
    if unknown:
        raise SystemExit(f"unknown sets {sorted(unknown)}; choose from {SET_NAMES}")
    times = sorted({t for name in wanted for t in sets[name]})
    with S.FrameSampler(ROOT / S.VIDEO) as sampler:
        # a detector fits on dev frames outside the SAM windows, so w1-w3 stay unseen
        context = S.DetectorContext(
            ROOT, args.scratch, sampler, sets_all["dev_excl_sam"], parse_args_kv(args.detector_arg)
        )
        started = time.time()
        detector = resolve_factory(args.detector)(context)
        fit_s = time.time() - started
        dets: dict[int, list[S.Det]] = {}
        spent = 0.0
        for i, (t, image) in enumerate(sampler.frames(times)):
            started = time.perf_counter()
            found = detector.detect(image)
            spent += time.perf_counter() - started
            dets[S.tenths(t)] = S.detections_to_dets(S.tenths(t), found, ref.ground)
            if args.progress and (i + 1) % args.progress == 0:
                print(f"  {i + 1}/{len(times)} frames, {spent / (i + 1) * 1000:.1f} ms/frame")
    results = {name: score_set(dets, ref, sets[name]) for name in wanted}
    control = {name: score_set(dets, chance, sets[name], intervals=False) for name in wanted}
    tight = S.DEFAULT_PARAMS.scaled(0.5)
    sensitivity = {
        name: {
            "windows_x0.5": score_set(dets, ref, sets[name], tight, intervals=False),
            "windows_x0.5_chance": score_set(dets, chance, sets[name], tight, intervals=False),
        }
        for name in wanted
    }
    by_block = {
        str(block): score_set(
            dets, ref, [t for t in sets["dev"] if S.block_of(t) == block], intervals=False
        )
        for block in S.dev_blocks()
        if "dev" in wanted
    }
    payload = {
        "harness_version": S.HARNESS_VERSION,
        "detector": args.detector,
        "detector_params": getattr(detector, "describe", dict)(),
        "detector_args": context.args,
        "repo_head": git_head(),
        "python": sys.version.split()[0],
        "evidence_class": "agreement with a correlated reference, not accuracy",
        "frame_convention": "source_s published convention; image = AVI frame round(10 s) - 1",
        "sync_s": ref.sync_s,
        "video_sha256": ref.meta["video_sha256"],
        "trajectory_sha256": ref.meta["trajectory_sha256"],
        "calibration": ref.meta["calibration"],
        "sam_step": args.sam_step,
        "frames_detected": len(times),
        "detector_fit_s": round(fit_s, 2),
        "detect_ms_per_frame": round(spent / max(len(times), 1) * 1000, 2),
        "fit_frames": "dev_excl_sam: the dev sample is in-sample, w1-w3 are not",
        "results": results,
        "dev_by_block": by_block,
        "sensitivity": sensitivity,
        "chance_control": {
            "definition": "the same detections against references 120 s away (two blocks, so "
            "still dev): what this box density matches when the boxes have nothing to do with "
            "the vehicles. Report agreement above it, not the raw figure.",
            "results": control,
        },
        "sam_baseline": SAM_BASELINE,
    }
    tag = args.detector.replace(":", "-").replace(".", "-")
    path = S.write_record(record_dir, f"{tag}.json", payload)
    print(f"detector {args.detector}: {payload['detect_ms_per_frame']} ms/frame")
    print(
        f"{'set':14} {'recall':>8} {'[ci]':>16} {'prec.proxy':>10} {'det/frame':>9} {'frames':>6}"
    )
    for name, r in results.items():
        rec, pre = r["vehicle_frame_recall"], r["precision_proxy"]
        print(
            f"{name:14} {rec['value']!s:>8} {rec['ci95_cluster_bootstrap_vehicles']!s:>16} "
            f"{pre['value']!s:>10} {r['mean_detections_per_frame']!s:>9} {r['frames_scored']:>6}"
        )
    for name, r in control.items():
        print(
            f"chance {name:7} recall {r['vehicle_frame_recall']['value']} "
            f"precision {r['precision_proxy']['value']}"
        )
    print("->", path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--detector", help="median-blob, or package.module:factory")
    parser.add_argument("--detector-arg", action="append", default=[], metavar="KEY=VALUE")
    parser.add_argument("--sets", default=",".join(SET_NAMES), help="comma list of frame sets")
    parser.add_argument("--sam-step", type=int, default=1, help="use every n-th SAM window frame")
    parser.add_argument("--build-references", action="store_true")
    parser.add_argument("--reproduce-sam", action="store_true")
    parser.add_argument("--check-frame-map", action="store_true")
    parser.add_argument("--scratch", type=Path, default=DEFAULT_SCRATCH)
    parser.add_argument("--record-dir", type=Path, default=None)
    parser.add_argument("--progress", type=int, default=0, help="print every n frames")
    args = parser.parse_args(argv)
    args.scratch = args.scratch.expanduser()
    record_dir = args.record_dir or ROOT / "experiments/records" / f"cv-bakeoff-{utc_stamp()}"
    if args.build_references:
        return build_references(args.scratch)
    if args.reproduce_sam:
        return reproduce_sam(args.scratch, record_dir)
    if args.check_frame_map:
        return check_frame_map(record_dir)
    if not args.detector:
        parser.error(
            "give --detector NAME, --build-references, --reproduce-sam or --check-frame-map"
        )
    return run_detector(args, record_dir)


if __name__ == "__main__":
    raise SystemExit(main())
