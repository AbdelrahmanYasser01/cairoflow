"""Image-side geometry v2 for NGSIM Lankershim camera 2 (intersection 2), in pixels.

    uv run --frozen python scripts/draw_ngsim_cam2_geometry_v2.py

Replaces the provisional v1 drawing (scripts/draw_ngsim_cam2_geometry.py, kept unchanged) with
the corrections of DATA_REGISTER.md §14.4: 6 / 5 / 3 / 3 approach lanes (north / south / west /
east legs) and limit lines on the crosswalk edges. Pixels only: no ground metres, because no
calibration is accepted for this camera (§14.5, ADR-017).

Method. Every line starts from a seed (two pixels) taken from the independent reading of
2026-10-27 (§14.4). It is then measured on the full-video median background
(data/processed/ngsim-cam2-analysis/background.png, made by scripts/analyse_ngsim_cam2.py and
checked by SHA-256): a perpendicular profile every 2 px along the seed, the sub-pixel peak within
+-3 px (paint strength for painted lines, brightness gradient for kerb and zebra edges), a total-
least-squares line, outlier removal. A line with too few samples keeps its seed and is marked
hand-read. Lanes are the quadrilaterals between adjacent boundary lines, cut by the limit line and
by a fixed outer line (the extent drawn, not a road feature).

Arms are compass legs from the identification (§14.3): image right = N (bearing ~26), left = S,
top = W (~299), bottom = E (~119). Turns come from painted arrows where readable, otherwise from
the published NGSIM lane codes (11+ left-turn bays, 31+ right-turn bays), otherwise unknown.

Writes data/geometry/ngsim-lankershim-cam2/v2/image_geometry.json (no pixels of the video) and two
overlays in data/processed/ngsim-cam2-analysis/ (CC BY-SA 4.0 video pixels). Never overwrites.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from cairoflow_contracts.evidence import ImageGeometry, ImageLane, ImageLine
from cairoflow_perception.scene import paint_strength, profile_peaks

ROOT = Path(__file__).resolve().parents[1]
BACKGROUND = ROOT / "data/processed/ngsim-cam2-analysis/background.png"
BACKGROUND_SHA256 = "4a1668e4aa243e771d5242aae62561966ead8ee804878146d39e6009dc2a5e10"
V1_GEOMETRY = ROOT / "data/geometry/ngsim-lankershim-cam2/d300/geometry.json"
OUT_JSON = ROOT / "data/geometry/ngsim-lankershim-cam2/v2/image_geometry.json"
OUT_DIR = ROOT / "data/processed/ngsim-cam2-analysis"
CLIP_IDS = ("4a06259854300b67", "fd74a18fdb99bc73")  # DATA_REGISTER.md §11 (d010, d300)
USABLE_V_MAX = 410.0  # window frame from about v 421 down
SEARCH_PX, STEP_PX, HAND_ERROR_PX = 3.0, 2.0, 3.0
EDGE_ERROR_FLOOR_PX = 2.0  # a kerb or zebra edge is less sharply defined than its fit rms says
MIN_HEIGHT = {"white": 10.0, "yellow": 8.0, "edge": 8.0}
# Share of profile steps that must agree on the line: dashed paint is mostly gaps; an edge that
# only some steps find may be another edge (kerb against gutter, lawn or arrow), so keep the seed.
MIN_COVERAGE = {"white": 0.25, "yellow": 0.25, "edge": 0.5}
READING = "seeded from the independent reading of 2026-10-27 (DATA_REGISTER.md §14.4)"

# line_id: (arm, role, kind, seed start, seed end). kind: white / yellow paint, or edge.
SEEDS = {
    # North leg (image right): SB approach on the top half, 6 lanes from the kerb to the yellow
    "N_kerb": ("N", "kerb_edge", "edge", (380.0, 96.0), (470.0, 96.0)),
    "N_31_3": ("N", "lane_line", "white", (380.0, 118.6), (470.0, 120.2)),
    "N_3_2": ("N", "lane_line", "white", (380.0, 133.2), (470.0, 135.6)),
    "N_2_1": ("N", "lane_line", "white", (380.0, 149.2), (470.0, 150.8)),
    "N_1_12": ("N", "lane_line", "white", (380.0, 165.7), (470.0, 166.8)),
    "N_12_11": ("N", "lane_line", "white", (380.0, 182.6), (470.0, 182.9)),
    "N_centre": ("N", "centreline", "yellow", (380.0, 199.5), (470.0, 198.9)),
    "N_limit": ("N", "limit_line", "white", (370.2, 96.0), (375.4, 198.5)),
    "N_exit": ("N", "exit_line", "white", (375.4, 198.5), (377.5, 252.0)),
    # South leg (image left): NB approach on the bottom half, 5 lanes beside the painted median
    "S_median": ("S", "centreline", "yellow", (15.0, 187.0), (130.0, 186.6)),
    "S_11_1": ("S", "lane_line", "white", (15.0, 205.3), (130.0, 205.3)),
    "S_1_2": ("S", "lane_line", "white", (15.0, 224.0), (130.0, 224.0)),
    "S_2_3": ("S", "lane_line", "white", (15.0, 245.0), (130.0, 245.0)),
    "S_3_4": ("S", "lane_line", "white", (15.0, 264.4), (130.0, 264.4)),
    "S_kerb": ("S", "kerb_edge", "edge", (15.0, 282.0), (130.0, 282.0)),
    "S_limit": ("S", "limit_line", "white", (149.0, 186.0), (140.5, 286.0)),
    # West leg (image top): EB approach on the left half, 3 lanes, lines lean du/dv ~ +0.065
    "W_kerb": ("W", "kerb_edge", "edge", (200.6, 4.0), (203.0, 60.0)),
    "W_1_2": ("W", "lane_line", "white", (218.1, 4.0), (222.0, 60.0)),
    "W_2_3": ("W", "lane_line", "white", (234.9, 4.0), (238.4, 60.0)),
    "W_centre": ("W", "centreline", "yellow", (252.0, 4.0), (255.6, 59.8)),
    "W_limit": ("W", "limit_line", "white", (208.0, 66.0), (255.6, 59.8)),
    "W_exit": ("W", "exit_line", "white", (255.6, 59.8), (325.0, 52.0)),
    # East leg (image bottom): WB approach on the right half, 3 lanes, lean du/dv ~ +0.083
    "E_planter": ("E", "lane_line", "white", (271.0, 296.0), (279.6, 400.0)),
    "E_0_1": ("E", "lane_line", "white", (294.2, 296.0), (302.8, 400.0)),
    "E_1_2": ("E", "lane_line", "white", (314.5, 296.0), (323.1, 400.0)),
    "E_kerb": ("E", "kerb_edge", "edge", (331.5, 296.0), (340.1, 400.0)),
    "E_limit": ("E", "limit_line", "edge", (270.5, 289.3), (331.0, 281.1)),
    "E_exit": ("E", "exit_line", "edge", (221.0, 295.9), (262.0, 290.4)),
}
LIMIT_NOTES = {
    "N": "outer line of the two-line crosswalk; it is the limit line (no separate stop bar)",
    "S": "single painted stop line from the median nose to the kerb",
    "W": "outer line of the two-line crosswalk (the inner line is v1's 'stop line')",
    "E": "lower edge of the zebra crosswalk",
}
# Outer cut of each arm's lanes (a line, two pixels): the extent drawn, not a road feature.
OUTER = {
    "N": ((470.0, 80.0), (470.0, 210.0)),
    "S": ((12.0, 170.0), (12.0, 300.0)),
    "W": ((190.0, 4.0), (270.0, 4.0)),
    "E": ((260.0, 400.0), (350.0, 400.0)),
}
# lane_id: (arm, boundary line a, boundary line b, turns, turns source, published lane)
CODE_BAY_L = "NGSIM lane code 11+ = left-turn bay (metadata); arrow hidden by queues"
CODE_BAY_R = "NGSIM lane code 31+ = right-turn bay (metadata); right-turn arrow painted"
CODE_THRU = "NGSIM numbered lane, not a bay; arrows hidden by queues (right turn not established)"
NO_ARROW = "no arrow visible; the cross street has no published lane ids"
LANES = {
    "N_SB31": ("N", "N_kerb", "N_31_3", ("right",), CODE_BAY_R, "NGSIM SB lane_id 31"),
    "N_SB3": ("N", "N_31_3", "N_3_2", ("through",), CODE_THRU, "NGSIM SB lane_id 3"),
    "N_SB2": ("N", "N_3_2", "N_2_1", ("through",), CODE_THRU, "NGSIM SB lane_id 2"),
    "N_SB1": ("N", "N_2_1", "N_1_12", ("through",), CODE_THRU, "NGSIM SB lane_id 1"),
    "N_SB12": ("N", "N_1_12", "N_12_11", ("left",), CODE_BAY_L, "NGSIM SB lane_id 12"),
    "N_SB11": ("N", "N_12_11", "N_centre", ("left",), CODE_BAY_L, "NGSIM SB lane_id 11"),
    "S_NB11": ("S", "S_median", "S_11_1", ("left",), CODE_BAY_L, "NGSIM NB lane_id 11"),
    "S_NB1": ("S", "S_11_1", "S_1_2", ("through",), CODE_THRU, "NGSIM NB lane_id 1"),
    "S_NB2": ("S", "S_1_2", "S_2_3", ("through",), CODE_THRU, "NGSIM NB lane_id 2"),
    "S_NB3": ("S", "S_2_3", "S_3_4", ("through",), CODE_THRU, "NGSIM NB lane_id 3"),
    "S_NB4": ("S", "S_3_4", "S_kerb", ("through",),
              CODE_THRU + "; kerb lane, seen in one still (medium confidence)",
              "NGSIM NB lane_id 4"),
    "W_EB_kerb": ("W", "W_kerb", "W_1_2", (), NO_ARROW, None),
    "W_EB_mid": ("W", "W_1_2", "W_2_3", (), NO_ARROW, None),
    "W_EB_inner": ("W", "W_2_3", "W_centre", (), NO_ARROW, None),
    "E_WB_left": ("E", "E_planter", "E_0_1", ("left",),
                  "painted left arrow, low confidence (partly hidden by a static blue object)",
                  None),
    "E_WB_through": ("E", "E_0_1", "E_1_2", ("through",), "painted straight arrow", None),
    "E_WB_right": ("E", "E_1_2", "E_kerb", ("right",), "painted right-turn arrow with 'ONLY'",
                   None),
}  # fmt: skip


@dataclass(frozen=True)
class Measured:
    start: np.ndarray
    end: np.ndarray
    point: np.ndarray  # on the fitted line
    direction: np.ndarray  # unit
    rms_px: float | None
    samples: int
    refined: bool


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample(image: np.ndarray, uv: np.ndarray) -> np.ndarray:
    u = np.ascontiguousarray(uv[:, 0], np.float32)[None]
    v = np.ascontiguousarray(uv[:, 1], np.float32)[None]
    return cv2.remap(image, u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)[0]


def measure(maps: dict[str, np.ndarray], kind: str, seed0, seed1) -> Measured:
    """Refine a seed segment against the image (see the module docstring)."""
    p0, p1 = np.asarray(seed0, float), np.asarray(seed1, float)
    length = float(np.linalg.norm(p1 - p0))
    d = (p1 - p0) / length
    n = np.array([-d[1], d[0]])
    t = np.arange(-SEARCH_PX - 1.0, SEARCH_PX + 1.0 + 1e-9, 0.25)
    image = maps["gray" if kind == "edge" else kind]
    pts = []
    steps = np.arange(0.0, length + 1e-9, STEP_PX)
    for s in steps:
        c = p0 + s * d
        prof = sample(image, c + t[:, None] * n)
        if kind == "edge":
            prof = np.abs(np.gradient(prof, 0.25))
        found = [p for p in profile_peaks(t, prof - np.median(prof), MIN_HEIGHT[kind], 0.75)
                 if abs(p.position) <= SEARCH_PX]  # fmt: skip
        if found:
            pts.append(c + max(found, key=lambda p: p.height).position * n)
    needed = max(6, int(np.ceil(MIN_COVERAGE[kind] * len(steps))))
    pts_a = np.array(pts)
    if len(pts) >= needed:
        for _ in range(2):  # fit, drop points over 1 px from the line, refit
            centre = pts_a.mean(axis=0)
            _, _, vt = np.linalg.svd(pts_a - centre)
            dist = np.abs((pts_a - centre) @ vt[1])
            if (dist <= 1.0).sum() >= 6:
                pts_a = pts_a[dist <= 1.0]
    if len(pts_a) >= needed:
        centre = pts_a.mean(axis=0)
        _, _, vt = np.linalg.svd(pts_a - centre)
        direction = vt[0] if vt[0] @ d > 0 else -vt[0]
        rms = float(np.sqrt(np.mean(((pts_a - centre) @ vt[1]) ** 2)))
        project = [centre + ((q - centre) @ direction) * direction for q in (p0, p1)]
        return Measured(project[0], project[1], centre, direction, rms, len(pts_a), True)
    return Measured(p0, p1, p0, d, None, len(pts_a), False)


def crossing(p1, d1, p2, d2) -> np.ndarray:
    m = np.column_stack([d1, -np.asarray(d2)])
    if abs(np.linalg.det(m)) < 1e-9:
        raise ValueError("parallel lines do not cross")
    t = np.linalg.solve(m, np.asarray(p2) - np.asarray(p1))[0]
    return np.asarray(p1) + t * np.asarray(d1)


def line_error(m: Measured, kind: str) -> float:
    if not m.refined:
        return HAND_ERROR_PX
    error = m.rms_px + 0.5
    return round(max(error, EDGE_ERROR_FLOOR_PX) if kind == "edge" else error, 2)


def line_source(m: Measured) -> str:
    if m.refined:
        return (f"{READING}; refined on the full-video median background: {m.samples} profile "
                f"samples, rms {m.rms_px:.2f} px")  # fmt: skip
    return (f"{READING}; profile refinement agreed on too few samples ({m.samples}), so the "
            "hand-read seed is kept")  # fmt: skip


def build(background: np.ndarray) -> tuple[ImageGeometry, dict[str, Measured]]:
    maps = {
        "white": paint_strength(background, "white"),
        "yellow": paint_strength(background, "yellow"),
        "gray": cv2.cvtColor(background, cv2.COLOR_BGR2GRAY).astype(np.float32),
    }
    measured = {lid: measure(maps, kind, a, b) for lid, (_, _, kind, a, b) in SEEDS.items()}
    lines = []
    for lid, (arm, role, kind, _, _) in SEEDS.items():
        m = measured[lid]
        note = f"; {LIMIT_NOTES[arm]}" if role == "limit_line" else ""
        lines.append(ImageLine(
            line_id=lid, arm=arm, role=role,
            painted=kind != "edge" or role in ("limit_line", "exit_line"),
            segment_px=(tuple(round(float(c), 2) for c in m.start),
                        tuple(round(float(c), 2) for c in m.end)),
            provenance="approximated", error_px=line_error(m, kind),
            source=line_source(m) + note,
        ))  # fmt: skip
    lanes = []
    for lane_id, (arm, a_id, b_id, turns, turns_source, ref) in LANES.items():
        a, b, lim = measured[a_id], measured[b_id], measured[f"{arm}_limit"]
        kinds = (SEEDS[a_id][2], SEEDS[b_id][2], SEEDS[f"{arm}_limit"][2])
        o0, o1 = (np.asarray(p, float) for p in OUTER[arm])
        outer_d = (o1 - o0) / np.linalg.norm(o1 - o0)
        corners = [
            crossing(a.point, a.direction, lim.point, lim.direction),
            crossing(b.point, b.direction, lim.point, lim.direction),
            crossing(b.point, b.direction, o0, outer_d),
            crossing(a.point, a.direction, o0, outer_d),
        ]
        lanes.append(ImageLane(
            lane_id=lane_id, arm=arm, direction="approach",
            polygon_px=tuple(tuple(round(float(c), 2) for c in p) for p in corners),
            allowed_turns=turns, turns_source=turns_source, reference_lane=ref,
            provenance="approximated",
            error_px=max(line_error(m, k) for m, k in zip((a, b, lim), kinds, strict=True)),
            source=f"between lines {a_id} and {b_id}, from {arm}_limit to the drawn extent",
        ))  # fmt: skip
    geometry = ImageGeometry(
        geometry_id="ngsim-lankershim-cam2-image-v2",
        clip_ids=CLIP_IDS,
        source_image_sha256=BACKGROUND_SHA256,
        image_width_px=background.shape[1],
        image_height_px=background.shape[0],
        usable_v_max_px=USABLE_V_MAX,
        lanes=tuple(lanes),
        lines=tuple(lines),
        supersedes="ngsim-lankershim-cam2-provisional-d010/d300 (v1, kept unchanged)",
        notes=(
            "NGSIM intersection 2 (DATA_REGISTER.md §14.3); arms are compass legs: image right N "
            "(~26 deg), left S, top W (~299 deg), bottom E (~119 deg)",
            "pixels only; no ground metres until a calibration is accepted (ADR-017)",
            "exit lanes not drawn; the S leg has no visible exit-side crosswalk line, so no "
            "S exit_line",
            "E_WB_left holds a static blue object for most of the video (bus or painted pad, "
            "not determined)",
        ),
    )  # fmt: skip
    return geometry, measured


def draw(background, geometry: ImageGeometry, v1: dict | None) -> np.ndarray:
    k = 2
    out = cv2.resize(background, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    colours = {"N": (0, 200, 255), "S": (255, 160, 0), "W": (0, 255, 0), "E": (255, 0, 255)}
    if v1 is not None:  # v1 lanes in grey: its synthetic metres back to its pixels
        for lane in v1["lanes"]:
            pts = [(258.0 + x * 4.5, 190.0 - y * 4.5) for x, y in lane["polygon_m"]]
            poly = (np.array(pts) * k).astype(np.int32)
            cv2.polylines(out, [poly], True, (160, 160, 160), 1, cv2.LINE_AA)
    for lane in geometry.lanes:
        poly = (np.array(lane.polygon_px) * k).astype(np.int32)
        cv2.polylines(out, [poly], True, colours[lane.arm], 1, cv2.LINE_AA)
        cx, cy = poly.mean(axis=0).astype(int)
        cv2.putText(out, lane.lane_id, (int(cx) - 18, int(cy) + 4), cv2.FONT_HERSHEY_SIMPLEX,
                    0.3, colours[lane.arm], 1)  # fmt: skip
    for line in geometry.lines:
        if line.role in ("limit_line", "exit_line"):
            a, b = ((np.array(p) * k).astype(int) for p in line.segment_px)
            colour = (0, 0, 255) if line.role == "limit_line" else (255, 255, 255)
            cv2.line(out, (int(a[0]), int(a[1])), (int(b[0]), int(b[1])), colour, 2, cv2.LINE_AA)
    w, h = out.shape[1], out.shape[0]
    cv2.rectangle(out, (0, 0), (w, 24), (0, 0, 0), -1)
    text = "v2 image geometry: approach lanes by arm, limit lines red, exit lines white"
    cv2.putText(out, text + ("; v1 lanes grey" if v1 is not None else ""), (6, 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)  # fmt: skip
    cv2.rectangle(out, (0, h - 24), (w, h), (0, 0, 0), -1)
    credit = "Video: NGSIM Lankershim camera 2, USDOT FHWA, CC BY-SA 4.0. NOT GROUND TRUTH."
    cv2.putText(out, credit, (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
    return out


def main() -> int:
    if OUT_JSON.exists():
        print(f"refusing to overwrite {OUT_JSON}", file=sys.stderr)
        return 2
    if not BACKGROUND.exists() or sha256(BACKGROUND) != BACKGROUND_SHA256:
        print(f"{BACKGROUND} is missing or not the recorded background; run "
              "scripts/analyse_ngsim_cam2.py first", file=sys.stderr)  # fmt: skip
        return 2
    background = cv2.imread(str(BACKGROUND))
    geometry, measured = build(background)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(geometry.model_dump_json(indent=2) + "\n", "utf-8")
    v1 = json.loads(V1_GEOMETRY.read_text("utf-8"))
    figures = {
        "image_geometry_v2.png": draw(background, geometry, None),
        "image_geometry_v1_vs_v2.png": draw(background, geometry, v1),
    }
    for name, image in figures.items():
        target = OUT_DIR / name
        if not target.exists():
            cv2.imwrite(str(target), image)
    for lid, m in measured.items():
        state = f"rms {m.rms_px:.2f} px, {m.samples} samples" if m.refined else "SEED KEPT"
        shift = max(np.linalg.norm(m.start - np.asarray(SEEDS[lid][3])),
                    np.linalg.norm(m.end - np.asarray(SEEDS[lid][4])))  # fmt: skip
        print(f"{lid:10s} {state:28s} max move from seed {shift:4.1f} px")
    counts = {arm: sum(lane.arm == arm for lane in geometry.lanes) for arm in "NSWE"}
    print("approach lanes per arm:", counts, "->", OUT_JSON.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
