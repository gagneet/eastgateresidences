"""
Tests for FinancialReadService.get_unit_levy_balance_list()'s bulk-query rewrite (GAP-FIN-069).

Written during a 2026-08-19 audit of the rewrite, which found a real, previously-untested
behavioural difference: the prior per-unit fan-out version returned `[]` (empty list) when
`_get_financial_year_window()` failed to resolve for the whole building (because every per-unit
`get_unit_levy_balance()` call independently returned None, and the old code's
`[b for b in balances if b is not None]` filtered them all out) -- the bulk rewrite instead
returns `None` directly, since it checks the window once up front.

This is NOT a regression -- of the 4 live callers, `scripts/dr_mongo_snapshot.py` already treats
`None`/`[]` identically (`if not balances:`, per its own 2026-08-11 audit note), and the other 3
(`routers/finance.py`'s levy_kpi/arrears_detail PG branches, `finance_shadow_read_service.py`'s
finance.unit_levy_ledger branch) check `is None` specifically -- meaning the OLD `[]`-on-failure
behaviour would have let them silently proceed as if the building had zero units with valid data,
instead of correctly falling back. The NEW `None` behaviour fixes that for those 3 callers. This
test suite locks in the corrected, intentional contract.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.financial_read_service import FinancialReadService


def _mock_result(rows: list) -> MagicMock:
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows)
    return result


@pytest.mark.asyncio
async def test_returns_none_when_financial_year_window_unresolvable():
    """The whole-building window check must short-circuit to None, matching the corrected
    contract -- not silently return [] as if the building had zero lots."""
    service = FinancialReadService()
    with patch.object(service, "_get_financial_year_window", new=AsyncMock(return_value=None)):
        result = await service.get_unit_levy_balance_list(building_id="13195", financial_year="2099")
    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_building_has_no_lots():
    service = FinancialReadService()
    window = SimpleNamespace(financial_year="2026", starts_on="2026-01-01", ends_on="2026-12-31")
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=_mock_result([]))  # empty core.lots result
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch.object(service, "_get_financial_year_window", new=AsyncMock(return_value=window)), \
         patch.object(service, "_resolve_scheme", new=AsyncMock(return_value={
             "scheme_id": "11111111-1111-1111-1111-111111111111",
             "tenant_id": "22222222-2222-2222-2222-222222222222",
         })), \
         patch("services.financial_read_service.async_session_context", return_value=session_ctx), \
         patch("services.financial_read_service.set_tenant", new=AsyncMock()):
        result = await service.get_unit_levy_balance_list(building_id="13195", financial_year="2026")
    assert result is None


@pytest.mark.asyncio
async def test_aggregates_bulk_rows_correctly_per_lot():
    """Two lots: one with full opening/levy/closing rows, one with NO opening/closing rows at
    all (must default to 0, matching the old per-unit COALESCE(...,0) semantics -- not KeyError
    or a wrong default)."""
    service = FinancialReadService()
    window = SimpleNamespace(financial_year="2026", starts_on="2026-01-01", ends_on="2026-12-31")

    lot_rows = _mock_result([
        SimpleNamespace(lot_id="lot-a", unit_number="1"),
        SimpleNamespace(lot_id="lot-b", unit_number="2"),
    ])
    opening_rows = _mock_result([
        SimpleNamespace(lot_id="lot-a", opening_balance_cents=1000),
        # lot-b has no journal_lines before the FY start at all -- absent from this result set.
    ])
    levy_rows = _mock_result([
        SimpleNamespace(lot_id="lot-a", levied_cents=50000, paid_cents=40000),
        SimpleNamespace(lot_id="lot-b", levied_cents=20000, paid_cents=20000),
    ])
    closing_rows = _mock_result([
        SimpleNamespace(lot_id="lot-a", closing_balance_cents=11000),
        # lot-b has zero net AR activity -- absent from this result set too.
    ])

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[lot_rows, opening_rows, levy_rows, closing_rows])
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch.object(service, "_get_financial_year_window", new=AsyncMock(return_value=window)), \
         patch.object(service, "_resolve_scheme", new=AsyncMock(return_value={
             "scheme_id": "11111111-1111-1111-1111-111111111111",
             "tenant_id": "22222222-2222-2222-2222-222222222222",
         })), \
         patch("services.financial_read_service.async_session_context", return_value=session_ctx), \
         patch("services.financial_read_service.set_tenant", new=AsyncMock()):
        result = await service.get_unit_levy_balance_list(building_id="13195", financial_year="2026")

    assert result is not None
    assert len(result) == 2

    lot_a = next(b for b in result if b["unit_number"] == "1")
    assert lot_a["opening_balance"] == 10.0
    assert lot_a["levied_amount"] == 500.0
    assert lot_a["paid_amount"] == 400.0
    assert lot_a["closing_balance"] == 110.0
    assert lot_a["arrears"] == 110.0
    assert lot_a["financial_year"] == "2026"

    lot_b = next(b for b in result if b["unit_number"] == "2")
    assert lot_b["opening_balance"] == 0.0  # no opening row for lot-b -- defaults to 0
    assert lot_b["levied_amount"] == 200.0
    assert lot_b["paid_amount"] == 200.0
    assert lot_b["closing_balance"] == 0.0  # no closing row for lot-b -- defaults to 0
    assert lot_b["arrears"] == 0.0

    # Exactly 4 queries issued (lots + opening + levy + closing) regardless of lot count --
    # the whole point of the bulk rewrite.
    assert mock_session.execute.call_count == 4


@pytest.mark.asyncio
async def test_negative_closing_balance_clamps_arrears_to_zero():
    """arrears = max(0, closing_balance_cents) -- a credit-holding lot (negative closing)
    must show $0 arrears, never a negative arrears figure."""
    service = FinancialReadService()
    window = SimpleNamespace(financial_year="2026", starts_on="2026-01-01", ends_on="2026-12-31")

    lot_rows = _mock_result([SimpleNamespace(lot_id="lot-a", unit_number="1")])
    empty = _mock_result([])
    closing_rows = _mock_result([SimpleNamespace(lot_id="lot-a", closing_balance_cents=-5000)])

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[lot_rows, empty, empty, closing_rows])
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch.object(service, "_get_financial_year_window", new=AsyncMock(return_value=window)), \
         patch.object(service, "_resolve_scheme", new=AsyncMock(return_value={
             "scheme_id": "11111111-1111-1111-1111-111111111111",
             "tenant_id": "22222222-2222-2222-2222-222222222222",
         })), \
         patch("services.financial_read_service.async_session_context", return_value=session_ctx), \
         patch("services.financial_read_service.set_tenant", new=AsyncMock()):
        result = await service.get_unit_levy_balance_list(building_id="13195", financial_year="2026")

    assert result[0]["closing_balance"] == -50.0
    assert result[0]["arrears"] == 0.0
