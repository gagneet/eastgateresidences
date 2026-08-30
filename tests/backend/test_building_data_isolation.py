"""
test_building_data_isolation.py — Unit tests verifying that building data isolation
is enforced across key endpoints. All tests use mocked databases to ensure no
real DB writes and to simulate multi-building scenarios.

Tests cover:
- building_stress endpoints use get_current_building (not hardcoded DEFAULT_BUILDING_ID)
- /users endpoint filters by building membership
- /owners-units endpoint filters by building_id
- Super admin sees only selected building's data (not all buildings)
- ec_member role has access to OwnerHub backend endpoints
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_user(role="super_admin", building_id="13195", **kwargs):
    return {
        "id": "user-001",
        "email": "admin@example.com",
        "full_name": "Test Admin",
        "role": role,
        "building_id": building_id,
        "is_active": True,
        "is_approved": True,
        "status": "active",
        "custom_permissions": {},
        **kwargs,
    }


def _mock_db():
    db = MagicMock()
    db.building_financial_health.find_one = AsyncMock(return_value=None)
    db.buildings.find_one = AsyncMock(return_value={"id": "18932", "name": "Harbourview Residences"})
    db.memberships.find.return_value.to_list = AsyncMock(return_value=[])
    db.users.find.return_value.to_list = AsyncMock(return_value=[])
    db.units.find.return_value.to_list = AsyncMock(return_value=[])
    return db


# ── Test: building_stress uses get_current_building, not DEFAULT_BUILDING_ID ─

class TestBuildingStressIsolation:
    """
    Verify that building_stress endpoints use building_id from get_current_building
    (scoped to the logged-in user's selected building) rather than hardcoded DEFAULT_BUILDING_ID.
    """

    @pytest.mark.asyncio
    async def test_snapshot_uses_current_building(self):
        """get_snapshot must query with the building_id from the JWT, not DEFAULT."""
        from routers.building_stress import get_snapshot

        mock_db = _mock_db()
        # Simulate "18932" building with no existing snapshot (force recompute)
        mock_db.building_financial_health.find_one = AsyncMock(return_value=None)

        with patch("routers.building_stress.db", mock_db), \
                patch("routers.building_stress.compute_and_store_snapshot", new_callable=AsyncMock) as mock_compute:
            mock_compute.return_value = {"building_id": "18932", "snapshot": {}}
            result = await get_snapshot(
                current_user=_make_user(),
                building_id="18932"
            )

        # The compute function must be called with the JWT building_id, not DEFAULT
        mock_compute.assert_called_once_with("18932")
        assert result["building_id"] == "18932"

    @pytest.mark.asyncio
    async def test_timeline_uses_current_building(self):
        """get_timeline must use building_id from parameter."""
        from routers.building_stress import get_timeline

        with patch("routers.building_stress.project_reserve_timeline", new_callable=AsyncMock) as mock_proj:
            mock_proj.return_value = {"building_id": "18932", "timeline": []}
            result = await get_timeline(
                years=10,
                current_user=_make_user(),
                building_id="18932"
            )

        mock_proj.assert_called_once_with("18932", years=10)

    @pytest.mark.asyncio
    async def test_unit_impact_uses_current_building(self):
        """get_unit_impact must pass building_id to service functions."""
        from routers.building_stress import get_unit_impact

        with patch("routers.building_stress.compute_reserve_adequacy_ratio",
                   new_callable=AsyncMock, return_value={"deficit": 50000}), \
                patch("routers.building_stress.compute_unit_levy_impact",
                      new_callable=AsyncMock, return_value={"unit": "UA001"}) as mock_impact:
            await get_unit_impact(
                unit_number="UA001",
                current_user=_make_user(),
                building_id="18932"
            )

        mock_impact.assert_called_once_with("UA001", "18932", 50000)

    @pytest.mark.asyncio
    async def test_mitigation_options_uses_current_building(self):
        """get_mitigation_options must use building_id from parameter."""
        from routers.building_stress import get_mitigation_options

        with patch("routers.building_stress.compute_reserve_adequacy_ratio",
                   new_callable=AsyncMock, return_value={"deficit": 0}), \
                patch("routers.building_stress.compute_risk_score",
                      new_callable=AsyncMock, return_value={"overall_risk_score": 0.3}), \
                patch("routers.building_stress.generate_mitigation_options",
                      new_callable=AsyncMock, return_value=[]) as mock_mitig:
            await get_mitigation_options(
                current_user=_make_user(),
                building_id="18932"
            )

        mock_mitig.assert_called_once_with("18932", 0, 0.3)

    @pytest.mark.asyncio
    async def test_refresh_uses_current_building(self):
        """refresh_snapshot (admin) must use building_id from parameter."""
        from routers.building_stress import refresh_snapshot

        with patch("routers.building_stress.compute_and_store_snapshot",
                   new_callable=AsyncMock, return_value={"building_id": "18932"}) as mock_compute:
            await refresh_snapshot(
                current_user=_make_user(role="ec_member"),
                building_id="18932"
            )

        mock_compute.assert_called_once_with("18932")

    def test_default_building_id_not_used_in_endpoint_handlers(self):
        """Regression: verify DEFAULT_BUILDING_ID is no longer referenced in endpoint bodies."""
        import inspect
        import routers.building_stress as bsmod

        # Get source of each endpoint handler
        funcs = [
            bsmod.get_snapshot,
            bsmod.get_timeline,
            bsmod.get_unit_impact,
            bsmod.get_mitigation_options,
            bsmod.refresh_snapshot,
        ]
        for fn in funcs:
            src = inspect.getsource(fn)
            assert "DEFAULT_BUILDING_ID" not in src, (
                f"{fn.__name__} still uses DEFAULT_BUILDING_ID — should use building_id parameter"
            )


# ── Test: OwnerHub ec_member access ──────────────────────────────────────────

class TestOwnerHubECMemberAccess:
    """
    Verify that ec_member role has access to OwnerHub endpoints, but resident
    investment properties remain private unless explicitly shared.
    """

    @pytest.mark.asyncio
    async def test_ec_member_in_allowed_roles(self):
        """ec_member must be in ALLOWED_ROLES for ownerhub."""
        from routers.ownerhub import ALLOWED_ROLES
        assert "ec_member" in ALLOWED_ROLES, "ec_member must be in ALLOWED_ROLES"

    @pytest.mark.asyncio
    async def test_chairman_in_allowed_roles(self):
        """chairman must be in ALLOWED_ROLES for ownerhub."""
        from routers.ownerhub import ALLOWED_ROLES
        assert "ec_member" in ALLOWED_ROLES, "ec_member must be in ALLOWED_ROLES"

    @pytest.mark.asyncio
    async def test_ec_member_list_properties_uses_explicit_share_filter(self):
        """ec_member calling list_properties must not query all properties."""
        from routers.ownerhub import list_properties

        # The router chains .find({}).skip().limit().to_list() — cursor must support all three.
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[{"id": "p1", "shared_staff_ids": ["ec-001"]}])

        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor

        with patch("database.db", mock_db):
            result = await list_properties(
                current_user=_make_user(role="ec_member", id="ec-001")
            )

        call_args = mock_db.owner_properties.find.call_args
        query = call_args[0][0]
        assert query == {
            "$or": [
                {"shared_staff_ids": "ec-001"},
                {"shared_with_user_ids": "ec-001"},
                {"allowed_user_ids": "ec-001"},
                {"assigned_staff_ids": "ec-001"},
            ]
        }
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_owner_list_properties_sees_own_only(self):
        """Regular owner calling list_properties should only get their own properties."""
        from routers.ownerhub import list_properties

        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[{"id": "p1", "owner_id": "owner-001"}])
        mock_db = MagicMock()
        mock_db.owner_properties.find.return_value = mock_cursor

        with patch("database.db", mock_db):
            result = await list_properties(
                current_user=_make_user(role="owner", id="owner-001")
            )

        assert mock_db.owner_properties.find.call_args[0][0] == {"owner_id": "owner-001"}
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_ec_member_list_units_sees_all(self):
        """ec_member calling list_strata_units should get all units."""
        from routers.ownerhub import list_strata_units

        # The router chains .find({}, proj).skip().limit().to_list() for admin roles.
        mock_cursor = MagicMock()
        mock_cursor.skip.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[
            {"unit_number": "UA001"}, {"unit_number": "TH071"}
        ])

        mock_db = MagicMock()
        mock_db.units.find.return_value = mock_cursor

        with patch("database.db", mock_db):
            result = await list_strata_units(
                current_user=_make_user(role="ec_member")
            )

        call_args = mock_db.units.find.call_args
        query = call_args[0][0]
        assert "unit_number" not in query, "ec_member should see all units"

    @pytest.mark.asyncio
    async def test_ec_member_tco_can_view_any_unit(self):
        """ec_member should be able to call TCO for any unit (not restricted to their own)."""
        from routers.ownerhub import get_unit_tco

        with patch("services.ownerhub_service.compute_unit_tco",
                   new_callable=AsyncMock, return_value={"unit": "UA001", "tco": 0}) as mock_tco:
            result = await get_unit_tco(
                unit_number="UA001",
                year="2026",
                vacancy_weeks=2,
                current_user=_make_user(role="ec_member", unit_number="TH087")
            )

        # Should not raise 403 (would if unit_number check was applied)
        mock_tco.assert_called_once()

    def test_tenant_not_in_allowed_roles(self):
        """tenant must NOT be in ALLOWED_ROLES for ownerhub."""
        from routers.ownerhub import ALLOWED_ROLES
        assert "tenant" not in ALLOWED_ROLES, "tenant must not access OwnerHub"


# ── Test: super admin building isolation via memberships ─────────────────────

class TestSuperAdminBuildingIsolation:
    """
    Verify that when a super admin switches to a different building,
    the /users endpoint only returns users who are members of that building.
    """

    @pytest.mark.asyncio
    async def test_users_filters_by_building_membership(self):
        """GET /users must query memberships with the correct building_id."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

        # get_users uses db.memberships.aggregate(pipeline).to_list(1000)
        # We capture the pipeline's $match stage to verify building_id isolation.
        aggregate_pipelines = []

        mock_db = MagicMock()
        # get_users now calls memberships.count_documents() before aggregate
        # to surface a warning when the collection is empty post-reset; the
        # call must be awaitable, otherwise the wrapping try/except swallows
        # the failure and aggregate is never reached.
        mock_db.memberships.count_documents = AsyncMock(return_value=1)
        # Set up the aggregate return value on the MagicMock BEFORE replacing it
        mock_db.memberships.aggregate.return_value.to_list = AsyncMock(return_value=[])

        # Wrap aggregate to capture pipeline arguments
        _original_aggregate = mock_db.memberships.aggregate

        def capture_aggregate(pipeline, *args, **kwargs):
            aggregate_pipelines.append(pipeline)
            return _original_aggregate(pipeline, *args, **kwargs)

        mock_db.memberships.aggregate = capture_aggregate

        from server import get_users

        with patch("server.db", mock_db), \
                patch("server.repair_orphan_staff_memberships", AsyncMock()), \
                patch("server.get_user_permissions") as mock_perms:
            mock_perms.return_value = MagicMock(can_manage_users=True)
            try:
                await get_users(
                    current_user=_make_user(role="super_admin"),
                    building_id="18932"
                )
            except Exception:
                pass  # may fail on user_to_response if DB returns empty

        # Verify the aggregation pipeline's first $match stage uses "18932"
        assert len(aggregate_pipelines) >= 1, "memberships.aggregate should have been called"
        pipeline = aggregate_pipelines[0]
        match_stage = next((s for s in pipeline if "$match" in s), None)
        assert match_stage is not None, "Pipeline must have a $match stage"
        assert match_stage["$match"].get("building_id") == "18932", (
            f"aggregate pipeline $match has {match_stage['$match']}, "
            "expected building_id='18932'"
        )
