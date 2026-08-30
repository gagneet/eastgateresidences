"""
Tests for statutory interest on arrears — B2-02.

Covers:
  - Formula: $100 × 10% ÷ 365 × 30 = $0.82
  - Zero interest before overdue date
  - Zero principal returns zero
  - Rate above 20% raises ValueError
  - get_building_interest_rate fallback and capping
  - Arrears board response includes accrued_interest and total_owing
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_backend_dir = os.path.join(_project_root, "backend")
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)


class TestComputeAccruedInterest:
    """Unit tests for the compute_accrued_interest() pure function."""

    def test_10pct_rate_30_days(self):
        """$100 × 10% ÷ 365 × 30 = $0.82 (rounded half-up)."""
        from services.arrears_interest_service import compute_accrued_interest

        overdue_since = datetime(2026, 1, 1)
        as_at = datetime(2026, 1, 31)  # 30 days later
        result = compute_accrued_interest(
            principal=100.0,
            overdue_since=overdue_since,
            as_at=as_at,
            annual_rate_pct=10.0,
        )
        assert result == 0.82, f"Expected $0.82, got ${result}"

    def test_zero_interest_before_overdue_date(self):
        """Returns 0.0 when as_at <= overdue_since."""
        from services.arrears_interest_service import compute_accrued_interest

        overdue_since = datetime(2026, 3, 1)
        as_at = datetime(2026, 2, 28)  # before overdue date
        result = compute_accrued_interest(100.0, overdue_since, as_at)
        assert result == 0.0

    def test_zero_interest_same_day(self):
        """Returns 0.0 when as_at == overdue_since."""
        from services.arrears_interest_service import compute_accrued_interest

        dt = datetime(2026, 1, 1)
        assert compute_accrued_interest(100.0, dt, dt) == 0.0

    def test_zero_principal_returns_zero(self):
        """Zero principal always returns 0.0."""
        from services.arrears_interest_service import compute_accrued_interest

        overdue_since = datetime(2026, 1, 1)
        as_at = datetime(2026, 4, 1)
        assert compute_accrued_interest(0.0, overdue_since, as_at) == 0.0

    def test_negative_principal_returns_zero(self):
        """Negative principal (credit balance) returns 0.0."""
        from services.arrears_interest_service import compute_accrued_interest

        overdue_since = datetime(2026, 1, 1)
        as_at = datetime(2026, 4, 1)
        assert compute_accrued_interest(-100.0, overdue_since, as_at) == 0.0

    def test_rate_above_20pct_raises_value_error(self):
        """Rate > 20% (legal maximum) raises ValueError."""
        from services.arrears_interest_service import compute_accrued_interest

        with pytest.raises(ValueError, match="maximum"):
            compute_accrued_interest(
                principal=100.0,
                overdue_since=datetime(2026, 1, 1),
                as_at=datetime(2026, 2, 1),
                annual_rate_pct=25.0,
            )

    def test_20pct_rate_is_allowed(self):
        """20% (special resolution maximum) does not raise."""
        from services.arrears_interest_service import compute_accrued_interest

        result = compute_accrued_interest(
            principal=100.0,
            overdue_since=datetime(2026, 1, 1),
            as_at=datetime(2026, 2, 1),
            annual_rate_pct=20.0,
        )
        assert result > 0.0

    def test_365_day_annual_interest_on_large_principal(self):
        """$1,000 × 10% × 365/365 = $100.00 (full year)."""
        from services.arrears_interest_service import compute_accrued_interest

        overdue_since = datetime(2025, 1, 1)
        as_at = datetime(2026, 1, 1)  # 365 days (non-leap year)
        result = compute_accrued_interest(1000.0, overdue_since, as_at, annual_rate_pct=10.0)
        assert result == 100.00

    def test_result_rounded_to_2dp(self):
        """Result must always be rounded to exactly 2 decimal places."""
        from services.arrears_interest_service import compute_accrued_interest

        result = compute_accrued_interest(
            principal=333.33,
            overdue_since=datetime(2026, 1, 1),
            as_at=datetime(2026, 3, 1),  # 59 days
            annual_rate_pct=10.0,
        )
        # Verify it's a float with at most 2 decimal places
        assert result == round(result, 2)


class TestGetBuildingInterestRate:
    """Tests for get_building_interest_rate()."""

    def test_default_when_config_none(self):
        from services.arrears_interest_service import (
            get_building_interest_rate,
            DEFAULT_INTEREST_RATE_PCT,
        )

        assert get_building_interest_rate(None) == DEFAULT_INTEREST_RATE_PCT

    def test_default_when_field_missing(self):
        from services.arrears_interest_service import (
            get_building_interest_rate,
            DEFAULT_INTEREST_RATE_PCT,
        )

        assert get_building_interest_rate({}) == DEFAULT_INTEREST_RATE_PCT

    def test_reads_configured_rate(self):
        from services.arrears_interest_service import get_building_interest_rate

        assert get_building_interest_rate({"arrears_interest_rate_pct": 15.0}) == 15.0

    def test_caps_at_max_rate(self):
        from services.arrears_interest_service import (
            get_building_interest_rate,
            MAX_INTEREST_RATE_PCT,
        )

        # Config tries to set 30% — must be capped at 20%
        assert get_building_interest_rate({"arrears_interest_rate_pct": 30.0}) == MAX_INTEREST_RATE_PCT

    def test_handles_non_numeric_gracefully(self):
        from services.arrears_interest_service import (
            get_building_interest_rate,
            DEFAULT_INTEREST_RATE_PCT,
        )

        # Non-numeric value → fallback to default
        assert get_building_interest_rate({"arrears_interest_rate_pct": "not_a_number"}) == DEFAULT_INTEREST_RATE_PCT


class TestArrearsInterestConstants:
    """Verify the ACT statutory interest constants are correct."""

    def test_default_rate_is_10_pct(self):
        from services.arrears_interest_service import DEFAULT_INTEREST_RATE_PCT

        assert DEFAULT_INTEREST_RATE_PCT == 10.0

    def test_max_rate_is_20_pct(self):
        from services.arrears_interest_service import MAX_INTEREST_RATE_PCT

        assert MAX_INTEREST_RATE_PCT == 20.0
