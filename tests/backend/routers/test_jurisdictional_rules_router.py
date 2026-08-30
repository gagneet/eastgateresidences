"""
Tests for jurisdictional_rules_router — GET /api/jurisdictional-rules/
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from routers.jurisdictional_rules_router import (
    get_building_rules,
    get_all_jurisdictions,
    list_supported_jurisdictions,
    _require_admin_or_manager,
)
from domain.jurisdictional_rules import _VALID_JURISDICTIONS, rule_engine


def _make_user(role="super_admin", building_id="13195"):
    return {"role": role, "effective_role": role, "building_id": building_id}


def _make_db(jurisdiction="ACT"):
    db = MagicMock()
    db.jurisdiction_config.find_one = AsyncMock(return_value=None)
    db.buildings.find_one = AsyncMock(return_value={"jurisdiction": jurisdiction})
    return db


class TestGetBuildingRules:
    @pytest.mark.asyncio
    async def test_returns_rules_for_act_building(self):
        mock_db = _make_db("ACT")
        with patch("routers.jurisdictional_rules_router.db", mock_db):
            result = await get_building_rules(
                building_id="13195",
                current_user=_make_user("super_admin"),
            )
        assert result["jurisdiction"] == "ACT"
        assert result["building_id"] == "13195"
        assert "typed_rules" in result
        assert "full_rules" in result
        typed = result["typed_rules"]
        assert typed["max_interest_rate_pa"] == "10"
        assert typed["pooled_trust_permitted"] is True
        assert typed["can_transfer_between_funds"] is True

    @pytest.mark.asyncio
    async def test_returns_rules_for_qld_building(self):
        mock_db = _make_db("QLD")
        with patch("routers.jurisdictional_rules_router.db", mock_db):
            result = await get_building_rules(
                building_id="99999",
                current_user=_make_user("super_admin"),
            )
        assert result["jurisdiction"] == "QLD"
        typed = result["typed_rules"]
        assert typed["can_transfer_between_funds"] is False
        assert typed["inter_fund_transfer_resolution_required"] == "prohibited"
        assert typed["committee_spending_cap_formula"] is not None

    @pytest.mark.asyncio
    async def test_nsw_trial_balance_21_days(self):
        mock_db = _make_db("NSW")
        with patch("routers.jurisdictional_rules_router.db", mock_db):
            result = await get_building_rules(
                building_id="16244",
                current_user=_make_user("strata_manager"),
            )
        assert result["jurisdiction"] == "NSW"
        assert result["typed_rules"]["trial_balance_deadline_days"] == 21
        assert result["typed_rules"]["interest_belongs_to"] == "statutory_account"

    @pytest.mark.asyncio
    async def test_meta_contains_legislation(self):
        mock_db = _make_db("ACT")
        with patch("routers.jurisdictional_rules_router.db", mock_db):
            result = await get_building_rules(
                building_id="13195",
                current_user=_make_user("super_admin"),
            )
        meta = result["meta"]
        assert "ACT" in str(meta)

    @pytest.mark.asyncio
    async def test_multi_tenant_isolation(self):
        """Building 16244 (QLD) must not return ACT rules."""
        mock_db = _make_db("QLD")
        with patch("routers.jurisdictional_rules_router.db", mock_db):
            result = await get_building_rules(
                building_id="16244",
                current_user=_make_user("super_admin"),
            )
        assert result["jurisdiction"] == "QLD"
        assert result["typed_rules"]["can_transfer_between_funds"] is False


class TestRoleGuard:
    def test_403_for_owner_role(self):
        from fastapi import HTTPException
        user = {"role": "owner", "effective_role": "owner"}
        with pytest.raises(HTTPException) as exc_info:
            _require_admin_or_manager(user)
        assert exc_info.value.status_code == 403

    def test_403_for_tenant_role(self):
        from fastapi import HTTPException
        user = {"role": "tenant", "effective_role": "tenant"}
        with pytest.raises(HTTPException) as exc_info:
            _require_admin_or_manager(user)
        assert exc_info.value.status_code == 403

    def test_ec_member_is_denied(self):
        """EC member (former chairman position) does not have access to jurisdictional rules."""
        from fastapi import HTTPException
        user = {"role": "ec_member", "effective_role": "ec_member"}
        with pytest.raises(HTTPException) as exc_info:
            _require_admin_or_manager(user)
        assert exc_info.value.status_code == 403

    def test_strata_manager_can_access(self):
        user = {"role": "strata_manager", "effective_role": "strata_manager"}
        result = _require_admin_or_manager(user)
        assert result == user

    def test_super_admin_can_access(self):
        user = {"role": "super_admin", "effective_role": "super_admin"}
        result = _require_admin_or_manager(user)
        assert result == user

    def test_effective_role_used_not_raw_role(self):
        """Elevated ec_member has raw role 'owner' but effective_role 'ec_member' — should be denied."""
        from fastapi import HTTPException
        user = {"role": "owner", "effective_role": "ec_member"}
        with pytest.raises(HTTPException):
            _require_admin_or_manager(user)


class TestGetAllJurisdictions:
    @pytest.mark.asyncio
    async def test_returns_all_jurisdictions(self):
        """Asserts against the domain's own _VALID_JURISDICTIONS set rather than a
        hardcoded 4-state list -- this repo covers all 8 AU states/territories
        (SA/WA/TAS/NT joined ACT/NSW/VIC/QLD at some point after this test was
        originally written to a 4-jurisdiction snapshot)."""
        result = await get_all_jurisdictions(current_user=_make_user())
        assert set(result["jurisdictions"].keys()) == set(_VALID_JURISDICTIONS)

    @pytest.mark.asyncio
    async def test_each_jurisdiction_has_typed_rules(self):
        result = await get_all_jurisdictions(current_user=_make_user())
        for jur, data in result["jurisdictions"].items():
            assert "typed_rules" in data, f"Missing typed_rules for {jur}"
            assert "max_interest_rate_pa" in data["typed_rules"]

    @pytest.mark.asyncio
    async def test_qld_transfer_prohibited(self):
        result = await get_all_jurisdictions(current_user=_make_user())
        assert result["jurisdictions"]["QLD"]["typed_rules"]["can_transfer_between_funds"] is False

    @pytest.mark.asyncio
    async def test_nsw_interest_to_statutory_account(self):
        result = await get_all_jurisdictions(current_user=_make_user())
        assert result["jurisdictions"]["NSW"]["typed_rules"]["interest_belongs_to"] == "statutory_account"

    @pytest.mark.asyncio
    async def test_vic_pooled_trust_not_permitted(self):
        result = await get_all_jurisdictions(current_user=_make_user())
        assert result["jurisdictions"]["VIC"]["typed_rules"]["pooled_trust_permitted"] is False


class TestListSupportedJurisdictions:
    @pytest.mark.asyncio
    async def test_returns_all_supported(self):
        result = await list_supported_jurisdictions(current_user=_make_user("owner"))
        assert set(result["jurisdictions"]) == set(_VALID_JURISDICTIONS)
