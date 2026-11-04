"""D2 search (ADR-033 decision 8): every menu move on every timeline member, seeds 101-108.

For each member of the timeline ensemble record (T1-T4) a replay baseline is built exactly as
scripts/build_ngsim_int2_realism.py builds one (same inputs, timeline version, window, drain and
compile options, the same admissibility gate), on the search seeds only, and checked to be the
recorded member's network and settings apart from the seeds. Every move of the approved menu
(cairoflow_simulation.d2_moves, `menu_sha256` pinned below) runs on it as candidate I1-I80 through
`run_candidates`; `evaluate_experiment` and `report.candidate_standings` judge it; the approved
rule (cairoflow_evaluation.d2_selection) selects. Output, all new files (nothing is overwritten):

- experiments/runs/d2-search-<stamp>/ (Git-ignored): member baselines, experiments, evaluations;
- experiments/records/d2-search-<stamp>.json: every member's standing of every move, the
  selection, hashes of every input and output, the git state and software versions;
- experiments/records/d2-plan-<stamp>.json: the frozen plan (or `not_testable`), its
  `plan_sha256` (pairing.plan_sha256) and a `record_sha256` over the record itself.

Seeds 101-108 are the D2 search seeds (ADR-033 decision 5); any other seed is refused. No model
call. `--dry-run` prints the work and writes nothing.

    uv run --frozen python scripts/run_d2_search_experiment.py [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from cairoflow_contracts.experiment import BaselineManifest, RunSettings
from cairoflow_contracts.integrity import canonical_sha256
from cairoflow_contracts.reasoning import ValidationOutcome
from cairoflow_evaluation.compare import EVALUATION_FILE, evaluate_experiment
from cairoflow_evaluation.d2_selection import program_with, select_plan, selection_record
from cairoflow_evaluation.report import candidate_standings
from cairoflow_simulation.candidates import EXPERIMENT_FILE, load_baseline, run_candidates
from cairoflow_simulation.d2_moves import (
    MOVES_VERSION,
    VALIDATED_AT,
    AcceptedMove,
    fit_members,
    menu_sha256,
    screen_moves,
)
from cairoflow_simulation.geometry_compiler import CompileOptions, ReplayOptions, known_window
from cairoflow_simulation.replay_candidates import replay_program_for
from cairoflow_simulation.runner import MANIFEST_FILE, run_baseline
from cairoflow_simulation.validation import validate_proposal

REPO = Path(__file__).resolve().parents[1]
RECORDS = REPO / "experiments/records"
SEARCH_SEEDS = tuple(range(101, 109))
APPROVED_MENU_SHA256 = "709bc2cfcc6ebe3f56ad264c3c65608b34561c8f86989e873ff2e612c09ff10e"


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_state() -> dict[str, Any]:
    """The commit and whether the tree differs from it, so a reader knows what code ran."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout

    changed = sorted(line[3:] for line in git("status", "--porcelain").splitlines())
    return {
        "head": git("rev-parse", "HEAD").strip(),
        "branch": git("branch", "--show-current").strip(),
        "dirty_paths": len(changed),
        "tracked_diff_sha256": hashlib.sha256(git("diff", "HEAD", "--binary").encode()).hexdigest(),
        "changed_paths_sha256": canonical_sha256(changed),
    }


def check_seeds(seeds: tuple[int, ...], allowed: tuple[int, ...], role: str) -> None:
    if not seeds or not set(seeds) <= set(allowed):
        raise ValueError(f"{role} seeds must be within {allowed[0]}-{allowed[-1]}: got {seeds}")


def build_member_baseline(
    real: ModuleType, version: str, seeds: tuple[int, ...], out: Path, now: datetime
) -> BaselineManifest:
    """A member's replay baseline on `seeds`, as build_ngsim_int2_realism.main builds one."""
    geometry, reference, world, scenario = real.load_inputs()
    timeline = reference if version == "v1" else real.load_timeline_version(scenario, version)[0]
    window = known_window(timeline)
    settings = RunSettings(
        seeds=seeds, warmup_s=0, horizon_s=window[1] - window[0], drain_s=real.DRAIN_S
    )
    replay = ReplayOptions(window_s=window, drain_s=real.DRAIN_S)
    options = CompileOptions.model_validate(scenario["baseline"]["compile_options"])
    problems = real.admissibility(geometry, timeline, reference, options, replay)
    if problems:
        raise ValueError(f"member {version}: admissibility failed: {'; '.join(problems)}")
    return run_baseline(world, geometry, timeline, out, settings, options, replay=replay, now=now)


def same_member(new: BaselineManifest, recorded_dir: Path) -> list[str]:
    """Differences from the recorded member baseline other than the seeds (empty = same)."""
    recorded = BaselineManifest.model_validate_json((recorded_dir / MANIFEST_FILE).read_bytes())
    problems = []
    if new.network.net_sha256 != recorded.network.net_sha256:
        problems.append("compiled network differs")
    if new.network.signal_program != recorded.network.signal_program:
        problems.append("signal program differs")
    if new.world_model_sha256 != recorded.world_model_sha256:
        problems.append("world model differs")
    if new.settings.model_dump(exclude={"seeds"}) != recorded.settings.model_dump(
        exclude={"seeds"}
    ):
        problems.append("run settings differ apart from the seeds")
    return problems


def menu_outcomes(
    cut_network, bundle, replays
) -> tuple[tuple[AcceptedMove, ...], tuple[ValidationOutcome, ...], dict[str, str]]:
    """The approved menu and one validated outcome per move, ids I1.. in menu order."""
    screen = screen_moves(cut_network, bundle)
    _, menu = fit_members(screen, cut_network.manifest.signal_program, replays)
    if menu_sha256(menu) != APPROVED_MENU_SHA256:
        raise ValueError(f"the menu is not the approved one ({menu_sha256(menu)})")
    program_id = cut_network.manifest.signal_program.program_id
    outcomes, labels = [], {}
    for n, accepted in enumerate(menu, start=1):
        iid = f"I{n}"
        proposal = accepted.move.proposal(program_id, iid)
        outcome = validate_proposal(proposal, cut_network, bundle, now=VALIDATED_AT)
        if outcome.status != "accepted" or outcome.phase_durations_s != accepted.durations:
            raise ValueError(f"{accepted.move.label} does not validate as it did in the menu")
        outcomes.append(outcome)
        labels[iid] = accepted.move.label
    return menu, tuple(outcomes), labels


def write_new(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, nargs="+", default=list(SEARCH_SEEDS))
    parser.add_argument("--dry-run", action="store_true", help="print the work, write nothing")
    args = parser.parse_args(argv)
    seeds = tuple(args.seeds)
    try:
        check_seeds(seeds, SEARCH_SEEDS, "D2 search")
        d2script = load_script("d2_move_family")
        real = load_script("build_ngsim_int2_realism")
        scenario = json.loads(d2script.SCENARIO.read_text(encoding="utf-8"))
        _, cut_network = load_baseline(REPO / scenario["baseline"]["run_dir"])
        cut = cut_network.manifest.signal_program
        bundle = d2script.bundle_of(d2script.MUSE)
        ensemble = json.loads(d2script.ENSEMBLE.read_text(encoding="utf-8"))
        members = [(m["id"], m["version"], REPO / m["baseline_dir"]) for m in ensemble["members"]]
        replays = {mid: load_baseline(d)[1].manifest.signal_program for mid, _, d in members}
        menu, outcomes, labels = menu_outcomes(cut_network, bundle, replays)
    except (OSError, ValueError, KeyError) as error:
        print(f"cannot start the D2 search: {error}", file=sys.stderr)
        return 2
    runs = len(members) * len(seeds) * (len(outcomes) + 1)
    print(
        f"D2 search: {len(members)} members x {len(seeds)} seeds x ({len(outcomes)} moves + "
        f"baseline) = {runs} SUMO runs plus baseline reruns; menu {APPROVED_MENU_SHA256[:12]}"
    )
    if args.dry_run:
        return 0

    now = datetime.now(UTC).replace(microsecond=0)
    stamp = f"{now:%Y%m%dT%H%M%SZ}"
    out = REPO / "experiments/runs" / f"d2-search-{stamp}"
    record_path = RECORDS / f"d2-search-{stamp}.json"
    plan_path = RECORDS / f"d2-plan-{stamp}.json"
    if record_path.exists() or plan_path.exists():
        print(f"refusing to overwrite {record_path.name}", file=sys.stderr)
        return 2
    git = git_state()
    out.mkdir(parents=True)
    program_for = replay_program_for(cut)
    standings, member_records = {}, {}
    baseline = None
    for mid, version, recorded_dir in members:
        started = time.monotonic()
        baseline_dir = out / mid / "baseline"
        baseline = build_member_baseline(real, version, seeds, baseline_dir, now)
        problems = same_member(baseline, recorded_dir)
        if problems:
            print(f"member {mid}: not the recorded member: {'; '.join(problems)}", file=sys.stderr)
            return 1
        experiment_dir = out / mid / "experiment"
        run_candidates(baseline_dir, outcomes, experiment_dir, now=now, program_for=program_for)
        evaluation = evaluate_experiment(experiment_dir)
        by_id = candidate_standings(evaluation)
        standings[mid] = {labels[iid]: row for iid, row in by_id.items() if iid in labels}
        member_records[mid] = {
            "version": version,
            "recorded_member_baseline": recorded_dir.relative_to(REPO).as_posix(),
            "baseline_dir": baseline_dir.relative_to(REPO).as_posix(),
            "baseline_manifest_sha256": sha256_file(baseline_dir / MANIFEST_FILE),
            "experiment_manifest_sha256": sha256_file(experiment_dir / EXPERIMENT_FILE),
            "evaluation_sha256": sha256_file(experiment_dir / EVALUATION_FILE),
            "rules_version": evaluation.rules_version,
            "wall_s": round(time.monotonic() - started, 1),
        }
        print(f"  {mid} ({version}): {member_records[mid]['wall_s']} s", flush=True)
    for mid, rows in standings.items():
        if set(rows) != set(labels.values()):
            print(f"member {mid}: standings do not cover the menu", file=sys.stderr)
            return 1
    selection = select_plan(menu, cut, standings, expected_members=[m for m, _, _ in members])
    selection_body = selection_record(selection, menu, cut)
    search = {
        "script": "scripts/run_d2_search_experiment.py",
        "created_at_utc": now.isoformat(),
        "label": "simulated",
        "seed_role": "D2 search (ADR-033 decision 5): seeds 101-108 only",
        "seeds": list(seeds),
        "moves_version": MOVES_VERSION,
        "menu_sha256": menu_sha256(menu),
        "intervention_ids": labels,
        "inputs": {
            "cut_baseline": scenario["baseline"]["run_dir"],
            "cut_net_sha256": cut_network.manifest.net_sha256,
            "bundle_id": bundle.bundle_id,
            "ensemble_record_sha256": sha256_file(d2script.ENSEMBLE),
            "scenario_record_sha256": sha256_file(d2script.SCENARIO),
        },
        "members": member_records,
        "software": {
            "sumo": baseline.network.sumo_version if baseline else None,
            "python": sys.version.split()[0],
        },
        "git": git,
        "run_dir": out.relative_to(REPO).as_posix(),
        "selection": selection_body,
    }
    search["record_sha256"] = canonical_sha256(search)
    write_new(record_path, search)
    chosen = selection.selected
    plan: dict[str, Any] = {
        "script": "scripts/run_d2_search_experiment.py",
        "arm": "D2",
        "rule": selection_body["rule"],
        "outcome": selection.outcome,
        "reported": selection.reported,
        "search_record": record_path.name,
        "search_record_sha256": sha256_file(record_path),
        "selection_record_sha256": selection_body["record_sha256"],
        "label": None if chosen is None else chosen.label,
        "program": None
        if chosen is None
        else program_with(cut, chosen.durations).model_dump(mode="json"),
        "plan_sha256": selection.plan_sha256,
    }
    plan["record_sha256"] = canonical_sha256(plan)
    write_new(plan_path, plan)
    summary = "not_testable" if chosen is None else f"{chosen.label} score {chosen.score:g} s"
    print(f"selection: {selection.outcome}: {summary}; plan {selection.plan_sha256}")
    print(f"wrote {record_path.relative_to(REPO)} and {plan_path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
