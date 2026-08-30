from datetime import datetime
from types import SimpleNamespace

import pytest


class _FakeCursor:
    def __init__(self, docs):
        self.docs = docs
        self.to_list_limits = []

    def sort(self, *_args):
        return self

    async def to_list(self, limit):
        self.to_list_limits.append(limit)
        return list(self.docs)


class _FakeCollection:
    def __init__(self, docs):
        self.cursor = _FakeCursor(docs)

    def find(self, *_args, **_kwargs):
        return self.cursor


@pytest.mark.asyncio
async def test_unit_levy_ledger_fetches_all_rows_without_hard_200_cap(monkeypatch):
    """The all-unit ledger endpoint must not silently truncate large schemes.

    GET /unit-levy-ledger has no public pagination parameter. If the route uses
    Motor's to_list(200), schemes above 200 lots lose rows even though the API
    response still looks successful. The route should request the complete
    building/year result set and leave pagination to a future explicit contract.
    """
    from routers import finance

    entries = [
        {
            "building_id": "BIG-001",
            "year": "2026",
            "unit_number": f"{idx:03d}",
            "total_levied": 100.0,
        }
        for idx in range(1, 206)
    ]
    units = [
        {
            "unit_number": f"{idx:03d}",
            "owner_name": f"Owner {idx}",
            "owner_name_b": None,
        }
        for idx in range(1, 206)
    ]
    fake_db = SimpleNamespace(
        unit_levy_ledger=_FakeCollection(entries),
        units=_FakeCollection(units),
        user_units=_FakeCollection([]),
        users=_FakeCollection([]),
    )

    monkeypatch.setattr(finance, "db", fake_db)
    monkeypatch.setattr(
        finance,
        "get_user_permissions",
        lambda _user: SimpleNamespace(can_view_finances=True),
    )

    result = await finance.get_unit_levy_ledger(
        year="2026",
        current_user={"id": "sa-1", "role": "super_admin"},
        building_id="BIG-001",
    )

    assert len(result) == 205
    assert fake_db.unit_levy_ledger.cursor.to_list_limits == [None]
    assert fake_db.units.cursor.to_list_limits == [None]


@pytest.mark.asyncio
async def test_unit_levy_ledger_coerces_real_datetime_created_at_to_string(monkeypatch):
    """GAP-FIN-030 Fix 4 regression: UnitLevyLedgerResponse declares created_at/
    updated_at as str, but some real documents (confirmed live 2026-08-02, older/
    reconstructed rows) store an actual datetime instead of an ISO string --
    Pydantic v2 does not auto-coerce datetime -> str for a str-typed field, so a
    single such row previously 500'd the ENTIRE endpoint response instead of
    returning the other units' data (a likely real contributor to the reported
    "Levy Status tab shows no ledger data" symptom -- a swallowed 500, not a
    genuine empty state)."""
    from routers import finance

    entries = [
        {
            "building_id": "13195",
            "year": "2026",
            "unit_number": "TH071",
            "total_levied": 3523.0,
            "created_at": datetime(2026, 5, 4, 12, 18, 17),
            "updated_at": datetime(2026, 7, 31, 12, 30, 2),
        },
    ]
    fake_db = SimpleNamespace(
        unit_levy_ledger=_FakeCollection(entries),
        units=_FakeCollection([]),
        user_units=_FakeCollection([]),
        users=_FakeCollection([]),
    )

    monkeypatch.setattr(finance, "db", fake_db)
    monkeypatch.setattr(
        finance,
        "get_user_permissions",
        lambda _user: SimpleNamespace(can_view_finances=True),
    )

    result = await finance.get_unit_levy_ledger(
        year="2026",
        current_user={"id": "sa-1", "role": "super_admin"},
        building_id="13195",
    )

    assert len(result) == 1
    assert result[0].created_at == "2026-05-04T12:18:17"
    assert result[0].updated_at == "2026-07-31T12:30:02"
