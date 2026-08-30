#!/usr/bin/env python3
# @featuretrace:postgres-cutover-readiness — Read-only P0 gate checks for PostgreSQL cutover.
# Data flow: operator CLI → PostgreSQL information_schema/core/finance tables → JSON readiness report.
# Related: docs/migration/tasks-to-postgres.md
#          docs/architecture/mindmap/12_postgresql_financial_core.md
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg

EXPECTED_0044_TABLES = (
    ("finance", "evidence_documents"),
    ("finance", "financial_cutover_config"),
    ("finance", "financial_onboarding_audit"),
)
EXPECTED_EVIDENCE_COLUMNS = (
    "approved_by",
    "approved_at",
    "declared_totals_by_fund",
    "source_system",
    "import_batch_id",
    "notes",
    "supersedes_document_id",
)
EXPECTED_JOURNAL_COLUMNS = (
    "approved_by",
    "evidence_document_id",
    "posted_by",
    "entry_hash",
    "prev_entry_hash",
)
P0_CUTOVER_FEATURE_KEYS = (
    "financial_integration_layer_v2",
    "financial_pg_writes_enabled",
    "financial_pg_reads_enabled",
    "financial_shadow_reads_enabled",
    "bank_integration_abstraction_enabled",
    "trust_pg_ledger_enabled",
    "trust_reconciliation_pg_enabled",
    "external_api_finance_pg_enabled",
    "onboarding_current_balance_adapters_enabled",
    "financial_core.read_from_postgres",
    "financial_core.shadow_read_postgres",
)
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class CheckResult:
    id: str
    status: str
    summary: str
    details: dict[str, Any]


def _load_database_url() -> str:
    """Generated function header.

    Function: _load_database_url
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for env_path in (Path("backend/.env"), Path(".env")):
        if not env_path.exists():
            continue
        for line in env_path.read_text(errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or not stripped.startswith("DATABASE_URL="):
                continue
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _asyncpg_url(raw_url: str) -> str:
    """Generated function header.

    Function: _asyncpg_url
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw_url)


def _qualified_table_name(schema: str, table: str) -> str:
    """Generated function header.

    Function: _qualified_table_name
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for identifier in (schema, table):
        if not IDENTIFIER_RE.fullmatch(identifier):
            msg = f"Invalid SQL identifier: {identifier!r}"
            raise ValueError(msg)
    return f'"{schema}"."{table}"'


def _scheme_number_candidates(building_id: str) -> list[str]:
    """Generated function header.

    Function: _scheme_number_candidates
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    normalized = building_id.strip()
    candidates = {normalized}
    upper = normalized.upper()
    if upper.startswith("UP"):
        bare = normalized[2:]
        if bare:
            candidates.add(bare)
    else:
        candidates.add(f"UP{normalized}")
    return sorted(candidates)


def _parse_alembic_version(raw_version: Any) -> int | None:
    """Generated function header.

    Function: _parse_alembic_version
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if raw_version is None:
        return None
    text = str(raw_version).strip()
    match = re.match(r"^(\d+)", text)
    if not match:
        return None
    return int(match.group(1))


async def _table_exists(conn: asyncpg.Connection, schema: str, table: str) -> bool:
    """Generated function header.

    Function: _table_exists
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return bool(
        await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = $1 AND table_name = $2
            )
            """,
            schema,
            table,
        )
    )


async def _count_if_exists(
    conn: asyncpg.Connection,
    schema: str,
    table: str,
    where_clause: str | None = None,
    *params: Any,
) -> int | None:
    """Generated function header.

    Function: _count_if_exists
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not await _table_exists(conn, schema, table):
        return None
    query = f"SELECT count(*) FROM {_qualified_table_name(schema, table)}"
    if where_clause:
        query = f"{query} WHERE {where_clause}"
    return int(await conn.fetchval(query, *params))


async def _matching_schemes(conn: asyncpg.Connection, building_id: str) -> list[asyncpg.Record]:
    """Generated function header.

    Function: _matching_schemes
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not await _table_exists(conn, "core", "schemes"):
        return []
    return await conn.fetch(
        """
        SELECT scheme_id::text, tenant_id::text, scheme_number, scheme_name, status
        FROM core.schemes
        WHERE scheme_number = ANY($1::text[])
        ORDER BY scheme_number
        """,
        _scheme_number_candidates(building_id),
    )


async def _matching_schemes_with_hints(
    conn: asyncpg.Connection,
    *,
    building_id: str,
    scheme_id: str | None = None,
    plan_number: str | None = None,
    building_slug: str | None = None,
) -> list[asyncpg.Record]:
    """Generated function header.

    Function: _matching_schemes_with_hints
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not await _table_exists(conn, "core", "schemes"):
        return []

    number_seed = plan_number or building_id
    candidates = _scheme_number_candidates(number_seed)
    use_slug = bool(building_slug and await _table_exists(conn, "core", "buildings"))
    if use_slug:
        return await conn.fetch(
            """
            SELECT DISTINCT
                s.scheme_id::text,
                s.tenant_id::text,
                s.scheme_number,
                s.scheme_name,
                s.status
            FROM core.schemes s
            LEFT JOIN core.buildings b ON b.scheme_id = s.scheme_id
            WHERE (
                    ($1::uuid IS NOT NULL AND s.scheme_id = $1::uuid)
                 OR s.scheme_number = ANY($2::text[])
                 OR lower(coalesce(b.asset_profile->>'building_slug','')) = lower($3)
            )
            ORDER BY s.scheme_number
            """,
            scheme_id,
            candidates,
            building_slug,
        )
    return await conn.fetch(
        """
        SELECT DISTINCT
            s.scheme_id::text,
            s.tenant_id::text,
            s.scheme_number,
            s.scheme_name,
            s.status
        FROM core.schemes s
        WHERE (
                ($1::uuid IS NOT NULL AND s.scheme_id = $1::uuid)
             OR s.scheme_number = ANY($2::text[])
        )
        ORDER BY s.scheme_number
        """,
        scheme_id,
        candidates,
    )


async def check_schema(conn: asyncpg.Connection) -> list[CheckResult]:
    """Generated function header.

    Function: check_schema
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    alembic_version = await conn.fetchval("SELECT version_num FROM core.alembic_version LIMIT 1")
    parsed_alembic_version = _parse_alembic_version(alembic_version)
    missing_tables = [
        f"{schema}.{table}"
        for schema, table in EXPECTED_0044_TABLES
        if not await _table_exists(conn, schema, table)
    ]
    column_rows = await conn.fetch(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'finance'
          AND table_name = 'journal_entries'
          AND column_name = ANY($1::text[])
        ORDER BY column_name
        """,
        list(EXPECTED_JOURNAL_COLUMNS),
    )
    columns = {
        row["column_name"]: {"data_type": row["data_type"], "is_nullable": row["is_nullable"]}
        for row in column_rows
    }
    missing_columns = [name for name in EXPECTED_JOURNAL_COLUMNS if name not in columns]
    evidence_column_rows = await conn.fetch(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'finance'
          AND table_name = 'evidence_documents'
          AND column_name = ANY($1::text[])
        ORDER BY column_name
        """,
        list(EXPECTED_EVIDENCE_COLUMNS),
    )
    evidence_columns = {
        row["column_name"]: {"data_type": row["data_type"], "is_nullable": row["is_nullable"]}
        for row in evidence_column_rows
    }
    missing_evidence_columns = [name for name in EXPECTED_EVIDENCE_COLUMNS if name not in evidence_columns]
    schema_ok = (
        not missing_tables
        and not missing_columns
        and not missing_evidence_columns
        and parsed_alembic_version is not None
        and parsed_alembic_version >= 51
    )
    return [
        CheckResult(
            id="P0-01",
            status="pass" if schema_ok else "fail",
            summary="Migration 0044 schema is present" if schema_ok else "Migration 0044 schema is not fully present",
            details={
                "alembic_version": str(alembic_version),
                "parsed_alembic_version": parsed_alembic_version,
                "missing_tables": missing_tables,
                "journal_columns": columns,
                "missing_journal_columns": missing_columns,
                "evidence_columns": evidence_columns,
                "missing_evidence_columns": missing_evidence_columns,
            },
        )
    ]


async def check_toggle_safety(conn: asyncpg.Connection) -> CheckResult:
    """Generated function header.

    Function: check_toggle_safety
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not await _table_exists(conn, "core", "feature_toggles"):
        return CheckResult("P0-04", "fail", "core.feature_toggles table is missing", {})
    rows = await conn.fetch(
        """
        SELECT feature_key, is_enabled
        FROM core.feature_toggles
        WHERE feature_key = ANY($1::text[])
        ORDER BY feature_key
        """,
        list(P0_CUTOVER_FEATURE_KEYS),
    )
    enabled = [row["feature_key"] for row in rows if bool(row["is_enabled"])]
    return CheckResult(
        id="P0-04",
        status="warn" if enabled else "pass",
        summary="Global PostgreSQL cutover toggles require review" if enabled else "No enabled global PostgreSQL cutover toggles found",
        details={"enabled_global_toggles": enabled, "checked_keys": list(P0_CUTOVER_FEATURE_KEYS)},
    )


async def check_foundation(
    conn: asyncpg.Connection,
    building_id: str,
    *,
    scheme_id: str | None = None,
    plan_number: str | None = None,
    building_slug: str | None = None,
) -> CheckResult:
    """Generated function header.

    Function: check_foundation
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    validated_scheme_id: str | None = None
    if scheme_id:
        try:
            validated_scheme_id = str(UUID(str(scheme_id)))
        except (ValueError, TypeError):
            validated_scheme_id = None
    if scheme_id or plan_number or building_slug:
        scheme_rows = await _matching_schemes_with_hints(
            conn,
            building_id=building_id,
            scheme_id=validated_scheme_id,
            plan_number=plan_number,
            building_slug=building_slug,
        )
    else:
        scheme_rows = await _matching_schemes(conn, building_id)
    if not scheme_rows:
        counts = {
            "core.tenants": 0,
            "core.schemes": 0,
            "core.users": 0,
            "core.lots": 0,
            "core.parties": 0,
            "core.ownership_periods": 0,
        }
    else:
        scheme_ids = [row["scheme_id"] for row in scheme_rows]
        tenant_ids = sorted({row["tenant_id"] for row in scheme_rows})
        if tenant_ids:
            await conn.execute("SELECT set_config('app.tenant_id', $1, false)", tenant_ids[0])
        counts = {
            "core.tenants": await _count_if_exists(conn, "core", "tenants", "tenant_id::text = ANY($1::text[])", tenant_ids),
            "core.schemes": await _count_if_exists(conn, "core", "schemes", "scheme_id::text = ANY($1::text[])", scheme_ids),
            "core.users": await _count_if_exists(conn, "core", "users", "tenant_id::text = ANY($1::text[])", tenant_ids),
            "core.lots": await _count_if_exists(conn, "core", "lots", "scheme_id::text = ANY($1::text[])", scheme_ids),
            "core.parties": await _count_if_exists(conn, "core", "parties", "tenant_id::text = ANY($1::text[])", tenant_ids),
            "core.ownership_periods": await _count_if_exists(conn, "core", "ownership_periods", "scheme_id::text = ANY($1::text[])", scheme_ids),
        }
    has_scheme = (counts["core.schemes"] or 0) > 0
    has_identity = all((counts[key] or 0) > 0 for key in ("core.users", "core.lots", "core.parties", "core.ownership_periods"))
    status = "pass" if has_scheme and has_identity else "fail"
    return CheckResult(
        id="P0-05",
        status=status,
        summary="PostgreSQL identity/ownership foundation exists" if status == "pass" else "PostgreSQL identity/ownership foundation is incomplete",
        details={
            "building_id": building_id,
            "scheme_id_hint": scheme_id,
            "plan_number_hint": plan_number,
            "building_slug_hint": building_slug,
            "counts": counts,
            "matching_schemes": [dict(row) for row in scheme_rows],
        },
    )


async def _load_financial_onboarding_snapshot(conn: asyncpg.Connection, building_id: str) -> dict[str, Any]:
    """Generated function header.

    Function: _load_financial_onboarding_snapshot
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme_rows = await _matching_schemes(conn, building_id)
    scheme_ids = [row["scheme_id"] for row in scheme_rows]
    building_ids = _scheme_number_candidates(building_id)

    evidence_rows: list[dict[str, Any]] = []
    fund_rows: list[dict[str, Any]] = []
    gl_rows: list[dict[str, Any]] = []
    journal_entries: list[dict[str, Any]] = []
    journal_lines: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    cutover_rows: list[dict[str, Any]] = []

    if scheme_ids:
        if await _table_exists(conn, "finance", "evidence_documents"):
            evidence_rows = [dict(row) for row in await conn.fetch(
                """
                SELECT document_id::text, building_id, scheme_id::text, sha256_hash,
                       approved_by::text, approved_at, declared_total_cents,
                       declared_totals_by_fund, uploaded_by::text, document_type,
                       supersedes_document_id::text, is_test_data
                FROM finance.evidence_documents
                WHERE scheme_id::text = ANY($1::text[])
                ORDER BY uploaded_at
                """,
                scheme_ids,
            )]
        if await _table_exists(conn, "finance", "funds"):
            fund_rows = [dict(row) for row in await conn.fetch(
                """
                SELECT fund_id::text, scheme_id::text, fund_code, fund_type::text, status::text
                FROM finance.funds
                WHERE scheme_id::text = ANY($1::text[])
                ORDER BY fund_code
                """,
                scheme_ids,
            )]
        if await _table_exists(conn, "finance", "gl_accounts"):
            gl_rows = [dict(row) for row in await conn.fetch(
                """
                SELECT gl_account_id::text, scheme_id::text, account_code, account_type::text, status::text
                FROM finance.gl_accounts
                WHERE scheme_id::text = ANY($1::text[])
                ORDER BY account_code
                """,
                scheme_ids,
            )]
        if await _table_exists(conn, "finance", "journal_entries"):
            journal_entries = [dict(row) for row in await conn.fetch(
                """
                SELECT journal_entry_id::text, scheme_id::text, fund_id::text,
                       entry_number, evidence_document_id::text, approved_by::text,
                       posted_by::text, prev_entry_hash, entry_hash, status::text, source_type
                FROM finance.journal_entries
                WHERE scheme_id::text = ANY($1::text[])
                  AND source_type = 'genesis'
                ORDER BY entry_number
                """,
                scheme_ids,
            )]
        if await _table_exists(conn, "finance", "journal_lines"):
            journal_lines = [dict(row) for row in await conn.fetch(
                """
                SELECT journal_entry_id::text, direction::text, amount_cents
                FROM finance.journal_lines
                WHERE scheme_id::text = ANY($1::text[])
                """,
                scheme_ids,
            )]
        if await _table_exists(conn, "finance", "financial_onboarding_audit"):
            audit_rows = [dict(row) for row in await conn.fetch(
                """
                SELECT audit_id::text, scheme_id::text, approved_by::text,
                       evidence_document_id::text, evidence_sha256_hash, cutover_date, opening_balances
                FROM finance.financial_onboarding_audit
                WHERE building_id = ANY($1::text[])
                ORDER BY cutover_date DESC
                """,
                building_ids,
            )]
        if await _table_exists(conn, "finance", "financial_cutover_config"):
            cutover_rows = [dict(row) for row in await conn.fetch(
                """
                SELECT config_id::text, scheme_id::text, cutover_date, onboarded,
                       approved_by::text, evidence_document_id::text, journal_entry_ids, metadata
                FROM finance.financial_cutover_config
                WHERE building_id = ANY($1::text[])
                ORDER BY cutover_date DESC
                """,
                building_ids,
            )]

    return {
        "building_id": building_id,
        "matching_schemes": [dict(row) for row in scheme_rows],
        "evidence_rows": evidence_rows,
        "fund_rows": fund_rows,
        "gl_rows": gl_rows,
        "journal_entries": journal_entries,
        "journal_lines": journal_lines,
        "audit_rows": audit_rows,
        "cutover_rows": cutover_rows,
    }


def evaluate_financial_onboarding_snapshot(building_id: str, snapshot: dict[str, Any]) -> list[CheckResult]:
    """Generated function header.

    Function: evaluate_financial_onboarding_snapshot
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    evidence_rows = snapshot.get("evidence_rows") or []
    fund_rows = snapshot.get("fund_rows") or []
    gl_rows = snapshot.get("gl_rows") or []
    journal_entries = snapshot.get("journal_entries") or []
    journal_lines = snapshot.get("journal_lines") or []
    audit_rows = snapshot.get("audit_rows") or []
    cutover_rows = snapshot.get("cutover_rows") or []
    matching_schemes = snapshot.get("matching_schemes") or []

    counts = {
        "finance.evidence_documents": len(evidence_rows),
        "finance.funds": len(fund_rows),
        "finance.gl_accounts": len(gl_rows),
        "finance.journal_entries": len(journal_entries),
        "finance.journal_lines": len(journal_lines),
        "finance.financial_onboarding_audit": len(audit_rows),
        "finance.financial_cutover_config": len(cutover_rows),
    }

    scheme_failures: list[str] = []
    if not matching_schemes:
        scheme_failures.append("no matching schemes")

    evidence_failures: list[str] = []
    if not evidence_rows:
        evidence_failures.append("missing evidence")
    for row in evidence_rows:
        if not row.get("sha256_hash"):
            evidence_failures.append(f"evidence {row.get('document_id')} missing sha256_hash")
        if not row.get("approved_by"):
            evidence_failures.append(f"evidence {row.get('document_id')} missing approved_by")
        if not row.get("approved_at"):
            evidence_failures.append(f"evidence {row.get('document_id')} missing approved_at")
        if row.get("declared_total_cents") is None:
            evidence_failures.append(f"evidence {row.get('document_id')} missing declared_total_cents")
    evidence_status = "pass" if not evidence_failures else "fail"

    required_funds = {"admin", "sinking"}
    fund_types = set()
    for row in fund_rows:
        raw_type = str(row.get("fund_type") or row.get("fund_code") or "").lower()
        if "admin" in raw_type:
            fund_types.add("admin")
        elif "sink" in raw_type or "capital" in raw_type:
            fund_types.add("sinking")
        elif "special" in raw_type:
            fund_types.add("special")
    required_gl_codes = {"1000", "3100"}
    gl_codes = {str(row.get("account_code")) for row in gl_rows if row.get("account_code")}
    fund_failures: list[str] = []
    missing_funds = sorted(required_funds - fund_types)
    missing_gl = sorted(required_gl_codes - gl_codes)
    if missing_funds:
        fund_failures.append(f"missing funds: {', '.join(missing_funds)}")
    if missing_gl:
        fund_failures.append(f"missing GL accounts: {', '.join(missing_gl)}")
    fund_status = "pass" if not fund_failures else "fail"

    journal_failures: list[str] = []
    if not journal_entries:
        journal_failures.append("missing genesis journal entries")
    lines_by_entry: dict[str, list[dict[str, Any]]] = {}
    for line in journal_lines:
        lines_by_entry.setdefault(str(line.get("journal_entry_id")), []).append(line)
    prev_hash: str | None = None
    for entry in journal_entries:
        entry_id = str(entry.get("journal_entry_id"))
        lines = lines_by_entry.get(entry_id, [])
        if not entry.get("evidence_document_id"):
            journal_failures.append(f"journal {entry_id} missing evidence_document_id")
        if not entry.get("approved_by"):
            journal_failures.append(f"journal {entry_id} missing approved_by")
        if not entry.get("posted_by"):
            journal_failures.append(f"journal {entry_id} missing posted_by")
        if not lines:
            journal_failures.append(f"journal {entry_id} missing journal lines")
        debit = sum(int(line["amount_cents"]) for line in lines if str(line.get("direction")) == "debit")
        credit = sum(int(line["amount_cents"]) for line in lines if str(line.get("direction")) == "credit")
        if lines and (debit == 0 and credit == 0):
            journal_failures.append(f"journal {entry_id} has no signed amounts")
        if lines and debit != credit:
            journal_failures.append(f"journal {entry_id} is unbalanced")
        entry_hash = entry.get("entry_hash")
        if not entry_hash:
            journal_failures.append(f"journal {entry_id} missing entry_hash")
        if prev_hash is None:
            if entry.get("prev_entry_hash") not in (None, ""):
                journal_failures.append(f"journal {entry_id} broken hash chain")
        elif entry.get("prev_entry_hash") != prev_hash:
            journal_failures.append(f"journal {entry_id} broken hash chain")
        prev_hash = entry_hash or prev_hash
    journal_status = "pass" if not journal_failures else "fail"

    audit_failures: list[str] = []
    if not audit_rows:
        audit_failures.append("missing onboarding audit")
    if not cutover_rows:
        audit_failures.append("missing cutover config")
    for row in cutover_rows:
        if not bool(row.get("onboarded")):
            audit_failures.append("financial cutover config is not marked onboarded")
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if isinstance(metadata, dict):
            if metadata.get("readiness_only") is not True:
                audit_failures.append("finance cutover config is not readiness-only")
            if str(metadata.get("mode") or "mongo_primary") != "mongo_primary":
                audit_failures.append("finance cutover config promotes finance automatically")
            if str(metadata.get("read_source") or "mongo") != "mongo":
                audit_failures.append("finance cutover config read_source is not mongo")
            if str(metadata.get("write_source") or "mongo") != "mongo":
                audit_failures.append("finance cutover config write_source is not mongo")
        else:
            audit_failures.append("financial cutover config metadata is invalid")
    audit_status = "pass" if not audit_failures else "fail"

    return [
        CheckResult(
            id="P0-06",
            status=evidence_status,
            summary="Opening-balance evidence exists and is approved" if evidence_status == "pass" else "Opening-balance evidence is missing or invalid",
            details={
                "building_id": building_id,
                "counts": counts,
                "failures": evidence_failures,
                "matching_schemes": matching_schemes,
            },
        ),
        CheckResult(
            id="P0-07",
            status=fund_status,
            summary="Fund and GL foundation exists" if fund_status == "pass" else "Fund or GL foundation is incomplete",
            details={
                "building_id": building_id,
                "counts": counts,
                "failures": fund_failures,
                "matching_schemes": matching_schemes,
            },
        ),
        CheckResult(
            id="P0-08",
            status=journal_status,
            summary="Genesis journals are balanced and evidence-linked" if journal_status == "pass" else "Genesis journals are missing or invalid",
            details={
                "building_id": building_id,
                "counts": counts,
                "failures": journal_failures,
                "matching_schemes": matching_schemes,
            },
        ),
        CheckResult(
            id="P0-09",
            status="fail" if scheme_failures else audit_status,
            summary="Onboarding audit trail and readiness-only cutover config exist" if audit_status == "pass" and not scheme_failures else "Onboarding audit trail or cutover config is missing",
            details={
                "building_id": building_id,
                "counts": counts,
                "failures": audit_failures + scheme_failures,
                "matching_schemes": matching_schemes,
            },
        ),
    ]


async def check_financial_onboarding_state(conn: asyncpg.Connection, building_id: str) -> list[CheckResult]:
    """Generated function header.

    Function: check_financial_onboarding_state
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    snapshot = await _load_financial_onboarding_snapshot(conn, building_id)
    return evaluate_financial_onboarding_snapshot(building_id, snapshot)


async def run(
    building_id: str,
    *,
    scheme_id: str | None = None,
    plan_number: str | None = None,
    building_slug: str | None = None,
) -> dict[str, Any]:
    """Generated function header.

    Function: run
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw_url = _load_database_url()
    if not raw_url:
        return {"status": "fail", "error": "DATABASE_URL is not set", "checks": []}
    conn = await asyncpg.connect(_asyncpg_url(raw_url))
    try:
        checks: list[CheckResult] = []
        await conn.execute("SELECT set_config('app.tenant_id', '00000000-0000-0000-0000-000000000000', false)")
        checks.extend(await check_schema(conn))
        checks.append(await check_toggle_safety(conn))
        checks.append(
            await check_foundation(
                conn,
                building_id,
                scheme_id=scheme_id,
                plan_number=plan_number,
                building_slug=building_slug,
            )
        )
        checks.extend(await check_financial_onboarding_state(conn, building_id))
        failed = [check for check in checks if check.status == "fail"]
        warnings = [check for check in checks if check.status == "warn"]
        return {
            "status": "fail" if failed else "warn" if warnings else "pass",
            "building_id": building_id,
            "checks": [asdict(check) for check in checks],
        }
    finally:
        await conn.close()


def main() -> int:
    """Generated function header.

    Function: main
    Path: backend/scripts/postgres_cutover_p0_readiness.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    parser = argparse.ArgumentParser(description="Run read-only P0 PostgreSQL cutover readiness checks.")
    parser.add_argument("--building-id", default="13195", help="Legacy building/plan number to validate. Default: 13195.")
    parser.add_argument("--scheme-id", default=None, help="Optional scheme UUID hint for P0-05 identity matching.")
    parser.add_argument("--plan-number", default=None, help="Optional scheme number hint for P0-05 identity matching.")
    parser.add_argument("--building-slug", default=None, help="Optional building slug hint for P0-05 identity matching.")
    args = parser.parse_args()
    report = asyncio.run(
        run(
            args.building_id,
            scheme_id=args.scheme_id,
            plan_number=args.plan_number,
            building_slug=args.building_slug,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
