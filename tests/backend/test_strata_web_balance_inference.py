"""
tests/backend/test_strata_web_balance_inference.py

GAP-FIN-015 blocker 4: Strata Web balance-delta inference must produce CANDIDATE
demo_bank_transactions (requires_review=True), never mutate unit_levy_ledger, and
only produce a candidate when the delta is positive and explainable.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId

_backend = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend")
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from services.strata_web_balance_inference_service import (
    derive_strata_web_balance_delta_transactions,
)

BUILDING_A = "13195"

_SCHEDULE = [
    {"quarter": "Q1", "due_date": "2026-01-01"},
    {"quarter": "Q2", "due_date": "2026-04-01"},
    {"quarter": "Q3", "due_date": "2026-07-01"},
    {"quarter": "Q4", "due_date": "2026-10-01"},
]


def _snapshot(_id, snapshot_date: str, balances: list[dict], **overrides) -> dict:
    base = {
        "_id": _id,
        "building_id": BUILDING_A,
        "financial_year": "2026",
        "snapshot_date": snapshot_date,
        "per_unit_balances": balances,
        "is_test_data": False,
    }
    base.update(overrides)
    return base


def _snapshot_find(docs: list[dict], *, expect_building: str | None = None):
    """Mock `staging_strata_web_snapshots.find(...)` -> cursor with `.to_list()`.

    The service pairs consecutive snapshots by NORMALISED financial year, which
    cannot be expressed as a Mongo equality filter, so it fetches the building's
    snapshots and filters in Python. The filter it does push down is
    `building_id` — `expect_building` asserts that tenant scoping survives.
    """
    def _find(query, *args, **kwargs):
        if expect_building is not None:
            assert query.get("building_id") == expect_building, (
                "query must be scoped to the requested building_id only"
            )
        bid = query.get("building_id")
        matching = [d for d in docs if d is not None and (bid is None or d.get("building_id") == bid)]
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=matching)
        return cursor

    return MagicMock(side_effect=_find)


@pytest.fixture(autouse=True)
def _stub_unit_display_rules(monkeypatch):
    """East Gate's real rules, so the resolver behaves as it does in production without
    the test reaching the live settings store."""
    async def _rules(_building_id, *a, **k):
        return [
            {"prefix": "UA", "min": 1, "max": 70, "pad": 3},
            {"prefix": "TH", "min": 71, "max": 87, "pad": 3},
        ]

    monkeypatch.setattr(
        "services.strata_web_balance_inference_service.get_unit_display_rules",
        _rules,
        raising=False,
    )

async def _resolve_unit(query, *args, **kwargs):
    """Stand-in for the `units` lookup behind resolve_canonical_unit_number.

    Echoes back whichever unit token was probed, which is what a building whose
    snapshot already carries canonical unit numbers looks like. Returns None for the
    `lot_number` fallback probe so the exact-match path is the one under test.
    """
    exact = query.get("unit_number")
    if isinstance(exact, str) and exact:
        return {"unit_number": exact}
    if isinstance(exact, dict) and exact.get("$in"):
        return {"unit_number": exact["$in"][0]}
    return None


def _make_db(*, current, previous, levy_doc=None, ledger=None):
    db = MagicMock()

    async def _find_snapshot(query, *args, **kwargs):
        if "_id" in query:
            return current if current and current["_id"] == query["_id"] else None
        if "$lt" in query.get("snapshot_date", {}):
            return previous
        return current

    db.staging_strata_web_snapshots.find_one = AsyncMock(side_effect=_find_snapshot)
    db.staging_strata_web_snapshots.find = _snapshot_find([current, previous])
    # The service resolves lot -> addressable unit through utils.unit_number, which
    # probes `units`. Motor methods are coroutines, so this MUST be an AsyncMock —
    # a MagicMock here raises "object MagicMock can't be used in 'await' expression"
    # (backend/CLAUDE.md's first documented test gotcha).
    db.units.find_one = AsyncMock(side_effect=_resolve_unit)
    db.annual_levies.find_one = AsyncMock(return_value=levy_doc)
    db.unit_levy_ledger.find_one = AsyncMock(return_value=ledger)
    return db


def _patch_zero_interest(monkeypatch):
    """Isolate the balance-delta formula from real interest-rate math (day-count
    and rounding are already covered by tests/backend/test_arrears_interest.py) —
    these tests assert 0.0% so the interest term is a known, inert quantity."""
    monkeypatch.setattr(
        "services.arrears_interest_service.get_effective_interest_rate",
        AsyncMock(return_value={"rate_pct": 0.0, "max_rate_pct": 20.0}),
    )


@pytest.mark.asyncio
async def test_positive_explainable_delta_creates_high_confidence_candidate(monkeypatch):
    _patch_zero_interest(monkeypatch)
    current_id, previous_id = ObjectId(), ObjectId()
    current = _snapshot(current_id, "2026-04-15", [
        {"lot_number": "TH078", "balance_cents": 0, "owner_name": "J. Smith"},
    ])
    previous = _snapshot(previous_id, "2026-01-15", [
        {"lot_number": "TH078", "balance_cents": 55000, "owner_name": "J. Smith"},
    ])
    ledger = {"total_levied": 2200.0}  # 4 quarters => 550.00/period => 55000 cents
    db = _make_db(current=current, previous=previous,
                   levy_doc={"payment_schedule": _SCHEDULE}, ledger=ledger)

    captured = {}

    async def fake_upsert(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        "integrations.demo_bank.ingestion._upsert_transaction", fake_upsert
    )

    result = await derive_strata_web_balance_delta_transactions(
        db=db, building_id=BUILDING_A, financial_year="2026",
        current_snapshot_id=str(current_id),
    )

    assert result["candidates_created"] == 1
    assert result["candidates_skipped"] == 0
    assert captured["source_type"] == "strata_web_inferred_payment"
    assert captured["provenance_class"] == "inferred_from_balance_delta"
    assert captured["requires_review"] is True
    assert captured["confidence"] == "high"  # unit reached a $0 balance
    # previous arrears (55000) + newly-accrued Q2 charge (55000) + interest (0, patched) - current (0)
    assert captured["amount_cents"] == 110000
    assert captured["unit_number"] == "TH078"

    # Never touches unit_levy_ledger for a write.
    db.unit_levy_ledger.find_one.assert_awaited()
    assert not hasattr(db.unit_levy_ledger, "update_one") or not db.unit_levy_ledger.update_one.called


@pytest.mark.asyncio
async def test_accrued_interest_on_opening_arrears_is_included_in_the_inferred_amount(monkeypatch):
    """Doc formula: previous_balance + charges_since + interest_or_penalties_raised
    - current_balance. A nonzero jurisdiction rate must show up in amount_cents —
    this is the term that was missing before this fix."""
    current_id, previous_id = ObjectId(), ObjectId()
    current = _snapshot(current_id, "2026-04-15", [
        {"lot_number": "TH078", "balance_cents": 0, "owner_name": "J. Smith"},
    ])
    previous = _snapshot(previous_id, "2026-01-15", [
        {"lot_number": "TH078", "balance_cents": 55000, "owner_name": "J. Smith"},
    ])
    ledger = {"total_levied": 2200.0}
    db = _make_db(current=current, previous=previous,
                   levy_doc={"payment_schedule": _SCHEDULE}, ledger=ledger)

    monkeypatch.setattr(
        "services.arrears_interest_service.get_effective_interest_rate",
        AsyncMock(return_value={"rate_pct": 10.0, "max_rate_pct": 20.0}),
    )
    monkeypatch.setattr(
        "services.arrears_interest_service.compute_accrued_interest",
        MagicMock(return_value=13.56),
    )

    captured = {}

    async def fake_upsert(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr("integrations.demo_bank.ingestion._upsert_transaction", fake_upsert)

    await derive_strata_web_balance_delta_transactions(
        db=db, building_id=BUILDING_A, financial_year="2026",
        current_snapshot_id=str(current_id),
    )

    # 55000 (opening arrears) + 55000 (Q2 charge) + 1356 (interest cents) - 0 (current)
    assert captured["amount_cents"] == 111356


@pytest.mark.asyncio
async def test_negative_or_unexplained_delta_creates_no_candidate(monkeypatch):
    _patch_zero_interest(monkeypatch)
    current_id, previous_id = ObjectId(), ObjectId()
    current = _snapshot(current_id, "2026-04-15", [
        {"lot_number": "U1", "balance_cents": 60000, "owner_name": "A. Owner"},
    ])
    previous = _snapshot(previous_id, "2026-01-15", [
        {"lot_number": "U1", "balance_cents": 55000, "owner_name": "A. Owner"},
    ])
    # Balance INCREASED and no new charge fell due in the window (no payment_schedule
    # at all) — nothing can explain a positive delta here, so it must be skipped.
    db = _make_db(current=current, previous=previous, levy_doc=None, ledger=None)

    upsert_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("integrations.demo_bank.ingestion._upsert_transaction", upsert_mock)

    result = await derive_strata_web_balance_delta_transactions(
        db=db, building_id=BUILDING_A, financial_year="2026",
        current_snapshot_id=str(current_id),
    )

    assert result["candidates_created"] == 0
    assert result["candidates_skipped"] == 1
    upsert_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_annual_levies_fetched_once_regardless_of_unit_count(monkeypatch):
    """Copilot review: _charges_since() re-fetched the same annual_levies
    (building+year) document on every unit — an avoidable N+1. It must now be
    fetched exactly once per run, shared across all units via the caller."""
    _patch_zero_interest(monkeypatch)
    current_id, previous_id = ObjectId(), ObjectId()
    current = _snapshot(current_id, "2026-04-15", [
        {"lot_number": "U1", "balance_cents": 0, "owner_name": "A"},
        {"lot_number": "U2", "balance_cents": 0, "owner_name": "B"},
        {"lot_number": "U3", "balance_cents": 0, "owner_name": "C"},
    ])
    previous = _snapshot(previous_id, "2026-01-15", [
        {"lot_number": "U1", "balance_cents": 55000, "owner_name": "A"},
        {"lot_number": "U2", "balance_cents": 55000, "owner_name": "B"},
        {"lot_number": "U3", "balance_cents": 55000, "owner_name": "C"},
    ])
    db = _make_db(current=current, previous=previous,
                   levy_doc={"payment_schedule": _SCHEDULE}, ledger={"total_levied": 2200.0})

    monkeypatch.setattr(
        "integrations.demo_bank.ingestion._upsert_transaction", AsyncMock(return_value=True)
    )

    result = await derive_strata_web_balance_delta_transactions(
        db=db, building_id=BUILDING_A, financial_year="2026",
        current_snapshot_id=str(current_id),
    )

    assert result["candidates_created"] == 3
    db.annual_levies.find_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_previous_snapshot_returns_zero_candidates_with_warning():
    current_id = ObjectId()
    current = _snapshot(current_id, "2026-04-15", [
        {"lot_number": "U1", "balance_cents": 0, "owner_name": "A. Owner"},
    ])
    db = _make_db(current=current, previous=None)

    result = await derive_strata_web_balance_delta_transactions(
        db=db, building_id=BUILDING_A, financial_year="2026",
        current_snapshot_id=str(current_id),
    )

    assert result["candidates_created"] == 0
    assert result["warnings"]


# ── GAP-FIN-015 criterion 8 ────────────────────────────────────────────────────
#
# Investigation (2026-07-13) found criterion 8's literal wording — "a high-confidence
# candidate auto-allocates via MatchingEngine" — does not match shipped behavior:
# requires_review=True is unconditional here regardless of confidence tier, and
# source_type="strata_web_inferred_payment" is deliberately excluded from
# financial_matching._PROMOTABLE_SOURCE_TYPES (the deterministic-bypass allowlist
# reserved for data that is exact by construction, e.g. synthetic_from_budget).
# User decision: keep this conservative behavior — it reads as a deliberate design
# choice (unconditional requires_review=True + explicit allowlist exclusion), not
# an oversight. These tests lock in and document the ACTUAL behavior rather than
# the originally-drafted criterion.

@pytest.mark.asyncio
async def test_high_confidence_candidate_still_requires_review_never_auto_allocates(monkeypatch):
    """A $0-balance (fully reconciled) candidate scores confidence='high', but it
    must still carry requires_review=True and a source_type that financial_matching's
    deterministic-promotion allowlist does not recognise — so it can only ever reach
    the ledger via the normal MatchingEngine review queue, never the fast-track path,
    regardless of how confident the label is."""
    _patch_zero_interest(monkeypatch)
    from routers.financial_matching import _PROMOTABLE_SOURCE_TYPES

    current_id, previous_id = ObjectId(), ObjectId()
    current = _snapshot(current_id, "2026-04-15", [
        {"lot_number": "TH078", "balance_cents": 0, "owner_name": "J. Smith"},
    ])
    previous = _snapshot(previous_id, "2026-01-15", [
        {"lot_number": "TH078", "balance_cents": 55000, "owner_name": "J. Smith"},
    ])
    ledger = {"total_levied": 2200.0}
    db = _make_db(current=current, previous=previous,
                   levy_doc={"payment_schedule": _SCHEDULE}, ledger=ledger)

    captured = {}

    async def fake_upsert(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        "integrations.demo_bank.ingestion._upsert_transaction", fake_upsert
    )

    await derive_strata_web_balance_delta_transactions(
        db=db, building_id=BUILDING_A, financial_year="2026",
        current_snapshot_id=str(current_id),
    )

    assert captured["confidence"] == "high"
    assert captured["requires_review"] is True
    assert captured["source_type"] not in _PROMOTABLE_SOURCE_TYPES


@pytest.mark.asyncio
async def test_idempotent_rerun_produces_identical_upsert_transaction_inputs(monkeypatch):
    """Calling derive_strata_web_balance_delta_transactions() twice for the SAME
    snapshot pair must pass byte-identical idempotency-key inputs to
    _upsert_transaction() both times (account_ref, posted_date, amount_cents,
    direction, description, running_balance_cents — the exact fields
    _external_txn_id() hashes into demo_bank_transactions.idempotency_key). This
    proves a real re-run is a no-op via the existing $setOnInsert upsert, without
    needing a live/mongomock database."""
    _patch_zero_interest(monkeypatch)
    current_id, previous_id = ObjectId(), ObjectId()
    current = _snapshot(current_id, "2026-04-15", [
        {"lot_number": "TH078", "balance_cents": 0, "owner_name": "J. Smith"},
    ])
    previous = _snapshot(previous_id, "2026-01-15", [
        {"lot_number": "TH078", "balance_cents": 55000, "owner_name": "J. Smith"},
    ])
    ledger = {"total_levied": 2200.0}

    idem_fields = (
        "account_ref", "posted_date", "amount_cents", "direction",
        "description", "running_balance_cents",
    )
    calls = []

    async def fake_upsert(**kwargs):
        calls.append({k: kwargs.get(k) for k in idem_fields})
        return True

    monkeypatch.setattr(
        "integrations.demo_bank.ingestion._upsert_transaction", fake_upsert
    )

    for _ in range(2):
        db = _make_db(current=current, previous=previous,
                       levy_doc={"payment_schedule": _SCHEDULE}, ledger=ledger)
        result = await derive_strata_web_balance_delta_transactions(
            db=db, building_id=BUILDING_A, financial_year="2026",
            current_snapshot_id=str(current_id),
        )
        assert result["candidates_created"] == 1

    assert len(calls) == 2
    assert calls[0] == calls[1]


@pytest.mark.asyncio
async def test_multi_tenant_isolation_building_b_cannot_see_building_a_candidates(monkeypatch):
    """A run scoped to building 16244 must only ever query 16244-tagged snapshots
    — even when the mock db's underlying query function has 13195 data available,
    the building_id filter passed into every staging_strata_web_snapshots query
    must select only the requested building's own documents."""
    _patch_zero_interest(monkeypatch)
    building_b = "16244"
    current_id, previous_id = ObjectId(), ObjectId()

    current_a = _snapshot(current_id, "2026-04-15", [
        {"lot_number": "TH078", "balance_cents": 0, "owner_name": "J. Smith"},
    ])
    current_b = _snapshot(current_id, "2026-04-15", [
        {"lot_number": "B01", "balance_cents": 0, "owner_name": "B. Owner"},
    ], building_id=building_b)
    previous_b = _snapshot(previous_id, "2026-01-15", [
        {"lot_number": "B01", "balance_cents": 20000, "owner_name": "B. Owner"},
    ], building_id=building_b)

    db = MagicMock()

    async def _find_snapshot(query, *args, **kwargs):
        assert query["building_id"] == building_b, (
            "query must be scoped to the requested building_id only"
        )
        if "_id" in query:
            return current_b if query["_id"] == current_id else None
        if "$lt" in query.get("snapshot_date", {}):
            return previous_b
        return current_b

    db.staging_strata_web_snapshots.find_one = AsyncMock(side_effect=_find_snapshot)
    db.units.find_one = AsyncMock(side_effect=_resolve_unit)
    db.staging_strata_web_snapshots.find = _snapshot_find(
        [current_a, current_b, previous_b], expect_building=building_b,
    )
    db.annual_levies.find_one = AsyncMock(return_value={"payment_schedule": _SCHEDULE})
    db.unit_levy_ledger.find_one = AsyncMock(return_value={"total_levied": 800.0})

    captured = {}

    async def fake_upsert(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        "integrations.demo_bank.ingestion._upsert_transaction", fake_upsert
    )

    result = await derive_strata_web_balance_delta_transactions(
        db=db, building_id=building_b, financial_year="2026",
        current_snapshot_id=str(current_id),
    )

    assert result["building_id"] == building_b
    assert result["candidates_created"] == 1
    assert captured["building_id"] == building_b
    assert captured["unit_number"] == "B01"
    # The building-A snapshot object (current_a) was never consulted.
    assert current_a["building_id"] == BUILDING_A


# ---------------------------------------------------------------------------
# Financial-year label normalisation (2026-08-28)
# ---------------------------------------------------------------------------

class TestFinancialYearLabelPairing:
    """Two snapshots of the SAME year under DIFFERENT labels must still pair.

    `staging_strata_web_snapshots.financial_year` stores whatever label its caller
    passed, and East Gate holds "2025", "2026" and "2026-2027" side by side. The
    exact-string match this replaces meant such a pair never matched, so the run
    returned "no earlier snapshot to compare against" and produced ZERO candidates
    — a whole scraper run silently reduced to a no-op.
    """

    @pytest.mark.asyncio
    async def test_hyphenated_and_plain_labels_pair(self, monkeypatch):
        _patch_zero_interest(monkeypatch)
        # Same actual year (FY2026), labelled two different ways.
        previous = _snapshot(ObjectId(), "2026-01-15", [
            {"lot_number": "UA001", "balance_cents": 50000, "owner_name": "A. Owner"},
        ], financial_year="2025-2026")
        current = _snapshot(ObjectId(), "2026-04-15", [
            {"lot_number": "UA001", "balance_cents": 0, "owner_name": "A. Owner"},
        ], financial_year="2026")

        db = _make_db(current=current, previous=previous,
                      levy_doc={"payment_schedule": _SCHEDULE},
                      ledger={"total_levied": 800.0})
        created = []

        async def fake_upsert(**kwargs):
            created.append(kwargs)
            return True

        monkeypatch.setattr("integrations.demo_bank.ingestion._upsert_transaction", fake_upsert)
        result = await derive_strata_web_balance_delta_transactions(db, BUILDING_A, "2026")

        assert result["candidates_created"] >= 1, (
            f"labels '2025-2026' and '2026' name the same year and must pair; got {result}"
        )

    @pytest.mark.asyncio
    async def test_single_snapshot_says_how_many_it_found(self, monkeypatch):
        """The old warning said only 'no earlier snapshot'. It must now say how many
        snapshots the year actually has, so a no-op run is diagnosable."""
        _patch_zero_interest(monkeypatch)
        current = _snapshot(ObjectId(), "2026-04-15", [
            {"lot_number": "UA001", "balance_cents": 0, "owner_name": "A. Owner"},
        ])
        db = _make_db(current=current, previous=None)
        result = await derive_strata_web_balance_delta_transactions(db, BUILDING_A, "2026")

        assert result["candidates_created"] == 0
        assert result["snapshots_for_year"] == 1
        assert "TWO" in result["warnings"][0]

    @pytest.mark.asyncio
    async def test_different_years_do_not_pair(self, monkeypatch):
        """Normalisation must not over-match: FY2026 and FY2027 are different years
        and a balance delta across them would be meaningless."""
        _patch_zero_interest(monkeypatch)
        other_year = _snapshot(ObjectId(), "2026-01-15", [
            {"lot_number": "UA001", "balance_cents": 50000, "owner_name": "A. Owner"},
        ], financial_year="2026-2027")   # normalises to 2027
        current = _snapshot(ObjectId(), "2026-04-15", [
            {"lot_number": "UA001", "balance_cents": 0, "owner_name": "A. Owner"},
        ], financial_year="2026")
        db = _make_db(current=current, previous=other_year)
        result = await derive_strata_web_balance_delta_transactions(db, BUILDING_A, "2026")

        assert result["candidates_created"] == 0
        assert result["snapshots_for_year"] == 1
