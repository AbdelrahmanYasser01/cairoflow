"""A3 review scoring (scripts/score_a3_review.py) on a small synthetic sheet; and the committed
sheet's protocol, if the sheet is committed."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "experiments/records/ngsim-cam2-sam-20261028T151050Z/a3_sheet.json"
REVIEW = COMMITTED.parent / "a3_review.json"  # the scored record of the reviewer's marks


def _script():
    path = REPO / "scripts" / "score_a3_review.py"
    spec = importlib.util.spec_from_file_location("score_a3", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["score_a3"] = module
    spec.loader.exec_module(module)
    return module


SHEET = {
    "sheet_id": "s1",
    "protocol": {"sample_frames": [0, 20]},
    "boxes": [
        {"frame": 0, "box": 1, "contact_above_roi": True},
        {"frame": 0, "box": 2, "contact_above_roi": True},
        {"frame": 20, "box": 1, "contact_above_roi": False},
        {"frame": 20, "box": 2, "contact_above_roi": True},
    ],
    "strips": [{"track_id": "car:t1"}, {"track_id": "car:t2"}],
}


def _marks(**changes):
    marks = [
        {"kind": "box", "frame": 0, "box": 1, "verdict": "vehicle"},
        {"kind": "box", "frame": 0, "box": 2, "verdict": "partial"},
        {"kind": "box", "frame": 20, "box": 1, "verdict": "vehicle"},  # below the line
        {"kind": "box", "frame": 20, "box": 2, "verdict": "vehicle"},
        {"kind": "frame", "frame": 0, "missed": 3},
        {"kind": "frame", "frame": 20, "missed": 1},
        {"kind": "track", "track_id": "car:t1", "switches": 0},
        {"kind": "track", "track_id": "car:t2", "switches": 2},
    ]
    return [{**m, "sheet_id": "s1", **changes} for m in marks]


def test_scores_follow_the_protocol() -> None:
    s = _script().score(SHEET, _marks())
    assert s["verdicts"] == {"vehicle": 3, "not_vehicle": 0, "partial": 1}
    assert s["precision"]["value"] == 0.75  # 3 of 4 boxes
    # recall counts only vehicle boxes above the line: 2 found, 4 missed
    assert (s["recall"]["found"], s["recall"]["missed"], s["recall"]["value"]) == (2, 4, 0.3333)
    assert s["id_switches"] == {"total": 2, "tracks_with_a_switch": 1, "tracks": 2}
    low, high = s["precision"]["ci95"]
    assert low < 0.75 < high


def test_incomplete_duplicate_or_foreign_marks_are_refused() -> None:
    module = _script()
    score, error = module.score, module.ReviewError
    with pytest.raises(error, match="box 2 in frame 20 not marked"):
        score(SHEET, [m for m in _marks() if (m.get("frame"), m.get("box")) != (20, 2)])
    with pytest.raises(error, match="marked 2 times"):
        score(SHEET, [*_marks(), _marks()[0]])
    with pytest.raises(error, match="not marked"):
        score(SHEET, _marks(sheet_id="another sheet"))  # marks on another sheet do not count
    with pytest.raises(error, match="does not define"):
        score(SHEET, [*_marks(), {"kind": "box", "frame": 5, "box": 1, "verdict": "vehicle",
                                  "sheet_id": "s1"}])  # fmt: skip


def test_wilson_interval_and_exported_mark_files(tmp_path: Path) -> None:
    module = _script()
    assert module.wilson(0, 0) is None
    low, high = module.wilson(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-4) and high == pytest.approx(0.5962, abs=1e-4)
    folder = tmp_path / "marks"
    folder.mkdir()
    for i, m in enumerate(_marks()):  # ArtifactData out_dir: one file per document
        (folder / f"{i}.json").write_text(json.dumps({"id": str(i), "data": m}), "utf-8")
    assert len(module.load_marks(folder)) == 8


@pytest.mark.skipif(not REVIEW.exists(), reason="no committed A3 review")
def test_the_committed_review_rescores_from_its_own_marks() -> None:
    record = json.loads(REVIEW.read_text(encoding="utf-8"))
    assert record["sheet_sha256"] == hashlib.sha256(COMMITTED.read_bytes()).hexdigest()
    sheet = json.loads(COMMITTED.read_text(encoding="utf-8"))
    rescored = json.loads(json.dumps(_script().score(sheet, record["marks"])))
    assert rescored == record["scores"]


@pytest.mark.skipif(not COMMITTED.exists(), reason="no committed A3 sheet")
def test_the_committed_sheet_samples_at_least_50_boxes() -> None:
    sheet = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert len(sheet["boxes"]) >= 50
    assert sheet["protocol"]["sample_frames"] == [0, 20, 40, 60, 80, 99]
    assert {b["frame"] for b in sheet["boxes"]} <= set(sheet["protocol"]["sample_frames"])
