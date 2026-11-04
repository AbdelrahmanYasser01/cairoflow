"""SUMO baseline from the synthetic scenario (synthetic_scenario.py): the synthetic junction, an
assumed 60 s signal cycle cut at [22 s, 82 s), and world-model flows over 60 s.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import sumolib
from synthetic_clip import CLIP_ID, NOW
from synthetic_junction import geometry
from synthetic_scenario import SIGNALS
from synthetic_scenario import scenario_geometry as _geometry
from synthetic_scenario import timeline as _timeline
from synthetic_scenario import world_model as _world_model

from cairoflow_contracts.experiment import BaselineManifest, RunSettings
from cairoflow_simulation.demand import DemandError, generate_demand
from cairoflow_simulation.geometry_compiler import CompileError, compile_network, signal_cycle
from cairoflow_simulation.network import read_links
from cairoflow_simulation.runner import MANIFEST_FILE, run_baseline

SETTINGS = RunSettings(warmup_s=60, horizon_s=300, drain_s=300)


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory) -> tuple[BaselineManifest, Path]:
    out = tmp_path_factory.mktemp("sim") / "baseline"
    geo = _geometry()
    return run_baseline(_world_model(geo), geo, SIGNALS, out, SETTINGS, now=NOW), out


def test_network_follows_the_drawing_and_labels_every_source(baseline) -> None:
    manifest, out = baseline
    params = {p.name: p for p in manifest.network.parameters}

    def value_and_label(name: str):
        return params[name].value, params[name].provenance

    assert value_and_label("S.approach_lanes") == (2, "approximated")
    assert value_and_label("N.approach_lanes") == (1, "synthetic")
    assert value_and_label("S2.sumo_lane_index") == (0, "approximated")  # nearest the kerb
    assert value_and_label("S1.sumo_lane_index") == (1, "approximated")
    assert value_and_label("S1.width") == (3.5, "approximated")
    assert value_and_label("S.exit_lanes") == (2, "synthetic")  # none drawn
    assert value_and_label("E.arm_length") == (200.0, "synthetic")
    assert "beyond the camera" in params["E.arm_length"].source
    assert value_and_label("signal.cycle") == (60.0, "synthetic")  # an assumed timeline
    assert value_and_label("S.stop_line_distance_drawn") == (10.0, "synthetic")
    # SUMO's stop line follows from its junction shape, not the drawing; it must be near it.
    for arm in ("N", "S", "W"):
        sumo_m = params[f"{arm}.stop_line_distance_sumo"].value
        assert 3.0 < sumo_m < 20.0, (arm, sumo_m)

    net = sumolib.net.readNet(str(out / "network" / "network.net.xml"))
    assert not net.hasEdge("E_in")  # nothing drawn approaches from the east

    def targets(edge: str, lane: int) -> set[str]:
        outgoing = net.getEdge(edge).getLane(lane).getOutgoing()
        return {c.getToLane().getEdge().getID() for c in outgoing}

    assert targets("S_in", 0) == {"E_out", "N_out"}  # S2: right, through
    assert targets("S_in", 1) == {"N_out", "W_out"}  # S1: through, left
    assert targets("W_in", 0) == {"E_out", "S_out"}


def test_signal_program_is_one_cycle_cut_from_the_timeline(baseline) -> None:
    manifest, out = baseline
    program = manifest.network.signal_program
    assert (program.cycle_start_s, program.cycle_s, program.label) == (22, 60, "assumed")
    assert [p.duration_s for p in program.phases] == [25, 3, 2, 25, 3, 2]
    assert program.phases[0].arm_states == {"N": "red", "E": "green", "S": "red", "W": "green"}

    links = read_links(out / "network" / "network.net.xml")
    ns_green = program.phases[3].state
    for link in links:
        if link.approach in ("N", "S"):
            # A left turn yields to the opposing through movement; through traffic has priority.
            expected = {"s": "G", "l": "g"}.get(link.turn, ns_green[link.index])
            assert ns_green[link.index] == expected
        else:
            assert ns_green[link.index] == "r"
    logic = ET.parse(out / "network" / "network.net.xml").getroot().find("tlLogic")
    assert logic is not None and len(logic.findall("phase")) == 6


def test_a_cycle_needs_two_green_onsets_and_no_unknown_state() -> None:
    short = _timeline({"N": [("red", 0, 10), ("green", 10, 20)]}, 20.0)
    with pytest.raises(CompileError, match="no complete cycle"):
        signal_cycle(short)
    hidden = [("red", 0, 10), ("green", 10, 20), ("yellow", 20, 23), ("red", 23, 25)]
    hidden += [("unknown", 25, 28), ("red", 28, 30), ("green", 30, 40)]
    with pytest.raises(CompileError, match="unknown at 25"):
        signal_cycle(_timeline({"N": hidden}, 40.0))


def test_demand_is_poisson_per_seed_and_reproducible(baseline, tmp_path: Path) -> None:
    manifest, out = baseline
    demand = manifest.demand
    assert [f.seed for f in demand.files] == [1, 2, 3, 4, 5]
    assert len({f.sha256 for f in demand.files}) == 5
    expected = 24 * 60 * SETTINGS.generation_end_s / 3600  # 1440 veh/h over 360 s = 144
    assert all(abs(f.vehicles - expected) < 4 * expected**0.5 for f in demand.files)
    s_to_n = next(m for m in demand.movements if (m.approach, m.exit_arm) == ("S", "N"))
    assert s_to_n.vehicle_type_mix == {"bus": 0.125, "passenger": 0.875}

    routes = ET.parse(out / "demand" / "seed_1.rou.xml").getroot()
    assert {v.get("type") for v in routes.iter("vehicle") if v.get("id", "").startswith("WS.")} == {
        "bus"
    }
    departs = [float(v.get("depart", 0)) for v in routes.iter("vehicle")]
    assert departs == sorted(departs) and max(departs) < SETTINGS.generation_end_s

    # Rebuilding from the same inputs gives the same network and the same demand files.
    geo = _geometry()
    network = compile_network(geo, SIGNALS, tmp_path / "network")
    assert network.manifest.net_sha256 == manifest.network.net_sha256
    again = generate_demand(_world_model(geo), network, SETTINGS, tmp_path / "demand")
    assert [f.sha256 for f in again.files] == [f.sha256 for f in demand.files]


def test_baseline_runs_every_seed_without_teleports_and_reruns_identically(baseline) -> None:
    manifest, out = baseline
    assert manifest.valid and manifest.reasons == ()
    assert [run.seed for run in manifest.runs] == [1, 2, 3, 4, 5]
    for run in manifest.runs:
        assert (run.teleports, run.collisions, run.unfinished) == (0, 0, 0)
        assert run.arrived == run.loaded == run.inserted > 0
    assert manifest.reruns_identical is True
    assert [r.tripinfo_sha256 for r in manifest.reruns] == [
        r.tripinfo_sha256 for r in manifest.runs
    ]
    assert len({r.tripinfo_sha256 for r in manifest.runs}) == 5  # seeds differ

    saved = BaselineManifest.model_validate_json((out / MANIFEST_FILE).read_text("utf-8"))
    assert saved == manifest
    geo = _geometry()
    with pytest.raises(FileExistsError):
        run_baseline(_world_model(geo), geo, SIGNALS, out, SETTINGS)


def test_teleports_make_a_run_invalid(tmp_path: Path) -> None:
    geo = _geometry()
    impatient = RunSettings(seeds=(1,), warmup_s=0, horizon_s=120, drain_s=60, time_to_teleport_s=5)
    manifest = run_baseline(
        _world_model(geo), geo, SIGNALS, tmp_path / "b", impatient, verify_reruns=False
    )
    (run,) = manifest.runs
    assert run.teleports > 0 and not run.valid and not manifest.valid
    assert manifest.reruns_identical is None
    assert "teleports" in manifest.reasons[0]


def test_demand_refuses_movements_the_network_cannot_serve(tmp_path: Path) -> None:
    geo = _geometry()
    network = compile_network(geo, SIGNALS, tmp_path / "network")
    from_east = _world_model(geo, {("E", "W"): ["car"]})
    with pytest.raises(DemandError, match="no such movement"):
        generate_demand(from_east, network, SETTINGS, tmp_path / "demand")
    elsewhere = _world_model(geometry("d" * 64, CLIP_ID))
    with pytest.raises(DemandError, match="different geometry"):
        generate_demand(elsewhere, network, SETTINGS, tmp_path / "demand2")
