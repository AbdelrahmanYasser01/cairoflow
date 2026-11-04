"""Baseline validity: the recorded SUMO baseline against the NGSIM trajectories, by identical rules.

The observed side is the trajectory world model of the committed intersection 2 record
(`cairoflow_perception.world_model.build_world_model_from_ground` on the published positions).
The simulated side is the same function run on SUMO floating-car data (FCD) of the recorded seeds,
in a SUMO-equivalent geometry (`sim_geometry`):

- Frame: the record's junction metres; the compiler builds the SUMO network in them.
- Stop line: the observed counting-line rule, the most advanced decile of the pooled stopped queue
  heads less 0.5 m (`stop_gate_rule`), applied to SUMO's own stopped vehicles. SUMO's stop lines
  lie 5-6.5 m from the observed gates (DATA_REGISTER.md §16), so each side counts at its own.
- Approach lanes: SUMO's lanes, named after the NGSIM lane they were compiled from and observed
  only as far beyond the stop line as that NGSIM lane is, so a queue is censored alike.
- Exit lines: at the observed exit-line distances, across SUMO's exit lanes.
- Window and box: SUMO seconds [warm-up, warm-up + horizon) shifted to [0, horizon), positions
  inside the same ±110 m study box (`series_from_fcd`).
- A replay baseline (ADR-022: the observed timeline and vehicles replayed over the clip window
  [start, end), `replay_window`) is measured over that window on both sides: observed clip
  seconds [start, end) against simulated [0, end - start) (`comparison_windows`, `clip_series`,
  `clip_timeline`), and its vehicles can be paired with the tracks they replay (`paired_lags`).

Added here, the same way for both sides:
- Discharge after each red (`discharge_records`): the vehicles queued in a lane at the end of red,
  in stop-line crossing order. Headway k is the k-th crossing time less the (k-1)-th (k >= 2); a
  queued vehicle not across before the following yellow ends is residual.
- A fixed segment (`segment_records`): from an inbound line on the approach arm to the exit line,
  with the speed at every whole second inside it (centred 1 s window, the world model's speed
  rule) and its stops (below `stopped_m_s`, counted again only after exceeding `restart_m_s`).
- Intervals: Wilson for shares, bootstrap over clusters (red ends, cycles) or blocks of a time
  series for means, and the spread over seeds for simulated values.

Nothing here scores agreement; the caller states per quantity whether the sides are comparable.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.evidence import (
    ARM_ORDER,
    HEAD_ORDER,
    Arm,
    FrameRef,
    Gate,
    IntersectionGeometry,
    LanePolygon,
    MovementCount,
    QueueSample,
    SignalHead,
    SignalInterval,
    SignalState,
    SignalTimeline,
    Turn,
)
from cairoflow_perception.ngsim_junction import arm_coords, arm_point
from cairoflow_perception.world_model import GroundSeries

Point = tuple[float, float]
Label = Literal["observed", "derived", "simulated", "assumed"]
Status = Literal["comparable", "comparable-with-caveats", "not comparable"]
CLASS_OF_VTYPE = {"passenger": "car", "truck": "truck", "bus": "bus", "motorcycle": "motorcycle"}


def new_output_dir(path: Path) -> Path:
    """Create `path` and its parents; refuse an existing path: a validation is never overwritten."""
    if path.exists():
        raise FileExistsError(f"{path} exists; a validation is never overwritten")
    path.mkdir(parents=True)
    return path


def tripinfo_sha256_without_device(text: str, device: str = "fcd") -> tuple[str, int]:
    """SHA-256 of tripinfo XML as `network.content_sha256(strip_comments=True)` hashes it, after
    removing the `<device>_<trip id>` entry an extra output adds to each trip's `devices` list;
    and how many entries were removed.

    Asking SUMO for FCD equips every vehicle with an 'fcd' device, which tripinfo lists; nothing
    else in the file changes when the run is the same. Only an entry naming its own trip is removed.
    """
    text = re.sub(r"<!--.*?-->", "", text.replace("\r\n", "\n"), flags=re.DOTALL)
    pattern = re.compile(
        rf'(<tripinfo id="([^"]+)"[^>]*? devices="[^"]*?) {re.escape(device)}_\2(?=[ "])'
    )
    text, removed = pattern.subn(r"\1", text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), removed


# --- Intervals and summaries ----------------------------------------------------------------


class Spread(BaseModel):
    """Simulated values over seeds (one value per seed)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    n: int = Field(ge=0)
    mean: float | None
    sd: float | None = Field(description="sample SD (n - 1); None for fewer than two values")
    min: float | None
    max: float | None
    values: tuple[float, ...]


def spread(values: Iterable[float | None]) -> Spread:
    """Mean, sample SD and range of the finite values (the others are dropped)."""
    x = [float(v) for v in values if v is not None and math.isfinite(v)]
    return Spread(
        n=len(x),
        mean=statistics.fmean(x) if x else None,
        sd=statistics.stdev(x) if len(x) > 1 else None,
        min=min(x) if x else None,
        max=max(x) if x else None,
        values=tuple(x),
    )


def wilson_interval(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial share (counting noise only)."""
    if trials <= 0 or not 0 <= successes <= trials or not 0 < confidence < 1:
        raise ValueError("need 0 <= successes <= trials, trials > 0 and confidence in (0, 1)")
    z = statistics.NormalDist().inv_cdf(0.5 + confidence / 2)
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    # the bounds are exactly 0 and 1 at the ends; keep float noise out of them
    low = 0.0 if successes == 0 else max(0.0, centre - half)
    return low, 1.0 if successes == trials else min(1.0, centre + half)


def cluster_bootstrap_interval(
    clusters: Sequence[Sequence[float]],
    statistic: Callable[[np.ndarray], float] = lambda x: float(np.mean(x)),
    *,
    resamples: int = 2000,
    seed: int = 0,
    confidence: float = 0.95,
) -> tuple[float, float] | None:
    """Percentile interval of `statistic` over the pooled values, resampling whole clusters (e.g.
    red ends or cycles) with replacement; None without two non-empty clusters."""
    groups = [np.asarray(c, dtype=float) for c in clusters if len(c)]
    if len(groups) < 2:
        return None
    rng = np.random.default_rng(seed)
    stats = np.empty(resamples)
    for i in range(resamples):
        pick = rng.integers(0, len(groups), len(groups))
        stats[i] = statistic(np.concatenate([groups[j] for j in pick]))
    tail = (1 - confidence) / 2
    low, high = np.quantile(stats, [tail, 1 - tail])
    return float(low), float(high)


def block_bootstrap_interval(
    series: Sequence[float],
    block: int,
    *,
    resamples: int = 2000,
    seed: int = 0,
    confidence: float = 0.95,
) -> tuple[float, float] | None:
    """Percentile interval of a time series' mean by the moving-block bootstrap (blocks of `block`
    consecutive samples keep the dependence inside a cycle); None if under two blocks long."""
    x = np.asarray(series, dtype=float)
    if block < 1 or len(x) < 2 * block:
        return None
    starts, count = len(x) - block + 1, math.ceil(len(x) / block)
    rng = np.random.default_rng(seed)
    means = np.empty(resamples)
    for i in range(resamples):
        index = (rng.integers(0, starts, count)[:, None] + np.arange(block)).ravel()[: len(x)]
        means[i] = x[index].mean()
    tail = (1 - confidence) / 2
    low, high = np.quantile(means, [tail, 1 - tail])
    return float(low), float(high)


def describe(values: Sequence[float]) -> dict[str, float | int | None]:
    """n, mean, median, 15th and 85th percentile, min and max (None when empty)."""
    x = np.asarray(values, dtype=float)
    if not len(x):
        return dict.fromkeys(("mean", "median", "p15", "p85", "min", "max"), None) | {"n": 0}
    return {
        "n": len(x),
        "mean": float(x.mean()),
        "median": float(np.median(x)),
        "p15": float(np.quantile(x, 0.15)),
        "p85": float(np.quantile(x, 0.85)),
        "min": float(x.min()),
        "max": float(x.max()),
    }


# --- The simulated side's timeline and geometry -----------------------------------------------

PhaseStates = tuple[float, Mapping[Arm, SignalState], Mapping[Arm, SignalState]]


def program_timeline(
    phases: Sequence[PhaseStates],
    begin_s: float,
    window_s: float,
    *,
    timeline_id: str,
    clip_id: str,
    source: str,
    offset_s: float = 0.0,
) -> SignalTimeline:
    """What a fixed-time program shows over simulation time [begin_s, begin_s + window_s), as a
    timeline on [0, window_s).

    `phases` are (duration s, `all` head state per arm, `left` head state per arm) in program
    order; phase 0 starts at simulation time `offset_s` and the cycle repeats (SUMO's tlLogic with
    offset 0). Durations, begin, window and offset must be whole seconds, as the runner requires
    of a 1 s step; the states are then exact at every second. Label `assumed`: it is the program
    the simulation ran, not an observation.
    """
    durations = [float(d) for d, _, _ in phases]
    if not phases or any(
        abs(x - round(x)) > 1e-9 for x in (*durations, begin_s, window_s, offset_s)
    ):
        raise ValueError("phases, window and offset must be whole seconds")
    cycle = round(sum(durations))
    ends = list(np.cumsum(durations))
    heads = sorted(
        {(arm, "all") for _, states, _ in phases for arm in states}
        | {(arm, "left") for _, _, lefts in phases for arm in lefts},
        key=lambda h: (ARM_ORDER.index(h[0]), HEAD_ORDER.index(h[1])),
    )

    def state(arm: Arm, head: str, t: float) -> SignalState:
        index = bisect.bisect_right(ends, (t - offset_s) % cycle)
        _, states, lefts = phases[index]
        return (states if head == "all" else lefts)[arm]

    intervals: list[SignalInterval] = []
    for arm, head in heads:
        current, start = state(arm, head, begin_s), 0
        for second in range(1, round(window_s)):
            now = state(arm, head, begin_s + second)
            if now != current:
                intervals.append(_interval(arm, head, current, start, second))
                current, start = now, second
        intervals.append(_interval(arm, head, current, start, round(window_s)))
    return SignalTimeline(
        timeline_id=timeline_id,
        clip_id=clip_id,
        label="assumed",
        source=source,
        window_s=window_s,
        intervals=tuple(intervals),
    )


def _interval(arm: Arm, head: str, state: SignalState, a: float, b: float) -> SignalInterval:
    return SignalInterval(arm=arm, head=head, state=state, start_s=float(a), end_s=float(b))


# --- Replay baselines ---------------------------------------------------------------------------


def replay_window(parameters: Mapping[str, float]) -> tuple[float, float] | None:
    """A replay baseline's window in clip seconds, from its network parameters
    `signal.replay_start` (simulation time 0) and `signal.replay_end`; None for a baseline that
    repeats one cut cycle. One bound without the other, or an empty window, is an error."""
    start = parameters.get("signal.replay_start")
    end = parameters.get("signal.replay_end")
    if start is None and end is None:
        return None
    if start is None or end is None or not end > start >= 0:
        raise ValueError(f"replay window [{start}, {end}) s is incomplete or empty")
    return float(start), float(end)


def comparison_windows(
    replay: tuple[float, float] | None, warmup_s: float, horizon_s: float, observed_s: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """(observed window in clip seconds, simulated window in simulation seconds), equally long.

    A repeated-cycle baseline measures [warm-up, warm-up + horizon) against the whole observation
    [0, observed_s). A replay baseline measures the replay itself: observed [start, end) against
    simulated [0, end - start), so its run must have no warm-up and a horizon of end - start.
    """
    if replay is None:
        observed, simulated = (0.0, float(observed_s)), (float(warmup_s), warmup_s + horizon_s)
    else:
        if replay[1] > observed_s:
            raise ValueError(f"the replay window ends at {replay[1]:g} s, after the observation")
        if warmup_s != 0:
            raise ValueError("a replay baseline is measured from its start: warm-up must be 0")
        observed, simulated = replay, (0.0, float(horizon_s))
    if abs((observed[1] - observed[0]) - (simulated[1] - simulated[0])) > 1e-6:
        raise ValueError(f"windows of different length: observed {observed}, simulated {simulated}")
    return observed, simulated


def known_throughout(timeline: SignalTimeline, start: float, end: float) -> bool:
    """Whether every head of the timeline shows a known state throughout [start, end)."""
    inside = 0 <= start < end <= timeline.window_s
    return inside and all(
        i.state != "unknown" for i in timeline.intervals if i.start_s < end and i.end_s > start
    )


def clip_timeline(timeline: SignalTimeline, start: float, end: float) -> SignalTimeline:
    """The timeline over clip seconds [start, end), shifted to start at 0 (same clip, heads and
    label), as the observation of that window alone."""
    if not 0 <= start < end <= timeline.window_s:
        raise ValueError(f"[{start:g}, {end:g}) s is not inside the timeline's window")
    return SignalTimeline(
        timeline_id=f"{timeline.timeline_id}@{start:g}-{end:g}",
        clip_id=timeline.clip_id,
        label=timeline.label,
        source=f"{timeline.source}; clipped to [{start:g}, {end:g}) s and shifted to 0",
        annotation_sha256=timeline.annotation_sha256,
        window_s=round(end - start, 6),
        intervals=tuple(
            SignalInterval(
                arm=i.arm,
                head=i.head,
                state=i.state,
                start_s=round(max(i.start_s, start) - start, 6),
                end_s=round(min(i.end_s, end) - start, 6),
            )
            for i in timeline.intervals
            if i.start_s < end and i.end_s > start
        ),
    )


def clip_series(
    series: Sequence[GroundSeries], start: float, end: float, frame_s: float
) -> list[GroundSeries]:
    """Ground series restricted to [start, end) and shifted to start at 0, as if observed in that
    window alone (frame indices shift by start / frame_s); series with no point inside go."""
    shift = round(start / frame_s)
    out = []
    for s in series:
        keep = [k for k, f in enumerate(s.frames) if start <= f.timestamp_s < end]
        if not keep:
            continue
        out.append(
            GroundSeries(
                track_id=s.track_id,
                class_label=s.class_label,
                frames=tuple(
                    FrameRef(
                        clip_id=s.frames[k].clip_id,
                        frame_index=s.frames[k].frame_index - shift,
                        timestamp_s=round(s.frames[k].timestamp_s - start, 6),
                    )
                    for k in keep
                ),
                points_m=tuple(s.points_m[k] for k in keep),
                errors_m=tuple(s.errors_m[k] for k in keep),
            )
        )
    return out


def paired_lags(
    observed: Sequence[MovementCount],
    simulated: Sequence[MovementCount],
    track_of_vehicle: Mapping[str, str],
) -> dict[str, list[float]]:
    """Per '<approach> <turn>' (the simulated movement), for every vehicle counted on both sides
    as the same observed track (`track_of_vehicle`: SUMO vehicle id -> observed track id), its
    simulated stop-line crossing time less its observed one (s, both in window time)."""
    seen = {t.track_id: t.stop_line_time_s for m in observed for t in m.tracks}
    out: dict[str, list[float]] = defaultdict(list)
    for m in simulated:
        for t in m.tracks:
            track = track_of_vehicle.get(t.track_id.split(":", 1)[-1])
            if track in seen:
                out[f"{m.approach} {m.turn}"].append(round(t.stop_line_time_s - seen[track], 6))
    return dict(out)


def pooled_heads(
    distance_m: Sequence[float], lane: Sequence, time_s: Sequence[float]
) -> np.ndarray:
    """Per lane and time step, the distance from the centre of the most advanced (nearest) stopped
    vehicle: the pooled queue heads of `ngsim_junction.arm_statistics`."""
    d, lanes, t = np.asarray(distance_m, dtype=float), np.asarray(lane), np.asarray(time_s)
    order = np.lexsort((d, t, lanes))
    d, lanes, t = d[order], lanes[order], t[order]
    first = np.r_[True, (lanes[1:] != lanes[:-1]) | (t[1:] != t[:-1])]
    return d[first]


def stop_gate_rule(
    heads_m: Sequence[float],
    head_quantile: float = 0.9,
    margin_m: float = 0.5,
    min_samples: int = 50,
) -> float:
    """The observed counting line's distance rule (JunctionPolicy `head_quantile`, `gate_margin_m`,
    `min_head_samples`): the most advanced `head_quantile` of the pooled queue heads, that is
    their `1 - head_quantile` quantile of distance, less `margin_m`."""
    heads = np.asarray(heads_m, dtype=float)
    if len(heads) < min_samples:
        raise ValueError(f"{len(heads)} pooled queue heads; the rule needs {min_samples}")
    return round(float(np.quantile(heads, 1 - head_quantile)) - margin_m, 3)


@dataclass(frozen=True)
class SimLane:
    """One simulated lane on an arm, across it: offsets to the right of inbound travel (metres;
    exit lanes lie on the left, so theirs are negative). Approach lanes carry the NGSIM lane id
    they were compiled from."""

    lane_id: str
    arm: Arm
    direction: Literal["approach", "exit"]
    inner_m: float
    outer_m: float


def gate_distance_m(gate: Gate) -> float:
    """Distance of a gate from the centre along its arm (its midpoint)."""
    (ax, ay), (bx, by) = gate.segment_m
    along, _ = arm_coords((ax + bx) / 2, (ay + by) / 2, gate.arm)
    return float(along)


def observed_depth_m(geometry: IntersectionGeometry) -> dict[str, float]:
    """Per approach lane, how far beyond its arm's stop line the lane is observed (metres)."""
    stops = {g.arm: gate_distance_m(g) for g in geometry.gates if g.role == "stop_line"}
    depth: dict[str, float] = {}
    for lane in geometry.lanes:
        if lane.direction == "approach":
            along, _ = arm_coords(
                [p[0] for p in lane.polygon_m], [p[1] for p in lane.polygon_m], lane.arm
            )
            depth[lane.lane_id] = round(float(np.max(along)) - stops[lane.arm], 3)
    return depth


def sim_geometry(
    observed: IntersectionGeometry,
    lanes: Sequence[SimLane],
    stop_gate_m: Mapping[Arm, float],
    exit_gate_m: Mapping[Arm, float],
    *,
    geometry_id: str,
    clip_id: str,
    ground_source_sha256: str,
    depth_m: Mapping[str, float] | None = None,
    pad_m: float = 0.5,
) -> IntersectionGeometry:
    """The simulated junction as an `IntersectionGeometry` the world model can measure.

    Each simulated approach lane becomes a polygon from its arm's stop line (`stop_gate_m`) to
    `depth_m` beyond it (default: the observed lane's depth, so queues are censored alike), with
    the observed lane's allowed turns. Stop lines span the arm's approach lanes and exit lines,
    at `exit_gate_m`, its exit lanes, `pad_m` wider on each side.
    """
    depth = observed_depth_m(observed) if depth_m is None else dict(depth_m)
    seen = {lane.lane_id: lane for lane in observed.lanes if lane.direction == "approach"}
    polygons: list[LanePolygon] = []
    gates: list[Gate] = []
    for arm in ARM_ORDER:
        approach = [x for x in lanes if x.arm == arm and x.direction == "approach"]
        if approach:
            near = stop_gate_m[arm]
            for lane in approach:
                drawn = seen.get(lane.lane_id)
                if drawn is None or drawn.arm != arm:
                    raise ValueError(f"{lane.lane_id}: no observed approach lane on arm {arm}")
                far = near + depth[lane.lane_id]
                polygons.append(
                    LanePolygon(
                        lane_id=lane.lane_id,
                        arm=arm,
                        direction="approach",
                        polygon_m=(
                            arm_point(arm, near, lane.inner_m),
                            arm_point(arm, far, lane.inner_m),
                            arm_point(arm, far, lane.outer_m),
                            arm_point(arm, near, lane.outer_m),
                        ),
                        allowed_turns=drawn.allowed_turns,
                        provenance="synthetic",
                        source=(
                            f"simulated lane {lane.inner_m:.2f} to {lane.outer_m:.2f} m right of "
                            f"inbound travel, {near:.2f} to {far:.2f} m from the centre (the "
                            f"observed depth); turns of NGSIM lane {lane.lane_id}"
                        ),
                    )
                )
            low = min(x.inner_m for x in approach) - pad_m
            high = max(x.outer_m for x in approach) + pad_m
            gates.append(
                Gate(
                    arm=arm,
                    role="stop_line",
                    segment_m=(arm_point(arm, near, low), arm_point(arm, near, high)),
                    provenance="synthetic",
                    source=f"simulated counting line {near:.3f} m from the centre (observed rule)",
                )
            )
        exits = [x for x in lanes if x.arm == arm and x.direction == "exit"]
        if exits and arm in exit_gate_m:
            low = min(min(x.inner_m, x.outer_m) for x in exits) - pad_m
            high = max(max(x.inner_m, x.outer_m) for x in exits) + pad_m
            d = exit_gate_m[arm]
            gates.append(
                Gate(
                    arm=arm,
                    role="exit_line",
                    segment_m=(arm_point(arm, d, low), arm_point(arm, d, high)),
                    provenance="synthetic",
                    source=f"exit line {d:.3f} m from the centre (the observed distance)",
                )
            )
    return IntersectionGeometry(
        geometry_id=geometry_id,
        clip_id=clip_id,
        ground_source_sha256=ground_source_sha256,
        lanes=tuple(polygons),
        gates=tuple(gates),
    )


FcdSample = tuple[float, str, str, float, float]  # time s, vehicle id, vType, x m, y m


def series_from_fcd(
    samples: Iterable[FcdSample],
    *,
    begin_s: float,
    window_s: float,
    clip_id: str,
    step_s: float = 1.0,
    box_half_m: float | None = None,
) -> list[GroundSeries]:
    """Simulated vehicles as ground series, built like `ngsim_junction.ground_series`.

    Keeps positions in [begin_s, begin_s + window_s), shifted to start at 0, and inside the study
    box (|x| and |y| at most `box_half_m`) if one is given; a missing step starts a new run. Track
    ids are '<class>:<vehicle id>' (class from the SUMO vType), with '.<k>' for a vehicle's later
    runs. SUMO positions (front bumper centre, like NGSIM's) carry no position error.
    """
    rows: dict[str, list[tuple[float, str, float, float]]] = defaultdict(list)
    for t, vehicle, vtype, x, y in samples:
        if not begin_s <= t < begin_s + window_s:
            continue
        if box_half_m is not None and (abs(x) > box_half_m or abs(y) > box_half_m):
            continue
        rows[vehicle].append((t, vtype, x, y))
    out: list[GroundSeries] = []
    for vehicle, points in rows.items():
        points.sort()
        runs = [[points[0]]]
        for p in points[1:]:
            if p[0] - runs[-1][-1][0] > step_s + 1e-6:
                runs.append([p])
            else:
                runs[-1].append(p)
        for k, run in enumerate(runs):
            label = CLASS_OF_VTYPE.get(run[0][1], run[0][1])
            out.append(
                GroundSeries(
                    track_id=f"{label}:{vehicle}{'' if k == 0 else f'.{k}'}",
                    class_label=label,
                    frames=tuple(
                        FrameRef(
                            clip_id=clip_id,
                            frame_index=round((t - begin_s) / step_s),
                            timestamp_s=round(t - begin_s, 6),
                        )
                        for t, *_ in run
                    ),
                    points_m=tuple((round(x, 4), round(y, 4)) for _, _, x, y in run),
                    errors_m=(0.0,) * len(run),
                )
            )
    return sorted(out, key=lambda s: s.track_id)


# --- Measurements on world-model outputs ------------------------------------------------------


def movement_counts(movements: Sequence[MovementCount]) -> dict[str, int]:
    """Counted vehicles per '<approach> <turn>' (U-turns left out: the network has none)."""
    return {f"{m.approach} {m.turn}": m.count_veh for m in movements if m.turn != "u_turn"}


def lane_use(movements: Sequence[MovementCount], by_turn: bool = False) -> dict[str, Counter[str]]:
    """Counted vehicles per approach lane just before the stop line ('unknown' if in none), keyed
    by approach arm, or by '<arm> <turn>'."""
    out: dict[str, Counter[str]] = defaultdict(Counter)
    for m in movements:
        if m.turn == "u_turn":
            continue
        key = f"{m.approach} {m.turn}" if by_turn else m.approach
        for track in m.tracks:
            out[key][track.lane_id or "unknown"] += 1
    return dict(out)


def arm_queue_series(
    queues: Sequence[QueueSample],
) -> dict[Arm, list[tuple[float, float, int, bool]]]:
    """Per arm and sampled second, its longest lane queue: (time s, length m, queued vehicles,
    censored); on equal length the later lane wins, as in the record's observed metrics."""
    best: dict[Arm, dict[float, tuple[float, int, bool]]] = defaultdict(dict)
    for q in queues:
        t = q.frame.timestamp_s
        if q.length_m >= best[q.arm].get(t, (0.0, 0, False))[0]:
            best[q.arm][t] = (q.length_m, q.queued_veh, q.censored)
    return {arm: [(t, *v) for t, v in sorted(rows.items())] for arm, rows in best.items()}


def queue_spacings_m(
    samples: Iterable[QueueSample], series: Sequence[GroundSeries]
) -> list[list[float]]:
    """Per queue sample of two or more vehicles, the front-to-front spacing (m along the arm)
    between consecutive queued vehicles at the sample's time: the jam spacing, vehicle length
    plus gap (one list per sample, a cluster for the bootstrap)."""
    by_id = {s.track_id: s for s in series}
    out = []
    for q in samples:
        if q.queued_veh < 2:
            continue
        distances = []
        for track in q.track_ids:
            s = by_id[track]
            x, y = _position_at([f.timestamp_s for f in s.frames], s.points_m, q.frame.timestamp_s)
            distances.append(float(arm_coords(x, y, q.arm)[0]))
        distances.sort()
        out.append([round(b - a, 6) for a, b in zip(distances, distances[1:], strict=False)])
    return out


def follows_head(lane: LanePolygon, left_arms: set[Arm]) -> SignalHead:
    """The head a lane's queue waits for (the world model's rule): left-only lanes of an arm with a
    protected-left head follow it; every other lane follows the arm's `all` head."""
    return "left" if lane.arm in left_arms and set(lane.allowed_turns) == {"left"} else "all"


def service_windows(
    timeline: SignalTimeline, arm: Arm, head: SignalHead = "all"
) -> list[tuple[float, float]]:
    """Each green that follows a red, from its start to the end of the yellow after it (greens
    whose start or end is not known in the timeline are left out)."""
    own = [i for i in timeline.intervals if i.arm == arm and i.head == head]
    out = []
    for k in range(1, len(own) - 1):
        before, green, after = own[k - 1], own[k], own[k + 1]
        if before.state == "red" and green.state == "green" and after.state == "yellow":
            out.append((green.start_s, after.end_s))
    return out


@dataclass(frozen=True)
class DischargeRecord:
    """How one lane's end-of-red queue crossed the stop line in the green that followed."""

    arm: Arm
    head: SignalHead
    lane_id: str
    green_s: float
    service_end_s: float
    queued_veh: int
    censored: bool
    crossings_s: tuple[float, ...]  # queued vehicles across within the service window, in order
    headways_s: tuple[tuple[int, float], ...]  # (queue position k >= 2, crossing gap s)

    @property
    def residual_veh(self) -> int:
        """Queued at the end of red but not across before the following yellow ended."""
        return self.queued_veh - len(self.crossings_s)


def discharge_records(
    end_of_red: Sequence[QueueSample],
    movements: Sequence[MovementCount],
    timeline: SignalTimeline,
    geometry: IntersectionGeometry,
    *,
    max_lead_s: float = 1.0,
) -> list[DischargeRecord]:
    """Per end-of-red queue sample (taken at most `max_lead_s` before the green), the stop-line
    crossings of its queued vehicles inside the green and yellow that follow, in crossing order."""
    crossing = {t.track_id: t.stop_line_time_s for m in movements for t in m.tracks}
    lanes = {x.lane_id: x for x in geometry.lanes if x.direction == "approach"}
    left_arms = {arm for arm, head in timeline.heads() if head == "left"}
    windows: dict[tuple[Arm, SignalHead], list[tuple[float, float]]] = {}
    out = []
    for q in end_of_red:
        head = follows_head(lanes[q.lane_id], left_arms)
        if (q.arm, head) not in windows:
            windows[(q.arm, head)] = service_windows(timeline, q.arm, head)
        t = q.frame.timestamp_s
        window = next(
            (w for w in windows[(q.arm, head)] if t < w[0] <= t + max_lead_s + 1e-9), None
        )
        if window is None:
            continue
        times = sorted(
            c
            for c in (crossing.get(track) for track in q.track_ids)
            if c is not None and window[0] <= c < window[1]
        )
        out.append(
            DischargeRecord(
                arm=q.arm,
                head=head,
                lane_id=q.lane_id,
                green_s=window[0],
                service_end_s=window[1],
                queued_veh=q.queued_veh,
                censored=q.censored,
                crossings_s=tuple(times),
                headways_s=tuple(
                    (j + 2, round(times[j + 1] - times[j], 6)) for j in range(len(times) - 1)
                ),
            )
        )
    return out


def headways_from(
    records: Iterable[DischargeRecord], min_position: int, max_position: int | None = None
) -> list[list[float]]:
    """Per record (a cluster for the bootstrap), its headways at queue positions in range."""
    return [
        [
            h
            for k, h in r.headways_s
            if k >= min_position and (max_position is None or k <= max_position)
        ]
        for r in records
    ]


def discharge_per_green(
    movements: Sequence[MovementCount], timeline: SignalTimeline
) -> dict[Arm, list[tuple[float, float, int]]]:
    """Per arm, each service window of its `all` head as (green start s, service end s, stop-line
    crossings inside it by the movements that head controls: not left turns where the arm has a
    protected-left head)."""
    left_arms = {arm for arm, head in timeline.heads() if head == "left"}
    out: dict[Arm, list[tuple[float, float, int]]] = {}
    for arm in [a for a, head in timeline.heads() if head == "all"]:
        times = [
            t.stop_line_time_s
            for m in movements
            if m.approach == arm and not (m.turn == "left" and arm in left_arms)
            for t in m.tracks
        ]
        out[arm] = [
            (a, b, sum(a <= t < b for t in times)) for a, b in service_windows(timeline, arm)
        ]
    return out


# --- A fixed segment: travel time and stops ----------------------------------------------------


def _position_at(times: Sequence[float], points: Sequence[Point], t: float) -> Point:
    i = bisect.bisect_left(times, t)
    if times[i] == t:
        return points[i]
    f = (t - times[i - 1]) / (times[i] - times[i - 1])
    (x0, y0), (x1, y1) = points[i - 1], points[i]
    return x0 + f * (x1 - x0), y0 + f * (y1 - y0)


def centred_speed(
    times: Sequence[float], points: Sequence[Point], t: float, half_window_s: float
) -> float | None:
    """The world model's speed rule: distance between the interpolated positions `half_window_s`
    before and after `t` (clamped to the series), over the elapsed time; None where that span is
    shorter than `half_window_s`."""
    if not times:
        return None
    t0, t1 = max(t - half_window_s, times[0]), min(t + half_window_s, times[-1])
    if t1 - t0 < half_window_s - 1e-9:
        return None
    a, b = _position_at(times, points, t0), _position_at(times, points, t1)
    return math.dist(a, b) / (t1 - t0)


def inbound_crossing_s(
    times: Sequence[float], points: Sequence[Point], arm: Arm, distance_m: float
) -> float | None:
    """First time the series passes `distance_m` from the centre inbound along `arm`, linearly
    interpolated; None if it never does (it starts nearer, or never comes that far)."""
    along, _ = arm_coords([p[0] for p in points], [p[1] for p in points], arm)
    for i in range(len(times) - 1):
        if along[i] >= distance_m > along[i + 1]:
            f = (along[i] - distance_m) / (along[i] - along[i + 1])
            return times[i] + f * (times[i + 1] - times[i])
    return None


def count_stops(speeds: Iterable[float], stopped_m_s: float = 1.0, restart_m_s: float = 2.0) -> int:
    """Stops in a speed sequence: one begins below `stopped_m_s`; the next can begin only after the
    speed has exceeded `restart_m_s`, so creeping forward in a queue stays one stop."""
    stops, armed = 0, True
    for v in speeds:
        if armed and v < stopped_m_s:
            stops, armed = stops + 1, False
        elif not armed and v > restart_m_s:
            armed = True
    return stops


@dataclass(frozen=True)
class SegmentRecord:
    """One counted vehicle through the segment from the entry line to its exit-line crossing."""

    track_id: str
    approach: Arm
    turn: Turn
    entry_s: float
    exit_s: float
    stops: int
    stopped_s: float  # whole-second samples below the stopped speed, times the grid step
    samples: int

    @property
    def travel_time_s(self) -> float:
        return self.exit_s - self.entry_s


def segment_records(
    series: Sequence[GroundSeries],
    movements: Sequence[MovementCount],
    approaches: Iterable[Arm],
    entry_m: float,
    *,
    half_window_s: float = 0.5,
    stopped_m_s: float = 1.0,
    restart_m_s: float = 2.0,
    grid_s: float = 1.0,
) -> tuple[list[SegmentRecord], Counter[str]]:
    """Per counted vehicle from `approaches`: the segment from its inbound crossing of `entry_m` on
    its approach arm (before its stop-line crossing) to its exit-line crossing, with the speed at
    every whole `grid_s` inside it; and the vehicles left out, by reason."""
    keep = set(approaches)
    by_id = {s.track_id: s for s in series}
    out: list[SegmentRecord] = []
    skipped: Counter[str] = Counter()
    for m in movements:
        if m.approach not in keep or m.turn == "u_turn":
            continue
        for track in m.tracks:
            s = by_id[track.track_id]
            times = [f.timestamp_s for f in s.frames]
            entry = inbound_crossing_s(times, s.points_m, m.approach, entry_m)
            if entry is None or entry > track.stop_line_time_s:
                skipped[f"{m.approach} {m.turn}: no inbound crossing at {entry_m:g} m"] += 1
                continue
            first = math.ceil(entry / grid_s - 1e-9)
            last = math.floor(track.exit_line_time_s / grid_s + 1e-9)
            speeds = [
                v
                for v in (
                    centred_speed(times, s.points_m, k * grid_s, half_window_s)
                    for k in range(first, last + 1)
                )
                if v is not None
            ]
            out.append(
                SegmentRecord(
                    track_id=track.track_id,
                    approach=m.approach,
                    turn=m.turn,
                    entry_s=entry,
                    exit_s=track.exit_line_time_s,
                    stops=count_stops(speeds, stopped_m_s, restart_m_s),
                    stopped_s=grid_s * sum(v < stopped_m_s for v in speeds),
                    samples=len(speeds),
                )
            )
    return out, skipped


def present_at(series: Sequence[GroundSeries], t: float, tolerance_s: float) -> int:
    """Vehicles whose series spans time `t` (within `tolerance_s`)."""
    return sum(
        s.frames[0].timestamp_s <= t + tolerance_s and s.frames[-1].timestamp_s >= t - tolerance_s
        for s in series
    )


# --- The report's records ---------------------------------------------------------------------


class Source(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: str


class Side(BaseModel):
    """One side of a comparison: the value, how it was obtained and how uncertain it is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: Label
    value: float | None = Field(description="the observed estimate, or the mean over seeds")
    unit: str = Field(min_length=1)
    window: str = Field(min_length=1)
    n: int | None = Field(None, description="vehicles, samples or clusters behind the value")
    interval: tuple[float, float] | None = None
    interval_method: str | None = None
    lower_bound: bool = Field(False, description="censored: the value may be larger")
    spread: Spread | None = Field(None, description="simulated: per-seed values")
    sources: tuple[Source, ...] = ()
    note: str | None = None


class Comparison(BaseModel):
    """One quantity on both sides, with its status. No pass/fail: the status says whether the two
    values mean the same thing, not whether they agree."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    quantity: str
    unit: str
    method: str
    observed: Side
    simulated: Side
    discrepancy: float | None = Field(description="simulated minus observed, in `unit`")
    simulated_inside_observed_interval: bool | None
    observed_inside_seed_range: bool | None
    status: Status
    limitations: tuple[str, ...]
    evidence_needed: str | None = None


def compare(
    key: str,
    quantity: str,
    unit: str,
    method: str,
    observed: Side,
    simulated: Side,
    status: Status,
    limitations: Sequence[str],
    evidence_needed: str | None = None,
) -> Comparison:
    """A comparison with its discrepancy and the two overlap checks filled in."""
    obs, sim, seeds = observed.value, simulated.value, simulated.spread
    inside = None
    if obs is not None and sim is not None and observed.interval is not None:
        inside = observed.interval[0] <= sim <= observed.interval[1]
    in_range = None
    if obs is not None and seeds is not None and seeds.min is not None and seeds.max is not None:
        in_range = seeds.min <= obs <= seeds.max
    return Comparison(
        key=key,
        quantity=quantity,
        unit=unit,
        method=method,
        observed=observed,
        simulated=simulated,
        discrepancy=None if obs is None or sim is None else sim - obs,
        simulated_inside_observed_interval=inside,
        observed_inside_seed_range=in_range,
        status=status,
        limitations=tuple(limitations),
        evidence_needed=evidence_needed,
    )
