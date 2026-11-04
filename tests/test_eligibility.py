"""Experiment eligibility (cairoflow_evaluation.eligibility) on synthetic run records and files."""

from __future__ import annotations

from pathlib import Path

import pytest

from cairoflow_contracts.experiment import CandidateRecord, RunRecord, RunSettings
from cairoflow_contracts.reasoning import ValidationOutcome
from cairoflow_evaluation.eligibility import (
    EligibilityError,
    approach_lane_lengths,
    candidate_eligibility,
    censored_lanes,
    classify_run,
    unfinished_movements,
)
from cairoflow_simulation.network import content_sha256

SETTINGS = RunSettings(warmup_s=300, horizon_s=900, drain_s=600)
TRIPS = """<?xml version="1.0" encoding="UTF-8"?>
<!-- generated on 2026-10-28 -->
<tripinfos>
    <tripinfo id="SN.0" depart="301.00" departDelay="0.40" arrival="340.00" timeLoss="9.10"/>
    <tripinfo id="WN.7" depart="951.00" departDelay="0.30" arrival="-1.00" timeLoss="821.64"/>
    <tripinfo id="WN.9" depart="1198.00" departDelay="19.70" arrival="-1.00" timeLoss="593.72"/>
    <tripinfo id="EN.2" depart="1191.00" departDelay="0.20" arrival="-1.00" timeLoss="600.10"/>
</tripinfos>
"""
COMPLETE = """<tripinfos>
    <tripinfo id="SN.0" depart="301.00" departDelay="0.40" arrival="340.00" timeLoss="9.10"/>
</tripinfos>
"""
NET = """<net>
    <edge id=":J_0" function="internal"><lane id=":J_0_0" length="9.00"/></edge>
    <edge id="W_in" from="w" to="J"><lane id="W_in_0" length="177.94"/>
        <lane id="W_in_1" length="177.94"/></edge>
    <edge id="N_out" from="J" to="n"><lane id="N_out_0" length="200.00"/></edge>
</net>
"""
QUEUE = """<queue-export>
    <data timestep="299.00"><lanes><lane id="W_in_1" queueing_length="177.90"/></lanes></data>
    <data timestep="300.00"><lanes><lane id="W_in_0" queueing_length="170.44"/>
        <lane id="W_in_1" queueing_length="40.00"/></lanes></data>
    <data timestep="1200.00"><lanes><lane id="W_in_1" queueing_length="177.94"/></lanes></data>
</queue-export>
"""


def _run(seed: int = 1, *, valid: bool = True, **counts) -> RunRecord:
    fields = {"loaded": 10, "inserted": 10, "arrived": 10, "unfinished": 0, "teleports": 0,
              "collisions": 0} | counts  # fmt: skip
    return RunRecord(
        seed=seed,
        run_dir=f"seed_{seed}",
        command=("sumo",),
        demand_sha256="d" * 64,
        tripinfo_sha256="t" * 64,
        valid=valid,
        reasons=() if valid else ("recorded by the runner",),
        **fields,
    )


def _written(runs_dir: Path, run: RunRecord, text: str) -> RunRecord:
    """`run` with its tripinfo.xml written under `runs_dir` and its recorded hash set to it."""
    path = runs_dir / run.run_dir / "tripinfo.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return run.model_copy(update={"tripinfo_sha256": content_sha256(path, strip_comments=True)})


def _candidate(runs: tuple[RunRecord, ...], valid: bool) -> CandidateRecord:
    outcome = ValidationOutcome.model_construct(intervention_id="I1", status="accepted")
    network = None  # not read by the eligibility rules
    return CandidateRecord.model_construct(
        intervention_id="I1", outcome=outcome, network=network, runs=runs, valid=valid,
        reasons=(),
    )  # fmt: skip


def test_runs_are_complete_incomplete_or_broken() -> None:
    assert classify_run(_run()) == "complete"
    assert classify_run(_run(valid=False, unfinished=2, arrived=8)) == "incomplete"
    # still waiting to enter at the end: incomplete, even with nothing in the network
    assert classify_run(_run(valid=False, inserted=8, arrived=8)) == "incomplete"
    assert classify_run(_run(valid=False, teleports=1)) == "integrity_failure"
    # a teleport makes the run broken even when vehicles are also unfinished
    assert classify_run(_run(valid=False, collisions=1, unfinished=3, arrived=7)) == (
        "integrity_failure"
    )


def test_a_run_record_that_contradicts_itself_is_refused() -> None:
    with pytest.raises(EligibilityError, match="recorded valid=True but the run is incomplete"):
        classify_run(_run(unfinished=1, arrived=9))
    with pytest.raises(EligibilityError, match="recorded valid=False but the run is complete"):
        classify_run(_run(valid=False))


def test_unfinished_vehicles_are_grouped_by_movement(tmp_path: Path) -> None:
    path = tmp_path / "tripinfo.xml"
    path.write_text(TRIPS, encoding="utf-8")
    found = unfinished_movements(path, seed=4)
    assert [(m.movement, m.vehicles, m.first_depart_s) for m in found] == [
        ("EN", 1, 1191.0),
        ("WN", 2, 951.0),
    ]
    wn = found[1]
    assert wn.seed == 4
    # timeLoss + departDelay at the end: lower bounds, since the trips never finished
    assert wn.delay_lower_bound_range_s == pytest.approx((593.72 + 19.7, 821.64 + 0.3))


def test_a_candidate_is_compared_only_when_every_seed_is_complete(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    complete = _written(runs_dir, _run(1), COMPLETE)
    incomplete = _written(
        runs_dir, _run(2, valid=False, unfinished=3, arrived=6, inserted=9), TRIPS
    )
    found = candidate_eligibility(_candidate((complete, incomplete), False), runs_dir, SETTINGS)
    assert found.status == "incomplete"
    assert found.run_classes == {1: "complete", 2: "incomplete"}
    assert found.not_inserted == {1: 0, 2: 1}
    assert [m.movement for m in found.unfinished] == ["EN", "WN"]
    assert found.reasons == (
        "seed 2: 3 vehicles still in the network (EN 1, WN 2) and 1 not yet inserted at 1800 s, "
        "after a 600 s drain with no new demand",
    )
    assert found.censored_lanes == {}  # no lane lengths given

    both = (complete, _written(runs_dir, _run(2), COMPLETE))
    result = candidate_eligibility(_candidate(both, True), runs_dir, SETTINGS)
    assert (result.status, result.unfinished, result.reasons) == ("compared", (), ())


def test_a_broken_seed_outranks_an_incomplete_one(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    broken = _written(runs_dir, _run(1, valid=False, teleports=2), TRIPS)
    incomplete = _written(runs_dir, _run(2, valid=False, unfinished=3, arrived=7), TRIPS)
    found = candidate_eligibility(_candidate((broken, incomplete), False), runs_dir, SETTINGS)
    assert found.status == "invalid_simulation"
    assert found.reasons[0] == "seed 1: recorded by the runner"


def test_changed_outputs_and_inconsistent_records_are_refused(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    complete = _written(runs_dir, _run(1), COMPLETE)
    # every run is checked, a complete one included
    (runs_dir / "seed_1" / "tripinfo.xml").write_text(TRIPS, encoding="utf-8")
    with pytest.raises(EligibilityError, match="changed after the run"):
        candidate_eligibility(_candidate((complete,), True), runs_dir, SETTINGS)
    complete = _written(runs_dir, _run(1), COMPLETE)
    with pytest.raises(EligibilityError, match="recorded valid=False but its runs are compared"):
        candidate_eligibility(_candidate((complete,), False), runs_dir, SETTINGS)


def test_a_queue_that_fills_its_lane_is_listed_as_censored(tmp_path: Path) -> None:
    net, queue = tmp_path / "network.net.xml", tmp_path / "queue.xml"
    net.write_text(NET, encoding="utf-8")
    queue.write_text(QUEUE, encoding="utf-8")
    lengths = approach_lane_lengths(net)
    assert lengths == {"W_in_0": 177.94, "W_in_1": 177.94}  # approach lanes only
    # W_in_0 is within one car (7.5 m) of its end inside the window; W_in_1 is full only at
    # 299 s and 1200 s, outside the window [300, 1200)
    assert censored_lanes(queue, lengths, SETTINGS) == ("W_in_0",)

    runs_dir = tmp_path / "runs"
    run = _written(runs_dir, _run(1), COMPLETE)
    (runs_dir / "seed_1" / "queue.xml").write_text(QUEUE, encoding="utf-8")
    found = candidate_eligibility(_candidate((run,), True), runs_dir, SETTINGS, lengths)
    assert found.censored_lanes == {1: ("W_in_0",)}
