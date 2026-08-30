"""
tests/backend/test_historical_levy_reconstruction_service.py

GAP-FIN-015 criterion 6: historical levy-payment reconstruction must be
building-agnostic (no hardcoded East Gate assumptions) and idempotent (a
second run against the same data must not create duplicate
demo_bank_transactions). GAP-FIN-015 criterion 8: multi-tenant isolation must
hold — one building's reconstruction run must never read or write another
building's data.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from services.historical_levy_reconstruction_service import (
    synthesize_historical_levy_payments,
)

BUILDING_A = "13195"
BUILDING_B = "16244"

_LEVY_DOC = {
    "year": 2022,
    "admin_levy_per_uoe_quarterly": 10.0,
    "sinking_levy_per_uoe_quarterly": 2.0,
    "payment_schedule": [
        {"quarter": "Q1", "due_date": "2022-01-01"},
        {"quarter": "Q2", "due_date": "2022-04-01"},
        {"quarter": "Q3", "due_date": "2022-07-01"},
        {"quarter": "Q4", "due_date": "2022-10-01"},
    ],
}

_UNITS = [
    {"unit_number": "U1", "entitlement": 100.0},
    {"unit_number": "U2", "entitlement": 50.0},
]


def _cursor(items):
    cur = MagicMock()
    cur.to_list = AsyncMock(return_value=items)
    return cur


def _make_db(*, building_id: str, levy_docs=None, units=None):
    db = MagicMock()
    levy_docs = levy_docs if levy_docs is not None else [dict(_LEVY_DOC)]
    units = units if units is not None else [dict(u) for u in _UNITS]

    def _annual_levies_find(query, *args, **kwargs):
        assert query["building_id"] == building_id
        return _cursor(levy_docs)

    def _units_find(query, *args, **kwargs):
        assert query["building_id"] == building_id
        return _cursor(units)

    db.annual_levies.find = MagicMock(side_effect=_annual_levies_find)
    db.units.find = MagicMock(side_effect=_units_find)

    upserted = {}

    async def _update_one(filt, update, upsert=False):
        key = (filt["building_id"], filt["idempotency_key"])
        result = MagicMock()
        if key in upserted:
            result.upserted_id = None
        else:
            upserted[key] = update["$setOnInsert"]
            result.upserted_id = "new"
        return result

    db.demo_bank_transactions.update_one = AsyncMock(side_effect=_update_one)
    db._upserted = upserted
    return db


@pytest.mark.asyncio
async def test_synthesizes_one_admin_and_sinking_row_per_unit_per_quarter():
    db = _make_db(building_id=BUILDING_A)

    result = await synthesize_historical_levy_payments(
        db, building_id=BUILDING_A, from_year=2022, to_year=2022,
    )

    # 2 units x 4 quarters x 2 funds (admin + sinking) = 16 rows
    assert result["inserted"] == 16
    assert result["already_existed"] == 0
    assert result["units_processed"] == 2
    assert result["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_idempotent_rerun_produces_no_duplicate_transactions():
    db = _make_db(building_id=BUILDING_A)

    first = await synthesize_historical_levy_payments(
        db, building_id=BUILDING_A, from_year=2022, to_year=2022,
    )
    second = await synthesize_historical_levy_payments(
        db, building_id=BUILDING_A, from_year=2022, to_year=2022,
    )

    assert first["inserted"] == 16
    assert second["inserted"] == 0
    assert second["already_existed"] == 16
    # No more distinct documents were ever staged than the first run created.
    assert len(db._upserted) == 16


@pytest.mark.asyncio
async def test_building_agnostic_a_non_east_gate_building_id_works_identically():
    """No hardcoded '13195' fallback anywhere in the reconstruction path — a
    different building_id must produce the same shape of result."""
    db = _make_db(building_id=BUILDING_B)

    result = await synthesize_historical_levy_payments(
        db, building_id=BUILDING_B, from_year=2022, to_year=2022,
    )

    assert result["building_id"] == BUILDING_B
    assert result["inserted"] == 16
    for doc in db._upserted.values():
        assert doc["building_id"] == BUILDING_B


@pytest.mark.asyncio
async def test_multi_tenant_isolation_building_b_run_never_touches_building_a_data():
    """Simulates two buildings' independent onboarding runs against a single
    shared mock db — building B's run must only ever query/write its own
    building_id, confirmed by the query-time assertions inside _make_db."""
    db = _make_db(building_id=BUILDING_B)

    await synthesize_historical_levy_payments(
        db, building_id=BUILDING_B, from_year=2022, to_year=2022,
    )

    # Every staged transaction belongs to building B only.
    assert db._upserted, "expected at least one staged transaction"
    assert all(doc["building_id"] == BUILDING_B for doc in db._upserted.values())
    assert not any(doc["building_id"] == BUILDING_A for doc in db._upserted.values())


@pytest.mark.asyncio
async def test_zero_rate_year_skips_that_funds_transactions_not_the_whole_year():
    """2021-style year: sinking rate is legitimately zero (combined levy, no
    fund split yet) — admin rows must still be created, sinking must not."""
    levy_doc = dict(_LEVY_DOC)
    levy_doc["sinking_levy_per_uoe_quarterly"] = 0.0
    db = _make_db(building_id=BUILDING_A, levy_docs=[levy_doc])

    result = await synthesize_historical_levy_payments(
        db, building_id=BUILDING_A, from_year=2022, to_year=2022,
    )

    # 2 units x 4 quarters x 1 fund (admin only) = 8 rows
    assert result["inserted"] == 8
    assert all(
        "Admin Fund" in doc["description"] for doc in db._upserted.values()
    )


@pytest.mark.asyncio
async def test_mixed_half_yearly_and_quarterly_schedule_uses_period_fractions():
    """A building can change levy frequency mid-year. The reconstruction must
    use each schedule entry's period_fraction instead of assuming four equal
    quarters for every historical year."""
    levy_doc = {
        "year": 2022,
        "admin_levy_per_uoe_annual": 20.0,
        "sinking_levy_per_uoe_annual": 4.0,
        "payment_schedule": [
            {"period": "H1", "due_date": "2022-06-30", "period_fraction": 0.5},
            {"period": "Q3", "due_date": "2022-09-30", "period_fraction": 0.25},
            {"period": "Q4", "due_date": "2022-12-31", "period_fraction": 0.25},
        ],
    }
    db = _make_db(building_id=BUILDING_A, levy_docs=[levy_doc], units=[{"unit_number": "U1", "entitlement": 100.0}])

    result = await synthesize_historical_levy_payments(
        db, building_id=BUILDING_A, from_year=2022, to_year=2022,
    )

    assert result["inserted"] == 6  # 1 unit x 3 instalments x 2 funds
    docs = list(db._upserted.values())
    h1_admin = next(d for d in docs if "H1 2022 Admin Fund" in d["description"])
    q3_admin = next(d for d in docs if "Q3 2022 Admin Fund" in d["description"])
    h1_sinking = next(d for d in docs if "H1 2022 Sinking Fund" in d["description"])
    q3_sinking = next(d for d in docs if "Q3 2022 Sinking Fund" in d["description"])
    assert h1_admin["amount_cents"] == 100_000
    assert q3_admin["amount_cents"] == 50_000
    assert h1_sinking["amount_cents"] == 20_000
    assert q3_sinking["amount_cents"] == 10_000


@pytest.mark.asyncio
async def test_explicit_zero_period_rate_is_not_replaced_by_annual_rate():
    """A schedule entry can intentionally waive one fund for a period. Explicit
    0.0 must be preserved instead of falling through to annual-rate derivation."""
    levy_doc = {
        "year": 2022,
        "admin_levy_per_uoe_annual": 20.0,
        "sinking_levy_per_uoe_annual": 4.0,
        "payment_schedule": [
            {
                "period": "H1",
                "due_date": "2022-06-30",
                "period_fraction": 0.5,
                "admin_levy_per_uoe": 0.0,
            },
        ],
    }
    db = _make_db(building_id=BUILDING_A, levy_docs=[levy_doc], units=[{"unit_number": "U1", "entitlement": 100.0}])

    result = await synthesize_historical_levy_payments(
        db, building_id=BUILDING_A, from_year=2022, to_year=2022,
    )

    assert result["inserted"] == 1
    docs = list(db._upserted.values())
    assert len(docs) == 1
    assert "Sinking Fund" in docs[0]["description"]
    assert docs[0]["amount_cents"] == 20_000


@pytest.mark.asyncio
async def test_no_annual_levies_raises_value_error():
    db = _make_db(building_id=BUILDING_A, levy_docs=[])

    with pytest.raises(ValueError, match="annual_levies"):
        await synthesize_historical_levy_payments(
            db, building_id=BUILDING_A, from_year=2022, to_year=2022,
        )


@pytest.mark.asyncio
async def test_no_units_raises_value_error():
    db = _make_db(building_id=BUILDING_A, units=[])

    with pytest.raises(ValueError, match="[Uu]nits"):
        await synthesize_historical_levy_payments(
            db, building_id=BUILDING_A, from_year=2022, to_year=2022,
        )


@pytest.mark.asyncio
async def test_dry_run_does_not_write_to_demo_bank_transactions():
    db = _make_db(building_id=BUILDING_A)

    result = await synthesize_historical_levy_payments(
        db, building_id=BUILDING_A, from_year=2022, to_year=2022, dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["inserted"] == 16  # reported as "would insert"
    db.demo_bank_transactions.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_future_quarters_are_excluded():
    """A year entirely in the future (no due_date <= today) must be skipped,
    not raise or synthesize impossible future payments."""
    future_year = date.today().year + 5
    levy_doc = {
        "year": future_year,
        "admin_levy_per_uoe_quarterly": 10.0,
        "sinking_levy_per_uoe_quarterly": 2.0,
        "payment_schedule": [
            {"quarter": "Q1", "due_date": f"{future_year}-01-01"},
        ],
    }
    db = _make_db(building_id=BUILDING_A, levy_docs=[levy_doc])

    result = await synthesize_historical_levy_payments(
        db, building_id=BUILDING_A, from_year=future_year, to_year=future_year,
    )

    assert result["inserted"] == 0
    assert result["years_skipped"] == 1
