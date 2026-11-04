"""Camera gate v3's measurement (ADR-031), beside, not instead of, gates v1 and v2 (P1-1).

Gates v1 and v2 (camera_world.compare and apply_gate) stay exactly as they ran. The gate v2
diagnosis (experiments/diagnostics/camera-gate-v2-20261030T223228Z/report.md) found that their
lateral measure picks up along-road error in two ways. v3 changes those two steps and nothing
else:

- Lateral error is the camera point's offset across the reference vehicle's own heading, for
  every point: no arm axis and no |x| < |y| fallback inside the junction.
- A camera track is paired with an NGSIM vehicle as a whole track, by a cost that weighs across
  distance more than along distance, so an along-road offset cannot hand it to the car in the
  next lane. A vehicle may take several tracks whose frames do not overlap (a track SAM split),
  never two in the same frame.

Placement (camera_world.place_track), reference centres (camera_world.reference_at), lanes and
the four thresholds are v1/v2's. The time sync is the caller's: the gate file fixes it. Every
constant below is copied into the gate file, and the runner refuses a gate file that differs.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from cairoflow_contracts.evidence import CameraGate, CameraTrack, IntersectionGeometry, LanePolygon
from cairoflow_perception.camera_world import (
    MIN_LANE_POINTS,
    MIN_MATCH_SHARE,
    MIN_MATCHED_POINTS,
    Reference,
    _axis_is_y,
    lane_hits,
    reference_at,
)
from cairoflow_perception.ngsim import DIRECTIONS, MOVEMENTS, Trajectories
from cairoflow_perception.ngsim_junction import JunctionFrame

Point = tuple[float, float]
ACROSS_WINDOW_M = 3.0  # a frame pairs only within this across the heading (v1/v2's radius)
ALONG_WINDOW_M = 8.0  # and within this along it (checkpoints_ngsim_cam2.MATCH_ALONG_M)
ACROSS_SCALE_M = 1.6  # pairing cost scales (checkpoints_ngsim_cam2.MATCH_ACROSS_M, _ALONG_M)
ALONG_SCALE_M = 8.0
MOVING_M_S, STOPPED_M_S = 2.0, 0.5
JUNCTION_MIN_POINTS, JUNCTION_MIN_TRACKS = 50, 3
PREDICTION_MIN_POINTS = 30
PREDICTION_REGISTERED_M = 0.75  # |median along| of moving vehicles at the registered sync
PREDICTION_OTHER_M = 1.0  # |median along| of moving vehicles at the other candidate sync
PARAMETERS = {
    "across_window_m": ACROSS_WINDOW_M, "along_window_m": ALONG_WINDOW_M,
    "across_scale_m": ACROSS_SCALE_M, "along_scale_m": ALONG_SCALE_M,
    "min_matched_points": MIN_MATCHED_POINTS, "min_match_share": MIN_MATCH_SHARE,
    "min_lane_points": MIN_LANE_POINTS, "moving_m_s": MOVING_M_S, "stopped_m_s": STOPPED_M_S,
    "junction_min_points": JUNCTION_MIN_POINTS, "junction_min_tracks": JUNCTION_MIN_TRACKS,
    "prediction_min_points": PREDICTION_MIN_POINTS,
    "prediction_registered_m": PREDICTION_REGISTERED_M, "prediction_other_m": PREDICTION_OTHER_M,
}  # fmt: skip
THRESHOLDS = {
    "A_track_match_share": 0.60,
    "B_point_lane_agreement": 0.85,
    "B_min_points": 100,
    "C_track_lane_agreement": 0.80,
    "C_min_tracks": 8,
    "D_across_heading_p90_m": 1.5,
    "J_across_heading_p90_m": 1.5,
}
REGIONS = ("approach", "departure", "junction_interior", "unknown")


@dataclass(frozen=True)
class HeadedReference:
    """One NGSIM vehicle at one tick, as camera_world.Reference, with its heading (junction-frame
    bearing, clockwise from +y), speed and published direction and movement."""

    vehicle_id: int
    centre: Point
    lane: LanePolygon | None
    heading_deg: float
    speed_m_s: float | None
    direction: str
    movement: str


@dataclass(frozen=True)
class Pairing:
    """A camera track and the NGSIM vehicle it is paired with: the frames that count (both
    present, within both windows) and how many of the track's frames had any vehicle there."""

    track_id: str
    vehicle_id: int
    cost: float
    frames: tuple[int, ...]
    candidate_frames: int


def headed_references(
    traj: Trajectories,
    speed_m_s: np.ndarray,
    heading_deg: np.ndarray,
    frame: JunctionFrame,
    lanes: Sequence[LanePolygon],
    tick_ms: int,
) -> list[HeadedReference]:
    """Every vehicle at `tick_ms` that has a heading, with its centre exactly as
    camera_world.reference_at places it. `heading_deg` and `speed_m_s` are ngsim.kinematics'."""
    found, _ = reference_at(traj, heading_deg, frame, lanes, tick_ms)
    rows = {int(traj.vehicle_id[r]): int(r) for r in np.flatnonzero(traj.global_time_ms == tick_ms)}
    out = []
    for ref in found:
        row = rows[ref.vehicle_id]
        speed = float(speed_m_s[row])
        out.append(HeadedReference(
            vehicle_id=ref.vehicle_id, centre=ref.centre, lane=ref.lane,
            heading_deg=(float(heading_deg[row]) - frame.bearing_deg) % 360,
            speed_m_s=None if math.isnan(speed) else speed,
            direction=DIRECTIONS.get(int(traj.direction[row]), str(traj.direction[row])),
            movement=MOVEMENTS.get(int(traj.movement[row]), str(traj.movement[row])),
        ))  # fmt: skip
    return out


def heading_components(dx: float, dy: float, heading_deg: float) -> tuple[float, float]:
    """An offset along and across a heading (bearing from +y, clockwise): along > 0 is ahead,
    across > 0 to the right."""
    h = math.radians(heading_deg)
    return dx * math.sin(h) + dy * math.cos(h), dx * math.cos(h) - dy * math.sin(h)


def residual(position: Sequence[float], ref: HeadedReference) -> tuple[float, float]:
    """Camera point minus reference centre, (along, across) the reference's heading."""
    return heading_components(position[0] - ref.centre[0], position[1] - ref.centre[1],
                              ref.heading_deg)  # fmt: skip


def stop_box(geometry: IntersectionGeometry) -> dict[str, float]:
    """Each arm's stop line as one coordinate: y for N and S, x for E and W."""
    box = {}
    for gate in geometry.gates:
        if gate.role == "stop_line":
            (ax, ay), (bx, by) = gate.segment_m
            box[gate.arm] = (ay + by) / 2 if gate.arm in ("N", "S") else (ax + bx) / 2
    return box


def region_of(p: Point, lanes: Sequence[LanePolygon], box: Mapping[str, float]) -> str:
    """approach / departure (inside exactly one approach or exit lane), junction_interior (in no
    lane, inside the box the four stop lines bound) or unknown (anything else)."""
    hits = lane_hits(lanes, p)
    if len(hits) == 1:
        return "approach" if hits[0].direction == "approach" else "departure"
    if not hits and box["W"] < p[0] < box["E"] and box["S"] < p[1] < box["N"]:
        return "junction_interior"
    return "unknown"


def pair_tracks(
    tracks: Sequence[CameraTrack], references: Mapping[int, Sequence[HeadedReference]]
) -> dict[str, Pairing]:
    """Each camera track's NGSIM vehicle, or none.

    A track-vehicle option needs at least MIN_MATCHED_POINTS frames where the vehicle lies within
    both windows of the track's point, and those frames must be at least MIN_MATCH_SHARE of the
    track's frames with any vehicle there. Its cost is the median over those frames of
    (across / ACROSS_SCALE_M)^2 + (along / ALONG_SCALE_M)^2. Options are taken cheapest first; a
    track takes one vehicle, and a vehicle takes another track only on frames it has not taken.
    """
    options = []
    for track in tracks:
        per_vehicle: dict[int, list[tuple[int, float]]] = defaultdict(list)
        candidate = 0
        for point in track.points:
            seen = False
            for ref in references.get(point.frame.frame_index, ()):
                along, across = residual(point.position_m, ref)
                if abs(along) <= ALONG_WINDOW_M and abs(across) <= ACROSS_WINDOW_M:
                    seen = True
                    cost = (across / ACROSS_SCALE_M) ** 2 + (along / ALONG_SCALE_M) ** 2
                    per_vehicle[ref.vehicle_id].append((point.frame.frame_index, cost))
            candidate += seen
        for vehicle, found in per_vehicle.items():
            if len(found) < MIN_MATCHED_POINTS or len(found) / candidate < MIN_MATCH_SHARE:
                continue
            cost = statistics.median(c for _, c in found)
            options.append((cost, track.track_id, vehicle, tuple(f for f, _ in found), candidate))
    options.sort(key=lambda o: (o[0], o[1], o[2]))
    taken: dict[int, set[int]] = defaultdict(set)
    paired: dict[str, Pairing] = {}
    for cost, track_id, vehicle, frames, candidate in options:
        if track_id in paired or taken[vehicle].intersection(frames):
            continue
        paired[track_id] = Pairing(track_id, vehicle, round(cost, 6), frames, candidate)
        taken[vehicle].update(frames)
    return paired


def measure(
    tracks: Sequence[CameraTrack],
    references: Mapping[int, Sequence[HeadedReference]],
    pairing: Mapping[str, Pairing],
    lanes: Sequence[LanePolygon],
    box: Mapping[str, float],
) -> list[dict]:
    """One row per counted point: the pairing's frames, read against `references` (which may be
    another sync's, for the sensitivity and prediction 2; a frame whose vehicle is absent there
    is skipped)."""
    by_lane = {lane.lane_id: lane for lane in lanes}
    at = {(i, r.vehicle_id): r for i, refs in references.items() for r in refs}
    rows = []
    for track in tracks:
        pair = pairing.get(track.track_id)
        if pair is None:
            continue
        frames = set(pair.frames)
        for point in track.points:
            ref = at.get((point.frame.frame_index, pair.vehicle_id))
            if point.frame.frame_index not in frames or ref is None:
                continue
            along, across = residual(point.position_m, ref)
            dx = point.position_m[0] - ref.centre[0]
            dy = point.position_m[1] - ref.centre[1]
            camera_lane = by_lane.get(point.lane_id) if point.lane_id else None
            axis_y = _axis_is_y(Reference(ref.vehicle_id, ref.centre, ref.lane), camera_lane)
            rows.append({
                "track": track.track_id, "vehicle": pair.vehicle_id,
                "frame": point.frame.frame_index,
                "camera_m": list(point.position_m), "reference_m": list(ref.centre),
                "camera_lane": point.lane_id,
                "reference_lane": ref.lane.lane_id if ref.lane else None,
                "region": region_of(ref.centre, lanes, box),
                "direction": ref.direction, "movement": ref.movement, "speed_m_s": ref.speed_m_s,
                "along_m": along, "across_m": across, "v2_axis_lateral_m": dx if axis_y else dy,
                "error_2d_m": math.hypot(dx, dy),
            })  # fmt: skip
    return rows


def excluded_by_window(
    tracks: Sequence[CameraTrack],
    references: Mapping[int, Sequence[HeadedReference]],
    pairing: Mapping[str, Pairing],
) -> int:
    """Frames where a paired track and its vehicle are both present but outside the windows: not
    counted in D (as v1/v2's 3 m radius did not count them), reported."""
    present = {(i, r.vehicle_id) for i, refs in references.items() for r in refs}
    count = 0
    for track in tracks:
        pair = pairing.get(track.track_id)
        if pair is None:
            continue
        frames = set(pair.frames)
        count += sum((p.frame.frame_index, pair.vehicle_id) in present
                     and p.frame.frame_index not in frames for p in track.points)  # fmt: skip
    return count


def _p90(values: list[float]) -> float | None:
    return round(float(np.percentile(values, 90)), 4) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def along_medians(rows: Sequence[dict], *, moving: bool) -> dict[str, dict]:
    """Median signed along error by published direction, for moving (>= MOVING_M_S) or stopped
    (< STOPPED_M_S) reference vehicles."""
    found: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        s = r["speed_m_s"]
        if s is not None and (s >= MOVING_M_S if moving else s < STOPPED_M_S):
            found[r["direction"]].append(r["along_m"])
    return {d: {"points": len(v), "median_m": _median(v)} for d, v in sorted(found.items())}


def summarise(
    rows: Sequence[dict], tracks: Sequence[CameraTrack], pairing: Mapping[str, Pairing]
) -> dict:
    """The gate's measures (A, B, C, D, J) and what it reports without gating."""
    both = [r for r in rows if r["camera_lane"] and r["reference_lane"]]
    votes: dict[str, tuple[Counter, Counter]] = defaultdict(lambda: (Counter(), Counter()))
    for r in both:
        votes[r["track"]][0][r["camera_lane"]] += 1
        votes[r["track"]][1][r["reference_lane"]] += 1
    judged = {t: v for t, v in votes.items() if sum(v[0].values()) >= MIN_LANE_POINTS}
    agreeing = sum(v[0].most_common(1)[0][0] == v[1].most_common(1)[0][0]
                   for v in judged.values())  # fmt: skip
    junction = [r for r in rows if r["region"] == "junction_interior"]
    matched = {r["track"] for r in rows}
    regions = {}
    for name in REGIONS:
        group = [r for r in rows if r["region"] == name]
        regions[name] = {"points": len(group), "tracks": len({r["track"] for r in group}),
                         "across_median_m": _median([abs(r["across_m"]) for r in group]),
                         "across_p90_m": _p90([abs(r["across_m"]) for r in group])}  # fmt: skip
    agreement = sum(r["camera_lane"] == r["reference_lane"] for r in both)
    return {
        "camera_tracks": len(tracks),
        "matched_tracks": len(matched),
        "A_track_match_share": round(len(matched) / len(tracks), 4) if tracks else None,
        "matched_points": len(rows),
        "B_points": len(both),
        "B_point_lane_agreement": round(agreement / len(both), 4) if both else None,
        "C_tracks": len(judged),
        "C_track_lane_agreement": round(agreeing / len(judged), 4) if judged else None,
        "D_across_heading_p90_m": _p90([abs(r["across_m"]) for r in rows]),
        "J_points": len(junction),
        "J_tracks": len({r["track"] for r in junction}),
        "J_across_heading_p90_m": _p90([abs(r["across_m"]) for r in junction]),
        "across_median_m": _median([abs(r["across_m"]) for r in rows]),
        "along_median_m": _median([abs(r["along_m"]) for r in rows]),
        "error_2d_p90_m": _p90([r["error_2d_m"] for r in rows]),
        "v2_axis_lateral_p90_m": _p90([abs(r["v2_axis_lateral_m"]) for r in rows]),
        "by_region": regions,
        "moving_along_by_direction": along_medians(rows, moving=True),
        "stopped_along_by_direction": along_medians(rows, moving=False),
        "matched_vehicle_of_track": {t: pairing[t].vehicle_id for t in sorted(matched)},
    }


def predictions(registered: Sequence[dict], other: Sequence[dict]) -> dict:
    """The two pre-registered predictions of the timing explanation (reported, not gated).

    1. At the registered sync, every direction with at least PREDICTION_MIN_POINTS moving points
       has |median along| <= PREDICTION_REGISTERED_M.
    2. With the same pairs read at the other candidate sync, the moving points' pooled
       |median along| > PREDICTION_OTHER_M.
    Each is True, False, or None when too few moving points exist to test it.
    """
    tested = {d: m for d, m in along_medians(registered, moving=True).items()
              if m["points"] >= PREDICTION_MIN_POINTS}  # fmt: skip
    first = (all(abs(m["median_m"]) <= PREDICTION_REGISTERED_M for m in tested.values())
             if tested else None)  # fmt: skip
    pooled = [r["along_m"] for r in other
              if r["speed_m_s"] is not None and r["speed_m_s"] >= MOVING_M_S]  # fmt: skip
    second = (abs(statistics.median(pooled)) > PREDICTION_OTHER_M
              if len(pooled) >= PREDICTION_MIN_POINTS else None)  # fmt: skip
    return {
        "1_registered_sync_moving_along_within_m": PREDICTION_REGISTERED_M,
        "1_tested_directions": tested,
        "1_holds": first,
        "2_other_sync_moving_along_beyond_m": PREDICTION_OTHER_M,
        "2_points": len(pooled),
        "2_median_m": _median(pooled),
        "2_holds": second,
    }


def apply_gate_v3(metrics: Mapping, gate_id: str, gate_sha256: str) -> CameraGate:
    """The pre-registered go rule, applied as written: A, B, C and D as v1/v2 (D across the
    heading); J only when the junction box has enough points and tracks to judge."""
    t = THRESHOLDS
    judge_junction = (metrics["J_points"] >= JUNCTION_MIN_POINTS
                      and metrics["J_tracks"] >= JUNCTION_MIN_TRACKS)  # fmt: skip
    checks = {
        "A_track_match_share": (metrics["A_track_match_share"] or 0) >= t["A_track_match_share"],
        "B_point_lane_agreement": metrics["B_points"] >= t["B_min_points"]
        and (metrics["B_point_lane_agreement"] or 0) >= t["B_point_lane_agreement"],
        "C_track_lane_agreement": metrics["C_tracks"] >= t["C_min_tracks"]
        and (metrics["C_track_lane_agreement"] or 0) >= t["C_track_lane_agreement"],
        "D_across_heading_p90_m": metrics["D_across_heading_p90_m"] is not None
        and metrics["D_across_heading_p90_m"] <= t["D_across_heading_p90_m"],
        "J_across_heading_p90_m": not judge_junction
        or metrics["J_across_heading_p90_m"] <= t["J_across_heading_p90_m"],
    }
    rule = {
        "A_track_match_share": f">= {t['A_track_match_share']}",
        "B_point_lane_agreement": f">= {t['B_point_lane_agreement']} over >= "
        f"{t['B_min_points']} points (got {metrics['B_points']})",
        "C_track_lane_agreement": f">= {t['C_track_lane_agreement']} over >= "
        f"{t['C_min_tracks']} tracks (got {metrics['C_tracks']})",
        "D_across_heading_p90_m": f"<= {t['D_across_heading_p90_m']} m",
        "J_across_heading_p90_m": f"<= {t['J_across_heading_p90_m']} m when the junction box "
        f"has >= {JUNCTION_MIN_POINTS} points from >= {JUNCTION_MIN_TRACKS} tracks",
    }
    reasons = tuple(f"{name} is {metrics[name]}, the rule needs {rule[name]}"
                    for name, ok in checks.items() if not ok)  # fmt: skip
    gated = {k: v for k, v in metrics.items()
             if v is None or (isinstance(v, int | float) and not isinstance(v, bool))}  # fmt: skip
    gated["J_judged"] = int(judge_junction)
    return CameraGate(gate_id=gate_id, gate_sha256=gate_sha256,
                      outcome="no_go" if reasons else "go", metrics=gated, checks=checks,
                      reasons=reasons)  # fmt: skip
