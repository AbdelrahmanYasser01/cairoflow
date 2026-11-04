"""Server-rendered HTML for the WorldView inspector, the problem record and the decision trace.

One deterministic URL per entity: /world lists them all, /world/{entity_id} shows one. Every page
is an offline replay of recorded files, committed records and Git-ignored local ones (the WorldView
snapshot, the world model, the NGSIM file, a run directory), and says so; nothing is computed here
but layout. Each
entity carries a kind badge from its provenance (cairoflow_api.world.entity_badge), its source
and source hash, coverage and confidence only where the snapshot records them, and an explicit
list of what is missing; a value that is not there is written "not available", never 0. Every
string is HTML-escaped; there are no scripts, so the strict Content-Security-Policy holds.
"""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from cairoflow_api.demo import Demo
from cairoflow_api.flow import DEFAULT_LAYERS
from cairoflow_api.flow_html import (
    FLOW_CSS,
    INCOMING,
    PAGES,
    _citations,
    _evidence_texts,
    _legend,
    _record_provenance,
    _svg,
)
from cairoflow_api.html import _hypothesis, _page
from cairoflow_api.hypotheses import AttachedDetail
from cairoflow_api.world import (
    BADGE_OF_CATEGORY,
    BADGES,
    CAMERA_BREAK,
    SNAPSHOT_MISSING,
    CameraRecord,
    DecisionTrace,
    Inspector,
    entity_badge,
    entity_trace,
    queue_series,
    signal_intervals,
)
from cairoflow_contracts.problem_statement import (
    ASSESSMENT_WORDS,
    AttachedHypothesis,
    ProblemStatement,
)
from cairoflow_contracts.provenance_chain import CAMERA_NOT_ACCEPTED, DecisionChain
from cairoflow_contracts.worldview import WorldEntity, WorldView

OFFLINE = (
    "Offline replay of recorded files: committed records, and Git-ignored local files this "
    "checkout holds (the WorldView snapshot, the world model, the NGSIM file, a run directory). "
    "No model is called and nothing is simulated while you read. Decision support only: nothing "
    "here controls real traffic."
)
BADGE_MEANING = {
    "dataset-derived": "counted or measured from the published NGSIM trajectories by fixed rules",
    "World Model": "the World Model's own construct: the geometry, the reconstructed signals, "
    "a rule over other entities",
    "camera probe": "from the camera (SAM 3.1 output); its calibration is not accepted",
    "simulated": "computed by SUMO runs",
    "AI-generated": "model text (Muse Spark): shown for review, never evidence",
}
WORLD_CSS = """
.badge.kind-dataset-derived { color: var(--obs); } .badge.kind-world-model { color: var(--appr); }
.badge.kind-camera-probe { color: var(--bad); } .badge.kind-ai-generated { color: var(--model); }
.badge.kind-simulated { color: var(--sim); }
section { border-top: 1px solid var(--line); margin-top: 1.25rem; }
.pages a { margin-right: 14px; }
.groups li { margin: 4px 0; } .groups a { margin-right: 6px; }
svg.series, svg.signal { width: 100%; height: auto; background: var(--panel);
  border: 1px solid var(--line); border-radius: 6px; }
svg .q { fill: none; stroke: var(--appr); stroke-width: 1.2; }
svg .cens { stroke: var(--bad); stroke-width: 0.6; }
svg .axis { fill: var(--muted); font-size: 9px; }
svg .st.green { fill: var(--good); } svg .st.yellow { fill: var(--appr); }
svg .st.red { fill: var(--bad); } svg .st.unknown { fill: var(--line); }
.trace li { margin: 4px 0; } .trace .via { color: var(--muted); font-size: 0.85rem; }
.missing li { margin: 2px 0; }
"""
SERIES_W, SERIES_H, PAD = 600.0, 140.0, 12.0


def entity_href(entity_id: str) -> str:
    return f"/world/{quote(entity_id, safe='')}"


def badge(name: str) -> str:
    slug = name.lower().replace(" ", "-")
    return f'<span class="badge kind-{escape(slug)}">{escape(name)}</span>'


def _value(value: object) -> str:
    if value is None:
        return '<span class="muted">not available</span>'
    if isinstance(value, float):
        return escape(f"{value:.6g}")
    return escape(str(value))


def _pages(here: str) -> str:
    links = [("/flow", "The flow"), *PAGES]
    return '<p class="pages">' + "".join(
        f'<a href="{url}">{"<strong>" if url == here else ""}{escape(text)}'
        f'{"</strong>" if url == here else ""}</a>'
        for url, text in links
    ) + "</p>"


def _section(name: str, title: str, body: list[str]) -> str:
    return f'<section id="{name}"><h2>{escape(title)}</h2>' + "\n".join(body) + "</section>"


def _dl(rows: list[tuple[str, str]]) -> str:
    return '<dl class="panel">' + "".join(f"<dt>{escape(a)}</dt><dd>{b}</dd>" for a, b in rows) + (
        "</dl>"
    )


def _page_of(title: str, body: list[str]) -> str:
    return _page(title, "\n".join(body), synthetic=False, extra_css=FLOW_CSS + WORLD_CSS)


def render_snapshot_missing(title: str) -> str:
    """The whole page a fresh clone sees without the Git-ignored snapshot: one line."""
    return _page(title, f"<p>{escape(SNAPSHOT_MISSING)}</p>", synthetic=False)


def render_world_problem(title: str, problem: str) -> str:
    return _page_of(title, [
        "<h1>World Model inspector</h1>",
        f'<div class="abstain"><strong>The snapshot cannot be shown.</strong> '
        f"{escape(problem)}</div>",
    ])  # fmt: skip


def _source_rows(view: WorldView, inspector: Inspector) -> list[tuple[str, str]]:
    return [
        ("Source model hash", f"<code>{escape(view.world_model_sha256)}</code> (TrafficWorldModel, "
         "sha256 of its canonical JSON; the world model the baseline replays)"),
        ("Projection", f"WorldView, projection version {escape(view.view_version)}, view "
         f"<code>{escape(view.view_sha256)}</code>"),
        ("Geometry", f"{escape(view.geometry_id)} <code>{escape(view.geometry_sha256)}</code>"),
        ("Evidence bundle", escape(view.bundle_id or "none given")),
        ("Read from", escape(inspector.origin)),
        ("World model file",
         escape(inspector.world_model_file or "not present here (Git-ignored)")),
        ("NGSIM file", escape(inspector.dataset_file or "not present here (Git-ignored)")),
    ]  # fmt: skip


def _camera(camera: CameraRecord) -> str:
    if camera.layer is None:
        body = [f"<p>{badge('camera probe')} The camera layer record {escape(camera.path)} "
                f"{escape(camera.problem or '')}</p>"]  # fmt: skip
        return _section("camera", "Camera layer", body)
    layer = camera.layer
    if layer.calibration_accepted:
        verdict = "<p>Its calibration is recorded as accepted: the chain links it.</p>"
    else:
        verdict = (
            '<div class="abstain"><p><strong>Typed break</strong> <code>not_accepted</code> · '
            "<code>world_entity &lt;- observation</code> · camera layer: "
            f"<strong>{escape(CAMERA_BREAK)}</strong>.</p><p>The camera tracks file "
            f"<code>{escape(layer.tracks_sha256)}</code> is stated by the record, and the chain "
            f"refuses the link ({escape(CAMERA_NOT_ACCEPTED)}): no camera track feeds a World "
            "Model entity, the simulation or the decision.</p></div>"
        )
    body = [
        f"<p>{badge('camera probe')} {escape(layer.label)}: {len(layer.tracks)} tracks from "
        f"{escape(layer.sam_model)} on clip {escape(layer.camera_clip_id)}, "
        f"{len(layer.sam_request_keys)} SAM requests.</p>",
        verdict,
        f'<p class="muted">Record {escape(camera.path)} (sha256 '
        f"<code>{escape(camera.sha256 or '')}</code>), calibration_accepted = "
        f"{escape(str(layer.calibration_accepted).lower())}.</p>",
    ]
    return _section("camera", "Camera layer", body)


def render_world_index(
    demo: Demo, inspector: Inspector, camera: CameraRecord
) -> str:  # fmt: skip
    view = inspector.view
    assert view is not None and inspector.geometry is not None
    legend = "".join(
        f"<li>{badge(name)} {escape(BADGE_MEANING[name])}</li>" for name in BADGES
    )
    groups = []
    for kind in ("intersection", "approach", "lane", "movement", "signal_head", "queue",
                 "conflict_zone", "signal_program", "camera_layer", "camera_track"):  # fmt: skip
        found = view.of_type(kind)  # type: ignore[arg-type]
        if not found:
            continue
        links = " ".join(
            f'<a href="{escape(entity_href(e.entity_id))}">{escape(e.entity_id.split(":", 1)[1])}'
            "</a>"
            for e in found
        )
        kinds = sorted({entity_badge(e) for e in found})
        groups.append(
            f"<li><strong>{escape(kind.replace('_', ' '))} ({len(found)})</strong> "
            f"{' '.join(badge(k) for k in kinds)}<br>{links}</li>"
        )
    by_id = {e.entity_id: e for e in view.entities}
    relations = "".join(
        f'<tr><td><a href="{escape(entity_href(r.source_id))}">{escape(by_id[r.source_id].label)}'
        f"</a></td><td><code>{escape(r.relation)}</code></td>"
        f'<td><a href="{escape(entity_href(r.target_id))}">{escape(by_id[r.target_id].label)}</a>'
        f"</td><td>{escape(r.evidence_kind)}</td><td>{escape(r.basis)}</td></tr>"
        for r in view.relations
    )
    absent = "".join(
        f"<li><strong>{escape(a.layer)}:</strong> {escape(a.reason)}</li>" for a in view.absent
    )
    body = [
        f"<h1>World Model inspector: {escape(demo.manifest.title)}</h1>",
        f'<p class="say">{escape(OFFLINE)}</p>',
        _pages("/world"),
        _section("snapshot", "The snapshot", [_dl(_source_rows(view, inspector))]),
        _section("legend", "What each badge means", [f"<ul>{legend}</ul>"]),
        _section("map", "The junction", [
            _svg(view, inspector.geometry, None, DEFAULT_LAYERS, "core", href=entity_href),
            _legend(),
        ]),  # fmt: skip
        _section("entities", f"All {len(view.entities)} entities, one page each", [
            f'<ul class="groups">{"".join(groups)}</ul>'
        ]),  # fmt: skip
        _section("relations", f"Relations ({len(view.relations)})", [
            "<table><thead><tr><th>From</th><th>Relation</th><th>To</th><th>Certainty</th>"
            f"<th>Basis</th></tr></thead><tbody>{relations}</tbody></table>"
        ]),  # fmt: skip
        _camera(camera),
        _section("absent", "What the snapshot says it does not have", [
            f"<ul>{absent}</ul>",
            f'<p class="muted">{len(view.ambiguities)} tracks crossed only one gate of a movement '
            f"and are listed, never counted; {len(view.unmapped_evidence_ids)} of "
            f"{len(view.evidence)} evidence items describe no single entity.</p>",
        ]),  # fmt: skip
    ]
    return _page_of(f"CairoFlow World Model {view.view_sha256[:12]}", body)


# --- one entity --------------------------------------------------------------------------------


def _root(view: WorldView) -> WorldEntity:
    return view.of_type("intersection")[0]


def _number(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return f"{value:g}"


def coverage_of(view: WorldView, entity: WorldEntity) -> str | None:
    """What part of the observed window the entity's values cover, from recorded values only."""
    root = _root(view).attributes
    window = _number(root.get("window_s"))
    a = entity.attributes
    if window is None:
        return None
    if entity.entity_type == "intersection":
        return f"the whole {window} s window of clip {root.get('clip_id')}"
    if entity.entity_type == "movement" and _number(a.get("count_veh")) is not None:
        return f"{a['count_veh']} stop-line to exit-line crossings in the {window} s window"
    if entity.entity_type == "queue" and _number(a.get("samples")) is not None:
        # two populations: one sample a second, and one at each end of red; the snapshot's
        # Confidence note counts the censored ones of both together
        samples, censored = int(a["samples"]), int(a.get("censored_samples") or 0)
        reds = int(a.get("end_of_red_samples") or 0)
        reds_cut = int(a.get("end_of_red_censored") or 0)
        text = f"{samples} per-second samples in the {window} s window"
        if censored:
            text += f", {censored} of them reach the lane's far end (censored)"
        if reds:
            text += f"; {reds} end-of-red samples, {reds_cut} of them censored"
            if censored or reds_cut:
                text += (
                    f"; {censored + reds_cut} of all {samples + reds} samples (both kinds) "
                    "reach the lane's far end"
                )
        return text
    if entity.entity_type == "signal_head":
        states = [(s, _number(a.get(f"{s}_s"))) for s in ("green", "yellow", "red", "unknown")]
        if all(v is not None for _, v in states):
            return ", ".join(f"{s} {v} s" for s, v in states) + f" of the {window} s window"
    return None


def _confidence(entity: WorldEntity) -> str | None:
    u = entity.uncertainty
    if u is None:
        return None
    bits = []
    if u.interval95 is not None:
        bits.append(f"95% interval [{u.interval95[0]:g}, {u.interval95[1]:g}] {u.unit or ''}")
    if u.lower_bound:
        bits.append("a lower bound (censored): the true value is at least this")
    if u.note:
        bits.append(u.note)
    return "; ".join(b.strip() for b in bits) or None


def _relations(view: WorldView, entity: WorldEntity) -> str:
    by_id = {e.entity_id: e for e in view.entities}
    items = []
    for r in view.relations:
        if entity.entity_id not in (r.source_id, r.target_id):
            continue
        outgoing = r.source_id == entity.entity_id
        other = r.target_id if outgoing else r.source_id
        words = r.relation if outgoing else f"{INCOMING[r.relation]} ({r.relation})"
        items.append(
            f"<li>{escape(words)} <a href=\"{escape(entity_href(other))}\">"
            f"{escape(by_id[other].label)}</a> <span class=\"muted\">({escape(r.evidence_kind)}: "
            f"{escape(r.basis)})</span></li>"
        )
    return f"<ul>{''.join(items)}</ul>" if items else '<p class="muted">None.</p>'


def _evidence(view: WorldView, demo: Demo, entity: WorldEntity) -> tuple[str, int]:
    texts = _evidence_texts(demo)
    refs = [ref for ref in view.evidence if entity.entity_id in ref.entity_ids]
    items = []
    for ref in refs:
        cited = _citations(demo, ref.evidence_id)
        cites = (
            f'<br><span class="muted">cited by the model: {escape(", ".join(cited))}</span> '
            f"{badge('AI-generated')}"
            if cited
            else ""
        )
        items.append(
            f'<div class="ev"><code>{escape(ref.evidence_id)}</code> ({escape(ref.kind)})<br>'
            f"{escape(texts.get(ref.evidence_id, 'no summary in the bundle'))}{cites}</div>"
        )
    return "".join(items), len(refs)


def _trace(chain: DecisionChain) -> str:
    """The chain walk: each record (badge, type, id, hash), each link between, every break."""
    items = []
    for i, record in enumerate(chain.path):
        if i:
            link = chain.path_links[i - 1]
            items.append(
                f'<li class="via">{escape(chain.path[i - 1].stage)} &lt;- {escape(record.stage)}'
                f" · basis: {escape(link.basis)} ({escape(link.kind)})</li>"
            )
        items.append(
            f"<li>{badge(BADGE_OF_CATEGORY[record.category])} {escape(record.record_type)} "
            f"{escape(record.record_id)} <code>{escape(record.content_sha256)}</code> "
            f'<span class="muted">({escape(record.hash_basis)}; {escape(record.stage)})</span></li>'
        )
    head = (
        f"<p>Reaches a source record: <strong>{'yes' if chain.reaches_source else 'no'}</strong>"
        + (f", the content hash <code>{escape(chain.source_sha256)}</code>"
           if chain.source_sha256 else "")
        + f'. <span class="muted">Chain graph <code>{escape(chain.graph_sha256)}</code>.</span></p>'
    )  # fmt: skip
    path = f'<ol class="trace">{"".join(items)}</ol>' if items else ""
    breaks = "".join(
        f"<li><code>{escape(b.code)}</code> · <code>{escape(b.link)}</code> · at "
        f"{escape(b.at)}: {escape(b.reason)}</li>"
        for b in chain.breaks
    )
    tail = f"<h3>Typed breaks ({len(chain.breaks)})</h3><ul>{breaks}</ul>" if breaks else ""
    return head + path + tail


def _series_svg(
    points: list[tuple[float, float, bool]], window: float
) -> tuple[str, float, bool]:  # fmt: skip
    """The series, its longest value, and whether that value is itself censored (a lower bound:
    the queue reached the end of the observed lane then)."""
    top = max((length for _, length, _ in points), default=0.0)
    cut = any(censored for _, length, censored in points if length == top)
    scale = top if top > 0 else 1.0
    inner_w, inner_h = SERIES_W - 2 * PAD, SERIES_H - 2 * PAD

    def x(t: float) -> float:
        return PAD + inner_w * t / window

    def y(length: float) -> float:
        return SERIES_H - PAD - inner_h * length / scale

    line = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v, _ in points)
    ticks = "".join(
        f'<line class="cens" x1="{x(t):.1f}" y1="{PAD - 6:.1f}" x2="{x(t):.1f}" '
        f'y2="{PAD - 1:.1f}"/>'
        for t, _, censored in points
        if censored
    )
    axis = (
        f'<text class="axis" x="{PAD}" y="{SERIES_H - 1}">0 s</text>'
        f'<text class="axis" x="{SERIES_W - PAD}" y="{SERIES_H - 1}" text-anchor="end">'
        f"{window:g} s</text>"
        f'<text class="axis" x="{PAD + 2}" y="{PAD + 8}">{"&#8805; " if cut else ""}'
        f"{top:.1f} m</text>"
    )
    svg = (
        f'<svg class="series" viewBox="0 0 {SERIES_W:g} {SERIES_H:g}" role="img" '
        f'aria-label="queue length over the window">{axis}{ticks}'
        f'<polyline class="q" points="{line}"/></svg>'
    )
    return svg, top, cut


def _series(inspector: Inspector, view: WorldView, entity: WorldEntity) -> tuple[str, list[str]]:
    """The queue series or the signal timeline from the world model, or what is missing."""
    model = inspector.world_model
    window = _number(_root(view).attributes.get("window_s"))
    if entity.entity_type == "queue":
        name = "queue series"
    elif entity.entity_type == "signal_head":
        name = "signal timeline"
    else:
        return "", []
    if model is None or window is None:
        return "", [
            f"{name}: not available (it is read from the world model, world_model.json with "
            f"SHA-256 {view.world_model_sha256}, which is not here)"
        ]
    total = float(window)
    if entity.entity_type == "queue":
        lane = entity.entity_id.split(":", 1)[1]
        samples = queue_series(model, lane)
        if not samples:
            return "", [f"{name}: the world model holds no queue sample for lane {lane}"]
        points = [(q.frame.timestamp_s, q.length_m, q.censored) for q in samples]
        svg, top, cut = _series_svg(points, total)
        censored = sum(1 for _, _, c in points if c)
        longest = (
            f"the longest is at least {top:.1f} m (that sample reached the lane's far end)"
            if cut
            else f"the longest is {top:.1f} m"
        )
        body = [
            f"<p>{badge('dataset-derived')} Queue length on lane {escape(lane)}: {len(points)} "
            f"per-second samples from the world model's queue samples; {longest}. "
            f"{censored} samples (red ticks) reached the lane's far end, so the queue then was at "
            "least that long: the data stop there, the queue may not.</p>",
            svg,
        ]
        return _section("series", "Queue series", body), []
    arm, head = str(entity.attributes.get("arm")), str(entity.attributes.get("head"))
    intervals = signal_intervals(model, arm, head)
    if not intervals:
        return "", [f"{name}: the world model holds no interval for head {arm} {head}"]
    inner_w = SERIES_W - 2 * PAD
    rects = "".join(
        f'<rect class="st {escape(i.state)}" x="{PAD + inner_w * i.start_s / total:.2f}" y="8" '
        f'width="{inner_w * (i.end_s - i.start_s) / total:.2f}" height="24">'
        f"<title>{escape(i.state)} {i.start_s:g}-{i.end_s:g} s</title></rect>"
        for i in intervals
    )
    svg = (
        f'<svg class="signal" viewBox="0 0 {SERIES_W:g} 48" role="img" '
        f'aria-label="reconstructed signal states over the window">{rects}'
        f'<text class="axis" x="{PAD}" y="46">0 s</text>'
        f'<text class="axis" x="{SERIES_W - PAD}" y="46" text-anchor="end">{total:g} s</text>'
        "</svg>"
    )
    seconds = ", ".join(
        f"{state} {entity.attributes.get(f'{state}_s'):g} s"
        for state in ("green", "yellow", "red", "unknown")
        if isinstance(entity.attributes.get(f"{state}_s"), int | float)
    )
    timeline = model.signal_timeline
    body = [
        f"<p>{badge('World Model')} Signal head {escape(arm)} {escape(head)}, "
        f"<strong>reconstructed</strong> from when vehicles crossed the stop line: there is no "
        f"signal log, and no signal head was read (ADR-023). {len(intervals)} intervals: "
        f"{escape(seconds)}.</p>",
        svg,
        f'<p class="muted">Timeline {escape(timeline.timeline_id if timeline else "")} '
        f"({escape(timeline.label if timeline else '')}): "
        f"{escape(timeline.source if timeline else '')}</p>",
    ]
    return _section("series", "Signal timeline (reconstructed)", body), []


def source_hash(view: WorldView, entity: WorldEntity) -> tuple[str, str] | None:
    """The SHA-256 of the record the entity's SourceRef names, as the snapshot states it, and
    what that record is; None when the snapshot states no hash for it."""
    model = entity.source.model
    if model == "IntersectionGeometry":
        return view.geometry_sha256, f"the IntersectionGeometry {view.geometry_id}"
    if model == "TrafficWorldModel":
        return view.world_model_sha256, "the TrafficWorldModel"
    if model == "SignalTimeline":
        return view.world_model_sha256, "the TrafficWorldModel that holds the signal timeline"
    return None


def render_world_entity(
    demo: Demo, inspector: Inspector, entity: WorldEntity
) -> str:  # fmt: skip
    view = inspector.view
    assert view is not None and inspector.geometry is not None
    kind = entity_badge(entity)
    missing: list[str] = []
    hashed = source_hash(view, entity)
    if hashed is None:
        source_sha = '<span class="muted">not available</span>'
        missing.append(f"source hash: the snapshot states none for a {entity.source.model}")
    else:
        source_sha = f"<code>{escape(hashed[0])}</code> ({escape(hashed[1])})"
    rows = [
        ("Entity", f"<code>{escape(entity.entity_id)}</code> ({escape(entity.entity_type)})"),
        ("Kind", badge(kind)),
        ("Evidence kind", f"{escape(entity.evidence_kind)} (as the snapshot records it)"),
        ("Status", escape(entity.status)),
        ("Source", f"{escape(entity.source.model)} {escape(entity.source.path)}"),
        ("Source hash", source_sha),
        ("Projection", f"version {escape(view.view_version)}, view "
         f"<code>{escape(view.view_sha256)}</code> of the world model "
         f"<code>{escape(view.world_model_sha256)}</code> on the geometry "
         f"<code>{escape(view.geometry_sha256)}</code>"),
    ]  # fmt: skip
    coverage = coverage_of(view, entity)
    if coverage is not None:
        rows.append(("Coverage", escape(coverage)))
    else:
        missing.append(f"coverage: not recorded for a {entity.entity_type.replace('_', ' ')}")
    confidence = _confidence(entity)
    if confidence is not None:
        rows.append(("Confidence", escape(confidence)))
    else:
        missing.append("confidence: not recorded (no interval, bound or note in the snapshot)")
    for key, value in entity.attributes.items():
        rows.append((key, _value(value)))
        if value is None:
            missing.append(f"{key}: not recorded in the snapshot")
    evidence, n_evidence = _evidence(view, demo, entity)
    if not n_evidence:
        missing.append(
            f"evidence the model was given: none of the {len(view.evidence)} items maps to this "
            "entity"
        )
    series, series_missing = _series(inspector, view, entity)
    missing += series_missing
    chain = entity_trace(inspector, entity.entity_id)
    if chain is None:
        trace = (
            '<p class="muted">Not available here: the walk reads the world model '
            f"(world_model.json, SHA-256 <code>{escape(view.world_model_sha256)}</code>), which "
            "is Git-ignored and not present.</p>"
        )
        missing.append(
            f"evidence trace: not available (needs world_model.json with SHA-256 "
            f"{view.world_model_sha256})"
        )
    else:
        trace = _trace(chain)
        missing += [f"evidence trace: {b.code} at {b.link}: {b.reason}" for b in chain.breaks]
        missing += [f"evidence trace: matched by id only, no hash: {u}" for u in
                    chain.unhashed_links]  # fmt: skip
    missing += [f"{a.layer}: {a.reason} (the whole snapshot)" for a in view.absent]
    body = [
        f"<h1>{escape(entity.label)}</h1>",
        f'<p class="say">{escape(OFFLINE)}</p>',
        _pages(""),
        '<p><a href="/world">&#8592; all entities</a></p>',
        '<div class="inspector"><div class="fig-col">',
        _svg(view, inspector.geometry, entity.entity_id, DEFAULT_LAYERS + "c", "core",
             href=entity_href),  # fmt: skip
        '</div><div class="panel-col">',
        _section("entity", "What it is", [
            _dl(rows), _record_provenance(entity, inspector.geometry, demo),
        ]),  # fmt: skip
        "</div></div>",
        series,
        _section("trace", "Evidence trace (the decision chain, walked from this entity)", [trace]),
        _section("relations", "Relations", [_relations(view, entity)]),
        _section("evidence", f"Evidence the model was given about this ({n_evidence})", [
            evidence or '<p class="muted">None.</p>'
        ]),  # fmt: skip
        _section("missing", "Missing information", [
            '<ul class="missing">' + "".join(f"<li>{escape(m)}</li>" for m in missing) + "</ul>"
        ]),  # fmt: skip
    ]
    return _page_of(f"CairoFlow {entity.entity_id}", body)


def render_world_not_found() -> str:
    return _page_of("CairoFlow: no such entity", [
        "<h1>No such entity</h1>",
        '<p>This snapshot has no entity by that id. <a href="/world">All entities</a>.</p>',
    ])  # fmt: skip


# --- the problem -------------------------------------------------------------------------------


def _quantity(value: float | None, unit: str, lower: bool) -> str:
    if value is None:
        return '<span class="muted">not available</span>'
    return f"{'&#8805; ' if lower else ''}{value:.4g} {escape(unit)}"


def _attached(h: AttachedHypothesis, detail: AttachedDetail | None) -> str:
    """One attached hypothesis, as /demo and /flow show one: boxed and labelled AI-generated,
    with what it cites, assumes and offers instead as its stored reply holds it, and the citation
    check stored with that reply (named with its path). If the reply cannot be read back
    (hypotheses.attached_details), the statement alone, and why nothing more is shown."""
    if detail is not None and detail.hypothesis is not None:
        return _hypothesis(detail.hypothesis, None, stored=detail.stored)
    why = detail.refusal if detail is not None and detail.refusal else "its reply was not read"
    return (
        f'<div class="model"><span class="label">{badge("AI-generated")} '
        f"{escape(h.hypothesis_id)}: model-generated text, not evidence "
        f"({escape(h.source.path)})</span>{escape(h.statement)}</div>"
        f'<p class="muted">Details not shown: {escape(why)}.</p>'
    )


def render_problem(
    problem: ProblemStatement | None,
    error: str | None,
    details: dict[tuple[str, str], AttachedDetail] | None = None,
) -> str:
    """`details`: each attached hypothesis read back from its pinned reply, keyed by (source
    path, hypothesis id) (hypotheses.attached_details)."""
    if problem is None:
        return _page_of("CairoFlow problem unavailable", [
            "<h1>Problem record unavailable</h1>",
            f'<div class="abstain">The records do not give a problem statement: '
            f"<code>{escape(error or '')}</code></div>",
        ])  # fmt: skip
    observed = "".join(
        f"<tr><td><code>{escape(q.key)}</code><br>{escape(q.quantity)}</td>"
        f"<td>{_quantity(q.value, q.unit, q.lower_bound)}"
        + (f" [{q.interval95[0]:.4g}, {q.interval95[1]:.4g}]" if q.interval95 else "")
        + f" {badge('dataset-derived')}</td>"
        f"<td>{_quantity(q.simulated_value, q.unit, q.simulated_lower_bound)} "
        f"{badge('simulated')}</td>"
        f"<td>{escape({True: 'inside', False: 'outside', None: 'not stated'}[q.simulated_inside_observed_interval])}</td>"  # noqa: E501
        f"<td>{escape(q.comparability)}</td><td>{escape(q.member)}</td>"
        f"<td>{escape('; '.join(q.limitations)) or '-'}</td></tr>"
        for q in problem.observed
    )
    members = "".join(
        f"<tr><td>{escape(m.member)}{' (required)' if m.required else ''}<br>"
        f"<code>{escape(m.timeline_id)}</code></td>"
        f"<td>{m.baseline_mean:.4g} {escape(m.unit)} {badge('simulated')}<br>"
        f"<span class=\"muted\">{escape(m.metric_name)}, {escape(m.better)} is better</span></td>"
        f"<td>{escape(m.unit_of_inference)}<br><span class=\"muted\">{escape(m.unit_basis)}</span>"
        f"</td><td>{escape(ASSESSMENT_WORDS[m.assessment])}</td>"
        "<td>" + ("<br>".join(
            f"{escape(i.candidate)}: {i.mean_difference:+.4g} {escape(m.unit)} "
            f"[{i.ci95_difference[0]:+.4g}, {i.ci95_difference[1]:+.4g}], p {i.p_value:.3g} "
            f"&#8804; Holm {i.holm_threshold:.3g} {badge(i.label)}"
            for i in m.improvements
        ) or "none") + "</td></tr>"
        for m in problem.members
    )  # fmt: skip
    if problem.hypotheses:
        found = details or {}
        hypotheses = "".join(
            _attached(h, found.get((h.source.path, h.hypothesis_id))) for h in problem.hypotheses
        )
    else:
        hypotheses = f'<p class="muted">{escape(problem.hypotheses_note or "")}</p>'
    sources = "".join(
        f"<tr><td>{escape(s.path)}</td><td><code>{escape(s.sha256)}</code></td></tr>"
        for s in problem.sources
    )
    entities = " ".join(
        f'<a href="{escape(entity_href(e))}">{escape(e)}</a>' for e in problem.entity_ids
    )
    body = [
        f"<h1>Problem: {escape(problem.problem_id)}</h1>",
        f'<p class="say">{escape(OFFLINE)}</p>',
        _pages("/problem"),
        _section("statement", "The statement", [
            f'<div class="decision"><p><strong>{escape(problem.statement)}</strong></p></div>',
            f'<p class="muted">Written by code from the values below, each copied from a record '
            f"listed with its SHA-256; nothing is estimated here. About {entities}. Record "
            f"<code>{escape(problem.problem_sha256)}</code>, version "
            f"{escape(problem.problem_version)}.</p>",
        ]),  # fmt: skip
        _section("observed", "Observed on the approach (NGSIM-derived), beside the baseline", [
            "<table><thead><tr><th>Quantity</th><th>Observed [95%]</th><th>Baseline, same "
            "definition</th><th>Simulated inside?</th><th>Comparable?</th><th>Member</th>"
            f"<th>Caveats</th></tr></thead><tbody>{observed}</tbody></table>",
        ]),  # fmt: skip
        _section("members", "The decision metric in each timeline member's baseline", [
            "<table><thead><tr><th>Member</th><th>Baseline</th><th>Unit of inference</th>"
            "<th>Assessment</th><th>Holm-confirmed improvements, in simulation</th></tr></thead>"
            f"<tbody>{members}</tbody></table>",
        ]),  # fmt: skip
        _section("conditions", "Failure conditions", [
            "<ul>" + "".join(f"<li>{escape(c)}</li>" for c in problem.failure_conditions)
            + "</ul>",
        ]),  # fmt: skip
        _section("hypotheses", "What the model suggested (attached, never evidence)", [hypotheses]),
        _section("sources", f"Records read ({len(problem.sources)})", [
            "<table><thead><tr><th>Path</th><th>SHA-256</th></tr></thead>"
            f"<tbody>{sources}</tbody></table>",
        ]),  # fmt: skip
    ]
    return _page_of(f"CairoFlow problem {problem.problem_id}", body)


# --- the decision trace ------------------------------------------------------------------------


def render_decision_trace(trace: DecisionTrace) -> str:
    report = trace.report
    selected = report.get("selected_intervention_id") or "none"
    recommended = report.get("recommended_intervention_id") or "none"
    assessment = report.get("baseline_assessment")
    assessed = (
        f"<p>Baseline assessment, as recorded: <strong>{escape(str(assessment.get('status')))}"
        f"</strong> ({escape(str(assessment.get('path')))}, SHA-256 "
        f"<code>{escape(str(assessment.get('file_sha256')))}</code>).</p>"
        if isinstance(assessment, dict)
        else ""
    )
    decision = [
        f'<div class="abstain"><p><strong>Selected: {escape(selected)}. Recommended: '
        f"{escape(recommended)}.</strong></p><p>{escape(report.get('selection_reason', ''))}</p>"
        f"<p>{escape(report.get('recommendation', ''))}</p>{assessed}</div>",
        f'<p class="muted">As recorded in {escape(trace.record)} (pinned by the demo manifest); '
        f"its report.json: <code>{escape(str(report.get('report_json_sha256')))}</code>.</p>",
    ]
    if trace.chain is None:
        chain = ['<p class="muted">The chain walk is not available here.</p>']
    else:
        c = trace.chain
        chain = [
            f"<p>Walked from <code>{escape(c.start)}</code>. Full seven-stage chain: "
            f"<strong>{'yes' if c.full_chain else 'no'}</strong>.</p>",
            _trace(c),
        ]
    if trace.pairs is None:
        pairs = ['<p class="muted">The pairs are not available here.</p>']
    else:
        p = trace.pairs
        seeds = "".join(
            f"<tr><td>seed {s.seed}</td><td><code>{escape(s.demand_sha256)}</code></td>"
            f"<td><code>{escape(s.population_sha256 or 'not read')}</code></td></tr>"
            for s in p.seeds
        )
        pair_rows = "".join(
            f"<tr><td>{escape(x.reference.arm)} vs {escape(x.compared.arm)}</td>"
            f"<td>seed {x.reference.seed}</td><td><code>{escape(x.pair_sha256)}</code></td></tr>"
            for x in p.pairs
        )
        pairs = [
            "<p>This is a <strong>consistency check only</strong>, and a narrow one. The pairs "
            "are rebuilt from the run's recorded files, which also re-hashes each seed's demand "
            "file against the hash the baseline manifest records. Against the committed record, "
            "only its baseline manifest hash, baseline directory and seed list are checked. The "
            "pair and demand hashes below are computed from the run's files and are not compared "
            "with any hash the record cites. No metric is recomputed on this page.</p>",
            f"<p>Unit of inference: <strong>{escape(p.unit_of_inference)}</strong>. Baseline "
            f"manifest (member) <code>{escape(p.member_sha256)}</code>, timeline "
            f"{escape(p.timeline_id)}, world model <code>{escape(p.world_model_sha256)}</code>, "
            f"network <code>{escape(p.network_sha256)}</code>, SUMO binary "
            f"{escape(p.sumo_binary_version or 'not named')}, horizon {p.horizon_s:g} s. Pair view "
            f"<code>{escape(p.view_sha256)}</code>.</p>",
            f"<h3>Seeds ({len(p.seeds)}): demand and vehicle population</h3>",
            "<table><thead><tr><th>Seed</th><th>Demand file SHA-256</th><th>Population "
            f"SHA-256</th></tr></thead><tbody>{seeds}</tbody></table>",
            f"<h3>Pairs ({len(p.pairs)})</h3>",
            "<table><thead><tr><th>Arms</th><th>Seed</th><th>Pair SHA-256</th></tr></thead>"
            f"<tbody>{pair_rows}</tbody></table>",
        ]
    problems = "".join(f"<li>{escape(x)}</li>" for x in trace.problems)
    body = [
        "<h1>Decision trace</h1>",
        f'<p class="say">{escape(OFFLINE)}</p>',
        _pages("/decision/trace"),
        _section("decision", "The decision, as recorded", decision),
        *([_section("unavailable", "Not here", [f"<ul>{problems}</ul>"])] if problems else []),
        _section("chain", "From the decision back to its source", chain),
        _section("pairs", "The paired runs it rests on", pairs),
    ]
    return _page_of("CairoFlow decision trace", body)
