"""The camera-analysis diagnosis request: structured evidence in, competing hypotheses out.

Muse Spark sees only facts copied from an `AnalysisRecord` by code, each with an id, what kind of
evidence it is and whether a gate covered it. It proposes hypotheses that cite those ids. The strict
schema has no numeric field. Two checks run on the reply: every cited id must exist, and any number
written in a statement must appear in the evidence (rule 6: model text never becomes a measurement).
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from cairoflow_contracts.analysis_record import AnalysisRecord
from cairoflow_contracts.cv_diagnosis import CvDiagnosis, CvEvidenceItem
from cairoflow_reasoning.muse_adapter import MuseResponseError, extract_output
from cairoflow_reasoning.prompts import _object

CV_PROMPT_VERSION = "cv-0.1.0"
CV_SCHEMA_NAME = "cairoflow_cv_diagnosis"
CV_INSTRUCTIONS = (
    "You are assisting a traffic engineer. Below is evidence about one signalised junction, "
    "produced by a computer-vision pipeline from a fixed overhead camera. Each item has an id, a "
    "statement, a value, what kind of evidence it is and whether an independent gate covered it. "
    "A status of gate_fail or not_available means the value is withheld: do not reason from it or "
    "guess it. Propose two to four competing hypotheses about what is limiting this junction's "
    "operation. Each must cite the evidence ids that support it and any that contradict it, and "
    "say what further measurement would settle it. Use only these facts: state no number that is "
    "not in the evidence, do not invent signal timings, and do not recommend an intervention. "
    "Give your confidence as low, medium or high. In limitations, say what this evidence cannot "
    "show. Answer only with JSON in the given schema."
)
CV_SCHEMA: dict[str, Any] = _object(
    {
        "summary": {"type": "string"},
        "hypotheses": {
            "type": "array",
            "items": _object(
                {
                    "hypothesis_id": {"type": "string"},
                    "statement": {"type": "string"},
                    "supporting_evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "contradicting_evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "how_to_check": {"type": "string"},
                }
            ),
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
    }
)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")


def evidence_items(record: AnalysisRecord) -> tuple[CvEvidenceItem, ...]:
    """The facts Muse is shown, copied from the record by code."""
    items = [
        CvEvidenceItem(
            evidence_id=f"metric:{m.metric_id}",
            statement=f"{m.label} ({m.scope})",
            value=m.value,
            unit=m.unit,
            evidence_kind=m.evidence_kind,
            status=m.status,
        )
        for m in record.metrics
    ]
    for cell in record.movements:
        items.append(
            CvEvidenceItem(
                evidence_id=f"movement:{cell.approach}-{cell.exit_arm}",
                statement=f"Vehicles from arm {cell.approach} leaving by arm {cell.exit_arm} "
                f"({cell.turn})",
                value=None if cell.count is None else float(cell.count),
                unit="veh",
                evidence_kind="approximated",
                status=cell.status,
            )
        )
    for series in record.series:
        values = [v for v in series.values if v is not None]
        if values:
            items.append(
                CvEvidenceItem(
                    evidence_id=f"series-peak:{series.series_id}",
                    statement=f"Highest {series.bin_s:g} s bin of: {series.label}",
                    value=max(values),
                    unit=series.unit,
                    evidence_kind=series.evidence_kind,
                    status=series.status,
                )
            )
    return tuple(items)


def build_cv_request_body(
    record: AnalysisRecord,
    items: tuple[CvEvidenceItem, ...],
    model: str,
    *,
    effort: str = "medium",
    max_output_tokens: int = 8000,
    store: bool = False,
) -> dict[str, Any]:
    evidence = {
        "site": record.source.site_name,
        "analysed_seconds": record.frame_count / record.fps,
        "disclosures": list(record.disclosures),
        "evidence": [item.model_dump(mode="json", exclude={"schema_version"}) for item in items],
    }
    return {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": CV_INSTRUCTIONS},
                    {"type": "input_text", "text": json.dumps(evidence, sort_keys=True)},
                ],
            }
        ],
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": CV_SCHEMA_NAME,
                "schema": CV_SCHEMA,
                "strict": True,
            }
        },
        "store": store,
    }


def parse_cv_reply(response: dict[str, Any]) -> CvDiagnosis:
    text = extract_output(response)
    try:
        return CvDiagnosis.model_validate_json(text)
    except (ValidationError, json.JSONDecodeError) as error:
        count = error.error_count() if isinstance(error, ValidationError) else 1
        raise MuseResponseError(f"the reply does not match the schema ({count} errors)") from error


def validate_cv_reply(response: dict[str, Any]) -> None:
    parse_cv_reply(response)


def citation_problems(diagnosis: CvDiagnosis, items: tuple[CvEvidenceItem, ...]) -> tuple[str, ...]:
    """Citations of evidence that doesn't exist, and hypotheses that cite nothing."""
    known = {item.evidence_id for item in items}
    problems = []
    for hypothesis in diagnosis.hypotheses:
        cited = (*hypothesis.supporting_evidence_ids, *hypothesis.contradicting_evidence_ids)
        if not hypothesis.supporting_evidence_ids:
            problems.append(f"{hypothesis.hypothesis_id}: cites no supporting evidence")
        problems += [
            f"{hypothesis.hypothesis_id}: unknown evidence id {evidence_id}"
            for evidence_id in cited
            if evidence_id not in known
        ]
    return tuple(problems)


def unsupported_numbers(
    diagnosis: CvDiagnosis, items: tuple[CvEvidenceItem, ...]
) -> tuple[str, ...]:
    """Numbers in the model's prose that no evidence value matches."""
    values = {f"{item.value:g}" for item in items if item.value is not None}
    texts = [diagnosis.summary]
    for hypothesis in diagnosis.hypotheses:
        texts += [hypothesis.statement, hypothesis.how_to_check]
    found = []
    for text in texts:
        for match in _NUMBER.findall(text):
            if f"{float(match):g}" not in values:
                found.append(match)
    return tuple(sorted(set(found)))
