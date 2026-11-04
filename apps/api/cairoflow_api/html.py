"""Server-rendered HTML for a pipeline run: the decision report with provenance on every number.

The page shows the same content as report.md, built from report.json so every number can carry
visible labels: its evidence kind (observed, approximated, assumed, simulated), its source
(metric, comparison, world model, validated input, setting) and its references. Model text is
boxed as model-generated and never shown as evidence. Every string is HTML-escaped; the page
has no scripts.
"""

from __future__ import annotations

from html import escape

from cairoflow_api.demo import (
    DECISION_METRICS,
    ENSEMBLE,
    FOOTAGE_NOTE,
    SAM_NOTICE,
    SAM_OVERLAY,
    SAM_RECORD,
    SAM_REVIEW,
    Demo,
    Difference,
    baseline_ranges,
    differences,
    paired_lags,
    validation_rows,
)
from cairoflow_api.hypotheses import StoredCitations, stored_citations_of_demo
from cairoflow_contracts.reasoning import Hypothesis, HypothesisGrounding
from cairoflow_contracts.report import DecisionReport, ReportNumber
from cairoflow_evaluation.pipeline import PipelineRecord
from cairoflow_evaluation.report import (
    RECOMMENDATION_RULE,
    SELECTION_RULE,
    SIMULATION_TEXT,
    confirmation,
)

KIND_TEXT = {
    "observed": "observed",
    "approximated": "approximated (our procedure)",
    "assumed": "assumed",
    "simulated": "simulated (SUMO)",
}
SOURCE_TEXT = {
    "metric": "SUMO metric",
    "comparison": "paired comparison of SUMO metrics",
    "world_model": "world model",
    "validated_input": "validated input",
    "settings": "experiment setting",
}
STYLE = """
:root { color-scheme: light dark; --fg: #1d1f23; --bg: #ffffff; --muted: #5d6470;
  --line: #d9dde3; --panel: #f4f6f8; --sim: #1f5fbf; --appr: #9a5b00; --assm: #5d6470;
  --obs: #1d7a3a; --model: #7a3db8; --bad: #b3261e; --good: #1d7a3a; }
@media (prefers-color-scheme: dark) { :root { --fg: #e6e8eb; --bg: #15171a; --muted: #a1a8b3;
  --line: #2e333a; --panel: #1d2025; --sim: #7fb0ff; --appr: #f0b45a; --assm: #a1a8b3;
  --obs: #6fd18f; --model: #c9a2f2; --bad: #ff8a80; --good: #6fd18f; } }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.5rem; margin: 0.5rem 0; } h2 { font-size: 1.15rem; margin-top: 2rem; }
.banner { padding: 8px 16px; font-weight: 600; border-bottom: 1px solid var(--line);
  background: var(--panel); }
.banner .synthetic { color: var(--bad); margin-left: 1rem; }
table { border-collapse: collapse; width: 100%; margin: 0.5rem 0 1rem; font-size: 0.9rem; }
th, td { border-bottom: 1px solid var(--line); padding: 6px 8px; text-align: left;
  vertical-align: top; }
td.value { white-space: nowrap; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; border: 1px solid currentColor; border-radius: 4px;
  padding: 0 6px; font-size: 0.78rem; white-space: nowrap; }
.kind-simulated { color: var(--sim); } .kind-approximated { color: var(--appr); }
.kind-assumed { color: var(--assm); } .kind-observed { color: var(--obs); }
.source { color: var(--muted); font-size: 0.8rem; }
details summary { cursor: pointer; color: var(--muted); font-size: 0.8rem; }
details ul { margin: 4px 0; padding-left: 18px; font-family: ui-monospace, monospace;
  font-size: 0.75rem; overflow-wrap: anywhere; }
.model { border-left: 4px solid var(--model); background: var(--panel); padding: 8px 12px;
  margin: 0.5rem 0; }
.model .label { color: var(--model); font-weight: 600; font-size: 0.8rem; display: block; }
.verdict-improves { color: var(--good); font-weight: 600; }
.verdict-worsens { color: var(--bad); font-weight: 600; }
.verdict-inconclusive { color: var(--muted); }
.fig .verdict-improves, .fig .verdict-worsens, .fig .verdict-inconclusive { white-space: nowrap; }
video { width: 100%; max-height: 70vh; background: #000; }
.caption, .muted { color: var(--muted); font-size: 0.85rem; }
code { font-size: 0.8rem; overflow-wrap: anywhere; }
.table-wrap { overflow-x: auto; }
@media (max-width: 600px) { .badge { white-space: normal; } th, td { padding: 4px; }
  main { padding: 12px; } }
.kind-derived { color: var(--appr); } .kind-model { color: var(--model); }
.kind-checked { color: var(--muted); } .kind-reviewed { color: var(--obs); }
.badge.kind-ai-generated { color: var(--model); } .model .label .badge { margin-right: 4px; }
.stored { color: var(--muted); font-size: 0.85rem; margin: 0.25rem 0 0.75rem; }
.stored ul { margin: 2px 0; padding-left: 18px; font-family: ui-monospace, monospace;
  overflow-wrap: anywhere; }
.step { border-top: 1px solid var(--line); margin-top: 1.5rem; }
.step h2 { margin-top: 1rem; } .step .say { color: var(--muted); font-style: italic; }
.status { display: inline-block; padding: 2px 10px; border-radius: 4px; font-weight: 600;
  background: var(--panel); border: 1px solid var(--appr); color: var(--appr); }
.decision { background: var(--panel); padding: 8px 12px; border-left: 4px solid var(--bad); }
ol.toc { padding-left: 20px; } ol.toc li { margin: 2px 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 0.8rem; }
"""


def _page(title: str, body: str, *, synthetic: bool, extra_css: str = "") -> str:
    flag = '<span class="synthetic">SYNTHETIC DATA: not a real junction</span>' if synthetic else ""
    # Every table scrolls inside its own box on narrow screens; all text in `body` is escaped,
    # so these tags can only be the page's own.
    body = body.replace("<table>", '<div class="table-wrap"><table>').replace(
        "</table>", "</table></div>"
    )
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n<style>{STYLE}{extra_css}</style>\n</head>\n<body>\n"
        f'<div class="banner">CairoFlow · decision support only: nothing here controls real '
        f"traffic{flag}</div>\n<main>\n{body}\n</main>\n</body>\n</html>\n"
    )


def _number_row(n: ReportNumber) -> str:
    refs = "".join(f"<li>{escape(ref)}</li>" for ref in n.refs)
    return (
        f"<tr><td>{escape(n.label)}</td>"
        f'<td class="value">{escape(f"{n.value:.6g}")} {escape(n.unit)}</td>'
        f'<td><span class="badge kind-{escape(n.kind)}">{escape(KIND_TEXT.get(n.kind, n.kind))}'
        f'</span><br><span class="source">{escape(SOURCE_TEXT.get(n.source, n.source))}</span></td>'
        f"<td><details><summary>{escape(n.number_id)}: {len(n.refs)} reference"
        f"{'s' if len(n.refs) != 1 else ''}</summary><ul>{refs}</ul></details></td></tr>"
    )


def _numbers_table(numbers: list[ReportNumber]) -> str:
    if not numbers:
        return '<p class="muted">None.</p>'
    rows = "\n".join(_number_row(n) for n in numbers)
    return (
        "<table><thead><tr><th>What</th><th>Value</th><th>Provenance</th><th>Traced to</th>"
        f"</tr></thead><tbody>\n{rows}\n</tbody></table>"
    )


def _model_text(label: str, text: str) -> str:
    return (
        f'<div class="model"><span class="label">{escape(label)} · model-generated text, '
        f"not evidence</span>{escape(text)}</div>"
    )


AI_BADGE = '<span class="badge kind-ai-generated">AI-generated</span>'  # = world_html's badge


def _hypothesis_items(h: Hypothesis) -> str:
    """What a hypothesis cites, assumes and offers instead, as the model stated it."""
    fields = [
        ("Supporting evidence cited", ", ".join(h.supporting_evidence_ids) or "none"),
        ("Contradicting evidence cited", ", ".join(h.contradicting_evidence_ids) or "none"),
        ("Assumptions (model-stated)", "; ".join(h.assumptions) or "none"),
        ("Alternative explanations (model-stated)", "; ".join(h.alternative_explanations)
         or "none"),
        ("Limiting factor", h.limiting_factor or ""),
        ("Claim type", h.claim_type or ""),
        ("Restated evidence", "; ".join(f"{o.evidence_id}: {o.statement}"
                                        for o in h.observations)),
        ("Inference (model-stated)", h.inference or ""),
        ("Missing evidence (model-stated)", "; ".join(h.missing_evidence)),
    ]  # fmt: skip
    return "".join(f"<li><strong>{escape(label)}:</strong> {escape(text)}</li>"
                   for label, text in fields if text)  # fmt: skip


def _stored_where(stored: StoredCitations) -> str:
    return f"(<code>{escape(stored.key)}</code> in <code>{escape(stored.source)}</code>)"


def _stored_block(title: str, stored: StoredCitations | None, found: tuple[str, ...] | None,
                  nothing: str) -> str:  # fmt: skip
    """One stored-citation line: the entries as stored, "none ...", or why there is none to show.
    Code wrote the list when it parsed the reply; it is not model text and is not recomputed."""
    head = f"{escape(title)} {_badge('checked')}"
    if stored is None or found is None:
        why = stored.note if stored is not None and stored.note else None
        tail = f"not shown: {escape(why)}" if why else "not recorded for this reply"
        return f'<div class="stored">{head}: {tail}.</div>'
    if not found:
        return f'<div class="stored">{head}: {escape(nothing)} {_stored_where(stored)}.</div>'
    entries = "".join(f"<li>{escape(entry)}</li>" for entry in found)
    return f'<div class="stored">{head} {_stored_where(stored)}:<ul>{entries}</ul></div>'


def _stored_citations(stored: StoredCitations | None, hypothesis_id: str) -> str:
    """Below a hypothesis's box: what the citation check stored with its reply lists for it."""
    found = stored.of(hypothesis_id) if stored is not None else None
    return _stored_block("Stored citation check", stored, found, f"none for {hypothesis_id}")


def _stored_rest(stored: StoredCitations | None, hypothesis_ids: list[str]) -> str:
    """The same stored list's entries about the proposals (unknown evidence or hypotheses)."""
    found = stored.rest(hypothesis_ids) if stored is not None else None
    return _stored_block("Stored citation check of the proposals", stored, found, "none")


def _hypothesis(
    h: Hypothesis, checked: HypothesisGrounding | None, *, stored: StoredCitations | None
) -> str:
    """One hypothesis: the model's words boxed and labelled AI-generated, what it cites and
    assumes, code's evidence check of what it cites when one is given (cairoflow_reasoning.
    grounding: a supporting id, every id in the bundle, support not all assumed; recomputed by
    /demo and /flow, as report.json holds it on /runs), and the citation check stored with its
    reply."""
    check = ""
    if checked is not None:
        why = "; ".join(checked.reasons) or "every cited id is in the evidence"
        flags = "".join(f"<li>{escape(flag)}</li>" for flag in checked.flags)
        check = (
            f"<p>Evidence check {_badge('checked')}: <strong>{escape(checked.status)}</strong>, "
            f"{escape(why)}.</p>" + (f"<ul>{flags}</ul>" if flags else "")
        )
    label = f"{h.hypothesis_id}, {h.confidence_label} confidence"
    return (  # the model's words, and only they, inside the box; code's checks below it
        f'<div class="model"><span class="label">{AI_BADGE} {escape(label)} · model-generated '
        f"text, not evidence</span>{escape(h.statement)}<ul>{_hypothesis_items(h)}</ul></div>"
        f"{check}{_stored_citations(stored, h.hypothesis_id)}"
    )


def render_run_page(
    run_name: str,
    report: DecisionReport,
    record: PipelineRecord,
    *,
    overlay_url: str | None,
    stored: StoredCitations,
) -> str:
    """`stored`: the citation check stored with the run's reply, as its pipeline record pins it
    (hypotheses.stored_citations_of_run)."""
    numbers = {n.number_id: n for n in report.numbers}
    parts = [
        f"<h1>Decision report <code>{escape(report.report_id)}</code></h1>",
        f'<p class="muted">Run <code>{escape(run_name)}</code> · experiment '
        f"<code>{escape(report.experiment_id)}</code> · recorded in {escape(record.mode)} mode · "
        f'<a href="report.json">report.json</a> · <a href="report.md">report.md</a></p>',
        "<h2>Data source</h2>",
        f"<p>{escape(record.data_source)}</p>",
        *(f"<p>{escape(source)}</p>" for source in report.data_sources),
    ]
    if record.clip_attribution:
        parts.append(f'<p class="caption">Attribution: {escape(record.clip_attribution)}</p>')
    if record.notes:
        notes = "".join(f"<li>{escape(note)}</li>" for note in record.notes)
        parts.append(f'<ul class="muted">{notes}</ul>')
    parts.append("<h2>Review video</h2>")
    if overlay_url:
        parts += [
            f'<video controls preload="metadata" src="{escape(overlay_url)}"></video>',
            '<p class="caption">Boxes, track ids and contact points: SAM 3.1 output turned into '
            "tracks by our procedure. A review aid, not ground truth.</p>",
        ]
    else:
        parts.append('<p class="muted">No overlay video for this run (see the notes above).</p>')

    selected = report.selected_intervention_id or "none"
    assessment = report.baseline_assessment
    parts += [
        "<h2>Selection</h2>",
        f"<p><strong>Selected: {escape(selected)}.</strong> {escape(report.selection_reason)}</p>",
    ]
    if report.recommendation:  # reports before the recommendation rule have none
        recommended = report.recommended_intervention_id or "none"
        parts.append(
            f"<p><strong>Recommended: {escape(recommended)}.</strong> "
            f"{escape(report.recommendation)}</p>"
        )
    if assessment is not None:
        findings = "".join(f"<li>{escape(item)}</li>" for item in assessment.findings)
        parts += [
            f"<p><strong>Baseline: {escape(assessment.status.replace('_', ' '))}.</strong> "
            f"{escape(assessment.summary)}</p>",
            f"<ul>{findings}</ul>",
            f'<p class="muted">Assessed on {escape(assessment.assessed_on)}; see '
            f"{escape(assessment.documented_in)}.</p>",
        ]
    parts += [
        f'<p class="muted">Rule: {escape(report.selection_rule)}</p>',
        f'<p class="muted">Objective: {escape(report.objective)}</p>',
        "<p>Provenance labels: "
        + " ".join(
            f'<span class="badge kind-{k}">{escape(text)}</span>' for k, text in KIND_TEXT.items()
        )
        + ' <span class="badge" style="color: var(--model)">model-generated (text only)</span></p>',
        "<h2>Experiment settings</h2>",
        _numbers_table([n for n in report.numbers if n.source == "settings"]),
        "<h2>Observed demand</h2>",
        '<p class="muted">Counts and flows in the world model (from the data source above), by '
        "our procedure, with Poisson intervals.</p>",
        _numbers_table([n for n in report.numbers if n.source == "world_model"]),
        "<h2>Candidates</h2>",
    ]
    for c in report.candidates:
        parts.append(f"<h3>{escape(c.intervention_id)} ({escape(c.status)} by the validator)</h3>")
        if c.validator_reasons:
            parts.append(
                "<p>Rejected by the validator: " + escape("; ".join(c.validator_reasons)) + "</p>"
            )
        elif c.simulation_status in SIMULATION_TEXT:
            reasons = "".join(f"<li>{escape(reason)}</li>" for reason in c.simulation_reasons)
            parts.append(
                f"<p><strong>Never selectable:</strong> "
                f"{escape(SIMULATION_TEXT[c.simulation_status])}</p>"
                + (f"<ul>{reasons}</ul>" if reasons else "")
            )
        elif not c.verdicts:
            parts.append(
                '<p class="muted">Not compared in simulation: no verdicts. The reason is under '
                "Limitations.</p>"
            )
        if c.rationale:
            parts.append(_model_text("Rationale", c.rationale))
        if c.expected_tradeoffs:
            parts.append(_model_text("Stated trade-offs", "; ".join(c.expected_tradeoffs)))
        if c.unverified_assumptions:
            parts.append(_model_text("Preconditions: unverified assumptions",
                                     "; ".join(c.unverified_assumptions)))  # fmt: skip
        if c.grounding_reasons:
            parts.append("<p>Kept from planning by the evidence check: "
                         + escape("; ".join(c.grounding_reasons)) + "</p>")  # fmt: skip
        if c.verdicts:
            rows = "".join(
                f'<tr><td>{escape(name)}</td><td class="verdict-{escape(verdict)}">'
                f"{escape(verdict)}</td><td>{escape(confirmation(c, name))}</td></tr>"
                for name, verdict in c.verdicts.items()
            )
            parts.append(
                "<table><thead><tr><th>Metric (simulated)</th><th>Verdict (paired 95% interval, "
                "uncorrected)</th><th>Improvement confirmed (Holm)</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
            )
        parts.append(_numbers_table([numbers[i] for i in c.number_ids]))

    parts.append("<h2>Hypotheses</h2>")
    if not report.hypotheses:
        parts.append('<p class="muted">No hypotheses.</p>')
    checked = {g.hypothesis_id: g for g in report.grounding.hypotheses} if report.grounding else {}
    for h in report.hypotheses:
        parts.append(_hypothesis(h, checked.get(h.hypothesis_id), stored=stored))
    if report.hypotheses:
        parts.append(_stored_rest(stored, [h.hypothesis_id for h in report.hypotheses]))
    if report.grounding is not None:
        parts += [f'<p class="muted">{escape(note)}</p>' for note in report.grounding.notes]
    parts += ["<h2>Limitations</h2>", "<ul>"]
    parts += [f"<li>{escape(item)}</li>" for item in report.limitations]
    parts += ["</ul>", "<h2>Reproducibility</h2>", "<table><tbody>"]
    parts += [
        f"<tr><td>{escape(name)}</td><td><code>{escape(value)}</code></td></tr>"
        for name, value in report.reproducibility.items()
    ]
    parts.append("</tbody></table>")
    return _page(f"CairoFlow report {report.report_id}", "\n".join(parts),
                 synthetic=record.clip_synthetic)  # fmt: skip


def render_index(
    runs: list[tuple[str, PipelineRecord]],
    demo_url: str | None = None,
    flow_url: str | None = None,
) -> str:
    flow = (
        f'<p><a href="{escape(flow_url)}"><strong>The flow: from an intersection to a '
        "decision</strong></a> (inspect the World Model, the problem, the interventions and "
        "why nothing is recommended)</p>"
        '<p><a href="/world"><strong>The World Model inspector</strong></a> (one page per lane, '
        "movement, queue and signal head, each traced to its evidence)</p>"
        if flow_url
        else ""
    )
    demo = flow + (
        f'<p><a href="{escape(demo_url)}"><strong>The demo: the intersection 2 story in seven '
        "steps</strong></a> (committed records only)</p>"
        if demo_url
        else ""
    )
    if not runs:
        body = f'<h1>CairoFlow runs</h1>{demo}<p class="muted">No runs found.</p>'
        return _page("CairoFlow runs", body, synthetic=False)
    rows = "".join(
        f'<tr><td><a href="/runs/{escape(name)}/">{escape(name)}</a></td>'
        f"<td>{escape(record.run_name)}</td><td>{escape(record.mode)}</td>"
        f"<td>{escape(record.selected_intervention_id or 'none')}</td>"
        f"<td>{'synthetic: ' if record.clip_synthetic else ''}{escape(record.data_source)}"
        "</td></tr>"
        for name, record in runs
    )
    body = (
        f"<h1>CairoFlow runs</h1>{demo}<table><thead><tr><th>Directory</th><th>Run</th>"
        "<th>Mode</th><th>Selected</th><th>Data</th></tr></thead><tbody>"
        f"{rows}</tbody></table>"
    )
    return _page("CairoFlow runs", body, synthetic=any(r.clip_synthetic for _, r in runs))


# --- The demo page ------------------------------------------------------------------------

DEMO_KINDS = {
    "observed": "observed (NGSIM positions)",
    "derived": "approximated (our fixed rules)",
    "assumed": "assumed",
    "simulated": "simulated (SUMO)",
    "model": "model-generated",
    "checked": "computed by code",
    "sam": "model output (SAM 3.1)",
    "reviewed": "checked by a person (by eye)",
}
METRIC_TEXT = {
    "mean_delay_s": "Mean delay",
    "throughput_veh_per_h": "Throughput",
    "mean_queue_m.N": "Mean queue N",
    "mean_queue_m.E": "Mean queue E",
    "mean_queue_m.S": "Mean queue S",
    "mean_queue_m.W": "Mean queue W",
}
UNIT_TEXT = {"s": "s", "veh/h": "veh/h", "veh_per_h": "veh/h", "m": "m"}
STEPS = (
    ("evidence", "What was observed"),
    ("signals", "How the signals were estimated"),
    ("baseline", "The baseline in SUMO, and how far to trust it"),
    ("proposals", "What the model proposed"),
    ("results", "What the simulation says"),
    ("decision", "The decision"),
    ("sensitivity", "How sure: the verdict depends on the signal timeline"),
)


def _badge(kind: str) -> str:
    return f'<span class="badge kind-{kind}">{escape(DEMO_KINDS[kind])}</span>'


def _fig(cells: list[str], *kinds: str) -> str:
    """One table row of figures; its last cell names every kind of figure in it."""
    tds = "".join(f"<td>{cell}</td>" for cell in cells)
    return f'<tr class="fig">{tds}<td>{" ".join(_badge(kind) for kind in kinds)}</td></tr>'


def _table(head: list[str], rows: list[str]) -> str:
    ths = "".join(f"<th>{escape(h)}</th>" for h in [*head, "Kind"])
    return f"<table><thead><tr>{ths}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _side(side: dict, unit: str) -> str:
    """An observed or simulated value of a validation comparison, with its interval."""
    scale, suffix = (100.0, "%") if unit == "share" else (1.0, f" {unit}")
    text = (
        "&#8805; " if side.get("lower_bound") else ""
    ) + f"{side['value'] * scale:.1f}{escape(suffix)}"
    if side.get("interval"):
        low, high = side["interval"]
        text += f" [{low * scale:.1f}, {high * scale:.1f}]"
    return text


def _difference(d: Difference | None) -> str:
    if d is None:
        return '<span class="muted">not compared</span>'
    unit = escape(UNIT_TEXT.get(d.unit, d.unit))
    low, high = d.interval
    holm = " · Holm ✓" if d.confirmed else ""
    return (
        f"{d.value:+.2f} {unit} [{low:+.2f}, {high:+.2f}]<br>"
        f'<span class="verdict-{escape(d.verdict)}">{escape(d.verdict)}{holm}</span>'
    )


def _step(number: int, say: str, body: list[str]) -> str:
    anchor, title = STEPS[number - 1]
    return (
        f'<section class="step" id="{anchor}"><h2>{number} · {escape(title)}</h2>'
        f'<p class="say">{escape(say)}</p>' + "\n".join(body) + "</section>"
    )


def _sam_review(review: dict) -> list[str]:
    """The A3 hand check of step 0's boxes: one person's marks, scored by fixed rules."""
    s = review["scores"]
    p, r, ids = s["precision"], s["recall"], s["id_switches"]
    line = review["protocol"]["roi_bottom_px"]

    def with_interval(score: dict) -> str:
        low, high = score["ci95"]
        return f"{score['value']:.2f} [{low:.2f}, {high:.2f}]"

    rows = [
        _fig(["Precision", with_interval(p),
              f"{s['verdicts']['vehicle']} of {s['boxes']} sampled boxes cover one vehicle"],
             "reviewed"),
        _fig(["Recall", with_interval(r),
              f"{r['found']} vehicles boxed and {r['missed']} missed, above row {line}"],
             "reviewed"),
        _fig(["Id switches", str(ids["total"]),
              f"{ids['tracks_with_a_switch']} of {ids['tracks']} tracks change vehicle"],
             "reviewed"),
    ]  # fmt: skip
    frames = [
        _fig([str(f["frame"]), str(f["boxes"]), str(f["vehicle"]), str(f["missed"])],
             "sam", "reviewed")
        for f in s["per_frame"]
    ]  # fmt: skip
    caveats = "".join(f"<li>{escape(c)}</li>" for c in s["caveats"])
    return [
        "<p><strong>What it misses, measured.</strong> One person marked every "
        f"box in six sampled frames, counted the vehicles above image row {line} that no box "
        f"covers, and checked each track's crops for a change of vehicle: {p['value']:.0%} of "
        f"the sampled boxes are vehicles, and {r['value']:.0%} of the vehicles above the row "
        "have a box.</p>",
        _table(["Measure", "Value [95% interval]", "From"], rows),
        _table(["Frame", "Boxes", "Marked vehicle", f"Missed above row {line}"], frames),
        '<p class="caption">Below that row are the window frame and reflections. By eye, not '
        "counted, most missed vehicles are queued cars, and the bus prompt found nothing "
        "although a long vehicle is visible.</p>",
        f'<details><summary>Limits of this check</summary><ul class="caption">{caveats}</ul>'
        "</details>",
    ]


def _sam_step(demo: Demo, video_url: str | None) -> str:
    """Step 0: SAM 3.1 on the real footage, kept apart from everything the later steps use."""
    sam = demo.records[SAM_RECORD]
    clip, tracking = sam["clip"], sam["tracking"]
    # the cost is our estimate (sam_adapter.video_cost_usd), not SAM output: "computed by code"
    request_rows = [
        _fig([escape(q["prompt"]), str(q["frames_billed"]), str(q["observations"]),
              str(q["frames_with_an_observation"]), str(len(q["parser_diagnostics"])),
              f"${q['estimated_cost_usd']:.2f}"], "sam", "checked")
        for q in sam["requests"]
    ]  # fmt: skip
    per = tracking["tracked_objects_per_frame"]
    spread = f"{per['min']}–{per['max']} (median {per['median']:g})"
    track_row = _fig([str(tracking["tracks"]), spread, str(tracking["short_tracks_removed"])],
                     "sam", "derived")  # fmt: skip
    low, high = clip["trajectory_clip_window_s"]
    video = (
        f'<video controls preload="metadata" src="{escape(video_url)}"></video>'
        '<p class="caption">Boxes, track ids and trails: SAM 3.1 output turned into tracks by '
        "our procedure. A review aid, not ground truth.</p>"
        if video_url
        else f'<p class="caption">{escape(FOOTAGE_NOTE)}</p>'
        if SAM_OVERLAY in demo.absent
        else ""
    )
    body = [
        f"<p>SAM 3.1 segmented 10 s of the NGSIM camera 2 video ({clip['frames']} frames, "
        f"{low:g}–{high:g} s after 08:30:00, inside the window of step 3), prompted with "
        + " and then ".join(f"“{escape(q['prompt'])}”" for q in sam["requests"]) + ".</p>",
        video,
        _table(["Prompt", "Frames billed", "Observations", "Frames with one",
                "Parser diagnostics",
                "Estimated cost (computed by code: frames billed × the published per-frame "
                "price; not a bill)"], request_rows),
        _table(["Tracks", "Tracked cars per frame", "Short tracks removed"], [track_row]),
        *(_sam_review(demo.records[SAM_REVIEW]) if SAM_REVIEW in demo.records else [
            "<p><strong>What it misses.</strong> By eye (observed by us, not measured), many "
            "visible vehicles carry no box, most of them queued cars, and the bus prompt found "
            "nothing although a long vehicle is visible: SAM's recall on this 640×480 rooftop "
            "view is low. A measured check (precision, recall and id switches on at least 50 "
            "vehicles) is still to do.</p>"]),
        "<p><strong>Not used downstream.</strong> These tracks do not feed anything below. The "
        "camera's calibration was not accepted (ADR-018), so steps 1–7 use the published "
        "trajectories (ADR-019), and nothing here is compared with them.</p>",
        "<details><summary>Licence and attribution (CC BY-SA 4.0)</summary>"
        f"<pre>{escape(demo.texts.get(SAM_NOTICE, ''))}</pre></details>",
    ]  # fmt: skip
    say = "Meta's segmentation model on the real camera: what it sees, and what it misses."
    return (
        '<section class="step" id="sam"><h2>0 · What SAM 3.1 sees on the real footage</h2>'
        f'<p class="say">{escape(say)}</p>' + "\n".join(body) + "</section>"
    )


TIMELINE_NAMES = {"T1": "version 1", "T2": "version 2", "T3": "v2a", "T4": "v2b"}
CONTROL_TITLE = "Control: a known answer on a junction drawn in code (synthetic)"


def _ensemble_body(demo: Demo) -> list[str]:
    """Step 7 from the pre-registered ensemble (ADR-025): every member alone, then the rule."""
    record = demo.records[ENSEMBLE]
    members = record["members"]
    per = {m["id"]: differences(m) for m in members}
    verdicts = {v["candidate"]: v for v in record["verdict"]["candidates"]}
    head = ["Candidate", *(f"{m['id']} ({TIMELINE_NAMES.get(m['id'], m['id'])})"
                           for m in members), "Across timelines"]  # fmt: skip
    rows = [
        _fig([escape(cand),
              *(_difference(per[m["id"]].get(cand, {}).get("mean_delay_s")) for m in members),
              f"<strong>{escape(v['verdict'].replace('_', ' '))}</strong>"
              + (f"<br><span class='muted'>worse on every timeline: "
                 f"{escape(', '.join(v['robust_harm']))}</span>" if v["robust_harm"] else "")],
             "simulated", "checked")
        for cand, v in sorted(verdicts.items())
    ]  # fmt: skip
    member_rows = [
        _fig([escape(m["id"]), f"<code>{escape(m['timeline']['file'].rsplit('/', 1)[-1])}</code>",
              escape(str(m["baseline_assessment"]).replace("_", " "))], "derived", "checked")
        for m in members
    ]  # fmt: skip
    rules = record.get("rules", {})
    amended = (
        f'<p class="muted">Rules: pre-registered {escape(rules["preregistered"])}, used '
        f"{escape(rules['used'])}. {escape(rules['amendment'])}</p>"
        if rules.get("amendment")
        else ""
    )
    verdict = record["verdict"]
    return [
        "<p>No signal log exists, so the timeline is estimated, and two of its rules cannot be "
        "settled by the trajectories. Four timelines, the corners of those two rules, were fixed "
        "before any run on the new ones. Each is judged on its own; a candidate counts only if it "
        "passes on all four. Nothing is pooled or averaged (ADR-025). Mean-delay difference from "
        "each timeline's baseline, same vehicles and seeds:</p>",
        _table(head, rows),
        _table(["Timeline", "File", "Its baseline, assessed against observations"], member_rows),
        f'<div class="decision"><p><strong>{escape(verdict["selection_reason"])}</strong> '
        f"{escape(verdict['recommendation'])}</p></div>",
        "<p>I1's one gain, on version 2, is a property of that timeline estimate: on the three "
        "others it worsens delay. Each interval still measures SUMO's noise only; the four "
        "corners are not a probability distribution, and the assumed signal constants and the "
        "single observed morning are not varied.</p>",
        amended,
    ]


def _control_section(demo: Demo) -> str:
    """The synthetic known-answer control (ADR-026), kept apart from the real junction above."""
    plan_rows, control_rows = [], []
    for role in demo.controls:
        record = demo.records[role]
        outcome = record["outcome"]
        rows = record["comparisons"]
        rows = rows["rows"] if isinstance(rows, dict) else rows
        fix, null, mirror = outcome["known_correction"], outcome["null"], outcome["mirror"]
        delay = next(row for row in rows if row["candidate"] == fix["intervention_id"]
                     and row["metric"] == "mean_delay_s")  # fmt: skip
        change = Difference(
            value=delay["mean_difference"],
            interval=(delay["ci95_difference"][0], delay["ci95_difference"][1]),
            unit=delay["unit"],
            verdict=delay["verdict"],
            confirmed="mean_delay_s" in fix["holm_confirmed_improvements"],
        )
        bad, good = null["greens_s"], fix["greens_s"]
        chosen = "<strong>selected</strong>" if fix["selected"] else (
            f"<strong>not selected</strong>: {escape(fix['why_not_selected'] or '')}")  # fmt: skip
        plan_rows.append(_fig([escape(record["scenario"]), f"{bad['EW']} / {bad['NS']} s",
                               f"{good['EW']} / {good['NS']} s", _difference(change), chosen],
                              "assumed", "simulated", "checked"))  # fmt: skip
        perturbations = outcome["random_perturbations"]
        false = outcome["false_positives"]
        control_rows.append(_fig([
            escape(record["scenario"]),
            escape(null["why_not_selected"] or ""),
            f"{escape(mirror['simulation_status'])}; "
            + ("selected" if mirror["selected"] else "not selected"),
            f"{perturbations['with_holm_confirmed_improvement']['count']} of "
            f"{perturbations['count']} with a Holm-confirmed improvement",
            f"{false['count']} of {false['opportunities']} "
            f"(95% upper bound {false['rule_of_three_upper_bound_95']:.2f})",
        ], "simulated", "checked"))  # fmt: skip
    body = [
        '<div class="decision"><p><strong>Synthetic.</strong> This junction, its demand, its '
        "signal plans and every candidate are written in code. It says nothing about the Los "
        "Angeles junction above, and no model proposes here: it tests whether the evaluation and "
        "selection stage finds a known answer and refuses what it should (ADR-026).</p></div>",
        "<p>Each scenario starts from a deliberately bad plan. The better plan was computed "
        "before any simulation (Webster's method) and put among the candidates with a half-way "
        "plan, an exact copy of the bad plan, its mirror image and eight small random changes. "
        "All went through the same validator; the exact copy is rejected there (its timing is "
        "the baseline's), so no simulated null exists, and the others went through the same "
        "simulation, statistics and selection as above, on 20 paired seeds.</p>",
        _table(
            [
                "Scenario",
                "Bad plan (E-W / N-S green)",
                "Known better plan",
                "Its mean-delay change",
                "Outcome",
            ],
            plan_rows,
        ),  # fmt: skip
        "<p>In the long-cycle scenario the better plan harms nothing, and it is selected. In the "
        "reallocation scenario it trades the quiet road's queue for the busy road's delay, and the "
        "rule refuses any worsening: that refusal is the rule working, not a miss. Agreeing harm "
        "margins is a decision for people, not for the code.</p>",
        _table(
            [
                "Scenario",
                "Exact copy of the bad plan",
                "Mirror image",
                "Random changes",
                "False positives",
            ],
            control_rows,
        ),  # fmt: skip
        '<p class="muted">A false positive is an improvement confirmed against the direction '
        "predicted in advance. The test's power is limited: the known answer moves delay far "
        "more than the random changes do (DATA_REGISTER.md §26).</p>",
    ]
    say = "Check the stage on a case whose answer is known before trusting its refusals."
    return (
        f'<section class="step" id="control"><h2>8 · {escape(CONTROL_TITLE)}</h2>'
        f'<p class="say">{escape(say)}</p>' + "\n".join(body) + "</section>"
    )


def render_demo_page(
    demo: Demo,
    *,
    run_url: str | None,
    sam_video_url: str | None = None,
    after_sam: str = "",
    after_proposals: str = "",
) -> str:
    """The demo's steps, every figure taken from a pinned record and labelled by kind.

    `after_sam` and `after_proposals` are panels rendered elsewhere (cairoflow_api.meta_depth)
    from records pinned by their own sidecar; they are inserted as given."""
    r = demo.records
    scenario, estimate, baseline = r["scenario"], r["timeline_estimate"], r["baseline"]
    validation, results = r["validation"], r["results"]
    report = results["report"]
    legend = " ".join(_badge(kind) for kind in DEMO_KINDS)
    toc = "".join(f'<li><a href="#{a}">{escape(t)}</a></li>' for a, t in STEPS)
    if demo.has_sam:
        toc = '<li value="0"><a href="#sam">What SAM 3.1 sees on the real footage</a></li>' + toc
    if demo.controls:
        toc += f'<li><a href="#control">{escape(CONTROL_TITLE)}</a></li>'
    parts = [
        f"<h1>CairoFlow demo: {escape(demo.manifest.title)}</h1>",
        f'<p class="muted">Demo <code>{escape(demo.manifest.demo_id)}</code>. Every figure below '
        "is read from a committed record pinned by SHA-256 (see Provenance at the end), or "
        "computed from those records by this page's code as it renders: the evidence check "
        "(labelled &#8220;computed by code&#8221;), totals, differences and spans. No model is "
        "called and nothing is simulated while you read. Decision support only.</p>",
        f"<p>Kinds: {legend}</p>",
        f'<ol class="toc">{toc}</ol>',
    ]
    if demo.has_sam:
        parts.append(_sam_step(demo, sam_video_url))
    if after_sam:
        parts.append(after_sam)

    # 1 · evidence
    rows = [
        _fig([escape(f"{m['approach']} → {m['exit']} ({m['turn']})"), str(m["count_veh"]),
              f"{m['flow_veh_per_h']:.0f} [{m['flow_ci95_veh_per_h'][0]:.0f}, "
              f"{m['flow_ci95_veh_per_h'][1]:.0f}]"], "derived")
        for m in scenario["movements"]
    ]  # fmt: skip
    total = sum(m["count_veh"] for m in scenario["movements"])
    parts.append(_step(1, "Real traffic, not a toy: every vehicle through one Los Angeles "
                          "junction in fifteen minutes, from published trajectories.", [
        f"<p>{escape(scenario['input']['attribution'])}; {escape(scenario['input']['licence'])}. "
        "The numbers from here on use no camera, no pixels and no image registration "
        "(ADR-018, ADR-019).</p>",
        f"<p>{total} vehicles counted over {scenario['window']['duration_s']:.0f} s, by "
        "movement (flows with exact Poisson 95% intervals):</p>",
        _table(["Movement", "Vehicles", "Flow, veh/h"], rows),
    ]))  # fmt: skip

    # 2 · signals
    v1, v2 = estimate["greens"]["v1"], estimate["greens"]["v2"]
    green_rows = [
        _fig([escape(head), f"{v1[head]['median_green_s']:g} s ({v1[head]['greens']})",
              f"{v2[head]['median_green_s']:g} s ({v2[head]['greens']})"], "derived")
        for head in v2 if head in v1
    ]  # fmt: skip
    shares = {(c["approach"], c["turn"]): c for c in estimate["consistency"]}
    share_rows = [
        _fig([escape(f"{arm} {turn}"), f"{shares[(arm, turn)]['v1_share'] * 100:.0f}%",
              f"{shares[(arm, turn)]['v2_share'] * 100:.0f}%"], "derived")
        for arm, turn in (("W", "left"), ("W", "through"), ("N", "through"), ("S", "through"))
        if (arm, turn) in shares
    ]  # fmt: skip
    policy = estimate["policy"]
    constants = (
        f"yellow {policy['yellow_s']:g} s, start-up {policy['startup_lost_s']:g} s, clearance "
        f"{policy['all_red_s']:g} s, minimum green {policy['min_green_s']:g} s"
    )
    parts.append(_step(2, "No signal head was ever read: the timing is estimated from when "
                          "vehicles crossed the stop line.", [
        "<p>The timeline replayed here is version 2 (ADR-023). It counts permissive left turners "
        "as evidence of a green, and starts each yellow at the last crossing. Version 1 is kept "
        "unchanged beside it.</p>",
        _table(["Signal head", "Median green, v1 (greens)", "Median green, v2 (greens)"],
               green_rows),
        _table(["Movement", "Crossing in green or yellow, v1", "v2"], share_rows),
        f"<p>Constants inside the timeline: {escape(constants)} {_badge('assumed')}</p>",
    ]))  # fmt: skip

    # 3 · baseline
    lag_rows = [
        _fig([escape(lag.movement), str(lag.paired_veh),
              f"{lag.median_low_s:+.1f} to {lag.median_high_s:+.1f} s",
              f"{lag.share_within_5_s * 100:.0f}%"], "derived", "simulated")
        for lag in paired_lags(validation)
    ]  # fmt: skip
    val_rows = [
        _fig([escape(c["quantity"]), _side(c["observed"], c["unit"]),
              _side(c["simulated"], c["unit"]), escape(c["status"])], "derived", "simulated")
        for c in validation_rows(validation)
    ]  # fmt: skip
    gaps = "".join(
        f"<li><strong>{escape(n['quantity'])}</strong>: {escape(n['reason'])}</li>"
        for n in validation["not_validated"][:3]
    )
    a = demo.assessment
    findings = "".join(f"<li>{escape(item)}</li>" for item in a.findings)
    seeds = len(baseline["settings"]["seeds"])
    entered = sum(baseline["entries"].values())
    window = baseline["replay"]["window_s"]
    parts.append(_step(3, "Before trusting any what-if, check the model against what really "
                          "happened, vehicle by vehicle.", [
        f"<p>SUMO replays the estimated signals over clip [{window[0]:g}, {window[1]:g}) s "
        f"({(window[1] - window[0]) / 60:.1f} of the 15 minutes: the span where every signal "
        f"state is known), with {entered} vehicles as its demand: "
        f"{baseline['entries'].get('observed', 0)} observed and "
        f"{entered - baseline['entries'].get('observed', 0)} placed by the replay rules, "
        f"on {seeds} "
        "pre-registered seeds. "
        + ("Every seed is valid (no teleports or collisions, every vehicle arrived)"
           if baseline["valid"] else "<strong>Not every seed is valid</strong>")
        + ("; each rerun is identical" if baseline["reruns_identical"]
           else "; <strong>reruns differ</strong>")
        + f" {_badge('checked')}.</p>",
        "<p>Each simulated vehicle is paired with the vehicle it replays. Stop-line crossing, "
        "simulated less observed:</p>",
        _table(["Movement", "Paired vehicles", "Median lag, range over seeds",
                "Within 5 s (pooled)"], lag_rows),
        "<p>The same rules measure both sides (observed: 95% interval; simulated: range over "
        "seeds):</p>",
        _table(["Quantity", "Observed (approximated)", "Simulated", "Status"], val_rows),
        f"<p>Not validatable at all, among others:</p><ul>{gaps}</ul>",
        f'<p>Baseline assessment: <span class="status">{escape(a.status.replace("_", " "))}'
        f"</span></p><p>{escape(a.summary)}</p><details><summary>The assessment's "
        f"{len(a.findings)} findings</summary><ul>{findings}</ul></details>",
        f'<p class="muted">Assessed on {escape(a.assessed_on)}; {escape(a.documented_in)}.</p>',
    ]))  # fmt: skip

    # 4 · proposals
    texts = {p.intervention_id: p for p in demo.reasoning.interventions}
    outcomes = {o["intervention_id"]: o for o in r["proposals"]["validation"]}
    body = [
        "<p>Muse Spark read an evidence bundle of the observations and proposed hypotheses and "
        "signal-timing changes. Its words are shown boxed; they are not evidence. A "
        "deterministic validator then checked each change against the compiled network.</p>",
        f"<p>Muse was asked once, on {escape(r['proposals']['created_at_utc'][:10])}, with the "
        "evidence of the first (repeated-cycle) baseline; it never saw the replay baseline "
        "shown in step 3. Its changes are carried onto the replay's cycles by phase, as listed "
        "under each proposal.</p>",
        '<p class="muted">Known slip in the reply: it calls arm N\'s left turners "northbound"; '
        "arm N means vehicles arriving from the north (cache/muse/NOTICE.md).</p>",
    ]
    checked = {g.hypothesis_id: g for g in demo.grounding.hypotheses} if demo.grounding else {}
    stored = stored_citations_of_demo(demo)
    body += [
        _hypothesis(h, checked.get(h.hypothesis_id), stored=stored)
        for h in demo.reasoning.hypotheses
    ]
    for p in r["proposals"]["proposals"]:
        pid = p["intervention_id"]
        changes = [
            _fig([escape(c["phase_name"]), f"{c['green_s']:g} s"], "model")
            for c in p["phase_changes"]
        ]
        outcome = outcomes.get(pid, {})
        mapped = "".join(f"<li>{escape(n)}</li>" for n in results["candidate_notes"].get(pid, []))
        body += [
            f"<h3>{escape(pid)}</h3>",
            _table(["Phase", "Proposed green"], changes),
            f"<p>Validator: <strong>{escape(outcome.get('status', 'unknown'))}</strong>, cycle "
            f"{outcome.get('baseline_cycle_s', 0):g} s → {outcome.get('cycle_s', 0):g} s "
            f"{_badge('checked')}</p>",
            f"<p>Carried onto every replayed cycle {_badge('derived')}</p><ul>{mapped}</ul>",
        ]
        if pid in texts:
            body.append(_model_text(f"{pid} rationale", texts[pid].rationale))
    body.append(_stored_rest(stored, [h.hypothesis_id for h in demo.reasoning.hypotheses]))
    parts.append(_step(4,"The model proposes; it never decides. Every proposal passes a "
                          "deterministic check before anything is simulated.", body))  # fmt: skip
    if after_proposals:
        parts.append(after_proposals)

    # 5 · results
    diffs = differences(results)
    status = {e["candidate"]: e["status"] for e in results["eligibility"]}
    head = ["Candidate", *(METRIC_TEXT[m] for m in DECISION_METRICS)]
    scale = baseline_ranges(results)
    base_row = _fig(["Baseline<br><span class='muted'>range over seeds</span>",
                     *(f"{scale[m][0]:.1f}–{scale[m][1]:.1f} "
                       f"{escape(UNIT_TEXT.get(scale[m][2], scale[m][2]))}" if m in scale
                       else "" for m in DECISION_METRICS)], "simulated")  # fmt: skip
    result_rows = [base_row] + [
        _fig([f"{escape(cand)}<br><span class='muted'>{escape(status.get(cand, ''))}</span>",
              *(_difference(diffs[cand].get(m)) for m in DECISION_METRICS)], "simulated")
        for cand in sorted(diffs)
    ]  # fmt: skip
    parts.append(_step(5, "Each change is simulated on the same vehicles and seeds as the "
                          "baseline, and judged metric by metric: no weighted score.", [
        f"<p>Paired difference from the baseline over {len(results['seeds'])} seeds, with its "
        "95% interval; the first row gives the baseline's own values for scale. A metric "
        "worsens when the interval excludes zero on the worse side; an improvement also needs "
        "Holm's correction (✓). Mean delay and the east and west queues have no observed "
        "counterpart (step 3): they are SUMO's numbers only.</p>",
        _table(head, result_rows),
    ]))  # fmt: skip

    # 6 · decision
    selected = report["selected_intervention_id"] or "none"
    recommended = report["recommended_intervention_id"] or "none"
    parts.append(_step(6, "Nothing passes, so nothing is recommended, and the page says why.", [
        f'<div class="decision"><p><strong>Selected: {escape(selected)}.</strong> '
        f"{escape(report['selection_reason'])}</p><p><strong>Recommended: "
        f"{escape(recommended)}.</strong> {escape(report['recommendation'])}</p></div>",
        f'<p class="muted">Selection rule: {escape(SELECTION_RULE)}</p>',
        f'<p class="muted">Recommendation rule: {escape(RECOMMENDATION_RULE)}</p>',
    ]))  # fmt: skip

    # 7 · sensitivity
    if demo.has_ensemble:
        parts.append(_step(7, "Narrow intervals are not certainty: a verdict must hold on every "
                              "signal timeline the evidence supports, and none does.",
                           _ensemble_body(demo)))  # fmt: skip
    else:
        old = differences(r["results_v1"])
        sens_rows = [
            _fig([escape(cand), _difference(old.get(cand, {}).get("mean_delay_s")),
                  _difference(diffs[cand].get("mean_delay_s"))], "simulated")
            for cand in sorted(diffs)
        ]  # fmt: skip
        parts.append(_step(7, "Narrow intervals are not certainty: change the signal estimate "
                              "and a verdict can flip.", [
            "<p>Mean-delay difference of the same candidates on the same vehicles and seeds, "
            "with the signals estimated by version 1 and by version 2 of the timeline rules:</p>",
            _table(["Candidate", "Timeline version 1", "Timeline version 2"], sens_rows),
            "<p>Each interval measures SUMO's noise for one timeline estimate, not the "
            "uncertainty of the estimate itself. Where the two exclude each other, no "
            "single-timeline verdict is a basis for action until that uncertainty is carried "
            "into the comparison (ADR-023).</p>",
        ]))  # fmt: skip
    if demo.controls:
        parts.append(_control_section(demo))

    # provenance
    pins = "".join(
        f"<tr><td>{escape(role)}</td><td><code>{escape(pin.path)}</code></td>"
        f"<td><code>{escape(pin.sha256)}</code></td></tr>"
        for role, pin in demo.manifest.files.items()
    )
    full = (
        f'<p>Full run report, with every number traced: <a href="{escape(run_url)}">'
        f"{escape(run_url)}</a></p>"
        if run_url
        else '<p class="muted">The full run report is not served here (its run directory is '
        "Git-ignored; the README says how to recreate it).</p>"
    )
    parts += [
        '<section class="step" id="provenance"><h2>Provenance</h2>',
        "<p>The files this page reads, as pinned in the demo manifest. If any of them changes, "
        "the page refuses to show.</p>",
        f"<table><thead><tr><th>Role</th><th>File</th><th>SHA-256</th></tr></thead>"
        f"<tbody>{pins}</tbody></table>",
        full,
        "</section>",
    ]
    return _page(f"CairoFlow demo {demo.manifest.demo_id}", "\n".join(parts), synthetic=False)


def render_demo_error(message: str) -> str:
    body = (
        "<h1>Demo unavailable</h1><p>The demo shows only records pinned by its manifest, and "
        f"one of them does not match:</p><p><code>{escape(message)}</code></p>"
        '<p class="muted">Rebuild the manifest from the current records with '
        "<code>scripts/build_demo_manifest.py</code> under a new demo id.</p>"
    )
    return _page("CairoFlow demo unavailable", body, synthetic=False)
