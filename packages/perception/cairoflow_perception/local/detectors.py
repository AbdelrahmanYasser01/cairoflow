"""Which neural detector a run uses, and the only module allowed to load the AGPL adapter.

data/cv/detector.json names this module as the pipeline's factory and picks the model:

    {"detector": "detectors",       `pipeline.resolve_factory` turns this into `make_detector`
     "model": "dfine-l",            a key of PROFILES
     "conf": 0.27,                  the operating point the bake-off scored this model at
     "low_conf": 0.1,               `detect` reports boxes down to here, for the tracker's
                                    second stage; never above `conf`
     "requires_cuda": true,         `pipeline.plan_device` reads these two
     "cpu_min_frame_stride": 3,
     "tracker": {"high_score": 0.27, "low_score": 0.03}}

The committed model is Apache-2.0, so a fresh clone with only the `vision` extra works and nothing
AGPL loads by accident. Later wins: the file, then the environment variable CAIROFLOW_DETECTOR, then
`--detector-arg model=...`. The file's `conf` and `low_conf` belong to the file's model: choosing
another one takes that model's own values from PROFILES. Without CUDA the detector runs on CPU.

`conf` is a detector's bake-off operating point, and it does not filter by itself. `detect` reports
down to `low_conf`, and which boxes may start a track is the tracker's `high_score`. A low `conf` is
not a low start threshold: the winner's is 0.03, where painted arrows and road marks become tracks
if they may start one. The pipeline reads one `tracker` block for every model, so its values must
suit both. A `high_score` of 0.27 is D-FINE's operating point, and the winner matches the same
vehicles within one from 0.15 to 0.27 on dev blocks 4 and 14; at 0.15 D-FINE makes 254 tracks for
the 102 vehicles in view on block 14. `low_score` must not sit above a model's `low_conf`, or the
tracker drops what the detector reports.

The neural models need an extra (`uv sync --extra vision`), so nothing heavy is imported here.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from cairoflow_perception.local import neural as N
from cairoflow_perception.local.pipeline import cuda_available
from cairoflow_perception.local.scoring import DetectorContext

ENV_DETECTOR = "CAIROFLOW_DETECTOR"
SETTINGS_FILE = Path("data/cv/detector.json")
AGPL_MODULE = "cairoflow_perception.local.detector_agpl"
RESERVED_ARGS = frozenset({"model", "device", "conf", "low_conf"})


class DetectorUnavailable(RuntimeError):
    """The chosen model's libraries are not installed in this environment."""


@dataclass(frozen=True)
class Profile:
    """A selectable model. `conf` and `low_conf` are its own scale, in 0..1."""

    licence: str
    extra: str  # the pyproject extra that installs its libraries
    conf: float
    low_conf: float
    weights_sha256: str


PROFILES: Mapping[str, Profile] = {
    "dfine-l": Profile(
        licence="Apache-2.0",
        extra="vision",
        conf=0.27,  # experiments/records/cv-bakeoff-20261103T235327Z, best Apache
        low_conf=0.1,
        weights_sha256="82508394c2070c7d82c286b78f8b4b42dd64b5a3144578427c6e67b07b9f8154",
    ),
    "visdrone-yolo26s": Profile(
        licence="AGPL-3.0",
        extra="vision-agpl",
        conf=0.03,  # the same record, the winner
        low_conf=0.03,
        weights_sha256="98a66ac96e4470ab7cf118bf8c700fa65f7078a05f32578fca43a4c398c01879",
    ),
}


@dataclass(frozen=True)
class Choice:
    """What was picked and what picked it, for the stage receipt."""

    model: str
    licence: str
    conf: float
    low_conf: float
    chosen_by: str


def load_settings(repo: Path) -> dict[str, Any]:
    """data/cv/detector.json, or nothing if the file is not there."""
    path = repo / SETTINGS_FILE
    return json.loads(path.read_text("utf-8")) if path.is_file() else {}


def choose(
    args: Mapping[str, str], environ: Mapping[str, str], settings: Mapping[str, Any]
) -> Choice:
    """The model and its confidences. Pure: everything it depends on is an argument."""
    filed = settings.get("model")
    if "model" in args:
        model, chosen_by = args["model"], "--detector-arg model"
    elif environ.get(ENV_DETECTOR):
        model, chosen_by = environ[ENV_DETECTOR], ENV_DETECTOR
    elif filed:
        model, chosen_by = str(filed), str(SETTINGS_FILE)
    else:
        raise ValueError(f"no model chosen: set `model` in {SETTINGS_FILE} or {ENV_DETECTOR}")
    profile = PROFILES.get(model)
    if profile is None:
        raise ValueError(f"unknown detector model {model!r}; choose from {sorted(PROFILES)}")
    own = settings if model == filed else {}
    conf = float(args.get("conf", own.get("conf", profile.conf)))
    low_conf = float(args.get("low_conf", own.get("low_conf", profile.low_conf)))
    return Choice(model, profile.licence, conf, min(low_conf, conf), chosen_by)


def pick_device(asked: str | None) -> str:
    """CUDA if torch can see a GPU, otherwise CPU, whatever was asked for."""
    return (asked or "cuda") if asked != "cpu" and cuda_available() else "cpu"


def load_backend(model: str, device: str) -> N.Backend:
    """The model's backend, with its weights checked against the pin. This is the one place that
    imports the AGPL adapter."""
    profile = PROFILES[model]
    try:
        if profile.licence == "AGPL-3.0":
            backend = importlib.import_module(AGPL_MODULE).build_backend(device)
        else:
            backend = N.build_backend(model, device)
    except ImportError as missing:
        raise DetectorUnavailable(
            f"{model} needs the {profile.extra} extra, which this environment does not have "
            f"({missing}). Build one outside the repository, because `uv sync` strips extras from "
            "the project's own .venv: UV_PROJECT_ENVIRONMENT=$HOME/.venvs/cairoflow-vision "
            f"uv sync --frozen --extra {profile.extra}"
        ) from missing
    if backend.weights_sha256 != profile.weights_sha256:
        raise N.WeightsRefused(
            f"{model} weights have SHA-256 {backend.weights_sha256}, "
            f"not the pinned {profile.weights_sha256}"
        )
    return backend


class ChosenDetector(N.NeuralDetector):
    """A `NeuralDetector` that also says how it was picked. Its `tool`, `version`, `licence`,
    `weights_sha256` and `device` are what the pipeline's stage receipt records."""

    def __init__(self, backend: N.Backend, params: N.ViewParams, choice: Choice) -> None:
        super().__init__(backend, params)
        self.choice = choice

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), **asdict(self.choice)}


def view_params(choice: Choice, args: Mapping[str, str]) -> N.ViewParams:
    """The tiled view every chosen model uses, with `detect`'s floor at `low_conf`."""
    extra = {k: v for k, v in args.items() if k not in RESERVED_ARGS}
    defaults = N.ViewParams(variant="tiled2x", conf=choice.low_conf)
    unknown = set(extra) - set(N.ViewParams.__dataclass_fields__)
    if unknown:
        raise ValueError(f"unknown detector arguments {sorted(unknown)}")
    return replace(defaults, **{k: type(getattr(defaults, k))(v) for k, v in extra.items()})


def make_detector(context: DetectorContext) -> ChosenDetector:
    """Factory for the pipeline: `{"detector": "detectors"}` in data/cv/detector.json."""
    choice = choose(context.args, os.environ, load_settings(context.repo))
    backend = load_backend(choice.model, pick_device(context.args.get("device")))
    return ChosenDetector(backend, view_params(choice, context.args), choice)
