"""The whole flow on synthetic data: decision report traceability (A9) and offline replay (A10).

Inputs come from synthetic_pipeline.py (manifest-only clip, no video): SAM output is a synthetic
stream in a SAM cache record; Muse answers through httpx.MockTransport with a fake key, once,
and is replayed from its cache afterwards. Nothing leaves the machine.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, TypeAdapter
from synthetic_pipeline import FAKE_KEY, MuseStub, refuse, write_inputs

from cairoflow_contracts.config import Settings
from cairoflow_contracts.evidence import TrafficWorldModel
from cairoflow_contracts.experiment import EvaluationResult, ExperimentManifest
from cairoflow_contracts.reasoning import EvidenceBundle, ReasoningResult, ValidationOutcome
from cairoflow_contracts.report import BaselineAssessment, DecisionReport, ReportNumber
from cairoflow_evaluation.compare import paired_comparison, planned_improvement_tests
from cairoflow_evaluation.pipeline import (
    RECORD_FILE,
    PipelineError,
    PipelineRecord,
    compare_records,
    run_pipeline,
)
from cairoflow_evaluation.report import (
    REPRODUCIBILITY_KEYS,
    assemble_report,
    render_markdown,
    select_candidate,
    trace_report,
)
from cairoflow_reasoning.muse_adapter import MuseCacheMiss

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("pipeline")
    inputs = write_inputs(root)
    stub = MuseStub()
    live = run_pipeline(
        inputs,
        root / "live",
        mode="live",
        settings=Settings(model_api_key=SecretStr(FAKE_KEY)),
        spend_limit_usd=1.0,
        sam_transport=httpx.MockTransport(refuse),
        muse_transport=httpx.MockTransport(stub),
    )
    reference = run_pipeline(inputs, root / "reference", mode="replay")
    return root, inputs, stub, live, reference


def _load(run_dir: Path):
    world = TrafficWorldModel.model_validate_json((run_dir / "world_model.json").read_text())
    outcomes = TypeAdapter(tuple[ValidationOutcome, ...]).validate_json(
        (run_dir / "outcomes.json").read_text()
    )
    experiment = ExperimentManifest.model_validate_json(
        (run_dir / "experiment/experiment_manifest.json").read_text()
    )
    evaluation = EvaluationResult.model_validate_json(
        (run_dir / "experiment/evaluation.json").read_text()
    )
    report = DecisionReport.model_validate_json((run_dir / "report.json").read_text())
    return world, outcomes, experiment, evaluation, report


def _trace(run_dir: Path, **overrides) -> tuple[str, ...]:
    world, outcomes, experiment, evaluation, report = _load(run_dir)
    values = {
        "report": report,
        "world_model": world,
        "outcomes": outcomes,
        "experiment": experiment,
        "evaluation": evaluation,
        "experiment_dir": run_dir / "experiment",
        "bundle": EvidenceBundle.model_validate_json((run_dir / "bundle.json").read_text()),
        "reasoning": ReasoningResult.model_validate_json((run_dir / "reasoning.json").read_text()),
    } | overrides
    report = values.pop("report")
    return trace_report(report, **values)


# --- the flow itself ------------------------------------------------------------------------


def test_the_flow_runs_from_sam_cache_to_report(runs) -> None:
    root, _, stub, live, reference = runs
    assert stub.requests == 1  # Muse was called once, in the live run only
    world, outcomes, experiment, evaluation, report = _load(root / "reference")
    counted = {(m.approach, m.exit_arm): m.count_veh for m in world.movements if m.count_veh}
    assert counted == {("S", "N"): 3, ("S", "W"): 1, ("W", "E"): 1}
    assert [(o.intervention_id, o.status) for o in outcomes] == [
        ("I1", "accepted"),
        ("I2", "accepted"),
        ("I3", "rejected"),
    ]
    assert experiment.valid and [c.intervention_id for c in experiment.candidates] == ["I1", "I2"]
    assert {c.candidate for c in evaluation.comparisons} == {"I1", "I2"}
    assert reference.selected_intervention_id == report.selected_intervention_id
    assert len(reference.tripinfo) == 5 * 2 + 5 * 2  # baseline runs and reruns, 2 candidates
    assert set(live.artifacts) == set(reference.artifacts)
    # The clip is a manifest only: no pixels, so no overlay, and the record says why.
    assert reference.review_artifacts == {}
    assert reference.notes[0].startswith("no overlay video")
    assert reference.clip_synthetic


# --- A9: every number traces to its source -----------------------------------------------------


def test_every_report_number_traces_to_its_source(runs) -> None:
    root = runs[0]
    world, outcomes, experiment, evaluation, report = _load(root / "reference")
    assert _trace(root / "reference") == ()
    sources = {n.source for n in report.numbers}
    assert sources == {"settings", "world_model", "validated_input", "comparison"}
    assert all(n.kind != "model_generated" for n in report.numbers)
    flows = [n for n in report.numbers if n.refs[0] == "world_model:movement:SN:flow_veh_per_h"]
    assert flows[0].value == pytest.approx(3 * 3600 / 70)
    assert {r for r in flows[0].refs if r.startswith("track:")} == {
        f"track:{t.track_id}" for m in world.movements if m.approach + m.exit_arm == "SN"
        for t in m.tracks
    }  # fmt: skip
    assert (report.selected_intervention_id, report.selection_reason) == select_candidate(
        evaluation
    )
    rejected = next(c for c in report.candidates if c.intervention_id == "I3")
    assert rejected.number_ids == () and "outside 5-90 s" in rejected.validator_reasons[0]

    markdown = (root / "reference" / "report.md").read_text("utf-8")
    assert markdown == render_markdown(report)
    assert all(f"[{n.number_id}]" in markdown for n in report.numbers)
    assert "model-generated text, not evidence" in markdown


def test_tampering_breaks_the_trace(runs, tmp_path: Path) -> None:
    root = runs[0]
    world, _, _, evaluation, _ = _load(root / "reference")

    changed = evaluation.metrics[0].model_copy(update={"value": evaluation.metrics[0].value + 1})
    metrics = (changed, *evaluation.metrics[1:])
    problems = _trace(
        root / "reference", evaluation=evaluation.model_copy(update={"metrics": metrics})
    )
    # the stored value is compared with one recomputed from the SUMO file it cites
    assert any(p.startswith("evaluation: metric baseline/") and "recomputed" in p
               for p in problems)  # fmt: skip

    movement = next(m for m in world.movements if m.count_veh)
    edited = world.model_copy(
        update={
            "movements": tuple(
                m.model_copy(update={"count_veh": m.count_veh + 1}) if m is movement else m
                for m in world.movements
            )
        }
    )
    assert any("count_veh gives" in p for p in _trace(root / "reference", world_model=edited))

    # the selection, its improvement tests and the recommendation are re-derived as well
    _, _, _, _, report = _load(root / "reference")
    claimed = report.model_copy(update={"recommended_intervention_id": "I1"})
    assert "the recommendation does not follow from the baseline assessment" in _trace(
        root / "reference", report=claimed
    )
    forged = evaluation.model_copy(
        update={"improvement_tests": tuple(
            t.model_copy(update={"confirmed": True}) for t in evaluation.improvement_tests
        )}
    )  # fmt: skip
    assert "the improvement tests do not follow from the comparisons" in _trace(
        root / "reference", evaluation=forged
    )
    # a recommendation in an evaluation from before the rules is refused, not skipped
    old = evaluation.model_copy(update={"rules_version": None})
    assert "a recommendation from an evaluation without recommendation rules" in _trace(
        root / "reference", report=claimed, evaluation=old
    )

    # the assessment must be about this experiment's baseline, and its evidence unchanged
    _, _, experiment, _, _ = _load(root / "reference")
    (tmp_path / "evidence.json").write_text("{}", encoding="utf-8")
    assessment = BaselineAssessment(
        baseline_manifest_sha256="0" * 64, status="validated", summary="s", findings=(),
        evidence={"evidence.json": "1" * 64}, assessed_on="2026-10-28", documented_in="d",
    )  # fmt: skip
    problems = _trace(
        root / "reference",
        report=report.model_copy(update={"baseline_assessment": assessment}),
        evidence_root=tmp_path,
    )
    assert "the baseline assessment is about another baseline" in problems
    assert "baseline assessment evidence evidence.json is missing or changed" in problems
    same = assessment.model_copy(
        update={"baseline_manifest_sha256": experiment.baseline_manifest_sha256}
    )
    assert "the baseline assessment is about another baseline" not in _trace(
        root / "reference", report=report.model_copy(update={"baseline_assessment": same})
    )

    copy = tmp_path / "run"
    shutil.copytree(root / "reference", copy)
    tripinfo = next(copy.rglob("seed_1/tripinfo.xml"))
    tripinfo.write_text(
        tripinfo.read_text("utf-8").replace('timeLoss="', 'timeLoss="9', 1), "utf-8"
    )
    assert any("changed" in p for p in _trace(copy))


def _forged_run(root: Path, tmp_path: Path, forge) -> Path:
    """A copy of the reference run whose report.json is replaced by `forge(report, inputs)`."""
    copy = tmp_path / "forged"
    shutil.copytree(root / "reference", copy)
    world, outcomes, experiment, evaluation, report = _load(copy)
    bundle = EvidenceBundle.model_validate_json((copy / "bundle.json").read_text())
    reasoning = ReasoningResult.model_validate_json((copy / "reasoning.json").read_text())
    inputs = {"world_model": world, "bundle": bundle, "reasoning": reasoning,
              "outcomes": outcomes, "experiment": experiment, "evaluation": evaluation}  # fmt: skip
    forged = forge(report, inputs)
    (copy / "report.json").write_text(forged.model_dump_json(indent=2), "utf-8")
    return copy


def test_a_consistent_forgery_of_a_metric_fails_the_trace(runs, tmp_path: Path) -> None:
    """The audit's experiment (D2): candidate I1's mean delay is forged to the baseline's minus
    25 s on every seed, the comparisons, Holm tests and report are rebuilt from the forged
    values, and every cited file keeps its hash. The old trace passed it and selected I1."""
    root = runs[0]
    world, outcomes, experiment, evaluation, report = _load(root / "reference")
    base = {m.seed: m.value for m in evaluation.metrics
            if m.arm == "baseline" and m.metric_name == "mean_delay_s"}  # fmt: skip
    metrics = tuple(
        m.model_copy(update={"value": base[m.seed] - 25})
        if m.arm == "I1" and m.metric_name == "mean_delay_s" else m
        for m in evaluation.metrics
    )  # fmt: skip
    comparisons = tuple(
        paired_comparison(
            c.candidate,
            [m for m in metrics if m.arm == "baseline" and m.metric_name == c.metric_name],
            [m for m in metrics if m.arm == c.candidate and m.metric_name == c.metric_name],
            c.shared_state_seeds,
        )
        for c in evaluation.comparisons
    )
    forged = evaluation.model_copy(update={"metrics": metrics, "comparisons": comparisons})
    forged = forged.model_copy(update={"improvement_tests": planned_improvement_tests(forged)})
    bundle = EvidenceBundle.model_validate_json((root / "reference/bundle.json").read_text())
    reasoning = ReasoningResult.model_validate_json((root / "reference/reasoning.json").read_text())
    rebuilt = assemble_report(
        world_model=world, bundle=bundle, reasoning=reasoning, outcomes=outcomes,
        experiment=experiment, evaluation=forged,
        artifacts={k: v for k, v in report.reproducibility.items()
                   if k not in REPRODUCIBILITY_KEYS},
        now=report.created_at_utc,
    )  # fmt: skip
    problems = _trace(root / "reference", report=rebuilt, evaluation=forged)
    assert any(p.startswith("evaluation: metric I1/1/mean_delay_s changed: stored")
               for p in problems)  # fmt: skip
    assert any("differs from the report assembled again" in p for p in problems)
    assert _trace(root / "reference") == ()  # the honest run still traces


def test_edits_a_report_check_used_to_miss_fail_the_trace(runs, tmp_path: Path) -> None:
    """Each edit the audit's reviewers made to a report without the old trace noticing (D2)."""
    root = runs[0]

    def edit_number(report: DecisionReport, pick, **update) -> DecisionReport:
        number = next(n for n in report.numbers if pick(n))
        numbers = tuple(n.model_copy(update=update) if n is number else n for n in report.numbers)
        return report.model_copy(update={"numbers": numbers})

    comparison = lambda n: n.source == "comparison"  # noqa: E731
    edits = {
        "kind simulated -> observed": lambda r, _: edit_number(r, comparison, kind="observed"),
        "label and unit rewritten": lambda r, _: edit_number(
            r, comparison, label="annual fuel saved", unit="litres"
        ),
        "appended number": lambda r, _: r.model_copy(update={"numbers": (*r.numbers, ReportNumber(
            number_id=f"n{len(r.numbers) + 1}", label="Estimated annual fuel saved, litres",
            value=r.numbers[0].value, unit="count", kind="assumed", source="settings",
            refs=r.numbers[0].refs))}),
        "every verdict set to improves": lambda r, _: r.model_copy(update={"candidates": tuple(
            c.model_copy(update={"verdicts": dict.fromkeys(c.verdicts, "improves")})
            for c in r.candidates)}),
        "limitation appended": lambda r, _: r.model_copy(update={"limitations": (
            *r.limitations, "38% cut in travel time and 61% fewer conflicts across Cairo.")}),
        "objective rewritten": lambda r, _: r.model_copy(
            update={"objective": "J = w1 T + w2 Q + w3 D + w4 C, minimised."}
        ),
    }  # fmt: skip
    for name, forge in edits.items():
        run = _forged_run(root, tmp_path / name.replace(" ", "_"), forge)
        assert _trace(run) != (), name

    # count tracks borrowed from another movement: the flow keeps its value, its evidence lies
    world, *_ = _load(root / "reference")
    other = next(m for m in world.movements if m.count_veh and m.approach + m.exit_arm != "SN")
    borrowed = tuple(f"track:{t.track_id}" for t in other.tracks)

    def borrow(r: DecisionReport, _) -> DecisionReport:
        return edit_number(
            r,
            lambda n: n.refs[0] == "world_model:movement:SN:count_veh",
            refs=("world_model:movement:SN:count_veh", *borrowed),
        )

    problems = _trace(_forged_run(root, tmp_path / "borrowed", borrow))
    assert any("not the tracks counted in the movement" in p for p in problems)


def test_a_missing_sumo_output_fails_the_trace(runs, tmp_path: Path) -> None:
    root = runs[0]
    copy = tmp_path / "run"
    shutil.copytree(root / "reference", copy)
    next(copy.rglob("candidates/I1/runs/seed_2/queue.xml")).unlink()
    problems = _trace(copy)
    assert len(problems) == 1 and "cannot be recomputed" in problems[0]


# --- A10: offline replay ------------------------------------------------------------------------


def _replay_script():
    spec = importlib.util.spec_from_file_location("replay_script", REPO / "scripts" / "replay.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_replay_reproduces_every_hash(runs, capsys) -> None:
    root, inputs, stub, _, reference = runs
    reference_file = root / "reference" / RECORD_FILE
    status = _replay_script().main(
        [str(inputs), str(root / "again"), "--reference", str(reference_file)]
    )
    assert status == 0, capsys.readouterr().out
    assert "identical to the reference" in capsys.readouterr().out
    again = PipelineRecord.model_validate_json((root / "again" / RECORD_FILE).read_text())
    assert compare_records(reference, again) == ()
    assert stub.requests == 1  # replay never called the stub


def test_replay_reports_mismatches_and_refuses_bad_references(runs, capsys) -> None:
    root, inputs, _, live, reference = runs
    tampered = reference.model_copy(
        update={"artifacts": reference.artifacts | {"world_model.json": "0" * 64}}
    )
    path = root / "tampered_record.json"
    path.write_text(tampered.model_dump_json(indent=2), "utf-8")
    status = _replay_script().main([str(inputs), str(root / "again2"), "--reference", str(path)])
    assert status == 1
    assert "MISMATCH artifact world_model.json differs" in capsys.readouterr().out
    with pytest.raises(PipelineError, match="replay mode"):
        compare_records(live, reference)


def test_replay_without_the_model_cache_fails_offline(runs, tmp_path: Path) -> None:
    root, inputs, _, _, _ = runs
    copy = tmp_path / "inputs"
    shutil.copytree(inputs.parent, copy, ignore=shutil.ignore_patterns("muse"))
    with pytest.raises(MuseCacheMiss):
        run_pipeline(copy / inputs.name, tmp_path / "run", mode="replay")
