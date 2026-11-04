"""Pair identity: what two runs must share to be compared seed by seed (schema version 0.1.0).

A paired comparison (compare.paired_comparison) subtracts one arm's value from another's per seed.
That only means something if the two runs differ in the signal timings and nothing else. A
`RunIdentity` names everything about one run that must NOT differ; a `PairIdentity` is two of them
that the rules below accept. Nothing here reads a file: the identities are built from records
(cairoflow_evaluation.pairing) and checked here.

Scope (ADR-033). A pair lives inside one timeline member: the baseline experiment it was measured
against (`member_sha256`, the SHA-256 of its baseline_manifest.json) and the signal timeline that
experiment replays (`timeline_id`). A candidate of one member is never paired with a run of
another, even if the two happen to share a vehicle population (T2 and T3 do).

Must match: member, timeline, seed, demand file (its bytes, speed factors included), vehicle
population, world model, geometry, the network apart from its signal program (`network_sha256`),
the signal program's phase structure (names and SUMO states, `program_structure_sha256`), how SUMO
was run (`run_config_sha256`: the recorded command without its file paths) and the run settings
except the seeds. Must differ: the arm (a run is not paired with itself or its rerun). Free to
differ, because it is the treatment: the phase durations (`plan_sha256`) and the intervention.
`population_sha256` is None when the route file could not be read; the pair is then not
`population_verified`, and the demand file's SHA-256 still has to match, which implies the same
population.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from cairoflow_contracts.evidence import EvidenceModel, _check_sha256
from cairoflow_contracts.experiment import RunSettings, SignalProgramSpec
from cairoflow_contracts.integrity import canonical_sha256

PAIRING_VERSION = "0.1.0"
POPULATION_VERSION = "population-v1"  # the definition is cairoflow_simulation.population's

# (field, what it is) in the order problems are reported
_MUST_MATCH = (
    ("member_sha256", "timeline member"),
    ("timeline_id", "signal timeline"),
    ("seed", "seed"),
    ("demand_sha256", "demand file"),
    ("world_model_sha256", "world model"),
    ("geometry_sha256", "geometry"),
    ("network_sha256", "network apart from its signal program"),
    ("program_structure_sha256", "signal program structure"),
    ("run_config_sha256", "SUMO run configuration"),
    ("settings_sha256", "run settings"),
)


class PairingError(ValueError):
    """Two runs, or an experiment's records, do not form valid pairs."""


def settings_pairing_sha256(settings: RunSettings) -> str:
    """Hash of what paired runs share in their settings: every field but the seeds, because a
    pair is made of one seed's runs and different experiments may plan different seeds."""
    return canonical_sha256(settings.model_dump(mode="json", exclude={"seeds"}))


def plan_sha256(program: SignalProgramSpec) -> str:
    """Hash of a signal program as it was run: each phase's name, SUMO state and duration. The
    program id, the cycle's start and the timeline are not part of it, so equal timings hash
    equal whatever they are called."""
    return canonical_sha256([[p.name, p.state, p.duration_s] for p in program.phases])


def program_structure_sha256(program: SignalProgramSpec) -> str:
    """Hash of what a signal program may not change between arms: its phases' names and SUMO
    states, in order. Only the durations are the treatment (candidates.py enforces the same)."""
    return canonical_sha256([[p.name, p.state] for p in program.phases])


class RunIdentity(EvidenceModel):
    """One run of one arm on one seed, as far as pairing is concerned."""

    member_sha256: str = Field(description="SHA-256 of the member's baseline_manifest.json")
    timeline_id: str = Field(min_length=1, description="the signal timeline the member replays")
    arm: str = Field(min_length=1, description="'baseline' or an intervention id")
    seed: int = Field(ge=0)
    demand_sha256: str = Field(description="SHA-256 of the route file's bytes (RunRecord)")
    population_version: Literal["population-v1"] = POPULATION_VERSION
    population_sha256: str | None = Field(
        description="the route file's vehicles without speedFactor; None when it was not read"
    )
    world_model_sha256: str
    geometry_sha256: str
    network_sha256: str = Field(
        description="the compiled network but for its signal program: plain XML hashes, link "
        "count, simulator version and parameters other than the cycle"
    )
    program_structure_sha256: str = Field(description="phase names and states, not durations")
    run_config_sha256: str = Field(
        description="the SUMO command as recorded, without the values of --net-file and "
        "--route-files, which name directories"
    )
    settings_sha256: str = Field(description="settings_pairing_sha256: all but the seeds")
    plan_sha256: str = Field(description="plan_sha256 of the signal program the run executed")
    intervention_sha256: str | None = Field(
        None, description="the proposal's hash (ValidationOutcome), when the arm has one"
    )

    @field_validator(
        "member_sha256",
        "demand_sha256",
        "population_sha256",
        "world_model_sha256",
        "geometry_sha256",
        "network_sha256",
        "program_structure_sha256",
        "run_config_sha256",
        "settings_sha256",
        "plan_sha256",
        "intervention_sha256",
    )
    @classmethod
    def _hash(cls, value: str | None) -> str | None:
        return None if value is None else _check_sha256(value)


def _short(value: object) -> str:
    text = str(value)
    return text[:12] if len(text) == 64 else text


def pairing_problems(reference: RunIdentity, compared: RunIdentity) -> tuple[str, ...]:
    """Every reason two runs are not a valid pair; empty when they are."""
    problems = [
        f"{label} differs: {_short(getattr(reference, field))} vs "
        f"{_short(getattr(compared, field))}"
        for field, label in _MUST_MATCH
        if getattr(reference, field) != getattr(compared, field)
    ]
    first, second = reference.population_sha256, compared.population_sha256
    if first is not None and second is not None and first != second:
        problems.append(f"vehicle population differs: {_short(first)} vs {_short(second)}")
    elif (
        reference.demand_sha256 != compared.demand_sha256 and first is not None and first == second
    ):
        problems.append("same vehicles but another speedFactor draw: not this seed's demand")
    if reference.arm == compared.arm:
        problems.append(f"both runs are arm {reference.arm!r}: not a pair of different arms")
    return tuple(problems)


class PairIdentity(EvidenceModel):
    """Two runs that may be compared: `pair_sha256` names the pair in a ledger or a report."""

    pairing_version: Literal["0.1.0"] = PAIRING_VERSION
    reference: RunIdentity
    compared: RunIdentity
    pair_sha256: str

    @staticmethod
    def digest_of(reference: RunIdentity, compared: RunIdentity) -> str:
        return canonical_sha256(
            {
                "reference": reference.model_dump(mode="json"),
                "compared": compared.model_dump(mode="json"),
            }
        )

    @classmethod
    def of(cls, reference: RunIdentity, compared: RunIdentity) -> PairIdentity:
        """The pair, or a PairingError naming every rule the two runs break."""
        problems = pairing_problems(reference, compared)
        if problems:
            raise PairingError("; ".join(problems))
        return cls(
            reference=reference,
            compared=compared,
            pair_sha256=cls.digest_of(reference, compared),
        )

    @property
    def population_verified(self) -> bool:
        return self.reference.population_sha256 is not None and (
            self.compared.population_sha256 is not None
        )

    @field_validator("pair_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)

    @model_validator(mode="after")
    def _consistent(self) -> PairIdentity:
        problems = pairing_problems(self.reference, self.compared)
        if problems:
            raise ValueError("not a valid pair: " + "; ".join(problems))
        if self.pair_sha256 != self.digest_of(self.reference, self.compared):
            raise ValueError("pair_sha256 does not hash the pair's content")
        return self
