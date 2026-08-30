#!/usr/bin/env python3
# @featuretrace:finance-postgres-read-cutover — retire mis-scoped shadow diffs.
# Layer: script
# Data flow: core.shadow_diffs -> scope/fixture classification -> resolved=true (building-scoped).
# Related: backend/services/finance_shadow_read_service.py (the guard that prevents recurrence)
#          backend/services/cutover_status_service.py (record_shadow_diff's test-data backstop)
# Tests: tests/backend/test_finance_shadow_scope_guard.py
"""Resolve shadow diffs that compare two different populations, not two stores.

DRY-RUN BY DEFAULT. `--apply` sets `resolved=true` with an explanatory note. It never
deletes a row and never touches a diff it cannot classify.

The evidence (measured live 2026-08-29, building 13195)
-------------------------------------------------------
260 unresolved `finance_ledger` diffs blocked the finance read gate. They were not
telling us anything about the data:

* `finance.unit_levy_ledger` carried `unit_count {pg: 87, mongo: 1}` beside
  `total_paid {pg: 21214626, mongo: 352300}` — a one-unit payload against a
  whole-building aggregate.
* `finance.transactions` carried `total_expense {pg: 14565265, mongo: 12345}`.
  `12345` cents is a fixture constant from tests/backend/test_finance_shadow_read_service.py.
* Measured directly the same day the two stores AGREE to the cent on that route:
  Mongo FY2026 is 87 units / $220,187.56 levied / $212,146.26 paid; Postgres is
  22018756 / 21214626 cents over 87 lots. `run_finance_dr_sync` independently reported
  87/87 lots matching with a $0.00 net gap.

Both causes are now fixed at source — `population_scope_conflict` refuses a mis-scoped
comparison, `_compare_transactions_payloads` honours `_dimension`, and
`record_shadow_diff` flags writes made under pytest — so this backlog cannot rebuild.
This script clears the rows those defects already wrote.

A diff is classified STALE only when it carries positive proof of mis-scoping. Anything
else is left untouched and reported, because a diff nobody can explain is exactly the
kind that must not be swept away.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import asyncpg  # noqa: E402

BYPASS = "00000000-0000-0000-0000-000000000000"

# Values proven to be test fixtures by appearing as literals in the shadow-read test
# module. This list is deliberately TINY and is NOT a general "looks like a round
# number" heuristic.
#
# An earlier version of this script also listed 250000, 100000, 550000 and 352300 —
# $2,500.00, $1,000.00, $5,500.00 and $3,523.00. Every one of those is a perfectly
# plausible real arrears or payment figure. Auto-resolving a diff because its Mongo
# side happened to equal a round number would have retired genuine divergence, which is
# the same class of mistake as the 2026-08-28 receipt-retirement that had to be rolled
# back. Removed.
FIXTURE_CENTS = {12345}

NOTE = (
    "Resolved 2026-08-29: STRUCTURALLY mis-scoped comparison — the two sides did not "
    "describe the same population or the same dimension, so the recorded difference was "
    "never a statement about the data. Corroboration: on the unit ledger the two stores "
    "agree exactly (87 units, $220,187.56 levied / $212,146.26 paid, verified by live "
    "query against BOTH stores), and run_finance_dr_sync reports 87/87 lots matching "
    "with a $0.00 net gap. NOTE this does NOT mean the stores agree everywhere: arrears "
    "still differs by one lot / $190.00 (PG 14 units/$8,041.30 vs Mongo 13/$7,851.30), "
    "which is the known unbanked TH075 receipt and is NOT resolved by this script. "
    "Root causes fixed at source: population_scope_conflict + _dimension handling + "
    "record_shadow_diff pytest backstop."
)


def classify(route: str, mongo_value: dict | None) -> str | None:
    """Return a reason string only when the diff is STRUCTURALLY provably mis-scoped.

    "Structurally" is the whole point. Every rule here reads a property of the
    comparison's SHAPE — how many units it covered, how many dimensions it could have
    populated — never the plausibility of its money value. A rule that judged the
    number itself would eventually retire a real divergence, and the operator would
    have no way to notice: the row is marked resolved with a confident explanation
    attached.

    Anything not matched here is left open for a human, however obviously stale it
    looks. This script's job is to clear noise it can PROVE is noise, not to empty the
    table.
    """
    fields = (mongo_value or {}).get("fields") or {}
    if not fields:
        return None

    # 1. Explicit population mismatch recorded in the diff itself: the two sides
    #    covered a different number of units, so their totals were never comparable.
    entry = fields.get("unit_count")
    if isinstance(entry, dict) and entry.get("pg") != entry.get("mongo"):
        return (
            f"population mismatch: unit_count pg={entry.get('pg')} "
            f"mongo={entry.get('mongo')} — the totals covered different unit sets"
        )

    # 2. finance.transactions fires from two endpoints, each passing an empty list for
    #    the other dimension. A payload carrying exactly one dimension was compared
    #    against a whole-building total for a dimension it never measured.
    if route == "finance.transactions" and len(fields) == 1:
        return "single-dimension payload compared against a whole-building total"

    # 3. A Mongo side equal to a literal that appears in the shadow-read TEST module.
    #    Kept to the single unambiguous value; see FIXTURE_CENTS for why the rest of
    #    that list was removed.
    for name, entry in fields.items():
        if isinstance(entry, dict) and entry.get("mongo") in FIXTURE_CENTS:
            return f"fixture-valued mongo side on {name}: {entry.get('mongo')} (test literal)"

    return None


async def run(building_id: str, apply: bool) -> int:
    pg = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        await pg.execute(f"SET app.tenant_id = '{BYPASS}'")
        row = await pg.fetchrow(
            "SELECT tenant_id::text tid FROM core.schemes WHERE scheme_number = $1 AND is_test_data = FALSE",
            building_id,
        )
        if not row:
            raise SystemExit(f"No scheme for building_id={building_id!r}")
        # core.shadow_diffs is tenant-scoped with no bypass clause: without this the
        # next query returns zero rows and no error (footgun #8).
        await pg.execute(f"SET app.tenant_id = '{row['tid']}'")

        # `is_test_data = FALSE` mirrors get_route_shadow_readiness' own filter exactly.
        # Without it this script reports a different population than the gate it exists
        # to unblock — counting rows the gate already ignores, which reads as "the
        # backlog is bigger than it is" and makes the two numbers impossible to
        # reconcile. Rows written under pytest are flagged by record_shadow_diff and
        # belong to neither.
        diffs = await pg.fetch(
            """
            SELECT id::text AS id, route, diff_type, mongo_value, created_at
              FROM core.shadow_diffs
             WHERE domain = 'finance_ledger'
               AND resolved = FALSE
               AND is_test_data = FALSE
               AND building_id = $1
             ORDER BY created_at
            """,
            building_id,
        )

        test_flagged = await pg.fetchval(
            """
            SELECT count(*) FROM core.shadow_diffs
             WHERE domain = 'finance_ledger' AND resolved = FALSE
               AND is_test_data = TRUE AND building_id = $1
            """,
            building_id,
        )

        stale: list[tuple[str, str]] = []
        kept: list[asyncpg.Record] = []
        for d in diffs:
            mv = d["mongo_value"]
            if isinstance(mv, str):
                mv = json.loads(mv)
            reason = classify(d["route"], mv)
            if reason:
                stale.append((d["id"], reason))
            else:
                kept.append(d)

        print("=" * 76)
        print(f"Stale shadow-diff sweep — building {building_id}  "
              f"[{'APPLY' if apply else 'DRY-RUN'}]")
        print("=" * 76)
        print(f"  unresolved finance_ledger diffs : {len(diffs)}  (gate-visible; "
              f"{test_flagged} more are is_test_data and already ignored by the gate)")
        print(f"  provably mis-scoped (STALE)     : {len(stale)}")
        print(f"  NOT classified — left untouched : {len(kept)}")
        for d in kept[:15]:
            mv = d["mongo_value"]
            print(f"      KEEP {d['route']:<38} {str(mv)[:110]}")
        if len(kept) > 15:
            print(f"      ... and {len(kept) - 15} more kept")

        if not apply:
            print("\n  DRY-RUN — nothing written. Re-run with --apply to resolve the stale rows.")
            return 0

        await pg.executemany(
            """
            UPDATE core.shadow_diffs
               SET resolved = TRUE, resolved_at = now(), resolved_by = 'resolve_stale_shadow_diffs_20260829',
                   notes = $2
             WHERE id = CAST($1 AS UUID)
            """,
            [(diff_id, f"{NOTE} Classification: {reason}") for diff_id, reason in stale],
        )
        print(f"\n  resolved {len(stale)} stale diff(s); {len(kept)} left open for review.")
        return 0
    finally:
        await pg.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", default="13195")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.building_id, args.apply)))


if __name__ == "__main__":
    main()
