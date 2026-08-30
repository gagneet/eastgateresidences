from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


BACKEND_PATH = str(Path(__file__).resolve().parents[2] / "backend")
if BACKEND_PATH not in sys.path:
    sys.path.insert(0, BACKEND_PATH)
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("ENCRYPTION_KEY", "uJw9lYkG9btQfIp7q0M6vFlxnPVO92jYbP4P5Ih6QqQ=")

from migrations.postgres.finance_migration import FinanceMigration
from migrations.postgres.framework.reporting import MigrationReportWriter
from migrations.postgres.framework.runtime import MigrationContext, MigrationOptions
from migrations.postgres.trust_migration import AdjustmentRequirements, TrustMigration


class FakeMongoClient:
    def __getitem__(self, _name: str) -> "FakeMongoDb":
        return FakeMongoDb()

    def close(self) -> None:
        return None


class FakeMongoDb:
    def __getitem__(self, _name: str) -> "FakeMongoCollection":
        return FakeMongoCollection()


class FakeMongoCollection:
    def find(self, *_args, **_kwargs):
        return self

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, *_args, **_kwargs):
        return []


class FakePgAdapter:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}

    async def ensure_row(self, *, table, row, pk_col, conflict_cols, casts=None):
        rows = self.tables.setdefault(table, [])
        for existing in rows:
            if all(existing.get(col) == row.get(col) for col in conflict_cols):
                return existing[pk_col], False
        rows.append(dict(row))
        return row[pk_col], True

    async def fetchval(self, query, *args):
        if "SELECT entry_hash FROM finance.journal_entries WHERE journal_entry_id = $1" in query:
            for row in self.tables.get("finance.journal_entries", []):
                if row["journal_entry_id"] == args[0]:
                    return row.get("entry_hash")
            return None
        if "ORDER BY entry_number DESC" in query:
            fund_rows = [row for row in self.tables.get("finance.journal_entries", []) if row.get("fund_id") == args[1]]
            return fund_rows[-1].get("entry_hash") if fund_rows else None
        if "AND jl.gl_account_id = $4" in query:
            scheme_id, fund_id, script_name, bank_gl = args
            total = 0
            entries = {
                row["journal_entry_id"]: row
                for row in self.tables.get("finance.journal_entries", [])
                if row["scheme_id"] == scheme_id
                and row["fund_id"] == fund_id
                and row.get("metadata", {}).get("script") == script_name
            }
            for line in self.tables.get("finance.journal_lines", []):
                if line["journal_entry_id"] not in entries or line["gl_account_id"] != bank_gl:
                    continue
                total += line["amount_cents"] if line["direction"] == "debit" else -line["amount_cents"]
            return total
        raise AssertionError(f"Unhandled fetchval query: {query}")

    async def execute(self, *_args, **_kwargs):
        return None

    def transaction(self):
        return _NullAsyncContext()


class _NullAsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
async def test_finance_levy_batch_rerun_skips_duplicates(tmp_path):
    pg = FakePgAdapter()
    options = MigrationOptions(
        building_id="UP-TEST-001",
        mongo_dsn="mongodb://example",
        mongo_db_name="testdb",
        pg_dsn="postgresql://example",
        output_dir=tmp_path / "runtime-1",
        checkpoint_dir=tmp_path / "checkpoints-1",
    )
    migration = FinanceMigration(options=options, mongo_client=FakeMongoClient(), pg=pg)
    migration.context = MigrationContext(
        tenant_id=uuid.uuid4(),
        scheme_id=uuid.uuid4(),
        scheme_number="UP-TEST-001",
        building_id="UP-TEST-001",
        lot_by_unit_number={"1": uuid.uuid4()},
        owner_by_lot_id={},
        fund_ids={"admin": uuid.uuid4(), "capital_works": uuid.uuid4()},
    )
    lot_id = migration.context.lot_by_unit_number["1"]
    migration.context.owner_by_lot_id[lot_id] = uuid.uuid4()
    migration.checkpoints.start_run(
        run_id=options.run_id,
        script=migration.script_name,
        building_id=options.building_id,
        dry_run=False,
    )

    async def fake_ensure_period(**_kwargs):
        return uuid.uuid4()

    migration.ensure_period = fake_ensure_period  # type: ignore[assignment]
    row = {
        "_id": "ledger-1",
        "year": "2026",
        "unit_number": "1",
        "admin_levied": 125000,
        "admin_paid": 25000,
        "sinking_levied": 50000,
        "sinking_paid": 0,
    }
    await migration._process_levy_batch([row], batch_index=0, transactional_pg=pg)

    assert migration.report.table_counts["migrated"]["finance.levy_items"] == 2
    assert len(pg.tables["finance.levy_items"]) == 2

    rerun = FinanceMigration(
        options=MigrationOptions(
            building_id="UP-TEST-001",
            mongo_dsn="mongodb://example",
            mongo_db_name="testdb",
            pg_dsn="postgresql://example",
            output_dir=tmp_path / "runtime-2",
            checkpoint_dir=tmp_path / "checkpoints-2",
        ),
        mongo_client=FakeMongoClient(),
        pg=pg,
    )
    rerun.context = migration.context
    rerun.checkpoints.start_run(
        run_id=rerun.options.run_id,
        script=rerun.script_name,
        building_id=rerun.options.building_id,
        dry_run=False,
    )
    rerun.ensure_period = fake_ensure_period  # type: ignore[assignment]
    await rerun._process_levy_batch([row], batch_index=0, transactional_pg=pg)

    assert rerun.report.table_counts["skipped"]["finance.levy_items"] == 2
    assert len(pg.tables["finance.levy_items"]) == 2


@pytest.mark.asyncio
async def test_finance_validate_only_skips_mutations(tmp_path):
    pg = FakePgAdapter()
    options = MigrationOptions(
        building_id="UP-TEST-001",
        mongo_dsn="mongodb://example",
        mongo_db_name="testdb",
        pg_dsn="postgresql://example",
        validate_only=True,
        output_dir=tmp_path / "runtime-validate",
        checkpoint_dir=tmp_path / "checkpoints-validate",
    )
    migration = FinanceMigration(options=options, mongo_client=FakeMongoClient(), pg=pg)
    migration.prepare = AsyncMock(return_value=None)  # type: ignore[assignment]
    migration._load_existing_reference_data = AsyncMock(return_value=None)  # type: ignore[assignment]
    migration._validate = AsyncMock(return_value=None)  # type: ignore[assignment]
    migration._raise_on_validation_issues = MagicMock(return_value=None)  # type: ignore[assignment]
    migration._ensure_reference_data = AsyncMock(side_effect=AssertionError("validate-only must not mutate"))  # type: ignore[assignment]
    migration._migrate_levy_runs_and_items = AsyncMock(side_effect=AssertionError("validate-only must not mutate"))  # type: ignore[assignment]
    migration._migrate_receipts = AsyncMock(side_effect=AssertionError("validate-only must not mutate"))  # type: ignore[assignment]
    migration._allocate_receipts = AsyncMock(side_effect=AssertionError("validate-only must not mutate"))  # type: ignore[assignment]
    migration._derive_journals = AsyncMock(side_effect=AssertionError("validate-only must not mutate"))  # type: ignore[assignment]
    migration.checkpoints.start_run = MagicMock(return_value=None)  # type: ignore[assignment]
    migration.checkpoints.finish_run = MagicMock(return_value=None)  # type: ignore[assignment]

    await migration.run()

    assert migration.report.table_counts == {}
    assert pg.tables == {}


@pytest.mark.asyncio
async def test_finance_resolves_source_trust_account_id(tmp_path):
    pg = FakePgAdapter()
    migration = FinanceMigration(
        options=MigrationOptions(
            building_id="UP-TEST-001",
            mongo_dsn="mongodb://example",
            mongo_db_name="testdb",
            pg_dsn="postgresql://example",
            output_dir=tmp_path / "runtime-resolve",
            checkpoint_dir=tmp_path / "checkpoints-resolve",
        ),
        mongo_client=FakeMongoClient(),
        pg=pg,
    )
    migration.context = MigrationContext(
        tenant_id=uuid.uuid4(),
        scheme_id=uuid.uuid4(),
        scheme_number="UP-TEST-001",
        building_id="UP-TEST-001",
        trust_accounts={"source-account-id": uuid.uuid4()},
    )

    assert migration.resolve_trust_account_id({"trust_account_id": "source-account-id"}) == migration.context.trust_accounts["source-account-id"]


@pytest.mark.asyncio
async def test_trust_adjustment_requires_reason_and_records_adjustment(tmp_path):
    pg = FakePgAdapter()
    options = MigrationOptions(
        building_id="UP-TEST-001",
        mongo_dsn="mongodb://example",
        mongo_db_name="testdb",
        pg_dsn="postgresql://example",
        output_dir=tmp_path / "runtime",
        checkpoint_dir=tmp_path / "checkpoints",
    )
    migration = TrustMigration(
        options=options,
        mongo_client=FakeMongoClient(),
        pg=pg,
        adjustments=AdjustmentRequirements(
            reason_code="UNRECONCILED_IMPORT_BALANCE",
            operator_note="Legacy trust history is incomplete; applying explicit restatement.",
            evidence_reference="bank-statement-2026-05.pdf",
        ),
    )
    fund_id = uuid.uuid4()
    bank_gl = uuid.uuid4()
    clearing_gl = uuid.uuid4()
    migration.context = MigrationContext(
        tenant_id=uuid.uuid4(),
        scheme_id=uuid.uuid4(),
        scheme_number="UP-TEST-001",
        building_id="UP-TEST-001",
        fund_ids={"admin": fund_id},
        gl_accounts={"1010": bank_gl, "3100": clearing_gl, "1011": uuid.uuid4()},
    )
    migration.account_map = {
        "acct-1": {
            "fund_id": fund_id,
            "fund_key": "admin",
            "opening_balance_cents": 10000,
            "current_balance_cents": 15000,
            "last_reconciled_balance_cents": 15000,
        }
    }
    migration.checkpoints.start_run(
        run_id=options.run_id,
        script=migration.script_name,
        building_id=options.building_id,
        dry_run=False,
    )

    async def fake_ensure_period(**_kwargs):
        return uuid.uuid4()

    migration.ensure_period = fake_ensure_period  # type: ignore[assignment]
    await migration._post_balance_adjustments()

    assert len(pg.tables["finance.journal_entries"]) == 1
    entry = pg.tables["finance.journal_entries"][0]
    assert entry["source_type"] == "migration_adjustment"
    assert entry["metadata"]["reason_code"] == "UNRECONCILED_IMPORT_BALANCE"
    assert entry["metadata"]["operator_note"].startswith("Legacy trust history is incomplete")
    assert entry["metadata"]["evidence_reference"] == "bank-statement-2026-05.pdf"
    assert migration.report.table_counts["adjusted"]["finance.journal_entries"] == 1

    rerun = TrustMigration(
        options=MigrationOptions(
            building_id="UP-TEST-001",
            mongo_dsn="mongodb://example",
            mongo_db_name="testdb",
            pg_dsn="postgresql://example",
            output_dir=tmp_path / "runtime-rerun",
            checkpoint_dir=tmp_path / "checkpoints-rerun",
        ),
        mongo_client=FakeMongoClient(),
        pg=pg,
        adjustments=migration.adjustments,
    )
    rerun.context = migration.context
    rerun.account_map = migration.account_map
    rerun.checkpoints.start_run(
        run_id=rerun.options.run_id,
        script=rerun.script_name,
        building_id=rerun.options.building_id,
        dry_run=False,
    )
    rerun.ensure_period = fake_ensure_period  # type: ignore[assignment]
    await rerun._post_balance_adjustments()

    assert len(pg.tables["finance.journal_entries"]) == 1
    assert rerun.report.table_counts.get("adjusted", {}).get("finance.journal_entries", 0) == 0


@pytest.mark.asyncio
async def test_trust_validate_only_skips_mutations(tmp_path):
    pg = FakePgAdapter()
    options = MigrationOptions(
        building_id="UP-TEST-001",
        mongo_dsn="mongodb://example",
        mongo_db_name="testdb",
        pg_dsn="postgresql://example",
        validate_only=True,
        output_dir=tmp_path / "runtime-trust-validate",
        checkpoint_dir=tmp_path / "checkpoints-trust-validate",
    )
    migration = TrustMigration(
        options=options,
        mongo_client=FakeMongoClient(),
        pg=pg,
        adjustments=AdjustmentRequirements(
            reason_code="UNRECONCILED_IMPORT_BALANCE",
            operator_note="note",
            evidence_reference="evidence",
        ),
    )
    migration.prepare = AsyncMock(return_value=None)  # type: ignore[assignment]
    migration._load_existing_reference_data = AsyncMock(return_value=None)  # type: ignore[assignment]
    migration._load_existing_account_map = AsyncMock(return_value=None)  # type: ignore[assignment]
    migration._validate = AsyncMock(return_value=None)  # type: ignore[assignment]
    migration._raise_on_validation_issues = MagicMock(return_value=None)  # type: ignore[assignment]
    migration._ensure_reference_data = AsyncMock(side_effect=AssertionError("validate-only must not mutate"))  # type: ignore[assignment]
    migration._migrate_trust_accounts = AsyncMock(side_effect=AssertionError("validate-only must not mutate"))  # type: ignore[assignment]
    migration._migrate_bank_transactions = AsyncMock(side_effect=AssertionError("validate-only must not mutate"))  # type: ignore[assignment]
    migration._migrate_trust_journals = AsyncMock(side_effect=AssertionError("validate-only must not mutate"))  # type: ignore[assignment]
    migration._post_balance_adjustments = AsyncMock(side_effect=AssertionError("validate-only must not mutate"))  # type: ignore[assignment]
    migration.checkpoints.start_run = MagicMock(return_value=None)  # type: ignore[assignment]
    migration.checkpoints.finish_run = MagicMock(return_value=None)  # type: ignore[assignment]

    await migration.run()

    assert migration.report.table_counts == {}
    assert pg.tables == {}


from datetime import date as _date


def test_year_bounds_jan_dec_fy():
    """fy_start_month=1 → FY 2026 is 2026-01-01 to 2026-12-31 (East Gate)."""
    from migrations.postgres.finance_migration import _year_bounds
    start, end = _year_bounds("2026", fy_start_month=1)
    assert start == _date(2026, 1, 1)
    assert end == _date(2026, 12, 31)


def test_year_bounds_jul_jun_fy():
    """fy_start_month=7 → FY 2026 is 2026-07-01 to 2027-06-30 (ATO standard)."""
    from migrations.postgres.finance_migration import _year_bounds
    start, end = _year_bounds("2026", fy_start_month=7)
    assert start == _date(2026, 7, 1)
    assert end == _date(2027, 6, 30)


def test_fy_for_date_jan_dec():
    """fy_start_month=1 → every date in a calendar year maps to that year."""
    from migrations.postgres.finance_migration import _fy_for_date
    assert _fy_for_date(_date(2026, 1, 1), fy_start_month=1) == "2026"
    assert _fy_for_date(_date(2026, 6, 30), fy_start_month=1) == "2026"
    assert _fy_for_date(_date(2026, 12, 31), fy_start_month=1) == "2026"


def test_fy_for_date_jul_jun():
    """fy_start_month=7 → dates before July roll to the prior FY year label."""
    from migrations.postgres.finance_migration import _fy_for_date
    assert _fy_for_date(_date(2026, 3, 15), fy_start_month=7) == "2025"
    assert _fy_for_date(_date(2026, 7, 1), fy_start_month=7) == "2026"
    assert _fy_for_date(_date(2027, 6, 30), fy_start_month=7) == "2026"


def test_migration_options_fy_start_month_default():
    """MigrationOptions defaults to fy_start_month=7 (ATO standard)."""
    from migrations.postgres.framework.runtime import MigrationOptions
    opts = MigrationOptions(building_id="UP-TEST-001")
    assert opts.fy_start_month == 7


def test_migration_options_fy_start_month_east_gate():
    """East Gate migrations must be constructed with fy_start_month=1."""
    from migrations.postgres.framework.runtime import MigrationOptions
    opts = MigrationOptions(building_id="13195", fy_start_month=1)
    assert opts.fy_start_month == 1


def test_report_writer_separates_variances_failures_and_unresolved_rows(tmp_path):
    writer = MigrationReportWriter(
        script_name="finance_migration",
        building_id="UP-TEST-001",
        run_id="run-123",
        output_dir=tmp_path,
        dry_run=True,
    )
    writer.record_trace(
        status="migrated",
        target_table="finance.levy_items",
        target_id="abc",
        source_collection="unit_levy_ledger",
        source_id="mongo-1",
        phase="levies",
    )
    writer.add_variance(name="trial_balance", expected_cents=100, actual_cents=100)
    writer.add_failure(
        phase="receipts",
        source_collection="levy_payments",
        source_id="pay-1",
        error="boom",
    )
    writer.add_unresolved(
        phase="levies",
        source_collection="unit_levy_ledger",
        source_id="mongo-2",
        reason="missing_owner_party",
    )

    payload = writer.finalize(status="completed")

    assert payload["table_counts"]["migrated"]["finance.levy_items"] == 1
    assert payload["variances"][0]["variance_cents"] == 0
    assert payload["failures"][0]["source_collection"] == "levy_payments"
    assert payload["unresolved_rows"][0]["reason"] == "missing_owner_party"
