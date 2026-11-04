"""Score the bake-off's box dumps and apply the selection rule.

    ~/.venvs/cv-bakeoff/bin/python scripts/cv_neural_score.py

Reads <scratch>/bakeoff/dumps (scripts/cv_neural_dump.py), sweeps confidence by filtering, scores
every configuration with the harness's own association and metrics, fuses the best neural model
with the blobs, and writes experiments/records/cv-bakeoff-<UTC stamp>/results.json. What is scored
is agreement with NGSIM, a correlated reference, never accuracy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for package in ("perception", "contracts"):
    path = str(ROOT / "packages" / package)
    if path not in sys.path:
        sys.path.insert(0, path)
sys.path.insert(0, str(ROOT / "scripts"))

import cv_bakeoff as H  # noqa: E402

from cairoflow_perception.local import neural as N  # noqa: E402
from cairoflow_perception.local import scoring as S  # noqa: E402

SWEEP = (0.10, 0.25, 0.40)  # the sweep the bake-off was asked for
CURVE = tuple(round(0.02 + 0.01 * i, 2) for i in range(59))  # 0.02..0.60, on the dev set only
MIN_PRECISION = 0.80
AGPL_MARGIN = 0.10
SELECTION_SET = "dev"
FUSION_IOU = (0.1, 0.3, 0.5)
FULL_SPAN_FRAMES = 9000
COVERAGE_CELL_PX = 8  # about 1.5 m on the ground
CANDIDATES = ("dfine-l", "rtdetrv2-r50", "rfdetr-m", "visdrone-yolo26s")
VARIANTS = ("native", "tiled2x")
STOPPED_M_S, MOVING_M_S = 0.5, 2.0  # camera_gate_v3.STOPPED_M_S, MOVING_M_S
TOP_STRIP_V_PX = 24  # the rows a top mask would take, for the sensitivity only
MAIN_SETS = ("dev", "dev_excl_sam", "w1", "w2", "w3")

LICENCE = {
    "dfine-l": "Apache-2.0",
    "rtdetrv2-r50": "Apache-2.0",
    "rfdetr-m": "Apache-2.0",
    "visdrone-yolo26s": "AGPL-3.0",
    "median-blob": "repository code",
}


def motion(ref: S.Ref) -> str:
    if math.isnan(ref.speed_m_s):
        return "unknown"
    if ref.speed_m_s < STOPPED_M_S:
        return "stopped"
    return "moving" if ref.speed_m_s >= MOVING_M_S else "slow"


def arm(ref: S.Ref) -> str:
    return ref.arm.split("-")[0]


def vehicle_class(length_m: float) -> str:
    """scripts/build_reference_analysis.py's rule: class from the published length."""
    if length_m < 3.0:
        return "motorcycle"
    if length_m < 7.5:
        return "car"
    return "truck" if length_m < 10.5 else "bus"


def split_recall(
    results: Mapping[int, S.FrameResult],
    refs: Mapping[int, Sequence[S.Ref]],
    key: Callable[[S.Ref], str],
) -> dict[str, dict[str, Any]]:
    """Vehicle-frame recall by a property of the reference; the groups sum to the whole."""
    tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for frame, r in results.items():
        hit = {m.vehicle_id for m in r.matches if m.ref_in_view}
        for ref in refs[frame]:
            if ref.in_view:
                group = tally[key(ref)]
                group[1] += 1
                group[0] += ref.vehicle_id in hit
    return {
        k: {"value": round(a / b, 4) if b else None, "numerator": a, "denominator": b}
        for k, (a, b) in sorted(tally.items())
    }


def persistence(
    results: Mapping[int, S.FrameResult], refs: Mapping[int, Sequence[S.Ref]]
) -> dict[str, Any]:
    """Of the in-view vehicles matched in a frame and still in view in the next one, the share
    matched there too. A detector that flickers around its threshold scores low here however good
    its recall is. Only consecutive frames count, so it means something on w1-w3 alone."""
    stayed = total = 0
    for frame, here in results.items():
        after = results.get(frame + 1)
        if after is None:
            continue
        matched = {m.vehicle_id for m in here.matches if m.ref_in_view}
        still = matched & {r.vehicle_id for r in refs[frame + 1] if r.in_view}
        total += len(still)
        stayed += len(still & {m.vehicle_id for m in after.matches if m.ref_in_view})
    return {
        "value": round(stayed / total, 4) if total else None,
        "numerator": stayed,
        "denominator": total,
    }


def boxes_by_frame(z: Mapping[str, np.ndarray], conf: float) -> dict[int, list[S.Detection]]:
    keep = z["score"] >= conf
    out: dict[int, list[S.Detection]] = defaultdict(list)
    for frame, box, score, label in zip(
        z["frame"][keep].tolist(),
        z["box"][keep].tolist(),
        z["score"][keep].tolist(),
        z["label"][keep].tolist(),
        strict=True,
    ):
        out[frame].append(S.Detection(*box, score, label))
    return dict(out)


class Scorer:
    def __init__(self, scratch: Path) -> None:
        self.ref = H.load_reference(scratch, "sync120")
        self.chance = H.load_reference(scratch, "chance")
        self.sets = H.frame_sets(1)
        self.frames = sorted({S.tenths(t) for v in self.sets.values() for t in v})
        self.tight = S.DEFAULT_PARAMS.scaled(0.5)

    def _pick(self, results: Mapping[int, S.FrameResult], name: str) -> dict[int, S.FrameResult]:
        return {f: results[f] for f in (S.tenths(t) for t in self.sets[name]) if f in results}

    def score(
        self, found: Mapping[int, list[S.Detection]], *, intervals: bool, sensitivity: bool
    ) -> dict[str, Any]:
        dets = {f: S.detections_to_dets(f, found.get(f, []), self.ref.ground) for f in self.frames}
        base = S.associate(dets, self.ref.refs, self.frames)
        control = S.associate(dets, self.chance.refs, self.frames)
        out: dict[str, Any] = {"sets": {}, "chance_control": {}}
        for name in MAIN_SETS:
            part = self._pick(base, name)
            row = S.summarise(part, intervals=intervals)
            row["recall_by_arm"] = split_recall(part, self.ref.refs, arm)
            row["recall_by_motion"] = split_recall(part, self.ref.refs, motion)
            row["next_frame_persistence"] = persistence(part, self.ref.refs)
            row["blank_frames_with_3_or_more_references"] = sum(
                r.n_det == 0 and r.n_ref_in_view >= 3 for r in part.values()
            )
            out["sets"][name] = row
            c = S.summarise(self._pick(control, name), intervals=False)
            out["chance_control"][name] = {
                "vehicle_frame_recall": c["vehicle_frame_recall"]["value"],
                "precision_proxy": c["precision_proxy"]["value"],
            }
        if sensitivity:
            tight = S.associate(dets, self.ref.refs, self.frames, self.tight)
            tight_control = S.associate(dets, self.chance.refs, self.frames, self.tight)
            out["windows_x0.5"] = {}
            for name in MAIN_SETS:
                a = S.summarise(self._pick(tight, name), intervals=False)
                b = S.summarise(self._pick(tight_control, name), intervals=False)
                out["windows_x0.5"][name] = {
                    "vehicle_frame_recall": a["vehicle_frame_recall"]["value"],
                    "precision_proxy": a["precision_proxy"]["value"],
                    "chance_recall": b["vehicle_frame_recall"]["value"],
                    "chance_precision": b["precision_proxy"]["value"],
                }
        return out

    def quick(self, found: Mapping[int, list[S.Detection]], conf: float) -> dict[str, Any]:
        """Recall and precision proxy on the selection set alone, for a confidence curve."""
        frames = [S.tenths(t) for t in self.sets[SELECTION_SET]]
        dets = {f: S.detections_to_dets(f, found.get(f, []), self.ref.ground) for f in frames}
        results = S.associate(dets, self.ref.refs, frames)
        hit, n_ref = S.vehicle_frame_recall(results)
        matched, n_det = S.precision_proxy(results)
        return {
            "conf": conf,
            "vehicle_frame_recall": round(hit / n_ref, 4) if n_ref else None,
            "precision_proxy": round(matched / n_det, 4) if n_det else None,
            "detections": n_det,
        }

    def coverage(self) -> np.ndarray:
        """Image cells any reference footprint touched on the scored frames, grown by one cell:
        where NGSIM has vehicles at all. A box outside it cannot match, whatever it is."""
        c = COVERAGE_CELL_PX
        grid = np.zeros((S.IMAGE_H_PX // c + 2, S.IMAGE_W_PX // c + 2), dtype=bool)
        for frame in self.frames:
            for r in self.ref.refs.get(frame, []):
                if r.footprint_px is None:
                    continue
                x1, y1, x2, y2 = r.footprint_px
                u1, u2 = max(int(x1 // c) - 1, 0), min(int(x2 // c) + 2, grid.shape[1])
                v1, v2 = max(int(y1 // c) - 1, 0), min(int(y2 // c) + 2, grid.shape[0])
                if u1 < u2 and v1 < v2:
                    grid[v1:v2, u1:u2] = True
        return grid

    def unmatched_split(
        self, found: Mapping[int, list[S.Detection]], grid: np.ndarray
    ) -> dict[str, Any]:
        """On the selection set: how much of the precision proxy's loss is boxes where NGSIM has
        no vehicles at all. A diagnostic; the rule still uses the proxy as defined."""
        c = COVERAGE_CELL_PX
        matched = inside = outside = 0
        for t in self.sets[SELECTION_SET]:
            frame = S.tenths(t)
            if frame not in self.ref.refs:
                continue
            boxes = found.get(frame, [])
            dets = S.detections_to_dets(frame, boxes, self.ref.ground)
            result = S.associate_frame(frame, dets, self.ref.refs[frame])
            matched += len(result.matches)
            for det_id, _group in result.unmatched_dets:
                d = boxes[int(det_id.split(":")[1])]
                u, v = (d.x1 + d.x2) / 2, (d.y1 + d.y2) / 2
                if grid[int(v // c), int(u // c)]:
                    inside += 1
                else:
                    outside += 1
        total = matched + inside + outside
        return {
            "detections": total,
            "matched": matched,
            "unmatched_inside_reference_coverage": inside,
            "unmatched_outside_reference_coverage": outside,
            "precision_proxy": round(matched / total, 4) if total else None,
            "precision_proxy_inside_coverage": (
                round(matched / (matched + inside), 4) if matched + inside else None
            ),
        }

    def label_table(self, found: Mapping[int, list[S.Detection]]) -> dict[str, Any]:
        """Detector label against the class NGSIM's vehicle length implies, on matched pairs. A
        length is weak evidence of class, so this is a consistency table, not a confusion
        matrix."""
        table: dict[str, Counter[str]] = defaultdict(Counter)
        for f in self.frames:
            if f not in self.ref.refs:
                continue
            dets = S.detections_to_dets(f, found.get(f, []), self.ref.ground)
            by_id = {r.vehicle_id: r for r in self.ref.refs[f]}
            for m in S.associate_frame(f, dets, self.ref.refs[f]).matches:
                index = int(m.det_id.split(":")[1])
                truth = vehicle_class(by_id[m.vehicle_id].length_m)
                table[truth][found[f][index].label] += 1
        return {k: dict(v) for k, v in sorted(table.items())}


def fuse(
    neural: Mapping[int, list[S.Detection]],
    blobs: Mapping[int, list[S.Detection]],
    iou: float,
) -> dict[int, list[S.Detection]]:
    """Neural boxes kept whole; a blob box is added only where it overlaps no neural box."""
    out: dict[int, list[S.Detection]] = {}
    for frame in set(neural) | set(blobs):
        a, b = list(neural.get(frame, [])), list(blobs.get(frame, []))
        pa = np.array([d[:4] for d in a], np.float32).reshape(-1, 4)
        pb = np.array([d[:4] for d in b], np.float32).reshape(-1, 4)
        out[frame] = a + [b[i] for i in N.fuse_keep(pa, pb, iou).tolist()]
    return out


def strip_top(
    found: Mapping[int, list[S.Detection]], top_v_px: int
) -> dict[int, list[S.Detection]]:
    return {f: [d for d in v if (d.y1 + d.y2) / 2 >= top_v_px] for f, v in found.items()}


def line(row: Mapping[str, Any]) -> str:
    sets = row["scores"]["sets"]
    cells = []
    for name in ("w1", "w2", "w3", "dev"):
        r, p = sets[name]["vehicle_frame_recall"]["value"], sets[name]["precision_proxy"]["value"]
        cells.append(f"{name} {r:.3f}/{p:.3f}")
    motion_dev = sets["dev"]["recall_by_motion"]
    stopped = motion_dev.get("stopped", {}).get("value")
    moving = motion_dev.get("moving", {}).get("value")
    return (
        f"{row['candidate']:18} {row['variant']:8} c={row['conf']!s:5} "
        + "  ".join(cells)
        + f"  stop {stopped} move {moving} n/f {sets['dev']['mean_detections_per_frame']}"
    )


def dev_of(row: Mapping[str, Any], metric: str) -> float:
    return row["scores"]["sets"][SELECTION_SET][metric]["value"]


def select(rows: Sequence[Mapping[str, Any]], grid: Sequence[float]) -> dict[str, Any]:
    """The selection rule, on the all-dev sample: best Apache at precision proxy >= 0.80; an AGPL
    model only if the best Apache trails it by 0.10 vehicle-frame recall or more."""

    def best(licence: str) -> Mapping[str, Any] | None:
        pool = [
            r
            for r in rows
            if r["licence"] == licence
            and r["conf"] in grid
            and dev_of(r, "precision_proxy") >= MIN_PRECISION
        ]
        return max(
            pool,
            key=lambda r: (dev_of(r, "vehicle_frame_recall"), dev_of(r, "precision_proxy")),
            default=None,
        )

    apache, agpl = best("Apache-2.0"), best("AGPL-3.0")

    def brief(r: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if r is None:
            return None
        return {
            "candidate": r["candidate"],
            "variant": r["variant"],
            "conf": r["conf"],
            "licence": r["licence"],
            "dev_vehicle_frame_recall": dev_of(r, "vehicle_frame_recall"),
            "dev_precision_proxy": dev_of(r, "precision_proxy"),
        }

    gap = None
    if apache is not None and agpl is not None:
        gap = round(
            dev_of(agpl, "vehicle_frame_recall") - dev_of(apache, "vehicle_frame_recall"), 4
        )
    agpl_eligible = gap is not None and gap >= AGPL_MARGIN
    # With no qualifying Apache model the rule names no winner: that is for a person to decide.
    winner = None if apache is None else (agpl if agpl_eligible else apache)
    overall = max(
        (r for r in (apache, agpl) if r is not None),
        key=lambda r: dev_of(r, "vehicle_frame_recall"),
        default=None,
    )
    return {
        "best_apache": brief(apache),
        "best_agpl": brief(agpl),
        "best_overall_by_recall": brief(overall),
        "agpl_minus_apache_recall": gap,
        "agpl_eligible": agpl_eligible,
        "winner": brief(winner),
    }


def operating_point(curve: Sequence[Mapping[str, Any]]) -> float | None:
    """The confidence with the highest dev recall at precision proxy >= the bar."""
    pool = [c for c in curve if c["precision_proxy"] is not None]
    pool = [c for c in pool if c["precision_proxy"] >= MIN_PRECISION]
    if not pool:
        return None
    return max(pool, key=lambda c: (c["vehicle_frame_recall"], c["precision_proxy"]))["conf"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scratch", type=Path, default=Path.home() / "scratch-cv")
    parser.add_argument("--record-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    dumps = args.scratch / "bakeoff/dumps"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    record = args.record_dir or ROOT / "experiments/records" / f"cv-bakeoff-{stamp}"
    scorer = Scorer(args.scratch)
    print(f"{len(scorer.frames)} frames, sets {[(k, len(v)) for k, v in scorer.sets.items()]}")

    def load(name: str) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
        with np.load(dumps / f"{name}.npz", allow_pickle=False) as z:
            arrays = {k: z[k] for k in z.files}
        return arrays, json.loads((dumps / f"{name}.json").read_text("utf-8"))

    rows: list[dict[str, Any]] = []
    metas: dict[str, dict[str, Any]] = {}
    arrays_of: dict[str, dict[str, np.ndarray]] = {}
    others: dict[str, dict[str, int]] = {}
    curves: dict[str, list[dict[str, Any]]] = {}
    points: dict[str, float | None] = {}
    for candidate in CANDIDATES:
        for variant in VARIANTS:
            name = f"{candidate}__{variant}"
            z, meta = load(name)
            timing_file = dumps / f"{name}_timing.json"
            if timing_file.is_file():
                meta["timing_pass"] = json.loads(timing_file.read_text("utf-8"))["timing"]
                meta["timing_pass_gpu"] = json.loads(timing_file.read_text("utf-8"))[
                    "gpu_after_run"
                ]
            metas[name], arrays_of[name] = meta, z
            strong = z["other_score"] >= 0.25
            others[name] = dict(Counter(z["other_label"][strong].tolist()).most_common(12))
            curves[name] = [scorer.quick(boxes_by_frame(z, c), c) for c in CURVE]
            points[name] = operating_point(curves[name])
            print(name, "operating point", points[name], flush=True)
            for conf in sorted({*SWEEP, *([points[name]] if points[name] is not None else [])}):
                chosen = conf == points[name]
                row = {
                    "candidate": candidate,
                    "variant": variant,
                    "conf": conf,
                    "licence": LICENCE[candidate],
                    "in_requested_sweep": conf in SWEEP,
                    "operating_point": chosen,
                    "scores": scorer.score(
                        boxes_by_frame(z, conf), intervals=chosen, sensitivity=True
                    ),
                }
                rows.append(row)
                print(line(row), "*" if chosen else "", flush=True)

    blob_z, blob_meta = load("median-blob__tuned")
    blob_timing = dumps / "median-blob__tuned_timing.json"
    if blob_timing.is_file():
        blob_meta["timing_pass"] = json.loads(blob_timing.read_text("utf-8"))["timing"]
    blobs = boxes_by_frame(blob_z, 0.0)
    blob_row = {
        "candidate": "median-blob",
        "variant": "tuned",
        "conf": None,
        "licence": LICENCE["median-blob"],
        "scores": scorer.score(blobs, intervals=True, sensitivity=True),
    }
    print(line(blob_row), flush=True)
    harness_path = next((ROOT / "experiments/records").glob("cv-bakeoff-*/median-blob.json"))
    harness = json.loads(harness_path.read_text("utf-8"))
    parity = {
        name: {
            "harness_record": [
                harness["results"][name]["vehicle_frame_recall"]["numerator"],
                harness["results"][name]["precision_proxy"]["numerator"],
            ],
            "this_script": [
                blob_row["scores"]["sets"][name]["vehicle_frame_recall"]["numerator"],
                blob_row["scores"]["sets"][name]["precision_proxy"]["numerator"],
            ],
        }
        for name in MAIN_SETS
    }
    same = all(v["harness_record"] == v["this_script"] for v in parity.values())
    print("scorer parity with the harness's median-blob record:", same)

    point_rows = [r for r in rows if r["operating_point"]]
    selection = {
        "rule": "Best Apache model at precision proxy >= 0.80. An AGPL model is eligible only if "
        "the best Apache trails it by 0.10 vehicle-frame recall or more. Blobs are fusion or "
        "fallback only.",
        "applied_on": f"the {SELECTION_SET} set (240 frames across all eight even blocks)",
        "governing": "operating_point: each model at its own confidence on a 0.01 grid. Recall "
        "falls about 0.02 per 0.01 of confidence near the bar, so a three-point grid would "
        "compare where the grid happened to land, not the models.",
        "operating_point": select(point_rows, sorted({r["conf"] for r in point_rows})),
        "requested_sweep_only": select([r for r in rows if r["in_requested_sweep"]], SWEEP),
    }
    print(json.dumps(selection, indent=1))

    picks = selection["operating_point"]
    targets = {
        f"{r['candidate']}__{r['variant']}": r
        for r in (picks["winner"], picks["best_apache"])
        if r is not None
    }
    fusion: dict[str, Any] = {}
    label_tables: dict[str, Any] = {}
    top_mask: dict[str, Any] = {}
    span: dict[str, Any] = {}
    dev_frames = {S.tenths(t) for t in scorer.sets[SELECTION_SET]}
    dev_blobs = {f: v for f, v in blobs.items() if f in dev_frames}
    grid = scorer.coverage()
    unmatched: dict[str, Any] = {"median-blob__tuned": scorer.unmatched_split(blobs, grid)}
    for name, pick in targets.items():
        z = arrays_of[name]
        system: dict[str, Any] = {"curves": {}, "operating_points": {}, "rows": []}
        for iou in FUSION_IOU:
            curve = []
            for c in CURVE:
                dev_neural = {f: v for f, v in boxes_by_frame(z, c).items() if f in dev_frames}
                curve.append(scorer.quick(fuse(dev_neural, dev_blobs, iou), c))
            point = operating_point(curve)
            system["curves"][str(iou)] = curve
            system["operating_points"][str(iou)] = point
            for conf in sorted({*SWEEP, pick["conf"], *([point] if point is not None else [])}):
                chosen = conf == point
                fused = fuse(boxes_by_frame(z, conf), blobs, iou)
                row = {
                    "candidate": f"{pick['candidate']}+blobs",
                    "variant": pick["variant"],
                    "conf": conf,
                    "licence": pick["licence"],
                    "fusion_iou": iou,
                    "fusion_operating_point": chosen,
                    "scores": scorer.score(fused, intervals=chosen, sensitivity=chosen),
                }
                system["rows"].append(row)
                print(line(row), "iou", iou, "*" if chosen else "", flush=True)
                if chosen:
                    unmatched[f"{name}+blobs@{conf}/iou{iou}"] = scorer.unmatched_split(fused, grid)
        fusion[name] = system
        found = boxes_by_frame(z, pick["conf"])
        clipped = scorer.score(strip_top(found, TOP_STRIP_V_PX), intervals=False, sensitivity=False)
        refs_in_strip = sum(
            1
            for f in scorer.frames
            for r in scorer.ref.refs.get(f, [])
            if r.in_view
            and r.footprint_px is not None
            and (r.footprint_px[1] + r.footprint_px[3]) / 2 < TOP_STRIP_V_PX
        )
        top_mask[name] = {
            "rows_masked": TOP_STRIP_V_PX,
            "reference_vehicle_frames_centred_in_rows": refs_in_strip,
            "sets": {
                k: {
                    "vehicle_frame_recall": clipped["sets"][k]["vehicle_frame_recall"]["value"],
                    "precision_proxy": clipped["sets"][k]["precision_proxy"]["value"],
                }
                for k in ("w1", "w2", "w3", "dev")
            },
        }
        timing = metas[name].get("timing_pass", metas[name]["timing"])
        span[name] = {
            "frames": FULL_SPAN_FRAMES,
            "basis": f"{timing['frames']} dev frames on this GPU, one frame per call, float32",
            "detect_fps": timing["detect_fps"],
            "end_to_end_fps": timing["end_to_end_fps"],
            "detect_only_minutes": round(FULL_SPAN_FRAMES / timing["detect_fps"] / 60, 1),
            "end_to_end_minutes": round(FULL_SPAN_FRAMES / timing["end_to_end_fps"] / 60, 1),
            "note": "An estimate, not a run: the full span holds the odd blocks, which only the "
            "pre-registered gate may read.",
        }
    for name, conf in points.items():
        if conf is not None:
            found = boxes_by_frame(arrays_of[name], conf)
            label_tables[f"{name}@{conf}"] = scorer.label_table(found)
            unmatched[f"{name}@{conf}"] = scorer.unmatched_split(found, grid)
    print(json.dumps(unmatched, indent=0))

    payload = {
        "schema": "cv-bakeoff-results/1",
        "stamp_utc": stamp,
        "evidence_class": "agreement with a correlated reference, not accuracy; dev frames only; "
        "confidences were chosen on the dev sample, and w1-w3 sit in dev blocks, so nothing here "
        "is a held-out result",
        "repo_head": H.git_head(),
        "harness_version": S.HARNESS_VERSION,
        "reference": {
            "sync_s": scorer.ref.sync_s,
            "video_sha256": scorer.ref.meta["video_sha256"],
            "trajectory_sha256": scorer.ref.meta["trajectory_sha256"],
            "calibration": scorer.ref.meta["calibration"],
        },
        "held_out_rule": "block = floor((source_s - 120) / 60); odd blocks were not read",
        "frames": {
            "scored_union": len(scorer.frames),
            **{k: len(v) for k, v in scorer.sets.items()},
        },
        "definitions": {
            "vehicle_frame_recall": "matched in-view reference vehicle-frames / in-view "
            "reference vehicle-frames",
            "precision_proxy": "detections matched to a reference / detections with a ground "
            "position; an unmatched box is not necessarily false",
            "match_window_m": {"across": 3.0, "along": 8.0},
            "stopped": f"speed < {STOPPED_M_S} m/s",
            "moving": f"speed >= {MOVING_M_S} m/s",
            "slow": "between stopped and moving",
            "arm": "the arm of the lane NGSIM geometry assigns; no-lane if none",
            "throughput": "detect_fps: painting, scaling, tiling, model, class map and NMS, one "
            "frame per call, batch = the frame's tiles; float32; no decode. timing_pass is a "
            "dedicated run on spread dev frames, back to back under the heavy lock, and is the "
            "figure to quote; timing is the scoring run, which other work may have disturbed",
            "vram": "torch allocator peak in the process (allocated, reserved); the CUDA context "
            "is extra, see gpu_after_run.device_used_mib, which also holds other processes",
        },
        "sweep": {
            "requested": list(SWEEP),
            "operating_point_grid": [CURVE[0], CURVE[-1], 0.01],
        },
        "candidates": {
            name: {
                k: meta.get(k)
                for k in (
                    "candidate",
                    "variant",
                    "seed",
                    "score_floor",
                    "settings",
                    "timing",
                    "timing_pass",
                    "timing_pass_gpu",
                    "gpu_before_load",
                    "gpu_after_run",
                    "repeatability",
                    "frame_count",
                    "load_s",
                )
            }
            for name, meta in {**metas, "median-blob__tuned": blob_meta}.items()
        },
        "non_vehicle_classes_at_0.25": others,
        "dev_curves": curves,
        "operating_points": points,
        "results": rows,
        "blob": blob_row,
        "blob_scorer_parity": {"equal": same, "counts": parity},
        "fusion": {
            "rule": "neural boxes kept whole; a blob box added where it overlaps no neural box "
            "by fusion_iou, then NMS among the added blobs",
            "note": "run for the rule's winner and for the best Apache model, each at every "
            "fusion_iou; confidence and fusion_iou were chosen on the dev sample",
            "systems": fusion,
        },
        "selection": selection,
        "full_span_estimate": span,
        "label_consistency": {
            "note": "detector label by the class NGSIM's length implies, on matched pairs",
            "tables": label_tables,
        },
        "top_strip_sensitivity": top_mask,
        "unmatched_by_reference_coverage": {
            "note": "dev set; coverage = image cells any reference footprint touched, grown by "
            "one 8 px cell. A diagnostic: the selection rule uses the proxy as defined.",
            "rows": unmatched,
        },
        "sam_baseline": H.SAM_BASELINE,
    }
    out = S.write_record(record, "results.json", payload)
    print("->", out, f"{out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
