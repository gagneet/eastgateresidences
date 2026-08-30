#!/usr/bin/env python3
"""Integration tests for API fixes (requires running backend)."""
import os

import pytest
import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8003")
RUN_INTEGRATION = os.getenv("RUN_INTEGRATION_TESTS") == "1"

if not RUN_INTEGRATION:
    pytest.skip("Integration tests disabled. Set RUN_INTEGRATION_TESTS=1 to run.", allow_module_level=True)


def _api_url(path: str) -> str:
    return f"{API_BASE_URL.rstrip('/')}{path}"


@pytest.mark.integration
def test_login_and_owner_unit_endpoint():
    response = requests.post(
        _api_url("/api/auth/login"),
        json={"email": "administrator@eastgateresidences.com.au", "password": os.environ.get("E2E_ADMIN_PASSWORD", "")},
        timeout=10,
    )
    assert response.status_code == 200, f"Login failed: {response.status_code} {response.text[:200]}"
    token = response.json()["token"]

    response2 = requests.get(
        _api_url("/api/owners-units/UA017"),
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert response2.status_code == 200, f"Owners-units failed: {response2.status_code} {response2.text[:200]}"


@pytest.mark.integration
def test_public_by_laws_endpoint():
    response3 = requests.get(_api_url("/api/by-laws/current"), timeout=10)
    assert response3.status_code == 200, f"By-laws failed: {response3.status_code} {response3.text[:200]}"
