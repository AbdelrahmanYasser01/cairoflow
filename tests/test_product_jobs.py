"""Jobs: a real subprocess per job, driven through the API with the fake worker under tests/."""

from __future__ import annotations

import errno
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_product_support import (
    FAKE_WORKER,
    REPO,
    client_for,
    make_repo,
    make_settings,
    make_video,
    parse_sse,
    wait_for_job,
    write_test_analysis,
)

from cairoflow_api.product import jobs as jobs_module
from cairoflow_api.product.index import ProductIndex
from cairoflow_api.product.jobs import INTERRUPTED, ProgressReader, last_progress, parse_line

ANALYSIS_ID = "fake-live-analysis"
# Finishes its work, then keeps running: the moment a stop arrives after the analysis exists.
LATE_WORKER = """
import json, os, shutil, sys, time
from pathlib import Path

job_dir = Path(sys.argv[2])
job = json.loads((job_dir / "job.json").read_text("utf-8"))
template = Path(os.environ["FAKE_WORKER_TEMPLATE"])
analysis_id = os.environ.get("LATE_WORKER_ID")
if analysis_id is None:
    analysis_id = template.name
    shutil.copytree(template, Path(job["analyses_dir"]) / analysis_id)
with (job_dir / "progress.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"stage": "done", "analysis_id": analysis_id}) + "\\n")
time.sleep(120)
"""


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_video(tmp_path_factory.mktemp("clip") / "clip.mp4")


@pytest.fixture
def repo(tmp_path: Path, clip: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_repo(tmp_path / "repo")
    target = root / "data/processed/clips/sample"
    target.mkdir(parents=True)
    (target / "clip.mp4").write_bytes(clip.read_bytes())
    template = write_test_analysis(tmp_path / "template", ANALYSIS_ID)
    monkeypatch.setenv("FAKE_WORKER_TEMPLATE", str(template))
    return root


@pytest.fixture
def client(repo: Path):
    with client_for(make_settings(repo)) as test_client:
        yield test_client


def post_job(client, **overrides: object):
    body = {"source_id": "clip-sample", "start_s": 0, "duration_s": 1.0, "frame_stride": 2}
    return client.post("/api/v1/jobs", json={**body, **overrides})


def start_job(client, **overrides: object) -> dict:
    response = post_job(client, **overrides)
    assert response.status_code == 202, response.text
    return response.json()


def worker_pid(repo: Path, job_id: str) -> int:
    path = repo / "var/jobs" / job_id / "worker.pid"
    deadline = time.monotonic() + 10
    while not path.is_file() or not path.read_text().strip():
        assert time.monotonic() < deadline, "the worker did not start"
        time.sleep(0.01)
    return int(path.read_text())


def alive(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def test_a_job_succeeds_and_its_analysis_appears(client, repo: Path) -> None:
    job = start_job(client)
    assert job["status"] == "queued" or job["status"] == "running"
    assert job["progress"] is None or job["progress"]["stage"]
    done = wait_for_job(client, job["id"], ("succeeded", "failed"))
    assert done["status"] == "succeeded", done
    assert done["analysis_id"] == ANALYSIS_ID and done["error"] is None
    assert done["started_at"] and done["finished_at"]
    assert done["progress"]["stage"] == "done" and done["has_preview"] is True
    listed = {a["id"]: a for a in client.get("/api/v1/analyses").json()["analyses"]}
    assert listed[ANALYSIS_ID]["valid"] is True
    assert client.get(f"/api/v1/analyses/{ANALYSIS_ID}").status_code == 200
    preview = client.get(f"/api/v1/jobs/{job['id']}/preview.jpg")
    assert preview.status_code == 200 and preview.content.startswith(b"\xff\xd8")
    assert preview.headers["content-type"] == "image/jpeg"
    assert [j["id"] for j in client.get("/api/v1/jobs").json()["jobs"]] == [job["id"]]


def test_the_worker_gets_the_protocol_job_file(client, repo: Path) -> None:
    job = start_job(client, start_s=0.5, duration_s=1.0, frame_stride=3)
    wait_for_job(client, job["id"], ("succeeded",))
    spec = json.loads((repo / "var/jobs" / job["id"] / "job.json").read_text("utf-8"))
    assert spec["protocol"] == "cairoflow-job/1" and spec["job_id"] == job["id"]
    assert spec["start_s"] == 0.5 and spec["duration_s"] == 1.0 and spec["frame_stride"] == 3
    assert spec["source_id"] == "clip-sample"
    assert spec["video_path"] == str(repo / "data/processed/clips/sample/clip.mp4")
    assert spec["analyses_dir"] == str(repo / "data/analyses")
    assert spec["calibrated"] is False


def test_the_real_worker_accepts_the_job_file_the_api_writes(client, repo: Path) -> None:
    """The fake worker reads job.json the way these tests expect; this reads it the way the
    delivered worker does, so the two sides of the protocol cannot drift apart unseen."""
    worker = REPO / "scripts/cv_run.py"
    if not worker.is_file():
        pytest.skip("scripts/cv_run.py is not here")
    spec = importlib.util.spec_from_file_location("cv_run_under_test", worker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    job = start_job(client)
    wait_for_job(client, job["id"], ("succeeded",))
    read = module.read_job(repo / "var/jobs" / job["id"])
    assert Path(read["video_path"]).is_file() and read["start_s"] == 0


def test_a_job_with_no_preview_yet_answers_404(client, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "fail")
    job = start_job(client)
    wait_for_job(client, job["id"], ("failed",))
    assert client.get(f"/api/v1/jobs/{job['id']}/preview.jpg").status_code == 404


def test_a_reported_failure_keeps_the_workers_reason(client, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "fail")
    job = start_job(client)
    failed = wait_for_job(client, job["id"], ("failed", "succeeded"))
    assert failed["status"] == "failed" and "the decoder gave up" in failed["error"]
    assert failed["analysis_id"] is None


def test_a_worker_that_dies_silently_is_a_failed_job(client, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "die")
    job = start_job(client)
    failed = wait_for_job(client, job["id"], ("failed", "succeeded"))
    assert failed["status"] == "failed"
    assert "code 3" in failed["error"] and "without reporting done or failed" in failed["error"]
    assert "worker.log" in failed["error"]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("done-missing", "does not exist"),
        ("done-corrupt", "failed verification"),
        ("done-nonzero", "exited with code 1"),
    ],
)
def test_a_done_line_is_a_claim_that_is_checked(client, monkeypatch, mode, expected) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", mode)
    job = start_job(client)
    failed = wait_for_job(client, job["id"], ("failed", "succeeded"))
    assert failed["status"] == "failed" and expected in failed["error"], failed
    assert failed["analysis_id"] is None


def test_a_running_job_can_be_cancelled_and_its_worker_stops(client, repo, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "hang")
    job = start_job(client)
    pid = worker_pid(repo, job["id"])
    wait_for_job(client, job["id"], ("running",))
    response = client.delete(f"/api/v1/jobs/{job['id']}")
    assert response.status_code == 202
    cancelled = wait_for_job(client, job["id"], ("cancelled",))
    assert cancelled["error"] is None and cancelled["analysis_id"] is None
    assert not alive(pid)
    assert client.delete(f"/api/v1/jobs/{job['id']}").status_code == 409


def test_a_worker_that_ignores_sigterm_is_killed(repo, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "ignore-term")
    with client_for(make_settings(repo, cancel_grace_s=0.3)) as client:
        job = start_job(client)
        pid = worker_pid(repo, job["id"])
        deadline = time.monotonic() + 10
        while "holding" not in (repo / "var/jobs" / job["id"] / "progress.jsonl").read_text():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        client.delete(f"/api/v1/jobs/{job['id']}")
        wait_for_job(client, job["id"], ("cancelled",))
    assert not alive(pid)


def test_one_job_runs_at_a_time_and_a_queued_one_can_be_cancelled(
    client, repo, monkeypatch
) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "hang")
    first = start_job(client)
    worker_pid(repo, first["id"])
    second, third = start_job(client), start_job(client)
    time.sleep(0.2)
    assert client.get(f"/api/v1/jobs/{second['id']}").json()["status"] == "queued"
    assert client.get(f"/api/v1/jobs/{third['id']}").json()["status"] == "queued"
    cancelled = client.delete(f"/api/v1/jobs/{second['id']}")
    assert cancelled.json()["status"] == "cancelled"
    assert not (repo / "var/jobs" / second["id"] / "worker.pid").exists()
    monkeypatch.setenv("FAKE_WORKER_MODE", "success")
    client.delete(f"/api/v1/jobs/{first['id']}")
    wait_for_job(client, first["id"], ("cancelled",))
    done = wait_for_job(client, third["id"], ("succeeded",))  # the next in line runs by itself
    assert done["analysis_id"] == ANALYSIS_ID


def test_events_stream_worker_lines_in_order_then_end(client) -> None:
    job = start_job(client)
    with client.stream(
        "GET", f"/api/v1/jobs/{job['id']}/events", headers={"Accept-Encoding": "gzip"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in response.headers
        text = response.read().decode()
    events = parse_sse(text)
    names = [name for name, _, _ in events]
    assert names[-2:] == ["status", "end"] and json.loads(events[-2][1])["status"] == "succeeded"
    progress = [json.loads(data) for name, data, _ in events if name == "progress"]
    assert [line["stage"] for line in progress] == ["decode", "track", "track", "track", "done"]
    assert [line.get("frame") for line in progress][:4] == [0, 1, 2, 3]
    ids = [int(event_id) for name, _, event_id in events if name == "progress"]
    assert ids == sorted(ids)  # the unparsable line is skipped, its number is not reused
    assert ids[-1] == 6
    finished = json.loads(events[-1][1])
    assert finished["id"] == job["id"] and finished["analysis_id"] == ANALYSIS_ID


def test_events_resume_after_the_last_id_seen(client) -> None:
    job = start_job(client)
    wait_for_job(client, job["id"], ("succeeded",))
    response = client.get(f"/api/v1/jobs/{job['id']}/events", headers={"Last-Event-ID": "3"})
    frames = [
        json.loads(data).get("frame")
        for name, data, _ in parse_sse(response.text)
        if name == "progress"
    ]
    assert frames == [3, None]  # lines 4 and 6; line 5 is not JSON


def test_events_of_a_live_job_run_status_progress_status_end(client, repo, monkeypatch) -> None:
    """The stream is opened while the worker runs and ends only when the job does."""
    monkeypatch.setenv("FAKE_WORKER_MODE", "hang")
    job = start_job(client)
    worker_pid(repo, job["id"])
    wait_for_job(client, job["id"], ("running",))
    captured: list[str] = []
    reader = threading.Thread(
        target=lambda: captured.append(client.get(f"/api/v1/jobs/{job['id']}/events").text)
    )
    reader.start()
    time.sleep(0.3)
    assert reader.is_alive()  # a running job's stream stays open
    client.delete(f"/api/v1/jobs/{job['id']}")
    reader.join(timeout=15)
    assert not reader.is_alive() and captured
    events = parse_sse(captured[0])
    assert [name for name, _, _ in events] == ["status", "progress", "progress", "status", "end"]
    assert json.loads(events[0][1]) == {"status": "running"}
    assert [json.loads(events[i][1])["stage"] for i in (1, 2)] == ["decode", "waiting"]
    assert json.loads(events[3][1]) == {"status": "cancelled"}
    assert json.loads(events[4][1])["status"] == "cancelled"


def test_post_jobs_answers_503_with_instructions_when_the_worker_is_absent(repo: Path) -> None:
    python = repo / "no-env/bin/python"
    with client_for(make_settings(repo, vision_python=python)) as client:
        response = post_job(client)
        assert client.get("/api/v1/jobs").json()["jobs"] == []
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert str(python) in detail and "CAIROFLOW_VISION_PYTHON" in detail
    assert "-m venv" in detail and "opencv-python-headless" in detail
    script = repo / "scripts/cv_run.py"
    with client_for(make_settings(repo, worker_script=script)) as client:
        response = post_job(client)
    assert response.status_code == 503
    assert str(script) in response.json()["detail"]
    assert "CAIROFLOW_VISION_WORKER" in response.json()["detail"]


def test_a_job_needs_a_known_source_and_a_window_inside_it(client) -> None:
    assert post_job(client, source_id="nope").status_code == 404
    assert post_job(client, duration_s=999).status_code == 422
    assert post_job(client, start_s=-1).status_code == 422
    assert post_job(client, duration_s=0).status_code == 422
    assert post_job(client, frame_stride=0).status_code == 422
    assert post_job(client, video_path="/etc/passwd").status_code == 422
    assert client.post("/api/v1/jobs", json={"source_id": "clip-sample"}).status_code == 422
    assert client.get("/api/v1/jobs").json()["jobs"] == []


def test_unknown_jobs_answer_404(client) -> None:
    for method, path in (
        ("GET", "/api/v1/jobs/nope"),
        ("DELETE", "/api/v1/jobs/nope"),
        ("GET", "/api/v1/jobs/nope/events"),
        ("GET", "/api/v1/jobs/nope/preview.jpg"),
        ("GET", "/api/v1/jobs/..%2F..%2Fsecret/preview.jpg"),
    ):
        assert client.request(method, path).status_code == 404, path


# --- restarts ---------------------------------------------------------------------------------


def seed_job(repo: Path, job_id: str, *, status: str) -> None:
    """A job row as a crashed server left it."""
    settings = make_settings(repo)
    index = ProductIndex(settings.db_path, (), repo)
    settings.var_dir.mkdir(exist_ok=True)
    index.open()
    directory = settings.jobs_dir / job_id
    directory.mkdir(parents=True)
    job = {
        "job_id": job_id, "source_id": "clip-sample", "start_s": 0.0, "duration_s": 1.0,
        "frame_stride": 1, "status": status, "created_at_utc": "2026-11-04T00:00:00Z",
    }  # fmt: skip
    index.insert_job(job)
    (directory / "job.json").write_text(json.dumps(job))


def test_a_job_left_running_at_boot_is_interrupted_and_a_queued_one_runs(repo: Path) -> None:
    seed_job(repo, "job-crashed", status="running")
    seed_job(repo, "job-waiting", status="queued")
    with client_for(make_settings(repo)) as client:
        crashed = client.get("/api/v1/jobs/job-crashed").json()
        assert crashed["status"] == "interrupted" and crashed["error"] == INTERRUPTED
        assert crashed["finished_at"]
        wait_for_job(client, "job-waiting", ("succeeded", "failed"))


def test_a_worker_that_outlived_the_server_is_stopped_at_boot(repo: Path, monkeypatch) -> None:
    seed_job(repo, "job-orphan", status="running")
    directory = repo / "var/jobs/job-orphan"
    monkeypatch.setenv("FAKE_WORKER_MODE", "hang")
    (directory / "progress.jsonl").touch()
    orphan = subprocess.Popen(
        [sys.executable, str(FAKE_WORKER), "--job-dir", str(directory)], start_new_session=True
    )
    (directory / "worker.pid").write_text(str(orphan.pid))
    deadline = time.monotonic() + 10
    while "holding" not in (directory / "progress.jsonl").read_text():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    with client_for(make_settings(repo)) as client:
        assert client.get("/api/v1/jobs/job-orphan").json()["status"] == "interrupted"
    assert orphan.wait(timeout=10) != 0


def test_a_recycled_pid_is_left_alone_at_boot(repo: Path) -> None:
    seed_job(repo, "job-recycled", status="running")
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        (repo / "var/jobs/job-recycled/worker.pid").write_text(str(bystander.pid))
        with client_for(make_settings(repo)) as client:
            assert client.get("/api/v1/jobs/job-recycled").json()["status"] == "interrupted"
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()


def test_jobs_come_back_from_their_files_when_the_database_is_deleted(repo, monkeypatch) -> None:
    settings = make_settings(repo)
    with client_for(settings) as client:
        succeeded = start_job(client)
        wait_for_job(client, succeeded["id"], ("succeeded",))
        monkeypatch.setenv("FAKE_WORKER_MODE", "fail")
        failed = start_job(client)
        wait_for_job(client, failed["id"], ("failed",))
        before = {j["id"]: j for j in client.get("/api/v1/jobs").json()["jobs"]}
    settings.db_path.unlink()
    with client_for(settings) as client:
        after = {j["id"]: j for j in client.get("/api/v1/jobs").json()["jobs"]}
    assert after == before


def test_a_running_job_is_stopped_when_the_server_stops(repo, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "hang")
    settings = make_settings(repo)
    with client_for(settings) as client:
        job = start_job(client)
        pid = worker_pid(repo, job["id"])
    assert not alive(pid)
    result = json.loads((settings.jobs_dir / job["id"] / "result.json").read_text("utf-8"))
    assert result["status"] == "interrupted" and result["error"] == INTERRUPTED


# --- faults in the job thread -----------------------------------------------------------------


def no_space(path: Path) -> OSError:
    return OSError(errno.ENOSPC, "No space left on device", str(path))


def test_a_fault_after_the_worker_started_stops_it_and_the_queue_moves_on(
    client, repo, monkeypatch
) -> None:
    monkeypatch.setenv("FAKE_WORKER_MODE", "hang")
    real_write_text = Path.write_text
    pids: list[int] = []

    def write_text(path: Path, text: str, *args: object, **kwargs: object) -> int:
        if path.name == "worker.pid" and not pids:
            pids.append(int(text))
            raise no_space(path)
        return real_write_text(path, text, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write_text)
    job = start_job(client)
    failed = wait_for_job(client, job["id"], ("failed", "succeeded"))
    assert failed["status"] == "failed" and "No space left" in failed["error"]
    assert str(repo) not in failed["error"]  # no absolute paths in a response
    assert failed["finished_at"] and not alive(pids[0])
    monkeypatch.setenv("FAKE_WORKER_MODE", "fail")
    after = wait_for_job(client, start_job(client)["id"], ("failed", "succeeded"))
    assert "the decoder gave up" in after["error"]  # the thread is alive and ran the next job


def test_a_fault_writing_result_json_keeps_the_true_status_and_the_thread(
    client, monkeypatch
) -> None:
    real_write_json = jobs_module._write_json
    faults: list[Path] = []

    def write_json(path: Path, value: dict) -> None:
        if path.name == "result.json" and not faults:
            faults.append(path)
            raise no_space(path)
        real_write_json(path, value)

    monkeypatch.setattr(jobs_module, "_write_json", write_json)
    done = wait_for_job(client, start_job(client)["id"], ("succeeded", "failed"))
    assert done["status"] == "succeeded" and done["analysis_id"] == ANALYSIS_ID
    assert faults
    monkeypatch.setenv("FAKE_WORKER_MODE", "fail")
    after = wait_for_job(client, start_job(client)["id"], ("failed", "succeeded"))
    assert "the decoder gave up" in after["error"]


def test_a_database_fault_between_jobs_does_not_end_the_thread(client, monkeypatch) -> None:
    index = client.app.state.services.index
    real_next = index.next_queued_job
    faults: list[int] = []

    def next_queued_job() -> dict | None:
        if not faults:
            faults.append(1)
            raise sqlite3.OperationalError("database is locked")
        return real_next()

    monkeypatch.setattr(index, "next_queued_job", next_queued_job)
    done = wait_for_job(client, start_job(client)["id"], ("succeeded", "failed"))
    assert done["status"] == "succeeded" and faults


def test_a_job_a_fault_left_running_is_closed_once_the_database_answers(
    client, monkeypatch
) -> None:
    """The close itself fails, and so does the first retry: the job must still end."""
    index = client.app.state.services.index
    real_move = index.move_job
    faults: list[str] = []

    def move_job(job_id: str, *, expected: str, **fields: object) -> bool:
        if expected == "running" and len(faults) < 2:
            faults.append(job_id)
            raise sqlite3.OperationalError("database is locked")
        return real_move(job_id, expected=expected, **fields)

    monkeypatch.setattr(index, "move_job", move_job)
    monkeypatch.setenv("FAKE_WORKER_MODE", "fail")
    job = start_job(client)
    failed = wait_for_job(client, job["id"], ("failed", "succeeded"))
    assert failed["status"] == "failed" and "database is locked" in failed["error"]
    assert failed["finished_at"] and faults == [job["id"], job["id"]]


# --- a stop that arrives after the work is done -----------------------------------------------


def late_settings(repo: Path, tmp_path: Path):
    script = tmp_path / "late_worker.py"
    script.write_text(LATE_WORKER, encoding="utf-8")
    return make_settings(repo, worker_script=script)


def wait_for_done_line(repo: Path, job_id: str) -> None:
    path = repo / "var/jobs" / job_id / "progress.jsonl"
    deadline = time.monotonic() + 10
    while '"done"' not in path.read_text("utf-8"):
        assert time.monotonic() < deadline, "the worker did not report done"
        time.sleep(0.01)


def test_a_cancel_after_the_worker_reported_done_keeps_job_and_list_in_agreement(
    repo: Path, tmp_path: Path
) -> None:
    settings = late_settings(repo, tmp_path)
    with client_for(settings) as client:
        job = start_job(client)
        wait_for_done_line(repo, job["id"])
        assert client.delete(f"/api/v1/jobs/{job['id']}").status_code == 202
        done = wait_for_job(client, job["id"], ("succeeded", "cancelled", "failed"))
        listed = [a["id"] for a in client.get("/api/v1/analyses").json()["analyses"]]
    assert done["status"] == "succeeded" and done["analysis_id"] == ANALYSIS_ID
    assert ANALYSIS_ID in listed
    with client_for(settings) as client:  # and after a restart, which reads the directories
        job_after = client.get(f"/api/v1/jobs/{job['id']}").json()
        analysis = client.get(f"/api/v1/analyses/{ANALYSIS_ID}")
    assert job_after["analysis_id"] == ANALYSIS_ID and analysis.status_code == 200


def test_a_done_line_is_still_checked_when_a_stop_followed_it(
    repo: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LATE_WORKER_ID", "never-written")
    with client_for(late_settings(repo, tmp_path)) as client:
        job = start_job(client)
        wait_for_done_line(repo, job["id"])
        client.delete(f"/api/v1/jobs/{job['id']}")
        failed = wait_for_job(client, job["id"], ("succeeded", "cancelled", "failed"))
    assert failed["status"] == "failed" and "does not exist" in failed["error"]
    assert failed["analysis_id"] is None


# --- the queue --------------------------------------------------------------------------------


def test_jobs_inserted_at_the_same_moment_get_distinct_queue_positions(tmp_path: Path) -> None:
    index = ProductIndex(tmp_path / "jobs.db", (), tmp_path)
    index.open()
    writers, per_writer = 8, 20
    barrier = threading.Barrier(writers)

    def insert(writer: int) -> None:
        barrier.wait()
        for number in range(per_writer):
            index.insert_job(
                {
                    "job_id": f"job-{writer}-{number}",
                    "source_id": "clip-sample",
                    "start_s": 0.0,
                    "duration_s": 1.0,
                    "frame_stride": 1,
                    "status": "queued",
                    "created_at_utc": "2026-11-04T00:00:00Z",
                }  # fmt: skip
            )

    threads = [threading.Thread(target=insert, args=(writer,)) for writer in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    positions = sorted(job["seq"] for job in index.jobs())
    assert positions == list(range(1, writers * per_writer + 1))


# --- reading progress -------------------------------------------------------------------------


def test_a_half_written_line_waits_for_its_newline(tmp_path: Path) -> None:
    path = tmp_path / "progress.jsonl"
    reader = ProgressReader(path)
    assert reader.read() == []  # no file yet
    path.write_text('{"stage": "a"}\n{"stage": "b"')
    assert [line["stage"] for _, line in reader.read()] == ["a"]
    assert reader.read() == []
    with path.open("a") as handle:
        handle.write("}\n")
    assert reader.read() == [(2, {"stage": "b"})]
    assert last_progress(path) == {"stage": "b"}


@pytest.mark.parametrize(
    "text",
    ["not json", "[1, 2]", '{"no": "stage"}', '{"stage": 3}', '{"stage": "x", "fps": NaN}'],
)
def test_a_line_outside_the_protocol_is_ignored(text: str) -> None:
    assert parse_line(text) is None


def test_no_progress_is_reported_before_the_worker_writes_any(tmp_path: Path) -> None:
    assert last_progress(tmp_path / "missing.jsonl") is None
    empty = tmp_path / "empty.jsonl"
    empty.touch()
    assert last_progress(empty) is None
    assert os.path.getsize(empty) == 0
