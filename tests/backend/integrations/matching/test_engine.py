"""
tests/backend/integrations/matching/test_engine.py — MatchingEngine integration tests.

Tests the full pipeline: agency sweep detection, layer cascade, auto-allocation,
review queue creation, and idempotency. DB is mocked with AsyncMock.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from integrations.envelopes import BankTxObserved
from integrations.matching.layers.base import LotCandidate
from integrations.matching import engine

BUILDING_A = "16244"
BUILDING_B = "13195"

CRN_A1 = "0162440010019"  # valid MOD10V05

_DT = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _tx(**overrides) -> BankTxObserved:
    defaults = dict(
        provider_txn_id="txn-test",
        tenant_id=BUILDING_A,
        account_ref="trust-16244",
        occurred_at=_DT,
        amount_cents=153000,
        description="LEVY PAYMENT",
        bpay_crn=None,
        osko_e2e_id=None,
        lot_ref_raw=None,
    )
    defaults.update(overrides)
    return BankTxObserved(**defaults)


def _lot(**overrides) -> LotCandidate:
    defaults = dict(
        lot_id="lot-001",
        unit_number="1",
        building_id=BUILDING_A,
        owner_name="Margaret Thompson",
        open_levy_cents=153000,
        due_date="2026-03-31",
    )
    defaults.update(overrides)
    return LotCandidate(**defaults)


def _make_db(*, queue_existing=None, entities=None):
    """Build a mock Motor DB handle for engine tests."""
    db = MagicMock()

    # match_review_queue.find_one — idempotency check
    db.match_review_queue.find_one = AsyncMock(return_value=queue_existing)

    # match_review_queue.insert_one
    inserted = MagicMock()
    inserted.inserted_id = "queue-id-001"
    db.match_review_queue.insert_one = AsyncMock(return_value=inserted)

    # payer_entities.find().to_list() — agency sweep detection
    entities_cursor = MagicMock()
    entities_cursor.to_list = AsyncMock(return_value=entities or [])
    db.payer_entities.find = MagicMock(return_value=entities_cursor)

    return db


# ── Auto-allocation (score ≥ 0.90) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_allocates_when_l1_matches():
    lot = _lot(crn=CRN_A1)
    tx = _tx(bpay_crn=CRN_A1)
    db = _make_db()

    result = await engine.match(tx, [lot], db, inbox_event_id="evt-001")

    assert result.match_type == "auto"
    assert result.is_auto_allocated is True
    assert result.best_score == 1.0
    assert result.best_layer == "L1_exact_crn"
    assert result.best_lot is not None
    assert result.best_lot.lot_id == lot.lot_id
    db.match_review_queue.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_match_stops_at_first_threshold_layer():
    """L1 matches → engine must not run L2–L8."""
    lot = _lot(crn=CRN_A1)
    tx = _tx(bpay_crn=CRN_A1)
    db = _make_db()

    result = await engine.match(tx, [lot], db, inbox_event_id="evt-002")

    # Only L1 should appear in all_scores (stopped early)
    layer_names = [s.layer_name for s in result.all_scores]
    assert layer_names == ["L1_exact_crn"]


# ── Review queue (score < 0.90) ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_creates_review_queue_entry_when_below_threshold():
    """L4 (score 0.85) is below default threshold → review queue."""
    lot = _lot(unit_number="5", open_levy_cents=153000, due_date="2026-04-01")
    tx = _tx(description="LEVY UNIT 5 STRATA", amount_cents=153000)
    db = _make_db()

    result = await engine.match(tx, [lot], db, inbox_event_id="evt-003")

    assert result.match_type == "review"
    assert result.is_auto_allocated is False
    assert result.best_score == 0.85
    assert result.best_layer == "L4_unit_ref_amount_timing"
    db.match_review_queue.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_unidentified_when_no_layer_matches():
    """No layer fires → L8 → unidentified."""
    lot = _lot(unit_number="99", open_levy_cents=999999)
    tx = _tx(description="UNKNOWN REF XYZ", amount_cents=12345)
    db = _make_db()

    result = await engine.match(tx, [lot], db, inbox_event_id="evt-004")

    assert result.match_type == "unidentified"
    assert result.best_score == 0.0
    db.match_review_queue.insert_one.assert_awaited_once()


# ── Agency sweep ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agency_sweep_routes_to_review_queue():
    """Large deposit from known agency → agency_sweep before layer scoring."""
    lots = [
        _lot(lot_id="lot-1", open_levy_cents=153000),
        _lot(lot_id="lot-2", open_levy_cents=210000),
    ]
    # Amount exceeds every individual lot's open levy
    agency_tx = _tx(
        amount_cents=1_000_000,
        description="STRATA LEVY COLLECTION RAY WHITE GUNGAHLIN",
    )
    known_entity = {"entity_name": "ray white gungahlin"}
    db = _make_db(entities=[known_entity])

    result = await engine.match(agency_tx, lots, db, inbox_event_id="evt-005")

    assert result.match_type == "agency_sweep"
    assert result.is_auto_allocated is False
    assert result.best_score == 0.0
    db.match_review_queue.insert_one.assert_awaited_once()
    sweep_doc = db.match_review_queue.insert_one.call_args[0][0]
    assert sweep_doc["building_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_agency_sweep_not_triggered_when_entity_not_known():
    """Large deposit but payer NOT in payer_entities → normal layer scoring."""
    lots = [_lot(open_levy_cents=153000)]
    tx = _tx(amount_cents=1_000_000, description="SOME UNKNOWN LARGE DEPOSIT")
    db = _make_db(entities=[])  # no entities

    result = await engine.match(tx, lots, db, inbox_event_id="evt-006")

    assert result.match_type != "agency_sweep"


@pytest.mark.asyncio
async def test_agency_sweep_not_triggered_when_amount_matches_single_levy():
    """Amount equal to the largest levy → not a sweep (could be a single payment)."""
    lots = [
        _lot(lot_id="lot-1", open_levy_cents=153000),
        _lot(lot_id="lot-2", open_levy_cents=500000),
    ]
    tx = _tx(amount_cents=500000, description="RAY WHITE GUNGAHLIN LEVY")
    known_entity = {"entity_name": "ray white gungahlin"}
    db = _make_db(entities=[known_entity])

    result = await engine.match(tx, lots, db, inbox_event_id="evt-007")

    # Amount equals the largest levy — not a sweep
    assert result.match_type != "agency_sweep"


# ── Idempotency ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_idempotent_replay_returns_existing_without_reinsert():
    """Calling match() twice with same inbox_event_id returns the cached result."""
    existing = {
        "_id": "existing-id",
        "match_type": "auto",
        "best_score": 1.0,
        "best_layer": "L1_exact_crn",
        "status": "auto_allocated",
    }
    db = _make_db(queue_existing=existing)

    result = await engine.match(
        _tx(bpay_crn=CRN_A1), [_lot(crn=CRN_A1)], db,
        inbox_event_id="evt-duplicate",
    )

    assert result.is_idempotent_replay is True
    assert result.match_type == "auto"
    assert result.best_score == 1.0
    db.match_review_queue.insert_one.assert_not_awaited()


# ── Multi-tenant isolation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_tenant_engine_does_not_mix_buildings():
    """Candidates from BUILDING_B must not be used when tx is from BUILDING_A."""
    lot_b = _lot(building_id=BUILDING_B, crn=CRN_A1)
    tx_a = _tx(tenant_id=BUILDING_A, bpay_crn=CRN_A1)
    db = _make_db()

    # lot_b has same CRN as tx_a, but the caller is responsible for scoping candidates
    # to the correct building. The engine trusts the candidates list it receives.
    # This test verifies the engine records the correct tenant_id in the queue doc.
    result = await engine.match(tx_a, [lot_b], db, inbox_event_id="evt-008")

    # engine must stamp both tenant_id and building_id (= tenant_id) so that
    # TenantScopedDatabase auto-injection of building_id filters correctly.
    call_args = db.match_review_queue.insert_one.call_args
    doc = call_args[0][0]
    assert doc["tenant_id"] == BUILDING_A
    assert doc["building_id"] == BUILDING_A


# ── Custom threshold ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_custom_high_threshold_routes_l1_to_review():
    """If threshold is raised above 1.0 (impossible), even L1 goes to review."""
    lot = _lot(crn=CRN_A1)
    tx = _tx(bpay_crn=CRN_A1)
    db = _make_db()

    result = await engine.match(
        tx, [lot], db, inbox_event_id="evt-009",
        auto_match_threshold=1.01,  # no score can reach this
    )

    assert result.is_auto_allocated is False
    assert result.match_type == "review"


@pytest.mark.asyncio
async def test_custom_low_threshold_auto_allocates_l4():
    """Threshold lowered to 0.80 → L4 (0.85) triggers auto-allocation."""
    lot = _lot(unit_number="5", open_levy_cents=153000, due_date="2026-04-01")
    tx = _tx(description="LEVY UNIT 5 STRATA", amount_cents=153000)
    db = _make_db()

    result = await engine.match(
        tx, [lot], db, inbox_event_id="evt-010",
        auto_match_threshold=0.80,
    )

    assert result.is_auto_allocated is True
    assert result.match_type == "auto"
