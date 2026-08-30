"""Tests for scripts/preview_bounded_portal_reconciliation.py (GAP-ONBOARD-004 A2).

Static SQL-text checks -- the script's queries are simple enough that asserting the fix is
present in the compiled SQL is a meaningful regression guard without needing a live-DB harness
for this thin orchestration script (the real classification logic is covered separately in
test_bounded_portal_reconciliation_engine.py).
"""
from __future__ import annotations

import sys

sys.path.insert(0, "backend")

from scripts.preview_bounded_portal_reconciliation import _PERIOD_BOUND_SQL_SIMPLE  # noqa: E402


class TestUnitNumberFallback:
    """Self-audit fix (2026-08-10): core.lots.unit_number IS NULLable at the schema level
    (unlike lot_number, which is NOT NULL). Without a fallback, a lot with NULL unit_number
    would be silently misclassified as 'no_portal_data' every time (a Python-None vs
    SQL-string-"None" key mismatch between this query and the lot_to_unit query in main()),
    hiding it from classification entirely on any building where this occurs -- East Gate
    happens to have every lot's unit_number populated, so this was invisible there."""

    def test_period_bound_sql_coalesces_unit_number_and_lot_number(self):
        sql = str(_PERIOD_BOUND_SQL_SIMPLE)
        assert "COALESCE(l.unit_number, l.lot_number)" in sql

    def test_period_bound_sql_groups_by_the_coalesced_value_not_bare_unit_number(self):
        """A GROUP BY on the bare column (not the COALESCE expression) would fail on
        PostgreSQL for a SELECT that projects the COALESCE expression under that alias."""
        sql = str(_PERIOD_BOUND_SQL_SIMPLE)
        assert "GROUP BY l.lot_id, COALESCE(l.unit_number, l.lot_number)" in sql
