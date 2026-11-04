"""The pipeline end to end on a tiny generated video, and the worker's protocol.

`BrightBoxDetector` is a real, if simple, detector: it looks at the pixels of a generated test video
and finds the bright squares drawn into it. It and everything it produces live under pytest's
temporary directories; nothing here writes into a repository analysis directory.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from cairoflow_contracts.evidence import TrimSpec
from cairoflow_perception.analysis_store import (
    NO_GROUND,
    NO_SPEED,
    decode_trajectories,
    load_tracks,
    verify_analysis,
)
from cairoflow_perception.ingest import (
    OUTPUT_PLACEHOLDER,
    SOURCE_PLACEHOLDER,
    sha256_file,
    transcode_command,
)
from cairoflow_perception.local import frames as F
from cairoflow_perception.local import pipeline as P
from cairoflow_perception.local import render as R
from cairoflow_perception.local.scoring import GATE_V3, VIDEO, HeldOutError
from cairoflow_perception.site_int2 import load_site

REPO = Path(__file__).resolve().parents[1]
WORKER = REPO / "scripts/cv_run.py"
ANALYSE = REPO / "scripts/cv_analyse.py"
RENDER = REPO / "scripts/render_annotated_video.py"
CAMERA2 = REPO / VIDEO
WIDTH, HEIGHT, FPS = 640, 480, 10
needs_camera2 = pytest.mark.skipif(
    not CAMERA2.is_file() or not shutil.which("ffmpeg"),
    reason="the camera 2 video is not distributed with the repository",
)


class BrightBoxDetector:
    """Connected bright regions of a frame."""

    tool = "tests.BrightBoxDetector"
    version = "1"
    device = "cpu"

    def __init__(self, delay_s: float = 0.0) -> None:
        self.delay_s = delay_s

    def detect(self, frame_bgr: np.ndarray) -> list[tuple]:
        if self.delay_s:
            time.sleep(self.delay_s)
        mask = (frame_bgr.max(axis=2) > 128).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        return [
            (float(x), float(y), float(x + w), float(y + h), 0.9, "car")
            for x, y, w, h, area in (stats[i] for i in range(1, count))
            if area >= 20
        ]


def make_detector(context) -> BrightBoxDetector:
    return BrightBoxDetector()


def make_slow_detector(context) -> BrightBoxDetector:
    return BrightBoxDetector(delay_s=0.05)


def make_broken_detector(context):
    raise RuntimeError("the weights are missing")


def write_video(path: Path, seconds: float = 3.0) -> Path:
    """Two bright squares crossing a dark frame, one down the middle, one across it."""
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), float(FPS), (WIDTH, HEIGHT)
    )
    assert writer.isOpened()
    for k in range(round(seconds * FPS)):
        image = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
        a = 20 + 5 * k
        cv2.rectangle(image, (320, a), (330, a + 20), (255, 255, 255), thickness=-1)
        b = 40 + 6 * k
        cv2.rectangle(image, (b, 100), (b + 20, 110), (255, 255, 255), thickness=-1)
        writer.write(image)
    writer.release()
    return path


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    return write_video(tmp_path_factory.mktemp("video") / "tiny.avi")


@pytest.fixture(scope="module")
def site():
    return load_site(REPO)


def analyse(video, out, analysis_id="tiny", *, site=None, **kwargs):
    kwargs.setdefault("duration_s", 3.0)
    return P.run_analysis(
        video,
        kwargs.pop("start_s", 0.0),
        kwargs.pop("duration_s"),
        kwargs.pop("frame_stride", 1),
        kwargs.pop("detector", BrightBoxDetector()),
        site,
        out,
        analysis_id,
        kwargs.pop("progress_callback", None),
        kwargs.pop("cancel_check", None),
        **kwargs,
    )


# ---- frames ----------------------------------------------------------------------------------


def test_frames_stream_by_time(video):
    got = list(F.iter_frames(video, 1.0, 1.0, 2))
    assert [k for k, _, _ in got] == [0, 1, 2, 3, 4]
    assert [s for _, s, _ in got] == [1.0, 1.2, 1.4, 1.6, 1.8]
    assert got[0][2].shape == (HEIGHT, WIDTH, 3)


def test_the_offset_moves_source_time_and_the_lag_moves_the_image(video):
    plain = list(F.iter_frames(video, 1.0, 0.3))
    lagged = list(F.iter_frames(video, 1.0, 0.3, frame_lag=1, source_offset_s=500.0))
    assert [s for _, s, _ in lagged] == [501.0, 501.1, 501.2]
    assert np.array_equal(lagged[1][2], plain[0][2])


def test_a_video_shorter_than_asked_ends_early_and_says_so(video):
    stats = F.FrameStats()
    got = list(F.iter_frames(video, 2.0, 60.0, stats=stats))
    assert len(got) == 10
    assert stats.ended_early
    assert stats.read == 10
    assert stats.requested == 600


def test_a_window_past_the_end_is_an_error(video):
    with pytest.raises(F.FrameStreamError):
        list(F.iter_frames(video, 100.0, 1.0))


def test_a_missing_file_is_an_error(tmp_path):
    with pytest.raises(F.FrameStreamError):
        F.probe_video(tmp_path / "nothing.avi")


def test_an_unreadable_frame_leaves_a_hole_and_no_shift(video, monkeypatch):
    real = cv2.VideoCapture

    class Flaky:
        def __init__(self, path):
            self._capture = real(path)
            self._next = 0

        def __getattr__(self, name):
            return getattr(self._capture, name)

        def set(self, prop, value):
            self._next = int(value)
            return self._capture.set(prop, value)

        def grab(self):
            self._next += 1
            return self._capture.grab()

        def read(self):
            index = self._next
            self._next += 1
            found = self._capture.read()
            return (False, None) if index == 4 else found

    monkeypatch.setattr(cv2, "VideoCapture", Flaky)
    stats = F.FrameStats()
    got = list(F.iter_frames(video, 0.0, 1.0, stats=stats))
    assert [k for k, _, _ in got] == [0, 1, 2, 3, 5, 6, 7, 8, 9]
    assert stats.unreadable == 1
    assert not stats.ended_early
    assert stats.missing == 1


def test_an_odd_frame_rate_header_does_not_add_a_frame():
    assert len(F.plan_frames(10.0, 960.0, 60.0)) == 600
    assert len(F.plan_frames(10.0, 960.0, 60.0, 3)) == 200


def test_an_odd_block_is_refused_before_any_pixel():
    with pytest.raises(HeldOutError):
        F.held_out_frames(F.plan_frames(10.0, 180.0, 5.0, 1, 1))
    with pytest.raises(HeldOutError):
        F.held_out_frames(F.plan_frames(10.0, 230.0, 20.0, 1, 1))  # runs into it from a dev block
    with pytest.raises(HeldOutError):
        F.held_out_frames(F.plan_frames(10.0, 120.0, 10.0, 1, 0, source_offset_s=60.0))  # a clip


def test_a_dev_block_is_allowed_but_for_its_first_image():
    plan = F.plan_frames(10.0, 960.0, 60.0, 1, 1)
    assert F.held_out_frames(plan) == {0}  # its image is the last frame of block 13
    assert F.held_out_frames(F.plan_frames(10.0, 970.0, 30.0, 1, 1)) == set()


def test_seconds_with_no_block_are_not_held_out():
    assert F.held_out_frames(F.plan_frames(10.0, 30.0, 60.0, 1, 1)) == set()
    assert F.held_out_frames(F.plan_frames(10.0, 1030.0, 60.0, 1, 1)) == set()


# ---- the pipeline ----------------------------------------------------------------------------


def test_an_uncalibrated_run_writes_a_verifiable_analysis(video, tmp_path):
    record = analyse(video, tmp_path)
    directory = tmp_path / "tiny"
    assert verify_analysis(directory) == record
    assert record.data_origin == "camera_cv"
    assert record.mode == "precomputed"
    assert record.calibration.mode == "uncalibrated"
    assert record.site_id == P.UNCALIBRATED_SITE_ID
    assert record.source.uploaded_by_user
    assert record.source.source_sha256
    assert (record.fps, record.frame_count, record.frame_stride) == (10.0, 30, 1)
    assert record.source_start_s == 0.0
    tracks = load_tracks(directory).tracks
    assert len(tracks) == 2
    assert all(t.vehicle_class == "car" and t.class_source == "detector" for t in tracks)
    assert all(t.approach is None and t.turn is None for t in tracks)
    _, columns = decode_trajectories((directory / "trajectories.bin").read_bytes())
    assert np.all(columns.gx == NO_GROUND)
    by_id = {m.metric_id: m for m in record.metrics}
    assert by_id["vehicles.total"].value == 2.0
    assert by_id["vehicles.class.car"].value == 2.0
    assert by_id["flow.entering"].status == "not_available"
    assert any("Uncalibrated" in d for d in record.disclosures)
    assert any("not ground truth" in d for d in record.disclosures)


def test_every_stage_leaves_a_receipt(video, tmp_path):
    record = analyse(video, tmp_path)
    stages = {s.stage: s for s in record.stages}
    assert set(stages) == {"decode", "detect", "track", "relink", "place", "compute"}
    detect = stages["detect"]
    assert (detect.tool, detect.version, detect.device) == ("tests.BrightBoxDetector", "1", "cpu")
    assert detect.licence is None and detect.weights_sha256 is None  # not known, so not claimed
    assert detect.frames_in == 30
    assert detect.items_out >= 58  # two boxes in nearly every frame
    assert stages["relink"].items_out == 2
    assert all(s.elapsed_s is not None for s in record.stages)
    assert stages["track"].parameters["confirm_hits"] == 3


def test_the_annotated_video_is_listed_but_not_bundled(video, tmp_path):
    record = analyse(video, tmp_path)
    artifact = next(a for a in record.artifacts if a.role == "annotated_video")
    assert not artifact.bundled
    assert artifact.sha256 is None
    assert artifact.path.endswith(".mp4")
    assert "render_annotated_video" in artifact.rebuild_hint
    assert not (tmp_path / "tiny" / artifact.path).exists()


def test_density_is_a_real_artifact(video, tmp_path):
    record = analyse(video, tmp_path)
    artifact = next(a for a in record.artifacts if a.role == "density")
    grid = json.loads((tmp_path / "tiny" / artifact.path).read_text("utf-8"))
    assert grid["frame"] == "image_px"
    assert sum(map(sum, grid["counts"])) > 50


def test_progress_reports_real_numbers_and_a_real_preview(video, tmp_path):
    updates = []
    preview = tmp_path / "live" / "preview.jpg"
    analyse(video, tmp_path, progress_callback=updates.append, preview_path=preview, mode="live")
    frames = [u.frame for u in updates if u.stage == "detect" and u.fps is not None]
    assert frames == sorted(frames)
    assert frames[-1] == 30
    assert {u.frames_total for u in updates} == {30}
    assert updates[-1].stage == "write"
    assert updates[0].as_dict().keys() == {"stage", "elapsed_s", "message", "frames_total"}
    image = cv2.imread(str(preview))
    assert image.shape == (HEIGHT, WIDTH, 3)


def test_cancelling_writes_nothing(video, tmp_path):
    seen = []

    def stop() -> bool:
        seen.append(1)
        return len(seen) > 5

    with pytest.raises(P.AnalysisCancelled):
        analyse(video, tmp_path, cancel_check=stop)
    assert not (tmp_path / "tiny").exists()
    assert not list(tmp_path.glob(".tiny.partial*"))


def test_an_analysis_is_never_overwritten(video, tmp_path):
    analyse(video, tmp_path)
    with pytest.raises(P.AnalysisError):
        analyse(video, tmp_path)


def test_a_detector_that_fails_leaves_nothing(video, tmp_path):
    class Broken:
        def detect(self, frame_bgr):
            raise RuntimeError("out of memory")

    with pytest.raises(RuntimeError):
        analyse(video, tmp_path, detector=Broken())
    assert not (tmp_path / "tiny").exists()


def test_a_blind_detector_gives_an_empty_honest_analysis(video, tmp_path):
    class Blind:
        def detect(self, frame_bgr):
            return []

    record = analyse(video, tmp_path, detector=Blind())
    assert {m.metric_id: m.value for m in record.metrics}["vehicles.total"] == 0.0
    assert verify_analysis(tmp_path / "tiny") == record
    assert load_tracks(tmp_path / "tiny").tracks == ()


def test_a_short_video_is_disclosed(video, tmp_path):
    record = analyse(video, tmp_path, duration_s=100.0)
    assert record.frame_count == 30
    assert any("ended before" in d for d in record.disclosures)


def test_a_stride_keeps_frame_over_fps_in_seconds(video, tmp_path):
    record = analyse(video, tmp_path, frame_stride=2)
    assert record.frame_stride == 2
    assert record.fps == 5.0
    assert record.frame_count == 15
    assert record.frame_count / record.fps == 3.0
    assert any("every 2th" in d for d in record.disclosures)


def test_notes_become_disclosures(video, tmp_path):
    record = analyse(video, tmp_path, notes=("CUDA was not available.",))
    assert "CUDA was not available." in record.disclosures


def test_a_calibrated_run_needs_to_know_where_the_video_sits(video, tmp_path, site):
    with pytest.raises(P.AnalysisError):
        analyse(video, tmp_path, site=site)


def test_a_calibrated_run_has_ground_arms_and_withheld_speed(video, tmp_path, site):
    record = analyse(video, tmp_path, site=site, source_offset_s=970.0)
    assert record.calibration.mode == "calibrated"
    assert record.calibration.ground_frame == "local_enu_junction_m"
    assert not record.calibration.accepted_along_road
    assert record.calibration.valid_below_row_px == 410
    assert record.site_id == "ngsim-lankershim-int2"
    assert record.source_start_s == 970.0
    assert record.window_offset_s == 120.0
    assert not record.source.uploaded_by_user
    assert "CC BY-SA" in record.source.attribution
    assert record.provenance.kind == "approximated"
    by_id = {m.metric_id: m for m in record.metrics}
    assert by_id["speed.mean_moving"].status == "not_available"
    assert "ADR-018" in by_id["speed.mean_moving"].reason
    assert by_id["vehicles.arm.n"].status == "ungated"
    _, columns = decode_trajectories((tmp_path / "tiny" / "trajectories.bin").read_bytes())
    assert np.all(columns.gx != NO_GROUND)


def test_a_camera_analysis_stores_no_per_point_speed(video, tmp_path, site):
    """Written and read back, as the twin reads it: ADR-018 leaves no speed to show."""
    record = analyse(video, tmp_path, site=site, source_offset_s=970.0)
    _, columns = decode_trajectories((tmp_path / "tiny" / "trajectories.bin").read_bytes())
    assert len(columns) > 0
    assert np.all(columns.speed_cm_s == NO_SPEED)
    assert any("no per-point speed is stored" in d for d in record.disclosures)


def logged_read(log: Path, analysis_id: str = "tiny") -> F.HeldOutGrant:
    entry = {"stage": "read", "gate_id": "g", "gate_sha256": "a" * 64, "analysis_id": analysis_id}
    log.write_text(json.dumps(entry) + "\n", "utf-8")
    return F.HeldOutGrant("g", "a" * 64, analysis_id, log)


def test_an_odd_block_opens_only_to_a_logged_read(video, tmp_path, site):
    with pytest.raises(HeldOutError):
        analyse(video, tmp_path, site=site, source_offset_s=190.0)
    assert not (tmp_path / "tiny").exists()
    unlogged = F.HeldOutGrant("g", "a" * 64, "tiny", tmp_path / "reads.jsonl")
    with pytest.raises(HeldOutError):
        analyse(video, tmp_path, site=site, source_offset_s=190.0, held_out_grant=unlogged)
    assert not (tmp_path / "tiny").exists()
    grant = logged_read(tmp_path / "reads.jsonl")
    record = analyse(video, tmp_path, site=site, source_offset_s=190.0, held_out_grant=grant)
    assert any("held-out gate g" in d for d in record.disclosures)
    assert record.provenance.parameters["held_out_gate_id"] == "g"


def test_a_read_of_one_analysis_does_not_open_another(video, tmp_path, site):
    grant = logged_read(tmp_path / "reads.jsonl", "other")
    with pytest.raises(HeldOutError):
        analyse(video, tmp_path, site=site, source_offset_s=190.0, held_out_grant=grant)
    assert not (tmp_path / "tiny").exists()


def pose_as_camera2(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(F, "CAMERA2_BYTES", path.stat().st_size)
    monkeypatch.setattr(F, "CAMERA2_SHA256", sha256_file(path))


def test_a_renamed_copy_of_camera_2_keeps_the_block_rule(video, tmp_path, monkeypatch):
    """The bytes decide, so renaming the AVI does not make it an upload."""
    copy = tmp_path / "holiday.avi"
    shutil.copy(video, copy)
    pose_as_camera2(monkeypatch, copy)
    assert F.is_camera2(copy)
    assert F.camera2_offset_s(copy) == 0.0
    with pytest.raises(HeldOutError):
        analyse(copy, tmp_path, start_s=190.0)  # no offset is given: the file is known anyway
    assert not (tmp_path / "tiny").exists()


def test_another_file_of_the_same_size_is_not_camera_2(video, tmp_path, monkeypatch):
    copy = tmp_path / "holiday.avi"
    shutil.copy(video, copy)
    monkeypatch.setattr(F, "CAMERA2_BYTES", copy.stat().st_size)
    assert not F.is_camera2(copy)


def manifest_for(root: Path, clip: Path, start_s: float, source_sha256: str = F.CAMERA2_SHA256):
    directory = root / "some-clip"
    directory.mkdir()
    (directory / "source_clip.json").write_text(
        json.dumps(
            {
                "output_sha256": sha256_file(clip),
                "source_sha256": source_sha256,
                "trim": {"start_s": start_s, "duration_s": 3.0},
            }
        ),
        "utf-8",
    )


def test_a_clip_is_known_by_its_manifest_wherever_the_file_is(video, tmp_path, monkeypatch):
    """data/processed/clips holds a clip spanning two odd blocks; nothing in its name says so."""
    monkeypatch.setattr(F, "CLIP_MANIFESTS", tmp_path)
    assert F.camera2_offset_s(video) is None
    manifest_for(tmp_path, video, 190.0)
    assert F.camera2_offset_s(video) == 190.0
    out = tmp_path / "out"
    with pytest.raises(HeldOutError):
        analyse(video, out)  # no offset is given
    assert not (out / "tiny").exists()


def test_a_manifest_from_another_source_says_nothing(video, tmp_path, monkeypatch):
    monkeypatch.setattr(F, "CLIP_MANIFESTS", tmp_path)
    manifest_for(tmp_path, video, 190.0, source_sha256="b" * 64)
    assert F.camera2_offset_s(video) is None


def test_a_factory_never_gets_a_held_out_second_of_a_known_clip(video, tmp_path, monkeypatch):
    monkeypatch.setattr(F, "CLIP_MANIFESTS", tmp_path)
    manifest_for(tmp_path, video, 178.5)  # seconds 0 to 1.5 are dev, the rest block 1
    seen = {}

    def factory(context):
        seen["times"] = context.dev_times
        return BrightBoxDetector()

    monkeypatch.setattr(P, "resolve_factory", lambda name: factory)
    P.build_detector("x", {}, video)
    assert seen["times"]
    assert all(178.5 + t < 180.0 for t in seen["times"])


def test_a_whole_window_inside_the_validated_seconds_needs_no_note(video, tmp_path, site):
    record = analyse(video, tmp_path, site=site, source_offset_s=970.0)
    assert not any("validated window" in d for d in record.disclosures)


def test_a_window_outside_the_validated_seconds_says_so(video, tmp_path, site):
    record = analyse(video, tmp_path, site=site, source_offset_s=240.0)
    note = next(d for d in record.disclosures if "outside the validated window" in d)
    assert "240-243" in note
    assert "970-1000" in note
    assert record.calibration.valid_windows_s == ((970.0, 1000.0),)


def test_a_window_half_inside_the_validated_seconds_says_how_much(video, tmp_path, site):
    record = analyse(video, tmp_path, site=site, source_offset_s=998.5)
    note = next(d for d in record.disclosures if "validated window" in d)
    assert "Part of this run" in note
    assert "1.5 s of this run's 3 s" in note


@pytest.mark.parametrize(("stride", "opening"), [(1, "0.3 s"), (5, "1 s")])
def test_the_entering_note_states_the_rule_that_ran(video, tmp_path, site, stride, opening):
    record = analyse(video, tmp_path, site=site, source_offset_s=970.0, frame_stride=stride)
    note = next(d for d in record.disclosures if d.startswith("Entering counts"))
    assert f"in the first {opening} of the window" in note
    assert "the two definitions differ" in note


def test_the_validated_seconds_are_the_ones_gate_v3_ran_on():
    gate = json.loads((REPO / GATE_V3).read_text("utf-8"))
    assert "source seconds 970-1000" in gate["data"]["excerpt"]
    assert P.GATE_V3_WINDOWS_S == ((970.0, 1000.0),)


def test_the_first_frame_of_a_dev_block_stays_a_hole_and_says_so(video, tmp_path, site):
    record = analyse(video, tmp_path, site=site, source_offset_s=960.0)
    assert record.frame_count == 30  # frames keep their time
    assert record.source_start_s == 960.0
    assert any("held-out block before it" in d for d in record.disclosures)
    assert min(t.start_frame for t in load_tracks(tmp_path / "tiny").tracks) >= 1


# ---- rendering -------------------------------------------------------------------------------


def probe(path: Path) -> dict:
    done = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v", "-print_format", "json",
            "-show_entries", "stream=codec_name,pix_fmt,nb_read_frames,width,height",
            "-count_frames", str(path),
        ],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True,
    )  # fmt: skip
    return json.loads(done.stdout)["streams"][0]


def test_render_makes_a_browser_playable_annotated_video(video, tmp_path):
    analyse(video, tmp_path)
    updates = []
    result = R.render_annotated_video(tmp_path / "tiny", video, on_frame=updates.append)
    assert result.path == tmp_path / "tiny" / R.ANNOTATED_VIDEO
    assert result.frames == 30
    assert result.boxes >= 55
    assert result.encoder in R.ENCODERS
    facts = probe(result.path)
    assert facts["codec_name"] == "h264"
    assert facts["pix_fmt"] == "yuv420p"
    assert int(facts["nb_read_frames"]) == 30
    data = result.path.read_bytes()
    assert data.find(b"moov") < data.find(b"mdat")  # +faststart
    assert verify_analysis(tmp_path / "tiny")  # the record is untouched
    assert updates[-1] == 30


def test_render_with_libx264_when_nvenc_is_unavailable(video, tmp_path, monkeypatch):
    analyse(video, tmp_path)
    monkeypatch.setattr(R, "encoder_works", lambda name: name == "libx264")
    assert R.render_annotated_video(tmp_path / "tiny", video).encoder == "libx264"


def test_render_refuses_to_overwrite_and_the_wrong_video(video, tmp_path):
    analyse(video, tmp_path)
    R.render_annotated_video(tmp_path / "tiny", video)
    with pytest.raises(R.RenderError):
        R.render_annotated_video(tmp_path / "tiny", video)
    other = write_video(tmp_path / "other.avi", seconds=2.0)
    with pytest.raises(R.RenderError):
        R.render_annotated_video(tmp_path / "tiny", other, overwrite=True)


def test_render_keeps_a_held_back_frame_as_black_and_its_time(video, tmp_path, site):
    analyse(video, tmp_path, site=site, source_offset_s=960.0)
    result = R.render_annotated_video(tmp_path / "tiny", video)
    assert result.frames == 30


def test_a_held_out_analysis_renders_only_after_its_gate_scored_it(video, tmp_path, site):
    log = tmp_path / "reads.jsonl"
    analyse(video, tmp_path, site=site, source_offset_s=190.0, held_out_grant=logged_read(log))
    with pytest.raises(HeldOutError):
        R.render_annotated_video(tmp_path / "tiny", video)  # no log to show
    with pytest.raises(HeldOutError):
        R.render_annotated_video(tmp_path / "tiny", video, read_log=log)  # read, not scored
    assert not (tmp_path / "tiny" / R.ANNOTATED_VIDEO).exists()
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"stage": "score", "gate_id": "g", "analysis_id": "tiny"}) + "\n")
    assert R.render_annotated_video(tmp_path / "tiny", video, read_log=log).frames == 30


def test_the_caption_says_what_this_is_and_credits_the_source():
    image = np.zeros((480, 640, 3), np.uint8)
    R.draw_banners(image, f"t {R.CAPTION}", "word " * 200)
    assert image[:14].any()  # the top banner
    assert image[-30:].any()  # the wrapped attribution
    assert "NOT ground truth" in R.CAPTION
    assert len(R.wrap_text("word " * 200, 632)) > 3


def test_a_jpeg_is_replaced_whole(tmp_path):
    path = tmp_path / "preview.jpg"
    R.write_jpeg_atomic(path, np.zeros((16, 16, 3), np.uint8))
    R.write_jpeg_atomic(path, np.full((16, 16, 3), 255, np.uint8))
    assert cv2.imread(str(path)).mean() > 200
    assert [p.name for p in tmp_path.iterdir()] == ["preview.jpg"]
    assert path.stat().st_mode & 0o777 == 0o644


# ---- the worker ------------------------------------------------------------------------------


def job(tmp_path: Path, video: Path, **overrides) -> Path:
    directory = tmp_path / "job"
    directory.mkdir()
    fields = {
        "protocol": "cairoflow-job/1",
        "job_id": "job-20261104t000000z-abcd1234",
        "video_path": str(video),
        "calibrated": False,
        "source_start_s": None,
        "start_s": 0.0,
        "duration_s": 3.0,
        "frame_stride": 1,
        "analyses_dir": str(tmp_path / "analyses"),
        "repo_root": str(REPO),
    }
    (directory / "job.json").write_text(json.dumps({**fields, **overrides}), "utf-8")
    (directory / "progress.jsonl").touch()
    return directory


def worker(directory: Path, detector: str = "test_cv_pipeline:make_detector") -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(REPO / "tests")}
    return subprocess.Popen(
        [sys.executable, str(WORKER), "--job-dir", str(directory), "--detector", detector],
        cwd=REPO,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def lines(directory: Path) -> list[dict]:
    text = (directory / "progress.jsonl").read_text("utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_the_worker_runs_a_job_to_done(video, tmp_path):
    directory = job(tmp_path, video)
    process = worker(directory)
    process.communicate(timeout=120)
    assert process.returncode == 0
    reported = lines(directory)
    assert reported[-1] == {"stage": "done", "analysis_id": "cv-job-20261104t000000z-abcd1234"}
    assert all("stage" in line for line in reported)
    frames = [line for line in reported if line["stage"] == "detect" and "fps" in line]
    assert frames[-1]["frame"] == 30
    assert frames[-1]["frames_total"] == 30
    assert frames[-1]["detections"] > 0
    assert (directory / "preview.jpg").is_file()
    assert not list(directory.glob(".*"))  # no temporary left
    record = verify_analysis(tmp_path / "analyses" / reported[-1]["analysis_id"])
    assert record.mode == "live"


def test_the_worker_leaves_out_what_it_does_not_know(video, tmp_path):
    directory = job(tmp_path, video)
    process = worker(directory)
    process.communicate(timeout=120)
    first = lines(directory)[0]
    assert first["stage"] == "load"
    assert "fps" not in first
    assert "frame" not in first


def test_the_worker_analyses_a_calibrated_clip_in_its_own_seconds(video, tmp_path):
    directory = job(tmp_path, video, calibrated=True, source_start_s=970.0, start_s=1.0)
    directory_ok = worker(directory)
    directory_ok.communicate(timeout=120)
    assert directory_ok.returncode == 0
    record = verify_analysis(tmp_path / "analyses" / lines(directory)[-1]["analysis_id"])
    assert record.calibration.mode == "calibrated"
    assert record.source_start_s == 971.0


def test_the_worker_reports_a_missing_video_and_exits_non_zero(tmp_path):
    directory = job(tmp_path, tmp_path / "nothing.avi")
    process = worker(directory)
    process.communicate(timeout=120)
    assert process.returncode != 0
    final = lines(directory)[-1]
    assert final["stage"] == "failed"
    assert "nothing.avi" in final["error"]


def test_the_worker_reports_a_detector_that_will_not_load(video, tmp_path):
    directory = job(tmp_path, video)
    process = worker(directory, "test_cv_pipeline:make_broken_detector")
    process.communicate(timeout=120)
    assert process.returncode != 0
    final = lines(directory)[-1]
    assert final["stage"] == "failed"
    assert "the weights are missing" in final["error"]
    assert not (tmp_path / "analyses").exists()


def test_the_worker_refuses_another_protocol(video, tmp_path):
    directory = job(tmp_path, video, protocol="cairoflow-job/2")
    process = worker(directory)
    process.communicate(timeout=120)
    assert process.returncode != 0
    assert "cairoflow-job/2" in lines(directory)[-1]["error"]


def test_the_worker_refuses_a_held_out_window(video, tmp_path):
    directory = job(tmp_path, video, calibrated=True, source_start_s=190.0)
    process = worker(directory)
    process.communicate(timeout=120)
    assert process.returncode != 0
    final = lines(directory)[-1]
    assert final["stage"] == "failed"
    assert "held-out" in final["error"]


def test_sigterm_stops_the_worker_cleanly(video, tmp_path):
    directory = job(tmp_path, video)
    process = worker(directory, "test_cv_pipeline:make_slow_detector")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if any(line.get("frame", 0) >= 5 for line in lines(directory)):
            break
        time.sleep(0.02)
    else:
        process.kill()
        pytest.fail("the worker never reported a frame")
    os.killpg(process.pid, signal.SIGTERM)
    process.communicate(timeout=30)
    assert process.returncode == 143
    assert lines(directory)[-1] == {"stage": "failed", "error": "cancelled"}
    assert not any((tmp_path / "analyses").glob("cv-*"))


def test_cpu_fallback_raises_the_stride_and_says_so(monkeypatch):
    monkeypatch.setattr(P, "cuda_available", lambda: False)
    settings = P.DetectorSettings("x", requires_cuda=True, cpu_min_frame_stride=5)
    plan = P.plan_device(settings, 1)
    assert (plan.device, plan.frame_stride) == ("cpu", 5)
    assert "CUDA was not available" in plan.notes[0]
    assert "stride 5" in plan.notes[0]
    assert P.plan_device(settings, 8).frame_stride == 8


def test_cuda_keeps_the_stride(monkeypatch):
    monkeypatch.setattr(P, "cuda_available", lambda: True)
    settings = P.DetectorSettings("x", requires_cuda=True, cpu_min_frame_stride=5)
    assert P.plan_device(settings, 1) == P.DevicePlan("cuda", 1, ())


def test_a_cpu_detector_needs_no_cuda_check(monkeypatch):
    monkeypatch.setattr(P, "cuda_available", lambda: pytest.fail("must not be called"))
    assert P.plan_device(P.DetectorSettings("x"), 2) == P.DevicePlan("cpu", 2, ())


def test_settings_load_from_the_lead_file(tmp_path):
    path = tmp_path / "detector.json"
    path.write_text(
        json.dumps(
            {
                "detector": "pkg.mod:factory",
                "args": {"threshold": 0.3},
                "requires_cuda": True,
                "cpu_min_frame_stride": 4,
                "tracker": {"high_score": 0.5},
            }
        ),
        "utf-8",
    )
    settings = P.DetectorSettings.load(path)
    assert settings.detector == "pkg.mod:factory"
    assert settings.args == {"threshold": "0.3"}
    assert settings.tracker_params().high_score == 0.5


def capture_context(monkeypatch) -> list:
    seen = []

    def factory(context):
        seen.append((context, [context.sampler.read(t).shape for t in context.dev_times]))
        return BrightBoxDetector()

    monkeypatch.setattr(P, "resolve_factory", lambda name: factory)
    return seen


def test_a_factory_never_gets_a_held_out_second_of_a_clip(video, monkeypatch):
    seen = capture_context(monkeypatch)
    P.build_detector("x", {}, video, source_offset_s=178.5)  # block 1 starts 1.5 s in
    context, shapes = seen[0]
    assert context.dev_times
    assert max(context.dev_times) < 1.5
    assert all(not F.is_held_out_second(178.5 + t) for t in context.dev_times)
    assert len(shapes) == len(context.dev_times)


def test_a_sampler_on_a_clip_refuses_a_held_out_second(video):
    sampler = P.ClipSampler(video, source_offset_s=178.5)
    try:
        assert sampler.read(0.5).shape == (HEIGHT, WIDTH, 3)
        with pytest.raises(HeldOutError):
            sampler.read(2.0)
    finally:
        sampler.close()


def test_a_factory_on_an_upload_reads_through_the_whole_video(video, monkeypatch):
    seen = capture_context(monkeypatch)
    P.build_detector("x", {}, video)
    context, _ = seen[0]
    assert len(context.dev_times) == 60
    assert max(context.dev_times) > 2.5


def test_a_second_is_held_out_by_either_reading_of_its_time():
    assert F.is_held_out_second(190.0)  # block 1
    assert F.is_held_out_second(240.0)  # block 2, but its image is the last frame of block 1
    assert not F.is_held_out_second(240.1)
    assert not F.is_held_out_second(130.0)  # block 0
    assert not F.is_held_out_second(50.0)  # no block
    assert not F.is_held_out_second(1100.0)  # no block


def test_a_factory_is_found_by_name():
    assert P.resolve_factory("median-blob").__name__ == "make_detector"
    assert P.resolve_factory("cairoflow_perception.local.median_blob:make_detector")


# ---- the commands refuse a held-out read -----------------------------------------------------


def command(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args], cwd=REPO, capture_output=True, text=True,
        timeout=120, stdin=subprocess.DEVNULL,
        env={**os.environ, "PYTHONPATH": str(REPO / "tests")},
    )  # fmt: skip


def analyse_command(video: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    return command(
        ANALYSE, "--video", str(video), "--start-s", "0", "--duration-s", "3", "--id", "cli",
        "--out", str(tmp_path / "analyses"), "--read-log", str(tmp_path / "reads.jsonl"),
        "--detector", "test_cv_pipeline:make_detector", *args,
    )  # fmt: skip


def wrote_nothing(tmp_path: Path) -> bool:
    return not (tmp_path / "reads.jsonl").exists() and not (tmp_path / "analyses" / "cli").exists()


@pytest.mark.parametrize("script", [ANALYSE, RENDER])
def test_no_command_has_a_flag_that_opens_held_out_pixels(script):
    done = command(script, "--help")
    assert done.returncode == 0
    assert "allow-held-out" not in done.stdout


def test_the_analyse_command_refuses_an_odd_block_and_writes_nothing(video, tmp_path):
    done = analyse_command(video, tmp_path, "--source-offset-s", "190")
    assert done.returncode == 2
    assert "REFUSED" in done.stderr
    assert wrote_nothing(tmp_path)


def test_the_analyse_command_refuses_a_gate_that_is_not_committed(video, tmp_path):
    gate = tmp_path / "gate.json"
    gate.write_text(
        json.dumps(
            {
                "gate_id": "cv-test-v1",
                "created_at": "2026-11-04T12:00:00Z",
                "blocks": {
                    "formula": "floor((source_s - 120) / 60)", "dev": "even", "held_out": "odd",
                },
                "metrics": {"vehicle_recall": {"comparator": ">=", "threshold": 0.5, "min_n": 1}},
            }
        ),
        "utf-8",
    )  # fmt: skip
    done = analyse_command(
        video, tmp_path, "--source-offset-s", "190", "--held-out-gate", str(gate)
    )
    assert done.returncode == 2
    assert "REFUSED" in done.stderr
    assert wrote_nothing(tmp_path)


def test_the_analyse_command_still_runs_a_dev_window(video, tmp_path):
    done = analyse_command(video, tmp_path, "--source-offset-s", "970")
    assert done.returncode == 0, done.stderr
    assert verify_analysis(tmp_path / "analyses" / "cli").calibration.mode == "calibrated"
    assert not (tmp_path / "reads.jsonl").exists()  # a dev run spends nothing


# ---- on the real camera 2 file ---------------------------------------------------------------
# Dev blocks only: these tests read no odd block, and the refusal below reads no pixel at all.


def cut_clip(start_s: float, seconds: float, out: Path) -> Path:
    fill = {SOURCE_PLACEHOLDER: str(CAMERA2), OUTPUT_PLACEHOLDER: str(out)}
    run = transcode_command(TrimSpec(start_s=start_s, duration_s=seconds), FPS)
    subprocess.run(
        [fill.get(part, part) for part in run], check=True, stdin=subprocess.DEVNULL, timeout=120
    )
    return out


def difference(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a[:400].astype(np.int16) - b[:400].astype(np.int16)).mean())


@needs_camera2
@pytest.mark.parametrize("trim_s", [130.0, 965.3, 970.0, 1000.5])
def test_a_clip_and_the_avi_show_the_same_image_at_each_source_second(tmp_path, trim_s):
    """The label a run gives a clip frame and the label it gives an AVI frame mean the same image,
    so a clip needs no lag. Measured on the real file, whose 10.00001 fps header is what moves the
    cut by a frame: a generated video could not show this."""
    assert F.held_out_frames(F.plan_frames(FPS, trim_s, 1.0)) == set()
    clip = cut_clip(trim_s, 1.0, tmp_path / "clip.mp4")
    from_clip = list(F.iter_frames(clip, 0.0, 1.0, source_offset_s=trim_s))
    from_avi = list(F.iter_frames(CAMERA2, trim_s, 1.0, frame_lag=F.AVI_FRAME_LAG))
    assert [s for _, s, _ in from_clip] == [s for _, s, _ in from_avi]
    assert len(from_clip) == 10
    unlagged = list(F.iter_frames(CAMERA2, trim_s, 1.0))
    for (_, _, image), (_, _, same), (_, _, next_frame) in zip(
        from_clip, from_avi, unlagged, strict=True
    ):
        assert difference(image, same) < 0.6 * difference(image, next_frame)


@needs_camera2
def test_a_renamed_camera_2_is_refused_on_an_odd_block(tmp_path):
    """The real bytes under another name: refused before any pixel is read."""
    link = tmp_path / "holiday.avi"
    link.symlink_to(CAMERA2)
    assert F.camera2_offset_s(link) == 0.0
    with pytest.raises(HeldOutError):
        analyse(link, tmp_path, start_s=190.0, duration_s=1.0)
    assert not (tmp_path / "tiny").exists()


def test_the_camera_2_hash_is_the_published_one():
    sums = CAMERA2.parent / "SHA256SUMS"
    if not sums.is_file():
        pytest.skip("the camera 2 checksums are not distributed with the repository")
    assert F.CAMERA2_SHA256 in sums.read_text("utf-8")


def test_every_clip_on_disk_has_a_manifest_this_code_can_read():
    """The block rule on a clip rests on these fields; a real manifest must still have them."""
    manifests = sorted(F.CLIP_MANIFESTS.glob("*/source_clip.json"))
    if not manifests:
        pytest.skip("no clips here")
    for manifest in manifests:
        clip = json.loads(manifest.read_text("utf-8"))
        assert clip["source_sha256"] == F.CAMERA2_SHA256
        assert len(clip["output_sha256"]) == 64
        assert clip["trim"]["start_s"] >= 0
