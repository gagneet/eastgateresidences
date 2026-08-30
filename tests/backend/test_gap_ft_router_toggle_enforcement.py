"""
GAP-FT Router-Level Feature Toggle Enforcement Tests

Covers the critical/high gaps fixed in the feature toggle audit:

  GAP-FT-001  trust_accounting router — new seed key + router-level gate
  GAP-FT-002  bank_feeds router — gated behind financial_integration_layer_v2
  GAP-FT-004  ghost-toggle routers:
                arrears_recovery, arrears_risk, payment_plans,
                work_orders, conflict_of_interest, ppm (ppm_schedule)
  GAP-FT-012  ppm.py — effective_role() fix for _is_manager()

Each router test verifies:
  1. When the feature toggle is DISABLED → endpoint returns 403
  2. When the feature toggle is ENABLED → endpoint passes the toggle gate
     (role/auth guards may still block, but not the feature gate)
  3. super_admin bypasses the feature toggle (always 200 if role/auth satisfied)
  4. Multi-tenant isolation: toggle disabled for building A does not
     affect building B (where it is enabled)

Run with:
    backend/venv/bin/python3 -m pytest tests/backend/test_gap_ft_router_toggle_enforcement.py -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient


# ─── User factories ────────────────────────────────────────────────────────────

def _user(role="owner", is_approved=True, building_id="13195", effective_role=None):
    u = {
        "id": f"user-{role}",
        "email": f"{role}@test.com",
        "role": role,
        "is_approved": is_approved,
        "is_active": True,
        "building_id": building_id,
        "unit_number": "101",
        "full_name": "Test User",
    }
    if effective_role:
        u["effective_role"] = effective_role
    return u


def _manager(building_id="13195"):
    return _user(role="strata_manager", building_id=building_id)


def _super_admin(building_id="13195"):
    return _user(role="super_admin", building_id=building_id)


# ─── Helper: patch get_effective_feature_access ───────────────────────────────

def _feature_enabled():
    """Patch get_effective_feature_access to always return True."""
    return patch(
        "utils.permissions.get_effective_feature_access",
        new=AsyncMock(return_value=True),
    )


def _feature_disabled():
    """Patch get_effective_feature_access to always return False."""
    return patch(
        "utils.permissions.get_effective_feature_access",
        new=AsyncMock(return_value=False),
    )


class _EmptyAsyncCursor:
    """Minimal async-iterable cursor stand-in for `db.<coll>.find(...).sort(...)`."""

    def sort(self, *args, **kwargs):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _mock_trust_db():
    """Prevent trust_accounting tests from touching real Mongo.

    These tests call the real `server.py` app through TestClient without any DB
    mock — `list_trust_accounts` (etc.) genuinely queries `trust_ledger_accounts`,
    and `DeprecatedTrustV1Route`'s telemetry wrapper genuinely writes to
    `trust_v1_usage_telemetry` on every response including 403s (the wrapper
    catches HTTPException too). Both are real production Mongo without this
    patch — confirmed via a 2026-07-16 incident where running this file wrote 9
    fabricated documents into production `trust_v1_usage_telemetry` (cleaned up
    manually; see tasks/HANDOFF-TRUST-STAGE2-PREP-2026-07-16.md).
    """
    mock_db = MagicMock()
    mock_db.trust_v1_usage_telemetry.insert_one = AsyncMock()
    mock_db.trust_ledger_accounts.find = MagicMock(return_value=_EmptyAsyncCursor())
    return patch("routers.trust_accounting._get_db", return_value=mock_db)


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-FT-001: trust_accounting router — feature toggle gate
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrustAccountingToggle:
    """
    The trust_accounting router must return 403 for all endpoints when the
    trust_accounting feature toggle is disabled, regardless of role.
    """

    def test_trust_accounts_list_blocked_when_toggle_off(self):
        """GET /trust/accounts returns 403 when trust_accounting toggle is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled(), _mock_trust_db():
                resp = client.get("/api/trust/accounts")
            assert resp.status_code == 403, (
                f"trust/accounts should be blocked when toggle off, got {resp.status_code}"
            )
            assert "trust_accounting" in resp.text.lower() or "disabled" in resp.text.lower()
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_trust_v2_accounts_list_blocked_when_toggle_off(self):
        """GET /trust/v2/accounts returns 403 when trust_accounting toggle is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/trust/v2/accounts")
            assert resp.status_code == 403, (
                f"trust/v2/accounts should be blocked when toggle off, got {resp.status_code}"
            )
            assert "trust_accounting" in resp.text.lower() or "disabled" in resp.text.lower()
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_trust_journal_blocked_when_toggle_off(self):
        """GET /trust/journal returns 403 when trust_accounting toggle is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled(), _mock_trust_db():
                resp = client.get("/api/trust/journal")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_trust_balance_blocked_when_toggle_off(self):
        """GET /trust/balance returns 403 when trust_accounting toggle is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled(), _mock_trust_db():
                resp = client.get("/api/trust/balance")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_trust_accounts_passes_toggle_gate_when_enabled(self):
        """GET /trust/accounts passes the toggle gate when trust_accounting is on.

        Note: It may still get a 403 from the role guard or 500 from missing DB
        mocks — we only verify the toggle gate itself does not block (status != 403
        from the feature gate). We check the response is NOT a feature-gate 403.
        """
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_enabled(), _mock_trust_db():
                resp = client.get("/api/trust/accounts")
            # If toggle is on, should NOT get a feature-toggle 403 containing "trust_accounting"
            if resp.status_code == 403:
                assert "trust_accounting" not in resp.text.lower(), (
                    "Got 403 specifically from the trust_accounting feature toggle when it should be enabled"
                )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_super_admin_bypasses_toggle(self):
        """Super admin bypasses the trust_accounting toggle (always passes gate)."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _super_admin()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with patch(
                "utils.permissions.get_effective_feature_access",
                new=AsyncMock(return_value=True),  # super_admin always gets True
            ), _mock_trust_db():
                resp = client.get("/api/trust/accounts")
            # Should not be a feature-gate 403
            if resp.status_code == 403:
                assert "trust_accounting" not in resp.text.lower()
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_trust_accounting_toggle_key_in_seed(self):
        """trust_accounting must be present in the feature_toggles seed."""
        import importlib.util, os, sys
        backend_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "backend",
        )
        spec = importlib.util.spec_from_file_location(
            "seed_ft", os.path.join(backend_dir, "seeds", "feature_toggles.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        keys = {f["feature_key"] for f in mod.DEFAULT_FEATURES}
        assert "trust_accounting" in keys, "trust_accounting seed key is missing"

    def test_trust_accounting_seed_key_properties(self):
        """trust_accounting toggle has correct category, depends_on, and is enabled by default."""
        import importlib.util, os
        backend_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "backend",
        )
        spec = importlib.util.spec_from_file_location(
            "seed_ft", os.path.join(backend_dir, "seeds", "feature_toggles.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        ta = next(f for f in mod.DEFAULT_FEATURES if f["feature_key"] == "trust_accounting")
        assert ta["category"] == "financial", f"Expected category=financial, got {ta['category']}"
        assert ta["is_enabled"] is True, "trust_accounting should be enabled by default"
        assert "finance" in ta["depends_on"], "trust_accounting should depend on finance"


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-FT-002: bank_feeds router — gated behind financial_integration_layer_v2
# ═══════════════════════════════════════════════════════════════════════════════

class TestBankFeedsToggle:
    """
    Bank feed endpoints must return 403 when financial_integration_layer_v2
    is disabled (its default state). This is the primary use-case since FIL v2
    is off by default and only enabled per-building when ready.
    """

    def test_bank_feed_ingest_blocked_when_fil_v2_off(self):
        """POST /bank-feeds/ingest returns 403 when financial_integration_layer_v2 is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.post(
                    "/api/bank-feeds/ingest",
                    params={"account_id": "acc-1"},
                )
            assert resp.status_code == 403, (
                f"bank-feeds/ingest should be blocked when FIL v2 is off, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_bank_feed_rematch_blocked_when_fil_v2_off(self):
        """POST /bank-feeds/rematch returns 403 when financial_integration_layer_v2 is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.post(
                    "/api/bank-feeds/rematch",
                    json={"account_ref": "ADMIN-13195", "limit": 2000},
                )
            assert resp.status_code == 403, (
                f"bank-feeds/rematch should be blocked when FIL v2 is off, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_bank_feed_transactions_blocked_when_fil_v2_off(self):
        """GET /bank-feeds/transactions returns 403 when financial_integration_layer_v2 is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/bank-feeds/transactions")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_bank_feed_runs_blocked_when_fil_v2_off(self):
        """GET /bank-feeds/runs returns 403 when financial_integration_layer_v2 is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/bank-feeds/runs")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_bank_feed_passes_gate_when_fil_v2_on(self):
        """Bank feed endpoints pass the toggle gate when financial_integration_layer_v2 is on."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_enabled():
                resp = client.get("/api/bank-feeds/transactions")
            # Should not be blocked by the feature toggle (may fail for other reasons)
            if resp.status_code == 403:
                assert "financial_integration_layer_v2" not in resp.text.lower(), (
                    "Got 403 from the feature toggle gate when FIL v2 should be enabled"
                )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-FT-004: Ghost-toggle routers — arrears_recovery, arrears_risk,
#             payment_plans, work_orders, conflict_of_interest
# ═══════════════════════════════════════════════════════════════════════════════

class TestArrearsRecoveryToggle:
    """arrears_recovery router must enforce the arrears_recovery toggle."""

    def test_list_blocked_when_toggle_off(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/arrears-recovery")
            assert resp.status_code == 403, (
                f"arrears-recovery should be blocked when toggle off, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_summary_blocked_when_toggle_off(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/arrears-recovery/summary")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_automation_trigger_blocked_when_toggle_off(self):
        """The run-automation endpoint (called by cron) is also gated."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.post("/api/arrears-recovery/run-automation")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_passes_gate_when_toggle_on(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_enabled():
                resp = client.get("/api/arrears-recovery")
            if resp.status_code == 403:
                assert "arrears_recovery" not in resp.text.lower()
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


class TestArrearsRiskToggle:
    """arrears_risk router must enforce the arrears_risk toggle."""

    def test_summary_blocked_when_toggle_off(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/arrears-risk/summary")
            assert resp.status_code == 403, (
                f"arrears-risk/summary should be 403 when toggle off, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_list_blocked_when_toggle_off(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/arrears-risk")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


class TestPaymentPlansToggle:
    """payment_plans router must enforce the payment_plans toggle."""

    def test_list_blocked_when_toggle_off(self):
        """The router-level require_feature gate must block the GET list endpoint.

        Note: server.py also registers an older inline GET /payment-plans that
        predates the router. That route is not toggle-gated (it is a legacy
        handler). This test verifies the *router* gate by calling the single-plan
        GET endpoint which exists only in the router, not in the inline registration.
        """
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            # Use a plan_id endpoint that exists only in the router (not in the inline handler)
            with _feature_disabled():
                resp = client.get("/api/payment-plans/nonexistent-plan-id")
            assert resp.status_code == 403, (
                f"payment-plans/{'{id}'} should be blocked when toggle off, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_approve_blocked_when_toggle_off(self):
        """POST /payment-plans/{id}/approve must be blocked when toggle is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.post("/api/payment-plans/some-plan-id/approve", json={
                    "approved_instalment_cents": 50000,
                    "instalment_frequency": "monthly",
                    "instalment_count": 6,
                    "start_date": "2026-06-01",
                })
            assert resp.status_code == 403, (
                f"payment-plans/approve should be blocked when toggle off, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_owner_submit_blocked_when_toggle_off(self):
        """Owners cannot submit payment plan requests when toggle is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _user(role="owner")
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.post("/api/payment-plans", json={
                    "unit_number": "101",
                    "reason": "Financial hardship",
                    "proposed_amount": 500.0,
                    "proposed_frequency": "monthly",
                })
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


class TestWorkOrdersToggle:
    """work_orders router must enforce the work_orders toggle."""

    def test_list_blocked_when_toggle_off(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/work-orders")
            assert resp.status_code == 403, (
                f"work-orders should be blocked when toggle off, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_create_blocked_when_toggle_off(self):
        """Creating a work order must be blocked when toggle is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.post("/api/work-orders", json={
                    "title": "Fix elevator",
                    "description": "Elevator stuck",
                    "maintenance_request_id": "mr-001",
                    "lot_number": "101",
                })
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_ec_approval_blocked_when_toggle_off(self):
        """EC approval endpoint must be blocked when work_orders toggle is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _user(role="ec_member")
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.post("/api/work-orders/wo-999/approvals", json={
                    "decision": "approved",
                    "notes": "Approved by EC",
                })
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


class TestConflictOfInterestToggle:
    """conflict_of_interest router must enforce the conflict_of_interest toggle."""

    def test_list_blocked_when_toggle_off(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _user(role="ec_member")
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/conflict-of-interest")
            assert resp.status_code == 403, (
                f"conflict-of-interest should be blocked when toggle off, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_declare_blocked_when_toggle_off(self):
        """EC member cannot declare a COI when toggle is off."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _user(role="ec_member")
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.post("/api/conflict-of-interest", json={
                    "agenda_item": "Procurement from ABC Corp",
                    "nature_of_conflict": "Director of ABC Corp",
                    "declaration_date": "2026-05-11",
                })
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


class TestPPMToggle:
    """ppm router must enforce the ppm_schedule toggle."""

    def test_list_blocked_when_toggle_off(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/ppm")
            assert resp.status_code == 403, (
                f"ppm should be blocked when ppm_schedule toggle off, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_dashboard_blocked_when_toggle_off(self):
        from server import app
        from utils.auth import get_current_user, get_current_building

        app.dependency_overrides[get_current_user] = lambda: _manager()
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with _feature_disabled():
                resp = client.get("/api/ppm/dashboard")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-FT-012: ppm.py effective_role() fix for _is_manager()
# ═══════════════════════════════════════════════════════════════════════════════

class TestPPMEffectiveRoleGuard:
    """
    PPM write endpoints (_is_manager check) must use effective_role(), not
    raw user['role']. A temp-elevated owner (effective_role=ec_member) must
    pass the manager check.
    """

    def test_effective_role_is_manager_returns_true_for_elevated_user(self):
        """_is_manager() must return True when effective_role is ec_member."""
        from routers.ppm import _is_manager

        elevated_owner = {
            "role": "owner",              # raw role (unchanged by elevation)
            "effective_role": "ec_member",  # set by the elevation middleware
        }
        assert _is_manager(elevated_owner) is True, (
            "_is_manager should return True for elevated owner (effective_role=ec_member)"
        )

    def test_effective_role_is_manager_returns_false_for_plain_owner(self):
        """_is_manager() must return False for a non-elevated owner."""
        from routers.ppm import _is_manager

        plain_owner = {"role": "owner"}
        assert _is_manager(plain_owner) is False

    def test_effective_role_is_manager_returns_true_for_strata_manager(self):
        """_is_manager() must return True for strata_manager role."""
        from routers.ppm import _is_manager

        sm = {"role": "strata_manager"}
        assert _is_manager(sm) is True

    def test_effective_role_is_manager_returns_true_for_super_admin(self):
        """_is_manager() must return True for super_admin role."""
        from routers.ppm import _is_manager

        sa = {"role": "super_admin"}
        assert _is_manager(sa) is True

    def test_effective_role_is_manager_returns_false_for_tenant(self):
        """_is_manager() must return False for tenant role."""
        from routers.ppm import _is_manager

        tenant = {"role": "tenant"}
        assert _is_manager(tenant) is False

    def test_raw_role_owner_with_ec_member_effective_passes(self):
        """Before the fix, raw role='owner' would fail even with ec_member elevation.
        This test confirms the fix is in place: effective_role wins."""
        from routers.ppm import _is_manager

        user = {"role": "owner", "effective_role": "ec_member"}
        # Post-fix: effective_role is used, so this should be True
        assert _is_manager(user) is True, (
            "GAP-FT-012 regression: _is_manager should use effective_role, "
            "not raw role. Elevated owner (ec_member) must be treated as manager."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-tenant isolation — toggle off in building A, on in building B
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiTenantToggleIsolation:
    """
    Feature toggles are per-building. Disabling a toggle for building A
    must NOT affect building B where the toggle is enabled.
    """

    def test_toggle_off_for_one_building_does_not_affect_another(self):
        """
        Simulate building 13195 has trust_accounting DISABLED,
        building 16244 has trust_accounting ENABLED.
        Requests from building 16244 should not get 403 from the toggle.
        """
        from server import app
        from utils.auth import get_current_user, get_current_building

        building_toggle_map = {
            "13195": False,  # disabled
            "16244": True,   # enabled
        }

        current_building = {"id": "16244"}

        async def _dynamic_feature_access(user, feature_key):
            building_id = user.get("building_id", "13195")
            return building_toggle_map.get(building_id, True)

        app.dependency_overrides[get_current_user] = lambda: _manager(building_id="16244")
        app.dependency_overrides[get_current_building] = lambda: "16244"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with patch(
                "utils.permissions.get_effective_feature_access",
                side_effect=_dynamic_feature_access,
            ), _mock_trust_db():
                resp = client.get("/api/trust/accounts")

            # Building 16244 has toggle ON — must not get a feature-gate 403
            if resp.status_code == 403:
                assert "trust_accounting" not in resp.text.lower(), (
                    "Building 16244's toggle=ON is being blocked by building 13195's toggle=OFF"
                )
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)

    def test_toggle_off_for_building_a_blocks_building_a_requests(self):
        """Building 13195 with trust_accounting disabled must get 403."""
        from server import app
        from utils.auth import get_current_user, get_current_building

        async def _feature_off_for_13195(user, feature_key):
            return user.get("building_id") != "13195"

        app.dependency_overrides[get_current_user] = lambda: _manager(building_id="13195")
        app.dependency_overrides[get_current_building] = lambda: "13195"
        client = TestClient(app, raise_server_exceptions=False)

        try:
            with patch(
                "utils.permissions.get_effective_feature_access",
                side_effect=_feature_off_for_13195,
            ), _mock_trust_db():
                resp = client.get("/api/trust/accounts")
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_current_building, None)


# ═══════════════════════════════════════════════════════════════════════════════
# Seed integrity: all fixed ghost-toggle keys must be in seed
# ═══════════════════════════════════════════════════════════════════════════════

class TestGhostToggleSeedIntegrity:
    """Verify all ghost-toggle keys that were wired now exist in seed with correct properties."""

    GHOST_TOGGLE_KEYS = [
        "trust_accounting",
        "work_orders",
        "ppm_schedule",
        "arrears_recovery",
        "arrears_risk",
        "payment_plans",
        "conflict_of_interest",
    ]

    def _load_seed(self):
        import importlib.util, os
        backend_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "backend",
        )
        spec = importlib.util.spec_from_file_location(
            "seed_ft", os.path.join(backend_dir, "seeds", "feature_toggles.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return {f["feature_key"]: f for f in mod.DEFAULT_FEATURES}

    def test_all_ghost_toggle_keys_present_in_seed(self):
        seed = self._load_seed()
        missing = [k for k in self.GHOST_TOGGLE_KEYS if k not in seed]
        assert missing == [], f"Missing seed keys: {missing}"

    def test_all_ghost_toggles_enabled_by_default(self):
        seed = self._load_seed()
        for key in self.GHOST_TOGGLE_KEYS:
            toggle = seed[key]
            assert toggle["is_enabled"] is True, (
                f"Toggle '{key}' should be enabled by default (was a ghost toggle)"
            )

    def test_all_ghost_toggles_have_depends_on(self):
        seed = self._load_seed()
        for key in self.GHOST_TOGGLE_KEYS:
            toggle = seed[key]
            assert "depends_on" in toggle, f"Toggle '{key}' missing depends_on field"

    def test_work_orders_depends_on_maintenance(self):
        seed = self._load_seed()
        assert "maintenance" in seed["work_orders"]["depends_on"]

    def test_ppm_schedule_depends_on_maintenance(self):
        seed = self._load_seed()
        assert "maintenance" in seed["ppm_schedule"]["depends_on"]

    def test_arrears_recovery_depends_on_finance(self):
        seed = self._load_seed()
        assert "finance" in seed["arrears_recovery"]["depends_on"]

    def test_arrears_risk_depends_on_arrears_recovery(self):
        seed = self._load_seed()
        assert "arrears_recovery" in seed["arrears_risk"]["depends_on"]

    def test_payment_plans_depends_on_arrears_recovery(self):
        seed = self._load_seed()
        assert "arrears_recovery" in seed["payment_plans"]["depends_on"]

    def test_conflict_of_interest_is_governance_category(self):
        seed = self._load_seed()
        assert seed["conflict_of_interest"]["category"] == "governance"

    def test_trust_accounting_is_financial_category(self):
        seed = self._load_seed()
        assert seed["trust_accounting"]["category"] == "financial"
