"""A stand-in for scripts/cv_run.py, for tests only: it follows the worker protocol of
cairoflow_api.product.jobs and does no vision. FAKE_WORKER_MODE picks how it behaves."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", type=Path, required=True)
    job_dir = parser.parse_args().job_dir
    job = json.loads((job_dir / "job.json").read_text("utf-8"))
    mode = os.environ.get("FAKE_WORKER_MODE", "success")

    def report(**fields: object) -> None:
        with (job_dir / "progress.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(fields) + "\n")

    report(stage="decode", frame=0, frames_total=3, fps=10.0, detections=0, tracks=0, elapsed_s=0.0)
    if mode == "die":
        os._exit(3)
    if mode == "fail":
        report(stage="failed", error="the decoder gave up")
        return 2
    if mode == "ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if mode in ("hang", "ignore-term"):
        report(stage="waiting", message="holding")
        time.sleep(120)
        return 0
    for frame in (1, 2, 3):
        report(stage="track", frame=frame, frames_total=3, fps=10.0, detections=frame, tracks=1)
    with (job_dir / "progress.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("this line is not JSON\n")
    temporary = job_dir / ".preview.tmp"
    temporary.write_bytes(b"\xff\xd8\xff\xd9")
    os.replace(temporary, job_dir / "preview.jpg")
    if mode == "done-missing":
        report(stage="done", analysis_id="never-written")
        return 0
    template = Path(os.environ["FAKE_WORKER_TEMPLATE"])
    target = Path(job["analyses_dir"]) / template.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, target)
    if mode == "done-corrupt":
        (target / "trajectories.bin").write_bytes(b"not the bytes the record hashed")
    report(stage="done", analysis_id=template.name)
    return 1 if mode == "done-nonzero" else 0


if __name__ == "__main__":
    sys.exit(main())
