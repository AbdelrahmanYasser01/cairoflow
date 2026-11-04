#!/usr/bin/env bash
# One command for the CairoFlow demo: the product server on committed records, an offline replay
# of them plus the Git-ignored World Model view when it is present.
#
#   bash scripts/demo_run.sh           serve the demo on 127.0.0.1:${CF_DEMO_PORT:-8000} and list
#                                      the URLs to open, in order
#   bash scripts/demo_run.sh --check   start it on a free port, open every URL in
#                                      scripts/demo_urls.txt, require 200, no error page and, for
#                                      a /flow URL with ?e= or ?zoom=, that entity and zoom shown
#                                      (the app quietly falls back to its default panel), stop
#
# The server (cairoflow_api.serve) answers the web app, /api/v1 and the report pages. Those read
# the files the demo manifest pins, each checked against its SHA-256, and the World Model view
# $SNAPSHOT, which is Git-ignored (ADR-033 #9) and in no manifest: the app ties it only to the
# world-model, geometry and bundle hashes the view itself declares. So this launcher pins it: when
# present its SHA-256 must be SNAPSHOT_SHA256 below, the view scripts/build_worldview_snapshot.py
# writes from the world model the baseline replays, or nothing is served. Without it the /flow and
# /world pages say so and still load. Nothing here calls a model or runs a simulation; the server
# keeps its rebuildable index under var/, which Git ignores.
# Env: CF_DEMO_PORT (serve mode), CF_DEMO_RUNS (runs root, default experiments/runs),
# CF_DEMO_URLS (the URL list, default scripts/demo_urls.txt).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

MANIFEST="data/demo/ngsim-int2-no-footage.json"
URLS_FILE="${CF_DEMO_URLS:-scripts/demo_urls.txt}"
SNAPSHOT="data/demo/ngsim-int2-worldview.json"
# build_worldview_snapshot.py from world model 09452b7b604be76c (projection 0.1.0, view 48d9aff9)
SNAPSHOT_SHA256="b88ff8080e7ba32d7e39fb36af5422d794f830a5621702a222fab01b76826941"
RUNS="${CF_DEMO_RUNS:-experiments/runs}"
# Any claim of immediacy on a report page (those replay committed records). The web app has an
# analysis mode that runs a real job while you watch, so its pages are not scanned;
# tests/test_release_demo.py checks this pattern against the same phrases as its own.
BANNED='\breal[- ]?time\b|\blive\b|\bstream(ing|ed)\b'
HOST="127.0.0.1"

die() { echo "demo_run: $*" >&2; exit 1; }

[[ -f "$MANIFEST" ]] || die "$MANIFEST is missing"
[[ -f "$URLS_FILE" ]] || die "$URLS_FILE is missing"
if [[ -f "$SNAPSHOT" ]]; then
  found="$(sha256sum "$SNAPSHOT")"
  found="${found%% *}"
  [[ "$found" == "$SNAPSHOT_SHA256" ]] || die "$SNAPSHOT has SHA-256 $found, not the" \
    "pinned $SNAPSHOT_SHA256: its numbers are not the recorded ones. Move it away, then run" \
    "scripts/build_worldview_snapshot.py (or serve without it)"
fi
mkdir -p "$RUNS"
mapfile -t URLS < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//' "$URLS_FILE" | grep -v '^$')
(( ${#URLS[@]} > 0 )) || die "$URLS_FILE lists no URLs"

banner() {
  echo "CairoFlow demo: an offline replay of committed records (pinned by SHA-256 in $MANIFEST)."
  echo "Nothing is called or simulated while it runs. Decision support only: nothing here controls real traffic."
  if [[ ! -f "$SNAPSHOT" ]]; then
    echo "WorldView snapshot not present - run scripts/build_worldview_snapshot.py"
  else
    echo "World Model view: $SNAPSHOT (Git-ignored, built on this machine), SHA-256 ${SNAPSHOT_SHA256:0:16} as pinned."
  fi
}

serve() {  # serve PORT: the server in the foreground of this process
  exec uv run --frozen python -m cairoflow_api.serve --host "$HOST" --port "$1" \
    --runs-root "$RUNS" --demo "$MANIFEST" --repo-root .
}

if [[ "${1:-}" != "--check" ]]; then
  [[ $# -eq 0 ]] || die "usage: bash scripts/demo_run.sh [--check]"
  PORT="${CF_DEMO_PORT:-8000}"
  banner
  echo "Open, in order:"
  for url in "${URLS[@]}"; do echo "  http://$HOST:$PORT$url"; done
  serve "$PORT"
fi

# --- --check: every URL of the recording answers 200 with no error page -------------------------
query_value() {  # query_value URL KEY: the percent-decoded value of KEY in URL's query, or nothing
  [[ "$1" == *\?* ]] || return 0
  local pair value
  IFS='&' read -ra pairs <<<"${1#*\?}"
  for pair in "${pairs[@]}"; do
    [[ "${pair%%=*}" == "$2" ]] || continue
    value="${pair#*=}"
    value="${value//+/ }"
    printf '%b' "${value//%/\\x}"
  done
}

shown() {  # shown PAGE KEY: what a /flow page shows, the panel's entity (e) or the zoom marked on
  local flat
  flat="$(tr '\n' ' ' <"$1")"
  case "$2" in
    e) grep -o '<div class="panel"><h3>[^<]*</h3> *<p><code>[^<]*</code>' <<<"$flat" \
         | head -n 1 | sed -e 's/.*<code>//' -e 's/<\/code>$//' || true ;;
    zoom) grep -o 'View: .*' <<<"$flat" | grep -o '<a class="on" href="[^"]*">[^<]*</a>' \
         | head -n 1 | sed -e 's/<\/a>$//' -e 's/.*>//' || true ;;
  esac
}

view_problem() {  # view_problem URL PAGE: why the page does not show what the URL asks for
  local key want got
  for key in e zoom; do
    want="$(query_value "$1" "$key")"
    [[ -n "$want" ]] || continue
    got="$(shown "$2" "$key")"
    [[ -z "$got" && ! -f "$SNAPSHOT" ]] && continue  # no view without the snapshot: said above
    if [[ "$got" != "$want" ]]; then
      echo "asked for $key=$want, the page shows ${got:-no World Model view}"
      return
    fi
  done
}

report_page() {  # report_page URL: is it one of the report pages, as opposed to the web app?
  case "${1%%\?*}" in
    /demo*|/flow*|/world*|/problem*|/decision*|/runs*) return 0 ;;
  esac
  return 1
}

command -v curl >/dev/null || die "curl is needed for --check"
PORT="$(uv run --frozen python -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
WORK="$(mktemp -d)"
LOG="$WORK/server.log"
serve "$PORT" >"$LOG" 2>&1 &
SERVER=$!
stop() {
  pkill -TERM -P "$SERVER" 2>/dev/null || true
  kill "$SERVER" 2>/dev/null || true
  wait "$SERVER" 2>/dev/null || true
  rm -rf "$WORK"
}
trap stop EXIT

banner
BASE="http://$HOST:$PORT"
for _ in $(seq 1 120); do
  kill -0 "$SERVER" 2>/dev/null || { cat "$LOG" >&2; die "the server exited before it answered"; }
  [[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE${URLS[0]}" || true)" == "200" ]] && break
  sleep 0.5
done

failed=0
for url in "${URLS[@]}"; do
  meta="$(curl -s -o "$WORK/body" -w '%{http_code} %{content_type}' "$BASE$url" || echo "000 -")"
  code="${meta%% *}"
  kind="${meta#* }"
  problem=""
  if [[ "$code" != "200" ]]; then
    problem="HTTP $code"
  elif [[ "$kind" == text/html* ]]; then
    if grep -q -e 'Traceback (most recent call last)' -e 'Internal Server Error' "$WORK/body"; then
      problem="error page"
    elif report_page "$url" && grep -q -i -E "$BANNED" "$WORK/body"; then
      problem="a banned claim of immediacy (the demo is an offline replay)"
    else
      problem="$(view_problem "$url" "$WORK/body")"
    fi
  elif [[ ! -s "$WORK/body" ]]; then
    problem="empty $kind"
  fi
  if [[ -n "$problem" ]]; then
    echo "FAIL $url: $problem"
    failed=1
  else
    echo "ok   $url ($kind)"
  fi
done

if (( failed )); then
  echo "server log:" >&2
  tail -n 40 "$LOG" >&2
  exit 1
fi
echo "ok: ${#URLS[@]} URLs served"
