"""Held-out evaluation of the frozen D1 (EQ-1) and D2 plans on seeds 201-220 (ADR-033).

Two steps, so the plans are frozen before any held-out run:

1. `preregister D2_PLAN_RECORD` writes experiments/records/ladder-preregistration-<stamp>.json:
   the D1 plan (cairoflow_simulation.eq1 on the committed inputs, its full record, assumption
   (e) included) and the D2 plan (the frozen record of scripts/run_d2_search_experiment.py),
   each with its `pairing.plan_sha256` and its validator verdict on the cut network; the seeds
   201-220; the members; Holm's family (the improvement metrics of `significance`, alpha
   `significance.FAMILY_ALPHA`, fixed per member by the experiment as `compare` fixes it); the
   rules and ensemble-rule versions; and `record_sha256` over the record. Commit it.
2. `run PREREGISTRATION_RECORD` refuses unless that record is committed and unchanged since
   (`git ls-files` and `git diff HEAD`), its `record_sha256` holds, and both plan hashes are
   re-derived equal. It then builds every member's replay baseline on seeds 201-220 (as the D2
   search builds them), runs D1 as I1 and D2 as I2, evaluates each member with the experiment
   rules, combines the members with the pre-registered ensemble rule (`ensemble.ensemble_verdict`)
   and writes experiments/records/ladder-evaluation-<stamp>.json.

Nothing is tuned on these seeds: the plans are read from the committed record. A failure is
recorded as it is; a new attempt is a new record. No model call.

    uv run --frozen python scripts/run_ladder_evaluation_experiment.py preregister RECORD
    uv run --frozen python scripts/run_ladder_evaluation_experiment.py run RECORD
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cairoflow_contracts.evidence import IntersectionGeometry
from cairoflow_contracts.experiment import SignalProgramSpec
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.pairing import plan_sha256
from cairoflow_contracts.reasoning import InterventionProposal, PhaseChange, ValidationOutcome
from cairoflow_evaluation.compare import EVALUATION_FILE, evaluate_experiment
from cairoflow_evaluation.d2_selection import program_with
from cairoflow_evaluation.eligibility import RULES_VERSION
from cairoflow_evaluation.ensemble import (
    ENSEMBLE_RULE_VERSION,
    MemberEvidence,
    ensemble_verdict,
    phase_changes_from_notes,
)
from cairoflow_evaluation.report import candidate_standings
from cairoflow_evaluation.significance import FAMILY_ALPHA, IMPROVEMENT_METRICS
from cairoflow_simulation import eq1
from cairoflow_simulation.candidates import EXPERIMENT_FILE, load_baseline, run_candidates
from cairoflow_simulation.d2_moves import EVIDENCE_ID, VALIDATED_AT
from cairoflow_simulation.replay_candidates import replay_program_for
from cairoflow_simulation.runner import MANIFEST_FILE
from cairoflow_simulation.validation import validate_proposal

REPO = Path(__file__).resolve().parents[1]
RECORDS = REPO / "experiments/records"
HELD_OUT_SEEDS = tuple(range(201, 221))
GEOMETRY = REPO / "data/geometry/ngsim-lankershim-int2/geometry.json"
ARM_IDS = {"D1": "I1", "D2": "I2"}


def search_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "run_d2_search_experiment", REPO / "scripts/run_d2_search_experiment.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checked_record(path: Path) -> dict[str, Any]:
    """A record whose `record_sha256` hashes the rest of it."""
    record = json.loads(path.read_text(encoding="utf-8"))
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if record.get("record_sha256") != canonical_sha256(body):
        raise ValueError(f"{path.name}: record_sha256 does not hash the record")
    return record


def committed_unchanged(path: Path) -> None:
    rel = path.resolve().relative_to(REPO).as_posix()
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", rel], cwd=REPO,
                             capture_output=True)  # fmt: skip
    if tracked.returncode != 0:
        raise ValueError(f"{rel} is not committed: commit the pre-registration before running")
    changed = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=REPO)
    if changed.returncode != 0:
        raise ValueError(f"{rel} differs from the committed version")


def d1_plan(
    cut: SignalProgramSpec, version: str = eq1.EQ1_VERSION
) -> tuple[dict[str, Any], tuple[float, ...]]:
    scenario = json.loads(search_script().load_script("d2_move_family").SCENARIO.read_text("utf-8"))
    geometry = IntersectionGeometry.model_validate_json(GEOMETRY.read_text(encoding="utf-8"))
    inputs = eq1.inputs_from_records(cut, scenario, geometry, version=version)
    result = eq1.allocate(inputs)
    return eq1.record(inputs, result), tuple(float(d) for d in result.durations)


def outcome_for(arm: str, durations: tuple[float, ...], cut_network, bundle) -> ValidationOutcome:
    """The plan as a validated proposal on the cut network (the validator decides, not us)."""
    cut = cut_network.manifest.signal_program
    changes = tuple(
        PhaseChange(phase_name=p.name, green_s=d)
        for p, d in zip(cut.phases, durations, strict=True)
        if d != p.duration_s
    )
    proposal = InterventionProposal(
        intervention_id=ARM_IDS[arm], type="signal_timing", target_program=cut.program_id,
        phase_changes=changes, rationale=f"{arm} plan, frozen before seeds 201-220",
        expected_tradeoffs=(), preconditions=(), addresses_hypothesis_ids=(),
        evidence_ids=(EVIDENCE_ID,),
    )  # fmt: skip
    return validate_proposal(proposal, cut_network, bundle, now=VALIDATED_AT)


def load_cut(search):
    d2script = search.load_script("d2_move_family")
    scenario = json.loads(d2script.SCENARIO.read_text(encoding="utf-8"))
    _, cut_network = load_baseline(REPO / scenario["baseline"]["run_dir"])
    bundle = d2script.bundle_of(d2script.MUSE)
    ensemble = json.loads(d2script.ENSEMBLE.read_text(encoding="utf-8"))
    return d2script, cut_network, bundle, ensemble


def previous_evaluations() -> list[str]:
    """Held-out evaluations already on record: their seeds have been used before."""
    return sorted(p.name for p in RECORDS.glob("ladder-evaluation-*.json"))


def preregister(d2_plan_path: Path, eq1_version: str = eq1.EQ1_VERSION) -> int:
    search = search_script()
    d2script, cut_network, bundle, ensemble = load_cut(search)
    cut = cut_network.manifest.signal_program
    d2_plan = checked_record(d2_plan_path)
    d1_record, d1_durations = d1_plan(cut, eq1_version)
    arms: dict[str, Any] = {}
    d1_outcome = outcome_for("D1", d1_durations, cut_network, bundle)
    arms["D1"] = {
        "intervention_id": ARM_IDS["D1"],
        "source": f"cairoflow_simulation.eq1 {eq1_version} on the committed inputs",
        "durations_s": list(d1_durations),
        "plan_sha256": plan_sha256(program_with(cut, d1_durations)),
        "eq1_record": d1_record,
        "validator": {"status": d1_outcome.status, "reasons": list(d1_outcome.reasons)},
    }
    if d2_plan["outcome"] == "selected":
        program = SignalProgramSpec.model_validate(d2_plan["program"])
        d2_durations = tuple(p.duration_s for p in program.phases)
        if plan_sha256(program) != d2_plan["plan_sha256"]:
            raise ValueError("the D2 plan record's program does not hash to its plan_sha256")
        d2_outcome = outcome_for("D2", d2_durations, cut_network, bundle)
        arms["D2"] = {
            "intervention_id": ARM_IDS["D2"],
            "source": f"experiments/records/{d2_plan_path.name}",
            "plan_record_sha256": search.sha256_file(d2_plan_path),
            "label": d2_plan["label"],
            "durations_s": list(d2_durations),
            "plan_sha256": d2_plan["plan_sha256"],
            "validator": {"status": d2_outcome.status, "reasons": list(d2_outcome.reasons)},
        }
    else:
        arms["D2"] = {"reported": "not_testable", "source": d2_plan_path.name}
    for arm, spec in arms.items():
        if "validator" in spec and spec["validator"]["status"] != "accepted":
            raise ValueError(f"{arm} is not accepted by the validator: {spec['validator']}")
    now = datetime.now(UTC).replace(microsecond=0)
    record = {
        "script": "scripts/run_ladder_evaluation_experiment.py preregister",
        "created_at_utc": now.isoformat(),
        "label": "pre-registration",
        "arms": arms,
        "seeds": list(HELD_OUT_SEEDS),
        "seed_role": "held-out confirmation (ADR-033 decision 5); 301-320 stay reserved",
        "members": [
            {"id": m["id"], "version": m["version"], "required": m["required"]}
            for m in ensemble["members"]
        ],  # fmt: skip
        "ensemble_record_sha256": search.sha256_file(d2script.ENSEMBLE),
        "family": {
            "improvement_metrics": list(IMPROVEMENT_METRICS),
            "alpha": FAMILY_ALPHA,
            "scope": "per member: every tested arm x every improvement metric, fixed when the "
            "member's experiment manifest is written (compare.improvement_plan, ADR-033 #4)",
        },
        "rules_version": RULES_VERSION,
        "ensemble_rule_version": ENSEMBLE_RULE_VERSION,
        "r_arm": "not defined in any surviving file (ADR-033 decision 8); not an arm here",
        "seeds_previously_used_by": previous_evaluations(),
        "git": search.git_state(),
    }
    record["record_sha256"] = canonical_sha256(record)
    path = RECORDS / f"ladder-preregistration-{now:%Y%m%dT%H%M%SZ}.json"
    search.write_new(path, record)
    print(f"wrote {path.relative_to(REPO)}; commit it before `run`")
    return 0


def _comparisons(evaluation, candidate: str) -> list[dict[str, Any]]:
    return [
        {
            "metric": c.metric_name,
            "mean_difference": c.mean_difference,
            "ci95_difference": c.ci95_difference,
            "verdict": c.verdict,
        }
        for c in evaluation.comparisons
        if c.candidate == candidate
    ]


def run(prereg_path: Path) -> int:
    search = search_script()
    committed_unchanged(prereg_path)
    prereg = checked_record(prereg_path)
    seeds = tuple(prereg["seeds"])
    search.check_seeds(seeds, HELD_OUT_SEEDS, "held-out")
    d2script, cut_network, bundle, ensemble = load_cut(search)
    cut = cut_network.manifest.signal_program
    _, d1_durations = d1_plan(cut, prereg["arms"]["D1"]["eq1_record"]["algorithm"])
    if plan_sha256(program_with(cut, d1_durations)) != prereg["arms"]["D1"]["plan_sha256"]:
        raise ValueError("D1 no longer derives the pre-registered plan")
    outcomes = [outcome_for("D1", d1_durations, cut_network, bundle)]
    if "plan_sha256" in prereg["arms"]["D2"]:
        d2_durations = tuple(prereg["arms"]["D2"]["durations_s"])
        if plan_sha256(program_with(cut, d2_durations)) != prereg["arms"]["D2"]["plan_sha256"]:
            raise ValueError("the D2 durations do not hash to the pre-registered plan")
        outcomes.append(outcome_for("D2", d2_durations, cut_network, bundle))
    if any(o.status != "accepted" for o in outcomes):
        raise ValueError("a pre-registered plan is no longer accepted by the validator")
    real = search.load_script("build_ngsim_int2_realism")
    now = datetime.now(UTC).replace(microsecond=0)
    stamp = f"{now:%Y%m%dT%H%M%SZ}"
    out = REPO / "experiments/runs" / f"ladder-evaluation-{stamp}"
    record_path = RECORDS / f"ladder-evaluation-{stamp}.json"
    if record_path.exists():
        raise ValueError(f"refusing to overwrite {record_path.name}")
    git = search.git_state()
    out.mkdir(parents=True)
    program_for = replay_program_for(cut)
    evidences, members = [], {}
    sumo = None
    for spec in ensemble["members"]:
        mid = spec["id"]
        baseline_dir = out / mid / "baseline"
        baseline = search.build_member_baseline(real, spec["version"], seeds, baseline_dir, now)
        sumo = baseline.network.sumo_version
        problems = search.same_member(baseline, REPO / spec["baseline_dir"])
        if problems:
            raise ValueError(f"member {mid}: not the recorded member: {'; '.join(problems)}")
        experiment_dir = out / mid / "experiment"
        experiment = run_candidates(baseline_dir, outcomes, experiment_dir, now=now,
                                    program_for=program_for)  # fmt: skip
        evaluation = evaluate_experiment(experiment_dir)
        notes = [n for c in experiment.candidates for n in c.network.notes]
        evidences.append(MemberEvidence(
            member=mid, required=bool(spec["required"]), planned_seeds=seeds,
            evaluation=evaluation, phase_changes=phase_changes_from_notes(notes),
            baseline_assessment="not_assessed",
        ))  # fmt: skip
        standings = candidate_standings(evaluation)
        members[mid] = {
            "version": spec["version"],
            "baseline_dir": baseline_dir.relative_to(REPO).as_posix(),
            "baseline_manifest_sha256": search.sha256_file(baseline_dir / MANIFEST_FILE),
            "experiment_manifest_sha256": search.sha256_file(experiment_dir / EXPERIMENT_FILE),
            "evaluation_sha256": search.sha256_file(experiment_dir / EVALUATION_FILE),
            "arms": {
                arm: {
                    "status": standings[iid].status,
                    "delay_difference_s": standings[iid].delay_difference,
                    "worsens": list(standings[iid].worsens),
                    "confirmed": list(standings[iid].confirmed),
                    "held_off": list(standings[iid].held_off),
                    "comparisons": _comparisons(evaluation, iid),
                }
                for arm, iid in ARM_IDS.items()
                if iid in standings
            },
        }
        print(f"  {mid}: done", flush=True)
    verdict = ensemble_verdict(evidences)
    result = {
        "script": "scripts/run_ladder_evaluation_experiment.py run",
        "created_at_utc": now.isoformat(),
        "label": "simulated",
        "preregistration": prereg_path.name,
        "preregistration_sha256": search.sha256_file(prereg_path),
        "seeds": list(seeds),
        "arm_ids": ARM_IDS,
        "members": members,
        "ensemble": json.loads(verdict.model_dump_json()),
        "baseline_assessment": "not_assessed: the held-out baselines have no assessment, so no "
        "selection can become a recommendation",
        "software": {"sumo": sumo, "python": sys.version.split()[0]},
        "git": git,
        "run_dir": out.relative_to(REPO).as_posix(),
    }
    result["record_sha256"] = canonical_sha256(result)
    search.write_new(record_path, result)
    for c in verdict.candidates:
        arm = next((a for a, i in ARM_IDS.items() if i == c.candidate), c.candidate)
        print(f"{arm}: {c.verdict} ({c.reason})")
    print(f"wrote {record_path.relative_to(REPO)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("preregister", "run"))
    parser.add_argument("record", type=Path)
    parser.add_argument("--eq1-version", choices=tuple(eq1.VERSIONS), default=eq1.EQ1_VERSION,
                        help="preregister: the EQ-1 version that defines the D1 arm")  # fmt: skip
    args = parser.parse_args(argv)
    try:
        if args.step == "preregister":
            return preregister(args.record, args.eq1_version)
        return run(args.record)
    except (OSError, ValueError, KeyError) as error:
        print(f"cannot {args.step}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
