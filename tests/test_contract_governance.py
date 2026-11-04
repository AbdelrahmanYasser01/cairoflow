"""Contract governance (ADR-033): schema snapshots, the change classifier and hash stability.

The snapshot test is the tripwire: any change to a shared model's JSON Schema fails it until the
snapshot is regenerated on purpose (scripts/contract_snapshot.py --update). The snapshot cannot
see field order, serializers or number formatting, so the record tests are the other half:
committed records, written by earlier versions of the models, must still load and serialise to
the same bytes, and fixed instances of the most-cited models must keep their SHA-256, because
hashes of that JSON are cited elsewhere.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field, create_model
from synthetic_scenario import world_model
from test_worldview import CAMERA_LAYER, GEO, network

from cairoflow_contracts.evidence import (
    CameraLayer,
    EvidenceModel,
    IntersectionGeometry,
    SignalTimeline,
)
from cairoflow_contracts.governance import (
    CONTRACT_MODULES,
    SNAPSHOT_DIR,
    SchemaChange,
    check_snapshots,
    classify_changes,
    module_models,
    ungoverned_modules,
)
from cairoflow_contracts.reasoning import EvidenceBundle
from cairoflow_contracts.report import BaselineAssessment
from cairoflow_reasoning.evidence import build_evidence_bundle

REPO = Path(__file__).resolve().parents[1]


def test_the_committed_schemas_are_the_current_ones() -> None:
    changes = check_snapshots()
    assert not changes, (
        "a shared contract changed:\n  "
        + "\n  ".join(map(str, changes))
        + "\nIf that is intended, record why in docs/DECISIONS.md, give a migration for every "
        "'breaking' line, then run: python scripts/contract_snapshot.py --update"
    )


def test_every_contract_module_has_a_snapshot_and_every_model_is_an_evidence_model() -> None:
    assert ungoverned_modules() == []  # a new contract module must join CONTRACT_MODULES
    for module in CONTRACT_MODULES:
        assert (SNAPSHOT_DIR / f"{module}.json").is_file(), module
        for name, model in module_models(module).items():
            assert issubclass(model, EvidenceModel), f"{module}.{name}: frozen, closed, versioned"
            assert model.model_config["extra"] == "forbid" and model.model_config["frozen"]


def test_snapshots_are_written_with_unix_newlines() -> None:
    for module in CONTRACT_MODULES:
        assert b"\r" not in (SNAPSHOT_DIR / f"{module}.json").read_bytes()


# --- the classifier on hand-written schemas ----------------------------------------------------


def schema(properties: dict, required: tuple[str, ...] = ()) -> dict:
    """What pydantic writes: `required` is left out when no field is required."""
    found = {"properties": properties, "type": "object", "additionalProperties": False}
    return {**found, "required": list(required)} if required else found


def levels(old: dict, new: dict) -> set[str]:
    return {c.level for c in classify_changes({"M": old}, {"M": new})}


BASE = schema({"a": {"type": "integer"}, "b": {"type": "string"}}, ("a",))


def test_no_difference_is_no_change() -> None:
    assert classify_changes({"M": BASE}, {"M": BASE}) == []


def test_a_new_optional_field_is_additive() -> None:
    new = schema({**BASE["properties"], "c": {"type": "number"}}, ("a",))
    assert classify_changes({"M": BASE}, {"M": new}) == [
        SchemaChange("additive", "M.properties.c", "added")
    ]


def test_a_new_required_field_is_breaking() -> None:
    new = schema({**BASE["properties"], "c": {"type": "number"}}, ("a", "c"))
    assert levels(BASE, new) == {"breaking"}


@pytest.mark.parametrize(
    "new",
    [
        schema({"a": {"type": "integer"}}, ("a",)),  # a field removed
        schema({"a": {"type": "number"}, "b": {"type": "string"}}, ("a",)),  # a type changed
        schema({"a": {"type": "integer"}, "b": {"type": "string"}}, ("a", "b")),  # now required
        schema(
            {"a": {"type": "integer", "minimum": 0}, "b": {"type": "string"}}, ("a",)
        ),  # tighter
        {**BASE, "additionalProperties": True},  # the model opened up
    ],
)
def test_removing_retyping_requiring_or_tightening_is_breaking(new: dict) -> None:
    assert "breaking" in levels(BASE, new)


def test_wording_is_not_a_change() -> None:
    reworded = schema({"a": {"type": "integer", "description": "new words", "title": "A"},
                       "b": {"type": "string"}}, ("a",))  # fmt: skip
    assert classify_changes({"M": BASE}, {"M": reworded}) == []


def test_models_come_and_go() -> None:
    assert classify_changes({}, {"N": BASE}) == [SchemaChange("additive", "N", "model added")]
    assert classify_changes({"N": BASE}, {}) == [SchemaChange("breaking", "N", "model removed")]


# --- the classifier on what pydantic really writes ---------------------------------------------


class Foo(BaseModel):
    n: int


def toy(**fields) -> dict:
    return create_model("M", __base__=BaseModel, **fields).model_json_schema()


NULL, ZERO = None, 0
LOOSER = [
    ("int to optional int", dict(x=(int, ...)), dict(x=(int | None, ...))),
    ("str to str or int", dict(x=(str, ...)), dict(x=(str | int, ...))),
    ("a model to an optional model", dict(x=(Foo, ...)), dict(x=(Foo | None, ...))),
    ("literal widened", dict(x=(Literal["a"], ...)), dict(x=(Literal["a", "b"], ...))),
    ("literal to enum", dict(x=(Literal["a", "b"], ...)), dict(x=(Literal["a", "b", "c"], ...))),
    ("lower bound lowered", dict(x=(Annotated[int, Field(ge=0)], ...)),
     dict(x=(Annotated[int, Field(ge=-5)], ...))),
    ("gt to ge", dict(x=(Annotated[int, Field(gt=0)], ...)),
     dict(x=(Annotated[int, Field(ge=0)], ...))),
    ("upper bound raised", dict(x=(Annotated[int, Field(le=10)], ...)),
     dict(x=(Annotated[int, Field(le=20)], ...))),
    ("lower bound dropped", dict(x=(Annotated[int, Field(ge=0)], ...)), dict(x=(int, ...))),
    ("min length dropped", dict(x=(Annotated[str, Field(min_length=1)], ...)), dict(x=(str, ...))),
    ("pattern dropped", dict(x=(Annotated[str, Field(pattern="^a")], ...)), dict(x=(str, ...))),
    ("last required field made optional", dict(x=(int, ...)), dict(x=(int, 0))),
    ("optional field added", dict(x=(int, ...)), dict(x=(int, ...), y=(int, 0))),
]  # fmt: skip
STRICTER = [
    ("optional int to int", dict(x=(int | None, ...)), dict(x=(int, ...))),
    ("literal narrowed", dict(x=(Literal["a", "b"], ...)), dict(x=(Literal["a"], ...))),
    ("lower bound raised", dict(x=(Annotated[int, Field(ge=0)], ...)),
     dict(x=(Annotated[int, Field(ge=1)], ...))),
    ("bound added", dict(x=(int, ...)), dict(x=(Annotated[int, Field(ge=0)], ...))),
    ("upper bound lowered", dict(x=(Annotated[int, Field(le=20)], ...)),
     dict(x=(Annotated[int, Field(le=10)], ...))),
    ("max length added", dict(x=(str, ...)), dict(x=(Annotated[str, Field(max_length=5)], ...))),
    ("pattern added", dict(x=(str, ...)), dict(x=(Annotated[str, Field(pattern="^a")], ...))),
    ("type changed", dict(x=(str, ...)), dict(x=(int, ...))),
    ("field removed", dict(x=(int, ...), y=(int, 0)), dict(x=(int, ...))),
    ("required field added", dict(x=(int, ...)), dict(x=(int, ...), y=(int, ...))),
    ("optional made required", dict(x=(int, 0)), dict(x=(int, ...))),
]  # fmt: skip


@pytest.mark.parametrize("name, old, new", LOOSER, ids=[c[0] for c in LOOSER])
def test_widening_changes_in_real_pydantic_schemas_are_additive(name, old, new) -> None:
    assert levels(toy(**old), toy(**new)) == {"additive"}, name


@pytest.mark.parametrize("name, old, new", STRICTER, ids=[c[0] for c in STRICTER])
def test_tightening_changes_in_real_pydantic_schemas_are_breaking(name, old, new) -> None:
    assert "breaking" in levels(toy(**old), toy(**new)), name


# --- snapshots ---------------------------------------------------------------------------------


def test_a_missing_snapshot_is_reported_not_ignored(tmp_path) -> None:
    changes = check_snapshots(("pairing",), directory=tmp_path)
    assert changes == [SchemaChange("breaking", "pairing", "has no committed snapshot")]


def test_a_model_that_moved_since_its_snapshot_fails_the_check(tmp_path) -> None:
    """Take the committed evidence snapshot as it would have been before Provenance gained
    `produced_by` as a required field: the current model then has a new required field."""
    committed = json.loads((SNAPSHOT_DIR / "evidence.json").read_text(encoding="utf-8"))
    provenance = committed["Provenance"]
    del provenance["properties"]["produced_by"]
    provenance["required"].remove("produced_by")
    (tmp_path / "evidence.json").write_text(json.dumps(committed), encoding="utf-8")
    changes = check_snapshots(("evidence",), directory=tmp_path)
    assert (
        SchemaChange("breaking", "evidence.Provenance.required", "produced_by became required")
        in changes
    )


def _script():
    spec = importlib.util.spec_from_file_location(
        "contract_snapshot", REPO / "scripts/contract_snapshot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_update_refuses_to_write_over_a_breaking_change_unless_it_is_accepted(monkeypatch) -> None:
    script = _script()
    written = []
    monkeypatch.setattr(
        script, "check_snapshots", lambda: [SchemaChange("breaking", "m.M", "model removed")]
    )
    monkeypatch.setattr(script, "write_snapshots", lambda: written.append("x") or [])
    assert script.main(["--update"]) == 2 and written == []
    assert script.main(["--update", "--accept-breaking"]) == 0 and written == ["x"]
    assert script.main([]) == 1  # plain check mode fails on any difference


def test_a_first_snapshot_for_a_new_module_does_not_need_acceptance(monkeypatch) -> None:
    script = _script()
    new = [SchemaChange("breaking", "newmod", "has no committed snapshot")]
    monkeypatch.setattr(script, "check_snapshots", lambda: new)
    monkeypatch.setattr(script, "write_snapshots", lambda: [])
    assert script.main(["--update"]) == 0


# --- hash stability against committed records and fixed instances ------------------------------

GEOMETRY_DIR = REPO / "data/geometry/ngsim-lankershim-int2"


def same_bytes(model, path: Path) -> bool:
    """The model serialises to exactly the JSON the committed file holds: same keys in the same
    order, same numbers, compared as compact text (indentation is the file's, not the model's)."""
    text = path.read_text(encoding="utf-8")
    compact = json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)
    return model.model_dump_json() == compact


def test_committed_geometry_and_timelines_serialise_as_they_were_written() -> None:
    geometry = GEOMETRY_DIR / "geometry.json"
    timelines = sorted(GEOMETRY_DIR.glob("signal_timeline*.json"))
    assert geometry.is_file() and len(timelines) >= 4
    parsed = IntersectionGeometry.model_validate_json(geometry.read_text(encoding="utf-8"))
    assert same_bytes(parsed, geometry)
    for path in timelines:
        assert same_bytes(SignalTimeline.model_validate_json(path.read_text("utf-8")), path), path


def test_committed_baseline_assessments_serialise_as_they_were_written() -> None:
    paths = [GEOMETRY_DIR / "baseline_assessment.json",
             *sorted((GEOMETRY_DIR / "replay_assessments").glob("*.json"))]  # fmt: skip
    assert len(paths) >= 2
    for path in paths:
        assessment = BaselineAssessment.model_validate_json(path.read_text(encoding="utf-8"))
        assert same_bytes(assessment, path), path


def test_the_committed_camera_layer_serialises_as_it_was_written() -> None:
    layer = CameraLayer.model_validate_json(CAMERA_LAYER.read_text(encoding="utf-8"))
    assert same_bytes(layer, CAMERA_LAYER)


def test_the_committed_muse_bundle_keeps_its_id() -> None:
    """EvidenceBundle.bundle_id is the first 16 hex digits of the SHA-256 of the bundle's JSON
    without the id (reasoning/evidence.py). A schema change that moved this would orphan every
    cached reply keyed on the bundle."""
    path = REPO / "cache/muse/ddfb8029b1e8810e65b2ad986e0da99be630b86e6d7c86669007e2a197ef47ad.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    bundle = EvidenceBundle.model_validate_json(
        record["body"]["input"].partition("\nEVIDENCE (JSON):\n")[2]
    )
    rebuilt = hashlib.sha256(bundle.model_dump_json(exclude={"bundle_id"}).encode()).hexdigest()
    assert bundle.bundle_id == rebuilt[:16]
    assert bundle.bundle_id.startswith("94d8ba7f")  # the id recorded when the reply was cached


GOLDEN = {  # SHA-256 of model_dump_json() of fixed synthetic instances (tests/synthetic_*.py)
    "world model": "73e601bfa0f12ee504950ce66cacdb2f582c55ad6fef025f7dbdb49271b07e91",
    "world model with a camera layer": (
        "58e2625b51f82aa469fe5d662182db831778fbdc8bfb23a1e45cc1e732d11a9a"
    ),
    "compiled network manifest": (
        "6636dd775e89be2e4858c5714ba508b57b8587b986ddbbc2fb011a57013c7f8f"
    ),
}


def test_fixed_instances_of_the_most_cited_models_keep_their_hash() -> None:
    """The world model is cited by the hash of its JSON in bundles, demand manifests and reports.
    These pin that JSON, field order and the 'omit while unset' camera field included: a model
    change that moves it is a break even when the JSON Schema looks harmless."""

    def sha(model) -> str:
        return hashlib.sha256(model.model_dump_json().encode("utf-8")).hexdigest()

    model = world_model(GEO)
    assert "camera" not in model.model_dump_json()  # omitted while unset, so old hashes hold
    layer = CameraLayer.model_validate_json(CAMERA_LAYER.read_text(encoding="utf-8"))
    assert sha(model) == GOLDEN["world model"]
    assert (
        sha(model.model_copy(update={"camera": layer})) == GOLDEN["world model with a camera layer"]
    )
    assert sha(network()) == GOLDEN["compiled network manifest"]
    bundle = build_evidence_bundle(model, network())
    assert (
        bundle.bundle_id
        == hashlib.sha256(bundle.model_dump_json(exclude={"bundle_id"}).encode()).hexdigest()[:16]
    )
