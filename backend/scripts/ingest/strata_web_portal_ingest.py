#!/usr/bin/env python3
# @featuretrace:strata-web-portal-finance-ingest — Stages Strata Web portal scrape snapshots for finance review.
# Data flow: CLI -> Mongo strata_financials/strata_owners/bank_accounts/building_summaries -> staging_strata_web_snapshots.
# Related: backend/routers/strata_web_portal.py (sync trigger + staging); backend/services/portal_ledger_reconciliation_service.py (snapshot consumer)
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from database import db  # noqa: E402
from integrations.portal_adapters import CIVIUM_PORTAL  # noqa: E402
# Back-compat re-exports: these helpers moved to integrations.portal_adapters (the PortalAdapter
# seam, GAP-ONBOARD-003). Kept importable here for any caller that referenced them.
from integrations.portal_adapters import (  # noqa: E402,F401
    cents as _cents,
    date_part as _date_part,
    year_candidates as _year_candidates,
)

SOURCE = CIVIUM_PORTAL


async def build_snapshot(building_id: str, financial_year: str, snapshot_date: str | None = None) -> dict[str, Any]:
    """Build the canonical portal snapshot for a building via its selected PortalAdapter.

    GAP-ONBOARD-003: the Civium-specific gather logic moved into
    `integrations.portal_adapters.CiviumPortalAdapter`; this now routes through the provider
    registry so a building can use any portal provider that emits the same typed evidence. Returns
    the staging-document dict (unchanged shape) for back-compat with `run()` and any other caller.
    """
    from integrations.registry import get_provider_registry

    adapter = await get_provider_registry().get_portal_adapter(building_id)
    snapshot = await adapter.build_snapshot(building_id, financial_year, snapshot_date)
    return snapshot.to_staging_document()


# @featuretrace:levy — Entry point of the "Strata Web scrape" chain; this is a CLI aggregator over
#                      already-populated Mongo collections, not a live scraper of the Strata Web site.
# Layer: cron
# Data flow: CLI (--apply) -> build_snapshot() reads bank_accounts/building_summaries/strata_owners
#            -> run() -> staging_strata_web_snapshots (building-scoped).
# Related: backend/routers/strata_web_portal.py (POST /settings/strata-web-portal/sync trigger + GET/PUT config)
#           backend/services/portal_ledger_reconciliation_service.py + backend/routers/finance_reconciliation.py
#           (the staged-snapshot consumers: reconcile the portal balance against the constructed ledger)
# Collection: bank_accounts, building_summaries, strata_owners, staging_strata_web_snapshots

# IMPORTANT: the actual screen-scrape of my.civium.com.au happens outside this repository and
#            populates bank_accounts/building_summaries/strata_owners by an out-of-repo process.
#            That out-of-repo scraper now reads PER-BUILDING connection details (portal URL,
#            scheme, username, decrypted password) from
#            services.strata_web_portal_config_service.get_portal_credentials(building_id) —
#            the replacement for the legacy global PORTAL_EMAIL/PORTAL_PASSWORD env vars. The
#            sync trigger (POST /settings/strata-web-portal/sync) invokes the scraper for the
#            selected building (when the integration package is installed) and then calls run()
#            below to stage the snapshot.
#            A staged snapshot is reconciliation EVIDENCE, not a journal: portal_ledger_reconciliation_service
#            computes per-unit variance (Bc - P) against the constructed unit_levy_ledger and persists
#            exceptions to reconciliation_unit_exceptions (surfaced at GET /finance/reconciliation/analysis).
#            It NEVER writes annual_levies/unit_levy_ledger — a scrape never overwrites a journal (Financial
#            Evidence Gateway rule); reconciled figures are posted separately through the evidence pipeline.
async def run(building_id: str, financial_year: str, snapshot_date: str | None, dry_run: bool) -> dict[str, Any]:
    """Stage a portal snapshot into Mongo `staging_strata_web_snapshots` (the existing,
    consumed-by-reads path) and, best-effort, dual-write the same snapshot into PostgreSQL
    `finance.portal_balance_snapshots` (GAP-ONBOARD-004 B2b -- additive, PG reads not wired yet).

    The Mongo write is authoritative and unconditional; the PG dual-write is defensive -- a
    failure there is logged and reported in the result, never raised, so this function's
    long-standing Mongo-staging contract (used by `POST /settings/strata-web-portal/sync`)
    cannot regress because of the new, additive PG path.
    """
    snapshot = await build_snapshot(building_id, financial_year, snapshot_date)
    key = {"building_id": building_id, "financial_year": str(financial_year), "snapshot_date": snapshot["snapshot_date"]}
    existing = await db._db.staging_strata_web_snapshots.find_one(key)
    if dry_run:
        return {"dry_run": True, "would_upsert": key, "existing": bool(existing), "snapshot": snapshot}
    await db._db.staging_strata_web_snapshots.update_one(key, {"$set": snapshot}, upsert=True)

    pg_result: dict[str, Any] = {"attempted": False}
    try:
        from db_postgres.repos.config_repo import resolve_scheme_context
        from db_postgres.session import async_session_context, set_tenant
        from services.finance_metrics.portal_snapshot_pg_writer import write_pg_portal_snapshot

        scheme = await resolve_scheme_context(building_id)
        if scheme:
            pg_result["attempted"] = True
            async with async_session_context() as pg_session:
                await set_tenant(pg_session, str(scheme["tenant_id"]))
                pg_snapshot_id = await write_pg_portal_snapshot(
                    pg_session, snapshot,
                    scheme_id=str(scheme["scheme_id"]), tenant_id=str(scheme["tenant_id"]),
                )
                await pg_session.commit()
            pg_result["snapshot_id"] = pg_snapshot_id
        else:
            pg_result["skipped_reason"] = "no PostgreSQL scheme for this building_id yet"
    except Exception as exc:  # pragma: no cover - defensive, never breaks the Mongo staging contract
        pg_result["error"] = f"{type(exc).__name__}: {exc}"

    return {
        "dry_run": False, "upserted": key, "replaced_existing": bool(existing),
        "per_unit_count": len(snapshot["per_unit_balances"]), "pg_dual_write": pg_result,
    }


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/ingest/strata_web_portal_ingest.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Stage a dated Strata Web portal scrape snapshot.")
    parser.add_argument("--building-id", default="13195")
    parser.add_argument("--year", required=True)
    parser.add_argument("--snapshot-date")
    parser.add_argument("--apply", action="store_true", help="Write staging_strata_web_snapshots. Default is dry-run.")
    args = parser.parse_args()
    result = asyncio.run(run(args.building_id, args.year, args.snapshot_date, dry_run=not args.apply))
    import json
    print(json.dumps(result, default=str, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
