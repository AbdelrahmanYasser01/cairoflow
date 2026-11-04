"""Reading and writing analysis directories: record.json, tracks.json and trajectories.bin.

trajectories.bin is little-endian and columnar, so a browser can view it with typed arrays and no
parsing. It is a 32-byte header followed by ten columns, each padded to a 4-byte boundary:

    header  magic "CFT1", u32 n_points, u32 n_tracks, u32 n_frames,
            f32 fps, f32 px_scale, f32 ground_scale, u32 flags
    u16     x, y, w, h        box centre and size in pixels, value = stored * px_scale
    i16     gx, gy            ground metres, value = stored * ground_scale; NO_GROUND = no value
    u16     speed_cm_s        NO_SPEED = withheld
    u8      heading           256 steps per turn, 0 = junction +y axis, clockwise
    u8      conf              detector confidence, value = stored / 255
    u8      point_flags       POINT_* bits

A track's rows are contiguous, one per frame: row = first_row + frame - start_frame.

An analysis directory is immutable: writing refuses an existing directory, and `verify_analysis`
checks every bundled artifact against the hash in its record.
"""

from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cairoflow_contracts.analysis_record import (
    TRAJECTORY_MAGIC,
    AnalysisRecord,
    ArtifactRef,
    TrackEntry,
    TrackIndex,
)

RECORD_FILE = "record.json"
TRACKS_FILE = "tracks.json"
TRAJECTORIES_FILE = "trajectories.bin"

PX_SCALE = 1 / 16
GROUND_SCALE = 0.02
NO_GROUND = -32768
NO_SPEED = 0xFFFF
HEADER = struct.Struct("<4sIIIfffI")
POINT_INTERPOLATED = 1
POINT_SAM_REFINED = 2
POINT_OCCLUDED = 4
POINT_OUTSIDE_VALID_REGION = 8

_LAYOUT = (
    *((name, np.dtype("<u2")) for name in ("x", "y", "w", "h")),
    *((name, np.dtype("<i2")) for name in ("gx", "gy")),
    ("speed_cm_s", np.dtype("<u2")),
    ("heading", np.dtype("u1")),
    ("conf", np.dtype("u1")),
    ("point_flags", np.dtype("u1")),
)


class AnalysisStoreError(ValueError):
    """An analysis directory is malformed, changed since it was written, or already exists."""


@dataclass(frozen=True)
class TrajectoryColumns:
    """Stored (quantised) columns, one element per point, in track row order."""

    x: np.ndarray
    y: np.ndarray
    w: np.ndarray
    h: np.ndarray
    gx: np.ndarray
    gy: np.ndarray
    speed_cm_s: np.ndarray
    heading: np.ndarray
    conf: np.ndarray
    point_flags: np.ndarray

    def __len__(self) -> int:
        return len(self.x)


def quantise(
    *,
    x_px: np.ndarray,
    y_px: np.ndarray,
    w_px: np.ndarray,
    h_px: np.ndarray,
    ground_x_m: np.ndarray | None = None,
    ground_y_m: np.ndarray | None = None,
    speed_m_s: np.ndarray | None = None,
    heading_deg: np.ndarray | None = None,
    confidence: np.ndarray | None = None,
    point_flags: np.ndarray | None = None,
) -> TrajectoryColumns:
    """Real-valued columns to stored ones. NaN (or a missing column) means no value."""
    n = len(x_px)

    def px(values: np.ndarray) -> np.ndarray:
        return np.clip(np.rint(np.asarray(values, float) / PX_SCALE), 0, 0xFFFF).astype("<u2")

    def ground(values: np.ndarray | None) -> np.ndarray:
        if values is None:
            return np.full(n, NO_GROUND, "<i2")
        values = np.asarray(values, float)
        stored = np.clip(np.rint(np.nan_to_num(values) / GROUND_SCALE), -32767, 32767)
        return np.where(np.isfinite(values), stored, NO_GROUND).astype("<i2")

    if speed_m_s is None:
        speed = np.full(n, NO_SPEED, "<u2")
    else:
        values = np.asarray(speed_m_s, float)
        stored = np.clip(np.rint(np.nan_to_num(values) * 100), 0, NO_SPEED - 1)
        speed = np.where(np.isfinite(values), stored, NO_SPEED).astype("<u2")
    if heading_deg is None:
        heading = np.zeros(n, "u1")
    else:
        turned = np.mod(np.nan_to_num(np.asarray(heading_deg, float)), 360) / 360 * 256
        heading = np.mod(np.rint(turned), 256).astype("u1")
    conf = (
        np.zeros(n, "u1")
        if confidence is None
        else np.clip(np.rint(np.asarray(confidence, float) * 255), 0, 255).astype("u1")
    )
    flags = np.zeros(n, "u1") if point_flags is None else np.asarray(point_flags, "u1")
    return TrajectoryColumns(
        px(x_px), px(y_px), px(w_px), px(h_px),
        ground(ground_x_m), ground(ground_y_m), speed, heading, conf, flags,
    )  # fmt: skip


def encode_trajectories(
    columns: TrajectoryColumns, *, n_tracks: int, n_frames: int, fps: float
) -> bytes:
    n = len(columns)
    parts = [
        HEADER.pack(
            TRAJECTORY_MAGIC.encode(), n, n_tracks, n_frames, fps, PX_SCALE, GROUND_SCALE, 0
        )
    ]
    for name, dtype in _LAYOUT:
        values = getattr(columns, name)
        if len(values) != n:
            raise AnalysisStoreError(f"column {name} has {len(values)} rows, expected {n}")
        raw = np.ascontiguousarray(values, dtype=dtype).tobytes()
        parts.append(raw + b"\0" * (-len(raw) % 4))
    return b"".join(parts)


def decode_trajectories(data: bytes) -> tuple[dict[str, float | int], TrajectoryColumns]:
    if len(data) < HEADER.size:
        raise AnalysisStoreError("trajectories file is shorter than its header")
    magic, n, n_tracks, n_frames, fps, px_scale, ground_scale, flags = HEADER.unpack_from(data)
    if magic != TRAJECTORY_MAGIC.encode():
        raise AnalysisStoreError(f"not a trajectories file: magic {magic!r}")
    offset, columns = HEADER.size, {}
    for name, dtype in _LAYOUT:
        size = n * dtype.itemsize
        if offset + size > len(data):
            raise AnalysisStoreError(f"trajectories file is truncated at column {name}")
        columns[name] = np.frombuffer(data, dtype=dtype, count=n, offset=offset)
        offset += size + (-size % 4)
    header = {
        "n_points": n, "n_tracks": n_tracks, "n_frames": n_frames, "fps": fps,
        "px_scale": px_scale, "ground_scale": ground_scale, "flags": flags,
    }  # fmt: skip
    return header, TrajectoryColumns(**columns)


def trajectory_checksum(columns: TrajectoryColumns) -> int:
    """A sum over every stored column, comparable across decoders (Python and TypeScript)."""
    total = 0
    for name, _ in _LAYOUT:
        values = getattr(columns, name).astype(np.int64)
        total = (total + int(values.sum()) + int(values[::97].sum()) * 3) % 2_147_483_647
    return total


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(model: AnalysisRecord | TrackIndex) -> bytes:
    return (model.model_dump_json(indent=1) + "\n").encode()


def write_analysis(
    directory: Path,
    record: AnalysisRecord,
    tracks: Sequence[TrackEntry],
    columns: TrajectoryColumns,
    extra_artifacts: Sequence[tuple[ArtifactRef, bytes | None]] = (),
) -> AnalysisRecord:
    """Write a new analysis directory. `record.artifacts` is replaced with the real hashes of
    what was written; extra artifacts with None bytes are listed but not written (`bundled`
    False). Refuses a directory that exists: analyses are immutable."""
    if directory.exists():
        raise AnalysisStoreError(f"{directory} already exists; analyses are immutable")
    expected_rows = sum(track.n for track in tracks)
    if expected_rows != len(columns):
        raise AnalysisStoreError(f"tracks cover {expected_rows} rows, columns have {len(columns)}")
    binary = encode_trajectories(
        columns, n_tracks=len(tracks), n_frames=record.frame_count, fps=record.fps
    )
    index = TrackIndex(
        analysis_id=record.analysis_id,
        trajectories_sha256=_sha256(binary),
        tracks=tuple(tracks),
    )
    index_bytes = _json_bytes(index)
    artifacts = [
        ArtifactRef(
            role="trajectories", path=TRAJECTORIES_FILE, sha256=_sha256(binary),
            bytes=len(binary), media_type="application/octet-stream",
        ),
        ArtifactRef(
            role="tracks", path=TRACKS_FILE, sha256=_sha256(index_bytes),
            bytes=len(index_bytes), media_type="application/json",
        ),
    ]  # fmt: skip
    written: list[tuple[str, bytes]] = [
        (TRAJECTORIES_FILE, binary),
        (TRACKS_FILE, index_bytes),
    ]
    for artifact, data in extra_artifacts:
        if data is None:
            artifacts.append(artifact)
            continue
        artifacts.append(artifact.model_copy(update={"sha256": _sha256(data), "bytes": len(data)}))
        written.append((artifact.path, data))
    final = AnalysisRecord.model_validate(
        {
            **record.model_dump(mode="json"),
            "artifacts": [a.model_dump(mode="json") for a in artifacts],
        }
    )
    directory.mkdir(parents=True)
    for name, data in written:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (directory / RECORD_FILE).write_bytes(_json_bytes(final))
    return final


def load_record(directory: Path) -> AnalysisRecord:
    path = directory / RECORD_FILE
    if not path.is_file():
        raise AnalysisStoreError(f"{directory} has no {RECORD_FILE}")
    return AnalysisRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))


def verify_analysis(directory: Path) -> AnalysisRecord:
    """Load a record and check each bundled artifact's hash; raises on any difference."""
    record = load_record(directory)
    for artifact in record.artifacts:
        if not artifact.bundled or artifact.sha256 is None:
            continue
        path = directory / artifact.path
        if not path.is_file():
            raise AnalysisStoreError(f"{record.analysis_id}: {artifact.path} is missing")
        if _sha256(path.read_bytes()) != artifact.sha256:
            raise AnalysisStoreError(f"{record.analysis_id}: {artifact.path} has changed")
    return record


def load_tracks(directory: Path) -> TrackIndex:
    data = (directory / TRACKS_FILE).read_text(encoding="utf-8")
    return TrackIndex.model_validate(json.loads(data))
