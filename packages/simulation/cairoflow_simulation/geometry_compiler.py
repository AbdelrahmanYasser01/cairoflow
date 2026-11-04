"""Compile an `IntersectionGeometry` and a `SignalTimeline` into a SUMO network.

SUMO network coordinates are the local ground metres of the geometry (same origin at the junction
centre, x east, y north); that is a choice, recorded in the notes. Every value the network is
built from goes into the manifest with its ADR-014 label:

- Approach lanes per arm, their order from the kerb (SUMO lane 0) and their turns: the drawn lane
  polygons (their labels). Lane width: polygon area over its length along the arm.
- Exit lanes: the drawn exit polygons, else `default_exit_lanes` of `default_lane_width_m`
  (synthetic). Exit edges exist for every arm a drawn turn leads to.
- Arm length, speed limit and straight arms pointing exactly N/E/S/W: synthetic.
- Stop lines: the drawn distance from the centre (its label) and where netconvert actually puts
  the end of the approach lanes (synthetic: follows from lane widths, no drawn junction shape).
- Signal program: one complete cycle cut from the timeline, from an arm's green onset to its next
  one (by default the arm whose second onset comes first; `CompileOptions.cycle_start_s` picks
  another onset); or, with `ReplayOptions`, the timeline itself over a window where every head is
  known, followed by a clearance and the cut cycle for the drain (ADR-022). An arm's links get
  its state, except that its left-turn links follow its protected-left head where it has one; a
  turning link that is green with a conflicting green link gets permissive green ('g', yields),
  a through link 'G'.

The build reuses the two netconvert passes of `cairoflow_simulation.network`: pass one gives the
link order, the program is generated from it, pass two embeds it.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import sumolib
from pydantic import BaseModel, ConfigDict, Field

from cairoflow_contracts.evidence import (
    ARM_ORDER,
    Arm,
    GeometryProvenance,
    IntersectionGeometry,
    LanePolygon,
    SignalLabel,
    SignalState,
    SignalTimeline,
    Turn,
)
from cairoflow_contracts.experiment import (
    CompiledNetworkManifest,
    NetworkParameter,
    SignalPhaseSpec,
    SignalProgramSpec,
)
from cairoflow_simulation.network import (
    JUNCTION_ID,
    LinkInfo,
    _run_netconvert,
    content_sha256,
    read_links,
)

ARM_VECTORS: dict[Arm, tuple[float, float]] = {
    "N": (0.0, 1.0),
    "E": (1.0, 0.0),
    "S": (0.0, -1.0),
    "W": (-1.0, 0.0),
}
TURN_OF_LETTER: dict[str, Turn] = {"l": "left", "s": "through", "r": "right"}
_TURN_STEPS: dict[Turn, int] = {"u_turn": 0, "left": 1, "through": 2, "right": 3}
_WEAKNESS: dict[GeometryProvenance, int] = {"measured": 0, "approximated": 1, "synthetic": 2}
_NEXT_STATE: dict[SignalState, SignalState] = {"green": "yellow", "yellow": "red", "red": "green"}
SIGNAL_PROVENANCE: dict[SignalLabel, GeometryProvenance] = {
    "published": "measured",
    "observed-annotated": "approximated",
    "trajectory-derived": "approximated",
    "assumed": "synthetic",
}
FILES = ("nodes.nod.xml", "edges.edg.xml", "connections.con.xml", "signals.tll.xml")
NET_FILE = "network.net.xml"
_PASS_ONE = "pass1.net.xml"


class CompileError(ValueError):
    """The geometry or timeline cannot be turned into a valid SUMO network."""


class CompileOptions(BaseModel):
    """The synthetic values the compiler fills in; each is listed in the manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    arm_length_m: float = Field(200.0, ge=50.0, description="queues must fit (MVP: >= 150 m)")
    speed_limit_m_s: float = Field(13.89, gt=0)
    default_exit_lanes: int = Field(2, ge=1, le=4)
    default_lane_width_m: float = Field(3.2, ge=2.5, le=4.5)
    program_id: str = Field("timeline_cycle", pattern=r"^[A-Za-z0-9_]+$")
    cycle_start_s: float | None = Field(
        None, ge=0, description="green onset that starts the cut cycle; None: the first cycle"
    )
    right_turn_on_red: bool = Field(
        False,
        description="right turns may go on red after stopping (SUMO state 's', junction type "
        "traffic_light_right_on_red)",
    )


class ReplayOptions(BaseModel):
    """Replay the timeline itself over a window instead of repeating one cut (ADR-022).

    SUMO then shows the observed sequence of states over `window_s`, in clip time shifted to start
    at 0, so traffic driven from the observed arrivals meets the signals it met. After the window:
    every green head turns yellow for `yellow_s`, all heads show red for `clearance_s` (both
    synthetic), then the cut cycle repeats until the program covers `drain_s` more. It never
    wraps back to the replay within warm-up + horizon + drain of a run that matches it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    window_s: tuple[float, float] = Field(description="clip seconds; every head known inside")
    drain_s: float = Field(600.0, ge=0)
    yellow_s: float = Field(4.0, gt=0)
    clearance_s: float = Field(1.0, ge=0)


@dataclass(frozen=True)
class CompiledNetwork:
    net_path: Path
    links: tuple[LinkInfo, ...]
    manifest: CompiledNetworkManifest


def exit_for(approach: Arm, turn: Turn) -> Arm:
    return ARM_ORDER[(ARM_ORDER.index(approach) + _TURN_STEPS[turn]) % 4]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _centroid(lane: LanePolygon) -> tuple[float, float]:
    n = len(lane.polygon_m)
    return sum(p[0] for p in lane.polygon_m) / n, sum(p[1] for p in lane.polygon_m) / n


def _width(lane: LanePolygon) -> tuple[float, float]:
    """(width, length) of a lane polygon: its area over its extent along the arm."""
    points, n = lane.polygon_m, len(lane.polygon_m)
    twice_area = sum(
        points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1]
        for i in range(n)
    )
    along = [_dot(p, ARM_VECTORS[lane.arm]) for p in points]
    length = max(along) - min(along)
    if length <= 0:
        raise CompileError(f"lane {lane.lane_id} has no length along arm {lane.arm}")
    return abs(twice_area) / 2 / length, length


def _from_kerb(lanes: Sequence[LanePolygon], arm: Arm, inbound: bool) -> list[LanePolygon]:
    """Lanes ordered from the right-hand kerb of their direction of travel (SUMO lane 0 first)."""
    vx, vy = ARM_VECTORS[arm]
    tx, ty = (-vx, -vy) if inbound else (vx, vy)
    right = (ty, -tx)
    return sorted(lanes, key=lambda lane: (-_dot(_centroid(lane), right), lane.lane_id))


def _weakest(labels: Sequence[GeometryProvenance]) -> GeometryProvenance:
    return max(labels, key=lambda label: _WEAKNESS[label])


CycleSegment = tuple[float, dict[Arm, SignalState], dict[Arm, SignalState]]


def green_onsets(timeline: SignalTimeline) -> dict[Arm, list[float]]:
    """Per arm, the times its `all` head turns green from a known state."""
    onsets: dict[Arm, list[float]] = {}
    own = [i for i in timeline.intervals if i.head == "all"]
    for previous, current in zip(own, own[1:], strict=False):
        if previous.arm == current.arm and current.state == "green" and previous.state != "unknown":
            onsets.setdefault(current.arm, []).append(current.start_s)
    return onsets


def signal_cycle(
    timeline: SignalTimeline, start_s: float | None = None
) -> tuple[float, float, list[CycleSegment]]:
    """(start s, end s, [(duration s, state per arm, state per protected-left head)]) of one
    complete cycle: the first one, or the one starting at the green onset `start_s`."""
    onsets = green_onsets(timeline)
    if start_s is None:
        candidates = sorted(
            (times[1], ARM_ORDER.index(arm), times[0])
            for arm, times in onsets.items()
            if len(times) >= 2
        )
        if not candidates:
            raise CompileError(
                "the signal timeline holds no complete cycle (no arm turns green twice); "
                "annotate a longer clip or supply an assumed timeline"
            )
        end, _, start = candidates[0]
    else:
        ends = sorted(
            times[i + 1]
            for times in onsets.values()
            for i, t in enumerate(times[:-1])
            if abs(t - start_s) < 1e-6
        )
        if not ends:
            raise CompileError(f"no arm turns green at {start_s} s and again later")
        start = next(t for ts in onsets.values() for t in ts if abs(t - start_s) < 1e-6)
        end = ends[0]
    heads = timeline.heads()
    arms = [arm for arm, head in heads if head == "all"]
    left_arms = [arm for arm, head in heads if head == "left"]
    cuts = sorted({start, *(i.start_s for i in timeline.intervals if start < i.start_s < end)})
    segments: list[CycleSegment] = []
    for a, b in zip(cuts, [*cuts[1:], end], strict=True):
        states = {arm: timeline.state_at(arm, a) for arm in arms}
        lefts = {arm: timeline.state_at(arm, a, "left") for arm in left_arms}
        if "unknown" in (*states.values(), *lefts.values()):
            raise CompileError(f"a signal state is unknown at {a} s, inside the cut cycle")
        segments.append((b - a, states, lefts))  # type: ignore[arg-type]
    # SUMO repeats the cut: each head's last state must lead legally to its first
    _, first_all, first_left = segments[0]
    _, last_all, last_left = segments[-1]
    for name, last, first in (
        *((arm, last_all[arm], first_all[arm]) for arm in arms),
        *((f"{arm} left", last_left[arm], first_left[arm]) for arm in left_arms),
    ):
        if last != first and _NEXT_STATE[last] != first:
            raise CompileError(
                f"the cycle cut at {start} s does not close: {name} goes from {last} to {first}"
            )
    return start, end, segments


def known_window(timeline: SignalTimeline) -> tuple[float, float]:
    """The span (clip seconds) in which every head's state is known: after each head's leading
    unknown and before its trailing one. An unknown state inside it is an error."""
    starts, ends = [], []
    for arm, head in timeline.heads():
        own = [i for i in timeline.intervals if i.arm == arm and i.head == head]
        starts.append(own[0].end_s if own[0].state == "unknown" else own[0].start_s)
        ends.append(own[-1].start_s if own[-1].state == "unknown" else own[-1].end_s)
    start, end = max(starts), min(ends)
    if end <= start:
        raise CompileError("no span of the timeline has every head known")
    replay_segments(timeline, start, end)  # raises on an unknown state inside
    return start, end


def replay_segments(timeline: SignalTimeline, start: float, end: float) -> list[CycleSegment]:
    """The timeline's states over [start, end), split wherever any head changes."""
    heads = timeline.heads()
    arms = [arm for arm, head in heads if head == "all"]
    left_arms = [arm for arm, head in heads if head == "left"]
    cuts = sorted({start, *(i.start_s for i in timeline.intervals if start < i.start_s < end)})
    segments: list[CycleSegment] = []
    for a, b in zip(cuts, [*cuts[1:], end], strict=True):
        states = {arm: timeline.state_at(arm, a) for arm in arms}
        lefts = {arm: timeline.state_at(arm, a, "left") for arm in left_arms}
        if "unknown" in (*states.values(), *lefts.values()) or None in states.values():
            raise CompileError(f"a signal state is unknown at {a} s, inside the replay window")
        segments.append((b - a, states, lefts))  # type: ignore[arg-type]
    return segments


def drain_segments(
    last: CycleSegment, cycle: Sequence[CycleSegment], options: ReplayOptions
) -> list[CycleSegment]:
    """After a replay that ends in `last`: yellow for every green head, an all-red clearance, then
    `cycle` repeated until the drain is covered; every change is checked to be legal."""
    _, last_all, last_left = last

    def turned(states: dict[Arm, SignalState], to: SignalState) -> dict[Arm, SignalState]:
        if to == "red":
            return dict.fromkeys(states, "red")
        return {arm: "yellow" if s == "green" else s for arm, s in states.items()}

    segments: list[CycleSegment] = []
    if {"green", "yellow"} & {*last_all.values(), *last_left.values()}:
        segments.append((options.yellow_s, turned(last_all, "yellow"), turned(last_left, "yellow")))
    if options.clearance_s:
        segments.append((options.clearance_s, turned(last_all, "red"), turned(last_left, "red")))
    cycle_s = sum(d for d, _, _ in cycle)
    covered = sum(d for d, _, _ in segments)
    while covered < options.drain_s + cycle_s:  # one cycle spare: the program never wraps
        segments += list(cycle)
        covered += cycle_s
    previous = last
    for segment in segments:
        for before, after in (
            *((previous[1][a], segment[1][a]) for a in previous[1]),
            *((previous[2][a], segment[2][a]) for a in previous[2]),
        ):
            if before != after and _NEXT_STATE[before] != after:
                raise CompileError(f"the drain program changes a head from {before} to {after}")
        previous = segment
    return segments


def _phase_state(
    links: Sequence[LinkInfo],
    states: dict[Arm, SignalState],
    lefts: dict[Arm, SignalState],
    foes: Callable[[int, int], bool],
    right_on_red: bool = False,
) -> str:
    def head(link: LinkInfo) -> SignalState | None:
        if link.turn == "l" and link.approach in lefts:
            return lefts[link.approach]
        return states.get(link.approach)

    # a foe on yellow still has the right of way, so it counts as a rival too
    active = [link for link in links if head(link) in ("green", "yellow")]
    chars: list[str] = []
    for link in links:
        state = head(link)
        if state is None:
            raise CompileError(f"arm {link.approach} has approach lanes but no signal")
        if state == "green":
            rivals = [o for o in active if o.index != link.index and foes(link.index, o.index)]
            if link.turn == "s" and any(o.turn == "s" for o in rivals):
                raise CompileError(f"conflicting through movements are active: link {link.index}")
            chars.append("g" if rivals and link.turn != "s" else "G")
        elif state == "red" and right_on_red and link.turn == "r":
            chars.append("s")  # stop, then go if no prioritised foe stream uses the junction
        else:
            chars.append({"yellow": "y", "red": "r"}[state])
    return "".join(chars)


def _xml(root: str, rows: Sequence[str]) -> str:
    body = "\n".join(rows)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<{root}>\n{body}\n</{root}>\n'


def _edge(edge_id: str, start: str, end: str, widths: Sequence[float], speed: float) -> str:
    lanes = "\n".join(f'        <lane index="{i}" width="{w:.3f}"/>' for i, w in enumerate(widths))
    return (
        f'    <edge id="{edge_id}" from="{start}" to="{end}" numLanes="{len(widths)}" '
        f'speed="{speed:g}">\n{lanes}\n    </edge>'
    )


NETCONVERT_ARGS = (
    "--node-files", FILES[0],
    "--edge-files", FILES[1],
    "--connection-files", FILES[2],
    "--no-turnarounds", "true",
    # Keep SUMO coordinates equal to ground metres; netconvert otherwise shifts the network
    # so its lower-left corner is at (0, 0).
    "--offset.disable-normalization", "true",
)  # fmt: skip


def embed_program(
    out_dir: Path, program: SignalProgramSpec, links: Sequence[LinkInfo]
) -> tuple[Path, list[str]]:
    """Write `program` as the signal file and build the .net.xml with it (the second pass).

    Fails if the link order differs from `links` or the program is not embedded as written.
    """
    phase_rows = [
        f'        <phase duration="{p.duration_s:g}" state="{p.state}" name="{p.name}"/>'
        for p in program.phases
    ]
    logic = (
        f'    <tlLogic id="{JUNCTION_ID}" type="static" programID="{program.program_id}" '
        f'offset="0">\n' + "\n".join(phase_rows) + "\n    </tlLogic>"
    )
    (out_dir / FILES[3]).write_text(_xml("tlLogics", [logic]), encoding="utf-8", newline="\n")
    warnings = _run_netconvert(
        out_dir, [*NETCONVERT_ARGS, "--tllogic-files", FILES[3], "--output-file", NET_FILE]
    )
    net_path = out_dir / NET_FILE
    if read_links(net_path) != list(links):
        raise CompileError("the link order differs from the one the program was written for")
    programs = [
        e
        for e in ET.parse(net_path).getroot().iter("tlLogic")
        if e.get("programID") == program.program_id
    ]
    embedded = (
        [(float(p.get("duration", 0)), p.get("state")) for p in programs[0].findall("phase")]
        if programs
        else []
    )
    if len(programs) != 1 or embedded != [(p.duration_s, p.state) for p in program.phases]:
        raise CompileError(f"the network does not embed program {program.program_id} as written")
    return net_path, warnings


def compile_network(
    geometry: IntersectionGeometry,
    signals: SignalTimeline,
    out_dir: Path,
    options: CompileOptions | None = None,
    *,
    replay: ReplayOptions | None = None,
) -> CompiledNetwork:
    """Write SUMO plain XML and a .net.xml with the timeline's cycle into `out_dir` (new).

    With `replay`, the program replays the timeline over `replay.window_s` and then drains with
    the cut cycle (`ReplayOptions`); without it, SUMO repeats the cut cycle, as before.
    """
    options = options or CompileOptions()
    if signals.clip_id != geometry.clip_id:
        raise CompileError("the signal timeline and the geometry belong to different clips")
    approach: dict[Arm, list[LanePolygon]] = {}
    for arm in ARM_ORDER:
        drawn = [x for x in geometry.lanes if x.arm == arm and x.direction == "approach"]
        if drawn:
            approach[arm] = _from_kerb(drawn, arm, inbound=True)
    if not approach:
        raise CompileError("the geometry has no approach lanes")
    notes = [
        "SUMO network coordinates are the geometry's local ground metres",
        "arms are assumed straight and exactly N/E/S/W",
        "no pedestrians, no U-turns (netconvert --no-turnarounds)",
    ]
    if any("u_turn" in lane.allowed_turns for lanes in approach.values() for lane in lanes):
        notes.append("u_turn markings are not built")
    if geometry.ground_source_sha256 is not None:
        notes.append(
            "ground frame from a published ground dataset (SHA-256 "
            f"{geometry.ground_source_sha256}), not from a camera calibration"
        )
    exit_arms = {
        exit_for(arm, turn)
        for arm, lanes in approach.items()
        for lane in lanes
        for turn in lane.allowed_turns
        if turn != "u_turn"
    }
    arms = sorted(set(approach) | exit_arms, key=ARM_ORDER.index)

    params = [
        NetworkParameter(
            name="speed_limit",
            value=options.speed_limit_m_s,
            unit="m/s",
            provenance="synthetic",
            source="CompileOptions.speed_limit_m_s",
        )
    ]
    node_type = "traffic_light"
    if options.right_turn_on_red:
        node_type = "traffic_light_right_on_red"
        params.append(
            NetworkParameter(
                name="signal.right_turn_on_red",
                value=1,
                unit="count",
                provenance="synthetic",
                source="CompileOptions.right_turn_on_red: right turns stop, then may go on red",
            )
        )
    node_rows = [f'    <node id="{JUNCTION_ID}" x="0" y="0" type="{node_type}"/>']
    edge_rows: list[str] = []
    exit_counts: dict[Arm, int] = {}
    for arm in arms:
        x, y = (options.arm_length_m * c for c in ARM_VECTORS[arm])
        node_rows.append(f'    <node id="{arm}" x="{x:g}" y="{y:g}" type="dead_end"/>')
        params.append(
            NetworkParameter(
                name=f"{arm}.arm_length",
                value=options.arm_length_m,
                unit="m",
                provenance="synthetic",
                source="CompileOptions.arm_length_m: beyond the camera's field of view",
            )
        )
        if arm in approach:
            lanes = approach[arm]
            params.append(
                NetworkParameter(
                    name=f"{arm}.approach_lanes",
                    value=len(lanes),
                    unit="count",
                    provenance=_weakest([lane.provenance for lane in lanes]),
                    source="drawn approach lane polygons",
                )
            )
            widths = []
            for index, lane in enumerate(lanes):
                width, length = _width(lane)
                widths.append(width)
                params += [
                    NetworkParameter(
                        name=f"{lane.lane_id}.sumo_lane_index",
                        value=index,
                        unit="count",
                        provenance=lane.provenance,
                        source="order from the right-hand kerb of the drawn polygons",
                    ),
                    NetworkParameter(
                        name=f"{lane.lane_id}.width",
                        value=round(width, 3),
                        unit="m",
                        provenance=lane.provenance,
                        source=f"polygon area over its {length:.1f} m along the arm",
                    ),
                ]
            edge_rows.append(_edge(f"{arm}_in", arm, JUNCTION_ID, widths, options.speed_limit_m_s))
        if arm in exit_arms:
            drawn = [x for x in geometry.lanes if x.arm == arm and x.direction == "exit"]
            if drawn:
                widths = [_width(lane)[0] for lane in _from_kerb(drawn, arm, inbound=False)]
                label = _weakest([lane.provenance for lane in drawn])
                source = "drawn exit lane polygons"
            else:
                widths = [options.default_lane_width_m] * options.default_exit_lanes
                label, source = "synthetic", "CompileOptions.default_exit_lanes: none drawn"
            exit_counts[arm] = len(widths)
            params.append(
                NetworkParameter(
                    name=f"{arm}.exit_lanes",
                    value=len(widths),
                    unit="count",
                    provenance=label,
                    source=source,
                )
            )
            edge_rows.append(_edge(f"{arm}_out", JUNCTION_ID, arm, widths, options.speed_limit_m_s))

    connection_rows: list[str] = []
    for arm, lanes in approach.items():
        for turn in ("right", "through", "left"):
            users = [i for i, lane in enumerate(lanes) if turn in lane.allowed_turns]
            if turn == "left":
                users.reverse()  # innermost first, so parallel left turns do not cross
            target = exit_for(arm, turn)
            for k, from_lane in enumerate(users):
                n = exit_counts[target]
                to_lane = max(n - 1 - k, 0) if turn == "left" else min(k, n - 1)
                connection_rows.append(
                    f'    <connection from="{arm}_in" to="{target}_out" '
                    f'fromLane="{from_lane}" toLane="{to_lane}"/>'
                )

    out_dir.mkdir(parents=True, exist_ok=False)
    for name, root, rows in (
        (FILES[0], "nodes", node_rows),
        (FILES[1], "edges", edge_rows),
        (FILES[2], "connections", connection_rows),
    ):
        (out_dir / name).write_text(_xml(root, rows), encoding="utf-8", newline="\n")
    warnings = _run_netconvert(out_dir, [*NETCONVERT_ARGS, "--output-file", _PASS_ONE])
    links = read_links(out_dir / _PASS_ONE)
    node = sumolib.net.readNet(str(out_dir / _PASS_ONE)).getNode(JUNCTION_ID)

    start, end, segments = signal_cycle(signals, options.cycle_start_s)
    cycle_text = f"cut from timeline {signals.timeline_id} ({signals.label}) at {start} s"
    if replay is not None:
        a, b = replay.window_s
        replayed = replay_segments(signals, a, b)
        drain = drain_segments(replayed[-1], segments, replay)
        _, last_all, last_left = replayed[-1]
        active = {"green", "yellow"} & {*last_all.values(), *last_left.values()}
        transition_s = (replay.yellow_s if active else 0.0) + replay.clearance_s
        cycle_text = (
            f"replay of timeline {signals.timeline_id} ({signals.label}) over [{a:g}, {b:g}) s, "
            f"then {replay.yellow_s:g} s yellow and {replay.clearance_s:g} s all-red (synthetic) "
            f"and the cycle cut at {start} s repeated for the drain"
        )
        params += [
            NetworkParameter(name="signal.replay_start", value=a, unit="s",
                             provenance=SIGNAL_PROVENANCE[signals.label],
                             source="clip time where the replay starts (simulation time 0)"),
            NetworkParameter(name="signal.replay_end", value=b, unit="s",
                             provenance=SIGNAL_PROVENANCE[signals.label],
                             source="clip time where the replay ends and the drain begins"),
            NetworkParameter(name="signal.drain_transition", value=transition_s, unit="s",
                             provenance="synthetic",
                             source="ReplayOptions.yellow_s and clearance_s after the replay"),
        ]  # fmt: skip
        segments, start = [*replayed, *drain], a
    phases = [
        SignalPhaseSpec(
            name=f"p{i}_"
            + "_".join(
                [
                    *(f"{arm}{state[0]}" for arm, state in states.items()),
                    *(f"{arm}L{state[0]}" for arm, state in lefts.items()),
                ]
            ),
            duration_s=round(duration, 6),
            state=_phase_state(links, states, lefts, node.areFoes, options.right_turn_on_red),
            arm_states=states,
            left_states=lefts,
        )
        for i, (duration, states, lefts) in enumerate(segments)
    ]
    program = SignalProgramSpec(
        program_id=options.program_id,
        timeline_id=signals.timeline_id,
        label=signals.label,
        annotation_sha256=signals.annotation_sha256,
        cycle_start_s=start,
        cycle_s=round(sum(p.duration_s for p in phases), 6),
        phases=tuple(phases),
    )
    params.append(
        NetworkParameter(
            name="signal.cycle",
            value=program.cycle_s,
            unit="s",
            provenance=SIGNAL_PROVENANCE[signals.label],
            source=f"{cycle_text}; timeline source: {signals.source}",
        )
    )
    net_path, more_warnings = embed_program(out_dir, program, links)
    warnings += more_warnings
    (out_dir / _PASS_ONE).unlink()

    net = sumolib.net.readNet(str(net_path))
    stop_lines = {g.arm: g for g in geometry.gates if g.role == "stop_line"}
    for arm in approach:
        gate = stop_lines[arm]
        (ax, ay), (bx, by) = gate.segment_m
        lane_end = net.getEdge(f"{arm}_in").getLane(0).getShape()[-1]
        params += [
            NetworkParameter(
                name=f"{arm}.stop_line_distance_drawn",
                value=round(_dot(((ax + bx) / 2, (ay + by) / 2), ARM_VECTORS[arm]), 3),
                unit="m",
                provenance=gate.provenance,
                source="midpoint of the drawn stop line, from the junction centre",
            ),
            NetworkParameter(
                name=f"{arm}.stop_line_distance_sumo",
                value=round(_dot(lane_end, ARM_VECTORS[arm]), 3),
                unit="m",
                provenance="synthetic",
                source="end of the approach lanes in netconvert's junction shape",
            ),
        ]

    manifest = CompiledNetworkManifest(
        geometry_id=geometry.geometry_id,
        geometry_sha256=hashlib.sha256(geometry.model_dump_json().encode("utf-8")).hexdigest(),
        sumo_version=metadata.version("eclipse-sumo"),
        plain_xml_sha256={name: content_sha256(out_dir / name) for name in FILES},
        net_sha256=content_sha256(net_path, strip_comments=True),
        link_count=len(links),
        parameters=tuple(params),
        signal_program=program,
        netconvert_warnings=tuple(warnings),
        notes=tuple(notes),
    )
    return CompiledNetwork(net_path=net_path, links=tuple(links), manifest=manifest)
