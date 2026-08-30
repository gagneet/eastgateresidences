"""
Tests for insurance management — Gap 1.1
Covers: policy CRUD, compliance checks, broker management, claims with timeline.
"""
import os
import sys
import importlib.util
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


def _load_insurance_seed():
    spec = importlib.util.spec_from_file_location(
        "test_insurance_seed",
        os.path.join(_backend_dir, "seeds", "insurance.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BROKERS, module.POLICIES


BROKERS, POLICIES = _load_insurance_seed()


def _manager_user():
    return {"id": "u1", "full_name": "Test Manager", "role": "strata_manager",
            "effective_role": "strata_manager", "is_approved": True, "building_id": "13195"}


def _mock_db():
    db = MagicMock()
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.__aiter__ = AsyncMock(return_value=iter([]))
    cursor.to_list = AsyncMock(return_value=[])
    db.insurance_policies.find.return_value = cursor
    db.insurance_policies.find_one = AsyncMock(return_value=None)
    db.insurance_policies.insert_one = AsyncMock(return_value=MagicMock(inserted_id="abc"))
    db.insurance_policies.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    db.insurance_policies.count_documents = AsyncMock(return_value=0)
    db.insurance_brokers.find.return_value = cursor
    db.insurance_brokers.insert_one = AsyncMock(return_value=MagicMock(inserted_id="brk1"))
    db.insurance_brokers.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    db.insurance_brokers.find_one = AsyncMock(return_value=None)
    db.insurance_claims.find.return_value = cursor
    db.insurance_claims.find_one = AsyncMock(return_value=None)
    db.insurance_claims.insert_one = AsyncMock(return_value=MagicMock(inserted_id="clm1"))
    db.insurance_claims.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    return db


# ── Policy model tests ────────────────────────────────────────────────────────

class TestInsurancePolicyModels:
    def test_policy_type_constants(self):
        from models.insurance import PolicyType
        assert PolicyType.BUILDING == "building"
        assert PolicyType.PUBLIC_LIABILITY == "public_liability"

    def test_policy_create_schema(self):
        from models.insurance import InsurancePolicyCreate
        p = InsurancePolicyCreate(
            building_id="13195",
            policy_type="building",
            insurer_name="CHU",
            policy_number="CHU-001",
            cover_amount=18_000_000,
            premium_annual=42_000,
            start_date="2025-07-01",
            expiry_date="2026-07-01",
        )
        assert p.policy_type == "building"
        assert p.cover_amount == 18_000_000

    def test_broker_model(self):
        from models.insurance import InsuranceBrokerCreate
        b = InsuranceBrokerCreate(
            broker_name="Sharon Mitchell",
            afsl_number="234567",
            commission_disclosure="12% on building policies",
        )
        assert b.broker_name == "Sharon Mitchell"
        assert b.building_id == ""  # default, set from auth


# ── Policy status helpers ─────────────────────────────────────────────────────

class TestPolicyStatusHelpers:
    def test_determine_status_active(self):
        from routers.insurance import _determine_status
        future = (date.today() + timedelta(days=90)).isoformat()
        assert _determine_status(future) == "active"

    def test_determine_status_pending_renewal(self):
        from routers.insurance import _determine_status
        soon = (date.today() + timedelta(days=30)).isoformat()
        assert _determine_status(soon) == "pending_renewal"

    def test_determine_status_expired(self):
        from routers.insurance import _determine_status
        past = (date.today() - timedelta(days=1)).isoformat()
        assert _determine_status(past) == "expired"

    def test_days_until(self):
        from routers.insurance import _days_until
        future = (date.today() + timedelta(days=45)).isoformat()
        assert _days_until(future) == 45

    def test_compliance_check_expired_non_compliant(self):
        from routers.insurance import _compliance_check
        policy = {"policy_type": "building", "status": "expired"}
        assert _compliance_check(policy, {}) == "non_compliant"

    def test_compliance_check_pli_below_minimum(self):
        from routers.insurance import _compliance_check
        policy = {"policy_type": "public_liability", "status": "active",
                  "public_liability_amount": 5_000_000}
        rules = {"public_liability_minimum_aud": {"value": 10_000_000}}
        assert _compliance_check(policy, rules) == "non_compliant"

    def test_compliance_check_pli_meets_minimum(self):
        from routers.insurance import _compliance_check
        policy = {"policy_type": "public_liability", "status": "active",
                  "public_liability_amount": 20_000_000}
        rules = {"public_liability_minimum_aud": {"value": 10_000_000}}
        assert _compliance_check(policy, rules) == "compliant"


# ── Claims model tests ────────────────────────────────────────────────────────

class TestInsuranceClaimModels:
    def test_claim_create_schema(self):
        from models.insurance_claim import InsuranceClaimCreate
        c = InsuranceClaimCreate(
            building_id="13195",
            insurer="CHU",
            incident_date="2025-09-14",
            description="Water damage to unit 12",
            estimated_cost_cents=840000,
            excess_cents=500000,
        )
        assert c.insurer == "CHU"
        assert c.estimated_cost_cents == 840000

    def test_claim_update_schema(self):
        from models.insurance_claim import InsuranceClaimUpdate
        u = InsuranceClaimUpdate(settlement_amount_cents=750000, assessor_name="Ben Jacobs")
        assert u.settlement_amount_cents == 750000

    def test_timeline_entry_schema(self):
        from models.insurance_claim import InsuranceClaimTimelineEntry
        t = InsuranceClaimTimelineEntry(
            milestone="Claim lodged with insurer",
            occurred_at="2025-09-15T09:00:00+10:00",
            recorded_by="u1",
        )
        assert t.milestone == "Claim lodged with insurer"

    def test_claim_response_has_timeline(self):
        from models.insurance_claim import InsuranceClaimResponse
        r = InsuranceClaimResponse(
            id="c1", building_id="13195", insurer="CHU", incident_date="2025-09-14",
            description="desc", created_by="u1", created_at="2025-09-14T00:00:00Z",
            updated_at="2025-09-14T00:00:00Z", status="reported",
        )
        assert r.timeline == []
        assert r.settlement_amount_cents is None


# ── Multi-tenant isolation ────────────────────────────────────────────────────

class TestMultiTenantIsolation:
    @pytest.mark.asyncio
    async def test_list_policies_scoped_to_building(self):
        """Policies from building 16244 must not appear in 13195 results."""
        from routers.insurance import list_insurance_policies
        from request_context import set_ctx_building_id

        mock_db = _mock_db()

        # Fix async iteration: the cursor returned by find() must support async iteration
        async def _aiter():
            return
            yield  # make it an async generator

        async_cursor = MagicMock()
        async_cursor.sort = MagicMock(return_value=async_cursor)
        async_cursor.skip = MagicMock(return_value=async_cursor)
        async_cursor.limit = MagicMock(return_value=async_cursor)
        async_cursor.__aiter__ = lambda _: _aiter()
        mock_db.insurance_policies.find.return_value = async_cursor
        mock_db.insurance_policies.count_documents = AsyncMock(return_value=0)

        set_ctx_building_id("13195")
        with patch("routers.insurance._get_db", return_value=mock_db):
            result = await list_insurance_policies(
                building_id="13195",
                current_user=_manager_user(),
                policy_type=None,
                policy_status=None,
                page=1,
                page_size=20,
            )

        # The find call must include building_id filter
        call_args = mock_db.insurance_policies.find.call_args
        assert call_args is not None
        query_arg = call_args[0][0]
        assert query_arg.get("building_id") == "13195"


# ── Insurance seed ────────────────────────────────────────────────────────────

class TestInsuranceSeed:
    def test_seed_brokers_defined_for_all_buildings(self):
        assert "13195" in BROKERS
        assert "16244" in BROKERS
        assert "18932" in BROKERS

    def test_seed_policies_cover_all_buildings(self):
        """East Gate and Sierra only.

        Harbourview (18932) was removed on 2026-08-20 and its insurance policy rows were
        deleted with the rest of its seed data, so asserting its presence would now be
        asserting that a removed building is still being seeded.
        """
        building_ids = {p["building_id"] for p in POLICIES}
        assert "13195" in building_ids
        assert "16244" in building_ids
        assert "18932" not in building_ids

    def test_eastgate_has_building_and_pli(self):
        eg = [p for p in POLICIES if p["building_id"] == "13195"]
        types = {p["policy_type"] for p in eg}
        assert "building" in types
        assert "public_liability" in types

    def test_eastgate_pli_meets_act_minimum(self):
        pli = next(p for p in POLICIES if p["building_id"] == "13195" and p["policy_type"] == "public_liability")
        assert pli["public_liability_amount"] >= 10_000_000

    def test_nsw_policies_have_three_quotes(self):
        """Seed policies with quotes must have minimum 3 (PSABAA 2002 s.47 — enforced in router)."""
        for p in POLICIES:
            if p.get("quotes"):
                assert len(p["quotes"]) >= 3
