"""Unit tests for scripts/historical_levy_payment_posting.py.

run_apply()'s residue-reference and dual-control/status guard clauses all execute BEFORE any
Postgres session is opened, so they're testable by patching only the Mongo client + the batch
lookup helper — no real database connection required (mirrors
test_historical_levy_charge_posting_script.py). The _fund_bucket()/_period_label() helpers are pure
and tested directly.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.historical_levy_payment_posting import (
    _fund_bucket,
    _period_label,
    run_apply,
)

_MODULE = "scripts.historical_levy_payment_posting"

BUILDING_ID = "16244"
BATCH_ID = str(uuid.uuid4())
APPROVED_BY = uuid.uuid4()
EXECUTED_BY = uuid.uuid4()
GOOD_REF = "reverse_duplicate_levy_payments_20260803.py run 2026-08-05"


def _apply_kwargs(**overrides) -> dict:
    defaults = dict(
        building_id=BUILDING_ID, batch_id=BATCH_ID, executed_by=EXECUTED_BY,
        residue_reversed_ref=GOOD_REF, from_year=2021, to_year=2025,
        as_of_date=__import__("datetime").date(2026, 8, 5), is_test_data=False,
    )
    defaults.update(overrides)
    return defaults


class TestPureHelpers:
    def test_fund_bucket_maps_capital_works_and_sinking_to_sinking(self):
        assert _fund_bucket("capital_works") == "sinking"
        assert _fund_bucket("sinking") == "sinking"

    def test_fund_bucket_maps_everything_else_to_admin(self):
        assert _fund_bucket("admin") == "admin"
        assert _fund_bucket("administrative") == "admin"
        assert _fund_bucket("") == "admin"

    def test_period_label_uses_quarter_index(self):
        assert _period_label(1) == "Q1"
        assert _period_label(4) == "Q4"

    def test_period_label_zero_is_neutral_not_q0(self):
        # quarter_no of 0 means "unknown/unset" — must not claim a real quarter.
        assert _period_label(0) == "P0"


def _mock_mongo(monkeypatch, batch_doc):
    """Patch the Mongo client + _get_batch_doc so run_apply's guard clauses run without a DB.
    Any test that reaches these has already passed the residue-ref guard."""
    mongo_db = MagicMock()
    mock_client = MagicMock()
    mock_client.__getitem__.return_value = mongo_db
    monkeypatch.setattr(f"{_MODULE}.AsyncIOMotorClient", lambda *a, **k: mock_client)
    monkeypatch.setattr(f"{_MODULE}._mongo_url_and_db", lambda: ("mongodb://fake", "fake_db"))
    monkeypatch.setattr(f"{_MODULE}._get_batch_doc", AsyncMock(return_value=batch_doc))
    return mongo_db


class TestRunApplyGuardClauses:
    @pytest.mark.asyncio
    async def test_refuses_missing_residue_reversed_ref(self, monkeypatch):
        # Guard #1 runs before Mongo is even opened, so no mock is needed. Patch the client to a
        # bomb to prove it is never reached.
        def _boom(*a, **k):
            raise AssertionError("Mongo must not be opened before the residue-ref guard passes")
        monkeypatch.setattr(f"{_MODULE}.AsyncIOMotorClient", _boom)
        with pytest.raises(SystemExit, match="residue-reversed-ref"):
            await run_apply(**_apply_kwargs(residue_reversed_ref="   "))

    @pytest.mark.asyncio
    async def test_refuses_unapproved_batch(self, monkeypatch):
        _mock_mongo(monkeypatch, batch_doc={"status": "extracted", "batch_id": BATCH_ID})
        with pytest.raises(SystemExit, match="requires 'approved'"):
            await run_apply(**_apply_kwargs())

    @pytest.mark.asyncio
    async def test_refuses_missing_approved_by(self, monkeypatch):
        _mock_mongo(monkeypatch, batch_doc={"status": "approved", "batch_id": BATCH_ID, "approved_by": None})
        with pytest.raises(SystemExit, match="no approved_by"):
            await run_apply(**_apply_kwargs())

    @pytest.mark.asyncio
    async def test_refuses_same_approver_and_executor(self, monkeypatch):
        _mock_mongo(monkeypatch, batch_doc={
            "status": "approved", "batch_id": BATCH_ID, "approved_by": str(EXECUTED_BY),
        })
        with pytest.raises(SystemExit, match="dual control"):
            await run_apply(**_apply_kwargs())
