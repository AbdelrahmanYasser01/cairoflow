"""Shared helpers of the product backend tests: a small repository in a temporary directory, so the
tests never read or write the real var/, data/uploads or data/analyses."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cairoflow_api.product.settings import ProductSettings
from cairoflow_api.serve import create_product_app
from cairoflow_contracts.analysis_record import ArtifactRef
from cairoflow_perception.analysis_store import (
    decode_trajectories,
    load_record,
    load_tracks,
    write_analysis,
)

REPO = Path(__file__).resolve().parents[1]
SAMPLE_ID = "ref-ngsim-int2-960-1020"
SAMPLE = REPO / "data/sample/analyses" / SAMPLE_ID
FAKE_WORKER = Path(__file__).with_name("fake_cv_worker.py")
COPIED = (
    "data/geometry/ngsim-lankershim-int2/geometry.json",
    "data/geometry/ngsim-lankershim-cam2/v2/image_geometry.json",
    "data/sample/corridor_summary.json",
    "data/sample/decision_summary.json",
)


def make_repo(root: Path) -> Path:
    for relative in COPIED:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, root / relative)
    (root / "data/sample/analyses").mkdir(parents=True)
    shutil.copytree(SAMPLE, root / "data/sample/analyses" / SAMPLE_ID)
    return root


def make_settings(root: Path, **overrides: object) -> ProductSettings:
    values: dict[str, object] = {
        "repo_root": root,
        "vision_python": Path(sys.executable),
        "worker_script": FAKE_WORKER,
        "job_poll_s": 0.02,
        "cancel_grace_s": 1.0,
    }
    return ProductSettings(**{**values, **overrides})


def client_for(settings: ProductSettings) -> TestClient:
    return TestClient(create_product_app(settings))


def write_test_analysis(
    directory: Path,
    analysis_id: str,
    *,
    created_at: datetime | None = None,
    extra: Sequence[tuple[ArtifactRef, bytes | None]] = (),
) -> Path:
    """A real analysis on disk, written by the store, from the sample's data."""
    record = load_record(SAMPLE)
    _, columns = decode_trajectories((SAMPLE / "trajectories.bin").read_bytes())
    updated = record.model_copy(
        update={
            "analysis_id": analysis_id,
            "created_at_utc": created_at or record.created_at_utc,
        }
    )
    target = directory / analysis_id
    write_analysis(target, updated, load_tracks(SAMPLE).tracks, columns, extra)
    return target


def make_video(path: Path, seconds: int = 2) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={seconds}:size=64x48:rate=10",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )  # fmt: skip
    return path


def wait_for_job(
    client: TestClient, job_id: str, statuses: Sequence[str], timeout_s: float = 20.0
) -> dict:
    deadline = time.monotonic() + timeout_s
    while True:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in statuses:
            return job
        if time.monotonic() > deadline:
            raise AssertionError(f"job stayed {job['status']}, wanted {statuses}: {job}")
        time.sleep(0.02)


def parse_sse(text: str) -> list[tuple[str, str, str | None]]:
    """Event name, data and id of each event in a stream."""
    events = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if not line.startswith(":")
        )
        if "event" in fields:
            events.append((fields["event"], fields["data"], fields.get("id")))
    return events
