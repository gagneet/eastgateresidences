"""
tests/backend/test_reconciliation_ui_contract.py

GAP-FIN-015 blocker 5: the reconciliation contract must expose portal balance,
ledger balance, delta, candidate transactions (with provenance), and match/ledger
result per unit — and must respect building isolation like every other route.

Copilot review (PR #492): the original building-wide implementation looped
per-unit awaits, each triggering a per-candidate match_review_queue.find_one() —
an N (units) x M (candidates) query explosion. get_building_reconciliation() now
batch-fetches ledger/candidate/queue documents in a fixed number of queries
regardless of unit count; test_building_wide_view_issues_bounded_query_count
locks this in.

Patch target: "routers.finance_reconciliation.db" (module-level import).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from routers.finance_reconciliation import get_building_reconciliation, get_unit_reconciliation

BUILDING_A = "13195"
BUILDING_B = "16244"

_MANAGER_USER = {"role": "strata_manager", "effective_role": "strata_manager", "email": "m@test.com"}
_OWNER_USER = {"role": "owner", "effective_role": "owner", "email": "o@test.com"}


def _cursor(items: list):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=items)
    return cursor


def _make_db(*, portal_snapshot=None, ledger=None, units=None, candidates=None, queue_docs=None):
    """Covers both endpoints: unit_levy_ledger.find_one (single-unit path) and
    unit_levy_ledger.find (building-wide batched path); demo_bank_transactions.find
    and match_review_queue.find are batched in both paths — see
    _candidates_for_unit()'s docstring for why the single-unit endpoint still
    batches its (bounded, one-unit) candidate/queue lookups."""
    db = MagicMock()
    db.staging_strata_web_snapshots.find_one = AsyncMock(return_value=portal_snapshot)
    db.unit_levy_ledger.find_one = AsyncMock(return_value=ledger)
    db.unit_levy_ledger.find = MagicMock(return_value=_cursor([ledger] if ledger else []))
    db.units.find = MagicMock(return_value=_cursor(units or []))
    db.demo_bank_transactions.find = MagicMock(return_value=_cursor(candidates or []))
    db.match_review_queue.find = MagicMock(return_value=_cursor(queue_docs or []))
    return db


@pytest.mark.asyncio
async def test_unit_row_reports_portal_ledger_delta_and_candidates():
    portal_snapshot = {
        "snapshot_date": "2026-04-15",
        "per_unit_balances": [{"lot_number": "TH078", "balance_cents": 55000}],
    }
    ledger = {"net_balance": 0.0}
    candidate = {
        "source_type": "strata_web_inferred_payment",
        "confidence": "medium",
        "provenance_class": "inferred_from_balance_delta",
        "evidence_type": "consecutive_snapshot_delta",
        "amount_cents": 55000,
        "requires_review": True,
        "finance_bank_transaction_ref": "pg-tx-1",
    }
    queue_doc = {
        "_id": "queue-item-1", "inbox_event_id": "pg-tx-1",
        "status": "pending", "match_type": "review", "receipt_id": None,
    }
    db = _make_db(portal_snapshot=portal_snapshot, ledger=ledger,
                   candidates=[candidate], queue_docs=[queue_doc])

    with patch("routers.finance_reconciliation.db", db):
        row = await get_unit_reconciliation(
            "TH078", year="2026", current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert row["portal_balance_cents"] == 55000
    assert row["ledger_balance_cents"] == 0
    assert row["delta_cents"] == 55000
    assert row["requires_reconciliation"] is True
    assert len(row["candidates"]) == 1
    assert row["candidates"][0]["match_status"] == "pending"
    assert row["candidates"][0]["queue_item_id"] == "queue-item-1"
    assert row["ledger_result"] is None  # not yet allocated


@pytest.mark.asyncio
async def test_unit_row_shows_ledger_result_once_allocated():
    candidate = {
        "source_type": "synthetic_from_budget",
        "confidence": "high",
        "provenance_class": "reconstruction",
        "evidence_type": "budget_uoe_reconstruction",
        "amount_cents": 55384,
        "requires_review": False,
        "finance_bank_transaction_ref": "pg-tx-2",
    }
    queue_doc = {"inbox_event_id": "pg-tx-2", "status": "allocated",
                 "match_type": "high_confidence_reconstruction", "receipt_id": "rcpt-1"}
    db = _make_db(portal_snapshot=None, ledger={"net_balance": 0.0},
                   candidates=[candidate], queue_docs=[queue_doc])

    with patch("routers.finance_reconciliation.db", db):
        row = await get_unit_reconciliation(
            "TH078", year="2021", current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert row["portal_balance_cents"] is None
    assert row["ledger_result"] == {"receipt_id": "rcpt-1", "status": "allocated"}


@pytest.mark.asyncio
async def test_owner_role_forbidden():
    db = _make_db()
    with patch("routers.finance_reconciliation.db", db):
        with pytest.raises(HTTPException) as exc:
            await get_unit_reconciliation(
                "TH078", year="2026", current_user=_OWNER_USER, building_id=BUILDING_A,
            )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_building_wide_view_scopes_units_query_to_caller_building():
    units = [{"unit_number": "1"}, {"unit_number": "2"}]
    db = _make_db(units=units, ledger={"net_balance": 0.0})

    with patch("routers.finance_reconciliation.db", db):
        result = await get_building_reconciliation(
            year="2026", current_user=_MANAGER_USER, building_id=BUILDING_B,
        )

    assert result["building_id"] == BUILDING_B
    assert len(result["units"]) == 2
    db.units.find.assert_called_once_with(
        {"building_id": BUILDING_B, "is_test_data": {"$ne": True}}, {"unit_number": 1, "_id": 0}
    )


@pytest.mark.asyncio
async def test_building_wide_view_issues_bounded_query_count_not_per_unit():
    """Copilot review regression: candidates x units must not multiply the query
    count. 50 units with 2 candidates each must still issue exactly one units
    query, one ledger query, one demo_bank_transactions query, and one
    match_review_queue query — never one per unit or one per candidate."""
    units = [{"unit_number": str(i)} for i in range(50)]
    ledger_docs = [{"unit_number": str(i), "net_balance": 0.0} for i in range(50)]
    tx_docs = []
    queue_docs = []
    for i in range(50):
        for c in range(2):
            ref = f"pg-tx-{i}-{c}"
            tx_docs.append({
                "unit_number": str(i), "source_type": "synthetic_from_budget",
                "confidence": "high", "amount_cents": 1000,
                "finance_bank_transaction_ref": ref,
            })
            queue_docs.append({"inbox_event_id": ref, "status": "pending", "receipt_id": None})

    db = MagicMock()
    db.staging_strata_web_snapshots.find_one = AsyncMock(return_value=None)
    db.units.find = MagicMock(return_value=_cursor(units))
    db.unit_levy_ledger.find = MagicMock(return_value=_cursor(ledger_docs))
    db.demo_bank_transactions.find = MagicMock(return_value=_cursor(tx_docs))
    db.match_review_queue.find = MagicMock(return_value=_cursor(queue_docs))

    with patch("routers.finance_reconciliation.db", db):
        result = await get_building_reconciliation(
            year="2026", current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert len(result["units"]) == 50
    assert all(len(row["candidates"]) == 2 for row in result["units"])
    db.units.find.assert_called_once()
    db.unit_levy_ledger.find.assert_called_once()
    db.demo_bank_transactions.find.assert_called_once()
    db.match_review_queue.find.assert_called_once()
    # Never the per-unit path's find_one methods.
    assert not hasattr(db.unit_levy_ledger, "find_one") or not db.unit_levy_ledger.find_one.called
