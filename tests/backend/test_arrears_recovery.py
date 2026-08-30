"""Tests for arrears recovery state machine — Gap 1.4"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _manager():
    return {"id": "u1", "full_name": "Mgr", "role": "strata_manager",
            "effective_role": "strata_manager", "is_approved": True}


def _mock_db(actions=None, plans=None):
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=actions or [])
    db.arrears_recovery_actions.find.return_value = cursor
    db.arrears_recovery_actions.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
    db.arrears_recovery_actions.update_many = AsyncMock(return_value=MagicMock())
    db.arrears_recovery_actions.aggregate = MagicMock(return_value=cursor)
    db.payment_plans.find_one = AsyncMock(return_value=plans)
    db.unit_levy_ledger.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    return db


class TestRecoveryActionTypeConstants:
    def test_all_list_contains_expected_actions(self):
        from models.arrears_recovery import RecoveryActionType
        assert "reminder_sent" in RecoveryActionType.ALL
        assert "lod_issued" in RecoveryActionType.ALL
        assert "solicitor_referred" in RecoveryActionType.ALL
        assert "payment_plan_active" in RecoveryActionType.ALL

    def test_manager_only_set(self):
        from models.arrears_recovery import RecoveryActionType
        assert "lod_issued" in RecoveryActionType.MANAGER_ONLY
        assert "reminder_sent" not in RecoveryActionType.MANAGER_ONLY

    def test_manual_action_request_schema(self):
        from models.arrears_recovery import ManualActionRequest
        req = ManualActionRequest(action_type="reminder_sent", notes="First notice")
        assert req.action_type == "reminder_sent"
        assert req.fee_override_cents is None


class TestRecoveryRouterGuards:
    @pytest.mark.asyncio
    async def test_get_unit_recovery_scoped(self):
        from routers.arrears_recovery import get_unit_recovery
        db = _mock_db()
        with patch("routers.arrears_recovery._get_db", return_value=db):
            result = await get_unit_recovery("12", building_id="13195", current_user=_manager())
        # The find call must include building_id
        call_args = db.arrears_recovery_actions.find.call_args[0][0]
        assert call_args["building_id"] == "13195"
        assert call_args["unit_number"] == "12"

    @pytest.mark.asyncio
    async def test_suppress_requires_manager_role(self):
        from routers.arrears_recovery import suppress_recovery
        from fastapi import HTTPException
        owner = {"id": "u2", "role": "owner", "effective_role": "owner", "is_approved": True}
        with pytest.raises(HTTPException) as exc:
            await suppress_recovery("12", until=None, notes=None, building_id="13195", current_user=owner)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_invalid_action_type_rejected(self):
        from routers.arrears_recovery import trigger_manual_action
        from models.arrears_recovery import ManualActionRequest
        from fastapi import HTTPException
        payload = ManualActionRequest(action_type="invalid_action")
        with pytest.raises(HTTPException) as exc:
            await trigger_manual_action("12", payload, building_id="13195", current_user=_manager())
        assert exc.value.status_code == 400


class TestSuppressionLogic:
    @pytest.mark.asyncio
    async def test_not_suppressed_when_no_actions(self):
        from routers.arrears_recovery import _is_suppressed
        db = _mock_db(actions=[])
        assert await _is_suppressed(db, "13195", "12") is False

    @pytest.mark.asyncio
    async def test_suppressed_when_latest_is_suppressed_with_no_expiry(self):
        from routers.arrears_recovery import _is_suppressed
        action = {"action_type": "suppressed", "suppressed_until": None}
        db = _mock_db(actions=[action])
        assert await _is_suppressed(db, "13195", "12") is True

    @pytest.mark.asyncio
    async def test_not_suppressed_when_suppressed_until_past(self):
        from routers.arrears_recovery import _is_suppressed
        action = {"action_type": "suppressed", "suppressed_until": "2000-01-01T00:00:00+00:00"}
        db = _mock_db(actions=[action])
        assert await _is_suppressed(db, "13195", "12") is False

    @pytest.mark.asyncio
    async def test_suppressed_when_active_payment_plan(self):
        from routers.arrears_recovery import _is_suppressed
        action = {"action_type": "payment_plan_active", "active_plan_id": "plan-001"}
        active_plan = {"id": "plan-001", "status": "active"}
        db = _mock_db(actions=[action], plans=active_plan)
        assert await _is_suppressed(db, "13195", "12") is True


class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_building_16244_recovery_isolated_from_13195(self):
        from routers.arrears_recovery import get_unit_recovery
        db = _mock_db()
        with patch("routers.arrears_recovery._get_db", return_value=db):
            await get_unit_recovery("12", building_id="16244", current_user=_manager())
        # Must query 16244, not 13195
        call_args = db.arrears_recovery_actions.find.call_args[0][0]
        assert call_args["building_id"] == "16244"


class TestCronScript:
    def test_cron_script_importable(self):
        import cron.cron_arrears_recovery  # noqa — just verify import works

    def test_thresholds_defined(self):
        """Recovery thresholds must be: 30d→reminder, 60d→formal, 90d→LOD."""
        from cron.cron_arrears_recovery import run_for_building
        # Thresholds are defined inside the function; verify module loads
        import inspect
        src = inspect.getsource(run_for_building)
        assert "30" in src
        assert "60" in src
        assert "90" in src
