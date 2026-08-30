"""GAP-ONBOARD-003 — PortalAdapter seam: cross-provider genericity + registry selection.

Proves the core genericity claim: a non-Civium portal export (CsvPortalAdapter) produces the
SAME typed evidence (canonical PortalBalanceSnapshot / staging document) as the Civium scrape
adapter for the same balances — so any building can onboard portal/current-balance evidence
through the generic pipeline, not just Civium-shaped ones.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.portal_adapters import (
    CIVIUM_PORTAL,
    CSV_PORTAL,
    CiviumPortalAdapter,
    CsvPortalAdapter,
    PortalBalanceSnapshot,
    cents,
)
from integrations.registry import ProviderRegistry


SNAPSHOT_DATE = "2026-08-06"

# The same three lots + fund position, expressed once in Civium's raw Mongo shape and once in a
# provider-neutral CSV export shape.
_LOTS = [
    {"lot": "1", "balance": 254.98, "status": "current", "owner_name": "Alice"},
    {"lot": "2", "balance": 0, "status": "current", "owner": "Bob"},
    {"lot": "3", "balance": 1000.00, "status": "arrears", "owner_name": "Carol"},
]
_CSV_ROWS = [
    {"lot_number": "1", "balance": 254.98, "status": "current", "owner_name": "Alice"},
    {"lot_number": "2", "balance": 0, "status": "current", "owner_name": "Bob"},
    {"lot_number": "3", "balance": 1000.00, "status": "arrears", "owner_name": "Carol"},
]
_SUMMARY = {
    "admin_fund_balance_cents": 1_000_000,
    "sinking_fund_balance_cents": 2_000_000,
    "arrears_cents": 50_000,
    "credit_total": 100.00,
    "collection_rate": 0.95,
    "updated_at": "2026-08-06T00:00:00Z",
}


def _civium_db_mock():
    mock_db = MagicMock()
    mock_db._db.bank_accounts.find_one = AsyncMock(return_value=None)
    mock_db._db.building_summaries.find_one = AsyncMock(return_value=dict(_SUMMARY))
    owners_cursor = MagicMock()
    owners_cursor.sort.return_value = owners_cursor
    owners_cursor.to_list = AsyncMock(return_value=[dict(o) for o in _LOTS])
    mock_db._db.strata_owners.find.return_value = owners_cursor
    return mock_db


async def _build_civium():
    with patch("integrations.portal_adapters.db", _civium_db_mock()):
        return await CiviumPortalAdapter().build_snapshot("13195", "2026", SNAPSHOT_DATE)


async def _build_csv():
    adapter = CsvPortalAdapter(
        rows=_CSV_ROWS,
        admin_fund_balance=10_000.00,     # -> 1_000_000 cents, matches the Civium summary
        sinking_fund_balance=20_000.00,   # -> 2_000_000 cents
        arrears_total=500.00,             # -> 50_000 cents, matches arrears_cents
        credit_total=100.00,
        collection_rate=0.95,
    )
    return await adapter.build_snapshot("13195", "2026", SNAPSHOT_DATE)


@pytest.mark.asyncio
async def test_both_adapters_produce_identical_typed_evidence():
    civ = await _build_civium()
    csv = await _build_csv()

    assert isinstance(civ, PortalBalanceSnapshot) and isinstance(csv, PortalBalanceSnapshot)
    # Per-lot balances identical (Pydantic models compare by value).
    assert civ.per_unit_balances == csv.per_unit_balances
    assert [u.balance_cents for u in civ.per_unit_balances] == [25498, 0, 100000]
    # Fund + arrears + credit + collection-rate all identical.
    assert civ.raw_admin_fund_balance_cents == csv.raw_admin_fund_balance_cents == 1_000_000
    assert civ.raw_sinking_fund_balance_cents == csv.raw_sinking_fund_balance_cents == 2_000_000
    assert civ.raw_arrears_total_cents == csv.raw_arrears_total_cents == 50_000
    assert civ.raw_credit_total_cents == csv.raw_credit_total_cents == 10_000
    assert civ.raw_collection_rate == csv.raw_collection_rate == 0.95


@pytest.mark.asyncio
async def test_only_source_differs_between_providers():
    civ = (await _build_civium()).to_staging_document()
    csv = (await _build_csv()).to_staging_document()

    # Same keys (the frozen staging contract).
    assert set(civ) == set(csv)
    # `source` names the provider (differs by design); `created_at` is a timestamp.
    assert civ["source"] == CIVIUM_PORTAL
    assert csv["source"] == CSV_PORTAL
    for doc in (civ, csv):
        doc.pop("source")
        doc.pop("created_at")
    # Everything else is byte-for-byte identical — the genericity guarantee.
    assert civ == csv


def test_cents_conversion_is_half_up_at_the_boundary():
    assert cents("254.98") == 25498
    assert cents(1000) == 100000
    assert cents(None) is None
    assert cents("") is None
    assert cents("0.005") == 1  # half-up


# ── Registry selection ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_registry_defaults_to_civium_without_startup():
    """A fresh registry (no register_mock_providers) still resolves the in-repo Civium default —
    lazy registration covers the CLI/test path."""
    reg = ProviderRegistry()
    with patch.object(reg, "_get_preference", AsyncMock(return_value={})):
        adapter = await reg.get_portal_adapter("13195")
    assert adapter.name == CIVIUM_PORTAL


@pytest.mark.asyncio
async def test_registry_selects_per_building_preference():
    reg = ProviderRegistry()
    reg.register_portal_adapter(CsvPortalAdapter())
    with patch.object(reg, "_get_preference", AsyncMock(return_value={"portal": CSV_PORTAL})):
        adapter = await reg.get_portal_adapter("UP-DEMO-001")
    assert adapter.name == CSV_PORTAL
    assert "portal" in reg.list_registered()


@pytest.mark.asyncio
async def test_registry_falls_back_to_default_for_unknown_provider():
    reg = ProviderRegistry()
    with patch.object(reg, "_get_preference", AsyncMock(return_value={"portal": "no_such_provider"})):
        adapter = await reg.get_portal_adapter("13195")
    # Unknown requested provider -> the Civium default, never a crash.
    assert adapter.name == CIVIUM_PORTAL
