"""Postgres trial-request repository.

All trial request reads and writes go through this module.
Replaces the legacy MongoDB ``db.trial_requests`` collection (Task C+G).

Postgres table: ``core.trial_requests``
Enum:           ``core.trial_request_status``  (submitted | approved | rejected | expired)

Idempotency rule
----------------
A duplicate submission for the same contact_email while a ``submitted`` record
already exists returns the existing ``request_id`` rather than raising 409.
This is enforced by a partial unique index on ``(contact_email) WHERE status = 'submitted'``
created in migration 0028.

Field mapping from the legacy marketing form
--------------------------------------------
Legacy form field   → Postgres column
--------------------  ----------------
name (full name)    → contact_first_name (full name stored here; last_name = "")
company             → org_name
email               → contact_email
phone               → contact_phone
state               → jurisdiction  (mapped to compliance.jurisdiction_code enum)
lots                → stored in notes as "Lots: <value>"
message             → message
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from asyncpg import UniqueViolationError  # type: ignore[import]
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from db_postgres.session import async_session_context

logger = logging.getLogger(__name__)

# Valid jurisdiction codes that match compliance.jurisdiction_code enum
_JURISDICTION_MAP = {
    "act": "ACT", "nsw": "NSW", "vic": "VIC", "qld": "QLD",
    "sa": "SA", "wa": "WA", "tas": "TAS", "nt": "NT",
    # Friendly long names → code
    "australian capital territory": "ACT",
    "new south wales": "NSW",
    "victoria": "VIC",
    "queensland": "QLD",
    "south australia": "SA",
    "western australia": "WA",
    "tasmania": "TAS",
    "northern territory": "NT",
}
_DEFAULT_JURISDICTION = "NSW"

PG_STATUS_MAP = {
    # Legacy Mongo status → Postgres status (best-effort for patch endpoint)
    "new":              "submitted",
    "submitted":        "submitted",
    "contacted":        "submitted",
    "demo_scheduled":   "submitted",
    "converted":        "approved",
    "disqualified":     "rejected",
    "approved":         "approved",
    "rejected":         "rejected",
    "expired":          "expired",
}

# Statuses that are still "open" (same as status = 'submitted' in PG)
OPEN_PG_STATUSES = {"submitted"}
CLOSED_PG_STATUSES = {"approved", "rejected", "expired"}
ALL_PG_STATUSES = OPEN_PG_STATUSES | CLOSED_PG_STATUSES


def _map_jurisdiction(state: str) -> str:
    """Map form 'state' value to a valid compliance.jurisdiction_code."""
    if not state:
        return _DEFAULT_JURISDICTION
    normalised = state.strip().lower()
    return _JURISDICTION_MAP.get(normalised, _DEFAULT_JURISDICTION)


def _row_to_dict(row) -> dict:
    """Convert a SQLAlchemy Row to a plain dict."""
    if row is None:
        return {}
    return dict(row._mapping)


# ── Create ─────────────────────────────────────────────────────────────────────

async def create_trial_request(
    *,
    org_name: str,
    contact_name: str,
    contact_email: str,
    contact_phone: str = "",
    jurisdiction: str,
    message: str = "",
    notes: str = "",
    source_ip: Optional[str] = None,
) -> dict:
    """Insert a new trial request.

    Returns the full row dict.
    On duplicate (same email + open submission), returns the existing row dict
    with an extra key ``_duplicate = True``.
    """
    async with async_session_context() as session:
        try:
            result = await session.execute(
                text("""
                    INSERT INTO core.trial_requests
                        (org_name, contact_first_name, contact_last_name,
                         contact_email, contact_phone, jurisdiction,
                         message, notes, source_ip)
                    VALUES
                        (:org_name, :first_name, '',
                         :contact_email, :contact_phone, CAST(:jurisdiction AS compliance.jurisdiction_code),
                         :message, :notes, CAST(:source_ip AS INET))
                    RETURNING *
                """),
                {
                    "org_name": org_name,
                    "first_name": contact_name,
                    "contact_email": contact_email,
                    "contact_phone": contact_phone or "",
                    "jurisdiction": jurisdiction,
                    "message": message or "",
                    "notes": notes or "",
                    "source_ip": source_ip,
                },
            )
            await session.commit()
            row = result.fetchone()
            return _row_to_dict(row)
        except IntegrityError as exc:
            await session.rollback()
            # Partial unique index violation → return existing open record
            if "trial_requests_email_open_unique" in str(exc.orig):
                logger.info("Duplicate trial request for %s — returning existing", contact_email)
                existing = await get_open_trial_request_by_email(contact_email)
                if existing:
                    existing["_duplicate"] = True
                    return existing
            raise


# ── Read ───────────────────────────────────────────────────────────────────────

async def get_open_trial_request_by_email(contact_email: str) -> dict | None:
    """Return the first open (status='submitted') record for this email, or None."""
    async with async_session_context() as session:
        result = await session.execute(
            text("""
                SELECT * FROM core.trial_requests
                WHERE contact_email = :email
                  AND status = 'submitted'
                ORDER BY submitted_at ASC
                LIMIT 1
            """),
            {"email": contact_email},
        )
        return _row_to_dict(result.fetchone()) or None


async def get_trial_request_by_id(request_id: str) -> dict | None:
    """Fetch a single trial request by primary key (no tenant scoping — global table)."""
    async with async_session_context() as session:
        result = await session.execute(
            text("SELECT * FROM core.trial_requests WHERE request_id = :rid"),
            {"rid": request_id},
        )
        row = result.fetchone()
        return _row_to_dict(row) if row else None


async def list_trial_requests(
    *,
    status: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 25,
) -> dict:
    """Paginated list with status counts.

    Returns:
        {"data": [...], "total": int, "counts": {status: int, "total": int}}
    """
    conditions: list[str] = []
    params: dict[str, Any] = {"offset": (page - 1) * limit, "limit": limit}

    if status and status in ALL_PG_STATUSES:
        conditions.append("status = CAST(:status AS core.trial_request_status)")
        params["status"] = status
    if from_date:
        conditions.append("submitted_at >= :from_date")
        params["from_date"] = from_date
    if to_date:
        conditions.append("submitted_at <= :to_date")
        params["to_date"] = to_date
    if search:
        conditions.append(
            "(contact_first_name ILIKE :search OR org_name ILIKE :search OR contact_email ILIKE :search)"
        )
        params["search"] = f"%{search.strip()}%"

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    async with async_session_context() as session:
        data_result = await session.execute(
            text(f"""
                SELECT request_id, submitted_at, status, org_name,
                       contact_first_name, contact_last_name, contact_email,
                       contact_phone, jurisdiction, message, notes,
                       reviewed_at, reject_reason, expires_at,
                       created_tenant_id, invitation_id
                FROM core.trial_requests
                {where}
                ORDER BY submitted_at DESC
                OFFSET :offset LIMIT :limit
            """),
            params,
        )
        rows = [_row_to_dict(r) for r in data_result.fetchall()]

        count_result = await session.execute(
            text(f"SELECT COUNT(*) FROM core.trial_requests {where}"),
            params,
        )
        total = count_result.scalar() or 0

        agg_result = await session.execute(
            text("SELECT status::text, COUNT(*) AS cnt FROM core.trial_requests GROUP BY status")
        )
        counts: dict[str, int] = {s: 0 for s in ALL_PG_STATUSES}
        for agg_row in agg_result.fetchall():
            s = agg_row[0]
            if s in counts:
                counts[s] = agg_row[1]
        counts["total"] = sum(v for k, v in counts.items() if k != "total")

    return {"data": rows, "total": total, "counts": counts}


# ── Update ─────────────────────────────────────────────────────────────────────

async def update_trial_request(request_id: str, *, notes: Optional[str] = None) -> dict | None:
    """Update mutable fields on an open trial request.

    Only ``notes`` is mutable via the PATCH endpoint.
    Status transitions use the dedicated approve/reject helpers.
    Returns the updated row, or None if not found.
    """
    async with async_session_context() as session:
        result = await session.execute(
            text("""
                UPDATE core.trial_requests
                SET notes = COALESCE(:notes, notes)
                WHERE request_id = :rid
                RETURNING *
            """),
            {"rid": request_id, "notes": notes},
        )
        await session.commit()
        row = result.fetchone()
        return _row_to_dict(row) if row else None


async def approve_trial_request(
    request_id: str,
    reviewed_by: str,
    created_tenant_id: Optional[str] = None,
    invitation_id: Optional[str] = None,
) -> dict | None:
    """Mark a trial request as approved; link tenant + invitation if provided."""
    async with async_session_context() as session:
        result = await session.execute(
            text("""
                UPDATE core.trial_requests
                SET status           = 'approved',
                    reviewed_at      = NOW(),
                    reviewed_by      = CAST(:reviewed_by AS UUID),
                    created_tenant_id = CAST(:tenant_id AS UUID),
                    invitation_id    = CAST(:invitation_id AS UUID)
                WHERE request_id = :rid
                  AND status = 'submitted'
                RETURNING *
            """),
            {
                "rid": request_id,
                "reviewed_by": reviewed_by,
                "tenant_id": created_tenant_id,
                "invitation_id": invitation_id,
            },
        )
        await session.commit()
        row = result.fetchone()
        return _row_to_dict(row) if row else None


async def reject_trial_request(
    request_id: str,
    reviewed_by: str,
    reason: str = "",
) -> dict | None:
    """Mark a trial request as rejected."""
    async with async_session_context() as session:
        result = await session.execute(
            text("""
                UPDATE core.trial_requests
                SET status        = 'rejected',
                    reviewed_at   = NOW(),
                    reviewed_by   = CAST(:reviewed_by AS UUID),
                    reject_reason = :reason
                WHERE request_id = :rid
                  AND status = 'submitted'
                RETURNING *
            """),
            {"rid": request_id, "reviewed_by": reviewed_by, "reason": reason},
        )
        await session.commit()
        row = result.fetchone()
        return _row_to_dict(row) if row else None
