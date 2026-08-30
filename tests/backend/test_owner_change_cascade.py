"""
Owner Change Cascade Tests
tests/_cascade_owner_change() side-effects fired from PUT /api/owners-units/{unit}.

Covers:
  - strata_owners sync (owner name + uoe updated; integer lot/unit NOT overwritten)
  - inherited arrears snapshot when balance_owing > 0
  - AGM proxy admin notifications when proxy exists
  - EC member admin alert when old owner is on EC list
  - chat group cleanup (type==unit_based groups)
  - rental certificate supersede on tenanted→owner-occupied transition
  - multi-tenant isolation: building_id in every write

Run:
    cd /home/gagneet/strata-management
    python3 -m pytest tests/backend/test_owner_change_cascade.py -v
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _unit(building_id="13195", unit_number="UA010", owner_name="Old Owner",
          owner_email="old@example.com", balance_owing=1500.0,
          is_owner_occupied=False, entitlement=100):
    return {
        "building_id": building_id,
        "unit_number": unit_number,
        "owner_name": owner_name,
        "owner_name_b": "",
        "owner_email": owner_email,
        "balance_owing": balance_owing,
        "is_owner_occupied": is_owner_occupied,
        "entitlement": entitlement,
    }


def _async_list(items):
    """Return a coroutine that yields a list — mocks cursor.to_list()."""

    async def _coro(*_, **__):
        return items

    return _coro


def _async_none():
    async def _coro(*_, **__):
        return None

    return _coro


def _make_cursor(items):
    """Mock a Motor cursor: .find() returns an object with .to_list(n) coroutine."""
    cursor = MagicMock()
    cursor.to_list = _async_list(items)
    return cursor


# ─── Import target after sys.path is set by conftest ──────────────────────────

@pytest.fixture(autouse=True)
def _patch_db():
    """
    Replace every db.* collection used in _cascade_owner_change with AsyncMock.
    Tests override individual attributes as needed.
    """
    mock_db = MagicMock()
    # Default: all collections return empty / no-op
    for col in ("strata_owners", "units", "user_units", "agm", "agm_attendance",
                "users", "memberships", "ec_members", "chat_groups", "rental_certificates",
                "unit_levy_ledger", "levy_payments"):
        coll = MagicMock()
        coll.update_one = AsyncMock(return_value=MagicMock(modified_count=0))
        coll.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
        coll.find_one = AsyncMock(return_value=None)
        coll.find = MagicMock(return_value=_make_cursor([]))
        setattr(mock_db, col, coll)

    with patch("server.db", mock_db):
        yield mock_db


@pytest.fixture
def cascade():
    """Import _cascade_owner_change after db is patched."""
    import server
    return server._cascade_owner_change


# ─── 1. strata_owners sync ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_strata_owners_sync_updates_owner_and_uoe(cascade, _patch_db):
    """strata_owners.update_one is called with new owner name and UOE."""
    old = _unit(owner_name="Old Owner", entitlement=120)
    update = {"owner_name": "New Owner"}

    await cascade("13195", "UA010", old, update, {"id": "actor1"})

    _patch_db.strata_owners.update_one.assert_awaited_once()
    args, kwargs = _patch_db.strata_owners.update_one.call_args
    query, update_doc = args[0], args[1]

    assert query == {"building_id": "13195", "unit_number": "UA010"}
    assert update_doc["$set"]["owner"] == "New Owner"
    assert update_doc["$set"]["uoe"] == 120
    # Integer lot/unit fields must NOT be in the $set (they are set at strata import)
    assert "lot" not in update_doc["$set"]
    assert "unit" not in update_doc["$set"]
    # Must be upsert=False to preserve integer fields
    assert kwargs.get("upsert") is False


@pytest.mark.asyncio
async def test_strata_owners_sync_always_fires_even_without_owner_change(cascade, _patch_db):
    """strata_owners sync happens on any unit update, not only when owner changes."""
    old = _unit(owner_name="Same Owner")
    update = {"is_owner_occupied": True}  # no owner_name change

    await cascade("13195", "UA010", old, update, {"id": "actor1"})

    _patch_db.strata_owners.update_one.assert_awaited_once()


# ─── 2. Inherited arrears snapshot ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_inherited_arrears_snapshot_written_when_balance_owing(cascade, _patch_db):
    """When old owner had balance owing (from ledger) > 0, arrears_metadata is written."""
    old = _unit(owner_name="Old Owner", balance_owing=2500.0)
    update = {"owner_name": "New Owner"}

    # Ledger shows net_balance = 2500 (arrears); no unreconciled payments
    _patch_db.unit_levy_ledger.find_one = AsyncMock(return_value={"net_balance": 2500.0, "total_paid": 0.0})
    _patch_db.levy_payments.find.return_value = _make_cursor([])
    _patch_db.user_units.find.return_value = _make_cursor([])
    _patch_db.memberships.find.return_value = _make_cursor([])
    _patch_db.users.find.return_value = _make_cursor([])
    _patch_db.agm.find.return_value = _make_cursor([])
    _patch_db.ec_members.find_one = AsyncMock(return_value=None)
    _patch_db.chat_groups.update_many = AsyncMock()

    await cascade("13195", "UA010", old, update, {"id": "actor1"})

    _patch_db.units.update_one.assert_awaited_once()
    _, upd = _patch_db.units.update_one.call_args[0]
    meta = upd["$set"]
    assert meta["arrears_metadata.previous_owner"] == "Old Owner"
    assert meta["arrears_metadata.inherited_arrears"] == 2500.0
    assert "arrears_metadata.transferred_at" in meta


@pytest.mark.asyncio
async def test_no_arrears_snapshot_when_balance_zero(cascade, _patch_db):
    """When old owner balance == 0 (from ledger), arrears_metadata is NOT written."""
    old = _unit(owner_name="Old Owner", balance_owing=0.0)
    update = {"owner_name": "New Owner"}

    # Ledger shows net_balance = 0 (fully paid)
    _patch_db.unit_levy_ledger.find_one = AsyncMock(return_value={"net_balance": 0.0, "total_paid": 1800.0})
    _patch_db.levy_payments.find.return_value = _make_cursor([])
    _patch_db.user_units.find.return_value = _make_cursor([])
    _patch_db.memberships.find.return_value = _make_cursor([])
    _patch_db.users.find.return_value = _make_cursor([])
    _patch_db.agm.find.return_value = _make_cursor([])
    _patch_db.ec_members.find_one = AsyncMock(return_value=None)
    _patch_db.chat_groups.update_many = AsyncMock()

    await cascade("13195", "UA010", old, update, {"id": "actor1"})

    # units.update_one must NOT be called for arrears snapshot
    _patch_db.units.update_one.assert_not_awaited()


# ─── 3. AGM proxy notification ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agm_proxy_notifies_admins_when_proxy_found(cascade, _patch_db):
    """When old owner has a proxy on upcoming AGM, admins are notified."""
    old = _unit(owner_name="Old Owner", balance_owing=0.0)
    update = {"owner_name": "New Owner"}

    old_user_id = "user-old-001"
    admin_id = "admin-001"
    agm_id = "agm-abc"

    _patch_db.user_units.find.return_value = _make_cursor([{"user_id": old_user_id}])
    _patch_db.memberships.find.return_value = _make_cursor([{"user_id": admin_id}])
    _patch_db.users.find.return_value = _make_cursor([{"id": admin_id}])
    _patch_db.agm.find.return_value = _make_cursor(
        [{"id": agm_id, "title": "AGM 2026"}]
    )
    _patch_db.agm_attendance.find_one = AsyncMock(
        return_value={"agm_id": agm_id, "user_id": old_user_id, "proxy_id": "proxy-xyz"}
    )
    _patch_db.ec_members.find_one = AsyncMock(return_value=None)
    _patch_db.chat_groups.update_many = AsyncMock()

    with patch("server.create_user_notification", new_callable=AsyncMock) as mock_notify:
        await cascade("13195", "UA010", old, update, {"id": "actor1"})

    mock_notify.assert_awaited()
    args = mock_notify.call_args_list[0][0]
    assert args[0] == admin_id
    assert "Proxy" in args[1]
    assert "UA010" in args[1]


@pytest.mark.asyncio
async def test_agm_proxy_no_notification_when_no_proxy(cascade, _patch_db):
    """No notification when old owner has no proxy on upcoming AGM."""
    old = _unit(owner_name="Old Owner", balance_owing=0.0)
    update = {"owner_name": "New Owner"}

    _patch_db.user_units.find.return_value = _make_cursor([{"user_id": "user-old-001"}])
    _patch_db.memberships.find.return_value = _make_cursor([])
    _patch_db.users.find.return_value = _make_cursor([])
    _patch_db.agm.find.return_value = _make_cursor([{"id": "agm-abc", "title": "AGM 2026"}])
    _patch_db.agm_attendance.find_one = AsyncMock(return_value=None)  # no proxy
    _patch_db.ec_members.find_one = AsyncMock(return_value=None)
    _patch_db.chat_groups.update_many = AsyncMock()

    with patch("server.create_user_notification", new_callable=AsyncMock) as mock_notify:
        await cascade("13195", "UA010", old, update, {"id": "actor1"})

    mock_notify.assert_not_awaited()


# ─── 4. EC member alert ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ec_member_alert_fired_when_old_owner_on_ec(cascade, _patch_db):
    """When old owner is in ec_members, admins get a warning notification."""
    old = _unit(owner_name="Anthony McDonald", owner_email="anthony@example.com",
                balance_owing=0.0)
    update = {"owner_name": "New Owner"}

    admin_id = "admin-001"
    _patch_db.user_units.find.return_value = _make_cursor([])
    _patch_db.memberships.find.return_value = _make_cursor([{"user_id": admin_id}])
    _patch_db.users.find.return_value = _make_cursor([{"id": admin_id}])
    _patch_db.agm.find.return_value = _make_cursor([])
    _patch_db.ec_members.find_one = AsyncMock(
        return_value={"name": "Anthony McDonald", "position": "Chairman",
                      "building_id": "13195"}
    )
    _patch_db.chat_groups.update_many = AsyncMock()

    with patch("server.create_user_notification", new_callable=AsyncMock) as mock_notify:
        await cascade("13195", "UA010", old, update, {"id": "actor1"})

    mock_notify.assert_awaited()
    notification_titles = [c[0][1] for c in mock_notify.call_args_list]
    assert any("EC Member" in t for t in notification_titles)


# ─── 5. Chat group cleanup ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_group_cleanup_targets_unit_based_type(cascade, _patch_db):
    """Old users are pulled from type='unit_based' groups using the type field, not nested key."""
    old = _unit(owner_name="Old Owner", balance_owing=0.0)
    update = {"owner_name": "New Owner"}
    old_user_id = "user-old-001"

    _patch_db.user_units.find.return_value = _make_cursor([{"user_id": old_user_id}])
    _patch_db.memberships.find.return_value = _make_cursor([])
    _patch_db.users.find.return_value = _make_cursor([])
    _patch_db.agm.find.return_value = _make_cursor([])
    _patch_db.ec_members.find_one = AsyncMock(return_value=None)

    await cascade("13195", "UA010", old, update, {"id": "actor1"})

    _patch_db.chat_groups.update_many.assert_awaited_once()
    query = _patch_db.chat_groups.update_many.call_args[0][0]
    # Must filter by type not by a nested settings key
    assert query["type"] == "unit_based"
    assert query["building_id"] == "13195"
    assert query["members.user_id"] == old_user_id

    update_op = _patch_db.chat_groups.update_many.call_args[0][1]
    assert update_op["$pull"]["members"]["user_id"] == old_user_id


@pytest.mark.asyncio
async def test_chat_group_cleanup_skipped_when_no_linked_users(cascade, _patch_db):
    """No chat_groups.update_many when there are no linked users for the unit."""
    old = _unit(owner_name="Old Owner", balance_owing=0.0)
    update = {"owner_name": "New Owner"}

    _patch_db.user_units.find.return_value = _make_cursor([])  # no linked users
    _patch_db.memberships.find.return_value = _make_cursor([])
    _patch_db.users.find.return_value = _make_cursor([])
    _patch_db.agm.find.return_value = _make_cursor([])
    _patch_db.ec_members.find_one = AsyncMock(return_value=None)

    await cascade("13195", "UA010", old, update, {"id": "actor1"})

    _patch_db.chat_groups.update_many.assert_not_awaited()


# ─── 6. Rental certificate supersede ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_rental_cert_superseded_on_tenanted_to_owner_occupied(cascade, _patch_db):
    """Issued certs are superseded when unit flips from tenanted to owner-occupied."""
    old = _unit(is_owner_occupied=False, owner_name="Same Owner", balance_owing=0.0)
    update = {"is_owner_occupied": True}

    await cascade("13195", "UA010", old, update, {"id": "actor1"})

    _patch_db.rental_certificates.update_many.assert_awaited_once()
    query = _patch_db.rental_certificates.update_many.call_args[0][0]
    # Must include building_id — multi-tenant isolation
    assert query["building_id"] == "13195"
    assert query["unit_number"] == "UA010"
    assert query["status"] == "issued"

    op = _patch_db.rental_certificates.update_many.call_args[0][1]
    assert op["$set"]["status"] == "superseded"
    assert "superseded_reason" in op["$set"]


@pytest.mark.asyncio
async def test_rental_cert_not_touched_when_already_owner_occupied(cascade, _patch_db):
    """No cert update when unit was already owner-occupied before the change."""
    old = _unit(is_owner_occupied=True, owner_name="Same Owner", balance_owing=0.0)
    update = {"owner_name": "New Owner"}

    _patch_db.user_units.find.return_value = _make_cursor([])
    _patch_db.memberships.find.return_value = _make_cursor([])
    _patch_db.users.find.return_value = _make_cursor([])
    _patch_db.agm.find.return_value = _make_cursor([])
    _patch_db.ec_members.find_one = AsyncMock(return_value=None)
    _patch_db.chat_groups.update_many = AsyncMock()

    await cascade("13195", "UA010", old, update, {"id": "actor1"})

    _patch_db.rental_certificates.update_many.assert_not_awaited()


# ─── 7. Multi-tenant isolation ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_writes_scoped_to_building_id(cascade, _patch_db):
    """Every DB write includes building_id to prevent cross-building contamination."""
    old = _unit(building_id="16244", owner_name="Old Owner", balance_owing=500.0,
                is_owner_occupied=False)
    update = {"owner_name": "New Owner", "is_owner_occupied": True}

    # Ledger shows balance owing so arrears snapshot is written
    _patch_db.unit_levy_ledger.find_one = AsyncMock(return_value={"net_balance": 500.0, "total_paid": 0.0})
    _patch_db.levy_payments.find.return_value = _make_cursor([])
    _patch_db.user_units.find.return_value = _make_cursor([])
    _patch_db.memberships.find.return_value = _make_cursor([])
    _patch_db.users.find.return_value = _make_cursor([])
    _patch_db.agm.find.return_value = _make_cursor([])
    _patch_db.ec_members.find_one = AsyncMock(return_value=None)
    _patch_db.chat_groups.update_many = AsyncMock()

    await cascade("16244", "TH001", old, update, {"id": "actor1"})

    # strata_owners
    so_query = _patch_db.strata_owners.update_one.call_args[0][0]
    assert so_query["building_id"] == "16244"

    # arrears metadata on units
    units_query = _patch_db.units.update_one.call_args[0][0]
    assert units_query["building_id"] == "16244"

    # rental certs
    rc_query = _patch_db.rental_certificates.update_many.call_args[0][0]
    assert rc_query["building_id"] == "16244"
