"""cairoflow_evaluation.muse_recurrence: what recurs across replies to one request."""

from __future__ import annotations

import pytest

from cairoflow_contracts.meta_depth import ReplyRef
from cairoflow_contracts.reasoning import ReasoningOutput
from cairoflow_evaluation.muse_recurrence import jaccard, recurrence

SHA = "b" * 64


def _h(hid: str, support: list[str], against: list[str] = ()) -> dict:
    return {
        "hypothesis_id": hid, "statement": f"statement {hid}",
        "supporting_evidence_ids": support, "contradicting_evidence_ids": list(against),
        "assumptions": [], "confidence_label": "medium", "alternative_explanations": [],
    }  # fmt: skip


def _i(iid: str, evidence: list[str]) -> dict:
    return {
        "intervention_id": iid, "type": "signal_timing", "target_program": "p",
        "phase_changes": [{"phase_name": "p0", "green_s": 30}], "rationale": "r",
        "expected_tradeoffs": [], "preconditions": [], "addresses_hypothesis_ids": ["H1"],
        "evidence_ids": evidence,
    }  # fmt: skip


def _reply(hypotheses: list[dict], interventions: list[dict] | None = None) -> ReasoningOutput:
    return ReasoningOutput.model_validate(
        {
            "hypotheses": hypotheses,
            "interventions": interventions or [_i("I1", []), _i("I2", [])],
        }  # fmt: skip
    )


def _ref(i: int) -> ReplyRef:
    return ReplyRef(reply_id=f"r{i}", replicate=i, cache_path="c", cache_sha256=SHA,
                    response_id=None)  # fmt: skip


def _run(*outputs: ReasoningOutput):
    return recurrence([(_ref(i), o) for i, o in enumerate(outputs, 1)], request_key=SHA,
                      prompt_version="0.1.0", bundle_id="b")  # fmt: skip


def test_jaccard_edges() -> None:
    assert jaccard({"a"}, {"a"}) == 1.0 and jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard(set(), set()) == 0.0 and jaccard(set(), {"a"}) == 0.0


def test_evidence_counts_are_per_reply_not_per_citation() -> None:
    r1 = _reply(
        [_h("H1", ["e1", "e2"]), _h("H2", ["e1"], ["e3"])], [_i("I1", ["e4"]), _i("I2", [])]
    )
    r2 = _reply([_h("H1", ["e2"]), _h("H2", ["e5"])])
    result = _run(r1, r2)
    by = {e.evidence_id: e for e in result.evidence}
    assert (by["e1"].supporting_in, by["e1"].cited_in, by["e1"].replies) == (1, 1, ("r1",))
    assert (by["e2"].supporting_in, by["e2"].cited_in) == (2, 2)
    assert (by["e3"].contradicting_in, by["e3"].supporting_in) == (1, 0)
    assert by["e4"].cited_in == 1 and by["e4"].supporting_in == 0  # cited by a proposal only
    assert [e.evidence_id for e in result.evidence][:1] == ["e2"]  # most-cited first
    assert result.n_replies == 2


def test_hypotheses_link_across_replies_at_half_overlap_and_chain_single_link() -> None:
    r1 = _reply([_h("H1", ["a", "b"]), _h("H2", ["x"])])
    r2 = _reply([_h("H1", ["b", "c"]), _h("H2", ["y"])])  # {a,b} vs {b,c}: 1/3, no link
    r3 = _reply([_h("H1", ["a", "b", "c"]), _h("H2", ["x", "z"])])  # links r1:H1 (2/3), r2:H1 (2/3)
    result = _run(r1, r2, r3)
    top = result.hypotheses[0]
    assert top.in_replies == 3
    assert [f"{m.reply_id}:{m.hypothesis_id}" for m in top.members] == ["r1:H1", "r2:H1", "r3:H1"]
    assert top.shared_evidence_ids == ("b",)
    pair = next(c for c in result.hypotheses if any(m.supporting_evidence_ids == ("x",)
                                                    for m in c.members))  # fmt: skip
    assert pair.in_replies == 2  # {x} vs {x,z} = 1/2: linked at the threshold
    assert sum(c.in_replies == 1 for c in result.hypotheses) == 1  # r2:H2 alone
    assert [c.cluster_id for c in result.hypotheses] == ["C1", "C2", "C3"]


def test_hypotheses_of_the_same_reply_are_never_linked_directly() -> None:
    r1 = _reply([_h("H1", ["a"]), _h("H2", ["a"])])
    r2 = _reply([_h("H1", ["q"]), _h("H2", ["w"])])
    result = _run(r1, r2)
    assert all(c.in_replies == 1 and len(c.members) == 1 for c in result.hypotheses)


def test_identical_replies_recur_fully_and_the_result_is_deterministic() -> None:
    r = _reply([_h("H1", ["a"]), _h("H2", ["b"])])
    first, second = _run(r, r, r, r), _run(r, r, r, r)
    assert first == second
    assert [c.in_replies for c in first.hypotheses] == [4, 4]
    assert first.interventions[0].intervention_type == "signal_timing"
    assert first.interventions[0].in_replies == 4
