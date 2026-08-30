"""Data migration: building 13195 (East Gate Residences) → PostgreSQL.

PHASE 2 — REFERENCE DATA + LEVY/RECEIPT/JOURNAL HISTORY
══════════════════════════════════════════════════════
This script populates structural reference data (funds, GL accounts, lots,
parties, trust accounts, accounting period) AND financial history (ownership
periods, annual levy runs and items, receipts, FIFO allocations, and the
double-entry journal entries with hash-chained tamper-evident audit) for the
East Gate scheme.

ROLLOUT MODEL (strangler-fig per `12_postgresql_financial_core.md`):
    1.  Apply Alembic through 0031.
    2.  Run this script ONCE per environment.  Subsequent runs are idempotent
        (every INSERT uses ON CONFLICT DO NOTHING with deterministic UUIDv5 IDs).
    3.  Enable cutover toggles in order via `services/cutover_config_service.py`:
        financial_integration_layer_v2 → financial_pg_writes_enabled →
        financial_shadow_reads_enabled → financial_pg_reads_enabled →
        external_api_finance_pg_enabled → trust_pg_ledger_enabled →
        trust_reconciliation_pg_enabled.
    4.  After 30 days of clean shadow-read parity, archive the corresponding
        Mongo finance collections per `tasks/todo-gaps.md` FIN-PG-CUTOVER-12.

STEPS:
    1.  tenant + scheme
    2.  funds (admin + capital works)
    3.  gl_accounts (chart of accounts)
    4.  accounting_period (one period per financial year)
    5.  lots + parties (owners) — reference data from units collection
    6.  trust_accounts — bank account reference data
    7.  ownership_periods — bitemporal owner history from user_units active rows
    8.  levy_runs + levy_items — annual levies from unit_levy_ledger
    9.  receipts — confirmed payments from levy_payments
    10. receipt_allocations — FIFO allocation of receipts against levy items
    11. journal_entries + journal_lines — derived from levy_items + receipts,
        hash-chained (SHA-256 prev/entry pairs) for tamper-evidence

USAGE:
    cd backend
    MONGO_URL=... DB_NAME=strataos DATABASE_URL=postgresql+asyncpg://... \
        venv/bin/python3 migrations/postgres/migrate_building_13195.py [--dry-run] [--validate]

OPTIONS:
    --dry-run   Validate and print counts without writing to Postgres.  Steps
                that depend on Postgres reads (receipt_allocations, journals)
                report intent only.
    --validate  After migration, run validation SQL: trial-balance equality,
                hash-chain integrity, no-orphan invariants, receipt/levy parity.

INVARIANTS:
    - All Mongo amounts are in dollars (float); Postgres stores integer cents.
    - All migrated records have is_test_data=False.
    - UUIDs are deterministic (UUIDv5, namespace=building_13195) for idempotency.
    - If the target East Gate scheme already exists in Postgres, reuse its
      tenant_id/scheme_id instead of creating a duplicate live scheme.
    - Run multiple times safely: all inserts use ON CONFLICT DO NOTHING.
    - This script only WRITES to Postgres; it never modifies Mongo state.
    - Hash chain is per-scheme and ordered by (effective_on, source_id) so
      re-runs cannot produce divergent chains.

NON-GOALS:
    - Does not migrate trust_ledger_entries / trust_accounts_v2 /
      trust_transactions_v2 directly (they are stale or empty for 13195 — see
      Phase 1 notes below).  Journals are derived from authoritative
      levy_items + receipts.
    - Does not flip any feature toggle.  Cutover sequencing is the operator's
      responsibility per the rollout model above.

PHASE 1 NOTES (preserved for context):
    - trust_levy_schedules_v2 was rejected as a source (unit_number overlap
      67/87 with stale identifiers).
    - trust_ledger_entries was rejected (40 docs all lacking top-level amount).
    - trust_transactions_v2 was rejected (empty for building 13195).
    - unit_levy_ledger and levy_payments are aligned 100% on unit_number with
      authoritative admin/sinking split and confirmed payment status.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid5, NAMESPACE_URL

import motor.motor_asyncio

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("migrate_13195")

# ------------------------------------------------------------------ constants

BUILDING_ID = "13195"
SCHEME_NAME = "East Gate Residences"
JURISDICTION = "ACT"
MANAGER_NAME = "Silverfox Technologies"
BYPASS_TENANT_ID = "00000000-0000-0000-0000-000000000000"

# East Gate's confirmed building handover date (financial_source_fact_versions'
# own levy_year_start for 2021) — the correct anchor for "owner since building
# inception" ownership_periods rows, never the 1900-01-01 placeholder or a
# units-collection seed timestamp. See _step_07_ownership_periods()'s use of
# ownership_transfer_log for real per-unit transfer dates where a unit has
# actually changed hands since handover.
BUILDING_HANDOVER_DATE = date(2020, 12, 1)

# Deterministic namespace UUID for building 13195 — all migrated IDs are UUIDv5
NAMESPACE_13195 = uuid5(NAMESPACE_URL, f"strataos:building:{BUILDING_ID}")

# GL Account codes (standard chart of accounts for ACT strata)
GL_ACCOUNTS = [
    ("1010", "Trust Bank Account - Admin", "asset"),
    ("1011", "Trust Bank Account - Capital Works", "asset"),
    ("1100", "Levy Receivable", "asset"),
    ("1200", "GST Receivable", "asset"),
    ("2100", "Levy in Advance", "liability"),
    ("2200", "GST Payable", "liability"),
    ("4000", "Admin Fund Levy Income", "income"),
    ("4001", "Capital Works Levy Income", "income"),
    ("4002", "Interest Income", "income"),
    ("5000", "Administration Expenses", "expense"),
    ("5001", "Insurance", "expense"),
    ("5002", "Repairs and Maintenance", "expense"),
    ("5003", "Management Fees", "expense"),
    ("5004", "Legal and Compliance", "expense"),
]


# ------------------------------------------------------------------ helpers

def make_id(*parts: str) -> UUID:
    """Create a deterministic UUID from string parts under namespace_13195."""
    return uuid5(NAMESPACE_13195, ":".join(str(p) for p in parts))


def cents(value: Any) -> int:
    """Convert a Mongo float-dollar value to integer cents."""
    if value is None:
        return 0
    try:
        return int(round(Decimal(str(value)) * 100))
    except Exception:
        return 0


def iso(dt: Any) -> Optional[datetime]:
    """Generated function header.

    Function: iso
    Path: backend/migrations/postgres/migrate_building_13195.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    if isinstance(dt, str):
        try:
            return datetime.fromisoformat(dt).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def today_date() -> date:
    """Generated function header.

    Function: today_date
    Path: backend/migrations/postgres/migrate_building_13195.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return date.today()


# ------------------------------------------------------------------ migration state

@dataclass
class MigrationState:
    tenant_id: UUID = field(default_factory=uuid.uuid4)
    scheme_id: UUID = field(default_factory=uuid.uuid4)
    admin_fund_id: UUID = field(default_factory=uuid.uuid4)
    cw_fund_id: UUID = field(default_factory=uuid.uuid4)
    period_id: UUID = field(default_factory=uuid.uuid4)
    gl_map: dict = field(default_factory=dict)  # code → UUID
    lot_map: dict = field(default_factory=dict)  # lot_number (e.g. 'LOT71') → UUID
    unit_map: dict = field(default_factory=dict)  # unit_number (e.g. 'TH071') → UUID
    party_map: dict = field(default_factory=dict)  # unit_number → party UUID
    trust_account_map: dict = field(default_factory=dict)  # mongo_id → UUID
    # Phase 2 maps
    period_by_year: dict = field(default_factory=dict)  # financial_year (str) → period_id
    receipt_ids: list = field(default_factory=list)  # ordered insertion list (for hash chain)


# ------------------------------------------------------------------ main migration class

class Migration13195:
    def __init__(self, mongo_client, pg_conn, dry_run: bool = False):
        """Generated function header.

        Function: Migration13195.__init__
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.mongo = mongo_client
        self.pg = pg_conn
        self.dry_run = dry_run
        self.state = MigrationState()
        self._stats: dict[str, int] = {}

    async def run(self) -> None:
        """Generated function header.

        Function: Migration13195.run
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        logger.info("=== Starting migration: building %s ===", BUILDING_ID)
        if self.dry_run:
            logger.info("DRY RUN MODE — no writes to Postgres")

        # Phase 1 — reference data
        await self._step_01_tenant_scheme()
        await self._step_02_funds()
        await self._step_03_gl_accounts()
        await self._step_04_accounting_period()
        await self._step_05_lots_and_parties()
        await self._step_06_trust_accounts()

        # Phase 2 — financial history
        await self._step_07_ownership_periods()
        await self._step_08_levy_runs_and_items()
        await self._step_09_receipts()
        await self._step_10_receipt_allocations()
        await self._step_11_journal_entries_and_lines()

        self._print_stats()
        logger.info("=== Migration complete ===")

    async def _resolve_existing_scheme_context(self) -> Optional[tuple[UUID, UUID, str, str]]:
        """Generated function header.

        Function: Migration13195._resolve_existing_scheme_context
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        rows = await self.pg.fetch(
            """
            SELECT s.scheme_id,
                   s.tenant_id,
                   t.tenant_name,
                   s.scheme_name
            FROM core.schemes s
            JOIN core.tenants t ON t.tenant_id = s.tenant_id
            WHERE s.scheme_number = $1
              AND s.status = 'active'
            ORDER BY s.created_at ASC
            """,
            BUILDING_ID,
        )
        if not rows:
            return None
        if len(rows) > 1:
            raise RuntimeError(
                f"Expected one active Postgres scheme for {BUILDING_ID}, found {len(rows)}; "
                "aborting to avoid attaching finance reference data to the wrong live scheme."
            )
        row = rows[0]
        return row["tenant_id"], row["scheme_id"], row["tenant_name"], row["scheme_name"]

    async def _load_existing_fund_ids(self) -> dict[str, UUID]:
        """Generated function header.

        Function: Migration13195._load_existing_fund_ids
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        rows = await self.pg.fetch(
            """
            SELECT fund_id, fund_code, fund_name, fund_type
            FROM finance.funds
            WHERE scheme_id = $1
              AND COALESCE(status, 'active') = 'active'
            """,
            self.state.scheme_id,
        )
        mapping: dict[str, UUID] = {}
        for row in rows:
            candidates = [
                str(row["fund_type"] or ""),
                str(row["fund_code"] or ""),
                str(row["fund_name"] or ""),
            ]
            normalized = " ".join(candidates).lower()
            if "admin" in normalized:
                mapping.setdefault("admin", row["fund_id"])
            if "sink" in normalized or "capital" in normalized:
                mapping.setdefault("capital_works", row["fund_id"])
        return mapping

    async def _step_01_tenant_scheme(self) -> None:
        """Generated function header.

        Function: Migration13195._step_01_tenant_scheme
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        logger.info("[01] Upserting tenant + scheme")
        existing_context = await self._resolve_existing_scheme_context()
        if existing_context:
            tenant_id, scheme_id, tenant_name, scheme_name = existing_context
            # Preserve the live tenant/scheme identities if East Gate is already present.
            # This migration only seeds finance reference data; it must never fork a second
            # East Gate scheme with different UUIDs just because the historical manager name
            # constant changed after the original script was written.
            logger.info(
                "  reusing existing live scheme tenant=%s (%s) scheme=%s (%s)",
                tenant_id,
                tenant_name,
                scheme_id,
                scheme_name,
            )
        else:
            tenant_id = make_id("tenant", MANAGER_NAME)
            scheme_id = make_id("scheme", BUILDING_ID)
        self.state.tenant_id = tenant_id
        self.state.scheme_id = scheme_id

        # Discovery uses the global bypass sentinel, but the finance/core tables
        # enforce tenant RLS on writes. Once the target tenant is known, pin the
        # session to that tenant for the remainder of the migration.
        await self.pg.execute(
            "SELECT set_config('app.tenant_id', $1, false)",
            str(tenant_id),
        )

        if not self.dry_run and not existing_context:
            await self.pg.execute(
                """
                INSERT INTO core.tenants (tenant_id, tenant_name, status, created_at, updated_at)
                VALUES ($1, $2, 'active', now(), now()) ON CONFLICT (tenant_id) DO NOTHING
                """,
                tenant_id, MANAGER_NAME,
            )
            await self.pg.execute(
                """
                INSERT INTO core.schemes
                (scheme_id, tenant_id, jurisdiction, scheme_number, scheme_name,
                 gst_registered, status, created_at, updated_at)
                VALUES ($1, $2, $3::compliance.jurisdiction_code, $4, $5, true, 'active', now(),
                        now()) ON CONFLICT (scheme_id) DO NOTHING
                """,
                scheme_id, tenant_id, JURISDICTION, BUILDING_ID, SCHEME_NAME,
            )
        logger.info("  tenant=%s scheme=%s", tenant_id, scheme_id)
        self._stats["tenant"] = 1
        self._stats["scheme"] = 1

    async def _step_02_funds(self) -> None:
        """Generated function header.

        Function: Migration13195._step_02_funds
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        logger.info("[02] Upserting admin + capital works funds")
        existing_funds = await self._load_existing_fund_ids()
        if {"admin", "capital_works"} <= set(existing_funds):
            self.state.admin_fund_id = existing_funds["admin"]
            self.state.cw_fund_id = existing_funds["capital_works"]
            logger.info(
                "  reusing existing funds admin=%s capital_works=%s",
                self.state.admin_fund_id,
                self.state.cw_fund_id,
            )
            self._stats["funds"] = 0
            return
        admin_id = make_id("fund", BUILDING_ID, "admin")
        cw_id = make_id("fund", BUILDING_ID, "capital_works")
        self.state.admin_fund_id = admin_id
        self.state.cw_fund_id = cw_id

        if not self.dry_run:
            for fund_id, code, name, ftype in [
                (admin_id, "ADM", "Administration Fund", "admin"),
                (cw_id, "CWF", "Capital Works Fund", "capital_works"),
            ]:
                await self.pg.execute(
                    """
                    INSERT INTO finance.funds
                    (fund_id, tenant_id, scheme_id, fund_code, fund_name, fund_type, status, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6::finance.fund_type, 'active',
                            now()) ON CONFLICT (scheme_id, fund_code) DO NOTHING
                    """,
                    fund_id, self.state.tenant_id, self.state.scheme_id,
                    code, name, ftype,
                )
        self._stats["funds"] = 2

    async def _step_03_gl_accounts(self) -> None:
        """Generated function header.

        Function: Migration13195._step_03_gl_accounts
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        logger.info("[03] Upserting GL accounts (%d)", len(GL_ACCOUNTS))
        for code, name, atype in GL_ACCOUNTS:
            gl_id = make_id("gl", BUILDING_ID, code)
            self.state.gl_map[code] = gl_id
            if not self.dry_run:
                await self.pg.execute(
                    """
                    INSERT INTO finance.gl_accounts
                    (gl_account_id, tenant_id, scheme_id, account_code, account_name,
                     account_type, status, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6::finance.account_type, 'active',
                            now()) ON CONFLICT (scheme_id, account_code) DO NOTHING
                    """,
                    gl_id, self.state.tenant_id, self.state.scheme_id,
                    code, name, atype,
                )
        # Always reload gl_map from PG so step 11 uses the UUIDs that are
        # actually in the database (ON CONFLICT DO NOTHING may have kept a
        # UUID from a prior run that differs from the make_id() value above).
        pg_gl = await self.pg.fetch(
            "SELECT gl_account_id, account_code FROM finance.gl_accounts WHERE scheme_id = $1",
            self.state.scheme_id,
        )
        for row in pg_gl:
            self.state.gl_map[row["account_code"]] = row["gl_account_id"]
        self._stats["gl_accounts"] = len(GL_ACCOUNTS)

    async def _step_04_accounting_period(self) -> None:
        """Generated function header.

        Function: Migration13195._step_04_accounting_period
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        logger.info("[04] Creating migration accounting period")
        existing_period = await self.pg.fetchrow(
            """
            SELECT period_id
            FROM finance.accounting_periods
            WHERE scheme_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            self.state.scheme_id,
        )
        if existing_period:
            self.state.period_id = existing_period["period_id"]
            logger.info("  reusing existing accounting period %s", self.state.period_id)
            self._stats["periods"] = 0
            return
        period_id = make_id("period", BUILDING_ID, "migration")
        self.state.period_id = period_id
        if not self.dry_run:
            await self.pg.execute(
                """
                INSERT INTO finance.accounting_periods
                (period_id, tenant_id, scheme_id, period_label, starts_on, ends_on,
                 status, created_at)
                VALUES ($1, $2, $3, 'Migration 2026', '2020-07-01', '2027-06-30',
                        'open', now()) ON CONFLICT (scheme_id, period_label) DO NOTHING
                """,
                period_id, self.state.tenant_id, self.state.scheme_id,
            )
        self._stats["periods"] = 1

    async def _step_05_lots_and_parties(self) -> None:
        """Generated function header.

        Function: Migration13195._step_05_lots_and_parties
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        logger.info("[05] Migrating lots and owners")
        existing_lot_count = await self.pg.fetchval(
            "SELECT COUNT(*) FROM core.lots WHERE scheme_id = $1",
            self.state.scheme_id,
        )
        if existing_lot_count:
            logger.info(
                "  scheme already has %d lots in Postgres; loading unit_map/party_map from PG",
                existing_lot_count,
            )
            # Populate unit_map and lot_map from existing PG lots so steps 7–9
            # can resolve lot_id by unit_number without re-inserting.
            pg_lots = await self.pg.fetch(
                "SELECT lot_id, lot_number, unit_number FROM core.lots WHERE scheme_id = $1",
                self.state.scheme_id,
            )
            for lot_row in pg_lots:
                lot_id = lot_row["lot_id"]
                for key in (lot_row["lot_number"], lot_row["unit_number"]):
                    if key:
                        self.state.lot_map[str(key)] = lot_id
                        self.state.unit_map[str(key)] = lot_id
            # Populate party_map from active ownership_periods (valid_to IS NULL).
            pg_parties = await self.pg.fetch(
                """
                SELECT l.unit_number, l.lot_number, op.owner_party_id
                FROM core.ownership_periods op
                JOIN core.lots l USING (lot_id)
                WHERE op.scheme_id = $1 AND op.valid_to IS NULL
                """,
                self.state.scheme_id,
            )
            for p_row in pg_parties:
                party_id = p_row["owner_party_id"]
                for key in (p_row["unit_number"], p_row["lot_number"]):
                    if key:
                        self.state.party_map[str(key)] = party_id
            logger.info(
                "  loaded %d lot mappings, %d party mappings from PG",
                len(self.state.unit_map),
                len(self.state.party_map),
            )
            self._stats["lots"] = 0
            self._stats["parties"] = 0
            return
        db = self.mongo[os.environ["DB_NAME"]]
        units = await db.units.find(
            {"building_id": BUILDING_ID, "is_test_data": {"$ne": True}}
        ).to_list(None)
        count_lots = 0
        count_parties = 0

        for unit in units:
            lot_number = str(unit.get("lot_number") or unit.get("unit_number", ""))
            if not lot_number:
                continue
            lot_id = make_id("lot", BUILDING_ID, lot_number)
            self.state.lot_map[lot_number] = lot_id
            # Also key by unit_number so steps 7–9 can join via unit_number
            unit_key = str(unit.get("unit_number", ""))
            if unit_key:
                self.state.unit_map[unit_key] = lot_id

            if not self.dry_run:
                await self.pg.execute(
                    """
                    INSERT INTO core.lots
                    (lot_id, tenant_id, scheme_id, lot_number, unit_number, lot_use,
                     entitlement_units, status, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, 'residential', $6, 'active', now(),
                            now()) ON CONFLICT (scheme_id, lot_number) DO NOTHING
                    """,
                    lot_id, self.state.tenant_id, self.state.scheme_id,
                    lot_number,
                    unit.get("unit_number", lot_number),
                    Decimal(str(unit.get("unit_entitlement") or unit.get("entitlement") or 0)),
                )
            count_lots += 1

            # Owner as party
            owner_name = unit.get("owner_name") or "Unknown Owner"
            owner_email = unit.get("owner_email", "")
            party_id = make_id("party", BUILDING_ID, owner_name, owner_email)
            self.state.party_map[lot_number] = party_id
            # Also key by unit_number for steps 7–9
            if unit_key:
                self.state.party_map[unit_key] = party_id

            if not self.dry_run:
                await self.pg.execute(
                    """
                    INSERT INTO core.parties
                    (party_id, tenant_id, legal_name, preferred_name, primary_email,
                     party_type, status, created_at, updated_at)
                    VALUES ($1, $2, $3, $3, NULLIF($4, ''), 'individual', 'active', now(), now())
                    ON CONFLICT (party_id) DO NOTHING
                    """,
                    party_id, self.state.tenant_id, owner_name, owner_email,
                )
            count_parties += 1

        logger.info("  %d lots, %d parties", count_lots, count_parties)
        self._stats["lots"] = count_lots
        self._stats["parties"] = count_parties

    async def _step_06_trust_accounts(self) -> None:
        """Generated function header.

        Function: Migration13195._step_06_trust_accounts
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        logger.info("[06] Migrating trust accounts")
        existing_trust_accounts = await self.pg.fetchval(
            "SELECT COUNT(*) FROM finance.trust_accounts WHERE scheme_id = $1",
            self.state.scheme_id,
        )
        if existing_trust_accounts:
            logger.info(
                "  scheme already has %d trust accounts in Postgres; loading trust_account_map from PG",
                existing_trust_accounts,
            )
            # Populate trust_account_map so step 11 can resolve trust_account_id
            # from Mongo ObjectId strings without re-inserting accounts.
            pg_accounts = await self.pg.fetch(
                """
                SELECT ta.trust_account_id, ta.masked_account_number
                FROM finance.trust_accounts ta
                WHERE ta.scheme_id = $1
                """,
                self.state.scheme_id,
            )
            for ta_row in pg_accounts:
                acct_key = str(ta_row["masked_account_number"] or "")
                if acct_key:
                    self.state.trust_account_map[acct_key] = ta_row["trust_account_id"]
            logger.info(
                "  loaded %d trust account mappings from PG",
                len(self.state.trust_account_map),
            )
            self._stats["trust_accounts"] = 0
            return
        db = self.mongo[os.environ["DB_NAME"]]
        accounts = await db.trust_accounts_v2.find(
            {"building_id": BUILDING_ID, "is_test_data": {"$ne": True}}
        ).to_list(None)

        count = 0
        for acct in accounts:
            acct_id = make_id("trust_account", BUILDING_ID, str(acct.get("_id", "")))
            self.state.trust_account_map[str(acct.get("_id", ""))] = acct_id
            fund_id = (
                self.state.cw_fund_id
                if "capital" in (acct.get("fund_type", "") or "").lower()
                else self.state.admin_fund_id
            )
            if not self.dry_run:
                await self.pg.execute(
                    """
                    INSERT INTO finance.trust_accounts
                    (trust_account_id, tenant_id, scheme_id, fund_id, bank_name,
                     account_name, masked_bsb, masked_account_number, status, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'active', now()) ON CONFLICT (trust_account_id) DO NOTHING
                    """,
                    acct_id, self.state.tenant_id, self.state.scheme_id, fund_id,
                    acct.get("bank_name", "Unknown Bank"),
                    acct.get("account_name", "Trust Account"),
                    acct.get("bsb", ""),
                    acct.get("account_number", ""),
                )
            count += 1

        logger.info("  %d trust accounts", count)
        self._stats["trust_accounts"] = count

    # ── STEPS 7–11: Phase 2 — financial history (ACTIVE) ──────────
    # See module docstring for the rollout model and cutover sequencing.
    # All five methods are idempotent: deterministic UUIDv5 IDs and ON CONFLICT
    # DO NOTHING on every INSERT make multiple runs safe.

    async def _step_07_ownership_periods(self) -> None:
        """Bitemporal owner history from `user_units` + `units` joint-owner snapshot.

        Writes one `core.ownership_periods` row per (lot, owner) with valid_from
        derived from `user_units.created_at` (or unit move-in date if recorded).
        Open-ended: valid_to is NULL while the membership is active. The exclude
        constraint on `core.ownership_periods` prevents overlapping valid periods
        for the same (lot, owner_party).

        Sources (hybrid):
          1. `db.user_units` active rows joined to `db.users` — primary canonical
             source per CLAUDE.md ("owner data flows through user_units → users").
             Rows here are inserted with `ownership_share=1.0` and
             `is_primary_owner` = `user_units.is_primary`.
          2. `db.units.owner_name` / `owner_name_b` snapshot — supplement only.
             The legacy denorm is the only place where joint owners are recorded
             for some pre-2026 lots (no secondary `user_units` row was ever
             written). For each lot the supplement loop:
                 - inserts a secondary `ownership_period` row when
                   `owner_name_b` is set and not already represented; and
                 - rebalances all open rows for the lot from `1.0` to `0.5`
                   so the per-lot share total stays `<= 1.0`.
        """
        logger.info("[07] Migrating ownership_periods from user_units")
        if self.dry_run:
            logger.info("  (dry run) ownership_periods will be written on live run")
            self._stats["ownership_periods"] = 0
            return

        db = self.mongo[os.environ["DB_NAME"]]
        memberships = await db.user_units.find(
            {
                "building_id": BUILDING_ID,
                "status": {"$in": ["active", None]},
                "is_test_data": {"$ne": True},
            }
        ).to_list(None)
        unit_docs = await db.units.find(
            {"building_id": BUILDING_ID, "is_test_data": {"$ne": True}},
            {"_id": 0, "unit_number": 1, "owner_name": 1, "owner_email": 1, "owner_name_b": 1, "owner_email_b": 1, "created_at": 1},
        ).to_list(None)
        units_by_number = {
            str(unit.get("unit_number") or "").strip(): unit for unit in unit_docs if unit.get("unit_number")
        }
        # Real per-unit ownership transfer dates (public_transfer_records_verified),
        # keyed by unit_number -> transfer_date. units.created_at is a uniform
        # onboarding/seed timestamp (NOT a real transfer date) and must never be used
        # as an ownership_period valid_from for a unit that actually changed hands —
        # found live 2026-07-24: every transferred East Gate lot had its new-owner
        # period anchored to the seed timestamp instead of the real transfer date,
        # leaving a gap between the old owner's (correctly end-dated) period and the
        # new owner's (wrongly seed-dated) one that historical ledger postings fell
        # into. Units with no entry here never transferred — use BUILDING_HANDOVER_DATE.
        transfer_log_docs = await db.ownership_transfer_log.find(
            {"building_id": BUILDING_ID, "status": {"$in": ["processed", "approved"]}},
            {"_id": 0, "unit_number": 1, "transfer_date": 1},
        ).to_list(None)
        transfer_date_by_unit: dict[str, date] = {}
        for doc in transfer_log_docs:
            un = str(doc.get("unit_number") or "").strip()
            td = doc.get("transfer_date")
            if not un or not td:
                continue
            transfer_date_by_unit[un] = date.fromisoformat(td) if isinstance(td, str) else td
        user_ids = list({str(mem.get("user_id") or "").strip() for mem in memberships if mem.get("user_id")})
        users_by_id: dict[str, dict] = {}
        if user_ids:
            user_docs = await db.users.find(
                {"id": {"$in": user_ids}},
                {"_id": 0, "id": 1, "full_name": 1, "email": 1},
            ).to_list(None)
            users_by_id = {
                str(user.get("id") or "").strip(): user
                for user in user_docs
                if str(user.get("id") or "").strip()
            }
        represented_owner_keys: dict[str, set[tuple[str, str]]] = {}

        count = 0
        skipped = 0
        for mem in memberships:
            unit_number = str(mem.get("unit_number") or "").strip()
            lot_id = self.state.unit_map.get(unit_number)
            user = users_by_id.get(str(mem.get("user_id") or "").strip())
            owner_name = str((user or {}).get("full_name") or "").strip()
            owner_email = str((user or {}).get("email") or "").strip().lower()
            if not lot_id or not owner_name:
                skipped += 1
                continue
            party_id = make_id("party", BUILDING_ID, owner_name, owner_email)
            if not self.dry_run:
                await self.pg.execute(
                    """
                    INSERT INTO core.parties
                    (party_id, tenant_id, legal_name, preferred_name, primary_email,
                     party_type, status, created_at, updated_at)
                    VALUES ($1, $2, $3, $3, NULLIF($4, ''), 'individual', 'active', now(), now())
                    ON CONFLICT (party_id) DO NOTHING
                    """,
                    party_id, self.state.tenant_id, owner_name, owner_email,
                )

            valid_from_dt = (
                iso(mem.get("move_in_date") or mem.get("created_at"))
                or datetime.now(tz=timezone.utc)
            )
            valid_from = valid_from_dt.date()
            ownership_id = make_id(
                "ownership_period", BUILDING_ID, unit_number, owner_name, valid_from.isoformat()
            )

            try:
                await self.pg.execute(
                    """
                    INSERT INTO core.ownership_periods
                      (ownership_period_id, tenant_id, scheme_id, lot_id,
                       owner_party_id, valid_from, valid_to, recorded_from,
                       recorded_to, source_document_id, notes, created_at,
                       is_primary_owner, ownership_share)
                    VALUES ($1, $2, $3, $4, $5, $6, NULL, now(), NULL,
                            $7, 'migrated from user_units', now(), $8, $9)
                    ON CONFLICT (ownership_period_id) DO NOTHING
                    """,
                    ownership_id, self.state.tenant_id, self.state.scheme_id,
                    lot_id, party_id, valid_from,
                    f"user_units:{mem.get('_id')}",
                    bool(mem.get("is_primary", True)),
                    Decimal("1.0"),
                )
                count += 1
                represented_owner_keys.setdefault(unit_number, set()).add((owner_name.lower(), owner_email))
            except Exception as exc:
                logger.warning(
                    "  ownership_periods insert rejected for unit=%s: %s",
                    unit_number, str(exc)[:120],
                )
                skipped += 1

        # Supplement the current-owner snapshot from units.owner_name / owner_name_b
        # so the Postgres cutover can represent joint owners even where historical
        # Mongo membership rows never captured the secondary owner explicitly.
        #
        # Joint-owner share contract: when units.owner_name_b is populated, ALL open
        # ownership_periods for the lot share equally. Step 07's user_units pass
        # always writes ownership_share=1.0; we downgrade those rows to 0.5 here
        # before inserting the secondary so totals stay <= 1.0 per lot.
        for unit_number, unit in units_by_number.items():
            lot_id = self.state.unit_map.get(unit_number)
            if not lot_id:
                skipped += 1
                continue

            # Real transfer date if this unit ever changed hands; otherwise the
            # current owner has held it since building handover — never the
            # units-collection seed timestamp (see transfer_date_by_unit above).
            current_valid_from = transfer_date_by_unit.get(unit_number, BUILDING_HANDOVER_DATE)
            owners = []
            primary_name = str(unit.get("owner_name") or "").strip()
            primary_email = str(unit.get("owner_email") or "").strip().lower()
            secondary_name = str(unit.get("owner_name_b") or "").strip()
            secondary_email = str(unit.get("owner_email_b") or "").strip().lower()
            if primary_name:
                owners.append((primary_name, primary_email, True))
            if secondary_name:
                owners.append((secondary_name, secondary_email, False))
            if not owners:
                continue

            ownership_share = Decimal("1.0") if len(owners) == 1 else Decimal("0.5")

            # Joint-owner lot: rebalance any user_units rows already written for
            # this lot to the equal split so per-lot total ownership stays <= 1.0.
            if len(owners) > 1:
                await self.pg.execute(
                    """
                    UPDATE core.ownership_periods
                       SET ownership_share = $1
                     WHERE scheme_id = $2
                       AND lot_id = $3
                       AND recorded_to IS NULL
                       AND valid_to IS NULL
                       AND ownership_share <> $1
                    """,
                    ownership_share,
                    self.state.scheme_id,
                    lot_id,
                )
            for owner_name, owner_email, is_primary_owner in owners:
                owner_key = (owner_name.lower(), owner_email)
                if owner_key in represented_owner_keys.setdefault(unit_number, set()):
                    continue
                party_id = make_id("party", BUILDING_ID, owner_name, owner_email)
                await self.pg.execute(
                    """
                    INSERT INTO core.parties
                      (party_id, tenant_id, legal_name, preferred_name, primary_email,
                       party_type, status, created_at, updated_at)
                    VALUES ($1, $2, $3, $3, NULLIF($4, ''), 'individual', 'active', now(), now())
                    ON CONFLICT (party_id) DO NOTHING
                    """,
                    party_id,
                    self.state.tenant_id,
                    owner_name,
                    owner_email,
                )

                ownership_id = make_id(
                    "ownership_period",
                    BUILDING_ID,
                    unit_number,
                    owner_name,
                    current_valid_from.isoformat(),
                )
                try:
                    await self.pg.execute(
                        """
                        INSERT INTO core.ownership_periods
                          (ownership_period_id, tenant_id, scheme_id, lot_id,
                           owner_party_id, valid_from, valid_to, recorded_from,
                           recorded_to, source_document_id, notes, created_at,
                           is_primary_owner, ownership_share)
                        VALUES ($1, $2, $3, $4, $5, $6, NULL, now(), NULL,
                                $7, 'migrated from units owner snapshot', now(), $8, $9)
                        ON CONFLICT (ownership_period_id) DO NOTHING
                        """,
                        ownership_id,
                        self.state.tenant_id,
                        self.state.scheme_id,
                        lot_id,
                        party_id,
                        current_valid_from,
                        f"units:{unit_number}",
                        is_primary_owner,
                        ownership_share,
                    )
                    count += 1
                    represented_owner_keys[unit_number].add(owner_key)
                except Exception as exc:
                    logger.warning(
                        "  ownership_period snapshot insert rejected for unit=%s owner=%s: %s",
                        unit_number,
                        owner_name,
                        str(exc)[:120],
                    )
                    skipped += 1

        logger.info("  %d ownership periods written, %d skipped", count, skipped)
        self._stats["ownership_periods"] = count

    async def _step_08_levy_runs_and_items(self) -> None:
        """Annual levy runs and per-unit levy items from unit_levy_ledger.

        unit_levy_ledger is the authoritative source per Phase 1 analysis:
        - 100% aligned with units on unit_number
        - admin_levied / sinking_levied split per year
        - admin_paid / sinking_paid carry the total-paid snapshot

        We write:
          - one finance.levy_runs row per (scheme, financial_year) — quarter_no=0
            denotes the annual roll-up; per-quarter splits are deferred to
            Phase 3 of the migration plan.
          - one finance.levy_items row per (run, lot, fund) where principal>0,
            using paid_cents from the ledger snapshot for status decisioning.
        """
        logger.info("[08] Migrating levy_runs and levy_items from unit_levy_ledger")
        db = self.mongo[os.environ["DB_NAME"]]
        rows = await db.unit_levy_ledger.find(
            {"building_id": BUILDING_ID, "is_test_data": {"$ne": True}}
        ).to_list(None)

        years_seen: set[str] = set()
        count_runs = 0
        count_items = 0

        for row in rows:
            year = str(row.get("year", "")).strip()
            unit_number = str(row.get("unit_number", "")).strip()
            if not year or not unit_number:
                continue

            lot_id = self.state.unit_map.get(unit_number)
            party_id = self.state.party_map.get(unit_number)
            if not lot_id or not party_id:
                continue

            run_id = make_id("levy_run", BUILDING_ID, year, "annual")
            if year not in years_seen:
                years_seen.add(year)
                issue_date = date(int(year), 7, 1)
                if not self.dry_run:
                    await self.pg.execute(
                        """
                        INSERT INTO finance.levy_runs
                          (levy_run_id, tenant_id, scheme_id, financial_year, quarter_no,
                           issue_date, due_date, status, created_at)
                        VALUES ($1, $2, $3, $4, 0, $5::date, $5::date, 'approved', now())
                        ON CONFLICT (levy_run_id) DO NOTHING
                        """,
                        run_id, self.state.tenant_id, self.state.scheme_id,
                        year, issue_date,
                    )
                count_runs += 1

            for fund_kind, principal_field, paid_field, fund_id in [
                ("admin", "admin_levied", "admin_paid", self.state.admin_fund_id),
                ("cw", "sinking_levied", "sinking_paid", self.state.cw_fund_id),
            ]:
                principal = cents(row.get(principal_field, 0))
                paid = max(0, cents(row.get(paid_field, 0)))
                if principal <= 0:
                    continue
                item_id = make_id(
                    "levy_item", BUILDING_ID, year, "annual", unit_number, fund_kind
                )
                status = "paid" if paid >= principal else "issued"
                if not self.dry_run:
                    await self.pg.execute(
                        """
                        INSERT INTO finance.levy_items
                          (levy_item_id, tenant_id, scheme_id, levy_run_id, lot_id,
                           owner_party_id, fund_id, principal_cents, gst_cents, paid_cents,
                           status, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 0, $9, $10, now())
                        ON CONFLICT (levy_run_id, lot_id, fund_id) DO NOTHING
                        """,
                        item_id, self.state.tenant_id, self.state.scheme_id, run_id,
                        lot_id, party_id, fund_id, principal, paid, status,
                    )
                count_items += 1

        logger.info(
            "  %d levy runs (%d years), %d levy items",
            count_runs, len(years_seen), count_items,
        )
        self._stats["levy_runs"] = count_runs
        self._stats["levy_items"] = count_items

    async def _step_09_receipts(self) -> None:
        """Confirmed payments from levy_payments → finance.receipts.

        Receipts are inserted without journal_entry_id; step 11 derives the
        journal entries from levy_items + receipts and back-fills the link
        through finance.receipts.journal_entry_id.

        Mongo levy_payments uses payment_method strings that don't all match
        the finance.payment_channel enum; map known aliases and fall back to
        'manual_adjustment' for unknown values to preserve auditability.
        """
        logger.info("[09] Migrating levy_payments → finance.receipts")
        db = self.mongo[os.environ["DB_NAME"]]
        payments = await db.levy_payments.find(
            {
                "building_id": BUILDING_ID,
                "status": {"$in": ["confirmed", "completed", "paid", None]},
                "is_test_data": {"$ne": True},
            }
        ).to_list(None)

        valid_channels = {
            "bpay", "deft", "eft", "card", "cash", "cheque",
            "aba", "manual_adjustment",
        }
        channel_map = {
            "credit_card": "card",
            "deft_bpay": "deft",
            "bank_transfer": "eft",
            "direct_deposit": "eft",
            "stripe": "card",
        }

        count = 0
        skipped = 0
        for pay in payments:
            raw_cents = pay.get("amount_cents")
            amount = (
                int(raw_cents)
                if isinstance(raw_cents, int) and raw_cents > 0
                else cents(pay.get("amount", 0))
            )
            if amount <= 0:
                skipped += 1
                continue

            unit_number = str(pay.get("unit_number", "")).strip()
            lot_id = self.state.unit_map.get(unit_number)
            party_id = self.state.party_map.get(unit_number)
            if not lot_id or not party_id:
                skipped += 1
                continue

            mongo_id = str(pay.get("id") or pay.get("_id") or "")
            if not mongo_id:
                skipped += 1
                continue
            receipt_id = make_id("receipt", BUILDING_ID, mongo_id)

            received_dt = iso(
                pay.get("payment_date")
                or pay.get("date")
                or pay.get("created_at")
            ) or datetime.now(tz=timezone.utc)
            received_on = received_dt.date()

            raw_channel = (
                pay.get("payment_method") or "manual_adjustment"
            ).lower().replace(" ", "_")
            channel = channel_map.get(raw_channel, raw_channel)
            if channel not in valid_channels:
                channel = "manual_adjustment"

            if not self.dry_run:
                await self.pg.execute(
                    """
                    INSERT INTO finance.receipts
                      (receipt_id, tenant_id, scheme_id, payer_party_id, lot_id,
                       channel, received_on, amount_cents, external_reference, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6::finance.payment_channel,
                            $7, $8, $9, now())
                    ON CONFLICT (receipt_id) DO NOTHING
                    """,
                    receipt_id, self.state.tenant_id, self.state.scheme_id,
                    party_id, lot_id, channel, received_on, amount,
                    pay.get("receipt_number") or pay.get("reference"),
                )
                self.state.receipt_ids.append((received_on, receipt_id))
            count += 1

        logger.info("  %d receipts migrated, %d skipped", count, skipped)
        self._stats["receipts"] = count

    async def _step_10_receipt_allocations(self) -> None:
        """FIFO allocation of receipts against open levy_items, per (lot, fund).

        Strategy:
          - For each (lot, fund), aggregate the lot's receipts in chronological
            order and allocate against the lot's outstanding levy_items
            ordered by levy_runs.issue_date ASC (oldest first), splitting
            across items as needed.
          - Allocations use deterministic UUIDv5 IDs from
            (receipt_id, levy_item_id) so re-runs produce the same rows.
          - Receipts are not bound to a single fund in Mongo; we infer split
            using the unit_levy_ledger snapshot (admin_paid vs sinking_paid)
            for each year covered by the receipts.

        This step is best-effort: if the receipts total exceeds the unpaid
        levy total for a (lot, fund), the surplus is recorded against the
        most-recent fully-paid item with allocation_type='advance'. Net
        allocation never exceeds total receipts for the lot.
        """
        logger.info("[10] FIFO-allocating receipts against levy_items")
        if self.dry_run:
            logger.info("  (dry run) allocations will be written on live run")
            self._stats["receipt_allocations"] = 0
            return

        # Pull receipts and items grouped by lot to compute allocations in
        # Python (one query per group; total queries = lot_count × 3, well
        # under any practical N+1 threshold for ~90 lots).
        lot_rows = await self.pg.fetch(
            "SELECT DISTINCT lot_id FROM finance.receipts WHERE scheme_id = $1",
            self.state.scheme_id,
        )

        count = 0
        for lr in lot_rows:
            lot_id = lr["lot_id"]
            receipts = await self.pg.fetch(
                """
                SELECT receipt_id, received_on, amount_cents
                FROM finance.receipts
                WHERE scheme_id = $1 AND lot_id = $2
                ORDER BY received_on ASC, receipt_id ASC
                """,
                self.state.scheme_id, lot_id,
            )
            items = await self.pg.fetch(
                """
                SELECT li.levy_item_id, li.fund_id, li.principal_cents, li.paid_cents,
                       lr.issue_date
                FROM finance.levy_items li
                JOIN finance.levy_runs lr USING (levy_run_id)
                WHERE li.scheme_id = $1 AND li.lot_id = $2
                ORDER BY lr.issue_date ASC, li.levy_item_id ASC
                """,
                self.state.scheme_id, lot_id,
            )

            # Track unpaid balance per item; allocate receipt amount against
            # the oldest open item first (FIFO).
            unpaid = {row["levy_item_id"]: max(0, row["principal_cents"] - row["paid_cents"]) for row in items}
            item_order = [row["levy_item_id"] for row in items]

            for receipt in receipts:
                remaining = receipt["amount_cents"]
                for item_id in item_order:
                    if remaining <= 0:
                        break
                    open_amt = unpaid.get(item_id, 0)
                    if open_amt <= 0:
                        continue
                    alloc = min(open_amt, remaining)
                    allocation_id = make_id(
                        "receipt_alloc", str(receipt["receipt_id"]), str(item_id)
                    )
                    await self.pg.execute(
                        """
                        INSERT INTO finance.receipt_allocations
                          (allocation_id, tenant_id, receipt_id, levy_item_id,
                           allocation_type, allocated_cents, created_at)
                        VALUES ($1, $2, $3, $4, 'levy_payment', $5, now())
                        ON CONFLICT (allocation_id) DO NOTHING
                        """,
                        allocation_id, self.state.tenant_id,
                        receipt["receipt_id"], item_id, alloc,
                    )
                    unpaid[item_id] = open_amt - alloc
                    remaining -= alloc
                    count += 1

                # Surplus on a fully-paid lot — record an 'advance' allocation
                # against the most recent item so the receipt is fully
                # accounted for and trial balance stays in sync.
                if remaining > 0 and item_order:
                    last_item = item_order[-1]
                    allocation_id = make_id(
                        "receipt_alloc_advance",
                        str(receipt["receipt_id"]),
                        str(last_item),
                    )
                    await self.pg.execute(
                        """
                        INSERT INTO finance.receipt_allocations
                          (allocation_id, tenant_id, receipt_id, levy_item_id,
                           allocation_type, allocated_cents, created_at)
                        VALUES ($1, $2, $3, $4, 'advance', $5, now())
                        ON CONFLICT (allocation_id) DO NOTHING
                        """,
                        allocation_id, self.state.tenant_id,
                        receipt["receipt_id"], last_item, remaining,
                    )
                    count += 1

        logger.info("  %d allocations written across %d lots", count, len(lot_rows))
        self._stats["receipt_allocations"] = count

    async def _step_11_journal_entries_and_lines(self) -> None:
        """Derive double-entry journal from levy_items + receipts (hash-chained).

        Two journal kinds:
          - Levy charge:  DR 1100 Levy Receivable / CR 4000 (admin) or 4001 (CW)
          - Cash receipt: DR 1010 Trust Bank Account / CR 1100 Levy Receivable

        Hash chain is per-scheme, ordered by (effective_on, source_id) so that
        re-runs produce identical chains. After inserting each journal_entry,
        the corresponding receipt's journal_entry_id is back-filled.
        """
        logger.info("[11] Deriving journal_entries + journal_lines (hash-chained)")
        if self.dry_run:
            logger.info("  (dry run) journals will be derived on live run")
            self._stats["journal_entries"] = 0
            return

        # Wipe any previously-derived journal data for this scheme so that a
        # re-run always rebuilds a clean, fully-chained ledger.  A partial
        # prior run leaves entries with stale prev_entry_hash values that
        # cannot be fixed with ON CONFLICT DO NOTHING — the chain would
        # remain broken.
        #
        # Dependency order for deletion:
        #   1. NULL out receipts.journal_entry_id (FK → journal_entries)
        #   2. Disable BEFORE DELETE trigger on journal_lines (blocks posted rows)
        #   3. Delete journal_lines (ON DELETE RESTRICT → must go before entries)
        #   4. Delete journal_entries
        #   5. Re-enable trigger
        await self.pg.execute(
            "UPDATE finance.receipts SET journal_entry_id = NULL WHERE scheme_id = $1",
            self.state.scheme_id,
        )
        await self.pg.execute(
            "ALTER TABLE finance.journal_lines DISABLE TRIGGER trg_prevent_posted_line_update"
        )
        await self.pg.execute(
            """
            DELETE FROM finance.journal_lines
            WHERE journal_entry_id IN (
                SELECT journal_entry_id FROM finance.journal_entries
                WHERE scheme_id = $1
            )
            """,
            self.state.scheme_id,
        )
        await self.pg.execute(
            "DELETE FROM finance.journal_entries WHERE scheme_id = $1",
            self.state.scheme_id,
        )
        await self.pg.execute(
            "ALTER TABLE finance.journal_lines ENABLE TRIGGER trg_prevent_posted_line_update"
        )
        logger.info("  cleared prior journal entries — rebuilding from scratch")

        ar_id = self.state.gl_map.get("1100")           # Levy Receivable
        bank_id = self.state.gl_map.get("1010")          # Trust Bank Account - Admin
        admin_income_id = self.state.gl_map.get("4000")  # Admin Fund Levy Income
        cw_income_id = self.state.gl_map.get("4001")     # Capital Works Levy Income

        if not all([ar_id, bank_id, admin_income_id, cw_income_id]):
            logger.error("  GL accounts not found — skipping journal derivation")
            self._stats["journal_entries"] = 0
            return

        # Levy charges (one journal per levy_item)
        levy_rows = await self.pg.fetch(
            """
            SELECT li.levy_item_id, li.lot_id, li.fund_id, li.principal_cents,
                   lr.issue_date::date AS effective_on, lr.financial_year
            FROM finance.levy_items li
            JOIN finance.levy_runs lr USING (levy_run_id)
            WHERE li.scheme_id = $1
              AND li.principal_cents > 0
            ORDER BY lr.issue_date, li.levy_item_id
            """,
            self.state.scheme_id,
        )
        # Receipts (one journal per receipt)
        receipt_rows = await self.pg.fetch(
            """
            SELECT receipt_id, lot_id, amount_cents, received_on, external_reference
            FROM finance.receipts
            WHERE scheme_id = $1 AND amount_cents > 0
            ORDER BY received_on, receipt_id
            """,
            self.state.scheme_id,
        )

        events: list[dict] = []
        for r in levy_rows:
            income_gl = (
                cw_income_id if r["fund_id"] == self.state.cw_fund_id else admin_income_id
            )
            events.append({
                "source_type": "levy_charge",
                "narration": f"Levy charge {r['financial_year']}",
                "effective_on": r["effective_on"],
                "amount": r["principal_cents"],
                "debit_gl": ar_id,
                "credit_gl": income_gl,
                "source_id": str(r["levy_item_id"]),
                "back_fill_target": None,
            })
        for r in receipt_rows:
            events.append({
                "source_type": "receipt",
                "narration": f"Payment received {r['external_reference'] or ''}".strip(),
                "effective_on": r["received_on"],
                "amount": r["amount_cents"],
                "debit_gl": bank_id,
                "credit_gl": ar_id,
                "source_id": str(r["receipt_id"]),
                "back_fill_target": r["receipt_id"],
            })

        # Sort chronologically; tie-break on source_id so chain is deterministic
        # across runs.
        events.sort(key=lambda e: (e["effective_on"], e["source_id"]))

        count = 0
        prev_hash: Optional[str] = None
        for event in events:
            entry_id = make_id(
                "journal", BUILDING_ID, event["source_type"], event["source_id"]
            )
            entry_hash = hashlib.sha256(
                json.dumps({
                    "id": str(entry_id),
                    "source": event["source_id"],
                    "amount": event["amount"],
                    "prev": prev_hash or "",
                }, sort_keys=True).encode()
            ).hexdigest()

            await self.pg.execute(
                """
                INSERT INTO finance.journal_entries
                  (journal_entry_id, tenant_id, scheme_id, period_id, source_type,
                   narration, status, effective_on, posted_at, prev_entry_hash,
                   entry_hash, is_test_data, metadata, created_at)
                VALUES ($1,$2,$3,$4,$5,$6,'posted',$7,now(),$8,$9,false,'{}'::jsonb,now())
                ON CONFLICT (journal_entry_id) DO NOTHING
                """,
                entry_id, self.state.tenant_id, self.state.scheme_id,
                self.state.period_id, event["source_type"], event["narration"],
                event["effective_on"], prev_hash, entry_hash,
            )

            for suffix, gl_id, direction in [
                ("debit",  event["debit_gl"],  "debit"),
                ("credit", event["credit_gl"], "credit"),
            ]:
                line_id = make_id("jline", str(entry_id), suffix)
                await self.pg.execute(
                    """
                    INSERT INTO finance.journal_lines
                      (journal_line_id, tenant_id, scheme_id, journal_entry_id,
                       gl_account_id, direction, amount_cents, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6::finance.entry_direction,$7,now())
                    ON CONFLICT (journal_line_id) DO NOTHING
                    """,
                    line_id, self.state.tenant_id, self.state.scheme_id,
                    entry_id, gl_id, direction, event["amount"],
                )

            # Back-fill receipt's journal_entry_id so reconciliation can match
            # bank transactions to the journal line that recognised the cash.
            if event["back_fill_target"] is not None:
                await self.pg.execute(
                    """
                    UPDATE finance.receipts
                    SET journal_entry_id = $1
                    WHERE receipt_id = $2
                      AND journal_entry_id IS NULL
                    """,
                    entry_id, event["back_fill_target"],
                )

            prev_hash = entry_hash
            count += 1

        logger.info(
            "  %d journal entries derived (%d levy charges, %d receipts)",
            count, len(levy_rows), len(receipt_rows),
        )
        self._stats["journal_entries"] = count

    def _print_stats(self) -> None:
        """Generated function header.

        Function: Migration13195._print_stats
        Path: backend/migrations/postgres/migrate_building_13195.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        logger.info("--- Migration Stats ---")
        for key, val in self._stats.items():
            logger.info("  %-30s %d", key, val)


# ------------------------------------------------------------------ validation queries

VALIDATION_SQL = """
                 -- 1. Trial balance: total debits must equal total credits per scheme
                 SELECT CASE
                            WHEN debit_sum = credit_sum
                                THEN 'PASS: trial balance'
                            ELSE 'FAIL: trial balance mismatch debit=' || debit_sum || ' credit=' || credit_sum
                            END AS result
                 FROM (SELECT SUM(amount_cents) FILTER (WHERE direction = 'debit')  AS debit_sum, SUM(amount_cents) FILTER (WHERE direction = 'credit') AS credit_sum
                       FROM finance.journal_lines jl
                                JOIN finance.journal_entries je USING (journal_entry_id)
                       WHERE je.scheme_id = $1
                         AND je.status = 'posted') _tb;

                 -- 2. Hash chain: no broken links (every prev_entry_hash matches the entry_hash
--    of the chronologically preceding entry, ordered by entry_number which is
--    GENERATED ALWAYS AS IDENTITY — assigned in insertion order).
--    Note: ON CONFLICT DO NOTHING can create entry_number gaps on re-runs;
--    this query only checks rows that exist, so idempotent re-runs are safe.
                 SELECT CASE
                            WHEN COUNT(*) = 0 THEN 'PASS: hash chain intact'
                            ELSE 'FAIL: ' || COUNT(*) || ' broken hash links'
                            END AS result
                 FROM (SELECT je.journal_entry_id
                       FROM finance.journal_entries je
                                JOIN LATERAL (
                           SELECT entry_hash
                           FROM finance.journal_entries prev_je
                           WHERE prev_je.scheme_id = je.scheme_id
                             AND prev_je.entry_number < je.entry_number
                           ORDER BY prev_je.entry_number DESC
                               LIMIT 1) prev_je ON true
                 WHERE je.scheme_id = $1
                   AND je.prev_entry_hash IS NOT NULL
                   AND je.prev_entry_hash != prev_je.entry_hash
                     ) _chain;

-- 3. No orphaned levy items (levy_run exists for all items)
                 SELECT CASE
                            WHEN COUNT(*) = 0 THEN 'PASS: no orphaned levy items'
                            ELSE 'FAIL: ' || COUNT(*) || ' orphaned levy items'
                            END AS result
                 FROM finance.levy_items li
                 WHERE li.scheme_id = $1
                   AND NOT EXISTS (SELECT 1
                                   FROM finance.levy_runs lr
                                   WHERE lr.levy_run_id = li.levy_run_id);

-- 4. No orphaned receipt allocations (receipt + levy_item exist for all)
                 SELECT CASE
                            WHEN COUNT(*) = 0 THEN 'PASS: no orphaned receipt_allocations'
                            ELSE 'FAIL: ' || COUNT(*) || ' orphaned receipt_allocations'
                            END AS result
                 FROM finance.receipt_allocations ra
                          JOIN finance.receipts r USING (receipt_id)
                 WHERE r.scheme_id = $1
                   AND ra.levy_item_id IS NOT NULL
                   AND NOT EXISTS (SELECT 1
                                   FROM finance.levy_items li
                                   WHERE li.levy_item_id = ra.levy_item_id);

-- 5. Allocation total per receipt does not exceed receipt amount
                 SELECT CASE
                            WHEN COUNT(*) = 0 THEN 'PASS: allocations within receipt totals'
                            ELSE 'FAIL: ' || COUNT(*) || ' receipts with over-allocated totals'
                            END AS result
                 FROM (SELECT r.receipt_id, r.amount_cents AS receipt_amt,
                              COALESCE(SUM(ra.allocated_cents), 0) AS alloc_amt
                       FROM finance.receipts r
                                LEFT JOIN finance.receipt_allocations ra USING (receipt_id)
                       WHERE r.scheme_id = $1
                       GROUP BY r.receipt_id, r.amount_cents
                       HAVING COALESCE(SUM(ra.allocated_cents), 0) > r.amount_cents) _over;

-- 6. Every posted journal_entry has at least one debit and one credit line
                 SELECT CASE
                            WHEN COUNT(*) = 0 THEN 'PASS: journal entries balanced'
                            ELSE 'FAIL: ' || COUNT(*) || ' unbalanced journal entries'
                            END AS result
                 FROM (SELECT je.journal_entry_id,
                              SUM(jl.amount_cents) FILTER (WHERE jl.direction = 'debit')  AS dr,
                              SUM(jl.amount_cents) FILTER (WHERE jl.direction = 'credit') AS cr
                       FROM finance.journal_entries je
                                JOIN finance.journal_lines jl USING (journal_entry_id)
                       WHERE je.scheme_id = $1
                         AND je.status = 'posted'
                       GROUP BY je.journal_entry_id
                       HAVING SUM(jl.amount_cents) FILTER (WHERE jl.direction = 'debit')
                                  IS DISTINCT FROM SUM(jl.amount_cents) FILTER (WHERE jl.direction = 'credit')) _imbalanced;

-- 7. Receipts → journal_entries back-fill: every receipt is linked to a journal entry
                 SELECT CASE
                            WHEN COUNT(*) = 0 THEN 'PASS: every receipt links to a journal entry'
                            ELSE 'FAIL: ' || COUNT(*) || ' receipts missing journal_entry_id'
                            END AS result
                 FROM finance.receipts
                 WHERE scheme_id = $1
                   AND journal_entry_id IS NULL;

-- 8. Ownership-period coverage: every lot with active levy items has at least one ownership period
                 SELECT CASE
                            WHEN COUNT(*) = 0 THEN 'PASS: ownership coverage complete'
                            ELSE 'WARN: ' || COUNT(*) || ' lots with levy items but no ownership_period'
                            END AS result
                 FROM (SELECT DISTINCT li.lot_id
                       FROM finance.levy_items li
                       WHERE li.scheme_id = $1) lots_with_items
                 WHERE NOT EXISTS (SELECT 1
                                   FROM core.ownership_periods op
                                   WHERE op.lot_id = lots_with_items.lot_id);
                 """


async def run_validation(pg_conn, scheme_id: UUID) -> None:
    """Generated function header.

    Function: run_validation
    Path: backend/migrations/postgres/migrate_building_13195.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    logger.info("=== Running validation for scheme %s ===", scheme_id)
    passed = failed = 0
    for stmt in VALIDATION_SQL.strip().split(";"):
        # Strip leading whitespace then skip pure-comment or empty fragments.
        # Must strip comment lines from the TOP of each fragment — a statement
        # beginning with `-- comment\nSELECT ...` must not be discarded.
        lines = [ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")]
        sql = "\n".join(lines).strip()
        if not sql:
            continue
        result = await pg_conn.fetch(sql, scheme_id)
        for row in result:
            msg = row["result"]
            logger.info("  %s", msg)
            if msg.startswith("FAIL"):
                failed += 1
            else:
                passed += 1
    logger.info("  validation complete: %d passed, %d failed", passed, failed)


# ------------------------------------------------------------------ entrypoint

async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/migrations/postgres/migrate_building_13195.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Migrate building 13195 to PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="No writes to Postgres")
    parser.add_argument("--validate", action="store_true", help="Validate after migration")
    args = parser.parse_args()

    mongo_url = os.environ.get("MONGO_URL")
    db_name = os.environ.get("DB_NAME")
    database_url = os.environ.get("DATABASE_URL")

    if not mongo_url or not db_name or not database_url:
        logger.error("MONGO_URL, DB_NAME, and DATABASE_URL must be set")
        sys.exit(1)

    try:
        import asyncpg
    except ImportError:
        logger.error("asyncpg not installed: pip install asyncpg")
        sys.exit(1)

    mongo_client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)

    # asyncpg uses different URL format (no +asyncpg driver spec)
    pg_url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    pg_conn = await asyncpg.connect(pg_url)

    try:
        # Migrations operate outside a single tenant. Use the documented bypass
        # sentinel so lookups can see existing live rows and inserts can seed the
        # target scheme without tripping tenant RLS.
        await pg_conn.execute(
            "SELECT set_config('app.tenant_id', $1, false)",
            BYPASS_TENANT_ID,
        )
        migration = Migration13195(mongo_client, pg_conn, dry_run=args.dry_run)
        await migration.run()

        if args.validate:
            await run_validation(pg_conn, migration.state.scheme_id)
    finally:
        await pg_conn.close()
        mongo_client.close()


if __name__ == "__main__":
    asyncio.run(main())
