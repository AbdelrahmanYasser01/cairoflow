"""Provenance nodes and links for intervention, experiment and report (ADR-033 addendum).

The perception half (cairoflow_perception.provenance) covers source to problem. This half lives
here because a proposal is identified by `cairoflow_simulation.validation.proposal_sha256`, the
hash `ValidationOutcome.proposal_sha256` already holds, and perception does not import simulation.

What each field establishes:
- `CandidateRecord.outcome.proposal_sha256`: the proposal an experiment's candidate was built from
  (outcomes written before validator 0.2.0 hold none, so their candidates link to nothing);
- `DecisionReport.experiment_id`: the experiment a report was written on, by id only (the report
  records no hash of the manifest).

No field ties an InterventionProposal to a ProblemStatement: proposals cite hypotheses
(`addresses_hypothesis_ids`), which are model text. So no link is made, and `walk` reports
"intervention <- problem_statement" as missing.
"""

from __future__ import annotations

import hashlib

from cairoflow_contracts.experiment import ExperimentManifest
from cairoflow_contracts.provenance import LinkEnd, NodeRef, ProvenanceLink
from cairoflow_contracts.reasoning import InterventionProposal
from cairoflow_contracts.report import DecisionReport
from cairoflow_simulation.validation import proposal_sha256


class ProvenanceError(ValueError):
    """Two records that should be tied are not; no link is made from them."""


def proposal_node(proposal: InterventionProposal) -> NodeRef:
    """A model's proposal: ai_generated model output, named by its proposal hash."""
    digest = proposal_sha256(proposal)
    return NodeRef(
        stage="intervention", record_type="InterventionProposal", record_id=digest,
        content_sha256=digest, hash_basis="proposal_sha256", category="ai_generated",
        evidence_kind="model_generated", model_output=True,
    )  # fmt: skip


def experiment_node(raw: bytes) -> tuple[NodeRef, ExperimentManifest]:
    """An experiment manifest as written (experiment_manifest.json), by the hash of its bytes."""
    manifest = ExperimentManifest.model_validate_json(raw)
    node = NodeRef(
        stage="experiment", record_type="ExperimentManifest", record_id=manifest.experiment_id,
        content_sha256=hashlib.sha256(raw).hexdigest(), hash_basis="file_bytes",
        category="simulated", evidence_kind="simulated",
    )  # fmt: skip
    return node, manifest


def report_node(report: DecisionReport) -> NodeRef:
    """A decision report, by `report_id`. Its numbers are simulator outputs; its model text is
    kept model_generated inside it (`model_text_kind`)."""
    return NodeRef(
        stage="decision_report", record_type="DecisionReport", record_id=report.report_id,
        content_sha256=hashlib.sha256(report.model_dump_json().encode("utf-8")).hexdigest(),
        hash_basis="model_dump_json", category="simulated", evidence_kind="simulated",
    )  # fmt: skip


def _end(node: NodeRef) -> LinkEnd:
    return LinkEnd(
        record_type=node.record_type, record_id=node.record_id,
        stated_sha256=node.content_sha256,
    )  # fmt: skip


def experiment_links(node: NodeRef, manifest: ExperimentManifest) -> list[ProvenanceLink]:
    """The experiment's link to each proposal its candidates name; none for a candidate whose
    outcome records no proposal hash."""
    if node.record_id != manifest.experiment_id:
        raise ProvenanceError("the node is not this experiment's")
    links = []
    for record in sorted(manifest.candidates, key=lambda r: r.intervention_id):
        digest = record.outcome.proposal_sha256
        if digest is None:
            continue
        parent = LinkEnd(record_type="InterventionProposal", record_id=digest, stated_sha256=digest)
        links.append(
            ProvenanceLink(
                child=_end(node), parent=parent, basis="CandidateRecord.outcome.proposal_sha256"
            )
        )
    return links


def report_links(report: DecisionReport, experiment: NodeRef) -> list[ProvenanceLink]:
    """The report's link to the experiment it names (by id: the report holds no manifest hash)."""
    if report.experiment_id != experiment.record_id:
        raise ProvenanceError(
            f"the report is about experiment {report.experiment_id}, not {experiment.record_id}"
        )
    parent = LinkEnd(record_type="ExperimentManifest", record_id=experiment.record_id)
    return [
        ProvenanceLink(child=_end(report_node(report)), parent=parent,
                       basis="DecisionReport.experiment_id"),
    ]  # fmt: skip
