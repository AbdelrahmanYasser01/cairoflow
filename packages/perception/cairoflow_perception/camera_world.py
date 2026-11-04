"""Camera tracks in the world model's frame and lanes, checked against NGSIM (P1-1).

Pixels reach the intersection 2 junction frame in two steps that share one State Plane origin:
the camera 2 homography gives ADR-017 grid metres, and the world model's own rotation
(ngsim_junction.JunctionFrame, the bearing in scenario.json) turns those into its frame. Each SAM
box contributes its centre, since camera 2 looks almost straight down. Lanes are the world
model's lane polygons; a point in none or in several keeps an explicit status rather than a
guessed lane.

`compare` and `apply_gate` implement the pre-registered gate
(data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate.json): placed points are paired
with the published trajectories at the synced instants, and four measures decide go or no-go.
The thresholds are copied from that file; nothing here was tuned on a result.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from cairoflow_contracts.evidence import (
    CameraGate,
    CameraTrack,
    CameraTrackPoint,
    LanePolygon,
    Track,
)
from cairoflow_perception.calibration import CalibrationError, Homography
from cairoflow_perception.ngsim import US_SURVEY_FOOT_M, LocalFrame, Trajectories
from cairoflow_perception.ngsim_junction import JunctionFrame
from cairoflow_perception.world_model import _Ground, _inside

Point = tuple[float, float]
SIGMA_PX = 2.0  # assumed image error of a box centre, as WorldModelPolicy.contact_sigma_px
SPEED_HALF_WINDOW_S = 0.5  # as WorldModelPolicy.speed_half_window_s
# The pre-registered gate (camera_world_model_gate.json), verbatim.
MATCH_RADIUS_M = 3.0
MIN_MATCHED_POINTS = 5
MIN_MATCH_SHARE = 0.70
MIN_LANE_POINTS = 3
THRESHOLDS = {
    "A_track_match_share": 0.60,
    "B_point_lane_agreement": 0.85,
    "B_min_points": 100,
    "C_track_lane_agreement": 0.80,
    "C_min_tracks": 8,
    "D_lateral_p90_m": 1.5,
}


@dataclass(frozen=True)
class Placement:
    """Image pixels to the junction frame: the camera homography, then the world model's
    rotation. `check_median_m` is the calibration's median held-out error, added to each point's
    pixel-error term in quadrature."""

    homography: Homography
    frame: JunctionFrame
    check_median_m: float

    def to_junction(self, image_px: Sequence[float]) -> Point:
        gx, gy = self.homography.to_ground(image_px)
        local = LocalFrame(self.frame.origin_x_ft, self.frame.origin_y_ft)
        x_ft, y_ft = local.to_state_plane(gx, gy)
        x, y = self.frame.to_junction(x_ft, y_ft)
        return float(x), float(y)

    def error_m(self, image_px: Sequence[float]) -> float:
        return math.hypot(self.homography.ground_error_at(image_px, SIGMA_PX), self.check_median_m)


def lane_hits(lanes: Sequence[LanePolygon], p: Point) -> list[LanePolygon]:
    return [lane for lane in lanes if _inside(lane.polygon_m, p)]


def edge_distance(polygon: Sequence[Point], p: Point) -> float:
    """Distance from `p` to the nearest edge of `polygon`."""
    best = math.inf
    for (ax, ay), (bx, by) in zip(polygon, (*polygon[1:], polygon[0]), strict=True):
        length = (bx - ax) ** 2 + (by - ay) ** 2
        t = ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / length if length else 0.0
        t = max(0.0, min(1.0, t))
        best = min(best, math.dist(p, (ax + t * (bx - ax), ay + t * (by - ay))))
    return best


def place_track(
    track: Track,
    placement: Placement,
    lanes: Sequence[LanePolygon],
    *,
    camera: str,
    window_offset_s: float,
    confidence: Mapping[str, float | None],
) -> tuple[CameraTrack | None, list[str]]:
    """One SAM track on the ground with its lanes, and the observations that could not be
    projected (at or beyond the horizon). `window_offset_s` is the world-model second of the
    clip's first frame."""
    ground = _Ground(track.track_id, track.class_label)
    placed: list[tuple] = []
    unprojected: list[str] = []
    for point in track.points:
        x1, y1, x2, y2 = point.bbox_xyxy_px
        centre = ((x1 + x2) / 2, (y1 + y2) / 2)
        try:
            position, error = placement.to_junction(centre), placement.error_m(centre)
        except CalibrationError:
            unprojected.append(point.observation_id)
            continue
        ground.frames.append(point.frame)
        ground.points.append(position)
        ground.errors.append(error)
        placed.append((point, centre, position, error))
    ground.times = [frame.timestamp_s for frame in ground.frames]
    if not placed:
        return None, unprojected
    points: list[CameraTrackPoint] = []
    for point, centre, position, error in placed:
        hits = lane_hits(lanes, position)
        lane = hits[0] if len(hits) == 1 else None
        speed = ground.speed_at(point.frame.timestamp_s, SPEED_HALF_WINDOW_S)
        points.append(CameraTrackPoint(
            frame=point.frame,
            window_time_s=round(window_offset_s + point.frame.timestamp_s, 6),
            observation_id=point.observation_id,
            image_point_px=(round(centre[0], 3), round(centre[1], 3)),
            position_m=(round(position[0], 4), round(position[1], 4)),
            position_error_m=round(error, 4),
            speed_m_s=None if speed is None else round(speed, 4),
            lane_id=lane.lane_id if lane else None,
            lane_status="in_lane" if lane else ("ambiguous" if hits else "outside_lanes"),
            near_lane_boundary=bool(lane) and edge_distance(lane.polygon_m, position) < error,
            confidence=confidence.get(point.observation_id),
        ))  # fmt: skip
    in_lane = Counter(p.lane_id for p in points if p.lane_id)
    lane_id, count = in_lane.most_common(1)[0] if in_lane else (None, 0)
    return CameraTrack(
        track_id=f"{camera}:{track.track_id}",
        sam_track_id=track.track_id,
        sam_object_ids=track.sam_object_ids,
        points=tuple(points),
        lane_id=lane_id,
        lane_share=round(count / sum(in_lane.values()), 4) if in_lane else None,
    ), unprojected


@dataclass(frozen=True)
class Reference:
    """One NGSIM vehicle at one tick: its centre in the junction frame and its lane there."""

    vehicle_id: int
    centre: Point
    lane: LanePolygon | None


def reference_at(
    traj: Trajectories,
    heading_deg: np.ndarray,
    frame: JunctionFrame,
    lanes: Sequence[LanePolygon],
    tick_ms: int,
) -> tuple[list[Reference], int]:
    """Every vehicle at `tick_ms` with its centre (front centre moved back half its length along
    its heading), and how many had no heading (never moved: no centre can be placed)."""
    rows = np.flatnonzero(traj.global_time_ms == tick_ms)
    local = LocalFrame(frame.origin_x_ft, frame.origin_y_ft)
    found: list[Reference] = []
    no_heading = 0
    for row in rows:
        heading = float(heading_deg[row])
        if not math.isfinite(heading):
            no_heading += 1
            continue
        fx, fy = local.to_local(traj.x_ft[row], traj.y_ft[row])
        half = float(traj.length_ft[row]) * US_SURVEY_FOOT_M / 2
        b = math.radians(heading)
        x_ft, y_ft = local.to_state_plane(fx - half * math.sin(b), fy - half * math.cos(b))
        x, y = frame.to_junction(x_ft, y_ft)
        centre = (float(x), float(y))
        hits = lane_hits(lanes, centre)
        lane = hits[0] if len(hits) == 1 else None
        found.append(Reference(int(traj.vehicle_id[row]), centre, lane))
    return found, no_heading


def _axis_is_y(reference: Reference, camera_lane: LanePolygon | None) -> bool:
    """Whether lateral is across x (a north or south arm): the reference's lane arm, else the
    camera's, else the arm whose axis the reference centre is nearest."""
    lane = reference.lane or camera_lane
    if lane is not None:
        return lane.arm in ("N", "S")
    return abs(reference.centre[0]) < abs(reference.centre[1])


def _pairs(tracks: Sequence[CameraTrack], references: Mapping[int, list[Reference]]) -> dict:
    """(track id, frame index) -> the reference vehicle it is paired with: per frame, greedily
    by distance, one to one, within MATCH_RADIUS_M."""
    per_frame: dict[int, list[tuple[str, CameraTrackPoint]]] = defaultdict(list)
    for track in tracks:
        for point in track.points:
            per_frame[point.frame.frame_index].append((track.track_id, point))
    paired: dict[tuple[str, int], Reference] = {}
    for index, members in per_frame.items():
        candidates = sorted(
            ((math.dist(point.position_m, ref.centre), track_id, ref.vehicle_id, ref)
             for track_id, point in members for ref in references.get(index, [])),
            key=lambda c: (c[0], c[1], c[2]),
        )  # fmt: skip
        used_tracks: set[str] = set()
        used_refs: set[int] = set()
        for distance, track_id, vehicle, ref in candidates:
            if distance > MATCH_RADIUS_M:
                break
            if track_id in used_tracks or vehicle in used_refs:
                continue
            used_tracks.add(track_id)
            used_refs.add(vehicle)
            paired[(track_id, index)] = ref
    return paired


def compare(
    tracks: Sequence[CameraTrack],
    references: Mapping[int, list[Reference]],
    lanes: Sequence[LanePolygon],
) -> dict:
    """The gate's measures. `references` maps a camera frame index to the NGSIM vehicles at its
    synced tick."""
    by_lane = {lane.lane_id: lane for lane in lanes}
    paired = _pairs(tracks, references)
    matched_tracks: dict[str, int] = {}
    rows: list[dict] = []
    for track in tracks:
        pairs = [(p, paired[(track.track_id, p.frame.frame_index)]) for p in track.points
                 if (track.track_id, p.frame.frame_index) in paired]  # fmt: skip
        votes = Counter(ref.vehicle_id for _, ref in pairs)
        vehicle, count = votes.most_common(1)[0] if votes else (None, 0)
        if len(pairs) < MIN_MATCHED_POINTS or count / len(pairs) < MIN_MATCH_SHARE:
            continue
        matched_tracks[track.track_id] = vehicle
        for point, ref in pairs:
            if ref.vehicle_id != vehicle:
                continue
            camera_lane = by_lane.get(point.lane_id) if point.lane_id else None
            dx = point.position_m[0] - ref.centre[0]
            dy = point.position_m[1] - ref.centre[1]
            lateral, along = (dx, dy) if _axis_is_y(ref, camera_lane) else (dy, dx)
            rows.append({"track": track.track_id, "camera_lane": point.lane_id,
                         "reference_lane": ref.lane.lane_id if ref.lane else None,
                         "lateral_m": lateral, "along_m": along,
                         "near_boundary": point.near_lane_boundary})  # fmt: skip

    both = [r for r in rows if r["camera_lane"] and r["reference_lane"]]
    votes_by_track: dict[str, tuple[Counter, Counter]] = defaultdict(lambda: (Counter(), Counter()))
    for r in both:
        votes_by_track[r["track"]][0][r["camera_lane"]] += 1
        votes_by_track[r["track"]][1][r["reference_lane"]] += 1
    judged = {t: v for t, v in votes_by_track.items() if sum(v[0].values()) >= MIN_LANE_POINTS}
    agreeing = sum(v[0].most_common(1)[0][0] == v[1].most_common(1)[0][0]
                   for v in judged.values())  # fmt: skip
    lateral = [abs(r["lateral_m"]) for r in rows]
    along = [abs(r["along_m"]) for r in rows]
    in_lane_refs = {ref.vehicle_id for refs in references.values() for ref in refs if ref.lane}

    def p90(values: list[float]) -> float | None:
        return round(float(np.percentile(values, 90)), 4) if values else None

    def median(values: list[float]) -> float | None:
        return round(statistics.median(values), 4) if values else None

    agreement = sum(r["camera_lane"] == r["reference_lane"] for r in both)
    return {
        "camera_tracks": len(tracks),
        "matched_tracks": len(matched_tracks),
        "A_track_match_share": round(len(matched_tracks) / len(tracks), 4) if tracks else None,
        "matched_points": len(rows),
        "B_points": len(both),
        "B_point_lane_agreement": round(agreement / len(both), 4) if both else None,
        "C_tracks": len(judged),
        "C_track_lane_agreement": round(agreeing / len(judged), 4) if judged else None,
        "D_lateral_p90_m": p90(lateral),
        "lateral_median_m": median(lateral),
        "along_median_m": median(along),
        "along_p90_m": p90(along),
        "matched_points_near_lane_boundary": sum(r["near_boundary"] for r in rows),
        "reference_vehicles_in_a_lane": len(in_lane_refs),
        "reference_vehicles_matched": len(set(matched_tracks.values())),
        "matched_vehicle_of_track": dict(sorted(matched_tracks.items())),
    }


def apply_gate(metrics: Mapping, gate_id: str, gate_sha256: str) -> CameraGate:
    """The pre-registered go rule, applied as written."""
    t = THRESHOLDS
    checks = {
        "A_track_match_share": (metrics["A_track_match_share"] or 0) >= t["A_track_match_share"],
        "B_point_lane_agreement": metrics["B_points"] >= t["B_min_points"]
        and (metrics["B_point_lane_agreement"] or 0) >= t["B_point_lane_agreement"],
        "C_track_lane_agreement": metrics["C_tracks"] >= t["C_min_tracks"]
        and (metrics["C_track_lane_agreement"] or 0) >= t["C_track_lane_agreement"],
        "D_lateral_p90_m": metrics["D_lateral_p90_m"] is not None
        and metrics["D_lateral_p90_m"] <= t["D_lateral_p90_m"],
    }
    rule = {
        "A_track_match_share": f">= {t['A_track_match_share']}",
        "B_point_lane_agreement": f">= {t['B_point_lane_agreement']} over >= "
        f"{t['B_min_points']} points (got {metrics['B_points']})",
        "C_track_lane_agreement": f">= {t['C_track_lane_agreement']} over >= "
        f"{t['C_min_tracks']} tracks (got {metrics['C_tracks']})",
        "D_lateral_p90_m": f"<= {t['D_lateral_p90_m']} m",
    }
    reasons = tuple(
        f"{name} is {metrics[name]}, the rule needs {rule[name]}"
        for name, ok in checks.items()
        if not ok
    )
    gated = {k: v for k, v in metrics.items() if v is None or isinstance(v, int | float)}
    return CameraGate(gate_id=gate_id, gate_sha256=gate_sha256,
                      outcome="no_go" if reasons else "go", metrics=gated, checks=checks,
                      reasons=reasons)  # fmt: skip
