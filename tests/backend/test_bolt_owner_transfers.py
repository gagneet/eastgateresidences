from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from backend.server import (
        OwnerTransferRequest,
        ProcessOwnerTransferRequest,
        UpdateOwnerTransferRequest,
        create_owner_transfer_request,
        process_owner_transfer,
        update_owner_transfer_request,
    )

    SERVER_MODULE = "backend.server"
except ImportError:
    from server import (
        OwnerTransferRequest,
        ProcessOwnerTransferRequest,
        UpdateOwnerTransferRequest,
        create_owner_transfer_request,
        process_owner_transfer,
        update_owner_transfer_request,
    )

    SERVER_MODULE = "server"


@pytest.fixture
def mock_db():
    with patch(f"{SERVER_MODULE}.db") as mock:
        yield mock


@pytest.fixture
def admin_user():
    return {"id": "admin_id", "role": "super_admin", "full_name": "Super Admin"}


@pytest.mark.asyncio
async def test_create_owner_transfer_request_builds_owner_snapshot(mock_db, admin_user):
    transfer_data = OwnerTransferRequest(
        unit_number="101",
        new_owner_id="new_owner_id",
        ownership_documents=["contract.pdf"],
    )

    mock_db.users.find_one = AsyncMock(
        return_value={"id": "new_owner_id", "full_name": "New Owner", "email": "new@example.com"}
    )
    mock_db.user_units.find.return_value.to_list = AsyncMock(
        return_value=[
            {"user_id": "owner1", "unit_number": "101"},
            {"user_id": "owner2", "unit_number": "101"},
        ]
    )
    mock_db.users.find.return_value.to_list = AsyncMock(
        return_value=[
            {"id": "owner1", "full_name": "Owner One", "email": "owner1@example.com"},
            {"id": "owner2", "full_name": "Owner Two", "email": "owner2@example.com"},
        ]
    )
    mock_db.owner_transfer_requests.insert_one = AsyncMock()

    response = await create_owner_transfer_request(transfer_data, admin_user)

    assert response["status"] == "pending"
    insert_doc = mock_db.owner_transfer_requests.insert_one.call_args[0][0]
    assert insert_doc["submitted_by_id"] == "admin_id"
    assert insert_doc["required_approvals"] == 1
    assert [owner["full_name"] for owner in insert_doc["old_owners"]] == ["Owner One", "Owner Two"]


@pytest.mark.asyncio
async def test_owner_can_create_transfer_for_owned_unit(mock_db):
    owner_user = {"id": "owner_1", "role": "owner", "full_name": "Current Owner"}
    transfer_data = OwnerTransferRequest(
        unit_number="201",
        new_owner_email="incoming@example.com",
    )

    mock_db.user_units.find_one = AsyncMock(return_value={"user_id": "owner_1", "unit_number": "201"})
    mock_db.users.find_one = AsyncMock(
        return_value={"id": "incoming_owner", "full_name": "Incoming Owner", "email": "incoming@example.com"}
    )
    mock_db.user_units.find.return_value.to_list = AsyncMock(
        return_value=[{"user_id": "owner_1", "unit_number": "201"}]
    )
    mock_db.users.find.return_value.to_list = AsyncMock(
        return_value=[{"id": "owner_1", "full_name": "Current Owner", "email": "owner@example.com"}]
    )
    mock_db.owner_transfer_requests.insert_one = AsyncMock()

    response = await create_owner_transfer_request(transfer_data, owner_user)

    assert response["status"] == "pending"
    insert_doc = mock_db.owner_transfer_requests.insert_one.call_args[0][0]
    assert insert_doc["new_owner"]["email"] == "incoming@example.com"
    assert insert_doc["submitted_by_role"] == "owner"


@pytest.mark.asyncio
async def test_update_owner_transfer_request_edits_pending_request(mock_db):
    requester = {"id": "owner_1", "role": "owner", "full_name": "Current Owner"}
    existing_transfer = {
        "id": "transfer_123",
        "status": "pending",
        "current_approvals": 0,
        "unit_number": "201",
        "new_owner": {"user_id": "incoming_owner", "email": "incoming@example.com"},
        "submitted_by_id": "owner_1",
        "settlement_date": None,
        "request_notes": None,
    }
    update_payload = UpdateOwnerTransferRequest(
        unit_number="201",
        new_owner_email="updated@example.com",
        request_notes="Updated details",
        ownership_documents=["updated-contract.pdf"],
    )

    mock_db.owner_transfer_requests.find_one = AsyncMock(return_value=existing_transfer)
    mock_db.user_units.find_one = AsyncMock(return_value={"user_id": "owner_1", "unit_number": "201"})
    mock_db.users.find_one = AsyncMock(
        return_value={"id": "incoming_owner", "full_name": "Updated Owner", "email": "updated@example.com"}
    )
    mock_db.user_units.find.return_value.to_list = AsyncMock(
        return_value=[{"user_id": "owner_1", "unit_number": "201"}]
    )
    mock_db.users.find.return_value.to_list = AsyncMock(
        return_value=[{"id": "owner_1", "full_name": "Current Owner", "email": "owner@example.com"}]
    )
    mock_db.owner_transfer_requests.update_one = AsyncMock()

    response = await update_owner_transfer_request("transfer_123", update_payload, requester)

    assert response["status"] == "pending"
    updated_fields = mock_db.owner_transfer_requests.update_one.call_args[0][1]["$set"]
    assert updated_fields["new_owner"]["email"] == "updated@example.com"
    assert updated_fields["request_notes"] == "Updated details"
    assert updated_fields["ownership_documents"] == ["updated-contract.pdf"]


@pytest.mark.asyncio
async def test_update_owner_transfer_request_falls_back_to_legacy_owner_name(mock_db, admin_user):
    """Regression test: a transfer created by the portal drift detector
    (ownership_transfer_detection_service.detect_and_create_portal_owner_transfer)
    can legitimately reference a unit with NO active user_units row -- that
    detector's own _active_owner_info() already falls back to units.owner_name.
    Editing such a transfer used to 404 ("No current owners found for this
    unit") because this endpoint's re-derivation had no such fallback. Real
    incident: East Gate units UA029/UA042, 2026-08-19.
    """
    existing_transfer = {
        "id": "transfer_456",
        "status": "pending",
        "current_approvals": 0,
        "unit_number": "UA029",
        "new_owner": {"user_id": "incoming_owner", "email": "incoming@example.com"},
        "submitted_by_id": "system:external_ledger_owner_name_drift",
        "settlement_date": None,
        "request_notes": None,
    }
    update_payload = UpdateOwnerTransferRequest(
        unit_number="UA029",
        request_notes="Reviewed by chairman",
    )

    mock_db.owner_transfer_requests.find_one = AsyncMock(return_value=existing_transfer)
    # No active user_units owner row for this unit -- the exact UA029/UA042 signature.
    mock_db.user_units.find.return_value.to_list = AsyncMock(return_value=[])
    mock_db.units.find_one = AsyncMock(
        return_value={"owner_name": "Sonja Zink", "owner_name_b": None}
    )
    mock_db.users.find_one = AsyncMock(
        return_value={"id": "incoming_owner", "full_name": "Incoming Owner", "email": "incoming@example.com"}
    )
    mock_db.owner_transfer_requests.update_one = AsyncMock()

    response = await update_owner_transfer_request("transfer_456", update_payload, admin_user)

    assert response["status"] == "pending"
    updated_fields = mock_db.owner_transfer_requests.update_one.call_args[0][1]["$set"]
    assert updated_fields["old_owners"][0]["full_name"] == "Sonja Zink"
    assert updated_fields["request_notes"] == "Reviewed by chairman"


@pytest.mark.asyncio
async def test_process_owner_transfer_first_ec_approval_requires_second_approval(mock_db):
    ec_user = {"id": "ec_1", "role": "ec_member", "full_name": "EC Member"}
    process_request = ProcessOwnerTransferRequest(
        action="approve_keep_old",
        review_notes="Looks correct",
    )
    transfer_doc = {
        "id": "transfer_123",
        "status": "pending",
        "unit_number": "101",
        "submitted_by_id": "owner_1",
        "new_owner": {"user_id": "new_owner_id", "full_name": "New Owner", "email": "new@example.com"},
        "old_owners": [{"user_id": "owner1", "full_name": "Owner One", "email": "owner1@example.com"}],
        "ownership_documents": ["doc1.pdf"],
        "approval_history": [],
    }

    mock_db.owner_transfer_requests.find_one = AsyncMock(return_value=transfer_doc)
    mock_db.owner_transfer_requests.update_one = AsyncMock()

    response = await process_owner_transfer("transfer_123", process_request, ec_user)

    assert response["status"] == "pending_second_approval"
    update_doc = mock_db.owner_transfer_requests.update_one.call_args[0][1]["$set"]
    assert update_doc["required_approvals"] == 2
    assert update_doc["current_approvals"] == 1
    assert update_doc["approval_mode"] == "ec_dual"


@pytest.mark.asyncio
async def test_process_owner_transfer_real_estate_approves_immediately(mock_db):
    reviewer = {
        "id": "rea_1",
        "role": "real_estate_agent",
        "full_name": "Real Estate Agent",
    }
    process_request = ProcessOwnerTransferRequest(
        action="approve_remove_old",
        review_notes="Settlement confirmed",
        remove_owner_ids=["owner1", "owner2"],
    )
    transfer_doc = {
        "id": "transfer_123",
        "status": "pending",
        "unit_number": "101",
        "submitted_by_id": "manager_1",
        "new_owner": {"user_id": "new_owner_id", "full_name": "New Owner", "email": "new@example.com"},
        "old_owners": [
            {"user_id": "owner1", "full_name": "Owner One", "email": "owner1@example.com"},
            {"user_id": "owner2", "full_name": "Owner Two", "email": "owner2@example.com"},
        ],
        "ownership_documents": ["doc1.pdf"],
        "approval_history": [],
    }

    mock_db.owner_transfer_requests.find_one = AsyncMock(return_value=transfer_doc)
    # Production checks db.users.find_one(id=new_owner_id) before deciding to
    # create vs reuse the new-owner account — return the existing record so
    # the create-and-invite branch is skipped (matches the test's intent).
    mock_db.users.find_one = AsyncMock(return_value={
        "id": "new_owner_id",
        "full_name": "New Owner",
        "email": "new@example.com",
    })
    mock_db.user_units.find_one = AsyncMock(return_value=None)
    mock_db.user_units.insert_one = AsyncMock()
    mock_db.user_units.update_many = AsyncMock()
    mock_db.user_units.find.return_value.to_list = AsyncMock(
        return_value=[
            {"user_id": "new_owner_id"},
            {"user_id": "owner1"},
        ]
    )
    # Batch archive queries — _server_agg() does `cursor = await collection.aggregate(pipeline)`,
    # so aggregate must be AsyncMock, not a plain MagicMock.
    _uu_cursor = MagicMock()
    _uu_cursor.to_list = AsyncMock(return_value=[])
    mock_db.user_units.aggregate = AsyncMock(return_value=_uu_cursor)
    mock_db.memberships.update_many = AsyncMock()
    mock_db.memberships.find_one = AsyncMock(return_value={
        "user_id": "new_owner_id",
        "building_id": "13195",
        "roles": ["owner"],
        "units": ["101"],
    })
    mock_db.memberships.update_one = AsyncMock()
    mock_db.memberships.insert_one = AsyncMock()
    _mem_cursor = MagicMock()
    _mem_cursor.to_list = AsyncMock(return_value=[])
    mock_db.memberships.aggregate = AsyncMock(return_value=_mem_cursor)
    mock_db.users.update_many = AsyncMock()
    mock_db.users.update_one = AsyncMock()
    mock_db.ownership_transfer_log.insert_one = AsyncMock()
    mock_db.users.find.return_value.to_list = AsyncMock(
        return_value=[
            {"id": "new_owner_id", "full_name": "New Owner", "email": "new@example.com", "phone": "0400 000 000"},
            {"id": "owner1", "full_name": "Owner One", "email": "owner1@example.com", "phone": "0400 000 001"},
        ]
    )
    mock_db.units.find_one = AsyncMock(return_value=None)
    mock_db.units.update_one = AsyncMock()
    # _finalize_owner_transfer_approval also writes to strata_owners
    mock_db.strata_owners.update_one = AsyncMock()
    mock_db.strata_owners.update_many = AsyncMock()
    mock_db.strata_owners.find.return_value.to_list = AsyncMock(return_value=[])
    mock_db.strata_owners.insert_one = AsyncMock()
    # asyncio.gather() requires every arg to be awaitable, so the
    # lot_ownerships writes must use AsyncMock too.
    _lo_result = MagicMock(matched_count=1, modified_count=1)
    mock_db.lot_ownerships.update_one = AsyncMock(return_value=_lo_result)
    mock_db.lot_ownerships.insert_one = AsyncMock()
    mock_db.lot_ownerships.update_many = AsyncMock()
    mock_db.owner_transfer_requests.update_one = AsyncMock()

    with patch(f"{SERVER_MODULE}._cascade_owner_change", new=AsyncMock()):
        response = await process_owner_transfer("transfer_123", process_request, reviewer)

    assert response["status"] == "approved"
    mock_db.user_units.update_many.assert_called_once()
    args, _ = mock_db.user_units.update_many.call_args
    assert args[0]["user_id"]["$in"] == ["owner1", "owner2"]
    final_update = mock_db.owner_transfer_requests.update_one.call_args[0][1]["$set"]
    assert final_update["approval_mode"] == "manager"
    assert final_update["status"] == "approved"


def test_ec_approver_roles_never_contains_literal_chairman_string():
    """Regression guard: 'chairman' is not a top-level user.role value anywhere in this
    codebase (see rules/post-compact-critical.md) — a chairman is a user with
    role='ec_member' and ec_position='CHAIRMAN'. _effective_role() never returns
    'chairman' (removed in migration 0025 / commit 67fbc4a5), so a literal 'chairman'
    entry in OWNER_TRANSFER_EC_APPROVER_ROLES can never match and is dead weight that's
    easy to reintroduce by copy-pasting a role set from elsewhere in the app."""
    try:
        from backend.server import OWNER_TRANSFER_EC_APPROVER_ROLES, OWNER_TRANSFER_REVIEWER_ROLES
    except ImportError:
        from server import OWNER_TRANSFER_EC_APPROVER_ROLES, OWNER_TRANSFER_REVIEWER_ROLES

    assert "chairman" not in OWNER_TRANSFER_EC_APPROVER_ROLES
    assert "chairman" not in OWNER_TRANSFER_REVIEWER_ROLES
    assert "ec_member" in OWNER_TRANSFER_EC_APPROVER_ROLES
