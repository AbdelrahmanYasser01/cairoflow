"""Signal-timing sensitivity of the NGSIM intersection 2 baseline to the cycle SUMO repeats.

SUMO repeats one 99 s cut of the trajectory-derived signal timeline (DATA_REGISTER.md §16,
ADR-019). This script asks whether that choice matters when green splits are compared. It uses
only cuts that come from the observations: every candidate cut the compiler can take (an arm's
green onset to its next one, as `cairoflow_perception.ngsim_junction.representative_cycle` and
`cairoflow_simulation.geometry_compiler.signal_cycle` cut them). Only cuts that qualify (every head
shows green, none is unknown, each head's last state leads legally to its first) can be repeated;
the others are listed with the reason and not run. No timing is invented or edited.

For each qualifying cut the baseline is compiled and run exactly as recorded except
`CompileOptions.cycle_start_s`: the same processed world model, geometry and timeline, the recorded
compile options and RunSettings, and demand files that must hash as the recorded baseline's, so
only the signal program differs. Metrics come from `cairoflow_evaluation.metrics.run_metrics`.

With `--i2`, each cut with the recorded program's phase structure also gets I2's recorded change
(the same per-phase differences: 5 s from the N/S through phase to the protected-left phase). It is
validated by `cairoflow_simulation.validation` against that cut's network and run on that cut's
seeds and demand: a robustness check of the existing validated candidate, not a new candidate.

    uv run --frozen python scripts/signal_sensitivity_ngsim_int2.py --out <new dir> [--i2]

Inputs are checked against the committed record's hashes (scenario record, processed outputs,
baseline manifest and demand files, the I2 runs and comparisons). Writes only under `--out` (new,
never overwritten): runs/, i2/, sensitivity.json and sensitivity.md. Labels: observed (NGSIM
trajectories), derived (our fixed rule over observed values), assumed (synthetic constants and
modelling choices), simulated (SUMO output).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import numpy as np

from cairoflow_contracts.evidence import (
    IntersectionGeometry,
    SignalTimeline,
    TrafficWorldModel,
)
from cairoflow_contracts.experiment import (
    BaselineManifest,
    ExperimentManifest,
    MetricResult,
)
from cairoflow_contracts.reasoning import InterventionProposal, PhaseChange
from cairoflow_evaluation.compare import paired_comparison, t95_half_width
from cairoflow_evaluation.metrics import better_direction, metric_names, run_metrics
from cairoflow_perception.ngsim_junction import heads_conflict
from cairoflow_reasoning.evidence import build_evidence_bundle
from cairoflow_simulation.candidates import CandidateError, load_baseline, run_candidates
from cairoflow_simulation.geometry_compiler import CompileError, CompileOptions, green_onsets
from cairoflow_simulation.runner import SimulationError, run_baseline
from cairoflow_simulation.validation import validate_proposal

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
RECORD_SHA256 = "9d08eb6e033d2e60e81aaa10b5c6c6773fc4bb55058e39baae7224f59ff156a1"
EXPERIMENT_RUN = REPO / "experiments/runs/ngsim-int2-experiment-20261028T084336Z"
EXPERIMENT_RECORD = REPO / "experiments/records/ngsim-int2-experiment-20261028T075803Z.json"
CANDIDATE = "I2"
Head = tuple[str, str]


class InputError(ValueError):
    """An input does not match the committed record."""


# --- cuts (pure) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cut:
    start_s: float
    end_s: float
    arms: tuple[str, ...]  # the arms whose green onset starts the cut

    @property
    def length_s(self) -> float:
        return round(self.end_s - self.start_s, 6)


def candidate_cuts(onsets: Mapping[str, Sequence[float]]) -> list[Cut]:
    """Every cut from an arm's green onset to the next onset of the arms turning green then.

    The same set as `representative_cycle` counts (cuts from different arms overlap).
    """
    out: dict[tuple[float, float], Cut] = {}
    for times in onsets.values():
        for start in times[:-1]:
            starting = [arm for arm, ts in onsets.items() if start in ts[:-1]]
            end = min(onsets[arm][list(onsets[arm]).index(start) + 1] for arm in starting)
            out[(start, end)] = Cut(start, end, tuple(sorted(starting, key="NESW".index)))
    return [out[key] for key in sorted(out)]


def _spans(timeline: SignalTimeline, head: Head, start: float, end: float) -> list:
    arm, kind = head
    return [
        i
        for i in timeline.intervals
        if i.arm == arm and i.head == kind and i.start_s < end and i.end_s > start
    ]


_NEXT = {"green": "yellow", "yellow": "red", "red": "green"}


def cut_problems(timeline: SignalTimeline, cut: Cut) -> list[str]:
    """Why a cut cannot be repeated (empty: it qualifies), by the rule of `representative_cycle`."""
    problems: list[str] = []
    for head in timeline.heads():
        name = f"{head[0]} {head[1]}"
        inside = _spans(timeline, head, cut.start_s, cut.end_s)
        if any(i.state == "unknown" for i in inside):
            problems.append(f"{name}: unknown inside")
            continue
        if not any(i.state == "green" for i in inside):
            problems.append(f"{name}: no green")
            continue
        first, last = inside[0].state, inside[-1].state
        if first != last and _NEXT[last] != first:
            problems.append(f"{name}: does not close ({last} to {first})")
    return problems


def green_in(timeline: SignalTimeline, head: Head, start: float, end: float) -> float:
    """Seconds of green of one head inside [start, end)."""
    return round(
        sum(
            min(i.end_s, end) - max(i.start_s, start)
            for i in _spans(timeline, head, start, end)
            if i.state == "green"
        ),
        6,
    )


def head_medians(timeline: SignalTimeline) -> dict[Head, float]:
    """Median green duration of each head over the whole timeline."""
    return {
        head: float(
            np.median(
                [
                    i.end_s - i.start_s
                    for i in timeline.intervals
                    if (i.arm, i.head) == head and i.state == "green"
                ]
            )
        )
        for head in timeline.heads()
    }


def onset_spacings(onsets: Mapping[str, Sequence[float]]) -> list[float]:
    return [b - a for ts in onsets.values() for a, b in zip(ts, ts[1:], strict=False)]


def cut_score(
    timeline: SignalTimeline, cut: Cut, medians: Mapping[Head, float], median_length: float
) -> float:
    """Summed |difference| of the cut's head greens and length from their medians."""
    return round(
        abs(cut.length_s - median_length)
        + sum(
            abs(green_in(timeline, head, cut.start_s, cut.end_s) - medians[head])
            for head in timeline.heads()
        ),
        6,
    )


def select_cut(scores: Mapping[Cut, float]) -> Cut:
    """The qualifying cut nearest the medians; earliest on a tie (as `representative_cycle`)."""
    if not scores:
        raise ValueError("no qualifying cut")
    return min(scores, key=lambda c: (round(scores[c], 6), c.start_s))


def all_red_s(timeline: SignalTimeline, start: float, end: float) -> float:
    """Seconds in [start, end) when every head shows red (on the timeline's 1 s grid)."""
    heads = timeline.heads()
    edges = sorted(
        {start, end, *(i.start_s for i in timeline.intervals if start < i.start_s < end)}
    )
    total = 0.0
    for a, b in zip(edges, edges[1:], strict=False):
        if all(timeline.state_at(arm, a, kind) == "red" for arm, kind in heads):
            total += b - a
    return round(total, 6)


def green_bounds(timeline: SignalTimeline, all_red: float) -> list[dict[str, object]]:
    """Per green window: how much earlier its onset and how much later its end could have been
    without overlapping a conflicting head's estimated green or yellow (plus `all_red`).

    A bound of the estimate, not a measurement: the true green lies between the estimate and these
    limits. None where no conflicting window bounds it inside the timeline.
    """
    windows: dict[Head, list[tuple[float, float, float]]] = defaultdict(list)
    for head in timeline.heads():
        spans = [i for i in timeline.intervals if (i.arm, i.head) == head]
        for k, interval in enumerate(spans):
            if interval.state != "green":
                continue
            yellow_end = interval.end_s
            if k + 1 < len(spans) and spans[k + 1].state == "yellow":
                yellow_end = spans[k + 1].end_s
            windows[head].append((interval.start_s, interval.end_s, yellow_end))
    out: list[dict[str, object]] = []
    for head, spans in windows.items():
        rivals = [
            w for other, ws in windows.items() if heads_conflict(head, other) for w in ws
        ]  # a head conflicts with itself, so its own other windows bound it too
        for start, green_end, yellow_end in spans:
            before = [y for s, _, y in rivals if y <= start and (s, y) != (start, yellow_end)]
            after = [s for s, _, y in rivals if s >= yellow_end and (s, y) != (start, yellow_end)]
            out.append(
                {
                    "head": f"{head[0]} {head[1]}",
                    "green_start_s": start,
                    "green_end_s": green_end,
                    "green_s": round(green_end - start, 6),
                    "onset_could_be_earlier_by_s": (
                        round(start - max(before) - all_red, 6) if before else None
                    ),
                    "end_could_be_later_by_s": (
                        round(min(after) - all_red - yellow_end, 6) if after else None
                    ),
                }
            )
    return sorted(out, key=lambda w: (w["green_start_s"], w["head"]))


# --- statistics (pure) ---------------------------------------------------------------------


def describe(values: Sequence[float]) -> dict[str, float | int | None]:
    """n, mean, sample SD (None for one value), min and max."""
    if not values:
        return {"n": 0, "mean": None, "sd": None, "min": None, "max": None}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 6),
        "sd": round(statistics.stdev(values), 6) if len(values) > 1 else None,
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def spread(means: Mapping[str, float]) -> dict[str, object]:
    """Spread of per-cut means: lowest, highest, range and SD of the means."""
    if not means:
        return {"n": 0}
    low = min(means, key=lambda k: (means[k], k))
    high = max(means, key=lambda k: (means[k], k))
    values = list(means.values())
    return {
        "n": len(values),
        "lowest": {"cut": low, "mean": round(means[low], 6)},
        "highest": {"cut": high, "mean": round(means[high], 6)},
        "range": round(means[high] - means[low], 6),
        "sd_of_means": round(statistics.stdev(values), 6) if len(values) > 1 else None,
    }


def t_interval(diffs: Sequence[float]) -> tuple[float, float] | None:
    """Two-sided 95% t interval of the mean of paired differences (as compare.py)."""
    if len(diffs) < 2:
        return None
    mean, sd = statistics.fmean(diffs), statistics.stdev(diffs)
    half = t95_half_width(len(diffs), sd)
    return round(mean - half, 6), round(mean + half, 6)


def phase_signature(names: Sequence[str]) -> tuple[str, ...]:
    """Phase names without their index prefix: the program's phase structure."""
    return tuple(name.split("_", 1)[1] for name in names)


def recorded_deltas(baseline: Sequence[float], candidate: Sequence[float]) -> dict[int, float]:
    """Per phase index, candidate minus baseline duration (only the changed phases)."""
    return {
        i: round(c - b, 6)
        for i, (b, c) in enumerate(zip(baseline, candidate, strict=True))
        if abs(c - b) > 1e-9
    }


def map_change(
    recorded: Sequence[str], cut: Sequence[str], deltas: Mapping[int, float]
) -> tuple[dict[int, float] | None, str]:
    """Carry per-phase changes of the recorded program over to a cut's program.

    Phases are matched by their state signature. Identical structure: same indices. Otherwise each
    changed phase must occur exactly once in both programs (the other phases, such as the order
    of clearances, may differ). Returns (index -> change, how it matched) or (None, why not).
    """
    rec, other = phase_signature(recorded), phase_signature(cut)
    if other == rec:
        return dict(deltas), "identical phase structure"
    mapped: dict[int, float] = {}
    for i, delta in deltas.items():
        if rec.count(rec[i]) != 1:
            return None, f"phase {rec[i]} is not unique in the recorded program"
        found = [k for k, sig in enumerate(other) if sig == rec[i]]
        if len(found) != 1:
            return None, f"phase {rec[i]} occurs {len(found)} times in this cut"
        mapped[found[0]] = delta
    rotated = any(other == rec[k:] + rec[:k] for k in range(len(rec)))
    return mapped, (
        "same phase structure, rotated"
        if rotated
        else "each changed phase occurs once; other phases (clearance order) differ"
    )


# --- files ---------------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_out(path: Path) -> Path:
    """Create the new output directory; an existing one is refused, never overwritten."""
    if path.exists():
        raise FileExistsError(f"{path} exists; outputs are never overwritten")
    path.mkdir(parents=True)
    return path


def write_new(path: Path, text: str) -> str:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- inputs --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Inputs:
    record: dict
    world: TrafficWorldModel
    geometry: IntersectionGeometry
    timeline: SignalTimeline
    baseline_dir: Path
    baseline: BaselineManifest
    experiment: ExperimentManifest
    experiment_record: dict
    evaluation: dict
    checks: list[str]


def load_inputs() -> Inputs:
    """Load every input and check it against the committed record; raise on any difference."""
    checks: list[str] = []

    def check(ok: bool, what: str) -> None:
        if not ok:
            raise InputError(what)
        checks.append(what)

    check(sha256_file(RECORD) == RECORD_SHA256, f"{RECORD.relative_to(REPO)}: SHA-256 as recorded")
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    processed = REPO / record["processed_outputs"]["dir"]
    for name, want in sorted(record["processed_outputs"]["sha256"].items()):
        check(sha256_file(processed / name) == want, f"{name}: SHA-256 as the record's")
    world = TrafficWorldModel.model_validate_json((processed / "world_model.json").read_text())
    geometry = IntersectionGeometry.model_validate_json((processed / "geometry.json").read_text())
    timeline = SignalTimeline.model_validate_json((processed / "signal_timeline.json").read_text())
    committed = SignalTimeline.model_validate_json(
        (RECORD.parent / "signal_timeline.json").read_text()
    )
    check(committed == timeline, "processed timeline equals the committed signal_timeline.json")
    baseline_dir = REPO / record["baseline"]["run_dir"]
    check(
        sha256_file(baseline_dir / "baseline_manifest.json")
        == record["baseline"]["manifest_sha256"],
        "baseline_manifest.json: SHA-256 as the record's",
    )
    baseline = BaselineManifest.model_validate_json(
        (baseline_dir / "baseline_manifest.json").read_text()
    )
    for file in baseline.demand.files:
        check(
            sha256_file(baseline_dir / "demand" / file.file) == file.sha256,
            f"demand/{file.file}: SHA-256 as the baseline manifest's",
        )
    check(
        [r.tripinfo_sha256 for r in baseline.runs]
        == [r["tripinfo_sha256"] for r in record["baseline"]["runs"]],
        "baseline tripinfo hashes as the record's",
    )
    exp_dir = EXPERIMENT_RUN / "experiment"
    experiment = ExperimentManifest.model_validate_json(
        (exp_dir / "experiment_manifest.json").read_text()
    )
    check(
        experiment.baseline_manifest_sha256 == record["baseline"]["manifest_sha256"],
        "experiment ran against the recorded baseline",
    )
    experiment_record = json.loads(EXPERIMENT_RECORD.read_text(encoding="utf-8"))
    check(
        experiment_record["inputs"]["scenario_record_sha256"] == RECORD_SHA256,
        "committed experiment record names this scenario record",
    )
    ours = next(c for c in experiment.candidates if c.intervention_id == CANDIDATE)
    theirs = next(
        c
        for c in experiment_record["experiment"]["candidates"]
        if c["intervention_id"] == CANDIDATE
    )
    check(
        [r.tripinfo_sha256 for r in ours.runs] == [r["tripinfo_sha256"] for r in theirs["runs"]],
        f"{CANDIDATE} tripinfo hashes as the committed experiment record's",
    )
    evaluation = json.loads((exp_dir / "evaluation.json").read_text(encoding="utf-8"))
    mine = [c for c in evaluation["comparisons"] if c["candidate"] == CANDIDATE]
    recorded = [
        c for c in experiment_record["evaluation"]["comparisons"] if c["candidate"] == CANDIDATE
    ]
    keys = ("metric_name", "mean_difference", "ci95_difference", "verdict")
    check(
        [[c[k] for k in keys] for c in mine] == [[c[k] for k in keys] for c in recorded],
        f"{CANDIDATE} comparisons as the committed experiment record's",
    )
    return Inputs(
        record, world, geometry, timeline, baseline_dir, baseline, experiment,
        experiment_record, evaluation, checks,
    )  # fmt: skip


# --- per-cut observed counts ---------------------------------------------------------------


def crossings_in(world: TrafficWorldModel, start: float, end: float) -> dict[str, int]:
    """Observed stop-line crossings per approach and turn in [start, end)."""
    out: dict[str, int] = {}
    for m in world.movements:
        if m.turn == "u_turn":
            continue
        n = sum(start <= t.stop_line_time_s < end for t in m.tracks)
        out[f"{m.approach} {m.turn}"] = out.get(f"{m.approach} {m.turn}", 0) + n
    return out


def queue_at(
    world: TrafficWorldModel, geometry: IntersectionGeometry, head: Head, t: float
) -> dict[str, object] | None:
    """Observed queued vehicles in the head's lanes in the 1 s sample just before `t`."""
    arm, kind = head
    lanes = {
        lane.lane_id
        for lane in geometry.lanes
        if lane.arm == arm
        and lane.direction == "approach"
        and (
            "through" in lane.allowed_turns
            if kind == "all"
            else "left" in lane.allowed_turns and "through" not in lane.allowed_turns
        )
    }
    at = math.floor(t) - 1.0
    samples = [q for q in world.queues if q.lane_id in lanes and q.frame.timestamp_s == at]
    if not samples:
        return None
    return {
        "at_s": at,
        "queued_veh": sum(q.queued_veh for q in samples),
        "lower_bound": any(q.censored for q in samples),
    }


# --- runs ----------------------------------------------------------------------------------


def _safety(seed_dir: Path, attribute: str) -> int:
    safety = ET.parse(seed_dir / "statistics.xml").getroot().find("safety")
    return int(safety.get(attribute, 0)) if safety is not None else 0


def run_cut(
    inputs: Inputs, cut: Cut, out: Path, verify_reruns: bool, now: datetime
) -> tuple[dict[str, object], dict[str, list[MetricResult]]]:
    """Compile and run the baseline on one cut; its record and its metrics by name."""
    recorded = inputs.record["baseline"]
    options = CompileOptions.model_validate(
        recorded["compile_options"] | {"cycle_start_s": cut.start_s}
    )
    run_dir = out / "runs" / f"cut_{cut.start_s:g}s"
    entry: dict[str, object] = {"run_dir": str(run_dir.relative_to(out))}
    try:
        manifest = run_baseline(
            inputs.world,
            inputs.geometry,
            inputs.timeline,
            run_dir,
            inputs.baseline.settings,
            options,
            verify_reruns=verify_reruns,
            now=now,
        )
    except (CompileError, SimulationError) as error:
        entry["error"] = str(error)
        return entry, {}
    demand_same = [f.sha256 for f in manifest.demand.files] == [
        f.sha256 for f in inputs.baseline.demand.files
    ]
    if not demand_same:
        raise InputError(f"cut {cut.start_s:g} s: demand files differ from the recorded baseline")
    program = manifest.network.signal_program
    entry |= {
        "compile_options": options.model_dump(),
        "settings_as_recorded": manifest.settings == inputs.baseline.settings,
        "demand_sha256_as_recorded": demand_same,
        "net_sha256": manifest.network.net_sha256,
        "net_as_recorded": manifest.network.net_sha256 == inputs.baseline.network.net_sha256,
        "cycle_start_s": program.cycle_start_s,
        "cycle_s": program.cycle_s,
        "phases": [{"name": p.name, "duration_s": p.duration_s} for p in program.phases],
        "valid": manifest.valid,
        "reasons": list(manifest.reasons),
        "reruns_identical": manifest.reruns_identical,
        "runs": [
            {
                "seed": r.seed,
                "loaded": r.loaded,
                "inserted": r.inserted,
                "arrived": r.arrived,
                "unfinished": r.unfinished,
                "teleports": r.teleports,
                "collisions": r.collisions,
                "emergency_braking": _safety(run_dir / "runs" / r.run_dir, "emergencyBraking"),
                "valid": r.valid,
                "tripinfo_sha256": r.tripinfo_sha256,
            }
            for r in manifest.runs
        ],
        "tripinfo_as_recorded": [r.tripinfo_sha256 for r in manifest.runs]
        == [r.tripinfo_sha256 for r in inputs.baseline.runs],
    }
    arms = [a for a in "NESW" if any(x.arm == a for x in inputs.geometry.lanes)]
    metrics: dict[str, list[MetricResult]] = defaultdict(list)
    for r in manifest.runs:
        for m in run_metrics(
            run_dir / "runs" / r.run_dir,
            experiment_id=manifest.experiment_id,
            arm="baseline",
            seed=r.seed,
            settings=manifest.settings,
            approach_arms=arms,
            root=out,
        ):
            metrics[m.metric_name].append(m)
    return entry, metrics


def run_i2(
    inputs: Inputs,
    cut: Cut,
    out: Path,
    now: datetime,
    base_metrics: Mapping[str, Sequence[MetricResult]],
) -> tuple[dict[str, object], dict[str, dict]]:
    """Re-apply I2's recorded per-phase change on one cut's baseline; validate; run; compare
    seed by seed with that cut's baseline (`paired_comparison`, the recorded verdict rule)."""
    recorded_base = [p.duration_s for p in inputs.baseline.network.signal_program.phases]
    candidate = next(c for c in inputs.experiment.candidates if c.intervention_id == CANDIDATE)
    deltas = recorded_deltas(recorded_base, candidate.outcome.phase_durations_s or ())
    cut_dir = out / "runs" / f"cut_{cut.start_s:g}s"
    manifest, network = load_baseline(cut_dir)
    program = network.manifest.signal_program
    names = [p.name for p in program.phases]
    entry: dict[str, object] = {
        "label": "robustness check of the existing validated candidate I2, not a new candidate",
        "recorded_change_s": {
            inputs.baseline.network.signal_program.phases[i].name: d for i, d in deltas.items()
        },
    }
    recorded_names = [p.name for p in inputs.baseline.network.signal_program.phases]
    mapped, how = map_change(recorded_names, names, deltas)
    entry["structure"] = how
    if mapped is None:
        entry["skipped"] = how
        return entry, {}
    if not manifest.valid:
        entry["skipped"] = "this cut's baseline is not valid, so no comparison is meaningful"
        return entry, {}
    proposal_record = next(
        p for p in inputs.experiment_record["proposals"] if p["intervention_id"] == CANDIDATE
    )
    proposal = InterventionProposal(
        intervention_id=CANDIDATE,
        type="signal_timing",
        target_program=program.program_id,
        phase_changes=tuple(
            PhaseChange(phase_name=names[i], green_s=program.phases[i].duration_s + d)
            for i, d in sorted(mapped.items())
        ),
        rationale=(
            f"robustness check: the recorded validated {CANDIDATE} per-phase change re-applied to "
            f"the cut starting at {cut.start_s:g} s; written by this script, not model-generated"
        ),
        expected_tradeoffs=(),
        preconditions=(),
        addresses_hypothesis_ids=(),
        evidence_ids=tuple(proposal_record["evidence_ids"]),
    )
    bundle = build_evidence_bundle(
        inputs.world, network.manifest, (), end_of_red_per_lane=True, conventions=()
    )
    outcome = validate_proposal(proposal, network, bundle, now=now)
    entry |= {
        "phase_changes": [c.model_dump() for c in proposal.phase_changes],
        "validation": {"status": outcome.status, "reasons": list(outcome.reasons)},
    }
    if outcome.status != "accepted":
        return entry, {}
    exp_dir = out / "i2" / f"cut_{cut.start_s:g}s"
    try:
        experiment = run_candidates(cut_dir, [outcome], exp_dir, now=now)
    except CandidateError as error:
        entry["error"] = str(error)
        return entry, {}
    record = experiment.candidates[0]
    entry |= {
        "experiment_dir": str(exp_dir.relative_to(out)),
        "valid": record.valid,
        "reasons": list(record.reasons),
        "runs": [
            {
                "seed": r.seed,
                "loaded": r.loaded,
                "arrived": r.arrived,
                "unfinished": r.unfinished,
                "teleports": r.teleports,
                "collisions": r.collisions,
                "valid": r.valid,
                "tripinfo_sha256": r.tripinfo_sha256,
            }
            for r in record.runs
        ],
        "tripinfo_as_recorded_i2": [r.tripinfo_sha256 for r in record.runs]
        == [r.tripinfo_sha256 for r in candidate.runs],
    }
    if not record.valid:  # as the recorded evaluation: a run with unfinished vehicles is excluded
        return entry, {}
    found: dict[str, list[MetricResult]] = defaultdict(list)
    for r in record.runs:
        for m in run_metrics(
            exp_dir / "candidates" / CANDIDATE / "runs" / r.run_dir,
            experiment_id=experiment.experiment_id,
            arm=CANDIDATE,
            seed=r.seed,
            settings=experiment.settings,
            approach_arms=list(base_metrics_arms(base_metrics)),
            root=out,
        ):
            found[m.metric_name].append(m)
    comparisons = {}
    for name, cand in found.items():
        c = paired_comparison(CANDIDATE, base_metrics[name], cand)
        comparisons[name] = {
            "mean_difference": c.mean_difference,
            "sd_difference": c.sd_difference,
            "ci95_difference": list(c.ci95_difference) if c.ci95_difference else None,
            "verdict": c.verdict,
        }
    return entry, comparisons


def base_metrics_arms(metrics: Mapping[str, Sequence[MetricResult]]) -> list[str]:
    """The approach arms the metric names cover (max_queue_m.<arm>), in N, E, S, W order."""
    arms = {name.split(".", 1)[1] for name in metrics if name.startswith("max_queue_m.")}
    return [a for a in "NESW" if a in arms]


# --- analysis ------------------------------------------------------------------------------


def analyse_cuts(inputs: Inputs) -> tuple[list[Cut], list[Cut], dict[str, object]]:
    """Every candidate cut with its greens, problems, score and observed counts; checked against
    the record's representative cycle."""
    timeline = inputs.timeline
    onsets = green_onsets(timeline)
    cuts = candidate_cuts(onsets)
    medians = head_medians(timeline)
    spacings = onset_spacings(onsets)
    median_length = float(np.median(spacings))
    qualifying = [c for c in cuts if not cut_problems(timeline, c)]
    scores = {c: cut_score(timeline, c, medians, median_length) for c in qualifying}
    chosen = select_cut(scores)
    rep = inputs.record["signal_estimate"]["representative_cycle"]
    agree = (
        chosen.start_s == rep["start_s"]
        and chosen.end_s == rep["end_s"]
        and len(cuts) == rep["candidate_cuts"]
        and len(qualifying) == rep["qualifying_cuts"]
        and math.isclose(scores[chosen], rep["distance_from_medians_s"])
        and [min(spacings), max(spacings)] == rep["onset_spacing_range_s"]
    )
    if not agree:
        raise InputError("cut enumeration does not reproduce the record's representative cycle")
    spolicy = inputs.record["policies"]["signal"]
    rows = []
    for cut in cuts:
        heads_in = {
            f"{a} {k}": green_in(timeline, (a, k), cut.start_s, cut.end_s)
            for a, k in timeline.heads()
        }
        onset_order = sorted(
            (i.start_s, f"{i.arm} {i.head}")
            for i in timeline.intervals
            if i.state == "green" and cut.start_s <= i.start_s < cut.end_s
        )
        queues = {}
        for t, name in onset_order:
            arm, kind = name.split(" ")
            queues[name] = queue_at(inputs.world, inputs.geometry, (arm, kind), t)
        problems = cut_problems(timeline, cut)
        rows.append(
            {
                "start_s": cut.start_s,
                "end_s": cut.end_s,
                "length_s": cut.length_s,
                "starting_arms": list(cut.arms),
                "qualifies": not problems,
                "problems": problems,
                "chosen_in_record": cut == chosen,
                "score_s": scores.get(cut),
                "green_in_cut_s": heads_in,
                "onset_order": [f"{name}@{t:g}" for t, name in onset_order],
                "all_red_s": all_red_s(timeline, cut.start_s, cut.end_s),
                "observed_crossings": crossings_in(inputs.world, cut.start_s, cut.end_s),
                "observed_queue_before_onset": queues,
            }
        )
    summary = {
        "label": "derived (fixed rule over the trajectory-derived timeline); counts observed",
        "onsets_s": {arm: list(ts) for arm, ts in onsets.items()},
        "onset_spacing": describe(spacings),
        "median_onset_spacing_s": median_length,
        "head_median_green_s": {f"{a} {k}": v for (a, k), v in medians.items()},
        "candidate_cuts": len(cuts),
        "qualifying_cuts": len(qualifying),
        "chosen": {"start_s": chosen.start_s, "end_s": chosen.end_s, "score_s": scores[chosen]},
        "reproduces_record": agree,
        "cuts": rows,
        "green_bounds": green_bounds(timeline, float(spolicy["all_red_s"])),
    }
    return cuts, sorted(qualifying, key=lambda c: c.start_s), summary


def metric_tables(
    per_cut: Mapping[str, Mapping[str, list[MetricResult]]],
    valid: Sequence[str],
    recorded_cut: str,
    names: Sequence[str],
    i2_recorded: Mapping[str, dict],
) -> dict[str, dict[str, object]]:
    """Per metric: each cut's seed statistics, the spread of cut means over valid cuts, the seed
    spread, paired differences of each cut against the recorded one, and I2's recorded effect."""
    out: dict[str, dict[str, object]] = {}
    for name in names:
        by_cut = {
            cut: describe([m.value for m in sorted(ms[name], key=lambda m: m.seed)])
            for cut, ms in per_cut.items()
            if name in ms
        }
        means = {c: float(by_cut[c]["mean"]) for c in valid if c in by_cut}
        sds = [float(by_cut[c]["sd"]) for c in valid if c in by_cut and by_cut[c]["sd"] is not None]
        paired = {}
        if recorded_cut in per_cut:
            base = per_cut[recorded_cut][name]
            for cut in valid:
                if cut == recorded_cut or cut not in per_cut:
                    continue
                p = paired_comparison(cut, base, per_cut[cut][name])
                paired[cut] = {
                    "mean_difference": p.mean_difference,
                    "ci95_difference": list(p.ci95_difference) if p.ci95_difference else None,
                    "excludes_zero": bool(
                        p.ci95_difference and (p.ci95_difference[0] > 0 or p.ci95_difference[1] < 0)
                    ),
                }
        i2 = i2_recorded.get(name, {})
        effect = abs(i2["mean_difference"]) if i2 else None
        cut_range = spread(means).get("range")
        out[name] = {
            "better": better_direction(name),
            "unit": next(iter(per_cut.values()))[name][0].unit if per_cut else None,
            "per_cut": by_cut,
            "across_valid_cuts": spread(means),
            "seed_sd": {
                "recorded_cut": by_cut.get(recorded_cut, {}).get("sd"),
                "median_over_valid_cuts": round(float(np.median(sds)), 6) if sds else None,
            },
            "paired_vs_recorded_cut": paired,
            "i2_recorded": {
                k: i2.get(k) for k in ("mean_difference", "ci95_difference", "verdict")
            },
            "cut_range_over_abs_i2_effect": (
                round(cut_range / effect, 3) if effect and cut_range is not None else None
            ),
        }
    return out


# --- report --------------------------------------------------------------------------------


def _f(x: object, digits: int = 1) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, (int, float)):
        return f"{x:.{digits}f}"
    return str(x)


def render_markdown(result: dict) -> str:
    lines = [
        "# Signal-timing sensitivity, NGSIM intersection 2 (baseline validity, investigation C)",
        "",
        f"Created {result['created_at_utc']} by `{result['script']}` at commit "
        f"`{result['git_commit']}`; SUMO {result['software']['eclipse-sumo']}, numpy "
        f"{result['software']['numpy']}. Labels: **observed** (NGSIM trajectories), **derived** "
        "(fixed rule over observed values), **assumed** (synthetic), **simulated** (SUMO).",
        "",
        "## Inputs checked against the committed record",
        "",
        *(f"- {c}" for c in result["input_checks"]),
        "",
        "## Candidate cuts (derived; counts observed)",
        "",
        f"{result['cuts']['candidate_cuts']} candidate cuts, "
        f"{result['cuts']['qualifying_cuts']} qualifying; the record's choice "
        f"({result['cuts']['chosen']['start_s']:g} s) is reproduced: "
        f"{_f(result['cuts']['reproduces_record'])}. Onset spacing (all arms): "
        f"{_f(result['cuts']['onset_spacing']['min'], 0)}-"
        f"{_f(result['cuts']['onset_spacing']['max'], 0)} s, median "
        f"{_f(result['cuts']['median_onset_spacing_s'], 0)} s.",
        "",
        "| start | arm | length | N all | N left | S all | S left | E | W | all-red | "
        "qualifies / problems |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in result["cuts"]["cuts"]:
        g = row["green_in_cut_s"]
        lines.append(
            f"| {row['start_s']:g} | {','.join(row['starting_arms'])} | {row['length_s']:g} | "
            + " | ".join(
                _f(g.get(h), 0) for h in ("N all", "N left", "S all", "S left", "E all", "W all")
            )
            + f" | {row['all_red_s']:g} | "
            + ("**yes**" if row["qualifies"] else "; ".join(row["problems"]))
            + (" (recorded choice)" if row["chosen_in_record"] else "")
            + " |"
        )
    lines += [
        "",
        "## Physical cycles: S-through onsets (derived greens; observed crossings and queues)",
        "",
        "| cycle | length | onset order | N thr / left crossings | S thr / left | E thr+left | "
        "W thr+left | N queue before N onset | N-left queue | S queue |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in result["cuts"]["cuts"]:
        if row["starting_arms"] != ["S"]:
            continue
        x, q = row["observed_crossings"], row["observed_queue_before_onset"]

        def qv(name: str, q: dict = q) -> str:
            v = q.get(name)
            return "n/a" if v is None else f"{v['queued_veh']}{'+' if v['lower_bound'] else ''}"

        lines.append(
            f"| {row['start_s']:g}-{row['end_s']:g} | {row['length_s']:g} | "
            f"{' > '.join(o.split('@')[0] for o in row['onset_order'])} | "
            f"{x.get('N through', 0)} / {x.get('N left', 0)} | "
            f"{x.get('S through', 0)} / {x.get('S left', 0)} | "
            f"{x.get('E through', 0) + x.get('E left', 0)} | "
            f"{x.get('W through', 0) + x.get('W left', 0)} | "
            f"{qv('N all')} | {qv('N left')} | {qv('S all')} |"
        )
    lines += [
        "",
        "Queues: observed queued vehicles in the head's lanes 1 s before its estimated onset; "
        "'+' marks a lower bound (queue reached the end of the observed lane).",
        "",
        "## Baseline runs per qualifying cut (simulated)",
        "",
        "| cut | cycle | net as recorded | valid | unfinished | teleports | collisions | "
        "emergency braking | reruns identical | tripinfo as recorded |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cut, entry in result["baselines"].items():
        if "error" in entry:
            lines.append(f"| {cut} | error: {entry['error']} | | | | | | | | |")
            continue
        runs = entry["runs"]
        lines.append(
            f"| {cut} | {entry['cycle_s']:g} | {_f(entry['net_as_recorded'])} | "
            f"{_f(entry['valid'])} | {sum(r['unfinished'] for r in runs)} | "
            f"{sum(r['teleports'] for r in runs)} | {sum(r['collisions'] for r in runs)} | "
            f"{sum(r['emergency_braking'] for r in runs)} | {_f(entry['reruns_identical'])} | "
            f"{_f(entry['tripinfo_as_recorded'])} |"
        )
    cuts = list(result["baselines"])
    lines += [
        "",
        "## Metric means over seeds 1-5 per cut (simulated), spread and I2's recorded effect",
        "",
        "| metric | " + " | ".join(cuts) + " | range (valid cuts) | seed SD (recorded cut) | "
        "I2 recorded mean diff [95% CI] | range / abs(I2 diff) |",
        "|---|" + "---|" * (len(cuts) + 4),
    ]
    for name, t in result["metrics"].items():
        i2 = t["i2_recorded"]
        ci = i2.get("ci95_difference")
        lines.append(
            f"| {name} | "
            + " | ".join(
                _f(t["per_cut"].get(c, {}).get("mean"))
                + ("" if result["baselines"][c].get("valid") else "*")
                for c in cuts
            )
            + f" | {_f(t['across_valid_cuts'].get('range'))} | "
            f"{_f(t['seed_sd']['recorded_cut'])} | "
            f"{_f(i2.get('mean_difference'))} [{_f(ci[0]) if ci else 'n/a'}, "
            f"{_f(ci[1]) if ci else 'n/a'}] {i2.get('verdict', '')} | "
            f"{_f(t['cut_range_over_abs_i2_effect'], 2)} |"
        )
    lines += ["", "\\* the cut's baseline is not valid; excluded from the range.", ""]
    lines += [
        "## Paired difference of each valid cut against the recorded cut (same seeds and demand)",
        "",
        "| metric | " + " | ".join(c for c in cuts if c != result["recorded_cut"]) + " |",
        "|---|" + "---|" * (len(cuts) - 1),
    ]
    for name, t in result["metrics"].items():
        cells = []
        for c in cuts:
            if c == result["recorded_cut"]:
                continue
            p = t["paired_vs_recorded_cut"].get(c)
            cells.append(
                "n/a"
                if p is None
                else f"{_f(p['mean_difference'])}{' (CI excludes 0)' if p['excludes_zero'] else ''}"
            )
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    if result.get("i2"):
        lines += [
            "",
            "## I2 re-applied per cut: robustness check of the existing validated candidate",
            "",
            "Not a new candidate: the recorded I2 per-phase change on each cut's own baseline, "
            "validated by `cairoflow_simulation.validation`, same seeds and demand.",
            "",
            "| cut | structure | greens set | validation | valid runs | "
            + " | ".join(result["metrics"])
            + " |",
            "|---|---|---|---|---|" + "---|" * len(result["metrics"]),
        ]
        for cut, item in result["i2"].items():
            entry, comps = item["entry"], item["comparisons"]
            status = entry.get("skipped") or entry.get("validation", {}).get("status", "n/a")
            if entry.get("validation", {}).get("reasons"):
                status += ": " + "; ".join(entry["validation"]["reasons"])
            if entry.get("valid") is False:
                status += " (runs not valid: " + "; ".join(entry["reasons"]) + ")"
            greens = ", ".join(
                f"{c['phase_name'].split('_', 1)[0]} {c['green_s']:g} s"
                for c in entry.get("phase_changes", [])
            )
            cells = []
            for name in result["metrics"]:
                c = comps.get(name)
                cells.append("n/a" if c is None else f"{_f(c['mean_difference'])} {c['verdict']}")
            lines.append(
                f"| {cut} | {entry.get('structure', 'n/a')} | {greens or 'n/a'} | {status} | "
                f"{_f(entry.get('valid'))} | " + " | ".join(cells) + " |"
            )
        lines += [
            "",
            "Recorded I2 (cut "
            f"{result['recorded_cut']}): "
            + "; ".join(
                f"{n} {_f(t['i2_recorded']['mean_difference'])} {t['i2_recorded']['verdict']}"
                for n, t in result["metrics"].items()
            ),
        ]
    lines += ["", "## Limitations", "", *(f"- {x}" for x in result["limitations"]), ""]
    return "\n".join(lines)


LIMITATIONS = (
    "Every cut is a slice of one trajectory-derived timeline; no published timing plan or signal "
    "head was read. Yellow 4 s, start-up 2 s, clearance 1 s and minimum green 5 s are assumed.",
    "Each cut is repeated as fixed time; none represents actuated or coordinated behaviour.",
    "Only qualifying cuts can be compiled; non-qualifying cuts are listed with the reason, not "
    "repaired.",
    "Five seeds per cut: seed SDs and 95% intervals are wide; differences are simulated, not "
    "observed.",
    "Right turn on red stays on (assumed, as recorded).",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, help="new output directory (never overwritten)")
    parser.add_argument("--i2", action="store_true", help="also re-apply I2's recorded change")
    parser.add_argument("--no-reruns", action="store_true", help="skip the rerun identity check")
    args = parser.parse_args(argv)
    out_path = Path(args.out).expanduser().resolve()
    # A new directory under experiments/runs is fine; the recorded runs and records are not.
    # The roots are resolved too: in a linked checkout the run directories are symlinks into the
    # main checkout, and an unresolved root let a refused --out land in the main tree's records.
    baseline_dir = REPO / json.loads(RECORD.read_text(encoding="utf-8"))["baseline"]["run_dir"]
    for forbidden in (baseline_dir, EXPERIMENT_RUN, REPO / "data", REPO / "cache"):
        if out_path.is_relative_to(forbidden.resolve()):
            print(f"{out_path} is inside {forbidden}; choose another --out", file=sys.stderr)
            return 2
    if out_path.exists():  # refused before any work; prepare_out refuses again when creating it
        print(f"{out_path} exists; outputs are never overwritten", file=sys.stderr)
        return 2
    try:
        inputs = load_inputs()
    except (InputError, FileNotFoundError) as error:
        print(f"input check failed: {error}", file=sys.stderr)
        return 2
    try:
        out = prepare_out(out_path)
    except FileExistsError as error:
        print(error, file=sys.stderr)
        return 2
    now = datetime.now(UTC).replace(microsecond=0)
    _, qualifying, cut_summary = analyse_cuts(inputs)
    recorded_start = float(inputs.record["baseline"]["cycle_start_s"])
    recorded_cut = f"cut_{recorded_start:g}s"

    baselines: dict[str, dict[str, object]] = {}
    per_cut: dict[str, dict[str, list[MetricResult]]] = {}
    for cut in qualifying:
        key = f"cut_{cut.start_s:g}s"
        print(f"{key}: running baseline", flush=True)
        entry, metrics = run_cut(inputs, cut, out, not args.no_reruns, now)
        baselines[key] = entry
        if metrics:
            per_cut[key] = metrics
    valid = [k for k, e in baselines.items() if e.get("valid")]
    arms = [a for a in "NESW" if any(x.arm == a for x in inputs.geometry.lanes)]
    i2_recorded = {
        c["metric_name"]: c for c in inputs.evaluation["comparisons"] if c["candidate"] == CANDIDATE
    }
    tables = metric_tables(per_cut, valid, recorded_cut, metric_names(arms), i2_recorded)

    i2: dict[str, dict] = {}
    if args.i2:
        for cut in qualifying:
            key = f"cut_{cut.start_s:g}s"
            if key not in per_cut:
                continue
            print(f"{key}: I2 robustness check", flush=True)
            entry, comparisons = run_i2(inputs, cut, out, now, per_cut[key])
            i2[key] = {"entry": entry, "comparisons": comparisons}

    result = {
        "script": "scripts/signal_sensitivity_ngsim_int2.py",
        "created_at_utc": now.isoformat(),
        "git_commit": subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip(),
        "software": {"numpy": np.__version__, "eclipse-sumo": metadata.version("eclipse-sumo")},
        "input_checks": inputs.checks,
        "recorded_cut": recorded_cut,
        "settings": inputs.baseline.settings.model_dump(),
        "compile_options_recorded": inputs.record["baseline"]["compile_options"],
        "cuts": cut_summary,
        "baselines": baselines,
        "metrics": tables,
        "i2": i2,
        "limitations": list(LIMITATIONS),
    }
    write_new(out / "sensitivity.json", json.dumps(result, indent=2) + "\n")
    write_new(out / "sensitivity.md", render_markdown(result))
    print(f"wrote {out / 'sensitivity.json'} and {out / 'sensitivity.md'}")
    invalid = [k for k in baselines if not baselines[k].get("valid")]
    print(f"valid baselines: {len(valid)} of {len(baselines)}; not valid: {invalid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
