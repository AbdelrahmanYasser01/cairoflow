"""Traffic World Model: ground trajectories, lanes, turning movements, flows and queues.

Inputs: the clip manifest, tracks (image pixels), the intersection geometry (lane polygons and
gate lines drawn by hand in local ground metres) and the calibration it was drawn against.
Every value is computed by the fixed rules below; none comes from a model.

- Ground position: the track's contact point (box bottom-centre) through the homography. Its error
  is the ground error caused by `contact_sigma_px` of image error at that point, so it grows with
  distance from the camera. Points at or beyond the horizon are dropped and listed.
- Speed: distance between the interpolated positions `speed_half_window_s` before and after, over
  the elapsed time (clamped to the track's span).
- Movement: the first inbound crossing of a stop line, then the first outbound crossing of an exit
  line. Inbound means towards the junction centre, the origin of the ground frame. The turn follows
  from the two arms, for right-hand traffic. A track with only one of the two crossings is
  ambiguous: listed with a reason, not counted.
- Lane: the approach lane containing the track's last position before the stop-line crossing,
  looking back at most `lane_lookback_s`. A point inside no polygon, or several, has no lane.
- Flow: stop-line crossings over the clip duration, in veh/h, with an exact Poisson 95% interval
  (counting noise only, not detection misses). This is the discharge flow; it equals the arrival
  flow only when there is no queue at the start or the end of the clip.
- Queue: per approach lane, sampled every `queue_interval_s`. Vehicles slower than
  `stopped_speed_m_s` are chained back from the stop line while each is within
  `max_queue_spacing_m` of the previous one. Length runs from the stop line to the contact point of
  the last queued vehicle, with that point's ground error. Gaps inside a track are interpolated;
  vehicles that were never tracked are missed. A queue whose last vehicle is within
  `max_queue_spacing_m` of the lane polygon's far end is marked `censored` (a lower bound).
- Queue at the end of red: with a signal timeline, each approach lane of an arm is sampled in the
  last frame before its red turns green. Where an arm has a protected-left head, its left-only
  lanes follow that head and its other lanes the `all` head. A red that turns into `unknown` has
  no known end and is noted, not sampled; a red still showing when the clip ends has not ended.
  The speed window is centred, so this relies on start-up lost time (normally well over
  `speed_half_window_s`) to keep the queue stationary.

`build_world_model` takes camera tracks through a calibration. `build_world_model_from_ground`
takes vehicles whose ground positions are published already (e.g. the NGSIM trajectories, in the
geometry's frame, with the dataset's stated position error); both use the same rules above.
"""

from __future__ import annotations

import bisect
import hashlib
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.evidence import (
    AmbiguousMovement,
    Arm,
    CalibrationReport,
    CalibrationSpec,
    FrameRef,
    Gate,
    GroundTrack,
    GroundTrackPoint,
    IntersectionGeometry,
    LanePolygon,
    MovementCount,
    MovementTrack,
    Provenance,
    QueueSample,
    SignalTimeline,
    SourceClip,
    Track,
    TrafficWorldModel,
    Turn,
)
from cairoflow_perception.calibration import CalibrationError, Homography
from cairoflow_perception.overlay import tracks_sha256

ARMS: tuple[Arm, ...] = ("N", "E", "S", "W")  # clockwise
_TURN_STEPS: dict[Turn, int] = {"u_turn": 0, "left": 1, "through": 2, "right": 3}
_TURN_ORDER: tuple[Turn, ...] = ("left", "through", "right", "u_turn")
_ORIGIN = (0.0, 0.0)
Point = tuple[float, float]


class WorldModelError(ValueError):
    """The inputs do not belong together, or cannot support the measurements."""


class WorldModelPolicy(BaseModel):
    """Every threshold of the world model; recorded in its provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contact_sigma_px: float = Field(2.0, gt=0, description="assumed contact-point image error")
    speed_half_window_s: float = Field(0.5, gt=0)
    stopped_speed_m_s: float = Field(1.0, gt=0)
    max_queue_spacing_m: float = Field(12.0, gt=0)
    lane_lookback_s: float = Field(2.0, ge=0)
    queue_interval_s: float = Field(1.0, gt=0)
    require_accepted_calibration: bool = True


def turn_for(approach: Arm, exit_arm: Arm) -> Turn:
    """The turn of a vehicle entering from `approach` and leaving by `exit_arm` (right-hand)."""
    step = (ARMS.index(exit_arm) - ARMS.index(approach)) % 4
    return next(turn for turn, value in _TURN_STEPS.items() if value == step)


def exit_for(approach: Arm, turn: Turn) -> Arm:
    return ARMS[(ARMS.index(approach) + _TURN_STEPS[turn]) % 4]


def _poisson_cdf(count: int, mean: float) -> float:
    if mean == 0:
        return 1.0
    log_mean = math.log(mean)
    return math.fsum(math.exp(i * log_mean - mean - math.lgamma(i + 1)) for i in range(count + 1))


def _root(increasing: Callable[[float], float], high: float) -> float:
    low = 0.0
    for _ in range(200):
        mid = (low + high) / 2
        if increasing(mid) < 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def poisson_interval(count: int, confidence: float = 0.95) -> tuple[float, float]:
    """Exact (Garwood) confidence interval for a Poisson mean, from one observed count."""
    if count < 0 or not 0 < confidence < 1:
        raise ValueError("count must be >= 0 and confidence in (0, 1)")
    tail = (1 - confidence) / 2
    high = count + 20 * math.sqrt(count + 1) + 20
    upper = _root(lambda mean: tail - _poisson_cdf(count, mean), high)
    if count == 0:
        return 0.0, upper
    lower = _root(lambda mean: 1 - _poisson_cdf(count - 1, mean) - tail, high)
    return lower, upper


def _orient(a: Point, b: Point, p: Point) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _inside(polygon: Sequence[Point], p: Point) -> bool:
    inside = False
    for (x1, y1), (x2, y2) in zip(polygon, (*polygon[1:], polygon[0]), strict=True):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _lane_at(lanes: Sequence[LanePolygon], p: Point) -> str | None:
    hits = [lane.lane_id for lane in lanes if _inside(lane.polygon_m, p)]
    return hits[0] if len(hits) == 1 else None


@dataclass(frozen=True)
class GroundSeries:
    """One vehicle's published ground positions in the geometry's frame, in time order."""

    track_id: str
    class_label: str
    frames: tuple[FrameRef, ...]
    points_m: tuple[Point, ...]
    errors_m: tuple[float, ...] = field(metadata={"description": "1-sigma ground error"})


@dataclass
class _Ground:
    """One track on the ground, in frame order."""

    track_id: str
    class_label: str
    frames: list[FrameRef] = field(default_factory=list)
    points: list[Point] = field(default_factory=list)
    errors: list[float] = field(default_factory=list)
    unprojected: list[int] = field(default_factory=list)
    times: list[float] = field(default_factory=list)

    def position_at(self, t: float) -> Point | None:
        if not self.times or not self.times[0] <= t <= self.times[-1]:
            return None
        i = bisect.bisect_left(self.times, t)
        if self.times[i] == t:
            return self.points[i]
        t0, t1 = self.times[i - 1], self.times[i]
        f = (t - t0) / (t1 - t0)
        (x0, y0), (x1, y1) = self.points[i - 1], self.points[i]
        return x0 + f * (x1 - x0), y0 + f * (y1 - y0)

    def speed_at(self, t: float, half_window: float) -> float | None:
        if not self.times:
            return None
        t0, t1 = max(t - half_window, self.times[0]), min(t + half_window, self.times[-1])
        if t1 - t0 < half_window - 1e-9:
            return None
        a, b = self.position_at(t0), self.position_at(t1)
        assert a is not None and b is not None
        return math.dist(a, b) / (t1 - t0)


def _project(track: Track, homography: Homography, sigma_px: float) -> _Ground:
    ground = _Ground(track.track_id, track.class_label)
    for point in track.points:
        try:
            position = homography.to_ground(point.contact_point_px)
            error = homography.ground_error_at(point.contact_point_px, sigma_px)
        except CalibrationError:
            ground.unprojected.append(point.frame.frame_index)
            continue
        ground.frames.append(point.frame)
        ground.points.append(position)
        ground.errors.append(error)
    ground.times = [frame.timestamp_s for frame in ground.frames]
    return ground


def _crossings(ground: _Ground, gates: Sequence[Gate]) -> list[tuple[float, Gate]]:
    """Every crossing of a gate in its own direction, as (time s, gate), in time order."""
    found: list[tuple[float, Gate]] = []
    for i in range(len(ground.points) - 1):
        p0, p1 = ground.points[i], ground.points[i + 1]
        t0, t1 = ground.times[i], ground.times[i + 1]
        for gate in gates:
            a, b = gate.segment_m
            d0, d1 = _orient(a, b, p0), _orient(a, b, p1)
            if (d0 > 0) == (d1 > 0):
                continue  # same side of the gate line
            if _orient(p0, p1, a) * _orient(p0, p1, b) > 0:
                continue  # passes beside the gate segment
            inbound = (d1 > 0) == (_orient(a, b, _ORIGIN) > 0)
            if inbound != (gate.role == "stop_line"):
                continue
            found.append((t0 + d0 / (d0 - d1) * (t1 - t0), gate))
    found.sort(key=lambda c: (c[0], ARMS.index(c[1].arm), c[1].role))
    return found


def _lane_before(
    ground: _Ground, time_s: float, lanes: Sequence[LanePolygon], lookback_s: float
) -> str | None:
    for i in range(len(ground.times) - 1, -1, -1):
        if ground.times[i] > time_s:
            continue
        if ground.times[i] < time_s - lookback_s:
            break
        lane = _lane_at(lanes, ground.points[i])
        if lane is not None:
            return lane
    return None


def _sample_frames(frames: Sequence[FrameRef], interval_s: float) -> list[FrameRef]:
    picked: list[FrameRef] = []
    next_t = frames[0].timestamp_s if frames else 0.0
    for frame in frames:
        if frame.timestamp_s >= next_t - 1e-9:
            picked.append(frame)
            while next_t <= frame.timestamp_s + 1e-9:
                next_t += interval_s
    return picked


def _check_inputs(
    clip: SourceClip,
    tracks: Sequence[Track],
    geometry: IntersectionGeometry,
    spec: CalibrationSpec,
    report: CalibrationReport,
    policy: WorldModelPolicy,
) -> None:
    if {geometry.clip_id, spec.clip_id, report.clip_id} != {clip.clip_id}:
        raise WorldModelError("the clip, geometry and calibration must belong to the same clip")
    if geometry.calibration_spec_sha256 != report.spec_sha256:
        raise WorldModelError("the geometry was drawn against a different calibration")
    if policy.require_accepted_calibration and not report.accepted:
        raise WorldModelError("the calibration is not accepted: " + "; ".join(report.reasons))
    ids = [track.track_id for track in tracks]
    if len(set(ids)) != len(ids):
        raise WorldModelError("track ids must be unique")
    if any(track.clip_id != clip.clip_id for track in tracks):
        raise WorldModelError("every track must belong to the clip")


def build_world_model(
    clip: SourceClip,
    tracks: Sequence[Track],
    geometry: IntersectionGeometry,
    calibration_spec: CalibrationSpec,
    calibration_report: CalibrationReport,
    policy: WorldModelPolicy | None = None,
    *,
    signals: SignalTimeline | None = None,
    now: datetime | None = None,
) -> TrafficWorldModel:
    """Measure movements, flows and queues from one clip's tracks."""
    policy = policy or WorldModelPolicy()
    _check_inputs(clip, tracks, geometry, calibration_spec, calibration_report, policy)
    if signals is not None and (
        signals.clip_id != clip.clip_id
        or abs(signals.window_s - clip.output_stream.duration_s) > 1e-6
    ):
        raise WorldModelError("the signal timeline belongs to another clip or duration")
    try:
        homography = Homography.from_report(calibration_report, calibration_spec)
    except CalibrationError as error:
        raise WorldModelError(str(error)) from error

    grounds = [
        _project(track, homography, policy.contact_sigma_px)
        for track in sorted(tracks, key=lambda t: t.track_id)
    ]

    def error_at(p: Point) -> float:
        return homography.ground_error_at(homography.to_image(p), policy.contact_sigma_px)

    window_s = clip.output_stream.duration_s
    movements, ambiguous = _movements(grounds, geometry, window_s, policy)
    context = _QueueContext(
        grounds=grounds,
        approach_lanes=_approach_lanes(geometry),
        stop_lines={gate.arm: gate for gate in geometry.gates if gate.role == "stop_line"},
        error_at=error_at,
        policy=policy,
    )
    queues = _queues(clip.frames, context)
    end_of_red, signal_notes = _end_of_red_queues(clip.frames, signals, context)
    geometry_sha256 = hashlib.sha256(geometry.model_dump_json().encode("utf-8")).hexdigest()
    tracks_hash = tracks_sha256(tracks)
    return TrafficWorldModel(
        clip_id=clip.clip_id,
        geometry_id=geometry.geometry_id,
        geometry_sha256=geometry_sha256,
        calibration_spec_sha256=calibration_report.spec_sha256,
        tracks_sha256=tracks_hash,
        window_s=window_s,
        ground_tracks=_ground_tracks(grounds, geometry, policy, None),
        movements=movements,
        ambiguous=ambiguous,
        queues=queues,
        signal_timeline=signals,
        signal_per_second=signals.per_second() if signals else (),
        end_of_red_queues=end_of_red,
        signal_notes=signal_notes,
        provenance=Provenance(
            kind="approximated",
            produced_by="cairoflow_perception.world_model",
            produced_at_utc=now or datetime.now(UTC),
            input_sha256=(
                clip.output_sha256,
                tracks_hash,
                geometry_sha256,
                calibration_report.spec_sha256,
                *((signals.annotation_sha256,) if signals and signals.annotation_sha256 else ()),
            ),
            parameters=policy.model_dump(),
        ),
    )


def _approach_lanes(geometry: IntersectionGeometry) -> list[LanePolygon]:
    return sorted(
        (lane for lane in geometry.lanes if lane.direction == "approach"),
        key=lambda lane: (ARMS.index(lane.arm), lane.lane_id),
    )


def _grounds_from_series(series: Sequence[GroundSeries]) -> list[_Ground]:
    grounds = []
    for s in sorted(series, key=lambda s: s.track_id):
        if not len(s.frames) == len(s.points_m) == len(s.errors_m):
            raise WorldModelError(f"{s.track_id}: frames, points and errors differ in length")
        times = [frame.timestamp_s for frame in s.frames]
        if any(b <= a for a, b in zip(times, times[1:], strict=False)):
            raise WorldModelError(f"{s.track_id}: timestamps must increase strictly")
        grounds.append(
            _Ground(
                s.track_id,
                s.class_label,
                frames=list(s.frames),
                points=list(s.points_m),
                errors=list(s.errors_m),
                times=times,
            )
        )
    return grounds


def count_movements(
    series: Sequence[GroundSeries],
    geometry: IntersectionGeometry,
    window_s: float,
    policy: WorldModelPolicy | None = None,
) -> tuple[tuple[MovementCount, ...], tuple[AmbiguousMovement, ...]]:
    """Movements and flows of published ground trajectories (no queues, no signals)."""
    return _movements(
        _grounds_from_series(series), geometry, window_s, policy or WorldModelPolicy()
    )


def build_world_model_from_ground(
    window_id: str,
    frames: Sequence[FrameRef],
    window_s: float,
    series: Sequence[GroundSeries],
    geometry: IntersectionGeometry,
    source_sha256: str,
    policy: WorldModelPolicy | None = None,
    *,
    signals: SignalTimeline | None = None,
    ground_track_interval_s: float | None = None,
    parameters: dict[str, str | int | float | bool] | None = None,
    now: datetime | None = None,
) -> TrafficWorldModel:
    """Measure movements, flows and queues from published ground trajectories.

    `frames` are the observation window's time steps (queues are sampled from them); `source_sha256`
    is the dataset file, which the geometry must be anchored on. With `ground_track_interval_s`,
    the stored ground tracks keep one point per interval (the measurements use every point).
    """
    policy = policy or WorldModelPolicy()
    if geometry.ground_source_sha256 != source_sha256 or geometry.clip_id != window_id:
        raise WorldModelError("the geometry was not derived from this dataset and window")
    if any(frame.clip_id != window_id for frame in frames):
        raise WorldModelError("every frame must belong to the observation window")
    ids = [s.track_id for s in series]
    if len(set(ids)) != len(ids):
        raise WorldModelError("track ids must be unique")
    if signals is not None and (
        signals.clip_id != window_id or abs(signals.window_s - window_s) > 1e-6
    ):
        raise WorldModelError("the signal timeline belongs to another window or duration")
    grounds = _grounds_from_series(series)
    for ground in grounds:
        if any(frame.clip_id != window_id for frame in ground.frames):
            raise WorldModelError(f"{ground.track_id}: a point lies outside the window")
    movements, ambiguous = _movements(grounds, geometry, window_s, policy)
    errors = [e for ground in grounds for e in ground.errors]
    error = max(errors) if errors else 0.0
    context = _QueueContext(
        grounds=grounds,
        approach_lanes=_approach_lanes(geometry),
        stop_lines={gate.arm: gate for gate in geometry.gates if gate.role == "stop_line"},
        error_at=lambda _p: error,
        policy=policy,
    )
    queues = _queues(frames, context)
    end_of_red, signal_notes = _end_of_red_queues(frames, signals, context)
    keep = (
        None
        if ground_track_interval_s is None
        else {f.frame_index for f in _sample_frames(frames, ground_track_interval_s)}
    )
    geometry_sha256 = hashlib.sha256(geometry.model_dump_json().encode("utf-8")).hexdigest()
    return TrafficWorldModel(
        clip_id=window_id,
        geometry_id=geometry.geometry_id,
        geometry_sha256=geometry_sha256,
        ground_source_sha256=source_sha256,
        tracks_sha256=source_sha256,
        window_s=window_s,
        ground_tracks=_ground_tracks(grounds, geometry, policy, keep),
        movements=movements,
        ambiguous=ambiguous,
        queues=queues,
        signal_timeline=signals,
        signal_per_second=signals.per_second() if signals else (),
        end_of_red_queues=end_of_red,
        signal_notes=signal_notes,
        provenance=Provenance(
            kind="approximated",
            produced_by="cairoflow_perception.world_model.build_world_model_from_ground",
            produced_at_utc=now or datetime.now(UTC),
            input_sha256=(
                source_sha256,
                geometry_sha256,
                *((signals.annotation_sha256,) if signals and signals.annotation_sha256 else ()),
            ),
            parameters={
                **policy.model_dump(exclude={"contact_sigma_px", "require_accepted_calibration"}),
                "queue_length_error_m": error,
                **(
                    {"ground_track_interval_s": ground_track_interval_s}
                    if ground_track_interval_s is not None
                    else {}
                ),
                **(parameters or {}),
            },
        ),
    )


def _movements(
    grounds: Sequence[_Ground],
    geometry: IntersectionGeometry,
    window_s: float,
    policy: WorldModelPolicy,
) -> tuple[tuple[MovementCount, ...], tuple[AmbiguousMovement, ...]]:
    approach_lanes = _approach_lanes(geometry)
    lanes_by_id = {lane.lane_id: lane for lane in geometry.lanes}
    observed: dict[tuple[Arm, Arm], list[MovementTrack]] = defaultdict(list)
    ambiguous: list[AmbiguousMovement] = []
    for ground in grounds:
        crossings = _crossings(ground, geometry.gates)
        stop = next((c for c in crossings if c[1].role == "stop_line"), None)
        out = next(
            (c for c in crossings if c[1].role == "exit_line" and (stop is None or c[0] > stop[0])),
            None,
        )
        track_id = ground.track_id
        if stop is None and out is None:
            continue
        if stop is None or out is None:
            ambiguous.append(
                AmbiguousMovement(
                    track_id=track_id,
                    approach=stop[1].arm if stop else None,
                    exit_arm=out[1].arm if out else None,
                    reason=(
                        f"crossed the {stop[1].arm} stop line but no exit line after it"
                        if stop
                        else f"crossed the {out[1].arm} exit line without a stop-line crossing"
                    ),
                )
            )
            continue
        approach, exit_arm = stop[1].arm, out[1].arm
        lane_id = _lane_before(
            ground,
            stop[0],
            [lane for lane in approach_lanes if lane.arm == approach],
            policy.lane_lookback_s,
        )
        turn = turn_for(approach, exit_arm)
        observed[(approach, exit_arm)].append(
            MovementTrack(
                track_id=track_id,
                lane_id=lane_id,
                stop_line_time_s=round(stop[0], 6),
                exit_line_time_s=round(out[0], 6),
                follows_markings=(
                    None if lane_id is None else turn in lanes_by_id[lane_id].allowed_turns
                ),
            )
        )

    scale = 3600.0 / window_s
    keys = {
        (lane.arm, exit_for(lane.arm, turn))
        for lane in approach_lanes
        for turn in lane.allowed_turns
    } | set(observed)
    movements: list[MovementCount] = []
    for approach, exit_arm in sorted(
        keys, key=lambda k: (ARMS.index(k[0]), _TURN_ORDER.index(turn_for(*k)))
    ):
        turn = turn_for(approach, exit_arm)
        members = sorted(
            observed.get((approach, exit_arm), []),
            key=lambda m: (m.stop_line_time_s, m.track_id),
        )
        low, high = poisson_interval(len(members))
        movements.append(
            MovementCount(
                approach=approach,
                exit_arm=exit_arm,
                turn=turn,
                count_veh=len(members),
                flow_veh_per_h=round(len(members) * scale, 6),
                flow_ci95_veh_per_h=(round(low * scale, 6), round(high * scale, 6)),
                marked_lane_ids=tuple(
                    lane.lane_id
                    for lane in approach_lanes
                    if lane.arm == approach and turn in lane.allowed_turns
                ),
                tracks=tuple(members),
            )
        )
    return tuple(movements), tuple(sorted(ambiguous, key=lambda a: a.track_id))


def _ground_tracks(
    grounds: Sequence[_Ground],
    geometry: IntersectionGeometry,
    policy: WorldModelPolicy,
    keep: set[int] | None,
) -> tuple[GroundTrack, ...]:
    """The ground tracks as stored: every point, or only frames in `keep`."""
    return tuple(
        GroundTrack(
            track_id=ground.track_id,
            class_label=ground.class_label,
            points=tuple(
                GroundTrackPoint(
                    frame=frame,
                    position_m=(round(p[0], 6), round(p[1], 6)),
                    position_error_m=round(error, 6),
                    speed_m_s=_rounded(
                        ground.speed_at(frame.timestamp_s, policy.speed_half_window_s)
                    ),
                    lane_id=_lane_at(geometry.lanes, p),
                )
                for frame, p, error in zip(ground.frames, ground.points, ground.errors, strict=True)
                if keep is None or frame.frame_index in keep
            ),
            unprojected_frames=tuple(ground.unprojected),
        )
        for ground in grounds
    )


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


@dataclass(frozen=True)
class _QueueContext:
    grounds: Sequence[_Ground]
    approach_lanes: Sequence[LanePolygon]
    stop_lines: dict[Arm, Gate]
    error_at: Callable[[Point], float]  # ground error of a position, metres
    policy: WorldModelPolicy


def _end_of_red_queues(
    frames: Sequence[FrameRef], signals: SignalTimeline | None, context: _QueueContext
) -> tuple[tuple[QueueSample, ...], tuple[str, ...]]:
    """Queues in the last frame before each red turns green, and the red ends left out."""
    if signals is None:
        return (), ("no signal timeline: queues at the end of red were not measured",)
    times = [frame.timestamp_s for frame in frames]
    samples: list[QueueSample] = []
    notes: list[str] = []
    lane_arms = {lane.arm for lane in context.approach_lanes}
    left_heads = {arm for arm, head in signals.heads() if head == "left"}

    def follows(lane: LanePolygon, head: str) -> bool:
        left_only = lane.arm in left_heads and set(lane.allowed_turns) == {"left"}
        return left_only == (head == "left")

    for red, after in zip(signals.intervals, signals.intervals[1:], strict=False):
        if red.arm != after.arm or red.head != after.head or red.state != "red":
            continue
        head = "" if red.head == "all" else f" {red.head}"
        where = f"{red.arm}{head} red ending at {red.end_s} s"
        lanes = [
            lane
            for lane in context.approach_lanes
            if lane.arm == red.arm and follows(lane, red.head)
        ]
        if after.state != "green":
            notes.append(f"{where}: next state is {after.state}, so the end of red is unknown")
        elif red.arm not in lane_arms:
            notes.append(f"{where}: no approach lane drawn for arm {red.arm}")
        elif not lanes:
            notes.append(f"{where}: no approach lane follows this head")
        elif (index := bisect.bisect_left(times, red.end_s) - 1) < 0:
            notes.append(f"{where}: no frame before it")
        else:
            samples += _queue_at(frames[index], lanes, context)
    samples.sort(key=lambda q: (q.frame.frame_index, ARMS.index(q.arm), q.lane_id))
    return tuple(samples), tuple(notes)


def _queues(frames: Sequence[FrameRef], context: _QueueContext) -> tuple[QueueSample, ...]:
    samples: list[QueueSample] = []
    for frame in _sample_frames(frames, context.policy.queue_interval_s):
        samples += _queue_at(frame, context.approach_lanes, context)
    return tuple(samples)


def _queue_at(
    frame: FrameRef, lanes: Sequence[LanePolygon], context: _QueueContext
) -> list[QueueSample]:
    policy, stop_lines = context.policy, context.stop_lines
    t = frame.timestamp_s
    present: list[tuple[str, str, Point, float | None]] = []
    for ground in context.grounds:
        position = ground.position_at(t)
        if position is None:
            continue
        lane_id = _lane_at(context.approach_lanes, position)
        if lane_id is not None:
            speed = ground.speed_at(t, policy.speed_half_window_s)
            present.append((lane_id, ground.track_id, position, speed))
    samples: list[QueueSample] = []
    for lane in lanes:
        a, b = stop_lines[lane.arm].segment_m
        origin_side = _orient(a, b, _ORIGIN) > 0
        length = math.dist(a, b)
        stopped: list[tuple[float, str, Point]] = []
        unknown: list[str] = []
        for lane_id, track_id, position, speed in present:
            if lane_id != lane.lane_id:
                continue
            if speed is None:
                unknown.append(track_id)
            elif speed < policy.stopped_speed_m_s and (
                (_orient(a, b, position) > 0) != origin_side
            ):
                stopped.append((abs(_orient(a, b, position)) / length, track_id, position))
        chain: list[tuple[float, str, Point]] = []
        reach = 0.0
        for distance, track_id, position in sorted(stopped):
            if distance - reach > policy.max_queue_spacing_m:
                break
            chain.append((distance, track_id, position))
            reach = distance
        error = context.error_at(chain[-1][2]) if chain else 0.0
        # the lane is observed only up to its polygon's far end; a chain that reaches within one
        # spacing of it may continue unseen
        far = max(abs(_orient(a, b, p)) / length for p in lane.polygon_m)
        samples.append(
            QueueSample(
                frame=frame,
                arm=lane.arm,
                lane_id=lane.lane_id,
                queued_veh=len(chain),
                length_m=round(reach, 6),
                length_error_m=round(error, 6),
                track_ids=tuple(track_id for _, track_id, _ in chain),
                unknown_speed_track_ids=tuple(sorted(unknown)),
                censored=bool(chain) and reach > far - policy.max_queue_spacing_m,
            )
        )
    return samples
