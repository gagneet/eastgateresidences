"""Pure tests for the per-unit interest + late-fee penalty computation engine.

Covers the East Gate model (nil percentage interest + $55 GST-incl late fee per 14 days overdue,
from 2022) and the generic percentage-interest path. The late-fee/period logic runs without the
pydantic dependency graph (lazy import in compute_percentage_interest); the percentage-interest
assertions exercise the shared arrears_interest_service formula.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from services.interest_penalty_service import (  # noqa: E402
    apply_net_credit_override,
    build_overdue_periods_from_status,
    compute_credit_interest_estimate,
    compute_late_fee_penalty,
    compute_percentage_interest,
    compute_unit_interest_and_penalty,
    zero_charges,
)


# ── Late-fee penalty (flat amount per window) ────────────────────────────────

def test_late_fee_one_amount_per_full_window():
    assert compute_late_fee_penalty([{"days_overdue": 14}], 55.0, 14) == 55.0
    assert compute_late_fee_penalty([{"days_overdue": 30}], 55.0, 14) == 110.0  # floor(30/14)=2
    assert compute_late_fee_penalty([{"days_overdue": 13}], 55.0, 14) == 0.0    # < one window


def test_late_fee_zero_when_no_policy():
    assert compute_late_fee_penalty([{"days_overdue": 100}], 0.0, 14) == 0.0
    assert compute_late_fee_penalty([{"days_overdue": 100}], 55.0, 0) == 0.0


def test_late_fee_sums_across_periods():
    periods = [{"days_overdue": 14}, {"days_overdue": 28}]
    assert compute_late_fee_penalty(periods, 55.0, 14) == 55.0 + 110.0


# ── Overdue-period builder ───────────────────────────────────────────────────

def test_overdue_period_included_when_past_grace_and_unpaid():
    ps = [{"quarter": "Q1", "due_date": "2025-03-31", "standard_amount": 900.0, "actual_paid": 0.0}]
    ov = build_overdue_periods_from_status(ps, grace_period_days=14, as_of=date(2025, 12, 31))
    assert len(ov) == 1
    assert ov[0]["principal"] == 900.0
    assert ov[0]["days_overdue"] > 0


def test_overdue_period_excluded_when_paid():
    ps = [{"quarter": "Q1", "due_date": "2025-03-31", "standard_amount": 900.0, "actual_paid": 900.0}]
    assert build_overdue_periods_from_status(ps, grace_period_days=14, as_of=date(2025, 12, 31)) == []


def test_overdue_period_excluded_before_grace_deadline():
    ps = [{"quarter": "Q4", "due_date": "2025-12-31", "standard_amount": 900.0, "actual_paid": 0.0}]
    assert build_overdue_periods_from_status(ps, grace_period_days=14, as_of=date(2025, 6, 30)) == []


def test_overdue_period_uses_standard_not_cumulative():
    # standard_amount is the period's own levy; a big cumulative amount_due must not be used.
    ps = [{"quarter": "Q2", "due_date": "2025-06-30", "standard_amount": 900.0,
           "amount_due": 3545.02, "actual_paid": 0.0}]
    ov = build_overdue_periods_from_status(ps, grace_period_days=14, as_of=date(2025, 12, 31))
    assert ov[0]["principal"] == 900.0


# ── Percentage interest (shared formula; runs in CI with pydantic present) ────

def test_percentage_interest_nil_rate_is_zero():
    ov = [{"principal": 1000.0, "overdue_since": date(2025, 1, 1), "as_at": date(2025, 12, 31)}]
    assert compute_percentage_interest(ov, 0.0, 20.0) == 0.0


def test_percentage_interest_ten_percent_year():
    ov = [{"principal": 1000.0, "overdue_since": date(2025, 1, 1), "as_at": date(2026, 1, 1)}]
    v = compute_percentage_interest(ov, 10.0, 20.0)
    assert 99.9 <= v <= 100.1  # ~ 1000 * 0.10 / 365 * 365


# ── East Gate model: nil interest + $55/14-day late fee from 2022 ────────────

def test_east_gate_model_nil_interest_late_fee_from_2022():
    ps = [{"quarter": "Q1", "due_date": "2025-03-31", "standard_amount": 900.0, "actual_paid": 0.0}]
    r = compute_unit_interest_and_penalty(
        period_status=ps, grace_period_days=14, as_of=date(2025, 12, 31),
        annual_rate_pct=0.0, max_rate_pct=20.0,
        late_fee_policy={"enabled": True, "amount": 55.0, "period_days": 14, "start_year": 2022},
        levy_year=2025,
    )
    assert r["interest"] == 0.0
    assert r["penalty"] > 0.0
    assert r["late_fee_applied"] is True
    assert r["total"] == r["penalty"]


def test_late_fee_policy_gated_by_start_year():
    ps = [{"quarter": "Q1", "due_date": "2021-03-31", "standard_amount": 900.0, "actual_paid": 0.0}]
    r = compute_unit_interest_and_penalty(
        period_status=ps, grace_period_days=14, as_of=date(2021, 12, 31),
        annual_rate_pct=0.0, max_rate_pct=20.0,
        late_fee_policy={"enabled": True, "amount": 55.0, "period_days": 14, "start_year": 2022},
        levy_year=2021,
    )
    assert r["penalty"] == 0.0  # 2021 < 2022 start year


def test_no_policy_yields_zero_penalty():
    ps = [{"quarter": "Q1", "due_date": "2025-03-31", "standard_amount": 900.0, "actual_paid": 0.0}]
    r = compute_unit_interest_and_penalty(
        period_status=ps, grace_period_days=14, as_of=date(2025, 12, 31),
        annual_rate_pct=0.0, max_rate_pct=20.0, late_fee_policy=None, levy_year=2025,
    )
    assert r["penalty"] == 0.0
    assert r["total"] == 0.0


# ── Net-credit override (GAP-FIN-047 §1: never show interest on a unit owed nothing) ─

def test_zero_charges_returns_fresh_dict_each_call():
    """zero_charges() must not return a shared mutable -- a caller mutating one
    result must never leak into the next call's result."""
    a = zero_charges()
    a["interest"] = 999.0
    b = zero_charges()
    assert b["interest"] == 0.0


def test_net_credit_override_zeroes_a_residual_estimate():
    """The exact TH087 scenario (GAP-FIN-047): the per-period estimator finds a
    residual overdue period (interest > 0) even though the unit's authoritative
    year-scoped net_balance is a credit. The override must zero everything, not
    just 'interest' -- penalty/total/late_fee_applied/overdue_period_count must
    all reset too, or a stale nonzero penalty/late-fee-applied flag would survive
    the override and still show the unit as delinquent."""
    residual_estimate = {
        "interest": 79.64, "penalty": 55.0, "total": 134.64, "source": "computed",
        "rate_pct": 0.0, "late_fee_applied": True, "overdue_period_count": 1,
    }
    result = apply_net_credit_override(residual_estimate, ledger_net_balance=-254.98)
    assert result == {
        "interest": 0.0, "penalty": 0.0, "total": 0.0, "source": "computed",
        "late_fee_applied": False, "overdue_period_count": 0,
    }


def test_net_credit_override_boundary_is_inclusive_of_zero():
    """net_balance == 0.0 (exactly settled, no credit and no arrears) must also be
    treated as 'no arrears' -- a unit that owes nothing accrues nothing, whether
    its balance is a credit or an exact nil."""
    estimate = {"interest": 10.0, "penalty": 0.0, "total": 10.0, "source": "computed",
                "late_fee_applied": False, "overdue_period_count": 1}
    assert apply_net_credit_override(estimate, ledger_net_balance=0.0)["interest"] == 0.0


def test_net_credit_override_passes_through_genuine_arrears_unchanged():
    """A unit that genuinely owes money (net_balance > the 0.01 float-rounding
    tolerance) must keep its computed estimate untouched -- the override must
    never suppress interest for a unit that actually is in arrears."""
    estimate = {"interest": 42.0, "penalty": 55.0, "total": 97.0, "source": "computed",
                "late_fee_applied": True, "overdue_period_count": 1}
    result = apply_net_credit_override(estimate, ledger_net_balance=150.00)
    assert result == estimate


def test_net_credit_override_tolerance_matches_rounding_used_elsewhere():
    """0.02 above zero is genuine (if tiny) arrears -- must NOT be swallowed by
    the 0.01 tolerance meant only for float-rounding noise on an settled/credit
    balance."""
    estimate = {"interest": 1.0, "penalty": 0.0, "total": 1.0, "source": "computed",
                "late_fee_applied": False, "overdue_period_count": 1}
    result = apply_net_credit_override(estimate, ledger_net_balance=0.02)
    assert result == estimate


# ── Owner-earned credit interest (GAP-FIN-047 §2, approved 2026-08-19) ───────

def test_compute_credit_interest_estimate_zero_rate_is_zero():
    """Default/off building policy (rate=0.0) — the common case for every building today,
    including East Gate — must yield exactly $0, never a computed positive figure."""
    assert compute_credit_interest_estimate(1000.0, 0.0, 365) == 0.0


def test_compute_credit_interest_estimate_zero_balance_is_zero():
    assert compute_credit_interest_estimate(0.0, 10.0, 365) == 0.0


def test_compute_credit_interest_estimate_zero_days_is_zero():
    assert compute_credit_interest_estimate(1000.0, 10.0, 0) == 0.0


def test_compute_credit_interest_estimate_full_year_matches_simple_interest():
    """$1,000 at 10% p.a. for a full 365-day year = exactly $100.00 simple interest."""
    assert compute_credit_interest_estimate(1000.0, 10.0, 365) == 100.0


def test_compute_credit_interest_estimate_partial_year():
    """$254.98 at 5% p.a. for 100 days: 254.98 * 0.05 / 365 * 100 = $3.49 (rounded)."""
    assert compute_credit_interest_estimate(254.98, 5.0, 100) == 3.49


def test_apply_net_credit_override_no_credit_interest_by_default():
    """Calling with no credit-interest kwargs (the pre-existing call shape, still used by
    every test above) must be byte-identical to before this feature existed — no new keys."""
    estimate = {"interest": 79.64, "penalty": 55.0, "total": 134.64, "source": "computed",
                "rate_pct": 0.0, "late_fee_applied": True, "overdue_period_count": 1}
    result = apply_net_credit_override(estimate, ledger_net_balance=-254.98)
    assert "credit_interest_estimate" not in result
    assert "credit_interest_rate_pct" not in result


def test_apply_net_credit_override_attaches_credit_interest_when_configured():
    """When the building has opted in (rate > 0) and the unit is in real credit, the override
    attaches credit_interest_estimate/credit_interest_rate_pct alongside the zeroed arrears
    fields -- the two concepts (owed BY vs owed TO the owner) must both be visible, not one
    overwriting the other."""
    estimate = {"interest": 79.64, "penalty": 55.0, "total": 134.64, "source": "computed",
                "rate_pct": 0.0, "late_fee_applied": True, "overdue_period_count": 1}
    result = apply_net_credit_override(
        estimate, ledger_net_balance=-254.98,
        credit_interest_rate_pct=5.0, credit_days=100,
    )
    assert result["interest"] == 0.0
    assert result["penalty"] == 0.0
    assert result["credit_interest_estimate"] == 3.49
    assert result["credit_interest_rate_pct"] == 5.0


def test_apply_net_credit_override_no_credit_interest_at_exact_zero_balance():
    """A unit at EXACTLY $0.00 (ledger_net_balance == 0.0) has no money sitting there to earn
    anything -- must not attach a credit_interest_estimate even with a configured rate, since
    apply_net_credit_override's own credit check is ledger_net_balance < 0 (strict), not <= 0."""
    estimate = {"interest": 0.0, "penalty": 0.0, "total": 0.0, "source": "computed",
                "late_fee_applied": False, "overdue_period_count": 0}
    result = apply_net_credit_override(
        estimate, ledger_net_balance=0.0,
        credit_interest_rate_pct=5.0, credit_days=100,
    )
    assert "credit_interest_estimate" not in result


def test_apply_net_credit_override_no_credit_interest_for_genuine_arrears():
    """A unit that genuinely owes money must never see a credit_interest_estimate, even if the
    building has a configured rate — that field only ever applies to a real credit balance."""
    estimate = {"interest": 42.0, "penalty": 55.0, "total": 97.0, "source": "computed",
                "late_fee_applied": True, "overdue_period_count": 1}
    result = apply_net_credit_override(
        estimate, ledger_net_balance=150.00,
        credit_interest_rate_pct=5.0, credit_days=100,
    )
    assert result == estimate
    assert "credit_interest_estimate" not in result


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
