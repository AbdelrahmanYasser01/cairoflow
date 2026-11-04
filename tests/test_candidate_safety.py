"""Candidate safety: the validator's expected-red cases and the per-candidate safety record.

Synthetic cases run anywhere (synthetic_scenario.py). The NGSIM intersection 2 cases replay the
committed Muse reply (prompt 0.1.0, ADR-020) offline against the cut baseline, which is
Git-ignored; they are skipped without it. No request leaves the machine.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from synthetic_clip import NOW
from synthetic_scenario import SIGNALS, scenario_geometry, world_model

from cairoflow_contracts.experiment import RunSettings, SignalProgramSpec
from cairoflow_contracts.pair_identity import ReservedSeedError
from cairoflow_contracts.reasoning import InterventionProposal, PhaseChange
from cairoflow_reasoning.evidence import build_evidence_bundle
from cairoflow_simulation import candidates, safety
from cairoflow_simulation.candidates import CandidateError, embed_candidate_program, load_baseline
from cairoflow_simulation.runner import SimulationError, run_baseline
from cairoflow_simulation.validation import (
    UNVERIFIED_PRECONDITION,
    check_proposals,
    compiled_conflicts,
    compiled_problems,
    validate_proposal,
)

REPO = Path(__file__).resolve().parents[1]
SCENARIO = json.loads((REPO / "data/geometry/ngsim-lankershim-int2/scenario.json").read_text())
WORLD = REPO / SCENARIO["processed_outputs"]["dir"] / "world_model.json"
CUT = REPO / SCENARIO["baseline"]["run_dir"]
NEEDS_INT2 = pytest.mark.skipif(
    not (WORLD.is_file() and CUT.is_dir()),
    reason="the world model and cut baseline are Git-ignored",
)
SETTINGS = RunSettings(warmup_s=60, horizon_s=300, drain_s=300)
NS, EW = "p3_Ng_Er_Sg_Wr", "p0_Nr_Eg_Sr_Wg"  # the synthetic cycle's two green phases
LEFT, NS_THROUGH, W_THROUGH = (
    "p11_Nr_Er_Sr_Wr_NLg_SLg",
    "p0_Ng_Er_Sg_Wr_NLr_SLr",
    "p8_Nr_Er_Sr_Wg_NLr_SLr",
)
PRECONDITIONS = ("counts are representative", "the controller accepts new splits")


def _proposal(iid: str, changes: dict[str, float], kind: str = "signal_timing",
              target: str = "timeline_cycle") -> InterventionProposal:  # fmt: skip
    return InterventionProposal(
        intervention_id=iid,
        type=kind,
        target_program=target,
        phase_changes=tuple(PhaseChange(phase_name=n, green_s=g) for n, g in changes.items()),
        rationale="r",
        expected_tradeoffs=(),
        preconditions=PRECONDITIONS,
        addresses_hypothesis_ids=("H1",),
        evidence_ids=("mv.SN",),
    )


def _script():
    path = REPO / "scripts/run_candidate_safety_experiment.py"
    spec = importlib.util.spec_from_file_location("run_candidate_safety_experiment", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["run_candidate_safety_experiment"] = module
    spec.loader.exec_module(module)
    return module


def _set_phase(net_path: Path, index: int, **attributes: str) -> None:
    """Rewrite one phase of the compiled tlLogic: what a faulty compile step could leave behind."""
    tree = ET.parse(net_path)
    phase = next(tree.getroot().iter("tlLogic")).findall("phase")[index]
    for name, value in attributes.items():
        phase.set(name, value)
    tree.write(net_path, encoding="utf-8", xml_declaration=True)


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("m2a")
    geo = scenario_geometry()
    model = world_model(geo)
    run_baseline(model, geo, SIGNALS, root / "baseline", SETTINGS, verify_reruns=False, now=NOW)
    _, network = load_baseline(root / "baseline")
    return root, network, build_evidence_bundle(model, network.manifest)


@pytest.fixture(scope="module")
def int2():
    """The cut network, its 0.1.0 bundle and the committed reply's I1-I3, replayed offline."""
    if not (WORLD.is_file() and CUT.is_dir()):
        pytest.skip("the world model and cut baseline are Git-ignored")
    run = _script()
    replay = run.replay_stored_reply()
    assert replay.findings == ()
    return run, replay


# --- (1) and (5): timing bounds ------------------------------------------------------------


@NEEDS_INT2
def test_an_80_s_protected_left_green_is_rejected_under_the_5_40_s_bound(int2) -> None:
    _, replay = int2
    outcome = validate_proposal(
        _proposal("I9", {LEFT: 80}, target="ngsim_int2_cycle"), replay.network, replay.bundle
    )
    assert outcome.status == "rejected"
    assert "head N-left would show green for 87 s in a run, outside 5-40 s" in outcome.reasons
    assert "head S-left would show green for 80 s in a run, outside 5-40 s" in outcome.reasons


@NEEDS_INT2
def test_90_s_plus_90_s_stays_rejected_on_the_cut_cycle(int2) -> None:
    _, replay = int2
    proposal = _proposal("I9", {NS_THROUGH: 90, W_THROUGH: 90}, target="ngsim_int2_cycle")
    outcome = validate_proposal(proposal, replay.network, replay.bundle)
    assert outcome.status == "rejected" and "cycle 236 s is outside 30-180 s" in outcome.reasons


def test_90_s_plus_90_s_stays_rejected_on_the_synthetic_cycle(synthetic) -> None:
    _, network, bundle = synthetic
    outcome = validate_proposal(_proposal("I1", {NS: 90, EW: 90}), network, bundle)
    assert outcome.status == "rejected" and "cycle 190 s is outside 30-180 s" in outcome.reasons


# --- (4): the stored candidates stay accepted ----------------------------------------------


@NEEDS_INT2
def test_the_stored_muse_candidates_stay_accepted(int2) -> None:
    _, replay = int2
    assert replay.source["request_key"].startswith("ddfb8029")
    assert replay.source["prompt_version"] == "0.1.0" and replay.source["mode"] == "replay"
    outcomes = [c.outcome for c in replay.checked]
    assert [o.intervention_id for o in outcomes] == ["I1", "I2", "I3"]
    assert all(o.status == "accepted" and o.reasons == () for o in outcomes)
    assert [o.cycle_s for o in outcomes] == [102.0, 99.0, 102.0]
    assert {o.baseline_cycle_s for o in outcomes} == {99.0}


def test_a_cache_miss_is_a_finding_not_a_live_call(tmp_path: Path) -> None:
    if not (WORLD.is_file() and CUT.is_dir()):
        pytest.skip("the world model and cut baseline are Git-ignored")
    replay = _script().replay_stored_reply(cache_dir=tmp_path / "empty")
    assert replay.checked == ()
    assert len(replay.findings) == 1 and replay.findings[0].startswith("cache miss:")
    assert "no live request was made" in replay.findings[0]
    assert not (tmp_path / "empty").exists()


# --- (2): preconditions are unverified assumptions in the validator's result ---------------


def test_preconditions_are_carried_as_unverified_assumptions(synthetic) -> None:
    _, network, bundle = synthetic
    proposals = [_proposal("I1", {NS: 30, EW: 20}), _proposal("I2", {NS: 90, EW: 90})]
    checked = check_proposals(proposals, network, bundle, now=NOW)
    assert [c.outcome.status for c in checked] == ["accepted", "rejected"]
    expected = tuple(f"{UNVERIFIED_PRECONDITION}: {text}" for text in PRECONDITIONS)
    for result in checked:  # whatever the verdict: no code checks the model's preconditions
        assert result.unverified_assumptions == expected
    assert checked[0].outcome.reasons == ()  # they are not reasons and do not reject


@NEEDS_INT2
def test_the_stored_candidates_carry_their_preconditions(int2) -> None:
    _, replay = int2
    for result in replay.checked:
        assert len(result.unverified_assumptions) == 2
        assert all(a.startswith(UNVERIFIED_PRECONDITION) for a in result.unverified_assumptions)
    assert replay.checked[0].unverified_assumptions[0].endswith("accepts revised green splits")


# --- (3): the conflict check reads the compiled candidate ----------------------------------


def _candidate(network, durations: tuple[float, ...]) -> SignalProgramSpec:
    program = network.manifest.signal_program
    return SignalProgramSpec.model_validate(program.model_dump() | {
        "program_id": f"{program.program_id}_I1",
        "cycle_s": sum(durations),
        "phases": [p.model_dump() | {"duration_s": d}
                   for p, d in zip(program.phases, durations, strict=True)],
    })  # fmt: skip


def test_the_conflict_check_reads_the_compiled_candidate(synthetic, tmp_path, monkeypatch) -> None:
    _, network, _ = synthetic
    durations = (20.0, 3.0, 2.0, 30.0, 3.0, 2.0)
    real = candidates.embed_program

    def faulty(out_dir, program, links):  # compiles, then the file SUMO runs shows all-priority
        net_path, warnings = real(out_dir, program, links)
        _set_phase(net_path, 0, state="G" * len(program.phases[0].state))
        return net_path, warnings

    monkeypatch.setattr(candidates, "embed_program", faulty)
    with pytest.raises(CandidateError, match="gives priority green to conflicting links"):
        embed_candidate_program(network, _candidate(network, durations), "I1", tmp_path / "net")


def test_compiled_checks_find_what_the_spec_does_not_show(synthetic, tmp_path) -> None:
    _, network, _ = synthetic
    durations = (20.0, 3.0, 2.0, 30.0, 3.0, 2.0)
    compiled = embed_candidate_program(
        network, _candidate(network, durations), "I1", tmp_path / "n"
    )
    program = compiled.manifest.signal_program
    assert compiled_conflicts(compiled.net_path, program.program_id) == []
    assert compiled_problems(compiled.net_path, program.program_id, program.phases, durations) == []
    _set_phase(compiled.net_path, 0, state="G" * len(program.phases[0].state), duration="95")
    problems = compiled_problems(compiled.net_path, program.program_id, program.phases, durations)
    assert any("does not show the validated phases and states" in p for p in problems)
    assert any("not the validated durations" in p for p in problems)
    assert any("gives priority green to conflicting links" in p for p in problems)
    with pytest.raises(ValueError, match="holds 0 programs"):
        compiled_conflicts(compiled.net_path, "another_program")


# --- the per-candidate safety record -------------------------------------------------------


@pytest.fixture(scope="module")
def record(synthetic, tmp_path_factory: pytest.TempPathFactory):
    root, network, bundle = synthetic
    proposals = [
        _proposal("I1", {NS: 30, EW: 20}),
        _proposal("I2", {NS: 30, EW: 20}, "route_guidance"),
    ]
    checked = check_proposals(proposals, network, bundle, now=NOW)
    out = tmp_path_factory.mktemp("safety") / "record"
    built = safety.build_safety_record(
        checked, root / "baseline", out, now=NOW, source={"reply": "synthetic test proposals"},
        closed=(safety.closed_result("D1", "no support on seeds 201-220", ()),),
    )  # fmt: skip
    return out, built


def _steps(candidate) -> dict[str, str]:
    return {step.step: step.status for step in candidate.steps}


def test_an_accepted_candidate_passes_every_step_in_order(record) -> None:
    _, built = record
    i1 = built.candidates[0]
    assert [s.step for s in i1.steps] == list(safety.STEPS)
    assert set(_steps(i1).values()) == {"passed"} and i1.safe
    assert i1.smoke_seed == 1 and i1.smoke_statistics == {
        "collisions": 0, "teleports": 0, "unfinished": 0}  # fmt: skip
    demand = next(s for s in i1.steps if s.step == "demand")
    assert [d.split(":")[0] for d in demand.details] == [f"seed {s}" for s in (1, 2, 3, 4, 5)]
    assert set(i1.timings_s) == {"setup_s", "compile_s", "run_s", "rerun_s"}
    assert all(seconds > 0 for seconds in i1.timings_s.values())
    assert set(i1.files_sha256) == {"tripinfo.xml", "queue.xml"}
    assert i1.unverified_assumptions == tuple(
        f"{UNVERIFIED_PRECONDITION}: {t}" for t in PRECONDITIONS)  # fmt: skip


def test_a_rejected_proposal_stops_at_the_verdict(record) -> None:
    _, built = record
    i2 = built.candidates[1]
    assert _steps(i2) == {"verdict": "failed"} | {s: "not_run" for s in safety.STEPS[1:]}
    assert not i2.safe and i2.timings_s == {} and i2.smoke_seed is None
    assert "not in the MVP allowlist" in i2.steps[0].details[0]


def test_the_sidecar_is_new_and_never_overwritten(record, synthetic) -> None:
    out, built = record
    stored = safety.SafetyRecord.model_validate_json((out / safety.SAFETY_FILE).read_text("utf-8"))
    assert stored == built and not built.safe  # one candidate was rejected
    assert built.closed[0].status == safety.CLOSED and built.closed[0].re_run is False
    root, _, _ = synthetic
    with pytest.raises(FileExistsError):
        safety.build_safety_record((), root / "baseline", out, now=NOW, source={})


def _one(synthetic, tmp_path, monkeypatch=None, **patches):
    root, network, bundle = synthetic
    for name, value in patches.items():
        monkeypatch.setattr(safety, name, value)
    checked = check_proposals([_proposal("I1", {NS: 30, EW: 20})], network, bundle, now=NOW)
    return safety.check_candidate(checked[0], root / "baseline", tmp_path / "I1")


def test_a_changed_demand_file_on_any_seed_fails_the_demand_step(synthetic, tmp_path) -> None:
    root, network, bundle = synthetic
    baseline = tmp_path / "baseline"
    shutil.copytree(root / "baseline", baseline)
    seed_4 = baseline / "demand" / "seed_4.rou.xml"
    seed_4.write_text(seed_4.read_text("utf-8") + "<!-- changed -->\n", "utf-8")
    checked = check_proposals([_proposal("I1", {NS: 30, EW: 20})], network, bundle, now=NOW)
    result = safety.check_candidate(checked[0], baseline, tmp_path / "I1")
    steps = _steps(result)
    assert steps["demand"] == "failed" and steps["smoke"] == steps["rerun"] == "not_run"
    failed = next(s for s in result.steps if s.step == "demand").details
    assert [d for d in failed if "differs" in d] == [d for d in failed if d.startswith("seed 4:")]
    assert result.smoke_seed is None  # no smoke ran


def test_a_missing_demand_file_fails_the_demand_step_and_the_record_is_written(
    synthetic, tmp_path
) -> None:
    """A demand file that is gone is a failed demand step on that seed, not a crash that leaves a
    directory without its record behind (and refuses every later attempt)."""
    root, network, bundle = synthetic
    baseline = tmp_path / "baseline"
    shutil.copytree(root / "baseline", baseline)
    (baseline / "demand" / "seed_5.rou.xml").unlink()
    checked = check_proposals([_proposal("I1", {NS: 30, EW: 20})], network, bundle, now=NOW)
    out = tmp_path / "record"
    built = safety.build_safety_record(checked, baseline, out, now=NOW, source={"reply": "test"})
    assert (out / safety.SAFETY_FILE).is_file() and not built.safe
    i1 = built.candidates[0]
    steps = _steps(i1)
    assert steps["demand"] == "failed" and steps["smoke"] == steps["rerun"] == "not_run"
    details = next(s for s in i1.steps if s.step == "demand").details
    assert [d.split(":")[0] for d in details] == [f"seed {s}" for s in (1, 2, 3, 4, 5)]
    assert details[-1] == "seed 5: seed_5.rou.xml is missing"
    assert all("as recorded" in d for d in details[:-1])
    assert i1.smoke_seed is None


def test_a_chain_that_stops_before_the_smoke_names_no_smoke_seed(
    synthetic, tmp_path, monkeypatch
) -> None:
    def broken(baseline, outcome, out_dir):
        raise CandidateError("broken on purpose")

    result = _one(synthetic, tmp_path, monkeypatch, build_candidate_network=broken)
    assert _steps(result)["compile"] == "failed" and _steps(result)["smoke"] == "not_run"
    assert result.smoke_seed is None and result.smoke_statistics is None


def _reserved_baseline(synthetic, tmp_path: Path) -> Path:
    """A copy of the synthetic baseline whose seeds 1-5 are renumbered 301-305 everywhere."""
    root, _, _ = synthetic
    baseline = tmp_path / "reserved"
    shutil.copytree(root / "baseline", baseline)
    path = baseline / "baseline_manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    manifest["settings"]["seeds"] = [300 + s for s in manifest["settings"]["seeds"]]
    for entry in (*manifest["demand"]["files"], *manifest["runs"], *manifest["reruns"]):
        entry["seed"] += 300
    path.write_text(json.dumps(manifest), "utf-8")
    return baseline


def test_reserved_confirmation_seeds_are_refused_before_anything_runs(
    synthetic, tmp_path, monkeypatch
) -> None:
    """Seeds 301-320 stay unused and unrecorded (ADR-033): the safety chain refuses them, as
    run_candidates does, before it builds, runs or writes anything, unless confirmation=True."""
    _, network, bundle = synthetic
    calls: list[int] = []

    def no_sumo(network, demand_path, seed, settings, run_dir):
        calls.append(seed)
        raise SimulationError("not run in this test")

    monkeypatch.setattr(safety, "run_seed", no_sumo)
    baseline = _reserved_baseline(synthetic, tmp_path)
    checked = check_proposals([_proposal("I1", {NS: 30, EW: 20})], network, bundle, now=NOW)
    out = tmp_path / "record"
    with pytest.raises(ReservedSeedError, match="confirmation"):
        safety.build_safety_record(checked, baseline, out, now=NOW, source={})
    assert not out.exists()
    with pytest.raises(ReservedSeedError, match="confirmation"):
        safety.check_candidate(checked[0], baseline, tmp_path / "I1")
    assert not (tmp_path / "I1").exists()
    root, _, _ = synthetic
    with pytest.raises(ReservedSeedError, match="confirmation"):  # an ordinary baseline, too
        safety.check_candidate(checked[0], root / "baseline", tmp_path / "I1b", smoke_seed=301)
    assert calls == []
    confirmed = safety.check_candidate(checked[0], baseline, tmp_path / "I1c", confirmation=True)
    assert calls == [301] and _steps(confirmed)["smoke"] == "failed"  # the stub, not SUMO
    assert confirmed.smoke_seed == 301


def test_a_tampered_plain_xml_file_fails_its_step(synthetic, tmp_path, monkeypatch) -> None:
    real = safety.build_candidate_network

    def tampered(baseline, outcome, out_dir):
        built = real(baseline, outcome, out_dir)
        edges = out_dir / "edges.edg.xml"
        edges.write_text(edges.read_text("utf-8").replace("</edges>", "</edges>\n"), "utf-8")
        return built

    result = _one(synthetic, tmp_path, monkeypatch, build_candidate_network=tampered)
    assert _steps(result)["plain_xml"] == "failed" and _steps(result)["demand"] == "not_run"
    details = next(s for s in result.steps if s.step == "plain_xml").details
    assert any(d.startswith("edges.edg.xml differs") for d in details)


def test_the_smoke_reads_sumos_statistics_output(synthetic, tmp_path, monkeypatch) -> None:
    real = safety.run_seed

    def collided(network, demand_path, seed, settings, run_dir):
        run = real(network, demand_path, seed, settings, run_dir)
        stats = run_dir / "statistics.xml"
        tree = ET.parse(stats)
        tree.getroot().find("safety").set("collisions", "2")
        tree.write(stats, encoding="utf-8", xml_declaration=True)
        return run

    result = _one(synthetic, tmp_path, monkeypatch, run_seed=collided)
    assert _steps(result)["smoke"] == "failed" and _steps(result)["rerun"] == "not_run"
    assert result.smoke_statistics == {"collisions": 2, "teleports": 0, "unfinished": 0}
    assert "2 collisions" in next(s for s in result.steps if s.step == "smoke").details
    assert result.smoke_seed == 1  # the smoke ran on it, and failed


def test_the_rerun_compares_tripinfo_and_queue_without_comments(
    synthetic, tmp_path, monkeypatch
) -> None:
    real = safety.run_seed

    def rerun_differs(network, demand_path, seed, settings, run_dir):
        run = real(network, demand_path, seed, settings, run_dir)
        if run_dir.name.endswith("_rerun"):
            for name in ("tripinfo.xml", "queue.xml"):  # another timestamp comment is ignored
                path = run_dir / name
                text = path.read_text("utf-8")
                assert "<!-- generated on " in text
                path.write_text(text.replace("<!-- generated on ", "<!-- generated on 2099 "),
                                "utf-8")  # fmt: skip
            queue = run_dir / "queue.xml"
            queue.write_text(queue.read_text("utf-8").replace("<data", '<data x="1"', 1), "utf-8")
        return run

    result = _one(synthetic, tmp_path, monkeypatch, run_seed=rerun_differs)
    assert _steps(result)["rerun"] == "failed"
    details = next(s for s in result.steps if s.step == "rerun").details
    assert any(d.startswith("tripinfo.xml identical") for d in details)
    assert any(d.startswith("queue.xml differs") for d in details)


@NEEDS_INT2
def test_an_80_s_left_in_the_compiled_candidate_fails_the_compile_step(
    int2, tmp_path, monkeypatch
) -> None:
    _, replay = int2
    real = safety.build_candidate_network
    left = [p.name for p in replay.network.manifest.signal_program.phases].index(LEFT)

    def long_left(baseline, outcome, out_dir):
        built = real(baseline, outcome, out_dir)
        _set_phase(built.net_path, left, duration="80")
        return built

    monkeypatch.setattr(safety, "build_candidate_network", long_left)
    result = safety.check_candidate(replay.checked[0], CUT, tmp_path / "I1")
    assert _steps(result)["compile"] == "failed" and _steps(result)["plain_xml"] == "not_run"
    details = next(s for s in result.steps if s.step == "compile").details
    assert "head N-left would show green for 87 s in a run, outside 5-40 s" in details
    assert any("not the validated durations" in d for d in details)


@NEEDS_INT2
def test_the_stored_candidates_have_a_complete_safety_record(int2, tmp_path) -> None:
    run, _ = int2
    built = run.build(tmp_path / "safety", now=NOW)
    assert [c.intervention_id for c in built.candidates] == ["I1", "I2", "I3"]
    for candidate in built.candidates:
        assert candidate.safe and set(_steps(candidate).values()) == {"passed"}
        assert candidate.smoke_statistics == {"collisions": 0, "teleports": 0, "unfinished": 0}
        demand = next(s for s in candidate.steps if s.step == "demand").details
        assert len(demand) == 5
    assert [c.cycle_s for c in built.candidates] == [102.0, 99.0, 102.0]
    assert built.safe and built.findings == ()
    assert [(c.result_id, c.status, c.re_run) for c in built.closed] == [
        ("D1", safety.CLOSED, False), ("D2", safety.CLOSED, False)]  # fmt: skip
    for closed in built.closed:
        for path, digest in closed.evidence.items():
            assert (REPO / path).is_file() and len(digest) == 64
    stored = json.loads((tmp_path / "safety" / safety.SAFETY_FILE).read_text("utf-8"))
    assert stored["source"]["request_key"].startswith("ddfb8029")
