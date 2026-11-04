"""Pair identity as a derived view: what one experiment's pairs share, seed by seed (0.1.0).

`cairoflow_contracts.pairing` decides whether two runs form a pair. A `PairIdentityView` records,
for one experiment, what a reader needs to check those pairs without opening the run directories:
the timeline member and signal timeline every pair lives in, each seed's route file (its SHA-256,
its vehicle population and its speed draws, ADR-033 "population-v1"), each arm's plan, network
and SUMO binary, the horizon every run shares, and the unit of inference those facts imply.

It adds no field to RunRecord or to any other frozen record: it is a sidecar computed from them
(cairoflow_evaluation.pair_identity reads the files). Nothing here reads a file. Its validator
checks the view against itself: every pair side against its arm (plan, network, intervention)
and the view (member, timeline, world model, the seed's demand file and population); exactly one
pair per candidate and seed, with the baseline as reference; canonical order; and the hash. An
edited view that contradicts its own pairs is refused. The hash is a content hash, not a seal:
whether the view matches the run directories is checked by rebuilding it.

Seeds 301-320 are the reserve of ADR-033 decision 5 (unused until a documented trigger), which
the execution plan's SEEDS decision calls the reserved confirmation seeds: a view over any of
them is refused unless the caller passes confirmation=True, and the flag is part of the hash.

The SUMO version is kept twice, apart. `sumo_binary_version` is what every run's output header
says wrote it (the binary that ran); `sumo_wheel_version` is the eclipse-sumo package version the
manifests record (CompiledNetworkManifest.sumo_version), which names an install, not a run.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import Field, field_validator, model_validator

from cairoflow_contracts.evidence import EvidenceModel, _check_sha256
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.pairing import PairIdentity, PairingError

PAIR_VIEW_VERSION = "0.1.0"
CONFIRMATION_SEEDS = frozenset(range(301, 321))
POPULATION_EXCLUDES = ("speedFactor",)  # what population-v1 leaves out (population.py)
BASELINE_ARM = "baseline"
SHOWN_PROBLEMS = 6


class ReservedSeedError(PairingError):
    """A reserved confirmation seed (301-320) was used without confirmation=True."""


def check_seeds(seeds: Iterable[int], *, confirmation: bool = False) -> None:
    """Raise ReservedSeedError if `seeds` holds a confirmation seed and confirmation is not set."""
    reserved = sorted(set(seeds) & CONFIRMATION_SEEDS)
    if reserved and not confirmation:
        raise ReservedSeedError(
            f"seeds {', '.join(map(str, reserved))} are reserved confirmation seeds (301-320): "
            "pass confirmation=True to use them"
        )


def unit_of_inference(populations: Sequence[str | None]) -> str:
    """What n paired seeds are a sample of, given each seed's vehicle population hash."""
    n = len(populations)
    if n == 0 or None in populations:
        return f"n = {n} seeds; vehicle populations not verified"
    distinct = len(set(populations))
    if distinct == 1:
        return f"n = {n} seeds on one vehicle realisation; conditional on it"
    if distinct == n:
        return f"n = {n} seeds, one vehicle realisation each"
    return f"n = {n} seeds on {distinct} vehicle realisations"


def _hash_or_none(value: str | None) -> str | None:
    return None if value is None else _check_sha256(value)


class SeedDemand(EvidenceModel):
    """One seed's route file, as the baseline recorded it."""

    seed: int = Field(ge=0)
    demand_file: str = Field(min_length=1, description="relative to the baseline's demand dir")
    demand_sha256: str = Field(
        description="SHA-256 of the file's bytes: DemandFile.sha256, and RunRecord.demand_sha256 "
        "of every run on this seed"
    )
    population_sha256: str | None = Field(
        description="population-v1: the vehicles without speedFactor; None when not read"
    )
    speed_factors_sha256: str | None = Field(
        description="the seed's speedFactor draws alone; None when not read"
    )

    @field_validator("demand_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)

    @field_validator("population_sha256", "speed_factors_sha256")
    @classmethod
    def _optional_hash(cls, value: str | None) -> str | None:
        return _hash_or_none(value)


class ArmRuns(EvidenceModel):
    """One arm of the experiment: what it ran, on which seeds, with which SUMO binary."""

    arm: str = Field(min_length=1, description="'baseline' or an intervention id")
    plan_sha256: str = Field(description="pairing.plan_sha256 of the signal program it ran")
    intervention_sha256: str | None = Field(description="the proposal's hash; None for baseline")
    network_sha256: str = Field(description="the network apart from its signal program")
    sumo_binary_version: str | None = Field(
        description="the version every run output's header names; None when none names one"
    )
    seeds: tuple[int, ...]

    @field_validator("plan_sha256", "network_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)

    @field_validator("intervention_sha256")
    @classmethod
    def _optional_hash(cls, value: str | None) -> str | None:
        return _hash_or_none(value)


def _short(value: str) -> str:
    return value[:12]


def _arm_order(arm: ArmRuns) -> tuple[bool, str]:
    return (arm.arm != BASELINE_ARM, arm.arm)


def _pair_order(pair: PairIdentity) -> tuple[str, str, int]:
    return (pair.reference.arm, pair.compared.arm, pair.reference.seed)


def view_problems(
    member_sha256: str,
    timeline_id: str,
    world_model_sha256: str,
    network_sha256: str,
    seeds: Sequence[SeedDemand],
    arms: Sequence[ArmRuns],
    pairs: Sequence[PairIdentity],
    sumo_binary_version: str | None,
    confirmation: bool,
) -> list[str]:
    """Every reason the parts do not form one experiment's view; empty when they do."""
    problems: list[str] = []
    numbers = [s.seed for s in seeds]
    if numbers != sorted(set(numbers)):
        problems.append("the seeds must be listed once each, in increasing order")
    reserved = sorted(set(numbers) & CONFIRMATION_SEEDS)
    if reserved and not confirmation:
        problems.append(f"seeds {reserved} are reserved confirmation seeds and confirmation is off")
    by_seed = {s.seed: s for s in seeds}
    by_arm = {a.arm: a for a in arms}
    if len(by_arm) != len(arms):
        problems.append("an arm is listed twice")
    if list(arms) != sorted(arms, key=_arm_order):
        problems.append("the arms must be in canonical order: the baseline first, then by name")
    baseline = by_arm.get(BASELINE_ARM)
    if baseline is None:
        problems.append("the view has no baseline arm")
    else:
        if baseline.intervention_sha256 is not None:
            problems.append("the baseline arm names an intervention")
        if baseline.network_sha256 != network_sha256:
            problems.append("the view's network is not its baseline arm's network")
    for a in arms:
        if list(a.seeds) != numbers:
            problems.append(f"arm {a.arm!r} ran seeds {list(a.seeds)}, not the view's {numbers}")
    versions = sorted({str(a.sumo_binary_version) for a in arms})
    if len(versions) > 1:
        problems.append(f"the arms ran different SUMO binaries: {', '.join(versions)}")
    elif arms and arms[0].sumo_binary_version != sumo_binary_version:
        problems.append("the view's SUMO binary version is not its arms'")
    if list(pairs) != sorted(pairs, key=_pair_order):
        problems.append("the pairs must be in canonical order (reference, compared, seed)")
    seen: set[str] = set()
    paired: dict[tuple[str, int], int] = {}
    for pair in pairs:
        tag = f"pair {_short(pair.pair_sha256)}"
        if pair.pair_sha256 in seen:
            problems.append(f"{tag} is listed twice")
        seen.add(pair.pair_sha256)
        if pair.reference.arm != BASELINE_ARM:
            problems.append(f"{tag}: its reference is {pair.reference.arm!r}, not the baseline")
        key = (pair.compared.arm, pair.compared.seed)
        paired[key] = paired.get(key, 0) + 1
        for side in (pair.reference, pair.compared):
            where = f"{tag} ({side.arm}, seed {side.seed})"
            if side.member_sha256 != member_sha256:
                problems.append(f"{where}: its timeline member is not the view's")
            if side.timeline_id != timeline_id:
                problems.append(f"{where}: its signal timeline is not the view's")
            if side.world_model_sha256 != world_model_sha256:
                problems.append(f"{where}: its world model is not the view's")
            arm = by_arm.get(side.arm)
            if arm is None:
                problems.append(f"{where}: arm {side.arm!r} is not one of the view's arms")
            else:
                for field, label in (
                    ("plan_sha256", "plan"),
                    ("network_sha256", "network"),
                    ("intervention_sha256", "intervention"),
                ):
                    if getattr(side, field) != getattr(arm, field):
                        problems.append(f"{where}: its {label} is not arm {arm.arm!r}'s")
            listed = by_seed.get(side.seed)
            if listed is None:
                problems.append(f"{where}: seed {side.seed} is not one of the view's seeds")
                continue
            if side.demand_sha256 != listed.demand_sha256:
                problems.append(f"{where}: its demand file is not the seed's recorded demand")
            if side.population_sha256 != listed.population_sha256:
                problems.append(f"{where}: its vehicle population is not the seed's population")
    for a in arms:
        if a.arm == BASELINE_ARM:
            continue
        for seed in numbers:
            count = paired.get((a.arm, seed), 0)
            if count != 1:
                problems.append(
                    f"arm {a.arm!r} seed {seed}: {count} pairs with the baseline, not 1"
                )
    return problems


def _summary(problems: Sequence[str]) -> str:
    more = len(problems) - SHOWN_PROBLEMS
    return "; ".join(problems[:SHOWN_PROBLEMS]) + (f"; and {more} more" if more > 0 else "")


class PairIdentityView(EvidenceModel):
    """One experiment's pairs and what they share: a sidecar, named by `view_sha256`."""

    view_version: Literal["0.1.0"] = PAIR_VIEW_VERSION
    experiment_id: str = Field(min_length=1)
    baseline_experiment_id: str = Field(min_length=1)
    member_sha256: str = Field(description="SHA-256 of the baseline_manifest.json: the member")
    timeline_id: str = Field(min_length=1, description="the signal timeline the member replays")
    world_model_sha256: str
    network_sha256: str = Field(description="the baseline network apart from its signal program")
    horizon_s: float = Field(gt=0, description="measurement window every run shares (settings)")
    sumo_binary_version: str | None = Field(
        description="the SUMO binary every run's output header names; None when none names one"
    )
    sumo_wheel_version: str = Field(
        description="the eclipse-sumo package version the manifests record; not the binary"
    )
    confirmation: bool = Field(description="True only if reserved seeds 301-320 were allowed")
    seeds: tuple[SeedDemand, ...] = Field(min_length=1)
    arms: tuple[ArmRuns, ...] = Field(min_length=1)
    pairs: tuple[PairIdentity, ...]
    unit_of_inference: str = Field(description="unit_of_inference() of the seeds' populations")
    view_sha256: str

    @field_validator("member_sha256", "world_model_sha256", "network_sha256", "view_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)

    def digest(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"view_sha256"}))

    def pair_ids(self) -> tuple[str, ...]:
        return tuple(p.pair_sha256 for p in self.pairs)

    @model_validator(mode="after")
    def _consistent(self) -> PairIdentityView:
        problems = view_problems(
            self.member_sha256, self.timeline_id, self.world_model_sha256, self.network_sha256,
            self.seeds, self.arms, self.pairs, self.sumo_binary_version, self.confirmation,
        )  # fmt: skip
        if problems:
            raise ValueError("not a consistent view: " + _summary(problems))
        expected = unit_of_inference([s.population_sha256 for s in self.seeds])
        if self.unit_of_inference != expected:
            raise ValueError(
                f"unit of inference {self.unit_of_inference!r} is not what the seeds' "
                f"populations give ({expected!r})"
            )
        if self.view_sha256 != self.digest():
            raise ValueError("view_sha256 does not hash the view's content")
        return self


def build_view(
    *,
    experiment_id: str,
    baseline_experiment_id: str,
    member_sha256: str,
    timeline_id: str,
    world_model_sha256: str,
    network_sha256: str,
    horizon_s: float,
    sumo_wheel_version: str,
    seeds: Iterable[SeedDemand],
    arms: Iterable[ArmRuns],
    pairs: Iterable[PairIdentity],
    confirmation: bool = False,
) -> PairIdentityView:
    """The view, or a PairingError (ReservedSeedError for confirmation seeds) naming every
    reason the parts are not one experiment's pairs. Parts are put in a canonical order."""
    ordered_seeds = tuple(sorted(seeds, key=lambda s: s.seed))
    check_seeds((s.seed for s in ordered_seeds), confirmation=confirmation)
    ordered_arms = tuple(sorted(arms, key=_arm_order))
    ordered_pairs = tuple(sorted(pairs, key=_pair_order))
    versions = {a.sumo_binary_version for a in ordered_arms}
    version = next(iter(versions)) if len(versions) == 1 else None
    problems = view_problems(
        member_sha256, timeline_id, world_model_sha256, network_sha256, ordered_seeds,
        ordered_arms, ordered_pairs, version, confirmation,
    )  # fmt: skip
    if problems:
        raise PairingError(_summary(problems))
    fields = dict(
        experiment_id=experiment_id,
        baseline_experiment_id=baseline_experiment_id,
        member_sha256=member_sha256,
        timeline_id=timeline_id,
        world_model_sha256=world_model_sha256,
        network_sha256=network_sha256,
        horizon_s=horizon_s,
        sumo_binary_version=version,
        sumo_wheel_version=sumo_wheel_version,
        confirmation=confirmation,
        seeds=ordered_seeds,
        arms=ordered_arms,
        pairs=ordered_pairs,
        unit_of_inference=unit_of_inference([s.population_sha256 for s in ordered_seeds]),
    )
    draft = PairIdentityView.model_construct(**fields, view_sha256="0" * 64)
    return PairIdentityView(**fields, view_sha256=draft.digest())
