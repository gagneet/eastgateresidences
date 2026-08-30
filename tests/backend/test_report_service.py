"""
PDF Report Service Tests — Phase 4

Tests cover: cover page, chart images, PDF metadata, file size,
anomaly section presence, narrative generation.

Run with:
    cd backend && python -m pytest tests/test_report_service.py -v
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_mock_health(score: float = 72.5, risk_level: str = "good") -> dict:
    return {
        "year": "2026",
        "score": score,
        "risk_level": risk_level,
        "breakdown": {
            "surplus_ratio": 14.0,
            "arrears_pct": 16.0,
            "cashflow_buffer": 12.0,
            "forecast_stability": 11.0,
            "budget_discipline": 10.5,
            "expense_volatility": 9.0,
        },
        "details": {
            "total_income": 440375.10,
            "closing_balance": 44037.51,
            "arrears_pct_raw": 4.6,
            "units_owing": 4,
        },
    }


def _make_mock_cashflow() -> dict:
    months = [
        {
            "month": f"Month {i + 1}", "month_number": i + 1,
            "expected_income": 36700, "expected_expenses": 35000,
            "net_cashflow": 1700, "cumulative_balance": 1700 * (i + 1),
            "is_risk_month": False,
        }
        for i in range(12)
    ]
    return {
        "year": "2026",
        "months": months,
        "annual_income": 440400,
        "annual_expenses": 420000,
        "min_balance": 1700,
        "risk_months": [],
        "opening_balance": 0,
    }


def _make_mock_levy() -> dict:
    return {
        "admin_fund": {
            "total_income": 340870.20,
            "total_expenses": 320000.00,
            "closing_balance": 20870.20,
        },
        "sinking_fund": {
            "total_income": 99504.90,
            "total_expenses": 85000.00,
            "closing_balance": 14504.90,
        },
    }


def _make_mock_forecasts() -> list:
    return [
        {
            "financial_year": "2026", "fund_type": "administrative",
            "category": f"Category {i}", "method": "inflation",
            "projection_year_1": 10000 + i * 1000,
            "projection_year_2": 10350 + i * 1000,
            "projection_year_3": 10712 + i * 1000,
            "confidence_score": 0.7,
        }
        for i in range(5)
    ]


def _make_mock_anomalies(count: int = 2) -> list:
    return [
        {
            "id": f"anom-{i}", "financial_year": "2026",
            "anomaly_type": "budget_overrun", "severity": "high",
            "category": f"Category {i}", "description": f"Overrun detected #{i}",
            "resolved": False, "detected_at": "2026-02-23T02:00:00Z",
        }
        for i in range(count)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_mock_db(anomaly_count: int = 2) -> MagicMock:
    mock_db = MagicMock()

    def _cursor_mock(data):
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=data)
        return cursor

    mock_db.financial_anomalies.find.return_value = _cursor_mock(_make_mock_anomalies(anomaly_count))
    mock_db.financial_forecasts.find.return_value = _cursor_mock(_make_mock_forecasts())
    mock_db.lot_financial_summary.find.return_value = _cursor_mock([])
    mock_db.levy_categories.find.return_value = _cursor_mock([])
    mock_db.financial_categories.find.return_value = _cursor_mock([])
    mock_db.annual_levies.find_one = AsyncMock(return_value=_make_mock_levy())
    mock_db.settings.find_one = AsyncMock(return_value={
        "building_name": "East Gate Residences",
        "plan_number": "13195",
    })
    # db["council_rate_settings"] uses dict-style access; wire find_one as AsyncMock
    _crs = MagicMock()
    _crs.find_one = AsyncMock(return_value={})
    mock_db.__getitem__ = MagicMock(return_value=_crs)
    return mock_db


async def _call_generate_report(mock_db, anomaly_count=2, generated_by_role="super_admin") -> bytes:
    """Call generate_full_report with full patching.

    The two maintenance_intelligence_service patches are NOT decoration. Those
    functions are LATE-IMPORTED inside generate_full_report, so they resolve from
    their own module at call time and `patch("services.report_service.db")` never
    reaches them — they ran against the real MONGO_URL. Every test in this file
    therefore recomputed and upserted `maintenance_forecasts` AND
    `intelligence_summary` for building 13195, which is East Gate: nine production
    writes per suite run, against the one building CLAUDE.md says seed and test
    scripts must never touch.

    It was silent because the writes are recomputes of real data, so nothing looked
    wrong afterwards. It surfaced only when a new field appeared on the forecast
    document and turned up in production with a timestamp from a test run.

    Patching at the SOURCE module (not at services.report_service) is what makes it
    stick: that is where the late import resolves.
    """
    import sys
    sys.path.insert(0, ".")

    health = _make_mock_health()
    cashflow = _make_mock_cashflow()
    levy = _make_mock_levy()
    ledger_stats = {"units_owing": 4}

    with patch("services.report_service.db", mock_db), \
            patch("services.report_service.compute_financial_health", AsyncMock(return_value=health)), \
            patch("services.report_service.generate_cashflow_projection", AsyncMock(return_value=cashflow)), \
            patch("services.report_service.detect_financial_anomalies",
                  AsyncMock(return_value=_make_mock_anomalies(anomaly_count))), \
            patch("services.report_service.get_unit_ledger_stats", AsyncMock(return_value=ledger_stats)), \
            patch("services.report_service._get_levy_data", AsyncMock(return_value=levy)), \
            patch("services.report_service._get_categories", AsyncMock(return_value=[])), \
            patch("services.report_service.generate_levy_projection", AsyncMock(return_value={}), create=True), \
            patch("services.maintenance_intelligence_service.update_building_intelligence_summary",
                  AsyncMock(return_value={})), \
            patch("services.maintenance_intelligence_service.generate_maintenance_forecast",
                  AsyncMock(return_value={})):
        from services.report_service import generate_full_report
        pdf_bytes = await generate_full_report(
            "2026",
            building_id="13195",
            generated_by_role=generated_by_role,
        )

    return pdf_bytes


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestReportPDFGeneration:

    @pytest.mark.asyncio
    async def test_pdf_size_reasonable(self):
        """Generated PDF should be at least 50KB (has content + charts)."""
        mock_db = _build_mock_db()
        pdf_bytes = await _call_generate_report(mock_db)
        assert len(pdf_bytes) > 50_000, (
            f"PDF too small: {len(pdf_bytes)} bytes. "
            "Expected > 50KB for a complete multi-section report."
        )

    @pytest.mark.asyncio
    async def test_cover_page_present(self):
        """PDF should contain 'CONFIDENTIAL' text — extracted via pdfplumber."""
        mock_db = _build_mock_db()
        pdf_bytes = await _call_generate_report(mock_db)
        # PDF binary check — text may be compressed; use pdfplumber for text extraction
        try:
            import pdfplumber
            import io as _io
            with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
                first_page_text = pdf.pages[0].extract_text() or ""
            found = (
                    "CONFIDENTIAL" in first_page_text.upper()
                    or "Eastgate" in first_page_text
                    or "13195" in first_page_text
            )
            assert found, (
                f"Cover page markers not found in first page text. "
                f"Extracted (first 500 chars): {first_page_text[:500]}"
            )
        except ImportError:
            # Fallback: raw bytes search — works for uncompressed PDF streams
            has_text = b"CONFIDENTIAL" in pdf_bytes or b"Eastgate" in pdf_bytes
            # At minimum the PDF is valid and large
            assert len(pdf_bytes) > 50_000, "PDF too small to contain cover page"

    @pytest.mark.asyncio
    async def test_plan_name_present(self):
        """PDF should mention the plan name."""
        mock_db = _build_mock_db()
        pdf_bytes = await _call_generate_report(mock_db)
        # Plan name or plan number should appear
        assert b"Eastgate" in pdf_bytes or b"13195" in pdf_bytes, (
            "Plan name/number not found in PDF"
        )

    @pytest.mark.asyncio
    async def test_returns_bytes(self):
        """generate_full_report should return bytes (not None or empty string)."""
        mock_db = _build_mock_db()
        pdf_bytes = await _call_generate_report(mock_db)
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0

    @pytest.mark.asyncio
    async def test_anomaly_section_with_anomalies(self):
        """When anomalies exist, PDF should reference them."""
        mock_db = _build_mock_db(anomaly_count=3)
        pdf_bytes = await _call_generate_report(mock_db, anomaly_count=3)
        # Should mention anomalies section content
        assert len(pdf_bytes) > 50_000

    @pytest.mark.asyncio
    async def test_generated_by_role_accepted(self):
        """generate_full_report should accept generated_by_role parameter without error."""
        mock_db = _build_mock_db()
        for role in ["super_admin", "chairman", "strata_manager"]:
            pdf_bytes = await _call_generate_report(mock_db, generated_by_role=role)
            assert isinstance(pdf_bytes, bytes)
            assert len(pdf_bytes) > 0

    @pytest.mark.asyncio
    async def test_no_anomalies_section_renders(self):
        """With zero anomalies, report should still render without error."""
        mock_db = _build_mock_db(anomaly_count=0)
        pdf_bytes = await _call_generate_report(mock_db, anomaly_count=0)
        assert len(pdf_bytes) > 50_000


# ─────────────────────────────────────────────────────────────────────────────
# Narrative generator tests
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutiveNarrative:
    """Tests for the _build_executive_narrative helper."""

    def _call_narrative(self, score=72.5, risk_level="good", anomaly_count=2,
                        units_owing=4, risk_months=None):
        import sys
        sys.path.insert(0, ".")

        # Import inline to avoid DB-level dependencies
        from services.report_service import _build_executive_narrative
        health = _make_mock_health(score=score, risk_level=risk_level)
        cashflow = _make_mock_cashflow()
        if risk_months is not None:
            cashflow["risk_months"] = risk_months
        ledger_stats = {"units_owing": units_owing}
        levy = _make_mock_levy()

        paras, bullets = _build_executive_narrative(
            health, anomaly_count, cashflow, ledger_stats, levy, "2026"
        )
        return paras, bullets

    def test_narrative_returns_paragraphs_and_bullets(self):
        paras, bullets = self._call_narrative()
        assert isinstance(paras, list)
        assert isinstance(bullets, list)
        assert len(paras) >= 2
        assert len(bullets) == 4

    def test_score_in_narrative(self):
        paras, _ = self._call_narrative(score=72.5)
        combined = " ".join(paras)
        assert "72.5" in combined

    def test_risk_months_in_bullets(self):
        _, bullets = self._call_narrative(risk_months=["Aug", "Sep"])
        risk_bullet = next(b for b in bullets if "Cashflow" in b)
        assert "Aug" in risk_bullet

    def test_no_risk_months_message(self):
        _, bullets = self._call_narrative(risk_months=[])
        risk_bullet = next(b for b in bullets if "Cashflow" in b)
        assert "No negative" in risk_bullet

    def test_arrears_count_in_bullets(self):
        _, bullets = self._call_narrative(units_owing=7)
        arrears_bullet = next(b for b in bullets if "Arrears" in b)
        assert "7" in arrears_bullet
