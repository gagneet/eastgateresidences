"""
Tests for GAP-GOV-001: E-voting with jurisdiction-aware proxy caps and ballot audit.

Covers:
  1.  Proxy validation — ACT strata_manager rejected as holder (s.76(2))
  2.  Proxy validation — valid holder accepted
  3.  Proxy validation — grantor cannot be their own proxy
  4.  Proxy validation — completed AGM blocks new proxy
  5.  Proxy summary — groups by holder correctly
  6.  Quorum calculation — UE-weighted, ACT 1/3 threshold
  7.  UE-weighted results — ordinary and special resolution thresholds
  8.  Ballot entry hash chain — genesis and chaining
  9.  BallotEntry tampering detection — hash mismatch raises ValueError
  10. BallotSeal creation and hash verification
  11. BallotSeal tampering detection — hash mismatch raises ValueError
  12. verify_chain — detects broken entry
  13. POST /agm/{agm_id}/proxy — submit proxy endpoint
  14. DELETE /agm/{agm_id}/proxy — revoke proxy endpoint
  15. POST /agm/{agm_id}/close-ballot — role guard (owner rejected)
  16. POST /agm/{agm_id}/close-ballot — seals ballot and marks AGM completed
  17. GET /agm/{agm_id}/evidence-pack — role guard (owner rejected)
  18. GET /agm/{agm_id}/evidence-pack — returns full pack for chairman
  19. Multi-tenant isolation — building 16244 cannot see 13195 data
  20. NSW proxy cap — 5% numerical limit enforced

Run:
    cd /home/gagneet/strata-management
    backend/venv/bin/python3 -m pytest tests/backend/test_voting_evoting.py -q
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.ballot_audit import (
    BallotEntry,
    BallotEntryCreate,
    BallotSeal,
    BallotSealCreate,
    compute_entry_hash,
    compute_seal_hash,
    verify_chain,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


class _AsyncCursor:
    """Async-iterable mock cursor for testing code that uses `async for` on DB results."""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for item in self._items:
            yield item

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, n):
        return self._items


# ─── Constants ────────────────────────────────────────────────────────────────

BUILDING_A = "13195"
BUILDING_B = "16244"

AGM_ID = "agm-test-001"
MOTION_ID_1 = "motion-001"
MOTION_ID_2 = "motion-002"


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def super_admin():
    return {
        "id": "user-admin-001",
        "full_name": "Super Admin",
        "email": "admin@test.com",
        "role": "super_admin",
        "effective_role": "super_admin",
        "unit_number": None,
        "is_approved": True,
        "building_id": BUILDING_A,
    }


@pytest.fixture
def chairman():
    # "chairman" is a legacy alias; normalize_user_role maps it to "strata_admin"
    # at runtime. Tests must use the canonical role so effective_role() returns the
    # correct value without needing a live normalize_user_role call chain.
    return {
        "id": "user-chair-001",
        "full_name": "Chair Person",
        "email": "chair@test.com",
        "role": "strata_admin",
        "effective_role": "strata_admin",
        "unit_number": "UA001",
        "is_approved": True,
        "building_id": BUILDING_A,
    }


@pytest.fixture
def strata_manager():
    return {
        "id": "user-sm-001",
        "full_name": "Strata Manager",
        "email": "sm@test.com",
        "role": "strata_manager",
        "effective_role": "strata_manager",
        "unit_number": None,
        "is_approved": True,
        "building_id": BUILDING_A,
    }


@pytest.fixture
def owner_a():
    return {
        "id": "user-owner-001",
        "full_name": "Owner Alpha",
        "email": "ownerA@test.com",
        "role": "owner",
        "effective_role": "owner",
        "unit_number": "UA002",
        "is_approved": True,
        "building_id": BUILDING_A,
    }


@pytest.fixture
def owner_b():
    return {
        "id": "user-owner-002",
        "full_name": "Owner Beta",
        "email": "ownerB@test.com",
        "role": "owner",
        "effective_role": "owner",
        "unit_number": "UA003",
        "is_approved": True,
        "building_id": BUILDING_A,
    }


@pytest.fixture
def agm_doc():
    return {
        "id": AGM_ID,
        "building_id": BUILDING_A,
        "title": "2026 Annual General Meeting",
        "date": "2026-06-01",
        "location": "Community Room",
        "agenda": ["Financials", "Levy Approval"],
        "status": "in_progress",
        "motions": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture
def completed_agm_doc(agm_doc):
    return {**agm_doc, "status": "completed"}


# ─── Helper: build a valid BallotEntry dict ───────────────────────────────────


def _make_entry_dict(
        motion_id: str,
        unit_number: str,
        vote: str,
        prev_hash: str,
        ue_weight: int = 10,
) -> dict:
    """Return a ballot entry dict with a correctly computed entry_hash."""
    created_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "id": str(uuid.uuid4()),
        "building_id": BUILDING_A,
        "agm_id": AGM_ID,
        "motion_id": motion_id,
        "unit_number": unit_number,
        "ue_weight": ue_weight,
        "vote": vote,
        "proxy_holder_id": None,
        "cast_by_user_id": "user-owner-001",
        "created_at": created_at,
    }
    entry_hash = compute_entry_hash(payload, prev_hash)
    return {**payload, "prev_hash": prev_hash, "entry_hash": entry_hash}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Proxy validation — strata_manager rejected (ACT s.76(2))
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_proxy_validation_strata_manager_rejected(strata_manager, owner_a, agm_doc):
    """ACT s.76(2): strata_manager role must be rejected as proxy holder."""
    with (
        patch("services.proxy_validator.db") as mock_db,
    ):
        mock_db.users.find_one = AsyncMock(return_value=strata_manager)
        mock_db.agm.find_one = AsyncMock(return_value=agm_doc)

        from services.proxy_validator import validate_proxy_eligibility

        result = await validate_proxy_eligibility(
            proxy_holder_id=strata_manager["id"],
            agm_id=AGM_ID,
            grantor_user_id=owner_a["id"],
            building_id=BUILDING_A,
            jurisdiction="ACT",
        )

    assert result["valid"] is False
    assert "s.76(2)" in result["legislation"]
    assert "strata_manager" in result["reason"]


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Proxy validation — valid owner holder accepted
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_proxy_validation_owner_accepted(owner_b, owner_a, agm_doc):
    """A regular owner is eligible to hold a proxy in ACT."""
    membership = {"user_id": owner_b["id"], "building_id": BUILDING_A, "is_active": True}
    with patch("services.proxy_validator.db") as mock_db:
        # find_one returns holder first, then grantor
        mock_db.users.find_one = AsyncMock(side_effect=[owner_b, owner_a])
        mock_db.agm.find_one = AsyncMock(return_value=agm_doc)
        mock_db.agm_attendance.count_documents = AsyncMock(return_value=0)
        # Proxy holder must have an active lot in this building (new security check)
        mock_db.user_units.find_one = AsyncMock(return_value=membership)

        from services.proxy_validator import validate_proxy_eligibility

        result = await validate_proxy_eligibility(
            proxy_holder_id=owner_b["id"],
            agm_id=AGM_ID,
            grantor_user_id=owner_a["id"],
            building_id=BUILDING_A,
            jurisdiction="ACT",
        )

    assert result["valid"] is True
    assert "s.77" in result["legislation"]


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Proxy validation — grantor cannot appoint themselves
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_proxy_validation_self_proxy_rejected(owner_a, agm_doc):
    """A lot owner cannot be their own proxy holder."""
    membership = {"user_id": owner_a["id"], "building_id": BUILDING_A, "is_active": True}
    with patch("services.proxy_validator.db") as mock_db:
        mock_db.users.find_one = AsyncMock(return_value=owner_a)
        mock_db.agm.find_one = AsyncMock(return_value=agm_doc)
        # Building membership check passes; self-proxy check catches it after
        mock_db.user_units.find_one = AsyncMock(return_value=membership)

        from services.proxy_validator import validate_proxy_eligibility

        result = await validate_proxy_eligibility(
            proxy_holder_id=owner_a["id"],
            agm_id=AGM_ID,
            grantor_user_id=owner_a["id"],  # same user
            building_id=BUILDING_A,
            jurisdiction="ACT",
        )

    assert result["valid"] is False
    assert "themselves" in result["reason"]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Proxy validation — completed AGM blocks registration
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_proxy_validation_completed_agm_rejected(owner_b, owner_a, completed_agm_doc):
    """Cannot register a proxy once the AGM is completed."""
    with patch("services.proxy_validator.db") as mock_db:
        mock_db.users.find_one = AsyncMock(return_value=owner_b)
        mock_db.agm.find_one = AsyncMock(return_value=completed_agm_doc)

        from services.proxy_validator import validate_proxy_eligibility

        result = await validate_proxy_eligibility(
            proxy_holder_id=owner_b["id"],
            agm_id=AGM_ID,
            grantor_user_id=owner_a["id"],
            building_id=BUILDING_A,
            jurisdiction="ACT",
        )

    assert result["valid"] is False
    assert "completed" in result["reason"].lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Proxy summary — groups by holder correctly
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_proxy_summary_grouping(owner_b):
    """get_proxy_summary should group attendance records by proxy holder."""
    attendance_rows = [
        {
            "proxy_id": owner_b["id"],
            "user_id": "user-owner-003",
            "proxy_submitted_at": "2026-05-01T10:00:00+00:00",
            "grantor_name": "Owner Gamma",
            "grantor_unit": "UA005",
            "holder_name": owner_b["full_name"],
        },
        {
            "proxy_id": owner_b["id"],
            "user_id": "user-owner-004",
            "proxy_submitted_at": "2026-05-01T11:00:00+00:00",
            "grantor_name": "Owner Delta",
            "grantor_unit": "UA006",
            "holder_name": owner_b["full_name"],
        },
    ]

    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=attendance_rows)

    with patch("services.proxy_validator.db") as mock_db:
        mock_db.agm_attendance.aggregate = MagicMock(return_value=cursor)

        from services.proxy_validator import get_proxy_summary

        summary = await get_proxy_summary(AGM_ID, BUILDING_A)

    assert summary["total_proxies"] == 2
    assert len(summary["holders"]) == 1
    holder = summary["holders"][0]
    assert holder["holder_user_id"] == owner_b["id"]
    assert holder["lot_count"] == 2
    assert len(holder["granted_by"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Quorum calculation — ACT 1/3 UE threshold
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_quorum_threshold_act():
    """ACT quorum = ceil(total_ue / 3). Assert quorum_met is computed correctly."""
    # Simulate total building UE = 300; attending UE = 90 (exact 1/3 → quorate)
    attendance_records = [
        {"user_id": "user-owner-001", "status": "attending"},
        {"user_id": "user-owner-002", "status": "attending"},
    ]

    # attending pipeline returns total_ue=100, proxy returns total_ue=0
    attending_agg = [{"total_ue": 100, "lot_count": 2}]
    proxy_agg: list = []

    total_ue_agg = [{"_id": None, "total": 300}]

    def _cursor(result):
        c = MagicMock()
        c.to_list = AsyncMock(return_value=result)
        return c

    with (
        patch("routers.voting.db") as mock_db,
        patch("routers.voting.get_approved_user", return_value={"id": "x", "role": "owner", "effective_role": "owner"}),
        patch("routers.voting.get_current_building", return_value=BUILDING_A),
    ):
        mock_db.agm.find_one = AsyncMock(return_value={
            "id": AGM_ID, "building_id": BUILDING_A, "status": "in_progress",
            "title": "Test AGM",
        })
        mock_db.agm_attendance.find = MagicMock(return_value=_AsyncCursor(attendance_records))
        # users.aggregate called twice: attending then proxy
        mock_db.users.aggregate = MagicMock(side_effect=[
            _cursor(attending_agg),
            _cursor(proxy_agg),
        ])
        mock_db.units.aggregate = MagicMock(return_value=_cursor(total_ue_agg))
        mock_db.units.count_documents = AsyncMock(return_value=30)

        from routers.voting import get_quorum_status

        result = await get_quorum_status(
            agm_id=AGM_ID,
            current_user={"id": "x", "role": "owner", "effective_role": "owner"},
            building_id=BUILDING_A,
        )

    import math
    assert result["total_building_ue"] == 300
    assert result["quorum_threshold_ue"] == math.ceil(300 / 3)  # 100
    assert result["attending_ue"] == 100
    assert result["quorum_met"] is True  # 100 >= 100


@pytest.mark.asyncio
async def test_quorum_not_met_when_ue_below_threshold():
    """Quorum not met when attending UE < ceil(total / 3)."""
    attendance_records = [{"user_id": "user-owner-001", "status": "attending"}]
    attending_agg = [{"total_ue": 50, "lot_count": 1}]  # below 100
    proxy_agg: list = []
    total_ue_agg = [{"_id": None, "total": 300}]

    def _cursor(result):
        c = MagicMock()
        c.to_list = AsyncMock(return_value=result)
        return c

    with patch("routers.voting.db") as mock_db:
        mock_db.agm.find_one = AsyncMock(return_value={
            "id": AGM_ID, "building_id": BUILDING_A, "status": "in_progress",
            "title": "Test AGM",
        })
        mock_db.agm_attendance.find = MagicMock(return_value=_AsyncCursor(attendance_records))
        mock_db.users.aggregate = MagicMock(side_effect=[
            _cursor(attending_agg),
            _cursor(proxy_agg),
        ])
        mock_db.units.aggregate = MagicMock(return_value=_cursor(total_ue_agg))
        mock_db.units.count_documents = AsyncMock(return_value=30)

        from routers.voting import get_quorum_status

        result = await get_quorum_status(
            agm_id=AGM_ID,
            current_user={"id": "x", "role": "owner", "effective_role": "owner"},
            building_id=BUILDING_A,
        )

    assert result["quorum_met"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 7. UE-weighted results — ordinary and special resolution
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_weighted_results_ordinary_and_special():
    """
    for_ue=75, against_ue=25 → ordinary passed (75>25), special passed (75%>=75%)
    for_ue=60, against_ue=40 → ordinary passed (60>40), special NOT passed (60%<75%)
    """
    motions = [
        {
            "id": MOTION_ID_1, "building_id": BUILDING_A, "agm_id": AGM_ID,
            "title": "Levy Approval", "motion_type": "special",
            "votes_for": 0, "votes_against": 0, "votes_abstain": 0,
            "status": "pending", "voters": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "id": MOTION_ID_2, "building_id": BUILDING_A, "agm_id": AGM_ID,
            "title": "Budget Adoption", "motion_type": "ordinary",
            "votes_for": 0, "votes_against": 0, "votes_abstain": 0,
            "status": "pending", "voters": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    ]

    vote_aggregation = [
        {
            "_id": MOTION_ID_1,
            "votes_for_ue": 75, "votes_against_ue": 25, "votes_abstain_ue": 0,
            "votes_for_count": 3, "votes_against_count": 1, "votes_abstain_count": 0,
        },
        {
            "_id": MOTION_ID_2,
            "votes_for_ue": 60, "votes_against_ue": 40, "votes_abstain_ue": 0,
            "votes_for_count": 2, "votes_against_count": 2, "votes_abstain_count": 0,
        },
    ]

    def _cursor(result):
        c = MagicMock()
        c.to_list = AsyncMock(return_value=result)
        return c

    with patch("routers.voting.db") as mock_db:
        mock_db.agm_motions.find = MagicMock(return_value=_cursor(motions))
        mock_db.agm_votes.aggregate = MagicMock(return_value=_cursor(vote_aggregation))

        from routers.voting import _build_weighted_results

        results = await _build_weighted_results(AGM_ID, BUILDING_A)

    r1 = next(r for r in results if r["motion_id"] == MOTION_ID_1)
    r2 = next(r for r in results if r["motion_id"] == MOTION_ID_2)

    assert r1["ordinary_resolution_passed"] is True
    assert r1["special_resolution_passed"] is True  # 75/(75+25) = 75% >= 75%

    assert r2["ordinary_resolution_passed"] is True
    assert r2["special_resolution_passed"] is False  # 60/(60+40) = 60% < 75%


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Ballot entry hash chain — genesis and chaining
# ═══════════════════════════════════════════════════════════════════════════════


def test_ballot_entry_hash_chain():
    """Genesis entry chains from agm_id; subsequent entries chain from prev entry_hash."""
    genesis_prev = AGM_ID
    entry1_dict = _make_entry_dict(MOTION_ID_1, "UA001", "for", genesis_prev, ue_weight=10)
    entry2_dict = _make_entry_dict(MOTION_ID_1, "UA002", "against", entry1_dict["entry_hash"], ue_weight=5)

    # Verify first entry
    e1 = BallotEntry(**entry1_dict)
    assert e1.prev_hash == genesis_prev

    # Verify second entry chains from first
    e2 = BallotEntry(**entry2_dict)
    assert e2.prev_hash == e1.entry_hash

    # Verify chain is intact
    result = verify_chain([entry1_dict, entry2_dict])
    assert result["valid"] is True
    assert result["checked"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 9. BallotEntry tampering detection
# ═══════════════════════════════════════════════════════════════════════════════


def test_ballot_entry_tampering_detected():
    """Modifying a field after hash computation must cause a validation error."""
    entry_dict = _make_entry_dict(MOTION_ID_1, "UA001", "for", AGM_ID)
    # Tamper with the vote field
    tampered = {**entry_dict, "vote": "against"}

    with pytest.raises(ValueError, match="hash mismatch"):
        BallotEntry(**tampered)


# ═══════════════════════════════════════════════════════════════════════════════
# 10. BallotSeal creation and hash verification
# ═══════════════════════════════════════════════════════════════════════════════


def test_ballot_seal_creation():
    """BallotSealCreate.to_seal() produces a valid BallotSeal with correct seal_hash."""
    final_hash = "a" * 64  # dummy SHA-256-length hex
    creator = BallotSealCreate(
        agm_id=AGM_ID,
        building_id=BUILDING_A,
        sealed_by_user_id="user-chair-001",
        total_entries=42,
        final_hash=final_hash,
    )
    seal = creator.to_seal()

    assert seal.agm_id == AGM_ID
    assert seal.building_id == BUILDING_A
    assert seal.total_entries == 42
    assert seal.final_hash == final_hash
    assert seal.is_valid is True
    assert len(seal.seal_hash) == 64  # SHA-256 hex digest


# ═══════════════════════════════════════════════════════════════════════════════
# 11. BallotSeal tampering detection
# ═══════════════════════════════════════════════════════════════════════════════


def test_ballot_seal_tampering_detected():
    """Modifying total_entries after seal creation must cause a validation error."""
    creator = BallotSealCreate(
        agm_id=AGM_ID,
        building_id=BUILDING_A,
        sealed_by_user_id="user-chair-001",
        total_entries=10,
        final_hash="b" * 64,
    )
    seal = creator.to_seal()
    seal_dict = seal.model_dump()

    # Tamper with total_entries
    tampered = {**seal_dict, "total_entries": 999}

    with pytest.raises(ValueError, match="hash mismatch"):
        BallotSeal(**tampered)


# ═══════════════════════════════════════════════════════════════════════════════
# 12. verify_chain — detects broken entry in sequence
# ═══════════════════════════════════════════════════════════════════════════════


def test_verify_chain_broken_at_index_1():
    """verify_chain should detect hash break at index 1."""
    entry1 = _make_entry_dict(MOTION_ID_1, "UA001", "for", AGM_ID)
    entry2 = _make_entry_dict(MOTION_ID_1, "UA002", "for", entry1["entry_hash"])

    # Break chain at entry2 by swapping its prev_hash
    broken_entry2 = {**entry2, "prev_hash": "wrong_hash"}
    # entry_hash was computed with the correct prev_hash, so the stored hash no
    # longer matches the recomputed one (different prev_hash in payload).
    # Manually corrupt entry_hash too to make the stored hash "wrong":
    broken_entry2["entry_hash"] = "0" * 64

    result = verify_chain([entry1, broken_entry2])
    assert result["valid"] is False
    assert result["first_broken_at"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 13. POST /agm/{agm_id}/proxy — submit proxy endpoint
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_submit_proxy_endpoint(owner_a, owner_b, agm_doc):
    """POST /agm/{agm_id}/proxy — happy path for a valid proxy submission."""
    validation_result = {
        "valid": True,
        "reason": "Eligible.",
        "legislation": "ACT s.77",
        "jurisdiction": "ACT",
        "proxy_holder_id": owner_b["id"],
        "grantor_user_id": owner_a["id"],
    }

    update_result = MagicMock()
    update_result.matched_count = 1

    with (
        patch("routers.voting.db") as mock_db,
        patch(
            "routers.voting.validate_proxy_eligibility",
            new=AsyncMock(return_value=validation_result),
        ),
        patch("routers.voting.log_activity", new=AsyncMock()),
    ):
        mock_db.agm.find_one = AsyncMock(return_value=agm_doc)
        mock_db.buildings.find_one = AsyncMock(return_value={"id": BUILDING_A, "jurisdiction": "ACT"})
        mock_db.agm_attendance.update_one = AsyncMock(return_value=update_result)

        from routers.voting import submit_proxy
        from routers.voting import ProxySubmitRequest

        request_data = ProxySubmitRequest(
            proxy_holder_id=owner_b["id"],
            proxy_form_text="I, Owner Alpha, appoint Owner Beta as my proxy for the 2026 AGM.",
            special_instructions=None,
        )

        response = await submit_proxy(
            agm_id=AGM_ID,
            data=request_data,
            current_user=owner_a,
            building_id=BUILDING_A,
        )

    assert response["message"] == "Proxy submitted successfully."
    assert response["validation"]["valid"] is True
    assert response["attendance"]["proxy_id"] == owner_b["id"]


# ═══════════════════════════════════════════════════════════════════════════════
# 14. DELETE /agm/{agm_id}/proxy — revoke proxy endpoint
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_revoke_proxy_endpoint(owner_a, agm_doc):
    """DELETE /agm/{agm_id}/proxy — grantor successfully revokes their proxy."""
    update_result = MagicMock()
    update_result.matched_count = 1

    with (
        patch("routers.voting.db") as mock_db,
        patch("routers.voting.log_activity", new=AsyncMock()),
    ):
        mock_db.agm.find_one = AsyncMock(return_value=agm_doc)
        mock_db.agm_votes.find_one = AsyncMock(return_value=None)  # no vote cast yet
        mock_db.agm_attendance.update_one = AsyncMock(return_value=update_result)

        from routers.voting import revoke_proxy

        response = await revoke_proxy(
            agm_id=AGM_ID,
            current_user=owner_a,
            building_id=BUILDING_A,
        )

    assert response["message"] == "Proxy revoked successfully."


# ═══════════════════════════════════════════════════════════════════════════════
# 15. POST /agm/{agm_id}/close-ballot — owner role rejected
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_close_ballot_owner_rejected(owner_a, agm_doc):
    """Owners must not be able to close a ballot (role guard)."""
    with patch("routers.voting.db") as mock_db:
        mock_db.agm.find_one = AsyncMock(return_value=agm_doc)

        from fastapi import HTTPException
        from routers.voting import close_ballot

        with pytest.raises(HTTPException) as exc_info:
            await close_ballot(
                agm_id=AGM_ID,
                current_user=owner_a,
                building_id=BUILDING_A,
            )

    assert exc_info.value.status_code == 403
    assert "strata_admin" in exc_info.value.detail or "chairman" in exc_info.value.detail or "strata_manager" in exc_info.value.detail


# ═══════════════════════════════════════════════════════════════════════════════
# 16. POST /agm/{agm_id}/close-ballot — chairman seals ballot
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_close_ballot_chairman_success(chairman, agm_doc):
    """Chairman successfully closes ballot and receives a BallotSeal."""
    votes = [
        {
            "id": str(uuid.uuid4()),
            "building_id": BUILDING_A,
            "agm_id": AGM_ID,
            "motion_id": MOTION_ID_1,
            "unit_number": "UA001",
            "vote": "for",
            "user_id": "user-owner-001",
            "proxy_for": None,
        }
    ]
    unit_ue_rows = [{"unit_number": "UA001", "entitlement": 10}]

    def _cursor(result):
        c = MagicMock()
        c.to_list = AsyncMock(return_value=result)
        # Support .sort(...).to_list(...) chaining used after security fix
        c.sort = MagicMock(return_value=c)
        return c

    insert_result = MagicMock()
    insert_result.inserted_id = "some-oid"

    with (
        patch("routers.voting.db") as mock_db,
        patch("routers.voting.log_activity", new=AsyncMock()),
    ):
        mock_db.agm.find_one = AsyncMock(return_value=agm_doc)
        # agm_votes.find returns votes
        mock_db.agm_votes.find = MagicMock(return_value=_cursor(votes))
        # No existing ballot entries
        mock_db.ballot_entries.find_one = AsyncMock(return_value=None)
        mock_db.ballot_entries.find = MagicMock(return_value=_cursor([]))
        mock_db.ballot_entries.count_documents = AsyncMock(return_value=1)
        mock_db.ballot_entries.insert_many = AsyncMock(return_value=MagicMock(inserted_ids=["oid1"]))
        mock_db.units.aggregate = MagicMock(return_value=_cursor(unit_ue_rows))
        mock_db.ballot_seals.insert_one = AsyncMock(return_value=insert_result)
        mock_db.agm.update_one = AsyncMock()

        from routers.voting import close_ballot

        response = await close_ballot(
            agm_id=AGM_ID,
            current_user=chairman,
            building_id=BUILDING_A,
        )

    assert "seal" in response
    assert response["seal"]["agm_id"] == AGM_ID
    assert response["seal"]["is_valid"] is True
    assert len(response["seal"]["seal_hash"]) == 64


# ═══════════════════════════════════════════════════════════════════════════════
# 17. GET /agm/{agm_id}/evidence-pack — owner role rejected
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_evidence_pack_owner_rejected(owner_a, agm_doc):
    """Plain owners must not receive the evidence pack."""
    with patch("routers.voting.db") as mock_db:
        mock_db.agm.find_one = AsyncMock(return_value=agm_doc)

        from fastapi import HTTPException
        from routers.voting import get_evidence_pack

        with pytest.raises(HTTPException) as exc_info:
            await get_evidence_pack(
                agm_id=AGM_ID,
                current_user=owner_a,
                building_id=BUILDING_A,
            )

    assert exc_info.value.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 18. GET /agm/{agm_id}/evidence-pack — chairman receives full pack
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_evidence_pack_chairman_receives_full_pack(chairman, agm_doc):
    """Chairman gets a complete evidence pack with all required keys."""
    motions = [
        {
            "id": MOTION_ID_1,
            "building_id": BUILDING_A,
            "agm_id": AGM_ID,
            "title": "Levy Approval",
            "motion_type": "ordinary",
            "votes_for": 1, "votes_against": 0, "votes_abstain": 0,
            "status": "pending", "voters": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    seal = BallotSealCreate(
        agm_id=AGM_ID,
        building_id=BUILDING_A,
        sealed_by_user_id=chairman["id"],
        total_entries=1,
        final_hash="c" * 64,
    ).to_seal().model_dump()

    entry1 = _make_entry_dict(MOTION_ID_1, "UA001", "for", AGM_ID)

    # _build_weighted_results uses aggregate with UE fields; _vote_counts uses aggregate
    # with a simple {_id, count} group. Return different shapes per successive call.
    vote_ue_agg = [
        {
            "_id": MOTION_ID_1,
            "votes_for_ue": 10, "votes_against_ue": 0, "votes_abstain_ue": 0,
            "votes_for_count": 1, "votes_against_count": 0, "votes_abstain_count": 0,
        }
    ]
    vote_count_agg = [{"_id": MOTION_ID_1, "count": 1}]

    def _cursor(result):
        c = MagicMock()
        c.to_list = AsyncMock(return_value=result)
        return c

    with (
        patch("routers.voting.db") as mock_db,
        patch("routers.voting.get_proxy_summary", new=AsyncMock(return_value={
            "agm_id": AGM_ID, "total_proxies": 0, "holders": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })),
    ):
        mock_db.agm.find_one = AsyncMock(return_value=agm_doc)
        mock_db.agm_motions.find = MagicMock(return_value=_cursor(motions))
        mock_db.ballot_seals.find_one = AsyncMock(return_value=seal)

        # ballot_entries.find is iterated with async-for in _get_all_ballot_entries
        mock_db.ballot_entries.find = MagicMock(return_value=_AsyncCursor([entry1]))

        # First aggregate call: UE-weighted results; second: per-motion lot counts
        mock_db.agm_votes.aggregate = MagicMock(
            side_effect=[_cursor(vote_ue_agg), _cursor(vote_count_agg)]
        )

        # _compute_quorum — no attendees for simplicity; avoids users.aggregate calls
        mock_db.agm_attendance.find = MagicMock(return_value=_AsyncCursor([]))
        mock_db.units.aggregate = MagicMock(return_value=_cursor([{"_id": None, "total": 0}]))
        mock_db.units.count_documents = AsyncMock(return_value=0)

        from routers.voting import get_evidence_pack

        pack = await get_evidence_pack(
            agm_id=AGM_ID,
            current_user=chairman,
            building_id=BUILDING_A,
        )

    required_keys = {
        "agm", "motions", "weighted_results", "ballot_seal",
        "vote_count_by_motion", "proxy_summary", "quorum_at_close",
        "chain_integrity", "generated_at",
    }
    assert required_keys.issubset(set(pack.keys()))
    assert pack["chain_integrity"]["valid"] is True
    assert pack["ballot_seal"]["agm_id"] == AGM_ID


# ═══════════════════════════════════════════════════════════════════════════════
# 19. Multi-tenant isolation — building B cannot see building A data
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_multitenant_isolation_quorum():
    """AGM from building A must not be visible when queried from building B."""
    # The DB returns None because building_id=16244 doesn't match the AGM's building_id=13195
    with patch("routers.voting.db") as mock_db:
        mock_db.agm.find_one = AsyncMock(return_value=None)

        from fastapi import HTTPException
        from routers.voting import get_quorum_status

        with pytest.raises(HTTPException) as exc_info:
            await get_quorum_status(
                agm_id=AGM_ID,
                current_user={"id": "x", "role": "owner", "effective_role": "owner"},
                building_id=BUILDING_B,  # different building
            )

    assert exc_info.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 20. NSW proxy cap — 5% numerical limit enforced
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_nsw_proxy_cap_exceeded(owner_b, owner_a, agm_doc):
    """NSW: a holder may not hold proxies for > 5% of lots (e.g. 1 of 10 lots)."""
    with patch("services.proxy_validator.db") as mock_db:
        mock_db.users.find_one = AsyncMock(return_value=owner_b)
        mock_db.agm.find_one = AsyncMock(return_value=agm_doc)
        # 10 total lots, cap = max(1, floor(10*0.05)) = 1
        mock_db.units.count_documents = AsyncMock(return_value=10)
        # Holder already holds 1 proxy — cap would be exceeded by adding another
        mock_db.agm_attendance.count_documents = AsyncMock(return_value=1)

        from services.proxy_validator import validate_proxy_eligibility

        result = await validate_proxy_eligibility(
            proxy_holder_id=owner_b["id"],
            agm_id=AGM_ID,
            grantor_user_id=owner_a["id"],
            building_id=BUILDING_A,
            jurisdiction="NSW",
        )

    assert result["valid"] is False
    assert "NSW" in result["legislation"]
    assert "cap" in result["reason"].lower()


# ═══════════════════════════════════════════════════════════════════════════════
# 21–24. NSW proxy cap enforcement at VOTE CAST time (GAP-GOV-001 belt-and-suspenders)
# ═══════════════════════════════════════════════════════════════════════════════
# The server.py cast_vote endpoint re-checks the NSW cap when proxy_for is set.
# These tests exercise that path directly via the server module, isolating the
# cap-check block with a mock DB.  The server imports domain.jurisdictional_rules
# lazily inside the function; we patch it at the module level here.
#
# AGMVoteCreate is defined inline in server.py (not in a separate models module).
# Re-declare a structurally identical Pydantic model here so the model is available
# at class definition time without a top-level server import. Each test imports
# cast_vote from server lazily (inside the patched context) to control when
# server-level side-effects (DB connection, router registration) are triggered.
# ─────────────────────────────────────────────────────────────────────────────

from typing import Optional as _Opt
from pydantic import BaseModel as _BaseModel


class _AGMVoteCreate(_BaseModel):
    """Minimal replica of server.AGMVoteCreate for test use only."""
    motion_id: str
    vote: str
    proxy_for: _Opt[str] = None
    is_pre_vote: bool = False


def _make_cast_vote_mocks(
    *,
    building_jurisdiction: str = "NSW",
    total_lots: int = 10,
    existing_proxies: int = 0,
    motion_doc: dict | None = None,
    agm_doc: dict | None = None,
    is_owner: dict | None = None,
    proxy_authorized: dict | None = None,
    proxy_grantor_id: str = "user-owner-001",  # matches owner_a fixture
    upsert_result_id: str | None = "new-vote-id",
):
    """Return a configured mock_db for cast_vote proxy-cap tests.

    When `is_owner` is None (proxy vote), the mock correctly populates
    `user_units.find().to_list()` with a single owner record for the target
    unit so that `owner_ids` is non-empty and the `agm_attendance` proxy-auth
    check is reached.  Set `proxy_authorized` to the attendance doc that
    grants proxy authority to the voting user.
    """
    mock_db = MagicMock()

    motion = motion_doc or {
        "id": MOTION_ID_1,
        "agm_id": AGM_ID,
        "building_id": BUILDING_A,
        "motion_type": "ordinary",
    }
    agm = agm_doc or {
        "id": AGM_ID,
        "building_id": BUILDING_A,
        "status": "in_progress",
        "title": "AGM 2026",
    }

    mock_db.agm_motions.find_one = AsyncMock(return_value=motion)
    mock_db.agm.find_one = AsyncMock(return_value=agm)
    mock_db.buildings.find_one = AsyncMock(
        return_value={"id": BUILDING_A, "jurisdiction": building_jurisdiction}
    )
    mock_db.units.count_documents = AsyncMock(return_value=total_lots)
    # Direct ownership check (find_one)
    mock_db.user_units.find_one = AsyncMock(return_value=is_owner)

    # Proxy path: find() returns one owner record for the target unit so that
    # owner_ids is non-empty and the agm_attendance lookup is triggered.
    owner_record = {
        "user_id": proxy_grantor_id,
        "unit_number": "TH001",
        "building_id": BUILDING_A,
        "is_active": True,
    }
    owners_for_unit = [] if is_owner is not None else [owner_record]
    cursor_mock = MagicMock()
    cursor_mock.to_list = AsyncMock(return_value=owners_for_unit)
    mock_db.user_units.find = MagicMock(return_value=cursor_mock)

    # Proxy-authorization check (agm_attendance.find_one)
    mock_db.agm_attendance.find_one = AsyncMock(return_value=proxy_authorized)
    # NSW cap check: count of proxies already held (excluding the grantor)
    mock_db.agm_attendance.count_documents = AsyncMock(return_value=existing_proxies)

    # Vote upsert result
    upsert_mock = MagicMock()
    upsert_mock.upserted_id = upsert_result_id
    mock_db.agm_votes.update_one = AsyncMock(return_value=upsert_mock)
    mock_db.agm_motions.update_one = AsyncMock(return_value=MagicMock())

    return mock_db


@pytest.mark.asyncio
async def test_cast_vote_nsw_proxy_cap_respected_when_under_limit(owner_a, owner_b, agm_doc):
    """NSW scheme: proxy holder with 0 existing proxies (cap=1 for 10 lots) can vote.
    
    GAP-GOV-001: cast_vote re-checks cap at ballot time.
    10 lots → cap = max(1, floor(10 * 5%)) = 1.  Holder has 0 → within cap.
    """
    mock_db = _make_cast_vote_mocks(
        building_jurisdiction="NSW",
        total_lots=10,
        existing_proxies=0,       # no existing proxies held
        is_owner=None,            # not direct owner
        proxy_authorized={        # proxy IS authorized in attendance
            "agm_id": AGM_ID,
            "user_id": owner_a["id"],
            "proxy_id": owner_b["id"],
        },
    )

    vote_data = _AGMVoteCreate(motion_id=MOTION_ID_1, vote="for", proxy_for="TH001")
    voter = {**owner_b, "unit_number": None}  # holder has no direct unit

    with patch("server.db", mock_db), \
         patch("server._effective_role", return_value="owner"):
        from server import cast_vote
        result = await cast_vote(
            motion_id=MOTION_ID_1,
            data=vote_data,
            current_user=voter,
            building_id=BUILDING_A,
        )

    assert result == {"message": "Vote recorded"}


@pytest.mark.asyncio
async def test_cast_vote_nsw_proxy_cap_exceeded_at_vote_time(owner_a, owner_b, agm_doc):
    """NSW: cast_vote rejects when holder already holds cap-many proxies.

    GAP-GOV-001 belt-and-suspenders: cap enforced at vote time even if
    proxy_validator was somehow bypassed at submission time.
    10 lots → cap = 1.  Holder already holds 1 → exceeds cap → HTTP 422.
    """
    mock_db = _make_cast_vote_mocks(
        building_jurisdiction="NSW",
        total_lots=10,
        existing_proxies=1,       # holder already holds cap (1)
        is_owner=None,
        proxy_authorized={
            "agm_id": AGM_ID,
            "user_id": owner_a["id"],
            "proxy_id": owner_b["id"],
        },
    )

    from fastapi import HTTPException
    vote_data = _AGMVoteCreate(motion_id=MOTION_ID_1, vote="for", proxy_for="TH001")
    voter = {**owner_b, "unit_number": None}

    with patch("server.db", mock_db), \
         patch("server._effective_role", return_value="owner"):
        from server import cast_vote
        with pytest.raises(HTTPException) as exc_info:
            await cast_vote(
                motion_id=MOTION_ID_1,
                data=vote_data,
                current_user=voter,
                building_id=BUILDING_A,
            )

    assert exc_info.value.status_code == 422
    assert "NSW" in exc_info.value.detail
    assert "cap" in exc_info.value.detail.lower()
    assert "s.60" in exc_info.value.detail


@pytest.mark.asyncio
async def test_cast_vote_act_scheme_no_proxy_cap_check(owner_a, owner_b, agm_doc):
    """ACT scheme: proxy cap check is not applied (ACT has no numerical cap).

    Even with a large number of proxies, ACT voting succeeds — only role-based
    restrictions (strata_manager prohibition) apply, and those are checked at
    proxy-submission time, not here.
    """
    mock_db = _make_cast_vote_mocks(
        building_jurisdiction="ACT",
        total_lots=10,
        existing_proxies=99,      # large number — irrelevant under ACT
        is_owner=None,
        proxy_authorized={
            "agm_id": AGM_ID,
            "user_id": owner_a["id"],
            "proxy_id": owner_b["id"],
        },
    )

    vote_data = _AGMVoteCreate(motion_id=MOTION_ID_1, vote="against", proxy_for="TH001")
    voter = {**owner_b, "unit_number": None}

    with patch("server.db", mock_db), \
         patch("server._effective_role", return_value="owner"):
        from server import cast_vote
        result = await cast_vote(
            motion_id=MOTION_ID_1,
            data=vote_data,
            current_user=voter,
            building_id=BUILDING_A,
        )

    assert result == {"message": "Vote recorded"}


@pytest.mark.asyncio
async def test_cast_vote_direct_owner_no_proxy_cap_check(owner_a, agm_doc):
    """Direct owner (no proxy_for) is never subject to proxy cap check."""
    mock_db = _make_cast_vote_mocks(
        building_jurisdiction="NSW",
        total_lots=10,
        existing_proxies=99,      # irrelevant — not a proxy vote
        is_owner={"user_id": owner_a["id"], "unit_number": "TH001", "is_active": True},
        proxy_authorized=None,
    )

    # proxy_for is None — this is a direct owner vote, not a proxy vote
    vote_data = _AGMVoteCreate(motion_id=MOTION_ID_1, vote="for")
    voter = {**owner_a, "unit_number": "TH001"}

    with patch("server.db", mock_db), \
         patch("server._effective_role", return_value="owner"):
        from server import cast_vote
        result = await cast_vote(
            motion_id=MOTION_ID_1,
            data=vote_data,
            current_user=voter,
            building_id=BUILDING_A,
        )

    assert result == {"message": "Vote recorded"}


# ═══════════════════════════════════════════════════════════════════════════════
# 25–27. NSW proxy cap — large-scheme branch (≥ 20 lots, % cap path)
# ═══════════════════════════════════════════════════════════════════════════════
# F-5 FIX: The four tests above only exercised the small-scheme path (< 20 lots).
# These tests cover the large-scheme branch (proxy_cap_scheme_pct, float division).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cast_vote_nsw_large_scheme_cap_respected(owner_a, owner_b, agm_doc):
    """NSW large-scheme (≥ 20 lots): holder with 0 existing proxies can vote.

    40 lots → cap = max(1, floor(40 * 5 // 100)) = max(1, 2) = 2.
    Holder has 0 proxies → within cap → vote succeeds.
    """
    mock_db = _make_cast_vote_mocks(
        building_jurisdiction="NSW",
        total_lots=40,            # large-scheme: ≥ 20
        existing_proxies=0,
        is_owner=None,
        proxy_authorized={
            "agm_id": AGM_ID,
            "user_id": owner_a["id"],
            "proxy_id": owner_b["id"],
        },
    )

    vote_data = _AGMVoteCreate(motion_id=MOTION_ID_1, vote="for", proxy_for="TH001")
    voter = {**owner_b, "unit_number": None}

    with patch("server.db", mock_db), \
         patch("server._effective_role", return_value="owner"):
        from server import cast_vote
        result = await cast_vote(
            motion_id=MOTION_ID_1,
            data=vote_data,
            current_user=voter,
            building_id=BUILDING_A,
        )

    assert result == {"message": "Vote recorded"}


@pytest.mark.asyncio
async def test_cast_vote_nsw_large_scheme_cap_exceeded(owner_a, owner_b, agm_doc):
    """NSW large-scheme (≥ 20 lots): holder at cap is blocked.

    40 lots → cap = 2.  Holder already holds 2 proxies → 422.
    """
    mock_db = _make_cast_vote_mocks(
        building_jurisdiction="NSW",
        total_lots=40,
        existing_proxies=2,       # at cap → next proxy violates s.60
        is_owner=None,
        proxy_authorized={
            "agm_id": AGM_ID,
            "user_id": owner_a["id"],
            "proxy_id": owner_b["id"],
        },
    )

    from fastapi import HTTPException
    vote_data = _AGMVoteCreate(motion_id=MOTION_ID_1, vote="for", proxy_for="TH001")
    voter = {**owner_b, "unit_number": None}

    with patch("server.db", mock_db), \
         patch("server._effective_role", return_value="owner"):
        from server import cast_vote
        with pytest.raises(HTTPException) as exc_info:
            await cast_vote(
                motion_id=MOTION_ID_1,
                data=vote_data,
                current_user=voter,
                building_id=BUILDING_A,
            )

    assert exc_info.value.status_code == 422
    assert "s.60" in exc_info.value.detail
    # Large-scheme error message shows "5% of 40 lots = 2 proxy slot(s)"
    assert "40" in exc_info.value.detail


@pytest.mark.asyncio
async def test_cast_vote_nsw_cap_rule_not_configured_skips_check(owner_a, owner_b, agm_doc):
    """F-1 guard: if rule engine returns None for the cap, the check is skipped
    (no TypeError crash), and the vote proceeds normally.

    This simulates a jurisdiction whose rules JSON lacks proxy_cap_small_scheme_lots.
    """
    mock_db = _make_cast_vote_mocks(
        building_jurisdiction="NSW",
        total_lots=10,            # small-scheme path
        existing_proxies=99,      # would exceed any sane cap — but cap is None
        is_owner=None,
        proxy_authorized={
            "agm_id": AGM_ID,
            "user_id": owner_a["id"],
            "proxy_id": owner_b["id"],
        },
    )

    vote_data = _AGMVoteCreate(motion_id=MOTION_ID_1, vote="for", proxy_for="TH001")
    voter = {**owner_b, "unit_number": None}

    # cast_vote does `from domain.jurisdictional_rules import rule_engine as _rule_engine`
    # lazily.  Since the module is already cached (proxy_validator imported it at module
    # level), patching its `rule_engine` attribute is picked up by the lazy import.
    with patch("server.db", mock_db), \
         patch("server._effective_role", return_value="owner"), \
         patch("domain.jurisdictional_rules.rule_engine") as mock_engine:
        mock_engine.proxy_cap_scheme_size_threshold.return_value = 20
        mock_engine.proxy_cap_small_scheme_lots.return_value = None  # F-1: None → skip cap
        mock_engine.proxy_cap_scheme_pct.return_value = None

        from server import cast_vote
        result = await cast_vote(
            motion_id=MOTION_ID_1,
            data=vote_data,
            current_user=voter,
            building_id=BUILDING_A,
        )

    assert result == {"message": "Vote recorded"}


# ── F-3 ordering fix: AGM status checked before proxy cap ────────────────────

@pytest.mark.asyncio
async def test_cast_vote_closed_agm_returns_400_not_422(owner_a, owner_b):
    """F-3 ordering fix: a closed AGM must return 400 'AGM not in progress',
    NOT 422 'Proxy cap exceeded', even if the holder is at their cap.

    Previously the cap check ran before the status check, giving the wrong error.
    """
    mock_db = _make_cast_vote_mocks(
        building_jurisdiction="NSW",
        total_lots=10,
        existing_proxies=1,       # at cap (would trigger 422 if checked first)
        is_owner=None,
        proxy_authorized={
            "agm_id": AGM_ID,
            "user_id": owner_a["id"],
            "proxy_id": owner_b["id"],
        },
        agm_doc={
            "id": AGM_ID,
            "building_id": BUILDING_A,
            "status": "completed",   # ← AGM is closed
            "title": "AGM 2026",
        },
    )

    from fastapi import HTTPException
    vote_data = _AGMVoteCreate(motion_id=MOTION_ID_1, vote="for", proxy_for="TH001")
    voter = {**owner_b, "unit_number": None}

    with patch("server.db", mock_db), \
         patch("server._effective_role", return_value="owner"):
        from server import cast_vote
        with pytest.raises(HTTPException) as exc_info:
            await cast_vote(
                motion_id=MOTION_ID_1,
                data=vote_data,
                current_user=voter,
                building_id=BUILDING_A,
            )

    # Must be 400, not 422 — status check fires before cap check (F-3 fix)
    assert exc_info.value.status_code == 400
    assert "in progress" in exc_info.value.detail.lower()
