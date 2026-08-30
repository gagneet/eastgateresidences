"""
tests/backend/domain/test_journals.py — Unit tests for domain/journals.py (Phase 2).

Covers all three write-time invariants:
  1. At least 2 lines.
  2. Every line amount_cents > 0 (zero-value lines must be rejected).
  3. Exact integer balance: sum(debits) == sum(credits).

Also verifies:
  - A correctly balanced journal is constructed without error.
  - control_total_cents matches sum of debit lines.
  - Concurrent construction (multiple Journal objects) is independently validated.
  - Multi-tenant: building_id is carried through correctly.
"""
import pytest
from pydantic import ValidationError

from domain.journals import Journal, JournalLine, JournalSide

BUILDING_A = "16244"
BUILDING_B = "13195"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _balanced_lines(amount: int = 10000):
    """Return a minimal balanced pair: one debit, one credit."""
    return [
        JournalLine(account_id="acc-1", side=JournalSide.DEBIT, amount_cents=amount),
        JournalLine(account_id="acc-2", side=JournalSide.CREDIT, amount_cents=amount),
    ]


def _make_journal(**overrides) -> Journal:
    defaults = dict(
        building_id=BUILDING_A,
        period_id="2026-Q1",
        posting_date="2026-04-23",
        reference="REF-001",
        narration="Test journal entry",
        lines=_balanced_lines(),
    )
    defaults.update(overrides)
    return Journal(**defaults)


# ── Happy path ────────────────────────────────────────────────────────────────

def test_balanced_journal_constructs_successfully():
    j = _make_journal()
    assert j.building_id == BUILDING_A
    assert j.period_id == "2026-Q1"
    assert len(j.lines) == 2


def test_control_total_cents_is_sum_of_debits():
    j = _make_journal(lines=[
        JournalLine(account_id="a", side=JournalSide.DEBIT, amount_cents=15000),
        JournalLine(account_id="b", side=JournalSide.DEBIT, amount_cents=5000),
        JournalLine(account_id="c", side=JournalSide.CREDIT, amount_cents=20000),
    ])
    assert j.control_total_cents == 20000


def test_multi_line_balanced_journal():
    """3-line journal: two debits, one credit — must balance exactly."""
    j = _make_journal(lines=[
        JournalLine(account_id="a", side=JournalSide.DEBIT, amount_cents=30000),
        JournalLine(account_id="b", side=JournalSide.DEBIT, amount_cents=20000),
        JournalLine(account_id="c", side=JournalSide.CREDIT, amount_cents=50000),
    ])
    assert j.control_total_cents == 50000


def test_journal_carries_building_id_isolation():
    """building_id must be stored as supplied — multi-tenant partition."""
    j_a = _make_journal(building_id=BUILDING_A)
    j_b = _make_journal(building_id=BUILDING_B)
    assert j_a.building_id == BUILDING_A
    assert j_b.building_id == BUILDING_B
    assert j_a.building_id != j_b.building_id


def test_concurrent_independent_journals():
    """Two Journal objects constructed independently do not share state."""
    j1 = _make_journal(reference="REF-001", lines=_balanced_lines(10000))
    j2 = _make_journal(reference="REF-002", lines=_balanced_lines(25000))
    assert j1.control_total_cents == 10000
    assert j2.control_total_cents == 25000


# ── Invariant 1: at least 2 lines ─────────────────────────────────────────────

def test_single_line_journal_rejected():
    with pytest.raises(ValidationError) as exc:
        _make_journal(lines=[
            JournalLine(account_id="a", side=JournalSide.DEBIT, amount_cents=10000),
        ])
    assert "2" in str(exc.value) or "least" in str(exc.value).lower()


def test_empty_lines_journal_rejected():
    with pytest.raises(ValidationError):
        _make_journal(lines=[])


# ── Invariant 2: no zero-value lines ──────────────────────────────────────────

def test_zero_amount_debit_line_rejected():
    with pytest.raises(ValidationError) as exc:
        _make_journal(lines=[
            JournalLine(account_id="a", side=JournalSide.DEBIT, amount_cents=0),
            JournalLine(account_id="b", side=JournalSide.CREDIT, amount_cents=0),
        ])
    assert "greater than 0" in str(exc.value).lower() or "non-positive" in str(exc.value).lower()


def test_negative_amount_line_rejected():
    """Negative amounts are always invalid — reversals use opposite side."""
    with pytest.raises(ValidationError):
        JournalLine(account_id="a", side=JournalSide.DEBIT, amount_cents=-500)


def test_zero_amount_credit_line_rejected():
    with pytest.raises(ValidationError) as exc:
        _make_journal(lines=[
            JournalLine(account_id="a", side=JournalSide.DEBIT, amount_cents=100),
            JournalLine(account_id="b", side=JournalSide.CREDIT, amount_cents=0),
        ])
    assert "greater than 0" in str(exc.value).lower() or "non-positive" in str(exc.value).lower()


# ── Invariant 3: exact integer balance ────────────────────────────────────────

def test_unbalanced_journal_rejected_debit_excess():
    """Debits > credits by 1 cent must fail — no float tolerance."""
    with pytest.raises(ValidationError) as exc:
        _make_journal(lines=[
            JournalLine(account_id="a", side=JournalSide.DEBIT, amount_cents=10001),
            JournalLine(account_id="b", side=JournalSide.CREDIT, amount_cents=10000),
        ])
    assert "balanced" in str(exc.value).lower() or "balance" in str(exc.value).lower()


def test_unbalanced_journal_rejected_credit_excess():
    with pytest.raises(ValidationError) as exc:
        _make_journal(lines=[
            JournalLine(account_id="a", side=JournalSide.DEBIT, amount_cents=9999),
            JournalLine(account_id="b", side=JournalSide.CREDIT, amount_cents=10000),
        ])
    assert "balanced" in str(exc.value).lower() or "balance" in str(exc.value).lower()


def test_float_tolerance_gap_rejected():
    """The old router allowed abs(debit-credit) <= 0.001; domain enforces exact equality.

    50001 and 50000 differ by 1 cent — previously within the router's float threshold
    if amounts were small, but the domain model must reject any non-zero difference.
    """
    with pytest.raises(ValidationError):
        _make_journal(lines=[
            JournalLine(account_id="a", side=JournalSide.DEBIT, amount_cents=50001),
            JournalLine(account_id="b", side=JournalSide.CREDIT, amount_cents=50000),
        ])


def test_large_balanced_journal_passes():
    """Large amounts (e.g. $1M levy) must work with integer cents."""
    j = _make_journal(lines=[
        JournalLine(account_id="a", side=JournalSide.DEBIT, amount_cents=100_000_000),
        JournalLine(account_id="b", side=JournalSide.CREDIT, amount_cents=100_000_000),
    ])
    assert j.control_total_cents == 100_000_000


def test_journal_period_id_none_is_valid():
    """period_id=None is valid — used for postings outside a managed period (legacy path)."""
    j = _make_journal(period_id=None)
    assert j.period_id is None
    assert j.building_id == BUILDING_A


def test_malformed_input_missing_required_field():
    """Missing narration must raise ValidationError before invariant checks run."""
    with pytest.raises((ValidationError, TypeError)):
        Journal(
            building_id=BUILDING_A,
            period_id="2026-Q1",
            posting_date="2026-04-23",
            reference="REF-001",
            # narration is missing
            lines=_balanced_lines(),
        )
