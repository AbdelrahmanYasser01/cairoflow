"""The exit status of `scripts/fetch_ngsim_data.py --check` (the README's fresh-clone steps).

0 every input is present and matches its pin; 3 something is missing and nothing differs;
1 something differs from its pin, which outranks anything missing. The camera gate inputs count
like the NGSIM files. Runs on a temporary tree with its own pins: no network, no real data.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fetch_ngsim_data", REPO / "scripts/fetch_ngsim_data.py"
)
fetch = importlib.util.module_from_spec(SPEC)
sys.modules["fetch_ngsim_data"] = fetch
SPEC.loader.exec_module(fetch)

NAMES = ("trajectories", "camera2", *fetch.RESTORED)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Pins for every input on a temporary tree; returns a writer of (name, content)."""
    paths = {name: f"inputs/{name}.bin" for name in NAMES}
    monkeypatch.setattr(fetch, "REPO", tmp_path)
    for name, constant in (("trajectories", "TRAJECTORIES"), ("camera2", "CAMERA2")):
        monkeypatch.setattr(fetch, constant, {"path": paths[name], "sha256": sha(name)})
    gate = tmp_path / "gate.json"
    pins = {name: {"path": paths[name], "sha256": sha(name)} for name in fetch.RESTORED}
    gate.write_text(json.dumps({"inputs": pins}), encoding="utf-8")
    monkeypatch.setattr(fetch, "GATE", gate)

    def write(name: str, content: str | None = None) -> None:
        target = tmp_path / paths[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(name if content is None else content)

    return write


def test_every_input_present_and_pinned_is_0(tree) -> None:
    for name in NAMES:
        tree(name)
    assert fetch.check() == 0


def test_a_missing_camera_gate_input_is_3(tree) -> None:
    for name in NAMES[:-1]:
        tree(name)
    assert fetch.check() == 3


@pytest.mark.parametrize("tampered", fetch.RESTORED)
def test_a_camera_gate_input_that_differs_from_its_pin_is_1(tree, tampered: str) -> None:
    for name in NAMES:
        tree(name, "edited" if name == tampered else None)
    assert fetch.check() == 1


def test_a_mismatch_is_not_hidden_by_a_later_missing_file(tree, capsys) -> None:
    tree("trajectories", "edited")  # differs from its pin; camera2 is then missing
    for name in fetch.RESTORED:
        tree(name)
    assert fetch.check() == 1
    out = capsys.readouterr().out
    assert "trajectories: inputs/trajectories.bin does NOT match its pin" in out
    assert "camera2: missing" in out
