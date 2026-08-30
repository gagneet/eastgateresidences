"""Pure-logic tests for the portal ⇄ constructed-ledger reconciliation engine.

No DB / FastAPI — exercises the building-agnostic pure compute in
backend/services/portal_ledger_reconciliation_service.py and the re-chain planner in
backend/scripts/reconcile_portal_ledger.py. All amounts in integer cents.
"""
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from services.portal_ledger_reconciliation_service import (  # noqa: E402
    DEFAULT_TOLERANCE_CENTS,
    UnitYearLedger,
    _to_cents,
    compute_rechain_plan,
    compute_unit_reconciliation,
)


def mk(year, a_open, a_lev, a_paid, a_close, s_open, s_lev, s_paid, s_close,
       a_int=0, s_int=0):
    return UnitYearLedger(
        year=year,
        admin_opening_cents=a_open, admin_levied_cents=a_lev, admin_interest_cents=a_int,
        admin_paid_cents=a_paid, admin_closing_cents=a_close,
        sinking_opening_cents=s_open, sinking_levied_cents=s_lev, sinking_interest_cents=s_int,
        sinking_paid_cents=s_paid, sinking_closing_cents=s_close,
    )


# ── _to_cents boundary ────────────────────────────────────────────────────────

def test_to_cents_converts_dollar_floats():
    assert _to_cents(698.78) == 69878
    assert _to_cents("203.99") == 20399
    assert _to_cents(None) == 0
    assert _to_cents("") == 0
    assert _to_cents("garbage") == 0


def test_from_doc_parses_dollars_and_spanned_year():
    row = UnitYearLedger.from_doc({
        "year": "2026-27", "admin_opening": 0.0, "admin_levied": 698.78, "admin_paid": 0.0,
        "admin_closing": 698.78, "sinking_opening": 0.0, "sinking_levied": 203.99,
        "sinking_paid": 0.0, "sinking_closing": 203.99,
    })
    assert row is not None
    assert row.year == 2026
    assert row.admin_levied_cents == 69878
    assert row.closing_cents == 69878 + 20399


# ── Clean, fully-chained unit that matches the portal ─────────────────────────

def test_reconciled_when_chained_and_portal_matches():
    y2025 = mk(2025, 0, 200000, 200000, 0, 0, 50000, 50000, 0)   # fully paid, closes 0
    y2026 = mk(2026, 0, 69878, 0, 69878, 0, 20399, 0, 20399)     # opening chains from 0
    r = compute_unit_reconciliation(
        unit_number="U1", years=[y2025, y2026], portal_balance_cents=90277,
    )
    assert r.status == "reconciled"
    assert r.chain_breaks == []
    assert r.row_nonreconciling_years == []
    assert r.rechained_variance_cents == 0
    assert r.paid_gap_class == "reconciled"


# ── The East Gate FY2026 archetype: back-solved opening = chain break ─────────

def test_chain_break_from_backsolved_opening_rechain_resolves():
    # 2025 closes at 0; 2026 opening was back-solved to 81300 (≠ prior closing 0),
    # but its row is internally consistent (closing = opening + levied − paid).
    y2025 = mk(2025, 0, 200000, 200000, 0, 0, 50000, 50000, 0)
    y2026 = mk(2026, 81300, 69878, 0, 151178, 0, 20399, 0, 20399)
    # Portal (real) equals the RE-CHAINED balance: 69878 + 20399 = 90277.
    r = compute_unit_reconciliation(
        unit_number="U1", years=[y2025, y2026], portal_balance_cents=90277,
    )
    assert r.status == "variance"
    # stored balance is inflated by the back-solved opening...
    assert r.constructed_balance_cents == 171577
    assert r.variance_cents == 171577 - 90277
    # ...but re-chaining resolves it to the portal exactly.
    assert r.rechained_balance_cents == 90277
    assert r.rechained_variance_cents == 0
    assert r.rechain_would_resolve is True
    assert r.paid_gap_class == "reconciled"
    # the break is localised to 2026, admin fund.
    assert any(cb.year == 2026 and cb.fund == "admin" for cb in r.chain_breaks)
    assert any(s["year"] == 2026 for s in r.suspected_years)


# ── Reverse-engineered paid classification ────────────────────────────────────

def test_missing_payment_when_constructed_owes_more_than_portal():
    # One year: levied 100000, only 30000 recorded paid → closing 70000.
    # Portal says the owner actually owes only 50000 → they paid 50000, we recorded 30000.
    y = mk(2026, 0, 100000, 30000, 70000, 0, 0, 0, 0)
    r = compute_unit_reconciliation(unit_number="U1", years=[y], portal_balance_cents=50000)
    assert r.paid_gap_class == "missing_payment"
    assert r.rechained_variance_cents == 20000
    assert r.implied_paid_cents == 50000
    assert r.recorded_paid_cents == 30000
    assert r.paid_gap_cents == 20000  # == rechained_variance (algebraic identity)


def test_over_recorded_when_constructed_owes_less_than_portal():
    # levied 100000, 80000 recorded paid (20000 of it a duplicate) → closing 20000.
    # Portal says owner really owes 50000 → we over-credited by 30000.
    y = mk(2026, 0, 100000, 80000, 20000, 0, 0, 0, 0)
    r = compute_unit_reconciliation(unit_number="U1", years=[y], portal_balance_cents=50000)
    assert r.paid_gap_class == "over_recorded"
    assert r.rechained_variance_cents == -30000


def test_no_portal_data_is_its_own_status():
    y = mk(2026, 0, 100000, 100000, 0, 0, 0, 0, 0)
    r = compute_unit_reconciliation(unit_number="U1", years=[y], portal_balance_cents=None)
    assert r.status == "no_portal_data"
    assert r.paid_gap_class == "no_portal_data"
    assert r.variance_cents is None


def test_no_ledger_data():
    r = compute_unit_reconciliation(unit_number="U1", years=[], portal_balance_cents=5000)
    assert r.status == "no_ledger_data"


# ── Row-level (within-year) non-reconciliation ────────────────────────────────

def test_row_nonreconciling_year_flagged():
    # closing (99999) != opening + levied − paid (70000).
    y = mk(2026, 0, 100000, 30000, 99999, 0, 0, 0, 0)
    r = compute_unit_reconciliation(unit_number="U1", years=[y], portal_balance_cents=70000)
    assert 2026 in r.row_nonreconciling_years
    assert any("row_does_not_reconcile" in s["reasons"] for s in r.suspected_years if s["year"] == 2026)


# ── Suspected-year ranking fuses signals ──────────────────────────────────────

def test_suspected_year_ranking_prioritises_multi_signal_years():
    y2025 = mk(2025, 0, 200000, 200000, 0, 0, 50000, 50000, 0)
    y2026 = mk(2026, 81300, 69878, 0, 151178, 0, 20399, 0, 20399)  # chain break on 2026
    r = compute_unit_reconciliation(
        unit_number="U1", years=[y2025, y2026], portal_balance_cents=90277,
        annual_control_variance_years={2026},  # annual-control ALSO flags 2026
    )
    top = r.suspected_years[0]
    assert top["year"] == 2026
    assert top["signal_count"] >= 2  # chain_break + annual_control_variance
    assert "annual_control_variance" in top["reasons"]


# ── Re-chain planner (Phase 2 core) ───────────────────────────────────────────

def test_rechain_plan_recomputes_broken_year_forward():
    y2025 = mk(2025, 0, 200000, 200000, 0, 0, 50000, 50000, 0)
    y2026 = mk(2026, 81300, 69878, 0, 151178, 0, 20399, 0, 20399)
    changes = compute_rechain_plan([y2025, y2026], DEFAULT_TOLERANCE_CENTS)
    assert len(changes) == 1
    c = changes[0]
    assert c["year"] == 2026
    # opening re-chained to prior closing (0), closing recomputed from trusted levied/paid.
    assert c["after"]["admin_opening_cents"] == 0
    assert c["after"]["admin_closing_cents"] == 69878
    assert c["after"]["net_balance_cents"] == 90277
    assert c["before"]["net_balance_cents"] == 171577


def test_rechain_plan_empty_when_already_chained():
    y2025 = mk(2025, 0, 200000, 200000, 0, 0, 50000, 50000, 0)
    y2026 = mk(2026, 0, 69878, 0, 69878, 0, 20399, 0, 20399)
    assert compute_rechain_plan([y2025, y2026], DEFAULT_TOLERANCE_CENTS) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
