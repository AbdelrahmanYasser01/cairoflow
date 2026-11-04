"""Ask Muse Spark what a camera analysis might mean, a few independent times.

    uv run --frozen python scripts/muse_cv_diagnosis.py ANALYSIS_DIR --send --replicates 3
    uv run --frozen python scripts/muse_cv_diagnosis.py ANALYSIS_DIR --replay --replicates 3

Cache-first: a stored reply is never paid for twice. Refuses anything past the project cap. The
output is model-generated: hypotheses for a person to test, shown as that, and never used as a
measurement. Writes data/sample/diagnoses/<analysis_id>.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meta_spend  # noqa: E402

from cairoflow_contracts.config import load_settings  # noqa: E402
from cairoflow_contracts.cv_diagnosis import (  # noqa: E402
    CvDiagnosisRecord,
    CvDiagnosisReplicate,
)
from cairoflow_contracts.evidence import Provenance  # noqa: E402
from cairoflow_perception.analysis_store import RECORD_FILE, verify_analysis  # noqa: E402
from cairoflow_reasoning.cv_prompt import (  # noqa: E402
    CV_PROMPT_VERSION,
    build_cv_request_body,
    citation_problems,
    evidence_items,
    parse_cv_reply,
    unsupported_numbers,
    validate_cv_reply,
)
from cairoflow_reasoning.muse_adapter import (  # noqa: E402
    MuseAdapterError,
    MuseClient,
    request_key,
    upper_bound_cost_usd,
)

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "data/sample/diagnoses"
LABEL = "model-generated hypotheses; not a measurement"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("analysis", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--send", action="store_true")
    mode.add_argument("--replay", action="store_true")
    parser.add_argument("--replicates", type=int, default=3)
    args = parser.parse_args(argv)

    record = verify_analysis(args.analysis)
    if record.data_origin != "camera_cv":
        print("refused: this diagnoses camera analyses only", file=sys.stderr)
        return 2
    settings = load_settings(REPO / ".env")
    items = evidence_items(record)
    body = build_cv_request_body(record, items, settings.muse_model)
    key = request_key(body)
    bound = upper_bound_cost_usd(body)
    replicates: list[CvDiagnosisReplicate] = []
    for replicate in range(1, args.replicates + 1):
        allowance = 0.0
        if args.send:
            allowance = meta_spend.remaining_usd()
            if bound > allowance:
                print(f"stopped: ${bound:.4f} is past the project cap", file=sys.stderr)
                break
        client = MuseClient(
            settings,
            REPO / "cache/muse",
            "live" if args.send else "replay",
            spend_limit_usd=allowance,
            prompt_version=CV_PROMPT_VERSION,
            replicate=replicate,
        )
        try:
            reply, cached = client.fetch_body(body, f"cv:{record.analysis_id}", validate_cv_reply)
        except MuseAdapterError as error:
            if client.spent_usd:
                meta_spend.record("muse-cv", client.spent_usd, key, f"failed r{replicate}")
            replicates.append(
                CvDiagnosisReplicate(
                    replicate=replicate, status="failed", failure=str(error),
                    request_key=key, from_cache=False,
                )
            )  # fmt: skip
            continue
        if not cached:
            meta_spend.record("muse-cv", client.spent_usd, key, f"replicate {replicate}")
        diagnosis = parse_cv_reply(json.loads(reply.response_text))
        replicates.append(
            CvDiagnosisReplicate(
                replicate=replicate,
                status="ok",
                request_key=key,
                from_cache=cached,
                cost_usd=reply.estimated_cost_usd,
                citation_problems=citation_problems(diagnosis, items),
                unsupported_numbers=unsupported_numbers(diagnosis, items),
                diagnosis=diagnosis,
            )
        )

    now = datetime.now(UTC).replace(microsecond=0)
    digest = hashlib.sha256((args.analysis / RECORD_FILE).read_bytes()).hexdigest()
    result = CvDiagnosisRecord(
        label=LABEL,
        analysis_id=record.analysis_id,
        analysis_record_sha256=digest,
        model=settings.muse_model,
        prompt_version=CV_PROMPT_VERSION,
        created_at_utc=now,
        evidence=items,
        replicates=tuple(replicates),
        provenance=Provenance(
            kind="model_generated",
            produced_by=settings.muse_model,
            produced_at_utc=now,
            input_sha256=(digest, key),
        ),
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{record.analysis_id}.json"
    out.write_text(result.model_dump_json(indent=1) + "\n", encoding="utf-8")
    ok = sum(1 for r in replicates if r.status == "ok")
    print(f"{out}: {ok} of {len(replicates)} replies usable")
    print(f"spent ${meta_spend.spent_usd():.4f}, remaining ${meta_spend.remaining_usd():.4f}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
