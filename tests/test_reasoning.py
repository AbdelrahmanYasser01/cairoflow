"""Evidence bundle, Muse adapter and proposal validator, on the synthetic scenario.

No request leaves the machine: HTTP goes through httpx.MockTransport, the key is a fake string,
and the only real reply used is the cached `muse-structured` smoke response (skipped if absent).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from synthetic_clip import NOW
from synthetic_scenario import SIGNALS, scenario_geometry, world_model

from cairoflow_contracts.config import Settings
from cairoflow_contracts.reasoning import InterventionProposal, PhaseChange
from cairoflow_reasoning.evidence import ARM_CONVENTIONS, EvidenceError, build_evidence_bundle
from cairoflow_reasoning.muse_adapter import (
    MuseAdapterError,
    MuseCacheMiss,
    MuseClient,
    MuseRequestError,
    MuseResponseError,
    MuseSpendRefused,
    extract_output,
    usage_cost_usd,
)
from cairoflow_reasoning.prompts import (
    PROMPT_VERSION,
    RESPONSE_SCHEMA,
    SCHEMA_KEYWORDS,
    build_input,
    schema_keywords,
)
from cairoflow_simulation.geometry_compiler import CompiledNetwork, compile_network
from cairoflow_simulation.validation import (
    ValidationInputError,
    validate_proposal,
    validate_proposals,
)

FAKE_KEY = "fake-test-key-0123456789"
SETTINGS = Settings(model_api_key=SecretStr(FAKE_KEY))
USAGE = {
    "input_tokens": 1000,
    "input_tokens_details": {"cached_tokens": 200},
    "output_tokens": 500,
    "output_tokens_details": {"reasoning_tokens": 300},
    "total_tokens": 1500,
}
SMOKE_CACHE = Path(__file__).resolve().parents[1] / "cache" / "smoke"


@pytest.fixture(scope="module")
def scenario(tmp_path_factory: pytest.TempPathFactory):
    geo = scenario_geometry()
    network = compile_network(geo, SIGNALS, tmp_path_factory.mktemp("m5") / "network")
    model = world_model(geo)
    return model, network, build_evidence_bundle(model, network.manifest)


def _phases(network: CompiledNetwork) -> tuple[str, str]:
    """(E-W green phase, N-S green phase) of the compiled cycle."""
    phases = network.manifest.signal_program.phases
    return phases[0].name, phases[3].name


def _output(network: CompiledNetwork, **intervention_overrides: Any) -> dict[str, Any]:
    ew, ns = _phases(network)
    hypotheses = [
        {
            "hypothesis_id": hid,
            "statement": statement,
            "supporting_evidence_ids": support,
            "contradicting_evidence_ids": [],
            "assumptions": ["the counted minute is typical"],
            "confidence_label": "low",
            "alternative_explanations": ["the clip is too short to tell"],
        }
        for hid, statement, support in (
            ("H1", "N-S green is short for the south approach", ["mv.SN", "sig.program"]),
            ("H2", "E-W green is longer than its demand needs", ["mv.WE", "sig.program"]),
        )
    ]
    interventions = [
        {
            "intervention_id": iid,
            "type": "signal_timing",
            "target_program": "timeline_cycle",
            "phase_changes": [
                {"phase_name": ns, "green_s": ns_green},
                {"phase_name": ew, "green_s": ew_green},
            ],
            "rationale": "move green time towards the busier axis",
            "expected_tradeoffs": ["west approach waits longer"],
            "preconditions": ["counts are representative"],
            "addresses_hypothesis_ids": ["H1", "H2"],
            "evidence_ids": ["mv.SN", "mv.WE"],
        }
        for iid, ns_green, ew_green in (("I1", 30, 20), ("I2", 35, 15))
    ]
    interventions[0].update(intervention_overrides)
    return {"hypotheses": hypotheses, "interventions": interventions}


def _response(text: str, status: str = "completed", **extra: Any) -> dict[str, Any]:
    return {
        "id": "resp_test",
        "object": "response",
        "status": status,
        "model": "muse-spark-1.3",
        "output": [
            {"type": "reasoning", "id": "rs_1", "summary": [], "status": "completed"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
                "status": "completed",
            },
        ],
        "usage": USAGE,
        **extra,
    }


class Recorder:
    """A MockTransport handler that answers with one fixed reply and counts requests."""

    def __init__(self, status: int = 200, payload: dict[str, Any] | None = None) -> None:
        self.status, self.payload, self.requests = status, payload, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        headers = {"x-request-id": "req-123", "x-ratelimit-remaining-requests": "2999"}
        return httpx.Response(self.status, json=self.payload or {"error": "x"}, headers=headers)


def _client(tmp_path: Path, recorder: Recorder, mode="live", spend=1.0, **kwargs) -> MuseClient:
    settings = kwargs.pop("settings", SETTINGS)
    return MuseClient(
        settings,
        tmp_path / "muse",
        mode,
        spend_limit_usd=spend,
        transport=httpx.MockTransport(recorder),
        **kwargs,
    )


# --- Evidence bundle and request ------------------------------------------------------------


def test_evidence_bundle_cites_every_value_with_its_kind(scenario) -> None:
    model, network, bundle = scenario
    kinds = {item.evidence_id: item.kind for item in bundle.items}
    assert kinds["mv.SN"] == "approximated"
    assert "8 vehicles in 60 s = 480 veh/h" in next(
        i.summary for i in bundle.items if i.evidence_id == "mv.SN"
    )
    assert kinds["net.S1.width"] == "approximated"
    assert kinds["net.S.arm_length"] == "assumed"  # synthetic
    assert kinds["sig.program"] == "assumed"  # an assumed timeline
    assert [p.adjustable for p in bundle.phases] == [True, False, False, True, False, False]
    assert (bundle.phases[0].min_green_s, bundle.phases[0].max_green_s) == (5.0, 90.0)
    assert build_evidence_bundle(model, network.manifest) == bundle  # deterministic id

    other = world_model(scenario_geometry().model_copy(update={"geometry_id": "other"}))
    other = other.model_copy(update={"geometry_sha256": "0" * 64})
    with pytest.raises(EvidenceError):
        build_evidence_bundle(other, network.manifest)


def test_arm_convention_in_bundle_and_prompt(scenario) -> None:
    model, network, bundle = scenario
    assert bundle.conventions == ARM_CONVENTIONS
    assert "arriving from the north" in bundle.conventions[0]
    plain = build_evidence_bundle(model, network.manifest, conventions=())
    assert (
        "conventions" in bundle.model_dump_json() and "conventions" not in plain.model_dump_json()
    )
    assert plain.bundle_id != bundle.bundle_id
    new, old = build_input(bundle), build_input(plain, "0.1.0")
    # 0.3.0 (evidence discipline) keeps the arm convention of 0.2.0
    assert PROMPT_VERSION == "0.3.0" and "never 'northbound'" in new
    assert "northbound" not in old and '"conventions"' not in old  # 0.1.0 as sent on 2026-10-28
    with pytest.raises(ValueError, match="unknown prompt version"):
        build_input(bundle, "9.9.9")


def test_extra_limitations_and_per_lane_end_of_red(scenario) -> None:
    model, network, bundle = scenario
    compact = build_evidence_bundle(
        model, network.manifest, ("evidence from a published dataset",), end_of_red_per_lane=True
    )
    assert compact.limitations[: len(bundle.limitations)] == bundle.limitations
    assert compact.limitations[-1] == "evidence from a published dataset"
    per_sample = [i for i in bundle.items if i.evidence_id.startswith("qr.")]
    per_lane = [i for i in compact.items if i.evidence_id.startswith("qr.")]
    lanes = {q.lane_id for q in model.end_of_red_queues}
    assert len(per_sample) == len(model.end_of_red_queues) and len(per_lane) == len(lanes)
    assert {i.evidence_id for i in per_lane} == {f"qr.{lane}" for lane in lanes}
    assert all("median" in i.summary and "max" in i.summary for i in per_lane)
    assert compact.bundle_id != bundle.bundle_id


def test_request_body_matches_the_verified_shape(scenario, tmp_path: Path) -> None:
    _, _, bundle = scenario
    body = _client(tmp_path, Recorder()).request_body(bundle)
    assert set(body) == {"model", "input", "reasoning", "max_output_tokens", "text"}
    assert "store" not in body  # store: false is untested; not sent by default
    fmt = body["text"]["format"]
    assert (fmt["type"], fmt["strict"]) == ("json_schema", True)
    assert schema_keywords(RESPONSE_SCHEMA) <= SCHEMA_KEYWORDS
    assert '"timeline_cycle"' in body["input"] and "mv.SN" in body["input"]
    assert FAKE_KEY not in json.dumps(body)

    def objects(schema: Any):
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                yield schema
            for value in schema.values():
                yield from objects(value)

    for obj in objects(RESPONSE_SCHEMA):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    intervention = RESPONSE_SCHEMA["properties"]["interventions"]["items"]
    assert intervention["properties"]["type"]["enum"] == ["signal_timing"]


# --- Adapter ----------------------------------------------------------------------------------


def test_live_sends_one_request_then_the_cache_answers(scenario, tmp_path: Path) -> None:
    _, network, bundle = scenario
    text = json.dumps(_output(network))
    recorder = Recorder(payload=_response(text, echoed=f"Bearer {FAKE_KEY}"))
    result = _client(tmp_path, recorder).diagnose(bundle)

    assert len(recorder.requests) == 1
    assert recorder.requests[0].headers["authorization"] == f"Bearer {FAKE_KEY}"
    assert [h.hypothesis_id for h in result.output.hypotheses] == ["H1", "H2"]
    assert [i.intervention_id for i in result.output.interventions] == ["I1", "I2"]
    assert result.problems == ()
    assert (result.request_id, result.response_id) == ("req-123", "resp_test")
    assert result.usage["reasoning_tokens"] == 300
    assert result.estimated_cost_usd == pytest.approx((800 * 1.25 + 200 * 0.15 + 500 * 4.25) / 1e6)
    assert result.provenance.kind == "model_generated" and not result.provenance.cached

    (cached,) = (tmp_path / "muse").glob("*.json")
    assert FAKE_KEY not in cached.read_text("utf-8")  # scrubbed, even where the reply echoed it

    silent = Recorder()
    replayed = _client(tmp_path, silent, mode="replay", spend=0.0).diagnose(bundle)
    assert silent.requests == [] and replayed.provenance.cached
    assert replayed.output == result.output


def test_replay_refuses_a_cache_file_whose_request_was_edited(scenario, tmp_path: Path) -> None:
    """The file name and the stored key are not enough: the stored body must hash to the key,
    so a committed reply cannot be paired with a different request."""
    _, network, bundle = scenario
    _client(tmp_path, Recorder(payload=_response(json.dumps(_output(network))))).diagnose(bundle)
    (cached,) = (tmp_path / "muse").glob("*.json")
    record = json.loads(cached.read_text("utf-8"))
    record["body"]["max_output_tokens"] += 1  # the stored request_key is left as it was
    cached.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    silent = Recorder()
    with pytest.raises(MuseAdapterError, match="holds a different request"):
        _client(tmp_path, silent, mode="replay", spend=0.0).diagnose(bundle)
    assert silent.requests == []


def test_replay_refuses_a_reply_recorded_under_another_prompt_version(
    scenario, tmp_path: Path
) -> None:
    """Prompts 0.3.0 and 0.3.1 share their instructions, so one bundle gives one request key
    under both: a reply recorded under one version is never served as the other."""
    _, network, bundle = scenario
    _client(tmp_path, Recorder(payload=_response(json.dumps(_output(network))))).diagnose(bundle)
    assert PROMPT_VERSION == "0.3.0"  # the reply above was recorded under 0.3.0

    silent = Recorder()
    with pytest.raises(MuseAdapterError, match="holds another prompt version"):
        _client(tmp_path, silent, mode="replay", prompt_version="0.3.1").diagnose(bundle)
    replayed = _client(tmp_path, silent, mode="replay", prompt_version="0.3.0").diagnose(bundle)
    assert silent.requests == [] and replayed.provenance.cached
    assert replayed.prompt_version == "0.3.0"


def test_replay_never_connects(scenario, tmp_path: Path) -> None:
    recorder = Recorder()
    with pytest.raises(MuseCacheMiss):
        _client(tmp_path, recorder, mode="replay").diagnose(scenario[2])
    assert recorder.requests == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"spend": 0.0}, "spend limit"),
        ({"settings": Settings()}, "no API key"),
        (
            {"settings": Settings(model_api_key=SecretStr(FAKE_KEY), meta_base_url="http://x")},
            "non-HTTPS",
        ),
    ],
)
def test_live_refuses_before_sending(scenario, tmp_path: Path, kwargs, message) -> None:
    recorder = Recorder()
    with pytest.raises(MuseSpendRefused, match=message):
        _client(tmp_path, recorder, **kwargs).diagnose(scenario[2])
    assert recorder.requests == []


def test_error_status_is_classified_kept_and_not_retried(scenario, tmp_path: Path) -> None:
    recorder = Recorder(status=429)
    with pytest.raises(MuseRequestError) as error:
        _client(tmp_path, recorder).diagnose(scenario[2])
    assert (error.value.error_class, error.value.request_id) == ("rate_limited", "req-123")
    assert len(recorder.requests) == 1
    assert len(list((tmp_path / "muse" / "failed").glob("*.json"))) == 1
    assert list((tmp_path / "muse").glob("*.json")) == []


@pytest.mark.parametrize(
    ("make", "message"),
    [
        (lambda net: _response("{}", status="incomplete"), "status 'incomplete'"),
        (
            lambda net: (
                _response("") | {"output": [{"type": "reasoning", "id": "r", "summary": []}]}
            ),
            "no output text",
        ),
        (
            lambda net: (
                _response("")
                | {
                    "output": [
                        {"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}
                    ]
                }
            ),
            "refused",
        ),
        (
            lambda net: _response(
                json.dumps(_output(net) | {"hypotheses": _output(net)["hypotheses"][:1]})
            ),
            "does not match the schema",
        ),
    ],
)
def test_unusable_replies_are_kept_as_failures(scenario, tmp_path: Path, make, message) -> None:
    _, network, bundle = scenario
    with pytest.raises(MuseResponseError, match=message):
        _client(tmp_path, Recorder(payload=make(network))).diagnose(bundle)
    assert len(list((tmp_path / "muse" / "failed").glob("*.json"))) == 1
    assert list((tmp_path / "muse").glob("*.json")) == []


def test_unknown_citations_are_flagged_not_dropped(scenario, tmp_path: Path) -> None:
    _, network, bundle = scenario
    output = _output(network, evidence_ids=["mv.XX"], addresses_hypothesis_ids=["H9"])
    result = _client(tmp_path, Recorder(payload=_response(json.dumps(output)))).diagnose(bundle)
    assert result.problems == (
        "I1 cites unknown evidence 'mv.XX'",
        "I1 addresses unknown hypothesis 'H9'",
    )


def test_the_cached_real_reply_parses_and_prices() -> None:
    files = sorted(SMOKE_CACHE.glob("*_muse-structured.json"))
    if not files:
        pytest.skip("no cached muse-structured smoke reply (cache/ is not committed)")
    record = json.loads(files[-1].read_text("utf-8"))
    response = json.loads(record["response"]["text"])
    assert json.loads(extract_output(response)) == {
        "confidence_label": "high",
        "longest_queue_approach": "east",
    }
    assert usage_cost_usd(response["usage"]) == pytest.approx(record["cost_usd"])


# --- Validator --------------------------------------------------------------------------------


def _proposal(network: CompiledNetwork, **overrides: Any) -> InterventionProposal:
    ew, ns = _phases(network)
    values = {
        "intervention_id": "I1",
        "type": "signal_timing",
        "target_program": "timeline_cycle",
        "phase_changes": (
            PhaseChange(phase_name=ns, green_s=30),
            PhaseChange(phase_name=ew, green_s=20),
        ),
        "rationale": "r",
        "expected_tradeoffs": (),
        "preconditions": (),
        "addresses_hypothesis_ids": ("H1",),
        "evidence_ids": ("mv.SN",),
    }
    return InterventionProposal(**(values | overrides))


def test_a_valid_timing_is_accepted_with_its_cycle(scenario) -> None:
    _, network, bundle = scenario
    outcome = validate_proposal(_proposal(network), network, bundle, now=NOW)
    assert (outcome.status, outcome.reasons) == ("accepted", ())
    assert outcome.phase_durations_s == (20, 3, 2, 30, 3, 2)
    assert (outcome.cycle_s, outcome.baseline_cycle_s) == (60, 60)


def _change(network: CompiledNetwork, index: int, green: float) -> tuple[PhaseChange, ...]:
    return (
        PhaseChange(phase_name=network.manifest.signal_program.phases[index].name, green_s=green),
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (lambda n: {"type": "turn_restriction"}, "not in the MVP allowlist"),
        (lambda n: {"target_program": "other"}, "is not the compiled program"),
        (lambda n: {"phase_changes": ()}, "no phase changes"),
        (lambda n: {"phase_changes": _change(n, 1, 5)}, "clearance interval"),
        (
            lambda n: {"phase_changes": (PhaseChange(phase_name="p9", green_s=20),)},
            "does not exist",
        ),
        (lambda n: {"phase_changes": _change(n, 0, 3)}, "outside 5-90 s"),
        (lambda n: {"phase_changes": _change(n, 0, 95)}, "outside 5-90 s"),
        (lambda n: {"phase_changes": _change(n, 0, 27.5)}, "not whole seconds"),
        (lambda n: {"phase_changes": _change(n, 0, 25)}, "same as the baseline"),
        (
            lambda n: {"phase_changes": _change(n, 0, 90) + _change(n, 3, 90)},
            "cycle 190 s is outside 30-180 s",
        ),
        (lambda n: {"phase_changes": _change(n, 0, 20) * 2}, "changed more than once"),
        (lambda n: {"evidence_ids": ()}, "no evidence is cited"),
        (lambda n: {"evidence_ids": ("mv.XX",)}, "'mv.XX' is not in bundle"),
    ],
)
def test_invalid_proposals_are_rejected_with_reasons(scenario, overrides, reason) -> None:
    _, network, bundle = scenario
    outcome = validate_proposal(_proposal(network, **overrides(network)), network, bundle, now=NOW)
    assert outcome.status == "rejected" and outcome.phase_durations_s is None
    assert any(reason in r for r in outcome.reasons), outcome.reasons


def test_other_types_get_only_the_allowlist_reason(scenario) -> None:
    _, network, bundle = scenario
    proposal = _proposal(network, type="route_guidance", evidence_ids=())
    (reason,) = validate_proposal(proposal, network, bundle, now=NOW).reasons
    assert "ADR-015" in reason


def test_duplicates_are_rejected_and_inputs_must_match(scenario) -> None:
    _, network, bundle = scenario
    first = _proposal(network)
    same = _proposal(network, intervention_id="I2")
    outcomes = validate_proposals([first, same], network, bundle, now=NOW)
    assert [o.status for o in outcomes] == ["accepted", "rejected"]
    assert outcomes[1].reasons == ("same timing as I1",)

    with pytest.raises(ValidationInputError):
        validate_proposal(first, network, bundle.model_copy(update={"net_sha256": "0" * 64}))


def test_priority_greens_on_conflicting_links_are_rejected(scenario) -> None:
    _, network, bundle = scenario
    program = network.manifest.signal_program
    all_green = program.phases[0].model_copy(update={"state": "G" * network.manifest.link_count})
    tampered = network.manifest.model_copy(
        update={
            "signal_program": program.model_copy(
                update={"phases": (all_green, *program.phases[1:])}
            )
        }
    )
    broken = CompiledNetwork(net_path=network.net_path, links=network.links, manifest=tampered)
    outcome = validate_proposal(_proposal(network), broken, bundle, now=NOW)
    assert outcome.status == "rejected"
    assert any("priority green to conflicting links" in r for r in outcome.reasons)
