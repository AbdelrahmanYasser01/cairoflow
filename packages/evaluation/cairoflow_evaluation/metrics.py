"""Simulated metrics from SUMO output files: the only source of numbers in a report.

Measurement window: [warm-up, warm-up + horizon) in simulation seconds (RunSettings). Per run:

- `mean_delay_s` (s, lower is better): mean of `timeLoss + departDelay` from tripinfo.xml over
  vehicles that depart in the window. `timeLoss` is SUMO's time lost against free-flow driving
  (waiting at red included); `departDelay` adds time spent waiting to enter the network.
- `throughput_veh_per_h` (veh/h, higher is better): vehicles arriving in the window, per hour.
- `max_queue_m.<arm>` and `mean_queue_m.<arm>` (m, lower is better): per step, the longest
  `queueing_length` among the arm's approach lanes in queue.xml (0 when none is listed), then the
  maximum and the mean over the window's steps.

Every value records its source file, the SHA-256 of its content with XML comments stripped (SUMO
puts a generation timestamp in a comment) and the calculation version.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

from cairoflow_contracts.experiment import MetricResult, RunSettings
from cairoflow_simulation.network import content_sha256

CALCULATION_VERSION = "0.1.0"
LOWER_IS_BETTER = ("mean_delay_s", "max_queue_m", "mean_queue_m")


class MetricError(ValueError):
    """An output file is missing or cannot support a metric."""


def better_direction(metric_name: str) -> str:
    return "lower" if metric_name.split(".", 1)[0] in LOWER_IS_BETTER else "higher"


def metric_names(arms: Sequence[str]) -> tuple[str, ...]:
    """The fixed metric list for a network whose approach arms are `arms`."""
    return (
        "mean_delay_s",
        "throughput_veh_per_h",
        *(f"max_queue_m.{arm}" for arm in arms),
        *(f"mean_queue_m.{arm}" for arm in arms),
    )


def run_metrics(
    run_dir: Path,
    *,
    experiment_id: str,
    arm: str,
    seed: int,
    settings: RunSettings,
    approach_arms: Sequence[str],
    root: Path,
) -> list[MetricResult]:
    """All metrics of one run; `root` is what source paths are recorded relative to."""
    start, end = settings.warmup_s, settings.warmup_s + settings.horizon_s
    tripinfo, queue = run_dir / "tripinfo.xml", run_dir / "queue.xml"
    for path in (tripinfo, queue):
        if not path.is_file():
            raise MetricError(f"{path} is missing")

    def result(name: str, value: float, unit: str, n: int, path: Path) -> MetricResult:
        return MetricResult(
            experiment_id=experiment_id,
            arm=arm,
            seed=seed,
            metric_name=name,
            value=round(value, 6),
            unit=unit,
            window_s=(start, end),
            sample_size=n,
            source_output_file=os.path.relpath(path, root),
            # SUMO writes a generation timestamp in an XML comment; hash the content only.
            source_sha256=content_sha256(path, strip_comments=True),
            calculation_version=CALCULATION_VERSION,
        )

    trips = ET.parse(tripinfo).getroot().findall("tripinfo")
    delays = [
        float(t.get("timeLoss", 0)) + float(t.get("departDelay", 0))
        for t in trips
        if start <= float(t.get("depart", -1)) < end
    ]
    if not delays:
        raise MetricError(f"no vehicle departed in [{start:g}, {end:g}) s in {run_dir}")
    arrived = sum(1 for t in trips if start <= float(t.get("arrival", -1)) < end)
    results = [
        result("mean_delay_s", sum(delays) / len(delays), "s", len(delays), tripinfo),
        result(
            "throughput_veh_per_h", arrived * 3600 / settings.horizon_s, "veh/h", arrived, tripinfo
        ),
    ]

    series: dict[str, list[float]] = {a: [] for a in approach_arms}
    for _, element in ET.iterparse(queue):
        if element.tag != "data":
            continue
        t = float(element.get("timestep", -1))
        if start <= t < end:
            longest = dict.fromkeys(approach_arms, 0.0)
            for lane in element.iter("lane"):
                edge = lane.get("id", "").rsplit("_", 1)[0]
                lane_arm, _, kind = edge.partition("_")
                if kind == "in" and lane_arm in longest:
                    longest[lane_arm] = max(
                        longest[lane_arm], float(lane.get("queueing_length", 0))
                    )
            for a, value in longest.items():
                series[a].append(value)
        element.clear()
    for a in approach_arms:
        if not series[a]:
            raise MetricError(f"queue.xml in {run_dir} has no steps in the window")
    results += [
        result(f"max_queue_m.{a}", max(series[a]), "m", len(series[a]), queue)
        for a in approach_arms
    ]
    results += [
        result(f"mean_queue_m.{a}", sum(series[a]) / len(series[a]), "m", len(series[a]), queue)
        for a in approach_arms
    ]
    return results
