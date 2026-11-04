"""How far a camera analysis agrees with the published NGSIM trajectories, per 60 s block.

What is measured is agreement with a correlated reference (same video; the camera placement was
fitted to it), never accuracy.

Metrics, by the name a gate file uses:

    vehicle_recall              matched in-view reference vehicle-frames / in-view reference
                                vehicle-frames (local/scoring.py); n = the denominator
    precision_proxy             rows matched to a reference / rows with a ground position
                                (local/scoring.py); n = the denominator
    across_heading_p90_m        p90 of |offset across the reference's heading| over paired track
                                points (camera_gate_v3); n = paired points
    lane_agreement              share of paired points in the same lane as the reference
                                (camera_gate_v3 B); n = paired points with a lane on both sides
    entering_count_error_share  sum over blocks and arms of |camera - reference| entering counts,
                                over the reference's entering count; n = that count
    turning_matrix_agreement    sum over blocks and cells of min(camera, reference), over the larger
                                of the two totals; n = that total
    speed_abs_error_median_m_s  median |camera - reference| speed on paired points whose reference
                                moves at >= 2 m/s; n = those points. The analysis stores no speed
                                (ADR-018), so the camera side is recomputed here from the ground
                                positions inside the block, with analytics.centred_speed

Only observed rows count: interpolated ones are the tracker's guess. The camera side is the tracks
of the analysis; the reference side is every published vehicle at the same frames, restricted to
what camera 2 can see, so a vehicle outside the image is not a miss. Both sides are classified from
their own points inside the block by the same rule (local/zones.py), so a count here can differ
from `record.json`, which classifies a track over the whole run, and from a reference analysis,
which is not limited to the camera's view.

The two entering counts are not the same definition. A camera track enters only if it is first seen
near the edge of the usable image or at the start of the block; the reference enters every vehicle
it holds. A queued vehicle the detector first saw moving is in the reference and not in the camera
count, so `mid_view_by_arm` reports what the camera rule left out.

Blocks. block = floor((source_s - 120) / 60) on [120, 1020); even blocks are dev, odd blocks are
held out. A split only ever reads its own rows.

Gate file (data/gates/<name>.json, committed):

    {
      "gate_id": "cv-int2-v1",                         a new read needs a new id
      "created_at": "2026-11-04T12:00:00Z",
      "blocks": {"formula": "floor((source_s - 120) / 60)", "dev": "even", "held_out": "odd"},
      "metrics": {
        "<metric name above>": {"comparator": ">=" or "<=", "threshold": 0.6, "min_n": 1000},
        ...
      }
    }

A check passes when n >= min_n and the value meets the comparator; a value that cannot be computed
fails. A gate spends its one look in two logged steps of `data/gates/held_out_reads.jsonl`:

    read    before any held-out pixel is decoded (`grant_held_out_pixels`, used by
            scripts/cv_analyse.py --held-out-gate). The gate file must be committed and clean. A
            second read of that gate_id or those bytes is refused: it needs a new gate version.
    score   before any held-out row is scored (scripts/cv_gate.py --split held_out). Only the
            analysis the read named can be scored, and only once.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from cairoflow_contracts.analysis_record import (
    AnalysisRecord,
    GateComparator,
    GateOutcome,
    GateSplit,
    TrackIndex,
)
from cairoflow_contracts.evidence import IntersectionGeometry, LanePolygon
from cairoflow_perception.analysis_store import (
    GROUND_SCALE,
    NO_GROUND,
    POINT_INTERPOLATED,
    POINT_OUTSIDE_VALID_REGION,
    PX_SCALE,
    TRAJECTORIES_FILE,
    TrajectoryColumns,
    decode_trajectories,
    load_tracks,
    verify_analysis,
)
from cairoflow_perception.camera_gate_v3 import (
    MOVING_M_S,
    HeadedReference,
    headed_references,
    measure,
    pair_tracks,
    stop_box,
)
from cairoflow_perception.camera_gate_v3 import summarise as summarise_placement
from cairoflow_perception.camera_world import lane_hits
from cairoflow_perception.local import scoring as S
from cairoflow_perception.local import zones
from cairoflow_perception.local.analytics import centred_speed
from cairoflow_perception.local.frames import HeldOutGrant
from cairoflow_perception.ngsim import LocalFrame, kinematics
from cairoflow_perception.ngsim_junction import ARMS, JunctionFrame

BLOCK_FORMULA = "floor((source_s - 120) / 60)"
METRICS = (
    "vehicle_recall",
    "precision_proxy",
    "across_heading_p90_m",
    "lane_agreement",
    "entering_count_error_share",
    "turning_matrix_agreement",
    "speed_abs_error_median_m_s",
)
READ_LOG = Path("data/gates/held_out_reads.jsonl")
EVIDENCE_CLASS = "agreement with a correlated reference, not accuracy"
ENTERING_DEFINITION = (
    "camera: tracks first seen near the edge of the usable image or at the start of the block; "
    "reference: every published vehicle in view. mid_view_by_arm is what the camera rule left out."
)


class GateRefused(RuntimeError):
    """The gate cannot be read: nothing held out was touched."""


class GateBlocks(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    formula: Literal["floor((source_s - 120) / 60)"]
    dev: Literal["even"]
    held_out: Literal["odd"]


class GateMetric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    comparator: GateComparator
    threshold: float = Field(allow_inf_nan=False)
    min_n: int = Field(ge=0)


class GateFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gate_id: str = Field(min_length=1)
    created_at: datetime
    blocks: GateBlocks
    metrics: dict[str, GateMetric] = Field(min_length=1)

    @field_validator("metrics")
    @classmethod
    def _known(cls, value: dict[str, GateMetric]) -> dict[str, GateMetric]:
        unknown = sorted(set(value) - set(METRICS))
        if unknown:
            raise ValueError(f"unknown gate metrics {unknown}; choose from {METRICS}")
        return value


def load_gate(path: Path) -> tuple[GateFile, str]:
    """The gate and the SHA-256 of its bytes."""
    data = path.read_bytes()
    return GateFile.model_validate_json(data), hashlib.sha256(data).hexdigest()


# ---- the held-out rule -----------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)


def require_committed(repo: Path, gate_path: Path) -> str:
    """The commit that last touched the gate file; refuses one not committed, or changed since."""
    try:
        relative = str(gate_path.resolve().relative_to(repo.resolve()))
    except ValueError as error:
        raise GateRefused(f"{gate_path} is outside the repository, so not committed") from error
    if _git(repo, "ls-files", "--error-unmatch", relative).returncode != 0:
        raise GateRefused(f"{relative} is not committed: register the gate before a held-out read")
    if _git(repo, "cat-file", "-e", f"HEAD:{relative}").returncode != 0:
        raise GateRefused(f"{relative} is staged but not committed")
    if _git(repo, "status", "--porcelain", "--", relative).stdout.strip():
        raise GateRefused(f"{relative} differs from its commit: a held-out read needs it clean")
    commit = _git(repo, "log", "-1", "--format=%H", "--", relative).stdout.strip()
    if not commit:
        raise GateRefused(f"{relative} has no commit")
    return commit


def held_out_reads(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.is_file():
        return []
    return [json.loads(line) for line in log_path.read_text("utf-8").splitlines() if line.strip()]


def claim_held_out_read(
    log_path: Path,
    gate: GateFile,
    gate_sha256: str,
    commit: str,
    analysis_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append this gate's one read to the log, or refuse a gate id or gate file that has been read.
    Nothing held out may be touched before this returns."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        for line in handle.read().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry["gate_id"] == gate.gate_id or entry["gate_sha256"] == gate_sha256:
                raise GateRefused(
                    f"gate {entry['gate_id']} was read on {entry['read_at_utc']}: a second "
                    "held-out read needs a new gate version"
                )
        entry = {
            "stage": "read",
            "gate_id": gate.gate_id,
            "gate_sha256": gate_sha256,
            "gate_commit": commit,
            "analysis_id": analysis_id,
            "read_at_utc": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        handle.seek(0, 2)
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
    return entry


def grant_held_out_pixels(
    repo: Path, gate_path: Path, read_log: Path, analysis_id: str
) -> HeldOutGrant:
    """Spend a committed gate's one read on `analysis_id`, and return what opens its held-out
    pixels. A run that fails afterwards has still spent it: a retry needs a new gate version."""
    gate, gate_sha256 = load_gate(gate_path)
    commit = require_committed(repo, gate_path)
    claim_held_out_read(read_log, gate, gate_sha256, commit, analysis_id)
    return HeldOutGrant(gate.gate_id, gate_sha256, analysis_id, read_log)


def claim_held_out_score(
    log_path: Path,
    gate: GateFile,
    gate_sha256: str,
    analysis_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append this gate's one score of the analysis its read named, or refuse."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        entries = [json.loads(line) for line in handle.read().splitlines() if line.strip()]
        mine = [e for e in entries if e["gate_id"] == gate.gate_id]
        read = next((e for e in mine if e.get("stage", "read") == "read"), None)
        if read is None or read["gate_sha256"] != gate_sha256:
            raise GateRefused(
                f"gate {gate.gate_id} has no read for these bytes: a held-out analysis comes from "
                "scripts/cv_analyse.py --held-out-gate"
            )
        if read["analysis_id"] != analysis_id:
            raise GateRefused(f"gate {gate.gate_id} read {read['analysis_id']}, not {analysis_id}")
        if any(e.get("stage") == "score" for e in mine):
            raise GateRefused(
                f"gate {gate.gate_id} has already scored {analysis_id}: a second held-out read "
                "needs a new gate version"
            )
        entry = {
            "stage": "score",
            "gate_id": gate.gate_id,
            "gate_sha256": gate_sha256,
            "gate_commit": read["gate_commit"],
            "analysis_id": analysis_id,
            "read_at_utc": (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        handle.seek(0, 2)
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        handle.flush()
    return entry


# ---- reading the two sides -------------------------------------------------------------------


def frame_key(record: AnalysisRecord, frame: int) -> int:
    """The harness's frame key (source second in tenths) of an analysis frame."""
    return S.tenths(record.source_start_s + frame / record.fps)


def split_frames(record: AnalysisRecord, split: GateSplit) -> dict[int, int]:
    """Analysis frame -> block, for the frames of a split. Outside [120, 1020) there is no block."""
    wanted = 0 if split == "dev" else 1
    out = {}
    for frame in range(record.frame_count):
        source_s = S.source_s_of(frame_key(record, frame))
        if S.COVERED_START_S <= source_s < S.COVERED_END_S:
            block = S.block_of(source_s)
            if block % 2 == wanted:
                out[frame] = block
    return out


@dataclass(frozen=True)
class CameraRun:
    record: AnalysisRecord
    index: TrackIndex
    columns: TrajectoryColumns


def load_run(directory: Path) -> CameraRun:
    record = verify_analysis(directory)
    _, columns = decode_trajectories((directory / TRAJECTORIES_FILE).read_bytes())
    return CameraRun(record, load_tracks(directory), columns)


@dataclass(frozen=True)
class Reference:
    """The published vehicles at a set of frames; all keyed by the harness's frame key."""

    refs: Mapping[int, Sequence[S.Ref]]
    headed: Mapping[int, Sequence[HeadedReference]]
    lanes: Sequence[LanePolygon]
    box: Mapping[str, float]


def load_reference(repo: Path, keys: Sequence[int]) -> Reference:
    """Reference rows for exactly these frames. As in local/scoring.py, kinematics run on the whole
    table, because a queued vehicle's heading is carried from the rows around it; only the rows at
    `keys` leave this function."""
    scenario = json.loads((repo / S.SCENARIO).read_text("utf-8"))
    frame = JunctionFrame(
        *scenario["frame"]["origin_state_plane_ft"], scenario["frame"]["bearing_deg"]
    )
    table = S.load_table(repo / S.TRAJECTORY_CSV)
    speed, heading = kinematics(table, LocalFrame(frame.origin_x_ft, frame.origin_y_ft))
    ticks = {S.global_time_ms(S.source_s_of(key)): key for key in keys}
    keep = np.isin(table.global_time_ms, np.fromiter(ticks, dtype=np.int64))
    traj, speed, heading = table.subset(keep), speed[keep], heading[keep]
    del table
    ground, _ = S.build_ground_map(repo)
    geometry = IntersectionGeometry.model_validate_json((repo / S.GEOMETRY).read_text("utf-8"))
    lanes = list(geometry.lanes)
    refs: dict[int, list[S.Ref]] = {}
    headed: dict[int, list[HeadedReference]] = {}
    for tick, key in ticks.items():
        found = headed_references(traj, speed, heading, frame, lanes, tick)
        headed[key] = found
        refs[key] = [
            S.Ref(
                key, h.vehicle_id, h.centre[0], h.centre[1], h.heading_deg,
                ground.visible(*h.centre),
                speed_m_s=math.nan if h.speed_m_s is None else h.speed_m_s,
            )
            for h in found
        ]  # fmt: skip
    return Reference(refs, headed, lanes, stop_box(geometry))


# ---- the camera side -------------------------------------------------------------------------


@dataclass(frozen=True)
class _Frame:
    frame_index: int


@dataclass(frozen=True)
class _Point:
    frame: _Frame
    position_m: tuple[float, float]
    lane_id: str | None


@dataclass(frozen=True)
class _Track:
    """What camera_gate_v3 reads of a CameraTrack."""

    track_id: str
    points: tuple[_Point, ...]


@dataclass(frozen=True)
class _Row:
    key: int
    block: int
    x_px: float
    y_px: float
    x_m: float | None
    y_m: float | None
    speed_m_s: float | None
    usable: bool  # observed and inside the valid region


def _block_speeds(
    run: CameraRun, rows: Mapping[int, int], frames: Mapping[int, int]
) -> dict[int, float]:
    """Ground speed per trajectory row, for `rows` (trajectory row -> analysis frame). A run of rows
    stops at a block's edge, so a split's speed is computed from that split's rows alone."""
    columns = run.columns
    by_block: dict[int, list[int]] = defaultdict(list)
    for row, frame in rows.items():
        if int(columns.gx[row]) != NO_GROUND:
            by_block[frames[frame]].append(row)
    out: dict[int, float] = {}
    for part in by_block.values():
        chosen = np.asarray(part)
        u = columns.gx[chosen].astype(float) * GROUND_SCALE
        v = columns.gy[chosen].astype(float) * GROUND_SCALE
        speeds, _, _ = centred_speed(u, v, 1 / run.record.fps)
        out.update(
            (row, float(speed))
            for row, speed in zip(part, speeds, strict=True)
            if math.isfinite(speed)
        )
    return out


def _track_rows(run: CameraRun, frames: Mapping[int, int]) -> dict[int, list[_Row]]:
    """Observed rows of each track at the split's frames. Interpolated rows are left out."""
    columns = run.columns
    out: dict[int, list[_Row]] = {}
    for track in run.index.tracks:
        inside = {
            track.first_row + i: track.start_frame + i
            for i in range(track.n)
            if track.start_frame + i in frames
        }
        speeds = _block_speeds(run, inside, frames)
        rows = []
        for row, frame in inside.items():
            flags = int(columns.point_flags[row])
            if flags & POINT_INTERPOLATED:
                continue
            placed = int(columns.gx[row]) != NO_GROUND
            rows.append(
                _Row(
                    frame_key(run.record, frame),
                    frames[frame],
                    float(columns.x[row]) * PX_SCALE,
                    float(columns.y[row]) * PX_SCALE,
                    float(columns.gx[row]) * GROUND_SCALE if placed else None,
                    float(columns.gy[row]) * GROUND_SCALE if placed else None,
                    speeds.get(row),
                    placed and not flags & POINT_OUTSIDE_VALID_REGION,
                )
            )
        if rows:
            out[track.track_id] = rows
    return out


def _lane_of(lanes: Sequence[LanePolygon], position: tuple[float, float]) -> str | None:
    hits = lane_hits(lanes, position)
    return hits[0].lane_id if len(hits) == 1 else None


def _pairing_tracks(
    rows: Mapping[int, Sequence[_Row]], lanes: Sequence[LanePolygon]
) -> list[_Track]:
    tracks = []
    for track_id, track_rows in rows.items():
        points = tuple(
            _Point(_Frame(r.key), (r.x_m, r.y_m), _lane_of(lanes, (r.x_m, r.y_m)))
            for r in track_rows
            if r.usable
        )
        if points:
            tracks.append(_Track(str(track_id), points))
    return tracks


def _detections(rows: Mapping[int, Sequence[_Row]]) -> dict[int, list[S.Det]]:
    by_key: dict[int, list[S.Det]] = defaultdict(list)
    for track_id, track_rows in rows.items():
        for r in track_rows:
            by_key[r.key].append(S.Det(r.key, f"{track_id}:{r.key}", str(track_id), r.x_m, r.y_m))
    return by_key


# ---- counts ----------------------------------------------------------------------------------

Cell = tuple[str, str, str]


@dataclass
class BlockCounts:
    entering: Counter  # arm -> count
    turning: Counter  # (approach, exit, turn) -> count
    mid_view: Counter  # arm -> tracks with an arm that are left out of `entering`


def _empty_counts() -> BlockCounts:
    return BlockCounts(Counter(), Counter(), Counter())


def _add_path(
    counts: BlockCounts, u: Sequence[float], v: Sequence[float], *, entered: bool = True
) -> None:
    movement = zones.classify(np.asarray(u), np.asarray(v), entered=entered)
    if movement.entering:
        counts.entering[movement.entering] += 1
    elif movement.approach:
        counts.mid_view[movement.approach] += 1
    if movement.complete:
        counts.turning[(movement.approach, movement.exit_arm, movement.turn)] += 1


def camera_counts(
    run: CameraRun, rows: Mapping[int, Sequence[_Row]], block_of_key: Mapping[int, int]
) -> dict[int, BlockCounts]:
    """Per block, each counted track classified from its own points inside that block. A track
    already running when the block opens enters it, as a track at the start of a run does."""
    counted = {t.track_id for t in run.index.tracks if t.status != "fragment"}
    opens: dict[int, int] = {}
    for key, block in block_of_key.items():
        opens[block] = min(key, opens.get(block, key))
    record = run.record
    bottom_px = record.calibration.valid_below_row_px or record.height_px
    out: dict[int, BlockCounts] = defaultdict(_empty_counts)
    for track_id, track_rows in rows.items():
        if track_id not in counted:
            continue
        by_block: dict[int, list[_Row]] = defaultdict(list)
        for r in track_rows:
            if r.usable:
                by_block[r.block].append(r)
        for block, part in by_block.items():
            first = part[0]
            entered = zones.enters_view(
                first.x_px, first.y_px, (first.key - opens[block]) / S.FPS, 1 / record.fps,
                record.width_px, min(bottom_px, record.height_px),
            )  # fmt: skip
            _add_path(out[block], [r.x_m for r in part], [r.y_m for r in part], entered=entered)
    return out


def reference_counts(
    refs: Mapping[int, Sequence[S.Ref]], block_of_key: Mapping[int, int]
) -> dict[int, BlockCounts]:
    """Per block, each published vehicle classified from its in-view points inside that block."""
    paths: dict[tuple[int, int], list[tuple[int, float, float]]] = defaultdict(list)
    for key, found in refs.items():
        for ref in found:
            if ref.in_view:
                paths[(block_of_key[key], ref.vehicle_id)].append((key, ref.x_m, ref.y_m))
    out: dict[int, BlockCounts] = defaultdict(_empty_counts)
    for (block, _vehicle), points in sorted(paths.items()):
        points.sort()
        _add_path(out[block], [p[1] for p in points], [p[2] for p in points])
    return out


def _count_metrics(
    camera: Mapping[int, BlockCounts], reference: Mapping[int, BlockCounts], blocks: Sequence[int]
) -> dict[str, Any]:
    entering_error = entering_ref = 0
    overlap = larger_total = 0
    by_block: dict[int, dict[str, Any]] = {}
    by_arm = {arm: {"camera": 0, "reference": 0} for arm in ARMS}
    mid_view: Counter = Counter()
    for block in blocks:
        cam = camera.get(block, _empty_counts())
        ref = reference.get(block, _empty_counts())
        errors = {arm: cam.entering[arm] - ref.entering[arm] for arm in ARMS}
        block_ref = sum(ref.entering.values())
        block_error = sum(abs(e) for e in errors.values())
        cells = set(cam.turning) | set(ref.turning)
        block_overlap = sum(min(cam.turning[c], ref.turning[c]) for c in cells)
        block_total = max(sum(cam.turning.values()), sum(ref.turning.values()))
        entering_error += block_error
        entering_ref += block_ref
        overlap += block_overlap
        larger_total += block_total
        for arm in ARMS:
            by_arm[arm]["camera"] += cam.entering[arm]
            by_arm[arm]["reference"] += ref.entering[arm]
        mid_view.update(cam.mid_view)
        by_block[block] = {
            "entering_by_arm": {
                arm: {"camera": cam.entering[arm], "reference": ref.entering[arm]} for arm in ARMS
            },
            "mid_view_by_arm": {arm: cam.mid_view[arm] for arm in ARMS},
            "entering_count_error_share": _metric(_ratio(block_error, block_ref), block_ref),
            "turning_matrix_agreement": _metric(_ratio(block_overlap, block_total), block_total),
            "turning_cells": {
                "-".join(c): {"camera": cam.turning[c], "reference": ref.turning[c]}
                for c in sorted(cells)
            },
        }
    return {
        "entering_count_error_share": _metric(_ratio(entering_error, entering_ref), entering_ref),
        "turning_matrix_agreement": _metric(_ratio(overlap, larger_total), larger_total),
        "entering_by_arm": by_arm,
        "mid_view_by_arm": {arm: mid_view[arm] for arm in ARMS},
        "by_block": by_block,
    }


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _metric(value: float | None, n: int, **extra: Any) -> dict[str, Any]:
    return {"value": value, "n": n, **extra}


def _speed_errors(
    rows: Sequence[Mapping[str, Any]], camera: Mapping[int, Sequence[_Row]]
) -> list[float]:
    """Signed camera - reference speed on paired points whose reference is moving."""
    speed_at = {(str(t), r.key): r.speed_m_s for t, part in camera.items() for r in part}
    errors = []
    for row in rows:
        reference = row["speed_m_s"]
        camera_speed = speed_at.get((row["track"], row["frame"]))
        if reference is not None and reference >= MOVING_M_S and camera_speed is not None:
            errors.append(camera_speed - reference)
    return errors


def _placement_metrics(placement: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "across_heading_p90_m": _metric(
            placement["D_across_heading_p90_m"], placement["matched_points"]
        ),
        "lane_agreement": _metric(placement["B_point_lane_agreement"], placement["B_points"]),
    }


def _speed_metric(errors: Sequence[float]) -> dict[str, Any]:
    if not errors:
        return {"speed_abs_error_median_m_s": _metric(None, 0)}
    absolute = np.abs(np.asarray(errors))
    return {
        "speed_abs_error_median_m_s": _metric(
            round(float(np.median(absolute)), 4),
            len(errors),
            p90_m_s=round(float(np.percentile(absolute, 90)), 4),
            median_signed_m_s=round(float(np.median(errors)), 4),
        )
    }


def _detection_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    recall, precision = summary["vehicle_frame_recall"], summary["precision_proxy"]
    return {
        "vehicle_recall": _metric(
            recall["value"], recall["denominator"], numerator=recall["numerator"],
            ci95=recall["ci95_cluster_bootstrap_vehicles"],
        ),
        "precision_proxy": _metric(
            precision["value"], precision["denominator"], numerator=precision["numerator"],
            ci95=precision["ci95_cluster_bootstrap_groups"],
        ),
    }  # fmt: skip


def compare(run: CameraRun, reference: Reference, frames: Mapping[int, int]) -> dict[str, Any]:
    """Every metric, for the whole split and for each block, at `frames` (analysis frame -> block).
    `reference` must cover exactly these frames."""
    keys = {frame_key(run.record, f): block for f, block in frames.items()}
    blocks = sorted(set(frames.values()))
    camera = _track_rows(run, frames)
    detections = _detections(camera)
    results = S.associate(detections, reference.refs, sorted(keys))
    tracks = _pairing_tracks(camera, reference.lanes)
    pairing = pair_tracks(tracks, reference.headed)
    points = measure(tracks, reference.headed, pairing, reference.lanes, reference.box)
    counts = _count_metrics(
        camera_counts(run, camera, keys), reference_counts(reference.refs, keys), blocks
    )
    metrics = {
        **_detection_metrics(S.summarise(results)),
        **_placement_metrics(summarise_placement(points, tracks, pairing)),
        "entering_count_error_share": counts["entering_count_error_share"],
        "turning_matrix_agreement": counts["turning_matrix_agreement"],
        **_speed_metric(_speed_errors(points, camera)),
    }
    by_block: dict[str, Any] = {}
    for block in blocks:
        part = {k: r for k, r in results.items() if keys[k] == block}
        block_points = [p for p in points if keys[p["frame"]] == block]
        block_keys = {k for k, b in keys.items() if b == block}
        block_tracks = [
            t for t in tracks if any(p.frame.frame_index in block_keys for p in t.points)
        ]
        by_block[str(block)] = {
            "frames": len(block_keys),
            **_detection_metrics(S.summarise(part, intervals=False)),
            **_placement_metrics(summarise_placement(block_points, block_tracks, pairing)),
            **counts["by_block"][block],
            **_speed_metric(_speed_errors(block_points, camera)),
        }
    return {
        "metrics": metrics,
        "entering_by_arm": counts["entering_by_arm"],
        "mid_view_by_arm": counts["mid_view_by_arm"],
        "entering_definition": ENTERING_DEFINITION,
        "by_block": by_block,
        "tracks": {"paired": len(pairing), "with_points": len(tracks)},
        "frames_scored": len(results),
    }


# ---- the gate --------------------------------------------------------------------------------


def _meets(value: float, comparator: str, threshold: float) -> bool:
    return value <= threshold if comparator == "<=" else value >= threshold


def evaluate_gate(
    gate: GateFile, gate_sha256: str, metrics: Mapping[str, Mapping[str, Any]], split: GateSplit
) -> tuple[GateOutcome, ...]:
    """Each check of a gate file against measured metrics."""
    outcomes = []
    for name, rule in gate.metrics.items():
        found = metrics.get(name, {})
        value, n = found.get("value"), int(found.get("n", 0))
        note = None
        if value is None:
            passed, note = False, "could not be computed"
        elif n < rule.min_n:
            passed, note = False, f"n {n} is below the required {rule.min_n}"
        else:
            passed = _meets(value, rule.comparator, rule.threshold)
        outcomes.append(
            GateOutcome(
                gate_id=gate.gate_id, gate_sha256=gate_sha256, metric=name, scope="site",
                split=split, measured=value, comparator=rule.comparator,
                threshold=rule.threshold, n=n, passed=passed, note=note,
            )
        )  # fmt: skip
    return tuple(outcomes)
