"""Server-rendered HTML for the product flow (cairoflow_api.flow). No scripts: the inspector's
selection is a link (`?e=<entity id>`), so the page keeps the strict Content-Security-Policy.

Every figure is a value a pinned record or the World Model view already holds; this module
formats and links them. Every string is HTML-escaped, and the kind of each figure is labelled:
observed (NGSIM positions), approximated (our fixed rules), simulated (SUMO), model-generated
(Muse Spark text), model output (SAM 3.1) or computed by code.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape
from urllib.parse import urlencode

from cairoflow_api.demo import (
    DECISION_METRICS,
    FOOTAGE_NOTE,
    SAM_OVERLAY,
    SAM_RECORD,
    SAM_REVIEW,
    Demo,
    baseline_ranges,
    differences,
)
from cairoflow_api.flow import (
    DEFAULT_LAYERS,
    LAYERS,
    ZOOMS,
    Problem,
    WorldViewLoad,
    find_problem,
)
from cairoflow_api.html import (
    KIND_TEXT,
    METRIC_TEXT,
    UNIT_TEXT,
    _badge,
    _difference,
    _ensemble_body,
    _fig,
    _hypothesis,
    _model_text,
    _page,
    _stored_rest,
    _table,
)
from cairoflow_api.hypotheses import stored_citations_of_demo
from cairoflow_contracts.evidence import IntersectionGeometry
from cairoflow_contracts.worldview import WorldEntity, WorldView
from cairoflow_evaluation.report import RECOMMENDATION_RULE, SELECTION_RULE

STEPS = (
    ("intersection", "Intersection"),
    ("model", "World Model"),
    ("state", "Traffic state"),
    ("problem", "Problem"),
    ("interventions", "Interventions"),
    ("results", "Experiment results"),
    ("decision", "Decision"),
)
PAGES = (  # one page per part of the flow, each on its own URL (cairoflow_api.world_html)
    ("/world", "World Model inspector"),
    ("/problem", "Problem record"),
    ("/decision/trace", "Decision trace"),
)
FLOW_CSS = """
.flow-nav { position: sticky; top: 0; z-index: 5; background: var(--bg); padding: 6px 0;
  border-bottom: 1px solid var(--line); display: flex; flex-wrap: wrap; gap: 2px 16px;
  font-size: 0.85rem; }
.flow-nav a { text-decoration: none; }
.inspector { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
.inspector .fig-col { flex: 0 0 auto; width: min(100%, 360px); }
.inspector .panel-col { flex: 1 1 380px; min-width: 0; }
svg.wm { width: 100%; height: auto; max-height: 80vh; background: var(--panel);
  border: 1px solid var(--line); border-radius: 6px; }
svg.wm a { cursor: pointer; }
svg.wm .lane { fill: var(--bg); stroke: var(--muted); stroke-width: 0.25; }
svg.wm .lane.exit { fill: var(--panel); }
svg.wm .lane:hover, svg.wm .mv:hover, svg.wm .cz:hover { stroke: var(--fg); }
svg.wm .queue { fill: var(--appr); fill-opacity: 0.55; stroke: var(--appr); stroke-width: 0.2; }
svg.wm .queue.censored { fill-opacity: 0.3; stroke-dasharray: 1 0.6; }
svg.wm .mv { fill: none; stroke-opacity: 0.6; stroke-linecap: round; }
svg.wm .mv.left { stroke: var(--appr); } svg.wm .mv.through { stroke: var(--sim); }
svg.wm .mv.right { stroke: var(--obs); } svg.wm .mv.uturn { stroke: var(--bad); }
svg.wm .gate { stroke-width: 0.9; stroke-linecap: butt; }
svg.wm .gate.stop_line { stroke: var(--bad); } svg.wm .gate.exit_line { stroke: var(--muted); }
svg.wm .sh rect { fill: var(--fg); } svg.wm .sh text { fill: var(--bg); font-weight: 700; }
svg.wm .cz { fill: var(--assm); fill-opacity: 0.35; stroke: var(--assm); stroke-width: 0.2;
  stroke-dasharray: 0.6 0.4; }
svg.wm .lbl { fill: var(--muted); font-size: 2.6px; text-anchor: middle; pointer-events: none; }
svg.wm .sel { stroke: var(--model) !important; stroke-width: 0.9 !important; }
svg.wm .sh.sel rect { stroke: var(--model); stroke-width: 0.6; }
.layers a { margin-right: 10px; } .layers a.on { font-weight: 700; }
.swatch { display: inline-block; width: 14px; height: 8px; margin: 0 4px 0 10px; }
.panel dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 4px 0; }
.panel dt { color: var(--muted); font-size: 0.85rem; } .panel dd { margin: 0; }
.ev { border-left: 3px solid var(--line); padding-left: 8px; margin: 6px 0; }
.abstain { background: var(--panel); border-left: 4px solid var(--appr); padding: 8px 12px; }
"""
ARM_AXIS = {"N": (0.0, 1.0), "S": (0.0, -1.0), "E": (1.0, 0.0), "W": (-1.0, 0.0)}
CORE_BOX = (-34.0, 36.0, -50.0, 50.0)  # x0, x1, y0, y1 in ground metres
_KIND_OF_VIEW = {"observed": "observed", "approximated": "derived", "assumed": "assumed"}
INCOMING = {  # a relation read from its target's side
    "contains": "is part of",
    "allows": "is allowed by",
    "controlled_by": "controls",
    "involves": "is involved in",
    "queues_on": "has a queue on it:",
    "observed_in": "has observations:",
}


def _url(e: str | None, layers: str, zoom: str, anchor: str = "model") -> str:
    query = {}
    if e:
        query["e"] = e
    if layers != DEFAULT_LAYERS:
        query["layers"] = layers
    if zoom != "core":
        query["zoom"] = zoom
    return "/flow" + (f"?{urlencode(query, safe=':|')}" if query else "") + f"#{anchor}"


def _num(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return escape(str(value))
    if isinstance(value, float):
        return escape(f"{value:.4g}")
    return escape(str(value))


# --- The inspector's picture --------------------------------------------------------------------


def _pt(x: float, y: float) -> str:
    return f"{x:.2f},{-y:.2f}"


def _dot(p: tuple[float, float], q: tuple[float, float]) -> float:
    return p[0] * q[0] + p[1] * q[1]


def _mid(segment) -> tuple[float, float]:
    (x1, y1), (x2, y2) = segment
    return (x1 + x2) / 2, (y1 + y2) / 2


def _line(name: str, a: tuple[float, float], b: tuple[float, float], extra: str = "") -> str:
    return (
        f'<line class="{name}" x1="{a[0]:.2f}" y1="{-a[1]:.2f}" '
        f'x2="{b[0]:.2f}" y2="{-b[1]:.2f}"{extra}/>'
    )


def _svg(
    view: WorldView,
    geometry: IntersectionGeometry,
    selected: str | None,
    layers: str,
    zoom: str,
    href: Callable[[str], str] | None = None,
) -> str:
    """The junction map; each shape links to `href(entity_id)` (default: /flow?e=<entity id>)."""
    gates = {(g.arm, g.role): g for g in geometry.gates}
    lanes = {lane.lane_id: lane for lane in geometry.lanes}
    if zoom == "core":
        x0, x1, y0, y1 = CORE_BOX
    else:
        xs = [p[0] for lane in geometry.lanes for p in lane.polygon_m]
        ys = [p[1] for lane in geometry.lanes for p in lane.polygon_m]
        x0, x1, y0, y1 = min(xs) - 4, max(xs) + 4, min(ys) - 4, max(ys) + 4
    sel = selected or ""

    def link(entity_id: str, inner: str, title: str) -> str:
        url = escape(href(entity_id) if href else _url(entity_id, layers, zoom))
        return f'<a href="{url}"><title>{escape(title)}</title>{inner}</a>'

    def cls(name: str, entity_id: str) -> str:
        return f'class="{name}{" sel" if entity_id == sel else ""}"'

    out: list[str] = []
    if "l" in layers:
        for entity in view.of_type("lane"):
            lane = lanes.get(entity.entity_id.split(":", 1)[1])
            if lane is None:
                continue
            side = "approach" if lane.direction == "approach" else "exit"
            points = " ".join(_pt(*p) for p in lane.polygon_m)
            name = f"lane {side}" + (" sel" if entity.entity_id == sel else "")
            title = f"{entity.label}: {side}, allowed turns {', '.join(lane.allowed_turns) or '-'}"
            out.append(
                link(entity.entity_id, f'<polygon class="{name}" points="{points}"/>', title)
            )
            ax = ARM_AXIS[lane.arm]
            perp = (-ax[1], ax[0])
            far = max(_dot(p, ax) for p in lane.polygon_m)
            cs = [_dot(p, perp) for p in lane.polygon_m]
            s_text = min(far, 46.0 if zoom == "core" else far) - 2.5
            c_mid = (min(cs) + max(cs)) / 2
            x, y = ax[0] * s_text + perp[0] * c_mid, ax[1] * s_text + perp[1] * c_mid
            out.append(
                f'<text class="lbl" x="{x:.2f}" y="{-y + 0.9:.2f}">{escape(lane.lane_id)}</text>'
            )
    if "q" in layers:
        for entity in view.of_type("queue"):
            lane = lanes.get(entity.entity_id.split(":", 1)[1])
            stop = gates.get((lane.arm, "stop_line")) if lane else None
            length = entity.attributes.get("max_length_m")
            if lane is None or stop is None or not isinstance(length, (int, float)):
                continue
            ax = ARM_AXIS[lane.arm]
            perp = (-ax[1], ax[0])
            s0 = _dot(_mid(stop.segment_m), ax)
            far = max(_dot(p, ax) for p in lane.polygon_m)
            cs = [_dot(p, perp) for p in lane.polygon_m]
            s1 = min(s0 + float(length), far)
            if s1 <= s0:
                continue
            corners = [(s0, min(cs)), (s1, min(cs)), (s1, max(cs)), (s0, max(cs))]
            points = " ".join(
                _pt(ax[0] * s + perp[0] * c, ax[1] * s + perp[1] * c) for s, c in corners
            )
            censored = entity.status == "censored"
            name = (
                "queue"
                + (" censored" if censored else "")
                + (" sel" if entity.entity_id == sel else "")
            )
            bound = "at least " if entity.uncertainty and entity.uncertainty.lower_bound else ""
            title = f"{entity.label}: longest {bound}{float(length):.0f} m in the window"
            out.append(
                link(entity.entity_id, f'<polygon class="{name}" points="{points}"/>', title)
            )
    if "g" in layers:
        for entity in view.of_type("approach"):
            arm = entity.entity_id.split(":", 1)[1]
            for role in ("stop_line", "exit_line"):
                gate = gates.get((arm, role))
                if gate is None:
                    continue
                name = f"gate {role}" + (" sel" if entity.entity_id == sel else "")
                a, b = gate.segment_m
                title = f"{entity.label}: {role.replace('_', ' ')}"
                out.append(link(entity.entity_id, _line(name, a, b), title))
    if "m" in layers:
        for entity in view.of_type("movement"):
            code = entity.entity_id.split(":", 1)[1]
            start, end = gates.get((code[0], "stop_line")), gates.get((code[1], "exit_line"))
            flow = entity.attributes.get("flow_veh_per_h")
            if start is None or end is None or not isinstance(flow, (int, float)):
                continue
            width = 0.3 + float(flow) / 400.0
            turn = str(entity.attributes.get("turn", "through"))
            name = f"mv {turn}" + (" sel" if entity.entity_id == sel else "")
            count = entity.attributes.get("count_veh")
            title = f"{entity.label}: {count} vehicles, {float(flow):.0f} veh/h"
            a, b = _mid(start.segment_m), _mid(end.segment_m)
            line = _line(name, a, b, f' stroke-width="{width:.2f}"')
            out.append(link(entity.entity_id, line, title))
    if "c" in layers:
        for entity in view.of_type("conflict_zone"):
            x, y = entity.attributes.get("x_m"), entity.attributes.get("y_m")
            if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
                continue
            name = "cz" + (" sel" if entity.entity_id == sel else "")
            out.append(
                link(
                    entity.entity_id,
                    f'<circle class="{name}" cx="{x:.2f}" cy="{-y:.2f}" r="1.1"/>',
                    f"{entity.label} (assumed, chord rule)",
                )
            )
    if "s" in layers:
        for entity in view.of_type("signal_head"):
            arm = str(entity.attributes.get("arm"))
            stop = gates.get((arm, "stop_line"))
            if stop is None or arm not in ARM_AXIS:
                continue
            ax = ARM_AXIS[arm]
            perp = (-ax[1], ax[0])
            mx, my = _mid(stop.segment_m)
            side = 4.5 if entity.attributes.get("head") == "left" else 0.0
            x, y = mx + ax[0] * 5 + perp[0] * side, my + ax[1] * 5 + perp[1] * side
            tag = arm + ("L" if entity.attributes.get("head") == "left" else "")
            name = "sh" + (" sel" if entity.entity_id == sel else "")
            rect = f'<rect x="{x - 2:.2f}" y="{-y - 1.6:.2f}" width="4" height="3.2" rx="0.6"/>'
            text = (
                f'<text x="{x:.2f}" y="{-y + 0.9:.2f}" text-anchor="middle" font-size="2.4">'
                f"{escape(tag)}</text>"
            )
            title = f"{entity.label}: estimated from trajectories, not read from the signal"
            out.append(link(entity.entity_id, f'<g class="{name}">{rect}{text}</g>', title))
    box = f"{x0:g} {-y1:g} {x1 - x0:g} {y1 - y0:g}"
    return (
        f'<svg class="wm" viewBox="{box}" role="img" aria-label="World Model: lanes, gates, '
        f'movements, queues and signal heads of the junction">' + "".join(out) + "</svg>"
    )


def _controls(selected: str | None, layers: str, zoom: str) -> str:
    toggles = []
    for letter, name in LAYERS.items():
        on = letter in layers
        new = (
            layers.replace(letter, "") if on else "".join(k for k in LAYERS if k in layers + letter)
        )
        toggles.append(
            f'<a class="{"on" if on else ""}" href="{escape(_url(selected, new, zoom))}">'
            f"{'&#9745;' if on else '&#9744;'} {escape(name)}</a>"
        )
    zooms = " ".join(
        f'<a class="{"on" if z == zoom else ""}" href="{escape(_url(selected, layers, z))}">{z}</a>'
        for z in ZOOMS
    )
    return f'<p class="layers">{"".join(toggles)}<br>View: {zooms}</p>'


def _legend() -> str:
    def swatch(color: str, text: str) -> str:
        return f'<span class="swatch" style="background: var({color})"></span>{escape(text)}'

    return (
        '<p class="caption">Ground metres, north up. '
        + swatch("--bad", "stop line")
        + swatch("--muted", "exit line")
        + swatch("--appr", "longest queue (dashed: censored, at least)")
        + swatch("--sim", "through")
        + swatch("--appr", "left")
        + swatch("--obs", "right")
        + " · line width is flow. Black tags are signal heads; the grey circles are assumed "
        "conflict zones (a chord rule, not surveyed). Click anything.</p>"
    )


# --- The inspector's panel ---------------------------------------------------------------------


def _evidence_texts(demo: Demo) -> dict[str, str]:
    return {item.evidence_id: item.summary for item in demo.bundle.items} if demo.bundle else {}


def _citations(demo: Demo, evidence_id: str) -> list[str]:
    cited = []
    for h in demo.reasoning.hypotheses:
        if evidence_id in h.supporting_evidence_ids:
            cited.append(f"{h.hypothesis_id} (supporting)")
        if evidence_id in h.contradicting_evidence_ids:
            cited.append(f"{h.hypothesis_id} (contradicting)")
    for p in demo.records["proposals"]["proposals"]:
        if evidence_id in p.get("evidence_ids", []):
            cited.append(f"{p['intervention_id']} (evidence listed)")
    return cited


def _record_provenance(entity: WorldEntity, geometry: IntersectionGeometry, demo: Demo) -> str:
    kind, _, key = entity.entity_id.partition(":")
    rows: list[tuple[str, str]] = []
    if kind == "lane":
        lane = next((x for x in geometry.lanes if x.lane_id == key), None)
        if lane:
            rows = [("Geometry record", lane.source), ("Provenance label", lane.provenance)]
    elif kind == "approach":
        for gate in geometry.gates:
            if gate.arm == key:
                rows.append((gate.role.replace("_", " ").capitalize(), gate.source))
    elif kind == "movement":
        m = next(
            (
                x
                for x in demo.records["scenario"]["movements"]
                if x["approach"] == key[0] and x["exit"] == key[1]
            ),
            None,
        )
        if m:
            low, high = m["flow_ci95_veh_per_h"]
            rows = [
                ("Scenario record", f"{m['count_veh']} stop-line to exit-line crossings in "
                 f"{demo.records['scenario']['window']['duration_s']:.0f} s = "
                 f"{m['flow_veh_per_h']:.0f} veh/h, exact Poisson 95% [{low:.0f}, {high:.0f}]; "
                 f"{m['lane_unknown']} with no lane"),
            ]  # fmt: skip
    if not rows:
        return ""
    items = "".join(f"<dt>{escape(a)}</dt><dd>{escape(b)}</dd>" for a, b in rows)
    return f"<h4>Where this value was recorded</h4><dl>{items}</dl>"


def _panel(
    view: WorldView,
    geometry: IntersectionGeometry,
    demo: Demo,
    entity: WorldEntity,
    layers: str,
    zoom: str,
) -> str:
    by_id = {e.entity_id: e for e in view.entities}
    kind = entity.evidence_kind
    parts = [
        f"<h3>{escape(entity.label)}</h3>",
        f'<p><code>{escape(entity.entity_id)}</code> · <span class="badge kind-{kind}">'
        f"{escape(KIND_TEXT.get(kind, kind))}</span> · status <strong>{escape(entity.status)}"
        f"</strong></p>",
    ]
    if entity.attributes:
        rows = "".join(
            f"<dt>{escape(k)}</dt><dd>{_num(v)}</dd>" for k, v in entity.attributes.items()
        )
        parts.append(f"<dl>{rows}</dl>")
    u = entity.uncertainty
    if u is not None:
        bits = []
        if u.interval95:
            bits.append(f"95% interval [{u.interval95[0]:g}, {u.interval95[1]:g}] {u.unit or ''}")
        if u.lower_bound:
            bits.append("a lower bound: the data are censored")
        if u.note:
            bits.append(u.note)
        text = escape("; ".join(bits)) if bits else "none recorded beyond the values above"
        parts.append(f"<p><strong>Uncertainty.</strong> {text}</p>")
    where = (
        "the whole snapshot"
        if entity.source.path == "."
        else f"<code>{escape(entity.source.path)}</code>"
    )
    parts.append(f"<p><strong>Source.</strong> {escape(entity.source.model)} {where}</p>")
    parts.append(_record_provenance(entity, geometry, demo))
    links = []
    for r in view.relations:
        if r.source_id == entity.entity_id or r.target_id == entity.entity_id:
            other = r.target_id if r.source_id == entity.entity_id else r.source_id
            arrow = r.relation if r.source_id == entity.entity_id else INCOMING[r.relation]
            if other in by_id:
                href = escape(_url(other, layers, zoom))
                note = f"({escape(r.evidence_kind)}: {escape(r.basis)})"
                links.append(
                    f'<li>{escape(arrow.replace("_", " "))} <a href="{href}">'
                    f'{escape(by_id[other].label)}</a> <span class="muted">{note}</span></li>'
                )
    if links:
        parts.append(f"<h4>Relations ({len(links)})</h4><ul>{''.join(links[:24])}</ul>")
        if len(links) > 24:
            parts.append(f'<p class="muted">{len(links) - 24} more not listed.</p>')
    texts = _evidence_texts(demo)
    refs = [ref for ref in view.evidence if entity.entity_id in ref.entity_ids]
    parts.append(f"<h4>Evidence the model was given about this ({len(refs)})</h4>")
    if not refs:
        parts.append(
            f'<p class="muted">None of the {len(view.evidence)} evidence items maps to this '
            "entity.</p>"
        )
    for ref in refs[:12]:
        cited = _citations(demo, ref.evidence_id)
        cites = (
            f'<br><span class="muted">cited by the model: {escape(", ".join(cited))} '
            f"(model-generated, not evidence)</span>"
            if cited
            else ""
        )
        parts.append(
            f'<div class="ev"><code>{escape(ref.evidence_id)}</code> '
            f'<span class="badge kind-{ref.kind}">{escape(KIND_TEXT.get(ref.kind, ref.kind))}'
            f"</span><br>{escape(texts.get(ref.evidence_id, ''))}{cites}</div>"
        )
    if len(refs) > 12:
        parts.append(f'<p class="muted">{len(refs) - 12} more not listed.</p>')
    return '<div class="panel">' + "\n".join(p for p in parts if p) + "</div>"


def _entity_index(view: WorldView, layers: str, zoom: str) -> str:
    groups = []
    for kind in ("intersection", "approach", "lane", "movement", "signal_head", "queue",
                 "conflict_zone"):  # fmt: skip
        found = view.of_type(kind)
        links = " · ".join(
            f'<a href="{escape(_url(e.entity_id, layers, zoom))}">'
            f"{escape(e.entity_id.split(':', 1)[1])}</a>"
            for e in found
        )
        groups.append(
            f"<li><strong>{escape(kind.replace('_', ' '))} ({len(found)}):</strong> {links}</li>"
        )
    return (
        f"<details><summary>All {len(view.entities)} entities</summary><ul>{''.join(groups)}</ul>"
        "</details>"
    )


def _camera_box(demo: Demo, view: WorldView, video_url: str | None) -> str:
    absent = [a.reason for a in view.absent if a.layer == "camera"]
    lines = [
        '<div class="abstain"><strong>Camera evidence is not part of this World Model.</strong> '
        + escape(absent[0] if absent else "This snapshot carries no camera layer.")
        + " The measurements in steps 2 to 4, and the vehicles SUMO replays in steps 5 to 7, come "
        "from the published NGSIM trajectories (ADR-018, ADR-019); the camera's calibration was "
        "not accepted, and no camera track feeds the simulation, the experiment or the "
        "decision.</div>"
    ]
    if demo.has_sam:
        sam = demo.records[SAM_RECORD]
        tracks = sam["tracking"]["tracks"]
        facts = f"SAM 3.1 produced {tracks} tracks on a 10 s excerpt of camera 2"
        if SAM_REVIEW in demo.records:
            s = demo.records[SAM_REVIEW]["scores"]
            facts += (
                f"; a hand check found {s['precision']['value']:.0%} of the sampled boxes are "
                f"vehicles and {s['recall']['value']:.0%} of the vehicles above the checked row "
                "have a box"
            )
        video = (
            f'<video controls preload="metadata" src="{escape(video_url)}"></video>'
            if video_url
            else f'<p class="muted">{escape(FOOTAGE_NOTE)}</p>'
            if SAM_OVERLAY in demo.absent
            else ""
        )
        lines += [
            f"<p>{_badge('sam')} {escape(facts)}. Shown for review only (CC BY-SA 4.0); detail in "
            f'<a href="/demo#sam">the demo</a>.</p>',
            video,
        ]
    return "\n".join(lines)


# --- Sections ----------------------------------------------------------------------------------


def _section(anchor: str, number: int, say: str, body: list[str]) -> str:
    title = dict(STEPS)[anchor]
    return (
        f'<section class="step" id="{anchor}"><h2>{number} · {escape(title)}</h2>'
        f'<p class="say">{escape(say)}</p>' + "\n".join(body) + "</section>"
    )


def _view_source(load: WorldViewLoad) -> str:
    """Where the World Model view of steps 2 and 3 comes from, and what it was checked against.
    The demo manifest pins no WorldView: the export is Git-ignored (ADR-033 #9)."""
    if load.view is None:
        return "The World Model view (steps 2 and 3) is not available here."
    checked = (
        "it is checked only against the world-model, geometry and evidence-bundle hashes the "
        "pinned records give, and against its own content hash"
    )
    if load.origin.startswith("exported view"):  # "exported view <path>", as loaded
        return (
            f"The World Model view in steps 2 and 3 is read from the {load.origin}, a Git-ignored "
            f"export that no manifest pins: {checked}."
        )
    return f"The World Model view in steps 2 and 3 is {load.origin}: {checked}."


def _intersection(demo: Demo, load: WorldViewLoad) -> str:
    scenario = demo.records["scenario"]
    total = sum(m["count_veh"] for m in scenario["movements"])
    legend = " ".join(_badge(k) for k in ("observed", "derived", "assumed", "simulated", "model",
                                          "checked", "sam"))  # fmt: skip
    return _section("intersection", 1, "One real junction, one real quarter-hour.", [
        f"<p><strong>{escape(demo.manifest.title)}</strong></p>",
        f"<p>{total} vehicles crossed it in {scenario['window']['duration_s']:.0f} s. "
        f"Source: {escape(scenario['input']['attribution'])}; "
        f"{escape(scenario['input']['licence'])}.</p>",
        "<p>The flow below goes from this junction's World Model to a decision. It reads the "
        "committed records the demo manifest pins, each checked against its SHA-256. "
        f"{escape(_view_source(load))} No model is called and nothing is simulated while you "
        "read. <strong>Decision support only.</strong></p>",
        f"<p>Labels used on every figure: {legend}</p>",
    ])  # fmt: skip


SHOWN_BACK = 80  # characters of a request echoed in a not-found note


def _shown_back(raw: str) -> str:
    return escape(raw if len(raw) <= SHOWN_BACK else raw[:SHOWN_BACK] + "…")


def _not_found(
    view: WorldView, entity: WorldEntity, zoom: str, requested_entity: str | None,
    requested_zoom: str | None,
) -> str:  # fmt: skip
    """What the request asked for and the page could not show, said, never silently replaced."""
    notes = []
    if requested_entity is not None and requested_entity != entity.entity_id:
        notes.append(
            f"The entity &#8220;{_shown_back(requested_entity)}&#8221; asked for by "
            f"<code>?e=</code> is not in this view (none of its {len(view.entities)} entities "
            f"has that id): the panel shows {escape(entity.entity_id)} instead."
        )
    if requested_zoom is not None and requested_zoom != zoom:
        notes.append(
            f"The zoom &#8220;{_shown_back(requested_zoom)}&#8221; asked for by "
            f"<code>?zoom=</code> is not one of {escape(', '.join(ZOOMS))}: the map shows "
            f"{escape(zoom)} instead."
        )
    if not notes:
        return ""
    return (
        '<div class="abstain notfound"><strong>Not in this view.</strong> '
        + " ".join(notes)
        + "</div>"
    )


def _model(
    demo: Demo,
    load: WorldViewLoad,
    selected: str | None,
    layers: str,
    zoom: str,
    video_url: str | None,
    requested_entity: str | None = None,
    requested_zoom: str | None = None,
) -> str:
    say = "Click a lane, queue, movement, gate or signal head to see what it is made of."
    if load.view is None or load.geometry is None:
        return _section("model", 2, say, [
            f'<div class="abstain"><strong>The World Model view cannot be shown.</strong> '
            f"{escape(load.problem or '')}</div>"
        ])  # fmt: skip
    view = load.view
    entity = next((e for e in view.entities if e.entity_id == selected), None)
    if entity is None:
        entity = view.of_type("intersection")[0]
    missed = _not_found(view, entity, zoom, requested_entity, requested_zoom)
    absent = "".join(
        f"<li><strong>{escape(a.layer.replace('_', ' '))}:</strong> {escape(a.reason)}</li>"
        for a in view.absent
    )
    unmapped = len(view.unmapped_evidence_ids)
    body = [
        missed,
        f'<p class="muted">{len(view.entities)} entities, {len(view.relations)} relations; '
        f"{escape(load.origin)}; view <code>{escape(view.view_sha256[:16])}</code> of world model "
        f"<code>{escape(view.world_model_sha256[:16])}</code>, the one the baseline replays.</p>",
        '<div class="inspector"><div class="fig-col">',
        _controls(entity.entity_id, layers, zoom),
        _svg(view, load.geometry, entity.entity_id, layers, zoom),
        _legend(),
        '</div><div class="panel-col">',
        _panel(view, load.geometry, demo, entity, layers, zoom),
        "</div></div>",
        _entity_index(view, layers, zoom),
        "<h3>What the view says it does not have</h3>",
        f"<ul>{absent}</ul>",
        f'<p class="muted">{len(view.ambiguities)} tracks crossed only one gate of a movement and '
        f"are listed, never counted; {unmapped} of {len(view.evidence)} evidence items describe "
        "no single entity (frame-level or whole-snapshot items).</p>",
        _camera_box(demo, view, video_url),
    ]
    return _section("model", 2, say, body)


def _state(demo: Demo, view: WorldView | None, origin: str = "") -> str:
    say = "What the junction was doing, as the World Model records it."
    if view is None:
        return _section("state", 3, say, [
            '<p class="muted">Needs the World Model view (step 2).</p>'
        ])  # fmt: skip
    arms = {"N": 0, "E": 0, "S": 0, "W": 0}
    flow_rows = []
    for e in sorted(view.of_type("movement"), key=lambda x: x.entity_id):
        count = e.attributes.get("count_veh")
        arms[e.entity_id[9]] += int(count or 0)
        u = e.uncertainty
        ci = f"[{u.interval95[0]:.0f}, {u.interval95[1]:.0f}]" if u and u.interval95 else ""
        flow = float(e.attributes.get("flow_veh_per_h", 0))
        flow_rows.append(_fig([escape(e.label), str(count), f"{flow:.0f} {ci}"],
                              _KIND_OF_VIEW[e.evidence_kind]))  # fmt: skip
    queue_rows = []
    for e in sorted(view.of_type("queue"), key=lambda x: x.entity_id):
        a = e.attributes
        bound = "&#8805; " if e.uncertainty and e.uncertainty.lower_bound else ""
        queue_rows.append(_fig([
            escape(e.entity_id.split(":", 1)[1]), str(a["max_queued_veh"]),
            f"{bound}{float(a['max_length_m']):.1f} m",
            f"{a['censored_samples']} of {a['samples']}", str(a["end_of_red_samples"]),
        ], _KIND_OF_VIEW[e.evidence_kind]))  # fmt: skip
    head_rows = []
    for e in sorted(view.of_type("signal_head"), key=lambda x: x.entity_id):
        a = e.attributes
        head_rows.append(_fig([
            escape(f"{a['arm']} {a['head']}"), f"{a['green_s']:g}", f"{a['yellow_s']:g}",
            f"{a['red_s']:g}", f"{a['unknown_s']:g}",
        ], _KIND_OF_VIEW[e.evidence_kind]))  # fmt: skip
    totals = ", ".join(f"{arm} {n}" for arm, n in arms.items())
    return _section("state", 3, say, [
        f'<p class="muted">Every figure in this step is a value of the World Model view of step 2 '
        f"({escape(origin)}); step 1 says what that view is checked against.</p>",
        f"<h3>Demand by movement</h3><p>Vehicles through each stop line to its exit line. By "
        f"approach: {escape(totals)} vehicles (sum computed by code).</p>",
        _table(["Movement", "Vehicles", "Flow, veh/h [95%]"], flow_rows),
        "<h3>Longest queue per lane</h3><p>The longest queue seen in the 900 s window. A queue "
        "that reached the end of the observed lane is a lower bound (&#8805;): the data stop "
        "there, the queue may not.</p>",
        _table(["Lane", "Vehicles", "Length", "Samples at lane end", "End-of-red samples"],
               queue_rows),
        "<h3>Signal heads</h3><p>Seconds of each state in the window. No signal log exists: "
        "these are estimated from when vehicles crossed the stop line (ADR-023), not read from "
        "a signal.</p>",
        _table(["Head", "Green", "Yellow", "Red", "Unknown"], head_rows),
    ])  # fmt: skip


def _kind_of_label(label: str) -> str:
    """The badge of a validation record's observed.label ("derived": NGSIM values aggregated by
    our fixed rules, shown as approximated, ADR-019); nothing unrecorded is shown as observed."""
    return {"observed": "observed", "assumed": "assumed"}.get(label, "derived")


def _reading_rows(readings, percent: bool) -> list[str]:
    rows = []
    for r in readings:
        scale = 100.0 if percent else 1.0
        unit = "%" if percent else f" {r.unit}"
        bound = "&#8805; " if r.lower_bound else ""
        ci = f" [{r.interval[0] * scale:.0f}, {r.interval[1] * scale:.0f}]" if r.interval else ""
        sim = "" if r.simulated is None else f"{r.simulated * scale:.0f}{unit}"
        flag = "<strong>flagged</strong>" if r.flagged else ""
        rows.append(_fig(
            [escape(r.key.split(".", 1)[1].replace("_", " ")),
             f"{bound}{r.observed * scale:.0f}{unit}{ci}", sim, escape(r.status), flag],
            _kind_of_label(r.label), "simulated",
        ))  # fmt: skip
    return rows


def _problem(demo: Demo) -> str:
    problem: Problem = find_problem(demo.records["validation"])
    checked = {g.hypothesis_id: g for g in demo.grounding.hypotheses} if demo.grounding else {}
    head = ["Where", "Observed (approximated) [95%]", "Baseline replay", "Comparable?", "Rule"]
    readings = [*problem.stopping, *problem.end_of_red, *problem.queues]
    flagged = [r for r in readings if r.flagged] or readings
    kinds = sorted({_kind_of_label(r.label) for r in flagged})
    body = [
        f'<div class="decision"><p><strong>{escape(problem.sentence)}</strong> '
        f"{_badge('checked')} {' '.join(_badge(k) for k in kinds)}</p></div>",
        '<p class="muted">Two rules on values the validation record derives from the observed '
        "trajectories by fixed rules: a movement is flagged when the whole 95% "
        "interval of its share of vehicles stopping at least once is above 50%; an approach is "
        "flagged when its longest queue is a censored lower bound of at least 50 m. This says "
        "where drivers wait. It does not say why, and it says nothing about delay.</p>",
        "<h3>Share of vehicles that stop at least once</h3>",
        _table(head, _reading_rows(problem.stopping, True)),
        "<h3>Queue at the end of red</h3>",
        _table(head, _reading_rows(problem.end_of_red, False)),
        "<h3>Longest queue in the window</h3>",
        _table(head, _reading_rows(problem.queues, False)),
        "<p>Not measurable from the data, among others:</p><ul>"
        + "".join(f"<li>{escape(x)}</li>" for x in problem.limits)
        + "</ul>",
        "<h3>What the model suggested the problem is</h3>",
        "<p>Muse Spark was asked once to read the evidence and propose hypotheses. Its words are "
        "boxed and are not evidence; code then checked what each one cites.</p>",
    ]
    stored = stored_citations_of_demo(demo)
    body += [
        _hypothesis(h, checked.get(h.hypothesis_id), stored=stored)
        for h in demo.reasoning.hypotheses
    ]
    return _section("problem", 4, "Measured first, then what the model made of it.", body)


def _interventions(demo: Demo) -> str:
    r = demo.records
    texts = {p.intervention_id: p for p in demo.reasoning.interventions}
    outcomes = {o["intervention_id"]: o for o in r["proposals"]["validation"]}
    status = {e["candidate"]: e for e in r["results"]["eligibility"]}
    ensemble = (
        {v["candidate"]: v for v in r["ensemble"]["verdict"]["candidates"]}
        if demo.has_ensemble
        else {}
    )
    body = [
        "<p>Only signal-timing changes are allowed (ADR-015). The model proposes; a deterministic "
        "validator checks each one against the compiled network; only the validator and the "
        "simulation decide what runs and what it measures.</p>"
    ]
    rows = []
    for p in r["proposals"]["proposals"]:
        pid = p["intervention_id"]
        o = outcomes.get(pid, {})
        changes = "<br>".join(
            f"{escape(c['phase_name'])}: green {c['green_s']:g} s" for c in p["phase_changes"]
        )
        e = status.get(pid, {})
        verdict = ensemble.get(pid, {}).get("verdict", "not run")
        addresses = escape(", ".join(p["addresses_hypothesis_ids"]))
        mappings = len(r["results"]["candidate_notes"].get(pid, []))
        rows.append(_fig([
            f"<strong>{escape(pid)}</strong><br>addresses {addresses}",
            changes,
            f"<strong>{escape(o.get('status', 'unknown'))}</strong>: cycle "
            f"{o.get('baseline_cycle_s', 0):g} s &#8594; {o.get('cycle_s', 0):g} s",
            f"{escape(e.get('status', 'not compared'))} ({mappings} replay mappings)",
            escape(verdict.replace("_", " ")),
        ], "model", "checked", "simulated"))  # fmt: skip
    body.append(_table(
        ["Intervention", "Proposed change (model-generated)", "Validator", "In simulation",
         "Across four timelines"], rows))  # fmt: skip
    for pid, p in texts.items():
        body.append(_model_text(f"{pid} rationale", p.rationale))
    hypothesis_ids = [h.hypothesis_id for h in demo.reasoning.hypotheses]
    body.append(_stored_rest(stored_citations_of_demo(demo), hypothesis_ids))
    body.append(
        '<p class="muted">"In simulation" is the experiment record\'s status for the candidate on '
        'the replay baseline; "Across four timelines" is the pre-registered ensemble verdict '
        "(step 6). A change the validator accepts is allowed to run, not expected to help.</p>"
    )
    return _section("interventions", 5, "What could change, and whether it is even allowed.", body)


def _results(demo: Demo) -> str:
    r = demo.records
    results, a = r["results"], demo.assessment
    diffs = differences(results)
    status = {e["candidate"]: e["status"] for e in results["eligibility"]}
    scale = baseline_ranges(results)
    head = ["Candidate", *(METRIC_TEXT[m] for m in DECISION_METRICS)]
    base = _fig(
        ["Baseline<br><span class='muted'>range over seeds</span>",
         *(f"{scale[m][0]:.1f}&#8211;{scale[m][1]:.1f} "
           f"{escape(UNIT_TEXT.get(scale[m][2], scale[m][2]))}" if m in scale else ""
           for m in DECISION_METRICS)],
        "simulated",
    )  # fmt: skip
    rows = [base] + [
        _fig([f"{escape(c)}<br><span class='muted'>{escape(status.get(c, ''))}</span>",
              *(_difference(diffs[c].get(m)) for m in DECISION_METRICS)], "simulated")
        for c in sorted(diffs)
    ]  # fmt: skip
    body = [
        f'<p>Baseline in SUMO against what was observed: <span class="status">'
        f"{escape(a.status.replace('_', ' '))}</span> {_badge('checked')}</p>",
        f"<p>{escape(a.summary)}</p>",
        f"<p>Paired difference from the baseline over {len(results['seeds'])} seeds (same "
        "vehicles, same seeds), with its 95% interval. A metric worsens when the interval "
        "excludes zero on the worse side; an improvement also needs Holm's correction "
        "(&#10003;). Mean delay and the east and west queues have no observed counterpart: they "
        "are SUMO's numbers only.</p>",
        _table(head, rows),
    ]
    if demo.has_ensemble:
        body += [
            "<h3>How sure: four signal timelines, fixed in advance</h3>",
            *_ensemble_body(demo),
            '<details><summary>Limits stated by the ensemble record</summary><ul class="caption">'
            + "".join(f"<li>{escape(x)}</li>" for x in r["ensemble"].get("limitations", []))
            + "</ul></details>",
        ]
    body.append(
        '<p class="muted">Seeds 1 to 20 were used throughout; nothing on this page ran a new '
        'simulation. Full report: <a href="/demo#results">the demo</a>.</p>'
    )
    say = "Simulated, paired, with intervals, and repeated on four estimated signal timelines."
    return _section("results", 6, say, body)


def _decision(demo: Demo) -> str:
    report = demo.records["results"]["report"]
    selected = report["selected_intervention_id"] or "none"
    recommended = report["recommended_intervention_id"] or "none"
    verdicts = demo.records["ensemble"]["verdict"]["candidates"] if demo.has_ensemble else []
    reasons = "".join(
        f"<li><strong>{escape(v['candidate'])}</strong>: {escape(v['verdict'].replace('_', ' '))}"
        f" &#8212; {escape(v['reason'])}</li>"
        for v in verdicts
    )
    return _section("decision", 7, "A recommendation needs evidence; without it, say so.", [
        f'<div class="abstain"><p><strong>Selected: {escape(selected)}. Recommended: '
        f"{escape(recommended)}.</strong></p><p>{escape(report['selection_reason'])}</p>"
        f"<p>{escape(report['recommendation'])}</p></div>",
        "<h3>Why the answer is to abstain</h3>",
        "<ul><li>Every candidate worsens at least one decision metric in simulation; the rule "
        "refuses any worsening, and nothing is traded off with a weighted score.</li>"
        "<li>The signals were estimated, not read, so a candidate counts only if it is eligible "
        "on all four estimated timelines (ADR-025). The ensemble record finds none eligible on "
        "any of them: on each timeline every candidate worsens at least one decision "
        "metric.</li>"
        f"<li>The baseline is assessed as &#8220;{escape(demo.assessment.status.replace('_', ' '))}"
        "&#8221;, and the recommendation rule below needs &#8220;validated&#8221;. Mean delay and "
        "the E and W queues have no observed counterpart to check against.</li></ul>"
        + (f"<h3>Across the four timelines</h3><ul>{reasons}</ul>" if reasons else ""),
        "<h3>What this decision does not rest on</h3>",
        "<ul><li>No camera evidence: the World Model snapshot has no camera layer.</li>"
        "<li>No AI text: the model's rationale is shown for review; the validator, the "
        "simulation and the rules decide.</li>"
        "<li>No new run: these are the recorded results on seeds 1 to 20.</li></ul>",
        f'<p class="muted">Selection rule: {escape(SELECTION_RULE)}</p>',
        f'<p class="muted">Recommendation rule: {escape(RECOMMENDATION_RULE)}</p>',
        '<p class="muted">Every pinned input and its SHA-256: <a href="/demo#provenance">'
        "provenance</a>. Decision support only: nothing here controls real traffic.</p>",
    ])  # fmt: skip


def render_flow_page(
    demo: Demo,
    load: WorldViewLoad,
    selected: str | None,
    *,
    layers: str = DEFAULT_LAYERS,
    zoom: str = "core",
    video_url: str | None = None,
    requested_entity: str | None = None,
    requested_zoom: str | None = None,
) -> str:
    """The seven steps; `requested_entity` and `requested_zoom` are the raw query values, named
    back on the page when the view has no such entity or zoom (`selected` and `zoom` are what is
    shown)."""
    nav = "".join(f'<a href="#{a}">{i} · {escape(t)}</a>' for i, (a, t) in enumerate(STEPS, 1))
    nav += "".join(f'<a href="{url}">{escape(text)} &#8599;</a>' for url, text in PAGES)
    parts = [
        "<h1>CairoFlow: from an intersection to a decision</h1>",
        f'<nav class="flow-nav">{nav}</nav>',
        _intersection(demo, load),
        _model(demo, load, selected, layers, zoom, video_url, requested_entity, requested_zoom),
        _state(demo, load.view, load.origin),
        _problem(demo),
        _interventions(demo),
        _results(demo),
        _decision(demo),
    ]
    return _page(
        f"CairoFlow flow {demo.manifest.demo_id}", "\n".join(parts), synthetic=False,
        extra_css=FLOW_CSS,
    )  # fmt: skip


def render_flow_error(message: str) -> str:
    body = (
        "<h1>Flow unavailable</h1><p>The flow shows only records pinned by the demo manifest, "
        f"and one of them does not match:</p><p><code>{escape(message)}</code></p>"
    )
    return _page("CairoFlow flow unavailable", body, synthetic=False)
