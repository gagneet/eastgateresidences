#!/usr/bin/env python3
# @featuretrace:finance-postgres-read-cutover — move levy_categories into PostgreSQL.
# Layer: script
# Data flow: MongoDB levy_categories → finance.budget_categories (building-scoped).
# Related: backend/alembic/versions/0106_budget_categories.py
#          backend/services/financial_read_service.py
# Tests: tests/backend/test_budget_categories_backfill.py
"""Copy MongoDB `levy_categories` into `finance.budget_categories`.

DRY-RUN BY DEFAULT. `--apply` writes rows. Idempotent on `legacy_mongo_id`, so a re-run
updates rather than duplicating — there is no natural key that survives the archived
duplicates (109 of 322 rows are archived, and an archived row and its replacement share
scheme + year + fund + name).

WHAT IS AND IS NOT COPIED
-------------------------
`actual_amount` is NOT copied. The target table has no column for it, deliberately —
see the migration for why. The actual is derived at read time from
`finance.expense_transactions`, which already holds it at exactly this grain.

`budgeted_amount` is a dollar FLOAT in MongoDB. It is converted to integer cents HERE,
at the adapter boundary, exactly once, and nothing downstream re-derives it (CLAUDE.md
rule 9). A row with no `budgeted_amount` gets NULL, not 0 — a category can exist as an
actual-only line, and writing 0 would state a budget nobody set.

`fund_type` ("admin"/"sinking") is resolved to a real `finance.funds.fund_id`. A
category whose fund cannot be resolved is SKIPPED and reported, never given a
best-guess fund: putting a sinking-fund expense in the admin fund is silently wrong in
exactly the way nobody notices until a levy is set from it.

    python3 backend/scripts/data_migration/backfill_budget_categories.py --building-id 13195
    python3 backend/scripts/data_migration/backfill_budget_categories.py --building-id 13195 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import asyncpg  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

BYPASS = "00000000-0000-0000-0000-000000000000"

# MongoDB fund_type -> finance.funds.fund_type. Explicit rather than passed through, so
# an unrecognised value is a reported skip instead of a silent FK failure.
FUND_TYPE_MAP = {
    "admin": "admin",
    "administrative": "admin",
    "sinking": "sinking",
    "capital_works": "sinking",
    "special": "special_purpose",
    "special_purpose": "special_purpose",
}


def _to_cents(amount) -> int | None:
    """Dollars -> integer cents at the boundary, exactly once.

    None (not 0) when the source has no budget: a category with no budgeted_amount is a
    line nobody budgeted, which is different from a line budgeted at zero.
    """
    if amount is None or amount == "":
        return None
    return int(round(float(amount) * 100))


async def run(building_id: str, apply: bool) -> int:
    mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = mongo[os.environ.get("DB_NAME", "strataos_production")]
    pg = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        # `SELECT set_config(...)` with a BOUND parameter, not an f-string `SET`.
        # asyncpg cannot bind a parameter to `SET`, which is why the f-string form is
        # widespread in this repo — but set_config() is a normal function call and takes
        # one. The value here is a UUID read from core.schemes, so the f-string was not
        # exploitable; the point is that it is a pattern which stops being safe the
        # moment its source changes to anything a user can influence, and there is no
        # reason to keep it when a parameterised form exists. tests/backend/conftest.py
        # already uses exactly this.
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", BYPASS)
        scheme = await pg.fetchrow(
            "SELECT scheme_id::text sid, tenant_id::text tid FROM core.schemes "
            "WHERE scheme_number = $1 AND is_test_data = FALSE",
            building_id,
        )
        if not scheme:
            raise SystemExit(f"No scheme for building_id={building_id!r}")
        # finance.* has no RLS bypass: without the real tenant every query below returns
        # zero rows and no error, which reads exactly like "nothing to migrate".
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", scheme["tid"])

        funds = {
            r["fund_type"]: str(r["fund_id"])
            for r in await pg.fetch(
                "SELECT fund_id, fund_type FROM finance.funds WHERE scheme_id = $1::uuid",
                scheme["sid"],
            )
        }
        existing = {
            r["legacy_mongo_id"]
            for r in await pg.fetch(
                "SELECT legacy_mongo_id FROM finance.budget_categories WHERE scheme_id = $1::uuid",
                scheme["sid"],
            )
            if r["legacy_mongo_id"]
        }

        docs = await db.levy_categories.find({"building_id": building_id}, {"_id": 0}).to_list(5000)

        rows, skipped_fund, skipped_no_id = [], [], []
        for doc in docs:
            mongo_id = doc.get("id")
            if not mongo_id:
                skipped_no_id.append(doc.get("name"))
                continue
            fund_type = FUND_TYPE_MAP.get(str(doc.get("fund_type") or "").lower())
            fund_id = funds.get(fund_type) if fund_type else None
            if not fund_id:
                skipped_fund.append((doc.get("name"), doc.get("fund_type")))
                continue
            rows.append(
                (
                    scheme["tid"], scheme["sid"], fund_id,
                    str(doc.get("year") or ""),
                    doc.get("name") or "",
                    doc.get("canonical_key"),
                    doc.get("canonical_name"),
                    _to_cents(doc.get("budgeted_amount")),
                    doc.get("budget_source"),
                    doc.get("source_file"),
                    str(doc.get("status") or "budgeted"),
                    bool(doc.get("is_archived", False)),
                    doc.get("archived_at"),
                    doc.get("archived_reason"),
                    mongo_id,
                    bool(doc.get("is_test_data", False)),
                )
            )

        new = [r for r in rows if r[14] not in existing]
        budgeted = [r for r in rows if r[7] is not None]

        print("=" * 76)
        print(f"levy_categories -> finance.budget_categories — building {building_id}"
              f"  [{'APPLY' if apply else 'DRY-RUN'}]")
        print("=" * 76)
        print(f"  MongoDB documents          : {len(docs)}")
        print(f"  mappable rows              : {len(rows)}")
        print(f"    of which already present : {len(rows) - len(new)}")
        print(f"    of which NEW             : {len(new)}")
        print(f"  with a budget set          : {len(budgeted)}  "
              f"(total ${sum(r[7] for r in budgeted) / 100:,.2f})")
        print(f"  archived                   : {sum(1 for r in rows if r[11])}")
        print(f"  SKIPPED — unresolvable fund: {len(skipped_fund)}")
        for name, ft in skipped_fund[:10]:
            print(f"      {name!r} fund_type={ft!r}")
        print(f"  SKIPPED — no MongoDB id    : {len(skipped_no_id)}")

        if not apply:
            print("\n  DRY-RUN — nothing written. Re-run with --apply.")
            return 0

        await pg.executemany(
            """
            INSERT INTO finance.budget_categories (
                tenant_id, scheme_id, fund_id, financial_year, name,
                canonical_key, canonical_name, budgeted_cents, budget_source,
                source_file, status, is_archived, archived_at, archived_reason,
                legacy_mongo_id, is_test_data
            ) VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, $16
            )
            ON CONFLICT (tenant_id, legacy_mongo_id) DO UPDATE SET
                name = EXCLUDED.name,
                canonical_key = EXCLUDED.canonical_key,
                canonical_name = EXCLUDED.canonical_name,
                budgeted_cents = EXCLUDED.budgeted_cents,
                budget_source = EXCLUDED.budget_source,
                status = EXCLUDED.status,
                is_archived = EXCLUDED.is_archived,
                archived_at = EXCLUDED.archived_at,
                archived_reason = EXCLUDED.archived_reason,
                updated_at = now()
            """,
            rows,
        )
        total = await pg.fetchval(
            "SELECT count(*) FROM finance.budget_categories WHERE scheme_id = $1::uuid",
            scheme["sid"],
        )
        print(f"\n  upserted {len(rows)} row(s); table now holds {total} for this scheme.")
        return 0
    finally:
        await pg.close()
        mongo.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.building_id, args.apply)))


if __name__ == "__main__":
    main()
