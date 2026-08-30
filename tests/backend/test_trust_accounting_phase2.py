"""
tests/backend/test_trust_accounting_phase2.py — Phase 2 router-level tests.

Covers the Phase 2 enhancements to POST /trust/journal:
  - Period guard: OPEN → 201, non-OPEN → 409, not-found → 404
  - fund_type included in period lookup (multi-fund isolation)
  - Domain Journal validation for debit/credit entry types (exact integer-cent balance)
  - debit_total_cents / credit_total_cents / period_id stored on persisted entry
  - Cross-building isolation: period from BUILDING_A unreachable from BUILDING_B
  - Backward compatibility: omitting period_id bypasses the period guard
  - effective_role: raw owner with elevated role can access, raw manager with guest role blocked

Patch target: "routers.trust_accounting._get_db"
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from bson import ObjectId
from fastapi import HTTPException

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

# ─── Constants ────────────────────────────────────────────────────────────────

BUILDING_A = "16244"
BUILDING_B = "13195"

_ACCT_A = str(ObjectId())
_ACCT_B = str(ObjectId())

_MANAGER = {"id": "mgr-1", "role": "strata_manager", "full_name": "Test Manager"}
_OWNER = {"id": "own-1", "role": "owner", "full_name": "Test Owner"}

_OPEN_PERIOD = {
    "building_id": BUILDING_A,
    "period_id": "2026-Q1",
    "fund_type": "admin_fund",
    "state": "open",
    "opened_at": "2026-01-01T00:00:00+00:00",
    "opened_by": "mgr-1",
}

_CLOSED_PERIOD = {**_OPEN_PERIOD, "state": "closed"}
_RECONCILING_PERIOD = {**_OPEN_PERIOD, "state": "reconciling"}
_LOCKED_PERIOD = {**_OPEN_PERIOD, "state": "locked"}


def _make_db(period_doc=None, account_count=2):
    db = MagicMock()
    db.financial_periods.find_one = AsyncMock(return_value=period_doc)
    db.trust_ledger_accounts.count_documents = AsyncMock(return_value=account_count)
    db.trust_ledger_entries.insert_one = AsyncMock(
        return_value=MagicMock(inserted_id=ObjectId())
    )
    db.trust_ledger_accounts.bulk_write = AsyncMock(return_value=MagicMock())
    return db


def _payload(amount=100.0):
    """Minimal balanced debit/credit payload."""
    from routers.trust_accounting import JournalEntryCreate  # noqa: F401 — triggers import
    from models.trust_accounting import JournalEntryCreate, JournalLineCreate, FundType, EntryType
    return JournalEntryCreate(
        building_id=BUILDING_A,
        fund_type=FundType.ADMIN,
        date="2026-04-23",
        reference="TEST-001",
        narration="Phase 2 test entry",
        lines=[
            JournalLineCreate(account_id=_ACCT_A, entry_type=EntryType.DEBIT, amount=amount),
            JournalLineCreate(account_id=_ACCT_B, entry_type=EntryType.CREDIT, amount=amount),
        ],
    )


def _unbalanced_payload():
    from models.trust_accounting import JournalEntryCreate, JournalLineCreate, FundType, EntryType
    return JournalEntryCreate(
        building_id=BUILDING_A,
        fund_type=FundType.ADMIN,
        date="2026-04-23",
        reference="TEST-UNBAL",
        narration="Unbalanced test",
        lines=[
            JournalLineCreate(account_id=_ACCT_A, entry_type=EntryType.DEBIT, amount=100.01),
            JournalLineCreate(account_id=_ACCT_B, entry_type=EntryType.CREDIT, amount=100.00),
        ],
    )


# ── Period guard ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_period_allows_post():
    """Posting to an OPEN period must succeed with 201."""
    from routers.trust_accounting import post_journal_entry
    db = _make_db(period_doc=_OPEN_PERIOD)
    with patch("routers.trust_accounting._get_db", return_value=db), \
            patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock):
        result = await post_journal_entry(
            payload=_payload(),
            period_id="2026-Q1",
            building_id=BUILDING_A,
            current_user=_MANAGER,
        )
    assert result.building_id == BUILDING_A


@pytest.mark.asyncio
async def test_closed_period_raises_409():
    """Posting to a CLOSED period must raise HTTP 409 with a reversal hint."""
    from routers.trust_accounting import post_journal_entry
    from fastapi import HTTPException
    db = _make_db(period_doc=_CLOSED_PERIOD)
    with patch("routers.trust_accounting._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await post_journal_entry(
                payload=_payload(),
                period_id="2026-Q1",
                building_id=BUILDING_A,
                current_user=_MANAGER,
            )
    assert exc.value.status_code == 409
    assert "reversal" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_reconciling_period_raises_409():
    from routers.trust_accounting import post_journal_entry
    from fastapi import HTTPException
    db = _make_db(period_doc=_RECONCILING_PERIOD)
    with patch("routers.trust_accounting._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await post_journal_entry(
                payload=_payload(),
                period_id="2026-Q1",
                building_id=BUILDING_A,
                current_user=_MANAGER,
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_locked_period_raises_409():
    from routers.trust_accounting import post_journal_entry
    from fastapi import HTTPException
    db = _make_db(period_doc=_LOCKED_PERIOD)
    with patch("routers.trust_accounting._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await post_journal_entry(
                payload=_payload(),
                period_id="2026-Q1",
                building_id=BUILDING_A,
                current_user=_MANAGER,
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_unknown_period_raises_404():
    """If period_id doesn't exist for this building, return 404."""
    from routers.trust_accounting import post_journal_entry
    from fastapi import HTTPException
    db = _make_db(period_doc=None)
    with patch("routers.trust_accounting._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await post_journal_entry(
                payload=_payload(),
                period_id="nonexistent-period",
                building_id=BUILDING_A,
                current_user=_MANAGER,
            )
    assert exc.value.status_code == 404


# ── fund_type isolation in period lookup ─────────────────────────────────────

@pytest.mark.asyncio
async def test_period_lookup_includes_fund_type():
    """financial_periods.find_one must filter on fund_type, not just period_id."""
    from routers.trust_accounting import post_journal_entry
    db = _make_db(period_doc=_OPEN_PERIOD)
    with patch("routers.trust_accounting._get_db", return_value=db), \
            patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock):
        await post_journal_entry(
            payload=_payload(),
            period_id="2026-Q1",
            building_id=BUILDING_A,
            current_user=_MANAGER,
        )
    query = db.financial_periods.find_one.call_args[0][0]
    assert query["fund_type"] == "admin_fund"
    assert query["building_id"] == BUILDING_A
    assert query["period_id"] == "2026-Q1"


# ── Cross-building isolation ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cross_building_period_not_accessible():
    """A period owned by BUILDING_A must not be reachable when posting from BUILDING_B."""
    from routers.trust_accounting import post_journal_entry
    from fastapi import HTTPException
    # DB returns None because building_id filter is BUILDING_B but period belongs to BUILDING_A
    db = _make_db(period_doc=None)
    with patch("routers.trust_accounting._get_db", return_value=db), \
            patch(
                "routers.trust_accounting.are_cutover_features_enabled",
                new_callable=AsyncMock, return_value=False,
            ):
        # are_cutover_features_enabled mocked False so _block_v1_write_if_pg_primary's
        # trust_pg_ledger_enabled 409 (live for building 13195 since 2026-08-04) doesn't
        # mask the 404 this test actually exercises (cross-building period isolation).
        with pytest.raises(HTTPException) as exc:
            await post_journal_entry(
                payload=_payload(),
                period_id="2026-Q1",
                building_id=BUILDING_B,
                current_user=_MANAGER,
            )
    assert exc.value.status_code == 404
    query = db.financial_periods.find_one.call_args[0][0]
    assert query["building_id"] == BUILDING_B  # query used BUILDING_B, not BUILDING_A


# ── Backward compatibility ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_period_id_skips_period_guard():
    """Omitting period_id must not call financial_periods.find_one (legacy path)."""
    from routers.trust_accounting import post_journal_entry
    db = _make_db()
    with patch("routers.trust_accounting._get_db", return_value=db), \
            patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock):
        result = await post_journal_entry(
            payload=_payload(),
            period_id=None,
            building_id=BUILDING_A,
            current_user=_MANAGER,
        )
    db.financial_periods.find_one.assert_not_called()
    assert result.building_id == BUILDING_A


# ── Domain validation (debit/credit entry types) ──────────────────────────────

@pytest.mark.asyncio
async def test_unbalanced_debit_credit_raises_400():
    """An unbalanced debit/credit journal must be rejected with HTTP 400."""
    from routers.trust_accounting import post_journal_entry
    from fastapi import HTTPException
    db = _make_db()
    with patch("routers.trust_accounting._get_db", return_value=db):
        with pytest.raises(HTTPException) as exc:
            await post_journal_entry(
                payload=_unbalanced_payload(),
                period_id=None,
                building_id=BUILDING_A,
                current_user=_MANAGER,
            )
    assert exc.value.status_code == 400
    assert "balanced" in exc.value.detail.lower() or "balance" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_balanced_by_1_cent_passes():
    """Exactly balanced (10000 cents each side) must succeed."""
    from routers.trust_accounting import post_journal_entry
    db = _make_db()
    with patch("routers.trust_accounting._get_db", return_value=db), \
            patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock):
        result = await post_journal_entry(
            payload=_payload(amount=100.00),  # = 10000 cents each side
            period_id=None,
            building_id=BUILDING_A,
            current_user=_MANAGER,
        )
    assert result.building_id == BUILDING_A


# ── Integer-cent fields stored on entry ───────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_stores_debit_total_cents():
    """Persisted document must include debit_total_cents as integer."""
    from routers.trust_accounting import post_journal_entry
    db = _make_db()
    with patch("routers.trust_accounting._get_db", return_value=db), \
            patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock):
        await post_journal_entry(
            payload=_payload(amount=150.00),
            period_id=None,
            building_id=BUILDING_A,
            current_user=_MANAGER,
        )
    inserted_doc = db.trust_ledger_entries.insert_one.call_args[0][0]
    assert inserted_doc["debit_total_cents"] == 15000
    assert inserted_doc["credit_total_cents"] == 15000
    assert isinstance(inserted_doc["debit_total_cents"], int)


@pytest.mark.asyncio
async def test_entry_stores_period_id():
    """If period_id was supplied, it must be persisted on the entry document."""
    from routers.trust_accounting import post_journal_entry
    db = _make_db(period_doc=_OPEN_PERIOD)
    with patch("routers.trust_accounting._get_db", return_value=db), \
            patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock):
        await post_journal_entry(
            payload=_payload(),
            period_id="2026-Q1",
            building_id=BUILDING_A,
            current_user=_MANAGER,
        )
    inserted_doc = db.trust_ledger_entries.insert_one.call_args[0][0]
    assert inserted_doc["period_id"] == "2026-Q1"


@pytest.mark.asyncio
async def test_entry_period_id_none_when_not_supplied():
    """If period_id was not supplied, the entry stores period_id=None."""
    from routers.trust_accounting import post_journal_entry
    db = _make_db()
    with patch("routers.trust_accounting._get_db", return_value=db), \
            patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock):
        await post_journal_entry(
            payload=_payload(),
            period_id=None,
            building_id=BUILDING_A,
            current_user=_MANAGER,
        )
    inserted_doc = db.trust_ledger_entries.insert_one.call_args[0][0]
    assert inserted_doc["period_id"] is None


# ── Role guards ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_owner_role_blocked_from_journal_post():
    """Raw owner must receive HTTP 403 — trust ledger is manager-only."""
    from routers.trust_accounting import post_journal_entry
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await post_journal_entry(
            payload=_payload(),
            period_id=None,
            building_id=BUILDING_A,
            current_user=_OWNER,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_effective_role_allows_elevated_owner():
    """effective_role='strata_manager' on a raw-owner must pass the finance guard."""
    from routers.trust_accounting import post_journal_entry
    elevated = {"id": "eo-1", "role": "owner", "effective_role": "strata_manager", "full_name": "Elevated"}
    db = _make_db()
    with patch("routers.trust_accounting._get_db", return_value=db), \
            patch("routers.trust_accounting.create_audit_log", new_callable=AsyncMock):
        result = await post_journal_entry(
            payload=_payload(),
            period_id=None,
            building_id=BUILDING_A,
            current_user=elevated,
        )
    assert result.building_id == BUILDING_A


@pytest.mark.asyncio
async def test_effective_role_blocks_downgraded_manager():
    """effective_role='guest' on a raw-manager must be blocked with 403."""
    from routers.trust_accounting import post_journal_entry
    from fastapi import HTTPException
    downgraded = {"id": "dm-1", "role": "strata_manager", "effective_role": "guest"}
    with pytest.raises(HTTPException) as exc:
        await post_journal_entry(
            payload=_payload(),
            period_id=None,
            building_id=BUILDING_A,
            current_user=downgraded,
        )
    assert exc.value.status_code == 403
