"""
tests/backend/routers/test_bank_feeds_dispatch.py — _dispatch_to_matching_engine() wiring.

GAP-FIN-015 Phase 1: engine.match() classifies a transaction status="auto_allocated"
when its best score exceeds the auto-match threshold, but never itself posts to the
ledger. _dispatch_to_matching_engine() must call auto_allocate_queue_item() exactly
when the match result says so — and never for a replayed (idempotent) event, since
that item was already handled by an earlier dispatch.

Patch targets: match() and auto_allocate_queue_item() are imported locally inside
_dispatch_to_matching_engine(), so they must be patched at their defining module
(integrations.matching.engine / routers.financial_matching), not at routers.bank_feeds.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "backend",
)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from routers.bank_feeds import (
    _AUTO_ALLOCATION_DISABLED_THRESHOLD,
    _dispatch_to_matching_engine,
    _event_from_pg_row,
)
from integrations.envelopes import BankTxObserved
from integrations.matching.engine import EngineResult

BUILDING_A = "16244"


def _pg_row(description: str, **overrides):
    base = dict(
        external_transaction_id="ext-1",
        bank_transaction_id="bt-1",
        transaction_date=datetime(2026, 7, 1, tzinfo=timezone.utc),
        amount_cents=176150,
        description=description,
        balance_after_cents=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestEventFromPgRowSignalExtraction:
    """GAP-FIN-015: _event_from_pg_row() must extract lot_ref_raw/bpay_crn/osko_e2e_id
    from the description the same way integrations.demo_bank.ingestion._extract_signals()
    already does for the direct-ingestion path — otherwise every transaction synced from
    Postgres reaches MatchingEngine with lot_ref_raw=None and L4 unit-ref scoring can never
    fire, which is why 2,131 of East Gate's 2,768 staged transactions landed in
    `unidentified` with zero ever auto-allocated."""

    def test_extracts_lot_ref_from_lot_suffix(self):
        row = _pg_row("DEFT/BPAY payment received — reconciled from FY2025 closing balance - LOT TH078")
        event = _event_from_pg_row(row, building_id="13195", account_ref="ADMIN-13195")
        assert event.lot_ref_raw == "TH078"

    def test_no_lot_reference_leaves_lot_ref_raw_none(self):
        row = _pg_row("Attend site to inspect water ingress at roof above Level 6")
        event = _event_from_pg_row(row, building_id="13195", account_ref="SINKING-13195")
        assert event.lot_ref_raw is None


def _tx() -> BankTxObserved:
    return BankTxObserved(
        provider_txn_id="tx-001",
        tenant_id=BUILDING_A,
        account_ref="ADMIN-16244",
        occurred_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        amount_cents=153000,
        description="LEVY UNIT 1",
    )


def _engine_result(**overrides) -> EngineResult:
    base = dict(
        queue_id="queue-1",
        match_type="auto",
        best_score=0.95,
        best_layer="L4_unit_ref_amount_timing",
        best_lot=None,
        all_scores=[],
        is_auto_allocated=True,
        is_idempotent_replay=False,
    )
    base.update(overrides)
    return EngineResult(**base)


@pytest.mark.asyncio
async def test_dispatch_calls_auto_allocate_when_engine_auto_allocates():
    db = MagicMock()
    with patch("routers.bank_feeds._get_db", return_value=db), \
         patch("routers.bank_feeds._load_lot_candidates", AsyncMock(return_value=[])), \
         patch("integrations.matching.engine.match",
               AsyncMock(return_value=_engine_result())), \
         patch("routers.financial_matching.auto_allocate_queue_item",
               AsyncMock(return_value="rcpt-1")) as auto_mock:
        await _dispatch_to_matching_engine(
            event=_tx(), building_id=BUILDING_A, inbox_event_id="inbox-1", is_test_data=False,
        )

    auto_mock.assert_awaited_once_with("queue-1", BUILDING_A)


@pytest.mark.asyncio
async def test_dispatch_skips_auto_allocate_when_not_auto_allocated():
    db = MagicMock()
    with patch("routers.bank_feeds._get_db", return_value=db), \
         patch("routers.bank_feeds._load_lot_candidates", AsyncMock(return_value=[])), \
         patch("integrations.matching.engine.match",
               AsyncMock(return_value=_engine_result(is_auto_allocated=False, match_type="review"))), \
         patch("routers.financial_matching.auto_allocate_queue_item",
               AsyncMock()) as auto_mock:
        await _dispatch_to_matching_engine(
            event=_tx(), building_id=BUILDING_A, inbox_event_id="inbox-1", is_test_data=False,
        )

    auto_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_retries_auto_allocate_on_idempotent_replay():
    """A replayed inbox_event_id can happen after a crash between match() committing
    status="auto_allocated" and auto_allocate_queue_item() finishing — every future
    replay of that inbox_event_id would return is_idempotent_replay=True forever, so
    dispatch must still attempt auto_allocate_queue_item() on a replay rather than
    leaving the item stuck. Safe because auto_allocate_queue_item() race-guards on
    status="auto_allocated" itself — a replay of an already-completed item is a no-op."""
    db = MagicMock()
    with patch("routers.bank_feeds._get_db", return_value=db), \
         patch("routers.bank_feeds._load_lot_candidates", AsyncMock(return_value=[])), \
         patch("integrations.matching.engine.match",
               AsyncMock(return_value=_engine_result(is_idempotent_replay=True))), \
         patch("routers.financial_matching.auto_allocate_queue_item",
               AsyncMock(return_value="rcpt-1")) as auto_mock:
        await _dispatch_to_matching_engine(
            event=_tx(), building_id=BUILDING_A, inbox_event_id="inbox-1", is_test_data=False,
        )

    auto_mock.assert_awaited_once_with("queue-1", BUILDING_A)


@pytest.mark.asyncio
async def test_dispatch_loads_candidates_as_of_the_transactions_own_date():
    """GAP-FIN-015: candidates must be resolved for the date the transaction actually
    occurred, not "today" — otherwise a historical transaction is compared against a
    receivable amount from a period it could never have paid."""
    db = MagicMock()
    with patch("routers.bank_feeds._get_db", return_value=db), \
         patch("routers.bank_feeds._load_lot_candidates", AsyncMock(return_value=[])) as load_mock, \
         patch("integrations.matching.engine.match",
               AsyncMock(return_value=_engine_result(is_auto_allocated=False, match_type="review"))):
        await _dispatch_to_matching_engine(
            event=_tx(), building_id=BUILDING_A, inbox_event_id="inbox-1", is_test_data=False,
        )

    load_mock.assert_awaited_once_with(db, BUILDING_A, as_of_date=date(2026, 7, 1))


@pytest.mark.asyncio
async def test_dispatch_disables_auto_allocation_when_requested():
    """East Gate 13195 investigation (2026-07-22): historical/backfill sync must be
    able to force every match into human review, regardless of MatchingEngine
    confidence score. disable_auto_allocation=True should pass an
    auto_match_threshold above 1.0 (a score can never exceed 1.0) through to
    match() so is_auto_allocated can never be True — verified here at the
    match() call-argument level, not just the auto_allocate_queue_item() outcome,
    so this fails loudly if the threshold value or kwarg name ever drifts."""
    db = MagicMock()
    with patch("routers.bank_feeds._get_db", return_value=db), \
         patch("routers.bank_feeds._load_lot_candidates", AsyncMock(return_value=[])), \
         patch("integrations.matching.engine.match",
               AsyncMock(return_value=_engine_result(is_auto_allocated=False, match_type="review"))) as match_mock, \
         patch("routers.financial_matching.auto_allocate_queue_item", AsyncMock()) as auto_mock:
        await _dispatch_to_matching_engine(
            event=_tx(), building_id=BUILDING_A, inbox_event_id="inbox-1",
            is_test_data=False, disable_auto_allocation=True,
        )

    assert match_mock.await_args.kwargs["auto_match_threshold"] == _AUTO_ALLOCATION_DISABLED_THRESHOLD
    assert _AUTO_ALLOCATION_DISABLED_THRESHOLD > 1.0
    auto_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_leaves_default_threshold_when_not_disabled():
    """default disable_auto_allocation=False must not change existing live-bank-feed
    behaviour — no auto_match_threshold override should be passed to match() at all,
    so it falls through to the engine's own default."""
    db = MagicMock()
    with patch("routers.bank_feeds._get_db", return_value=db), \
         patch("routers.bank_feeds._load_lot_candidates", AsyncMock(return_value=[])), \
         patch("integrations.matching.engine.match",
               AsyncMock(return_value=_engine_result(is_auto_allocated=False, match_type="review"))) as match_mock:
        await _dispatch_to_matching_engine(
            event=_tx(), building_id=BUILDING_A, inbox_event_id="inbox-1", is_test_data=False,
        )

    assert "auto_match_threshold" not in match_mock.await_args.kwargs


@pytest.mark.asyncio
async def test_dispatch_does_not_propagate_matching_engine_failure():
    """Sync must never be aborted by a matching or auto-allocate error — this is the
    module's own documented contract for _dispatch_to_matching_engine()."""
    db = MagicMock()
    with patch("routers.bank_feeds._get_db", return_value=db), \
         patch("routers.bank_feeds._load_lot_candidates", AsyncMock(return_value=[])), \
         patch("integrations.matching.engine.match",
               AsyncMock(side_effect=RuntimeError("boom"))):
        await _dispatch_to_matching_engine(
            event=_tx(), building_id=BUILDING_A, inbox_event_id="inbox-1", is_test_data=False,
        )
    # No exception raised — the try/except in _dispatch_to_matching_engine caught it.
