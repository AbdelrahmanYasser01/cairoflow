"""The secret scan over the full history (scripts/fresh_clone_scan.py).

It reports key NAMES, commits and paths, never a value. The credential-like strings below are
assembled at run time, so this file never contains one and never trips the scan it tests.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fresh_clone_scan", REPO / "scripts/fresh_clone_scan.py"
)
scan = importlib.util.module_from_spec(SPEC)
sys.modules["fresh_clone_scan"] = scan
SPEC.loader.exec_module(scan)

VALUE = "Zq" + "7rT2" * 6  # 26 token characters, letters and digits
SK = "sk" + "-" + "a1B2" * 6
AWS = "AK" + "IA" + "Q7" * 8
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"
SHA = "c74ef7cc6d92aec6efb165bd40f4eccf788ae568988076eefb41196021f5520e"


def diff(*added: str, path: str = "config.py", commit: str = "1" * 40) -> list[str]:
    return [
        f"commit {commit}",
        f"diff --git a/{path} b/{path}",
        f"+++ b/{path}",
        "@@ -0,0 +1 @@",
        *(f"+{line}" for line in added),
    ]


def names(lines: list[str]) -> list[str]:
    return [finding.name for finding in scan.scan_diff(lines)]


def test_a_named_key_with_a_value_is_found_by_its_name_and_the_value_is_never_printed() -> None:
    findings = scan.scan_diff(diff(f"MODEL_API_KEY={VALUE}", path=".env.local"))
    assert [(f.name, f.commit, f.path) for f in findings] == [
        ("MODEL_API_KEY", "1" * 12, ".env.local")
    ]
    assert VALUE not in scan.report(findings)
    assert "MODEL_API_KEY" in scan.report(findings)


@pytest.mark.parametrize(
    "line",
    [
        "MODEL_API_KEY=",
        "MODEL_API_KEY=<your key>",
        'api_key = os.environ["MODEL_API_KEY"]',
        'API_KEY_ENV = "MODEL_API_KEY"',
        f'"sha256": "{SHA}"',
        f'token_sha256 = "{SHA}"',
        '"max_output_tokens": 16000',
        'headers = {"Authorization": f"Bearer {key}"}',
        "MODEL_API_KEY=${MODEL_API_KEY}",
    ],
)
def test_placeholders_lookups_and_digests_are_not_secrets(line: str) -> None:
    assert names(diff(line)) == []


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        (f'client = Client("{SK}")', "sk- API key"),
        (f"aws = '{AWS}'", "AWS access key id"),
        (PEM, "private key block"),
        (f'"Authorization": "Bearer {VALUE}"', "bearer token"),
    ],
)
def test_known_token_shapes_are_found_by_kind(line: str, kind: str) -> None:
    findings = scan.scan_diff(diff(line))
    assert [f.name for f in findings] == [kind]
    assert all(secret not in scan.report(findings) for secret in (SK, AWS, VALUE))


LETTERS = "QwErTyUiOp" * 3 + "As"  # 32 letters, mixed case, no digit


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        (f"MODEL_API_KEY=LLM|{'1' * 16}|{VALUE}", "MODEL_API_KEY"),  # pipe-delimited
        (f"MODEL_API_KEY=abc.{VALUE}", "MODEL_API_KEY"),  # a short dotted prefix
        (f"MODEL_API_KEY={LETTERS}", "MODEL_API_KEY"),  # letters only
        (f'"Authorization": "Bearer LLM|{"1" * 16}|{VALUE}"', "bearer token"),
    ],
)
def test_a_key_under_a_credential_name_is_found_in_common_delimited_shapes(
    line: str, kind: str
) -> None:
    findings = scan.scan_diff(diff(line))
    assert [f.name for f in findings] == [kind]
    assert VALUE not in scan.report(findings) and LETTERS not in scan.report(findings)


@pytest.mark.parametrize(
    "line",
    [
        'token_path = "data/demo/ngsim-int2-worldview.json"',
        'secret_name = "the_variable_that_holds_it"',
        'TOKENIZER = "PreTrainedTokenizerFast"',
    ],
)
def test_paths_and_identifiers_under_credential_like_names_are_not_values(line: str) -> None:
    assert names(diff(line)) == []


def test_only_added_lines_count_and_headers_are_not_content() -> None:
    lines = diff("x = 1")
    # a removed line: its addition, earlier in the history, is where it is a finding
    lines.insert(4, f"-MODEL_API_KEY={VALUE}")
    assert names(lines) == []


def test_files_that_hold_credentials_are_found_by_name() -> None:
    added = [".env", ".env.production", "secrets/model.key", "deploy/id_rsa", "cert.pem"]
    findings = scan.scan_paths(
        [("2" * 40, path) for path in [*added, ".env.example", "docs/key.md"]]
    )
    assert sorted(f.path for f in findings) == sorted(added)


def new_repo(root: Path):
    """A throwaway repository with one clean commit on main; returns its git runner."""

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    git("config", "commit.gpgsign", "false")
    git("config", "merge.conflictstyle", "merge")
    (root / "f.py").write_text("x = 1\n")
    git("add", "f.py")
    git("commit", "-q", "-m", "clean")
    return git


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_conflict_resolution_that_writes_a_key_is_found(tmp_path: Path) -> None:
    git = new_repo(tmp_path)
    git("checkout", "-q", "-b", "side")
    (tmp_path / "f.py").write_text("x = 2\n")
    git("commit", "-q", "-am", "side")
    git("checkout", "-q", "main")
    (tmp_path / "f.py").write_text("x = 3\n")
    git("commit", "-q", "-am", "main")
    subprocess.run(["git", "merge", "-q", "side"], cwd=tmp_path, capture_output=True)
    (tmp_path / "f.py").write_text(f"MODEL_API_KEY = '{VALUE}'\n")  # the resolution
    git("commit", "-q", "-am", "merge side")

    assert [(f.name, f.path) for f in scan.scan_repo(tmp_path)] == [("MODEL_API_KEY", "f.py")]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_files_and_lines_that_only_a_merge_adds_are_found(tmp_path: Path) -> None:
    git = new_repo(tmp_path)
    git("checkout", "-q", "-b", "side")
    (tmp_path / "s.txt").write_text("side\n")
    git("add", "s.txt")
    git("commit", "-q", "-m", "side")
    git("checkout", "-q", "main")
    git("merge", "-q", "--no-ff", "--no-commit", "side")
    (tmp_path / "settings.py").write_text(f"api_token = '{VALUE}'\n")
    (tmp_path / ".env").write_text("MODEL_API_KEY=\n")
    git("add", "-f", "settings.py", ".env")
    git("commit", "-q", "-m", "merge side")

    found = sorted((f.name, f.path) for f in scan.scan_repo(tmp_path))
    assert found == [("api_token", "settings.py"), ("credential file", ".env")]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_credential_file_made_by_a_rename_is_found(tmp_path: Path) -> None:
    git = new_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("some notes that are long enough to be a rename\n" * 4)
    git("add", "notes.txt")
    git("commit", "-q", "-m", "notes")
    git("mv", "notes.txt", "deploy.pem")
    git("commit", "-q", "-m", "rename")

    assert [(f.name, f.path) for f in scan.scan_repo(tmp_path)] == [
        ("credential file", "deploy.pem")
    ]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_scan_reads_every_branch_not_only_the_checked_out_one(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    git("config", "commit.gpgsign", "false")
    (tmp_path / "a.txt").write_text("nothing here\n")
    git("add", "a.txt")
    git("commit", "-q", "-m", "clean")
    git("checkout", "-q", "-b", "side")
    (tmp_path / "b.py").write_text(f"MODEL_API_KEY = '{VALUE}'\n")
    git("add", "b.py")
    git("commit", "-q", "-m", "leak")
    git("rm", "-q", "b.py")
    git("commit", "-q", "-m", "removed, but still in the history")
    git("checkout", "-q", "main")

    findings = scan.scan_repo(tmp_path)
    assert [(f.name, f.path) for f in findings] == [("MODEL_API_KEY", "b.py")]
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts/fresh_clone_scan.py"), "--repo", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 1
    assert "MODEL_API_KEY" in out.stdout and VALUE not in out.stdout + out.stderr
