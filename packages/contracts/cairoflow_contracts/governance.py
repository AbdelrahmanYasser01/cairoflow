"""Contract governance (ADR-033): schema snapshots and a classifier for changes between them.

Every shared model lives in `cairoflow_contracts` and is cited by hash somewhere: a world model by
the SHA-256 of its JSON, a bundle by an id derived from its JSON, a report by an identity over its
artifacts. A change to a shared model therefore has two costs: code that reads it may break, and a
serialisation that changes breaks every hash that cites an old record. Two safeguards exist:

1. Every public model's JSON Schema is snapshotted in `packages/contracts/schemas/`. The snapshot
   test fails on any difference, and `classify_changes` says which differences are additive (a new
   optional field, a wider enum or union, a looser bound, a new model) and which are breaking
   (anything removed, a field that became required, a changed type, a tighter bound). The snapshot
   is regenerated only on purpose, with `python scripts/contract_snapshot.py --update`, in the same
   change that records the reason (docs/DECISIONS.md) and, when breaking, the migration.
2. Hash stability is checked against real records. The snapshot is a JSON Schema in validation
   mode: it does NOT see field order, `model_serializer` wrappers (the "omit while unset" pattern
   that keeps old hashes), number formatting or custom validators. So
   tests/test_contract_governance.py also loads committed records through the current models and
   requires byte-identical JSON back, and pins the SHA-256 of fixed instances of the models whose
   hash is cited most.

Where the classifier errs, it errs towards "breaking": a change it cannot place is reported as
breaking and a person decides.
"""

from __future__ import annotations

import importlib
import inspect
import json
import pkgutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

CONTRACT_MODULES = (
    "analysis_record",
    "cv_diagnosis",
    "decision_summary",
    "evidence",
    "experiment",
    "integrity",
    "meta_depth",
    "pair_identity",
    "pairing",
    "problem_statement",
    "provenance",
    "provenance_chain",
    "reasoning",
    "report",
    "worldview",
)
NOT_SCHEMA_MODULES = frozenset({"config", "governance"})  # no shared record model of their own
SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "schemas"
Level = Literal["additive", "breaking"]
_COSMETIC = frozenset({"title", "description", "examples", "$comment"})
_LOOSEN_ON_REMOVE = frozenset({"pattern", "format", "enum"})  # no pattern or enum: anything goes
_LOWER_COUNTS = ("minLength", "minItems", "minProperties")
_UPPER_COUNTS = ("maxLength", "maxItems", "maxProperties")
_BOUND_KEYS = frozenset(
    {"minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum", *_LOWER_COUNTS, *_UPPER_COUNTS}
)


@dataclass(frozen=True)
class SchemaChange:
    level: Level
    where: str
    detail: str

    def __str__(self) -> str:
        return f"{self.level}: {self.where} {self.detail}"


def module_models(module: str) -> dict[str, type[BaseModel]]:
    """The models a contract module defines itself (not the ones it imports), by name."""
    loaded = importlib.import_module(f"cairoflow_contracts.{module}")
    found = {
        name: cls
        for name, cls in vars(loaded).items()
        if inspect.isclass(cls) and issubclass(cls, BaseModel) and cls.__module__ == loaded.__name__
    }
    return dict(sorted(found.items()))


def ungoverned_modules() -> list[str]:
    """Modules of the package that define models but are not in CONTRACT_MODULES: a new contract
    module added without a snapshot would otherwise change silently."""
    package = importlib.import_module("cairoflow_contracts")
    names = [m.name for m in pkgutil.iter_modules(package.__path__)]
    return sorted(
        name
        for name in names
        if name not in CONTRACT_MODULES and name not in NOT_SCHEMA_MODULES and module_models(name)
    )


def module_schemas(module: str) -> dict[str, Any]:
    return {name: cls.model_json_schema() for name, cls in module_models(module).items()}


def snapshot_text(schemas: dict[str, Any]) -> str:
    return json.dumps(schemas, indent=1, sort_keys=True) + "\n"


# --- the classifier ----------------------------------------------------------------------------


def _normal(node: dict) -> dict:
    """A `const` is a one-value `enum`, so widening one to the other compares like an enum."""
    if "const" in node and "enum" not in node:
        return {**{k: v for k, v in node.items() if k != "const"}, "enum": [node["const"]]}
    return node


def _wrap(node: dict) -> dict:
    """A plain schema as a one-branch union, to compare with a schema that gained a branch."""
    keep = _COSMETIC | {"default"}
    branch = {k: v for k, v in node.items() if k not in keep}
    return {**{k: v for k, v in node.items() if k in keep}, "anyOf": [branch]}


def _bound(node: dict, exclusive: str, inclusive: str) -> tuple[Any, bool] | None:
    if exclusive in node:
        return node[exclusive], True
    return (node[inclusive], False) if inclusive in node else None


def _diff_bounds(old: dict, new: dict, where: str, out: list[SchemaChange]) -> None:
    """Looser bounds are additive, tighter ones breaking, whichever key states them."""
    for exclusive, inclusive, lower in (
        ("exclusiveMinimum", "minimum", True),
        ("exclusiveMaximum", "maximum", False),
    ):
        before, after = _bound(old, exclusive, inclusive), _bound(new, exclusive, inclusive)
        if before == after:
            continue
        if before is None:
            loose = False
        elif after is None:
            loose = True
        else:
            (a, a_open), (b, b_open) = before, after
            loose = (b < a if lower else b > a) or (b == a and a_open and not b_open)
        verb = "loosened" if loose else "tightened"
        out.append(SchemaChange("additive" if loose else "breaking", f"{where}.{inclusive}", verb))
    for keys, lower in ((_LOWER_COUNTS, True), (_UPPER_COUNTS, False)):
        for key in keys:
            before, after = old.get(key), new.get(key)
            if before == after:
                continue
            loose = after is None or (
                before is not None and (after < before if lower else after > before)
            )
            out.append(
                SchemaChange(
                    "additive" if loose else "breaking",
                    f"{where}.{key}",
                    "loosened" if loose else "tightened",
                )  # fmt: skip
            )


def _diff_required(old: dict, new: dict, where: str, out: list[SchemaChange]) -> None:
    before, after = set(old.get("required", ())), set(new.get("required", ()))
    path = f"{where}.required"
    out += [SchemaChange("breaking", path, f"{n} became required") for n in sorted(after - before)]
    out += [
        SchemaChange("additive", path, f"{n} is no longer required") for n in sorted(before - after)
    ]


def _diff(old: Any, new: Any, where: str, out: list[SchemaChange]) -> None:
    if old == new:
        return
    if not (isinstance(old, dict) and isinstance(new, dict)):
        out.append(SchemaChange("breaking", where, f"changed from {old!r} to {new!r}"))
        return
    old, new = _normal(old), _normal(new)
    if ("anyOf" in old) != ("anyOf" in new):
        old, new = (_wrap(old), new) if "anyOf" in new else (old, _wrap(new))
    _diff_required(old, new, where, out)
    _diff_bounds(old, new, where, out)
    for key in sorted(old.keys() | new.keys()):
        if key in _COSMETIC or key in _BOUND_KEYS or key == "required":
            continue
        path = f"{where}.{key}" if where else key
        if key not in new:
            level: Level = "additive" if key in _LOOSEN_ON_REMOVE else "breaking"
            out.append(SchemaChange(level, path, "removed"))
        elif key not in old:
            out.append(SchemaChange("additive" if key == "default" else "breaking", path, "added"))
        elif old[key] != new[key]:
            _diff_key(key, old, new, path, out)


def _diff_key(key: str, old: dict, new: dict, path: str, out: list[SchemaChange]) -> None:
    before, after = old[key], new[key]
    if key in ("properties", "$defs"):
        required = set(new.get("required", ()))
        for name in sorted(before.keys() | after.keys()):
            if name not in after:
                out.append(SchemaChange("breaking", f"{path}.{name}", "removed"))
            elif name not in before:
                level: Level = "breaking" if name in required else "additive"
                out.append(SchemaChange(level, f"{path}.{name}", "added"))
            else:
                _diff(before[name], after[name], f"{path}.{name}", out)
    elif key == "enum":
        out += [
            SchemaChange("breaking", path, f"value {v!r} removed") for v in before if v not in after
        ]
        out += [
            SchemaChange("additive", path, f"value {v!r} added") for v in after if v not in before
        ]
    elif key in ("anyOf", "oneOf"):
        kept = all(branch in after for branch in before)
        level = "additive" if kept else "breaking"
        out.append(SchemaChange(level, path, "a branch was added" if kept else "a branch changed"))
    elif isinstance(before, dict) and isinstance(after, dict):
        _diff(before, after, path, out)
    else:
        out.append(SchemaChange("breaking", path, f"changed from {before!r} to {after!r}"))


def classify_changes(
    old: dict[str, Any], new: dict[str, Any], where: str = ""
) -> list[SchemaChange]:
    """Every difference between two module snapshots (model name -> JSON Schema)."""
    out: list[SchemaChange] = []
    for name in sorted(old.keys() | new.keys()):
        path = f"{where}{name}"
        if name not in new:
            out.append(SchemaChange("breaking", path, "model removed"))
        elif name not in old:
            out.append(SchemaChange("additive", path, "model added"))
        else:
            _diff(old[name], new[name], path, out)
    return out


# --- snapshots ---------------------------------------------------------------------------------


def snapshot_path(module: str, directory: Path = SNAPSHOT_DIR) -> Path:
    return directory / f"{module}.json"


def check_snapshots(
    modules: Iterable[str] = CONTRACT_MODULES, directory: Path = SNAPSHOT_DIR
) -> list[SchemaChange]:
    """Differences between the committed snapshots and the models as they are now."""
    out: list[SchemaChange] = []
    for module in modules:
        path = snapshot_path(module, directory)
        current = module_schemas(module)
        if not path.is_file():
            out.append(SchemaChange("breaking", module, "has no committed snapshot"))
            continue
        text = path.read_text(encoding="utf-8")
        changes = classify_changes(json.loads(text), current, f"{module}.")
        if not changes and text != snapshot_text(current):
            changes = [SchemaChange("additive", module, "snapshot text differs (formatting only)")]
        out += changes
    return out


def write_snapshots(
    modules: Iterable[str] = CONTRACT_MODULES, directory: Path = SNAPSHOT_DIR
) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for module in modules:
        path = snapshot_path(module, directory)
        # LF whatever the platform, so a snapshot written on Windows does not differ on Linux
        path.write_text(snapshot_text(module_schemas(module)), encoding="utf-8", newline="\n")
        written.append(path)
    return written
