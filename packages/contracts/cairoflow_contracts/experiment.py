"""Simulation experiment records: compiled network, demand and runs (schema version 0.1.0).

Everything needed to rebuild and rerun a baseline: input hashes, every network parameter with its
ADR-014 label, the signal program and where it came from, the per-seed demand files and the hash
of every SUMO output. Numbers here describe inputs and run validity only; simulated metrics are
computed later from the recorded output files (cairoflow_evaluation).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from cairoflow_contracts.evidence import (
    Arm,
    EvidenceModel,
    GeometryProvenance,
    GeometryUnit,
    SignalLabel,
    SignalState,
    Turn,
    _check_sha256,
)
from cairoflow_contracts.reasoning import ValidationOutcome


class NetworkParameter(EvidenceModel):
    """One value the network was built from, with where it came from."""

    name: str = Field(min_length=1, description="e.g. 'S.arm_length' or 'S1.width'")
    value: float
    unit: GeometryUnit
    provenance: GeometryProvenance
    source: str = Field(min_length=1)


class SignalPhaseSpec(EvidenceModel):
    name: str = Field(min_length=1)
    duration_s: float = Field(gt=0)
    state: str = Field(
        pattern=r"^[GgyrOs]+$",
        description="SUMO link states, by link index ('s': right turn on red, after stopping)",
    )
    arm_states: dict[Arm, SignalState]
    left_states: dict[Arm, SignalState] = Field(
        default_factory=dict, description="protected-left heads, for arms that have one"
    )

    @property
    def adjustable(self) -> bool:
        """True for a green phase in which no head shows yellow: only its duration may change.

        A phase with any yellow is a clearance interval for that head, and a phase without green
        is all-red; both stay as compiled.
        """
        states = (*self.arm_states.values(), *self.left_states.values())
        return "green" in states and "yellow" not in states


class SignalProgramSpec(EvidenceModel):
    """One signal cycle cut from a timeline and repeated by SUMO."""

    program_id: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    timeline_id: str
    label: SignalLabel
    annotation_sha256: str | None
    cycle_start_s: float = Field(ge=0, description="clip time where the cut cycle begins")
    cycle_s: float = Field(gt=0)
    phases: tuple[SignalPhaseSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _sums(self) -> SignalProgramSpec:
        if abs(sum(p.duration_s for p in self.phases) - self.cycle_s) > 1e-6:
            raise ValueError("phase durations must add up to the cycle")
        return self


class CompiledNetworkManifest(EvidenceModel):
    geometry_id: str
    geometry_sha256: str
    sumo_version: str
    plain_xml_sha256: dict[str, str] = Field(description="file name -> SHA-256")
    net_sha256: str = Field(description="of the .net.xml with XML comments stripped")
    link_count: int = Field(gt=0)
    parameters: tuple[NetworkParameter, ...]
    signal_program: SignalProgramSpec
    netconvert_warnings: tuple[str, ...]
    notes: tuple[str, ...]

    @field_validator("geometry_sha256", "net_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)


class MovementDemand(EvidenceModel):
    """The demand of one movement, taken from the world model's point estimate."""

    approach: Arm
    exit_arm: Arm
    turn: Turn
    observed_count_veh: int = Field(ge=0)
    flow_veh_per_h: float = Field(ge=0)
    flow_ci95_veh_per_h: tuple[float, float]
    vehicle_type_mix: dict[str, float] = Field(description="SUMO vType -> share of counted tracks")


class DemandFile(EvidenceModel):
    seed: int = Field(ge=0)
    file: str = Field(min_length=1, description="relative to the demand directory")
    sha256: str
    vehicles: int = Field(ge=0)

    @field_validator("sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)


class DemandManifest(EvidenceModel):
    world_model_sha256: str
    movements: tuple[MovementDemand, ...]
    generation_end_s: float = Field(gt=0, description="vehicles depart in [0, this)")
    speed_factor_sd: float = Field(ge=0)
    files: tuple[DemandFile, ...]
    notes: tuple[str, ...]


class RunSettings(EvidenceModel):
    """Shared by the baseline and every candidate (ADR-008)."""

    seeds: tuple[int, ...] = Field((1, 2, 3, 4, 5), min_length=1)
    warmup_s: float = Field(300.0, ge=0)
    horizon_s: float = Field(900.0, gt=0, description="measurement window after warm-up")
    drain_s: float = Field(600.0, ge=0, description="no new vehicles; lets the network empty")
    step_length_s: float = Field(1.0, gt=0)
    time_to_teleport_s: float = Field(300.0, gt=0)

    @field_validator("seeds")
    @classmethod
    def _unique(cls, seeds: tuple[int, ...]) -> tuple[int, ...]:
        if len(set(seeds)) != len(seeds) or min(seeds) < 0:
            raise ValueError("seeds must be unique and non-negative")
        return seeds

    @property
    def generation_end_s(self) -> float:
        return self.warmup_s + self.horizon_s

    @property
    def end_s(self) -> float:
        return self.warmup_s + self.horizon_s + self.drain_s


class RunRecord(EvidenceModel):
    """One SUMO run: what went in, the output hashes and whether it is valid."""

    seed: int
    run_dir: str
    command: tuple[str, ...] = Field(description="relative paths, run from run_dir")
    demand_sha256: str
    tripinfo_sha256: str = Field(description="XML comments stripped")
    loaded: int = Field(ge=0)
    inserted: int = Field(ge=0)
    arrived: int = Field(ge=0)
    unfinished: int = Field(ge=0, description="still running or waiting to insert at the end")
    teleports: int = Field(ge=0)
    collisions: int = Field(ge=0)
    valid: bool
    reasons: tuple[str, ...]


class BaselineManifest(EvidenceModel):
    """A baseline experiment: replayable from the recorded inputs and settings."""

    experiment_id: str = Field(min_length=1)
    created_at_utc: datetime
    world_model_sha256: str
    settings: RunSettings
    network: CompiledNetworkManifest
    demand: DemandManifest
    runs: tuple[RunRecord, ...]
    reruns: tuple[RunRecord, ...] = Field(description="each seed run again, to compare hashes")
    reruns_identical: bool | None = Field(description="None when reruns were not made")
    valid: bool
    reasons: tuple[str, ...]


class CandidateRecord(EvidenceModel):
    """One validated intervention, rebuilt into a network and run on the baseline's seeds."""

    intervention_id: str
    outcome: ValidationOutcome
    network: CompiledNetworkManifest
    runs: tuple[RunRecord, ...]
    valid: bool
    reasons: tuple[str, ...]


class ExperimentManifest(EvidenceModel):
    """Candidates run against a baseline with the same network, demand, settings and seeds."""

    experiment_id: str = Field(min_length=1)
    created_at_utc: datetime
    baseline_dir: str = Field(description="relative to the experiment directory")
    baseline_experiment_id: str
    baseline_manifest_sha256: str
    settings: RunSettings
    candidates: tuple[CandidateRecord, ...]
    skipped: tuple[str, ...] = Field(description="rejected outcomes, not simulated")
    valid: bool
    reasons: tuple[str, ...]


MetricUnit = Literal["s", "veh/h", "m", "veh"]


class MetricResult(EvidenceModel):
    """One simulated metric of one run, computed from one SUMO output file (never from a model)."""

    experiment_id: str
    arm: str = Field(description="'baseline' or an intervention id")
    seed: int
    metric_name: str = Field(description="e.g. 'mean_delay_s' or 'max_queue_m.S'")
    value: float
    unit: MetricUnit
    window_s: tuple[float, float] = Field(description="measurement window, simulation seconds")
    sample_size: int = Field(ge=0, description="vehicles or time steps behind the value")
    source_output_file: str = Field(description="relative to the experiment directory")
    source_sha256: str
    calculation_version: str
    kind: Literal["simulated"] = "simulated"

    @field_validator("source_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)


class PairedComparison(EvidenceModel):
    """Candidate minus baseline for one metric, seed by seed."""

    candidate: str
    metric_name: str
    unit: MetricUnit
    better: Literal["lower", "higher"]
    seeds: tuple[int, ...]
    baseline_values: tuple[float, ...]
    candidate_values: tuple[float, ...]
    differences: tuple[float, ...]
    baseline_mean: float
    candidate_mean: float
    mean_difference: float
    sd_difference: float | None = Field(description="sample standard deviation; None if n < 2")
    ci95_difference: tuple[float, float] | None = Field(description="t interval; None if n < 2")
    verdict: Literal["improves", "worsens", "inconclusive", "uninformative"] = Field(
        description="improves or worsens only when the interval excludes zero; uninformative "
        "(rules 0.3.0) when the values measure a state both runs share, not the plan"
    )
    shared_state_seeds: tuple[int, ...] = Field(
        default=(),
        description="maximum queues only: seeds on which both runs reached their maximum before "
        "their signal programs first differed, so that seed's difference is zero by construction",
    )

    @model_validator(mode="after")
    def _paired(self) -> PairedComparison:
        n = len(self.seeds)
        if not (
            len(self.baseline_values) == len(self.candidate_values) == len(self.differences) == n
        ):
            raise ValueError("values and differences must be paired by seed")
        return self


RunClass = Literal["complete", "incomplete", "integrity_failure"]
CandidateStatus = Literal["compared", "incomplete", "invalid_simulation"]


class UnfinishedMovement(EvidenceModel):
    """Vehicles of one movement still in the network when a run ended (from tripinfo.xml)."""

    seed: int
    movement: str = Field(description="approach and exit arm, from the vehicle id, e.g. 'WN'")
    vehicles: int = Field(gt=0)
    first_depart_s: float = Field(description="earliest insertion among them")
    delay_lower_bound_range_s: tuple[float, float] = Field(
        description="min and max over these vehicles of timeLoss + departDelay at the end; the "
        "trips are unfinished, so each vehicle's value is a lower bound of its delay"
    )


class CandidateEligibility(EvidenceModel):
    """Whether a candidate's runs can be compared with the baseline, and why not."""

    candidate: str
    status: CandidateStatus
    run_classes: dict[int, RunClass] = Field(description="seed -> class of that run")
    not_inserted: dict[int, int] = Field(
        description="seed -> vehicles still waiting to enter at the end (not in tripinfo.xml)"
    )
    unfinished: tuple[UnfinishedMovement, ...]
    reasons: tuple[str, ...]
    censored_lanes: dict[int, tuple[str, ...]] = Field(
        default_factory=dict,
        description="seed -> approach lanes whose queue reached their upstream end in the window, "
        "so the arm's queue metrics are lower bounds",
    )
    entry_blocked: dict[int, dict[str, float]] = Field(
        default_factory=dict,
        description="seed -> arm -> vehicle-seconds in the window that vehicles of that arm waited "
        "to enter the network (tripinfo insertion delays of at least 2 s; rules 0.3.1, in 0.3.0 "
        "only while a standing queue was at their entry); queue.xml does not see them, so the "
        "arm's queue metrics are lower bounds",
    )


class ImprovementTest(EvidenceModel):
    """One planned improvement claim (a candidate and a metric), tested with Holm's step-down."""

    candidate: str
    metric_name: str
    n: int = Field(ge=0, description="paired seeds")
    t_statistic: float | None = Field(
        description="paired t of the difference, signed so that positive means better; None "
        "when untested (no comparison, fewer than 2 seeds, a zero standard deviation, or a "
        "censored queue: lane full or vehicles held off the network at the entry)"
    )
    p_value: float | None = Field(description="two-sided paired t test; None when untested")
    holm_threshold: float | None = Field(
        description="family alpha / (planned tests - rank + 1) at this test's rank; None when "
        "untested. Tests ranked after the first one not rejected are never rejected"
    )
    confirmed: bool = Field(description="rejected by Holm's procedure and in the better direction")


class EvaluationResult(EvidenceModel):
    """Every metric of every compared run and the paired comparisons; no combined score."""

    experiment_id: str
    calculation_version: str
    metrics: tuple[MetricResult, ...]
    comparisons: tuple[PairedComparison, ...]
    excluded: tuple[str, ...] = Field(
        description="candidates that can never be selected, and why (before 0.2.0: not compared)"
    )
    eligibility: tuple[CandidateEligibility, ...] = Field(
        default=(), description="every simulated candidate's status; empty in records before 0.2.0"
    )
    rules_version: str | None = Field(
        default=None, description="version of the eligibility and selection rules; None before"
    )
    improvement_tests: tuple[ImprovementTest, ...] = Field(
        default=(),
        description="every planned improvement test (each simulated candidate with each "
        "improvement metric); empty before 0.2.0",
    )
    baseline_censored_lanes: dict[int, tuple[str, ...]] = Field(
        default_factory=dict,
        description="seed -> baseline approach lanes whose queue reached their upstream end",
    )
    baseline_entry_blocked: dict[int, dict[str, float]] = Field(
        default_factory=dict,
        description="seed -> arm -> vehicle-seconds the baseline's vehicles waited to enter the "
        "network (as CandidateEligibility.entry_blocked)",
    )
