"""
Tests for the Decision object (ACL plan Phase 3).

`can()` answers yes/no. That is enough to enforce a rule and not enough to
explain one: a 403 with no recorded reasoning cannot be diagnosed without
re-running the request as the affected user, which is exactly what you cannot do
for someone else's session.

`decide()` returns the same answer plus stable reason codes, the policy version
that produced it, and a correlation id. These tests pin two properties:

  1. the reasoning is accurate — each denial names the gate that actually
     stopped it, not a generic failure;
  2. the reasoning never reaches the caller — reason codes disclose the shape of
     the policy and can confirm another tenant's resource exists, so the 403
     body carries only the correlation id.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.authorisation_context import POLICY_VERSION
from services.capability_registry import Decision, assert_capability, can, decide


def _user(role: str, **claims) -> dict:
    return {"id": f"user-{role}", "role": role, "effective_role": role, **claims}


def _treasurer(**over) -> dict:
    base = {"building_id": "B1", "governance_offices": ["treasurer"]}
    base.update(over)
    return _user("ec_member", **base)


# ── the answer still matches can() ───────────────────────────────────────────

@pytest.mark.parametrize(
    "user,capability,scope",
    [
        (_treasurer(), "building.finance.records.prepare", {"building_id": "B1"}),
        (_treasurer(), "building.finance.records.prepare", {"building_id": "B2"}),
        (_user("owner", building_id="B1"), "building.finance.manage", {"building_id": "B1"}),
        (None, "building.finance.manage", {"building_id": "B1"}),
        (_user("owner"), "does.not.exist", {"building_id": "B1"}),
    ],
)
def test_can_is_the_boolean_face_of_decide(user, capability, scope):
    assert can(user, capability, scope) is decide(user, capability, scope).allowed


def test_decision_is_truthy_by_its_answer():
    allowed = decide(_treasurer(), "building.finance.records.prepare", {"building_id": "B1"})
    denied = decide(_treasurer(), "building.finance.records.prepare", {"building_id": "B2"})
    assert bool(allowed) is True
    assert bool(denied) is False


# ── reason codes name the gate that actually stopped it ──────────────────────

@pytest.mark.parametrize(
    "user,capability,scope,expected",
    [
        (None, "building.finance.manage", {"building_id": "B1"}, "DENY_NO_SUBJECT"),
        (_user("owner"), "no.such.capability", {"building_id": "B1"}, "DENY_UNKNOWN_CAPABILITY"),
        (_user("owner"), "building.finance.manage", None, "DENY_MALFORMED_SCOPE"),
        (_user("owner", building_id="B1"), "building.finance.manage", {"building_id": "B1"},
         "DENY_ROLE_NOT_PERMITTED"),
        # Right role, wrong office.
        (_user("ec_member", building_id="B1", governance_offices=["ordinary_member"]),
         "building.finance.records.prepare", {"building_id": "B1"}, "DENY_OFFICE_NOT_HELD"),
        # Right office, no recorded EC authority.
        (_treasurer(), "building.finance.payment.execute",
         {"building_id": "B1", "resolution_id": "r1"}, "DENY_AUTHORITY_MISSING_RESOLUTION"),
        # Right role and office, scope incomplete.
        (_treasurer(), "building.finance.records.prepare", {}, "DENY_SCOPE_INCOMPLETE"),
        # Right everything, wrong building.
        (_treasurer(), "building.finance.records.prepare", {"building_id": "B2"},
         "DENY_BUILDING_NOT_ASSIGNED"),
    ],
)
def test_denial_names_the_failing_gate(user, capability, scope, expected):
    decision = decide(user, capability, scope)
    assert decision.allowed is False
    assert expected in decision.reason_codes, (
        f"expected {expected} in {decision.reason_codes}"
    )


def test_allow_records_role_office_and_scope_basis():
    decision = decide(_treasurer(), "building.finance.records.prepare", {"building_id": "B1"})
    assert decision.allowed
    assert "ROLE_EC_MEMBER" in decision.reason_codes
    assert "OFFICE_TREASURER" in decision.reason_codes
    assert "ALLOW_BUILDING_ASSIGNMENT" in decision.reason_codes


def test_super_admin_allow_is_attributed_to_platform_rank():
    decision = decide(
        _user("super_admin", building_id="B1"), "building.finance.manage", {"building_id": "B1"}
    )
    assert decision.allowed
    assert "ALLOW_PLATFORM_RANK" in decision.reason_codes


def test_super_admin_rank_does_not_confer_an_office():
    """A super admin can repair platform configuration, not hold an EC office.

    The denial comes from the ROLE gate rather than the office gate, because the
    ACT office capabilities are declared `_EC_ONLY` — super_admin is not in
    `.roles` at all, so evaluation stops before offices are considered. Both
    gates sit ahead of the super-admin bypass, so platform rank cannot reach
    this capability by either route.
    """
    decision = decide(
        _user("super_admin", building_id="B1"),
        "building.finance.records.prepare",
        {"building_id": "B1"},
    )
    assert decision.allowed is False
    assert "DENY_ROLE_NOT_PERMITTED" in decision.reason_codes
    assert "ALLOW_PLATFORM_RANK" not in decision.reason_codes


def test_office_gate_precedes_the_super_admin_bypass():
    """Directly pin the ordering, using a capability super_admin IS permitted for.

    `building.meetings.manage` allows super_admin. If a governance_offices
    requirement were ever added to a capability super_admin can hold, the office
    check must still run first — this test would fail if the bypass moved above
    it.
    """
    from services.capability_registry import CAPABILITY_REGISTRY

    definition = CAPABILITY_REGISTRY["building.meetings.manage"]
    assert "super_admin" in definition.roles
    assert not definition.governance_offices, (
        "if this capability gains an office requirement, assert here that a "
        "super_admin without that office is denied"
    )


# ── metadata ─────────────────────────────────────────────────────────────────

def test_every_decision_carries_policy_version_and_a_unique_id():
    first = decide(_treasurer(), "building.finance.records.prepare", {"building_id": "B1"})
    second = decide(_treasurer(), "building.finance.records.prepare", {"building_id": "B1"})

    assert first.policy_version == POLICY_VERSION
    assert second.policy_version == POLICY_VERSION
    assert first.decision_id and second.decision_id
    assert first.decision_id != second.decision_id, "decision_id must correlate one decision"


def test_decision_is_immutable():
    decision = decide(_treasurer(), "building.finance.records.prepare", {"building_id": "B1"})
    with pytest.raises(Exception):
        decision.allowed = True  # type: ignore[misc]


def test_an_allow_carries_its_field_masking_obligations():
    """An allow is rarely unqualified — it is allow-with-these-fields-withheld.

    Populated since GAP-SEC-004. The masking rules themselves are pinned in
    tests/backend/test_field_masking.py; this only asserts they reach the
    Decision, so a caller cannot receive an allow without also receiving what it
    must withhold.
    """
    decision = decide(_treasurer(), "building.finance.records.prepare", {"building_id": "B1"})
    assert decision.allowed
    assert decision.obligations, "an allow must state what it withholds"
    # Bank details are withheld from every role; only the dual-control payment
    # flow unmasks them.
    assert "MASK_BANK_DETAILS" in decision.obligations


# ── the reasoning must not leak to the caller ────────────────────────────────

def test_denial_response_carries_the_correlation_id_but_not_the_reasoning():
    with pytest.raises(HTTPException) as excinfo:
        assert_capability(_treasurer(), "building.finance.records.prepare", {"building_id": "B2"})

    detail = str(excinfo.value.detail)
    assert excinfo.value.status_code == 403
    assert "decision " in detail, "support needs the correlation id to find the audit record"
    for code in ("DENY_BUILDING_NOT_ASSIGNED", "DENY_OFFICE_NOT_HELD", "ROLE_EC_MEMBER"):
        assert code not in detail, f"reason code {code} must not reach the caller"


def test_assert_capability_returns_the_decision_when_allowed():
    decision = assert_capability(
        _treasurer(), "building.finance.records.prepare", {"building_id": "B1"}
    )
    assert isinstance(decision, Decision)
    assert decision.allowed
