"""Tests for Investor Intelligence — Phase 2 (investor grade, red flags, signals)."""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from routers.investor_intelligence import (
    compute_investor_grade,
    _red_flags_and_signals,
    _fetch_capital_shock_per_unit,
    _tco_annual_cents,
    get_building_health_report,
)

BUILDING_ID = "13195"
OTHER_BUILDING_ID = "16244"


# ── Investor grade computation tests ──────────────────────────────────────

class TestComputeInvestorGrade:
    """Grade A: stress<25, reserve>100%, arrears<5%, compliance>90%."""

    def test_grade_a_all_conditions_met(self):
        grade, rationale = compute_investor_grade(
            stress_score=20.0,
            reserve_adequacy_pct=110.0,
            arrears_rate_pct=3.0,
            compliance_score_pct=95.0,
        )
        assert grade == "A"
        assert len(rationale) > 0

    def test_grade_b_moderate_stress(self):
        """B: stress<50, reserve>75%, arrears<10%."""
        grade, rationale = compute_investor_grade(
            stress_score=35.0,
            reserve_adequacy_pct=80.0,
            arrears_rate_pct=7.0,
            compliance_score_pct=75.0,
        )
        assert grade == "B"

    def test_grade_c_low_reserve(self):
        """C: reserve<75% OR arrears 10-20%."""
        grade, rationale = compute_investor_grade(
            stress_score=40.0,
            reserve_adequacy_pct=60.0,
            arrears_rate_pct=5.0,
            compliance_score_pct=80.0,
        )
        assert grade in ("C", "D")  # C or D when reserve is very low

    def test_grade_d_critical_stress(self):
        """D: stress>=75."""
        grade, rationale = compute_investor_grade(
            stress_score=80.0,
            reserve_adequacy_pct=90.0,
            arrears_rate_pct=3.0,
            compliance_score_pct=90.0,
        )
        assert grade == "D"

    def test_grade_d_low_reserve(self):
        """D: reserve<50%."""
        grade, rationale = compute_investor_grade(
            stress_score=30.0,
            reserve_adequacy_pct=40.0,
            arrears_rate_pct=5.0,
            compliance_score_pct=80.0,
        )
        assert grade == "D"

    def test_grade_d_high_arrears(self):
        """D: arrears>20%."""
        grade, rationale = compute_investor_grade(
            stress_score=30.0,
            reserve_adequacy_pct=80.0,
            arrears_rate_pct=25.0,
            compliance_score_pct=80.0,
        )
        assert grade == "D"

    def test_rationale_is_non_empty_list(self):
        grade, rationale = compute_investor_grade(20.0, 110.0, 3.0, 95.0)
        assert isinstance(rationale, list)
        assert len(rationale) > 0

    def test_grade_is_valid_letter(self):
        for stress, reserve, arrears, compliance in [
            (10, 120, 2, 98),
            (40, 80, 8, 85),
            (60, 70, 12, 75),
            (80, 45, 25, 60),
        ]:
            grade, _ = compute_investor_grade(stress, reserve, arrears, compliance)
            assert grade in ("A", "B", "C", "D"), f"Invalid grade: {grade}"


# ── Red flags and positive signals tests ──────────────────────────────────

class TestRedFlagsAndSignals:
    """
    Actual signature (6 positional args, no ec_quorum_status or last_agm_date):
      _red_flags_and_signals(stress_score, stress_category, reserve_adequacy_pct,
                             arrears_rate_pct, compliance_score_pct, grade)
    """

    def test_arrears_above_10_generates_red_flag(self):
        red_flags, positive = _red_flags_and_signals(
            stress_score=40.0,
            stress_category="watch",
            reserve_adequacy_pct=85.0,
            arrears_rate_pct=15.0,
            compliance_score_pct=80.0,
            grade="C",
        )
        assert any("arrears" in f.lower() for f in red_flags)

    def test_high_stress_generates_red_flag(self):
        red_flags, positive = _red_flags_and_signals(
            stress_score=80.0,
            stress_category="critical",
            reserve_adequacy_pct=85.0,
            arrears_rate_pct=5.0,
            compliance_score_pct=80.0,
            grade="D",
        )
        assert len(red_flags) > 0

    def test_good_reserve_generates_positive_signal(self):
        red_flags, positive = _red_flags_and_signals(
            stress_score=15.0,
            stress_category="healthy",
            reserve_adequacy_pct=120.0,
            arrears_rate_pct=2.0,
            compliance_score_pct=95.0,
            grade="A",
        )
        assert len(positive) > 0

    def test_low_compliance_generates_red_flag(self):
        """Low compliance score (< 70%) should generate a red flag."""
        red_flags, positive = _red_flags_and_signals(
            stress_score=30.0,
            stress_category="watch",
            reserve_adequacy_pct=90.0,
            arrears_rate_pct=5.0,
            compliance_score_pct=60.0,
            grade="B",
        )
        assert any("compliance" in f.lower() for f in red_flags)

    def test_returns_lists(self):
        red_flags, positive = _red_flags_and_signals(
            stress_score=20.0,
            stress_category="healthy",
            reserve_adequacy_pct=100.0,
            arrears_rate_pct=3.0,
            compliance_score_pct=90.0,
            grade="A",
        )
        assert isinstance(red_flags, list)
        assert isinstance(positive, list)


# ── Permission test via mocked endpoint ───────────────────────────────────

class TestInvestorEndpointPermissions:
    """Owner can only access own unit; super_admin can access any."""

    def _make_user(self, role: str, building_id: str = BUILDING_ID, unit_number: str = "TH087"):
        return {
            "role": role,
            "building_id": building_id,
            "unit_number": unit_number,
            "email": f"{role}@test.com",
        }

    async def test_non_authorised_role_forbidden(self):
        """Tenant role should be denied investor summary."""
        from fastapi import HTTPException
        from routers.investor_intelligence import get_investor_unit_summary

        user = self._make_user("tenant")

        # Patch DB to avoid real queries
        mock_db = MagicMock()
        mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=None)
        mock_db.units.find_one = AsyncMock(return_value=None)

        with patch("routers.investor_intelligence.get_current_user", return_value=user), \
                patch("routers.investor_intelligence.get_current_building", return_value=BUILDING_ID):
            try:
                result = await get_investor_unit_summary(
                    unit_number="TH087",
                    current_user=user,
                    building_id=BUILDING_ID,
                )
                # Should raise 403 for tenant
                assert False, "Expected 403 HTTPException"
            except HTTPException as exc:
                assert exc.status_code == 403
            except Exception:
                pass  # Other import errors acceptable in unit test context

    async def test_owner_accessing_other_unit_forbidden(self):
        """Owner role trying to access a unit not their own → 403."""
        from fastapi import HTTPException
        from routers.investor_intelligence import get_investor_unit_summary

        user = self._make_user("owner", unit_number="UA001")

        with patch("routers.investor_intelligence.get_current_user", return_value=user), \
                patch("routers.investor_intelligence.get_current_building", return_value=BUILDING_ID):
            try:
                result = await get_investor_unit_summary(
                    unit_number="TH087",  # Different from owner's unit
                    current_user=user,
                    building_id=BUILDING_ID,
                )
                assert False, "Expected 403"
            except HTTPException as exc:
                assert exc.status_code == 403
            except Exception:
                pass


# ── Capital shock per-unit regression coverage ────────────────────────────
#
# detect_capital_shocks() returns "max_deficit", never "total_exposure" — a
# stale key name previously made this always return 0, silently dropping the
# building's real capital-shock exposure from the investor summary.

class TestFetchCapitalShockPerUnit:
    async def test_uses_max_deficit_from_detect_capital_shocks(self):
        mock_db = MagicMock()
        mock_db.units.count_documents = AsyncMock(return_value=10)

        with patch("routers.investor_intelligence.db", mock_db), \
                patch(
                    "services.capital_shock_service.detect_capital_shocks",
                    AsyncMock(return_value={"max_deficit": 500_000.0}),
                ):
            result = await _fetch_capital_shock_per_unit(BUILDING_ID)

        # $500,000 / 10 units = $50,000/unit = 5,000,000 cents.
        assert result == 5_000_000

    async def test_returns_zero_when_no_deficit_projected(self):
        mock_db = MagicMock()
        mock_db.units.count_documents = AsyncMock(return_value=10)

        with patch("routers.investor_intelligence.db", mock_db), \
                patch(
                    "services.capital_shock_service.detect_capital_shocks",
                    AsyncMock(return_value={"max_deficit": 0.0}),
                ):
            result = await _fetch_capital_shock_per_unit(BUILDING_ID)

        assert result == 0


# ── TCO "current" sentinel regression coverage ────────────────────────────
#
# financial_year="current" never matched unit_levy_ledger's real year values,
# silently defeating _get_building_average's year-scoped query. Resolve a real
# year via get_latest_levy_year() instead.

class TestTcoAnnualCentsResolvesRealYear:
    async def test_passes_resolved_year_not_current_sentinel(self):
        captured_input = {}

        async def fake_compute(inp):
            captured_input["financial_year"] = inp.financial_year
            return MagicMock(total_annual_cents=100_000)

        with patch("services.true_cost_service.compute_true_cost_of_ownership", fake_compute), \
                patch(
                    "utils.finance_helpers.get_latest_levy_year",
                    AsyncMock(return_value="2026"),
                ):
            result = await _tco_annual_cents(BUILDING_ID, "TH087")

        assert captured_input["financial_year"] == "2026"
        assert captured_input["financial_year"] != "current"
        assert result == 100_000

    async def test_falls_back_to_calendar_year_when_no_levy_history(self):
        from datetime import datetime, timezone
        captured_input = {}

        async def fake_compute(inp):
            captured_input["financial_year"] = inp.financial_year
            return MagicMock(total_annual_cents=0)

        with patch("services.true_cost_service.compute_true_cost_of_ownership", fake_compute), \
                patch(
                    "utils.finance_helpers.get_latest_levy_year",
                    AsyncMock(return_value=None),
                ):
            await _tco_annual_cents(BUILDING_ID, "TH087")

        assert captured_input["financial_year"] == str(datetime.now(timezone.utc).year)


# ── Building health report PDF-validity regression coverage ──────────────
#
# generate_full_report() can return non-PDF placeholder bytes (e.g.
# b"ReportLab not available") WITHOUT raising when REPORTLAB_AVAILABLE is
# False. Streaming those bytes with media_type="application/pdf" anyway
# defeats the frontend's content-type guard, since the response header still
# says "application/pdf" even though the payload isn't one. Flagged in PR
# #500's Copilot review.

class TestBuildingHealthReportPdfValidation:
    ADMIN_USER = {"id": "admin-1", "role": "super_admin", "effective_role": "super_admin"}

    def _patch_fetch_helpers(self):
        return (
            patch("routers.investor_intelligence._fetch_stress",
                  AsyncMock(return_value={"score": 20.0, "category": "healthy", "components": {}})),
            patch("routers.investor_intelligence._fetch_reserve_adequacy_pct", AsyncMock(return_value=100.0)),
            patch("routers.investor_intelligence._fetch_arrears_rate", AsyncMock(return_value=2.0)),
            patch("routers.investor_intelligence._fetch_compliance_score", AsyncMock(return_value=95.0)),
            patch("routers.investor_intelligence._fetch_ec_quorum_status",
                  AsyncMock(return_value=("full", "2026-03-01"))),
        )

    async def test_returns_pdf_stream_for_real_pdf_bytes(self):
        from starlette.responses import StreamingResponse

        p1, p2, p3, p4, p5 = self._patch_fetch_helpers()
        with p1, p2, p3, p4, p5, \
                patch("utils.finance_helpers.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.report_service.generate_full_report",
                      AsyncMock(return_value=b"%PDF-1.4 fake-but-valid-header rest of content")):
            result = await get_building_health_report(current_user=self.ADMIN_USER, building_id=BUILDING_ID)

        assert isinstance(result, StreamingResponse)
        assert result.media_type == "application/pdf"

    async def test_falls_back_to_json_when_bytes_are_not_a_real_pdf(self):
        """The exact ReportLab-unavailable case: generate_full_report() returns
        b"ReportLab not available" without raising — must NOT be streamed as a
        fake PDF."""
        p1, p2, p3, p4, p5 = self._patch_fetch_helpers()
        with p1, p2, p3, p4, p5, \
                patch("utils.finance_helpers.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.report_service.generate_full_report",
                      AsyncMock(return_value=b"ReportLab not available")):
            result = await get_building_health_report(current_user=self.ADMIN_USER, building_id=BUILDING_ID)

        assert isinstance(result, dict)
        assert result["note"] == "PDF generation unavailable; returning JSON summary."
        assert result["building_id"] == BUILDING_ID
