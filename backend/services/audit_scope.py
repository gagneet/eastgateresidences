# @featuretrace:scoped-capability-access — Which authorisation decisions get recorded.
# Layer: service
# Data flow: Decision -> should_audit() -> core.audit_events write decision (global).
# Related: backend/services/capability_registry.py
#          docs/security/audit_scope_statutory_research_2026_08_24.md
# Tests: tests/backend/test_audit_scope.py

"""Decide which authorisation decisions are worth writing to the audit trail.

## Why this is configuration and not an `if` in the writer

The plan's §8 decision 8 set the initial scope — all denials, plus allows on
financial and records actions — **on write-volume grounds**, and said so
explicitly: it is "NOT a compliance conclusion". It also required that the
audited-action set be configuration, so that the GAP-SEC-011 research could
widen it without a rewrite.

That research is now done
(``docs/security/audit_scope_statutory_research_2026_08_24.md``) and it did widen
it. This module is where that answer lives.

## What the research actually found

No instrument reviewed — UTMA 2011 (ACT), the Agents Act 2003 (ACT), APP 11 or
APP 12 — requires a record of who *read* anything. Every retention duty found is
a duty to keep the records themselves; every record-making duty found is a duty
to record transactions. On the statutes alone, denial-only logging is defensible.

The obligation that actually bites is **Privacy Act 1988 (Cth) Part IIIC**.
s 26WH(2) requires an entity that suspects an eligible data breach to complete
its assessment within "30 calendar days after the day the entity became aware of
the grounds", and s 26WE(2) defines that breach to include "unauthorised access
to … personal information". An entity cannot assess whether unauthorised access
occurred if it kept no record of access.

So the widening is specific: **an allow that could have disclosed personal
information is auditable**, because that is the population Part IIIC asks
questions about. Everything else stays denial-only.

## Why the rule reads the obligations rather than a route list

``Decision.obligations`` already names the masking classes in play, and it is
computed for every allow. A decision carrying ``MASK_OWNER_CONTACT`` is, by
construction, a decision on a response that could have contained owner contact
details. Deriving auditability from the obligations means the audited set tracks
the masking policy automatically — a new masking class is audited the day it is
added, with no route list to remember to update.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

#: Capability name prefixes whose ALLOWS are audited regardless of obligations.
#: This is the plan §8 decision-8 baseline: money and statutory records.
AUDITED_ALLOW_PREFIXES: frozenset[str] = frozenset({
    "building.finance",
    "building.arrears",
    "building.documents",
    "unit.levies",
    "organisation.users",
    "building.people",
    "platform.",
})

#: Masking classes whose presence on an ALLOW makes it auditable.
#:
#: The obligation says the response could have carried this class of personal
#: information — which is precisely the population Privacy Act Part IIIC asks
#: about after a suspected breach. MASK_BANK_DETAILS is deliberately absent: it
#: is attached to EVERY decision (see field_masking.obligations_for), so it
#: carries no signal and including it would silently mean "audit every allow".
PERSONAL_INFORMATION_OBLIGATIONS: frozenset[str] = frozenset({
    "MASK_OWNER_CONTACT",
    "MASK_OTHER_OWNER_ARREARS",
    "MASK_VOTE_ATTRIBUTION",
    "MASK_PRIVILEGED_DOCUMENT",
    "MASK_DRAFT_MINUTES",
})

#: Escape hatch for an incident: set STRATAOS_AUDIT_ALL_DECISIONS=true to record
#: every decision, allow or deny. Intended for a bounded investigation window,
#: not as a standing setting — Phase 5 takes guarded routes from 14 to roughly
#: 1400 and this makes every one of them a write.
_AUDIT_ALL_ENV = "STRATAOS_AUDIT_ALL_DECISIONS"


class _DecisionLike(Protocol):
    """The shape ``should_audit`` needs, so it does not import the evaluator."""

    allowed: bool
    capability: str
    obligations: tuple[str, ...]


def audit_everything() -> bool:
    """True when the environment has asked for exhaustive decision auditing."""
    return str(os.getenv(_AUDIT_ALL_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def should_audit(decision: Any) -> bool:
    """Return whether this decision belongs in the audit trail.

    Fail-closed in the direction that matters here: anything this function cannot
    read is audited rather than dropped. Over-recording costs storage;
    under-recording costs the ability to answer a Part IIIC assessment, and the
    gap is only discovered when someone asks.
    """
    if audit_everything():
        return True

    allowed = getattr(decision, "allowed", None)
    if allowed is None:
        return True  # unreadable decision — record it and let the reader judge
    if not allowed:
        return True  # every denial, per plan §8 decision 8

    capability = str(getattr(decision, "capability", "") or "")
    if any(capability.startswith(prefix) for prefix in AUDITED_ALLOW_PREFIXES):
        return True

    obligations = getattr(decision, "obligations", ()) or ()
    return bool(PERSONAL_INFORMATION_OBLIGATIONS.intersection(str(o) for o in obligations))


def audit_reason(decision: Any) -> str:
    """A short, stable code explaining WHY this decision was recorded.

    Stored alongside the audit row so a later scope review can tell which rule
    pulled a decision in, and drop or widen that rule specifically instead of
    guessing at the whole set.
    """
    if audit_everything():
        return "SCOPE_AUDIT_ALL_ENABLED"
    if not getattr(decision, "allowed", True):
        return "SCOPE_DENIAL"

    capability = str(getattr(decision, "capability", "") or "")
    for prefix in sorted(AUDITED_ALLOW_PREFIXES):
        if capability.startswith(prefix):
            return f"SCOPE_SENSITIVE_CAPABILITY:{prefix}"

    obligations = PERSONAL_INFORMATION_OBLIGATIONS.intersection(
        str(o) for o in (getattr(decision, "obligations", ()) or ())
    )
    if obligations:
        return f"SCOPE_PERSONAL_INFORMATION:{sorted(obligations)[0]}"
    return "SCOPE_NOT_AUDITED"
