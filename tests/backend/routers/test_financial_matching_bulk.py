"""
Bulk match-review-queue decision tests — POST /financial/matching/queue/bulk-decide.

# @featuretrace:financial_matching — tests for the bounded bulk reject|unidentified endpoint.
# Layer: test
# Data flow: bulk_decide_queue_items() → match_review_queue.update_one + event_log.insert_many (building-scoped).
# Related: backend/routers/financial_matching.py
#           docs/runbooks/east_gate_match_queue_backlog_clearance_2026_07_03.md

Covers: RBAC (owner 403, elevated ec_member allowed), dry-run default makes no writes,
direction filter shape, race-guarded per-item update, MatchDecisionRecorded events with
shared bulk_operation_id, lot-code extraction annotation, request-model bounds, and
multi-tenant isolation (scoped db object is the only data path).

Run:
    backend/venv/bin/python3 -m pytest tests/backend/routers/test_financial_matching_bulk.py -q
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

from routers.financial_matching import (  # noqa: E402
    BulkDecideRequest,
    _extract_known_lot_code,
    bulk_decide_queue_items,
)
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

BUILDING = "13195"
OTHER_BUILDING = "16244"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _queue_doc(amount_cents: int, description: str, building_id: str = BUILDING) -> dict:
    return {
        "_id": ObjectId(),
        "tenant_id": building_id,
        "building_id": building_id,
        "inbox_event_id": f"evt-{ObjectId()}",
        "status": "pending",
        "match_type": "unidentified",
        "tx": {
            "amount_cents": amount_cents,
            "description": description,
            "account_ref": f"ADMIN-{building_id}",
        },
        "is_test_data": True,
    }


def _mock_db(docs: list[dict], units: list[dict]):
    """MagicMock stand-in for the TenantScopedDatabase module-level `db`."""
    db = MagicMock()
    db.match_review_queue.count_documents = AsyncMock(return_value=len(docs))

    cursor = MagicMock()
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=docs)
    db.match_review_queue.find.return_value = cursor

    units_cursor = MagicMock()
    units_cursor.to_list = AsyncMock(return_value=units)
    db.units.find.return_value = units_cursor

    db.match_review_queue.update_one = AsyncMock(
        return_value=MagicMock(modified_count=1)
    )
    db.event_log.insert_many = AsyncMock()
    return db


def _manager(email="manager@test.com", role="strata_manager", effective=None):
    user = {"email": email, "role": role}
    if effective:
        user["effective_role"] = effective
    return user


async def _call(db, payload: BulkDecideRequest, user: dict, building_id: str = BUILDING):
    with patch("routers.financial_matching.db", db):
        return await bulk_decide_queue_items(
            payload=payload, current_user=user, building_id=building_id
        )


# ── Request-model bounds ──────────────────────────────────────────────────────

class TestBulkDecideRequestModel:
    def test_allocate_action_is_rejected(self):
        # Bulk allocate is deliberately unsupported — it would post to the ledger.
        with pytest.raises(ValidationError):
            BulkDecideRequest(action="allocate", notes="clearing the backlog rows")

    def test_notes_required_and_min_length(self):
        with pytest.raises(ValidationError):
            BulkDecideRequest(action="reject", notes="short")

    def test_max_items_upper_bound(self):
        with pytest.raises(ValidationError):
            BulkDecideRequest(action="reject", notes="clearing the backlog rows",
                              max_items=1001)

    def test_dry_run_defaults_true(self):
        req = BulkDecideRequest(action="reject", notes="clearing the backlog rows")
        assert req.dry_run is True


# ── Lot-code extraction ───────────────────────────────────────────────────────

class TestLotCodeExtraction:
    def test_lot_prefix(self):
        assert _extract_known_lot_code(
            "DEFT/BPAY payment received — reconciled - LOT TH078", {"TH078"}
        ) == "TH078"

    def test_unit_prefix_case_insensitive(self):
        assert _extract_known_lot_code(
            "Levy Q1 2021 Admin Fund — unit ua070 (82.0 UOE)", {"UA070"}
        ) == "UA070"

    def test_unknown_code_returns_none(self):
        assert _extract_known_lot_code("LOT ZZ999 mystery", {"TH078"}) is None

    def test_date_fragment_not_extracted(self):
        # "12/02/2025 ... Level Plumbing" must never be read as unit 12.
        assert _extract_known_lot_code(
            "12/02/2025 249215 Attend site to inspect water ingress", {"12"}
        ) is None

    def test_no_description(self):
        assert _extract_known_lot_code("", {"TH078"}) is None


# ── RBAC ──────────────────────────────────────────────────────────────────────

class TestBulkDecideRBAC:
    @pytest.mark.asyncio
    async def test_owner_forbidden(self):
        db = _mock_db([], [])
        with pytest.raises(HTTPException) as exc:
            await _call(db, BulkDecideRequest(action="reject", notes="bulk close outflows"),
                        _manager(role="owner"))
        assert exc.value.status_code == 403
        db.match_review_queue.count_documents.assert_not_called()

    @pytest.mark.asyncio
    async def test_tenant_forbidden(self):
        db = _mock_db([], [])
        with pytest.raises(HTTPException) as exc:
            await _call(db, BulkDecideRequest(action="reject", notes="bulk close outflows"),
                        _manager(role="tenant"))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_elevated_owner_with_ec_member_effective_role_allowed(self):
        # Elevation sets effective_role while raw role stays "owner".
        db = _mock_db([], [])
        result = await _call(
            db, BulkDecideRequest(action="reject", notes="bulk close outflows"),
            _manager(role="owner", effective="ec_member"),
        )
        assert result.dry_run is True

    @pytest.mark.asyncio
    async def test_strata_manager_allowed(self):
        db = _mock_db([], [])
        result = await _call(
            db, BulkDecideRequest(action="reject", notes="bulk close outflows"),
            _manager(role="strata_manager"),
        )
        assert result.matched_total == 0


# ── Dry-run behaviour ─────────────────────────────────────────────────────────

class TestBulkDecideDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_makes_no_writes(self):
        docs = [_queue_doc(-43175, "12/02/2025 plumbing invoice"),
                _queue_doc(157356, "payment - LOT TH078")]
        db = _mock_db(docs, [{"unit_number": "TH078"}])
        result = await _call(
            db, BulkDecideRequest(action="reject", notes="bulk close outflow expenses"),
            _manager(),
        )
        assert result.dry_run is True
        assert result.decided == 0
        assert result.selected == 2
        assert result.matched_total == 2
        assert result.identified_lot_count == 1
        assert result.bulk_operation_id is None
        db.match_review_queue.update_one.assert_not_called()
        db.event_log.insert_many.assert_not_called()

    @pytest.mark.asyncio
    async def test_sample_excludes_internal_doc(self):
        docs = [_queue_doc(-100, "expense row one for filters")]
        db = _mock_db(docs, [])
        result = await _call(
            db, BulkDecideRequest(action="reject", notes="bulk close outflow expenses"),
            _manager(),
        )
        assert len(result.sample) == 1
        assert "_doc" not in result.sample[0]
        assert result.sample[0]["amount_cents"] == -100


# ── Filter construction ───────────────────────────────────────────────────────

class TestBulkDecideFilters:
    @pytest.mark.asyncio
    async def test_outflow_filter_shape(self):
        db = _mock_db([], [])
        await _call(
            db,
            BulkDecideRequest(action="reject", notes="bulk close outflow expenses",
                              direction="outflow", match_type="unidentified"),
            _manager(),
        )
        query = db.match_review_queue.count_documents.call_args[0][0]
        assert query["status"] == "pending"
        assert query["is_test_data"] == {"$ne": True}
        assert query["match_type"] == "unidentified"
        assert query["tx.amount_cents"] == {"$lt": 0}
        # No hardcoded building filter — TenantScopedDatabase injects it.
        assert "building_id" not in query

    @pytest.mark.asyncio
    async def test_inflow_filter_shape(self):
        db = _mock_db([], [])
        await _call(
            db,
            BulkDecideRequest(action="unidentified", notes="historical rows already booked",
                              direction="inflow"),
            _manager(),
        )
        query = db.match_review_queue.count_documents.call_args[0][0]
        assert query["tx.amount_cents"] == {"$gt": 0}
        assert "match_type" not in query


# ── Real-run writes ───────────────────────────────────────────────────────────

class TestBulkDecideRealRun:
    @pytest.mark.asyncio
    async def test_updates_are_race_guarded_and_events_written(self):
        docs = [_queue_doc(-43175, "plumbing invoice payment"),
                _queue_doc(-9900, "gardening invoice payment")]
        db = _mock_db(docs, [])
        result = await _call(
            db,
            BulkDecideRequest(action="reject", notes="bulk close outflow expenses",
                              direction="outflow", dry_run=False),
            _manager(email="mgr@test.com"),
        )
        assert result.dry_run is False
        assert result.decided == 2
        assert result.bulk_operation_id

        assert db.match_review_queue.update_one.call_count == 2
        for call, doc in zip(db.match_review_queue.update_one.call_args_list, docs):
            filt, update = call[0]
            assert filt == {"_id": doc["_id"], "status": "pending"}
            assert update["$set"]["status"] == "rejected"
            assert update["$set"]["decided_by"] == "mgr@test.com"
            assert update["$set"]["decision"]["bulk_operation_id"] == result.bulk_operation_id
            assert update["$set"]["decision"]["notes"] == "bulk close outflow expenses"

        events = db.event_log.insert_many.call_args[0][0]
        assert len(events) == 2
        assert all(e["event_type"] == "MatchDecisionRecorded" for e in events)
        assert all(e.get("event_id") for e in events)
        assert len({e["event_id"] for e in events}) == len(events)
        assert all(e["bulk_operation_id"] == result.bulk_operation_id for e in events)
        assert all(e["building_id"] == BUILDING for e in events)

    @pytest.mark.asyncio
    async def test_concurrently_decided_item_skipped(self):
        docs = [_queue_doc(-100, "expense one already raced"),
                _queue_doc(-200, "expense two still pending")]
        db = _mock_db(docs, [])
        db.match_review_queue.update_one = AsyncMock(
            side_effect=[MagicMock(modified_count=0), MagicMock(modified_count=1)]
        )
        result = await _call(
            db,
            BulkDecideRequest(action="reject", notes="bulk close outflow expenses",
                              dry_run=False),
            _manager(),
        )
        assert result.selected == 2
        assert result.decided == 1
        events = db.event_log.insert_many.call_args[0][0]
        assert len(events) == 1
        assert events[0]["queue_item_id"] == str(docs[1]["_id"])

    @pytest.mark.asyncio
    async def test_no_events_insert_when_nothing_decided(self):
        db = _mock_db([], [])
        result = await _call(
            db,
            BulkDecideRequest(action="unidentified", notes="nothing matches this filter",
                              dry_run=False),
            _manager(),
        )
        assert result.decided == 0
        db.event_log.insert_many.assert_not_called()

    @pytest.mark.asyncio
    async def test_unidentified_action_maps_to_unidentified_status(self):
        docs = [_queue_doc(5000, "payment - LOT TH078")]
        db = _mock_db(docs, [{"unit_number": "TH078"}])
        result = await _call(
            db,
            BulkDecideRequest(action="unidentified",
                              notes="historical inflow already in closing balances",
                              dry_run=False),
            _manager(),
        )
        update = db.match_review_queue.update_one.call_args[0][1]
        assert update["$set"]["status"] == "unidentified"
        assert update["$set"]["decision"]["extracted_lot_code"] == "TH078"
        assert result.identified_lot_count == 1


# ── Multi-tenant isolation ────────────────────────────────────────────────────

class TestBulkDecideTenantIsolation:
    @pytest.mark.asyncio
    async def test_other_building_docs_never_reach_update_when_scoped_db_filters(self):
        """The endpoint's only data path is the module-level TenantScopedDatabase.

        Simulate the scoped db returning only building-13195 docs (as the wrapper
        guarantees) and assert nothing addressed to 16244 is written.
        """
        docs = [_queue_doc(-100, "expense", building_id=BUILDING)]
        db = _mock_db(docs, [])
        await _call(
            db,
            BulkDecideRequest(action="reject", notes="bulk close outflow expenses",
                              dry_run=False),
            _manager(), building_id=BUILDING,
        )
        events = db.event_log.insert_many.call_args[0][0]
        assert all(e["building_id"] == BUILDING for e in events)
        assert all(e["building_id"] != OTHER_BUILDING for e in events)
