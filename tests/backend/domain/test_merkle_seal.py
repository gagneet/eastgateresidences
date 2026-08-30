"""
tests/backend/domain/test_merkle_seal.py — Unit tests for workers/merkle_seal.py.

Covers:
  - compute_merkle_root: empty, single, even/odd leaf counts, determinism.
  - verify_seal: intact seal passes, tampered journal fails, tampered hash fails.
  - seal_period: correct seal fields written, filesystem export created.
  - Hash chain: this_seal_hash changes when previous_seal_hash changes.
  - Multi-tenant isolation: building_id partitions seal documents.
  - Retroactive tampering detection.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from workers.merkle_seal import (
    _journal_leaf_hash,
    _sha256,
    compute_merkle_root,
    seal_period,
    verify_seal,
)

BUILDING_A = "16244"
BUILDING_B = "13195"


# ── _sha256 ───────────────────────────────────────────────────────────────────

def test_sha256_is_deterministic():
    assert _sha256("hello") == _sha256("hello")


def test_sha256_different_inputs_differ():
    assert _sha256("hello") != _sha256("world")


# ── compute_merkle_root ───────────────────────────────────────────────────────

def test_empty_tree_returns_hash_of_empty_string():
    root = compute_merkle_root([])
    assert root == _sha256("")


def test_single_leaf():
    h = _sha256("leaf")
    root = compute_merkle_root([h])
    assert root == h


def test_two_leaves():
    h1 = _sha256("a")
    h2 = _sha256("b")
    root = compute_merkle_root([h1, h2])
    assert root == _sha256(h1 + h2)


def test_three_leaves_pads_to_four():
    """Odd leaf count: last leaf is duplicated before hashing the pair."""
    h1, h2, h3 = _sha256("a"), _sha256("b"), _sha256("c")
    expected_left = _sha256(h1 + h2)
    expected_right = _sha256(h3 + h3)  # h3 duplicated
    expected_root = _sha256(expected_left + expected_right)
    assert compute_merkle_root([h1, h2, h3]) == expected_root


def test_merkle_root_is_deterministic():
    leaves = [_sha256(str(i)) for i in range(8)]
    assert compute_merkle_root(leaves) == compute_merkle_root(leaves)


def test_merkle_root_changes_on_leaf_mutation():
    leaves = [_sha256(str(i)) for i in range(4)]
    mutated = list(leaves)
    mutated[2] = _sha256("tampered")
    assert compute_merkle_root(leaves) != compute_merkle_root(mutated)


# ── _journal_leaf_hash ────────────────────────────────────────────────────────

def test_leaf_hash_is_deterministic():
    entry = {"_id": "abc", "date": "2026-04-23", "debit_total_cents": 10000,
             "credit_total_cents": 10000, "reference": "REF-1", "building_id": BUILDING_A}
    assert _journal_leaf_hash(entry) == _journal_leaf_hash(entry)


def test_leaf_hash_differs_when_amount_changes():
    e1 = {"_id": "x", "date": "2026-04-23", "debit_total_cents": 10000,
          "credit_total_cents": 10000, "reference": "R", "building_id": BUILDING_A}
    e2 = {**e1, "debit_total_cents": 10001, "credit_total_cents": 10001}
    assert _journal_leaf_hash(e1) != _journal_leaf_hash(e2)


# ── verify_seal ───────────────────────────────────────────────────────────────

def _make_seal_and_entries():
    entries = [
        {"_id": "e1", "date": "2026-04-23", "debit_total_cents": 5000,
         "credit_total_cents": 5000, "reference": "R1", "building_id": BUILDING_A,
         "status": "posted"},
    ]
    leaf_hashes = [_journal_leaf_hash(e) for e in entries]
    merkle_root = compute_merkle_root(leaf_hashes)
    control_total = 5000
    prev_hash = _sha256("genesis")
    date = "2026-04-23"
    this_hash = _sha256(BUILDING_A + merkle_root + str(control_total) + prev_hash + date)
    seal = {
        "tenant_id": BUILDING_A,
        "period_id": "2026-Q1",
        "date": date,
        "merkle_root": merkle_root,
        "journal_count": 1,
        "control_total_cents": control_total,
        "previous_seal_hash": prev_hash,
        "this_seal_hash": this_hash,
    }
    return seal, entries, prev_hash


def test_verify_seal_intact():
    seal, entries, prev_hash = _make_seal_and_entries()
    assert verify_seal(seal, entries, prev_hash) is True


def test_verify_seal_detects_tampered_journal_amount():
    seal, entries, prev_hash = _make_seal_and_entries()
    tampered_entries = [{**entries[0], "debit_total_cents": 9999}]
    assert verify_seal(seal, tampered_entries, prev_hash) is False


def test_verify_seal_detects_tampered_this_hash():
    seal, entries, prev_hash = _make_seal_and_entries()
    tampered_seal = {**seal, "this_seal_hash": _sha256("forged")}
    assert verify_seal(tampered_seal, entries, prev_hash) is False


def test_verify_seal_detects_extra_journal_retroactively():
    """Adding a journal entry to a closed period must break the seal."""
    seal, entries, prev_hash = _make_seal_and_entries()
    extra_entry = {"_id": "e_extra", "date": "2026-04-23", "debit_total_cents": 1000,
                   "credit_total_cents": 1000, "reference": "EXTRA", "building_id": BUILDING_A}
    tampered_entries = entries + [extra_entry]
    assert verify_seal(seal, tampered_entries, prev_hash) is False


# ── seal_period (async integration) ──────────────────────────────────────────

def _make_seal_db(entries=None):
    db = MagicMock()
    entries = entries or []

    sort_cursor = MagicMock()
    sort_cursor.to_list = AsyncMock(return_value=entries)
    find_cursor = MagicMock()
    find_cursor.sort = MagicMock(return_value=sort_cursor)
    db.trust_ledger_entries.find = MagicMock(return_value=find_cursor)

    db.trust_ledger_seals.find_one = AsyncMock(return_value=None)  # no prior seal
    db.trust_ledger_seals.insert_one = AsyncMock(return_value=MagicMock(inserted_id="seal-1"))
    return db


@pytest.mark.asyncio
async def test_seal_period_writes_correct_fields(tmp_path):
    entry = {"_id": "e1", "date": "2026-04-23", "debit_total_cents": 10000,
             "credit_total_cents": 10000, "reference": "R", "building_id": BUILDING_A,
             "status": "posted"}
    db = _make_seal_db([entry])

    with patch.dict("os.environ", {"SEAL_EXPORT_PATH": str(tmp_path)}):
        seal = await seal_period(BUILDING_A, "2026-Q1", db, seal_date="2026-04-23")

    assert seal["tenant_id"] == BUILDING_A
    assert seal["period_id"] == "2026-Q1"
    assert seal["journal_count"] == 1
    assert seal["control_total_cents"] == 10000
    assert len(seal["merkle_root"]) == 64  # SHA-256 hex
    assert len(seal["this_seal_hash"]) == 64
    db.trust_ledger_seals.insert_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_seal_period_exports_to_filesystem(tmp_path):
    entry = {"_id": "e1", "date": "2026-04-23", "debit_total_cents": 5000,
             "credit_total_cents": 5000, "reference": "R", "building_id": BUILDING_A,
             "status": "posted"}
    db = _make_seal_db([entry])

    with patch.dict("os.environ", {"SEAL_EXPORT_PATH": str(tmp_path)}):
        await seal_period(BUILDING_A, "2026-Q1", db, seal_date="2026-04-23")

    seal_file = tmp_path / BUILDING_A / "2026-Q1" / "seal_2026-04-23.json"
    assert seal_file.exists()
    data = json.loads(seal_file.read_text())
    assert data["tenant_id"] == BUILDING_A


@pytest.mark.asyncio
async def test_seal_period_empty_period_has_known_merkle_root(tmp_path):
    """An empty period produces a deterministic merkle_root = sha256('')."""
    db = _make_seal_db([])
    with patch.dict("os.environ", {"SEAL_EXPORT_PATH": str(tmp_path)}):
        seal = await seal_period(BUILDING_A, "2026-Q1", db, seal_date="2026-04-23")
    assert seal["journal_count"] == 0
    assert seal["merkle_root"] == _sha256("")


@pytest.mark.asyncio
async def test_hash_chain_links_to_previous_seal(tmp_path):
    """The second seal's previous_seal_hash must equal the first seal's this_seal_hash."""
    prev_seal = {
        "this_seal_hash": _sha256("prior-seal"),
        "tenant_id": BUILDING_A,
        "period_id": "2026-Q1",
        "date": "2026-04-22",
    }
    db = _make_seal_db([])
    db.trust_ledger_seals.find_one = AsyncMock(return_value=prev_seal)

    with patch.dict("os.environ", {"SEAL_EXPORT_PATH": str(tmp_path)}):
        seal = await seal_period(BUILDING_A, "2026-Q1", db, seal_date="2026-04-23")

    assert seal["previous_seal_hash"] == prev_seal["this_seal_hash"]


@pytest.mark.asyncio
async def test_seal_period_idempotent_same_day(tmp_path):
    """Calling seal_period when today's seal already exists must return it without re-inserting."""
    existing_seal = {
        "tenant_id": BUILDING_A,
        "period_id": "2026-Q1",
        "date": "2026-04-23",
        "merkle_root": _sha256("existing"),
        "journal_count": 1,
        "control_total_cents": 5000,
        "previous_seal_hash": _sha256("genesis"),
        "this_seal_hash": "idempotency-guard-hash",
        "sealed_at": "2026-04-23T01:00:00+00:00",
    }
    db = _make_seal_db([])
    db.trust_ledger_seals.find_one = AsyncMock(return_value=existing_seal)

    with patch.dict("os.environ", {"SEAL_EXPORT_PATH": str(tmp_path)}):
        seal = await seal_period(BUILDING_A, "2026-Q1", db, seal_date="2026-04-23")

    assert seal["this_seal_hash"] == "idempotency-guard-hash"
    db.trust_ledger_seals.insert_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_seal_per_building_isolated(tmp_path):
    """Seals for BUILDING_A and BUILDING_B use separate find queries."""
    db_a = _make_seal_db([])
    db_b = _make_seal_db([])

    with patch.dict("os.environ", {"SEAL_EXPORT_PATH": str(tmp_path)}):
        seal_a = await seal_period(BUILDING_A, "2026-Q1", db_a, seal_date="2026-04-23")
        seal_b = await seal_period(BUILDING_B, "2026-Q1", db_b, seal_date="2026-04-23")

    assert seal_a["tenant_id"] == BUILDING_A
    assert seal_b["tenant_id"] == BUILDING_B
    # building_id IS included in this_seal_hash, so even identical empty-period seals
    # on the same date produce different hashes for different tenants.
    assert seal_a["this_seal_hash"] != seal_b["this_seal_hash"]
    assert seal_a["tenant_id"] != seal_b["tenant_id"]
