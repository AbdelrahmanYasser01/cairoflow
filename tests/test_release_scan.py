"""The release scan (scripts/release_scan.py): no footage and no stray paths in a public tree.

Throwaway repositories hold a launcher and a demo manifest of the same shape as this repository's.
A manifest that lets files be absent is what makes a tree public; any other is not judged.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCAN = REPO / "scripts/release_scan.py"
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def make_repo(root: Path, absent: list[str]) -> Path:
    manifest = {"absent_allowed": absent, "files": {}}
    files = {
        "scripts/demo_run.sh": 'MANIFEST="data/demo/m.json"\n',
        "data/demo/m.json": json.dumps(manifest),
        "LICENSE": "MIT License\n",
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    commit(root)
    return root


def commit(root: Path) -> None:
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "-C", str(root), "add", "-A", "-f"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(root), *identity, "commit", "-q", "-m", "x"], check=True, env=env
    )


def scan(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCAN), "--repo", str(root), *args], capture_output=True, text=True
    )


@pytest.fixture()
def public(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "public", ["sam_overlay"])


def test_a_clean_public_tree_passes(public: Path) -> None:
    out = scan(public)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "no footage, nothing unexpected at the top level" in out.stdout


def test_a_tree_that_requires_every_pin_is_not_judged(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "private", [])
    (root / "data/overlay.mp4").write_bytes(b"\x00" * 16)
    commit(root)
    out = scan(root)
    assert out.returncode == 3 and "not a public tree" in out.stdout


@pytest.mark.parametrize("name", ["data/overlay.mp4", "docs/frame.JPG", "experiments/a/plot.png"])
def test_footage_is_found_by_name(public: Path, name: str) -> None:
    (public / name).parent.mkdir(parents=True, exist_ok=True)
    (public / name).write_bytes(b"not really an image")
    commit(public)
    out = scan(public)
    assert out.returncode == 1 and f"footage: {name}" in out.stdout


@pytest.mark.parametrize(
    "head",
    [
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff\xe0",
        b"\x00\x00\x00\x18ftypisom",
        b"RIFF\x00\x00\x00\x00AVI ",
        b"\x1a\x45\xdf\xa3",
    ],
)
def test_footage_under_another_name_is_found_by_its_first_bytes(public: Path, head: bytes) -> None:
    (public / "data/notes.bin").write_bytes(head + b"\x00" * 32)
    commit(public)
    out = scan(public)
    assert out.returncode == 1 and "footage: data/notes.bin" in out.stdout


def test_an_icon_of_the_web_app_is_not_footage(public: Path) -> None:
    (public / "apps/web/public").mkdir(parents=True)
    (public / "apps/web/public/logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    commit(public)
    assert scan(public).returncode == 0


def test_an_unexpected_top_level_path_fails(public: Path) -> None:
    (public / ".tooling").mkdir()
    (public / ".tooling/settings.json").write_text("{}", encoding="utf-8")
    commit(public)
    out = scan(public)
    assert out.returncode == 1 and "unexpected at the top level: .tooling" in out.stdout


def test_a_listed_term_fails_by_path_and_records_are_left_alone(public: Path, tmp_path) -> None:
    terms = tmp_path / "terms.txt"
    terms.write_text("# a comment\nsecret[- ]plan\n", encoding="utf-8")
    assert scan(public, "--terms", str(terms)).returncode == 0
    (public / "experiments/records").mkdir(parents=True)
    (public / "experiments/records/r.json").write_text('"SECRET PLAN"', encoding="utf-8")
    commit(public)
    assert scan(public, "--terms", str(terms)).returncode == 0  # records stay as written
    (public / "docs").mkdir()
    (public / "docs/x.md").write_text("the Secret-Plan\n", encoding="utf-8")
    commit(public)
    out = scan(public, "--terms", str(terms))
    assert out.returncode == 1 and "holds a listed term: docs/x.md" in out.stdout
    assert "Secret-Plan" not in out.stdout  # paths only
