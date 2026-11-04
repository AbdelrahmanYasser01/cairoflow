"""Cumulative ledger for paid Meta calls on this project.

`--spend-limit` caps one process only; this caps the project across every process. Each paid call
appends one line before its result is used, and `remaining_usd` is what a script may pass on.

    uv run --frozen python scripts/meta_spend.py        # prints spent and remaining
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "experiments/records/meta-spend-20261104.jsonl"
CAP_USD = 3.00  # the spend cap, ADR-035


def spent_usd(ledger: Path = LEDGER) -> float:
    if not ledger.is_file():
        return 0.0
    lines = ledger.read_text("utf-8").splitlines()
    return round(sum(json.loads(line)["usd"] for line in lines if line.strip()), 6)


def remaining_usd(ledger: Path = LEDGER, cap_usd: float = CAP_USD) -> float:
    return max(0.0, round(cap_usd - spent_usd(ledger), 6))


def record(kind: str, usd: float, request_key: str, note: str, ledger: Path = LEDGER) -> None:
    """Append one paid call. `usd` is the estimate from published prices."""
    entry = {
        "at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "kind": kind,
        "usd": round(usd, 6),
        "request_key": request_key,
        "note": note,
    }
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    print(f"spent ${spent_usd():.4f} of ${CAP_USD:.2f}; remaining ${remaining_usd():.4f}")
