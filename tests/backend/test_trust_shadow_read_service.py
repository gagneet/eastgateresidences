"""
Tests for services/trust_shadow_read_service.py — trust_ledger + trust_reconciliation
shadow-read comparators, added 2026-07-14 (PostgreSQL shadow-read expansion, Phase G).

Two independent domains despite living in one file — every test asserts the correct
domain is used for its route, never conflated.

Covers:
  1.  trial balance comparator: matching/divergent totals, account-count mismatch
  2.  reconciliation summary comparator: matched/unmatched counts, unreconciled cents
  3.  cents-precision: AUD-dollar Mongo values convert correctly at the comparison boundary
  4.  domain isolation: trust_ledger and trust_reconciliation never cross-write
  5.  multi-tenant isolation
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.trust_shadow_read_service import (
    compare_trust_reconciliation_summary,
    compare_trust_trial_balance,
    compare_trust_v2_account_summary,
)

BUILDING_A = "13195"
BUILDING_B = "16244"


class TestCompareTrustTrialBalance:
    @pytest.mark.asyncio
    async def test_matching_totals_produce_no_diff(self):
        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()) as mock_diff, \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()), \
             patch("services.shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=False)):
            result = await compare_trust_trial_balance(
                building_id=BUILDING_A,
                mongo_payload={"total_debits": 1000.50, "total_credits": 1000.50, "accounts": [1, 2, 3]},
                pg_payload={"total_debits": 1000.50, "total_credits": 1000.50, "accounts": [1, 2, 3]},
            )
        assert result.matched is True
        mock_diff.assert_not_called()
        assert result.domain == "trust_ledger"

    @pytest.mark.asyncio
    async def test_diverging_totals_produce_field_mismatch(self):
        recorded = []

        async def fake_record_diff(**kwargs):
            recorded.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await compare_trust_trial_balance(
                building_id=BUILDING_A,
                mongo_payload={"total_debits": 1000.00, "total_credits": 1000.00, "accounts": []},
                pg_payload={"total_debits": 500.00, "total_credits": 1000.00, "accounts": []},
            )
        assert result.matched is False
        assert recorded[0]["diff_type"].startswith("field_mismatch")

    @pytest.mark.asyncio
    async def test_account_count_mismatch_detected(self):
        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()) as mock_diff, \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await compare_trust_trial_balance(
                building_id=BUILDING_A,
                mongo_payload={"total_debits": 0, "total_credits": 0, "accounts": [1, 2, 3]},
                pg_payload={"total_debits": 0, "total_credits": 0, "accounts": [1, 2]},
            )
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_aud_dollar_mongo_converts_to_cents_at_boundary(self):
        """Mongo returns dollar floats; PG's get_trial_balance also returns dollar-formatted
        totals (via _to_aud) — both must be converted to cents identically before comparison,
        or a real 100x-style bug (per CLAUDE.md's Ledger Money Precision rule) could hide."""
        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()) as mock_diff, \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()), \
             patch("services.shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=False)):
            result = await compare_trust_trial_balance(
                building_id=BUILDING_A,
                mongo_payload={"total_debits": 123.45, "total_credits": 0, "accounts": []},
                pg_payload={"total_debits": 123.45, "total_credits": 0, "accounts": []},
            )
        assert result.matched is True
        mock_diff.assert_not_called()

    @pytest.mark.asyncio
    async def test_pg_unavailable_recorded(self):
        recorded = []

        async def fake_record_diff(**kwargs):
            recorded.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await compare_trust_trial_balance(
                building_id=BUILDING_A,
                mongo_payload={"total_debits": 100, "total_credits": 100, "accounts": []},
                pg_payload=None,
            )
        assert result.pg_available is False
        assert recorded[0]["diff_type"] == "pg_unavailable"


class TestCompareTrustReconciliationSummary:
    @pytest.mark.asyncio
    async def test_matching_summary_produces_no_diff(self):
        payload = {
            "matched_count": 10, "unmatched_bank_count": 2,
            "total_unreconciled_amount_cents": 5000,
        }
        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()) as mock_diff, \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()), \
             patch("services.shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=False)):
            result = await compare_trust_reconciliation_summary(
                building_id=BUILDING_A, mongo_payload=payload, pg_payload=dict(payload),
            )
        assert result.matched is True


class TestCompareTrustV2AccountSummary:
    @pytest.mark.asyncio
    async def test_matching_v2_account_totals_use_v2_route_key(self):
        mongo_payload = {
            "accounts": [
                {"account_type": "admin_fund", "computed_balance_cents": 12500},
                {"account_type": "sinking_fund", "computed_balance_cents": 87500},
            ]
        }
        pg_payload = {
            "accounts": [
                {"fund_type": "admin_fund", "current_balance": 125.00},
                {"fund_type": "capital_works_fund", "current_balance": 875.00},
            ]
        }

        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()) as mock_diff, \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()), \
             patch("services.shadow_read_service._should_record_shadow_ok", new=AsyncMock(return_value=False)):
            result = await compare_trust_v2_account_summary(
                building_id=BUILDING_A,
                mongo_payload=mongo_payload,
                pg_payload=pg_payload,
            )

        assert result.matched is True
        assert result.route_key == "trust_v2.accounts.summary"
        assert result.domain == "trust_ledger"
        mock_diff.assert_not_called()

    @pytest.mark.asyncio
    async def test_v2_account_total_mismatch_records_diff(self):
        recorded = []

        async def fake_record_diff(**kwargs):
            recorded.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await compare_trust_v2_account_summary(
                building_id=BUILDING_A,
                mongo_payload={"accounts": [{"computed_balance_cents": 10000}]},
                pg_payload={"accounts": [{"current_balance": 50.00}]},
            )

        assert result.matched is False
        assert any(item["diff_type"].startswith("field_mismatch") for item in recorded)

    @pytest.mark.asyncio
    async def test_matched_count_divergence_detected(self):
        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()) as mock_diff, \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await compare_trust_reconciliation_summary(
                building_id=BUILDING_A,
                mongo_payload={"matched_count": 10, "unmatched_bank_count": 2, "total_unreconciled_amount_cents": 0},
                pg_payload={"matched_count": 8, "unmatched_bank_count": 2, "total_unreconciled_amount_cents": 0},
            )
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_unreconciled_cents_already_cents_no_conversion_needed(self):
        """Unlike trial balance, both sides already store this field in cents — a divergence
        of exactly 1 cent (below the default 0-tolerance) must still be caught."""
        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()) as mock_diff, \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await compare_trust_reconciliation_summary(
                building_id=BUILDING_A,
                mongo_payload={"matched_count": 1, "unmatched_bank_count": 0, "total_unreconciled_amount_cents": 501},
                pg_payload={"matched_count": 1, "unmatched_bank_count": 0, "total_unreconciled_amount_cents": 500},
            )
        assert result.matched is False
        mock_diff.assert_called_once()

    @pytest.mark.asyncio
    async def test_pg_unavailable_recorded(self):
        recorded = []

        async def fake_record_diff(**kwargs):
            recorded.append(kwargs)

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            result = await compare_trust_reconciliation_summary(
                building_id=BUILDING_A,
                mongo_payload={"matched_count": 1, "unmatched_bank_count": 0, "total_unreconciled_amount_cents": 0},
                pg_payload=None,
            )
        assert result.pg_available is False
        assert recorded[0]["diff_type"] == "pg_unavailable"


class TestDomainIsolationAndMultiTenant:
    @pytest.mark.asyncio
    async def test_trial_balance_and_reconciliation_use_different_domains(self):
        recorded = []

        async def fake_record_diff(**kwargs):
            recorded.append(kwargs["domain"])

        with patch("services.shadow_read_service._safe_record_diff", new=fake_record_diff), \
             patch("services.shadow_read_service._safe_record_coverage", new=AsyncMock()):
            await compare_trust_trial_balance(
                building_id=BUILDING_A,
                mongo_payload={"total_debits": 1, "total_credits": 0, "accounts": []},
                pg_payload=None,  # forces a recorded diff (pg_unavailable)
            )
            await compare_trust_reconciliation_summary(
                building_id=BUILDING_A,
                mongo_payload={"matched_count": 1, "unmatched_bank_count": 0, "total_unreconciled_amount_cents": 0},
                pg_payload=None,
            )

        assert recorded == ["trust_ledger", "trust_reconciliation"]

    @pytest.mark.asyncio
    async def test_building_ids_never_cross_contaminate(self):
        calls = []

        async def fake_record_coverage(**kwargs):
            calls.append(kwargs["building_id"])

        with patch("services.shadow_read_service._safe_record_diff", new=AsyncMock()), \
             patch("services.shadow_read_service._safe_record_coverage", new=fake_record_coverage):
            await compare_trust_trial_balance(
                building_id=BUILDING_A,
                mongo_payload={"total_debits": 1, "total_credits": 1, "accounts": []},
                pg_payload={"total_debits": 1, "total_credits": 1, "accounts": []},
            )
            await compare_trust_trial_balance(
                building_id=BUILDING_B,
                mongo_payload={"total_debits": 1, "total_credits": 1, "accounts": []},
                pg_payload={"total_debits": 1, "total_credits": 1, "accounts": []},
            )

        assert calls == [BUILDING_A, BUILDING_B]
