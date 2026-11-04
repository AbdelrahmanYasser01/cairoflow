"""Check a public tree at HEAD: no footage, nothing unexpected at the top level.

    python3 scripts/release_scan.py --repo PATH [--terms FILE]

Reads the commit, never the working tree, and uses only the standard library. A tree is public
when the manifest its launcher serves lets pixel-bearing files be absent; any other tree may carry
footage and is not judged here (exit 3). Footage is found by name and by the first bytes of each
file, so a renamed video is caught too. With `--terms`, a file of extended regular expressions,
one per line, each must match no tracked file outside the records (data/, experiments/, cache/),
which stay as they were written. Prints paths only. Exit 0 clean, 1 on a finding, 2 on a usage
error, 3 if the tree is not a public one.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

LAUNCHER = "scripts/demo_run.sh"
PIXEL_NAME = re.compile(
    r"\.(mp4|m4v|avi|mov|webm|mkv|mpe?g|png|jpe?g|gif|bmp|tiff?|webp)$", re.IGNORECASE
)
PIXEL_ALLOWED = re.compile(r"^apps/web/public/")  # logos and icons, no footage
RECORDS = re.compile(r"^(data|experiments|cache)/")
TOP_LEVEL = frozenset(
    {
        ".env.example", ".gitattributes", ".gitignore", ".python-version", "LICENSE", "NOTICE",
        "README.md", "apps", "cache", "data", "docs", "experiments", "packages", "pyproject.toml",
        "scripts", "tests", "uv.lock",
    }
)  # fmt: skip


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout


def tracked(repo: Path) -> list[str]:
    listed = git(repo, "ls-tree", "-r", "-z", "--name-only", "HEAD").decode("utf-8")
    return [name for name in listed.split("\0") if name]


def blob(repo: Path, name: str) -> bytes:
    return git(repo, "show", f"HEAD:{name}")


def is_public(repo: Path, names: list[str]) -> bool:
    if LAUNCHER not in names:
        return False
    found = re.search(r'^MANIFEST="([^"]+)"$', blob(repo, LAUNCHER).decode("utf-8"), re.M)
    if found is None or found.group(1) not in names:
        return False
    return bool(json.loads(blob(repo, found.group(1))).get("absent_allowed"))


def looks_like_footage(head: bytes) -> bool:
    return (
        head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"\x1a\x45\xdf\xa3"))
        or head[4:8] == b"ftyp"
        or (head[:4] == b"RIFF" and head[8:12] in (b"AVI ", b"WEBP"))
    )


def footage(repo: Path, names: list[str]) -> list[str]:
    found = []
    for name in names:
        if PIXEL_ALLOWED.match(name):
            continue
        if PIXEL_NAME.search(name) or looks_like_footage(blob(repo, name)[:12]):
            found.append(name)
    return found


def term_hits(repo: Path, names: list[str], terms: Path) -> list[str]:
    """Which tracked files hold a term; the matched text itself is not printed."""
    lines = terms.read_text("utf-8").splitlines()
    wanted = [line for line in lines if line and not line.startswith("#")]
    pattern = re.compile("|".join(wanted).encode("utf-8"), re.IGNORECASE)
    return [n for n in names if not RECORDS.match(n) and pattern.search(blob(repo, n))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--terms", type=Path)
    args = parser.parse_args(argv)
    names = tracked(args.repo)
    if not is_public(args.repo, names):
        print("not a public tree: its demo manifest lets nothing be absent")
        return 3
    tops = sorted({name.split("/")[0] for name in names} - TOP_LEVEL)
    pixels = footage(args.repo, names)
    hits = term_hits(args.repo, names, args.terms) if args.terms else []
    for label, items in (
        ("unexpected at the top level", tops),
        ("footage", pixels),
        ("holds a listed term", hits),
    ):
        for item in items:
            print(f"{label}: {item}")
    if tops or pixels or hits:
        return 1
    listed = f", no listed term ({args.terms.name})" if args.terms else ""
    print(f"{len(names)} tracked files: no footage, nothing unexpected at the top level{listed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
