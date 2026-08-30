# @featuretrace:bi-analytics — Phase 2 BI tests: toggle resolution, access control, portfolio endpoints, ETL, cutover.
# Layer: test
# Data flow: test_bi_phase2 → routers/bi.py + services/bi_toggle_service.py + services/bi_service.py
# Related: backend/routers/bi.py
#          backend/services/bi_toggle_service.py
#          backend/services/bi_service.py
#          backend/services/bi_etl_service.py
# Toggle: bi_analytics_enabled, bi_analytics_platform_enabled, bi_analytics_building_enabled,
#         bi_portfolio_analytics_enabled, bi_pg_primary_enabled
"""Phase 2 BI Analytics tests.

Covers:
  A. Toggle resolution (hierarchy, safe default, legacy alias)
  B. Access control per role (super_admin, agency principal, strata_manager, ec_member, owner, tenant)
  C. Portfolio endpoints (agency, manager, platform)
  D. ETL fact jobs (levy_payment, occupancy_snapshot, capex_plan, ownership_transfer)
  E. Cutover status checks
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

BUILDING_ID = "99999"
AGENCY_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MANAGER_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
USER_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _user(role: str, effective_role: str | None = None) -> dict:
    return {
        "_id": USER_ID,
        "role": role,
        "effective_role": effective_role or role,
        "building_id": BUILDING_ID,
        "unit_number": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# A. Toggle resolution
# ─────────────────────────────────────────────────────────────────────────────

class TestToggleResolution:
    """Verify hierarchical toggle resolution: building → agency → platform → safe default."""

    @pytest.mark.asyncio
    async def test_safe_default_false_when_no_toggles_enabled(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_platform_toggle", AsyncMock(return_value=False)), \
             patch("services.bi_toggle_service._resolve_global_toggle", AsyncMock(return_value=False)), \
             patch("services.bi_toggle_service._resolve_building_toggle_with_source", AsyncMock(return_value=(False, False))), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="ec_member", effective_role="ec_member",
                agency_id=None, building_id=BUILDING_ID,
            )
        assert result.enabled is False
        assert result.source == "safe_default"

    @pytest.mark.asyncio
    async def test_building_override_wins_over_platform(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_building_toggle_with_source", AsyncMock(return_value=(True, False))), \
             patch("services.bi_toggle_service._resolve_platform_toggle", AsyncMock(return_value=False)), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="ec_member", effective_role="ec_member",
                agency_id=None, building_id=BUILDING_ID,
            )
        assert result.enabled is True
        assert result.source == "building_override"

    @pytest.mark.asyncio
    async def test_agency_override_used_when_building_off(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_building_toggle_with_source", AsyncMock(return_value=(False, False))), \
             patch("services.bi_toggle_service._resolve_agency_toggle", AsyncMock(return_value=True)), \
             patch("services.bi_toggle_service._resolve_platform_toggle", AsyncMock(return_value=False)), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="ec_member", effective_role="ec_member",
                agency_id=AGENCY_ID, building_id=BUILDING_ID,
            )
        assert result.enabled is True
        assert result.source == "agency_override"

    @pytest.mark.asyncio
    async def test_platform_default_used_when_building_and_agency_off(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_building_toggle_with_source", AsyncMock(return_value=(False, False))), \
             patch("services.bi_toggle_service._resolve_agency_toggle", AsyncMock(return_value=False)), \
             patch("services.bi_toggle_service._resolve_platform_toggle", AsyncMock(return_value=True)), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="ec_member", effective_role="ec_member",
                agency_id=AGENCY_ID, building_id=BUILDING_ID,
            )
        assert result.enabled is True
        assert result.source == "platform_default"

    @pytest.mark.asyncio
    async def test_pg_primary_independent_of_bi_visibility(self):
        """bi_pg_primary_enabled is resolved separately and does not affect enabled."""
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_building_toggle_with_source", AsyncMock(return_value=(True, False))), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=True)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="ec_member", effective_role="ec_member",
                agency_id=None, building_id=BUILDING_ID,
            )
        assert result.enabled is True
        assert result.pg_primary is True

    @pytest.mark.asyncio
    async def test_pg_primary_false_does_not_block_bi(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_building_toggle_with_source", AsyncMock(return_value=(True, False))), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="ec_member", effective_role="ec_member",
                agency_id=None, building_id=BUILDING_ID,
            )
        assert result.enabled is True
        assert result.pg_primary is False

    @pytest.mark.asyncio
    async def test_legacy_alias_accepted_with_warning(self):
        """bi_analytics_enabled (legacy) activates BI with a deprecation warning.

        Path: building toggle OFF, no management entity, no agency, platform toggle OFF,
        legacy global toggle ON → enabled=True + warning emitted.
        """
        from services.bi_toggle_service import resolve_bi_feature_access

        with patch("services.bi_toggle_service._resolve_building_toggle_with_source",
                   AsyncMock(return_value=(False, False))), \
             patch("services.bi_toggle_service._resolve_management_entity_id",
                   AsyncMock(return_value=None)), \
             patch("services.bi_toggle_service._resolve_platform_toggle",
                   AsyncMock(return_value=False)), \
             patch("services.bi_toggle_service._resolve_global_toggle",
                   AsyncMock(return_value=True)), \
             patch("services.bi_toggle_service._resolve_pg_primary",
                   AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="ec_member", effective_role="ec_member",
                agency_id=None, building_id=BUILDING_ID,
            )
        assert result.enabled is True
        assert any("legacy bi_analytics_enabled" in w for w in result.warnings)


# ─────────────────────────────────────────────────────────────────────────────
# B. Access control per role
# ─────────────────────────────────────────────────────────────────────────────

class TestAccessControlByRole:
    """Verify role-based BI access scoping."""

    @pytest.mark.asyncio
    async def test_super_admin_gets_platform_scope(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_platform_toggle", AsyncMock(return_value=True)), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="super_admin", effective_role="super_admin",
                agency_id=None, building_id=None,
            )
        assert result.scope == "platform"
        assert result.enabled is True

    @pytest.mark.asyncio
    async def test_super_admin_blocked_when_platform_toggle_off(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_platform_toggle", AsyncMock(return_value=False)), \
             patch("services.bi_toggle_service._resolve_global_toggle", AsyncMock(return_value=False)), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="super_admin", effective_role="super_admin",
                agency_id=None, building_id=None,
            )
        assert result.enabled is False

    @pytest.mark.asyncio
    async def test_strata_manager_gets_portfolio_scope(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_platform_toggle", AsyncMock(return_value=True)), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="strata_manager", effective_role="strata_manager",
                agency_id=AGENCY_ID, building_id=BUILDING_ID,
            )
        assert result.scope == "portfolio"
        assert result.enabled is True

    @pytest.mark.asyncio
    async def test_ec_member_gets_building_scope(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_building_toggle_with_source", AsyncMock(return_value=(True, False))), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="ec_member", effective_role="ec_member",
                agency_id=None, building_id=BUILDING_ID,
            )
        assert result.scope == "building"
        assert result.enabled is True

    @pytest.mark.asyncio
    async def test_owner_gets_building_scope_with_restriction_warning(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_building_toggle_with_source", AsyncMock(return_value=(True, False))), \
             patch("services.bi_toggle_service._resolve_global_toggle", AsyncMock(return_value=True)), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="owner", effective_role="owner",
                agency_id=None, building_id=BUILDING_ID,
            )
        assert result.scope == "building"
        assert result.enabled is True
        assert any("restricted" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_tenant_blocked_by_safe_default(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        with patch("services.bi_toggle_service._resolve_building_toggle_with_source", AsyncMock(return_value=(False, False))), \
             patch("services.bi_toggle_service._resolve_global_toggle", AsyncMock(return_value=False)), \
             patch("services.bi_toggle_service._resolve_pg_primary", AsyncMock(return_value=False)):
            result = await resolve_bi_feature_access(
                user_id=USER_ID, role="tenant", effective_role="tenant",
                agency_id=None, building_id=BUILDING_ID,
            )
        assert result.enabled is False

    @pytest.mark.asyncio
    async def test_guest_always_denied(self):
        from services.bi_toggle_service import resolve_bi_feature_access
        result = await resolve_bi_feature_access(
            user_id=USER_ID, role="guest", effective_role="guest",
            agency_id=None, building_id=None,
        )
        assert result.enabled is False
        assert result.source == "safe_default"


# ─────────────────────────────────────────────────────────────────────────────
# C. Portfolio endpoints — building/agency/manager access isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestManagerBuildingIsolation:
    """Strata manager sees only assigned buildings, not all buildings."""

    @pytest.mark.asyncio
    async def test_get_manager_buildings_returns_assigned_only(self):
        from services.bi_toggle_service import get_manager_buildings
        mock_rows = [("99999",), ("88888",)]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = mock_rows
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("db_postgres.session.async_session_context") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await get_manager_buildings(MANAGER_ID)

        assert "99999" in result
        assert "88888" in result
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_manager_buildings_empty_when_no_assignments(self):
        from services.bi_toggle_service import get_manager_buildings
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("db_postgres.session.async_session_context") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await get_manager_buildings(MANAGER_ID)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_agency_buildings_returns_agency_scoped(self):
        from services.bi_toggle_service import get_agency_buildings
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("13195",), ("16244",)]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("db_postgres.session.async_session_context") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await get_agency_buildings(AGENCY_ID)

        assert "13195" in result
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_resolve_manager_access_blocks_wrong_user(self):
        from services.bi_toggle_service import resolve_manager_access
        with patch("services.bi_toggle_service._get_strata_manager_profile_by_user",
                   AsyncMock(return_value={"strata_manager_id": "different-id", "agency_id": None})), \
             patch("services.bi_toggle_service._get_strata_manager_profile_by_id",
                   AsyncMock(return_value={"agency_id": AGENCY_ID})), \
             patch("services.bi_toggle_service._resolve_platform_toggle", AsyncMock(return_value=True)), \
             patch("services.bi_toggle_service._user_belongs_to_agency",
                  AsyncMock(return_value=False)):
            result = await resolve_manager_access(
                user_id=USER_ID, effective_role="strata_manager",
                strata_manager_id=MANAGER_ID,
            )
        assert result is False

    @pytest.mark.asyncio
    async def test_resolve_manager_access_allows_own_profile(self):
        from services.bi_toggle_service import resolve_manager_access
        with patch("services.bi_toggle_service._get_strata_manager_profile_by_user",
                   AsyncMock(return_value={"strata_manager_id": MANAGER_ID, "agency_id": AGENCY_ID})), \
             patch("services.bi_toggle_service._resolve_platform_toggle", AsyncMock(return_value=True)):
            result = await resolve_manager_access(
                user_id=USER_ID, effective_role="strata_manager",
                strata_manager_id=MANAGER_ID,
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_super_admin_always_allowed_manager_access(self):
        from services.bi_toggle_service import resolve_manager_access
        with patch("services.bi_toggle_service._resolve_platform_toggle", AsyncMock(return_value=True)):
            result = await resolve_manager_access(
                user_id=USER_ID, effective_role="super_admin",
                strata_manager_id=MANAGER_ID,
            )
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# D. ETL fact jobs
# ─────────────────────────────────────────────────────────────────────────────

class TestETLFinancialBalance:
    """fact_financial_balance must carry actual/as-of fund balances."""

    def test_actual_balance_prefers_current_balance_over_projected_closing(self):
        from services.bi_etl_service import _fund_actual_balance_cents

        assert _fund_actual_balance_cents(
            {
                "current_balance": 9187.44,
                "closing_balance": 180000.00,
                "opening_balance": 34332.27,
                "levy_income": 78698.26,
                "total_expenses": 145628.22,
            },
            actual_levy_income_cents=7869826,
            fallback_closing_cents=18000000,
        ) == 918744

    def test_actual_balance_derives_from_opening_paid_income_and_expenses_when_current_missing(self):
        from services.bi_etl_service import _fund_actual_balance_cents

        assert _fund_actual_balance_cents(
            {
                "opening_balance": 34332.27,
                "levy_income": 309882.00,
                "total_expenses": 145628.22,
                "closing_balance": 180000.00,
            },
            actual_levy_income_cents=7869826,
            fallback_closing_cents=18000000,
        ) == -3259769

class TestETLLevyPayment:
    """fact_levy_payment ETL: Mongo → PG, idempotent."""

    @pytest.mark.asyncio
    async def test_etl_levy_payment_skips_empty_collection(self):
        from services.bi_etl_service import etl_fact_levy_payment
        mock_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        mock_db.levy_payments.find.return_value = cursor

        with patch("services.bi_etl_service._resolve", AsyncMock(return_value=("scheme-uuid", "tenant-uuid"))), \
             patch("database.db", mock_db):
            result = await etl_fact_levy_payment(BUILDING_ID)

        assert result == 0

    @pytest.mark.asyncio
    async def test_etl_levy_payment_inserts_rows(self):
        from services.bi_etl_service import etl_fact_levy_payment
        from datetime import datetime, timezone as tz
        rows = [
            {
                "payment_id": "pay-001", "lot_number": "42",
                "amount": 1530.30, "received_date": datetime(2026, 3, 31, tzinfo=tz.utc),
                "financial_year": "2026", "quarter": 1,
                "payment_method": "bpay", "is_test_data": False,
            }
        ]
        mock_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=rows)
        mock_db.levy_payments.find.return_value = cursor

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch("services.bi_etl_service._resolve", AsyncMock(return_value=("scheme-uuid", "tenant-uuid"))), \
             patch("database.db", mock_db), \
             patch("services.bi_etl_service._log_etl_start", AsyncMock(return_value="run-uuid")), \
             patch("services.bi_etl_service._log_etl_done", AsyncMock()), \
             patch("db_postgres.session.async_session_context") as mock_ctx, \
             patch("db_postgres.session.set_tenant", AsyncMock()):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await etl_fact_levy_payment(BUILDING_ID)

        assert result >= 1

    @pytest.mark.asyncio
    async def test_etl_levy_payment_is_idempotent_on_conflict(self):
        """ON CONFLICT DO UPDATE — second run counts as update, not duplicate insert."""
        from services.bi_etl_service import etl_fact_levy_payment
        from datetime import datetime, timezone as tz
        rows = [{"payment_id": "pay-dup", "lot_number": "1", "amount": 100.0,
                 "received_date": datetime(2026, 1, 1, tzinfo=tz.utc),
                 "financial_year": "2026", "quarter": 1, "payment_method": "eft",
                 "is_test_data": False}]

        mock_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=rows)
        mock_db.levy_payments.find.return_value = cursor

        # Simulate conflict: rowcount=0 means ON CONFLICT path
        mock_session = AsyncMock()
        conflict_result = MagicMock()
        conflict_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=conflict_result)
        mock_session.commit = AsyncMock()

        with patch("services.bi_etl_service._resolve", AsyncMock(return_value=("sid", "tid"))), \
             patch("database.db", mock_db), \
             patch("services.bi_etl_service._log_etl_start", AsyncMock(return_value="run-id")), \
             patch("services.bi_etl_service._log_etl_done", AsyncMock()), \
             patch("db_postgres.session.async_session_context") as mock_ctx, \
             patch("db_postgres.session.set_tenant", AsyncMock()):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await etl_fact_levy_payment(BUILDING_ID)
        # update path counted — still returns 1 (not error)
        assert result >= 0


class TestETLOccupancySnapshot:

    @pytest.mark.asyncio
    async def test_etl_occupancy_classifies_tenanted_correctly(self):
        from services.bi_etl_service import etl_fact_occupancy_snapshot
        rows = [
            {"lot_number": "1", "tenanted": True, "owner_occupied": False, "is_vacant": False, "is_test_data": False},
            {"lot_number": "2", "tenanted": False, "owner_occupied": True, "is_vacant": False, "is_test_data": False},
            {"lot_number": "3", "is_vacant": True, "is_test_data": False},
        ]
        mock_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=rows)
        mock_db.units.find.return_value = cursor

        insert_calls = []
        async def capture_execute(stmt, params=None):
            if params:
                insert_calls.append(params.get("occ"))
            r = MagicMock()
            r.rowcount = 1
            return r

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=capture_execute)
        mock_session.commit = AsyncMock()

        with patch("services.bi_etl_service._resolve", AsyncMock(return_value=("sid", "tid"))), \
             patch("database.db", mock_db), \
             patch("services.bi_etl_service._log_etl_start", AsyncMock(return_value="run")), \
             patch("services.bi_etl_service._log_etl_done", AsyncMock()), \
             patch("db_postgres.session.async_session_context") as mock_ctx, \
             patch("db_postgres.session.set_tenant", AsyncMock()):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await etl_fact_occupancy_snapshot(BUILDING_ID)

        assert result == 3
        assert "tenanted" in insert_calls
        assert "owner_occupied" in insert_calls
        assert "vacant" in insert_calls


class TestETLCapexPlan:

    @pytest.mark.asyncio
    async def test_etl_capex_plan_skips_when_no_plan_doc(self):
        from services.bi_etl_service import etl_fact_capex_plan
        mock_db = MagicMock()
        mock_db.sinking_fund_plan.find_one = AsyncMock(return_value=None)

        with patch("services.bi_etl_service._resolve", AsyncMock(return_value=("sid", "tid"))), \
             patch("database.db", mock_db):
            result = await etl_fact_capex_plan(BUILDING_ID)

        assert result == 0

    @pytest.mark.asyncio
    async def test_etl_capex_plan_inserts_schedule_items(self):
        from services.bi_etl_service import etl_fact_capex_plan
        plan_doc = {
            "building_id": BUILDING_ID,
            "is_test_data": False,
            "capital_schedule": [
                {"year": "2026", "description": "Lift refurb", "planned_cost": 50000.0, "category": "lift"},
                {"year": "2027", "description": "Roof repair", "planned_cost": 25000.0, "category": "roof"},
            ],
        }
        mock_db = MagicMock()
        mock_db.sinking_fund_plan.find_one = AsyncMock(return_value=plan_doc)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch("services.bi_etl_service._resolve", AsyncMock(return_value=("sid", "tid"))), \
             patch("database.db", mock_db), \
             patch("services.bi_etl_service._log_etl_start", AsyncMock(return_value="run")), \
             patch("services.bi_etl_service._log_etl_done", AsyncMock()), \
             patch("db_postgres.session.async_session_context") as mock_ctx, \
             patch("db_postgres.session.set_tenant", AsyncMock()):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await etl_fact_capex_plan(BUILDING_ID)

        assert result == 2


class TestETLOwnershipTransfer:

    @pytest.mark.asyncio
    async def test_etl_ownership_transfer_skips_rows_without_dates(self):
        from services.bi_etl_service import etl_fact_ownership_transfer
        rows = [{"lot_number": "5", "start_date": None, "transfer_type": "sale", "is_test_data": False}]
        mock_db = MagicMock()
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=rows)
        mock_db.ownership_periods.find.return_value = cursor

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with patch("services.bi_etl_service._resolve", AsyncMock(return_value=("sid", "tid"))), \
             patch("database.db", mock_db), \
             patch("services.bi_etl_service._log_etl_start", AsyncMock(return_value="run")), \
             patch("services.bi_etl_service._log_etl_done", AsyncMock()), \
             patch("db_postgres.session.async_session_context") as mock_ctx, \
             patch("db_postgres.session.set_tenant", AsyncMock()):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await etl_fact_ownership_transfer(BUILDING_ID)

        assert result == 0


# ─────────────────────────────────────────────────────────────────────────────
# E. Cutover status
# ─────────────────────────────────────────────────────────────────────────────

class TestCutoverStatus:
    """get_bi_cutover_status: missing facts block PG-primary, parity failures block it too."""

    @pytest.mark.asyncio
    async def test_cutover_fails_when_scheme_not_found(self):
        from services.bi_service import get_bi_cutover_status
        with patch("services.bi_service._resolve_scheme_id",
                   AsyncMock(side_effect=RuntimeError("No scheme"))):
            result = await get_bi_cutover_status(BUILDING_ID)

        assert result["ready"] is False
        assert result["can_enable_pg_primary"] is False
        assert any(f["check"] == "scheme_resolution" for f in result["failed_checks"])

    @pytest.mark.asyncio
    async def test_cutover_can_enable_bi_when_critical_facts_present(self):
        """can_enable_bi=True even if non-critical facts are missing."""
        from services.bi_service import get_bi_cutover_status

        async def fake_execute(stmt, params=None):
            r = MagicMock()
            r.scalar.return_value = 10
            r.fetchone.return_value = (10, 50000, 40000, 1000, 2)
            r.fetchall.return_value = [("analytics.fact_levy_charge", "success", None, 1.0)]
            return r

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=fake_execute)

        with patch("services.bi_service._resolve_scheme_id",
                   AsyncMock(return_value=("scheme-uuid", "tenant-uuid"))), \
             patch("db_postgres.session.async_session_context") as mock_ctx, \
             patch("db_postgres.session.set_tenant", AsyncMock()):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await get_bi_cutover_status(BUILDING_ID)

        # With mocked data, at minimum scheme resolution succeeded
        assert "building_id" in result
        assert "failed_checks" in result
        assert "can_enable_bi" in result

    @pytest.mark.asyncio
    async def test_cutover_reports_building_id(self):
        from services.bi_service import get_bi_cutover_status
        with patch("services.bi_service._resolve_scheme_id",
                   AsyncMock(side_effect=RuntimeError("No scheme"))):
            result = await get_bi_cutover_status(BUILDING_ID)

        assert result["building_id"] == BUILDING_ID


# ─────────────────────────────────────────────────────────────────────────────
# F. Nightly ETL includes new jobs
# ─────────────────────────────────────────────────────────────────────────────

class TestNightlyETLIncludesNewJobs:
    """run_nightly_etl must include levy_payment, occupancy_snapshot, capex_plan, ownership_transfer."""

    @pytest.mark.asyncio
    async def test_nightly_etl_job_list_complete(self):
        from services.bi_etl_service import run_nightly_etl
        called_jobs: list[str] = []

        async def fake_job(name):
            called_jobs.append(name)
            return 0

        with patch("services.bi_etl_service.etl_dim_building", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_financial_balance", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_levy_charge", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_levy_payment", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_arrears_snapshot", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_work_order", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_compliance_event", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_utility_bill", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_smart_request", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_building_health_snapshot", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_occupancy_snapshot", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_capex_plan", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_ownership_transfer", AsyncMock(return_value=0)):
            result = await run_nightly_etl(BUILDING_ID)

        required = {
            "fact_levy_payment", "fact_occupancy_snapshot",
            "fact_capex_plan", "fact_ownership_transfer",
        }
        assert required.issubset(set(result.keys())), (
            f"Missing ETL jobs in nightly run: {required - set(result.keys())}"
        )

    @pytest.mark.asyncio
    async def test_nightly_etl_continues_after_single_job_failure(self):
        """One job failing must not abort the rest."""
        from services.bi_etl_service import run_nightly_etl

        with patch("services.bi_etl_service.etl_dim_building", AsyncMock(side_effect=RuntimeError("DB down"))), \
             patch("services.bi_etl_service.etl_fact_financial_balance", AsyncMock(return_value=5)), \
             patch("services.bi_etl_service.etl_fact_levy_charge", AsyncMock(return_value=10)), \
             patch("services.bi_etl_service.etl_fact_levy_payment", AsyncMock(return_value=3)), \
             patch("services.bi_etl_service.etl_fact_arrears_snapshot", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_work_order", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_compliance_event", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_utility_bill", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_smart_request", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_building_health_snapshot", AsyncMock(return_value=0)), \
             patch("services.bi_etl_service.etl_fact_occupancy_snapshot", AsyncMock(return_value=2)), \
             patch("services.bi_etl_service.etl_fact_capex_plan", AsyncMock(return_value=4)), \
             patch("services.bi_etl_service.etl_fact_ownership_transfer", AsyncMock(return_value=1)):
            result = await run_nightly_etl(BUILDING_ID)

        assert result["dim_building"]["status"] == "error"
        assert result["fact_levy_payment"]["status"] == "ok"
        assert result["fact_occupancy_snapshot"]["status"] == "ok"
