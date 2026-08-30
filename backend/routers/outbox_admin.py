# @featuretrace:financial_core — Super-admin review endpoints for outbox dead-letter rows.
# Layer: router
# Data flow: core.outbox → /admin/outbox/dead-letter → super-admin review UI/API (building-scoped).
# Related: backend/workers/outbox_relay.py
#          backend/services/financial_core/adapters/db_postgres/outbox_repo.py
#          backend/server.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from db_postgres.session import async_session_context
from utils.auth import effective_role, get_current_user
from workers.outbox_relay import DEAD_LETTER_PREFIX, MAX_RETRIES

router = APIRouter(prefix="/admin/outbox", tags=["Outbox Admin"])

BYPASS_UUID = "00000000-0000-0000-0000-000000000000"


def _require_super_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Generated function header.

    Function: _require_super_admin
    Path: backend/routers/outbox_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if effective_role(current_user) != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin access required")
    return current_user


def _serialize_dead_letter_row(row: Any) -> dict[str, Any]:
    """Generated function header.

    Function: _serialize_dead_letter_row
    Path: backend/routers/outbox_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    def _as_iso(value: Optional[datetime]) -> Optional[str]:
        """Generated function header.

        Function: _as_iso
        Path: backend/routers/outbox_admin.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return value.isoformat() if isinstance(value, datetime) else value

    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id) if row.tenant_id else None,
        "scheme_id": str(row.scheme_id) if row.scheme_id else None,
        "event_type": row.event_type,
        "aggregate_id": str(row.aggregate_id) if row.aggregate_id else None,
        "aggregate_type": row.aggregate_type,
        "attempts": row.attempts or 0,
        "last_error": row.last_error,
        "created_at": _as_iso(row.created_at),
        "published_at": _as_iso(getattr(row, "published_at", None)),
    }


@router.get("/dead-letter")
async def list_dead_letter_rows(
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=100),
        current_user: dict = Depends(_require_super_admin),
):
    """Generated function header.

    Function: list_dead_letter_rows
    Path: backend/routers/outbox_admin.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    del current_user
    offset = (page - 1) * page_size

    async with async_session_context() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": BYPASS_UUID},
        )
        # Mirror the worker's dead-letter definition: prefix OR max attempts reached.
        _dead_letter_filter = (
            "published_at IS NULL "
            "  AND (COALESCE(last_error, '') LIKE :prefix OR COALESCE(attempts, 0) >= :max_retries)"
        )
        total = (
            await session.execute(
                text(f"SELECT COUNT(*) FROM core.outbox WHERE {_dead_letter_filter}"),
                {"prefix": f"{DEAD_LETTER_PREFIX}%", "max_retries": MAX_RETRIES},
            )
        ).scalar_one()
        rows = (
            await session.execute(
                text(
                    "SELECT id, tenant_id, scheme_id, event_type, aggregate_id, aggregate_type, "
                    "       attempts, last_error, created_at, published_at "
                    f"FROM core.outbox WHERE {_dead_letter_filter} "
                    "ORDER BY created_at DESC "
                    "LIMIT :limit OFFSET :offset"
                ),
                {
                    "prefix": f"{DEAD_LETTER_PREFIX}%",
                    "max_retries": MAX_RETRIES,
                    "limit": page_size,
                    "offset": offset,
                },
            )
        ).fetchall()

    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [_serialize_dead_letter_row(row) for row in rows],
    }


# Thresholds are deliberately conservative and log-based (no new dashboard/alerting framework
# per GAP-FIN-061's own "lightweight check" scope) -- a value to poll from an existing external
# monitor (cron + log grep, or a future proper alert) rather than a new subsystem.
_STALE_UNPUBLISHED_AGE_MINUTES = 30


@router.get("/health")
async def outbox_health(current_user: dict = Depends(_require_super_admin)):
    """Lightweight core.outbox health snapshot (GAP-FIN-061 monitoring item).

    Reports unpublished/dead-letter counts and the age of the oldest still-pending row, so "the
    relay is healthy" can be verified directly instead of inferred from absence of complaints --
    the exact blind spot that let 15,256 events dead-letter silently for months (2026-05-04 to
    2026-08-09) before anyone noticed.
    """
    del current_user
    async with async_session_context() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": BYPASS_UUID},
        )
        row = (
            await session.execute(
                text(
                    "SELECT "
                    "  COUNT(*) FILTER (WHERE published_at IS NULL) AS unpublished, "
                    "  COUNT(*) FILTER ("
                    "    WHERE published_at IS NULL AND ("
                    "      COALESCE(last_error, '') LIKE :prefix OR COALESCE(attempts, 0) >= :max_retries"
                    "    )"
                    "  ) AS dead_lettered, "
                    "  MIN(created_at) FILTER (WHERE published_at IS NULL) AS oldest_unpublished_at "
                    "FROM core.outbox"
                ),
                {"prefix": f"{DEAD_LETTER_PREFIX}%", "max_retries": MAX_RETRIES},
            )
        ).fetchone()

    oldest = row.oldest_unpublished_at
    age_minutes = None
    if oldest is not None:
        age_minutes = (datetime.now(tz=oldest.tzinfo) - oldest).total_seconds() / 60

    return {
        "unpublished": row.unpublished,
        "dead_lettered": row.dead_lettered,
        "oldest_unpublished_at": oldest.isoformat() if oldest else None,
        "oldest_unpublished_age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "stale": age_minutes is not None and age_minutes > _STALE_UNPUBLISHED_AGE_MINUTES,
        "stale_threshold_minutes": _STALE_UNPUBLISHED_AGE_MINUTES,
    }
