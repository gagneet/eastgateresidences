#!/usr/bin/env python3
"""
External API v1 — Test Suite

Tests covering:
1. API key management (create, list, revoke) — admin JWT auth required
2. API health endpoint (unauthenticated)
3. Scope enforcement — correct 403 when scope is missing
4. Building & units endpoints (read:building scope)
5. Finance summary endpoint (read:finance scope)
6. Maintenance request endpoints (read:maintenance / write:maintenance)
7. Work order endpoints (read:work-orders)
8. Service provider endpoints (write:service)
9. Webhook management (manage:webhooks)

Run:
    cd /path/to/strata-management
    backend/venv/bin/python3 -m pytest tests/backend/test_external_api.py -v

Requirements:
    Backend running on localhost:8003
    Admin credentials: administrator@eastgateresidences.com.au / $E2E_ADMIN_PASSWORD
"""
import os
import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"
V1_URL = f"{BASE_URL}/v1"
ADMIN_EMAIL = "administrator@eastgateresidences.com.au"
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")


def _create_api_key(admin_headers, name, scopes, cleanup_registry, test_run_id):
    resp = requests.post(
        f"{V1_URL}/api-keys",
        json={
            "name": f"{name} [{test_run_id}]",
            "scopes": scopes,
            "description": "Automated test key",
        },
        headers=admin_headers,
        timeout=10,
    )
    if resp.status_code == 404:
        pytest.skip("External API router not mounted — skipping tests")
    assert resp.status_code in (200, 201), f"Could not create API key: {resp.text}"
    data = resp.json()
    cleanup_registry.register_id("api_keys", data.get("id"))
    return data


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def admin_token():
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD
    }, timeout=10)
    if resp.status_code != 200:
        pytest.skip(f"Backend not available or login failed: {resp.status_code}")
    return resp.json()["token"]


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="module")
def api_key_and_headers(admin_headers, cleanup_registry, test_run_id):
    """Create a full-admin API key, return (raw_key, headers)."""
    data = _create_api_key(admin_headers, "Test Integration Key", ["admin"], cleanup_registry, test_run_id)
    raw_key = data["raw_key"]
    key_id = data["id"]
    yield raw_key, {"X-API-Key": raw_key}, key_id

    # Cleanup — revoke the key after the module finishes
    requests.delete(
        f"{V1_URL}/api-keys/{key_id}",
        headers=admin_headers,
        timeout=10,
    )


@pytest.fixture(scope="module")
def api_headers(api_key_and_headers):
    _, headers, _ = api_key_and_headers
    return headers


@pytest.fixture(scope="module")
def scoped_key_headers(admin_headers, cleanup_registry, test_run_id):
    """Create a read-only key with limited scopes."""
    data = _create_api_key(
        admin_headers,
        "Scoped Read-Only Key",
        ["read:building", "read:finance"],
        cleanup_registry,
        test_run_id,
    )
    raw_key = data["raw_key"]
    key_id = data["id"]
    yield {"X-API-Key": raw_key}, key_id

    requests.delete(f"{V1_URL}/api-keys/{key_id}", headers=admin_headers, timeout=10)


# ── Health ────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_unauthenticated(self):
        resp = requests.get(f"{V1_URL}/health", timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0"
        assert "timestamp" in data


# ── API Key Management ────────────────────────────────────────────────────────

class TestAPIKeyManagement:
    def test_create_key_requires_admin(self):
        resp = requests.post(
            f"{V1_URL}/api-keys",
            json={"name": "Unauthorized Key", "scopes": ["read:building"]},
            timeout=10,
        )
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        # No auth → 403 or 401
        assert resp.status_code in (401, 403)

    def test_create_key_success(self, admin_headers, cleanup_registry, test_run_id):
        data = _create_api_key(admin_headers, "Temp Test Key", ["read:building"], cleanup_registry, test_run_id)
        assert "id" in data
        assert "raw_key" in data
        assert data["raw_key"].startswith("egst_")
        assert data["scopes"] == ["read:building"]

        requests.delete(f"{V1_URL}/api-keys/{data['id']}", headers=admin_headers, timeout=10)

    def test_create_key_invalid_scope(self, admin_headers):
        resp = requests.post(
            f"{V1_URL}/api-keys",
            json={"name": "Bad Scope Key", "scopes": ["nonexistent:scope"]},
            headers=admin_headers,
            timeout=10,
        )
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 422

    def test_list_keys_requires_admin(self):
        resp = requests.get(f"{V1_URL}/api-keys", timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code in (401, 403)

    def test_list_keys_success(self, admin_headers):
        resp = requests.get(f"{V1_URL}/api-keys", headers=admin_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_raw_key_not_in_list(self, admin_headers):
        resp = requests.get(f"{V1_URL}/api-keys", headers=admin_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        for key in resp.json():
            assert "raw_key" not in key
            assert "key_hash" not in key


# ── Scope Enforcement ─────────────────────────────────────────────────────────

class TestScopeEnforcement:
    def test_no_key_returns_401(self):
        resp = requests.get(f"{V1_URL}/building", timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 401

    def test_wrong_scope_returns_403(self, scoped_key_headers):
        """A read:building key cannot access write:service endpoints."""
        headers, _ = scoped_key_headers
        resp = requests.post(
            f"{V1_URL}/service/invoices",
            json={
                "work_order_id": "test",
                "amount": 100.0,
                "description": "Test invoice description",
                "invoice_number": "INV-001",
                "invoice_date": "2026-01-01",
            },
            headers=headers,
            timeout=10,
        )
        assert resp.status_code == 403

    def test_correct_scope_grants_access(self, scoped_key_headers):
        headers, _ = scoped_key_headers
        resp = requests.get(f"{V1_URL}/building", headers=headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200


# ── Building & Units ──────────────────────────────────────────────────────────

class TestBuilding:
    def test_get_building_info(self, api_headers):
        resp = requests.get(f"{V1_URL}/building", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "building_name" in data
        assert "total_units" in data
        assert isinstance(data["total_units"], int)

    def test_list_units_returns_paginated(self, api_headers):
        resp = requests.get(f"{V1_URL}/units?page=1&page_size=10", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "meta" in data
        meta = data["meta"]
        assert "total" in meta
        assert "page" in meta
        assert meta["page"] == 1
        assert meta["page_size"] == 10

    def test_list_units_tenanted_filter(self, api_headers):
        resp = requests.get(
            f"{V1_URL}/units?is_tenanted=true&page_size=5",
            headers=api_headers,
            timeout=10,
        )
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200

    def test_get_unit_not_found(self, api_headers):
        resp = requests.get(f"{V1_URL}/units/UNIT-DOES-NOT-EXIST-999", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 404

    def test_list_building_defects(self, api_headers):
        resp = requests.get(f"{V1_URL}/building/defects", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "meta" in data


# ── Owners Corporation ────────────────────────────────────────────────────────

class TestOC:
    def test_oc_summary(self, api_headers):
        resp = requests.get(f"{V1_URL}/oc/summary", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_units" in data
        assert "current_financial_year" in data
        assert "collection_rate_pct" in data

    def test_oc_levies(self, api_headers):
        resp = requests.get(f"{V1_URL}/oc/levies", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        # 200 or 404 (no levy data in test DB)
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.json()
            assert "financial_year" in data
            assert "total_budgeted" in data


# ── Finance ───────────────────────────────────────────────────────────────────

class TestFinance:
    def test_finance_summary(self, api_headers):
        resp = requests.get(f"{V1_URL}/finance/summary", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            data = resp.json()
            assert "financial_year" in data
            assert "total_income" in data
            assert "levy_arrears_total" in data

    def test_finance_budget(self, api_headers):
        resp = requests.get(f"{V1_URL}/finance/budget", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code in (200, 404)

    def test_finance_transactions_paginated(self, api_headers):
        resp = requests.get(
            f"{V1_URL}/finance/transactions?page=1&page_size=10",
            headers=api_headers,
            timeout=10,
        )
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "meta" in data


# ── Maintenance ───────────────────────────────────────────────────────────────

class TestMaintenance:
    def test_list_maintenance(self, api_headers):
        resp = requests.get(f"{V1_URL}/maintenance", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "meta" in data

    def test_report_defect(self, api_headers):
        resp = requests.post(
            f"{V1_URL}/building/defects",
            json={
                "title": "Test External API Defect",
                "description": "Created by automated test",
                "location": "Level 3 - Common Area",
                "priority": "low",
            },
            headers=api_headers,
            timeout=10,
        )
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["status"] == "open"

    def test_report_defect_validation(self, api_headers):
        resp = requests.post(
            f"{V1_URL}/building/defects",
            json={"title": "A"},  # too short
            headers=api_headers,
            timeout=10,
        )
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 422

    def test_get_maintenance_not_found(self, api_headers):
        resp = requests.get(f"{V1_URL}/maintenance/nonexistent-id", headers=api_headers, timeout=10)
        assert resp.status_code == 404


# ── Work Orders ───────────────────────────────────────────────────────────────

class TestWorkOrders:
    def test_list_work_orders(self, api_headers):
        resp = requests.get(f"{V1_URL}/work-orders", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "meta" in data

    def test_get_work_order_not_found(self, api_headers):
        resp = requests.get(f"{V1_URL}/work-orders/nonexistent-id", headers=api_headers, timeout=10)
        assert resp.status_code == 404

    def test_service_work_orders(self, api_headers):
        resp = requests.get(f"{V1_URL}/service/work-orders", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data


# ── Webhooks ──────────────────────────────────────────────────────────────────

class TestWebhooks:
    def test_list_webhook_events(self, api_headers):
        resp = requests.get(f"{V1_URL}/webhooks/events", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "events" in data
        assert "maintenance.created" in data["events"]

    def test_register_webhook_http_rejected(self, api_headers):
        """HTTP URLs must be rejected — only HTTPS allowed."""
        resp = requests.post(
            f"{V1_URL}/webhooks",
            json={
                "url": "http://example.com/webhook",
                "events": ["maintenance.created"],
            },
            headers=api_headers,
            timeout=10,
        )
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 422

    def test_register_webhook_invalid_event(self, api_headers):
        resp = requests.post(
            f"{V1_URL}/webhooks",
            json={
                "url": "https://example.com/webhook",
                "events": ["nonexistent.event"],
            },
            headers=api_headers,
            timeout=10,
        )
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 422

    def test_register_and_delete_webhook(self, api_headers, cleanup_registry, test_run_id):
        # Register
        resp = requests.post(
            f"{V1_URL}/webhooks",
            json={
                "url": "https://hooks.example.com/eastgate",
                "events": ["maintenance.created", "levy.paid"],
                "description": f"Test webhook [{test_run_id}]",
            },
            headers=api_headers,
            timeout=10,
        )
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 201
        wh_id = resp.json()["id"]
        cleanup_registry.register_id("api_webhooks", wh_id)
        assert "secret" not in resp.json()
        try:
            # List
            list_resp = requests.get(f"{V1_URL}/webhooks", headers=api_headers, timeout=10)
            assert list_resp.status_code == 200
            ids = [w["id"] for w in list_resp.json()]
            assert wh_id in ids
        finally:
            del_resp = requests.delete(f"{V1_URL}/webhooks/{wh_id}", headers=api_headers, timeout=10)
            assert del_resp.status_code == 204

    def test_delete_nonexistent_webhook(self, api_headers):
        resp = requests.delete(
            f"{V1_URL}/webhooks/nonexistent-webhook-id",
            headers=api_headers,
            timeout=10,
        )
        assert resp.status_code == 404


# ── Scopes endpoint ───────────────────────────────────────────────────────────

class TestScopesEndpoint:
    def test_list_scopes(self, api_headers):
        resp = requests.get(f"{V1_URL}/scopes", headers=api_headers, timeout=10)
        if resp.status_code == 404:
            pytest.skip("External API not mounted")
        assert resp.status_code == 200
        data = resp.json()
        assert "scopes" in data
        assert "admin" in data["scopes"]
        assert "read:building" in data["scopes"]
        assert "read:finance" in data["scopes"]
        assert "write:service" in data["scopes"]
