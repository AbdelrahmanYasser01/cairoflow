"""Candidate runs, metrics and paired comparison on the synthetic scenario (synthetic_scenario.py).

One baseline (5 seeds) and two accepted signal_timing candidates against an assumed 60 s cycle
(baseline greens E-W 25 s, N-S 25 s): I1 gives N-S 30 s and E-W 20 s, I2 gives N-S 35 s and E-W
15 s. A third proposal is rejected by the validator and must be skipped.
"""

from __future__ import annotations

import json
import shutil
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from synthetic_clip import NOW
from synthetic_scenario import SIGNALS, scenario_geometry, world_model

from cairoflow_contracts.experiment import (
    EvaluationResult,
    ExperimentManifest,
    MetricResult,
    RunSettings,
)
from cairoflow_contracts.reasoning import InterventionProposal, PhaseChange
from cairoflow_evaluation.compare import (
    compute_evaluation,
    evaluate_experiment,
    paired_comparison,
)
from cairoflow_reasoning.evidence import build_evidence_bundle
from cairoflow_simulation.candidates import (
    EXPERIMENT_FILE,
    CandidateError,
    build_candidate_network,
    load_baseline,
    run_candidates,
)
from cairoflow_simulation.geometry_compiler import FILES
from cairoflow_simulation.network import content_sha256
from cairoflow_simulation.runner import run_baseline
from cairoflow_simulation.validation import validate_proposals

SETTINGS = RunSettings(warmup_s=60, horizon_s=300, drain_s=300)
WINDOW = (60.0, 360.0)
DELAYS = [10, 12, 11, 13, 9]


def _proposal(iid: str, ns: float, ew: float, kind: str = "signal_timing") -> InterventionProposal:
    return InterventionProposal(
        intervention_id=iid,
        type=kind,
        target_program="timeline_cycle",
        phase_changes=(
            PhaseChange(phase_name="p3_Ng_Er_Sg_Wr", green_s=ns),
            PhaseChange(phase_name="p0_Nr_Eg_Sr_Wg", green_s=ew),
        ),
        rationale="r",
        expected_tradeoffs=(),
        preconditions=(),
        addresses_hypothesis_ids=("H1",),
        evidence_ids=("mv.SN",),
    )


@pytest.fixture(scope="module")
def experiment(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("m6")
    geo = scenario_geometry()
    model = world_model(geo)
    run_baseline(model, geo, SIGNALS, root / "baseline", SETTINGS, verify_reruns=False, now=NOW)
    _, network = load_baseline(root / "baseline")
    bundle = build_evidence_bundle(model, network.manifest)
    proposals = [
        _proposal("I1", 30, 20),
        _proposal("I2", 35, 15),
        _proposal("I3", 30, 20, kind="route_guidance"),
    ]
    outcomes = validate_proposals(proposals, network, bundle, now=NOW)
    manifest = run_candidates(root / "baseline", outcomes, root / "experiment", now=NOW)
    evaluation = evaluate_experiment(root / "experiment")
    return root, network, outcomes, manifest, evaluation


def test_candidates_change_only_the_green_durations(experiment) -> None:
    root, baseline, outcomes, manifest, _ = experiment
    assert [c.intervention_id for c in manifest.candidates] == ["I1", "I2"]
    i1 = manifest.candidates[0].network
    for name in FILES[:3]:
        assert i1.plain_xml_sha256[name] == baseline.manifest.plain_xml_sha256[name]
    assert i1.net_sha256 != baseline.manifest.net_sha256
    assert i1.signal_program.program_id == "timeline_cycle_I1"
    assert [p.duration_s for p in i1.signal_program.phases] == [20, 3, 2, 30, 3, 2]
    assert [p.state for p in i1.signal_program.phases] == [
        p.state for p in baseline.manifest.signal_program.phases
    ]
    cycle = next(p for p in i1.parameters if p.name == "signal.cycle")
    assert (cycle.value, cycle.provenance) == (60, "synthetic")
    assert "I1" in cycle.source

    embedded = ET.parse(root / "experiment/candidates/I1/network/network.net.xml").getroot()
    logic = embedded.find("tlLogic")
    assert logic is not None and logic.get("programID") == "timeline_cycle_I1"
    assert [float(p.get("duration", 0)) for p in logic.findall("phase")] == [20, 3, 2, 30, 3, 2]

    with pytest.raises(CandidateError, match="not accepted"):
        build_candidate_network(baseline, outcomes[2], root / "unused")


def test_candidates_run_on_the_baselines_seeds_and_demand(experiment) -> None:
    root, _, _, manifest, _ = experiment
    assert manifest.valid and manifest.reasons == ()
    assert manifest.skipped == (
        "I3: type 'route_guidance' is not in the MVP allowlist (ADR-015: signal_timing only)",
    )
    baseline = json.loads((root / "baseline/baseline_manifest.json").read_text("utf-8"))
    baseline_demand = {r["seed"]: r["demand_sha256"] for r in baseline["runs"]}
    for candidate in manifest.candidates:
        assert [r.seed for r in candidate.runs] == [1, 2, 3, 4, 5]
        for run in candidate.runs:
            assert run.demand_sha256 == baseline_demand[run.seed]
            assert (run.teleports, run.collisions, run.unfinished) == (0, 0, 0)
    assert manifest.settings == SETTINGS
    saved = (root / "experiment" / EXPERIMENT_FILE).read_text("utf-8")
    assert ExperimentManifest.model_validate_json(saved) == manifest
    with pytest.raises(FileExistsError):
        run_candidates(root / "baseline", [], root / "experiment")


def test_changed_demand_files_are_refused(experiment, tmp_path: Path) -> None:
    root, _, outcomes, _, _ = experiment
    copy = tmp_path / "baseline"
    shutil.copytree(root / "baseline", copy)
    demand = copy / "demand" / "seed_3.rou.xml"
    demand.write_text(demand.read_text("utf-8").replace('depart="', 'depart="1', 1), "utf-8")
    with pytest.raises(CandidateError, match="seed_3.rou.xml changed"):
        run_candidates(copy, outcomes, tmp_path / "experiment")


def test_metrics_come_from_sumo_outputs_with_their_sources(experiment) -> None:
    root, _, _, manifest, evaluation = experiment
    names = {
        "mean_delay_s",
        "throughput_veh_per_h",
        *(f"{kind}_queue_m.{arm}" for kind in ("max", "mean") for arm in "NSW"),
    }
    assert len(evaluation.metrics) == 3 * 5 * len(names)  # baseline, I1, I2 x 5 seeds
    assert {m.metric_name for m in evaluation.metrics} == names
    for metric in evaluation.metrics:
        assert metric.kind == "simulated" and metric.window_s == WINDOW
        assert metric.experiment_id == manifest.experiment_id
        source = (root / "experiment" / metric.source_output_file).resolve()
        assert content_sha256(source, strip_comments=True) == metric.source_sha256

    # Recompute one delay independently from the tripinfo file.
    delay = next(
        m for m in evaluation.metrics if (m.arm, m.seed, m.metric_name) == ("I1", 2, "mean_delay_s")
    )
    trips = ET.parse(root / "experiment" / delay.source_output_file).getroot()
    values = [
        float(t.get("timeLoss", 0)) + float(t.get("departDelay", 0))
        for t in trips.iter("tripinfo")
        if 60 <= float(t.get("depart", 0)) < 360
    ]
    assert delay.value == pytest.approx(statistics.fmean(values), abs=1e-6)
    assert delay.sample_size == len(values)
    # Red phases make queues; the queue output is read, not assumed empty.
    assert max(m.value for m in evaluation.metrics if m.metric_name == "max_queue_m.S") > 0


def test_comparisons_pair_seeds_and_report_spread(experiment) -> None:
    root, _, _, _, evaluation = experiment
    assert len(evaluation.comparisons) == 2 * 8
    for c in evaluation.comparisons:
        assert c.seeds == (1, 2, 3, 4, 5)
        assert c.differences == pytest.approx(
            [b - a for a, b in zip(c.baseline_values, c.candidate_values, strict=True)], abs=1e-6
        )
        assert c.mean_difference == pytest.approx(statistics.fmean(c.differences), abs=1e-6)
        assert c.sd_difference == pytest.approx(statistics.stdev(c.differences), abs=1e-6)
        half = 2.776 * c.sd_difference / 5**0.5  # t(0.975, 4)
        assert c.ci95_difference == pytest.approx(
            (c.mean_difference - half, c.mean_difference + half), abs=1e-5
        )
    assert compute_evaluation(root / "experiment") == evaluation  # reproducible
    with pytest.raises(FileExistsError):
        evaluate_experiment(root / "experiment")


def _metric(seed: int, value: float, name: str = "mean_delay_s", arm: str = "baseline"):
    return MetricResult(
        experiment_id="e",
        arm=arm,
        seed=seed,
        metric_name=name,
        value=value,
        unit="s" if name == "mean_delay_s" else "veh/h",
        window_s=WINDOW,
        sample_size=10,
        source_output_file="x",
        source_sha256="a" * 64,
        calculation_version="0.1.0",
    )


def test_verdicts_follow_the_interval_and_the_better_direction() -> None:
    base = [_metric(s, v) for s, v in enumerate(DELAYS, start=1)]
    shifts = [-1.9, -1.8, -1.7, -1.6, -1.5]
    paired = enumerate(zip(DELAYS, shifts, strict=True), 1)
    better = [_metric(s, v + d, arm="I1") for s, (v, d) in paired]
    noise = [3, -3, 2, -2, 0]
    paired = enumerate(zip(DELAYS, noise, strict=True), 1)
    mixed = [_metric(s, v + d, arm="I2") for s, (v, d) in paired]
    assert paired_comparison("I1", base, better).verdict == "improves"
    assert paired_comparison("I2", base, mixed).verdict == "inconclusive"

    flow = [_metric(s, 600.0, "throughput_veh_per_h") for s in (1, 2, 3)]
    lower = [_metric(s, 540.0 + s, "throughput_veh_per_h", "I1") for s in (1, 2, 3)]
    comparison = paired_comparison("I1", flow, lower)
    assert (comparison.better, comparison.verdict) == ("higher", "worsens")

    single = paired_comparison("I1", base[:1], better[:1])
    assert (single.sd_difference, single.ci95_difference, single.verdict) == (
        None,
        None,
        "inconclusive",
    )


def _with_run(manifest, copy: Path, candidate: int, seed: int, **update):
    """The manifest with one candidate run replaced, written to `copy`; the candidate is invalid."""
    record = manifest.candidates[candidate]
    runs = tuple(r.model_copy(update=update) if r.seed == seed else r for r in record.runs)
    reasons = tuple(f"seed {r.seed}: {'; '.join(r.reasons)}" for r in runs if not r.valid)
    broken = record.model_copy(update={"runs": runs, "valid": False, "reasons": reasons})
    candidates = list(manifest.candidates)
    candidates[candidate] = broken
    tampered = manifest.model_copy(update={"candidates": tuple(candidates)})
    (copy / EXPERIMENT_FILE).write_text(tampered.model_dump_json(indent=2), "utf-8")


def test_broken_candidates_are_excluded_from_comparison(experiment) -> None:
    root, _, _, manifest, _ = experiment
    copy = root / "experiment_copy"  # a sibling, so the relative baseline path still resolves
    shutil.copytree(root / "experiment", copy, ignore=shutil.ignore_patterns("evaluation.json"))
    _with_run(manifest, copy, 1, 2, teleports=3, valid=False, reasons=("3 teleports",))
    result: EvaluationResult = compute_evaluation(copy)
    assert result.excluded == ("I2 (invalid simulation): seed 2: 3 teleports",)
    assert [(e.candidate, e.status) for e in result.eligibility] == [
        ("I1", "compared"),
        ("I2", "invalid_simulation"),
    ]
    assert {c.candidate for c in result.comparisons} == {"I1"}
    assert {m.arm for m in result.metrics} == {"baseline", "I1"}


def test_incomplete_candidates_are_listed_with_their_unfinished_vehicles(experiment) -> None:
    """A vehicle still in the network at the end makes the candidate incomplete: never
    selectable, compared on throughput and queues only (on every seed), not on mean delay, and
    its unfinished vehicles are named per movement."""
    root, _, _, manifest, _ = experiment
    copy = root / "experiment_incomplete"
    shutil.copytree(root / "experiment", copy, ignore=shutil.ignore_patterns("evaluation.json"))
    tripinfo = copy / "candidates/I1/runs/seed_3/tripinfo.xml"
    text = tripinfo.read_text("utf-8")
    trip = next(t for t in ET.fromstring(text).iter("tripinfo"))
    arrival = f'arrival="{trip.get("arrival")}"'
    tripinfo.write_text(text.replace(arrival, 'arrival="-1.00"', 1), "utf-8")
    record = next(r for r in manifest.candidates[0].runs if r.seed == 3)
    _with_run(
        manifest,
        copy,
        0,
        3,
        arrived=record.arrived - 1,
        unfinished=1,
        valid=False,
        reasons=("1 vehicles unfinished at 660 s",),
        tripinfo_sha256=content_sha256(tripinfo, strip_comments=True),
    )
    result = compute_evaluation(copy)
    eligibility = {e.candidate: e for e in result.eligibility}
    assert eligibility["I1"].status == "incomplete"
    assert eligibility["I1"].run_classes == {1: "complete", 2: "complete", 3: "incomplete",
                                             4: "complete", 5: "complete"}  # fmt: skip
    movement = trip.get("id", "").split(".", 1)[0]
    assert [(m.seed, m.movement, m.vehicles) for m in eligibility["I1"].unfinished] == [
        (3, movement, 1)
    ]
    assert result.excluded == (
        f"I1 (incomplete): seed 3: 1 vehicles still in the network ({movement} 1) and 0 not yet "
        "inserted at 660 s, after a 300 s drain with no new demand",
    )
    names = {c.metric_name for c in result.comparisons if c.candidate == "I1"}
    assert "mean_delay_s" not in names and "throughput_veh_per_h" in names
    assert all(c.seeds == (1, 2, 3, 4, 5) for c in result.comparisons)
    assert not any(m.arm == "I1" and m.metric_name == "mean_delay_s" for m in result.metrics)
    assert result.rules_version == "0.3.1"
    # every simulated candidate's improvement claims are planned; I1's mean delay is untested
    tests = {(t.candidate, t.metric_name): t for t in result.improvement_tests}
    assert {candidate for candidate, _ in tests} == {"I1", "I2"}
    assert tests[("I1", "mean_delay_s")].p_value is None
    assert not tests[("I1", "mean_delay_s")].confirmed


def test_rules_0_3_wiring_reaches_the_comparisons(experiment, monkeypatch) -> None:
    """compute_evaluation hands each arm's shared-state seeds to that arm's maximum-queue
    comparisons only, and records the baseline's entry waits, which make that arm's queue claims
    untested (ADR-024). Guards against unplugging either from the evaluation."""
    import cairoflow_evaluation.compare as compare

    root, *_ = experiment
    plain = compute_evaluation(root / "experiment")
    assert not any(c.verdict == "uninformative" for c in plain.comparisons)
    seeds = plain.comparisons[0].seeds
    monkeypatch.setattr(compare, "shared_state_seeds", lambda *args, **kwargs: {"N": seeds})
    monkeypatch.setattr(
        compare, "run_entry_blocked", lambda runs, *args, **kwargs: {runs[0].seed: {"S": 4.0}}
    )
    result = compute_evaluation(root / "experiment")
    by = {(c.candidate, c.metric_name): c for c in result.comparisons}
    assert by[("I1", "max_queue_m.N")].verdict == "uninformative"
    assert by[("I1", "max_queue_m.N")].shared_state_seeds == seeds
    assert by[("I1", "mean_queue_m.N")].verdict != "uninformative"  # means are never relabelled
    assert result.baseline_entry_blocked == {seeds[0]: {"S": 4.0}}
    assert ("I1", "mean_queue_m.S") in compare.untestable_claims(result)


def _pairing_inputs(root: Path):
    import hashlib

    from cairoflow_contracts.experiment import BaselineManifest

    raw = (root / "baseline/baseline_manifest.json").read_bytes()
    return BaselineManifest.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def test_every_candidate_seed_is_paired_with_the_baseline_run_of_that_seed(experiment) -> None:
    from cairoflow_evaluation.pairing import experiment_pairs

    root, _, _, manifest, _ = experiment
    baseline, member = _pairing_inputs(root)
    found = experiment_pairs(manifest, baseline, root / "baseline", member)
    assert len(found.pairs) == 2 * 5 and found.unverified == ()
    assert {p.reference.arm for p in found.pairs} == {"baseline"}
    assert {p.compared.arm for p in found.pairs} == {"I1", "I2"}
    assert {p.reference.member_sha256 for p in found.pairs} == {member}
    assert all(p.population_verified for p in found.pairs)
    assert len({p.pair_sha256 for p in found.pairs}) == 10
    # this baseline draws departures per seed (demand.py), so each seed is its own population;
    # only a trace baseline (trace_demand.py) shares one population across seeds
    assert len({p.reference.population_sha256 for p in found.pairs}) == 5


def test_candidates_that_ran_on_other_demand_are_refused(experiment) -> None:
    from cairoflow_evaluation.compare import EvaluationError

    root, _, _, manifest, _ = experiment
    copy = root / "experiment_other_demand"
    shutil.copytree(root / "experiment", copy, ignore=shutil.ignore_patterns("evaluation.json"))
    _with_run(manifest, copy, 0, 2, demand_sha256="a" * 64)
    with pytest.raises(EvaluationError, match=r"not paired with the baseline: I1 seed 2: demand"):
        compute_evaluation(copy)


def test_a_candidate_on_another_signal_timeline_is_refused(experiment) -> None:
    from cairoflow_evaluation.compare import EvaluationError

    root, _, _, manifest, _ = experiment
    copy = root / "experiment_other_member"
    shutil.copytree(root / "experiment", copy, ignore=shutil.ignore_patterns("evaluation.json"))
    record = manifest.candidates[0]
    program = record.network.signal_program.model_copy(update={"timeline_id": "another-member"})
    moved = record.model_copy(
        update={"network": record.network.model_copy(update={"signal_program": program})}
    )
    tampered = manifest.model_copy(update={"candidates": (moved, manifest.candidates[1])})
    (copy / EXPERIMENT_FILE).write_text(tampered.model_dump_json(indent=2), "utf-8")
    with pytest.raises(EvaluationError, match="I1 seed 1: signal timeline differs"):
        compute_evaluation(copy)


def test_a_candidate_on_another_geometry_is_refused(experiment) -> None:
    from cairoflow_evaluation.compare import EvaluationError

    root, _, _, manifest, _ = experiment
    copy = root / "experiment_other_geometry"
    shutil.copytree(root / "experiment", copy, ignore=shutil.ignore_patterns("evaluation.json"))
    record = manifest.candidates[1]
    network = record.network.model_copy(update={"geometry_sha256": "b" * 64})
    moved = record.model_copy(update={"network": network})
    tampered = manifest.model_copy(update={"candidates": (manifest.candidates[0], moved)})
    (copy / EXPERIMENT_FILE).write_text(tampered.model_dump_json(indent=2), "utf-8")
    with pytest.raises(EvaluationError, match="I2 seed 1: geometry differs"):
        compute_evaluation(copy)


def test_a_changed_or_missing_baseline_route_file_is_not_ignored(experiment, tmp_path) -> None:
    from cairoflow_contracts.pairing import PairingError
    from cairoflow_evaluation.pairing import experiment_pairs

    root, _, _, manifest, _ = experiment
    baseline, member = _pairing_inputs(root)
    shutil.copytree(root / "baseline/demand", tmp_path / "demand")
    route = tmp_path / "demand" / "seed_3.rou.xml"
    route.unlink()
    found = experiment_pairs(manifest, baseline, tmp_path, member)
    missing = "seed 3: demand file seed_3.rou.xml is missing; population not read"
    assert found.unverified == (missing,)
    assert len(found.pairs) == 10 and not all(p.population_verified for p in found.pairs)
    with pytest.raises(PairingError, match="seed_3.rou.xml is missing"):
        experiment_pairs(manifest, baseline, tmp_path, member, require_populations=True)
    shutil.copyfile(root / "baseline/demand/seed_3.rou.xml", route)
    other = tmp_path / "demand" / "seed_2.rou.xml"
    other.write_text(other.read_text("utf-8").replace("<routes>", "<routes>\n", 1), "utf-8")
    with pytest.raises(PairingError, match="seed 2: demand file seed_2.rou.xml changed"):
        experiment_pairs(manifest, baseline, tmp_path, member)


# --- what the pair rules check on each side, one rule at a time --------------------------------


def _pairs_after(experiment, candidate_change=None, **experiment_change):
    """experiment_pairs on the fixture's records after an in-memory edit of candidate I1 (and of
    the experiment's own fields); nothing on disk changes."""
    from cairoflow_evaluation.pairing import experiment_pairs

    root, _, _, manifest, _ = experiment
    baseline, member = _pairing_inputs(root)
    candidates = list(manifest.candidates)
    if candidate_change is not None:
        candidates[0] = candidate_change(candidates[0])
    edited = manifest.model_copy(update={"candidates": tuple(candidates)} | experiment_change)
    return experiment_pairs(edited, baseline, root / "baseline", member)


def _run_edit(**update):
    def edit(record):
        runs = (record.runs[0].model_copy(update=update), *record.runs[1:])
        return record.model_copy(update={"runs": runs})

    return edit


def _command_with(flag: str, value: str):
    def edit(record):
        command = list(record.runs[0].command)
        command[command.index(flag) + 1] = value
        return _run_edit(command=tuple(command))(record)

    return edit


def _network_edit(**update):
    def edit(record):
        return record.model_copy(update={"network": record.network.model_copy(update=update)})

    return edit


def _first_phase_state(record):
    program = record.network.signal_program
    phase = program.phases[0].model_copy(update={"state": "r" * len(program.phases[0].state)})
    changed = program.model_copy(update={"phases": (phase, *program.phases[1:])})
    return _network_edit(signal_program=changed)(record)


def _first_parameter_plus_one(record):
    params = record.network.parameters
    index = next(i for i, p in enumerate(params) if p.name != "signal.cycle")
    moved = params[index].model_copy(update={"value": params[index].value + 1})
    return _network_edit(parameters=(*params[:index], moved, *params[index + 1 :]))(record)


@pytest.mark.parametrize(
    "edit, message",
    [
        (_network_edit(sumo_version="0.0.0"), "network apart from its signal program differs"),
        (_first_parameter_plus_one, "network apart from its signal program differs"),
        (_network_edit(link_count=999), "network apart from its signal program differs"),
        (_first_phase_state, "signal program structure differs"),
        (_command_with("--step-length", "0.5"), "SUMO run configuration differs"),
        (_command_with("--end", "1"), "SUMO run configuration differs"),
        (_command_with("--seed", "999"), "its command runs SUMO with seed 999"),
        (_run_edit(demand_sha256="a" * 64), "demand file differs"),
    ],
)
def test_a_candidate_whose_network_or_run_differs_beyond_its_timings_is_refused(
    experiment, edit, message
) -> None:
    from cairoflow_contracts.pairing import PairingError

    with pytest.raises(PairingError, match=message):
        _pairs_after(experiment, edit)


def test_a_network_that_lost_its_plain_xml_hashes_is_refused(experiment) -> None:
    from cairoflow_contracts.pairing import PairingError

    zeroed = lambda record: _network_edit(  # noqa: E731
        plain_xml_sha256={k: "0" * 64 for k in record.network.plain_xml_sha256}
    )(record)
    with pytest.raises(PairingError, match="network apart from its signal program differs"):
        _pairs_after(experiment, zeroed)


def test_other_run_settings_are_refused(experiment) -> None:
    from cairoflow_contracts.pairing import PairingError

    root, _, _, manifest, _ = experiment
    longer = manifest.settings.model_copy(update={"warmup_s": manifest.settings.warmup_s + 1})
    with pytest.raises(PairingError, match="run settings are not the baseline's"):
        _pairs_after(experiment, settings=longer)
    reordered = manifest.settings.model_copy(update={"seeds": (5, 4, 3, 2, 1)})
    assert _pairs_after(experiment, settings=reordered).pairs  # the same seeds are not a mismatch


def test_a_candidate_must_run_exactly_the_baselines_seeds_once_each(experiment) -> None:
    from cairoflow_contracts.pairing import PairingError

    short = lambda r: r.model_copy(update={"runs": r.runs[:-1]})  # noqa: E731
    extra = lambda r: r.model_copy(  # noqa: E731
        update={"runs": (*r.runs, r.runs[0].model_copy(update={"seed": 99}))}
    )
    twice = lambda r: r.model_copy(update={"runs": (*r.runs, r.runs[0])})  # noqa: E731
    for edit in (short, extra):
        with pytest.raises(PairingError, match="its seeds are not the baseline's"):
            _pairs_after(experiment, edit)
    with pytest.raises(PairingError, match="it lists a seed twice"):
        _pairs_after(experiment, twice)


def test_candidate_ids_must_be_unique_and_not_the_baselines(experiment) -> None:
    from cairoflow_contracts.pairing import PairingError

    root, _, _, manifest, _ = experiment
    called_baseline = lambda r: r.model_copy(update={"intervention_id": "baseline"})  # noqa: E731
    with pytest.raises(PairingError, match="a candidate is called 'baseline'"):
        _pairs_after(experiment, called_baseline)
    clone = manifest.candidates[0]
    with pytest.raises(PairingError, match="intervention 'I1' appears 2 times"):
        _pairs_after(experiment, candidates=(clone, clone))


def test_a_baseline_run_must_have_used_the_demand_file_its_manifest_lists(experiment) -> None:
    from cairoflow_contracts.pairing import PairingError
    from cairoflow_evaluation.pairing import experiment_pairs

    root, _, _, manifest, _ = experiment
    baseline, member = _pairing_inputs(root)
    first = baseline.runs[0].model_copy(update={"demand_sha256": "a" * 64})
    edited = baseline.model_copy(update={"runs": (first, *baseline.runs[1:])})
    with pytest.raises(PairingError, match="not run on the demand file the manifest lists"):
        experiment_pairs(manifest, edited, root / "baseline", member)


def test_malformed_records_are_pairing_errors_not_validation_errors(experiment) -> None:
    from cairoflow_contracts.pairing import PairingError

    garbled = lambda r: r.model_copy(  # noqa: E731
        update={"outcome": r.outcome.model_copy(update={"proposal_sha256": "ABC"})}
    )
    with pytest.raises(PairingError, match=r"I1 seed 1: malformed record \(intervention_sha256"):
        _pairs_after(experiment, garbled)
    with pytest.raises(PairingError, match="malformed record"):
        _pairs_after(experiment, _run_edit(demand_sha256="ABC"))


def test_the_pairing_check_does_not_change_what_a_valid_experiment_evaluates_to(
    experiment, monkeypatch
) -> None:
    """Every addition is a refusal: with the check removed, the evaluation is the same object."""
    import cairoflow_evaluation.compare as compare
    from cairoflow_evaluation.pairing import ExperimentPairing

    root, *_ = experiment
    checked = compute_evaluation(root / "experiment")
    monkeypatch.setattr(compare, "experiment_pairs", lambda *a, **k: ExperimentPairing((), ()))
    assert compute_evaluation(root / "experiment") == checked


def test_a_pair_that_could_not_be_verified_is_logged_by_the_evaluation(
    experiment, tmp_path, caplog
) -> None:
    root, *_ = experiment
    shutil.copytree(root / "baseline", tmp_path / "baseline")
    shutil.copytree(root / "experiment", tmp_path / "experiment",
                    ignore=shutil.ignore_patterns("evaluation.json"))  # fmt: skip
    (tmp_path / "baseline/demand/seed_4.rou.xml").unlink()
    with caplog.at_level("WARNING", logger="cairoflow_evaluation.compare"):
        result = compute_evaluation(tmp_path / "experiment")
    assert result == compute_evaluation(root / "experiment")  # the output cannot show it
    messages = [r.getMessage() for r in caplog.records]
    assert any("seed 4" in m and "population not read" in m for m in messages)
