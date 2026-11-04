"""Model text about a camera analysis: what code checks before anyone sees it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cairoflow_contracts.cv_diagnosis import CvDiagnosis, CvEvidenceItem, CvHypothesis
from cairoflow_perception.analysis_store import load_record
from cairoflow_reasoning.cv_prompt import (
    CV_SCHEMA,
    build_cv_request_body,
    citation_problems,
    evidence_items,
    parse_cv_reply,
    unsupported_numbers,
)
from cairoflow_reasoning.muse_adapter import MuseResponseError

SAMPLE = Path(__file__).resolve().parents[1] / "data/sample/analyses/ref-ngsim-int2-960-1020"


def item(evidence_id: str = "metric:flow", value: float | None = 120.0) -> CvEvidenceItem:
    return CvEvidenceItem(
        evidence_id=evidence_id, statement="s", value=value, unit="veh", evidence_kind="observed",
        status="ungated",
    )  # fmt: skip


def hypothesis(statement: str = "North queue", support: tuple[str, ...] = ("metric:flow",)):
    return CvHypothesis(
        hypothesis_id="H1", statement=statement, supporting_evidence_ids=support,
        contradicting_evidence_ids=(), confidence="low", how_to_check="Count again",
    )  # fmt: skip


def diagnosis(*hypotheses: CvHypothesis, summary: str = "Summary") -> CvDiagnosis:
    return CvDiagnosis(summary=summary, hypotheses=hypotheses, limitations=("one window",))


def test_every_fact_shown_to_the_model_comes_from_the_record() -> None:
    record = load_record(SAMPLE)
    items = evidence_items(record)
    ids = {i.evidence_id for i in items}
    assert {f"metric:{m.metric_id}" for m in record.metrics} <= ids
    assert any(i.evidence_id.startswith("movement:") for i in items)
    assert any(i.evidence_id.startswith("series-peak:") for i in items)


def test_the_schema_has_no_numeric_field() -> None:
    assert '"number"' not in json.dumps(CV_SCHEMA) and '"integer"' not in json.dumps(CV_SCHEMA)


def test_the_request_is_strict_and_not_stored() -> None:
    record = load_record(SAMPLE)
    body = build_cv_request_body(record, evidence_items(record), "muse-spark-1.3")
    assert body["text"]["format"]["strict"] is True
    assert body["store"] is False


def test_a_citation_of_unknown_evidence_is_reported() -> None:
    problems = citation_problems(diagnosis(hypothesis(support=("metric:nope",))), (item(),))
    assert problems == ("H1: unknown evidence id metric:nope",)


def test_a_hypothesis_citing_nothing_is_reported() -> None:
    problems = citation_problems(diagnosis(hypothesis(support=())), (item(),))
    assert problems == ("H1: cites no supporting evidence",)


def test_clean_citations_report_nothing() -> None:
    assert citation_problems(diagnosis(hypothesis()), (item(),)) == ()


def test_a_number_from_the_evidence_is_allowed() -> None:
    text = diagnosis(hypothesis("Flow of 120 veh"))
    assert unsupported_numbers(text, (item(),)) == ()


def test_an_invented_number_is_reported() -> None:
    text = diagnosis(hypothesis("A 90 second cycle and 45.5 m queue"))
    assert unsupported_numbers(text, (item(),)) == ("45.5", "90")


def test_ids_inside_words_are_not_numbers() -> None:
    assert unsupported_numbers(diagnosis(hypothesis("Hypothesis H2 on arm N1")), (item(),)) == ()


def test_a_reply_off_schema_is_refused() -> None:
    response = {
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": '{"summary": 1}'}]}
        ],
    }
    with pytest.raises(MuseResponseError, match="schema"):
        parse_cv_reply(response)
