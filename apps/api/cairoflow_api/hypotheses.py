"""The citation check stored with a Muse reply, read back for the pages that show its hypotheses.

A hypothesis is AI-generated text (Muse Spark). When code parsed the reply it listed every
supporting or contradicting evidence id a hypothesis cites, and every evidence id a proposal
cites, that the bundle lacks, and every hypothesis a proposal addresses that the reply lacks
(`cairoflow_reasoning.muse_adapter.citation_problems`; ids restated under a hypothesis's
observations are not in it). It says nothing on whether the evidence supports a hypothesis.
That list was stored with the reply:
in the experiment record (`muse_request.citation_problems`) and in a run's `reasoning.json`
(`problems`). This module reads the stored list back; it never recomputes it (the recomputed
evidence check is `Demo.grounding`). An empty list is a recorded "none". A list that is missing,
unreadable or stored for another reply is never shown as "none": it is "not recorded", or the
reason it is not shown.

Read-only: every path comes from a record the app already trusts (the demo manifest, an
allow-listed run directory, the ProblemStatement's pinned source), never from a request.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from cairoflow_contracts.problem_statement import AttachedHypothesis, SourceFile
from cairoflow_contracts.reasoning import Hypothesis, ReasoningResult

if TYPE_CHECKING:
    from cairoflow_api.demo import Demo

RECORD_KEY = "muse_request.citation_problems"  # in the experiment record the demo pins
RUN_FILE = "reasoning.json"  # a pipeline run's ReasoningResult
RUN_FILES = (RUN_FILE, f"source/{RUN_FILE}")  # as pipeline runs and replay experiments pin it
RUN_KEY = "problems"


@dataclass(frozen=True)
class StoredCitations:
    """The citation check stored with one reply: where it was read, under which key, and the list
    as stored (None when there is none to show; `note` then says why, if not simply absent)."""

    source: str
    key: str
    problems: tuple[str, ...] | None
    note: str | None = None

    def of(self, hypothesis_id: str) -> tuple[str, ...] | None:
        """The entries about one hypothesis ("H1 cites ..."), by its whole id: never H10's."""
        if self.problems is None:
            return None
        return tuple(p for p in self.problems if p.startswith(f"{hypothesis_id} "))

    def rest(self, hypothesis_ids: Iterable[str]) -> tuple[str, ...] | None:
        """The entries about none of these hypotheses: the proposals' ("I1 cites ...")."""
        if self.problems is None:
            return None
        prefixes = tuple(f"{h} " for h in hypothesis_ids)
        return tuple(p for p in self.problems if not p.startswith(prefixes))


@dataclass(frozen=True)
class AttachedDetail:
    """An attached hypothesis read back from the reply its ProblemStatement pins: the full
    hypothesis and its stored check, or why neither is shown."""

    hypothesis: Hypothesis | None
    stored: StoredCitations | None
    refusal: str | None


def _strings(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return None


def stored_citations_of_demo(demo: Demo) -> StoredCitations:
    """The list the pinned proposals record stored. `load_demo` has already checked that this
    record's request key is the pinned reply's, so the list is that reply's."""
    path = demo.manifest.files["proposals"].path
    request = demo.records["proposals"].get("muse_request")
    stored = request.get("citation_problems") if isinstance(request, dict) else None
    return StoredCitations(path, RECORD_KEY, _strings(stored))


def stored_citations_of_run(
    run_dir: Path, artifacts: Mapping[str, str], hypotheses: Sequence[Hypothesis]
) -> StoredCitations:
    """The list the reasoning.json a run's pipeline record pins (`artifacts`: path in the run
    directory -> SHA-256) stored, used only if the file is the pinned one and holds exactly the
    hypotheses the run's report shows. Pipeline runs pin `reasoning.json`; the replay experiments
    pin `source/reasoning.json`, which their run directories do not hold."""
    pinned = next(((name, artifacts[name]) for name in RUN_FILES if name in artifacts), None)
    if pinned is None:
        return StoredCitations(RUN_FILE, RUN_KEY, None)  # nothing stored: "not recorded"
    name, sha256 = pinned
    try:
        data = (run_dir / name).read_bytes()
    except OSError:
        return StoredCitations(
            name, RUN_KEY, None,
            f"the pipeline record pins {name} (SHA-256 {sha256[:12]}), which this run "
            "directory does not hold",
        )  # fmt: skip
    if hashlib.sha256(data).hexdigest() != sha256:
        return StoredCitations(name, RUN_KEY, None, f"{name} is not the file the record pins")
    try:
        result = ReasoningResult.model_validate_json(data)
    except ValidationError:
        return StoredCitations(name, RUN_KEY, None, f"{name} is not a stored reasoning result")
    if result.output.hypotheses != tuple(hypotheses):
        return StoredCitations(
            name, RUN_KEY, None, f"{name} holds other hypotheses than this report"
        )
    return StoredCitations(name, RUN_KEY, result.problems)


def _reply(repo: Path, source: SourceFile) -> ReasoningResult | str:
    try:
        data = (repo / source.path).read_bytes()
    except OSError:
        return f"{source.path} is not present"
    if hashlib.sha256(data).hexdigest() != source.sha256:
        return f"{source.path} no longer has the SHA-256 the problem record names"
    try:
        return ReasoningResult.model_validate_json(data)
    except ValidationError:
        return f"{source.path} is not a stored reasoning result"


def attached_details(
    attached: Iterable[AttachedHypothesis], repo: Path
) -> dict[tuple[str, str], AttachedDetail]:
    """Each attached hypothesis, keyed by (source path, hypothesis id), read back from the
    reasoning.json its ProblemStatement pins: the file must still have the pinned SHA-256 and hold
    that hypothesis with the same statement, or the detail is refused with the reason."""
    replies: dict[tuple[str, str], ReasoningResult | str] = {}
    details: dict[tuple[str, str], AttachedDetail] = {}
    for item in attached:
        pin = (item.source.path, item.source.sha256)
        if pin not in replies:
            replies[pin] = _reply(repo, item.source)
        reply = replies[pin]
        key = (item.source.path, item.hypothesis_id)
        if isinstance(reply, str):
            details[key] = AttachedDetail(None, None, reply)
            continue
        found = next(
            (h for h in reply.output.hypotheses if h.hypothesis_id == item.hypothesis_id), None
        )
        if found is None or found.statement != item.statement:
            details[key] = AttachedDetail(
                None,
                None,
                f"{item.source.path} holds no {item.hypothesis_id} with this statement",
            )
            continue
        stored = StoredCitations(item.source.path, RUN_KEY, reply.problems)
        details[key] = AttachedDetail(found, stored, None)
    return details
