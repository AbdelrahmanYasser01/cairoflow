"""The diagnosis prompt and the strict JSON schema Muse Spark must answer in (versioned).

The schema is written by hand and uses only plain JSON Schema keywords (`type`, `properties`,
`required`, `additionalProperties`, `enum`, `items`), all accepted with `strict: true` by the
first real diagnosis (2026-10-28, prompt 0.1.0). Counts such as "at least two hypotheses" are not
in the schema: `ReasoningOutput` enforces them after parsing, and the evidence rules are checked
by code afterwards (cairoflow_reasoning.grounding), never trusted from the reply.

Each prompt version has its own schema, so a cached reply always replays with the exact request
it answered. Prompt 0.3.0 asks each hypothesis to separate what the cited evidence shows from what
is inferred, to name its missing evidence, to say whether it claims a cause, and to give the
limiting factor it is about (one of LIMITING_FACTORS), so replies can be compared across runs.
Prompt 0.3.1 keeps 0.3.0's instructions and schema. It names the corrected evidence limitations of
scripts/run_cairoflow.py, which chooses that text by prompt version (investigation
camera-to-muse-20261031). Since the instructions do not tell the two apart, the cache also checks
the version a reply was recorded under (muse_adapter.MuseClient.fetch).

The schema allows only `signal_timing` (ADR-015), but the validator checks the type again,
because cached or future replies need not obey it.
"""

from __future__ import annotations

import json
from typing import Any

from cairoflow_contracts.reasoning import (
    ALLOWED_INTERVENTIONS,
    LIMITING_FACTORS,
    MAX_CYCLE_S,
    MIN_CYCLE_S,
    EvidenceBundle,
)

PROMPT_VERSION = "0.3.0"  # earlier versions stay in PROMPTS so cached replies still replay
SCHEMA_NAME = "cairoflow_diagnosis"
SCHEMA_KEYWORDS = frozenset(
    {"type", "properties", "required", "additionalProperties", "enum", "items"}
)


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_STRINGS = {"type": "array", "items": {"type": "string"}}
RESPONSE_SCHEMA: dict[str, Any] = _object(
    {
        "hypotheses": {
            "type": "array",
            "items": _object(
                {
                    "hypothesis_id": {"type": "string"},
                    "statement": {"type": "string"},
                    "supporting_evidence_ids": _STRINGS,
                    "contradicting_evidence_ids": _STRINGS,
                    "assumptions": _STRINGS,
                    "confidence_label": {"type": "string", "enum": ["low", "medium", "high"]},
                    "alternative_explanations": _STRINGS,
                }
            ),
        },
        "interventions": {
            "type": "array",
            "items": _object(
                {
                    "intervention_id": {"type": "string"},
                    "type": {"type": "string", "enum": list(ALLOWED_INTERVENTIONS)},
                    "target_program": {"type": "string"},
                    "phase_changes": {
                        "type": "array",
                        "items": _object(
                            {"phase_name": {"type": "string"}, "green_s": {"type": "number"}}
                        ),
                    },
                    "rationale": {"type": "string"},
                    "expected_tradeoffs": _STRINGS,
                    "preconditions": _STRINGS,
                    "addresses_hypothesis_ids": _STRINGS,
                    "evidence_ids": _STRINGS,
                }
            ),
        },
    }
)

_INSTRUCTIONS_0_1_0 = f"""\
You assist traffic engineers with decision support for one signalised junction.
Nothing you write controls real traffic. Your proposals are checked by deterministic code and then
simulated in SUMO; only the simulation produces numbers.

Use only the evidence below. Every evidence item has an id; cite ids exactly as given.

Return JSON matching the schema:
1. At least two competing hypotheses about what limits this junction. For each, cite supporting
   and contradicting evidence ids, list assumptions, give a confidence label (low, medium, high)
   and alternative explanations. Low confidence is expected when evidence is short or assumed.
2. Two to four interventions of type signal_timing on target_program "{{program_id}}". Each
   changes the green duration (green_s, whole seconds) of one or more phases marked adjustable,
   within each phase's min and max. Do not change clearance phases. Keep the cycle between
   {MIN_CYCLE_S:g} and {MAX_CYCLE_S:g} s. Make the interventions differ from each other and from
   the current timing. Link each to the hypothesis ids it addresses and cite its evidence ids.
3. expected_tradeoffs and rationale are words only: do not predict delays, queues, flows or any
   other number; the simulation will measure them.
Use ids H1, H2, ... for hypotheses and I1, I2, ... for interventions.
"""
# 0.2.0: the arm convention, after the first real diagnosis read arm N as "northbound".
_INSTRUCTIONS_0_2_0 = _INSTRUCTIONS_0_1_0.replace(
    "cite ids exactly as given.\n",
    "cite ids exactly as given.\n"
    "Arms N, E, S and W are sides of the junction, as the evidence's conventions say: arm N\n"
    "holds the vehicles arriving from the north (travelling southbound), never 'northbound'\n"
    "traffic; 'N to E (left)' starts on arm N and leaves by arm E. Likewise for E, S and W.\n",
)
_HYPOTHESIS_0_3_0 = {
    **RESPONSE_SCHEMA["properties"]["hypotheses"]["items"]["properties"],
    "limiting_factor": {"type": "string", "enum": list(LIMITING_FACTORS)},
    "claim_type": {"type": "string", "enum": ["causal", "associative", "descriptive"]},
    "observations": {
        "type": "array",
        "items": _object({"evidence_id": {"type": "string"}, "statement": {"type": "string"}}),
    },
    "inference": {"type": "string"},
    "missing_evidence": _STRINGS,
}
RESPONSE_SCHEMA_0_3_0: dict[str, Any] = _object(
    {
        "hypotheses": {"type": "array", "items": _object(_HYPOTHESIS_0_3_0)},
        "interventions": RESPONSE_SCHEMA["properties"]["interventions"],
    }
)
# 0.3.0: evidence discipline (P0-3). The rules below are checked by code after the reply.
_INSTRUCTIONS_0_3_0 = (
    _INSTRUCTIONS_0_2_0.replace(
        "Return JSON matching the schema:\n",
        "Evidence discipline (checked by code after your reply; a hypothesis that breaks a rule "
        "is\nmarked unsupported, and an intervention that addresses only unsupported "
        "hypotheses is not simulated):\n"
        "- Each hypothesis cites at least one supporting evidence id, and only ids from the "
        "evidence.\n"
        "- Keep what the evidence shows apart from what you infer: `observations` restate cited "
        "items\n  (one per id, no inference); `inference` says what you conclude beyond them.\n"
        "- A hypothesis that rests only on evidence of kind assumed is unsupported.\n"
        "- `missing_evidence` names observations that are not in the evidence and would confirm "
        "or\n  refute the hypothesis. Say plainly what is missing; do not fill gaps with "
        "guesses.\n"
        '- Co-occurrence is not causation. Use claim_type "causal" only for a stated '
        "mechanism, and then\n  give alternative explanations. Use low or medium confidence "
        "when the supporting evidence is\n  approximated or assumed rather than observed.\n"
        "- `limiting_factor` is the category the hypothesis is about.\n\n"
        "Return JSON matching the schema:\n",
    )
    .replace(
        "1. At least two competing hypotheses about what limits this junction. For each, cite",
        "1. At least two competing hypotheses about what limits this junction, most plausible "
        "first. For each,\n   give its limiting_factor, claim_type, observations, inference "
        "and missing_evidence, and cite",
    )
    .replace(
        "Link each to the hypothesis ids it addresses and cite its evidence ids.",
        "Link each to the hypothesis ids it addresses (hypotheses you can support) and cite "
        "its evidence ids.",
    )
)
# 0.3.1: 0.3.0's instructions and schema; only the run's evidence limitations differ (see above).
PROMPTS: dict[str, str] = {
    "0.1.0": _INSTRUCTIONS_0_1_0,
    "0.2.0": _INSTRUCTIONS_0_2_0,
    "0.3.0": _INSTRUCTIONS_0_3_0,
    "0.3.1": _INSTRUCTIONS_0_3_0,
}
SCHEMAS: dict[str, dict[str, Any]] = {
    "0.1.0": RESPONSE_SCHEMA,
    "0.2.0": RESPONSE_SCHEMA,
    "0.3.0": RESPONSE_SCHEMA_0_3_0,
    "0.3.1": RESPONSE_SCHEMA_0_3_0,
}
INSTRUCTIONS = PROMPTS[PROMPT_VERSION]


def build_input(bundle: EvidenceBundle, prompt_version: str = PROMPT_VERSION) -> str:
    """The full prompt: fixed instructions of a version, then the bundle as compact JSON."""
    if prompt_version not in PROMPTS:
        raise ValueError(f"unknown prompt version {prompt_version!r}")
    evidence = bundle.model_dump(mode="json", exclude={"schema_version"})
    return (
        PROMPTS[prompt_version].replace("{program_id}", bundle.program_id)
        + "\nEVIDENCE (JSON):\n"
        + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    )


def build_request_body(
    bundle: EvidenceBundle,
    model: str,
    *,
    effort: str = "medium",
    max_output_tokens: int = 8000,
    store: bool | None = None,
    prompt_version: str = PROMPT_VERSION,
) -> dict[str, Any]:
    """Responses API body. `store` is sent only when set: Muse accepted `store: false` in the
    smoke test of 2026-10-28; requests made before that
    leave it out, so their bodies, and the cache keys they hash to, stay as sent."""
    body: dict[str, Any] = {
        "model": model,
        "input": build_input(bundle, prompt_version),
        "reasoning": {"effort": effort},
        "max_output_tokens": max_output_tokens,
        "text": {
            "format": {
                "type": "json_schema",
                "name": SCHEMA_NAME,
                "schema": SCHEMAS[prompt_version],
                "strict": True,
            }
        },
    }
    if store is not None:
        body["store"] = store
    return body


def schema_keywords(schema: Any) -> set[str]:
    """Every keyword used anywhere in a schema (property names are not keywords)."""
    found: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            found.add(key)
            if key == "properties":
                for child in value.values():
                    found |= schema_keywords(child)
            elif isinstance(value, dict):
                found |= schema_keywords(value)
    return found
