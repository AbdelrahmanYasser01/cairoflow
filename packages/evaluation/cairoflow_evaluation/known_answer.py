"""A SYNTHETIC known-answer test of the evaluation and selection stage (not NGSIM evidence).

What is tested: the deterministic validator, paired SUMO runs, eligibility, paired comparisons,
Holm's correction and the selection rule, on candidates whose answer is known in advance. Positive
controls and negative controls are included. What is NOT tested: discovery. No proposer runs
(Muse is not exercised); the correction is the pre-registered answer itself, written by code.

Two scenarios on one four-arm junction drawn in code (one through-only approach lane per arm),
each with a deliberately bad fixed-time two-phase plan (yellow 3 s and all-red 2 s after each
green):

- `reallocation`: E-W 300 and N-S 150 veh/h per approach; the green the wrong way round (E-W 20 s,
  N-S 40 s, cycle 70 s). Correction: Webster's split at the same cycle (E-W 40, N-S 20). Moving
  green from N-S to E-W lengthens N-S red: a trade-off between the axes.
- `long-cycle`: E-W 450 and N-S 300 veh/h per approach; Webster's split but a cycle far longer than
  the demand needs (E-W 84 s, N-S 56 s, cycle 150 s). Correction: Webster's optimum cycle, rounded
  up to whole seconds within the validator's cycle limits, and its split. It shortens every
  approach's red time. Designed after the first scenario's result (SCENARIO_TEXT).

Candidates, all through the same validator and pipeline, with ids from a seeded permutation:
- the correction (positive control);
- a half-way plan;
- the exact null: the baseline plan relabelled. The validator rejects a timing equal to the
  baseline's, so it is expected never to be simulated or selected;
- the mirror of the correction: the same change in the opposite direction, moved into the
  validator's limits if needed (a negative control);
- K small random perturbations (every green moved by an integer in [-5, +5] s) of the
  deliberately bad plan. These are not nulls: they may genuinely improve or worsen.

Before anything is simulated, `preregister` computes all of this from the design alone, with
analytical Webster predictions per approach (delay, red time). `run_known_answer` writes it to
`preregistration.json` and hashes it before the first SUMO run. It also records the git commit
and whether the files it imports are clean against it, at the start and at the end. Then it runs
validator -> `run_candidates` -> `evaluate_experiment` -> `assemble_report` -> `trace_report`.
Nothing here selects: `summarize_outcome` reads the report's selection and standings and
compares them with the pre-registration. Design values, the expectation and the predictions are
`assumed` (the last two analytical); every comparison is `simulated`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from importlib import import_module, metadata
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from cairoflow_contracts.evidence import (
    ARM_ORDER,
    Arm,
    Gate,
    IntersectionGeometry,
    LanePolygon,
    MovementCount,
    MovementTrack,
    Provenance,
    SignalInterval,
    SignalTimeline,
    TrafficWorldModel,
)
from cairoflow_contracts.experiment import EvaluationResult, RunSettings
from cairoflow_contracts.reasoning import (
    MAX_CYCLE_S,
    MAX_GREEN_S,
    MIN_CYCLE_S,
    MIN_GREEN_S,
    InterventionProposal,
    PhaseChange,
    ValidationOutcome,
)
from cairoflow_evaluation.compare import evaluate_experiment
from cairoflow_evaluation.eligibility import RULES_VERSION
from cairoflow_evaluation.metrics import CALCULATION_VERSION
from cairoflow_evaluation.report import (
    assemble_report,
    candidate_standings,
    comparison_rows,
    id_order,
    select_candidate,
    trace_report,
    write_report,
)
from cairoflow_evaluation.significance import FAMILY_ALPHA
from cairoflow_perception.world_model import poisson_interval, turn_for
from cairoflow_reasoning.evidence import build_evidence_bundle
from cairoflow_simulation.candidates import CandidateError, load_baseline, run_candidates
from cairoflow_simulation.geometry_compiler import CompiledNetwork, CompileOptions
from cairoflow_simulation.runner import run_baseline
from cairoflow_simulation.validation import validate_proposals

NAME = "synthetic-known-answer"
SCRIPT = "scripts/run_synthetic_known_answer.py"
PREREGISTRATION_FILE = "preregistration.json"
SUMMARY_FILE = "summary.json"
DEFAULT_CONTROL_SEED = 20261029
DEFAULT_SETTINGS = RunSettings(seeds=tuple(range(1, 21)))  # 20 seeds (ADR-021: about 20)
MAX_PERTURBATION_S = 5
DEAD_BAND_S = 1.0  # analytical changes smaller than this (delay or red, s) are "about unchanged"
WINDOW_S = 3600.0  # the designed demand is written as one hour of counts: count = veh/h
AXES: dict[str, tuple[Arm, ...]] = {"EW": ("E", "W"), "NS": ("N", "S")}  # in phase order
AXIS_OF: dict[Arm, str] = {arm: axis for axis, arms in AXES.items() for arm in arms}
OPPOSITE: dict[Arm, Arm] = {"N": "S", "S": "N", "E": "W", "W": "E"}
SYNTHETIC_LABEL = (
    "SYNTHETIC known-answer test of the evaluation and selection stage (positive and negative "
    "controls). The junction, its demand, its signal plans and every candidate are written in "
    "code; no proposer runs (Muse is not exercised), so this is not a test of discovery. It says "
    "nothing about the real NGSIM Lankershim intersection 2 baseline, or about any real junction."
)
REVIEW_NOTE = (
    "Revised on 2026-10-29 after an independent review: framed as a test of the evaluation and "
    "selection stage, not of discovery; an exact null and the mirror of the correction added as "
    "negative controls; provenance from git. The design numbers are unchanged."
)
Scenario = Literal["reallocation", "long-cycle"]
CorrectionMethod = Literal["webster_split_at_fixed_cycle", "webster_optimum_cycle_and_split"]
Role = Literal["correction", "halfway", "null", "mirror", "control"]
SCENARIO_TEXT: dict[str, dict[str, str]] = {
    "reallocation": {
        "purpose": "Positive control with a trade-off. Does the evaluation and selection stage "
        "confirm a pre-registered correction of a green split known to be wrong, and how does its "
        "selection rule treat a correction that helps one axis and lengthens the other's red? "
        "Negative controls (the exact null and the mirror of the correction) and small "
        "perturbations of the bad plan go through the same pipeline.",
        "design_history": "Designed and pre-registered before any simulation (2026-10-29). Its "
        "first run (synthetic-known-answer-20261029T112743Z) was made while the evaluation rules "
        "files were being changed, so it is not reproducible from one tree and is superseded "
        "(its record is kept in that run directory as "
        "superseded_record_rules_0.2.0_mixed_tree.json). " + REVIEW_NOTE,
    },
    "long-cycle": {
        "purpose": "To separate 'does the evaluation and selection stage confirm and select a "
        "known Pareto improvement' from 'the selection rule refuses trade-offs'. The bad plan "
        "already has Webster's split, but its cycle is far longer than the demand needs. The "
        "correction (Webster's optimum cycle and split) shortens every approach's red time, so by "
        "first principles it should improve every approach. Same negative controls.",
        "design_history": "Designed on 2026-10-29 AFTER seeing the reallocation scenario's "
        "result. There, the stage confirmed the Webster split correction's improvements, the "
        "largest of any candidate, but the selection rule refused it because the N-S queues "
        "worsened: a trade-off, which the rule refuses by design. This scenario tests whether "
        "the stage selects a known improvement that involves no such trade-off. Its design and "
        "pre-registration were fixed before any simulation of it and were not tuned after its "
        "simulation output. " + REVIEW_NOTE,
    },
}
BUNDLE_LIMITATIONS = (
    SYNTHETIC_LABEL,
    "Movement counts are the designed hourly demand (assumed), written as a one-hour count.",
)
DESIGNED_DEMAND_NOTES = (
    "Report text written for counted demand does not apply to this designed demand: the clip "
    "length ('Counts come from a 3600 s clip; flow intervals are wide'), the note on stop-line "
    "discharge counts, and the Poisson 95% flow interval shown for each movement describe "
    "counting vehicles in a clip. Here the flows are designed, so those intervals carry no "
    "information.",
    "The bundle's instruction to a model ('state expected effects in words, not numbers') does "
    "not apply: no model was asked; every candidate was written by code from the "
    "pre-registration.",
)
ROLE_TEXT: dict[str, str] = {
    "correction": "positive control: the pre-registered correction, written by code",
    "halfway": "half-way between the bad plan and the correction",
    "null": "negative control: the exact null, the baseline plan itself relabelled. The "
    "validator rejects a timing equal to the baseline's ('the timing is the same as the "
    "baseline'), so it is expected never to be simulated and never selected. Had it run, its "
    "runs would equal the baseline's, every difference would be 0 and every improvement test "
    "untested (zero standard deviation)",
    "mirror": "negative control: the correction's change applied in the opposite direction, "
    "moved into the validator's limits if needed; expected worse and never selected",
    "control": "a small perturbation (every green moved by an integer in [-5, +5] s) of the "
    "deliberately bad plan. Not a null: it may genuinely improve or worsen; its analytical "
    "prediction is recorded",
}
WEBSTER_FORMULA = {
    "flow_ratio": "y_i = max over the approaches served in phase i of q_a / (n_lanes_a * s)",
    "total_lost_time": "L = n_phases * l",
    "optimum_cycle": "C0 = (1.5 L + 5) / (1 - Y), with Y = sum_i y_i",
    "effective_green": "g_i = (C - L) * y_i / Y",
    "displayed_green": "G_i = g_i - I + l (with l = I, as assumed here, G_i = g_i)",
    "whole_seconds": "largest remainder, keeping sum_i G_i = C - n_phases * I; a phase below the "
    "minimum green gets the minimum and the others share the rest in proportion",
    "delay_for_predictions_only": "Webster (1958): d = C(1-lam)^2 / (2(1-lam*x)) + x^2/(2q(1-x)) "
    "- 0.65 (C/q^2)^(1/3) x^(2+5 lam), lam = G/C, x = q/(s*lam), q and s in veh/s",
    "queues_for_predictions_only": "mean queue (veh) = q*d (Little's law); queue at the end of "
    "red (veh) = q*r with red r = C - G",
}
CODE_MODULES = (
    "cairoflow_evaluation.known_answer",
    "cairoflow_evaluation.compare",
    "cairoflow_evaluation.eligibility",
    "cairoflow_evaluation.significance",
    "cairoflow_evaluation.metrics",
    "cairoflow_evaluation.report",
    "cairoflow_contracts.evidence",
    "cairoflow_contracts.experiment",
    "cairoflow_contracts.reasoning",
    "cairoflow_contracts.report",
    "cairoflow_perception.world_model",
    "cairoflow_reasoning.evidence",
    "cairoflow_simulation.validation",
    "cairoflow_simulation.candidates",
    "cairoflow_simulation.runner",
    "cairoflow_simulation.geometry_compiler",
    "cairoflow_simulation.demand",
    "cairoflow_simulation.network",
)


class KnownAnswerError(ValueError):
    """The design or the compiled network cannot support the known-answer test."""


class KnownAnswerDesign(BaseModel):
    """One scenario's design. Every value is assumed (written in code), none observed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scenario: Scenario = "reallocation"
    correction_method: CorrectionMethod = "webster_split_at_fixed_cycle"
    flows_veh_per_h: dict[Arm, int] = Field(
        default_factory=lambda: {"N": 150, "E": 300, "S": 150, "W": 300},
        description="through movements only, per approach",
    )
    lanes_per_approach: int = Field(1, ge=1)
    lane_width_m: float = 3.5
    saturation_flow_veh_per_h_per_lane: float = Field(1800.0, gt=0, description="assumed")
    bad_greens_s: dict[str, int] = Field(
        default_factory=lambda: {"EW": 20, "NS": 40}, description="the deliberately bad plan"
    )
    yellow_s: int = 3
    all_red_s: int = 2
    lost_time_per_phase_s: float = Field(5.0, description="assumed equal to the intergreen")
    min_green_s: float = MIN_GREEN_S
    arm_length_m: float = 200.0
    stop_line_offset_m: float = 10.0

    @property
    def intergreen_s(self) -> int:
        return self.yellow_s + self.all_red_s

    def cycle_s(self, greens: Mapping[str, int]) -> int:
        return sum(greens.values()) + len(AXES) * self.intergreen_s


SCENARIOS: dict[str, KnownAnswerDesign] = {
    "reallocation": KnownAnswerDesign(),
    "long-cycle": KnownAnswerDesign(
        scenario="long-cycle",
        correction_method="webster_optimum_cycle_and_split",
        flows_veh_per_h={"N": 300, "E": 450, "S": 300, "W": 450},
        bad_greens_s={"EW": 84, "NS": 56},
    ),
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_new(path: Path, text: str) -> str:
    """Write `text` to a new file (never overwrites) and return its SHA-256."""
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return sha256_text(text)


def design_sha256(design: KnownAnswerDesign) -> str:
    return sha256_text(design.model_dump_json())


# --- The expected correction, from the design alone ------------------------------------------


def flow_ratios(design: KnownAnswerDesign) -> dict[str, float]:
    """Critical flow ratio y per phase: the largest q / s among the approaches it serves."""
    s = design.saturation_flow_veh_per_h_per_lane * design.lanes_per_approach
    return {axis: max(design.flows_veh_per_h[a] for a in arms) / s for axis, arms in AXES.items()}


def whole_seconds(values: Mapping[str, float], total: int, minimum: float) -> dict[str, int]:
    """Whole-second greens adding up to `total`: largest remainder, ties to the earlier phase;
    a value below `minimum` is raised to it and the others share the rest in proportion."""
    names = list(values)
    if total < len(names) * math.ceil(minimum):
        raise KnownAnswerError(f"{total} s cannot give every phase {minimum:g} s")
    fixed: dict[str, int] = {}
    while True:
        free = [n for n in names if n not in fixed]
        if not free:
            raise KnownAnswerError("every phase is at the minimum green")
        remaining = total - sum(fixed.values())
        weight = sum(max(values[n], 0.0) for n in free)
        scaled = {
            n: remaining * max(values[n], 0.0) / weight if weight > 0 else remaining / len(free)
            for n in free
        }
        low = [n for n in free if scaled[n] < minimum]
        if not low:
            break
        for n in low:
            fixed[n] = math.ceil(minimum)
    floors = {n: math.floor(scaled[n] + 1e-9) for n in free}
    left = remaining - sum(floors.values())
    for n in sorted(free, key=lambda n: (-(scaled[n] - floors[n]), names.index(n)))[:left]:
        floors[n] += 1
    return {n: fixed[n] if n in fixed else floors[n] for n in names}


def webster_optimum_cycle_s(ratios: Mapping[str, float], lost_time_per_phase_s: float) -> float:
    """Webster's optimum cycle C0 = (1.5 L + 5) / (1 - Y)."""
    y_total = sum(ratios.values())
    if not 0 < y_total < 1:
        raise KnownAnswerError(f"Y = {y_total:g}: the design must be undersaturated (0 < Y < 1)")
    return (1.5 * len(ratios) * lost_time_per_phase_s + 5) / (1 - y_total)


def admissible_cycle(c0: float, n_phases: int, intergreen_s: int, min_green_s: float) -> dict:
    """C0 rounded up to whole seconds, then moved into the validator's cycle limits if outside
    (and up to what minimum greens need)."""
    rounded = math.ceil(c0 - 1e-9)
    lowest = max(MIN_CYCLE_S, n_phases * (intergreen_s + math.ceil(min_green_s)))
    cycle = int(min(max(rounded, lowest), MAX_CYCLE_S))
    return {
        "webster_optimum_cycle_s": round(c0, 3),
        "rounded_up_s": rounded,
        "validator_cycle_limits_s": [MIN_CYCLE_S, MAX_CYCLE_S],
        "admissible_as_rounded": MIN_CYCLE_S <= rounded <= MAX_CYCLE_S,
        "cycle_s": cycle,
        "moved_into_limits": cycle != rounded,
    }


def webster_split(
    ratios: Mapping[str, float],
    cycle_s: float,
    intergreen_s: float,
    lost_time_per_phase_s: float,
    min_green_s: float,
) -> dict[str, Any]:
    """Webster's green split at a given cycle (WEBSTER_FORMULA); pure arithmetic."""
    phases = list(ratios)
    y_total = sum(ratios.values())
    if not 0 < y_total < 1:
        raise KnownAnswerError(f"Y = {y_total:g}: the design must be undersaturated (0 < Y < 1)")
    lost = len(phases) * lost_time_per_phase_s
    effective_total = cycle_s - lost
    if effective_total <= 0:
        raise KnownAnswerError("the cycle is shorter than the lost time")
    effective = {p: effective_total * ratios[p] / y_total for p in phases}
    displayed = {p: effective[p] - intergreen_s + lost_time_per_phase_s for p in phases}
    total = cycle_s - len(phases) * intergreen_s
    if abs(total - round(total)) > 1e-9:
        raise KnownAnswerError("the green time to share is not whole seconds")
    return {
        "Y": round(y_total, 6),
        "total_lost_time_s": lost,
        "effective_green_total_s": effective_total,
        "effective_greens_s": {p: round(v, 6) for p, v in effective.items()},
        "displayed_greens_unrounded_s": {p: round(v, 6) for p, v in displayed.items()},
        "greens_s": whole_seconds(displayed, int(round(total)), min_green_s),
    }


def correction_plan(design: KnownAnswerDesign) -> tuple[dict[str, Any], dict[str, Any]]:
    """(cycle choice, Webster split at that cycle) for the design's correction method."""
    ratios = flow_ratios(design)
    c0 = webster_optimum_cycle_s(ratios, design.lost_time_per_phase_s)
    if design.correction_method == "webster_split_at_fixed_cycle":
        cycle = {
            "method": "kept at the baseline's cycle",
            "cycle_s": design.cycle_s(design.bad_greens_s),
            "webster_optimum_cycle_s_information_only": round(c0, 3),
        }
    else:
        cycle = {
            "method": "Webster's optimum cycle rounded up to whole seconds, within the "
            "validator's cycle limits",
            **admissible_cycle(c0, len(AXES), design.intergreen_s, design.min_green_s),
        }
    split = webster_split(
        ratios, cycle["cycle_s"], design.intergreen_s, design.lost_time_per_phase_s,
        design.min_green_s,
    )  # fmt: skip
    return cycle, split


def expected_correction(design: KnownAnswerDesign) -> dict[str, Any]:
    """The pre-registered known-better plan, with its sensitivity to the assumptions."""
    cycle, split = correction_plan(design)
    bad = dict(design.bad_greens_s)
    if split["greens_s"] == bad:
        raise KnownAnswerError("the bad plan already is the expected correction")
    if any(not MIN_GREEN_S <= g <= MAX_GREEN_S for g in split["greens_s"].values()):
        raise KnownAnswerError("the correction's greens are outside the validator's limits")

    def variant(update: dict[str, float]) -> dict[str, Any]:
        other_cycle, other = correction_plan(design.model_copy(update=update))
        return {"cycle_s": other_cycle["cycle_s"], "greens_s": other["greens_s"]}

    fixed = design.correction_method == "webster_split_at_fixed_cycle"
    robustness = {}
    for sat in (1500.0, 1800.0, 2000.0):
        other = design.model_copy(update={"saturation_flow_veh_per_h_per_lane": sat})
        base = analytical_prediction(other, bad, None)
        found = analytical_prediction(other, split["greens_s"], base)
        robustness[f"{sat:g}"] = {
            "network_mean_delay_change_s": found["change_vs_baseline_s"]["mean_delay_s"],
            "pareto_improving": found["pareto_improving"],
            "expected_eligible": found["expected_eligible"],
        }
    return {
        "kind": "assumed (analytical; computed from the design only, before any simulation)",
        "method": "Webster (1958) green split at the baseline's cycle; clearances unchanged"
        if fixed
        else "Webster (1958) optimum cycle and split; clearances unchanged",
        "formula": WEBSTER_FORMULA,
        "assumptions": [
            f"saturation flow s = {design.saturation_flow_veh_per_h_per_lane:g} veh/h per lane "
            "(textbook base value, not calibrated against SUMO). Every approach has the same "
            "lanes, so s cancels out of the split; it matters for the optimum cycle and the "
            "degrees of saturation (see sensitivity)",
            f"lost time per phase l = {design.lost_time_per_phase_s:g} s, equal to the intergreen "
            f"(yellow {design.yellow_s} s + all-red {design.all_red_s} s): start-up loss and use "
            "of the yellow assumed to cancel, so effective green = displayed green",
            f"cycle kept at the baseline's {cycle['cycle_s']} s"
            if fixed
            else "cycle: Webster's C0 rounded up to whole seconds (the conservative side of its "
            f"flat optimum), moved into the validator's {MIN_CYCLE_S:g}-{MAX_CYCLE_S:g} s if "
            "outside",
            "yellow, all-red and phase order unchanged",
            f"minimum green {design.min_green_s:g} s (the validator's MIN_GREEN_S)",
            "through movements only; no turning, pedestrian or lane-utilisation factors",
        ],
        "inputs": {
            "flows_veh_per_h": dict(design.flows_veh_per_h),
            "lanes_per_approach": design.lanes_per_approach,
            "saturation_flow_veh_per_h_per_lane": design.saturation_flow_veh_per_h_per_lane,
            "bad_greens_s": bad,
            "bad_cycle_s": design.cycle_s(bad),
            "intergreen_s": design.intergreen_s,
            "lost_time_per_phase_s": design.lost_time_per_phase_s,
        },
        "flow_ratios": {p: round(v, 6) for p, v in flow_ratios(design).items()},
        "cycle": cycle,
        **split,
        "cycle_s": cycle["cycle_s"],
        "sensitivity": {
            "by_lost_time_per_phase_s": {
                f"{lost:g}": variant({"lost_time_per_phase_s": lost})
                for lost in (3.0, 4.0, 5.0, 6.0, 7.0)
            },
            "by_saturation_flow_veh_per_h_per_lane": {
                f"{sat:g}": variant({"saturation_flow_veh_per_h_per_lane": sat})
                for sat in (1500.0, 1800.0, 2000.0)
            },
            "pre_registered_correction_judged_with_other_saturation_flows": robustness,
        },
    }


# --- Analytical predictions (assumed, uncalibrated model; for interpreting results only) -----


def webster_delay_s(
    cycle_s: float, green_s: float, flow_veh_per_h: float, saturation_flow_veh_per_h: float
) -> float | None:
    """Webster's (1958) average delay per vehicle; None when x >= 1 (formula invalid)."""
    lam = green_s / cycle_s
    q, s = flow_veh_per_h / 3600.0, saturation_flow_veh_per_h / 3600.0
    x = q / (s * lam)
    if x >= 1:
        return None
    uniform = cycle_s * (1 - lam) ** 2 / (2 * (1 - lam * x))
    overflow = x**2 / (2 * q * (1 - x))
    correction = 0.65 * (cycle_s / q**2) ** (1 / 3) * x ** (2 + 5 * lam)
    return uniform + overflow - correction


def _direction(change: float | None) -> str:
    if change is None:
        return "unknown (oversaturated: formula invalid)"
    if change <= -DEAD_BAND_S:
        return "better"
    if change >= DEAD_BAND_S:
        return "worse"
    return "about_unchanged"


def directions(changes: Mapping[str, Any]) -> dict[str, Any]:
    """Directions of a `change_vs_baseline_s` block, with the DEAD_BAND_S dead-band."""
    return {
        "mean_delay_s": _direction(changes["mean_delay_s"]),
        "delay": {arm: _direction(v) for arm, v in changes["delay_s"].items()},
        "red": {arm: _direction(v) for arm, v in changes["red_s"].items()},
    }


def analytical_prediction(
    design: KnownAnswerDesign, greens: Mapping[str, int], baseline: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Webster delays, red times and queues per approach for one plan, and the change from
    `baseline` (this function's prediction for the baseline plan)."""
    cycle = design.cycle_s(greens)
    s = design.saturation_flow_veh_per_h_per_lane * design.lanes_per_approach
    delays: dict[str, float | None] = {}
    saturation: dict[str, float] = {}
    red: dict[str, int] = {}
    mean_queue: dict[str, float | None] = {}
    end_of_red: dict[str, float] = {}
    for arm in ARM_ORDER:
        flow, green = design.flows_veh_per_h[arm], greens[AXIS_OF[arm]]
        red[arm] = cycle - green
        saturation[arm] = round(flow / (s * green / cycle), 4)
        d = webster_delay_s(cycle, green, flow, s)
        delays[arm] = None if d is None else round(d, 3)
        mean_queue[arm] = None if d is None else round(flow / 3600 * d, 3)
        end_of_red[arm] = round(flow / 3600 * red[arm], 3)
    weighted = [(design.flows_veh_per_h[a], d) for a, d in delays.items() if d is not None]
    mean = (
        round(sum(q * d for q, d in weighted) / sum(q for q, _ in weighted), 3)
        if len(weighted) == len(delays)
        else None
    )
    found: dict[str, Any] = {
        "greens_s": dict(greens),
        "cycle_s": cycle,
        "red_s": red,
        "degree_of_saturation": saturation,
        "delay_s": delays,
        "network_mean_delay_s": mean,
        "mean_queue_veh_littles_law": mean_queue,
        "queue_at_end_of_red_veh": end_of_red,
    }
    if baseline is not None:

        def diff(new: float | None, old: float | None) -> float | None:
            return None if new is None or old is None else round(new - old, 3)

        changes = {
            "mean_delay_s": diff(mean, baseline["network_mean_delay_s"]),
            "delay_s": {arm: diff(delays[arm], baseline["delay_s"][arm]) for arm in ARM_ORDER},
            "red_s": {arm: red[arm] - baseline["red_s"][arm] for arm in ARM_ORDER},
        }
        direction = directions(changes)
        arms = [*direction["delay"].values(), *direction["red"].values()]
        found["change_vs_baseline_s"] = changes
        found["direction"] = direction
        found["expected_eligible"] = direction["mean_delay_s"] == "better" and all(
            d in ("better", "about_unchanged") for d in arms
        )
        found["pareto_improving"] = direction["mean_delay_s"] == "better" and all(
            d == "better" for d in arms
        )
    return found


def predicted_change(prediction: Mapping[str, Any] | None, metric_name: str) -> float | None:
    """The analytical change for a metric: network mean delay for mean_delay_s; the arm's delay
    for its mean queue (Little's law at a fixed arrival rate); the arm's red time for its maximum
    queue. None for throughput or without a prediction."""
    if prediction is None or "change_vs_baseline_s" not in prediction:
        return None
    changes = prediction["change_vs_baseline_s"]
    base, _, arm = metric_name.partition(".")
    if base == "mean_delay_s":
        return changes["mean_delay_s"]
    if base == "mean_queue_m":
        return changes["delay_s"].get(arm)
    if base == "max_queue_m":
        return changes["red_s"].get(arm)
    return None


def predicted_direction(prediction: Mapping[str, Any] | None, metric_name: str) -> str | None:
    """The analytical direction for a metric (see predicted_change), with the dead-band."""
    if prediction is None or "direction" not in prediction:
        return None
    direction = prediction["direction"]
    base, _, arm = metric_name.partition(".")
    if base == "mean_delay_s":
        return direction["mean_delay_s"]
    if base == "mean_queue_m":
        return direction["delay"].get(arm)
    if base == "max_queue_m":
        return direction["red"].get(arm)
    return None


# --- Candidates --------------------------------------------------------------------------------


def draw_controls(
    base: Mapping[str, int],
    k: int,
    seed: int,
    exclude: Collection[tuple[int, ...]] = (),
    max_abs_s: int = MAX_PERTURBATION_S,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """K small perturbations: every phase green moved by an independent integer in [-max, +max]
    s, drawn from numpy's default_rng([seed, 0]). A draw equal to the baseline, to an excluded
    plan or to an earlier draw is redrawn (the validator would reject it as a duplicate); every
    redraw is listed."""
    rng = np.random.default_rng([seed, 0])
    seen = {tuple(base.values()), *exclude}
    controls: list[dict[str, Any]] = []
    redrawn: list[dict[str, Any]] = []
    while len(controls) < k:
        if len(controls) + len(redrawn) > 100 * (k + 1):
            raise KnownAnswerError("could not draw enough distinct perturbations")
        step = {axis: int(rng.integers(-max_abs_s, max_abs_s + 1)) for axis in base}
        greens = {axis: base[axis] + step[axis] for axis in base}
        if tuple(greens.values()) in seen:
            redrawn.append({"perturbation_s": step, "reason": "same plan as the baseline, another "
                            "pre-registered plan or an earlier draw"})  # fmt: skip
            continue
        seen.add(tuple(greens.values()))
        controls.append({"perturbation_s": step, "greens_s": greens})
    return controls, redrawn


def assign_ids(n: int, seed: int) -> list[str]:
    """Intervention ids for n candidates in canonical order, by a seeded permutation (numpy
    default_rng([seed, 1])), so no role always gets the lowest id (the last tie-break)."""
    order = np.random.default_rng([seed, 1]).permutation(n)
    return [f"I{int(position) + 1}" for position in order]


def halfway_plan(
    design: KnownAnswerDesign, bad: Mapping[str, int], correction: Mapping[str, int]
) -> dict[str, int]:
    """Greens half-way between the bad plan and the correction: the mean cycle (half up), its
    green time shared in proportion to the mean greens."""
    cycle = math.floor((design.cycle_s(bad) + design.cycle_s(correction)) / 2 + 0.5)
    middle = {axis: (bad[axis] + correction[axis]) / 2 for axis in AXES}
    return whole_seconds(middle, cycle - len(AXES) * design.intergreen_s, design.min_green_s)


def mirror_plan(
    design: KnownAnswerDesign, bad: Mapping[str, int], correction: Mapping[str, int]
) -> dict[str, Any]:
    """The correction's change applied in the opposite direction: 2 * bad - correction.

    When that leaves the validator's limits, the closest admissible plan is used: every green is
    scaled down by the largest common factor that respects the maximum green and the maximum
    cycle (keeping the split), then whole seconds with the minimum green (keeping the total)."""
    raw = {axis: 2 * bad[axis] - correction[axis] for axis in AXES}
    values = {axis: float(v) for axis, v in raw.items()}
    largest = max(values.values())
    factor = min(1.0, MAX_GREEN_S / largest, (MAX_CYCLE_S - len(AXES) * design.intergreen_s)
                 / sum(values.values()))  # fmt: skip
    scaled = {axis: v * factor for axis, v in values.items()}
    greens = whole_seconds(scaled, math.floor(sum(scaled.values()) + 1e-9), design.min_green_s)
    if any(not MIN_GREEN_S <= g <= MAX_GREEN_S for g in greens.values()):
        raise KnownAnswerError(f"no admissible mirror plan near {raw}")
    return {
        "raw_greens_s": raw,
        "greens_s": greens,
        "scale_factor": round(factor, 6),
        "adjusted": greens != raw,
        "rule": "2 * bad - correction; if outside the validator's limits, scaled down to the "
        "maximum green and cycle keeping the split, then whole seconds with the minimum green",
    }


def preregister(
    design: KnownAnswerDesign,
    settings: RunSettings,
    *,
    controls: int = 8,
    control_seed: int = DEFAULT_CONTROL_SEED,
    include_halfway: bool = True,
    written_at: datetime | None = None,
) -> dict[str, Any]:
    """Everything decided before any simulation: design, expected correction, candidates and
    analytical predictions. Uses no simulator output (no SUMO call happens in here)."""
    expectation = expected_correction(design)
    bad = dict(design.bad_greens_s)
    correction = expectation["greens_s"]
    mirror = mirror_plan(design, bad, correction)
    planned: list[dict[str, Any]] = [{"role": "correction", "greens_s": correction}]
    if include_halfway:
        planned.append({"role": "halfway", "greens_s": halfway_plan(design, bad, correction)})
    planned += [
        {"role": "null", "greens_s": dict(bad)},
        {"role": "mirror", "greens_s": mirror["greens_s"]},
    ]
    drawn, redrawn = draw_controls(
        bad, controls, control_seed, exclude={tuple(p["greens_s"].values()) for p in planned}
    )
    planned += [{"role": "control", "draw_index": i, **c} for i, c in enumerate(drawn)]
    ids = assign_ids(len(planned), control_seed)
    baseline_prediction = analytical_prediction(design, bad, None)
    candidates = []
    for iid, plan in zip(ids, planned, strict=True):
        greens = plan["greens_s"]
        cycle = design.cycle_s(greens)
        if not MIN_CYCLE_S <= cycle <= MAX_CYCLE_S:
            raise KnownAnswerError(f"{iid}: cycle {cycle} s is outside the validator's limits")
        candidates.append(
            {
                "intervention_id": iid,
                "role": plan["role"],
                "role_text": ROLE_TEXT[plan["role"]],
                "greens_s": greens,
                "cycle_s": cycle,
                "change_from_baseline_s": {axis: greens[axis] - bad[axis] for axis in AXES},
                "perturbation_s": plan.get("perturbation_s"),
                "draw_index": plan.get("draw_index"),
            }
        )
    candidates.sort(key=lambda c: id_order(c["intervention_id"]))
    predictions = {
        c["intervention_id"]: analytical_prediction(design, c["greens_s"], baseline_prediction)
        for c in candidates
    }
    by_role = {c["role"]: c["intervention_id"] for c in candidates if c["role"] != "control"}
    corrected = predictions[by_role["correction"]]
    mirrored = predictions[by_role["mirror"]]
    text = SCENARIO_TEXT[design.scenario]
    return {
        "label": SYNTHETIC_LABEL,
        "experiment": NAME,
        "scenario": design.scenario,
        "what_is_tested": "the evaluation and selection stage: the deterministic validator, "
        "paired SUMO runs, eligibility, paired comparisons, Holm's correction and the selection "
        "rule, on candidates whose answer is known in advance",
        "what_is_not_tested": "discovery: no proposer runs and Muse is not exercised; the "
        "correction is the pre-registered answer itself, written by code",
        "purpose": text["purpose"],
        "design_history": text["design_history"],
        "written_at_utc": (written_at or datetime.now(UTC)).isoformat(),
        "design": {
            "kind": "assumed (designed in code; nothing observed)",
            "design_sha256": design_sha256(design),
            **design.model_dump(mode="json"),
            "axes": {axis: list(arms) for axis, arms in AXES.items()},
            "movements": "through only: N->S, E->W, S->N, W->E; one approach lane per arm",
            "baseline_cycle_s": design.cycle_s(bad),
        },
        "expectation": expectation,
        "mirror": mirror,
        "candidates": candidates,
        "roles": {
            "correction": by_role["correction"],
            "halfway": by_role.get("halfway"),
            "null": by_role["null"],
            "mirror": by_role["mirror"],
        },
        "control_draw": {
            "kind": "assumed (random design choice)",
            "what_they_are": ROLE_TEXT["control"],
            "k": controls,
            "seed": control_seed,
            "generator": "numpy.random.default_rng([seed, 0]).integers(-5, 6) per phase, in "
            f"phase order {list(AXES)}",
            "range_s": [-MAX_PERTURBATION_S, MAX_PERTURBATION_S],
            "applied_to": "the baseline (bad) greens; clearances unchanged, so the cycle changes "
            "by the sum of the two perturbations (validator: cycle within "
            f"{MIN_CYCLE_S:g}-{MAX_CYCLE_S:g} s, greens within {MIN_GREEN_S:g}-{MAX_GREEN_S:g} s)",
            "redrawn": redrawn,
        },
        "id_assignment": {
            "method": "numpy.random.default_rng([seed, 1]).permutation over the canonical order "
            "(correction, half-way, null, mirror, perturbations in draw order)",
            "seed": control_seed,
        },
        "analytical_predictions": {
            "kind": "assumed (analytical Webster model with the stated saturation flow; NOT "
            "calibrated against SUMO; used only to label directions)",
            "dead_band_s": DEAD_BAND_S,
            "metric_mapping": "mean_delay_s -> network mean delay; mean_queue_m.<arm> -> the "
            "arm's delay (Little's law, fixed arrivals); max_queue_m.<arm> -> the arm's red time; "
            "throughput -> no prediction",
            "baseline": baseline_prediction,
            "candidates": predictions,
        },
        "pre_registered_predictions": {
            "correction_reduces_mean_delay": corrected["direction"]["mean_delay_s"] == "better",
            "correction_expected_pareto_improving": corrected["pareto_improving"],
            "correction_expected_eligible": corrected["expected_eligible"],
            "candidates_expected_eligible": [
                iid for iid, p in predictions.items() if p["expected_eligible"]
            ],
            "null_expected": "rejected by the validator (same timing as the baseline); never "
            "simulated; never selected",
            "null_expected_selected": False,
            "mirror_expected": "worse: predicted mean delay change "
            + (
                f"{mirrored['change_vs_baseline_s']['mean_delay_s']:+g} s"
                if mirrored["change_vs_baseline_s"]["mean_delay_s"] is not None
                else "unknown: at least one approach oversaturated (degree of saturation "
                f"{max(mirrored['degree_of_saturation'].values()):g}), so it is expected not to "
                "clear its demand (incomplete) and to be not selectable"
            )
            + "; never selected",
            "mirror_expected_selected": False,
            "definition": "expected eligible: predicted mean delay better and no approach's "
            "delay or red time predicted worse (beyond the dead-band). Pareto-improving: every "
            "approach's delay and red time predicted better",
        },
        "settings": settings.model_dump(mode="json"),
        "rules": {
            "rules_version": RULES_VERSION,
            "calculation_version": CALCULATION_VERSION,
            "family_alpha": FAMILY_ALPHA,
        },
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "eclipse-sumo": metadata.version("eclipse-sumo"),
        },
    }


def preregistration_content_sha256(prereg: Mapping[str, Any]) -> str:
    """SHA-256 of the pre-registration without its timestamp (`written_at_utc`): the same design,
    settings, seed and software give the same content hash on every run."""
    content = {key: value for key, value in prereg.items() if key != "written_at_utc"}
    return sha256_text(json.dumps(content, indent=2, sort_keys=True))


# --- Provenance ---------------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def code_files() -> dict[str, Path]:
    """Module -> its source file, for the modules a run depends on."""
    return {name: Path(str(import_module(name).__file__)).resolve() for name in CODE_MODULES}


def code_hashes() -> dict[str, str]:
    """Module -> SHA-256 of its source file."""
    return {name: _sha256_file(path) for name, path in code_files().items()}


def git_state() -> dict[str, Any]:
    """The git commit of the repository holding this module, and whether the files a run imports
    (and its script) are clean against it (`git status --porcelain` on them)."""

    def git(*args: str, cwd: Path) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        root = Path(git("rev-parse", "--show-toplevel", cwd=Path(__file__).resolve().parent))
        commit = git("rev-parse", "HEAD", cwd=root)
        paths = [os.path.relpath(path, root) for path in code_files().values()]
        if (root / SCRIPT).is_file():
            paths.append(SCRIPT)
        status = git("status", "--porcelain", "--", *paths, cwd=root)
    except (OSError, subprocess.CalledProcessError) as error:
        return {"commit": None, "clean": None, "error": str(error)[:300]}
    dirty = sorted(line[3:] for line in status.splitlines() if line.strip())
    return {
        "commit": commit,
        "files": sorted(Path(p).as_posix() for p in paths),
        "dirty_files": dirty,
        "clean": not dirty,
    }


# --- The synthetic junction, its plan and its demand ------------------------------------------


def synthetic_geometry(design: KnownAnswerDesign) -> IntersectionGeometry:
    """One through-only approach lane per arm on the right-hand side, stop lines 10 m out.

    The ground frame anchor is the SHA-256 of the design (there is no dataset or calibration)."""
    w, near, far = design.lane_width_m, design.stop_line_offset_m, design.stop_line_offset_m + 50
    shapes: dict[Arm, tuple[tuple[float, float], ...]] = {
        "N": ((-w, near), (0, near), (0, far), (-w, far)),
        "E": ((near, 0), (far, 0), (far, w), (near, w)),
        "S": ((0, -far), (w, -far), (w, -near), (0, -near)),
        "W": ((-far, -w), (-near, -w), (-near, 0), (-far, 0)),
    }
    stops: dict[Arm, tuple[tuple[float, float], tuple[float, float]]] = {
        "N": ((-w, near), (0, near)),
        "E": ((near, 0), (near, w)),
        "S": ((0, -near), (w, -near)),
        "W": ((-near, -w), (-near, 0)),
    }
    source = f"{NAME}: drawn in code (synthetic)"
    return IntersectionGeometry(
        geometry_id=f"{NAME}-4arm",
        clip_id=NAME,
        ground_source_sha256=design_sha256(design),
        lanes=tuple(
            LanePolygon(
                lane_id=f"{arm}1",
                arm=arm,
                direction="approach",
                polygon_m=shapes[arm],
                allowed_turns=("through",),
                provenance="synthetic",
                source=source,
            )
            for arm in ARM_ORDER
        ),  # fmt: skip
        gates=tuple(
            Gate(
                arm=arm,
                role="stop_line",
                segment_m=stops[arm],
                provenance="synthetic",
                source=source,
            )
            for arm in ARM_ORDER
        ),  # fmt: skip
    )


def two_phase_timeline(
    design: KnownAnswerDesign, greens: Mapping[str, int], cycles: int = 3
) -> SignalTimeline:
    """An assumed two-phase timeline: E-W green, yellow, all-red, then N-S likewise."""
    cycle = design.cycle_s(greens)
    rows: dict[Arm, list[list[Any]]] = {arm: [] for arm in ARM_ORDER}

    def add(arm: Arm, state: str, start: float, end: float) -> None:
        if end <= start:
            return
        if rows[arm] and rows[arm][-1][0] == state and rows[arm][-1][2] == start:
            rows[arm][-1][2] = end
        else:
            rows[arm].append([state, start, end])

    for k in range(cycles):
        offset = 0
        for axis in AXES:
            g, t = greens[axis], k * cycle
            for arm in AXES[axis]:
                add(arm, "red", t, t + offset)
                add(arm, "green", t + offset, t + offset + g)
                add(arm, "yellow", t + offset + g, t + offset + g + design.yellow_s)
                add(arm, "red", t + offset + g + design.yellow_s, t + cycle)
            offset += g + design.intergreen_s
    return SignalTimeline(
        timeline_id=f"{NAME}-{design.scenario}-bad-plan",
        clip_id=NAME,
        label="assumed",
        source=f"{NAME} ({design.scenario}): deliberately bad two-phase plan, written in code",
        window_s=float(cycles * cycle),
        intervals=tuple(
            SignalInterval(arm=arm, state=state, start_s=float(a), end_s=float(b))
            for arm in ARM_ORDER
            for state, a, b in rows[arm]
        ),
    )


def synthetic_world_model(
    design: KnownAnswerDesign, geometry: IntersectionGeometry, now: datetime
) -> TrafficWorldModel:
    """The designed demand as a one-hour count per through movement (count = veh/h).

    Tracks are placeholders, one per designed vehicle (class 'car' -> SUMO passenger); their
    times are not used by the demand generator, which reads only the flow. Provenance kind is
    `assumed`, which the report and bundle carry onto the movement numbers."""
    digest = design_sha256(design)
    movements = []
    for approach in ARM_ORDER:
        exit_arm = OPPOSITE[approach]
        count = int(round(design.flows_veh_per_h[approach] * WINDOW_S / 3600.0))
        low, high = poisson_interval(count)
        scale = 3600.0 / WINDOW_S
        movements.append(
            MovementCount(
                approach=approach,
                exit_arm=exit_arm,
                turn=turn_for(approach, exit_arm),
                count_veh=count,
                flow_veh_per_h=count * scale,
                flow_ci95_veh_per_h=(low * scale, high * scale),
                marked_lane_ids=(f"{approach}1",),
                tracks=tuple(
                    MovementTrack(
                        track_id=f"car:{approach}{exit_arm}-{n:04d}",
                        lane_id=f"{approach}1",
                        stop_line_time_s=0.0,
                        exit_line_time_s=0.0,
                        follows_markings=True,
                    )
                    for n in range(1, count + 1)
                ),  # fmt: skip
            )
        )
    return TrafficWorldModel(
        clip_id=geometry.clip_id,
        geometry_id=geometry.geometry_id,
        geometry_sha256=sha256_text(geometry.model_dump_json()),
        ground_source_sha256=digest,
        tracks_sha256=digest,
        window_s=WINDOW_S,
        ground_tracks=(),
        movements=tuple(movements),
        ambiguous=(),
        queues=(),
        signal_timeline=None,
        signal_per_second=(),
        end_of_red_queues=(),
        signal_notes=(),
        provenance=Provenance(
            kind="assumed",
            produced_by="cairoflow_evaluation.known_answer.synthetic_world_model",
            produced_at_utc=now,
            input_sha256=(digest,),
            parameters={"synthetic": True, "scenario": design.scenario, "window_s": WINDOW_S},
        ),
    )


def phases_by_axis(network: CompiledNetwork, design: KnownAnswerDesign) -> dict[str, str]:
    """The compiled adjustable phase of each axis, checked against the design's bad plan."""
    found: dict[str, str] = {}
    program = network.manifest.signal_program
    for phase in program.phases:
        if phase.adjustable:
            green = {arm for arm, state in phase.arm_states.items() if state == "green"}
            axis = next((a for a, arms in AXES.items() if green == set(arms)), None)
            if axis is None or axis in found:
                raise KnownAnswerError(f"unexpected adjustable phase {phase.name}")
            if phase.duration_s != design.bad_greens_s[axis]:
                raise KnownAnswerError(
                    f"{phase.name} lasts {phase.duration_s:g} s, not the design's"
                )
            found[axis] = phase.name
        elif phase.duration_s not in (design.yellow_s, design.all_red_s):
            raise KnownAnswerError(f"clearance {phase.name} lasts {phase.duration_s:g} s")
    if set(found) != set(AXES) or program.cycle_s != design.cycle_s(design.bad_greens_s):
        raise KnownAnswerError("the compiled program is not the designed two-phase plan")
    return found


def proposals(
    prereg: Mapping[str, Any], phase_of: Mapping[str, str], program_id: str
) -> list[InterventionProposal]:
    """One signal_timing proposal per pre-registered candidate, written by code (not a model).
    The null proposes the baseline's own greens and goes to the validator like every other."""
    return [
        InterventionProposal(
            intervention_id=c["intervention_id"],
            type="signal_timing",
            target_program=program_id,
            phase_changes=tuple(
                PhaseChange(phase_name=phase_of[axis], green_s=float(c["greens_s"][axis]))
                for axis in AXES
            ),
            rationale=f"pre-registered {c['role']} candidate of the {NAME} test (written by code)",
            expected_tradeoffs=(),
            preconditions=(),
            addresses_hypothesis_ids=(),
            evidence_ids=("sig.program", *(f"mv.{arm}{OPPOSITE[arm]}" for arm in ARM_ORDER)),
        )
        for c in prereg["candidates"]
    ]


# --- What happened, read from the report and the evaluation ----------------------------------

FALSE_POSITIVE = (
    "a Holm-confirmed improvement (any candidate, any improvement metric) whose pre-registered "
    "analytical change for that metric has the opposite sign, i.e. is predicted > 0 (worse) by "
    "any amount. The reference is an uncalibrated Webster model (see model_agreement), so a "
    "count here is a disagreement with that model, not proof of a spurious result"
)


def _agreement(verdict: str, direction: str | None) -> str:
    if direction is None or direction.startswith("unknown"):
        return "no_prediction"
    if verdict == "uninformative":
        return "uninformative"
    pairs = {("improves", "better"), ("worsens", "worse"), ("inconclusive", "about_unchanged")}
    if (verdict, direction) in pairs:
        return "agree"
    if (verdict, direction) in {("improves", "worse"), ("worsens", "better")}:
        return "opposite"
    if verdict == "inconclusive":
        return "predicted_change_not_resolved"
    return "significant_where_about_unchanged_predicted"


def baseline_censoring(evaluation: EvaluationResult) -> dict[str, Any]:
    """Arms whose queue was censored in the baseline (a lane filled, or vehicles held off the
    network at their entry), and the seeds: their queue claims are untestable for every
    candidate."""
    filled: dict[str, set[int]] = defaultdict(set)
    for seed, lanes in evaluation.baseline_censored_lanes.items():
        for lane in lanes:
            filled[lane.split("_", 1)[0]].add(int(seed))
    held: dict[str, set[int]] = defaultdict(set)
    for seed, arms in (getattr(evaluation, "baseline_entry_blocked", None) or {}).items():
        for arm in arms:
            held[arm].add(int(seed))
    arms = sorted(set(filled) | set(held), key=lambda a: ARM_ORDER.index(a))  # type: ignore[arg-type]

    def on(seeds: set[int]) -> str:
        return f"seed{'s' if len(seeds) > 1 else ''} {', '.join(map(str, sorted(seeds)))}"

    parts = []
    for arm in arms:
        how = []
        if filled.get(arm):
            how.append(f"a lane filled on {on(filled[arm])}")
        if held.get(arm):
            how.append(f"vehicles held off at entry on {on(held[arm])}")
        parts.append(f"arm {arm} ({'; '.join(how)})")
    return {
        "lane_filled": {arm: sorted(seeds) for arm, seeds in filled.items()},
        "entry_blocked": {arm: sorted(seeds) for arm, seeds in held.items()},
        "queue_claims_untestable_for_every_candidate": [
            f"{kind}_queue_m.{arm}" for arm in arms for kind in ("mean", "max")
        ],
        "text": (
            "In the baseline the queue was censored on " + ", ".join(parts) + ", so those arms' "
            "queue claims are untestable for every candidate (lower bounds)."
        )
        if parts
        else "No baseline queue was censored.",
    }


def summarize_outcome(
    prereg: Mapping[str, Any],
    evaluation: EvaluationResult,
    selected: str | None,
    selection_reason: str,
    rejected: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Compare the stage's result with the pre-registration. Reads, never selects: eligibility
    and confirmations come from the report's `candidate_standings`; `rejected` maps the ids the
    validator rejected to its reasons."""
    rejected = dict(rejected or {})
    roles = {c["intervention_id"]: c["role"] for c in prereg["candidates"]}
    greens = {c["intervention_id"]: c["greens_s"] for c in prereg["candidates"]}
    predictions = prereg["analytical_predictions"]["candidates"]
    standings = candidate_standings(evaluation)
    status = {e.candidate: e.status for e in evaluation.eligibility}
    verdicts: dict[str, dict[str, str]] = defaultdict(dict)
    for c in evaluation.comparisons:
        verdicts[c.candidate][c.metric_name] = c.verdict
    tests: dict[str, dict[str, Any]] = defaultdict(dict)
    for test in evaluation.improvement_tests:
        tests[test.candidate][test.metric_name] = test

    per: dict[str, dict[str, Any]] = {}
    agreement: Counter[str] = Counter()
    opposite: list[dict[str, Any]] = []
    opportunities = 0
    for iid in sorted(roles, key=id_order):
        standing = standings.get(iid)
        prediction = predictions.get(iid)
        conf = list(standing.confirmed) if standing else []
        own = verdicts.get(iid, {})
        for metric, verdict in own.items():
            found = _agreement(verdict, predicted_direction(prediction, metric))
            agreement[found] += 1
            if found == "opposite":
                change = predicted_change(prediction, metric)
                opposite.append(
                    {"intervention_id": iid, "metric": metric, "verdict": verdict,
                     "predicted_change": change}
                )  # fmt: skip
        for metric, test in tests.get(iid, {}).items():
            if test.p_value is not None and (predicted_change(prediction, metric) or 0.0) > 0:
                opportunities += 1
        if iid in rejected:
            note = "rejected by the validator: " + "; ".join(rejected[iid])
        else:
            note = (standing.note if standing else "not simulated") or None
        per[iid] = {
            "role": roles[iid],
            "validator": "rejected" if iid in rejected else "accepted",
            "simulation_status": status.get(iid, "not_simulated"),
            "holm_confirmed_improvements": conf,
            "worsens_uncorrected": [m for m, v in own.items() if v == "worsens"],
            "uninformative": [m for m, v in own.items() if v == "uninformative"],
            "improves_uncorrected_not_holm_confirmed": [
                m for m, v in own.items() if v == "improves" and m not in conf
            ],
            "held_off_arms": list(getattr(standing, "held_off", ()) or ()) if standing else [],
            "eligible": bool(standing and standing.eligible and iid not in rejected),
            "not_eligible_because": note,
            "selected": iid == selected,
            "missed_predicted_improvements": [
                m
                for m in tests.get(iid, {})
                if predicted_direction(prediction, m) == "better" and m not in conf
            ],
            "confirmed_contradicting_prediction": [
                {"metric": m, "predicted_change": predicted_change(prediction, m)}
                for m in conf
                if (predicted_change(prediction, m) or 0.0) > 0
            ],
            "confirmed_where_about_unchanged_predicted": [
                m for m in conf if predicted_direction(prediction, m) == "about_unchanged"
            ],
        }

    role_ids = prereg["roles"]
    perturbations = sorted((i for i, r in roles.items() if r == "control"), key=id_order)

    def why_not(iid: str) -> str | None:
        item = per[iid]
        if item["selected"]:
            return None
        if not item["eligible"]:
            return item["not_eligible_because"]
        return f"eligible, but {selected} ranks first under the selection rule"

    def block(iid: str | None) -> dict[str, Any] | None:
        if iid is None or iid not in per:
            return None
        item = per[iid]
        return {
            "intervention_id": iid,
            "greens_s": greens[iid],
            "validator": item["validator"],
            "simulation_status": item["simulation_status"],
            "selected": item["selected"],
            "eligible": item["eligible"],
            "why_not_selected": why_not(iid),
            "holm_confirmed_improvements": item["holm_confirmed_improvements"],
            "worsens_uncorrected": item["worsens_uncorrected"],
            "uninformative": item["uninformative"],
        }

    with_improvement = [
        {"intervention_id": i, "metrics": per[i]["holm_confirmed_improvements"]}
        for i in perturbations
        if per[i]["holm_confirmed_improvements"]
    ]
    worsening = [{"intervention_id": i, "metrics": per[i]["worsens_uncorrected"]}
                 for i in perturbations if per[i]["worsens_uncorrected"]]  # fmt: skip
    contradicting = [
        {"intervention_id": i, "role": roles[i], **item}
        for i in per
        for item in per[i]["confirmed_contradicting_prediction"]
    ]
    predicted = sum(n for key, n in agreement.items() if key not in ("no_prediction",
                                                                      "uninformative"))  # fmt: skip
    decided_agree = sum(
        1
        for c in evaluation.comparisons
        if (c.verdict, predicted_direction(predictions.get(c.candidate), c.metric_name))
        in {("improves", "better"), ("worsens", "worse")}
    )
    rate = round(agreement["agree"] / predicted, 4) if predicted else None
    sign_rate = (
        round(decided_agree / (decided_agree + len(opposite)), 4)
        if decided_agree + len(opposite)
        else None
    )
    bound = round(3 / opportunities, 4) if not contradicting and opportunities else None
    censoring = baseline_censoring(evaluation)

    null_block = block(role_ids.get("null"))
    if null_block is not None:
        null_block["as_expected"] = (
            not null_block["selected"] and not (null_block["holm_confirmed_improvements"])
        )
    mirror_block = block(role_ids.get("mirror"))
    if mirror_block is not None:
        mirror_block["as_expected"] = not mirror_block["selected"]
    correction = role_ids["correction"]
    c_item = per[correction]
    expected = prereg["pre_registered_predictions"]
    selected_role = roles.get(selected) if selected else None

    def describe(item: dict[str, Any] | None, name: str) -> str:
        if item is None:
            return f"no {name}"
        if item["validator"] == "rejected":
            how = "rejected by the validator before simulation"
        else:
            how = f"simulated ({item['simulation_status'].replace('_', ' ')})"
        return f"the {name} {item['intervention_id']} was {how} and " + (
            "SELECTED" if item["selected"] else "not selected"
        )

    conclusion = (
        f"[{prereg.get('scenario', '?')}] Known-answer test of the evaluation and selection stage "
        "(no proposer runs; Muse is not exercised). Positive control: the pre-registered "
        f"correction {correction} (greens {greens[correction]} s) was "
        + ("SELECTED" if c_item["selected"] else f"NOT selected ({why_not(correction)})")
        + "; its Holm-confirmed improvements: "
        + (", ".join(c_item["holm_confirmed_improvements"]) or "none")
        + ". Negative controls: "
        + describe(null_block, "exact null")
        + "; "
        + describe(mirror_block, "mirror")
        + f". Of {len(perturbations)} small perturbations of the bad plan (not nulls), "
        f"{len(with_improvement)} had a Holm-confirmed improvement and {len(worsening)} worsened "
        f"at least one metric. False positives (a confirmed improvement contradicting the "
        f"pre-registered direction of an uncalibrated Webster model, which agreed with "
        f"{agreement['agree']} of {predicted} predicted comparisons"
        + (f", {rate:.0%}" if rate is not None else "")
        + f", and was opposite in {len(opposite)}): {len(contradicting)} of {opportunities} "
        "confirmable claims predicted worse"
        + (f" (rule-of-three 95% upper bound {bound:.2f})" if bound is not None else "")
        + f". {censoring['text']} Selected: {selected or 'none'}"
        + (f" ({selected_role})" if selected_role else "")
        + ". "
        + SYNTHETIC_LABEL
    )
    return {
        "kind": "simulated results read from the decision report and evaluation; predictions "
        "are assumed (analytical, uncalibrated)",
        "label": SYNTHETIC_LABEL,
        "what_is_tested": prereg.get("what_is_tested"),
        "what_is_not_tested": prereg.get("what_is_not_tested"),
        "scenario": prereg.get("scenario"),
        "rules_version": evaluation.rules_version,
        "selected_intervention_id": selected,
        "selected_role": selected_role,
        "selection_reason": selection_reason,
        "known_correction": {
            **(block(correction) or {}),
            "role": "positive control",
            "expected_pareto_improving": expected.get("correction_expected_pareto_improving"),
            "expected_eligible": expected.get("correction_expected_eligible"),
        },
        "halfway_plan": block(role_ids.get("halfway")),
        "null": None
        if null_block is None
        else {**null_block, "role": "negative control", "expected": expected.get("null_expected")},
        "mirror": None
        if mirror_block is None
        else {
            **mirror_block,
            "role": "negative control",
            "expected": expected.get("mirror_expected"),
        },
        "random_perturbations": {
            "what_they_are": ROLE_TEXT["control"],
            "count": len(perturbations),
            "with_holm_confirmed_improvement": {
                "count": len(with_improvement),
                "candidates": with_improvement,
                "note": "not a false-positive count: these are perturbations of a deliberately "
                "bad plan, which may genuinely improve",
            },
            "worsening_any_metric": {"count": len(worsening), "candidates": worsening},
            "not_compared": {
                i: per[i]["simulation_status"]
                for i in perturbations
                if per[i]["simulation_status"] != "compared"
            },  # fmt: skip
            "eligible": [i for i in perturbations if per[i]["eligible"]],
        },
        "false_positives": {
            "definition": FALSE_POSITIVE,
            "count": len(contradicting),
            "among_perturbations": sum(1 for x in contradicting if x["role"] == "control"),
            "beyond_dead_band": sum(
                1 for x in contradicting if (x["predicted_change"] or 0) >= DEAD_BAND_S
            ),
            "items": contradicting,
            "opportunities": opportunities,
            "opportunities_definition": "tested improvement claims (p-value computed) whose "
            "predicted change is > 0: the claims that would be false positives if confirmed",
            "rule_of_three_upper_bound_95": bound,
            "rule_of_three_note": "3/n when 0 of n; it treats the claims as independent, which "
            "they are not (they share the baseline runs)",
        },
        "model_agreement": {
            "note": "verdicts against an uncalibrated Webster model (assumed saturation flow): "
            "agree = improves/better, worsens/worse or inconclusive/about unchanged",
            "counts": dict(sorted(agreement.items())),
            "predicted_comparisons": predicted,
            "agreement_rate": rate,
            "sign_agreement_when_both_decided": sign_rate,
            "opposite_cases": opposite,
            "confirmed_where_about_unchanged_predicted": {
                i: per[i]["confirmed_where_about_unchanged_predicted"]
                for i in per
                if per[i]["confirmed_where_about_unchanged_predicted"]
            },
        },
        "censoring": censoring,
        "missed_improvements": {
            "definition": "improvement metrics predicted better (beyond the "
            f"{DEAD_BAND_S:g} s dead-band) that Holm did not confirm, untestable ones included",
            "by_candidate": {
                i: per[i]["missed_predicted_improvements"]
                for i in per
                if per[i]["missed_predicted_improvements"]
            },  # fmt: skip
            "uncorrected_improves_not_holm_confirmed": {
                i: per[i]["improves_uncorrected_not_holm_confirmed"]
                for i in per
                if per[i]["improves_uncorrected_not_holm_confirmed"]
            },
        },
        "pre_registered_vs_observed": {
            "expected_eligible": expected.get("candidates_expected_eligible", []),
            "eligible": [i for i in per if per[i]["eligible"]],
            "selected_was_expected_eligible": None
            if selected is None
            else selected in expected.get("candidates_expected_eligible", []),
            "null_as_expected": None if null_block is None else null_block["as_expected"],
            "mirror_as_expected": None if mirror_block is None else mirror_block["as_expected"],
        },
        "per_candidate": per,
        "conclusion": conclusion,
    }


# comparison_rows (the rows a known-answer record keeps) lives in report.py, which re-derives them
# when it traces a committed record again; it is imported above under the same name.


# --- The whole run ------------------------------------------------------------------------------


def run_known_answer(
    run_dir: Path,
    *,
    design: KnownAnswerDesign | None = None,
    settings: RunSettings = DEFAULT_SETTINGS,
    controls: int = 8,
    control_seed: int = DEFAULT_CONTROL_SEED,
    include_halfway: bool = True,
    verify_reruns: bool = True,
    now: datetime | None = None,
    record_path: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Pre-register, then run every candidate through the normal pipeline; returns the summary.

    Writes `run_dir` (new) and, with `record_path` (new), the same summary there. Nothing is
    overwritten: an existing run directory or record file is refused before anything runs.
    """
    design = design or SCENARIOS["reallocation"]
    now = now or datetime.now(UTC).replace(microsecond=0)
    if run_dir.exists():
        raise FileExistsError(f"{run_dir} exists; a run is never overwritten")
    if record_path is not None and record_path.exists():
        raise FileExistsError(f"{record_path} exists; a record is never overwritten")
    started = time.perf_counter()
    git_at_start = git_state()
    code_at_start = code_hashes()
    run_dir.mkdir(parents=True)
    written_at = datetime.now(UTC)
    prereg = preregister(
        design, settings, controls=controls, control_seed=control_seed,
        include_halfway=include_halfway, written_at=written_at,
    )  # fmt: skip
    prereg_sha = write_new(run_dir / PREREGISTRATION_FILE, json.dumps(prereg, indent=2) + "\n")
    # ---- nothing above this line reads or runs a simulator ----
    artifacts: dict[str, str] = {PREREGISTRATION_FILE: prereg_sha}
    geometry = synthetic_geometry(design)
    signals = two_phase_timeline(design, design.bad_greens_s)
    world = synthetic_world_model(design, geometry, now)
    for name, model in (("geometry.json", geometry), ("signals.json", signals),
                        ("world_model.json", world)):  # fmt: skip
        artifacts[name] = write_new(run_dir / name, model.model_dump_json(indent=2) + "\n")
    root = repo_root or run_dir.parent
    summary: dict[str, Any] = {
        "label": SYNTHETIC_LABEL,
        "experiment": NAME,
        "scenario": design.scenario,
        "what_is_tested": prereg["what_is_tested"],
        "what_is_not_tested": prereg["what_is_not_tested"],
        "purpose": prereg["purpose"],
        "design_history": prereg["design_history"],
        "script": SCRIPT,
        "module": "cairoflow_evaluation.known_answer",
        "rules_version": RULES_VERSION,
        "created_at_utc": now.isoformat(),
        "run_dir": _relative(run_dir, root),
        "kinds": {
            "assumed": "designed in code or computed analytically from the design (demand, plans, "
            "expectation, predictions, settings)",
            "simulated": "computed from SUMO output files by the evaluation package",
        },
        "provenance": {
            "git_at_start": git_at_start,
            "code_sha256_at_start": code_at_start,
            "note": "git commit and `git status --porcelain` of the files the run imports (and "
            "its script), at the start and at the end; and the SHA-256 of their source files",
        },
        "preregistration": {
            "file": PREREGISTRATION_FILE,
            "file_sha256": prereg_sha,
            "content_sha256": preregistration_content_sha256(prereg),
            "content_sha256_note": "without written_at_utc: equal for runs of the same design, "
            "settings, seed and software",
            "written_at_utc": written_at.isoformat(),
            "content": prereg,
        },
    }
    summary["preregistration"]["file_sha256_before_simulation"] = _sha256_file(
        run_dir / PREREGISTRATION_FILE
    )
    summary["preregistration"]["simulation_started_at_utc"] = datetime.now(UTC).isoformat()
    baseline = run_baseline(
        world, geometry, signals, run_dir / "baseline", settings,
        CompileOptions(arm_length_m=design.arm_length_m), verify_reruns=verify_reruns, now=now,
    )  # fmt: skip
    artifacts["baseline/baseline_manifest.json"] = _sha256_file(
        run_dir / "baseline/baseline_manifest.json"
    )
    summary["baseline"] = {
        "kind": "simulated (validity checks only; program and demand are assumed)",
        "experiment_id": baseline.experiment_id,
        "manifest_sha256": artifacts["baseline/baseline_manifest.json"],
        "valid": baseline.valid,
        "reasons": list(baseline.reasons),
        "reruns_identical": baseline.reruns_identical,
        "program_s": {p.name: p.duration_s for p in baseline.network.signal_program.phases},
        "vehicles_per_seed": {f.seed: f.vehicles for f in baseline.demand.files},
        "runs": [
            {
                "seed": r.seed,
                "loaded": r.loaded,
                "arrived": r.arrived,
                "teleports": r.teleports,
                "collisions": r.collisions,
                "valid": r.valid,
            }
            for r in baseline.runs
        ],  # fmt: skip
    }
    limitations = [
        SYNTHETIC_LABEL,
        *DESIGNED_DEMAND_NOTES,
        "The analytical Webster model is not calibrated against SUMO (saturation flow assumed); "
        "it only labels expected directions.",
        "The small random perturbations change a deliberately bad plan by up to 5 s per green; "
        "they are not nulls. The exact null is rejected by the validator before simulation, so "
        "no simulated null exists.",
    ]
    if design.scenario == "long-cycle":
        limitations.append("This scenario was designed after seeing the reallocation result.")
    summary["limitations"] = limitations
    if not baseline.valid:
        return _finish(summary, run_dir, record_path, started, prereg_sha, error="baseline invalid")

    _, network = load_baseline(run_dir / "baseline")
    phase_of = phases_by_axis(network, design)
    bundle = build_evidence_bundle(world, network.manifest, BUNDLE_LIMITATIONS)
    artifacts["bundle.json"] = write_new(
        run_dir / "bundle.json", bundle.model_dump_json(indent=2) + "\n"
    )
    outcomes = validate_proposals(
        proposals(prereg, phase_of, network.manifest.signal_program.program_id),
        network, bundle, now=now,
    )  # fmt: skip
    artifacts["outcomes.json"] = write_new(
        run_dir / "outcomes.json",
        TypeAdapter(tuple[ValidationOutcome, ...]).dump_json(outcomes, indent=2).decode() + "\n",
    )
    roles = {c["intervention_id"]: c["role"] for c in prereg["candidates"]}
    names = [p.name for p in network.manifest.signal_program.phases]
    rejected = {o.intervention_id: o.reasons for o in outcomes if o.status != "accepted"}
    summary["validation"] = {
        "kind": "assumed inputs, checked by the deterministic validator",
        "outcomes": [
            {
                "intervention_id": o.intervention_id,
                "role": roles[o.intervention_id],
                "status": o.status,
                "reasons": list(o.reasons),
                "phase_durations_s": None
                if o.phase_durations_s is None
                else dict(zip(names, o.phase_durations_s, strict=True)),
                "cycle_s": o.cycle_s,
            }
            for o in outcomes
        ],
    }
    try:
        experiment = run_candidates(run_dir / "baseline", outcomes, run_dir / "experiment", now=now)
    except CandidateError as error:
        return _finish(summary, run_dir, record_path, started, prereg_sha, error=str(error))
    artifacts["experiment/experiment_manifest.json"] = _sha256_file(
        run_dir / "experiment/experiment_manifest.json"
    )
    evaluation = evaluate_experiment(run_dir / "experiment")
    artifacts["experiment/evaluation.json"] = _sha256_file(run_dir / "experiment/evaluation.json")
    censoring = baseline_censoring(evaluation)
    if censoring["queue_claims_untestable_for_every_candidate"]:
        limitations.append(censoring["text"])
    extra_limitations = (
        f"Scenario {design.scenario}. {prereg['purpose']} {prereg['design_history']}",
        *limitations,
    )
    report = assemble_report(
        world_model=world,
        bundle=bundle,
        reasoning=None,
        outcomes=outcomes,
        experiment=experiment,
        evaluation=evaluation,
        artifacts=dict(sorted(artifacts.items())),
        now=now,
        extra_limitations=extra_limitations,
    )
    problems = trace_report(
        report,
        world_model=world,
        outcomes=outcomes,
        experiment=experiment,
        evaluation=evaluation,
        experiment_dir=run_dir / "experiment",
        bundle=bundle,
        extra_limitations=extra_limitations,
    )
    json_path, md_path = write_report(report, run_dir)
    summary["experiment"] = {
        "kind": "simulated",
        "experiment_id": experiment.experiment_id,
        "manifest_sha256": artifacts["experiment/experiment_manifest.json"],
        "valid": experiment.valid,
        "reasons": list(experiment.reasons),
        "skipped": list(experiment.skipped),
        "seeds": list(experiment.settings.seeds),
        "sumo_runs": len(baseline.runs)
        + len(baseline.reruns)
        + sum(len(c.runs) for c in experiment.candidates),
    }
    summary["eligibility"] = {
        "kind": "simulated (classes from SUMO's own records)",
        "candidates": [e.model_dump(mode="json") for e in evaluation.eligibility],
        "baseline_censored_lanes": evaluation.baseline_censored_lanes,
        "baseline_entry_blocked": getattr(evaluation, "baseline_entry_blocked", {}),
    }
    summary["comparisons"] = {
        "kind": "simulated (candidate - baseline, paired by seed; t interval)",
        "rows": comparison_rows(evaluation),
    }
    summary["holm"] = {
        "kind": "simulated",
        "family_alpha": FAMILY_ALPHA,
        "planned_tests": len(evaluation.improvement_tests),
        "tests": [t.model_dump(mode="json") for t in evaluation.improvement_tests],
    }
    summary["report"] = {
        "selected_intervention_id": report.selected_intervention_id,
        "selected_role": roles.get(report.selected_intervention_id or ""),
        "selection_reason": report.selection_reason,
        "selection_recomputed_matches": (report.selected_intervention_id, report.selection_reason)
        == select_candidate(evaluation),
        "recommended_intervention_id": report.recommended_intervention_id,
        "recommendation": report.recommendation,
        "trace_problems": list(problems),
        "report_json_sha256": _sha256_file(json_path),
        "report_md_sha256": _sha256_file(md_path),
    }
    summary["outcome"] = summarize_outcome(
        prereg, evaluation, report.selected_intervention_id, report.selection_reason, rejected
    )
    return _finish(summary, run_dir, record_path, started, prereg_sha)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _finish(
    summary: dict[str, Any],
    run_dir: Path,
    record_path: Path | None,
    started: float,
    prereg_sha: str,
    error: str | None = None,
) -> dict[str, Any]:
    after = _sha256_file(run_dir / PREREGISTRATION_FILE)
    prereg = summary["preregistration"]
    prereg["file_sha256_after_run"] = after
    prereg["unchanged_after_run"] = after == prereg_sha
    prereg["evidence_written_before_simulation"] = {
        "file_hash_before_first_sumo_run_matches": prereg["file_sha256_before_simulation"]
        == prereg_sha,
        "written_not_after_simulation_start_by_clock": datetime.fromisoformat(
            prereg["written_at_utc"]
        )
        <= datetime.fromisoformat(prereg["simulation_started_at_utc"]),
    }
    provenance = summary["provenance"]
    provenance["git_at_end"] = git_state()
    provenance["code_unchanged_during_run"] = code_hashes() == provenance["code_sha256_at_start"]
    provenance["clean_against_commit_at_start_and_end"] = bool(
        provenance["git_at_start"].get("clean")
        and provenance["git_at_end"].get("clean")
        and provenance["git_at_start"].get("commit") == provenance["git_at_end"].get("commit")
    )
    if error is not None:
        summary["error"] = error
    summary["runtime"] = {"wall_clock_s": round(time.perf_counter() - started, 1)}
    text = json.dumps(summary, indent=2) + "\n"
    write_new(run_dir / SUMMARY_FILE, text)
    if record_path is not None:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        write_new(record_path, text)
    return json.loads(text)  # exactly what was written (tuples as lists, keys as strings)
