"""
tests/backend/test_bank_transactions_provenance_fields.py

GAP-FIN-015 blocker 2: Demo Bank / synthetic / inferred transactions carry
provenance fields (source_type, confidence, provenance_class, etc.) that must
survive the Mongo → Postgres sync step so the deterministic promotion path
(financial_matching.promote_high_confidence_reconstruction) and the
reconciliation UI can read them back from finance.bank_transactions.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from integrations.envelopes import BankTxObserved
from routers.bank_feeds import _tx_from_envelope, _insert_bank_transaction


def _event() -> BankTxObserved:
    return BankTxObserved(
        provider_txn_id="tx-synthetic-1",
        tenant_id="13195",
        account_ref="ADMIN-13195",
        occurred_at=datetime(2021, 3, 31, tzinfo=timezone.utc),
        amount_cents=55384,
        description="Synthetic Q1 2021 levy reconstruction",
    )


def test_tx_from_envelope_copies_provenance_fields_from_source_doc():
    source_doc = {
        "reference": "REF-1",
        "source_type": "synthetic_from_budget",
        "confidence": "high",
        "provenance_class": "reconstruction",
        "evidence_type": "budget_uoe_reconstruction",
        "formula_version": "historical-levy-reconstruction-v1",
        "source_snapshot_ids": ["snap-1", "snap-2"],
        "supersedes_event_id": None,
        "requires_review": False,
        "date_basis": "scheduled_due_date_offset",
        "unit_number": "TH078",
    }

    tx = _tx_from_envelope(_event(), source_doc)

    assert tx["source_type"] == "synthetic_from_budget"
    assert tx["confidence"] == "high"
    assert tx["provenance_class"] == "reconstruction"
    assert tx["evidence_type"] == "budget_uoe_reconstruction"
    assert tx["formula_version"] == "historical-levy-reconstruction-v1"
    assert tx["source_snapshot_ids"] == ["snap-1", "snap-2"]
    assert tx["requires_review"] is False
    assert tx["date_basis"] == "scheduled_due_date_offset"
    assert tx["unit_number"] == "TH078"


def test_tx_from_envelope_defaults_when_no_source_doc():
    """A real future provider (Basiq/PayID) has no Demo Bank Mongo document —
    provenance fields must default safely rather than raise."""
    tx = _tx_from_envelope(_event(), None)

    assert tx["source_type"] is None
    assert tx["confidence"] is None
    assert tx["source_snapshot_ids"] == []
    assert tx["requires_review"] is False
    assert tx["unit_number"] is None


def test_tx_from_envelope_transaction_origin_falls_back_to_bank_reconciled_when_no_source_doc():
    """GAP-ONBOARD-004 B1b (migration 0085): finance.bank_transactions.transaction_origin
    is NOT NULL. When there is NO Demo Bank Mongo document at all (a real, non-Demo-Bank
    provider event), that is genuinely a real, observed bank movement, so it is tagged
    'bank_reconciled' rather than hitting a NOT NULL violation at insert."""
    tx = _tx_from_envelope(_event(), None)

    assert tx["transaction_origin"] == "bank_reconciled"


class TestTransactionOriginSourceTypeFallback:
    """Self-audit fix (2026-08-10): the original fallback defaulted EVERY unset case to
    'bank_reconciled', including real Demo Bank data (CSV upload, manual entry, Strata-Web
    inference, seed) that demo_bank/ingestion.py's own docstring confirms leaves
    transaction_origin at its None default for every call site except
    import_historical_reconstruction(). Tagging those 'bank_reconciled' is a false
    provenance claim (they were never confirmed against a real bank feed), not just a
    missing one. These tests cover the corrected, source_type-aware fallback."""

    def test_csv_upload_maps_to_external_import(self):
        tx = _tx_from_envelope(_event(), {"source_type": "csv_upload"})
        assert tx["transaction_origin"] == "external_import"
        assert tx["transaction_origin"] != "bank_reconciled"

    def test_seed_maps_to_reconstructed_historical(self):
        tx = _tx_from_envelope(_event(), {"source_type": "seed"})
        assert tx["transaction_origin"] == "reconstructed_historical"

    def test_strata_web_inferred_payment_maps_to_consecutive_snapshot_delta(self):
        tx = _tx_from_envelope(_event(), {"source_type": "strata_web_inferred_payment"})
        assert tx["transaction_origin"] == "consecutive_snapshot_delta"

    def test_strata_web_payment_maps_to_strata_web_portal_scrape(self):
        tx = _tx_from_envelope(_event(), {"source_type": "strata_web_payment"})
        assert tx["transaction_origin"] == "strata_web_portal_scrape"

    def test_manual_maps_to_manual_adjustment(self):
        tx = _tx_from_envelope(_event(), {"source_type": "manual"})
        assert tx["transaction_origin"] == "manual_adjustment"

    def test_unknown_source_type_with_source_doc_falls_back_to_reconstructed_historical_not_bank_reconciled(self):
        """A source_doc exists (so this IS Demo Bank data), but its source_type isn't in the
        mapping -- must NOT claim the false certainty of 'bank_reconciled'."""
        tx = _tx_from_envelope(_event(), {"source_type": "some_future_source_type"})
        assert tx["transaction_origin"] == "reconstructed_historical"
        assert tx["transaction_origin"] != "bank_reconciled"

    def test_empty_source_doc_dict_also_falls_back_to_reconstructed_historical(self):
        """An empty (but non-None) source_doc -- source_type is None -- must not be
        mislabeled 'bank_reconciled' either; a truthy-but-empty dict still means real Demo
        Bank data exists, just with no source_type recorded."""
        tx = _tx_from_envelope(_event(), {})
        assert tx["transaction_origin"] == "reconstructed_historical"


def test_tx_from_envelope_transaction_origin_prefers_explicit_metadata():
    event = BankTxObserved(
        provider_txn_id="tx-synthetic-1",
        tenant_id="13195",
        account_ref="ADMIN-13195",
        occurred_at=datetime(2021, 3, 31, tzinfo=timezone.utc),
        amount_cents=55384,
        description="Synthetic Q1 2021 levy reconstruction",
        metadata={"transaction_origin": "reconstructed_historical"},
    )

    tx = _tx_from_envelope(event, {"transaction_origin": "portal_reconciliation"})

    assert tx["transaction_origin"] == "reconstructed_historical"


def test_tx_from_envelope_transaction_origin_falls_back_to_source_doc():
    tx = _tx_from_envelope(_event(), {"transaction_origin": "portal_reconciliation"})

    assert tx["transaction_origin"] == "portal_reconciliation"


class _Row:
    def __init__(self, value):
        self._value = value

    def __getitem__(self, idx):
        return self._value


class _Result:
    def __init__(self, first=None):
        self._first = first

    def first(self):
        return self._first


@pytest.mark.asyncio
async def test_insert_bank_transaction_passes_provenance_params_to_sql():
    session = MagicMock()
    session.execute = AsyncMock(return_value=_Result(first=_Row("pg-tx-001")))
    tx = {
        "posted_date": datetime(2021, 3, 31, tzinfo=timezone.utc),
        "description": "Synthetic Q1 2021 levy reconstruction",
        "reference": None,
        "amount_cents": 55384,
        "running_balance_cents": None,
        "external_transaction_id": "ext-synth-1",
        "provider": "demo_bank_feed",
        "source_type": "synthetic_from_budget",
        "confidence": "high",
        "provenance_class": "reconstruction",
        "evidence_type": "budget_uoe_reconstruction",
        "formula_version": "historical-levy-reconstruction-v1",
        "source_snapshot_ids": [],
        "supersedes_event_id": None,
        "requires_review": False,
        "date_basis": "scheduled_due_date_offset",
        "unit_number": "TH078",
    }

    result = await _insert_bank_transaction(
        session,
        tenant_id="00000000-0000-0000-0000-000000000001",
        scheme_id="00000000-0000-0000-0000-000000000002",
        trust_account_id="00000000-0000-0000-0000-000000000003",
        tx=tx,
    )

    assert result == "pg-tx-001"
    sql_text = str(session.execute.call_args.args[0])
    params = session.execute.call_args.args[1]
    assert "source_type" in sql_text and "unit_number" in sql_text
    assert params["source_type"] == "synthetic_from_budget"
    assert params["confidence"] == "high"
    assert params["unit_number"] == "TH078"
    assert params["requires_review"] is False
