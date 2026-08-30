"""
GAP-FIN-014 — GET /finance/kpi-contract returns building-wide collection,
arrears and unit-status figures derived from the ledger-quality-reconciled
canonical unit set, so pages consuming this contract never need to
independently re-derive collection rate/arrears/status client-side.

Tests use year="2025" (a non-current year relative to the fixed system date
used across this session, 2026-07-04) so the current-year arrears-override
branch does not fire, keeping the mock surface small and the assertions
focused on the ledger-quality-driven unit_counts/portal_cross_check
behaviour this GAP is about.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi import HTTPException

from utils.permissions import Permission


def _cursor(rows):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=rows)
    return c


def _east_gate_units():
    return [f"TH{n:03d}" for n in range(1, 88)]  # 87 canonical units


def _make_mock_db(units, ledger_entries, portal_doc=None, levy_doc=None):
    """ledger_entries: list of (unit_number, net_balance, total_levied, total_paid) tuples."""
    mock_db = MagicMock()
    mock_db.units.find = MagicMock(return_value=_cursor([{"unit_number": u} for u in units]))
    mock_db.unit_levy_ledger.find = MagicMock(return_value=_cursor([
        {"unit_number": u, "net_balance": nb} for u, nb, *_ in ledger_entries
    ]))
    agg_rows = [{
        "total_levied": sum(e[2] if len(e) > 2 else 0 for e in ledger_entries),
        "total_paid": sum(e[3] if len(e) > 3 else 0 for e in ledger_entries),
        "total_net_balance": sum(e[1] for e in ledger_entries),
        "units_owing": sum(1 for e in ledger_entries if e[1] > 0),
        "units_credit": sum(1 for e in ledger_entries if e[1] < 0),
        "units_paid_up": sum(1 for e in ledger_entries if e[1] == 0),
        "total_outstanding": sum(e[1] for e in ledger_entries if e[1] > 0),
        "total_opening_arrears": 0,
        "units_opening_arrears": 0,
        "admin_paid": 0,
        "sinking_paid": 0,
    }] if ledger_entries else []
    mock_db.unit_levy_ledger.aggregate = MagicMock(return_value=_cursor(agg_rows))
    mock_db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
    mock_db.building_summaries.find_one = AsyncMock(return_value=portal_doc)
    return mock_db


async def _call(mock_db, year="2025", building_id="13195"):
    from routers.finance import get_finance_kpi_contract
    settings_doc = {
        "grace_period_days": 14,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
    }
    with patch("routers.finance.db", mock_db), \
         patch("utils.finance_helpers.db", mock_db), \
         patch("routers.finance._get_general_settings", AsyncMock(return_value=settings_doc)), \
         patch("routers.finance.get_user_permissions",
               return_value=Permission(can_view_finances=True)):
        return await get_finance_kpi_contract(
            year=year,
            current_user={"id": "u1", "building_id": building_id, "role": "super_admin"},
            building_id=building_id,
        )


@pytest.mark.asyncio
async def test_unit_counts_sum_to_canonical_unit_count():
    """spec test 7: paid_up + owing + credit must equal canonical_unit_count,
    and these must match what get_ledger_quality/get_unit_ledger_stats
    independently compute — no third re-derivation."""
    units = _east_gate_units()
    entries = [(u, 0.0, 1000.0, 1000.0) for u in units[:-2]]
    entries.append((units[-2], 50.0, 1000.0, 950.0))   # owing
    entries.append((units[-1], -20.0, 1000.0, 1020.0))  # credit

    result = await _call(_make_mock_db(units, entries))

    uc = result["unit_counts"]
    assert uc["canonical_unit_count"] == 87
    assert uc["paid_up"] + uc["owing"] + uc["credit"] == 87
    assert uc["paid_up"] == 85
    assert uc["owing"] == 1
    assert uc["credit"] == 1
    assert uc["duplicate_ledger_rows"] == 0
    assert uc["missing_ledger"] == 0


@pytest.mark.asyncio
async def test_portal_mismatch_requires_reconciliation_without_altering_ledger_arrears():
    """spec test 4: portal arrears differing from ledger arrears must set
    requires_reconciliation=True and must NOT change the ledger-derived
    arrears figure."""
    units = _east_gate_units()
    entries = [(u, 0.0, 1000.0, 1000.0) for u in units[:-1]]
    entries.append((units[-1], 200.0, 1000.0, 800.0))  # one unit owing $200
    portal_doc = {"arrears_total": 9999.99, "updated_at": "2026-06-01T00:00:00Z"}

    result = await _call(_make_mock_db(units, entries, portal_doc=portal_doc))

    pcc = result["portal_cross_check"]
    assert pcc["available"] is True
    assert pcc["requires_reconciliation"] is True
    assert pcc["ledger_arrears_total"] == 200.0
    assert result["arrears_metrics"]["true_arrears_amount"] == 200.0
    assert any("reconciliation" in w.lower() for w in result["warnings"])


@pytest.mark.asyncio
async def test_no_portal_doc_does_not_require_reconciliation():
    units = _east_gate_units()
    entries = [(u, 0.0, 1000.0, 1000.0) for u in units]

    result = await _call(_make_mock_db(units, entries, portal_doc=None))

    assert result["portal_cross_check"]["available"] is False
    assert result["portal_cross_check"]["requires_reconciliation"] is False


@pytest.mark.asyncio
async def test_kpi_contract_requires_can_view_finances_permission():
    """RBAC: a user without can_view_finances must get 403, not a data leak
    or a silent empty response — mirrors the guard on /finance/summary."""
    units = _east_gate_units()
    entries = [(u, 0.0, 1000.0, 1000.0) for u in units]
    mock_db = _make_mock_db(units, entries)

    from routers.finance import get_finance_kpi_contract
    settings_doc = {
        "grace_period_days": 14,
        "levy_due_months": [3, 6, 9, 12],
        "levy_due_day_type": "first",
    }
    with patch("routers.finance.db", mock_db), \
         patch("utils.finance_helpers.db", mock_db), \
         patch("routers.finance._get_general_settings", AsyncMock(return_value=settings_doc)), \
         patch("routers.finance.get_user_permissions",
               return_value=Permission(can_view_finances=False)):
        with pytest.raises(HTTPException) as exc_info:
            await get_finance_kpi_contract(
                year="2025",
                current_user={"id": "u1", "building_id": "13195", "role": "owner"},
                building_id="13195",
            )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_ledger_quality_queries_scoped_to_calling_building_id():
    """Multi-tenant isolation (unit-test level): get_ledger_quality must
    filter both `units` and `unit_levy_ledger` by the caller's building_id —
    never a different building's data, and never unscoped."""
    from utils.finance_helpers import get_ledger_quality

    mock_db = MagicMock()
    mock_db.units.find = MagicMock(return_value=_cursor([{"unit_number": "TH001"}]))
    mock_db.unit_levy_ledger.find = MagicMock(
        return_value=_cursor([{"unit_number": "TH001", "net_balance": 0.0}])
    )

    with patch("utils.finance_helpers.db", mock_db):
        await get_ledger_quality(year="2025", building_id="16244")

    units_filter = mock_db.units.find.call_args[0][0]
    ledger_filter = mock_db.unit_levy_ledger.find.call_args[0][0]
    assert units_filter["building_id"] == "16244"
    assert ledger_filter["building_id"] == "16244"
    assert ledger_filter["year"] == "2025"


@pytest.mark.asyncio
async def test_east_gate_kpi_contract_reports_87_canonical_units():
    """East Gate sanity: a fully consistent 87-unit ledger must never report
    a canonical_unit_count above 87."""
    units = _east_gate_units()
    entries = [(u, 0.0, 1000.0, 1000.0) for u in units]

    result = await _call(_make_mock_db(units, entries))

    assert result["unit_counts"]["canonical_unit_count"] == 87
    assert result["ledger_quality"]["is_unit_count_consistent"] is True
    assert result["source_contract_version"] == "ledger-kpi-v1"
