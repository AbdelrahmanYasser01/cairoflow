"""Identify the NGSIM Lankershim intersection seen by camera 2 and record time-independent
correspondences for it (DATA_REGISTER.md §14). Local only: no network, no API call.

    uv run --frozen python scripts/analyse_ngsim_cam2.py [--out DIR] [--geometry-out DIR]

Inputs (Git-ignored, checked by SHA-256): the 08:30-08:45 trajectories (§13) and the camera 2
08:30 video (§10). Steps:
1. Coordinate check: which State Plane zone puts the data inside the metadata bounding box.
2. Per intersection (int_id 1-4): centre, vehicles by entry direction (direction code of their
   first row; codes change along a trajectory), Lankershim lane structure.
3. Video: median background (paint) and activity map (where vehicles were), whole 18.7 min.
4. Registration: for every intersection, four rotations of Lankershim against the image axes, three
   seed scales and both handedness (64 hypotheses), ECC between the trajectory footprint raster
   and the activity map, from a hand-read junction centre near pixel (258, 195).
5. Consistency check against paint, which the registration does not use: lane lines in the
   background versus lane boundaries implied by the trajectories (midpoints of per-lane median
   positions), on the two Lankershim arms. Lane lines measured in the image (A) are paired with
   those boundaries (B) as line correspondences (C). This checks the registration across the road;
   it does not by itself identify the intersection.
6. Along the road: painted limit lines (independently read landmarks, through the registration)
   versus queue heads, with lanes assigned by lateral position (lane_id is 0 near the line).

No point correspondences and no calibration spec are written: the cross streets are tracked only
from their limit lines inward, so nothing time-independent fixes the along-road direction.

Writes DIR (default data/processed/ngsim-cam2-analysis, Git-ignored; figures with video pixels are
CC BY-SA 4.0 and contain trajectory data, CC BY-SA 3.0) and, with --geometry-out, the curated
record (no pixels; CC BY-SA 3.0 for the trajectory-derived values). Neither directory may exist
already.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np

from cairoflow_perception.ngsim import (
    CA_ZONE3_FTUS,
    CA_ZONE5_FTUS,
    DIRECTIONS,
    MOVEMENTS,
    US_SURVEY_FOOT_M,
    LocalFrame,
    along_across,
    boundaries,
    circular_mean_deg,
    footprint_occupancy,
    kinematics,
    lane_offsets,
    load_trajectories,
    queue_heads,
)
from cairoflow_perception.scene import (
    activity_map,
    median_background,
    normalise,
    paint_strength,
    profile_peaks,
    register,
)

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
CSV_SHA256 = "c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e"
VIDEO = ROOT / "data/raw/ngsim-lankershim/camera2/lankershim-camera2-0830am-0845am.avi"
VIDEO_SHA256 = "04ef7435be3a107e7321900c384bb1ee7703ce753a122559cd2937fef341f530"
CLIP_IDS = {"d010": "4a06259854300b67", "d300": "fd74a18fdb99bc73"}  # DATA_REGISTER.md §11
SEED_SCALES_PX_PER_M = (4.4, 5.2, 6.2)  # multi-start for the registration
LANE_ASSIGN_M = 1.6  # a row belongs to the nearest lane centre within this lateral distance
# Metadata PDF "Bounding Coordinates (Lankershim Boulevard)", degrees.
BBOX = {"west": -118.363, "east": -118.360, "south": 34.137, "north": 34.143}
ORIGIN_FT = (6452083.0, 1873176.0)  # rounded centroid of the int_id 2 rows; a reference point

IMAGE_W, IMAGE_H = 640, 480
WINDOW_BAND_V = 410  # rows from here down show the camera's window frame (glass edge ~v 421)
BACKGROUND_STEP, ACTIVITY_STEP, ACTIVITY_THRESHOLD = 40, 5, 35
RASTER_RES_M, RASTER_HALF_M = 0.2, 80.0
SEED_CENTRE_PX, SEED_PX_PER_M = (258.0, 195.0), 5.2  # hand-read from the background
NEAR_M = 60.0  # radius around an intersection for its bearings and lanes
STOPPED_M_S = 0.3
PROFILE_STEP_M, SAMPLE_STEP_M, TOPHAT_M = 0.05, 0.25, 1.5
MATCH_TOL_M = 0.9
LINE_SEARCH_PX, LINE_STEP_M, MIN_LINE_POINTS = 4.0, 0.5, 6

# Category A landmarks read independently by eye-and-profile on medians of three stills (review
# of 2026-10-27, DATA_REGISTER.md §14); not used in any fit, kept for later check points.
IMAGE_LANDMARKS = {
    "E_limit_north_kerb": ((370.2, 96.0), 2.0, "east crosswalk outer (limit) line at north kerb"),
    "E_limit_centreline": ((375.4, 198.5), 2.0, "east limit line crossing the yellow centreline"),
    "E_limit_south_kerb": ((377.5, 252.0), 2.5, "east limit line at the south kerb"),
    "E_inner_south_end": ((344.5, 254.0), 2.5, "east crosswalk inner line, south end"),
    "NE_crosswalk_corner": ((338.0, 85.0), 1.0, "L-corner of the north and east inner lines"),
    "N_limit_centreline": ((255.6, 59.8), 1.5, "north limit line crossing the yellow centreline"),
    "N_inner_centreline": ((257.9, 94.6), 1.5, "north yellow centreline end at the inner line"),
    "N_limit_east_end": ((325.0, 52.0), 3.0, "north limit line east end at the kerb curve"),
    "W_limit_median_nose": ((149.0, 186.0), 1.5, "west limit line north end at the median nose"),
    "W_limit_south_kerb": ((140.5, 286.0), 2.5, "west limit line south end at the kerb"),
    "S_zebra_east_upper": ((326.5, 264.5), 3.0, "south zebra crosswalk east end, upper corner"),
    "S_zebra_east_lower": ((330.0, 281.5), 2.5, "south zebra crosswalk east end, lower corner"),
    "E_pocket_arrow_tip": ((456.5, 107.0), 1.5, "east arm right-turn pocket arrow tip"),
    "S_through_arrow_tip": ((305.0, 295.5), 1.5, "south arm through arrow tip"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rounded(value, nd: int = 4):
    """JSON-ready copy with floats rounded and NaN/inf as null (numpy values converted)."""
    if isinstance(value, np.ndarray):
        return rounded(value.tolist(), nd)
    if isinstance(value, np.floating | np.integer | np.bool_):
        return rounded(value.item(), nd)
    if isinstance(value, float):
        return round(value, nd) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): rounded(v, nd) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [rounded(v, nd) for v in value]
    return value


# --------------------------------------------------------------------------- coordinates
def crs_check(traj) -> dict:
    xs = np.array([traj.x_ft.min(), traj.x_ft.max()])
    ys = np.array([traj.y_ft.min(), traj.y_ft.max()])
    out = {}
    for crs in (CA_ZONE5_FTUS, CA_ZONE3_FTUS):
        lat, lon = crs.to_geographic(xs, ys)
        tol = 5e-4
        inside = bool(
            BBOX["south"] - tol <= lat.min() and lat.max() <= BBOX["north"] + tol
            and BBOX["west"] - tol <= lon.min() and lon.max() <= BBOX["east"] + tol
        )  # fmt: skip
        out[crs.name] = {
            "lat_range_deg": [lat.min(), lat.max()],
            "lon_range_deg": [lon.min(), lon.max()],
            "inside_metadata_bbox_within_0.0005_deg": inside,
        }
    lat_c, lon_c = CA_ZONE5_FTUS.to_geographic(traj.x_ft.mean(), traj.y_ft.mean())
    out["zone5_at_data_centre"] = {
        "lat_deg": float(lat_c),
        "lon_deg": float(lon_c),
        "grid_convergence_deg": CA_ZONE5_FTUS.convergence_deg(float(lon_c)),
        "grid_scale_factor": CA_ZONE5_FTUS.scale_factor(float(lat_c)),
    }
    out["unit_check"] = {
        "v_length_median": float(np.median(traj.length_ft)),
        "v_width_median": float(np.median(traj.width_ft)),
        "reading": "a median length of 15.8 is a car in feet (4.8 m); in metres it would be a bus",
    }
    return out


# --------------------------------------------------------------------------- intersections
def entry_directions(traj) -> dict[int, int]:
    """Direction code of each vehicle's earliest row (codes may change along a trajectory)."""
    return {v: d for v, (d, _) in first_rows(traj).items()}


def first_rows(traj) -> dict[int, tuple[int, int]]:
    """Per vehicle: (direction code, row index) of its earliest row."""
    order = np.lexsort((traj.global_time_ms, traj.vehicle_id))
    vid = traj.vehicle_id[order]
    first = np.r_[True, vid[1:] != vid[:-1]]
    rows = order[first]
    return dict(zip(vid[first].tolist(), zip(traj.direction[rows].tolist(), rows.tolist(),
                                             strict=True), strict=True))  # fmt: skip


def intersection_table(traj, x, y, speed, heading) -> dict:
    first = first_rows(traj)
    changes = {
        name: int(sum(len(np.unique(col[traj.vehicle_id == v])) > 1 for v in first))
        for name, col in (("direction", traj.direction), ("movement", traj.movement))
    }
    centres = {k: (float(x[traj.int_id == k].mean()), float(y[traj.int_id == k].mean()))
               for k in (1, 2, 3, 4)}  # fmt: skip
    entered_at = {}  # vehicle -> intersection whose centre is nearest its first row, within NEAR_M
    for v, (_, row) in first.items():
        k, d = min(((k, math.dist((x[row], y[row]), c)) for k, c in centres.items()),
                   key=lambda t: t[1])  # fmt: skip
        entered_at[v] = k if d < NEAR_M else 0
    table: dict = {
        "vehicles_whose_code_changes_along_the_trajectory": changes,
        "entry_rule": "a vehicle enters at the intersection whose centre is nearest its first row "
                      f"(within {NEAR_M:g} m), with the direction code of that row",
    }  # fmt: skip
    for k in (1, 2, 3, 4):
        at = traj.int_id == k
        cx, cy = centres[k]
        near = np.hypot(x - cx, y - cy) < NEAR_M
        moving_through = near & (traj.movement == 1) & (speed > 3) & np.isfinite(heading)
        bearings = {}
        for code, name in DIRECTIONS.items():
            m = moving_through & (traj.direction == code)
            if m.sum() >= 50:
                bearings[name] = {
                    "bearing_deg": circular_mean_deg(heading[m]),
                    "rows": int(m.sum()),
                }
        volumes = {
            name: sum(entered_at[v] == k and first[v][0] == code for v in first)
            for code, name in DIRECTIONS.items()
        }
        rows_by_movement = {name: int((at & (traj.movement == code)).sum())
                            for code, name in MOVEMENTS.items()}  # fmt: skip
        lanes = {}
        if "NB" in bearings:
            along, across = along_across(x - cx, y - cy, bearings["NB"]["bearing_deg"])
            for code, sign in ((2, 1), (4, -1)):
                for label, lo, hi in (("approach", -40, -15), ("exit", 15, 40)):
                    m = (traj.direction == code) & (sign * along > lo) & (sign * along < hi)
                    m &= np.abs(across) < 30
                    lanes[f"{DIRECTIONS[code]}_{label}"] = [
                        {"lane_id": s.lane_id, "across_m": s.median_m, "rows": s.rows}
                        for s in lane_offsets(across[m], traj.lane_id[m])
                    ]
        table[str(k)] = {
            "centroid_state_plane_ft": [float(traj.x_ft[at].mean()), float(traj.y_ft[at].mean())],
            "centroid_local_m": [cx, cy],
            "rows": int(at.sum()),
            "through_bearings": bearings,
            "vehicles_entering_here_by_direction": volumes,
            "rows_by_movement_code": rows_by_movement,
            "lankershim_lanes_15_to_40_m": lanes,
        }
    return table


# --------------------------------------------------------------------------- registration
def raster_geometry(centre_xy) -> np.ndarray:
    """Ground metres -> raster (col, row): north up, RASTER_RES_M per cell."""
    x0, y0 = centre_xy[0] - RASTER_HALF_M, centre_xy[1] + RASTER_HALF_M
    return np.array(
        [[1 / RASTER_RES_M, 0, -x0 / RASTER_RES_M], [0, -1 / RASTER_RES_M, y0 / RASTER_RES_M],
         [0, 0, 1]], np.float64,
    )  # fmt: skip


def seed_raster_to_image(
    right_bearing: float, mirrored: bool, px_per_m: float = SEED_PX_PER_M
) -> np.ndarray:
    """Seed warp: image +u along `right_bearing`, +v 90 degrees clockwise of it (anticlockwise if
    mirrored), `px_per_m`, junction at SEED_CENTRE_PX, raster centre at the junction."""
    cells_per_px = 1 / (px_per_m * RASTER_RES_M)

    def cell(bearing: float) -> np.ndarray:
        b = math.radians(bearing)
        return np.array([math.sin(b), -math.cos(b)])

    v_bearing = right_bearing + (-90 if mirrored else 90)
    image_to_raster = np.eye(3)
    image_to_raster[:2, :2] = cells_per_px * np.column_stack([cell(right_bearing), cell(v_bearing)])
    centre = RASTER_HALF_M / RASTER_RES_M
    image_to_raster[:2, 2] = centre - image_to_raster[:2, :2] @ np.array(SEED_CENTRE_PX)
    warp = np.linalg.inv(image_to_raster)
    return warp / warp[2, 2]


def local_scale(raster_to_image: np.ndarray) -> tuple[float, float, float]:
    """Jacobian determinant, image px per raster cell and anisotropy at the raster centre."""
    c = RASTER_HALF_M / RASTER_RES_M
    h = raster_to_image
    p = h @ np.array([c, c, 1.0])
    u, v, w = p[0] / p[2], p[1] / p[2], p[2]
    jac = np.array([[h[0, 0] - u * h[2, 0], h[0, 1] - u * h[2, 1]],
                    [h[1, 0] - v * h[2, 0], h[1, 1] - v * h[2, 1]]]) / w  # fmt: skip
    det = float(np.linalg.det(jac))
    sv = np.linalg.svd(jac, compute_uv=False)
    return det, math.sqrt(abs(det)), float(sv[0] / sv[1])


def run_candidates(traj, x, y, heading, table, act_norm, mask) -> tuple[list[dict], dict]:
    length_m, width_m = traj.length_ft * US_SURVEY_FOOT_M, traj.width_ft * US_SURVEY_FOOT_M
    expected = SEED_PX_PER_M * RASTER_RES_M  # image px per raster cell
    results, warps = [], {}
    for k in ("1", "2", "3", "4"):
        centre = tuple(table[k]["centroid_local_m"])
        raster = normalise(footprint_occupancy(x, y, heading, length_m, width_m, centre,
                                               RASTER_HALF_M, RASTER_RES_M))  # fmt: skip
        nb = table[k]["through_bearings"]["NB"]["bearing_deg"]
        starts = [(rot, False, s) for rot in (0, 90, 180, 270) for s in SEED_SCALES_PX_PER_M]
        starts += [(rot, True, SEED_PX_PER_M) for rot in (0, 90, 180, 270)]
        for rot, mirrored, seed_scale in starts:
            reg = register(raster, act_norm, seed_raster_to_image(nb + rot, mirrored, seed_scale),
                           mask)  # fmt: skip
            det, scale, aniso = local_scale(reg.raster_to_image)
            plausible = bool(
                reg.converged and det > 0 and 0.5 < scale / expected < 2 and aniso < 1.5
            )
            key = (f"int{k}|image +u = NB bearing + {rot}|"
                   f"{'mirrored' if mirrored else 'proper'}|seed {seed_scale} px/m")  # fmt: skip
            results.append({
                "hypothesis": key, "int_id": int(k), "rotation_deg": rot,
                "seed_px_per_m": seed_scale, "handedness": "mirrored" if mirrored else "proper",
                "initial_correlation": reg.initial_correlation,
                "final_correlation": reg.correlation, "converged": reg.converged,
                "local_det": det, "image_px_per_raster_cell": scale, "anisotropy": aniso,
                "physically_plausible": plausible, "note": reg.note,
            })  # fmt: skip
            warps[key] = (reg.raster_to_image, centre)
            print(f"  {key:52s} r0 {reg.initial_correlation:+.3f} r {reg.correlation:+.3f} "
                  f"det {det:+.3g} plausible {plausible}", flush=True)  # fmt: skip

    def rank(r: dict) -> tuple[int, float]:
        return (0 if r["physically_plausible"] else 1, -(r["final_correlation"] or -9.0))

    results.sort(key=rank)
    return results, warps


# --------------------------------------------------------------------------- paint vs lanes
def ground_to_image(h_g2i: np.ndarray, xy: np.ndarray) -> np.ndarray:
    xy = np.asarray(xy, np.float64).reshape(-1, 2)
    p = np.c_[xy, np.ones(len(xy))] @ h_g2i.T
    return p[:, :2] / p[:, 2:3]


def arm_points(bearing: float, along, across) -> np.ndarray:
    b = math.radians(bearing)
    u, r = np.array([math.sin(b), math.cos(b)]), np.array([math.cos(b), -math.sin(b)])
    along, across = np.asarray(along, np.float64), np.asarray(across, np.float64)
    return along[..., None] * u + across[..., None] * r


def sample(image: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Bilinear samples at image points (..., 2); NaN outside the usable image."""
    u = np.ascontiguousarray(uv[..., 0], np.float32)
    v = np.ascontiguousarray(uv[..., 1], np.float32)
    out = cv2.remap(image.astype(np.float32), u, v, cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=float("nan"))  # fmt: skip
    out[(v >= WINDOW_BAND_V) | (u < 0) | (u > IMAGE_W - 1) | (v < 0)] = np.nan
    return out


def tophat(profile: np.ndarray, step: float) -> np.ndarray:
    half = max(int(TOPHAT_M / step / 2), 1)
    padded = np.pad(profile, half, mode="edge")
    base = np.array([np.nanmedian(padded[i : i + 2 * half + 1]) for i in range(len(profile))])
    return profile - base


def paint_profile(paint, h_g2i, bearing, along_window, across_range, axis="across"):
    """Mean paint strength across (or along) an arm, minus a running median (white top-hat)."""
    fine, coarse = PROFILE_STEP_M, SAMPLE_STEP_M
    along = np.arange(along_window[0], along_window[1] + 1e-9, coarse if axis == "across" else fine)
    across = np.arange(
        across_range[0], across_range[1] + 1e-9, fine if axis == "across" else coarse
    )
    grid_a, grid_c = np.meshgrid(along, across, indexing="ij")
    uv = ground_to_image(h_g2i, arm_points(bearing, grid_a, grid_c)).reshape(*grid_a.shape, 2)
    values = sample(paint, uv)
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            prof = np.nanmean(values, axis=0 if axis == "across" else 1)
    coords = across if axis == "across" else along
    ok = np.isfinite(prof)
    return coords[ok], tophat(prof[ok], fine)


def peaks_of(coords, prof, kind: str) -> list[dict]:
    noise = float(np.median(np.abs(prof - np.median(prof)))) * 1.4826
    return [{"position_m": p.position, "height": p.height, "width_m": p.width, "kind": kind}
            for p in profile_peaks(coords, prof, max(4 * noise, 3.0), 0.8)]  # fmt: skip


def match(boundaries_m: list[float], peaks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Each trajectory boundary to the nearest unused paint peak within MATCH_TOL_M."""
    pairs, used = [], set()
    for b in boundaries_m:
        options = [(abs(p["position_m"] - b), i) for i, p in enumerate(peaks) if i not in used]
        best = min(options, default=None)
        if best is not None and best[0] <= MATCH_TOL_M:
            used.add(best[1])
            p = peaks[best[1]]
            pairs.append({"boundary_m": b, "paint_m": p["position_m"], "paint_kind": p["kind"],
                          "residual_m": p["position_m"] - b,
                          "paint_height": p["height"]})  # fmt: skip
        else:
            pairs.append({"boundary_m": b, "paint_m": None, "paint_kind": None, "residual_m": None})
    return pairs, [p for i, p in enumerate(peaks) if i not in used]


def refine_image_line(paint, h_g2i, bearing, across_m, along_window):
    """Measure a painted line in image pixels: a perpendicular paint profile every LINE_STEP_M
    along the arm, the sub-pixel peak within +-LINE_SEARCH_PX of where the registration puts the
    line, then a total-least-squares line. The registration only says where to look."""
    pts = []
    for s in np.arange(along_window[0], along_window[1] + 1e-9, LINE_STEP_M):
        a, b = ground_to_image(h_g2i, arm_points(bearing, [s, s], [across_m, across_m + 0.5]))
        normal = (b - a) / np.linalg.norm(b - a)
        t = np.arange(-LINE_SEARCH_PX - 1, LINE_SEARCH_PX + 1 + 1e-9, 0.25)
        prof = sample(paint, (a + t[:, None] * normal)[None])[0]
        if not np.all(np.isfinite(prof)):
            continue
        found = profile_peaks(t, prof - np.median(prof), 12.0, 1.0)
        found = [p for p in found if abs(p.position) <= LINE_SEARCH_PX]
        if found:
            pts.append(a + max(found, key=lambda p: p.height).position * normal)
    if len(pts) < MIN_LINE_POINTS:
        return None
    pts = np.array(pts)
    centre = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - centre)
    rms = float(np.sqrt(np.mean(((pts - centre) @ vt[1]) ** 2)))
    return centre, vt[0], rms, len(pts)


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "data/processed/ngsim-cam2-analysis")
    parser.add_argument("--geometry-out", type=Path, default=None)
    args = parser.parse_args(argv)
    args.out = args.out.resolve()
    if args.geometry_out is not None:
        args.geometry_out = args.geometry_out.resolve()
    for directory in (args.out, args.geometry_out):
        if directory is not None and directory.exists():
            print(f"refusing to overwrite {directory}", file=sys.stderr)
            return 2
    for path, expected in ((CSV, CSV_SHA256), (VIDEO, VIDEO_SHA256)):
        if sha256(path) != expected:
            print(f"{path} does not match its recorded SHA-256", file=sys.stderr)
            return 2
    args.out.mkdir(parents=True)
    report: dict = {
        "script": "scripts/analyse_ngsim_cam2.py",
        "inputs": {
            "trajectories": {"path": str(CSV.relative_to(ROOT)), "sha256": CSV_SHA256,
                             "licence": "CC BY-SA 3.0 Unported "
                                        "(data.transportation.gov 8ect-6jqj)"},
            "video": {"path": str(VIDEO.relative_to(ROOT)), "sha256": VIDEO_SHA256,
                      "licence": "CC BY-SA 4.0 (data.transportation.gov uv3e-y54k)"},
        },
        "opencv": cv2.__version__, "numpy": np.__version__,
    }  # fmt: skip

    traj = load_trajectories(CSV)
    frame = LocalFrame(*ORIGIN_FT)
    x, y = frame.to_local(traj.x_ft, traj.y_ft)
    speed, heading = kinematics(traj, frame)
    report["rows"] = len(traj)
    report["crs"] = crs_check(traj)
    report["local_frame"] = {
        "origin_state_plane_ft": ORIGIN_FT, "crs": CA_ZONE5_FTUS.name,
        "axes": "x grid east, y grid north, metres (US survey foot = 1200/3937 m)",
        "origin_definition": "rounded centroid of the int_id 2 rows (a reference, not a survey)",
    }  # fmt: skip
    table = intersection_table(traj, x, y, speed, heading)
    report["intersections"] = table
    print("intersections tabulated", flush=True)

    background, n_bg = median_background(VIDEO, BACKGROUND_STEP)
    activity, n_act = activity_map(VIDEO, background, ACTIVITY_STEP, ACTIVITY_THRESHOLD)
    cv2.imwrite(str(args.out / "background.png"), background)
    report["video"] = {
        "background_frames": n_bg, "activity_frames": n_act,
        "background_png_sha256": sha256(args.out / "background.png"),
        "activity_sha256": hashlib.sha256(activity.tobytes()).hexdigest(),
    }  # fmt: skip
    mask = np.zeros((IMAGE_H, IMAGE_W), np.uint8)
    mask[:WINDOW_BAND_V] = 255
    act_norm = normalise(activity, top_percentile=100.0)
    print("video background and activity done", flush=True)

    candidates, warps = run_candidates(traj, x, y, heading, table, act_norm, mask)
    report["registration_candidates"] = candidates
    best = candidates[0]

    def top(rows: list[dict]) -> list:
        r = max(rows, key=lambda c: c["final_correlation"] or -9.0)
        return [r["final_correlation"], r["hypothesis"], r["physically_plausible"]]

    same_int = [c for c in candidates if c["int_id"] == best["int_id"]]
    others = [c for c in candidates if c["int_id"] != best["int_id"]]
    report["identification"] = {
        "best": best["hypothesis"], "best_correlation": best["final_correlation"],
        "same_intersection_other_rotation_or_handedness": top(
            [c for c in same_int if (c["rotation_deg"], c["handedness"])
             != (best["rotation_deg"], best["handedness"])]),
        "best_other_intersection_any_warp": top(others),
        "best_other_intersection_plausible_warp": top(
            [c for c in others if c["physically_plausible"]] or [{"final_correlation": None,
             "hypothesis": "none", "physically_plausible": False}]),
        "note": "a warp is plausible if converged, un-mirrored, within 2x of the seed scale and "
                "of anisotropy below 1.5",
    }  # fmt: skip
    r2i, centre = warps[best["hypothesis"]]
    h_g2i = r2i @ raster_geometry(centre)
    h_g2i /= h_g2i[2, 2]
    h_i2g = np.linalg.inv(h_g2i)
    h_i2g /= h_i2g[2, 2]
    report["registration"] = {
        "hypothesis": best["hypothesis"],
        "ground_to_image": h_g2i, "image_to_ground": h_i2g,
        "ground_frame": "local metres of local_frame",
        "status": "approximated: fitted to where vehicles were (no paint, no time); "
                  "see paint_check",
    }  # fmt: skip
    cv2.imwrite(
        str(args.out / "registration_overlay.png"),
        registration_overlay(background, act_norm, r2i, x, y, heading, traj, centre),
    )

    if best["int_id"] != 2:
        (args.out / "analysis.json").write_text(json.dumps(rounded(report), indent=2) + "\n")
        print("the best hypothesis is not int 2; the arm analysis below is written for int 2")
        return 1

    # Lankershim arms only: the cross streets are tracked only from their limit lines inward, so
    # their lane boundaries cannot be compared with painted lane lines (DATA_REGISTER.md §14.5).
    bearing_l = table["2"]["through_bearings"]["NB"]["bearing_deg"]
    arms = {  # arm: (axis bearing, along window in m from the origin along that bearing)
        "north": (bearing_l, (8.0, 32.0)),
        "south": (bearing_l, (-32.0, -8.0)),
    }
    white = paint_strength(background, "white")
    yellow = paint_strength(background, "yellow")
    near2 = np.hypot(x, y) < NEAR_M
    paint_check, arm_lines = {}, {}
    for arm, (bearing, window) in arms.items():
        along, across = along_across(x, y, bearing)
        inside = near2 & (along > window[0]) & (along < window[1]) & (np.abs(across) < 25)
        centres = []
        for code in (2, 4):
            m = inside & (traj.direction == code)
            centres += [(f"{DIRECTIONS[code]} lane {s.lane_id}", s.median_m, s.rows)
                        for s in lane_offsets(across[m], traj.lane_id[m])]  # fmt: skip
        centres.sort(key=lambda c: c[1])
        bnds = boundaries([c[1] for c in centres])
        lo, hi = centres[0][1] - 4, centres[-1][1] + 4
        peaks = []
        for kind, paint in (("white", white), ("yellow", yellow)):
            peaks += peaks_of(*paint_profile(paint, h_g2i, bearing, window, (lo, hi)), kind)
        yellows = [p["position_m"] for p in peaks if p["kind"] == "yellow"]
        pairs, unmatched = match(bnds, peaks)
        for p in pairs:  # a boundary between two yellow lines lies inside a painted median
            gaps = [y_ - p["boundary_m"] for y_ in yellows]
            p["inside_painted_median"] = any(-2.0 < g < 0 for g in gaps) and any(
                0 < g < 2.0 for g in gaps
            )
            if p["inside_painted_median"]:
                p["residual_m"] = None
        res = np.array([p["residual_m"] for p in pairs if p["residual_m"] is not None])
        paint_check[arm] = {
            "axis_bearing_deg": bearing, "along_window_m": window,
            "trajectory_lane_centres": [{"lane": c[0], "across_m": c[1], "rows": c[2]}
                                        for c in centres],
            "pairs": pairs, "paint_peaks_unmatched": unmatched,
            "matched": int(res.size), "boundaries": len(bnds),
            "boundaries_inside_painted_median": sum(p["inside_painted_median"] for p in pairs),
            "residual_mean_m": float(res.mean()) if res.size else None,
            "residual_rms_m": float(np.sqrt(np.mean(res**2))) if res.size else None,
            "residual_max_abs_m": float(np.abs(res).max()) if res.size else None,
        }  # fmt: skip
        lines = []
        for i, p in enumerate(pairs):
            if p["paint_m"] is None or p["inside_painted_median"]:
                continue
            paint = yellow if p["paint_kind"] == "yellow" else white
            measured = refine_image_line(paint, h_g2i, bearing, p["paint_m"], window)
            if measured is None:
                continue
            centre_px, direction_px, rms_px, n_pts = measured
            g0 = arm_points(bearing, window[0], p["boundary_m"])
            g1 = arm_points(bearing, window[1], p["boundary_m"])
            lines.append({
                "id": f"{arm}#{i}", "boundary_m": p["boundary_m"], "paint_kind": p["paint_kind"],
                "image_point_px": centre_px, "image_direction": direction_px,
                "image_rms_px": rms_px, "image_samples": n_pts,
                "ground_point_m": g0, "ground_direction": (g1 - g0) / np.linalg.norm(g1 - g0),
            })  # fmt: skip
        arm_lines[arm] = lines
        print(f"  arm {arm}: {len(centres)} lane centres, {res.size}/{len(bnds)} boundaries "
              f"matched to paint (rms {paint_check[arm]['residual_rms_m']}), {len(lines)} lines "
              "measured in the image", flush=True)  # fmt: skip
    report["paint_check"] = paint_check
    report["measured_lines"] = arm_lines

    report["limit_lines_vs_queue_heads"] = limit_lines_vs_queue_heads(
        traj, x, y, speed, near2, arms, paint_check, h_i2g, white, h_g2i
    )
    (args.out / "analysis.json").write_text(json.dumps(rounded(report), indent=2) + "\n")
    cv2.imwrite(str(args.out / "paint_check_overlay.png"),
                paint_overlay(background, h_g2i, arms, paint_check))  # fmt: skip
    cv2.imwrite(str(args.out / "v1_vs_v2_points.png"), v1_vs_v2_overlay(background))
    if args.geometry_out is not None:
        write_geometry(args.geometry_out, report)
    print(json.dumps(rounded(report["identification"]), indent=2))
    for arm, s in report["limit_lines_vs_queue_heads"].items():
        print(arm, rounded(s["gap_limit_line_to_head_median_m"]))
    return 0


LIMIT_LANDMARKS = {  # IMAGE_LANDMARKS on each approach's limit line
    "north": ("E_limit_north_kerb", "E_limit_centreline"),
    "south": ("W_limit_median_nose", "W_limit_south_kerb"),
}


def assign_lanes(across_m: np.ndarray, centres: list[dict]) -> np.ndarray:
    """lane_id of the nearest lane centre within LANE_ASSIGN_M, else 0 (published lane_id is 0
    inside the intersection zone, which starts a few metres before the limit line)."""
    ids = np.array([int(c["lane"].rsplit(" ", 1)[1]) for c in centres])
    pos = np.array([c["across_m"] for c in centres])
    d = np.abs(across_m[:, None] - pos[None, :])
    nearest = d.argmin(axis=1)
    return np.where(d[np.arange(len(across_m)), nearest] < LANE_ASSIGN_M, ids[nearest], 0)


def limit_lines_vs_queue_heads(traj, x, y, speed, near2, arms, paint_check, h_i2g, white, h_g2i):
    """Queue heads (front of the most advanced stopped vehicle per lane and frame, lanes assigned
    by lateral position) against the painted limit line, both along the approach."""
    out = {}
    for arm, code in (("north", 4), ("south", 2)):
        bearing, _ = arms[arm]
        along, across = along_across(x, y, bearing)
        sign = -1 if arm == "north" else 1  # distance increasing towards the junction
        m = near2 & (traj.direction == code) & (sign * along < 0) & (sign * along > -45)
        lanes = [c for c in paint_check[arm]["trajectory_lane_centres"]
                 if c["lane"].startswith(DIRECTIONS[code])]  # fmt: skip
        by_position = assign_lanes(across[m], lanes)
        heads = queue_heads(sign * along[m], by_position, traj.global_time_ms[m], speed[m],
                            STOPPED_M_S)  # fmt: skip
        coded = m & (traj.lane_id != 0)
        limit = {}
        for name in LIMIT_LANDMARKS[arm]:
            p = h_i2g @ np.r_[IMAGE_LANDMARKS[name][0], 1.0]
            a, c = along_across(p[0] / p[2], p[1] / p[2], bearing)
            limit[name] = {"along_towards_junction_m": float(sign * a), "across_m": float(c)}
        line = float(np.mean([v["along_towards_junction_m"] for v in limit.values()]))
        lo = min(c["across_m"] for c in lanes) - 1.5
        hi = max(c["across_m"] for c in lanes) + 1.5
        window = (0.0, 25.0) if arm == "north" else (-25.0, 0.0)
        transverse = peaks_of(*paint_profile(white, h_g2i, bearing, window, (lo, hi), "along"),
                              "white")  # fmt: skip
        out[arm] = {
            "approach": DIRECTIONS[code],
            "coordinate": "metres along the approach from the origin, increasing towards the "
                          "junction (negative = upstream of the origin)",
            "limit_line_landmarks_through_registration": limit,
            "limit_line_mean_m": line,
            "published_lane_id_ends_p99_m_by_lane": {
                int(ln): float(np.percentile(sign * along[coded & (traj.lane_id == ln)], 99))
                for ln in np.unique(traj.lane_id[coded])
                if (coded & (traj.lane_id == ln)).sum() >= 30
            },
            "queue_heads_front_bumper_lanes_by_position": [asdict(h) for h in heads],
            "gap_limit_line_to_head_median_m": {h.lane_id: line - h.median_m for h in heads},
            "gap_limit_line_to_head_q90_m": {h.lane_id: line - h.q90_m for h in heads},
            "transverse_paint_lines_found_by_profile": sorted(
                ({**p, "position_m": sign * p["position_m"]} for p in transverse),
                key=lambda p: p["position_m"]),
        }  # fmt: skip
    return out


def registration_overlay(background, act_norm, r2i, x, y, heading, traj, centre) -> np.ndarray:
    length_m, width_m = traj.length_ft * US_SURVEY_FOOT_M, traj.width_ft * US_SURVEY_FOOT_M
    raster = normalise(footprint_occupancy(x, y, heading, length_m, width_m, centre,
                                           RASTER_HALF_M, RASTER_RES_M))  # fmt: skip
    warped = cv2.warpPerspective(raster, r2i, (IMAGE_W, IMAGE_H), flags=cv2.INTER_LINEAR)
    heat = cv2.applyColorMap((warped * 255).astype(np.uint8), cv2.COLORMAP_JET)
    alpha = np.clip(warped * 1.5, 0, 0.7)[..., None]
    out = (background * (1 - alpha) + heat * alpha).astype(np.uint8)
    out = cv2.resize(out, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    caption(
        out, "NGSIM trajectory footprints (15 min) registered onto camera 2 by ECC; no time sync"
    )
    return out


def paint_overlay(background, h_g2i, arms, paint_check) -> np.ndarray:
    out = cv2.resize(background, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    for arm, (bearing, window) in arms.items():
        for c in paint_check[arm]["trajectory_lane_centres"]:
            a, b = 2 * ground_to_image(h_g2i, arm_points(bearing, window, [c["across_m"]] * 2))
            cv2.line(out, tuple(a.astype(int)), tuple(b.astype(int)), (0, 200, 0), 1)
        for p in paint_check[arm]["pairs"]:
            a, b = 2 * ground_to_image(h_g2i, arm_points(bearing, window, [p["boundary_m"]] * 2))
            colour = (0, 0, 255) if p["residual_m"] is None else (255, 0, 255)
            cv2.line(out, tuple(a.astype(int)), tuple(b.astype(int)), colour, 1)
    caption(out, "green: NGSIM lane centres; magenta: lane boundaries matched to paint; red: "
            "unmatched or inside the painted median")  # fmt: skip
    return out


V1_CALIBRATION = ROOT / "data/geometry/ngsim-lankershim-cam2/d300/calibration.json"


def v1_vs_v2_overlay(background) -> np.ndarray:
    """The provisional (v1) points in magenta against the independently read landmarks in cyan."""
    out = cv2.resize(background, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    v1 = json.loads(V1_CALIBRATION.read_text("utf-8"))["points"]
    for p in v1:
        u, v = (int(round(2 * c)) for c in p["image_px"])
        cv2.drawMarker(out, (u, v), (255, 0, 255), cv2.MARKER_TILTED_CROSS, 12, 2)
        cv2.putText(out, p["point_id"], (u + 6, v - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                    (255, 0, 255), 1)  # fmt: skip
    for name, ((u, v), sigma, _) in IMAGE_LANDMARKS.items():
        centre = (int(round(2 * u)), int(round(2 * v)))
        cv2.circle(out, centre, max(int(round(2 * sigma)), 3), (255, 255, 0), 1)
        cv2.putText(out, name, (centre[0] + 6, centre[1] + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.3,
                    (255, 255, 0), 1)  # fmt: skip
    caption(out, "magenta x: provisional v1 points (commit 7041e67); cyan o: landmarks read "
            "independently (radius = uncertainty)")  # fmt: skip
    return out


def caption(image: np.ndarray, text: str) -> None:
    cv2.rectangle(image, (0, 0), (image.shape[1], 24), (0, 0, 0), -1)
    cv2.putText(image, text, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
    h = image.shape[0]
    cv2.rectangle(image, (0, h - 24), (image.shape[1], h), (0, 0, 0), -1)
    cv2.putText(image, "Video: NGSIM Lankershim camera 2, USDOT FHWA, CC BY-SA 4.0. Trajectories: "
                "NGSIM, CC BY-SA 3.0. NOT GROUND TRUTH.", (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX,
                0.42, (255, 255, 255), 1)  # fmt: skip


def write_geometry(out: Path, report: dict) -> None:
    """Curated, pixel-free record of categories A (image), B (ground) and C (correspondences).

    No calibration spec is written: the only time-independent correspondences are lane lines along
    Lankershim, which fix the across-road direction but not the along-road one (§14.5).
    """
    out.mkdir(parents=True)
    record = {
        "schema": "cairoflow ngsim-cam2 control points v2 (DATA_REGISTER.md §14)",
        "produced_by": "scripts/analyse_ngsim_cam2.py --geometry-out " + str(out.relative_to(ROOT)),
        "inputs": report["inputs"],
        "licence": "values derived from NGSIM trajectories: CC BY-SA 3.0 Unported, attribution "
                   "U.S. DOT FHWA NGSIM (data.transportation.gov 8ect-6jqj); no video pixels",
        "local_frame": report["local_frame"],
        "identification": report["identification"],
        "registration": report["registration"],
        "category_A_image": {
            "measured_lines": {arm: [{k: v for k, v in line.items() if not k.startswith("ground")}
                                     for line in lines]
                               for arm, lines in report["measured_lines"].items()},
            "landmarks": {k: {"image_px": v[0], "uncertainty_px": v[1], "feature": v[2],
                              "method": "independent reading on the median of 3 stills"}
                          for k, v in IMAGE_LANDMARKS.items()},
        },
        "category_B_ground": {
            "lane_centres_and_boundaries": {
                arm: {k: v for k, v in c.items() if k in ("axis_bearing_deg", "along_window_m",
                                                          "trajectory_lane_centres")}
                for arm, c in report["paint_check"].items()},
            "queue_heads": report["limit_lines_vs_queue_heads"],
        },
        "category_C_correspondences": {
            "line_pairs": {arm: c["pairs"] for arm, c in report["paint_check"].items()},
            "points": [],
            "why_no_points": "a point needs a second, crossing line known in both frames; the "
                             "cross streets are tracked only from their limit lines inward, so "
                             "their painted lane lines have no trajectory counterpart, and "
                             "published stop positions disagree with the painted limit lines "
                             "(see category_B_ground.queue_heads)",
        },
        "calibration_files": {},
        "status": "no calibration: the across-Lankershim direction agrees with paint to lane "
                  "level, the along-Lankershim direction needs time-synchronised or surveyed "
                  "check points (DATA_REGISTER.md §14.5)",
    }  # fmt: skip
    (out / "control_points.json").write_text(json.dumps(rounded(record), indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
