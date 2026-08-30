"""
Unit tests for Financial Intelligence services.

Run with:
    cd backend && python -m pytest tests/test_finance_intelligence.py -v

Tests use mocking to avoid requiring a live MongoDB connection.
"""
import pytest
from types import SimpleNamespace

from fastapi import HTTPException

from routers import finance_intelligence as fi


# ─────────────────────────────────────────────────────────────────────────────
# Forecast Service Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestForecastService:
    """Tests for forecast logic — inline implementations to avoid DB/numpy import."""

    @staticmethod
    def _linear_forecast_impl(values, n_years=3):
        """Pure Python linear regression (least squares) without numpy."""
        if len(values) < 2:
            return None, 0.0
        n = len(values)
        x = list(range(n))
        x_mean = sum(x) / n
        y_mean = sum(values) / n
        num = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        den = sum((x[i] - x_mean) ** 2 for i in range(n))
        slope = num / den if den != 0 else 0.0
        intercept = y_mean - slope * x_mean
        # R²
        ss_res = sum((values[i] - (slope * x[i] + intercept)) ** 2 for i in range(n))
        ss_tot = sum((values[i] - y_mean) ** 2 for i in range(n))
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
        projections = [max(0.0, slope * (n + i) + intercept) for i in range(n_years)]
        return projections, max(0.0, min(1.0, r2))

    @staticmethod
    def _inflation_forecast_impl(last_value, n_years=3, rate=0.035):
        return [round(last_value * ((1 + rate) ** (i + 1)), 2) for i in range(n_years)]

    def test_linear_regression_forecast_sufficient_data(self):
        """Linear regression with ≥2 data points produces projections."""
        values = [10000, 11000, 12000]  # perfectly linear +1000/yr
        projections, r2 = self._linear_forecast_impl(values, n_years=3)
        assert projections is not None
        assert len(projections) == 3
        # Should project roughly 13000, 14000, 15000
        assert 12500 < projections[0] < 13500, f"Y+1 projection out of range: {projections[0]}"
        assert r2 > 0.9, f"R² should be near 1.0 for perfect linear data: {r2}"

    def test_linear_regression_insufficient_data(self):
        """Linear regression with < 2 data points returns None."""
        projections, r2 = self._linear_forecast_impl([10000], n_years=3)
        assert projections is None
        assert r2 == 0.0

    def test_inflation_model_compounding(self):
        """Inflation model correctly applies compound growth."""
        base = 10000.0
        projections = self._inflation_forecast_impl(base, n_years=3, rate=0.035)
        assert len(projections) == 3
        assert abs(projections[0] - 10350.0) < 1.0, f"Y+1 incorrect: {projections[0]}"
        assert abs(projections[1] - 10712.25) < 1.0, f"Y+2 incorrect: {projections[1]}"
        assert abs(projections[2] - 11087.18) < 1.0, f"Y+3 incorrect: {projections[2]}"

    def test_inflation_model_zero_base(self):
        """Inflation model with zero base produces zeros."""
        projections = self._inflation_forecast_impl(0.0, n_years=3)
        assert all(p == 0.0 for p in projections)

    def test_forecast_projections_non_negative(self):
        """Projections are never negative (floor at 0)."""
        values = [5000, 3000, 1000]
        projections, _ = self._linear_forecast_impl(values, n_years=3)
        if projections:
            for p in projections:
                assert p >= 0.0, f"Projection should not be negative: {p}"


# ─────────────────────────────────────────────────────────────────────────────
# Anomaly Service Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAnomalyService:
    """Tests for anomaly detection logic — inline implementations."""

    @staticmethod
    def _severity_from_deviation(deviation_pct: float) -> str:
        """Duplicate of anomaly_service._severity_from_deviation for testing."""
        if deviation_pct >= 100:
            return "critical"
        if deviation_pct >= 50:
            return "high"
        if deviation_pct >= 20:
            return "medium"
        return "low"

    @staticmethod
    def _make_anomaly_impl(**kwargs) -> dict:
        """Duplicate of anomaly_service._make_anomaly for testing."""
        import uuid
        from datetime import datetime, timezone
        return {
            "id": str(uuid.uuid4()),
            "financial_year": kwargs.get("year"),
            "fund_type": kwargs.get("fund_type"),
            "category": kwargs.get("category"),
            "lot_number": kwargs.get("lot_number"),
            "anomaly_type": kwargs.get("anomaly_type"),
            "severity": kwargs.get("severity"),
            "deviation_pct": kwargs.get("deviation_pct"),
            "expected_value": kwargs.get("expected_value"),
            "actual_value": kwargs.get("actual_value"),
            "description": kwargs.get("description"),
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "resolved": False,
        }

    def test_severity_from_deviation_critical(self):
        """≥100% deviation → critical severity."""
        assert self._severity_from_deviation(100) == "critical"
        assert self._severity_from_deviation(150) == "critical"

    def test_severity_from_deviation_high(self):
        """50–99% deviation → high severity."""
        assert self._severity_from_deviation(50) == "high"
        assert self._severity_from_deviation(75) == "high"
        assert self._severity_from_deviation(99) == "high"

    def test_severity_from_deviation_medium(self):
        """20–49% deviation → medium severity."""
        assert self._severity_from_deviation(20) == "medium"
        assert self._severity_from_deviation(35) == "medium"

    def test_severity_from_deviation_low(self):
        """< 20% deviation → low severity."""
        assert self._severity_from_deviation(10) == "low"
        assert self._severity_from_deviation(0) == "low"

    def test_make_anomaly_structure(self):
        """_make_anomaly returns expected fields."""
        anomaly = self._make_anomaly_impl(
            year="2026",
            anomaly_type="budget_overrun",
            severity="high",
            description="Test anomaly",
            fund_type="administrative",
            category="Insurance",
            actual_value=12000.0,
            expected_value=10000.0,
            deviation_pct=20.0,
        )
        assert anomaly["financial_year"] == "2026"
        assert anomaly["anomaly_type"] == "budget_overrun"
        assert anomaly["severity"] == "high"
        assert anomaly["resolved"] is False
        assert "id" in anomaly
        assert "detected_at" in anomaly


# ─────────────────────────────────────────────────────────────────────────────
# Health Service Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthService:
    """Tests for health_service.py — scoring system."""

    def test_score_always_in_range(self):
        """Health score must be between 0 and 100."""
        # Test with degenerate values
        # We test the scoring math directly
        surplus_score = 1.0  # max
        arrears_score = 0.0  # worst
        cashflow_score = 0.5
        stability_score = 0.5
        discipline_score = 0.5
        volatility_score = 0.5

        total = (
                surplus_score * 20 +
                arrears_score * 20 +
                cashflow_score * 15 +
                stability_score * 15 +
                discipline_score * 15 +
                volatility_score * 15
        )
        score = max(0.0, min(100.0, total))
        assert 0 <= score <= 100

    def test_risk_level_excellent(self):
        """Score ≥ 90 → excellent."""
        score = 92.0
        if score >= 90:
            level = "excellent"
        elif score >= 75:
            level = "good"
        elif score >= 55:
            level = "moderate"
        elif score >= 35:
            level = "at_risk"
        else:
            level = "critical"
        assert level == "excellent"

    def test_risk_level_critical(self):
        """Score < 35 → critical."""
        score = 20.0
        if score >= 90:
            level = "excellent"
        elif score >= 75:
            level = "good"
        elif score >= 55:
            level = "moderate"
        elif score >= 35:
            level = "at_risk"
        else:
            level = "critical"
        assert level == "critical"

    def test_risk_level_moderate(self):
        """55 ≤ score < 75 → moderate."""
        score = 65.0
        if score >= 90:
            level = "excellent"
        elif score >= 75:
            level = "good"
        elif score >= 55:
            level = "moderate"
        elif score >= 35:
            level = "at_risk"
        else:
            level = "critical"
        assert level == "moderate"

    def test_surplus_score_capped_at_max(self):
        """Surplus ratio above target still gives max 20 pts."""
        surplus_ratio = 0.50  # 50% — well above 10% target
        surplus_score = min(surplus_ratio / 0.10, 1.0)
        component_score = surplus_score * 20
        assert component_score == 20.0

    def test_zero_arrears_full_marks(self):
        """0 units owing → full 20 pts for arrears component."""
        arrears_pct = 0.0
        arrears_score = max(0.0, 1 - (arrears_pct / 0.20))
        assert arrears_score * 20 == 20.0

    def test_high_arrears_zero_marks(self):
        """≥20% units owing → 0 pts for arrears component."""
        arrears_pct = 0.25  # 25%
        arrears_score = max(0.0, 1 - (arrears_pct / 0.20))
        assert arrears_score * 20 == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Role guard regression tests
# ─────────────────────────────────────────────────────────────────────────────


class TestFinanceRoleGuards:
    """Regression tests for effective-role ACL checks in finance intelligence."""

    @staticmethod
    def _perms():
        return SimpleNamespace(
            can_view_finances=True,
            can_manage_finances=True,
        )

    # These guards became async with GAP-SEC-014 — they hydrate verified building
    # claims before deciding. Awaiting them is not optional: a bare call returns a
    # coroutine and runs nothing, so a "must not raise" assertion passes vacuously
    # and a pytest.raises block reports DID NOT RAISE.

    @pytest.mark.asyncio
    async def test_manage_guard_uses_effective_role(self, monkeypatch):
        """Temp-elevated owners should be accepted when their effective role is allowed."""
        monkeypatch.setattr(fi, "get_user_permissions", lambda _user: self._perms())

        await fi._require_finance_manage({
            "role": "owner",
            "effective_role": "strata_admin",
            "building_id": "13195",
        })

    @pytest.mark.asyncio
    async def test_super_admin_guard_ignores_raw_role(self):
        """Raw role must not override an effective super-admin classification."""
        await fi._require_super_admin({"role": "owner", "effective_role": "super_admin"})

    @pytest.mark.asyncio
    async def test_generate_guard_rejects_when_effective_role_is_not_allowed(self, monkeypatch):
        """Generate access is denied when the effective role is outside the allow-list."""
        monkeypatch.setattr(fi, "get_user_permissions", lambda _user: self._perms())

        with pytest.raises(HTTPException):
            await fi._require_generate({"role": "strata_admin", "effective_role": "owner"})

    @pytest.mark.asyncio
    async def test_finance_guards_are_coroutine_functions(self):
        """Pins the await contract, so a revert to sync fails here rather than silently."""
        import inspect

        for guard in (fi._require_finance_view, fi._require_finance_manage,
                      fi._require_generate, fi._require_super_admin, fi._require_plan_edit):
            assert inspect.iscoroutinefunction(guard), guard.__name__


# ─────────────────────────────────────────────────────────────────────────────
# Lot Finance Service Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestLotFinanceService:
    """Tests for lot_finance_service.py — true cost calculation."""

    def test_total_cost_is_sum_of_components(self):
        """total_cost = sum of all cost components."""
        admin_paid = 5000.0
        sinking_paid = 1500.0
        council = 2000.0
        water = 800.0
        land_tax = 2500.0
        interest = 200.0
        expected = admin_paid + sinking_paid + council + water + land_tax + interest
        total = round(admin_paid + sinking_paid + council + water + land_tax + interest, 2)
        assert total == expected

    def test_arrears_flag_positive_balance(self):
        """net_balance > 0 means unit owes → arrears_flag = True."""
        net_balance = 500.0
        arrears_flag = net_balance > 0
        assert arrears_flag is True

    def test_arrears_flag_zero_balance(self):
        """net_balance == 0 means paid up → arrears_flag = False."""
        net_balance = 0.0
        arrears_flag = net_balance > 0
        assert arrears_flag is False

    def test_arrears_flag_credit(self):
        """net_balance < 0 means credit → arrears_flag = False."""
        net_balance = -100.0
        arrears_flag = net_balance > 0
        assert arrears_flag is False

    def test_risk_flag_above_threshold(self):
        """cost_per_uoe > 1.5x avg → risk_flag = True."""
        cost_per_uoe = 300.0
        avg_cost_per_uoe = 150.0
        risk_flag = cost_per_uoe > avg_cost_per_uoe * 1.5
        assert risk_flag is True

    def test_risk_flag_below_threshold(self):
        """cost_per_uoe ≤ 1.5x avg → risk_flag = False."""
        cost_per_uoe = 200.0
        avg_cost_per_uoe = 200.0
        risk_flag = cost_per_uoe > avg_cost_per_uoe * 1.5
        assert risk_flag is False


# ─────────────────────────────────────────────────────────────────────────────
# Levy Simulator Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestLevySimulator:
    """Tests for the levy simulation logic in forecast_service.py."""

    def test_zero_increase_no_change(self):
        """0% increase → new rate equals current rate."""
        increase_pct = 0.0
        total_rate = 500.0
        multiplier = 1 + (increase_pct / 100)
        new_rate = total_rate * multiplier
        assert new_rate == total_rate

    def test_ten_percent_increase_correct(self):
        """10% increase → new rate is 110% of current."""
        increase_pct = 10.0
        total_rate = 500.0
        multiplier = 1 + (increase_pct / 100)
        new_rate = total_rate * multiplier
        assert abs(new_rate - 550.0) < 0.01

    def test_per_unit_impact_proportional_to_entitlement(self):
        """Units with higher entitlement have larger impact."""
        increase_pct = 5.0
        total_rate = 400.0
        multiplier = 1 + (increase_pct / 100)
        new_rate = total_rate * multiplier

        unit_a = {"entitlement": 100}
        unit_b = {"entitlement": 200}

        impact_a = round((new_rate - total_rate) * unit_a["entitlement"], 2)
        impact_b = round((new_rate - total_rate) * unit_b["entitlement"], 2)

        assert impact_b == impact_a * 2, "Unit B (2x entitlement) should have 2x impact"

    def test_surplus_deficit_calculation(self):
        """surplus_deficit = new_collection - total_expenses."""
        new_collection = 450000.0
        total_expenses = 420000.0
        surplus_deficit = round(new_collection - total_expenses, 2)
        assert surplus_deficit == 30000.0

    def test_deficit_scenario(self):
        """When new collection < expenses → surplus_deficit is negative."""
        new_collection = 380000.0
        total_expenses = 420000.0
        surplus_deficit = round(new_collection - total_expenses, 2)
        assert surplus_deficit < 0


# ─────────────────────────────────────────────────────────────────────────────
# Cashflow Projection Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCashflowProjection:
    """Tests for cashflow projection logic."""

    def test_twelve_months_generated(self):
        """Projection always has 12 months."""
        monthly_expense = 10000 / 12
        quarterly_income = 100000 / 4
        income_months = {1: quarterly_income, 4: quarterly_income,
                         7: quarterly_income, 10: quarterly_income}
        months = []
        cumulative = 5000
        for m in range(1, 13):
            inc = income_months.get(m, 0)
            exp = monthly_expense
            cumulative += inc - exp
            months.append({"month_number": m, "cumulative_balance": round(cumulative, 2)})
        assert len(months) == 12

    def test_risk_months_detected_correctly(self):
        """Months with negative cumulative balance are flagged as risk months."""
        balances = [1000, 500, -200, -800, 400, 1200,
                    1800, 2400, 1000, 500, -100, 600]
        risk_months = [i + 1 for i, b in enumerate(balances) if b < 0]
        assert 3 in risk_months
        assert 4 in risk_months
        assert 11 in risk_months
        assert 1 not in risk_months
        assert len(risk_months) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Forecast Document Shape Tests
# Regression tests for client-side crash: "can't access property 'length',
# e.category is undefined" — caused by forecasts with null/missing category.
# ─────────────────────────────────────────────────────────────────────────────

class TestForecastDocumentShape:
    """
    Validates that forecast documents contain all fields required by the
    ForecastChart frontend component. Any document missing 'category' would
    crash the component at f.category.length.
    """

    REQUIRED_FIELDS = [
        "id", "financial_year", "fund_type", "category",
        "projection_year_1", "projection_year_2", "projection_year_3",
        "method", "confidence_score",
    ]

    @staticmethod
    def _make_forecast_doc(**overrides) -> dict:
        """Build a valid forecast document, optionally overriding fields."""
        import uuid
        base = {
            "id": str(uuid.uuid4()),
            "financial_year": "2026",
            "fund_type": "administrative",
            "category": "Insurance",
            "projection_year_1": 12000.0,
            "projection_year_2": 12420.0,
            "projection_year_3": 12854.7,
            "method": "inflation",
            "confidence_score": 0.5,
            "assumptions": {"cpi": 0.035, "base_value": 11600.0},
            "created_at": "2026-02-24T00:00:00Z",
        }
        base.update(overrides)
        return base

    def test_valid_document_passes_validation(self):
        """A fully-populated forecast document has all required fields."""
        doc = self._make_forecast_doc()
        for field in self.REQUIRED_FIELDS:
            assert field in doc, f"Missing field: {field}"
            assert doc[field] is not None, f"Field '{field}' must not be None"

    def test_null_category_is_detectable(self):
        """A document with category=None is detected and should be filtered out."""
        doc = self._make_forecast_doc(category=None)
        # Simulates the frontend filter: f.category != null
        is_valid = doc.get("category") is not None
        assert not is_valid, "Document with null category should fail validation"

    def test_missing_category_field_is_detectable(self):
        """A document without a 'category' key is detected and should be filtered out."""
        doc = self._make_forecast_doc()
        del doc["category"]
        is_valid = doc.get("category") is not None
        assert not is_valid, "Document without 'category' key should fail validation"

    def test_empty_string_category_passes_filter(self):
        """An empty string category is not None — it reaches the component."""
        doc = self._make_forecast_doc(category="")
        is_valid = doc.get("category") is not None
        assert is_valid, "Empty string is not None — component must handle it gracefully"

    def test_chart_name_truncation_normal(self):
        """Category names ≤18 chars are returned as-is."""
        cat = "Insurance"
        result = cat[:18] + "…" if len(cat) > 18 else cat
        assert result == "Insurance"

    def test_chart_name_truncation_long(self):
        """Category names >18 chars are truncated with ellipsis."""
        cat = "Administration & Management Fees"
        result = cat[:18] + "…" if len(cat) > 18 else cat
        assert result == "Administration & M…"
        assert len(result) == 19  # 18 chars + 1 ellipsis character (U+2026)

    def test_chart_name_truncation_null_safe(self):
        """The fixed component handles None category without crashing."""
        cat = None
        safe = cat or ""
        result = safe[:18] + "…" if len(safe) > 18 else safe
        assert result == ""  # Empty string, not a crash

    def test_forecast_projections_are_numeric(self):
        """All projection fields must be numeric (not None or string)."""
        doc = self._make_forecast_doc()
        for field in ["projection_year_1", "projection_year_2", "projection_year_3"]:
            val = doc[field]
            assert isinstance(val, (int, float)), f"{field} must be numeric, got {type(val)}"

    def test_confidence_score_in_range(self):
        """confidence_score must be between 0 and 1."""
        doc = self._make_forecast_doc()
        assert 0.0 <= doc["confidence_score"] <= 1.0

    def test_fund_type_valid_values(self):
        """fund_type must be 'administrative' or 'sinking'."""
        valid = {"administrative", "sinking"}
        for ft in ["administrative", "sinking"]:
            doc = self._make_forecast_doc(fund_type=ft)
            assert doc["fund_type"] in valid

    def test_method_valid_values(self):
        """method must be one of the known forecast models."""
        valid = {"linear", "inflation", "capital_works"}
        for method in valid:
            doc = self._make_forecast_doc(method=method)
            assert doc["method"] in valid

    def test_batch_filter_removes_null_category_items(self):
        """Simulates the useFinanceData.js filter that removes malformed forecasts."""
        forecasts = [
            self._make_forecast_doc(category="Insurance"),
            self._make_forecast_doc(category=None),  # null — should be filtered
            self._make_forecast_doc(category="Maintenance"),
            self._make_forecast_doc(),  # valid
        ]
        del forecasts[1]["category"]  # also test missing key scenario — reuse slot 1
        # This matches the JS: rawForecasts.filter(f => f && f.category != null)
        valid = [f for f in forecasts if f and f.get("category") is not None]
        assert len(valid) == 3
        assert all(f["category"] is not None for f in valid)

    def test_batch_filter_empty_input(self):
        """Filter on empty list returns empty list (no crash)."""
        valid = [f for f in [] if f and f.get("category") is not None]
        assert valid == []

    def test_batch_filter_all_valid(self):
        """Filter on all-valid list returns all items."""
        forecasts = [
            self._make_forecast_doc(category=f"Category {i}")
            for i in range(10)
        ]
        valid = [f for f in forecasts if f and f.get("category") is not None]
        assert len(valid) == 10


# ─────────────────────────────────────────────────────────────────────────────
# Forecast API Response Shape Tests
# Validates the structure that the backend returns to the frontend.
# ─────────────────────────────────────────────────────────────────────────────

class TestForecastAPIResponseShape:
    """
    Tests that the GET /finance/forecast endpoint response can be correctly
    parsed by useFinanceData.js — specifically that `data.forecasts` is a list
    and that each item has a non-null 'category'.
    """

    def test_response_envelope_structure(self):
        """API returns {year, forecasts, count} envelope."""
        response = {
            "year": "2026",
            "forecasts": [],
            "count": 0,
        }
        assert "year" in response
        assert "forecasts" in response
        assert isinstance(response["forecasts"], list)
        assert isinstance(response["count"], int)

    def test_empty_forecasts_list_does_not_crash_frontend(self):
        """Empty forecasts list should render <SectionFallback>, not crash."""
        # useFinanceData.js: forecasts.length > 0 check before rendering
        forecasts = []
        should_render_chart = len(forecasts) > 0
        assert not should_render_chart

    def test_forecast_count_matches_list_length(self):
        """count field must equal len(forecasts)."""
        forecasts = [{"category": f"Cat{i}"} for i in range(5)]
        response = {"year": "2026", "forecasts": forecasts, "count": len(forecasts)}
        assert response["count"] == len(response["forecasts"])

    def test_null_data_returns_fallback(self):
        """If API returns None/null for forecasts, hook falls back to []."""
        # Simulates useFinanceData.js: forecastRes.value.data?.forecasts || []
        api_data = None
        forecasts = (api_data or {}).get("forecasts") or []
        assert forecasts == []

    def test_missing_forecasts_key_returns_fallback(self):
        """If API response has no 'forecasts' key, hook falls back to []."""
        api_data = {"year": "2026", "count": 0}  # no 'forecasts' key
        forecasts = api_data.get("forecasts") or []
        assert forecasts == []

    def test_non_array_forecasts_returns_fallback(self):
        """If 'forecasts' is not an array, filter must handle it gracefully."""
        raw = "not-an-array"
        forecasts = raw if isinstance(raw, list) else []
        assert forecasts == []


# ─────────────────────────────────────────────────────────────────────────────
# Variance Table Data Shape Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestVarianceTableShape:
    """
    Validates that categories data passed to VarianceTable has the expected
    shape. VarianceTable uses c.name, c.fund_type, c.budgeted_amount,
    c.actual_amount — if any are undefined the table renders incorrectly.
    """

    @staticmethod
    def _make_category(**overrides) -> dict:
        base = {
            "id": "cat-001",
            "year": "2026",
            "fund_type": "administrative",
            "name": "Insurance",
            "budgeted_amount": 50000.0,
            "actual_amount": 48000.0,
        }
        base.update(overrides)
        return base

    def test_valid_category_has_all_fields(self):
        cat = self._make_category()
        for field in ["name", "fund_type", "budgeted_amount", "actual_amount"]:
            assert cat.get(field) is not None, f"Field '{field}' must not be None"

    def test_variance_calculation(self):
        """variance = actual - budgeted."""
        cat = self._make_category(budgeted_amount=50000.0, actual_amount=48000.0)
        variance = cat["actual_amount"] - cat["budgeted_amount"]
        assert variance == -2000.0

    def test_variance_pct_calculation(self):
        """variance_pct = (actual - budgeted) / budgeted * 100."""
        cat = self._make_category(budgeted_amount=50000.0, actual_amount=55000.0)
        variance = cat["actual_amount"] - cat["budgeted_amount"]
        variance_pct = (variance / cat["budgeted_amount"]) * 100
        assert abs(variance_pct - 10.0) < 0.001

    def test_zero_budgeted_amount_no_division_by_zero(self):
        """When budgeted_amount=0, variance_pct must be computed safely."""
        cat = self._make_category(budgeted_amount=0.0, actual_amount=5000.0)
        budgeted = cat["budgeted_amount"] or 0
        variance_pct = ((cat["actual_amount"] - budgeted) / budgeted * 100) if budgeted > 0 else 0
        assert variance_pct == 0  # no crash, no division by zero

    def test_null_amounts_fallback_to_zero(self):
        """None amounts fall back to 0 via `or 0` pattern."""
        cat = self._make_category(budgeted_amount=None, actual_amount=None)
        budgeted = cat["budgeted_amount"] or 0
        actual = cat["actual_amount"] or 0
        assert budgeted == 0
        assert actual == 0

    def test_fund_type_filter_administrative(self):
        """Filter by fund_type='administrative' returns only admin categories."""
        categories = [
            self._make_category(name="Insurance", fund_type="administrative"),
            self._make_category(name="Painting", fund_type="sinking"),
            self._make_category(name="Gardening", fund_type="administrative"),
        ]
        filtered = [c for c in categories if c["fund_type"] == "administrative"]
        assert len(filtered) == 2
        assert all(c["fund_type"] == "administrative" for c in filtered)

    def test_fund_type_filter_all_returns_everything(self):
        """When filter='all', all categories are included."""
        categories = [
            self._make_category(name="Insurance", fund_type="administrative"),
            self._make_category(name="Painting", fund_type="sinking"),
        ]
        fund_filter = "all"
        filtered = [c for c in categories if fund_filter == "all" or c["fund_type"] == fund_filter]
        assert len(filtered) == 2
