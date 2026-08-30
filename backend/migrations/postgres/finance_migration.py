from __future__ import annotations

import argparse
import asyncio
import calendar
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from motor.motor_asyncio import AsyncIOMotorClient

from migrations.postgres.framework import AsyncpgMigrationAdapter, BaseBuildingMigration, MigrationOptions


FUND_SPECS = (
    ("ADM", "Administration Fund", "admin"),
    ("CWF", "Capital Works Fund", "capital_works"),
)

GL_SPECS = (
    ("1010", "Trust Bank Account - Admin", "asset", True, "admin"),
    ("1011", "Trust Bank Account - Capital Works", "asset", True, "capital_works"),
    ("1100", "Levy Receivable", "asset", True, None),
    ("3100", "Opening Balance Clearing", "equity", False, None),
    ("4000", "Admin Fund Levy Income", "income", False, "admin"),
    ("4001", "Capital Works Levy Income", "income", False, "capital_works"),
)


def _year_bounds(financial_year: str, fy_start_month: int = 7) -> tuple[date, date]:
    """Return (start, end) dates for a financial year label.

    fy_start_month=1  → Jan–Dec  (East Gate: UP13195)
    fy_start_month=7  → Jul–Jun  (ATO standard, default)
    """
    year = int(str(financial_year).split("-")[0])
    start = date(year, fy_start_month, 1)
    if fy_start_month == 1:
        end = date(year, 12, 31)
    else:
        end_month = fy_start_month - 1
        end_year = year + 1
        end_day = calendar.monthrange(end_year, end_month)[1]
        end = date(end_year, end_month, end_day)
    return start, end


def _fy_for_date(value: date, fy_start_month: int = 7) -> str:
    """Return the financial year label that contains *value*.

    fy_start_month=1 → every calendar date maps to its own year (Jan–Dec).
    fy_start_month=7 → dates before July roll back one year (Jul–Jun).
    """
    return str(value.year if value.month >= fy_start_month else value.year - 1)


def _compute_entry_hash(entry_id: str, narration: str, prev_hash: str | None) -> str:
    """Generated function header.

    Function: _compute_entry_hash
    Path: backend/migrations/postgres/finance_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = json.dumps(
        {"id": entry_id, "narration": narration, "prev": prev_hash or ""},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class ReceiptAllocationState:
    receipt_id: Any
    remaining_cents: int


class FinanceMigration(BaseBuildingMigration):
    script_name = "finance_migration"

    def __init__(self, *, options: MigrationOptions, mongo_client: AsyncIOMotorClient, pg: AsyncpgMigrationAdapter) -> None:
        """Generated function header.

        Function: FinanceMigration.__init__
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        super().__init__(options=options, mongo_client=mongo_client, pg=pg)
        self.levy_source_by_item_id: dict[str, dict[str, Any]] = {}
        self.receipt_source_by_id: dict[str, dict[str, Any]] = {}

    async def run(self) -> dict[str, Any]:
        """Generated function header.

        Function: FinanceMigration.run
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            await self.prepare()
            if self.options.validate_only:
                await self._load_existing_reference_data()
                await self._validate()
                self._raise_on_validation_issues()
                return await self.finalize(status="completed")
            await self._ensure_reference_data()
            await self._migrate_levy_runs_and_items()
            await self._migrate_receipts()
            await self._allocate_receipts()
            await self._derive_journals()
            await self._validate()
            self._raise_on_validation_issues()
            return await self.finalize(status="completed")
        except Exception:
            await self.finalize(status="failed")
            raise

    async def _load_existing_reference_data(self) -> None:
        """Generated function header.

        Function: FinanceMigration._load_existing_reference_data
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        await self.load_fund_ids()
        await self.load_gl_accounts()
        await self.load_trust_account_map()
        if not self.context.fund_ids:
            raise RuntimeError("No finance.funds rows exist for this scheme")
        if not self.context.gl_accounts:
            raise RuntimeError("No finance.gl_accounts rows exist for this scheme")

    async def _ensure_reference_data(self) -> None:
        """Generated function header.

        Function: FinanceMigration._ensure_reference_data
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None

        for code, name, fund_type in FUND_SPECS:
            fund_id, inserted = await self.pg.ensure_row(
                table="finance.funds",
                pk_col="fund_id",
                conflict_cols=("scheme_id", "fund_code"),
                casts={"fund_type": "finance.fund_type", "status": "core.record_status"},
                row={
                    "fund_id": self.deterministic_id("fund", fund_type),
                    "tenant_id": self.context.tenant_id,
                    "scheme_id": self.context.scheme_id,
                    "fund_code": code,
                    "fund_name": name,
                    "fund_type": fund_type,
                    "status": "active",
                },
            )
            self.report.record_trace(
                status="migrated" if inserted else "skipped",
                target_table="finance.funds",
                target_id=str(fund_id),
                source_collection="virtual.funds",
                source_id=fund_type,
                phase="reference_data",
            )

        await self.load_fund_ids()
        await self.load_gl_accounts()
        await self.load_trust_account_map()

        for code, name, account_type, is_control, fund_type in GL_SPECS:
            fund_id = self.context.fund_ids.get(fund_type) if fund_type else None
            gl_id, inserted = await self.pg.ensure_row(
                table="finance.gl_accounts",
                pk_col="gl_account_id",
                conflict_cols=("scheme_id", "account_code"),
                casts={"account_type": "finance.account_type", "status": "core.record_status"},
                row={
                    "gl_account_id": self.deterministic_id("gl", code),
                    "tenant_id": self.context.tenant_id,
                    "scheme_id": self.context.scheme_id,
                    "fund_id": fund_id,
                    "account_code": code,
                    "account_name": name,
                    "account_type": account_type,
                    "is_control_account": is_control,
                    "status": "active",
                },
            )
            self.report.record_trace(
                status="migrated" if inserted else "skipped",
                target_table="finance.gl_accounts",
                target_id=str(gl_id),
                source_collection="virtual.gl_accounts",
                source_id=code,
                phase="reference_data",
            )

        await self.load_gl_accounts()

    async def _migrate_levy_runs_and_items(self) -> None:
        """Generated function header.

        Function: FinanceMigration._migrate_levy_runs_and_items
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        phase = "levy_runs_and_items"

        async for batch_index, rows in self.iter_collection_batches(
            collection_name="unit_levy_ledger",
            query={"building_id": self.options.building_id, "is_test_data": {"$ne": True}},
            phase=phase,
        ):
            if self.options.dry_run:
                await self._process_levy_batch(rows, batch_index, None)
                continue
            async with self.pg.transaction():
                await self._process_levy_batch(rows, batch_index, self.pg)

    async def _process_levy_batch(
        self,
        rows: list[dict[str, Any]],
        batch_index: int,
        transactional_pg: AsyncpgMigrationAdapter | None,
    ) -> None:
        """Generated function header.

        Function: FinanceMigration._process_levy_batch
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        pg = transactional_pg or self.pg
        last_token: str | None = None
        batch_stats = {"rows": len(rows)}

        for row in rows:
            source_id = self.source_token(row, "year", "unit_number")
            last_token = source_id
            year = str(row.get("year") or "").strip()
            if not year:
                self.report.add_unresolved(
                    phase="levy_runs_and_items",
                    source_collection="unit_levy_ledger",
                    source_id=source_id,
                    reason="missing_year",
                )
                continue
            lot_id = await self.resolve_lot_id(row)
            if lot_id is None:
                self.report.add_unresolved(
                    phase="levy_runs_and_items",
                    source_collection="unit_levy_ledger",
                    source_id=source_id,
                    reason="missing_lot_match",
                    metadata={"unit_number": row.get("unit_number"), "lot_number": row.get("lot_number")},
                )
                continue
            owner_party_id = self.context.owner_by_lot_id.get(lot_id)
            if owner_party_id is None:
                self.report.add_unresolved(
                    phase="levy_runs_and_items",
                    source_collection="unit_levy_ledger",
                    source_id=source_id,
                    reason="missing_owner_party",
                    metadata={"lot_id": str(lot_id)},
                )
                continue

            starts_on, ends_on = _year_bounds(year, self.options.fy_start_month)
            await self.ensure_period(label=f"FY {year}", starts_on=starts_on, ends_on=ends_on)
            run_id, run_inserted = await pg.ensure_row(
                table="finance.levy_runs",
                pk_col="levy_run_id",
                conflict_cols=("levy_run_id",),
                row={
                    "levy_run_id": self.deterministic_id("levy_run", year, "annual"),
                    "tenant_id": self.context.tenant_id,
                    "scheme_id": self.context.scheme_id,
                    "financial_year": year,
                    "quarter_no": 0,
                    "issue_date": starts_on,
                    "due_date": starts_on,
                    "status": "approved",
                },
            )
            self.report.record_trace(
                status="migrated" if run_inserted else "skipped",
                target_table="finance.levy_runs",
                target_id=str(run_id),
                source_collection="unit_levy_ledger",
                source_id=source_id,
                phase="levy_runs_and_items",
                metadata={"financial_year": year},
            )

            unit_number = str(row.get("unit_number") or row.get("lot_number") or "").strip()
            fund_map = (
                ("admin", "admin_levied", "admin_paid", self.context.fund_ids.get("admin")),
                (
                    "capital_works",
                    "sinking_levied",
                    "sinking_paid",
                    self.context.fund_ids.get("capital_works") or self.context.fund_ids.get("sinking"),
                ),
            )
            for fund_key, principal_field, paid_field, fund_id in fund_map:
                principal_cents = self.coerce_cents(row.get(principal_field))
                paid_cents = max(0, self.coerce_cents(row.get(paid_field)))
                if principal_cents <= 0 or fund_id is None:
                    continue
                item_id, inserted = await pg.ensure_row(
                    table="finance.levy_items",
                    pk_col="levy_item_id",
                    conflict_cols=("levy_run_id", "lot_id", "fund_id"),
                    row={
                        "levy_item_id": self.deterministic_id("levy_item", year, unit_number, fund_key),
                        "tenant_id": self.context.tenant_id,
                        "scheme_id": self.context.scheme_id,
                        "levy_run_id": run_id,
                        "lot_id": lot_id,
                        "owner_party_id": owner_party_id,
                        "fund_id": fund_id,
                        "principal_cents": principal_cents,
                        "gst_cents": 0,
                        "interest_cents": 0,
                        "recovery_costs_cents": 0,
                        "paid_cents": min(paid_cents, principal_cents),
                        "status": "paid" if paid_cents >= principal_cents else "issued",
                    },
                )
                self.levy_source_by_item_id[str(item_id)] = {
                    "source_collection": "unit_levy_ledger",
                    "source_id": source_id,
                    "financial_year": year,
                    "fund_key": fund_key,
                }
                self.report.record_trace(
                    status="migrated" if inserted else "skipped",
                    target_table="finance.levy_items",
                    target_id=str(item_id),
                    source_collection="unit_levy_ledger",
                    source_id=source_id,
                    phase="levy_runs_and_items",
                    metadata={"financial_year": year, "fund_key": fund_key},
                )

        self.checkpoints.mark_phase(
            run_id=self.options.run_id,
            phase="levy_runs_and_items",
            batch_index=batch_index,
            status="committed",
            last_source_token=last_token,
            stats=batch_stats,
        )

    async def _migrate_receipts(self) -> None:
        """Generated function header.

        Function: FinanceMigration._migrate_receipts
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        phase = "receipts"
        valid_channels = {"bpay", "deft", "eft", "card", "cash", "cheque", "bank_transfer", "aba", "manual_adjustment"}
        channel_map = {
            "credit_card": "card",
            "deft_bpay": "deft",
            "bank_transfer": "bank_transfer",
            "direct_deposit": "eft",
            "stripe": "card",
        }
        async for batch_index, rows in self.iter_collection_batches(
            collection_name="levy_payments",
            query={
                "building_id": self.options.building_id,
                "status": {"$in": ["confirmed", "completed", "paid", None]},
                "is_test_data": {"$ne": True},
            },
            phase=phase,
        ):
            last_token: str | None = None
            if self.options.dry_run:
                transactional_pg = None
            else:
                transactional_pg = self.pg
            context_manager = transactional_pg.transaction() if transactional_pg else _NullAsyncContext()
            async with context_manager:
                for row in rows:
                    source_id = self.source_token(row, "id", "reference")
                    last_token = source_id
                    amount_cents = self.coerce_cents(row.get("amount_cents"))
                    if amount_cents <= 0:
                        amount_cents = self.coerce_cents(row.get("amount"))
                    if amount_cents <= 0:
                        self.report.add_unresolved(
                            phase=phase,
                            source_collection="levy_payments",
                            source_id=source_id,
                            reason="non_positive_amount",
                        )
                        continue

                    lot_id = await self.resolve_lot_id(row)
                    if lot_id is None:
                        self.report.add_unresolved(
                            phase=phase,
                            source_collection="levy_payments",
                            source_id=source_id,
                            reason="missing_lot_match",
                        )
                        continue
                    payer_party_id = self.context.owner_by_lot_id.get(lot_id)
                    if payer_party_id is None:
                        self.report.add_unresolved(
                            phase=phase,
                            source_collection="levy_payments",
                            source_id=source_id,
                            reason="missing_owner_party",
                        )
                        continue
                    trust_account_id = self.resolve_trust_account_id(row)
                    if trust_account_id is None:
                        self.report.add_unresolved(
                            phase=phase,
                            source_collection="levy_payments",
                            source_id=source_id,
                            reason="missing_trust_account_match",
                            metadata={"trust_account_id": row.get("trust_account_id")},
                        )
                        continue

                    received_on = self.coerce_date(
                        row.get("payment_date") or row.get("date") or row.get("created_at")
                    )
                    raw_channel = str(row.get("payment_method") or "manual_adjustment").lower().replace(" ", "_")
                    channel = channel_map.get(raw_channel, raw_channel)
                    if channel not in valid_channels:
                        channel = "manual_adjustment"

                    receipt_id, inserted = await self.pg.ensure_row(
                        table="finance.receipts",
                        pk_col="receipt_id",
                        conflict_cols=("receipt_id",),
                        casts={"channel": "finance.payment_channel"},
                        row={
                            "receipt_id": self.deterministic_id("receipt", source_id),
                            "tenant_id": self.context.tenant_id,
                            "scheme_id": self.context.scheme_id,
                            "trust_account_id": trust_account_id,
                            "payer_party_id": payer_party_id,
                            "lot_id": lot_id,
                            "channel": channel,
                            "received_on": received_on,
                            "amount_cents": amount_cents,
                            "external_reference": row.get("receipt_number") or row.get("reference") or source_id,
                        },
                    )
                    self.receipt_source_by_id[str(receipt_id)] = {
                        "source_collection": "levy_payments",
                        "source_id": source_id,
                    }
                    self.report.record_trace(
                        status="migrated" if inserted else "skipped",
                        target_table="finance.receipts",
                        target_id=str(receipt_id),
                        source_collection="levy_payments",
                        source_id=source_id,
                        phase=phase,
                        metadata={"channel": channel},
                    )

            self.checkpoints.mark_phase(
                run_id=self.options.run_id,
                phase=phase,
                batch_index=batch_index,
                status="committed",
                last_source_token=last_token,
                stats={"rows": len(rows)},
            )

    async def _allocate_receipts(self) -> None:
        """Generated function header.

        Function: FinanceMigration._allocate_receipts
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        phase = "receipt_allocations"
        lot_ids = await self.pg.fetch(
            "SELECT DISTINCT lot_id FROM finance.receipts WHERE scheme_id = $1",
            self.context.scheme_id,
        )
        last_token = None
        context_manager = _NullAsyncContext() if self.options.dry_run else self.pg.transaction()
        async with context_manager:
            for batch_index, lot_row in enumerate(lot_ids):
                lot_id = lot_row["lot_id"]
                last_token = str(lot_id)
                receipt_rows = await self.pg.fetch(
                    """
                    SELECT receipt_id, received_on, amount_cents
                    FROM finance.receipts
                    WHERE scheme_id = $1 AND lot_id = $2
                    ORDER BY received_on ASC, receipt_id ASC
                    """,
                    self.context.scheme_id,
                    lot_id,
                )
                item_rows = await self.pg.fetch(
                    """
                    SELECT li.levy_item_id,
                           li.fund_id,
                           li.principal_cents,
                           COALESCE(SUM(ra.allocated_cents) FILTER (WHERE ra.allocation_type = 'levy_payment'), 0) AS already_allocated
                    FROM finance.levy_items li
                    LEFT JOIN finance.receipt_allocations ra ON ra.levy_item_id = li.levy_item_id
                    WHERE li.scheme_id = $1 AND li.lot_id = $2
                    GROUP BY li.levy_item_id
                    ORDER BY li.levy_item_id
                    """,
                    self.context.scheme_id,
                    lot_id,
                )
                items = [
                    {
                        "levy_item_id": row["levy_item_id"],
                        "remaining_cents": max(0, row["principal_cents"] - row["already_allocated"]),
                    }
                    for row in item_rows
                ]
                for receipt in receipt_rows:
                    state = ReceiptAllocationState(receipt_id=receipt["receipt_id"], remaining_cents=receipt["amount_cents"])
                    for item in items:
                        if state.remaining_cents <= 0:
                            break
                        if item["remaining_cents"] <= 0:
                            continue
                        allocated = min(item["remaining_cents"], state.remaining_cents)
                        allocation_id, inserted = await self.pg.ensure_row(
                            table="finance.receipt_allocations",
                            pk_col="allocation_id",
                            conflict_cols=("allocation_id",),
                            row={
                                "allocation_id": self.deterministic_id("allocation", receipt["receipt_id"], item["levy_item_id"]),
                                "receipt_id": receipt["receipt_id"],
                                "levy_item_id": item["levy_item_id"],
                                "allocation_type": "levy_payment",
                                "allocated_cents": allocated,
                            },
                        )
                        source = self.receipt_source_by_id.get(str(receipt["receipt_id"]), {"source_collection": "levy_payments", "source_id": str(receipt["receipt_id"])})
                        self.report.record_trace(
                            status="migrated" if inserted else "skipped",
                            target_table="finance.receipt_allocations",
                            target_id=str(allocation_id),
                            source_collection=source["source_collection"],
                            source_id=source["source_id"],
                            phase=phase,
                            metadata={"levy_item_id": str(item["levy_item_id"])},
                        )
                        item["remaining_cents"] -= allocated
                        state.remaining_cents -= allocated

                    if state.remaining_cents > 0 and items:
                        last_item_id = items[-1]["levy_item_id"]
                        allocation_id, inserted = await self.pg.ensure_row(
                            table="finance.receipt_allocations",
                            pk_col="allocation_id",
                            conflict_cols=("allocation_id",),
                            row={
                                "allocation_id": self.deterministic_id("allocation_advance", receipt["receipt_id"], last_item_id),
                                "receipt_id": receipt["receipt_id"],
                                "levy_item_id": last_item_id,
                                "allocation_type": "advance",
                                "allocated_cents": state.remaining_cents,
                            },
                        )
                        source = self.receipt_source_by_id.get(str(receipt["receipt_id"]), {"source_collection": "levy_payments", "source_id": str(receipt["receipt_id"])})
                        self.report.record_trace(
                            status="adjusted" if inserted else "skipped",
                            target_table="finance.receipt_allocations",
                            target_id=str(allocation_id),
                            source_collection=source["source_collection"],
                            source_id=source["source_id"],
                            phase=phase,
                            metadata={"allocation_type": "advance"},
                        )

                self.checkpoints.mark_phase(
                    run_id=self.options.run_id,
                    phase=phase,
                    batch_index=batch_index,
                    status="committed",
                    last_source_token=last_token,
                    stats={"lot_id": str(lot_id)},
                )

    async def _derive_journals(self) -> None:
        """Generated function header.

        Function: FinanceMigration._derive_journals
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        phase = "journal_derivation"
        period_cache: dict[str, Any] = {}
        gl_map = self.context.gl_accounts
        fund_ids = self.context.fund_ids
        prev_hash_by_fund: dict[str, str | None] = {}
        for fund_key, fund_id in fund_ids.items():
            prev_hash_by_fund[str(fund_id)] = await self.pg.fetchval(
                """
                SELECT entry_hash
                FROM finance.journal_entries
                WHERE scheme_id = $1 AND fund_id = $2
                ORDER BY entry_number DESC
                LIMIT 1
                """,
                self.context.scheme_id,
                fund_id,
            )

        levy_rows = await self.pg.fetch(
            """
            SELECT li.levy_item_id, li.fund_id, li.principal_cents, li.lot_id, li.owner_party_id,
                   lr.issue_date, lr.financial_year
            FROM finance.levy_items li
            JOIN finance.levy_runs lr ON lr.levy_run_id = li.levy_run_id
            WHERE li.scheme_id = $1 AND li.principal_cents > 0
            ORDER BY lr.issue_date ASC, li.levy_item_id ASC
            """,
            self.context.scheme_id,
        )
        receipt_rows = await self.pg.fetch(
            """
            SELECT receipt_id, trust_account_id, lot_id, payer_party_id, amount_cents, received_on, external_reference
            FROM finance.receipts
            WHERE scheme_id = $1 AND amount_cents > 0
            ORDER BY received_on ASC, receipt_id ASC
            """,
            self.context.scheme_id,
        )

        context_manager = _NullAsyncContext() if self.options.dry_run else self.pg.transaction()
        async with context_manager:
            for index, levy in enumerate(levy_rows):
                fund_id = levy["fund_id"]
                fund_key = "capital_works" if fund_id == fund_ids.get("capital_works") else "admin"
                if levy["journal_entry_id"] if "journal_entry_id" in levy else None:
                    pass
                period_label = f"FY {levy['financial_year']}"
                if period_label not in period_cache:
                    starts_on, ends_on = _year_bounds(levy["financial_year"], self.options.fy_start_month)
                    period_cache[period_label] = await self.ensure_period(label=period_label, starts_on=starts_on, ends_on=ends_on)
                narration = f"Levy charge {levy['financial_year']}"
                entry_id, inserted = await self.pg.ensure_row(
                    table="finance.journal_entries",
                    pk_col="journal_entry_id",
                    conflict_cols=("journal_entry_id",),
                    casts={"status": "finance.entry_status", "metadata": "jsonb"},
                    row={
                        "journal_entry_id": self.deterministic_id("journal", "levy_charge", levy["levy_item_id"]),
                        "tenant_id": self.context.tenant_id,
                        "scheme_id": self.context.scheme_id,
                        "fund_id": fund_id,
                        "period_id": period_cache[period_label],
                        "source_type": "levy_charge",
                        "source_reference": str(levy["levy_item_id"]),
                        "narration": narration,
                        "status": "posted",
                        "effective_on": levy["issue_date"],
                        "posted_at": levy["issue_date"],
                        "idempotency_key": f"finance-migration:levy:{levy['levy_item_id']}",
                        "prev_entry_hash": prev_hash_by_fund.get(str(fund_id)),
                        "entry_hash": _compute_entry_hash(
                            str(self.deterministic_id("journal", "levy_charge", levy["levy_item_id"])),
                            narration,
                            prev_hash_by_fund.get(str(fund_id)),
                        ),
                        "is_test_data": False,
                        "metadata": {"script": self.script_name, "source_kind": "unit_levy_ledger"},
                    },
                )
                prev_hash_by_fund[str(fund_id)] = await self.pg.fetchval(
                    "SELECT entry_hash FROM finance.journal_entries WHERE journal_entry_id = $1",
                    entry_id,
                )
                for suffix, gl_code, direction in (
                    ("dr", "1100", "debit"),
                    ("cr", "4001" if fund_key == "capital_works" else "4000", "credit"),
                ):
                    line_id, line_inserted = await self.pg.ensure_row(
                        table="finance.journal_lines",
                        pk_col="journal_line_id",
                        conflict_cols=("journal_line_id",),
                        casts={"direction": "finance.entry_direction"},
                        row={
                            "journal_line_id": self.deterministic_id("journal_line", entry_id, suffix),
                            "tenant_id": self.context.tenant_id,
                            "scheme_id": self.context.scheme_id,
                            "journal_entry_id": entry_id,
                            "gl_account_id": gl_map[gl_code],
                            "direction": direction,
                            "amount_cents": levy["principal_cents"],
                            "lot_id": levy["lot_id"],
                            "party_id": levy["owner_party_id"],
                            "narration": narration,
                        },
                    )
                    source = self.levy_source_by_item_id.get(
                        str(levy["levy_item_id"]),
                        {"source_collection": "unit_levy_ledger", "source_id": str(levy["levy_item_id"])},
                    )
                    self.report.record_trace(
                        status="migrated" if line_inserted else "skipped",
                        target_table="finance.journal_lines",
                        target_id=str(line_id),
                        source_collection=source["source_collection"],
                        source_id=source["source_id"],
                        phase=phase,
                        metadata={"journal_entry_id": str(entry_id), "direction": direction},
                    )
                if inserted and not self.options.dry_run:
                    await self.pg.execute(
                        """
                        UPDATE finance.levy_items
                        SET journal_entry_id = $1
                        WHERE levy_item_id = $2
                          AND journal_entry_id IS NULL
                        """,
                        entry_id,
                        levy["levy_item_id"],
                    )
                source = self.levy_source_by_item_id.get(
                    str(levy["levy_item_id"]),
                    {"source_collection": "unit_levy_ledger", "source_id": str(levy["levy_item_id"])},
                )
                self.report.record_trace(
                    status="migrated" if inserted else "skipped",
                    target_table="finance.journal_entries",
                    target_id=str(entry_id),
                    source_collection=source["source_collection"],
                    source_id=source["source_id"],
                    phase=phase,
                )
                self.checkpoints.mark_phase(
                    run_id=self.options.run_id,
                    phase=phase,
                    batch_index=index,
                    status="committed",
                    last_source_token=str(levy["levy_item_id"]),
                    stats={"kind": "levy_charge"},
                )

            for index, receipt in enumerate(receipt_rows, start=len(levy_rows)):
                period_label = f"FY {_fy_for_date(receipt['received_on'], self.options.fy_start_month)}"
                if period_label not in period_cache:
                    starts_on, ends_on = _year_bounds(_fy_for_date(receipt["received_on"], self.options.fy_start_month), self.options.fy_start_month)
                    period_cache[period_label] = await self.ensure_period(label=period_label, starts_on=starts_on, ends_on=ends_on)
                entry_id, inserted = await self.pg.ensure_row(
                    table="finance.journal_entries",
                    pk_col="journal_entry_id",
                    conflict_cols=("journal_entry_id",),
                    casts={"status": "finance.entry_status", "metadata": "jsonb"},
                    row={
                        "journal_entry_id": self.deterministic_id("journal", "receipt", receipt["receipt_id"]),
                        "tenant_id": self.context.tenant_id,
                        "scheme_id": self.context.scheme_id,
                        "fund_id": fund_ids.get("admin"),
                        "period_id": period_cache[period_label],
                        "source_type": "levy_receipt",
                        "source_reference": str(receipt["receipt_id"]),
                        "narration": f"Levy receipt {receipt['external_reference'] or receipt['receipt_id']}",
                        "status": "posted",
                        "effective_on": receipt["received_on"],
                        "posted_at": receipt["received_on"],
                        "idempotency_key": f"finance-migration:receipt:{receipt['receipt_id']}",
                        "prev_entry_hash": prev_hash_by_fund.get(str(fund_ids.get("admin"))),
                        "entry_hash": _compute_entry_hash(
                            str(self.deterministic_id("journal", "receipt", receipt["receipt_id"])),
                            f"Levy receipt {receipt['external_reference'] or receipt['receipt_id']}",
                            prev_hash_by_fund.get(str(fund_ids.get("admin"))),
                        ),
                        "is_test_data": False,
                        "metadata": {"script": self.script_name, "source_kind": "levy_payments"},
                    },
                )
                prev_hash_by_fund[str(fund_ids.get("admin"))] = await self.pg.fetchval(
                    "SELECT entry_hash FROM finance.journal_entries WHERE journal_entry_id = $1",
                    entry_id,
                )
                for suffix, gl_code, direction in (
                    ("dr", "1010", "debit"),
                    ("cr", "1100", "credit"),
                ):
                    line_id, line_inserted = await self.pg.ensure_row(
                        table="finance.journal_lines",
                        pk_col="journal_line_id",
                        conflict_cols=("journal_line_id",),
                        casts={"direction": "finance.entry_direction"},
                        row={
                            "journal_line_id": self.deterministic_id("journal_line", entry_id, suffix),
                            "tenant_id": self.context.tenant_id,
                            "scheme_id": self.context.scheme_id,
                            "journal_entry_id": entry_id,
                            "gl_account_id": gl_map[gl_code],
                            "direction": direction,
                            "amount_cents": receipt["amount_cents"],
                            "lot_id": receipt["lot_id"],
                            "party_id": receipt["payer_party_id"],
                            "narration": f"Levy receipt {receipt['external_reference'] or receipt['receipt_id']}",
                        },
                    )
                    source = self.receipt_source_by_id.get(
                        str(receipt["receipt_id"]),
                        {"source_collection": "levy_payments", "source_id": str(receipt["receipt_id"])},
                    )
                    self.report.record_trace(
                        status="migrated" if line_inserted else "skipped",
                        target_table="finance.journal_lines",
                        target_id=str(line_id),
                        source_collection=source["source_collection"],
                        source_id=source["source_id"],
                        phase=phase,
                        metadata={"journal_entry_id": str(entry_id), "direction": direction},
                    )
                if inserted and not self.options.dry_run:
                    await self.pg.execute(
                        """
                        UPDATE finance.receipts
                        SET journal_entry_id = $1
                        WHERE receipt_id = $2
                          AND journal_entry_id IS NULL
                        """,
                        entry_id,
                        receipt["receipt_id"],
                    )
                source = self.receipt_source_by_id.get(
                    str(receipt["receipt_id"]),
                    {"source_collection": "levy_payments", "source_id": str(receipt["receipt_id"])},
                )
                self.report.record_trace(
                    status="migrated" if inserted else "skipped",
                    target_table="finance.journal_entries",
                    target_id=str(entry_id),
                    source_collection=source["source_collection"],
                    source_id=source["source_id"],
                    phase=phase,
                )
                self.checkpoints.mark_phase(
                    run_id=self.options.run_id,
                    phase=phase,
                    batch_index=index,
                    status="committed",
                    last_source_token=str(receipt["receipt_id"]),
                    stats={"kind": "receipt"},
                )

    async def _validate(self) -> None:
        """Generated function header.

        Function: FinanceMigration._validate
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        mongo_levy_total = 0
        async for row in self.mongo_db.unit_levy_ledger.find(
            {"building_id": self.options.building_id, "is_test_data": {"$ne": True}},
            {"admin_levied": 1, "sinking_levied": 1},
        ):
            mongo_levy_total += self.coerce_cents(row.get("admin_levied"))
            mongo_levy_total += self.coerce_cents(row.get("sinking_levied"))
        pg_levy_total = await self.pg.fetchval(
            "SELECT COALESCE(SUM(principal_cents), 0) FROM finance.levy_items WHERE scheme_id = $1",
            self.context.scheme_id,
        )
        self.report.add_variance(
            name="levy_principal_total",
            expected_cents=mongo_levy_total,
            actual_cents=pg_levy_total,
        )

        mongo_receipt_total = 0
        async for row in self.mongo_db.levy_payments.find(
            {"building_id": self.options.building_id, "status": {"$in": ["confirmed", "completed", "paid", None]}, "is_test_data": {"$ne": True}},
            {"amount": 1, "amount_cents": 1},
        ):
            amount_cents = self.coerce_cents(row.get("amount_cents"))
            if amount_cents <= 0:
                amount_cents = self.coerce_cents(row.get("amount"))
            if amount_cents > 0:
                mongo_receipt_total += amount_cents
        pg_receipt_total = await self.pg.fetchval(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM finance.receipts WHERE scheme_id = $1",
            self.context.scheme_id,
        )
        self.report.add_variance(
            name="receipt_total",
            expected_cents=mongo_receipt_total,
            actual_cents=pg_receipt_total,
        )

        debit_total = await self.pg.fetchval(
            """
            SELECT COALESCE(SUM(jl.amount_cents), 0)
            FROM finance.journal_lines jl
            JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
            WHERE je.scheme_id = $1
              AND je.metadata->>'script' = $2
              AND jl.direction = 'debit'
            """,
            self.context.scheme_id,
            self.script_name,
        )
        credit_total = await self.pg.fetchval(
            """
            SELECT COALESCE(SUM(jl.amount_cents), 0)
            FROM finance.journal_lines jl
            JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
            WHERE je.scheme_id = $1
              AND je.metadata->>'script' = $2
              AND jl.direction = 'credit'
            """,
            self.context.scheme_id,
            self.script_name,
        )
        self.report.add_variance(
            name="trial_balance",
            expected_cents=int(debit_total),
            actual_cents=int(credit_total),
            metadata={"note": "expected_cents mirrors debits so zero variance means balanced"},
        )

    def _raise_on_validation_issues(self) -> None:
        """Generated function header.

        Function: FinanceMigration._raise_on_validation_issues
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        unresolved_variances = [item for item in self.report.variances if int(item["variance_cents"]) != 0]
        if unresolved_variances:
            raise RuntimeError(
                "Validation failed: unresolved variances remain: "
                + ", ".join(item["name"] for item in unresolved_variances)
            )
        if self.report.unresolved_rows:
            raise RuntimeError("Validation failed: unresolved source rows remain")
        if self.report.failures:
            raise RuntimeError("Validation failed: failed source rows remain")


class _NullAsyncContext:
    async def __aenter__(self) -> "_NullAsyncContext":
        """Generated function header.

        Function: _NullAsyncContext.__aenter__
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Generated function header.

        Function: _NullAsyncContext.__aexit__
        Path: backend/migrations/postgres/finance_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return None


def parse_args() -> argparse.Namespace:
    """Generated function header.

    Function: parse_args
    Path: backend/migrations/postgres/finance_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Migrate levy and receipt data from MongoDB to PostgreSQL.")
    parser.add_argument("--building-id", required=True)
    parser.add_argument("--scheme-number")
    parser.add_argument("--mongo-dsn")
    parser.add_argument("--mongo-db-name")
    parser.add_argument("--pg-dsn")
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--output-dir", default="backend/migrations/postgres/runtime")
    parser.add_argument("--checkpoint-dir", default="backend/migrations/postgres/runtime/checkpoints")
    return parser.parse_args()


async def _main_async(args: argparse.Namespace) -> dict[str, Any]:
    """Generated function header.

    Function: _main_async
    Path: backend/migrations/postgres/finance_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    options = MigrationOptions.from_env(
        building_id=args.building_id,
        scheme_number=args.scheme_number,
        mongo_dsn=args.mongo_dsn,
        mongo_db_name=args.mongo_db_name,
        pg_dsn=args.pg_dsn,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        validate_only=args.validate_only,
        output_dir=args.output_dir,
        checkpoint_dir=args.checkpoint_dir,
    )
    if not options.mongo_dsn or not options.mongo_db_name or not options.pg_dsn:
        raise RuntimeError("mongo_dsn, mongo_db_name, and pg_dsn are required")
    mongo_client = AsyncIOMotorClient(options.mongo_dsn)
    pg = await AsyncpgMigrationAdapter.connect(options.pg_dsn)
    try:
        migration = FinanceMigration(options=options, mongo_client=mongo_client, pg=pg)
        return await migration.run()
    finally:
        mongo_client.close()
        await pg.close()


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/migrations/postgres/finance_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = asyncio.run(_main_async(parse_args()))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
