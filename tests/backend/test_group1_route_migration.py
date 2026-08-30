"""GAP-SEC-005 group 1 — organisation users, roles and building assignments.

These tests pin what the migration actually changed, so a later edit that widens
one of these routes fails here rather than in production.

The migration is deliberately **additive**: each route keeps its original check
and gains a ``require_capability`` dependency. An additive guard can only narrow
access, so a capability that turns out to be too tight produces a visible 403 in
testing rather than silently opening a route. These tests therefore assert the
*intersection*, and name the two intentional tightenings.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from models.user import UserRole  # noqa: E402
from services.capability_registry import CAPABILITY_REGISTRY, can  # noqa: E402
from services.field_masking import obligations_for  # noqa: E402

SERVER_PY = REPO_ROOT / "backend" / "server.py"

#: Handler name → the capability it must be guarded by. This IS the record of the
#: migration: the group-1 routes that were wired, and to what.
MIGRATED_ROUTES = {
    "get_users": "building.people.view",
    "get_archived_users": "building.people.view",
    "get_expired_users": "building.people.view",
    "reactivate_expired_user": "building.people.manage",
    "update_user": "building.people.onboarding.manage",
    "reject_user": "building.people.onboarding.manage",
    "request_user_info": "building.people.onboarding.manage",
    "request_profile_info": "building.people.onboarding.manage",
    "delete_user": "building.people.manage",
    "archive_user_endpoint": "building.people.manage",
    "restore_user_endpoint": "building.people.manage",
    "elevate_user": "building.people.manage",
    "revoke_elevation": "building.people.manage",
}

#: Group-1 routes deliberately NOT migrated, with the reason. Recorded here
#: because GAP-SEC-005's "done when" requires every un-migrated route to be named
#: with a reason rather than quietly skipped.
NOT_MIGRATED = {
    "owner_registration_decision": (
        "The actor is an OWNER approving a tenant/guest for their own lot. No "
        "building-scoped capability includes owners, and the action is really "
        "unit-scoped (unit.tenancy.manage) but the route does not carry a unit in "
        "its path. Needs the scope work before it can be migrated."
    ),
    "get_chat_users": (
        "Reclassified out of group 1 into group 5. It is the chat participant "
        "picker, reachable by any approved resident and already filtered by the "
        "directory visibility settings — building.people.view would deny chat to "
        "every owner and tenant."
    ),
}


@pytest.fixture(scope="module")
def server_source() -> str:
    """Generated function header.

    Function: server_source
    Path: tests/backend/test_group1_route_migration.py
    """
    return SERVER_PY.read_text()


def _handler_signature(source: str, handler: str) -> str:
    """Return the text of ``handler``'s signature, from ``def`` to the closing paren."""
    match = re.search(rf"^async def {re.escape(handler)}\(", source, re.MULTILINE)
    assert match, f"handler {handler} not found in server.py"
    start = match.start()
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    pytest.fail(f"unbalanced signature for {handler}")


# ── The wiring is present and points at a real capability ────────────────────

@pytest.mark.parametrize("handler,capability", sorted(MIGRATED_ROUTES.items()))
def test_route_is_guarded_by_its_capability(server_source, handler, capability):
    """Each migrated route declares require_capability with the agreed capability."""
    signature = _handler_signature(server_source, handler)
    assert "require_capability(" in signature, f"{handler} lost its capability guard"
    assert f'require_capability("{capability}"' in signature, (
        f"{handler} should be guarded by {capability}; signature was:\n{signature}"
    )


@pytest.mark.parametrize("capability", sorted(set(MIGRATED_ROUTES.values())))
def test_every_capability_used_exists(capability):
    """A typo'd capability name denies everything and looks like a policy decision."""
    assert capability in CAPABILITY_REGISTRY, (
        f"{capability} is not in CAPABILITY_REGISTRY. An unknown capability fails "
        "closed with DENY_UNKNOWN_CAPABILITY, which is indistinguishable from a "
        "deliberate denial in the logs."
    )


@pytest.mark.parametrize("handler,capability", sorted(MIGRATED_ROUTES.items()))
def test_migration_kept_the_original_building_scope(server_source, handler, capability):
    """building_from_context is required: these routes never name a building in the URL.

    Without it the scope carries no building_id and every request denies with
    DENY_SCOPE_INCOMPLETE — a total outage that reads like a policy decision.
    """
    signature = _handler_signature(server_source, handler)
    assert "building_from_context=True" in signature, (
        f"{handler} resolves its building from the auth context, not the path"
    )


def test_unmigrated_group1_routes_are_named_with_a_reason(server_source):
    """A route left behind must be a recorded decision, not an oversight."""
    for handler, reason in NOT_MIGRATED.items():
        signature = _handler_signature(server_source, handler)
        assert "require_capability(" not in signature, (
            f"{handler} is recorded as un-migrated but now has a capability guard. "
            "Move it into MIGRATED_ROUTES."
        )
        assert len(reason) > 40, f"{handler} needs a real reason, not a placeholder"


# ── The intended access changes ──────────────────────────────────────────────

def _subject(role: str, **extra) -> dict:
    """Generated function header.

    Function: _subject
    Path: tests/backend/test_group1_route_migration.py
    """
    return {"id": "u1", "role": role, "building_ids": ["B1"], **extra}


SCOPE = {"building_id": "B1"}


def test_ec_member_loses_user_administration():
    """Settled decision: EC member is governance oversight, not user administration.

    DEFAULT_PERMISSIONS still grants ec_member ``can_manage_users``. The additive
    capability guard is what actually stops them, so this asserts the capability
    — not the boolean — is the binding constraint.
    """
    ec = _subject(UserRole.EC_MEMBER)

    assert can(ec, "building.people.view", SCOPE) is False
    assert can(ec, "building.people.manage", SCOPE) is False
    assert can(ec, "building.people.onboarding.manage", SCOPE) is False


def test_admin_staff_keeps_onboarding_review_but_loses_account_lifecycle():
    """The second intentional tightening, and the reason the onboarding capability exists.

    admin_staff are the registration reviewers (``_STAFF_REVIEWER_ROLES`` in
    routers/auth.py) and must be able to act on the approval email they receive.
    They should not also be able to delete or archive accounts, which
    ``can_manage_users`` previously gave them as a side effect — a cost the
    permission model's own comment acknowledges.
    """
    staff = _subject(UserRole.ADMIN_STAFF)

    assert can(staff, "building.people.view", SCOPE) is True
    assert can(staff, "building.people.onboarding.manage", SCOPE) is True
    assert can(staff, "building.people.manage", SCOPE) is False


@pytest.mark.parametrize(
    "role", [UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER]
)
def test_management_roles_keep_everything(role):
    """The migration must not break the people who actually administer a building."""
    subject = _subject(role, organisation_id="B1", tenant_id="B1")

    for capability in sorted(set(MIGRATED_ROUTES.values())):
        assert can(subject, capability, SCOPE) is True, f"{role} lost {capability}"


@pytest.mark.parametrize("role", [UserRole.OWNER, UserRole.TENANT, UserRole.GUEST, UserRole.REAL_ESTATE_AGENT])
def test_residents_never_reach_user_administration(role):
    """Generated function header.

    Function: test_residents_never_reach_user_administration
    Path: tests/backend/test_group1_route_migration.py
    """
    subject = _subject(role)
    for capability in sorted(set(MIGRATED_ROUTES.values())):
        assert can(subject, capability, SCOPE) is False


# ── The point of the migration: masking ──────────────────────────────────────

def test_staff_reading_the_user_list_get_masked_contact_details():
    """The access matrix's "staff: PII masked by default" row, made real.

    ``GET /users`` returns UserResponse rows carrying email and phone. Before this
    migration nothing masked them for admin_staff — a cost the permission model's
    own comment names explicitly ("unmasked resident PII via GET /users").
    """
    obligations = obligations_for(UserRole.ADMIN_STAFF, ())

    assert "MASK_OWNER_CONTACT" in obligations
    assert "MASK_BANK_DETAILS" in obligations


def test_management_reading_the_user_list_still_see_contact_details():
    """Masking staff must not blind the manager who has to phone the owner."""
    obligations = obligations_for(UserRole.STRATA_MANAGER, ())

    assert "MASK_OWNER_CONTACT" not in obligations
    # Bank details are withheld from everyone outside the dual-control payment flow.
    assert "MASK_BANK_DETAILS" in obligations
