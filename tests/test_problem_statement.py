"""ProblemStatement: the S-approach queue problem as a projection of committed records (G3a).

Every number pinned below was read from a committed record (the timeline-ensemble record, two
realism validation records and their replay assessments) and is checked here against what the
builder copies. The builder computes no number. The Muse hypotheses are attached only when the
Git-ignored reply the realism records pin is present, and always as AI-generated text.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.problem_statement import (
    AttachedHypothesis,
    ProblemStatement,
    conditions_of,
    statement_of,
)
from cairoflow_evaluation.problem_statement import (
    ENSEMBLE_RECORD,
    ProblemStatementError,
    problem_statement_from_records,
)

REPO = Path(__file__).resolve().parents[1]
ENSEMBLE = REPO / ENSEMBLE_RECORD

# from experiments/records/ngsim-int2-timeline-ensemble-20261029T123045Z.json, members[*]
BASELINE_MEAN_QUEUE_S_M = {"T1": 26.03052, "T2": 24.730442, "T3": 26.042979, "T4": 24.729291}
ASSESSMENT = {
    "T1": "partially_validated",
    "T2": "partially_validated",
    "T3": "not_assessed",
    "T4": "not_assessed",
}
# member -> candidate -> (mean difference m, p value) of the Holm-confirmed mean_queue_m.S tests
CONFIRMED = {
    "T1": {"I1": (-4.822959, 6.0932e-22), "I3": (-0.337717, 0.00174219)},
    "T2": {"I1": (-3.912817, 3.4531e-21), "I3": (-2.2458, 3.54792e-18)},
    "T3": {"I1": (-4.100427, 1.3835e-19), "I3": (-1.002577, 1.64589e-10)},
    "T4": {"I1": (-4.298915, 2.27283e-22)},
}
# from the realism validation records of the T1 and T2 baselines: NGSIM-derived (observed
# definition), and the same-definition value simulated by that baseline
OBSERVED_QUEUE_MEAN_S_M = 24.825706161137443
SIMULATED_SAME_DEFINITION_M = {"T1": 18.145215639810424, "T2": 17.33562638230648}


@pytest.fixture(scope="module")
def problem() -> ProblemStatement:
    return problem_statement_from_records(REPO)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_subject_is_the_s_approach_mean_queue(problem: ProblemStatement) -> None:
    assert problem.approach == "S" and problem.metric_name == "mean_queue_m.S"
    assert problem.entity_ids == ("approach:S",)
    assert problem.problem_id == "s-approach-mean-queue"


def test_the_simulated_baselines_are_copied_from_the_ensemble_record(problem) -> None:
    assert [m.member for m in problem.members] == ["T1", "T2", "T3", "T4"]
    for member in problem.members:
        assert member.baseline_mean == BASELINE_MEAN_QUEUE_S_M[member.member]
        assert member.unit == "m" and member.n == 20 and member.label == "simulated"
        assert member.unit_of_inference == (
            "n = 20 seeds on one vehicle realisation; conditional on it"
        )


def test_i1_and_i3_are_the_holm_confirmed_improvements_as_recorded(problem) -> None:
    for member in problem.members:
        found = {i.candidate: (i.mean_difference, i.p_value) for i in member.improvements}
        assert found == CONFIRMED[member.member]
        assert all(i.label == "simulated" and i.p_value <= i.holm_threshold
                   for i in member.improvements)  # fmt: skip


def test_the_baseline_is_not_validated_and_t3_t4_are_not_assessed(problem) -> None:
    assert {m.member: m.assessment for m in problem.members} == ASSESSMENT
    assert problem.baseline_validated is False
    conditions = " | ".join(problem.failure_conditions)
    assert "T3: baseline not assessed" in conditions and "T4: baseline not assessed" in conditions
    assert "No member's baseline is assessed as validated" in conditions  # the record's own words
    # each cited assessment's own conclusion is carried, verbatim
    assert conditions.count("It is not validated for estimating intervention effects.") == 2
    assert "no observed counterpart with metrics.py's definition" in conditions
    ensemble = json.loads(ENSEMBLE.read_text(encoding="utf-8"))
    for member in problem.members:
        if member.assessment == "not_assessed":
            assert member.assessment_source is None and member.validation is None
            continue
        entry = next(m for m in ensemble["members"] if m["id"] == member.member)
        record = json.loads(
            (REPO / f"experiments/records/{Path(entry['experiment_dir']).name}.json").read_text(
                encoding="utf-8"
            )
        )
        cited = record["report"]["baseline_assessment"]  # what the experiment record states
        assert member.assessment_source.path == cited["path"]
        assert member.assessment_source.sha256 == cited["file_sha256"] == sha(REPO / cited["path"])


def test_the_observed_quantities_are_ngsim_derived_and_kept_apart_from_the_metric(problem) -> None:
    queue = [q for q in problem.observed if q.key == "queue_mean.S"]
    assert {q.member for q in queue} == {"T1", "T2"}
    for q in queue:
        assert q.label == "derived" and q.unit == "m" and q.lower_bound
        assert q.value == OBSERVED_QUEUE_MEAN_S_M
        assert q.simulated_value == SIMULATED_SAME_DEFINITION_M[q.member]
        assert q.simulated_inside_observed_interval is False  # as the validation record says
        assert q.discrepancy == pytest.approx(q.simulated_value - q.value, abs=1e-4)
        assert q.comparability == "comparable-with-caveats" and q.limitations
        assert sha(REPO / q.source.path) == q.source.sha256
    keys = {q.key for q in problem.observed}
    assert {"queue_max.S", "count.S_through", "end_of_red_queue.S_all"} <= keys
    assert all(q.key.split(".", 1)[1].startswith("S") for q in problem.observed)
    through = next(q for q in problem.observed if q.key == "count.S_through")
    assert any("circular by construction" in text for text in through.limitations)


def test_the_statement_never_sets_one_definition_beside_the_other(problem) -> None:
    """The decision metric (SUMO's definition) has no observed counterpart; the observed queue is
    compared only with the same-definition simulated values, which lie outside its interval."""
    text = problem.statement
    assert "24.7-26.0 m in the replay baseline" in text
    assert "no observed counterpart with this definition" in text
    assert "24.8 m [18.9, 30.8] derived from NGSIM" in text
    assert "18.1 m (T1, outside the observed interval)" in text
    assert "17.3 m (T2, outside the observed interval)" in text


def test_the_unit_of_inference_rests_on_each_baseline_records_own_note(problem) -> None:
    for member in problem.members:
        assert "every seed has the same vehicles" in member.unit_basis
        assert member.unit_basis.startswith("experiments/records/ngsim-int2-realism-baseline-")


def test_every_record_read_is_listed_with_its_hash(problem: ProblemStatement) -> None:
    paths = [s.path for s in problem.sources]
    assert paths == sorted(set(paths))
    assert ENSEMBLE_RECORD in paths
    for source in problem.sources:
        assert sha(REPO / source.path) == source.sha256


def test_two_builds_are_byte_identical(problem: ProblemStatement) -> None:
    again = problem_statement_from_records(REPO)
    assert again.model_dump_json() == problem.model_dump_json()
    assert ProblemStatement.model_validate_json(problem.model_dump_json()) == problem


def rehashed(data: dict) -> dict:
    """The edited record with its hash recomputed, so only the content checks can refuse it."""
    content = {k: v for k, v in data.items() if k != "problem_sha256"}
    return data | {"problem_sha256": canonical_sha256(content)}


def test_the_statement_is_written_by_code_from_the_copied_values(problem) -> None:
    assert problem.statement == statement_of(problem)
    assert problem.failure_conditions == conditions_of(problem)
    data = json.loads(problem.model_dump_json())
    data["members"][0]["baseline_mean"] = 12.0
    with pytest.raises(ValidationError, match="statement_of"):
        ProblemStatement.model_validate(rehashed(data))


def edit(problem: ProblemStatement, change) -> dict:
    data = json.loads(problem.model_dump_json())
    change(data)
    return rehashed(data)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["observed"][0].update(label="simulated"), "derived"),
        (lambda d: d.update(baseline_validated=True), "baseline_validated"),
        (lambda d: d.update(failure_conditions=d["failure_conditions"][:4]), "conditions_of"),
        (lambda d: d["members"][0]["improvements"][1].update(p_value=0.5), "Holm"),
        (lambda d: d["members"][0]["improvements"][0].update(ci95_difference=[1.0, 2.0]),
         "not an improvement"),
        (lambda d: d["observed"][0].update(member="T9"), "no such validation record"),
        (lambda d: d["members"][0].update(assessment_source=None), "assessment is cited"),
        (lambda d: d.update(hypotheses=[], hypotheses_note=None), "why no hypothesis"),
    ],
)  # fmt: skip
def test_each_content_check_refuses_an_edit_even_with_a_recomputed_hash(
    problem, change, message
) -> None:
    with pytest.raises(ValidationError, match=message):
        ProblemStatement.model_validate(edit(problem, change))


def test_the_hash_alone_is_not_a_seal(problem: ProblemStatement) -> None:
    """An edit no content check covers passes with a recomputed hash: the record is checked by
    rebuilding it from the committed records, as test_two_builds_are_byte_identical does."""
    data = edit(problem, lambda d: d["observed"][1].update(quantity="edited"))
    assert ProblemStatement.model_validate(data) != problem


def _copy_committed(target: Path) -> None:
    patterns = [
        ENSEMBLE_RECORD,
        "experiments/records/ngsim-int2-realism-validity-*/validation/validation.json",
        "data/geometry/ngsim-lankershim-int2/replay_assessments/*.json",
        "experiments/records/ngsim-int2-realism-experiment-*.json",
        "experiments/records/ngsim-int2-realism-baseline-*.json",
    ]
    for pattern in patterns:
        for path in REPO.glob(pattern):
            copy = target / path.relative_to(REPO)
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(path.read_bytes())


def test_a_validation_record_edited_after_its_assessment_is_refused(tmp_path: Path) -> None:
    _copy_committed(tmp_path)
    path = (
        tmp_path
        / "experiments/records/ngsim-int2-realism-validity-20261028T135241Z"
        / ("validation/validation.json")
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    row = next(c for c in data["comparisons"] if c["key"] == "queue_mean.S")
    row["observed"]["value"] = 26.0
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ProblemStatementError, match="its assessment pins"):
        problem_statement_from_records(tmp_path)


def test_hypotheses_attach_as_ai_generated_text_never_as_evidence(problem) -> None:
    assert AttachedHypothesis.model_fields["kind"].default == "ai_generated"
    for hypothesis in problem.hypotheses:
        assert hypothesis.kind == "ai_generated"
        assert hypothesis.hypothesis_id not in {q.key for q in problem.observed}
        assert sha(REPO / hypothesis.source.path) == hypothesis.source.sha256
    if not problem.hypotheses:
        assert "not present" in (problem.hypotheses_note or "")
    with pytest.raises(ValidationError):
        AttachedHypothesis(hypothesis_id="H1", statement="text", kind="observed",
                           source={"path": "x.json", "sha256": "0" * 64})  # fmt: skip


def test_without_the_pinned_reply_no_hypothesis_is_invented(tmp_path: Path, problem) -> None:
    """A copy of the committed records only (no run directories): same numbers and statement,
    no hypotheses, and a note saying why."""
    _copy_committed(tmp_path)
    bare = problem_statement_from_records(tmp_path)
    assert bare.hypotheses == () and "not present" in bare.hypotheses_note
    assert [m.baseline_mean for m in bare.members] == list(BASELINE_MEAN_QUEUE_S_M.values())
    assert bare.statement == problem.statement
