from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _cursor(items):
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=items)
    return cursor


@pytest.mark.asyncio
async def test_get_general_settings_scopes_to_general_docs():
    from services.settings_service import get_general_settings

    with patch("services.settings_service.db") as mock_db, \
         patch("services.settings_service._get_postgres_general_settings", AsyncMock(return_value=None)):
        mock_db.settings.find_one = AsyncMock(
            return_value={"building_id": "13195", "type": "general", "building_name": "East Gate"}
        )
        result = await get_general_settings("13195", {"_id": 0})

    query = mock_db.settings.find_one.call_args[0][0]
    assert {"building_id": "13195"} in query["$and"]
    assert {"type": "general"} in query["$and"][1]["$or"]
    assert result["building_name"] == "East Gate"


@pytest.mark.asyncio
async def test_update_owner_unit_rejects_owner_identity_edits():
    import server

    current_user = {"id": "admin-1", "role": "super_admin"}

    with patch("server.db") as mock_db:
        mock_db.units.find_one = AsyncMock(
            return_value={"building_id": "13195", "unit_number": "UA001", "owner_name": "Old Owner"}
        )

        with pytest.raises(HTTPException) as exc:
            await server.update_owner_unit(
                "UA001",
                server.OwnerUnitUpdate(owner_name="New Owner"),
                current_user,
                "13195",
            )

    assert exc.value.status_code == 400
    assert "ownership transfer workflow" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_get_owners_units_search_uses_canonical_owner_map():
    import server

    current_user = {"id": "admin-1", "role": "super_admin", "unit_number": None}
    unit_doc = {
        "id": "unit-1",
        "building_id": "13195",
        "unit_number": "UA001",
        "lot_number": "LOT1",
        "owner_name": "Legacy Owner",
        "entitlement": 100,
        "unit_type": "apartment",
        "permissions": {},
    }

    with (
        patch("server.db") as mock_db,
        patch("server._get_general_settings", new=AsyncMock(return_value={"levy_collection_frequency": "quarterly"})),
        patch("server._get_all_unit_owners", new=AsyncMock(return_value={
            "UA001": {
                "owner_name": "Transferred Owner",
                "owner_name_b": None,
                "owner_email": "new-owner@example.com",
                "owner_email_b": None,
            }
        })),
        patch("utils.finance_helpers.get_latest_levy_year", new=AsyncMock(return_value="2026")),
        patch("utils.finance_helpers.get_latest_ledger_year", new=AsyncMock(return_value="2026")),
        patch("utils.finance_helpers.get_levy_rates", new=AsyncMock(return_value={
            "admin_annual": 10.0,
            "sinking_annual": 5.0,
            "admin_quarterly": 2.5,
            "sinking_quarterly": 1.25,
        })),
    ):
        mock_db.units.find.return_value = _cursor([unit_doc])
        mock_db.memberships.find.return_value = _cursor([])
        mock_db.unit_levy_ledger.find.return_value = _cursor([])
        mock_db.levy_payments.find.return_value = _cursor([])
        mock_db.users.find.side_effect = [_cursor([]), _cursor([])]

        response = await server.get_owners_units(
            skip=0,
            limit=100,
            search="Transferred",
            current_user=current_user,
            building_id="13195",
        )

    assert len(response) == 1
    assert response[0].owner_name == "Transferred Owner"


@pytest.mark.asyncio
async def test_get_owner_info_uses_owner_equivalent_user_when_role_at_unit_missing():
    from services.owner_service import get_owner_info

    with patch("services.owner_service.db") as mock_db, \
         patch("services.owner_service.is_cutover_feature_enabled", AsyncMock(return_value=False)):
        mock_db.user_units.find_one = AsyncMock(side_effect=[None, None])
        mock_db.user_units.find.return_value = _cursor([
            {"user_id": "user-1", "is_primary": True, "role_at_unit": None}
        ])
        mock_db.users.find.return_value = _cursor([
            {"id": "user-1", "full_name": "Canonical Owner", "email": "owner@example.com", "role": "owner"}
        ])
        mock_db.units.find_one = AsyncMock(
            return_value={
                "unit_number": "UA001",
                "owner_name": "Legacy Owner",
                "owner_email": "legacy@example.com",
                "owner_name_b": "Co Owner",
                "owner_email_b": "co@example.com",
            }
        )

        result = await get_owner_info("UA001", "13195")

    assert result["owner_name"] == "Canonical Owner"
    assert result["owner_email"] == "owner@example.com"
    assert result["owner_name_b"] == "Co Owner"
    assert result["source"] == "user_units_legacy_role_backfill"


@pytest.mark.asyncio
async def test_request_user_info_uses_building_scoped_general_settings(monkeypatch):
    from routers import users as users_router

    current_user = {"id": "admin-1", "role": "super_admin", "full_name": "Admin User"}
    captured = {}

    def fake_build_email(user_name, reason_label, update_url, building_name, building_address):
        captured["building_name"] = building_name
        captured["building_address"] = building_address
        return "<p>html</p>", "text"

    monkeypatch.setattr(users_router, "_build_info_request_email", fake_build_email)
    monkeypatch.setattr("utils.email.send_email_async", lambda *args, **kwargs: None)
    monkeypatch.setattr("utils.helpers.create_audit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(users_router.asyncio, "create_task", lambda coro: None)

    with (
        patch("routers.users.db") as mock_db,
        patch(
            "routers.users.get_general_settings_or_default",
            new=AsyncMock(return_value={"building_name": "Sierra", "building_address": "1 Demo Street"}),
        ),
    ):
        mock_db.memberships.find_one = AsyncMock(return_value={"user_id": "user-1", "building_id": "16244"})
        mock_db.users.find_one = AsyncMock(
            return_value={
                "id": "user-1",
                "email": "resident@example.com",
                "full_name": "Pending Resident",
                "role": "owner",
                "is_approved": False,
            }
        )
        mock_db.users.update_one = AsyncMock()

        response = await users_router.request_user_info(
            user_id="user-1",
            request_data=users_router.RequestInfoData(reason="wrong_unit"),
            current_user=current_user,
            building_id="16244",
        )

    assert response["message"].startswith("Info request sent")
    assert captured == {"building_name": "Sierra", "building_address": "1 Demo Street"}


@pytest.mark.asyncio
async def test_get_building_info_scopes_units_to_api_key_building():
    from routers import external_api

    with (
        patch("routers.external_api.db") as mock_db,
        patch("routers.external_api.resolve_building_id_for_key", new=AsyncMock(return_value="16244")),
        patch(
            "routers.external_api.get_general_settings_or_default",
            new=AsyncMock(return_value={"building_name": "Sierra", "building_address": "1 Demo Street"}),
        ),
    ):
        mock_db.units.count_documents = AsyncMock(return_value=12)

        result = await external_api.get_building_info({"id": "key-1"})

    mock_db.units.count_documents.assert_awaited_once_with({"building_id": "16244"})
    assert result["building_name"] == "Sierra"
    assert result["total_units"] == 12


@pytest.mark.asyncio
async def test_estimate_water_charges_uses_building_scoped_settings():
    from routers import water_bills

    with patch(
            "routers.water_bills.get_general_settings_or_default",
            new=AsyncMock(return_value={"building_name": "Harbourside View"}),
    ):
        result = await water_bills.estimate_water_charges(
            start_date="2026-01-01",
            end_date="2026-01-31",
            usage_kl=None,
            current_user={"id": "user-1", "role": "owner"},
            building_id="harbourside_view",
        )

    assert "Harbourside View" in result.note


@pytest.mark.asyncio
async def test_get_strata_roll_includes_legacy_owner_mapping_without_role_at_unit():
    from routers import building as building_router

    current_user = {"id": "ec-1", "role": "ec_member"}

    with patch("routers.building.db") as mock_db:
        mock_db.units.find.return_value = _cursor([
            {
                "unit_number": "UA001",
                "lot_number": "1",
                "entitlement": 100,
                "owner_name": "Legacy Owner",
                "owner_email": "legacy@example.com",
            }
        ])
        mock_db.user_units.find.return_value = _cursor([
            {
                "user_id": "owner-1",
                "unit_number": "UA001",
                "is_active": True,
                "is_primary": True,
                "role_at_unit": None,
            }
        ])
        mock_db.users.find.return_value = _cursor([
            {
                "id": "owner-1",
                "full_name": "Portal Owner",
                "email": "owner@example.com",
                "role": "owner",
                "strata_roll_consent": True,
            }
        ])

        response = await building_router.get_strata_roll(current_user=current_user, building_id="16244")

    assert response[0]["owner_name"] == "Portal Owner"
    assert response[0]["registered_owners"][0]["full_name"] == "Portal Owner"
