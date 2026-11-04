"""Choosing a detector. No model, no GPU and no torch: backends are fakes, and the two questions
about what gets imported are asked of a fresh interpreter."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import types
from pathlib import Path
from typing import Any

import pytest

from cairoflow_perception.local import detector_agpl as A
from cairoflow_perception.local import detectors as D
from cairoflow_perception.local import neural as N
from cairoflow_perception.local import pipeline as P
from cairoflow_perception.local.scoring import DetectorContext

ROOT = Path(__file__).resolve().parents[1]
APACHE, AGPL = "dfine-l", "visdrone-yolo26s"
FILED = {"model": APACHE, "conf": 0.3, "low_conf": 0.2}


class FakeBackend:
    tool, version, device = "fake", "0", "cpu"
    label_map = N.COCO_VEHICLE_LABELS

    def __init__(self, licence: str, weights_sha256: str, device: str = "cpu") -> None:
        self.licence, self.weights_sha256, self.device = licence, weights_sha256, device

    def infer(self, images_rgb: Any, floor: float) -> list[N.RawBoxes]:
        return []

    def describe(self) -> dict[str, Any]:
        return {"repo": "fake"}


def context(tmp_path: Path, args: dict[str, str] | None = None) -> DetectorContext:
    (tmp_path / "data/cv").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data/cv/detector.json").write_text(json.dumps(FILED), "utf-8")
    return DetectorContext(tmp_path, tmp_path, None, [], args or {})  # type: ignore[arg-type]


def fresh_python(code: str, **env: str) -> subprocess.CompletedProcess[str]:
    paths = [str(ROOT / "packages" / p) for p in ("perception", "contracts", "evaluation")]
    clean = {k: v for k, v in os.environ.items() if k != D.ENV_DETECTOR}
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env={**clean, "PYTHONPATH": os.pathsep.join(paths), **env},
    )


@pytest.fixture
def apache_backend(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Stands in for `N.build_backend` and records what it was asked for."""
    asked: list[tuple[str, str]] = []

    def build(model: str, device: str) -> FakeBackend:
        asked.append((model, device))
        return FakeBackend("Apache-2.0", D.PROFILES[model].weights_sha256, device)

    monkeypatch.setattr(N, "build_backend", build)
    monkeypatch.delenv(D.ENV_DETECTOR, raising=False)
    return asked


# ---- which model -------------------------------------------------------------------------------


def test_the_file_decides_when_nothing_overrides_it() -> None:
    got = D.choose({}, {}, FILED)
    assert (got.model, got.conf, got.low_conf) == (APACHE, 0.3, 0.2)
    assert got.licence == "Apache-2.0"
    assert got.chosen_by == str(D.SETTINGS_FILE)


def test_the_environment_overrides_the_file_and_takes_that_models_own_scale() -> None:
    got = D.choose({}, {D.ENV_DETECTOR: AGPL}, FILED)
    assert got.model == AGPL
    assert got.conf == D.PROFILES[AGPL].conf != FILED["conf"]
    assert got.low_conf == D.PROFILES[AGPL].low_conf
    assert got.chosen_by == D.ENV_DETECTOR


def test_an_empty_environment_variable_is_not_a_choice() -> None:
    assert D.choose({}, {D.ENV_DETECTOR: ""}, FILED).model == APACHE


def test_an_argument_overrides_the_environment() -> None:
    got = D.choose({"model": APACHE, "conf": "0.5"}, {D.ENV_DETECTOR: AGPL}, FILED)
    assert (got.model, got.conf) == (APACHE, 0.5)


def test_low_conf_is_never_above_conf() -> None:
    assert D.choose({"conf": "0.1"}, {}, FILED).low_conf == 0.1


def test_an_unknown_model_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="unknown detector model 'yolo'"):
        D.choose({}, {D.ENV_DETECTOR: "yolo"}, FILED)


def test_nothing_chosen_is_an_error_and_not_a_silent_default() -> None:
    with pytest.raises(ValueError, match="no model chosen"):
        D.choose({}, {}, {})


def test_the_committed_default_is_apache_and_reaches_this_factory() -> None:
    settings = P.DetectorSettings.load(ROOT / D.SETTINGS_FILE)
    assert P.resolve_factory(settings.detector) is D.make_detector
    assert settings.requires_cuda
    choice = D.choose({}, {}, D.load_settings(ROOT))
    assert choice.licence == "Apache-2.0"
    assert D.PROFILES[choice.model].extra == "vision"


def test_the_tracker_keeps_every_box_a_model_reports() -> None:
    params = P.DetectorSettings.load(ROOT / D.SETTINGS_FILE).tracker_params()
    assert all(params.low_score <= p.low_conf for p in D.PROFILES.values())


def test_a_low_operating_point_does_not_become_a_low_start_threshold() -> None:
    params = P.DetectorSettings.load(ROOT / D.SETTINGS_FILE).tracker_params()
    assert params.high_score >= D.PROFILES[APACHE].conf > D.PROFILES[AGPL].conf


# ---- building ----------------------------------------------------------------------------------


def test_make_detector_reports_what_the_receipt_needs(
    tmp_path: Path, apache_backend: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(D, "cuda_available", lambda: True)
    detector = D.make_detector(context(tmp_path))
    assert apache_backend == [(APACHE, "cuda")]
    assert detector.licence == "Apache-2.0"
    assert detector.version == "0"
    assert detector.weights_sha256 == D.PROFILES[APACHE].weights_sha256
    assert detector.params.variant == "tiled2x"
    assert detector.params.conf == FILED["low_conf"]
    assert detector.describe()["chosen_by"] == str(D.SETTINGS_FILE)


def test_without_cuda_the_detector_falls_back_to_cpu(
    tmp_path: Path, apache_backend: list[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(D, "cuda_available", lambda: False)
    assert D.make_detector(context(tmp_path)).device == "cpu"
    assert apache_backend == [(APACHE, "cpu")]


def test_cpu_can_be_asked_for_with_a_gpu_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(D, "cuda_available", lambda: True)
    assert D.pick_device("cpu") == "cpu"


def test_a_view_field_passes_through_and_a_typo_does_not() -> None:
    choice = D.choose({}, {}, FILED)
    assert D.view_params(choice, {"nms_iou": "0.4"}).nms_iou == 0.4
    with pytest.raises(ValueError, match="unknown detector arguments"):
        D.view_params(choice, {"tile": "640"})


def test_a_missing_extra_says_which_one(monkeypatch: pytest.MonkeyPatch) -> None:
    def build(model: str, device: str) -> None:
        raise ModuleNotFoundError("No module named 'torch'")

    monkeypatch.setattr(N, "build_backend", build)
    with pytest.raises(D.DetectorUnavailable, match="--extra vision"):
        D.load_backend(APACHE, "cpu")


# ---- the AGPL boundary -------------------------------------------------------------------------


def test_importing_the_factory_imports_no_agpl_and_no_torch() -> None:
    code = (
        "import sys; import cairoflow_perception.local.detectors\n"
        "bad = {'cairoflow_perception.local.detector_agpl', 'ultralytics', 'torch'}\n"
        "print(sorted(bad & set(sys.modules)))"
    )
    done = fresh_python(code)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


def test_building_the_default_never_touches_the_agpl_adapter() -> None:
    code = (
        "import sys, pathlib\n"
        "from cairoflow_perception.local import detectors as D, neural as N\n"
        "from cairoflow_perception.local.scoring import DetectorContext\n"
        "class B:\n"
        "    tool = version = device = 'fake'; licence = 'Apache-2.0'; label_map = {}\n"
        "    weights_sha256 = D.PROFILES['dfine-l'].weights_sha256\n"
        "N.build_backend = lambda m, d: B()\n"
        f"repo = pathlib.Path({str(ROOT)!r})\n"
        "D.make_detector(DetectorContext(repo, repo, None, [], {}))\n"
        "print(D.AGPL_MODULE in sys.modules, 'ultralytics' in sys.modules)"
    )
    done = fresh_python(code)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "False False"


def test_choosing_the_agpl_model_loads_the_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[str] = []
    fake = types.ModuleType(D.AGPL_MODULE)

    def build_backend(device: str) -> FakeBackend:
        built.append(device)
        return FakeBackend("AGPL-3.0", D.PROFILES[AGPL].weights_sha256, device)

    fake.build_backend = build_backend  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, D.AGPL_MODULE, fake)
    monkeypatch.setenv(D.ENV_DETECTOR, AGPL)
    monkeypatch.setattr(D, "cuda_available", lambda: False)
    detector = D.make_detector(context(tmp_path))
    assert built == ["cpu"]
    assert detector.licence == "AGPL-3.0"
    assert detector.params.conf == D.PROFILES[AGPL].low_conf


def test_the_extras_stay_out_of_the_default_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    defaults = " ".join(project["dependencies"])
    for name in ("torch", "transformers", "ultralytics"):
        assert name not in defaults
    extras = project["optional-dependencies"]
    assert any(d.startswith("ultralytics") for d in extras["vision-agpl"])
    assert not any(d.startswith("ultralytics") for d in extras["vision"])


def test_notice_tells_the_agpl_consequence() -> None:
    notice = " ".join((ROOT / "NOTICE").read_text("utf-8").split())
    assert "makes that combined runtime subject to AGPL-3.0" in notice


# ---- pinned weights ----------------------------------------------------------------------------


def test_a_pinned_file_passes(tmp_path: Path) -> None:
    path = tmp_path / "best.pt"
    path.write_bytes(b"weights")
    pin = N.sha256_file(path)
    assert A.verify_weights(path, pin) == pin


def test_a_changed_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "best.pt"
    path.write_bytes(b"not the pinned file")
    with pytest.raises(N.WeightsRefused, match="not the pinned"):
        A.verify_weights(path)


def test_the_adapter_refuses_before_any_library_opens_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "best.pt"
    path.write_bytes(b"a pickle that must not be opened")
    monkeypatch.setattr(A, "fetch_weights", lambda: path)
    monkeypatch.setitem(sys.modules, "ultralytics", None)  # importing it would raise ImportError
    with pytest.raises(N.WeightsRefused):
        A.YoloBackend("cpu")


def test_the_factory_refuses_a_backend_whose_weights_differ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(N, "build_backend", lambda m, d: FakeBackend("Apache-2.0", "0" * 64))
    with pytest.raises(N.WeightsRefused, match="not the pinned"):
        D.load_backend(APACHE, "cpu")


def test_the_adapters_pin_is_the_profiles_pin() -> None:
    assert A.WEIGHTS_SHA256 == D.PROFILES[AGPL].weights_sha256
    assert A.LICENCE == D.PROFILES[AGPL].licence
    assert A.MODEL == AGPL
