"""Phase 2 contract tests for backend/migrations/postgres/migrate_building_13195.py.

These tests exercise the Phase 2 steps (07–11) with mocked Mongo and Postgres
clients so we can lock in the contract — Mongo source field names, deterministic
UUIDv5 IDs, ON CONFLICT idempotency, and the FIFO allocation + hash-chain
invariants — without requiring a real database.

Phase 1 steps (01–06) are exercised in dry-run mode in test_migration_pipeline.py.

The tests use a recorded list of SQL execute() calls per asyncpg connection and
assert on the structure of those calls, not on the live PG state. This is the
same pattern used by tests/backend/test_d004_user_units_migration.py.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest


# Load the migration module by path because the migrations folder is not a
# regular import root. Same trick used in test_migration_021_indexes.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "backend" / "migrations" / "postgres" / "migrate_building_13195.py"


def _load_migration_module():
    if "migrate_building_13195" in sys.modules:
        return sys.modules["migrate_building_13195"]
    spec = importlib.util.spec_from_file_location(
        "migrate_building_13195", _MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["migrate_building_13195"] = module
    spec.loader.exec_module(module)
    return module


migration_mod = _load_migration_module()
Migration13195 = migration_mod.Migration13195
make_id = migration_mod.make_id
cents = migration_mod.cents
BUILDING_ID = migration_mod.BUILDING_ID


# --------------------------------------------------------------- mock plumbing

class _RecordedPgConn:
    """Minimal asyncpg connection mock that records every execute/fetch call.

    fetch() returns rows from a table-keyed registry the test pre-populates;
    fetchval() returns a scalar from the same registry; execute() records the
    call and returns None. fetchrow() returns the first registered row.
    """

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.fetch_responses: dict[str, list[dict]] = {}
        self.fetchval_responses: dict[str, Any] = {}

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql.strip(), args))

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        for key, rows in self.fetch_responses.items():
            if key in sql:
                return rows
        return []

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        for key, val in self.fetchval_responses.items():
            if key in sql:
                return val
        return 0


class _MotorCollection:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def find(self, *args, **kwargs):
        return _MotorCursor(self._docs)


class _MotorCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    async def to_list(self, _limit):
        return list(self._docs)


class _MotorDb:
    def __init__(self, collections: dict[str, list[dict]]) -> None:
        self._cols = collections

    def __getitem__(self, name: str) -> _MotorCollection:
        return _MotorCollection(self._cols.get(name, []))

    def __getattr__(self, name: str) -> _MotorCollection:
        return _MotorCollection(self._cols.get(name, []))


class _MotorClient:
    def __init__(self, collections: dict[str, list[dict]]) -> None:
        self._db = _MotorDb(collections)

    def __getitem__(self, name: str) -> _MotorDb:
        return self._db


# --------------------------------------------------------------- fixtures

UNIT_NUMBER = "TH071"
LOT_NUMBER = "LOT71"


@pytest.fixture
def state_with_phase1():
    """Migration state populated as if steps 01–06 had run."""
    state = migration_mod.MigrationState()
    state.tenant_id = make_id("tenant", "Silverfox Technologies")
    state.scheme_id = make_id("scheme", BUILDING_ID)
    state.admin_fund_id = make_id("fund", BUILDING_ID, "admin")
    state.cw_fund_id = make_id("fund", BUILDING_ID, "capital_works")
    state.period_id = make_id("period", BUILDING_ID, "2026")
    state.gl_map = {
        "1010": make_id("gl", BUILDING_ID, "1010"),
        "1100": make_id("gl", BUILDING_ID, "1100"),
        "4000": make_id("gl", BUILDING_ID, "4000"),
        "4001": make_id("gl", BUILDING_ID, "4001"),
    }
    lot_id = make_id("lot", BUILDING_ID, LOT_NUMBER)
    state.lot_map = {LOT_NUMBER: lot_id}
    state.unit_map = {UNIT_NUMBER: lot_id}
    party_id = make_id("party", BUILDING_ID, "Test Owner", "owner@example.com")
    state.party_map = {LOT_NUMBER: party_id, UNIT_NUMBER: party_id}
    return state


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DB_NAME", "strataos_test")
    return monkeypatch


# --------------------------------------------------------------- step 07

@pytest.mark.asyncio
async def test_step_07_writes_one_ownership_period_per_membership(state_with_phase1, env):
    pg = _RecordedPgConn()
    pg.fetchval_responses["FROM core.ownership_periods"] = 0

    # Step 07 resolves owner_name via users collection — user_id in user_units
    # must have a matching document in users with full_name so owner_name is non-empty.
    # Without this, the migration logs "0 written, 1 skipped" (owner_name empty → skip).
    owner_user_id = "test-owner-uid-001"
    mongo = _MotorClient({
        "user_units": [
            {
                "_id": "uu_1",
                "building_id": BUILDING_ID,
                "unit_number": UNIT_NUMBER,
                "user_id": owner_user_id,
                "status": "active",
                "move_in_date": datetime(2024, 1, 15, tzinfo=timezone.utc),
                "is_test_data": False,
            },
        ],
        "users": [
            {
                "_id": "users_1",
                "id": owner_user_id,
                "full_name": "Test Owner",
                "email": "owner@example.com",
            },
        ],
    })
    mig = Migration13195(mongo, pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_07_ownership_periods()

    inserts = [c for c in pg.executed if "INTO core.ownership_periods" in c[0]]
    assert len(inserts) == 1
    args = inserts[0][1]
    # ownership_period_id, tenant_id, scheme_id, lot_id, owner_party_id, valid_from, ...
    assert isinstance(args[0], UUID)
    assert args[1] == state_with_phase1.tenant_id
    assert args[2] == state_with_phase1.scheme_id
    assert args[3] == state_with_phase1.unit_map[UNIT_NUMBER]
    # owner_party_id is computed from owner_name+email, not from state.party_map
    assert isinstance(args[4], UUID)
    assert args[5] == date(2024, 1, 15)
    assert mig._stats["ownership_periods"] == 1


@pytest.mark.asyncio
async def test_step_07_skips_when_existing_rows_present(state_with_phase1, env):
    pg = _RecordedPgConn()
    pg.fetchval_responses["FROM core.ownership_periods"] = 5  # already-populated scheme
    mongo = _MotorClient({"user_units": []})
    mig = Migration13195(mongo, pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_07_ownership_periods()

    assert not any("INTO core.ownership_periods" in c[0] for c in pg.executed)
    assert mig._stats["ownership_periods"] == 0


@pytest.mark.asyncio
async def test_step_07_dry_run_writes_nothing(state_with_phase1, env):
    pg = _RecordedPgConn()
    mongo = _MotorClient({"user_units": [{"unit_number": UNIT_NUMBER}]})
    mig = Migration13195(mongo, pg, dry_run=True)
    mig.state = state_with_phase1

    await mig._step_07_ownership_periods()

    assert pg.executed == []
    assert mig._stats["ownership_periods"] == 0


@pytest.mark.asyncio
async def test_step_07_units_snapshot_uses_real_transfer_date_not_seed_timestamp(state_with_phase1, env):
    """2026-07-24 regression: the units.owner_name snapshot supplement pass used
    to anchor the CURRENT owner's ownership_period at units.created_at — a
    uniform onboarding/seed timestamp, never a real transfer date. Found live on
    East Gate 13195: every transferred lot's new-owner period was dated to the
    seed timestamp instead of when the transfer actually happened, leaving a gap
    historical ledger postings fell into. A unit present in ownership_transfer_log
    must use that real transfer_date instead."""
    pg = _RecordedPgConn()
    pg.fetchval_responses["FROM core.ownership_periods"] = 0
    real_transfer_date = "2023-03-27"
    mongo = _MotorClient({
        "user_units": [],
        "units": [{
            "unit_number": UNIT_NUMBER,
            "owner_name": "New Owner",
            "owner_email": "new.owner@example.com",
            "owner_name_b": "",
            "owner_email_b": "",
            "created_at": datetime(2026, 2, 14, 13, 22, 26, tzinfo=timezone.utc),  # seed timestamp
        }],
        "ownership_transfer_log": [{
            "building_id": BUILDING_ID,
            "unit_number": UNIT_NUMBER,
            "transfer_date": real_transfer_date,
            "status": "processed",
        }],
    })
    mig = Migration13195(mongo, pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_07_ownership_periods()

    inserts = [c for c in pg.executed if "INTO core.ownership_periods" in c[0]]
    assert len(inserts) == 1
    valid_from = inserts[0][1][5]
    assert valid_from == date(2023, 3, 27), (
        f"expected the real transfer_date {real_transfer_date}, got {valid_from} "
        "(the seed timestamp 2026-02-14 would mean the regression is back)"
    )


@pytest.mark.asyncio
async def test_step_07_units_snapshot_uses_handover_date_when_never_transferred(state_with_phase1, env):
    """A unit with no ownership_transfer_log entry has never changed hands —
    its current owner has held it since building handover (2020-12-01), never
    the meaningless units.created_at seed timestamp."""
    pg = _RecordedPgConn()
    pg.fetchval_responses["FROM core.ownership_periods"] = 0
    mongo = _MotorClient({
        "user_units": [],
        "units": [{
            "unit_number": UNIT_NUMBER,
            "owner_name": "Original Owner",
            "owner_email": "original.owner@example.com",
            "owner_name_b": "",
            "owner_email_b": "",
            "created_at": datetime(2026, 2, 14, 13, 22, 26, tzinfo=timezone.utc),
        }],
        "ownership_transfer_log": [],
    })
    mig = Migration13195(mongo, pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_07_ownership_periods()

    inserts = [c for c in pg.executed if "INTO core.ownership_periods" in c[0]]
    assert len(inserts) == 1
    assert inserts[0][1][5] == date(2020, 12, 1)


# --------------------------------------------------------------- step 08

@pytest.mark.asyncio
async def test_step_08_creates_one_run_per_year_and_two_items_per_unit(state_with_phase1, env):
    pg = _RecordedPgConn()
    mongo = _MotorClient({
        "unit_levy_ledger": [
            {
                "building_id": BUILDING_ID,
                "year": "2025",
                "unit_number": UNIT_NUMBER,
                "admin_levied": 1500.00,
                "admin_paid": 1500.00,
                "sinking_levied": 500.00,
                "sinking_paid": 250.00,
                "is_test_data": False,
            },
            {
                "building_id": BUILDING_ID,
                "year": "2026",
                "unit_number": UNIT_NUMBER,
                "admin_levied": 1700.00,
                "admin_paid": 0.00,
                "sinking_levied": 600.00,
                "sinking_paid": 0.00,
                "is_test_data": False,
            },
        ],
    })
    mig = Migration13195(mongo, pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_08_levy_runs_and_items()

    runs = [c for c in pg.executed if "INTO finance.levy_runs" in c[0]]
    items = [c for c in pg.executed if "INTO finance.levy_items" in c[0]]

    assert len(runs) == 2  # one per year
    assert len(items) == 4  # admin + sinking × 2 years

    # Confirm fund_ids were used correctly (admin vs cw)
    admin_inserts = [i for i in items if i[1][6] == state_with_phase1.admin_fund_id]
    cw_inserts = [i for i in items if i[1][6] == state_with_phase1.cw_fund_id]
    assert len(admin_inserts) == 2
    assert len(cw_inserts) == 2

    # SQL placeholders: $1..$8 = ..principal, gst_cents hardcoded to 0,
    # $9 = paid_cents, $10 = status. So principal is at index 7, paid at 8, status at 9.

    # 2025 admin item: principal 150000c, paid 150000c -> 'paid'
    paid_2025 = next(i for i in admin_inserts if i[1][7] == 150000 and i[1][8] == 150000)
    assert paid_2025[1][9] == "paid"

    # 2026 sinking: principal 60000c, paid 0c -> 'issued'
    issued_2026 = next(i for i in cw_inserts if i[1][7] == 60000 and i[1][8] == 0)
    assert issued_2026[1][9] == "issued"

    assert mig._stats["levy_runs"] == 2
    assert mig._stats["levy_items"] == 4


@pytest.mark.asyncio
async def test_step_08_skips_unmapped_unit_numbers(state_with_phase1, env):
    pg = _RecordedPgConn()
    mongo = _MotorClient({
        "unit_levy_ledger": [
            {
                "building_id": BUILDING_ID,
                "year": "2025",
                "unit_number": "GHOST_UNIT",  # not in unit_map
                "admin_levied": 999.00,
                "admin_paid": 0,
                "sinking_levied": 0,
                "sinking_paid": 0,
                "is_test_data": False,
            },
        ],
    })
    mig = Migration13195(mongo, pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_08_levy_runs_and_items()

    assert not any("INTO finance.levy_items" in c[0] for c in pg.executed)
    assert not any("INTO finance.levy_runs" in c[0] for c in pg.executed)


# --------------------------------------------------------------- step 09

@pytest.mark.asyncio
async def test_step_09_inserts_receipts_with_canonical_channels(state_with_phase1, env):
    pg = _RecordedPgConn()
    mongo = _MotorClient({
        "levy_payments": [
            {
                "_id": "pay_eft",
                "building_id": BUILDING_ID,
                "unit_number": UNIT_NUMBER,
                "amount": 1500.00,
                "payment_method": "bank_transfer",  # maps to 'eft'
                "payment_date": datetime(2025, 9, 1, tzinfo=timezone.utc),
                "status": "confirmed",
                "is_test_data": False,
                "reference": "REF-EFT",
            },
            {
                "_id": "pay_credit",
                "building_id": BUILDING_ID,
                "unit_number": UNIT_NUMBER,
                "amount_cents": 50000,  # int wins over amount
                "payment_method": "credit_card",  # maps to 'card'
                "payment_date": datetime(2025, 12, 1, tzinfo=timezone.utc),
                "status": "completed",
                "is_test_data": False,
            },
            {
                "_id": "pay_skip",
                "building_id": BUILDING_ID,
                "unit_number": "MISSING_UNIT",
                "amount": 100.00,
                "status": "confirmed",
            },
        ],
    })
    mig = Migration13195(mongo, pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_09_receipts()

    inserts = [c for c in pg.executed if "INTO finance.receipts" in c[0]]
    assert len(inserts) == 2

    channels = sorted(c[1][5] for c in inserts)
    assert channels == ["card", "eft"]

    eft_args = next(c[1] for c in inserts if c[1][5] == "eft")
    # receipt_id, tenant, scheme, party, lot, channel, received_on, amount_cents, ref
    assert eft_args[7] == 150000
    assert eft_args[8] == "REF-EFT"
    assert eft_args[6] == date(2025, 9, 1)

    card_args = next(c[1] for c in inserts if c[1][5] == "card")
    assert card_args[7] == 50000  # amount_cents int preferred over amount float

    assert mig._stats["receipts"] == 2


@pytest.mark.asyncio
async def test_step_09_falls_back_to_manual_adjustment_for_unknown_channel(state_with_phase1, env):
    pg = _RecordedPgConn()
    mongo = _MotorClient({
        "levy_payments": [
            {
                "_id": "pay_x",
                "building_id": BUILDING_ID,
                "unit_number": UNIT_NUMBER,
                "amount": 1.00,
                "payment_method": "MysteryCoin",  # unmapped → manual_adjustment
                "payment_date": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "is_test_data": False,
            },
        ],
    })
    mig = Migration13195(mongo, pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_09_receipts()

    inserts = [c for c in pg.executed if "INTO finance.receipts" in c[0]]
    assert len(inserts) == 1
    assert inserts[0][1][5] == "manual_adjustment"


# --------------------------------------------------------------- step 10

@pytest.mark.asyncio
async def test_step_10_fifo_allocates_oldest_first_then_advance(state_with_phase1, env):
    """FIFO: a $2000 receipt should pay off the $1500 oldest item, then $500
    against the next item; surplus goes to 'advance' on the most recent item.
    """
    pg = _RecordedPgConn()
    lot_id = state_with_phase1.unit_map[UNIT_NUMBER]
    receipt_id_a = make_id("receipt", BUILDING_ID, "pay_a")
    item_old = make_id("levy_item", BUILDING_ID, "2024", "annual", UNIT_NUMBER, "admin")
    item_mid = make_id("levy_item", BUILDING_ID, "2025", "annual", UNIT_NUMBER, "admin")
    item_new = make_id("levy_item", BUILDING_ID, "2026", "annual", UNIT_NUMBER, "admin")

    # Register most-specific keys first — _RecordedPgConn.fetch matches by
    # substring in dict insertion order.
    pg.fetch_responses["SELECT DISTINCT lot_id"] = [{"lot_id": lot_id}]
    pg.fetch_responses["FROM finance.receipts"] = [
        {"receipt_id": receipt_id_a, "received_on": date(2026, 1, 5), "amount_cents": 200000},
    ]
    pg.fetch_responses["FROM finance.levy_items"] = [
        {"levy_item_id": item_old, "fund_id": state_with_phase1.admin_fund_id,
         "principal_cents": 150000, "paid_cents": 0, "issue_date": date(2024, 7, 1)},
        {"levy_item_id": item_mid, "fund_id": state_with_phase1.admin_fund_id,
         "principal_cents": 150000, "paid_cents": 100000, "issue_date": date(2025, 7, 1)},
        {"levy_item_id": item_new, "fund_id": state_with_phase1.admin_fund_id,
         "principal_cents": 150000, "paid_cents": 150000, "issue_date": date(2026, 7, 1)},
    ]

    mig = Migration13195(_MotorClient({}), pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_10_receipt_allocations()

    inserts = [c for c in pg.executed if "INTO finance.receipt_allocations" in c[0]]
    assert len(inserts) == 2  # 150000 + 50000 — fully consumed, no advance

    # allocation_type is hardcoded into the SQL, not a placeholder — distinguish
    # 'levy_payment' vs 'advance' by inspecting the SQL fragment.
    levy_payment_inserts = [c for c in inserts if "'levy_payment'" in c[0]]
    advance_inserts = [c for c in inserts if "'advance'" in c[0]]

    # args layout: ($1=allocation_id, $2=tenant_id, $3=receipt_id, $4=levy_item_id, $5=allocated_cents)
    # Index [4] holds allocated_cents (0-based from the args tuple).
    assert sorted(c[1][4] for c in levy_payment_inserts) == [50000, 150000]

    # No surplus: receipt was 200000c, items had 200000c open (150k + 50k unpaid)
    assert advance_inserts == []

    assert mig._stats["receipt_allocations"] == 2


@pytest.mark.asyncio
async def test_step_10_records_advance_when_receipt_exceeds_open(state_with_phase1, env):
    pg = _RecordedPgConn()
    lot_id = state_with_phase1.unit_map[UNIT_NUMBER]
    receipt_id = make_id("receipt", BUILDING_ID, "pay_overpay")
    paid_item = make_id("levy_item", BUILDING_ID, "2025", "annual", UNIT_NUMBER, "admin")

    pg.fetch_responses["SELECT DISTINCT lot_id"] = [{"lot_id": lot_id}]
    pg.fetch_responses["FROM finance.receipts"] = [
        {"receipt_id": receipt_id, "received_on": date(2026, 1, 5), "amount_cents": 100000},
    ]
    pg.fetch_responses["FROM finance.levy_items"] = [
        {"levy_item_id": paid_item, "fund_id": state_with_phase1.admin_fund_id,
         "principal_cents": 150000, "paid_cents": 150000, "issue_date": date(2025, 7, 1)},
    ]

    mig = Migration13195(_MotorClient({}), pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_10_receipt_allocations()

    inserts = [c for c in pg.executed if "INTO finance.receipt_allocations" in c[0]]
    assert len(inserts) == 1
    # SQL hardcodes 'advance' for the surplus path.
    # args layout: ($1=allocation_id, $2=tenant_id, $3=receipt_id, $4=levy_item_id, $5=allocated_cents)
    # Index [4] holds allocated_cents (0-based from the args tuple).
    assert "'advance'" in inserts[0][0]
    assert inserts[0][1][4] == 100000


# --------------------------------------------------------------- step 11

@pytest.mark.asyncio
async def test_step_11_chains_journal_entries_chronologically(state_with_phase1, env):
    pg = _RecordedPgConn()
    levy_item_id = make_id("levy_item", BUILDING_ID, "2025", "annual", UNIT_NUMBER, "admin")
    receipt_id = make_id("receipt", BUILDING_ID, "pay_a")
    lot_id = state_with_phase1.unit_map[UNIT_NUMBER]

    pg.fetch_responses["FROM finance.levy_items"] = [
        {"levy_item_id": levy_item_id, "lot_id": lot_id,
         "fund_id": state_with_phase1.admin_fund_id, "principal_cents": 150000,
         "effective_on": date(2025, 7, 1), "financial_year": "2025"},
    ]
    pg.fetch_responses["FROM finance.receipts"] = [
        {"receipt_id": receipt_id, "lot_id": lot_id, "amount_cents": 150000,
         "received_on": date(2025, 9, 15), "external_reference": "REF-A"},
    ]

    mig = Migration13195(_MotorClient({}), pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_11_journal_entries_and_lines()

    journal_inserts = [c for c in pg.executed if "INTO finance.journal_entries" in c[0]]
    line_inserts = [c for c in pg.executed if "INTO finance.journal_lines" in c[0]]
    receipt_updates = [c for c in pg.executed if "UPDATE finance.receipts" in c[0]]

    assert len(journal_inserts) == 2  # 1 levy_charge + 1 receipt
    assert len(line_inserts) == 4  # 2 lines per entry × 2 entries
    assert len(receipt_updates) == 2  # clear (idempotent reset) + back-fill journal_entry_id on receipt

    # Hash chain: first entry has prev_entry_hash=None, second references first
    first_args = journal_inserts[0][1]
    second_args = journal_inserts[1][1]
    # arg index 7 = prev_entry_hash, 8 = entry_hash
    assert first_args[7] is None
    assert first_args[8] is not None
    assert second_args[7] == first_args[8]
    assert second_args[8] != first_args[8]

    assert mig._stats["journal_entries"] == 2


@pytest.mark.asyncio
async def test_step_11_returns_zero_when_gl_accounts_missing(state_with_phase1, env):
    state_with_phase1.gl_map.pop("4001")  # missing CW income
    pg = _RecordedPgConn()
    mig = Migration13195(_MotorClient({}), pg, dry_run=False)
    mig.state = state_with_phase1

    await mig._step_11_journal_entries_and_lines()

    assert not any("INTO finance.journal_entries" in c[0] for c in pg.executed)
    assert mig._stats["journal_entries"] == 0


@pytest.mark.asyncio
async def test_step_11_dry_run_writes_nothing(state_with_phase1, env):
    pg = _RecordedPgConn()
    mig = Migration13195(_MotorClient({}), pg, dry_run=True)
    mig.state = state_with_phase1

    await mig._step_11_journal_entries_and_lines()

    assert pg.executed == []
    assert mig._stats["journal_entries"] == 0


# --------------------------------------------------------------- determinism

def test_make_id_is_deterministic_across_runs():
    """Re-running the migration must produce identical UUIDs so ON CONFLICT
    DO NOTHING wins. This locks the deterministic-ID invariant."""
    a = make_id("levy_run", BUILDING_ID, "2025", "annual")
    b = make_id("levy_run", BUILDING_ID, "2025", "annual")
    assert a == b
    # Different inputs yield different IDs
    c = make_id("levy_run", BUILDING_ID, "2026", "annual")
    assert a != c


def test_cents_handles_dollars_floats_and_strings():
    assert cents(1500) == 150000
    assert cents(1500.50) == 150050
    assert cents("125.99") == 12599
    assert cents(None) == 0
    assert cents("") == 0
    assert cents("not-a-number") == 0
