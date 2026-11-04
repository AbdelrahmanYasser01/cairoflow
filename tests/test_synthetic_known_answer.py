"""The SYNTHETIC known-answer test of the evaluation and selection stage (known_answer.py).

Covered here:
- the expected correction is arithmetic on the design alone;
- the candidate set holds the correction, a half-way plan, the exact null, the mirror and K seeded
  perturbations within +/-5 s, each with its pre-registered expectation;
- nothing is overwritten;
- the selection is the report's own rule, so a fabricated evaluation in which the correction
  worsens does not select it;
- false positives are counted only against the pre-registered direction;
- the provenance block records git and the pre-registration's hashes.

One small end-to-end run per scenario (3 seeds, short windows, 2 perturbations) goes through SUMO
with the normal pipeline.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sumo

import cairoflow_evaluation.known_answer as known_answer
from cairoflow_contracts.evidence import ARM_ORDER
from cairoflow_contracts.experiment import (
    CandidateEligibility,
    EvaluationResult,
    MetricResult,
    RunSettings,
)
from cairoflow_contracts.reasoning import MAX_CYCLE_S, MAX_GREEN_S, MIN_CYCLE_S, MIN_GREEN_S
from cairoflow_evaluation import report as report_module
from cairoflow_evaluation.compare import paired_comparison, planned_improvement_tests
from cairoflow_evaluation.eligibility import RULES_VERSION
from cairoflow_evaluation.known_answer import (
    SCENARIOS,
    admissible_cycle,
    directions,
    draw_controls,
    mirror_plan,
    preregister,
    preregistration_content_sha256,
    run_known_answer,
    summarize_outcome,
    webster_optimum_cycle_s,
    webster_split,
    whole_seconds,
    write_new,
)

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
SMALL = RunSettings(seeds=(1, 2, 3), warmup_s=60, horizon_s=300, drain_s=300)
SUMO_BIN = Path(sumo.SUMO_HOME) / "bin" / ("sumo.exe" if os.name == "nt" else "sumo")
VERDICTS = ("improves", "worsens", "inconclusive", "uninformative")
ROLES = ("correction", "halfway", "null", "mirror", "control")


def test_webster_split_on_fixed_inputs() -> None:
    # y = 300/1800 and 150/1800; C = 70 s, two phases, l = I = 5 s: g = 60 * y_i / Y = 40, 20
    split = webster_split({"EW": 300 / 1800, "NS": 150 / 1800}, 70, 5, 5, MIN_GREEN_S)
    assert split["greens_s"] == {"EW": 40, "NS": 20}
    assert split["Y"] == pytest.approx(0.25)
    # three phases, 1:2:3 over 80 s of effective green
    three = webster_split({"a": 0.1, "b": 0.2, "c": 0.3}, 95, 5, 5, 5)
    assert three["greens_s"] == {"a": 13, "b": 27, "c": 40}  # 13.33, 26.67, 40: largest remainder
    # s cancels when every approach has the same lanes; l != I shifts the displayed greens
    assert webster_split({"EW": 0.2, "NS": 0.1}, 70, 5, 5, 5)["greens_s"] == {"EW": 40, "NS": 20}
    assert webster_split({"EW": 0.2, "NS": 0.1}, 70, 5, 3, 5)["greens_s"] == {"EW": 41, "NS": 19}
    with pytest.raises(known_answer.KnownAnswerError, match="undersaturated"):
        webster_split({"EW": 0.7, "NS": 0.4}, 70, 5, 5, 5)


def test_webster_optimum_cycle_and_the_validators_limits() -> None:
    # Y = 450/1800 + 300/1800 = 5/12, L = 10 s: C0 = 20 / (7/12) = 34.29 s, rounded up to 35 s
    c0 = webster_optimum_cycle_s({"EW": 450 / 1800, "NS": 300 / 1800}, 5)
    assert c0 == pytest.approx(240 / 7)
    found = admissible_cycle(c0, 2, 5, MIN_GREEN_S)
    assert (found["cycle_s"], found["admissible_as_rounded"], found["moved_into_limits"]) == (
        35, True, False,
    )  # fmt: skip
    assert webster_split({"EW": 0.25, "NS": 1 / 6}, 35, 5, 5, 5)["greens_s"] == {"EW": 15, "NS": 10}
    # Y = 0.25: C0 = 26.7 s rounds up to 27 s, below the validator's 30 s, so 30 s is used
    low = admissible_cycle(webster_optimum_cycle_s({"EW": 1 / 6, "NS": 1 / 12}, 5), 2, 5, 5)
    assert (low["rounded_up_s"], low["cycle_s"], low["admissible_as_rounded"]) == (27, 30, False)
    assert admissible_cycle(400.0, 2, 5, 5)["cycle_s"] == MAX_CYCLE_S


def test_whole_seconds_keeps_the_total_and_the_minimum_green() -> None:
    assert whole_seconds({"a": 1.0, "b": 59.0}, 60, 5) == {"a": 5, "b": 55}
    assert whole_seconds({"a": 30.5, "b": 29.5}, 60, 5) == {"a": 31, "b": 29}
    assert whole_seconds({"a": 30.5, "b": 30.5}, 61, 5) == {"a": 31, "b": 30}  # tie: earlier
    with pytest.raises(known_answer.KnownAnswerError):
        whole_seconds({"a": 1.0, "b": 1.0}, 8, 5)


def test_the_mirror_reverses_the_correction_within_the_validators_limits() -> None:
    design = SCENARIOS["reallocation"]
    # 2 * (20, 40) - (40, 20) = (0, 60): E-W below the minimum green, so (5, 55), same cycle
    mirror = mirror_plan(design, {"EW": 20, "NS": 40}, {"EW": 40, "NS": 20})
    assert mirror["raw_greens_s"] == {"EW": 0, "NS": 60}
    assert (mirror["greens_s"], mirror["adjusted"]) == ({"EW": 5, "NS": 55}, True)
    # 2 * (84, 56) - (15, 10) = (153, 102): scaled to the 90 s maximum green, split kept
    long = mirror_plan(SCENARIOS["long-cycle"], {"EW": 84, "NS": 56}, {"EW": 15, "NS": 10})
    assert long["raw_greens_s"] == {"EW": 153, "NS": 102}
    assert long["greens_s"] == {"EW": 90, "NS": 60}
    assert SCENARIOS["long-cycle"].cycle_s(long["greens_s"]) <= MAX_CYCLE_S
    # inside the limits it is exact
    exact = mirror_plan(design, {"EW": 30, "NS": 30}, {"EW": 35, "NS": 25})
    assert (exact["greens_s"], exact["adjusted"]) == ({"EW": 25, "NS": 35}, False)


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_the_expectation_uses_no_simulator(scenario: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-registration is arithmetic on the design: any subprocess (SUMO, netconvert) fails."""

    def refuse(*args, **kwargs):
        raise AssertionError("pre-registration must not run a simulator")

    monkeypatch.setattr(subprocess, "run", refuse)
    prereg = preregister(SCENARIOS[scenario], SMALL, controls=8, written_at=NOW)
    assert "written_before_simulation" not in prereg  # evidence is recorded by the run instead
    assert prereg["scenario"] == scenario
    assert prereg["expectation"]["kind"].startswith("assumed")
    assert prereg["rules"]["rules_version"] == RULES_VERSION
    assert "not a test of discovery" in prereg["label"]
    assert "Muse is not exercised" in prereg["what_is_not_tested"]
    assert "discover" not in prereg["purpose"]
    expected = {
        "reallocation": ({"EW": 40, "NS": 20}, 70),
        "long-cycle": ({"EW": 15, "NS": 10}, 35),
    }
    assert (prereg["expectation"]["greens_s"], prereg["expectation"]["cycle_s"]) == expected[
        scenario
    ]
    # nothing in it is keyed to a simulated value: a design change moves the answer
    other = SCENARIOS["reallocation"].model_copy(
        update={"flows_veh_per_h": {"N": 100, "E": 300, "S": 100, "W": 300}}
    )
    assert preregister(other, SMALL, written_at=NOW)["expectation"]["greens_s"] == {
        "EW": 45,
        "NS": 15,
    }


def test_the_content_hash_leaves_out_the_timestamp() -> None:
    design = SCENARIOS["long-cycle"]
    first = preregister(design, SMALL, written_at=NOW)
    later = preregister(design, SMALL, written_at=datetime(2026, 11, 1, tzinfo=UTC))
    assert first["written_at_utc"] != later["written_at_utc"]
    assert preregistration_content_sha256(first) == preregistration_content_sha256(later)
    other = preregister(design, SMALL, control_seed=1, written_at=NOW)
    assert preregistration_content_sha256(other) != preregistration_content_sha256(first)


def test_the_long_cycle_scenario_is_pre_registered_as_pareto_improving() -> None:
    design = SCENARIOS["long-cycle"]
    prereg = preregister(design, SMALL, written_at=NOW)
    assert prereg["design"]["baseline_cycle_s"] == 150
    assert "AFTER seeing the reallocation scenario's result" in prereg["design_history"]
    assert "trade-offs" in prereg["purpose"]
    cycle = prereg["expectation"]["cycle"]
    assert cycle["admissible_as_rounded"] and not cycle["moved_into_limits"]
    expected = prereg["pre_registered_predictions"]
    assert expected["correction_expected_pareto_improving"] is True
    correction = prereg["roles"]["correction"]
    change = prereg["analytical_predictions"]["candidates"][correction]["change_vs_baseline_s"]
    assert all(change["red_s"][arm] < 0 and change["delay_s"][arm] < 0 for arm in ARM_ORDER)
    robust = prereg["expectation"]["sensitivity"][
        "pre_registered_correction_judged_with_other_saturation_flows"
    ]
    assert all(item["pareto_improving"] for item in robust.values())
    # the split of the bad plan is already Webster's for its cycle: only the cycle is wrong
    assert webster_split(known_answer.flow_ratios(design), 150, 5, 5, 5)["greens_s"] == (
        design.bad_greens_s
    )
    # the reallocation correction is a trade-off, and is pre-registered as such
    other = preregister(SCENARIOS["reallocation"], SMALL, written_at=NOW)
    assert other["pre_registered_predictions"]["correction_expected_eligible"] is False


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_the_candidate_set_holds_positive_and_negative_controls(scenario: str) -> None:
    design = SCENARIOS[scenario]
    prereg = preregister(design, SMALL, controls=8, control_seed=7, written_at=NOW)
    roles = [c["role"] for c in prereg["candidates"]]
    assert {role: roles.count(role) for role in ROLES} == {
        "correction": 1, "halfway": 1, "null": 1, "mirror": 1, "control": 8,
    }  # fmt: skip
    ids = [c["intervention_id"] for c in prereg["candidates"]]
    assert ids == [f"I{i}" for i in range(1, 13)]
    by_role = {c["role"]: c for c in prereg["candidates"] if c["role"] != "control"}
    assert {role: by_role[role]["intervention_id"] for role in by_role} == {
        role: prereg["roles"][role] for role in by_role
    }
    # the exact null is the baseline plan; the mirror reverses the correction's change
    assert by_role["null"]["greens_s"] == design.bad_greens_s
    assert by_role["null"]["change_from_baseline_s"] == {"EW": 0, "NS": 0}
    assert by_role["mirror"]["greens_s"] == prereg["mirror"]["greens_s"]
    for axis, change in by_role["correction"]["change_from_baseline_s"].items():
        assert by_role["mirror"]["change_from_baseline_s"][axis] * change < 0
    # their pre-registered expectations: never selected; the null rejected by the validator
    expected = prereg["pre_registered_predictions"]
    assert expected["null_expected_selected"] is False
    assert expected["mirror_expected_selected"] is False
    assert "rejected by the validator" in expected["null_expected"]
    assert expected["mirror_expected"].startswith("worse")
    for role in ("null", "mirror"):
        assert by_role[role]["intervention_id"] not in expected["candidates_expected_eligible"]
    assert "not a null" in prereg["control_draw"]["what_they_are"].lower()
    perturbations = sorted(
        (c for c in prereg["candidates"] if c["role"] == "control"), key=lambda c: c["draw_index"]
    )
    assert [c["draw_index"] for c in perturbations] == list(range(8))
    for c in perturbations:
        for axis, step in c["perturbation_s"].items():
            assert isinstance(step, int) and -5 <= step <= 5
            assert c["greens_s"][axis] == design.bad_greens_s[axis] + step
    for c in prereg["candidates"]:  # every plan is inside the validator's limits
        assert all(MIN_GREEN_S <= g <= MAX_GREEN_S for g in c["greens_s"].values())
        assert MIN_CYCLE_S <= c["cycle_s"] <= MAX_CYCLE_S
    plans = [tuple(c["greens_s"].values()) for c in prereg["candidates"] if c["role"] != "null"]
    assert len(set(plans)) == len(plans) and tuple(design.bad_greens_s.values()) not in plans
    # the recorded seed reproduces the draws; another seed gives others
    exclude = {
        tuple(c["greens_s"].values()) for c in prereg["candidates"] if c["role"] != "control"
    }
    again, _ = draw_controls(design.bad_greens_s, 8, 7, exclude=exclude)
    assert [c["perturbation_s"] for c in again] == [c["perturbation_s"] for c in perturbations]
    other, _ = draw_controls(design.bad_greens_s, 8, 8, exclude=exclude)
    assert [c["perturbation_s"] for c in other] != [c["perturbation_s"] for c in perturbations]
    # the ids come from a seeded permutation, not fixed slots
    correction_ids = {
        preregister(design, SMALL, control_seed=seed, written_at=NOW)["roles"]["correction"]
        for seed in range(10)
    }
    assert len(correction_ids) > 1


def test_records_and_runs_are_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    write_new(path, "{}\n")
    with pytest.raises(FileExistsError):
        write_new(path, "{}\n")
    assert path.read_text("utf-8") == "{}\n"
    # an existing record or run directory is refused before anything is written or simulated
    with pytest.raises(FileExistsError, match="record"):
        run_known_answer(tmp_path / "run", record_path=path, settings=SMALL, now=NOW)
    assert not (tmp_path / "run").exists()
    (tmp_path / "existing").mkdir()
    with pytest.raises(FileExistsError):
        run_known_answer(tmp_path / "existing", settings=SMALL, now=NOW)


def _metric(arm: str, seed: int, name: str, value: float) -> MetricResult:
    return MetricResult(
        experiment_id="e", arm=arm, seed=seed, metric_name=name, value=value,
        unit="s" if name.endswith("_s") else "m", window_s=(60.0, 360.0), sample_size=10,
        source_output_file="x", source_sha256="a" * 64, calculation_version="0.1.0",
    )  # fmt: skip


def _evaluation(
    effects: dict[str, dict[str, float]], censored: dict[int, tuple[str, ...]] | None = None
) -> EvaluationResult:
    """A fabricated evaluation: per candidate, a mean shift per metric over five seeds."""
    names = ("mean_delay_s", "mean_queue_m.E", "mean_queue_m.N")
    seeds = range(1, 6)
    noise = (-0.2, -0.1, 0.0, 0.1, 0.2)
    metrics = [_metric("baseline", s, n, 20.0) for s in seeds for n in names]
    comparisons = []
    for candidate, shifts in effects.items():
        found = [
            _metric(candidate, s, n, 20.0 + shifts.get(n, 0.0) + e)
            for n in names
            for s, e in zip(seeds, noise, strict=True)
        ]
        metrics += found
        comparisons += [
            paired_comparison(
                candidate,
                [m for m in metrics if m.arm == "baseline" and m.metric_name == n],
                [m for m in found if m.metric_name == n],
            )
            for n in names
        ]
    evaluation = EvaluationResult(
        experiment_id="e",
        calculation_version="0.1.0",
        metrics=tuple(metrics),
        comparisons=tuple(comparisons),
        excluded=(),
        eligibility=tuple(
            CandidateEligibility(
                candidate=c,
                status="compared",
                run_classes=dict.fromkeys(seeds, "complete"),
                not_inserted=dict.fromkeys(seeds, 0),
                unfinished=(),
                reasons=(),
            )
            for c in effects
        ),  # fmt: skip
        rules_version=RULES_VERSION,
        baseline_censored_lanes=censored or {},
    )
    return evaluation.model_copy(
        update={"improvement_tests": planned_improvement_tests(evaluation)}
    )


def _prediction(mean: float, delay: dict[str, float] | None = None) -> dict:
    changes = {
        "mean_delay_s": mean,
        "delay_s": {arm: (delay or {}).get(arm, 0.0) for arm in ARM_ORDER},
        "red_s": dict.fromkeys(ARM_ORDER, 0),
    }
    return {"change_vs_baseline_s": changes, "direction": directions(changes)}


def _prereg(predictions: dict[str, dict], roles: dict[str, str]) -> dict:
    """A minimal pre-registration with the given roles and predicted changes per candidate."""
    return {
        "scenario": "test",
        "candidates": [
            {"intervention_id": iid, "role": role, "greens_s": {"EW": 1, "NS": 1}}
            for iid, role in roles.items()
        ],
        "roles": {
            role: next((i for i, r in roles.items() if r == role), None)
            for role in ("correction", "halfway", "null", "mirror")
        },
        "analytical_predictions": {"candidates": predictions},
        "pre_registered_predictions": {"candidates_expected_eligible": []},
    }


def test_selection_is_the_reports_rule_not_the_known_answer() -> None:
    # the module selects nothing itself: it uses the report's functions
    assert known_answer.select_candidate is report_module.select_candidate
    assert known_answer.candidate_standings is report_module.candidate_standings
    assert known_answer.assemble_report is report_module.assemble_report
    roles = {"I1": "correction", "I2": "control"}
    prereg = _prereg({"I1": _prediction(-5.0), "I2": _prediction(0.0)}, roles)

    # the correction (I1) makes delay worse; a perturbation (I2) improves it, worsens nothing
    worse = _evaluation({"I1": {"mean_delay_s": +5.0}, "I2": {"mean_delay_s": -5.0}})
    selected, reason = report_module.select_candidate(worse)
    assert selected == "I2"
    outcome = summarize_outcome(prereg, worse, selected, reason)
    assert outcome["known_correction"]["selected"] is False
    assert outcome["known_correction"]["worsens_uncorrected"] == ["mean_delay_s"]
    assert outcome["selected_role"] == "control"

    # a correction that improves delay but worsens a side queue is not selectable either
    traded = _evaluation({"I1": {"mean_delay_s": -5.0, "mean_queue_m.N": +3.0}, "I2": {}})
    selected, reason = report_module.select_candidate(traded)
    assert selected is None
    outcome = summarize_outcome(prereg, traded, selected, reason)
    assert outcome["known_correction"]["holm_confirmed_improvements"] == ["mean_delay_s"]
    assert outcome["known_correction"]["why_not_selected"] == "I1 worsens mean_queue_m.N"

    # when the data favour it, the same code selects it
    better = _evaluation({"I1": {"mean_delay_s": -5.0, "mean_queue_m.E": -3.0}, "I2": {}})
    selected, reason = report_module.select_candidate(better)
    assert selected == "I1"
    assert summarize_outcome(prereg, better, selected, reason)["known_correction"]["selected"]


def test_null_and_mirror_are_reported_against_their_expectations() -> None:
    roles = {"I1": "correction", "I2": "null", "I3": "mirror"}
    predictions = {"I1": _prediction(-5.0), "I2": _prediction(0.0), "I3": _prediction(+5.0)}
    # the null never reaches the simulator: only I1 and I3 have runs
    evaluation = _evaluation({"I1": {"mean_delay_s": -5.0}, "I3": {"mean_delay_s": +5.0}})
    outcome = summarize_outcome(
        _prereg(predictions, roles),
        evaluation,
        *report_module.select_candidate(evaluation),
        rejected={"I2": ("the timing is the same as the baseline",)},
    )
    null, mirror = outcome["null"], outcome["mirror"]
    assert (null["validator"], null["simulation_status"], null["selected"]) == (
        "rejected", "not_simulated", False,
    )  # fmt: skip
    assert null["why_not_selected"] == (
        "rejected by the validator: the timing is the same as the baseline"
    )
    assert null["as_expected"] is True and null["role"] == "negative control"
    assert mirror["worsens_uncorrected"] == ["mean_delay_s"] and mirror["as_expected"] is True
    assert outcome["pre_registered_vs_observed"]["null_as_expected"] is True
    assert "rejected by the validator before simulation" in outcome["conclusion"]
    assert "Muse is not exercised" in outcome["conclusion"]


def test_false_positives_are_only_contradictions_of_the_pre_registered_direction() -> None:
    roles = {"I1": "correction", "I2": "control", "I3": "control", "I4": "control"}
    predictions = {
        "I1": _prediction(-5.0),  # predicted better, confirmed: not a false positive
        "I2": _prediction(+2.0),  # predicted worse, confirmed better: a false positive
        "I3": _prediction(-3.0),  # predicted better, confirmed: not a false positive
        "I4": _prediction(+0.3),  # positive inside the dead-band, confirmed: a false positive
    }
    effects = {c: {"mean_delay_s": -5.0} for c in roles}
    evaluation = _evaluation(effects)
    outcome = summarize_outcome(
        _prereg(predictions, roles), evaluation, *report_module.select_candidate(evaluation)
    )
    improved = outcome["random_perturbations"]["with_holm_confirmed_improvement"]
    assert improved["count"] == 3 and "not a false-positive count" in improved["note"]
    fp = outcome["false_positives"]
    assert [(x["intervention_id"], x["metric"]) for x in fp["items"]] == [
        ("I2", "mean_delay_s"),
        ("I4", "mean_delay_s"),
    ]
    assert (fp["count"], fp["among_perturbations"], fp["beyond_dead_band"]) == (2, 2, 1)
    assert fp["rule_of_three_upper_bound_95"] is None  # not 0 of n
    agreement = outcome["model_agreement"]
    assert agreement["confirmed_where_about_unchanged_predicted"] == {"I4": ["mean_delay_s"]}
    assert agreement["counts"]["opposite"] == 1  # I2: improves where worse was predicted
    assert "uncalibrated Webster model" in outcome["conclusion"]

    # with none contradicting, the rule-of-three bound is 3/n over the confirmable claims
    clean = {"I1": _prediction(-5.0), "I2": _prediction(+2.0), "I3": _prediction(-3.0),
             "I4": _prediction(+2.0)}  # fmt: skip
    quiet = _evaluation({"I1": {"mean_delay_s": -5.0}, "I2": {}, "I3": {}, "I4": {}})
    outcome = summarize_outcome(
        _prereg(clean, roles), quiet, *report_module.select_candidate(quiet)
    )
    fp = outcome["false_positives"]
    assert (fp["count"], fp["opportunities"]) == (0, 2)  # I2 and I4 delay: predicted worse
    assert fp["rule_of_three_upper_bound_95"] == pytest.approx(1.5)
    assert outcome["missed_improvements"]["by_candidate"] == {"I3": ["mean_delay_s"]}


def test_a_censored_baseline_lane_is_stated_in_the_conclusion() -> None:
    roles = {"I1": "correction", "I2": "control"}
    predictions = {"I1": _prediction(-5.0), "I2": _prediction(0.0)}
    evaluation = _evaluation(
        {"I1": {"mean_delay_s": -5.0}, "I2": {}}, censored={2: ("E_in_0",), 8: ("E_in_0",)}
    )
    outcome = summarize_outcome(
        _prereg(predictions, roles), evaluation, *report_module.select_candidate(evaluation)
    )
    censoring = outcome["censoring"]
    assert censoring["lane_filled"] == {"E": [2, 8]}
    assert censoring["queue_claims_untestable_for_every_candidate"] == [
        "mean_queue_m.E",
        "max_queue_m.E",
    ]
    assert "arm E (a lane filled on seeds 2, 8)" in outcome["conclusion"]
    tests = {(t.candidate, t.metric_name): t for t in evaluation.improvement_tests}
    assert tests[("I1", "mean_queue_m.E")].p_value is None  # untestable, as stated


@pytest.mark.skipif(not SUMO_BIN.exists(), reason="SUMO is not installed")
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_a_small_end_to_end_run_uses_the_normal_pipeline(
    scenario: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, record = tmp_path / "runs" / "ka", tmp_path / "records" / "ka.json"
    seen: dict[str, str] = {}
    real = known_answer.run_baseline

    def checked_baseline(*args, **kwargs):
        # the pre-registration exists, and is hashed, before the first SUMO run
        seen["prereg"] = known_answer._sha256_file(run_dir / known_answer.PREREGISTRATION_FILE)
        return real(*args, **kwargs)

    monkeypatch.setattr(known_answer, "run_baseline", checked_baseline)
    summary = run_known_answer(
        run_dir, design=SCENARIOS[scenario], settings=SMALL, controls=2, control_seed=3,
        verify_reruns=False, now=NOW, record_path=record,
    )  # fmt: skip
    assert "error" not in summary, summary.get("error")
    assert summary["scenario"] == scenario and summary["rules_version"] == RULES_VERSION
    assert json.loads(record.read_text("utf-8")) == summary
    assert json.loads((run_dir / "summary.json").read_text("utf-8")) == summary
    assert not (run_dir / "pipeline_record.json").exists()  # kept out of the report app

    # provenance: the pre-registration's hashes, git and the imported files
    prereg_block = summary["preregistration"]
    assert (
        seen["prereg"]
        == prereg_block["file_sha256"]
        == prereg_block["file_sha256_before_simulation"]
    )
    assert prereg_block["file_sha256_after_run"] == prereg_block["file_sha256"]
    assert prereg_block["unchanged_after_run"] is True
    assert prereg_block["content_sha256"] != prereg_block["file_sha256"]
    assert prereg_block["content_sha256"] == preregistration_content_sha256(prereg_block["content"])
    evidence = prereg_block["evidence_written_before_simulation"]
    assert all(evidence.values())
    provenance = summary["provenance"]
    for key in ("git_at_start", "git_at_end"):
        state = provenance[key]
        assert len(state["commit"]) == 40 and isinstance(state["clean"], bool)
        assert "packages/evaluation/cairoflow_evaluation/known_answer.py" in state["files"]
        assert "scripts/run_synthetic_known_answer.py" in state["files"]
    assert isinstance(provenance["clean_against_commit_at_start_and_end"], bool)
    assert provenance["code_unchanged_during_run"] is True
    assert provenance["code_sha256_at_start"]["cairoflow_evaluation.report"] == (
        known_answer._sha256_file(Path(report_module.__file__))
    )

    # every candidate went to the same validator; the exact null is rejected there
    prereg = prereg_block["content"]
    ids = {c["intervention_id"] for c in prereg["candidates"]}
    status = {o["intervention_id"]: o for o in summary["validation"]["outcomes"]}
    assert set(status) == ids
    null, mirror = prereg["roles"]["null"], prereg["roles"]["mirror"]
    assert status[null]["status"] == "rejected"
    assert status[null]["reasons"] == ["the timing is the same as the baseline"]
    simulated = {e["candidate"] for e in summary["eligibility"]["candidates"]}
    assert simulated == ids - {null}  # every accepted candidate ran on the baseline's seeds
    assert mirror in simulated
    assert summary["holm"]["planned_tests"] == len(simulated) * 5  # delay + 4 mean queues each
    assert summary["report"]["trace_problems"] == []
    assert summary["report"]["selection_recomputed_matches"] is True

    outcome = summary["outcome"]
    assert outcome["selected_intervention_id"] == summary["report"]["selected_intervention_id"]
    assert outcome["selected_intervention_id"] != null
    assert outcome["null"]["validator"] == "rejected" and outcome["null"]["as_expected"] is True
    assert outcome["mirror"]["intervention_id"] == mirror
    assert outcome["label"].startswith("SYNTHETIC")
    assert set(outcome) >= {"random_perturbations", "false_positives", "model_agreement",
                            "censoring", "missed_improvements"}  # fmt: skip
    for row in summary["comparisons"]["rows"]:
        assert row["n"] == 3 and row["verdict"] in VERDICTS
    with pytest.raises(FileExistsError):
        run_known_answer(run_dir, settings=SMALL, now=NOW)
