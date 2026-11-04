"""Video to an analysis directory, with any local detector.

`run_analysis` streams frames, detects, tracks, then re-links, places, classifies, computes and
writes through `write_analysis`. A detector is anything with `detect(frame_bgr)` returning
`(x1, y1, x2, y2, score, label)` tuples (local/scoring.py), so the bake-off winner plugs straight
in. Optional attributes are read for the receipt and nothing is invented when they are absent:
`tool`, `version`, `licence`, `weights_sha256`, `device`, and `describe()` for its parameters.

Frame numbers. `record.fps` is the rate of *analysed* frames (source fps / stride), because every
reader maps frame k to second k / fps; `frame_stride` records how the source was thinned. Frame k
is source second `source_start_s + k / fps`, and `window_s = source_s - window_offset_s`.

A run either completes or leaves nothing: it writes to a hidden sibling and renames, and cancelling
raises `AnalysisCancelled`. A camera 2 window that reaches an odd block is refused before any pixel
is read, unless `held_out_grant` is a read claimed in the log by a committed gate
(cairoflow_evaluation.cv_agreement.grant_held_out_pixels).

Truth rules: a detector's boxes are the only source of positions; nothing is filled in when a stage
has nothing to give. Geometry is always evidence_kind approximated; values stay ungated and speed
withheld, in the record and in the trajectory columns (local/analytics.py).
"""

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from cairoflow_contracts.analysis_record import (
    AnalysisMode,
    AnalysisRecord,
    ArtifactRef,
    CalibrationState,
    StageReceipt,
    TrackEntry,
    VideoSource,
)
from cairoflow_contracts.evidence import Provenance
from cairoflow_perception.analysis_store import write_analysis
from cairoflow_perception.ingest import sha256_file
from cairoflow_perception.local import analytics, render, zones
from cairoflow_perception.local.frames import (
    AVI_FRAME_LAG,
    FrameStats,
    HeldOutGrant,
    camera2_offset_s,
    check_grant,
    held_out_frames,
    is_camera2,
    is_held_out_second,
    iter_frames,
    plan_frames,
    probe_video,
)
from cairoflow_perception.local.scoring import (
    SYNC_S,
    DetectorContext,
    FrameSampler,
    HeldOutError,
    dev_sample_times,
)
from cairoflow_perception.local.tracker import (
    OnlineTracker,
    TrackerParams,
    fill_gaps,
    relink,
    renumber,
)
from cairoflow_perception.site_int2 import SITE_ID, SITE_NAME, VALID_BELOW_ROW_PX, Site

REPO = Path(__file__).resolve().parents[4]
LICENCE_FILE = REPO / "data/licences/ngsim-lankershim-videos.json"
PIPELINE_VERSION = "0.2.1"
PROGRESS_EVERY_FRAMES = 5
PREVIEW_EVERY_S = 1.0
UNCALIBRATED_SITE_ID = "uncalibrated"
# the source-second window gate v3 ran on (data/geometry/.../camera_world_model_gate_v3.json)
GATE_V3_WINDOWS_S = ((970.0, 1000.0),)


class AnalysisError(RuntimeError):
    """The analysis cannot be produced; nothing was written."""


class AnalysisCancelled(RuntimeError):
    """`cancel_check` asked the run to stop; nothing was written."""


@dataclass(frozen=True)
class Progress:
    """One report from a running analysis: real numbers from the frames done so far. `frame` counts
    frames done, `detections` all detections so far, `tracks` the tracks followed right now. A
    field that is not known yet is None and left out of the line, never guessed."""

    stage: str
    elapsed_s: float
    message: str
    frame: int | None = None
    frames_total: int | None = None
    fps: float | None = None
    detections: int | None = None
    tracks: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {name: value for name, value in asdict(self).items() if value is not None}


ProgressCallback = Callable[[Progress], None]


def _git_version() -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
        )
    except OSError:
        return "unknown"
    return done.stdout.strip() or "unknown"


def _detector_receipt(
    detector: Any, frames_in: int, items_out: int, elapsed_s: float
) -> StageReceipt:
    described = getattr(detector, "describe", None)
    parameters: dict[str, str | int | float | bool] = {}
    if callable(described):
        for key, value in described().items():
            parameters[str(key)] = (
                value if isinstance(value, str | int | float | bool) else str(value)
            )
    return StageReceipt(
        stage="detect",
        tool=str(getattr(detector, "tool", type(detector).__name__)),
        version=str(getattr(detector, "version", "unknown")),
        licence=getattr(detector, "licence", None),
        weights_sha256=getattr(detector, "weights_sha256", None),
        device=getattr(detector, "device", None),
        frames_in=frames_in,
        items_out=items_out,
        elapsed_s=round(elapsed_s, 3),
        parameters=parameters,
    )


def _source_for(video_path: Path, sha256: str, camera2: bool) -> VideoSource:
    if not camera2:
        return VideoSource(
            source_id=f"upload-{sha256[:16]}",
            title=video_path.name,
            site_name="unknown site",
            licence_name="user-supplied; licence not verified",
            attribution=f"User-supplied video {video_path.name}",
            source_file=video_path.name,
            source_sha256=sha256,
            uploaded_by_user=True,
        )
    licence = json.loads(LICENCE_FILE.read_text("utf-8"))
    return VideoSource(
        source_id="ngsim-lankershim-cam2-0830",
        title="NGSIM Lankershim Boulevard, camera 2, 08:30-08:45",
        site_name=SITE_NAME,
        captured="16 June 2005",
        licence_name=licence["licence_name"],
        licence_url=licence["licence_url"],
        attribution=licence["attribution"],
        source_file=video_path.name,
        source_sha256=sha256,
    )


def _calibration(site: Site | None) -> CalibrationState:
    if site is None:
        return CalibrationState(
            mode="uncalibrated",
            notes=("No camera calibration: nothing here has a ground position.",),
        )
    return CalibrationState(
        mode="calibrated",
        ground_frame="local_enu_junction_m",
        image_to_ground=tuple(tuple(float(c) for c in row) for row in site.image_to_junction),
        spec_sha256=site.spec_sha256,
        accepted_along_road=False,
        valid_windows_s=GATE_V3_WINDOWS_S,
        valid_below_row_px=VALID_BELOW_ROW_PX,
        origin_lat_deg=site.origin_lat_deg,
        origin_lon_deg=site.origin_lon_deg,
        bearing_deg=site.frame.bearing_deg,
        notes=(
            "A ground position is a detector box centre through the camera homography. Along-road "
            "distances from this homography are not accepted (ADR-018).",
        ),
    )


def validated_seconds(start_s: float, end_s: float) -> float:
    """How much of source seconds [start_s, end_s) lies in a window the geometry check covered."""
    return sum(max(0.0, min(end_s, high) - max(start_s, low)) for low, high in GATE_V3_WINDOWS_S)


def _validated_window_note(start_s: float, end_s: float) -> str | None:
    """What a reader must know when a run is not wholly inside the geometry check's window."""
    windows = ", ".join(f"{low:g}-{high:g}" for low, high in GATE_V3_WINDOWS_S)
    covered = validated_seconds(start_s, end_s)
    if covered >= end_s - start_s:
        return None
    what = "Ground positions, arms and turns"
    if covered == 0:
        return (
            f"This run, source seconds {start_s:g}-{end_s:g}, is outside the validated window: "
            f"the geometry check ran on source seconds {windows} only. {what} here carry no "
            "check of their own."
        )
    return (
        f"Part of this run is outside the validated window: the geometry check ran on source "
        f"seconds {windows}, which cover {covered:g} s of this run's {end_s - start_s:g} s. {what} "
        "outside it carry no check of their own."
    )


def _mid_view_note(mid_view: Mapping[str, int], frame_dt_s: float) -> str:
    edge = f"{zones.ENTRY_MARGIN_PX:g} px of the image's edge"
    rule = (
        f"Entering counts hold tracks first seen within {edge} or in the first "
        f"{zones.entry_opening_s(frame_dt_s):g} s of the window. The published reference counts "
        "every vehicle it holds, so the two definitions differ."
    )
    total = sum(mid_view.values())
    if not total:
        return f"{rule} No counted track began mid-view here."
    arms = ", ".join(f"{arm} {count}" for arm, count in sorted(mid_view.items()))
    return (
        f"{rule} {total} other counted tracks began mid-view ({arms}). Each is either a restarted "
        "track or a queued vehicle the detector first saw moving, so they are left out of "
        "entering counts, which can then run low; they stay in the vehicle total and the turns."
    )


def _disclosures(
    *,
    calibrated: bool,
    camera2: bool,
    frame_stride: int,
    frame_dt_s: float,
    stats: FrameStats,
    interpolated_share: float,
    fragments: int,
    mid_view: Mapping[str, int],
    window_s: tuple[float, float],
    held_out_grant: HeldOutGrant | None,
    notes: Sequence[str],
) -> tuple[str, ...]:
    out = [
        "CairoFlow computer-vision output: boxes come from a detector and tracks from our "
        "procedure. It is not ground truth and no value here was measured on the road.",
        "Every value is ungated: no registered gate has compared it with the published "
        "trajectories for this analysis.",
    ]
    if calibrated:
        out += [
            "Ground positions are approximated through a camera homography. Along-road distances "
            "from it are not accepted (ADR-018), so speed and stopped share are withheld and no "
            "per-point speed is stored.",
            "Arms and turns are coarse junction quadrants from a track's first and last points; "
            "a track the window cut off has no exit and no turn.",
            _mid_view_note(mid_view, frame_dt_s),
            "Vehicle classes without a detector label are inferred from a rough placed size.",
        ]
        outside = _validated_window_note(*window_s)
        if outside:
            out.append(outside)
    else:
        out.append(
            "Uncalibrated: no ground positions, arms, turns, flows, speeds or sizes can be "
            "claimed from this video. Density is on the image grid, in pixels."
        )
    if camera2:
        out.append(
            "Footage is from Los Angeles in 2005, not Cairo. Image rows from "
            f"{VALID_BELOW_ROW_PX} down are the window frame; points there are flagged."
        )
    out.append(
        f"{interpolated_share:.1%} of track points are interpolated across missed detections "
        f"and flagged; {fragments} tracks shorter than {analytics.MIN_TRACK_S:g} s are fragments, "
        "kept in the files and left out of the counts."
    )
    if frame_stride > 1:
        out.append(f"Only every {frame_stride}th source frame was analysed.")
    if stats.ended_early:
        out.append(
            f"The video ended before the requested window: {stats.read} of {stats.requested} "
            "frames were read."
        )
    if stats.unreadable:
        out.append(f"{stats.unreadable} frames could not be read and left a hole in the tracks.")
    if stats.withheld:
        out.append(
            f"{stats.withheld} frame at the start of the window was not read: by the video's own "
            "clock it belongs to the held-out block before it."
        )
    if held_out_grant:
        out.append(
            f"This window was read for registered held-out gate {held_out_grant.gate_id}; it may "
            "not be used to tune anything."
        )
    return (*out, *notes)


def _preview(
    image: np.ndarray,
    boxes: Sequence[tuple[int, float, float, float, float]],
    path: Path,
    source_s: float,
    frame: int,
    attribution: str,
) -> None:
    for track_id, x1, y1, x2, y2 in boxes:
        render.draw_box(
            image, (x1, y1, x2, y2), f"#{track_id}", render.CLASS_COLOURS["vehicle"], thickness=2
        )
    render.draw_banners(
        image, f"source {source_s:.1f}s frame {frame}  {render.CAPTION}", attribution
    )
    render.write_jpeg_atomic(path, image)


def run_analysis(
    video_path: Path,
    start_s: float,
    duration_s: float,
    frame_stride: int,
    detector: Any,
    site: Site | None,
    out_dir: Path,
    analysis_id: str,
    progress_callback: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    *,
    mode: AnalysisMode = "precomputed",
    preview_path: Path | None = None,
    tracker_params: TrackerParams | None = None,
    held_out_grant: HeldOutGrant | None = None,
    source: VideoSource | None = None,
    source_offset_s: float | None = None,
    notes: Sequence[str] = (),
    device: str | None = None,
) -> AnalysisRecord:
    """Analyse `video_path` from `start_s` for `duration_s` into `out_dir`/`analysis_id`.

    `start_s` is in seconds of this file. `source_offset_s` says where its second 0 sits in the
    camera 2 source: 0 for the AVI, the trim start for a clip cut from it, None for any other video.
    Left None, it is what the file itself shows (`camera2_offset_s`), so a known clip keeps the
    block rule. `site` gives a calibrated analysis, None an uncalibrated one; a calibrated run needs
    a known offset. `preview_path` defaults to a file beside the analysis.
    `notes` become disclosures. `held_out_grant` is the logged read of a committed gate; nothing
    else opens an odd block. Raises `AnalysisCancelled` or `AnalysisError` with nothing written, and
    `HeldOutError` for a camera 2 window that reaches an odd block without one.
    """
    video_path = Path(video_path)
    directory = out_dir / analysis_id
    if directory.exists():
        raise AnalysisError(f"{directory} already exists; analyses are immutable")
    started = time.perf_counter()
    info = probe_video(video_path)
    if source_offset_s is None:
        source_offset_s = camera2_offset_s(video_path)
    camera2 = source_offset_s is not None
    if site is not None and not camera2:
        raise AnalysisError("a calibrated analysis needs the video's place in the camera 2 source")
    offset_s = source_offset_s or 0.0
    frame_lag = AVI_FRAME_LAG if is_camera2(video_path) else 0
    plan = plan_frames(info.fps, start_s, duration_s, frame_stride, frame_lag, offset_s)
    if held_out_grant is not None:
        check_grant(held_out_grant, analysis_id)
    skip = held_out_frames(plan) if camera2 and held_out_grant is None else set()
    frames_total = len(plan)
    frame_dt_s = frame_stride / info.fps
    analysis_fps = info.fps / frame_stride
    preview = preview_path or out_dir / f"{analysis_id}.preview.jpg"
    params = tracker_params or TrackerParams()

    def report(stage: str, message: str, **numbers: float | int | None) -> None:
        if progress_callback:
            elapsed_s = round(time.perf_counter() - started, 3)
            progress_callback(
                Progress(stage, elapsed_s, message, frames_total=frames_total, **numbers)
            )

    def check_cancel() -> None:
        if cancel_check and cancel_check():
            raise AnalysisCancelled("cancelled")

    report("hash", f"hashing {video_path.name}")
    video_sha256 = sha256_file(video_path)
    hashed_s = time.perf_counter() - started
    check_cancel()

    tracker = OnlineTracker(frame_dt_s, params)
    stats = FrameStats()
    detect_s = track_s = 0.0
    detections_total = 0
    decoded_at = time.perf_counter()
    decode_s = 0.0
    width_px, height_px = info.width_px, info.height_px
    attribution = (source or _source_for(video_path, video_sha256, camera2)).attribution
    preview_every = max(1, round(PREVIEW_EVERY_S / frame_dt_s))
    loop_started = time.perf_counter()
    report("detect", "reading frames", frame=0)
    for count, (frame, source_s, image) in enumerate(
        iter_frames(
            video_path, start_s, duration_s, frame_stride, frame_lag=frame_lag,
            source_offset_s=offset_s, skip=skip, stats=stats,
        ),
        start=1,
    ):  # fmt: skip
        decode_s += time.perf_counter() - decoded_at
        check_cancel()
        height_px, width_px = image.shape[:2]
        now = time.perf_counter()
        found = detector.detect(image)
        after_detect = time.perf_counter()
        tracker.update(frame, found)
        after_track = time.perf_counter()
        detect_s += after_detect - now
        track_s += after_track - after_detect
        detections_total += len(found)
        if count == 1 or count % preview_every == 0:
            _preview(image, tracker.current_boxes(), preview, source_s, frame, attribution)
        if count == 1 or count % PROGRESS_EVERY_FRAMES == 0:
            rate = count / max(time.perf_counter() - loop_started, 1e-9)
            report(
                "detect", f"frame {frame + 1} of {frames_total}", frame=frame + 1,
                fps=round(rate, 2), detections=detections_total, tracks=len(tracker.active),
            )  # fmt: skip
        decoded_at = time.perf_counter()
    check_cancel()
    assert stats.last_frame is not None  # iter_frames raises when nothing was read
    frame_count = stats.last_frame + 1
    seconds = frame_count * frame_dt_s

    step = time.perf_counter()
    fragments = tracker.finish()
    done = {"frame": frame_count, "detections": detections_total}
    report("relink", "joining fragments", tracks=len(fragments), **done)
    linked = relink(fragments, frame_dt_s, params)
    filled = renumber([fill_gaps(fragment) for fragment in linked])
    relink_s = time.perf_counter() - step
    check_cancel()

    step = time.perf_counter()
    report("place", "placing and classifying tracks", tracks=len(filled), **done)
    valid_below = VALID_BELOW_ROW_PX if site is not None or camera2 else None
    placed = [
        analytics.place_track(
            t, site, frame_dt_s, valid_below_row_px=valid_below,
            image_size_px=(width_px, height_px),
        )
        for t in filled
    ]  # fmt: skip
    place_s = time.perf_counter() - step

    step = time.perf_counter()
    report("compute", "computing metrics", tracks=len(placed), **done)
    calibrated = site is not None
    metrics = analytics.compute_metrics(placed, calibrated=calibrated, seconds=seconds)
    series = analytics.compute_series(
        placed, calibrated=calibrated, seconds=seconds, fps=analysis_fps
    )
    movements = analytics.compute_movements(placed)
    grid = analytics.density_grid(placed, width_px=width_px, height_px=height_px)
    density = analytics.density_bytes(grid)
    columns = analytics.columns_of(placed)
    mid_view = analytics.mid_view_starts(placed)
    compute_s = time.perf_counter() - step

    entries: list[TrackEntry] = []
    first_row = 0
    for p in placed:
        n = len(p.track)
        entries.append(
            TrackEntry(
                track_id=p.track.track_id,
                first_row=first_row,
                start_frame=p.track.start_frame,
                n=n,
                vehicle_class=p.vehicle_class,
                class_source=p.class_source,
                class_confidence=p.class_confidence,
                length_m=p.length_m,
                width_m=p.width_m,
                approach=p.movement.approach,
                exit_arm=p.movement.exit_arm,
                turn=p.movement.turn,
                status=p.status,
            )
        )
        first_row += n
    rows = len(columns)
    interpolated_rows = sum(int(p.track.interpolated.sum()) for p in placed)
    counted = sum(p.counted for p in placed)
    version = _git_version()
    now_utc = datetime.now(UTC).replace(microsecond=0)
    resolved_source = source or _source_for(video_path, video_sha256, camera2)
    repo_tool = "cairoflow_perception.local"
    stages = (
        StageReceipt(
            stage="decode", tool="opencv", version=cv2.__version__, licence="Apache-2.0",
            device="cpu", frames_in=stats.requested, items_out=stats.read,
            elapsed_s=round(decode_s + hashed_s, 3),
            parameters={
                "frame_stride": frame_stride, "frame_lag": frame_lag,
                "unreadable": stats.unreadable, "withheld": stats.withheld,
                "ended_early": stats.ended_early,
            },
        ),
        _detector_receipt(detector, stats.read, detections_total, detect_s),
        StageReceipt(
            stage="track", tool=f"{repo_tool}.tracker.OnlineTracker", version=version,
            licence="MIT", device="cpu", frames_in=stats.read, items_out=len(fragments),
            elapsed_s=round(track_s, 3), parameters=params.as_parameters(),
        ),
        StageReceipt(
            stage="relink", tool=f"{repo_tool}.tracker.relink", version=version, licence="MIT",
            device="cpu", frames_in=stats.read, items_out=len(filled),
            elapsed_s=round(relink_s, 3),
            parameters={"fragments_in": len(fragments), "interpolated_rows": interpolated_rows},
        ),
        StageReceipt(
            stage="place", tool=f"{repo_tool}.analytics.place_track", version=version,
            licence="MIT", device="cpu", frames_in=stats.read, items_out=len(placed),
            elapsed_s=round(place_s, 3),
            parameters={"calibrated": calibrated, "rows": rows},
        ),
        StageReceipt(
            stage="compute", tool=f"{repo_tool}.analytics", version=version, licence="MIT",
            device="cpu", frames_in=stats.read, items_out=len(metrics) + len(series),
            elapsed_s=round(compute_s, 3),
            parameters={
                "counted_tracks": counted,
                "mid_view_starts": sum(mid_view.values()),
                **{f"mid_view_starts_{arm.lower()}": mid_view[arm] for arm in sorted(mid_view)},
            },
        ),
    )  # fmt: skip
    source_start_s = round(offset_s + start_s, 6)
    file_parameters: dict[str, str | int | float | bool] = {"file_start_s": start_s}
    if camera2:
        file_parameters["source_offset_s"] = offset_s
    if held_out_grant is not None:
        file_parameters["held_out_gate_id"] = held_out_grant.gate_id
        file_parameters["held_out_gate_sha256"] = held_out_grant.gate_sha256
    record = AnalysisRecord(
        analysis_id=analysis_id,
        title=f"CairoFlow CV, source {source_start_s:.0f}-{source_start_s + seconds:.0f} s",
        mode=mode,
        data_origin="camera_cv",
        site_id=SITE_ID if calibrated else UNCALIBRATED_SITE_ID,
        created_at_utc=now_utc,
        source=resolved_source,
        fps=analysis_fps,
        frame_count=frame_count,
        frame_stride=frame_stride,
        width_px=width_px,
        height_px=height_px,
        source_start_s=source_start_s,
        window_offset_s=SYNC_S if camera2 else 0.0,
        calibration=_calibration(site),
        stages=stages,
        metrics=metrics,
        series=series,
        movements=movements,
        artifacts=(
            ArtifactRef(
                role="trajectories", path="trajectories.bin", media_type="application/octet-stream"
            ),
            ArtifactRef(role="tracks", path="tracks.json", media_type="application/json"),
        ),
        disclosures=_disclosures(
            calibrated=calibrated,
            camera2=camera2,
            frame_stride=frame_stride,
            frame_dt_s=frame_dt_s,
            stats=stats,
            interpolated_share=interpolated_rows / rows if rows else 0.0,
            fragments=len(placed) - counted,
            mid_view=mid_view,
            window_s=(source_start_s, source_start_s + seconds),
            held_out_grant=held_out_grant,
            notes=notes,
        ),
        provenance=Provenance(
            kind="approximated",
            produced_by="cairoflow_perception.local.pipeline",
            produced_at_utc=now_utc,
            input_sha256=(video_sha256,),
            parameters={
                **file_parameters, "duration_s": duration_s, "frame_stride": frame_stride,
                "frame_lag": frame_lag, "pipeline_version": PIPELINE_VERSION,
                "repo_version": version,
                "device": device or str(getattr(detector, "device", "unspecified")),
            },
            software={
                "python": platform.python_version(), "numpy": np.__version__,
                "opencv": cv2.__version__,
            },
        ),
    )  # fmt: skip
    hint = (
        "uv run --frozen python scripts/render_annotated_video.py "
        f"--analysis <dir>/{analysis_id} --video <path to {resolved_source.source_file}>"
    )
    extras = [
        (
            ArtifactRef(role="density", path="density.json", media_type="application/json"),
            density,
        ),
        (
            ArtifactRef(
                role="annotated_video",
                path=render.ANNOTATED_VIDEO,
                media_type="video/mp4",
                bundled=False,
                licence_note=f"carries the source's pixels: {resolved_source.licence_name}",
                rebuild_hint=hint,
            ),
            None,
        ),
    ]
    check_cancel()
    report("write", "writing the analysis", tracks=len(placed), **done)
    partial = out_dir / f".{analysis_id}.partial-{os.getpid()}"
    try:
        written = write_analysis(partial, record, entries, columns, extras)
        partial.rename(directory)
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    report("write", f"wrote {directory}", tracks=len(placed), **done)
    return written


# ---- choosing a detector -------------------------------------------------------------------


@dataclass(frozen=True)
class DetectorSettings:
    """data/cv/detector.json, committed after the bake-off.

    {"detector": "median-blob" or "package.module:factory",
     "args": {"name": "value"},          passed to the factory as DetectorContext.args
     "requires_cuda": false,             true: check for CUDA and fall back to CPU without it
     "cpu_min_frame_stride": 3,          the stride a CPU fallback must use at least
     "tracker": {"high_score": 0.35}}    overrides of TrackerParams fields
    """

    detector: str
    args: dict[str, str] = field(default_factory=dict)
    requires_cuda: bool = False
    cpu_min_frame_stride: int = 1
    tracker: dict[str, float] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> DetectorSettings:
        raw = json.loads(path.read_text("utf-8"))
        return cls(
            detector=str(raw["detector"]),
            args={str(k): str(v) for k, v in raw.get("args", {}).items()},
            requires_cuda=bool(raw.get("requires_cuda", False)),
            cpu_min_frame_stride=int(raw.get("cpu_min_frame_stride", 1)),
            tracker=dict(raw.get("tracker", {})),
        )

    def tracker_params(self) -> TrackerParams:
        return TrackerParams(**self.tracker)


def cuda_available() -> bool:
    """Whether torch can see a GPU. Imported only here, at run time, never at collection."""
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        return False
    return bool(torch.cuda.is_available())


@dataclass(frozen=True)
class DevicePlan:
    device: str
    frame_stride: int
    notes: tuple[str, ...]


def plan_device(settings: DetectorSettings, frame_stride: int) -> DevicePlan:
    """Keep the asked stride on CUDA; without CUDA use CPU with the stride raised, and say so."""
    if not settings.requires_cuda:
        return DevicePlan("cpu", frame_stride, ())
    if cuda_available():
        return DevicePlan("cuda", frame_stride, ())
    stride = max(frame_stride, settings.cpu_min_frame_stride)
    note = f"CUDA was not available, so the detector ran on CPU at frame stride {stride}"
    if stride != frame_stride:
        note += f" instead of the requested {frame_stride}"
    return DevicePlan("cpu", stride, (note + ".",))


class ClipSampler:
    """`FrameSampler`'s `read` for any other video, by seconds of that file. A clip of camera 2
    (`source_offset_s` known) keeps the held-out rule: a held-out second raises `HeldOutError`."""

    def __init__(self, path: Path, source_offset_s: float | None = None) -> None:
        self._capture = cv2.VideoCapture(str(path))
        self._fps = probe_video(path).fps
        self._offset = source_offset_s

    def read(self, file_s: float) -> np.ndarray:
        if self._offset is not None and is_held_out_second(self._offset + file_s):
            raise HeldOutError(f"source second {self._offset + file_s} is held out")
        self._capture.set(cv2.CAP_PROP_POS_FRAMES, int(file_s * self._fps))  # the frame on show
        ok, image = self._capture.read()
        if not ok or image is None:
            raise OSError(f"no frame at {file_s} s")
        return image

    def close(self) -> None:
        self._capture.release()


def resolve_factory(name: str) -> Callable[[DetectorContext], Any]:
    """`name` is `package.module:factory`, or a module of cairoflow_perception.local."""
    module, _, attribute = name.partition(":")
    if not attribute:
        module, attribute = f"cairoflow_perception.local.{name.replace('-', '_')}", "make_detector"
    return getattr(importlib.import_module(module), attribute)


def build_detector(
    name: str,
    args: dict[str, str],
    video_path: Path,
    *,
    scratch: Path | None = None,
    source_offset_s: float | None = None,
) -> Any:
    """A detector from its factory. A factory may fit on frames, so it only ever gets frames that
    development may read: dev frames of the camera 2 AVI, or for a clip of it (`source_offset_s`
    known, or shown by the file) the frames outside its held-out seconds; on any other video, frames
    spread through it."""
    scratch = scratch or Path(os.environ.get("CAIROFLOW_CV_SCRATCH", "~/scratch-cv")).expanduser()
    factory = resolve_factory(name)
    if is_camera2(video_path):
        with FrameSampler(video_path) as sampler:
            return factory(DetectorContext(REPO, scratch, sampler, dev_sample_times(), args))
    if source_offset_s is None:
        source_offset_s = camera2_offset_s(video_path)
    sampler = ClipSampler(video_path, source_offset_s)
    try:
        info = probe_video(video_path)
        spread = [info.duration_s * (i + 0.5) / 60 for i in range(60)]
        if source_offset_s is not None:
            spread = [t for t in spread if not is_held_out_second(source_offset_s + t)]
        return factory(DetectorContext(REPO, scratch, sampler, spread, args))
    finally:
        sampler.close()
