"""Drain-sensitivity script: the pre-end identity check, refusal to overwrite and summary building.
Pure logic on small synthetic SUMO outputs; no SUMO run."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _script():
    path = REPO / "scripts" / "drain_sensitivity_ngsim_int2.py"
    spec = importlib.util.spec_from_file_location("drain_sensitivity", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["drain_sensitivity"] = module
    spec.loader.exec_module(module)
    return module


ds = _script()


def _trip(vid: str, depart: float, arrival: float, time_loss: float = 1.0) -> str:
    return (
        f'    <tripinfo id="{vid}" depart="{depart:.2f}" departLane="W_in_2" '
        f'arrival="{arrival:.2f}" duration="{max(arrival - depart, 0):.2f}" '
        f'timeLoss="{time_loss:.2f}" departDelay="0.10"/>\n'
    )


def _write_run(
    run_dir: Path,
    trips: list[tuple[str, float, float]],
    queue: dict[float, float],
    *,
    loaded: int,
    running: int,
    comment: str = "",
) -> Path:
    """A run directory with tripinfo.xml, queue.xml (W_in_2 only) and statistics.xml."""
    run_dir.mkdir(parents=True)
    (run_dir / "tripinfo.xml").write_text(
        f'<?xml version="1.0"?>\n<!-- {comment} -->\n<tripinfos>\n'
        + "".join(_trip(*t) for t in trips)
        + "</tripinfos>\n"
    )
    steps = []
    for t, length in sorted(queue.items()):
        lanes = (
            f'<lane id="W_in_2" queueing_time="2.00" queueing_length="{length:.2f}" '
            'queueing_length_experimental="0.00"/>'
            if length
            else ""
        )
        steps.append(f'  <data timestep="{t:.2f}"><lanes>{lanes}</lanes></data>\n')
    (run_dir / "queue.xml").write_text("<queue-export>\n" + "".join(steps) + "</queue-export>\n")
    (run_dir / "statistics.xml").write_text(
        f'<statistics><vehicles loaded="{loaded}" inserted="{loaded}" running="{running}" '
        'waiting="0"/><teleports total="0"/><safety collisions="0"/></statistics>\n'
    )
    return run_dir


# The recorded run ends at 20 s with B still in the network; the later run lets B finish at 25 s.
REF_TRIPS = [("A", 0.0, 8.0), ("C", 1.0, 12.0), ("B", 2.0, -1.0)]
LATER_TRIPS = [("A", 0.0, 8.0), ("C", 1.0, 12.0), ("B", 2.0, 25.0)]
REF_QUEUE = {float(t): (7.5 if t >= 10 else 0.0) for t in range(20)}


def _pair(tmp_path: Path, later_trips=LATER_TRIPS, later_queue=None):
    ref = _write_run(tmp_path / "ref", REF_TRIPS, REF_QUEUE, loaded=3, running=1, comment="a")
    queue = {**REF_QUEUE, 20.0: 7.5, 21.0: 0.0} if later_queue is None else later_queue
    later = _write_run(tmp_path / "later", later_trips, queue, loaded=3, running=0, comment="b")
    return ref, later


def test_identical_before_the_reference_end(tmp_path: Path) -> None:
    ref, later = _pair(tmp_path)
    check = ds.pre_end_identity(ref, later, 20.0)
    assert check.identical and check.first_difference is None
    assert (check.arrivals_compared, check.unfinished_compared, check.queue_steps_compared) == (
        2,
        1,
        20,
    )


def test_a_changed_arrival_is_found(tmp_path: Path) -> None:
    ref, later = _pair(tmp_path, later_trips=[("A", 0.0, 8.0), ("C", 1.0, 13.0), ("B", 2.0, 25.0)])
    check = ds.pre_end_identity(ref, later, 20.0)
    assert not check.identical and not check.arrivals_identical
    assert "trip C" in check.first_difference


def test_a_vehicle_finishing_before_the_end_only_later_is_found(tmp_path: Path) -> None:
    # B arrives at 19 s in the later run, but was unfinished in the recorded run.
    ref, later = _pair(tmp_path, later_trips=[("A", 0.0, 8.0), ("C", 1.0, 12.0), ("B", 2.0, 19.0)])
    check = ds.pre_end_identity(ref, later, 20.0)
    assert not check.identical
    assert not check.unfinished_identical or not check.arrivals_identical


def test_a_changed_queue_step_is_found_and_later_steps_are_ignored(tmp_path: Path) -> None:
    ref, later = _pair(tmp_path, later_queue={**REF_QUEUE, 15.0: 15.0, 30.0: 99.0})
    check = ds.pre_end_identity(ref, later, 20.0)
    assert check.arrivals_identical and check.unfinished_identical and not check.queue_identical
    assert check.first_difference == "queue.xml: timestep 15 differs"
    ref2, later2 = _pair(tmp_path / "b", later_queue={**REF_QUEUE, 30.0: 99.0})
    assert ds.pre_end_identity(ref2, later2, 20.0).identical


def test_prepare_out_refuses_existing_and_recorded_trees(tmp_path: Path) -> None:
    existing = tmp_path / "out"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        ds.prepare_out(existing, forbidden=())
    recorded = tmp_path / "experiments" / "runs"
    with pytest.raises(ValueError, match="never writes there"):
        ds.prepare_out(recorded / "new", forbidden=(recorded,))
    assert not (recorded / "new").exists()
    made = ds.prepare_out(tmp_path / "fresh" / "dir", forbidden=(recorded,))
    assert made.is_dir()


def test_default_forbidden_trees_are_the_repository_records() -> None:
    with pytest.raises(ValueError):
        ds.prepare_out(ds.BASELINE_DIR / "never-created")
    with pytest.raises(ValueError):
        ds.prepare_out(ds.EXPERIMENT_DIR.parent / "never-created")
    with pytest.raises(ValueError):
        ds.prepare_out(REPO / "data" / "never-created")
    assert not (ds.BASELINE_DIR / "never-created").exists()


def test_rerun_args_change_only_the_end_and_input_paths(tmp_path: Path) -> None:
    command = [
        "sumo", "--net-file", "../../network/network.net.xml", "--route-files", "../d.rou.xml",
        "--seed", "4", "--end", "1800", "--tripinfo-output", "tripinfo.xml",
    ]  # fmt: skip
    run_dir = tmp_path / "x" / "runs" / "seed_4"
    args = ds.rerun_args(command, run_dir, 4200.0)
    assert args[args.index("--end") + 1] == "4200"
    net = (tmp_path / "x/network/network.net.xml").resolve()
    assert args[args.index("--net-file") + 1] == str(net)
    assert args[args.index("--route-files") + 1] == str((tmp_path / "x/runs/d.rou.xml").resolve())
    unchanged = [a for i, a in enumerate(args) if i not in (1, 3, 7)]
    assert unchanged == [a for i, a in enumerate(command[1:]) if i not in (1, 3, 7)]
    with pytest.raises(ds.InputError):
        ds.rerun_args(["netconvert", "--end", "1"], run_dir, 1.0)


def test_recorded_commands_hold_no_machine_specific_path() -> None:
    inside = str(REPO / "experiments/runs/x/network/network.net.xml")
    outside = "/opt/elsewhere/d.rou.xml"
    assert ds.repo_relative(["--net-file", inside, "--route-files", outside, "--end", "4200"]) == [
        "--net-file", "experiments/runs/x/network/network.net.xml", "--route-files", outside,
        "--end", "4200",
    ]  # fmt: skip


def test_summarize_run_and_queue_times(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "r", REF_TRIPS, REF_QUEUE, loaded=3, running=1)
    summary = ds.summarize_run(run, "W_in_2", (5.0, 19.0, 20.0))
    assert (summary.loaded, summary.arrived, summary.unfinished) == (3, 2, 1)
    assert summary.last_arrival_s == 12.0 and summary.unfinished_ids == ("B",)
    assert summary.queue_m == {"5": 0.0, "19": 7.5, "20": None}


def _entry(arm: str, seed: int, end: float, unfinished: int, delay: float, ok: bool = True):
    return {
        "arm": arm,
        "seed": seed,
        "end_s": end,
        "summary": {
            "unfinished": unfinished,
            "last_arrival_s": 12.0 if end == 20.0 else 25.0,
            "queue_m": {"10": 7.5, "19": 7.5, "20": None},
        },
        "metrics": [
            {"metric_name": "mean_delay_s", "value": delay, "unit": "s"},
            {"metric_name": "throughput_veh_per_h", "value": 3600.0, "unit": "veh/h"},
        ],
        "tripinfo_matches_record": ok if end == 20.0 else None,
        "queue_matches_record": True if end == 20.0 else None,
        "identity": None if end == 20.0 else {"identical": ok},
    }


def test_summary_rows_and_markdown() -> None:
    runs = [
        _entry("baseline", 1, 20.0, 0, 30.0),
        _entry("baseline", 1, 40.0, 0, 30.0),
        _entry("I3", 1, 20.0, 1, 50.0),
        _entry("I3", 1, 40.0, 0, 53.0, ok=False),
    ]
    rows = ds.summary_rows(runs, (20.0, 40.0), 20.0, 10.0)
    base, cand = rows
    assert base["checks_passed"] and base["metrics_changed_with_end"] == []
    assert cand["unfinished"] == {"20": 1, "40": 0}
    assert cand["mean_delay_s"] == {"20": 50.0, "40": 53.0}
    assert cand["metrics_changed_with_end"] == ["mean_delay_s"]
    assert cand["queue_m_at_window_end"] == 7.5 and cand["queue_m_before_reference_end"] == 7.5
    assert not cand["checks_passed"]
    assert not ds.arm_complete(rows, "I3", 20.0) and ds.arm_complete(rows, "I3", 40.0)

    comparison = {
        "metric_name": "mean_delay_s",
        "unit": "s",
        "mean_difference": 20.0,
        "ci95_difference": [1.0, 39.0],
        "verdict": "worsens",
    }
    doc = {
        "ends_s": [20.0, 40.0],
        "reference_end_s": 20.0,
        "window_s": [5.0, 10.0],
        "software": {"eclipse-sumo": "1.28.0"},
        "queue_lane": "W_in_2",
        "inputs": {"checks": ["ok"]},
        "checks": {"reference_hashes_match": True, "pre_end_identical": False, "all_passed": False},
        "rows": rows,
        "comparisons": [
            {
                "candidate": "I3",
                "end_s": 20.0,
                "candidate_complete": False,
                "metrics": [comparison],
            },
            {"candidate": "I3", "end_s": 40.0, "candidate_complete": True, "metrics": [comparison]},
        ],
    }
    text = ds.render_markdown(doc)
    assert "| I3 | 1 | 1 | 0 | 12 | 25 | 7.5 | 7.5 | FAIL |" in text
    assert "| baseline | 1 | 30.000 | 30.000 | none |" in text
    assert "worsens (lower bound; not compared under the rule)" in text
    assert "| I3 | 40 | yes | mean_delay_s | +20.000 s | [+1.000, +39.000] | worsens |" in text
    assert "All checks passed: **False**" in text
