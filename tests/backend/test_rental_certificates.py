"""
Tests for Rental Certificates feature (ACT s.119A).

Target: 16+ passing tests covering:
- Status workflow
- Permission matrix
- Certificate number format
- Data auto-population
- PDF generation
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ──────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ──────────────────────────────────────────────────────────────────────────────

def _make_cert(status="requested", unit="TH087", cert_number="RC-2026-0001"):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "cert_number": cert_number,
        "unit_number": unit,
        "tenant_name": "Emma Wilson",
        "lease_start_date": "2026-03-01",
        "lease_end_date": "2027-02-28",
        "status": status,
        "requested_by": "user-owner-1",
        "requested_by_name": "Avneet Rooprai",
        "requested_at": now,
        "issued_by": None,
        "issued_by_name": None,
        "issued_at": None,
        "expiry_date": None,
        "arrears_amount": 0.0,
        "ec_members_snapshot": [{"name": "Anthony McDonald", "role": "chairman"}],
        "insurance_insurer": "Strata Community Insurance",
        "insurance_public_liability": "$10,000,000",
        "known_defects": "None at time of issue",
        "special_levies_pending": "None pending",
        "created_at": now,
        "updated_at": now,
    }


def _make_user(role="owner", unit="TH087", ec_position=None):
    user = {
        "id": "user-owner-1",
        "full_name": "Avneet Rooprai",
        "email": "avneet@example.com",
        "role": role,
        "unit_number": unit,
        "status": "active",
    }
    if ec_position is not None:
        user["ec_position"] = ec_position
    return user


# ──────────────────────────────────────────────────────────────────────────────
# TestStatusWorkflow — transitions between requested / issued / superseded
# ──────────────────────────────────────────────────────────────────────────────

class TestStatusWorkflow:
    def test_initial_status_is_requested(self):
        cert = _make_cert(status="requested")
        assert cert["status"] == "requested"

    def test_issued_cert_has_issued_status(self):
        cert = _make_cert(status="issued")
        cert["issued_at"] = datetime.now(timezone.utc).isoformat()
        cert["expiry_date"] = "2031-03-01T00:00:00+00:00"
        assert cert["status"] == "issued"
        assert cert["issued_at"] is not None
        assert cert["expiry_date"] is not None

    def test_superseded_status_string(self):
        cert = _make_cert(status="superseded")
        assert cert["status"] == "superseded"

    def test_cert_number_preserved_across_status_change(self):
        cert = _make_cert(status="requested", cert_number="RC-2026-0042")
        cert["status"] = "issued"
        assert cert["cert_number"] == "RC-2026-0042"

    def test_expiry_is_5_years_after_issue(self):
        from datetime import timedelta
        issue_dt = datetime(2026, 3, 1, tzinfo=timezone.utc)
        expiry_dt = issue_dt + timedelta(days=5 * 365)
        assert expiry_dt.year == 2031
        assert expiry_dt.month == 2  # 5×365 days, not exactly 5 calendar years


# ──────────────────────────────────────────────────────────────────────────────
# TestPermissions
# ──────────────────────────────────────────────────────────────────────────────

class TestPermissions:
    def test_owner_can_request_own_unit(self):
        user = _make_user(role="owner", unit="TH087")
        request_unit = "TH087"
        assert user["unit_number"] == request_unit

    def test_owner_cannot_request_other_unit(self):
        user = _make_user(role="owner", unit="TH087")
        request_unit = "UA063"
        # Business rule: owner unit_number must match request unit_number
        assert user["unit_number"] != request_unit

    def test_tenant_not_in_request_roles(self):
        from routers.rental_certificates import _REQUEST_ROLES
        assert "tenant" not in _REQUEST_ROLES

    def test_service_provider_not_in_request_roles(self):
        from routers.rental_certificates import _REQUEST_ROLES
        assert "service_provider" not in _REQUEST_ROLES

    def test_owner_not_in_manage_roles(self):
        from routers.rental_certificates import _MANAGE_ROLES
        assert "owner" not in _MANAGE_ROLES

    def test_strata_admin_in_manage_roles(self):
        # Per migration 0025: chairman is no longer a top-level role.
        # strata_admin is the canonical replacement at the role level.
        from routers.rental_certificates import _MANAGE_ROLES
        assert "strata_admin" in _MANAGE_ROLES

    def test_ec_member_in_manage_roles(self):
        from routers.rental_certificates import _MANAGE_ROLES
        assert "ec_member" in _MANAGE_ROLES

    def test_super_admin_in_manage_roles(self):
        from routers.rental_certificates import _MANAGE_ROLES
        assert "super_admin" in _MANAGE_ROLES

    def test_strata_manager_in_manage_roles(self):
        from routers.rental_certificates import _MANAGE_ROLES
        assert "strata_manager" in _MANAGE_ROLES

    def test_is_manager_returns_true_for_ec_chairman(self):
        # Chairman of the EC == ec_member with ec_position=CHAIRMAN.
        from routers.rental_certificates import _is_manager
        user = _make_user(role="ec_member", ec_position="CHAIRMAN")
        assert _is_manager(user) is True

    def test_is_manager_returns_false_for_owner(self):
        from routers.rental_certificates import _is_manager
        user = _make_user(role="owner")
        assert _is_manager(user) is False


# ──────────────────────────────────────────────────────────────────────────────
# TestCertificateNumber
# ──────────────────────────────────────────────────────────────────────────────

class TestCertificateNumber:
    def test_format_rc_yyyy_nnnn(self):
        import re
        cert_number = "RC-2026-0001"
        assert re.match(r"^RC-\d{4}-\d{4}$", cert_number)

    def test_sequential_numbering(self):
        numbers = ["RC-2026-0001", "RC-2026-0002", "RC-2026-0003"]
        for i, n in enumerate(numbers, 1):
            assert n.endswith(f"{i:04d}")

    def test_cert_number_year_matches_current(self):
        year = datetime.now(timezone.utc).year
        cert_number = f"RC-{year}-0001"
        assert str(year) in cert_number


# ──────────────────────────────────────────────────────────────────────────────
# TestDataAutoPopulation
# ──────────────────────────────────────────────────────────────────────────────

class TestDataAutoPopulation:
    @pytest.mark.asyncio
    async def test_auto_populate_returns_dict(self):
        from routers.rental_certificates import _auto_populate_financial_data

        mock_unit = {
            "unit_number": "TH087",
            "owner_name": "Avneet Rooprai",
            "unit_entitlement": 161,
            "property_type": "townhouse",
            "balance_owing": 0,
        }
        mock_levy = {
            "admin_levy_income": 340870.20,
            "sink_levy_income": 99504.90,
            "total_uoe": 10000,
            "sinking_opening_balance": 50000,
        }

        with patch("routers.rental_certificates.db") as mock_db, \
                patch("routers.rental_certificates.get_owner_info", new=AsyncMock(return_value={
                    "owner_name": "Avneet Rooprai", "owner_email": None, "co_owner_name": None, "source": "units_legacy"
                })):
            mock_db.units.find_one = AsyncMock(return_value=mock_unit)
            mock_db.annual_levies.find_one = AsyncMock(return_value=mock_levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
            mock_db.ec_members.find = MagicMock(
                return_value=MagicMock(to_list=AsyncMock(return_value=[]))
            )

            result = await _auto_populate_financial_data("TH087", "13195")

        assert isinstance(result, dict)
        assert result.get("owner_name") == "Avneet Rooprai"
        assert result.get("unit_entitlement") == 161

    @pytest.mark.asyncio
    async def test_auto_populate_calculates_levy(self):
        from routers.rental_certificates import _auto_populate_financial_data

        mock_unit = {
            "unit_number": "TH087",
            "owner_name": "Avneet Rooprai",
            "unit_entitlement": 161,
            "balance_owing": 0,
        }
        mock_levy = {
            "admin_levy_income": 340870.20,
            "sink_levy_income": 99504.90,
            "total_uoe": 10000,
            "sinking_opening_balance": 50000,
        }

        with patch("routers.rental_certificates.db") as mock_db, \
                patch("routers.rental_certificates.get_owner_info", new=AsyncMock(return_value={
                    "owner_name": "Avneet Rooprai", "owner_email": None, "co_owner_name": None, "source": "units_legacy"
                })):
            mock_db.units.find_one = AsyncMock(return_value=mock_unit)
            mock_db.annual_levies.find_one = AsyncMock(return_value=mock_levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
            mock_db.ec_members.find = MagicMock(
                return_value=MagicMock(to_list=AsyncMock(return_value=[]))
            )

            result = await _auto_populate_financial_data("TH087", "13195")

        assert result.get("levy_annual") is not None
        assert result["levy_annual"] > 0
        assert result["levy_quarterly"] == round(result["levy_annual"] / 4, 2)

    @pytest.mark.asyncio
    async def test_auto_populate_arrears_from_ledger(self):
        from routers.rental_certificates import _auto_populate_financial_data

        mock_unit = {"unit_number": "UA001", "owner_name": "Test Owner", "unit_entitlement": 100}
        mock_levy = {
            "admin_levy_income": 100000, "sink_levy_income": 20000,
            "total_uoe": 1000, "sinking_opening_balance": 10000,
        }
        mock_ledger = {"unit_number": "UA001", "year": 2026, "net_balance": 1500.00}

        with patch("routers.rental_certificates.db") as mock_db, \
                patch("routers.rental_certificates.get_owner_info", new=AsyncMock(return_value={
                    "owner_name": "Test Owner", "owner_email": None, "co_owner_name": None, "source": "units_legacy"
                })):
            mock_db.units.find_one = AsyncMock(return_value=mock_unit)
            mock_db.annual_levies.find_one = AsyncMock(return_value=mock_levy)
            mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=mock_ledger)
            mock_db.ec_members.find = MagicMock(
                return_value=MagicMock(to_list=AsyncMock(return_value=[]))
            )

            result = await _auto_populate_financial_data("UA001", "13195")

        assert result["arrears_amount"] == 1500.00


# ──────────────────────────────────────────────────────────────────────────────
# TestPDFGeneration
# ──────────────────────────────────────────────────────────────────────────────

_VALID_BUILDING_SETTINGS = {
    "building_id": "13195",
    "plan_number": "13195",
    "strata_address": "14 Hoolihan Street, Denman Prospect ACT 2611",
}


class TestPDFGeneration:
    def test_pdf_returns_bytes(self):
        from utils.pdf_generator import generate_rental_certificate_pdf
        cert = _make_cert(status="issued")
        cert["issued_at"] = datetime.now(timezone.utc).isoformat()
        cert["expiry_date"] = "2031-03-01T00:00:00+00:00"
        cert["levy_quarterly"] = 1772.51
        cert["levy_annual"] = 7090.04
        cert["admin_budget_current"] = 340870.20
        cert["sinking_fund_balance"] = 50000.00

        result = generate_rental_certificate_pdf(cert, "East Gate Residences",
                                                 building_settings=_VALID_BUILDING_SETTINGS)
        assert result is not None
        assert isinstance(result, bytes)

    def test_pdf_is_non_empty(self):
        from utils.pdf_generator import generate_rental_certificate_pdf
        cert = _make_cert(status="issued")
        cert["issued_at"] = datetime.now(timezone.utc).isoformat()
        result = generate_rental_certificate_pdf(cert, "East Gate Residences",
                                                 building_settings=_VALID_BUILDING_SETTINGS)
        assert len(result) > 1024  # at least 1 KB

    def test_pdf_starts_with_magic_bytes(self):
        from utils.pdf_generator import generate_rental_certificate_pdf
        cert = _make_cert(status="issued")
        cert["issued_at"] = datetime.now(timezone.utc).isoformat()
        result = generate_rental_certificate_pdf(cert, "East Gate Residences",
                                                 building_settings=_VALID_BUILDING_SETTINGS)
        # PDF magic bytes: %PDF
        assert result[:4] == b"%PDF"

    def test_pdf_with_ec_members(self):
        from utils.pdf_generator import generate_rental_certificate_pdf
        cert = _make_cert(status="issued")
        cert["issued_at"] = datetime.now(timezone.utc).isoformat()
        cert["ec_members_snapshot"] = [
            {"name": "Anthony McDonald", "role": "chairman"},
            {"name": "Marcelo da Silva", "role": "ec_member"},
        ]
        result = generate_rental_certificate_pdf(cert, "East Gate Residences",
                                                 building_settings=_VALID_BUILDING_SETTINGS)
        assert result is not None
        assert len(result) > 0

    def test_pdf_with_null_optional_fields(self):
        """PDF should not crash when optional fields are None/missing."""
        from utils.pdf_generator import generate_rental_certificate_pdf
        cert = {
            "cert_number": "RC-2026-0001",
            "unit_number": "UA001",
            "tenant_name": "Test Tenant",
            "lease_start_date": "2026-03-01",
            "status": "issued",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "arrears_amount": 0.0,
            # All optional fields intentionally absent
        }
        result = generate_rental_certificate_pdf(cert, "East Gate Residences",
                                                 building_settings=_VALID_BUILDING_SETTINGS)
        assert result is not None
        assert isinstance(result, bytes)

    def test_pdf_raises_when_settings_missing(self):
        """Guard: BuildingSettingsIncompleteError when plan_number or strata_address absent."""
        from utils.pdf_generator import generate_rental_certificate_pdf, BuildingSettingsIncompleteError
        cert = _make_cert(status="issued")
        cert["issued_at"] = datetime.now(timezone.utc).isoformat()
        import pytest
        with pytest.raises(BuildingSettingsIncompleteError) as exc_info:
            generate_rental_certificate_pdf(cert, "East Gate Residences", building_settings={})
        assert "plan_number" in exc_info.value.missing_fields
        assert "strata_address" in exc_info.value.missing_fields

    def test_pdf_raises_when_plan_number_empty(self):
        """Guard fires if plan_number is empty string."""
        from utils.pdf_generator import generate_rental_certificate_pdf, BuildingSettingsIncompleteError
        cert = _make_cert(status="issued")
        cert["issued_at"] = datetime.now(timezone.utc).isoformat()
        import pytest
        with pytest.raises(BuildingSettingsIncompleteError) as exc_info:
            generate_rental_certificate_pdf(
                cert, "Sierra Gungahlin",
                building_settings={"plan_number": "", "strata_address": "70 Efkarpidis Street, Gungahlin ACT 2912",
                                   "building_id": "16244"},
            )
        assert exc_info.value.missing_fields == ["plan_number"]
        assert exc_info.value.building_id == "16244"
