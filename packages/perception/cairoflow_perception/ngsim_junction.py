"""NGSIM intersection 2 as a junction: frame, ground series, geometry and signal timeline.

Everything here is derived from the published NGSIM Lankershim trajectories (table `8ect-6jqj`,
CC BY-SA 3.0; DATA_REGISTER.md §13-§16) by the fixed rules below. Nothing uses camera 2: no
pixels, no calibration, no image registration (ADR-019). Thresholds are policy fields, recorded
with every output.

Frame. ADR-017's local metres (origin at the int 2 reference point, x grid east, y grid north),
rotated so +y points along Lankershim northbound: the circular mean bearing of NB through rows
near the junction. Arms: N and S are Lankershim north and south of the junction, E and W the
cross street. Positions are NGSIM's vehicle front centres; the dataset's stated accuracy (about
4 ft along the road, 2 ft across) is used as their error.

Geometry (ADR-014 label `approximated` unless a value says otherwise):
- A point belongs to the arm whose axis it is nearest (|u| against |v|). Its offset is measured
  to the right of inbound travel (right-hand traffic). Its direction of travel along the arm
  comes from its velocity: approach statistics use rows that are inbound or stopped, on the
  approach side; exit statistics use rows clearly outbound (`moving_m_s`), so a vehicle on the
  wrong side of the axis (a left bay against the median) is never taken for an exit.
- Stop gate: a counting line just downstream of where vehicles stop: the most advanced
  `head_quantile` of the arm's pooled per-lane queue heads, or, where an arm is tracked only from
  its limit line (the cross streets), the `first_tracked_quantile` of its entering vehicles'
  first tracked distance; the nearer of the two to the centre, less `gate_margin_m`. Laterally
  it spans the inbound vehicles crossing that distance, on either side of the axis. The stop
  *position* reported separately is the median queue head, or none where vehicles are first
  tracked where they stop (not observable).
- Exit gate: at `exit_distance_m`, or nearer where exiting vehicles stop being tracked, spanning
  the outbound vehicles there.
- Lanes: one per NGSIM `lane_id` (not 0) seen in a band from the gate back to where vehicles are
  first tracked or stand queued (at most `lane_band_m`), at its median lateral position;
  boundaries midway between neighbours, but no lane wider than `max_lane_width_m` (a median or a
  gap stays outside every lane). Lanes closer than `min_lane_spacing_m` are merged into the one
  with more rows. Approach and exit lanes of an arm meet no nearer than midway between their
  innermost lanes, so they never overlap.
- Allowed turns: the turns vehicles made from the lane (at least `min_turn_share` of its vehicles
  and `min_turn_count` of them). Every observed movement is allowed in at least one lane. A lane
  no vehicle was assigned to gets turns by position (`synthetic`). Observed use, not markings.

Signal timeline (label `trajectory-derived`): from stop-line crossing times, per signal head.
- Heads: each approach arm has an `all` head; its reference crossings are its through vehicles.
  Permissive left turners (no left head) are added only when the throughs are fewer than
  `min_reference_crossings`, since they also cross at the end of green. Right turns never count
  (right turn on red). An arm with too few crossings follows the opposite arm's `all` head if it
  has at least `min_follow_crossings` and at least `follow_min_share` of them fall in that arm's
  service; otherwise its own sparse windows are used, single crossings included. An arm with no
  through or left crossing at all follows the opposite arm, recorded as assumed.
- A protected `left` head is created where an arm's left turners rarely cross while the opposite
  through movement is being served (share at most `protected_left_max_share`). An arm with at
  least `min_follow_crossings` left turners, mostly crossing during that protected left green,
  follows it, clipped where it would conflict.
- Service windows: crossings split where they are more than `service_gap_s` apart; windows of
  fewer than `min_window_crossings` are dropped and listed. Times are on a `time_step_s` grid
  (the SUMO step). Green runs from the first crossing less `startup_lost_s` (rounded down), for
  at least `min_green_s`, until `yellow_s` of yellow (both synthetic) that ends at or after one
  data step past the last crossing (rounded up). Windows are committed in time order of their
  start (the first discharge, the sharpest evidence). A committed window of a conflicting head
  that runs into a new window's start is ended `all_red_s` before it, if it keeps at least
  `yellow_s` + 1 s (its late crossings, stragglers, are then outside it); whatever conflict
  remains, the new window keeps its longest conflict-free part. Followers are fitted around
  committed windows. Every change is listed.
- Before a head's first green and after its last yellow the state is `unknown`. Red gaps between
  windows include time when the head may have been green with nobody crossing.
- The cycle to simulate: `representative_cycle`.
- Version 2 (`SignalRevision`, DATA_REGISTER.md §22) changes two rules; without it the estimate
  is version 1, the committed record's. An arm with no left head always adds its permissive left
  turners to its `all` evidence (they move only on that green; version 1 dropped west greens
  whose few throughs fell below `min_window_crossings`). And a window's yellow starts at its
  last crossing (rounded up) instead of ending there, so every reference vehicle crosses in
  green; conflicts are trimmed as before.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.evidence import (
    Arm,
    FrameRef,
    Gate,
    IntersectionGeometry,
    LanePolygon,
    MovementCount,
    SignalHead,
    SignalInterval,
    SignalState,
    SignalTimeline,
    Turn,
)
from cairoflow_perception.ngsim import (
    US_SURVEY_FOOT_M,
    LocalFrame,
    Trajectories,
    circular_mean_deg,
    lane_offsets,
    queue_heads,
)
from cairoflow_perception.world_model import GroundSeries

ARMS: tuple[Arm, ...] = ("N", "E", "S", "W")
OUTWARD: dict[Arm, tuple[float, float]] = {"N": (0, 1), "E": (1, 0), "S": (0, -1), "W": (-1, 0)}
OPPOSITE: dict[Arm, Arm] = {"N": "S", "S": "N", "E": "W", "W": "E"}
NGSIM_NB, NGSIM_THROUGH = 2, 1  # `direction` and `movement` codes (metadata PDF)
WINDOW_START_MS = 1118935800000  # 2005-06-16 08:30:00.0 PDT (DATA_REGISTER.md §13)
FRAME_S = 0.1
NGSIM_POSITION_ERROR_M = 4 * US_SURVEY_FOOT_M  # stated along-road accuracy (metadata PDF)
Head = tuple[Arm, SignalHead]


class DerivationError(ValueError):
    """The trajectories cannot support a value under the fixed rules."""


class JunctionPolicy(BaseModel):
    """Thresholds of the geometry derivation; recorded with its output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    frame_radius_m: float = Field(60.0, gt=0, description="NB rows used for the frame bearing")
    frame_min_speed_m_s: float = Field(3.0, ge=0)
    frame_min_rows: int = Field(100, ge=1)
    box_half_size_m: float = Field(110.0, gt=0, description="study area around the centre")
    arm_min_distance_m: float = Field(8.0, ge=0, description="nearer: the junction interior")
    moving_m_s: float = Field(0.5, gt=0, description="along-arm speed that fixes the direction")
    stopped_m_s: float = Field(0.3, gt=0)
    stopped_zone_max_m: float = Field(80.0, gt=0, description="queue heads searched up to here")
    min_head_samples: int = Field(50, ge=1)
    head_quantile: float = Field(0.9, gt=0, lt=1, description="pooled queue heads, advanced end")
    first_tracked_quantile: float = Field(0.05, gt=0, lt=1)
    gate_margin_m: float = Field(0.5, ge=0)
    gate_span_window_m: float = Field(1.5, gt=0, description="rows this close to a gate set it")
    gate_span_quantile: float = Field(0.005, ge=0, lt=0.5, description="each end of the span")
    gate_span_pad_m: float = Field(1.0, ge=0)
    exit_distance_m: float = Field(25.0, gt=0)
    exit_backoff_m: float = Field(1.0, ge=0, description="inside the last tracked distance")
    lane_band_m: float = Field(30.0, gt=0, description="lane statistics beyond the gate")
    min_band_m: float = Field(5.0, gt=0)
    polygon_length_m: float = Field(100.0, gt=0, description="approach lanes, at most")
    polygon_backoff_m: float = Field(2.0, ge=0, description="inside the first tracked distance")
    min_lane_rows: int = Field(100, ge=1)
    min_lane_spacing_m: float = Field(2.0, gt=0)
    max_lane_width_m: float = Field(4.0, gt=0)
    min_turn_share: float = Field(0.05, ge=0, le=1)
    min_turn_count: int = Field(2, ge=1)
    heavy_length_m: float = Field(7.5, gt=0, description="median length at or above: 'truck'")


class SignalPolicy(BaseModel):
    """Thresholds of the signal estimate; recorded with its output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service_gap_s: float = Field(25.0, gt=0)
    startup_lost_s: float = Field(2.0, ge=0, description="synthetic (HCM default)")
    yellow_s: float = Field(4.0, gt=0, description="synthetic")
    all_red_s: float = Field(1.0, ge=0, description="synthetic")
    min_green_s: float = Field(5.0, gt=0)
    min_reference_crossings: int = Field(20, ge=1)
    min_window_crossings: int = Field(2, ge=1)
    protected_left_max_share: float = Field(0.2, ge=0, le=1)
    follow_min_share: float = Field(0.5, ge=0, le=1)
    min_follow_crossings: int = Field(5, ge=1, description="needed to decide on following")
    service_margin_s: float = Field(2.0, ge=0)
    time_step_s: float = Field(
        1.0, gt=0, description="timeline grid; the SUMO step, so the cut cycle runs as recorded"
    )


class SignalRevision(BaseModel):
    """Version 2 of the signal estimate's rules (module docstring); none keeps version 1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field("2", description="recorded with the timeline it produced")
    permissive_left_evidence: bool = Field(
        True, description="an arm with no left head adds its left crossings to its 'all' evidence"
    )
    yellow_from_last_crossing: bool = Field(
        True, description="a window's yellow starts at its last crossing instead of ending there"
    )


# --- Frame and rows -----------------------------------------------------------------------


@dataclass(frozen=True)
class JunctionFrame:
    """Local metres from a State Plane origin, rotated so +y has grid bearing `bearing_deg`."""

    origin_x_ft: float
    origin_y_ft: float
    bearing_deg: float

    def to_junction(self, x_ft, y_ft) -> tuple[np.ndarray, np.ndarray]:
        x, y = LocalFrame(self.origin_x_ft, self.origin_y_ft).to_local(x_ft, y_ft)
        b = math.radians(self.bearing_deg)
        return x * math.cos(b) - y * math.sin(b), x * math.sin(b) + y * math.cos(b)


def junction_frame(
    traj: Trajectories,
    speed_m_s: np.ndarray,
    heading_deg: np.ndarray,
    origin_ft: tuple[float, float],
    policy: JunctionPolicy | None = None,
) -> tuple[JunctionFrame, int]:
    """The frame whose +y is the mean bearing of NB through rows near the origin, and their count.

    `speed_m_s` and `heading_deg` come from `ngsim.kinematics` on the same rows.
    """
    policy = policy or JunctionPolicy()
    x, y = LocalFrame(*origin_ft).to_local(traj.x_ft, traj.y_ft)
    rows = (
        (np.hypot(x, y) < policy.frame_radius_m)
        & (traj.direction == NGSIM_NB)
        & (traj.movement == NGSIM_THROUGH)
        & np.isfinite(heading_deg)
        & (speed_m_s > policy.frame_min_speed_m_s)
    )
    if rows.sum() < policy.frame_min_rows:
        raise DerivationError(f"only {int(rows.sum())} NB through rows near the origin")
    bearing = round(circular_mean_deg(heading_deg[rows]), 4)
    return JunctionFrame(origin_ft[0], origin_ft[1], bearing), int(rows.sum())


@dataclass(frozen=True)
class JunctionRows:
    """Published rows inside the study box and window, in junction metres, sorted by run then time.

    A run is a stretch of one `vehicle_id` with rows exactly one frame apart.
    """

    vehicle_id: np.ndarray
    time_ms: np.ndarray
    u: np.ndarray
    v: np.ndarray
    speed_m_s: np.ndarray
    lane_id: np.ndarray
    length_m: np.ndarray
    run: np.ndarray
    movement_code: np.ndarray  # NGSIM `movement` (1 TH, 2 LT, 3 RT), as published per row
    vu: np.ndarray  # velocity, m/s, from the neighbouring rows of the same run (0 if alone)
    vv: np.ndarray

    def __len__(self) -> int:
        return len(self.u)

    def run_bounds(self) -> tuple[np.ndarray, np.ndarray]:
        starts = np.flatnonzero(np.r_[True, self.run[1:] != self.run[:-1]])
        return starts, np.r_[starts[1:], len(self.run)]


def junction_rows(
    traj: Trajectories,
    frame: JunctionFrame,
    speed_m_s: np.ndarray,
    window_start_ms: int,
    window_s: float,
    policy: JunctionPolicy | None = None,
) -> JunctionRows:
    policy = policy or JunctionPolicy()
    u, v = frame.to_junction(traj.x_ft, traj.y_ft)
    t = traj.global_time_ms
    half = policy.box_half_size_m
    keep = (
        (np.abs(u) <= half)
        & (np.abs(v) <= half)
        & (t >= window_start_ms)
        & (t < window_start_ms + round(window_s * 1000))
    )
    order = np.flatnonzero(keep)[np.lexsort((t[keep], traj.vehicle_id[keep]))]
    vid, tm = traj.vehicle_id[order], t[order]
    new_run = np.r_[True, (vid[1:] != vid[:-1]) | (tm[1:] - tm[:-1] != round(FRAME_S * 1000))]
    uu, vv = u[order], v[order]
    vel_u, vel_v = np.zeros(len(uu)), np.zeros(len(uu))
    linked = ~new_run[1:]  # row i and i + 1 belong to the same run
    du, dv = np.diff(uu) / FRAME_S, np.diff(vv) / FRAME_S
    vel_u[:-1][linked], vel_v[:-1][linked] = du[linked], dv[linked]  # forward difference
    last = np.r_[~linked, True] & np.r_[False, linked]  # last row of a run: backward difference
    vel_u[last], vel_v[last] = du[np.flatnonzero(last) - 1], dv[np.flatnonzero(last) - 1]
    return JunctionRows(
        vehicle_id=vid,
        time_ms=tm,
        u=uu,
        v=vv,
        speed_m_s=speed_m_s[order],
        lane_id=traj.lane_id[order],
        length_m=traj.length_ft[order] * US_SURVEY_FOOT_M,
        run=np.cumsum(new_run) - 1,
        movement_code=traj.movement[order],
        vu=vel_u,
        vv=vel_v,
    )


def window_frames(window_id: str, window_s: float) -> tuple[FrameRef, ...]:
    """The observation window's 0.1 s time steps, as frames."""
    n = round(window_s / FRAME_S)
    return tuple(
        FrameRef(clip_id=window_id, frame_index=i, timestamp_s=round(i * FRAME_S, 1))
        for i in range(n)
    )


def ground_series(
    rows: JunctionRows,
    window_id: str,
    window_start_ms: int,
    policy: JunctionPolicy | None = None,
) -> list[GroundSeries]:
    """One series per run: 'car:<vehicle_id>' or 'truck:<vehicle_id>' (by median length), with a
    '.<k>' suffix when a vehicle has several runs."""
    policy = policy or JunctionPolicy()
    starts, ends = rows.run_bounds()
    seen: Counter[int] = Counter()
    out = []
    for s, e in zip(starts, ends, strict=True):
        vid = int(rows.vehicle_id[s])
        seen[vid] += 1
        heavy = float(np.median(rows.length_m[s:e])) >= policy.heavy_length_m
        suffix = "" if seen[vid] == 1 else f".{seen[vid] - 1}"
        steps = [round((t - window_start_ms) / (FRAME_S * 1000)) for t in rows.time_ms[s:e]]
        out.append(
            GroundSeries(
                track_id=f"{'truck' if heavy else 'car'}:{vid}{suffix}",
                class_label="truck" if heavy else "car",
                frames=tuple(
                    FrameRef(clip_id=window_id, frame_index=i, timestamp_s=round(i * FRAME_S, 1))
                    for i in steps
                ),
                points_m=tuple(
                    (round(float(a), 4), round(float(b), 4))
                    for a, b in zip(rows.u[s:e], rows.v[s:e], strict=True)
                ),
                errors_m=(round(NGSIM_POSITION_ERROR_M, 4),) * (e - s),
            )
        )
    return sorted(out, key=lambda g: g.track_id)


# --- Geometry ---------------------------------------------------------------------------


def arm_of(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """The arm whose axis each point is nearest."""
    return np.where(np.abs(v) >= np.abs(u), np.where(v > 0, "N", "S"), np.where(u > 0, "E", "W"))


def arm_coords(u, v, arm: Arm) -> tuple[np.ndarray, np.ndarray]:
    """(distance from the centre along the arm, offset to the right of inbound travel)."""
    ox, oy = OUTWARD[arm]
    rx, ry = -oy, ox
    u, v = np.asarray(u), np.asarray(v)
    return u * ox + v * oy, u * rx + v * ry


def arm_point(arm: Arm, distance_m: float, offset_m: float) -> tuple[float, float]:
    ox, oy = OUTWARD[arm]
    return (
        round(distance_m * ox - offset_m * oy, 4),
        round(distance_m * oy + offset_m * ox, 4),
    )


@dataclass(frozen=True)
class LaneLayout:
    """One lane across an arm: NGSIM lane id, lateral centre and edges (right of inbound travel)."""

    lane_id: int
    rows: int
    centre_m: float
    inner_m: float
    outer_m: float


def lane_layout(
    offsets: np.ndarray,
    lane_id: np.ndarray,
    min_rows: int,
    min_spacing_m: float,
    max_width_m: float = 4.0,
) -> tuple[list[LaneLayout], list[str]]:
    """Lanes from lateral offsets (sorted from the arm axis outwards) and the merges made.

    Each lane reaches midway to its neighbours, but at most `max_width_m / 2` either side of its
    centre, so a median or a gap between lanes stays outside every lane. An edge lane mirrors
    its inner side.
    """
    stats = lane_offsets(offsets, lane_id, min_rows=min_rows)
    notes: list[str] = []
    kept: list = []
    for stat in stats:
        if kept and stat.median_m - kept[-1].median_m < min_spacing_m:
            loser, winner = sorted((kept[-1], stat), key=lambda s: s.rows)
            notes.append(
                f"lane_id {loser.lane_id} ({loser.median_m:.2f} m, {loser.rows} rows) merged into "
                f"lane_id {winner.lane_id} ({winner.median_m:.2f} m)"
            )
            kept[-1] = winner
        else:
            kept.append(stat)
    if not kept:
        return [], notes
    half = max_width_m / 2
    if len(kept) == 1:
        notes.append(f"a single lane: width set to {max_width_m:g} m (no neighbour)")
    layout = []
    for i, s in enumerate(kept):
        c = s.median_m
        to_inner = min((c - kept[i - 1].median_m) / 2, half) if i else None
        to_outer = min((kept[i + 1].median_m - c) / 2, half) if i + 1 < len(kept) else None
        # an edge lane mirrors its other side (a single lane gets the maximum)
        to_inner = to_inner if to_inner is not None else (to_outer or half)
        to_outer = to_outer if to_outer is not None else to_inner
        layout.append(
            LaneLayout(
                s.lane_id, s.rows, round(c, 3), round(c - to_inner, 3), round(c + to_outer, 3)
            )
        )
    return layout, notes


@dataclass
class ArmStats:
    """What the rows say about one arm (distances from the centre, metres)."""

    arm: Arm
    stop_gate_m: float | None = None
    stop_position_m: float | None = None
    queue_heads: dict[int, tuple[float, float, float, int]] = field(default_factory=dict)
    first_tracked_p5_m: float | None = None
    first_tracked_p95_m: float | None = None
    exit_gate_m: float | None = None
    last_tracked_p5_m: float | None = None
    last_tracked_p95_m: float | None = None
    approach: list[LaneLayout] = field(default_factory=list)
    exits: list[LaneLayout] = field(default_factory=list)
    approach_band_m: tuple[float, float] | None = None
    exit_band_m: tuple[float, float] | None = None
    stop_gate_span_m: tuple[float, float] | None = None
    exit_gate_span_m: tuple[float, float] | None = None
    notes: list[str] = field(default_factory=list)


def _span(offsets: np.ndarray, quantile: float, pad: float) -> tuple[float, float]:
    lo, hi = np.quantile(offsets, [quantile, 1 - quantile])
    return round(float(lo) - pad, 3), round(float(hi) + pad, 3)


def _pooled_heads(d: np.ndarray, lane: np.ndarray, time_ms: np.ndarray) -> np.ndarray:
    """Per lane and time step, the distance of the most advanced stopped vehicle (pooled)."""
    order = np.lexsort((d, time_ms, lane))
    lane, time_ms, d = lane[order], time_ms[order], d[order]
    first = np.r_[True, (lane[1:] != lane[:-1]) | (time_ms[1:] != time_ms[:-1])]
    return d[first]


def arm_statistics(rows: JunctionRows, policy: JunctionPolicy | None = None) -> dict[Arm, ArmStats]:
    """Gates, stop positions and lane layouts per arm, by the module's rules.

    A row's direction of travel along the arm comes from its velocity: approach statistics use
    rows that are inbound or stopped, exit statistics rows that are clearly outbound, so a vehicle
    on the wrong side of the arm axis (a left bay against the median) is not taken for an exit.
    """
    policy = policy or JunctionPolicy()
    arms = arm_of(rows.u, rows.v)
    starts, ends = rows.run_bounds()
    near = policy.arm_min_distance_m
    out: dict[Arm, ArmStats] = {}
    for arm in ARMS:
        st = ArmStats(arm)
        d, k = arm_coords(rows.u, rows.v, arm)
        ox, oy = OUTWARD[arm]
        along = rows.vu * ox + rows.vv * oy  # m/s, positive outbound
        on = arms == arm
        not_out = along < policy.moving_m_s
        inbound, outbound = along < -policy.moving_m_s, along > policy.moving_m_s
        first = [i for i in starts if on[i] and k[i] > 0 and d[i] > near and not_out[i]]
        last = [e - 1 for e in ends if on[e - 1] and d[e - 1] > near and outbound[e - 1]]
        q = policy.first_tracked_quantile
        if first:
            st.first_tracked_p5_m, st.first_tracked_p95_m = (
                round(float(x), 3) for x in np.quantile(d[first], [q, 1 - q])
            )
        if last:
            st.last_tracked_p5_m, st.last_tracked_p95_m = (
                round(float(x), 3) for x in np.quantile(d[last], [q, 1 - q])
            )
        zone = on & (k > 0) & (d > near) & (d < policy.stopped_zone_max_m) & not_out
        heads = queue_heads(
            -d[zone],
            rows.lane_id[zone],
            rows.time_ms[zone],
            rows.speed_m_s[zone],
            stopped_m_s=policy.stopped_m_s,
            min_samples=policy.min_head_samples,
        )
        st.queue_heads = {
            h.lane_id: (round(-h.q90_m, 3), round(-h.median_m, 3), round(-h.q10_m, 3), h.samples)
            for h in heads
        }
        stopped = (
            zone & np.isfinite(rows.speed_m_s) & (rows.speed_m_s < policy.stopped_m_s)
            & (rows.lane_id != 0)
        )  # fmt: skip
        pooled = _pooled_heads(d[stopped], rows.lane_id[stopped], rows.time_ms[stopped])
        candidates = []
        if len(pooled) >= policy.min_head_samples:
            candidates.append(float(np.quantile(pooled, 1 - policy.head_quantile)))
        if st.first_tracked_p5_m is not None:
            candidates.append(st.first_tracked_p5_m)
        if not first or not candidates:
            out[arm] = st  # no approach traffic
            continue
        st.stop_gate_m = round(min(candidates) - policy.gate_margin_m, 3)
        reach = max([st.first_tracked_p95_m or 0.0, *(v[2] for v in st.queue_heads.values())])
        if st.queue_heads:
            stop = round(float(np.median([v[1] for v in st.queue_heads.values()])), 3)
            if (st.first_tracked_p95_m or 0.0) < stop + policy.min_band_m:
                st.notes.append(
                    f"stop position not observable: vehicles are first tracked at about "
                    f"{st.first_tracked_p95_m} m, where they stop ({stop} m)"
                )
            else:
                st.stop_position_m = stop
        band = min(
            policy.lane_band_m,
            max(policy.min_band_m, reach + policy.gate_span_pad_m - st.stop_gate_m),
        )
        st.approach_band_m = (st.stop_gate_m, round(st.stop_gate_m + band, 3))
        in_band = (
            on & (k > 0) & not_out & (rows.lane_id != 0)
            & (d >= st.approach_band_m[0]) & (d <= st.approach_band_m[1])
        )  # fmt: skip
        st.approach, notes = lane_layout(
            k[in_band],
            rows.lane_id[in_band],
            policy.min_lane_rows,
            policy.min_lane_spacing_m,
            policy.max_lane_width_m,
        )
        st.notes += [f"approach: {n}" for n in notes]
        at_gate = inbound & (np.abs(d - st.stop_gate_m) < policy.gate_span_window_m)
        at_gate &= (arms == arm) | (np.abs(k) < policy.max_lane_width_m)
        st.stop_gate_span_m = _span(k[at_gate], policy.gate_span_quantile, policy.gate_span_pad_m)
        out[arm] = st

    for arm in ARMS:
        st = out[arm]
        d, k = arm_coords(rows.u, rows.v, arm)
        ox, oy = OUTWARD[arm]
        outbound = (rows.vu * ox + rows.vv * oy) > policy.moving_m_s
        on = arms == arm
        if st.last_tracked_p5_m is None:
            continue
        st.exit_gate_m = round(
            min(policy.exit_distance_m, st.last_tracked_p5_m - policy.exit_backoff_m), 3
        )
        far = (st.last_tracked_p95_m or 0.0) - st.exit_gate_m
        band = min(policy.lane_band_m, max(policy.min_band_m, far))
        st.exit_band_m = (st.exit_gate_m, round(st.exit_gate_m + band, 3))
        lanes = (
            on & (k < 0) & outbound & (rows.lane_id != 0)
            & (d >= st.exit_band_m[0]) & (d <= st.exit_band_m[1])
        )  # fmt: skip
        exits, notes = lane_layout(
            -k[lanes],
            rows.lane_id[lanes],
            policy.min_lane_rows,
            policy.min_lane_spacing_m,
            policy.max_lane_width_m,
        )
        st.exits = exits
        st.notes += [f"exit: {n}" for n in notes]
        at_gate = outbound & on & (np.abs(d - st.exit_gate_m) < policy.gate_span_window_m)
        if at_gate.any():
            st.exit_gate_span_m = _span(
                k[at_gate], policy.gate_span_quantile, policy.gate_span_pad_m
            )
    return out


_TURN_RULE = {"inner": ("left", "through"), "outer": ("through", "right"), "middle": ("through",)}


def build_geometry(
    stats: dict[Arm, ArmStats],
    geometry_id: str,
    window_id: str,
    source_sha256: str,
    turns: dict[str, tuple[Turn, ...]] | None = None,
    turn_sources: dict[str, str] | None = None,
    policy: JunctionPolicy | None = None,
) -> IntersectionGeometry:
    """Lane polygons and gates from the arm statistics.

    `turns` maps lane ids to allowed turns; without it every approach lane allows left, through
    and right (a provisional geometry for counting which turns each lane is used for).
    """
    policy = policy or JunctionPolicy()
    lanes: list[LanePolygon] = []
    gates: list[Gate] = []
    for arm in ARMS:
        st = stats[arm]
        # the two directions of an arm meet midway between their innermost lanes
        sep = (
            (st.approach[0].centre_m - st.exits[0].centre_m) / 2
            if st.approach and st.exits
            else None
        )
        if st.stop_gate_m is not None and st.approach and st.stop_gate_span_m:
            near = st.stop_gate_m
            far = near + max(
                st.approach_band_m[1] - near if st.approach_band_m else 0.0,
                min(
                    policy.polygon_length_m,
                    (st.first_tracked_p5_m or near) - near - policy.polygon_backoff_m,
                ),
            )
            for lane in st.approach:
                inner = lane.inner_m if sep is None else max(lane.inner_m, sep)
                lane_key = f"{arm}{lane.lane_id}"
                allowed = (turns or {}).get(lane_key, ("left", "through", "right"))
                source = (turn_sources or {}).get(lane_key, "provisional: all turns")
                lanes.append(
                    LanePolygon(
                        lane_id=lane_key,
                        arm=arm,
                        direction="approach",
                        polygon_m=(
                            arm_point(arm, near, inner),
                            arm_point(arm, far, inner),
                            arm_point(arm, far, lane.outer_m),
                            arm_point(arm, near, lane.outer_m),
                        ),
                        allowed_turns=allowed,
                        provenance="synthetic"
                        if source.startswith("synthetic")
                        else "approximated",
                        source=(
                            f"NGSIM lane_id {lane.lane_id}: median {lane.centre_m:.2f} m right "
                            f"of the {arm} arm axis over {lane.rows} rows at "
                            f"{st.approach_band_m[0]:.1f}-{st.approach_band_m[1]:.1f} m; edges "
                            f"midway to neighbours; turns: {source}"
                        ),
                    )
                )
            a, b = st.stop_gate_span_m
            gates.append(
                Gate(
                    arm=arm,
                    role="stop_line",
                    segment_m=(arm_point(arm, near, a), arm_point(arm, near, b)),
                    provenance="approximated",
                    source=(
                        f"counting line {near:.2f} m from the centre: nearer of the pooled queue "
                        "heads' most advanced decile and the first tracked distance, less "
                        f"{policy.gate_margin_m} m, spanning inbound vehicles at that distance; "
                        "not the painted limit line"
                    ),
                )
            )
        if st.exit_gate_m is not None and st.exit_gate_span_m:
            if st.exits and st.exit_band_m:
                for lane in st.exits:
                    near, far = st.exit_band_m
                    inner = lane.inner_m if sep is None else max(lane.inner_m, -sep)
                    lanes.append(
                        LanePolygon(
                            lane_id=f"{arm}x{lane.lane_id}",
                            arm=arm,
                            direction="exit",
                            polygon_m=(
                                arm_point(arm, near, -inner),
                                arm_point(arm, far, -inner),
                                arm_point(arm, far, -lane.outer_m),
                                arm_point(arm, near, -lane.outer_m),
                            ),
                            allowed_turns=(),
                            provenance="approximated",
                            source=(
                                f"NGSIM lane_id {lane.lane_id}: median {lane.centre_m:.2f} m "
                                f"left of inbound travel over {lane.rows} rows at "
                                f"{near:.1f}-{far:.1f} m"
                            ),
                        )
                    )
            a, b = st.exit_gate_span_m
            gates.append(
                Gate(
                    arm=arm,
                    role="exit_line",
                    segment_m=(
                        arm_point(arm, st.exit_gate_m, a),
                        arm_point(arm, st.exit_gate_m, b),
                    ),
                    provenance="approximated",
                    source=(
                        f"counting line {st.exit_gate_m:.2f} m from the centre, inside the "
                        "distance to which exiting vehicles are tracked"
                    ),
                )
            )
    return IntersectionGeometry(
        geometry_id=geometry_id,
        clip_id=window_id,
        ground_source_sha256=source_sha256,
        lanes=tuple(lanes),
        gates=tuple(gates),
    )


def allowed_turns(
    movements: Sequence[MovementCount],
    stats: dict[Arm, ArmStats],
    policy: JunctionPolicy | None = None,
) -> tuple[dict[str, tuple[Turn, ...]], dict[str, str], list[str]]:
    """Allowed turns per approach lane from where counted vehicles came from, with sources."""
    policy = policy or JunctionPolicy()
    used: dict[str, Counter[Turn]] = defaultdict(Counter)
    observed: dict[Arm, Counter[Turn]] = defaultdict(Counter)
    for movement in movements:
        if movement.turn == "u_turn":
            continue
        observed[movement.approach][movement.turn] += movement.count_veh
        for track in movement.tracks:
            if track.lane_id is not None:
                used[track.lane_id][movement.turn] += 1
    turns: dict[str, tuple[Turn, ...]] = {}
    sources: dict[str, str] = {}
    notes: list[str] = []
    order: tuple[Turn, ...] = ("left", "through", "right")
    for arm in ARMS:
        layout = stats[arm].approach
        for position, lane in enumerate(layout):
            key = f"{arm}{lane.lane_id}"
            counts = used.get(key, Counter())
            total = sum(counts.values())
            if total:
                keep = [
                    t
                    for t in order
                    if counts[t] >= policy.min_turn_count
                    and counts[t] >= policy.min_turn_share * total
                ]
                turns[key] = tuple(keep)
                sources[key] = "observed use " + ", ".join(
                    f"{t} {counts[t]}" for t in order if counts[t]
                )
            else:
                where = (
                    "inner"
                    if position == 0
                    else "outer"
                    if position == len(layout) - 1
                    else "middle"
                )
                rule = tuple(t for t in _TURN_RULE[where] if observed[arm][t]) or ("through",)
                turns[key] = rule
                sources[key] = f"synthetic: no vehicle assigned; {where} lane rule"
        for turn in order:
            if not observed[arm][turn] or any(turn in turns[f"{arm}{x.lane_id}"] for x in layout):
                continue
            if not layout:
                continue
            best = max(
                layout,
                key=lambda x: (
                    used.get(f"{arm}{x.lane_id}", Counter())[turn],
                    -x.centre_m if turn == "left" else x.centre_m,
                ),
            )
            key = f"{arm}{best.lane_id}"
            turns[key] = tuple(t for t in order if t in turns[key] or t == turn)
            sources[key] += (
                f"; {turn} added: {observed[arm][turn]} {turn} vehicles, no lane passed the rule"
            )
            notes.append(f"{arm} {turn}: allowed from {key} so the observed movement is served")
        for key in [f"{arm}{x.lane_id}" for x in layout]:
            if not turns[key]:
                raise DerivationError(f"lane {key} has no allowed turn")
    return turns, sources, notes


# --- Signal timeline -------------------------------------------------------------------------


def _windows(times: Sequence[float], gap_s: float) -> list[tuple[float, float, int]]:
    """Crossing times split into service windows (first, last, count)."""
    ts = sorted(times)
    out: list[tuple[float, float, int]] = []
    for t in ts:
        if out and t - out[-1][1] <= gap_s:
            first, _, n = out[-1]
            out[-1] = (first, t, n + 1)
        else:
            out.append((t, t, 1))
    return out


def _inside(t: float, windows: Sequence[tuple[float, float, int]], margin: float) -> bool:
    return any(a - margin <= t <= b + margin for a, b, _ in windows)


def _crossing_arms(a: Arm, b: Arm) -> bool:
    return (a in "NS") != (b in "NS")


def heads_conflict(a: Head, b: Head) -> bool:
    """Whether two heads may not show green or yellow at the same time (a head conflicts with
    itself: its windows need a red between them)."""
    if a[0] == b[0]:
        return a[1] == b[1]
    if _crossing_arms(a[0], b[0]):
        return True
    return {a[1], b[1]} == {"all", "left"}


@dataclass(frozen=True)
class SignalEstimate:
    timeline: SignalTimeline
    heads: dict[str, str]
    dropped: tuple[str, ...]
    adjustments: tuple[str, ...]
    consistency: tuple[dict[str, object], ...]
    representative_cycle: dict[str, object]


def _r(x: float) -> float:
    """Remove float noise from times built on the timeline grid."""
    return round(x + 0.0, 6)


def estimate_signal_timeline(
    movements: Sequence[MovementCount],
    window_id: str,
    window_s: float,
    timeline_id: str,
    source: str,
    policy: SignalPolicy | None = None,
    revision: SignalRevision | None = None,
) -> SignalEstimate:
    """A per-head signal timeline from stop-line crossing times, by the module's rules (version
    1, or version 2 with `revision`)."""
    policy = policy or SignalPolicy()
    permissive = revision is not None and revision.permissive_left_evidence
    yellow_after = revision is not None and revision.yellow_from_last_crossing
    times: dict[tuple[Arm, Turn], list[float]] = defaultdict(list)
    for movement in movements:
        times[(movement.approach, movement.turn)] += [t.stop_line_time_s for t in movement.tracks]
    arms = [a for a in ARMS if any(times.get((a, t)) for t in ("left", "through", "right"))]
    gap, margin = policy.service_gap_s, policy.service_margin_s
    heads: dict[str, str] = {}

    left_own: list[Arm] = []
    for arm in arms:
        lefts, opposing = times.get((arm, "left"), []), times.get((OPPOSITE[arm], "through"), [])
        if len(lefts) < policy.min_reference_crossings:
            continue
        if len(opposing) < policy.min_reference_crossings:
            heads[f"{arm} left"] = (
                f"none: {len(opposing)} opposing through crossings, too few to test protection"
            )
            continue
        served = _windows(opposing, gap)
        share = sum(_inside(t, served, margin) for t in lefts) / len(lefts)
        if share <= policy.protected_left_max_share:
            left_own.append(arm)
            heads[f"{arm} left"] = (
                f"own: {len(lefts)} left crossings, {share:.0%} while the opposite through "
                "was being served"
            )
        else:
            heads[f"{arm} left"] = f"none: {share:.0%} of lefts cross during the opposing through"
    left_follow: dict[Arm, Arm] = {}
    for arm in arms:
        lefts = times.get((arm, "left"), [])
        if arm in left_own or not lefts or OPPOSITE[arm] not in left_own:
            continue
        if len(lefts) < policy.min_follow_crossings:
            heads[f"{arm} left"] = f"none: {len(lefts)} left crossings, too few to decide"
            continue
        served = _windows(times[(OPPOSITE[arm], "left")], gap)
        share = sum(_inside(t, served, margin) for t in lefts) / len(lefts)
        if share >= policy.follow_min_share:
            left_follow[arm] = OPPOSITE[arm]
            heads[f"{arm} left"] = (
                f"follows {OPPOSITE[arm]} left: {share:.0%} of its {len(lefts)} left crossings "
                f"fall in {OPPOSITE[arm]}'s protected-left service"
            )
        else:
            heads.setdefault(f"{arm} left", f"none: only {share:.0%} with the opposite's lefts")
    has_left = set(left_own) | set(left_follow)

    reference: dict[Head, list[float]] = {}
    sparse: set[Head] = set()
    all_follow: dict[Arm, Arm] = {}

    def all_reference(arm: Arm) -> tuple[list[float], str]:
        """Through crossings; permissive lefts are added if the throughs are too few (version 1)
        or whenever the arm has no left head (version 2)."""
        through = times.get((arm, "through"), [])
        if arm in has_left or (len(through) >= policy.min_reference_crossings and not permissive):
            return sorted(through), "through"
        return sorted(through + times.get((arm, "left"), [])), "through and left"

    for arm in arms:
        ref, what = all_reference(arm)
        if len(ref) >= policy.min_reference_crossings:
            reference[(arm, "all")] = ref
            heads[f"{arm} all"] = f"own: {len(ref)} {what} crossings"
    # arms with too few crossings: first those with some, then those with none (which follow)
    empty = [a for a in arms if (a, "all") not in reference and not all_reference(a)[0]]
    for arm in arms:
        if (arm, "all") in reference or arm in empty:
            continue
        partner = OPPOSITE[arm]
        ref, what = all_reference(arm)
        if (partner, "all") in reference and len(ref) >= policy.min_follow_crossings:
            served = _windows(reference[(partner, "all")], gap)
            share = sum(_inside(t, served, margin) for t in ref) / len(ref)
            if share >= policy.follow_min_share:
                all_follow[arm] = partner
                heads[f"{arm} all"] = (
                    f"follows {partner} all: {share:.0%} of its {len(ref)} {what} crossings fall "
                    f"in {partner}'s service"
                )
                continue
            note = f"{share:.0%} of them in {partner}'s service, so it does not follow it"
        elif (partner, "all") in reference:
            note = "too few to test following the opposite arm"
        else:
            note = f"{partner} has no own estimate either"
        reference[(arm, "all")] = ref
        sparse.add((arm, "all"))
        heads[f"{arm} all"] = f"own, sparse: {len(ref)} {what} crossings ({note})"
    for arm in empty:
        partner = OPPOSITE[arm]
        if (partner, "all") not in reference:
            raise DerivationError(f"arm {arm}: no crossing to estimate its signal from")
        all_follow[arm] = partner
        what = all_reference(arm)[1]
        heads[f"{arm} all"] = (
            f"follows {partner} all, assumed: no {what} crossing to test it (only rights)"
        )
    for arm in left_own:
        reference[(arm, "left")] = sorted(times[(arm, "left")])

    dropped: list[str] = []
    candidates: list[tuple[float, int, Head, float, float]] = []
    head_order = {h: i for i, h in enumerate(sorted(reference, key=_head_key))}
    for head, ts in reference.items():
        for first, last, n in _windows(ts, gap):
            if n < policy.min_window_crossings and head not in sparse:
                dropped.append(f"{_name(head)}: {n} crossing at {first:g} s, outside any window")
                continue
            # on the time grid: green from the first crossing less start-up (rounded down); the
            # yellow ends (version 1) or starts (version 2) at or after one data step past the
            # last crossing (rounded up)
            step = policy.time_step_s
            start = _r(max(0.0, math.floor((first - policy.startup_lost_s) / step) * step))
            end = math.ceil((last + FRAME_S) / step - 1e-9) * step
            if not yellow_after:
                end -= policy.yellow_s
            green_end = _r(max(end, start + policy.min_green_s))
            candidates.append((start, head_order[head], head, green_end, n))
    committed: dict[Head, list[tuple[float, float]]] = defaultdict(list)  # green start, yellow end
    adjustments: list[str] = []

    def trim_earlier(head: Head, start: float) -> None:
        """End conflicting committed windows that run into `start`, where they stay long enough."""
        for other, spans in committed.items():
            if other == head or not heads_conflict(head, other):
                continue
            for i, (a, b) in enumerate(spans):
                new_end = _r(start - policy.all_red_s)
                if a < start and b > new_end and new_end - a >= policy.yellow_s + 1.0:
                    spans[i] = (a, new_end)
                    adjustments.append(
                        f"{_name(other)}: {a:g}-{b:g} s ended at {new_end:g} s, before "
                        f"{_name(head)} starts at {start:g} s"
                    )

    def commit(head: Head, start: float, green_end: float, label: str) -> None:
        end = _r(min(window_s, green_end + policy.yellow_s))
        blocked = sorted(
            (_r(a - policy.all_red_s), _r(b + policy.all_red_s))
            for other, spans in committed.items()
            if heads_conflict(head, other)
            for a, b in spans
        )
        free, cursor = [], start
        for a, b in blocked:
            if b <= cursor or a >= end:
                continue
            if a > cursor:
                free.append((cursor, a))
            cursor = max(cursor, b)
        if cursor < end:
            free.append((cursor, end))
        best = max(free, key=lambda p: (p[1] - p[0], -p[0]), default=None)
        if best is None or best[1] - best[0] < policy.yellow_s + 1.0:
            dropped.append(f"{_name(head)} {label} {start:g}-{end:g} s: conflicts leave no green")
            return
        if best != (start, end):
            adjustments.append(
                f"{_name(head)} {label}: {start:g}-{end:g} s cut to {best[0]:g}-{best[1]:g} s "
                "to avoid a conflicting head"
            )
        committed[head].append((_r(best[0]), _r(best[1])))

    for start, _, head, green_end, n in sorted(candidates):
        trim_earlier(head, start)
        commit(head, start, green_end, f"window of {n}")
    for arm, partner in sorted(all_follow.items(), key=lambda kv: ARMS.index(kv[0])):
        for a, b in list(committed[(partner, "all")]):
            commit((arm, "all"), a, _r(b - policy.yellow_s), f"following {partner}")
    for arm, partner in sorted(left_follow.items(), key=lambda kv: ARMS.index(kv[0])):
        for a, b in list(committed[(partner, "left")]):
            commit((arm, "left"), a, _r(b - policy.yellow_s), f"following {partner} left")

    intervals: list[SignalInterval] = []
    for head in sorted(committed, key=_head_key):
        intervals += _head_intervals(head, sorted(committed[head]), window_s, policy.yellow_s)
    timeline = SignalTimeline(
        timeline_id=timeline_id,
        clip_id=window_id,
        label="trajectory-derived",
        source=source,
        window_s=window_s,
        intervals=tuple(intervals),
    )
    return SignalEstimate(
        timeline=timeline,
        heads=heads,
        dropped=tuple(dropped),
        adjustments=tuple(adjustments),
        consistency=consistency(timeline, movements),
        representative_cycle=representative_cycle(timeline),
    )


def _head_key(head: Head) -> tuple[int, int]:
    return ARMS.index(head[0]), ("all", "left").index(head[1])


def _name(head: Head) -> str:
    return f"{head[0]} {head[1]}"


def _head_intervals(
    head: Head, spans: Sequence[tuple[float, float]], window_s: float, yellow_s: float
) -> list[SignalInterval]:
    arm, kind = head
    out: list[SignalInterval] = []

    def add(state: SignalState, a: float, b: float) -> None:
        if b > a:
            out.append(SignalInterval(arm=arm, head=kind, state=state, start_s=a, end_s=b))

    cursor = 0.0
    for i, (start, end) in enumerate(spans):
        green_end = _r(max(start, end - yellow_s))
        add("unknown" if i == 0 else "red", cursor, start)
        add("green", start, green_end)
        add("yellow", green_end, end)
        cursor = end
    add("unknown", cursor, window_s)
    return out


def consistency(
    timeline: SignalTimeline, movements: Sequence[MovementCount]
) -> tuple[dict[str, object], ...]:
    """Per movement, the share of its stop-line crossings while its head showed green or yellow.

    A consistency check of the estimate against all counted vehicles (including those it was not
    built from, such as right turners), not an independent validation.
    """
    heads = set(timeline.heads())
    out = []
    for movement in movements:
        if movement.turn == "u_turn" or not movement.tracks:
            continue
        head: SignalHead = (
            "left" if movement.turn == "left" and (movement.approach, "left") in heads else "all"
        )
        states = [
            timeline.state_at(movement.approach, t.stop_line_time_s, head) for t in movement.tracks
        ]
        known = [s for s in states if s not in (None, "unknown")]
        served = sum(s in ("green", "yellow") for s in known)
        out.append(
            {
                "approach": movement.approach,
                "turn": movement.turn,
                "head": head,
                "crossings": len(states),
                "during_known_state": len(known),
                "during_green_or_yellow": served,
                "share": round(served / len(known), 4) if known else None,
            }
        )
    return tuple(out)


_NEXT: dict[str, str] = {"green": "yellow", "yellow": "red", "red": "green"}


def representative_cycle(timeline: SignalTimeline) -> dict[str, object]:
    """The cycle to repeat in SUMO, and how typical it is.

    Candidate cuts run from an arm's green onset to the next onset of the arms turning green then
    (as the compiler cuts them). A cut qualifies when every head shows green in it, none is
    unknown, and each head's last state leads legally to its first (SUMO repeats the cut). The
    chosen cut minimises the summed distance of each head's green in it, and of its length, from
    their medians (head greens over the timeline; length over consecutive onsets of every arm);
    earliest on a tie. Cuts from different arms overlap, so they are reported as candidate cuts,
    beside the number of physical cycles the window holds (the most onsets of one arm, less one).
    """
    heads = timeline.heads()
    onsets: dict[Arm, list[float]] = defaultdict(list)
    own = [i for i in timeline.intervals if i.head == "all"]
    for previous, current in zip(own, own[1:], strict=False):
        if previous.arm == current.arm and current.state == "green" and previous.state != "unknown":
            onsets[current.arm].append(current.start_s)

    def spans(arm: Arm, kind: SignalHead, start: float, end: float) -> list[SignalInterval]:
        return [
            i
            for i in timeline.intervals
            if i.arm == arm and i.head == kind and i.start_s < end and i.end_s > start
        ]

    def complete(start: float, end: float) -> bool:
        for arm, kind in heads:
            inside = spans(arm, kind, start, end)
            if any(i.state == "unknown" for i in inside) or not any(
                i.state == "green" for i in inside
            ):
                return False
            first, last = inside[0].state, inside[-1].state
            if first != last and _NEXT[last] != first:
                return False
        return True

    cuts = sorted(
        {
            (start, min(ts[ts.index(start) + 1] for ts in onsets.values() if start in ts[:-1]))
            for times in onsets.values()
            for start in times[:-1]
        }
    )
    cycles = [c for c in cuts if complete(*c)]
    if not cycles:
        raise DerivationError("no complete cycle in which every head turns green")

    def green_in(arm: Arm, kind: SignalHead, start: float, end: float) -> float:
        return sum(
            min(i.end_s, end) - max(i.start_s, start)
            for i in spans(arm, kind, start, end)
            if i.state == "green"
        )

    medians = {
        (arm, kind): float(
            np.median(
                [
                    i.end_s - i.start_s
                    for i in timeline.intervals
                    if i.arm == arm and i.head == kind and i.state == "green"
                ]
            )
        )
        for arm, kind in heads
    }
    spacings = [b - a for ts in onsets.values() for a, b in zip(ts, ts[1:], strict=False)]
    median_length = float(np.median(spacings))

    def score(cycle: tuple[float, float]) -> float:
        a, b = cycle
        return abs((b - a) - median_length) + sum(
            abs(green_in(arm, kind, a, b) - medians[(arm, kind)]) for arm, kind in heads
        )

    start, end = min(cycles, key=lambda c: (round(score(c), 6), c[0]))
    return {
        "start_s": start,
        "end_s": end,
        "length_s": round(end - start, 3),
        "qualifying_cuts": len(cycles),
        "candidate_cuts": len(cuts),
        "physical_cycles_in_window": max(len(ts) for ts in onsets.values()) - 1,
        "median_length_s": round(median_length, 3),
        "onset_spacing_range_s": [min(spacings), max(spacings)],
        "greens_in_cycle_s": {
            f"{arm} {kind}": round(green_in(arm, kind, start, end), 3) for arm, kind in heads
        },
        "median_greens_s": {f"{arm} {kind}": round(medians[(arm, kind)], 3) for arm, kind in heads},
        "distance_from_medians_s": round(score((start, end)), 3),
        "rule": (
            "qualifying cut (every head green, none unknown, closes legally) whose head greens "
            "and length are nearest their medians (summed |difference|); earliest on a tie"
        ),
    }
