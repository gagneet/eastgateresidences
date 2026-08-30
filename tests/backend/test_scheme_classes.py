"""
Tests for Scheme Classes backend: models, service calculation, and router endpoints.

Run with:
    backend/venv/bin/python3 -m pytest tests/backend/test_scheme_classes.py -v
"""
from datetime import datetime, timezone, timedelta

import pytest

from services.scheme_levy_service import (
    UnitRecord,
    CategoryAllocation,
    compute_class_split_levies,
    compute_class_rates,
    is_split_active,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ua(num: str, ue: float = 100.0) -> UnitRecord:
    """Class A (apartment) unit."""
    return UnitRecord(num, ue, "class_a")


def _th(num: str, ue: float = 100.0) -> UnitRecord:
    """Class B (townhouse) unit."""
    return UnitRecord(num, ue, "class_b")


def _cat(alloc_type: str, amount: float, fund: str = "admin",
         class_a_pct: float = 50.0, class_b_pct: float = 50.0) -> CategoryAllocation:
    return CategoryAllocation(
        category_id=f"cat-{alloc_type}",
        category_name=alloc_type,
        fund=fund,
        allocation_type=alloc_type,
        class_a_pct=class_a_pct,
        class_b_pct=class_b_pct,
        amount_cents=int(amount * 100),
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── is_split_active ────────────────────────────────────────────────────────────

def test_split_active_past_date():
    past = _now() - timedelta(days=1)
    assert is_split_active(past) is True


def test_split_not_active_future_date():
    future = _now() + timedelta(days=1)
    assert is_split_active(future) is False


def test_split_not_active_none():
    assert is_split_active(None) is False


def test_split_active_naive_datetime():
    """Naive datetimes (no tzinfo) should be treated as UTC."""
    past_naive = datetime.utcnow() - timedelta(hours=1)
    assert is_split_active(past_naive) is True


# ── compute_class_split_levies — class_a_only ──────────────────────────────────

def test_class_a_only_paid_only_by_class_a():
    units = [_ua("UA001", 100.0), _ua("UA002", 100.0), _th("TH001", 100.0)]
    cats = [_cat("class_a_only", 1000.0)]
    admin, sinking = compute_class_split_levies(units, cats)
    # Class A pays 500 each (equal UE), TH pays 0
    assert admin["UA001"] == pytest.approx(500.0)
    assert admin["UA002"] == pytest.approx(500.0)
    assert admin["TH001"] == pytest.approx(0.0)


def test_class_b_only_paid_only_by_class_b():
    units = [_ua("UA001", 100.0), _th("TH001", 200.0), _th("TH002", 100.0)]
    cats = [_cat("class_b_only", 3000.0)]
    admin, _ = compute_class_split_levies(units, cats)
    assert admin["UA001"] == pytest.approx(0.0)
    assert admin["TH001"] == pytest.approx(2000.0)  # 200/300 * 3000
    assert admin["TH002"] == pytest.approx(1000.0)  # 100/300 * 3000


def test_class_a_only_with_no_class_a_units_skips(caplog):
    """If no Class A units exist, class_a_only allocation is skipped with a warning."""
    units = [_th("TH001", 100.0)]
    cats = [_cat("class_a_only", 1000.0)]
    admin, _ = compute_class_split_levies(units, cats)
    assert admin["TH001"] == pytest.approx(0.0)


# ── compute_class_split_levies — common ────────────────────────────────────────

def test_common_split_60_40():
    units = [_ua("UA001", 100.0), _th("TH001", 100.0)]
    cats = [_cat("common", 1000.0, class_a_pct=60.0, class_b_pct=40.0)]
    admin, _ = compute_class_split_levies(units, cats)
    assert admin["UA001"] == pytest.approx(600.0)  # 60% of 1000
    assert admin["TH001"] == pytest.approx(400.0)  # 40% of 1000


def test_common_split_equal_ue_within_class():
    """Within each class, UOE weighting applies."""
    units = [_ua("UA001", 100.0), _ua("UA002", 300.0), _th("TH001", 100.0)]
    cats = [_cat("common", 1000.0, class_a_pct=80.0, class_b_pct=20.0)]
    admin, _ = compute_class_split_levies(units, cats)
    # Class A gets $800: UA001=200 (100/400*800), UA002=600 (300/400*800)
    assert admin["UA001"] == pytest.approx(200.0)
    assert admin["UA002"] == pytest.approx(600.0)
    # Class B gets $200
    assert admin["TH001"] == pytest.approx(200.0)


# ── compute_class_split_levies — all_lots ──────────────────────────────────────

def test_all_lots_uses_whole_pool():
    units = [_ua("UA001", 100.0), _th("TH001", 100.0)]
    cats = [_cat("all_lots", 2000.0)]
    admin, _ = compute_class_split_levies(units, cats)
    assert admin["UA001"] == pytest.approx(1000.0)
    assert admin["TH001"] == pytest.approx(1000.0)


def test_all_lots_proportional_to_ue():
    units = [_ua("UA001", 100.0), _th("TH001", 300.0)]
    cats = [_cat("all_lots", 4000.0)]
    admin, _ = compute_class_split_levies(units, cats)
    assert admin["UA001"] == pytest.approx(1000.0)  # 100/400 * 4000
    assert admin["TH001"] == pytest.approx(3000.0)  # 300/400 * 4000


# ── compute_class_split_levies — fund separation ──────────────────────────────

def test_admin_and_sinking_fund_separated():
    units = [_ua("UA001", 100.0)]
    cats = [
        _cat("class_a_only", 1000.0, fund="admin"),
        _cat("class_a_only", 500.0, fund="sinking"),
    ]
    admin, sinking = compute_class_split_levies(units, cats)
    assert admin["UA001"] == pytest.approx(1000.0)
    assert sinking["UA001"] == pytest.approx(500.0)


# ── compute_class_split_levies — mixed categories ─────────────────────────────

def test_mixed_category_types():
    units = [_ua("UA001", 100.0), _ua("UA002", 100.0), _th("TH001", 200.0)]
    cats = [
        _cat("class_a_only", 2000.0),  # $2000 from Class A only
        _cat("class_b_only", 1000.0),  # $1000 from Class B only
        _cat("common", 1000.0, class_a_pct=60, class_b_pct=40),  # $600 A / $400 B
        _cat("all_lots", 400.0),  # All units by UOE: 100+100+200=400
    ]
    admin, _ = compute_class_split_levies(units, cats)

    # class_a_only: 2000 / 2 = 1000 each for UA
    # common class_a share: 600 / 2 = 300 each
    # all_lots: 400 * (100/400) = 100 each UA; 400 * (200/400) = 200 for TH
    expected_ua = 1000.0 + 300.0 + 100.0  # = 1400
    expected_th = 0.0 + 1000.0 + 400.0 + 200.0  # class_b + common_b + all_lots = 1600

    assert admin["UA001"] == pytest.approx(expected_ua)
    assert admin["UA002"] == pytest.approx(expected_ua)
    assert admin["TH001"] == pytest.approx(expected_th)


# ── compute_class_rates ────────────────────────────────────────────────────────

def test_compute_class_rates_returns_expected_keys():
    units = [_ua("UA001", 100.0), _th("TH001", 100.0)]
    cats = [_cat("all_lots", 2000.0)]
    rates = compute_class_rates(units, cats)
    assert "class_a_admin_rate" in rates
    assert "class_b_admin_rate" in rates
    assert "all_lots_admin_rate" in rates
    assert "class_a_sinking_rate" in rates
    assert "class_b_sinking_rate" in rates
    assert "all_lots_sinking_rate" in rates


def test_class_a_only_rate_excludes_class_b():
    units = [_ua("UA001", 100.0), _th("TH001", 100.0)]
    cats = [_cat("class_a_only", 1000.0)]
    rates = compute_class_rates(units, cats)
    # Class A: $1000 total / 100 UE = $10/UE
    assert rates["class_a_admin_rate"] == pytest.approx(10.0)
    # Class B: $0 from class_a_only
    assert rates["class_b_admin_rate"] == pytest.approx(0.0)


# ── Pydantic model validation ─────────────────────────────────────────────────

def test_class_category_allocation_create_validates_pct_sum():
    from models.scheme_classes import ClassCategoryAllocationCreate, CategoryAllocationType

    # Valid: common with 100% total
    alloc = ClassCategoryAllocationCreate(
        financial_year="2026-2027",
        category_id="cat-1",
        category_name="Lift",
        fund="admin",
        allocation_type=CategoryAllocationType.COMMON,
        class_a_pct=70.0,
        class_b_pct=30.0,
        amount_cents=100000,
        effective_from=datetime.now(timezone.utc),
    )
    assert alloc.class_a_pct == 70.0


def test_class_category_allocation_create_invalid_pct_sum():
    from models.scheme_classes import ClassCategoryAllocationCreate, CategoryAllocationType
    from pydantic import ValidationError

    # Invalid: common allocation where A+B != 100
    with pytest.raises((ValidationError, ValueError)):
        ClassCategoryAllocationCreate(
            financial_year="2026-2027",
            category_id="cat-1",
            category_name="Lift",
            fund="admin",
            allocation_type=CategoryAllocationType.COMMON,
            class_a_pct=60.0,
            class_b_pct=60.0,  # 120% total — invalid
            amount_cents=100000,
            effective_from=datetime.now(timezone.utc),
        )


def test_scheme_class_response_unit_count():
    from models.scheme_classes import SchemeClassResponse, SchemeClassLabel

    resp = SchemeClassResponse(
        class_id="cls-1",
        building_id="13195",
        class_label=SchemeClassLabel.CLASS_A,
        class_name="Apartments",
        description=None,
        unit_numbers=["UA001", "UA002", "UA003"],
        unit_type_prefixes=["UA"],
        total_uoe=300.0,
        unit_count=3,
        effective_from=datetime.now(timezone.utc),
        is_active=True,
        notes=None,
        created_by="admin@test.com",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert resp.unit_count == 3
    assert resp.total_uoe == 300.0


# ── Zero UOE guard ────────────────────────────────────────────────────────────

def test_no_division_by_zero_with_empty_class():
    """If a class has no units (UE=0), should not raise ZeroDivisionError."""
    units = [_ua("UA001", 0.0)]  # unit exists but UE=0
    cats = [_cat("class_a_only", 1000.0)]
    # Guard: total_ue_a = max(0, 1) = 1
    admin, _ = compute_class_split_levies(units, cats)
    assert "UA001" in admin  # no exception


def test_grand_total_conserved():
    """Total levy collected across all units must equal the sum of all category budgets."""
    units = [_ua("UA001", 100.0), _ua("UA002", 150.0), _th("TH001", 200.0), _th("TH002", 50.0)]
    cats = [
        _cat("class_a_only", 5000.0),
        _cat("class_b_only", 3000.0),
        _cat("common", 2000.0, class_a_pct=70, class_b_pct=30),
        _cat("all_lots", 1000.0),
    ]
    admin, sinking = compute_class_split_levies(units, cats)
    total_admin = sum(admin.values())
    total_budget = sum(c.amount for c in cats if c.fund == "admin")
    assert total_admin == pytest.approx(total_budget, abs=0.01)


# ── Integration tests ─────────────────────────────────────────────────────────
# These tests call the live backend over HTTP and are skipped unless the
# environment variable RUN_INTEGRATION_TESTS=1 is set.
#
# Run with:
#   RUN_INTEGRATION_TESTS=1 backend/venv/bin/python3 -m pytest \
#       tests/backend/test_scheme_classes.py::TestSchemeClassesIntegration -v

import os
import requests as _req

_BACKEND = "http://127.0.0.1:8003/api"
_SKIP = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Set RUN_INTEGRATION_TESTS=1 to run integration tests",
)

# Credentials are read from environment variables — never hardcoded.
# Set before running:
#   export TEST_ADMIN_EMAIL=administrator@...
#   export TEST_ADMIN_PASSWORD=<password>
#   export TEST_OWNER_EMAIL=avneet@...
#   export TEST_OWNER_PASSWORD=<password>
_ADMIN_EMAIL = os.getenv("TEST_ADMIN_EMAIL", "administrator@eastgateresidences.com.au")
_ADMIN_PASSWORD = os.getenv("TEST_ADMIN_PASSWORD", "")
_OWNER_EMAIL = os.getenv("TEST_OWNER_EMAIL", "avneet@eastgateresidences.com.au")
_OWNER_PASSWORD = os.getenv("TEST_OWNER_PASSWORD", "")


def _login(email: str, password: str) -> str | None:
    """POST /auth/login, return JWT token or None on failure."""
    try:
        resp = _req.post(
            f"{_BACKEND}/auth/login",
            json={"email": email, "password": password},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()["token"]
    except Exception:
        pass
    return None


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
class TestSchemeClassesIntegration:
    """HTTP integration tests for the /api/scheme-classes router.

    All tests are skipped unless RUN_INTEGRATION_TESTS=1 is set.
    Tests are idempotent — no permanent data is written to the live DB.
    """

    # ── 1. GET /scheme-classes/status without auth ────────────────────────

    @_SKIP
    def test_status_unauthenticated(self):
        """Unauthenticated request to /status must be rejected."""
        r = _req.get(f"{_BACKEND}/scheme-classes/status", timeout=10)
        assert r.status_code in (401, 403), (
            f"Expected 401/403 without auth, got {r.status_code}: {r.text[:300]}"
        )

    # ── 2. GET /scheme-classes/status with admin auth ─────────────────────

    @_SKIP
    def test_status_with_admin_auth(self):
        """Admin token must receive a 200 from /status (route exists and responds)."""
        token = _login(_ADMIN_EMAIL, _ADMIN_PASSWORD)
        if not token:
            pytest.skip("Admin login failed — backend may be unavailable")
        r = _req.get(f"{_BACKEND}/scheme-classes/status", headers=_auth(token), timeout=10)
        assert r.status_code not in (404, 405), (
            f"Route not found: {r.status_code} {r.text[:300]}"
        )
        assert r.status_code == 200, (
            f"Expected 200 with admin auth, got {r.status_code}: {r.text[:300]}"
        )

    # ── 3. GET /scheme-classes without auth ───────────────────────────────

    @_SKIP
    def test_list_classes_unauthenticated(self):
        """Unauthenticated list request must be rejected."""
        r = _req.get(f"{_BACKEND}/scheme-classes", timeout=10)
        assert r.status_code in (401, 403), (
            f"Expected 401/403 without auth, got {r.status_code}: {r.text[:300]}"
        )

    # ── 4. POST /scheme-classes as owner (insufficient role) ──────────────

    @_SKIP
    def test_create_class_unauthorized_role(self):
        """An owner-role user must receive 403 when attempting to create a scheme class."""
        token = _login(_OWNER_EMAIL, _OWNER_PASSWORD)
        if not token:
            pytest.skip("Owner login failed — backend may be unavailable")
        r = _req.post(
            f"{_BACKEND}/scheme-classes",
            headers=_auth(token),
            json={
                "class_label": "class_a",
                "class_name": "Test Apartments",
                "unit_numbers": [],
                "unit_type_prefixes": ["UA"],
                "effective_from": "2026-01-01T00:00:00Z",
            },
            timeout=10,
        )
        assert r.status_code == 403, (
            f"Expected 403 for owner role, got {r.status_code}: {r.text[:300]}"
        )

    # ── 5. GET /scheme-classes/allocations/{year} without auth ───────────

    @_SKIP
    def test_allocations_unauthenticated(self):
        """Unauthenticated allocations request must be rejected."""
        r = _req.get(f"{_BACKEND}/scheme-classes/allocations/2026-2027", timeout=10)
        assert r.status_code in (401, 403), (
            f"Expected 401/403 without auth, got {r.status_code}: {r.text[:300]}"
        )

    # ── 6. POST /scheme-classes/preview-levy-impact without auth ─────────

    @_SKIP
    def test_preview_unauthenticated(self):
        """Unauthenticated preview request must be rejected."""
        r = _req.post(
            f"{_BACKEND}/scheme-classes/preview-levy-impact",
            json={},
            timeout=10,
        )
        assert r.status_code in (401, 403), (
            f"Expected 401/403 without auth, got {r.status_code}: {r.text[:300]}"
        )

    # ── 7. Multi-tenant isolation ─────────────────────────────────────────

    @_SKIP
    def test_multi_tenant_isolation(self):
        """Scheme classes for building 13195 must not appear in building 16244's records.

        Strategy:
          1. Fetch scheme classes for 13195 via HTTP (admin JWT).
          2. Verify every returned item carries building_id == "13195".
          3. Use a direct pymongo connection to confirm none of those class_ids
             exist under building_id == "16244" (data-level isolation check).
        """
        token = _login(_ADMIN_EMAIL, _ADMIN_PASSWORD)
        if not token:
            pytest.skip("Admin login failed — backend may be unavailable")

        r = _req.get(f"{_BACKEND}/scheme-classes", headers=_auth(token), timeout=10)
        assert r.status_code == 200, (
            f"List classes failed: {r.status_code} {r.text[:300]}"
        )
        classes_13195 = r.json()

        # All items in the response must belong to the authenticated building
        for cls in classes_13195:
            assert cls.get("building_id") == "13195", (
                f"Cross-building data in response: class_id={cls.get('class_id')} "
                f"has building_id={cls.get('building_id')} (expected 13195)"
            )

        class_ids_13195 = {c["class_id"] for c in classes_13195}

        # Verify at DB level that none of those IDs appear under building 16244
        if not class_ids_13195:
            return  # No classes configured — isolation trivially holds

        try:
            import pymongo
            sync_mongo = pymongo.MongoClient(
                "mongodb://localhost:27018", serverSelectionTimeoutMS=2000
            )
            sync_db = sync_mongo["strata_production"]
            leaked = list(sync_db.scheme_classes.find(
                {"building_id": "16244", "class_id": {"$in": list(class_ids_13195)}},
                {"class_id": 1},
            ))
            sync_mongo.close()
            assert len(leaked) == 0, (
                "Building isolation breach: class_ids "
                f"{[d['class_id'] for d in leaked]} from building 13195 "
                "found in building 16244 records"
            )
        except Exception as exc:
            import pymongo.errors
            if isinstance(exc, pymongo.errors.ConnectionFailure):
                pytest.skip("MongoDB not reachable for DB-level isolation check")
            raise
