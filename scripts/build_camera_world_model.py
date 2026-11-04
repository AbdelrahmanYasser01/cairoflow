"""Place camera 2's recorded SAM 3.1 tracks in the intersection 2 world model and apply the
pre-registered lane gate (P1-1). No model call, no spend.

    uv run --frozen python scripts/build_camera_world_model.py [--record]

Every input is checked against the hash the gate file
(data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate.json, committed before this
script first ran) holds for it; a missing input stops the run with the command that makes it.
The tracks are placed through the camera 2 calibration into the world model's junction frame and
lanes (cairoflow_perception.camera_world), paired with the published NGSIM vehicles at the
synced instants, and the gate decides go or no-go. Both outcomes are recorded.

Output: experiments/runs/ngsim-cam2-world-model-<stamp>/ (Git-ignored) with comparison.json and,
on go, camera_layer.json. With --record: the summary also goes to
experiments/records/ngsim-cam2-world-model-<stamp>.json, and the tracks, the comparison, the
camera layer (on go) and a NOTICE (CC BY-SA 4.0, as the overlay) to the folder of that name.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

from pydantic import TypeAdapter

from cairoflow_contracts.evidence import (
    CameraLayer,
    IntersectionGeometry,
    Observation,
    Provenance,
    SourceClip,
)
from cairoflow_perception.calibration import fit_homography, spec_sha256
from cairoflow_perception.camera_world import (
    Placement,
    apply_gate,
    compare,
    place_track,
    reference_at,
)
from cairoflow_perception.ngsim import LocalFrame, kinematics, load_trajectories
from cairoflow_perception.ngsim_junction import JunctionFrame
from cairoflow_perception.tracking import TrackingResult

REPO = Path(__file__).resolve().parents[1]
GATE = REPO / "data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate.json"
SCENARIO = REPO / "data/geometry/ngsim-lankershim-int2/scenario.json"
CHECKPOINTS = REPO / "scripts/checkpoints_ngsim_cam2.py"
SAM_RECORD = REPO / "experiments/records/ngsim-cam2-sam-20261028T151050Z.json"
LICENCE = REPO / "data/licences/ngsim-lankershim-videos.json"
SOURCE_AT_0830_S = 119.7  # DATA_REGISTER.md §15, as the gate file states
T0_MS = 1118935800000  # 2005-06-16 08:30:00.0 PDT, the world model window's start
CAMERA = "cam2"
HOW_TO_MAKE = {
    "sam_tracks": "scripts/fetch_ngsim_data.py --restore-camera-inputs",
    "sam_observations": "scripts/fetch_ngsim_data.py --restore-camera-inputs",
    "camera_clip_manifest": "scripts/fetch_ngsim_data.py --restore-camera-inputs",
    "reference_trajectories": "scripts/fetch_ngsim_data.py --download trajectories",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _checkpoints_module():
    spec = importlib.util.spec_from_file_location("checkpoints_ngsim_cam2", CHECKPOINTS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def input_paths(gate: dict, sam_run: Path | None) -> dict[str, Path]:
    """Each pinned input's path; `<SAM_RUN>` (a gate written before its SAM request was sent)
    stands for the run directory that request produced."""
    paths = {}
    for name, item in gate["inputs"].items():
        if isinstance(item, dict) and "path" in item:
            text = item["path"]
            if "<SAM_RUN>" in text:
                if sam_run is None:
                    raise SystemExit(f"the gate's {name} names <SAM_RUN>: pass --sam-run DIR")
                text = text.replace("<SAM_RUN>", str(sam_run.relative_to(REPO)))
            paths[name] = REPO / text
    return paths


def check_inputs(gate: dict, paths: dict[str, Path]) -> list[str]:
    """Missing inputs, and inputs whose hash differs from the gate's; an input the gate could not
    pin in advance (the output of a request sent after it) has a null hash and is pinned by the
    run's record instead."""
    problems = []
    for name, path in paths.items():
        pinned = gate["inputs"][name].get("sha256")
        if not path.is_file():
            hint = HOW_TO_MAKE.get(name, "see docs/DATA_REGISTER.md")
            problems.append(f"{name}: {path.relative_to(REPO)} is missing; make it with {hint}")
        elif pinned is not None and sha256_file(path) != pinned:
            problems.append(f"{name}: {path.relative_to(REPO)} does not match the gate's hash")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true", help="write the committed record")
    parser.add_argument("--gate", type=Path, default=GATE, help="a pre-registered gate file")
    parser.add_argument("--sam-run", type=Path, help="the SAM run a gate's <SAM_RUN> names")
    parser.add_argument("--sam-record", type=Path, default=SAM_RECORD,
                        help="the committed summary of that SAM run")  # fmt: skip
    args = parser.parse_args(argv)
    gate_path = args.gate.resolve()
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    paths = input_paths(gate, Path(os.path.abspath(args.sam_run)) if args.sam_run else None)
    problems = check_inputs(gate, paths)
    if problems:
        print("inputs do not match the pre-registered gate:", *problems, sep="\n  ",
              file=sys.stderr)  # fmt: skip
        return 2
    inputs = paths
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    frame = JunctionFrame(*scenario["frame"]["origin_state_plane_ft"],
                          scenario["frame"]["bearing_deg"])  # fmt: skip
    geometry = IntersectionGeometry.model_validate_json(
        inputs["world_model_geometry"].read_text("utf-8")
    )
    lanes = geometry.lanes

    checkpoints = _checkpoints_module()
    sync = json.loads(inputs["calibration_pairs"].read_text("utf-8"))
    spec = checkpoints.spec_for(sync["pairs"], checkpoints.CLIP_IDS["d300"])
    homography, calibration = fit_homography(spec, target_max_check_error_m=1.25)
    check_median = statistics.median(calibration.check_errors_m.values())
    placement = Placement(homography, frame, check_median)

    clip = SourceClip.model_validate_json(inputs["camera_clip_manifest"].read_text("utf-8"))
    offset = round(clip.trim.start_s - SOURCE_AT_0830_S, 6)  # world-model second of frame 0
    tracking = TrackingResult.model_validate_json(inputs["sam_tracks"].read_text("utf-8"))
    observations = TypeAdapter(tuple[Observation, ...]).validate_json(
        inputs["sam_observations"].read_text("utf-8")
    )
    confidence = {o.observation_id: o.confidence for o in observations}
    tracks, unprojected = [], []
    for track in sorted(tracking.tracks, key=lambda t: t.track_id):
        placed, lost = place_track(track, placement, lanes, camera=CAMERA, window_offset_s=offset,
                                   confidence=confidence)  # fmt: skip
        unprojected += lost
        if placed is not None:
            tracks.append(placed)

    traj = load_trajectories(inputs["reference_trajectories"])
    _, heading = kinematics(traj, LocalFrame(frame.origin_x_ft, frame.origin_y_ft))
    references, no_heading = {}, 0
    for f in clip.frames:
        tick = T0_MS + round((offset + f.timestamp_s) * 10) * 100
        references[f.frame_index], missing = reference_at(traj, heading, frame, lanes, tick)
        no_heading += missing
    metrics = compare(tracks, references, lanes)
    result = apply_gate(metrics, gate["gate_id"], sha256_file(gate_path))

    points = [p for t in tracks for p in t.points]
    status = Counter(p.lane_status for p in points)
    counts = {
        "input_observations": len(observations),
        "tracked_observations": sum(len(t.points) for t in tracking.tracks),
        "projected_points": len(points),
        "unprojected_points": len(unprojected),
        "in_lane": status["in_lane"],
        "outside_lanes": status["outside_lanes"],
        "ambiguous": status["ambiguous"],
        "near_lane_boundary": sum(p.near_lane_boundary for p in points),
        "reference_vehicles_without_heading": no_heading,
    }
    presence = Counter(lane for t in tracks for lane in {p.lane_id for p in t.points} if lane)
    sam = json.loads(args.sam_record.read_text(encoding="utf-8"))
    assessment = sync["calibration_assessment"]
    now = datetime.now(UTC).replace(microsecond=0)
    out = REPO / "experiments/runs" / f"ngsim-cam2-world-model-{now:%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True)
    layer = None
    if result.outcome == "go":
        layer = CameraLayer(
            label="camera-derived: SAM 3.1 boxes of camera 2 through its calibration "
            "(approximated; the calibration is not accepted for along-road distances, ADR-018)",
            camera_clip_id=clip.clip_id, camera_clip_sha256=clip.output_sha256,
            sam_model=sam["requests"][0]["model"],
            sam_request_keys=tuple(r["request_key"] for r in sam["requests"]),
            tracks_sha256=sha256_file(inputs["sam_tracks"]),
            calibration_spec_sha256=spec_sha256(spec), calibration_accepted=False,
            calibration_notes=(
                f"held-out errors: alternate split worst {assessment['check_max_m']:.3f} m, "
                f"swapped split worst {assessment['swapped_split']['check_max_m']:.3f} m; lane "
                f"lines within {assessment['lane_line_check']['max_abs_m']:.2f} m",
                f"lateral placement checked against NGSIM by the pre-registered gate "
                f"{gate['gate_id']}: {result.outcome}",
            ),
            frame=f"homography to ADR-017 grid metres, then rotation by "
            f"{scenario['frame']['bearing_deg']} deg into the world model's junction frame",
            time_sync=gate["inputs"]["time_sync"],
            window_s=(offset, round(offset + clip.output_stream.duration_s, 6)),
            tracks=tuple(tracks), counts=counts, lane_presence=dict(sorted(presence.items())),
            derived=("lane", "presence", "speed (box centre, 0.5 s half window)"),
            not_derived=("stop-line crossings and turning movements", "queue lengths",
                         "counts of vehicles SAM did not box (recall 0.28, A3)"),
            gate=result,
            provenance=Provenance(
                kind="approximated", produced_by="cairoflow_perception.camera_world",
                produced_at_utc=now,
                input_sha256=tuple(sha256_file(p) for p in inputs.values()),
                parameters={"gate_id": gate["gate_id"], "box_point": "centre",
                            "sigma_px": 2.0, "check_median_m": round(check_median, 4)},
                software={"cairoflow": metadata.version("cairoflow")},
            ),
        )  # fmt: skip
        write_new(out / "camera_layer.json", layer.model_dump_json(indent=2) + "\n")
    comparison = {
        "script": "scripts/build_camera_world_model.py",
        "created_at_utc": now.isoformat(),
        "run_dir": str(out.relative_to(REPO)),
        "label": "camera tracks (SAM 3.1 output) placed by our procedure and compared with "
        "published NGSIM trajectories; not ground truth on either side",
        "gate_file": {"path": str(gate_path.relative_to(REPO)), "sha256": result.gate_sha256},
        "inputs": {name: sha256_file(path) for name, path in inputs.items()},
        "calibration": {
            "spec_sha256": spec_sha256(spec),
            "fit_points": len(calibration.fit_point_ids),
            "check_median_m": round(check_median, 4),
            "accepted_under_adr_018": False,
        },  # fmt: skip
        "window_s": [offset, round(offset + clip.output_stream.duration_s, 6)],
        "counts": counts,
        "metrics": metrics,
        "gate": result.model_dump(mode="json"),
        "lane_presence": dict(sorted(presence.items())),
        "tracks": [
            {
                "track_id": t.track_id,
                "points": len(t.points),
                "lane_id": t.lane_id,
                "lane_share": t.lane_share,
                "matched_ngsim_vehicle": metrics["matched_vehicle_of_track"].get(t.track_id),
            }
            for t in tracks
        ],  # fmt: skip
        "camera_layer_sha256": sha256_file(out / "camera_layer.json") if layer else None,
    }
    text = json.dumps(comparison, indent=2) + "\n"
    write_new(out / "comparison.json", text)
    if args.record:
        attribution = json.loads(LICENCE.read_text(encoding="utf-8"))["attribution"]
        records = REPO / "experiments/records"
        write_new(records / f"{out.name}.json", text)
        folder = records / out.name
        folder.mkdir()
        shutil.copyfile(inputs["sam_tracks"], folder / "tracks.json")
        if layer:
            shutil.copyfile(out / "camera_layer.json", folder / "camera_layer.json")
        write_new(folder / "NOTICE.md", (
            "# Camera 2 tracks placed in the intersection 2 world model (CC BY-SA 4.0)\n\n"
            "`tracks.json` holds SAM 3.1 boxes on NGSIM Lankershim camera 2 footage (source "
            f"seconds {clip.trim.start_s:g}-{clip.trim.start_s + clip.output_stream.duration_s:g}"
            "), turned into tracks by CairoFlow's procedure; `camera_layer.json` places them in "
            "the world model's frame and lanes. Both derive from the camera 2 video, "
            f"{attribution} "
            "They are shared under CC BY-SA 4.0, as the overlay (decision D3). Model output and "
            "our placement, not ground truth.\n"
        ))  # fmt: skip
    print(f"wrote {out.relative_to(REPO)}: gate {result.outcome}")
    for name, ok in result.checks.items():
        print(f"  [{'pass' if ok else 'FAIL'}] {name}: {metrics[name]}")
    for reason in result.reasons:
        print(f"  no-go: {reason}")
    print(f"  counts: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
