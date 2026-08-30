"""
tests/backend/test_financial_matching_promotion.py

GAP-FIN-015 blocker 3: deterministic high-confidence promotion path for
reconstructed/synthetic transactions. These bypass the generic L1-L8
MatchingEngine scoring (which cannot auto-allocate them — L4 tops out at 0.85,
below the 0.90 threshold) but MUST still go through _post_payment_to_ledger(),
the single authorised ledger-write path, exactly like a human "allocate"
decision or engine auto-allocation.

Patch target: "routers.financial_matching.db" (module-level import).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from routers.financial_matching import (
    promote_high_confidence_reconstruction,
    promote_high_confidence_queue_item,
)

BUILDING_A = "13195"
BUILDING_B = "16244"
_NOW = datetime(2026, 7, 5, 10, 0, 0, tzinfo=timezone.utc).isoformat()

_MANAGER_USER = {"role": "strata_manager", "effective_role": "strata_manager",
                 "email": "manager@test.com", "id": "user-mgr-001"}
_OWNER_USER = {"role": "owner", "effective_role": "owner",
               "email": "owner@test.com", "id": "user-own-001"}


def _queue_doc(**overrides) -> dict:
    oid = ObjectId()
    base = {
        "_id": oid,
        "tenant_id": BUILDING_A,
        "building_id": BUILDING_A,
        "inbox_event_id": "pg-tx-synth-001",
        "status": "unidentified",
        "match_type": "unidentified",
        "tx": {"amount_cents": 55384, "description": "Synthetic Q1 2021 reconstruction"},
        "is_test_data": False,
    }
    base.update(overrides)
    return base


def _tx_doc(**overrides) -> dict:
    # source_type defaults to "historical_mongo". "historical_reconstruction" (the
    # CURRENT reconstruction generator's source_type -- added to the allowlist
    # 2026-08-01) is also promotable; see
    # test_promotes_high_confidence_historical_reconstruction_transaction below.
    # "synthetic_from_budget" was removed from that allowlist (fabricated
    # payments, not confirmed-by-construction) — see
    # test_synthetic_from_budget_rejected_409 below for the regression test.
    base = {
        "building_id": BUILDING_A,
        "finance_bank_transaction_ref": "pg-tx-synth-001",
        "source_type": "historical_mongo",
        "confidence": "high",
        "requires_review": False,
        "unit_number": "TH078",
    }
    base.update(overrides)
    return base


def _make_db(*, queue_doc=None, tx_doc=None):
    db = MagicMock()
    db.match_review_queue.find_one = AsyncMock(return_value=queue_doc)
    db.match_review_queue.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db.demo_bank_transactions.find_one = AsyncMock(return_value=tx_doc)
    db.event_log.insert_one = AsyncMock(return_value=MagicMock(inserted_id="log-1"))
    return db


@pytest.mark.asyncio
async def test_promotes_high_confidence_historical_mongo_transaction_and_posts_receipt():
    queue_doc = _queue_doc()
    tx_doc = _tx_doc()
    db = _make_db(queue_doc=queue_doc, tx_doc=tx_doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger",
               AsyncMock(return_value="rcpt-synth-0001")) as post_mock:
        result = await promote_high_confidence_reconstruction(
            queue_item_id=str(queue_doc["_id"]), building_id=BUILDING_A,
        )

    assert result["status"] == "allocated"
    assert result["match_type"] == "high_confidence_reconstruction"
    assert result["lot_id"] == "TH078"
    assert result["receipt_id"] == "rcpt-synth-0001"
    post_mock.assert_awaited_once()

    assert db.match_review_queue.update_one.await_count == 2
    claim_filter, claim_ops = db.match_review_queue.update_one.await_args_list[0].args
    assert claim_filter == {"_id": queue_doc["_id"], "status": "unidentified"}
    assert claim_ops["$set"]["status"] == "posting"

    final_filter, final_ops = db.match_review_queue.update_one.await_args_list[1].args
    assert final_filter == {"_id": queue_doc["_id"], "status": "posting"}
    assert final_ops["$set"]["status"] == "allocated"
    assert final_ops["$set"]["match_type"] == "high_confidence_reconstruction"

    event_doc = db.event_log.insert_one.call_args[0][0]
    assert event_doc["match_type"] == "high_confidence_reconstruction"
    assert event_doc["receipt_id"] == "rcpt-synth-0001"


@pytest.mark.asyncio
async def test_promotes_high_confidence_historical_reconstruction_transaction():
    """Regression for the 2026-08-01 finding: historical_reconstruction (the
    current reconstruction generator's source_type, per
    services/east_gate_levy_income_reconstruction.py) was never added to
    _PROMOTABLE_SOURCE_TYPES when it superseded the older synthetic_from_budget
    approach. Confirmed live: 2,388/2,388 non-archived historical_reconstruction
    Demo Bank transactions for the one production building on this platform are
    confidence="high" and not requires_review, yet every one sat permanently
    "pending" because historical_mongo (the only prior allowed source_type)
    matched zero real transactions for that building."""
    queue_doc = _queue_doc()
    tx_doc = _tx_doc(source_type="historical_reconstruction")
    db = _make_db(queue_doc=queue_doc, tx_doc=tx_doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger",
               AsyncMock(return_value="rcpt-synth-0002")) as post_mock:
        result = await promote_high_confidence_reconstruction(
            queue_item_id=str(queue_doc["_id"]), building_id=BUILDING_A,
        )

    assert result["status"] == "allocated"
    assert result["source_type"] == "historical_reconstruction"
    post_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_medium_confidence_transaction_rejected_409():
    queue_doc = _queue_doc()
    tx_doc = _tx_doc(confidence="medium")
    db = _make_db(queue_doc=queue_doc, tx_doc=tx_doc)

    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await promote_high_confidence_reconstruction(
                queue_item_id=str(queue_doc["_id"]), building_id=BUILDING_A,
            )

    assert exc.value.status_code == 409
    db.match_review_queue.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_requires_review_transaction_rejected_409():
    queue_doc = _queue_doc()
    tx_doc = _tx_doc(requires_review=True)
    db = _make_db(queue_doc=queue_doc, tx_doc=tx_doc)

    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await promote_high_confidence_reconstruction(
                queue_item_id=str(queue_doc["_id"]), building_id=BUILDING_A,
            )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_non_promotable_source_type_rejected_409():
    queue_doc = _queue_doc()
    tx_doc = _tx_doc(source_type="csv_upload")  # real bank CSV row, not a reconstruction
    db = _make_db(queue_doc=queue_doc, tx_doc=tx_doc)

    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await promote_high_confidence_reconstruction(
                queue_item_id=str(queue_doc["_id"]), building_id=BUILDING_A,
            )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_synthetic_from_budget_rejected_409():
    """Regression test: East Gate 13195 investigation (2026-07-22) found
    "synthetic_from_budget" Demo Bank rows are fabricated payments (amount =
    unit_uoe x levy_per_uoe_quarterly, randomized payment date — not an
    observed transaction), yet they were in _PROMOTABLE_SOURCE_TYPES with
    confidence="high"/requires_review=False, making them eligible for this
    endpoint's ledger-posting fast-path with zero human review. This source_type
    must never re-enter _PROMOTABLE_SOURCE_TYPES — if this test starts failing
    because someone re-added it, that is the bug being guarded against here,
    not a stale test."""
    queue_doc = _queue_doc()
    tx_doc = _tx_doc(source_type="synthetic_from_budget")
    db = _make_db(queue_doc=queue_doc, tx_doc=tx_doc)

    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await promote_high_confidence_reconstruction(
                queue_item_id=str(queue_doc["_id"]), building_id=BUILDING_A,
            )

    assert exc.value.status_code == 409
    assert "synthetic_from_budget" in exc.value.detail
    db.match_review_queue.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_underlying_transaction_rejected_404():
    """Copilot review: a queue item whose underlying Demo Bank transaction doesn't
    exist is a genuinely missing resource, not an eligibility conflict — 404, not
    409, matching this function's own docstring contract."""
    queue_doc = _queue_doc()
    db = _make_db(queue_doc=queue_doc, tx_doc=None)

    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await promote_high_confidence_reconstruction(
                queue_item_id=str(queue_doc["_id"]), building_id=BUILDING_A,
            )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_already_allocated_item_rejected_409():
    queue_doc = _queue_doc(status="allocated")
    db = _make_db(queue_doc=queue_doc, tx_doc=_tx_doc())

    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await promote_high_confidence_reconstruction(
                queue_item_id=str(queue_doc["_id"]), building_id=BUILDING_A,
            )

    assert exc.value.status_code == 409
    db.demo_bank_transactions.find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_ledger_post_failure_reverts_status_and_raises_502():
    queue_doc = _queue_doc()
    tx_doc = _tx_doc()
    db = _make_db(queue_doc=queue_doc, tx_doc=tx_doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger",
               AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await promote_high_confidence_reconstruction(
                queue_item_id=str(queue_doc["_id"]), building_id=BUILDING_A,
            )

    assert exc.value.status_code == 502
    revert_filter, revert_ops = db.match_review_queue.update_one.await_args_list[1].args
    assert revert_filter == {"_id": queue_doc["_id"], "status": "posting"}
    assert revert_ops["$set"]["status"] == "unidentified"
    db.event_log.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_cross_building_isolation_returns_404():
    """A queue item belonging to building 16244 must never be promotable via a
    building_id=13195 call."""
    queue_doc = _queue_doc(building_id=BUILDING_B, tenant_id=BUILDING_B)
    db = _make_db(queue_doc=queue_doc, tx_doc=_tx_doc(building_id=BUILDING_B))

    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await promote_high_confidence_reconstruction(
                queue_item_id=str(queue_doc["_id"]), building_id=BUILDING_A,
            )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_endpoint_rejects_owner_role_403():
    queue_doc = _queue_doc()
    db = _make_db(queue_doc=queue_doc, tx_doc=_tx_doc())

    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await promote_high_confidence_queue_item(
                str(queue_doc["_id"]), current_user=_OWNER_USER, building_id=BUILDING_A,
            )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_endpoint_allows_manager_and_delegates():
    queue_doc = _queue_doc()
    db = _make_db(queue_doc=queue_doc, tx_doc=_tx_doc())

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger",
               AsyncMock(return_value="rcpt-0002")):
        result = await promote_high_confidence_queue_item(
            str(queue_doc["_id"]), current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert result["receipt_id"] == "rcpt-0002"
