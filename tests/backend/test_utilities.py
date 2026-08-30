#!/usr/bin/env python3
"""
Backend Tests: Utilities Endpoint Suite

Tests for GET /api/utilities/{unit_number} which returns electricity,
gas, and NBN connection details for all 87 units at East Gate Residences.

All meter data is static (embedded in the router) — no DB required.

Run:
    cd /home/gagneet/strata-management
    source backend/venv/bin/activate
    pytest tests/backend/test_utilities.py -v

Requirements:
    Backend running on localhost:8003
    Admin auth: conftest.py admin_token fixture plus X-Building-ID header
    Owner auth: conftest.py mint_token(..., legacy_identity=True), unit TH087,
                lot 87 (has gas)
"""

import pytest
import requests

BASE_URL = "http://127.0.0.1:8003/api"
OWNER_EMAIL = "avneet@eastgateresidences.com.au"
OWNER_UNIT = "TH087"  # lot 87 — has gas meter EC 930 372


# ── Fixtures ──────────────────────────────────────────────────────────────────

# admin_token and admin_headers provided by conftest.py (session-scoped)


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    """Super-admin requests must carry an explicit East Gate building context."""
    return {"Authorization": f"Bearer {admin_token}", "X-Building-ID": "13195"}


@pytest.fixture(scope="module")
def owner_token(mint_token):
    # This suite validates unit-utility access rules, not password login.
    # Use a legacy-identity token so Mongo-primary owner resolution remains
    # exercised even when local credential fixtures drift.
    return mint_token(OWNER_EMAIL, legacy_identity=True)


@pytest.fixture(scope="module")
def owner_headers(owner_token):
    return {"Authorization": f"Bearer {owner_token}"}


# ── Auth / Permission Tests ───────────────────────────────────────────────────

class TestUtilitiesAuth:
    """Authentication and authorisation gate tests."""

    def test_unauthenticated_returns_401(self):
        resp = requests.get(f"{BASE_URL}/utilities/UA001")
        assert resp.status_code == 401

    def test_missing_auth_header_returns_401(self):
        resp = requests.get(f"{BASE_URL}/utilities/TH071", headers={"Authorization": "Bearer bad_token"})
        assert resp.status_code == 401

    def test_owner_cannot_view_other_unit(self, owner_headers):
        """An owner may only fetch their own unit."""
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=owner_headers)
        assert resp.status_code == 403
        error = resp.json().get("error", {})
        message = resp.json().get("detail") or error.get("message", "")
        assert "own unit" in message.lower()

    def test_owner_can_view_own_unit(self, owner_headers):
        resp = requests.get(f"{BASE_URL}/utilities/{OWNER_UNIT}", headers=owner_headers)
        assert resp.status_code == 200

    def test_admin_can_view_any_unit(self, admin_headers):
        for unit in ["UA001", "UA070", "TH071", "TH087"]:
            resp = requests.get(f"{BASE_URL}/utilities/{unit}", headers=admin_headers)
            assert resp.status_code == 200, f"Admin failed to access {unit}: {resp.text}"


# ── Unit Resolution Tests ─────────────────────────────────────────────────────

class TestUtilitiesUnitResolution:
    """Unit number parsing and validation."""

    def test_invalid_unit_format_returns_404(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/INVALID", headers=admin_headers)
        assert resp.status_code == 404

    def test_apartment_out_of_range_returns_404(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA071", headers=admin_headers)
        assert resp.status_code == 404

    def test_townhouse_out_of_range_returns_404(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/TH018", headers=admin_headers)
        assert resp.status_code == 404

    def test_zero_unit_returns_404(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA000", headers=admin_headers)
        assert resp.status_code == 404

    def test_unit_number_case_insensitive(self, admin_headers):
        """Unit numbers should be accepted in any case."""
        for variant in ["ua001", "UA001", "Ua001"]:
            resp = requests.get(f"{BASE_URL}/utilities/{variant}", headers=admin_headers)
            assert resp.status_code == 200, f"Case variant {variant!r} failed"

    def test_valid_apartment_range(self, admin_headers):
        for unit in ["UA001", "UA035", "UA070"]:
            resp = requests.get(f"{BASE_URL}/utilities/{unit}", headers=admin_headers)
            assert resp.status_code == 200, f"Apartment {unit} failed"

    def test_valid_townhouse_range(self, admin_headers):
        for unit in ["TH071", "TH079", "TH087"]:
            resp = requests.get(f"{BASE_URL}/utilities/{unit}", headers=admin_headers)
            assert resp.status_code == 200, f"Townhouse {unit} failed"


# ── Response Schema Tests ─────────────────────────────────────────────────────

class TestUtilitiesResponseSchema:
    """Response structure and field presence."""

    def test_response_has_top_level_fields(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        data = resp.json()
        assert "unit_number" in data
        assert "lot_number" in data
        assert "electricity" in data
        assert "gas" in data
        assert "nbn" in data

    def test_unit_number_normalised_to_uppercase(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/ua001", headers=admin_headers)
        assert resp.json()["unit_number"] == "UA001"

    def test_lot_number_is_integer(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        assert isinstance(resp.json()["lot_number"], int)

    def test_apartment_lot_range(self, admin_headers):
        """Apartments UA001–UA070 should have lot numbers 1–70."""
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        assert resp.json()["lot_number"] == 1
        resp = requests.get(f"{BASE_URL}/utilities/UA070", headers=admin_headers)
        assert resp.json()["lot_number"] == 70

    def test_townhouse_lot_range(self, admin_headers):
        """Townhouses TH071–TH087 should have lot numbers 71–87."""
        resp = requests.get(f"{BASE_URL}/utilities/TH071", headers=admin_headers)
        assert resp.json()["lot_number"] == 71
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=admin_headers)
        assert resp.json()["lot_number"] == 87


# ── Electricity Tests ─────────────────────────────────────────────────────────

class TestUtilitiesElectricity:
    """Electricity sub-object correctness."""

    def test_electricity_has_required_fields(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        elec = resp.json()["electricity"]
        for field in ["unit_number", "lot_number", "loc_id", "supplier", "distributor",
                      "supply_charge_per_day", "usage_charge_per_unit",
                      "faults_emergency", "assistance", "my_account_url"]:
            assert field in elec, f"Missing field: {field}"

    def test_electricity_supplier_is_origin(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        assert resp.json()["electricity"]["supplier"] == "Origin Energy"

    def test_electricity_distributor_is_evoenergy(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        assert "Evoenergy" in resp.json()["electricity"]["distributor"]

    def test_electricity_supply_charge_is_positive(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        assert resp.json()["electricity"]["supply_charge_per_day"] > 0

    def test_electricity_usage_charge_is_positive(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        assert resp.json()["electricity"]["usage_charge_per_unit"] > 0

    def test_all_units_have_loc_id(self, admin_headers):
        """Every unit should have an electricity LOC ID."""
        for unit in ["UA001", "UA070", "TH071", "TH087"]:
            resp = requests.get(f"{BASE_URL}/utilities/{unit}", headers=admin_headers)
            loc_id = resp.json()["electricity"]["loc_id"]
            assert loc_id is not None and loc_id.startswith("LOC"), \
                f"{unit} has bad LOC ID: {loc_id!r}"

    def test_loc_id_format(self, admin_headers):
        """LOC IDs should be 15-character strings starting with LOC."""
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        loc_id = resp.json()["electricity"]["loc_id"]
        assert len(loc_id) == 15
        assert loc_id.startswith("LOC")

    def test_electricity_faults_number_present(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        faults = resp.json()["electricity"]["faults_emergency"]
        assert faults and len(faults) > 0

    def test_electricity_my_account_url(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        url = resp.json()["electricity"]["my_account_url"]
        assert url.startswith("http")
        assert "originenergy" in url


# ── Gas Tests ─────────────────────────────────────────────────────────────────

class TestUtilitiesGas:
    """Gas sub-object — gas vs induction logic."""

    # Townhouses with gas (lot 71, 73, 77, 79–87):
    #   TH071=71, TH073=73, TH077=77, TH079=79, TH080=80...TH087=87
    # Townhouses with induction:
    #   TH072=72, TH074=74, TH075=75, TH076=76, TH078=78

    def test_gas_has_required_fields(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=admin_headers)
        gas = resp.json()["gas"]
        for field in ["unit_number", "lot_number", "has_gas", "cooktop_type"]:
            assert field in gas, f"Missing gas field: {field}"

    def test_townhouse_with_gas_has_gas_true(self, admin_headers):
        """TH087 (lot 87) has a gas meter."""
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=admin_headers)
        gas = resp.json()["gas"]
        assert gas["has_gas"] is True
        assert gas["cooktop_type"] == "Gas"

    def test_townhouse_with_gas_has_meter_number(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=admin_headers)
        gas = resp.json()["gas"]
        assert gas["meter_number"] is not None
        assert gas["meter_number"].startswith("EC")

    def test_townhouse_with_gas_has_charges(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=admin_headers)
        gas = resp.json()["gas"]
        assert gas["supply_charge_per_day"] is not None
        assert gas["supply_charge_per_day"] > 0
        assert gas["usage_charge_first_3863"] is not None
        assert gas["usage_charge_first_3863"] > 0

    def test_townhouse_with_gas_has_origin_supplier(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=admin_headers)
        gas = resp.json()["gas"]
        assert "Origin" in gas["supplier"]

    def test_townhouse_induction_has_gas_false(self, admin_headers):
        """TH072 (lot 72) has an induction cooktop."""
        resp = requests.get(f"{BASE_URL}/utilities/TH072", headers=admin_headers)
        gas = resp.json()["gas"]
        assert gas["has_gas"] is False
        assert gas["cooktop_type"] == "Induction"

    def test_townhouse_induction_has_no_meter(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/TH072", headers=admin_headers)
        gas = resp.json()["gas"]
        assert gas["meter_number"] is None

    def test_townhouse_induction_has_no_gas_charges(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/TH072", headers=admin_headers)
        gas = resp.json()["gas"]
        assert gas["supply_charge_per_day"] is None
        assert gas["usage_charge_first_3863"] is None

    def test_apartment_has_no_gas(self, admin_headers):
        """All apartments (UA001–UA070) should have has_gas=False."""
        for unit in ["UA001", "UA035", "UA070"]:
            resp = requests.get(f"{BASE_URL}/utilities/{unit}", headers=admin_headers)
            gas = resp.json()["gas"]
            assert gas["has_gas"] is False, f"{unit} should not have gas"
            assert gas["cooktop_type"] == "Induction"

    def test_gas_distributor_is_evoenergy(self, admin_headers):
        """Evoenergy manages the ACT gas network for all units."""
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=admin_headers)
        assert "Evoenergy" in resp.json()["gas"]["distributor"]

    def test_all_twelve_townhouses_with_gas(self, admin_headers):
        """
        Townhouses 71, 73, 77, 79–87 (= TH071, TH073, TH077, TH079–TH087)
        should all have has_gas=True.
        """
        gas_units = ["TH071", "TH073", "TH077", "TH079", "TH080",
                     "TH081", "TH082", "TH083", "TH084", "TH085", "TH086", "TH087"]
        for unit in gas_units:
            resp = requests.get(f"{BASE_URL}/utilities/{unit}", headers=admin_headers)
            gas = resp.json()["gas"]
            assert gas["has_gas"] is True, f"{unit} should have gas"
            assert gas["meter_number"] is not None, f"{unit} should have meter number"

    def test_five_induction_townhouses(self, admin_headers):
        """TH072, TH074, TH075, TH076, TH078 have induction cooktops."""
        induction_units = ["TH072", "TH074", "TH075", "TH076", "TH078"]
        for unit in induction_units:
            resp = requests.get(f"{BASE_URL}/utilities/{unit}", headers=admin_headers)
            gas = resp.json()["gas"]
            assert gas["has_gas"] is False, f"{unit} should NOT have gas"


# ── NBN Tests ─────────────────────────────────────────────────────────────────

class TestUtilitiesNBN:
    """NBN sub-object correctness."""

    def test_nbn_has_required_fields(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        nbn = resp.json()["nbn"]
        for field in ["unit_number", "lot_number", "nmi", "property_number", "supplier", "note"]:
            assert field in nbn, f"Missing NBN field: {field}"

    def test_nbn_supplier_is_nbn(self, admin_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        assert "National Broadband Network" in resp.json()["nbn"]["supplier"]

    def test_nbn_note_mentions_person(self, admin_headers):
        """NBN note should explain that connectivity is person-specific."""
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        note = resp.json()["nbn"]["note"].lower()
        assert "person" in note or "resident" in note or "rsp" in note.lower()

    def test_all_units_have_nmi(self, admin_headers):
        """Every unit should have an NMI starting with ENNE."""
        for unit in ["UA001", "UA070", "TH071", "TH087"]:
            resp = requests.get(f"{BASE_URL}/utilities/{unit}", headers=admin_headers)
            nmi = resp.json()["nbn"]["nmi"]
            assert nmi is not None and nmi.startswith("ENNE"), \
                f"{unit} has bad NMI: {nmi!r}"

    def test_nmi_format(self, admin_headers):
        """NMIs should be 10-character strings like ENNE019259."""
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        nmi = resp.json()["nbn"]["nmi"]
        assert len(nmi) == 10
        assert nmi[:4] == "ENNE"

    def test_property_number_format(self, admin_headers):
        """Property numbers should start with ED04."""
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        prop = resp.json()["nbn"]["property_number"]
        assert prop.startswith("ED04"), f"Unexpected property number format: {prop!r}"

    def test_ua001_nmi_is_correct(self, admin_headers):
        """UA001 (lot 1) should have NMI ENNE019259."""
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=admin_headers)
        assert resp.json()["nbn"]["nmi"] == "ENNE019259"

    def test_th017_nmi_is_correct(self, admin_headers):
        """TH087 (lot 87) should have NMI ENNE019345."""
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=admin_headers)
        assert resp.json()["nbn"]["nmi"] == "ENNE019345"


# ── Owner Boundary Tests ──────────────────────────────────────────────────────

class TestUtilitiesOwnerBoundary:
    """Owner unit isolation — cannot cross-access other units."""

    def test_owner_th017_cannot_access_ua001(self, owner_headers):
        resp = requests.get(f"{BASE_URL}/utilities/UA001", headers=owner_headers)
        assert resp.status_code == 403

    def test_owner_th017_cannot_access_th001(self, owner_headers):
        resp = requests.get(f"{BASE_URL}/utilities/TH071", headers=owner_headers)
        assert resp.status_code == 403

    def test_owner_th017_can_access_th017(self, owner_headers):
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=owner_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["unit_number"] == "TH087"
        # TH087 has gas
        assert data["gas"]["has_gas"] is True

    def test_owner_sees_correct_gas_meter_for_own_unit(self, owner_headers):
        """TH087 gas meter is EC 930 372."""
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=owner_headers)
        assert resp.json()["gas"]["meter_number"] == "EC 930 372"

    def test_owner_sees_correct_loc_id_for_own_unit(self, owner_headers):
        """TH087 (lot 87) electricity LOC ID is LOC000186231477."""
        resp = requests.get(f"{BASE_URL}/utilities/TH087", headers=owner_headers)
        assert resp.json()["electricity"]["loc_id"] == "LOC000186231477"
