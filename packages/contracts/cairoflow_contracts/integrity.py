"""Experiment identity and integrity verdicts (schema version 0.1.0).

An `ExperimentIdentity` binds what defines one experiment to what it produced: the run settings
and seeds, the simulator build, the baseline and candidate networks and per-seed demand, every
input the decision report was assembled from, every SUMO output file, the evaluation and the
report. Each artifact is listed with its SHA-256 (SUMO XML with its comments stripped, because
SUMO writes a generation timestamp there). `identity_sha256` hashes the canonical JSON of all the
other fields, so changing any recorded hash, setting or limitation changes the identity; a
committed record names the identity, and Git holds that name.

The verifier (cairoflow_evaluation.integrity) trusts none of it: it re-hashes every artifact from
disk, recomputes the evaluation from the SUMO outputs and assembles the report again. Its verdict
is `verified` only when every check ran and passed; a missing artifact makes it `unverifiable`,
never `verified`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from cairoflow_contracts.evidence import EvidenceModel, _check_sha256
from cairoflow_contracts.experiment import RunSettings

IDENTITY_VERSION = "0.1.0"
ArtifactRole = Literal["config", "input", "simulator_output", "metrics", "report"]


def canonical_json(value: Any) -> str:
    """The one serialisation identities are hashed over: sorted keys, no spaces, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ArtifactHash(EvidenceModel):
    """One file the experiment is defined by or produced."""

    name: str = Field(min_length=1, description="e.g. 'bundle', 'net:I1', 'tripinfo:baseline:3'")
    path: str = Field(min_length=1, description="POSIX path relative to the run directory")
    role: ArtifactRole
    sha256: str
    comments_stripped: bool = Field(False, description="hash of the XML with comments removed")

    @field_validator("sha256")
    @classmethod
    def _hash(cls, value: str) -> str:
        return _check_sha256(value)


class ExperimentIdentity(EvidenceModel):
    """What one experiment is: written next to its report as integrity.json."""

    identity_version: Literal["0.1.0"] = IDENTITY_VERSION
    experiment_id: str = Field(min_length=1)
    report_id: str = Field(min_length=1)
    settings: RunSettings
    simulator: dict[str, str] = Field(description="the sumo binary's --version line, the wheel")
    extra_limitations: tuple[str, ...] = Field(
        description="limitations the producer added to the report; the report is rebuilt with them"
    )
    data_sources: tuple[str, ...] = Field(
        (), description="the report's data sources, which it is rebuilt with too"
    )
    artifacts: tuple[ArtifactHash, ...] = Field(min_length=1)
    identity_sha256: str

    @staticmethod
    def digest_of(fields: dict[str, Any]) -> str:
        """identity_sha256 of the other fields, in their JSON form."""
        return canonical_sha256({k: v for k, v in fields.items() if k != "identity_sha256"})

    @model_validator(mode="after")
    def _consistent(self) -> ExperimentIdentity:
        names = [a.name for a in self.artifacts]
        if len(set(names)) != len(names):
            raise ValueError("artifact names must be unique")
        if self.identity_sha256 != self.digest_of(self.model_dump(mode="json")):
            raise ValueError("identity_sha256 does not hash the identity's content")
        return self


class IntegrityCheck(EvidenceModel):
    name: str
    passed: bool
    detail: str


class IntegrityVerdict(EvidenceModel):
    """The verifier's finding about one run directory."""

    status: Literal["verified", "failed", "unverifiable"]
    run_dir: str
    identity_sha256: str | None
    checked_at_utc: datetime
    checks: tuple[IntegrityCheck, ...]
    missing: tuple[str, ...] = Field(description="artifacts the verification needed and lacked")
    problems: tuple[str, ...]

    @model_validator(mode="after")
    def _status(self) -> IntegrityVerdict:
        if self.status == "verified" and (self.missing or self.problems or not self.checks):
            raise ValueError("a verified verdict has checks, and no problems or missing files")
        if self.status == "verified" and not all(c.passed for c in self.checks):
            raise ValueError("a verified verdict has no failed check")
        return self
