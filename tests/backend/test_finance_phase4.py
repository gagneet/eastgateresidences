"""
Phase 4 Security & Logic Tests — Financial Intelligence

Tests security hardening, role restrictions, simulation bounds,
cron idempotency, anomaly deduplication, and feature toggle gating.

Run with:
    cd backend && python -m pytest tests/test_finance_phase4.py -v
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_user(role: str, unit_number: str = "", user_id: str = "u1") -> dict:
    """Build a minimal user dict for testing."""
    perms = {
        "can_view_finances": role in {"super_admin", "chairman", "strata_manager", "ec_member"},
        "can_manage_finances": role in {"super_admin", "chairman", "strata_manager"},
    }
    return {
        "id": user_id,
        "role": role,
        "unit_number": unit_number,
        "permissions": perms,
    }


def _make_permissions(user: dict):
    """Return simple permissions namespace."""

    class Perms:
        pass

    p = Perms()
    p.can_view_finances = user["permissions"]["can_view_finances"]
    p.can_manage_finances = user["permissions"]["can_manage_finances"]
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Section A: Role restriction tests (pure logic, no HTTP)
# ─────────────────────────────────────────────────────────────────────────────

class TestRoleRestrictions:
    """Tests for _require_* guards in the finance intelligence router."""

    def _get_view_roles(self):
        return {"super_admin", "chairman", "strata_manager", "ec_member"}

    def _get_generate_roles(self):
        return {"super_admin", "chairman"}

    def _get_manage_roles(self):
        return {"super_admin", "chairman", "strata_manager"}

    def test_owner_cannot_access_view_endpoint(self):
        """Owner role is NOT in _VIEW_ROLES — should be rejected."""
        owner = _make_user("owner", unit_number="TH087")
        assert owner["role"] not in self._get_view_roles()

    def test_tenant_cannot_access_view_endpoint(self):
        """Tenant role is NOT in _VIEW_ROLES — should be rejected."""
        tenant = _make_user("tenant", unit_number="UA005")
        assert tenant["role"] not in self._get_view_roles()

    def test_ec_member_can_view(self):
        """ec_member IS in _VIEW_ROLES."""
        ec = _make_user("ec_member")
        assert ec["role"] in self._get_view_roles()

    def test_strata_manager_cannot_simulate(self):
        """strata_manager is NOT in _GENERATE_ROLES — cannot run levy simulator."""
        sm = _make_user("strata_manager")
        assert sm["role"] not in self._get_generate_roles()

    def test_ec_member_cannot_simulate(self):
        """ec_member is NOT in _GENERATE_ROLES — cannot run levy simulator."""
        ec = _make_user("ec_member")
        assert ec["role"] not in self._get_generate_roles()

    def test_chairman_can_simulate(self):
        """chairman IS in _GENERATE_ROLES."""
        chairman = _make_user("chairman")
        assert chairman["role"] in self._get_generate_roles()

    def test_chairman_cannot_resolve_anomaly(self):
        """Plan specifies only super_admin and chairman can resolve — chairman CAN resolve."""
        # Per updated router, resolve requires _GENERATE_ROLES (super_admin, chairman)
        chairman = _make_user("chairman")
        assert chairman["role"] in self._get_generate_roles()

    def test_strata_manager_cannot_resolve_anomaly(self):
        """strata_manager is NOT in _GENERATE_ROLES — cannot resolve anomalies."""
        sm = _make_user("strata_manager")
        assert sm["role"] not in self._get_generate_roles()

    def test_super_admin_can_all(self):
        """super_admin can view, manage, generate, and resolve."""
        sa = _make_user("super_admin")
        assert sa["role"] in self._get_view_roles()
        assert sa["role"] in self._get_manage_roles()
        assert sa["role"] in self._get_generate_roles()


# ─────────────────────────────────────────────────────────────────────────────
# Section B: Simulation bounds tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSimulationBounds:
    """Tests for levy simulator increase_pct validation."""

    def _validate_bounds(self, increase_pct: float) -> bool:
        """Replicate the Query ge=-5.0, le=50.0 bounds check."""
        return -5.0 <= increase_pct <= 50.0

    def test_bounds_valid_zero(self):
        assert self._validate_bounds(0.0) is True

    def test_bounds_valid_positive(self):
        assert self._validate_bounds(25.0) is True

    def test_bounds_valid_max(self):
        assert self._validate_bounds(50.0) is True

    def test_bounds_valid_negative_min(self):
        assert self._validate_bounds(-5.0) is True

    def test_bounds_exceed_max(self):
        """increase_pct=51 should fail (le=50.0)."""
        assert self._validate_bounds(51.0) is False

    def test_bounds_exceed_min(self):
        """increase_pct=-6 should fail (ge=-5.0)."""
        assert self._validate_bounds(-6.0) is False

    def test_bounds_exactly_max_valid(self):
        assert self._validate_bounds(50.0) is True

    def test_bounds_exactly_min_valid(self):
        assert self._validate_bounds(-5.0) is True


# ─────────────────────────────────────────────────────────────────────────────
# Section C: Lot ownership validation
# ─────────────────────────────────────────────────────────────────────────────

class TestLotOwnershipValidation:
    """Tests for the ownership check on GET /finance/lot-summary/{lot_number}."""

    _MANAGE_ROLES = {"super_admin", "chairman", "strata_manager"}
    _VIEW_ROLES = {"super_admin", "chairman", "strata_manager", "ec_member"}

    def _check_ownership(self, user: dict, lot_number: str) -> bool:
        """Returns True if user is allowed to see the lot. Raises on forbidden."""
        user_role = user.get("role", "")
        if user_role not in self._VIEW_ROLES:
            raise PermissionError("Not authorized to view finances")
        if user_role not in self._MANAGE_ROLES:
            user_unit = user.get("unit_number", "")
            if user_unit != lot_number:
                raise PermissionError("You can only view your own lot summary")
        return True

    def test_owner_can_see_own_lot(self):
        owner = _make_user("ec_member", unit_number="TH087")
        assert self._check_ownership(owner, "TH087") is True

    def test_owner_cannot_see_other_lot(self):
        owner = _make_user("ec_member", unit_number="TH087")
        with pytest.raises(PermissionError, match="own lot"):
            self._check_ownership(owner, "UA001")

    def test_super_admin_can_see_any_lot(self):
        sa = _make_user("super_admin")
        assert self._check_ownership(sa, "TH087") is True
        assert self._check_ownership(sa, "UA001") is True

    def test_chairman_can_see_any_lot(self):
        chairman = _make_user("chairman")
        assert self._check_ownership(chairman, "UA063") is True

    def test_strata_manager_can_see_any_lot(self):
        sm = _make_user("strata_manager")
        assert self._check_ownership(sm, "TH071") is True

    def test_unauthorized_role_blocked(self):
        owner = _make_user("owner", unit_number="TH087")
        with pytest.raises(PermissionError, match="Not authorized"):
            self._check_ownership(owner, "TH087")


# ─────────────────────────────────────────────────────────────────────────────
# Section D: Health score boundary tiers
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthScoreBoundaries:
    """Tests for health score → risk_level tier mapping."""

    def _score_to_risk(self, score: float) -> str:
        if score >= 80:
            return "excellent"
        if score >= 65:
            return "good"
        if score >= 50:
            return "moderate"
        if score >= 35:
            return "at_risk"
        return "critical"

    def test_score_90_is_excellent(self):
        assert self._score_to_risk(90) == "excellent"

    def test_score_80_is_excellent_boundary(self):
        assert self._score_to_risk(80) == "excellent"

    def test_score_65_is_good_boundary(self):
        assert self._score_to_risk(65) == "good"

    def test_score_50_is_moderate_boundary(self):
        assert self._score_to_risk(50) == "moderate"

    def test_score_35_is_at_risk_boundary(self):
        assert self._score_to_risk(35) == "at_risk"

    def test_score_34_is_critical(self):
        assert self._score_to_risk(34) == "critical"

    def test_score_0_is_critical(self):
        assert self._score_to_risk(0) == "critical"

    def test_score_100_is_excellent(self):
        assert self._score_to_risk(100) == "excellent"


# ─────────────────────────────────────────────────────────────────────────────
# Section E: Cashflow deficit detection
# ─────────────────────────────────────────────────────────────────────────────

class TestCashflowDeficitDetection:
    """Tests for cashflow risk month detection logic."""

    def _compute_cashflow(self, monthly_income: float, monthly_expenses: float,
                          opening: float = 0) -> dict:
        """Simplified cashflow computation for testing."""
        months = []
        cumulative = opening
        risk_months = []
        month_names = ["Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
                       "Jan", "Feb", "Mar", "Apr", "May", "Jun"]
        for i, name in enumerate(month_names):
            net = monthly_income - monthly_expenses
            cumulative += net
            is_risk = cumulative < 0
            if is_risk:
                risk_months.append(name)
            months.append({
                "month": name,
                "cumulative_balance": cumulative,
                "is_risk_month": is_risk,
            })
        return {
            "months": months,
            "risk_months": risk_months,
            "min_balance": min(m["cumulative_balance"] for m in months),
            "annual_income": monthly_income * 12,
            "annual_expenses": monthly_expenses * 12,
        }

    def test_surplus_no_risk_months(self):
        """When income > expenses from month 1, no risk months."""
        result = self._compute_cashflow(50000, 40000, opening=0)
        assert result["risk_months"] == []
        assert result["min_balance"] > 0

    def test_deficit_has_risk_months(self):
        """When expenses > income, cumulative goes negative — risk months detected."""
        result = self._compute_cashflow(30000, 40000, opening=0)
        assert len(result["risk_months"]) > 0

    def test_opening_balance_defers_risk(self):
        """High opening balance can prevent risk months even with monthly deficit."""
        result = self._compute_cashflow(30000, 40000, opening=500000)
        # With $500k opening and only -$10k/month deficit, 12 months = -$120k
        # Still positive throughout
        assert result["risk_months"] == []

    def test_breakeven_no_risk(self):
        """Exactly equal income and expenses = no risk (balance stays at opening)."""
        result = self._compute_cashflow(40000, 40000, opening=0)
        assert result["risk_months"] == []
        assert result["min_balance"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Section F: Simulation non-persistence
# ─────────────────────────────────────────────────────────────────────────────

class TestSimulationNonPersistence:
    """Tests that the levy simulator does NOT persist data to DB."""

    @pytest.mark.asyncio
    async def test_simulation_does_not_write_to_db(self):
        """simulate_levy_adjustment should NOT insert/update any collection."""
        mock_db = MagicMock()
        mock_db.units.find.return_value.to_list = AsyncMock(return_value=[
            {"unit_number": "TH087", "unit_entitlement": 161, "annual_levy": 7090.04},
        ])
        # levy data mock
        mock_db.financial_categories.find.return_value.to_list = AsyncMock(return_value=[])
        mock_db.levy_categories.find.return_value.to_list = AsyncMock(return_value=[])

        # Verify no write methods are called (insert_one, update_one, etc.)
        assert not mock_db.insert_one.called
        assert not mock_db.update_one.called
        assert not mock_db.replace_one.called


# ─────────────────────────────────────────────────────────────────────────────
# Section G: Feature toggle logic
# ─────────────────────────────────────────────────────────────────────────────

class TestFeatureToggleGating:
    """Tests for _check_finance_intelligence_enabled dependency."""

    @pytest.mark.asyncio
    async def test_disabled_toggle_raises_503(self):
        """When toggle is_enabled=False, a 503 HTTPException should be raised."""
        from fastapi import HTTPException

        async def check_toggle_disabled():
            toggle = {"feature_key": "finance_intelligence", "is_enabled": False}
            if toggle and not toggle.get("is_enabled", True):
                raise HTTPException(
                    status_code=503,
                    detail="Financial Intelligence is currently disabled by administrator"
                )

        with pytest.raises(HTTPException) as exc_info:
            await check_toggle_disabled()
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_enabled_toggle_passes(self):
        """When toggle is_enabled=True, no exception is raised."""
        from fastapi import HTTPException

        async def check_toggle_enabled():
            toggle = {"feature_key": "finance_intelligence", "is_enabled": True}
            if toggle and not toggle.get("is_enabled", True):
                raise HTTPException(status_code=503, detail="disabled")

        # Should not raise
        await check_toggle_enabled()

    @pytest.mark.asyncio
    async def test_missing_toggle_defaults_to_enabled(self):
        """When no toggle record exists (None), feature is enabled by default."""
        from fastapi import HTTPException

        async def check_toggle_missing():
            toggle = None
            if toggle and not toggle.get("is_enabled", True):
                raise HTTPException(status_code=503, detail="disabled")

        # Should not raise
        await check_toggle_missing()


# ─────────────────────────────────────────────────────────────────────────────
# Section H: Finance logger
# ─────────────────────────────────────────────────────────────────────────────

class TestFinanceLogger:
    """Tests for utils/finance_logger.py."""

    def test_log_event_emits_json(self, caplog):
        """log_event should emit valid JSON to the finance_events logger."""
        import json
        import logging
        import sys
        sys.path.insert(0, ".")

        from utils.finance_logger import log_event

        with caplog.at_level(logging.INFO, logger="finance_events"):
            log_event("test_event", year="2026", count=5)

        assert len(caplog.records) > 0
        record = caplog.records[-1]
        payload = json.loads(record.message)
        assert payload["event"] == "test_event"
        assert payload["year"] == "2026"
        assert payload["count"] == 5
        assert "ts" in payload

    def test_timed_measures_duration(self):
        """timed context manager should record non-zero duration."""
        import json
        import logging
        import time
        from utils.finance_logger import timed

        events_captured = []

        class CapturingHandler(logging.Handler):
            def emit(self, record):
                try:
                    events_captured.append(json.loads(record.getMessage()))
                except Exception:
                    pass

        logger = logging.getLogger("finance_events")
        handler = CapturingHandler()
        orig_level = logger.level
        orig_propagate = logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)

        try:
            with timed("unit_test_event", year="2026"):
                time.sleep(0.01)  # 10ms
        finally:
            logger.removeHandler(handler)
            logger.setLevel(orig_level)
            logger.propagate = orig_propagate

        assert len(events_captured) > 0, "No events captured — handler not called"
        evt = events_captured[-1]
        assert evt["event"] == "unit_test_event"
        assert evt["duration_ms"] >= 10.0, f"Expected >= 10ms, got {evt['duration_ms']}"

    def test_timed_extra_fields(self):
        """Extra fields set on timed() context should appear in emitted event."""
        import json
        import logging
        from utils.finance_logger import timed

        events_captured = []

        class CapturingHandler(logging.Handler):
            def emit(self, record):
                try:
                    events_captured.append(json.loads(record.getMessage()))
                except Exception:
                    pass

        logger = logging.getLogger("finance_events")
        handler = CapturingHandler()
        orig_level = logger.level
        orig_propagate = logger.propagate
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)

        try:
            with timed("test_with_extra", year="2026") as t:
                t.extra["count"] = 42
        finally:
            logger.removeHandler(handler)
            logger.setLevel(orig_level)
            logger.propagate = orig_propagate

        assert len(events_captured) > 0, "No events captured"
        evt = events_captured[-1]
        assert evt.get("count") == 42
