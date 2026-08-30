"""Tests for Trust Posting Service — Phase 2."""
from __future__ import annotations

import os
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from services.trust_posting_service import (
    TrustPostingService, PostingRequest, JournalLine
)

BUILDING_ID = "13195"


def _make_balanced_request(**kwargs):
    lines = [
        JournalLine(account_id="ACC1", entry_type="debit", amount_cents=10000),
        JournalLine(account_id="ACC2", entry_type="credit", amount_cents=10000),
    ]
    defaults = dict(
        building_id=BUILDING_ID,
        unit_number="TH087",
        amount_cents=10000,
        period="2026-03",
        source_reference="REF-001",
        source_type="levy_receipt",
        lines=lines,
        created_by="test@example.com",
    )
    defaults.update(kwargs)
    return PostingRequest(**defaults)


def _make_db(*, locked=False, existing_id=None):
    db = MagicMock()
    db.trust_period_locks.find_one = AsyncMock(
        return_value={"locked_by": "admin@test.com", "locked_at": "2026-03-01"} if locked else None
    )
    db.trust_audit_logs.find_one = AsyncMock(
        return_value={"metadata": {"entry_id": existing_id}} if existing_id else None
    )
    db.trust_ledger_entries.insert_one = AsyncMock(
        return_value=MagicMock(inserted_id="507f1f77bcf86cd799439011")
    )
    db.trust_audit_logs.insert_one = AsyncMock(return_value=None)
    return db


@pytest.mark.asyncio
async def test_balanced_posting_success():
    db = _make_db()
    svc = TrustPostingService(db)
    result = await svc.post(_make_balanced_request())
    assert result.status == "success"
    assert result.entry_id is not None


@pytest.mark.asyncio
async def test_unbalanced_posting_raises():
    db = _make_db()
    svc = TrustPostingService(db)
    lines = [
        JournalLine(account_id="ACC1", entry_type="debit", amount_cents=10000),
        JournalLine(account_id="ACC2", entry_type="credit", amount_cents=5000),
    ]
    req = _make_balanced_request(lines=lines)
    result = await svc.post(req)
    assert result.status == "unbalanced"


@pytest.mark.asyncio
async def test_idempotent_replay():
    db = _make_db(existing_id="existing123")
    svc = TrustPostingService(db)
    result = await svc.post(_make_balanced_request())
    assert result.status == "idempotent_replay"
    assert result.existing_id is not None


@pytest.mark.asyncio
async def test_period_locked_rejection():
    db = _make_db(locked=True)
    svc = TrustPostingService(db)
    result = await svc.post(_make_balanced_request())
    assert result.status == "period_locked"


@pytest.mark.asyncio
async def test_audit_metadata_completeness():
    captured = {}
    db = _make_db()
    original_insert = db.trust_audit_logs.insert_one

    async def capture_audit(doc):
        captured.update(doc)
        return MagicMock()

    db.trust_audit_logs.insert_one = capture_audit
    svc = TrustPostingService(db)
    await svc.post(_make_balanced_request())

    assert "building_id" in captured
    assert "action" in captured
    assert "performed_by" in captured
    assert "created_at" in captured
    meta = captured.get("metadata", {})
    assert "idempotency_key" in meta
    assert "entry_id" in meta


def test_float_amount_raises_type_error():
    with pytest.raises(TypeError):
        JournalLine(account_id="ACC1", entry_type="debit", amount_cents=100.0)
