"""Scoring harness for local detectors on NGSIM camera 2, comparable with the SAM baseline.

What is measured is *agreement with NGSIM*, a correlated reference (same video; the camera
placement is fitted to it), never accuracy. The definitions are those of an earlier offline scorer,
ported here:

- vehicle-frame recall = reference vehicle-frames in view that are matched / reference
  vehicle-frames in view;
- precision proxy = detections matched to a reference vehicle / detections with a ground position
  (an unmatched box is not necessarily false: NGSIM has no parked or off-lane vehicles).

Matching is per frame, greedy on cost, one to one, inside gate v3's registered windows (3 m across
and 8 m along the reference's heading). A detection's ground point is its box centre through the
v2 homography; a reference is the NGSIM front centre moved back half a length along its heading.

Time. `source_s` is the published convention: clip frame k of a clip trimmed at `start` is source
second start + k/10. NGSIM tick = T0 + round((source_s - 120.0) * 10) * 100 ms (ADR-031).
The image behind that second is **AVI frame round(source_s * 10) - 1, not round(source_s * 10)**:
the clips were cut with `ffmpeg -ss`, and their frame k is AVI frame 10*start + k - 1 (checked on
all 700 clip frames, `scripts/cv_bakeoff.py --check-frame-map`). `avi_index` applies that, so a
detector sees exactly the image SAM saw at the same source second, against the same tick.

Held-out rule (fixed). block = floor((source_s - 120) / 60) over [120, 1020); even blocks are
development, odd blocks are reserved for a pre-registered gate. `FrameSampler` raises on an odd
block, and the reference builder drops every odd-block row right after the kinematics pass, so
this module cannot return a held-out image or score a held-out tick.

Imports: standard library and numpy only at module load. OpenCV is imported by `FrameSampler`, the
repository's contract-bound packages only by `build_reference_set`, and a built reference set is
a plain .npz, so scoring runs from a venv that has numpy and OpenCV and nothing else of ours.
"""

from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NamedTuple, Protocol, runtime_checkable

import numpy as np

HARNESS_VERSION = "0.1.0"

T0_MS = 1118935800000  # 08:30:00.0 in NGSIM global time
SYNC_S = 120.0  # source second at 08:30:00.0 (ADR-031; the 119.7 in old SAM records is stale)
SYNC_OTHER_S = 119.7  # the published sensitivity only; it decides nothing
FPS = 10
COVERED_START_S, COVERED_END_S = 120.0, 1020.0  # the source seconds NGSIM covers
BLOCK_S = 60.0
AVI_FRAME_LAG = 1  # clip frame at source second s is AVI frame round(s * FPS) - AVI_FRAME_LAG
IMAGE_W_PX, IMAGE_H_PX = 640, 480
USABLE_V_PX = 410  # rows from here down are the glass band with the burned-in caption
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261031
US_SURVEY_FOOT_M = 1200 / 3937

VIDEO = "data/raw/ngsim-lankershim/camera2/lankershim-camera2-0830am-0845am.avi"
TRAJECTORY_CSV = "data/raw/ngsim-lankershim/trajectories/lankershim-0830-0845.csv"
SCENARIO = "data/geometry/ngsim-lankershim-int2/scenario.json"
GEOMETRY = "data/geometry/ngsim-lankershim-int2/geometry.json"
SYNC_CHECKPOINTS = "data/geometry/ngsim-lankershim-cam2/v2/sync_checkpoints.json"
GATE_V3 = "data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate_v3.json"

WINDOWS: dict[str, dict[str, Any]] = {
    "w1": {
        "source_s": (370.0, 380.0),
        "sam_run": "ngsim-cam2-sam-20261028T151050Z",
        "clip": "ngsim-lankershim-cam2-0830-t370-d010",
    },
    "w2": {
        "source_s": (390.0, 420.0),
        "sam_run": "ngsim-cam2-sam-20261030T220322Z",
        "clip": "ngsim-lankershim-cam2-0830-t390-d030",
    },
    "w3": {
        "source_s": (970.0, 1000.0),
        "sam_run": "ngsim-cam2-sam-20261030T230408Z",
        "clip": "ngsim-lankershim-cam2-0830-t970-d030",
    },
}
# The offline scorer's results (results/baseline_metrics.json; its report, section 3).
PUBLISHED_SAM = {
    "w1": {"recall": (1069, 4451), "precision": (1070, 1227), "vehicles": (20, 60)},
    "w2": {"recall": (2135, 9838), "precision": (2192, 2219), "vehicles": (35, 82)},
    "w3": {"recall": (2404, 5322), "precision": (2429, 2854), "vehicles": (21, 55)},
}


class HeldOutError(RuntimeError):
    """Something asked for an odd block. Those are reserved for a pre-registered gate."""


# ---- blocks and the time map --------------------------------------------------------------


def block_of(source_s: float) -> int:
    """block = floor((source_s - 120) / 60), defined on [120, 1020)."""
    if not COVERED_START_S <= source_s < COVERED_END_S:
        raise ValueError(f"source second {source_s} is outside [120, 1020): no block")
    return math.floor((source_s - COVERED_START_S) / BLOCK_S)


def is_dev(source_s: float) -> bool:
    """True in an even block; False in an odd one; ValueError outside the covered range."""
    return block_of(source_s) % 2 == 0


def require_dev(source_s: float) -> None:
    if not is_dev(source_s):
        raise HeldOutError(f"source second {source_s} is in held-out block {block_of(source_s)}")


def tenths(source_s: float) -> int:
    """The harness's frame key: the source second in tenths (published convention)."""
    return round(source_s * FPS)


def source_s_of(frame: int) -> float:
    return frame / FPS


def window_s(source_s: float, sync_s: float = SYNC_S) -> float:
    return round(source_s - sync_s, 6)


def global_time_ms(source_s: float, sync_s: float = SYNC_S) -> int:
    return T0_MS + round(window_s(source_s, sync_s) * FPS) * (1000 // FPS)


def source_s_of_tick(tick_ms: int) -> float:
    """The source second a NGSIM tick belongs to at the governing sync."""
    return (tick_ms - T0_MS) / 1000 + SYNC_S


def tick_is_dev(tick_ms: int) -> bool:
    s = round(source_s_of_tick(tick_ms), 6)
    return COVERED_START_S <= s < COVERED_END_S and is_dev(s)


def avi_index(source_s: float) -> int:
    """The AVI frame (0-based, as OpenCV and `ffmpeg select=eq(n,...)` count) the published
    pipeline showed at this source second."""
    return tenths(source_s) - AVI_FRAME_LAG


def require_dev_image(source_s: float) -> None:
    """A dev image must not be held out under either reading of its own time: the published one
    (`source_s`) or the AVI's own (index / 10), which is one frame earlier. Without the second
    test the first frame of an even block would be the last frame of the odd block before it."""
    require_dev(source_s)
    native = avi_index(source_s) / FPS
    if COVERED_START_S <= native < COVERED_END_S:
        require_dev(native)


# ---- which frames are scored --------------------------------------------------------------


def dev_blocks() -> list[int]:
    return [b for b in range(int((COVERED_END_S - COVERED_START_S) / BLOCK_S)) if b % 2 == 0]


def dev_sample_times(step_s: float = 2.0) -> list[float]:
    """Every `step_s` seconds through every even block, starting half a step in: the sample
    keeps clear of block edges, where the two readings of a frame's time differ."""
    out = []
    for block in dev_blocks():
        start = COVERED_START_S + block * BLOCK_S
        out += [start + step_s / 2 + i * step_s for i in range(int(BLOCK_S / step_s))]
    return out


def sam_window_times(key: str, step: int = 1) -> list[float]:
    """The source seconds of the frames behind the published numbers, every `step`-th."""
    start, end = WINDOWS[key]["source_s"]
    return [source_s_of(t) for t in range(tenths(start), tenths(end), step)]


def in_sam_window(source_s: float) -> bool:
    return any(w["source_s"][0] <= source_s < w["source_s"][1] for w in WINDOWS.values())


# ---- the frame sampler --------------------------------------------------------------------


class FrameSampler:
    """Frames of the camera 2 AVI by source second. Raises `HeldOutError` on an odd block.

    Every AVI frame is intra coded and OpenCV's seek lands on the requested index (checked
    against a sequential read and `ffmpeg select`), so no held-out frame is decoded to reach a
    dev one.
    """

    def __init__(self, path: Path) -> None:
        import cv2  # noqa: PLC0415 - optional at import time

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(str(path))
        if not self._cap.isOpened():
            raise FileNotFoundError(f"cannot open {path}")
        self._next: int | None = None

    def read(self, source_s: float) -> np.ndarray:
        """The BGR image (uint8, 480 x 640 x 3) behind one source second."""
        require_dev_image(source_s)
        index = avi_index(source_s)
        if index != self._next:
            self._cap.set(self._cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = self._cap.read()
        if not ok or image is None:
            self._next = None
            raise OSError(f"no frame at AVI index {index} (source second {source_s})")
        self._next = index + 1
        return image

    def frames(self, times: Iterable[float]) -> Iterator[tuple[float, np.ndarray]]:
        for source_s in times:
            yield source_s, self.read(source_s)

    def close(self) -> None:
        self._cap.release()

    def __enter__(self) -> FrameSampler:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# ---- image <-> ground ---------------------------------------------------------------------

_HORIZON_EPS = 1e-9


@dataclass(frozen=True)
class GroundMap:
    """Image pixels to junction-frame metres and back: the refit v2 homography then the world
    model's rotation, as plain numbers. Mirrors calibration.Homography and camera_world.Placement
    (tests/test_cv_scoring.py compares them where the data is present)."""

    image_to_ground: tuple[tuple[float, float, float], ...]  # 3 x 3, last entry 1
    side: float  # sign of the projective coordinate on the visible ground
    bearing_deg: float  # junction +y as a grid bearing

    def _matrix(self) -> np.ndarray:
        return np.asarray(self.image_to_ground, dtype=np.float64)

    def to_junction(self, u: float, v: float) -> tuple[float, float] | None:
        """Junction metres of an image point, or None at or beyond the horizon."""
        ground = self._matrix() @ np.array([u, v, 1.0])
        if ground[2] * math.copysign(1.0, self.side) <= _HORIZON_EPS:
            return None
        gx, gy = ground[0] / ground[2], ground[1] / ground[2]
        b = math.radians(self.bearing_deg)
        return (
            float(gx * math.cos(b) - gy * math.sin(b)),
            float(gx * math.sin(b) + gy * math.cos(b)),
        )

    def to_image(self, x_m: float, y_m: float) -> tuple[float, float] | None:
        """Image pixels of a junction point, or None when it does not project."""
        b = math.radians(self.bearing_deg)
        gx = x_m * math.cos(b) + y_m * math.sin(b)
        gy = -x_m * math.sin(b) + y_m * math.cos(b)
        p = np.linalg.inv(self._matrix()) @ np.array([gx, gy, 1.0])
        if abs(p[2]) <= _HORIZON_EPS:
            return None
        return float(p[0] / p[2]), float(p[1] / p[2])

    def visible(self, x_m: float, y_m: float) -> bool:
        """The point projects into the usable image (above the glass band) and back to itself,
        so it is in front of the camera: the offline scorer's 'in view' rule."""
        px = self.to_image(x_m, y_m)
        if px is None:
            return False
        back = self.to_junction(*px)
        if back is None or math.hypot(back[0] - x_m, back[1] - y_m) >= 0.01:
            return False
        return 0 <= px[0] < IMAGE_W_PX and 0 <= px[1] < USABLE_V_PX


def footprint_corners_m(
    x_m: float, y_m: float, heading_deg: float, length_m: float, width_m: float
) -> list[tuple[float, float]]:
    """The four corners of a vehicle rectangle centred on (x, y), in junction metres. A heading
    is a bearing clockwise from +y."""
    h = math.radians(heading_deg)
    ax, ay = math.sin(h), math.cos(h)  # along the heading
    cx, cy = math.cos(h), -math.sin(h)  # to its right
    half_l, half_w = length_m / 2, width_m / 2
    return [
        (x_m + a * half_l * ax + c * half_w * cx, y_m + a * half_l * ay + c * half_w * cy)
        for a, c in ((1, 1), (1, -1), (-1, -1), (-1, 1))
    ]


def footprint_box_px(
    gm: GroundMap, x_m: float, y_m: float, heading_deg: float, length_m: float, width_m: float
) -> tuple[float, float, float, float] | None:
    """The image bounding box (x1, y1, x2, y2) of a vehicle's ground rectangle."""
    corners = [
        gm.to_image(*c) for c in footprint_corners_m(x_m, y_m, heading_deg, length_m, width_m)
    ]
    if any(c is None for c in corners):
        return None
    us, vs = [c[0] for c in corners], [c[1] for c in corners]
    return min(us), min(vs), max(us), max(vs)


# ---- reference ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Ref:
    """One NGSIM vehicle at one frame; positions in the junction frame, metres."""

    frame: int
    vehicle_id: int
    x_m: float
    y_m: float
    heading_deg: float
    in_view: bool = True
    length_m: float = 0.0
    width_m: float = 0.0
    arm: str = "no-lane"
    speed_m_s: float = math.nan
    footprint_px: tuple[float, float, float, float] | None = None


@dataclass
class ReferenceSet:
    """References by frame key. A frame with no entry is unscorable (no reference at its tick, or
    its tick is held out), never scored as empty."""

    sync_s: float
    shift_s: float
    ground: GroundMap
    refs: dict[int, list[Ref]]
    meta: dict[str, Any]

    _FIELDS = ("frame", "vehicle_id", "x_m", "y_m", "heading_deg", "in_view", "length_m",
               "width_m", "speed_m_s")  # fmt: skip

    def save(self, path: Path) -> None:
        rows = [r for frame in sorted(self.refs) for r in self.refs[frame]]
        arrays: dict[str, np.ndarray] = {
            name: np.array([getattr(r, name) for r in rows]) for name in self._FIELDS
        }
        arrays["footprint_px"] = np.array(
            [r.footprint_px if r.footprint_px else (math.nan,) * 4 for r in rows]
        ).reshape(-1, 4)
        arrays["arm"] = np.array([r.arm for r in rows])
        arrays["frames"] = np.array(sorted(self.refs), dtype=np.int64)
        header = {
            "sync_s": self.sync_s,
            "shift_s": self.shift_s,
            "ground": {
                "image_to_ground": self.ground.image_to_ground,
                "side": self.ground.side,
                "bearing_deg": self.ground.bearing_deg,
            },
            "meta": self.meta,
        }
        arrays["header"] = np.array(json.dumps(header))
        np.savez_compressed(path, **arrays)

    @classmethod
    def load(cls, path: Path) -> ReferenceSet:
        with np.load(path, allow_pickle=False) as z:
            a = {name: z[name] for name in z.files}  # each z[name] decompresses the whole array
        header = json.loads(str(a["header"]))
        g = header["ground"]
        ground = GroundMap(
            tuple(tuple(row) for row in g["image_to_ground"]), g["side"], g["bearing_deg"]
        )
        refs: dict[int, list[Ref]] = {int(f): [] for f in a["frames"]}
        columns = [a[name].tolist() for name in cls._FIELDS]
        boxes = a["footprint_px"].tolist()
        for frame, vid, x, y, heading, in_view, length, width, speed, arm, box in zip(
            *columns, a["arm"].tolist(), boxes, strict=True
        ):
            refs[frame].append(
                Ref(frame, vid, x, y, heading, in_view, length, width, arm, speed,
                    None if math.isnan(box[0]) else tuple(box))
            )  # fmt: skip
        return cls(header["sync_s"], header["shift_s"], ground, refs, header["meta"])


def build_ground_map(repo: Path) -> tuple[GroundMap, dict[str, Any]]:
    """Refit the camera 2 homography from the v2 check points, as
    scripts/build_camera_world_model_v3.py does. Needs the repository's packages."""
    import importlib.util  # noqa: PLC0415
    import statistics  # noqa: PLC0415

    from cairoflow_perception.calibration import fit_homography  # noqa: PLC0415

    name = "checkpoints_ngsim_cam2"
    spec_file = importlib.util.spec_from_file_location(name, repo / f"scripts/{name}.py")
    checkpoints = importlib.util.module_from_spec(spec_file)
    assert spec_file.loader is not None
    spec_file.loader.exec_module(checkpoints)
    pairs = json.loads((repo / SYNC_CHECKPOINTS).read_text("utf-8"))
    # CLIP_IDS["d300"] only labels the spec; the points are the v2 synced check points.
    spec = checkpoints.spec_for(pairs["pairs"], checkpoints.CLIP_IDS["d300"])
    homography, calibration = fit_homography(spec, target_max_check_error_m=1.25)
    scenario = json.loads((repo / SCENARIO).read_text("utf-8"))
    matrix = tuple(tuple(float(a) for a in row) for row in homography.matrix)
    gm = GroundMap(matrix, float(homography._side), float(scenario["frame"]["bearing_deg"]))
    return gm, {
        "fit_points": len(calibration.fit_point_ids),
        "check_median_m": round(statistics.median(calibration.check_errors_m.values()), 4),
        "accepted_under_adr_018": False,
    }


def load_table(path: Path) -> Any:
    """`ngsim.load_trajectories` without its Python row objects: about 60 MB of float64 instead
    of a gigabyte of strings, on a machine with 5.8 GiB. Every column fits a float64 exactly."""
    from cairoflow_perception.ngsim import COLUMNS, Trajectories  # noqa: PLC0415

    with path.open(encoding="utf-8") as handle:
        header = tuple(next(csv.reader(handle)))
    if header != COLUMNS:
        raise ValueError(f"unexpected header {header}; expected {COLUMNS}")
    table = np.loadtxt(path, delimiter=",", quotechar='"', skiprows=1, dtype=np.float64)
    col = {name: table[:, i] for i, name in enumerate(COLUMNS)}
    return Trajectories(
        vehicle_id=col["vehicle_id"].astype(np.int64),
        frame_id=col["frame_id"].astype(np.int64),
        global_time_ms=col["global_time"].astype(np.int64),
        x_ft=col["global_x"].copy(),
        y_ft=col["global_y"].copy(),
        length_ft=col["v_length"].copy(),
        width_ft=col["v_width"].copy(),
        lane_id=col["lane_id"].astype(np.int64),
        int_id=col["int_id"].astype(np.int64),
        section_id=col["section_id"].astype(np.int64),
        direction=col["direction"].astype(np.int64),
        movement=col["movement"].astype(np.int64),
    )


class _Trajectories:
    """The trajectory table, kinematics computed, odd-block rows dropped before anyone reads it.

    Kinematics run on the whole table first because a queued vehicle's heading is carried from
    the rows around it; on a dev row that is the only thing a held-out row can touch, and without
    it the published vehicle set would not reproduce.
    """

    def __init__(self, repo: Path) -> None:
        from cairoflow_perception.ngsim import LocalFrame, kinematics  # noqa: PLC0415
        from cairoflow_perception.ngsim_junction import JunctionFrame  # noqa: PLC0415

        scenario = json.loads((repo / SCENARIO).read_text("utf-8"))
        self.frame = JunctionFrame(
            *scenario["frame"]["origin_state_plane_ft"], scenario["frame"]["bearing_deg"]
        )
        traj = load_table(repo / TRAJECTORY_CSV)
        speed, heading = kinematics(
            traj, LocalFrame(self.frame.origin_x_ft, self.frame.origin_y_ft)
        )
        ticks = np.unique(traj.global_time_ms)
        dev_ticks = np.array([t for t in ticks if tick_is_dev(int(t))], dtype=np.int64)
        keep = np.isin(traj.global_time_ms, dev_ticks)
        self.traj, self.speed, self.heading = traj.subset(keep), speed[keep], heading[keep]
        self.rows_total, self.rows_dev = int(len(traj)), int(keep.sum())
        self.first_tick, self.last_tick = int(ticks.min()), int(ticks.max())


def build_reference_set(
    repo: Path,
    times: Iterable[float],
    *,
    sync_s: float = SYNC_S,
    shift_s: float = 0.0,
    trajectories: _Trajectories | None = None,
) -> ReferenceSet:
    """References for the given source seconds. `shift_s` moves the reference in time and is only
    for the chance-agreement control; it must keep frames in dev blocks to be scored. Needs the
    repository's packages; the result is plain numbers (`ReferenceSet.save`)."""
    from cairoflow_contracts.evidence import IntersectionGeometry  # noqa: PLC0415
    from cairoflow_perception.camera_gate_v3 import headed_references  # noqa: PLC0415

    gate = json.loads((repo / GATE_V3).read_text("utf-8"))
    registered = gate["time_sync"]["registered_source_second_at_0830"]
    if registered != SYNC_S:
        raise ValueError(f"gate v3 registers sync {registered}, the harness assumes {SYNC_S}")
    data = trajectories or _Trajectories(repo)
    ground, calibration = build_ground_map(repo)
    geometry = IntersectionGeometry.model_validate_json((repo / GEOMETRY).read_text("utf-8"))
    lanes = list(geometry.lanes)
    refs: dict[int, list[Ref]] = {}
    held_out_ticks = 0
    for source_s in times:
        require_dev(source_s)
        tick = global_time_ms(source_s + shift_s, sync_s)
        if not data.first_tick <= tick <= data.last_tick:
            continue  # no reference here: unscorable, not empty
        if not tick_is_dev(tick):
            held_out_ticks += 1
            continue  # this sync or shift lands on a held-out tick: unscorable by rule
        frame = tenths(source_s)
        rows = np.flatnonzero(data.traj.global_time_ms == tick)
        size = {
            int(data.traj.vehicle_id[r]): (
                float(data.traj.length_ft[r]) * US_SURVEY_FOOT_M,
                float(data.traj.width_ft[r]) * US_SURVEY_FOOT_M,
            )
            for r in rows
        }
        found = headed_references(data.traj, data.speed, data.heading, data.frame, lanes, tick)
        out = []
        for h in found:
            length_m, width_m = size[h.vehicle_id]
            x, y = h.centre
            lane = f"{h.lane.arm}-{h.lane.direction}" if h.lane is not None else "no-lane"
            out.append(
                Ref(
                    frame,
                    h.vehicle_id,
                    x,
                    y,
                    h.heading_deg,
                    ground.visible(x, y),
                    length_m,
                    width_m,
                    lane,
                    math.nan if h.speed_m_s is None else h.speed_m_s,
                    footprint_box_px(ground, x, y, h.heading_deg, length_m, width_m),
                )
            )
        refs[frame] = out
    return ReferenceSet(
        sync_s,
        shift_s,
        ground,
        refs,
        {
            "harness_version": HARNESS_VERSION,
            "calibration": calibration,
            "trajectory_rows_total": data.rows_total,
            "trajectory_rows_dev": data.rows_dev,
            "frames_dropped_held_out_tick": held_out_ticks,
        },
    )


# ---- association (ported from offline_scorer/association.py) ------------------------------


@dataclass(frozen=True)
class Det:
    """One detection with its ground position, if it could be projected. `group` is the unit a
    precision interval resamples: SAM's object id, or the frame for a detector without ids."""

    frame: int
    det_id: str
    group: str
    x_m: float | None
    y_m: float | None


@dataclass(frozen=True)
class AssocParams:
    across_window_m: float = 3.0
    along_window_m: float = 8.0
    across_scale_m: float = 1.6
    along_scale_m: float = 8.0

    def scaled(self, factor: float) -> AssocParams:
        return replace(
            self,
            across_window_m=self.across_window_m * factor,
            along_window_m=self.along_window_m * factor,
        )


DEFAULT_PARAMS = AssocParams()


@dataclass(frozen=True)
class Match:
    frame: int
    det_id: str
    group: str
    vehicle_id: int
    cost: float
    along_m: float
    across_m: float
    ref_in_view: bool


@dataclass(frozen=True)
class FrameResult:
    frame: int
    n_det: int  # detections with a ground position
    n_det_unprojected: int
    n_ref_in_view: int
    matches: tuple[Match, ...]
    unmatched_dets: tuple[tuple[str, str], ...]  # (det_id, group)
    unmatched_ref_in_view: tuple[int, ...]


def offsets(dx: float, dy: float, heading_deg: float) -> tuple[float, float]:
    """(along, across) of an offset relative to a heading."""
    h = math.radians(heading_deg)
    return dx * math.sin(h) + dy * math.cos(h), dx * math.cos(h) - dy * math.sin(h)


def pair_cost(det: Det, ref: Ref, p: AssocParams) -> tuple[float, float, float] | None:
    """(cost, along, across), or None when the detection is outside either window."""
    if det.x_m is None or det.y_m is None:
        return None
    along, across = offsets(det.x_m - ref.x_m, det.y_m - ref.y_m, ref.heading_deg)
    if abs(along) > p.along_window_m or abs(across) > p.across_window_m:
        return None
    return (across / p.across_scale_m) ** 2 + (along / p.along_scale_m) ** 2, along, across


def associate_frame(
    frame: int, dets: Sequence[Det], refs: Sequence[Ref], p: AssocParams = DEFAULT_PARAMS
) -> FrameResult:
    ids = [r.vehicle_id for r in refs]
    if len(set(ids)) != len(ids):
        raise ValueError(f"frame {frame}: duplicate reference vehicle ids")
    projected = [d for d in dets if d.x_m is not None and d.y_m is not None]
    cands: list[tuple[float, str, int, int, int, float, float]] = []
    for i, d in enumerate(projected):
        for j, r in enumerate(refs):
            found = pair_cost(d, r, p)
            if found is not None:
                cands.append((found[0], d.det_id, r.vehicle_id, i, j, found[1], found[2]))
    cands.sort(key=lambda c: (c[0], c[1], c[2]))
    used_d: set[int] = set()
    used_r: set[int] = set()
    matches = []
    for cost, _det_id, vehicle_id, i, j, along, across in cands:
        if i in used_d or j in used_r:
            continue
        used_d.add(i)
        used_r.add(j)
        d = projected[i]
        matches.append(
            Match(frame, d.det_id, d.group, vehicle_id, round(cost, 6), round(along, 4),
                  round(across, 4), refs[j].in_view)
        )  # fmt: skip
    matched = {m.vehicle_id for m in matches}
    return FrameResult(
        frame=frame,
        n_det=len(projected),
        n_det_unprojected=len(dets) - len(projected),
        n_ref_in_view=sum(r.in_view for r in refs),
        matches=tuple(matches),
        unmatched_dets=tuple(
            sorted((d.det_id, d.group) for i, d in enumerate(projected) if i not in used_d)
        ),
        unmatched_ref_in_view=tuple(
            sorted(r.vehicle_id for r in refs if r.in_view and r.vehicle_id not in matched)
        ),
    )


def associate(
    dets_by_frame: Mapping[int, Sequence[Det]],
    refs_by_frame: Mapping[int, Sequence[Ref]],
    frames: Sequence[int],
    p: AssocParams = DEFAULT_PARAMS,
) -> dict[int, FrameResult]:
    """Associate every frame in `frames` that has a reference entry; a frame without one is
    absent from the result, not scored as empty."""
    return {
        f: associate_frame(f, dets_by_frame.get(f, ()), refs_by_frame[f], p)
        for f in sorted(set(frames))
        if f in refs_by_frame
    }


# ---- metrics (ported from offline_scorer/metrics.py and intervals.py) ---------------------


def cluster_bootstrap_ratio(
    clusters: Sequence[tuple[float, float]],
    *,
    n_boot: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float] | None:
    """95% percentile interval of sum(a)/sum(b) when whole clusters (a, b) are resampled."""
    groups = [(a, b) for a, b in clusters if b > 0]
    if len(groups) < 2:
        return None
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(n_boot):
        num = den = 0.0
        for _ in groups:
            a, b = groups[rng.randrange(len(groups))]
            num += a
            den += b
        if den > 0:
            stats.append(num / den)
    stats.sort()

    def pct(q: float) -> float:
        pos = (len(stats) - 1) * q / 100
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(stats) - 1)
        return stats[lo] + (stats[hi] - stats[lo]) * (pos - lo)

    return (round(pct(2.5), 4), round(pct(97.5), 4)) if stats else None


def vehicle_frame_recall(results: Mapping[int, FrameResult]) -> tuple[int, int]:
    """(matched in-view reference vehicle-frames, in-view reference vehicle-frames)."""
    return (
        sum(m.ref_in_view for r in results.values() for m in r.matches),
        sum(r.n_ref_in_view for r in results.values()),
    )


def precision_proxy(results: Mapping[int, FrameResult]) -> tuple[int, int]:
    """(detections matched to a reference vehicle, detections with a ground position). A match to
    a reference outside the usable view still counts, as in the offline scorer."""
    return (
        sum(len(r.matches) for r in results.values()),
        sum(r.n_det for r in results.values()),
    )


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def summarise(results: Mapping[int, FrameResult], *, intervals: bool = True) -> dict[str, Any]:
    """Recall, precision proxy and any-exposure vehicle recall, each with its counts."""
    per_vehicle: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    per_group: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results.values():
        for m in r.matches:
            per_group[m.group][0] += 1
            per_group[m.group][1] += 1
            if m.ref_in_view:
                per_vehicle[m.vehicle_id][0] += 1
                per_vehicle[m.vehicle_id][1] += 1
        for _det, group in r.unmatched_dets:
            per_group[group][1] += 1
        for v in r.unmatched_ref_in_view:
            per_vehicle[v][1] += 1
    tp, n_ref = vehicle_frame_recall(results)
    matched, n_det = precision_proxy(results)
    ever = sum(a > 0 for a, _b in per_vehicle.values())
    ordered = sorted(results)
    thirds = []
    if ordered:
        base, extra = divmod(len(ordered), 3)
        i = 0
        for b in range(3):
            size = base + (1 if b < extra else 0)
            part = {f: results[f] for f in ordered[i : i + size]}
            i += size
            a, c = vehicle_frame_recall(part)
            thirds.append(_ratio(a, c))
    return {
        "frames_scored": len(results),
        "vehicle_frame_recall": {
            "value": _ratio(tp, n_ref),
            "numerator": tp,
            "denominator": n_ref,
            "ci95_cluster_bootstrap_vehicles": (
                cluster_bootstrap_ratio([tuple(v) for v in per_vehicle.values()])
                if intervals
                else None
            ),
            "by_third": thirds,
        },
        "precision_proxy": {
            "value": _ratio(matched, n_det),
            "numerator": matched,
            "denominator": n_det,
            "ci95_cluster_bootstrap_groups": (
                cluster_bootstrap_ratio([tuple(v) for v in per_group.values()])
                if intervals
                else None
            ),
        },
        "vehicle_recall_any_exposure": {
            "value": _ratio(ever, len(per_vehicle)),
            "numerator": ever,
            "denominator": len(per_vehicle),
        },
        "detections_without_ground_position": sum(r.n_det_unprojected for r in results.values()),
        "mean_detections_per_frame": (
            round(sum(r.n_det for r in results.values()) / len(results), 3) if results else None
        ),
    }


# ---- detectors ----------------------------------------------------------------------------


class Detection(NamedTuple):
    """Image pixels; (x1, y1) is the top-left corner. `score` in 0..1, higher is more confident."""

    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    label: str


@runtime_checkable
class Detector(Protocol):
    """A detector sees one BGR uint8 frame at a time and nothing about the reference."""

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]: ...


def detections_to_dets(
    frame: int, detections: Iterable[Sequence[Any]], ground: GroundMap
) -> list[Det]:
    """Ground points of a frame's boxes: the box centre, as gate v3 places a SAM box."""
    out = []
    for i, d in enumerate(detections):
        x1, y1, x2, y2 = (float(a) for a in d[:4])
        point = ground.to_junction((x1 + x2) / 2, (y1 + y2) / 2)
        x, y = point if point is not None else (None, None)
        out.append(Det(frame, f"{frame}:{i:04d}", str(frame), x, y))
    return out


def load_sam_dets(repo: Path, key: str, ground: GroundMap) -> dict[int, list[Det]]:
    """The recorded SAM observations of a window, keyed by frame, box centres on the ground."""
    window = WINDOWS[key]
    start = window["source_s"][0]
    path = repo / "experiments/runs" / window["sam_run"] / "observations.json"
    by_frame: dict[int, list[Det]] = defaultdict(list)
    for o in json.loads(path.read_text("utf-8")):
        x1, y1, x2, y2 = o["bbox_xyxy_px"]
        point = ground.to_junction((x1 + x2) / 2, (y1 + y2) / 2)
        x, y = point if point is not None else (None, None)
        frame = tenths(start + o["frame"]["timestamp_s"])
        by_frame[frame].append(Det(frame, o["observation_id"], o["object_id"], x, y))
    return dict(by_frame)


@dataclass
class DetectorContext:
    """What a detector factory may use: dev frames to fit on, a scratch directory for anything
    with pixels in it (never the repository), and the --detector-arg values."""

    repo: Path
    scratch: Path
    sampler: FrameSampler
    dev_times: list[float]
    args: dict[str, str]


def write_record(directory: Path, name: str, payload: Mapping[str, Any]) -> Path:
    """Write one JSON record, numbers only, refusing to overwrite (experiments are immutable)."""
    if not name.endswith(".json"):
        raise ValueError("records are JSON")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path
