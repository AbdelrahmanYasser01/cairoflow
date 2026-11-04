"""The signal-timeline ensemble (ADR-025): the cross-timeline rule cannot be met by pooling, and the
committed members are what the pre-registered design and the builder say they are."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from cairoflow_contracts.evidence import SignalTimeline
from cairoflow_contracts.experiment import (
    CandidateEligibility,
    EvaluationResult,
    MetricResult,
    PairedComparison,
)
from cairoflow_evaluation.compare import paired_comparison
from cairoflow_evaluation.ensemble import (
    MemberEvidence,
    PhaseChange,
    ensemble_verdict,
    phase_changes_from_notes,
)
from cairoflow_evaluation.timeline_gate import conflicting_seconds
from cairoflow_simulation.geometry_compiler import known_window

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "data/geometry/ngsim-lankershim-int2"
DESIGN = RECORD / "timeline_ensemble.json"
SEEDS = tuple(range(1, 21))
NOISE = tuple((i - 9.5) / 50 for i in range(20))  # mean 0, SD about 0.118
MEMBERS = ("T1", "T2", "T3", "T4")


def _script(name: str):
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _comparison(name: str, mean: float, seeds=SEEDS, candidate: str = "I1") -> PairedComparison:
    unit = "veh/h" if name.startswith("throughput") else "s" if name.endswith("_s") else "m"

    def metric(arm: str, seed: int, value: float) -> MetricResult:
        return MetricResult(
            experiment_id="e", arm=arm, seed=seed, metric_name=name, value=value, unit=unit,
            window_s=(0.0, 633.0), sample_size=10, source_output_file="x",
            source_sha256="a" * 64, calculation_version="0.1.0",
        )  # fmt: skip

    return paired_comparison(
        candidate,
        [metric("baseline", s, 50.0) for s in seeds],
        [metric(candidate, s, 50.0 + mean + NOISE[i]) for i, s in enumerate(seeds)],
    )


def _member(
    member: str,
    delay: float = -2.0,
    queue: float = 0.0,
    status: str = "compared",
    seeds=SEEDS,
    applied: int = 14,
    required: bool = True,
) -> MemberEvidence:
    """One member where I1's mean delay and west mean queue differ by `delay` and `queue`."""
    comparisons = [
        _comparison("mean_delay_s", delay, seeds),
        _comparison("mean_queue_m.W", queue, seeds),
    ]
    eligibility = CandidateEligibility(
        candidate="I1", status=status, run_classes={s: "complete" for s in seeds},
        not_inserted={s: 0 for s in seeds}, unfinished=(), reasons=(),
    )  # fmt: skip
    evaluation = EvaluationResult(
        experiment_id=f"e-{member}", calculation_version="0.1.0", metrics=(),
        comparisons=tuple(comparisons), excluded=(), eligibility=(eligibility,),
        rules_version="0.3.0",
    )  # fmt: skip
    change = PhaseChange(phases="W=green", change_s=-6.0, applied=applied, clamped=0,
                         already_shorter=0 if applied else 15)  # fmt: skip
    return MemberEvidence(
        member=member, required=required, planned_seeds=SEEDS, evaluation=evaluation,
        phase_changes={"I1": (change,)}, baseline_assessment="partially_validated",
    )  # fmt: skip


def _verdict(*members: MemberEvidence):
    result = ensemble_verdict(members)
    (only,) = result.candidates
    return result, only


def test_a_candidate_eligible_on_every_member_is_robust_but_not_recommended() -> None:
    result, i1 = _verdict(*(_member(m) for m in MEMBERS))
    assert i1.verdict == "robust_improvement" and i1.supported_on == MEMBERS
    assert i1.common_confirmed == ("mean_delay_s",) and i1.robust_harm == ()
    assert result.robust_selection == "I1" and result.recommended is None
    assert "not assessed as validated" in result.recommendation


def test_eligible_on_three_of_four_members_is_not_robust() -> None:
    result, i1 = _verdict(*(_member(m) for m in MEMBERS[:3]), _member("T4", status="incomplete"))
    assert i1.verdict == "timeline_dependent"
    assert i1.supported_on == MEMBERS[:3] and i1.not_supported_on == ("T4",)
    assert "T4" in i1.reason and result.robust_selection is None


def test_one_worsening_on_one_member_blocks_even_when_the_pooled_effect_improves() -> None:
    # three members improve delay by 3 s, one worsens it by 1 s: pooled, -2 s would "improve"
    members = [_member(m, delay=-3.0) for m in MEMBERS[:3]] + [_member("T4", delay=1.0)]
    result, i1 = _verdict(*members)
    assert i1.verdict == "timeline_dependent" and i1.not_supported_on == ("T4",)
    (delay,) = [d for d in i1.direction_disagreements if d.metric_name == "mean_delay_s"]
    assert delay.opposite and delay.verdicts == {
        "T1": "improves", "T2": "improves", "T3": "improves", "T4": "worsens",
    }  # fmt: skip
    assert result.robust_selection is None and result.recommended is None


def test_a_worsened_secondary_metric_on_one_member_blocks() -> None:
    members = [_member(m) for m in ("T1", "T3", "T4")] + [_member("T2", queue=2.0)]
    _, i1 = _verdict(*sorted(members, key=lambda e: e.member))
    assert i1.verdict == "timeline_dependent" and i1.not_supported_on == ("T2",)
    assert any("worsens mean_queue_m.W" in b for m in i1.members for b in m.blocked_by)
    assert i1.robust_harm == ()


def test_an_inconclusive_member_blocks() -> None:
    members = [_member(m) for m in ("T1", "T2", "T4")] + [_member("T3", delay=-0.01)]
    _, i1 = _verdict(*sorted(members, key=lambda e: e.member))
    assert i1.verdict == "timeline_dependent" and i1.not_supported_on == ("T3",)
    t3 = next(m for m in i1.members if m.member == "T3")
    assert t3.confirmed == () and "survives Holm's correction" in " ".join(t3.blocked_by)


def test_fewer_paired_seeds_than_planned_blocks() -> None:
    members = [_member("T1", seeds=SEEDS[:-1])] + [_member(m) for m in MEMBERS[1:]]
    _, i1 = _verdict(*members)
    assert i1.verdict == "timeline_dependent" and i1.not_supported_on == ("T1",)
    t1 = next(m for m in i1.members if m.member == "T1")
    assert any("planned" in b for b in t1.blocked_by)


def test_a_change_that_applies_to_no_phase_is_not_the_same_plan() -> None:
    members = [_member(m, applied=0 if m == "T2" else 14) for m in MEMBERS]
    _, i1 = _verdict(*members)
    assert i1.verdict == "timeline_dependent" and i1.not_supported_on == ("T2",)
    t2 = next(m for m in i1.members if m.member == "T2")
    assert any("not the same plan" in b for b in t2.blocked_by)


def test_no_support_and_robust_harm() -> None:
    _, i1 = _verdict(*(_member(m, delay=2.0) for m in MEMBERS))
    assert i1.verdict == "no_support" and i1.supported_on == ()
    assert i1.robust_harm == ("mean_delay_s",)


def test_an_optional_member_does_not_decide() -> None:
    members = [_member(m) for m in MEMBERS] + [_member("T5", delay=2.0, required=False)]
    _, i1 = _verdict(*members)
    assert i1.verdict == "robust_improvement"
    assert any(d.metric_name == "mean_delay_s" and d.opposite for d in i1.direction_disagreements)


def test_the_members_must_share_one_planned_seed_set() -> None:
    other = _member("T2").model_copy(update={"planned_seeds": SEEDS[:-1]})
    with pytest.raises(ValueError, match="same"):
        ensemble_verdict([_member("T1"), other])
    with pytest.raises(ValueError, match="unique"):
        ensemble_verdict([_member("T1"), _member("T1")])


def test_replay_notes_are_read_into_phase_changes() -> None:
    notes = (
        "I3 on the replay: E=red N=green S=red W=red N left=red S left=red: +4 s applied to 0 "
        "phases (0 stopped at the 5 s minimum green; 15 already shorter, unchanged)",
        "candidate network note without the replay marker",
    )
    (change,) = phase_changes_from_notes(notes)["I3"]
    assert change.change_s == 4.0 and change.applied == 0 and change.already_shorter == 15
    with pytest.raises(ValueError, match="unreadable"):
        phase_changes_from_notes(["I1 on the replay: something else"])


# --- the committed members -----------------------------------------------------------------------


@pytest.mark.skipif(not DESIGN.exists(), reason="no ensemble design committed")
def test_the_design_names_the_committed_member_files() -> None:
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    assert [m["id"] for m in design["members"]] == list(MEMBERS)
    assert all(m["required"] for m in design["members"])
    assert tuple(design["replay"]["seeds"]) == SEEDS
    v1 = SignalTimeline.model_validate_json((RECORD / "signal_timeline.json").read_text())
    assert tuple(design["replay"]["window_s"]) == known_window(v1)
    corners = set()
    for member in design["members"]:
        path = RECORD / member["timeline_file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == member["timeline_sha256"]
        timeline = SignalTimeline.model_validate_json(path.read_text(encoding="utf-8"))
        assert timeline.timeline_id == member["timeline_id"] and timeline.heads() == v1.heads()
        assert known_window(timeline) == known_window(v1) and conflicting_seconds(timeline) == []
        corners.add((member["permissive_left_evidence"], member["yellow_from_last_crossing"]))
        if member["estimate_file"]:
            estimate_path = RECORD / member["estimate_file"]
            assert (
                hashlib.sha256(estimate_path.read_bytes()).hexdigest() == member["estimate_sha256"]
            )
            estimate = json.loads(estimate_path.read_text(encoding="utf-8"))
            assert estimate["timeline_sha256"] == member["timeline_sha256"]
            assert (
                estimate["revision"]["permissive_left_evidence"]
                == member["permissive_left_evidence"]
            )
            assert (
                estimate["revision"]["yellow_from_last_crossing"]
                == member["yellow_from_last_crossing"]
            )
            if member["version"] in ("v2a", "v2b"):
                assert estimate["reproduces_v1"] and estimate["reproduces_v2"]
                assert estimate["gate"]["passed"] and not estimate["gate"]["G4_more_red_than_v1"]
    assert corners == {(False, False), (True, True), (True, False), (False, True)}  # the 2 x 2


@pytest.mark.skipif(not DESIGN.exists(), reason="no ensemble design committed")
def test_the_replay_build_loads_every_version_hash_checked() -> None:
    build = _script("build_ngsim_int2_realism")
    scenario = json.loads((RECORD / "scenario.json").read_text(encoding="utf-8"))
    assert build.load_timeline_v2(scenario) == build.load_timeline_version(scenario, "v2")
    for version in ("v2a", "v2b"):
        timeline, path = build.load_timeline_version(scenario, version)
        assert path.name == f"signal_timeline_{version}.json"
        assert timeline.timeline_id.endswith(f"-{version}")


@pytest.mark.skipif(not DESIGN.exists(), reason="no ensemble design committed")
def test_the_validation_finds_each_versions_estimate() -> None:
    validate = _script("validate_ngsim_int2_baseline")
    for version in ("v2", "v2a", "v2b"):
        found = validate.estimate_record_for(RECORD, RECORD / f"signal_timeline_{version}.json")
        assert found.name == f"signal_estimate_{version}.json"
    with pytest.raises(validate.InputError):
        validate.estimate_record_for(RECORD, RECORD / "signal_timeline.json")


def _processed_inputs_present() -> bool:
    scenario = json.loads((RECORD / "scenario.json").read_text(encoding="utf-8"))
    return (REPO / scenario["processed_outputs"]["dir"] / "world_model.json").exists()


@pytest.mark.skipif(
    not DESIGN.exists() or not _processed_inputs_present(),
    reason="the processed world model (Git-ignored) is not built",
)
def test_the_builder_reproduces_versions_1_and_2_and_the_committed_members(capsys) -> None:
    builder = _script("build_ngsim_int2_timeline_ensemble")
    assert builder.main(["--dry-run"]) == 0  # G1 refuses (exit 2) unless v1 and v2 reproduce
    printed = capsys.readouterr().out
    assert "reproduced byte for byte" in printed
    for version in ("v2a", "v2b"):
        digest = hashlib.sha256(
            (RECORD / f"signal_timeline_{version}.json").read_bytes()
        ).hexdigest()
        assert f"{version}: sha256 {digest}; gate passed True" in printed
