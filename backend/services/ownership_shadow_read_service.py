# @featuretrace:pg-migration-shadow — Ownership shadow-read comparators, identity_core domain.
# Layer: service
# Data flow: owner_service.py's 4 lookup functions → compare_owner_payloads() →
#            shadow_read_service.run_shadow_compare() → core.shadow_diffs (building-scoped).
#            Mongo remains the response source at all times.
# Related: backend/services/owner_service.py
#          backend/services/owner_read_service.py
#          backend/services/shadow_read_service.py
#          docs/migration/shadow-correction-updates-plan-p1.md
"""Ownership shadow-read comparators — Phase D of the PostgreSQL shadow-read rollout.

Unlike identity.users.list (a deliberate Mongo+PG union), ownership lookups are a genuine
Mongo-primary/Postgres-shadow pair: OwnerReadService (Postgres) and the legacy Mongo
resolution chain answer the *same* question about the *same* unit/user, so a real
comparison is meaningful here (Pattern B in
docs/migration/phase-d-choices-mongodb-postgres.md).

Covers the 4 owner_service.py entry points: get_owner_info, get_all_unit_owners,
resolve_agm_voters, is_user_current_owner_of_unit.

Redaction contract: owner name/email are real PII. Never pass them raw into a FieldDiff —
this module hashes every name/email-shaped field before comparison and records only
whether the hashes matched, never the values themselves. Metadata-only fields and
cross-store identifiers such as owner_id are excluded from presentation parity.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from services.shadow_read_service import FieldDiff, ShadowCompareResult, run_shadow_compare

logger = logging.getLogger(__name__)

_DOMAIN = "identity_core"

# Field names treated as PII wherever they appear (top-level dict key, or a key inside a
# dict value of a list/dict-of-dicts payload) — hashed before ever being compared/stored.
_PII_FIELD_NAMES = {
    "owner_name", "owner_email", "owner_name_b", "owner_email_b",
    "co_owner_name", "co_owner_email", "full_name", "email", "name",
}

# Found 2026-07-20, tracing real ownership.owner.all_units mismatches against live data
# per docs/migration/strataos_updated_tasks_list_work-2026-07-22.md's Priority A2
# methodology: "source" is a self-describing tag naming which store answered
# ("user_units"/"units_legacy"/"not_found" in Mongo vs "postgres_owner_read"/
# "postgres_not_found" in Postgres) — the two vocabularies are disjoint by
# construction, so this field can never match and was previously included in the
# whole-payload equality check, guaranteeing a reported mismatch on every single
# call regardless of whether the actual owner data agreed. Stripped entirely.
_METADATA_FIELD_NAMES = {"source"}

# owner_id is deliberately excluded from owner-presentation parity. Mongo's
# users.id and Postgres's core.users.user_id are independently generated UUIDs,
# and legal owners without portal accounts may legitimately have no user row in
# one projection. Access/authorization parity is checked separately by explicit
# membership decision tests; display parity should compare display/contact facts.
_EXCLUDED_FIELD_NAMES = {"owner_id"}


def _hash_value(value: Any) -> str | None:
    """Return a stable, non-reversible fingerprint for a PII comparison value."""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]  # short prefix — a fingerprint, not a reversible store


def _redact(value: Any) -> Any:
    """Recursively redact PII-named fields, strip metadata-only fields, and reduce
    cross-system-incomparable id fields in a dict/list/scalar payload."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for k, v in value.items():
            if k in _METADATA_FIELD_NAMES or k in _EXCLUDED_FIELD_NAMES:
                continue
            if k in _PII_FIELD_NAMES and isinstance(v, (str, type(None))):
                result[k] = _hash_value(v)
            else:
                result[k] = _redact(v)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _compare_owner_payloads(
        *, mongo_payload: dict[str, Any], pg_payload: dict[str, Any], tolerance_cents: int = 0,
) -> list[FieldDiff]:
    """Generic comparator for all 4 owner_service.py methods.

    The 4 methods return different shapes (dict, dict-of-dicts, list-of-dicts, bool) — all
    are wrapped by the caller into {"value": <original_return_value>} before reaching here,
    so this function only ever sees one key. Redact, then compare structurally; on mismatch
    record one aggregate diff (hashes only), not a field-by-field breakdown — there's no
    stable field-path concept across four different payload shapes.
    """
    mongo_redacted = _redact(mongo_payload.get("value"))
    pg_redacted = _redact(pg_payload.get("value"))

    if mongo_redacted == pg_redacted:
        return []

    return [FieldDiff(
        field_path="value",
        mongo_value=mongo_redacted,
        pg_value=pg_redacted,
        tolerance_cents=0,
        severity="warn",
    )]


async def compare_owner_payloads(
        *,
        building_id: str,
        route_key: str,
        mongo_value: Any,
        pg_value: Any,
        is_test_data: bool = False,
) -> ShadowCompareResult:
    """Compare a Mongo vs Postgres owner-lookup result, redacting PII before storage/comparison.

    Never raises — matches every other shadow-compare entry point's contract. Call via
    shadow_read_service.schedule_shadow_compare(), never awaited inline in a request.
    """
    return await run_shadow_compare(
        building_id=building_id,
        domain=_DOMAIN,
        route_key=route_key,
        mongo_payload={"value": mongo_value},
        pg_payload={"value": pg_value} if pg_value is not None else None,
        comparator=_compare_owner_payloads,
        is_test_data=is_test_data,
    )
