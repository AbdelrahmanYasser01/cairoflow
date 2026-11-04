"""Pair identities of an experiment: each candidate run against its baseline run (ADR-033).

`experiment_pairs` builds a `PairIdentity` for every (candidate, seed) from records that already
exist: the baseline and experiment manifests and the baseline's route files. It records nothing
and changes no output; `compare.compute_evaluation` calls it to refuse an experiment whose
candidates did not run on their baseline's demand, network, SUMO configuration and settings. A
paired t test on mismatched runs gives a number, not an answer.

What is compared, and what cannot be. The records keep, per run, the SHA-256 of the route file it
read, the full SUMO command and its seed; per network, the plain XML hashes, link count, simulator
version and parameters. Those are compared with the baseline's. Three things are filled in from
the baseline because candidates carry none of their own: the member (one experiment, one
baseline), the world model, and the vehicle population (a candidate's recorded demand hash equals
the baseline file's, so the file's population is its population). They cannot disagree here; they
matter when the ladder pairs arms from different experiments. A record edited by hand is caught,
and a run that really used other inputs is caught when it left a different trace in the above.

A route file that is gone is not an error unless `require_populations` is set: experiments
evaluated before this module existed may have lost theirs. The seed is then listed in
`unverified`, and its pairs keep every manifest-level rule. The ladder will require them.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cairoflow_contracts.experiment import (
    BaselineManifest,
    CompiledNetworkManifest,
    ExperimentManifest,
    RunRecord,
)
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.pairing import (
    PairIdentity,
    PairingError,
    RunIdentity,
    pairing_problems,
    plan_sha256,
    program_structure_sha256,
    settings_pairing_sha256,
)
from cairoflow_simulation.geometry_compiler import FILES
from cairoflow_simulation.population import PopulationError, population_sha256

DEMAND_DIR = "demand"
BASELINE_ARM = "baseline"
SHOWN_PROBLEMS = 6
PATH_FLAGS = ("--net-file", "--route-files")  # their values name directories, which differ by arm


@dataclass(frozen=True)
class ExperimentPairing:
    """The valid pairs of one experiment, and what could not be verified."""

    pairs: tuple[PairIdentity, ...]
    unverified: tuple[str, ...]


def _summary(problems: Sequence[str]) -> str:
    shown = "; ".join(problems[:SHOWN_PROBLEMS])
    more = len(problems) - SHOWN_PROBLEMS
    return shown + (f"; and {more} more" if more > 0 else "")


def network_sha256(network: CompiledNetworkManifest) -> str:
    """Hash of a compiled network but for its signal program: the plain XML hashes of the nodes,
    edges and connections (the files a candidate must copy, candidates.py), the link count, the
    simulator version and every parameter except the cycle length, which the program sets."""
    return canonical_sha256(
        {
            "plain_xml_sha256": {name: network.plain_xml_sha256.get(name) for name in FILES[:3]},
            "link_count": network.link_count,
            "sumo_version": network.sumo_version,
            "parameters": [
                p.model_dump(mode="json") for p in network.parameters if p.name != "signal.cycle"
            ],
        }
    )


def command_without_paths(command: Sequence[str]) -> list[str]:
    """A recorded SUMO command without the path flags and their values."""
    kept: list[str] = []
    skip = False
    for part in command:
        if skip:
            skip = False
        elif part in PATH_FLAGS:
            skip = True
        else:
            kept.append(part)
    return kept


def _command_seed(command: Sequence[str]) -> int | None:
    try:
        return int(command[list(command).index("--seed") + 1])
    except (ValueError, IndexError):
        return None


def _baseline_populations(
    baseline: BaselineManifest, baseline_dir: Path, require: bool, problems: list[str],
    unverified: list[str],
) -> dict[int, str | None]:  # fmt: skip
    """Seed -> the population hash of the baseline's route file (None if it was not read)."""
    found: dict[int, str | None] = {}
    for file in baseline.demand.files:
        path = baseline_dir / DEMAND_DIR / file.file
        found[file.seed] = None
        if not path.is_file():
            (problems if require else unverified).append(
                f"seed {file.seed}: demand file {file.file} is missing; population not read"
            )
        elif hashlib.sha256(path.read_bytes()).hexdigest() != file.sha256:
            problems.append(f"seed {file.seed}: demand file {file.file} changed after the baseline")
        else:
            try:
                found[file.seed] = population_sha256(path)
            except PopulationError as error:
                problems.append(str(error))
    return found


def _identity(problems: list[str], where: str, **fields: Any) -> RunIdentity | None:
    """A RunIdentity, or None and a problem when a record holds something that is not one."""
    try:
        return RunIdentity(**fields)
    except ValidationError as error:
        first = error.errors()[0]
        problems.append(
            f"{where}: malformed record ({'.'.join(map(str, first['loc']))}: {first['msg']})"
        )
        return None


def _run_problems(arm: str, run: RunRecord) -> list[str]:
    seed = _command_seed(run.command)
    if seed != run.seed:
        return [f"{arm} seed {run.seed}: its command runs SUMO with seed {seed}"]
    return []


def experiment_pairs(
    experiment: ExperimentManifest,
    baseline: BaselineManifest,
    baseline_dir: Path,
    member_sha256: str,
    *,
    require_populations: bool = False,
) -> ExperimentPairing:
    """Every candidate run paired with the baseline run of its seed, or a PairingError.

    `member_sha256` is the SHA-256 of the baseline's baseline_manifest.json, which the caller has
    read; it names the timeline member and is the scope of every pair."""
    problems: list[str] = []
    unverified: list[str] = []
    settings_hash = settings_pairing_sha256(baseline.settings)
    if settings_pairing_sha256(experiment.settings) != settings_hash:
        problems.append("the experiment's run settings are not the baseline's")
    base_seeds = [run.seed for run in baseline.runs]
    if len(set(base_seeds)) != len(base_seeds):
        problems.append("the baseline lists a seed twice")
    populations = _baseline_populations(
        baseline, baseline_dir, require_populations, problems, unverified
    )
    program = baseline.network.signal_program
    network_hash, structure_hash = (
        network_sha256(baseline.network),
        program_structure_sha256(program),
    )
    demand = {file.seed: file for file in baseline.demand.files}
    references: dict[int, RunIdentity] = {}
    for run in baseline.runs:
        listed = demand.get(run.seed)
        if listed is None or listed.sha256 != run.demand_sha256:
            problems.append(
                f"baseline seed {run.seed}: not run on the demand file the manifest lists"
            )
            continue
        problems += _run_problems("baseline", run)
        identity = _identity(
            problems, f"baseline seed {run.seed}",
            member_sha256=member_sha256, timeline_id=program.timeline_id, arm=BASELINE_ARM,
            seed=run.seed, demand_sha256=run.demand_sha256,
            population_sha256=populations.get(run.seed),
            world_model_sha256=baseline.world_model_sha256,
            geometry_sha256=baseline.network.geometry_sha256, network_sha256=network_hash,
            program_structure_sha256=structure_hash,
            run_config_sha256=canonical_sha256(command_without_paths(run.command)),
            settings_sha256=settings_hash, plan_sha256=plan_sha256(program),
        )  # fmt: skip
        if identity is not None:
            references[run.seed] = identity
    ids = Counter(record.intervention_id for record in experiment.candidates)
    problems += [f"intervention {i!r} appears {n} times" for i, n in sorted(ids.items()) if n > 1]
    if BASELINE_ARM in ids:
        problems.append("a candidate is called 'baseline'")
    pairs: list[PairIdentity] = []
    for record in experiment.candidates:
        arm = record.intervention_id
        seeds = [run.seed for run in record.runs]
        if len(set(seeds)) != len(seeds):
            problems.append(f"{arm}: it lists a seed twice")
        if set(seeds) != set(base_seeds):
            problems.append(f"{arm}: its seeds are not the baseline's")
        network = record.network
        for run in record.runs:
            reference = references.get(run.seed)
            if reference is None:
                continue  # reported above, or the seed is not the baseline's
            problems += _run_problems(arm, run)
            compared = _identity(
                problems, f"{arm} seed {run.seed}",
                member_sha256=member_sha256, timeline_id=network.signal_program.timeline_id,
                arm=arm, seed=run.seed, demand_sha256=run.demand_sha256,
                population_sha256=(
                    reference.population_sha256
                    if run.demand_sha256 == reference.demand_sha256
                    else None
                ),
                world_model_sha256=baseline.world_model_sha256,  # candidates carry none
                geometry_sha256=network.geometry_sha256, network_sha256=network_sha256(network),
                program_structure_sha256=program_structure_sha256(network.signal_program),
                run_config_sha256=canonical_sha256(command_without_paths(run.command)),
                settings_sha256=settings_pairing_sha256(experiment.settings),
                plan_sha256=plan_sha256(network.signal_program),
                intervention_sha256=record.outcome.proposal_sha256,
            )  # fmt: skip
            if compared is None:
                continue
            found = pairing_problems(reference, compared)
            if found:
                problems.append(f"{arm} seed {run.seed}: " + "; ".join(found))
            else:
                pairs.append(PairIdentity.of(reference, compared))
    if problems:
        raise PairingError(_summary(problems))
    return ExperimentPairing(pairs=tuple(pairs), unverified=tuple(unverified))
