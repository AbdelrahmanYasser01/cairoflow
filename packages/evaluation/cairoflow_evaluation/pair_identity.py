"""An experiment's pair identity view, read from its records (ADR-033).

`experiment_pair_view` opens an experiment directory, checks that the baseline manifest is the
one the experiment cites, pairs every candidate run with the baseline run of its seed
(`pairing.experiment_pairs`, with the populations required), re-hashes every seed's route file
against the hash the baseline recorded, and reads the SUMO binary version from the header of each
run's tripinfo.xml (the hashes in the records strip XML comments, so the header is recorded but not
hashed). `pair_identity_view_from_record` starts from a committed experiment record instead
(experiments/records/*.json) and also checks what the record cites.

Nothing is written and no record gains a field: the view is a sidecar (contracts.pair_identity).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path

from cairoflow_contracts.experiment import (
    BaselineManifest,
    DemandFile,
    ExperimentManifest,
    RunRecord,
)
from cairoflow_contracts.pair_identity import (
    BASELINE_ARM,
    ArmRuns,
    PairIdentityView,
    SeedDemand,
    build_view,
    check_seeds,
)
from cairoflow_contracts.pairing import PairingError, plan_sha256
from cairoflow_evaluation.compare import BASELINE_FILE, EXPERIMENT_FILE
from cairoflow_evaluation.pairing import DEMAND_DIR, experiment_pairs, network_sha256
from cairoflow_simulation.population import PopulationError, population_sha256, speed_factors_sha256

OUTPUT_FILE = "tripinfo.xml"
HEADER_BYTES = 4096
# "<!-- generated on <time> by Eclipse SUMO sumo 1.28.0" (older builds write "... sumo Version 1.x")
_HEADER_VERSION = re.compile(r"by Eclipse SUMO \S+ (?:Version )?v?(\d\S*)")


def sumo_binary_version(output_file: Path) -> str | None:
    """The SUMO version the output file's header comment names, or None if it names none."""
    with output_file.open("rb") as handle:
        head = handle.read(HEADER_BYTES).decode("utf-8", errors="replace")
    found = _HEADER_VERSION.search(head)
    return found.group(1) if found else None


def _arm_version(arm: str, runs: Sequence[RunRecord], runs_dir: Path) -> str | None:
    versions: set[str | None] = set()
    for run in runs:
        path = runs_dir / run.run_dir / OUTPUT_FILE
        if not path.is_file():
            raise PairingError(f"{arm} seed {run.seed}: {OUTPUT_FILE} is missing")
        versions.add(sumo_binary_version(path))
    if len(versions) > 1:
        shown = ", ".join(sorted(str(v) for v in versions))
        raise PairingError(f"{arm}: its runs were written by different SUMO binaries ({shown})")
    return versions.pop() if versions else None


def _seed_demand(baseline_dir: Path, file: DemandFile) -> SeedDemand:
    path = baseline_dir / DEMAND_DIR / file.file
    if not path.is_file():
        raise PairingError(f"seed {file.seed}: demand file {file.file} is missing")
    if hashlib.sha256(path.read_bytes()).hexdigest() != file.sha256:
        raise PairingError(
            f"seed {file.seed}: demand file {file.file} no longer hashes to the recorded "
            f"{file.sha256[:12]}"
        )
    try:
        population, speeds = population_sha256(path), speed_factors_sha256(path)
    except PopulationError as error:
        raise PairingError(str(error)) from error
    return SeedDemand(
        seed=file.seed,
        demand_file=file.file,
        demand_sha256=file.sha256,
        population_sha256=population,
        speed_factors_sha256=speeds,
    )


def _read(experiment_dir: Path, confirmation: bool) -> tuple[PairIdentityView, Path]:
    experiment = ExperimentManifest.model_validate_json(
        (experiment_dir / EXPERIMENT_FILE).read_text(encoding="utf-8")
    )
    baseline_dir = (experiment_dir / experiment.baseline_dir).resolve()
    raw = (baseline_dir / BASELINE_FILE).read_bytes()
    member = hashlib.sha256(raw).hexdigest()
    if member != experiment.baseline_manifest_sha256:
        raise PairingError("the baseline manifest is not the one the experiment cites")
    baseline = BaselineManifest.model_validate_json(raw)
    if baseline.experiment_id != experiment.baseline_experiment_id:
        raise PairingError("the baseline is not the experiment's baseline experiment")
    check_seeds(
        [*baseline.settings.seeds, *experiment.settings.seeds, *(r.seed for r in baseline.runs)],
        confirmation=confirmation,
    )
    pairing = experiment_pairs(experiment, baseline, baseline_dir, member, require_populations=True)
    root = experiment_dir.resolve()
    arms = [
        ArmRuns(
            arm=BASELINE_ARM,
            plan_sha256=plan_sha256(baseline.network.signal_program),
            intervention_sha256=None,
            network_sha256=network_sha256(baseline.network),
            sumo_binary_version=_arm_version(BASELINE_ARM, baseline.runs, baseline_dir / "runs"),
            seeds=tuple(sorted(run.seed for run in baseline.runs)),
        )
    ]
    for record in experiment.candidates:
        runs_dir = root / "candidates" / record.intervention_id / "runs"
        arms.append(
            ArmRuns(
                arm=record.intervention_id,
                plan_sha256=plan_sha256(record.network.signal_program),
                intervention_sha256=record.outcome.proposal_sha256,
                network_sha256=network_sha256(record.network),
                sumo_binary_version=_arm_version(record.intervention_id, record.runs, runs_dir),
                seeds=tuple(sorted(run.seed for run in record.runs)),
            )
        )
    view = build_view(
        experiment_id=experiment.experiment_id,
        baseline_experiment_id=baseline.experiment_id,
        member_sha256=member,
        timeline_id=baseline.network.signal_program.timeline_id,
        world_model_sha256=baseline.world_model_sha256,
        network_sha256=network_sha256(baseline.network),
        horizon_s=baseline.settings.horizon_s,
        sumo_wheel_version=baseline.network.sumo_version,
        seeds=[_seed_demand(baseline_dir, file) for file in baseline.demand.files],
        arms=arms,
        pairs=pairing.pairs,
        confirmation=confirmation,
    )
    return view, baseline_dir


def experiment_pair_view(experiment_dir: Path, *, confirmation: bool = False) -> PairIdentityView:
    """The view of the experiment in `experiment_dir`, or a PairingError saying why not."""
    return _read(experiment_dir, confirmation)[0]


def pair_identity_view_from_record(
    record_path: Path, repo: Path, *, confirmation: bool = False
) -> PairIdentityView:
    """The view of a committed experiment record's run (`run_dir`/experiment, relative to the
    repository), checked against the baseline manifest hash, baseline and seeds the record cites."""
    record = json.loads(record_path.read_text(encoding="utf-8"))
    view, baseline_dir = _read(repo / record["run_dir"] / "experiment", confirmation)
    cited = str(record.get("baseline_manifest_sha256"))
    if cited != view.member_sha256:
        raise PairingError(
            f"the record cites baseline manifest {cited[:12]}, the experiment's is "
            f"{view.member_sha256[:12]}"
        )
    if "baseline_dir" in record and (repo / record["baseline_dir"]).resolve() != baseline_dir:
        raise PairingError("the record's baseline_dir is not the experiment's baseline")
    if sorted(record.get("seeds", ())) != [s.seed for s in view.seeds]:
        raise PairingError("the record's seeds are not the experiment's")
    return view
