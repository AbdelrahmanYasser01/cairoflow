"""Drain sensitivity of the recorded NGSIM intersection 2 experiment: does a later end change it?

The recorded baseline and experiment run SUMO to 1800 s (warm-up 300 s, window [300, 1200) s,
demand over [0, 1200) s, drain 600 s). Candidates I1 and I3 left vehicles in the network at
1800 s. This script reruns every recorded run (the baseline and every candidate, every seed) with
only `--end` changed (default ends 1800, 2400 and 4200 s), each into a new run directory under
`--out`, and records what a later end changes:

- at the recorded end, the tripinfo SHA-256 (XML comments stripped) must equal the recorded
  RunRecord.tripinfo_sha256, and queue.xml must hash as the recorded one: the same run;
- at every later end, every output before the recorded end must be identical to the recorded
  run: the tripinfo records of vehicles that arrived by then (all attributes, same order), the
  set of vehicles not arrived by then, and every queue.xml step before it;
- per arm and seed: vehicles unfinished at each end, the last arrival, and the W_in_2 (west
  left-turn lane) queue at the window end and at the last step before the recorded end;
- the window metrics of `cairoflow_evaluation.metrics.run_metrics` at each end, and the paired
  comparisons of `cairoflow_evaluation.compare.paired_comparison` at each end.

It is a diagnostic: RunSettings and the recorded experiment stay as they are. Under the
eligibility rule an incomplete candidate is never selectable and its mean delay is not compared
(an unfinished trip's timeLoss is a lower bound); the comparison of mean delay is still listed
here for every end, flagged, to show what the lower bound does.

    uv run --frozen python scripts/drain_sensitivity_ngsim_int2.py --out <new dir> \
        [--ends 1800 2400 4200]

Inputs are checked first: the baseline manifest against the scenario record
(`baseline.manifest_sha256`) and against the experiment manifest's `baseline_manifest_sha256`;
the experiment manifest against its recorded hash; every network and demand file against its
manifest. SUMO reads the recorded network and demand files in place (absolute paths); it writes
only into the new run directories. Nothing is written under experiments/runs/ or data/. Outputs:
runs/<arm>/end_<s>/seed_<n>/, drain_sensitivity.json and drain_sensitivity.md. Every outcome
number is simulated (SUMO output); the settings are assumed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import sumo

from cairoflow_contracts.experiment import MetricResult, RunSettings
from cairoflow_evaluation.compare import paired_comparison
from cairoflow_evaluation.metrics import metric_names, run_metrics
from cairoflow_simulation.network import content_sha256

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
BASELINE_DIR = REPO / "experiments/runs/ngsim-int2-baseline-20261028T074304Z"
EXPERIMENT_DIR = REPO / "experiments/runs/ngsim-int2-experiment-20261028T084336Z/experiment"
# experiment/experiment_manifest.json as listed in that run's report.json and report.md.
EXPERIMENT_MANIFEST_SHA256 = "419b07ca9b3d556d191f724ed1c4ed189c085d03e0606b740fefdfb5320318b5"
# The recorded runs and the committed records; a new directory under experiments/runs is fine.
FORBIDDEN = (BASELINE_DIR, EXPERIMENT_DIR.parent, REPO / "data", REPO / "cache")
QUEUE_LANE = "W_in_2"
DEFAULT_ENDS = (1800.0, 2400.0, 4200.0)
DELAY_METRIC = "mean_delay_s"


class InputError(ValueError):
    """An input does not match its record."""


# --- files (pure) --------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_out(path: Path, forbidden: Sequence[Path] = FORBIDDEN) -> Path:
    """Create the new output directory; refuse an existing one or one under a recorded tree."""
    resolved = path.resolve()
    for root in forbidden:
        if resolved.is_relative_to(root.resolve()):
            raise ValueError(f"{path} is under {root}; this script never writes there")
    if path.exists():
        raise FileExistsError(f"{path} exists; outputs are never overwritten")
    path.mkdir(parents=True)
    return path


def write_new(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def read_trips(path: Path) -> list[dict[str, str]]:
    """Every <tripinfo> of a tripinfo.xml, in file order, as attribute dicts."""
    return [dict(t.attrib) for t in ET.parse(path).getroot().findall("tripinfo")]


QueueStep = tuple[float, tuple[tuple[tuple[str, str], ...], ...]]


def queue_steps(path: Path, before_s: float) -> list[QueueStep]:
    """Every queue.xml step with timestep < before_s: (timestep, sorted attributes per lane)."""
    steps: list[QueueStep] = []
    for _, element in ET.iterparse(path):
        if element.tag != "data":
            continue
        t = float(element.get("timestep", "nan"))
        if t < before_s:
            lanes = tuple(tuple(sorted(lane.attrib.items())) for lane in element.iter("lane"))
            steps.append((t, lanes))
        element.clear()
    return steps


def queue_at(path: Path, lane: str, times_s: Sequence[float]) -> dict[float, float | None]:
    """queueing_length (m) of `lane` at each time; 0 when the step lists no queue on it, None
    when the run wrote no such step (SUMO's last step is end - step)."""
    out: dict[float, float | None] = dict.fromkeys(times_s)
    for _, element in ET.iterparse(path):
        if element.tag != "data":
            continue
        t = float(element.get("timestep", "nan"))
        if t in out:
            out[t] = 0.0
            for item in element.iter("lane"):
                if item.get("id") == lane:
                    out[t] = float(item.get("queueing_length", 0))
        element.clear()
    return out


# --- checks and summaries (pure) -----------------------------------------------------------


@dataclass(frozen=True)
class IdentityCheck:
    """A later-ending run against the recorded run, over everything before the recorded end."""

    reference_end_s: float
    arrivals_compared: int
    arrivals_identical: bool
    unfinished_compared: int
    unfinished_identical: bool
    queue_steps_compared: int
    queue_identical: bool
    first_difference: str | None

    @property
    def identical(self) -> bool:
        return self.arrivals_identical and self.unfinished_identical and self.queue_identical


def pre_end_identity(reference_dir: Path, later_dir: Path, reference_end_s: float) -> IdentityCheck:
    """Compare a run ending later with the reference run (ending at reference_end_s).

    Arrivals: the reference's arrived trips (arrival >= 0) against the later run's trips that
    arrived in [0, reference_end_s], all attributes and order. Unfinished: the reference's trips
    with arrival -1 against the later run's trips not arrived by reference_end_s. Queue: every
    step with timestep < reference_end_s.
    """
    ref = read_trips(reference_dir / "tripinfo.xml")
    later = read_trips(later_dir / "tripinfo.xml")

    def arrived_by(trip: Mapping[str, str]) -> bool:
        return 0 <= float(trip.get("arrival", -1)) <= reference_end_s

    ref_arrived = [t for t in ref if float(t.get("arrival", -1)) >= 0]
    later_arrived = [t for t in later if arrived_by(t)]
    ref_unfinished = sorted(t["id"] for t in ref if float(t.get("arrival", -1)) < 0)
    later_unfinished = sorted(t["id"] for t in later if not arrived_by(t))
    ref_queue = queue_steps(reference_dir / "queue.xml", reference_end_s)
    later_queue = queue_steps(later_dir / "queue.xml", reference_end_s)

    first: str | None = None
    if ref_arrived != later_arrived:
        pairs = zip(ref_arrived, later_arrived, strict=False)
        index = next((i for i, (a, b) in enumerate(pairs) if a != b), None)
        if index is None:
            first = f"arrivals: {len(ref_arrived)} recorded, {len(later_arrived)} later"
        else:
            first = f"arrivals: trip {ref_arrived[index].get('id')} differs (position {index})"
    elif ref_unfinished != later_unfinished:
        first = f"unfinished: {ref_unfinished} recorded, {later_unfinished} later"
    elif ref_queue != later_queue:
        pairs_q = zip(ref_queue, later_queue, strict=False)
        index = next((i for i, (a, b) in enumerate(pairs_q) if a != b), None)
        if index is None:
            first = f"queue.xml: {len(ref_queue)} steps recorded, {len(later_queue)} later"
        else:
            first = f"queue.xml: timestep {ref_queue[index][0]:g} differs"
    return IdentityCheck(
        reference_end_s=reference_end_s,
        arrivals_compared=len(ref_arrived),
        arrivals_identical=ref_arrived == later_arrived,
        unfinished_compared=len(ref_unfinished),
        unfinished_identical=ref_unfinished == later_unfinished,
        queue_steps_compared=len(ref_queue),
        queue_identical=ref_queue == later_queue,
        first_difference=first,
    )


@dataclass(frozen=True)
class RunSummary:
    """Completion of one run, from statistics.xml, tripinfo.xml and queue.xml."""

    loaded: int
    inserted: int
    arrived: int
    unfinished: int
    teleports: int
    collisions: int
    last_arrival_s: float | None
    unfinished_ids: tuple[str, ...]
    queue_m: dict[str, float | None]


def summarize_run(run_dir: Path, lane: str, queue_times_s: Sequence[float]) -> RunSummary:
    """Counts as runner.run_seed takes them (unfinished = running + waiting at the end)."""
    stats = ET.parse(run_dir / "statistics.xml").getroot()
    vehicles, teleports, safety = (stats.find(tag) for tag in ("vehicles", "teleports", "safety"))
    if vehicles is None or teleports is None or safety is None:
        raise InputError(f"{run_dir / 'statistics.xml'} lacks vehicles, teleports or safety")
    trips = read_trips(run_dir / "tripinfo.xml")
    arrivals = [float(t["arrival"]) for t in trips if float(t.get("arrival", -1)) >= 0]
    queue = queue_at(run_dir / "queue.xml", lane, queue_times_s)
    return RunSummary(
        loaded=int(vehicles.get("loaded", 0)),
        inserted=int(vehicles.get("inserted", 0)),
        arrived=len(arrivals),
        unfinished=int(vehicles.get("running", 0)) + int(vehicles.get("waiting", 0)),
        teleports=int(teleports.get("total", 0)),
        collisions=int(safety.get("collisions", 0)),
        last_arrival_s=max(arrivals) if arrivals else None,
        unfinished_ids=tuple(t["id"] for t in trips if float(t.get("arrival", -1)) < 0),
        queue_m={f"{t:g}": value for t, value in queue.items()},
    )


def _key(end_s: float) -> str:
    return f"{end_s:g}"


def summary_rows(
    runs: Sequence[Mapping], ends_s: Sequence[float], reference_end_s: float, window_end_s: float
) -> list[dict]:
    """One row per arm and seed from the per-run records (see `run_record`), in input order."""
    rows: dict[tuple[str, int], dict] = {}
    for run in runs:
        key = (run["arm"], run["seed"])
        row = rows.setdefault(
            key,
            {
                "arm": run["arm"],
                "seed": run["seed"],
                "unfinished": {},
                "last_arrival_s": {},
                "mean_delay_s": {},
                "queue_m_at_window_end": None,
                "queue_m_before_reference_end": None,
                "checks_passed": True,
                "metrics_changed_with_end": [],
            },
        )
        end = _key(run["end_s"])
        summary = run["summary"]
        row["unfinished"][end] = summary["unfinished"]
        row["last_arrival_s"][end] = summary["last_arrival_s"]
        values = {m["metric_name"]: m["value"] for m in run["metrics"]}
        row["mean_delay_s"][end] = values.get(DELAY_METRIC)
        row.setdefault("_metrics", {})[end] = values
        if run["end_s"] == reference_end_s:
            row["queue_m_at_window_end"] = summary["queue_m"].get(_key(window_end_s))
            row["queue_m_before_reference_end"] = summary["queue_m"].get(_key(reference_end_s - 1))
            passed = bool(run["tripinfo_matches_record"]) and bool(run["queue_matches_record"])
        else:
            passed = bool(run["identity"] and run["identity"]["identical"])
        row["checks_passed"] = row["checks_passed"] and passed
    for row in rows.values():
        per_end = row.pop("_metrics")
        names = sorted({n for values in per_end.values() for n in values})
        row["metrics_changed_with_end"] = [
            n
            for n in names
            if len({per_end[_key(e)].get(n) for e in ends_s if _key(e) in per_end}) > 1
        ]
    return list(rows.values())


def arm_complete(rows: Sequence[Mapping], arm: str, end_s: float) -> bool:
    """Every seed of the arm had no vehicle left in the network at that end."""
    return all(r["unfinished"][_key(end_s)] == 0 for r in rows if r["arm"] == arm)


def _fmt(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def render_markdown(doc: Mapping) -> str:
    """The report from the JSON document (rows, comparisons, inputs, checks)."""
    ends = [_key(e) for e in doc["ends_s"]]
    ref = _key(doc["reference_end_s"])
    lines = [
        "# Drain sensitivity: NGSIM intersection 2 experiment",
        "",
        "Diagnostic reruns of the recorded runs with only `--end` changed. Decision support only:"
        " nothing here controls real traffic. Every outcome number is simulated by SUMO "
        f"{doc['software']['eclipse-sumo']}; the settings are assumed. Window "
        f"[{doc['window_s'][0]:g}, {doc['window_s'][1]:g}) s, demand over "
        f"[0, {doc['window_s'][1]:g}) s, recorded end {ref} s.",
        "",
        "## Checks",
        "",
        f"- Inputs: {'; '.join(doc['inputs']['checks'])}.",
        f"- At {ref} s every rerun's tripinfo and queue.xml hash as recorded: "
        f"{doc['checks']['reference_hashes_match']}.",
        f"- At every later end, every output before {ref} s is identical to the recorded run: "
        f"{doc['checks']['pre_end_identical']}.",
        f"- All checks passed: **{doc['checks']['all_passed']}**.",
        "",
        "## Completion per arm and seed",
        "",
        f"`{doc['queue_lane']}` queue: queue.xml queueing_length at the window end and at the "
        f"last step an {ref} s run writes (identical at every end, by the check above).",
        "",
        "| Arm | Seed | "
        + " | ".join(f"Unfinished @{e}" for e in ends)
        + " | "
        + " | ".join(f"Last arrival @{e} (s)" for e in ends)
        + f" | {doc['queue_lane']} @{_key(doc['window_s'][1])} (m)"
        + f" | {doc['queue_lane']} @{_key(doc['reference_end_s'] - 1)} (m) | Checks |",
        "|---|---|" + "---|" * (2 * len(ends) + 3),
    ]
    for r in doc["rows"]:
        lines.append(
            f"| {r['arm']} | {r['seed']} | "
            + " | ".join(str(r["unfinished"].get(e, "-")) for e in ends)
            + " | "
            + " | ".join(_fmt(r["last_arrival_s"].get(e), 0) for e in ends)
            + f" | {_fmt(r['queue_m_at_window_end'])} | {_fmt(r['queue_m_before_reference_end'])}"
            + f" | {'pass' if r['checks_passed'] else 'FAIL'} |"
        )
    lines += [
        "",
        "## Window metrics that change with the end",
        "",
        "Per arm and seed, `mean_delay_s` (s) at each end; the metrics listed in the last column "
        "differ between ends (every other window metric is identical at every end).",
        "",
        "| Arm | Seed | " + " | ".join(f"mean_delay_s @{e}" for e in ends) + " | Changed |",
        "|---|---|" + "---|" * (len(ends) + 1),
    ]
    for r in doc["rows"]:
        lines.append(
            f"| {r['arm']} | {r['seed']} | "
            + " | ".join(_fmt(r["mean_delay_s"].get(e), 3) for e in ends)
            + f" | {', '.join(r['metrics_changed_with_end']) or 'none'} |"
        )
    lines += [
        "",
        "## Paired comparisons with the baseline at each end",
        "",
        "Candidate - baseline over paired seeds, 95% t interval (`paired_comparison`). Listed: "
        "mean delay, throughput and every metric whose verdict is not inconclusive. Where the "
        "candidate left vehicles unfinished at that end, its mean delay is a lower bound: under "
        "the eligibility rule it is not compared, and the row says so.",
        "",
        "| Candidate | End (s) | Complete | Metric | Mean difference | 95% interval | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in doc["comparisons"]:
        for m in c["metrics"]:
            if m["verdict"] == "inconclusive" and m["metric_name"] not in (
                DELAY_METRIC,
                "throughput_veh_per_h",
            ):
                continue
            ci = m["ci95_difference"]
            verdict = m["verdict"]
            if m["metric_name"] == DELAY_METRIC and not c["candidate_complete"]:
                verdict = f"{verdict} (lower bound; not compared under the rule)"
            complete = "yes" if c["candidate_complete"] else "no"
            lines.append(
                f"| {c['candidate']} | {_key(c['end_s'])} | {complete}"
                f" | {m['metric_name']} | {m['mean_difference']:+.3f} {m['unit']} | "
                + ("-" if ci is None else f"[{ci[0]:+.3f}, {ci[1]:+.3f}]")
                + f" | {verdict} |"
            )
    return "\n".join(lines) + "\n"


# --- inputs and runs -----------------------------------------------------------------------


@dataclass(frozen=True)
class Arm:
    name: str
    runs: tuple[dict, ...]  # RunRecord dicts as recorded
    runs_dir: Path  # the recorded runs/ directory
    net_sha256: str  # content hash, XML comments stripped


def load_inputs(
    baseline_dir: Path, experiment_dir: Path, record: Path
) -> tuple[dict, dict, list[Arm], list[str]]:
    """Both manifests, checked against the records, and the arms to rerun."""
    checks: list[str] = []
    raw_baseline = (baseline_dir / "baseline_manifest.json").read_bytes()
    baseline_sha = hashlib.sha256(raw_baseline).hexdigest()
    recorded = json.loads(record.read_text(encoding="utf-8"))["baseline"]["manifest_sha256"]
    if baseline_sha != recorded:
        raise InputError(f"baseline manifest {baseline_sha} != scenario record {recorded}")
    checks.append(f"baseline manifest {baseline_sha[:12]} matches the scenario record")
    raw_experiment = (experiment_dir / "experiment_manifest.json").read_bytes()
    experiment_sha = hashlib.sha256(raw_experiment).hexdigest()
    if experiment_sha != EXPERIMENT_MANIFEST_SHA256:
        raise InputError(f"experiment manifest {experiment_sha} != {EXPERIMENT_MANIFEST_SHA256}")
    checks.append(f"experiment manifest {experiment_sha[:12]} matches its recorded hash")
    experiment = json.loads(raw_experiment)
    if experiment["baseline_manifest_sha256"] != baseline_sha:
        raise InputError("the experiment manifest names a different baseline manifest")
    if (experiment_dir / experiment["baseline_dir"]).resolve() != baseline_dir.resolve():
        raise InputError("the experiment manifest's baseline_dir is not --baseline-dir")
    checks.append("the experiment manifest's baseline_manifest_sha256 and baseline_dir match")
    baseline = json.loads(raw_baseline)
    if baseline["settings"] != experiment["settings"]:
        raise InputError("the baseline and the experiment ran under different settings")
    arms = [
        Arm(
            "baseline",
            tuple(baseline["runs"]),
            baseline_dir / "runs",
            baseline["network"]["net_sha256"],
        )
    ]
    for candidate in experiment["candidates"]:
        name = candidate["intervention_id"]
        arms.append(
            Arm(
                name,
                tuple(candidate["runs"]),
                experiment_dir / "candidates" / name / "runs",
                candidate["network"]["net_sha256"],
            )
        )
    seeds = [sorted(r["seed"] for r in arm.runs) for arm in arms]
    if any(s != seeds[0] for s in seeds):
        raise InputError("the arms were not run on the same seeds")
    return baseline, experiment, arms, checks


def rerun_args(command: Sequence[str], recorded_run_dir: Path, end_s: float) -> list[str]:
    """The recorded command's arguments with --end replaced and input paths made absolute."""
    if not command or command[0] != "sumo":
        raise InputError(f"unexpected recorded command {list(command)[:3]}")
    args = list(command[1:])
    for flag in ("--net-file", "--route-files"):
        i = args.index(flag)
        args[i + 1] = str((recorded_run_dir / args[i + 1]).resolve())
    i = args.index("--end")
    args[i + 1] = f"{end_s:g}"
    return args


def repo_relative(args: Sequence[str]) -> list[str]:
    """Arguments as recorded: absolute paths inside the repository relative to its root, so the
    record holds no machine-specific path."""
    out = []
    for arg in args:
        path = Path(arg)
        out.append(
            path.relative_to(REPO).as_posix()
            if path.is_absolute() and path.is_relative_to(REPO)
            else arg
        )
    return out


def _flag(args: Sequence[str], flag: str) -> str:
    return args[list(args).index(flag) + 1]


def run_sumo(args: Sequence[str], run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)
    binary = Path(sumo.SUMO_HOME) / "bin" / "sumo"
    result = subprocess.run(
        [str(binary), *args], cwd=run_dir, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"sumo failed in {run_dir} (rc={result.returncode}): {result.stderr[-600:]}"
        )


def run_record(
    arm: Arm,
    record: Mapping,
    end_s: float,
    reference_end_s: float,
    out: Path,
    settings: RunSettings,
    approach_arms: Sequence[str],
    lane: str,
) -> tuple[dict, list[MetricResult]]:
    recorded_dir = arm.runs_dir / record["run_dir"]
    args = rerun_args(record["command"], recorded_dir, end_s)
    net, demand = Path(_flag(args, "--net-file")), Path(_flag(args, "--route-files"))
    if content_sha256(net, strip_comments=True) != arm.net_sha256:
        raise InputError(f"{net} does not hash as {arm.name}'s network manifest")
    if sha256_file(demand) != record["demand_sha256"]:
        raise InputError(f"{demand} does not hash as recorded for seed {record['seed']}")
    run_dir = out / "runs" / arm.name / f"end_{end_s:g}" / f"seed_{record['seed']}"
    run_sumo(args, run_dir)
    window_end = settings.warmup_s + settings.horizon_s
    summary = summarize_run(run_dir, lane, (window_end, reference_end_s - 1, reference_end_s))
    tripinfo_sha = content_sha256(run_dir / "tripinfo.xml", strip_comments=True)
    entry: dict = {
        "arm": arm.name,
        "seed": record["seed"],
        "end_s": end_s,
        "run_dir": str(run_dir.relative_to(out)),
        "command": ["sumo", *repo_relative(args)],
        "summary": asdict(summary),
        "tripinfo_sha256": tripinfo_sha,
        "tripinfo_matches_record": None,
        "queue_matches_record": None,
        "identity": None,
    }
    if end_s == reference_end_s:
        entry["tripinfo_matches_record"] = tripinfo_sha == record["tripinfo_sha256"]
        entry["queue_matches_record"] = content_sha256(
            run_dir / "queue.xml", strip_comments=True
        ) == content_sha256(recorded_dir / "queue.xml", strip_comments=True)
    else:
        check = pre_end_identity(recorded_dir, run_dir, reference_end_s)
        entry["identity"] = {**asdict(check), "identical": check.identical}
    metrics = run_metrics(
        run_dir,
        experiment_id=f"drain-sensitivity-end{end_s:g}",
        arm=arm.name,
        seed=record["seed"],
        settings=settings,
        approach_arms=approach_arms,
        root=out,
    )
    entry["metrics"] = [m.model_dump(mode="json") for m in metrics]
    return entry, metrics


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="new output directory")
    parser.add_argument("--baseline-dir", type=Path, default=BASELINE_DIR)
    parser.add_argument("--experiment-dir", type=Path, default=EXPERIMENT_DIR)
    parser.add_argument("--record", type=Path, default=RECORD, help="scenario record")
    parser.add_argument("--ends", type=float, nargs="+", default=list(DEFAULT_ENDS))
    parser.add_argument("--lane", default=QUEUE_LANE, help="lane whose queue is reported")
    ns = parser.parse_args(argv)
    if ns.out.exists():  # refused before any work; prepare_out refuses again when creating it
        raise FileExistsError(f"{ns.out} exists; outputs are never overwritten")

    baseline, experiment, arms, input_checks = load_inputs(
        ns.baseline_dir, ns.experiment_dir, ns.record
    )
    settings = RunSettings.model_validate(experiment["settings"])
    reference_ends = {float(_flag(r["command"][1:], "--end")) for arm in arms for r in arm.runs}
    if len(reference_ends) != 1:
        raise InputError(f"the recorded runs end at different times: {sorted(reference_ends)}")
    reference_end = reference_ends.pop()
    ends = sorted(set(ns.ends))
    if reference_end not in ends or min(ends) < reference_end:
        raise InputError(f"--ends must include {reference_end:g} s and none earlier")
    approach_arms = [
        p["name"].split(".", 1)[0]
        for p in baseline["network"]["parameters"]
        if p["name"].endswith(".approach_lanes")
    ]
    names = metric_names(approach_arms)
    out = prepare_out(ns.out)

    runs: list[dict] = []
    metrics: dict[tuple[str, float], list[MetricResult]] = {}
    for arm in arms:
        for record in sorted(arm.runs, key=lambda r: r["seed"]):
            for end in ends:
                entry, found = run_record(
                    arm, record, end, reference_end, out, settings, approach_arms, ns.lane
                )
                runs.append(entry)
                metrics.setdefault((arm.name, end), []).extend(found)
                print(
                    f"{arm.name} seed {record['seed']} end {end:g}: unfinished "
                    f"{entry['summary']['unfinished']}, last arrival "
                    f"{entry['summary']['last_arrival_s']}",
                    flush=True,
                )

    window_end = settings.warmup_s + settings.horizon_s
    rows = summary_rows(runs, ends, reference_end, window_end)
    comparisons = []
    for arm in arms[1:]:
        for end in ends:
            items = [
                paired_comparison(
                    arm.name,
                    [m for m in metrics[("baseline", end)] if m.metric_name == name],
                    [m for m in metrics[(arm.name, end)] if m.metric_name == name],
                ).model_dump(mode="json")
                for name in names
            ]
            comparisons.append(
                {
                    "candidate": arm.name,
                    "end_s": end,
                    "candidate_complete": arm_complete(rows, arm.name, end),
                    "baseline_complete": arm_complete(rows, "baseline", end),
                    "metrics": items,
                }
            )
    reference_ok = all(
        r["tripinfo_matches_record"] and r["queue_matches_record"]
        for r in runs
        if r["end_s"] == reference_end
    )
    identical = all(r["identity"]["identical"] for r in runs if r["end_s"] != reference_end)
    doc = {
        "schema_version": "0.1.0",
        "created_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "scripts/drain_sensitivity_ngsim_int2.py",
        "software": {"eclipse-sumo": metadata.version("eclipse-sumo")},
        "inputs": {
            "baseline_dir": repo_relative([str(ns.baseline_dir.resolve())])[0],
            "experiment_dir": repo_relative([str(ns.experiment_dir.resolve())])[0],
            "baseline_manifest_sha256": experiment["baseline_manifest_sha256"],
            "experiment_manifest_sha256": EXPERIMENT_MANIFEST_SHA256,
            "experiment_id": experiment["experiment_id"],
            "checks": input_checks,
        },
        "settings": experiment["settings"],
        "window_s": [settings.warmup_s, window_end],
        "reference_end_s": reference_end,
        "ends_s": ends,
        "queue_lane": ns.lane,
        "labels": {"outcomes": "simulated", "settings": "assumed"},
        "checks": {
            "reference_hashes_match": reference_ok,
            "pre_end_identical": identical,
            "all_passed": reference_ok and identical,
        },
        "rows": rows,
        "comparisons": comparisons,
        "runs": runs,
    }
    write_new(out / "drain_sensitivity.json", json.dumps(doc, indent=1) + "\n")
    write_new(out / "drain_sensitivity.md", render_markdown(doc))
    print(
        f"wrote {out / 'drain_sensitivity.json'} and drain_sensitivity.md; checks passed: "
        f"{doc['checks']['all_passed']}"
    )
    return 0 if doc["checks"]["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
