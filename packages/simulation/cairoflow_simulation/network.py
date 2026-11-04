"""Hand-authored four-way signalised intersection: build and validate the SUMO network.

Sources live in packages/simulation/networks/baseline_4way (SUMO plain XML). The builder runs
netconvert twice because traffic-light link indices are only known after a first conversion:
pass one yields the link order, the signal program is generated from it, pass two embeds it.

Coordinates are SUMO network metres (x east, y north). Geometry and the signal plan are
documented assumptions until calibrated against a clip. The signal state is never observed.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import Literal, NamedTuple

import sumo
import sumolib
from pydantic import BaseModel, ConfigDict, Field

Approach = Literal["N", "E", "S", "W"]
Turn = Literal["l", "s", "r"]

APPROACH_ORDER: tuple[Approach, ...] = ("N", "E", "S", "W")  # clockwise around the junction
JUNCTION_ID = "C"
SOURCE_DIR = Path(__file__).resolve().parents[1] / "networks" / "baseline_4way"
SOURCE_FILES = ("nodes.nod.xml", "edges.edg.xml", "connections.con.xml")
NET_FILE = "baseline.net.xml"
TLLOGIC_FILE = "signals.tll.xml"
_PASS_ONE_NET = "pass1.net.xml"


class NetworkBuildError(RuntimeError):
    """Raised when netconvert fails or the generated network violates an invariant."""


class SignalPlan(BaseModel):
    """Fixed-time, four-phase protected plan. All durations are simulation seconds."""

    model_config = ConfigDict(frozen=True)

    program_id: str = Field("baseline_fixed", pattern=r"^[A-Za-z0-9_]+$")
    green_through_s: float = Field(30.0, ge=5.0, le=90.0)
    green_left_s: float = Field(12.0, ge=5.0, le=40.0)
    yellow_s: float = Field(3.0, ge=3.0, le=6.0)
    all_red_s: float = Field(2.0, ge=1.0, le=6.0)

    @property
    def cycle_s(self) -> float:
        """Total cycle length: two axes, each with a through/right and a left stage."""
        return 2 * (self.green_through_s + self.green_left_s) + 4 * (self.yellow_s + self.all_red_s)


class LinkInfo(BaseModel):
    """One controlled signal link: its SUMO link index and the movement it serves."""

    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    approach: Approach
    turn: Turn


class SignalPhase(NamedTuple):
    name: str
    duration_s: float
    state: str


class NetworkBuildManifest(BaseModel):
    """Everything needed to check that a network was rebuilt from the same inputs."""

    model_config = ConfigDict(frozen=True)

    sumo_version: str
    source_sha256: dict[str, str]
    tllogic_sha256: str
    net_sha256: str
    signal_plan: SignalPlan
    link_count: int
    netconvert_warnings: tuple[str, ...]


class NetworkBuild(BaseModel):
    model_config = ConfigDict(frozen=True)

    net_path: Path
    tllogic_path: Path
    links: tuple[LinkInfo, ...]
    manifest: NetworkBuildManifest


def turn_between(approach_in: Approach, approach_out: Approach) -> Turn | None:
    """Turn taken when entering from one arm and leaving by another (right-hand traffic).

    Arms are ordered clockwise, so the next arm is a left turn, the opposite arm is straight
    through and the previous arm is a right turn. Returning to the same arm (a U-turn) is None.
    """
    offset = (APPROACH_ORDER.index(approach_out) - APPROACH_ORDER.index(approach_in)) % 4
    return {1: "l", 2: "s", 3: "r"}.get(offset)  # type: ignore[return-value]


def _approach_of(edge_id: str) -> Approach:
    letter = edge_id.split("_", 1)[0]
    if letter not in APPROACH_ORDER:
        raise NetworkBuildError(f"Unexpected edge id {edge_id!r}; expected N_/E_/S_/W_ prefix")
    return letter  # type: ignore[return-value]


def read_links(net_path: Path) -> list[LinkInfo]:
    """Read the controlled links of the junction, ordered by SUMO link index."""
    net = sumolib.net.readNet(str(net_path))
    raw_links = net.getTLS(JUNCTION_ID).getLinks()
    links: list[LinkInfo] = []
    for index in sorted(raw_links):
        in_lane, out_lane, _ = raw_links[index][0]
        approach_in = _approach_of(in_lane.getEdge().getID())
        approach_out = _approach_of(out_lane.getEdge().getID())
        turn = turn_between(approach_in, approach_out)
        if turn is None:
            raise NetworkBuildError(f"Link {index} is a U-turn, which this network does not allow")
        links.append(LinkInfo(index=index, approach=approach_in, turn=turn))
    return links


# stage name, approaches that get green, turns that get green
_STAGES: tuple[tuple[str, tuple[Approach, ...], tuple[Turn, ...]], ...] = (
    ("NS_through_right", ("N", "S"), ("s", "r")),
    ("NS_left", ("N", "S"), ("l",)),
    ("EW_through_right", ("E", "W"), ("s", "r")),
    ("EW_left", ("E", "W"), ("l",)),
)


def _state(count: int, active: set[int], letter: str) -> str:
    return "".join(letter if index in active else "r" for index in range(count))


def build_phases(links: Sequence[LinkInfo], plan: SignalPlan) -> list[SignalPhase]:
    """Generate green, yellow and all-red phases for each stage from the link table."""
    indices = sorted(link.index for link in links)
    if indices != list(range(len(indices))):
        raise NetworkBuildError(f"Link indices must be contiguous from 0, got {indices}")
    count = len(indices)
    phases: list[SignalPhase] = []
    for name, approaches, turns in _STAGES:
        green = {link.index for link in links if link.approach in approaches and link.turn in turns}
        if not green:
            raise NetworkBuildError(f"Stage {name} serves no links")
        duration = plan.green_left_s if turns == ("l",) else plan.green_through_s
        phases.append(SignalPhase(f"{name}_green", duration, _state(count, green, "G")))
        phases.append(SignalPhase(f"{name}_yellow", plan.yellow_s, _state(count, green, "y")))
        phases.append(SignalPhase(f"{name}_all_red", plan.all_red_s, "r" * count))
    return phases


def render_tllogic(plan: SignalPlan, phases: Sequence[SignalPhase]) -> str:
    """Render a SUMO tlLogic file for the junction."""
    rows = "\n".join(
        f'        <phase duration="{p.duration_s:g}" state="{p.state}" name="{p.name}"/>'
        for p in phases
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<tlLogics>\n"
        f'    <tlLogic id="{JUNCTION_ID}" type="static" programID="{plan.program_id}" offset="0">\n'
        f"{rows}\n"
        "    </tlLogic>\n"
        "</tlLogics>\n"
    )


def find_conflicting_greens(
    net_path: Path, phases: Sequence[SignalPhase]
) -> list[tuple[str, int, int]]:
    """Return (phase name, link a, link b) for every pair of green links that are foes.

    This is the deterministic check a proposed signal plan must pass before it is simulated.
    """
    node = sumolib.net.readNet(str(net_path)).getNode(JUNCTION_ID)
    conflicts: list[tuple[str, int, int]] = []
    for phase in phases:
        green = [index for index, char in enumerate(phase.state) if char in "Gg"]
        for position, first in enumerate(green):
            for second in green[position + 1 :]:
                if node.areFoes(first, second):
                    conflicts.append((phase.name, first, second))
    return conflicts


def _sumo_bin(name: str) -> Path:
    path = Path(sumo.SUMO_HOME) / "bin" / (name + (".exe" if os.name == "nt" else ""))
    if not path.exists():
        raise NetworkBuildError(f"SUMO executable not found: {path}")
    return path


def _run_netconvert(work_dir: Path, args: Sequence[str]) -> list[str]:
    # Relative paths from work_dir: SUMO tools echo absolute paths into XML comments, and a
    # path containing a double dash makes those comments invalid XML.
    result = subprocess.run(
        [str(_sumo_bin("netconvert")), *args],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0 or "Error:" in output:
        raise NetworkBuildError(f"netconvert failed (rc={result.returncode}): {output[-800:]}")
    return [line.strip() for line in output.splitlines() if line.strip().startswith("Warning:")]


def content_sha256(path: Path, strip_comments: bool = False) -> str:
    """Hash text content with normalised newlines, optionally ignoring XML comments.

    netconvert writes a generation timestamp in a comment; stripping comments keeps the hash
    stable across rebuilds of identical inputs.
    """
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    if strip_comments:
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_network(out_dir: Path, plan: SignalPlan | None = None) -> NetworkBuild:
    """Build the baseline network with an embedded signal program into out_dir."""
    plan = plan or SignalPlan()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in SOURCE_FILES:
        shutil.copyfile(SOURCE_DIR / name, out_dir / name)

    base_args = [
        "--node-files", SOURCE_FILES[0],
        "--edge-files", SOURCE_FILES[1],
        "--connection-files", SOURCE_FILES[2],
        "--no-turnarounds", "true",
    ]  # fmt: skip
    warnings = _run_netconvert(out_dir, [*base_args, "--output-file", _PASS_ONE_NET])
    links = read_links(out_dir / _PASS_ONE_NET)
    phases = build_phases(links, plan)
    (out_dir / TLLOGIC_FILE).write_text(
        render_tllogic(plan, phases), encoding="utf-8", newline="\n"
    )
    warnings += _run_netconvert(
        out_dir, [*base_args, "--tllogic-files", TLLOGIC_FILE, "--output-file", NET_FILE]
    )

    final_links = read_links(out_dir / NET_FILE)
    if final_links != links:
        raise NetworkBuildError("Link order changed between netconvert passes")
    _check_embedded_program(out_dir / NET_FILE, plan, len(phases))
    (out_dir / _PASS_ONE_NET).unlink()

    manifest = NetworkBuildManifest(
        sumo_version=metadata.version("eclipse-sumo"),
        source_sha256={name: content_sha256(SOURCE_DIR / name) for name in SOURCE_FILES},
        tllogic_sha256=content_sha256(out_dir / TLLOGIC_FILE),
        net_sha256=content_sha256(out_dir / NET_FILE, strip_comments=True),
        signal_plan=plan,
        link_count=len(links),
        netconvert_warnings=tuple(warnings),
    )
    return NetworkBuild(
        net_path=out_dir / NET_FILE,
        tllogic_path=out_dir / TLLOGIC_FILE,
        links=tuple(links),
        manifest=manifest,
    )


def _check_embedded_program(net_path: Path, plan: SignalPlan, phase_count: int) -> None:
    logics = [
        element
        for element in ET.parse(net_path).getroot().iter("tlLogic")
        if element.get("id") == JUNCTION_ID and element.get("programID") == plan.program_id
    ]
    if len(logics) != 1 or len(logics[0].findall("phase")) != phase_count:
        raise NetworkBuildError(
            f"Network does not embed exactly one {plan.program_id!r} program with "
            f"{phase_count} phases for junction {JUNCTION_ID}"
        )
