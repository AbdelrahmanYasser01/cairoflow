"""An online tracker for camera 2, an offline re-linking pass, and gap filling.

The footage sets the design: from this height a car is about 20 px long and moves up to about
6.6 px per frame at 10 fps, whatever its image row, so the noise is a fixed number of pixels rather
than a share of the box height (the near-nadir view has no perspective growth to scale by), and
association is by centroid, because a 20 px box moves a third of its length per frame and box
overlap fails on the fast ones. The same reason rules out appearance: there is almost none to use.

`OnlineTracker` runs a constant-velocity Kalman filter per track. Both image axes share one model
and one fixed noise, so one 2 x 2 covariance serves both, and the gate is a circle whose radius
follows the filter's own innovation variance: wide on a new track, tight on a settled one, widening
again while a track coasts. Each frame matches in the BYTE order (Zhang et al., ECCV 2022):
confirmed tracks take high-confidence detections, then the low-confidence ones keep what is left
alive, then tentative tracks take what high-confidence detections remain, and the rest start new
tentative tracks. A low-confidence detection never starts a track. Boxes whose centres coincide are
one vehicle seen twice, so only the best of them is used: the detector interface does not promise
non-maximum suppression, and a car counted twice is two tracks. A tentative track is confirmed
after `confirm_hits` hits and dropped at its first miss limit; a confirmed one coasts for
`max_miss_s` before it ends.

`relink` then joins fragments that are consistent in position, velocity and time, and `fill_gaps`
gives each track one row per frame, interpolating what was not observed and flagging it. Rows keep
the detector's measurement; the filter is only used to decide who is who.

Time-based parameters are in seconds, so a stride changes the frame interval and nothing else. Pure
numpy and standard library; the same input gives the same output (ties break on track id, then
detection order).
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

_BIG = 1e9
_MIN_DIRECTION_PX = 6.0  # net travel shorter than this gives no direction


@dataclass(frozen=True)
class TrackerParams:
    """Pixels and seconds of the image at camera 2's scale."""

    high_score: float = 0.35  # a detection at or above this may start a track
    low_score: float = 0.05  # below this it is ignored
    confirm_hits: int = 3  # hits before a tentative track is a track
    max_miss_s: float = 3.0  # a confirmed track coasts this long before it ends
    measurement_sigma_px: float = 2.0  # box-centre noise; fixed, not scaled by box height
    accel_sigma_px_s2: float = 25.0  # process noise, a car's acceleration in px/s^2
    max_speed_px_s: float = 66.0  # 6.6 px per frame at 10 fps
    gate_sigmas: float = 3.0  # gate radius in innovation standard deviations
    min_gate_px: float = 6.0
    max_gate_px: float = 30.0
    duplicate_px: float = 8.0  # box centres this close are one vehicle: under half a car width
    # re-linking (offline)
    max_gap_s: float = 3.0  # moving fragments further apart than this are not joined
    max_stopped_gap_s: float = 30.0  # a queued car can vanish for most of a red
    stopped_speed_px_s: float = 25.0  # both ends at or below this count as queued
    link_position_px: float = 10.0  # allowed miss of the constant-acceleration bridge
    link_position_px_per_s: float = 5.0  # and how it widens with the gap, for moving ends only
    link_speed_px_s: float = 20.0  # allowed velocity change across a gap
    link_accel_px_s2: float = 25.0  # and how it widens with the time between the two fits
    fit_points: int = 8  # end-velocity fit uses this many observed points at most
    queue_creep_px: float = 25.0  # a queued car can move up this far while hidden: under a spacing
    queue_lateral_px: float = 8.0  # staying this close to its line of travel

    def as_parameters(self) -> dict[str, int | float]:
        return dict(vars(self))


@dataclass(frozen=True)
class Fragment:
    """A confirmed track: observed rows only, in frame order."""

    track_id: int
    frames: np.ndarray  # int, strictly increasing
    x: np.ndarray  # box centre, px
    y: np.ndarray
    w: np.ndarray  # box size, px
    h: np.ndarray
    conf: np.ndarray  # detector score, 0..1
    labels: dict[str, float] = field(default_factory=dict)  # label -> summed score

    @property
    def start(self) -> int:
        return int(self.frames[0])

    @property
    def end(self) -> int:
        return int(self.frames[-1])


@dataclass(frozen=True)
class FilledTrack:
    """One row per frame from first to last observation; `interpolated` marks the filled ones."""

    track_id: int
    start_frame: int
    x: np.ndarray
    y: np.ndarray
    w: np.ndarray
    h: np.ndarray
    conf: np.ndarray
    interpolated: np.ndarray  # bool
    labels: dict[str, float]

    def __len__(self) -> int:
        return len(self.x)


def assign(cost: np.ndarray) -> list[tuple[int, int]]:
    """Minimum-cost assignment of rows to columns (Hungarian, O(n^2 m)); every row of the smaller
    side is assigned, so the caller drops pairs at _BIG."""
    n_rows, n_cols = cost.shape
    if n_rows == 0 or n_cols == 0:
        return []
    if n_rows > n_cols:
        return [(r, c) for c, r in assign(cost.T)]
    u = np.zeros(n_rows + 1)
    v = np.zeros(n_cols + 1)
    match = np.zeros(n_cols + 1, dtype=int)  # column -> row, 1-based, 0 = free
    way = np.zeros(n_cols + 1, dtype=int)
    for row in range(1, n_rows + 1):
        match[0] = row
        col = 0
        best = np.full(n_cols + 1, np.inf)
        used = np.zeros(n_cols + 1, dtype=bool)
        while True:
            used[col] = True
            current = match[col]
            free = ~used[1:]
            reduced = cost[current - 1] - u[current] - v[1:]
            better = free & (reduced < best[1:])
            best[1:][better] = reduced[better]
            way[1:][better] = col
            masked = np.where(free, best[1:], np.inf)
            nxt = int(np.argmin(masked)) + 1
            delta = masked[nxt - 1]
            u[match[used]] += delta
            v[used] -= delta
            best[1:][free] -= delta
            col = nxt
            if match[col] == 0:
                break
        while col:
            match[col] = match[way[col]]
            col = way[col]
    return sorted((int(match[c]) - 1, c - 1) for c in range(1, n_cols + 1) if match[c])


def _match(
    tracks: Sequence[_Live], det_xy: np.ndarray, dets: Sequence[int], params: TrackerParams
) -> tuple[list[tuple[_Live, int]], list[_Live], list[int]]:
    """Gated minimum-distance matching of `tracks` to the detections numbered in `dets`.

    Connected groups of the gate graph are solved separately, which is exact and keeps each solve
    tiny. Returns (pairs, tracks left over, detections left over)."""
    if not len(tracks) or not len(dets):
        return [], list(tracks), list(dets)
    points = det_xy[np.asarray(dets)]
    predicted = np.array([[t.px, t.py] for t in tracks])
    gates = np.array([t.gate_px(params) for t in tracks])
    distance = np.hypot(*(predicted[:, None, :] - points[None, :, :]).transpose(2, 0, 1))
    allowed = distance <= gates[:, None]
    group = list(range(len(tracks) + len(dets)))

    def find(i: int) -> int:
        while group[i] != i:
            group[i] = group[group[i]]
            i = group[i]
        return i

    for r, c in zip(*np.nonzero(allowed), strict=True):
        group[find(int(r))] = find(len(tracks) + int(c))
    members: dict[int, tuple[list[int], list[int]]] = {}
    for r in range(len(tracks)):
        if allowed[r].any():
            members.setdefault(find(r), ([], []))[0].append(r)
    for c in range(len(dets)):
        if allowed[:, c].any():
            members.setdefault(find(len(tracks) + c), ([], []))[1].append(c)
    pairs: list[tuple[_Live, int]] = []
    taken_rows: set[int] = set()
    taken_cols: set[int] = set()
    for rows, cols in members.values():
        block = np.where(allowed[np.ix_(rows, cols)], distance[np.ix_(rows, cols)], _BIG)
        for r, c in assign(block):
            if block[r, c] < _BIG:
                pairs.append((tracks[rows[r]], dets[cols[c]]))
                taken_rows.add(rows[r])
                taken_cols.add(cols[c])
    left_tracks = [t for i, t in enumerate(tracks) if i not in taken_rows]
    left_dets = [d for i, d in enumerate(dets) if i not in taken_cols]
    return pairs, left_tracks, left_dets


class _Live:
    """A track being followed: filter state and the observed rows so far."""

    __slots__ = (
        "track_id", "px", "py", "vx", "vy", "p00", "p01", "p11", "hits", "confirmed", "last_seen",
        "frames", "xs", "ys", "ws", "hs", "confs", "labels",
    )  # fmt: skip

    def __init__(self, track_id: int, frame: int, row: Sequence[Any], params: TrackerParams):
        self.track_id = track_id
        self.px, self.py = _centre(row)
        self.vx = self.vy = 0.0
        self.p00 = params.measurement_sigma_px**2
        self.p01 = 0.0
        self.p11 = (params.max_speed_px_s / 2) ** 2
        self.hits = 0
        self.confirmed = False
        self.last_seen = frame
        self.frames: list[int] = []
        self.xs: list[float] = []
        self.ys: list[float] = []
        self.ws: list[float] = []
        self.hs: list[float] = []
        self.confs: list[float] = []
        self.labels: dict[str, float] = {}
        self._record(frame, row, params)

    def _record(self, frame: int, row: Sequence[Any], params: TrackerParams) -> None:
        cx, cy = _centre(row)
        self.frames.append(frame)
        self.xs.append(cx)
        self.ys.append(cy)
        self.ws.append(float(row[2]) - float(row[0]))
        self.hs.append(float(row[3]) - float(row[1]))
        score = float(row[4])
        self.confs.append(score)
        label = str(row[5])
        self.labels[label] = self.labels.get(label, 0.0) + score
        self.last_seen = frame
        self.hits += 1
        self.confirmed = self.confirmed or self.hits >= params.confirm_hits

    def predict(self, dt_s: float, params: TrackerParams) -> None:
        self.px += self.vx * dt_s
        self.py += self.vy * dt_s
        q = params.accel_sigma_px_s2**2
        p00 = self.p00 + 2 * dt_s * self.p01 + dt_s * dt_s * self.p11 + q * dt_s**4 / 4
        p01 = self.p01 + dt_s * self.p11 + q * dt_s**3 / 2
        p11 = self.p11 + q * dt_s**2
        self.p00, self.p01, self.p11 = p00, p01, p11

    def gate_px(self, params: TrackerParams) -> float:
        innovation = self.p00 + params.measurement_sigma_px**2
        radius = params.gate_sigmas * math.sqrt(innovation)
        return min(max(radius, params.min_gate_px), params.max_gate_px)

    def correct(self, frame: int, row: Sequence[Any], params: TrackerParams) -> None:
        cx, cy = _centre(row)
        innovation = self.p00 + params.measurement_sigma_px**2
        k0, k1 = self.p00 / innovation, self.p01 / innovation
        rx, ry = cx - self.px, cy - self.py
        self.px += k0 * rx
        self.py += k0 * ry
        self.vx += k1 * rx
        self.vy += k1 * ry
        p00 = (1 - k0) * self.p00
        p01 = (1 - k0) * self.p01
        p11 = self.p11 - k1 * self.p01
        self.p00, self.p01, self.p11 = p00, p01, p11
        limit = params.max_speed_px_s * 1.5
        speed = math.hypot(self.vx, self.vy)
        if speed > limit:
            self.vx *= limit / speed
            self.vy *= limit / speed
        self._record(frame, row, params)

    def fragment(self) -> Fragment:
        return Fragment(
            self.track_id,
            np.asarray(self.frames, dtype=np.int64),
            np.asarray(self.xs),
            np.asarray(self.ys),
            np.asarray(self.ws),
            np.asarray(self.hs),
            np.asarray(self.confs),
            dict(self.labels),
        )


def _centre(row: Sequence[Any]) -> tuple[float, float]:
    return (float(row[0]) + float(row[2])) / 2, (float(row[1]) + float(row[3])) / 2


def drop_duplicates(rows: Sequence[Sequence[Any]], within_px: float) -> list[Sequence[Any]]:
    """Greedy suppression on centre distance: the best box of each coincident group, in the order
    the detector gave them. Ties break on that order, so the result is deterministic."""
    if len(rows) < 2:
        return list(rows)
    centres = np.array([_centre(r) for r in rows])
    order = sorted(range(len(rows)), key=lambda i: (-float(rows[i][4]), i))
    kept: list[int] = []
    for i in order:
        if all(np.hypot(*(centres[i] - centres[k])) > within_px for k in kept):
            kept.append(i)
    return [rows[i] for i in sorted(kept)]


class OnlineTracker:
    """Feed `update` one frame at a time, in frame order; `finish` returns the fragments.

    `frame_dt_s` is the time between consecutive analysis frames. A hole in the frame numbers (an
    unreadable frame) is simply a longer prediction.
    """

    def __init__(self, frame_dt_s: float, params: TrackerParams | None = None) -> None:
        if frame_dt_s <= 0:
            raise ValueError("frame_dt_s must be positive")
        self.params = params or TrackerParams()
        self.frame_dt_s = frame_dt_s
        self._live: list[_Live] = []
        self._ended: list[Fragment] = []
        self._next_id = 0
        self._last_frame: int | None = None
        self.detections_seen = 0

    @property
    def active(self) -> list[_Live]:
        """Confirmed tracks seen on the latest frame or still coasting."""
        return [t for t in self._live if t.confirmed]

    def current_boxes(self) -> list[tuple[int, float, float, float, float]]:
        """(track_id, x1, y1, x2, y2) of confirmed tracks seen on the latest frame."""
        out = []
        for t in self.active:
            if t.last_seen == self._last_frame:
                x, y, w, h = t.xs[-1], t.ys[-1], t.ws[-1], t.hs[-1]
                out.append((t.track_id, x - w / 2, y - h / 2, x + w / 2, y + h / 2))
        return out

    def update(self, frame: int, detections: Iterable[Sequence[Any]]) -> None:
        if self._last_frame is not None and frame <= self._last_frame:
            raise ValueError(f"frame {frame} is not after frame {self._last_frame}")
        params = self.params
        steps = 1 if self._last_frame is None else frame - self._last_frame
        self._last_frame = frame
        rows = [d for d in detections if float(d[4]) >= params.low_score]
        rows = drop_duplicates(rows, params.duplicate_px)
        self.detections_seen += len(rows)
        for track in self._live:
            track.predict(steps * self.frame_dt_s, params)
        det_xy = np.array([_centre(r) for r in rows]).reshape(-1, 2)
        high = [i for i, r in enumerate(rows) if float(r[4]) >= params.high_score]
        low = [i for i, r in enumerate(rows) if float(r[4]) < params.high_score]
        confirmed = [t for t in self._live if t.confirmed]
        tentative = [t for t in self._live if not t.confirmed]

        pairs, left_tracks, left_high = _match(confirmed, det_xy, high, params)
        low_pairs, _, _ = _match(left_tracks, det_xy, low, params)
        tent_pairs, _, new_dets = _match(tentative, det_xy, left_high, params)
        for track, det in (*pairs, *low_pairs, *tent_pairs):
            track.correct(frame, rows[det], params)
        for det in new_dets:
            self._live.append(_Live(self._next_id, frame, rows[det], params))
            self._next_id += 1

        keep = []
        limit = params.max_miss_s / self.frame_dt_s
        for track in self._live:
            missed = frame - track.last_seen
            if track.confirmed and missed <= limit:
                keep.append(track)
            elif not track.confirmed and missed == 0:
                keep.append(track)  # a tentative track gets no coasting
            elif track.confirmed:
                self._ended.append(track.fragment())
        self._live = keep

    def finish(self) -> list[Fragment]:
        """End every track. Tentative tracks that were never confirmed are dropped."""
        self._ended.extend(t.fragment() for t in self._live if t.confirmed)
        self._live = []
        return sorted(self._ended, key=lambda f: f.track_id)


def _end_velocity(frag: Fragment, n: int, *, at_end: bool, dt_s: float) -> tuple[float, float]:
    """Least-squares velocity (px/s) over the last or first `n` observed points."""
    part = slice(-n, None) if at_end else slice(0, n)
    t = frag.frames[part].astype(float) * dt_s
    if len(t) < 2:
        return 0.0, 0.0
    t = t - t.mean()
    denom = float(t @ t)
    return float(t @ frag.x[part]) / denom, float(t @ frag.y[part]) / denom


def _join(a: Fragment, b: Fragment) -> Fragment:
    labels = dict(a.labels)
    for label, score in b.labels.items():
        labels[label] = labels.get(label, 0.0) + score
    return Fragment(
        min(a.track_id, b.track_id),
        np.concatenate([a.frames, b.frames]),
        np.concatenate([a.x, b.x]),
        np.concatenate([a.y, b.y]),
        np.concatenate([a.w, b.w]),
        np.concatenate([a.h, b.h]),
        np.concatenate([a.conf, b.conf]),
        labels,
    )


def _travel_direction(a: Fragment, b: Fragment) -> tuple[float, float] | None:
    """Unit vector of where this vehicle was going: `a`'s net travel, else `b`'s."""
    for dx, dy in (
        (float(a.x[-1] - a.x[0]), float(a.y[-1] - a.y[0])),
        (float(b.x[-1] - b.x[0]), float(b.y[-1] - b.y[0])),
    ):
        length = math.hypot(dx, dy)
        if length >= _MIN_DIRECTION_PX:
            return dx / length, dy / length
    return None


def _queue_cost(
    a: Fragment, b: Fragment, gap_s: float, moved: tuple[float, float], params: TrackerParams
) -> float | None:
    """A car in a slow queue, hidden while it stopped, reappears further up its own line. Both ends
    are slow, so velocity says nothing; the geometry does: forward of where it vanished, on the same
    line of travel, and by less than one car spacing (about 29 px), because a longer move is as
    likely to be the next car in line."""
    direction = _travel_direction(a, b)
    if direction is None:
        forward, lateral = 0.0, math.hypot(*moved)
    else:
        forward = moved[0] * direction[0] + moved[1] * direction[1]
        lateral = abs(moved[0] * direction[1] - moved[1] * direction[0])
    if lateral > params.queue_lateral_px:
        return None
    if not -params.queue_lateral_px <= forward <= params.queue_creep_px:
        return None
    # always dearer than a good bridge: only what the bridge could not explain falls through to here
    return 2.0 + abs(forward) / params.queue_creep_px + gap_s / params.max_stopped_gap_s


def _link_cost(a: Fragment, b: Fragment, dt_s: float, params: TrackerParams) -> float | None:
    """How badly `b` continues `a` (lower is better), or None when it cannot. The bridge assumes
    constant acceleration: the displacement across the gap is the mean of the two end velocities
    times the gap, which holds for a car braking or pulling away as well as a steady one. The two
    velocities are fits centred some points inside each fragment, so the time between them, which
    the acceleration allowance must span, is longer than the gap.

    Two stopped ends have no velocity to bridge with. What the fit returns there is box jitter, and
    over a long gap jitter alone predicts tens of pixels, while an allowance that widens with the
    gap would only measure how far apart two cars are. So they use neither: the car is back where it
    was, within `link_position_px` (a lane is about 15 px wide), or it has to pass `_queue_cost`."""
    gap_s = (b.start - a.end) * dt_s
    va = _end_velocity(a, params.fit_points, at_end=True, dt_s=dt_s)
    vb = _end_velocity(b, params.fit_points, at_end=False, dt_s=dt_s)
    stopped = max(math.hypot(*va), math.hypot(*vb)) <= params.stopped_speed_px_s
    if gap_s > (params.max_stopped_gap_s if stopped else params.max_gap_s):
        return None
    moved = float(b.x[0] - a.x[-1]), float(b.y[0] - a.y[-1])
    if stopped:
        miss = math.hypot(*moved)
        position_limit = params.link_position_px
    else:
        miss = math.hypot(
            moved[0] - (va[0] + vb[0]) / 2 * gap_s, moved[1] - (va[1] + vb[1]) / 2 * gap_s
        )
        position_limit = params.link_position_px + params.link_position_px_per_s * gap_s
    between_fits_s = gap_s + (min(len(a.frames), params.fit_points) - 1) * dt_s / 2
    between_fits_s += (min(len(b.frames), params.fit_points) - 1) * dt_s / 2
    speed_limit = params.link_speed_px_s + params.link_accel_px_s2 * between_fits_s
    change = math.hypot(va[0] - vb[0], va[1] - vb[1])
    if miss <= position_limit and change <= speed_limit:
        return miss / position_limit + change / speed_limit + gap_s / params.max_stopped_gap_s
    return _queue_cost(a, b, gap_s, moved, params) if stopped else None


def relink(
    fragments: Sequence[Fragment], frame_dt_s: float, params: TrackerParams | None = None
) -> list[Fragment]:
    """Join fragments that are one vehicle: cheapest link first, each end used once, repeated until
    nothing joins. The joined track keeps the lower id."""
    params = params or TrackerParams()
    current = sorted(fragments, key=lambda f: (f.start, f.track_id))
    reach = math.ceil(max(params.max_gap_s, params.max_stopped_gap_s) / frame_dt_s)
    while True:
        starts = [f.start for f in current]
        links: list[tuple[float, int, int, int, int]] = []
        for i, a in enumerate(current):
            for j in range(bisect_right(starts, a.end), bisect_left(starts, a.end + reach + 1)):
                b = current[j]
                cost = _link_cost(a, b, frame_dt_s, params)
                if cost is not None:
                    links.append((cost, a.track_id, b.track_id, i, j))
        links.sort()
        used_tail: set[int] = set()
        used_head: set[int] = set()
        chosen: dict[int, int] = {}
        for _cost, _a, _b, i, j in links:
            if i in used_tail or j in used_head:
                continue
            used_tail.add(i)
            used_head.add(j)
            chosen[i] = j
        if not chosen:
            break
        merged: list[Fragment] = []
        for i, fragment in enumerate(current):
            if i in used_head:
                continue  # it arrives through the chain that reaches it
            while i in chosen:
                i = chosen[i]
                fragment = _join(fragment, current[i])
            merged.append(fragment)
        current = sorted(merged, key=lambda f: (f.start, f.track_id))
    return sorted(current, key=lambda f: f.track_id)


def fill_gaps(fragment: Fragment) -> FilledTrack:
    """One row per frame: observed rows as they are, missing ones linear in time and flagged."""
    frames = np.arange(fragment.start, fragment.end + 1)
    observed = np.isin(frames, fragment.frames)
    conf = np.zeros(len(frames))
    conf[observed] = fragment.conf

    def fill(values: np.ndarray) -> np.ndarray:
        return np.interp(frames, fragment.frames, values)

    return FilledTrack(
        fragment.track_id,
        fragment.start,
        fill(fragment.x),
        fill(fragment.y),
        fill(fragment.w),
        fill(fragment.h),
        conf,
        ~observed,
        dict(fragment.labels),
    )


def renumber(tracks: Sequence[FilledTrack]) -> list[FilledTrack]:
    """Ids 0..n-1 by first frame, then old id, so a track's id says when it appeared."""
    ordered = sorted(tracks, key=lambda t: (t.start_frame, t.track_id))
    return [replace(t, track_id=i) for i, t in enumerate(ordered)]
