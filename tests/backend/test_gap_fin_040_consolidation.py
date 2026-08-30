"""
tests/backend/test_gap_fin_040_consolidation.py

GAP-FIN-040 contract tests for the canonical arrears helpers. These lock the
invariant that every consumer which migrated onto the shared helpers agrees by
construction:

  - the per-unit map (get_unit_arrears_map) and the building aggregate
    (get_arrears_metrics) derive from ONE loop, so a per-unit arrears_flag can
    never diverge from the building count;
  - a unit in credit is NEVER netted against another unit's arrears;
  - the still-in-grace slice of the current period is excluded from arrears.

Run standalone (no conftest / no server import needed):
    backend/venv/bin/python -m pytest tests/backend/test_gap_fin_040_consolidation.py -q --noconftest
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

# Minimal env so `from database import db` (imported transitively) does not raise
# at module import — the client is lazy and never actually connects here.
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
# DB_NAME is not set here: conftest selects the test database before
# backend.database is imported, which is the only moment it can be redirected.
# The setdefault that used to sit on this line was a no-op — backend/.env had
# already put strataos_production in the environment (GAP-TEST-001).
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

from utils import finance_helpers as fh  # noqa: E402


def _fake_ledger_db(ledger_rows, settings_doc=None, years=("2026",)):
    """Build a fake `db` whose unit_levy_ledger.find returns `ledger_rows`."""
    db = MagicMock()

    def _find(_filter, _proj=None):
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=list(ledger_rows))
        return cursor

    db.unit_levy_ledger.find = MagicMock(side_effect=_find)
    db.settings.find_one = AsyncMock(return_value=settings_doc)
    db.annual_levies.distinct = AsyncMock(return_value=list(years))
    return db


# 4 units: one arrears, one credit, one paid-up, one bigger arrears.
_ROWS = [
    {"unit_number": "UA01", "net_balance": 100.0, "total_levied": 400.0, "quarters_charged": 4},
    {"unit_number": "UA02", "net_balance": -50.0, "total_levied": 400.0, "quarters_charged": 4},
    {"unit_number": "UA03", "net_balance": 0.0, "total_levied": 400.0, "quarters_charged": 4},
    {"unit_number": "UA04", "net_balance": 200.0, "total_levied": 800.0, "quarters_charged": 4},
]


def test_compute_grace_period_counts_past_and_future_years():
    past = fh.compute_grace_period_counts(
        settings_doc={"grace_period_days": 14, "levy_due_months": [3, 6, 9, 12],
                      "levy_due_day_type": "last", "financial_year_start_month": 1},
        levy_year_int=2020,
    )
    assert past["num_overdue"] == 4 and past["in_grace_count"] == 0
    future = fh.compute_grace_period_counts(settings_doc=None, levy_year_int=2099)
    assert future["num_overdue"] == 0 and future["in_grace_count"] == 0


@pytest.mark.asyncio
async def test_arrears_metrics_no_grace(monkeypatch):
    monkeypatch.setattr(fh, "db", _fake_ledger_db(_ROWS))
    res = await fh.get_arrears_metrics("2026", num_overdue_periods=4, building_id="13195",
                                       total_periods=4, in_grace_periods=0)
    # UA01 ($100) + UA04 ($200) = 2 units, $300; UA02 is a $50 credit (never netted).
    assert res["unit_count"] == 2
    assert res["total_amount"] == 300.0
    assert res["credit_unit_count"] == 1
    assert res["total_credit_amount"] == 50.0


@pytest.mark.asyncio
async def test_credit_never_nets_against_arrears(monkeypatch):
    # A large credit unit must not reduce the building's arrears total.
    rows = [
        {"unit_number": "A", "net_balance": 100.0, "total_levied": 400.0, "quarters_charged": 4},
        {"unit_number": "B", "net_balance": -10000.0, "total_levied": 400.0, "quarters_charged": 4},
    ]
    monkeypatch.setattr(fh, "db", _fake_ledger_db(rows))
    res = await fh.get_arrears_metrics("2026", 4, "13195", 4, in_grace_periods=0)
    assert res["unit_count"] == 1 and res["total_amount"] == 100.0  # not netted to 0


@pytest.mark.asyncio
async def test_in_grace_portion_excluded(monkeypatch):
    # UA04: net_balance 200, per_period = 800/4 = 200. With 1 in-grace period the
    # whole 200 is in grace -> arrears 0. UA01: net 100 < per_period 100 -> in grace -> 0.
    monkeypatch.setattr(fh, "db", _fake_ledger_db(_ROWS))
    res = await fh.get_arrears_metrics("2026", 3, "13195", 4, in_grace_periods=1)
    assert res["unit_count"] == 0
    assert res["total_amount"] == 0.0


@pytest.mark.asyncio
async def test_unit_map_agrees_with_building_aggregate(monkeypatch):
    """The per-unit map and the building aggregate must agree by construction."""
    monkeypatch.setattr(fh, "db", _fake_ledger_db(_ROWS, settings_doc=None))
    unit_map = await fh.get_unit_arrears_map("13195", "2026")
    agg = await fh.get_arrears_metrics("2026", 4, "13195", 4, in_grace_periods=0)

    map_arrears_units = sum(1 for r in unit_map.values() if r["in_arrears"])
    map_arrears_total = round(sum(r["arrears"] for r in unit_map.values() if r["in_arrears"]), 2)
    assert map_arrears_units == agg["unit_count"]
    assert map_arrears_total == agg["total_amount"]
    # per-unit flags are correct and never netted
    assert unit_map["UA01"]["in_arrears"] is True
    assert unit_map["UA02"]["in_credit"] is True and unit_map["UA02"]["credit"] == 50.0
    assert unit_map["UA03"]["in_arrears"] is False


@pytest.mark.asyncio
async def test_building_arrears_summary_uses_grace_schedule(monkeypatch):
    # Past year (2020) via settings-derived schedule -> all overdue, no grace ->
    # both arrears units surface.
    monkeypatch.setattr(
        fh, "db",
        _fake_ledger_db(
            _ROWS,
            settings_doc={"grace_period_days": 14, "levy_due_months": [3, 6, 9, 12],
                          "levy_due_day_type": "last", "financial_year_start_month": 1},
            years=("2020",),
        ),
    )
    summary = await fh.get_building_arrears_summary("13195", "2020")
    assert summary["units_in_arrears"] == 2
    assert summary["true_arrears_amount"] == 300.0
    assert summary["total_credit_amount"] == 50.0
    assert summary["num_overdue"] == 4
