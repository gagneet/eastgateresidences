# @featuretrace:scoped-capability-access — Field-level masking obligations.
# Layer: service
# Data flow: decide() -> obligations tuple -> apply_obligations(payload) -> response (scope param: building|global).
# Related: backend/services/capability_registry.py
#           backend/services/authorisation_context.py
#           docs/security/acl_information_access_implementation_plan.md §5
# Tests: tests/backend/test_field_masking.py

"""Decide which fields a subject may not see, and withhold them in one place.

## Why obligations rather than allow/deny

Access to a *record* is not access to every *field*. An owner may legitimately
read the levy register and not the other owners' arrears within it; a staff
member may read the resident directory and not the residents' phone numbers. A
read is therefore rarely allow-or-deny — it is usually
allow-with-these-fields-withheld.

`decide()` returns those withholdings as ``obligations``. This module both
computes them and applies them, so the rule lives in exactly one place. The plan
is explicit that masking must not be implemented per route: rules applied in
twenty serialisers drift apart, and every drift is in the disclosing direction.

## Withheld is not empty

A masked field renders as ``WITHHELD``, never as ``None``, ``""`` or ``0``.
Rendering a withheld amount as ``$0.00`` tells the reader something false — that
the balance is nil — and the codebase already carries this rule for finance
("missing and zero are distinct states"). The same applies here: the reader must
be able to tell "you may not see this" from "there is nothing here".

## Statutory basis

The masking table implements ACT UTMA 2011 s 113 and s 116 (privacy-filtered
register and record access) and feeds the s 120A inspection response, which must
never return more than the requester could see directly. See
``docs/security/acl_information_access_implementation_plan.md`` §5 and
``tasks/GAP-SEC-010``.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from models.user import UserRole

#: Rendered in place of a withheld value. Deliberately a distinctive string
#: rather than None/""/0 so a masked field is never mistaken for an absent or
#: zero one, in a UI or in a downstream consumer.
WITHHELD = "__withheld__"


# ── Field classes ────────────────────────────────────────────────────────────
# Column/attribute names verified against the live schema on 2026-08-23. This
# list IS the contract: a sensitive field not named here is not masked. Adding a
# column that carries one of these kinds of data means adding it here — the
# tests assert the classes are non-empty but cannot know about a field nobody
# declared.

BANK_DETAIL_FIELDS = frozenset({
    # finance.trust_accounts, ops.vendors
    "bsb", "bsb_encrypted", "masked_bsb",
    "account_number", "account_number_encrypted", "masked_account_number",
    "bank_account", "bank_account_token", "account_name",
    "payment_token", "card_number",
})

OWNER_CONTACT_FIELDS = frozenset({
    # core.parties, and the legacy Mongo user shape
    "primary_email", "secondary_email", "primary_mobile", "postal_address",
    "email", "phone", "mobile", "address",
})

ARREARS_FIELDS = frozenset({
    "net_balance", "arrears", "arrears_cents", "total_outstanding",
    "outstanding_balance", "amount_overdue",
})

VOTE_ATTRIBUTION_FIELDS = frozenset({
    "voter_id", "voted_by", "vote_value", "voter_party_id", "voter_lot_id",
})

PRIVILEGED_DOCUMENT_FIELDS = frozenset({
    "privileged_content", "legal_advice", "storage_key",
})

DRAFT_MINUTES_FIELDS = frozenset({
    "draft_minutes", "in_camera_notes", "confidential_notes",
})

#: obligation name → the fields it withholds.
OBLIGATION_FIELDS: Mapping[str, frozenset[str]] = {
    "MASK_BANK_DETAILS": BANK_DETAIL_FIELDS,
    "MASK_OWNER_CONTACT": OWNER_CONTACT_FIELDS,
    "MASK_OTHER_OWNER_ARREARS": ARREARS_FIELDS,
    "MASK_VOTE_ATTRIBUTION": VOTE_ATTRIBUTION_FIELDS,
    "MASK_PRIVILEGED_DOCUMENT": PRIVILEGED_DOCUMENT_FIELDS,
    "MASK_DRAFT_MINUTES": DRAFT_MINUTES_FIELDS,
}

_MANAGEMENT = frozenset({UserRole.STRATA_MANAGER, UserRole.STRATA_ADMIN, UserRole.SUPER_ADMIN})


def obligations_for(
    role: str,
    offices: Iterable[str] = (),
    *,
    own_resource: bool = False,
) -> tuple[str, ...]:
    """Return the maskings that apply to this subject, per plan §5.

    Pure and I/O-free, so ``decide()`` keeps its no-I/O contract.

    ``own_resource`` means the subject is reading their OWN lot/record. An owner
    always sees their own arrears and their own contact details; the masking
    exists to stop them reading somebody else's.

    Ordering note: this returns what to withhold, never what to reveal. A caller
    that cannot determine ``own_resource`` should leave it False — the
    fail-closed direction is to mask.
    """
    role = str(role or UserRole.GUEST)
    offices = {str(o).lower() for o in offices}
    obligations: set[str] = set()

    # Bank details are withheld from everyone here. The dual-control payment
    # flow (GAP-SEC-007) is the only path that may unmask them, and it does so
    # explicitly rather than by being a privileged role.
    obligations.add("MASK_BANK_DETAILS")

    # Owner contact: the secretary is the statutory records custodian (s 42) and
    # management needs it operationally. Everyone else — including ordinary EC
    # members — does not get it by default.
    if not (role in _MANAGEMENT or "secretary" in offices or own_resource):
        obligations.add("MASK_OWNER_CONTACT")

    # Per-lot arrears sit with the treasurer (s 43) and management. Ordinary EC
    # members see aggregates only — settled decision, see the plan §8.
    if not (role in _MANAGEMENT or "treasurer" in offices or own_resource):
        obligations.add("MASK_OTHER_OWNER_ARREARS")

    # How an individual voted is visible to the committee that voted, not to the
    # owners at large and not to staff.
    if role not in _MANAGEMENT and role != UserRole.EC_MEMBER:
        obligations.add("MASK_VOTE_ATTRIBUTION")
    if role == UserRole.ADMIN_STAFF:
        obligations.add("MASK_VOTE_ATTRIBUTION")

    # Privileged material: EC and management only, and never in a s 120A
    # response regardless of who asks.
    if not (role in _MANAGEMENT or role == UserRole.EC_MEMBER):
        obligations.add("MASK_PRIVILEGED_DOCUMENT")

    # Draft and in-camera minutes are not owner-visible until approved.
    if not (role in _MANAGEMENT or role == UserRole.EC_MEMBER):
        obligations.add("MASK_DRAFT_MINUTES")

    return tuple(sorted(obligations))


def fields_withheld_by(obligations: Iterable[str], extra_fields: Iterable[str] = ()) -> frozenset[str]:
    """Resolve obligation names to the concrete field names they withhold.

    ``extra_fields`` carries a per-resource ``field_mask`` from
    ``core.resource_access_grants``, so a specific grant can withhold more than
    the class policy does. It can only ADD — a grant may narrow what a subject
    sees, never widen it.
    """
    withheld: set[str] = set()
    for obligation in obligations or ():
        withheld |= OBLIGATION_FIELDS.get(str(obligation), frozenset())
    withheld |= {str(f) for f in (extra_fields or ())}
    return frozenset(withheld)


def apply_obligations(
    payload: Any,
    obligations: Iterable[str],
    *,
    extra_fields: Iterable[str] = (),
) -> Any:
    """Return a copy of ``payload`` with withheld fields replaced by ``WITHHELD``.

    Recurses through dicts and lists so a masked field is caught wherever it
    appears in a nested response, not only at the top level. The input is never
    mutated.

    Keys are matched case-insensitively: the same logical field appears as
    ``primary_email`` from Postgres and ``email`` from the legacy Mongo shape,
    and a mask that depends on which store answered would be a mask that fails
    intermittently.
    """
    withheld = fields_withheld_by(obligations, extra_fields)
    if not withheld:
        return payload
    lowered = {f.lower() for f in withheld}
    return _mask(payload, lowered)


def _mask(value: Any, withheld: set[str]) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (WITHHELD if str(key).lower() in withheld else _mask(item, withheld))
            for key, item in value.items()
        }
    # str/bytes are iterable but are values, not containers.
    if isinstance(value, (list, tuple)):
        masked = [_mask(item, withheld) for item in value]
        return type(value)(masked) if isinstance(value, tuple) else masked
    return value
