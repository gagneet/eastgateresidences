#!/usr/bin/env python3
# @featuretrace:financial_core — Read-only cutover-readiness live snapshot.
# Layer: script (audit / read-only)
# Purpose: Produce, in ONE run against the live databases, every figure the
#          go-live readiness doc needs, mirrored to the fields the frontend
#          "Source-of-Truth Control Plane" page (CutoverStatusPage.tsx) shows.
#
# READ-ONLY GUARANTEE: this script issues only SELECT statements and session-local
# `SET app.tenant_id` (required to satisfy Postgres RLS — see CLAUDE.md). It never
# INSERTs, UPDATEs, DELETEs, or writes to Mongo. Safe to run against production.
#
# Usage (on the live server, from the repo root):
#   cd backend && venv/bin/python3 scripts/audits/cutover_readiness_snapshot.py --building 13195
#
# Reads connection strings from backend/.env (MONGO_URL, DB_NAME, DATABASE_URL).
"""Live, read-only cutover-readiness snapshot for the PostgreSQL migration.

Prints seven sections:
  1. East Gate tenant resolution (proves RLS context is set correctly)
  2. core.domain_cutover_status  — per-domain mode / readiness / read+write source
  3. Protected feature toggles   — global + per-building, confirms data_source_primary OFF
  4. core.shadow_diffs           — unresolved divergence backlog (the finance gate)
  5. finance.* row coverage      — bank imports / reconciliation runs / receipts / levy items
  6. core.outbox                 — relay backlog + dead-letter count
  7. Mongo DR snapshot + ledger  — dr_snapshot_meta freshness, unit_levy_ledger count
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from pathlib import Path

# This file lives at backend/scripts/audits/, so parents[2] IS the backend dir.
# (Do not append "backend" again — that yields backend/backend and breaks both the
#  `import core` path and the .env load.)
BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

BYPASS_TENANT = "00000000-0000-0000-0000-000000000000"

# Authoritative protected-toggle key set — imported from the app so this never drifts.
try:
    from core.toggle_classification import TOGGLE_CLASSIFICATION, PROTECTED_TOGGLE_CLASSES
    PROTECTED_KEYS = {
        k: v.value if hasattr(v, "value") else str(v)
        for k, v in TOGGLE_CLASSIFICATION.items()
        if (v.value if hasattr(v, "value") else str(v)) in {
            (c.value if hasattr(c, "value") else str(c)) for c in PROTECTED_TOGGLE_CLASSES
        }
    }
except Exception as e:  # pragma: no cover - defensive
    print(f"! could not import toggle_classification ({e}); using a static fallback list")
    PROTECTED_KEYS = {
        "financial_pg_reads_enabled": "data_source_primary",
        "financial_pg_writes_enabled": "finance_write",
        "bi_pg_primary_enabled": "data_source_primary",
        "owner_read_pg_enabled": "data_source_primary",
        "governance_read_pg_enabled": "data_source_primary",
        "external_api_finance_pg_enabled": "data_source_primary",
        "settings_pg_reads_enabled": "data_source_primary",
        "users_pg_reads_enabled": "data_source_primary",
        "financial_shadow_reads_enabled": "shadow_read",
        "financial_integration_layer_v2": "cutover_sensitive",
        "trust_pg_ledger_enabled": "trust_write",
        "trust_reconciliation_pg_enabled": "trust_write",
    }


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _asyncpg_dsn() -> str:
    dsn = os.getenv("DATABASE_URL", "")
    # asyncpg.connect() wants a bare postgresql:// DSN, not the SQLAlchemy dialect form.
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", dsn)


async def _set_tenant(conn, tenant_id: str) -> None:
    # tenant_id is DB-sourced (core.schemes) or the BYPASS constant, never user input.
    # Validate it as a UUID defensively, then set RLS context via set_config() with a
    # BIND PARAMETER — no string interpolation. (Postgres `SET` cannot take bind params,
    # but set_config('app.tenant_id', $1, false) is the parameterisable equivalent.)
    tid = str(uuid.UUID(str(tenant_id)))
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tid)


async def _safe(conn, label: str, sql: str, *args):
    try:
        return await conn.fetch(sql, *args)
    except Exception as e:
        print(f"  ! {label}: {type(e).__name__}: {str(e)[:160]}")
        return None


async def _columns(conn, schema: str, table: str) -> set[str]:
    """Discover actual column names so queries don't assume a schema shape."""
    try:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2",
            schema, table,
        )
        return {r["column_name"] for r in rows}
    except Exception as e:
        print(f"  ! columns({schema}.{table}): {type(e).__name__}: {str(e)[:120]}")
        return set()


async def run_postgres(building: str) -> None:
    import asyncpg

    dsn = _asyncpg_dsn()
    if not dsn:
        print("! DATABASE_URL not set — skipping Postgres section")
        return
    try:
        conn = await asyncpg.connect(dsn)
    except Exception as e:
        print(f"! Failed to connect to Postgres: {type(e).__name__}: {str(e)[:160]}")
        return
    try:
        # ---- 1. Tenant resolution (bypass first, to read core.schemes) --------
        _hr("1. TENANT RESOLUTION")
        await _set_tenant(conn, BYPASS_TENANT)
        # Column-adaptive: this schema's core.schemes has no `id` column, and other
        # optional columns vary — select only what actually exists.
        scols = await _columns(conn, "core", "schemes")
        sel = ["tenant_id::text AS tenant_id"]
        for c in ("scheme_number", "scheme_id", "id", "is_demo", "is_test_data", "name"):
            if c in scols:
                sel.append(c)
        filter_col = "scheme_number" if "scheme_number" in scols else None
        srow = None
        if filter_col:
            srow = await _safe(
                conn, "core.schemes lookup",
                f"SELECT {', '.join(sel)} FROM core.schemes WHERE {filter_col} = $1",
                building,
            )
        else:
            print(f"  ! core.schemes has no scheme_number column; available: {sorted(scols)}")
        tenant_id = None
        if srow:
            for r in srow:
                print("  " + "  ".join(f"{k}={r[k]}" for k in r.keys()))
            tenant_id = srow[0]["tenant_id"]
        else:
            print(f"  ! no core.schemes row for scheme_number={building} (RLS context or missing row)")
        print(f"  --> resolved tenant_id: {tenant_id or 'NONE (finance.* reads will be RLS-blocked → false 0)'}")

        # ---- 2. domain_cutover_status ---------------------------------------
        _hr("2. core.domain_cutover_status  (mirrors the main control-plane table)")
        if tenant_id:
            await _set_tenant(conn, tenant_id)
        rows = await _safe(
            conn, "domain_cutover_status",
            "SELECT domain, mode, readiness_status, read_source, write_source, "
            "       last_promoted_at, last_shadow_diff_at, "
            "       COALESCE(rollback_available, NULL) AS rollback_available, notes "
            "FROM core.domain_cutover_status WHERE building_id = $1 ORDER BY domain",
            building,
        )
        if rows is None:
            # rollback_available column may not exist in this schema version — retry minimal set.
            rows = await _safe(
                conn, "domain_cutover_status (minimal)",
                "SELECT domain, mode, readiness_status, read_source, write_source, "
                "last_promoted_at, last_shadow_diff_at, notes "
                "FROM core.domain_cutover_status WHERE building_id = $1 ORDER BY domain",
                building,
            )
        if rows:
            for r in rows:
                d = dict(r)
                print(f"  {d.get('domain'):<24} mode={str(d.get('mode')):<16} "
                      f"readiness={str(d.get('readiness_status')):<16} "
                      f"read={str(d.get('read_source')):<8} write={str(d.get('write_source')):<8} "
                      f"last_promoted={d.get('last_promoted_at')} last_diff={d.get('last_shadow_diff_at')}")
                if d.get("notes"):
                    print(f"      notes: {str(d['notes'])[:200]}")
        else:
            print("  (no rows)")

        # ---- 3. Protected feature toggles -----------------------------------
        _hr("3. PROTECTED FEATURE TOGGLES  (must stay FALSE until gates pass)")
        # core.feature_toggles is the global catalogue — read under BYPASS_TENANT.
        await _set_tenant(conn, BYPASS_TENANT)
        # This schema's toggle tables do NOT have a building_id column — scope is
        # carried by scheme_id/tenant_id/scope (or the row is a global default).
        # Fetch everything and filter/label in Python so we don't assume a shape.
        g = await _safe(conn, "core.feature_toggles", "SELECT * FROM core.feature_toggles")
        # core.feature_toggle_overrides' RLS policy (tenant_id = core.current_tenant_id())
        # has NO bypass clause — unlike core.schemes/core.tenants, BYPASS_TENANT context
        # silently returns zero rows here (confirmed via pg_policy 2026-08-11), which
        # previously made real per-building overrides invisible to this script. Must
        # read it under the RESOLVED tenant_id for THIS building instead.
        if tenant_id:
            await _set_tenant(conn, tenant_id)
            o = await _safe(conn, "core.feature_toggle_overrides",
                            "SELECT * FROM core.feature_toggle_overrides")
            await _set_tenant(conn, BYPASS_TENANT)
        else:
            print("  ! no resolved tenant_id — cannot read core.feature_toggle_overrides under RLS")
            o = None

        SCOPE_COLS = ("building_id", "scheme_id", "tenant_id", "scope", "organisation_id")

        def _key_of(row: dict):
            for col in ("feature_key", "key", "toggle_key", "name", "feature_name"):
                if col in row and row[col] in PROTECTED_KEYS:
                    return row[col]
            for v in row.values():
                if isinstance(v, str) and v in PROTECTED_KEYS:
                    return v
            return None

        def _enabled_of(row: dict):
            for col in ("enabled", "is_enabled", "value", "enabled_bool"):
                if col in row and isinstance(row[col], bool):
                    return row[col]
            for k, v in row.items():
                if isinstance(v, bool):
                    return v
            return "?"

        def _scope_of(row: dict) -> str:
            parts = [f"{c}={row[c]}" for c in SCOPE_COLS if c in row and row[c] is not None]
            return " ".join(parts) if parts else "GLOBAL(default)"

        print("  -- core.feature_toggles (catalogue + defaults) --")
        seen = set()
        for r in (g or []):
            d = dict(r)
            k = _key_of(d)
            if k:
                seen.add(k)
                print(f"    {k:<40} class={PROTECTED_KEYS[k]:<20} enabled={str(_enabled_of(d)):<6} [{_scope_of(d)}]")
        missing = [k for k in PROTECTED_KEYS if k not in seen]
        if missing:
            print(f"    (no catalogue row for: {', '.join(sorted(missing))})")
        print(f"  -- core.feature_toggle_overrides (protected-key overrides for THIS building's tenant, {tenant_id}) --")
        any_override = False
        for r in (o or []):
            d = dict(r)
            k = _key_of(d)
            if k:
                any_override = True
                print(f"    {k:<40} class={PROTECTED_KEYS[k]:<20} enabled={str(_enabled_of(d)):<6} [{_scope_of(d)}]")
        if not any_override:
            print("    (no overrides for protected keys on this building's tenant)")

        # ---- 4. shadow_diffs backlog ----------------------------------------
        _hr("4. core.shadow_diffs  (unresolved divergence = the finance read gate)")
        if tenant_id:
            await _set_tenant(conn, tenant_id)
        sd = await _safe(
            conn, "shadow_diffs by domain",
            "SELECT domain, COUNT(*) AS total, "
            "       COUNT(*) FILTER (WHERE resolved = false) AS unresolved, "
            "       COUNT(*) FILTER (WHERE resolved = false AND COALESCE(divergence_score,0) >= 1) AS critical, "
            "       MAX(created_at) AS last_diff "
            "FROM core.shadow_diffs WHERE building_id = $1 GROUP BY domain ORDER BY unresolved DESC",
            building,
        )
        if sd:
            tot = 0
            for r in sd:
                d = dict(r)
                tot += d.get("unresolved") or 0
                print(f"  {str(d.get('domain')):<24} unresolved={d.get('unresolved'):<6} "
                      f"critical={d.get('critical'):<6} total={d.get('total'):<6} last={d.get('last_diff')}")
            print(f"  --> TOTAL UNRESOLVED (all domains): {tot}")
        else:
            print("  (no rows / table empty)")

        # ---- 5. finance.* coverage ------------------------------------------
        _hr("5. finance.* ROW COVERAGE  (PG ledger data)")
        # NOTE: bank_statement_imports / reconciliation_runs being empty is NOT a
        # gap — no real bank is connected; ALL bank data flows through Demo Bank
        # ONLY, tagged by transaction_origin (see section 7). These two tables are
        # simply not the intake path for this deployment.
        if tenant_id:
            await _set_tenant(conn, tenant_id)
        for tbl in ("finance.bank_statement_imports", "finance.reconciliation_runs",
                    "finance.receipts", "finance.levy_items", "finance.journal_lines"):
            c = await _safe(conn, tbl, f"SELECT COUNT(*) AS n FROM {tbl}")
            note = "  (not used — Demo Bank is the intake path)" if tbl in (
                "finance.bank_statement_imports", "finance.reconciliation_runs") else ""
            print(f"  {tbl:<34} rows={dict(c[0])['n'] if c else '?'}{note}")

        # ---- 6. outbox backlog ----------------------------------------------
        _hr("6. core.outbox  (relay backlog + dead-letter)")
        await _set_tenant(conn, BYPASS_TENANT)
        ob = await _safe(
            conn, "outbox",
            "SELECT COUNT(*) AS total, "
            "       COUNT(*) FILTER (WHERE published_at IS NULL) AS unpublished, "
            "       COUNT(*) FILTER (WHERE published_at IS NULL AND COALESCE(attempts,0) >= 5) AS dead_letter, "
            "       COUNT(*) FILTER (WHERE published_at IS NULL AND last_error ILIKE '%DEAD%') AS dead_letter_tagged "
            "FROM core.outbox",
        )
        if ob:
            d = dict(ob[0])
            print(f"  total={d['total']}  unpublished={d['unpublished']}  "
                  f"dead_letter(attempts>=5)={d['dead_letter']}  dead_letter(tagged)={d['dead_letter_tagged']}")
        else:
            print("  (query failed / table missing)")
    finally:
        await conn.close()


async def run_mongo(building: str) -> None:
    _hr("7. MONGO — DR snapshot freshness + ledger coverage")
    mongo_url = os.getenv("MONGO_URL")
    db_name = os.getenv("DB_NAME")
    if not mongo_url or not db_name:
        print("! MONGO_URL / DB_NAME not set — skipping Mongo section")
        return
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=6000)
        db = client[db_name]
        # DR snapshot metadata (only exists once dr_mongo_snapshot.py has run).
        meta = await db.dr_snapshot_meta.find_one(
            {"building_id": building}, sort=[("completed_at", -1)]
        )
        if meta:
            print(f"  dr_snapshot_meta: status={meta.get('reconciliation_status')}  "
                  f"completed_at={meta.get('completed_at')}  row_count={meta.get('row_count')}  "
                  f"control_total_cents={meta.get('control_total_cents')}")
        else:
            print("  dr_snapshot_meta: NONE yet (DR snapshot has not run for this building)")
        ulc = await db.unit_levy_ledger.count_documents({"building_id": building})
        print(f"  unit_levy_ledger rows (building={building}): {ulc}")
        dbt = await db.demo_bank_transactions.count_documents({"building_id": building})
        print(f"  demo_bank_transactions rows (building={building}): {dbt}")

        # Demo Bank is the ONLY bank-intake path (all banks flow through it). The
        # reconciliation-evidence view is this transaction_origin breakdown — NOT
        # finance.bank_statement_imports. "derived" = reconstructed/seed/adjustment;
        # "from bank" = observed_bank_feed / imported_bank_statement.
        print("  demo_bank_transactions by transaction_origin (the real evidence view):")
        try:
            pipeline = [
                {"$match": {"building_id": building}},
                {"$group": {"_id": "$transaction_origin", "n": {"$sum": 1}}},
                {"$sort": {"n": -1}},
            ]
            from_bank = {"observed_bank_feed", "imported_bank_statement"}
            async for row in db.demo_bank_transactions.aggregate(pipeline):
                origin = row.get("_id") or "(unset)"
                kind = "FROM BANK" if origin in from_bank else "derived/other"
                print(f"    {str(origin):<26} {row['n']:<6} [{kind}]")
        except Exception as e:
            print(f"    ! origin breakdown failed: {type(e).__name__}: {str(e)[:120]}")
    except Exception as e:
        print(f"  ! Mongo error: {type(e).__name__}: {str(e)[:160]}")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only cutover-readiness live snapshot.")
    ap.add_argument("--building", default="13195", help="building_id / scheme_number (default: 13195 East Gate)")
    args = ap.parse_args()
    print(f"Cutover-readiness snapshot for building={args.building}  (READ-ONLY)")
    await run_postgres(args.building)
    await run_mongo(args.building)
    print("\nDone. Paste this entire output back to finalize the readiness doc's live figures.")


if __name__ == "__main__":
    asyncio.run(main())
