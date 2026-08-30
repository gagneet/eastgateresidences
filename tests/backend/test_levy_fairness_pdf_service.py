"""
Tests for levy_fairness_pdf_service.py — AGM Committee Report (PDF) and AGM Presentation (PPTX).

Run with:
    cd /home/gagneet/strata-management/backend
    venv/bin/pytest tests/backend/test_levy_fairness_pdf_service.py -v
"""
from unittest.mock import AsyncMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_full_result() -> dict:
    """Full fairness result with all sections populated."""
    return {
        "building_id": "13195",
        "computed_at": "2026-03-10T10:00:00Z",
        "lei_score": 91,
        "sei_scheme": 0.12,
        "total_budget": 440375.10,
        "lbfi": {
            "current_score": 91,
            "benefit_score": 100,
            "fairness_gain": 9,
            "D": 0.045,
            "interpretation": "Good — minor cross-subsidies present",
        },
        "subsidy_map": {
            "total_cost_basis": 440375.10,
            "group_summary": [
                {
                    "group": "Apartment",
                    "count": 70,
                    "ue": 8540,
                    "current_total": 376521.50,
                    "benefit_total": 399120.00,
                    "change_pct": 6.0,
                    "role": "Recipient",
                    "net_subsidy": -22598.50,
                },
                {
                    "group": "Townhouse",
                    "count": 17,
                    "ue": 2210,
                    "current_total": 63853.60,
                    "benefit_total": 41255.10,
                    "change_pct": -35.4,
                    "role": "Contributor",
                    "net_subsidy": 22598.50,
                },
            ],
            "flows": [
                {"from": "Townhouse", "to": "Apartment", "amount": 22598.50},
            ],
        },
        "facility_breakdown": [
            {"facility_name": "Lifts", "annual_cost": 25000, "benefit_group_id": "apt-only", "tier": "apartment"},
            {"facility_name": "Grounds", "annual_cost": 50000, "benefit_group_id": "global", "tier": "global"},
        ],
        "unit_impact": [
            {
                "unit_number": "UA001", "unit_type": "apartment", "owner_name": "Alice Smith",
                "current_levy": 5380, "fair_levy": 5703, "proposed_levy": 5703, "change": 323, "sei": 0.06,
            },
            {
                "unit_number": "TH087", "unit_type": "townhouse", "owner_name": "Bob Jones",
                "current_levy": 3759, "fair_levy": 2427, "proposed_levy": 2427, "change": -1332, "sei": 0.35,
            },
        ],
        "impact_by_group": [
            {"group": "Apartment", "current_total": 376521, "benefit_total": 399120, "change_pct": 6.0,
             "lot_count": 70},
            {"group": "Townhouse", "current_total": 63853, "benefit_total": 41255, "change_pct": -35.4,
             "lot_count": 17},
        ],
        "simulation": {
            "special_levy_probability": 8.5,
            "p50": 420000,
            "p75": 530000,
            "p90": 350000,
            "p95": 280000,
        },
    }


def _make_minimal_result() -> dict:
    """Minimal result with no optional sections."""
    return {
        "building_id": "13195",
        "lei_score": 85,
        "sei_scheme": 0.0,
        "total_budget": 0,
        "lbfi": {"current_score": 85, "benefit_score": 100, "fairness_gain": 15, "D": 0.07},
        "subsidy_map": {},
        "facility_breakdown": [],
        "unit_impact": [],
        "impact_by_group": [],
    }


# ── PDF Tests ─────────────────────────────────────────────────────────────────

class TestGenerateAgmReportPdf:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_result(self):
        """Returns None when there is no fairness result in DB."""
        from services.levy_fairness_pdf_service import generate_agm_report_pdf
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=None)
            result = await generate_agm_report_pdf("13195")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_bytes_with_full_result(self):
        """Returns a non-empty bytes object (valid PDF) for a full result."""
        from services.levy_fairness_pdf_service import generate_agm_report_pdf
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=_make_full_result())
            result = await generate_agm_report_pdf("13195")
        assert isinstance(result, bytes)
        assert len(result) > 1000  # should be a real PDF, not trivially small
        # PDF files start with %PDF
        assert result[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_returns_bytes_with_minimal_result(self):
        """Handles minimal result without optional sections gracefully."""
        from services.levy_fairness_pdf_service import generate_agm_report_pdf
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=_make_minimal_result())
            result = await generate_agm_report_pdf("13195")
        assert isinstance(result, bytes)
        assert result[:4] == b"%PDF"

    @pytest.mark.asyncio
    async def test_pdf_larger_than_before(self):
        """Enhanced PDF should be substantially larger (more content) than old 3-section version."""
        from services.levy_fairness_pdf_service import generate_agm_report_pdf
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=_make_full_result())
            result = await generate_agm_report_pdf("13195")
        # Enhanced PDF with 8 sections should be > 5KB
        assert len(result) > 5000

    @pytest.mark.asyncio
    async def test_pdf_has_multiple_pages(self):
        """Enhanced PDF should have multiple pages (8 sections)."""
        from services.levy_fairness_pdf_service import generate_agm_report_pdf
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=_make_full_result())
            result = await generate_agm_report_pdf("13195")
        # Count /Page entries in the PDF (each page is a /Type /Page object)
        page_count = result.count(b"/Type /Page")
        assert page_count >= 3  # should have at least 3 pages worth of content

    @pytest.mark.asyncio
    async def test_pdf_with_50_plus_units(self):
        """PDF truncates to 50 units in the per-lot table without error."""
        from services.levy_fairness_pdf_service import generate_agm_report_pdf
        result_doc = _make_full_result()
        # Add 80 units
        result_doc["unit_impact"] = [
            {"unit_number": f"UA{i:03d}", "unit_type": "apartment", "owner_name": f"Owner {i}",
             "current_levy": 5000 + i * 10, "fair_levy": 5100 + i * 10, "proposed_levy": 5100 + i * 10,
             "change": 100, "sei": 0.02}
            for i in range(80)
        ]
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=result_doc)
            result = await generate_agm_report_pdf("13195")
        assert isinstance(result, bytes)
        assert result[:4] == b"%PDF"


# ── PPTX Tests ────────────────────────────────────────────────────────────────

class TestGenerateAgmPresentation:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_result(self):
        """Returns None when there is no fairness result in DB."""
        from services.levy_fairness_pdf_service import generate_agm_presentation
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=None)
            result = await generate_agm_presentation("13195")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_bytes_when_pptx_available(self):
        """Returns non-empty bytes when python-pptx is available."""
        from services.levy_fairness_pdf_service import generate_agm_presentation, PPTX_AVAILABLE
        if not PPTX_AVAILABLE:
            pytest.skip("python-pptx not installed")
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=_make_full_result())
            mock_db.settings.find_one = AsyncMock(return_value={
                "building_name": "East Gate Residences",
                "plan_number": "13195",
            })
            result = await generate_agm_presentation("13195")
        assert isinstance(result, bytes)
        assert len(result) > 1000
        # PPTX files are ZIP archives, start with PK magic bytes
        assert result[:2] == b"PK"

    @pytest.mark.asyncio
    async def test_returns_none_when_pptx_not_available(self):
        """Returns None when python-pptx is not installed."""
        with patch("services.levy_fairness_pdf_service.PPTX_AVAILABLE", False), \
                patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=_make_full_result())
            from services.levy_fairness_pdf_service import generate_agm_presentation
            result = await generate_agm_presentation("13195")
        assert result is None

    @pytest.mark.asyncio
    async def test_pptx_with_minimal_result(self):
        """Handles minimal result (no groups, no facilities) without error."""
        from services.levy_fairness_pdf_service import generate_agm_presentation, PPTX_AVAILABLE
        if not PPTX_AVAILABLE:
            pytest.skip("python-pptx not installed")
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=_make_minimal_result())
            mock_db.settings.find_one = AsyncMock(return_value={
                "building_name": "East Gate Residences",
                "plan_number": "13195",
            })
            result = await generate_agm_presentation("13195")
        assert isinstance(result, bytes)
        assert result[:2] == b"PK"

    @pytest.mark.asyncio
    async def test_pptx_larger_than_old_version(self):
        """Enhanced PPTX (8 slides) should be bigger than the old 3-slide version."""
        from services.levy_fairness_pdf_service import generate_agm_presentation, PPTX_AVAILABLE
        if not PPTX_AVAILABLE:
            pytest.skip("python-pptx not installed")
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=_make_full_result())
            mock_db.settings.find_one = AsyncMock(return_value={
                "building_name": "East Gate Residences",
                "plan_number": "13195",
            })
            result = await generate_agm_presentation("13195")
        # 8 slides should produce > 20KB
        assert len(result) > 20000


# ── CSV Tests ─────────────────────────────────────────────────────────────────

class TestGenerateImpactCsv:
    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_result(self):
        """Returns empty string when there is no fairness result in DB."""
        from services.levy_fairness_pdf_service import generate_impact_csv
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=None)
            result = await generate_impact_csv("13195")
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_unit_impact(self):
        """Returns empty string when result has no unit_impact key."""
        from services.levy_fairness_pdf_service import generate_impact_csv
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value={"building_id": "13195"})
            result = await generate_impact_csv("13195")
        assert result == ""

    @pytest.mark.asyncio
    async def test_returns_csv_with_header_and_rows(self):
        """Returns CSV string with header row and data rows."""
        from services.levy_fairness_pdf_service import generate_impact_csv
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=_make_full_result())
            result = await generate_impact_csv("13195")
        assert "unit_number" in result
        assert "current_levy" in result
        assert "UA001" in result
        assert "TH087" in result

    @pytest.mark.asyncio
    async def test_csv_row_count_matches_unit_impact(self):
        """CSV has one row per unit plus the header."""
        from services.levy_fairness_pdf_service import generate_impact_csv
        with patch("services.levy_fairness_pdf_service.db") as mock_db:
            mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=_make_full_result())
            result = await generate_impact_csv("13195")
        lines = [l for l in result.strip().split("\n") if l]
        assert len(lines) == 3  # 1 header + 2 data rows
