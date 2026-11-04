#!/usr/bin/env bash
# Fresh-clone reproduction: what a reviewer gets from `git clone`, checked end to end.
#
#   bash scripts/fresh_clone_check.sh                 a clone of this checkout's committed HEAD
#   bash scripts/fresh_clone_check.sh --remote URL    a clone of URL's default branch instead,
#                                                     e.g. the repository you pushed to
#
# Clones the COMMITTED HEAD (uncommitted changes are not in it) into a temp dir, so none of this
# checkout's Git-ignored data comes along: no .env, no data/raw, no data/processed, no runs, no
# WorldView snapshot. Every step runs inside the clone, in an environment without API keys:
#   clone    HEAD only; asserts that nothing Git-ignored is present
#   secrets  the full history (every branch and tag) scanned; key NAMES only, never values
#   install  uv sync --frozen
#   suite    pytest
#   pages    every report page (/demo, /flow, ...) and the WorldView inspector (/world, its entity
#            pages) through the app test client with the snapshot absent: each loads, and the
#            inspector says in one line that the snapshot is not present
#            (tests/test_release_demo.py)
#   launcher the README's third command: bash scripts/demo_run.sh --check starts the server on
#            the clone and opens every demo URL with curl; the web app is not built here, so its
#            pages answer with their build instructions
#   release  a public tree (its demo manifest lets footage be absent) holds no footage and
#            nothing unexpected at the top level, and no term of $RELEASE_TERMS if that exists
#            (scripts/release_scan.py); any other tree is reported as not judged
#   docs     LICENSE is MIT, NOTICE names CC BY-SA 3.0 and the Meta outputs, README has the
#            three-command reproduction, no public page says "731 observed"
#   fetch    scripts/fetch_ngsim_data.py: camera gate inputs restored from their committed
#            record and the NGSIM trajectories downloaded (about 54 MB, data.transportation.gov);
#            each accepted only if its SHA-256 equals its pin, then --check
#   build    scripts/build_ngsim_int2_scenario.py at the recorded time with --check-record:
#            every rebuilt output's SHA-256 equals the committed record's
#   worldview scripts/build_worldview_snapshot.py on the rebuilt world model, then
#            demo_run.sh --check again: the view must equal the launcher's pin and every /flow
#            URL show the entity and zoom it asks for
# Env: CF_FRESH_KEEP=1 keeps the clone; CF_RELEASE_TERMS is the terms file of the release step
# (default: scripts/release_terms.txt beside this script, if there is one); CF_FRESH_CAMERA2=1
# also fetches and checks the camera 2 video (about 449 MB; without it --check reports that one
# file missing and nothing else);
# CF_FRESH_STOP_AFTER=clone (tests) stops after the clone step with exit 3, never 0.
# With --remote, a user:token@ part or a query string of the URL is masked in everything
# printed and in the clone's own config. The clone uses no stored login: no prompt, no askpass
# program, no credential helper (a token in the URL is used, never stored), ssh in batch mode,
# at most 900 s, so a remote a reviewer cannot clone fails here too.
# Exit 0 only if every step passes, 1 if one fails, 2 on a usage or setup error. Nothing is
# written outside the temp dir, and nothing is ever sent to a remote.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAPSHOT="data/demo/ngsim-int2-worldview.json"
AS_OF="2026-10-28T07:43:04Z"  # the recorded build time (data/geometry/ngsim-lankershim-int2)
README_COMMANDS=("uv sync --frozen" "uv run --frozen pytest -q" "bash scripts/demo_run.sh")
RELEASE_TERMS="${CF_RELEASE_TERMS:-$REPO/scripts/release_terms.txt}"

die() { echo "fresh_clone: $*" >&2; exit 2; }
# user:token@host -> ***@host (to the last @ before the path), and any query string of a URL
mask() { sed -E 's#(://)[^/[:space:]]*@#\1***@#g; s#(://[^?[:space:]]*)\?[^[:space:]]*#\1?***#g'; }
USAGE="usage: bash scripts/fresh_clone_check.sh [--remote URL]"

# Arguments first: a usage error leaves nothing behind.
REMOTE=""
while (( $# )); do
  case "$1" in
    --remote)
      (( $# >= 2 )) && [[ -n "$2" ]] || die "--remote needs a URL ($USAGE)"
      REMOTE="$2"
      shift 2 ;;
    --remote=*)
      REMOTE="${1#--remote=}"
      [[ -n "$REMOTE" ]] || die "--remote needs a URL ($USAGE)"
      shift ;;
    -h|--help) echo "$USAGE"; exit 0 ;;
    *) die "unknown argument: $(mask <<<"$1") ($USAGE)" ;;
  esac
done
command -v git >/dev/null || die "git is needed"
command -v uv >/dev/null || die "uv is needed"

LOCAL_SHA="$(git -C "$REPO" rev-parse HEAD 2>/dev/null)" || LOCAL_SHA=""
if [[ -z "$REMOTE" ]]; then
  [[ -n "$LOCAL_SHA" ]] || die "not a Git checkout: $REPO"
  HEAD_SHA="$LOCAL_SHA"
  WHAT="${HEAD_SHA:0:12}"
else
  SHOWN="$(mask <<<"$REMOTE")"
  HEAD_SHA=""  # the remote's, once cloned
fi
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cairoflow-fresh-XXXXXX")"
CLONE="$WORK/clone"
cleanup() { [[ "${CF_FRESH_KEEP:-}" == "1" ]] && echo "kept: $CLONE" || rm -rf "$WORK"; }
trap cleanup EXIT

# The environment of a reviewer: no API keys, no virtualenv of this checkout, no project overrides.
KEEP_VARS=(HOME LANG LC_ALL TERM TMPDIR USER UV_CACHE_DIR XDG_CACHE_HOME XDG_DATA_HOME
           UV_PYTHON_INSTALL_DIR SSL_CERT_FILE http_proxy https_proxy no_proxy HTTP_PROXY
           HTTPS_PROXY NO_PROXY)
CLEAN_PATH="$(tr ':' '\n' <<<"$PATH" | grep -v -e '/\.venv/bin$' | paste -sd: -)"
ENV=(env -i "PATH=$CLEAN_PATH")
for name in "${KEEP_VARS[@]}"; do
  [[ -n "${!name:-}" ]] && ENV+=("$name=${!name}")
done
in_clone() { (cd "$CLONE" && "${ENV[@]}" "$@"); }
py() { in_clone uv run --frozen --no-sync python "$@"; }

failed=()
pass() { echo "PASS $1: $2"; }
fail() { echo "FAIL $1: $2"; failed+=("$1"); }
step() { echo; echo "== $1 =="; }

# --- clone ---------------------------------------------------------------------------------------
step clone
if [[ -n "$REMOTE" ]]; then
  # The reviewer's view of the pushed repository: its default branch, with no stored login. No
  # terminal prompt, no askpass program (an empty GIT_ASKPASS also skips core.askPass and
  # SSH_ASKPASS), no credential helper (so a token in the URL is used but never stored), ssh in
  # batch mode, and a time limit: an inaccessible remote fails instead of waiting.
  if ! out="$(GIT_TERMINAL_PROMPT=0 GIT_ASKPASS='' SSH_ASKPASS='' \
      GIT_SSH_COMMAND='ssh -o BatchMode=yes' timeout 900 \
      git -c credential.helper= -c core.askPass= clone --quiet --no-checkout -- "$REMOTE" "$CLONE" 2>&1)"; then
    mask <<<"$out" >&2
    die "git clone of $SHOWN failed"
  fi
  git -C "$CLONE" remote set-url origin "$SHOWN"  # no credential left in the clone's config
  HEAD_SHA="$(git -C "$CLONE" rev-parse --verify --quiet 'HEAD^{commit}')" \
    || die "$SHOWN has no commit on its default branch"
  WHAT="$SHOWN at ${HEAD_SHA:0:12}"
  branch="$(git -C "$CLONE" symbolic-ref --quiet --short HEAD || echo "a detached HEAD")"
  if [[ "$HEAD_SHA" == "$LOCAL_SHA" ]]; then
    echo "remote $SHOWN: $branch at ${HEAD_SHA:0:12}, the same commit as this checkout's HEAD"
  else
    echo "remote $SHOWN: $branch at ${HEAD_SHA:0:12}, not this checkout's HEAD (${LOCAL_SHA:0:12})"
  fi
else
  [[ -z "$(git -C "$REPO" status --porcelain --untracked-files=no)" ]] \
    || echo "note: this checkout has uncommitted changes; the clone has HEAD ${HEAD_SHA:0:12} only"
  git clone --quiet --no-local --no-checkout "$REPO" "$CLONE" || die "git clone failed"
fi
git -C "$CLONE" -c advice.detachedHead=false checkout --quiet --detach "$HEAD_SHA" \
  || die "checkout of $HEAD_SHA failed"
ignored="$(git -C "$CLONE" status --porcelain --ignored | grep '^!!' || true)"
extra="$(find "$CLONE/data/raw" "$CLONE/data/processed" "$CLONE/experiments/runs" -mindepth 1 \
  ! -name .gitkeep 2>/dev/null | head -5)"
if [[ -n "$ignored$extra" ]] || [[ -e "$CLONE/.env" ]] || [[ -e "$CLONE/$SNAPSHOT" ]]; then
  fail clone "Git-ignored data present in the clone: ${ignored}${extra}"
else
  pass clone "HEAD ${HEAD_SHA:0:12}, $(git -C "$CLONE" ls-files | wc -l) tracked files, nothing Git-ignored, no .env, no $SNAPSHOT"
fi
if [[ "${CF_FRESH_STOP_AFTER:-}" == "clone" ]]; then  # tests only: never reported as a pass
  echo "fresh clone of $WHAT: STOPPED after the clone step (CF_FRESH_STOP_AFTER=clone)"
  (( ${#failed[@]} )) && exit 1
  exit 3
fi

# --- install -------------------------------------------------------------------------------------
step install
if in_clone uv sync --frozen --quiet; then
  pass install "uv sync --frozen ($(py -c 'import sys; print(sys.version.split()[0])'))"
else
  fail install "uv sync --frozen failed"
  echo "FAIL: ${failed[*]} (nothing else can run without the environment)"
  exit 1
fi
if py -c 'import os, sys; sys.exit(any("API_KEY" in k for k in os.environ))'; then
  pass env "no *API_KEY* variable, no .env: what a reviewer without credentials has"
else
  fail env "an API key variable reached the clone's environment"
fi

# --- secrets -------------------------------------------------------------------------------------
step secrets
if out="$(py scripts/fresh_clone_scan.py --repo "$CLONE" 2>&1)"; then
  pass secrets "$(tr '\n' ' ' <<<"$out")"
else
  echo "$out"
  fail secrets "the full history holds credential-like text (names above, values not printed)"
fi

# --- suite ---------------------------------------------------------------------------------------
step suite
out="$(py -m pytest -q -p no:cacheprovider 2>&1)"
rc=$?
summary="$(tail -n 1 <<<"$out")"
if (( rc == 0 )); then
  pass suite "$summary"
else
  grep -E '^(FAILED|ERROR)' <<<"$out" | head -20
  fail suite "rc=$rc: $summary"
fi

# --- pages (snapshot absent) ---------------------------------------------------------------------
step pages
[[ -e "$CLONE/$SNAPSHOT" ]] && fail pages "$SNAPSHOT exists in the clone"
# Every report page on the clone, and the inspector's one line.
out="$(py -m pytest -q -p no:cacheprovider tests/test_release_demo.py \
  -k 'works_on_a_fresh_clone or inspector_says_the_snapshot' 2>&1)"
rc=$?
if (( rc == 0 )); then
  pass pages "/demo and the inspector load without the snapshot and say it is not present: $(tail -n 1 <<<"$out")"
else
  grep -E '^(FAILED|ERROR|E  )' <<<"$out" | head -20
  fail pages "rc=$rc: $(tail -n 1 <<<"$out")"
fi

# --- launcher ------------------------------------------------------------------------------------
step launcher
out="$(in_clone bash scripts/demo_run.sh --check 2>&1)"
rc=$?
grep -E '^(ok|FAIL|WorldView)' <<<"$out"
if (( rc == 0 )); then
  pass launcher "bash scripts/demo_run.sh --check on the clone: $(tail -n 1 <<<"$out")"
else
  tail -n 20 <<<"$out"
  fail launcher "rc=$rc: the README's third command does not serve the demo on the clone"
fi

# --- release -------------------------------------------------------------------------------------
step release
terms=()
[[ -f "$RELEASE_TERMS" ]] && terms=(--terms "$RELEASE_TERMS")
out="$(py scripts/release_scan.py --repo "$CLONE" "${terms[@]}" 2>&1)"
rc=$?
case "$rc" in
  0) pass release "$(tail -n 1 <<<"$out")" ;;
  3) echo "INFO release: not run: $(tail -n 1 <<<"$out")" ;;
  *) head -n 30 <<<"$out"; fail release "rc=$rc: footage or a stray path or term in the tree" ;;
esac

# --- docs ----------------------------------------------------------------------------------------
step docs
problems=()
head -n 1 "$CLONE/LICENSE" 2>/dev/null | grep -qx 'MIT License' || problems+=("LICENSE is not the MIT licence")
for term in "CC BY-SA 3.0" "Meta Model API"; do
  grep -qF "$term" "$CLONE/NOTICE" 2>/dev/null || problems+=("NOTICE does not name '$term'")
done
for cmd in "${README_COMMANDS[@]}"; do
  grep -qF "$cmd" "$CLONE/README.md" || problems+=("README.md lacks '$cmd'")
done
said="$(grep -lE '731 observed' "$CLONE/README.md" "$CLONE"/docs/DEMO*.md 2>/dev/null || true)"
[[ -z "$said" ]] || problems+=("'731 observed' in: ${said//$CLONE\//}")
if (( ${#problems[@]} == 0 )); then
  pass docs "LICENSE MIT; NOTICE names CC BY-SA 3.0 and the Meta Model API outputs; README has the three commands"
else
  fail docs "$(printf '%s; ' "${problems[@]}")"
fi

# --- fetch ---------------------------------------------------------------------------------------
step fetch
fetched=1
py scripts/fetch_ngsim_data.py --restore-camera-inputs || fetched=0
py scripts/fetch_ngsim_data.py --download trajectories || fetched=0
if [[ "${CF_FRESH_CAMERA2:-}" == "1" ]]; then
  py scripts/fetch_ngsim_data.py --download camera2 || fetched=0
fi
check="$(py scripts/fetch_ngsim_data.py --check 2>&1)"
rc=$?
echo "$check"
not_ok="$(grep -v -E ': ok$' <<<"$check" || true)"
if [[ "${CF_FRESH_CAMERA2:-}" != "1" ]]; then
  not_ok="$(grep -v -E '^camera2: missing ' <<<"$not_ok" || true)"
  (( rc == 3 || rc == 0 )) && rc=0
fi
if (( fetched )) && (( rc == 0 )) && [[ -z "$not_ok" ]]; then
  pass fetch "every fetched or restored input matches its pinned SHA-256$([[ "${CF_FRESH_CAMERA2:-}" == "1" ]] || echo ' (camera 2 video not fetched: CF_FRESH_CAMERA2=1)')"
else
  fail fetch "an input is missing or differs from its pin: ${not_ok:-download failed}"
fi

# --- build ---------------------------------------------------------------------------------------
step build
if (( fetched )); then
  out="$(py scripts/build_ngsim_int2_scenario.py --as-of "$AS_OF" --check-record 2>&1)"
  rc=$?
  grep '^record check' <<<"$out"
  if (( rc == 0 )) && grep -qx 'record check: every output matches the committed record' <<<"$out"; then
    pass build "rebuilt at $AS_OF: every output's SHA-256 matches the committed record"
    built=1
  else
    tail -n 15 <<<"$out"
    fail build "rc=$rc: the rebuild differs from the committed record"
  fi
else
  fail build "not run: the trajectories were not fetched"
fi

# --- worldview: the World Model view from the rebuilt world model -------------------------------
step worldview
if (( ${built:-0} )); then
  out="$(py scripts/build_worldview_snapshot.py 2>&1)"
  rc=$?
  tail -n 1 <<<"$out"
  if (( rc == 0 )); then
    out="$(in_clone bash scripts/demo_run.sh --check 2>&1)"  # refuses a view that is not its pin
    rc=$?
    grep -E '^(ok|FAIL|World Model view|demo_run)' <<<"$out"
    if (( rc == 0 )); then
      pass worldview "$SNAPSHOT rebuilt on the clone equals the launcher's pin; every demo URL shows the entity and zoom it asks for"
    else
      fail worldview "rc=$rc: the rebuilt view is not the pinned one, or a demo URL does not show what it asks for"
    fi
  else
    tail -n 15 <<<"$out"
    fail worldview "rc=$rc: scripts/build_worldview_snapshot.py failed"
  fi
else
  fail worldview "not run: the world model was not rebuilt"
fi

echo
if (( ${#failed[@]} )); then
  echo "fresh clone of $WHAT: FAIL (${failed[*]})"
  exit 1
fi
echo "fresh clone of $WHAT: PASS (clone, install, secrets, suite, pages, launcher, release, docs, fetch, build, worldview)"
