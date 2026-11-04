import os
import xml.etree.ElementTree as ET
from pathlib import Path

import libsumo
import pytest
import sumo
import sumolib
from pydantic import ValidationError

from cairoflow_simulation.network import (
    APPROACH_ORDER,
    JUNCTION_ID,
    NetworkBuild,
    SignalPhase,
    SignalPlan,
    build_network,
    build_phases,
    find_conflicting_greens,
    turn_between,
)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> NetworkBuild:
    return build_network(tmp_path_factory.mktemp("network"))


def _program_durations(net_path: Path, program_id: str) -> list[float]:
    for logic in ET.parse(net_path).getroot().iter("tlLogic"):
        if logic.get("id") == JUNCTION_ID and logic.get("programID") == program_id:
            return [float(phase.get("duration")) for phase in logic.findall("phase")]
    raise AssertionError(f"program {program_id} not embedded")


def test_build_succeeds_with_twelve_controlled_links(built: NetworkBuild) -> None:
    assert built.net_path.is_file()
    assert built.manifest.link_count == 12
    assert [link.index for link in built.links] == list(range(12))


def test_every_approach_has_exactly_one_left_through_and_right(built: NetworkBuild) -> None:
    for approach in APPROACH_ORDER:
        turns = sorted(link.turn for link in built.links if link.approach == approach)
        assert turns == ["l", "r", "s"]


def test_turns_agree_with_netconvert_geometry_labels(built: NetworkBuild) -> None:
    """Independent check: our edge-name turn table must match SUMO's geometric direction."""
    turn_by_index = {link.index: link.turn for link in built.links}
    net = sumolib.net.readNet(str(built.net_path))
    checked = 0
    for edge in net.getEdges():
        for connections in edge.getOutgoing().values():
            for connection in connections:
                assert connection.getDirection() == turn_by_index[connection.getTLLinkIndex()]
                checked += 1
    assert checked == 12


@pytest.mark.parametrize(
    ("approach_in", "approach_out", "expected"),
    [("N", "E", "l"), ("N", "S", "s"), ("N", "W", "r"), ("W", "N", "l"), ("S", "E", "r")],
)
def test_turn_between_uses_clockwise_arm_order(
    approach_in: str, approach_out: str, expected: str
) -> None:
    assert turn_between(approach_in, approach_out) == expected  # type: ignore[arg-type]


def test_u_turn_has_no_turn_label() -> None:
    assert turn_between("N", "N") is None


def test_signal_plan_cycle_and_validation() -> None:
    assert SignalPlan().cycle_s == 104.0
    with pytest.raises(ValidationError):
        SignalPlan(yellow_s=1.0)
    with pytest.raises(ValidationError):
        SignalPlan(green_through_s=200.0)


def test_phases_cover_the_cycle_and_never_give_foes_green(built: NetworkBuild) -> None:
    plan = built.manifest.signal_plan
    phases = build_phases(list(built.links), plan)
    assert len(phases) == 12
    assert sum(phase.duration_s for phase in phases) == plan.cycle_s
    assert all(len(phase.state) == 12 for phase in phases)
    assert find_conflicting_greens(built.net_path, phases) == []


def test_conflict_detector_flags_crossing_movements(built: NetworkBuild) -> None:
    def link_index(approach: str, turn: str) -> int:
        return next(
            link.index for link in built.links if link.approach == approach and link.turn == turn
        )

    def state_with(*greens: int) -> str:
        return "".join("G" if i in greens else "r" for i in range(12))

    crossing = SignalPhase("bad", 10.0, state_with(link_index("N", "s"), link_index("E", "s")))
    parallel = SignalPhase("ok", 10.0, state_with(link_index("N", "s"), link_index("S", "s")))
    assert find_conflicting_greens(built.net_path, [crossing]) != []
    assert find_conflicting_greens(built.net_path, [parallel]) == []


def test_embedded_program_matches_the_plan(built: NetworkBuild) -> None:
    durations = _program_durations(built.net_path, built.manifest.signal_plan.program_id)
    assert len(durations) == 12
    assert sum(durations) == built.manifest.signal_plan.cycle_s


def test_custom_plan_is_embedded(tmp_path: Path) -> None:
    plan = SignalPlan(program_id="longer_green", green_through_s=45.0)
    build = build_network(tmp_path / "custom", plan)
    assert sum(_program_durations(build.net_path, "longer_green")) == plan.cycle_s == 134.0


def test_rebuild_is_reproducible(built: NetworkBuild, tmp_path: Path) -> None:
    second = build_network(tmp_path / "second")
    assert second.manifest.net_sha256 == built.manifest.net_sha256
    assert second.manifest.tllogic_sha256 == built.manifest.tllogic_sha256
    assert second.manifest.source_sha256 == built.manifest.source_sha256


def _write_one_vehicle_per_movement(path: Path) -> None:
    rows = []
    for approach_index, approach in enumerate(APPROACH_ORDER):
        for turn, offset in (("r", 3), ("s", 2), ("l", 1)):
            target = APPROACH_ORDER[(approach_index + offset) % 4]
            rows.append((approach, turn, f"{approach}_in {target}_out"))
    vehicles = "\n".join(
        f'    <vehicle id="{a}_{t}" depart="{8 * i}" departLane="best">'
        f'<route edges="{edges}"/></vehicle>'
        for i, (a, t, edges) in enumerate(rows)
    )
    path.write_text(f"<routes>\n{vehicles}\n</routes>\n", encoding="utf-8")


def test_headless_run_every_movement_completes_without_teleports_or_collisions(
    built: NetworkBuild, tmp_path: Path
) -> None:
    routes = tmp_path / "movements.rou.xml"
    _write_one_vehicle_per_movement(routes)
    exe = Path(sumo.SUMO_HOME) / "bin" / ("sumo.exe" if os.name == "nt" else "sumo")
    libsumo.start(
        [str(exe), "-n", str(built.net_path), "-r", str(routes), "--seed", "1",
         "--no-step-log", "true", "--no-warnings", "true"]
    )  # fmt: skip
    arrived = teleports = collisions = 0
    try:
        while libsumo.simulation.getMinExpectedNumber() > 0 and libsumo.simulation.getTime() < 1500:
            libsumo.simulationStep()
            arrived += libsumo.simulation.getArrivedNumber()
            teleports += libsumo.simulation.getStartingTeleportNumber()
            collisions += libsumo.simulation.getCollidingVehiclesNumber()
        finished_at = libsumo.simulation.getTime()
    finally:
        libsumo.close()
    assert arrived == 12
    assert teleports == 0
    assert collisions == 0
    assert finished_at < 1500
