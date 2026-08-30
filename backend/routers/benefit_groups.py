# @featuretrace:levy-fairness — operator-configured comparison groups (building-scoped).
# Layer: router
# Data flow: settings UI → /benefit-groups → core.benefit_groups + core.lot_benefit_groups
#            → levy_fairness_service group resolution (building-scoped).
# Related: backend/alembic/versions/0107_benefit_groups.py
#          backend/services/levy_fairness_service.py
# Tests: tests/backend/test_benefit_groups.py
"""Define who is compared with whom, and assign lots to those groups.

Replaces the inference in `levy_fairness_service._group_key()`, which string-matched
`unit_type` for "apartment"/"townhouse" and fell back to a `UA`/`TH` unit-number prefix.
That silently collapses to one group for a single-form scheme and mis-groups anything
split on another axis, with no way for an operator to correct it.

Groups default to `Group A`, `Group B` deliberately. A neutral label keeps the analysis
about who benefits from what, rather than implying that the building form is itself the
justification for a different contribution — which is not the argument, and is not what
the Act turns on.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from db_postgres.repos.config_repo import resolve_scheme_context
from db_postgres.session import async_session_context, set_tenant
from models.user import UserRole
from utils.auth import effective_role, get_current_building, get_current_user
from utils.test_data_flag import under_pytest

logger = logging.getLogger(__name__)
router = APIRouter()

# Defining who subsidises whom is a governance act, not day-to-day administration.
_MANAGE_ROLES = {UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN,
                 UserRole.STRATA_MANAGER, UserRole.EC_MEMBER}


class BenefitGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=80)
    description: Optional[str] = Field(None, max_length=500)
    display_order: int = 0


class BenefitGroupResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    benefit_group_id: str
    name: str
    description: Optional[str] = None
    display_order: int = 0
    lot_count: int = 0


class LotAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lot_ids: list[str] = Field(..., min_length=1, max_length=2000)


def _require_manage(current_user: dict) -> None:
    # effective_role, never current_user["role"]: an elevated owner reads as "owner" on
    # the raw field and would be refused despite holding the EC seat.
    if effective_role(current_user) not in _MANAGE_ROLES:
        raise HTTPException(status_code=403, detail="Not authorized to manage benefit groups")


async def _scheme(building_id: str) -> dict:
    scheme = await resolve_scheme_context(building_id)
    if not scheme or not scheme.get("tenant_id"):
        # Refuse rather than return an empty list: core.benefit_groups carries a strict
        # RLS policy, so a query without tenant context returns zero rows and no error,
        # which reads exactly like "this building has no groups configured".
        raise HTTPException(
            status_code=409,
            detail=f"No PostgreSQL scheme context for building {building_id}",
        )
    return scheme


@router.get("/benefit-groups", response_model=list[BenefitGroupResponse])
async def list_benefit_groups(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Groups configured for this building, with how many lots each holds."""
    scheme = await _scheme(building_id)
    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        rows = (await session.execute(
            # One pass over the membership table, not one per group.
            #
            # This was a correlated subquery, which re-scans core.lot_benefit_groups once
            # for every group -- O(groups x lots). An index on benefit_group_id exists
            # (migration 0107) and the planner correctly declines it at this size: a seq
            # scan of 87 rows is cheaper than an index lookup, so the cost is the REPEAT,
            # not the absence of an index, and adding another would change nothing.
            #
            # A grouped LEFT JOIN scans the membership once whatever the group count. It
            # must be a LEFT JOIN: an inner join silently drops a group with no lots, and
            # an empty group is a legitimate mid-configuration state that the settings UI
            # has to be able to show in order for anyone to put lots into it.
            text("""
                SELECT g.benefit_group_id::text, g.name, g.description, g.display_order,
                       count(m.lot_id) AS lot_count
                  FROM core.benefit_groups g
                  LEFT JOIN core.lot_benefit_groups m
                         ON m.benefit_group_id = g.benefit_group_id
                 WHERE g.scheme_id = :sid
                 GROUP BY g.benefit_group_id, g.name, g.description, g.display_order
                 ORDER BY g.display_order, g.name
            """),
            {"sid": str(scheme["scheme_id"])},
        )).fetchall()
    return [
        BenefitGroupResponse(
            benefit_group_id=r[0], name=r[1], description=r[2],
            display_order=r[3], lot_count=r[4],
        ) for r in rows
    ]


@router.get("/benefit-groups/unassigned")
async def list_unassigned_lots(
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Lots not yet in any group.

    Deliberately its own endpoint and not folded into a default group. An unassigned lot
    is a legitimate mid-configuration state, and defaulting it into a group would silently
    change who subsidises whom.
    """
    scheme = await _scheme(building_id)
    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        rows = (await session.execute(
            text("""
                SELECT l.lot_id::text, l.unit_number, l.entitlement_units
                  FROM core.lots l
                 WHERE l.scheme_id = :sid
                   AND NOT EXISTS (SELECT 1 FROM core.lot_benefit_groups m
                                    WHERE m.lot_id = l.lot_id)
                 ORDER BY l.unit_number
            """),
            {"sid": str(scheme["scheme_id"])},
        )).fetchall()
    return [
        {"lot_id": r[0], "unit_number": r[1],
         "entitlement_units": float(r[2]) if r[2] is not None else None}
        for r in rows
    ]


@router.post("/benefit-groups", response_model=BenefitGroupResponse, status_code=201)
async def create_benefit_group(
        body: BenefitGroupCreate,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    _require_manage(current_user)
    scheme = await _scheme(building_id)
    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        try:
            row = (await session.execute(
                text("""
                    INSERT INTO core.benefit_groups
                        (tenant_id, scheme_id, name, description, display_order, is_test_data)
                    VALUES (CAST(:tid AS UUID), CAST(:sid AS UUID), :name, :descr, :ord, :test)
                    RETURNING benefit_group_id::text, name, description, display_order
                """),
                {"tid": str(scheme["tenant_id"]), "sid": str(scheme["scheme_id"]),
                 "name": body.name.strip(), "descr": body.description,
                 "ord": body.display_order, "test": under_pytest()},
            )).fetchone()
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            if "benefit_groups_scheme_name_ux" in str(exc):
                raise HTTPException(
                    status_code=409,
                    detail=f"A group named {body.name!r} already exists for this building",
                )
            raise
    return BenefitGroupResponse(
        benefit_group_id=row[0], name=row[1], description=row[2],
        display_order=row[3], lot_count=0,
    )


@router.put("/benefit-groups/{group_id}/lots")
async def assign_lots(
        group_id: str,
        body: LotAssignment,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Assign lots to a group. Membership is EXCLUSIVE — a lot moves rather than joining.

    Upsert on the lot, not on (lot, group): a lot in two groups is counted on both sides
    of a redistribution that must be zero-sum, so the arithmetic still balances and
    nothing downstream can detect it.
    """
    _require_manage(current_user)
    scheme = await _scheme(building_id)
    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        owns = (await session.execute(
            text("SELECT 1 FROM core.benefit_groups WHERE benefit_group_id = CAST(:g AS UUID) "
                 "AND scheme_id = CAST(:sid AS UUID)"),
            {"g": group_id, "sid": str(scheme["scheme_id"])},
        )).fetchone()
        if not owns:
            raise HTTPException(status_code=404, detail="Benefit group not found for this building")

        # Every lot must belong to THIS building. Without the check a caller could file
        # another scheme's lot into this scheme's group; RLS would not stop it, because
        # the row written carries this tenant's id.
        valid = {
            r[0] for r in (await session.execute(
                text("SELECT lot_id::text FROM core.lots WHERE scheme_id = CAST(:sid AS UUID)"),
                {"sid": str(scheme["scheme_id"])},
            )).fetchall()
        }
        unknown = [x for x in body.lot_ids if x not in valid]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"{len(unknown)} lot id(s) do not belong to this building",
            )

        for lot_id in body.lot_ids:
            await session.execute(
                text("""
                    INSERT INTO core.lot_benefit_groups
                        (lot_id, tenant_id, benefit_group_id, assigned_by, is_test_data)
                    VALUES (CAST(:lot AS UUID), CAST(:tid AS UUID), CAST(:g AS UUID),
                            CAST(:by AS UUID), :test)
                    ON CONFLICT (lot_id) DO UPDATE
                       SET benefit_group_id = EXCLUDED.benefit_group_id,
                           assigned_by = EXCLUDED.assigned_by,
                           assigned_at = now()
                """),
                {"lot": lot_id, "tid": str(scheme["tenant_id"]), "g": group_id,
                 "by": current_user.get("id"), "test": under_pytest()},
            )
        await session.commit()
    return {"assigned": len(body.lot_ids), "benefit_group_id": group_id}


@router.delete("/benefit-groups/{group_id}")
async def delete_benefit_group(
        group_id: str,
        current_user: dict = Depends(get_current_user),
        building_id: str = Depends(get_current_building),
):
    """Delete a group. Its lots become unassigned (ON DELETE CASCADE), not reassigned."""
    _require_manage(current_user)
    scheme = await _scheme(building_id)
    async with async_session_context() as session:
        await set_tenant(session, scheme["tenant_id"])
        result = await session.execute(
            text("DELETE FROM core.benefit_groups WHERE benefit_group_id = CAST(:g AS UUID) "
                 "AND scheme_id = CAST(:sid AS UUID)"),
            {"g": group_id, "sid": str(scheme["scheme_id"])},
        )
        await session.commit()
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="Benefit group not found for this building")
    return {"deleted": group_id}
