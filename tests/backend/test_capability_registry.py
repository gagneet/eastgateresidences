# @featuretrace:scoped-capability-access — Core role/scope and direct-route dependency coverage.
# Layer: test
# Data flow: pytest user claims + requested scope -> capability_registry.can/require_capability -> allow or 403 (scope param: building|global).
# Related: backend/services/capability_registry.py

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from services.capability_registry import (
    CAPABILITY_REGISTRY,
    can,
    require_capability,
    serialise_capability_registry,
)


def _user(role: str, **claims) -> dict:
    return {"id": f"user-{role}", "role": role, "effective_role": role, **claims}


def test_registry_is_serialisable_and_canonical():
    serialised = serialise_capability_registry()
    assert len(serialised) == len(CAPABILITY_REGISTRY)
    assert [item["name"] for item in serialised] == sorted(CAPABILITY_REGISTRY)
    assert all(item["scope_type"] in {"platform", "organisation", "building", "unit", "work_order"} for item in serialised)


def test_strata_admin_is_limited_to_assigned_organisation():
    user = _user("strata_admin", organisation_id="org-1")
    assert can(user, "organisation.portfolio.view", {"organisation_id": "org-1"})
    assert not can(user, "organisation.portfolio.view", {"organisation_id": "org-2"})


def test_strata_admin_building_access_requires_matching_organisation_or_building():
    user = _user("strata_admin", organisation_id="org-1")
    assert can(
        user,
        "building.finance.manage",
        {"building_id": "13195", "organisation_id": "org-1"},
    )
    assert not can(
        user,
        "building.finance.manage",
        {"building_id": "16244", "organisation_id": "org-2"},
    )


def test_strata_manager_is_limited_to_explicit_buildings():
    user = _user("strata_manager", assigned_building_ids=["13195"])
    assert can(user, "building.finance.manage", {"building_id": "13195"})
    assert not can(user, "building.finance.manage", {"building_id": "16244"})


def test_ec_chairperson_is_building_only_and_not_management_admin():
    user = _user(
        "ec_member",
        building_id="13195",
        governance_office="chairperson",
    )
    assert can(user, "building.bi.view", {"building_id": "13195"})
    assert not can(user, "building.bi.view", {"building_id": "16244"})
    assert not can(user, "building.finance.manage", {"building_id": "13195"})
    assert not can(user, "organisation.users.manage", {"organisation_id": "org-1"})


def test_owner_cannot_query_arbitrary_bi_or_finance_data():
    user = _user("owner", building_id="13195", unit_id="unit-7")
    assert not can(user, "building.bi.view", {"building_id": "13195"})
    assert can(
        user,
        "unit.levies.view",
        {"building_id": "13195", "unit_id": "unit-7"},
    )
    assert not can(
        user,
        "unit.levies.view",
        {"building_id": "13195", "unit_id": "unit-8"},
    )


def test_tenant_cannot_access_owner_financial_records():
    user = _user("tenant", building_id="13195", unit_id="unit-7")
    assert not can(
        user,
        "unit.levies.view",
        {"building_id": "13195", "unit_id": "unit-7"},
    )
    assert can(
        user,
        "unit.documents.view",
        {"building_id": "13195", "unit_id": "unit-7"},
    )


def test_service_provider_is_limited_to_assigned_work_and_building():
    user = _user(
        "service_provider",
        assigned_building_ids=["13195"],
        assigned_work_order_ids=["wo-1"],
    )
    assert can(
        user,
        "work_order.assigned.view",
        {"building_id": "13195", "work_order_id": "wo-1"},
    )
    assert not can(
        user,
        "work_order.assigned.view",
        {"building_id": "13195", "work_order_id": "wo-2"},
    )
    assert not can(
        user,
        "work_order.assigned.view",
        {"building_id": "16244", "work_order_id": "wo-1"},
    )


@pytest.mark.parametrize("role", ["chairman", "guest", "unknown_role"])
def test_legacy_or_unknown_roles_fail_closed(role):
    user = _user(role, building_id="13195", organisation_id="org-1")
    assert not can(user, "building.finance.manage", {"building_id": "13195"})
    assert not can(user, "organisation.users.manage", {"organisation_id": "org-1"})


def test_unknown_capability_and_missing_scope_fail_closed():
    user = _user("super_admin")
    assert not can(user, "building.not_registered", {"building_id": "13195"})
    assert not can(user, "building.finance.manage", {})
    assert not can(user, "building.finance.manage", None)


@pytest.mark.asyncio
async def test_route_dependency_denies_cross_building_direct_access():
    dependency = require_capability(
        "building.cutover.view",
        scope_params={"building_id": "building_id"},
    )
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/admin/cutover/status/16244",
        "path_params": {"building_id": "16244"},
        "query_string": b"",
        "headers": [],
    })
    user = _user("strata_manager", assigned_building_ids=["13195"])

    with pytest.raises(HTTPException) as exc:
        await dependency(request=request, current_user=user)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_route_dependency_does_not_treat_caller_organisation_as_building_scope():
    dependency = require_capability(
        "building.cutover.view",
        scope_params={"building_id": "building_id"},
    )
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/admin/cutover/status/16244",
        "path_params": {"building_id": "16244"},
        "query_string": b"",
        "headers": [],
    })
    user = _user("strata_admin", organisation_id="org-1")

    with pytest.raises(HTTPException) as exc:
        await dependency(request=request, current_user=user)

    assert exc.value.status_code == 403


def _cutover_request(building_id: str = "13195") -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": f"/admin/cutover/status/{building_id}",
        "path_params": {"building_id": building_id},
        "query_string": b"",
        "headers": [],
    })


@pytest.mark.asyncio
async def test_route_dependency_rejects_caller_asserted_building_claims():
    """A claim the caller supplies is not a claim the server verified.

    This test previously asserted the opposite: it handed the dependency a user
    carrying `assigned_building_ids=["13195"]` and expected access to be
    granted. That encoded the vulnerability the Phase 2 hydrator closes — the
    subject's building claims must come from core.user_role_assignments, never
    from whatever the caller's user mapping happened to contain.

    With no resolvable tenant identity, hydration is fail-closed and yields
    empty claims, so the decision denies.
    """
    dependency = require_capability(
        "building.cutover.view",
        scope_params={"building_id": "building_id"},
    )
    user = _user("strata_manager", assigned_building_ids=["13195"])

    with pytest.raises(HTTPException) as excinfo:
        await dependency(request=_cutover_request(), current_user=user)
    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_route_dependency_allows_verified_assigned_building():
    """With the assignment verified by hydration, the same request is allowed."""
    dependency = require_capability(
        "building.cutover.view",
        scope_params={"building_id": "building_id"},
    )
    user = _user("strata_manager")

    async def _verified(subject, scope, **_hydration_hints):
        return {**subject, "assigned_building_ids": ["13195"], "governance_offices": []}

    with patch(
        "services.authorisation_context.hydrate_authorisation_claims",
        new=AsyncMock(side_effect=_verified),
    ):
        assert await dependency(request=_cutover_request(), current_user=user) is user


@pytest.mark.asyncio
async def test_route_dependency_denies_a_building_the_user_is_not_assigned_to():
    """Verified claims still scope the decision to the requested building."""
    dependency = require_capability(
        "building.cutover.view",
        scope_params={"building_id": "building_id"},
    )
    user = _user("strata_manager")

    async def _verified(subject, scope, **_hydration_hints):
        return {**subject, "assigned_building_ids": ["13195"], "governance_offices": []}

    with patch(
        "services.authorisation_context.hydrate_authorisation_claims",
        new=AsyncMock(side_effect=_verified),
    ):
        with pytest.raises(HTTPException) as excinfo:
            await dependency(request=_cutover_request("16244"), current_user=user)
    assert excinfo.value.status_code == 403


def test_act_chairperson_capability_is_office_scoped():
    chair = _user(
        "ec_member",
        building_id="13195",
        governance_office="chairperson",
    )
    ordinary_member = _user(
        "ec_member",
        building_id="13195",
        governance_office="ordinary_member",
    )

    assert can(
        chair,
        "building.meetings.agenda.manage",
        {"building_id": "13195"},
    )
    assert not can(
        ordinary_member,
        "building.meetings.agenda.manage",
        {"building_id": "13195"},
    )
    assert not can(
        chair,
        "building.meetings.agenda.manage",
        {"building_id": "16244"},
    )


def test_act_legacy_chairman_office_claim_is_normalised():
    user = _user(
        "ec_member",
        building_id="13195",
        ec_position="chairman",
    )
    assert can(
        user,
        "building.meetings.agenda.manage",
        {"building_id": "13195"},
    )


def test_act_secretary_and_treasurer_functions_do_not_leak_to_all_ec_members():
    secretary = _user(
        "ec_member",
        building_id="13195",
        governance_offices=["secretary"],
    )
    treasurer = _user(
        "ec_member",
        building_id="13195",
        governance_offices=["treasurer"],
    )
    ordinary_member = _user(
        "ec_member",
        building_id="13195",
        governance_offices=["ordinary_member"],
    )

    scope = {"building_id": "13195"}
    assert can(secretary, "building.meetings.minutes.prepare", scope)
    assert not can(secretary, "building.finance.records.prepare", scope)
    assert can(treasurer, "building.finance.records.prepare", scope)
    assert not can(treasurer, "building.meetings.minutes.prepare", scope)
    assert not can(ordinary_member, "building.meetings.minutes.prepare", scope)
    assert not can(ordinary_member, "building.finance.records.prepare", scope)


def test_act_treasurer_payment_requires_verified_committee_authorisation():
    treasurer = _user(
        "ec_member",
        building_id="13195",
        governance_office="treasurer",
        active_resolution_ids=["resolution-42"],
    )

    assert can(
        treasurer,
        "building.finance.payment.execute",
        {"building_id": "13195", "resolution_id": "resolution-42"},
    )
    assert not can(
        treasurer,
        "building.finance.payment.execute",
        {"building_id": "13195"},
    )
    assert not can(
        treasurer,
        "building.finance.payment.execute",
        {"building_id": "13195", "resolution_id": "unverified"},
    )


def test_manager_and_staff_execution_requires_verified_written_delegation():
    manager = _user(
        "strata_manager",
        assigned_building_ids=["13195"],
        active_delegation_ids=["delegation-7"],
    )
    staff = _user(
        "admin_staff",
        assigned_building_ids=["13195"],
        delegation_ids=["delegation-7"],
    )

    allowed_scope = {"building_id": "13195", "delegation_id": "delegation-7"}
    assert can(manager, "building.management.delegated.execute", allowed_scope)
    assert can(staff, "building.management.delegated.execute", allowed_scope)
    assert not can(
        manager,
        "building.management.delegated.execute",
        {"building_id": "13195", "delegation_id": "delegation-other"},
    )
    assert not can(
        manager,
        "building.management.delegated.execute",
        {"building_id": "16244", "delegation_id": "delegation-7"},
    )


def test_super_admin_does_not_bypass_office_or_delegation_business_authority():
    super_admin = _user(
        "super_admin",
        building_id="13195",
        active_resolution_ids=["resolution-42"],
        active_delegation_ids=["delegation-7"],
    )

    assert not can(
        super_admin,
        "building.meetings.agenda.manage",
        {"building_id": "13195"},
    )
    assert not can(
        super_admin,
        "building.finance.payment.execute",
        {"building_id": "13195", "resolution_id": "resolution-42"},
    )
    assert not can(
        super_admin,
        "building.management.delegated.execute",
        {"building_id": "13195", "delegation_id": "delegation-7"},
    )
