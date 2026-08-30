"""
tests/backend/test_gap_fin_014_ledger_segregation.py — GAP-FIN-014 fitness function.

Static regression guard for the bug found and fixed in backend/routers/portfolio.py:
GET /portfolio/summary, /dashboard, /buildings, and /arrears-summary were sourcing
dollar arrears/outstanding figures from building_summaries (Strata Web portal
cross-check data) or levy_payments.balance (the receipts/detail layer, not the
accounting ledger) instead of unit_levy_ledger (the accounting ledger).

This test does not attempt a generic, repo-wide AST audit — that has a high false
positive rate (many legitimate reads of these collections exist for cross-check
widgets, imports, and reconciliation views). Instead it pins down the exact
regression that was found: the four portfolio.py endpoints must keep calling the
ledger-derived helper, and must never reintroduce a portal/receipts-sourced dollar
figure. If a future change reverts this, this test fails instead of the drift
going unnoticed until the next audit.
"""
from __future__ import annotations

import ast
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))

from routers import portfolio  # noqa: E402


def _executable_source(fn) -> str:
    """Source of fn's body, excluding its docstring and full-line comments —
    so explanatory prose documenting *why* a pattern is forbidden (which
    necessarily mentions that pattern) doesn't trip its own regression check."""
    src = inspect.getsource(fn)
    tree = ast.parse(src)
    func_node = tree.body[0]
    lines = src.splitlines()

    body = func_node.body
    has_docstring = (
        body and isinstance(body[0], ast.Expr)
        and isinstance(getattr(body[0], "value", None), ast.Constant)
        and isinstance(body[0].value.value, str)
    )
    body_start = body[1].lineno if has_docstring and len(body) > 1 else (
        body[0].lineno if not has_docstring else len(lines) + 1
    )

    code_lines = [ln for ln in lines[body_start - 1:] if not ln.strip().startswith("#")]
    return "\n".join(code_lines)


_PORTFOLIO_DOLLAR_ENDPOINTS = (
    portfolio.get_portfolio_summary,
    portfolio.get_portfolio_dashboard,
    portfolio.list_portfolio_buildings,
    portfolio.get_arrears_summary,
)


def test_portfolio_dollar_endpoints_call_ledger_arrears_helper():
    """Every endpoint that returns a dollar arrears/outstanding figure must call
    _get_portfolio_ledger_arrears() — the single ledger-derived source for those
    figures. A regression back to reading building_summaries.arrears_cents (or
    similar) directly would not call this helper."""
    for fn in _PORTFOLIO_DOLLAR_ENDPOINTS:
        source = _executable_source(fn)
        assert "_get_portfolio_ledger_arrears(" in source, (
            f"{fn.__name__} no longer calls _get_portfolio_ledger_arrears() — "
            "GAP-FIN-014 regression: dollar arrears must come from unit_levy_ledger."
        )


_PORTAL_ARREARS_FIELD_ACCESS = re.compile(r'''["']arrears_cents["']''')


def test_portfolio_dollar_endpoints_never_read_portal_arrears_field():
    """None of these endpoints may read building_summaries' arrears_cents field —
    that field name only exists on the portal-derived summary doc, never on
    unit_levy_ledger, so its presence (as a quoted dict key/string, not merely as
    a substring of an unrelated identifier like total_arrears_cents) is an
    unambiguous signal of a regression."""
    for fn in _PORTFOLIO_DOLLAR_ENDPOINTS:
        source = _executable_source(fn)
        assert not _PORTAL_ARREARS_FIELD_ACCESS.search(source), (
            f"{fn.__name__} references the quoted string 'arrears_cents' (a "
            "building_summaries/portal field) — GAP-FIN-014 regression: this "
            "must come from unit_levy_ledger."
        )


def test_arrears_summary_never_reads_levy_payments():
    """GET /portfolio/arrears-summary must never source figures from levy_payments
    — that collection is the receipts/detail layer, not the accounting ledger."""
    source = _executable_source(portfolio.get_arrears_summary)
    assert "levy_payments" not in source, (
        "get_arrears_summary references levy_payments — GAP-FIN-014 regression: "
        "arrears must come from unit_levy_ledger (via _get_portfolio_ledger_arrears), "
        "not the receipts/detail layer."
    )


def test_ledger_arrears_helper_only_reads_unit_levy_ledger():
    """The shared helper itself must only read unit_levy_ledger — if it ever grows
    a building_summaries or levy_payments fallback, that reintroduces exactly the
    bug this helper was built to close."""
    source = _executable_source(portfolio._get_portfolio_ledger_arrears)
    assert "unit_levy_ledger" in source
    assert "building_summaries" not in source
    assert "levy_payments" not in source
