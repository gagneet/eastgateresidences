"""
tests/backend/test_finance_input_source_guardrails.py

GAP-FIN-015 / migration doc "Data contract rules": operational finance routes must
read from the ledger, never from Demo Bank input/staging tables directly. This is
a static (source-text) guard, not a runtime test — it fails the build the moment
someone adds a Demo Bank query to an operational route, before it ever ships.

Deliberately excluded from this guard (by design, not oversight):
  - finance.py's existing `staging_strata_web_snapshots` read for the GAP-FIN-014
    `portal_cross_check` block — already an explicitly labelled cross-check field,
    not a source of operational truth.
  - routers/bank_feeds.py, demo_bank's router.py, routers/financial_matching.py,
    routers/finance_reconciliation.py — these ARE the input/reconciliation surfaces;
    forbidding them from touching Demo Bank tables would defeat their purpose.
  - finance.py's `_fallback_financial_transactions` (2026-08-05 audit finding, commit
    `4ab7f05c`): reads `demo_bank_transactions` ONLY when the `financial_transactions`
    mirror is empty for a year, to avoid a blank Transactions tab. Reviewed and kept as
    a narrow, function-scoped exemption (its body is excluded from the scan below, not
    the whole file) because every row it returns is now explicitly labelled
    `is_unposted_evidence: true` + a `disclosure` string (see
    `test_fallback_demo_bank_rows_are_labelled_unposted_evidence` below) — satisfying
    this guard's own instruction to "expose it as an explicitly labelled cross-check
    field instead" rather than presenting staging data as posted ledger truth.

"""
from __future__ import annotations

import os

from routers import demo_bank as _demo_bank_router_module

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKEND_ROOT = os.path.join(_REPO_ROOT, "backend")
_DEMO_BANK_ROUTER_PATH = _demo_bank_router_module.__file__

# Files whose job is to serve OPERATIONAL finance values (KPI contract, arrears,
# owner-facing balances). These must derive every value from the ledger.
_OPERATIONAL_FINANCE_FILES = [
    "routers/finance.py",
    "routers/portfolio.py",
    "routers/owner_finance.py",
]

# Demo Bank input/staging collections. An operational route touching any of these
# directly (bypassing ledger posting) is exactly the bug GAP-FIN-015 identified.
_FORBIDDEN_DEMO_BANK_TOKENS = [
    "demo_bank_transactions",
    "demo_bank_accounts",
    "demo_bank_import_batches",
]

# Files that are explicitly allowed to read Demo Bank tables — they ARE the
# input/reconciliation surface, not operational display routes. demo_bank's router.py
# is looked up via _DEMO_BANK_ROUTER_PATH (installed package), not a backend-relative path.
_INPUT_SURFACE_FILES = [
    "routers/bank_feeds.py",
    _DEMO_BANK_ROUTER_PATH,
    "routers/financial_matching.py",
    "routers/finance_reconciliation.py",
]


def _read(relative_path: str) -> str:
    path = relative_path if os.path.isabs(relative_path) else os.path.join(_BACKEND_ROOT, relative_path)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# One narrow, function-scoped exemption per (file, function) -- NOT a whole-file
# exemption. Adding an entry here must be paired with a test proving the excluded
# function actually labels its Demo Bank-sourced output as unposted evidence (see
# test_fallback_demo_bank_rows_are_labelled_unposted_evidence below). Blanking the
# function body (not deleting it) keeps line numbers stable if this ever needs
# debugging against the original file.
_REVIEWED_FUNCTION_EXEMPTIONS = {
    "routers/finance.py": ["_fallback_financial_transactions"],
}


def _read_with_reviewed_exemptions(relative_path: str) -> str:
    """Same as _read(), minus the body of any function listed in
    _REVIEWED_FUNCTION_EXEMPTIONS for this file. A NEW Demo Bank reference anywhere
    else in the file -- including a new function -- still fails this guard."""
    import ast

    source = _read(relative_path)
    exempt_functions = _REVIEWED_FUNCTION_EXEMPTIONS.get(relative_path, [])
    if not exempt_functions:
        return source

    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in exempt_functions:
            start, end = node.lineno - 1, node.end_lineno  # end_lineno is inclusive
            lines[start:end] = ["\n"] * (end - start)
    return "".join(lines)


def test_operational_finance_routes_never_reference_demo_bank_tables():
    for relative_path in _OPERATIONAL_FINANCE_FILES:
        source = _read_with_reviewed_exemptions(relative_path)
        for token in _FORBIDDEN_DEMO_BANK_TOKENS:
            assert token not in source, (
                f"{relative_path} references {token!r} — operational finance routes "
                "must derive values from the ledger, not Demo Bank input/staging "
                "tables (GAP-FIN-015). If this route now needs input/evidence data, "
                "expose it as an explicitly labelled cross-check field instead, or "
                "move the logic to a dedicated reconciliation route."
            )


def test_guard_is_not_vacuous_input_surfaces_do_reference_demo_bank_tables():
    """Sanity check: prove the guard actually distinguishes something — the
    input/reconciliation surfaces SHOULD reference Demo Bank tables, otherwise
    the guard above would trivially pass for the wrong reason (e.g. a typo'd
    token that never appears anywhere)."""
    for relative_path in _INPUT_SURFACE_FILES:
        source = _read(relative_path)
        assert any(token in source for token in _FORBIDDEN_DEMO_BANK_TOKENS), (
            f"{relative_path} was expected to reference at least one Demo Bank "
            "table (it's an input/reconciliation surface) — if this file's role "
            "changed, update _INPUT_SURFACE_FILES/_OPERATIONAL_FINANCE_FILES above."
        )


def _read_function_body(relative_path: str, function_name: str) -> str:
    """AST-sliced body of one function -- reuses the same lineno/end_lineno technique
    as _read_with_reviewed_exemptions above (not a "\\nasync def " string search,
    which would silently mis-slice if the target were the last function in the file,
    contained a nested def, or a sibling were renamed to a non-`async def` form)."""
    import ast

    source = _read(relative_path)
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return "".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"{function_name!r} not found in {relative_path}")


def test_fallback_demo_bank_rows_are_labelled_unposted_evidence():
    """Companion to the exemption in _REVIEWED_FUNCTION_EXEMPTIONS above:
    _fallback_financial_transactions is allowed to read demo_bank_transactions ONLY
    because it stamps every such row with is_unposted_evidence + a disclosure string
    (Financial Evidence Gateway: staging data is never presented indistinguishably
    from posted ledger truth). If a future edit removes that labelling, this must
    fail even though the SQL-guard test above can't see inside the exempted function."""
    fn_body = _read_function_body("routers/finance.py", "_fallback_financial_transactions")
    assert "is_unposted_evidence" in fn_body
    assert "disclosure" in fn_body


def test_finance_kpi_contract_labels_portal_data_as_cross_check_only():
    """finance.py IS allowed to read staging_strata_web_snapshots-derived portal
    data, but only as an explicitly labelled cross-check — never as a value that
    feeds unit_counts/collection_mix/arrears_metrics directly (GAP-FIN-014)."""
    source = _read("routers/finance.py")
    assert "portal_cross_check" in source
    assert "requires_reconciliation" in source
