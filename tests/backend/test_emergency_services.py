"""
tests/backend/test_emergency_services.py

Tests for the emergency services API endpoints and data integrity.
Verifies: CRUD operations, privacy filtering, category structure,
and all required contacts from the East Gate contact sheet.

Run: backend/venv/bin/pytest tests/backend/test_emergency_services.py -v
"""
import os
import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def admin_token():
    """Log in as admin and return bearer token."""
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "administrator@eastgateresidences.com.au",
        "password": os.environ.get("E2E_ADMIN_PASSWORD", "")
    })
    assert resp.status_code == 200, f"Admin login failed: {resp.text}"
    return resp.json()["token"]


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ── Public listing ────────────────────────────────────────────────────────────

class TestPublicEmergencyServicesList:
    """Unauthenticated access should return only public (non-private) contacts."""

    def test_returns_200_without_auth(self):
        resp = requests.get(f"{BASE_URL}/emergency-services")
        assert resp.status_code == 200

    def test_returns_list(self):
        resp = requests.get(f"{BASE_URL}/emergency-services")
        data = resp.json()
        assert isinstance(data, list)

    def test_no_private_contacts_returned_to_anonymous(self):
        resp = requests.get(f"{BASE_URL}/emergency-services")
        data = resp.json()
        private = [s for s in data if s.get("is_private") is True]
        assert len(private) == 0, f"Private contacts exposed: {[s['name'] for s in private]}"

    def test_triple_zero_always_present(self):
        resp = requests.get(f"{BASE_URL}/emergency-services")
        data = resp.json()
        names = [s["name"] for s in data]
        assert any("000" in s.get("phone", "") for s in data), \
            "Triple Zero (000) not found in public services"

    def test_non_emergency_police_present(self):
        resp = requests.get(f"{BASE_URL}/emergency-services")
        data = resp.json()
        assert any("131" in s.get("phone", "") for s in data), \
            "131 444 Police number not found"

    def test_all_services_have_required_fields(self):
        resp = requests.get(f"{BASE_URL}/emergency-services")
        data = resp.json()
        for s in data:
            assert "id" in s, f"Missing id: {s}"
            assert "name" in s, f"Missing name: {s}"
            assert "phone" in s, f"Missing phone: {s}"
            assert "category" in s, f"Missing category: {s}"

    def test_services_sorted_by_order(self):
        resp = requests.get(f"{BASE_URL}/emergency-services")
        data = resp.json()
        orders = [s.get("order", 0) for s in data]
        assert orders == sorted(orders), "Services not sorted by order field"


# ── Authenticated listing ─────────────────────────────────────────────────────

class TestAuthenticatedEmergencyServicesList:
    """Authenticated requests should also return private (management) contacts."""

    def test_management_contacts_visible_when_authenticated(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/emergency-services", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        private = [s for s in data if s.get("is_private") is True]
        assert len(private) > 0, "No private management contacts returned for authenticated user"

    def test_civium_strata_manager_present(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/emergency-services", headers=admin_headers)
        data = resp.json()
        assert any("Jessica" in s["name"] or "Civium" in s["name"] for s in data), \
            "Jessica Minichiello / Civium contact not found"

    def test_anthony_mcdonald_chair_present(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/emergency-services", headers=admin_headers)
        data = resp.json()
        assert any("Anthony" in (s.get("description") or "") or "Anthony" in s["name"]
                   for s in data), \
            "Anthony McDonald (Chair) contact not found"

    def test_management_category_contacts_exist(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/emergency-services", headers=admin_headers)
        data = resp.json()
        mgmt = [s for s in data if s.get("category") == "management"]
        assert len(mgmt) >= 3, f"Expected >=3 management contacts, got {len(mgmt)}"


# ── Contractor contacts ───────────────────────────────────────────────────────

class TestContractorContacts:
    """Verify all required contractor contacts from the East Gate contact sheet."""

    def _get_services(self):
        return requests.get(f"{BASE_URL}/emergency-services").json()

    def test_gmh_electrical_present(self):
        data = self._get_services()
        assert any("GMH" in s["name"] or "0418 623 046" in s.get("phone", "") for s in data)

    def test_lee_madden_electrician_present(self):
        data = self._get_services()
        assert any("Lee Madden" in s["name"] and "Electrician" in s["name"] for s in data), \
            "Lee Madden Electrician entry not found"

    def test_level_plumbing_present(self):
        data = self._get_services()
        assert any("Level Plumbing" in s["name"] for s in data)

    def test_jims_plumbing_present(self):
        data = self._get_services()
        assert any("Jim" in s["name"] for s in data), "Jim's Plumbing not found"

    def test_canberra_locksmiths_present(self):
        data = self._get_services()
        assert any("Canberra Locksmiths" in s["name"] for s in data)

    def test_act_mobile_locksmith_present(self):
        data = self._get_services()
        assert any("ACT Mobile" in s["name"] for s in data), "ACT Mobile Locksmith not found"

    def test_electra_lift_co_present(self):
        data = self._get_services()
        assert any("Electra" in s["name"] for s in data)

    def test_stiebel_eltron_hot_water_present(self):
        data = self._get_services()
        assert any("Stiebel" in s["name"] for s in data)

    def test_capital_glass_present(self):
        data = self._get_services()
        assert any("Capital Glass" in s["name"] for s in data)

    def test_ew_glass_present(self):
        data = self._get_services()
        assert any("EW Glass" in s["name"] for s in data), "EW Glass not found"

    def test_joseph_corradi_painter_present(self):
        data = self._get_services()
        assert any("Corradi" in s["name"] for s in data), "Joseph Corradi (Painter) not found"

    def test_lee_madden_painter_present(self):
        data = self._get_services()
        assert any("Lee Madden" in s["name"] and "Paint" in s["name"] for s in data), \
            "Lee Madden Painting entry not found"

    def test_locksmiths_are_24_7(self):
        data = self._get_services()
        locksmiths = [s for s in data if "Locksmith" in s["name"] or "locksmith" in s.get("category", "")]
        for lk in locksmiths:
            if "Canberra" in lk["name"] or "ACT Mobile" in lk["name"]:
                assert lk.get("is_24_7") is True, f"{lk['name']} should be 24/7"

    def test_total_contact_count(self, admin_headers):
        """Should have at least 20 contacts including private ones."""
        resp = requests.get(f"{BASE_URL}/emergency-services", headers=admin_headers)
        data = resp.json()
        assert len(data) >= 20, f"Expected >=20 total contacts, got {len(data)}"


# ── CRUD operations (admin only) ──────────────────────────────────────────────

class TestEmergencyServicesCRUD:
    """Admin can create, update, and delete emergency service contacts."""

    _created_id = None

    def test_create_service_as_admin(self, admin_headers):
        payload = {
            "name": "Test Plumber – Pytest",
            "category": "utility",
            "phone": "0400 000 001",
            "description": "Pytest test contact — delete after test",
            "is_24_7": False,
            "is_private": False,
            "order": 999
        }
        resp = requests.post(f"{BASE_URL}/emergency-services", json=payload, headers=admin_headers)
        assert resp.status_code in (200, 201), f"Create failed: {resp.text}"
        data = resp.json()
        assert data["name"] == payload["name"]
        assert "id" in data
        TestEmergencyServicesCRUD._created_id = data["id"]

    def test_update_service_as_admin(self, admin_headers):
        svc_id = TestEmergencyServicesCRUD._created_id
        if not svc_id:
            pytest.skip("No service created to update")
        payload = {
            "name": "Test Plumber – Pytest (updated)",
            "category": "utility",
            "phone": "0400 000 002",
            "is_24_7": True,
            "is_private": False,
            "order": 999
        }
        resp = requests.put(f"{BASE_URL}/emergency-services/{svc_id}", json=payload, headers=admin_headers)
        assert resp.status_code == 200, f"Update failed: {resp.text}"
        data = resp.json()
        assert data["phone"] == "0400 000 002"
        assert data["is_24_7"] is True

    def test_delete_service_as_admin(self, admin_headers):
        svc_id = TestEmergencyServicesCRUD._created_id
        if not svc_id:
            pytest.skip("No service created to delete")
        resp = requests.delete(f"{BASE_URL}/emergency-services/{svc_id}", headers=admin_headers)
        assert resp.status_code == 200

    def test_cannot_create_service_without_auth(self):
        payload = {
            "name": "Unauthorized Test",
            "category": "utility",
            "phone": "000",
            "is_24_7": False,
            "is_private": False,
            "order": 999
        }
        resp = requests.post(f"{BASE_URL}/emergency-services", json=payload)
        assert resp.status_code in (401, 403), "Unauthenticated create should be rejected"

    def test_cannot_delete_service_without_auth(self, admin_headers):
        # Get the first public service id
        resp = requests.get(f"{BASE_URL}/emergency-services")
        services = resp.json()
        if not services:
            pytest.skip("No services to test delete")
        svc_id = services[0]["id"]
        del_resp = requests.delete(f"{BASE_URL}/emergency-services/{svc_id}")
        assert del_resp.status_code in (401, 403), "Unauthenticated delete should be rejected"
