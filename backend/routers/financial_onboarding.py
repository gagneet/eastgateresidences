# @featuretrace:demo_bank — API surface for historical reconstruction batches that stage into Demo Bank (building-scoped).
# @featuretrace:financial-onboarding — API surface for financial-only onboarding of already-created schemes (building-scoped).
# Layer: router
# Data flow: /financials/onboarding/* evidence/cutover endpoints → financial_onboarding service →
#            finance.evidence_documents / journal_entries; reconstruction-batches/* →
#            reconstruction_batch_service → Mongo manifests → demo_bank_transactions; import-financial-data →
#            gap_tolerant_financial_import → annual_levies/levy_categories.
# Related: backend/services/onboarding/financial_onboarding.py; backend/services/reconstruction_batch_service.py
#           backend/services/onboarding/financial_onboarding.py
#           backend/services/reconstruction_batch_service.py
#           backend/services/levy_generation_service.py
#           backend/alembic/versions/0044_financial_onboarding_cutover.py
#           docs/migration/historical_ledger_reconciliation_plan01.md
#           docs/migration/historical_ledger_reconciliation_plan02.md
# Toggle: historical_financial_reconstruction, historical_reconstruction_posting
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from database import db
from db_postgres.repos.config_repo import resolve_scheme_context
from db_postgres.session import async_session_context, set_tenant
from services.financial_core.domain.entities import SchemeRef
from services.onboarding.financial_onboarding import (
    EvidenceDocumentCreate,
    FundBalance,
    create_evidence_document,
    get_cutover_config,
    onboard_building_financials,
)
from services.onboarding.gap_tolerant_financial_import import (
    FinancialImportError,
    import_gap_tolerant_financial_data,
)
from services.reconstruction_generators.budget_levy_generator import (
    GENERATION_METHOD,
    ReconstructionGenerationError,
    generate_manifest_from_proposed_budget,
)
from services.reconstruction_generators.expense_category_generator import (
    generate_expense_manifest_from_categories,
)
from services.reconstruction_generators.expense_posting_service import (
    ExpensePostingError,
    run_expense_posting,
    validate_batch_authorisation,
)
from services.reconstruction_generators.historical_levy_charge_generator import (
    LevyChargeGenerationError,
    generate_historical_levy_charge_plan,
)
from integrations.demo_bank.reconstruction_batch_schemas import (
    FundBalanceReconciliationLine,
    ManifestLevyChargeLine,
    ReconstructionManifest,
)
from utils.auth import effective_role, get_current_building, get_current_user
from utils.file_scan import scan_upload
from utils.helpers import create_audit_log
from utils.permissions import get_effective_feature_access, require_feature

router = APIRouter(prefix="")

_FINANCE_ARCHIVE_COLLECTIONS = (
    "annual_levies",
    "unit_levy_ledger",
    "levy_payments",
    "expense_transactions",
    "income_transactions",
    "bank_accounts",
    "strata_financials",
    "building_summaries",
)
_ALLOWED_FINANCE_ROLES = {"super_admin", "strata_admin", "strata_manager"}

# ── Historical reconstruction RBAC (docs/migration/historical_ledger_reconciliation_plan02.md
#    amendment 13) — role guards are inline effective_role() checks per this repo's
#    mandatory pattern (never raw current_user["role"]), not the Permission-dataclass
#    system, matching the existing _require_finance_admin convention in this file. ──
_RECONSTRUCTION_UPLOAD_ROLES = {"super_admin", "strata_admin", "strata_manager"}
_RECONSTRUCTION_REVIEW_ROLES = {"super_admin", "strata_admin"}
_RECONSTRUCTION_APPROVE_ROLES = {"super_admin", "strata_admin"}
_RECONSTRUCTION_APPLY_ROLES = {"super_admin"}
_RECONSTRUCTION_SYNC_ROLES = {"super_admin", "strata_admin"}
_RECONSTRUCTION_REVERSE_ROLES = {"super_admin"}


def _require_role(current_user: dict, allowed: set[str], action: str) -> None:
    role = effective_role(current_user)
    if role not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"{action} requires one of {sorted(allowed)}; caller has role={role!r}",
        )


class EvidenceDocumentRequest(BaseModel):
    document_type: str = Field(
        ..., pattern="^(bank_statement|reconciliation|xero_export|agm_pack|invoice|quote|historical_expense_import|opening_balance)$"
    )
    file_url: str
    sha256_hash: str = Field(..., min_length=64, max_length=64)
    uploaded_by: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    declared_total_cents: int = Field(...)
    declared_totals_by_fund: dict[str, int] = Field(..., min_length=1)
    source_system: Optional[str] = None
    import_batch_id: Optional[str] = None
    notes: Optional[str] = None
    supersedes_document_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_test_data: bool = False


class FundBalanceRequest(BaseModel):
    fund_code: str
    amount_cents: int


class FinancialOnboardingRequest(BaseModel):
    cutover_date: date
    opening_balances: list[FundBalanceRequest]
    evidence_document_id: str
    approved_by_user_id: Optional[str] = None


def _require_finance_admin(current_user: dict) -> None:
    """Allow only platform finance operators to run financial onboarding endpoints."""
    role = effective_role(current_user)
    if role not in _ALLOWED_FINANCE_ROLES:
        raise HTTPException(status_code=403, detail="Finance super_admin, strata_admin, or strata_manager role required")


def _building_id_from_scheme_number(scheme_number: str | None) -> str | None:
    """Generated function header.

    Function: _building_id_from_scheme_number
    Path: backend/routers/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not scheme_number:
        return None
    value = str(scheme_number).strip()
    upper = value.upper()
    if upper.startswith("UP"):
        return upper.removeprefix("UP") or None
    return value


async def _resolve_building_context(building_id: str) -> dict[str, Any]:
    """Generated function header.

    Function: _resolve_building_context
    Path: backend/routers/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    scheme = await resolve_scheme_context(building_id)
    if not scheme:
        raise HTTPException(status_code=400, detail=f"No PostgreSQL scheme mapping found for building_id={building_id}")
    return scheme


def _resolved_plan_number(scheme: dict[str, Any]) -> str:
    """Generated function header.

    Function: _resolved_plan_number
    Path: backend/routers/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _building_id_from_scheme_number(scheme.get("scheme_number")) or str(scheme["scheme_id"])


async def _assert_building_access(path_building_id: str, current_building: str, current_user: dict) -> str:
    """Generated function header.

    Function: _assert_building_access
    Path: backend/routers/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    role = effective_role(current_user)
    path_scheme = await _resolve_building_context(path_building_id)
    if role != "super_admin":
        current_scheme = await _resolve_building_context(current_building)
        if str(path_scheme["scheme_id"]) != str(current_scheme["scheme_id"]):
            raise HTTPException(status_code=403, detail="Cannot access financial data for another building")
    return _resolved_plan_number(path_scheme)


def _json_safe(value: Any) -> Any:
    """Generated function header.

    Function: _json_safe
    Path: backend/routers/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "binary") and hasattr(value, "generation_time"):
        return str(value)
    return value


@router.post("/financials/onboarding/evidence")
async def create_financial_evidence_document(
    request: EvidenceDocumentRequest,
    current_user: dict = Depends(get_current_user),
    building_id: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Generated function header.

    Function: create_financial_evidence_document
    Path: backend/routers/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_finance_admin(current_user)
    resolved_building_id = _resolved_plan_number(await _resolve_building_context(building_id))
    uploaded_by = request.uploaded_by or current_user.get("id")
    approved_by = request.approved_by or current_user.get("id")
    try:
        return await create_evidence_document(
            EvidenceDocumentCreate(
                building_id=resolved_building_id,
                document_type=request.document_type,
                file_url=request.file_url,
                sha256_hash=request.sha256_hash,
                uploaded_by=str(uploaded_by),
                approved_by=str(approved_by),
                approved_at=request.approved_at,
                declared_total_cents=request.declared_total_cents,
                declared_totals_by_fund=request.declared_totals_by_fund,
                source_system=request.source_system,
                import_batch_id=request.import_batch_id,
                notes=request.notes,
                supersedes_document_id=request.supersedes_document_id,
                metadata=request.metadata,
                is_test_data=request.is_test_data,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/financials/onboarding/building/{building_id}")
async def onboard_financials_for_building(
    building_id: str,
    request: FinancialOnboardingRequest,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_reconstruction_posting")),
):
    """Generated function header.

    Function: onboard_financials_for_building
    Path: backend/routers/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_finance_admin(current_user)
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    approved_by = request.approved_by_user_id or current_user.get("id")
    try:
        result = await onboard_building_financials(
            building_id=resolved_building_id,
            cutover_date=request.cutover_date,
            opening_balances=[
                FundBalance(fund_code=item.fund_code, amount_cents=item.amount_cents)
                for item in request.opening_balances
            ],
            evidence_document_id=request.evidence_document_id,
            approved_by_user_id=str(approved_by),
        )
        return {
            "building_id": result.building_id,
            "cutover_date": result.cutover_date.isoformat(),
            "source_of_truth": result.source_of_truth,
            "readiness_status": result.readiness_status,
            "cutover_mode": result.cutover_mode,
            "read_source": result.read_source,
            "write_source": result.write_source,
            "onboarded": result.onboarded,
            "evidence_document_id": result.evidence_document_id,
            "evidence_sha256_hash": result.evidence_sha256_hash,
            "journal_entry_ids": result.journal_entry_ids,
            "opening_balances": result.opening_balances,
        }
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        message = str(exc)
        status = 409 if "already" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc


@router.get("/financials/cutover/{building_id}")
async def get_financial_cutover_config(
    building_id: str,
    as_of: Optional[date] = Query(default=None),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Generated function header.

    Function: get_financial_cutover_config
    Path: backend/routers/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_finance_admin(current_user)
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)
    try:
        return await get_cutover_config(resolved_building_id, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/financials/archive/{building_id}")
async def get_financial_archive(
    building_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Generated function header.

    Function: get_financial_archive
    Path: backend/routers/financial_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    _require_finance_admin(current_user)
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    archive: dict[str, Any] = {}
    for collection_name in _FINANCE_ARCHIVE_COLLECTIONS:
        cursor = db._db[collection_name].find({"building_id": resolved_building_id}).limit(limit)
        docs = await cursor.to_list(length=limit)
        archive[collection_name] = [_json_safe(doc) for doc in docs]

    return {
        "building_id": resolved_building_id,
        "archive_source": "mongo",
        "read_only": True,
        "pre_cutover_data": True,
        "note": "MongoDB financial records are retained as historical read-only archive data and are not replayed into PostgreSQL.",
        "collections": archive,
    }


# ════════════════════════════════════════════════════════════════════════════
# Historical reconstruction batch/manifest workflow
# (docs/migration/historical_ledger_reconciliation_plan01.md, plan02.md)
#
# Approval and apply are deliberately separate endpoints — never combined —
# per plan02's explicit instruction. Every write here is either Mongo workflow
# state (demo_bank_reconstruction_batches/manifests) or, for regenerate-apply
# only, a controlled UPDATE-in-place of finance.levy_items. Nothing here calls
# /bank-feeds/sync or posts ledger entries.
# ════════════════════════════════════════════════════════════════════════════

class CreateReconstructionBatchRequest(BaseModel):
    financial_year_start: int
    financial_year_end: int
    reconstruction_method: str = GENERATION_METHOD
    source_document_ids: list[str] = Field(default_factory=list)
    onboarding_session_id: Optional[str] = None
    is_test_data: bool = False


class ReviewReconstructionBatchRequest(BaseModel):
    notes: Optional[str] = Field(default=None, max_length=2000)


class ApproveReconstructionBatchRequest(BaseModel):
    """Intentionally carries no reviewed_by/approved_by fields.

    Both identities are always derived from the authenticated caller
    (current_user), never accepted from the client — a caller-supplied user
    id here would let anyone attribute a review/approval to someone else.
    Review and approval are now separate endpoints (POST .../review, then
    POST .../approve) and the router enforces the approver must be a
    DIFFERENT authenticated user than whoever recorded the review — see
    approve_reconstruction_batch's docstring for the genuine dual-control
    enforcement."""


class RejectReconstructionBatchRequest(BaseModel):
    # min_length alone does not reject a whitespace-only string ("   " satisfies
    # min_length=3) — the router also strips + re-checks before persisting, same
    # defence RegenerateLevyApplyRequest.override_reason needs for the same reason.
    reason: str = Field(..., min_length=3, max_length=2000)


class ReverseReconstructionBatchRequest(BaseModel):
    reason: str


class RegenerateLevyApplyRequest(BaseModel):
    confirm: bool = False
    override_reconciliation_mismatch: bool = False
    override_reason: Optional[str] = Field(
        default=None, min_length=10, max_length=1000,
        description="Required, recorded justification when override_reconciliation_mismatch=True.",
    )


def _mongo(building_id: str):
    """Reconstruction batch/manifest collections are plain Motor access
    (db._db) — every query in reconstruction_batch_service.py already filters
    by building_id explicitly, so the TenantScopedDatabase wrapper's context
    requirement is unnecessary overhead here, matching get_financial_archive's
    existing db._db[...] pattern in this same file."""
    return db._db


_RECONCILIATION_TOLERANCE_CENTS = 100  # $1 — rounding/date-basis slack, not a hard gate


async def _fund_balance_reconciliation(
    mongo_db, *, building_id: str, batch, warnings: list[str],
) -> tuple[list[FundBalanceReconciliationLine], list[dict[str, Any]]]:
    """Thin adapter over annual_fund_reconciliation.compute_annual_fund_reconciliation() — the
    direct, year-by-year annual-control reconciliation, entirely decoupled from this batch's own
    synthetic income/expense transactions. The generated Demo Bank rows are modelling assumptions;
    they are not evidence that the building's actual historical cash movements occurred.

    Returns (fund_balance_reconciliation, annual_fund_reconciliation) — the first flattened into
    FundBalanceReconciliationLine for compatibility with the existing manifest schema; the second
    is the richer year-keyed detail (opening provenance, control totals, bridge status, cutoff
    flags) attached at manifest.calculation_configuration["annual_fund_reconciliation"]."""
    from services.reconstruction_generators.annual_fund_reconciliation import compute_annual_fund_reconciliation
    from services.reconstruction_generators.reconciliation_exceptions import list_exceptions

    exceptions = await list_exceptions(
        mongo_db, building_id=building_id,
        financial_year_start=batch.financial_year_start, financial_year_end=batch.financial_year_end,
    )
    annual_lines = await compute_annual_fund_reconciliation(
        mongo_db, building_id=building_id,
        financial_year_start=batch.financial_year_start, financial_year_end=batch.financial_year_end,
        warnings=warnings, exceptions=exceptions,
    )

    fund_balance_lines: list[FundBalanceReconciliationLine] = []
    for line in annual_lines:
        if line.declared_closing_balance_cents is None or line.expected_closing_cents is None:
            continue
        fund_balance_lines.append(FundBalanceReconciliationLine(
            account_ref=f"{line.fund_type.upper()}-{building_id} ({line.year})",
            opening_balance_cents=line.opening_balance_cents,
            reconstructed_credits_cents=line.actual_income_cents or 0,
            reconstructed_debits_cents=line.actual_spent_cents or 0,
            closing_balance_cents=line.declared_closing_balance_cents,
            matches_expected_closing=bool(line.within_tolerance),
        ))

    return fund_balance_lines, [l.as_dict() for l in annual_lines]


async def _build_manifest_for_batch(mongo_db, building_id: str, batch) -> Any:
    """Dispatch to the generic budget-based generator (default for new batches)
    or the legacy East-Gate-only retroactive-linking path, by reconstruction_method.
    Shared by preview_reconstruction_batch and submit_reconstruction_batch_for_review
    so the two endpoints can never drift on which path a given batch takes.

    For GENERATION_METHOD, merges TWO independent generators into one manifest: the income
    (owner-payment) side and the expense (actual category spend) side — a building can
    legitimately have data for only one of the two, so neither side's own "nothing to generate"
    error is treated as fatal here; only "both sides came back empty" is."""
    from services import reconstruction_batch_service as rbs

    if batch.reconstruction_method == GENERATION_METHOD:
        scheme = await _resolve_building_context(building_id)
        scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))
        income_manifest: ReconstructionManifest | None = None
        income_error: str | None = None
        levy_charge_lines: list[ManifestLevyChargeLine] = []
        levy_charge_planned_not_posted_count = 0
        levy_charge_error: str | None = None
        async with async_session_context() as session:
            await set_tenant(session, scheme_ref.tenant_id)
            try:
                income_manifest = await generate_manifest_from_proposed_budget(
                    mongo_db=mongo_db, pg_session=session, scheme_ref=scheme_ref,
                    building_id=building_id, batch=batch,
                )
            except ReconstructionGenerationError as exc:
                income_error = str(exc)

            # Levy CHARGES (receivable obligations) — included in the SAME manifest/batch as the
            # income/expense Demo Bank rows above so both share one dual-control approval
            # workflow (product decision 2026-07-31), but never materialised into
            # demo_bank_transactions — see ManifestLevyChargeLine's docstring.
            try:
                charge_plan = await generate_historical_levy_charge_plan(
                    mongo_db=mongo_db, pg_session=session, scheme_ref=scheme_ref,
                    building_id=building_id, from_year=batch.financial_year_start,
                    to_year=batch.financial_year_end, as_of_date=date.today(),
                )
                levy_charge_lines = [
                    ManifestLevyChargeLine(
                        unit_number=line.unit_number, lot_id=str(line.lot_id),
                        owner_party_id=str(line.owner_party_id),
                        owner_resolution_status=line.owner_resolution_status,
                        financial_year=str(line.year), fund_type=line.fund_type,
                        levy_type=line.levy_type, period=line.period, quarter_no=line.quarter_no,
                        issue_date=line.issue_date, due_date=line.due_date, uoe=line.uoe,
                        total_uoe=line.total_uoe, principal_cents=line.principal_cents,
                        gst_cents=line.gst_cents, total_cents=line.total_cents,
                        source_annual_control=line.source_annual_control,
                        derivation_type=line.derivation_type, idempotency_key=line.idempotency_key,
                    )
                    for line in charge_plan.lines
                ]
                levy_charge_planned_not_posted_count = len(charge_plan.planned_not_posted)
            except LevyChargeGenerationError as exc:
                levy_charge_error = str(exc)

        expense_transactions, expense_warnings = await generate_expense_manifest_from_categories(
            mongo_db=mongo_db, building_id=building_id, batch=batch,
        )

        income_transactions = list(income_manifest.transactions) if income_manifest else []
        income_credit_cents = income_manifest.expected_credit_cents if income_manifest else 0
        income_warnings = list(income_manifest.warnings) if income_manifest else (
            [income_error] if income_error else []
        )
        if levy_charge_error:
            income_warnings.append(f"Levy charge generator: {levy_charge_error}")
        if levy_charge_planned_not_posted_count:
            income_warnings.append(
                f"{levy_charge_planned_not_posted_count} levy charge line(s) fall after today's "
                "date and are excluded from this manifest as planned_not_posted."
            )

        if not income_transactions and not expense_transactions and not levy_charge_lines:
            detail = f"Nothing to generate for building_id={building_id!r} years " \
                     f"{batch.financial_year_start}-{batch.financial_year_end}: no owner-payment " \
                     "income, no category expense data, and no levy charges resolved for this range."
            if income_error:
                detail += f" Income generator reported: {income_error}"
            if levy_charge_error:
                detail += f" Levy charge generator reported: {levy_charge_error}"
            raise ReconstructionGenerationError(detail)

        expense_debit_cents = sum(t.amount_cents for t in expense_transactions)
        levy_charge_total_cents = sum(l.total_cents for l in levy_charge_lines)
        all_transactions = income_transactions + expense_transactions
        all_warnings = income_warnings + expense_warnings

        fund_balance_reconciliation, annual_fund_reconciliation = await _fund_balance_reconciliation(
            mongo_db, building_id=building_id, batch=batch, warnings=all_warnings,
        )

        input_hashes = sorted(
            f"{t.account_ref}:{t.unit_number}:{t.financial_year}:{t.quarter}:{t.fund_type}:"
            f"{t.direction}:{t.amount_cents}"
            for t in all_transactions
        )
        input_hashes += sorted(
            f"charge:{l.unit_number}:{l.financial_year}:{l.fund_type}:{l.period}:{l.total_cents}"
            for l in levy_charge_lines
        )
        input_fact_hash = hashlib.sha256("|".join(input_hashes).encode()).hexdigest()
        manifest_hash = hashlib.sha256(
            f"{building_id}:{GENERATION_METHOD}:{len(all_transactions)}:{income_credit_cents}:"
            f"{expense_debit_cents}:{len(levy_charge_lines)}:{levy_charge_total_cents}:"
            f"{input_fact_hash}".encode()
        ).hexdigest()

        return ReconstructionManifest(
            manifest_id=str(uuid4()),
            batch_id=batch.batch_id,
            building_id=building_id,
            version=1,
            input_document_hashes=[],
            input_fact_hash=input_fact_hash,
            calculation_configuration={
                "generation_method": GENERATION_METHOD,
                "income_transaction_count": len(income_transactions),
                "expense_transaction_count": len(expense_transactions),
                "levy_charge_line_count": len(levy_charge_lines),
                "levy_charge_total_cents": levy_charge_total_cents,
                "levy_charge_planned_not_posted_count": levy_charge_planned_not_posted_count,
                # Authoritative year-by-year annual-control reconciliation (see
                # annual_fund_reconciliation.py) — richer than fund_balance_reconciliation above,
                # which is a backward-compatible flattening into the sibling repo's frozen,
                # year-less FundBalanceReconciliationLine shape. This dict is the real source of
                # truth for opening provenance, control totals, bridge/exception netting, and
                # partial-period-mismatch status.
                "annual_fund_reconciliation": annual_fund_reconciliation,
            },
            generator_version=GENERATION_METHOD,
            expected_transaction_count=len(all_transactions),
            expected_credit_cents=income_credit_cents,
            expected_debit_cents=expense_debit_cents,
            transactions=all_transactions,
            levy_charge_lines=levy_charge_lines,
            fund_balance_reconciliation=fund_balance_reconciliation,
            warnings=all_warnings,
            manifest_hash=manifest_hash,
            generated_at=datetime.now(timezone.utc),
            generated_by=batch.created_by,
            is_test_data=batch.is_test_data,
        )
    return await rbs.link_existing_rows_as_manifest(mongo_db, building_id=building_id, batch=batch)


@router.get("/financials/onboarding/building/{building_id}/historical-data-status")
async def get_historical_data_status(
    building_id: str,
    from_year: int = Query(...),
    to_year: int = Query(...),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Answers "does this building already have financial history for this range?"
    without needing a reconstruction batch to exist first — the pre-flight check
    the financial-onboarding UI shows before an admin commits to creating a batch."""
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Checking historical data status")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from scripts.migrations.migration_027_randomize_east_gate_demo_bank_levies import _load_units
    from services.levy_generation_service import _resolve_current_levy_items
    from services.reconstruction_generators.budget_levy_generator import _paid_periods

    scheme = await _resolve_building_context(resolved_building_id)
    scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))
    mongo_db = _mongo(resolved_building_id)

    units = await _load_units(mongo_db, resolved_building_id)
    async with async_session_context() as session:
        await set_tenant(session, scheme_ref.tenant_id)
        existing_items = await _resolve_current_levy_items(session, scheme_ref.scheme_id, mongo_db, resolved_building_id)

    years_in_range = {str(y) for y in range(from_year, to_year + 1)}
    # _resolve_current_levy_items() reads Mongo units with no UOE filter, but _load_units()
    # (the same universe total_period_count is built from) excludes units with uoe<=0 — without
    # this intersection, paid_period_count could count a unit total_period_count never includes,
    # producing a nonsensical paid_period_count > total_period_count.
    generator_unit_numbers = {u.unit_number for u in units}
    paid_period_count = sum(
        1 for (year, _fund, unit_number) in _paid_periods(existing_items)
        if year in years_in_range and unit_number in generator_unit_numbers
    )
    # Two funds (admin, sinking) x every unit x every year in the requested range —
    # the full universe of lot/year/fund combinations the generator would consider.
    total_period_count = len(units) * len(years_in_range) * 2
    return {
        "building_id": resolved_building_id,
        "unit_count": len(units),
        "has_any_paid_periods": paid_period_count > 0,
        "paid_period_count": paid_period_count,
        "total_period_count": total_period_count,
    }


class LevyChargePreviewRequest(BaseModel):
    from_year: int
    to_year: int
    as_of_date: Optional[date] = None
    is_test_data: bool = False


@router.post("/financials/onboarding/building/{building_id}/levy-charges/preview")
async def preview_historical_levy_charges(
    building_id: str,
    request: LevyChargePreviewRequest,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Pure, unpersisted, batch-INDEPENDENT preview of the levy charge schedule
    `generate_historical_levy_charge_plan()` would produce right now — no batch required, no
    writes performed. Distinct from a reconstruction batch's own persisted manifest (which
    already includes `levy_charge_lines` once generated for a GENERATION_METHOD batch — see
    `_build_manifest_for_batch()`); this endpoint exists so the UI/an operator can sanity-check
    the levy-charge numbers before committing to creating a batch at all (per
    docs/finances/reconcilation-2021-2022-finances-plan02.md's "preview should not require a
    batch ID" point). Imports no privileged closed-period-posting types (see
    tests/backend/test_financial_core_service.py::TestHistoricalPathwayIsolation) — posting only
    ever happens via backend/scripts/historical_levy_charge_posting.py against an
    already-approved batch.
    """
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Previewing historical levy charges")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    scheme = await _resolve_building_context(resolved_building_id)
    scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))
    mongo_db = _mongo(resolved_building_id)
    as_of_date = request.as_of_date or date.today()

    async with async_session_context() as session:
        await set_tenant(session, scheme_ref.tenant_id)
        try:
            plan = await generate_historical_levy_charge_plan(
                mongo_db=mongo_db, pg_session=session, scheme_ref=scheme_ref,
                building_id=resolved_building_id, from_year=request.from_year,
                to_year=request.to_year, as_of_date=as_of_date,
            )
        except LevyChargeGenerationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    from services.reconstruction_generators.historical_levy_charge_generator import (
        summarize_by_year_fund_period,
    )

    return {
        "building_id": resolved_building_id,
        "from_year": request.from_year,
        "to_year": request.to_year,
        "as_of_date": as_of_date.isoformat(),
        "postable_line_count": len(plan.lines),
        "planned_not_posted_count": len(plan.planned_not_posted),
        "skipped_already_charged": plan.skipped_already_charged,
        "total_principal_cents": plan.total_principal_cents,
        "total_gst_cents": plan.total_gst_cents,
        "by_year_fund_period": summarize_by_year_fund_period(plan),
        "warnings": plan.warnings,
    }


@router.post("/financials/onboarding/building/{building_id}/import-financial-data")
async def import_financial_data(
    building_id: str,
    file: UploadFile = File(...),
    is_test_data: bool = Form(False),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Gap-tolerant CSV/JSON upload of historical financial data for the requested building —
    proposed Admin/Sinking budget, closing balances, and actual category spend, any subset, any
    combination of years. Upserts into annual_levies/levy_categories (the same collections the
    reconstruction generators already read) and returns a coverage summary of what's now present
    per year so the caller can decide whether to generate a reconstruction batch yet or upload
    more data first. Building-agnostic — works for any building the caller has access to, not
    just East Gate. `is_test_data=True` (perf/test scripts only, never a production default)
    stamps every written annual_levies/levy_categories doc so the generators can exclude it from
    real reconstructions and the standard is_test_data sweep can clean it up."""
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Importing historical financial data")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    content = await file.read()
    await scan_upload(content, context="document", filename=file.filename or "")

    try:
        result = await import_gap_tolerant_financial_data(
            _mongo(resolved_building_id), building_id=resolved_building_id, content=content,
            filename=file.filename or "", uploaded_by=str(current_user.get("id")),
            is_test_data=is_test_data,
        )
    except FinancialImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await create_audit_log(
        action="financial_data_imported", resource_type="annual_levies",
        resource_id=resolved_building_id, user_id=str(current_user.get("id")),
        user_name=current_user.get("full_name", "unknown"),
        details={
            "filename": file.filename, "years_touched": result["years_touched"],
            "fund_rows_written": result["fund_rows_written"],
            "category_rows_written": result["category_rows_written"],
        },
        building_id=resolved_building_id,
    )
    return result


@router.delete("/financials/onboarding/building/{building_id}/test-data")
async def delete_financial_onboarding_test_data(
    building_id: str,
    year: Optional[int] = Query(default=None, description="Restrict cleanup to one financial year."),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Delete only financial-onboarding rows explicitly stamped `is_test_data=True`.

    This is intentionally narrow: k6 and integration tests can clean the annual_levies,
    levy_categories, reconstruction batch, and manifest records they create without exposing a
    general deletion path for real onboarding evidence or posted finance data.
    """
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Cleaning financial onboarding test data")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)
    mongo_db = _mongo(resolved_building_id)

    batch_query: dict[str, Any] = {"building_id": resolved_building_id, "is_test_data": True}
    if year is not None:
        batch_query["financial_year_start"] = {"$lte": year}
        batch_query["financial_year_end"] = {"$gte": year}

    batch_docs = await mongo_db.demo_bank_reconstruction_batches.find(
        batch_query, {"_id": 0, "batch_id": 1}
    ).to_list(length=None)
    batch_ids = [doc["batch_id"] for doc in batch_docs if doc.get("batch_id")]

    manifest_deleted = 0
    if batch_ids:
        manifest_result = await mongo_db.demo_bank_reconstruction_manifests.delete_many({
            "building_id": resolved_building_id,
            "is_test_data": True,
            "batch_id": {"$in": batch_ids},
        })
        manifest_deleted = manifest_result.deleted_count

    batch_result = await mongo_db.demo_bank_reconstruction_batches.delete_many(batch_query)

    yearly_query: dict[str, Any] = {"building_id": resolved_building_id, "is_test_data": True}
    if year is not None:
        yearly_query["year"] = {"$in": [str(year), year]}

    levy_result = await mongo_db.annual_levies.delete_many(yearly_query)
    category_result = await mongo_db.levy_categories.delete_many(yearly_query)

    exception_query: dict[str, Any] = {"building_id": resolved_building_id, "is_test_data": True}
    if year is not None:
        exception_query["year"] = year
    exception_result = await mongo_db.reconciliation_annual_exceptions.delete_many(exception_query)

    await create_audit_log(
        action="financial_onboarding_test_data_deleted",
        resource_type="financial_onboarding_test_data",
        resource_id=resolved_building_id,
        user_id=str(current_user.get("id")),
        user_name=current_user.get("full_name", "unknown"),
        details={"year": year, "is_test_data": True},
        building_id=resolved_building_id,
    )
    return {
        "annual_levies_deleted": levy_result.deleted_count,
        "levy_categories_deleted": category_result.deleted_count,
        "reconstruction_batches_deleted": batch_result.deleted_count,
        "reconstruction_manifests_deleted": manifest_deleted,
        "reconciliation_exceptions_deleted": exception_result.deleted_count,
    }


class CreateReconciliationExceptionRequest(BaseModel):
    year: int
    fund_type: str
    amount_cents: int = Field(..., gt=0)
    direction: str
    reason_code: str
    effect_scope: str
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/financials/onboarding/building/{building_id}/reconciliation-exceptions")
async def create_reconciliation_exception(
    building_id: str,
    request: CreateReconciliationExceptionRequest,
    is_test_data: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """A lightweight, admin-entered explanation (and, only when effect_scope=
    "annual_control_adjustment", a genuine accounting adjustment) for a gap
    annual_fund_reconciliation.py surfaces. Deliberately NOT a propose/approve governance
    workflow — see services/reconstruction_generators/reconciliation_exceptions.py's module
    docstring for why. Building-scoped, not batch-scoped: these describe a building's
    accounting history independent of any one reconstruction attempt."""
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Recording a reconciliation exception")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services.reconstruction_generators.reconciliation_exceptions import (
        ReconciliationExceptionError, create_exception,
    )

    try:
        doc = await create_exception(
            _mongo(resolved_building_id), building_id=resolved_building_id,
            year=request.year, fund_type=request.fund_type, amount_cents=request.amount_cents,
            direction=request.direction, reason_code=request.reason_code,
            effect_scope=request.effect_scope, note=request.note,
            created_by=str(current_user.get("id")), is_test_data=is_test_data,
        )
    except ReconciliationExceptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await create_audit_log(
        action="reconciliation_exception_created", resource_type="reconciliation_annual_exception",
        resource_id=doc["id"], user_id=str(current_user.get("id")),
        user_name=current_user.get("full_name", "unknown"),
        details={
            "year": request.year, "fund_type": request.fund_type,
            "amount_cents": request.amount_cents, "reason_code": request.reason_code,
            "effect_scope": request.effect_scope,
        },
        building_id=resolved_building_id,
    )
    return doc


@router.get("/financials/onboarding/building/{building_id}/reconciliation-exceptions")
async def list_reconciliation_exceptions(
    building_id: str,
    from_year: int = Query(...),
    to_year: int = Query(...),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Viewing reconciliation exceptions")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services.reconstruction_generators.reconciliation_exceptions import list_exceptions

    docs = await list_exceptions(
        _mongo(resolved_building_id), building_id=resolved_building_id,
        financial_year_start=from_year, financial_year_end=to_year,
    )
    return {"exceptions": docs}


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches")
async def create_reconstruction_batch(
    building_id: str,
    request: CreateReconstructionBatchRequest,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Create a new historical-reconstruction batch in status=draft."""
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Creating a reconstruction batch")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services import reconstruction_batch_service as rbs

    batch = await rbs.create_batch(
        _mongo(resolved_building_id),
        building_id=resolved_building_id,
        financial_year_start=request.financial_year_start,
        financial_year_end=request.financial_year_end,
        reconstruction_method=request.reconstruction_method,
        created_by=str(current_user.get("id")),
        source_document_ids=request.source_document_ids,
        onboarding_session_id=request.onboarding_session_id,
        is_test_data=request.is_test_data,
    )
    await create_audit_log(
        action="reconstruction_batch_created", resource_type="demo_bank_reconstruction_batch",
        resource_id=batch.batch_id, user_id=str(current_user.get("id")),
        user_name=current_user.get("full_name", "unknown"),
        details={"reconstruction_method": request.reconstruction_method}, building_id=resolved_building_id,
    )
    return batch.model_dump(mode="json")


# ── Read endpoints (list / detail / manifest) ──────────────────────────────────
# Added per docs/migration/build-demo-data-ui-system.md finding 1.1: the workflow
# endpoints above are action-oriented only; a management UI needs a real list/
# detail/manifest read surface rather than reconstructing state from action
# responses or a browser-session cache.

_VALID_BATCH_STATUSES = {
    "draft", "extracted", "validation_failed", "needs_review", "approved",
    "generation_ready", "generated", "syncing", "synced", "matching",
    "partially_posted", "posted", "failed", "superseded", "reversed",
}


class ReconstructionBatchSummary(BaseModel):
    """Summary projection for the batch list endpoint — omits the larger
    validation_results/assumptions/warnings blobs carried on the full batch
    (fetch GET .../reconstruction-batches/{batch_id} for those)."""

    batch_id: str
    building_id: str
    financial_year_start: int
    financial_year_end: int
    reconstruction_method: str
    status: str
    unit_count: Optional[int] = None
    generated_credit_cents: int = 0
    reconciliation_variance_cents: int = 0
    created_by: Optional[str] = None
    reviewed_by: Optional[str] = None
    approved_by: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    is_test_data: bool = False


class ReconstructionBatchListResponse(BaseModel):
    items: list[ReconstructionBatchSummary]
    next_cursor: Optional[str] = None


@router.get(
    "/financials/onboarding/building/{building_id}/reconstruction-batches",
    response_model=ReconstructionBatchListResponse,
)
async def list_reconstruction_batches(
    building_id: str,
    status: Optional[str] = Query(default=None, description="Filter to one ReconstructionBatchStatus value."),
    from_year: Optional[int] = Query(default=None, description="Include batches whose year range ends >= from_year."),
    to_year: Optional[int] = Query(default=None, description="Include batches whose year range starts <= to_year."),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None, description="Opaque cursor from a prior page's next_cursor."),
    include_test_data: bool = Query(default=False),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Server-paginated batch list, newest first, building-scoped."""
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Listing reconstruction batches")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    if status is not None and status not in _VALID_BATCH_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown status={status!r}; expected one of {sorted(_VALID_BATCH_STATUSES)}",
        )

    query: dict[str, Any] = {"building_id": resolved_building_id}
    if status is not None:
        query["status"] = status
    if from_year is not None:
        query["financial_year_end"] = {"$gte": from_year}
    if to_year is not None:
        query["financial_year_start"] = {"$lte": to_year}
    if not include_test_data:
        query["is_test_data"] = {"$ne": True}
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed cursor") from exc
        query["created_at"] = {"$lt": cursor_dt}

    mongo_db = _mongo(resolved_building_id)
    raw_docs = await (
        mongo_db.demo_bank_reconstruction_batches.find(query)
        .sort("created_at", -1)
        .limit(limit + 1)
        .to_list(length=limit + 1)
    )

    has_more = len(raw_docs) > limit
    page_docs = raw_docs[:limit]
    items = [
        ReconstructionBatchSummary(
            batch_id=doc["batch_id"],
            building_id=doc["building_id"],
            financial_year_start=doc["financial_year_start"],
            financial_year_end=doc["financial_year_end"],
            reconstruction_method=doc["reconstruction_method"],
            status=doc["status"],
            unit_count=doc.get("unit_count"),
            generated_credit_cents=doc.get("generated_credit_cents", 0),
            reconciliation_variance_cents=doc.get("reconciliation_variance_cents", 0),
            created_by=doc.get("created_by"),
            reviewed_by=doc.get("reviewed_by"),
            approved_by=doc.get("approved_by"),
            created_at=doc["created_at"],
            reviewed_at=doc.get("reviewed_at"),
            approved_at=doc.get("approved_at"),
            is_test_data=doc.get("is_test_data", False),
        )
        for doc in page_docs
    ]
    next_cursor = page_docs[-1]["created_at"].isoformat() if has_more and page_docs else None
    return ReconstructionBatchListResponse(items=items, next_cursor=next_cursor)


@router.get("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}")
async def get_reconstruction_batch(
    building_id: str,
    batch_id: str,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Full batch workflow-state detail. The manifest itself (potentially large:
    one row per generated transaction) is fetched separately via
    GET .../reconstruction-batches/{batch_id}/manifest."""
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Viewing a reconstruction batch")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services import reconstruction_batch_service as rbs

    try:
        batch_doc = await rbs._get_batch_doc(_mongo(resolved_building_id), resolved_building_id, batch_id)
    except rbs.ReconstructionWorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    batch = rbs._doc_to_batch(batch_doc)
    return batch.model_dump(mode="json")


@router.get("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/manifest")
async def get_reconstruction_batch_manifest(
    building_id: str,
    batch_id: str,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """The manifest payload for this batch (planned transactions, year/lot/fund
    reconciliation lines). 404s if no manifest has been generated yet (i.e.
    before generate-preview/submit-review has run for this batch) — Preview is
    a dry-run and does not persist a manifest; only submit-review does."""
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Viewing a reconstruction batch manifest")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    mongo_db = _mongo(resolved_building_id)
    manifest_doc = await mongo_db.demo_bank_reconstruction_manifests.find_one(
        {"building_id": resolved_building_id, "batch_id": batch_id}
    )
    if manifest_doc is None:
        raise HTTPException(status_code=404, detail=f"No manifest found for batch {batch_id!r}")

    from integrations.demo_bank.reconstruction_batch_schemas import ReconstructionManifest

    manifest = ReconstructionManifest(**{k: v for k, v in manifest_doc.items() if k != "_id"})
    return manifest.model_dump(mode="json")


@router.get("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/reconciliation-summary")
async def get_reconstruction_batch_reconciliation_summary(
    building_id: str,
    batch_id: str,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """The decision-basis view for a reviewer/approver: what does this batch's
    dollar total actually reconcile against, and how material are the
    exceptions?

    Deliberately a thin wrapper, not new computation — reuses
    build_levy_regeneration_plan() (levy_generation_service.py), which already
    computes the real 3-way reconciliation per year/fund: the proposed annual
    levy (annual_levies), the GST-aware regenerated charge total, and the
    already-generated Demo Bank payment total for that year/fund. That
    function's full line-level output (957 rows for East Gate) already backs
    the separate Levy Regeneration tab; this endpoint condenses the same
    computation to the year/fund aggregates and manual-review materiality a
    reviewer actually needs, without shipping the full line list twice.

    Returns 200 with totals_reconcile=None (not False) whenever
    build_levy_regeneration_plan() cannot compute a plan at all — either no
    units with positive UOE exist, or annual_levies is missing a doc for any
    year in the batch's range (both raise RuntimeError there). That's a
    different condition from "computed and found a mismatch" and must not be
    presented as one.
    """
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Viewing a reconstruction batch reconciliation summary")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services import reconstruction_batch_service as rbs

    mongo_db = _mongo(resolved_building_id)
    try:
        batch_doc = await rbs._get_batch_doc(mongo_db, resolved_building_id, batch_id)
    except rbs.ReconstructionWorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    batch = rbs._doc_to_batch(batch_doc)

    manifest_doc = await mongo_db.demo_bank_reconstruction_manifests.find_one(
        {"building_id": resolved_building_id, "batch_id": batch_id}
    )
    manifest_summary = None
    if manifest_doc is not None:
        manifest_summary = {
            "version": manifest_doc.get("version"),
            "expected_transaction_count": manifest_doc.get("expected_transaction_count"),
            "expected_credit_cents": manifest_doc.get("expected_credit_cents"),
            "manifest_hash": manifest_doc.get("manifest_hash"),
        }

    from services.levy_generation_service import build_levy_regeneration_plan

    scheme = await _resolve_building_context(resolved_building_id)
    scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))

    try:
        async with async_session_context() as session:
            await set_tenant(session, scheme_ref.tenant_id)
            plan = await build_levy_regeneration_plan(
                building_id=resolved_building_id, mongo_db=db._db, pg_session=session,
                scheme_ref=scheme_ref,
                from_year=batch.financial_year_start, to_year=batch.financial_year_end,
            )
    except RuntimeError as exc:
        # build_levy_regeneration_plan raises RuntimeError when no units with positive UOE
        # exist for the building — a real "cannot compute" state, not a mismatch. Surface it
        # as reconciles=None (unknown), never silently as False (would misreport "broken").
        return {
            "batch_id": batch_id,
            "manifest": manifest_summary,
            "by_year_fund": [],
            "manual_review": {"count": 0, "total_variance_cents": 0, "percentage_of_batch": None},
            "totals_reconcile": None,
            "warnings": [f"reconciliation_unavailable: {exc}"],
        }

    by_year_fund = [
        {
            "year": yf.year,
            "fund_type": yf.fund_type,
            "annual_levies_proposed_inc_gst_cents": yf.annual_levies_proposed_inc_gst_cents,
            "regenerated_levy_items_total_cents": yf.regenerated_levy_items_total_cents,
            "demo_bank_payment_total_cents": yf.synthetic_bank_payment_total_cents,
            "existing_levy_items_total_cents": yf.existing_levy_items_total_cents,
            "variance_cents": yf.variance_cents,
            "within_tolerance": yf.within_tolerance,
        }
        for yf in plan.by_year_fund
    ]

    manual_review_lines = [l for l in plan.lines if l.action == "manual_review_overpaid"]
    manual_review_total_variance_cents = sum(
        l.existing_paid_cents - (l.principal_cents + l.gst_cents) for l in manual_review_lines
    )
    batch_total_cents = (manifest_summary or {}).get("expected_credit_cents") or 0
    percentage_of_batch = (
        round(100 * manual_review_total_variance_cents / batch_total_cents, 2)
        if batch_total_cents else None
    )

    demo_bank_total_from_plan_cents = sum(yf.synthetic_bank_payment_total_cents for yf in plan.by_year_fund)
    warnings = list(plan.warnings)
    if manifest_summary and manifest_summary.get("expected_credit_cents") is not None \
            and demo_bank_total_from_plan_cents != manifest_summary["expected_credit_cents"]:
        # The manifest's own total and the regeneration plan's independently-recomputed
        # Demo Bank total for the same years should always agree — they read the same
        # demo_bank_transactions rows via different code paths. A mismatch here means the
        # batch's financial_year_start/end doesn't fully cover its own manifest, not a
        # levy-side data problem — surfaced explicitly rather than silently ignored.
        warnings.append(
            "manifest_year_range_mismatch: the batch's manifest total "
            f"({manifest_summary['expected_credit_cents']} cents) does not equal the sum of "
            f"Demo Bank totals for financial_year_start..end ({demo_bank_total_from_plan_cents} cents) "
            "— the batch's declared year range may not cover all of its own manifest rows."
        )

    return {
        "batch_id": batch_id,
        "manifest": manifest_summary,
        "by_year_fund": by_year_fund,
        "manual_review": {
            "count": len(manual_review_lines),
            "total_variance_cents": manual_review_total_variance_cents,
            "percentage_of_batch": percentage_of_batch,
        },
        "totals_reconcile": plan.totals_reconcile,
        "warnings": warnings,
    }


@router.get("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/audit-log")
async def get_reconstruction_batch_audit_log(
    building_id: str,
    batch_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Audit trail for this one batch — building-scoped AND resource_id-scoped,
    unlike the generic GET /notifications/admin/audit-logs (which filters only
    by resource_type/action, has no building_id filter at all, and excludes
    strata_manager — who can create and view batches per
    _RECONSTRUCTION_UPLOAD_ROLES here). Returns every create_audit_log() entry
    this router has written for the batch (created, extracted, submitted for
    review, reviewed, approved, generated, sync completed/failed, reversed)."""
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Viewing a reconstruction batch audit log")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    mongo_db = _mongo(resolved_building_id)
    cursor = mongo_db.audit_logs.find(
        {
            "resource_type": "demo_bank_reconstruction_batch",
            "resource_id": batch_id,
            "building_id": resolved_building_id,
        },
        {"_id": 0},
    ).sort("created_at", -1).limit(limit)
    entries = await cursor.to_list(length=limit)
    return {"batch_id": batch_id, "entries": entries}


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/extract")
async def extract_reconstruction_batch(
    building_id: str,
    batch_id: str,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Mark a batch as extracted (source facts have been gathered elsewhere —
    e.g. import-historical-expenses, or East Gate's existing Demo Bank rows)."""
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Extracting a reconstruction batch")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services import reconstruction_batch_service as rbs

    try:
        batch = await rbs._transition(_mongo(resolved_building_id), resolved_building_id, batch_id, to_status="extracted")
    except rbs.ReconstructionWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await create_audit_log(
        action="reconstruction_batch_extracted", resource_type="demo_bank_reconstruction_batch",
        resource_id=batch_id, user_id=str(current_user.get("id")), user_name=current_user.get("full_name", "unknown"),
        details={}, building_id=resolved_building_id,
    )
    return batch.model_dump(mode="json")


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/generate-preview")
async def preview_reconstruction_batch(
    building_id: str,
    batch_id: str,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Dry-run: compute the manifest without persisting it or changing status.

    Dispatches on `batch.reconstruction_method`: the generic, building-agnostic
    "generate_from_budget_v1" path (default for new batches) generates owner levy
    payments from the proposed budget; any other value falls back to the East-Gate-only
    retroactive-linking path (source_type=synthetic_from_budget rows already in
    demo_bank_transactions), unchanged.
    """
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Previewing a reconstruction batch")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services import reconstruction_batch_service as rbs

    mongo_db = _mongo(resolved_building_id)
    batch_doc = await rbs._get_batch_doc(mongo_db, resolved_building_id, batch_id)
    batch = rbs._doc_to_batch(batch_doc)
    try:
        manifest = await _build_manifest_for_batch(mongo_db, resolved_building_id, batch)
    except (rbs.ReconstructionWorkflowError, ReconstructionGenerationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return manifest.model_dump(mode="json")


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/submit-review")
async def submit_reconstruction_batch_for_review(
    building_id: str,
    batch_id: str,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Recompute the manifest fresh (never trust a client-cached preview),
    persist it, and move the batch to needs_review. This is the mandatory
    stopping point for any automated pipeline — approval is a separate,
    later, human governance action.

    Dispatches on `batch.reconstruction_method` — see preview_reconstruction_batch's
    docstring for the same dispatch."""
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Submitting a reconstruction batch for review")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services import reconstruction_batch_service as rbs

    mongo_db = _mongo(resolved_building_id)
    batch_doc = await rbs._get_batch_doc(mongo_db, resolved_building_id, batch_id)
    batch = rbs._doc_to_batch(batch_doc)
    try:
        manifest = await _build_manifest_for_batch(mongo_db, resolved_building_id, batch)
        updated = await rbs.submit_for_review(
            mongo_db, building_id=resolved_building_id, batch_id=batch_id, manifest=manifest,
            warnings=manifest.warnings,
        )
    except (rbs.ReconstructionWorkflowError, ReconstructionGenerationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await create_audit_log(
        action="reconstruction_batch_submitted_for_review", resource_type="demo_bank_reconstruction_batch",
        resource_id=batch_id, user_id=str(current_user.get("id")), user_name=current_user.get("full_name", "unknown"),
        details={"manifest_id": manifest.manifest_id, "transaction_count": manifest.expected_transaction_count},
        building_id=resolved_building_id,
    )
    return updated.model_dump(mode="json")


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/review")
async def review_reconstruction_batch(
    building_id: str,
    batch_id: str,
    request: ReviewReconstructionBatchRequest,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_reconstruction_posting")),
):
    """Dual-control step 1 of 2: records a human review of the needs_review
    manifest. Does NOT approve — POST .../approve is a separate, later call
    that additionally requires a DIFFERENT authenticated user (enforced
    there). Reviewer identity always derives from the authenticated caller,
    never accepted from the client."""
    _require_role(current_user, _RECONSTRUCTION_REVIEW_ROLES, "Reviewing a reconstruction batch")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services import reconstruction_batch_service as rbs

    mongo_db = _mongo(resolved_building_id)
    reviewed_by = str(current_user.get("id"))
    try:
        batch = await rbs.record_review(
            mongo_db, building_id=resolved_building_id, batch_id=batch_id,
            reviewed_by=reviewed_by, notes=request.notes,
        )
    except rbs.ReconstructionWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await create_audit_log(
        action="reconstruction_batch_reviewed", resource_type="demo_bank_reconstruction_batch",
        resource_id=batch_id, user_id=reviewed_by, user_name=current_user.get("full_name", "unknown"),
        details={"notes": request.notes, "manifest_hash": batch.manifest_hash}, building_id=resolved_building_id,
    )
    return batch.model_dump(mode="json")


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/reject")
async def reject_reconstruction_batch(
    building_id: str,
    batch_id: str,
    request: RejectReconstructionBatchRequest,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_reconstruction_posting")),
):
    """The negative outcome of the needs_review step — rejects a manifest a reviewer found to
    contain wrong/unreliable data (a bad source upload, an implausible reconciliation variance,
    etc.), returning the batch to `extracted` so a corrected manifest can be generated fresh.
    Unlike approve, reject has no dual-control precondition — any single reviewer can reject,
    since rejecting is the safe/conservative action, not the one that risks posting bad data.
    A reason is mandatory so the rejection is auditable."""
    _require_role(current_user, _RECONSTRUCTION_REVIEW_ROLES, "Rejecting a reconstruction batch")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    reason = request.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="Rejection reason cannot be blank.")

    from services import reconstruction_batch_service as rbs

    mongo_db = _mongo(resolved_building_id)
    rejected_by = str(current_user.get("id"))
    try:
        batch = await rbs.reject_batch(
            mongo_db, building_id=resolved_building_id, batch_id=batch_id,
            rejected_by=rejected_by, reason=reason,
        )
    except rbs.ReconstructionWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await create_audit_log(
        action="reconstruction_batch_rejected", resource_type="demo_bank_reconstruction_batch",
        resource_id=batch_id, user_id=rejected_by, user_name=current_user.get("full_name", "unknown"),
        details={"reason": request.reason}, building_id=resolved_building_id,
    )
    return batch.model_dump(mode="json")


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/approve")
async def approve_reconstruction_batch(
    building_id: str,
    batch_id: str,
    request: ApproveReconstructionBatchRequest,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_reconstruction_posting")),
):
    """Dual-control step 2 of 2: approves an already-reviewed manifest.
    Requires POST .../review to have run first, and requires the approver to
    be a DIFFERENT authenticated user than whoever recorded the review — a
    real two-person control enforced server-side, not merely a typed
    confirmation dialog in the UI. Approver identity always derives from the
    authenticated caller, never accepted from the client."""
    _require_role(current_user, _RECONSTRUCTION_APPROVE_ROLES, "Approving a reconstruction batch")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services import reconstruction_batch_service as rbs

    mongo_db = _mongo(resolved_building_id)
    approved_by = str(current_user.get("id"))

    batch_doc = await rbs._get_batch_doc(mongo_db, resolved_building_id, batch_id)
    reviewed_by = batch_doc.get("reviewed_by")
    if not reviewed_by:
        raise HTTPException(
            status_code=409,
            detail="Batch has not been reviewed yet — call POST .../review before approve.",
        )
    if reviewed_by == approved_by:
        raise HTTPException(
            status_code=403,
            detail="The approver must be a different authenticated user than the reviewer "
                   "(dual control) — this account already recorded the review for this batch.",
        )

    # Defensive re-check that the manifest hasn't changed since it was
    # reviewed. The status machine already guarantees this in practice
    # (needs_review's manifest is locked — see MANIFEST_LOCKED_STATUSES /
    # _VALID_TRANSITIONS; the only way out of needs_review other than
    # approve is back to extracted, and submit_for_review explicitly clears
    # reviewed_by/reviewed_at on every resubmission) — but this is a
    # financial approval gate, so verify explicitly rather than relying
    # solely on that invariant continuing to hold as the code evolves.
    manifest_doc = await mongo_db.demo_bank_reconstruction_manifests.find_one(
        {"building_id": resolved_building_id, "batch_id": batch_id},
        sort=[("version", -1)],
    )
    if manifest_doc is None or manifest_doc.get("manifest_hash") != batch_doc.get("manifest_hash"):
        raise HTTPException(
            status_code=409,
            detail="Manifest has changed since it was reviewed — re-run review before approving.",
        )

    try:
        batch = await rbs.approve_batch(mongo_db, building_id=resolved_building_id, batch_id=batch_id, approved_by=approved_by)
    except rbs.ReconstructionWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await create_audit_log(
        action="reconstruction_batch_approved", resource_type="demo_bank_reconstruction_batch",
        resource_id=batch_id, user_id=approved_by, user_name=current_user.get("full_name", "unknown"),
        details={"reviewed_by": reviewed_by, "manifest_hash": batch.manifest_hash}, building_id=resolved_building_id,
    )
    return batch.model_dump(mode="json")


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/generate")
async def generate_reconstruction_batch(
    building_id: str,
    batch_id: str,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_reconstruction_posting")),
):
    """Materialise the approved manifest's rows into demo_bank_transactions
    via integrations.demo_bank.ingestion.import_historical_reconstruction()
    (requires batch.status in {approved, generation_ready}). Does NOT sync to
    Postgres or post any ledger entry — that is the separate /sync step and
    the existing /bank-feeds/sync endpoint."""
    _require_role(current_user, _RECONSTRUCTION_SYNC_ROLES, "Generating a reconstruction batch")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services import reconstruction_batch_service as rbs
    from integrations.demo_bank.ingestion import import_historical_reconstruction

    mongo_db = _mongo(resolved_building_id)
    batch_doc = await rbs._get_batch_doc(mongo_db, resolved_building_id, batch_id)
    batch = rbs._doc_to_batch(batch_doc)
    manifest_doc = await mongo_db.demo_bank_reconstruction_manifests.find_one(
        {"building_id": resolved_building_id, "batch_id": batch_id}
    )
    if manifest_doc is None:
        raise HTTPException(status_code=404, detail=f"No manifest found for batch {batch_id!r}")
    from integrations.demo_bank.reconstruction_batch_schemas import ReconstructionManifest
    manifest = ReconstructionManifest(**{k: v for k, v in manifest_doc.items() if k != "_id"})

    if batch.reconstruction_method == GENERATION_METHOD:
        from services.reconstruction_generators.budget_levy_generator import ensure_generation_account
        await ensure_generation_account(resolved_building_id, is_test_data=batch.is_test_data)

    try:
        await rbs._transition(mongo_db, resolved_building_id, batch_id, to_status="generation_ready")
        # import_historical_reconstruction() dereferences `db._db.demo_bank_transactions`.
        # Pass the wrapped TenantScopedDatabase, not this router's raw AsyncDatabase helper.
        result = await import_historical_reconstruction(db, resolved_building_id, batch, manifest)

        # Label every materialised row as synthetic (never a real observed bank movement) —
        # if the labeling write doesn't reach every imported row, the batch stays at
        # generation_ready and cannot be synced by the state machine. Matching both metadata
        # fields keeps this verifier compatible with the ingestion contract: `source_batch_id`
        # is the Demo Bank import field, while `reconstruction_batch_id` is the sync/ledger
        # provenance field used downstream.
        imported_count = result.get("imported_count", 0)
        labeling_result = await db._db.demo_bank_transactions.update_many(
            {
                "building_id": resolved_building_id,
                "$or": [
                    {"source_batch_id": batch_id},
                    {"reconstruction_batch_id": batch_id},
                ],
            },
            {"$set": {
                "is_synthetic": True, "basis": "synthetic",
                "actual_payment_confirmed": False, "excluded_from_cash_reconciliation": True,
            }},
        )
        if imported_count and labeling_result.matched_count != imported_count:
            await mongo_db.demo_bank_reconstruction_batches.update_one(
                {"building_id": resolved_building_id, "batch_id": batch_id},
                {"$set": {
                    "labeling_verified": False,
                    "labeling_error": (
                        f"Expected to label {imported_count} materialised transaction(s) as "
                        f"synthetic but only matched {labeling_result.matched_count} — batch "
                        "left at generation_ready, not marked generated. Retry this endpoint; "
                        "the underlying materialisation is idempotent."
                    ),
                }},
            )
            raise HTTPException(status_code=409, detail={
                "code": "LABELING_INCOMPLETE",
                "message": (
                    f"Generated {imported_count} transaction(s) but only labeled "
                    f"{labeling_result.matched_count} of them as synthetic — refusing to mark "
                    "this batch generated until every row is verifiably labeled. Retry this "
                    "endpoint; materialisation itself is idempotent and safe to repeat."
                ),
            })

        await mongo_db.demo_bank_reconstruction_batches.update_one(
            {"building_id": resolved_building_id, "batch_id": batch_id},
            {"$set": {"labeling_verified": True}, "$unset": {"labeling_error": ""}},
        )
        updated = await rbs.mark_generated(mongo_db, building_id=resolved_building_id, batch_id=batch_id)
    except (rbs.ReconstructionWorkflowError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await create_audit_log(
        action="reconstruction_batch_generated", resource_type="demo_bank_reconstruction_batch",
        resource_id=batch_id, user_id=str(current_user.get("id")), user_name=current_user.get("full_name", "unknown"),
        details=result, building_id=resolved_building_id,
    )
    return {"batch": updated.model_dump(mode="json"), "materialisation_result": result}


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/sync")
async def sync_reconstruction_batch(
    building_id: str,
    batch_id: str,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_reconstruction_posting")),
):
    """Orchestrates a real sync: transitions to syncing, calls the canonical
    bank-feed sync pipeline (bank_feeds.run_bank_feed_sync) scoped to this
    batch's rows via reconstruction_batch_id, verifies inserted/duplicate/
    failed counts, and only marks synced on a clean, non-empty result.
    Zero matched rows or any per-row failure moves the batch to failed
    instead of a false "synced" — never claims a sync happened when it
    did not (see docs/migration/build-demo-data-ui-system.md finding 1.2)."""
    _require_role(current_user, _RECONSTRUCTION_SYNC_ROLES, "Syncing a reconstruction batch")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    for required_toggle in ("financial_integration_layer_v2", "bank_feeds_sync_enabled"):
        if not await get_effective_feature_access(current_user, required_toggle):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "FEATURE_DISABLED",
                    "message": f"Reconstruction sync requires '{required_toggle}' to be enabled for this building.",
                    "feature_key": required_toggle,
                },
            )

    from services import reconstruction_batch_service as rbs
    from routers.bank_feeds import BankFeedSyncRequest, run_bank_feed_sync

    mongo_db = _mongo(resolved_building_id)
    batch_doc = await rbs._get_batch_doc(mongo_db, resolved_building_id, batch_id)
    batch = rbs._doc_to_batch(batch_doc)

    try:
        await rbs.mark_syncing(mongo_db, building_id=resolved_building_id, batch_id=batch_id)
    except rbs.ReconstructionWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    sync_payload = BankFeedSyncRequest(
        include_test_data=batch.is_test_data,
        reconstruction_batch_id=batch_id,
        disable_auto_allocation=True,
    )
    # run_bank_feed_sync() and everything it calls (_account_refs_for_sync,
    # _mark_demo_bank_sync, ...) do `db._db.<collection>` themselves — they need
    # the WRAPPED TenantScopedDatabase, not `mongo_db` (already db._db here).
    # Passing mongo_db double-unwraps and raises AttributeError.
    sync_result = await run_bank_feed_sync(
        db, building_id=resolved_building_id, payload=sync_payload, actor=current_user,
    )

    if sync_result["processed"] == 0:
        batch = await rbs._transition(mongo_db, resolved_building_id, batch_id, to_status="failed")
        await create_audit_log(
            action="reconstruction_batch_sync_failed", resource_type="demo_bank_reconstruction_batch",
            resource_id=batch_id, user_id=str(current_user.get("id")), user_name=current_user.get("full_name", "unknown"),
            details={"reason": "no_transactions_matched_batch", "sync_result": sync_result},
            building_id=resolved_building_id,
        )
        raise HTTPException(
            status_code=409,
            detail=f"No Demo Bank transactions tagged with reconstruction_batch_id={batch_id!r} were found; "
                   "run Generate before Sync.",
        )

    if sync_result["failed"] > 0:
        batch = await rbs._transition(mongo_db, resolved_building_id, batch_id, to_status="failed")
        await create_audit_log(
            action="reconstruction_batch_sync_failed", resource_type="demo_bank_reconstruction_batch",
            resource_id=batch_id, user_id=str(current_user.get("id")), user_name=current_user.get("full_name", "unknown"),
            details={"sync_result": sync_result}, building_id=resolved_building_id,
        )
        return {"batch": batch.model_dump(mode="json"), "sync_result": sync_result}

    batch = await rbs.mark_synced(mongo_db, building_id=resolved_building_id, batch_id=batch_id)
    await create_audit_log(
        action="reconstruction_batch_sync_completed", resource_type="demo_bank_reconstruction_batch",
        resource_id=batch_id, user_id=str(current_user.get("id")), user_name=current_user.get("full_name", "unknown"),
        details={"sync_result": sync_result}, building_id=resolved_building_id,
    )
    return {"batch": batch.model_dump(mode="json"), "sync_result": sync_result}


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/post-expenses")
async def post_reconstruction_batch_expenses(
    building_id: str,
    batch_id: str,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_reconstruction_posting")),
):
    """In-app, dual-control posting of an approved+synced batch's category-EXPENSE (debit) lines
    into the Postgres GL — the expense-side analog of the income match-queue -> _post_payment_to_ledger()
    path, replacing the out-of-band `historical_expense_posting.py --apply` shell script. Shares the
    exact same validated posting core (`expense_posting_service`) as that script, so the two can
    never diverge (the GAP-FIN-035 Item 4 "two disconnected expense totals" incident). See
    tasks/GAP-FIN-038-expense-pipeline-unification.md.

    Governance: super_admin only; requires `historical_reconstruction_posting` +
    `financial_integration_layer_v2`; the caller (executed_by) MUST differ from the batch's
    approved_by (dual control); the batch must already be synced to Demo Bank. Posting is
    idempotent (record_historical_expense's idempotency key) so it is safe to retry and safe for
    the script and this endpoint to coexist. Never manufactures an approval or progresses a batch
    past what a human has reviewed/approved/synced."""
    _require_role(current_user, _RECONSTRUCTION_APPLY_ROLES, "Posting reconstruction batch expenses")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    if not await get_effective_feature_access(current_user, "financial_integration_layer_v2"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Posting reconstruction expenses requires 'financial_integration_layer_v2'.",
                "feature_key": "financial_integration_layer_v2",
            },
        )

    from services import reconstruction_batch_service as rbs

    mongo_db = _mongo(resolved_building_id)
    try:
        batch_doc = await rbs._get_batch_doc(mongo_db, resolved_building_id, batch_id)
    except rbs.ReconstructionWorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    status = batch_doc.get("status")
    if status not in {"synced", "matching", "partially_posted"}:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Posting expenses requires the batch to be synced to Demo Bank first (status must "
                f"be one of synced/matching/partially_posted); current status={status!r}. Run Sync "
                "before posting expenses."
            ),
        )
    if int(batch_doc.get("generated_debit_count") or 0) == 0:
        raise HTTPException(
            status_code=409,
            detail="This batch's manifest has no expense (debit) lines to post.",
        )

    try:
        executed_by = UUID(str(current_user.get("id")))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Current user id is not a valid UUID; cannot establish dual control.",
        ) from exc

    # Dual-control pre-check BEFORE any state change, so a same-approver attempt can't leave the
    # batch stuck in 'matching'.
    try:
        validate_batch_authorisation(batch_doc=batch_doc, executed_by=executed_by)
    except ExpensePostingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if status == "synced":
        try:
            await rbs._transition(mongo_db, resolved_building_id, batch_id, to_status="matching")
        except rbs.ReconstructionWorkflowError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        result = await run_expense_posting(
            mongo_db=mongo_db,
            building_id=resolved_building_id,
            batch_id=batch_id,
            executed_by=executed_by,
            is_test_data=bool(batch_doc.get("is_test_data")),
        )
    except ExpensePostingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Reconciliation evidence + forward state. A batch with income (credit) rows still needs the
    # match queue to finish, so it lands 'partially_posted'; an expense-only batch is 'posted'.
    has_income = int(batch_doc.get("generated_credit_count") or 0) > 0
    target = "partially_posted" if has_income else "posted"
    reconciliation_extra = {
        "expenses_posted_at": rbs._now(),
        "expenses_posted_by": str(executed_by),
        "expenses_posted_total_cents": result.posted_total_cents,
        "expenses_posted_line_count": result.line_count,
        "expenses_per_fund_cents": result.per_fund_cents,
    }
    current_now = (await rbs._get_batch_doc(mongo_db, resolved_building_id, batch_id)).get("status")
    if current_now != target and target in rbs._VALID_TRANSITIONS.get(current_now, set()):
        batch = await rbs._transition(
            mongo_db, resolved_building_id, batch_id, to_status=target, extra=reconciliation_extra,
        )
    else:
        # Idempotent re-run (already at target) — persist fresh reconciliation evidence, no transition.
        await mongo_db.demo_bank_reconstruction_batches.update_one(
            {"building_id": resolved_building_id, "batch_id": batch_id},
            {"$set": reconciliation_extra},
        )
        batch = rbs._doc_to_batch(await rbs._get_batch_doc(mongo_db, resolved_building_id, batch_id))

    await create_audit_log(
        action="reconstruction_batch_expenses_posted",
        resource_type="demo_bank_reconstruction_batch",
        resource_id=batch_id,
        user_id=str(current_user.get("id")),
        user_name=current_user.get("full_name", "unknown"),
        details={"result": result.as_dict()},
        building_id=resolved_building_id,
    )
    return {"batch": batch.model_dump(mode="json"), "result": result.as_dict()}


@router.post("/financials/onboarding/building/{building_id}/reconstruction-batches/{batch_id}/reverse")
async def reverse_reconstruction_batch(
    building_id: str,
    batch_id: str,
    request: ReverseReconstructionBatchRequest,
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_reconstruction_posting")),
):
    """Dual-control finance authority only. Marks the batch reversed; does not
    itself post reversing journal entries — pair with
    FinancialCoreService.reverse_and_replace_posted_levy_journals for the
    ledger-side reversal when the batch has already been posted."""
    _require_role(current_user, _RECONSTRUCTION_REVERSE_ROLES, "Reversing a reconstruction batch")
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    from services import reconstruction_batch_service as rbs

    try:
        batch = await rbs.reverse(_mongo(resolved_building_id), building_id=resolved_building_id, batch_id=batch_id, reason=request.reason)
    except rbs.ReconstructionWorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await create_audit_log(
        action="reconstruction_batch_reversed", resource_type="demo_bank_reconstruction_batch",
        resource_id=batch_id, user_id=str(current_user.get("id")), user_name=current_user.get("full_name", "unknown"),
        details={"reason": request.reason}, building_id=resolved_building_id,
    )
    return batch.model_dump(mode="json")


# ════════════════════════════════════════════════════════════════════════════
# GST-aware historical levy-item regeneration
# ════════════════════════════════════════════════════════════════════════════

async def _build_levy_plan(*, source: str, building_id: str, session, scheme_ref, from_year, to_year):
    """Build a levy-regeneration plan from either the live or the staging source.

    Both branches return the same ``LevyReconciliationReport`` and run the same
    GST-aware UOE apportionment core — only the fund-total input differs.
    """
    from services.levy_generation_service import (
        build_levy_regeneration_plan,
        build_phase_f_prime_levy_plan,
    )

    if source == "onboarding_staging":
        try:
            return await build_phase_f_prime_levy_plan(
                building_id=building_id, mongo_db=db._db, pg_session=session, scheme_ref=scheme_ref,
            )
        except RuntimeError as exc:
            # No staging rows is an operator-actionable state, not a 500.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, KeyError) as exc:
            # Staging rows are operator-supplied CSV content, so a malformed `year`
            # or a missing column is bad INPUT, not a server fault. Surfacing it as
            # 422 tells them which file to correct instead of showing a 500.
            raise HTTPException(
                status_code=422,
                detail=f"Onboarding staging data could not be read: {exc}",
            ) from exc
    return await build_levy_regeneration_plan(
        building_id=building_id, mongo_db=db._db, pg_session=session,
        scheme_ref=scheme_ref, from_year=from_year, to_year=to_year,
    )


def _require_year_window(source: str, from_year: int | None, to_year: int | None) -> None:
    """``live`` needs an explicit year window; ``onboarding_staging`` derives its own."""
    if source == "live" and (from_year is None or to_year is None):
        raise HTTPException(
            status_code=422,
            detail="from_year and to_year are required when source=live.",
        )


@router.post("/financials/onboarding/building/{building_id}/levy-items/regenerate-plan")
async def plan_levy_items_regeneration(
    building_id: str,
    from_year: int | None = Query(None),
    to_year: int | None = Query(None),
    source: str = Query(
        "live",
        pattern="^(live|onboarding_staging)$",
        description=(
            "'live' reads annual_levies (a building already operating in the app); "
            "'onboarding_staging' reads the historical_annual_levies rows written by "
            "POST /onboarding/scheme/{session_id}/import-historical-financials."
        ),
    ),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_financial_reconstruction")),
):
    """Always dry-run — writes nothing. Returns the full GST-aware UOE
    reconciliation report (see services/levy_generation_service.py).

    ``source=onboarding_staging`` is the bridge out of onboarding: the wizard's
    historical-financials import deliberately writes to isolated ``historical_*``
    staging collections (ADR-022) so it cannot disturb a live building's records,
    and this is what reads them back out. Without it, a newly onboarded building's
    uploaded history had no route into the ledger at all — the staging collections
    had no reader anywhere in the codebase.
    """
    _require_role(current_user, _RECONSTRUCTION_UPLOAD_ROLES, "Planning a levy-item regeneration")
    _require_year_window(source, from_year, to_year)
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)
    scheme = await _resolve_building_context(resolved_building_id)
    scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))

    from dataclasses import asdict

    async with async_session_context() as session:
        await set_tenant(session, scheme_ref.tenant_id)
        plan = await _build_levy_plan(
            source=source, building_id=resolved_building_id, session=session,
            scheme_ref=scheme_ref, from_year=from_year, to_year=to_year,
        )
    return {**asdict(plan), "source": source}


@router.post("/financials/onboarding/building/{building_id}/levy-items/regenerate-apply")
async def apply_levy_items_regeneration(
    building_id: str,
    request: RegenerateLevyApplyRequest,
    from_year: int | None = Query(None),
    to_year: int | None = Query(None),
    source: str = Query(
        "live",
        pattern="^(live|onboarding_staging)$",
        description="Must match the source the operator reviewed via regenerate-plan.",
    ),
    batch_id: str = Query(..., description="Governing demo_bank_reconstruction_batch — must be status=approved"),
    current_user: dict = Depends(get_current_user),
    current_building: str = Depends(get_current_building),
    _feature: dict = Depends(require_feature("historical_reconstruction_posting")),
):
    """Re-fetches a fresh plan server-side (never trusts a client-cached one),
    refuses unless the governing reconstruction batch has status=approved and
    request.confirm=True, then applies via
    FinancialCoreService.bulk_upsert_historical_levy_items()."""
    _require_role(current_user, _RECONSTRUCTION_APPLY_ROLES, "Applying a levy-item regeneration")
    _require_year_window(source, from_year, to_year)
    resolved_building_id = await _assert_building_access(building_id, current_building, current_user)

    # Defense-in-depth beyond historical_reconstruction_posting, matching
    # sync_reconstruction_batch's own extra check — this is the one endpoint in this
    # router that writes directly to finance.levy_items (Postgres), so it must not be
    # reachable purely because historical_reconstruction_posting is globally enabled.
    if not await get_effective_feature_access(current_user, "financial_integration_layer_v2"):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "FEATURE_DISABLED",
                "message": "Levy-item regeneration apply requires 'financial_integration_layer_v2' to be enabled for this building.",
                "feature_key": "financial_integration_layer_v2",
            },
        )

    from services import reconstruction_batch_service as rbs
    batch_doc = await rbs._get_batch_doc(_mongo(resolved_building_id), resolved_building_id, batch_id)
    batch = rbs._doc_to_batch(batch_doc)
    if batch.status != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"Governing batch {batch_id!r} has status={batch.status!r}; regenerate-apply requires 'approved'.",
        )
    if not request.confirm:
        raise HTTPException(status_code=400, detail="regenerate-apply requires confirm=True")
    if request.override_reconciliation_mismatch and not request.override_reason:
        raise HTTPException(
            status_code=400,
            detail="override_reconciliation_mismatch=True requires a recorded override_reason "
                   "(min 10 characters) justifying the mismatch override.",
        )

    scheme = await _resolve_building_context(resolved_building_id)
    scheme_ref = SchemeRef(tenant_id=UUID(str(scheme["tenant_id"])), scheme_id=UUID(str(scheme["scheme_id"])))

    from services.financial_core.adapters.db_postgres.ledger_repo import PostgresLedgerRepository
    from services.financial_core.adapters.db_postgres.outbox_repo import PostgresOutboxRepository
    from services.levy_generation_service import apply_levy_regeneration

    async with async_session_context() as session:
        await set_tenant(session, scheme_ref.tenant_id)
        plan = await _build_levy_plan(
            source=source, building_id=resolved_building_id, session=session,
            scheme_ref=scheme_ref, from_year=from_year, to_year=to_year,
        )
        if not plan.totals_reconcile and not request.override_reconciliation_mismatch:
            raise HTTPException(
                status_code=422,
                detail="Freshly recomputed plan does not reconcile — pass override_reconciliation_mismatch=True "
                       "with a recorded justification to proceed anyway.",
            )
        ledger_repo = PostgresLedgerRepository(session)
        outbox_repo = PostgresOutboxRepository(session)
        result = await apply_levy_regeneration(
            scheme_ref=scheme_ref, pg_session=session, ledger_repo=ledger_repo, outbox_repo=outbox_repo,
            plan=plan, confirm=True, override_reconciliation_mismatch=request.override_reconciliation_mismatch,
        )

    await create_audit_log(
        action="levy_items_gst_regenerated", resource_type="finance.levy_items",
        resource_id=resolved_building_id, user_id=str(current_user.get("id")), user_name=current_user.get("full_name", "unknown"),
        details={
            "batch_id": batch_id, "source": source, "applied_item_count": result.applied_item_count,
            "total_principal_cents": result.total_principal_cents, "total_gst_cents": result.total_gst_cents,
            "manual_review_count": result.manual_review_count,
            "override_reconciliation_mismatch": request.override_reconciliation_mismatch,
            "override_reason": request.override_reason,
        },
        building_id=resolved_building_id,
    )
    return {
        "applied_item_count": result.applied_item_count,
        "total_principal_cents": result.total_principal_cents,
        "total_gst_cents": result.total_gst_cents,
        "manual_review_count": result.manual_review_count,
        "batch_id": batch_id,
        "override_reconciliation_mismatch": request.override_reconciliation_mismatch,
        "override_reason": request.override_reason,
    }
