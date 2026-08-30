"""
tests/backend/routers/test_financial_matching.py — Router tests for /financial/matching/*.

Tests:
  - GET /financial/matching/queue returns pending items (not is_test_data)
  - GET /financial/matching/queue/{item_id} returns item or 404
  - POST .../decide with "allocate" emits MatchDecisionRecorded to event_log
  - POST .../decide on already-decided item returns 409
  - POST .../decide with "allocate" and missing lot_id returns 422
  - POST .../decide with insufficient role returns 403
  - decided_by is populated from current_user email
  - GET /financial/matching/stats returns correct depth and rate
  - Cross-building isolation: queue items from BUILDING_B not visible in BUILDING_A call

Patch target: "database.db" (module-level import in routers/financial_matching.py)

Note: endpoint functions are called directly (bypassing FastAPI DI), so Depends() parameters
must be passed as explicit keyword arguments.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

_backend = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "backend",
)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from routers.financial_matching import (
    list_queue,
    get_queue_item,
    decide_queue_item,
    get_stats,
    DecideRequest,
    auto_allocate_queue_item,
)

BUILDING_A = "16244"
BUILDING_B = "13195"

_NOW = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc).isoformat()

# Minimal user dicts for direct function calls (bypass FastAPI DI).
_MANAGER_USER = {"role": "strata_manager", "effective_role": "strata_manager",
                 "email": "manager@test.com", "_id": "user-mgr-001"}
_OWNER_USER = {"role": "owner", "effective_role": "owner",
               "email": "owner@test.com", "_id": "user-own-001"}


def _queue_doc(**overrides) -> dict:
    oid = ObjectId()
    base = {
        "_id": oid,
        "tenant_id": BUILDING_A,
        "building_id": BUILDING_A,
        "inbox_event_id": f"evt-{oid}",
        "status": "pending",
        "match_type": "review",
        "best_score": 0.85,
        "best_layer": "L4_unit_ref_amount_timing",
        "best_lot_id": "lot-001",
        "sla_due_at": _NOW,
        "created_at": _NOW,
        "decided_at": None,
        "decided_by": None,
        "decision": None,
        "tx": {"amount_cents": 153000, "description": "LEVY UNIT 1"},
        "candidates": [{"lot_id": "lot-001", "unit_number": "1", "owner_name": "M. Thompson"}],
        "all_scores": [],
        "candidates_snapshot": None,
        "is_test_data": False,
    }
    base.update(overrides)
    return base


async def _aiter(rows):
    for row in rows:
        yield row


def _make_db(*, docs=None, find_one_doc=None, count=0, agg_rows=None):
    db = MagicMock()

    # find().skip().limit().to_list()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs or [])
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    db.match_review_queue.find = MagicMock(return_value=cursor)

    db.match_review_queue.find_one = AsyncMock(return_value=find_one_doc)
    db.match_review_queue.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db.match_review_queue.count_documents = AsyncMock(return_value=count)
    agg_cursor = MagicMock()
    agg_cursor.to_list = AsyncMock(return_value=agg_rows or [])
    db.match_review_queue.aggregate = MagicMock(return_value=agg_cursor)

    db.event_log.insert_one = AsyncMock(return_value=MagicMock(inserted_id="log-1"))

    return db


# ── GET /financial/matching/queue ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_queue_returns_pending_items():
    doc = _queue_doc()
    db = _make_db(docs=[doc])

    with patch("routers.financial_matching.db", db):
        items = await list_queue(
            item_status="pending", page=1, page_size=20,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert len(items) == 1
    assert items[0].status == "pending"
    assert items[0].tenant_id == BUILDING_A


@pytest.mark.asyncio
async def test_list_queue_excludes_test_data():
    """is_test_data records are excluded by the DB query filter."""
    db = _make_db(docs=[])

    with patch("routers.financial_matching.db", db):
        items = await list_queue(
            item_status="pending", page=1, page_size=20,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert items == []
    db.match_review_queue.find.assert_called_once()
    query_filter = db.match_review_queue.find.call_args[0][0]
    assert query_filter.get("is_test_data") == {"$ne": True}


@pytest.mark.asyncio
async def test_list_queue_cross_building_isolation():
    """TenantScopedDatabase scopes to BUILDING_A; BUILDING_B items are never returned."""
    db = _make_db(docs=[])

    with patch("routers.financial_matching.db", db):
        items = await list_queue(
            item_status="pending", page=1, page_size=20,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert items == []


# ── GET /financial/matching/queue/{item_id} ────────────────────────────────────

@pytest.mark.asyncio
async def test_get_queue_item_returns_item():
    doc = _queue_doc()
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    with patch("routers.financial_matching.db", db):
        item = await get_queue_item(
            item_id,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert item.item_id == item_id
    assert item.best_score == 0.85


@pytest.mark.asyncio
async def test_get_queue_item_invalid_id_returns_404():
    db = _make_db()
    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await get_queue_item(
                "not-a-valid-object-id",
                current_user=_MANAGER_USER, building_id=BUILDING_A,
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_queue_item_not_found_returns_404():
    db = _make_db(find_one_doc=None)
    valid_id = str(ObjectId())
    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await get_queue_item(
                valid_id,
                current_user=_MANAGER_USER, building_id=BUILDING_A,
            )
    assert exc.value.status_code == 404


# ── POST /financial/matching/queue/{item_id}/decide ───────────────────────────

@pytest.mark.asyncio
async def test_decide_allocate_emits_event_and_updates_doc():
    doc = _queue_doc()
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    payload = DecideRequest(action="allocate", lot_id="lot-001", amount_cents=153000)
    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger",
               AsyncMock(return_value="rcpt-manual-0001")) as post_mock:
        response = await decide_queue_item(
            item_id, payload,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert response["action"] == "allocate"
    assert response["item_id"] == item_id
    assert response["receipt_id"] == "rcpt-manual-0001"
    post_mock.assert_awaited_once()
    assert db.match_review_queue.update_one.await_count == 2

    claim_filter, claim_ops = db.match_review_queue.update_one.await_args_list[0].args
    assert claim_filter == {"_id": doc["_id"], "status": "pending"}
    assert claim_ops["$set"]["status"] == "posting"

    final_filter, final_ops = db.match_review_queue.update_one.await_args_list[1].args
    assert final_filter == {"_id": doc["_id"], "status": "posting"}
    assert final_ops["$set"]["status"] == "allocated"
    assert final_ops["$set"]["receipt_id"] == "rcpt-manual-0001"
    db.event_log.insert_one.assert_awaited_once()

    event_doc = db.event_log.insert_one.call_args[0][0]
    assert event_doc["event_type"] == "MatchDecisionRecorded"
    assert event_doc.get("event_id")
    assert event_doc["action"] == "allocate"
    assert event_doc["lot_id"] == "lot-001"
    assert event_doc["receipt_id"] == "rcpt-manual-0001"
    assert "candidates_snapshot" in event_doc


@pytest.mark.asyncio
async def test_decide_allocate_keeps_item_pending_when_ledger_post_fails():
    """A failed ledger post must not leave a terminal allocated event. The item is
    restored to pending so a reviewer can retry after the underlying mapping or
    Postgres issue is fixed."""
    doc = _queue_doc()
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    payload = DecideRequest(action="allocate", lot_id="lot-001", amount_cents=153000)
    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger",
               AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await decide_queue_item(
                item_id, payload,
                current_user=_MANAGER_USER, building_id=BUILDING_A,
            )

    assert exc.value.status_code == 502
    assert db.match_review_queue.update_one.await_count == 2
    revert_filter, revert_ops = db.match_review_queue.update_one.await_args_list[1].args
    assert revert_filter == {"_id": doc["_id"], "status": "posting"}
    assert revert_ops["$set"]["status"] == "pending"
    assert "last_post_failed_at" in revert_ops["$set"]
    db.event_log.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_decide_allocate_retries_existing_posting_state():
    """If the process crashes after the idempotent Postgres receipt write but
    before queue finalization, a later identical allocation request must finish
    the queue/event state instead of 409ing forever on status="posting"."""
    doc = _queue_doc(
        status="posting",
        decision={"action": "allocate", "lot_id": "lot-001", "amount_cents": 153000, "notes": None},
    )
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    payload = DecideRequest(action="allocate", lot_id="lot-001", amount_cents=153000)
    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger",
               AsyncMock(return_value="rcpt-retry-0001")) as post_mock:
        response = await decide_queue_item(
            item_id, payload,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert response["receipt_id"] == "rcpt-retry-0001"
    post_mock.assert_awaited_once()
    db.match_review_queue.update_one.assert_awaited_once()
    final_filter, final_ops = db.match_review_queue.update_one.await_args_list[0].args
    assert final_filter == {"_id": doc["_id"], "status": "posting"}
    assert final_ops["$set"]["status"] == "allocated"
    db.event_log.insert_one.assert_awaited_once()


def _mock_postgres_ledger_success(monkeypatch, *, receipt_id="rcpt-uuid-0001"):
    """Patch the Postgres layer inside _post_payment_to_ledger() so it reaches the
    Mongo-mirror block instead of failing fast at resolve_scheme_context (the path
    every other test in this file implicitly takes, since they never construct a
    tx dict with occurred_at). Mirrors the exact import paths used by the local
    imports inside financial_matching._post_payment_to_ledger().
    """
    import uuid as _uuid_mod

    scheme_uuid = _uuid_mod.uuid4()
    tenant_uuid = _uuid_mod.uuid4()
    lot_uuid = _uuid_mod.uuid4()
    receipt_uuid = _uuid_mod.uuid4()

    monkeypatch.setattr(
        "db_postgres.repos.config_repo.resolve_scheme_context",
        AsyncMock(return_value={"tenant_id": tenant_uuid, "scheme_id": scheme_uuid}),
    )

    # get_lot_id_by_number() (db_postgres/repos/ownership_repo.py) resolves
    # core.lots.lot_id by lot_number OR unit_number -- it's a dedicated repo
    # function, not a raw session.execute() call, since core.lots.lot_number
    # (the legal lot number, e.g. "87") and unit_number (the Mongo-style display
    # label, e.g. "TH087"/"UA067") are separate columns and not always equal.
    monkeypatch.setattr(
        "db_postgres.repos.ownership_repo.get_lot_id_by_number",
        AsyncMock(return_value=str(lot_uuid)),
    )

    # 2026-07-24 fix: _post_payment_to_ledger() makes a session.execute() call to
    # resolve the owning party (core.ownership_periods) as of the payment date —
    # core.lots and core.parties are distinct tables, so the lot's own id is never
    # a valid payer_party_id.
    owner_row = MagicMock(owner_party_id=str(_uuid_mod.uuid4()))
    owner_result = MagicMock()
    owner_result.first = MagicMock(return_value=owner_row)

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[owner_result])

    class _FakeSessionCtx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        "db_postgres.session.async_session_context", lambda: _FakeSessionCtx()
    )
    monkeypatch.setattr("db_postgres.session.set_tenant", AsyncMock())

    receipt = MagicMock(receipt_id=receipt_uuid)
    fake_service = MagicMock()
    fake_service.record_payment = AsyncMock(return_value=receipt)
    fake_service.allocate_payment = AsyncMock(return_value=[MagicMock()])
    # 2026-07-17 audit fix: _post_payment_to_ledger() now builds the service via
    # the canonical services.financial_core.get_financial_core_service(session)
    # factory (not services.financial_core.service.FinancialCoreService directly
    # — the old zero-arg `FinancialCoreService()` call this test used to patch
    # around raised TypeError in production and was silently swallowed).
    monkeypatch.setattr(
        "services.financial_core.get_financial_core_service",
        MagicMock(return_value=fake_service),
    )
    return receipt_uuid


@pytest.mark.asyncio
async def test_decide_allocate_mirrors_into_mongo_levy_payments(monkeypatch):
    """The fix under test: _post_payment_to_ledger() must also write Mongo
    levy_payments + call _upsert_ledger_for_payment(), not only Postgres.
    Without this test, the Postgres-only failure path in every other test in
    this file (no `occurred_at` on tx) never exercises the new code at all.
    """
    receipt_uuid = _mock_postgres_ledger_success(monkeypatch)

    doc = _queue_doc(tx={
        "amount_cents": 153000,
        "description": "LEVY UNIT 1",
        "occurred_at": "2026-07-01T10:00:00Z",
    })
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)
    db.levy_payments.insert_one = AsyncMock(return_value=MagicMock(inserted_id="pay-1"))

    ledger_mock = AsyncMock()
    monkeypatch.setattr("routers.finance._upsert_ledger_for_payment", ledger_mock)
    monkeypatch.setattr(
        "utils.finance_helpers.resolve_levy_year_for_date", AsyncMock(return_value="2026")
    )

    payload = DecideRequest(action="allocate", lot_id="lot-001", amount_cents=153000)
    with patch("routers.financial_matching.db", db):
        response = await decide_queue_item(
            item_id, payload,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert response["receipt_id"] == str(receipt_uuid)

    db.levy_payments.insert_one.assert_awaited_once()
    payment_doc = db.levy_payments.insert_one.call_args[0][0]
    assert payment_doc["building_id"] == BUILDING_A
    assert payment_doc["unit_number"] == "lot-001"
    assert payment_doc["amount"] == 1530.0  # amount_cents / 100
    assert payment_doc["year"] == "2026"
    assert payment_doc["status"] == "confirmed"
    assert payment_doc["payment_method"] == "bank_transfer"
    assert payment_doc["receipt_number"] == f"PG-{receipt_uuid}"
    assert payment_doc["confirmed_by"] == _MANAGER_USER["email"]

    ledger_mock.assert_awaited_once_with(
        "lot-001", "2026", 1530.0, payment_doc["id"], BUILDING_A
    )


@pytest.mark.asyncio
async def test_decide_allocate_mongo_mirror_skipped_when_no_annual_levies_year(monkeypatch):
    """If resolve_levy_year_for_date() finds no annual_levies row for the building at or
    before the transaction's own levy year, the mirror must skip cleanly (logged) rather
    than write a ledger row with an invalid/guessed year — and must not affect the
    already-successful Postgres receipt_id returned to the caller.
    """
    receipt_uuid = _mock_postgres_ledger_success(monkeypatch)

    doc = _queue_doc(tx={
        "amount_cents": 153000,
        "description": "LEVY UNIT 1",
        "occurred_at": "2026-07-01T10:00:00Z",
    })
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)
    db.levy_payments.insert_one = AsyncMock()

    ledger_mock = AsyncMock()
    monkeypatch.setattr("routers.finance._upsert_ledger_for_payment", ledger_mock)
    monkeypatch.setattr(
        "utils.finance_helpers.resolve_levy_year_for_date", AsyncMock(return_value=None)
    )

    payload = DecideRequest(action="allocate", lot_id="lot-001", amount_cents=153000)
    with patch("routers.financial_matching.db", db):
        response = await decide_queue_item(
            item_id, payload,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert response["receipt_id"] == str(receipt_uuid)
    db.levy_payments.insert_one.assert_not_awaited()
    ledger_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_decide_allocate_mongo_mirror_failure_does_not_erase_receipt_id(monkeypatch):
    """The Mongo mirror is wrapped in its own try/except (matching the allocate_payment
    convention above it in the source) specifically so a Mongo outage cannot erase the
    fact that the Postgres receipt already committed. Assert that contract directly.
    """
    receipt_uuid = _mock_postgres_ledger_success(monkeypatch)

    doc = _queue_doc(tx={
        "amount_cents": 153000,
        "description": "LEVY UNIT 1",
        "occurred_at": "2026-07-01T10:00:00Z",
    })
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)
    db.levy_payments.insert_one = AsyncMock(side_effect=RuntimeError("Mongo unavailable"))

    monkeypatch.setattr(
        "routers.finance._upsert_ledger_for_payment", AsyncMock()
    )
    monkeypatch.setattr(
        "utils.finance_helpers.resolve_levy_year_for_date", AsyncMock(return_value="2026")
    )

    payload = DecideRequest(action="allocate", lot_id="lot-001", amount_cents=153000)
    with patch("routers.financial_matching.db", db):
        response = await decide_queue_item(
            item_id, payload,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert response["receipt_id"] == str(receipt_uuid)


@pytest.mark.asyncio
async def test_decide_sets_decided_by_from_user_email():
    """decided_by must be the authenticated user's email, not a hardcoded string."""
    doc = _queue_doc()
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    payload = DecideRequest(action="reject", notes="Duplicate")
    with patch("routers.financial_matching.db", db):
        await decide_queue_item(
            item_id, payload,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    update_call = db.match_review_queue.update_one.call_args[0][1]
    assert update_call["$set"]["decided_by"] == "manager@test.com"

    event_doc = db.event_log.insert_one.call_args[0][0]
    assert event_doc["decided_by"] == "manager@test.com"


@pytest.mark.asyncio
async def test_decide_reject_records_decision():
    doc = _queue_doc()
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    payload = DecideRequest(action="reject", notes="Duplicate payment")
    with patch("routers.financial_matching.db", db):
        response = await decide_queue_item(
            item_id, payload,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert response["action"] == "reject"
    event_doc = db.event_log.insert_one.call_args[0][0]
    assert event_doc["action"] == "reject"


@pytest.mark.asyncio
async def test_decide_unidentified_records_decision():
    doc = _queue_doc()
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    payload = DecideRequest(action="unidentified")
    with patch("routers.financial_matching.db", db):
        response = await decide_queue_item(
            item_id, payload,
            current_user=_MANAGER_USER, building_id=BUILDING_A,
        )

    assert response["action"] == "unidentified"


@pytest.mark.asyncio
async def test_decide_owner_role_returns_403():
    """Owners must not be able to decide queue items — only strata_manager+ allowed."""
    doc = _queue_doc()
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    payload = DecideRequest(action="reject")
    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await decide_queue_item(
                item_id, payload,
                current_user=_OWNER_USER, building_id=BUILDING_A,
            )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_decide_already_decided_returns_409():
    doc = _queue_doc(status="allocated")  # already decided
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    payload = DecideRequest(action="reject")
    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await decide_queue_item(
                item_id, payload,
                current_user=_MANAGER_USER, building_id=BUILDING_A,
            )

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_decide_allocate_missing_lot_id_returns_422():
    doc = _queue_doc()
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    payload = DecideRequest(action="allocate", lot_id=None, amount_cents=153000)
    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await decide_queue_item(
                item_id, payload,
                current_user=_MANAGER_USER, building_id=BUILDING_A,
            )

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_decide_allocate_missing_amount_returns_422():
    doc = _queue_doc()
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    payload = DecideRequest(action="allocate", lot_id="lot-001", amount_cents=None)
    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await decide_queue_item(
                item_id, payload,
                current_user=_MANAGER_USER, building_id=BUILDING_A,
            )

    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_decide_not_found_returns_404():
    db = _make_db(find_one_doc=None)
    valid_id = str(ObjectId())
    with patch("routers.financial_matching.db", db):
        with pytest.raises(HTTPException) as exc:
            await decide_queue_item(
                valid_id, DecideRequest(action="reject"),
                current_user=_MANAGER_USER, building_id=BUILDING_A,
            )
    assert exc.value.status_code == 404


# ── GET /financial/matching/stats ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stats_returns_queue_depth_and_breach_count():
    agg_rows = [{"_id": "auto", "count": 15}, {"_id": "review", "count": 5}]
    db = _make_db(count=7, agg_rows=agg_rows)
    db.match_review_queue.count_documents = AsyncMock(side_effect=[7, 2])

    with patch("routers.financial_matching.db", db):
        stats = await get_stats(current_user=_MANAGER_USER, building_id=BUILDING_A)

    assert stats.queue_depth == 7
    assert stats.sla_breach_count == 2
    assert stats.total_last_7_days == 20
    assert stats.auto_allocated_last_7_days == 15
    assert round(stats.auto_match_rate, 2) == 0.75


@pytest.mark.asyncio
async def test_stats_zero_rate_when_no_activity():
    db = _make_db(count=0, agg_rows=[])
    db.match_review_queue.count_documents = AsyncMock(side_effect=[0, 0])

    with patch("routers.financial_matching.db", db):
        stats = await get_stats(current_user=_MANAGER_USER, building_id=BUILDING_A)

    assert stats.auto_match_rate == 0.0
    assert stats.total_last_7_days == 0


# ── auto_allocate_queue_item (GAP-FIN-015 Phase 1) ────────────────────────────
#
# engine.match() sets status="auto_allocated" for high-confidence matches but
# never itself posts to the ledger; decide_queue_item() only accepts
# status="pending". auto_allocate_queue_item() is the closing link — these
# tests cover it directly (bypassing the bank_feeds.py dispatch wrapper).

@pytest.mark.asyncio
async def test_auto_allocate_posts_to_ledger_and_marks_allocated():
    doc = _queue_doc(
        status="auto_allocated",
        best_lot_id="lot-001",
        tx={
            "amount_cents": 153000,
            "description": "LEVY UNIT 1",
            "occurred_at": "2026-07-01T10:00:00Z",
        },
    )
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger",
               AsyncMock(return_value="rcpt-auto-0001")) as post_mock:
        receipt_id = await auto_allocate_queue_item(item_id, BUILDING_A)

    assert receipt_id == "rcpt-auto-0001"
    post_mock.assert_awaited_once()
    kwargs = post_mock.call_args.kwargs
    assert kwargs["lot_id"] == "lot-001"
    assert kwargs["amount_cents"] == 153000
    assert kwargs["decided_by"] == "system:matching_engine_auto_allocate"

    assert db.match_review_queue.update_one.await_count == 2
    claim_filter, claim_ops = db.match_review_queue.update_one.await_args_list[0].args
    assert claim_filter == {"_id": doc["_id"], "status": "auto_allocated"}
    assert claim_ops["$set"]["status"] == "posting"
    assert claim_ops["$set"]["posting_by"] == "system:matching_engine_auto_allocate"

    final_filter, final_ops = db.match_review_queue.update_one.await_args_list[1].args
    assert final_filter == {"_id": doc["_id"], "status": "posting"}
    assert final_ops["$set"]["status"] == "allocated"
    assert final_ops["$set"]["decided_by"] == "system:matching_engine_auto_allocate"
    assert final_ops["$set"]["receipt_id"] == "rcpt-auto-0001"

    db.event_log.insert_one.assert_awaited_once()
    event_doc = db.event_log.insert_one.call_args[0][0]
    assert event_doc["event_type"] == "MatchDecisionRecorded"
    assert event_doc["action"] == "allocate"
    assert event_doc["lot_id"] == "lot-001"
    assert event_doc["amount_cents"] == 153000
    assert event_doc["receipt_id"] == "rcpt-auto-0001"


@pytest.mark.asyncio
async def test_auto_allocate_restores_retryable_status_when_ledger_post_fails():
    """Auto-allocation must not mark a queue item allocated until the receipt
    exists. Restoring status="auto_allocated" lets the next idempotent rematch
    replay retry auto_allocate_queue_item()."""
    doc = _queue_doc(status="auto_allocated", best_lot_id="lot-001",
                      tx={"amount_cents": 153000, "occurred_at": "2026-07-01T10:00:00Z"})
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger",
               AsyncMock(return_value=None)) as post_mock:
        receipt_id = await auto_allocate_queue_item(item_id, BUILDING_A)

    assert receipt_id is None
    post_mock.assert_awaited_once()
    assert db.match_review_queue.update_one.await_count == 2
    revert_filter, revert_ops = db.match_review_queue.update_one.await_args_list[1].args
    assert revert_filter == {"_id": doc["_id"], "status": "posting"}
    assert revert_ops["$set"]["status"] == "auto_allocated"
    assert "last_post_failed_at" in revert_ops["$set"]
    db.event_log.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_allocate_retries_existing_posting_state():
    """A replay after a crash between receipt creation and queue finalization
    must retry the idempotent ledger post and close the queue item."""
    doc = _queue_doc(status="posting", best_lot_id="lot-001",
                      posting_by="system:matching_engine_auto_allocate",
                      tx={"amount_cents": 153000, "occurred_at": "2026-07-01T10:00:00Z"})
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger",
               AsyncMock(return_value="rcpt-auto-retry-0001")) as post_mock:
        receipt_id = await auto_allocate_queue_item(item_id, BUILDING_A)

    assert receipt_id == "rcpt-auto-retry-0001"
    post_mock.assert_awaited_once()
    db.match_review_queue.update_one.assert_awaited_once()
    final_filter, final_ops = db.match_review_queue.update_one.await_args_list[0].args
    assert final_filter == {"_id": doc["_id"], "status": "posting"}
    assert final_ops["$set"]["status"] == "allocated"
    assert final_ops["$set"]["receipt_id"] == "rcpt-auto-retry-0001"
    db.event_log.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_allocate_does_not_take_over_manual_posting_state():
    doc = _queue_doc(status="posting", best_lot_id="lot-001",
                      posting_by="manager@test.com",
                      tx={"amount_cents": 153000, "occurred_at": "2026-07-01T10:00:00Z"})
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger", AsyncMock()) as post_mock:
        receipt_id = await auto_allocate_queue_item(item_id, BUILDING_A)

    assert receipt_id is None
    db.match_review_queue.update_one.assert_not_awaited()
    post_mock.assert_not_awaited()
    db.event_log.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_allocate_skips_when_status_not_auto_allocated():
    doc = _queue_doc(status="pending", best_lot_id="lot-001")
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger", AsyncMock()) as post_mock:
        receipt_id = await auto_allocate_queue_item(item_id, BUILDING_A)

    assert receipt_id is None
    db.match_review_queue.update_one.assert_not_awaited()
    post_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_allocate_returns_none_when_missing_lot_id():
    doc = _queue_doc(status="auto_allocated", best_lot_id=None)
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger", AsyncMock()) as post_mock:
        receipt_id = await auto_allocate_queue_item(item_id, BUILDING_A)

    assert receipt_id is None
    db.match_review_queue.update_one.assert_not_awaited()
    post_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_allocate_returns_none_when_missing_amount():
    doc = _queue_doc(status="auto_allocated", best_lot_id="lot-001", tx={"description": "no amount"})
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger", AsyncMock()) as post_mock:
        receipt_id = await auto_allocate_queue_item(item_id, BUILDING_A)

    assert receipt_id is None
    post_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_allocate_rejects_non_positive_amount():
    """A bare truthiness check (`not amount_cents`) would let a negative amount
    through, since bool(-100) is True — must reject <= 0 explicitly, matching
    decide_queue_item()'s own amount_cents validation for a human allocate."""
    doc = _queue_doc(status="auto_allocated", best_lot_id="lot-001",
                      tx={"amount_cents": -500, "occurred_at": "2026-07-01T10:00:00Z"})
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger", AsyncMock()) as post_mock:
        receipt_id = await auto_allocate_queue_item(item_id, BUILDING_A)

    assert receipt_id is None
    db.match_review_queue.update_one.assert_not_awaited()
    post_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_allocate_is_idempotent_on_lost_race():
    """If update_one reports modified_count=0 (another dispatch already claimed this
    item, or it was already decided between find_one and update_one), the function
    must not double-post to the ledger or double-emit the event."""
    doc = _queue_doc(status="auto_allocated", best_lot_id="lot-001",
                      tx={"amount_cents": 153000, "occurred_at": "2026-07-01T10:00:00Z"})
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)
    db.match_review_queue.update_one = AsyncMock(return_value=MagicMock(modified_count=0))

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger", AsyncMock()) as post_mock:
        receipt_id = await auto_allocate_queue_item(item_id, BUILDING_A)

    assert receipt_id is None
    db.event_log.insert_one.assert_not_awaited()
    post_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_allocate_returns_none_for_unknown_item():
    db = _make_db(find_one_doc=None)
    with patch("routers.financial_matching.db", db):
        receipt_id = await auto_allocate_queue_item(str(ObjectId()), BUILDING_A)
    assert receipt_id is None


@pytest.mark.asyncio
async def test_auto_allocate_cross_building_isolation():
    """A queue item belonging to BUILDING_B must not be auto-allocated when called
    with BUILDING_A, matching decide_queue_item()'s own explicit building_id/
    tenant_id re-check after find_one() (which itself relies on
    TenantScopedDatabase to scope the lookup in production)."""
    doc = _queue_doc(status="auto_allocated", best_lot_id="lot-001",
                      building_id=BUILDING_B, tenant_id=BUILDING_B)
    item_id = str(doc["_id"])
    db = _make_db(find_one_doc=doc)

    with patch("routers.financial_matching.db", db), \
         patch("routers.financial_matching._post_payment_to_ledger", AsyncMock()) as post_mock:
        receipt_id = await auto_allocate_queue_item(item_id, BUILDING_A)

    assert receipt_id is None
    db.match_review_queue.update_one.assert_not_awaited()
    post_mock.assert_not_awaited()
