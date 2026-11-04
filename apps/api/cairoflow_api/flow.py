"""The product flow, read-only: Intersection, World Model, Traffic State, Problem, Intervention,
Experiment Results, Decision, on the records the demo manifest pins.

This module only loads and selects. It adds no data: the World Model is shown through its
`WorldView` (ADR-033), a pure projection that is checked against the committed geometry, the
baseline's recorded world-model hash and the evidence bundle the model read. The "problem" is a
rule applied to measured values the validation record already holds; nothing is estimated here.

A fresh clone has no snapshot (it is Git-ignored), so the view comes, in this order, from
`data/demo/ngsim-int2-worldview.json` (written by scripts/export_world_view.py) or is rebuilt from
the local snapshot whose SHA-256 the baseline records. With neither, the page says so and names
the command; it never fills the gap.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from cairoflow_api.demo import Demo
from cairoflow_contracts.evidence import IntersectionGeometry, TrafficWorldModel
from cairoflow_contracts.worldview import ENTITY_ID, WorldView
from cairoflow_perception.worldview import WorldViewError, build_world_view

WORLD_VIEW_FILE = "data/demo/ngsim-int2-worldview.json"
GEOMETRY_FILE = "data/geometry/ngsim-lankershim-int2/geometry.json"
SNAPSHOT_GLOB = "data/processed/ngsim-int2/*/world_model.json"
EXPORT_COMMAND = "uv run --frozen python scripts/export_world_view.py"
LAYERS = {
    "l": "lanes",
    "q": "longest queues",
    "m": "movements",
    "g": "gates",
    "s": "signal heads",
    "c": "conflict zones (assumed)",
}
DEFAULT_LAYERS = "lqmgs"
ZOOMS = ("core", "full")
ENTITY = re.compile(ENTITY_ID)
_LIVE: dict[tuple[str, str], WorldView] = {}


@dataclass(frozen=True)
class WorldViewLoad:
    """The inspector's source, or the reason there is none."""

    view: WorldView | None
    geometry: IntersectionGeometry | None
    origin: str
    problem: str | None = None


def clean_layers(raw: str) -> str:
    return "".join(letter for letter in LAYERS if letter in raw)


def load_world_view(demo: Demo, repo_root: Path) -> WorldViewLoad:
    """The WorldView of the snapshot the baseline replays, tied to every record it should match."""
    unavailable = (
        f"The World Model view is not available here. Its snapshot is Git-ignored; with the "
        f"snapshot present, run `{EXPORT_COMMAND}` once to write {WORLD_VIEW_FILE}."
    )
    try:
        geometry = IntersectionGeometry.model_validate_json(
            (repo_root / GEOMETRY_FILE).read_text("utf-8")
        )
    except (OSError, ValidationError) as error:
        return WorldViewLoad(None, None, "", f"the committed geometry cannot be read: {error}")
    wanted = demo.records["baseline"]["inputs"]["world_model_sha256"]
    path = repo_root / WORLD_VIEW_FILE
    if path.is_file():
        try:
            view = WorldView.model_validate_json(path.read_text("utf-8"))
        except ValidationError as error:
            return WorldViewLoad(None, geometry, "", f"{WORLD_VIEW_FILE} is invalid: {error}")
        origin = f"exported view {WORLD_VIEW_FILE}"
    else:
        view = _live_view(demo, repo_root, geometry, wanted)
        if view is None:
            return WorldViewLoad(None, geometry, "", unavailable)
        origin = "built now from the local snapshot (Git-ignored)"
    problems = _tie_problems(view, demo, geometry, wanted)
    if problems:
        return WorldViewLoad(None, geometry, origin, "; ".join(problems))
    return WorldViewLoad(view, geometry, origin)


def _tie_problems(
    view: WorldView, demo: Demo, geometry: IntersectionGeometry, wanted: str
) -> list[str]:
    geometry_sha = hashlib.sha256(geometry.model_dump_json().encode("utf-8")).hexdigest()
    checks = {
        "the view is of another world model than the baseline replays": (
            view.world_model_sha256 == wanted
        ),
        "the view was measured on another geometry than the committed one": (
            view.geometry_sha256 == geometry_sha
        ),
        "the view is about another evidence bundle than the Muse request carried": (
            demo.bundle is not None and view.bundle_id == demo.bundle.bundle_id
        ),
    }
    return [problem for problem, ok in checks.items() if not ok]


def _live_view(
    demo: Demo, repo_root: Path, geometry: IntersectionGeometry, wanted: str
) -> WorldView | None:
    key = (str(repo_root), wanted)
    if key in _LIVE:
        return _LIVE[key]
    for path in sorted(repo_root.glob(SNAPSHOT_GLOB)):
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != wanted:
            continue
        try:
            model = TrafficWorldModel.model_validate_json(data)
            view = build_world_view(model, geometry, bundle=demo.bundle)
        except (ValidationError, WorldViewError):
            return None
        _LIVE[key] = view
        return view
    return None


# --- The problem: a rule on measured values ----------------------------------------------------

STOP_MAJORITY = 0.5  # more than half of the vehicles stop at least once, whole interval above
QUEUE_AT_LANE_END_M = 50.0  # a censored maximum at least this long: the queue outran the data


@dataclass(frozen=True)
class Reading:
    """One observed quantity of the validation record, next to the baseline's value for it."""

    key: str
    quantity: str
    unit: str
    observed: float
    interval: tuple[float, float] | None
    lower_bound: bool
    simulated: float | None
    status: str
    flagged: bool  # meets the rule that names it a problem
    label: str = "derived"  # the record's observed.label: NGSIM values aggregated by fixed rules


@dataclass(frozen=True)
class Problem:
    stopping: tuple[Reading, ...]
    end_of_red: tuple[Reading, ...]
    queues: tuple[Reading, ...]
    sentence: str
    limits: tuple[str, ...]


def _label(key: str) -> str:
    return key.split(".", 1)[1].replace("_", " ")


def _readings(validation: dict, prefix: str) -> list[dict]:
    return [c for c in validation["comparisons"] if c["key"].startswith(prefix + ".")]


def _reading(c: dict, flagged: bool) -> Reading:
    observed = c["observed"]
    interval = observed.get("interval")
    return Reading(
        key=c["key"],
        quantity=c["quantity"],
        unit=c["unit"],
        observed=observed["value"],
        interval=(interval[0], interval[1]) if interval else None,
        lower_bound=bool(observed.get("lower_bound")),
        simulated=c["simulated"].get("value"),
        status=c["status"],
        flagged=flagged,
        label=str(observed.get("label") or "derived"),
    )


def find_problem(validation: dict) -> Problem:
    """Where, in the observed data, most vehicles stop and queues outrun the observed lane.

    Two rules, both on observed values with their intervals: a movement is flagged when the 95%
    interval of its share of vehicles stopping at least once lies above one half; an approach is
    flagged when its longest observed queue is censored (a lower bound) and at least 50 m. They
    describe where drivers wait; they do not say why, and they say nothing about delay, which has
    no observed counterpart (the record's `not_validated` list)."""
    stopping = []
    for c in _readings(validation, "stopped_share"):
        low = (c["observed"].get("interval") or (0.0, 0.0))[0]
        stopping.append(_reading(c, flagged=low > STOP_MAJORITY))
    end_of_red = [_reading(c, flagged=False) for c in _readings(validation, "end_of_red_queue")]
    queues = []
    for c in _readings(validation, "queue_max"):
        o = c["observed"]
        long_and_censored = bool(o.get("lower_bound")) and o["value"] >= QUEUE_AT_LANE_END_M
        queues.append(_reading(c, flagged=long_and_censored))
    stop_names = [_label(r.key) for r in stopping if r.flagged]
    queue_names = [r.key.split(".")[1] for r in queues if r.flagged]
    parts = []
    if stop_names:
        shares = ", ".join(
            f"{_label(r.key)} {r.observed:.0%} [{r.interval[0]:.0%}, {r.interval[1]:.0%}]"
            for r in stopping
            if r.flagged and r.interval
        )
        parts.append(f"most vehicles stop at least once on {' and '.join(stop_names)} ({shares})")
    if queue_names:
        reach = ", ".join(
            f"{r.key.split('.')[1]} at least {r.observed:.0f} m" for r in queues if r.flagged
        )
        parts.append(
            f"the longest queues on the {' and '.join(queue_names)} approaches outrun the "
            f"observed lanes ({reach})"
        )
    sentence = (
        "In the observed 15 minutes, " + "; ".join(parts) + "."
        if parts
        else "No movement or approach meets the two rules below in the observed data."
    )
    limits = tuple(f"{n['quantity']}: {n['reason']}" for n in validation["not_validated"][:4])
    return Problem(tuple(stopping), tuple(end_of_red), tuple(queues), sentence, limits)
