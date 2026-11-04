"""The pre-push guard (scripts/prepush_check.sh): what a push would publish, checked first.

The real script runs against throwaway repositories (`--repo`), each holding a small launcher,
URL list and demo manifest of the same shape as this repository's. The size limit is lowered so
no test writes 50 MB, and the credential-like value is assembled at run time, so this file never
holds one. The guard only reads; it never publishes anything.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts/prepush_check.sh"
VALUE = "Qx" + "9mK3" * 6  # token-shaped: letters and digits, assembled here only
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

FILES = {
    "scripts/demo_run.sh": 'MANIFEST="data/demo/m.json"\nSNAPSHOT="data/demo/view.json"\n',
    "scripts/demo_urls.txt": (
        "# Served by scripts/demo_run.sh from data/demo/m.json; add the world/entity pages later.\n"
        "/demo\n/demo/sam-overlay.mp4\n/flow?e=movement:NE\n"
    ),
    "data/demo/m.json": '{"files": {"records": {"path": "data/r.json", "sha256": "0"}}}\n',
    "data/r.json": "{}\n",
    ".gitignore": "*.mp4\ndata/demo/view.json\nignored/\n",
}


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        TMPDIR=str(tmp_path),
        CF_PY=sys.executable,
    )
    env.update(extra)
    return env


def make_repo(root: Path, tmp_path: Path):
    """A committed throwaway repository; returns its git runner."""
    root.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True,
            env=env_for(tmp_path),
        )  # fmt: skip
        return done.stdout

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    git("config", "commit.gpgsign", "false")
    for name, text in FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    return git


def guard(repo: Path, tmp_path: Path, *args: str, **extra: str):
    return subprocess.run(
        ["bash", str(GUARD), "--repo", str(repo), *args], capture_output=True, text=True,
        env=env_for(tmp_path, **extra), timeout=120,
    )  # fmt: skip


@pytest.fixture()
def repo(tmp_path: Path) -> tuple[Path, object]:
    root = tmp_path / "repo"
    return root, make_repo(root, tmp_path)


def test_a_clean_small_repository_passes_and_nothing_is_published(repo, tmp_path: Path) -> None:
    root, _ = repo
    out = guard(root, tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    for line in ("PASS sizes", "INFO git-dir", "PASS secrets", "PASS clean", "PASS demo"):
        assert line in out.stdout, line
    assert "INFO pages: not run" in out.stdout  # no tests/test_release_demo.py in this repository
    assert str(root / ".git") in out.stdout  # the Git directory it measured
    assert out.stdout.rstrip().endswith("Nothing was published.")


def test_the_fifteen_largest_tracked_files_are_listed_largest_first(repo, tmp_path: Path) -> None:
    root, git = repo
    for n in range(1, 21):
        (root / f"f{n:02d}.bin").write_bytes(b"x" * (100 * n))
    git("add", "-A")
    git("commit", "-q", "-m", "twenty files")
    out = guard(root, tmp_path)
    listed = out.stdout.split("(bytes):\n", 1)[1].split("\nPASS sizes", 1)[0].splitlines()
    assert len(listed) == 15
    sizes = [int(line.split()[0]) for line in listed]
    assert sizes == sorted(sizes, reverse=True) and listed[0].split()[1] == "f20.bin"


def test_a_tracked_file_over_the_limit_fails_by_name(repo, tmp_path: Path) -> None:
    root, git = repo
    (root / "big.bin").write_bytes(b"x" * 2000)
    git("add", "big.bin")
    git("commit", "-q", "-m", "big")
    out = guard(root, tmp_path, CF_PREPUSH_MAX_BYTES="1000")
    assert out.returncode == 1, out.stdout
    assert "FAIL sizes" in out.stdout and "2000 big.bin" in out.stdout


def test_a_large_file_only_in_the_history_still_fails(repo, tmp_path: Path) -> None:
    root, git = repo
    git("checkout", "-q", "-b", "side")
    (root / "big.bin").write_bytes(b"x" * 2000)
    git("add", "big.bin")
    git("commit", "-q", "-m", "big")
    git("rm", "-q", "big.bin")
    git("commit", "-q", "-m", "removed, but still in the history")
    git("checkout", "-q", "main")
    out = guard(root, tmp_path, CF_PREPUSH_MAX_BYTES="1000")
    assert out.returncode == 1, out.stdout
    assert "over 1000 bytes in the history" in out.stdout and "big.bin" in out.stdout
    assert "over 1000 bytes at HEAD" not in out.stdout


@pytest.mark.parametrize("change", ["modified", "staged", "untracked"])
def test_anything_uncommitted_fails_the_clean_check(repo, tmp_path: Path, change: str) -> None:
    root, git = repo
    if change == "untracked":
        (root / "stray.txt").write_text("not committed\n", encoding="utf-8")
        expected = "?? stray.txt"
    else:
        (root / "data/r.json").write_text('{"edited": true}\n', encoding="utf-8")
        if change == "staged":
            git("add", "data/r.json")
        expected = ("M  " if change == "staged" else " M ") + "data/r.json"
    out = guard(root, tmp_path)
    assert out.returncode == 1 and "FAIL clean" in out.stdout
    assert expected in out.stdout


def test_a_credential_on_another_branch_fails_by_name_never_by_value(repo, tmp_path: Path) -> None:
    root, git = repo
    git("checkout", "-q", "-b", "side")
    (root / "b.py").write_text(f"MODEL_API_KEY = '{VALUE}'\n", encoding="utf-8")
    git("add", "b.py")
    git("commit", "-q", "-m", "leak")
    git("checkout", "-q", "main")
    out = guard(root, tmp_path)
    assert out.returncode == 1 and "FAIL secrets" in out.stdout
    assert "MODEL_API_KEY" in out.stdout and VALUE not in out.stdout + out.stderr


def test_a_pinned_file_that_is_not_committed_fails_and_names_the_rule(repo, tmp_path: Path) -> None:
    root, git = repo
    manifest = '{"files": {"view": {"path": "ignored/v.json", "sha256": "0"}}}\n'
    (root / "data/demo/m.json").write_text(manifest, encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored/v.json").write_text("{}\n", encoding="utf-8")  # present, never committed
    git("commit", "-q", "-am", "pin a Git-ignored file")
    out = guard(root, tmp_path)
    assert out.returncode == 1 and "FAIL demo" in out.stdout
    assert "ignored/v.json, pinned by data/demo/m.json, is not committed" in out.stdout
    assert "(Git-ignored by .gitignore:3:ignored/)" in out.stdout


def test_a_repository_path_named_in_a_url_list_comment_must_be_committed(
    repo, tmp_path: Path
) -> None:
    root, git = repo
    urls = root / "scripts/demo_urls.txt"
    urls.write_text(urls.read_text("utf-8") + "# also reads data/raw/x.csv.\n", encoding="utf-8")
    git("commit", "-q", "-am", "name an uncommitted input")
    out = guard(root, tmp_path)
    assert out.returncode == 1, out.stdout
    assert "data/raw/x.csv, named in scripts/demo_urls.txt, is not committed" in out.stdout


def test_routes_entity_ids_prose_and_local_exclude_rules_are_not_failures(
    repo, tmp_path: Path
) -> None:
    """/demo/sam-overlay.mp4 matches *.mp4 but is a route; ?e=movement:NE is an entity id;
    world/entity is prose; a local `demo/` exclude rule (as this repository's own
    .git/info/exclude has) matches the tracked data/demo/m.json, which is still committed."""
    root, _ = repo
    (root / ".git/info/exclude").write_text("demo/\n", encoding="utf-8")
    out = guard(root, tmp_path)
    assert out.returncode == 0, out.stdout
    assert "PASS demo" in out.stdout and "1 files it pins" in out.stdout


def test_a_committed_world_model_view_fails(repo, tmp_path: Path) -> None:
    root, git = repo
    (root / "data/demo/view.json").write_text("{}\n", encoding="utf-8")
    git("add", "-f", "data/demo/view.json")
    git("commit", "-q", "-m", "commit the view")
    out = guard(root, tmp_path)
    assert out.returncode == 1 and "FAIL demo" in out.stdout
    assert "data/demo/view.json is committed" in out.stdout and "ADR-033 #9" in out.stdout


def test_a_worktree_is_checked_with_its_shared_git_directory(repo, tmp_path: Path) -> None:
    root, git = repo
    worktree = tmp_path / "worktree"
    git("worktree", "add", "-q", "-b", "wt", str(worktree))
    out = guard(worktree, tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert f"INFO git-dir: {root / '.git'}:" in out.stdout


@pytest.mark.parametrize("args", [["--nope"], ["--repo"], ["--repo="]])
def test_bad_arguments_are_a_usage_error(tmp_path: Path, args: list[str]) -> None:
    out = subprocess.run(
        ["bash", str(GUARD), *args], capture_output=True, text=True, env=env_for(tmp_path)
    )
    assert out.returncode == 2 and "usage: bash scripts/prepush_check.sh" in out.stderr


def test_the_guard_never_publishes_anything() -> None:
    script = GUARD.read_text(encoding="utf-8")
    invocation = re.compile(r"\b(?:git|g)\b(?:\s+-[Cc]\s+\S+)*\s+(?:push|send-pack|remote\s+add)\b")
    assert not invocation.search(script)
    assert "never pushes" in script


def test_a_negation_rule_is_never_given_as_why_a_file_is_not_committed(
    repo, tmp_path: Path
) -> None:
    """`!cache/keep.json` un-ignores the file: it is merely uncommitted, as this repository's own
    rules for the pinned Muse reply would leave it if `git add` were forgotten."""
    root, git = repo
    (root / ".gitignore").write_text("cache/*\n!cache/keep.json\n", encoding="utf-8")
    manifest = '{"files": {"reply": {"path": "cache/keep.json", "sha256": "0"}}}\n'
    (root / "data/demo/m.json").write_text(manifest, encoding="utf-8")
    (root / "cache").mkdir()
    (root / "cache/keep.json").write_text("{}\n", encoding="utf-8")
    git("add", ".gitignore", "data/demo/m.json")
    git("commit", "-q", "-m", "pin a file that is not ignored but not added")
    (root / "cache/keep.json").unlink()  # keep the tree clean: only the demo step should fail
    out = guard(root, tmp_path)
    assert out.returncode == 1 and "FAIL demo" in out.stdout
    line = next(x for x in out.stdout.splitlines() if "cache/keep.json, pinned by" in x)
    assert line.strip().endswith("is not committed") and "Git-ignored" not in line


def test_the_last_line_names_what_passed_and_what_did_not_run(repo, tmp_path: Path) -> None:
    root, _ = repo
    out = guard(root, tmp_path)
    last = out.stdout.rstrip().splitlines()[-1]
    assert last.endswith(
        ": PASS (passed: sizes secrets clean demo; not run: pages release; "
        "information only: git-dir)."
        " Nothing was published."
    ), last


def test_the_geometry_the_flow_page_reads_by_a_fixed_path_must_be_committed(
    repo, tmp_path: Path
) -> None:
    root, git = repo
    flow = root / "apps/api/cairoflow_api/flow.py"
    flow.parent.mkdir(parents=True)
    flow.write_text('GEOMETRY_FILE = "data/geometry/g.json"\n', encoding="utf-8")
    git("add", "apps")
    git("commit", "-q", "-m", "a page that reads a fixed file")
    out = guard(root, tmp_path)
    assert out.returncode == 1, out.stdout
    assert "data/geometry/g.json, which /flow reads by a fixed path, is not committed" in out.stdout
    (root / "data/geometry").mkdir(parents=True)
    (root / "data/geometry/g.json").write_text("{}\n", encoding="utf-8")
    git("add", "data/geometry/g.json")
    git("commit", "-q", "-m", "commit it")
    out = guard(root, tmp_path)
    assert out.returncode == 0 and "data/geometry/g.json (read by /flow)" in out.stdout


def test_the_clean_check_does_not_rewrite_the_index(repo, tmp_path: Path) -> None:
    """A tracked file whose timestamp changed makes a plain `git status` refresh the index."""
    root, _ = repo
    os.utime(root / "data/r.json", (1_700_000_000, 1_700_000_000))
    index = root / ".git/index"
    before = index.read_bytes()
    out = guard(root, tmp_path)
    assert out.returncode == 0, out.stdout
    assert index.read_bytes() == before


def test_a_path_that_is_not_a_checkout_is_named(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repository"
    plain.mkdir()
    out = subprocess.run(
        ["bash", str(GUARD), "--repo", str(plain)], capture_output=True, text=True,
        env=env_for(tmp_path, GIT_CEILING_DIRECTORIES=str(tmp_path)),
    )  # fmt: skip
    assert out.returncode == 2 and f"not a Git checkout: {plain}" in out.stderr


def make_public(root: Path, git) -> None:
    """Let the launcher's manifest leave one pixel-bearing pin absent, as the public tree's does."""
    manifest = {
        "absent_allowed": ["sam_overlay"],
        "files": {
            "records": {"path": "data/r.json", "sha256": "0"},
            "sam_overlay": {"path": "data/overlay.mp4", "sha256": "0"},
        },
    }
    (root / "data/demo/m.json").write_text(json.dumps(manifest), encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "public")


def test_a_pin_the_manifest_lets_be_absent_need_not_be_committed(repo, tmp_path: Path) -> None:
    root, git = repo
    make_public(root, git)
    out = guard(root, tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "PASS demo" in out.stdout and "PASS release" in out.stdout
    assert "no footage, nothing unexpected at the top level" in out.stdout


def test_footage_in_a_public_tree_fails_the_release_check(repo, tmp_path: Path) -> None:
    root, git = repo
    make_public(root, git)
    (root / "data/overlay.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom")
    git("add", "-f", "data/overlay.mp4")
    git("commit", "-q", "-m", "footage")
    out = guard(root, tmp_path)
    assert out.returncode == 1, out.stdout
    assert "FAIL release" in out.stdout and "footage: data/overlay.mp4" in out.stdout


def test_a_tree_that_requires_every_pin_is_not_judged_for_footage(repo, tmp_path: Path) -> None:
    root, git = repo
    (root / "data/overlay.mp4").write_bytes(b"\x00\x00\x00\x18ftypisom")
    git("add", "-f", "data/overlay.mp4")
    git("commit", "-q", "-m", "footage")
    out = guard(root, tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "INFO release: not run: not a public tree" in out.stdout
