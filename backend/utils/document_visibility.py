# @featuretrace:documents — Canonical "which documents may this user see?" filter.
# Layer: service
# Data flow: current_user -> build_document_visibility_filter() -> Mongo filter
#            -> documents collection -> /documents list + /analytics/activities feed.
# Related: backend/routers/documents.py
#          backend/routers/analytics.py
#          backend/cron/cron_payment_reminders.py
# Tests: tests/backend/test_document_visibility.py

"""One definition of document visibility, shared by every reader.

## The bug this exists to prevent

Two schemas for the same collection grew up independently:

* the **upload** path writes ``is_public`` / ``uploaded_by`` / ``allowed_roles``;
* the **generated-notice** path (``cron_payment_reminders``) writes
  ``is_private`` / ``owner_id`` / ``unit_number``.

``routers/documents.py`` filtered only on the first set. Against East Gate's live
data — 242 documents, every one of them a generated levy notice — that matched
**zero** rows, so ``/documents`` rendered an empty page while the documents
plainly existed.

Meanwhile ``routers/analytics.py`` built its activity feed with no visibility
filter at all, so the SAME 242 invisible documents were advertised in the
Community Feed as "Document uploaded: Levy Notice - TH083 - 2026-09-01". Every
one of those links dead-ended on the empty documents page.

A feed that offers what the destination refuses to show is the worst of both
failures, and it happened precisely because two call sites each wrote their own
filter. There is now one function; both call it.

## The rule

* Privileged roles (super_admin / strata_admin / strata_manager / ec_member) see
  every document in their building.
* Everyone else sees a document when it is **not private** — under either
  spelling, ``is_private != True`` and ``is_public != False`` — or when it is
  theirs: ``owner_id`` or ``uploaded_by`` matches their user id, or
  ``unit_number`` matches a unit they hold, or ``allowed_roles`` names their
  effective role.

Absent fields are treated as "not private". A generated notice carrying only
``is_private: True`` is private; a plain upload carrying neither flag is shared,
which is how the upload UI has always presented it.
"""

from __future__ import annotations

from typing import Any, Optional

from models.user import UserRole
from utils.auth import effective_role

#: Roles that may read every document in the building.
PRIVILEGED_DOCUMENT_ROLES = {
    UserRole.SUPER_ADMIN,
    UserRole.STRATA_ADMIN,
    UserRole.STRATA_MANAGER,
    UserRole.EC_MEMBER,
}


def is_privileged_document_reader(current_user: Optional[dict]) -> bool:
    """True when the user may read every document in their building."""
    if not current_user:
        return False
    return effective_role(current_user) in PRIVILEGED_DOCUMENT_ROLES


def owned_unit_numbers(current_user: dict) -> list[str]:
    """Every unit identifier this user may hold, de-duplicated, order preserved."""
    candidates: list[Any] = []
    if current_user.get("unit_number"):
        candidates.append(current_user["unit_number"])
    owned = current_user.get("owned_units") or []
    if isinstance(owned, list):
        candidates.extend(owned)

    seen: set[str] = set()
    units: list[str] = []
    for raw in candidates:
        # owned_units may hold plain strings or {"unit_number": ...} shaped rows.
        value = raw.get("unit_number") if isinstance(raw, dict) else raw
        if value is None:
            continue
        text = str(value)
        if text and text not in seen:
            seen.add(text)
            units.append(text)
    return units


def build_document_visibility_filter(current_user: Optional[dict]) -> dict:
    """Return the Mongo filter fragment restricting `documents` to what the user may see.

    The caller supplies ``building_id`` and the ``is_test_data`` guard; this
    function contributes visibility only, so it composes with any query.

    Returns ``{}`` for privileged readers — deliberately, so callers can merge it
    unconditionally without special-casing.
    """
    if is_privileged_document_reader(current_user):
        return {}

    # Anonymous/public callers get strictly the non-private documents.
    not_private = [
        {"is_private": {"$ne": True}},
        {"is_public": {"$ne": False}},
    ]
    if not current_user:
        return {"$and": not_private}

    clauses: list[dict] = [{"$and": not_private}]

    user_id = current_user.get("id")
    if user_id:
        clauses.append({"owner_id": user_id})
        clauses.append({"uploaded_by": user_id})

    units = owned_unit_numbers(current_user)
    if units:
        clauses.append({"unit_number": {"$in": units}})

    role = effective_role(current_user)
    if role:
        clauses.append({"allowed_roles": {"$in": [role]}})

    # Nested under $and by the caller's merge, never emitted as a top-level $or —
    # TenantScopedDatabase._inject_bid() rejects a top-level $or when it needs to
    # add building_id (see CLAUDE.md).
    return {"$or": clauses}
