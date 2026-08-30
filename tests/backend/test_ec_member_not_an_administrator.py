"""An EC member is governance oversight, not an administrator — on every path.

GAP-SEC-006's "done when" asks for a test proving an EC member cannot perform
user administration or a financial write through **any** path: capability,
relation tuple, or permission boolean.

Two of those three are closed and asserted here. The third is not, and this file
says so with a strict xfail rather than a weaker assertion — because a test that
quietly checks less than the requirement is worse than no test: it reports green
on work that has not been done.

| Path | State |
|---|---|
| `capability_registry` | **closed** — `building.people.*` / `building.finance.manage` exclude `ec_member` |
| `authorization_engine` relation tuples | **closed 2026-08-24** — this branch |
| `DEFAULT_PERMISSIONS` booleans | **OPEN** — `can_manage_users` and `can_manage_finances` are still `True` |

The boolean path cannot be closed here. `can_manage_users` gates ~95 call sites
and `can_manage_finances` ~98, and most have no capability replacement yet.
Removing them wholesale is exactly what GAP-SEC-006's own sequencing forbids:
land GAP-SEC-005 for an area, observe it allowing the right people, *then* remove
that area's legacy check. The xfails below flip to failures the moment that work
lands, which is the signal to delete them.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from models.user import UserRole  # noqa: E402
from services.authorization_engine import (  # noqa: E402
    RELATION_PERMISSION_MAP,
    _CHAIRMAN_INHERITS,
    _STRATA_ADMIN_INHERITS,
    traverse_graph,
)
from services.capability_registry import can  # noqa: E402
from utils.permissions import get_user_permissions  # noqa: E402

BUILDING = "BLD-EC"
SCOPE = {"building_id": BUILDING}

#: Everything an EC member must not be able to do administratively.
ADMIN_PERMISSION_SLUGS = (
    "users.manage",
    "users.invite",
    "financial.manage",
    "financial.approve_invoice",
    "financial.export",
    "committee.manage",
    "maintenance.manage",
    "workorder.approve",
    "announcements.manage",
    "meetings.manage",
    "notifications.send",
)


def _ec_subject(**extra) -> dict:
    """Generated function header.

    Function: _ec_subject
    Path: tests/backend/test_ec_member_not_an_administrator.py
    """
    return {
        "id": "ec-1",
        "role": UserRole.EC_MEMBER,
        "building_id": BUILDING,
        "building_ids": [BUILDING],
        **extra,
    }


@contextmanager
def _graph(tuples: list[dict]):
    """Feed traverse_graph a fixed tuple set without a live Mongo."""
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=tuples)
    mock_db = MagicMock()
    mock_db.relationship_tuples.find = MagicMock(return_value=cursor)
    with patch("services.authorization_engine.db", mock_db):
        yield


def _tuple(relation: str, subject: str = "user:ec-1") -> dict:
    """Generated function header.

    Function: _tuple
    Path: tests/backend/test_ec_member_not_an_administrator.py
    """
    return {
        "subject": subject,
        "relation": relation,
        "object": f"building:{BUILDING}",
        "building_id": BUILDING,
        "is_active": True,
        "expires_at": None,
    }


# ── Path 1: the canonical evaluator ──────────────────────────────────────────

@pytest.mark.parametrize(
    "capability",
    [
        "building.people.view",
        "building.people.manage",
        "building.finance.manage",
        "building.arrears.manage",
        "organisation.users.manage",
    ],
)
def test_capability_path_denies_ec_member_administration(capability):
    """Generated function header.

    Function: test_capability_path_denies_ec_member_administration
    Path: tests/backend/test_ec_member_not_an_administrator.py
    """
    assert can(_ec_subject(), capability, SCOPE) is False, (
        f"{capability} must not be reachable by an ordinary EC member"
    )


def test_capability_path_still_allows_governance_oversight():
    """The narrowing must not blind the committee it is meant to keep informed."""
    subject = _ec_subject()

    assert can(subject, "building.finance.view", SCOPE) is True
    assert can(subject, "building.arrears.view", SCOPE) is True
    assert can(subject, "building.documents.view", SCOPE) is True
    assert can(subject, "building.meetings.view", SCOPE) is True
    assert can(subject, "building.governance.vote", SCOPE) is True


# ── Path 2: the relation graph ───────────────────────────────────────────────

@pytest.mark.parametrize("slug", ADMIN_PERMISSION_SLUGS)
async def test_relation_graph_denies_ec_member_administration(slug):
    """The GAP-SEC-006 over-grant itself: committee_member must confer none of these."""
    with _graph([_tuple("committee_member")]):
        perms = await traverse_graph("user:ec-1", building_id=BUILDING)

    assert slug not in perms, f"committee_member still grants {slug}"


async def test_relation_graph_keeps_ec_member_oversight_and_vote():
    """Generated function header.

    Function: test_relation_graph_keeps_ec_member_oversight_and_vote
    Path: tests/backend/test_ec_member_not_an_administrator.py
    """
    with _graph([_tuple("committee_member")]):
        perms = await traverse_graph("user:ec-1", building_id=BUILDING)

    for slug in ("financial.view", "committee.vote", "documents.view", "meetings.view"):
        assert slug in perms, f"committee_member lost {slug}, which is oversight not administration"


@pytest.mark.parametrize("slug", ADMIN_PERMISSION_SLUGS)
async def test_an_office_does_not_smuggle_administration_back_in(slug):
    """Offices add function, never rank — including through the inheritance edge.

    The chair inherits committee_member deliberately (a chairperson IS an EC
    member). This asserts the inheritance carries the narrowed set and not a
    reinstated administrative one.
    """
    if slug in {"meetings.manage", "announcements.manage"}:
        pytest.skip("secretary legitimately issues notices and authors communications (s 42)")

    for office in ("chairman", "treasurer"):
        with _graph([_tuple(office)]):
            perms = await traverse_graph("user:ec-1", building_id=BUILDING)
        assert slug not in perms, f"{office} relation still grants {slug}"


async def test_treasurer_prepares_records_but_cannot_execute_a_payment():
    """UTMA 2011 (ACT) s 43. Payment execution needs a recorded EC authority.

    A relation→permission map structurally cannot express "and only with
    authority X", so it must grant nothing that implies payment power. The
    authoritative gate is capability_registry's
    building.finance.payment.execute, carrying required_authority="resolution".
    """
    with _graph([_tuple("treasurer")]):
        perms = await traverse_graph("user:ec-1", building_id=BUILDING)

    assert "financial.records.prepare" in perms
    assert "financial.view" in perms
    assert "financial.approve_invoice" not in perms
    assert "financial.manage" not in perms
    assert "financial.export" not in perms


async def test_strata_admin_relation_cannot_cast_an_ec_vote():
    """The matrix is explicit: Strata Admin — cast an EC vote — no.

    This used to leak via `_STRATA_ADMIN_INHERITS = {"committee_member"}`.
    """
    assert _STRATA_ADMIN_INHERITS == set(), (
        "strata_admin must not inherit committee_member — it would confer committee.vote"
    )

    with _graph([_tuple("strata_admin", subject="user:sa-1")]):
        perms = await traverse_graph("user:sa-1", building_id=BUILDING)

    assert "committee.vote" not in perms


def test_chairman_still_inherits_committee_member():
    """Guards the premise of the office tests: the chair IS an ordinary member too."""
    assert _CHAIRMAN_INHERITS == {"committee_member"}


def test_committee_member_set_is_read_only_oversight():
    """A structural backstop: no `.manage` verb may appear in the EC member's set.

    Cheaper to maintain than an exhaustive slug list, and it catches a new
    administrative slug added to the wrong relation without anyone updating
    ADMIN_PERMISSION_SLUGS.
    """
    offenders = sorted(
        slug for slug in RELATION_PERMISSION_MAP["committee_member"]
        if slug.endswith(".manage") or slug.endswith(".approve") or slug.endswith(".invite")
    )
    assert not offenders, (
        f"committee_member gained administrative slug(s) {offenders}. An EC member gets "
        "governance read, oversight, deliberation and a vote — see plan §4 rule 1."
    )


# ── Path 3: the permission booleans — NOT closed ─────────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFAULT_PERMISSIONS still grants ec_member can_manage_users. Closing it means "
        "removing the boolean from ~95 gate sites that mostly have no capability "
        "replacement yet — GAP-SEC-005's job, area by area. When this xfail starts "
        "failing, the boolean has been removed: delete the marker."
    ),
)
def test_permission_boolean_path_denies_ec_member_user_administration():
    """Generated function header.

    Function: test_permission_boolean_path_denies_ec_member_user_administration
    Path: tests/backend/test_ec_member_not_an_administrator.py
    """
    assert get_user_permissions({"role": UserRole.EC_MEMBER}).can_manage_users is False


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFAULT_PERMISSIONS still grants ec_member can_manage_finances (~98 gate sites). "
        "Same sequencing as above. When this xfail starts failing, delete the marker."
    ),
)
def test_permission_boolean_path_denies_ec_member_financial_writes():
    """Generated function header.

    Function: test_permission_boolean_path_denies_ec_member_financial_writes
    Path: tests/backend/test_ec_member_not_an_administrator.py
    """
    assert get_user_permissions({"role": UserRole.EC_MEMBER}).can_manage_finances is False


def test_the_boolean_path_is_the_only_one_left_open():
    """Documents the gap as an assertion, so the xfails above cannot be misread.

    If this ever fails, an EC member has regained administrative reach through
    the capability or relation path and the narrowing has regressed.
    """
    ec = _ec_subject()
    assert can(ec, "building.people.manage", SCOPE) is False
    assert can(ec, "building.finance.manage", SCOPE) is False
    assert "users.manage" not in RELATION_PERMISSION_MAP["committee_member"]
    assert "financial.manage" not in RELATION_PERMISSION_MAP["committee_member"]

    # And the one that is still open, stated plainly rather than implied.
    assert get_user_permissions({"role": UserRole.EC_MEMBER}).can_manage_users is True
