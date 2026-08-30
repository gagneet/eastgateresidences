# @featuretrace:pg-migration — DEAD CODE: only referenced by routers/users.py, which is itself unreachable.
# Layer: service
# Data flow: none live — routers/users.py (the only caller) is never included into server.py's api_router.
# Related: backend/routers/users.py (dead)
#           backend/db_postgres/repos/identity_repo.py
# Toggle: users_pg_reads_enabled (defined, never evaluated on a live path)
#
# Confirmed 2026-07-14 (PostgreSQL shadow-read expansion, Phase D) — see
# docs/migration/phase-d-choices-mongodb-postgres.md. The live GET /users composite-read
# logic (Mongo primary + unconditional Postgres union, no toggle) lives directly in
# server.py and does not use this module. Preserved rather than deleted only because
# tests/backend/test_users_pg_service.py still imports it.

"""
PostgreSQL read service for users / building membership — UNREACHABLE, see module docstring above.

Provides a Postgres-first read path for GET /users that is gated by the
USERS_PG_READS_ENABLED feature toggle. The Mongo path in routers/users.py
remains the live production path until the cutover flag is enabled.

Data flow:
    GET /users
      → is_cutover_feature_enabled(building_id, USERS_PG_READS_ENABLED)
        → list_users_for_building(building_id)
          → identity_repo.list_active_users_for_scheme(scheme_number)
            → core.users + core.user_units + core.lots (Postgres RLS-scoped)
        → shadow-compare with Mongo count when FINANCIAL_SHADOW_READS_ENABLED
        → fallback to Mongo on any exception or empty PG result

Response shape: identical to the Mongo path (UserResponse Pydantic model).
Frontend sees no difference — no field additions or removals.

Normalisation notes:
- PG returns lot_numbers: list[str]. We map lot_numbers[0] → unit_number
  (strata buildings have one unit per owner in most cases; the full list is
  surfaced as lot_numbers for callers that need it).
- tenant_id is stripped before returning to match the Mongo shape.
- Fields absent from PG (e.g. is_name_flagged, flag_reason) default to
  None / False so user_to_response() does not KeyError.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _pg_row_to_user_dict(row: dict, building_id: str) -> dict[str, Any]:
    """Normalise a list_active_users_for_scheme row to the legacy user dict shape.

    The Mongo user document and the PG row share most field names but differ
    in a few areas — this function bridges the gap so user_to_response() works
    identically on both sources.
    """
    lot_numbers: list[str] = row.get("lot_numbers") or []
    # unit_number: first lot number (or None). Matches Mongo single-unit model.
    unit_number = lot_numbers[0] if lot_numbers else row.get("unit_number")

    effective_role = row.get("effective_role") or row.get("role") or "guest"

    return {
        # Core identity
        "id":               row.get("id"),
        "email":            row.get("email", ""),
        "full_name":        row.get("full_name") or "",
        "phone":            row.get("phone"),
        # Role / status
        "role":             row.get("role", "guest"),
        "ec_position":      row.get("ec_position"),
        "effective_role":   effective_role,
        "is_active":        bool(row.get("is_active", True)),
        "is_approved":      bool(row.get("is_approved", False)),
        "status":           row.get("status", "active"),
        # Building context — keep legacy string building_id for the response
        "building_id":      building_id,
        "unit_number":      unit_number,
        "lot_numbers":      lot_numbers,       # extra; stripped by user_to_response
        "relationship":     row.get("relationship"),  # extra; stripped by user_to_response
        # Security flags
        "is_name_flagged":  bool(row.get("is_name_flagged", False)),
        "flag_reason":      row.get("flag_reason"),
        "totp_enabled":     bool(row.get("totp_enabled", False)),
        # Timestamps
        "created_at":       row.get("created_at"),
        "last_login_at":    row.get("last_login_at"),
        "last_login_ip":    row.get("last_login_ip"),
        # Fields absent from PG path; defaults preserve user_to_response compat
        "is_test_data":     bool(row.get("is_test_data", False)),
        "permission_overrides": {},
        "password_hash":    "",
    }


async def list_users_for_building(
    building_id: str,
    role_filter: str | None = None,
    is_active_filter: bool | None = None,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Return users for a building from Postgres, normalised to legacy dict shape.

    Args:
        building_id: The building's scheme_number (e.g. "13195").
        role_filter: Optional role string to filter results.
        is_active_filter: Optional active-state filter.
        status_filter: "active" | "archived" | "all" | None (default active).

    Returns:
        List of user dicts matching the Mongo user_to_response() input shape.
        Returns an empty list if the PG scheme is not found.

    Raises:
        Exception: Any Postgres connectivity or query error — callers should
            catch and fall back to MongoDB.
    """
    from db_postgres.repos.identity_repo import list_active_users_for_scheme

    rows = await list_active_users_for_scheme(building_id)
    if not rows:
        return []

    users = [_pg_row_to_user_dict(row, building_id) for row in rows]

    # Apply the same filters as the Mongo path so callers get consistent results
    if role_filter:
        users = [u for u in users if u.get("role") == role_filter]

    if is_active_filter is not None:
        users = [u for u in users if bool(u.get("is_active", True)) == is_active_filter]

    if status_filter == "archived":
        users = [u for u in users if u.get("status") == "archived"]
    elif status_filter not in ("all", None):
        users = [u for u in users if u.get("status") == status_filter]
    elif status_filter is None:
        # Default: exclude archived (matches Mongo path default)
        users = [u for u in users if u.get("status") != "archived"]

    return users


async def shadow_compare_users(
    building_id: str,
    pg_users: list[dict],
    mongo_users: list[dict],
) -> None:
    """Log count and key-field divergences between PG and Mongo user lists.

    Does not raise — shadow comparisons are diagnostic only.
    """
    pg_count = len(pg_users)
    mongo_count = len(mongo_users)
    if pg_count != mongo_count:
        logger.warning(
            "users shadow-read divergence: building=%s pg_count=%d mongo_count=%d",
            building_id,
            pg_count,
            mongo_count,
        )
    else:
        logger.debug(
            "users shadow-read: building=%s counts match (%d)", building_id, pg_count
        )

    pg_ids = {u.get("id") for u in pg_users}
    mongo_ids = {u.get("id") for u in mongo_users}
    only_in_pg = pg_ids - mongo_ids
    only_in_mongo = mongo_ids - pg_ids
    if only_in_pg:
        logger.warning(
            "users shadow-read: building=%s users only in PG: %s",
            building_id,
            only_in_pg,
        )
    if only_in_mongo:
        logger.warning(
            "users shadow-read: building=%s users only in Mongo: %s",
            building_id,
            only_in_mongo,
        )
