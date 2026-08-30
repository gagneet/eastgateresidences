"""
tests/backend/test_financial_matching_ledger_posting.py

Regression coverage for a 2026-07-17 audit finding: `_post_payment_to_ledger()`
(routers/financial_matching.py) constructed `FinancialCoreService()` with zero
arguments — the class requires `ledger_repo`/`outbox_port` — so every call
raised TypeError, which the function's outer `except Exception` silently
swallowed and logged, returning None. Every "allocate" match decision and
every high-confidence reconstruction promotion therefore committed the queue
decision but never actually posted a receipt/journal entry to Postgres.

Fixed by opening a single db_postgres session across the lot_id lookup and the
ledger write, and building the service via the canonical
`services.financial_core.get_financial_core_service(session)` factory instead
of calling the class directly. This test exercises the real function body
(only the DB/session boundary is mocked) so a regression back to a bad
zero/mis-args construction raises instead of silently returning None.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from routers.financial_matching import _post_payment_to_ledger

BUILDING_ID = "13195"
TENANT_ID = uuid4()
SCHEME_ID = uuid4()
LOT_ID = uuid4()
RECEIPT_ID = uuid4()


def _queue_doc():
    bank_transaction_id = uuid4()
    return {
        "tx": {
            "provider_txn_id": "demo-bank-provider-tx-001",
            "amount_cents": 55384,
            "occurred_at": "2026-02-01T00:00:00+00:00",
            "metadata": {
                "transaction_origin": "reconstructed_historical",
                "reconstruction_batch_id": str(uuid4()),
                "assumption_code": "quarterly_regular",
            },
        },
        "is_test_data": False,
        "inbox_event_id": str(bank_transaction_id),
    }


class _FakeSessionCtx:
    """Mimics `async with async_session_context() as session:` without a real DB."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


OWNER_PARTY_ID = uuid4()


def _owner_execute_results(owner_party_id=OWNER_PARTY_ID, bank_transaction_exists=True):
    """session.execute() results, in the order _post_payment_to_ledger() issues them.

    Two direct queries now, not one:

      1. the ownership_periods owner lookup, and
      2. the finance.bank_transactions existence probe in
         _resolve_bank_transaction_id().

    Query 2 exists because receipts.bank_transaction_id is a real foreign key and
    the old code assumed any inbox_event_id that PARSED as a UUID was a valid value
    for it. On the Strata Web inferred-payment path it never is, so every allocate
    raised ForeignKeyViolationError and the endpoint answered 502 (live, East Gate,
    2026-08-28: 39 of 39 pending items). The lot_id lookup goes through
    get_lot_id_by_number() (patched separately via _patch_lot_lookup), not
    session.execute().
    """
    owner_result = MagicMock()
    if owner_party_id is None:
        owner_result.first = MagicMock(return_value=None)
    else:
        owner_row = MagicMock(owner_party_id=str(owner_party_id))
        owner_result.first = MagicMock(return_value=owner_row)

    bank_result = MagicMock()
    bank_result.first = MagicMock(return_value=(1,) if bank_transaction_exists else None)

    return AsyncMock(side_effect=[owner_result, bank_result])


def _patch_lot_lookup(lot_id=LOT_ID):
    """get_lot_id_by_number() now resolves core.lots.lot_id -- both lot_number and
    unit_number are valid match columns (East Gate's Postgres lot_number is a bare
    digit string like "87", never "TH087"), so this is a dedicated repo function,
    not a raw session.execute() call the test can simulate via side_effect."""
    return patch(
        "db_postgres.repos.ownership_repo.get_lot_id_by_number",
        AsyncMock(return_value=str(lot_id)),
    )


@pytest.mark.asyncio
async def test_post_payment_to_ledger_constructs_service_via_factory_not_bare_class():
    """The core regression assertion: get_financial_core_service(session) is
    called (proving the fixed construction path), and record_payment /
    allocate_payment are both awaited and their results used — none of which
    would happen if the old `FinancialCoreService()` (zero-arg) construction
    were still in place, since that raised TypeError before any of this ran."""
    doc = _queue_doc()

    fake_session = MagicMock()
    fake_session.execute = _owner_execute_results()

    fake_receipt = MagicMock(receipt_id=RECEIPT_ID)
    fake_svc = MagicMock()
    fake_svc.record_payment = AsyncMock(return_value=fake_receipt)
    fake_svc.allocate_payment = AsyncMock(return_value=[MagicMock(), MagicMock()])

    get_svc_mock = MagicMock(return_value=fake_svc)

    fake_db = MagicMock()
    fake_db.levy_payments.insert_one = AsyncMock(return_value=MagicMock(inserted_id="mp-1"))

    with patch(
        "db_postgres.repos.config_repo.resolve_scheme_context",
        AsyncMock(return_value={"tenant_id": TENANT_ID, "scheme_id": SCHEME_ID}),
    ), patch(
        "db_postgres.session.async_session_context",
        MagicMock(return_value=_FakeSessionCtx(fake_session)),
    ), patch(
        "db_postgres.session.set_tenant", AsyncMock(),
    ), patch(
        "services.financial_core.get_financial_core_service", get_svc_mock,
    ), patch(
        "utils.finance_helpers.resolve_levy_year_for_date", AsyncMock(return_value=None),
    ), patch(
        "routers.financial_matching.db", fake_db,
    ), _patch_lot_lookup():
        result = await _post_payment_to_ledger(
            doc=doc,
            lot_id="71",
            amount_cents=55384,
            decided_by="user-mgr-001",
            current_user={"id": "user-mgr-001"},
            building_id=BUILDING_ID,
            item_id="queue-item-1",
        )

    assert result == str(RECEIPT_ID), (
        "Expected a receipt id back — a None result means the ledger write "
        "silently failed, exactly the bug class this test guards against."
    )
    get_svc_mock.assert_called_once_with(fake_session)
    fake_svc.record_payment.assert_awaited_once()
    fake_svc.allocate_payment.assert_awaited_once()

    # Provenance propagated into the RecordPaymentCommand (disclosure surfacing).
    cmd = fake_svc.record_payment.await_args.args[0]
    assert cmd.reconstruction_batch_id is not None
    assert cmd.lot_id == LOT_ID
    assert cmd.bank_transaction_id == UUID(doc["inbox_event_id"])
    assert cmd.external_reference == "demo-bank-provider-tx-001"
    # 2026-07-24 regression: payer_party_id must be the resolved OWNER party,
    # never the lot's own id — core.lots and core.parties are distinct tables,
    # and reusing pg_lot_id as payer_party_id violated journal_lines_party_id_fkey
    # on every single allocate decision (found live, East Gate 13195, while
    # individually reviewing the levy-income reconstruction queue).
    assert cmd.payer_party_id == OWNER_PARTY_ID
    assert cmd.payer_party_id != cmd.lot_id


@pytest.mark.asyncio
async def test_post_payment_to_ledger_returns_none_when_no_owner_found_for_payment_date():
    """2026-07-24 regression: a lot with no core.ownership_periods row covering
    (or preceding) the payment date must fail safely — same 'stay retryable,
    don't post' contract as lot-not-found — never proceed with a null/garbage
    payer_party_id."""
    doc = _queue_doc()
    fake_session = MagicMock()
    fake_session.execute = _owner_execute_results(owner_party_id=None)

    with patch(
        "db_postgres.repos.config_repo.resolve_scheme_context",
        AsyncMock(return_value={"tenant_id": TENANT_ID, "scheme_id": SCHEME_ID}),
    ), patch(
        "db_postgres.session.async_session_context",
        MagicMock(return_value=_FakeSessionCtx(fake_session)),
    ), patch(
        "db_postgres.session.set_tenant", AsyncMock(),
    ), _patch_lot_lookup():
        result = await _post_payment_to_ledger(
            doc=doc,
            lot_id="78",
            amount_cents=55384,
            decided_by="user-mgr-001",
            current_user={"id": "user-mgr-001"},
            building_id=BUILDING_ID,
            item_id="queue-item-no-owner",
        )

    assert result is None


@pytest.mark.asyncio
async def test_post_payment_to_ledger_returns_none_when_lot_not_found():
    doc = _queue_doc()
    fake_session = MagicMock()

    with patch(
        "db_postgres.repos.config_repo.resolve_scheme_context",
        AsyncMock(return_value={"tenant_id": TENANT_ID, "scheme_id": SCHEME_ID}),
    ), patch(
        "db_postgres.session.async_session_context",
        MagicMock(return_value=_FakeSessionCtx(fake_session)),
    ), patch(
        "db_postgres.session.set_tenant", AsyncMock(),
    ), patch(
        "db_postgres.repos.ownership_repo.get_lot_id_by_number", AsyncMock(return_value=None),
    ):
        result = await _post_payment_to_ledger(
            doc=doc,
            lot_id="does-not-exist",
            amount_cents=55384,
            decided_by="user-mgr-001",
            current_user={"id": "user-mgr-001"},
            building_id=BUILDING_ID,
            item_id="queue-item-2",
        )

    assert result is None


# ── bank_transaction_id resolution (2026-08-28) ───────────────────────────────
#
# finance.receipts.bank_transaction_id is a FOREIGN KEY into
# finance.bank_transactions. _post_payment_to_ledger() used to pass the queue
# item's inbox_event_id through to it on the sole basis that it parsed as a UUID.
# For every queue item minted by the Strata Web balance-delta inference path that
# id names an ingestion inbox event with no bank_transactions row behind it, so
# record_payment() raised ForeignKeyViolationError, the helper returned None, and
# decide_queue_item answered 502 — verified live on East Gate 2026-08-28, where
# all 39 pending items were unallocatable for this reason.
#
# The real id was available all along on the Demo Bank row that produced the
# transaction (`finance_bank_transaction_ref`). These tests pin all three
# outcomes: inbox id verified, Demo Bank fallback, and neither → NULL rather than
# a failed allocation.


async def _run_post(doc, fake_session, fake_db):
    """Drive _post_payment_to_ledger() with the DB/session boundary mocked."""
    fake_receipt = MagicMock(receipt_id=RECEIPT_ID)
    fake_svc = MagicMock()
    fake_svc.record_payment = AsyncMock(return_value=fake_receipt)
    fake_svc.allocate_payment = AsyncMock(return_value=[])

    with patch(
        "db_postgres.repos.config_repo.resolve_scheme_context",
        AsyncMock(return_value={"tenant_id": TENANT_ID, "scheme_id": SCHEME_ID}),
    ), patch(
        "db_postgres.session.async_session_context",
        MagicMock(return_value=_FakeSessionCtx(fake_session)),
    ), patch(
        "db_postgres.session.set_tenant", AsyncMock(),
    ), patch(
        "services.financial_core.get_financial_core_service", MagicMock(return_value=fake_svc),
    ), patch(
        "utils.finance_helpers.resolve_levy_year_for_date", AsyncMock(return_value=None),
    ), patch(
        "routers.financial_matching.db", fake_db,
    ), _patch_lot_lookup():
        result = await _post_payment_to_ledger(
            doc=doc,
            lot_id="71",
            amount_cents=55384,
            decided_by="user-mgr-001",
            current_user={"id": "user-mgr-001"},
            building_id=BUILDING_ID,
            item_id="queue-item-1",
        )
    return result, fake_svc


def _fake_db(demo_bank_row=None):
    fake_db = MagicMock()
    fake_db.levy_payments.insert_one = AsyncMock(return_value=MagicMock(inserted_id="mp-1"))
    fake_db.demo_bank_transactions.find_one = AsyncMock(return_value=demo_bank_row)
    return fake_db


@pytest.mark.asyncio
async def test_bank_transaction_id_falls_back_to_demo_bank_ref_when_inbox_id_is_not_a_bank_txn():
    """The 502 fix: an inbox_event_id with no bank_transactions row must NOT be
    written into the FK column. The Demo Bank row's finance_bank_transaction_ref
    is the real link and must be used instead — the allocation succeeds."""
    doc = _queue_doc()
    real_bank_txn_id = uuid4()

    fake_session = MagicMock()
    # Probe 1: owner found. Probe 2: inbox_event_id is NOT a bank transaction.
    # Probe 3: the Demo Bank ref IS.
    owner_result = MagicMock()
    owner_result.first = MagicMock(return_value=MagicMock(owner_party_id=str(OWNER_PARTY_ID)))
    miss = MagicMock(); miss.first = MagicMock(return_value=None)
    hit = MagicMock(); hit.first = MagicMock(return_value=(1,))
    fake_session.execute = AsyncMock(side_effect=[owner_result, miss, hit])

    result, fake_svc = await _run_post(
        doc,
        fake_session,
        _fake_db({"finance_bank_transaction_ref": str(real_bank_txn_id)}),
    )

    assert result == str(RECEIPT_ID), (
        "Allocation must succeed. A None result here is the live 502: the receipt "
        "never posted and the queue item stayed pending."
    )
    cmd = fake_svc.record_payment.await_args.args[0]
    assert cmd.bank_transaction_id == real_bank_txn_id, (
        "Must post the Demo Bank row's real finance.bank_transactions id, not the "
        "inbox event id that violates the foreign key."
    )


@pytest.mark.asyncio
async def test_bank_transaction_id_is_null_when_no_bank_transaction_can_be_verified():
    """Neither source resolves: post with a NULL FK rather than failing the whole
    allocation. The column is nullable and external_reference still records the
    provider's own id, so provenance survives."""
    doc = _queue_doc()

    fake_session = MagicMock()
    owner_result = MagicMock()
    owner_result.first = MagicMock(return_value=MagicMock(owner_party_id=str(OWNER_PARTY_ID)))
    miss = MagicMock(); miss.first = MagicMock(return_value=None)
    fake_session.execute = AsyncMock(side_effect=[owner_result, miss])

    result, fake_svc = await _run_post(doc, fake_session, _fake_db(demo_bank_row=None))

    assert result == str(RECEIPT_ID)
    cmd = fake_svc.record_payment.await_args.args[0]
    assert cmd.bank_transaction_id is None, (
        "An unverifiable link must degrade to NULL, never to a guessed UUID that "
        "the foreign key will reject."
    )
    assert cmd.external_reference == "demo-bank-provider-tx-001"
