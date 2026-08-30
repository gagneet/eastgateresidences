"""
Sentinel Security Tests: Feature Toggle & Finance Endpoint Hardening

Covers:
- require_feature uses get_current_user (not get_approved_user) so service_provider
  and other non-resident roles are NOT blocked at the feature-gate level.
- require_approved_feature raises 403 for unapproved owners/tenants and service_providers.
- GET /finance/building-overview returns 403 for unapproved users, 200 for approved users.
- GET /finance/levy-kpi returns 403 for unapproved users, 200 for approved users.

Run with:
    backend/venv/bin/python3 -m pytest tests/backend/test_sentinel_feature_toggle_hardening.py -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _user(role="owner", is_approved=True, unit="101"):
    return {
        "id": f"user-{role}",
        "email": f"{role}@test.com",
        "role": role,
        "is_approved": is_approved,
        "is_active": True,
        "building_id": "13195",
        "unit_number": unit,
        "full_name": "Test User",
    }


def _service_provider():
    return {
        "id": "user-sp",
        "email": "supplier@vendor.com",
        "role": "service_provider",
        "is_approved": False,
        "is_active": True,
        "building_id": "13195",
        "unit_number": None,
        "full_name": "Supplier Co",
    }


# ─── require_feature: service_provider must NOT be blocked ────────────────────

class TestRequireFeatureAllowsServiceProvider:
    """
    require_feature is gated by get_current_user (not get_approved_user).
    A service_provider user with the feature enabled must receive 200, not 403.
    """

    def _build_test_app(self):
        from utils.permissions import require_feature

        mini = FastAPI()

        @mini.get("/test-feature")
        async def _test_endpoint(u: dict = Depends(require_feature("ap_automation"))):
            return {"ok": True}

        return mini

    @patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True))
    def test_service_provider_gets_200_when_feature_enabled(self):
        """service_provider must not be blocked when feature is on."""
        from utils.auth import get_current_user

        app = self._build_test_app()
        app.dependency_overrides[get_current_user] = lambda: _service_provider()

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-feature")
        assert resp.status_code == 200, (
            f"service_provider should get 200 when feature is enabled, got {resp.status_code}: {resp.text}"
        )

    @patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=False))
    def test_any_user_gets_403_when_feature_disabled(self):
        """Any user gets 403 when the feature toggle is off."""
        from utils.auth import get_current_user

        app = self._build_test_app()
        app.dependency_overrides[get_current_user] = lambda: _user("owner")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-feature")
        assert resp.status_code == 403

    @patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True))
    def test_approved_owner_gets_200_when_feature_enabled(self):
        """Approved owner must pass through when feature is on."""
        from utils.auth import get_current_user

        app = self._build_test_app()
        app.dependency_overrides[get_current_user] = lambda: _user("owner", is_approved=True)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/test-feature")
        assert resp.status_code == 200


# ─── require_approved_feature: unapproved users must be blocked ───────────────

class TestRequireApprovedFeatureBlocksUnapproved:
    """
    require_approved_feature uses get_approved_user.
    Unapproved owners, unapproved tenants, and service_providers must get 403.
    """

    def _build_test_app(self):
        from utils.permissions import require_approved_feature

        mini = FastAPI()

        @mini.get("/community-feature")
        async def _test_endpoint(u: dict = Depends(require_approved_feature("chat"))):
            return {"ok": True}

        return mini

    @patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True))
    def test_unapproved_owner_gets_403(self):
        """Unapproved owner must get 403 from the approval gate."""
        from utils.auth import get_current_user

        app = self._build_test_app()
        app.dependency_overrides[get_current_user] = lambda: _user("owner", is_approved=False)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/community-feature")
        assert resp.status_code == 403, (
            f"Unapproved owner should be blocked, got {resp.status_code}"
        )

    @patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True))
    def test_service_provider_gets_403_on_community_feature(self):
        """service_provider is not an approved resident and must be blocked."""
        from utils.auth import get_current_user

        app = self._build_test_app()
        app.dependency_overrides[get_current_user] = lambda: _service_provider()

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/community-feature")
        assert resp.status_code == 403

    @patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True))
    def test_approved_owner_gets_200(self):
        """Approved owner gets 200 when feature is enabled."""
        from utils.auth import get_current_user

        app = self._build_test_app()
        app.dependency_overrides[get_current_user] = lambda: _user("owner", is_approved=True)

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/community-feature")
        assert resp.status_code == 200

    @patch("utils.permissions.get_effective_feature_access", new=AsyncMock(return_value=True))
    def test_admin_gets_200_regardless_of_is_approved_flag(self):
        """Administrative roles are auto-approved; is_approved field is irrelevant."""
        from utils.auth import get_current_user

        admin = {**_user("super_admin", is_approved=False), "role": "super_admin"}
        app = self._build_test_app()
        app.dependency_overrides[get_current_user] = lambda: admin

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/community-feature")
        assert resp.status_code == 200


# ─── Finance endpoints: /finance/building-overview ────────────────────────────

class TestBuildingOverviewApprovalGate:
    """
    GET /finance/building-overview uses get_approved_user.
    Unapproved users must receive 403; approved users/admins receive 200.
    """

    def _configure_db_mock(self, mock_db: MagicMock) -> None:
        """Configure mock_db with every collection the building-overview handler calls.

        Three DB calls are made:
          1. annual_levies.find_one — resolve the current financial year
          2. unit_levy_ledger.aggregate (×2) — levy totals and units_in_arrears_count
          3. annual_levies.find().sort().to_list — fund-trend sparkline (commit 22c327de)
        building_levy_portal is NOT called by this handler and is intentionally absent.
        """
        mock_db.annual_levies.find_one = AsyncMock(return_value={"year": "2026"})
        # No year query param is passed below — get_building_fund_overview resolves via
        # _resolve_default_levy_year(), which calls db.annual_levies.distinct(...) rather
        # than find_one(...sort...). See routers/finance.py module note above
        # get_available_years for why (a premature next-year annual_levies row must never
        # be picked as "current").
        mock_db.annual_levies.distinct = AsyncMock(return_value=["2026"])

        mock_trend = MagicMock()
        mock_trend.sort = MagicMock(return_value=mock_trend)
        # .limit(8) was added to the production chain after this test was
        # written (find().sort().limit(8).to_list(8)) — without this mock,
        # .limit() returns an unconfigured MagicMock whose .to_list() is not
        # awaitable, raising inside the route and turning every 200-expecting
        # test in this class into a 500.
        mock_trend.limit = MagicMock(return_value=mock_trend)
        mock_trend.to_list = AsyncMock(return_value=[])
        mock_db.annual_levies.find = MagicMock(return_value=mock_trend)

        mock_agg = MagicMock()
        mock_agg.to_list = AsyncMock(return_value=[])
        mock_db.unit_levy_ledger.aggregate = MagicMock(return_value=mock_agg)

    def test_unapproved_owner_gets_403(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _user("owner", is_approved=False)
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            resp = client.get("/api/finance/building-overview")
            assert resp.status_code == 403, (
                f"Unapproved owner must be blocked from building-overview, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_service_provider_gets_403(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _service_provider()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            resp = client.get("/api/finance/building-overview")
            assert resp.status_code == 403, (
                f"service_provider must be blocked from building-overview, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    @patch("routers.finance._get_general_settings", new_callable=AsyncMock)
    @patch("routers.finance.db")
    def test_approved_owner_gets_200(self, mock_db, mock_settings):
        from server import app
        from utils.auth import get_current_user, get_current_building

        self._configure_db_mock(mock_db)
        mock_settings.return_value = {"financial_year_start_month": 1}

        app.dependency_overrides[get_current_user] = lambda: _user("owner", is_approved=True)
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            resp = client.get("/api/finance/building-overview")
            assert resp.status_code == 200, (
                f"Approved owner should get 200 from building-overview, got {resp.status_code}: {resp.text}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    @patch("routers.finance._get_general_settings", new_callable=AsyncMock)
    @patch("routers.finance.db")
    def test_admin_gets_200(self, mock_db, mock_settings):
        from server import app
        from utils.auth import get_current_user, get_current_building

        self._configure_db_mock(mock_db)
        mock_settings.return_value = {"financial_year_start_month": 1}

        admin = {**_user("super_admin", is_approved=False), "role": "super_admin"}
        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            resp = client.get("/api/finance/building-overview")
            assert resp.status_code == 200, (
                f"Admin should always get 200 from building-overview, got {resp.status_code}: {resp.text}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


# ─── Finance endpoints: /finance/levy-kpi ─────────────────────────────────────

class TestLevyKpiApprovalGate:
    """
    GET /finance/levy-kpi uses get_approved_user.
    Unapproved users must receive 403; approved users/admins receive 200.
    """

    def test_unapproved_owner_gets_403(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _user("owner", is_approved=False)
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            resp = client.get("/api/finance/levy-kpi")
            assert resp.status_code == 403, (
                f"Unapproved owner must be blocked from levy-kpi, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_service_provider_gets_403(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _service_provider()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            resp = client.get("/api/finance/levy-kpi")
            assert resp.status_code == 403, (
                f"service_provider must be blocked from levy-kpi, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    @patch("routers.finance._get_general_settings", new_callable=AsyncMock)
    @patch("routers.finance.db")
    def test_approved_owner_gets_200(self, mock_db, mock_settings):
        from server import app
        from utils.auth import get_current_user, get_current_building

        # Mock _get_general_settings (imported from services.settings_service)
        mock_settings.return_value = {"total_uoe": 100}

        # Mock annual levies
        mock_db.annual_levies.find_one = AsyncMock(return_value={
            "year": "2026", "admin_levy": 200000, "sinking_levy": 100000,
            "admin_fund": {"closing_balance": 50000},
            "sinking_fund": {"closing_balance": 150000},
            "building_id": "13195",
        })
        # No year query param is passed below (matches LevyKpiDialog.tsx before
        # selectedYear loads) — get_levy_kpi resolves via _resolve_default_levy_year(),
        # which calls db.annual_levies.distinct(...) rather than find_one(...sort...).
        mock_db.annual_levies.distinct = AsyncMock(return_value=["2026"])
        # Mock buildings.find_one (needed for trust_config)
        mock_db.buildings.find_one = AsyncMock(return_value={"trust_config": None})
        # unit_levy_ledger.find() used in second parallel block
        mock_ledger_cursor = MagicMock()
        mock_ledger_cursor.to_list = AsyncMock(return_value=[])
        mock_db.unit_levy_ledger.find = MagicMock(return_value=mock_ledger_cursor)
        # expense_transactions.aggregate() (two calls: admin + sinking)
        mock_exp_agg = MagicMock()
        mock_exp_agg.to_list = AsyncMock(return_value=[])
        mock_db.expense_transactions.aggregate = MagicMock(return_value=mock_exp_agg)
        # Units list for per-lot detail
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_db.units.find = MagicMock(return_value=mock_cursor)

        app.dependency_overrides[get_current_user] = lambda: _user("owner", is_approved=True)
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            resp = client.get("/api/finance/levy-kpi")
            assert resp.status_code == 200, (
                f"Approved owner should get 200 from levy-kpi, got {resp.status_code}: {resp.text}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    @patch("routers.finance._get_general_settings", new_callable=AsyncMock)
    @patch("routers.finance.db")
    def test_admin_gets_200(self, mock_db, mock_settings):
        from server import app
        from utils.auth import get_current_user, get_current_building

        mock_settings.return_value = {"total_uoe": 100}

        mock_db.annual_levies.find_one = AsyncMock(return_value={
            "year": "2026", "admin_levy": 200000, "sinking_levy": 100000,
            "admin_fund": {"closing_balance": 50000},
            "sinking_fund": {"closing_balance": 150000},
            "building_id": "13195",
        })
        # No year query param — resolves via _resolve_default_levy_year(), which calls
        # db.annual_levies.distinct(...) rather than find_one(...sort...).
        mock_db.annual_levies.distinct = AsyncMock(return_value=["2026"])
        mock_db.buildings.find_one = AsyncMock(return_value={"trust_config": None})
        mock_ledger_cursor = MagicMock()
        mock_ledger_cursor.to_list = AsyncMock(return_value=[])
        mock_db.unit_levy_ledger.find = MagicMock(return_value=mock_ledger_cursor)
        mock_exp_agg = MagicMock()
        mock_exp_agg.to_list = AsyncMock(return_value=[])
        mock_db.expense_transactions.aggregate = MagicMock(return_value=mock_exp_agg)
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_db.units.find = MagicMock(return_value=mock_cursor)

        admin = {**_user("super_admin", is_approved=False), "role": "super_admin"}
        app.dependency_overrides[get_current_user] = lambda: admin
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            resp = client.get("/api/finance/levy-kpi")
            assert resp.status_code == 200, (
                f"Admin should always get 200 from levy-kpi, got {resp.status_code}: {resp.text}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)
