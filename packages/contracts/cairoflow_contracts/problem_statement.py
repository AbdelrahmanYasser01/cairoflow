"""ProblemStatement: the traffic problem a decision is about, as a record (schema 0.1.0, G3a).

A deterministic projection of committed records, computing no number of its own. Each value is
copied from a record that the statement lists with its SHA-256, under that record's own label:

- `observed`: NGSIM-derived quantities on the subject approach ("derived": CairoFlow's fixed
  rules over observed values, as the validation records call them), each beside the value the
  assessed baseline simulated with the same definition, the record's comparability, whether that
  simulated value lies inside the observed interval, the discrepancy and the record's caveats;
- `members`: per timeline member, the simulated baseline value of the decision metric (SUMO,
  metrics.py's definition, which has no observed counterpart: `definition_note`), the unit of
  inference with the record sentence it rests on, the baseline's assessment as recorded, and the
  Holm-confirmed improvements on that metric;
- `hypotheses`: model text from a stored Muse reply, attached for context as AI-generated.
  Never evidence: no hypothesis is cited by an observed quantity or by the statement.

`statement` is written by `statement_of` from those values; it never sets a value of one
definition beside a value of the other. `failure_conditions` are written by `conditions_of`:
one per member whose baseline is not validated (with its assessment's own summary), the
definition note, and the records' limitations. The validator rewrites both, checks that every
recorded improvement is a Holm-confirmed one in the metric's better direction and that every
value's source is listed, and checks the hash. The hash is a content hash, not a seal: anyone can
recompute it after an edit, so the record is checked by rebuilding it from the committed records
(two builds are byte-identical on one set of files).

`cairoflow_contracts.provenance.ProblemStatement` (frozen) is the chain's minimal stand-in for a
problem; this record is the problem itself, and names the WorldView entities it is about.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import Field, field_validator, model_validator

from cairoflow_contracts.evidence import Arm, EvidenceModel, Sha256, _check_sha256
from cairoflow_contracts.integrity import canonical_sha256

PROBLEM_STATEMENT_VERSION = "0.1.0"
Assessment = Literal["validated", "partially_validated", "not_validated", "not_assessed"]
ASSESSMENT_WORDS: dict[str, str] = {
    "validated": "validated",
    "partially_validated": "partially validated",
    "not_validated": "not validated",
    "not_assessed": "not assessed",
}


class SourceFile(EvidenceModel):
    """A record the statement read, by its repository path and the SHA-256 of its bytes."""

    path: str = Field(min_length=1, description="POSIX path relative to the repository")
    sha256: Sha256

    @field_validator("sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)


class ObservedQuantity(EvidenceModel):
    """An NGSIM-derived quantity as a validation record states it, with the same-definition
    value that record's baseline simulated and what the record says of the comparison."""

    key: str = Field(min_length=1, description="the validation record's key, e.g. queue_mean.S")
    quantity: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    label: Literal["derived"] = Field(description="fixed rules over observed NGSIM values")
    value: float | None = Field(description="None when the record states none")
    interval95: tuple[float, float] | None
    lower_bound: bool = Field(description="censored: the true value is at least this")
    simulated_value: float | None = Field(description="same definition, the record's baseline")
    simulated_lower_bound: bool = Field(description="the simulated value is censored too")
    simulated_inside_observed_interval: bool | None = Field(description="as the record states")
    discrepancy: float | None = Field(description="simulated minus observed, as recorded")
    comparability: str = Field(min_length=1, description="the record's status for the pair")
    limitations: tuple[str, ...] = Field(description="the record's caveats on this comparison")
    member: str = Field(min_length=1, description="the member whose baseline was validated")
    source: SourceFile


class ConfirmedImprovement(EvidenceModel):
    """A Holm-confirmed improvement of one candidate on the subject metric, as recorded."""

    candidate: str = Field(min_length=1)
    mean_difference: float = Field(description="candidate minus baseline, the metric's unit")
    ci95_difference: tuple[float, float]
    t_statistic: float
    p_value: float
    holm_threshold: float
    label: Literal["simulated"] = "simulated"


class MemberBaseline(EvidenceModel):
    """One timeline member's simulated baseline of the subject metric, and what is known of it."""

    member: str = Field(min_length=1)
    timeline_id: str = Field(min_length=1)
    required: bool
    baseline_manifest_sha256: Sha256
    metric_name: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    better: Literal["lower", "higher"]
    n: int = Field(ge=0, description="paired seeds")
    baseline_mean: float
    label: Literal["simulated"] = "simulated"
    unit_of_inference: str = Field(min_length=1)
    unit_basis: str = Field(min_length=1, description="the record sentence the unit rests on")
    assessment: Assessment
    assessment_source: SourceFile | None = Field(description="the assessment file, when cited")
    assessment_summary: str | None = Field(description="the assessment's own summary, copied")
    validation: SourceFile | None = Field(description="the committed validation record, if any")
    improvements: tuple[ConfirmedImprovement, ...]

    @field_validator("baseline_manifest_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)


class AttachedHypothesis(EvidenceModel):
    """Model text from a stored Muse reply: AI-generated, attached for context, never evidence."""

    hypothesis_id: str = Field(pattern=r"^H[1-9][0-9]?$")
    statement: str = Field(min_length=1)
    kind: Literal["ai_generated"] = "ai_generated"
    source: SourceFile


def _members(names: Sequence[str]) -> str:
    return ", ".join(names) if names else "no member"


def _observed_sentence(problem: ProblemStatement) -> str:
    queue = [q for q in problem.observed if q.key == f"queue_mean.{problem.approach}"]
    if not queue or queue[0].value is None:
        return "No NGSIM-derived counterpart of the queue was read."
    first = queue[0]
    bound = ", a censored lower bound" if first.lower_bound else ""
    interval = (
        f" [{first.interval95[0]:.1f}, {first.interval95[1]:.1f}]" if first.interval95 else ""
    )
    same = []
    for q in queue:
        if q.simulated_value is None:
            continue
        where = {True: "inside", False: "outside"}.get(q.simulated_inside_observed_interval)
        place = f", {where} the observed interval" if where else ""
        same.append(f"{q.simulated_value:.1f} {q.unit} ({q.member}{place})")
    simulated = (
        f", against {' and '.join(same)} simulated by the assessed baselines" if same else ""
    )
    quantity = first.quantity.removesuffix(f", {problem.approach}")
    quantity = quantity[:1].lower() + quantity[1:]
    return (
        f"With the observed definition ({quantity}{bound}): {first.value:.1f} "
        f"{first.unit}{interval} derived from NGSIM{simulated}."
    )


def statement_of(problem: ProblemStatement) -> str:
    """The statement, written from the copied values (no number is computed beyond rounding)."""
    means = [m.baseline_mean for m in problem.members]
    unit = problem.members[0].unit if problem.members else ""
    counterpart = (
        "; no observed counterpart with this definition" if problem.definition_note else ""
    )
    parts = [
        f"{problem.approach} approach, mean queue. Decision metric {problem.metric_name} "
        f"(SUMO's definition{counterpart}): "
        + (f"{min(means):.1f}-{max(means):.1f} {unit}" if means else "not recorded")
        + f" in the replay baseline of {len(means)} timeline members (simulated).",
        _observed_sentence(problem),
    ]
    on: dict[str, list[str]] = {}
    for m in problem.members:
        for improvement in m.improvements:
            on.setdefault(improvement.candidate, []).append(m.member)
    confirmed = [f"{c} on {_members(names)}" for c, names in sorted(on.items())]
    parts.append("Holm-confirmed shorter in simulation: " + ("; ".join(confirmed) or "none") + ".")
    by_status: dict[str, list[str]] = {}
    for m in problem.members:
        by_status.setdefault(m.assessment, []).append(m.member)
    status = "; ".join(
        f"{_members(names)} {ASSESSMENT_WORDS[s]}" for s, names in sorted(by_status.items())
    )
    validated = len(by_status.get("validated", []))
    if validated == len(problem.members):
        opening = "Every member's baseline is validated"
    elif validated == 0:
        opening = "No member's baseline is validated"
    else:
        opening = "Not every member's baseline is validated"
    parts.append(f"{opening} ({status}).")
    return " ".join(parts)


def conditions_of(problem: ProblemStatement) -> tuple[str, ...]:
    """The failure conditions: each member not validated (with its assessment's summary), the
    decision metric's definition note, then the records' limitations, all copied."""
    found = []
    for m in problem.members:
        if m.assessment == "validated":
            continue
        cited = f" ({m.assessment_source.path})" if m.assessment_source else ""
        summary = f": {m.assessment_summary}" if m.assessment_summary else ""
        found.append(f"{m.member}: baseline {ASSESSMENT_WORDS[m.assessment]}{cited}{summary}")
    if problem.definition_note:
        found.append(f"{problem.metric_name}: {problem.definition_note}")
    return (*found, *problem.limitations)


class ProblemStatement(EvidenceModel):
    """The problem on one approach and metric, projected from committed records."""

    problem_version: Literal["0.1.0"] = PROBLEM_STATEMENT_VERSION
    problem_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]*$")
    approach: Arm
    metric_name: str = Field(min_length=1)
    statement: str = Field(min_length=1, description="statement_of(): written by code")
    observed: tuple[ObservedQuantity, ...]
    members: tuple[MemberBaseline, ...] = Field(min_length=1)
    definition_note: str | None = Field(
        description="what the validation records say of the decision metric's definition"
    )
    hypotheses: tuple[AttachedHypothesis, ...]
    hypotheses_note: str | None = Field(description="why no hypothesis is attached, if none is")
    baseline_validated: bool = Field(description="True only if every member's baseline is")
    limitations: tuple[str, ...] = Field(description="the records' limitations, copied")
    failure_conditions: tuple[str, ...] = Field(description="conditions_of(): written by code")
    sources: tuple[SourceFile, ...] = Field(description="every record read, by path")
    entity_ids: tuple[str, ...] = Field(min_length=1, description="WorldView entity ids")
    problem_sha256: Sha256

    @field_validator("problem_sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)

    def digest(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"problem_sha256"}))

    @model_validator(mode="after")
    def _consistent(self) -> ProblemStatement:
        names = [m.member for m in self.members]
        if names != sorted(set(names)):
            raise ValueError("members must be listed once each, in order")
        if any(m.metric_name != self.metric_name for m in self.members):
            raise ValueError("every member reports the statement's metric")
        for m in self.members:
            if (m.assessment == "not_assessed") != (m.assessment_source is None):
                raise ValueError(f"{m.member}: an assessment is cited exactly when one was made")
            for i in m.improvements:
                low, high = i.ci95_difference
                better = high < 0 if m.better == "lower" else low > 0
                if not (low <= i.mean_difference <= high and better):
                    raise ValueError(
                        f"{m.member} {i.candidate}: the interval is not an improvement"
                    )
                if not i.p_value <= i.holm_threshold:
                    raise ValueError(f"{m.member} {i.candidate}: not rejected by Holm's procedure")
        validations = {m.member: m.validation for m in self.members}
        for q in self.observed:
            if validations.get(q.member) is None or validations[q.member] != q.source:
                raise ValueError(f"{q.key}: its member {q.member!r} has no such validation record")
        validated = all(m.assessment == "validated" for m in self.members)
        if self.baseline_validated != validated:
            raise ValueError("baseline_validated says whether every member's baseline is validated")
        if self.failure_conditions != conditions_of(self):
            raise ValueError("the failure conditions are not the ones conditions_of() writes")
        paths = [s.path for s in self.sources]
        if paths != sorted(set(paths)):
            raise ValueError("sources must be listed once each, in order")
        used = [q.source for q in self.observed] + [h.source for h in self.hypotheses]
        used += [s for m in self.members for s in (m.assessment_source, m.validation) if s]
        if any(s not in self.sources for s in used):
            raise ValueError("every record a value is copied from is listed in sources")
        if not self.hypotheses and not self.hypotheses_note:
            raise ValueError("say why no hypothesis is attached")
        if self.statement != statement_of(self):
            raise ValueError("the statement is not the one statement_of() writes from the values")
        if self.problem_sha256 != self.digest():
            raise ValueError("problem_sha256 does not hash the statement's content")
        return self


def build_problem_statement(
    *,
    approach: Arm,
    metric_name: str,
    members: Sequence[MemberBaseline],
    observed: Sequence[ObservedQuantity],
    definition_note: str | None,
    hypotheses: Sequence[AttachedHypothesis],
    hypotheses_note: str | None,
    limitations: Sequence[str],
    sources: Sequence[SourceFile],
) -> ProblemStatement:
    """The statement in canonical order, with its statement, failure conditions and hash."""
    unique = {s.path: s for s in sources}
    fields = dict(
        problem_id=f"{approach.lower()}-approach-mean-queue",
        approach=approach,
        metric_name=metric_name,
        statement="pending",
        observed=tuple(sorted(observed, key=lambda q: (q.key, q.member))),
        members=tuple(sorted(members, key=lambda m: m.member)),
        definition_note=definition_note,
        hypotheses=tuple(sorted(hypotheses, key=lambda h: (h.source.path, h.hypothesis_id))),
        hypotheses_note=None if hypotheses else hypotheses_note,
        baseline_validated=all(m.assessment == "validated" for m in members),
        limitations=tuple(limitations),
        failure_conditions=(),
        sources=tuple(unique[p] for p in sorted(unique)),
        entity_ids=(f"approach:{approach}",),
    )
    draft = ProblemStatement.model_construct(**fields, problem_sha256="0" * 64)
    fields["statement"], fields["failure_conditions"] = statement_of(draft), conditions_of(draft)
    draft = ProblemStatement.model_construct(**fields, problem_sha256="0" * 64)
    return ProblemStatement(**fields, problem_sha256=draft.digest())
