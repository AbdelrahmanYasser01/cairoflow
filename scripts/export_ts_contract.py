"""Write the JSON Schema the web app's TypeScript types are generated from.

    uv run --frozen python scripts/export_ts_contract.py
    (cd apps/web && npx --yes json-schema-to-typescript@15 -i src/lib/analysis/contract.schema.json \
        -o src/lib/analysis/contract.d.ts --unreachableDefinitions)
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic.json_schema import models_json_schema

from cairoflow_contracts.analysis_record import AnalysisRecord, TrackIndex

OUT = Path(__file__).resolve().parents[1] / "apps/web/src/lib/analysis/contract.schema.json"


def main() -> int:
    _, schema = models_json_schema(
        [(AnalysisRecord, "serialization"), (TrackIndex, "serialization")],
        ref_template="#/$defs/{model}",
    )
    schema["title"] = "CairoFlowContract"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(schema, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
