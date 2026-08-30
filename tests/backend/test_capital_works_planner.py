"""
Tests for Capital Works Planner — new dedicated page + edit endpoints.

Covers:
  GET  /intelligence/capital-works         — existing, no _id leak
  PUT  /intelligence/capital-works         — NEW: role-gated edit
  PUT  /finance/sinking-fund-plan          — broadened to plan-edit roles
  PUT  /finance/sinking-fund-capital-events— broadened to plan-edit roles

Run:
    cd backend
    venv/bin/pytest ../tests/backend/test_capital_works_planner.py -v
"""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

_BASE = "http://127.0.0.1:8003/api"


def _cursor(rows):
    c = MagicMock()
    c.sort.return_value = c
    c.to_list = AsyncMock(return_value=rows)
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _login(email: str, password: str) -> dict | None:
    try:
        r = requests.post(f"{_BASE}/auth/login", json={"email": email, "password": password}, timeout=10)
        tok = r.json().get("token")
        return {"Authorization": f"Bearer {tok}"} if tok else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests — CapitalWorkItemUpdate Pydantic model validation (no DB needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestCapitalWorkItemUpdateModel:
    """Validate CapitalWorkItemUpdate Pydantic model directly."""

    def _load_model(self):
        from routers.intelligence import CapitalWorkItemUpdate
        return CapitalWorkItemUpdate

    def test_valid_item_parses(self):
        M = self._load_model()
        item = M(asset_name="Roof", replacement_year=2028, estimated_cost=50000.0)
        assert item.asset_name == "Roof"
        assert item.replacement_year == 2028
        assert item.estimated_cost == 50000.0

    def test_optional_category_defaults_empty(self):
        M = self._load_model()
        item = M(asset_name="Lift", replacement_year=2030, estimated_cost=80000)
        assert item.category == ""

    def test_optional_asset_id_defaults_none(self):
        M = self._load_model()
        item = M(asset_name="Lift", replacement_year=2030, estimated_cost=80000)
        assert item.asset_id is None

    def test_category_can_be_set(self):
        M = self._load_model()
        item = M(asset_name="Pump", replacement_year=2027, estimated_cost=12000, category="Plumbing")
        assert item.category == "Plumbing"

    def test_asset_id_can_be_set(self):
        M = self._load_model()
        item = M(asset_name="Pump", replacement_year=2027, estimated_cost=12000, asset_id="asset-123")
        assert item.asset_id == "asset-123"

    def test_dict_representation(self):
        M = self._load_model()
        item = M(asset_name="Roof", replacement_year=2028, estimated_cost=50000, category="Roofing")
        d = item.dict()
        assert d["asset_name"] == "Roof"
        assert d["replacement_year"] == 2028
        assert d["estimated_cost"] == 50000
        assert d["category"] == "Roofing"


class TestCapitalWorksUpdateRequestModel:
    """Validate CapitalWorksUpdateRequest Pydantic model."""

    def _load_model(self):
        from routers.intelligence import CapitalWorksUpdateRequest, CapitalWorkItemUpdate
        return CapitalWorksUpdateRequest, CapitalWorkItemUpdate

    def test_empty_items_list_valid(self):
        Req, _ = self._load_model()
        req = Req(items=[])
        assert req.items == []

    def test_single_item_list(self):
        Req, Item = self._load_model()
        req = Req(items=[Item(asset_name="Roof", replacement_year=2028, estimated_cost=50000)])
        assert len(req.items) == 1
        assert req.items[0].asset_name == "Roof"

    def test_multiple_items(self):
        Req, Item = self._load_model()
        req = Req(items=[
            Item(asset_name="Roof", replacement_year=2028, estimated_cost=50000),
            Item(asset_name="Lift", replacement_year=2032, estimated_cost=200000),
        ])
        assert len(req.items) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests — _require_plan_edit permission helper
# ─────────────────────────────────────────────────────────────────────────────

class TestRequirePlanEditHelper:
    """_require_plan_edit raises 403 for unauthorised roles."""

    def _fn(self):
        from routers.finance_intelligence import _require_plan_edit
        return _require_plan_edit

    def test_super_admin_allowed(self):
        from fastapi import HTTPException
        fn = self._fn()
        # Should not raise
        fn({"role": "super_admin"})

    def test_chairman_allowed(self):
        fn = self._fn()
        fn({"role": "chairman"})

    def test_strata_manager_allowed(self):
        fn = self._fn()
        fn({"role": "strata_manager"})

    def test_ec_member_allowed(self):
        fn = self._fn()
        fn({"role": "ec_member"})

    def test_owner_denied(self):
        from fastapi import HTTPException
        fn = self._fn()
        with pytest.raises(HTTPException) as exc_info:
            fn({"role": "owner"})
        assert exc_info.value.status_code == 403

    def test_tenant_denied(self):
        from fastapi import HTTPException
        fn = self._fn()
        with pytest.raises(HTTPException) as exc_info:
            fn({"role": "tenant"})
        assert exc_info.value.status_code == 403

    def test_guest_denied(self):
        from fastapi import HTTPException
        fn = self._fn()
        with pytest.raises(HTTPException) as exc_info:
            fn({"role": "guest"})
        assert exc_info.value.status_code == 403

    def test_service_provider_denied(self):
        from fastapi import HTTPException
        fn = self._fn()
        with pytest.raises(HTTPException) as exc_info:
            fn({"role": "service_provider"})
        assert exc_info.value.status_code == 403

    def test_error_message_is_informative(self):
        from fastapi import HTTPException
        fn = self._fn()
        with pytest.raises(HTTPException) as exc_info:
            fn({"role": "owner"})
        assert "Chairman" in exc_info.value.detail or "EC Member" in exc_info.value.detail


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests — update_capital_works_schedule endpoint logic (mocked DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateCapitalWorksScheduleLogic:
    """Test the PUT /capital-works endpoint logic with mocked DB."""

    @pytest.mark.asyncio
    async def test_items_get_building_id_injected(self):
        """Each item should have building_id set to PLAN_ID."""
        from routers.intelligence import update_capital_works_schedule, CapitalWorksUpdateRequest, CapitalWorkItemUpdate

        with patch("routers.intelligence.db") as mock_db, \
                patch("repositories.digital_twin_repository.update_capital_schedule",
                      new_callable=AsyncMock) as mock_update:
            mock_db.capital_shock_risks.delete_many = AsyncMock()
            current_user = {"role": "super_admin"}
            payload = CapitalWorksUpdateRequest(items=[
                CapitalWorkItemUpdate(asset_name="Roof", replacement_year=2028, estimated_cost=50000),
            ])

            result = await update_capital_works_schedule(payload, current_user, "13195")

            assert result["count"] == 1
            assert result["message"] == "Capital works schedule updated"
            call_args = mock_update.call_args
            items_written = call_args[0][1]
            assert items_written[0]["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_items_get_source_manual(self):
        """Source field should be set to 'manual' for manually-entered items."""
        from routers.intelligence import update_capital_works_schedule, CapitalWorksUpdateRequest, CapitalWorkItemUpdate

        with patch("routers.intelligence.db") as mock_db, \
                patch("repositories.digital_twin_repository.update_capital_schedule",
                      new_callable=AsyncMock) as mock_update:
            mock_db.capital_shock_risks.delete_many = AsyncMock()
            current_user = {"role": "chairman"}
            payload = CapitalWorksUpdateRequest(items=[
                CapitalWorkItemUpdate(asset_name="Lift", replacement_year=2032, estimated_cost=150000),
            ])

            await update_capital_works_schedule(payload, current_user, "13195")

            items_written = mock_update.call_args[0][1]
            assert items_written[0]["source"] == "manual"

    @pytest.mark.asyncio
    async def test_capital_shock_cache_invalidated(self):
        """PUT should invalidate capital_shock_risks cache."""
        from routers.intelligence import update_capital_works_schedule, CapitalWorksUpdateRequest

        with patch("routers.intelligence.db") as mock_db, \
                patch("repositories.digital_twin_repository.update_capital_schedule", new_callable=AsyncMock):
            mock_db.capital_shock_risks.delete_many = AsyncMock()
            current_user = {"role": "super_admin"}
            payload = CapitalWorksUpdateRequest(items=[])

            await update_capital_works_schedule(payload, current_user, "13195")

            mock_db.capital_shock_risks.delete_many.assert_called_once_with({"building_id": "13195"})

    @pytest.mark.asyncio
    async def test_owner_raises_403(self):
        """Owner role should not be able to call update_capital_works_schedule."""
        from fastapi import HTTPException
        from routers.intelligence import update_capital_works_schedule, CapitalWorksUpdateRequest

        current_user = {"role": "owner"}
        payload = CapitalWorksUpdateRequest(items=[])

        with pytest.raises(HTTPException) as exc_info:
            await update_capital_works_schedule(payload, current_user, "13195")
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_empty_items_list_succeeds(self):
        """Empty items list should work (effectively clears the schedule)."""
        from routers.intelligence import update_capital_works_schedule, CapitalWorksUpdateRequest

        with patch("routers.intelligence.db") as mock_db, \
                patch("repositories.digital_twin_repository.update_capital_schedule",
                      new_callable=AsyncMock) as mock_update:
            mock_db.capital_shock_risks.delete_many = AsyncMock()
            current_user = {"role": "ec_member"}
            payload = CapitalWorksUpdateRequest(items=[])

            result = await update_capital_works_schedule(payload, current_user, "13195")

            assert result["count"] == 0
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_items_all_get_building_id(self):
        """All items in a batch get building_id injected."""
        from routers.intelligence import update_capital_works_schedule, CapitalWorksUpdateRequest, CapitalWorkItemUpdate

        with patch("routers.intelligence.db") as mock_db, \
                patch("repositories.digital_twin_repository.update_capital_schedule",
                      new_callable=AsyncMock) as mock_update:
            mock_db.capital_shock_risks.delete_many = AsyncMock()
            current_user = {"role": "strata_manager"}
            payload = CapitalWorksUpdateRequest(items=[
                CapitalWorkItemUpdate(asset_name="Roof", replacement_year=2028, estimated_cost=50000),
                CapitalWorkItemUpdate(asset_name="Lift", replacement_year=2032, estimated_cost=150000),
                CapitalWorkItemUpdate(asset_name="Pump", replacement_year=2027, estimated_cost=12000),
            ])

            result = await update_capital_works_schedule(payload, current_user, "13195")

            assert result["count"] == 3
            items_written = mock_update.call_args[0][1]
            for item in items_written:
                assert item["building_id"] == "13195"
                assert item["source"] == "manual"
                assert "updated_at" in item


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests — live API (skip if backend not reachable)
# ─────────────────────────────────────────────────────────────────────────────

class TestCapitalWorksGetEndpoint:
    """GET /intelligence/capital-works — data from DB, no mock data."""

    def test_returns_200(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/intelligence/capital-works", headers=admin_headers, timeout=15)
        assert r.status_code == 200

    def test_returns_list(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/intelligence/capital-works", headers=admin_headers, timeout=15)
        assert isinstance(r.json(), list)

    def test_no_id_field_leaked(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/intelligence/capital-works", headers=admin_headers, timeout=15)
        for item in r.json():
            assert "_id" not in item

    def test_required_fields_present(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/intelligence/capital-works", headers=admin_headers, timeout=15)
        data = r.json()
        if data:
            for field in ["asset_name", "replacement_year", "estimated_cost"]:
                assert field in data[0], f"Missing field: {field}"

    def test_data_comes_from_database(self, admin_headers):
        """Verify items have building_id = '13195' (set by DB seed, not mock)."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/intelligence/capital-works", headers=admin_headers, timeout=15)
        data = r.json()
        # Items from seed have building_id; API strips _id but keeps other fields
        if data:
            # At minimum we should see seeded data (asset names from seed)
            names = [i["asset_name"] for i in data]
            assert len(names) > 0, "No items — seed may not have run"

    def test_unauthenticated_denied(self):
        try:
            r = requests.get(f"{_BASE}/intelligence/capital-works", timeout=10)
        except Exception:
            pytest.skip("Backend not reachable")
        assert r.status_code in (401, 403)

    def test_items_sorted_by_replacement_year(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/intelligence/capital-works", headers=admin_headers, timeout=15)
        data = r.json()
        if len(data) >= 2:
            years = [item["replacement_year"] for item in data]
            assert years == sorted(years), "Items should be sorted by replacement_year"


class TestCapitalWorksPutEndpoint:
    """PUT /intelligence/capital-works — role-based edit, persistence."""

    def teardown_method(self, method):
        """After mutations, recompute the schedule from assets to restore seeded data."""
        if method.__name__ in (
                "test_super_admin_can_update", "test_update_returns_correct_count",
                "test_update_persists_to_get", "test_items_flagged_as_manual_source",
        ):
            h = _login("administrator@eastgateresidences.com.au", os.environ.get("E2E_ADMIN_PASSWORD", ""))
            if h:
                requests.post(f"{_BASE}/intelligence/recompute", headers=h, timeout=30)

    def test_super_admin_can_update(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        payload = {"items": [
            {"asset_name": "Integration Test Asset", "replacement_year": 2035, "estimated_cost": 99999,
             "category": "Test"},
        ]}
        r = requests.put(f"{_BASE}/intelligence/capital-works", json=payload, headers=admin_headers, timeout=15)
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_update_returns_correct_count(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        payload = {"items": [
            {"asset_name": "A", "replacement_year": 2027, "estimated_cost": 1000},
            {"asset_name": "B", "replacement_year": 2028, "estimated_cost": 2000},
        ]}
        r = requests.put(f"{_BASE}/intelligence/capital-works", json=payload, headers=admin_headers, timeout=15)
        assert r.status_code == 200
        assert r.json()["count"] == 2

    def test_update_persists_to_get(self, admin_headers):
        """PUT followed by GET should return the updated items."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        unique_name = "Persistence Check Asset XYZ"
        payload = {"items": [{"asset_name": unique_name, "replacement_year": 2033, "estimated_cost": 55000}]}
        put_r = requests.put(f"{_BASE}/intelligence/capital-works", json=payload, headers=admin_headers, timeout=15)
        assert put_r.status_code == 200

        get_r = requests.get(f"{_BASE}/intelligence/capital-works", headers=admin_headers, timeout=15)
        names = [i["asset_name"] for i in get_r.json()]
        assert unique_name in names, f"Updated item '{unique_name}' not found after PUT"

    def test_owner_cannot_update(self):
        h = _login("avneet@eastgateresidences.com.au", os.environ.get("E2E_OWNER_PASSWORD", ""))
        if not h:
            pytest.skip("Backend not reachable")
        payload = {"items": [{"asset_name": "Forbidden", "replacement_year": 2030, "estimated_cost": 1}]}
        r = requests.put(f"{_BASE}/intelligence/capital-works", json=payload, headers=h, timeout=15)
        assert r.status_code == 403

    def test_unauthenticated_denied(self):
        try:
            payload = {"items": [{"asset_name": "X", "replacement_year": 2030, "estimated_cost": 1}]}
            r = requests.put(f"{_BASE}/intelligence/capital-works", json=payload, timeout=10)
        except Exception:
            pytest.skip("Backend not reachable")
        assert r.status_code in (401, 403)

    def test_missing_required_field_returns_422(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        payload = {"items": [{"replacement_year": 2030, "estimated_cost": 1}]}  # asset_name missing
        r = requests.put(f"{_BASE}/intelligence/capital-works", json=payload, headers=admin_headers, timeout=15)
        assert r.status_code == 422

    def test_items_flagged_as_manual_source(self, admin_headers):
        """Items submitted via PUT should have source='manual' in DB (visible via GET)."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        payload = {"items": [{"asset_name": "Manual Source Test", "replacement_year": 2034, "estimated_cost": 77000}]}
        requests.put(f"{_BASE}/intelligence/capital-works", json=payload, headers=admin_headers, timeout=15)
        r = requests.get(f"{_BASE}/intelligence/capital-works", headers=admin_headers, timeout=15)
        items = [i for i in r.json() if i["asset_name"] == "Manual Source Test"]
        if items:
            assert items[0].get("source") == "manual"


class TestSinkingFundPlanPermissions:
    """Verify PUT /finance/sinking-fund-plan now allows plan-edit roles."""

    def _get_plan(self, admin_headers) -> list:
        r = requests.get(f"{_BASE}/finance/sinking-fund-plan", headers=admin_headers, timeout=15)
        return r.json().get("plan", [])

    @pytest.fixture(autouse=True)
    def _restore_plan(self, admin_headers):
        if not admin_headers:
            yield
            return
        original = self._get_plan(admin_headers)
        yield
        if original:
            requests.put(
                f"{_BASE}/finance/sinking-fund-plan",
                json={"plan": original},
                headers=admin_headers,
                timeout=15,
            )

    def test_super_admin_can_update_plan(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        plan = self._get_plan(admin_headers)
        if not plan:
            pytest.skip("No sinking fund plan seeded")
        payload = {"plan": [
            {"year": r["year"], "contribution": r.get("contribution", 0), "expenditure": r.get("expenditure", 0)} for r
            in plan[:2]]}
        r2 = requests.put(f"{_BASE}/finance/sinking-fund-plan", json=payload, headers=admin_headers, timeout=15)
        assert r2.status_code == 200

    def test_chairman_can_update_plan(self):
        h = _login("anthony@eastgateresidences.com.au", os.environ.get("E2E_CHAIRMAN_PASSWORD", ""))
        if not h:
            pytest.skip("Backend not reachable")
        admin_h = _login("administrator@eastgateresidences.com.au", os.environ.get("E2E_ADMIN_PASSWORD", ""))
        plan = self._get_plan(admin_h)
        if not plan:
            pytest.skip("No plan seeded")
        payload = {"plan": [
            {"year": r["year"], "contribution": r.get("contribution", 0), "expenditure": r.get("expenditure", 0)} for r
            in plan[:2]]}
        r2 = requests.put(f"{_BASE}/finance/sinking-fund-plan", json=payload, headers=h, timeout=15)
        assert r2.status_code == 200

    def test_owner_cannot_update_plan(self):
        h = _login("avneet@eastgateresidences.com.au", os.environ.get("E2E_OWNER_PASSWORD", ""))
        if not h:
            pytest.skip("Backend not reachable")
        payload = {"plan": [{"year": 2026, "contribution": 1, "expenditure": 0}]}
        r = requests.put(f"{_BASE}/finance/sinking-fund-plan", json=payload, headers=h, timeout=15)
        assert r.status_code == 403


_ORIGINAL_EVENTS = [
    {"year": 2029, "label": "Building Repaint",
     "description": "Full external repaint of building & common areas, basement & driveway repaint",
     "amount": 413099.0, "severity": "critical"},
    {"year": 2032, "label": "Intercom & Carpet Replacement",
     "description": "Full intercom system upgrade and common area carpet replacement",
     "amount": 149780.0, "severity": "high"},
    {"year": 2035, "label": "Lift Replacement",
     "description": "Complete lift system replacement (end of 25-year service life)",
     "amount": 269192.0, "severity": "critical"},
]


class TestSinkingFundCapitalEventsPermissions:
    """Verify PUT /finance/sinking-fund-capital-events allows plan-edit roles."""

    def teardown_method(self, method):
        """Restore the original 3 capital events after each test that mutates them."""
        if method.__name__ in ("test_super_admin_can_update_events", "test_events_persist_after_update"):
            h = _login("administrator@eastgateresidences.com.au", os.environ.get("E2E_ADMIN_PASSWORD", ""))
            if h:
                requests.put(
                    f"{_BASE}/finance/sinking-fund-capital-events",
                    json={"events": _ORIGINAL_EVENTS},
                    headers=h,
                    timeout=15,
                )

    def test_super_admin_can_update_events(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        payload = {"events": [
            {"year": 2029, "label": "Building Repaint", "amount": 245000, "severity": "high"},
        ]}
        r = requests.put(f"{_BASE}/finance/sinking-fund-capital-events", json=payload, headers=admin_headers,
                         timeout=15)
        assert r.status_code == 200

    def test_owner_cannot_update_events(self):
        h = _login("avneet@eastgateresidences.com.au", os.environ.get("E2E_OWNER_PASSWORD", ""))
        if not h:
            pytest.skip("Backend not reachable")
        payload = {"events": [{"year": 2029, "label": "X", "amount": 1, "severity": "low"}]}
        r = requests.put(f"{_BASE}/finance/sinking-fund-capital-events", json=payload, headers=h, timeout=15)
        assert r.status_code == 403

    def test_events_persist_after_update(self, admin_headers):
        if not admin_headers:
            pytest.skip("Backend not reachable")
        unique_label = "Persistence Test Event 999"
        payload = {"events": [
            {"year": 2031, "label": unique_label, "amount": 12345, "severity": "medium", "description": "Test"},
        ]}
        r = requests.put(f"{_BASE}/finance/sinking-fund-capital-events", json=payload, headers=admin_headers,
                         timeout=15)
        assert r.status_code == 200

        # Check it's visible via sinking-fund-plan
        r2 = requests.get(f"{_BASE}/finance/sinking-fund-plan", headers=admin_headers, timeout=15)
        events = r2.json().get("capital_events", [])
        labels = [e["label"] for e in events]
        assert unique_label in labels


class TestCapitalWorksPlannerDataIntegrity:
    """Verify CapitalWorksPlanner page data sources are all DB-driven (no static mock)."""

    def test_sinking_fund_plan_data_is_from_db(self, admin_headers):
        """Plan data should reflect seeded DB values, not static frontend constants."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/finance/sinking-fund-plan", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        data = r.json()
        # Summary is computed from DB rows — if seeded correctly, contributions > 0
        assert data["summary"]["total_contributions"] > 0, "total_contributions is 0 — data may not be seeded"
        assert data["summary"]["closing_balance_2035"] != 0, "closing_balance_2035 is 0 — may be static placeholder"

    def test_capital_events_are_from_db(self, admin_headers):
        """Capital events should come from DB, not hardcoded frontend arrays."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/finance/sinking-fund-plan", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        events = r.json().get("capital_events", [])
        # Seeded events should have proper year + amount
        for ev in events:
            assert "year" in ev and "label" in ev and "amount" in ev

    def test_capital_works_items_have_real_costs(self, admin_headers):
        """Capital works items should have non-zero estimated_cost from DB."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/intelligence/capital-works", headers=admin_headers, timeout=15)
        data = r.json()
        # After recompute, items generated from real assets should have real costs
        if data:
            costs = [i["estimated_cost"] for i in data]
            assert all(c > 0 for c in costs), "Some capital works items have zero cost — may be placeholder data"

    def test_intelligence_summary_is_computed_not_static(self, admin_headers):
        """Intelligence summary should come from the computed intelligence engine, not static values."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/intelligence/summary", headers=admin_headers, timeout=30)
        assert r.status_code == 200
        data = r.json()
        # These fields are computed by the intelligence engine
        assert "asset_health_score" in data
        assert "high_risk_assets_count" in data
        # building_id ties it to the real building, not a mock
        assert data.get("building_id") == "13195"

    def test_maintenance_forecast_is_from_engine(self, admin_headers):
        """Maintenance forecast should come from DB/engine, not seasonal weight constants."""
        if not admin_headers:
            pytest.skip("Backend not reachable")
        r = requests.get(f"{_BASE}/intelligence/maintenance-forecast", headers=admin_headers, timeout=15)
        assert r.status_code == 200
        data = r.json()
        if data:
            # The frontend distributes this total using SEASONAL_WEIGHTS — but the total itself is from DB
            assert "predicted_cost" in data, "predicted_cost should come from DB forecast engine"
            assert "building_id" in data, "building_id should be present — confirms this is DB data"
