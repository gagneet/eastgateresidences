"""
tests/backend/routers/test_admin_diagnostics.py

GET /admin/demo-bank/transactions — super-admin-only raw Demo Bank transaction
viewer added for East Gate 13195's historical reconstruction (2026-07-22), moved
out of routers/bank_feeds.py into its own router (GAP-ADMIN-001, 2026-07-23) so it
is not gated behind bank_feeds.py's router-level financial_integration_layer_v2
feature-toggle dependency. Unlike GET /bank-feeds/transactions (Postgres-synced
finance.bank_transactions mirror, open to _require_finance's broader role set),
this endpoint reads demo_bank_transactions directly and is gated to super_admin
only via _require_super_admin_only, mirroring
routers.trial_request._require_super_admin.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

_backend = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "backend",
)
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from routers.admin_diagnostics import _require_super_admin_only, list_demo_bank_transactions_admin

BUILDING_A = "16244"
BUILDING_B = "18888"


def _doc(building_id: str, **overrides) -> dict:
    base = dict(
        _id="oid-1",
        building_id=building_id,
        account_ref=f"ADMIN-{building_id}",
        unit_number="1",
        posted_date=datetime(2023, 3, 1, tzinfo=timezone.utc),
        amount_cents=113615,
        direction="credit",
        description="Combined levy payment",
        source_type="historical_reconstruction",
        transaction_origin="reconstruction",
        reconstruction_batch_id="batch-1",
        status="posted",
        confidence="high",
        assumption_code="quarterly_regular",
        allocations=[
            {"fund_type": "admin", "amount_cents": 89080},
            {"fund_type": "sinking", "amount_cents": 24535},
        ],
    )
    base.update(overrides)
    return base


def _mock_db(docs: list[dict], total: int) -> MagicMock:
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=docs)
    db.demo_bank_transactions.find = MagicMock(return_value=cursor)
    db.demo_bank_transactions.count_documents = AsyncMock(return_value=total)
    return db, cursor


class TestRequireSuperAdminOnly:
    def test_super_admin_passes(self):
        _require_super_admin_only({"role": "super_admin"})  # no exception

    def test_effective_role_super_admin_passes(self):
        _require_super_admin_only({"role": "owner", "effective_role": "super_admin"})

    @pytest.mark.parametrize(
        "role", ["strata_admin", "strata_manager", "ec_member", "owner", "tenant", "guest"]
    )
    def test_every_other_role_rejected(self, role):
        with pytest.raises(HTTPException) as exc:
            _require_super_admin_only({"role": role})
        assert exc.value.status_code == 403


class TestListDemoBankTransactionsAdmin:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "role", ["strata_admin", "strata_manager", "ec_member", "owner"]
    )
    async def test_non_super_admin_rejected(self, role):
        db, _ = _mock_db([], 0)
        with patch("routers.admin_diagnostics._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await list_demo_bank_transactions_admin(
                    source_type=None,
                    reconstruction_batch_id=None,
                    page=1,
                    limit=50,
                    current_user={"role": role},
                    building_id=BUILDING_A,
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_super_admin_gets_data_and_pagination_shape(self):
        docs = [_doc(BUILDING_A)]
        db, cursor = _mock_db(docs, total=1)
        with patch("routers.admin_diagnostics._get_db", return_value=db):
            result = await list_demo_bank_transactions_admin(
                source_type=None,
                reconstruction_batch_id=None,
                page=1,
                limit=50,
                current_user={"role": "super_admin"},
                building_id=BUILDING_A,
            )

        assert result["pagination"] == {"page": 1, "limit": 50, "total": 1, "total_pages": 1}
        assert len(result["data"]) == 1
        row = result["data"][0]
        assert row["id"] == "oid-1"
        assert row["account_ref"] == f"ADMIN-{BUILDING_A}"
        assert row["allocations"] == [
            {"fund_type": "admin", "amount_cents": 89080},
            {"fund_type": "sinking", "amount_cents": 24535},
        ]

    @pytest.mark.asyncio
    async def test_source_type_filter_applied_to_query(self):
        db, cursor = _mock_db([], total=0)
        with patch("routers.admin_diagnostics._get_db", return_value=db):
            await list_demo_bank_transactions_admin(
                source_type="historical_reconstruction",
                reconstruction_batch_id=None,
                page=1,
                limit=50,
                current_user={"role": "super_admin"},
                building_id=BUILDING_A,
            )

        query = db.demo_bank_transactions.find.call_args[0][0]
        assert query["source_type"] == "historical_reconstruction"
        assert query["building_id"] == BUILDING_A

    @pytest.mark.asyncio
    async def test_reconstruction_batch_id_filter_applied_to_query(self):
        db, cursor = _mock_db([], total=0)
        with patch("routers.admin_diagnostics._get_db", return_value=db):
            await list_demo_bank_transactions_admin(
                source_type=None,
                reconstruction_batch_id="batch-42",
                page=1,
                limit=50,
                current_user={"role": "super_admin"},
                building_id=BUILDING_A,
            )

        query = db.demo_bank_transactions.find.call_args[0][0]
        assert query["reconstruction_batch_id"] == "batch-42"

    @pytest.mark.asyncio
    async def test_query_scoped_to_requesting_building_only(self):
        """Multi-tenant isolation: building A's query must never leak building B's
        filter, and vice versa — building_id is always the sole tenant key."""
        db_a, _ = _mock_db([_doc(BUILDING_A)], total=1)
        with patch("routers.admin_diagnostics._get_db", return_value=db_a):
            await list_demo_bank_transactions_admin(
                source_type=None, reconstruction_batch_id=None, page=1, limit=50,
                current_user={"role": "super_admin"}, building_id=BUILDING_A,
            )
        query_a = db_a.demo_bank_transactions.find.call_args[0][0]
        assert query_a["building_id"] == BUILDING_A
        assert query_a["building_id"] != BUILDING_B

        db_b, _ = _mock_db([_doc(BUILDING_B)], total=1)
        with patch("routers.admin_diagnostics._get_db", return_value=db_b):
            await list_demo_bank_transactions_admin(
                source_type=None, reconstruction_batch_id=None, page=1, limit=50,
                current_user={"role": "super_admin"}, building_id=BUILDING_B,
            )
        query_b = db_b.demo_bank_transactions.find.call_args[0][0]
        assert query_b["building_id"] == BUILDING_B

    @pytest.mark.asyncio
    async def test_pagination_math_at_page_boundary(self):
        db, cursor = _mock_db([], total=101)
        with patch("routers.admin_diagnostics._get_db", return_value=db):
            result = await list_demo_bank_transactions_admin(
                source_type=None, reconstruction_batch_id=None, page=3, limit=50,
                current_user={"role": "super_admin"}, building_id=BUILDING_A,
            )

        # 101 rows at limit=50 -> 3 pages (50 + 50 + 1), skip for page 3 = 100.
        assert result["pagination"]["total_pages"] == 3
        cursor.skip.assert_called_once_with(100)
        cursor.limit.assert_called_once_with(50)
