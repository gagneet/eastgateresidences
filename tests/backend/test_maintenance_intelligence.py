from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from services.maintenance_intelligence_service import (
    compute_asset_risk_score,
    generate_capital_replacement_schedule,
    simulate_levy_stabilization
)


@pytest.mark.asyncio
async def test_compute_asset_risk_score():
    asset = {
        "id": "asset1",
        "name": "Test Asset",
        "installation_date": "2020-01-01T00:00:00Z",
        "expected_lifespan_years": 10,
        "last_service_date": "2023-01-01T00:00:00Z",
        "maintenance_frequency_months": 12,
        "replacement_cost_estimate": 10000
    }

    settings = {
        "repair_weight": 3.0,
        "age_weight": 20.0,
        "maintenance_overdue_weight": 2.0
    }

    # Correctly mock the database access in the service
    with patch("services.maintenance_intelligence_service.db") as mock_db:
        mock_db.work_orders.count_documents = AsyncMock(return_value=2)

        score = await compute_asset_risk_score(asset, settings, "13195")

        assert score > 0
        assert score <= 100


@pytest.mark.asyncio
async def test_generate_capital_replacement_schedule():
    asset = {
        "id": "asset1",
        "name": "Test Asset",
        "installation_date": "2010-01-01T00:00:00Z",
        "expected_lifespan_years": 20,
        "replacement_cost_estimate": 10000
    }

    with patch("services.maintenance_intelligence_service.db") as mock_db, \
            patch("services.maintenance_intelligence_service.get_intelligence_settings",
                  new_callable=AsyncMock) as mock_settings, \
            patch("services.maintenance_intelligence_service.update_capital_schedule", new_callable=AsyncMock):
        mock_settings.return_value = {"inflation_rate": 0.03}
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[asset])
        mock_db.building_assets.find.return_value = mock_cursor

        schedule = await generate_capital_replacement_schedule("13195")

        # Replacement year = 2010 + 20 = 2030
        assert len(schedule) == 1
        assert schedule[0]["replacement_year"] == 2030
        assert schedule[0]["estimated_cost"] > 10000


# ===========================================================================
# TestRiskScoreEdgeCases
# ===========================================================================

class TestRiskScoreEdgeCases:
    """Edge cases for compute_asset_risk_score()."""

    @pytest.mark.asyncio
    async def test_zero_repair_new_asset_low_risk(self):
        """A brand-new asset with no repairs and 0% age should have near-zero risk."""
        asset = {
            "id": "new-asset",
            "installation_date": datetime.now(timezone.utc).isoformat(),
            "expected_lifespan_years": 30,
            "last_service_date": datetime.now(timezone.utc).isoformat(),
            "maintenance_frequency_months": 12,
        }
        settings = {"repair_weight": 3.0, "age_weight": 20.0, "maintenance_overdue_weight": 2.0}

        with patch("services.maintenance_intelligence_service.db") as mock_db:
            mock_db.work_orders.count_documents = AsyncMock(return_value=0)
            score = await compute_asset_risk_score(asset, settings, "13195")

        # Age ratio ≈ 0, repair_count = 0, overdue_factor = 0 → score ≈ 0
        assert score == 0.0 or score < 5.0, f"Expected near-zero risk, got {score}"

    @pytest.mark.asyncio
    async def test_old_overdue_asset_high_risk(self):
        """A 100-year-old asset well past service due date should score near 100."""
        asset = {
            "id": "old-asset",
            "installation_date": "1920-01-01T00:00:00Z",  # > 100 years ago
            "expected_lifespan_years": 20,  # age_ratio >> 1.0, capped at 1.0
            "last_service_date": "2000-01-01T00:00:00Z",  # ~25 years overdue for monthly service
            "maintenance_frequency_months": 1,
        }
        settings = {"repair_weight": 3.0, "age_weight": 20.0, "maintenance_overdue_weight": 2.0}

        with patch("services.maintenance_intelligence_service.db") as mock_db:
            mock_db.work_orders.count_documents = AsyncMock(return_value=12)
            score = await compute_asset_risk_score(asset, settings, "13195")

        # Should be at or very close to maximum (100)
        assert score >= 90.0, f"Expected high risk (≥90), got {score}"

    @pytest.mark.asyncio
    async def test_no_service_date_defaults_to_high_overdue(self):
        """An asset with no last_service_date should use overdue_factor=1.0."""
        asset = {
            "id": "never-serviced",
            "installation_date": "2010-01-01T00:00:00Z",
            "expected_lifespan_years": 20,
            "last_service_date": None,  # Never serviced
            "maintenance_frequency_months": 6,
        }
        settings = {"repair_weight": 3.0, "age_weight": 20.0, "maintenance_overdue_weight": 2.0}

        with patch("services.maintenance_intelligence_service.db") as mock_db:
            mock_db.work_orders.count_documents = AsyncMock(return_value=0)
            score_no_service = await compute_asset_risk_score(asset, settings, "13195")

        asset_with_service = dict(asset, last_service_date=datetime.now(timezone.utc).isoformat())
        with patch("services.maintenance_intelligence_service.db") as mock_db:
            mock_db.work_orders.count_documents = AsyncMock(return_value=0)
            score_with_service = await compute_asset_risk_score(asset_with_service, settings, "13195")

        # Missing service date should be riskier
        assert score_no_service > score_with_service


# ===========================================================================
# TestLevyStabilization
# ===========================================================================

class TestLevyStabilization:
    """Tests for simulate_levy_stabilization()."""

    @pytest.mark.asyncio
    async def test_simulation_returns_10_projections(self):
        """simulate_levy_stabilization() must always return exactly 10 yearly projections."""
        with patch("services.maintenance_intelligence_service.db") as mock_db, \
                patch("utils.finance_helpers.get_latest_levy_year", new_callable=AsyncMock) as mock_year:
            mock_year.return_value = "2026"
            mock_db.annual_levies.find_one = AsyncMock(return_value={
                "admin_fund": {"levy_income": 288000},
                "sinking_fund": {"levy_income": 109000, "closing_balance": 150000},
            })
            mock_cap_cursor = MagicMock()
            mock_cap_cursor.to_list = AsyncMock(return_value=[])
            mock_db.capital_replacement_schedule.find.return_value = mock_cap_cursor

            result = await simulate_levy_stabilization("13195")

        assert "projections" in result
        assert len(result["projections"]) == 10

    @pytest.mark.asyncio
    async def test_simulation_levy_increases_over_time(self):
        """Projected levy should increase (or at least not decrease) year on year."""
        with patch("services.maintenance_intelligence_service.db") as mock_db, \
                patch("utils.finance_helpers.get_latest_levy_year", new_callable=AsyncMock) as mock_year:
            mock_year.return_value = "2026"
            mock_db.annual_levies.find_one = AsyncMock(return_value={
                "admin_fund": {"levy_income": 288000},
                "sinking_fund": {"levy_income": 109000, "closing_balance": 50000},
            })
            mock_cap_cursor = MagicMock()
            mock_cap_cursor.to_list = AsyncMock(return_value=[])
            mock_db.capital_replacement_schedule.find.return_value = mock_cap_cursor

            result = await simulate_levy_stabilization("13195")

        projections = result["projections"]
        levies = [p["levy_required"] for p in projections]

        # Every year's levy should be >= previous year's (inflation + stabilization)
        for i in range(1, len(levies)):
            assert levies[i] >= levies[i - 1] * 0.99, (
                f"Year {i + 1} levy ({levies[i]:.0f}) should not drop below year {i} ({levies[i - 1]:.0f})"
            )

    @pytest.mark.asyncio
    async def test_simulation_reports_unknown_when_no_levy_doc(self):
        """When no levy doc exists the baseline is UNKNOWN — not an invented 350000/150000.

        This test previously asserted the opposite: that a missing levy document produced
        `current_levy = 350000` and `current_reserve = 150000`, and it checked the
        projection grew from that invented base. Those constants were a rule violation
        ("last-resort fallbacks must be 0.0 + logger.warning(), never a building-specific
        hardcoded amount") and they fabricated a ten-year forecast that looked plausible
        for a building with no financial data at all.

        The test is inverted rather than deleted, so the old behaviour cannot come back
        unnoticed: the projection must now start from zero and the result must declare
        that its baseline was unavailable.
        """
        with patch("services.maintenance_intelligence_service.db") as mock_db, \
                patch("utils.finance_helpers.get_latest_levy_year", new_callable=AsyncMock) as mock_year:
            mock_year.return_value = "2026"
            mock_db.annual_levies.find_one = AsyncMock(return_value=None)  # No doc
            mock_cap_cursor = MagicMock()
            mock_cap_cursor.to_list = AsyncMock(return_value=[])
            mock_db.capital_replacement_schedule.find.return_value = mock_cap_cursor

            result = await simulate_levy_stabilization("13195")

        assert len(result["projections"]) == 10
        first = result["projections"][0]
        # No levy document => nothing to project FROM. The series still computes (so the
        # shape of the response is stable) but every figure derives from 0.0, and the
        # caller is told the baseline was unavailable so it never presents this as a
        # real forecast.
        assert first["levy_required"] == 0, (
            "a missing levy document must not produce a non-zero projected levy — "
            f"got {first['levy_required']}, which means a default was substituted again"
        )
        assert result.get("baseline_available") is False, (
            "simulate_levy_stabilization must flag that its baseline was unavailable"
        )


# ── Monthly maintenance shape (GAP-UI-002) ────────────────────────────────────
#
# The building-intelligence page used to spread one annual `predicted_cost` across
# twelve months with a SEASONAL_WEIGHTS array invented in the frontend. These tests
# pin the replacement: the shape is either measured from this building's own work
# orders, or it is declared flat — never a curve nobody can trace.

def _wo(month: int, cost: float, *, actual=True, year=2025):
    key = "actual_cost" if actual else "estimated_cost"
    return {"completed_at": f"{year}-{month:02d}-15T00:00:00Z", key: cost}


class _AsyncCursor:
    """Minimal async-iterable stand-in for a Motor cursor.

    _monthly_maintenance_shape iterates the cursor rather than calling
    `.to_list(N)`, so a MagicMock with an AsyncMock `to_list` is not a valid
    double for it — `async for` needs __aiter__/__anext__. Getting this wrong
    fails loudly (TypeError), which is the point: the test double has to track
    how the code actually reads.
    """

    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._i]
        self._i += 1
        return row


def _mock_db_with(work_orders, levy_categories=None):
    """Patch the service's db so iterating work_orders.find(...) yields `work_orders`.

    `levy_categories` backs maintenance_spend_history() — the ACTUAL per-year
    maintenance spend the forecast is anchored to. It defaults to empty, which is
    the "building has no imported category actuals" case: annual_history == [] and
    history_based_forecast is None. Mocking it is not optional: generate_maintenance_forecast()
    reads this collection too, and a MagicMock is not awaitable, so leaving it out
    fails inside the service rather than in the assertion.
    """
    mock_db = MagicMock()
    mock_db.work_orders.find = MagicMock(return_value=_AsyncCursor(work_orders))
    cats = MagicMock()
    cats.to_list = AsyncMock(return_value=list(levy_categories or []))
    mock_db.levy_categories.find = MagicMock(return_value=cats)
    return mock_db


@pytest.mark.asyncio
async def test_monthly_shape_is_even_spread_without_enough_history():
    """Too little history must say "even spread", not invent a season.

    A building with three repairs on file has no detectable seasonality. Returning
    a shaped curve there would be the exact frontend bug this replaced, just moved
    one layer down.
    """
    from services.maintenance_intelligence_service import _monthly_maintenance_shape

    with patch("services.maintenance_intelligence_service.db", _mock_db_with([
        _wo(3, 100.0), _wo(7, 200.0), _wo(11, 300.0),
    ])):
        shape = await _monthly_maintenance_shape("13195")

    assert shape["basis"] == "even_spread"
    assert shape["sample_size"] == 3
    assert len(shape["weights"]) == 12
    assert all(abs(w - 1 / 12) < 1e-9 for w in shape["weights"])


@pytest.mark.asyncio
async def test_monthly_shape_is_measured_when_history_supports_it():
    """With enough spread-out history the weights are the building's real spend."""
    from services.maintenance_intelligence_service import _monthly_maintenance_shape

    # 12 work orders across 6 distinct months; July carries double the others.
    orders = []
    for month in (1, 2, 3, 4, 5):
        orders += [_wo(month, 100.0), _wo(month, 100.0)]   # 200 each
    orders += [_wo(7, 200.0), _wo(7, 200.0)]               # 400 in July
    with patch("services.maintenance_intelligence_service.db", _mock_db_with(orders)):
        shape = await _monthly_maintenance_shape("13195")

    assert shape["basis"] == "historical_seasonality"
    assert shape["sample_size"] == 12
    assert shape["distinct_months"] == 6
    assert abs(sum(shape["weights"]) - 1.0) < 1e-9
    # July (index 6) is double any single contributing month, and months with no
    # spend stay at zero rather than being smoothed into a curve.
    assert abs(shape["weights"][6] - 400 / 1400) < 1e-9
    assert shape["weights"][11] == 0.0


@pytest.mark.asyncio
async def test_monthly_shape_prefers_actual_cost_over_estimate():
    """An estimate is what a job was expected to cost; using it where an invoice
    exists would bake estimating bias into the seasonal shape."""
    from services.maintenance_intelligence_service import _monthly_maintenance_shape

    orders = [{"completed_at": "2025-04-10T00:00:00Z", "actual_cost": 900.0, "estimated_cost": 100.0}]
    orders += [_wo(m, 100.0) for m in (1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12)]
    with patch("services.maintenance_intelligence_service.db", _mock_db_with(orders)):
        shape = await _monthly_maintenance_shape("13195")

    assert shape["basis"] == "historical_seasonality"
    # April is 900 of 2000, which is only true if actual_cost won.
    assert abs(shape["weights"][3] - 900 / 2000) < 1e-9


@pytest.mark.asyncio
async def test_monthly_breakdown_absent_when_there_is_no_annual_figure():
    """Twelve zeros would read as "no maintenance expected", a far stronger claim
    than "we do not know yet". The breakdown must be absent instead."""
    from services.maintenance_intelligence_service import generate_maintenance_forecast

    mock_db = _mock_db_with([])
    assets_cursor = MagicMock()
    assets_cursor.to_list = AsyncMock(return_value=[])       # no costed assets
    mock_db.building_assets.find = MagicMock(return_value=assets_cursor)
    mock_db.work_orders.count_documents = AsyncMock(return_value=0)

    with patch("services.maintenance_intelligence_service.db", mock_db), \
            patch("services.maintenance_intelligence_service.get_intelligence_settings",
                  new_callable=AsyncMock, return_value={"inflation_rate": 0.03}), \
            patch("services.maintenance_intelligence_service.upsert_maintenance_forecast",
                  new_callable=AsyncMock):
        forecast = await generate_maintenance_forecast("13195")

    assert forecast["predicted_cost"] == 0
    assert forecast["monthly_breakdown"] is None
    assert forecast["monthly_basis"] == "even_spread"


@pytest.mark.asyncio
async def test_monthly_breakdown_sums_to_the_annual_forecast():
    """The months must add up to the year. A distribution that quietly loses or
    invents money is worse than no distribution."""
    from services.maintenance_intelligence_service import generate_maintenance_forecast

    mock_db = _mock_db_with([])
    assets_cursor = MagicMock()
    assets_cursor.to_list = AsyncMock(return_value=[
        {"name": "Lift", "replacement_cost_estimate": 120000, "risk_score": 10},
    ])
    mock_db.building_assets.find = MagicMock(return_value=assets_cursor)
    mock_db.work_orders.count_documents = AsyncMock(return_value=0)

    with patch("services.maintenance_intelligence_service.db", mock_db), \
            patch("services.maintenance_intelligence_service.get_intelligence_settings",
                  new_callable=AsyncMock, return_value={"inflation_rate": 0.03}), \
            patch("services.maintenance_intelligence_service.upsert_maintenance_forecast",
                  new_callable=AsyncMock):
        forecast = await generate_maintenance_forecast("13195")

    months = forecast["monthly_breakdown"]
    assert months is not None and len(months) == 12
    assert [m["month"] for m in months] == list(range(1, 13))
    # Cents-level rounding per month, so allow one cent of drift per month.
    assert abs(sum(m["predicted_cost"] for m in months) - forecast["predicted_cost"]) < 0.12


class TestLevyStabilizationBaselineSource:
    """The annual levy baseline must come from the PROPOSED annual amounts.

    Regression cover for a live defect on /intelligence/building (2026-08-28). The
    simulation read `admin_fund.levy_income + sinking_fund.levy_income` as its annual
    levy. That field is YTD *collected*, which CLAUDE.md names as a recurring bug — and
    on East Gate's FY2026 document, which is PDF-sourced, it is NULL while
    `proposed_amount_cents` carries the real budget.

    The result reaching the browser was a ten-year projection with levy_required =
    $0.00 in every year and a reserve falling to -$991,865.93, drawn as a chart under
    the confident recommendation "Increase levies by 5.0% annually" — and
    `baseline_available` stayed True the whole time, because it only asked whether the
    document existed.
    """

    @staticmethod
    def _db(levy_doc):
        mock_db = MagicMock()
        mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        mock_db.capital_replacement_schedule.find.return_value = cursor
        return mock_db

    @pytest.mark.asyncio
    async def test_proposed_cents_are_used_when_levy_income_is_null(self):
        """East Gate FY2026 in miniature: levy_income NULL, proposed_amount_cents set."""
        levy_doc = {
            "admin_fund": {"levy_income": None, "proposed_amount_cents": 30988200},
            "sinking_fund": {
                "levy_income": None,
                "proposed_amount_cents": 9045900,
                "closing_balance": 166969.06,
            },
        }
        with patch("services.maintenance_intelligence_service.db", self._db(levy_doc)), \
                patch("utils.finance_helpers.get_latest_levy_year", new_callable=AsyncMock) as mock_year:
            mock_year.return_value = "2026"
            result = await simulate_levy_stabilization("13195")

        assert result["baseline_available"] is True
        assert result["baseline_basis"]["annual_levy_total"] == pytest.approx(400341.0)
        # Operating cost is the ADMIN fund itself, not `total * 0.8` — the invented
        # constant this replaced. Sinking is capital and must not be counted as ops.
        assert result["baseline_basis"]["annual_admin_levy"] == pytest.approx(309882.0)
        # The defect's signature: every projected levy was exactly zero.
        assert all(p["levy_required"] > 0 for p in result["projections"])

    @pytest.mark.asyncio
    async def test_document_yielding_no_annual_levy_is_not_a_baseline(self):
        """A doc can exist and still carry nothing usable. Existence was the old test."""
        levy_doc = {
            "admin_fund": {"levy_income": None},
            "sinking_fund": {"levy_income": None, "closing_balance": 166969.06},
        }
        with patch("services.maintenance_intelligence_service.db", self._db(levy_doc)), \
                patch("utils.finance_helpers.get_latest_levy_year", new_callable=AsyncMock) as mock_year:
            mock_year.return_value = "2026"
            result = await simulate_levy_stabilization("13195")

        assert result["baseline_available"] is False, (
            "a levy document with no usable annual amount must not be reported as a baseline"
        )
        assert "no projection can be made" in result["recommendation"].lower()

    @pytest.mark.asyncio
    async def test_ytd_collected_does_not_outrank_the_proposed_annual_levy(self):
        """Where both are present they are different quantities, and proposed wins.

        East Gate FY2025 records $466,976.27 collected against a $361,338.00 proposed
        annual levy. Using the collected figure overstates the recurring baseline.
        """
        levy_doc = {
            "admin_fund": {"levy_income": 349652.23, "proposed_amount_cents": 26207600},
            "sinking_fund": {
                "levy_income": 117324.04,
                "proposed_amount_cents": 9926200,
                "closing_balance": 212644.97,
            },
        }
        with patch("services.maintenance_intelligence_service.db", self._db(levy_doc)), \
                patch("utils.finance_helpers.get_latest_levy_year", new_callable=AsyncMock) as mock_year:
            mock_year.return_value = "2025"
            result = await simulate_levy_stabilization("13195")

        assert result["baseline_basis"]["annual_levy_total"] == pytest.approx(361338.0)
        assert result["baseline_basis"]["annual_levy_total"] != pytest.approx(466976.27)


# ===========================================================================
# Maintenance spend history — the forecast's anchor in real money
# ===========================================================================

class TestMaintenanceSpendHistory:
    """The 12-month chart rendered a FLAT LINE, and the flat line was a symptom.

    Two causes, only one of which was the monthly split:

      1. `monthly_basis` is `even_spread` whenever there is too little costed
         work-order history to measure seasonality — and there is no month-level
         signal in the GL either (every finance.expense_transactions row for East
         Gate is an ANNUAL total dated 31 December). Inventing a curve would repeat
         the SEASONAL_WEIGHTS mistake this module already removed once.

      2. The annual figure being spread was itself wrong by ~6x. `predicted_cost`
         is an asset-risk model with no link to actual spend: $26,765/yr against
         East Gate's real maintenance spend of $147k-$191k/yr.

    So the fix is to anchor the forecast in the building's own actuals and show
    both estimates, never to fabricate a shape.
    """

    def test_classifier_separates_maintenance_from_overheads(self):
        from services.maintenance_intelligence_service import is_maintenance_category

        for name in ["Lift Maintenance Contract", "Roof Repairs", "Gardens & Grounds",
                     "Fire Protection - Contracted", "Plumbing & Drainage"]:
            assert is_maintenance_category(name), name

        # Overheads must NOT count as maintenance, including names that contain a
        # maintenance-ish word ("Lift Registration Fee" is a fee, not a repair).
        for name in ["Insurance Premiums", "Management Fee", "Bank Charges",
                     "Water - Utility", "Income Tax Expense", "Audit Fees",
                     "Lift Registration Fee"]:
            assert not is_maintenance_category(name), name

        assert not is_maintenance_category("")
        assert not is_maintenance_category(None)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_history_aggregates_actuals_per_year_ignoring_archived(self):
        from services.maintenance_intelligence_service import maintenance_spend_history

        # `is_archived` rows are excluded by the QUERY, so the mock returns only
        # what the query would: consolidated duplicates never reach the aggregation.
        mock_db = _mock_db_with([], levy_categories=[
            {"name": "Roof Repairs", "year": "2024", "actual_amount": 1000.0},
            {"name": "Lift Maintenance", "year": "2024", "actual_amount": 500.0},
            {"name": "Insurance Premiums", "year": "2024", "actual_amount": 9999.0},  # excluded
            {"name": "Plumbing & Drainage", "year": "2025", "actual_amount": 250.0},
            {"name": "Cleaning", "year": "2025", "actual_amount": 0.0},   # zero -> skipped
            {"name": "Gardening", "year": "", "actual_amount": 100.0},    # no year -> skipped
        ])
        with patch("services.maintenance_intelligence_service.db", mock_db):
            history = await maintenance_spend_history("13195")

        assert history == [
            {"year": "2024", "actual_cost": 1500.0, "category_count": 2},
            {"year": "2025", "actual_cost": 250.0, "category_count": 1},
        ]

    @pytest.mark.asyncio
    async def test_no_history_yields_empty_list_and_null_forecast(self):
        """Empty list means "no history", which the UI must render differently from
        a history of zero. Null forecast means "not enough years", never "$0
        expected" — the missing-vs-zero rule."""
        from services.maintenance_intelligence_service import generate_maintenance_forecast

        mock_db = _mock_db_with([], levy_categories=[])
        assets_cursor = MagicMock()
        assets_cursor.to_list = AsyncMock(return_value=[])
        mock_db.building_assets.find = MagicMock(return_value=assets_cursor)
        mock_db.work_orders.count_documents = AsyncMock(return_value=0)

        with patch("services.maintenance_intelligence_service.db", mock_db), \
                patch("services.maintenance_intelligence_service.get_intelligence_settings",
                      new_callable=AsyncMock, return_value={"inflation_rate": 0.03}), \
                patch("services.maintenance_intelligence_service.upsert_maintenance_forecast",
                      new_callable=AsyncMock):
            forecast = await generate_maintenance_forecast("13195")

        assert forecast["annual_history"] == []
        assert forecast["history_based_forecast"] is None
        assert forecast["history_forecast_basis"] is None

    @pytest.mark.asyncio
    async def test_forecast_averages_completed_years_only(self):
        """The CURRENT year is year-to-date, not an annual total. Including it would
        drag the mean down by however much of the year has not happened yet."""
        from datetime import date as _date
        from services.maintenance_intelligence_service import generate_maintenance_forecast

        this_year = str(_date.today().year)
        mock_db = _mock_db_with([], levy_categories=[
            {"name": "Roof Repairs", "year": "2022", "actual_amount": 100.0},
            {"name": "Roof Repairs", "year": "2023", "actual_amount": 200.0},
            {"name": "Roof Repairs", "year": "2024", "actual_amount": 300.0},
            {"name": "Roof Repairs", "year": "2025", "actual_amount": 400.0},
            # Part-year: must not enter the mean.
            {"name": "Roof Repairs", "year": this_year, "actual_amount": 10.0},
        ])
        assets_cursor = MagicMock()
        assets_cursor.to_list = AsyncMock(return_value=[])
        mock_db.building_assets.find = MagicMock(return_value=assets_cursor)
        mock_db.work_orders.count_documents = AsyncMock(return_value=0)

        with patch("services.maintenance_intelligence_service.db", mock_db), \
                patch("services.maintenance_intelligence_service.get_intelligence_settings",
                      new_callable=AsyncMock, return_value={"inflation_rate": 0.03}), \
                patch("services.maintenance_intelligence_service.upsert_maintenance_forecast",
                      new_callable=AsyncMock):
            forecast = await generate_maintenance_forecast("13195")

        # Trailing THREE completed years: 2023, 2024, 2025 -> (200+300+400)/3
        assert forecast["history_based_forecast"] == 300.0
        assert "2023-2025" in forecast["history_forecast_basis"]
        # The part-year row is still reported as history, just not averaged.
        assert {h["year"] for h in forecast["annual_history"]} == {
            "2022", "2023", "2024", "2025", this_year
        }

    @pytest.mark.asyncio
    async def test_two_completed_years_is_not_enough_to_project(self):
        """Two years cannot distinguish a trend from a one-off major work item."""
        from services.maintenance_intelligence_service import generate_maintenance_forecast

        mock_db = _mock_db_with([], levy_categories=[
            {"name": "Roof Repairs", "year": "2023", "actual_amount": 100.0},
            {"name": "Roof Repairs", "year": "2024", "actual_amount": 900.0},
        ])
        assets_cursor = MagicMock()
        assets_cursor.to_list = AsyncMock(return_value=[])
        mock_db.building_assets.find = MagicMock(return_value=assets_cursor)
        mock_db.work_orders.count_documents = AsyncMock(return_value=0)

        with patch("services.maintenance_intelligence_service.db", mock_db), \
                patch("services.maintenance_intelligence_service.get_intelligence_settings",
                      new_callable=AsyncMock, return_value={"inflation_rate": 0.03}), \
                patch("services.maintenance_intelligence_service.upsert_maintenance_forecast",
                      new_callable=AsyncMock):
            forecast = await generate_maintenance_forecast("13195")

        assert len(forecast["annual_history"]) == 2
        assert forecast["history_based_forecast"] is None
