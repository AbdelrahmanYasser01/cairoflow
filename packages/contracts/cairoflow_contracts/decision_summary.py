"""The decision summary (schema 0.1.0, format decision_summary/1): the decision on one page.

Everything here is copied by code from the records pinned in data/demo/ (written by
scripts/build_decision_summary.py); nothing is computed, estimated or defaulted, so a value a record
does not hold stays None. Each block keeps its own kind of evidence apart:

- `model_generated`: the hypotheses and proposals are Muse Spark's words. They carry no metric.
- `simulated`: every number comes from SUMO runs through the evaluation code. None is observed.
- code's checks on model output (grounding, the validator) and the recurrence counts are computed
  by deterministic code, and are carried beside the text they judge, never merged into it.

The summary holds no pixels and needs none: a screen can show it from this one JSON file.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from cairoflow_contracts.evidence import EvidenceKind, EvidenceModel, Sha256, _check_sha256
from cairoflow_contracts.meta_depth import EvidenceRecurrence
from cairoflow_contracts.reasoning import (
    Hypothesis,
    HypothesisGrounding,
    InterventionProposal,
    ProposalGrounding,
    ValidationOutcome,
)
from cairoflow_contracts.report import BaselineAssessment

SUMMARY_FORMAT = "decision_summary/1"

ModelGenerated = Literal["model_generated"]
Simulated = Literal["simulated"]


class SourceFile(EvidenceModel):
    """One file the summary was copied from, as it was when the summary was built."""

    role: str = Field(min_length=1, description="its role in the manifest or pin file")
    path: str = Field(min_length=1, description="repository-relative POSIX path")
    sha256: Sha256

    @field_validator("sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)


class RecordedOutcome(EvidenceModel):
    """What the deterministic selection rule recorded, on simulated results."""

    evidence_kind: Simulated = "simulated"
    outcome: Literal["none_selected", "selected"]
    selected_intervention_id: str | None
    selection_reason: str = Field(min_length=1)
    recommended_intervention_id: str | None
    recommendation: str = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> RecordedOutcome:
        if (self.outcome == "selected") != (self.selected_intervention_id is not None):
            raise ValueError("outcome and selected_intervention_id must agree")
        return self


class Citation(EvidenceModel):
    """One evidence item a hypothesis cites, read back from the bundle the model was sent."""

    evidence_id: str = Field(min_length=1)
    stance: Literal["supporting", "contradicting"]
    kind: EvidenceKind | None = Field(description="as the bundle holds it; None when not in it")
    summary: str | None = Field(description="as the bundle holds it; None when not in it")
    recurrence: EvidenceRecurrence | None = Field(
        description="how often replies to this request cite it; None when not counted"
    )


class HypothesisRecurrence(EvidenceModel):
    """How far a hypothesis recurs across replies to the same request (computed by code)."""

    cluster_id: str = Field(min_length=1)
    in_replies: int = Field(ge=1)
    n_replies: int = Field(ge=1)
    shared_evidence_ids: tuple[str, ...]


class HypothesisView(EvidenceModel):
    """One Muse hypothesis with code's checks beside it."""

    evidence_kind: ModelGenerated = "model_generated"
    hypothesis: Hypothesis
    citations: tuple[Citation, ...]
    grounding: HypothesisGrounding | None
    recurrence: HypothesisRecurrence | None


class CandidateView(EvidenceModel):
    """One Muse proposal, and what deterministic code did with it."""

    evidence_kind: ModelGenerated = "model_generated"
    intervention_id: str = Field(min_length=1)
    proposal: InterventionProposal
    grounding: ProposalGrounding | None
    validator: ValidationOutcome | None
    eligibility_status: str | None = Field(description="the evaluation's status for the candidate")
    eligibility_reasons: tuple[str, ...]


class SimulationScope(EvidenceModel):
    """Which simulated runs the metrics come from."""

    evidence_kind: Simulated = "simulated"
    run_id: str = Field(min_length=1)
    created_at_utc: datetime
    seeds: tuple[int, ...]
    replay_window_s: tuple[float, float] = Field(description="simulation seconds compared")
    baseline_manifest_sha256: Sha256

    @field_validator("baseline_manifest_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)


class SimulatedMetric(EvidenceModel):
    """One candidate against the baseline on one metric, paired by seed (SUMO output)."""

    evidence_kind: Simulated = "simulated"
    candidate: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    better: str = Field(min_length=1, description="'lower' or 'higher'")
    n_seeds: int = Field(ge=1)
    baseline_mean: float
    candidate_mean: float
    mean_difference: float
    sd_difference: float
    ci95_difference: tuple[float, float]
    verdict: str = Field(min_length=1)
    improvement_confirmed: bool | None = Field(
        description="survived the Holm correction; None when no improvement test was run"
    )


class EnsembleMember(EvidenceModel):
    """One signal-timeline version the candidates were re-run on."""

    member: str = Field(min_length=1)
    version: str = Field(min_length=1)
    required: bool
    baseline_assessment: str = Field(min_length=1)
    timeline_id: str = Field(min_length=1)
    timeline_sha256: Sha256
    gate_passed: bool

    @field_validator("timeline_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)


class EnsembleCandidateMember(EvidenceModel):
    """One candidate on one member, as the verdict recorded it."""

    member: str = Field(min_length=1)
    required: bool
    supported: bool
    blocked_by: tuple[str, ...]
    status: str = Field(min_length=1)
    worsens: tuple[str, ...]
    confirmed: tuple[str, ...]
    held_off: tuple[str, ...]
    mean_delay_difference_s: float | None
    mean_delay_ci95_s: tuple[float, float] | None


class DirectionDisagreement(EvidenceModel):
    """A metric whose verdict differs between members; `opposite` when the signs differ."""

    metric_name: str = Field(min_length=1)
    verdicts: dict[str, str]
    mean_difference: dict[str, float]
    ci95_difference: dict[str, tuple[float, float]]
    opposite: bool


class EnsembleCandidateVerdict(EvidenceModel):
    candidate: str = Field(min_length=1)
    verdict: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    supported_on: tuple[str, ...]
    not_supported_on: tuple[str, ...]
    common_confirmed: tuple[str, ...]
    robust_harm: tuple[str, ...]
    direction_disagreements: tuple[DirectionDisagreement, ...]
    members: tuple[EnsembleCandidateMember, ...]


class EnsembleSummary(EvidenceModel):
    """The pre-registered timeline ensemble's verdicts; nothing is pooled across members."""

    evidence_kind: Simulated = "simulated"
    rule: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    members: tuple[EnsembleMember, ...]
    candidates: tuple[EnsembleCandidateVerdict, ...]
    robust_selection: str | None
    selection_reason: str = Field(min_length=1)
    recommended: str | None
    recommendation: str = Field(min_length=1)


class DecisionSummary(EvidenceModel):
    """The recorded decision, what it rests on, and what it cannot say."""

    record_format: Literal["decision_summary/1"] = SUMMARY_FORMAT
    demo_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    outcome: RecordedOutcome
    baseline: BaselineAssessment
    hypotheses: tuple[HypothesisView, ...]
    candidates: tuple[CandidateView, ...]
    simulation: SimulationScope
    metrics: tuple[SimulatedMetric, ...]
    ensemble: EnsembleSummary | None
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    sources: tuple[SourceFile, ...]

    @model_validator(mode="after")
    def _linked(self) -> DecisionSummary:
        ids = {candidate.intervention_id for candidate in self.candidates}
        selected = self.outcome.selected_intervention_id
        if selected is not None and selected not in ids:
            raise ValueError("the selected intervention must be one of the candidates")
        unknown = {metric.candidate for metric in self.metrics} - ids
        if unknown:
            raise ValueError(f"metrics name unknown candidates: {sorted(unknown)}")
        return self
