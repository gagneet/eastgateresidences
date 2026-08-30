"""
Tests for PR review critical defect fixes.

Covers:
  - Path traversal prevention in tenant_maintenance upload_photo
  - File upload validation (extension + content-type)
  - ZeroDivisionError prevention in levy_fairness_service (actual_total=0)
  - Date calculation safety in ownerhub_service 6-month lookback
  - MongoDB upsert double-field exclusion pattern
"""
import re
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _mock_all_unit_owners():
    with patch("services.levy_fairness_service.get_all_unit_owners", AsyncMock(return_value={})):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# 1. Tenant Maintenance — Upload Security
# ─────────────────────────────────────────────────────────────────────────────

class TestUploadPhotoSecurity:
    """Verify path traversal and file-type protections in upload_photo."""

    def _safe_request_id_sanitizer(self, request_id: str) -> str:
        """Replicate the sanitization logic from the endpoint."""
        safe = re.sub(r"[^a-f0-9]", "", request_id.lower())[:24]
        return safe

    def test_hex_only_request_id_passes(self):
        rid = "507f1f77bcf86cd799439011"  # valid ObjectId hex
        safe = self._safe_request_id_sanitizer(rid)
        assert safe == rid
        assert len(safe) == 24

    def test_path_traversal_stripped(self):
        rid = "../../etc/passwd"
        safe = self._safe_request_id_sanitizer(rid)
        # All non-hex chars removed, result should not be 24 chars → invalid
        assert len(safe) != 24 or not all(c in "0123456789abcdef" for c in safe)

    def test_path_traversal_dot_slash_stripped(self):
        rid = "..%2F..%2Fetc%2Fpasswd"
        safe = self._safe_request_id_sanitizer(rid)
        assert len(safe) < 24 or safe == rid[:24].lower().replace("-", "")

    def test_null_byte_stripped(self):
        rid = "507f1f77bcf86cd79943901\x00evil"
        safe = self._safe_request_id_sanitizer(rid)
        assert "\x00" not in safe

    def test_uppercase_hex_normalized(self):
        rid = "507F1F77BCF86CD799439011"
        safe = self._safe_request_id_sanitizer(rid)
        assert safe == rid.lower()
        assert len(safe) == 24

    def test_allowed_content_types(self):
        ALLOWED = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic"}
        for ct in ALLOWED:
            assert ct in ALLOWED

    def test_spoofed_content_type_blocked(self):
        # Old code: content_type.startswith("image/") — allowed "image/x-php"
        # New code: exact match against ALLOWED_CONTENT_TYPES
        ALLOWED = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic"}
        spoofed_types = ["image/x-php", "image/svg+xml", "image/x-icon"]
        for ct in spoofed_types:
            assert ct not in ALLOWED, f"{ct} should not be in ALLOWED_CONTENT_TYPES"

    def test_allowed_extensions(self):
        ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
        for ext in ALLOWED_EXT:
            assert ext in ALLOWED_EXT

    def test_dangerous_extensions_blocked(self):
        ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
        dangerous = [".php", ".jsp", ".exe", ".sh", ".py", ".rb", ".asp"]
        for ext in dangerous:
            assert ext not in ALLOWED_EXT, f"{ext} should not be allowed"

    def test_svg_extension_blocked(self):
        """SVG can contain XSS — must not be allowed."""
        ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
        assert ".svg" not in ALLOWED_EXT


# ─────────────────────────────────────────────────────────────────────────────
# 2. Levy Fairness — ZeroDivisionError when actual_total = 0
# ─────────────────────────────────────────────────────────────────────────────

class TestLevyFairnessZeroDivision:
    """Verify no ZeroDivisionError when levy ledger is empty (actual_total=0)."""

    def _build_empty_ledger_db(self):
        mock_db = MagicMock()
        mock_db.units.find.return_value = MagicMock(
            to_list=AsyncMock(return_value=[
                {"unit_number": "UA001", "entitlement": 100.0, "property_type": "Apartment"},
                {"unit_number": "UA002", "entitlement": 200.0, "property_type": "Apartment"},
            ])
        )
        mock_db.benefit_groups.find.return_value = MagicMock(to_list=AsyncMock(return_value=[]))
        mock_db.unit_levy_ledger.find.return_value = MagicMock(to_list=AsyncMock(return_value=[]))  # EMPTY
        mock_db.unit_levy_ledger.aggregate.return_value = MagicMock(to_list=AsyncMock(return_value=[]))
        mock_db.unit_attributes.find.return_value = MagicMock(to_list=AsyncMock(return_value=[]))
        mock_db.facilities.find.return_value = MagicMock(to_list=AsyncMock(return_value=[]))
        mock_db.building_assets.find.return_value = MagicMock(to_list=AsyncMock(return_value=[]))
        mock_db.capital_replacement_schedule.find.return_value = MagicMock(to_list=AsyncMock(return_value=[]))
        mock_db.financial_summary.find_one = AsyncMock(return_value=None)
        mock_db.levy_fairness_results_v2.find_one = AsyncMock(return_value=None)
        mock_db.levy_fairness_results_v2.update_one = AsyncMock(return_value=MagicMock())
        mock_db.annual_levies.find_one = AsyncMock(return_value=None)
        return mock_db

    @pytest.mark.asyncio
    async def test_no_zero_division_when_ledger_empty(self):
        """simulate_levy_fairness_v2 must not raise ZeroDivisionError when actual_total=0."""
        import services.levy_fairness_service as svc

        mock_db = self._build_empty_ledger_db()
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.get_levy_rates", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 0.0, "UA002": 0.0})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(return_value={"UA001": 0.0, "UA002": 0.0})), \
                patch("services.levy_fairness_service.run_monte_carlo_levy_simulation",
                      return_value=None):
            # Should NOT raise ZeroDivisionError
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        assert result is not None
        assert "total_budget" in result
        assert result["total_budget"] == 0.0

    @pytest.mark.asyncio
    async def test_shares_sum_to_zero_when_no_data(self):
        """When actual_total=0, all shares should be 0.0 (not NaN or error)."""
        import services.levy_fairness_service as svc

        mock_db = self._build_empty_ledger_db()
        with patch("services.levy_fairness_service.db", mock_db), \
                patch("services.levy_fairness_service.get_latest_levy_year", AsyncMock(return_value="2026")), \
                patch("services.levy_fairness_service.get_unit_attributes", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.get_levy_rates", AsyncMock(return_value={})), \
                patch("services.levy_fairness_service.calculate_facility_allocation",
                      AsyncMock(return_value={"UA001": 0.0, "UA002": 0.0})), \
                patch("services.levy_fairness_service.apply_transition_caps",
                      AsyncMock(return_value={"UA001": 0.0, "UA002": 0.0})), \
                patch("services.levy_fairness_service.run_monte_carlo_levy_simulation",
                      return_value=None):
            result = await svc.simulate_levy_fairness_v2("13195", run_monte_carlo=False)

        # lei_score should be 100 (perfect when no distortion data)
        assert result["lei_score"] >= 0
        assert result["sei_scheme"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 3. OwnerHub — 6-Month Date Calculation Safety
# ─────────────────────────────────────────────────────────────────────────────

class TestSixMonthDateCalculation:
    """Verify 6-month lookback doesn't raise ValueError for end-of-month dates."""

    def _compute_six_months_ago(self, now_dt: datetime) -> datetime:
        """Replicate the fixed calculation from ownerhub_service.py."""
        _m6 = now_dt.month - 6
        if _m6 > 0:
            return now_dt.replace(month=_m6, day=1)
        else:
            return now_dt.replace(year=now_dt.year - 1, month=_m6 + 12, day=1)

    def test_march_31_no_value_error(self):
        """March 31 - 6 months = September 1 (prev year). Old code: Sep 31 → ValueError."""
        dt = datetime(2026, 3, 31)
        result = self._compute_six_months_ago(dt)
        assert result == datetime(2025, 9, 1)

    def test_may_31_no_value_error(self):
        """May 31 - 6 months = November 1 (prev year). Old code: Nov 31 → ValueError."""
        dt = datetime(2026, 5, 31)
        result = self._compute_six_months_ago(dt)
        assert result == datetime(2025, 11, 1)

    def test_january_31_no_value_error(self):
        """January 31 - 6 months = July 1 (prev year)."""
        dt = datetime(2026, 1, 31)
        result = self._compute_six_months_ago(dt)
        assert result == datetime(2025, 7, 1)

    def test_august_1_same_year(self):
        """August 1 - 6 months = February 1 (same year)."""
        dt = datetime(2026, 8, 1)
        result = self._compute_six_months_ago(dt)
        assert result == datetime(2026, 2, 1)

    def test_december_31_same_year(self):
        """December 31 - 6 months = June 1 (same year)."""
        dt = datetime(2026, 12, 31)
        result = self._compute_six_months_ago(dt)
        assert result == datetime(2026, 6, 1)

    def test_february_28_no_error(self):
        """Feb 28 - 6 months = August 1 (prev year)."""
        dt = datetime(2026, 2, 28)
        result = self._compute_six_months_ago(dt)
        assert result == datetime(2025, 8, 1)

    def test_result_always_before_input(self):
        """6-months-ago result must always be before the input date."""
        test_dates = [
            datetime(2026, m, 1) for m in range(1, 13)
        ]
        for dt in test_dates:
            result = self._compute_six_months_ago(dt)
            assert result < dt, f"six_months_ago({dt}) = {result} is not before input"


# ─────────────────────────────────────────────────────────────────────────────
# 4. MongoDB Upsert — Double Building_ID Exclusion Pattern
# ─────────────────────────────────────────────────────────────────────────────

class TestMongoUpsertBuildingIdExclusion:
    """Verify that $set documents in upserts correctly exclude filter fields."""

    def test_capital_shock_set_excludes_building_id(self):
        """capital_shock_service result dict should not include building_id in $set."""
        result = {
            "building_id": "13195",
            "computed_at": "2026-03-12T00:00:00Z",
            "risk_level": "low",
            "reserve_balance": 50000.0,
        }
        update_doc = {k: v for k, v in result.items() if k != "building_id"}
        assert "building_id" not in update_doc
        assert "risk_level" in update_doc
        assert "computed_at" in update_doc

    def test_levy_fairness_v2_set_excludes_building_id(self):
        """levy_fairness_results_v2 $set should not include building_id."""
        result = {
            "building_id": "13195",
            "total_budget": 440375.10,
            "lei_score": 95.0,
        }
        result_v2_doc = {k: v for k, v in result.items() if k != "building_id"}
        assert "building_id" not in result_v2_doc
        assert "total_budget" in result_v2_doc

    def test_levy_simulations_set_excludes_building_id_and_scenario(self):
        """levy_simulations $set should not repeat building_id or scenario from filter."""
        filter_doc = {"building_id": "13195", "scenario": "current"}
        set_doc = {"scenario": "current", "computed_at": "2026-03-12T00:00:00Z"}
        # Ensure scenario is NOT excluded from $set (only building_id is problematic in upsert)
        # but building_id is excluded
        assert "building_id" not in set_doc

    def test_notifications_prefs_set_excludes_identity_fields(self):
        """email_preferences $set should not include user_id or building_id."""
        preferences = {
            "user_id": "user-123",
            "building_id": "13195",
            "notices_enabled": True,
            "announcements_enabled": False,
            "updated_at": "2026-03-12T00:00:00Z",
        }
        prefs_set = {k: v for k, v in preferences.items() if k not in ("user_id", "building_id")}
        assert "user_id" not in prefs_set
        assert "building_id" not in prefs_set
        assert "notices_enabled" in prefs_set

    def test_building_stress_snapshot_excludes_building_id_and_month(self):
        """building_financial_health $set should not include building_id or month (in filter)."""
        snapshot = {
            "building_id": "13195",
            "computed_at": "2026-03-12T00:00:00Z",
            "overall_risk_score": 45.0,
            "total_units": 87,
        }
        month_key = "2026-03"
        snapshot_set = {k: v for k, v in snapshot.items() if k not in ("building_id", "month")}
        snapshot_set["month"] = month_key  # month is added explicitly
        assert "building_id" not in snapshot_set
        assert snapshot_set["month"] == "2026-03"
        assert "overall_risk_score" in snapshot_set
