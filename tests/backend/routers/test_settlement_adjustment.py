"""
Tests for settlement_adjustment router — pro-rata levy split at conveyancing.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from routers.settlement_adjustment import (
    get_settlement_adjustment,
    get_lot_ownership_history,
)
from server import app, get_current_user, get_current_building
import routers.settlement_adjustment as settlement_module


def _dt(year, month, day):
    return datetime(year, month, day, tzinfo=timezone.utc)


def _make_user(role="strata_manager"):
    return {"role": role, "effective_role": role, "building_id": "13195"}


def _make_db(owner_record=None, period=None, history=None):
    mock_db = MagicMock()
    mock_db.ownership_periods.find_one = AsyncMock(return_value=owner_record)
    mock_db.ownership_periods.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
    ins = MagicMock()
    ins.inserted_id = "new_id"
    mock_db.ownership_periods.insert_one = AsyncMock(return_value=ins)
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=history or [])
    mock_db.ownership_periods.find = MagicMock(return_value=cursor)
    mock_db.financial_periods.find_one = AsyncMock(return_value=period)
    return mock_db


class TestGetSettlementAdjustment:
    @pytest.mark.asyncio
    async def test_returns_pro_rata_split(self):
        owner = {
            "building_id": "13195", "lot_id": "5",
            "owner_name": "John Doe", "effective_from": _dt(2023, 1, 1),
            "effective_to": None, "is_test_data": True,
        }
        period = {
            "building_id": "13195", "state": "open",
            "period_start": _dt(2025, 4, 1), "period_end": _dt(2025, 6, 30),
            "levy_cents": 153000,
        }
        mock_db = _make_db(owner_record=owner, period=period)
        with patch("routers.settlement_adjustment.db", mock_db):
            result = await get_settlement_adjustment(
                lot_id="5", building_id="13195", date="2025-05-01",
                current_user=_make_user("strata_manager"),
            )
        assert result["lot_id"] == "5"
        assert result["building_id"] == "13195"
        assert result["outgoing_owner"] == "John Doe"
        assert result["outgoing_levy_cents"] + result["incoming_levy_cents"] == 153000

    @pytest.mark.asyncio
    async def test_pro_rata_amounts_are_integers(self):
        owner = {"building_id": "13195", "lot_id": "5", "owner_name": "A", "is_test_data": True}
        period = {
            "building_id": "13195", "state": "open",
            "period_start": _dt(2025, 4, 1), "period_end": _dt(2025, 6, 30),
            "levy_cents": 90000,
        }
        mock_db = _make_db(owner_record=owner, period=period)
        with patch("routers.settlement_adjustment.db", mock_db):
            result = await get_settlement_adjustment(
                lot_id="5", building_id="13195", date="2025-05-01",
                current_user=_make_user(),
            )
        assert isinstance(result["outgoing_levy_cents"], int)
        assert isinstance(result["incoming_levy_cents"], int)

    @pytest.mark.asyncio
    async def test_returns_404_when_no_owner(self):
        from fastapi import HTTPException
        mock_db = _make_db(owner_record=None)
        with patch("routers.settlement_adjustment.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await get_settlement_adjustment(
                    lot_id="99", building_id="13195", date="2025-05-01",
                    current_user=_make_user(),
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_404_for_future_date(self):
        from fastapi import HTTPException
        mock_db = _make_db()
        with patch("routers.settlement_adjustment.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await get_settlement_adjustment(
                    lot_id="1", building_id="13195", date="2099-01-01",
                    current_user=_make_user(),
                )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_422_for_bad_date_format(self):
        from fastapi import HTTPException
        mock_db = _make_db()
        with patch("routers.settlement_adjustment.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await get_settlement_adjustment(
                    lot_id="1", building_id="13195", date="not-a-date",
                    current_user=_make_user(),
                )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_403_for_owner_role(self):
        from fastapi import HTTPException
        mock_db = _make_db()
        with patch("routers.settlement_adjustment.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await get_settlement_adjustment(
                    lot_id="1", building_id="13195", date="2025-05-01",
                    current_user=_make_user("owner"),
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_no_period_returns_message(self):
        owner = {"building_id": "13195", "lot_id": "3", "owner_name": "A", "is_test_data": True}
        mock_db = _make_db(owner_record=owner, period=None)
        with patch("routers.settlement_adjustment.db", mock_db):
            result = await get_settlement_adjustment(
                lot_id="3", building_id="13195", date="2025-05-01",
                current_user=_make_user(),
            )
        assert result["adjustment"] is None
        assert "No active levy period" in result["message"]

    @pytest.mark.asyncio
    async def test_cross_building_isolation(self):
        """Query for building 16244 uses that building_id in the DB filter."""
        mock_db = _make_db(owner_record=None)
        with patch("routers.settlement_adjustment.db", mock_db):
            try:
                await get_settlement_adjustment(
                    lot_id="1", building_id="16244", date="2025-05-01",
                    current_user=_make_user("strata_manager"),
                )
            except Exception:
                pass
        call_filter = mock_db.ownership_periods.find_one.call_args[0][0]
        assert call_filter["building_id"] == "16244"

    @pytest.mark.asyncio
    async def test_ec_member_can_access(self):
        owner = {"building_id": "13195", "lot_id": "2", "owner_name": "B", "is_test_data": True}
        mock_db = _make_db(owner_record=owner, period=None)
        with patch("routers.settlement_adjustment.db", mock_db):
            result = await get_settlement_adjustment(
                lot_id="2", building_id="13195", date="2025-05-01",
                current_user=_make_user("ec_member"),
            )
        assert result["lot_id"] == "2"


class TestGetLotOwnershipHistory:
    @pytest.mark.asyncio
    async def test_returns_history_without_id(self):
        history = [
            {"lot_id": "7", "building_id": "13195", "owner_name": "Old", "_id": "x", "is_test_data": True},
            {"lot_id": "7", "building_id": "13195", "owner_name": "New", "_id": "y", "is_test_data": True},
        ]
        mock_db = _make_db(history=history)
        with patch("routers.settlement_adjustment.db", mock_db):
            result = await get_lot_ownership_history(
                lot_id="7", building_id="13195",
                current_user=_make_user("super_admin"),
            )
        assert result["lot_id"] == "7"
        for period in result["ownership_history"]:
            assert "_id" not in period

    @pytest.mark.asyncio
    async def test_403_for_ec_member(self):
        from fastapi import HTTPException
        mock_db = _make_db()
        with patch("routers.settlement_adjustment.db", mock_db):
            with pytest.raises(HTTPException) as exc:
                await get_lot_ownership_history(
                    lot_id="7", building_id="13195",
                    current_user=_make_user("ec_member"),
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_history(self):
        mock_db = _make_db(history=[])
        with patch("routers.settlement_adjustment.db", mock_db):
            result = await get_lot_ownership_history(
                lot_id="99", building_id="13195",
                current_user=_make_user("super_admin"),
            )
        assert result["ownership_history"] == []


class TestBOLAQueryParamRejection:
    """
    HTTP-level regression tests for the BOLA fix.

    Before the fix, ``building_id`` was a caller-supplied ``Query`` parameter,
    so an authenticated user from building "13195" could read settlement data
    for building "16244" by passing ``?building_id=16244``. After the fix,
    ``building_id`` is resolved server-side via ``Depends(get_current_building)``
    and any query-string value is ignored.
    """

    AUTHED_BUILDING = "13195"
    ATTACKER_BUILDING = "16244"

    def setup_method(self):
        self.client = TestClient(app)
        self.user = {
            "id": "user-1",
            "full_name": "Test SM",
            "role": "strata_manager",
            "effective_role": "strata_manager",
            "is_approved": True,
            "building_id": self.AUTHED_BUILDING,
        }
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_current_building] = lambda: self.AUTHED_BUILDING

    def teardown_method(self):
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_building, None)

    def _mock_db(self):
        owner = {
            "building_id": self.AUTHED_BUILDING,
            "lot_id": "5",
            "owner_name": "Authed Owner",
            "effective_from": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "effective_to": None,
            "is_test_data": True,
        }
        period = {
            "building_id": self.AUTHED_BUILDING,
            "state": "open",
            "period_start": datetime(2025, 4, 1, tzinfo=timezone.utc),
            "period_end": datetime(2025, 6, 30, tzinfo=timezone.utc),
            "levy_cents": 153000,
        }
        return _make_db(owner_record=owner, period=period)

    def test_query_param_building_id_is_ignored(self):
        """Caller-supplied ``?building_id=16244`` must NOT override session context."""
        mock_db = self._mock_db()
        with patch.object(settlement_module, "db", mock_db):
            response = self.client.get(
                f"/api/settlement-adjustment/5",
                params={"date": "2025-05-01", "building_id": self.ATTACKER_BUILDING},
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200, response.text
        body = response.json()

        # Endpoint reports the SESSION building, not the attacker-supplied one.
        assert body["building_id"] == self.AUTHED_BUILDING
        assert body["building_id"] != self.ATTACKER_BUILDING

        # DB filter used the session building, not the attacker's value.
        owner_filter = mock_db.ownership_periods.find_one.call_args[0][0]
        assert owner_filter["building_id"] == self.AUTHED_BUILDING
        assert owner_filter["building_id"] != self.ATTACKER_BUILDING

        period_filter = mock_db.financial_periods.find_one.call_args[0][0]
        assert period_filter["building_id"] == self.AUTHED_BUILDING

    def test_history_query_param_building_id_is_ignored(self):
        """``GET /{lot_id}/history`` must also ignore ``?building_id=`` from caller."""
        # super_admin to satisfy the role gate on the history endpoint
        self.user["role"] = "super_admin"
        self.user["effective_role"] = "super_admin"

        mock_db = _make_db(history=[
            {
                "lot_id": "7",
                "building_id": self.AUTHED_BUILDING,
                "owner_name": "Authed",
                "_id": "x",
                "is_test_data": True,
            }
        ])
        with patch.object(settlement_module, "db", mock_db):
            response = self.client.get(
                f"/api/settlement-adjustment/7/history",
                params={"building_id": self.ATTACKER_BUILDING},
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["building_id"] == self.AUTHED_BUILDING
        assert body["building_id"] != self.ATTACKER_BUILDING

    def test_cross_building_denied_when_dependency_rejects(self):
        """When ``get_current_building`` raises 403, endpoint must not return data."""
        from fastapi import HTTPException

        def deny():
            raise HTTPException(status_code=403, detail="You do not have access to this building.")

        app.dependency_overrides[get_current_building] = deny

        mock_db = self._mock_db()
        with patch.object(settlement_module, "db", mock_db):
            response = self.client.get(
                f"/api/settlement-adjustment/5",
                params={"date": "2025-05-01", "building_id": self.ATTACKER_BUILDING},
                headers={"Authorization": "Bearer test-token"},
            )

        assert response.status_code == 403
        # No DB lookup should have happened — the dependency short-circuits the request.
        assert mock_db.ownership_periods.find_one.await_count == 0
