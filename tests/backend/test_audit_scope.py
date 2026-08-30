"""The audited-action set, pinned as configuration.

GAP-SEC-011 required GAP-SEC-003's scope to be "either confirmed adequate or
widened, in configuration". The research widened it: an allow whose obligations
say the response could have carried personal information is now auditable,
because that is the population Privacy Act Part IIIC s 26WH(2) asks questions
about after a suspected breach.

These tests exist so the widening cannot be quietly undone, and so the two
easy mistakes — auditing everything by accident, or auditing nothing by accident
— both fail loudly.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from models.user import UserRole  # noqa: E402
from services.audit_scope import (  # noqa: E402
    AUDITED_ALLOW_PREFIXES,
    PERSONAL_INFORMATION_OBLIGATIONS,
    audit_reason,
    should_audit,
)
from services.capability_registry import CAPABILITY_REGISTRY, decide  # noqa: E402
from services.field_masking import OBLIGATION_FIELDS, obligations_for  # noqa: E402


@dataclass
class _Decision:
    allowed: bool
    capability: str = "building.dashboard.view"
    obligations: tuple[str, ...] = field(default_factory=tuple)


# ── The baseline: every denial ───────────────────────────────────────────────

@pytest.mark.parametrize("capability", ["building.dashboard.view", "platform.bi.view", "anything"])
def test_every_denial_is_audited(capability):
    """Plan §8 decision 8's one unambiguous half. A denial is always a signal."""
    assert should_audit(_Decision(allowed=False, capability=capability)) is True
    assert audit_reason(_Decision(allowed=False, capability=capability)) == "SCOPE_DENIAL"


# ── The §8 baseline: money and records allows ────────────────────────────────

@pytest.mark.parametrize("capability", sorted(AUDITED_ALLOW_PREFIXES))
def test_sensitive_capability_allows_are_audited(capability):
    """Generated function header.

    Function: test_sensitive_capability_allows_are_audited
    Path: tests/backend/test_audit_scope.py
    """
    decision = _Decision(allowed=True, capability=f"{capability}.something")
    assert should_audit(decision) is True
    assert audit_reason(decision).startswith("SCOPE_SENSITIVE_CAPABILITY:")


def test_an_ordinary_allow_is_not_audited():
    """The volume constraint that produced decision 8 is still respected."""
    decision = _Decision(allowed=True, capability="building.dashboard.view")

    assert should_audit(decision) is False
    assert audit_reason(decision) == "SCOPE_NOT_AUDITED"


# ── The widening ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("obligation", sorted(PERSONAL_INFORMATION_OBLIGATIONS))
def test_an_allow_that_could_disclose_personal_information_is_audited(obligation):
    """Privacy Act Part IIIC s 26WH(2): a 30-day breach assessment needs an access trail.

    An obligation naming a masking class means the response could have carried
    that class of personal information. That is exactly the population an
    assessment has to reason about.
    """
    decision = _Decision(
        allowed=True, capability="building.dashboard.view", obligations=(obligation,)
    )

    assert should_audit(decision) is True
    assert audit_reason(decision).startswith("SCOPE_PERSONAL_INFORMATION:")


def test_mask_bank_details_alone_does_not_make_everything_auditable():
    """The trap that would silently turn the widening into "audit every allow".

    field_masking.obligations_for() attaches MASK_BANK_DETAILS to EVERY decision,
    including a super admin's. If it counted as a personal-information signal,
    every allow in the system would be audited and the volume constraint behind
    decision 8 would be defeated without anyone deciding to defeat it.
    """
    assert "MASK_BANK_DETAILS" not in PERSONAL_INFORMATION_OBLIGATIONS

    decision = _Decision(
        allowed=True, capability="building.dashboard.view", obligations=("MASK_BANK_DETAILS",)
    )
    assert should_audit(decision) is False


def test_mask_bank_details_is_genuinely_universal():
    """Guards the premise of the test above rather than assuming it stays true."""
    for role in [UserRole.SUPER_ADMIN, UserRole.STRATA_MANAGER, UserRole.EC_MEMBER, UserRole.OWNER]:
        assert "MASK_BANK_DETAILS" in obligations_for(role, ()), (
            f"{role} no longer gets MASK_BANK_DETAILS — the audit-scope reasoning "
            "for excluding it from PERSONAL_INFORMATION_OBLIGATIONS needs revisiting"
        )


def test_every_personal_information_obligation_is_a_real_masking_class():
    """A typo here silently narrows the audit scope and nothing else would notice."""
    unknown = PERSONAL_INFORMATION_OBLIGATIONS - set(OBLIGATION_FIELDS)
    assert not unknown, (
        f"{sorted(unknown)} are not masking classes in field_masking.OBLIGATION_FIELDS. "
        "An obligation name that does not exist can never match, so these decisions "
        "would silently go unaudited."
    )


# ── The escape hatch ─────────────────────────────────────────────────────────

def test_audit_all_env_var_records_everything(monkeypatch):
    """Bounded-investigation mode, for when the standing scope is not enough."""
    monkeypatch.setenv("STRATAOS_AUDIT_ALL_DECISIONS", "true")

    decision = _Decision(allowed=True, capability="building.dashboard.view")
    assert should_audit(decision) is True
    assert audit_reason(decision) == "SCOPE_AUDIT_ALL_ENABLED"


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off", "maybe"])
def test_audit_all_defaults_off(monkeypatch, value):
    """Anything that is not an explicit yes must leave the standing scope in force."""
    monkeypatch.setenv("STRATAOS_AUDIT_ALL_DECISIONS", value)
    assert should_audit(_Decision(allowed=True, capability="building.dashboard.view")) is False


# ── Fail-closed on a malformed decision ──────────────────────────────────────

def test_an_unreadable_decision_is_audited():
    """Over-recording costs storage; under-recording costs a Part IIIC answer."""

    class Opaque:
        pass

    assert should_audit(Opaque()) is True


# ── Integration with the real evaluator ──────────────────────────────────────

def test_a_real_ec_member_arrears_read_is_audited():
    """End to end against decide(), not a hand-built stub.

    An ordinary EC member reading building arrears gets MASK_OTHER_OWNER_ARREARS.
    The capability is also under building.arrears, so this is doubly in scope —
    which is the point: the two rules overlap deliberately rather than
    partitioning.
    """
    subject = {"id": "u1", "role": UserRole.EC_MEMBER, "building_ids": ["B1"]}
    decision = decide(subject, "building.arrears.view", {"building_id": "B1"})

    assert decision.allowed is True
    assert "MASK_OTHER_OWNER_ARREARS" in decision.obligations
    assert should_audit(decision) is True


def test_the_prefixes_match_real_capabilities():
    """A prefix matching nothing is a rule that silently does nothing."""
    for prefix in AUDITED_ALLOW_PREFIXES:
        assert any(name.startswith(prefix) for name in CAPABILITY_REGISTRY), (
            f"audit prefix {prefix!r} matches no capability in CAPABILITY_REGISTRY"
        )
