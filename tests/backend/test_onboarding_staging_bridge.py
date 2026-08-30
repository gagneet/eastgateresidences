"""The onboarding staging collections must have a reader.

``POST /onboarding/scheme/{id}/import-historical-financials`` deliberately writes
to isolated ``historical_*`` Mongo collections (ADR-022) so an import cannot
disturb a live building's records. ``build_phase_f_prime_levy_plan()`` is the
documented bridge that reads them back out into the GST-aware UOE apportionment
core — but for a long time it had **zero callers anywhere in the repo**, so an
onboarded building's uploaded history could be staged and then never used.

These tests pin the bridge in place: the staging writer, the reader, and the
route that exposes the reader must all stay connected.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

STAGING_COLLECTIONS = [
    "historical_annual_levies",
    "historical_levy_issuances",
    "historical_financial_snapshots",
    "historical_expense_transactions",
]

ONBOARDING_ROUTER = BACKEND / "routers" / "onboarding.py"
FINANCIAL_ONBOARDING_ROUTER = BACKEND / "routers" / "financial_onboarding.py"
LEVY_SERVICE = BACKEND / "services" / "levy_generation_service.py"


def test_staging_collections_are_written_by_the_onboarding_importer():
    src = ONBOARDING_ROUTER.read_text()
    for collection in STAGING_COLLECTIONS:
        assert f"db.{collection}" in src, f"{collection} is no longer written by the importer"


def test_phase_f_prime_reader_has_a_caller_outside_its_own_module():
    """Regression guard: the reader was dead code with zero callers.

    Data that can be uploaded but never read is worse than an upload that fails —
    it looks like it worked.
    """
    callers = []
    for path in FINANCIAL_ONBOARDING_ROUTER.parent.rglob("*.py"):
        if path == LEVY_SERVICE:
            continue
        if "build_phase_f_prime_levy_plan" in path.read_text():
            callers.append(path.name)
    for path in (BACKEND / "services").rglob("*.py"):
        if path == LEVY_SERVICE:
            continue
        if "build_phase_f_prime_levy_plan" in path.read_text():
            callers.append(path.name)

    assert callers, (
        "build_phase_f_prime_levy_plan() has no caller — the onboarding "
        "historical_* staging collections would have no reader again."
    )


def test_regenerate_endpoints_expose_the_staging_source():
    src = FINANCIAL_ONBOARDING_ROUTER.read_text()
    assert src.count('pattern="^(live|onboarding_staging)$"') == 2, (
        "both regenerate-plan and regenerate-apply must offer the staging source"
    )
    assert "_build_levy_plan(" in src


def test_live_source_still_requires_an_explicit_year_window():
    """``live`` must not silently plan across an unbounded year range."""
    tree = ast.parse(FINANCIAL_ONBOARDING_ROUTER.read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_require_year_window"
    )
    body = ast.unparse(fn)
    assert "source == 'live'" in body
    assert "from_year is None or to_year is None" in body


@pytest.mark.parametrize("endpoint_fn", ["plan_levy_items_regeneration", "apply_levy_items_regeneration"])
def test_both_regenerate_endpoints_validate_the_year_window(endpoint_fn):
    tree = ast.parse(FINANCIAL_ONBOARDING_ROUTER.read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == endpoint_fn
    )
    assert "_require_year_window(source, from_year, to_year)" in ast.unparse(fn)


def test_apply_still_requires_an_approved_batch_and_confirm():
    """The staging source must not weaken dual control on the write path."""
    tree = ast.parse(FINANCIAL_ONBOARDING_ROUTER.read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "apply_levy_items_regeneration"
    )
    body = ast.unparse(fn)
    assert "batch.status != 'approved'" in body
    assert "if not request.confirm" in body
    assert "financial_integration_layer_v2" in body
