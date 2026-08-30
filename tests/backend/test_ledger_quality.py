"""
GAP-FIN-014 — get_ledger_quality() reconciles unit_levy_ledger rows for a year
against the canonical `units` collection, so a duplicate or orphaned ledger
row can never inflate/shrink the operational unit denominator (the East Gate
"59 of 156 units" symptom, where the building actually has 87 lots).

These are pure unit tests against the helper directly — no HTTP layer.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _cursor(rows):
    c = MagicMock()
    c.to_list = AsyncMock(return_value=rows)
    return c


def _units(unit_numbers):
    return [{"unit_number": u} for u in unit_numbers]


def _ledger_rows(entries):
    """entries: list of (unit_number, net_balance) tuples."""
    return [{"unit_number": u, "net_balance": nb} for u, nb in entries]


def _make_mock_db(unit_numbers, ledger_entries):
    mock_db = MagicMock()
    mock_db.units.find = MagicMock(return_value=_cursor(_units(unit_numbers)))
    mock_db.unit_levy_ledger.find = MagicMock(return_value=_cursor(_ledger_rows(ledger_entries)))
    return mock_db


async def _call(mock_db, year="2026", building_id="13195"):
    from utils.finance_helpers import get_ledger_quality
    with patch("utils.finance_helpers.db", mock_db):
        return await get_ledger_quality(year=year, building_id=building_id)


def _east_gate_units():
    return [f"TH{n:03d}" for n in range(1, 88)]  # 87 canonical units, TH001..TH087


@pytest.mark.asyncio
async def test_duplicate_ledger_row_does_not_inflate_denominator():
    """87 canonical units + 88 ledger rows (one unit duplicated via a
    lower-cased/whitespace-padded token) must still report
    canonical_unit_count=87 and flag the duplicate, not silently count 88
    distinct units. normalise_unit_token() only normalises case/whitespace,
    not cross-prefix formats, so the duplicate here is a same-lot token
    that differs only in case/whitespace (the case this bulk reconciliation
    is designed to catch cheaply, as opposed to the per-row expensive
    prefix-canonicalisation done by resolve_canonical_unit_number)."""
    units = _east_gate_units()
    entries = [(u, 0.0) for u in units]
    # Duplicate TH087 under a case/whitespace-variant of the same token.
    entries.append((" th087 ", 0.0))

    result = await _call(_make_mock_db(units, entries))

    assert result["canonical_unit_count"] == 87
    assert result["ledger_row_count"] == 88
    assert result["distinct_ledger_unit_count"] == 87
    assert result["duplicate_ledger_units"] == ["TH087"]
    assert result["duplicate_ledger_row_count"] == 1
    assert result["missing_ledger_units"] == []
    assert result["extra_ledger_units"] == []
    assert result["malformed_ledger_row_count"] == 0
    assert result["is_unit_count_consistent"] is False


@pytest.mark.asyncio
async def test_duplicate_ledger_row_count_counts_redundant_rows_not_units():
    """A single unit duplicated 3x must report duplicate_ledger_units == 1
    unit but duplicate_ledger_row_count == 2 (the redundant rows beyond the
    first) — these are different semantics and must not be conflated."""
    units = _east_gate_units()
    entries = [(u, 0.0) for u in units]
    entries.append(("TH087", 0.0))
    entries.append(("TH087", 0.0))

    result = await _call(_make_mock_db(units, entries))

    assert result["duplicate_ledger_units"] == ["TH087"]
    assert result["duplicate_ledger_row_count"] == 2


@pytest.mark.asyncio
async def test_malformed_ledger_row_skipped_not_raised():
    """A ledger row with a null/missing unit_number must be excluded from
    reconciliation (and counted) rather than propagating None into
    extra_ledger_units, which would raise a TypeError in the ", ".join(...)
    warning formatter downstream."""
    units = _east_gate_units()
    entries = [(u, 0.0) for u in units]
    entries.append((None, 0.0))

    result = await _call(_make_mock_db(units, entries))

    assert result["extra_ledger_units"] == []
    assert result["malformed_ledger_row_count"] == 1
    assert result["is_unit_count_consistent"] is False


@pytest.mark.asyncio
async def test_missing_ledger_row_reported():
    """87 canonical units but only 86 ledger rows must report the missing
    unit and flag inconsistency, not silently show a denominator of 86."""
    units = _east_gate_units()
    entries = [(u, 0.0) for u in units[:-1]]  # drop TH087

    result = await _call(_make_mock_db(units, entries))

    assert result["canonical_unit_count"] == 87
    assert result["ledger_row_count"] == 86
    assert result["distinct_ledger_unit_count"] == 86
    assert result["missing_ledger_units"] == ["TH087"]
    assert result["duplicate_ledger_units"] == []
    assert result["extra_ledger_units"] == []
    assert result["is_unit_count_consistent"] is False


@pytest.mark.asyncio
async def test_extra_ledger_row_excluded_from_canonical_count():
    """A ledger row referencing a unit_number absent from `units` must be
    reported as extra and never counted toward canonical_unit_count."""
    units = _east_gate_units()
    entries = [(u, 0.0) for u in units]
    entries.append(("TH999", 50.0))  # not a real unit

    result = await _call(_make_mock_db(units, entries))

    assert result["canonical_unit_count"] == 87
    assert result["ledger_row_count"] == 88
    assert result["distinct_ledger_unit_count"] == 87
    assert result["extra_ledger_units"] == ["TH999"]
    assert result["missing_ledger_units"] == []
    assert result["is_unit_count_consistent"] is False


@pytest.mark.asyncio
async def test_east_gate_consistent_ledger_reports_87():
    """East Gate sanity: 87 canonical units with exactly 87 matching ledger
    rows must report a fully consistent contract."""
    units = _east_gate_units()
    entries = [(u, 0.0) for u in units]

    result = await _call(_make_mock_db(units, entries))

    assert result["canonical_unit_count"] == 87
    assert result["ledger_row_count"] == 87
    assert result["distinct_ledger_unit_count"] == 87
    assert result["duplicate_ledger_units"] == []
    assert result["missing_ledger_units"] == []
    assert result["extra_ledger_units"] == []
    assert result["is_unit_count_consistent"] is True
    assert result["source"] == "units + unit_levy_ledger"


@pytest.mark.asyncio
async def test_canonical_status_counts_classified_by_net_balance():
    units = ["TH001", "TH002", "TH003"]
    entries = [("TH001", 100.0), ("TH002", -50.0), ("TH003", 0.0)]

    result = await _call(_make_mock_db(units, entries))

    assert result["canonical_status_counts"] == {"paid_up": 1, "owing": 1, "credit": 1}
