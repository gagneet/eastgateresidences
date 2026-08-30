# @featuretrace:occupancy_intelligence — API router for Building Occupancy Intelligence
# Layer: router
# Data flow: OccupancyIntelligencePage → /api/occupancy/* → occupancy_status (building-scoped)
# Related: backend/models/occupancy.py
#           backend/services/occupancy_service.py
#           backend/services/occupancy_recompute.py
#           frontend/src/pages/dashboard/OccupancyIntelligencePage.jsx

"""
Occupancy Intelligence Router
==============================
All endpoints require:
  - Valid JWT (get_current_user)
  - building_id sourced from JWT only (get_current_building)
  - Role: chairman | ec_member | strata_manager | super_admin
  - Feature toggle: occupancy_intelligence

Endpoints:
  GET  /occupancy/summary     — aggregate counts + percentages
  GET  /occupancy/lots        — per-lot classification list
  GET  /occupancy/trends      — 12-month trend data (monthly aggregation)
  POST /occupancy/recompute   — manual trigger (admin-only)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from database import db
from db_postgres.session import async_session_context, set_tenant
from db_postgres.repos import config_repo
from models.occupancy import (
    OccupancyLotResponse,
    OccupancySummaryResponse,
    OccupancyTrendPoint,
    OccupancyTrendsResponse,
)
from models.user import UserRole
from services.occupancy_recompute import recompute_building_occupancy
from services.cutover_status_service import resolve_read_source
from models.cutover_status import DataSource
from utils.auth import get_current_building
from utils.permissions import require_feature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/occupancy", tags=["Occupancy Intelligence"])

# Roles that may access occupancy data
_VIEW_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.EC_MEMBER,
    UserRole.STRATA_MANAGER,
}

# Only these roles may trigger a manual recompute
_ADMIN_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.EC_MEMBER,
    UserRole.STRATA_MANAGER,
}


def _require_view_role(user: dict) -> None:
    """Generated function header.

    Function: _require_view_role
    Path: backend/routers/occupancy.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role")
    if role not in _VIEW_ROLES:
        raise HTTPException(status_code=403, detail="Insufficient permissions to view occupancy data")


def _require_admin_role(user: dict) -> None:
    """Generated function header.

    Function: _require_admin_role
    Path: backend/routers/occupancy.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = user.get("effective_role") or user.get("role")
    if role not in _ADMIN_ROLES:
        raise HTTPException(status_code=403, detail="Only admins may trigger occupancy recompute")


async def _require_occupancy_access(
        current_user: dict = Depends(require_feature("occupancy_intelligence")),
        building_id: str = Depends(get_current_building),
) -> dict:
    """Check site-wide toggle AND per-building override for occupancy_intelligence."""
    enabled = await config_repo.resolve_feature_toggle(
        building_id,
        "occupancy_intelligence",
        default=True,
    )
    if not enabled:
        raise HTTPException(
            status_code=403,
            detail="Feature 'occupancy_intelligence' is disabled for this building",
        )
    return current_user


async def _resolve_scheme_context(building_id: str) -> dict:
    scheme = await config_repo.resolve_scheme_context(building_id)
    if not scheme:
        raise HTTPException(status_code=503, detail="PostgreSQL scheme context is not available for occupancy")
    return scheme


def _normalise_pg_occupancy_status(value: str | None) -> str:
    status = str(value or "").strip().lower()
    if status in {"tenant", "tenanted", "rented"}:
        return "tenant"
    if status in {"owner_occupied", "owner-occupied", "owner occupied", "owner_occupier"}:
        return "owner_occupied"
    return "investor_unknown"


async def _pg_occupancy_summary(building_id: str) -> OccupancySummaryResponse:
    scheme = await _resolve_scheme_context(building_id)
    tenant_id = str(scheme["tenant_id"])
    scheme_id = str(scheme["scheme_id"])
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text(
                """
                WITH latest AS (
                    SELECT MAX(snapshot_date) AS snapshot_date
                    FROM analytics.fact_occupancy_snapshot
                    WHERE scheme_id = CAST(:scheme_id AS UUID)
                      AND is_current = true
                      AND is_test_data = false
                )
                SELECT
                    CASE
                        WHEN occupancy_type IN ('tenant', 'tenanted', 'rented') THEN 'tenant'
                        WHEN occupancy_type IN ('owner_occupied', 'owner-occupied', 'owner occupied', 'owner_occupier') THEN 'owner_occupied'
                        ELSE 'investor_unknown'
                    END AS status,
                    COUNT(*) AS count,
                    AVG(confidence)::float AS avg_confidence,
                    MAX(ingested_at) AS last_computed_at
                FROM analytics.fact_occupancy_snapshot
                WHERE scheme_id = CAST(:scheme_id AS UUID)
                  AND snapshot_date = (SELECT snapshot_date FROM latest)
                  AND is_current = true
                  AND is_test_data = false
                GROUP BY 1
                """
            ),
            {"scheme_id": scheme_id},
        )
        rows = result.mappings().all()

    counts = {"tenant": 0, "owner_occupied": 0, "investor_unknown": 0}
    confidence_sum = 0.0
    total = 0
    last_computed_at = None
    for row in rows:
        status = row["status"]
        count = int(row["count"] or 0)
        counts[status] = counts.get(status, 0) + count
        confidence_sum += float(row["avg_confidence"] or 0.0) * count
        total += count
        if row["last_computed_at"] and (last_computed_at is None or row["last_computed_at"] > last_computed_at):
            last_computed_at = row["last_computed_at"]

    avg_confidence = round(confidence_sum / total, 4) if total else 0.0
    return OccupancySummaryResponse(
        building_id=building_id,
        total_lots=total,
        tenant_count=counts["tenant"],
        owner_occupied_count=counts["owner_occupied"],
        investor_unknown_count=counts["investor_unknown"],
        tenant_pct=round(counts["tenant"] / total * 100, 1) if total else 0.0,
        owner_occupied_pct=round(counts["owner_occupied"] / total * 100, 1) if total else 0.0,
        investor_unknown_pct=round(counts["investor_unknown"] / total * 100, 1) if total else 0.0,
        avg_confidence=avg_confidence,
        last_computed_at=last_computed_at,
    )


async def _pg_occupancy_lots(building_id: str) -> list[OccupancyLotResponse]:
    scheme = await _resolve_scheme_context(building_id)
    tenant_id = str(scheme["tenant_id"])
    scheme_id = str(scheme["scheme_id"])
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text(
                """
                WITH latest AS (
                    SELECT MAX(snapshot_date) AS snapshot_date
                    FROM analytics.fact_occupancy_snapshot
                    WHERE scheme_id = CAST(:scheme_id AS UUID)
                      AND is_current = true
                      AND is_test_data = false
                )
                SELECT lot_number, occupancy_type, confidence::float AS confidence, ingested_at
                FROM analytics.fact_occupancy_snapshot
                WHERE scheme_id = CAST(:scheme_id AS UUID)
                  AND snapshot_date = (SELECT snapshot_date FROM latest)
                  AND is_current = true
                  AND is_test_data = false
                ORDER BY lot_number
                """
            ),
            {"scheme_id": scheme_id},
        )
        rows = result.mappings().all()
    return [
        OccupancyLotResponse(
            lot_number=row["lot_number"],
            unit_type=None,
            status=_normalise_pg_occupancy_status(row["occupancy_type"]),
            confidence=float(row["confidence"] or 0.0),
            sources=["postgres_occupancy_snapshot"],
            last_verified=row["ingested_at"],
        )
        for row in rows
    ]


async def _pg_occupancy_trends(building_id: str) -> OccupancyTrendsResponse:
    scheme = await _resolve_scheme_context(building_id)
    tenant_id = str(scheme["tenant_id"])
    scheme_id = str(scheme["scheme_id"])
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text(
                """
                SELECT
                    to_char(snapshot_date, 'YYYY-MM') AS month,
                    CASE
                        WHEN occupancy_type IN ('tenant', 'tenanted', 'rented') THEN 'tenant'
                        WHEN occupancy_type IN ('owner_occupied', 'owner-occupied', 'owner occupied', 'owner_occupier') THEN 'owner_occupied'
                        ELSE 'investor_unknown'
                    END AS status,
                    COUNT(*) AS count
                FROM analytics.fact_occupancy_snapshot
                WHERE scheme_id = CAST(:scheme_id AS UUID)
                  AND snapshot_date >= (CURRENT_DATE - INTERVAL '365 days')
                  AND is_test_data = false
                GROUP BY 1, 2
                ORDER BY 1
                """
            ),
            {"scheme_id": scheme_id},
        )
        rows = result.mappings().all()

    by_month: dict[str, dict[str, int]] = {}
    for row in rows:
        month = row["month"]
        bucket = by_month.setdefault(month, {"tenant": 0, "owner_occupied": 0, "investor_unknown": 0})
        bucket[row["status"]] = int(row["count"] or 0)

    if not by_month:
        return OccupancyTrendsResponse(building_id=building_id, trend=[])

    latest_counts = by_month[max(by_month)]
    now = datetime.now(timezone.utc)
    trend = []
    for months_back in range(11, -1, -1):
        d = now - timedelta(days=months_back * 30)
        month = d.strftime("%Y-%m")
        counts = by_month.get(month, latest_counts)
        trend.append(
            OccupancyTrendPoint(
                month=month,
                tenant_count=counts["tenant"],
                owner_occupied_count=counts["owner_occupied"],
                investor_unknown_count=counts["investor_unknown"],
                total=sum(counts.values()),
            )
        )
    return OccupancyTrendsResponse(building_id=building_id, trend=trend)


# ── GET /occupancy/summary ────────────────────────────────────────────────────
@router.get("/summary", response_model=OccupancySummaryResponse)
async def get_occupancy_summary(
        current_user: dict = Depends(_require_occupancy_access),
        building_id: str = Depends(get_current_building),
):
    """Aggregate occupancy counts and percentages for the building."""
    _require_view_role(current_user)
    if await resolve_read_source(building_id, "occupancy") == DataSource.postgres:
        return await _pg_occupancy_summary(building_id)

    pipeline = [
        {"$match": {"building_id": building_id}},
        {
            "$group": {
                "_id": "$status",
                "count": {"$sum": 1},
                "avg_confidence": {"$avg": "$confidence"},
            }
        },
    ]
    rows = await db.occupancy_status.aggregate(pipeline).to_list(length=10)

    counts = {"tenant": 0, "owner_occupied": 0, "investor_unknown": 0}
    confidence_sum = 0.0
    total = 0

    for row in rows:
        status = row["_id"]
        cnt = row["count"]
        counts[status] = counts.get(status, 0) + cnt
        confidence_sum += row["avg_confidence"] * cnt
        total += cnt

    avg_confidence = round(confidence_sum / total, 4) if total else 0.0

    # Find most recent computation timestamp
    latest = await db.occupancy_status.find_one(
        {"building_id": building_id},
        {"_id": 0, "updated_at": 1},
        sort=[("updated_at", -1)],
    )

    return OccupancySummaryResponse(
        building_id=building_id,
        total_lots=total,
        tenant_count=counts["tenant"],
        owner_occupied_count=counts["owner_occupied"],
        investor_unknown_count=counts["investor_unknown"],
        tenant_pct=round(counts["tenant"] / total * 100, 1) if total else 0.0,
        owner_occupied_pct=round(counts["owner_occupied"] / total * 100, 1) if total else 0.0,
        investor_unknown_pct=round(counts["investor_unknown"] / total * 100, 1) if total else 0.0,
        avg_confidence=avg_confidence,
        last_computed_at=latest["updated_at"] if latest else None,
    )


# ── GET /occupancy/lots ───────────────────────────────────────────────────────
@router.get("/lots", response_model=List[OccupancyLotResponse])
async def get_occupancy_lots(
        current_user: dict = Depends(_require_occupancy_access),
        building_id: str = Depends(get_current_building),
):
    """Return per-lot occupancy classification, sorted by lot number."""
    _require_view_role(current_user)
    if await resolve_read_source(building_id, "occupancy") == DataSource.postgres:
        return await _pg_occupancy_lots(building_id)

    cursor = db.occupancy_status.find(
        {"building_id": building_id},
        {"_id": 0, "id": 0, "building_id": 0, "created_at": 0},
        sort=[("lot_number", 1)],
    )
    results = []
    async for doc in cursor:
        results.append(OccupancyLotResponse(**doc))
    return results


# ── GET /occupancy/trends ─────────────────────────────────────────────────────
@router.get("/trends", response_model=OccupancyTrendsResponse)
async def get_occupancy_trends(
        current_user: dict = Depends(_require_occupancy_access),
        building_id: str = Depends(get_current_building),
):
    """Return monthly occupancy counts for the past 12 months.

    Trend data is derived from a monthly snapshot collection (occupancy_snapshots).
    If no snapshots exist, returns the current status repeated as a flat baseline.
    """
    _require_view_role(current_user)
    if await resolve_read_source(building_id, "occupancy") == DataSource.postgres:
        return await _pg_occupancy_trends(building_id)

    # Try the snapshots collection first (populated by nightly cron)
    twelve_months_ago = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m")

    pipeline = [
        {
            "$match": {
                "building_id": building_id,
                "month": {"$gte": twelve_months_ago},
            }
        },
        {"$sort": {"month": 1}},
        {
            "$project": {
                "_id": 0,
                "month": 1,
                "tenant_count": 1,
                "owner_occupied_count": 1,
                "investor_unknown_count": 1,
                "total": 1,
            }
        },
    ]
    snapshots = await db.occupancy_snapshots.aggregate(pipeline).to_list(length=24)

    if snapshots:
        trend = [OccupancyTrendPoint(**s) for s in snapshots]
    else:
        # Fallback: derive from current status and repeat for past 12 months
        pipeline_current = [
            {"$match": {"building_id": building_id}},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]
        rows = await db.occupancy_status.aggregate(pipeline_current).to_list(length=10)
        counts = {"tenant": 0, "owner_occupied": 0, "investor_unknown": 0}
        for row in rows:
            counts[row["_id"]] = row["count"]
        total = sum(counts.values())

        now = datetime.now(timezone.utc)
        trend = []
        for months_back in range(11, -1, -1):
            d = now - timedelta(days=months_back * 30)
            trend.append(OccupancyTrendPoint(
                month=d.strftime("%Y-%m"),
                tenant_count=counts["tenant"],
                owner_occupied_count=counts["owner_occupied"],
                investor_unknown_count=counts["investor_unknown"],
                total=total,
            ))

    return OccupancyTrendsResponse(building_id=building_id, trend=trend)


# ── POST /occupancy/recompute ─────────────────────────────────────────────────
@router.post("/recompute", response_model=dict)
async def trigger_recompute(
        current_user: dict = Depends(_require_occupancy_access),
        building_id: str = Depends(get_current_building),
):
    """Manually trigger occupancy recompute for the building.

    Takes ~1–5 seconds for a typical 87-lot building. Returns summary.
    """
    _require_admin_role(current_user)
    if await resolve_read_source(building_id, "occupancy") == DataSource.postgres:
        raise HTTPException(
            status_code=409,
            detail=(
                "Occupancy reads are PostgreSQL-primary. Run "
                "backend/scripts/bootstrap_postgres_occupancy_snapshot.py to refresh the PostgreSQL snapshot."
            ),
        )

    result = await recompute_building_occupancy(building_id=building_id, db=db)

    # Write a snapshot for trend tracking
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    await db.occupancy_snapshots.update_one(
        {"building_id": building_id, "month": month_key},
        {"$set": {
            "building_id": building_id,
            "month": month_key,
            "tenant_count": result.get("tenant", 0),
            "owner_occupied_count": result.get("owner_occupied", 0),
            "investor_unknown_count": result.get("investor_unknown", 0),
            "total": result.get("total_lots", 0),
            "updated_at": datetime.now(timezone.utc),
        }},
        upsert=True,
    )

    logger.info(
        f"[occupancy] Manual recompute by {current_user.get('email', '?')} "
        f"for building {building_id}: {result}"
    )
    return result
