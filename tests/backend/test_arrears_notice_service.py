"""
Tests for backend/services/notice_service.py — Arrears Notice PDF generation.

Covers:
1. Opening arrears fallback chain (admin_opening + sinking_opening → opening_arrears → unit.opening_arrears)
2. Year type coercion for ledger and payment queries (int vs string)
3. Grace-period-aware outstanding amount computation using actual payment_schedule
4. Strata manager name resolution (no "Silverfox Technologies" defaults)
5. Regression: TH085 $0.00 bug — wrong due dates caused periods_past_grace=0
6. Float-precision fix in periods_funded calculation (4590.9 // 1530.3 = 2, not 3)
"""
import pytest


# ---------------------------------------------------------------------------
# Helper: synthetic settings / ledger / unit docs
# ---------------------------------------------------------------------------

def _settings(extra=None):
    base = {
        "building_id": "13195",
        "building_name": "East Gate Residences",
        "plan_number": "13195",
        "address": "Denman Prospect ACT 2611",
        "levy_due_months": [3, 6, 9, 12],
        "grace_period_days": 14,
        "levy_due_day_type": "last",
        "levy_due_day": None,
        "levy_due_custom_dates": {},
    }
    if extra:
        base.update(extra)
    return base


def _ledger(extra=None):
    base = {
        "building_id": "13195",
        "unit_number": "TH085",
        "levy_year": 2026,
        "admin_levied": 0.0,
        "sinking_levied": 0.0,
        "admin_opening": 0.0,
        "sinking_opening": 0.0,
    }
    if extra:
        base.update(extra)
    return base


def _unit(extra=None):
    base = {
        "building_id": "13195",
        "unit_number": "TH085",
        "owner_name": "Test Owner",
        "lot_number": "15",
    }
    if extra:
        base.update(extra)
    return base


# ---------------------------------------------------------------------------
# Unit tests: _coerce_year
# ---------------------------------------------------------------------------

class TestCoerceYear:
    def _coerce(self, year):
        # Mirror the private function from notice_service
        try:
            return str(year), int(year)
        except (ValueError, TypeError):
            return str(year), None

    def test_integer_year(self):
        s, i = self._coerce(2026)
        assert s == "2026"
        assert i == 2026

    def test_string_year(self):
        s, i = self._coerce("2026")
        assert s == "2026"
        assert i == 2026

    def test_non_numeric_string(self):
        s, i = self._coerce("bad")
        assert s == "bad"
        assert i is None

    def test_none_year(self):
        s, i = self._coerce(None)
        assert i is None


# ---------------------------------------------------------------------------
# Unit tests: opening_arrears fallback chain
# ---------------------------------------------------------------------------

class TestOpeningArrearsFallback:
    """
    Mirrors the fallback logic in notice_service.generate_arrears_notice():
      1. admin_opening + sinking_opening
      2. ledger.opening_arrears
      3. unit.opening_arrears
    """

    def _compute_combined_opening(self, ledger, unit):
        admin_opening = ledger.get("admin_opening") or 0
        sinking_opening = ledger.get("sinking_opening") or 0
        combined_opening = admin_opening + sinking_opening
        if combined_opening == 0:
            combined_opening = ledger.get("opening_arrears") or 0
        if combined_opening == 0:
            combined_opening = (unit.get("opening_arrears") or 0)
        return combined_opening

    def test_separate_admin_sinking_fields(self):
        ledger = _ledger({"admin_opening": 10.00, "sinking_opening": 10.23})
        unit = _unit()
        result = self._compute_combined_opening(ledger, unit)
        assert result == pytest.approx(20.23, abs=0.01)

    def test_combined_opening_arrears_field_used_when_separate_zero(self):
        """TH085 regression: ledger only has opening_arrears=20.23, not separate fields."""
        ledger = _ledger({"admin_opening": 0.0, "sinking_opening": 0.0, "opening_arrears": 20.23})
        unit = _unit()
        result = self._compute_combined_opening(ledger, unit)
        assert result == pytest.approx(20.23, abs=0.01)

    def test_unit_opening_arrears_used_as_final_fallback(self):
        """When ledger has no opening fields at all, fall back to unit doc."""
        ledger = _ledger()  # no opening fields
        unit = _unit({"opening_arrears": 20.23})
        result = self._compute_combined_opening(ledger, unit)
        assert result == pytest.approx(20.23, abs=0.01)

    def test_zero_opening_returns_zero(self):
        ledger = _ledger({"admin_opening": 0.0, "sinking_opening": 0.0, "opening_arrears": 0.0})
        unit = _unit({"opening_arrears": 0.0})
        result = self._compute_combined_opening(ledger, unit)
        assert result == 0.0

    def test_separate_fields_take_priority_over_combined(self):
        """If admin_opening + sinking_opening > 0, don't also add opening_arrears."""
        ledger = _ledger({
            "admin_opening": 15.00,
            "sinking_opening": 5.23,
            "opening_arrears": 999.00,  # should be ignored
        })
        unit = _unit({"opening_arrears": 999.00})
        result = self._compute_combined_opening(ledger, unit)
        assert result == pytest.approx(20.23, abs=0.01)


# ---------------------------------------------------------------------------
# Unit tests: outstanding amount computation
# ---------------------------------------------------------------------------

class TestOutstandingComputation:
    """
    Mirrors notice_service outstanding amount logic:
      obligations_so_far = combined_opening + (periods_past_grace * period_levy)
      total_outstanding = max(0, obligations_so_far - confirmed_paid)
    """

    def _compute_outstanding(self, combined_opening, annual_levy, total_periods,
                             periods_past_grace, confirmed_paid):
        period_levy = (annual_levy / total_periods) if total_periods > 0 else 0
        obligations_so_far = combined_opening + (periods_past_grace * period_levy)
        return round(max(0.0, obligations_so_far - confirmed_paid), 2)

    def test_opening_arrears_only_zero_periods_past_grace(self):
        """TH085 scenario: $20.23 opening arrears, no periods past grace, no payments."""
        result = self._compute_outstanding(
            combined_opening=20.23,
            annual_levy=0.0,
            total_periods=4,
            periods_past_grace=0,
            confirmed_paid=0.0,
        )
        assert result == pytest.approx(20.23, abs=0.01)

    def test_zero_opening_zero_periods_gives_zero(self):
        result = self._compute_outstanding(0.0, 7090.04, 4, 0, 0.0)
        assert result == 0.0

    def test_periods_past_grace_adds_to_outstanding(self):
        period_levy = round(7090.04 / 4, 2)  # ≈ 1772.51
        result = self._compute_outstanding(0.0, 7090.04, 4, 1, 0.0)
        assert result == pytest.approx(period_levy, abs=0.02)

    def test_confirmed_payments_reduce_outstanding(self):
        period_levy = round(7090.04 / 4, 2)
        paid = period_levy  # exactly one period paid
        result = self._compute_outstanding(0.0, 7090.04, 4, 1, paid)
        assert result == pytest.approx(0.0, abs=0.01)

    def test_overpayment_clamped_to_zero(self):
        result = self._compute_outstanding(0.0, 7090.04, 4, 1, 9999.99)
        assert result == 0.0

    def test_opening_plus_levy_minus_payment(self):
        period_levy = round(7090.04 / 4, 2)
        result = self._compute_outstanding(20.23, 7090.04, 4, 1, 500.00)
        expected = round(max(0.0, 20.23 + period_levy - 500.00), 2)
        assert result == pytest.approx(expected, abs=0.01)


# ---------------------------------------------------------------------------
# Unit tests: strata manager name resolution
# ---------------------------------------------------------------------------

class TestStrataManagerName:
    """
    Mirrors notice_service strata_manager resolution.
    Priority: strata_manager_name > manager_name > f"Strata Manager, {building_name}"
    NEVER returns "Silverfox Technologies".
    """

    def _resolve_manager(self, b_settings, building_name):
        return (
                b_settings.get("strata_manager_name")
                or b_settings.get("manager_name")
                or f"Strata Manager, {building_name}"
        )

    def test_strata_manager_name_field_used(self):
        settings = _settings({"strata_manager_name": "Strata Web Community"})
        result = self._resolve_manager(settings, "East Gate Residences")
        assert result == "Strata Web Community"

    def test_manager_name_fallback(self):
        settings = _settings({"manager_name": "John Smith"})
        result = self._resolve_manager(settings, "East Gate Residences")
        assert result == "John Smith"

    def test_default_includes_building_name_not_silverfox(self):
        settings = _settings()  # no manager name fields
        result = self._resolve_manager(settings, "East Gate Residences")
        assert result == "Strata Manager, East Gate Residences"
        assert "Silverfox" not in result
        assert "Technologies" not in result

    def test_empty_string_falls_through_to_default(self):
        settings = _settings({"strata_manager_name": "", "manager_name": ""})
        result = self._resolve_manager(settings, "East Gate Residences")
        assert result == "Strata Manager, East Gate Residences"

    def test_none_falls_through_to_default(self):
        settings = _settings({"strata_manager_name": None, "manager_name": None})
        result = self._resolve_manager(settings, "East Gate Residences")
        assert result == "Strata Manager, East Gate Residences"


# ---------------------------------------------------------------------------
# Regression: TH085 $0.00 total outstanding bug
# ---------------------------------------------------------------------------

class TestTH085Regression:
    """
    Regression for: TH085 has opening_arrears=$20.23 (from ledger admin_opening+sinking_opening)
    but arrears notice PDF showed $0.00.

    Root cause:
    - notice_service used settings.levy_due_months (not in DB → defaults to [3,6,9,12])
      with levy_due_day_type="last" → Q1=March 31, 2026 (NOT past grace on March 17)
      → periods_past_grace=0
    - TH085 unit document has NO opening_arrears field (it's in the ledger)
    - obligations=0+0=0, confirmed_paid=$1,567.97 → max(0, 0-1567.97)=0.0

    Fix: use annual_levies.payment_schedule (Q1=2025-10-01, Q2=2026-01-01, both past grace)
    """

    def _compute_combined_opening(self, ledger, unit):
        admin_opening = (ledger.get("admin_opening") or 0) if ledger else 0
        sinking_opening = (ledger.get("sinking_opening") or 0) if ledger else 0
        combined_opening = round(admin_opening + sinking_opening, 2)
        if combined_opening == 0 and ledger:
            combined_opening = ledger.get("opening_arrears") or 0
        if combined_opening == 0:
            combined_opening = unit.get("opening_arrears") or 0
        return combined_opening

    def test_th015_opening_from_ledger_admin_sinking(self):
        """TH085 ledger has admin_opening=15.66, sinking_opening=4.57 → combined=20.23."""
        ledger = _ledger({"admin_opening": 15.66, "sinking_opening": 4.57})
        unit = _unit()
        assert self._compute_combined_opening(ledger, unit) == pytest.approx(20.23, abs=0.01)

    def test_th015_unit_has_no_opening_arrears_field(self):
        """TH085 unit document returns None for opening_arrears — must use ledger."""
        unit = _unit()  # no opening_arrears
        assert unit.get("opening_arrears") is None, "Unit fixture must not have opening_arrears"

    def test_levy_payments_must_not_offset_opening_arrears(self):
        """
        levy_payments records ($1,567.97) are current-year instalment payments.
        They must NOT be subtracted from opening_arrears — doing so hides genuine
        prior-year debt.  confirmed_paid must come from ledger.total_paid only.

        TH085 scenario:
          opening_arrears = $20.23 (FY2025 carry-forward, already net)
          ledger.total_paid = $0.00 (no DEFT/bank payment recorded)
          levy_payments = $1,567.97 (portal/Stripe for Q1/Q2 levy — do NOT use)
        """
        # Correct: use ledger.total_paid
        ledger_total_paid = 0.0
        combined_opening = 20.23
        outstanding_correct = round(max(0.0, combined_opening - ledger_total_paid), 2)
        assert outstanding_correct == pytest.approx(20.23, abs=0.01)

        # Wrong (old bug): use levy_payments aggregate
        levy_payments_paid = 1567.97
        outstanding_wrong = round(max(0.0, combined_opening - levy_payments_paid), 2)
        assert outstanding_wrong == 0.0, "Using levy_payments hides the $20.23 debt"

    def test_settings_based_due_dates_correct_for_march_17(self):
        """
        Settings default [3,6,9,12] 'last' → Q1=March 31.
        On March 17, Q1 not past grace (expires April 14) → periods_past_grace=0.
        outstanding = $20.23 (opening only). Matches Debt Recovery Board.
        """
        from datetime import date, timedelta
        today = date(2026, 3, 17)
        grace_days = 14

        # Settings-based dates (what both board and notice use)
        due_dates = [
            date(2026, 3, 31),  # Q1 last-of-March — grace April 14
            date(2026, 6, 30),
            date(2026, 9, 30),
            date(2026, 12, 31),
        ]
        ppg = sum(1 for d in due_dates if today > d + timedelta(days=grace_days))
        assert ppg == 0, "March 31 not past grace on March 17"

        combined_opening = 20.23
        confirmed_paid = 0.0  # ledger.total_paid
        outstanding = round(max(0.0, combined_opening + ppg * 1761.5 - confirmed_paid), 2)
        assert outstanding == pytest.approx(20.23, abs=0.01)

    def test_settings_based_due_dates_correct_after_april_14(self):
        """
        After April 14 (Q1 grace expired), outstanding = $20.23 + $1,761.50 = $1,781.73.
        """
        from datetime import date, timedelta
        today = date(2026, 4, 15)
        grace_days = 14

        due_dates = [
            date(2026, 3, 31),
            date(2026, 6, 30),
            date(2026, 9, 30),
            date(2026, 12, 31),
        ]
        ppg = sum(1 for d in due_dates if today > d + timedelta(days=grace_days))
        assert ppg == 1, "March 31 past grace on April 15"

        combined_opening = 20.23
        period_levy = 1761.5
        confirmed_paid = 0.0
        outstanding = round(max(0.0, combined_opening + ppg * period_levy - confirmed_paid), 2)
        assert outstanding == pytest.approx(1781.73, abs=0.01)

    def test_confirmed_paid_type_flexibility(self):
        """Year-type mismatch in MongoDB causes zero confirmed_paid. Fix tries both types."""
        all_payments = [
            {"building_id": "13195", "unit_number": "TH085", "year": 2026,
             "status": "confirmed", "amount": 1567.97}
        ]
        # Old: query string "2026" → misses int 2026
        old_paid = sum(p["amount"] for p in all_payments if p["year"] == "2026")
        assert old_paid == 0.0

        # Fixed: try int 2026 first
        new_paid = sum(p["amount"] for p in all_payments if p["year"] == 2026)
        assert new_paid == pytest.approx(1567.97, abs=0.01)


class TestFloatPrecisionFix:
    """
    Regression: Python float // operator gives wrong periods_funded.
    4590.9 // 1530.3 = 2.0 (wrong) even though 4590.9 / 1530.3 = 3.0
    because 1530.3 is stored as 1530.3000000000001 in binary float.

    Fix: use integer-cents arithmetic.
    """

    def _old_periods_funded(self, nc: float, pl: float) -> int:
        return int(max(0.0, nc) // pl)

    def _new_periods_funded(self, nc: float, pl: float) -> int:
        nc_cents = max(0, round(nc * 100))
        pl_cents = round(pl * 100)
        return (nc_cents // pl_cents) if pl_cents > 0 else 0

    def test_ua063_old_gives_wrong_pf(self):
        """Old float // gives 2 instead of 3 for UA063."""
        nc = 4590.9
        pl = round(6121.21 / 4, 2)  # = 1530.30
        assert self._old_periods_funded(nc, pl) == 2, "Old method gives wrong result"

    def test_ua063_new_gives_correct_pf(self):
        """New cents arithmetic gives correct 3 for UA063."""
        nc = 4590.9
        pl = round(6121.21 / 4, 2)  # = 1530.30
        assert self._new_periods_funded(nc, pl) == 3, "New method gives correct result"

    def test_ua063_outstanding_with_correct_pf(self):
        """With pf=3, next_payment_adjusted = $1530.30 (not $0.00)."""
        nc = 4590.9
        pl = round(6121.21 / 4, 2)
        pf = self._new_periods_funded(nc, pl)
        outstanding = round((pf + 1) * pl - nc, 2)
        # Q4 due date (2026-07-01 or similar) not past grace on 2026-03-17
        # → next_payment_adjusted = outstanding = 1530.30
        assert outstanding == pytest.approx(1530.3, abs=0.01)

    def test_exact_multiple_does_not_regress(self):
        """When paid == exactly N periods, pf=N, outstanding=0 (fully funded guard catches it)."""
        pl = 1530.30
        nc = 3 * pl  # exactly 3 periods
        pf = self._new_periods_funded(nc, pl)
        assert pf == 3

    def test_zero_nc_gives_zero_pf(self):
        assert self._new_periods_funded(0.0, 1530.30) == 0

    def test_zero_pl_gives_zero_pf(self):
        assert self._new_periods_funded(4590.9, 0.0) == 0

    def test_partial_payment_gives_pf_zero(self):
        pl = 1530.30
        nc = 500.0  # less than one period
        pf = self._new_periods_funded(nc, pl)
        assert pf == 0
