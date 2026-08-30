from __future__ import annotations

import argparse
import asyncio
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
from utils.encryption import encrypt_field


GL_SPECS = (
    ("1010", "Trust Bank Account - Admin", "asset", True, "admin"),
    ("1011", "Trust Bank Account - Capital Works", "asset", True, "capital_works"),
    ("3100", "Opening Balance Clearing", "equity", False, None),
    ("4002", "Bank Interest Income", "income", False, "admin"),
    ("5000", "Bank Fees", "expense", False, "admin"),
)


def _fy_for_date(value: date) -> str:
    """Generated function header.

    Function: _fy_for_date
    Path: backend/migrations/postgres/trust_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return str(value.year if value.month >= 7 else value.year - 1)


def _year_bounds(financial_year: str) -> tuple[date, date]:
    """Generated function header.

    Function: _year_bounds
    Path: backend/migrations/postgres/trust_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    year = int(str(financial_year).split("-")[0])
    return date(year, 7, 1), date(year + 1, 6, 30)


def _compute_entry_hash(entry_id: str, narration: str, prev_hash: str | None) -> str:
    """Generated function header.

    Function: _compute_entry_hash
    Path: backend/migrations/postgres/trust_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = json.dumps(
        {"id": entry_id, "narration": narration, "prev": prev_hash or ""},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class AdjustmentRequirements:
    reason_code: str | None
    operator_note: str | None
    evidence_reference: str | None

    def require(self) -> None:
        """Generated function header.

        Function: AdjustmentRequirements.require
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        missing = [
            name
            for name, value in (
                ("reason_code", self.reason_code),
                ("operator_note", self.operator_note),
                ("evidence_reference", self.evidence_reference),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "migration_adjustment requires "
                + ", ".join(missing)
            )


class TrustMigration(BaseBuildingMigration):
    script_name = "trust_migration"

    def __init__(
        self,
        *,
        options: MigrationOptions,
        mongo_client: AsyncIOMotorClient,
        pg: AsyncpgMigrationAdapter,
        adjustments: AdjustmentRequirements,
    ) -> None:
        """Generated function header.

        Function: TrustMigration.__init__
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        super().__init__(options=options, mongo_client=mongo_client, pg=pg)
        self.adjustments = adjustments
        self.account_map: dict[str, dict[str, Any]] = {}

    async def run(self) -> dict[str, Any]:
        """Generated function header.

        Function: TrustMigration.run
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        try:
            await self.prepare()
            if self.options.validate_only:
                await self._load_existing_reference_data()
                await self._load_existing_account_map()
                await self._validate()
                self._raise_on_validation_issues()
                return await self.finalize(status="completed")
            await self._ensure_reference_data()
            await self._migrate_trust_accounts()
            await self._migrate_bank_transactions()
            await self._migrate_trust_journals()
            await self._post_balance_adjustments()
            await self._validate()
            self._raise_on_validation_issues()
            return await self.finalize(status="completed")
        except Exception:
            await self.finalize(status="failed")
            raise

    async def _load_existing_reference_data(self) -> None:
        """Generated function header.

        Function: TrustMigration._load_existing_reference_data
        Path: backend/migrations/postgres/trust_migration.py

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

    async def _load_existing_account_map(self) -> None:
        """Generated function header.

        Function: TrustMigration._load_existing_account_map
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        if not self.context.trust_accounts:
            await self.load_trust_account_map()
        docs = await self.mongo_db.trust_accounts_v2.find(
            {"building_id": self.options.building_id, "is_test_data": {"$ne": True}}
        ).to_list(None)
        self.account_map = {}
        for row in docs:
            source_id = self.source_token(row, "account_name", "bank_name")
            trust_account_id = self.context.trust_accounts.get(str(row.get("_id") or "").strip()) or self.context.trust_accounts.get(source_id)
            if trust_account_id is None:
                continue
            fund_key = self._normalize_fund_key(row.get("fund_type") or row.get("account_type"))
            fund_id = self.context.fund_ids.get(fund_key)
            if fund_id is None:
                continue
            self.account_map[source_id] = {
                "trust_account_id": trust_account_id,
                "fund_key": fund_key,
                "fund_id": fund_id,
                "opening_balance_cents": self.coerce_cents(
                    row.get("opening_balance_cents", row.get("current_balance_cents", 0))
                ),
                "current_balance_cents": self.coerce_cents(
                    row.get("current_balance_cents", row.get("opening_balance_cents", 0))
                ),
                "last_reconciled_balance_cents": self.coerce_cents(row.get("last_reconciled_balance_cents")),
                "last_reconciled_at": row.get("last_reconciled_at"),
                "source_id": source_id,
            }

    async def _ensure_reference_data(self) -> None:
        """Generated function header.

        Function: TrustMigration._ensure_reference_data
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        await self.load_fund_ids()
        await self.load_gl_accounts()
        await self.load_trust_account_map()
        for code, name, account_type, is_control, fund_key in GL_SPECS:
            fund_id = self.context.fund_ids.get(fund_key) if fund_key else None
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

    async def _migrate_trust_accounts(self) -> None:
        """Generated function header.

        Function: TrustMigration._migrate_trust_accounts
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        phase = "trust_accounts"
        async for batch_index, rows in self.iter_collection_batches(
            collection_name="trust_accounts_v2",
            query={"building_id": self.options.building_id, "is_test_data": {"$ne": True}},
            phase=phase,
        ):
            last_token = None
            context_manager = _NullAsyncContext() if self.options.dry_run else self.pg.transaction()
            async with context_manager:
                for row in rows:
                    source_id = self.source_token(row, "account_name", "bank_name")
                    last_token = source_id
                    fund_key = self._normalize_fund_key(row.get("fund_type") or row.get("account_type"))
                    fund_id = self.context.fund_ids.get(fund_key)
                    if fund_id is None:
                        self.report.add_unresolved(
                            phase=phase,
                            source_collection="trust_accounts_v2",
                            source_id=source_id,
                            reason="missing_target_fund",
                            metadata={"fund_key": fund_key},
                        )
                        continue

                    bsb_raw = str(row.get("bsb") or "")
                    account_number_raw = str(
                        row.get("account_number")
                        or row.get("account_number_masked")
                        or row.get("account_number_last4")
                        or ""
                    )
                    bsb_cipher = encrypt_field(bsb_raw) if bsb_raw else None
                    account_cipher = encrypt_field(account_number_raw) if account_number_raw else None
                    trust_account_id, inserted = await self.pg.ensure_row(
                        table="finance.trust_accounts",
                        pk_col="trust_account_id",
                        conflict_cols=("trust_account_id",),
                        casts={"status": "core.record_status"},
                        row={
                            "trust_account_id": self.deterministic_id("trust_account", source_id),
                            "tenant_id": self.context.tenant_id,
                            "scheme_id": self.context.scheme_id,
                            "fund_id": fund_id,
                            "bank_name": row.get("bank_name") or "Unknown Bank",
                            "account_name": row.get("account_name") or row.get("account_type") or "Trust Account",
                            "bsb_encrypted": bsb_cipher.encode("utf-8") if bsb_cipher else None,
                            "account_number_encrypted": account_cipher.encode("utf-8") if account_cipher else None,
                            "masked_bsb": row.get("bsb"),
                            "masked_account_number": row.get("account_number_masked") or row.get("account_number"),
                            "external_uid": source_id,
                            "opened_on": self.coerce_date(row.get("created_at") or row.get("opened_on")),
                            "status": "active" if row.get("is_active", True) else "inactive",
                        },
                    )
                    self.account_map[source_id] = {
                        "trust_account_id": trust_account_id,
                        "fund_key": fund_key,
                        "fund_id": fund_id,
                        "opening_balance_cents": self.coerce_cents(
                            row.get("opening_balance_cents", row.get("current_balance_cents", 0))
                        ),
                        "current_balance_cents": self.coerce_cents(
                            row.get("current_balance_cents", row.get("opening_balance_cents", 0))
                        ),
                        "last_reconciled_balance_cents": self.coerce_cents(row.get("last_reconciled_balance_cents")),
                        "last_reconciled_at": row.get("last_reconciled_at"),
                        "source_id": source_id,
                    }
                    self.report.record_trace(
                        status="migrated" if inserted else "skipped",
                        target_table="finance.trust_accounts",
                        target_id=str(trust_account_id),
                        source_collection="trust_accounts_v2",
                        source_id=source_id,
                        phase=phase,
                        metadata={"fund_key": fund_key},
                    )
            self.checkpoints.mark_phase(
                run_id=self.options.run_id,
                phase=phase,
                batch_index=batch_index,
                status="committed",
                last_source_token=last_token,
                stats={"rows": len(rows)},
            )

    async def _migrate_bank_transactions(self) -> None:
        """Generated function header.

        Function: TrustMigration._migrate_bank_transactions
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        phase = "bank_transactions"
        async for batch_index, rows in self.iter_collection_batches(
            collection_name="bank_transactions",
            query={"building_id": self.options.building_id, "is_test_data": {"$ne": True}},
            phase=phase,
        ):
            last_token = None
            context_manager = _NullAsyncContext() if self.options.dry_run else self.pg.transaction()
            async with context_manager:
                for row in rows:
                    source_id = self.source_token(row, "external_tx_id", "reference")
                    last_token = source_id
                    trust_account_id = self._resolve_trust_account_id(row)
                    if trust_account_id is None:
                        self.report.add_unresolved(
                            phase=phase,
                            source_collection="bank_transactions",
                            source_id=source_id,
                            reason="missing_trust_account_match",
                        )
                        continue
                    signed_amount = self.coerce_cents(row.get("signed_amount"))
                    if signed_amount == 0:
                        amount = self.coerce_cents(row.get("amount"))
                        signed_amount = amount if str(row.get("type") or "").lower() == "credit" else -amount
                    bank_tx_id, inserted = await self.pg.ensure_row(
                        table="finance.bank_transactions",
                        pk_col="bank_transaction_id",
                        conflict_cols=("trust_account_id", "external_transaction_id"),
                        casts={"reconciliation_status": "finance.reconciliation_status"},
                        row={
                            "bank_transaction_id": self.deterministic_id("bank_transaction", source_id),
                            "tenant_id": self.context.tenant_id,
                            "scheme_id": self.context.scheme_id,
                            "trust_account_id": trust_account_id,
                            "transaction_date": self.coerce_date(row.get("date") or row.get("created_at")),
                            "description": row.get("description") or "Imported bank transaction",
                            "reference": row.get("reference"),
                            "amount_cents": signed_amount,
                            "balance_after_cents": self.coerce_cents(row.get("balance")),
                            "external_transaction_id": row.get("external_tx_id") or source_id,
                            "reconciliation_status": "matched" if row.get("matched") else "unmatched",
                        },
                    )
                    self.report.record_trace(
                        status="migrated" if inserted else "skipped",
                        target_table="finance.bank_transactions",
                        target_id=str(bank_tx_id),
                        source_collection="bank_transactions",
                        source_id=source_id,
                        phase=phase,
                    )
            self.checkpoints.mark_phase(
                run_id=self.options.run_id,
                phase=phase,
                batch_index=batch_index,
                status="committed",
                last_source_token=last_token,
                stats={"rows": len(rows)},
            )

    async def _migrate_trust_journals(self) -> None:
        """Generated function header.

        Function: TrustMigration._migrate_trust_journals
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        phase = "trust_journals"
        prev_hash_by_fund: dict[str, str | None] = {}
        for account in self.account_map.values():
            prev_hash_by_fund[str(account["fund_id"])] = await self.pg.fetchval(
                """
                SELECT entry_hash
                FROM finance.journal_entries
                WHERE scheme_id = $1 AND fund_id = $2
                ORDER BY entry_number DESC
                LIMIT 1
                """,
                self.context.scheme_id,
                account["fund_id"],
            )
        tx_docs = await self.mongo_db.trust_transactions_v2.find(
            {"building_id": self.options.building_id, "is_reversed": {"$ne": True}, "is_test_data": {"$ne": True}}
        ).sort("created_at", 1).to_list(None)
        source_collection = "trust_transactions_v2"
        if not tx_docs:
            tx_docs = await self.mongo_db.trust_ledger_entries.find(
                {"building_id": self.options.building_id, "is_reversed": {"$ne": True}, "is_test_data": {"$ne": True}}
            ).sort("created_at", 1).to_list(None)
            source_collection = "trust_ledger_entries"

        context_manager = _NullAsyncContext() if self.options.dry_run else self.pg.transaction()
        async with context_manager:
            for batch_index, row in enumerate(tx_docs):
                source_id = self.source_token(row, "reference", "description")
                account_meta = self._resolve_account_meta(row)
                if account_meta is None:
                    self.report.add_unresolved(
                        phase=phase,
                        source_collection=source_collection,
                        source_id=source_id,
                        reason="missing_trust_account_match",
                    )
                    continue
                amount_cents = self.coerce_cents(row.get("amount_cents"))
                if amount_cents <= 0:
                    self.report.add_unresolved(
                        phase=phase,
                        source_collection=source_collection,
                        source_id=source_id,
                        reason="non_positive_amount",
                    )
                    continue

                tx_type = str(row.get("type") or row.get("entry_type") or "").lower()
                bank_gl = self.context.gl_accounts["1011" if account_meta["fund_key"] == "capital_works" else "1010"]
                if tx_type in {"interest"}:
                    source_type = "interest"
                    debit_gl = bank_gl
                    credit_gl = self.context.gl_accounts["4002"]
                elif tx_type in {"bank_charge", "bank_fee", "fee"}:
                    source_type = "bank_fee"
                    debit_gl = self.context.gl_accounts["5000"]
                    credit_gl = bank_gl
                elif tx_type in {"disbursement", "withdrawal", "payment", "reversal"}:
                    source_type = "disbursement"
                    debit_gl = self.context.gl_accounts["3100"]
                    credit_gl = bank_gl
                else:
                    source_type = "legacy_trust_transaction"
                    debit_gl = bank_gl
                    credit_gl = self.context.gl_accounts["3100"]

                effective_on = self.coerce_date(row.get("effective_date") or row.get("date") or row.get("created_at"))
                period_label = f"FY {_fy_for_date(effective_on)}"
                starts_on, ends_on = _year_bounds(_fy_for_date(effective_on))
                period_id = await self.ensure_period(label=period_label, starts_on=starts_on, ends_on=ends_on)
                narration = row.get("description") or tx_type or "Imported trust transaction"
                entry_id, inserted = await self.pg.ensure_row(
                    table="finance.journal_entries",
                    pk_col="journal_entry_id",
                    conflict_cols=("journal_entry_id",),
                    casts={"status": "finance.entry_status", "metadata": "jsonb"},
                    row={
                        "journal_entry_id": self.deterministic_id("journal", source_type, source_id),
                        "tenant_id": self.context.tenant_id,
                        "scheme_id": self.context.scheme_id,
                        "fund_id": account_meta["fund_id"],
                        "period_id": period_id,
                        "source_type": source_type,
                        "source_reference": source_id,
                        "narration": narration,
                        "status": "posted",
                        "effective_on": effective_on,
                        "posted_at": effective_on,
                        "idempotency_key": f"trust-migration:{source_type}:{source_id}",
                        "prev_entry_hash": prev_hash_by_fund.get(str(account_meta["fund_id"])),
                        "entry_hash": _compute_entry_hash(
                            str(self.deterministic_id("journal", source_type, source_id)),
                            narration,
                            prev_hash_by_fund.get(str(account_meta["fund_id"])),
                        ),
                        "is_test_data": False,
                        "metadata": {
                            "script": self.script_name,
                            "source_collection": source_collection,
                            "raw_type": tx_type,
                        },
                    },
                )
                prev_hash_by_fund[str(account_meta["fund_id"])] = await self.pg.fetchval(
                    "SELECT entry_hash FROM finance.journal_entries WHERE journal_entry_id = $1",
                    entry_id,
                )
                for suffix, gl_id, direction in (
                    ("dr", debit_gl, "debit"),
                    ("cr", credit_gl, "credit"),
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
                            "gl_account_id": gl_id,
                            "direction": direction,
                            "amount_cents": amount_cents,
                            "narration": narration,
                        },
                    )
                    self.report.record_trace(
                        status="migrated" if line_inserted else "skipped",
                        target_table="finance.journal_lines",
                        target_id=str(line_id),
                        source_collection=source_collection,
                        source_id=source_id,
                        phase=phase,
                        metadata={"journal_entry_id": str(entry_id), "direction": direction},
                    )
                self.report.record_trace(
                    status="migrated" if inserted else "skipped",
                    target_table="finance.journal_entries",
                    target_id=str(entry_id),
                    source_collection=source_collection,
                    source_id=source_id,
                    phase=phase,
                )
                self.checkpoints.mark_phase(
                    run_id=self.options.run_id,
                    phase=phase,
                    batch_index=batch_index,
                    status="committed",
                    last_source_token=source_id,
                    stats={"source_collection": source_collection},
                )

    async def _post_balance_adjustments(self) -> None:
        """Generated function header.

        Function: TrustMigration._post_balance_adjustments
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        phase = "migration_adjustments"
        for batch_index, (source_id, account_meta) in enumerate(sorted(self.account_map.items())):
            imported_net = await self.pg.fetchval(
                """
                SELECT COALESCE(SUM(
                    CASE
                        WHEN jl.direction = 'debit' THEN jl.amount_cents
                        ELSE -jl.amount_cents
                    END
                ), 0)
                FROM finance.journal_lines jl
                JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
                WHERE je.scheme_id = $1
                  AND je.fund_id = $2
                  AND je.metadata->>'script' = $3
                  AND jl.gl_account_id = $4
                """,
                self.context.scheme_id,
                account_meta["fund_id"],
                self.script_name,
                self.context.gl_accounts["1011" if account_meta["fund_key"] == "capital_works" else "1010"],
            )
            declared_balance = account_meta["current_balance_cents"] or account_meta["last_reconciled_balance_cents"]
            expected_balance = account_meta["opening_balance_cents"] + int(imported_net)
            delta = declared_balance - expected_balance
            if delta == 0:
                self.checkpoints.mark_phase(
                    run_id=self.options.run_id,
                    phase=phase,
                    batch_index=batch_index,
                    status="committed",
                    last_source_token=source_id,
                    stats={"delta_cents": 0},
                )
                continue
            self.adjustments.require()
            effective_on = self.coerce_date(account_meta.get("last_reconciled_at") or date.today())
            period_label = f"FY {_fy_for_date(effective_on)}"
            starts_on, ends_on = _year_bounds(_fy_for_date(effective_on))
            period_id = await self.ensure_period(label=period_label, starts_on=starts_on, ends_on=ends_on)
            bank_gl = self.context.gl_accounts["1011" if account_meta["fund_key"] == "capital_works" else "1010"]
            debit_gl = bank_gl if delta > 0 else self.context.gl_accounts["3100"]
            credit_gl = self.context.gl_accounts["3100"] if delta > 0 else bank_gl
            entry_id, inserted = await self.pg.ensure_row(
                table="finance.journal_entries",
                pk_col="journal_entry_id",
                conflict_cols=("journal_entry_id",),
                casts={"status": "finance.entry_status", "metadata": "jsonb"},
                row={
                    "journal_entry_id": self.deterministic_id("journal", "migration_adjustment", source_id),
                    "tenant_id": self.context.tenant_id,
                    "scheme_id": self.context.scheme_id,
                    "fund_id": account_meta["fund_id"],
                    "period_id": period_id,
                    "source_type": "migration_adjustment",
                    "source_reference": source_id,
                    "narration": f"Migration adjustment for trust account {source_id}",
                    "status": "posted",
                    "effective_on": effective_on,
                    "posted_at": effective_on,
                    "idempotency_key": f"trust-migration:migration_adjustment:{source_id}",
                    "prev_entry_hash": await self.pg.fetchval(
                        """
                        SELECT entry_hash
                        FROM finance.journal_entries
                        WHERE scheme_id = $1 AND fund_id = $2
                        ORDER BY entry_number DESC
                        LIMIT 1
                        """,
                        self.context.scheme_id,
                        account_meta["fund_id"],
                    ),
                    "entry_hash": _compute_entry_hash(
                        str(self.deterministic_id("journal", "migration_adjustment", source_id)),
                        f"Migration adjustment for trust account {source_id}",
                        await self.pg.fetchval(
                            """
                            SELECT entry_hash
                            FROM finance.journal_entries
                            WHERE scheme_id = $1 AND fund_id = $2
                            ORDER BY entry_number DESC
                            LIMIT 1
                            """,
                            self.context.scheme_id,
                            account_meta["fund_id"],
                        ),
                    ),
                    "is_test_data": False,
                    "metadata": {
                        "script": self.script_name,
                        "reason_code": self.adjustments.reason_code,
                        "operator_note": self.adjustments.operator_note,
                        "evidence_reference": self.adjustments.evidence_reference,
                        "declared_balance_cents": declared_balance,
                        "expected_balance_cents": expected_balance,
                        "delta_cents": delta,
                    },
                },
            )
            for suffix, gl_id, direction in (
                ("dr", debit_gl, "debit"),
                ("cr", credit_gl, "credit"),
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
                        "gl_account_id": gl_id,
                        "direction": direction,
                        "amount_cents": abs(delta),
                        "narration": f"Migration adjustment for trust account {source_id}",
                    },
                )
                self.report.record_trace(
                    status="adjusted" if line_inserted else "skipped",
                    target_table="finance.journal_lines",
                    target_id=str(line_id),
                    source_collection="migration_adjustment",
                    source_id=source_id,
                    phase=phase,
                    metadata={"direction": direction, "delta_cents": delta},
                )
            self.report.record_trace(
                status="adjusted" if inserted else "skipped",
                target_table="finance.journal_entries",
                target_id=str(entry_id),
                source_collection="migration_adjustment",
                source_id=source_id,
                phase=phase,
                metadata={"delta_cents": delta},
            )
            self.checkpoints.mark_phase(
                run_id=self.options.run_id,
                phase=phase,
                batch_index=batch_index,
                status="committed",
                last_source_token=source_id,
                stats={"delta_cents": delta},
            )

    async def _validate(self) -> None:
        """Generated function header.

        Function: TrustMigration._validate
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        mongo_bank_total = 0
        async for row in self.mongo_db.bank_transactions.find(
            {"building_id": self.options.building_id, "is_test_data": {"$ne": True}},
            {"signed_amount": 1, "amount": 1, "type": 1},
        ):
            signed_amount = self.coerce_cents(row.get("signed_amount"))
            if signed_amount == 0:
                amount = self.coerce_cents(row.get("amount"))
                signed_amount = amount if str(row.get("type") or "").lower() == "credit" else -amount
            mongo_bank_total += signed_amount
        pg_bank_total = await self.pg.fetchval(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM finance.bank_transactions WHERE scheme_id = $1",
            self.context.scheme_id,
        )
        self.report.add_variance(
            name="bank_transaction_total",
            expected_cents=mongo_bank_total,
            actual_cents=pg_bank_total,
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

        for source_id, account_meta in sorted(self.account_map.items()):
            bank_gl = self.context.gl_accounts["1011" if account_meta["fund_key"] == "capital_works" else "1010"]
            computed_balance = account_meta["opening_balance_cents"] + int(
                await self.pg.fetchval(
                    """
                    SELECT COALESCE(SUM(
                        CASE
                            WHEN jl.direction = 'debit' THEN jl.amount_cents
                            ELSE -jl.amount_cents
                        END
                    ), 0)
                    FROM finance.journal_lines jl
                    JOIN finance.journal_entries je ON je.journal_entry_id = jl.journal_entry_id
                    WHERE je.scheme_id = $1
                      AND je.fund_id = $2
                      AND je.metadata->>'script' = $3
                      AND jl.gl_account_id = $4
                    """,
                    self.context.scheme_id,
                    account_meta["fund_id"],
                    self.script_name,
                    bank_gl,
                )
            )
            declared_balance = account_meta["current_balance_cents"] or account_meta["last_reconciled_balance_cents"]
            self.report.add_variance(
                name=f"trust_balance:{source_id}",
                expected_cents=declared_balance,
                actual_cents=computed_balance,
            )

    def _raise_on_validation_issues(self) -> None:
        """Generated function header.

        Function: TrustMigration._raise_on_validation_issues
        Path: backend/migrations/postgres/trust_migration.py

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

    def _normalize_fund_key(self, raw_value: Any) -> str:
        """Generated function header.

        Function: TrustMigration._normalize_fund_key
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        text = str(raw_value or "").lower()
        if "capital" in text or "sinking" in text:
            return "capital_works"
        return "admin"

    def _resolve_account_meta(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Generated function header.

        Function: TrustMigration._resolve_account_meta
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        candidates = [
            str(row.get("trust_account_id") or ""),
            str(row.get("account_id") or ""),
            str(row.get("reference_account_id") or ""),
        ]
        for candidate in candidates:
            if candidate and candidate in self.account_map:
                return self.account_map[candidate]
        if len(self.account_map) == 1:
            return next(iter(self.account_map.values()))
        return None

    def _resolve_trust_account_id(self, row: dict[str, Any]) -> Any | None:
        """Generated function header.

        Function: TrustMigration._resolve_trust_account_id
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        account_meta = self._resolve_account_meta(row)
        return account_meta["trust_account_id"] if account_meta else None


class _NullAsyncContext:
    async def __aenter__(self) -> "_NullAsyncContext":
        """Generated function header.

        Function: _NullAsyncContext.__aenter__
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        """Generated function header.

        Function: _NullAsyncContext.__aexit__
        Path: backend/migrations/postgres/trust_migration.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return None


def parse_args() -> argparse.Namespace:
    """Generated function header.

    Function: parse_args
    Path: backend/migrations/postgres/trust_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Migrate trust account, bank transaction, and trust journal data.")
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
    parser.add_argument("--adjustment-reason-code")
    parser.add_argument("--adjustment-operator-note")
    parser.add_argument("--adjustment-evidence-reference")
    return parser.parse_args()


async def _main_async(args: argparse.Namespace) -> dict[str, Any]:
    """Generated function header.

    Function: _main_async
    Path: backend/migrations/postgres/trust_migration.py

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
        migration = TrustMigration(
            options=options,
            mongo_client=mongo_client,
            pg=pg,
            adjustments=AdjustmentRequirements(
                reason_code=args.adjustment_reason_code,
                operator_note=args.adjustment_operator_note,
                evidence_reference=args.adjustment_evidence_reference,
            ),
        )
        return await migration.run()
    finally:
        mongo_client.close()
        await pg.close()


def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/migrations/postgres/trust_migration.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = asyncio.run(_main_async(parse_args()))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
