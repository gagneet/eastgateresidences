#!/usr/bin/env python3
"""Full live database inventory refresh — Mongo + PostgreSQL.

Read-only. Does not write to either database. Refreshes
docs/architecture/database_live_inventory_2026-07-11.md with current live
data: every Postgres schema/table/column plus row-count estimates (matching
the prior doc's own methodology), every Mongo collection with a live
document count, and — the part the prior doc didn't have — REAL (not
estimated) counts for the finance-critical tables/collections, per real
building, since that is specifically where "gems" (data nobody knew existed,
or thought didn't exist) keep costing duplicated investigation effort.

Usage:
    cd backend
    venv/bin/python3 scripts/audits/generate_database_live_inventory.py --building-id 13195 \
        > /tmp/database_live_inventory_output.md
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from database import db as mongo_db  # noqa: E402
from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from request_context import set_ctx_building_id  # noqa: E402

BYPASS_TENANT = "00000000-0000-0000-0000-000000000000"

# Finance-critical + core-identity tables get a REAL count(*) under the
# actual tenant context, not the pg_stat_user_tables estimate — this is
# exactly the set of tables where a stale/estimated count previously masked
# real data (see rules/post-compact-critical.md footgun #8).
FINANCE_CRITICAL_TABLES = [
    "core.tenants", "core.schemes", "core.lots", "core.parties",
    "core.ownership_periods", "core.users",
    "finance.funds", "finance.gl_accounts", "finance.accounting_periods",
    "finance.bank_transactions", "finance.receipts", "finance.receipt_allocations",
    "finance.levy_runs", "finance.levy_items", "finance.journal_entries",
    "finance.journal_lines", "finance.expense_transactions",
    "finance.reconciliation_runs", "core.domain_cutover_status",
]

# Finance-critical Mongo collections, cross-checked per-building (not just a
# global count) since a global count can hide a building-specific gap.
FINANCE_CRITICAL_COLLECTIONS = [
    "unit_levy_ledger", "levy_payments", "annual_levies", "levy_categories",
    "demo_bank_transactions", "financial_transactions", "expense_transactions",
    "income_transactions", "building_summaries", "strata_owners", "units",
    "bank_accounts", "strata_financials",
]


async def _postgres_schema_dump(building_id: str) -> str:
    lines = ["## PostgreSQL — schemas, tables, columns, row counts\n"]

    async with async_session_context() as session:
        schemas_result = await session.execute(text(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast') "
            "ORDER BY schema_name"
        ))
        schemas = [r[0] for r in schemas_result.fetchall()]
        lines.append(f"Schemas ({len(schemas)}): {', '.join(schemas)}\n")

        tables_result = await session.execute(text(
            "SELECT table_schema, table_name, table_type FROM information_schema.tables "
            "WHERE table_schema = ANY(:schemas) ORDER BY table_schema, table_name"
        ), {"schemas": schemas})
        tables = tables_result.fetchall()
        lines.append(f"Total tables/views: {len(tables)}\n")

        estimates_result = await session.execute(text(
            "SELECT schemaname, relname, n_live_tup FROM pg_stat_user_tables"
        ))
        estimates = {(r[0], r[1]): r[2] for r in estimates_result.fetchall()}

        scheme = await resolve_scheme_context(building_id)
        real_tenant_id = str(scheme["tenant_id"]) if scheme else None

        real_counts: dict[str, int] = {}
        if real_tenant_id:
            for qualified in FINANCE_CRITICAL_TABLES:
                schema_name, table_name = qualified.split(".")
                for tenant_ctx in ([BYPASS_TENANT, real_tenant_id] if real_tenant_id else [BYPASS_TENANT]):
                    try:
                        await set_tenant(session, tenant_ctx)
                        count_result = await session.execute(
                            text(f"SELECT count(*) FROM {schema_name}.{table_name}")
                        )
                        count = count_result.scalar()
                        if count and count > 0:
                            real_counts[qualified] = count
                            break
                    except Exception:
                        continue
            await set_tenant(session, BYPASS_TENANT)

        lines.append(
            f"\n### Real (not estimated) counts for finance-critical tables — "
            f"building_id={building_id}, tenant_id={real_tenant_id}\n"
        )
        for qualified in FINANCE_CRITICAL_TABLES:
            count = real_counts.get(qualified, 0)
            lines.append(f"- `{qualified}`: **{count}** real rows (tenant-scoped or bypass-visible)")
        lines.append("")

        columns_result = await session.execute(text(
            "SELECT table_schema, table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns WHERE table_schema = ANY(:schemas) "
            "ORDER BY table_schema, table_name, ordinal_position"
        ), {"schemas": schemas})
        columns_by_table: dict[tuple[str, str], list[str]] = {}
        for schema_name, table_name, col_name, data_type, is_nullable in columns_result.fetchall():
            key = (schema_name, table_name)
            null_str = "" if is_nullable == "YES" else " not null"
            columns_by_table.setdefault(key, []).append(f"`{col_name}` {data_type}{null_str}")

        lines.append("\n### All tables/views by schema, with columns\n")
        current_schema = None
        for schema_name, table_name, table_type in tables:
            if schema_name != current_schema:
                lines.append(f"\n#### Schema `{schema_name}`\n")
                current_schema = schema_name
            est = estimates.get((schema_name, table_name), "n/a")
            qualified = f"{schema_name}.{table_name}"
            real_marker = f" — REAL: {real_counts[qualified]}" if qualified in real_counts else ""
            lines.append(f"\n##### `{qualified}` ({table_type}, est. rows: {est}{real_marker})\n")
            cols = columns_by_table.get((schema_name, table_name), [])
            lines.append(", ".join(cols) if cols else "(no columns found)")

    return "\n".join(lines)


async def _mongo_dump(building_id: str) -> str:
    lines = ["\n## MongoDB — collections and document counts\n"]

    collection_names = sorted(await mongo_db._db.list_collection_names())
    lines.append(f"Total collections: {len(collection_names)}\n")

    lines.append(
        f"\n### Real per-building counts + observed fields for finance-critical collections — "
        f"building_id={building_id}\n"
        "(Fields observed from up to 3 sampled documents scoped to this building — a collection can "
        "have more fields than any single sample shows, this is not an exhaustive schema.)\n"
    )
    for coll_name in FINANCE_CRITICAL_COLLECTIONS:
        if coll_name not in collection_names:
            lines.append(f"- `{coll_name}`: **collection does not exist**\n")
            continue
        try:
            total = await mongo_db._db[coll_name].count_documents({})
            scoped = await mongo_db._db[coll_name].count_documents({"building_id": building_id})
            samples = await mongo_db._db[coll_name].find(
                {"building_id": building_id}
            ).limit(3).to_list(3)
        except Exception as exc:
            lines.append(f"- `{coll_name}`: error counting — {exc}\n")
            continue
        fields: set[str] = set()
        for s in samples:
            fields.update(s.keys())
        fields.discard("_id")
        lines.append(f"- `{coll_name}`: {total} total, **{scoped} for building_id={building_id}**")
        lines.append(f"  fields observed: {', '.join(sorted(fields)) if fields else '(no documents to sample)'}\n")

    lines.append("\n### All collections (global counts)\n")
    for coll_name in collection_names:
        try:
            count = await mongo_db._db[coll_name].count_documents({})
        except Exception as exc:
            count = f"error: {exc}"
        lines.append(f"- `{coll_name}`: {count}")

    return "\n".join(lines)


async def main(building_id: str) -> None:
    set_ctx_building_id(building_id)
    today = date.today().isoformat()

    print(f"# Live Database Inventory — {today}\n")
    print(
        "Generated by `backend/scripts/audits/generate_database_live_inventory.py` "
        "(read-only, no writes to either database). PostgreSQL row counts marked "
        "\"est.\" are `pg_stat_user_tables.n_live_tup` estimates; rows marked "
        "\"REAL\" are live `count(*)` under the actual tenant context (bypass or "
        "the real tenant_id, whichever returns visible rows) for the finance-"
        "critical table list this script defines explicitly — see script source "
        "for the exact list and rationale.\n"
    )

    pg_output = await _postgres_schema_dump(building_id)
    print(pg_output)

    mongo_output = await _mongo_dump(building_id)
    print(mongo_output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--building-id", type=str, default="13195")
    args = parser.parse_args()
    asyncio.run(main(args.building_id))
