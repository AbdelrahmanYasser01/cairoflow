"""Muse Spark's reading of a camera analysis (schema 0.1.0).

Everything here except `evidence` and the `citation_problems` is model-generated text: a hypothesis
for a person to test, never a measurement. No number in a statement is a result; deterministic code
checks that each citation exists and flags any number the evidence doesn't contain.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from cairoflow_contracts.evidence import EvidenceKind, EvidenceModel, Provenance, Sha256


class CvEvidenceItem(EvidenceModel):
    """One fact the model was given, copied from the analysis record by code."""

    evidence_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    value: float | None = None
    unit: str | None = None
    evidence_kind: EvidenceKind
    status: str = Field(min_length=1)


class CvHypothesis(EvidenceModel):
    hypothesis_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    confidence: Literal["low", "medium", "high"]
    how_to_check: str = Field(min_length=1, description="what measurement would settle it")


class CvDiagnosis(EvidenceModel):
    summary: str = Field(min_length=1)
    hypotheses: tuple[CvHypothesis, ...]
    limitations: tuple[str, ...]


class CvDiagnosisReplicate(EvidenceModel):
    """One independent reply, with what code found wrong with it."""

    replicate: int = Field(ge=1)
    status: Literal["ok", "failed"]
    failure: str | None = None
    request_key: Sha256
    from_cache: bool
    cost_usd: float | None = Field(None, ge=0)
    citation_problems: tuple[str, ...] = ()
    unsupported_numbers: tuple[str, ...] = ()
    diagnosis: CvDiagnosis | None = None


class CvDiagnosisRecord(EvidenceModel):
    """Every reply to one request about one analysis."""

    label: str = Field(min_length=1)
    analysis_id: str = Field(min_length=1)
    analysis_record_sha256: Sha256
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    created_at_utc: datetime
    evidence: tuple[CvEvidenceItem, ...]
    replicates: tuple[CvDiagnosisReplicate, ...]
    provenance: Provenance
