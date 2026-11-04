"""Deterministic evidence checks on a Muse reply (P0-3): valid JSON is not a valid diagnosis.

Rules version 0.1.0. A hypothesis is **unsupported** when it
- cites no supporting evidence;
- cites an evidence id that is not in the bundle (supporting, contradicting or restated);
- rests only on assumed evidence (every supporting item has kind `assumed`).

It stays supported but is **flagged** when
- it claims a cause (claim_type `causal`, or causal wording in its statement) while naming no
  alternative explanation and no contradicting evidence: a correlation stated as a mechanism;
- its confidence is high although none of its supporting evidence is observed, or although it
  cites contradicting evidence;
- (prompt 0.3.0 onwards) it names no missing evidence, restates an item it does not cite, or
  gives a limiting factor outside the listed categories.

A proposal may go on to planning only when it addresses at least one supported hypothesis, every
hypothesis it names exists, and it cites at least one evidence id, all of them in the bundle.
Nothing is dropped: unsupported hypotheses and ineligible proposals stay in the record with their
reasons, and the validator rejects the proposals (simulation.validation).
"""

from __future__ import annotations

import re
from collections import Counter

from cairoflow_contracts.reasoning import (
    LIMITING_FACTORS,
    EvidenceBundle,
    GroundingReport,
    Hypothesis,
    HypothesisGrounding,
    ProposalGrounding,
    ReasoningOutput,
)

RULES_VERSION = "0.1.0"
CAUSAL_WORDING = re.compile(
    r"\b(caus(e|es|ed|ing)|because|due to|leads? to|led to|results? in|resulted in|responsible "
    r"for|driven by|is the reason)\b",
    re.IGNORECASE,
)


def _hypothesis(h: Hypothesis, bundle: EvidenceBundle, structured: bool) -> HypothesisGrounding:
    kinds = {item.evidence_id: item.kind for item in bundle.items}
    reasons: list[str] = []
    flags: list[str] = []
    if not h.supporting_evidence_ids:
        reasons.append("cites no supporting evidence")
    cited = (*h.supporting_evidence_ids, *h.contradicting_evidence_ids,
             *(o.evidence_id for o in h.observations))  # fmt: skip
    for eid in dict.fromkeys(cited):
        if eid not in kinds:
            reasons.append(f"cites evidence {eid!r}, which is not in the bundle")
    supporting = Counter(kinds[e] for e in h.supporting_evidence_ids if e in kinds)
    if supporting and set(supporting) == {"assumed"}:
        reasons.append("rests only on assumed evidence")

    causal = h.claim_type == "causal" or bool(CAUSAL_WORDING.search(h.statement))
    if causal and not h.alternative_explanations and not h.contradicting_evidence_ids:
        flags.append("claims a cause but names no alternative explanation and no contradicting "
                     "evidence: an association is not a mechanism")  # fmt: skip
    if h.confidence_label == "high" and "observed" not in supporting:
        flags.append("high confidence although none of its supporting evidence is observed")
    if h.confidence_label == "high" and h.contradicting_evidence_ids:
        flags.append("high confidence although it cites contradicting evidence")
    if structured:
        if not h.missing_evidence:
            flags.append("names no missing evidence")
        restated = {o.evidence_id for o in h.observations}
        unlinked = sorted(restated - set(h.supporting_evidence_ids)
                          - set(h.contradicting_evidence_ids))  # fmt: skip
        if unlinked:
            flags.append(f"restates evidence it does not cite: {', '.join(unlinked)}")
        if h.limiting_factor not in LIMITING_FACTORS:
            flags.append(f"limiting factor {h.limiting_factor!r} is not a listed category")
    return HypothesisGrounding(
        hypothesis_id=h.hypothesis_id,
        status="unsupported" if reasons else "supported",
        reasons=tuple(reasons),
        flags=tuple(flags),
        supporting_kinds=dict(sorted(supporting.items())),
    )


def ground(output: ReasoningOutput, bundle: EvidenceBundle, request_key: str) -> GroundingReport:
    """The grounding verdict on every hypothesis and proposal of one reply."""
    structured = any(h.limiting_factor is not None for h in output.hypotheses)  # prompt >= 0.3.0
    hypotheses = tuple(_hypothesis(h, bundle, structured) for h in output.hypotheses)
    status = {g.hypothesis_id: g.status for g in hypotheses}
    known = {item.evidence_id for item in bundle.items}
    proposals: list[ProposalGrounding] = []
    for p in output.interventions:
        reasons: list[str] = []
        if not p.addresses_hypothesis_ids:
            reasons.append("addresses no hypothesis")
        for hid in p.addresses_hypothesis_ids:
            if hid not in status:
                reasons.append(f"addresses hypothesis {hid!r}, which the reply does not state")
        supported = tuple(h for h in p.addresses_hypothesis_ids if status.get(h) == "supported")
        if p.addresses_hypothesis_ids and not supported:
            reasons.append("addresses only unsupported hypotheses")
        if not p.evidence_ids:
            reasons.append("cites no evidence")
        for eid in p.evidence_ids:
            if eid not in known:
                reasons.append(f"cites evidence {eid!r}, which is not in the bundle")
        proposals.append(ProposalGrounding(intervention_id=p.intervention_id, eligible=not reasons,
                                           reasons=tuple(reasons),
                                           supported_hypothesis_ids=supported))  # fmt: skip
    competing = sum(s == "supported" for s in status.values()) >= 2
    notes = (
        []
        if competing
        else ["fewer than two hypotheses are supported: the diagnosis has no competing explanation"]
    )
    if not structured:
        notes.append(
            "the reply predates prompt 0.3.0: it does not separate observations from inference "
            "or name missing evidence, so those checks were not applied"
        )
    return GroundingReport(
        rules_version=RULES_VERSION,
        bundle_id=bundle.bundle_id,
        request_key=request_key,
        hypotheses=hypotheses,
        proposals=tuple(proposals),
        competing_hypotheses=competing,
        missing_evidence=tuple(
            dict.fromkeys(m for h in output.hypotheses for m in h.missing_evidence)
        ),  # fmt: skip
        notes=tuple(notes),
    )
