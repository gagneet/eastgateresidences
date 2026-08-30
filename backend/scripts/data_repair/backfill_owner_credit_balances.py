#!/usr/bin/env python3
# @featuretrace:financial_core — backfill finance.owner_credit_balances from the canonical balance owner.
# Layer: script
# Data flow: compute_lot_true_balances (canonical) -> finance.owner_credit_balances (building-scoped).
# Related: backend/services/finance_metrics/lot_true_balance.py (canonical owner)
#          backend/services/financial_core/adapters/db_postgres/ledger_repo.py (upsert_owner_credit — the write path)
#          docs/architecture/canonical_owners.yaml (concept: lot-true-balance)
# Collection: finance.owner_credit_balances, finance.receipts, finance.levy_items, core.lots
# Tests: tests/backend/test_backfill_owner_credit_balances.py
"""Backfill ``finance.owner_credit_balances`` for a building's historical over-payments.

Why this exists
---------------
``finance.owner_credit_balances`` was created by migration 0004 and given an RLS
policy by 0008, then left with **no writer for its whole life** — a schema-only
table. PR #736 finally added one (``ledger_repo.upsert_owner_credit``), but it
fires on *allocation*, so it only ever records credit arising from that moment
forward. Every over-payment East Gate already held stayed invisible.

The visible consequence: PostgreSQL derives a lot's position from
``levy_items`` as ``charged - paid``, and ``paid_cents`` never exceeds the
charge, so **PG cannot represent a negative balance at all**. Measured against
the operator's portal position on 2026-08-28: the portal showed 34 lots holding
$35,675.42 of credit; PG showed **zero lots in credit**, because there was
nowhere for it to live.

What it does
------------
Reads each lot's true position from ``compute_lot_true_balances`` — the
canonical owner of that concept per ``docs/architecture/canonical_owners.yaml``
— and writes the credit side into ``owner_credit_balances``.

Calling the canonical owner is the whole point, not a style preference. The
formula is easy and the *filters* are the hard part: the second hand-written
copy of this calculation omitted the reversal-parity join and the ``retired_at``
check and reported $19,000-$30,000 of phantom credit on lots holding none. A
locally written ``GREATEST(0, received - levied)`` here would repeat that
exactly, and the registry's detector would fail the build for it.

Safety
------
* Dry-run by default; ``--apply`` is required to write.
* Idempotent: re-running SETS the stored figure to the recomputed one rather
  than accumulating, so a second run is a no-op when nothing changed. This
  differs deliberately from ``upsert_owner_credit``, which ACCUMULATES because
  it records one allocation event; this script states a whole-of-lot position.
* Never touches receipts, levy_items, journals or allocations — it only
  populates a table that has been empty since it was created.
* ``owner_party_id`` is resolved from the lot's current ownership period. A lot
  with no resolvable owner is reported and skipped, never guessed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

from db_postgres.session import async_session_context, set_tenant  # noqa: E402
from services.finance_metrics.lot_true_balance import compute_lot_true_balances  # noqa: E402


def _fmt(cents: int) -> str:
    return f"${cents / 100:,.2f}"


async def _resolve_scheme(session, building_id: str) -> tuple[str, str]:
    """Return (scheme_id, tenant_id) for a building's plan number.

    Never derive a tenant_id — resolve it from core.schemes (footgun #16).

    The bypass sentinel is set first because ``core.schemes`` RLS only admits a
    session under it or under the row's own tenant — and the row's tenant is the
    very thing being looked up. Without it this returns zero rows and *no error*,
    which reads exactly like "the building does not exist" (footgun #8).
    """
    await set_tenant(session, "00000000-0000-0000-0000-000000000000")
    row = (
        await session.execute(
            text(
                "SELECT scheme_id::text, tenant_id::text FROM core.schemes "
                "WHERE scheme_number = :bid AND is_test_data = FALSE"
            ),
            {"bid": building_id},
        )
    ).first()
    if not row:
        raise SystemExit(f"No scheme found for building_id={building_id!r}")
    return row[0], row[1]


async def backfill(building_id: str, financial_year: str, apply: bool) -> int:
    async with async_session_context() as session:
        # core.schemes carries an RLS bypass clause; the finance tables do not, so
        # the tenant context below is what makes the balance query return rows at
        # all (a bare connection silently reports zero — CLAUDE.md footgun #8).
        scheme_id, tenant_id = await _resolve_scheme(session, building_id)
        await set_tenant(session, tenant_id)

        balances = await compute_lot_true_balances(
            session,
            scheme_id=scheme_id,
            tenant_id=tenant_id,
            financial_year=financial_year,
        )

        # Current owner per lot. `valid_to IS NULL AND recorded_to IS NULL` is the
        # bitemporal definition of "current" — a valid_to-only filter reports
        # superseded owners as live (CLAUDE.md, ownership section).
        owners = {
            r.lot_id: r.owner_party_id
            for r in (
                await session.execute(
                    text(
                        """
                        SELECT lot_id::text AS lot_id, owner_party_id::text AS owner_party_id
                          FROM core.ownership_periods
                         WHERE tenant_id = :tid
                           AND valid_to IS NULL
                           AND recorded_to IS NULL
                        """
                    ),
                    {"tid": tenant_id},
                )
            ).fetchall()
        }

        units = {
            r.lot_id: r.unit_number
            for r in (
                await session.execute(
                    text(
                        "SELECT lot_id::text AS lot_id, unit_number FROM core.lots "
                        "WHERE tenant_id = :tid"
                    ),
                    {"tid": tenant_id},
                )
            ).fetchall()
        }

        with_credit = {
            lot_id: bal for lot_id, bal in balances.items() if bal.unapplied_credit_cents > 0
        }
        unowned = [lot_id for lot_id in with_credit if lot_id not in owners]
        writable = {k: v for k, v in with_credit.items() if k in owners}
        total = sum(b.unapplied_credit_cents for b in writable.values())

        print("=" * 74)
        print(f"owner_credit_balances backfill — building {building_id}, FY{financial_year}")
        print("=" * 74)
        print(f"  lots with a PG ledger position : {len(balances)}")
        print(f"  lots holding unapplied credit  : {len(with_credit)}")
        print(f"  writable (owner resolvable)    : {len(writable)}  {_fmt(total)}")
        if unowned:
            print(f"  SKIPPED — no current owner     : {len(unowned)}")
            for lot_id in unowned[:10]:
                print(f"      lot {units.get(lot_id, lot_id)} credit "
                      f"{_fmt(with_credit[lot_id].unapplied_credit_cents)}")

        for lot_id, bal in sorted(writable.items(), key=lambda kv: -kv[1].unapplied_credit_cents)[:15]:
            print(f"      {units.get(lot_id, lot_id):<8} {_fmt(bal.unapplied_credit_cents)}")

        if not apply:
            print("\n  DRY-RUN — re-run with --apply to write.")
            return 0

        written = 0
        for lot_id, bal in writable.items():
            # SET, not accumulate: this states the lot's whole-of-year position, so a
            # re-run must converge rather than double. `upsert_owner_credit` on the
            # live write path accumulates instead, because it records one event.
            await session.execute(
                text(
                    """
                    INSERT INTO finance.owner_credit_balances
                        (tenant_id, scheme_id, lot_id, owner_party_id, fund_id,
                         available_cents, created_at, updated_at)
                    VALUES
                        (:tid, :sid, :lot, :owner, NULL, :amt, NOW(), NOW())
                    ON CONFLICT (scheme_id, lot_id, owner_party_id)
                        WHERE fund_id IS NULL
                    DO UPDATE SET available_cents = EXCLUDED.available_cents,
                                  updated_at = NOW()
                    """
                ),
                {
                    "tid": tenant_id,
                    "sid": scheme_id,
                    "lot": lot_id,
                    "owner": owners[lot_id],
                    "amt": int(bal.unapplied_credit_cents),
                },
            )
            written += 1
        await session.commit()
        print(f"\n  WROTE {written} rows, {_fmt(total)} of credit.")
        return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--building-id", required=True)
    ap.add_argument("--financial-year", required=True, help="4-digit calendar year, e.g. 2026")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    asyncio.run(backfill(args.building_id, args.financial_year, args.apply))


if __name__ == "__main__":
    main()
