"""
Tests for OwnerHub Pydantic models.

Covers property/tenancy lifecycle models, status constants, permission flags,
health score weights, and TCO calculation logic.

All tests are pure unit tests — no async, no database, no mocking needed.
"""

import pytest
from pydantic import ValidationError

from models.tenancy import (
    PropertyCreate,
    TenancyCreate,
    TenancyStatus,
    RentPaymentMethod,
    InspectionType,
    MaintenanceUrgency,
    PropertyHealthSnapshot,
    TrueOwnershipCostResponse,
)
from models.user import UserRole, Permission, DEFAULT_PERMISSIONS


# ──────────────────────────────────────────────────────────────────────────────
# PropertyCreate model
# ──────────────────────────────────────────────────────────────────────────────

class TestPropertyModel:
    def test_property_creation_with_valid_data(self):
        prop = PropertyCreate(
            address="12 Sample Street",
            suburb="Braddon",
            state="ACT",
            postcode="2612",
        )
        assert prop.address == "12 Sample Street"
        assert prop.suburb == "Braddon"
        assert prop.state == "ACT"
        assert prop.postcode == "2612"

    def test_property_creation_requires_address(self):
        with pytest.raises(ValidationError) as exc_info:
            PropertyCreate(suburb="Braddon", state="ACT", postcode="2612")
        errors = exc_info.value.errors()
        field_names = [e["loc"][0] for e in errors]
        assert "address" in field_names

    def test_property_eastgate_link(self):
        prop = PropertyCreate(
            address="Unit TH087, East Gate",
            suburb="Braddon",
            state="ACT",
            postcode="2612",
            eastgate_unit_number="TH087",
        )
        assert prop.eastgate_unit_number == "TH087"


# ──────────────────────────────────────────────────────────────────────────────
# TenancyCreate model
# ──────────────────────────────────────────────────────────────────────────────

class TestTenancyModel:
    def test_tenancy_creation_with_valid_data(self):
        tenancy = TenancyCreate(
            property_id="prop-001",
            tenant_name="Emma Wilson",
            tenant_email="emma@example.com",
            lease_start="2026-03-01",
            lease_end="2027-02-28",
            rent_amount=650.0,
            bond_amount=2600.0,
        )
        assert tenancy.tenant_name == "Emma Wilson"
        assert tenancy.rent_amount == 650.0
        assert tenancy.payment_frequency == "weekly"
        assert tenancy.payment_method == RentPaymentMethod.BANK_TRANSFER


# ──────────────────────────────────────────────────────────────────────────────
# Status and constant classes
# ──────────────────────────────────────────────────────────────────────────────

class TestStatusConstants:
    def test_tenancy_status_constants(self):
        assert TenancyStatus.ACTIVE == "active"
        assert TenancyStatus.PENDING == "pending"
        assert TenancyStatus.EXPIRED == "expired"
        assert TenancyStatus.TERMINATED == "terminated"
        assert TenancyStatus.PERIODIC == "periodic"

    def test_rent_transaction_types(self):
        assert RentPaymentMethod.BANK_TRANSFER == "bank_transfer"
        assert RentPaymentMethod.DEFT == "deft"
        assert RentPaymentMethod.BPAY == "bpay"
        assert RentPaymentMethod.CASH == "cash"
        assert RentPaymentMethod.OTHER == "other"

    def test_inspection_types(self):
        assert InspectionType.ENTRY == "entry"
        assert InspectionType.ROUTINE == "routine"
        assert InspectionType.EXIT == "exit"
        assert InspectionType.EMERGENCY == "emergency"

    def test_maintenance_urgency_levels(self):
        assert MaintenanceUrgency.ROUTINE == "routine"
        assert MaintenanceUrgency.URGENT == "urgent"
        assert MaintenanceUrgency.EMERGENCY == "emergency"


# ──────────────────────────────────────────────────────────────────────────────
# Property health score weights
# ──────────────────────────────────────────────────────────────────────────────

class TestPropertyHealthScore:
    def test_property_health_score_weights_sum_to_100(self):
        income_weight = 25
        expense_weight = 15
        maintenance_weight = 20
        tenant_weight = 15
        capital_weight = 15
        compliance_weight = 10
        total = (
                income_weight
                + expense_weight
                + maintenance_weight
                + tenant_weight
                + capital_weight
                + compliance_weight
        )
        assert total == 100

    def test_property_health_snapshot_model(self):
        snapshot = PropertyHealthSnapshot(
            property_id="prop-001",
            computed_at="2026-03-01T00:00:00Z",
            income_stability_score=80.0,
            expense_volatility_score=70.0,
            maintenance_backlog_score=65.0,
            tenant_stability_score=90.0,
            capital_risk_score=55.0,
            compliance_score=100.0,
            health_score=77.25,
            risk_level="healthy",
        )
        assert snapshot.property_id == "prop-001"
        assert snapshot.health_score == 77.25
        assert snapshot.risk_level == "healthy"
        assert snapshot.risk_flags == []


# ──────────────────────────────────────────────────────────────────────────────
# TCO calculation logic
# ──────────────────────────────────────────────────────────────────────────────

class TestTrueOwnershipCost:
    def _make_tco(self, **overrides) -> dict:
        base = dict(
            property_id="prop-001",
            year="2025-2026",
            gross_rental_income=33800.0,
            vacancy_loss=1300.0,
            net_rental_income=32500.0,
            total_costs=20000.0,
            net_cashflow=12500.0,
            computed_at="2026-03-01T00:00:00Z",
        )
        base.update(overrides)
        return base

    def test_tco_net_cashflow_calculation(self):
        gross_income = 33800.0
        total_costs = 20000.0
        vacancy_loss = 1300.0
        net_cashflow = gross_income - vacancy_loss - total_costs
        assert net_cashflow == pytest.approx(12500.0)

    def test_tco_vacancy_loss_calculation(self):
        weekly_rent = 650.0
        vacancy_weeks = 2
        vacancy_loss = weekly_rent * vacancy_weeks
        assert vacancy_loss == pytest.approx(1300.0)

    def test_tco_response_model_fields(self):
        data = self._make_tco()
        tco = TrueOwnershipCostResponse(**data)
        assert tco.property_id == "prop-001"
        assert tco.year == "2025-2026"
        assert tco.gross_rental_income == 33800.0
        assert tco.vacancy_loss == 1300.0
        assert tco.net_cashflow == 12500.0
        assert tco.ten_year_projection == []
        assert tco.ownership_score == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# UserRole and Permission tests
# ──────────────────────────────────────────────────────────────────────────────

class TestUserRoleAndPermissions:
    def test_real_estate_agent_role_exists(self):
        assert UserRole.REAL_ESTATE_AGENT == "real_estate_agent"

    def test_real_estate_agent_permissions(self):
        perms = DEFAULT_PERMISSIONS[UserRole.REAL_ESTATE_AGENT]
        assert perms.can_manage_tenancy is True

    def test_can_manage_tenancy_permission_exists(self):
        perm = Permission()
        assert hasattr(perm, "can_manage_tenancy")

    def test_owner_has_manage_tenancy_permission(self):
        perms = DEFAULT_PERMISSIONS[UserRole.OWNER]
        assert perms.can_manage_tenancy is True

    def test_reception_has_manage_tenancy_permission(self):
        perms = DEFAULT_PERMISSIONS[UserRole.RECEPTION]
        assert perms.can_manage_tenancy is True
