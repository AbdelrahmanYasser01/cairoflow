"""What recurs across the committed Muse reply and its replicates: write a muse-recurrence record.

    uv run --frozen python scripts/muse_recurrence.py RECORD [RECORD ...]

Each RECORD is a status-"ok" record of scripts/run_muse_replicates.py. The committed reply
(replicate 1, the one the demo pins) is always included, so n = 1 + the records given. Each
reply is read from its stored cache file, checked against the SHA-256 its record holds and
against the one request key, and parsed with the adapter's own validation. The summary is
computed by cairoflow_evaluation.muse_recurrence (code only) and written to a NEW
experiments/records/muse-recurrence-<stamp>.json. No network access and no model calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cairoflow_contracts.meta_depth import ReplyRef
from cairoflow_evaluation.muse_recurrence import recurrence
from cairoflow_reasoning.muse_adapter import MuseRequestRecord, extract_output, parse_output

REPO = Path(__file__).resolve().parents[1]
MUSE_KEY = "ddfb8029b1e8810e65b2ad986e0da99be630b86e6d7c86669007e2a197ef47ad"
COMMITTED = f"cache/muse/{MUSE_KEY}.json"


def main(argv: list[str] | None = None, *, repo: Path = REPO, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", type=Path, nargs="+")
    args = parser.parse_args(argv)
    sources = [(1, COMMITTED, None)]
    for path in args.records:
        record = json.loads((repo / path).read_text("utf-8"))
        if record.get("status") != "ok" or record.get("request_key") != MUSE_KEY:
            print(f"{path}: not an ok reply to the committed request", file=sys.stderr)
            return 2
        sources.append((record["replicate"], record["cache"]["path"], record["cache"]["sha256"]))
    if len({n for n, _, _ in sources}) != len(sources):
        print("a replicate is given twice", file=sys.stderr)
        return 2
    replies = []
    bundle_id = prompt_version = None
    for n, rel, expected in sorted(sources):
        data = (repo / rel).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if expected is not None and digest != expected:
            print(f"{rel} differs from its record", file=sys.stderr)
            return 2
        stored = MuseRequestRecord.model_validate_json(data)
        if stored.request_key != MUSE_KEY or stored.replicate != n:
            print(f"{rel} is not replicate {n} of the committed request", file=sys.stderr)
            return 2
        response = json.loads(stored.response_text)
        output = parse_output(extract_output(response))
        bundle_id, prompt_version = stored.bundle_id, stored.prompt_version
        ref = ReplyRef(reply_id=f"r{n}", replicate=n, cache_path=rel, cache_sha256=digest,
                       response_id=response.get("id"))  # fmt: skip
        replies.append((ref, output))
    result = recurrence(replies, request_key=MUSE_KEY, prompt_version=prompt_version or "",
                        bundle_id=bundle_id or "")  # fmt: skip
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    out = repo / "experiments/records" / f"muse-recurrence-{now:%Y%m%dT%H%M%SZ}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(result.model_dump_json(indent=2) + "\n")
    print(f"n = {result.n_replies} replies; {len(result.hypotheses)} hypothesis clusters "
          f"(in replies: {[c.in_replies for c in result.hypotheses]}); "
          f"{len(result.evidence)} evidence ids cited")  # fmt: skip
    for e in result.evidence:
        print(f"  {e.evidence_id}: cited {e.cited_in}/{result.n_replies}, support "
              f"{e.supporting_in}, against {e.contradicting_in}")  # fmt: skip
    print(f"record: {out.relative_to(repo).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
