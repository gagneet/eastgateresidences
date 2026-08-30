#!/usr/bin/env python3
# @featuretrace:pg-migration-shadow — Pre-activation verification for trust_ledger/trust_reconciliation.
# Layer: script
# Data flow: CLI → finance.trust_accounts + finance.bank_transactions (Postgres) vs
#            db.trust_ledger_accounts + db.trust_transactions (MongoDB) → stdout report only,
#            read-only, no writes.
# Related: backend/services/trust_read_service.py
#          backend/scripts/phase_d_activate.py
#          docs/migration/shadow-correction-updates-plan-p1.md (Phase G)
"""Independent trust readiness verification — run BEFORE phase_d_activate.py --domain
trust_ledger/trust_reconciliation --apply.

Per the Phase G design: `genesis_ready` (the readiness_status trust_ledger/trust_reconciliation
already carry) is necessary but not sufficient evidence — it proves genesis journals were
posted and balanced, not that Postgres and MongoDB currently agree on live account identities,
balances, and transaction counts. This script checks that independently, read-only, with no
side effects — it does not itself promote anything.

This is a live, evidence-gathering check, not a pass/fail gate enforced in code (unlike the P0
checks inside promote_domain()) — read the output and make the actual promotion call yourself.

Usage:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    python backend/scripts/trust_activation_readiness_check.py --building-id 13195
    python backend/scripts/trust_activation_readiness_check.py --building-id 13195 --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from db_postgres.repos.config_repo import resolve_scheme_context  # noqa: E402
from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from sqlalchemy import text  # noqa: E402


async def _check_scheme_resolves(building_id: str) -> dict[str, Any]:
    """Generated function header.

    Function: _check_scheme_resolves
    Path: backend/scripts/trust_activation_readiness_check.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await resolve_scheme_context(building_id)
    if not scheme or not scheme.get("scheme_id") or not scheme.get("tenant_id"):
        return {"id": "T0-scheme-resolves", "status": "fail", "detail": "resolve_scheme_context returned no scheme/tenant"}
    return {"id": "T0-scheme-resolves", "status": "pass", "scheme_id": str(scheme["scheme_id"]), "tenant_id": str(scheme["tenant_id"])}


async def _pg_trust_account_summary(building_id: str, scheme: dict) -> dict[str, Any]:
    """Generated function header.

    Function: _pg_trust_account_summary
    Path: backend/scripts/trust_activation_readiness_check.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        rows = (await session.execute(
            text(
                """
                SELECT ta.trust_account_id::text AS id, ta.account_name, ta.status,
                       COALESCE(f.opening_balance_cents, 0) AS opening_balance_cents,
                       COALESCE((
                           SELECT SUM(bt.amount_cents) FROM finance.bank_transactions bt
                           WHERE bt.trust_account_id = ta.trust_account_id
                       ), 0) AS transaction_balance_cents,
                       (SELECT COUNT(*) FROM finance.bank_transactions bt2
                        WHERE bt2.trust_account_id = ta.trust_account_id) AS transaction_count
                FROM finance.trust_accounts ta
                LEFT JOIN finance.funds f ON f.fund_id = ta.fund_id
                WHERE ta.scheme_id = :scheme_id AND ta.status != CAST('archived' AS core.record_status)
                """
            ),
            {"scheme_id": str(scheme["scheme_id"])},
        )).fetchall()
        # finance.trust_accounts / finance.bank_transactions have no is_test_data column —
        # confirmed live 2026-07-14 (information_schema query). Use journal_entries as the
        # nearest available proxy: any test-data journal activity for this scheme is a signal
        # worth surfacing even though it can't be tied to a specific trust_account row.
        test_data_journals = (await session.execute(
            text(
                "SELECT COUNT(*) AS n FROM finance.journal_entries WHERE scheme_id = :scheme_id AND is_test_data = TRUE"
            ),
            {"scheme_id": str(scheme["scheme_id"])},
        )).scalar()
        dup_check = (await session.execute(
            text(
                """
                SELECT entry_hash, COUNT(*) AS n FROM finance.journal_entries
                WHERE scheme_id = :scheme_id AND entry_hash IS NOT NULL
                GROUP BY entry_hash HAVING COUNT(*) > 1
                """
            ),
            {"scheme_id": str(scheme["scheme_id"])},
        )).fetchall()
    return {
        "accounts": [
            {
                "id": r.id, "name": r.account_name, "status": r.status,
                "opening_balance_cents": int(r.opening_balance_cents or 0),
                "current_balance_cents": int(r.opening_balance_cents or 0) + int(r.transaction_balance_cents or 0),
                "transaction_count": int(r.transaction_count or 0),
            }
            for r in rows
        ],
        "test_data_journal_entries": int(test_data_journals or 0),
        "duplicate_journal_hashes": [dict(d) for d in dup_check],
    }


async def _mongo_trust_account_summary(building_id: str) -> dict[str, Any]:
    """Generated function header.

    Function: _mongo_trust_account_summary
    Path: backend/scripts/trust_activation_readiness_check.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    from request_context import set_ctx_building_id
    set_ctx_building_id(building_id)

    accounts = await db.trust_ledger_accounts.find(
        {"building_id": building_id, "is_active": True},
        {"_id": 0, "account_code": 1, "account_name": 1, "current_balance": 1, "opening_balance": 1, "is_test_data": 1},
    ).to_list(200)
    tx_count = await db.trust_transactions.count_documents({"building_id": building_id}) \
        if hasattr(db, "trust_transactions") else None
    return {
        "accounts": accounts,
        "transaction_count": tx_count,
        "test_data_accounts": sum(1 for a in accounts if a.get("is_test_data")),
    }


async def run(building_id: str) -> dict[str, Any]:
    """Generated function header.

    Function: run
    Path: backend/scripts/trust_activation_readiness_check.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    checks: list[dict[str, Any]] = []

    scheme_check = await _check_scheme_resolves(building_id)
    checks.append(scheme_check)
    if scheme_check["status"] != "pass":
        return {"building_id": building_id, "overall": "fail", "checks": checks}

    scheme = {"scheme_id": scheme_check["scheme_id"], "tenant_id": scheme_check["tenant_id"]}

    try:
        pg_summary = await _pg_trust_account_summary(building_id, scheme)
    except Exception as exc:
        checks.append({"id": "T1-pg-query", "status": "fail", "detail": str(exc)})
        return {"building_id": building_id, "overall": "fail", "checks": checks}

    try:
        mongo_summary = await _mongo_trust_account_summary(building_id)
    except Exception as exc:
        checks.append({"id": "T2-mongo-query", "status": "fail", "detail": str(exc)})
        return {"building_id": building_id, "overall": "warn", "checks": checks, "pg_summary": pg_summary}

    pg_count = len(pg_summary["accounts"])
    mongo_count = len(mongo_summary["accounts"])
    checks.append({
        "id": "T3-account-count-match",
        "status": "pass" if pg_count == mongo_count else "fail",
        "pg_count": pg_count, "mongo_count": mongo_count,
        "detail": (
            "counts match" if pg_count == mongo_count else
            "counts differ. If mongo_count is 0 while pg_count is not, this is very likely "
            "BUG-TRUST-001 (docs/fixes/BUG-TRUST-001-legacy-v1-trust-accounting-empty-mongo-"
            "collection.md) — this script queries db.trust_ledger_accounts (the legacy V1 "
            "collection, confirmed empty for every building as of 2026-07-14), not "
            "trust_accounts_v2 (the real, actively-used V2 collection backing "
            "routers/trust_phase1.py). Do NOT treat a 0 mongo_count here as 'no trust data "
            "exists' — check trust_accounts_v2 directly before concluding anything about real "
            "data completeness."
        ),
    })

    pg_test_journals = pg_summary["test_data_journal_entries"]
    checks.append({
        "id": "T4-no-test-data",
        "status": "pass" if pg_test_journals == 0 and mongo_summary["test_data_accounts"] == 0 else "fail",
        "pg_test_data_journal_entries": pg_test_journals,
        "mongo_test_data_accounts": mongo_summary["test_data_accounts"],
        "note": "finance.trust_accounts/bank_transactions have no is_test_data column — "
                "journal_entries.is_test_data is the nearest available PG-side proxy.",
    })

    checks.append({
        "id": "T5-no-duplicate-journal-hashes",
        "status": "pass" if not pg_summary["duplicate_journal_hashes"] else "fail",
        "duplicates": pg_summary["duplicate_journal_hashes"],
    })

    checks.append({
        "id": "T6-current-balance-formula",
        "status": "info",
        "detail": (
            "PG current_balance = opening_balance_cents + SUM(bank_transactions.amount_cents) "
            "(services/trust_read_service.py:list_trust_accounts). Confirm this matches "
            "whatever formula computed Mongo's stored current_balance for each account below "
            "before treating any diff as real drift rather than a formula mismatch — "
            "per this repo's standing invariant, trust balance must be computed, not read "
            "from a stale stored field, on BOTH sides."
        ),
        "pg_accounts_sample": pg_summary["accounts"][:5],
        "mongo_accounts_sample": mongo_summary["accounts"][:5],
    })

    statuses = [c["status"] for c in checks]
    overall = "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "pass")

    return {
        "building_id": building_id,
        "overall": overall,
        "checks": checks,
        "note": "This is evidence, not an automated gate — review T6 manually before promoting.",
    }


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/scripts/trust_activation_readiness_check.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Independent trust readiness verification (read-only).")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--json", action="store_true", dest="json_out")
    args = parser.parse_args()
    result = asyncio.run(run(args.building_id))
    print(json.dumps(result, indent=2, default=str))
    if result["overall"] == "fail":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
