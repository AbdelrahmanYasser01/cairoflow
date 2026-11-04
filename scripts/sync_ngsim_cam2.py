"""Clock offset between the camera 2 video and the NGSIM trajectories, from discharge sequences
at the four limit lines (DATA_REGISTER.md §13 step 5, §15). Local only.

    uv run --frozen python scripts/sync_ngsim_cam2.py

For each approach, a discharge zone is the band ZONE_PX deep just downstream of the v2 limit line,
across the approach lanes (image pixels). Two time series per zone:
- video: share of the zone's pixels that differ from the median background, every frame (10 fps);
- trajectories: number of NGSIM vehicle-body sample points inside the same zone mapped to the
  ground through the ECC registration, every 0.1 s.
Both are z-scored; their cross-correlation is averaged over the four zones (the phase pattern
between approaches rules out aliasing by the signal cycle) and searched over lags. The result is
video_time_s = (global_time_ms - T0_MS) / 1000 + offset_s, with T0 08:30:00.0 PDT. The peak's
width and its margin over the next peak at least 10 s away are reported, with each zone's own
best lag as a consistency check.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from cairoflow_contracts.evidence import ImageGeometry
from cairoflow_perception.ngsim import (
    US_SURVEY_FOOT_M,
    LocalFrame,
    kinematics,
    load_trajectories,
)

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
CSV_SHA256 = "c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e"
VIDEO = ROOT / "data/raw/ngsim-lankershim/camera2/lankershim-camera2-0830am-0845am.avi"
VIDEO_SHA256 = "04ef7435be3a107e7321900c384bb1ee7703ce753a122559cd2937fef341f530"
BACKGROUND = ROOT / "data/processed/ngsim-cam2-analysis/background.png"
BACKGROUND_SHA256 = "4a1668e4aa243e771d5242aae62561966ead8ee804878146d39e6009dc2a5e10"
ANALYSIS = ROOT / "data/processed/ngsim-cam2-analysis/analysis.json"
GEOMETRY = ROOT / "data/geometry/ngsim-lankershim-cam2/v2/image_geometry.json"
OUT = ROOT / "data/processed/ngsim-cam2-sync"
ORIGIN_FT = (6452083.0, 1873176.0)  # the analysis' local frame (ADR-017)
T0_MS = 1118935800000  # 08:30:00.0 PDT, first trajectory row
FPS = 10000000 / 999999
ZONE_PX = 25.0
DIFF = 35
LAGS_S = (-60.0, 282.0)  # video starts up to 60 s after or 282 s before 08:30 (>= 600 s overlap)
GRID_M = 0.1


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discharge_zones(geometry: ImageGeometry) -> dict[str, np.ndarray]:
    """Per arm: quadrilateral from the limit line ZONE_PX towards the junction, across lanes."""
    zones = {}
    for arm in ("N", "S", "W", "E"):
        lanes = [lane for lane in geometry.lanes if lane.arm == arm]
        first = np.asarray(lanes[0].polygon_px, float)
        last = np.asarray(lanes[-1].polygon_px, float)
        a, b = first[0], last[1]  # ends of the approach on the limit line
        inward = (first[0] + first[1]) / 2 - (first[2] + first[3]) / 2
        inward /= np.linalg.norm(inward)
        zones[arm] = np.array([a, b, b + inward * ZONE_PX, a + inward * ZONE_PX])
    return zones


def video_series(zones: dict[str, np.ndarray], background: np.ndarray) -> dict[str, np.ndarray]:
    masks = {}
    for arm, quad in zones.items():
        m = np.zeros(background.shape[:2], np.uint8)
        cv2.fillPoly(m, [np.round(quad).astype(np.int32)], 1)
        masks[arm] = m.astype(bool)
    bg = background.astype(np.int16)
    series: dict[str, list[float]] = {arm: [] for arm in zones}
    cap = cv2.VideoCapture(str(VIDEO))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            diff = np.abs(frame.astype(np.int16) - bg).max(axis=2) > DIFF
            for arm, m in masks.items():
                series[arm].append(float(diff[m].mean()))
    finally:
        cap.release()
    return {arm: np.asarray(v) for arm, v in series.items()}


def ground_polygon(quad_px: np.ndarray, h_i2g: np.ndarray) -> np.ndarray:
    p = np.c_[quad_px, np.ones(len(quad_px))] @ h_i2g.T
    return p[:, :2] / p[:, 2:3]


def trajectory_series(zones, h_i2g) -> tuple[dict[str, np.ndarray], int]:
    traj = load_trajectories(CSV)
    frame = LocalFrame(*ORIGIN_FT)
    x, y = frame.to_local(traj.x_ft, traj.y_ft)
    _, heading = kinematics(traj, frame)
    ok = np.isfinite(heading)
    h = np.radians(np.where(ok, heading, 0.0))
    length = traj.length_ft * US_SURVEY_FOOT_M
    tick = ((traj.global_time_ms - T0_MS) // 100).astype(np.int64)
    n_ticks = int(tick.max()) + 1
    series = {}
    for arm, quad in zones.items():
        poly = ground_polygon(quad, h_i2g)
        lo = poly.min(axis=0) - 1.0
        size = np.ceil((poly.max(axis=0) + 1.0 - lo) / GRID_M).astype(int)
        mask = np.zeros((size[1], size[0]), np.uint8)
        cv2.fillPoly(mask, [np.round((poly - lo) / GRID_M).astype(np.int32)], 1)
        counts = np.zeros(n_ticks)
        for a in np.linspace(0.05, 0.95, 7):  # body points behind the published front
            px = x - a * length * np.sin(h)
            py = y - a * length * np.cos(h)
            c = np.floor((px - lo[0]) / GRID_M).astype(int)
            r = np.floor((py - lo[1]) / GRID_M).astype(int)
            inside = ok & (c >= 0) & (c < size[0]) & (r >= 0) & (r < size[1])
            hit = np.zeros(len(x), bool)
            hit[inside] = mask[r[inside], c[inside]] > 0
            np.add.at(counts, tick[hit], 1.0)
        series[arm] = counts
    return series, n_ticks


def zscore(v: np.ndarray) -> np.ndarray:
    s = v.std()
    return (v - v.mean()) / s if s > 0 else v * 0


def correlate(video: np.ndarray, traj: np.ndarray, lags_ticks: np.ndarray) -> np.ndarray:
    """Mean product of z-scores (z-scored within the overlap), with the video shifted by each
    lag in ticks of 0.1 s: video index = trajectory index + lag."""
    out = np.full(len(lags_ticks), np.nan)
    for i, lag in enumerate(lags_ticks):
        start_v, start_g = max(lag, 0), max(-lag, 0)
        n = min(len(video) - start_v, len(traj) - start_g)
        if n < 6000:  # at least 600 s of overlap
            continue
        a, b = zscore(video[start_v : start_v + n]), zscore(traj[start_g : start_g + n])
        out[i] = float(np.mean(a * b))
    return out


def peaks(curve: np.ndarray, lags_s: np.ndarray) -> dict:
    best = int(np.nanargmax(curve))
    far = np.abs(lags_s - lags_s[best]) >= 10.0
    second = int(np.nanargmax(np.where(far, curve, -np.inf)))
    half = curve[best] / 2
    lo = hi = best
    while lo > 0 and curve[lo - 1] > half:
        lo -= 1
    while hi < len(curve) - 1 and curve[hi + 1] > half:
        hi += 1
    return {"lag_s": float(lags_s[best]), "peak": float(curve[best]),
            "next_peak_lag_s": float(lags_s[second]), "next_peak": float(curve[second]),
            "half_width_s": float((hi - lo) * 0.1 / 2)}  # fmt: skip


def plot(path: Path, lags_s, total, curves, offset) -> None:
    w, h = 900, 300
    img = np.full((h, w, 3), 255, np.uint8)
    x = ((lags_s - lags_s[0]) / (lags_s[-1] - lags_s[0]) * (w - 1)).astype(int)
    colours = {"N": (0, 160, 255), "S": (255, 120, 0), "W": (0, 180, 0), "E": (200, 0, 200)}
    for arm, c in [*curves.items(), ("all", total)]:
        y = (h / 2 - np.nan_to_num(c) * h * 0.8).astype(int)
        colour = colours.get(arm, (0, 0, 0))
        cv2.polylines(img, [np.c_[x, y].astype(np.int32)], False, colour, 2 if arm == "all" else 1)
    xo = int((offset - lags_s[0]) / (lags_s[-1] - lags_s[0]) * (w - 1))
    cv2.line(img, (xo, 0), (xo, h), (0, 0, 255), 1)
    text = f"cross-correlation vs lag ({lags_s[0]:.0f} to {lags_s[-1]:.0f} s); black = mean"
    cv2.putText(img, f"{text}; offset {offset:.1f} s", (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (0, 0, 0), 1)  # fmt: skip
    cv2.imwrite(str(path), img)


def main() -> int:
    if OUT.exists():
        print(f"refusing to overwrite {OUT}", file=sys.stderr)
        return 2
    for path, expected in ((CSV, CSV_SHA256), (VIDEO, VIDEO_SHA256),
                           (BACKGROUND, BACKGROUND_SHA256)):  # fmt: skip
        if sha256(path) != expected:
            print(f"{path} does not match its recorded SHA-256", file=sys.stderr)
            return 2
    geometry = ImageGeometry.model_validate_json(GEOMETRY.read_text("utf-8"))
    h_i2g = np.array(json.loads(ANALYSIS.read_text("utf-8"))["registration"]["image_to_ground"])
    zones = discharge_zones(geometry)
    video = video_series(zones, cv2.imread(str(BACKGROUND)))
    traj, n_ticks = trajectory_series(zones, h_i2g)
    lags_ticks = np.arange(int(LAGS_S[0] * 10), int(LAGS_S[1] * 10) + 1)
    lags_s = lags_ticks / 10.0
    curves = {arm: correlate(video[arm], traj[arm], lags_ticks) for arm in zones}
    total = np.nanmean(np.vstack(list(curves.values())), axis=0)
    offset = peaks(total, lags_s)["lag_s"]
    result = {
        "script": "scripts/sync_ngsim_cam2.py",
        "definition": "video_time_s = (global_time_ms - 1118935800000) / 1000 + offset_s; "
                      "video frame = video_time_s * 10000000 / 999999",
        "offset_s": offset, "video_frame_at_0830": offset * FPS,
        "video_frames": len(video["N"]), "trajectory_ticks": n_ticks,
        "zones_px": {arm: q.tolist() for arm, q in zones.items()},
        "combined": peaks(total, lags_s),
        "per_zone": {arm: peaks(c, lags_s) for arm, c in curves.items()},
    }  # fmt: skip
    OUT.mkdir(parents=True)
    (OUT / "sync.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    np.savez_compressed(OUT / "curves.npz", lags_s=lags_s, total=total, **curves)
    plot(OUT / "correlation.png", lags_s, total, curves, offset)
    print(json.dumps({k: result[k] for k in ("offset_s", "video_frame_at_0830", "combined",
                                             "per_zone")}, indent=2))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
