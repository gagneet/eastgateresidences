# @featuretrace:by-law-breach-register — Guards the dispute axis's missing-vs-zero rules.
# Layer: test
# Data flow: by_law_breach_reports -> _build_health_data -> _dispute (building-scoped).
# Related: backend/routers/community_dashboard.py, backend/services/health_score_service.py
"""
The dispute axis of the building health score, sourced from the by-law breach register.

Building Pulse showed "Disputes: NA" permanently, and the reason was recorded as "there is
no disputes register". There is one — `routers/by_law_breach.py` (GAP-OPS-005, the
ACAT/NCAT evidence trail) has been routed and reachable the whole time. Nothing read it,
so `open_disputes` was hardcoded `None`.

Two decisions are pinned here because both are easy to get wrong in the direction that
invents a good score:

1. An EMPTY register is not a clean one. A building that has never filed a breach report
   has no dispute evidence at all, and scoring it 100/100 would hand it 10% of its health
   score for nothing. Only a register with history can report a meaningful zero.

2. `tribunal_referred` counts as unresolved. `BreachStatus.OPEN` excludes it — correctly,
   for workflow purposes, because the register has handed off to a tribunal — but a matter
   before ACAT/NCAT is the most serious live dispute a scheme can have. Counting with
   `OPEN` would report a building with five active tribunal cases as having none.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.by_law_breach import BreachStatus
from services.health_score_service import _dispute


def _make_db(workflow=4, disputes_unresolved=0, disputes_total=0):
    """Minimal Motor-shaped mock for `_build_health_data`.

    Self-contained rather than imported from test_community_hub: these tests are about
    the dispute axis specifically, and a local builder keeps them readable and stops a
    change to that file's defaults quietly changing what is asserted here.
    """
    db = MagicMock()
    for name in ("units", "work_orders", "proposals", "volunteer_events",
                 "pet_requests", "amenity_bookings", "maintenance_requests"):
        getattr(db, name).count_documents = AsyncMock(return_value=1)
    db.workflow_requests.count_documents = AsyncMock(return_value=workflow)
    db.compliance_items.count_documents = AsyncMock(return_value=0)
    # Two counts off one collection: the filtered query carries "status", the total does not.
    db.by_law_breach_reports.count_documents = AsyncMock(side_effect=lambda q: (
        disputes_unresolved if "status" in q else disputes_total
    ))

    async def _empty():
        for _ in ():
            yield _

    db.sinking_fund_accounts.aggregate = MagicMock(return_value=_empty())
    db.building_summaries.find_one = AsyncMock(return_value=None)
    db.building_summaries.update_one = AsyncMock(return_value=None)
    return db


class TestUnresolvedVocabulary:
    def test_tribunal_referral_is_an_unresolved_dispute(self):
        assert BreachStatus.TRIBUNAL_REFERRED in BreachStatus.UNRESOLVED
        # ...and is deliberately absent from OPEN, which models workflow state.
        assert BreachStatus.TRIBUNAL_REFERRED not in BreachStatus.OPEN

    def test_only_resolved_and_withdrawn_are_closed(self):
        assert set(BreachStatus.CLOSED) == {BreachStatus.RESOLVED, BreachStatus.WITHDRAWN}

    def test_every_status_is_either_closed_or_unresolved(self):
        assert set(BreachStatus.UNRESOLVED) | set(BreachStatus.CLOSED) == set(BreachStatus.ALL)
        assert not set(BreachStatus.UNRESOLVED) & set(BreachStatus.CLOSED)

    def test_unresolved_is_derived_by_subtraction_so_it_fails_safe(self):
        """A status added to ALL must default to unresolved, not silently drop out."""
        derived = [s for s in BreachStatus.ALL if s not in BreachStatus.CLOSED]
        assert BreachStatus.UNRESOLVED == derived


class TestDisputeAxis:
    def test_empty_register_is_unavailable_not_a_perfect_score(self):
        assert _dispute({"open_disputes": None, "total_lots": 87}) is None

    def test_history_with_nothing_open_is_a_real_perfect_score(self):
        # The register has been used and currently holds no live dispute — earned, not assumed.
        assert _dispute({"open_disputes": 0, "total_lots": 87}) == 1.0

    def test_open_disputes_reduce_the_score(self):
        score = _dispute({"open_disputes": 4, "total_lots": 87})
        assert 0.0 < score < 1.0

    def test_score_floors_at_zero_rather_than_going_negative(self):
        assert _dispute({"open_disputes": 50, "total_lots": 87}) == 0.0

    def test_no_lots_is_unavailable_not_a_division_error(self):
        assert _dispute({"open_disputes": 1, "total_lots": 0}) is None


@pytest.mark.asyncio
class TestHealthDataWiring:
    """`_build_health_data` must translate the register into the axis correctly."""

    async def _run(self, unresolved, total):
        db = _make_db(disputes_unresolved=unresolved, disputes_total=total)
        with patch("routers.community_dashboard.db", db):
            from routers.community_dashboard import _build_health_data
            return await _build_health_data("13195")

    async def test_empty_register_reports_none(self):
        data = await self._run(0, 0)
        assert data["open_disputes"] is None
        assert data["disputes_total"] == 0

    async def test_register_with_history_reports_a_real_zero(self):
        data = await self._run(0, 12)
        assert data["open_disputes"] == 0        # a number, not None
        assert data["disputes_total"] == 12

    async def test_unresolved_count_is_surfaced(self):
        data = await self._run(3, 12)
        assert data["open_disputes"] == 3

    async def test_workflow_requests_do_not_leak_into_the_dispute_count(self):
        """The original bug: ordinary maintenance requests were passed as open_disputes."""
        db = _make_db(workflow=9, disputes_unresolved=1, disputes_total=4)
        with patch("routers.community_dashboard.db", db):
            from routers.community_dashboard import _build_health_data
            data = await _build_health_data("13195")
        assert data["open_disputes"] == 1        # not 9


def test_breach_register_is_tenant_scoped():
    """
    Legal records naming residents must not sit in a collection the wrapper ignores.

    A collection in neither scoping set fails OPEN — no injection, no error — which is
    exactly what GAP-SEC-013 demonstrated on demo_bank_transactions, where an unfiltered
    query returned two buildings' rows.
    """
    from database import TENANT_SCOPED_COLLECTIONS
    assert "by_law_breach_reports" in TENANT_SCOPED_COLLECTIONS
