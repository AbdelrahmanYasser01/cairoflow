"""The orchestrated run's camera link (ADR-031): which SAM run and gate it reads, and the
camera items a go gate's layer adds to the evidence bundle."""

from __future__ import annotations

import functools
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from cairoflow_contracts.evidence import CameraLayer, TrafficWorldModel
from cairoflow_reasoning.evidence import ARM_CONVENTIONS, build_evidence_bundle

REPO = Path(__file__).resolve().parents[1]
RECORDS = REPO / "experiments/records"
SCENARIO = json.loads((REPO / "data/geometry/ngsim-lankershim-int2/scenario.json").read_text())
WORLD = REPO / SCENARIO["processed_outputs"]["dir"] / "world_model.json"
CUT = REPO / SCENARIO["baseline"]["run_dir"]


def _script():
    path = REPO / "scripts/run_cairoflow.py"
    spec = importlib.util.spec_from_file_location("run_cairoflow", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["run_cairoflow"] = module
    spec.loader.exec_module(module)
    return module


def _json(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def test_the_perception_stage_is_described_from_the_sam_record() -> None:
    run = _script()
    stamps = ("20261028T151050Z", "20261030T220322Z", "20261030T230408Z")
    details = [run.perception_detail(_json(RECORDS / f"ngsim-cam2-sam-{s}.json")) for s in stamps]
    assert details[0].startswith("SAM 3.1 on 10 s of NGSIM camera 2 (source seconds 370-380; ")
    assert details[1].endswith(": 25 tracks")
    assert "(source seconds 970-1000; 1 live request on 2026-10-30" in details[2]


def test_the_default_gate_is_go_and_built_from_the_default_sam_run() -> None:
    run = _script()
    gate = _json(run.CAMERA_GATE)
    assert gate["gate"]["outcome"] == "go" and gate["gate"]["gate_id"] == "cam2-int2-lane-gate-v3"
    layer = CameraLayer.model_validate_json(
        (run.CAMERA_GATE.with_suffix("") / "camera_layer.json").read_text("utf-8"))  # fmt: skip
    assert run.layer_matches_sam(layer, _json(run.SAM_RECORD))
    assert not run.layer_matches_sam(layer, _json(RECORDS / "ngsim-cam2-sam-20261030T220322Z.json"))


@pytest.mark.skipif(not (WORLD.is_file() and CUT.is_dir()),
                    reason="the world model and cut baseline are Git-ignored")  # fmt: skip
def test_a_go_layer_puts_camera_items_in_the_bundle() -> None:
    from cairoflow_simulation.candidates import load_baseline

    run = _script()
    layer = CameraLayer.model_validate_json(
        (run.CAMERA_GATE.with_suffix("") / "camera_layer.json").read_text("utf-8"))  # fmt: skip
    world = TrafficWorldModel.model_validate_json(WORLD.read_text("utf-8"))
    with_camera = TrafficWorldModel.model_validate(world.model_dump() | {"camera": layer})
    _, network = load_baseline(CUT)
    plain = build_evidence_bundle(world, network.manifest, (), end_of_red_per_lane=True,
                                  conventions=ARM_CONVENTIONS)  # fmt: skip
    bundle = build_evidence_bundle(with_camera, network.manifest, (), end_of_red_per_lane=True,
                                   conventions=ARM_CONVENTIONS)  # fmt: skip
    camera = [i for i in bundle.items if i.evidence_id.startswith("cam.")]
    assert len(bundle.items) == len(plain.items) + len(camera)
    lanes = {f"cam.lane.{lane}" for lane in layer.lane_presence}
    assert {i.evidence_id for i in camera} == lanes | {"cam.gate"}
    gate = next(i.summary for i in camera if i.evidence_id == "cam.gate")
    assert "D_across_heading_p90_m 1.004" in gate and "J_across_heading_p90_m 0.5427" in gate
    assert all(i.kind == "approximated" for i in camera)
    assert bundle.bundle_id != plain.bundle_id


# --- prompt versions and the evidence limitations they send (camera-to-muse-20261031) ---------

CAMERA_FED = "cairoflow-20261030T235016Z-r1"
UNDER_0_3_0 = ("cairoflow-20261030T213806Z-r1", "cairoflow-20261030T213929Z-r2",
               "cairoflow-20261030T214027Z-r3", CAMERA_FED)  # fmt: skip
CAMERA_FED_KEY = _json(RECORDS / f"{CAMERA_FED}.json")["muse"]["request_key"]
CAMERA_FED_REPLY = REPO / "cache/muse" / f"{CAMERA_FED_KEY}.json"
GIT_IGNORED = "the world model and cut baseline are Git-ignored"
NEEDS_WORLD = pytest.mark.skipif(not (WORLD.is_file() and CUT.is_dir()), reason=GIT_IGNORED)
DISCLAIMER = "the presence of camera items does not mean they support any hypothesis"


def _layer(run) -> CameraLayer:
    return CameraLayer.model_validate_json(
        (run.CAMERA_GATE.with_suffix("") / "camera_layer.json").read_text("utf-8"))  # fmt: skip


@functools.cache
def _inputs():
    from cairoflow_simulation.candidates import load_baseline

    return TrafficWorldModel.model_validate_json(WORLD.read_text("utf-8")), load_baseline(CUT)[1]


def _bundle(run, prompt_version, layer):
    """The bundle scripts/run_cairoflow.py builds for a prompt version, from the same inputs."""
    world, network = _inputs()
    if layer is not None:
        world = TrafficWorldModel.model_validate(world.model_dump() | {"camera": layer})
    return build_evidence_bundle(world, network.manifest,
                                 run.evidence_limitations(prompt_version, layer),
                                 end_of_red_per_lane=True, conventions=ARM_CONVENTIONS)  # fmt: skip


def _request(bundle, model: str, prompt_version: str, max_output_tokens: int) -> dict:
    from cairoflow_reasoning.prompts import build_request_body

    return build_request_body(bundle, model, max_output_tokens=max_output_tokens, store=False,
                              prompt_version=prompt_version)  # fmt: skip


def test_recorded_runs_name_prompt_0_3_0_and_new_runs_default_to_0_3_1() -> None:
    run = _script()
    for name in UNDER_0_3_0:
        assert _json(RECORDS / f"{name}.json")["muse"]["prompt_version"] == "0.3.0"
    assert run.PROMPT_VERSIONS == ("0.3.0", "0.3.1") and run.PROMPT_VERSION == "0.3.1"
    assert run.build_parser().parse_args([]).prompt_version == "0.3.1"
    assert run.build_parser().parse_args(["--prompt-version", "0.3.0"]).prompt_version == "0.3.0"


def test_unknown_or_missing_prompt_versions_are_refused() -> None:
    run = _script()
    for version in ("9.9.9", "0.2.0", "", None):
        with pytest.raises(ValueError, match="no evidence limitations for prompt version"):
            run.evidence_limitations(version, _layer(run))
    with pytest.raises(SystemExit):
        run.build_parser().parse_args(["--prompt-version", "0.2.0"])


def test_prompt_0_3_1_says_what_camera_items_can_and_cannot_show() -> None:
    run = _script()
    old, new = (run.evidence_limitations(v, _layer(run)) for v in run.PROMPT_VERSIONS)
    text = " ".join(new)
    assert "not from camera tracking" in old[0] and "not from camera tracking" not in text
    for phrase in (
        "come from one camera",
        "cover only its field of view during world-model seconds 850-880",
        "not intersection-wide traffic measurements",
        "only some of the vehicles and movements in view",
        "not independent of the trajectory items",
        "overlap what the hypothesis is about",
        DISCLAIMER,
    ):
        assert phrase in text
    assert text.count("support") == 1  # only in the disclaimer: no claim that camera items do
    for claim in ("confirm", "improve", "inform", "validated", "reliable", "accurate"):
        assert claim not in text.lower()
    assert old[1:-1] == new[1:4]  # the limitations that were right are sent unchanged


def test_prompt_0_3_1_without_a_camera_layer_says_no_camera_evidence_is_included() -> None:
    run = _script()
    old, new = (run.evidence_limitations(v, None) for v in run.PROMPT_VERSIONS)
    assert old[-1].startswith("No camera-derived evidence")  # 0.3.0 as sent
    assert "no camera-derived evidence is included" in new[0] and "cam.*" not in " ".join(new)
    assert old != new


@NEEDS_WORLD
def test_prompt_0_3_0_rebuilds_the_recorded_requests_exactly() -> None:
    from cairoflow_reasoning.muse_adapter import request_key

    run = _script()
    for name, layer, cap, bundle_id in (
        (CAMERA_FED, _layer(run), 16000, "9df855820476337c"),
        ("cairoflow-20261030T213806Z-r1", None, 8000, "22548ce9b8413d3b"),  # gate v1: no layer
    ):
        muse = _json(RECORDS / f"{name}.json")["muse"]
        bundle = _bundle(run, muse["prompt_version"], layer)
        assert bundle.bundle_id == bundle_id
        body = _request(bundle, muse["model"], muse["prompt_version"], cap)
        assert request_key(body) == muse["request_key"]


@NEEDS_WORLD
def test_each_prompt_version_sends_its_own_request() -> None:
    from cairoflow_reasoning.muse_adapter import request_key

    run = _script()
    for layer in (_layer(run), None):
        bodies = {v: _request(_bundle(run, v, layer), "muse-spark-1.3", v, 16000)
                  for v in run.PROMPT_VERSIONS}  # fmt: skip
        assert request_key(bodies["0.3.0"]) != request_key(bodies["0.3.1"])
        assert "not from camera tracking" not in bodies["0.3.1"]["input"]
        assert (DISCLAIMER in bodies["0.3.1"]["input"]) == (layer is not None)


@NEEDS_WORLD
@pytest.mark.skipif(not CAMERA_FED_REPLY.is_file(), reason="the Muse cache is Git-ignored")
def test_the_recorded_camera_fed_reply_replays_only_under_its_own_prompt_version() -> None:
    import httpx

    from cairoflow_contracts.config import Settings
    from cairoflow_reasoning.muse_adapter import MuseCacheMiss, MuseClient

    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no request may be sent ({request.url})")

    run = _script()
    record = _json(RECORDS / f"{CAMERA_FED}.json")

    def client(version: str) -> MuseClient:
        return MuseClient(Settings(), run.MUSE_CACHE, "replay", max_output_tokens=16000,
                          store=False, prompt_version=version,
                          transport=httpx.MockTransport(refuse))  # fmt: skip

    reply = client("0.3.0").diagnose(_bundle(run, "0.3.0", _layer(run)))
    assert reply.provenance.cached and reply.prompt_version == "0.3.0"
    assert reply.request_key == record["muse"]["request_key"] == CAMERA_FED_REPLY.stem
    assert [h.limiting_factor for h in reply.output.hypotheses] == (
        record["grounding"]["limiting_factors"]
    )
    with pytest.raises(MuseCacheMiss):
        client("0.3.1").diagnose(_bundle(run, "0.3.1", _layer(run)))
