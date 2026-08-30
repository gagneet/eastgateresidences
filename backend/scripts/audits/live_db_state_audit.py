#!/usr/bin/env python3
# @featuretrace:cutover-toggle-safety — Read-only live MongoDB/PostgreSQL state inventory.
# Layer: script
# Data flow: operator CLI -> live MongoDB/PostgreSQL metadata/count queries -> JSON audit summary.
# Related: docs/audit/p1_live_cutover_guard_audit_verification.md
#          docs/audit/p2_capability_registry_status.md
"""Read-only live database state audit.

This script intentionally reports counts, schema metadata, column names, index
names, and redacted audit-event summaries. It does not print database URLs,
credentials, raw user documents, financial records, payment records, or request
payloads.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"
ROOT = Path(__file__).resolve().parents[3]


def _env() -> dict[str, str]:
    """Generated function header.

    Function: _env
    Path: backend/scripts/audits/live_db_state_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    values = {k: v for k, v in dotenv_values(ROOT / "backend" / ".env").items() if v is not None}
    values.update({k: v for k, v in os.environ.items() if v is not None})
    return values


def _pg_url(values: dict[str, str]) -> str:
    """Generated function header.

    Function: _pg_url
    Path: backend/scripts/audits/live_db_state_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return values.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


async def _pg_table_exists(conn: asyncpg.Connection, schema: str, table: str) -> bool:
    """Generated function header.

    Function: _pg_table_exists
    Path: backend/scripts/audits/live_db_state_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = $1 AND table_name = $2
            )
            """,
            schema,
            table,
        )
    )


async def _pg_count_if_exists(conn: asyncpg.Connection, schema: str, table: str) -> int | None:
    """Generated function header.

    Function: _pg_count_if_exists
    Path: backend/scripts/audits/live_db_state_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not await _pg_table_exists(conn, schema, table):
        return None
    count = int(await conn.fetchval(f'SELECT count(*) FROM "{schema}"."{table}"'))
    if count == 0:
        # RLS blind-spot guard. This inventory runs under the bypass sentinel
        # (app.tenant_id = 00000000-…-000000000000), which only unlocks tables
        # whose RLS policy carries an explicit bypass clause (core.schemes,
        # core.tenants, core.users, core.user_invitations). Tables with a strict
        # `tenant_id = current_tenant_id()` policy and NO bypass clause —
        # core.feature_toggle_overrides, core.building_settings, core.lots,
        # core.parties, core.ownership_periods, and every finance.* table —
        # return count(*)=0 under the sentinel even when fully populated, with no
        # error. pg_stat_user_tables.n_live_tup is not subject to RLS, so a
        # positive estimate here proves the zero is a masking artefact, not an
        # empty table. Report the estimate (and warn) instead of a misleading 0.
        # See CLAUDE.md "Postgres RLS — an ad-hoc asyncpg.connect() … returns 0 rows".
        est = await conn.fetchval(
            """
            SELECT n_live_tup FROM pg_stat_user_tables
            WHERE schemaname = $1 AND relname = $2
            """,
            schema, table,
        )
        if est and int(est) > 0:
            print(
                f"  ! RLS-masked count: {schema}.{table} count(*)=0 under the bypass "
                f"sentinel but pg_stat_user_tables.n_live_tup≈{int(est)} — this table's "
                f"policy has no bypass clause; reporting the estimate. Set app.tenant_id "
                f"to the real tenant UUID for an exact count.",
                file=sys.stderr,
            )
            return int(est)
    return count


def _scheme_number_candidates(building_id: str) -> list[str]:
    """Generated function header.

    Function: _scheme_number_candidates
    Path: backend/scripts/audits/live_db_state_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    normalized = str(building_id).strip()
    candidates = {normalized}
    if normalized.upper().startswith("UP"):
        bare = normalized[2:]
        if bare:
            candidates.add(bare)
    else:
        candidates.add(f"UP{normalized}")
    return sorted(candidates)


async def _east_gate_tenant_context_counts(
    conn: asyncpg.Connection,
    building_id: str = "13195",
) -> OrderedDict[str, Any]:
    """Return East Gate counts using the resolved scheme tenant context."""
    out: OrderedDict[str, Any] = OrderedDict()
    schemes = await conn.fetch(
        """
        SELECT scheme_id::text, tenant_id::text, scheme_number, scheme_name, status
        FROM core.schemes
        WHERE scheme_number = ANY($1::text[])
        ORDER BY scheme_number
        """,
        _scheme_number_candidates(building_id),
    )
    out["building_id"] = building_id
    out["matching_schemes"] = [
        {
            "scheme_id": row["scheme_id"],
            "tenant_id": row["tenant_id"],
            "scheme_number": row["scheme_number"],
            "scheme_name": row["scheme_name"],
            "status": row["status"],
        }
        for row in schemes
    ]
    if not schemes:
        out["counts"] = {}
        return out

    scheme_ids = [row["scheme_id"] for row in schemes]
    tenant_ids = sorted({row["tenant_id"] for row in schemes})
    await conn.execute("SELECT set_config($1, $2, false)", "app.tenant_id", tenant_ids[0])

    async def count(schema: str, table: str, where: str, *args: Any) -> int | None:
        """Generated function header.

        Function: count
        Path: backend/scripts/audits/live_db_state_audit.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if not await _pg_table_exists(conn, schema, table):
            return None
        query = f'SELECT count(*) FROM "{schema}"."{table}" WHERE {where}'
        return int(await conn.fetchval(query, *args))

    out["counts"] = OrderedDict([
        ("core.tenants", await count("core", "tenants", "tenant_id::text = ANY($1::text[])", tenant_ids)),
        ("core.schemes", await count("core", "schemes", "scheme_id::text = ANY($1::text[])", scheme_ids)),
        ("core.users", await count("core", "users", "tenant_id::text = ANY($1::text[])", tenant_ids)),
        ("core.lots", await count("core", "lots", "scheme_id::text = ANY($1::text[])", scheme_ids)),
        ("core.parties", await count("core", "parties", "tenant_id::text = ANY($1::text[])", tenant_ids)),
        ("core.ownership_periods", await count("core", "ownership_periods", "scheme_id::text = ANY($1::text[])", scheme_ids)),
        ("finance.funds", await count("finance", "funds", "scheme_id::text = ANY($1::text[])", scheme_ids)),
        ("finance.gl_accounts", await count("finance", "gl_accounts", "scheme_id::text = ANY($1::text[])", scheme_ids)),
        ("finance.journal_entries", await count("finance", "journal_entries", "scheme_id::text = ANY($1::text[])", scheme_ids)),
        ("finance.journal_lines", await count("finance", "journal_lines", "scheme_id::text = ANY($1::text[])", scheme_ids)),
        ("finance.evidence_documents", await count("finance", "evidence_documents", "scheme_id::text = ANY($1::text[])", scheme_ids)),
        ("finance.financial_cutover_config", await count("finance", "financial_cutover_config", "building_id = ANY($1::text[])", _scheme_number_candidates(building_id))),
    ])
    await conn.execute("SELECT set_config($1, $2, false)", "app.tenant_id", TENANT_BYPASS_ID)
    return out


async def postgres_inventory(values: dict[str, str]) -> OrderedDict[str, Any]:
    """Generated function header.

    Function: postgres_inventory
    Path: backend/scripts/audits/live_db_state_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    conn = await asyncpg.connect(_pg_url(values))
    try:
        await conn.execute("SELECT set_config($1, $2, false)", "app.tenant_id", TENANT_BYPASS_ID)
        out: OrderedDict[str, Any] = OrderedDict()
        out["database"] = await conn.fetchval("SELECT current_database()")
        out["server_version"] = await conn.fetchval("SHOW server_version")
        out["alembic_version"] = await conn.fetchval("SELECT version_num FROM core.alembic_version LIMIT 1")
        out["schema_count"] = int(
            await conn.fetchval(
                """
                SELECT count(*)
                FROM information_schema.schemata
                WHERE schema_name NOT LIKE 'pg_%'
                  AND schema_name <> 'information_schema'
                """
            )
        )
        out["table_count"] = int(
            await conn.fetchval(
                """
                SELECT count(*)
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema NOT LIKE 'pg_%'
                  AND table_schema <> 'information_schema'
                """
            )
        )
        rows = await conn.fetch(
            """
            SELECT table_schema, count(*) AS tables
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT LIKE 'pg_%'
              AND table_schema <> 'information_schema'
            GROUP BY table_schema
            ORDER BY table_schema
            """
        )
        out["tables_by_schema"] = {row["table_schema"]: int(row["tables"]) for row in rows}

        key_tables = [
            ("core", "tenants"),
            ("core", "schemes"),
            ("core", "users"),
            ("core", "user_role_assignments"),
            ("core", "user_units"),
            ("core", "lots"),
            ("core", "parties"),
            ("core", "ownership_periods"),
            ("core", "domain_cutover_status"),
            ("core", "cutover_audit_log"),
            ("core", "shadow_diffs"),
            ("core", "feature_toggles"),
            ("core", "feature_toggle_overrides"),
            ("core", "management_entities"),
            ("core", "scheme_manager_appointments"),
            ("analytics", "dim_building"),
            ("analytics", "dim_lot"),
            ("analytics", "dim_owner"),
            ("analytics", "fact_levy_charge"),
            ("analytics", "fact_levy_payment"),
            ("analytics", "fact_financial_balance"),
            ("finance", "journal_entries"),
            ("finance", "journal_lines"),
            ("finance", "gl_accounts"),
            ("finance", "evidence_documents"),
            ("finance", "financial_cutover_config"),
        ]
        out["key_table_counts"] = {
            f"{schema}.{table}": await _pg_count_if_exists(conn, schema, table)
            for schema, table in key_tables
        }
        out["east_gate_tenant_context_counts"] = await _east_gate_tenant_context_counts(conn)

        columns = await conn.fetch(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'core' AND table_name = 'cutover_audit_log'
            ORDER BY ordinal_position
            """
        )
        out["cutover_audit_log_columns"] = [dict(row) for row in columns]
        out["cutover_action_check"] = await conn.fetchval(
            """
            SELECT pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'core'
              AND t.relname = 'cutover_audit_log'
              AND c.conname LIKE '%cutover_audit_action%'
            """
        )

        events = await conn.fetch(
            """
            SELECT action, count(*) AS count, min(created_at) AS first_seen, max(created_at) AS last_seen
            FROM core.cutover_audit_log
            WHERE action LIKE 'domain_source.%'
            GROUP BY action
            ORDER BY action
            """
        )
        out["domain_source_events"] = [
            {
                "action": row["action"],
                "count": int(row["count"]),
                "first_seen": str(row["first_seen"]),
                "last_seen": str(row["last_seen"]),
            }
            for row in events
        ]
        recent = await conn.fetch(
            """
            SELECT created_at, building_id, domain, action, from_mode, to_mode,
                   reason,
                   metadata->>'route' AS route,
                   metadata->>'source_service' AS source_service,
                   is_test_data
            FROM core.cutover_audit_log
            WHERE action LIKE 'domain_source.%'
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
        out["recent_domain_source_events"] = [
            {key: str(value) if key == "created_at" else value for key, value in dict(row).items()}
            for row in recent
        ]
        return out
    finally:
        await conn.close()


async def mongo_inventory(values: dict[str, str]) -> OrderedDict[str, Any]:
    """Generated function header.

    Function: mongo_inventory
    Path: backend/scripts/audits/live_db_state_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    client = AsyncIOMotorClient(values.get("MONGO_URL", ""), serverSelectionTimeoutMS=5000)
    try:
        await client.admin.command("ping")
        db_name = values.get("DB_NAME", "")
        db = client[db_name]
        collection_names = sorted(await db.list_collection_names())
        out: OrderedDict[str, Any] = OrderedDict()
        out["database"] = db_name
        out["collection_count"] = len(collection_names)

        key_collections = [
            "buildings",
            "users",
            "user_units",
            "units",
            "annual_levies",
            "levy_payments",
            "financial_forecasts",
            "feature_toggles",
            "settings",
            "site_settings",
            "documents",
            "maintenance_requests",
            "workflow_requests",
            "notifications",
        ]
        out["key_collection_counts"] = {
            name: await db[name].count_documents({}) if name in collection_names else None
            for name in key_collections
        }
        out["east_gate_building_id_counts"] = {
            name: await db[name].count_documents({"building_id": "13195"})
            for name in key_collections
            if name in collection_names
        }
        out["building_docs_13195_or_plan_id"] = (
            await db.buildings.count_documents({"$or": [{"building_id": "13195"}, {"plan_id": "13195"}]})
            if "buildings" in collection_names
            else None
        )

        sample_key_collections = [
            "users",
            "user_units",
            "units",
            "annual_levies",
            "levy_payments",
            "settings",
            "feature_toggles",
        ]
        out["east_gate_sample_keys"] = {
            name: sorted((await db[name].find_one({"building_id": "13195"}, {"_id": 0}) or {}).keys())
            for name in sample_key_collections
            if name in collection_names
        }
        out["key_indexes"] = {
            name: sorted((await db[name].index_information()).keys())
            for name in sample_key_collections
            if name in collection_names
        }
        return out
    finally:
        client.close()


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/audits/live_db_state_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    values = _env()
    result: OrderedDict[str, Any] = OrderedDict()
    result["postgres"] = await postgres_inventory(values)
    result["mongo"] = await mongo_inventory(values)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
