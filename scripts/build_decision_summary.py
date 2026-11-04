"""Write data/sample/decision_summary.json from the records the demo pins (no model, no simulator).

    uv run --frozen python scripts/build_decision_summary.py [--out PATH]

Every value is copied by code from a record whose SHA-256 the manifest or the meta-depth pin file
holds; a missing or changed pin stops the build. The output has no wall-clock field, so the same
records always give the same bytes. The pixel-bearing pins (the SAM overlay and its record, the
annotated frame) are not read: neither this build nor the page that shows it needs a video.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from cairoflow_api.demo import (
    SAM_FILES,
    SAM_REVIEW,
    Demo,
    DemoError,
    DemoManifest,
    PinnedFile,
    load_demo,
    read_manifest,
)
from cairoflow_api.meta_depth import PINS_FILE, RECURRENCE_ROLE, MetaDepthPins
from cairoflow_contracts.decision_summary import (
    CandidateView,
    Citation,
    DecisionSummary,
    DirectionDisagreement,
    EnsembleCandidateMember,
    EnsembleCandidateVerdict,
    EnsembleMember,
    EnsembleSummary,
    HypothesisRecurrence,
    HypothesisView,
    RecordedOutcome,
    SimulatedMetric,
    SimulationScope,
    SourceFile,
)
from cairoflow_contracts.meta_depth import MuseRecurrence
from cairoflow_contracts.reasoning import Hypothesis, ValidationOutcome

REPO = Path(__file__).resolve().parents[1]
MANIFEST_FILE = "data/demo/ngsim-int2-ensemble-control.json"
OUT_FILE = "data/sample/decision_summary.json"
NOT_READ = (*SAM_FILES, SAM_REVIEW)  # step 0 of the demo: pixels, and nothing the decision used
MUSE_NOTICE = "cache/muse/NOTICE.md"
KNOWN_SLIP = "**Known slip:** "


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned(repo: Path, pin: PinnedFile) -> bytes:
    path = repo / pin.path
    if not path.is_file():
        raise DemoError(f"{pin.path} is missing")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != pin.sha256:
        raise DemoError(f"{pin.path} differs from its pin")
    return data


def known_slips(repo: Path) -> tuple[str, ...]:
    """Mistakes the reply's own notice records. Model text is copied word for word, so the
    correction has to travel beside it."""
    lines = (repo / MUSE_NOTICE).read_text("utf-8").splitlines()
    found = [line.partition(KNOWN_SLIP)[2] for line in lines if KNOWN_SLIP in line]
    return tuple(f"Known slip in the model text: {text}" for text in found)


def citations_of(
    hypothesis: Hypothesis, demo: Demo, recurrence: MuseRecurrence | None
) -> tuple[Citation, ...]:
    items = {item.evidence_id: item for item in demo.bundle.items} if demo.bundle else {}
    counts = {item.evidence_id: item for item in recurrence.evidence} if recurrence else {}
    cited = [("supporting", e) for e in hypothesis.supporting_evidence_ids]
    cited += [("contradicting", e) for e in hypothesis.contradicting_evidence_ids]
    return tuple(
        Citation(
            evidence_id=evidence_id,
            stance=stance,
            kind=items[evidence_id].kind if evidence_id in items else None,
            summary=items[evidence_id].summary if evidence_id in items else None,
            recurrence=counts.get(evidence_id),
        )
        for stance, evidence_id in cited
    )


def recurrence_of(
    hypothesis: Hypothesis, reply_id: str | None, recurrence: MuseRecurrence | None
) -> HypothesisRecurrence | None:
    """The cluster holding this hypothesis of the pinned reply, when the record has one."""
    if recurrence is None or reply_id is None:
        return None
    for cluster in recurrence.hypotheses:
        wanted = (reply_id, hypothesis.hypothesis_id)
        if wanted in {(m.reply_id, m.hypothesis_id) for m in cluster.members}:
            return HypothesisRecurrence(
                cluster_id=cluster.cluster_id,
                in_replies=cluster.in_replies,
                n_replies=recurrence.n_replies,
                shared_evidence_ids=cluster.shared_evidence_ids,
            )
    return None


def hypotheses_of(
    demo: Demo, reply_id: str | None, recurrence: MuseRecurrence | None
) -> tuple[HypothesisView, ...]:
    grounding = {g.hypothesis_id: g for g in demo.grounding.hypotheses} if demo.grounding else {}
    return tuple(
        HypothesisView(
            hypothesis=hypothesis,
            citations=citations_of(hypothesis, demo, recurrence),
            grounding=grounding.get(hypothesis.hypothesis_id),
            recurrence=recurrence_of(hypothesis, reply_id, recurrence),
        )
        for hypothesis in demo.reasoning.hypotheses
    )


def candidates_of(demo: Demo) -> tuple[CandidateView, ...]:
    grounding = {g.intervention_id: g for g in demo.grounding.proposals} if demo.grounding else {}
    outcomes = {
        v["intervention_id"]: ValidationOutcome.model_validate(v)
        for v in demo.records["proposals"]["validation"]
    }
    eligibility = {e["candidate"]: e for e in demo.records["results"]["eligibility"]}
    views = []
    for proposal in demo.reasoning.interventions:
        found = eligibility.get(proposal.intervention_id, {})
        views.append(
            CandidateView(
                intervention_id=proposal.intervention_id,
                proposal=proposal,
                grounding=grounding.get(proposal.intervention_id),
                validator=outcomes.get(proposal.intervention_id),
                eligibility_status=found.get("status"),
                eligibility_reasons=tuple(found.get("reasons", ())),
            )
        )
    return tuple(views)


def metrics_of(results: dict) -> tuple[SimulatedMetric, ...]:
    tests = results["improvement_tests"]
    tested = {(t["candidate"], t["metric_name"]): t["confirmed"] for t in tests}
    return tuple(
        SimulatedMetric(
            candidate=c["candidate"],
            metric_name=c["metric_name"],
            unit=c["unit"],
            better=c["better"],
            n_seeds=len(c["seeds"]),
            baseline_mean=c["baseline_mean"],
            candidate_mean=c["candidate_mean"],
            mean_difference=c["mean_difference"],
            sd_difference=c["sd_difference"],
            ci95_difference=tuple(c["ci95_difference"]),
            verdict=c["verdict"],
            improvement_confirmed=tested.get((c["candidate"], c["metric_name"])),
        )
        for c in results["comparisons"]
    )


def ensemble_of(record: dict) -> EnsembleSummary:
    verdict = record["verdict"]
    members = tuple(
        EnsembleMember(
            member=m["id"],
            version=m["version"],
            required=m["required"],
            baseline_assessment=m["baseline_assessment"],
            timeline_id=m["timeline"]["timeline_id"],
            timeline_sha256=m["timeline"]["sha256"],
            gate_passed=m["gate"]["passed"],
        )
        for m in record["members"]
    )
    candidates = tuple(
        EnsembleCandidateVerdict(
            candidate=c["candidate"],
            verdict=c["verdict"],
            reason=c["reason"],
            supported_on=tuple(c["supported_on"]),
            not_supported_on=tuple(c["not_supported_on"]),
            common_confirmed=tuple(c["common_confirmed"]),
            robust_harm=tuple(c["robust_harm"]),
            direction_disagreements=tuple(
                DirectionDisagreement(
                    metric_name=d["metric_name"],
                    verdicts=d["verdicts"],
                    mean_difference=d["mean_difference"],
                    ci95_difference={k: tuple(v) for k, v in d["ci95_difference"].items()},
                    opposite=d["opposite"],
                )
                for d in c["direction_disagreements"]
            ),
            members=tuple(
                EnsembleCandidateMember(
                    member=m["member"],
                    required=m["required"],
                    supported=m["supported"],
                    blocked_by=tuple(m["blocked_by"]),
                    status=m["status"],
                    worsens=tuple(m["worsens"]),
                    confirmed=tuple(m["confirmed"]),
                    held_off=tuple(m["held_off"]),
                    mean_delay_difference_s=m.get("mean_delay_difference_s"),
                    mean_delay_ci95_s=(
                        tuple(m["mean_delay_ci95_s"]) if m.get("mean_delay_ci95_s") else None
                    ),
                )
                for m in c["members"]
            ),
        )
        for c in verdict["candidates"]
    )
    return EnsembleSummary(
        rule=verdict["rule"],
        rule_version=verdict["rule_version"],
        members=members,
        candidates=candidates,
        robust_selection=verdict["robust_selection"],
        selection_reason=verdict["selection_reason"],
        recommended=verdict["recommended"],
        recommendation=verdict["recommendation"],
    )


def build_summary(repo: Path = REPO) -> DecisionSummary:
    manifest_path = repo / MANIFEST_FILE
    full = read_manifest(manifest_path)
    kept = {role: pin for role, pin in full.files.items() if role not in NOT_READ}
    manifest = DemoManifest.model_validate({**full.model_dump(), "files": kept})
    demo = load_demo(manifest, repo)  # every kept pin, and the links between the records
    pins_path = repo / PINS_FILE
    pins = MetaDepthPins.model_validate_json(pins_path.read_text("utf-8"))
    recurrence_pin = pins.files.get(RECURRENCE_ROLE)
    recurrence = (
        MuseRecurrence.model_validate_json(pinned(repo, recurrence_pin)) if recurrence_pin else None
    )
    reply_id = None
    if recurrence is not None:
        reply_sha256 = manifest.files["muse_reply"].sha256
        reply_id = next(
            (r.reply_id for r in recurrence.replies if r.cache_sha256 == reply_sha256), None
        )
    results = demo.records["results"]
    report = results["report"]
    selected = report["selected_intervention_id"]
    ensemble = demo.records.get("ensemble")
    sources = [
        SourceFile(role="demo_manifest", path=MANIFEST_FILE, sha256=sha256_of(manifest_path)),
        SourceFile(role="meta_depth_pins", path=PINS_FILE, sha256=sha256_of(pins_path)),
        *(SourceFile(role=r, path=p.path, sha256=p.sha256) for r, p in manifest.files.items()),
    ]
    sources.append(
        SourceFile(role="muse_notice", path=MUSE_NOTICE, sha256=sha256_of(repo / MUSE_NOTICE))
    )
    if recurrence_pin:
        sources.append(
            SourceFile(role=RECURRENCE_ROLE, path=recurrence_pin.path, sha256=recurrence_pin.sha256)
        )
    return DecisionSummary(
        demo_id=manifest.demo_id,
        title=manifest.title,
        outcome=RecordedOutcome(
            outcome="selected" if selected else "none_selected",
            selected_intervention_id=selected,
            selection_reason=report["selection_reason"],
            recommended_intervention_id=report["recommended_intervention_id"],
            recommendation=report["recommendation"],
        ),
        baseline=demo.assessment,
        hypotheses=hypotheses_of(demo, reply_id, recurrence),
        candidates=candidates_of(demo),
        simulation=SimulationScope(
            run_id=Path(results["run_dir"]).name,
            created_at_utc=results["created_at_utc"],
            seeds=tuple(results["seeds"]),
            replay_window_s=tuple(results["replay_window_s"]),
            baseline_manifest_sha256=results["baseline_manifest_sha256"],
        ),
        metrics=metrics_of(results),
        ensemble=ensemble_of(ensemble) if ensemble else None,
        assumptions=tuple(ensemble["assumptions"]) if ensemble else (),
        limitations=(
            *(ensemble["limitations"] if ensemble else ()),
            *demo.records["proposals"]["evidence_bundle"]["extra_limitations"],
            *known_slips(repo),
        ),
        sources=tuple(sources),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=REPO)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    out = args.out or args.repo_root / OUT_FILE
    summary = build_summary(args.repo_root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(summary.model_dump_json(indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}: {summary.outcome.outcome}, {len(summary.metrics)} simulated comparisons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
