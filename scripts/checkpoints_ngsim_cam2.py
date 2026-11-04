"""Along-road check points for camera 2: hand-read lead vehicles paired with the NGSIM vehicle
at the synced instant (DATA_REGISTER.md §15). Local only.

    uv run --frozen python scripts/checkpoints_ngsim_cam2.py

Inputs: the clock offset (data/processed/ngsim-cam2-sync/sync.json), the hand clicks of stopped
lead vehicles (v2/stop_gap_clicks.json, with the stopped flags of
data/processed/ngsim-cam2-stop-gap/stop_gap.json) and the trajectories. For each stopped click,
the NGSIM vehicles of the approach direction at tick round((frame / fps - offset) * 10) are taken
within MATCH_ACROSS_M of the clicked front across the road and MATCH_ALONG_M along it (positions
compared through the ECC registration); the nearest is the match if it is stopped too. Stopped
vehicles make the pair insensitive to a sub-second sync error.

Reports, per approach, the residual image-front-through-the-registration minus NGSIM-front,
split along and across Lankershim, and whether one along-road scale about the reference point
explains it (fitted scale against the car-length scale of §14.6).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

from cairoflow_contracts.evidence import CalibrationSpec, ControlPoint
from cairoflow_perception.calibration import Homography, fit_homography
from cairoflow_perception.ngsim import LocalFrame, along_across, kinematics, load_trajectories

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
SYNC = ROOT / "data/processed/ngsim-cam2-sync/sync.json"
CLICKS = ROOT / "data/geometry/ngsim-lankershim-cam2/v2/stop_gap_clicks.json"
STOP_GAP = ROOT / "data/processed/ngsim-cam2-stop-gap/stop_gap.json"
ANALYSIS = ROOT / "data/processed/ngsim-cam2-analysis/analysis.json"
OUT = ROOT / "data/processed/ngsim-cam2-sync/checkpoints.json"
V2_RECORD = ROOT / "data/geometry/ngsim-lankershim-cam2/v2/control_points.json"
V2_SYNC_RECORD = ROOT / "data/geometry/ngsim-lankershim-cam2/v2/sync_checkpoints.json"
CLIP_IDS = {"d010": "4a06259854300b67", "d300": "fd74a18fdb99bc73"}
ORIGIN_FT = (6452083.0, 1873176.0)
T0_MS = 1118935800000
FPS = 10000000 / 999999
MATCH_ACROSS_M, MATCH_ALONG_M, STOPPED_M_S = 1.6, 8.0, 0.5
# approach direction codes: north arm SB, south arm NB, west arm EB, east arm WB
DIRECTION = {"N": 4, "S": 2, "W": 1, "E": 3}
# matching windows (across, along Lankershim) in m: cross-street queues run across Lankershim
WINDOW = {"N": (1.6, 8.0), "S": (1.6, 8.0), "W": (4.0, 2.0), "E": (4.0, 2.0)}
CROSS_CLICKS = ROOT / "data/geometry/ngsim-lankershim-cam2/v2/cross_clicks.json"
CROSS_OUT = ROOT / "data/processed/ngsim-cam2-sync/cross"
GEOMETRY = ROOT / "data/geometry/ngsim-lankershim-cam2/v2/image_geometry.json"
CROSS_CROP = {"W": (190, 0, 300, 110), "E": (255, 255, 350, 360)}
CROSS_NEAR_M, CROSS_FRAMES, CROSS_SEPARATION_S = 8.0, 5, 60.0
# Camera 2 acceptance target (ADR-018): NGSIM's stated along-road accuracy, 4 ft = 1.22 m,
# rounded to 1.25 m. It applies to every held-out point and every painted lane line.
CHECK_TARGET_M = 1.25
LANE_LINE_MAX_M = CHECK_TARGET_M


def to_ground(h: np.ndarray, px) -> np.ndarray:
    p = h @ np.array([px[0], px[1], 1.0])
    return p[:2] / p[2]


def match(
    candidates_xy: np.ndarray, target_xy: np.ndarray, bearing: float,
    window: tuple[float, float] = (MATCH_ACROSS_M, MATCH_ALONG_M),
) -> int | None:  # fmt: skip
    """Index of the nearest candidate within the (across, along) windows, or None."""
    if len(candidates_xy) == 0:
        return None
    da, dc = along_across(candidates_xy[:, 0] - target_xy[0], candidates_xy[:, 1] - target_xy[1],
                          bearing)  # fmt: skip
    ok = (np.abs(dc) <= window[0]) & (np.abs(da) <= window[1])
    if not ok.any():
        return None
    idx = np.flatnonzero(ok)
    return int(idx[np.argmin(np.hypot(da[idx], dc[idx]))])


def main() -> int:
    if OUT.exists():
        print(f"refusing to overwrite {OUT}", file=sys.stderr)
        return 2
    sync = json.loads(SYNC.read_text("utf-8"))
    clicks = json.loads(CLICKS.read_text("utf-8"))
    stopped = {(r["frame"], r["lane"]) for r in json.loads(STOP_GAP.read_text("utf-8"))["rows"]
               if r["stopped"]}  # fmt: skip
    analysis = json.loads(ANALYSIS.read_text("utf-8"))
    h = np.array(analysis["registration"]["image_to_ground"])
    bearing = analysis["intersections"]["2"]["through_bearings"]["NB"]["bearing_deg"]
    traj = load_trajectories(CSV)
    frame = LocalFrame(*ORIGIN_FT)
    x, y = frame.to_local(traj.x_ft, traj.y_ft)
    speed, _ = kinematics(traj, frame)
    tick = (traj.global_time_ms - T0_MS) // 100
    pairs, unmatched = [], []
    frames = list(clicks["frames"])
    if CROSS_CLICKS.exists():  # cross streets: stopped in NGSIM is required below
        cross = json.loads(CROSS_CLICKS.read_text("utf-8"))["frames"]
        frames += cross
        stopped |= {(fc["frame"], lane) for fc in cross for lane in fc["lanes"]}
    for fc in frames:
        t_tick = round((fc["frame"] / FPS - sync["offset_s"]) * 10)
        rows = np.flatnonzero((tick == t_tick) & (traj.direction == DIRECTION[fc["arm"]]))
        xy = np.c_[x[rows], y[rows]]
        for lane, click in fc["lanes"].items():
            if (fc["frame"], lane) not in stopped:
                continue
            g = to_ground(h, click["front_px"])
            i = match(xy, g, bearing, WINDOW[fc["arm"]])
            if i is None or not (np.isfinite(speed[rows[i]]) and speed[rows[i]] < STOPPED_M_S):
                unmatched.append({"frame": fc["frame"], "lane": lane,
                                  "reason": "no stopped NGSIM vehicle in the window"})  # fmt: skip
                continue
            r = rows[i]
            d_along, d_across = along_across(g[0] - x[r], g[1] - y[r], bearing)
            pairs.append({
                "frame": fc["frame"], "arm": fc["arm"], "lane": lane, "tick": int(t_tick),
                "image_front_px": click["front_px"], "ngsim_vehicle_id": int(traj.vehicle_id[r]),
                "ngsim_lane_id": int(traj.lane_id[r]),
                "ngsim_front_m": [float(x[r]), float(y[r])],
                "ngsim_front_state_plane_ft": [float(traj.x_ft[r]), float(traj.y_ft[r])],
                "registered_image_front_m": g.tolist(),
                "residual_along_m": float(d_along), "residual_across_m": float(d_across),
            })  # fmt: skip
    # A vehicle stopped through a long red can be read in two frames; keep it once, so one car
    # cannot sit on both the fit and the check side.
    seen, kept = set(), []
    for p in pairs:
        key = (p["arm"], p["ngsim_vehicle_id"])
        if key in seen:
            unmatched.append({"frame": p["frame"], "lane": p["lane"],
                              "reason": "same NGSIM vehicle as an earlier pair"})  # fmt: skip
            continue
        seen.add(key)
        kept.append(p)
    pairs = kept
    summary = {}
    for arm in ("N", "S", "W", "E"):
        p = [q for q in pairs if q["arm"] == arm]
        if not p:
            continue
        al = np.array([q["residual_along_m"] for q in p])
        ac = np.array([q["residual_across_m"] for q in p])
        summary[arm] = {
            "pairs": len(p), "along_median_m": float(np.median(al)),
            "along_iqr_m": float(np.subtract(*np.percentile(al, [75, 25]))),
            "across_median_m": float(np.median(ac)),
            "across_iqr_m": float(np.subtract(*np.percentile(ac, [75, 25]))),
        }  # fmt: skip
    # One along-road scale about the reference point: NGSIM along = k * registered along
    lank = [q for q in pairs if q["arm"] in ("N", "S")]
    reg_al = np.array([along_across(*q["registered_image_front_m"], bearing)[0] for q in lank])
    ng_al = np.array([along_across(*q["ngsim_front_m"], bearing)[0] for q in lank])
    k = float(np.sum(reg_al * ng_al) / np.sum(reg_al**2)) if len(pairs) else math.nan
    after = ng_al - k * reg_al
    result = {
        "offset_s": sync["offset_s"], "pairs": pairs, "unmatched": unmatched,
        "summary": summary,
        "along_scale_fit": {
            "k_ngsim_over_registered": k,
            "residual_after_scale_m": {"median": float(np.median(after)),
                                       "rms": float(np.sqrt(np.mean(after**2)))},
            "note": "k > 1 means the registration shortens along-road distances; compare with "
                    "the car-length scale ratio of DATA_REGISTER.md §14.6 (1.11 N, 1.17 S)",
        },
    }  # fmt: skip
    result["calibration"] = calibrate(pairs, bearing)
    OUT.write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    if not V2_SYNC_RECORD.exists():  # curated, pixel-free record for the next calibration step
        record = {
            "schema": "cairoflow ngsim-cam2 synced check points (DATA_REGISTER.md §15)",
            "produced_by": "scripts/sync_ngsim_cam2.py, scripts/checkpoints_ngsim_cam2.py",
            "licence": "NGSIM-derived values: CC BY-SA 3.0 Unported (U.S. DOT FHWA NGSIM)",
            "sync": {k_: sync[k_] for k_ in ("definition", "offset_s", "video_frame_at_0830",
                                              "combined", "per_zone")},
            "pairs": pairs, "unmatched": unmatched, "summary": summary,
            "along_scale_fit": result["along_scale_fit"],
            "calibration_assessment": {k_: v for k_, v in result["calibration"].items()
                                       if k_ != "spec"},
            "status": ("calibration accepted" if result["calibration"]["accepted"] else
                       "check points only; calibration not accepted (calibration_assessment)")
                      + " (DATA_REGISTER.md §15)",
        }  # fmt: skip
        V2_SYNC_RECORD.write_text(json.dumps(record, indent=2) + "\n", "utf-8")
    print(json.dumps({k_: result[k_] for k_ in ("summary", "along_scale_fit")}, indent=2))
    print(json.dumps({k_: v for k_, v in result["calibration"].items() if k_ != "spec"}, indent=2))
    print("matched", len(pairs), "unmatched", len(unmatched))
    return 0


def spec_for(pairs: list[dict], clip_id: str) -> CalibrationSpec:
    """Fit on alternate frames of each approach, check on the others (frames are 60 s or more
    apart, so check vehicles are different vehicles in different signal cycles)."""
    check_frames = set()
    for arm in ("N", "S", "W", "E"):
        frames = sorted({p["frame"] for p in pairs if p["arm"] == arm})
        check_frames |= set(frames[1::2])
    points = tuple(
        ControlPoint(
            point_id=f"{p['arm']}{p['frame']}_{p['lane']}",
            image_px=(float(p["image_front_px"][0]), float(p["image_front_px"][1])),
            ground_m=(round(p["ngsim_front_m"][0], 3), round(p["ngsim_front_m"][1], 3)),
            role="check" if p["frame"] in check_frames else "fit",
            ground_provenance="approximated",
            source=(f"front of a stopped lead vehicle read by hand in source frame {p['frame']} "
                    f"(v2/stop_gap_clicks.json) and NGSIM vehicle {p['ngsim_vehicle_id']} at the "
                    "synced tick (offset from scripts/sync_ngsim_cam2.py); CC BY-SA 3.0"),
        )
        for p in pairs
    )  # fmt: skip
    return CalibrationSpec(clip_id=clip_id, image_width_px=640, image_height_px=480,
                           points=points)  # fmt: skip


def lane_line_check(homography: Homography) -> dict:
    """Painted lane lines (v2 control_points.json, category A) through the new calibration
    against the trajectory lane boundaries they were paired with: an independent lateral check."""
    record = json.loads(V2_RECORD.read_text("utf-8"))
    residuals = {}
    for arm, lines in record["category_A_image"]["measured_lines"].items():
        bearing = record["category_B_ground"]["lane_centres_and_boundaries"][arm][
            "axis_bearing_deg"
        ]
        for line in lines:
            p, d = np.asarray(line["image_point_px"]), np.asarray(line["image_direction"])
            offsets = []
            for t in (-30.0, 0.0, 30.0):
                g = homography.to_ground(p + t * d)
                offsets.append(along_across(g[0], g[1], bearing)[1])
            residuals[line["id"]] = float(np.mean(offsets) - line["boundary_m"])
    r = np.array(list(residuals.values()))
    return {"lines": len(r), "rms_m": float(np.sqrt(np.mean(r**2))),
            "max_abs_m": float(np.abs(r).max()), "per_line_m": residuals}  # fmt: skip


def calibrate(pairs: list[dict], bearing: float) -> dict:
    spec = spec_for(pairs, CLIP_IDS["d300"])
    homography, report = fit_homography(spec, target_max_check_error_m=CHECK_TARGET_M)
    lines = lane_line_check(homography)
    lines_pass = lines["max_abs_m"] <= LANE_LINE_MAX_M
    # The split by alternate frames is arbitrary: the swapped split must pass too, so that an
    # acceptance cannot hinge on which frames happen to be held out (DATA_REGISTER.md §15).
    swapped = spec.model_copy(
        update={
            "points": tuple(
                p.model_copy(update={"role": "fit" if p.role == "check" else "check"})
                for p in spec.points
            )
        }
    )
    _, swapped_report = fit_homography(swapped, target_max_check_error_m=CHECK_TARGET_M)
    swapped_errors = swapped_report.check_errors_m
    return {
        "arms": sorted({p["arm"] for p in pairs}),
        "fit_points": len(report.fit_point_ids), "check_points": len(report.check_errors_m),
        "fit_rms_m": report.fit_rms_m,
        "check_max_m": max(report.check_errors_m.values()) if report.check_errors_m else None,
        "check_median_m": float(np.median(list(report.check_errors_m.values()))),
        "accepted_by_repo_rule": report.accepted, "reasons": list(report.reasons),
        "lane_line_check": lines, "lane_line_check_passes": lines_pass,
        "swapped_split": {"check_points": len(swapped_errors),
                          "check_max_m": max(swapped_errors.values()),
                          "accepted_by_repo_rule": swapped_report.accepted,
                          "reasons": list(swapped_report.reasons)},
        "accepted": bool(report.accepted and swapped_report.accepted and lines_pass),
        "spec": spec.model_dump(mode="json"),
    }  # fmt: skip


def select_cross(arms: tuple[str, ...] = ("W", "E"), out: Path = CROSS_OUT,
                 separation_s: float = CROSS_SEPARATION_S) -> int:  # fmt: skip
    """Frames in which NGSIM has a stopped EB vehicle near the west limit line or a stopped WB
    vehicle near the east one (positions through the registration, which is reliable across
    Lankershim, i.e. along the cross streets); 5x gridded crops for reading by hand. Frames
    within CROSS_SEPARATION_S of frames already in cross_clicks.json are not chosen again."""
    if out.exists():
        print(f"refusing to overwrite {out}", file=sys.stderr)
        return 2
    import cv2  # only this mode reads video

    read_before = []
    if CROSS_CLICKS.exists():
        read_before = [fc for fc in json.loads(CROSS_CLICKS.read_text("utf-8"))["frames"]]

    from cairoflow_contracts.evidence import ImageGeometry

    sync = json.loads(SYNC.read_text("utf-8"))
    h = np.array(json.loads(ANALYSIS.read_text("utf-8"))["registration"]["image_to_ground"])
    geometry = ImageGeometry.model_validate_json(GEOMETRY.read_text("utf-8"))
    limits = {ln.arm: ln.segment_px for ln in geometry.lines if ln.role == "limit_line"}
    traj = load_trajectories(CSV)
    frame = LocalFrame(*ORIGIN_FT)
    x, y = frame.to_local(traj.x_ft, traj.y_ft)
    speed, _ = kinematics(traj, frame)
    tick = (traj.global_time_ms - T0_MS) // 100
    chosen = {}
    for arm in arms:
        a, b = (to_ground(h, p) for p in limits[arm])
        mid = (a + b) / 2
        near = (traj.direction == DIRECTION[arm]) & np.isfinite(speed) & (speed < 0.3)
        near &= np.hypot(x - mid[0], y - mid[1]) < CROSS_NEAR_M
        counts = np.bincount(tick[near] // 10, minlength=int(tick.max() // 10) + 1)
        before = [fc["frame"] / FPS - sync["offset_s"] for fc in read_before if fc["arm"] == arm]
        picks: list[int] = []
        for second in np.argsort(-counts, kind="stable"):
            if counts[second] == 0 or len(picks) == CROSS_FRAMES:
                break
            if all(abs(int(second) - p) >= separation_s for p in [*picks, *before]):
                picks.append(int(second))
        chosen[arm] = sorted(round((s + sync["offset_s"]) * FPS) for s in picks)
    out.mkdir(parents=True)
    wanted = {f: arm for arm, fs in chosen.items() for f in fs}
    cap = cv2.VideoCapture(str(ROOT / "data/raw/ngsim-lankershim/camera2/"
                               "lankershim-camera2-0830am-0845am.avi"))  # fmt: skip
    index = 0
    while wanted:
        ok, img = cap.read()
        if not ok:
            break
        if index in wanted:
            arm = wanted.pop(index)
            u0, v0, u1, v1 = CROSS_CROP[arm]
            crop = cv2.resize(img[v0:v1, u0:u1], None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
            for u in range(u0 - u0 % 5, u1, 5):
                x_ = (u - u0) * 5
                cv2.line(crop, (x_, 0), (x_, crop.shape[0]), (0, 140, 255) if u % 10 == 0
                         else (90, 90, 90), 1)  # fmt: skip
                if u % 10 == 0 and u >= u0:
                    cv2.putText(crop, str(u), (x_ + 1, 10), cv2.FONT_HERSHEY_SIMPLEX, 0.3,
                                (0, 255, 255), 1)  # fmt: skip
            for v in range(v0 - v0 % 5, v1, 5):
                y_ = (v - v0) * 5
                cv2.line(crop, (0, y_), (crop.shape[1], y_), (0, 140, 255) if v % 10 == 0
                         else (90, 90, 90), 1)  # fmt: skip
                if v % 10 == 0 and v >= v0:
                    cv2.putText(crop, str(v), (1, y_ - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.3,
                                (0, 255, 255), 1)  # fmt: skip
            cv2.imwrite(str(out / f"crop_{arm}_{index:05d}.png"), crop)
        index += 1
    cap.release()
    (out / "frames.json").write_text(json.dumps(chosen, indent=2) + "\n", "utf-8")
    print(json.dumps(chosen))
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["select-cross"]:  # select-cross [ARM ...]: a later round goes to cross2/
        args = sys.argv[2:]  # select-cross [ARM ...] [--separation S]
        sep = float(args[args.index("--separation") + 1]) if "--separation" in args else None
        args = [
            a
            for a in args
            if a != "--separation" and a != str(sep) and not a.replace(".", "").isdigit()
        ]
        chosen_arms = tuple(args) or ("W", "E")
        target = CROSS_OUT  # later rounds go to the next free cross2/, cross3/, ...
        if args:
            target = next(CROSS_OUT.with_name(f"cross{n}") for n in range(2, 100)
                          if not CROSS_OUT.with_name(f"cross{n}").exists())  # fmt: skip
        raise SystemExit(select_cross(chosen_arms, target, sep or CROSS_SEPARATION_S))
    raise SystemExit(main())
