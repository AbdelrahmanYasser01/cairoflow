"""`scripts/fresh_clone_check.sh --remote URL`: the fresh-clone check on a clone of a remote's
default branch (the repository as pushed) instead of this checkout's HEAD.

The full check (install, suite, network fetch) takes minutes; here CF_FRESH_STOP_AFTER=clone stops
after the clone step, which then exits 3, never 0. Remotes are throwaway bare repositories under
tmp_path; nothing here contacts a real host or sends anything anywhere. A credential in a URL is
assembled at run time and must never be printed.
"""

from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "scripts/fresh_clone_check.sh"
SECRET = "Zk7" + "Qw4" * 3  # the password part of a URL: assembled, never written whole
pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("uv") is None, reason="needs git and uv"
)


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    """This process's environment without proxies or the user's Git config, temp files here."""
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        TMPDIR=str(tmp_path),
    )
    env.update(extra)
    return env


def check(tmp_path: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(CHECK), *args],
        capture_output=True,
        text=True,
        env=env_for(tmp_path, **extra),
        timeout=180,
    )


def bare_remote(tmp_path: Path) -> tuple[Path, str]:
    """A bare repository with one commit (not this repository's); its path and HEAD sha."""
    source = tmp_path / "source"
    source.mkdir()

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=source, check=True, capture_output=True, text=True,
            env=env_for(tmp_path),
        )  # fmt: skip
        return done.stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (source / "README.md").write_text("a remote that is not this checkout\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-q", "-m", "remote")
    sha = git("rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(source), str(remote)], check=True,
        capture_output=True, env=env_for(tmp_path),
    )  # fmt: skip
    return remote, sha


def left_behind(tmp_path: Path) -> list[Path]:
    return sorted(tmp_path.glob("cairoflow-fresh-*"))


@pytest.mark.parametrize(
    "args", [["--nope"], ["--remote"], ["--remote", ""], ["--remote="], ["--remote", "x", "y"]]
)
def test_bad_arguments_are_a_usage_error_that_leaves_nothing_behind(
    tmp_path: Path, args: list[str]
) -> None:
    out = check(tmp_path, *args)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "usage: bash scripts/fresh_clone_check.sh [--remote URL]" in out.stderr
    assert left_behind(tmp_path) == []


@pytest.mark.parametrize("form", ["separate", "equals"])
def test_the_remote_is_cloned_at_its_own_head_not_this_checkouts(tmp_path: Path, form) -> None:
    remote, sha = bare_remote(tmp_path)
    url = f"file://{remote}"
    args = ["--remote", url] if form == "separate" else [f"--remote={url}"]
    out = check(tmp_path, *args, CF_FRESH_STOP_AFTER="clone")
    assert out.returncode == 3, out.stdout + out.stderr  # stopped: never reported as a pass
    assert f"PASS clone: HEAD {sha[:12]}" in out.stdout
    assert f"remote {url}: main at {sha[:12]}, not this checkout's HEAD" in out.stdout
    assert "STOPPED after the clone step" in out.stdout and ": PASS (" not in out.stdout
    assert left_behind(tmp_path) == []  # the temp dir is removed on exit


def test_a_credential_in_the_url_is_never_printed(tmp_path: Path) -> None:
    """Port 9 on the loopback refuses at once: the clone fails without leaving this machine."""
    url = f"https://cf-user:{SECRET}@127.0.0.1:9/x.git"
    out = check(tmp_path, "--remote", url, CF_FRESH_STOP_AFTER="clone")
    assert out.returncode == 2, out.stdout + out.stderr
    assert SECRET not in out.stdout + out.stderr
    assert "git clone of https://***@127.0.0.1:9/x.git failed" in out.stderr
    assert left_behind(tmp_path) == []
    out = check(tmp_path, f"--bogus={url}")
    assert out.returncode == 2 and SECRET not in out.stdout + out.stderr


def test_a_kept_remote_clone_holds_no_credential(tmp_path: Path) -> None:
    remote, _ = bare_remote(tmp_path)
    url = f"file://cf-user:{SECRET}@{remote}"  # a file URL with a user part: git ignores it
    out = check(tmp_path, "--remote", url, CF_FRESH_STOP_AFTER="clone", CF_FRESH_KEEP="1")
    kept = [line.removeprefix("kept: ") for line in out.stdout.splitlines() if "kept: " in line]
    assert out.returncode in (2, 3) and SECRET not in out.stdout + out.stderr
    if out.returncode == 3:  # the file transport accepted the URL: its config is masked
        config = (Path(kept[0]) / ".git" / "config").read_text(encoding="utf-8")
        assert SECRET not in config and "***@" in config


def test_a_password_with_an_at_sign_and_a_query_token_are_never_printed(tmp_path: Path) -> None:
    head, tail = SECRET[:5], SECRET[5:]
    url = f"https://cf-user:{head}@{tail}@127.0.0.1:9/x.git?token={SECRET}"
    out = check(tmp_path, "--remote", url, CF_FRESH_STOP_AFTER="clone")
    assert out.returncode == 2, out.stdout + out.stderr
    for part in (head, tail, SECRET):
        assert part not in out.stdout + out.stderr, part
    assert "https://***@127.0.0.1:9/x.git?***" in out.stderr


class _Remote(http.server.SimpleHTTPRequestHandler):
    """A remote on the loopback: /repo.git/ is a public repository over Git's dumb HTTP
    protocol, everything under /private/ asks for a login (401)."""

    def do_GET(self) -> None:
        if self.path.startswith("/private/"):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="private"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *args) -> None:  # keep the test output quiet
        pass


@pytest.fixture()
def http_remote(tmp_path: Path):
    remote, sha = bare_remote(tmp_path)
    subprocess.run(["git", "-C", str(remote), "update-server-info"], check=True)
    served = tmp_path / "served"
    served.mkdir()
    shutil.move(str(remote), served / "repo.git")
    handler = functools.partial(_Remote, directory=str(served))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_address[1]}", sha
    finally:
        server.shutdown()
        server.server_close()


def test_a_remote_clone_neither_stores_a_token_nor_asks_for_a_login(
    tmp_path: Path, http_remote
) -> None:
    """The user's own Git config may hold a credential helper or an askpass program: the
    remote clone uses neither, so a token in the URL is never written to disk and a remote that
    asks for a login fails at once, as it would for a reviewer without credentials."""
    host, sha = http_remote
    creds, asked = tmp_path / "stored-credentials", tmp_path / "askpass.log"
    config = tmp_path / "gitconfig"
    config.write_text(f"[credential]\n\thelper = store --file {creds}\n", encoding="utf-8")
    askpass = tmp_path / "askpass.sh"
    askpass.write_text(f'#!/bin/sh\necho "$1" >> "{asked}"\necho x\n', encoding="utf-8")
    askpass.chmod(0o755)
    env = {"GIT_CONFIG_GLOBAL": str(config), "GIT_ASKPASS": str(askpass)}

    url = f"http://cf-user:{SECRET}@{host}/repo.git"
    out = check(tmp_path, "--remote", url, CF_FRESH_STOP_AFTER="clone", **env)
    assert out.returncode == 3, out.stdout + out.stderr
    assert f"PASS clone: HEAD {sha[:12]}" in out.stdout
    assert SECRET not in out.stdout + out.stderr
    assert not creds.exists() or SECRET not in creds.read_text(encoding="utf-8")

    out = check(tmp_path, "--remote", f"http://{host}/private/x.git", **env)
    assert out.returncode == 2, out.stdout + out.stderr
    assert not asked.exists(), asked.read_text(encoding="utf-8")


def test_without_arguments_the_committed_head_of_this_checkout_is_cloned(tmp_path: Path) -> None:
    """No argument: a clone of this checkout's HEAD (stopped after the clone)."""
    head = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    out = check(tmp_path, CF_FRESH_STOP_AFTER="clone")
    assert out.returncode == 3, out.stdout + out.stderr
    assert f"PASS clone: HEAD {head[:12]}" in out.stdout and "remote " not in out.stdout
    assert f"fresh clone of {head[:12]}: STOPPED" in out.stdout
    script = CHECK.read_text(encoding="utf-8")
    assert 'git clone --quiet --no-local --no-checkout "$REPO" "$CLONE"' in script
