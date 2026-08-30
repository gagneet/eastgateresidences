"""
tests/backend/test_gap_fin_040_security.py

GAP-FIN-040 security tests — the consolidation routes every finance surface
through a handful of canonical helpers, so those helpers are now the single
choke point for two security-critical invariants:

  1. MULTI-TENANT ISOLATION — every ledger read is scoped by building_id; a
     helper called for building A must never read building B's rows. A regression
     here is a cross-tenant financial-data leak (the master partition rule).
  2. NO CROSS-UNIT NETTING — one unit's credit can never reduce another unit's
     arrears (a per-unit obligation is a legal/financial invariant, and netting
     would also let a crafted credit mask a real debtor).

Plus a static guard that the migrated BI/finance read endpoints remain building-
scoped (never a bare cross-tenant query).

Run standalone:
    backend/venv/bin/python -m pytest tests/backend/test_gap_fin_040_security.py -q --noconftest
"""
from __future__ import annotations

import os
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
# DB_NAME is not set here: conftest selects the test database before
# backend.database is imported, which is the only moment it can be redirected.
# The setdefault that used to sit on this line was a no-op — backend/.env had
# already put strataos_production in the environment (GAP-TEST-001).
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

from utils import finance_helpers as fh  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BACKEND_ROOT = os.path.join(_REPO_ROOT, "backend")


def _tenant_scoped_db(rows_by_building: dict[str, list[dict]]):
    """Fake db whose unit_levy_ledger.find honours the building_id in the filter,
    so a query for building A only ever returns A's rows."""
    captured_filters: list[dict] = []
    db = MagicMock()

    def _find(_filter, _proj=None):
        captured_filters.append(_filter)
        bid = _filter.get("building_id")
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=list(rows_by_building.get(bid, [])))
        return cursor

    db.unit_levy_ledger.find = MagicMock(side_effect=_find)
    db.settings.find_one = AsyncMock(return_value=None)
    db.annual_levies.distinct = AsyncMock(return_value=["2026"])
    db._captured_filters = captured_filters
    return db


_ROWS_BY_BUILDING = {
    "13195": [  # building A: one real debtor
        {"unit_number": "A1", "net_balance": 500.0, "total_levied": 2000.0, "quarters_charged": 4},
    ],
    "16244": [  # building B: a huge credit that must NOT leak into A's numbers
        {"unit_number": "B1", "net_balance": -99999.0, "total_levied": 2000.0, "quarters_charged": 4},
    ],
}


@pytest.mark.asyncio
async def test_arrears_metrics_are_building_scoped(monkeypatch):
    db = _tenant_scoped_db(_ROWS_BY_BUILDING)
    monkeypatch.setattr(fh, "db", db)

    a = await fh.get_arrears_metrics("2026", 4, "13195", 4, in_grace_periods=0)
    # Building A sees only its own debtor — B's giant credit never leaks in.
    assert a["unit_count"] == 1 and a["total_amount"] == 500.0
    assert a["total_credit_amount"] == 0.0

    # Every ledger read carried building_id == "13195".
    assert db._captured_filters, "no ledger query captured"
    assert all(f.get("building_id") == "13195" for f in db._captured_filters)


@pytest.mark.asyncio
async def test_unit_map_is_building_scoped(monkeypatch):
    db = _tenant_scoped_db(_ROWS_BY_BUILDING)
    monkeypatch.setattr(fh, "db", db)
    m = await fh.get_unit_arrears_map("16244", "2026")
    assert set(m.keys()) == {"B1"}  # only building B's unit
    assert all(f.get("building_id") == "16244" for f in db._captured_filters)


@pytest.mark.asyncio
async def test_credit_in_other_building_cannot_offset_arrears(monkeypatch):
    """Cross-tenant + cross-unit netting guard: building B's -99999 credit must not
    reduce building A's $500 arrears."""
    db = _tenant_scoped_db(_ROWS_BY_BUILDING)
    monkeypatch.setattr(fh, "db", db)
    a = await fh.get_building_arrears_summary("13195", "2026")
    assert a["true_arrears_amount"] == 500.0
    assert a["units_in_arrears"] == 1


@pytest.mark.asyncio
async def test_collection_rate_is_building_scoped(monkeypatch):
    db = _tenant_scoped_db(_ROWS_BY_BUILDING)
    monkeypatch.setattr(fh, "db", db)
    await fh.get_collection_rate_metrics("2026", "13195")
    assert db._captured_filters
    assert all(f.get("building_id") == "13195" for f in db._captured_filters)


def test_migrated_bi_reads_are_building_scoped():
    """Static guard: the migrated BI Mongo reads must always filter by building_id
    (no bare cross-tenant unit_levy_ledger scan)."""
    src = open(os.path.join(_BACKEND_ROOT, "services/bi_service.py"), encoding="utf-8").read()
    # Every unit_levy_ledger.find( in bi_service must include building_id in the same call.
    for m in re.finditer(r"unit_levy_ledger\.find(_one)?\(\s*(\{.*?\})", src, re.DOTALL):
        query = m.group(2)
        assert "building_id" in query, (
            "bi_service.py has a unit_levy_ledger read without building_id — "
            "cross-tenant leak risk:\n" + query[:200]
        )
