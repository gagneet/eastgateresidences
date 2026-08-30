from __future__ import annotations

"""
Owner resolution service.

Single source of truth for owner name/email lookups during the cutover:
  1. MongoDB legacy path remains the production contract while shadow soak runs.
  2. PostgreSQL owner_read_service becomes the primary read path only when
     owner_read_pg_enabled is on and identity_core is permitted by the cutover
     control plane.

Usage:
    from services.owner_service import get_owner_info, get_all_unit_owners

All functions require building_id — never omit it (multi-tenancy rule).
"""

import logging
from datetime import date, datetime
from typing import Any

from database import db
from services.cutover_config_service import (
    FINANCIAL_SHADOW_READS_ENABLED,
    OWNER_READ_PG_ENABLED,
    is_cutover_feature_enabled,
)
from services.domain_source_guard import require_domain_source
from services.owner_read_service import OwnerDTO, OwnerReadService
from services.ownership_shadow_read_service import compare_owner_payloads
from utils.name_utils import format_owner_names  # noqa: F401 — re-exported for callers

logger = logging.getLogger(__name__)

OWNER_EQUIVALENT_USER_ROLES = {
    "owner",
    "strata_admin",
    "ec_member",
    "strata_manager",
    "super_admin",
}

_UNITS_OWNER_PROJECTION = {
    "_id": 0,
    "unit_number": 1,
    "owner_name": 1,
    "owner_email": 1,
    "owner_name_b": 1,
    "owner_email_b": 1,
}

_owner_read_service = OwnerReadService()

# method_name (as passed to _run_owner_shadow) -> shadow-read route_key. Kept in lockstep
# with identity_shadow_read_service.ROUTE_READ_STRATEGY and
# phase_d_shadow_status.DOMAIN_CONTRACT_ROUTES["identity_core"] — update all three together.
_OWNER_METHOD_ROUTE_KEYS = {
    "get_owner_info": "ownership.owner.current",
    "get_all_unit_owners": "ownership.owner.all_units",
    "resolve_agm_voters": "ownership.owner.agm_voters",
    "is_user_current_owner_of_unit": "ownership.owner.membership_check",
}


async def _owner_read_runtime_state(building_id: str) -> tuple[bool, bool]:
    """Return (use_pg_owner_reads, run_shadow_reads) for this building.

    The feature toggles only say the code path exists. The identity_core
    control-plane row decides whether the building/domain is allowed to use it.
    """
    pg_toggle_enabled = await is_cutover_feature_enabled(building_id, OWNER_READ_PG_ENABLED)
    shadow_toggle_enabled = await is_cutover_feature_enabled(building_id, FINANCIAL_SHADOW_READS_ENABLED)

    use_pg = False
    if pg_toggle_enabled:
        decision = await require_domain_source(
            domain="identity_core",
            building_id=building_id,
            operation="read",
            requested_source="postgres",
        )
        use_pg = decision.postgres_allowed

    shadow_reads = False
    if shadow_toggle_enabled:
        decision = await require_domain_source(
            domain="identity_core",
            building_id=building_id,
            operation="shadow_read",
        )
        shadow_reads = decision.shadow_enabled

    return use_pg, shadow_reads


def _is_owner_candidate(mapping: dict, user: dict | None) -> bool:
    """Return whether a Mongo user_units row should count as an owner link."""
    if mapping.get("role_at_unit") == "owner":
        return True
    return mapping.get("role_at_unit") in (None, "") and (user or {}).get("role") in OWNER_EQUIVALENT_USER_ROLES


def _mapping_sort_key(mapping: dict, user: dict | None) -> tuple:
    """Rank Mongo owner links so the primary display owner is deterministic."""
    return (
        0 if mapping.get("role_at_unit") == "owner" else 1,
        0 if mapping.get("is_primary") else 1,
        ((user or {}).get("full_name") or "").lower(),
        ((user or {}).get("email") or "").lower(),
    )


def _owner_row_sort_key(owner: OwnerDTO) -> tuple:
    """Rank PG ownership rows to match Mongo owner presentation ordering."""
    return (
        0 if owner.is_primary_owner else 1,
        -float(owner.ownership_share),
        (owner.email or "").lower(),
        owner.full_name.lower(),
    )


def _record_from_owner_rows(rows: list[OwnerDTO]) -> dict[str, Any]:
    """Convert current PG ownership rows into the legacy owner_service payload."""
    if not rows:
        return {
            "owner_name": "Unknown",
            "owner_email": None,
            "owner_id": None,
            "owner_name_b": None,
            "owner_email_b": None,
            "co_owner_name": None,
            "co_owner_email": None,
            "source": "postgres_not_found",
        }

    ranked = sorted(rows, key=_owner_row_sort_key)
    primary = ranked[0]
    secondary = ranked[1] if len(ranked) > 1 else None
    return {
        "owner_name": primary.full_name or "Unknown",
        "owner_email": primary.email,
        "owner_id": primary.user_id,
        "owner_name_b": secondary.full_name if secondary else None,
        "owner_email_b": secondary.email if secondary else None,
        "co_owner_name": secondary.full_name if secondary else None,
        "co_owner_email": secondary.email if secondary else None,
        "source": "postgres_owner_read",
    }


def _group_owner_rows(rows: list[OwnerDTO]) -> dict[str, dict[str, Any]]:
    """Group PG owner rows by unit number for the all-units owner lookup."""
    grouped: dict[str, list[OwnerDTO]] = {}
    for owner in rows:
        grouped.setdefault(owner.unit_number, []).append(owner)
    return {
        unit_number: _record_from_owner_rows(owner_rows)
        for unit_number, owner_rows in grouped.items()
    }


async def _pg_get_owner_info(unit_number: str, building_id: str) -> dict[str, Any]:
    """Resolve one unit's current owner presentation from PostgreSQL."""
    rows = await _owner_read_service.list_owners_by_lot(building_id, unit_number)
    return _record_from_owner_rows(rows)


async def _pg_get_all_unit_owners(building_id: str) -> dict[str, dict[str, Any]]:
    """Resolve all current owner presentations for one building from PostgreSQL."""
    rows = await _owner_read_service.list_owners_for_scheme(building_id)
    return _group_owner_rows(rows)


async def _pg_resolve_agm_voters(building_id: str, as_at_date: date) -> list[dict[str, Any]]:
    """Resolve AGM voter rows from PG ownership periods as at the supplied date."""
    voters = await _owner_read_service.resolve_agm_voters(building_id, as_at_date)
    return [voter.to_dict() for voter in voters]


async def _pg_is_user_current_owner_of_unit(
    building_id: str, user_id: str, unit_identifier: str, as_at_date: date
) -> bool:
    """Return PG's current-owner decision for one user/unit/date tuple."""
    return await _owner_read_service.is_user_current_owner_of_unit(
        building_id, user_id, unit_identifier, as_at_date
    )


async def _mongo_is_user_current_owner_of_unit(
    building_id: str, user_id: str, unit_identifier: str, as_at_date: date
) -> bool:
    """Mongo equivalent of the PG existence check.

    Targeted (user_id, unit_number, building_id) lookup against ``user_units`` —
    avoids scanning the full owner list for the building. Mirrors the role
    qualifiers used by ``_mongo_resolve_agm_voters`` so the answers stay aligned
    during shadow-read soak.
    """
    candidates = await db.user_units.find(
        {
            "building_id": building_id,
            "user_id": user_id,
            "unit_number": unit_identifier,
            "is_active": True,
            "$or": [
                {"role_at_unit": "owner"},
                {"role_at_unit": {"$exists": False}},
                {"role_at_unit": None},
            ],
        },
        {"_id": 0, "user_id": 1, "is_primary": 1, "role_at_unit": 1, "start_date": 1,
         "actual_end_date": 1, "end_date": 1, "created_at": 1},
    ).to_list(None)
    if not candidates:
        return False

    user = await db.users.find_one(
        {"id": user_id},
        {"_id": 0, "id": 1, "role": 1, "full_name": 1, "email": 1},
    )
    if not user:
        return False

    for link in candidates:
        if not _is_owner_candidate(link, user):
            continue
        start_value = str(link.get("start_date") or link.get("created_at") or "")[:10]
        end_value = str(link.get("actual_end_date") or link.get("end_date") or "")[:10]
        try:
            starts_on = date.fromisoformat(start_value) if start_value else None
        except ValueError:
            starts_on = None
        try:
            ends_on = date.fromisoformat(end_value) if end_value else None
        except ValueError:
            ends_on = None
        if starts_on and starts_on > as_at_date:
            continue
        if ends_on and ends_on <= as_at_date:
            continue
        return True
    return False


async def _run_owner_shadow(
    *,
    building_id: str,
    method_name: str,
    query_params: dict[str, Any],
    mongodb_value: Any,
    postgres_value: Any,
    is_test_data: bool = False,
) -> None:
    """Compare Mongo vs Postgres owner-lookup results and record to core.shadow_diffs.

    Routes through ownership_shadow_read_service.compare_owner_payloads(), which hashes
    PII (name/email) before any storage or comparison — see that module's redaction
    contract. query_params is accepted for API-compatibility with existing call sites but
    is not currently persisted (the 4 owner_service.py methods' query params — unit_number,
    user_id, as_at_date — are not PII, but aren't needed for the comparison itself either).
    """
    route_key = _OWNER_METHOD_ROUTE_KEYS.get(method_name)
    if route_key is None:
        logger.warning("Owner shadow: no route_key mapped for method_name=%r", method_name)
        return
    try:
        await compare_owner_payloads(
            building_id=building_id,
            route_key=route_key,
            mongo_value=mongodb_value,
            pg_value=postgres_value,
            is_test_data=is_test_data,
        )
    except Exception as exc:
        logger.warning(
            "Owner shadow validation failed for %s on building %s: %s",
            method_name,
            building_id,
            exc,
        )


async def _mongo_get_owner_info(unit_number: str, building_id: str) -> dict:
    """
    Return canonical owner name and email for a unit.

    Resolution order:
      1. Active primary user_units record → linked users document
      2. Any active user_units record → linked users document
      3. Fallback: units.owner_name / units.owner_email (legacy imported data)
      4. Final fallback: {"owner_name": "Unknown", "owner_email": None}
    """
    mapping = await db.user_units.find_one(
        {
            "unit_number": unit_number,
            "building_id": building_id,
            "role_at_unit": "owner",
            "is_active": True,
            "is_primary": True,
        }
    )
    if not mapping:
        mapping = await db.user_units.find_one(
            {
                "unit_number": unit_number,
                "building_id": building_id,
                "role_at_unit": "owner",
                "is_active": True,
            }
        )

    unit = await db.units.find_one(
        {"unit_number": unit_number, "building_id": building_id},
        _UNITS_OWNER_PROJECTION,
    )
    leg = unit or {}

    if mapping:
        user = await db.users.find_one(
            {"id": mapping["user_id"]},
            {"_id": 0, "full_name": 1, "email": 1, "id": 1, "role": 1},
        )
        if user and _is_owner_candidate(mapping, user):
            return {
                "owner_name": user.get("full_name") or "Unknown",
                "owner_email": user.get("email"),
                "owner_id": user.get("id"),
                "owner_name_b": leg.get("owner_name_b"),
                "owner_email_b": leg.get("owner_email_b"),
                "co_owner_name": leg.get("owner_name_b"),
                "co_owner_email": leg.get("owner_email_b"),
                "source": "user_units",
            }

    legacy_owner_mappings = await db.user_units.find(
        {
            "unit_number": unit_number,
            "building_id": building_id,
            "is_active": True,
            "$or": [
                {"role_at_unit": {"$exists": False}},
                {"role_at_unit": None},
            ],
        },
        {"_id": 0, "user_id": 1, "is_primary": 1, "role_at_unit": 1},
    ).sort([("is_primary", -1), ("updated_at", -1), ("created_at", -1)]).to_list(10)

    if legacy_owner_mappings:
        legacy_user_ids = [candidate["user_id"] for candidate in legacy_owner_mappings if candidate.get("user_id")]
        legacy_users = await db.users.find(
            {"id": {"$in": legacy_user_ids}},
            {"_id": 0, "full_name": 1, "email": 1, "id": 1, "role": 1},
        ).to_list(len(legacy_user_ids))
        legacy_users_by_id = {user["id"]: user for user in legacy_users}
        for legacy_mapping in legacy_owner_mappings:
            user = legacy_users_by_id.get(legacy_mapping.get("user_id"))
            if not user or not _is_owner_candidate(legacy_mapping, user):
                continue
            return {
                "owner_name": user.get("full_name") or "Unknown",
                "owner_email": user.get("email"),
                "owner_id": user.get("id"),
                "owner_name_b": leg.get("owner_name_b"),
                "owner_email_b": leg.get("owner_email_b"),
                "co_owner_name": leg.get("owner_name_b"),
                "co_owner_email": leg.get("owner_email_b"),
                "source": "user_units_legacy_role_backfill",
            }

    if leg:
        return {
            "owner_name": leg.get("owner_name") or "Unknown",
            "owner_email": leg.get("owner_email"),
            "owner_id": None,
            "owner_name_b": leg.get("owner_name_b"),
            "owner_email_b": leg.get("owner_email_b"),
            "co_owner_name": leg.get("owner_name_b"),
            "co_owner_email": leg.get("owner_email_b"),
            "source": "units_legacy",
        }

    return {
        "owner_name": "Unknown",
        "owner_email": None,
        "owner_id": None,
        "owner_name_b": None,
        "owner_email_b": None,
        "co_owner_name": None,
        "co_owner_email": None,
        "source": "not_found",
    }


async def _mongo_get_all_unit_owners(building_id: str) -> dict[str, dict]:
    """Resolve all owner presentations from Mongo's canonical legacy records."""
    user_unit_docs = await db.user_units.find(
        {
            "building_id": building_id,
            "is_active": True,
            "$or": [
                {"role_at_unit": "owner"},
                {"role_at_unit": {"$exists": False}},
                {"role_at_unit": None},
            ],
        }
    ).to_list(None)

    user_ids = list({doc["user_id"] for doc in user_unit_docs if doc.get("user_id")})
    users_by_id: dict[str, dict] = {}
    if user_ids:
        user_docs = await db.users.find(
            {"id": {"$in": user_ids}}, {"_id": 0, "id": 1, "full_name": 1, "email": 1, "role": 1}
        ).to_list(None)
        users_by_id = {u["id"]: u for u in user_docs}

    unit_to_mapping: dict[str, dict] = {}
    for doc in user_unit_docs:
        user = users_by_id.get(doc.get("user_id"))
        if not user or not _is_owner_candidate(doc, user):
            continue
        unit_number = doc["unit_number"]
        existing = unit_to_mapping.get(unit_number)
        if not existing:
            unit_to_mapping[unit_number] = doc
            continue
        existing_user = users_by_id.get(existing.get("user_id"))
        if _mapping_sort_key(doc, user) < _mapping_sort_key(existing, existing_user):
            unit_to_mapping[unit_number] = doc

    unit_docs = await db.units.find(
        {"building_id": building_id},
        _UNITS_OWNER_PROJECTION,
    ).to_list(None)
    legacy_by_unit: dict[str, dict] = {u["unit_number"]: u for u in unit_docs}

    result: dict[str, dict] = {}
    for unit_doc in unit_docs:
        unit_number = unit_doc["unit_number"]
        mapping = unit_to_mapping.get(unit_number)
        leg = legacy_by_unit.get(unit_number, {})
        if mapping:
            user = users_by_id.get(mapping["user_id"], {})
            result[unit_number] = {
                "owner_name": user.get("full_name") or "Unknown",
                "owner_email": user.get("email"),
                "owner_id": user.get("id"),
                "owner_name_b": leg.get("owner_name_b"),
                "owner_email_b": leg.get("owner_email_b"),
                "co_owner_name": leg.get("owner_name_b"),
                "co_owner_email": leg.get("owner_email_b"),
                "source": "user_units",
            }
        else:
            result[unit_number] = {
                "owner_name": leg.get("owner_name") or "Unknown",
                "owner_email": leg.get("owner_email"),
                "owner_id": None,
                "owner_name_b": leg.get("owner_name_b"),
                "owner_email_b": leg.get("owner_email_b"),
                "co_owner_name": leg.get("owner_name_b"),
                "co_owner_email": leg.get("owner_email_b"),
                "source": "units_legacy",
            }

    return result


async def _mongo_resolve_agm_voters(building_id: str, as_at_date: date) -> list[dict[str, Any]]:
    """Resolve AGM voter rows from Mongo user_units as at the supplied date."""
    owner_links = await db.user_units.find(
        {
            "building_id": building_id,
            "is_active": True,
            "$or": [
                {"role_at_unit": "owner"},
                {"role_at_unit": {"$exists": False}},
                {"role_at_unit": None},
            ],
        },
        {"_id": 0},
    ).to_list(None)
    user_ids = [link["user_id"] for link in owner_links if link.get("user_id")]
    users = await db.users.find(
        {"id": {"$in": user_ids}},
        {"_id": 0, "id": 1, "full_name": 1, "email": 1, "role": 1},
    ).to_list(len(user_ids) or 1)
    users_by_id = {user["id"]: user for user in users}
    units = await db.units.find(
        {"building_id": building_id},
        {"_id": 0, "unit_number": 1, "lot_number": 1, "entitlement": 1, "unit_entitlement": 1},
    ).to_list(None)
    units_by_unit = {str(unit.get("unit_number")): unit for unit in units}

    result: list[dict[str, Any]] = []
    for link in owner_links:
        start_value = str(link.get("start_date") or link.get("created_at") or "")[:10]
        end_value = str(link.get("actual_end_date") or link.get("end_date") or "")[:10]
        try:
            starts_on = date.fromisoformat(start_value) if start_value else None
        except ValueError:
            starts_on = None
        try:
            ends_on = date.fromisoformat(end_value) if end_value else None
        except ValueError:
            ends_on = None

        if starts_on and starts_on > as_at_date:
            continue
        if ends_on and ends_on <= as_at_date:
            continue

        user = users_by_id.get(link.get("user_id"))
        if not user or not _is_owner_candidate(link, user):
            continue

        unit_number = str(link.get("unit_number") or "")
        unit = units_by_unit.get(unit_number, {})
        result.append(
            {
                "building_id": building_id,
                "lot_id": unit_number,
                "lot_number": str(unit.get("lot_number") or unit_number),
                "unit_number": unit_number,
                "party_id": None,
                "user_id": user.get("id"),
                "email": user.get("email"),
                "full_name": user.get("full_name") or "Unknown",
                "is_primary_owner": bool(link.get("is_primary", True)),
                "ownership_share": "1.0",
                "entitlement_units": str(unit.get("unit_entitlement") or unit.get("entitlement") or 0),
                "valid_from": starts_on.isoformat() if starts_on else None,
                "valid_to": ends_on.isoformat() if ends_on else None,
            }
        )

    return sorted(
        result,
        key=lambda item: (
            item.get("unit_number") or "",
            item.get("lot_number") or "",
            0 if item.get("is_primary_owner") else 1,
            (item.get("email") or "").lower(),
            (item.get("full_name") or "").lower(),
        ),
    )


async def get_owner_info(unit_number: str, building_id: str) -> dict:
    """Return one unit's owner payload from the cutover-approved source."""
    use_pg, shadow_reads = await _owner_read_runtime_state(building_id)

    if use_pg:
        result = await _pg_get_owner_info(unit_number, building_id)
        if shadow_reads:
            await _run_owner_shadow(
                building_id=building_id,
                method_name="get_owner_info",
                query_params={"unit_number": unit_number},
                mongodb_value=await _mongo_get_owner_info(unit_number, building_id),
                postgres_value=result,
            )
        return result

    result = await _mongo_get_owner_info(unit_number, building_id)
    if shadow_reads:
        await _run_owner_shadow(
            building_id=building_id,
            method_name="get_owner_info",
            query_params={"unit_number": unit_number},
            mongodb_value=result,
            postgres_value=await _pg_get_owner_info(unit_number, building_id),
        )
    return result


async def get_all_unit_owners(building_id: str) -> dict[str, dict]:
    """Return all owner payloads from the cutover-approved source."""
    use_pg, shadow_reads = await _owner_read_runtime_state(building_id)

    if use_pg:
        result = await _pg_get_all_unit_owners(building_id)
        if shadow_reads:
            await _run_owner_shadow(
                building_id=building_id,
                method_name="get_all_unit_owners",
                query_params={},
                mongodb_value=await _mongo_get_all_unit_owners(building_id),
                postgres_value=result,
            )
        return result

    result = await _mongo_get_all_unit_owners(building_id)
    if shadow_reads:
        await _run_owner_shadow(
            building_id=building_id,
            method_name="get_all_unit_owners",
            query_params={},
            mongodb_value=result,
            postgres_value=await _pg_get_all_unit_owners(building_id),
        )
    return result


async def resolve_agm_voters(building_id: str, as_at_date: date) -> list[dict[str, Any]]:
    """Return AGM voter rows from the cutover-approved owner source."""
    use_pg, shadow_reads = await _owner_read_runtime_state(building_id)

    if use_pg:
        result = await _pg_resolve_agm_voters(building_id, as_at_date)
        if shadow_reads:
            await _run_owner_shadow(
                building_id=building_id,
                method_name="resolve_agm_voters",
                query_params={"as_at_date": as_at_date.isoformat()},
                mongodb_value=await _mongo_resolve_agm_voters(building_id, as_at_date),
                postgres_value=result,
            )
        return result

    result = await _mongo_resolve_agm_voters(building_id, as_at_date)
    if shadow_reads:
        await _run_owner_shadow(
            building_id=building_id,
            method_name="resolve_agm_voters",
            query_params={"as_at_date": as_at_date.isoformat()},
            mongodb_value=result,
            postgres_value=await _pg_resolve_agm_voters(building_id, as_at_date),
        )
    return result


async def is_user_current_owner_of_unit(
    building_id: str,
    user_id: str,
    unit_identifier: str,
    as_at_date: date | None = None,
) -> bool:
    """Single-(user, unit) ownership existence check.

    Cheap alternative to ``resolve_agm_voters`` for vote eligibility and other
    BOLA gates that only need to verify one user/unit pair. Honours the same
    PostgreSQL/MongoDB cutover toggle as the rest of the owner read slice and
    runs the shadow comparison when shadow reads are enabled.
    """
    use_pg, shadow_reads = await _owner_read_runtime_state(building_id)
    as_at = as_at_date or date.today()
    query_params = {
        "user_id": user_id,
        "unit_identifier": unit_identifier,
        "as_at_date": as_at.isoformat(),
    }

    if use_pg:
        result = await _pg_is_user_current_owner_of_unit(building_id, user_id, unit_identifier, as_at)
        if shadow_reads:
            await _run_owner_shadow(
                building_id=building_id,
                method_name="is_user_current_owner_of_unit",
                query_params=query_params,
                mongodb_value=await _mongo_is_user_current_owner_of_unit(
                    building_id, user_id, unit_identifier, as_at
                ),
                postgres_value=result,
            )
        return result

    result = await _mongo_is_user_current_owner_of_unit(building_id, user_id, unit_identifier, as_at)
    if shadow_reads:
        await _run_owner_shadow(
            building_id=building_id,
            method_name="is_user_current_owner_of_unit",
            query_params=query_params,
            mongodb_value=result,
            postgres_value=await _pg_is_user_current_owner_of_unit(
                building_id, user_id, unit_identifier, as_at
            ),
        )
    return result


async def get_owner_name(unit_number: str, building_id: str) -> str:
    """Return the display owner name for a unit."""
    info = await get_owner_info(unit_number, building_id)
    return info["owner_name"]
