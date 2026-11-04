"""Apply camera gate v3 (ADR-031) to camera 2's recorded SAM 3.1 tracks (P1-1). No model call.

    uv run --frozen python scripts/build_camera_world_model_v3.py \\
        --gate data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate_v3.json \\
        --sam-run experiments/runs/ngsim-cam2-sam-<stamp> \\
        --sam-record experiments/records/ngsim-cam2-sam-<stamp>.json [--record]

    # exploration only: v3's procedure on data another gate already judged
    uv run --frozen python scripts/build_camera_world_model_v3.py \\
        --gate data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate_v3.json \\
        --explore-inputs-of \\
        data/geometry/ngsim-lankershim-cam2/v2/camera_world_model_gate_v2.json \\
        --sam-run experiments/runs/ngsim-cam2-sam-20261030T220322Z

The gate file must hold this code's parameters and thresholds exactly (camera_gate_v3.PARAMETERS,
THRESHOLDS), and every input must hash as the gate pins it. Tracks are placed as gates v1/v2
place them (camera_world.place_track); references are read at the gate's registered time sync;
camera_gate_v3 pairs, measures and decides. Beside the result, deciding nothing: v3 at the other
candidate sync, v1/v2's procedure at both, and the two pre-registered predictions.

Output: experiments/runs/ngsim-cam2-world-model-v3-<stamp>/ (Git-ignored) with comparison.json,
points.json and, on go, camera_layer.json. With --record the summary also goes to
experiments/records/ngsim-cam2-world-model-v3-<stamp>.json, and the tracks, points, camera layer
(on go) and a NOTICE (CC BY-SA 4.0) to the folder of that name. Exploration writes
experiments/diagnostics/camera-gate-v3-procedure-on-<gate id>-<stamp>/ and never records or
builds a camera layer.
"""

from __future__ import annotations

import argparse
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
from cairoflow_perception.camera_gate_v3 import (
    PARAMETERS,
    THRESHOLDS,
    apply_gate_v3,
    excluded_by_window,
    headed_references,
    measure,
    pair_tracks,
    predictions,
    stop_box,
    summarise,
)
from cairoflow_perception.camera_world import Placement, Reference, compare, place_track
from cairoflow_perception.ngsim import LocalFrame, kinematics, load_trajectories
from cairoflow_perception.ngsim_junction import JunctionFrame
from cairoflow_perception.tracking import TrackingResult

REPO = Path(__file__).resolve().parents[1]
LICENCE = REPO / "data/licences/ngsim-lankershim-videos.json"
GATED = ("matched_tracks", "matched_points", "A_track_match_share", "B_points",
         "B_point_lane_agreement", "C_tracks", "C_track_lane_agreement", "D_across_heading_p90_m",
         "J_points", "J_tracks", "J_across_heading_p90_m")  # fmt: skip
V2_KEYS = ("matched_tracks", "matched_points", "B_point_lane_agreement", "C_tracks",
           "C_track_lane_agreement", "D_lateral_p90_m", "along_median_m")  # fmt: skip


def _builder():
    """The v1/v2 runner, for its input checks and constants (it is not changed)."""
    path = REPO / "scripts/build_camera_world_model.py"
    spec = importlib.util.spec_from_file_location("build_camera_world_model", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gate", type=Path, required=True, help="the gate v3 file")
    parser.add_argument("--sam-run", type=Path, help="the SAM run a gate's <SAM_RUN> names")
    parser.add_argument("--sam-record", type=Path, help="the committed summary of that SAM run")
    parser.add_argument("--record", action="store_true", help="write the committed record")
    parser.add_argument(
        "--explore-inputs-of",
        type=Path,
        help="exploration: another gate whose (already judged) inputs to use",
    )
    parser.add_argument("--out-root", type=Path, help="where the output folder goes (tests)")
    args = parser.parse_args(argv)  # fmt: skip
    builder = _builder()
    gate_path = args.gate.resolve()
    gate = json.loads(gate_path.read_text("utf-8"))
    if gate.get("parameters") != PARAMETERS or gate.get("thresholds") != THRESHOLDS:
        print("the gate file's parameters or thresholds differ from camera_gate_v3's",
              file=sys.stderr)  # fmt: skip
        return 2
    exploring = args.explore_inputs_of is not None
    if exploring and args.record:
        print("exploration never writes a record", file=sys.stderr)
        return 2
    if not exploring and args.sam_record is None:
        print("a gate run names its SAM record (--sam-record)", file=sys.stderr)
        return 2
    source = json.loads(args.explore_inputs_of.read_text("utf-8")) if exploring else gate
    paths = builder.input_paths(
        source, Path(os.path.abspath(args.sam_run)) if args.sam_run else None
    )
    problems = builder.check_inputs(source, paths)
    if problems:
        print("inputs do not match the gate:", *problems, sep="\n  ", file=sys.stderr)
        return 2
    sync = gate["time_sync"]
    registered = sync["registered_source_second_at_0830"]
    other = sync["other_candidate_source_second_at_0830"]

    # placement, exactly as build_camera_world_model.py places gates v1/v2's tracks
    scenario = json.loads(builder.SCENARIO.read_text("utf-8"))
    frame = JunctionFrame(*scenario["frame"]["origin_state_plane_ft"],
                          scenario["frame"]["bearing_deg"])  # fmt: skip
    geometry = IntersectionGeometry.model_validate_json(
        paths["world_model_geometry"].read_text("utf-8"))  # fmt: skip
    lanes, box = geometry.lanes, stop_box(geometry)
    checkpoints = builder._checkpoints_module()
    pairs = json.loads(paths["calibration_pairs"].read_text("utf-8"))
    spec = checkpoints.spec_for(pairs["pairs"], checkpoints.CLIP_IDS["d300"])
    homography, calibration = fit_homography(spec, target_max_check_error_m=1.25)
    check_median = statistics.median(calibration.check_errors_m.values())
    placement = Placement(homography, frame, check_median)
    clip = SourceClip.model_validate_json(paths["camera_clip_manifest"].read_text("utf-8"))
    offset = round(clip.trim.start_s - registered, 6)  # world-model second of frame 0
    tracking = TrackingResult.model_validate_json(paths["sam_tracks"].read_text("utf-8"))
    observations = TypeAdapter(tuple[Observation, ...]).validate_json(
        paths["sam_observations"].read_text("utf-8"))  # fmt: skip
    confidence = {o.observation_id: o.confidence for o in observations}
    tracks, unprojected = [], []
    for track in sorted(tracking.tracks, key=lambda t: t.track_id):
        placed, lost = place_track(track, placement, lanes, camera=builder.CAMERA,
                                   window_offset_s=offset, confidence=confidence)  # fmt: skip
        unprojected += lost
        if placed is not None:
            tracks.append(placed)
    traj = load_trajectories(paths["reference_trajectories"])
    speed, heading = kinematics(traj, LocalFrame(frame.origin_x_ft, frame.origin_y_ft))

    def references(source_at_0830: float) -> dict:
        start = round(clip.trim.start_s - source_at_0830, 6)
        return {f.frame_index: headed_references(
                    traj, speed, heading, frame, lanes,
                    builder.T0_MS + round((start + f.timestamp_s) * 10) * 100)
                for f in clip.frames}  # fmt: skip

    def plain(refs: dict) -> dict:
        return {i: [Reference(r.vehicle_id, r.centre, r.lane) for r in found]
                for i, found in refs.items()}  # fmt: skip

    # the gate: v3 at the registered sync
    refs, refs_other = references(registered), references(other)
    pairing = pair_tracks(tracks, refs)
    rows = measure(tracks, refs, pairing, lanes, box)
    metrics = summarise(rows, tracks, pairing)
    metrics["window_excluded_points"] = excluded_by_window(tracks, refs, pairing)
    gate_sha = builder.sha256_file(gate_path)
    result = apply_gate_v3(metrics, gate["gate_id"], gate_sha)

    # beside it, deciding nothing
    pairing_other = pair_tracks(tracks, refs_other)
    metrics_other = summarise(measure(tracks, refs_other, pairing_other, lanes, box), tracks,
                              pairing_other)  # fmt: skip
    v2_registered = compare(tracks, plain(refs), lanes)
    v2_other = compare(tracks, plain(refs_other), lanes)
    v2_vehicle = v2_registered["matched_vehicle_of_track"]
    outcome_other = apply_gate_v3(metrics_other, gate["gate_id"], gate_sha).outcome
    sensitivity = {
        "v3_at_other_sync": {k: metrics_other[k] for k in GATED}
        | {"outcome_if_it_decided": outcome_other},
        "v1_v2_procedure_at_registered_sync": {k: v2_registered[k] for k in V2_KEYS},
        "v1_v2_procedure_at_other_sync": {k: v2_other[k] for k in V2_KEYS},
        "tracks_paired_with_another_vehicle_by_v1_v2": sorted(
            t for t, v in metrics["matched_vehicle_of_track"].items()
            if t in v2_vehicle and v2_vehicle[t] != v),
        "tracks_matched_by_v1_v2_only": sorted(
            set(v2_vehicle) - set(metrics["matched_vehicle_of_track"])),
        "tracks_matched_by_v3_only": sorted(
            set(metrics["matched_vehicle_of_track"]) - set(v2_vehicle)),
    }  # fmt: skip
    prediction = predictions(rows, measure(tracks, refs_other, pairing, lanes, box))

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
    }
    now = datetime.now(UTC).replace(microsecond=0)
    stamp = f"{now:%Y%m%dT%H%M%SZ}"
    if exploring:
        root = args.out_root or REPO / "experiments/diagnostics"
        out = root / f"camera-gate-v3-procedure-on-{source['gate_id']}-{stamp}"
    else:
        out = (args.out_root or REPO / "experiments/runs") / f"ngsim-cam2-world-model-v3-{stamp}"
    out.mkdir(parents=True)
    layer = None
    if result.outcome == "go" and not exploring:
        sam = json.loads(args.sam_record.read_text("utf-8"))
        assessment = pairs["calibration_assessment"]
        presence = Counter(lane for t in tracks for lane in {p.lane_id for p in t.points} if lane)
        layer = CameraLayer(
            label="camera-derived: SAM 3.1 boxes of camera 2 through its calibration "
            "(approximated; the calibration is not accepted for along-road distances, ADR-018)",
            camera_clip_id=clip.clip_id, camera_clip_sha256=clip.output_sha256,
            sam_model=sam["requests"][0]["model"],
            sam_request_keys=tuple(r["request_key"] for r in sam["requests"]),
            tracks_sha256=builder.sha256_file(paths["sam_tracks"]),
            calibration_spec_sha256=spec_sha256(spec), calibration_accepted=False,
            calibration_notes=(
                f"held-out errors: alternate split worst {assessment['check_max_m']:.3f} m, "
                f"swapped split worst {assessment['swapped_split']['check_max_m']:.3f} m; lane "
                f"lines within {assessment['lane_line_check']['max_abs_m']:.2f} m",
                f"lateral placement across each vehicle's heading checked against NGSIM by the "
                f"pre-registered gate {gate['gate_id']}: {result.outcome}",
            ),
            frame=f"homography to ADR-017 grid metres, then rotation by "
            f"{scenario['frame']['bearing_deg']} deg into the world model's junction frame",
            time_sync=gate["time_sync"]["statement"],
            window_s=(offset, round(offset + clip.output_stream.duration_s, 6)),
            tracks=tuple(tracks), counts=counts, lane_presence=dict(sorted(presence.items())),
            derived=("lane", "presence", "speed (box centre, 0.5 s half window)"),
            not_derived=("stop-line crossings and turning movements", "queue lengths",
                         "counts of vehicles SAM did not box (recall 0.28, A3)"),
            gate=result,
            provenance=Provenance(
                kind="approximated", produced_by="cairoflow_perception.camera_gate_v3",
                produced_at_utc=now,
                input_sha256=tuple(builder.sha256_file(p) for p in paths.values()),
                parameters={"gate_id": gate["gate_id"], "box_point": "centre",
                            "sigma_px": 2.0, "check_median_m": round(check_median, 4),
                            "source_second_at_0830": registered},
                software={"cairoflow": metadata.version("cairoflow")},
            ),
        )  # fmt: skip
        builder.write_new(out / "camera_layer.json", layer.model_dump_json(indent=2) + "\n")

    def tidy(value):
        if isinstance(value, float):
            return round(value, 4)
        return [tidy(v) for v in value] if isinstance(value, list) else value

    rounded = [{k: tidy(v) for k, v in r.items()} for r in rows]
    builder.write_new(out / "points.json", json.dumps(rounded, indent=1) + "\n")
    comparison = {
        "script": "scripts/build_camera_world_model_v3.py",
        "created_at_utc": now.isoformat(),
        "mode": "exploration" if exploring else "gate",
        "label": (
            f"exploration: gate v3's procedure on the data {source['gate_id']} already judged; "
            "not a gate result, and not evidence for or against v3's explanation"
            if exploring else
            "camera tracks (SAM 3.1 output) placed by our procedure and compared with published "
            "NGSIM trajectories; not ground truth on either side"
        ),
        "gate_file": {"path": str(gate_path.relative_to(REPO)), "sha256": gate_sha},
        "inputs_of": source["gate_id"],
        "inputs": {name: builder.sha256_file(path) for name, path in paths.items()},
        "time_sync": sync,
        "calibration": {"spec_sha256": spec_sha256(spec),
                        "fit_points": len(calibration.fit_point_ids),
                        "check_median_m": round(check_median, 4),
                        "accepted_under_adr_018": False},
        "window_s": [offset, round(offset + clip.output_stream.duration_s, 6)],
        "counts": counts,
        "metrics": metrics,
        ("procedure_outcome_not_a_gate_result" if exploring else "gate"):
            result.model_dump(mode="json"),
        "reported_not_gated": {"sensitivity": sensitivity, "predictions": prediction},
        "pairings": {t: {"vehicle": p.vehicle_id, "cost": p.cost, "frames": len(p.frames),
                         "candidate_frames": p.candidate_frames}
                     for t, p in sorted(pairing.items())},
        "camera_layer_sha256": builder.sha256_file(out / "camera_layer.json") if layer else None,
    }  # fmt: skip
    text = json.dumps(comparison, indent=2) + "\n"
    builder.write_new(out / "comparison.json", text)
    attribution = json.loads(LICENCE.read_text(encoding="utf-8"))["attribution"]
    if exploring:
        builder.write_new(out / "NOTICE.md", (
            f"# Gate v3's procedure on {source['gate_id']}'s data: exploration (CC BY-SA 4.0)\n\n"
            "`points.json` and `comparison.json` derive from SAM 3.1 boxes on NGSIM Lankershim "
            "camera 2 footage (source seconds "
            f"{clip.trim.start_s:g}-{clip.trim.start_s + clip.output_stream.duration_s:g}), "
            f"{attribution} and from the NGSIM trajectories (CC BY-SA 3.0; see the repository "
            "NOTICE). Shared under CC BY-SA 4.0, as the overlay (decision D3). Not a gate "
            "result; model output and our measurements, not ground truth.\n"
        ))  # fmt: skip
    if args.record:
        records = REPO / "experiments/records"
        builder.write_new(records / f"{out.name}.json", text)
        folder = records / out.name
        folder.mkdir()
        shutil.copyfile(paths["sam_tracks"], folder / "tracks.json")
        shutil.copyfile(paths["sam_observations"], folder / "observations.json")
        shutil.copyfile(paths["camera_clip_manifest"], folder / "source_clip.json")
        shutil.copyfile(out / "points.json", folder / "points.json")
        if layer:
            shutil.copyfile(out / "camera_layer.json", folder / "camera_layer.json")
        builder.write_new(folder / "NOTICE.md", (
            "# Camera 2 tracks checked by camera gate v3 (CC BY-SA 4.0)\n\n"
            "`tracks.json` holds SAM 3.1 boxes on NGSIM Lankershim camera 2 footage (source "
            f"seconds {clip.trim.start_s:g}-{clip.trim.start_s + clip.output_stream.duration_s:g}"
            "), turned into tracks by CairoFlow's procedure, with the observations and the "
            "excerpt's manifest; `points.json` holds each measured point; `camera_layer.json` "
            "(on go) places the tracks in the world model's frame and "
            f"lanes. All derive from the camera 2 video, {attribution} The points also derive "
            "from the NGSIM trajectories (CC BY-SA 3.0; see the repository NOTICE). Shared under "
            "CC BY-SA 4.0, as the overlay (decision D3). Model output and our measurements, not "
            "ground truth.\n"
        ))  # fmt: skip
    verdict = "procedure outcome (exploration, not a gate result)" if exploring else "gate"
    print(f"wrote {out}: {verdict} {result.outcome}")
    for name, ok in result.checks.items():
        print(f"  [{'pass' if ok else 'FAIL'}] {name}: {metrics[name]}")
    for reason in result.reasons:
        print(f"  no-go: {reason}")
    print(f"  predictions: 1 {prediction['1_holds']}, 2 {prediction['2_holds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
