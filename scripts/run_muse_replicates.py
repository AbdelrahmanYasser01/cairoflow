"""Send the demo's committed Muse request again, unchanged, as new replicates; one record each.

    uv run --frozen python scripts/run_muse_replicates.py --replicates 2 3 4 --spend-limit USD
    uv run --frozen python scripts/run_muse_replicates.py --replicates 2 3 4 --replay

The request is the one the demo pins (cache/muse/<MUSE_KEY>.json: prompt 0.1.0, its committed
effort, max_output_tokens and store setting). Its evidence bundle is read back from the stored
body (cairoflow_api.demo.bundle_of), the body is rebuilt by prompts.build_request_body with those
parameters, and nothing is sent unless the rebuilt body is identical to the stored one (same
request key). No prompt is edited. Each replicate n goes through the cache-first adapter as its
own sample (`MuseClient(replicate=n)`, cache file <key>.r<n>.json): ONE request, no retries,
refused once the process total would pass --spend-limit.

Each replicate writes a NEW experiments/records/muse-0.1.0-r<n>-<stamp>.json: status "ok" with
the reply's ids, usage, recorded cost and citation problems, or status "failed" and why (the raw
reply stays under cache/muse/failed/). The replies are model output; nothing here grades them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from cairoflow_api.demo import bundle_of
from cairoflow_contracts.config import Settings, load_settings
from cairoflow_reasoning.muse_adapter import (
    MuseAdapterError,
    MuseCacheMiss,
    MuseClient,
    MuseRequestError,
    MuseRequestRecord,
    MuseResponseError,
    MuseSpendRefused,
    request_key,
)
from cairoflow_reasoning.prompts import build_request_body

REPO = Path(__file__).resolve().parents[1]
MUSE_KEY = "ddfb8029b1e8810e65b2ad986e0da99be630b86e6d7c86669007e2a197ef47ad"
LABEL = (
    "model output (Muse Spark): an independent reply to the demo's committed request, sent "
    "again unchanged; hypotheses and proposals, not evidence and not a measurement"
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def shown(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path)


def committed_request(cache_dir: Path) -> tuple[MuseRequestRecord, dict]:
    reply = MuseRequestRecord.model_validate_json((cache_dir / f"{MUSE_KEY}.json").read_text())
    body = reply.body
    params = {
        "effort": body["reasoning"]["effort"],
        "max_output_tokens": body["max_output_tokens"],
        "store": body.get("store"),
    }
    return reply, params


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
    repo: Path = REPO,
    now: datetime | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replicates", type=int, nargs="+", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--spend-limit", type=float, help="USD cap for this whole process")
    mode.add_argument("--replay", action="store_true", help="cache only; no connection")
    args = parser.parse_args(argv)
    if any(n < 2 for n in args.replicates) or len(set(args.replicates)) != len(args.replicates):
        parser.error("replicates are distinct and start at 2 (1 is the committed reply)")

    cache_dir = repo / "cache/muse"
    records = repo / "experiments/records"
    reply, params = committed_request(cache_dir)
    settings = settings or load_settings(repo / ".env")
    if settings.muse_model != reply.model:
        print(f"MUSE_MODEL is {settings.muse_model!r}, the request used {reply.model!r}",
              file=sys.stderr)  # fmt: skip
        return 2
    bundle = bundle_of(reply)
    body = build_request_body(bundle, reply.model, prompt_version=reply.prompt_version, **params)
    if body != reply.body or request_key(body) != MUSE_KEY:
        print("the rebuilt request differs from the committed one; nothing sent", file=sys.stderr)
        return 2
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    stamp = f"{now:%Y%m%dT%H%M%SZ}"
    outs = {n: records / f"muse-0.1.0-r{n}-{stamp}.json" for n in args.replicates}
    if any(p.exists() for p in outs.values()):
        print("a record of this stamp exists; records are never overwritten", file=sys.stderr)
        return 2

    spent, failures = 0.0, 0
    for n in args.replicates:
        client = MuseClient(
            settings, cache_dir, "replay" if args.replay else "live",
            spend_limit_usd=max(0.0, (args.spend_limit or 0.0) - spent), transport=transport,
            prompt_version=reply.prompt_version, replicate=n, **params,
        )  # fmt: skip
        entry: dict = {
            "script": "scripts/run_muse_replicates.py",
            "created_at_utc": now.isoformat(),
            "label": LABEL,
            "request_key": MUSE_KEY,
            "replicate": n,
            "model": reply.model,
            "prompt_version": reply.prompt_version,
            "parameters": params,
            "bundle_id": bundle.bundle_id,
        }
        try:
            result = client.diagnose(bundle)
        except (MuseSpendRefused, MuseCacheMiss) as error:
            print(f"r{n}: not sent: {error}", file=sys.stderr)
            return 1
        except (MuseRequestError, MuseResponseError) as error:
            failures += 1
            spent += client.spent_usd
            found = sorted((cache_dir / "failed").glob(f"{MUSE_KEY}_*.json"))
            failed = MuseRequestRecord.model_validate_json(found[-1].read_text()) if found else None
            entry |= {
                "status": "failed",
                "failure": str(error),
                "request_id": failed.request_id if failed else None,
                "sent_at_utc": failed.sent_at_utc.isoformat() if failed else None,
                "recorded_cost_usd": failed.estimated_cost_usd if failed else 0.0,
                "cache": {"path": shown(found[-1], repo), "sha256": sha256_file(found[-1])}
                if found
                else None,
            }
            print(f"r{n}: failed: {error}", file=sys.stderr)
        except MuseAdapterError as error:
            print(f"r{n}: {error}", file=sys.stderr)
            return 1
        else:
            spent += client.spent_usd
            path = client.cache_path(MUSE_KEY)
            entry |= {
                "status": "ok",
                "response_id": result.response_id,
                "request_id": result.request_id,
                "sent_at_utc": result.provenance.produced_at_utc.isoformat(),
                "from_cache": result.provenance.cached,
                "recorded_cost_usd": result.estimated_cost_usd,
                "usage": result.usage,
                "hypotheses": len(result.output.hypotheses),
                "interventions": len(result.output.interventions),
                "citation_problems": list(result.problems),
                "cache": {"path": shown(path, repo), "sha256": sha256_file(path)},
            }
            print(f"r{n}: ok, {entry['hypotheses']} hypotheses, {entry['interventions']} "
                  f"proposals, ${result.estimated_cost_usd:.4f}, from cache "
                  f"{result.provenance.cached}")  # fmt: skip
        records.mkdir(parents=True, exist_ok=True)
        with outs[n].open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, indent=2) + "\n")
        print(f"   record: {shown(outs[n], repo)}")
    print(f"spent by this run: ${spent:.4f} (published prices x reported usage, not a bill)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
