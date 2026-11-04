"""The camera tracker on published trajectories turned into noisy detections.

The detections below are built here, from a committed reference analysis, and stay here: they exist
to test the tracker, and no test writes them to an analysis directory.
"""

from __future__ import annotations

import itertools
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pytest

from cairoflow_perception.analysis_store import (
    PX_SCALE,
    TRAJECTORIES_FILE,
    decode_trajectories,
    load_tracks,
)
from cairoflow_perception.local.tracker import (
    Fragment,
    OnlineTracker,
    TrackerParams,
    assign,
    drop_duplicates,
    fill_gaps,
    relink,
    renumber,
)

REPO = Path(__file__).resolve().parents[1]
REFERENCE = REPO / "data/sample/analyses/ref-ngsim-int2-960-1020"
DT_S = 0.1


def noisy_detections(seed: int, *, sigma_px: float = 1.5, dropout: float = 0.1):
    """Per frame, boxes of the reference vehicles with centre noise, random dropouts and one burst
    of misses per vehicle. Returns (frames, truth) where truth maps (frame, x, y) to vehicle."""
    rng = np.random.default_rng(seed)
    _, columns = decode_trajectories((REFERENCE / TRAJECTORIES_FILE).read_bytes())
    tracks = load_tracks(REFERENCE).tracks
    frames: dict[int, list[tuple]] = defaultdict(list)
    truth: dict[tuple[int, float, float], int] = {}
    for track in tracks:
        rows = slice(track.first_row, track.first_row + track.n)
        x = columns.x[rows] * PX_SCALE
        y = columns.y[rows] * PX_SCALE
        visible = (x > 0) & (x < 640) & (y > 0) & (y < 410)
        burst_at = int(rng.integers(0, track.n))
        burst = np.zeros(track.n, bool)
        burst[burst_at : burst_at + int(rng.integers(3, 9))] = True
        keep = visible & (rng.random(track.n) >= dropout) & ~burst
        for i in np.flatnonzero(keep):
            frame = track.start_frame + int(i)
            cx = float(x[i] + rng.normal(0, sigma_px))
            cy = float(y[i] + rng.normal(0, sigma_px))
            # every tenth detection is weak, as a real detector's are near an occlusion
            score = 0.2 if rng.random() < 0.1 else 0.9
            frames[frame].append((cx - 10, cy - 6, cx + 10, cy + 6, score, "vehicle"))
            truth[(frame, round(cx, 3), round(cy, 3))] = track.track_id
    return frames, truth


def run_tracker(frames, params=None):
    tracker = OnlineTracker(DT_S, params)
    for frame in sorted(frames):
        tracker.update(frame, frames[frame])
    fragments = tracker.finish()
    return fragments, relink(fragments, DT_S, params)


def truth_sequence(fragment: Fragment, truth) -> list[int]:
    return [
        truth[(int(f), round(float(x), 3), round(float(y), 3))]
        for f, x, y in zip(fragment.frames, fragment.x, fragment.y, strict=True)
    ]


@pytest.fixture(scope="module")
def tracked():
    frames, truth = noisy_detections(20261104)
    fragments, linked = run_tracker(frames)
    return frames, truth, fragments, linked


def test_fragmentation_stays_bounded(tracked):
    _, truth, fragments, linked = tracked
    long_enough = {v for v, n in Counter(truth.values()).items() if n >= 30}
    tracks_of: Counter[int] = Counter()
    for fragment in linked:
        seen = Counter(truth_sequence(fragment, truth))
        tracks_of[seen.most_common(1)[0][0]] += 1
    per_vehicle = [tracks_of[v] for v in long_enough]
    assert len(long_enough) > 80
    assert sum(1 for n in per_vehicle if n == 0) <= 0.02 * len(long_enough)  # hardly any lost
    assert np.mean(per_vehicle) <= 1.1
    assert len(linked) <= len(fragments)


def test_id_switches_stay_bounded(tracked):
    _, truth, _, linked = tracked
    rows = switches = 0
    for fragment in linked:
        sequence = truth_sequence(fragment, truth)
        rows += len(sequence)
        switches += sum(a != b for a, b in itertools.pairwise(sequence))
    assert rows > 0.9 * len(truth)  # most detections ended up in a track
    assert switches / rows <= 0.002


def test_most_tracks_are_one_vehicle(tracked):
    _, truth, _, linked = tracked
    purity = []
    for fragment in linked:
        seen = Counter(truth_sequence(fragment, truth))
        purity.append(seen.most_common(1)[0][1] / sum(seen.values()))
    assert np.mean(purity) >= 0.98


def test_same_input_gives_the_same_output():
    frames, _ = noisy_detections(7)
    _, first = run_tracker(frames)
    _, second = run_tracker(frames)
    assert [f.track_id for f in first] == [f.track_id for f in second]
    for a, b in zip(first, second, strict=True):
        assert np.array_equal(a.frames, b.frames)
        assert np.array_equal(a.x, b.x)
        assert np.array_equal(a.y, b.y)
        assert a.labels == b.labels


def test_gaps_fill_to_one_row_per_frame_and_are_flagged(tracked):
    *_, linked = tracked
    filled = [fill_gaps(f) for f in linked]
    gaps = 0
    for fragment, track in zip(linked, filled, strict=True):
        assert len(track) == fragment.end - fragment.start + 1
        assert int((~track.interpolated).sum()) == len(fragment.frames)
        assert np.all(track.conf[track.interpolated] == 0)
        observed = fragment.frames - fragment.start
        assert np.array_equal(track.x[observed], fragment.x)  # a measurement is kept as measured
        gaps += int(track.interpolated.sum())
    assert gaps > 0


def line(frame: int, x: float, y: float = 100.0, score: float = 0.9, label: str = "vehicle"):
    return (x - 10, y - 6, x + 10, y + 6, score, label)


def test_a_track_needs_confirming():
    tracker = OnlineTracker(DT_S, TrackerParams(confirm_hits=3))
    tracker.update(0, [line(0, 100)])
    tracker.update(1, [line(1, 105)])
    assert tracker.active == []
    for frame in range(2, 40):
        tracker.update(frame, [])
    assert tracker.finish() == []  # two hits and gone: never a track
    tracker = OnlineTracker(DT_S, TrackerParams(confirm_hits=3))
    for frame in range(3):
        tracker.update(frame, [line(frame, 100 + 5 * frame)])
    assert len(tracker.active) == 1


def test_a_low_score_keeps_a_track_alive_but_never_starts_one():
    params = TrackerParams(max_miss_s=0.3)
    tracker = OnlineTracker(DT_S, params)
    for frame in range(5):
        tracker.update(frame, [line(frame, 100 + 5 * frame)])
    for frame in range(5, 20):  # only low-confidence boxes from here
        tracker.update(frame, [line(frame, 100 + 5 * frame, score=0.2)])
    assert len(tracker.active) == 1
    assert tracker.active[0].last_seen == 19
    alone = OnlineTracker(DT_S, params)
    for frame in range(20):
        alone.update(frame, [line(frame, 300, score=0.2)])
    assert alone.finish() == []


def test_a_fast_car_is_followed_at_its_real_speed():
    tracker = OnlineTracker(DT_S)
    for frame in range(30):
        tracker.update(frame, [line(frame, 50 + 6.6 * frame)])
    assert len(tracker.finish()) == 1


def test_a_stride_only_changes_the_frame_interval():
    tracker = OnlineTracker(0.3)
    for frame in range(12):
        tracker.update(frame, [line(frame, 50 + 19.8 * frame)])
    assert len(tracker.finish()) == 1


def test_two_cars_side_by_side_keep_their_ids():
    tracker = OnlineTracker(DT_S)
    for frame in range(30):
        tracker.update(
            frame, [line(frame, 100 + 5 * frame, 100), line(frame, 400 - 5 * frame, 118)]
        )
    fragments = tracker.finish()
    assert len(fragments) == 2
    for fragment in fragments:
        assert len(set(np.round(fragment.y))) == 1  # each stayed in its lane


def test_a_frame_cannot_go_back():
    tracker = OnlineTracker(DT_S)
    tracker.update(5, [])
    with pytest.raises(ValueError):
        tracker.update(5, [])


def test_two_boxes_on_one_vehicle_are_one_track():
    """A detector need not suppress duplicates: a whole box and a part box, 7 px apart, are one
    car, so `vehicles.total` and the entering counts must not see it twice."""
    tracker = OnlineTracker(DT_S)
    for frame in range(40):
        x, y = 100.0 + 5 * frame, 200.0
        tracker.update(
            frame,
            [
                (x - 10, y - 6, x + 10, y + 6, 0.9, "car"),
                (x - 4, y - 2, x + 16, y + 10, 0.8, "car"),
            ],
        )
    fragments = tracker.finish()
    assert len(relink(fragments, DT_S)) == 1
    assert np.all(fragments[0].conf == 0.9)  # the better box is the one kept


def test_only_coincident_boxes_are_suppressed():
    near = (100, 100, 120, 112, 0.5, "car")
    best = (104, 102, 124, 114, 0.9, "car")  # 4.5 px away: the same vehicle
    other = (140, 100, 160, 112, 0.7, "car")  # 40 px away: another one
    assert drop_duplicates([near, best, other], 8.0) == [best, other]
    assert drop_duplicates([near], 8.0) == [near]
    assert drop_duplicates([], 8.0) == []


def test_two_cars_nose_to_tail_are_not_duplicates():
    tracker = OnlineTracker(DT_S)
    for frame in range(30):
        x = 100.0 + 2 * frame
        tracker.update(frame, [line(frame, x), line(frame, x + 25)])
    assert len(tracker.finish()) == 2


def fragment(track_id, frames, x, y=100.0):
    frames = np.asarray(frames)
    x = np.asarray(x, float)
    ones = np.ones(len(frames))
    return Fragment(track_id, frames, x, y * ones, 20 * ones, 12 * ones, 0.9 * ones, {"car": 1.0})


def stops_at(x: float, sign: int, track_id: int = 0, frames: int = 31) -> Fragment:
    """A car driving along x in direction `sign` that stops at `x` for its last 16 frames."""
    moving = x - sign * 8.0 * np.arange(frames - 16, 0, -1)
    return fragment(track_id, range(frames), np.concatenate([moving, np.full(16, x)]))


def standing(x: float, start: int, y: float = 100.0, n: int = 40, track_id: int = 1) -> Fragment:
    return fragment(track_id, range(start, start + n), np.full(n, x), y)


def test_relink_joins_a_steady_car_across_a_gap():
    a = fragment(0, range(0, 20), 100 + 5.0 * np.arange(20))
    b = fragment(7, range(30, 50), 100 + 5.0 * np.arange(30, 50))
    joined = relink([a, b], DT_S)
    assert len(joined) == 1
    assert joined[0].track_id == 0
    assert joined[0].labels == {"car": 2.0}


def test_relink_joins_a_car_pulling_away_on_the_next_frame():
    a = fragment(0, range(0, 20), 100 + 1.0 * np.arange(20))
    b = fragment(1, range(20, 40), 120 + 4.0 * np.arange(20))
    assert len(relink([a, b], DT_S)) == 1


def test_relink_does_not_join_opposite_traffic():
    a = fragment(0, range(0, 20), 100 + 5.0 * np.arange(20))
    b = fragment(1, range(25, 45), 225 - 5.0 * np.arange(20))
    assert len(relink([a, b], DT_S)) == 2


def test_relink_does_not_join_a_track_in_another_lane():
    a = fragment(0, range(0, 20), 100 + 5.0 * np.arange(20), y=100.0)
    b = fragment(1, range(24, 44), 220 + 5.0 * np.arange(20), y=160.0)
    assert len(relink([a, b], DT_S)) == 2


def test_relink_does_not_join_overlapping_fragments():
    a = fragment(0, range(0, 20), 100 + 5.0 * np.arange(20))
    b = fragment(1, range(15, 35), 175 + 5.0 * np.arange(20))
    assert len(relink([a, b], DT_S)) == 2


def test_relink_follows_a_queued_car_that_moved_up_while_hidden():
    a = fragment(0, range(0, 60), 100 + 0.5 * np.arange(60))  # ends at 129.5
    b = fragment(1, range(160, 220), 150 + 0.5 * np.arange(60))  # 20 px on: under a car spacing
    assert len(relink([a, b], DT_S)) == 1


def test_relink_does_not_send_a_queued_car_backwards():
    a = fragment(0, range(0, 60), 100 + 0.5 * np.arange(60))
    b = fragment(1, range(160, 220), 90 + 0.5 * np.arange(60))
    assert len(relink([a, b], DT_S)) == 2


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("gap_frames", [50, 100, 200])
def test_relink_keeps_two_stopped_cars_a_spacing_apart(sign, gap_frames):
    """A car stops and vanishes; a different one appears 30 px away, ahead of it or behind it. Its
    turn must not be taken from the wrong car, however long the hole."""
    a = stops_at(300.0, sign)
    assert len(relink([a, standing(270.0, 30 + gap_frames)], DT_S)) == 2


def test_relink_keeps_a_stopped_car_apart_from_one_in_the_next_lane():
    a = stops_at(300.0, 1)
    for across_px in (14.0, 16.0, 19.0):
        assert len(relink([a, standing(300.0, 80, y=100.0 + across_px)], DT_S)) == 2


def test_box_jitter_on_a_standing_car_is_not_a_velocity():
    """A 3 px/s drift is what a fit returns on a standing car. Over a 10 s hole it would bridge
    30 px exactly, so a different car standing there must not be taken for this one."""
    a = fragment(0, range(0, 30), 300 - 0.3 * np.arange(30))  # ends at 291.3
    b = fragment(1, range(129, 159), 261.3 - 0.3 * np.arange(30))
    assert len(relink([a, b], DT_S)) == 2


def test_relink_finds_a_stopped_car_again_where_it_stood():
    a = stops_at(300.0, 1)
    assert len(relink([a, standing(304.0, 200)], DT_S)) == 1


def test_relink_leaves_a_lone_fragment_alone():
    a = fragment(3, range(0, 10), 100 + 5.0 * np.arange(10))
    assert relink([a], DT_S) == [a]


def test_renumber_orders_by_first_frame():
    late = fill_gaps(fragment(0, range(50, 60), np.arange(10.0)))
    early = fill_gaps(fragment(1, range(0, 10), np.arange(10.0)))
    first, second = renumber([late, early])
    assert (first.track_id, first.start_frame) == (0, 0)
    assert (second.track_id, second.start_frame) == (1, 50)


@pytest.mark.parametrize("seed", range(50))
def test_assign_is_optimal(seed):
    rng = np.random.default_rng(seed)
    n, m = int(rng.integers(1, 6)), int(rng.integers(1, 6))
    cost = rng.random((n, m))
    if n <= m:
        best = min(
            sum(cost[i, p[i]] for i in range(n)) for p in itertools.permutations(range(m), n)
        )
    else:
        best = min(
            sum(cost[p[j], j] for j in range(m)) for p in itertools.permutations(range(n), m)
        )
    pairs = assign(cost)
    assert len(pairs) == min(n, m)
    assert sum(cost[r, c] for r, c in pairs) == pytest.approx(best)


def test_the_tracker_does_not_import_torch():
    code = (
        "import sys\n"
        "import cairoflow_perception.local.tracker\n"
        "import cairoflow_perception.local.analytics\n"
        "import cairoflow_perception.local.pipeline\n"
        "assert 'torch' not in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO)
    assert done.returncode == 0, done.stderr
