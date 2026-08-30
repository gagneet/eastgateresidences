# @featuretrace:demo_bank — A live bank feed must land in Demo Bank, not the GL.
# Layer: test
# Data flow: BankTxObserved -> ingest_provider_feed() -> demo_bank_transactions (building-scoped).
# Related: backend/integrations/demo_bank/ingestion.py
#          backend/integrations/envelopes.py
#          rules/post-compact-critical.md (§15, the intake contract)
"""The intake contract, asserted.

Operator decision 2026-08-27: every financial input MATERIALISES rows in Demo Bank's own
collections before anything downstream sees it, and provider integrations are input
adapters to Demo Bank rather than parallel paths into the GL.

Before `ingest_provider_feed` the module could only act AS a provider — provider.py reads
rows OUT and emits envelopes. Nothing brought a real feed IN, so a Basiq connector had
nowhere to land except the GL, which is the bypass the contract forbids.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from integrations.demo_bank.ingestion import ingest_provider_feed
from integrations.envelopes import BankTxObserved

BID = "13195"
ACC = "trust-main"


def _tx(provider_txn_id: str, cents: int, desc: str = "BPAY PAYMENT UA042", **kw):
    return BankTxObserved(
        provider_txn_id=provider_txn_id, tenant_id=BID, account_ref=ACC,
        occurred_at=datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
        amount_cents=cents, description=desc, **kw,
    )


@pytest.fixture
def captured(monkeypatch):
    """Capture the kwargs the ingest passes to the shared upsert."""
    calls = []

    async def _fake_upsert(db, **kwargs):
        calls.append(kwargs)
        return True                      # treat every row as newly created

    monkeypatch.setattr("integrations.demo_bank.ingestion._upsert_transaction", _fake_upsert)
    monkeypatch.setattr("integrations.demo_bank.ingestion._recompute_balance", AsyncMock())
    return calls


class TestProvenance:
    @pytest.mark.asyncio
    async def test_a_live_feed_is_tagged_as_observed_not_reconstructed(self, captured):
        await ingest_provider_feed(MagicMock(), BID, ACC, [_tx("basiq-1", 45000)],
                                   provider_name="basiq")
        row = captured[0]
        assert row["source_type"] == "bank_feed"
        assert row["transaction_origin"] == "observed_bank_feed"
        assert row["provenance_class"] == "observed"
        assert row["evidence_type"] == "bank_feed"

    @pytest.mark.asyncio
    async def test_bank_observed_rows_do_not_re_enter_the_evidence_queue(self, captured):
        """The reconciliation answer: the institution's own proof stands behind it.

        A reconstructed row needs review because the platform inferred it. This one
        arrived from the bank, so it is not queued for the same scrutiny — while still
        going through matching, since "proven to have happened" is not "known which lot".
        """
        await ingest_provider_feed(MagicMock(), BID, ACC, [_tx("basiq-2", 45000)],
                                   provider_name="basiq")
        assert captured[0]["requires_review"] is False
        assert captured[0]["confidence"] == "high"

    @pytest.mark.asyncio
    async def test_a_free_text_description_never_becomes_a_resolved_lot(self, captured):
        """lot_ref_raw is a hint for the matching engine, not an attribution."""
        await ingest_provider_feed(
            MagicMock(), BID, ACC,
            [_tx("basiq-3", 45000, lot_ref_raw="UA042")], provider_name="basiq")
        assert captured[0].get("unit_number") is None


class TestSignedAmountConversion:
    """The envelope is signed; this collection stores absolute amount + direction."""

    @pytest.mark.asyncio
    async def test_a_credit_becomes_a_positive_credit(self, captured):
        await ingest_provider_feed(MagicMock(), BID, ACC, [_tx("c", 45000)],
                                   provider_name="basiq")
        assert captured[0]["amount_cents"] == 45000
        assert captured[0]["direction"] == "credit"

    @pytest.mark.asyncio
    async def test_a_debit_becomes_a_positive_amount_with_debit_direction(self, captured):
        await ingest_provider_feed(MagicMock(), BID, ACC, [_tx("d", -12500)],
                                   provider_name="basiq")
        assert captured[0]["amount_cents"] == 12500, "stored absolute, never negative"
        assert captured[0]["direction"] == "debit"

    @pytest.mark.asyncio
    async def test_a_zero_amount_line_is_skipped_not_given_an_arbitrary_direction(self, captured):
        stats = await ingest_provider_feed(MagicMock(), BID, ACC, [_tx("z", 0)],
                                           provider_name="basiq")
        assert captured == []
        assert stats["skipped"] == 1


class TestStreamShapes:
    @pytest.mark.asyncio
    async def test_it_accepts_the_async_iterator_the_protocol_specifies(self, captured):
        async def _stream():
            yield _tx("a-1", 1000)
            yield _tx("a-2", 2000)

        stats = await ingest_provider_feed(MagicMock(), BID, ACC, _stream(),
                                           provider_name="frollo")
        assert stats["ingested"] == 2
        assert len(captured) == 2

    @pytest.mark.asyncio
    async def test_every_provider_uses_the_same_door(self, captured):
        """Basiq, Frollo, a direct bank feed and a trust feed are one code path."""
        for name in ("basiq", "frollo", "direct_bank", "trust_account"):
            await ingest_provider_feed(MagicMock(), BID, ACC, [_tx(f"{name}-1", 500)],
                                       provider_name=name)
        assert {c["source_type"] for c in captured} == {"bank_feed"}
