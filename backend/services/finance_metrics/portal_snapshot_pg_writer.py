"""backend/services/finance_metrics/portal_snapshot_pg_writer.py

# @featuretrace:financial-onboarding — dual-write portal snapshots to PG (B2b).
# Layer: service
# Data flow: integrations/portal_adapters.py::PortalBalanceSnapshot (already built by
#            scripts/ingest/strata_web_portal_ingest.py::run()) -> write_pg_portal_snapshot()
#            -> finance.portal_balance_snapshots + finance.portal_balance_snapshot_lines.
# Related: backend/alembic/versions/0083_portal_snapshot_pg.py (schema, B2)
#          backend/scripts/ingest/strata_web_portal_ingest.py (the Mongo write this dual-writes alongside)
#          backend/services/portal_ledger_reconciliation_service.py (still Mongo-read this session --
#              see module docstring "What this does NOT do")
#          tasks/GAP-ONBOARD-004-historical-reconciliation-and-pg-schema-hardening.md (B2b)
# Table: finance.portal_balance_snapshots, finance.portal_balance_snapshot_lines (writes)

Why this exists
----------------
`finance.portal_balance_snapshots` (migration 0083) is additive schema with nothing writing to
it yet. This module is the write side of B2b: given the SAME `PortalBalanceSnapshot` that
`strata_web_portal_ingest.run()` already stages to Mongo `staging_strata_web_snapshots`, also
write it to the PG table -- a dual-write, not a replacement, during the transition period.

It is reconciliation EVIDENCE, never a journal source (same rule as the Mongo staging document):
this module never calls `financial_core` and never posts a journal entry.

Idempotency: the table has no unique constraint (an index only, by design -- see the migration's
own comment), so this module implements an application-level upsert keyed on
(scheme_id, financial_year, snapshot_date), mirroring the Mongo upsert's own key exactly: delete
any existing header (and its lines, via ON DELETE CASCADE) for that key, then insert fresh. A
re-run for the same snapshot_date replaces, never duplicates.

Lot resolution (self-audit fix, 2026-08-10): each line's ``lot_id`` FK is resolved via
``db_postgres.repos.ownership_repo.get_lot_id_by_number()`` -- the existing, canonical
lot_number-or-unit_number lookup -- rather than left permanently NULL. Leaving it NULL was the
original shape; it would have quietly reproduced the exact incident already documented in
``portal_ledger_reconciliation_service.py``'s own docstring (2026-08-06: matching the portal's
bare lot number against the wrong column silently broke reconciliation for every unit) for any
future consumer of this table, since ``lot_number`` is a valid FK target and resolving it once
at write time is strictly better than every reader re-deriving the same fuzzy match. A lot that
cannot be resolved (building not yet CSV-onboarded) still gets its line inserted with
``lot_id=NULL`` -- this is the one case where NULL is correct (unknown, not an error).

What this does NOT do
----------------------
Does NOT change `portal_ledger_reconciliation_service._latest_portal_by_unit()` (or any other
consumer) to read from this table -- reads stay on Mongo `staging_strata_web_snapshots` this
session. Promoting reads to PG is a separate, deliberately later step (flip only after this
dual-write has soaked and been verified to match Mongo exactly for a real building), consistent
with this codebase's general dual-write-then-promote-reads cutover discipline.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_DELETE_HEADER_SQL = text(
    """
    DELETE FROM finance.portal_balance_snapshots
    WHERE scheme_id = CAST(:sid AS UUID) AND financial_year = :fy AND snapshot_date = :snapshot_date
    """
)

_INSERT_HEADER_SQL = text(
    """
    INSERT INTO finance.portal_balance_snapshots
        (snapshot_id, tenant_id, scheme_id, snapshot_date, financial_year, source,
         raw_admin_fund_balance_cents, raw_sinking_fund_balance_cents, raw_arrears_total_cents,
         raw_credit_total_cents, raw_collection_rate, metadata, is_test_data)
    VALUES
        (gen_random_uuid(), CAST(:tid AS UUID), CAST(:sid AS UUID), :snapshot_date, :fy, :source,
         :admin_cents, :sinking_cents, :arrears_cents, :credit_cents, :collection_rate,
         CAST(:metadata AS jsonb), :is_test_data)
    RETURNING snapshot_id::text
    """
)

_INSERT_LINE_SQL = text(
    """
    INSERT INTO finance.portal_balance_snapshot_lines
        (snapshot_line_id, snapshot_id, lot_id, lot_number, balance_cents, status, owner_name)
    VALUES
        (gen_random_uuid(), CAST(:snapshot_id AS UUID), CAST(:lot_id AS UUID), :lot_number,
         :balance_cents, :status, :owner_name)
    """
)


async def write_pg_portal_snapshot(
    session: AsyncSession,
    snapshot: dict[str, Any],
    *,
    scheme_id: str,
    tenant_id: str,
) -> str:
    """Dual-write a staged portal snapshot dict (the exact shape
    ``PortalBalanceSnapshot.to_staging_document()`` produces) into PG. Caller must already have
    called ``set_tenant(session, tenant_id)`` and must commit (this function does not commit).

    Returns the new ``snapshot_id``. Deletes and replaces any existing PG row for the same
    (scheme_id, financial_year, snapshot_date) -- see module docstring on idempotency.
    """
    await session.execute(
        _DELETE_HEADER_SQL,
        {"sid": scheme_id, "fy": str(snapshot["financial_year"]), "snapshot_date": snapshot["snapshot_date"]},
    )

    metadata_json = "{}"
    header_result = await session.execute(
        _INSERT_HEADER_SQL,
        {
            "tid": tenant_id,
            "sid": scheme_id,
            "snapshot_date": snapshot["snapshot_date"],
            "fy": str(snapshot["financial_year"]),
            "source": snapshot["source"],
            "admin_cents": int(snapshot.get("raw_admin_fund_balance_cents") or 0),
            "sinking_cents": int(snapshot.get("raw_sinking_fund_balance_cents") or 0),
            "arrears_cents": int(snapshot.get("raw_arrears_total_cents") or 0),
            "credit_cents": int(snapshot.get("raw_credit_total_cents") or 0),
            "collection_rate": snapshot.get("raw_collection_rate"),
            "metadata": metadata_json,
            "is_test_data": bool(snapshot.get("is_test_data") or False),
        },
    )
    snapshot_id = header_result.scalar()

    from db_postgres.repos.ownership_repo import get_lot_id_by_number

    for line in snapshot.get("per_unit_balances") or []:
        lot_number = str(line["lot_number"])
        lot_id = await get_lot_id_by_number(session, scheme_id, lot_number)
        await session.execute(
            _INSERT_LINE_SQL,
            {
                "snapshot_id": snapshot_id,
                "lot_id": lot_id,
                "lot_number": lot_number,
                "balance_cents": int(line.get("balance_cents") or 0),
                "status": line.get("status"),
                "owner_name": line.get("owner_name"),
            },
        )

    return snapshot_id
