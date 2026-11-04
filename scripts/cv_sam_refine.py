"""Refine the hero analysis with detector-seeded SAM 3.1 into a new analysis, and measure it.

    .venv/bin/python scripts/cv_sam_refine.py --dry-run             # the plan and cost; no request
    .venv/bin/python scripts/cv_sam_refine.py --send --limit 1      # one paid request, to look at
    .venv/bin/python scripts/cv_sam_refine.py --send                # the rest, cache-first
    .venv/bin/python scripts/cv_sam_refine.py --replay --no-write   # cache only; no connection
    .venv/bin/python scripts/cv_sam_refine.py --replay              # writes data/analyses/<id>
    .venv/bin/python scripts/cv_sam_refine.py --measure             # before and after, dev frames
    .venv/bin/python scripts/cv_sam_refine.py --replay --draw 971.5 985   # masks, to scratch

Paid requests stop at --budget (this run's cap) and at the project cap in scripts/meta_spend.py,
whichever comes first; every paid request is in that ledger before its result is used. A request
that failed is never sent again unless --retry-failed says so. Clips and drawings stay in the
scratch directory: they carry the source's pixels. The rules are in local/sam_refine.py.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
for package in ("perception", "contracts", "evaluation"):
    sys.path.insert(0, str(REPO / "packages" / package))
import meta_spend  # noqa: E402

from cairoflow_contracts.analysis_record import ArtifactRef  # noqa: E402
from cairoflow_contracts.config import load_settings  # noqa: E402
from cairoflow_contracts.evidence import ClipLicense, SourceClip, TrimSpec  # noqa: E402
from cairoflow_evaluation import cv_agreement as A  # noqa: E402
from cairoflow_perception.analysis_store import (  # noqa: E402
    POINT_INTERPOLATED,
    POINT_SAM_REFINED,
    decode_trajectories,
    load_record,
    load_tracks,
    write_analysis,
)
from cairoflow_perception.ingest import MANIFEST_FILE, ingest_clip, load_source_clip  # noqa: E402
from cairoflow_perception.local import analytics, render  # noqa: E402
from cairoflow_perception.local import sam_refine as R  # noqa: E402
from cairoflow_perception.local import scoring as S  # noqa: E402
from cairoflow_perception.sam_adapter import (  # noqa: E402
    SamAdapterError,
    SamCacheMiss,
    SamClient,
    SamRequestError,
    SamSpendRefused,
    request_key,
    video_cost_usd,
)
from cairoflow_perception.sam_parse import SamStreamError, mask_records  # noqa: E402
from cairoflow_perception.sam_seeded import (  # noqa: E402
    seeded_metadata,
    seeded_prompt,
    segment_seeded,
)
from cairoflow_perception.site_int2 import load_site  # noqa: E402

SOURCE = REPO / S.VIDEO
LICENCE = REPO / "data/licences/ngsim-lankershim-videos.json"
PARENT = REPO / "data/analyses/cv-hero-dev"
CACHE = REPO / "cache/sam"
RECORDS = REPO / "experiments/records"
SCRATCH = Path(os.environ.get("CAIROFLOW_CV_SCRATCH", "~/scratch-cv")).expanduser() / "samref"
KIND = "sam-refine"
W3_S = (970.0, 1000.0)
STOP_THE_RUN = {"auth", "billing", "rate_limited"}
LONG_FROM_M = 7.5  # the reference's own 'heavy' line, ngsim_junction.heavy_length_m


def task_spent_usd() -> float:
    """What this task has spent so far, across runs, from the shared ledger."""
    if not meta_spend.LEDGER.is_file():
        return 0.0
    lines = meta_spend.LEDGER.read_text("utf-8").splitlines()
    entries = (json.loads(line) for line in lines if line.strip())
    return round(sum(e["usd"] for e in entries if e["kind"] == KIND), 6)


def parent_state() -> tuple[Any, Any, Any, list[R.TrackRows]]:
    record = load_record(PARENT)
    index = load_tracks(PARENT)
    _, columns = decode_trajectories((PARENT / "trajectories.bin").read_bytes())
    return record, index, columns, R.rows_of(index, columns)


def priority_frames(record: Any) -> range:
    first = round((W3_S[0] - record.source_start_s) * record.fps)
    last = round((W3_S[1] - record.source_start_s) * record.fps)
    return range(max(first, 0), min(last, record.frame_count))


def build_plan(record: Any, tracks: list[R.TrackRows], budget: float) -> R.RefinePlan:
    return R.plan_refinement(
        tracks,
        source_start_s=record.source_start_s,
        fps=record.fps,
        frame_count=record.frame_count,
        valid_below_row_px=record.calibration.valid_below_row_px,
        width_px=record.width_px,
        height_px=record.height_px,
        budget_usd=budget,
        priority=priority_frames(record),
        params=R.RefineParams(),
    )


def print_plan(plan: R.RefinePlan, record: Any, tracks: list[R.TrackRows]) -> None:
    w3 = priority_frames(record)
    print("cohort  trim_s  frames  objects  fallback  seeds  rows_in_w3  est_usd")
    for c in plan.cohorts:
        seeds = [s.seed_frame for s in c.segments]
        print(
            f"{c.index:>6}  {c.trim_start_s:>6.0f}  {c.frames:>6}  {len(c.segments):>7}  "
            f"{sum(s.fallback for s in c.segments):>8}  {min(seeds)}-{max(seeds)}  "
            f"{R.rows_in(c, w3):>9}  {c.cost_usd:.4f}"
        )
    reached = {s.track_id for c in plan.cohorts for s in c.segments}
    print(
        f"total: {len(plan.cohorts)} requests, {plan.frames} frames billed, "
        f"${plan.cost_usd:.4f} estimated; {len(reached)} of {len(tracks)} tracks reached"
    )
    if plan.unseeded:
        print(f"parts with no usable seed: {len(plan.unseeded)}: {plan.unseeded[:8]}")
    if plan.dropped:
        tracks_out = sorted({s.track_id for c in plan.dropped for s in c.segments} - reached)
        print(f"dropped to stay in budget: {len(plan.dropped)} requests; tracks lost {tracks_out}")
    print(
        f"ledger: project spent ${meta_spend.spent_usd():.4f}, "
        f"remaining ${meta_spend.remaining_usd():.4f}; this task spent ${task_spent_usd():.4f}"
    )


def clip_for(cohort: R.Cohort) -> tuple[SourceClip, Path]:
    """Cut (or find) a cohort's clip in the scratch directory. Ingestion is byte-reproducible."""
    directory = SCRATCH / f"clips/t{cohort.trim_start_s:.0f}-d{cohort.duration_s:.0f}"
    manifest = directory / MANIFEST_FILE
    if manifest.is_file():
        clip = load_source_clip(manifest)
    else:
        licence = ClipLicense.model_validate_json(LICENCE.read_text("utf-8"))
        trim = TrimSpec(start_s=cohort.trim_start_s, duration_s=cohort.duration_s)
        clip = ingest_clip(SOURCE, licence, directory, trim=trim)
    if clip.output_stream.frame_count != cohort.frames:
        raise R.RefineError(
            f"clip {directory.name} has {clip.output_stream.frame_count} frames, "
            f"the plan says {cohort.frames}"
        )
    return clip, directory / clip.output_file


def check_frame_map(cohort: R.Cohort, clip_path: Path) -> dict[str, Any]:
    """On pixels: clip frame j must be the AVI frame the lag rule names, not a neighbour of it.
    The AVI is read through `FrameSampler`, which refuses a held-out image."""
    import cv2  # noqa: PLC0415

    capture = cv2.VideoCapture(str(clip_path))
    picks = [cohort.frames * q // 6 for q in range(1, 6)]
    picks = [j for j in picks if 0 < j < cohort.frames - 1]
    images = {}
    index = 0
    while index <= max(picks):
        ok, image = capture.read()
        if not ok:
            raise R.RefineError(f"cannot read frame {index} of {clip_path}")
        if index in picks:
            images[index] = image.astype(np.int16)
        index += 1
    capture.release()
    totals = dict.fromkeys((-1, 0, 1), 0.0)
    with S.FrameSampler(SOURCE) as sampler:
        for j, image in images.items():
            for offset in totals:
                source_s = round(cohort.trim_start_s + (j + offset) / 10, 1)
                avi = sampler.read(source_s).astype(np.int16)
                totals[offset] += float(np.abs(image - avi).mean())
    offset = R.frame_map_offset(lambda o: totals[o])
    result = {
        "trim_start_s": cohort.trim_start_s,
        "frames_compared": len(images),
        "mean_abs_difference_by_offset": {
            str(k): round(v / len(images), 3) for k, v in totals.items()
        },
        "measured_offset": offset,
    }
    if offset != 0:
        raise R.RefineError(f"clip at {cohort.trim_start_s:g} s is off by {offset}: {result}")
    return result


def failed_before(key: str) -> bool:
    return any((CACHE / "failed").glob(f"{key}_*.json"))


def fetch(
    plan: R.RefinePlan,
    record: Any,
    settings: Any,
    *,
    mode: str,
    budget: float,
    limit: int | None,
    retry_failed: bool,
) -> tuple[list[R.RequestOutcome], list[R.Followed], R.MaskIndex, list[dict]]:
    """Send or replay each planned request, in priority order, with no blind retries."""
    outcomes: list[R.RequestOutcome] = []
    followed: list[R.Followed] = []
    masks: dict = {}
    frame_checks: list[dict] = []
    stopped: str | None = None
    sent = 0
    for cohort in plan.cohorts:
        track_ids = tuple(sorted({s.track_id for s in cohort.segments}))

        def outcome(
            status: str,
            reason: str,
            cost: float = 0.0,
            key: str | None = None,
            cohort: R.Cohort = cohort,
            tracks: tuple[int, ...] = track_ids,
        ) -> None:
            outcomes.append(
                R.RequestOutcome(cohort, status, reason, key, cost_usd=cost, tracks=tracks)
            )

        if stopped:
            outcome("not_sent", stopped)
            continue
        clip, clip_path = clip_for(cohort)
        seeds = cohort.seeds(record.source_start_s, record.fps)
        prompt = seeded_prompt(seeds, clip.output_stream.width_px, clip.output_stream.height_px)
        key = request_key(settings.sam_model, prompt, clip.output_sha256, seeded_metadata())
        cached = (CACHE / f"{key}.json").is_file()
        if mode == "live" and not cached:
            if failed_before(key) and not retry_failed:
                outcome("failed", "failed on an earlier run; not retried", key=key)
                continue
            if limit is not None and sent >= limit:
                outcome("not_sent", f"--limit {limit}", key=key)
                continue
            cost = video_cost_usd(clip.output_stream.frame_count)
            left = min(budget - task_spent_usd(), meta_spend.remaining_usd())
            if cost > left + 1e-9:
                stopped = f"${cost:.4f} would pass the cap; ${left:.4f} left"
                outcome("not_sent", stopped, key=key)
                continue
            frame_checks.append(check_frame_map(cohort, clip_path))
        client = SamClient(
            settings, CACHE, "live" if mode == "live" and not cached else "replay",
            spend_limit_usd=video_cost_usd(clip.output_stream.frame_count) if not cached else 0.0,
        )  # fmt: skip
        try:
            sam_record, parsed, from_cache = segment_seeded(client, clip, clip_path, seeds)
        except SamCacheMiss as error:
            outcome("not_sent", str(error), key=key)
            continue
        except SamSpendRefused as error:
            stopped = str(error)
            outcome("not_sent", stopped, key=key)
            continue
        except SamRequestError as error:
            spent = client.spent_usd
            if spent:
                meta_spend.record(KIND, spent, key, f"failed: {error.error_class}")
            outcome("failed", f"{error.error_class}: {error}", spent, key)
            print(f"cohort {cohort.index} failed: {error.error_class} {error}", file=sys.stderr)
            if error.error_class in STOP_THE_RUN:
                stopped = f"stopped after {error.error_class}"
            continue
        except httpx.HTTPError as error:
            # The connection broke with the outcome unknown: the ledger assumes it was billed.
            spent = video_cost_usd(clip.output_stream.frame_count)
            meta_spend.record(KIND, spent, key, f"unknown outcome: {type(error).__name__}")
            outcome("failed", f"connection: {type(error).__name__}", spent, key)
            continue
        except (SamStreamError, SamAdapterError) as error:
            if client.spent_usd:
                meta_spend.record(KIND, client.spent_usd, key, "stream did not parse")
            outcome("failed", f"unusable reply: {error}", client.spent_usd, key)
            continue
        if not from_cache:
            sent += 1
            meta_spend.record(KIND, client.spent_usd, sam_record.request_key,
                              f"cohort {cohort.index}, {len(seeds)} box seeds")  # fmt: skip
        parts = R.sam_rows(
            parsed.observations, cohort, source_start_s=record.source_start_s, fps=record.fps
        )
        followed += parts
        masks.update(
            R.index_masks(
                cohort, mask_records(sam_record.lines),
                source_start_s=record.source_start_s, fps=record.fps,
            )
        )  # fmt: skip
        outcomes.append(
            R.RequestOutcome(
                cohort, "ok", None, sam_record.request_key, sam_record.frames_billed,
                sam_record.estimated_cost_usd, from_cache, sam_record.elapsed_s,
                len(parsed.observations), track_ids,
            )
        )  # fmt: skip
        print(
            f"cohort {cohort.index}: {'cache' if from_cache else 'sent'}, "
            f"{len(parsed.observations)} boxes, billed {sam_record.frames_billed}, "
            f"{sam_record.elapsed_s:.0f} s, {parsed.outcome}, diagnostics "
            f"{len(parsed.parser_diagnostics)}, dropped {len(parsed.dropped)}"
        )
    return outcomes, followed, R.MaskIndex(masks), frame_checks


def summarise_refinement(refined: list[R.RefinedTrack]) -> None:
    counts = R.total_counts(refined)
    print("counts:", dict(sorted(counts.items())))
    distances = [d for item in refined for d in item.distances_px]
    if distances:
        print("SAM to detector on observed rows, px, percentiles 50/75/90/95/99:",
              np.round(np.percentile(distances, [50, 75, 90, 95, 99]), 2).tolist())  # fmt: skip


def repo_version() -> str:
    done = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True
    )
    return done.stdout.strip() or "unknown"


def write_refined(
    args: argparse.Namespace,
    record: Any,
    index: Any,
    refined: list[R.RefinedTrack],
    outcomes: list[R.RequestOutcome],
    masks: R.MaskIndex,
    site: Any,
    sizes: dict[int, R.Footprint],
    classes: dict[int, str],
    class_note: str | None,
) -> Path:
    frame_dt_s = 1 / record.fps
    valid = record.calibration.valid_below_row_px
    size_px = (record.width_px, record.height_px)
    placed = R.place_all(
        refined, index.tracks, site, frame_dt_s=frame_dt_s, valid_below_row_px=valid,
        image_size_px=size_px,
    )  # fmt: skip
    placed = [
        replace(p, vehicle_class=classes[p.track.track_id], class_source="sam",
                class_confidence=None) if p.track.track_id in classes else p
        for p in placed
    ]  # fmt: skip
    params = R.RefineParams()
    now = datetime.now(UTC).replace(microsecond=0)
    keys = [o.request_key for o in outcomes if o.status == "ok"]
    final = R.build_record(
        record, analysis_id=args.id, placed=placed, refined=refined, outcomes=outcomes,
        params=params, created_at_utc=now, model=load_settings(REPO / ".env").sam_model,
        parser_version=metadata.version("meta-sam-parser"), repo_version=repo_version(),
        input_sha256=(record.provenance.input_sha256[0], *keys), class_note=class_note,
    )  # fmt: skip
    entries = R.track_entries(placed, refined, sizes)
    columns = analytics.columns_of(placed)
    grid = analytics.density_grid(placed, width_px=record.width_px, height_px=record.height_px)
    payload = R.outlines_payload(refined, masks, analysis_id=args.id, params=params)
    outlines = R.outlines_bytes(payload)
    print(f"outlines: {len(payload['entries'])} polygons, {len(outlines)} bytes")
    hint = (
        "uv run --frozen python scripts/render_annotated_video.py "
        f"--analysis <dir>/{args.id} --video <path to {record.source.source_file}>"
    )
    extras = [
        (ArtifactRef(role="density", path="density.json", media_type="application/json"),
         analytics.density_bytes(grid)),
        (ArtifactRef(
            role="report", path=R.OUTLINES_FILE, media_type="application/json",
            licence_note="derived geometry from SAM 3.1 masks; holds no pixels",
        ), outlines),
        (ArtifactRef(
            role="annotated_video", path=render.ANNOTATED_VIDEO, media_type="video/mp4",
            bundled=False,
            licence_note=f"carries the source's pixels: {record.source.licence_name}",
            rebuild_hint=hint,
        ), None),
    ]  # fmt: skip
    directory = args.out / args.id
    write_analysis(directory, final, entries, columns, extras)
    return directory


# ---- measuring --------------------------------------------------------------------------------


def reference_for(keys: list[int]) -> A.Reference:
    path = SCRATCH / "reference-dev.pkl"
    if path.is_file():
        with path.open("rb") as handle:
            found = pickle.load(handle)
        if sorted(found.refs) == sorted(keys):
            return found
    found = A.load_reference(REPO, keys)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        pickle.dump(found, handle)
    return found


def subset(reference: A.Reference, keys: set[int]) -> A.Reference:
    return A.Reference(
        {k: v for k, v in reference.refs.items() if k in keys},
        {k: v for k, v in reference.headed.items() if k in keys},
        reference.lanes,
        reference.box,
    )


def run_with_flags(run: A.CameraRun, flags: np.ndarray) -> A.CameraRun:
    return A.CameraRun(run.record, run.index, replace(run.columns, point_flags=flags))


def harness_detections(
    run: A.CameraRun, frames: dict, ground: S.GroundMap
) -> dict[int, list[S.Det]]:
    """Counted rows as the scoring harness places them: the box centre through the v2 homography.
    This is the definition behind the recorded w3 baselines. A gate's path reads the stored ground
    columns instead, from the site homography, and lands a little differently."""
    out: dict[int, list[S.Det]] = {}
    for track_id, rows in A._track_rows(run, frames).items():
        for r in rows:
            point = ground.to_junction(r.x_px, r.y_px)
            x, y = point if point is not None else (None, None)
            det = S.Det(r.key, f"{track_id}:{r.key}", str(track_id), x, y)
            out.setdefault(r.key, []).append(det)
    return out


def detection_summary(
    run: A.CameraRun, reference: A.Reference, frames: dict, ground: S.GroundMap | None = None
) -> dict[str, Any]:
    """Recall and precision proxy under the harness's definitions, and each counted row's match.
    With `ground` rows are placed as `harness_detections` says, else as a gate reads them."""
    keys = sorted(A.frame_key(run.record, f) for f in frames)
    dets = (
        harness_detections(run, frames, ground)
        if ground is not None
        else A._detections(A._track_rows(run, frames))
    )
    results = S.associate(dets, reference.refs, keys)
    summary = S.summarise(results, intervals=False)
    return {
        "recall": summary["vehicle_frame_recall"],
        "precision_proxy": summary["precision_proxy"],
        "any_exposure": summary["vehicle_recall_any_exposure"],
        "results": results,
    }


def compact(block: dict[str, Any]) -> dict[str, Any]:
    keep = ("value", "numerator", "denominator")
    return {
        name: {k: block[name][k] for k in keep}
        for name in ("recall", "precision_proxy", "any_exposure")
    }


def sam_rows_of(run: A.CameraRun, frames: dict) -> set[tuple[int, int]]:
    """(track id, frame key) of every row whose box is SAM's."""
    out = set()
    flags = run.columns.point_flags
    for track in run.index.tracks:
        for i in range(track.n):
            frame = track.start_frame + i
            if frame in frames and flags[track.first_row + i] & POINT_SAM_REFINED:
                out.add((track.track_id, A.frame_key(run.record, frame)))
    return out


def row_quality(summary: dict[str, Any], rows: set[tuple[int, int]]) -> dict[str, Any]:
    """How the rows named in `rows` fared against the reference."""
    matched = {}
    for result in summary["results"].values():
        for m in result.matches:
            matched[(int(m.group), m.frame)] = m
    hits = [matched[row] for row in rows if row in matched]
    return {
        "rows": len(rows),
        "matched": len(hits),
        "matched_share": round(len(hits) / len(rows), 4) if rows else None,
        "median_abs_across_m": round(statistics.median(abs(m.across_m) for m in hits), 3)
        if hits else None,
        "median_abs_along_m": round(statistics.median(abs(m.along_m) for m in hits), 3)
        if hits else None,
    }  # fmt: skip


def added_by_track(
    hero: A.CameraRun,
    refined: A.CameraRun,
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[dict[str, Any]]:
    """Each extended track's added rows against the reference vehicle its detector rows matched. An
    unmatched row is not a false one: a track no detector row matched has no reference vehicle."""
    owners: dict[int, Counter] = {}
    for result in before["results"].values():
        for m in result.matches:
            owners.setdefault(int(m.group), Counter())[m.vehicle_id] += 1
    matched = {
        (int(m.group), m.frame): m.vehicle_id
        for result in after["results"].values()
        for m in result.matches
    }
    lengths = {t.track_id: t.n for t in hero.index.tracks}
    out = []
    for track in refined.index.tracks:
        keys = [
            A.frame_key(refined.record, track.start_frame + i)
            for i in range(lengths[track.track_id], track.n)
        ]
        if not keys:
            continue
        found = owners.get(track.track_id)
        own = found.most_common(1)[0][0] if found else None
        hits = [matched.get((track.track_id, key)) for key in keys]
        out.append(
            {
                "track_id": track.track_id,
                "added_rows": len(keys),
                "reference_vehicle": own,
                "matched_own_vehicle": sum(hit is not None and hit == own for hit in hits),
                "matched_another_vehicle": sum(hit is not None and hit != own for hit in hits),
                "unmatched": hits.count(None),
            }
        )
    return sorted(out, key=lambda row: -row["added_rows"])


def counts_of(compared: dict[str, Any]) -> dict[str, Any]:
    metrics = compared["metrics"]
    return {
        "entering_by_arm": compared["entering_by_arm"],
        "mid_view_by_arm": compared["mid_view_by_arm"],
        "entering_count_error_share": metrics["entering_count_error_share"],
        "turning_matrix_agreement": metrics["turning_matrix_agreement"],
        "vehicle_recall": metrics["vehicle_recall"],
        "precision_proxy": metrics["precision_proxy"],
        "tracks": compared["tracks"],
        "frames_scored": compared["frames_scored"],
    }


def row_shares(run: A.CameraRun) -> dict[str, Any]:
    flags = run.columns.point_flags
    rows = len(flags)
    return {
        "rows": rows,
        "interpolated": int((flags & POINT_INTERPOLATED != 0).sum()),
        "interpolated_share": round(float((flags & POINT_INTERPOLATED != 0).mean()), 4),
        "sam_refined": int((flags & POINT_SAM_REFINED != 0).sum()),
    }


def measure(args: argparse.Namespace) -> int:
    hero = A.load_run(PARENT)
    refined = A.load_run(args.out / args.id)
    frames = A.split_frames(hero.record, "dev")
    keys = sorted(A.frame_key(hero.record, f) for f in frames)
    reference = reference_for(keys)
    w3 = {
        f: b for f, b in frames.items()
        if W3_S[0] <= hero.record.source_start_s + f / hero.record.fps < W3_S[1]
    }  # fmt: skip
    w3_reference = subset(reference, {A.frame_key(hero.record, f) for f in w3})

    # The control: the same rows counted as observed, at their interpolated positions, with no SAM.
    replaced_only = hero.columns.point_flags.copy()
    positions = {
        (t.track_id, t.start_frame + i): t.first_row + i
        for t in hero.index.tracks for i in range(t.n)
    }  # fmt: skip
    replaced = []
    for t in refined.index.tracks:
        for i in range(t.n):
            frame = t.start_frame + i
            if refined.columns.point_flags[t.first_row + i] & POINT_SAM_REFINED:
                row = positions.get((t.track_id, frame))
                if row is not None:
                    replaced.append(row)
                    replaced_only[row] &= ~np.uint8(POINT_INTERPOLATED)
    control = run_with_flags(hero, replaced_only)
    all_counted = run_with_flags(hero, hero.columns.point_flags & ~np.uint8(POINT_INTERPOLATED))

    ground, _ = S.build_ground_map(REPO)
    before = detection_summary(hero, w3_reference, w3, ground)
    after = detection_summary(refined, w3_reference, w3, ground)
    same_rows = detection_summary(control, w3_reference, w3, ground)
    every_row = detection_summary(all_counted, w3_reference, w3, ground)
    gate_before = detection_summary(hero, w3_reference, w3)
    gate_after = detection_summary(refined, w3_reference, w3)
    whole_before = detection_summary(hero, reference, frames, ground)
    whole_after = detection_summary(refined, reference, frames, ground)
    whole_control = detection_summary(control, reference, frames, ground)
    sam_all = sam_rows_of(refined, frames)
    all_hero = {(tid, A.frame_key(hero.record, f)) for tid, f in positions}
    sam_w3 = sam_rows_of(refined, w3)
    in_hero = {(t.track_id, t.start_frame + i) for t in hero.index.tracks for i in range(t.n)}
    in_w3 = {(tid, A.frame_key(hero.record, f)) for tid, f in in_hero if f in w3}
    sam_replaced = sam_w3 & in_w3
    sam_added = sam_w3 - in_w3

    payload = {
        "script": "scripts/cv_sam_refine.py --measure",
        "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "label": "agreement with a correlated reference on dev frames, not accuracy",
        "parent": hero.record.analysis_id,
        "refined": refined.record.analysis_id,
        "w3_source_s": list(W3_S),
        "w3": {
            "before": compact(before),
            "after": compact(after),
            "stored_ground_path_before": compact(gate_before),
            "stored_ground_path_after": compact(gate_after),
            "control_same_rows_interpolated": compact(same_rows),
            "control_all_interpolated_rows_counted": compact(every_row),
            "replaced_rows_sam_position": row_quality(after, sam_replaced),
            "replaced_rows_interpolated_position": row_quality(same_rows, sam_replaced),
            "added_rows_past_track_end": row_quality(after, sam_added),
        },
        "block_14_scoring_path": {
            "before": compact(whole_before),
            "after": compact(whole_after),
            "control_same_rows_interpolated": compact(whole_control),
            "replaced_rows_sam_position": row_quality(whole_after, sam_all & all_hero),
            "replaced_rows_interpolated_position": row_quality(whole_control, sam_all & all_hero),
            "added_rows_past_track_end": row_quality(whole_after, sam_all - all_hero),
            "added_rows_by_track": added_by_track(hero, refined, whole_before, whole_after),
        },
        "rows": {"before": row_shares(hero), "after": row_shares(refined)},
        "dev_block_14": {
            "before": counts_of(A.compare(hero, reference, frames)),
            "after": counts_of(A.compare(refined, reference, frames)),
        },
        "classes": {
            "before": dict(Counter(t.vehicle_class for t in hero.index.tracks)),
            "after": dict(Counter(t.vehicle_class for t in refined.index.tracks)),
            "after_by_source": dict(Counter(t.class_source for t in refined.index.tracks)),
        },
        "movement_cells": {
            "before": sum(m.count or 0 for m in hero.record.movements),
            "after": sum(m.count or 0 for m in refined.record.movements),
        },
        "tracks_status": {
            "before": dict(Counter(t.status for t in hero.index.tracks)),
            "after": dict(Counter(t.status for t in refined.index.tracks)),
        },
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = S.write_record(RECORDS / f"sam-refine-{stamp}", "measured.json", payload)
    print(json.dumps(payload, indent=1, default=str))
    print(f"wrote {out}")
    return 0


# ---- long vehicles ----------------------------------------------------------------------------


def paired_vehicles(run: A.CameraRun, reference: A.Reference, frames: dict) -> dict[int, int]:
    """Track id -> the reference vehicle most of its rows matched, at least ten rows of them."""
    votes: dict[int, Counter] = {}
    summary = detection_summary(run, reference, frames)
    for result in summary["results"].values():
        for m in result.matches:
            votes.setdefault(int(m.group), Counter())[m.vehicle_id] += 1
    return {
        track: found.most_common(1)[0][0]
        for track, found in votes.items()
        if found.most_common(1)[0][1] >= 10
    }


def reference_lengths(vehicle_ids: set[int]) -> dict[int, float]:
    table = S.load_table(REPO / S.TRAJECTORY_CSV)
    keep = np.isin(table.vehicle_id, np.fromiter(vehicle_ids, dtype=np.int64))
    lengths: dict[int, float] = {}
    for vid, feet in zip(table.vehicle_id[keep], table.length_ft[keep], strict=True):
        lengths[int(vid)] = float(feet) * S.US_SURVEY_FOOT_M
    return lengths


def size_evidence(
    footprints: dict[int, R.Footprint], index: Any, record: Any
) -> tuple[dict[str, Any], list[R.SizeRow]]:
    """Whether a mask's length picks out the reference's long vehicles better than the label."""
    run = A.load_run(PARENT)
    frames = A.split_frames(record, "dev")
    keys = sorted(A.frame_key(record, f) for f in frames)
    reference = reference_for(keys)
    pairs = paired_vehicles(run, reference, frames)
    lengths = reference_lengths(set(pairs.values()))
    entries = {t.track_id: t for t in index.tracks}
    rows = []
    for track_id, vehicle in pairs.items():
        entry, size = entries[track_id], footprints.get(track_id)
        rows.append(
            R.SizeRow(
                track_id, lengths[vehicle], entry.vehicle_class in ("truck", "bus"),
                size.length_m if size else None, entry.length_m,
            )
        )  # fmt: skip
    out = R.separation(rows, LONG_FROM_M)
    both = [r for r in rows if r.mask_length_m is not None]
    placed = [r for r in both if r.placed_length_m is not None]
    def error(chosen: list[R.SizeRow], name: str) -> float | None:
        values = [abs(getattr(r, name) - r.reference_length_m) for r in chosen]
        return round(statistics.median(values), 3) if values else None

    out["median_abs_length_error_m"] = {
        "mask": error(both, "mask_length_m"),
        "mask_where_placed_exists": error(placed, "mask_length_m"),
        "placed": error(placed, "placed_length_m"),
    }
    out["long_from_m"] = LONG_FROM_M
    out["long_rows"] = [
        vars(r) for r in rows
        if r.reference_length_m >= LONG_FROM_M or r.detector_long
        or (r.mask_length_m or 0) >= LONG_FROM_M
    ]  # fmt: skip
    return out, rows


# ---- looking at it ----------------------------------------------------------------------------


def draw(
    seconds: list[float],
    record: Any,
    tracks: list[R.TrackRows],
    refined: list[R.RefinedTrack],
    masks: R.MaskIndex,
) -> None:
    """Accepted masks over the frame, detector boxes in thin lines, into the scratch directory."""
    import cv2  # noqa: PLC0415

    out = SCRATCH / "frames"
    out.mkdir(parents=True, exist_ok=True)
    originals = {t.track_id: t for t in tracks}
    with S.FrameSampler(SOURCE) as sampler:
        for second in seconds:
            frame = round((second - record.source_start_s) * record.fps)
            image = sampler.read(second)
            big = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
            for item in refined:
                track = originals[item.track.track_id]
                if track.start_frame <= frame <= track.end_frame:
                    colour = (120, 120, 120)
                    if track.interpolated[track.row(frame)]:
                        colour = (0, 165, 255)
                    x1, y1, x2, y2 = (int(v * 2) for v in track.box(frame))
                    cv2.rectangle(big, (x1, y1), (x2, y2), colour, 1)
                found = item.accepted.get(frame)
                record_ = masks.of(found[0], found[1], frame) if found else None
                outline = R.mask_outline(record_, 0.5) if record_ else None
                if outline is None:
                    continue
                points = (outline[1] * 2).astype(np.int32)
                cv2.polylines(big, [points], True, (0, 255, 0), 1)
                x, y = points.min(axis=0)
                cv2.putText(big, str(item.track.track_id), (int(x), int(y) - 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)  # fmt: skip
            path = out / f"s{second:.1f}.jpg"
            cv2.imwrite(str(path), big, [cv2.IMWRITE_JPEG_QUALITY, 88])
            print(f"drew {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="plan and cost; no request")
    mode.add_argument("--send", action="store_true", help="paid requests, cache-first")
    mode.add_argument("--replay", action="store_true", help="cache only; opens no connection")
    mode.add_argument("--measure", action="store_true", help="before and after, on dev frames")
    parser.add_argument("--budget", type=float, default=1.00, help="this task's cap, USD")
    parser.add_argument("--limit", type=int, help="send at most this many new requests")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--no-write", action="store_true", help="report, write no analysis")
    parser.add_argument("--no-extension", action="store_true", help="no rows past a track's end")
    parser.add_argument("--sizes", action="store_true", help="report mask sizes and evidence")
    parser.add_argument(
        "--use-mask-sizes", action="store_true", help="write mask sizes and classes from them"
    )
    parser.add_argument("--draw", type=float, nargs="*", help="source seconds to draw")
    parser.add_argument("--id", default="cv-hero-sam")
    parser.add_argument("--out", type=Path, default=REPO / "data/analyses")
    args = parser.parse_args(argv)
    if args.measure:
        return measure(args)

    record, index, _, tracks = parent_state()
    plan = build_plan(record, tracks, args.budget)
    print_plan(plan, record, tracks)
    if args.dry_run:
        return 0

    settings = load_settings(REPO / ".env")
    outcomes, followed, masks, frame_checks = fetch(
        plan, record, settings, mode="live" if args.send else "replay", budget=args.budget,
        limit=args.limit, retry_failed=args.retry_failed,
    )  # fmt: skip
    print("requests:", dict(Counter(o.status for o in outcomes)))
    for o in outcomes:
        if o.status != "ok":
            print(f"  cohort {o.cohort.index} {o.status}: {o.reason}")
    if frame_checks:
        print("frame map checks:", json.dumps(frame_checks))
    print(f"ledger: project ${meta_spend.spent_usd():.4f}, this task ${task_spent_usd():.4f}")
    params = R.RefineParams()
    valid = record.calibration.valid_below_row_px
    refined = R.refine_all(
        tracks, followed, frame_count=record.frame_count, valid_below_row_px=valid,
        width_px=record.width_px, params=params, allow_extension=not args.no_extension,
    )  # fmt: skip
    summarise_refinement(refined)
    site = load_site(REPO)
    sizes: dict[int, R.Footprint] = {}
    classes: dict[int, str] = {}
    class_note = None
    evidence = None
    if args.sizes or args.use_mask_sizes:
        for item in refined:
            found = R.track_footprint(
                item, masks, site, width_px=record.width_px, valid_below_row_px=valid,
                params=params,
            )  # fmt: skip
            if found:
                sizes[found.track_id] = found
        evidence, rows = size_evidence(sizes, index, record)
        classes, class_note = classes_from_masks(sizes, index)
        evidence["class_agreement"] = class_agreement(rows, index, classes)
        print("size evidence:", json.dumps(evidence, indent=1))
        if not args.use_mask_sizes:
            sizes, classes, class_note = {}, {}, None
    if args.draw:
        draw(args.draw, record, tracks, refined, masks)
    if args.no_write:
        return 0
    bad = [o for o in outcomes if o.status != "ok"]
    if bad:
        print(f"note: {len(bad)} requests did not complete; the record says so", file=sys.stderr)
    directory = write_refined(
        args, record, index, refined, outcomes, masks, site, sizes, classes, class_note,
    )  # fmt: skip
    print(f"wrote {directory}")
    done = [o.cohort for o in outcomes if o.status == "ok"]
    checks = [check_frame_map(cohort, clip_for(cohort)[1]) for cohort in done]
    print(f"wrote {run_record(plan, outcomes, refined, checks, evidence)}")
    return 0


def classes_from_masks(
    sizes: dict[int, R.Footprint], index: Any
) -> tuple[dict[int, str], str | None]:
    """Where a mask's length answers the long-or-car question differently from the detector, by the
    reference's own length bands, so nothing here was tuned. Only that question: a mask under a tree
    is short, so it says nothing about motorcycles, and a track neither side calls long is left."""
    band = analytics.zones.vehicle_class
    classes = {}
    for track in index.tracks:
        size = sizes.get(track.track_id)
        if size is None or track.vehicle_class == "motorcycle":
            continue
        by_length = band(size.length_m)
        long_question = size.length_m >= LONG_FROM_M or track.vehicle_class in ("truck", "bus")
        if long_question and by_length not in (track.vehicle_class, "motorcycle"):
            classes[track.track_id] = by_length
    note = (
        f"Track lengths and widths come from SAM masks on {len(sizes)} tracks: the smallest "
        "rectangle round a mask's ground outline, median over masks the image did not cut off. "
        "They are approximated, on a flat-road homography whose along-road distances are not "
        f"accepted (ADR-018). {len(classes)} tracks take their class from that length by the "
        "reference's length bands, with class source sam; the evidence for this rests on very few "
        "long vehicles."
    )
    return classes, note


def class_agreement(
    rows: list[R.SizeRow], index: Any, classes: dict[int, str]
) -> dict[str, Any]:
    """Exact class against the reference's band for its length, before and after, same tracks."""
    band = analytics.zones.vehicle_class
    entries = {t.track_id: t for t in index.tracks}
    changed = []
    before = after = 0
    for row in rows:
        real = band(row.reference_length_m)
        was = entries[row.track_id].vehicle_class
        now = classes.get(row.track_id, was)
        before += was == real
        after += now == real
        if now != was:
            changed.append(
                {
                    "track_id": row.track_id,
                    "detector": was,
                    "sam": now,
                    "reference_band": real,
                    "reference_length_m": round(row.reference_length_m, 2),
                    "mask_length_m": row.mask_length_m,
                }
            )
    return {
        "tracks": len(rows),
        "agree_before": before,
        "agree_after": after,
        "changed": changed,
    }


def run_record(
    plan: R.RefinePlan,
    outcomes: list[R.RequestOutcome],
    refined: list[R.RefinedTrack],
    frame_checks: list[dict],
    evidence: dict[str, Any] | None,
) -> Path:
    """What was sent and what came back: numbers only, never overwritten."""
    billed = sum(o.frames_billed or 0 for o in outcomes)
    payload = {
        "script": "scripts/cv_sam_refine.py",
        "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "label": "model_generated; seeds are this analysis's own detector rows",
        "params": R.RefineParams().as_parameters(),
        "requests": [
            {
                "cohort": o.cohort.index,
                "status": o.status,
                "reason": o.reason,
                "trim_start_s": o.cohort.trim_start_s,
                "duration_s": o.cohort.duration_s,
                "objects": len(o.cohort.segments),
                "tracks": list(o.tracks),
                "request_key": o.request_key,
                "frames_billed": o.frames_billed,
                "estimated_cost_usd": o.cost_usd,
                "boxes": o.observations,
            }
            for o in outcomes
        ],
        "frames_billed": billed,
        "cost_estimated_usd": round(sum(o.cost_usd for o in outcomes), 6),
        "cost_at_billed_frames_usd": round(billed * 0.20 / 1000, 6),
        "tracks_unseeded": [list(item) for item in plan.unseeded],
        "requests_dropped_for_budget": len(plan.dropped),
        "counts": dict(sorted(R.total_counts(refined).items())),
        "frame_map_checks": frame_checks,
        "size_evidence": evidence,
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return S.write_record(RECORDS / f"sam-refine-{stamp}", "run.json", payload)


if __name__ == "__main__":
    raise SystemExit(main())
