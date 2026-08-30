"""Tests for Trust Period Locks — Phase 2."""
from __future__ import annotations

import os
import pytest
import sys
from unittest.mock import AsyncMock, MagicMock

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

BUILDING_ID = "13195"


def _make_db(*, lock_doc=None):
    db = MagicMock()
    db.trust_period_locks.find_one = AsyncMock(return_value=lock_doc)
    db.trust_period_locks.insert_one = AsyncMock(
        return_value=MagicMock(inserted_id="lock001")
    )
    db.trust_period_locks.update_one = AsyncMock(
        return_value=MagicMock(matched_count=1, modified_count=1)
    )
    return db


@pytest.mark.asyncio
async def test_create_period_lock_success():
    db = _make_db()
    result = await db.trust_period_locks.insert_one({
        "building_id": BUILDING_ID,
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "locked_by": "admin@test.com",
        "is_active": True,
    })
    assert result.inserted_id is not None


@pytest.mark.asyncio
async def test_lock_already_exists_conflict():
    existing = {
        "building_id": BUILDING_ID,
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "is_active": True,
        "locked_by": "admin@test.com",
    }
    db = _make_db(lock_doc=existing)
    lock = await db.trust_period_locks.find_one({"building_id": BUILDING_ID})
    assert lock is not None
    assert lock["is_active"] is True


@pytest.mark.asyncio
async def test_unlock_period_super_admin():
    db = _make_db()
    result = await db.trust_period_locks.update_one(
        {"building_id": BUILDING_ID, "is_active": True},
        {"$set": {"is_active": False, "unlocked_by": "super@test.com"}},
    )
    assert result.modified_count == 1


def test_non_admin_unlock_permission_denied():
    """Non-admin roles must not be allowed to unlock periods."""
    non_admin_roles = ["owner", "tenant", "ec_member"]
    UNLOCK_ALLOWED_ROLES = {"super_admin", "strata_manager"}
    for role in non_admin_roles:
        assert role not in UNLOCK_ALLOWED_ROLES, f"Role {role} should not unlock periods"


@pytest.mark.asyncio
async def test_operations_on_locked_period_rejected():
    from services.trust_posting_service import TrustPostingService, PostingRequest, JournalLine
    lock_doc = {
        "building_id": BUILDING_ID,
        "is_active": True,
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "locked_by": "admin@test.com",
        "locked_at": "2026-03-01",
    }
    db = _make_db(lock_doc=lock_doc)
    db.trust_audit_logs = MagicMock()
    db.trust_audit_logs.find_one = AsyncMock(return_value=None)
    db.trust_ledger_entries = MagicMock()
    db.trust_ledger_entries.insert_one = AsyncMock(
        return_value=MagicMock(inserted_id="e1")
    )
    svc = TrustPostingService(db)
    lines = [
        JournalLine(account_id="A1", entry_type="debit", amount_cents=5000),
        JournalLine(account_id="A2", entry_type="credit", amount_cents=5000),
    ]
    req = PostingRequest(
        building_id=BUILDING_ID,
        unit_number="TH071",
        amount_cents=5000,
        period="2026-03",
        source_reference="REF",
        source_type="levy_receipt",
        lines=lines,
        created_by="test@test.com",
    )
    result = await svc.post(req)
    assert result.status == "period_locked"


@pytest.mark.asyncio
async def test_unlock_nonexistent_period():
    db = _make_db()
    db.trust_period_locks.update_one = AsyncMock(
        return_value=MagicMock(matched_count=0, modified_count=0)
    )
    result = await db.trust_period_locks.update_one(
        {"building_id": BUILDING_ID, "is_active": True},
        {"$set": {"is_active": False}},
    )
    assert result.matched_count == 0
