"""Compare independent Muse Spark runs of one evidence bundle (P1-2). No model call.

    uv run --frozen python scripts/compare_muse_runs.py RECORD [RECORD ...] [--record]

Each RECORD is a committed summary of scripts/run_cairoflow.py (one replicate each, the same
request sent again). Read from the run directories it names: the reply (reasoning.json), the
grounding report and the outcomes. Reported, per run and across runs, by code only:
- the leading diagnosis (the first hypothesis' limiting factor, prompt 0.3.0) and whether runs
  agree on it; every hypothesis' factor, claim type, confidence and grounding status;
- overlap of the supporting evidence the leading hypotheses cite (pairwise Jaccard);
- the proposals: which phases each changes and in which direction from the cut cycle, and
  which survived grounding, validation and simulation;
- model uncertainty (confidence labels, disagreement between runs) kept apart from missing
  input evidence (what the replies say the bundle lacks).
The summary holds ids, categories and counts, no model-written prose. With --record it goes to
experiments/records/muse-cross-run-<stamp>.json.
"""

from __future__ import annotations

import argparse
import itertools
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from cairoflow_contracts.reasoning import (
    EvidenceBundle,
    GroundingReport,
    ReasoningResult,
    ValidationOutcome,
)

REPO = Path(__file__).resolve().parents[1]


def jaccard(a: set[str], b: set[str]) -> float:
    return round(len(a & b) / len(a | b), 4) if a | b else 1.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("records", nargs="+", type=Path)
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args(argv)
    runs = []
    for path in args.records:
        summary = json.loads(path.read_text(encoding="utf-8"))
        run = REPO / summary["run_dir"]
        reasoning = ReasoningResult.model_validate_json((run / "reasoning.json").read_text())
        bundle = EvidenceBundle.model_validate_json((run / "bundle.json").read_text())
        grounding = GroundingReport.model_validate_json((run / "grounding.json").read_text())
        outcomes = TypeAdapter(tuple[ValidationOutcome, ...]).validate_json(
            (run / "outcomes.json").read_text()
        )
        runs.append((path, summary, reasoning, bundle, grounding, outcomes))
    bundles = {bundle.bundle_id for *_, bundle, _, _ in runs}
    keys = {r.request_key for _, _, r, *_ in runs}
    phases = {p.phase_name: p.duration_s for p in runs[0][3].phases}

    per_run = []
    for path, summary, reasoning, _bundle, grounding, outcomes in runs:
        status = {g.hypothesis_id: g for g in grounding.hypotheses}
        changes = {}
        for p in reasoning.output.interventions:
            changes[p.intervention_id] = {
                c.phase_name: ("longer" if c.green_s > phases.get(c.phase_name, c.green_s)
                               else "shorter" if c.green_s < phases.get(c.phase_name, c.green_s)
                               else "same")
                for c in p.phase_changes
            }  # fmt: skip
        verdicts = {}
        for c in summary["comparisons"]:
            if c["metric_name"] in ("mean_delay_s", "throughput_veh_per_h"):
                verdicts[f"{c['candidate']}:{c['metric_name']}"] = c["verdict"]
        per_run.append({
            "record": str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path),
            "replicate": summary["muse"]["replicate"],
            "request_id": reasoning.request_id,
            "cost_usd": reasoning.estimated_cost_usd,
            "usage": reasoning.usage,
            "leading_factor": reasoning.output.hypotheses[0].limiting_factor,
            "hypotheses": [{"id": h.hypothesis_id, "limiting_factor": h.limiting_factor,
                            "claim_type": h.claim_type, "confidence": h.confidence_label,
                            "supporting": list(h.supporting_evidence_ids),
                            "contradicting": list(h.contradicting_evidence_ids),
                            "grounding": status[h.hypothesis_id].status,
                            "flags": list(status[h.hypothesis_id].flags),
                            "missing_evidence_items": len(h.missing_evidence)}
                           for h in reasoning.output.hypotheses],
            "proposal_phase_changes": changes,
            "validation": {o.intervention_id: o.status for o in outcomes},
            "simulated_verdicts": verdicts,
            "selected": summary["report"]["selected"],
            "recommended": summary["report"]["recommended"],
            "integrity": summary["integrity"]["status"],
        })  # fmt: skip

    leading = Counter(r["leading_factor"] for r in per_run)
    lead_support = [set(r["hypotheses"][0]["supporting"]) for r in per_run]
    pairs = {f"{a + 1}-{b + 1}": jaccard(lead_support[a], lead_support[b])
             for a, b in itertools.combinations(range(len(per_run)), 2)}  # fmt: skip
    factors = [{h["limiting_factor"] for h in r["hypotheses"]} for r in per_run]
    changed = [{(ph, d) for ch in r["proposal_phase_changes"].values() for ph, d in ch.items()}
               for r in per_run]  # fmt: skip
    missing = Counter(m for _, _, r, *_ in runs for h in r.output.hypotheses
                      for m in h.missing_evidence)  # fmt: skip
    comparison = {
        "script": "scripts/compare_muse_runs.py",
        "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "label": "model output compared by code; counts and categories only",
        "same_bundle": len(bundles) == 1, "bundle_ids": sorted(bundles),
        "same_request": len(keys) == 1,
        "runs": per_run,
        "agreement": {
            "leading_factor_counts": dict(leading),
            "leading_factor_unanimous": len(leading) == 1,
            "leading_support_jaccard": pairs,
            "factors_in_every_run": sorted(set.intersection(*factors)) if factors else [],
            "factors_in_any_run": sorted(set.union(*factors)) if factors else [],
            "phase_directions_in_every_run": sorted(map(list, set.intersection(*changed))),
            "phase_directions_in_any_run": sorted(map(list, set.union(*changed))),
        },
        "uncertainty": {
            "model": "confidence labels per hypothesis, and disagreement between runs on the "
            "leading factor and on the proposed directions (above)",
            "confidence_labels": dict(Counter(h["confidence"] for r in per_run
                                              for h in r["hypotheses"])),  # fmt: skip
            "input_evidence": "items the replies name as missing from the bundle, by how many "
            "hypotheses name them (text kept in the run directories)",
            "missing_evidence_mentions": len(missing),
            "missing_evidence_named_by_more_than_one_hypothesis": sum(
                1 for count in missing.values() if count > 1
            ),
        },
        "total_cost_usd": round(sum(r["cost_usd"] for r in per_run), 6),
    }  # fmt: skip
    text = json.dumps(comparison, indent=2) + "\n"
    if args.record:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = REPO / "experiments/records" / f"muse-cross-run-{stamp}.json"
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(f"wrote {path.relative_to(REPO)}")
    print(json.dumps({k: comparison[k] for k in ("same_bundle", "same_request", "agreement",
                                                  "total_cost_usd")}, indent=2))  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
