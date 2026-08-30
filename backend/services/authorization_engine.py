"""
Authorization Engine — Zanzibar-Style Relationship Graph

Implements relationship-based access control (ReBAC) inspired by Google's Zanzibar.

Core concept:
  Does SUBJECT have RELATION to OBJECT that grants PERMISSION?

Graph structure:
  user:87 → owner_of → unit:45
  unit:45 → part_of → building:101
  user:87 → committee_member → building:101
  user:87 → chairman → building:101

Permission derivation:
  chairman relation on building → committee_member's set + meetings.agenda.manage
  committee_member relation → committee.vote, financial.view (governance READ)
  owner_of (unit in building) → financial.view_own, workorder.create, documents.view

## Narrowed 2026-08-24 (GAP-SEC-006)

``RELATION_PERMISSION_MAP`` used to grant every ``committee_member``
``users.manage``, ``financial.manage``, ``committee.manage``,
``maintenance.manage``, ``workorder.approve``, ``announcements.manage`` and
``meetings.manage``; ``chairman`` and ``strata_admin`` added
``financial.approve_invoice``, ``financial.export`` and ``users.invite`` on top;
and ``strata_admin`` inherited ``committee_member``, which handed it
``committee.vote``.

That contradicted the confirmed ACT access model on three counts. An EC member
gets governance oversight, deliberation and a vote — **not** user
administration, **not** financial writes, **not** operational control. An office
adds **function, never rank**, so the chair does not acquire spending power by
chairing. And a Strata Admin **may not cast an EC vote** at all.

Removed accordingly. See
``docs/security/acl_information_access_implementation_plan.md`` §4 for the matrix
and ``tasks/GAP-SEC-006`` for the sequencing.

## What this module is NOT

**Nothing in production calls this engine.** ``traverse_graph`` and
``check_permission`` have no caller outside the test suite:
``routers/rbac.py`` imports ``check_permission`` but never invokes it, and
``POST /auth/rbac/check`` routes to ``permission_service.user_can``, which does
not consult this map at all.

The relationship tuples themselves ARE written — ``sync_user_relationships`` runs
on role assignment — so the graph is populated. The over-grant was therefore
**armed but not firing**: wire ``check_permission`` into any route and it would
have become live immediately. The plan and GAP-SEC-006 both described it as
already live; that was wrong, and is corrected there.

``tests/backend/test_authorization_engine_not_wired.py`` guards that gap: if
somebody gives this engine a production caller, the test fails and forces a
deliberate decision rather than an accidental grant. The canonical evaluator is
``services/capability_registry.py``.
"""

from collections import deque
from datetime import datetime, timezone

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Set

try:
    from database import db
except ImportError:
    db = None  # Allow import in test environments without a live DB

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RELATION_PERMISSION_MAP: dict[str, set[str]] = {
    "owner_of": {
        "financial.view_own", "workorder.view", "workorder.create",
        "documents.view", "committee.vote", "announcements.view",
        "meetings.view", "listings.view", "listings.create",
        "maintenance.request", "tenancy.view", "tenancy.manage",
        "chat.access", "email.access",
    },
    "tenant_of": {
        "workorder.view", "documents.view", "announcements.view",
        "meetings.view", "listings.view", "listings.create",
        "maintenance.request", "chat.access",
    },
    # An executive committee member gets governance READ, oversight,
    # deliberation and a VOTE. Not user administration, not financial writes,
    # not operational control. See the narrowing note at the top of this module
    # for what was removed and why.
    "committee_member": {
        "financial.view", "workorder.view", "documents.view",
        "committee.vote", "announcements.view", "meetings.view",
        "maintenance.request", "tenancy.view", "chat.access",
        "schedule.view", "email.access",
    },
    "strata_admin": {
        # Admin of a Strata Management Company. Manages PEOPLE, not buildings
        # (plan §8 decision 1) — organisation identity, staff roles and
        # portfolios. Deliberately does NOT inherit committee_member: a strata
        # admin is not an EC member and the matrix says they may not cast an EC
        # vote. Scheme business data requires an explicit building assignment.
        "users.view", "users.invite", "users.manage",
        "building.view", "chat.access", "email.access",
    },
    "chairman": {
        # Chairperson of the Executive Committee (UTMA 2011 (ACT) s 41).
        # Chairs meetings and sets agendas. Holds a casting vote ONLY to break a
        # tie (sch 2 s 2.11) — that is a tie-break rule inside the voting
        # procedure, not a separate permission, so it is not a slug here.
        # Inherits committee_member; adds function, never rank.
        "meetings.agenda.manage",
    },
    "treasurer": {
        # Treasurer (UTMA 2011 (ACT) s 43). PREPARES the statutory financial
        # records and statements. Payment execution is deliberately absent: it
        # requires a recorded EC authority, and a relation→permission map
        # cannot express "and only with authority X". The authoritative
        # definition is capability_registry's
        # building.finance.payment.execute, which carries
        # required_authority="resolution".
        #
        # The treasurer's per-lot arrears visibility is also absent by design.
        # It is not a capability — it is the ABSENCE of a masking obligation,
        # resolved in field_masking.obligations_for().
        "financial.view", "financial.records.prepare",
        "committee.vote", "meetings.view", "workorder.view",
        "documents.view", "chat.access", "announcements.view",
    },
    "secretary": {
        # Secretary (UTMA 2011 (ACT) s 42) — records custodian. Issues notices,
        # prepares minutes, authors communications.
        "committee.vote", "committee.finalize_minutes",
        "meetings.view", "meetings.manage", "documents.view",
        "documents.upload", "announcements.view", "announcements.create",
        "announcements.manage", "chat.access",
    },
    "agent_of": {
        "workorder.view", "documents.view", "documents.upload",
        "listings.view", "listings.create", "listings.manage",
        "maintenance.request", "maintenance.manage",
        "tenancy.view", "tenancy.manage", "chat.access", "schedule.view",
    },
    "manager_of": {
        # Strata/building manager
        "financial.view", "financial.manage", "financial.approve_invoice",
        "financial.export", "workorder.view", "workorder.create",
        "workorder.update", "workorder.approve", "documents.view",
        "documents.upload", "documents.manage", "committee.vote",
        "committee.finalize_minutes", "committee.manage",
        "users.view", "users.manage", "users.invite",
        "announcements.view", "announcements.create", "announcements.manage",
        "meetings.view", "meetings.manage", "listings.view",
        "maintenance.request", "maintenance.manage",
        "tenancy.view", "tenancy.manage", "chat.access",
        "notifications.send", "schedule.view", "schedule.manage",
        "email.access", "building.view", "building.update",
    },
}

# Relations that propagate graph traversal to the object node
_TRAVERSAL_RELATIONS = {"part_of", "belongs_to"}

# Permissions inherited by the "strata_admin" relation.
#
# Deliberately EMPTY. This previously inherited committee_member, which handed
# every strata admin `committee.vote` — and the access matrix says a Strata Admin
# may not cast an EC vote. A strata admin is not a committee member; they
# administer the organisation's people. Their own set above is complete.
_STRATA_ADMIN_INHERITS: set[str] = set()

# Permissions inherited by the "chairman" relation.
#
# A chairperson IS an EC member, so inheriting the (now narrowed) governance set
# is correct — the chair's own entry adds agenda control on top. This is the
# "offices add function, never rank" rule: the inheritance is what makes the
# chair an ordinary member for everything except chairing.
_CHAIRMAN_INHERITS = {"committee_member"}

# Committee-related relations that can be expired for a user
COMMITTEE_RELATIONS = {"committee_member", "strata_admin", "chairman", "treasurer", "secretary"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PermissionCheckResult:
    allowed: bool
    reason: str
    matched_relations: List[str] = field(default_factory=list)
    graph_depth: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_tuple_active(tup: dict) -> bool:
    """Returns False if the tuple has expired."""
    expires_at = tup.get("expires_at")
    if expires_at is None:
        return True
    if isinstance(expires_at, str):
        try:
            expires_at = datetime.fromisoformat(expires_at)
        except ValueError:
            return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Core engine functions
# ---------------------------------------------------------------------------

async def get_subject_relations(
        subject: str,
        building_id: Optional[str] = None,
) -> List[dict]:
    """
    Fetches active, non-expired relationship tuples for *subject* from
    ``db.relationship_tuples``.

    Parameters
    ----------
    subject:
        Formatted entity string, e.g. ``"user:abc123"`` or ``"unit:45"``.
    building_id:
        When provided, restricts results to tuples scoped to that building.

    Returns
    -------
    List of raw MongoDB documents.
    """
    if db is None:
        logger.warning("db is None — cannot fetch relations (test mode?)")
        return []

    query: dict = {
        "subject": subject,
        "is_active": True,
    }
    if building_id:
        query["building_id"] = building_id

    now = datetime.now(timezone.utc)
    # Tuples where expires_at is absent OR expires_at > now
    query["$or"] = [
        {"expires_at": None},
        {"expires_at": {"$exists": False}},
        {"expires_at": {"$gt": now}},
    ]

    try:
        cursor = db.relationship_tuples.find(query)
        return await cursor.to_list(length=None)
    except Exception:
        logger.exception("Error fetching relations for subject=%s", subject)
        return []


async def traverse_graph(
        start_subject: str,
        building_id: Optional[str] = None,
        max_depth: int = 4,
) -> Set[str]:
    """
    BFS traversal of the relationship graph starting from *start_subject*.

    Returns the full set of permission slugs derived from all reachable
    relationship tuples, respecting expiry and ``is_active`` flags.

    Parameters
    ----------
    start_subject:
        Entity string to start traversal from, e.g. ``"user:abc123"``.
    building_id:
        Optional building scope applied to every DB query.
    max_depth:
        Safety limit on graph depth to prevent runaway traversal.

    Returns
    -------
    Set of permission slug strings.
    """
    permissions: Set[str] = set()
    visited: set[str] = set()
    # Each queue entry is (subject, depth)
    queue: deque[tuple[str, int]] = deque([(start_subject, 0)])

    while queue:
        subject, depth = queue.popleft()

        if subject in visited or depth > max_depth:
            continue
        visited.add(subject)

        relations = await get_subject_relations(subject, building_id=building_id)

        for tup in relations:
            if not _is_tuple_active(tup):
                continue

            relation = tup.get("relation", "")
            obj = tup.get("object", "")

            # Accumulate direct permissions
            if relation in RELATION_PERMISSION_MAP:
                permissions |= RELATION_PERMISSION_MAP[relation]

            # strata_admin inherits committee_member permissions
            if relation == "strata_admin":
                for inherited in _STRATA_ADMIN_INHERITS:
                    permissions |= RELATION_PERMISSION_MAP.get(inherited, set())

            # chairman inherits committee_member permissions
            if relation == "chairman":
                for inherited in _CHAIRMAN_INHERITS:
                    permissions |= RELATION_PERMISSION_MAP.get(inherited, set())

            # Propagate traversal through structural relations
            if relation in _TRAVERSAL_RELATIONS and obj and obj not in visited:
                queue.append((obj, depth + 1))

    return permissions


async def check_permission(
        user_id: str,
        action: str,
        object_id: Optional[str] = None,
        building_id: Optional[str] = None,
) -> PermissionCheckResult:
    """
    Checks whether *user_id* has the right to perform *action*.

    Parameters
    ----------
    user_id:
        Raw user identifier (without the ``"user:"`` prefix).
    action:
        Permission slug, e.g. ``"financial.approve_invoice"``.
    object_id:
        Optional object the action targets (not yet used for path filtering
        but recorded for audit / future enforcement).
    building_id:
        Optional building scope.

    Returns
    -------
    :class:`PermissionCheckResult`
    """
    subject = f"user:{user_id}"

    try:
        permissions = await traverse_graph(subject, building_id=building_id)
    except Exception:
        logger.exception("Graph traversal failed for user=%s action=%s", user_id, action)
        return PermissionCheckResult(
            allowed=False,
            reason="internal_error",
        )

    if action in permissions:
        # Gather the matched relation names for the audit trail
        matched: List[str] = []
        try:
            direct_tuples = await get_subject_relations(subject, building_id=building_id)
            for tup in direct_tuples:
                rel = tup.get("relation", "")
                if rel in RELATION_PERMISSION_MAP and action in RELATION_PERMISSION_MAP[rel]:
                    matched.append(rel)
                if rel == "strata_admin" and action in RELATION_PERMISSION_MAP.get("committee_member", set()):
                    if rel not in matched:
                        matched.append(rel)
                if rel == "chairman" and action in RELATION_PERMISSION_MAP.get("committee_member", set()):
                    if rel not in matched:
                        matched.append(rel)
        except Exception:
            logger.warning("Could not collect matched_relations for audit", exc_info=True)

        return PermissionCheckResult(
            allowed=True,
            reason=f"permission '{action}' granted via relationship graph",
            matched_relations=matched,
            graph_depth=1,
        )

    return PermissionCheckResult(
        allowed=False,
        reason=f"no relationship grants '{action}' for user {user_id}",
    )


async def add_relationship(
        subject: str,
        relation: str,
        obj: str,
        building_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        created_by: Optional[str] = None,
) -> str:
    """
    Creates (or reactivates) a relationship tuple.

    Upserts on (subject, relation, object, building_id) so duplicate
    insertions are idempotent.

    Returns
    -------
    The ``str`` representation of the document's ``_id``.
    """
    if db is None:
        raise RuntimeError("Database is not available")

    now = datetime.now(timezone.utc)
    match_filter: dict = {
        "subject": subject,
        "relation": relation,
        "object": obj,
        "building_id": building_id,
    }
    update_doc: dict = {
        "$set": {
            "subject": subject,
            "relation": relation,
            "object": obj,
            "building_id": building_id,
            "is_active": True,
            "updated_at": now,
            "expires_at": expires_at,
        },
        "$setOnInsert": {
            "created_at": now,
            "created_by": created_by,
        },
    }

    result = await db.relationship_tuples.find_one_and_update(
        match_filter,
        update_doc,
        upsert=True,
        return_document=True,
    )

    doc_id = str(result["_id"]) if result else ""
    logger.debug(
        "add_relationship subject=%s relation=%s object=%s building=%s id=%s",
        subject, relation, obj, building_id, doc_id,
    )
    return doc_id


async def remove_relationship(
        subject: str,
        relation: str,
        obj: str,
        building_id: Optional[str] = None,
) -> bool:
    """
    Soft-deletes a relationship by setting ``is_active=False``.

    Returns
    -------
    ``True`` if at least one document was matched and updated.
    """
    if db is None:
        raise RuntimeError("Database is not available")

    match_filter: dict = {
        "subject": subject,
        "relation": relation,
        "object": obj,
        "building_id": building_id,
        "is_active": True,
    }
    result = await db.relationship_tuples.update_many(
        match_filter,
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}},
    )

    found = result.matched_count > 0
    logger.debug(
        "remove_relationship subject=%s relation=%s object=%s found=%s",
        subject, relation, obj, found,
    )
    return found


async def get_user_relationships(
        user_id: str,
        building_id: Optional[str] = None,
) -> List[dict]:
    """
    Returns all active, non-expired relationship tuples for ``user:{user_id}``.

    Parameters
    ----------
    user_id:
        Raw user identifier (without the ``"user:"`` prefix).
    building_id:
        Optional building scope.
    """
    subject = f"user:{user_id}"
    return await get_subject_relations(subject, building_id=building_id)


async def expire_committee_relations(user_id: str, building_id: str) -> None:
    """
    Soft-deletes all committee-related relationship tuples for a user within
    a specific building.

    Expired relations: committee_member, chairman, treasurer, secretary.

    Parameters
    ----------
    user_id:
        Raw user identifier (without the ``"user:"`` prefix).
    building_id:
        The building scope to restrict the operation.
    """
    if db is None:
        raise RuntimeError("Database is not available")

    subject = f"user:{user_id}"
    result = await db.relationship_tuples.update_many(
        {
            "subject": subject,
            "relation": {"$in": list(COMMITTEE_RELATIONS)},
            "building_id": building_id,
            "is_active": True,
        },
        {"$set": {"is_active": False, "updated_at": datetime.now(timezone.utc)}},
    )
    logger.info(
        "expire_committee_relations user=%s building=%s expired=%d",
        user_id, building_id, result.modified_count,
    )
