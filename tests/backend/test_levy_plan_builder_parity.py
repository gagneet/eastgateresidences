"""The two levy-plan builders feed the SAME apply function and must classify identically.

`apply_levy_regeneration()` decides what to write purely from `LevyRegenerationLine.action`,
and it holds a line back ONLY on the exact string "manual_review_overpaid". So a builder
that never emits that action does not merely lose a warning — it silently auto-applies a
rewrite over a lot that has already paid MORE than the regenerated charge.

`build_phase_f_prime_levy_plan()` (the onboarding-staging source) did exactly that: it was
dead code with zero callers, so the divergence from `build_levy_regeneration_plan()` (the
live source) was invisible until the staging source was wired into regenerate-apply. These
tests pin the two together at the level that matters — the safety classification — rather
than at the level of "they both call the same apportionment core", which was true and
insufficient.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

SERVICE = BACKEND / "services" / "levy_generation_service.py"
_TREE = ast.parse(SERVICE.read_text())

LIVE = "build_levy_regeneration_plan"
STAGING = "build_phase_f_prime_levy_plan"
APPLY = "apply_levy_regeneration"


def _fn(name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(_TREE):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {SERVICE}")


def _src(name: str) -> str:
    return ast.unparse(_fn(name))


def test_apply_holds_lines_back_only_on_the_exact_overpaid_action():
    """Pins the coupling the parity tests below depend on."""
    body = _src(APPLY)
    assert "line.action == 'manual_review_overpaid'" in body
    assert "manual_review_count += 1" in body


@pytest.mark.parametrize("builder", [LIVE, STAGING])
def test_both_builders_classify_an_overpaid_lot_for_manual_review(builder):
    body = _src(builder)
    assert "manual_review_overpaid" in body, (
        f"{builder} never emits 'manual_review_overpaid', so apply_levy_regeneration "
        f"would auto-apply over an already-overpaid lot instead of holding it for review."
    )
    assert "existing_paid > principal_cents + gst_cents" in body.replace("(", "").replace(")", ""), (
        f"{builder} does not compare existing paid_cents against the regenerated total."
    )


@pytest.mark.parametrize("builder", [LIVE, STAGING])
def test_both_builders_block_reconciliation_when_a_lot_is_overpaid(builder):
    """`totals_reconcile` is the router's 422 gate — it must fail closed on overpaid lots."""
    body = _src(builder)
    assert "not overpaid_lines" in body, (
        f"{builder} reports totals_reconcile without excluding overpaid lines, so the "
        f"router's reconciliation gate passes and nobody is asked to look."
    )


@pytest.mark.parametrize("builder", [LIVE, STAGING])
def test_both_builders_disclose_lots_with_no_postgres_row(builder):
    """apply_levy_regeneration silently `continue`s on lot_id=None; say so in warnings."""
    assert "excluded from apply set" in _src(builder), (
        f"{builder} drops unmatched lots from the applied set without telling the operator."
    )


def test_staging_builder_tolerates_financial_year_strings():
    """`year` is copied verbatim from operator CSV — "2025-2026" must not 500."""
    from services.levy_generation_service import _staging_year

    assert _staging_year("2025") == 2025
    assert _staging_year("2025-2026") == 2025
    assert _staging_year(2025) == 2025
    assert _staging_year(" 2025-2026 ") == 2025
    with pytest.raises(ValueError):
        _staging_year("")


def test_staging_source_surfaces_bad_input_as_4xx_not_500():
    router = (BACKEND / "routers" / "financial_onboarding.py").read_text()
    fn = next(
        n for n in ast.walk(ast.parse(router))
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_build_levy_plan"
    )
    body = ast.unparse(fn)
    assert "status_code=409" in body, "missing staging rows must be an actionable 409"
    assert "status_code=422" in body, "malformed staging data must be a 422, not a 500"
