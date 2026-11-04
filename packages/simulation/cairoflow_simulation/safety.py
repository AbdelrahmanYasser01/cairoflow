"""Per-candidate safety record: what was checked before a candidate's numbers may be read.

One chain per proposal, in this order; a step runs only when every step before it passed, and the
rest are recorded as 'not_run':

1. verdict: the validator accepted it (`validation.check_proposals`); its preconditions are
   carried as unverified assumptions whatever the verdict.
2. compile: the candidate network builds (`candidates.build_candidate_network`) and the tlLogic
   in the compiled .net.xml, which SUMO will run, shows the validated phases, states and
   durations, keeps the cycle and head-run bounds and gives no two foes priority green
   (`validation.compiled_problems`).
3. plain_xml: the candidate's nodes, edges and connections files hash as the baseline recorded.
4. demand: on EVERY seed, the demand file hashes as the baseline manifest and its run recorded;
   a file that is missing fails its seed (the record is still written).
5. smoke: one seed in SUMO, through the frozen runner (`runner.run_seed`), with the flags that
   make the counts meaningful; SUMO's statistics output must show 0 collisions, 0 teleports and
   0 vehicles running or waiting at the end, and the runner's own validity must hold. The record
   names the smoke seed only once the smoke runs.
6. rerun: the same seed again gives identical tripinfo.xml and queue.xml, XML comments (SUMO's
   generation timestamp) stripped.

This chain runs SUMO, so like run_candidates it refuses a baseline or smoke seed among the
reserved confirmation seeds 301-320 before anything runs (`pair_identity.check_seeds`, ADR-033)
unless the caller passes confirmation=True: nothing is simulated or recorded on them.

Timings are wall-clock seconds (time.perf_counter) measured around the setup, compile and runner
calls on the machine that made the record: they describe this run, not the simulated traffic.
The record is written once to a new file and never overwritten; committed records are not edited.
"""

from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.experiment import BaselineManifest
from cairoflow_contracts.pair_identity import check_seeds
from cairoflow_simulation.candidates import CandidateError, build_candidate_network, load_baseline
from cairoflow_simulation.geometry_compiler import FILES, CompileError
from cairoflow_simulation.network import content_sha256
from cairoflow_simulation.runner import MANIFEST_FILE, SimulationError, run_seed
from cairoflow_simulation.validation import (
    VALIDATOR_VERSION,
    CheckedProposal,
    ValidationInputError,
    compiled_problems,
)

SAFETY_VERSION = "0.1.0"
SAFETY_FILE = "candidate_safety.json"
CLOSED = "closed negative result (ADR-033)"
STEPS = ("verdict", "compile", "plain_xml", "demand", "smoke", "rerun")
COMPARED_OUTPUTS = ("tripinfo.xml", "queue.xml")
# runner.run_seed passes these; without them SUMO's zero counts would not mean "none happened"
REQUIRED_FLAGS: dict[str, str | None] = {
    "--statistic-output": None,
    "--collision.check-junctions": "true",
    "--tripinfo-output.write-unfinished": "true",
    "--queue-output": None,
}
StepName = Literal["verdict", "compile", "plain_xml", "demand", "smoke", "rerun"]


class SafetyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SafetyStep(SafetyModel):
    step: StepName
    status: Literal["passed", "failed", "not_run"]
    details: tuple[str, ...]


class CandidateSafety(SafetyModel):
    """One proposal's chain. `safe` only when every step passed."""

    intervention_id: str
    proposal_sha256: str | None
    validator_status: Literal["accepted", "rejected"]
    cycle_s: float | None = Field(description="validated candidate cycle; None when rejected")
    baseline_cycle_s: float
    unverified_assumptions: tuple[str, ...] = Field(description="model-stated preconditions")
    steps: tuple[SafetyStep, ...]
    safe: bool
    smoke_seed: int | None = Field(description="the seed the smoke ran on; None if it did not")
    smoke_statistics: dict[str, int] | None = Field(
        description="collisions, teleports, unfinished (running + waiting), from SUMO's "
        "statistics output of the smoke run; simulated"
    )
    files_sha256: dict[str, str] = Field(
        description="smoke-run output -> SHA-256 without XML comments, as the rerun matched it"
    )
    timings_s: dict[str, float] = Field(
        description="wall-clock seconds measured around setup, compile and the runner calls on "
        "this machine; not simulator outputs"
    )


class ClosedResult(SafetyModel):
    """A result that is closed and is listed, never re-run."""

    result_id: str
    status: Literal["closed negative result (ADR-033)"] = CLOSED
    finding: str
    evidence: dict[str, str] = Field(description="committed record path -> its SHA-256")
    re_run: Literal[False] = False


class SafetyRecord(SafetyModel):
    record_version: Literal["0.1.0"] = SAFETY_VERSION
    created_at_utc: datetime
    validator_version: str
    baseline_experiment_id: str
    baseline_manifest_sha256: str
    source: dict[str, str] = Field(description="where the proposals came from")
    candidates: tuple[CandidateSafety, ...]
    closed: tuple[ClosedResult, ...]
    findings: tuple[str, ...] = Field(description="what stopped a candidate from being checked")
    safe: bool = Field(description="at least one candidate, and every one passed every step")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def closed_result(
    result_id: str, finding: str, evidence: Sequence[str], root: Path | None = None
) -> ClosedResult:
    """A closed result with the hash of each committed record it rests on (paths from `root`)."""
    base = root or Path.cwd()
    return ClosedResult(
        result_id=result_id, finding=finding, evidence={p: _sha256(base / p) for p in evidence}
    )


def statistics_counts(path: Path) -> dict[str, int]:
    """Collisions, teleports and vehicles still running or waiting, from SUMO's statistics."""
    root = ET.parse(path).getroot()
    vehicles, teleports, safety = (root.find(name) for name in ("vehicles", "teleports", "safety"))
    if vehicles is None or teleports is None or safety is None:
        raise SimulationError(f"{path.name} lacks vehicles, teleports or safety")
    return {
        "collisions": int(safety.get("collisions", 0)),
        "teleports": int(teleports.get("total", 0)),
        "unfinished": int(vehicles.get("running", 0)) + int(vehicles.get("waiting", 0)),
    }


def _missing_flags(command: Sequence[str]) -> list[str]:
    values = dict(zip(command[1::2], command[2::2], strict=False))
    return [
        flag if want is None else f"{flag} {want}"
        for flag, want in REQUIRED_FLAGS.items()
        if flag not in values or (want is not None and values[flag] != want)
    ]


def _baseline_seeds(manifest: BaselineManifest, smoke_seed: int | None) -> list[int]:
    """Every seed the baseline names (settings, demand files, runs, reruns) and the smoke seed."""
    return [*manifest.settings.seeds, *(f.seed for f in manifest.demand.files),
            *(r.seed for r in (*manifest.runs, *manifest.reruns)),
            *(() if smoke_seed is None else (smoke_seed,))]  # fmt: skip


def check_candidate(
    checked: CheckedProposal,
    baseline_dir: Path,
    out_dir: Path,
    *,
    smoke_seed: int | None = None,
    confirmation: bool = False,
) -> CandidateSafety:
    """Run the chain of the module notes for one proposal; candidate files go under `out_dir`.
    An accepted proposal on a baseline (or smoke seed) among the reserved confirmation seeds
    301-320 is refused before anything is built or run (ReservedSeedError) unless `confirmation`
    is set."""
    outcome = checked.outcome
    steps: list[SafetyStep] = []
    timings: dict[str, float] = {}
    smoke: dict[str, int] | None = None
    hashes: dict[str, str] = {}
    seed: int | None = None

    def record() -> CandidateSafety:
        done = {s.step for s in steps}
        rest = [SafetyStep(step=s, status="not_run", details=("an earlier step failed",))
                for s in STEPS if s not in done]  # fmt: skip
        chain = (*steps, *rest)
        return CandidateSafety(
            intervention_id=outcome.intervention_id,
            proposal_sha256=outcome.proposal_sha256,
            validator_status=outcome.status,
            cycle_s=outcome.cycle_s,
            baseline_cycle_s=outcome.baseline_cycle_s,
            unverified_assumptions=checked.unverified_assumptions,
            steps=chain,
            safe=all(s.status == "passed" for s in chain),
            smoke_seed=seed,
            smoke_statistics=smoke,
            files_sha256=hashes,
            timings_s=timings,
        )

    def step(name: StepName, problems: Sequence[str], passed: Sequence[str],
             items: Sequence[str] = ()) -> bool:  # fmt: skip
        """Record a step; with `items` (one line per seed or file, in order) every line is kept,
        failed or not; otherwise the problems, or the summary when there are none."""
        extra = [p for p in problems if p not in items]
        details = (*items, *extra) if items else (tuple(problems) or tuple(passed))
        steps.append(SafetyStep(step=name, status="failed" if problems else "passed",
                                details=details))  # fmt: skip
        return not problems

    if outcome.status != "accepted" or outcome.phase_durations_s is None:
        step("verdict", outcome.reasons or ("rejected",), ())
        return record()
    accepted = (
        f"accepted by validator {outcome.validator_version}: cycle {outcome.cycle_s:g} s "
        f"(baseline {outcome.baseline_cycle_s:g} s)"
    )
    step("verdict", (), (accepted,))

    start = time.perf_counter()
    manifest, baseline = load_baseline(baseline_dir)
    check_seeds(_baseline_seeds(manifest, smoke_seed), confirmation=confirmation)
    demand_dir = baseline_dir / "demand"
    recorded = {run.seed: run.demand_sha256 for run in manifest.runs}
    actual = {f.seed: _sha256(demand_dir / f.file) if (demand_dir / f.file).is_file() else None
              for f in manifest.demand.files}  # fmt: skip
    files = manifest.demand.files
    chosen = smoke_seed if smoke_seed is not None else files[0].seed if files else None
    timings["setup_s"] = time.perf_counter() - start

    start = time.perf_counter()
    try:
        network = build_candidate_network(baseline, outcome, out_dir / "network")
    except (CandidateError, CompileError) as error:
        timings["compile_s"] = time.perf_counter() - start
        step("compile", (f"the candidate network does not build: {error}",), ())
        return record()
    timings["compile_s"] = time.perf_counter() - start
    program = network.manifest.signal_program
    try:
        problems = compiled_problems(network.net_path, program.program_id, program.phases,
                                     outcome.phase_durations_s)  # fmt: skip
    except ValidationInputError as error:
        problems = [str(error)]
    embedded = (
        f"{network.net_path.name} embeds {program.program_id} as validated; within the cycle "
        "and head-run bounds; no conflicting priority greens"
    )
    if not step("compile", problems, (embedded,)):
        return record()

    expected = manifest.network.plain_xml_sha256
    plain = {name: content_sha256(out_dir / "network" / name) for name in FILES[:3]}
    if not step("plain_xml",
                [f"{n} differs from the baseline's ({plain[n][:12]} vs {expected[n][:12]})"
                 for n in FILES[:3] if plain[n] != expected[n]],
                [f"{n} sha256 {plain[n][:12]} as the baseline" for n in FILES[:3]]):  # fmt: skip
        return record()

    demand_problems, demand_items = [], []
    for file in manifest.demand.files:
        sha = actual[file.seed]
        if sha is None:
            demand_items.append(f"seed {file.seed}: {file.file} is missing")
            demand_problems.append(demand_items[-1])
        elif sha == file.sha256 == recorded.get(file.seed):
            demand_items.append(f"seed {file.seed}: {file.file} sha256 {sha[:12]} as recorded")
        else:
            demand_items.append(f"seed {file.seed}: {file.file} differs from the baseline's "
                                f"record ({sha[:12]} vs {file.sha256[:12]})")  # fmt: skip
            demand_problems.append(demand_items[-1])
    if not files:
        demand_problems.append("the baseline lists no demand file")
    elif chosen not in actual:
        demand_problems.append(f"smoke seed {chosen} has no baseline demand file")
    if not step("demand", demand_problems, (), demand_items):
        return record()

    seed = chosen  # the smoke runs on it from here, so the record names it
    demand_path = demand_dir / next(f.file for f in manifest.demand.files if f.seed == seed)
    runs = out_dir / "runs"
    start = time.perf_counter()
    try:
        first = run_seed(network, demand_path, seed, manifest.settings, runs / f"seed_{seed}")
        smoke = statistics_counts(runs / first.run_dir / "statistics.xml")
    except SimulationError as error:
        timings["run_s"] = time.perf_counter() - start
        step("smoke", (f"seed {seed}: {error}",), ())
        return record()
    timings["run_s"] = time.perf_counter() - start
    problems = [f"{count} {name}" for name, count in smoke.items() if count]
    problems += [f"the run lacks {flag}" for flag in _missing_flags(first.command)]
    problems += [] if first.valid else [f"runner: {reason}" for reason in first.reasons]
    clean = (
        f"seed {seed}: 0 collisions, 0 teleports, 0 unfinished; "
        f"{first.arrived} of {first.loaded} vehicles arrived"
    )
    if not step("smoke", problems, (clean,)):
        return record()

    start = time.perf_counter()
    try:
        again = run_seed(network, demand_path, seed, manifest.settings, runs / f"seed_{seed}_rerun")
    except SimulationError as error:
        timings["rerun_s"] = time.perf_counter() - start
        step("rerun", (f"seed {seed}: {error}",), ())
        return record()
    timings["rerun_s"] = time.perf_counter() - start
    problems, items = [], []
    for name in COMPARED_OUTPUTS:
        one = content_sha256(runs / first.run_dir / name, strip_comments=True)
        two = content_sha256(runs / again.run_dir / name, strip_comments=True)
        hashes[name] = one
        if one == two:
            items.append(f"{name} identical on the rerun (sha256 {one[:12]}, comments stripped)")
        else:
            items.append(f"{name} differs on the rerun ({one[:12]} vs {two[:12]})")
            problems.append(items[-1])
    step("rerun", problems, (), items)
    return record()


def build_safety_record(
    checked: Sequence[CheckedProposal],
    baseline_dir: Path,
    out_dir: Path,
    *,
    now: datetime,
    source: Mapping[str, str],
    closed: Sequence[ClosedResult] = (),
    findings: Sequence[str] = (),
    smoke_seed: int | None = None,
    confirmation: bool = False,
) -> SafetyRecord:
    """Check every proposal under `out_dir` (new) and write `SAFETY_FILE` there, once. A baseline
    (or smoke seed) among the reserved confirmation seeds 301-320 is refused before `out_dir` is
    made or anything runs (ReservedSeedError) unless `confirmation` is set."""
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists; a safety record is never overwritten")
    ids = [c.outcome.intervention_id for c in checked]
    if len(set(ids)) != len(ids):
        raise CandidateError("intervention ids must be unique")
    manifest, _ = load_baseline(baseline_dir)
    check_seeds(_baseline_seeds(manifest, smoke_seed), confirmation=confirmation)
    out_dir.mkdir(parents=True)
    results = tuple(
        check_candidate(
            c,
            baseline_dir,
            out_dir / "candidates" / c.outcome.intervention_id,
            smoke_seed=smoke_seed,
            confirmation=confirmation,
        )  # fmt: skip
        for c in checked
    )
    record = SafetyRecord(
        created_at_utc=now,
        validator_version=VALIDATOR_VERSION,
        baseline_experiment_id=manifest.experiment_id,
        baseline_manifest_sha256=_sha256(baseline_dir / MANIFEST_FILE),
        source=dict(source),
        candidates=results,
        closed=tuple(closed),
        findings=tuple(findings),
        safe=bool(results) and all(r.safe for r in results),
    )
    with (out_dir / SAFETY_FILE).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(record.model_dump_json(indent=2) + "\n")
    return record
