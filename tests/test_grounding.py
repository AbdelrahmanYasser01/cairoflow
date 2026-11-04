"""Evidence discipline on Muse replies (P0-3) and the validator's timing bounds (P1-3).

Replies are built in the test on the synthetic scenario's evidence bundle (no request leaves the
machine). The bounds tests use a hand-written copy of the intersection 2 cut cycle, the program
the real proposals I1-I3 were validated against.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from synthetic_scenario import SIGNALS, scenario_geometry, world_model

from cairoflow_contracts.config import Settings
from cairoflow_contracts.experiment import SignalPhaseSpec
from cairoflow_contracts.reasoning import Hypothesis, ReasoningOutput
from cairoflow_reasoning.evidence import build_evidence_bundle
from cairoflow_reasoning.grounding import ground
from cairoflow_reasoning.muse_adapter import MuseClient, MuseSpendRefused
from cairoflow_reasoning.prompts import PROMPTS, SCHEMAS, build_request_body
from cairoflow_simulation.geometry_compiler import compile_network
from cairoflow_simulation.validation import head_runs, timing_problems, validate_proposals


@pytest.fixture(scope="module")
def scenario(tmp_path_factory: pytest.TempPathFactory):
    geo = scenario_geometry()
    network = compile_network(geo, SIGNALS, tmp_path_factory.mktemp("grounding") / "network")
    return network, build_evidence_bundle(world_model(geo), network.manifest)


def _hypothesis(hid: str, support: list[str], **extra: Any) -> dict[str, Any]:
    return {"hypothesis_id": hid, "statement": f"{hid}: the green split may not match demand",
            "supporting_evidence_ids": support, "contradicting_evidence_ids": [],
            "assumptions": ["the counted minute is typical"], "confidence_label": "low",
            "alternative_explanations": ["the clip is too short to tell"], **extra}  # fmt: skip


def _reply(network, hypotheses: list[dict], addresses=("H1",), evidence=("mv.SN",)) -> dict:
    phases = network.manifest.signal_program.phases
    return {"hypotheses": hypotheses, "interventions": [
        {"intervention_id": iid, "type": "signal_timing", "target_program": "timeline_cycle",
         "phase_changes": [{"phase_name": phases[3].name, "green_s": green}],
         "rationale": "more green for the busier axis", "expected_tradeoffs": ["others wait"],
         "preconditions": ["counts are representative"],
         "addresses_hypothesis_ids": list(addresses), "evidence_ids": list(evidence)}
        for iid, green in (("I1", 30), ("I2", 35))]}  # fmt: skip


def _ground(network, bundle, reply: dict):
    output = ReasoningOutput.model_validate(reply)
    return output, ground(output, bundle, "k" * 64)


def test_a_grounded_reply_reaches_planning(scenario) -> None:
    network, bundle = scenario
    output, report = _ground(network, bundle, _reply(network, [
        _hypothesis("H1", ["mv.SN", "sig.program"]), _hypothesis("H2", ["mv.WE"])]))  # fmt: skip
    assert [g.status for g in report.hypotheses] == ["supported", "supported"]
    assert report.competing_hypotheses and all(p.eligible for p in report.proposals)
    outcomes = validate_proposals(output.interventions, network, bundle, grounding=report)
    assert [o.status for o in outcomes] == ["accepted", "accepted"]
    assert all(o.validator_version == "0.2.0" and o.proposal_sha256 for o in outcomes)


def test_invalid_or_missing_evidence_makes_a_hypothesis_unsupported(scenario) -> None:
    network, bundle = scenario
    assumed = next(i.evidence_id for i in bundle.items if i.kind == "assumed")
    output, report = _ground(network, bundle, _reply(network, [
        _hypothesis("H1", ["mv.SN", "q.invented"]),  # an id not in the bundle
        _hypothesis("H2", []),  # no supporting evidence
        _hypothesis("H3", [assumed]),  # only assumed evidence
    ], addresses=("H1", "H2")))  # fmt: skip
    reasons = {g.hypothesis_id: g.reasons for g in report.hypotheses}
    assert reasons["H1"] == ("cites evidence 'q.invented', which is not in the bundle",)
    assert reasons["H2"] == ("cites no supporting evidence",)
    assert reasons["H3"] == ("rests only on assumed evidence",)
    assert not report.competing_hypotheses and report.notes[0].startswith("fewer than two")

    # a proposal that addresses only unsupported hypotheses never reaches the simulator
    outcomes = validate_proposals(output.interventions, network, bundle, grounding=report)
    assert [o.status for o in outcomes] == ["rejected", "rejected"]
    assert outcomes[0].reasons[0] == "not grounded: addresses only unsupported hypotheses"
    assert outcomes[0].phase_durations_s is None


def test_a_proposal_citing_unknown_evidence_or_hypotheses_is_kept_from_planning(scenario) -> None:
    network, bundle = scenario
    _, report = _ground(network, bundle, _reply(network, [
        _hypothesis("H1", ["mv.SN"]), _hypothesis("H2", ["mv.WE"])],
        addresses=("H1", "H9"), evidence=("mv.SN", "net.invented")))  # fmt: skip
    (first, _) = report.proposals
    assert not first.eligible
    assert first.reasons == (
        "addresses hypothesis 'H9', which the reply does not state",
        "cites evidence 'net.invented', which is not in the bundle",
    )
    assert first.supported_hypothesis_ids == ("H1",)


def test_causal_and_overconfident_claims_are_flagged(scenario) -> None:
    network, bundle = scenario
    causal = _hypothesis("H1", ["mv.SN"], alternative_explanations=[], confidence_label="high",
                         statement="The long west queue is caused by the short green.")  # fmt: skip
    _, report = _ground(network, bundle, _reply(network, [causal, _hypothesis("H2", ["mv.WE"])]))
    flags = report.hypotheses[0].flags
    assert report.hypotheses[0].status == "supported"  # flagged, not rejected
    assert any("claims a cause" in f for f in flags)
    assert any("none of its supporting evidence is observed" in f for f in flags)


def test_prompt_0_3_0_fields_separate_observation_from_inference(scenario) -> None:
    network, bundle = scenario
    structured = _hypothesis(
        "H1", ["mv.SN"], limiting_factor="green_split", claim_type="associative",
        observations=[{"evidence_id": "mv.SN", "statement": "3 vehicles S to N"},
                      {"evidence_id": "mv.WE", "statement": "1 vehicle W to E"}],
        inference="the south approach may need more green", missing_evidence=[],
    )  # fmt: skip
    other = _hypothesis(
        "H2",
        ["mv.WE"],
        limiting_factor="queue_spillback",
        claim_type="causal",
        observations=[],
        inference="",
        missing_evidence=["arrival flows"],
    )
    _, report = _ground(network, bundle, _reply(network, [structured, other]))
    assert report.hypotheses[0].flags == (
        "names no missing evidence", "restates evidence it does not cite: mv.WE"
    )  # fmt: skip
    unlisted = "limiting factor 'queue_spillback' is not a listed category"
    assert unlisted in report.hypotheses[1].flags
    assert report.missing_evidence == ("arrival flows",)


def test_earlier_replies_serialise_exactly_as_before() -> None:
    old = {"hypothesis_id": "H1", "statement": "s", "supporting_evidence_ids": ["a.b"],
           "contradicting_evidence_ids": [], "assumptions": [], "confidence_label": "low",
           "alternative_explanations": []}  # fmt: skip
    assert set(json.loads(Hypothesis.model_validate(old).model_dump_json())) == {
        "schema_version", *old
    }  # fmt: skip


def test_each_prompt_version_keeps_its_own_schema(scenario) -> None:
    _, bundle = scenario
    old = build_request_body(bundle, "muse-spark-1.3", prompt_version="0.1.0")
    new = build_request_body(bundle, "muse-spark-1.3", prompt_version="0.3.0")
    assert old["text"]["format"]["schema"] is SCHEMAS["0.1.0"]
    hypothesis = new["text"]["format"]["schema"]["properties"]["hypotheses"]["items"]
    assert {"limiting_factor", "claim_type", "observations", "inference",
            "missing_evidence"} <= set(hypothesis["required"])  # fmt: skip
    assert "Evidence discipline" in PROMPTS["0.3.0"] and "Evidence discipline" not in old["input"]


def test_contributor_variants_are_refused_and_replicates_have_their_own_cache(
    scenario, tmp_path: Path
) -> None:
    _, bundle = scenario
    sent: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(500))
    contributor = Settings(model_api_key=SecretStr("fake-key-0123456789"),
                           muse_model="muse-spark-1.3-contributor")  # fmt: skip
    client = MuseClient(contributor, tmp_path, "live", spend_limit_usd=1.0, transport=transport)
    with pytest.raises(MuseSpendRefused, match="contributor variant"):
        client.diagnose(bundle)
    assert sent == []  # refused before any connection
    settings = Settings(model_api_key=SecretStr("fake-key-0123456789"))
    first = MuseClient(settings, tmp_path, "replay")
    third = MuseClient(settings, tmp_path, "replay", replicate=3)
    key = "a" * 64
    assert first.cache_path(key).name == f"{key}.json"
    assert third.cache_path(key).name == f"{key}.r3.json"


# --- the validator's per-head timing bounds (P1-3), on the intersection 2 cut cycle ---------


def _phase(name: str, duration: float, arms: str, lefts: str) -> SignalPhaseSpec:
    """`arms` and `lefts` give the N, E, S, W and N-left, S-left states as g, y or r."""
    word = {"g": "green", "y": "yellow", "r": "red"}
    return SignalPhaseSpec(
        name=name, duration_s=duration, state="r",
        arm_states=dict(zip("NESW", (word[c] for c in arms), strict=True)),
        left_states=dict(zip("NS", (word[c] for c in lefts), strict=True)),
    )  # fmt: skip


CUT_CYCLE = [  # data/geometry/ngsim-lankershim-int2 cut cycle, as compiled (99 s)
    _phase("p0", 31, "grgr", "rr"), _phase("p1", 1, "yrgr", "rr"), _phase("p2", 3, "yryr", "rr"),
    _phase("p3", 1, "rryr", "rr"), _phase("p4", 1, "rrrr", "rr"), _phase("p5", 5, "rgrr", "rr"),
    _phase("p6", 4, "ryrr", "rr"), _phase("p7", 5, "rrrr", "rr"), _phase("p8", 12, "rrrg", "rr"),
    _phase("p9", 4, "rrry", "rr"), _phase("p10", 3, "rrrr", "rr"), _phase("p11", 10, "rrrr", "gg"),
    _phase("p12", 4, "rrrr", "gy"), _phase("p13", 1, "rrrr", "gr"), _phase("p14", 2, "grrr", "gr"),
    _phase("p15", 4, "grrr", "yr"), _phase("p16", 8, "grrr", "rr"),
]  # fmt: skip
BASE = [p.duration_s for p in CUT_CYCLE]


def _with(**changes: float) -> list[float]:
    index = {p.name: i for i, p in enumerate(CUT_CYCLE)}
    durations = list(BASE)
    for name, value in changes.items():
        durations[index[name]] = value
    return durations


def test_head_runs_join_split_yellows_and_wrap_round_the_cycle() -> None:
    runs = head_runs(CUT_CYCLE, BASE)
    assert runs["N"] == [("green", 45.0), ("yellow", 4.0), ("red", 50.0)]  # p14-p16 + p0
    assert runs["S"][:2] == [("green", 32.0), ("yellow", 4.0)]
    assert runs["N-left"][1] == ("green", 17.0) and runs["S-left"][1] == ("green", 10.0)


def test_the_recorded_proposals_pass_and_the_audit_probes_fail() -> None:
    assert timing_problems(CUT_CYCLE, BASE) == []
    for i_changes in (
        {"p0": 40, "p8": 6},
        {"p0": 26, "p11": 15},
        {"p8": 5, "p11": 16, "p16": 12},
    ):  # I1, I2, I3 (2026-10-28)
        assert timing_problems(CUT_CYCLE, _with(**i_changes)) == [], i_changes
    # the audit's probe: an 80 s left phase (cycle 169 s) was accepted before
    problems = timing_problems(CUT_CYCLE, _with(p11=80))
    assert problems == [
        "head N-left would show green for 87 s in a run, outside 5-40 s",
        "head S-left would show green for 80 s in a run, outside 5-40 s",
    ]
    assert timing_problems(CUT_CYCLE, _with(p7=7)) == [
        "all-red phase p7 lasts 7 s, outside 1-6 s"
    ]  # fmt: skip
    assert "head W would show green for 91 s" in timing_problems(CUT_CYCLE, _with(p8=91))[0]
