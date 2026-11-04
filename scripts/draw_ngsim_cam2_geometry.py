"""Provisional geometry for NGSIM Lankershim camera 2, drawn on frame 0 (source t = 60 s).

    uv run --frozen python scripts/draw_ngsim_cam2_geometry.py

Pixel positions were read from a gridded copy of the frame. Ground metres are SYNTHETIC: image
axes are taken as ground axes (right = +x, labelled "E"; up = +y, labelled "N"; the labels follow
the image, not the compass), origin at the junction centre (258, 190) px, scale 4.5 px/m from a
16.5 px lane assumed 3.66 m (12 ft) wide, view assumed vertical. Lane counts are read from the
image; allowed turns are assumed (arrows unreadable at 640x480). There are no check points:
checking against the same assumption would be circular, so the calibration is reported "not
accepted" until measured ground coordinates exist (DATA_REGISTER.md section 12).

Writes data/geometry/ngsim-lankershim-cam2/{d010,d300}/{calibration,geometry}.json (never
overwrites) and an overlay of the drawing on the frame in data/processed/ (contains pixels).
"""

from __future__ import annotations

from pathlib import Path

import cv2

from cairoflow_contracts.evidence import (
    CalibrationSpec,
    ControlPoint,
    Gate,
    IntersectionGeometry,
    LanePolygon,
)
from cairoflow_perception.calibration import fit_homography, spec_sha256

ROOT = Path(__file__).resolve().parents[1]
FRAME = ROOT / "data/processed/geometry/ngsim-cam2/frame0.png"
CLIPS = {"d010": "4a06259854300b67", "d300": "fd74a18fdb99bc73"}
U0, V0, PX_PER_M = 258.0, 190.0, 4.5
SRC = "frame 0 of the camera 2 08:30 excerpts (source t = 60 s)"
ASSUMED = "metres from an assumed 3.66 m lane width and a vertical view (synthetic)"


def g(u: float, v: float) -> tuple[float, float]:
    return (round((u - U0) / PX_PER_M, 3), round((V0 - v) / PX_PER_M, 3))


def rect(u1: float, v1: float, u2: float, v2: float) -> list[tuple[float, float]]:
    return [g(u1, v1), g(u2, v1), g(u2, v2), g(u1, v2)]


def lane(lane_id, arm, box, turns) -> LanePolygon:
    return LanePolygon(
        lane_id=lane_id,
        arm=arm,
        direction="approach",
        polygon_m=rect(*box),
        allowed_turns=turns,
        provenance="synthetic",
        source=f"{SRC}; lane count observed, turns assumed (arrows unreadable); {ASSUMED}",
    )


def gate(arm, role, p, q) -> Gate:
    return Gate(
        arm=arm,
        role=role,
        segment_m=(g(*p), g(*q)),
        provenance="synthetic",
        source=f"{SRC}; line position read from the image; {ASSUMED}",
    )


LANES = [
    # E approach (right; traffic moves left; kerb at the top)
    lane("E1", "E", (372, 118, 639, 134), ("through", "right")),
    lane("E2", "E", (372, 134, 639, 150), ("through",)),
    lane("E3", "E", (372, 150, 639, 166), ("through",)),
    lane("E4", "E", (372, 166, 639, 182), ("through",)),
    lane("E5", "E", (372, 182, 639, 198), ("left", "through")),
    # W approach (left; traffic moves right; kerb at the bottom)
    lane("W1", "W", (0, 247, 147, 262), ("through", "right")),
    lane("W2", "W", (0, 232, 147, 247), ("through",)),
    lane("W3", "W", (0, 216, 147, 232), ("through",)),
    lane("W4", "W", (0, 200, 147, 216), ("left", "through")),
    # N approach (top; traffic moves down; kerb on the left)
    lane("N1", "N", (205, 0, 224, 100), ("through", "right")),
    lane("N2", "N", (224, 0, 243, 100), ("through",)),
    lane("N3", "N", (243, 0, 262, 100), ("left", "through")),
    # S approach (bottom; traffic moves up; kerb on the right)
    lane("S1", "S", (308, 290, 330, 410), ("through", "right")),
    lane("S2", "S", (287, 290, 308, 410), ("through",)),
    lane("S3", "S", (265, 290, 287, 410), ("left", "through")),
]
GATES = [
    gate("E", "stop_line", (372, 118), (372, 198)),
    gate("E", "exit_line", (380, 200), (380, 262)),
    gate("W", "stop_line", (147, 200), (147, 262)),
    gate("W", "exit_line", (140, 118), (140, 198)),
    gate("N", "stop_line", (205, 100), (262, 100)),
    gate("N", "exit_line", (265, 95), (330, 95)),
    gate("S", "stop_line", (265, 290), (330, 290)),
    gate("S", "exit_line", (205, 290), (262, 290)),
]
POINTS = {  # id: (pixel, feature)
    "E_stop_top": ((372, 118), "upper end of the right-approach stop line"),
    "E_stop_bottom": ((372, 198), "lower end of the right-approach stop line"),
    "W_stop_top": ((147, 200), "upper end of the left-approach stop line"),
    "W_stop_bottom": ((147, 262), "lower end of the left-approach stop line"),
    "N_stop_left": ((205, 100), "left end of the top-approach stop line"),
    "N_exit_right": ((330, 95), "right end of the top-arm stop/crosswalk line"),
    "S_stop_left": ((265, 290), "left end of the bottom-approach stop line"),
    "S_stop_right": ((330, 290), "right end of the bottom-approach stop line"),
}
COLOURS = {"E": (0, 200, 255), "W": (255, 160, 0), "N": (0, 255, 0), "S": (255, 0, 255)}


def main() -> None:
    homography = None
    for tag, clip_id in CLIPS.items():
        points = [
            ControlPoint(
                point_id=pid,
                image_px=px,
                ground_m=g(*px),
                role="fit",
                ground_provenance="synthetic",
                source=f"{SRC}: {feature}; {ASSUMED}",
            )
            for pid, (px, feature) in POINTS.items()
        ]
        spec = CalibrationSpec(
            clip_id=clip_id, image_width_px=640, image_height_px=480, points=points
        )
        homography, report = fit_homography(spec)
        geometry = IntersectionGeometry(
            geometry_id=f"ngsim-lankershim-cam2-provisional-{tag}",
            clip_id=clip_id,
            calibration_spec_sha256=spec_sha256(spec),
            lanes=LANES,
            gates=GATES,
        )
        out = ROOT / "data/geometry/ngsim-lankershim-cam2" / tag
        out.mkdir(parents=True, exist_ok=False)
        for name, model in (("calibration.json", spec), ("geometry.json", geometry)):
            (out / name).write_text(model.model_dump_json(indent=2) + "\n", "utf-8")
        print(f"{tag} {clip_id}: accepted {report.accepted}; {report.reasons}")

    draw = cv2.imread(str(FRAME))
    for ln in LANES:
        pts = [tuple(int(round(c)) for c in homography.to_image(p)) for p in ln.polygon_m]
        for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
            cv2.line(draw, a, b, COLOURS[ln.arm], 1)
        cx, cy = sum(p[0] for p in pts) // 4, sum(p[1] for p in pts) // 4
        cv2.putText(draw, ln.lane_id, (cx - 8, cy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.3,
                    COLOURS[ln.arm], 1)  # fmt: skip
    for gt in GATES:
        a, b = (tuple(int(round(c)) for c in homography.to_image(p)) for p in gt.segment_m)
        cv2.line(draw, a, b, (0, 0, 255) if gt.role == "stop_line" else (255, 255, 255), 2)
    for px, _ in POINTS.values():
        cv2.circle(draw, px, 4, (0, 255, 255), 1)
    big = cv2.resize(draw, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(FRAME.with_name("frame0_drawing.png")), big)


if __name__ == "__main__":
    main()
