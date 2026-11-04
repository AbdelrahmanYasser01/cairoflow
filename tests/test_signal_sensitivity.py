"""Signal-timing sensitivity script: cut enumeration and selection, statistics, change mapping and
refusal to overwrite. Pure logic only; no SUMO run."""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from pathlib import Path

import pytest

from cairoflow_contracts.evidence import SignalInterval, SignalTimeline
from cairoflow_perception.ngsim_junction import representative_cycle
from cairoflow_simulation.geometry_compiler import green_onsets

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "data" / "geometry" / "ngsim-lankershim-int2"


def _script():
    path = REPO / "scripts" / "signal_sensitivity_ngsim_int2.py"
    spec = importlib.util.spec_from_file_location("signal_sensitivity", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["signal_sensitivity"] = module
    spec.loader.exec_module(module)
    return module


def _timeline(rows) -> SignalTimeline:
    return SignalTimeline(
        timeline_id="t",
        clip_id="c",
        label="assumed",
        source="test",
        window_s=max(r[3] for r in rows),
        intervals=tuple(
            SignalInterval(arm=arm, head="all", state=state, start_s=a, end_s=b)
            for arm, state, a, b in rows
        ),
    )


# Two arms in strict alternation, three N cycles of 40 s: N green 10 s, yellow 4 s; E green 10 s.
ALTERNATING = [
    ("N", "unknown", 0, 10), ("N", "green", 10, 20), ("N", "yellow", 20, 24),
    ("N", "red", 24, 50), ("N", "green", 50, 60), ("N", "yellow", 60, 64),
    ("N", "red", 64, 90), ("N", "green", 90, 100), ("N", "yellow", 100, 104),
    ("N", "red", 104, 130), ("N", "green", 130, 140), ("N", "yellow", 140, 144),
    ("N", "unknown", 144, 160),
    ("E", "unknown", 0, 30), ("E", "green", 30, 40), ("E", "yellow", 40, 44),
    ("E", "red", 44, 70), ("E", "green", 70, 85), ("E", "yellow", 85, 89),
    ("E", "red", 89, 110), ("E", "green", 110, 120), ("E", "yellow", 120, 124),
    ("E", "red", 124, 150), ("E", "unknown", 150, 160),
]  # fmt: skip


def test_candidate_cuts_run_from_each_onset_to_the_next() -> None:
    s = _script()
    cuts = s.candidate_cuts({"N": [50.0, 90.0, 130.0], "E": [70.0, 110.0]})
    assert [(c.start_s, c.end_s, c.arms) for c in cuts] == [
        (50.0, 90.0, ("N",)),
        (70.0, 110.0, ("E",)),
        (90.0, 130.0, ("N",)),
    ]
    assert cuts[0].length_s == 40.0
    # arms turning green together: the cut ends at the earlier of their next onsets
    both = s.candidate_cuts({"N": [0.0, 50.0], "S": [0.0, 45.0]})
    assert [(c.start_s, c.end_s, c.arms) for c in both] == [(0.0, 45.0, ("N", "S"))]


def test_cut_problems_and_selection_on_a_synthetic_timeline() -> None:
    s = _script()
    timeline = _timeline(ALTERNATING)
    onsets = green_onsets(timeline)
    assert onsets == {"N": [50.0, 90.0, 130.0], "E": [70.0, 110.0]}
    cuts = s.candidate_cuts(onsets)
    problems = {c.start_s: s.cut_problems(timeline, c) for c in cuts}
    assert problems[50.0] == [] and problems[70.0] == [] and problems[90.0] == []
    assert s.green_in(timeline, ("E", "all"), 50.0, 90.0) == 15.0
    medians = s.head_medians(timeline)
    assert medians == {("N", "all"): 10.0, ("E", "all"): 10.0}
    spacing = float(statistics.median(s.onset_spacings(onsets)))
    scores = {c: s.cut_score(timeline, c, medians, spacing) for c in cuts}
    assert [scores[c] for c in cuts] == [5.0, 5.0, 0.0]
    assert s.select_cut(scores).start_s == 90.0
    # the module's own rule picks the same cut
    assert representative_cycle(timeline)["start_s"] == 90.0


def test_cut_problems_name_unknown_missing_green_and_illegal_closure() -> None:
    s = _script()
    rows = [
        ("N", "unknown", 0, 10), ("N", "green", 10, 16), ("N", "yellow", 16, 20),
        ("N", "red", 20, 40), ("N", "green", 40, 46), ("N", "yellow", 46, 50),
        ("N", "red", 50, 70), ("N", "green", 70, 76), ("N", "yellow", 76, 80),
        ("N", "unknown", 80, 100),
        ("E", "unknown", 0, 21), ("E", "green", 21, 23), ("E", "yellow", 23, 25),
        ("E", "red", 25, 60), ("E", "green", 60, 62), ("E", "yellow", 62, 66),
        ("E", "unknown", 66, 100),
    ]  # fmt: skip
    timeline = _timeline(rows)
    # N closes (red leads to green); E's window ends in unknown
    assert s.cut_problems(timeline, s.Cut(40.0, 70.0, ("N",))) == ["E all: unknown inside"]
    assert s.cut_problems(timeline, s.Cut(10.0, 40.0, ("N",))) == ["E all: unknown inside"]
    assert s.cut_problems(timeline, s.Cut(26.0, 40.0, ("N",))) == [
        "N all: no green",
        "E all: no green",
    ]
    # a cut ending in yellow cannot lead back to its green
    single = _timeline(
        [("N", "unknown", 0, 10), ("N", "green", 10, 20), ("N", "yellow", 20, 24),
         ("N", "red", 24, 30), ("N", "green", 30, 40), ("N", "unknown", 40, 50)]
    )  # fmt: skip
    assert s.cut_problems(single, s.Cut(10.0, 24.0, ("N",))) == [
        "N all: does not close (yellow to green)"
    ]
    assert s.cut_problems(single, s.Cut(10.0, 30.0, ("N",))) == []


def test_select_cut_breaks_ties_by_the_earliest_start() -> None:
    s = _script()
    a, b = s.Cut(10.0, 50.0, ("N",)), s.Cut(5.0, 45.0, ("E",))
    assert s.select_cut({a: 3.0, b: 3.0000000001}) == b
    with pytest.raises(ValueError):
        s.select_cut({})


def test_all_red_counts_seconds_when_every_head_is_red() -> None:
    s = _script()
    timeline = _timeline(ALTERNATING)
    # 50-90: N green 50-60, yellow -64, E green 70-85, yellow -89; all red 64-70 and 89-90
    assert s.all_red_s(timeline, 50.0, 90.0) == 7.0


def test_green_bounds_use_the_nearest_conflicting_windows() -> None:
    s = _script()
    timeline = _timeline(ALTERNATING)
    bounds = {(b["head"], b["green_start_s"]): b for b in s.green_bounds(timeline, 1.0)}
    # E green 70-85 (yellow to 89): previous conflicting yellow end 64 (N), next onset 90 (N)
    e = bounds[("E all", 70.0)]
    assert e["onset_could_be_earlier_by_s"] == 5.0
    assert e["end_could_be_later_by_s"] == 0.0
    assert bounds[("N all", 10.0)]["onset_could_be_earlier_by_s"] is None


def test_describe_and_spread() -> None:
    s = _script()
    d = s.describe([1.0, 2.0, 4.0])
    assert d["n"] == 3 and d["mean"] == pytest.approx(7 / 3, abs=1e-6)
    assert d["sd"] == pytest.approx(statistics.stdev([1.0, 2.0, 4.0]), abs=1e-6)
    assert (d["min"], d["max"]) == (1.0, 4.0)
    assert s.describe([5.0])["sd"] is None
    assert s.describe([])["n"] == 0
    sp = s.spread({"a": 3.0, "b": 1.0, "c": 2.5})
    assert sp["lowest"] == {"cut": "b", "mean": 1.0}
    assert sp["highest"] == {"cut": "a", "mean": 3.0}
    assert sp["range"] == 2.0
    assert sp["sd_of_means"] == pytest.approx(statistics.stdev([3.0, 1.0, 2.5]), abs=1e-6)
    assert s.spread({}) == {"n": 0}


def test_t_interval_matches_the_comparison_rule() -> None:
    s = _script()
    diffs = [-2.21597, -8.853244, 0.791964, -0.317084, 0.464089]  # I2 mean delay, recorded
    low, high = s.t_interval(diffs)
    assert (low, high) == pytest.approx((-6.980614, 2.928516), abs=1e-5)
    assert s.t_interval([1.0]) is None


def test_map_change_by_phase_signature() -> None:
    s = _script()
    recorded = ["p0_Ng_Sg", "p1_Ny_Sy", "p2_Eg", "p3_Ey", "p4_NLg"]
    deltas = s.recorded_deltas([31, 4, 5, 4, 10], [26, 4, 5, 4, 15])
    assert deltas == {0: -5.0, 4: 5.0}
    same, how = s.map_change(recorded, recorded, deltas)
    assert same == deltas and how == "identical phase structure"
    rotated, how = s.map_change(
        recorded, ["p0_Eg", "p1_Ey", "p2_NLg", "p3_Ng_Sg", "p4_Ny_Sy"], deltas
    )
    assert rotated == {3: -5.0, 2: 5.0} and how == "same phase structure, rotated"
    other, how = s.map_change(
        recorded, ["p0_Eg", "p1_Ey", "p2_NLg", "p3_Ng_Sg", "p4_Ng_Sy", "p5_Ny_Sr"], deltas
    )
    assert other == {3: -5.0, 2: 5.0} and how.startswith("each changed phase occurs once")
    twice, how = s.map_change(recorded, ["p0_Ng_Sg", "p1_Eg", "p2_Ng_Sg", "p3_NLg"], deltas)
    assert twice is None and "occurs 2 times" in how


def test_prepare_out_and_write_new_refuse_to_overwrite(tmp_path: Path) -> None:
    s = _script()
    out = s.prepare_out(tmp_path / "run")
    assert out.is_dir()
    with pytest.raises(FileExistsError):
        s.prepare_out(tmp_path / "run")
    s.write_new(out / "a.json", "{}\n")
    with pytest.raises(FileExistsError):
        s.write_new(out / "a.json", "{}\n")
    assert (out / "a.json").read_text() == "{}\n"


def test_main_refuses_an_existing_out_directory(tmp_path: Path) -> None:
    s = _script()
    existing = tmp_path / "exists"
    existing.mkdir()
    (existing / "keep.txt").write_text("x")
    assert s.main(["--out", str(existing)]) == 2
    assert (existing / "keep.txt").read_text() == "x"
    assert sorted(p.name for p in existing.iterdir()) == ["keep.txt"]


def test_main_refuses_out_inside_protected_directories() -> None:
    """The recorded runs and the committed records are refused before any work is done."""
    s = _script()
    baseline = REPO / json.loads(s.RECORD.read_text(encoding="utf-8"))["baseline"]["run_dir"]
    for protected in (baseline, s.EXPERIMENT_RUN, REPO / "data", REPO / "cache"):
        assert s.main(["--out", str(protected / "never-created")]) == 2
        assert not (protected / "never-created").exists()


def test_a_protected_directory_reached_through_a_symlink_is_still_refused(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A linked checkout links the recorded runs from the main checkout; the guard must see
    through the link (2026-11-03: a run wrote never-created/ into the main tree)."""
    s = _script()
    real = tmp_path / "main" / "runs" / "baseline"
    real.mkdir(parents=True)
    lane = tmp_path / "lane"
    (lane / "runs").mkdir(parents=True)
    (lane / "runs" / "baseline").symlink_to(real, target_is_directory=True)
    for root, out in (
        (real, lane / "runs" / "baseline" / "never-created"),  # the root as the main tree names it
        (lane / "runs" / "baseline", real / "never-created"),  # the root as a lane names it
    ):
        monkeypatch.setattr(s, "EXPERIMENT_RUN", root)
        assert s.main(["--out", str(out)]) == 2
        assert "is inside" in capsys.readouterr().err  # refused by the guard, before any work
        assert not (real / "never-created").exists()


def test_committed_record_cut_counts() -> None:
    """The committed record states what the enumeration must reproduce (checked in the run)."""
    rep = json.loads((RECORD / "scenario.json").read_text(encoding="utf-8"))["signal_estimate"][
        "representative_cycle"
    ]
    s = _script()
    timeline = SignalTimeline.model_validate_json(
        (RECORD / "signal_timeline.json").read_text(encoding="utf-8")
    )
    onsets = green_onsets(timeline)
    cuts = s.candidate_cuts(onsets)
    qualifying = [c for c in cuts if not s.cut_problems(timeline, c)]
    medians = s.head_medians(timeline)
    spacing = float(statistics.median(s.onset_spacings(onsets)))
    chosen = s.select_cut({c: s.cut_score(timeline, c, medians, spacing) for c in qualifying})
    assert (len(cuts), len(qualifying)) == (rep["candidate_cuts"], rep["qualifying_cuts"])
    assert (chosen.start_s, chosen.end_s) == (rep["start_s"], rep["end_s"])
    assert sorted(c.start_s for c in qualifying) == [
        244.0,
        266.0,
        300.0,
        402.0,
        564.0,
        601.0,
        615.0,
    ]
