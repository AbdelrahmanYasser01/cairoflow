"""Pretrained detectors for camera 2 behind the harness's `detect(frame_bgr)` interface.

Two views of a frame, the same for every model:

- `native`: the 640 x 480 frame with the glass band painted over, handed to the model's own
  preprocessing (D-FINE and RT-DETRv2 stretch it to 640 x 640, RF-DETR Medium to 576 x 576);
- `tiled2x`: the usable rows upscaled 2x (bicubic) and cut into overlapping tiles, so a 25 px car
  reaches the model at about 50 px. Overlap is wider than any vehicle, so a vehicle is whole in
  some tile, and a box touching an inner tile edge is dropped as a fragment of one.

Only boxes whose centre lies in the usable rows survive, and class-agnostic NMS removes what
tiles, and models that score one query under two classes, report twice. Greedy NMS commutes with
a score threshold, so a run at a low floor gives every higher confidence exactly by filtering
(tests/test_cv_neural.py).

The class map is by name, never by index: Hugging Face's COCO checkpoints say "motorbike", and
RF-DETR's ids are sparse COCO category ids (3 = car), not the 80-class indices.

This module imports torch and the model libraries only when a backend is built. The AGPL
candidate's backend is deliberately not here: it is local/detector_agpl.py, which only
local/detectors.py may load, and only when it is explicitly chosen.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import numpy as np

from cairoflow_perception.local.scoring import USABLE_V_PX, Detection, DetectorContext

SCORE_FLOOR = 0.02  # what a backend is asked for; the confidence sweep filters above it
PAINT_GREY = 114  # the neutral grey detectors letterbox with

COCO_VEHICLE_LABELS: Mapping[str, str] = {
    "car": "car",
    "truck": "truck",
    "bus": "bus",
    "motorcycle": "motorcycle",
    "motorbike": "motorcycle",  # the name in the Hugging Face checkpoints' id2label
}
# VisDrone's motorised classes. A tricycle does not map to a class this project reports.
VISDRONE_VEHICLE_LABELS: Mapping[str, str] = {
    "car": "car",
    "van": "car",
    "truck": "truck",
    "bus": "bus",
    "motor": "motorcycle",
    "tricycle": "vehicle",
    "awning-tricycle": "vehicle",
}

MODELS: Mapping[str, Mapping[str, str]] = {
    "dfine-l": {
        "kind": "hf",
        "repo": "ustc-community/dfine-large-obj2coco-e25",
        "revision": "c89de47153e338c07cddc81fc21a776b97cbf75e",
        "licence": "Apache-2.0",
    },
    "rtdetrv2-r50": {
        "kind": "hf",
        "repo": "PekingU/rtdetr_v2_r50vd",
        "revision": "282494075698cab9faa1096ae26856890030c817",
        "licence": "Apache-2.0",
    },
    "rfdetr-m": {"kind": "rfdetr", "repo": "rfdetr:RFDETRMedium", "licence": "Apache-2.0"},
}


class RawBoxes(NamedTuple):
    """A model's output for one input image: xyxy in that image's pixels, a score, a class name."""

    boxes: np.ndarray  # (n, 4) float32
    scores: np.ndarray  # (n,) float32
    names: list[str]


class Backend(Protocol):
    """One model. `infer` takes RGB uint8 arrays and returns one `RawBoxes` for each."""

    tool: str
    version: str
    licence: str
    weights_sha256: str | None
    device: str
    label_map: Mapping[str, str]

    def infer(self, images_rgb: Sequence[np.ndarray], floor: float) -> list[RawBoxes]: ...

    def describe(self) -> dict[str, Any]: ...


class WeightsRefused(RuntimeError):
    """A weights file is not the pinned one, so it must not be used."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


# ---- geometry -----------------------------------------------------------------------------


def tile_starts(extent_px: int, tile_px: int, min_overlap_px: int) -> list[int]:
    """Evenly spread tile origins covering [0, extent): the fewest tiles that overlap by at least
    `min_overlap_px`, the first flush with 0 and the last with the end."""
    if extent_px <= tile_px:
        return [0]
    stride = tile_px - min_overlap_px
    if stride <= 0:
        raise ValueError(f"overlap {min_overlap_px} leaves no stride in a {tile_px} px tile")
    count = -(-(extent_px - min_overlap_px) // stride)
    last = extent_px - tile_px
    return [round(i * last / (count - 1)) for i in range(count)]


def nms_keep(boxes: np.ndarray, scores: np.ndarray, iou: float) -> np.ndarray:
    """Greedy class-agnostic NMS. Indices kept, in descending score; ties by input order."""
    order = np.argsort(-scores, kind="stable")
    x1, y1, x2, y2 = (boxes[:, i].astype(np.float64) for i in range(4))
    area = np.maximum(x2 - x1, 0) * np.maximum(y2 - y1, 0)
    keep = []
    while order.size:
        i, rest = order[0], order[1:]
        keep.append(int(i))
        w = np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest])
        h = np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest])
        inter = np.maximum(w, 0) * np.maximum(h, 0)
        union = area[i] + area[rest] - inter
        overlap = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
        order = rest[overlap < iou]
    return np.array(keep, dtype=np.int64)


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU of every box in `a` with every box in `b`, shape (len(a), len(b))."""
    a64, b64 = a.astype(np.float64)[:, None, :], b.astype(np.float64)[None, :, :]
    w = np.minimum(a64[..., 2], b64[..., 2]) - np.maximum(a64[..., 0], b64[..., 0])
    h = np.minimum(a64[..., 3], b64[..., 3]) - np.maximum(a64[..., 1], b64[..., 1])
    inter = np.maximum(w, 0) * np.maximum(h, 0)
    area_a = (a64[..., 2] - a64[..., 0]) * (a64[..., 3] - a64[..., 1])
    area_b = (b64[..., 2] - b64[..., 0]) * (b64[..., 3] - b64[..., 1])
    union = area_a + area_b - inter
    return np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)


def fuse_keep(primary: np.ndarray, secondary: np.ndarray, iou: float) -> np.ndarray:
    """The union of two box sets, the primary kept whole: indices of the secondary boxes that
    overlap no primary box by `iou`, then NMS among themselves. Scores of different detectors are
    not comparable, so priority, not score, decides."""
    if len(secondary) == 0:
        return np.zeros(0, dtype=np.int64)
    if len(primary) == 0:
        fresh = np.arange(len(secondary))
    else:
        fresh = np.flatnonzero(iou_matrix(secondary, primary).max(axis=1) < iou)
    if fresh.size == 0:
        return fresh.astype(np.int64)
    kept = nms_keep(secondary[fresh], np.ones(fresh.size, dtype=np.float32), iou)
    return np.sort(fresh[kept]).astype(np.int64)


def paint_strips(frame_bgr: np.ndarray, top_v_px: int, bottom_v_px: int) -> np.ndarray:
    """A copy with the rows outside [top, bottom) painted grey, so a model sees no glass band."""
    out = frame_bgr.copy()
    out[:top_v_px] = PAINT_GREY
    out[bottom_v_px:] = PAINT_GREY
    return out


# ---- the detector -------------------------------------------------------------------------


@dataclass(frozen=True)
class ViewParams:
    """How a frame reaches a model. Lengths in pixels of the image they are applied to."""

    variant: str = "native"  # "native" or "tiled2x"
    upscale: int = 2
    tile_px: int = 640
    min_overlap_px: int = 192  # upscaled: longer than a bus (about 66 source px)
    edge_margin_px: float = 2.0  # upscaled: a box this close to an inner tile edge is a fragment
    interpolation: str = "bicubic"
    top_v_px: int = 0  # camera 2's top rows hold real traffic and no burned-in text
    bottom_v_px: int = USABLE_V_PX
    nms_iou: float = 0.5
    conf: float = SCORE_FLOOR  # `detect` reports boxes at or above this


class NeuralDetector:
    """One backend, one view. `raw` gives vehicles and everything else, unfiltered by confidence;
    `detect` is the harness and pipeline interface."""

    def __init__(self, backend: Backend, params: ViewParams | None = None) -> None:
        import cv2  # noqa: PLC0415 - optional at import time

        self._cv2 = cv2
        self.backend = backend
        self.params = params or ViewParams()
        if self.params.variant not in ("native", "tiled2x"):
            raise ValueError(f"unknown variant {self.params.variant!r}")
        self.tool = backend.tool
        self.version = backend.version
        self.licence = backend.licence
        self.weights_sha256 = backend.weights_sha256
        self.device = backend.device

    def _views(self, frame_bgr: np.ndarray) -> list[tuple[np.ndarray, int, int, int]]:
        """(RGB image, x origin, y origin, scale) of each image the model is shown."""
        p = self.params
        painted = paint_strips(frame_bgr, p.top_v_px, p.bottom_v_px)
        rgb = np.ascontiguousarray(painted[..., ::-1])
        if p.variant == "native":
            return [(rgb, 0, 0, 1)]
        big = self._cv2.resize(
            rgb[: p.bottom_v_px],
            None,
            fx=p.upscale,
            fy=p.upscale,
            interpolation=self._cv2.INTER_CUBIC,
        )
        height, width = big.shape[:2]
        return [
            (np.ascontiguousarray(big[y : y + p.tile_px, x : x + p.tile_px]), x, y, p.upscale)
            for y in tile_starts(height, p.tile_px, p.min_overlap_px)
            for x in tile_starts(width, p.tile_px, p.min_overlap_px)
        ]

    def _inner_edge_fragment(
        self, boxes: np.ndarray, x0: int, y0: int, big_w: int, big_h: int
    ) -> np.ndarray:
        """True for boxes touching a tile edge that is not the edge of the whole image."""
        p = self.params
        m, t = p.edge_margin_px, p.tile_px
        left = (boxes[:, 0] <= m) & (x0 > 0)
        top = (boxes[:, 1] <= m) & (y0 > 0)
        right = (boxes[:, 2] >= t - m) & (x0 + t < big_w)
        bottom = (boxes[:, 3] >= t - m) & (y0 + t < big_h)
        return left | top | right | bottom

    def raw(self, frame_bgr: np.ndarray) -> tuple[RawBoxes, RawBoxes]:
        """(vehicles with mapped labels after NMS, every other class) in source pixels, down to
        the score floor."""
        p = self.params
        views = self._views(frame_bgr)
        found = self.backend.infer([v[0] for v in views], SCORE_FLOOR)
        big_h = p.bottom_v_px * p.upscale
        big_w = frame_bgr.shape[1] * p.upscale
        boxes, scores, names = [], [], []
        for (_image, x0, y0, scale), raw in zip(views, found, strict=True):
            b = raw.boxes.astype(np.float32).reshape(-1, 4)
            keep = np.ones(len(b), dtype=bool)
            if p.variant == "tiled2x":
                keep = ~self._inner_edge_fragment(b, x0, y0, big_w, big_h)
            shifted = (b + np.array([x0, y0, x0, y0], dtype=np.float32)) / scale
            boxes.append(shifted[keep])
            scores.append(raw.scores.astype(np.float32)[keep])
            names += [n for n, k in zip(raw.names, keep, strict=True) if k]
        all_boxes = np.concatenate(boxes) if boxes else np.zeros((0, 4), np.float32)
        all_scores = np.concatenate(scores) if scores else np.zeros(0, np.float32)
        centre_v = (all_boxes[:, 1] + all_boxes[:, 3]) / 2
        usable = (centre_v >= p.top_v_px) & (centre_v < p.bottom_v_px)
        mapped = [self.backend.label_map.get(n) for n in names]
        vehicle = np.array([m is not None for m in mapped], dtype=bool) & usable
        other = ~np.array([m is not None for m in mapped], dtype=bool) & usable
        idx = np.flatnonzero(vehicle)
        idx = idx[nms_keep(all_boxes[idx], all_scores[idx], p.nms_iou)] if idx.size else idx
        rest = np.flatnonzero(other)
        return (
            RawBoxes(all_boxes[idx], all_scores[idx], [mapped[i] for i in idx]),
            RawBoxes(all_boxes[rest], all_scores[rest], [names[i] for i in rest]),
        )

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        vehicles, _others = self.raw(frame_bgr)
        return [
            Detection(float(b[0]), float(b[1]), float(b[2]), float(b[3]), float(s), name)
            for b, s, name in zip(vehicles.boxes, vehicles.scores, vehicles.names, strict=True)
            if s >= self.params.conf
        ]

    def describe(self) -> dict[str, Any]:
        return {**self.backend.describe(), "view": asdict(self.params)}


# ---- backends -----------------------------------------------------------------------------


class HFBackend:
    """D-FINE and RT-DETRv2 through transformers, in float32, with the model's own processor."""

    licence = "Apache-2.0"
    label_map = COCO_VEHICLE_LABELS

    def __init__(self, repo: str, revision: str, device: str = "cuda") -> None:
        import torch  # noqa: PLC0415
        from huggingface_hub import hf_hub_download  # noqa: PLC0415
        from transformers import (  # noqa: PLC0415
            AutoImageProcessor,
            AutoModelForObjectDetection,
        )

        self._torch = torch
        self.repo, self.revision, self.device = repo, revision, device
        self.tool = repo
        self.version = importlib.metadata.version("transformers")
        self.processor = AutoImageProcessor.from_pretrained(repo, revision=revision)
        self.model = AutoModelForObjectDetection.from_pretrained(repo, revision=revision)
        self.model = self.model.to(device).eval()
        self.weights_path = Path(hf_hub_download(repo, "model.safetensors", revision=revision))
        self.weights_sha256 = sha256_file(self.weights_path)

    def infer(self, images_rgb: Sequence[np.ndarray], floor: float) -> list[RawBoxes]:
        torch = self._torch
        inputs = self.processor(images=list(images_rgb), return_tensors="pt").to(self.device)
        with torch.inference_mode():
            outputs = self.model(**inputs)
        sizes = torch.tensor([i.shape[:2] for i in images_rgb], device=self.device)
        results = self.processor.post_process_object_detection(
            outputs, threshold=floor, target_sizes=sizes
        )
        names = self.model.config.id2label
        return [
            RawBoxes(
                r["boxes"].float().cpu().numpy(),
                r["scores"].float().cpu().numpy(),
                [names[int(i)] for i in r["labels"].tolist()],
            )
            for r in results
        ]

    def describe(self) -> dict[str, Any]:
        size = self.processor.size
        return {
            "library": f"transformers {self.version}",
            "torch": self._torch.__version__,
            "repo": self.repo,
            "revision": self.revision,
            "weights_file": self.weights_path.name,
            "weights_sha256": self.weights_sha256,
            "licence": self.licence,
            "precision": "float32",
            "model_input": f"{size.get('height')} x {size.get('width')}, the processor's resize "
            "(aspect ratio not kept)",
            "label_map": dict(self.label_map),
        }


class RFDetrBackend:
    """RF-DETR Medium through rfdetr's `predict`, at the model's own square resolution."""

    licence = "Apache-2.0"
    label_map = COCO_VEHICLE_LABELS
    tool = "rfdetr:RFDETRMedium"

    def __init__(self, device: str = "cuda") -> None:
        import torch  # noqa: PLC0415
        from rfdetr import RFDETRMedium  # noqa: PLC0415
        from rfdetr.assets.coco_classes import COCO_CLASSES  # noqa: PLC0415

        self._torch = torch
        self.device = device
        self.version = importlib.metadata.version("rfdetr")
        self._names = COCO_CLASSES
        self.model = RFDETRMedium()
        self.resolution = int(self.model.model_config.resolution)
        self.weights_path = Path.home() / ".roboflow/models/rf-detr-medium.pth"
        self.weights_sha256 = sha256_file(self.weights_path)

    def infer(self, images_rgb: Sequence[np.ndarray], floor: float) -> list[RawBoxes]:
        out = self.model.predict(list(images_rgb), threshold=floor, include_source_image=False)
        found = out if isinstance(out, list) else [out]
        return [
            RawBoxes(
                np.asarray(d.xyxy, dtype=np.float32).reshape(-1, 4),
                np.asarray(d.confidence, dtype=np.float32),
                [self._names.get(int(c), f"coco-id-{int(c)}") for c in d.class_id],
            )
            for d in found
        ]

    def describe(self) -> dict[str, Any]:
        return {
            "library": f"rfdetr {self.version}",
            "torch": self._torch.__version__,
            "repo": self.tool,
            "weights_file": self.weights_path.name,
            "weights_sha256": self.weights_sha256,
            "licence": self.licence,
            "precision": "float32",
            "model_input": f"{self.resolution} x {self.resolution}, rfdetr's resize "
            "(aspect ratio not kept)",
            "label_map": dict(self.label_map),
            "class_ids": "COCO category ids, 1-90 and sparse, named by rfdetr's COCO_CLASSES",
        }


def build_backend(name: str, device: str = "cuda") -> Backend:
    spec = MODELS.get(name)
    if spec is None:
        raise ValueError(f"unknown model {name!r}; choose from {sorted(MODELS)}")
    if spec["kind"] == "hf":
        return HFBackend(spec["repo"], spec["revision"], device)
    return RFDetrBackend(device)


def make_detector(context: DetectorContext) -> NeuralDetector:
    """Factory for scripts/cv_bakeoff.py and data/cv/detector.json. Arguments: model (a key of
    MODELS), device, conf, and any other `ViewParams` field."""
    args = dict(context.args)
    backend = build_backend(args.pop("model", "dfine-l"), args.pop("device", "cuda"))
    fields = ViewParams.__dataclass_fields__
    unknown = set(args) - set(fields)
    if unknown:
        raise ValueError(f"unknown detector arguments {sorted(unknown)}")
    typed = {k: type(getattr(ViewParams(), k))(v) for k, v in args.items()}
    return NeuralDetector(backend, replace(ViewParams(), **typed))
