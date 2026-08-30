"""Adapter-level unit tests for the GAP-FIN-057 receipt-allocation reversal — the
PostgresLedgerRepository half of the command (the SELECT/DELETE/recompute the service
orchestrates).

These mock the SQLAlchemy AsyncSession so no live database is required: they prove the
adapter maps rows → domain objects, threads the receipt_ids scope, derives levy_item
status correctly, captures paid_cents before/after, and — the idempotency guarantee —
recomputes paid_cents from the SURVIVING allocations rather than decrementing. The
against-live-data verification (spec §6, the actual --apply) stays on the server; this
covers the pure mapping/derivation logic that CAN be tested in isolation.
"""
from __future__ import annotations

import sys
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

BACKEND_PATH = str(Path(__file__).resolve().parents[2] / "backend")
if BACKEND_PATH not in sys.path:
    sys.path.insert(0, BACKEND_PATH)

os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("ENCRYPTION_KEY", "uJw9lYkG9btQfIp7q0M6vFlxnPVO92jYbP4P5Ih6QqQ=")

import pytest

from services.financial_core.adapters.db_postgres.ledger_repo import (
    PostgresLedgerRepository,
    _derive_levy_status,
)
from services.financial_core.domain.entities import SchemeRef


def _scheme_ref() -> SchemeRef:
    return SchemeRef(tenant_id=uuid.uuid4(), scheme_id=uuid.uuid4())


def _result(all_rows=None, scalars_rows=None):
    """Build a mock result object supporting .all() and .scalars().all()."""
    res = MagicMock()
    res.all.return_value = all_rows or []
    scalars = MagicMock()
    scalars.all.return_value = scalars_rows or []
    res.scalars.return_value = scalars
    return res


# ------------------------------------------------------------------ _derive_levy_status

def test_derive_status_nothing_paid_is_issued():
    assert _derive_levy_status(0, 100000) == "issued"
    assert _derive_levy_status(-50, 100000) == "issued"


def test_derive_status_partial():
    assert _derive_levy_status(40000, 100000) == "partial"


def test_derive_status_fully_paid():
    assert _derive_levy_status(100000, 100000) == "paid"
    assert _derive_levy_status(120000, 100000) == "paid"  # overpaid still 'paid'


def test_derive_status_zero_charge_stays_issued():
    # A zero/negative charge with nothing applied has nothing to mark paid.
    assert _derive_levy_status(0, 0) == "issued"


# --------------------------------------------- get_allocations_for_reversed_receipts

@pytest.mark.asyncio
async def test_get_allocations_maps_rows_and_threads_receipt_scope():
    r_id, li_id, alloc_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fund_id, lot_id = uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(
        allocation_id=alloc_id, receipt_id=r_id, levy_item_id=li_id,
        allocated_cents=12345, fund_id=fund_id, lot_id=lot_id,
    )
    session = MagicMock()
    session.execute = AsyncMock(return_value=_result(all_rows=[row]))
    repo = PostgresLedgerRepository(session)

    out = await repo.get_allocations_for_reversed_receipts(_scheme_ref(), receipt_ids=[r_id])

    assert len(out) == 1
    got = out[0]
    assert got.allocation_id == alloc_id
    assert got.receipt_id == r_id
    assert got.levy_item_id == li_id
    assert got.allocated_cents == 12345
    assert got.fund_id == fund_id
    assert got.lot_id == lot_id
    # The receipt scope was passed as a bind param, and the SQL includes the filter.
    called_sql = str(session.execute.await_args.args[0])
    params = session.execute.await_args.args[1]
    assert "r.receipt_id = ANY(:receipt_ids)" in called_sql
    assert params["receipt_ids"] == [str(r_id)]


@pytest.mark.asyncio
async def test_get_allocations_empty_receipt_scope_short_circuits():
    session = MagicMock()
    session.execute = AsyncMock()
    repo = PostgresLedgerRepository(session)

    out = await repo.get_allocations_for_reversed_receipts(_scheme_ref(), receipt_ids=[])

    assert out == []
    session.execute.assert_not_called()  # never issues an ambiguous ANY(ARRAY[]) query


@pytest.mark.asyncio
async def test_get_allocations_none_scope_omits_filter():
    session = MagicMock()
    session.execute = AsyncMock(return_value=_result(all_rows=[]))
    repo = PostgresLedgerRepository(session)

    await repo.get_allocations_for_reversed_receipts(_scheme_ref(), receipt_ids=None)

    called_sql = str(session.execute.await_args.args[0])
    params = session.execute.await_args.args[1]
    assert "receipt_ids" not in called_sql
    assert "receipt_ids" not in params


# ------------------------------------------------------------------ delete_allocations

@pytest.mark.asyncio
async def test_delete_allocations_noop_on_empty():
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    repo = PostgresLedgerRepository(session)

    await repo.delete_allocations([])

    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_delete_allocations_issues_delete():
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    repo = PostgresLedgerRepository(session)

    await repo.delete_allocations([uuid.uuid4(), uuid.uuid4()])

    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()


# ------------------------------------------------------------------ recompute_paid_cents

@pytest.mark.asyncio
async def test_recompute_from_survivors_sets_paid_and_status():
    li_a, li_b = uuid.uuid4(), uuid.uuid4()
    fund, lot = uuid.uuid4(), uuid.uuid4()
    # Before: item A had paid 100000 (charge 100000), item B had paid 60000 (charge 100000).
    before_rows = [
        SimpleNamespace(levy_item_id=li_a, fund_id=fund, lot_id=lot, paid_cents=100000,
                        principal_cents=100000, gst_cents=0, interest_cents=0, recovery_costs_cents=0),
        SimpleNamespace(levy_item_id=li_b, fund_id=fund, lot_id=lot, paid_cents=60000,
                        principal_cents=100000, gst_cents=0, interest_cents=0, recovery_costs_cents=0),
    ]
    # Survivors after the stale rows were deleted: A has none left (→0), B keeps 40000.
    survivor_rows = [(li_b, 40000)]

    session = MagicMock()
    # call order: before-select, survivor-select, then one UPDATE per item, then flush.
    session.execute = AsyncMock(side_effect=[
        _result(all_rows=before_rows),
        _result(all_rows=survivor_rows),
        MagicMock(),  # UPDATE li_a
        MagicMock(),  # UPDATE li_b
    ])
    session.flush = AsyncMock()
    repo = PostgresLedgerRepository(session)

    out = await repo.recompute_paid_cents(_scheme_ref(), [li_a, li_b])

    by_item = {r.levy_item_id: r for r in out}
    # A: no survivors → paid 0, before 100000
    assert by_item[li_a].paid_cents_before == 100000
    assert by_item[li_a].paid_cents_after == 0
    # B: 40000 survives → partial
    assert by_item[li_b].paid_cents_before == 60000
    assert by_item[li_b].paid_cents_after == 40000
    # per-fund/lot carried through for the driver's §4 asserts
    assert by_item[li_a].fund_id == fund and by_item[li_a].lot_id == lot
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_recompute_noop_on_empty_item_list():
    session = MagicMock()
    session.execute = AsyncMock()
    repo = PostgresLedgerRepository(session)

    out = await repo.recompute_paid_cents(_scheme_ref(), [])

    assert out == []
    session.execute.assert_not_called()


# --------------------------------------------- get_receipt_ids_for_journal_entry

@pytest.mark.asyncio
async def test_get_receipt_ids_for_journal_entry_returns_scalars():
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    session = MagicMock()
    session.execute = AsyncMock(return_value=_result(scalars_rows=[r1, r2]))
    repo = PostgresLedgerRepository(session)

    out = await repo.get_receipt_ids_for_journal_entry(_scheme_ref(), uuid.uuid4())

    assert out == [r1, r2]
