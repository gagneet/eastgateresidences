"""Unit tests for FinancialCoreService.reverse_allocations() — the GAP-FIN-057
receipt-allocation reversal command.

Pure service-layer orchestration, exercised against a mocked ledger port (no live
data). Proves: it deletes exactly the stale allocations, recomputes paid_cents for the
DISTINCT affected levy_items, emits the payment.allocation_reversed outbox event, is an
idempotent no-op when nothing needs reversing, and — critically — posts NO journal (the
GL was already corrected by the prior reverse_entry()). The adapter SQL is out of scope
here by design (server-side / live-verify per the spec).
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from services.financial_core.domain.entities import (
    AllocationToReverse,
    ReverseAllocationsCommand,
    ReversedAllocationItem,
    SchemeRef,
)
from services.financial_core.service import FinancialCoreService

TENANT = uuid.uuid4()
SCHEME = uuid.uuid4()


def _scheme_ref() -> SchemeRef:
    return SchemeRef(tenant_id=TENANT, scheme_id=SCHEME)


def _alloc(receipt_id, levy_item_id, cents, fund_id=None, lot_id=None) -> AllocationToReverse:
    return AllocationToReverse(
        allocation_id=uuid.uuid4(),
        receipt_id=receipt_id,
        levy_item_id=levy_item_id,
        allocated_cents=cents,
        fund_id=fund_id or uuid.uuid4(),
        lot_id=lot_id or uuid.uuid4(),
    )


def _service(stale, recomputed=None):
    ledger = AsyncMock()
    ledger.get_allocations_for_reversed_receipts = AsyncMock(return_value=stale)
    ledger.delete_allocations = AsyncMock()
    ledger.recompute_paid_cents = AsyncMock(return_value=recomputed or [])
    outbox = AsyncMock()
    return FinancialCoreService(ledger_repo=ledger, outbox_port=outbox), ledger, outbox


@pytest.mark.asyncio
async def test_happy_path_deletes_recomputes_and_emits_event():
    r1, r2 = uuid.uuid4(), uuid.uuid4()
    i1, i2 = uuid.uuid4(), uuid.uuid4()
    # 3 stale allocations across 2 receipts and 2 distinct levy_items (i1 twice).
    stale = [_alloc(r1, i1, 1000), _alloc(r1, i2, 500), _alloc(r2, i1, 250)]
    recomputed = [
        ReversedAllocationItem(levy_item_id=i1, fund_id=uuid.uuid4(), lot_id=uuid.uuid4(),
                               paid_cents_before=1250, paid_cents_after=0),
        ReversedAllocationItem(levy_item_id=i2, fund_id=uuid.uuid4(), lot_id=uuid.uuid4(),
                               paid_cents_before=500, paid_cents_after=0),
    ]
    svc, ledger, outbox = _service(stale, recomputed)

    result = await svc.reverse_allocations(
        ReverseAllocationsCommand(scheme_ref=_scheme_ref(), reason="GAP-FIN-057 cleanup")
    )

    # deletes exactly the 3 stale allocation ids
    ledger.delete_allocations.assert_awaited_once()
    assert set(ledger.delete_allocations.await_args.args[0]) == {a.allocation_id for a in stale}

    # recomputes the 2 DISTINCT levy_items, first-seen order [i1, i2] (i1 appears twice)
    ledger.recompute_paid_cents.assert_awaited_once()
    assert ledger.recompute_paid_cents.await_args.args[1] == [i1, i2]

    # emits the reversal event with the right payload
    outbox.publish.assert_awaited_once()
    assert outbox.publish.await_args.kwargs["event_type"] == "payment.allocation_reversed"
    payload = outbox.publish.await_args.kwargs["payload"]
    assert payload["deleted_allocation_count"] == 3
    assert payload["reversed_allocated_cents"] == 1750
    assert set(payload["reversed_receipt_ids"]) == {str(r1), str(r2)}
    assert set(payload["affected_levy_item_ids"]) == {str(i1), str(i2)}

    # result reflects the unwind
    assert result.is_noop is False
    assert result.reversed_allocated_cents == 1750
    assert result.reversed_receipt_ids == [r1, r2]
    assert set(result.deleted_allocation_ids) == {a.allocation_id for a in stale}
    assert result.affected_items == recomputed


@pytest.mark.asyncio
async def test_posts_no_journal_the_gl_was_already_reversed():
    """The architectural invariant: reverse_allocations only unwinds the derived
    allocation linkage — it must never post/modify a journal entry (the GL was already
    corrected by reverse_entry())."""
    r1, i1 = uuid.uuid4(), uuid.uuid4()
    svc, ledger, _ = _service([_alloc(r1, i1, 999)])

    await svc.reverse_allocations(
        ReverseAllocationsCommand(scheme_ref=_scheme_ref(), reason="x")
    )

    ledger.save_journal_entry.assert_not_called()
    ledger.post_journal_entry.assert_not_called()
    ledger.save_allocations.assert_not_called()
    ledger.decrement_paid_for_year.assert_not_called()


@pytest.mark.asyncio
async def test_idempotent_noop_when_nothing_to_reverse():
    svc, ledger, outbox = _service([])

    result = await svc.reverse_allocations(
        ReverseAllocationsCommand(scheme_ref=_scheme_ref(), reason="replay")
    )

    assert result.is_noop is True
    assert result.deleted_allocation_ids == []
    assert result.reversed_receipt_ids == []
    assert result.reversed_allocated_cents == 0
    assert result.affected_items == []
    ledger.delete_allocations.assert_not_called()
    ledger.recompute_paid_cents.assert_not_called()
    outbox.publish.assert_not_called()


@pytest.mark.asyncio
async def test_dedups_multiple_allocations_on_same_levy_item():
    """Two allocations from the same reversed receipt hitting the SAME levy_item must
    recompute that item once (not twice), and both allocations' cents are summed."""
    r1, i1 = uuid.uuid4(), uuid.uuid4()
    stale = [_alloc(r1, i1, 300), _alloc(r1, i1, 700)]
    svc, ledger, _ = _service(stale, [])

    result = await svc.reverse_allocations(
        ReverseAllocationsCommand(scheme_ref=_scheme_ref(), reason="x")
    )

    assert len(ledger.delete_allocations.await_args.args[0]) == 2
    assert ledger.recompute_paid_cents.await_args.args[1] == [i1]
    assert result.reversed_allocated_cents == 1000


@pytest.mark.asyncio
async def test_passes_receipt_ids_scope_to_identification():
    """A receipt-scoped command (single-unit canary) threads receipt_ids through to the
    identification port call."""
    r = uuid.uuid4()
    svc, ledger, _ = _service([])

    await svc.reverse_allocations(
        ReverseAllocationsCommand(scheme_ref=_scheme_ref(), reason="canary", receipt_ids=[r])
    )

    ledger.get_allocations_for_reversed_receipts.assert_awaited_once()
    assert ledger.get_allocations_for_reversed_receipts.await_args.args[1] == [r]
