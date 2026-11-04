"""Cross-timeline verdicts over the intersection 2 signal-timeline ensemble (ADR-025); no model.

    uv run --frozen python scripts/run_ngsim_int2_timeline_ensemble.py \
        --member T1=EXPERIMENT_DIR --member T2=... --member T3=... --member T4=... [--record]

Each EXPERIMENT_DIR is a directory written by scripts/run_ngsim_int2_realism_experiment.py on the
replay baseline of one member of `data/geometry/ngsim-lankershim-int2/timeline_ensemble.json` (the
pre-registered design). Every member listed there must be given, and nothing else.

Per member, before any verdict (exit 2 on any failure; nothing is written):
- the member's timeline file hashes as the design records, and the baseline's signal program
  replays that timeline (by id);
- the experiment's baseline manifest hashes as the experiment and its summary record;
- the admissibility gate is re-checked from the files (cairoflow_evaluation.timeline_gate): G1
  (the timeline is a committed version, or an ensemble estimate that reproduced versions 1 and 2),
  G2 (no conflicting heads in the timeline or the compiled program), G3 (the design's replay window,
  replayed second by second), G4 (no through or left movement shown red more often than on
  version 1, from the processed world model) and G5 (a valid baseline on the planned seeds);
- the evaluation is recomputed from the runs (cairoflow_evaluation.compare) and must equal
  `evaluation.json`, under the rules version the design names, the same for every member;
- a member whose design lists earlier records must reproduce them: its baseline runs' tripinfo
  hashes and its candidates' tripinfo hashes as recorded (code drift would show here).

Then `cairoflow_evaluation.ensemble.ensemble_verdict` applies the pre-registered rule: each member
on its own, never pooled. Output: summary.json in a new directory
experiments/runs/ngsim-int2-timeline-ensemble-<stamp>/ (Git-ignored), and with `--record` the same
file in experiments/records/ (committed; opened "x"). Every value is `simulated`, except the
timelines (`trajectory-derived`) and the observed crossing counts of G4 (`derived`).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cairoflow_contracts.evidence import SignalTimeline, TrafficWorldModel
from cairoflow_contracts.experiment import EvaluationResult, ExperimentManifest
from cairoflow_evaluation.compare import compute_evaluation
from cairoflow_evaluation.eligibility import RULES_VERSION
from cairoflow_evaluation.ensemble import (
    MemberEvidence,
    ensemble_verdict,
    phase_changes_from_notes,
)
from cairoflow_evaluation.report import candidate_standings
from cairoflow_evaluation.timeline_gate import (
    baseline_problems,
    conflicting_seconds,
    more_red_than,
    priority_conflicts,
    program_seconds_differing,
    red_crossings,
)
from cairoflow_simulation.candidates import CandidateError, load_baseline
from cairoflow_simulation.geometry_compiler import known_window
from cairoflow_simulation.network import content_sha256

REPO = Path(__file__).resolve().parents[1]
# Rule versions used after the design was committed, with why (never silently): the members, the
# gate and the cross-timeline rule are the design's; only the per-member evaluation rules moved.
AMENDMENTS = {
    "0.3.1": (
        "ADR-024 amendment (2026-10-29), after the independent review: entry waits are read from "
        "SUMO's insertion delays (0.3.0 missed about half of them), and holding vehicles off the "
        "network blocks a candidate only when its paired 95% interval of the per-seed excess is "
        "above zero, as for any other harm. Members, gate and cross-timeline rule unchanged."
    ),
}
RECORD_DIR = REPO / "data/geometry/ngsim-lankershim-int2"
DESIGN = RECORD_DIR / "timeline_ensemble.json"
RECORDS_DIR = REPO / "experiments/records"
SCRIPT = "scripts/run_ngsim_int2_timeline_ensemble.py"


class EnsembleInputError(ValueError):
    """An input does not match the design or its records."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise EnsembleInputError(message)


def parse_members(values: list[str]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for value in values:
        member, sep, path = value.partition("=")
        require(bool(sep and member and path), f"--member {value!r}: expected ID=DIRECTORY")
        require(member not in found, f"member {member} given twice")
        found[member] = (REPO / path).resolve() if not Path(path).is_absolute() else Path(path)
    return found


def member_timeline(spec: dict) -> SignalTimeline:
    """The member's timeline, hash-checked against the design (G1: a committed version, or an
    ensemble estimate that reproduced versions 1 and 2 and passed its gate)."""
    path = RECORD_DIR / spec["timeline_file"]
    require(sha256_file(path) == spec["timeline_sha256"], f"{path.name} does not match the design")
    if spec.get("estimate_file"):
        estimate_path = RECORD_DIR / spec["estimate_file"]
        require(sha256_file(estimate_path) == spec["estimate_sha256"],
                f"{estimate_path.name} does not match the design")  # fmt: skip
        estimate = json.loads(estimate_path.read_text(encoding="utf-8"))
        require(estimate["timeline_sha256"] == spec["timeline_sha256"],
                f"{spec['estimate_file']} names another timeline")  # fmt: skip
        if spec["version"] not in ("v1", "v2"):
            require(estimate.get("reproduces_v1") is True and estimate.get("reproduces_v2") is True
                    and estimate.get("gate", {}).get("passed") is True,
                    f"{spec['estimate_file']}: G1 or the timeline gate did not pass")  # fmt: skip
    timeline = SignalTimeline.model_validate_json(path.read_text(encoding="utf-8"))
    require(timeline.timeline_id == spec["timeline_id"], f"{path.name}: another timeline id")
    return timeline


def comparison_row(c) -> dict[str, object]:
    """A paired comparison without its per-seed values (they stay in evaluation.json)."""
    return {
        "candidate": c.candidate, "metric_name": c.metric_name, "unit": c.unit,
        "better": c.better, "n": len(c.seeds), "baseline_mean": c.baseline_mean,
        "candidate_mean": c.candidate_mean, "mean_difference": c.mean_difference,
        "sd_difference": c.sd_difference, "ci95_difference": c.ci95_difference,
        "verdict": c.verdict, "shared_state_seeds": list(c.shared_state_seeds),
    }  # fmt: skip


def check_member(
    member: str, spec: dict, design: dict, exp_dir: Path, world: TrafficWorldModel,
    reference: SignalTimeline,
) -> tuple[MemberEvidence, dict[str, object]]:  # fmt: skip
    """One member's evidence for the verdict, and its section of the record."""
    timeline = member_timeline(spec)
    summary_path = exp_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    experiment_path = exp_dir / "experiment" / "experiment_manifest.json"
    evaluation_path = exp_dir / "experiment" / "evaluation.json"
    experiment = ExperimentManifest.model_validate_json(experiment_path.read_text())
    evaluation = EvaluationResult.model_validate_json(evaluation_path.read_text())
    baseline_dir = (exp_dir / "experiment" / experiment.baseline_dir).resolve()
    manifest_sha256 = sha256_file(baseline_dir / "baseline_manifest.json")
    require(manifest_sha256 == experiment.baseline_manifest_sha256
            == summary["baseline_manifest_sha256"],
            f"{member}: the baseline manifest changed after the experiment")  # fmt: skip
    try:
        manifest, network = load_baseline(baseline_dir)
    except CandidateError as error:
        raise EnsembleInputError(f"{member}: {error}") from error
    program = manifest.network.signal_program
    require(
        program.timeline_id == timeline.timeline_id,
        f"{member}: the baseline replays {program.timeline_id}, not {timeline.timeline_id}",
    )
    params = {p.name: p.value for p in manifest.network.parameters}
    replay = (params.get("signal.replay_start"), params.get("signal.replay_end"))
    window = tuple(design["replay"]["window_s"])
    seeds = tuple(design["replay"]["seeds"])

    # the admissibility gate, from the files
    red = red_crossings(timeline, world.movements, window)
    gate = {
        "G1_timeline_sha256": spec["timeline_sha256"],
        "G2_conflicting_seconds": conflicting_seconds(timeline),
        "G2_priority_conflicts": priority_conflicts(network),
        "G3_known_window_s": list(known_window(timeline)),
        "G3_replay_window_s": list(replay),
        "G3_program_seconds_differing": program_seconds_differing(program, timeline, window),
        "G4_red_crossings_in_window": {k: list(v) for k, v in red.items()},
        "G4_more_red_than_v1": more_red_than(
            red, red_crossings(reference, world.movements, window)
        ),
        "G5_baseline_problems": baseline_problems(manifest, seeds),
        "label": "timelines trajectory-derived; crossing counts derived; G5 from SUMO run records",
    }
    passed = (
        not gate["G2_conflicting_seconds"] and not gate["G2_priority_conflicts"]
        and tuple(gate["G3_known_window_s"]) == window and tuple(replay) == window
        and gate["G3_program_seconds_differing"] == 0 and not gate["G4_more_red_than_v1"]
        and not gate["G5_baseline_problems"]
    )  # fmt: skip
    require(passed, f"{member} fails the admissibility gate: {json.dumps(gate)[:2000]}")

    # the evaluation follows from the runs, under the design's rules or a disclosed amendment
    allowed = design["evaluation_rules_version"] == RULES_VERSION or RULES_VERSION in AMENDMENTS
    require(evaluation.rules_version == RULES_VERSION and allowed,
            f"{member}: rules {evaluation.rules_version}, the design names "
            f"{design['evaluation_rules_version']}, the code is at {RULES_VERSION}")  # fmt: skip
    require(compute_evaluation(exp_dir / "experiment") == evaluation,
            f"{member}: evaluation.json does not follow from the runs")  # fmt: skip
    require(summary["report"]["trace_problems"] == [], f"{member}: the report has trace problems")

    # earlier records the member must reproduce (versions 1 and 2)
    reproduction: dict[str, object] = {}
    if spec.get("reproduces"):
        recorded = json.loads((REPO / spec["reproduces"]["baseline_record"]).read_text())
        want = {r["seed"]: r["tripinfo_sha256"] for r in recorded["runs"]}
        got = {r.seed: r.tripinfo_sha256 for r in manifest.runs}
        require(want == got, f"{member}: baseline tripinfo differs from {spec['reproduces']}")
        recorded = json.loads((REPO / spec["reproduces"]["experiment_record"]).read_text())
        tripinfo = {
            path.relative_to(exp_dir).as_posix(): content_sha256(path, strip_comments=True)
            for path in sorted((exp_dir / "experiment").rglob("tripinfo.xml"))
        }
        require(tripinfo == recorded["tripinfo"] == summary["tripinfo"],
                f"{member}: candidate tripinfo differs from {spec['reproduces']}")  # fmt: skip
        reproduction = {
            **spec["reproduces"],
            "baseline_runs": "the recorded baseline directory, reused (not re-simulated)",
            "candidate_runs": "re-simulated; tripinfo identical to the record",
        }

    notes = [n for c in experiment.candidates for n in c.network.notes]
    changes = phase_changes_from_notes(notes)
    assessment = summary["report"]["baseline_assessment"]["status"]
    evidence = MemberEvidence(
        member=member, required=bool(spec["required"]), planned_seeds=seeds,
        evaluation=evaluation, phase_changes=changes, baseline_assessment=assessment,
    )  # fmt: skip
    standings = candidate_standings(evaluation)
    section = {
        "id": member,
        "version": spec["version"],
        "required": spec["required"],
        "rules": {k: spec[k] for k in ("permissive_left_evidence", "yellow_from_last_crossing")},
        "timeline": {
            "file": f"data/geometry/ngsim-lankershim-int2/{spec['timeline_file']}",
            "sha256": spec["timeline_sha256"],
            "timeline_id": timeline.timeline_id,
            "label": timeline.label,
        },  # fmt: skip
        "experiment_dir": rel(exp_dir),
        "experiment_summary_sha256": sha256_file(summary_path),
        "evaluation_json_sha256": sha256_file(evaluation_path),
        "baseline_dir": rel(baseline_dir),
        "baseline_manifest_sha256": manifest_sha256,
        "baseline_assessment": assessment,
        "gate": {**gate, "passed": True},
        "reproduction": reproduction or None,
        "rules_version": evaluation.rules_version,
        "eligibility": [e.model_dump(mode="json") for e in evaluation.eligibility],
        "standings": [
            dataclasses.asdict(s) | {"eligible": s.eligible} for s in standings.values()
        ],  # fmt: skip
        "entry_blocked": {
            "baseline": {str(k): v for k, v in evaluation.baseline_entry_blocked.items()},
            **{
                e.candidate: {str(k): v for k, v in e.entry_blocked.items()}
                for e in evaluation.eligibility
            },
        },  # fmt: skip
        "comparisons": [comparison_row(c) for c in evaluation.comparisons],
        "uninformative": [
            f"{c.candidate} {c.metric_name}"
            for c in evaluation.comparisons
            if c.verdict == "uninformative"
        ],  # fmt: skip
        "improvement_tests": [t.model_dump(mode="json") for t in evaluation.improvement_tests],
        "phase_changes": {c: [p.model_dump() for p in found] for c, found in changes.items()},
        "selection_on_this_member": summary["report"]["selected_intervention_id"],
        "label": "simulated",
    }
    return evidence, section


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--member", action="append", default=[], help="ID=EXPERIMENT_DIR")
    parser.add_argument("--design", type=Path, default=DESIGN, help="the pre-registered design")
    parser.add_argument("--record", action="store_true", help="write the committed record")
    args = parser.parse_args(argv)
    try:
        design = json.loads(args.design.read_text(encoding="utf-8"))
        given = parse_members(args.member)
        specs = {m["id"]: m for m in design["members"]}
        require(set(given) == set(specs),
                f"members given {sorted(given)}, the design lists {sorted(specs)}: pass "
                "--member ID=EXPERIMENT_DIR for each, an experiment that "
                "scripts/run_ngsim_int2_realism_experiment.py wrote on the member baseline "
                "scripts/build_ngsim_int2_timeline_ensemble.py built")  # fmt: skip
        scenario_path = RECORD_DIR / "scenario.json"
        require(sha256_file(scenario_path) == design["inputs"]["scenario_record_sha256"],
                "scenario.json differs from the design's")  # fmt: skip
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        world_path = REPO / scenario["processed_outputs"]["dir"] / "world_model.json"
        require(sha256_file(world_path) == design["inputs"]["world_model_sha256"],
                f"{world_path} differs from the design's world model")  # fmt: skip
        world = TrafficWorldModel.model_validate_json(world_path.read_text(encoding="utf-8"))
        reference = SignalTimeline.model_validate_json(
            (RECORD_DIR / "signal_timeline.json").read_text(encoding="utf-8")
        )
        evidences, sections = [], []
        for spec in design["members"]:
            evidence, section = check_member(spec["id"], spec, design, given[spec["id"]], world,
                                             reference)  # fmt: skip
            evidences.append(evidence)
            sections.append(section)
            print(f"{spec['id']}: inputs, gate and evaluation checked")
        verdict = ensemble_verdict(evidences)
    except (EnsembleInputError, FileNotFoundError, KeyError, ValueError) as error:
        print(f"ensemble check failed: {error}", file=sys.stderr)
        return 2

    now = datetime.now(UTC).replace(microsecond=0)
    out = REPO / "experiments/runs" / f"ngsim-int2-timeline-ensemble-{now:%Y%m%dT%H%M%SZ}"
    record = {
        "script": SCRIPT,
        "created_at_utc": now.isoformat(),
        "run_dir": rel(out),
        "label": "simulated",
        "design": {
            "path": rel(args.design),
            "sha256": sha256_file(args.design),
            "decision": design.get("decision"),
        },  # fmt: skip
        "rules_version": RULES_VERSION,
        "rules": {
            "preregistered": design["evaluation_rules_version"],
            "used": RULES_VERSION,
            "amendment": AMENDMENTS.get(RULES_VERSION),
        },
        "members": sections,
        "verdict": verdict.model_dump(mode="json"),
        "assumptions": design["assumptions"],
        "limitations": design["limitations"],
    }
    text = json.dumps(record, indent=2) + "\n"
    out.mkdir(parents=True)
    with (out / "summary.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    if args.record:
        RECORDS_DIR.mkdir(parents=True, exist_ok=True)
        path = RECORDS_DIR / f"ngsim-int2-timeline-ensemble-{now:%Y%m%dT%H%M%SZ}.json"
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    print(f"wrote {rel(out)}")
    for v in verdict.candidates:
        print(f"{v.candidate}: {v.verdict}. {v.reason}")
    print(f"{verdict.selection_reason} {verdict.recommendation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
