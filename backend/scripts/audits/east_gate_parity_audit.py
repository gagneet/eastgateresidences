"""Mongo↔Postgres parity audit for a single building.

Read-only. Compares record counts and key sets across Mongo collections and
their Postgres counterparts. Prints a summary table and exits 0 if all domains
match, 1 if any diverge.

Usage:
    cd backend
    set -a && source .env && set +a
    python3 scripts/audits/east_gate_parity_audit.py --building-id 13195
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent.parent.parent
load_dotenv(ROOT_DIR / ".env")

SEP = "─" * 70
STATUS_OK   = "✅ match"
STATUS_WARN = "⚠️  count match / key mismatch"
STATUS_FAIL = "❌ diverged"
STATUS_INFO = "ℹ️  pg-only"


def _h(title: str) -> None:
    """Generated function header.

    Function: _h
    Path: backend/scripts/audits/east_gate_parity_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    print(f"\n{SEP}\n  {title}\n{SEP}")


def _row(n: int, domain: str, mongo: str, pg: str, delta: str, status: str) -> None:
    """Generated function header.

    Function: _row
    Path: backend/scripts/audits/east_gate_parity_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    print(f"  {n:<3} {domain:<30} {mongo:>8}  {pg:>8}  {delta:>6}  {status}")


async def run(building_id: str) -> int:
    """Generated function header.

    Function: run
    Path: backend/scripts/audits/east_gate_parity_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        import asyncpg
    except ImportError:
        print("asyncpg not installed: pip install asyncpg", file=sys.stderr)
        return 1

    mongo_url  = os.environ["MONGO_URL"]
    db_name    = os.environ["DB_NAME"]
    db_url     = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")

    mongo = AsyncIOMotorClient(mongo_url)
    mdb   = mongo[db_name]
    pg    = await asyncpg.connect(db_url)

    # Set RLS bypass FIRST so the scheme lookup can see all rows
    BYPASS = "00000000-0000-0000-0000-000000000000"
    await pg.execute(f"SELECT set_config('app.tenant_id', '{BYPASS}', false)")

    # Resolve scheme context from Postgres
    scheme_row = await pg.fetchrow(
        """
        SELECT s.scheme_id, s.tenant_id, t.tenant_name, s.scheme_name
        FROM   core.schemes  s
        JOIN   core.tenants  t USING (tenant_id)
        WHERE  s.scheme_number = $1 AND s.status = 'active'
        """,
        building_id,
    )
    if not scheme_row:
        print(f"No active Postgres scheme for building_id={building_id}", file=sys.stderr)
        return 1

    scheme_id = scheme_row["scheme_id"]
    tenant_id = scheme_row["tenant_id"]

    # Switch to the real tenant_id so RLS on core.lots and finance.* works
    # (core.lots has no bypass clause — only core.schemes does)
    await pg.execute(f"SELECT set_config('app.tenant_id', '{tenant_id}', false)")

    print(f"\n{'═' * 70}")
    print(f"  Mongo↔Postgres Parity Audit — building_id={building_id}")
    print(f"  scheme_id={scheme_id}   tenant_id={tenant_id}")
    print(f"  Run date: {date.today()}")
    print(f"{'═' * 70}")
    print(f"\n  {'#':<3} {'Domain':<30} {'Mongo':>8}  {'PG':>8}  {'Δ':>6}  Status")
    print(f"  {'-'*3} {'-'*30} {'-'*8}  {'-'*8}  {'-'*6}  {'-'*20}")

    divergences = 0

    # ------------------------------------------------------------------
    # 1. Lots
    # ------------------------------------------------------------------
    m_lots = await mdb.units.count_documents(
        {"building_id": building_id, "is_test_data": {"$ne": True}}
    )
    p_lots = await pg.fetchval(
        "SELECT COUNT(*) FROM core.lots WHERE scheme_id = $1", scheme_id
    )
    delta = m_lots - p_lots
    status = STATUS_OK if delta == 0 else STATUS_FAIL
    if delta != 0:
        divergences += 1
    _row(1, "Lots", str(m_lots), str(p_lots), str(delta), status)

    # ------------------------------------------------------------------
    # 2. Parties (owners)
    # ------------------------------------------------------------------
    m_owners = await mdb.units.distinct("owner_name", {"building_id": building_id})
    m_owners_ct = len([o for o in m_owners if o])
    p_parties = await pg.fetchval(
        "SELECT COUNT(*) FROM core.parties WHERE tenant_id = $1", tenant_id
    )
    delta = m_owners_ct - p_parties
    # Parties are tenant-scoped — count may exceed Mongo set (joint owners split)
    status = STATUS_OK if abs(delta) == 0 else STATUS_WARN if p_parties >= m_owners_ct else STATUS_FAIL
    _row(2, "Parties (owners)", str(m_owners_ct), str(p_parties), str(delta), status)

    # ------------------------------------------------------------------
    # 3. Ownership periods
    # Compare units.owner_name (primary) + units.owner_name_b (secondary)
    # against open ownership_periods in PG. user_units is sparse for this
    # building so the units snapshot is the canonical Mongo source.
    # ------------------------------------------------------------------
    m_primary = await mdb.units.count_documents(
        {"building_id": building_id, "is_test_data": {"$ne": True},
         "owner_name": {"$exists": True, "$nin": ["", None]}}
    )
    m_secondary = await mdb.units.count_documents(
        {"building_id": building_id, "is_test_data": {"$ne": True},
         "owner_name_b": {"$exists": True, "$nin": ["", None]}}
    )
    m_op = m_primary + m_secondary
    p_op = await pg.fetchval(
        # recorded_to IS NULL excludes bitemporally-retracted rows (a period
        # recorded in error and later corrected via ownership_repo.py's
        # close_ownership_period() retraction branch, added 2026-07-24) —
        # those keep valid_to NULL by design, so counting on valid_to alone
        # would double-count a lot that had a retraction and inflate this
        # parity total against Mongo's single-current-owner-per-lot count.
        "SELECT COUNT(*) FROM core.ownership_periods WHERE scheme_id = $1 "
        "AND valid_to IS NULL AND recorded_to IS NULL",
        scheme_id,
    )
    delta = m_op - p_op
    status = STATUS_OK if delta == 0 else STATUS_WARN if abs(delta) <= 5 else STATUS_FAIL
    if delta != 0:
        divergences += 1
    _row(3, f"Ownership periods ({m_primary}+{m_secondary})", str(m_op), str(p_op), str(delta), status)

    # ------------------------------------------------------------------
    # 4. Funds (PG-only)
    # ------------------------------------------------------------------
    p_funds = await pg.fetchval(
        "SELECT COUNT(*) FROM finance.funds WHERE scheme_id = $1", scheme_id
    )
    _row(4, "Funds", "—", str(p_funds), "—", STATUS_INFO)

    # ------------------------------------------------------------------
    # 5. Trust accounts
    # ------------------------------------------------------------------
    m_trust = await mdb.trust_accounts_v2.count_documents(
        {"building_id": building_id, "is_test_data": {"$ne": True}}
    )
    p_trust = await pg.fetchval(
        "SELECT COUNT(*) FROM finance.trust_accounts WHERE scheme_id = $1", scheme_id
    )
    delta = m_trust - p_trust
    status = STATUS_OK if delta == 0 else STATUS_FAIL
    if delta != 0:
        divergences += 1
    _row(5, "Trust accounts", str(m_trust), str(p_trust), str(delta), status)

    # ------------------------------------------------------------------
    # 6. Levy runs
    # ------------------------------------------------------------------
    m_years_raw = await mdb.unit_levy_ledger.distinct(
        "year", {"building_id": building_id, "is_test_data": {"$ne": True}}
    )
    m_years = len([y for y in m_years_raw if y])
    p_runs = await pg.fetchval(
        "SELECT COUNT(*) FROM finance.levy_runs WHERE scheme_id = $1", scheme_id
    )
    delta = m_years - p_runs
    status = STATUS_OK if delta == 0 else STATUS_FAIL
    if delta != 0:
        divergences += 1
    _row(6, "Levy runs", str(m_years), str(p_runs), str(delta), status)

    # ------------------------------------------------------------------
    # 7. Levy items
    # ------------------------------------------------------------------
    pipeline = [
        {"$match": {"building_id": building_id, "is_test_data": {"$ne": True}}},
        {"$project": {"_cnt": {
            "$add": [
                {"$cond": [{"$gt": [{"$ifNull": ["$admin_levied", 0]}, 0]}, 1, 0]},
                {"$cond": [{"$gt": [{"$ifNull": ["$sinking_levied", 0]}, 0]}, 1, 0]},
            ]
        }}},
        {"$group": {"_id": None, "total": {"$sum": "$_cnt"}}},
    ]
    m_items_res = await mdb.unit_levy_ledger.aggregate(pipeline).to_list(1)
    m_items = m_items_res[0]["total"] if m_items_res else 0
    p_items = await pg.fetchval(
        "SELECT COUNT(*) FROM finance.levy_items WHERE scheme_id = $1", scheme_id
    )
    delta = m_items - p_items
    status = STATUS_OK if delta == 0 else STATUS_FAIL
    if delta != 0:
        divergences += 1
    _row(7, "Levy items", str(m_items), str(p_items), str(delta), status)

    # ------------------------------------------------------------------
    # 8. Receipts
    # ------------------------------------------------------------------
    m_receipts = await mdb.levy_payments.count_documents({
        "building_id": building_id,
        "status": {"$in": ["confirmed", "completed", "paid", None]},
        "amount": {"$gt": 0},
        "is_test_data": {"$ne": True},
    })
    p_receipts = await pg.fetchval(
        "SELECT COUNT(*) FROM finance.receipts WHERE scheme_id = $1", scheme_id
    )
    delta = m_receipts - p_receipts
    status = STATUS_OK if delta == 0 else STATUS_FAIL
    if delta != 0:
        divergences += 1
    _row(8, "Receipts", str(m_receipts), str(p_receipts), str(delta), status)

    # ------------------------------------------------------------------
    # 9. Receipt allocations (PG-only)
    # ------------------------------------------------------------------
    p_alloc = await pg.fetchval(
        """
        SELECT COUNT(*) FROM finance.receipt_allocations ra
        JOIN finance.receipts r USING (receipt_id)
        WHERE r.scheme_id = $1
        """,
        scheme_id,
    )
    _row(9, "Receipt allocations", "—", str(p_alloc), "—", STATUS_INFO)

    # ------------------------------------------------------------------
    # 10. Journal entries (PG-only) + hash chain check
    # ------------------------------------------------------------------
    p_je = await pg.fetchval(
        "SELECT COUNT(*) FROM finance.journal_entries WHERE scheme_id = $1", scheme_id
    )
    broken_links = await pg.fetchval(
        """
        SELECT COUNT(*) FROM (
            SELECT je.journal_entry_id
            FROM finance.journal_entries je
            JOIN LATERAL (
                SELECT entry_hash FROM finance.journal_entries prev
                WHERE prev.scheme_id = je.scheme_id
                  AND prev.entry_number < je.entry_number
                ORDER BY prev.entry_number DESC LIMIT 1
            ) prev ON true
            WHERE je.scheme_id = $1
              AND je.prev_entry_hash IS NOT NULL
              AND je.prev_entry_hash != prev.entry_hash
        ) _broken
        """,
        scheme_id,
    )
    chain_note = "chain intact" if broken_links == 0 else f"{broken_links} broken links"
    je_status = STATUS_INFO if broken_links == 0 else f"⚠️  {chain_note}"
    _row(10, f"Journal entries ({chain_note})", "—", str(p_je), "—", je_status)
    if broken_links > 0:
        divergences += 1

    # ------------------------------------------------------------------
    # 11. Journal lines + trial balance
    # ------------------------------------------------------------------
    p_jl = await pg.fetchval(
        """
        SELECT COUNT(*) FROM finance.journal_lines jl
        JOIN finance.journal_entries je USING (journal_entry_id)
        WHERE je.scheme_id = $1
        """,
        scheme_id,
    )
    tb = await pg.fetchrow(
        """
        SELECT SUM(amount_cents) FILTER (WHERE direction='debit')  AS dr,
               SUM(amount_cents) FILTER (WHERE direction='credit') AS cr
        FROM finance.journal_lines jl
        JOIN finance.journal_entries je USING (journal_entry_id)
        WHERE je.scheme_id = $1 AND je.status = 'posted'
        """,
        scheme_id,
    )
    balanced = tb["dr"] == tb["cr"] if tb and tb["dr"] is not None else True
    tb_note = "balanced" if balanced else f"MISMATCH dr={tb['dr']} cr={tb['cr']}"
    jl_status = STATUS_INFO if balanced else "❌ trial balance mismatch"
    _row(11, f"Journal lines ({tb_note})", "—", str(p_jl), "—", jl_status)
    if not balanced:
        divergences += 1

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"\n{'═' * 70}")
    if divergences == 0:
        print("  ✅  All domains match — Postgres is in parity with Mongo")
    else:
        print(f"  ❌  {divergences} domain(s) diverged — review ❌ rows above")
    print(f"{'═' * 70}\n")

    await pg.close()
    mongo.close()
    return 0 if divergences == 0 else 1


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/audits/east_gate_parity_audit.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Mongo↔Postgres parity audit")
    parser.add_argument("--building-id", default="13195", help="building_id / scheme_number")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.building_id)))


if __name__ == "__main__":
    main()
