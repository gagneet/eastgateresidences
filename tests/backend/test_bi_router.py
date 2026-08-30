# @featuretrace:bi-analytics — Integration tests for /bi/* router endpoints.
# Layer: test
# Data flow: test → routers/bi.py functions (direct call) → bi_service (patched at source module)
# Related: backend/routers/bi.py
#          backend/services/bi_service.py
# Toggle: bi_analytics_enabled
# Tests: this file
"""Tests for /api/bi/* endpoints.

Testing strategy: call route handler functions directly (no HTTP client) with
mocked current_user, patching bi_service functions at their source module
(services.bi_service.<func>) since the router uses lazy inline imports.

Coverage:
  - _require_manager: rejects non-manager roles, accepts manager roles.
  - Effective_role used (not raw role) for elevated users.
  - Building endpoints return correct envelope shape.
  - Owner lot access: own lot OK, cross-lot 403 for owner role.
  - Manager can access any lot.
  - Portfolio endpoints: non-manager 403 via _resolve_portfolio_buildings.
  - building_alerts: deduplicates results from 4 evaluators via asyncio.gather.
  - _require_admin: super_admin only on etl-status.
  - Multi-tenant: different buildings produce separate service calls.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

BUILDING_ID = "13195"


def _make_user(role: str, building_id: str = BUILDING_ID, unit_number: str | None = None):
    return {
        "id": "user-001",
        "role": role,
        "effective_role": role,
        "building_id": building_id,
        "unit_number": unit_number,
        "_id": "user-001",
        "is_test_data": False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# _require_manager role guard
# ─────────────────────────────────────────────────────────────────────────────

class TestRequireManagerGuard:
    """The guard is async since GAP-SEC-014 — it hydrates verified building claims.

    These MUST await it. A non-awaited call returns a coroutine and runs nothing, so
    `pytest.raises` sees no exception and a "must not raise" assertion passes
    vacuously — the deny tests failed loudly on the change, the allow tests did not.
    """

    NON_MANAGER_ROLES = ["owner", "tenant", "guest", "real_estate_agent", "service_provider"]
    MANAGER_ROLES = ["super_admin", "strata_manager", "strata_admin", "ec_member"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", NON_MANAGER_ROLES)
    async def test_non_manager_gets_403(self, role):
        from routers.bi import _require_manager
        user = _make_user(role)
        with pytest.raises(HTTPException) as exc:
            await _require_manager(user)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("role", MANAGER_ROLES)
    async def test_manager_roles_pass(self, role):
        from routers.bi import _require_manager
        user = _make_user(role)
        await _require_manager(user)  # must not raise

    @pytest.mark.asyncio
    async def test_effective_role_used_not_raw_role(self):
        """Elevated owner (effective_role=ec_member) must pass the guard."""
        from routers.bi import _require_manager
        user = _make_user("owner")
        user["effective_role"] = "ec_member"
        await _require_manager(user)  # must not raise

    @pytest.mark.asyncio
    async def test_raw_owner_role_without_effective_role_is_rejected(self):
        from routers.bi import _require_manager
        user = {"role": "owner", "building_id": BUILDING_ID}  # no effective_role key
        with pytest.raises(HTTPException) as exc:
            await _require_manager(user)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_guard_is_a_coroutine_function(self):
        """Pins the await contract itself.

        If the guard is ever made synchronous again, every `await` above still
        works (awaiting None fails, but a sync guard returning None would be
        awaited and raise) — this assertion is the unambiguous one.
        """
        import inspect

        from routers.bi import _require_manager

        assert inspect.iscoroutinefunction(_require_manager)


# ─────────────────────────────────────────────────────────────────────────────
# _require_admin guard
# ─────────────────────────────────────────────────────────────────────────────

class TestRequireAdminGuard:
    @pytest.mark.asyncio
    async def test_super_admin_passes(self):
        from routers.bi import _require_admin
        await _require_admin(_make_user("super_admin"))  # must not raise

    @pytest.mark.asyncio
    async def test_strata_manager_blocked(self):
        from routers.bi import _require_admin
        with pytest.raises(HTTPException) as exc:
            await _require_admin(_make_user("strata_manager"))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ec_member_blocked(self):
        from routers.bi import _require_admin
        with pytest.raises(HTTPException) as exc:
            await _require_admin(_make_user("ec_member"))
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# building_financial_summary endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestFinancialSummaryEndpoint:
    @pytest.mark.asyncio
    async def test_returns_envelope_with_required_fields(self):
        from routers import bi as bi_router

        mock_data = {
            "financial_year": "2026",
            "levy_collection_rate": 90.7,
            "total_levied": 4403.74,
            "total_outstanding": 408.74,
            "source": "postgres",
        }
        user = _make_user("strata_manager")

        with patch("services.bi_service.get_financial_summary", new=AsyncMock(return_value=mock_data)):
            result = await bi_router.building_financial_summary(
                building_id=BUILDING_ID,
                financial_year="2026",
                current_user=user,
            )
            assert "data" in result
            assert "building_id" in result
            assert "source" in result
            assert result["building_id"] == BUILDING_ID

    @pytest.mark.asyncio
    async def test_non_manager_gets_403(self):
        from routers import bi as bi_router

        user = _make_user("owner")
        with pytest.raises(HTTPException) as exc:
            await bi_router.building_financial_summary(
                building_id=BUILDING_ID,
                financial_year="2026",
                current_user=user,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_source_field_reflects_fallback(self):
        from routers import bi as bi_router

        mock_data = {"financial_year": "2026", "source": "mongo_fallback"}
        user = _make_user("strata_manager")

        with patch("services.bi_service.get_financial_summary", new=AsyncMock(return_value=mock_data)):
            result = await bi_router.building_financial_summary(
                building_id=BUILDING_ID,
                financial_year="2026",
                current_user=user,
            )
            assert result["source"] == "mongo_fallback"


# ─────────────────────────────────────────────────────────────────────────────
# building_arrears_hotspots endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestArrearHotspotsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        from routers import bi as bi_router

        mock_hotspots = [
            {"lot_number": "42", "total_outstanding": 150.0, "arrears_band": "61-90"},
        ]
        user = _make_user("strata_manager")

        with patch("services.bi_service.get_arrears_hotspots", new=AsyncMock(return_value=mock_hotspots)):
            result = await bi_router.building_arrears_hotspots(
                building_id=BUILDING_ID,
                min_days_overdue=0,
                current_user=user,
            )
            assert isinstance(result["data"], list)
            assert len(result["data"]) == 1

    @pytest.mark.asyncio
    async def test_owner_role_gets_403(self):
        from routers import bi as bi_router

        user = _make_user("owner")
        with pytest.raises(HTTPException) as exc:
            await bi_router.building_arrears_hotspots(
                building_id=BUILDING_ID,
                min_days_overdue=0,
                current_user=user,
            )
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# building_health_trend endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthTrendEndpoint:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        from routers import bi as bi_router

        mock_trend = [{"date": "2026-06-08", "overall_score": 82.5, "health_label": "good"}]
        user = _make_user("ec_member")

        with patch("services.bi_service.get_health_trend", new=AsyncMock(return_value=mock_trend)):
            result = await bi_router.building_health_trend(
                building_id=BUILDING_ID,
                days=90,
                current_user=user,
            )
            assert isinstance(result["data"], list)


# ─────────────────────────────────────────────────────────────────────────────
# Owner lot access control
# ─────────────────────────────────────────────────────────────────────────────

class TestOwnerLotAccessControl:
    @pytest.mark.asyncio
    async def test_owner_can_access_own_lot(self):
        from routers import bi as bi_router

        user = _make_user("owner", unit_number="42")
        mock_data = [{"financial_year": "2026", "total_charged": 4000.0}]

        with patch("services.bi_service.get_owner_levy_history", new=AsyncMock(return_value=mock_data)):
            result = await bi_router.owner_levy_history(
                owner_id="user-001",
                lot_number="42",
                building_id=BUILDING_ID,
                years=3,
                current_user=user,
            )
            assert result["data"] is not None

    @pytest.mark.asyncio
    async def test_owner_blocked_from_unassigned_building_risk_endpoint(self):
        from routers import bi as bi_router

        user = _make_user("owner", building_id="16244", unit_number="42")

        with pytest.raises(HTTPException) as exc:
            await bi_router.building_sinking_fund_risk(
                building_id=BUILDING_ID,
                current_user=user,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_owner_blocked_when_owner_id_does_not_match_token(self):
        from routers import bi as bi_router

        user = _make_user("owner", unit_number="42")

        with pytest.raises(HTTPException) as exc:
            await bi_router.owner_levy_history(
                owner_id="different-user",
                lot_number="42",
                building_id=BUILDING_ID,
                years=3,
                current_user=user,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_owner_blocked_from_different_lot(self):
        from routers import bi as bi_router

        user = _make_user("owner", unit_number="10")  # owns lot 10, not 42

        with pytest.raises(HTTPException) as exc:
            await bi_router.owner_levy_history(
                owner_id="user-001",
                lot_number="42",
                building_id=BUILDING_ID,
                years=3,
                current_user=user,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_manager_can_access_any_lot(self):
        from routers import bi as bi_router

        user = _make_user("strata_manager")  # no unit_number
        mock_data = [{"financial_year": "2026"}]

        with patch("services.bi_service.get_owner_levy_history", new=AsyncMock(return_value=mock_data)):
            result = await bi_router.owner_levy_history(
                owner_id="user-001",
                lot_number="42",
                building_id=BUILDING_ID,
                years=3,
                current_user=user,
            )
            assert result["data"] is not None

    @pytest.mark.asyncio
    async def test_elevated_owner_with_ec_member_role_can_access_any_lot(self):
        from routers import bi as bi_router

        user = _make_user("owner", unit_number="10")
        user["effective_role"] = "ec_member"  # elevated
        mock_data = [{"financial_year": "2026"}]

        with patch("services.bi_service.get_owner_levy_history", new=AsyncMock(return_value=mock_data)):
            result = await bi_router.owner_levy_history(
                owner_id="user-001",
                lot_number="42",
                building_id=BUILDING_ID,
                years=3,
                current_user=user,
            )
            assert result["data"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio endpoints — role guard via _resolve_portfolio_buildings
# ─────────────────────────────────────────────────────────────────────────────

class TestPortfolioRoleGuard:
    @pytest.mark.asyncio
    async def test_owner_blocked_from_portfolio_arrears(self):
        from routers import bi as bi_router

        user = _make_user("owner")
        with pytest.raises(HTTPException) as exc:
            await bi_router.portfolio_arrears_hotspots(
                org_id="org-001",
                current_user=user,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_tenant_blocked_from_portfolio_health(self):
        from routers import bi as bi_router

        user = _make_user("tenant")
        with pytest.raises(HTTPException) as exc:
            await bi_router.portfolio_health_ranking(
                org_id="org-001",
                current_user=user,
            )
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# building_alerts endpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildingAlertsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_alert_count_and_list(self):
        from routers import bi as bi_router

        user = _make_user("strata_manager")
        mock_alert = {"rule_code": "arrears_escalation", "severity": "high", "entity_type": "lot", "entity_id": "42"}

        with (
            patch("services.bi_service.evaluate_arrears_alerts", new=AsyncMock(return_value=[mock_alert])),
            patch("services.bi_service.evaluate_compliance_alerts", new=AsyncMock(return_value=[])),
            patch("services.bi_service.evaluate_utility_spike_alerts", new=AsyncMock(return_value=[])),
            patch("services.bi_service.evaluate_health_drop_alerts", new=AsyncMock(return_value=[])),
        ):
            result = await bi_router.building_alerts(
                building_id=BUILDING_ID,
                current_user=user,
            )
            assert result["alert_count"] == 1
            assert len(result["alerts"]) == 1
            assert result["alerts"][0]["rule_code"] == "arrears_escalation"

    @pytest.mark.asyncio
    async def test_all_clear_returns_zero_count(self):
        from routers import bi as bi_router

        user = _make_user("ec_member")
        with (
            patch("services.bi_service.evaluate_arrears_alerts", new=AsyncMock(return_value=[])),
            patch("services.bi_service.evaluate_compliance_alerts", new=AsyncMock(return_value=[])),
            patch("services.bi_service.evaluate_utility_spike_alerts", new=AsyncMock(return_value=[])),
            patch("services.bi_service.evaluate_health_drop_alerts", new=AsyncMock(return_value=[])),
        ):
            result = await bi_router.building_alerts(
                building_id=BUILDING_ID,
                current_user=user,
            )
            assert result["alert_count"] == 0

    @pytest.mark.asyncio
    async def test_alert_evaluator_exception_is_swallowed(self):
        """Exceptions in individual alert evaluators are logged but don't crash the endpoint."""
        from routers import bi as bi_router

        user = _make_user("strata_manager")
        with (
            patch("services.bi_service.evaluate_arrears_alerts", new=AsyncMock(side_effect=RuntimeError("DB down"))),
            patch("services.bi_service.evaluate_compliance_alerts", new=AsyncMock(return_value=[])),
            patch("services.bi_service.evaluate_utility_spike_alerts", new=AsyncMock(return_value=[])),
            patch("services.bi_service.evaluate_health_drop_alerts", new=AsyncMock(return_value=[])),
        ):
            # Should not raise — exceptions are caught via return_exceptions=True in gather
            result = await bi_router.building_alerts(
                building_id=BUILDING_ID,
                current_user=user,
            )
            assert "alert_count" in result
            assert result["alert_count"] == 0

    @pytest.mark.asyncio
    async def test_non_manager_gets_403(self):
        from routers import bi as bi_router

        user = _make_user("owner")
        with pytest.raises(HTTPException) as exc:
            await bi_router.building_alerts(
                building_id=BUILDING_ID,
                current_user=user,
            )
        assert exc.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Cross-building isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossBuildingIsolation:
    @pytest.mark.asyncio
    async def test_separate_building_ids_produce_separate_service_calls(self):
        from routers import bi as bi_router

        call_log: list[str] = []

        async def track_call(building_id, fy):
            call_log.append(building_id)
            return {"financial_year": fy, "source": "postgres"}

        user = _make_user("super_admin")
        with patch("services.bi_service.get_financial_summary", side_effect=track_call):
            await bi_router.building_financial_summary(building_id="13195", financial_year="2026", current_user=user)
            await bi_router.building_financial_summary(building_id="16244", financial_year="2026", current_user=user)

        assert "13195" in call_log
        assert "16244" in call_log
        assert call_log[0] != call_log[1]

    @pytest.mark.asyncio
    async def test_a_caller_asserted_building_claim_grants_nothing(self):
        """`assigned_building_ids` on the request's user mapping is not evidence.

        Before GAP-SEC-014 this router called the synchronous assert_capability(),
        which hydrated nothing — so whatever building list the user mapping happened
        to carry was tested directly. This test used to set that claim by hand and
        expect access. Hydration now REPLACES the claims it owns from
        core.user_role_assignments, so the self-asserted pair is discarded and the
        request denies.
        """
        from routers import bi as bi_router

        user = _make_user("strata_manager", building_id="99999")
        user["assigned_building_ids"] = ["13195", "16244"]

        with patch("services.bi_service.get_financial_summary", new=AsyncMock(return_value={"source": "postgres"})):
            with pytest.raises(HTTPException) as exc:
                await bi_router.building_financial_summary(
                    building_id="13195", financial_year="2026", current_user=user
                )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_building_id_propagated_in_envelope(self):
        """Envelope carries the requested building, for a VERIFIED assignment."""
        from routers import bi as bi_router

        user = _make_user("strata_manager")

        async def _verified(subject, scope, **_hydration_hints):
            return {**subject, "assigned_building_ids": ["13195", "16244"], "governance_offices": []}

        with patch(
            "services.authorisation_context.hydrate_authorisation_claims",
            new=AsyncMock(side_effect=_verified),
        ), patch("services.bi_service.get_financial_summary", new=AsyncMock(return_value={"source": "postgres"})):
            result_a = await bi_router.building_financial_summary(building_id="13195", financial_year="2026", current_user=user)
            result_b = await bi_router.building_financial_summary(building_id="16244", financial_year="2026", current_user=user)

        assert result_a["building_id"] == "13195"
        assert result_b["building_id"] == "16244"

    @pytest.mark.asyncio
    async def test_verified_assignment_does_not_reach_an_unassigned_building(self):
        from routers import bi as bi_router

        user = _make_user("strata_manager")

        async def _verified(subject, scope, **_hydration_hints):
            return {**subject, "assigned_building_ids": ["13195"], "governance_offices": []}

        with patch(
            "services.authorisation_context.hydrate_authorisation_claims",
            new=AsyncMock(side_effect=_verified),
        ), patch("services.bi_service.get_financial_summary", new=AsyncMock(return_value={"source": "postgres"})):
            with pytest.raises(HTTPException) as exc:
                await bi_router.building_financial_summary(
                    building_id="16244", financial_year="2026", current_user=user
                )
        assert exc.value.status_code == 403
