#!/usr/bin/env bash
# Pre-push guard: what a push would publish, checked before anything leaves this machine. It
# reads the repository and never changes it, and it never pushes.
#
#   bash scripts/prepush_check.sh [--repo PATH]       PATH: the checkout to check (default: this one)
#
#   sizes    the 15 largest files tracked at HEAD; FAIL if any is over 50 MB (50,000,000 bytes,
#            stricter than GitHub's 50 MiB warning), at HEAD or anywhere in the history of a
#            local ref, since a push sends history, not only HEAD
#   git-dir  the size of the Git directory (information; in a worktree .git is only a file)
#   secrets  scripts/fresh_clone_scan.py over the full history of every local ref (a push sends
#            the refs it names, a subset): key NAMES, commits and paths only, never a value
#   clean    nothing uncommitted: no staged, modified or untracked file
#   demo     what the demo URLs (scripts/demo_urls.txt) are served from is committed at HEAD:
#            the URL list, the launcher, the demo manifest it serves and every file that pins,
#            the geometry /flow reads by a fixed path (GEOMETRY_FILE in cairoflow_api/flow.py),
#            and every repository path a comment in the URL list names. The Git-ignored World
#            Model view (ADR-033 #9) must NOT be committed. A failure names the ignore rule that
#            hid a file, if one did. Routes (/demo/...) and entity ids (?e=...) are not files.
#            A pin the manifest lets be absent (absent_allowed) need not be committed.
#   pages    every report page served from the tracked files alone, without the World Model view
#            (tests/test_release_demo.py, the same selection as fresh_clone_check.sh's pages);
#            reported as not run where that test file is not committed
#   release  a public tree (its demo manifest lets footage be absent) holds no footage and
#            nothing unexpected at the top level, and no term of the terms file if there is one
#            (scripts/release_scan.py); reported as not run for any other tree
# The last line names what failed, what passed and what did not run. Env: CF_PREPUSH_MAX_BYTES
# (default 50000000); CF_PY, the Python for the stdlib-only scans (default python3);
# CF_RELEASE_TERMS, the terms file (default: scripts/release_terms.txt here, if there is one).
# Exit 0 if no check fails, 1 if one fails, 2 on a usage or setup error.
set -uo pipefail

die() { echo "prepush: $*" >&2; exit 2; }
USAGE="usage: bash scripts/prepush_check.sh [--repo PATH]"
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # the scan lives next to this script
REPO="$(dirname "$SCRIPTS")"
while (( $# )); do
  case "$1" in
    --repo)
      (( $# >= 2 )) && [[ -n "$2" ]] || die "--repo needs a path ($USAGE)"
      REPO="$2"
      shift 2 ;;
    --repo=*)
      REPO="${1#--repo=}"
      [[ -n "$REPO" ]] || die "--repo needs a path ($USAGE)"
      shift ;;
    -h|--help) echo "$USAGE"; exit 0 ;;
    *) die "unknown argument: $1 ($USAGE)" ;;
  esac
done
command -v git >/dev/null || die "git is needed"
top="$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null)" || die "not a Git checkout: $REPO"
REPO="$top"
HEAD_SHA="$(git -C "$REPO" rev-parse --verify --quiet 'HEAD^{commit}')" \
  || die "no commit at HEAD in $REPO"
LIMIT="${CF_PREPUSH_MAX_BYTES:-50000000}"
[[ "$LIMIT" =~ ^[0-9]+$ ]] || die "CF_PREPUSH_MAX_BYTES must be a whole number of bytes"
read -r -a PY <<<"${CF_PY:-python3}"
(( ${#PY[@]} )) || die "CF_PY is empty"

g() { git -C "$REPO" "$@"; }
committed() { g cat-file -e "HEAD:$1" 2>/dev/null; }  # a file or directory in the commit
why_not() {  # the ignore rule that hides a path, if any (check-ignore -v: source:line:pattern);
  local rule pattern  # a matching "!" rule un-hides the path, so it is no reason
  rule="$(g check-ignore -v --no-index -- "$1" 2>/dev/null | cut -f1)"
  pattern="${rule#*:}"
  pattern="${pattern#*:}"
  if [[ -n "$rule" && "$pattern" != '!'* ]]; then echo " (Git-ignored by ${rule})"; fi
}

failed=()
passed=()
skipped=()
pass() { echo "PASS $1: $2"; passed+=("$1"); }
fail() { echo "FAIL $1: $2"; failed+=("$1"); }
info() { echo "INFO $1: $2"; }
not_run() { echo "INFO $1: not run: $2"; skipped+=("$1"); }
step() { echo; echo "== $1 =="; }
echo "pre-push check of $REPO at ${HEAD_SHA:0:12} (nothing is sent anywhere)"

# --- sizes ---------------------------------------------------------------------------------------
step sizes
# "size path" per blob at HEAD, largest first; captured whole (no pipe into head under pipefail)
listing="$(g ls-tree -r -z --format='%(objecttype) %(objectsize) %(path)' HEAD | tr '\0' '\n' |
  awk '$1 == "blob" { sub(/^blob /, ""); print }' | sort -k1,1nr)"
files="$(grep -c . <<<"$listing")"
echo "the 15 largest of the $files files tracked at HEAD (bytes):"
head -n 15 <<<"$listing" | awk 'NF { s = $1; sub(/^[^ ]+ /, ""); printf "  %12d  %s\n", s, $0 }'
over="$(awk -v limit="$LIMIT" '$1 + 0 > limit + 0' <<<"$listing")"
history_over="$(g rev-list --objects --all |
  g cat-file --batch-check='%(objecttype) %(objectsize) %(rest)' |
  awk -v limit="$LIMIT" '$1 == "blob" && $2 + 0 > limit + 0 { s = $2; sub(/^[^ ]+ [^ ]+ /, ""); print s, $0 }' |
  sort -k1,1nr | uniq)"
if [[ -z "$over$history_over" ]]; then
  pass sizes "largest $(head -n 1 <<<"$listing" | cut -d' ' -f1) bytes; nothing over $LIMIT bytes at HEAD or in the history of a local ref"
else
  [[ -n "$over" ]] && { echo "over $LIMIT bytes at HEAD:"; sed 's/^/  /' <<<"$over"; }
  [[ -n "$history_over" ]] && { echo "over $LIMIT bytes in the history:"; sed 's/^/  /' <<<"$history_over"; }
  fail sizes "a file over $LIMIT bytes would be published (GitHub warns over 50 MiB and refuses over 100 MiB)"
fi

# --- git-dir -------------------------------------------------------------------------------------
step git-dir
GIT_DIR_PATH="$(g rev-parse --path-format=absolute --git-common-dir)"
bytes="$(du -sb "$GIT_DIR_PATH" 2>/dev/null | cut -f1)"
human="$(du -sh "$GIT_DIR_PATH" 2>/dev/null | cut -f1)"
objects="$(g count-objects -vH | paste -sd' ' -)"
info git-dir "$GIT_DIR_PATH: ${human:-?} on disk, ${bytes:-?} bytes apparent size ($objects)"

# --- secrets -------------------------------------------------------------------------------------
step secrets
out="$("${PY[@]}" "$SCRIPTS/fresh_clone_scan.py" --repo "$REPO" 2>&1)"
rc=$?
if (( rc == 0 )); then
  pass secrets "$(paste -sd' ' - <<<"$out") (every local ref)"
elif grep -q '^secret scan: .* finding(s); values are not printed$' <<<"$out"; then
  grep '^secret scan: ' <<<"$out"
  fail secrets "credential-like text in the history of a local ref (names above; no value printed)"
else
  tail -n 5 <<<"$out"
  fail secrets "rc=$rc: the scan did not finish"
fi

# --- clean ---------------------------------------------------------------------------------------
step clean
status="$(g --no-optional-locks status --porcelain=v1 --untracked-files=normal)"  # no index write
if [[ -z "$status" ]]; then
  pass clean "nothing staged, modified or untracked"
else
  head -n 20 <<<"$status" | sed 's/^/  /'
  fail clean "$(grep -c . <<<"$status") uncommitted entries (up to 20 above): commit, remove or ignore each; only commits are published"
fi

# --- demo ----------------------------------------------------------------------------------------
step demo
URLS="scripts/demo_urls.txt"
LAUNCHER="scripts/demo_run.sh"
problems=()
pinned=()
for name in "$URLS" "$LAUNCHER"; do
  committed "$name" || problems+=("$name is not committed$(why_not "$name")")
done
launcher="$(g show "HEAD:$LAUNCHER" 2>/dev/null)"
manifest="$(sed -n 's/^MANIFEST="\([^"]*\)"$/\1/p' <<<"$launcher" | head -n 1)"
snapshot="$(sed -n 's/^SNAPSHOT="\([^"]*\)"$/\1/p' <<<"$launcher" | head -n 1)"
if [[ -z "$manifest" ]]; then
  committed "$LAUNCHER" && problems+=("$LAUNCHER names no MANIFEST=\"...\"")
elif ! committed "$manifest"; then
  problems+=("$manifest, the manifest $LAUNCHER serves, is not committed$(why_not "$manifest")")
elif pins="$(g show "HEAD:$manifest" | "${PY[@]}" -c \
    'import json, sys; m = json.load(sys.stdin); a = m.get("absent_allowed", ()); [print(p["path"]) for r, p in m["files"].items() if r not in a]' 2>&1)"; then
  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    pinned+=("$path")
    committed "$path" || problems+=("$path, pinned by $manifest, is not committed$(why_not "$path")")
  done <<<"$pins"
else
  problems+=("$manifest does not list its pinned files: $(tail -n 1 <<<"$pins")")
fi
# repository paths a comment in the URL list names (the URLs themselves are routes); a word
# pair such as "world/entity" is prose unless its first part is in the repository
named="$(g show "HEAD:$URLS" 2>/dev/null | sed -n 's/^[^#]*#//p' |
  grep -oE '(^|[^A-Za-z0-9_./?=:-])[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_.-]+)+' |
  sed -E 's#^[^A-Za-z0-9_]##; s#\.+$##' | sort -u)"
while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  [[ -e "$REPO/${path%%/*}" ]] || committed "${path%%/*}" || continue
  committed "$path" || problems+=("$path, named in $URLS, is not committed$(why_not "$path")")
done <<<"$named"
# a file the demo pages read by a fixed path in the app rather than through the manifest
geometry="$(g show HEAD:apps/api/cairoflow_api/flow.py 2>/dev/null |
  sed -n 's/^GEOMETRY_FILE = "\([^"]*\)"$/\1/p' | head -n 1)"
if [[ -n "$geometry" ]] && ! committed "$geometry"; then
  problems+=("$geometry, which /flow reads by a fixed path, is not committed$(why_not "$geometry")")
fi
if [[ -n "$snapshot" ]] && committed "$snapshot"; then
  problems+=("$snapshot is committed: the World Model view stays out of Git (ADR-033 #9)")
fi
if (( ${#problems[@]} == 0 )); then
  pass demo "$URLS, $LAUNCHER, ${manifest:-?}, the ${#pinned[@]} files it pins${geometry:+, $geometry (read by /flow)} and every path the URL list names are committed"
else
  printf '  %s\n' "${problems[@]}"
  fail demo "a demo URL depends on a file that would not be published, or the World Model view would be"
fi
[[ -n "$snapshot" ]] && info demo "$snapshot is Git-ignored by design (ADR-033 #9); without it the World Model panels say it is not present"

# --- pages ---------------------------------------------------------------------------------------
step pages
if committed tests/test_release_demo.py && [[ -f "$REPO/tests/test_release_demo.py" ]]; then
  if command -v uv >/dev/null; then
    out="$(cd "$REPO" && PYTHONDONTWRITEBYTECODE=1 uv run --frozen --no-sync python -m pytest -q -p no:cacheprovider \
      tests/test_release_demo.py \
      -k 'works_on_a_fresh_clone or inspector_says_the_snapshot' 2>&1)"
    rc=$?
    if (( rc == 0 )); then
      pass pages "every demo URL loads from the tracked files alone, the World Model view absent: $(tail -n 1 <<<"$out")"
    else
      grep -E '^(FAILED|ERROR|E  )' <<<"$out" | head -20
      fail pages "rc=$rc: $(tail -n 1 <<<"$out")"
    fi
  else
    fail pages "uv is needed to run tests/test_release_demo.py"
  fi
else
  not_run pages "tests/test_release_demo.py is not in this checkout"
fi

# --- release -------------------------------------------------------------------------------------
step release
TERMS="${CF_RELEASE_TERMS:-$SCRIPTS/release_terms.txt}"
terms=()
[[ -f "$TERMS" ]] && terms=(--terms "$TERMS")
if [[ -f "$SCRIPTS/release_scan.py" ]]; then
  out="$("${PY[@]}" "$SCRIPTS/release_scan.py" --repo "$REPO" "${terms[@]}" 2>&1)"
  rc=$?
  case "$rc" in
    0) pass release "$(tail -n 1 <<<"$out")" ;;
    3) not_run release "$(tail -n 1 <<<"$out")" ;;
    *) head -n 30 <<<"$out" | sed 's/^/  /'; fail release "rc=$rc: footage or a stray path or term would be published" ;;
  esac
else
  not_run release "scripts/release_scan.py is not in this checkout"
fi

# the verdict names what failed, passed and did not run; git-dir is information, never a pass
echo
parts=()
(( ${#failed[@]} )) && parts+=("failed: ${failed[*]}")
parts+=("passed: ${passed[*]:-none}")
(( ${#skipped[@]} )) && parts+=("not run: ${skipped[*]}")
parts+=("information only: git-dir")
printf -v detail '%s; ' "${parts[@]}"
if (( ${#failed[@]} )); then
  echo "pre-push check of ${HEAD_SHA:0:12}: FAIL (${detail%; }). Nothing was published."
  exit 1
fi
echo "pre-push check of ${HEAD_SHA:0:12}: PASS (${detail%; }). Nothing was published."
