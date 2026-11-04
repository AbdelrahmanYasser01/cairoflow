"""Two /demo panels from the meta-depth records: a Muse frame annotation, and reply recurrence.

The records are pinned by SHA-256 in `data/demo/meta-depth-pins.json`, a sidecar to the manifest
(written by scripts/pin_meta_depth.py), and checked on every request like the manifest's own
pins: a missing or changed file shows one line saying so, never its content. Each group is
optional: the frame panel needs `frame_annotation`, `frame_image` and `frame_notice`; the
recurrence panel needs `muse_recurrence`. Everything shown is either model text (boxed and
labelled model-generated, HTML-escaped) or a count the recurrence record holds (computed by code).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from html import escape
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cairoflow_api.demo import PinnedFile
from cairoflow_api.html import _badge, _fig, _table
from cairoflow_contracts.meta_depth import FrameAnnotationRecord, MuseRecurrence

PINS_FILE = "data/demo/meta-depth-pins.json"
FRAME_ROLES = ("frame_annotation", "frame_image", "frame_notice")
RECURRENCE_ROLE = "muse_recurrence"


class MetaDepthPins(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "0.1.0"
    files: dict[str, PinnedFile]


@dataclass(frozen=True)
class MetaDepth:
    frame: FrameAnnotationRecord | None = None
    notice: str | None = None
    recurrence: MuseRecurrence | None = None
    problem: str | None = None  # why nothing is shown, when a pin does not hold
    pins: MetaDepthPins | None = None


def load_meta_depth(repo: Path, absent_allowed: tuple[str, ...] = ()) -> MetaDepth:
    """The pinned records, or the reason they cannot be shown. No file: nothing to show.

    `absent_allowed` comes from the demo manifest: a role it names may be missing from this tree,
    and the panel still shows what the records hold (it never draws the image).
    """
    path = repo / PINS_FILE
    if not path.is_file():
        return MetaDepth()
    pins = MetaDepthPins.model_validate_json(path.read_text("utf-8"))
    raw: dict[str, bytes] = {}
    for role, pin in pins.files.items():
        file = repo / pin.path
        if not file.is_file():
            if role in absent_allowed:
                continue
            return MetaDepth(problem=f"{pin.path} is missing", pins=pins)
        data = file.read_bytes()
        if hashlib.sha256(data).hexdigest() != pin.sha256:
            return MetaDepth(problem=f"{pin.path} differs from {PINS_FILE}", pins=pins)
        raw[role] = data
    frame = notice = recurrence = None
    if any(role in raw for role in FRAME_ROLES):
        if not all(role in raw or role in absent_allowed for role in FRAME_ROLES):
            return MetaDepth(problem="the frame annotation is pinned without its image or NOTICE")
        frame = FrameAnnotationRecord.model_validate_json(raw["frame_annotation"])
        if frame.frame.image_sha256 != pins.files["frame_image"].sha256:
            return MetaDepth(problem="the frame annotation names another image", pins=pins)
        notice = raw["frame_notice"].decode("utf-8")
    if RECURRENCE_ROLE in raw:
        recurrence = MuseRecurrence.model_validate_json(raw[RECURRENCE_ROLE])
    return MetaDepth(frame=frame, notice=notice, recurrence=recurrence, pins=pins)


def _model(text: str) -> str:
    return f'<div class="model">{escape(text)}</div>'


def _pins(meta: MetaDepth, roles: tuple[str, ...]) -> str:
    assert meta.pins is not None
    rows = "".join(
        f"<li><code>{escape(meta.pins.files[r].path)}</code> "
        f"<code>{meta.pins.files[r].sha256[:16]}…</code></li>"
        for r in roles
        if r in meta.pins.files
    )
    return f'<p class="muted">Pinned by SHA-256 in <code>{PINS_FILE}</code>:</p><ul>{rows}</ul>'


def render_pin_problem(meta: MetaDepth) -> str:
    return f'<p class="muted">Model receipts not shown: {escape(meta.problem or "")}.</p>'


def render_frame_panel(meta: MetaDepth) -> str:
    """After step 0: what Muse Spark says about one frame of the overlay above."""
    record = meta.frame
    if record is None or record.annotation is None:
        return ""
    a, f = record.annotation, record.frame
    rows = [
        _fig([escape(o.category), escape(o.region.replace("_", " ")),
              escape(o.overlay_box.replace("_", " ")), escape(o.note)], "model")
        for o in a.objects
    ]  # fmt: skip
    boxed = sum(o.overlay_box == "boxed" for o in a.objects)
    return "".join(
        [
            '<section id="frame-annotation"><h3>A second model reads one frame of this video</h3>',
            f"<p>Frame {f.frame_index} ({f.time_s:g} s into the overlay video, "
            f"{f.width_px}×{f.height_px}) was sent once to {escape(record.model)} with a strict "
            f"JSON schema (prompt <code>{escape(record.prompt_version)}</code>). Its reply "
            f"is {_badge('model')}: not ground truth, not a measurement, and nothing on this "
            "page uses it.</p>",
            f"<p>Scene, in the model's words: {_model(a.scene_summary)}</p>",
            _table(["Category", "Where in the frame", "Overlay box", "Model note"], rows),
            f"<p>Of the {len(a.objects)} road users it lists, it reads {boxed} as boxed "
            f"{_badge('checked')} (counted by this page from the reply; the reply holds no "
            f"numbers). Image quality, as the model rates it: {escape(a.image_quality)}.</p>",
            "<p>Overlay problems the model points to:</p>",
            "".join(_model(issue) for issue in a.overlay_issues) or "<p>None.</p>",
            "<p>Limitations it names:</p>",
            "".join(_model(item) for item in a.limitations),
            f'<p class="muted">Request <code>{record.request_key[:16]}…</code>, recorded cost '
            f"${record.recorded_cost_usd:.4f} (published prices × reported usage; not a bill). "
            f"The frame is CC BY-SA 4.0: {escape(meta.notice.splitlines()[0].lstrip('# '))}"
            "</p>"
            if meta.notice
            else "",
            _pins(meta, FRAME_ROLES),
            "</section>",
        ]
    )


def render_recurrence_panel(meta: MetaDepth) -> str:
    """After step 4: across n replies to the same request, what recurs (counts by code)."""
    rec = meta.recurrence
    if rec is None:
        return ""
    n = rec.n_replies
    evidence = [
        _fig([f"<code>{escape(e.evidence_id)}</code>", f"{e.cited_in} of {n}",
              f"{e.supporting_in} of {n}", f"{e.contradicting_in} of {n}"], "checked")
        for e in rec.evidence
    ]  # fmt: skip
    clusters = []
    for c in rec.hypotheses:
        members = ", ".join(f"{m.reply_id}:{m.hypothesis_id}" for m in c.members)
        shared = ", ".join(c.shared_evidence_ids) or "none"
        statements = "".join(
            _model(f"{m.reply_id}:{m.hypothesis_id} ({m.confidence_label}) {m.statement}")
            for m in c.members
        )
        clusters.append(
            f"<h4>{escape(c.cluster_id)}: in {c.in_replies} of {n} replies "
            f"{_badge('checked')}</h4><p>Members {escape(members)}; evidence every member cites: "
            f"<code>{escape(shared)}</code></p>{statements}"
        )
    types = [
        _fig([escape(t.intervention_type), f"{t.in_replies} of {n}"], "checked")
        for t in rec.interventions
    ]
    replies = ", ".join(f"{r.reply_id} (replicate {r.replicate})" for r in rec.replies)
    return "".join(
        [
            f'<section id="recurrence"><h3>The same request, {n} replies: what recurs</h3>',
            f"<p>The request behind step 4 (prompt <code>{escape(rec.prompt_version)}</code>, key "
            f"<code>{rec.request_key[:16]}…</code>) was sent again unchanged; each reply is a "
            f"stored record ({escape(replies)}). The counts are {_badge('checked')} from those "
            f"replies; the statements are {_badge('model')}. A hypothesis that recurs is "
            "still a hypothesis: recurrence measures the model's consistency, not the "
            "traffic.</p>",
            f'<p class="muted">Rule: {escape(rec.rule)}</p>',
            "<h4>Evidence ids the replies cite</h4>",
            _table(["Evidence id", "Cited", "In support", "Against"], evidence),
            "<h4>Hypotheses, linked across replies</h4>",
            *clusters,
            "<h4>Intervention types proposed</h4>",
            _table(["Type", "Proposed in"], types),
            _pins(meta, (RECURRENCE_ROLE,)),
            "</section>",
        ]
    )
