"""The decision report (schema version 0.1.0): every number traceable, model text labelled.

Every number in the report is a `ReportNumber` whose first reference yields
exactly its value and whose other references are the evidence behind it. Sources:

- `metric`: one `MetricResult` (`metric:<arm>:<seed>:<name>`), computed from a SUMO output file.
- `comparison`: one field of a `PairedComparison` (`comparison:<candidate>:<name>:<field>`),
  followed by the metric references of every paired seed.
- `world_model`: one movement value (`world_model:movement:<approach><exit>:<field>`), followed
  by the tracks counted in it (`track:<id>`).
- `validated_input`: a duration the validator accepted (`outcome:<id>:phase:<index>` or
  `outcome:<id>:cycle_s`); proposed by the model, checked by code, simulated by SUMO.
- `settings`: an experiment setting (`settings:<field>`).

No number comes from Muse output: hypotheses and rationales are text, labelled model_generated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from cairoflow_contracts.evidence import EvidenceKind, EvidenceModel
from cairoflow_contracts.reasoning import GroundingReport, Hypothesis


def _omit(data: dict[str, Any], model: EvidenceModel, unset: dict[str, Any]) -> dict[str, Any]:
    """Leave fields added after schema 0.1.0 out of the JSON while unset, so reports made before
    them serialise, and hash, exactly as they did."""
    for name, value in unset.items():
        if getattr(model, name) == value:
            data.pop(name, None)
    return data


NumberSource = Literal["metric", "comparison", "world_model", "validated_input", "settings"]


class ReportNumber(EvidenceModel):
    number_id: str = Field(pattern=r"^n[0-9]+$")
    label: str = Field(min_length=1)
    value: float
    unit: str
    kind: EvidenceKind
    source: NumberSource
    refs: tuple[str, ...] = Field(min_length=1, description="refs[0] yields the value")

    @model_validator(mode="after")
    def _not_model_generated(self) -> ReportNumber:
        if self.kind == "model_generated":
            raise ValueError("a report number never comes from model output")
        return self


class CandidateFinding(EvidenceModel):
    """One proposal: what the model said (text), what the validator and SUMO made of it."""

    intervention_id: str
    status: Literal["accepted", "rejected"]
    validator_reasons: tuple[str, ...]
    rationale: str | None = Field(description="model_generated text")
    expected_tradeoffs: tuple[str, ...] = Field(description="model_generated text")
    addresses_hypothesis_ids: tuple[str, ...]
    verdicts: dict[str, Literal["improves", "worsens", "inconclusive", "uninformative"]]
    number_ids: tuple[str, ...]
    simulation_status: (
        Literal["not_simulated", "compared", "incomplete", "invalid_simulation"] | None
    ) = Field(default=None, description="eligibility of its SUMO runs; None in older reports")
    simulation_reasons: tuple[str, ...] = Field(
        default=(), description="why it was not compared (unfinished vehicles, broken runs)"
    )
    confirmed_improvements: tuple[str, ...] = Field(
        default=(), description="metrics whose improvement survives Holm's correction"
    )
    unverified_assumptions: tuple[str, ...] = Field(
        default=(),
        description="the proposal's preconditions: model-stated, never checked by code",
    )
    grounding_reasons: tuple[str, ...] = Field(
        default=(), description="why the grounding gate kept it from planning, if it did"
    )

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit(handler(self), self, {"unverified_assumptions": (), "grounding_reasons": ()})


BaselineStatus = Literal["validated", "partially_validated", "not_validated", "not_assessed"]


class BaselineAssessment(EvidenceModel):
    """How far observations support the simulated baseline: an engineering judgement, recorded
    with the evidence it rests on. Only `validated` lets a selected candidate be recommended."""

    baseline_manifest_sha256: str = Field(description="the baseline this assessment is about")
    status: BaselineStatus
    summary: str = Field(min_length=1)
    findings: tuple[str, ...]
    evidence: dict[str, str] = Field(description="committed record path -> SHA-256")
    assessed_on: str = Field(description="ISO date; see the document named in `documented_in`")
    documented_in: str


class DecisionReport(EvidenceModel):
    report_id: str
    experiment_id: str
    created_at_utc: datetime
    objective: str = Field(description="how candidates are judged; no weighted score")
    selection_rule: str
    selected_intervention_id: str | None
    selection_reason: str
    model_text_kind: Literal["model_generated"] = "model_generated"
    hypotheses: tuple[Hypothesis, ...] = Field(description="model_generated text")
    candidates: tuple[CandidateFinding, ...]
    numbers: tuple[ReportNumber, ...]
    limitations: tuple[str, ...]
    reproducibility: dict[str, str] = Field(description="artifact or id name -> hash or id")
    baseline_assessment: BaselineAssessment | None = Field(
        default=None, description="None in older reports and when nobody assessed the baseline"
    )
    recommended_intervention_id: str | None = Field(
        default=None, description="the selection, only when the baseline is validated"
    )
    recommendation: str = Field(default="", description="why something is or is not recommended")
    grounding: GroundingReport | None = Field(
        default=None, description="code's evidence checks on the reply (P0-3); None before them"
    )
    data_sources: tuple[str, ...] = Field(
        default=(), description="where the world model's evidence comes from, source by source"
    )

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return _omit(handler(self), self, {"grounding": None, "data_sources": ()})

    @model_validator(mode="after")
    def _unique_numbers(self) -> DecisionReport:
        ids = [number.number_id for number in self.numbers]
        if len(set(ids)) != len(ids):
            raise ValueError("number ids must be unique")
        known = set(ids)
        for candidate in self.candidates:
            if not set(candidate.number_ids) <= known:
                raise ValueError(f"{candidate.intervention_id} lists unknown number ids")
        return self
