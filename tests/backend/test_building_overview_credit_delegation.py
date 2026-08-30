# @featuretrace:financial-onboarding — /finance/building-overview delegates credit, never recomputes it.
# Layer: test
# Data flow: static scan of routers/finance.py + behaviour of the FY-label guard.
# Scope: repo-wide (building-agnostic)
# Related: backend/services/finance_metrics/lot_true_balance.py  (THE owner)
#          docs/architecture/canonical_owners.yaml               (concept: lot-true-balance)
"""The building-overview PG branch must not compute unapplied credit itself.

What this pins
--------------
`/finance/building-overview`'s Postgres branch carried its own
``GREATEST(0, received - levied)`` query. It was the ORIGIN of that shape — the
canonical module's docstring cites it — and the copy then drifted away from the
original in four ways, every one of which over-counted credit:

  1. it anti-joined reversals on ``reversal_of_id`` alone, while 127 of East Gate's
     155 reversal entries carry a NULL ``reversal_of_id`` and name their target in
     ``source_reference`` only;
  2. it had no reversal-of-reversal test, so a receipt reversed and then un-reversed
     stayed excluded forever;
  3. it had no ``retired_at IS NULL``;
  4. it INNER-JOINed the levied subquery, dropping any lot that paid something and
     was levied nothing — the case where credit is largest.

Measured live on East Gate FY2026 before the fix: **$1,783,940.36** reported against
a true **$13,478.55**.

This is the drift a duplicate implementation produces even when the duplicate was
right on the day it was written. The fix is delegation, and this test keeps it.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_FINANCE = _ROOT / "backend" / "routers" / "finance.py"


class TestCreditIsDelegatedNotRecomputed:
    def test_the_router_calls_the_canonical_module(self):
        src = _FINANCE.read_text(encoding="utf-8")
        for symbol in ("compute_lot_true_balances", "building_unapplied_credit_cents"):
            assert symbol in src, (
                f"routers/finance.py must call {symbol} from "
                f"services.finance_metrics.lot_true_balance rather than computing "
                f"unapplied credit itself"
            )

    def test_no_local_credit_sql_remains(self):
        """The specific shape, not the word 'credit'.

        ``GREATEST(0, <something> received)`` is the per-lot credit computation. Its
        only correct home is lot_true_balance.py, whose filters are the hard part —
        the arithmetic is trivial and that is exactly why it gets re-typed.
        """
        src = _FINANCE.read_text(encoding="utf-8")
        code = [
            line for line in src.splitlines()
            if not line.lstrip().startswith("#")
        ]
        offenders = [
            line.strip()[:100] for line in code
            if re.search(r"GREATEST\s*\(\s*0\s*,[^)]*received", line)
        ]
        assert not offenders, (
            "routers/finance.py computes per-lot credit itself again:\n  "
            + "\n  ".join(offenders)
            + "\nUse services.finance_metrics.lot_true_balance.compute_lot_true_balances."
        )

    def test_a_financial_year_label_degrades_instead_of_raising(self):
        """A FY *label* ("2025-2026") cannot be cast to the int the received_on window
        needs. The inline query raised a Postgres error and took the whole overview
        down with it. Credit is an additive dimension, so the branch must report 0 and
        log — never corrupt or 500 the arrears and levied figures beside it."""
        from routers.finance import _is_plain_calendar_year

        assert _is_plain_calendar_year("2026") is True
        for label in ("2025-2026", "FY2026", "", "26"):
            assert _is_plain_calendar_year(label) is False, (
                f"{label!r} must not be treated as a calendar year — the credit window "
                f"casts it to int"
            )

    def test_the_guard_is_the_canonical_one(self):
        """Imported, not re-typed — a second copy of the guard could disagree with the
        module whose SQL constraint it exists to protect."""
        from routers import finance
        from services.finance_metrics import lot_true_balance

        assert finance._is_plain_calendar_year is lot_true_balance._is_plain_calendar_year
