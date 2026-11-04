"""Reasoning records: the evidence the model sees, what it proposes, and the validator's verdict
(schema version 0.1.0).

Muse Spark proposes; deterministic code decides. Hypotheses and proposals are
`model_generated` and carry no metric values: expected effects are words, and every number in a
report comes from SUMO output or the world model. A proposal's `type` is a free string here on
purpose, so that the validator, not the schema, is what enforces the allowlist (ADR-015).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from cairoflow_contracts.evidence import Arm, EvidenceKind, EvidenceModel, Provenance

ALLOWED_INTERVENTIONS: tuple[str, ...] = ("signal_timing",)  # ADR-015
# signal_timing bounds, as for the hand-authored SignalPlan (network.py): green 5-90 s. Cycle
# 30-180 s is a common practical range for fixed-time plans; both are assumptions, not standards.
MIN_GREEN_S, MAX_GREEN_S = 5.0, 90.0
MIN_CYCLE_S, MAX_CYCLE_S = 30.0, 180.0
EVIDENCE_ID = r"^[a-z]+(\.[A-Za-z0-9_@-]+)+$"


class EvidenceItem(EvidenceModel):
    """One fact the model may cite, summarised by code from a world-model or network value."""

    evidence_id: str = Field(pattern=EVIDENCE_ID, description="e.g. 'mv.SN' or 'net.S.arm_length'")
    kind: EvidenceKind = Field(description="observed, approximated, assumed, ... as recorded")
    summary: str = Field(min_length=1)


class PhaseOption(EvidenceModel):
    """One phase of the compiled cycle and whether a signal_timing proposal may change it."""

    phase_name: str
    duration_s: float = Field(gt=0)
    green_arms: tuple[Arm, ...]
    green_left_arms: tuple[Arm, ...] = Field((), description="arms whose protected left is green")
    adjustable: bool = Field(description="only green phases; clearance intervals are fixed")
    min_green_s: float | None = None
    max_green_s: float | None = None


class EvidenceBundle(EvidenceModel):
    bundle_id: str = Field(description="first 16 hex digits of the SHA-256 of the content")
    clip_id: str
    world_model_sha256: str
    net_sha256: str
    program_id: str
    phases: tuple[PhaseOption, ...]
    items: tuple[EvidenceItem, ...]
    limitations: tuple[str, ...]
    conventions: tuple[str, ...] = Field(
        (),
        description="naming conventions the model must follow (e.g. what arm N means); left out "
        "of the JSON when empty, so bundles made before the field existed keep their ids",
    )

    @model_serializer(mode="wrap")
    def _omit_empty_conventions(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if not self.conventions:
            data.pop("conventions", None)
        return data

    @model_validator(mode="after")
    def _unique(self) -> EvidenceBundle:
        ids = [item.evidence_id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError("evidence ids must be unique")
        return self


# --- What the model returns (mirrored by the JSON schema in cairoflow_reasoning.prompts) --------

# What a hypothesis says limits the junction (prompt 0.3.0 onwards), so replies can be compared.
LIMITING_FACTORS: tuple[str, ...] = (
    "protected_left_capacity",
    "through_capacity",
    "cross_street_capacity",
    "green_split",
    "cycle_length",
    "clearance_or_lost_time",
    "demand_peak",
    "downstream_blocking",
    "data_or_estimation_artifact",
    "other",
)
ClaimType = Literal["causal", "associative", "descriptive"]


class ObservedFact(EvidenceModel):
    """One cited evidence item restated without inference (prompt 0.3.0 onwards)."""

    evidence_id: str
    statement: str = Field(min_length=1)


# Hypothesis fields added with prompt 0.3.0 and their unset values: left out of the JSON when
# unset, so earlier replies, bundles and reports serialise (and hash) exactly as before.
_HYPOTHESIS_0_3_0 = {
    "limiting_factor": None,
    "claim_type": None,
    "observations": (),
    "inference": None,
    "missing_evidence": (),
}


class Hypothesis(EvidenceModel):
    hypothesis_id: str = Field(pattern=r"^H[1-9][0-9]?$")
    statement: str = Field(min_length=1)
    supporting_evidence_ids: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...]
    assumptions: tuple[str, ...]
    confidence_label: Literal["low", "medium", "high"]
    alternative_explanations: tuple[str, ...]
    limiting_factor: str | None = Field(None, description="one of LIMITING_FACTORS (0.3.0)")
    claim_type: ClaimType | None = Field(None, description="causal only for a stated mechanism")
    observations: tuple[ObservedFact, ...] = Field((), description="cited evidence, restated")
    inference: str | None = Field(None, description="what is concluded beyond the evidence")
    missing_evidence: tuple[str, ...] = Field((), description="what would confirm or refute it")

    @model_serializer(mode="wrap")
    def _omit_unset_0_3_0(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for name, unset in _HYPOTHESIS_0_3_0.items():
            if getattr(self, name) == unset:
                data.pop(name, None)
        return data


class PhaseChange(EvidenceModel):
    phase_name: str = Field(min_length=1)
    green_s: float


class InterventionProposal(EvidenceModel):
    intervention_id: str = Field(pattern=r"^I[1-9][0-9]?$")
    type: str = Field(pattern=r"^[a-z_]+$", description="checked against the allowlist later")
    target_program: str
    phase_changes: tuple[PhaseChange, ...]
    rationale: str = Field(min_length=1)
    expected_tradeoffs: tuple[str, ...] = Field(description="words only; no predicted metrics")
    preconditions: tuple[str, ...]
    addresses_hypothesis_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]


class ReasoningOutput(EvidenceModel):
    """The model's reply, after schema validation: at least two of each."""

    hypotheses: tuple[Hypothesis, ...] = Field(min_length=2)
    interventions: tuple[InterventionProposal, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def _unique_ids(self) -> ReasoningOutput:
        for label, ids in (
            ("hypothesis", [h.hypothesis_id for h in self.hypotheses]),
            ("intervention", [i.intervention_id for i in self.interventions]),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{label} ids must be unique")
        return self


class ReasoningResult(EvidenceModel):
    """One diagnosis: the model's output plus where it came from and what was wrong with it."""

    bundle_id: str
    request_key: str
    response_id: str | None
    request_id: str | None
    model: str
    prompt_version: str
    output: ReasoningOutput
    problems: tuple[str, ...] = Field(description="citations of unknown evidence or hypotheses")
    usage: dict[str, int]
    estimated_cost_usd: float = Field(ge=0)
    provenance: Provenance

    @model_validator(mode="after")
    def _model_generated(self) -> ReasoningResult:
        if self.provenance.kind != "model_generated":
            raise ValueError("a reasoning result is model_generated evidence")
        return self


class HypothesisGrounding(EvidenceModel):
    """Code's verdict on whether one hypothesis rests on the evidence it cites."""

    hypothesis_id: str
    status: Literal["supported", "unsupported"]
    reasons: tuple[str, ...] = Field(description="why it is unsupported; empty when supported")
    flags: tuple[str, ...] = Field(description="weaknesses that do not make it unsupported")
    supporting_kinds: dict[str, int] = Field(description="evidence kind -> supporting items cited")

    @model_validator(mode="after")
    def _consistent(self) -> HypothesisGrounding:
        if (self.status == "supported") == bool(self.reasons):
            raise ValueError("an unsupported hypothesis has reasons; a supported one none")
        return self


class ProposalGrounding(EvidenceModel):
    """Whether one proposal may go to planning: it must address a supported hypothesis."""

    intervention_id: str
    eligible: bool
    reasons: tuple[str, ...] = Field(description="why it may not; empty when eligible")
    supported_hypothesis_ids: tuple[str, ...]

    @model_validator(mode="after")
    def _consistent(self) -> ProposalGrounding:
        if self.eligible == bool(self.reasons):
            raise ValueError("an ineligible proposal has reasons; an eligible one none")
        return self


class GroundingReport(EvidenceModel):
    """Deterministic evidence checks on one reply (cairoflow_reasoning.grounding, P0-3). A valid
    JSON reply is not a valid diagnosis: only proposals that address a supported hypothesis go
    on to the validator and the simulator."""

    rules_version: str
    bundle_id: str
    request_key: str
    hypotheses: tuple[HypothesisGrounding, ...]
    proposals: tuple[ProposalGrounding, ...]
    competing_hypotheses: bool = Field(description="at least two hypotheses are supported")
    missing_evidence: tuple[str, ...] = Field(description="every missing item the model named")
    notes: tuple[str, ...]


class ValidationOutcome(EvidenceModel):
    """The validator's verdict on one proposal. Accepted proposals become simulation candidates."""

    intervention_id: str
    status: Literal["accepted", "rejected"]
    reasons: tuple[str, ...] = Field(description="why it was rejected; empty when accepted")
    program_id: str
    phase_durations_s: tuple[float, ...] | None = Field(
        description="the candidate cycle, in compiled phase order, when accepted"
    )
    cycle_s: float | None
    baseline_cycle_s: float
    validated_at_utc: datetime
    validator_version: str | None = Field(None, description="None in outcomes before 0.2.0")
    proposal_sha256: str | None = Field(
        None, description="the proposal's canonical JSON, so a candidate names what it came from"
    )

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)  # outcomes made before these fields serialise as they did
        for name in ("validator_version", "proposal_sha256"):
            if getattr(self, name) is None:
                data.pop(name, None)
        return data

    @model_validator(mode="after")
    def _consistent(self) -> ValidationOutcome:
        accepted = self.status == "accepted"
        if accepted == bool(self.reasons) or accepted != (self.phase_durations_s is not None):
            raise ValueError(
                "accepted outcomes have durations and no reasons; rejected the reverse"
            )
        return self
