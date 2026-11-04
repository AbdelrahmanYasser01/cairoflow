"""What recurs across n replies to the same Muse request: evidence ids, hypotheses, proposal types.

Code only, on stored replies (`ReasoningOutput`); no model is called and no reply text is
interpreted. Every count is "in how many replies", never "how many citations":

- evidence: an id counts once per reply that cites it in support of any hypothesis, against any
  hypothesis, or anywhere at all (hypotheses and proposals);
- hypotheses: two hypotheses of DIFFERENT replies are linked when the Jaccard overlap of their
  supporting evidence sets is at least the threshold (0.5); clusters are the connected
  components (single link), so a chain can join hypotheses that do not overlap directly. A
  hypothesis with no supporting evidence links to nothing. Statement wording is never compared;
- proposals: the intervention types each reply proposes.
Ordering is fixed (most replies first, then reply and hypothesis order), so two runs on the same
replies give the same record.
"""

from __future__ import annotations

from collections.abc import Sequence

from cairoflow_contracts.meta_depth import (
    EvidenceRecurrence,
    HypothesisCluster,
    HypothesisMember,
    MuseRecurrence,
    ReplyRef,
    TypeRecurrence,
)
from cairoflow_contracts.reasoning import ReasoningOutput

JACCARD_THRESHOLD = 0.5
RULE = (
    "hypotheses of different replies are linked when their supporting evidence sets overlap "
    "with Jaccard >= 0.5; a cluster is a connected group (single link); wording is not compared"
)
LABEL = (
    "computed by code (cairoflow_evaluation.muse_recurrence) from stored Muse replies; the "
    "statements are model-generated text; recurrence measures the model's consistency, not "
    "the traffic"
)


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _evidence(
    replies: Sequence[tuple[ReplyRef, ReasoningOutput]],
) -> tuple[EvidenceRecurrence, ...]:
    support: dict[str, set[str]] = {}
    against: dict[str, set[str]] = {}
    cited: dict[str, set[str]] = {}
    for ref, out in replies:
        for h in out.hypotheses:
            for eid in h.supporting_evidence_ids:
                support.setdefault(eid, set()).add(ref.reply_id)
                cited.setdefault(eid, set()).add(ref.reply_id)
            for eid in h.contradicting_evidence_ids:
                against.setdefault(eid, set()).add(ref.reply_id)
                cited.setdefault(eid, set()).add(ref.reply_id)
        for i in out.interventions:
            for eid in i.evidence_ids:
                cited.setdefault(eid, set()).add(ref.reply_id)
    order = [ref.reply_id for ref, _ in replies]
    rows = [
        EvidenceRecurrence(
            evidence_id=eid,
            supporting_in=len(support.get(eid, ())),
            contradicting_in=len(against.get(eid, ())),
            cited_in=len(ids),
            replies=tuple(r for r in order if r in ids),
        )
        for eid, ids in cited.items()
    ]
    return tuple(sorted(rows, key=lambda e: (-e.cited_in, -e.supporting_in, e.evidence_id)))


def _clusters(replies: Sequence[tuple[ReplyRef, ReasoningOutput]]) -> tuple[HypothesisCluster, ...]:
    nodes = [
        HypothesisMember(
            reply_id=ref.reply_id,
            hypothesis_id=h.hypothesis_id,
            statement=h.statement,
            confidence_label=h.confidence_label,
            supporting_evidence_ids=tuple(h.supporting_evidence_ids),
        )
        for ref, out in replies
        for h in out.hypotheses
    ]
    parent = list(range(len(nodes)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(nodes):
        for j in range(i + 1, len(nodes)):
            b = nodes[j]
            if a.reply_id == b.reply_id:
                continue
            overlap = jaccard(set(a.supporting_evidence_ids), set(b.supporting_evidence_ids))
            if overlap >= JACCARD_THRESHOLD:
                parent[root(j)] = root(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(nodes)):
        groups.setdefault(root(i), []).append(i)
    ordered = sorted(groups.values(), key=lambda g: (-len({nodes[i].reply_id for i in g}), g[0]))
    clusters = []
    for number, group in enumerate(ordered, 1):
        members = tuple(nodes[i] for i in group)
        shared = set(members[0].supporting_evidence_ids)
        for m in members[1:]:
            shared &= set(m.supporting_evidence_ids)
        clusters.append(
            HypothesisCluster(
                cluster_id=f"C{number}",
                in_replies=len({m.reply_id for m in members}),
                members=members,
                shared_evidence_ids=tuple(sorted(shared)),
            )
        )
    return tuple(clusters)


def recurrence(
    replies: Sequence[tuple[ReplyRef, ReasoningOutput]],
    *,
    request_key: str,
    prompt_version: str,
    bundle_id: str,
) -> MuseRecurrence:
    """The recurrence record of `replies`, all answers to the one request `request_key`."""
    if not replies:
        raise ValueError("no replies")
    types: dict[str, set[str]] = {}
    for ref, out in replies:
        for i in out.interventions:
            types.setdefault(i.type, set()).add(ref.reply_id)
    return MuseRecurrence(
        label=LABEL,
        request_key=request_key,
        prompt_version=prompt_version,
        bundle_id=bundle_id,
        n_replies=len(replies),
        replies=tuple(ref for ref, _ in replies),
        rule=RULE,
        jaccard_threshold=JACCARD_THRESHOLD,
        evidence=_evidence(replies),
        hypotheses=_clusters(replies),
        interventions=tuple(
            TypeRecurrence(intervention_type=t, in_replies=len(ids))
            for t, ids in sorted(types.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        ),
    )
