"""SAM refinement: request planning, the time map, the seed-forward rule, drift, failure, cost.

No network, no recorded stream and no torch: replies are synthetic (tests/synthetic_sam.py) and the
paid path runs against a stand-in for the adapter.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from synthetic_sam import sse, token

from cairoflow_contracts.analysis_record import TrackEntry
from cairoflow_contracts.evidence import FrameRef, Observation, Provenance
from cairoflow_perception.analysis_store import POINT_INTERPOLATED, POINT_SAM_REFINED
from cairoflow_perception.local import sam_refine as R
from cairoflow_perception.local.scoring import HeldOutError
from cairoflow_perception.sam_adapter import SamRequestError
from cairoflow_perception.sam_parse import mask_records
from cairoflow_perception.sam_seeded import MAX_OBJECTS_PER_REQUEST

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
PARAMS = R.RefineParams()
START_S, FPS, FRAMES = 960.0, 10.0, 600
END_S = START_S + FRAMES / FPS


def make_track(
    track_id: int = 1,
    start: int = 20,
    n: int = 60,
    *,
    x0: float = 100.0,
    y0: float = 100.0,
    vx: float = 2.0,
    conf: float = 0.8,
    gaps: tuple[int, ...] = (),
) -> R.TrackRows:
    """Rows of a car moving along x, 30 x 14 px; `gaps` are row numbers that are interpolated."""
    interpolated = np.zeros(n, bool)
    interpolated[list(gaps)] = True
    return R.TrackRows(
        track_id,
        start,
        x0 + vx * np.arange(n),
        np.full(n, y0),
        np.full(n, 30.0),
        np.full(n, 14.0),
        np.full(n, conf),
        interpolated,
    )


def sam_box(track: R.TrackRows, frame: int, dx: float = 0.0, dy: float = 0.0) -> R.SamRow:
    x1, y1, x2, y2 = track.box(frame)
    return R.SamRow(frame, (x1 + dx, y1 + dy, x2 + dx, y2 + dy), 0.9)


def segment(track: R.TrackRows, seed: int | None = None, tail: bool = True) -> R.Segment:
    frame = track.start_frame if seed is None else seed
    return R.Segment(track.track_id, frame, track.end_frame, (10, 10, 40, 24), 0.8, False, tail)


def followed(track: R.TrackRows, rows: dict[int, R.SamRow], seed: int | None = None) -> R.Followed:
    return R.Followed(0, "1", segment(track, seed), rows)


def refine(
    track: R.TrackRows, rows: dict[int, R.SamRow], seed: int | None = None, others=()
) -> R.RefinedTrack:
    return R.refine_track(
        track, [followed(track, rows, seed)], R.boxes_by_frame([track, *others]),
        frame_count=FRAMES, valid_below_row_px=410, width_px=640, params=PARAMS,
    )  # fmt: skip


def plan_options(**changes) -> dict:
    return {
        "source_start_s": START_S, "fps": FPS, "first_second": 961.0, "end_s": END_S,
        "frame_count": FRAMES, "params": PARAMS, **changes,
    }  # fmt: skip


# ---- cohort planning ---------------------------------------------------------------------------


def test_no_request_holds_more_than_sixteen_objects_or_one_track_twice() -> None:
    tracks = [make_track(i, 20 + i, 80, y0=20.0 + 9 * i, x0=60.0) for i in range(40)]
    segments, unseeded = R.plan_segments(
        tracks, earliest_frame=10, valid_below_row_px=410, width_px=640, height_px=480,
        params=R.RefineParams(seed_max_iou=1.0),
    )  # fmt: skip
    cohorts = R.pack_cohorts(segments, **plan_options())
    assert not unseeded
    assert sum(len(c.segments) for c in cohorts) == 40
    assert max(len(c.segments) for c in cohorts) <= MAX_OBJECTS_PER_REQUEST
    for cohort in cohorts:
        ids = [s.track_id for s in cohort.segments]
        assert len(ids) == len(set(ids))


def test_every_seed_lands_on_a_frame_of_its_own_clip() -> None:
    tracks = [make_track(i, 15 + 37 * i, 70) for i in range(12)]
    segments, _ = R.plan_segments(
        tracks, earliest_frame=10, valid_below_row_px=410, width_px=640, height_px=480,
        params=PARAMS,
    )  # fmt: skip
    for cohort in R.pack_cohorts(segments, **plan_options()):
        for seed in cohort.seeds(START_S, FPS):
            assert 0 <= seed.frame < cohort.frames


def test_a_long_track_is_followed_in_overlapping_parts() -> None:
    parts = R.segment_bounds(1, 397, 150, 30)
    assert parts[0] == (1, 150) and parts[-1][1] == 397
    assert all(b[0] == a[1] - 29 for a, b in zip(parts, parts[1:], strict=False))
    assert all(end - start + 1 <= 150 for start, end in parts)


def test_a_clip_stays_within_its_length_limit() -> None:
    tracks = [make_track(1, 20, 150), make_track(2, 400, 150, y0=200.0)]
    segments, _ = R.plan_segments(
        tracks, earliest_frame=10, valid_below_row_px=410, width_px=640, height_px=480,
        params=PARAMS,
    )  # fmt: skip
    assert len(R.pack_cohorts(segments, **plan_options())) == 2


def test_a_seed_is_observed_confident_and_inside_the_usable_rows() -> None:
    track = make_track(1, 20, 40, gaps=(0, 1))
    track.conf[2:4] = 0.3  # below seed_min_conf
    options = {
        "earliest_frame": 10, "valid_below_row_px": 410, "width_px": 640, "height_px": 480,
        "params": PARAMS,
    }  # fmt: skip
    assert R.choose_seed(track, 20, 59, {}, **options)[:2] == (24, 0.8)
    low = make_track(2, 20, 40, y0=405.0)  # its box reaches past row 410
    assert R.choose_seed(low, 20, 59, {}, **options) is None


def test_a_car_cut_off_by_the_side_of_the_image_is_only_a_last_resort() -> None:
    track = make_track(1, 20, 40, x0=5.0, vx=0.0)
    found = R.choose_seed(
        track, 20, 59, {}, earliest_frame=10, valid_below_row_px=410, width_px=640,
        height_px=480, params=PARAMS,
    )  # fmt: skip
    assert found == (20, 0.8, True)


def test_a_seed_overlapping_a_neighbour_is_passed_over() -> None:
    track = make_track(1, 20, 10, vx=0.0)
    other = make_track(2, 20, 5, vx=0.0)  # on top of it for the first five rows
    found = R.choose_seed(
        track, 20, 29, R.boxes_by_frame([track, other]), earliest_frame=10,
        valid_below_row_px=410, width_px=640, height_px=480, params=PARAMS,
    )  # fmt: skip
    assert found[0] == 25


# ---- the time map ------------------------------------------------------------------------------


@pytest.mark.parametrize("trim_s", [961.0, 980.0, 1013.0])
def test_clip_frames_map_to_the_analysis_frame_with_the_same_image(trim_s: float) -> None:
    """The lag rule of local/frames.py: both sides name one AVI frame, for every clip frame."""
    for clip_frame in range(50):
        frame = R.analysis_frame_of_clip(clip_frame, trim_s, START_S, FPS)
        assert R.clip_frame_of_analysis(frame, trim_s, START_S, FPS) == clip_frame
        avi = R.avi_frame_of_clip(clip_frame, trim_s)
        assert avi == R.avi_frame_of_analysis(frame, START_S, FPS)


def test_the_first_frame_of_a_clip_at_980_is_avi_frame_9799() -> None:
    assert R.avi_frame_of_clip(0, 980.0) == 9799  # 10 t + j - 1
    assert R.analysis_frame_of_clip(0, 980.0, START_S, FPS) == 200


def test_the_pixel_check_names_a_clip_that_is_a_frame_off() -> None:
    assert R.frame_map_offset({-1: 6.0, 0: 1.7, 1: 5.9}.__getitem__) == 0
    assert R.frame_map_offset({-1: 1.7, 0: 6.0, 1: 9.0}.__getitem__) == -1


def test_a_clip_never_holds_a_held_out_image() -> None:
    with pytest.raises(HeldOutError, match="held-out"):
        R.check_clip_window(960.0, 5.0, FPS, START_S, END_S)  # frame 0 is AVI 9599, block 13
    R.check_clip_window(961.0, 5.0, FPS, START_S, END_S)
    assert R.first_clear_second(START_S, END_S, FPS) == 961.0


def test_a_clip_in_an_odd_block_is_refused() -> None:
    with pytest.raises(HeldOutError):
        R.check_clip_window(905.0, 5.0, FPS, 900.0, 960.0)


def test_a_clip_outside_the_analysis_is_refused() -> None:
    with pytest.raises(R.RefineError, match="outside"):
        R.check_clip_window(1018.0, 5.0, FPS, START_S, END_S)


# ---- the seed-forward rule ---------------------------------------------------------------------


def observation(object_id: int, clip_frame: int, box: tuple[int, int, int, int]) -> Observation:
    return Observation(
        observation_id=f"clip:vehicle:{object_id}:{clip_frame}",
        frame=FrameRef(clip_id="clip", frame_index=clip_frame, timestamp_s=clip_frame / 10),
        class_label="vehicle",
        object_id=f"vehicle:{object_id}",
        bbox_xyxy_px=box,
        confidence=0.9,
        provenance=Provenance(kind="model_generated", produced_by="sam-3.1", produced_at_utc=NOW),
    )


def test_boxes_before_an_objects_own_seed_are_never_used() -> None:
    """Tracking runs from the earliest prompt, so object 2 has boxes before its own seed."""
    early = R.Segment(1, 200, 240, (10, 10, 40, 24), 0.8, False, True)
    late = R.Segment(2, 215, 240, (10, 10, 40, 24), 0.8, False, True)
    cohort = R.Cohort(0, 980.0, 5.0, (early, late))
    seen = [observation(o, f, (10, 10, 40, 24)) for o in (1, 2) for f in range(0, 30)]
    parts = R.sam_rows(seen, cohort, source_start_s=START_S, fps=FPS)
    assert min(parts[0].rows) == 200
    assert min(parts[1].rows) == 215
    assert max(parts[1].rows) == 229


def test_an_interpolated_row_before_the_seed_stays_interpolated() -> None:
    track = make_track(1, 20, 30, gaps=(2, 12))
    rows = {f: sam_box(track, f) for f in range(30, 50)}
    item = refine(track, rows, seed=30)
    assert bool(item.track.interpolated[2]) is True
    assert bool(item.track.interpolated[12]) is False
    assert item.counts["replaced"] == 1


# ---- judging -----------------------------------------------------------------------------------


def test_an_interpolated_row_takes_samis_box_and_an_observed_row_never_moves() -> None:
    track = make_track(1, 20, 20, gaps=(5,))
    rows = {f: sam_box(track, f, dx=2.0) for f in range(20, 40)}
    item = refine(track, rows)
    assert item.track.x[5] == pytest.approx(track.x[5] + 2.0)
    assert not item.track.interpolated[5] and item.refined[5]
    assert np.array_equal(item.track.x[:5], track.x[:5])
    assert item.counts["confirmed"] == 19 and item.counts["replaced"] == 1


def test_a_box_that_drifted_onto_a_neighbour_is_rejected_and_counted() -> None:
    track = make_track(1, 20, 20, gaps=(10,))
    other = make_track(2, 20, 20, y0=110.0)  # the next lane, ten pixels away
    rows = {f: sam_box(track, f) for f in range(20, 30)}
    rows.update({f: sam_box(other, f, dy=-2.0) for f in range(30, 40)})
    item = refine(track, rows, others=[other])
    assert not item.refined.any()
    assert bool(item.track.interpolated[10]) is True
    assert item.counts["rejected"] == 10 and item.counts["confirmed"] == 10


def test_a_far_box_is_rejected_at_the_gate() -> None:
    track = make_track(1, 20, 10)
    verdict = R.judge_row(track, 25, sam_box(track, 25, dx=12.0).box, {}, PARAMS)
    assert verdict == R.FAR
    assert R.judge_row(track, 25, sam_box(track, 25, dx=9.0).box, {}, PARAMS) == R.OK


def test_a_box_of_another_size_is_rejected() -> None:
    track = make_track(1, 20, 10)
    x1, y1, x2, y2 = track.box(25)
    assert R.judge_row(track, 25, (x1 - 60, y1, x2 + 60, y2), {}, PARAMS) == R.SIZE


def test_a_run_of_rejected_observed_rows_ends_the_part() -> None:
    """After `lost_after_misses`, a later row that looks right again is not believed."""
    track = make_track(1, 20, 30, gaps=(25,))
    rows = {f: sam_box(track, f) for f in range(20, 30)}
    rows.update({f: sam_box(track, f, dx=40.0) for f in range(30, 35)})
    rows.update({f: sam_box(track, f) for f in range(35, 50)})
    verdicts = R.judge_part(track, followed(track, rows), {}, PARAMS)
    assert verdicts[34] == R.FAR
    assert verdicts[35] == R.LOST and verdicts[45] == R.LOST
    assert not refine(track, rows).refined.any()


def test_a_newer_part_decides_a_row_two_parts_reach() -> None:
    track = make_track(1, 20, 40, gaps=(30,))
    old = R.Followed(0, "1", segment(track, 20, tail=False),
                     {f: sam_box(track, f, dx=9.0) for f in range(20, 60)})  # fmt: skip
    new = R.Followed(1, "1", segment(track, 40), {f: sam_box(track, f) for f in range(40, 60)})
    item = R.refine_track(
        track, [old, new], R.boxes_by_frame([track]), frame_count=FRAMES,
        valid_below_row_px=410, width_px=640, params=PARAMS,
    )  # fmt: skip
    assert item.track.x[30] == pytest.approx(track.x[30])
    assert item.accepted[50] == (1, "1")


# ---- extending a track -------------------------------------------------------------------------


def moving_on(track: R.TrackRows, frames: int, *, step: float = 2.0, conf: float = 0.9) -> dict:
    rows = {f: sam_box(track, f) for f in range(track.start_frame, track.end_frame + 1)}
    x1, y1, x2, y2 = track.box(track.end_frame)
    for k in range(1, frames + 1):
        frame = track.end_frame + k
        rows[frame] = R.SamRow(frame, (x1 + step * k, y1, x2 + step * k, y2), conf)
    return rows


def test_sam_may_carry_a_track_on_only_as_far_as_the_tracker_would_coast() -> None:
    track = make_track(1, 20, 20)
    item = refine(track, moving_on(track, 60))
    assert item.counts["extended"] == PARAMS.extend_frames
    assert len(item.track) == 20 + PARAMS.extend_frames
    assert item.refined[20:].all() and not item.refined[:20].any()
    assert not item.track.interpolated[20:].any()


def test_a_track_is_not_extended_onto_another_track() -> None:
    track = make_track(1, 20, 20)
    ahead = make_track(2, 20, 80, x0=track.x[-1] + 16.0, vx=0.0)
    item = refine(track, moving_on(track, 30), others=[ahead])
    assert item.counts["extended"] == 2  # 16 px away at 2 px a frame: the third row is at the gate
    assert item.counts["extension_stopped_reached_another_track"] == 1


@pytest.mark.parametrize(
    ("step", "conf", "stopped"),
    [(30.0, 0.9, "jump"), (2.0, 0.2, "low_confidence")],
)
def test_a_box_that_stops_looking_like_the_vehicle_ends_an_extension(step, conf, stopped) -> None:
    track = make_track(1, 20, 20)
    item = refine(track, moving_on(track, 30, step=step, conf=conf))
    assert item.counts["extended"] == 0
    assert item.counts[f"extension_stopped_{stopped}"] == 1


def test_a_mask_left_behind_when_the_car_moved_on_ends_an_extension() -> None:
    """Seen on the hero: a car went under a tree and its mask stayed on the leaves."""
    track = make_track(1, 20, 20, vx=6.0)
    item = refine(track, moving_on(track, 30, step=0.0))
    assert item.counts["extended"] == 1, "the first row is inside the allowance, the next is not"
    assert item.counts["extension_stopped_motion"] == 1


def test_a_queued_car_may_be_held_by_sam_while_it_stands() -> None:
    track = make_track(1, 20, 20, vx=0.0)
    item = refine(track, moving_on(track, 60, step=0.0))
    assert item.counts["extended"] == PARAMS.extend_frames


def test_a_box_that_shrank_ends_an_extension() -> None:
    track = make_track(1, 20, 20)
    rows = moving_on(track, 5)
    x1, y1, x2, y2 = rows[42].box
    rows[42] = R.SamRow(42, (x1 + 10, y1 + 2, x2 - 10, y2 - 2), 0.9)
    item = refine(track, rows)
    assert item.counts["extended"] == 2
    assert item.counts["extension_stopped_size"] == 1


def test_end_velocity_reads_observed_rows_only() -> None:
    track = make_track(1, 20, 20, vx=3.0, gaps=(19,))
    assert R.end_velocity(track, 8) == pytest.approx((3.0, 0.0))
    assert R.end_velocity(make_track(2, 20, 1), 8) == (0.0, 0.0)


def test_a_track_whose_last_row_was_not_accepted_is_not_extended() -> None:
    track = make_track(1, 20, 20)
    rows = moving_on(track, 30)
    rows[39] = sam_box(track, 39, dx=30.0)
    item = refine(track, rows)
    assert item.counts["extended"] == 0 and len(item.track) == 20


def test_a_track_leaving_the_usable_rows_is_not_extended() -> None:
    track = make_track(1, 20, 20, y0=400.0, vx=0.0)
    rows = {f: sam_box(track, f) for f in range(20, 40)}
    rows[40] = R.SamRow(40, (85.0, 396.5, 115.0, 410.5), 0.9)
    item = refine(track, rows)
    assert item.counts["extended"] == 0
    assert item.counts["extension_stopped_left_usable_image"] == 1


# ---- cost --------------------------------------------------------------------------------------


def cohort(index: int = 0, duration_s: float = 20.0, seed: int = 210) -> R.Cohort:
    part = R.Segment(index, seed, seed + 150, (10, 10, 40, 24), 0.8, False, True)
    return R.Cohort(index, 980.0, duration_s, (part,))


def test_a_request_costs_its_frames_with_a_fifty_frame_minimum() -> None:
    assert cohort(duration_s=20.0).cost_usd == pytest.approx(0.04)
    assert cohort(duration_s=3.0).cost_usd == pytest.approx(0.01)


def test_the_plan_prices_every_request_and_counts_billed_frames() -> None:
    plan = R.RefinePlan((cohort(0), cohort(1, 3.0)), ())
    assert plan.cost_usd == pytest.approx(0.05)
    assert plan.frames == 250


def test_over_budget_keeps_the_priority_window_and_says_what_went() -> None:
    near = cohort(0, seed=150)
    far = cohort(1, seed=520)
    plan = R.fit_budget([far, near], 0.05, range(100, 400))
    assert [c.index for c in plan.cohorts] == [0]
    assert [c.index for c in plan.dropped] == [1]


# ---- failure -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def script():
    path = REPO / "scripts/cv_sam_refine.py"
    spec = importlib.util.spec_from_file_location("cv_sam_refine", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    """Stands for the adapter: never opens a connection."""

    spent_usd = 0.0

    def __init__(self, *_args, **_kwargs) -> None:
        pass


def wire(script, monkeypatch, tmp_path, replies, *, left: float = 1.0) -> list:
    """The paid path with a stand-in for the clip, the adapter and the ledger."""
    clip = SimpleNamespace(
        output_stream=SimpleNamespace(width_px=640, height_px=480, frame_count=200),
        output_sha256="0" * 64,
    )
    ledger: list = []
    answers = iter(replies)

    def reply(*_args):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(script, "clip_for", lambda _cohort: (clip, tmp_path / "clip.mp4"))
    monkeypatch.setattr(script, "check_frame_map", lambda *_args: {})
    monkeypatch.setattr(script, "CACHE", tmp_path)
    monkeypatch.setattr(script, "SamClient", FakeClient)
    monkeypatch.setattr(script, "segment_seeded", reply)
    monkeypatch.setattr(script, "task_spent_usd", lambda: 0.0)
    monkeypatch.setattr(script.meta_spend, "remaining_usd", lambda: left)
    monkeypatch.setattr(script.meta_spend, "record", lambda *args: ledger.append(args))
    return ledger


def good_reply(object_id: int = 1):
    record = SimpleNamespace(
        lines=sse([token(0, object_id, 10, 10)]), request_key="k", frames_billed=200,
        estimated_cost_usd=0.04, elapsed_s=1.0,
    )  # fmt: skip
    parsed = SimpleNamespace(
        observations=(), outcome="completed", parser_diagnostics=(), dropped=()
    )
    return record, parsed, False


def fetch(script, plan: R.RefinePlan, **options):
    settings = SimpleNamespace(sam_model="sam-3.1")
    record = SimpleNamespace(source_start_s=START_S, fps=FPS)
    defaults = {"mode": "live", "budget": 1.0, "limit": None, "retry_failed": False}
    return script.fetch(plan, record, settings, **{**defaults, **options})


def server_error() -> SamRequestError:
    return SamRequestError("boom", status=500, error_class="server", request_id=None)


def test_a_failed_request_leaves_its_tracks_unrefined_and_says_which(
    script, monkeypatch, tmp_path
) -> None:
    ledger = wire(script, monkeypatch, tmp_path, [server_error(), good_reply()])
    outcomes, followed_parts, _, _ = fetch(script, R.RefinePlan((cohort(0), cohort(1)), ()))
    assert [o.status for o in outcomes] == ["failed", "ok"]
    assert outcomes[0].tracks == (0,)
    assert "server" in outcomes[0].reason
    assert {p.segment.track_id for p in followed_parts} == {1}
    assert len(ledger) == 1, "a refused request costs nothing; the good one is in the ledger"
    text = R._sam_disclosure({}, outcomes, 1, 1, 2, None)
    assert "1 planned requests did not complete: tracks [0]" in text


def test_an_account_problem_stops_the_run(script, monkeypatch, tmp_path) -> None:
    error = SamRequestError("no", status=402, error_class="billing", request_id=None)
    wire(script, monkeypatch, tmp_path, [error])
    outcomes, *_ = fetch(script, R.RefinePlan((cohort(0), cohort(1)), ()))
    assert [o.status for o in outcomes] == ["failed", "not_sent"]


def test_a_request_that_failed_before_is_not_sent_again(script, monkeypatch, tmp_path) -> None:
    wire(script, monkeypatch, tmp_path, [])  # any send would raise StopIteration
    monkeypatch.setattr(script, "failed_before", lambda _key: True)
    outcomes, *_ = fetch(script, R.RefinePlan((cohort(0),), ()))
    assert outcomes[0].status == "failed"
    assert "not retried" in outcomes[0].reason


def test_nothing_is_sent_past_the_cap(script, monkeypatch, tmp_path) -> None:
    ledger = wire(script, monkeypatch, tmp_path, [], left=0.01)
    outcomes, *_ = fetch(script, R.RefinePlan((cohort(0), cohort(1)), ()))
    assert [o.status for o in outcomes] == ["not_sent", "not_sent"]
    assert "would pass the cap" in outcomes[0].reason
    assert not ledger


def test_a_connection_that_broke_is_assumed_billed(script, monkeypatch, tmp_path) -> None:
    ledger = wire(script, monkeypatch, tmp_path, [script.httpx.ReadTimeout("slow")])
    outcomes, *_ = fetch(script, R.RefinePlan((cohort(0),), ()))
    assert outcomes[0].status == "failed"
    assert ledger[0][1] == pytest.approx(0.04)


def test_a_track_no_reply_reached_comes_back_unchanged() -> None:
    track = make_track(1, 20, 20, gaps=(3,))
    [item] = R.refine_all(
        [track], [], frame_count=FRAMES, valid_below_row_px=410, width_px=640, params=PARAMS
    )
    assert np.array_equal(item.track.x, track.x) and not item.accepted
    assert item.counts["unreached_interpolated"] == 1


# ---- masks and the new rows --------------------------------------------------------------------


def test_a_synthetic_mask_becomes_an_outline_in_image_pixels() -> None:
    records = mask_records(sse([token(0, 1, 100, 200)]))
    record = records[("1", 0)]
    assert R.mask_array(record).all()
    polygon, contour = R.mask_outline(record, 0.8)
    assert polygon.min(axis=0).tolist() == [100.5, 200.5]
    assert polygon.max(axis=0).tolist() == [147.5, 247.5]
    assert len(polygon) == 4 and len(contour) > 4


def test_a_footprint_is_measured_at_pixel_corners() -> None:
    record = mask_records(sse([token(0, 1, 100, 200)]))[("1", 0)]
    _, contour = R.mask_outline(record, 0.8)
    site = SimpleNamespace(project_to_junction=lambda x, y: np.stack([x * 0.1, y * 0.05], axis=1))
    length, width = R.footprint_m(contour, site)
    assert length == pytest.approx(4.8, abs=0.01)
    assert width == pytest.approx(2.4, abs=0.01)


def test_a_mask_cut_off_by_the_image_gives_no_size() -> None:
    record = mask_records(sse([token(0, 1, 0, 200)]))[("1", 0)]
    assert R.touches_edge(record, 640, 410)
    inside = mask_records(sse([token(0, 1, 100, 200)]))[("1", 0)]
    assert not R.touches_edge(inside, 640, 410)


def test_only_accepted_masks_reach_the_outlines() -> None:
    mask = mask_records(sse([token(0, 1, 100, 200)]))[("1", 0)]
    track = make_track(1, 20, 12)
    item = R.RefinedTrack(track, np.zeros(12, bool), {20: (0, "1")}, {}, ())
    masks = R.MaskIndex({(0, "1", 20): mask, (0, "1", 25): mask})
    payload = R.outlines_payload([item], masks, analysis_id="a", params=PARAMS)
    assert [e["frame"] for e in payload["entries"]] == [20]
    assert payload["entries"][0]["track_id"] == 1
    assert "model_generated" in payload["label"]
    assert len(R.outlines_bytes(payload)) < 1_000_000


def entry(track: R.TrackRows, vehicle_class: str = "truck") -> TrackEntry:
    return TrackEntry(
        track_id=track.track_id, first_row=0, start_frame=track.start_frame, n=len(track),
        vehicle_class=vehicle_class, class_source="detector", class_confidence=0.7,
        status="partial",
    )  # fmt: skip


def test_rows_sam_supplied_are_flagged_and_the_label_is_carried_over() -> None:
    track = make_track(1, 20, 20, gaps=(5,))
    item = refine(track, moving_on(track, 2))
    [placed] = R.place_all(
        [item], [entry(track)], None, frame_dt_s=0.1, valid_below_row_px=410,
        image_size_px=(640, 480),
    )  # fmt: skip
    assert placed.flags[5] & POINT_SAM_REFINED and not placed.flags[5] & POINT_INTERPOLATED
    assert placed.flags[-1] & POINT_SAM_REFINED
    assert not placed.flags[0] & POINT_SAM_REFINED, "a confirmed row is still the detector's"
    assert (placed.vehicle_class, placed.class_source) == ("truck", "detector")
    [written] = R.track_entries([placed], [item], {})
    assert written.sam_refined is True and written.n == 22


def test_a_track_sam_only_confirmed_is_not_marked_refined() -> None:
    track = make_track(1, 20, 20)
    item = refine(track, {f: sam_box(track, f) for f in range(20, 40)})
    [placed] = R.place_all(
        [item], [entry(track)], None, frame_dt_s=0.1, valid_below_row_px=410,
        image_size_px=(640, 480),
    )  # fmt: skip
    [written] = R.track_entries([placed], [item], {})
    assert item.counts["confirmed"] == 20 and written.sam_refined is False


def test_a_size_from_masks_is_used_only_where_passed() -> None:
    track = make_track(1, 20, 20)
    item = refine(track, {f: sam_box(track, f) for f in range(20, 40)})
    [placed] = R.place_all(
        [item], [entry(track)], None, frame_dt_s=0.1, valid_below_row_px=410,
        image_size_px=(640, 480),
    )  # fmt: skip
    size = R.Footprint(1, 11.5, 2.6, 9)
    [written] = R.track_entries([placed], [item], {1: size})
    assert (written.length_m, written.width_m) == (11.5, 2.6)


def test_separation_compares_the_methods_on_the_same_tracks() -> None:
    rows = [
        R.SizeRow(1, 12.0, False, 11.0, None),  # a long vehicle the detector called a car
        R.SizeRow(2, 12.0, True, 11.5, 9.0),
        R.SizeRow(3, 4.5, False, 4.8, 8.0),
        R.SizeRow(4, 4.5, True, 4.4, 5.0),  # a car the detector called a truck
        R.SizeRow(5, 4.5, False, None, 5.0),  # no mask size: left out of every method
    ]
    out = R.separation(rows, 7.5)
    assert out["tracks"] == {"with_reference": 5, "with_mask_size": 4}
    assert out["mask_length"]["true_long"] == 2 and out["mask_length"]["false_long"] == 0
    assert out["detector_label"]["missed_long"] == 1 and out["detector_label"]["false_long"] == 1
    assert out["placed_length"]["precision"] == pytest.approx(0.5)


def test_this_module_does_not_bring_in_torch() -> None:
    assert "torch" not in sys.modules
