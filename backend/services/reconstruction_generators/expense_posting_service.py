# @featuretrace:financial-onboarding — shared, single-sourced posting core for the historical
#   category EXPENSE (debit) lines carried inside an APPROVED reconstruction batch's manifest.
#   Consumed by BOTH scripts/historical_expense_posting.py (CLI) and the in-app, dual-control
#   endpoint routers/financial_onboarding.py::post_reconstruction_batch_expenses — so category
#   expense actuals reach the GL through ONE implementation, not two divergent pipelines
#   (the GAP-FIN-035 Item 4 "two disconnected expense totals" incident).
# @featuretrace:demo_bank — posts the debit rows an approved reconstruction batch staged into
#   Demo Bank, mirroring the income side's match-queue -> _post_payment_to_ledger() path.
# Layer: service
# Data flow: demo_bank_reconstruction_manifests (Mongo, approved batch's debit rows) ->
#            FinancialCoreService.record_historical_expense() (Postgres GL). (building-scoped)
# Related: backend/scripts/historical_expense_posting.py (thin CLI wrapper over this module)
#          backend/scripts/historical_levy_charge_posting.py (the levy-charge sibling pattern)
#          backend/services/reconstruction_generators/expense_category_generator.py (row producer)
#          tasks/GAP-FIN-038-expense-pipeline-unification.md
# Toggle: historical_reconstruction_posting
# Collection: demo_bank_reconstruction_batches, demo_bank_reconstruction_manifests
"""Shared posting core for an approved reconstruction batch's category-expense (debit) lines.

All the financial-risk logic lives here exactly once: dual-control authorisation, manifest-hash
drift detection, manifest self-consistency, debit-only filtering, GST-split amount handling,
per-fund reconciliation totals, and the post-apply integrity check. Both the CLI script and the
in-app endpoint call into this module so the two can never drift apart.

Amounts are sourced from the batch's own MONGO MANIFEST (not from `finance.bank_transactions`
post-sync) because the manifest carries the correctly GST-split
`amount_ex_gst_cents`/`gst_cents`, whereas `finance.bank_transactions` only stores one flattened
`amount_cents`. This is the same reasoning `historical_levy_charge_posting.py` applies.

This module NEVER progresses a batch's status and NEVER manufactures an approval — it only posts
what a human has already reviewed and approved, and cross-checks that posted totals reproduce the
approved manifest's debit total exactly. Status gating and state transitions are the caller's
responsibility (the CLI requires `approved`; the endpoint requires the batch to be synced first).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional, Protocol
from uuid import UUID

from services.financial_core.domain.entities import (
    HistoricalPostingAuthorisation,
    RecordHistoricalExpenseCommand,
    SchemeRef,
)


class ExpensePostingError(ValueError):
    """Raised on a governance/integrity precondition failure (dual-control, manifest drift,
    self-inconsistent manifest, post-apply total mismatch). A catchable exception rather than
    SystemExit so both the CLI (which converts it) and the HTTP endpoint (which maps it to 409)
    can consume this module. Message substrings are load-bearing — the CLI's regression tests
    match on 'no approved_by', 'dual control', 'Manifest has changed', 'integrity check failed'."""


# Matches expense_category_generator.py's own description format(s) exactly -- both files are
# maintained together; a change to one's format requires updating the other's parser/generator.
# Two formats now exist: the coarse per-category-per-year generator
# (generate_expense_manifest_from_categories, "Estimated {year} spend summary: {name} ({Fund}
# Fund) -- ...") and the itemized per-scraped-invoice-line generator
# (generate_itemized_expense_manifest_from_scraper, "{name} ({Fund} Fund) -- Supplier: ... --
# Invoice/Ref: ..."). Tried in order -- the coarse pattern first (exact prior behaviour,
# unchanged), then the leading-name pattern for itemized rows.
_CATEGORY_NAME_RE = re.compile(r"spend summary: (.+?) \((?:Admin|Sinking) Fund\)")
_CATEGORY_NAME_LEADING_RE = re.compile(r"^(.+?) \((?:Admin|Sinking) Fund\)")


def _extract_category_name(description: str) -> str:
    text = description or ""
    match = _CATEGORY_NAME_RE.search(text)
    if match:
        return match.group(1)
    match = _CATEGORY_NAME_LEADING_RE.match(text)
    if match:
        return match.group(1)
    return text or "Unspecified category"


class _ExpenseSink(Protocol):
    """The one method this module needs from FinancialCoreService — declared as a Protocol so the
    posting loop is trivially mockable in tests with a fake sink."""

    async def record_historical_expense(self, cmd: RecordHistoricalExpenseCommand) -> Any: ...


@dataclass(frozen=True)
class ExpensePostingPlan:
    """The validated, debit-only work list extracted from an approved batch's manifest, ready to
    post. `expected_total_cents` is Σ(ex_gst + gst) over the debit rows and is the figure the
    post-apply integrity check must reproduce exactly."""

    expense_rows: list[dict]
    approved_by: UUID
    reason: str
    expected_total_cents: int


@dataclass
class ExpensePostingResult:
    """Reconciliation evidence for a completed post — exact line/posted/replayed counts, cents
    totals, and per-fund split, for the audit log and the endpoint response."""

    line_count: int = 0
    posted: int = 0
    replayed: int = 0
    posted_total_cents: int = 0
    expected_total_cents: int = 0
    per_fund_cents: dict[str, int] = field(default_factory=dict)
    approved_by: Optional[str] = None
    executed_by: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "line_count": self.line_count,
            "posted": self.posted,
            "replayed": self.replayed,
            "posted_total_cents": self.posted_total_cents,
            "expected_total_cents": self.expected_total_cents,
            "per_fund_cents": self.per_fund_cents,
            "approved_by": self.approved_by,
            "executed_by": self.executed_by,
        }


def validate_batch_authorisation(*, batch_doc: dict, executed_by: UUID) -> UUID:
    """Dual-control gate — pure, manifest-free (so callers can reject before fetching the
    manifest). Returns the batch's `approved_by` UUID. Raises ExpensePostingError if approval is
    missing or the executor is the same person who approved."""
    approved_by_raw = batch_doc.get("approved_by")
    if not approved_by_raw:
        raise ExpensePostingError(
            f"Batch {batch_doc.get('batch_id')!r} has no approved_by — cannot establish dual control."
        )
    approved_by = UUID(str(approved_by_raw))
    if approved_by == executed_by:
        raise ExpensePostingError(
            f"executed_by ({executed_by}) must differ from the batch's approved_by "
            f"({approved_by}) — dual control requires two distinct identities."
        )
    return approved_by


def validate_manifest_and_extract_debits(
    *, batch_doc: dict, manifest_doc: dict, approved_by: UUID, building_id: str, batch_id: str,
) -> ExpensePostingPlan:
    """Manifest-hash drift check + manifest self-consistency check + debit-only extraction.
    Returns a plan whose `expense_rows` may be empty (a manifest with only credit rows is a clean
    no-op, not an error). Raises ExpensePostingError on hash drift or a self-inconsistent
    manifest."""
    if manifest_doc.get("manifest_hash") != batch_doc.get("manifest_hash"):
        raise ExpensePostingError(
            "Manifest has changed since the batch was reviewed/approved — re-run review before "
            "applying (same defensive check the /approve endpoint itself performs)."
        )

    all_transactions = manifest_doc.get("transactions") or []
    expense_rows = [t for t in all_transactions if t.get("direction") == "debit"]

    manifest_recorded_total = manifest_doc.get("expected_debit_cents")
    if manifest_recorded_total is not None and int(manifest_recorded_total) != sum(
        int(r["amount_cents"]) for r in expense_rows
    ):
        raise ExpensePostingError(
            "Manifest integrity check failed: debit transactions' amount_cents sum does not match "
            "expected_debit_cents — refusing to post against a manifest that disagrees with itself."
        )

    expected_total_cents = sum(
        int(r["amount_ex_gst_cents"]) + int(r["gst_cents"]) for r in expense_rows
    )
    reason = (
        f"Historical category-expense posting for building {building_id}, batch {batch_id}: "
        f"{len(expense_rows)} approved debit line(s), reviewed_by={batch_doc.get('reviewed_by')}, "
        f"approved_by={approved_by}."
    )
    return ExpensePostingPlan(
        expense_rows=expense_rows,
        approved_by=approved_by,
        reason=reason,
        expected_total_cents=expected_total_cents,
    )


def build_expense_command(
    *,
    row: dict,
    building_id: str,
    batch_id: str,
    scheme_ref: SchemeRef,
    fund_ids: dict[str, UUID],
    reason: str,
    approved_by: UUID,
    executed_by: UUID,
    is_test_data: bool,
) -> RecordHistoricalExpenseCommand:
    """Build one dual-control historical-expense command from a manifest debit row. Pure; raises
    ExpensePostingError if the row's fund_type has no resolved fund_id."""
    fund_type = row["fund_type"]
    fund_id = fund_ids.get(fund_type)
    if fund_id is None:
        raise ExpensePostingError(f"No fund_id resolved for fund_type={fund_type!r}")

    category_name = _extract_category_name(row.get("description", ""))
    financial_year = row["financial_year"]
    normalised_category = " ".join(category_name.lower().split())
    idempotency_key = (
        f"historical-expense:{building_id}:{financial_year}:{fund_type}:{normalised_category}"
    )
    authorisation = HistoricalPostingAuthorisation(
        reconstruction_batch_id=UUID(batch_id),
        reason=reason,
        approved_by=approved_by,
        executed_by=executed_by,
        approval_reference=f"batch:{batch_id}:approved_by:{approved_by}",
    )
    return RecordHistoricalExpenseCommand(
        scheme_ref=scheme_ref,
        category_name=category_name,
        amount_cents=int(row["amount_ex_gst_cents"]),
        gst_cents=int(row["gst_cents"]),
        transaction_date=date.fromisoformat(str(row["posted_date"])[:10]),
        authorisation=authorisation,
        fund_id=fund_id,
        financial_year=financial_year,
        description=row.get("description"),
        idempotency_key=idempotency_key,
        is_test_data=is_test_data,
    )


async def post_expense_plan(
    *,
    svc: _ExpenseSink,
    plan: ExpensePostingPlan,
    building_id: str,
    batch_id: str,
    scheme_ref: SchemeRef,
    fund_ids: dict[str, UUID],
    executed_by: UUID,
    is_test_data: bool,
) -> ExpensePostingResult:
    """Post every debit line in `plan` through `svc.record_historical_expense`, tally
    posted/replayed + per-fund totals, and enforce the post-apply integrity check. Idempotent:
    lines whose idempotency key already posted come back as replays and are not double-counted in
    `posted`. Raises ExpensePostingError if the posted total does not reproduce the manifest's
    debit total exactly."""
    result = ExpensePostingResult(
        line_count=len(plan.expense_rows),
        expected_total_cents=plan.expected_total_cents,
        approved_by=str(plan.approved_by),
        executed_by=str(executed_by),
    )
    for row in plan.expense_rows:
        cmd = build_expense_command(
            row=row,
            building_id=building_id,
            batch_id=batch_id,
            scheme_ref=scheme_ref,
            fund_ids=fund_ids,
            reason=plan.reason,
            approved_by=plan.approved_by,
            executed_by=executed_by,
            is_test_data=is_test_data,
        )
        expense = await svc.record_historical_expense(cmd)
        line_total = int(row["amount_ex_gst_cents"]) + int(row["gst_cents"])
        result.posted_total_cents += line_total
        result.per_fund_cents[row["fund_type"]] = (
            result.per_fund_cents.get(row["fund_type"], 0) + line_total
        )
        if getattr(expense, "metadata", {}).get("replayed"):
            result.replayed += 1
        else:
            result.posted += 1

    if result.posted_total_cents != plan.expected_total_cents:
        raise ExpensePostingError(
            f"Post-apply integrity check failed: posted {result.posted_total_cents} cents but the "
            f"approved manifest's debit lines sum to {plan.expected_total_cents} cents."
        )
    return result


# ── Mongo fetch helpers (self-contained so this module needs no rbs import) ───────────────────

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


async def _get_batch_doc(mongo_db, building_id: str, batch_id: str) -> dict:
    doc = await mongo_db.demo_bank_reconstruction_batches.find_one(
        {"building_id": building_id, "batch_id": batch_id}
    )
    if doc is None:
        raise ExpensePostingError(
            f"No reconstruction batch {batch_id!r} found for building_id={building_id!r}"
        )
    return doc


async def _get_latest_manifest_doc(mongo_db, building_id: str, batch_id: str) -> dict:
    doc = await mongo_db.demo_bank_reconstruction_manifests.find_one(
        {"building_id": building_id, "batch_id": batch_id},
        sort=[("version", -1)],
    )
    if doc is None:
        raise ExpensePostingError(
            f"No manifest found for batch {batch_id!r} — run generate-preview/submit-review first."
        )
    return doc


async def _resolve_scheme_tenant(session, building_id: str) -> tuple[str, str]:
    from sqlalchemy import text

    await session.execute(
        text(f"SELECT set_config('app.tenant_id', '{_TENANT_BYPASS_ID}', true)")
    )
    result = await session.execute(
        text(
            "SELECT tenant_id::text, scheme_id::text FROM core.schemes "
            "WHERE lower(scheme_number) = lower(:num)"
        ),
        {"num": building_id},
    )
    row = result.first()
    if row is None:
        raise ExpensePostingError(f"No core.schemes row found for building_id={building_id!r}")
    return row[0], row[1]


async def run_expense_posting(
    *, mongo_db, building_id: str, batch_id: str, executed_by: UUID, is_test_data: bool,
) -> ExpensePostingResult:
    """Full orchestration entry point for the in-app endpoint: validate → open a Postgres session
    → resolve scheme/fund ids → post via FinancialCoreService. Callers own the batch STATUS gate
    and any state transition; this function only validates authorisation/manifest and posts. DB
    dependencies are imported lazily so the pure validation/posting functions above remain
    importable in unit tests without the Postgres stack.

    Raises ExpensePostingError on any governance/integrity failure (mapped to HTTP 409 by the
    endpoint)."""
    from db_postgres.session import async_session_context, set_tenant
    from services.financial_core.adapters.db_postgres.ledger_repo import PostgresLedgerRepository
    from services.financial_core.adapters.db_postgres.outbox_repo import PostgresOutboxRepository
    from services.financial_core.genesis import resolve_scheme_fund_ids
    from services.financial_core.service import FinancialCoreService

    batch_doc = await _get_batch_doc(mongo_db, building_id, batch_id)
    approved_by = validate_batch_authorisation(batch_doc=batch_doc, executed_by=executed_by)
    manifest_doc = await _get_latest_manifest_doc(mongo_db, building_id, batch_id)
    plan = validate_manifest_and_extract_debits(
        batch_doc=batch_doc, manifest_doc=manifest_doc,
        approved_by=approved_by, building_id=building_id, batch_id=batch_id,
    )
    if not plan.expense_rows:
        return ExpensePostingResult(
            approved_by=str(approved_by), executed_by=str(executed_by),
        )

    async with async_session_context() as session:
        tenant_id, scheme_id = await _resolve_scheme_tenant(session, building_id)
        await set_tenant(session, tenant_id)
        scheme_ref = SchemeRef(tenant_id=UUID(tenant_id), scheme_id=UUID(scheme_id))
        fund_ids = await resolve_scheme_fund_ids(session, scheme_ref)

        ledger = PostgresLedgerRepository(session)
        outbox = PostgresOutboxRepository(session)
        svc = FinancialCoreService(ledger, outbox)

        return await post_expense_plan(
            svc=svc, plan=plan, building_id=building_id, batch_id=batch_id,
            scheme_ref=scheme_ref, fund_ids=fund_ids,
            executed_by=executed_by, is_test_data=is_test_data,
        )
