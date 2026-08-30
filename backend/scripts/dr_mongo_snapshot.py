#!/usr/bin/env python3
# @featuretrace:financial_core — Periodic PostgreSQL -> MongoDB DR snapshot (right-sized design).
# Layer: script
# Data flow: finance.levy_items/receipts/journal_lines (via FinancialReadService.get_unit_levy_balance_list,
#            the same canonical function the live route uses) -> scoped $set (own fields only,
#            GAP-FIN-068) per unit -> db.unit_levy_ledger + db.dr_snapshot_meta (building-scoped).
# Related: backend/services/financial_read_service.py (canonical PG read, reused verbatim)
#          backend/routers/finance.py::_get_unit_dashboard_overview_mongo_fallback (the consumer
#            of unit_levy_ledger this snapshot keeps fresh)
#          backend/services/finance_pg_read_dr.py (the freshness-gated fallback that reads
#            dr_snapshot_meta before trusting this snapshot)
#          docs/architecture/financial-postgres-cutover-status-2026-08-11.md §3/§4
#          tasks/GAP-FIN-066-postgres-mongo-operational-projector.md (superseded design, kept as
#            the reasoning trail for why this simpler approach was chosen instead)
"""Periodic, route-shaped, SCOPED-FIELD-SET snapshot of PostgreSQL financial data into MongoDB.

Right-sized DR design (2026-08-11): NOT an event-sourced projector (no per-event idempotency
keys, no tombstones, no aggregate versioning) -- this app's real scale (1 production building, 87
lots, non-critical reporting/automation tool) doesn't justify that. Every run reads the CURRENT
COMPLETE state per unit from Postgres and `$set`s the corresponding Mongo document's OWNED fields
(the 4 balance fields this script computes, plus its own snapshot-provenance fields) -- so a unit
whose PG values changed -- e.g. a reversed payment moving net_balance back up -- is never left
showing a stale value in any field this script owns.

GAP-FIN-068 (2026-08-18, fixed): this previously used `replace_one()` -- a WHOLESALE document
replace, not a scoped `$set` -- which achieved "no stale value in an OWNED field" the same way
`$set` does, but as a side effect also silently DELETED every field this script does NOT own
(`uoe`, `lot_number`, `admin_paid`/`sinking_paid`/`admin_opening`/`sinking_opening`, and others)
whenever it ran. Confirmed live: all 87 East Gate `unit_levy_ledger` documents for FY2026 were
reduced from their original ~21-field shape (written by the onboarding/reconstruction pipeline)
to this script's own 9-field shape, silently breaking `finance.levy_kpi` (and any other consumer
depending on those dropped fields) for the current financial year, every 15 minutes, since this
script's introduction. Switched to `update_one(..., {"$set": doc}, upsert=True)` -- functionally
identical for a NEW document (creates the same 9-field doc `replace_one` would have) and identical
for this script's OWN 4 balance fields on an EXISTING document (always overwritten fresh, so still
never stale), but now leaves every field this script doesn't compute untouched instead of wholesale
deleting it. See `tasks/GAP-FIN-068-dr-mongo-snapshot-clobbers-current-year-unit-levy-ledger-fields.md`.

Scope boundary, deliberate: a unit entirely ABSENT from Postgres's per-lot list (its lot
deleted/archived, or simply not yet CSV-onboarded to Postgres -- common during this app's phased
per-building onboarding, not rare) is never touched here, including never deleted from Mongo.
Deleting it would be actively wrong: that Mongo data may be the only source of truth for a unit
Postgres doesn't cover yet. This script only handles the common real case (a reported unit's
VALUES changing), not "a unit stops being reported at all."

Deliberately reuses FinancialReadService.get_unit_levy_balance_list() -- the SAME canonical,
already-correctness-verified per-unit query the live `finance.unit_dashboard_overview` PG-read
path uses -- rather than writing a second, parallel computation of levied/paid/balance. Two
implementations of "this unit's balance" computed independently is exactly the class of bug this
codebase has been burned by before (GAP-FIN-030's addendum). If PG's own computation is wrong, that
is a shadow-diff/reconciliation problem for the route-cutover comparators to catch -- this script's
only job is making sure Mongo reflects whatever PG currently says, faithfully and completely.

Writes two things per (building_id, financial_year) run:
  1. db.unit_levy_ledger -- one replace_one() per unit, keyed on (building_id, unit_number, year).
  2. db.dr_snapshot_meta -- one document per (building_id, route_key), replaced each run, carrying
     the evidence read_pg_first_with_mongo_dr()'s freshness gate checks before trusting this data
     as a DR fallback: snapshot_id, started_at, completed_at, row_count, control_total_cents,
     payload_hash, schema_version, reconciliation_status.

reconciliation_status only becomes "ok" after every unit's Mongo write is confirmed AND a fresh
read-back from Mongo sums to the same control total computed at snapshot time -- this verifies the
write actually landed correctly, not that PG's own business logic is correct (a different,
already-covered concern -- see the route-cutover shadow comparators).

Usage:
    cd backend && venv/bin/python3 scripts/dr_mongo_snapshot.py --building 13195 [--year 2026] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "backend" / ".env")

logger = logging.getLogger("dr_mongo_snapshot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

ROUTE_KEY = "finance.unit_dashboard_overview"
SCHEMA_VERSION = 1


def _payload_hash(units: list[dict]) -> str:
    """Deterministic hash of the snapshot content, for the metadata record's own audit trail."""
    canonical = json.dumps(units, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def snapshot_unit_levy_ledger(
    building_id: str,
    financial_year: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Snapshot one building/year's unit levy balances from Postgres into Mongo.

    Returns the snapshot metadata document (whether or not it was actually written -- dry_run
    still computes and returns it for inspection, just skips the Mongo writes).
    """
    from database import db
    from request_context import set_ctx_building_id
    from services.financial_read_service import FinancialReadService

    set_ctx_building_id(building_id)
    read_service = FinancialReadService()

    resolved_year = financial_year or str(date.today().year)
    started_at = datetime.now(tz=timezone.utc)
    snapshot_id = str(uuid.uuid4())

    balances = await read_service.get_unit_levy_balance_list(
        building_id=building_id, financial_year=resolved_year,
    )
    # Audit finding (2026-08-11): get_unit_levy_balance_list() returns None when the building has
    # NO lots at all, but returns [] (empty list, NOT None) when the building HAS lots and every
    # single per-unit read failed (e.g. a systemic PG issue affecting every unit's
    # _get_lot_id/_get_financial_year_window call) -- see its own `[b for b in balances if b is
    # not None]` filter. The original version of this function only checked `balances is None`,
    # so a building-wide failure that happened to return [] for every unit would have been
    # reported as reconciliation_status="ok" with row_count=0 -- a false "healthy, fresh, 0-unit
    # snapshot" masking what could be a real systemic outage, and the freshness gate in
    # finance_pg_read_dr.py would have treated that false "ok" as valid grounds to serve DR
    # fallback. Treating an empty result the same as "no data" either way is the conservative,
    # correct behaviour regardless of which of the two causes produced it.
    if not balances:
        logger.info("No PG unit balances for building=%s year=%s; nothing to snapshot", building_id, resolved_year)
        return {
            "building_id": building_id,
            "route_key": ROUTE_KEY,
            "financial_year": resolved_year,
            "snapshot_id": snapshot_id,
            "started_at": started_at,
            "completed_at": datetime.now(tz=timezone.utc),
            "row_count": 0,
            "control_total_cents": 0,
            "payload_hash": _payload_hash([]),
            "schema_version": SCHEMA_VERSION,
            "reconciliation_status": "no_data",
        }

    # AUD dollar floats are this collection's existing (documented, known-violation) precision --
    # matched here rather than silently introducing a cents-based sibling field, per CLAUDE.md's
    # "convert once, at the read boundary, don't invent a third representation" rule. Rounded to
    # the cent for the control total specifically, since that's a reconciliation figure, not a
    # value used in downstream financial calculations.
    #
    # Known, honest limitation (audit note, 2026-08-11): this sums `balances` in the order
    # get_unit_levy_balance_list() returned it, but the read-back sum below iterates whatever
    # order Mongo's find() returns (find() with no explicit sort() is NOT guaranteed to match
    # insertion order). Float addition is not strictly associative, so a different summation
    # order can -- in principle -- round to a different cent value. In practice this is not a
    # real risk for a few dozen values with 2 decimal places (the rounding error floor is far
    # below one cent), and a false mismatch here only ever causes an overly-cautious
    # "control_total_mismatch" (never a false "ok") -- but it is not rigorously guaranteed
    # identical, and is named here rather than silently assumed.
    control_total_cents = round(sum(b["closing_balance"] for b in balances) * 100)

    if not dry_run:
        write_errors = 0
        for b in balances:
            # GAP-FIN-068: these are the ONLY fields this script owns/computes -- $set them
            # fresh every run (so none of them can go stale, same guarantee replace_one gave)
            # WITHOUT wholesale-replacing the document and silently deleting sibling fields
            # (uoe, lot_number, admin/sinking splits, ...) this script has no data for and
            # other consumers (finance.levy_kpi) depend on. building_id/unit_number/year are
            # also the upsert filter's own keys -- $set-ing them too is required so that
            # upsert=True on a genuinely NEW document creates it with those key fields
            # present, matching what replace_one would have created.
            doc = {
                "building_id": building_id,
                "unit_number": b["unit_number"],
                "year": b["financial_year"],
                "total_levied": b["levied_amount"],
                "total_paid": b["paid_amount"],
                "net_balance": b["closing_balance"],
                "opening_balance": b["opening_balance"],
                "_dr_snapshot_id": snapshot_id,
                "_dr_snapshot_at": started_at,
            }
            try:
                await db.unit_levy_ledger.update_one(
                    {"building_id": building_id, "unit_number": b["unit_number"], "year": b["financial_year"]},
                    {"$set": doc},
                    upsert=True,
                )
            except Exception:
                logger.exception(
                    "DR snapshot write failed for building=%s unit=%s year=%s",
                    building_id, b["unit_number"], b["financial_year"],
                )
                write_errors += 1

        # Reconciliation: read back what was just written and confirm it sums to the same
        # control total -- verifies the writes actually landed, independent of whether PG's own
        # business logic is correct (a separate, already-covered concern).
        readback = await db.unit_levy_ledger.find(
            {"building_id": building_id, "year": resolved_year, "_dr_snapshot_id": snapshot_id},
            {"_id": 0, "net_balance": 1},
        ).to_list(len(balances) + 10)
        readback_total_cents = round(sum(r.get("net_balance", 0) for r in readback) * 100)

        if write_errors:
            reconciliation_status = "write_failed"
        elif len(readback) != len(balances):
            reconciliation_status = "row_count_mismatch"
        elif readback_total_cents != control_total_cents:
            reconciliation_status = "control_total_mismatch"
        else:
            reconciliation_status = "ok"
    else:
        reconciliation_status = "dry_run"

    completed_at = datetime.now(tz=timezone.utc)
    meta = {
        "building_id": building_id,
        "route_key": ROUTE_KEY,
        "financial_year": resolved_year,
        "snapshot_id": snapshot_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "row_count": len(balances),
        "control_total_cents": control_total_cents,
        "payload_hash": _payload_hash(balances),
        "schema_version": SCHEMA_VERSION,
        "reconciliation_status": reconciliation_status,
    }

    if not dry_run:
        await db.dr_snapshot_meta.replace_one(
            {"building_id": building_id, "route_key": ROUTE_KEY},
            meta,
            upsert=True,
        )

    logger.info(
        "DR snapshot building=%s year=%s rows=%d status=%s%s",
        building_id, resolved_year, len(balances), reconciliation_status,
        " (dry-run, not written)" if dry_run else "",
    )
    return meta


async def _main(building_id: str, year: str | None, dry_run: bool) -> None:
    meta = await snapshot_unit_levy_ledger(building_id, year, dry_run=dry_run)
    if meta["reconciliation_status"] not in ("ok", "no_data", "dry_run"):
        logger.error("DR snapshot did not reconcile: %s", meta)
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--building", required=True, dest="building_id")
    parser.add_argument("--year", default=None, help="Financial year (default: current calendar year)")
    parser.add_argument("--dry-run", action="store_true", help="Compute but do not write to Mongo")
    args = parser.parse_args()
    asyncio.run(_main(args.building_id, args.year, args.dry_run))
