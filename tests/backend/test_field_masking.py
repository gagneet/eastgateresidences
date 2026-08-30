"""
Tests for services/field_masking.py and the obligations `decide()` attaches.

These pin the settled access decisions from
`docs/security/acl_information_access_implementation_plan.md` §4/§5 as
executable rules, so widening what someone can see has to be a deliberate edit
to this file rather than a side effect somewhere else.

The two that matter most:

  * an ordinary EC member does NOT see per-lot arrears; the treasurer does
    (ACT s 43, settled 2026-08-23);
  * a withheld field renders as WITHHELD, never as None/""/0 — rendering a
    masked balance as $0.00 tells the reader something false.
"""

from __future__ import annotations

import pytest

from models.user import UserRole
from services.capability_registry import decide
from services.field_masking import (
    OBLIGATION_FIELDS,
    WITHHELD,
    apply_obligations,
    fields_withheld_by,
    obligations_for,
)


# ── who is masked from what ──────────────────────────────────────────────────

def test_bank_details_are_masked_from_everyone_including_super_admin():
    """Only the dual-control payment flow unmasks these, and it does so explicitly."""
    for role in (UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER,
                 UserRole.EC_MEMBER, UserRole.OWNER, UserRole.TENANT):
        assert "MASK_BANK_DETAILS" in obligations_for(role), role


def test_ordinary_ec_member_cannot_see_per_lot_arrears():
    """Settled: ordinary EC members get aggregates; per-lot detail is the treasurer's."""
    assert "MASK_OTHER_OWNER_ARREARS" in obligations_for(
        UserRole.EC_MEMBER, ["ordinary_member"]
    )


def test_treasurer_can_see_per_lot_arrears():
    """ACT s 43 puts financial records with the treasurer."""
    assert "MASK_OTHER_OWNER_ARREARS" not in obligations_for(
        UserRole.EC_MEMBER, ["treasurer"]
    )


def test_chairperson_does_not_inherit_the_treasurer_view():
    """An office adds function, never rank."""
    assert "MASK_OTHER_OWNER_ARREARS" in obligations_for(
        UserRole.EC_MEMBER, ["chairperson"]
    )


def test_secretary_is_the_records_custodian_for_owner_contact():
    """ACT s 42. Other EC members do not get resident contact details."""
    assert "MASK_OWNER_CONTACT" not in obligations_for(UserRole.EC_MEMBER, ["secretary"])
    assert "MASK_OWNER_CONTACT" in obligations_for(UserRole.EC_MEMBER, ["treasurer"])
    assert "MASK_OWNER_CONTACT" in obligations_for(UserRole.EC_MEMBER, ["chairperson"])


def test_staff_get_masked_resident_pii_by_default():
    assert "MASK_OWNER_CONTACT" in obligations_for(UserRole.ADMIN_STAFF)


def test_owner_reading_their_own_record_is_not_masked_from_themselves():
    own = obligations_for(UserRole.OWNER, own_resource=True)
    assert "MASK_OTHER_OWNER_ARREARS" not in own
    assert "MASK_OWNER_CONTACT" not in own


def test_owner_reading_someone_elses_record_is_masked():
    other = obligations_for(UserRole.OWNER, own_resource=False)
    assert "MASK_OTHER_OWNER_ARREARS" in other
    assert "MASK_OWNER_CONTACT" in other


@pytest.mark.parametrize("role", [None, "", "not_a_role", UserRole.GUEST])
def test_unknown_or_absent_role_is_masked_from_everything(role):
    """Fail-closed: an unrecognised subject gets the maximum masking."""
    obligations = set(obligations_for(role))
    assert obligations == set(OBLIGATION_FIELDS), f"{role} should get every mask"


@pytest.mark.parametrize("role", [UserRole.OWNER, UserRole.TENANT, UserRole.ADMIN_STAFF])
def test_vote_attribution_is_masked_from_owners_tenants_and_staff(role):
    assert "MASK_VOTE_ATTRIBUTION" in obligations_for(role)


def test_vote_attribution_is_visible_to_the_committee_that_voted():
    assert "MASK_VOTE_ATTRIBUTION" not in obligations_for(UserRole.EC_MEMBER)


@pytest.mark.parametrize("role", [UserRole.OWNER, UserRole.TENANT, UserRole.ADMIN_STAFF])
def test_privileged_and_draft_material_is_masked_outside_ec_and_management(role):
    obligations = obligations_for(role)
    assert "MASK_PRIVILEGED_DOCUMENT" in obligations
    assert "MASK_DRAFT_MINUTES" in obligations


# ── applying the mask ────────────────────────────────────────────────────────

def test_withheld_is_distinguishable_from_absent_and_from_zero():
    payload = {"net_balance": 1234.56, "unit_number": "7"}
    masked = apply_obligations(payload, ["MASK_OTHER_OWNER_ARREARS"])

    assert masked["net_balance"] == WITHHELD
    assert masked["net_balance"] is not None
    assert masked["net_balance"] != 0
    assert masked["net_balance"] != ""
    assert masked["unit_number"] == "7"


def test_masking_recurses_into_nested_structures():
    payload = {
        "building": "13195",
        "units": [
            {"unit_number": "1", "net_balance": 100, "owner": {"primary_email": "a@b.c"}},
            {"unit_number": "2", "net_balance": 200, "owner": {"primary_email": "d@e.f"}},
        ],
    }
    masked = apply_obligations(payload, ["MASK_OTHER_OWNER_ARREARS", "MASK_OWNER_CONTACT"])

    for unit in masked["units"]:
        assert unit["net_balance"] == WITHHELD
        assert unit["owner"]["primary_email"] == WITHHELD
        assert unit["unit_number"] in ("1", "2")
    assert masked["building"] == "13195"


@pytest.mark.parametrize("key", ["primary_email", "PRIMARY_EMAIL", "Email", "email"])
def test_masking_is_case_insensitive_across_the_two_stores(key):
    """The same logical field is `primary_email` in Postgres and `email` in Mongo.

    A mask that depended on which store answered would fail intermittently.
    """
    masked = apply_obligations({key: "a@b.c"}, ["MASK_OWNER_CONTACT"])
    assert masked[key] == WITHHELD


def test_masking_never_mutates_the_input():
    payload = {"net_balance": 100, "nested": {"primary_mobile": "0400000000"}}
    snapshot = {"net_balance": 100, "nested": {"primary_mobile": "0400000000"}}

    apply_obligations(payload, ["MASK_OTHER_OWNER_ARREARS", "MASK_OWNER_CONTACT"])

    assert payload == snapshot


def test_no_obligations_returns_the_payload_untouched():
    payload = {"net_balance": 100}
    assert apply_obligations(payload, []) is payload


def test_a_resource_grant_field_mask_can_only_narrow():
    """core.resource_access_grants.field_mask adds withholdings; it cannot reveal."""
    base = fields_withheld_by(["MASK_OTHER_OWNER_ARREARS"])
    widened = fields_withheld_by(["MASK_OTHER_OWNER_ARREARS"], extra_fields=["special_note"])

    assert base <= widened
    assert "special_note" in widened

    masked = apply_obligations(
        {"net_balance": 1, "special_note": "x"},
        ["MASK_OTHER_OWNER_ARREARS"],
        extra_fields=["special_note"],
    )
    assert masked["special_note"] == WITHHELD
    assert masked["net_balance"] == WITHHELD


def test_unknown_obligation_names_are_ignored_rather_than_crashing():
    masked = apply_obligations({"a": 1}, ["NOT_A_REAL_OBLIGATION"])
    assert masked == {"a": 1}


def test_every_obligation_maps_to_at_least_one_field():
    """An obligation withholding nothing is a rule that silently does not apply."""
    empty = [name for name, fields in OBLIGATION_FIELDS.items() if not fields]
    assert not empty, f"obligations with no fields: {empty}"


# ── obligations reach the Decision ───────────────────────────────────────────

def test_decide_attaches_obligations_to_an_allow():
    ec = {"id": "e", "role": "ec_member", "effective_role": "ec_member",
          "building_id": "B1", "governance_offices": ["ordinary_member"]}
    decision = decide(ec, "building.finance.view", {"building_id": "B1"})

    assert decision.allowed
    assert "MASK_OTHER_OWNER_ARREARS" in decision.obligations


def test_decide_unmasks_arrears_for_the_treasurer():
    treasurer = {"id": "t", "role": "ec_member", "effective_role": "ec_member",
                 "building_id": "B1", "governance_offices": ["treasurer"]}
    decision = decide(treasurer, "building.finance.view", {"building_id": "B1"})

    assert decision.allowed
    assert "MASK_OTHER_OWNER_ARREARS" not in decision.obligations


def test_decide_unmasks_an_owners_own_unit():
    owner = {"id": "o", "role": "owner", "effective_role": "owner",
             "building_id": "B1", "unit_id": "U1"}
    decision = decide(owner, "unit.levies.view", {"building_id": "B1", "unit_id": "U1"})

    assert decision.allowed
    assert "MASK_OTHER_OWNER_ARREARS" not in decision.obligations


def test_a_denial_carries_no_obligations():
    """There is nothing to mask in a response that is never produced."""
    owner = {"id": "o", "role": "owner", "effective_role": "owner", "building_id": "B1"}
    decision = decide(owner, "building.finance.manage", {"building_id": "B1"})

    assert decision.allowed is False
    assert decision.obligations == ()
