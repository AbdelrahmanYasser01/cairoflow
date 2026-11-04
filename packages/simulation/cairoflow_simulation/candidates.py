"""Candidate networks from accepted proposals, run on the baseline's seeds and demand files.

Fairness (ARCHITECTURE.md, ADR-008): a candidate differs from the baseline only in the green
durations an accepted `ValidationOutcome` gives. Its nodes, edges and connections are copied from
the baseline and must hash identically; its link order must match; it runs with the baseline's
settings, seeds and demand files, each checked against the hash the baseline recorded.

`run_candidates` writes everything under a new directory and `experiment_manifest.json` there,
never overwriting. Rejected outcomes are listed as skipped, not simulated.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from cairoflow_contracts.experiment import (
    BaselineManifest,
    CandidateRecord,
    ExperimentManifest,
    SignalProgramSpec,
)
from cairoflow_contracts.pair_identity import check_seeds
from cairoflow_contracts.reasoning import ValidationOutcome
from cairoflow_simulation.geometry_compiler import (
    FILES,
    NET_FILE,
    CompiledNetwork,
    embed_program,
)
from cairoflow_simulation.network import content_sha256, read_links
from cairoflow_simulation.runner import MANIFEST_FILE, run_seed
from cairoflow_simulation.validation import compiled_conflicts

EXPERIMENT_FILE = "experiment_manifest.json"
ProgramFor = Callable[
    [ValidationOutcome, CompiledNetwork], tuple[SignalProgramSpec, tuple[str, ...]]
]


class CandidateError(ValueError):
    """An outcome or a baseline cannot support a fair candidate run."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_baseline(baseline_dir: Path) -> tuple[BaselineManifest, CompiledNetwork]:
    """The baseline manifest and its network, checked against the recorded network hash."""
    manifest = BaselineManifest.model_validate_json(
        (baseline_dir / MANIFEST_FILE).read_text(encoding="utf-8")
    )
    net_path = baseline_dir / "network" / NET_FILE
    if content_sha256(net_path, strip_comments=True) != manifest.network.net_sha256:
        raise CandidateError("the baseline network changed after the baseline was run")
    network = CompiledNetwork(
        net_path=net_path, links=tuple(read_links(net_path)), manifest=manifest.network
    )
    return manifest, network


def build_candidate_network(
    baseline: CompiledNetwork, outcome: ValidationOutcome, out_dir: Path
) -> CompiledNetwork:
    """The baseline network with the outcome's green durations, in `out_dir` (new)."""
    program = baseline.manifest.signal_program
    durations = outcome.phase_durations_s
    if outcome.status != "accepted" or durations is None:
        raise CandidateError(f"{outcome.intervention_id} was not accepted by the validator")
    if outcome.program_id != program.program_id or len(durations) != len(program.phases):
        raise CandidateError(f"{outcome.intervention_id} was validated against another program")
    candidate = SignalProgramSpec.model_validate(
        program.model_dump()
        | {
            "program_id": f"{program.program_id}_{outcome.intervention_id}",
            "cycle_s": round(sum(durations), 6),
            "phases": [
                phase.model_dump() | {"duration_s": duration}
                for phase, duration in zip(program.phases, durations, strict=True)
            ],
        }
    )
    return embed_candidate_program(baseline, candidate, outcome.intervention_id, out_dir)


def embed_candidate_program(
    baseline: CompiledNetwork,
    candidate: SignalProgramSpec,
    intervention_id: str,
    out_dir: Path,
    notes: tuple[str, ...] = (),
) -> CompiledNetwork:
    """The baseline network with `candidate` as its program, in `out_dir` (new): the baseline's
    nodes, edges and connections, checked to hash identically, and the same link order; only
    phase durations may differ from the baseline program."""
    program = baseline.manifest.signal_program
    if [(p.name, p.state) for p in candidate.phases] != [(p.name, p.state) for p in program.phases]:
        raise CandidateError(f"{intervention_id} changes more than the phase durations")
    out_dir.mkdir(parents=True, exist_ok=False)
    source = baseline.net_path.parent
    for name in FILES[:3]:
        shutil.copyfile(source / name, out_dir / name)
    net_path, warnings = embed_program(out_dir, candidate, baseline.links)
    plain = {name: content_sha256(out_dir / name) for name in FILES}
    for name in FILES[:3]:
        if plain[name] != baseline.manifest.plain_xml_sha256[name]:
            raise CandidateError(f"{name} differs from the baseline's")
    # the file SUMO will run, checked again from its own tlLogic (not the spec it was written
    # from): no phase may give priority green to two foes
    conflicts = compiled_conflicts(net_path, candidate.program_id)
    if conflicts:
        raise CandidateError(f"{intervention_id}: " + "; ".join(conflicts))
    parameters = tuple(
        p.model_copy(
            update={
                "value": candidate.cycle_s,
                "provenance": "synthetic",
                "source": f"validated proposal {intervention_id} (model-proposed timing) "
                f"applied to: {p.source}",
            }
        )
        if p.name == "signal.cycle"
        else p
        for p in baseline.manifest.parameters
    )
    manifest = baseline.manifest.model_copy(
        update={
            "plain_xml_sha256": plain,
            "net_sha256": content_sha256(net_path, strip_comments=True),
            "signal_program": candidate,
            "parameters": parameters,
            "netconvert_warnings": tuple(warnings),
            "notes": (
                *baseline.manifest.notes,
                f"candidate {intervention_id}: green durations from a validated "
                "proposal; nodes, edges, connections and clearance intervals as the baseline",
                *notes,
            ),
        }
    )
    return CompiledNetwork(net_path=net_path, links=baseline.links, manifest=manifest)


def run_candidates(
    baseline_dir: Path,
    outcomes: Sequence[ValidationOutcome],
    out_dir: Path,
    *,
    now: datetime | None = None,
    program_for: ProgramFor | None = None,
    confirmation: bool = False,
) -> ExperimentManifest:
    """Build and run every accepted outcome against the baseline in `baseline_dir`.

    `program_for(outcome, baseline)` gives the candidate's program and notes when the outcome was
    validated against another program than the baseline's (a replay baseline: replay_candidates).
    A baseline on reserved confirmation seeds 301-320 (its settings, demand files or runs) is
    refused before anything runs (ReservedSeedError) unless `confirmation` is set.
    """
    now = now or datetime.now(UTC)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} exists; an experiment is never overwritten")
    manifest, baseline = load_baseline(baseline_dir)
    check_seeds(
        [*manifest.settings.seeds, *(f.seed for f in manifest.demand.files),
         *(r.seed for r in manifest.runs)],
        confirmation=confirmation,
    )  # fmt: skip
    if not manifest.valid:
        raise CandidateError("the baseline is not valid: " + "; ".join(manifest.reasons))
    accepted = [o for o in outcomes if o.status == "accepted"]
    skipped = tuple(
        f"{o.intervention_id}: {'; '.join(o.reasons)}" for o in outcomes if o.status != "accepted"
    )
    if not accepted:
        raise CandidateError("no accepted outcome to simulate")
    ids = [o.intervention_id for o in accepted]
    if len(set(ids)) != len(ids):
        raise CandidateError("intervention ids must be unique")
    demand_dir = baseline_dir / "demand"
    for file in manifest.demand.files:
        if _sha256_file(demand_dir / file.file) != file.sha256:
            raise CandidateError(f"demand file {file.file} changed after the baseline was run")

    out_dir.mkdir(parents=True)
    records: list[CandidateRecord] = []
    for outcome in accepted:
        candidate_dir = out_dir / "candidates" / outcome.intervention_id
        if program_for is None:
            network = build_candidate_network(baseline, outcome, candidate_dir / "network")
        else:
            program, notes = program_for(outcome, baseline)
            network = embed_candidate_program(
                baseline, program, outcome.intervention_id, candidate_dir / "network", notes
            )
        runs = tuple(
            run_seed(
                network,
                demand_dir / file.file,
                file.seed,
                manifest.settings,
                candidate_dir / "runs" / f"seed_{file.seed}",
            )
            for file in manifest.demand.files
        )
        reasons = tuple(f"seed {r.seed}: {'; '.join(r.reasons)}" for r in runs if not r.valid)
        records.append(
            CandidateRecord(
                intervention_id=outcome.intervention_id,
                outcome=outcome,
                network=network.manifest,
                runs=runs,
                valid=not reasons,
                reasons=reasons,
            )
        )
    reasons = tuple(
        f"{record.intervention_id}: {reason}" for record in records for reason in record.reasons
    )
    experiment = ExperimentManifest(
        experiment_id=f"experiment-{manifest.experiment_id}-{now:%Y%m%dT%H%M%SZ}",
        created_at_utc=now,
        baseline_dir=os.path.relpath(baseline_dir, out_dir),
        baseline_experiment_id=manifest.experiment_id,
        baseline_manifest_sha256=_sha256_file(baseline_dir / MANIFEST_FILE),
        settings=manifest.settings,
        candidates=tuple(records),
        skipped=skipped,
        valid=not reasons,
        reasons=reasons,
    )
    with (out_dir / EXPERIMENT_FILE).open("x", encoding="utf-8") as handle:
        handle.write(experiment.model_dump_json(indent=2) + "\n")
    return experiment
