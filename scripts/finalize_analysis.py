"""Turn a camera analysis and its held-out gate result into the analysis people see.

A run writes every value as ungated. This writes a NEW analysis (analyses are immutable) where each
value carries what the gate decided: a passed check marks it gate_pass, a failed one removes it.
Speed is computed and stored here only if its check passed, because a run stores none (ADR-018).
Measures no gate covers stay ungated with their reason, and that includes stopped share: it needs
those speeds, but the speed check scores moving vehicles only. It refuses anything but a held-out
result for this exact analysis.

    uv run --frozen python scripts/finalize_analysis.py --analysis data/analyses/RUN \
        --gate-result RESULTS.json --id NEW_ID [--out data/sample/analyses]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from cairoflow_contracts.analysis_record import (
    AnalysisRecord,
    ArtifactRef,
    DecisionRef,
    GateOutcome,
    Metric,
    MetricSeries,
    MovementCell,
    StageReceipt,
)
from cairoflow_contracts.evidence import Provenance
from cairoflow_perception.analysis_store import (
    GROUND_SCALE,
    NO_GROUND,
    POINT_INTERPOLATED,
    POINT_OUTSIDE_VALID_REGION,
    RECORD_FILE,
    TRAJECTORIES_FILE,
    TrajectoryColumns,
    decode_trajectories,
    load_record,
    load_tracks,
    verify_analysis,
    write_analysis,
)
from cairoflow_perception.local.analytics import centred_speed

REPO = Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = REPO / "data/sample/analyses/ref-ngsim-int2-full"
STOPPED_M_S = 0.5  # as the reference analysis
ENTERING = "entering_count_error_share"
TURNING = "turning_matrix_agreement"
SPEED = "speed_abs_error_median_m_s"
PLACEMENT = ("across_heading_p90_m", "lane_agreement")
STOPPED = "stopped.share"
ARM_METRICS = tuple(f"vehicles.arm.{arm}" for arm in "nesw")
CHECK_OF_METRIC: Mapping[str, str] = {
    "flow.entering": ENTERING,
    **{metric_id: ENTERING for metric_id in ARM_METRICS},
    "speed.mean_moving": SPEED,
}
NO_GATE_FOR_CLASS = (
    "no gate covers vehicle classes: NGSIM publishes a v_class field, but the trajectory columns "
    "used here leave it out, so classes weren't compared"
)
NO_GATE_FOR_STOPPED = (
    "no gate covers stopped time: the speed check scored only vehicles moving at 2 m/s or more"
)
NO_GATE_FOR_TOTAL = (
    "no gate covers this total: recall says how many vehicles were found, not how many tracks "
    "there should be, and the total includes tracks that restarted mid-view"
)
DECISION_SUMMARY = (
    "No intervention selected or recommended: every candidate worsens at least one metric, and no "
    "baseline is validated (two timelines partly, two not assessed)."
)


class FinalizeError(ValueError):
    """The inputs can't be finalized honestly."""


def load_outcomes(result: Mapping, record: AnalysisRecord) -> tuple[GateOutcome, ...]:
    if result.get("analysis_id") != record.analysis_id:
        raise FinalizeError(f"the gate result is for {result.get('analysis_id')}")
    if result.get("split") != "held_out":
        raise FinalizeError("only a held-out result can decide what is shown")
    outcomes = tuple(GateOutcome.model_validate(o) for o in result["outcomes"])
    if not outcomes:
        raise FinalizeError("the gate result has no outcomes")
    return outcomes


def failed_reason(check: GateOutcome) -> str:
    return (
        f"withheld: failed {check.gate_id} on {check.metric}, {check.measured} against "
        f"{check.comparator} {check.threshold} (n {check.n})"
    )


def speeds_for(index, columns: TrajectoryColumns, fps: float) -> np.ndarray:
    """Per-row speed in m/s from ground positions, NaN where a track has none."""
    speed = np.full(len(columns), np.nan)
    gx, gy = columns.gx.astype(float), columns.gy.astype(float)
    for track in index.tracks:
        rows = slice(track.first_row, track.first_row + track.n)
        if np.any(columns.gx[rows] == NO_GROUND):
            continue
        speed[rows] = centred_speed(gx[rows] * GROUND_SCALE, gy[rows] * GROUND_SCALE, 1 / fps)[0]
    return speed


def speed_summary(speed: np.ndarray, columns: TrajectoryColumns) -> tuple[float, float]:
    """(mean moving speed, stopped share) over observed rows inside the measured image."""
    skip = POINT_INTERPOLATED | POINT_OUTSIDE_VALID_REGION
    usable = np.isfinite(speed) & ((columns.point_flags & skip) == 0)
    values = speed[usable]
    if not len(values):
        raise FinalizeError("no observed row has a speed")
    moving = values[values > STOPPED_M_S]
    return round(float(moving.mean()), 2), round(float((values <= STOPPED_M_S).mean()), 3)


def gated_metrics(
    metrics: Sequence[Metric],
    by_check: Mapping[str, GateOutcome],
    released: Mapping[str, float],
) -> tuple[Metric, ...]:
    out = []
    for metric in metrics:
        check = by_check.get(CHECK_OF_METRIC.get(metric.metric_id, ""))
        if metric.metric_id.startswith("vehicles.class."):
            out.append(metric.model_copy(update={"reason": NO_GATE_FOR_CLASS}))
        elif metric.metric_id == "vehicles.total":
            update = {"reason": NO_GATE_FOR_TOTAL, "label": "Counted tracks", "unit": "tracks"}
            out.append(metric.model_copy(update=update))
        elif metric.metric_id == STOPPED and STOPPED in released:
            update = {"status": "ungated", "value": released[STOPPED]}
            out.append(metric.model_copy(update={**update, "reason": NO_GATE_FOR_STOPPED}))
        elif check is None:
            out.append(metric)
        elif not check.passed:
            out.append(
                metric.model_copy(
                    update={
                        "status": "gate_fail",
                        "value": None,
                        "gate_id": check.gate_id,
                        "reason": failed_reason(check),
                    }
                )
            )
        else:
            value = released.get(metric.metric_id, metric.value)
            out.append(
                metric.model_copy(
                    update={
                        "status": "gate_pass",
                        "value": value,
                        "gate_id": check.gate_id,
                        "reason": None,
                    }
                )
            )
    return tuple(out)


def gated_series(
    series: Sequence[MetricSeries], check: GateOutcome | None
) -> tuple[MetricSeries, ...]:
    if check is None:
        return tuple(series)
    if check.passed:
        update = {"status": "gate_pass", "gate_id": check.gate_id, "reason": None}
        return tuple(s.model_copy(update=update) for s in series)
    return tuple(
        s.model_copy(
            update={
                "status": "gate_fail",
                "gate_id": check.gate_id,
                "reason": failed_reason(check),
                "values": tuple(None for _ in s.values),
            }
        )
        for s in series
    )


def gated_movements(
    cells: Sequence[MovementCell],
    check: GateOutcome | None,
    reference: Mapping[tuple[str, str], int],
) -> tuple[MovementCell, ...]:
    out = []
    for cell in cells:
        update: dict = {"reference_count": reference.get((cell.approach, cell.exit_arm))}
        if check is not None and check.passed:
            update |= {"status": "gate_pass", "gate_id": check.gate_id, "reason": None}
        elif check is not None:
            update |= {
                "status": "gate_fail",
                "gate_id": check.gate_id,
                "reason": failed_reason(check),
                "count": None,
            }
        out.append(cell.model_copy(update=update))
    return tuple(out)


def checked_windows(blocks: Sequence[int], earlier: Sequence) -> tuple[tuple[float, float], ...]:
    """Windows where a gate has measured placement: the held-out blocks, plus what was there."""
    windows = {(float(a), float(b)) for a, b in earlier}
    windows |= {(120.0 + 60 * block, 180.0 + 60 * block) for block in blocks}
    return tuple(sorted(windows))


def rewrite_disclosures(
    disclosures: Sequence[str],
    outcomes: Sequence[GateOutcome],
    *,
    speed_passed: bool,
    placement_passed: bool,
    detector_licence: str | None,
) -> tuple[str, ...]:
    gate_id = outcomes[0].gate_id
    passed = sum(1 for o in outcomes if o.passed)
    kept = []
    for line in disclosures:
        if line.startswith("Every value is ungated"):
            continue
        if line.startswith("Ground positions are approximated") and speed_passed:
            continue
        if line.startswith("Part of this run is outside the validated window") and placement_passed:
            continue
        kept.append(line)
    lead = [
        f"Gate {gate_id}: {passed} of {len(outcomes)} checks passed on held-out 60 s blocks the "
        "detector and tracker were not tuned on. The camera placement was not held out: it was "
        "fitted on points Claude read from video frames, some from those same blocks, so the "
        "position and lane checks are not independent of that fit. This is agreement with the "
        "published NGSIM trajectories, which share this video, and not accuracy."
    ]
    lead += [
        f"{o.metric} failed: {o.measured} against a limit of {o.comparator} {o.threshold}. "
        "The limit was fixed before the read and was not moved, so what it covers is withheld."
        for o in outcomes
        if not o.passed
    ]
    if any(o.metric == TURNING and o.passed for o in outcomes):
        lead.append(
            "Turning agreement was checked pooled over the held-out blocks, not movement by "
            "movement, so no single movement count here has been checked on its own."
        )
    if speed_passed:
        lead.append(
            "Ground positions are approximated through a camera homography, and along-road "
            "distances from it are not accepted (ADR-018). Speed is shown because, on vehicles "
            "moving at 2 m/s or more, it agreed with the published trajectories within the gate's "
            "limit. That agreement is empirical, not a property of the homography, and it is not a "
            "calibrated speed measurement."
        )
    if detector_licence and "AGPL" in detector_licence:
        lead.append(
            "This run used the optional AGPL-3.0 detector, chosen because it found far more "
            "vehicles on this overhead view. The repository's default detector is Apache-2.0."
        )
    kept.append(
        "The published trajectories don't cover every approach in view, so recall can't show "
        "misses there. A visual check found stopped vehicles the detector missed."
    )
    return (*lead, *kept)


def finalize(
    analysis: Path,
    result: Mapping,
    result_sha256: str,
    new_id: str,
    out: Path,
    reference_dir: Path = DEFAULT_REFERENCE,
    decision: DecisionRef | None = None,
) -> AnalysisRecord:
    record = verify_analysis(analysis)
    if record.data_origin != "camera_cv":
        raise FinalizeError(f"{record.analysis_id} is not camera output")
    outcomes = load_outcomes(result, record)
    by_check = {o.metric: o for o in outcomes}
    index = load_tracks(analysis)
    _, columns = decode_trajectories((analysis / TRAJECTORIES_FILE).read_bytes())

    speed_check = by_check.get(SPEED)
    speed_passed = bool(speed_check and speed_check.passed)
    released: dict[str, float] = {}
    if speed_passed:
        speed = speeds_for(index, columns, record.fps)
        released["speed.mean_moving"], released["stopped.share"] = speed_summary(speed, columns)
        stored = np.clip(np.rint(np.nan_to_num(speed) * 100), 0, 0xFFFE)
        columns = TrajectoryColumns(
            **{
                **asdict(columns),
                "speed_cm_s": np.where(np.isfinite(speed), stored, 0xFFFF).astype("<u2"),
            }
        )

    reference = load_record(reference_dir)
    reference_counts = {
        (c.approach, c.exit_arm): c.count for c in reference.movements if c.count is not None
    }
    placement_passed = all(by_check[m].passed for m in PLACEMENT if m in by_check)
    calibration = record.calibration
    if placement_passed:
        calibration = calibration.model_copy(
            update={
                "valid_windows_s": checked_windows(
                    result.get("blocks", ()), calibration.valid_windows_s
                )
            }
        )
    detect = next((s for s in record.stages if s.stage == "detect"), None)
    inherited = record.decision_ref or reference.decision_ref
    if inherited is not None:
        inherited = inherited.model_copy(update={"summary": DECISION_SUMMARY})
    now = datetime.now(UTC).replace(microsecond=0)
    gate_id = outcomes[0].gate_id
    final = record.model_copy(
        update={
            "analysis_id": new_id,
            "title": f"{record.title}, checked by {gate_id}",
            "mode": "precomputed",
            "created_at_utc": now,
            "gates": outcomes,
            "calibration": calibration,
            "metrics": gated_metrics(record.metrics, by_check, released),
            "series": gated_series(record.series, by_check.get(ENTERING)),
            "movements": gated_movements(record.movements, by_check.get(TURNING), reference_counts),
            "stages": (
                *record.stages,
                StageReceipt(
                    stage="gate",
                    tool="cairoflow_evaluation.cv_agreement",
                    version=gate_id,
                    licence="MIT",
                    items_out=len(outcomes),
                ),
            ),
            "decision_ref": decision or inherited,
            "disclosures": rewrite_disclosures(
                record.disclosures,
                outcomes,
                speed_passed=speed_passed,
                placement_passed=placement_passed,
                detector_licence=detect.licence if detect else None,
            ),
            "provenance": Provenance(
                kind="approximated",
                produced_by="scripts/finalize_analysis.py",
                produced_at_utc=now,
                input_sha256=(
                    hashlib.sha256((analysis / RECORD_FILE).read_bytes()).hexdigest(),
                    result_sha256,
                ),
            ),
        }
    )
    extras: list[tuple[ArtifactRef, bytes | None]] = []
    for artifact in record.artifacts:
        if artifact.role in ("trajectories", "tracks"):
            continue
        if artifact.bundled:
            extras.append((artifact, (analysis / artifact.path).read_bytes()))
        else:
            hint = (artifact.rebuild_hint or "").replace(record.analysis_id, new_id)
            extras.append((artifact.model_copy(update={"rebuild_hint": hint}), None))
    return write_analysis(out / new_id, final, index.tracks, columns, extras)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--gate-result", type=Path, required=True)
    parser.add_argument("--id", required=True, help="the new analysis id")
    parser.add_argument("--out", type=Path, default=REPO / "data/sample/analyses")
    args = parser.parse_args(argv)
    data = args.gate_result.read_bytes()
    try:
        final = finalize(
            args.analysis, json.loads(data), hashlib.sha256(data).hexdigest(), args.id, args.out
        )
    except FinalizeError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    for metric in final.metrics:
        print(f"{metric.metric_id}: {metric.value} {metric.unit} [{metric.status}]")
    print(f"wrote {args.out / args.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
