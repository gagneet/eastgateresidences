from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Sequence

import asyncpg
from motor.motor_asyncio import AsyncIOMotorClient

from .checkpoints import CheckpointStore
from .ids import deterministic_uuid, mongo_source_token
from .reporting import MigrationReportWriter


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/migrations/postgres/framework/runtime.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def _coerce_date(value: Any) -> date:
    """Generated function header.

    Function: _coerce_date
    Path: backend/migrations/postgres/framework/runtime.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if value in (None, ""):
        return date.today()
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _json_ready(value: Any) -> Any:
    """Generated function header.

    Function: _json_ready
    Path: backend/migrations/postgres/framework/runtime.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_ready(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


@dataclass
class MigrationOptions:
    building_id: str
    scheme_number: str | None = None
    mongo_dsn: str | None = None
    mongo_db_name: str | None = None
    pg_dsn: str | None = None
    batch_size: int = 250
    dry_run: bool = False
    validate_only: bool = False
    # Month (1–12) the financial year starts. East Gate = 1 (Jan–Dec).
    # Default 7 matches the ATO standard (Jul–Jun) for other schemes.
    fy_start_month: int = 7
    output_dir: Path = Path("backend/migrations/postgres/runtime")
    checkpoint_dir: Path = Path("backend/migrations/postgres/runtime/checkpoints")
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @classmethod
    def from_env(cls, *, building_id: str, **kwargs: Any) -> "MigrationOptions":
        """Generated function header.

        Function: MigrationOptions.from_env
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return cls(
            building_id=building_id,
            scheme_number=kwargs.get("scheme_number"),
            mongo_dsn=kwargs.get("mongo_dsn") or os.getenv("MONGO_URL"),
            mongo_db_name=kwargs.get("mongo_db_name") or os.getenv("DB_NAME"),
            pg_dsn=kwargs.get("pg_dsn") or os.getenv("DATABASE_URL"),
            batch_size=kwargs.get("batch_size", 250),
            dry_run=kwargs.get("dry_run", False),
            validate_only=kwargs.get("validate_only", False),
            fy_start_month=int(kwargs.get("fy_start_month", 7)),
            output_dir=Path(kwargs.get("output_dir", "backend/migrations/postgres/runtime")),
            checkpoint_dir=Path(kwargs.get("checkpoint_dir", "backend/migrations/postgres/runtime/checkpoints")),
        )


@dataclass
class MigrationContext:
    tenant_id: uuid.UUID
    scheme_id: uuid.UUID
    scheme_number: str
    building_id: str
    lot_by_unit_number: dict[str, uuid.UUID] = field(default_factory=dict)
    lot_by_lot_number: dict[str, uuid.UUID] = field(default_factory=dict)
    owner_by_lot_id: dict[uuid.UUID, uuid.UUID] = field(default_factory=dict)
    fund_ids: dict[str, uuid.UUID] = field(default_factory=dict)
    gl_accounts: dict[str, uuid.UUID] = field(default_factory=dict)
    periods: dict[str, uuid.UUID] = field(default_factory=dict)
    trust_accounts: dict[str, uuid.UUID] = field(default_factory=dict)


class AsyncpgMigrationAdapter:
    def __init__(self, connection: asyncpg.Connection):
        """Generated function header.

        Function: AsyncpgMigrationAdapter.__init__
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.connection = connection

    @classmethod
    async def connect(cls, dsn: str) -> "AsyncpgMigrationAdapter":
        """Generated function header.

        Function: AsyncpgMigrationAdapter.connect
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        connection = await asyncpg.connect(dsn)
        return cls(connection)

    async def close(self) -> None:
        """Generated function header.

        Function: AsyncpgMigrationAdapter.close
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        await self.connection.close()

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        """Generated function header.

        Function: AsyncpgMigrationAdapter.fetch
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return await self.connection.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        """Generated function header.

        Function: AsyncpgMigrationAdapter.fetchrow
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return await self.connection.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        """Generated function header.

        Function: AsyncpgMigrationAdapter.fetchval
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return await self.connection.fetchval(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        """Generated function header.

        Function: AsyncpgMigrationAdapter.execute
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return await self.connection.execute(query, *args)

    def transaction(self) -> Any:
        """Generated function header.

        Function: AsyncpgMigrationAdapter.transaction
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return self.connection.transaction()

    async def ensure_row(
        self,
        *,
        table: str,
        row: dict[str, Any],
        pk_col: str,
        conflict_cols: Sequence[str],
        casts: dict[str, str] | None = None,
    ) -> tuple[Any, bool]:
        """Generated function header.

        Function: AsyncpgMigrationAdapter.ensure_row
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        casts = casts or {}
        columns = list(row.keys())
        values = [self._encode_param(row[column]) for column in columns]
        placeholders = []
        for index, column in enumerate(columns, start=1):
            suffix = f"::{casts[column]}" if column in casts else ""
            placeholders.append(f"${index}{suffix}")
        insert_sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(placeholders)}) "
            f"ON CONFLICT ({', '.join(conflict_cols)}) DO NOTHING "
            f"RETURNING {pk_col}"
        )
        inserted = await self.fetchrow(insert_sql, *values)
        if inserted is not None:
            return inserted[pk_col], True
        where_values = [self._encode_param(row[column]) for column in conflict_cols]
        where_clause = " AND ".join(f"{column} = ${idx}" for idx, column in enumerate(conflict_cols, start=1))
        select_sql = f"SELECT {pk_col} FROM {table} WHERE {where_clause}"
        existing = await self.fetchrow(select_sql, *where_values)
        if existing is None:
            raise RuntimeError(f"Unable to resolve existing row for {table} on conflict {conflict_cols}")
        return existing[pk_col], False

    @staticmethod
    def _encode_param(value: Any) -> Any:
        """Generated function header.

        Function: AsyncpgMigrationAdapter._encode_param
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if isinstance(value, (dict, list)):
            return json.dumps(_json_ready(value))
        return value


class BaseBuildingMigration:
    script_name = "base_migration"

    def __init__(
        self,
        *,
        options: MigrationOptions,
        mongo_client: AsyncIOMotorClient,
        pg: AsyncpgMigrationAdapter,
    ) -> None:
        """Generated function header.

        Function: BaseBuildingMigration.__init__
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.options = options
        self.mongo_client = mongo_client
        self.mongo_db = mongo_client[options.mongo_db_name or ""]
        self.pg = pg
        self.report = MigrationReportWriter(
            script_name=self.script_name,
            building_id=options.building_id,
            run_id=options.run_id,
            output_dir=options.output_dir,
            dry_run=options.dry_run,
        )
        checkpoint_path = options.checkpoint_dir / f"{options.run_id}.json"
        self.checkpoints = CheckpointStore(checkpoint_path)
        self.context: MigrationContext | None = None

    async def prepare(self) -> MigrationContext:
        """Generated function header.

        Function: BaseBuildingMigration.prepare
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        scheme_number = self.options.scheme_number or self.options.building_id
        scheme = await self.pg.fetchrow(
            """
            SELECT scheme_id, tenant_id, scheme_number
            FROM core.schemes
            WHERE scheme_number = $1
            """,
            scheme_number,
        )
        if scheme is None:
            raise RuntimeError(f"Could not find core.schemes row for scheme_number={scheme_number}")

        context = MigrationContext(
            tenant_id=scheme["tenant_id"],
            scheme_id=scheme["scheme_id"],
            scheme_number=scheme["scheme_number"],
            building_id=self.options.building_id,
        )

        lot_rows = await self.pg.fetch(
            """
            SELECT lot_id, unit_number, lot_number
            FROM core.lots
            WHERE scheme_id = $1
            """,
            context.scheme_id,
        )
        for row in lot_rows:
            if row["unit_number"]:
                context.lot_by_unit_number[str(row["unit_number"]).strip()] = row["lot_id"]
            if row["lot_number"]:
                context.lot_by_lot_number[str(row["lot_number"]).strip()] = row["lot_id"]

        owner_rows = await self.pg.fetch(
            """
            SELECT lot_id, owner_party_id
            FROM (
                SELECT op.lot_id,
                       op.owner_party_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY op.lot_id
                           ORDER BY op.is_primary_owner DESC, op.valid_from DESC, op.created_at DESC
                       ) AS rn
                FROM core.ownership_periods op
                WHERE op.scheme_id = $1
                  AND op.recorded_to IS NULL
            ) ranked
            WHERE rn = 1
            """,
            context.scheme_id,
        )
        context.owner_by_lot_id = {row["lot_id"]: row["owner_party_id"] for row in owner_rows}

        self.context = context
        self.checkpoints.start_run(
            run_id=self.options.run_id,
            script=self.script_name,
            building_id=self.options.building_id,
            dry_run=self.options.dry_run,
        )
        return context

    def deterministic_id(self, *parts: Any) -> uuid.UUID:
        """Generated function header.

        Function: BaseBuildingMigration.deterministic_id
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return deterministic_uuid(self.options.building_id, self.script_name, *parts)

    async def iter_collection_batches(
        self,
        *,
        collection_name: str,
        query: dict[str, Any],
        phase: str,
        projection: dict[str, Any] | None = None,
        sort_field: str = "_id",
    ) -> AsyncIterator[tuple[int, list[dict[str, Any]]]]:
        """Generated function header.

        Function: BaseBuildingMigration.iter_collection_batches
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        collection = self.mongo_db[collection_name]
        cursor = collection.find(query, projection)
        if hasattr(cursor, "sort"):
            cursor = cursor.sort(sort_field, 1)
        if hasattr(cursor, "batch_size"):
            cursor = cursor.batch_size(self.options.batch_size)

        batch: list[dict[str, Any]] = []
        batch_index = 0
        if hasattr(cursor, "__aiter__"):
            async for document in cursor:
                batch.append(document)
                if len(batch) >= self.options.batch_size:
                    yield batch_index, batch
                    batch_index += 1
                    batch = []
            if batch:
                yield batch_index, batch
            return

        documents = await cursor.to_list(None)
        for index in range(0, len(documents), self.options.batch_size):
            yield batch_index, documents[index:index + self.options.batch_size]
            batch_index += 1

    async def ensure_period(self, *, label: str, starts_on: date, ends_on: date) -> uuid.UUID:
        """Generated function header.

        Function: BaseBuildingMigration.ensure_period
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        if label in self.context.periods:
            return self.context.periods[label]
        period_id, inserted = await self.pg.ensure_row(
            table="finance.accounting_periods",
            pk_col="period_id",
            conflict_cols=("scheme_id", "period_label"),
            row={
                "period_id": self.deterministic_id("period", label),
                "tenant_id": self.context.tenant_id,
                "scheme_id": self.context.scheme_id,
                "period_label": label,
                "starts_on": starts_on,
                "ends_on": ends_on,
                "status": "open",
            },
        )
        self.context.periods[label] = period_id
        self.report.record_trace(
            status="migrated" if inserted else "skipped",
            target_table="finance.accounting_periods",
            target_id=str(period_id),
            source_collection="virtual.period",
            source_id=label,
            phase="periods",
        )
        return period_id

    async def load_fund_ids(self) -> dict[str, uuid.UUID]:
        """Generated function header.

        Function: BaseBuildingMigration.load_fund_ids
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        rows = await self.pg.fetch(
            """
            SELECT fund_id, fund_type
            FROM finance.funds
            WHERE scheme_id = $1
            """,
            self.context.scheme_id,
        )
        self.context.fund_ids = {row["fund_type"]: row["fund_id"] for row in rows}
        return self.context.fund_ids

    async def load_gl_accounts(self) -> dict[str, uuid.UUID]:
        """Generated function header.

        Function: BaseBuildingMigration.load_gl_accounts
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        rows = await self.pg.fetch(
            """
            SELECT gl_account_id, account_code
            FROM finance.gl_accounts
            WHERE scheme_id = $1
            """,
            self.context.scheme_id,
        )
        self.context.gl_accounts = {row["account_code"]: row["gl_account_id"] for row in rows}
        return self.context.gl_accounts

    async def load_trust_account_map(self) -> dict[str, uuid.UUID]:
        """Generated function header.

        Function: BaseBuildingMigration.load_trust_account_map
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        rows = await self.pg.fetch(
            """
            SELECT trust_account_id, external_uid
            FROM finance.trust_accounts
            WHERE scheme_id = $1
            """,
            self.context.scheme_id,
        )
        self.context.trust_accounts = {
            str(row["external_uid"]).strip(): row["trust_account_id"]
            for row in rows
            if row["external_uid"]
        }
        return self.context.trust_accounts

    def resolve_trust_account_id(self, document: dict[str, Any]) -> uuid.UUID | None:
        """Generated function header.

        Function: BaseBuildingMigration.resolve_trust_account_id
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        candidates = (
            document.get("trust_account_id"),
            document.get("account_id"),
            document.get("trust_account_external_uid"),
        )
        for candidate in candidates:
            key = str(candidate or "").strip()
            if key and key in self.context.trust_accounts:
                return self.context.trust_accounts[key]
        if len(self.context.trust_accounts) == 1:
            return next(iter(self.context.trust_accounts.values()))
        return None

    async def resolve_lot_id(self, document: dict[str, Any]) -> uuid.UUID | None:
        """Generated function header.

        Function: BaseBuildingMigration.resolve_lot_id
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        assert self.context is not None
        unit_number = str(document.get("unit_number") or "").strip()
        lot_number = str(document.get("lot_number") or "").strip()
        return (
            self.context.lot_by_unit_number.get(unit_number)
            or self.context.lot_by_lot_number.get(lot_number)
        )

    async def finalize(self, *, status: str) -> dict[str, Any]:
        """Generated function header.

        Function: BaseBuildingMigration.finalize
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        self.checkpoints.finish_run(run_id=self.options.run_id, status=status)
        return self.report.finalize(status=status)

    def source_token(self, document: dict[str, Any], *fallback_fields: str) -> str:
        """Generated function header.

        Function: BaseBuildingMigration.source_token
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return mongo_source_token(document, *fallback_fields)

    @staticmethod
    def coerce_cents(value: Any) -> int:
        """Generated function header.

        Function: BaseBuildingMigration.coerce_cents
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        if value in (None, ""):
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(round(value * 100))
        text = str(value).strip().replace(",", "")
        if not text:
            return 0
        if "." in text:
            return int(round(float(text) * 100))
        return int(text)

    @staticmethod
    def coerce_date(value: Any) -> date:
        """Generated function header.

        Function: BaseBuildingMigration.coerce_date
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return _coerce_date(value)

    @staticmethod
    def json_ready(value: Any) -> Any:
        """Generated function header.

        Function: BaseBuildingMigration.json_ready
        Path: backend/migrations/postgres/framework/runtime.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        return _json_ready(value)
