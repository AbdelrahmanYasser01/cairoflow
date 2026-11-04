"""Geometry, class maps and merging of the neural adapters. No model and no GPU is needed."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cairoflow_perception.local import neural as N


def box(x1: float, y1: float, x2: float, y2: float) -> list[float]:
    return [x1, y1, x2, y2]


class FakeBackend:
    """Returns the same boxes for every input image, whatever it is."""

    tool, version, licence, weights_sha256, device = "fake", "0", "none", None, "cpu"
    label_map = N.COCO_VEHICLE_LABELS

    def __init__(self, raw: N.RawBoxes) -> None:
        self.raw = raw
        self.seen: list[np.ndarray] = []

    def infer(self, images_rgb: Sequence[np.ndarray], floor: float) -> list[N.RawBoxes]:
        self.seen += list(images_rgb)
        return [self.raw for _ in images_rgb]

    def describe(self) -> dict[str, Any]:
        return {}


def raw(boxes: list[list[float]], scores: list[float], names: list[str]) -> N.RawBoxes:
    return N.RawBoxes(
        np.array(boxes, np.float32).reshape(-1, 4), np.array(scores, np.float32), names
    )


FRAME = np.zeros((480, 640, 3), np.uint8)


def test_tiles_cover_the_extent_with_the_asked_overlap() -> None:
    starts = N.tile_starts(1280, 640, 192)
    assert starts[0] == 0
    assert starts[-1] == 1280 - 640
    assert all(b - a <= 640 - 192 for a, b in zip(starts, starts[1:], strict=False))


def test_camera_two_gives_six_tiles_of_the_usable_rows() -> None:
    assert N.tile_starts(1280, 640, 192) == [0, 320, 640]
    assert N.tile_starts(820, 640, 192) == [0, 180]


def test_a_short_extent_is_one_tile() -> None:
    assert N.tile_starts(600, 640, 192) == [0]


def test_an_overlap_as_wide_as_the_tile_is_refused() -> None:
    with pytest.raises(ValueError, match="stride"):
        N.tile_starts(1280, 640, 640)


def test_nms_drops_the_lower_score_of_a_duplicate() -> None:
    boxes = np.array([box(0, 0, 10, 10), box(0, 0, 10, 9), box(50, 50, 60, 60)], np.float32)
    kept = N.nms_keep(boxes, np.array([0.5, 0.9, 0.4], np.float32), 0.5)
    assert kept.tolist() == [1, 2]


def test_nms_commutes_with_a_score_threshold() -> None:
    """Why one run at the floor gives every confidence exactly."""
    rng = np.random.default_rng(1)
    xy = rng.uniform(0, 100, (200, 2)).astype(np.float32)
    boxes = np.hstack([xy, xy + rng.uniform(8, 30, (200, 2)).astype(np.float32)])
    scores = rng.uniform(0, 1, 200).astype(np.float32)
    kept = N.nms_keep(boxes, scores, 0.5)
    for threshold in (0.1, 0.25, 0.4, 0.7):
        filtered_after = set(kept[scores[kept] >= threshold].tolist())
        above = np.flatnonzero(scores >= threshold)
        filtered_before = set(above[N.nms_keep(boxes[above], scores[above], 0.5)].tolist())
        assert filtered_after == filtered_before


def test_hugging_face_motorbike_is_a_motorcycle() -> None:
    assert N.COCO_VEHICLE_LABELS["motorbike"] == "motorcycle"
    assert N.COCO_VEHICLE_LABELS["motorcycle"] == "motorcycle"


@pytest.mark.parametrize("name", ["person", "bicycle", "boat", "train", "traffic light"])
def test_other_coco_classes_are_not_vehicles(name: str) -> None:
    assert name not in N.COCO_VEHICLE_LABELS


@pytest.mark.parametrize("name", ["pedestrian", "people", "bicycle"])
def test_visdrone_people_and_bicycles_are_not_vehicles(name: str) -> None:
    assert name not in N.VISDRONE_VEHICLE_LABELS


def test_painting_leaves_the_rows_between_and_the_input_alone() -> None:
    frame = np.full((480, 640, 3), 7, np.uint8)
    painted = N.paint_strips(frame, 10, 410)
    assert (painted[:10] == N.PAINT_GREY).all()
    assert (painted[410:] == N.PAINT_GREY).all()
    assert (painted[10:410] == 7).all()
    assert (frame == 7).all()


def test_the_native_view_paints_the_band_and_keeps_the_frame_size() -> None:
    backend = FakeBackend(raw([], [], []))
    N.NeuralDetector(backend).raw(np.full((480, 640, 3), 7, np.uint8))
    (image,) = backend.seen
    assert image.shape == (480, 640, 3)
    assert (image[410:] == N.PAINT_GREY).all()
    assert (image[:410] == 7).all()


def test_the_tiled_view_is_six_tiles_of_the_upscaled_usable_rows() -> None:
    backend = FakeBackend(raw([], [], []))
    N.NeuralDetector(backend, N.ViewParams(variant="tiled2x")).raw(FRAME)
    assert [i.shape for i in backend.seen] == [(640, 640, 3)] * 6


def test_a_tile_box_returns_to_source_pixels_once_after_merging() -> None:
    """The same image box in every tile lands in six places; the tile origin and scale decide
    where."""
    backend = FakeBackend(raw([box(100, 100, 150, 130)], [0.9], ["car"]))
    vehicles, _ = N.NeuralDetector(backend, N.ViewParams(variant="tiled2x")).raw(FRAME)
    got = {tuple(b.round(1).tolist()) for b in vehicles.boxes}
    assert (50.0, 50.0, 75.0, 65.0) in got  # tile (0, 0)
    assert (210.0, 50.0, 235.0, 65.0) in got  # tile x0 = 320
    assert (50.0, 140.0, 75.0, 155.0) in got  # tile y0 = 180


def test_a_box_on_an_inner_tile_edge_is_a_fragment_but_an_image_edge_is_not() -> None:
    backend = FakeBackend(raw([box(0, 300, 40, 330), box(600, 300, 640, 330)], [0.9, 0.8],
                              ["car", "car"]))  # fmt: skip
    vehicles, _ = N.NeuralDetector(backend, N.ViewParams(variant="tiled2x")).raw(FRAME)
    got = {tuple(b.round(1).tolist()) for b in vehicles.boxes}
    assert (0.0, 150.0, 20.0, 165.0) in got  # left edge of the leftmost tile: the image's own
    assert (300.0, 150.0, 320.0, 165.0) not in got  # right edge of the leftmost tile: inner
    assert (620.0, 150.0, 640.0, 165.0) in got  # right edge of the rightmost tile: the image's


def test_duplicates_across_labels_collapse_to_one_vehicle() -> None:
    backend = FakeBackend(
        raw([box(10, 10, 40, 25), box(10, 10, 40, 25)], [0.8, 0.3], ["car", "truck"])
    )
    vehicles, _ = N.NeuralDetector(backend).raw(FRAME)
    assert vehicles.names == ["car"]


def test_a_box_centred_in_the_band_is_dropped() -> None:
    backend = FakeBackend(raw([box(10, 412, 40, 430), box(10, 380, 40, 400)], [0.9, 0.8],
                              ["car", "car"]))  # fmt: skip
    vehicles, _ = N.NeuralDetector(backend).raw(FRAME)
    assert vehicles.boxes.tolist() == [[10.0, 380.0, 40.0, 400.0]]


def test_non_vehicles_are_kept_apart_and_labels_are_mapped() -> None:
    backend = FakeBackend(raw([box(10, 10, 40, 25), box(100, 10, 130, 25)], [0.9, 0.8],
                              ["motorbike", "boat"]))  # fmt: skip
    vehicles, others = N.NeuralDetector(backend).raw(FRAME)
    assert vehicles.names == ["motorcycle"]
    assert others.names == ["boat"]


def test_detect_applies_the_confidence() -> None:
    backend = FakeBackend(raw([box(10, 10, 40, 25), box(100, 10, 130, 25)], [0.9, 0.2],
                              ["car", "car"]))  # fmt: skip
    found = N.NeuralDetector(backend, N.ViewParams(conf=0.25)).detect(FRAME)
    assert [d.score for d in found] == pytest.approx([0.9])
    assert found[0].label == "car"


def test_an_unknown_variant_is_refused() -> None:
    with pytest.raises(ValueError, match="variant"):
        N.NeuralDetector(FakeBackend(raw([], [], [])), N.ViewParams(variant="huge"))


def test_fusion_keeps_the_primary_and_adds_only_what_it_missed() -> None:
    primary = np.array([box(0, 0, 10, 10)], np.float32)
    secondary = np.array([box(1, 0, 11, 10), box(50, 50, 60, 60)], np.float32)
    assert N.fuse_keep(primary, secondary, 0.3).tolist() == [1]


def test_fusion_with_no_primary_keeps_distinct_secondaries() -> None:
    secondary = np.array([box(0, 0, 10, 10), box(1, 0, 11, 10), box(50, 50, 60, 60)], np.float32)
    assert N.fuse_keep(np.zeros((0, 4), np.float32), secondary, 0.3).tolist() == [0, 2]


def test_fusion_with_no_secondary_adds_nothing() -> None:
    primary = np.array([box(0, 0, 10, 10)], np.float32)
    assert N.fuse_keep(primary, np.zeros((0, 4), np.float32), 0.3).tolist() == []


def score_module() -> Any:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "cv_neural_score", root / "scripts/cv_neural_score.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def row(name: str, licence: str, recall: float, precision: float, conf: float = 0.25) -> dict:
    dev = {
        "vehicle_frame_recall": {"value": recall},
        "precision_proxy": {"value": precision},
    }
    return {
        "candidate": name,
        "variant": "native",
        "conf": conf,
        "licence": licence,
        "scores": {"sets": {"dev": dev}},
    }


def test_the_rule_takes_the_best_apache_above_the_precision_bar() -> None:
    m = score_module()
    rows = [row("a", "Apache-2.0", 0.9, 0.79), row("b", "Apache-2.0", 0.7, 0.8)]
    got = m.select(rows, [0.25])
    assert got["winner"]["candidate"] == "b"
    assert got["best_apache"]["candidate"] == "b"


def test_agpl_stays_out_when_apache_trails_by_less_than_the_margin() -> None:
    m = score_module()
    rows = [row("a", "Apache-2.0", 0.71, 0.85), row("y", "AGPL-3.0", 0.80, 0.85)]
    got = m.select(rows, [0.25])
    assert got["agpl_minus_apache_recall"] == pytest.approx(0.09)
    assert got["agpl_eligible"] is False
    assert got["winner"]["candidate"] == "a"
    assert got["best_overall_by_recall"]["candidate"] == "y"


def test_agpl_comes_in_at_exactly_the_margin() -> None:
    m = score_module()
    rows = [row("a", "Apache-2.0", 0.7, 0.85), row("y", "AGPL-3.0", 0.8, 0.85)]
    got = m.select(rows, [0.25])
    assert got["agpl_eligible"] is True
    assert got["winner"]["candidate"] == "y"


def test_an_agpl_model_below_the_bar_does_not_count() -> None:
    m = score_module()
    rows = [row("a", "Apache-2.0", 0.5, 0.85), row("y", "AGPL-3.0", 0.9, 0.7)]
    got = m.select(rows, [0.25])
    assert got["best_agpl"] is None
    assert got["winner"]["candidate"] == "a"


def test_no_qualifying_apache_model_means_no_winner_not_a_default_to_agpl() -> None:
    m = score_module()
    got = m.select([row("a", "Apache-2.0", 0.9, 0.5), row("y", "AGPL-3.0", 0.8, 0.9)], [0.25])
    assert got["winner"] is None
    assert got["agpl_eligible"] is False


def test_blobs_are_never_selected() -> None:
    m = score_module()
    rows = [row("a", "Apache-2.0", 0.5, 0.85), row("median-blob", "repository code", 0.9, 0.9)]
    assert m.select(rows, [0.25])["winner"]["candidate"] == "a"


def frame_result(frame: int, matched: list[int], in_view: int) -> Any:
    m = score_module()
    matches = tuple(
        m.S.Match(frame, f"{frame}:{v}", str(frame), v, 0.0, 0.0, 0.0, True) for v in matched
    )
    return m.S.FrameResult(frame, len(matched), 0, in_view, matches, (), ())


def test_persistence_counts_only_vehicles_still_in_view_in_the_next_frame() -> None:
    m = score_module()
    refs = {
        10: [m.S.Ref(10, v, 0.0, 0.0, 0.0) for v in (1, 2, 3)],
        11: [m.S.Ref(11, v, 0.0, 0.0, 0.0) for v in (1, 2)],
    }
    results = {10: frame_result(10, [1, 2, 3], 3), 11: frame_result(11, [1], 2)}
    assert m.persistence(results, refs) == {"value": 0.5, "numerator": 1, "denominator": 2}


def test_persistence_ignores_frames_that_are_not_consecutive() -> None:
    m = score_module()
    refs = {10: [m.S.Ref(10, 1, 0.0, 0.0, 0.0)], 12: [m.S.Ref(12, 1, 0.0, 0.0, 0.0)]}
    results = {10: frame_result(10, [1], 1), 12: frame_result(12, [1], 1)}
    assert m.persistence(results, refs)["denominator"] == 0
