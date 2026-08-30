import pytest

from routers.finance import _compute_period_due_dates


def test_compute_period_due_dates_default():
    """Verify default due date computation (last day of month)."""
    # 2026 Feb has 28 days
    dates = _compute_period_due_dates(
        levy_year=2026,
        levy_due_months=[2, 5, 8, 11],
        levy_due_day_type="last",
        levy_due_day=None,
        num_periods=4
    )
    assert dates == ["2026-02-28", "2026-05-31", "2026-08-31", "2026-11-30"]


def test_compute_period_due_dates_custom_mapping():
    """Verify custom due dates mapping (e.g. 31/03/2026, 01/06/2026)."""
    # Requirements: 31/03, 01/06, 01/09, 01/12 for 2026
    dates = _compute_period_due_dates(
        levy_year=2026,
        levy_due_months=[3, 6, 9, 12],
        levy_due_day_type="custom",
        levy_due_day=None,
        num_periods=4,
        levy_due_custom_dates={
            "3": 31,
            "6": 1,
            "9": 1,
            "12": 1
        }
    )
    assert dates == ["2026-03-31", "2026-06-01", "2026-09-01", "2026-12-01"]


def test_compute_period_due_dates_clamping():
    """Verify clamping to last day of month."""
    # Feb 31st should become Feb 28th
    dates = _compute_period_due_dates(
        levy_year=2026,
        levy_due_months=[2],
        levy_due_day_type="custom",
        levy_due_day=None,
        num_periods=1,
        levy_due_custom_dates={"2": 31}
    )
    assert dates == ["2026-02-28"]
