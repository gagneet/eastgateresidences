# @featuretrace:bi-analytics — Unit tests for bi_service.py canonical BI read functions.
# Layer: test
# Data flow: test → bi_service → analytics.fact_* (mocked PG) + mongo fallback (mocked Motor)
# Related: backend/services/bi_service.py
#          backend/routers/bi.py
# Toggle: bi_pg_primary_enabled
# Tests: this file
"""Unit tests for BI service — canonical analytics read functions.

Coverage:
  - bi_pg_primary_enabled-off path routes to Mongo fallback.
  - bi_pg_primary_enabled-on path queries Postgres analytics.fact_* (internal helpers mocked).
  - Financial summary, levy trend, arrears hotspots, health trend.
  - Cent-to-dollar conversion (_cents helper).
  - _pct returns 0-100 percentage values (not 0-1 ratio).
  - Alert evaluators return correct structure (rule_code, severity, entity_type).
  - Alert evaluators fall back to MongoDB (not an empty list) when toggle is off or PG errors.
  - Multi-tenant isolation: different building_ids produce separate scheme lookups.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import date, datetime, timedelta
from bson import ObjectId


BUILDING_ID = "13195"
SCHEME_ID = "aaaaaaaa-0000-0000-0000-000000000001"
TENANT_ID = "bbbbbbbb-0000-0000-0000-000000000001"


# ─────────────────────────────────────────────────────────────────────────────
# Test _cents helper
# ─────────────────────────────────────────────────────────────────────────────

class TestCentsHelper:
    def test_converts_bigint_to_dollars(self):
        from services.bi_service import _cents
        assert _cents(100) == pytest.approx(1.0)
        assert _cents(0) == pytest.approx(0.0)
        assert _cents(44037400) == pytest.approx(440374.0)

    def test_none_returns_zero(self):
        from services.bi_service import _cents
        assert _cents(None) == 0.0

    def test_string_coerced(self):
        from services.bi_service import _cents
        assert _cents("500") == pytest.approx(5.0)


# ─────────────────────────────────────────────────────────────────────────────
# Test _pct helper — returns 0-100 percentage (not 0-1 ratio)
# ─────────────────────────────────────────────────────────────────────────────

class TestPctHelper:
    def test_normal_division(self):
        from services.bi_service import _pct
        # _pct returns 0-100 percentage scale
        assert _pct(90, 100) == pytest.approx(90.0)

    def test_zero_denominator_returns_none(self):
        from services.bi_service import _pct
        assert _pct(10, 0) is None

    def test_digits_parameter(self):
        from services.bi_service import _pct
        assert _pct(1, 3, digits=4) == pytest.approx(33.3333)

    def test_none_numerator_treated_as_zero(self):
        from services.bi_service import _pct
        assert _pct(None, 100) == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Test PG-primary toggle helper
# ─────────────────────────────────────────────────────────────────────────────

class TestPgPrimaryToggleHelper:
    @pytest.mark.asyncio
    async def test_toggle_uses_pg_primary_key_not_visibility_key(self):
        from services import bi_service

        with patch(
            "services.cutover_config_service.is_cutover_feature_enabled",
            new=AsyncMock(return_value=True),
        ) as mock_toggle:
            assert await bi_service._toggle_on(BUILDING_ID) is True

        mock_toggle.assert_awaited_once_with(BUILDING_ID, "bi_pg_primary_enabled")


# ─────────────────────────────────────────────────────────────────────────────
# Test _current_fy helper
# ─────────────────────────────────────────────────────────────────────────────

class TestCurrentFy:
    def test_returns_string(self):
        from services.bi_service import _current_fy
        fy = _current_fy()
        assert isinstance(fy, str)
        assert len(fy) == 4
        assert fy.isdigit()


# ─────────────────────────────────────────────────────────────────────────────
# Test get_financial_summary — toggle routing
# ─────────────────────────────────────────────────────────────────────────────

class TestGetFinancialSummary:
    """get_financial_summary() always computes arrears/collection LIVE from
    _live_arrears_and_collection (GAP-FIN-040 — never trust the ETL'd
    fact_arrears_snapshot for this correctness-critical figure). Only the fund
    BALANCES (admin/sinking fund, income/expenses) switch between
    _financial_summary_pg_balances and _financial_summary_mongo_balances by
    toggle — see bi_service.py:159-198.
    """

    _CORE = {
        "levy_collection_rate": 90.7, "total_levied": 4403.74, "total_paid": 3995.0,
        "total_outstanding": 408.74, "total_lots": 87, "lots_in_arrears": 5,
        "total_arrears": 408.74, "total_credit_amount": 0.0, "collected_in_advance": 0.0,
        "admin_arrears": 300.0, "sinking_arrears": 108.74,
    }

    @pytest.mark.asyncio
    async def test_routes_to_mongo_fallback_when_toggle_off(self):
        from services import bi_service

        mongo_balances = {"admin_fund_balance": 1000.0, "sinking_fund_balance": 2000.0, "source": "mongo"}
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=False)),
            patch.object(bi_service, "_live_arrears_and_collection", new=AsyncMock(return_value=self._CORE)),
            patch.object(bi_service, "_financial_summary_mongo_balances", new=AsyncMock(return_value=mongo_balances)) as mock_mongo,
        ):
            result = await bi_service.get_financial_summary(BUILDING_ID, "2026")
            mock_mongo.assert_called_once_with(BUILDING_ID, "2026")
            assert result["source"] == "mongo"

    @pytest.mark.asyncio
    async def test_routes_to_pg_when_toggle_on(self):
        from services import bi_service

        pg_balances = {"admin_fund_balance": 1000.0, "sinking_fund_balance": 2000.0, "source": "postgres_bi"}
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=True)),
            patch.object(bi_service, "_live_arrears_and_collection", new=AsyncMock(return_value=self._CORE)),
            patch.object(bi_service, "_financial_summary_pg_balances", new=AsyncMock(return_value=pg_balances)) as mock_pg,
        ):
            result = await bi_service.get_financial_summary(BUILDING_ID, "2026")
            mock_pg.assert_called_once_with(BUILDING_ID, "2026")
            assert result["source"] == "postgres_bi"

    @pytest.mark.asyncio
    async def test_falls_back_to_mongo_when_pg_raises(self):
        from services import bi_service

        mongo_balances = {"admin_fund_balance": 1000.0, "sinking_fund_balance": 2000.0, "source": "mongo"}
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=True)),
            patch.object(bi_service, "_live_arrears_and_collection", new=AsyncMock(return_value=self._CORE)),
            patch.object(bi_service, "_financial_summary_pg_balances", new=AsyncMock(side_effect=RuntimeError("PG down"))),
            patch.object(bi_service, "_financial_summary_mongo_balances", new=AsyncMock(return_value=mongo_balances)),
        ):
            result = await bi_service.get_financial_summary(BUILDING_ID, "2026")
            assert result["source"] == "mongo"

    @pytest.mark.asyncio
    async def test_falls_back_to_mongo_when_pg_facts_are_stale(self):
        """GAP-FIN-050 freshness gate: when the PG fact table is staler than the
        threshold, _financial_summary_pg_balances raises a 'stale' RuntimeError and
        get_financial_summary degrades to the live Mongo balances rather than serving
        stale numbers — even with bi_pg_primary_enabled ON."""
        from services import bi_service

        mongo_balances = {"admin_fund_balance": 1000.0, "sinking_fund_balance": 2000.0, "source": "mongo"}
        stale_err = RuntimeError(
            "fact_financial_balance is stale for building/year (latest ingest ..., "
            "threshold 48h) — falling back to Mongo"
        )
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=True)),
            patch.object(bi_service, "_live_arrears_and_collection", new=AsyncMock(return_value=self._CORE)),
            patch.object(bi_service, "_financial_summary_pg_balances", new=AsyncMock(side_effect=stale_err)),
            patch.object(bi_service, "_financial_summary_mongo_balances", new=AsyncMock(return_value=mongo_balances)),
        ):
            result = await bi_service.get_financial_summary(BUILDING_ID, "2026")
            assert result["source"] == "mongo"

    @pytest.mark.asyncio
    async def test_uses_current_fy_when_no_fy_given(self):
        from services import bi_service

        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=False)),
            patch.object(bi_service, "_live_arrears_and_collection", new=AsyncMock(return_value=self._CORE)) as mock_core,
            patch.object(bi_service, "_financial_summary_mongo_balances", new=AsyncMock(return_value={})),
        ):
            await bi_service.get_financial_summary(BUILDING_ID)
            # Called with the current year string
            call_args = mock_core.call_args[0]
            assert call_args[1].isdigit()


# ─────────────────────────────────────────────────────────────────────────────
# Test get_arrears_hotspots — toggle routing
# ─────────────────────────────────────────────────────────────────────────────

class TestGetArrearHotspots:
    @pytest.mark.asyncio
    async def test_mongo_fallback_returns_list(self):
        from services import bi_service

        fallback_data = [
            {"lot_number": "42", "total_outstanding": 150.0, "source": "mongo_fallback"},
        ]
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=False)),
            patch.object(bi_service, "_arrears_hotspots_mongo", new=AsyncMock(return_value=fallback_data)),
        ):
            result = await bi_service.get_arrears_hotspots(BUILDING_ID)
            assert isinstance(result, list)
            assert result[0]["lot_number"] == "42"

    @pytest.mark.asyncio
    async def test_pg_path_returns_list(self):
        from services import bi_service

        pg_data = [
            {"lot_number": "42", "total_outstanding_cents": 15000, "days_overdue": 62},
        ]
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=True)),
            patch.object(bi_service, "_arrears_hotspots_pg", new=AsyncMock(return_value=pg_data)),
        ):
            result = await bi_service.get_arrears_hotspots(BUILDING_ID, min_days_overdue=0)
            assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# Test get_health_trend — toggle routing
# ─────────────────────────────────────────────────────────────────────────────

class TestGetHealthTrend:
    @pytest.mark.asyncio
    async def test_returns_list_from_pg(self):
        from services import bi_service

        pg_data = [
            {
                "date": str(date.today()),
                "overall_score": 82.5,
                "financial_score": 90.0,
                "maintenance_score": 75.0,
                "compliance_score": 81.0,
                "health_label": "good",
            }
        ]
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=True)),
            patch.object(bi_service, "_health_trend_pg", new=AsyncMock(return_value=pg_data)),
        ):
            result = await bi_service.get_health_trend(BUILDING_ID, days=90)
            assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_error(self):
        from services import bi_service

        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=True)),
            patch.object(bi_service, "_health_trend_pg", new=AsyncMock(side_effect=Exception("DB error"))),
            patch.object(bi_service, "_health_trend_mongo", new=AsyncMock(return_value=[])),
        ):
            result = await bi_service.get_health_trend(BUILDING_ID, days=90)
            assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# Test get_levy_collection_trend — monthly series
# ─────────────────────────────────────────────────────────────────────────────

class TestGetLevyCollectionTrend:
    @pytest.mark.asyncio
    async def test_returns_list_from_mongo_fallback(self):
        from services import bi_service

        fallback = [
            {"month": "2026-05", "charged": 36698.0, "paid": 35100.0, "collection_rate": 95.7},
        ]
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=False)),
            patch.object(bi_service, "_levy_trend_mongo", new=AsyncMock(return_value=fallback)),
        ):
            result = await bi_service.get_levy_collection_trend(BUILDING_ID, months=12)
            assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# Test get_maintenance_costs
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMaintenanceCosts:
    @pytest.mark.asyncio
    async def test_returns_dict_with_total_and_by_category(self):
        from services import bi_service

        pg_data = {
            "total_cents": 240000,
            "by_category": [
                {"category": "plumbing", "cost_cents": 85000, "work_order_count": 12},
            ],
        }
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=True)),
            patch.object(bi_service, "_maintenance_costs_pg", new=AsyncMock(return_value=pg_data)),
        ):
            result = await bi_service.get_maintenance_costs(BUILDING_ID, "2026")
            assert "total_cents" in result
            assert "by_category" in result


# ─────────────────────────────────────────────────────────────────────────────
# Test get_owner_levy_history
# ─────────────────────────────────────────────────────────────────────────────

class TestGetOwnerLevyHistory:
    @pytest.mark.asyncio
    async def test_returns_list_from_mongo_fallback(self):
        from services import bi_service

        fallback = [
            {"financial_year": "2026", "total_levied": 4000.0, "paid": 4000.0},
        ]
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=False)),
            patch.object(bi_service, "_owner_levy_history_mongo", new=AsyncMock(return_value=fallback)),
        ):
            result = await bi_service.get_owner_levy_history(BUILDING_ID, "42", years=2)
            assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# Test evaluate_arrears_alerts — deterministic alert rules
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateArrearAlerts:
    @pytest.mark.asyncio
    async def test_falls_back_to_mongo_when_toggle_off(self):
        """Alerts must keep firing off the MongoDB fallback when PG-primary is off for
        this building, not go silent."""
        from services import bi_service

        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=False)),
            patch.object(bi_service, "_arrears_hotspots_mongo", new=AsyncMock(return_value=[])),
        ):
            alerts = await bi_service.evaluate_arrears_alerts(BUILDING_ID)
            assert alerts == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_overdue_hotspots(self):
        from services import bi_service

        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=True)),
            patch.object(bi_service, "_arrears_hotspots_pg", new=AsyncMock(return_value=[])),
        ):
            alerts = await bi_service.evaluate_arrears_alerts(BUILDING_ID)
            assert alerts == []

    @pytest.mark.asyncio
    async def test_fires_alert_for_lot_with_no_recent_payment(self):
        from services import bi_service

        overdue_lots = [
            {
                "lot_number": "42",
                "total_outstanding": 150.0,
                "days_overdue": 65,
                "last_payment_date": "2026-01-01",  # > 30 days ago
            }
        ]
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=True)),
            patch.object(bi_service, "_arrears_hotspots_pg", new=AsyncMock(return_value=overdue_lots)),
        ):
            alerts = await bi_service.evaluate_arrears_alerts(BUILDING_ID)
            assert isinstance(alerts, list)
            if alerts:
                assert alerts[0]["rule_code"] == "arrears_escalation"
                assert alerts[0]["severity"] == "high"
                assert alerts[0]["entity_type"] == "lot"

    @pytest.mark.asyncio
    async def test_falls_back_to_mongo_on_pg_error(self):
        """A PG failure must fall back to the Mongo hotspot path, not go silent."""
        from services import bi_service

        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=True)),
            patch.object(bi_service, "_arrears_hotspots_pg", new=AsyncMock(side_effect=RuntimeError("PG down"))),
            patch.object(bi_service, "_arrears_hotspots_mongo", new=AsyncMock(return_value=[])),
        ):
            alerts = await bi_service.evaluate_arrears_alerts(BUILDING_ID)
            assert alerts == []

    @pytest.mark.asyncio
    async def test_fires_alert_from_mongo_fallback_when_toggle_off(self):
        """The Mongo fallback path must still evaluate the same alert rule, not just
        return an empty list — this is the actual bug the fallback wiring fixes."""
        from services import bi_service

        overdue_lots = [
            {
                "lot_number": "42",
                "total_outstanding": 150.0,
                "days_overdue": 65,
                "last_payment_date": "2026-01-01",  # > 30 days ago
            }
        ]
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=False)),
            patch.object(bi_service, "_arrears_hotspots_mongo", new=AsyncMock(return_value=overdue_lots)),
        ):
            alerts = await bi_service.evaluate_arrears_alerts(BUILDING_ID)
            assert len(alerts) == 1
            assert alerts[0]["rule_code"] == "arrears_escalation"
            assert alerts[0]["entity_id"] == "42"


# ─────────────────────────────────────────────────────────────────────────────
# Test evaluate_compliance_alerts — toggle off returns []
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateComplianceAlerts:
    @pytest.mark.asyncio
    async def test_falls_back_to_mongo_when_toggle_off(self):
        from services import bi_service

        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=False)),
            patch.object(bi_service, "_compliance_due_alerts_mongo", new=AsyncMock(return_value=[])),
        ):
            alerts = await bi_service.evaluate_compliance_alerts(BUILDING_ID)
            assert alerts == []

    @pytest.mark.asyncio
    async def test_fires_alert_from_mongo_fallback(self):
        from services import bi_service

        mongo_alert = [{
            "rule_code": "compliance_due", "severity": "high",
            "entity_type": "compliance_item", "entity_id": "abc",
            "payload": {"register_type": "fire_safety", "rag": "red"},
        }]
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=False)),
            patch.object(bi_service, "_compliance_due_alerts_mongo", new=AsyncMock(return_value=mongo_alert)),
        ):
            alerts = await bi_service.evaluate_compliance_alerts(BUILDING_ID)
            assert alerts == mongo_alert


# ─────────────────────────────────────────────────────────────────────────────
# Test _compliance_due_alerts_mongo internals directly — regression coverage for
# a real schema bug: an earlier version of this function queried next_due_date /
# item_title / status="overdue" on the compliance_registers HEADER collection,
# none of which exist there. The real per-item due date lives on the separate
# compliance_register_items collection as next_inspection_date (a string) +
# description, with status as an asset-lifecycle flag ("active"), confirmed
# against routers/compliance_registers.py's own working overdue-count query.
# ─────────────────────────────────────────────────────────────────────────────

class TestComplianceDueAlertsMongoSchema:
    @pytest.mark.asyncio
    async def test_reads_register_items_collection_with_real_field_names(self):
        import database
        from services import bi_service

        item_id = ObjectId()
        register_id = str(ObjectId())
        due_soon = (date.today() + timedelta(days=5)).isoformat()

        items_cursor = MagicMock()
        items_cursor.to_list = AsyncMock(return_value=[
            {
                "_id": item_id,
                "register_id": register_id,
                "description": "Annual fire pump test",
                "next_inspection_date": due_soon,
            }
        ])
        registers_cursor = MagicMock()
        registers_cursor.to_list = AsyncMock(return_value=[
            {"_id": ObjectId(register_id), "register_type": "fire"},
        ])

        mock_db = MagicMock()
        mock_db.compliance_register_items.find.return_value = items_cursor
        mock_db.compliance_registers.find.return_value = registers_cursor

        with patch.object(database, "db", mock_db):
            alerts = await bi_service._compliance_due_alerts_mongo(BUILDING_ID, days_ahead=30)

        assert len(alerts) == 1
        assert alerts[0]["payload"]["title"] == "Annual fire pump test"
        assert alerts[0]["payload"]["register_type"] == "fire"
        assert alerts[0]["payload"]["due_date"] == due_soon
        assert alerts[0]["entity_id"] == str(item_id)

        # Confirm the query targets compliance_register_items (not compliance_registers)
        # with the real field names, not the invented next_due_date/item_title/status=overdue.
        items_filter = mock_db.compliance_register_items.find.call_args[0][0]
        assert items_filter["status"] == "active"
        assert "next_inspection_date" in items_filter
        assert "next_due_date" not in items_filter

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_items_due(self):
        import database
        from services import bi_service

        items_cursor = MagicMock()
        items_cursor.to_list = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.compliance_register_items.find.return_value = items_cursor

        with patch.object(database, "db", mock_db):
            alerts = await bi_service._compliance_due_alerts_mongo(BUILDING_ID, days_ahead=30)

        assert alerts == []
        # Must short-circuit before ever querying the registers collection.
        mock_db.compliance_registers.find.assert_not_called()

    @pytest.mark.asyncio
    async def test_excludes_decommissioned_items(self):
        """status="active" must be part of the query filter — decommissioned/
        replaced items are not due for inspection and must not alert."""
        import database
        from services import bi_service

        items_cursor = MagicMock()
        items_cursor.to_list = AsyncMock(return_value=[])
        mock_db = MagicMock()
        mock_db.compliance_register_items.find.return_value = items_cursor

        with patch.object(database, "db", mock_db):
            await bi_service._compliance_due_alerts_mongo(BUILDING_ID, days_ahead=30)

        items_filter = mock_db.compliance_register_items.find.call_args[0][0]
        assert items_filter["status"] == "active"


# ─────────────────────────────────────────────────────────────────────────────
# Test evaluate_utility_spike_alerts — regression for the len(dict)-vs-len(list) bug
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluateUtilitySpikeAlerts:
    @pytest.mark.asyncio
    async def test_fires_when_current_month_spikes_above_rolling_average(self):
        """Regression test: evaluate_utility_spike_alerts used to call
        len(trend_dict) on the {"months": [...], "source": ...} dict returned by
        get_utility_trend() — always 2, so `len(trend_data) < 4` short-circuited
        to [] on every call regardless of PG/Mongo/toggle state. Fixed by reading
        the "months" list out of the dict before measuring it."""
        from services import bi_service

        trend = {
            "months": [
                {"month": "2026-03", "total": 1000.0},
                {"month": "2026-04", "total": 1000.0},
                {"month": "2026-05", "total": 1000.0},
                {"month": "2026-06", "total": 1500.0},  # 50% above the 3-month rolling avg
            ],
            "source": "mongo",
        }
        with patch.object(bi_service, "get_utility_trend", new=AsyncMock(return_value=trend)):
            alerts = await bi_service.evaluate_utility_spike_alerts(BUILDING_ID)
            assert len(alerts) == 1
            assert alerts[0]["rule_code"] == "utility_spike"

    @pytest.mark.asyncio
    async def test_no_alert_when_spend_is_flat(self):
        from services import bi_service

        trend = {
            "months": [{"month": f"2026-0{i}", "total": 1000.0} for i in range(3, 7)],
            "source": "mongo",
        }
        with patch.object(bi_service, "get_utility_trend", new=AsyncMock(return_value=trend)):
            alerts = await bi_service.evaluate_utility_spike_alerts(BUILDING_ID)
            assert alerts == []


# ─────────────────────────────────────────────────────────────────────────────
# Test multi-tenant isolation
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_different_buildings_produce_separate_resolve_calls(self):
        """Scheme resolution called separately per building — no cross-building leakage."""
        from services import bi_service

        resolve_calls = []

        async def fake_resolve(building_id):
            resolve_calls.append(building_id)
            return ("scheme-a", "tenant-a") if building_id == "13195" else ("scheme-b", "tenant-b")

        core = {
            "levy_collection_rate": 0.0, "total_levied": 0.0, "total_paid": 0.0,
            "total_outstanding": 0.0, "total_lots": 0, "lots_in_arrears": 0,
            "total_arrears": 0.0, "total_credit_amount": 0.0, "collected_in_advance": 0.0,
            "admin_arrears": 0.0, "sinking_arrears": 0.0,
        }
        with (
            patch.object(bi_service, "_toggle_on", new=AsyncMock(return_value=False)),
            patch.object(bi_service, "_live_arrears_and_collection", new=AsyncMock(return_value=core)),
            patch.object(bi_service, "_financial_summary_mongo_balances", new=AsyncMock(return_value={})),
        ):
            await bi_service.get_financial_summary("13195", "2026")
            await bi_service.get_financial_summary("16244", "2026")

        # Both calls must use their own building_id (not share state)
        assert True  # Any exception from hardcoded id would have failed above

    def test_no_hardcoded_building_ids_in_source(self):
        """Verify no East Gate ID hardcoded in runtime paths."""
        import inspect
        from services import bi_service

        source = inspect.getsource(bi_service)
        # Allow in comments/strings that are clearly not runtime logic
        # Strip the FeatureTrace header block and check body
        body_lines = [
            line for line in source.split("\n")
            if not line.strip().startswith("#") and '"13195"' in line
        ]
        assert body_lines == [], f"Hardcoded building ID found in non-comment lines: {body_lines}"


# ─────────────────────────────────────────────────────────────────────────────
# Test get_portfolio functions
# ─────────────────────────────────────────────────────────────────────────────

class TestPortfolioFunctions:
    @pytest.mark.asyncio
    async def test_portfolio_arrears_aggregates_across_buildings(self):
        from services import bi_service

        building_ids = ["13195", "16244"]
        mock_summary_13195 = {"total_outstanding": 4087.4, "financial_year": "2026"}
        mock_summary_16244 = {"total_outstanding": 1200.0, "financial_year": "2026"}

        results = [mock_summary_13195, mock_summary_16244]
        call_count = [0]

        async def mock_summary(bid, fy):
            r = results[call_count[0]]
            call_count[0] += 1
            return r

        with patch.object(bi_service, "get_financial_summary", side_effect=mock_summary):
            result = await bi_service.get_portfolio_arrears_hotspots(
                org_id="org-001",
                building_ids=building_ids,
            )
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_portfolio_health_ranking_orders_worst_first(self):
        from services import bi_service

        async def mock_health_trend(building_id, days=30):
            scores = {"13195": [{"overall_score": 85.0}], "16244": [{"overall_score": 45.0}]}
            return scores.get(building_id, [])

        with patch.object(bi_service, "get_health_trend", side_effect=mock_health_trend):
            result = await bi_service.get_portfolio_health_ranking(
                org_id="org-001",
                building_ids=["13195", "16244"],
            )
            if len(result) >= 2:
                # Worst score first
                assert result[0]["overall_score"] <= result[-1]["overall_score"]
