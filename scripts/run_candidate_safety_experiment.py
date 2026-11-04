"""Per-candidate safety record for the stored Muse candidates, replayed offline.

    uv run --frozen python scripts/run_candidate_safety_experiment.py            # runs/ only
    uv run --frozen python scripts/run_candidate_safety_experiment.py --record   # + new record

The candidates are I1-I3 of the committed Muse reply (prompt 0.1.0, ADR-020), replayed from
cache/muse with no API key and in replay mode: no request can leave the machine, and a cache miss
is recorded as a finding, never answered by a live call. The evidence bundle is rebuilt as
scripts/run_ngsim_int2_experiment.py builds it for prompt 0.1.0, from the committed scenario
record and the world model and cut baseline it names (each checked against its recorded hash).

Each proposal goes through `cairoflow_simulation.safety` (validator verdict > compile > plain XML
hashes > demand hashes on every seed > one-seed SUMO smoke > identical rerun), under a new
`experiments/runs/candidate-safety-<stamp>/`, whose `candidate_safety.json` is the sidecar.
D1 (= EQ-1) and D2 are listed as closed negative results (ADR-033) and are never re-run.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cairoflow_contracts.config import Settings
from cairoflow_contracts.evidence import TrafficWorldModel
from cairoflow_contracts.reasoning import EvidenceBundle
from cairoflow_reasoning.evidence import build_evidence_bundle
from cairoflow_reasoning.muse_adapter import MuseCacheMiss, MuseClient, request_key
from cairoflow_simulation.candidates import load_baseline
from cairoflow_simulation.geometry_compiler import CompiledNetwork
from cairoflow_simulation.safety import (
    SAFETY_FILE,
    SafetyRecord,
    build_safety_record,
    closed_result,
)
from cairoflow_simulation.validation import CheckedProposal, check_proposals

REPO = Path(__file__).resolve().parents[1]
RECORDS_DIR = REPO / "experiments/records"
REPLY_PROMPT_VERSION = "0.1.0"  # the committed reply (ADR-020)
REPLY_MAX_OUTPUT_TOKENS = 7000  # as recorded for it (scripts/run_ngsim_int2_experiment.py)
CLOSED_RESULTS = (
    ("D1", "EQ-1 equal-saturation allocation: no support on held-out seeds 201-220",
     ("experiments/records/ladder-preregistration-20261103T001537Z.json",
      "experiments/records/ladder-evaluation-20261103T001603Z.json")),
    ("D2", "D2 search: no admissible plan among 80 timing moves",
     ("experiments/records/d2-search-20261102T232051Z.json",
      "experiments/records/d2-plan-20261102T232051Z.json")),
)  # fmt: skip


def _int2_runner():
    """scripts/run_ngsim_int2_experiment.py, for the inputs and limitations it sends."""
    path = REPO / "scripts/run_ngsim_int2_experiment.py"
    spec = importlib.util.spec_from_file_location("run_ngsim_int2_experiment", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault("run_ngsim_int2_experiment", module)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class StoredReply:
    network: CompiledNetwork
    bundle: EvidenceBundle
    baseline_dir: Path
    checked: tuple[CheckedProposal, ...]
    source: dict[str, str]
    findings: tuple[str, ...]


def replay_stored_reply(cache_dir: Path | None = None) -> StoredReply:
    """The committed reply's proposals, validated against the cut baseline; offline only."""
    int2 = _int2_runner()
    cache_dir = cache_dir or int2.MUSE_CACHE
    scenario = json.loads(int2.RECORD.read_text(encoding="utf-8"))
    world_path = REPO / scenario["processed_outputs"]["dir"] / "world_model.json"
    baseline_dir = REPO / scenario["baseline"]["run_dir"]
    if _sha256(world_path) != scenario["processed_outputs"]["sha256"]["world_model.json"]:
        raise ValueError(f"{world_path} does not match the scenario record")
    if _sha256(baseline_dir / "baseline_manifest.json") != scenario["baseline"]["manifest_sha256"]:
        raise ValueError(f"{baseline_dir} does not match the scenario record")
    world = TrafficWorldModel.model_validate_json(world_path.read_text(encoding="utf-8"))
    _, network = load_baseline(baseline_dir)
    bundle = build_evidence_bundle(world, network.manifest, int2.LIMITATIONS,
                                   end_of_red_per_lane=True, conventions=())  # fmt: skip
    settings = Settings()  # no API key: replay cannot send, whatever .env holds
    client = MuseClient(settings, cache_dir, "replay", prompt_version=REPLY_PROMPT_VERSION,
                        max_output_tokens=REPLY_MAX_OUTPUT_TOKENS)  # fmt: skip
    key = request_key(client.request_body(bundle))
    source = {
        "label": "model_generated proposals, replayed offline",
        "mode": "replay",
        "model": settings.muse_model,
        "prompt_version": REPLY_PROMPT_VERSION,
        "request_key": key,
        "bundle_id": bundle.bundle_id,
        "cache_file": client.cache_path(key).name,
    }
    try:
        reasoning = client.diagnose(bundle)
    except MuseCacheMiss as miss:
        finding = f"cache miss: {miss} (key {key[:16]}); no live request was made"
        return StoredReply(network, bundle, baseline_dir, (), source, (finding,))
    source["cache_file_sha256"] = _sha256(client.cache_path(key))
    checked = check_proposals(reasoning.output.interventions, network, bundle,
                              now=datetime.now(UTC).replace(microsecond=0))  # fmt: skip
    return StoredReply(network, bundle, baseline_dir, checked, source, ())


def build(out_dir: Path, *, now: datetime, cache_dir: Path | None = None) -> SafetyRecord:
    """Replay, check every proposal and write the sidecar under `out_dir` (new)."""
    replay = replay_stored_reply(cache_dir)
    closed = tuple(closed_result(rid, finding, evidence, REPO)
                   for rid, finding, evidence in CLOSED_RESULTS)  # fmt: skip
    return build_safety_record(replay.checked, replay.baseline_dir, out_dir, now=now,
                               source=replay.source, closed=closed,
                               findings=replay.findings)  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--record", action="store_true", help="also write a new committed record")
    args = parser.parse_args(argv)
    now = datetime.now(UTC).replace(microsecond=0)
    out = REPO / "experiments/runs" / f"candidate-safety-{now:%Y%m%dT%H%M%SZ}"
    record = build(out, now=now)
    for c in record.candidates:
        steps = " > ".join(f"{s.step} {s.status}" for s in c.steps)
        print(f"{c.intervention_id} ({c.validator_status}, cycle {c.cycle_s} s): {steps}; "
              f"timings {', '.join(f'{k} {v:.2f}' for k, v in c.timings_s.items())}")  # fmt: skip
    for closed in record.closed:
        print(f"{closed.result_id}: {closed.status}: {closed.finding}")
    for finding in record.findings:
        print(f"finding: {finding}")
    print(f"sidecar: {(out / SAFETY_FILE).relative_to(REPO)}; safe: {record.safe}")
    if args.record:
        path = RECORDS_DIR / f"candidate-safety-{now:%Y%m%dT%H%M%SZ}.json"
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write((out / SAFETY_FILE).read_text(encoding="utf-8"))
        print(f"record: {path.relative_to(REPO)}")
    return 0 if record.safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
