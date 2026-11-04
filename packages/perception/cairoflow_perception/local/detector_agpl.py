"""The opt-in AGPL-3.0 detector: a VisDrone YOLO26s checkpoint through ultralytics.

Both are AGPL-3.0, and neither is a default. `detectors.py` is the only module that imports this
one, and only when this model is explicitly chosen (CAIROFLOW_DETECTOR=visdrone-yolo26s); running
with it makes that combined runtime subject to AGPL-3.0 (NOTICE). It sits behind the same
`Backend` interface as local/neural.py, so `NeuralDetector` gives it the same `tiled2x` view.

The weights are downloaded at run time and never committed. best.pt can only be loaded by
unpickling it, so two checks come first. Its SHA-256 must equal the pin, and a file that differs is
refused before any library reads it. Then it must pass torch's restricted unpickler with an
allowlist of exactly the globals the pinned file names, and the full load that follows runs the
same stream. The pinned file was also scanned on 2026-11-03, with nothing flagged.

ultralytics is told it is offline and may not install anything, which also keeps its telemetry off:
the file is already here by then, and nothing else needs the network.
"""

from __future__ import annotations

import builtins
import collections
import importlib
import importlib.metadata
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from cairoflow_perception.local.neural import (
    VISDRONE_VEHICLE_LABELS,
    RawBoxes,
    WeightsRefused,
    sha256_file,
)

MODEL = "visdrone-yolo26s"
LICENCE = "AGPL-3.0"
REPO = "dronefreak/visdrone-yolo26s"
REVISION = "dc042f4b188e76413f256b5ff69653f11a38a854"
WEIGHTS_FILE = "best.pt"
WEIGHTS_SHA256 = "98a66ac96e4470ab7cf118bf8c700fa65f7078a05f32578fca43a4c398c01879"
IMAGE_SIZE_PX = 640
MAX_DETECTIONS = 300

ALLOWED_GLOBALS = (
    ("torch.nn.modules.activation", "SiLU"),
    ("torch.nn.modules.batchnorm", "BatchNorm2d"),
    ("torch.nn.modules.container", "ModuleList"),
    ("torch.nn.modules.container", "Sequential"),
    ("torch.nn.modules.conv", "Conv2d"),
    ("torch.nn.modules.linear", "Identity"),
    ("torch.nn.modules.pooling", "MaxPool2d"),
    ("torch.nn.modules.upsampling", "Upsample"),
    ("ultralytics.nn.modules.block", "Attention"),
    ("ultralytics.nn.modules.block", "Bottleneck"),
    ("ultralytics.nn.modules.block", "C2PSA"),
    ("ultralytics.nn.modules.block", "C3k"),
    ("ultralytics.nn.modules.block", "C3k2"),
    ("ultralytics.nn.modules.block", "PSABlock"),
    ("ultralytics.nn.modules.block", "SPPF"),
    ("ultralytics.nn.modules.conv", "Concat"),
    ("ultralytics.nn.modules.conv", "Conv"),
    ("ultralytics.nn.modules.conv", "DWConv"),
    ("ultralytics.nn.modules.head", "Detect"),
    ("ultralytics.nn.tasks", "DetectionModel"),
)


def verify_weights(path: Path, expected_sha256: str = WEIGHTS_SHA256) -> str:
    """The file's SHA-256 if it is the pinned file; raises `WeightsRefused` if it is not."""
    found = sha256_file(path)
    if found != expected_sha256:
        raise WeightsRefused(f"{path} has SHA-256 {found}, not the pinned {expected_sha256}")
    return found


def fetch_weights() -> Path:
    """The pinned revision of the checkpoint, from the Hugging Face cache or the Hub."""
    from huggingface_hub import hf_hub_download  # noqa: PLC0415

    return Path(hf_hub_download(REPO, WEIGHTS_FILE, revision=REVISION))


def restricted_check(path: Path) -> dict[str, Any]:
    """Unpickle under the allowlist. A stream that passes can only have built those objects."""
    import torch  # noqa: PLC0415

    allowed: list[Any] = [getattr(importlib.import_module(m), n) for m, n in ALLOWED_GLOBALS]
    allowed += [builtins.set, collections.OrderedDict, torch.Size]
    with torch.serialization.safe_globals(allowed):
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    return {
        "restricted_unpickle": "passed",
        "allowlisted_globals": len(allowed),
        "checkpoint_keys": sorted(str(k) for k in checkpoint),
    }


class YoloBackend:
    """The checkpoint through ultralytics' `predict`, at its own letterbox, in float32."""

    licence = LICENCE
    label_map = VISDRONE_VEHICLE_LABELS
    tool = REPO

    def __init__(self, device: str = "cuda") -> None:
        self.weights_path = fetch_weights()
        self.weights_sha256 = verify_weights(self.weights_path)  # before any library opens it
        os.environ["YOLO_AUTOINSTALL"] = "false"
        os.environ["YOLO_OFFLINE"] = "true"
        import torch  # noqa: PLC0415
        from ultralytics import YOLO  # noqa: PLC0415

        self._torch = torch
        self.device = device
        self._predict_device: int | str = 0 if device == "cuda" else device
        self.version = importlib.metadata.version("ultralytics")
        self.safety = restricted_check(self.weights_path)
        self.model = YOLO(str(self.weights_path), task="detect")
        self.names = dict(self.model.names)

    def infer(self, images_rgb: Sequence[np.ndarray], floor: float) -> list[RawBoxes]:
        bgr = [np.ascontiguousarray(i[..., ::-1]) for i in images_rgb]
        results = self.model.predict(
            bgr,
            conf=floor,
            imgsz=IMAGE_SIZE_PX,
            device=self._predict_device,
            verbose=False,
            max_det=MAX_DETECTIONS,
        )
        return [
            RawBoxes(
                r.boxes.xyxy.float().cpu().numpy(),
                r.boxes.conf.float().cpu().numpy(),
                [self.names[int(c)] for c in r.boxes.cls.tolist()],
            )
            for r in results
        ]

    def describe(self) -> dict[str, Any]:
        return {
            "library": f"ultralytics {self.version}",
            "torch": self._torch.__version__,
            "repo": REPO,
            "revision": REVISION,
            "weights_file": WEIGHTS_FILE,
            "weights_sha256": self.weights_sha256,
            "licence": self.licence,
            "precision": "float32",
            "model_input": f"imgsz {IMAGE_SIZE_PX}, ultralytics letterbox (aspect ratio kept)",
            "label_map": dict(self.label_map),
            "class_names": self.names,
            "load_safety": self.safety,
        }


def build_backend(device: str = "cuda") -> YoloBackend:
    return YoloBackend(device)
