#!/usr/bin/env python3
"""
Backend Tests: Building Occupancy Intelligence

Tests cover:
  1. Classification logic (occupancy_service.py)
  2. Summary API aggregation
  3. Multi-tenant isolation (building A cannot see building B data)
  4. Recompute engine (unit tests with mock DB)
  5. API contract (requires running backend on localhost:8003)

Run unit tests only (no backend needed):
    cd /path/to/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_occupancy.py -v -k "not ApiContract"

Run all tests (requires backend running on localhost:8003):
    RUN_INTEGRATION_TESTS=1 pytest tests/backend/test_occupancy.py -v

Credentials for integration tests are read from environment variables:
    TEST_ADMIN_EMAIL / TEST_ADMIN_PASSWORD
    TEST_OWNER_EMAIL / TEST_OWNER_PASSWORD
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Add backend to path ───────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))


# ═════════════════════════════════════════════════════════════════════════════
# 1. CLASSIFICATION LOGIC TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestClassifyOccupancy:
    """Unit tests for services/occupancy_service.py::classify_occupancy()."""

    def _classify(self, lot=None, certs=None, owner=None, building_addr=None):
        from services.occupancy_service import classify_occupancy
        return classify_occupancy(
            lot=lot or {"unit_number": "TH001"},
            rental_certificates=certs or [],
            owner_data=owner or {},
            building_address=building_addr,
        )

    # ── Rule 1: active rental certificate → tenant ──────────────────────────
    def test_active_cert_classifies_as_tenant(self):
        status, confidence, sources = self._classify(
            certs=[{"status": "issued", "cert_number": "RC-2026-0001"}]
        )
        assert status == "tenant"
        assert confidence == 0.95
        assert "rental_certificate" in sources

    def test_non_issued_cert_does_not_trigger_tenant(self):
        # An expired or superseded cert should NOT classify as tenant
        status, confidence, sources = self._classify(
            certs=[{"status": "superseded", "cert_number": "RC-2025-0001"}]
        )
        assert status != "tenant"

    def test_multiple_certs_only_active_counts(self):
        # One superseded + one issued → still tenant
        status, confidence, sources = self._classify(
            certs=[
                {"status": "superseded"},
                {"status": "issued", "cert_number": "RC-2026-0002"},
            ]
        )
        assert status == "tenant"

    # ── Rule 2: address match → owner_occupied ──────────────────────────────
    def test_address_match_classifies_owner_occupied(self):
        status, confidence, sources = self._classify(
            owner={"contact_address": "12 Denman Prospect Way Denman Prospect ACT 2611"},
            building_addr="12 Denman Prospect Way Denman Prospect",
        )
        assert status == "owner_occupied"
        assert confidence >= 0.85
        assert "address_match" in sources

    def test_no_address_does_not_trigger_owner_occupied(self):
        status, _, _ = self._classify(owner={}, building_addr="12 Denman Prospect Way")
        assert status != "owner_occupied"

    def test_weak_address_match_does_not_trigger(self):
        # Only 1 shared token — should not match
        status, _, _ = self._classify(
            owner={"contact_address": "ACT"},
            building_addr="12 Denman Prospect Way Denman Prospect ACT",
        )
        assert status != "owner_occupied"

    # ── Rule 3: agent indicator → investor_unknown (~0.70) ──────────────────
    def test_agent_in_owner_name_classifies_investor(self):
        status, confidence, sources = self._classify(
            owner={"owner_name": "Smith Real Estate Pty Ltd"}
        )
        assert status == "investor_unknown"
        assert abs(confidence - 0.70) < 0.01
        assert "agent_indicator" in sources

    def test_property_management_indicator(self):
        status, _, sources = self._classify(
            owner={"mailing_address": "c/- ABC Property Management, Civic ACT"}
        )
        assert status == "investor_unknown"
        assert "agent_indicator" in sources

    # ── Rule 4: fallback ─────────────────────────────────────────────────────
    def test_fallback_classifies_investor_unknown_low_confidence(self):
        status, confidence, sources = self._classify()
        assert status == "investor_unknown"
        assert confidence == 0.50
        assert "fallback" in sources

    # ── Priority: cert beats address match ───────────────────────────────────
    def test_active_cert_beats_address_match(self):
        # Even if address matches, a cert takes priority
        status, confidence, sources = self._classify(
            certs=[{"status": "issued"}],
            owner={"contact_address": "12 Denman Prospect Way Denman Prospect ACT"},
            building_addr="12 Denman Prospect Way Denman Prospect",
        )
        assert status == "tenant"
        assert confidence == 0.95

    # ── Registered user address match ────────────────────────────────────────
    def test_registered_user_address_match(self):
        status, confidence, sources = self._classify(
            owner={
                "_registered_user": {
                    "address": "7 Nectarine Street Denman Prospect ACT 2611 Unit TH017",
                }
            },
            building_addr="7 Nectarine Street Denman Prospect ACT",
        )
        assert status == "owner_occupied"
        assert confidence >= 0.80
        assert "user_address_match" in sources

    # ── Rule 3a: registered user with role=tenant → tenant ───────────────────
    def test_registered_tenant_role_classifies_as_tenant(self):
        """A user registered with role='tenant' for a unit → tenant, no cert needed."""
        status, confidence, sources = self._classify(
            owner={"_registered_user": {"role": "tenant", "unit_number": "TH075"}},
        )
        assert status == "tenant"
        assert confidence == 0.85
        assert "registered_tenant" in sources

    def test_registered_tenant_beats_address_match(self):
        """role=tenant takes priority over address-match owner_occupied."""
        status, confidence, sources = self._classify(
            owner={
                "_registered_user": {
                    "role": "tenant",
                    "address": "12 Denman Prospect Way Denman Prospect ACT",
                }
            },
            building_addr="12 Denman Prospect Way Denman Prospect ACT",
        )
        assert status == "tenant"
        assert "registered_tenant" in sources

    def test_registered_owner_role_does_not_trigger_tenant(self):
        """A user registered with role='owner' should NOT be classified as tenant."""
        status, _, _ = self._classify(
            owner={"_registered_user": {"role": "owner", "unit_number": "UA063"}},
        )
        assert status != "tenant"


# ═════════════════════════════════════════════════════════════════════════════
# 2. RECOMPUTE ENGINE TESTS (unit tests with mock DB)
# ═════════════════════════════════════════════════════════════════════════════

def _make_mock_db(units=None, certs=None, users=None, settings=None):
    """Build a minimal mock database for recompute tests."""
    db = MagicMock()

    # units.find().to_list(length=None)
    units_find = MagicMock()
    units_find.to_list = AsyncMock(return_value=units or [])
    db.units.find = MagicMock(return_value=units_find)

    # rental_certificates.find — async for cursor
    async def _cert_cursor(*a, **kw):
        for cert in (certs or []):
            yield cert

    db.rental_certificates.find = MagicMock(return_value=_async_iter(certs or []))

    # users.find — async for cursor
    db.users.find = MagicMock(return_value=_async_iter(users or []))

    # settings.find_one
    db.settings.find_one = AsyncMock(return_value=settings)

    # occupancy_status.find_one → None (triggers insert path)
    db.occupancy_status.find_one = AsyncMock(return_value=None)
    db.occupancy_status.insert_one = AsyncMock(return_value=None)
    db.occupancy_status.update_one = AsyncMock(return_value=None)

    return db


class _async_iter:
    """Minimal async iterator for Motor cursor mocking."""

    def __init__(self, items):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration


class _FakePgResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakePgSession:
    def __init__(self, rows):
        self.rows = rows
        self.tenant_ids = []

    async def execute(self, *args, **kwargs):
        return _FakePgResult(self.rows)


class _FakeAsyncSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeCutoverConn:
    def __init__(self, row):
        self.row = row
        self.executed = []

    async def execute(self, *args):
        self.executed.append(args)

    async def fetchrow(self, *args):
        self.executed.append(args)
        return self.row


class TestRecomputeEngine:
    """Unit tests for services/occupancy_recompute.py."""

    @pytest.mark.asyncio
    async def test_recompute_inserts_for_each_unit(self):
        from services.occupancy_recompute import recompute_building_occupancy

        units = [
            {"unit_number": "TH001", "building_id": "13195"},
            {"unit_number": "UA002", "building_id": "13195"},
        ]
        db = _make_mock_db(units=units)

        result = await recompute_building_occupancy("13195", db)

        assert result["total_lots"] == 2
        assert db.occupancy_status.insert_one.call_count == 2

    @pytest.mark.asyncio
    async def test_recompute_upserts_if_existing(self):
        from services.occupancy_recompute import recompute_building_occupancy

        db = _make_mock_db(units=[{"unit_number": "TH001", "building_id": "13195"}])
        # Simulate existing record
        db.occupancy_status.find_one = AsyncMock(return_value={"id": "existing-id"})

        result = await recompute_building_occupancy("13195", db)

        assert db.occupancy_status.update_one.call_count == 1
        assert db.occupancy_status.insert_one.call_count == 0

    @pytest.mark.asyncio
    async def test_active_cert_produces_tenant_status(self):
        from services.occupancy_recompute import recompute_building_occupancy

        certs = [{"unit_number": "TH001", "building_id": "13195", "status": "issued"}]
        units = [{"unit_number": "TH001", "building_id": "13195"}]
        db = _make_mock_db(units=units, certs=certs)

        await recompute_building_occupancy("13195", db)

        call_args = db.occupancy_status.insert_one.call_args[0][0]
        assert call_args["status"] == "tenant"
        assert call_args["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_building_id_scoping_in_insert(self):
        from services.occupancy_recompute import recompute_building_occupancy

        db = _make_mock_db(units=[{"unit_number": "UA001", "building_id": "13195"}])
        await recompute_building_occupancy("13195", db)

        inserted = db.occupancy_status.insert_one.call_args[0][0]
        assert inserted["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_units_without_unit_number_are_skipped(self):
        from services.occupancy_recompute import recompute_building_occupancy

        units = [
            {"unit_number": "UA001", "building_id": "13195"},
            {"building_id": "13195"},  # no unit_number
        ]
        db = _make_mock_db(units=units)
        result = await recompute_building_occupancy("13195", db)

        assert result["total_lots"] == 2
        # Only 1 insert (the one with unit_number)
        assert db.occupancy_status.insert_one.call_count == 1


# ═════════════════════════════════════════════════════════════════════════════
# 3. MULTI-TENANT ISOLATION TESTS (unit tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestMultiTenantIsolation:
    """Ensure building A data never leaks to building B."""

    @pytest.mark.asyncio
    async def test_recompute_only_reads_own_building_units(self):
        from services.occupancy_recompute import recompute_building_occupancy

        # Both buildings have units but we run for 13195 only
        building_a_units = [{"unit_number": "UA001", "building_id": "13195"}]
        db = _make_mock_db(units=building_a_units)

        await recompute_building_occupancy("13195", db)

        # units.find was called with building_id filter
        call_args = db.units.find.call_args
        query = call_args[0][0]
        assert query["building_id"] == "13195"

    @pytest.mark.asyncio
    async def test_inserted_document_carries_correct_building_id(self):
        from services.occupancy_recompute import recompute_building_occupancy

        db = _make_mock_db(units=[{"unit_number": "TH001", "building_id": "16244"}])
        await recompute_building_occupancy("16244", db)

        inserted = db.occupancy_status.insert_one.call_args[0][0]
        assert inserted["building_id"] == "16244"

    @pytest.mark.asyncio
    async def test_cert_query_scoped_to_building(self):
        from services.occupancy_recompute import _fetch_active_rental_certs

        db = MagicMock()
        db.rental_certificates.find = MagicMock(return_value=_async_iter([]))

        await _fetch_active_rental_certs("13195", db)

        query = db.rental_certificates.find.call_args[0][0]
        assert query["building_id"] == "13195"
        assert query["status"] == "issued"


class TestPostgresOccupancyReadModels:
    """Direct tests for the PG read-model helpers used after postgres_read promotion."""

    @pytest.mark.asyncio
    async def test_pg_summary_contract_counts_and_percentages(self):
        from routers import occupancy as occupancy_router

        rows = [
            {
                "status": "tenant",
                "count": 27,
                "avg_confidence": 1.0,
                "last_computed_at": datetime(2026, 7, 24, tzinfo=timezone.utc),
            },
            {
                "status": "owner_occupied",
                "count": 60,
                "avg_confidence": 1.0,
                "last_computed_at": datetime(2026, 7, 24, tzinfo=timezone.utc),
            },
        ]
        session = _FakePgSession(rows)

        with (
            patch("routers.occupancy._resolve_scheme_context", new=AsyncMock(return_value={
                "tenant_id": "tenant-1",
                "scheme_id": "scheme-1",
            })),
            patch("routers.occupancy.async_session_context", return_value=_FakeAsyncSessionContext(session)),
            patch("routers.occupancy.set_tenant", new=AsyncMock()),
        ):
            result = await occupancy_router._pg_occupancy_summary("13195")

        assert result.total_lots == 87
        assert result.tenant_count == 27
        assert result.owner_occupied_count == 60
        assert result.tenant_pct == 31.0
        assert result.owner_occupied_pct == 69.0

    @pytest.mark.asyncio
    async def test_pg_lots_normalises_status_and_sources(self):
        from routers import occupancy as occupancy_router

        rows = [
            {
                "lot_number": "TH087",
                "occupancy_type": "rented",
                "confidence": 0.9,
                "ingested_at": datetime(2026, 7, 24, tzinfo=timezone.utc),
            }
        ]
        session = _FakePgSession(rows)

        with (
            patch("routers.occupancy._resolve_scheme_context", new=AsyncMock(return_value={
                "tenant_id": "tenant-1",
                "scheme_id": "scheme-1",
            })),
            patch("routers.occupancy.async_session_context", return_value=_FakeAsyncSessionContext(session)),
            patch("routers.occupancy.set_tenant", new=AsyncMock()),
        ):
            result = await occupancy_router._pg_occupancy_lots("13195")

        assert len(result) == 1
        assert result[0].lot_number == "TH087"
        assert result[0].status == "tenant"
        assert result[0].sources == ["postgres_occupancy_snapshot"]

    @pytest.mark.asyncio
    async def test_occupancy_bootstrap_reads_current_cutover_mode_without_downgrading_shadow(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
        monkeypatch.setenv("MONGO_URL", "mongodb://localhost:27017")
        from scripts.bootstrap_postgres_occupancy_snapshot import _current_cutover_mode

        conn = _FakeCutoverConn({"mode": "postgres_shadow", "readiness_status": "shadow_active"})

        mode, readiness = await _current_cutover_mode(conn, "13195", "occupancy")

        assert mode == "postgres_shadow"
        assert readiness == "shadow_active"


# ═════════════════════════════════════════════════════════════════════════════
# 4. API CONTRACT TESTS (require running backend)
# ═════════════════════════════════════════════════════════════════════════════

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

BASE_URL = "http://127.0.0.1:8003/api"
_RUN_INTEGRATION = os.getenv("RUN_INTEGRATION_TESTS", "").lower() in ("1", "true", "yes")

# Credentials are read from environment variables — never hardcoded.
ADMIN_EMAIL = os.getenv("TEST_ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("TEST_ADMIN_PASSWORD", "")
OWNER_EMAIL = os.getenv("TEST_OWNER_EMAIL", "")
OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD", "")


def _login(email, password):
    resp = requests.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password}, timeout=10)
    if resp.status_code != 200:
        return None
    return resp.json().get("token")


@pytest.fixture(scope="module")
def admin_token():
    if not _RUN_INTEGRATION:
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run API contract tests")
    token = _login(ADMIN_EMAIL, ADMIN_PASSWORD)
    if not token:
        pytest.skip("Admin login failed — backend not running or TEST_ADMIN_EMAIL/PASSWORD not set")
    return token


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="module")
def owner_headers():
    if not _RUN_INTEGRATION:
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run API contract tests")
    token = _login(OWNER_EMAIL, OWNER_PASSWORD)
    if not token:
        pytest.skip("Owner login failed — backend not running or TEST_OWNER_EMAIL/PASSWORD not set")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.skipif(not REQUESTS_AVAILABLE or not _RUN_INTEGRATION,
                    reason="Set RUN_INTEGRATION_TESTS=1 and install requests to run API contract tests")
class TestApiContract:
    """Integration tests — require a running backend on localhost:8003."""

    def test_summary_endpoint_returns_200(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/occupancy/summary", headers=admin_headers, timeout=10)
        assert resp.status_code == 200, f"Summary failed: {resp.text}"
        data = resp.json()
        assert "total_lots" in data
        assert "tenant_count" in data
        assert "owner_occupied_count" in data
        assert "investor_unknown_count" in data

    def test_summary_percentages_sum_to_100(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/occupancy/summary", headers=admin_headers, timeout=10)
        if resp.status_code == 200 and resp.json().get("total_lots", 0) > 0:
            data = resp.json()
            total_pct = data["tenant_pct"] + data["owner_occupied_pct"] + data["investor_unknown_pct"]
            assert abs(total_pct - 100.0) < 1.0, f"Percentages don't sum to 100: {total_pct}"

    def test_lots_endpoint_returns_list(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/occupancy/lots", headers=admin_headers, timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        if data:
            lot = data[0]
            assert "lot_number" in lot
            assert "status" in lot
            assert "confidence" in lot
            assert lot["status"] in ("tenant", "owner_occupied", "investor_unknown")
            assert 0.0 <= lot["confidence"] <= 1.0

    def test_trends_endpoint_returns_structure(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/occupancy/trends", headers=admin_headers, timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert "building_id" in data
        assert "trend" in data
        assert isinstance(data["trend"], list)

    def test_owner_cannot_access_summary(self, owner_headers):
        """Owners must not have access to occupancy data (EC-only endpoint)."""
        resp = requests.get(f"{BASE_URL}/occupancy/summary", headers=owner_headers, timeout=10)
        assert resp.status_code in (403, 404), f"Expected 403/404 for owner, got {resp.status_code}"

    def test_recompute_requires_admin_role(self, owner_headers):
        """Non-admin owners must not trigger recompute."""
        resp = requests.post(f"{BASE_URL}/occupancy/recompute", headers=owner_headers, timeout=30)
        assert resp.status_code in (403, 404), f"Expected 403/404 for owner, got {resp.status_code}"

    def test_unauthenticated_request_returns_401(self):
        resp = requests.get(f"{BASE_URL}/occupancy/summary", timeout=10)
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


# ═════════════════════════════════════════════════════════════════════════════
# 5. FEATURE TOGGLE SEED TEST (unit test)
# ═════════════════════════════════════════════════════════════════════════════

class TestFeatureToggleSeed:
    def test_occupancy_toggle_in_default_features(self):
        from seeds.feature_toggles import DEFAULT_FEATURES
        keys = [f["feature_key"] for f in DEFAULT_FEATURES]
        assert "occupancy_intelligence" in keys, "occupancy_intelligence toggle not seeded"

    def test_occupancy_toggle_enabled_by_default(self):
        from seeds.feature_toggles import DEFAULT_FEATURES
        feature = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "occupancy_intelligence")
        assert feature["is_enabled"] is True
        assert feature["category"] == "intelligence"

    def test_occupancy_toggle_has_required_fields(self):
        from seeds.feature_toggles import DEFAULT_FEATURES
        feature = next(f for f in DEFAULT_FEATURES if f["feature_key"] == "occupancy_intelligence")
        for field in ("feature_name", "description", "icon", "routes"):
            assert field in feature, f"Missing field: {field}"
        assert "/intelligence/occupancy" in feature["routes"]
