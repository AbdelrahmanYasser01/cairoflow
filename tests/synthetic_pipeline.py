"""Inputs for a whole-flow pipeline run on synthetic data, shared by tests.

A 70 s, 10 fps clip of the synthetic junction (synthetic_junction.py) seen by the synthetic
camera: three cars go south to north, one turns left from the south, one goes west to east. SAM
output is a synthetic stream written into a SAM cache record (no request). `MuseStub` answers
like Muse Spark through httpx.MockTransport. Signals: an assumed 30 s two-phase cycle.

Without video, the clip is a manifest only (the pipeline needs no pixels once SAM is cached).
With video, a plain grey 1280x720 clip is generated with FFmpeg and ingested, so the run can also
render the review overlay.
"""

from __future__ import annotations

import json
import math
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from synthetic_camera import calibration_spec, project
from synthetic_clip import FPS
from synthetic_clip import clip as synthetic_clip
from synthetic_junction import geometry
from synthetic_sam import sse, token

from cairoflow_contracts.evidence import ClipLicense, SourceClip
from cairoflow_perception.calibration import spec_sha256
from cairoflow_perception.ingest import ingest_clip
from cairoflow_perception.sam_adapter import DEFAULT_METADATA, SamRequestRecord, request_key

AS_OF = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
FRAMES = 700  # 70 s
FAKE_KEY = "fake-test-key-0123456789"
PATHS = {  # ground waypoints (m) and start frame; 10 m/s
    1: ([(5.25, -30.0), (5.25, 30.0)], 0),
    2: ([(5.25, -30.0), (5.25, 30.0)], 150),
    3: ([(5.25, -30.0), (5.25, 30.0)], 300),
    4: ([(1.75, -30.0), (1.75, 3.5), (-25.0, 3.5)], 100),
    5: ([(-30.0, -3.5), (30.0, -3.5)], 200),
}


def positions(waypoints, start: int, speed: float = 10.0):
    legs = list(zip(waypoints, waypoints[1:], strict=False))
    total = sum(math.dist(a, b) for a, b in legs)
    for frame in range(start, FRAMES):
        s = (frame - start) / FPS * speed
        if s > total:
            return
        for a, b in legs:
            leg = math.dist(a, b)
            if s <= leg:
                yield frame, (a[0] + s / leg * (b[0] - a[0]), a[1] + s / leg * (b[1] - a[1]))
                break
            s -= leg


def signal_csv() -> str:
    rows = ["0,N,red", "0,S,red"]
    for k in range(3):
        base = 30 * k
        for offset, arms, state in (
            (0, "EW", "green"), (10, "EW", "yellow"), (13, "EW", "red"),
            (15, "NS", "green"), (25, "NS", "yellow"), (28, "NS", "red"),
        ):  # fmt: skip
            if base + offset < FRAMES / FPS:
                rows += [f"{base + offset},{arm},{state}" for arm in arms]
    return "# assumed two-phase plan, 30 s cycle\ntime_s,arm,state\n" + "\n".join(rows) + "\n"


def _video_clip(inputs: Path) -> tuple[SourceClip, str]:
    source = inputs / "grey.mp4"
    subprocess.run(
        [
            *("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"),
            *("-f", "lavfi", "-i", f"color=c=0x606060:size=1280x720:rate={FPS}"),
            *("-t", str(FRAMES / FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)),
        ],
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    licence = ClipLicense(
        source_id="synthetic-grey",
        source_url="https://ffmpeg.org/ffmpeg-filters.html#color",
        licence_name="generated in code",
        attribution="CairoFlow synthetic grey clip (generated with FFmpeg)",
        accessed_on=date(2026, 10, 27),
        register_ref="tests/synthetic_pipeline.py",
        permits_commercial_use=True,
        permits_cloud_processing=True,
        permits_redistribution=True,
        share_alike=False,
        synthetic=True,
    )
    clip = ingest_clip(source, licence, inputs / "clip", target_fps=FPS, now=AS_OF)
    return clip, "clip/source_clip.json"


def write_inputs(root: Path, *, with_video: bool = False) -> Path:
    """Write every input of a run under root/inputs and return pipeline_inputs.json."""
    inputs = root / "inputs"
    inputs.mkdir()
    if with_video:
        clip, manifest = _video_clip(inputs)
    else:
        clip, manifest = synthetic_clip(FRAMES), "source_clip.json"
        (inputs / manifest).write_text(clip.model_dump_json(indent=2), "utf-8")

    tokens = []
    for obj, (waypoints, start) in PATHS.items():
        for frame, ground in positions(waypoints, start):
            u, v = project(*ground)
            tokens.append((frame, obj, token(frame, obj, round(u - 24), round(v - 48), 1280, 720)))
    key = request_key("sam-3.1", "car", clip.output_sha256, dict(DEFAULT_METADATA))
    record = SamRequestRecord(
        request_key=key,
        model="sam-3.1",
        prompt="car",
        clip_id=clip.clip_id,
        clip_sha256=clip.output_sha256,
        metadata=dict(DEFAULT_METADATA),
        sent_at_utc=AS_OF,
        http_status=200,
        error_class="ok",
        request_id="req-sam-synthetic",
        elapsed_s=1.0,
        frames_billed=FRAMES,
        estimated_cost_usd=0.14,
        lines=tuple(sse([t for _, _, t in sorted(tokens)])),
    )
    (inputs / "sam").mkdir()
    (inputs / "sam" / f"{key}.json").write_text(record.model_dump_json(indent=2), "utf-8")

    spec = calibration_spec(clip_id=clip.clip_id)
    (inputs / "calibration.json").write_text(spec.model_dump_json(indent=2), "utf-8")
    geo = geometry(spec_sha256(spec), clip.clip_id)
    (inputs / "geometry.json").write_text(geo.model_dump_json(indent=2), "utf-8")
    (inputs / "signals.csv").write_text(signal_csv(), "utf-8")
    pipeline_inputs = {
        "run_name": "synthetic-junction",
        "as_of_utc": AS_OF.isoformat(),
        "clip_manifest": manifest,
        "sam_cache_dir": "sam",
        "calibration_spec": "calibration.json",
        "geometry": "geometry.json",
        "signal_annotation": "signals.csv",
        "signal_label": "assumed",
        "signal_source": "test plan",
        "muse_cache_dir": "muse",
        "settings": {"warmup_s": 30, "horizon_s": 120, "drain_s": 120},
    }
    path = inputs / "pipeline_inputs.json"
    path.write_text(json.dumps(pipeline_inputs, indent=2), "utf-8")
    return path


class MuseStub:
    """Answers like Muse Spark, proposing timings from the bundle's adjustable phases."""

    def __init__(self, rationale: str = "shift green towards the busier axis") -> None:
        self.requests = 0
        self.rationale = rationale

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        body = json.loads(request.content)
        bundle = json.loads(body["input"].split("EVIDENCE (JSON):\n", 1)[1])
        first, second = [p["phase_name"] for p in bundle["phases"] if p["adjustable"]]
        movement = next(i["evidence_id"] for i in bundle["items"] if i["evidence_id"][:3] == "mv.")

        def intervention(iid: str, changes: list[tuple[str, float]]):
            return {
                "intervention_id": iid,
                "type": "signal_timing",
                "target_program": bundle["program_id"],
                "phase_changes": [{"phase_name": n, "green_s": g} for n, g in changes],
                "rationale": self.rationale,
                "expected_tradeoffs": ["the other axis waits longer"],
                "preconditions": ["the counted minute is typical"],
                "addresses_hypothesis_ids": ["H1"],
                "evidence_ids": [movement, "sig.program"],
            }

        output = {
            "hypotheses": [
                {
                    "hypothesis_id": hid,
                    "statement": statement,
                    "supporting_evidence_ids": [movement, "sig.program"],
                    "contradicting_evidence_ids": [],
                    "assumptions": ["a short clip is representative"],
                    "confidence_label": "low",
                    "alternative_explanations": ["random arrivals"],
                }
                for hid, statement in (
                    ("H1", "North-south green is short for its demand."),
                    ("H2", "Delay comes from random arrivals, not the split."),
                )
            ],
            "interventions": [
                intervention("I1", [(first, 15), (second, 10)]),
                intervention("I2", [(first, 12), (second, 8)]),
                intervention("I3", [(first, 3)]),  # below the minimum green: rejected
            ],
        }
        reply = {
            "id": "resp_synthetic",
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": json.dumps(output)}],
                }
            ],
            "usage": {"input_tokens": 3000, "output_tokens": 900},
        }
        return httpx.Response(200, json=reply, headers={"x-request-id": "req-muse-synthetic"})


def refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError("this run must not send a request")
