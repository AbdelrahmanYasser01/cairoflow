"""Secret scan over the full Git history: key NAMES, commits and paths, never values.

    uv run --frozen python scripts/fresh_clone_scan.py [--repo PATH]

Reads every line ever added on every ref (`git log --all -p --cc`; in a clone that is every
branch and tag of its origin), including what a merge adds that none of its parents had (a
conflict resolution, a file swept in), and the path of every file ever added, copied or renamed
to, a merge's against each parent. A line counts when it assigns a
credential-like name (API key, secret, token, password, access key) a token-shaped value, or
holds a known token shape (an `sk-` key, an AWS key id, a bearer token, a private-key block);
a path counts when it is a credential file (`.env` other than `.env.example`, `*.pem`, `*.key`,
`id_rsa*`, `*.p12`). A value is 16+ characters of `A-Za-z0-9_-+/=|.` holding letters and digits,
or 20+ letters of mixed case; placeholders, environment lookups, identifiers, file paths and
40/64-hex digests are not values.
Exit 1 when anything is found; the report names it and never prints the matched text.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

NAMED = re.compile(
    r"(?i)\b([a-z0-9_]*(?:api[_-]?key|secret|token|passw(?:or)?d|access[_-]?key)[a-z0-9_]*)"
    r"[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_\-+/=|.]{16,})"
)
FILE_PATH = re.compile(r"(?:[\w.\-]+/)+[\w\-]+\.[A-Za-z0-9]{1,5}")
SHAPES = {
    "sk- API key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "AWS access key id": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "Hugging Face token": re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    "bearer token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.=|]{20,}"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}
DIGEST = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
CREDENTIAL_FILE = re.compile(r"^(?:\.env(?:\..+)?|id_rsa.*|.*\.(?:pem|key|p12|pfx))$")
TEMPLATES = {".env.example"}
COMMIT = re.compile(r"^commit ([0-9a-f]{40})\b")


@dataclass(frozen=True)
class Finding:
    """What was found and where; deliberately without the matched text."""

    name: str
    commit: str
    path: str


def _is_value(value: str) -> bool:
    """Token-shaped: letters and digits, or 20+ letters of mixed case (an identifier is not).

    A 40/64-hex digest and a file path (`dir/name.ext`) are not values.
    """
    if DIGEST.fullmatch(value.lower()) or FILE_PATH.fullmatch(value):
        return False
    letters = [c for c in value if c.isalpha()]
    if letters and any(c.isdigit() for c in value):
        return True
    upper = sum(c.isupper() for c in letters)
    return len(letters) >= 20 and 0.25 <= upper / len(letters) <= 0.75


def scan_line(line: str) -> list[str]:
    """The names (or token kinds) of the credentials one line of text holds."""
    found = [m.group(1) for m in NAMED.finditer(line) if _is_value(m.group(2))]
    found += [kind for kind, shape in SHAPES.items() if shape.search(line)]
    if "bearer token" in found and len(found) > 1:  # 'TOKEN = "Bearer …"' is one finding
        found.remove("bearer token")
    return list(dict.fromkeys(found))


def scan_diff(lines: Iterable[str]) -> list[Finding]:
    """Findings in the added lines of `git log -p --cc --unified=0` output.

    A merge's combined diff (`diff --cc`, `@@@` hunks) has one mark column per parent; a line
    with a '+' and no '-' in its columns is in the merge's result, so it is read too.
    """
    findings: dict[tuple[str, str], Finding] = {}
    commit, path, in_hunk, columns = "", "", False, 1
    for line in lines:
        head = COMMIT.match(line)
        if head:
            commit, path, in_hunk = head.group(1)[:12], "", False
        elif line.startswith("diff --git "):
            path, in_hunk = line.rsplit(" b/", 1)[-1], False
        elif line.startswith(("diff --cc ", "diff --combined ")):
            path, in_hunk = line.split(" ", 2)[2], False
        elif not in_hunk and line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
        elif line.startswith("@@"):
            in_hunk, columns = True, len(line) - len(line.lstrip("@")) - 1
        elif in_hunk and "+" in line[:columns] and "-" not in line[:columns]:
            for name in scan_line(line[columns:]):
                findings.setdefault((name, path), Finding(name, commit, path))
    return list(findings.values())


def scan_paths(added: Iterable[tuple[str, str]]) -> list[Finding]:
    """Findings among the paths of files ever added: (commit, path) pairs."""
    findings: dict[str, Finding] = {}
    for commit, path in added:
        name = PurePosixPath(path).name
        if name not in TEMPLATES and CREDENTIAL_FILE.match(name):
            findings.setdefault(path, Finding("credential file", commit[:12], path))
    return list(findings.values())


def _git_lines(repo: Path, *args: str) -> Iterator[str]:
    with subprocess.Popen(
        ["git", "-C", str(repo), *args], stdout=subprocess.PIPE, text=True, errors="replace"
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n")
    if proc.returncode:
        raise RuntimeError(f"git {args[0]} exited {proc.returncode}")


def _added_paths(repo: Path) -> Iterator[tuple[str, str]]:
    """Every path a commit added, copied or renamed to; a merge against each of its parents."""
    commit = ""
    for line in _git_lines(
        repo, "log", "--all", "-m", "--diff-filter=ACR", "--name-only", "--format=commit %H"
    ):
        head = COMMIT.match(line)
        if head:
            commit = head.group(1)
        elif line:
            yield commit, line


def scan_repo(repo: Path) -> list[Finding]:
    """Every finding in the full history of `repo` (all refs)."""
    # --cc: a merge's combined diff, what it adds that none of its parents had
    diff = _git_lines(
        repo, "log", "--all", "-p", "--cc", "--unified=0", "--no-color", "--no-ext-diff",
        "--no-textconv", "--format=commit %H",
    )  # fmt: skip
    return scan_diff(diff) + scan_paths(_added_paths(repo))


def report(findings: list[Finding]) -> str:
    """One line per finding: the name, the first commit that added it, the path. No values."""
    if not findings:
        return "secret scan: nothing found in the full history"
    rows = [f"secret scan: {f.name} in {f.commit} {f.path}" for f in findings]
    return "\n".join([*rows, f"secret scan: {len(findings)} finding(s); values are not printed"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    commits = subprocess.run(
        ["git", "-C", str(args.repo), "rev-list", "--all", "--count"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    findings = scan_repo(args.repo)
    print(f"secret scan: {commits} commits on all refs of {args.repo}")
    print(report(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
