"""EQ-1 (= D1): the equal-saturation allocation of the cut cycle's greens (ADR-033 decision 3).

A deterministic comparator, not a model output. Inputs, all committed: the cut program (17
phases, cycle 99 s, kept), the whole-clip movement flows of scenario.json (their intervals are
ignored) and the approach lanes of geometry.json.

1. Critical flow per head, veh/h per lane: N and S through flow per through lane; E and W the
   larger of through flow per through lane and left flow per left lane; NL and SL (the protected
   left heads) left flow per left lane. Right turns are no head. Lanes are counted from each
   approach lane's `allowed_turns`.
2. A head's green is the sum of the durations of the phases in which it shows green, fixed
   phases included.
3. ASSUMPTION (e), experimental: every head has the same saturation flow and there is no lost
   time, so a head's load is y / g. This is not a measured property of the junction.
4. The free phases (p0, p5, p8, p11, p14, p16; p13 stays, its change cannot be mapped onto the
   replays) take integer seconds, each at least the minimum green, summing to what they sum to
   now, so the cycle is kept. Every such vector is enumerated.
5. Order: leximin of the load vector sorted from the highest load down, then the smaller L1
   change from the baseline, then the lexicographically smaller vector of the adjustable phases
   in program order. Loads are compared exactly: y is scaled to integers and divided by green
   through lcm(1..cycle), so no float is involved.
6. The answer is the first vector in that order for which `validation.timing_problems` finds
   nothing (head-run bounds); its rank says how many came before it and failed.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import cached_property
from typing import Any

from cairoflow_contracts.evidence import IntersectionGeometry
from cairoflow_contracts.experiment import SignalProgramSpec
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.pairing import plan_sha256
from cairoflow_contracts.reasoning import MIN_GREEN_S
from cairoflow_simulation.validation import timing_problems

EQ1_VERSION = "eq1-v1"
HEADS = ("N", "S", "E", "W", "NL", "SL")
FREE_PHASES = ("p0", "p5", "p8", "p11", "p14", "p16")
# Versions differ only in the free phases (ADR-033 decision 3b). eq1-v2 (approved 2026-11-03) also
# fixes p16: on members T2 and T4 every replayed p16 is shorter than the minimum green, so a
# change to it applies to no replayed phase there (found by the eq1-v1 held-out evaluation).
VERSIONS: dict[str, tuple[str, ...]] = {
    "eq1-v1": FREE_PHASES,
    "eq1-v2": ("p0", "p5", "p8", "p11", "p14"),
}
ASSUMPTION = (
    "equal saturation flow for every signal head and no lost time, so a head's load is its "
    "critical flow per lane divided by its green (ADR-033 decision 3e)"
)
ASSUMPTION_STATUS = (
    "experimental assumption, accepted by the account holder on 2026-11-03 as an explicit "
    "experimental assumption, not an empirically established traffic characteristic; "
    "no saturation flow or lost time was calibrated"
)
ORDERING = (
    "leximin of the descending load vector, then smaller L1 change from the baseline, then the "
    "lexicographically smaller vector of the adjustable phases in program order; the first "
    "vector without timing_problems"
)
Movement = tuple[str, str]  # (approach arm, turn)


class Eq1Error(ValueError):
    """The inputs do not define an EQ-1 allocation."""


def _tag(name: str) -> str:
    return name.split("_", 1)[0]


@dataclass(frozen=True)
class Eq1Inputs:
    program: SignalProgramSpec
    flows: Mapping[Movement, Fraction]  # veh/h, whole clip
    lanes_through: Mapping[str, int]
    lanes_left: Mapping[str, int]
    free_phases: tuple[str, ...] = FREE_PHASES
    min_green_s: float = MIN_GREEN_S

    def with_flows(self, flows: Mapping[Movement, Fraction]) -> Eq1Inputs:
        return replace(self, flows=dict(flows))

    @property
    def version(self) -> str:
        """The named version whose free phases these are, else `eq1-custom` (tests, studies)."""
        return next((v for v, free in VERSIONS.items() if free == self.free_phases), "eq1-custom")

    @cached_property
    def base(self) -> tuple[int, ...]:
        durations = []
        for phase in self.program.phases:
            if phase.duration_s != int(phase.duration_s):
                raise Eq1Error(f"phase {phase.name} lasts {phase.duration_s} s: not whole seconds")
            durations.append(int(phase.duration_s))
        return tuple(durations)

    @cached_property
    def free_indices(self) -> tuple[int, ...]:
        found = []
        for tag in self.free_phases:
            matches = [i for i, p in enumerate(self.program.phases) if _tag(p.name) == tag]
            if len(matches) != 1:
                raise Eq1Error(f"no phase {tag} (or more than one) in {self.program.program_id}")
            if not self.program.phases[matches[0]].adjustable:
                raise Eq1Error(f"phase {self.program.phases[matches[0]].name} is not adjustable")
            found.append(matches[0])
        if len(set(found)) != len(found):
            raise Eq1Error("a free phase is named twice")
        return tuple(found)

    @cached_property
    def reported_indices(self) -> tuple[int, ...]:
        return tuple(i for i, p in enumerate(self.program.phases) if p.adjustable)

    @cached_property
    def head_phases(self) -> dict[str, tuple[int, ...]]:
        heads: dict[str, list[int]] = {h: [] for h in HEADS}
        for i, phase in enumerate(self.program.phases):
            for arm, state in phase.arm_states.items():
                if state == "green" and arm in heads:
                    heads[arm].append(i)
            for arm, state in phase.left_states.items():
                if state == "green" and f"{arm}L" in heads:
                    heads[f"{arm}L"].append(i)
        for head, phases in heads.items():
            if not phases:
                raise Eq1Error(f"head {head} is never green in {self.program.program_id}")
        return {h: tuple(p) for h, p in heads.items()}

    def _flow(self, arm: str, turn: str) -> Fraction:
        if (arm, turn) not in self.flows:
            raise Eq1Error(f"no flow for movement {arm} {turn}")
        flow = Fraction(self.flows[arm, turn])
        if flow < 0:
            raise Eq1Error(f"movement {arm} {turn} has a negative flow ({flow})")
        return flow

    def _per_lane(self, arm: str, turn: str) -> Fraction:
        lanes = (self.lanes_through if turn == "through" else self.lanes_left).get(arm, 0)
        if lanes < 1:
            raise Eq1Error(f"arm {arm} has no {turn} lane")
        return self._flow(arm, turn) / lanes

    @cached_property
    def critical_flow(self) -> dict[str, Fraction]:
        """y per head (veh/h/lane), step 1 of the module."""
        return {
            "N": self._per_lane("N", "through"),
            "S": self._per_lane("S", "through"),
            "E": max(self._per_lane("E", "through"), self._per_lane("E", "left")),
            "W": max(self._per_lane("W", "through"), self._per_lane("W", "left")),
            "NL": self._per_lane("N", "left"),
            "SL": self._per_lane("S", "left"),
        }

    def as_json(self) -> dict[str, Any]:
        names = [p.name for p in self.program.phases]
        flows = {f"{a} {t}": str(Fraction(v)) for (a, t), v in sorted(self.flows.items())}
        return {
            "program_id": self.program.program_id,
            "timeline_id": self.program.timeline_id,
            "phase_names": names,
            "baseline_durations_s": list(self.base),
            "cycle_s": sum(self.base),
            "free_phases": [names[i] for i in self.free_indices],
            "reported_phases": [names[i] for i in self.reported_indices],
            "head_green_phases": {h: [names[i] for i in p] for h, p in self.head_phases.items()},
            "flows_veh_per_h": flows,
            "lanes_through": dict(sorted(self.lanes_through.items())),
            "lanes_left": dict(sorted(self.lanes_left.items())),
            "critical_flow_veh_per_h_per_lane": {h: str(v) for h, v in self.critical_flow.items()},
            "min_green_s": self.min_green_s,
            "ordering": ORDERING,
        }


def inputs_from_records(
    program: SignalProgramSpec,
    scenario: Mapping[str, Any],
    geometry: IntersectionGeometry,
    version: str = EQ1_VERSION,
) -> Eq1Inputs:
    """The committed inputs: scenario.json's movement flows, geometry.json's approach lanes, and
    the free phases of `version`."""
    if version not in VERSIONS:
        raise Eq1Error(f"unknown EQ-1 version {version!r}: one of {', '.join(VERSIONS)}")
    flows = {
        (m["approach"], m["turn"]): Fraction(str(m["flow_veh_per_h"]))
        for m in scenario["movements"]
    }
    through: dict[str, int] = {}
    left: dict[str, int] = {}
    for lane in geometry.lanes:
        if lane.direction == "approach":
            through[lane.arm] = through.get(lane.arm, 0) + ("through" in lane.allowed_turns)
            left[lane.arm] = left.get(lane.arm, 0) + ("left" in lane.allowed_turns)
    return Eq1Inputs(
        program=program, flows=flows, lanes_through=through, lanes_left=left,
        free_phases=VERSIONS[version],
    )  # fmt: skip


def input_sha256(inputs: Eq1Inputs) -> str:
    return canonical_sha256(inputs.as_json())


@dataclass(frozen=True)
class _Scaled:
    """Integer forms of the loads: y[h] * (m // green) orders exactly as y / green."""

    y: tuple[int, ...]
    m: int
    fixed: tuple[int, ...]  # green from phases that are not free, per head
    parts: tuple[tuple[int, ...], ...]  # per head, the free phases (program indices) in it


def _scaled(inputs: Eq1Inputs) -> _Scaled:
    y = inputs.critical_flow
    scale = math.lcm(*(v.denominator for v in y.values()))
    free = inputs.free_indices
    heads = inputs.head_phases
    return _Scaled(
        y=tuple(int(y[h] * scale) for h in HEADS),
        m=math.lcm(*range(1, sum(inputs.base) + 1)),
        fixed=tuple(sum(inputs.base[i] for i in heads[h] if i not in free) for h in HEADS),
        parts=tuple(tuple(i for i in free if i in heads[h]) for h in HEADS),
    )


def _vectors(count: int, pool: int, low: int) -> Iterator[tuple[int, ...]]:
    """Every tuple of `count` integers >= low summing to `pool`, in lexicographic order."""
    if count == 1:
        if pool >= low:
            yield (pool,)
        return
    for first in range(low, pool - low * (count - 1) + 1):
        for rest in _vectors(count - 1, pool - first, low):
            yield (first, *rest)


def _durations(inputs: Eq1Inputs, vector: tuple[int, ...]) -> tuple[int, ...]:
    durations = list(inputs.base)
    for i, value in zip(inputs.free_indices, vector, strict=True):
        durations[i] = value
    return tuple(durations)


def _key(inputs: Eq1Inputs, scaled: _Scaled, durations: tuple[int, ...]) -> tuple:
    greens = [
        fixed + sum(durations[i] for i in parts)
        for fixed, parts in zip(scaled.fixed, scaled.parts, strict=True)
    ]
    loads = sorted(
        (y * (scaled.m // g) for y, g in zip(scaled.y, greens, strict=True)), reverse=True
    )
    l1 = sum(abs(durations[i] - inputs.base[i]) for i in inputs.free_indices)
    return (tuple(loads), l1, tuple(durations[i] for i in inputs.reported_indices))


def order_key(inputs: Eq1Inputs, durations: tuple[int, ...]) -> tuple:
    """The ordering of step 5 for a full duration vector (smaller is better)."""
    return _key(inputs, _scaled(inputs), tuple(durations))


@dataclass(frozen=True)
class Eq1Result:
    program_id: str
    durations: tuple[int, ...]
    reported_phases: tuple[str, ...]
    vector: tuple[int, ...]
    head_green_s: dict[str, int]
    loads: dict[str, Fraction]
    l1_change_s: int
    enumerated: int
    rank: int  # 1-based position of the answer in the ordering
    tied_on_loads: int  # vectors with the answer's load vector, the answer included
    plan_sha256: str

    @property
    def durations_s(self) -> list[float]:
        return [float(d) for d in self.durations]


def allocate(inputs: Eq1Inputs) -> Eq1Result:
    """The EQ-1 allocation (see the module); raises Eq1Error when there is none."""
    base, free = inputs.base, inputs.free_indices
    heads = inputs.head_phases
    scaled = _scaled(inputs)
    low = math.ceil(inputs.min_green_s)
    pool = sum(base[i] for i in free)
    if pool < low * len(free):
        raise Eq1Error(f"no allocation: {pool} s for {len(free)} free phases of at least {low} s")
    best_key: tuple | None = None
    best: tuple[int, ...] | None = None
    enumerated = 0
    for vector in _vectors(len(free), pool, low):
        enumerated += 1
        durations = _durations(inputs, vector)
        key = _key(inputs, scaled, durations)
        if best_key is not None and key >= best_key:
            continue
        if not timing_problems(inputs.program.phases, [float(d) for d in durations]):
            best_key, best = key, durations
    if best is None or best_key is None:
        raise Eq1Error("no allocation passes the head-run bounds (validation.timing_problems)")
    rank, tied = 1, 0
    for vector in _vectors(len(free), pool, low):
        key = _key(inputs, scaled, _durations(inputs, vector))
        rank += key < best_key
        tied += key[0] == best_key[0]
    green = {h: sum(best[i] for i in heads[h]) for h in HEADS}
    y = inputs.critical_flow
    program = inputs.program.model_copy(
        update={
            "phases": tuple(
                p.model_copy(update={"duration_s": float(d)})
                for p, d in zip(inputs.program.phases, best, strict=True)
            )
        }
    )
    return Eq1Result(
        program_id=inputs.program.program_id,
        durations=best,
        reported_phases=tuple(_tag(inputs.program.phases[i].name) for i in inputs.reported_indices),
        vector=tuple(best[i] for i in inputs.reported_indices),
        head_green_s=green,
        loads={h: y[h] / green[h] for h in HEADS},
        l1_change_s=best_key[1],
        enumerated=enumerated,
        rank=rank,
        tied_on_loads=tied,
        plan_sha256=plan_sha256(program),
    )


def record(inputs: Eq1Inputs, result: Eq1Result) -> dict[str, Any]:
    """Everything needed to reproduce and audit the allocation; deterministic (no clock)."""
    body: dict[str, Any] = {
        "algorithm": inputs.version,
        "source": "ADR-033 decision 3 (EQ-1 = D1)",
        "assumption": ASSUMPTION,
        "assumption_status": ASSUMPTION_STATUS,
        "inputs": inputs.as_json(),
        "input_sha256": input_sha256(inputs),
        "steps": {
            "enumerated": result.enumerated,
            "rank_of_selected": result.rank,
            "rejected_by_timing_before_selected": result.rank - 1,
            "tied_on_load_vector": result.tied_on_loads,
        },
        "result": {
            "reported_phases": list(result.reported_phases),
            "vector": list(result.vector),
            "durations_s": list(result.durations),
            "cycle_s": sum(result.durations),
            "head_green_s": result.head_green_s,
            "loads": {h: str(v) for h, v in result.loads.items()},
            "loads_rounded": {h: round(float(v), 6) for h, v in result.loads.items()},
            "max_load": round(float(max(result.loads.values())), 6),
            "l1_change_s": result.l1_change_s,
            "plan_sha256": result.plan_sha256,
        },
    }
    return body | {"record_sha256": canonical_sha256(body)}
