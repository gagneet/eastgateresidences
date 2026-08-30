# @featuretrace:financial-onboarding — Clean-slate East Gate financial cutover orchestration.
# Layer: service
# Data flow: /financials/onboarding/* → onboard_building_financials() → finance.evidence_documents,
#            finance.journal_entries, finance.financial_cutover_config, finance.financial_onboarding_audit (building-scoped).
# Related: backend/routers/financial_onboarding.py
#           backend/services/financial_core/service.py
#           backend/alembic/versions/0044_financial_onboarding_cutover.py
"""Clean-slate financial onboarding for the PostgreSQL ledger.

This module deliberately does not replay MongoDB financial history. It creates
one genesis journal per fund from approved opening balances, records first-class
evidence references, and records readiness without automatically promoting the
finance domain.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from db_postgres.repos.config_repo import resolve_scheme_context
from db_postgres.session import async_session_context, set_tenant
from services.cutover_status_service import record_financial_foundation_readiness
from services.financial_core import get_financial_core_service
from services.financial_core.domain.entities import PostGenesisJournalCommand, SchemeRef
from services.financial_core.genesis import (
    ensure_scheme_finance_bootstrap,
    normalize_fund_type,
    resolve_scheme_fund_ids,
)

_ALLOWED_APPROVAL_ROLES = {"super_admin", "strata_manager"}
_ALLOWED_DOCUMENT_TYPES = {
    "bank_statement", "reconciliation", "xero_export",
    "agm_pack", "invoice", "quote", "historical_expense_import",
    "opening_balance",
}
_FUND_CODE_TO_TYPE = {
    "admin": "admin",
    "admin_fund": "admin",
    "administrative": "admin",
    "administrative_fund": "admin",
    "sink": "sinking",
    "sinking": "sinking",
    "sinking_fund": "sinking",
    "capital": "sinking",
    "capital_works": "sinking",
    "special": "special",
    "special_fund": "special",
    "special_purpose": "special",
}


@dataclass(frozen=True)
class FundBalance:
    fund_code: str
    amount_cents: int


@dataclass(frozen=True)
class OnboardingResult:
    building_id: str
    cutover_date: date
    source_of_truth: str
    readiness_status: str
    cutover_mode: str
    read_source: str
    write_source: str
    onboarded: bool
    evidence_document_id: str
    evidence_sha256_hash: str
    journal_entry_ids: list[str]
    opening_balances: list[dict[str, Any]]


@dataclass(frozen=True)
class EvidenceDocumentCreate:
    building_id: str
    document_type: str
    file_url: str
    sha256_hash: str
    uploaded_by: str
    approved_by: str
    approved_at: datetime | None = None
    declared_total_cents: int | None = None
    declared_totals_by_fund: dict[str, int] | None = None
    source_system: str | None = None
    import_batch_id: str | None = None
    notes: str | None = None
    supersedes_document_id: str | None = None
    metadata: dict[str, Any] | None = None
    is_test_data: bool = False


def _entry_hash(entry_id: str, narration: str, prev_hash: str | None) -> str:
    """Generated function header.

    Function: _entry_hash
    Path: backend/services/onboarding/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = json.dumps(
        {"id": str(entry_id), "narration": narration, "prev": prev_hash or ""},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _normalise_fund_code(fund_code: str) -> str:
    """Generated function header.

    Function: _normalise_fund_code
    Path: backend/services/onboarding/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    raw = str(fund_code or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw not in _FUND_CODE_TO_TYPE:
        raise ValueError(f"Unsupported fund_code {fund_code!r}; expected admin, sinking, or special")
    return _FUND_CODE_TO_TYPE[raw]


def _coerce_uuid(value: str, field_name: str) -> UUID:
    """Generated function header.

    Function: _coerce_uuid
    Path: backend/services/onboarding/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a PostgreSQL UUID") from exc


def _coerce_declared_totals_by_fund(value: dict[str, Any] | None) -> dict[str, int]:
    """Generated function header.

    Function: _coerce_declared_totals_by_fund
    Path: backend/services/onboarding/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if value is None:
        raise ValueError("declared_totals_by_fund is required")
    if not isinstance(value, dict) or not value:
        raise ValueError("declared_totals_by_fund must be a non-empty mapping")
    totals: dict[str, int] = {}
    for raw_fund, raw_amount in value.items():
        fund_type = _normalise_fund_code(str(raw_fund))
        amount_cents = int(raw_amount)
        totals[fund_type] = amount_cents
    return totals


async def _resolve_scheme_ref(building_id: str) -> SchemeRef:
    """Generated function header.

    Function: _resolve_scheme_ref
    Path: backend/services/onboarding/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        raise ValueError(f"No PostgreSQL scheme mapping found for building_id={building_id}")
    return SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))


async def create_evidence_document(payload: EvidenceDocumentCreate) -> dict[str, Any]:
    """Create an evidence document metadata row in finance.evidence_documents."""
    document_type = str(payload.document_type or "").strip().lower()
    if document_type not in _ALLOWED_DOCUMENT_TYPES:
        raise ValueError(f"document_type must be one of {sorted(_ALLOWED_DOCUMENT_TYPES)}")
    sha256_hash = str(payload.sha256_hash or "").strip().lower()
    if len(sha256_hash) != 64 or any(ch not in "0123456789abcdef" for ch in sha256_hash):
        raise ValueError("sha256_hash must be a lowercase 64-character SHA-256 hex digest")

    uploaded_by = _coerce_uuid(payload.uploaded_by, "uploaded_by")
    approved_by = _coerce_uuid(payload.approved_by, "approved_by")
    approved_at = payload.approved_at or datetime.now(timezone.utc)
    declared_total_cents = payload.declared_total_cents
    if declared_total_cents is None:
        raise ValueError("declared_total_cents is required")
    declared_totals_by_fund = _coerce_declared_totals_by_fund(payload.declared_totals_by_fund)
    declared_fund_total = sum(declared_totals_by_fund.values())
    if declared_fund_total != int(declared_total_cents):
        raise ValueError("declared_totals_by_fund does not reconcile to declared_total_cents")
    supersedes_document_id = _coerce_uuid(payload.supersedes_document_id, "supersedes_document_id") if payload.supersedes_document_id else None
    scheme_ref = await _resolve_scheme_ref(payload.building_id)

    async with async_session_context() as session:
        await set_tenant(session, str(scheme_ref.tenant_id))
        await _validate_active_user(session, scheme_ref=scheme_ref, user_id=uploaded_by, field_name="uploaded_by")
        await _validate_approval_user(session, scheme_ref=scheme_ref, approved_by_user_id=approved_by)
        existing = await session.execute(
            text(
                """
                SELECT document_id::text, uploaded_at, approved_by::text, approved_at
                FROM finance.evidence_documents
                WHERE tenant_id = :tenant_id
                  AND scheme_id = :scheme_id
                  AND building_id = :building_id
                  AND sha256_hash = :sha256_hash
                """
            ),
            {
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "building_id": payload.building_id,
                "sha256_hash": sha256_hash,
            },
        )
        existing_row = existing.mappings().first()
        if existing_row:
            return {
                "document_id": str(existing_row["document_id"]),
                "building_id": payload.building_id,
                "document_type": document_type,
                "file_url": payload.file_url,
                "sha256_hash": sha256_hash,
                "uploaded_by": str(uploaded_by),
                "approved_by": existing_row["approved_by"],
                "approved_at": existing_row["approved_at"].isoformat() if hasattr(existing_row["approved_at"], "isoformat") else existing_row["approved_at"],
                "uploaded_at": existing_row["uploaded_at"].isoformat() if hasattr(existing_row["uploaded_at"], "isoformat") else existing_row["uploaded_at"],
                "declared_total_cents": declared_total_cents,
                "declared_totals_by_fund": declared_totals_by_fund,
                "source_system": payload.source_system,
                "import_batch_id": payload.import_batch_id,
                "notes": payload.notes,
                "supersedes_document_id": str(supersedes_document_id) if supersedes_document_id else None,
                "is_test_data": payload.is_test_data,
            }
        result = await session.execute(
            text(
                """
                INSERT INTO finance.evidence_documents
                    (tenant_id, scheme_id, building_id, document_type, file_url,
                     sha256_hash, uploaded_by, approved_by, approved_at,
                     declared_total_cents, declared_totals_by_fund, source_system,
                     import_batch_id, notes, supersedes_document_id, metadata, is_test_data)
                VALUES
                    (:tenant_id, :scheme_id, :building_id, :document_type, :file_url,
                     :sha256_hash, :uploaded_by, :approved_by, :approved_at,
                     :declared_total_cents, CAST(:declared_totals_by_fund AS jsonb), :source_system,
                     :import_batch_id, :notes, :supersedes_document_id, CAST(:metadata AS jsonb), :is_test_data)
                RETURNING document_id::text, uploaded_at
                """
            ),
            {
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "building_id": payload.building_id,
                "document_type": document_type,
                "file_url": payload.file_url,
                "sha256_hash": sha256_hash,
                "uploaded_by": str(uploaded_by),
                "approved_by": str(approved_by),
                "approved_at": approved_at,
                "declared_total_cents": declared_total_cents,
                "declared_totals_by_fund": json.dumps(declared_totals_by_fund),
                "source_system": payload.source_system,
                "import_batch_id": payload.import_batch_id,
                "notes": payload.notes,
                "supersedes_document_id": str(supersedes_document_id) if supersedes_document_id else None,
                "metadata": json.dumps(payload.metadata or {}),
                "is_test_data": payload.is_test_data,
            },
        )
        row = result.one()

    return {
        "document_id": str(row.document_id),
        "building_id": payload.building_id,
        "document_type": document_type,
        "file_url": payload.file_url,
        "sha256_hash": sha256_hash,
        "uploaded_by": str(uploaded_by),
        "approved_by": str(approved_by),
        "approved_at": approved_at.isoformat(),
        "uploaded_at": row.uploaded_at.isoformat() if hasattr(row.uploaded_at, "isoformat") else row.uploaded_at,
        "declared_total_cents": declared_total_cents,
        "declared_totals_by_fund": declared_totals_by_fund,
        "source_system": payload.source_system,
        "import_batch_id": payload.import_batch_id,
        "notes": payload.notes,
        "supersedes_document_id": str(supersedes_document_id) if supersedes_document_id else None,
        "is_test_data": payload.is_test_data,
    }


async def _load_evidence(session, *, scheme_ref: SchemeRef, building_id: str, evidence_document_id: UUID) -> dict[str, Any]:
    """Generated function header.

    Function: _load_evidence
    Path: backend/services/onboarding/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT document_id::text, document_type, file_url, sha256_hash,
                   uploaded_by::text, approved_by::text, approved_at,
                   declared_total_cents, declared_totals_by_fund, source_system,
                   import_batch_id, notes, supersedes_document_id::text, is_test_data
            FROM finance.evidence_documents
            WHERE tenant_id = :tenant_id
              AND scheme_id = :scheme_id
              AND building_id = :building_id
              AND document_id = :document_id
            """
        ),
        {
            "tenant_id": str(scheme_ref.tenant_id),
            "scheme_id": str(scheme_ref.scheme_id),
            "building_id": building_id,
            "document_id": str(evidence_document_id),
        },
    )
    row = result.mappings().first()
    if not row:
        raise ValueError("Evidence document does not exist for this building")
    return dict(row)


async def _validate_active_user(session, *, scheme_ref: SchemeRef, user_id: UUID, field_name: str) -> dict[str, Any]:
    """Generated function header.

    Function: _validate_active_user
    Path: backend/services/onboarding/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    result = await session.execute(
        text(
            """
            SELECT u.user_id::text AS user_id,
                   u.full_name,
                   u.email,
                   core.user_effective_role(u.user_id, :scheme_id) AS effective_role
            FROM core.users u
            WHERE u.tenant_id = :tenant_id
              AND u.user_id = :user_id
              AND u.is_active = TRUE
              AND u.is_approved = TRUE
            """
        ),
        {
            "tenant_id": str(scheme_ref.tenant_id),
            "scheme_id": str(scheme_ref.scheme_id),
            "user_id": str(user_id),
        },
    )
    row = result.mappings().first()
    if not row:
        raise ValueError(f"{field_name} does not exist or is not an active approved PostgreSQL user")
    return dict(row)


async def _validate_approval_user(session, *, scheme_ref: SchemeRef, approved_by_user_id: UUID) -> str:
    """Generated function header.

    Function: _validate_approval_user
    Path: backend/services/onboarding/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    row = await _validate_active_user(session, scheme_ref=scheme_ref, user_id=approved_by_user_id, field_name="approved_by")
    role = str(row["effective_role"] or "guest")
    if role not in _ALLOWED_APPROVAL_ROLES:
        raise PermissionError(f"Approval user must have one of these roles: {sorted(_ALLOWED_APPROVAL_ROLES)}")
    return str(row["full_name"] or row["email"] or approved_by_user_id)


async def _post_zero_balance_genesis_anchor(
    session,
    *,
    scheme_ref: SchemeRef,
    fund_id: UUID,
    fund_type: str,
    cutover_date: date,
    evidence_uuid: UUID,
    evidence_sha256_hash: str,
    approved_by_uuid: UUID,
    approver_name: str,
    building_id: str,
    idempotency_key: str,
    is_test_data: bool,
) -> str:
    """Generated function header.

    Function: _post_zero_balance_genesis_anchor
    Path: backend/services/onboarding/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    existing = await session.execute(
        text(
            """
            SELECT journal_entry_id::text
            FROM finance.journal_entries
            WHERE scheme_id = :scheme_id
              AND idempotency_key = :idempotency_key
            LIMIT 1
            """
        ),
        {"scheme_id": str(scheme_ref.scheme_id), "idempotency_key": idempotency_key},
    )
    existing_id = existing.scalar()
    if existing_id:
        return str(existing_id)

    period = await session.execute(
        text(
            """
            SELECT period_id::text
            FROM finance.accounting_periods
            WHERE scheme_id = :scheme_id
              AND starts_on <= :cutover_date
              AND ends_on >= :cutover_date
            ORDER BY starts_on DESC
            LIMIT 1
            """
        ),
        {"scheme_id": str(scheme_ref.scheme_id), "cutover_date": cutover_date},
    )
    period_id = period.scalar()
    if not period_id:
        period = await session.execute(
            text(
                """
                SELECT period_id::text
                FROM finance.accounting_periods
                WHERE scheme_id = :scheme_id
                ORDER BY starts_on
                LIMIT 1
                """
            ),
            {"scheme_id": str(scheme_ref.scheme_id)},
        )
        period_id = period.scalar()
    if not period_id:
        raise ValueError("No accounting period exists for zero-balance genesis anchor")

    accounts = await session.execute(
        text(
            """
            SELECT account_code, gl_account_id::text
            FROM finance.gl_accounts
            WHERE scheme_id = :scheme_id
              AND account_code IN ('1000', '3100')
            """
        ),
        {"scheme_id": str(scheme_ref.scheme_id)},
    )
    account_ids = {str(row.account_code): str(row.gl_account_id) for row in accounts.fetchall()}
    if "1000" not in account_ids or "3100" not in account_ids:
        raise ValueError("Missing GL account 1000 or 3100 for zero-balance genesis anchor")

    last_hash = await session.execute(
        text(
            """
            SELECT entry_hash
            FROM finance.journal_entries
            WHERE scheme_id = :scheme_id
              AND source_type = 'genesis'
            ORDER BY entry_number DESC
            LIMIT 1
            """
        ),
        {"scheme_id": str(scheme_ref.scheme_id)},
    )
    prev_hash = last_hash.scalar()
    journal_entry_id = str(uuid4())
    narration = f"Zero-balance genesis opening balance for {fund_type} fund"
    entry_hash = _entry_hash(journal_entry_id, narration, prev_hash)
    metadata = {
        "evidence_doc_id": str(evidence_uuid),
        "evidence_doc_hash": evidence_sha256_hash,
        "posted_by_user_id": str(approved_by_uuid),
        "approved_by_user_id": str(approved_by_uuid),
        "posted_by_user_name": approver_name,
        "building_id": building_id,
        "opening_balance_cents": 0,
        "zero_balance_genesis_anchor": True,
        "nominal_balancing_amount_cents": 1,
        "note": "New scheme commencement with zero opening balance; nominal balanced lines satisfy posted-journal invariants without net financial effect.",
    }
    await session.execute(
        text(
            """
            INSERT INTO finance.journal_entries
                (journal_entry_id, tenant_id, scheme_id, fund_id, period_id,
                 source_type, source_reference, narration, status, effective_on,
                 posted_at, posted_by, approved_by, evidence_document_id,
                 idempotency_key, prev_entry_hash, entry_hash, is_test_data,
                 metadata, created_at)
            VALUES
                (:journal_entry_id, :tenant_id, :scheme_id, :fund_id, :period_id,
                 'genesis', :source_reference, :narration, 'posted', :effective_on,
                 NOW(), :posted_by, :approved_by, :evidence_document_id,
                 :idempotency_key, :prev_entry_hash, :entry_hash, :is_test_data,
                 CAST(:metadata AS jsonb), NOW())
            """
        ),
        {
            "journal_entry_id": journal_entry_id,
            "tenant_id": str(scheme_ref.tenant_id),
            "scheme_id": str(scheme_ref.scheme_id),
            "fund_id": str(fund_id),
            "period_id": period_id,
            "source_reference": str(fund_id),
            "narration": narration,
            "effective_on": cutover_date,
            "posted_by": str(approved_by_uuid),
            "approved_by": str(approved_by_uuid),
            "evidence_document_id": str(evidence_uuid),
            "idempotency_key": idempotency_key,
            "prev_entry_hash": prev_hash,
            "entry_hash": entry_hash,
            "is_test_data": is_test_data,
            "metadata": json.dumps(metadata),
        },
    )
    for account_code, direction, line_narration in (
        ("1000", "debit", "Zero-balance genesis anchor (assets)"),
        ("3100", "credit", "Zero-balance genesis anchor (equity)"),
    ):
        await session.execute(
            text(
                """
                INSERT INTO finance.journal_lines
                    (journal_line_id, tenant_id, scheme_id, journal_entry_id,
                     gl_account_id, direction, amount_cents, gst_cents, narration, created_at)
                VALUES
                    (:journal_line_id, :tenant_id, :scheme_id, :journal_entry_id,
                     :gl_account_id, :direction, 1, 0, :narration, NOW())
                """
            ),
            {
                "journal_line_id": str(uuid4()),
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "journal_entry_id": journal_entry_id,
                "gl_account_id": account_ids[account_code],
                "direction": direction,
                "narration": line_narration,
            },
        )
    await session.execute(
        text(
            """
            UPDATE finance.funds
            SET opening_balance_cents = 0
            WHERE scheme_id = :scheme_id
              AND fund_id = :fund_id
            """
        ),
        {"scheme_id": str(scheme_ref.scheme_id), "fund_id": str(fund_id)},
    )
    return journal_entry_id


async def onboard_building_financials(
    building_id: str,
    cutover_date: date,
    opening_balances: list[FundBalance],
    evidence_document_id: str,
    approved_by_user_id: str,
) -> OnboardingResult:
    """Post genesis journals and activate the clean-slate financial cutover.

    Non-negotiable behaviour:
    - MongoDB financial history is not replayed or migrated.
    - One balanced genesis journal is posted per supplied fund.
    - The operation is idempotently blocked after onboarding has completed.
    """
    if not opening_balances:
        raise ValueError("opening_balances must include at least one fund")

    evidence_uuid = _coerce_uuid(evidence_document_id, "evidence_document_id")
    approved_by_uuid = _coerce_uuid(approved_by_user_id, "approved_by_user_id")
    scheme_ref = await _resolve_scheme_ref(building_id)

    normalised_balances: list[dict[str, Any]] = []
    seen_funds: set[str] = set()
    for balance in opening_balances:
        fund_type = _normalise_fund_code(balance.fund_code)
        if fund_type in seen_funds:
            raise ValueError(f"Duplicate opening balance for fund {fund_type}")
        seen_funds.add(fund_type)
        amount_cents = int(balance.amount_cents)
        normalised_balances.append(
            {
                "fund_type": fund_type,
                "fund_code": balance.fund_code,
                "opening_balance_cents": amount_cents,
                "as_at_date": cutover_date.isoformat(),
            }
        )

    if not {"admin", "sinking"}.issubset(seen_funds):
        raise ValueError("Opening balances must include admin and sinking funds")

    async with async_session_context() as session:
        await set_tenant(session, str(scheme_ref.tenant_id))

        existing = await session.execute(
            text(
                """
                SELECT onboarded
                FROM finance.financial_cutover_config
                WHERE tenant_id = :tenant_id AND scheme_id = :scheme_id AND building_id = :building_id
                """
            ),
            {
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "building_id": building_id,
            },
        )
        existing_row = existing.first()
        if existing_row and bool(existing_row.onboarded):
            raise ValueError("Financial onboarding has already completed for this building")

        evidence = await _load_evidence(
            session,
            scheme_ref=scheme_ref,
            building_id=building_id,
            evidence_document_id=evidence_uuid,
        )
        if not evidence.get("approved_by") or not evidence.get("approved_at"):
            raise ValueError("Approved evidence is required before genesis onboarding")
        approver_name = await _validate_approval_user(
            session,
            scheme_ref=scheme_ref,
            approved_by_user_id=approved_by_uuid,
        )

        declared_total = evidence.get("declared_total_cents")
        declared_totals_by_fund = evidence.get("declared_totals_by_fund") or {}
        declared_totals_by_fund = {
            _normalise_fund_code(str(fund_type)): int(amount)
            for fund_type, amount in declared_totals_by_fund.items()
        }
        total_opening = sum(item["opening_balance_cents"] for item in normalised_balances)
        if declared_total is None:
            raise ValueError("Evidence document must declare a total opening balance")
        if int(declared_total) != total_opening:
            raise ValueError("Opening balances do not reconcile to the evidence document declared_total_cents")
        if declared_totals_by_fund:
            expected_by_fund = {item["fund_type"]: int(item["opening_balance_cents"]) for item in normalised_balances}
            if declared_totals_by_fund != expected_by_fund:
                raise ValueError("Opening balances do not reconcile to the evidence document declared_totals_by_fund")

        await ensure_scheme_finance_bootstrap(
            session,
            scheme_ref=scheme_ref,
            opening_balances=normalised_balances,
        )
        fund_ids = await resolve_scheme_fund_ids(session, scheme_ref)
        financial_core = get_financial_core_service(session)

        posted_entries = []
        journal_entry_ids: list[str] = []
        for balance in normalised_balances:
            fund_type = normalize_fund_type(balance["fund_type"])
            idempotency_key = f"clean-slate-genesis:{scheme_ref.scheme_id}:{fund_type}:{cutover_date.isoformat()}"
            if int(balance["opening_balance_cents"]) == 0:
                journal_entry_ids.append(
                    await _post_zero_balance_genesis_anchor(
                        session,
                        scheme_ref=scheme_ref,
                        fund_id=fund_ids[fund_type],
                        fund_type=fund_type,
                        cutover_date=cutover_date,
                        evidence_uuid=evidence_uuid,
                        evidence_sha256_hash=str(evidence["sha256_hash"]),
                        approved_by_uuid=approved_by_uuid,
                        approver_name=approver_name,
                        building_id=building_id,
                        idempotency_key=idempotency_key,
                        is_test_data=bool(evidence.get("is_test_data")),
                    )
                )
            else:
                cmd = PostGenesisJournalCommand(
                    scheme_ref=scheme_ref,
                    fund_id=fund_ids[fund_type],
                    opening_balance_cents=int(balance["opening_balance_cents"]),
                    as_at_date=cutover_date,
                    evidence_doc_id=evidence_uuid,
                    evidence_doc_hash=str(evidence["sha256_hash"]),
                    posted_by_user_id=approved_by_uuid,
                    approved_by_user_id=approved_by_uuid,
                    posted_by_user_name=approver_name,
                    building_id=building_id,
                    idempotency_key=idempotency_key,
                    is_test_data=bool(evidence.get("is_test_data")),
                )
                entry = await financial_core.post_genesis_journal(cmd)
                posted_entries.append(entry)
                journal_entry_ids.append(str(entry.journal_entry_id))
        now = datetime.now(timezone.utc)

        await session.execute(
            text(
                """
                INSERT INTO finance.financial_cutover_config
                    (tenant_id, scheme_id, building_id, cutover_date, onboarded, onboarded_at,
                     approved_by, evidence_document_id, journal_entry_ids, metadata, is_test_data)
                VALUES
                    (:tenant_id, :scheme_id, :building_id, :cutover_date, TRUE, :onboarded_at,
                     :approved_by, :evidence_document_id, CAST(:journal_entry_ids AS uuid[]),
                     CAST(:metadata AS jsonb), :is_test_data)
                ON CONFLICT (scheme_id)
                DO UPDATE SET
                    cutover_date = EXCLUDED.cutover_date,
                    onboarded = TRUE,
                    onboarded_at = EXCLUDED.onboarded_at,
                    approved_by = EXCLUDED.approved_by,
                    evidence_document_id = EXCLUDED.evidence_document_id,
                    journal_entry_ids = EXCLUDED.journal_entry_ids,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """
            ),
            {
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "building_id": building_id,
                "cutover_date": cutover_date,
                "onboarded_at": now,
                "approved_by": str(approved_by_uuid),
                "evidence_document_id": str(evidence_uuid),
                "journal_entry_ids": journal_entry_ids,
                "metadata": json.dumps(
                    {
                        "strategy": "clean_slate",
                        "mongo_historical_source": "read_only_archive",
                        "readiness_only": True,
                        "mode": "mongo_primary",
                        "read_source": "mongo",
                        "write_source": "mongo",
                    }
                ),
                "is_test_data": bool(evidence.get("is_test_data")),
            },
        )

        await session.execute(
            text(
                """
                INSERT INTO finance.financial_onboarding_audit
                    (tenant_id, scheme_id, building_id, approved_by, cutover_date,
                     evidence_document_id, evidence_sha256_hash, opening_balances,
                     journal_entry_ids, is_test_data)
                VALUES
                    (:tenant_id, :scheme_id, :building_id, :approved_by, :cutover_date,
                     :evidence_document_id, :evidence_sha256_hash, CAST(:opening_balances AS jsonb),
                     CAST(:journal_entry_ids AS uuid[]), :is_test_data)
                """
            ),
            {
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "building_id": building_id,
                "approved_by": str(approved_by_uuid),
                "cutover_date": cutover_date,
                "evidence_document_id": str(evidence_uuid),
                "evidence_sha256_hash": str(evidence["sha256_hash"]),
                "opening_balances": json.dumps(normalised_balances),
                "journal_entry_ids": journal_entry_ids,
                "is_test_data": bool(evidence.get("is_test_data")),
            },
        )

        readiness_summary = {
            "evidence_document_id": str(evidence_uuid),
            "evidence_approved": True,
            "funds_bootstrapped": True,
            "genesis_journals_posted": len(journal_entry_ids),
            "cutover_date": cutover_date.isoformat(),
            "declared_total_cents": int(declared_total),
            "total_opening_cents": total_opening,
        }
        await record_financial_foundation_readiness(
            building_id=building_id,
            validation_passed=True,
            summary=readiness_summary,
            reason="Financial opening evidence and genesis onboarding completed",
            actor_user_id=str(approved_by_uuid),
            actor_role="system",
            is_test_data=bool(evidence.get("is_test_data")),
        )

    return OnboardingResult(
        building_id=building_id,
        cutover_date=cutover_date,
        source_of_truth="mongo",
        readiness_status="ready_for_shadow",
        cutover_mode="mongo_primary",
        read_source="mongo",
        write_source="mongo",
        onboarded=True,
        evidence_document_id=str(evidence_uuid),
        evidence_sha256_hash=str(evidence["sha256_hash"]),
        journal_entry_ids=journal_entry_ids,
        opening_balances=normalised_balances,
    )


async def get_cutover_config(building_id: str, as_of: date | None = None) -> dict[str, Any]:
    """Generated function header.

    Function: get_cutover_config
    Path: backend/services/onboarding/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme_ref = await _resolve_scheme_ref(building_id)
    async with async_session_context() as session:
        await set_tenant(session, str(scheme_ref.tenant_id))
        result = await session.execute(
            text(
                """
                SELECT building_id, cutover_date, onboarded, onboarded_at,
                       approved_by::text, evidence_document_id::text, journal_entry_ids, metadata
                FROM finance.financial_cutover_config
                WHERE tenant_id = :tenant_id AND scheme_id = :scheme_id AND building_id = :building_id
                """
            ),
            {
                "tenant_id": str(scheme_ref.tenant_id),
                "scheme_id": str(scheme_ref.scheme_id),
                "building_id": building_id,
            },
        )
        row = result.mappings().first()

    if not row:
        return {
            "building_id": building_id,
            "cutover_date": None,
            "onboarded": False,
            "source_of_truth": "mongo",
            "readiness_status": "not_started",
            "mode": "mongo_primary",
            "read_source": "mongo",
            "write_source": "mongo",
            "config_present": False,
        }

    cutover = row["cutover_date"]
    from services.cutover_status_service import get_or_default_cutover_status

    status = await get_or_default_cutover_status(building_id, "finance_ledger")
    mode = status.mode.value
    return {
        "building_id": row["building_id"],
        "cutover_date": cutover.isoformat() if hasattr(cutover, "isoformat") else str(cutover),
        "onboarded": bool(row["onboarded"]),
        "onboarded_at": row["onboarded_at"].isoformat() if hasattr(row["onboarded_at"], "isoformat") else row["onboarded_at"],
        "approved_by": row["approved_by"],
        "evidence_document_id": row["evidence_document_id"],
        "journal_entry_ids": [str(item) for item in (row["journal_entry_ids"] or [])],
        "source_of_truth": "postgres" if mode in {"postgres_read", "postgres_write", "mongo_archive"} else "mongo",
        "readiness_status": status.readiness_status.value,
        "mode": mode,
        "read_source": status.read_source.value,
        "write_source": status.write_source.value,
        "config_present": True,
        "metadata": row.get("metadata") or {},
    }
