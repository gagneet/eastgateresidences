# @featuretrace:finance-postgres-write-cutover — Route-level finance write-source resolution and readiness inventory.
# Layer: service
# Data flow: finance router / cutover admin → this service → cutover_status_service + feature toggles + shadow readiness
#            → route-level write-source selection (building-scoped).
# Related: backend/routers/finance.py
#          backend/routers/cutover_admin.py
#          backend/services/financial_core/service.py
#          docs/architecture/featuretrace/finance-postgres-write-cutover.md
# Toggle: financial_pg_writes_enabled
# @featuretrace:finance-postgres-write-lifecycle — Route policy inventory for Prompt 8 lifecycle routes (verify/reject/delete/adjustment-journals).
# Layer: service
# Data flow: lifecycle routes → get_finance_write_route_runtime_state() → policy lookup → postgres or mongo path (building-scoped).
# Related: backend/routers/finance.py
#          backend/services/finance_postgres_write_service.py
#          tests/backend/test_finance_payment_lifecycle.py
# Toggle: financial_pg_writes_enabled
# Table: finance.receipts, finance.journal_entries, finance.journal_lines
# Tests: tests/backend/test_finance_payment_lifecycle.py
"""Route-level finance write cutover controls.

Prompt 7 constraints:
  - Write promotion is building-scoped and route-scoped.
  - Domain postgres_write alone is not enough; each route must be explicitly eligible.
  - Promoted routes fail closed. They must not silently fall back to MongoDB.
  - Unpromoted routes remain Mongo-primary and keep their existing API contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models.cutover_status import CutoverMode
from services.cutover_config_service import (
    FINANCIAL_PG_READS_ENABLED,
    FINANCIAL_PG_WRITES_ENABLED,
    is_cutover_feature_enabled,
)
from services.cutover_status_service import (
    get_cutover_status,
    get_or_default_cutover_status,
    is_identity_foundation_ready,
)
from services.domain_source_guard import DomainSourceAuditContext, require_domain_source
from services.finance_shadow_read_service import get_route_shadow_readiness


@dataclass(frozen=True)
class FinanceWriteRoutePolicy:
    route_key: str
    method: str
    path: str
    handler: str
    frontend_consumer: str
    current_write_source: str
    target_write_source: str
    mongo_collections_written: tuple[str, ...]
    postgres_tables_written: tuple[str, ...]
    cutover_mode_required: str
    idempotency_required: bool
    audit_required: bool
    reversal_supported: bool
    tests: tuple[str, ...]
    featuretrace_marker: str
    production_readiness_status: str
    postgres_write_supported: bool
    notes: str


_WRITE_POLICIES: tuple[FinanceWriteRoutePolicy, ...] = (
    FinanceWriteRoutePolicy(
        route_key="finance.levy_payment_create",
        method="POST",
        path="/levy-payments",
        handler="record_levy_payment",
        frontend_consumer="Finance payment recording forms; owner self-report payment form",
        current_write_source="MongoDB levy_payments + unit_levy_ledger",
        target_write_source="PostgreSQL finance.receipts + finance.journal_entries + finance.receipt_allocations",
        mongo_collections_written=("levy_payments", "unit_levy_ledger"),
        postgres_tables_written=(
            "finance.journal_entries",
            "finance.journal_lines",
            "finance.receipts",
            "finance.receipt_allocations",
            "finance.levy_items",
            "core.outbox",
        ),
        cutover_mode_required="postgres_write",
        idempotency_required=True,
        audit_required=True,
        reversal_supported=True,
        tests=(
            "tests/backend/test_finance_write_cutover_service.py",
            "tests/backend/test_finance_postgres_payment_write.py",
        ),
        featuretrace_marker="@featuretrace:finance-postgres-write-cutover",
        production_readiness_status="eligible for postgres_write now",
        postgres_write_supported=True,
        notes=(
            "Smallest Prompt 7 slice: manager-approved manual receipt posting with "
            "FIFO allocation to open levy items. Owner self-report remains Mongo-primary "
            "until an approval workflow is implemented."
        ),
    ),
    FinanceWriteRoutePolicy(
        route_key="finance.levy_payment_verify",
        method="PATCH",
        path="/levy-payments/{payment_id}/verify",
        handler="verify_levy_payment",
        frontend_consumer="Finance payment verification tools",
        current_write_source="MongoDB levy_payments + unit_levy_ledger",
        target_write_source="PostgreSQL finance.journal_entries status transition (idempotent confirm)",
        mongo_collections_written=("levy_payments", "unit_levy_ledger"),
        postgres_tables_written=("finance.receipts", "finance.journal_entries", "core.outbox"),
        cutover_mode_required="postgres_write",
        idempotency_required=False,
        audit_required=True,
        reversal_supported=True,
        tests=(
            "tests/backend/test_finance_payment_lifecycle.py",
            "tests/backend/test_levy_payment_workflow.py",
        ),
        featuretrace_marker="@featuretrace:finance-postgres-write-lifecycle",
        production_readiness_status="implemented — Prompt 8",
        postgres_write_supported=True,
        notes=(
            "Prompt 8: PG receipt is always created confirmed (manager-approved at POST); "
            "verify is an idempotent audit no-op for already-confirmed receipts. "
            "For pending_review receipts transitions journal_entries.status to posted."
        ),
    ),
    FinanceWriteRoutePolicy(
        route_key="finance.levy_payment_reject",
        method="PATCH",
        path="/levy-payments/{payment_id}/reject",
        handler="reject_levy_payment",
        frontend_consumer="Finance payment verification tools",
        current_write_source="MongoDB levy_payments",
        target_write_source="PostgreSQL receipt reversal workflow (original immutable; new reversal entry)",
        mongo_collections_written=("levy_payments",),
        postgres_tables_written=("finance.journal_entries", "finance.journal_lines", "core.outbox"),
        cutover_mode_required="postgres_write",
        idempotency_required=False,
        audit_required=True,
        reversal_supported=True,
        tests=(
            "tests/backend/test_finance_payment_lifecycle.py",
            "tests/backend/test_levy_payment_workflow.py",
        ),
        featuretrace_marker="@featuretrace:finance-postgres-write-lifecycle",
        production_readiness_status="implemented — Prompt 8",
        postgres_write_supported=True,
        notes=(
            "Prompt 8: posted receipts get a reversal journal entry; original entry is NEVER mutated. "
            "Original journal status transitions to reversed. Duplicate rejection returns 409."
        ),
    ),
    FinanceWriteRoutePolicy(
        route_key="finance.levy_payment_delete",
        method="DELETE",
        path="/levy-payments/{payment_id}",
        handler="delete_levy_payment",
        frontend_consumer="Finance payment maintenance tools",
        current_write_source="MongoDB levy_payments delete",
        target_write_source="PostgreSQL safe cancel (posted → 409; unposted → voided status-only)",
        mongo_collections_written=("levy_payments",),
        postgres_tables_written=("finance.journal_entries",),
        cutover_mode_required="postgres_write",
        idempotency_required=False,
        audit_required=True,
        reversal_supported=True,
        tests=("tests/backend/test_finance_payment_lifecycle.py",),
        featuretrace_marker="@featuretrace:finance-postgres-write-lifecycle",
        production_readiness_status="implemented — Prompt 8",
        postgres_write_supported=True,
        notes=(
            "Prompt 8: posted records return 409 POSTED_RECORD_IMMUTABLE with suggested_action pointing "
            "to reject. Unposted drafts transition to voided (never physically deleted). "
            "Falls back to Mongo delete in mongo_primary mode."
        ),
    ),
    FinanceWriteRoutePolicy(
        route_key="finance.adjustment_journal",
        method="POST",
        path="/finance/adjustment-journals",
        handler="create_adjustment_journal",
        frontend_consumer="Internal finance operators",
        current_write_source="Not exposed as a dedicated route",
        target_write_source="PostgreSQL finance.journal_entries + finance.journal_lines",
        mongo_collections_written=(),
        postgres_tables_written=("finance.journal_entries", "finance.journal_lines", "core.outbox"),
        cutover_mode_required="postgres_write",
        idempotency_required=True,
        audit_required=True,
        reversal_supported=True,
        tests=(
            "tests/backend/test_finance_postgres_payment_write.py",
            "tests/backend/test_finance_payment_lifecycle.py",
        ),
        featuretrace_marker="@featuretrace:finance-postgres-write-lifecycle",
        production_readiness_status="implemented — Prompt 8",
        postgres_write_supported=True,
        notes=(
            "Prompt 8: POST /finance/adjustment-journals implemented with balance validation, "
            "integer-cents enforcement, idempotency key, GL account validation, and building scope. "
            "Requires can_manage_finances + postgres_write mode."
        ),
    ),
    FinanceWriteRoutePolicy(
        route_key="finance.bank_reconciliation_create",
        method="POST",
        path="/bank-reconciliations",
        handler="create_bank_reconciliation",
        frontend_consumer="Bank reconciliation tools",
        current_write_source="MongoDB bank_reconciliations",
        target_write_source="PostgreSQL finance.reconciliation_runs",
        mongo_collections_written=("bank_reconciliations",),
        postgres_tables_written=("finance.reconciliation_runs", "finance.bank_transactions"),
        cutover_mode_required="mongo_primary",
        idempotency_required=True,
        audit_required=True,
        reversal_supported=False,
        tests=("tests/backend/test_reconciliation.py",),
        featuretrace_marker="@featuretrace:finance-postgres-write-cutover",
        production_readiness_status="unsafe until later",
        postgres_write_supported=False,
        notes="High-risk automated reconciliation flow deferred.",
    ),
    FinanceWriteRoutePolicy(
        route_key="finance.payment_plan_create",
        method="POST",
        path="/payment-plans",
        handler="create_payment_plan",
        frontend_consumer="Payment plan forms",
        current_write_source="MongoDB payment_plans",
        target_write_source="PostgreSQL finance.payment_plans",
        mongo_collections_written=("payment_plans",),
        postgres_tables_written=("finance.payment_plans", "finance.payment_plan_installments"),
        cutover_mode_required="mongo_primary",
        idempotency_required=True,
        audit_required=True,
        reversal_supported=False,
        tests=("tests/backend/test_payment_plans.py",),
        featuretrace_marker="@featuretrace:finance-postgres-write-cutover",
        production_readiness_status="eligible after more tests",
        postgres_write_supported=False,
        notes="Deferred because it changes arrears workflow state rather than posting a simple ledger receipt.",
    ),
)

_POLICY_BY_KEY = {p.route_key: p for p in _WRITE_POLICIES}


def list_finance_write_route_policies() -> list[FinanceWriteRoutePolicy]:
    """Return the static finance write route inventory."""
    return list(_WRITE_POLICIES)


def get_finance_write_route_policy(route_key: str) -> FinanceWriteRoutePolicy | None:
    """Generated function header.

    Function: get_finance_write_route_policy
    Path: backend/services/finance_write_cutover_service.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return _POLICY_BY_KEY.get(route_key)


async def get_finance_write_route_runtime_state(
    *,
    building_id: str,
    route_key: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Resolve the runtime write source for one finance write route."""
    policy = get_finance_write_route_policy(route_key)
    if policy is None:
        return {
            "route_key": route_key,
            "source": "mongo",
            "eligible_for_postgres_write": False,
            "blocked_reason": "Unknown write route policy",
            "domain_mode": "mongo_primary",
            "policy": None,
        }

    status = await get_or_default_cutover_status(building_id, "finance_ledger")
    audit_context = DomainSourceAuditContext(
        route=policy.path,
        source_service="finance_write_cutover_service",
        feature_toggle_key=FINANCIAL_PG_WRITES_ENABLED,
        metadata={"route_key": route_key},
    )
    domain_decision = await require_domain_source(
        domain="finance_ledger",
        building_id=building_id,
        operation="write",
        requested_source="postgres",
        audit_context=audit_context,
    )
    domain_mode = status.mode
    pg_writes_enabled = await is_cutover_feature_enabled(building_id, FINANCIAL_PG_WRITES_ENABLED)
    pg_reads_enabled = await is_cutover_feature_enabled(building_id, FINANCIAL_PG_READS_ENABLED)
    read_ready = await get_route_shadow_readiness(
        building_id=building_id,
        route_key="finance.unit_dashboard_overview",
    )

    identity_status = await get_cutover_status(building_id, "identity_core")
    # is_identity_foundation_ready accepts identity_core's readiness under either snapshot
    # key (identity_foundation / identity_pg_write_preflight) or a promoted domain state —
    # a key-naming mismatch (identity_core finalized under the preflight key) previously
    # blocked ALL finance PG writes even though identity_core was genuinely live. See
    # cutover_status_service.is_identity_foundation_ready() and GAP-CUTOVER-001.
    identity_ready = is_identity_foundation_ready(identity_status)
    financial_snapshot = (status.p0_snapshot or {}).get("financial_onboarding", {})

    blocked_reason: str | None = None
    source = "mongo"
    eligible = False

    if domain_decision.blocked_reason:
        blocked_reason = domain_decision.blocked_reason
    elif not policy.postgres_write_supported:
        blocked_reason = policy.notes
    elif domain_mode != CutoverMode.postgres_write:
        blocked_reason = f"Domain mode is {domain_mode.value}; route remains Mongo-primary"
    elif not pg_writes_enabled:
        blocked_reason = f"Feature toggle {FINANCIAL_PG_WRITES_ENABLED} is disabled"
    elif not pg_reads_enabled:
        blocked_reason = f"Feature toggle {FINANCIAL_PG_READS_ENABLED} is disabled"
    elif not domain_decision.postgres_allowed:
        blocked_reason = domain_decision.blocked_reason or "Domain source state does not allow postgres write"
    elif financial_snapshot.get("status") != "pass":
        blocked_reason = "Financial genesis/readiness snapshot is not pass"
    elif not identity_ready:
        blocked_reason = "Identity foundation readiness snapshot is not pass"
    elif read_ready.get("status") != "shadow_pass":
        blocked_reason = (
            "Read compatibility is not proven; "
            f"finance.unit_dashboard_overview readiness is {read_ready.get('status', 'not_started')}"
        )
    elif int(read_ready.get("critical_count") or 0) > 0:
        blocked_reason = "Read compatibility has critical shadow diffs"
    elif policy.idempotency_required and not idempotency_key:
        blocked_reason = "Idempotency-Key header is required for this write route"
    else:
        source = "postgres"
        eligible = True

    return {
        "route_key": route_key,
        "source": source,
        "eligible_for_postgres_write": eligible,
        "blocked_reason": blocked_reason,
        "domain_mode": domain_mode.value,
        "read_compatibility": read_ready,
        "policy": policy.__dict__,
    }


async def get_finance_write_readiness_table(*, building_id: str) -> list[dict[str, Any]]:
    """Return route-level write readiness rows for the cutover admin UI."""
    rows: list[dict[str, Any]] = []
    for policy in _WRITE_POLICIES:
        state = await get_finance_write_route_runtime_state(
            building_id=building_id,
            route_key=policy.route_key,
            idempotency_key="readiness-probe" if policy.idempotency_required else None,
        )
        rows.append(
            {
                **policy.__dict__,
                "current_source": state["source"],
                "domain_mode": state["domain_mode"],
                "eligible_for_postgres_write": state["eligible_for_postgres_write"],
                "blocked_reason": state["blocked_reason"],
            }
        )
    return rows
