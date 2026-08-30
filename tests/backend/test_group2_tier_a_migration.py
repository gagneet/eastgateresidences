"""GAP-SEC-005 group 2, tier A — the routes where a wrong answer moves money.

Group 2 is 330 routes on paper, which is a planning figure rather than a work
queue: it lumps "read the levy summary" together with "release a payment" and
"change a supplier's bank details". This file covers the 13 routes where being
wrong means money leaves, bank credentials are read or written, or financial data
is exported in bulk.

The migration is **additive** — each route keeps its inline check and gains
``require_capability``, so the effective rule is the intersection and the change
can only narrow. These tests assert that intersection, and name the one
deliberate exclusion.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from models.user import UserRole  # noqa: E402
from services.capability_registry import CAPABILITY_REGISTRY, can  # noqa: E402

SCRIPT = REPO_ROOT / "backend" / "scripts" / "audits" / "classify_group2_financial_routes.py"
ROUTERS = REPO_ROOT / "backend" / "routers"

#: handler -> router file. This IS the record of what tier A covers.
MIGRATED = {
    "approve_payment_batch": "trust_accounting.py",
    "second_approve_payment_batch": "trust_accounting.py",
    "create_payment_batch": "trust_accounting.py",
    "create_trust_account": "trust_accounting.py",
    "download_aba_file": "trust_accounting.py",
    "export_trust_audit": "trust_accounting.py",
    "export_finance": "finance.py",
    "approve_invoice": "ap_approval.py",
    "approve_payment_plan": "payment_plans.py",
    "create_trust_account_v2": "trust_phase1.py",
    "update_trust_account": "trust_phase1.py",
    "list_accounts": "demo_bank.py",
}

CAPABILITY = "building.finance.manage"
SCOPE = {"building_id": "BLD-1"}


@pytest.fixture(scope="module")
def classifier():
    """Load the audit script without needing it importable as a package."""
    spec = importlib.util.spec_from_file_location("classify_group2", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _signature(fname: str, handler: str) -> str:
    """Return a handler's signature text, from ``def`` to its closing paren."""
    text = (ROUTERS / fname).read_text()
    match = re.search(rf"^async def {re.escape(handler)}\(", text, re.MULTILINE)
    assert match, f"{handler} not found in {fname}"
    depth = 0
    for index in range(match.start(), len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[match.start(): index + 1]
    pytest.fail(f"unbalanced signature for {handler}")


# ── The wiring ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("handler,fname", sorted(MIGRATED.items()))
def test_tier_a_route_is_guarded(handler, fname):
    """Every money-moving route declares the capability."""
    signature = _signature(fname, handler)
    assert f'require_capability("{CAPABILITY}"' in signature, (
        f"{fname}::{handler} lost its tier-A capability guard"
    )


@pytest.mark.parametrize("handler,fname", sorted(MIGRATED.items()))
def test_tier_a_route_resolves_its_building_from_context(handler, fname):
    """None of these name a building in the URL.

    Without ``building_from_context=True`` the scope carries no building_id and
    every request denies with DENY_SCOPE_INCOMPLETE — an outage that reads like a
    policy decision.
    """
    assert "building_from_context=True" in _signature(fname, handler)


@pytest.mark.parametrize("handler,fname", sorted(MIGRATED.items()))
def test_the_original_check_was_kept(handler, fname):
    """Additive, not swapped. The inline guard must still be in the body.

    GAP-SEC-005 is explicit: do not swap and hope. If the capability turns out to
    be wrong, the original check is what stops the route opening.
    """
    text = (ROUTERS / fname).read_text()
    match = re.search(rf"^async def {re.escape(handler)}\(", text, re.MULTILINE)
    body = text[match.start(): match.start() + 3000]
    inline_markers = (
        "_require_finance_role", "_require_manager", "_require_trust_manage",
        "_require_role", "permissions.", "effective_role", "require_feature",
    )
    assert any(marker in body for marker in inline_markers), (
        f"{fname}::{handler} appears to have lost its original check — the "
        "capability must be ADDED alongside it, never substituted for it"
    )


def test_the_capability_exists():
    """A typo'd capability denies everything and looks like a policy decision."""
    assert CAPABILITY in CAPABILITY_REGISTRY


# ── The access change ────────────────────────────────────────────────────────

def _subject(role: str, **extra) -> dict:
    """Generated function header.

    Function: _subject
    Path: tests/backend/test_group2_tier_a_migration.py
    """
    return {"id": "u1", "role": role, "building_ids": ["BLD-1"], **extra}


def test_ec_member_loses_trust_payment_operations():
    """The intended narrowing, and the reason tier A went first.

    ``trust_accounting._require_finance_role`` admits ``ec_member``, so an
    ordinary EC member could approve a payment batch, create a trust account and
    download an ABA file — the last carrying supplier BSB and account numbers in
    the clear. The access matrix gives payment execution to the treasurer against
    a recorded EC authority, and ordinary EC members read-only.
    """
    assert can(_subject(UserRole.EC_MEMBER), CAPABILITY, SCOPE) is False


def test_the_inline_guard_still_admits_ec_member():
    """Guards the premise above: the capability is what narrows, not the helper.

    If this ever fails, ``_require_finance_role`` has been tightened separately
    and the additive reasoning in this file needs revisiting.
    """
    source = (ROUTERS / "trust_accounting.py").read_text()
    block = source[source.index("def _require_finance_role"):][:600]
    assert "ec_member" in block, (
        "_require_finance_role no longer admits ec_member — the narrowing is now "
        "in two places, and this test file's reasoning should be updated"
    )


@pytest.mark.parametrize(
    "role", [UserRole.SUPER_ADMIN, UserRole.STRATA_ADMIN, UserRole.STRATA_MANAGER]
)
def test_management_keeps_tier_a_access(role):
    """The migration must not lock out the people who actually run the trust account."""
    subject = _subject(role, organisation_id="BLD-1", tenant_id="BLD-1")
    assert can(subject, CAPABILITY, SCOPE) is True


@pytest.mark.parametrize(
    "role", [UserRole.OWNER, UserRole.TENANT, UserRole.ADMIN_STAFF, UserRole.GUEST,
             UserRole.REAL_ESTATE_AGENT, UserRole.SERVICE_PROVIDER]
)
def test_nobody_else_reaches_tier_a(role):
    """Generated function header.

    Function: test_nobody_else_reaches_tier_a
    Path: tests/backend/test_group2_tier_a_migration.py
    """
    assert can(_subject(role), CAPABILITY, SCOPE) is False


def test_a_manager_at_another_building_is_denied():
    """Cross-building denial on the money routes specifically."""
    subject = _subject(UserRole.STRATA_MANAGER, building_ids=["OTHER"])
    assert can(subject, CAPABILITY, {"building_id": "BLD-1"}) is False


# ── The tier rules themselves ────────────────────────────────────────────────

def test_no_tier_a_route_is_left_unguarded(classifier):
    """The ratchet. A new money-moving route must be guarded or explicitly excluded."""
    routes = classifier.scan()
    gaps = [
        f"{r['file']}:{r['line']} {r['method']} {r['path']}"
        for r in routes
        if r["tier"] == "A"
        and r["guard_style"] != "capability"
        and r["func"] not in classifier.TIER_A_EXCLUSIONS
    ]
    assert not gaps, (
        "tier-A financial route(s) without a capability guard:\n  " + "\n  ".join(gaps)
    )


def test_every_exclusion_carries_a_real_reason(classifier):
    """An exclusion is a decision, and a decision needs its argument written down."""
    assert classifier.TIER_A_EXCLUSIONS, "expected at least the lookup_bsb exclusion"
    for handler, reason in classifier.TIER_A_EXCLUSIONS.items():
        assert len(reason) > 80, f"{handler}'s exclusion reason is too thin to review"


def test_prose_cannot_set_a_risk_tier(classifier):
    """Regression: a description string once promoted an IMPORT route to tier A.

    ``file: UploadFile = File(..., description="Bank CSV export file")`` matched
    the export pattern, and a query parameter named ``bank_account_id`` matched
    the bank-detail pattern on a plain reconciliation list. Tiering now reads
    identity for path rules and stripped annotations for model rules.
    """
    signature = '''
    async def import_thing(
        file: UploadFile = File(..., description="Bank CSV export file"),
        bank_account_id: str = Query(None),
    ):
    '''
    assert classifier._tier("POST", "/import/csv", "import_csv_endpoint", signature) != "A"


def test_a_bank_account_payload_still_sets_tier_a(classifier):
    """The counterpart: the model annotation must still be decisive."""
    signature = "async def update_account(account_id: str, payload: BankAccountUpdate):"
    assert classifier._tier("PATCH", "/accounts/{account_id}", "update_account", signature) == "A"
