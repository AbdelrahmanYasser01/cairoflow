"""Experiment eligibility: which candidates may be selected, which may be compared, and why not.

Every class comes from SUMO's own records (RunRecord, tripinfo.xml, queue.xml), never from model
output. Every arm runs under the same termination policy: demand over [0, warm-up + horizon),
metrics over [warm-up, warm-up + horizon), then a drain with no new demand, then a fixed end. A
run is

- `integrity_failure` when anything teleported or collided. The run is not a valid simulation of
  the plan: a collision is a model artefact, and a vehicle teleported after standing too long is
  often a jam the plan itself caused, so the outputs may hide harm. It is not compared, and its
  runs need inspecting;
- `incomplete` when nothing broke but vehicles were still in the network, or still waiting to
  enter it, at the end. The drain adds no demand, so the plan did not clear the demand it was
  given within the drain: a result of the intervention (typically an oversaturated movement),
  not missing data;
- `complete` otherwise.

A candidate is `invalid_simulation` when any seed failed integrity, `incomplete` when any seed is
incomplete, and `compared` only when every seed is complete. Only a compared candidate can be
selected. An incomplete candidate is still compared, on every seed, on the metrics that do not
need its trips to finish (window throughput and queues: a later end leaves them unchanged), so
its harm stays visible. Its mean delay is not compared: an unfinished vehicle's timeLoss stops
growing at the end, so the delay of an incomplete run is only a lower bound, and comparing its
complete seeds alone would keep the seeds that favour it. Its unfinished vehicles are listed per
movement, as evidence of what it did not clear.

A queue that reaches the upstream end of its lane cannot grow in queue.xml, and vehicles waiting
to enter are not in it at all, so that arm's queue metrics are then lower bounds. Such lanes are
listed per seed for every arm, the baseline included (`censored_lanes`).

Rules 0.3.x (ADR-024) add the same censoring where vehicles wait to enter, as the replay
baseline's do at the mid-lane point where they were first observed: a vehicle that cannot be
inserted waits off the network, where queue.xml cannot see it, although the lane is far from
full. `entry_blocked` records, per seed and arm, the vehicle-seconds of such waiting in the
window, from tripinfo's insertion delays (0.3.1). Nothing is extrapolated: the wait marks the
arm's queue values as lower bounds.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

from cairoflow_contracts.experiment import (
    CandidateEligibility,
    CandidateRecord,
    CandidateStatus,
    RunClass,
    RunRecord,
    RunSettings,
    UnfinishedMovement,
)
from cairoflow_simulation.network import content_sha256

RULES_VERSION = "0.3.1"
COMPLETION_METRICS = ("mean_delay_s",)  # need every trip to finish; others use the window only
# A queue within one passenger car (5.0 m long, 2.5 m gap: demand.VEHICLE_TYPES) of its lane's
# upstream end has filled the lane.
FULL_LANE_MARGIN_M = 7.5
# Insertion delays below two 1 s steps come from fractional depart times and the step itself.
ENTRY_DELAY_S = 2.0


class EligibilityError(ValueError):
    """A run record contradicts itself or its output files."""


def classify_run(run: RunRecord) -> RunClass:
    """The class of one run, checked against the validity the runner recorded."""
    if run.teleports or run.collisions:
        found: RunClass = "integrity_failure"
    elif run.unfinished or run.inserted != run.loaded or run.arrived != run.loaded:
        found = "incomplete"
    else:
        found = "complete"
    if run.valid != (found == "complete"):
        raise EligibilityError(
            f"seed {run.seed}: recorded valid={run.valid} but the run is {found}"
        )
    return found


def unfinished_movements(tripinfo: Path, seed: int) -> tuple[UnfinishedMovement, ...]:
    """Vehicles without an arrival in `tripinfo` (written with write-unfinished), per movement.

    The movement is the vehicle id's prefix (`<approach><exit>.<n>`, cairoflow_simulation.demand).
    Vehicles still waiting to enter are not in tripinfo.xml; RunRecord counts them.
    """
    groups: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for trip in ET.parse(tripinfo).getroot().iter("tripinfo"):
        if float(trip.get("arrival", -1)) >= 0:
            continue
        delay = float(trip.get("timeLoss", 0)) + float(trip.get("departDelay", 0))
        groups[trip.get("id", "").split(".", 1)[0]].append((float(trip.get("depart", -1)), delay))
    return tuple(
        UnfinishedMovement(
            seed=seed,
            movement=movement,
            vehicles=len(trips),
            first_depart_s=min(depart for depart, _ in trips),
            delay_lower_bound_range_s=(
                round(min(delay for _, delay in trips), 6),
                round(max(delay for _, delay in trips), 6),
            ),
        )
        for movement, trips in sorted(groups.items())
    )


def approach_lane_lengths(net_path: Path) -> dict[str, float]:
    """Length (m) of every approach lane (`<arm>_in_<n>`) in a compiled network."""
    return {
        lane.get("id", ""): float(lane.get("length", 0))
        for edge in ET.parse(net_path).getroot().iter("edge")
        if not edge.get("function") and edge.get("id", "").endswith("_in")
        for lane in edge.iter("lane")
    }


def censored_lanes(
    queue_xml: Path, lengths: Mapping[str, float], settings: RunSettings
) -> tuple[str, ...]:
    """Approach lanes whose queue came within FULL_LANE_MARGIN_M of their upstream end at some
    step of the measurement window."""
    start, end = settings.warmup_s, settings.warmup_s + settings.horizon_s
    full: set[str] = set()
    for _, element in ET.iterparse(queue_xml):
        if element.tag != "data":
            continue
        if start <= float(element.get("timestep", -1)) < end:
            for lane in element.iter("lane"):
                name = lane.get("id", "")
                length = lengths.get(name)
                if length is not None and float(lane.get("queueing_length", 0)) >= (
                    length - FULL_LANE_MARGIN_M
                ):
                    full.add(name)
        element.clear()
    return tuple(sorted(full))


def run_censored_lanes(
    runs: tuple[RunRecord, ...],
    runs_dir: Path,
    lengths: Mapping[str, float],
    settings: RunSettings,
) -> dict[int, tuple[str, ...]]:
    """Seed -> censored approach lanes, for the seeds that have any."""
    found = {run.seed: censored_lanes(runs_dir / run.run_dir / "queue.xml", lengths, settings)
             for run in runs}  # fmt: skip
    return {seed: lanes for seed, lanes in found.items() if lanes}


def lane_queues(queue_xml: Path, start: float, end: float) -> dict[float, dict[str, float]]:
    """Step -> lane -> `queueing_length` (m) for the steps of queue.xml in [start, end)."""
    steps: dict[float, dict[str, float]] = {}
    for _, element in ET.iterparse(queue_xml):
        if element.tag != "data":
            continue
        t = float(element.get("timestep", -1))
        if start <= t < end:
            steps[t] = {lane.get("id", ""): float(lane.get("queueing_length", 0))
                        for lane in element.iter("lane")}  # fmt: skip
        element.clear()
    return steps


def entry_blocking(
    tripinfo: Path, lengths: Mapping[str, float], settings: RunSettings
) -> dict[str, float]:
    """Arm -> vehicle-seconds in the window that its vehicles waited to enter the network.

    Read from SUMO's own record of each insertion (tripinfo `departDelay`, rules 0.3.1): every
    vehicle on an approach lane whose insertion was delayed by at least ENTRY_DELAY_S counts its
    wait inside the window. A vehicle waiting to enter is not in queue.xml at all, whatever
    stopped it: a standing queue at its entry point, or moving traffic with no gap. (Rules 0.3.0
    counted only waits while queue.xml showed a standing queue at the entry; queue.xml's
    `queueing_length` counts standing vehicles only, so that missed about half of the waiting.)
    Vehicles still waiting at the end are not in tripinfo; such a run is incomplete anyway.
    """
    start, end = settings.warmup_s, settings.warmup_s + settings.horizon_s
    found: dict[str, float] = defaultdict(float)
    for trip in ET.parse(tripinfo).getroot().iter("tripinfo"):
        delay = float(trip.get("departDelay", 0))
        lane = trip.get("departLane", "")
        if delay < ENTRY_DELAY_S or lane not in lengths:
            continue
        depart = float(trip.get("depart", -1))
        wait = (max(depart - delay, start), min(depart, end))
        if wait[0] < wait[1]:
            found[lane.split("_", 1)[0]] += wait[1] - wait[0]
    return {arm: round(seconds, 3) for arm, seconds in sorted(found.items())}


def run_entry_blocked(
    runs: tuple[RunRecord, ...],
    runs_dir: Path,
    lengths: Mapping[str, float],
    settings: RunSettings,
) -> dict[int, dict[str, float]]:
    """Seed -> arm -> waiting vehicle-seconds (entry_blocking), for the seeds that have any."""
    found = {run.seed: entry_blocking(runs_dir / run.run_dir / "tripinfo.xml", lengths, settings)
             for run in runs}  # fmt: skip
    return {seed: arms for seed, arms in found.items() if arms}


def candidate_eligibility(
    record: CandidateRecord,
    runs_dir: Path,
    settings: RunSettings,
    lengths: Mapping[str, float] | None = None,
) -> CandidateEligibility:
    """Classify every run of one candidate; `runs_dir` holds its `seed_<n>` directories.

    Every run's tripinfo.xml must hash as its RunRecord says. With `lengths` (approach lane ->
    m), lanes whose queue filled them are listed too.
    """
    classes = {run.seed: classify_run(run) for run in record.runs}
    unfinished: list[UnfinishedMovement] = []
    reasons: list[str] = []
    for run in record.runs:
        path = runs_dir / run.run_dir / "tripinfo.xml"
        if content_sha256(path, strip_comments=True) != run.tripinfo_sha256:
            raise EligibilityError(f"{path} changed after the run")
        if classes[run.seed] == "complete":
            continue
        found = unfinished_movements(path, run.seed)
        unfinished += found
        if classes[run.seed] == "integrity_failure":
            reasons.append(f"seed {run.seed}: {'; '.join(run.reasons)}")
            continue
        waiting = run.loaded - run.inserted
        parts = ", ".join(f"{m.movement} {m.vehicles}" for m in found)
        reasons.append(
            f"seed {run.seed}: {sum(m.vehicles for m in found)} vehicles still in the network "
            f"({parts or 'none'}) and {waiting} not yet inserted at {settings.end_s:g} s, after "
            f"a {settings.drain_s:g} s drain with no new demand"
        )
    status: CandidateStatus = (
        "invalid_simulation"
        if "integrity_failure" in classes.values()
        else "incomplete"
        if "incomplete" in classes.values()
        else "compared"
    )
    if record.valid != (status == "compared"):
        raise EligibilityError(
            f"{record.intervention_id}: recorded valid={record.valid} but its runs are {status}"
        )
    return CandidateEligibility(
        candidate=record.intervention_id,
        status=status,
        run_classes=classes,
        not_inserted={run.seed: run.loaded - run.inserted for run in record.runs},
        unfinished=tuple(unfinished),
        reasons=tuple(reasons),
        censored_lanes=(
            run_censored_lanes(record.runs, runs_dir, lengths, settings)
            if lengths is not None and status != "invalid_simulation"
            else {}
        ),
        entry_blocked=(
            run_entry_blocked(record.runs, runs_dir, lengths, settings)
            if lengths is not None and status != "invalid_simulation"
            else {}
        ),
    )
