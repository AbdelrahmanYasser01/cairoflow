"""Compare the contract schemas with their committed snapshots, or regenerate them on purpose.

    python scripts/contract_snapshot.py                          exit 1 and list every difference
    python scripts/contract_snapshot.py --update                 rewrite packages/contracts/schemas
    python scripts/contract_snapshot.py --update --accept-breaking   the same, over breaking changes

Run --update only in the change that records why the contract moved (docs/DECISIONS.md), and read
the listing first: a "breaking" line needs a migration for the records that cite the model, and
--update refuses to write over one unless --accept-breaking says a person has read it.
"""

from __future__ import annotations

import argparse
import sys

from cairoflow_contracts.governance import check_snapshots, ungoverned_modules, write_snapshots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="rewrite the snapshots")
    parser.add_argument("--accept-breaking", action="store_true", help="allow --update over breaks")
    args = parser.parse_args(argv)
    changes = check_snapshots()
    for change in changes:
        print(change)
    for module in ungoverned_modules():
        print(f"breaking: {module} defines models but is not in CONTRACT_MODULES")
    if args.update:
        breaking = [
            c for c in changes if c.level == "breaking" and "no committed snapshot" not in c.detail
        ]
        if breaking and not args.accept_breaking:
            print(
                f"{len(breaking)} breaking change(s); read them, then --accept-breaking",
                file=sys.stderr,
            )
            return 2
        for path in write_snapshots():
            print(f"wrote {path}")
        return 0
    if changes or ungoverned_modules():
        print(
            "contract schemas differ from their snapshots; regenerate on purpose with --update",
            file=sys.stderr,
        )
        return 1
    print("contract schemas match their snapshots")
    return 0


if __name__ == "__main__":
    sys.exit(main())
