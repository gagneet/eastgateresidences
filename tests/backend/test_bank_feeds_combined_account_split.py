"""
tests/backend/test_bank_feeds_combined_account_split.py

Regression coverage for the real production failure found 2026-07-31 when
syncing East Gate's first GAP-FIN-026 reconstruction batch: every income
transaction (2,088 of 2,172 rows, the full $1,992,118.40) failed with
"No unique active Postgres trust account found for account_ref='OPERATING-13195'".

Root cause: `ensure_generation_account()` creates ONE combined Demo Bank
account (account_type="transaction") for a GENERATION_METHOD batch's owner
payments, deliberately grouping a payment's admin+sinking+GST rows into one
Demo Bank transaction (see budget_levy_generator.py / ingestion.py's
payment_group_id grouping — using each fund's own account_ref instead silently
drops the group). `_fund_type_for_account()` has no mapping for
account_type="transaction", so the single-fund trust-account resolution in
run_bank_feed_sync() always failed for these rows.

Per explicit product direction: an owner never pays Admin and Sinking
separately — Demo Bank always holds ONE combined levy (or expense) amount.
Splitting that amount into the correct per-fund GL postings is the
application's job, not Demo Bank's. The fix reads the split from the
transaction's own `allocations` field (already present, set by
import_historical_reconstruction) and posts one finance.bank_transactions row
per fund, each into its own resolved trust account.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from integrations.envelopes import BankTxObserved  # noqa: E402
from routers.bank_feeds import (  # noqa: E402
    BankFeedSyncRequest,
    _split_allocations_by_fund,
    _tx_from_envelope,
    run_bank_feed_sync,
)

BUILDING_ID = "13195"
_SCHEME = {"scheme_id": "scheme-13195", "tenant_id": "tenant-13195"}

_ALLOCATIONS = [
    {"fund_type": "admin", "levy_component": "ordinary", "amount_cents": 69878,
     "amount_ex_gst_cents": 63526, "gst_cents": 6352, "assumption_code": "quarterly_regular"},
    {"fund_type": "sinking", "levy_component": "ordinary", "amount_cents": 20399,
     "amount_ex_gst_cents": 18544, "gst_cents": 1855, "assumption_code": "quarterly_regular"},
]


# ── Pure helper: _split_allocations_by_fund ─────────────────────────────────

def test_split_allocations_sums_by_fund_type():
    totals = _split_allocations_by_fund(_ALLOCATIONS)
    assert totals == {"admin": 69878, "sinking": 20399}


def test_split_allocations_sums_multiple_lines_for_the_same_fund():
    lines = [
        {"fund_type": "admin", "amount_cents": 100},
        {"fund_type": "admin", "amount_cents": 50},
        {"fund_type": "sinking", "amount_cents": 25},
    ]
    assert _split_allocations_by_fund(lines) == {"admin": 150, "sinking": 25}


def test_split_allocations_empty_when_no_allocations():
    assert _split_allocations_by_fund(None) == {}
    assert _split_allocations_by_fund([]) == {}


def test_split_allocations_skips_lines_missing_fund_type_or_amount():
    lines = [{"fund_type": None, "amount_cents": 100}, {"fund_type": "admin", "amount_cents": None}]
    assert _split_allocations_by_fund(lines) == {}


def test_tx_from_envelope_carries_allocations_through():
    event = BankTxObserved(
        provider_txn_id="tx-combined-1", tenant_id=BUILDING_ID, account_ref="OPERATING-13195",
        occurred_at=datetime(2026, 7, 1, tzinfo=timezone.utc), amount_cents=90277,
        description="Combined levy receipt",
    )
    tx = _tx_from_envelope(event, {"allocations": _ALLOCATIONS})
    assert tx["allocations"] == _ALLOCATIONS


def test_tx_from_envelope_allocations_default_empty_list():
    event = BankTxObserved(
        provider_txn_id="tx-2", tenant_id=BUILDING_ID, account_ref="ADMIN-13195",
        occurred_at=datetime(2026, 7, 1, tzinfo=timezone.utc), amount_cents=100,
        description="Single-fund",
    )
    assert _tx_from_envelope(event, None)["allocations"] == []


# ── End-to-end: run_bank_feed_sync splits a combined account's transaction ──

class _FakeCollection:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, *_a, **_k):
        return self._doc


class _FakeDB:
    """Minimal db._db stand-in: only demo_bank_accounts/demo_bank_transactions
    .find_one are exercised by run_bank_feed_sync's combined-account path."""

    def __init__(self, account_doc, source_doc):
        self.demo_bank_accounts = _FakeCollection(account_doc)
        self.demo_bank_transactions = _FakeCollection(source_doc)


class _FakeWrappedDB:
    def __init__(self, account_doc, source_doc):
        self._db = _FakeDB(account_doc, source_doc)


def _combined_event() -> BankTxObserved:
    return BankTxObserved(
        provider_txn_id="tx-combined-1", tenant_id=BUILDING_ID, account_ref="OPERATING-13195",
        occurred_at=datetime(2026, 7, 1, tzinfo=timezone.utc), amount_cents=90277,
        description="Combined levy receipt", metadata={"reconstruction_batch_id": "batch-1"},
    )


async def _one_event_iterator(_account_ref, _since, _building_id=None):
    yield _combined_event()


@pytest.mark.asyncio
async def test_run_bank_feed_sync_splits_combined_account_by_allocations():
    account_doc = {"building_id": BUILDING_ID, "account_ref": "OPERATING-13195", "account_type": "transaction"}
    source_doc = {"_id": "mongo-id-1", "allocations": _ALLOCATIONS, "reconstruction_batch_id": "batch-1"}
    fake_db = _FakeWrappedDB(account_doc, source_doc)

    insert_calls = []

    async def _fake_insert(_session, *, tenant_id, scheme_id, trust_account_id, tx):
        insert_calls.append({"trust_account_id": trust_account_id, "amount_cents": tx["amount_cents"]})
        return f"pg-ref-{trust_account_id}"

    async def _fake_resolve_trust_account(_session, *, scheme_id, account_ref, fund_type):
        if fund_type is None:
            return None  # the initial (unsplittable) lookup for account_ref itself
        return {"admin": "ta-admin", "sinking": "ta-sinking"}[fund_type]

    fake_session = MagicMock()
    fake_session_ctx = MagicMock()
    fake_session_ctx.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.bank_feeds.config_repo.resolve_scheme_context", AsyncMock(return_value=_SCHEME)), \
         patch("routers.bank_feeds.async_session_context", return_value=fake_session_ctx), \
         patch("routers.bank_feeds.set_tenant", AsyncMock()), \
         patch("routers.bank_feeds._account_refs_for_sync", AsyncMock(return_value=["OPERATING-13195"])), \
         patch("routers.bank_feeds.DemoBankFeed") as mock_provider_cls, \
         patch("routers.bank_feeds._resolve_trust_account", AsyncMock(side_effect=_fake_resolve_trust_account)), \
         patch("routers.bank_feeds._insert_bank_transaction", AsyncMock(side_effect=_fake_insert)), \
         patch("routers.bank_feeds._mark_demo_bank_sync", AsyncMock()) as mock_mark_sync, \
         patch("routers.bank_feeds._dispatch_to_matching_engine", AsyncMock()), \
         patch("routers.bank_feeds.create_audit_log", AsyncMock()):
        mock_provider_cls.return_value.pull_transactions_include_test = _one_event_iterator

        payload = BankFeedSyncRequest(
            include_test_data=True, reconstruction_batch_id="batch-1", disable_auto_allocation=True,
        )
        result = await run_bank_feed_sync(fake_db, building_id=BUILDING_ID, payload=payload, actor={"id": "u1"})

    # Two Postgres rows, one per fund, correctly split by the transaction's own allocations.
    assert len(insert_calls) == 2
    by_account = {c["trust_account_id"]: c["amount_cents"] for c in insert_calls}
    assert by_account == {"ta-admin": 69878, "ta-sinking": 20399}
    # Split amounts sum back to the original combined amount — no money created/lost.
    assert sum(by_account.values()) == 90277

    # Counted once at the source-event level, not once per split row.
    assert result["processed"] == 1
    assert result["inserted"] == 1
    assert result["failed"] == 0

    # Mongo sync-status update records both Postgres refs, not just one.
    mock_mark_sync.assert_awaited_once()
    _, kwargs = mock_mark_sync.call_args
    assert kwargs["status"] == "synced"
    assert set(kwargs["pg_ref"].split(",")) == {"pg-ref-ta-admin", "pg-ref-ta-sinking"}


def _many_events_iterator(n):
    async def _iter(_account_ref, _since, _building_id=None):
        for i in range(n):
            yield BankTxObserved(
                provider_txn_id=f"tx-combined-{i}", tenant_id=BUILDING_ID, account_ref="OPERATING-13195",
                occurred_at=datetime(2026, 7, 1, tzinfo=timezone.utc), amount_cents=90277,
                description="Combined levy receipt", metadata={"reconstruction_batch_id": "batch-1"},
            )
    return _iter


@pytest.mark.asyncio
async def test_run_bank_feed_sync_caches_split_trust_account_resolution():
    """Regression test for the real 2026-07-31 production incident: the split
    path originally called _resolve_trust_account() fresh for every row (2,088
    rows x 2 funds = 4,176 identical, uncached Postgres queries for this
    batch), which is what actually caused the 120-second Cloudflare proxy
    timeout on retry. Every combined transaction under the same account_ref
    resolves the same fund->trust_account mapping, so it must be cached."""
    account_doc = {"building_id": BUILDING_ID, "account_ref": "OPERATING-13195", "account_type": "transaction"}
    source_doc = {"_id": "mongo-id-1", "allocations": _ALLOCATIONS, "reconstruction_batch_id": "batch-1"}
    fake_db = _FakeWrappedDB(account_doc, source_doc)

    resolve_calls = []

    async def _fake_resolve_trust_account(_session, *, scheme_id, account_ref, fund_type):
        resolve_calls.append(fund_type)
        if fund_type is None:
            return None
        return {"admin": "ta-admin", "sinking": "ta-sinking"}[fund_type]

    async def _fake_insert(_session, *, tenant_id, scheme_id, trust_account_id, tx):
        return f"pg-ref-{trust_account_id}-{tx['amount_cents']}"

    fake_session = MagicMock()
    fake_session_ctx = MagicMock()
    fake_session_ctx.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_ctx.__aexit__ = AsyncMock(return_value=False)

    N = 25  # enough to prove it's O(1) resolves, not O(events)
    with patch("routers.bank_feeds.config_repo.resolve_scheme_context", AsyncMock(return_value=_SCHEME)), \
         patch("routers.bank_feeds.async_session_context", return_value=fake_session_ctx), \
         patch("routers.bank_feeds.set_tenant", AsyncMock()), \
         patch("routers.bank_feeds._account_refs_for_sync", AsyncMock(return_value=["OPERATING-13195"])), \
         patch("routers.bank_feeds.DemoBankFeed") as mock_provider_cls, \
         patch("routers.bank_feeds._resolve_trust_account", AsyncMock(side_effect=_fake_resolve_trust_account)), \
         patch("routers.bank_feeds._insert_bank_transaction", AsyncMock(side_effect=_fake_insert)), \
         patch("routers.bank_feeds._mark_demo_bank_sync", AsyncMock()), \
         patch("routers.bank_feeds._dispatch_to_matching_engine", AsyncMock()), \
         patch("routers.bank_feeds.create_audit_log", AsyncMock()):
        mock_provider_cls.return_value.pull_transactions_include_test = _many_events_iterator(N)

        payload = BankFeedSyncRequest(
            include_test_data=True, reconstruction_batch_id="batch-1", disable_auto_allocation=True,
        )
        result = await run_bank_feed_sync(fake_db, building_id=BUILDING_ID, payload=payload, actor={"id": "u1"})

    assert result["processed"] == N
    assert result["inserted"] == N
    # The account-level lookup (fund_type=None) is cached by the pre-existing
    # account_cache and runs once; the per-fund split lookups (admin, sinking)
    # must also run exactly once each, not once per event.
    assert resolve_calls.count(None) == 1
    assert resolve_calls.count("admin") == 1
    assert resolve_calls.count("sinking") == 1
    assert len(resolve_calls) == 3, (
        f"expected exactly 3 _resolve_trust_account calls total (cached across "
        f"{N} events), got {len(resolve_calls)}: {resolve_calls}"
    )


@pytest.mark.asyncio
async def test_run_bank_feed_sync_fails_combined_account_with_no_allocations():
    """A combined-type account with no allocations to split by (not this
    batch's shape, but a real possible future one) must still fail loudly,
    never silently guess a fund or drop money."""
    account_doc = {"building_id": BUILDING_ID, "account_ref": "OPERATING-13195", "account_type": "transaction"}
    source_doc = {"_id": "mongo-id-2", "allocations": [], "reconstruction_batch_id": "batch-1"}
    fake_db = _FakeWrappedDB(account_doc, source_doc)

    fake_session = MagicMock()
    fake_session_ctx = MagicMock()
    fake_session_ctx.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_ctx.__aexit__ = AsyncMock(return_value=False)

    async def _fake_resolve_trust_account(_session, *, scheme_id, account_ref, fund_type):
        return None

    with patch("routers.bank_feeds.config_repo.resolve_scheme_context", AsyncMock(return_value=_SCHEME)), \
         patch("routers.bank_feeds.async_session_context", return_value=fake_session_ctx), \
         patch("routers.bank_feeds.set_tenant", AsyncMock()), \
         patch("routers.bank_feeds._account_refs_for_sync", AsyncMock(return_value=["OPERATING-13195"])), \
         patch("routers.bank_feeds.DemoBankFeed") as mock_provider_cls, \
         patch("routers.bank_feeds._resolve_trust_account", AsyncMock(side_effect=_fake_resolve_trust_account)), \
         patch("routers.bank_feeds._insert_bank_transaction", AsyncMock()) as mock_insert, \
         patch("routers.bank_feeds._mark_demo_bank_sync", AsyncMock()), \
         patch("routers.bank_feeds.create_audit_log", AsyncMock()):
        mock_provider_cls.return_value.pull_transactions_include_test = _one_event_iterator

        payload = BankFeedSyncRequest(
            include_test_data=True, reconstruction_batch_id="batch-1", disable_auto_allocation=True,
        )
        result = await run_bank_feed_sync(fake_db, building_id=BUILDING_ID, payload=payload, actor={"id": "u1"})

    assert result["failed"] == 1
    assert result["inserted"] == 0
    mock_insert.assert_not_called()
