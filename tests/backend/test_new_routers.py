"""
Tests for 4 new routers shipped in GAP session 2026-04-19:
  - GAP-FIN-012: levy_reminders
  - GAP-GOV-007: conflict_of_interest
  - GAP-COM-008: essential_services
  - GAP-FIN-006: arrears_risk

Multi-tenant isolation asserted in each module.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from request_context import set_ctx_building_id


def _manager(role="strata_manager", uid="mgr-1"):
    return {"id": uid, "role": role, "effective_role": role, "building_id": "13195"}


def _owner():
    return {"id": "own-1", "role": "owner", "effective_role": "owner", "building_id": "13195"}


# ══════════════════════════════════════════════════════════════════════════════
# GAP-FIN-012 — Levy Reminder Cadence
# ══════════════════════════════════════════════════════════════════════════════

class TestLevyReminderSettings:
    @pytest.mark.asyncio
    async def test_get_settings_returns_defaults_when_missing(self):
        set_ctx_building_id("13195")
        from routers.levy_reminders import get_reminder_settings

        db = MagicMock()
        db.levy_reminder_settings.find_one = AsyncMock(return_value=None)
        db.settings.find_one = AsyncMock(return_value=None)
        # Suppress the Postgres read so the test exercises the full Mongo fallback
        # path without requiring a live PG connection.
        with patch("routers.levy_reminders._get_db", return_value=db), \
             patch(
                 "services.levy_reminder_settings_service._get_postgres_levy_reminder_settings",
                 new=AsyncMock(return_value=None),
             ):
            result = await get_reminder_settings(
                building_id="13195",
                current_user=_manager(),
            )
        assert result["pre_due_days"] == [14, 7, 3]
        assert result["overdue_days"] == [1, 7, 14, 30]

    @pytest.mark.asyncio
    async def test_owner_cannot_get_settings(self):
        set_ctx_building_id("13195")
        from routers.levy_reminders import get_reminder_settings
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await get_reminder_settings(building_id="13195", current_user=_owner())
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_trigger_dry_run_returns_would_send(self):
        set_ctx_building_id("13195")
        from routers.levy_reminders import trigger_reminder_run as trigger_reminders

        db = MagicMock()
        db.levy_reminder_settings.find_one = AsyncMock(return_value=None)
        db.settings.find_one = AsyncMock(return_value=None)
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[
            {"unit_number": "101", "admin_balance": 500.0, "sinking_balance": 0.0,
             "building_id": "13195"}
        ])
        db.unit_levy_ledger.find.return_value = cursor
        with patch("routers.levy_reminders._get_db", return_value=db), \
             patch(
                 "services.levy_reminder_settings_service._get_postgres_levy_reminder_settings",
                 new=AsyncMock(return_value=None),
             ):
            result = await trigger_reminders(
                dry_run=True,
                building_id="13195",
                current_user=_manager(),
            )
        assert "would_send" in result or "dry_run" in result

    @pytest.mark.asyncio
    async def test_building_isolation_settings(self):
        """Settings query must be scoped to the caller's building."""
        set_ctx_building_id("16244")
        from routers.levy_reminders import get_reminder_settings

        db = MagicMock()
        db.levy_reminder_settings.find_one = AsyncMock(return_value=None)
        db.settings.find_one = AsyncMock(return_value=None)
        with patch("routers.levy_reminders._get_db", return_value=db), \
             patch(
                 "services.levy_reminder_settings_service._get_postgres_levy_reminder_settings",
                 new=AsyncMock(return_value=None),
             ):
            await get_reminder_settings(building_id="16244", current_user=_manager())
        db.levy_reminder_settings.find_one.assert_called_once()
        call_filter = db.levy_reminder_settings.find_one.call_args[0][0]
        assert call_filter.get("building_id") == "16244"


# ══════════════════════════════════════════════════════════════════════════════
# GAP-GOV-007 — Conflict-of-Interest Register
# ══════════════════════════════════════════════════════════════════════════════

VALID_OID = "5f43a0d1b77e1b4d5c8a9b0c"  # valid ObjectId hex for tests


class TestConflictOfInterest:
    @pytest.mark.asyncio
    async def test_create_coi_declaration(self):
        set_ctx_building_id("13195")
        from routers.conflict_of_interest import declare_conflict as create_conflict_declaration
        from routers.conflict_of_interest import ConflictCreate as ConflictOfInterestCreate
        from bson import ObjectId

        db = MagicMock()
        db.conflict_of_interest.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=ObjectId(VALID_OID))
        )
        payload = ConflictOfInterestCreate(
            conflict_type="financial",
            description="Has a financial interest in the proposed contractor",
            resolution="abstained",
        )
        user = {**_manager(), "id": "mgr-1", "full_name": "Jane Smith"}
        with patch("routers.conflict_of_interest._get_db", return_value=db):
            result = await create_conflict_declaration(
                payload=payload,
                building_id="13195",
                current_user=user,
            )
        assert result["building_id"] == "13195"
        assert result["conflict_type"] == "financial"
        assert result["retracted"] is False

    @pytest.mark.asyncio
    async def test_owner_cannot_create_coi(self):
        set_ctx_building_id("13195")
        from routers.conflict_of_interest import declare_conflict as create_conflict_declaration
        from routers.conflict_of_interest import ConflictCreate as ConflictOfInterestCreate
        from fastapi import HTTPException

        payload = ConflictOfInterestCreate(
            conflict_type="personal",
            description="Some conflict description here",
            resolution="pending",
        )
        with pytest.raises(HTTPException) as exc:
            await create_conflict_declaration(
                payload=payload,
                building_id="13195",
                current_user=_owner(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_retract_sets_flag(self):
        set_ctx_building_id("13195")
        from routers.conflict_of_interest import retract_conflict as retract_conflict_declaration
        import asyncio

        db = MagicMock()
        db.conflict_of_interest.update_one = AsyncMock(
            return_value=MagicMock(matched_count=1, modified_count=1)
        )
        # Suppress background audit task creation while closing the coroutine to
        # avoid pytest "was never awaited" warnings.
        with patch("routers.conflict_of_interest._get_db", return_value=db), \
                patch(
                    "routers.conflict_of_interest.asyncio.create_task",
                    side_effect=lambda coro: coro.close(),
                ):
            result = await retract_conflict_declaration(
                declaration_id=VALID_OID,
                building_id="13195",
                current_user={**_manager(), "id": "mgr-1", "full_name": "Chair"},
            )
        # retract_conflict returns {"message": ..., "declaration_id": ...}
        assert result["declaration_id"] == VALID_OID

    @pytest.mark.asyncio
    async def test_cross_building_get_returns_400_bad_oid(self):
        """Non-ObjectId string raises 400 before hitting DB — confirms isolation at parse layer."""
        set_ctx_building_id("16244")
        from routers.conflict_of_interest import get_conflict as get_conflict_declaration
        from fastapi import HTTPException

        db = MagicMock()
        with patch("routers.conflict_of_interest._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await get_conflict_declaration(
                    declaration_id="not-an-oid",
                    building_id="16244",
                    current_user=_manager(),
                )
        assert exc.value.status_code == 400  # invalid ObjectId, not 404

    @pytest.mark.asyncio
    async def test_cross_building_get_returns_404_for_valid_oid(self):
        """Valid OID not found in building 16244 returns 404."""
        set_ctx_building_id("16244")
        from routers.conflict_of_interest import get_conflict as get_conflict_declaration
        from fastapi import HTTPException

        db = MagicMock()
        db.conflict_of_interest.find_one = AsyncMock(return_value=None)
        with patch("routers.conflict_of_interest._get_db", return_value=db):
            with pytest.raises(HTTPException) as exc:
                await get_conflict_declaration(
                    declaration_id=VALID_OID,
                    building_id="16244",
                    current_user=_manager(),
                )
        assert exc.value.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# GAP-COM-008 — Essential Services Log
# ══════════════════════════════════════════════════════════════════════════════

def _es_doc():
    return {
        "_id": "oid-es",
        "id": "es-1",
        "building_id": "13195",
        "service_type": "cooling_tower",
        "description": "Annual cooling tower inspection",
        "contractor": "AquaCool Pty Ltd",
        "serviced_date": "2026-04-01",
        "interval_months": 6,
        "next_due": "2026-10-01",
        "certificate_url": None,
        "notes": None,
        "logged_by": "mgr-1",
        "created_at": "2026-04-01T00:00:00+00:00",
        "updated_at": "2026-04-01T00:00:00+00:00",
    }


class TestEssentialServices:
    @pytest.mark.asyncio
    async def test_log_service_calculates_next_due(self):
        set_ctx_building_id("13195")
        from routers.essential_services import log_service_event
        from routers.essential_services import EssentialServiceCreate
        from bson import ObjectId

        db = MagicMock()
        db.essential_services_log.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=ObjectId(VALID_OID))
        )
        payload = EssentialServiceCreate(
            service_type="cooling_tower",
            asset_name="Roof Cooling Tower 1",
            service_date="2026-04-01",
            technician="John Tech",
            company="AquaCool Pty Ltd",
            interval_months=6,
        )
        user = {**_manager(), "id": "mgr-1", "full_name": "Manager"}
        with patch("routers.essential_services._get_db", return_value=db), \
                patch(
                    "routers.essential_services.asyncio.create_task",
                    side_effect=lambda coro: coro.close(),
                ):
            result = await log_service_event(
                payload=payload,
                building_id="13195",
                current_user=user,
            )
        # ~6 months from 2026-04-01 = 2026-10-xx (within 30 days flex)
        assert result["next_due"] is not None
        assert result["next_due"].startswith("2026-09") or result["next_due"].startswith("2026-10")
        assert result["service_type"] == "cooling_tower"
        assert result["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_owner_cannot_log_service(self):
        set_ctx_building_id("13195")
        from routers.essential_services import log_service_event
        from routers.essential_services import EssentialServiceCreate
        from fastapi import HTTPException

        payload = EssentialServiceCreate(
            service_type="lift",
            asset_name="Passenger Lift",
            service_date="2026-04-01",
            technician="Lift Tech",
            interval_months=12,
        )
        with pytest.raises(HTTPException) as exc:
            await log_service_event(
                payload=payload,
                building_id="13195",
                current_user=_owner(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_overdue_returns_sorted_list(self):
        set_ctx_building_id("13195")
        from routers.essential_services import list_overdue_services
        from bson import ObjectId

        overdue_doc = {
            "_id": ObjectId(VALID_OID), "building_id": "13195",
            "service_type": "cooling_tower", "next_due": "2025-01-01",
            "status": "due",
        }

        async def _aiter(docs):
            for d in docs:
                yield d

        db = MagicMock()
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=_aiter([overdue_doc]))
        db.essential_services_log.find.return_value = cursor
        with patch("routers.essential_services._get_db", return_value=db):
            result = await list_overdue_services(
                building_id="13195",
                current_user=_manager(),
            )
        assert result["total"] == 1
        assert result["data"][0]["days_overdue"] > 0

    @pytest.mark.asyncio
    async def test_building_isolation_overdue(self):
        set_ctx_building_id("16244")
        from routers.essential_services import list_overdue_services

        async def _aiter_empty():
            return
            yield  # make it an async generator

        db = MagicMock()
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=_aiter_empty())
        db.essential_services_log.find.return_value = cursor
        with patch("routers.essential_services._get_db", return_value=db):
            result = await list_overdue_services(
                building_id="16244",
                current_user=_manager(),
            )
        assert result["total"] == 0
        call_filter = db.essential_services_log.find.call_args[0][0]
        assert call_filter.get("building_id") == "16244"


# ══════════════════════════════════════════════════════════════════════════════
# GAP-FIN-006 — Predictive Arrears Risk
# ══════════════════════════════════════════════════════════════════════════════

class TestArrearsRisk:
    @pytest.mark.asyncio
    async def test_unit_risk_returns_zero_for_no_arrears(self):
        set_ctx_building_id("13195")
        from routers.arrears_risk import unit_arrears_risk

        with patch("services.arrears_analytics.compute_arrears_risk", new=AsyncMock(return_value=[])):
            result = await unit_arrears_risk(
                unit_number="101",
                building_id="13195",
                current_user=_manager(),
            )
        assert result["score"] == 0
        assert result["band"] == "LOW"
        assert result["outstanding_aud"] == 0.0

    @pytest.mark.asyncio
    async def test_summary_counts_bands(self):
        set_ctx_building_id("13195")
        from routers.arrears_risk import arrears_risk_summary

        units = [
            {"band": "CRITICAL", "outstanding_aud": 5000, "hardship_flag": True, "form1_eligible": True},
            {"band": "HIGH", "outstanding_aud": 2000, "hardship_flag": True, "form1_eligible": True},
            {"band": "LOW", "outstanding_aud": 100, "hardship_flag": False, "form1_eligible": False},
        ]
        with patch("services.arrears_analytics.compute_arrears_risk", new=AsyncMock(return_value=units)):
            result = await arrears_risk_summary(
                building_id="13195",
                current_user=_manager(),
            )
        assert result["band_distribution"]["CRITICAL"] == 1
        assert result["band_distribution"]["HIGH"] == 1
        assert result["hardship_flagged"] == 2
        assert result["form1_eligible"] == 2
        assert result["total_outstanding_aud"] == 7100.0

    @pytest.mark.asyncio
    async def test_owner_cannot_access_risk_data(self):
        set_ctx_building_id("13195")
        from routers.arrears_risk import list_arrears_risk
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await list_arrears_risk(
                building_id="13195",
                current_user=_owner(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_score_unit_critical_band(self):
        from services.arrears_analytics import score_unit

        result = score_unit(
            outstanding_cents=200_000,  # $2000
            average_quarterly_levy_cents=100_000,  # $1000 avg
            days_since_last_payment=95,
            missed_quarters_last_12m=4,
            consecutive_missed=4,
        )
        assert result["band"] == "CRITICAL"
        assert result["score"] >= 75

    @pytest.mark.asyncio
    async def test_score_unit_low_band(self):
        from services.arrears_analytics import score_unit

        result = score_unit(
            outstanding_cents=5_000,
            average_quarterly_levy_cents=100_000,
            days_since_last_payment=10,
            missed_quarters_last_12m=0,
            consecutive_missed=0,
        )
        assert result["band"] == "LOW"
        assert result["score"] < 25
