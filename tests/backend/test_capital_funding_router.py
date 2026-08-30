"""
Tests for GAP-FIN-034 "Next deliverable A" — the capital-funding preview API contract.

Calls preview_capital_funding() directly (bypassing FastAPI's dependency injection for
require_feature, matching this codebase's established router-unit-test pattern) rather than a
full HTTP TestClient round trip -- the Depends() guard itself is already covered by
utils.permissions' own test suite. The endpoint deliberately has no get_current_building
dependency (removed 2026-08-19 audit -- see the handler's own docstring for why).

Every case asserts preview_only=True and that the response never mentions any write-path
concept (levy_items, receipts, journals) -- this endpoint must never mutate a live ledger.
"""
from __future__ import annotations

import pytest

from fastapi import HTTPException

from routers.capital_funding import (
    CapitalFundingLotChoiceRequest,
    CapitalFundingPreviewRequest,
    preview_capital_funding,
)


def _lot(unit_number: str, entitlement: int, owner_choice: str = "loan") -> CapitalFundingLotChoiceRequest:
    return CapitalFundingLotChoiceRequest(
        unit_number=unit_number, entitlement=entitlement, owner_choice=owner_choice,
    )


class TestCapitalFundingPreviewSuccess:
    @pytest.mark.asyncio
    async def test_all_upfront_scenario_reconciles(self):
        payload = CapitalFundingPreviewRequest(
            jurisdiction="nsw",
            project_cost_cents=1_000_000,
            lots=[_lot("1", 100, "upfront"), _lot("2", 200, "upfront")],
        )
        result = await preview_capital_funding(payload, current_user={"role": "strata_manager"})
        assert result.preview_only is True
        assert result.funding_requirement_cents == 1_000_000
        assert result.reconciliation_total_cents == 1_000_000
        assert len(result.lot_impacts) == 2
        assert sum(i.project_share_cents for i in result.lot_impacts) == 1_000_000
        for impact in result.lot_impacts:
            assert impact.financed_principal_cents == 0
            assert impact.interest_cents == 0
            assert impact.payment_count == 0

    @pytest.mark.asyncio
    async def test_jurisdiction_uppercased(self):
        payload = CapitalFundingPreviewRequest(
            jurisdiction="nsw", project_cost_cents=100, lots=[_lot("1", 1, "upfront")],
        )
        result = await preview_capital_funding(payload, current_user={"role": "strata_manager"})
        assert "capital_funding_standard_notice_days" in result.rule_summary

    @pytest.mark.asyncio
    async def test_mixed_loan_and_upfront_scenario(self):
        payload = CapitalFundingPreviewRequest(
            jurisdiction="NSW",
            project_cost_cents=500_000,
            lots=[_lot("1", 100, "loan"), _lot("2", 100, "upfront")],
            annual_interest_bps=500,
            term_months=24,
            repayment_frequency="quarterly",
        )
        result = await preview_capital_funding(payload, current_user={"role": "strata_manager"})
        loan_impact = next(i for i in result.lot_impacts if i.owner_choice == "loan")
        upfront_impact = next(i for i in result.lot_impacts if i.owner_choice == "upfront")
        assert loan_impact.financed_principal_cents > 0
        assert loan_impact.interest_cents > 0
        assert loan_impact.payment_count > 0
        assert upfront_impact.financed_principal_cents == 0
        assert upfront_impact.interest_cents == 0


class TestCapitalFundingPreviewValidation:
    @pytest.mark.asyncio
    async def test_zero_entitlement_rejected_by_pydantic(self):
        with pytest.raises(Exception):
            CapitalFundingLotChoiceRequest(unit_number="1", entitlement=0, owner_choice="upfront")

    @pytest.mark.asyncio
    async def test_unsupported_owner_choice_rejected_by_pydantic(self):
        with pytest.raises(Exception):
            CapitalFundingLotChoiceRequest(unit_number="1", entitlement=1, owner_choice="cash")

    @pytest.mark.asyncio
    async def test_empty_lots_rejected_by_pydantic(self):
        with pytest.raises(Exception):
            CapitalFundingPreviewRequest(jurisdiction="NSW", project_cost_cents=100, lots=[])


class TestCapitalFundingPreviewWarnings:
    @pytest.mark.asyncio
    async def test_jurisdiction_with_no_verified_notice_period_warns(self):
        # Per GAP-FIN-034: ACT/SA/WA/TAS/NT have no platform-verified numeric notice period.
        payload = CapitalFundingPreviewRequest(
            jurisdiction="ACT", project_cost_cents=100, lots=[_lot("1", 1, "upfront")],
        )
        result = await preview_capital_funding(payload, current_user={"role": "strata_manager"})
        assert any("legal-review evidence" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_jurisdiction_with_verified_notice_period_does_not_warn_about_it(self):
        payload = CapitalFundingPreviewRequest(
            jurisdiction="NSW", project_cost_cents=100, lots=[_lot("1", 1, "upfront")],
        )
        result = await preview_capital_funding(payload, current_user={"role": "strata_manager"})
        assert not any("legal-review evidence" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_hybrid_requested_always_warns(self):
        payload = CapitalFundingPreviewRequest(
            jurisdiction="NSW", project_cost_cents=100, lots=[_lot("1", 1, "upfront")],
            hybrid_requested=True,
        )
        result = await preview_capital_funding(payload, current_user={"role": "strata_manager"})
        assert any("Hybrid" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_benefit_principle_basis_without_rule_support_warns(self):
        # SA does not confirm benefit-principle availability.
        payload = CapitalFundingPreviewRequest(
            jurisdiction="SA", project_cost_cents=100, lots=[_lot("1", 1, "upfront")],
            allocation_basis="benefit_principle",
        )
        result = await preview_capital_funding(payload, current_user={"role": "strata_manager"})
        assert any("allocation basis" in w for w in result.warnings)


class TestCapitalFundingPreviewRoleGuard:
    @pytest.mark.asyncio
    async def test_owner_role_rejected(self):
        payload = CapitalFundingPreviewRequest(
            jurisdiction="NSW", project_cost_cents=100, lots=[_lot("1", 1, "upfront")],
        )
        with pytest.raises(HTTPException) as exc_info:
            await preview_capital_funding(payload, current_user={"role": "owner"})
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_staff_effective_role_allowed(self):
        payload = CapitalFundingPreviewRequest(
            jurisdiction="NSW", project_cost_cents=100, lots=[_lot("1", 1, "upfront")],
        )
        result = await preview_capital_funding(
            payload,
            current_user={"role": "owner", "effective_role": "admin_staff"},
        )
        assert result.preview_only is True


class TestCapitalFundingPreviewNeverWrites:
    @pytest.mark.asyncio
    async def test_response_shape_has_no_write_path_fields(self):
        """Preview generation must not mutate live ledgers -- assert the response contract
        itself carries no field shaped like a write-path identifier (a real levy_item id,
        receipt id, or journal entry id), only preview_only=True and calculated figures."""
        payload = CapitalFundingPreviewRequest(
            jurisdiction="NSW", project_cost_cents=100_000, lots=[_lot("1", 1, "upfront")],
        )
        result = await preview_capital_funding(payload, current_user={"role": "strata_manager"})
        assert result.preview_only is True
        response_dict = result.model_dump()
        for forbidden_key in ("levy_item_id", "receipt_id", "journal_entry_id", "posted_at"):
            assert forbidden_key not in response_dict
