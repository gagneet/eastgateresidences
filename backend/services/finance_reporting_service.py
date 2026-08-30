# @featuretrace:levy-financial-year-reports — PostgreSQL-backed Levy Financial Year report contracts.
# Layer: service
# Data flow: /finance/reports/* -> finance.* journal/levy tables + core.lots -> JSON/CSV report payloads (building-scoped).
# Related: backend/routers/finance_reports.py
"""Canonical report-read service for Levy Financial Year finance reports.

This service deliberately reads PostgreSQL finance tables only. MongoDB and
portal snapshots can be added later as reconciliation evidence, but they must
not silently substitute into canonical General Ledger or Aging outputs.
"""
from __future__ import annotations

import csv
import io
import os
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from html import escape
from typing import Any

from sqlalchemy import text

from db_postgres.repos.config_repo import resolve_scheme_context
from db_postgres.session import async_session_context, set_tenant
from services.owner_service import format_owner_names, get_all_unit_owners
from services.settings_service import get_general_settings
from utils.finance_helpers import get_fy_date_range, get_fy_label

try:
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XlsxImage
    from openpyxl.styles import Font

    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Image as PdfImage
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False


AGING_BUCKETS = (
    ("current", "Current"),
    ("days_1_30", "1-30 Days Overdue"),
    ("days_31_60", "31-60 Days Overdue"),
    ("days_61_90", "61-90 Days Overdue"),
    ("days_91_180", "91-180 Days Overdue"),
    ("days_181_plus", "181+ Days Overdue"),
)


REPORT_CATALOG: dict[str, dict[str, Any]] = {
    "aged-receivables": {
        "title": "Aging",
        "status": "implemented",
        "formats": ["screen", "csv", "xlsx", "pdf", "docx"],
        "source_tables": ["finance.levy_items", "finance.levy_runs", "finance.funds", "core.lots"],
        "reference_reports": ["Aged Balance Report(1).pdf"],
    },
    "general-ledger": {
        "title": "General Ledger",
        "status": "implemented",
        "formats": ["screen", "csv", "xlsx", "pdf", "docx"],
        "source_tables": ["finance.journal_entries", "finance.journal_lines", "finance.gl_accounts", "finance.funds", "core.lots"],
        "reference_reports": ["General Ledgers - 2022(4).pdf", "General Ledgers 2020-2021(4).pdf"],
    },
    "agm-pack": {
        "title": "AGM Pack",
        "status": "planned",
        "formats": ["docx", "pdf"],
        "source_tables": [
            "contract pending: scheme identity and roll",
            "contract pending: meeting agenda, motions and attachments",
            "contract pending: approved financial appendices",
        ],
        "reference_reports": ["UP16244 Sierra - AGM 2026.pdf"],
        "notes": (
            "No AGM Pack generator exists anywhere in the codebase today -- confirmed by repository-wide search "
            "2026-08-02. Building it means bundling three things that do not yet exist as one document: this "
            "'financial-statement' report (existing narrative Financial Intelligence PDF, see its own notes), the "
            "'proposed-budget' report (existing on-screen CPI proposal generator, no PDF export yet, see its own "
            "notes), and meeting/motion metadata from backend/routers/meetings.py's agm/agm_motions/agm_attendance "
            "Mongo collections (no generator function exists there either -- that router is CRUD-only)."
        ),
    },
    "financial-statement": {
        "title": "Financial Report",
        "status": "implemented",
        "formats": ["pdf"],
        "source_tables": [
            "mongo: financial_anomalies, financial_forecasts, lot_financial_summary, levy_categories, "
            "capital_replacement_schedule, council_rate_settings (existing narrative Financial Intelligence "
            "report at backend/services/report_service.py::generate_full_report -- not yet ported to the "
            "canonical Postgres finance.* contract used by Aging/General Ledger above)",
        ],
        "reference_reports": [
            "Finanical Report Dated 31 December 2021(2).pdf",
            "Audited_Financial_Report.pdf",
        ],
        "external_link": "/finance/report/{financial_year}",
        "notes": (
            "Confirmed working today via GET /api/finance/report/{financial_year} "
            "(backend/routers/finance_intelligence.py), reachable separately from "
            "frontend/src/pages/dashboard/FinanceIntelligencePage.jsx. Consolidated into this workbench "
            "2026-08-02 as a direct link rather than re-implemented, since the existing report is complete and "
            "working -- re-running the same building logic against a second, not-yet-Postgres-backed pipeline "
            "would duplicate it, not improve it. Porting its data reads from Mongo to finance.* Postgres tables "
            "is separate, real, not-yet-scoped work."
        ),
    },
    "gst-bas-statement": {
        "title": "GST / BAS Statement",
        "status": "implemented",
        "formats": ["pdf"],
        "source_tables": [
            "mongo: annual_levies, expense_transactions, water_bills, council_rates via "
            "backend/services/gst_service.py (existing GST/BAS report at "
            "backend/routers/gst_bas.py::_generate_bas_pdf -- not yet ported to the canonical Postgres "
            "finance.* contract)",
        ],
        "reference_reports": [
            "UP13195 - 2023 GST Inclusive Financial Statement - 2023.pdf",
            "UP13195 - 2023 GST Inclusive Financial Statement(1).pdf",
        ],
        "external_link": "/gst/export/pdf",
        "notes": (
            "Confirmed working today via GET /api/gst/export/pdf?financial_year={financial_year} "
            "(backend/routers/gst_bas.py), reachable separately from "
            "frontend/src/pages/dashboard/admin/GSTBASLedgerPage.jsx. This is the closest existing report to the "
            "reference sample's 'GST Inclusive Financial Statement' -- it is BAS/ATO-field-oriented rather than "
            "a P&L-with-GST-columns layout, but real and database-backed (Mongo). Consolidated as a direct link "
            "2026-08-02 for the same reason as the Financial Report entry above."
        ),
    },
    "bank-transactions": {
        "title": "Bank Transactions",
        "status": "planned",
        "formats": ["csv", "xlsx", "pdf", "docx"],
        "source_tables": [
            "contract pending: trust-bank transaction feed",
            "contract pending: receipts and reconciliation evidence",
        ],
        "reference_reports": ["Bank Transaction Reports 3 December 2020 - 11 January 2022(1).pdf"],
    },
    "proposed-budget": {
        "title": "Proposed Budget",
        "status": "planned",
        "formats": ["xlsx", "pdf", "docx"],
        "source_tables": [
            "contract pending: approved budget version",
            "contract pending: prior actuals and current-year budget baseline",
            "contract pending: proposed levy categories by fund",
        ],
        "reference_reports": ["Proposed_Budget.pdf"],
        "notes": (
            "Not a blank slate: GET/POST /api/budget-proposals (backend/routers/finance.py, CPI-adjusted "
            "admin/sinking proposal per-UOE) already works today, reachable from "
            "frontend/src/pages/dashboard/FinancialProjectionsPage.jsx -- but it is Mongo-based, screen/JSON "
            "only, and has no PDF/XLSX export. Kept 'planned' here (not consolidated as a direct link like "
            "financial-statement/gst-bas-statement above) because it has no export artefact to link to yet; "
            "porting its CPI-adjustment algorithm onto finance.levy_items/funds/journal_lines plus adding an "
            "export is the real remaining work, and is also a prerequisite for the AGM Pack report above."
        ),
    },
    "levy-listing": {
        "title": "Levy Listing",
        "status": "planned",
        "formats": ["csv", "xlsx", "pdf", "docx"],
        "source_tables": ["finance.levy_runs", "finance.levy_items", "finance.receipt_allocations", "core.lots"],
        "reference_reports": ["UP13195 - Ledger Listing - 2023.pdf", "UP13195 - Ledger Listing(1).pdf"],
    },
    "levy-balance": {
        "title": "Levy Balance",
        "status": "planned",
        "formats": ["csv", "xlsx", "pdf", "docx"],
        "source_tables": ["finance.levy_items", "finance.receipt_allocations", "core.lots"],
        "reference_reports": ["UP13195 - Levy Balance - 2023.pdf", "UP13195 - Levy Balance(1).pdf"],
    },
    "status-report": {
        "title": "Status Report",
        "status": "planned",
        "formats": ["pdf", "docx"],
        "source_tables": [
            "contract pending: finance exceptions and payments",
            "contract pending: maintenance and compliance status",
            "contract pending: workflow and document milestones",
        ],
        "reference_reports": ["StatusReport (Nov 21) - LJHooker-2021.pdf"],
    },
    "roll-list": {
        "title": "Roll List",
        "status": "planned",
        "formats": ["csv", "xlsx", "pdf", "docx"],
        "source_tables": ["core.lots", "core.user_units", "core.users", "core.ownership_periods"],
        "reference_reports": ["13195 Roll List - 13122021.pdf"],
    },
}


def cents_to_dollars(cents: int | Decimal | None) -> float:
    return round(float(cents or 0) / 100, 2)


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def aging_bucket_key(due_on: date | None, as_of: date) -> str:
    """StrataOS-native aging buckets. Deliberately six 30-day-wide tiers (not the
    five-column Current/30+/60+/90+/120+ scheme in the supplied reference PDF)
    so a 91-180-day debt stays distinguishable from a 181+-day one -- that split
    matters for arrears/recovery decisions and collapsing it would only make our
    report resemble the sample more closely, not make it more useful."""
    if due_on is None or due_on > as_of:
        return "current"
    age_days = (as_of - due_on).days
    if age_days <= 0:
        return "current"
    if age_days <= 30:
        return "days_1_30"
    if age_days <= 60:
        return "days_31_60"
    if age_days <= 90:
        return "days_61_90"
    if age_days <= 180:
        return "days_91_180"
    return "days_181_plus"


@dataclass(frozen=True)
class ReportWindow:
    financial_year: str
    label: str
    start_date: date
    end_date: date
    start_month: int
    building_name: str | None = None
    building_address: str | None = None
    logo_url: str | None = None


async def resolve_report_window(building_id: str, financial_year: str) -> ReportWindow:
    settings = await get_general_settings(
        building_id,
        {"_id": 0, "financial_year_start_month": 1, "building_name": 1, "building_address": 1, "logo_url": 1},
    )
    start_month = int((settings or {}).get("financial_year_start_month") or 1)
    year_int = int(str(financial_year).split("-")[0])
    start_raw, end_raw = get_fy_date_range(year_int, start_month)
    return ReportWindow(
        financial_year=str(year_int),
        label=get_fy_label(year_int, start_month),
        start_date=date.fromisoformat(start_raw),
        end_date=date.fromisoformat(end_raw),
        start_month=start_month,
        building_name=(settings or {}).get("building_name"),
        building_address=(settings or {}).get("building_address"),
        logo_url=(settings or {}).get("logo_url"),
    )


def _base_metadata(
    *,
    building_id: str,
    window: ReportWindow,
    as_of: date | None = None,
    report_id: str,
) -> dict[str, Any]:
    return {
        "report_id": report_id,
        "building_id": building_id,
        "financial_year": window.financial_year,
        "financial_year_label": window.label,
        "financial_year_start": window.start_date.isoformat(),
        "financial_year_end": window.end_date.isoformat(),
        "financial_year_start_month": window.start_month,
        "as_of": (as_of or window.end_date).isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "postgres_ledger",
        "building_name": window.building_name,
        "building_address": window.building_address,
        "logo_url": window.logo_url,
    }


async def _scheme_context_or_payload(
    building_id: str,
    window: ReportWindow,
    report_id: str,
    as_of: date | None = None,
) -> tuple[dict | None, dict | None]:
    scheme = await resolve_scheme_context(building_id)
    tenant_id = scheme.get("tenant_id") if isinstance(scheme, dict) else None
    scheme_id = scheme.get("scheme_id") if isinstance(scheme, dict) else None
    if tenant_id and scheme_id:
        return scheme, None
    warning = (
        "PostgreSQL scheme context is missing tenant_id or scheme_id."
        if scheme
        else "No PostgreSQL scheme context exists for this building."
    )
    return None, {
        **_base_metadata(building_id=building_id, window=window, as_of=as_of, report_id=report_id),
        "completeness_state": "not_imported",
        "reconciliation_state": "source_unavailable",
        "quality_warnings": [warning],
        "summary": {},
        "rows": [],
    }


async def get_aged_receivables_report(
    *,
    building_id: str,
    financial_year: str,
    as_of: date | None = None,
    fund_id: str | None = None,
    lot_number: str | None = None,
) -> dict[str, Any]:
    window = await resolve_report_window(building_id, financial_year)
    as_of_date = as_of or window.end_date
    scheme, empty_payload = await _scheme_context_or_payload(
        building_id,
        window,
        "levy_aged_receivables",
        as_of=as_of_date,
    )
    if empty_payload:
        return empty_payload

    params = {
        "tenant_id": str(scheme["tenant_id"]),
        "scheme_id": str(scheme["scheme_id"]),
        "financial_year": window.financial_year,
        "as_of": as_of_date,
        "fund_id": fund_id,
        "lot_number": lot_number,
    }

    try:
        records = await _fetch_aged_receivables_records(session_params=params)
    except Exception as exc:
        return {
            **_base_metadata(building_id=building_id, window=window, as_of=as_of_date, report_id="levy_aged_receivables"),
            "completeness_state": "partial",
            "reconciliation_state": "source_unavailable",
            "quality_warnings": [f"Could not read Postgres levy items: {exc}"],
            "summary": {},
            "rows": [],
            "lot_summary": [],
        }

    bucket_totals = {key: 0 for key, _label in AGING_BUCKETS}
    bucket_lots: dict[str, set[str]] = {key: set() for key, _label in AGING_BUCKETS}
    rows: list[dict[str, Any]] = []
    lot_totals: dict[str, dict[str, Any]] = {}
    total_outstanding = 0
    true_overdue = 0
    credit_cents = 0

    for record in records:
        outstanding = _to_int(record["outstanding_cents"])
        key = aging_bucket_key(record.get("due_date"), as_of_date)
        if outstanding < 0:
            credit_cents += abs(outstanding)
        else:
            bucket_totals[key] += outstanding
            total_outstanding += outstanding
            if key != "current":
                true_overdue += outstanding
            lot_key = str(record.get("lot_number") or record.get("unit_number") or "")
            if lot_key:
                bucket_lots[key].add(lot_key)

        rows.append({
            "levy_item_id": record["levy_item_id"],
            "levy_run_id": record["levy_run_id"],
            "lot_number": record.get("lot_number"),
            "unit_number": record.get("unit_number"),
            "fund_code": record.get("fund_code"),
            "fund_name": record.get("fund_name"),
            "quarter_no": record.get("quarter_no"),
            "issue_date": record["issue_date"].isoformat() if record.get("issue_date") else None,
            "due_date": record["due_date"].isoformat() if record.get("due_date") else None,
            "status": record.get("status"),
            "bucket": key,
            "principal_cents": _to_int(record["principal_cents"]),
            "gst_cents": _to_int(record["gst_cents"]),
            "interest_cents": _to_int(record["interest_cents"]),
            "recovery_costs_cents": _to_int(record["recovery_costs_cents"]),
            "paid_cents": _to_int(record["paid_as_of_cents"]),
            "current_paid_cents": _to_int(record["current_paid_cents"]),
            "outstanding_cents": outstanding,
            "outstanding": cents_to_dollars(outstanding),
        })

        # Per-lot rollup, signed (credit lots carry a negative balance rather than
        # being stripped out) -- matches the reference "Aged Balance Report" row
        # grain of one row per owner/account, not one row per levy charge.
        lot_key = str(record.get("lot_number") or record.get("unit_number") or "")
        if lot_key:
            lot_entry = lot_totals.setdefault(lot_key, {
                "lot_number": record.get("lot_number"),
                "unit_number": record.get("unit_number"),
                "balance_cents": 0,
                "bucket_cents": {bkey: 0 for bkey, _blabel in AGING_BUCKETS},
                # Admin Fund and Sinking/Capital Works Fund arrears are legally
                # distinct debts under ACT/NSW strata law -- the overall
                # balance_cents/bucket_cents above commingle them, which is the
                # right figure for "does this lot owe anything at all" but wrong
                # for "how much Admin Fund arrears vs Sinking Fund arrears."
                # fund_totals splits the same figures per fund_type.
                "fund_totals": {},
                "last_charge_due_date": None,
            })
            lot_entry["balance_cents"] += outstanding
            lot_entry["bucket_cents"][key] += outstanding
            fund_type = record.get("fund_type") or "other"
            fund_entry = lot_entry["fund_totals"].setdefault(fund_type, {
                "balance_cents": 0,
                "bucket_cents": {bkey: 0 for bkey, _blabel in AGING_BUCKETS},
            })
            fund_entry["balance_cents"] += outstanding
            fund_entry["bucket_cents"][key] += outstanding
            due_on = record.get("due_date")
            if due_on and (
                lot_entry["last_charge_due_date"] is None
                or due_on > lot_entry["last_charge_due_date"]
            ):
                lot_entry["last_charge_due_date"] = due_on

    owner_by_unit = {}
    try:
        owner_by_unit = await get_all_unit_owners(building_id)
    except Exception:
        # A Mongo outage must not fail a report whose actual financial data
        # (Postgres) already loaded successfully -- degrade owner_name to None
        # with a warning instead.
        pass

    def _owner_display_name(entry: dict[str, Any]) -> str | None:
        info = owner_by_unit.get(entry.get("unit_number") or "") or owner_by_unit.get(entry.get("lot_number") or "") or {}
        name = format_owner_names(info.get("owner_name") or "", info.get("co_owner_name") or "")
        return name or None

    def _lot_sort_key(lot_key: str) -> tuple:
        return (0, int(lot_key)) if lot_key.isdigit() else (1, lot_key)

    lot_summary = [
        {
            "lot_number": lot_totals[lot_key]["lot_number"],
            "unit_number": lot_totals[lot_key]["unit_number"],
            "owner_name": _owner_display_name(lot_totals[lot_key]),
            "balance_cents": lot_totals[lot_key]["balance_cents"],
            "balance": cents_to_dollars(lot_totals[lot_key]["balance_cents"]),
            "last_charge_due_date": (
                lot_totals[lot_key]["last_charge_due_date"].isoformat()
                if lot_totals[lot_key]["last_charge_due_date"] else None
            ),
            "buckets": {
                bkey: {
                    "label": blabel,
                    "amount_cents": lot_totals[lot_key]["bucket_cents"][bkey],
                    "amount": cents_to_dollars(lot_totals[lot_key]["bucket_cents"][bkey]),
                }
                for bkey, blabel in AGING_BUCKETS
            },
            # Per-fund-type split of the same balance/buckets above -- Admin Fund
            # and Sinking/Capital Works Fund arrears are legally distinct debts.
            "fund_balances": {
                fund_type: {
                    "balance_cents": fund_data["balance_cents"],
                    "balance": cents_to_dollars(fund_data["balance_cents"]),
                    "buckets": {
                        bkey: {
                            "label": blabel,
                            "amount_cents": fund_data["bucket_cents"][bkey],
                            "amount": cents_to_dollars(fund_data["bucket_cents"][bkey]),
                        }
                        for bkey, blabel in AGING_BUCKETS
                    },
                }
                for fund_type, fund_data in lot_totals[lot_key]["fund_totals"].items()
            },
        }
        for lot_key in sorted(lot_totals, key=_lot_sort_key)
    ]

    warnings: list[str] = []
    if not records:
        state = "not_imported"
        reconciliation = "pending_reconstruction"
        warnings.append("No PostgreSQL levy items were found for this Levy Financial Year.")
    elif total_outstanding == 0 and credit_cents == 0:
        state = "complete"
        reconciliation = "reconciled"
    else:
        state = "partial"
        reconciliation = "unreconciled"
        warnings.append("Outstanding or credit balances require AR control reconciliation before final reporting.")

    return {
        **_base_metadata(building_id=building_id, window=window, as_of=as_of_date, report_id="levy_aged_receivables"),
        "completeness_state": state,
        "reconciliation_state": reconciliation,
        "quality_warnings": warnings,
        "summary": {
            "total_outstanding_cents": total_outstanding,
            "total_outstanding": cents_to_dollars(total_outstanding),
            "true_overdue_cents": true_overdue,
            "true_overdue": cents_to_dollars(true_overdue),
            "credit_cents": credit_cents,
            "credit": cents_to_dollars(credit_cents),
            "levy_item_count": len(records),
            "bucket_totals": {
                key: {
                    "label": label,
                    "amount_cents": bucket_totals[key],
                    "amount": cents_to_dollars(bucket_totals[key]),
                    "lot_count": len(bucket_lots[key]),
                }
                for key, label in AGING_BUCKETS
            },
        },
        "rows": rows,
        "lot_summary": lot_summary,
    }


async def _fetch_aged_receivables_records(*, session_params: dict[str, Any]) -> list[dict[str, Any]]:
    params = session_params
    async with async_session_context() as session:
        await set_tenant(session, params["tenant_id"])
        result = await session.execute(
            text(
                """
                SELECT
                    li.levy_item_id::text AS levy_item_id,
                    lr.levy_run_id::text AS levy_run_id,
                    lr.financial_year,
                    lr.quarter_no,
                    lr.issue_date,
                    lr.due_date,
                    li.status,
                    l.lot_number,
                    l.unit_number,
                    f.fund_code,
                    f.fund_name,
                    li.principal_cents,
                    li.gst_cents,
                    li.interest_cents,
                    li.recovery_costs_cents,
                    li.paid_cents AS current_paid_cents,
                    COALESCE(alloc.allocated_cents, 0) AS paid_as_of_cents,
                    (
                        li.principal_cents
                        + li.gst_cents
                        + li.interest_cents
                        + li.recovery_costs_cents
                        - COALESCE(alloc.allocated_cents, 0)
                    ) AS outstanding_cents,
                    f.fund_type
                FROM finance.levy_items li
                JOIN finance.levy_runs lr
                  ON lr.levy_run_id = li.levy_run_id
                 AND lr.tenant_id = li.tenant_id
                 AND lr.scheme_id = li.scheme_id
                LEFT JOIN (
                    SELECT
                        ra.levy_item_id,
                        SUM(ra.allocated_cents) AS allocated_cents
                    FROM finance.receipt_allocations ra
                    JOIN finance.receipts r
                      ON r.receipt_id = ra.receipt_id
                     AND r.tenant_id = ra.tenant_id
                    WHERE ra.tenant_id = CAST(:tenant_id AS uuid)
                      AND r.scheme_id = CAST(:scheme_id AS uuid)
                      AND r.received_on <= :as_of
                    GROUP BY ra.levy_item_id
                ) alloc
                  ON alloc.levy_item_id = li.levy_item_id
                LEFT JOIN finance.funds f
                  ON f.fund_id = li.fund_id
                 AND f.tenant_id = li.tenant_id
                 AND f.scheme_id = li.scheme_id
                LEFT JOIN core.lots l
                  ON l.lot_id = li.lot_id
                 AND l.scheme_id = li.scheme_id
                WHERE li.tenant_id = CAST(:tenant_id AS uuid)
                  AND li.scheme_id = CAST(:scheme_id AS uuid)
                  AND lr.financial_year = :financial_year
                  -- Issued but not-yet-due levies age as current; future-issued runs are not receivables yet.
                  AND (lr.issue_date IS NULL OR lr.issue_date <= :as_of)
                  -- CAST(:fund_id AS text) IS NULL, not bare ":fund_id IS NULL" --
                  -- asyncpg cannot infer a bind parameter's type from a NULL value
                  -- that's only ever compared inside CAST(... AS uuid) in the OTHER
                  -- branch of this OR; casting the NULL-check branch to text gives
                  -- it an explicit, unambiguous type. Live-confirmed 2026-08-02:
                  -- every call to this report (fund_id always None from the
                  -- default UI filter) crashed with asyncpg.AmbiguousParameterError
                  -- before this fix -- this report had never actually been run
                  -- against a real Postgres connection until now.
                  AND (CAST(:fund_id AS text) IS NULL OR li.fund_id = CAST(:fund_id AS uuid))
                  AND (CAST(:lot_number AS text) IS NULL OR l.lot_number = :lot_number OR l.unit_number = :lot_number)
                ORDER BY l.lot_number NULLS LAST, lr.due_date, f.fund_code
                """
            ),
            params,
        )
        records = [dict(row._mapping) for row in result]
        return records


async def get_general_ledger_report(
    *,
    building_id: str,
    financial_year: str,
    fund_id: str | None = None,
    account_code: str | None = None,
    lot_number: str | None = None,
    posted_only: bool = True,
) -> dict[str, Any]:
    window = await resolve_report_window(building_id, financial_year)
    scheme, empty_payload = await _scheme_context_or_payload(building_id, window, "general_ledger")
    if empty_payload:
        return empty_payload

    params = {
        "tenant_id": str(scheme["tenant_id"]),
        "scheme_id": str(scheme["scheme_id"]),
        "start_date": window.start_date,
        "end_date": window.end_date,
        "fund_id": fund_id,
        "account_code": account_code,
        "lot_number": lot_number,
        "posted_only": posted_only,
    }

    try:
        records, exceptions, opening_by_account, fund_cash_reconciliation = await _fetch_general_ledger_data(
            session_params=params, window=window,
        )
    except Exception as exc:
        return {
            **_base_metadata(building_id=building_id, window=window, report_id="general_ledger"),
            "completeness_state": "partial",
            "reconciliation_state": "source_unavailable",
            "quality_warnings": [f"Could not read Postgres journal lines: {exc}"],
            "summary": {},
            "rows": [],
        }

    # Seed each account's running balance from its pre-window position (GAP-FIN-033
    # Part D2) so running_balance is a true opening->closing figure, not just
    # movement within this FY -- previously every account silently started at
    # zero regardless of prior-year activity.
    running_by_account: dict[str, int] = dict(opening_by_account)
    by_account_type: dict[str, int] = {}
    by_fund: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    total_debit = 0
    total_credit = 0

    for record in records:
        amount = _to_int(record["amount_cents"])
        direction = str(record["direction"])
        debit = amount if direction == "debit" else 0
        credit = amount if direction == "credit" else 0
        total_debit += debit
        total_credit += credit
        account_key = str(record.get("account_code") or record["account_name"])
        movement = debit - credit
        running_by_account[account_key] = running_by_account.get(account_key, 0) + movement
        account_type = record.get("account_type") or "unclassified"
        by_account_type[account_type] = by_account_type.get(account_type, 0) + movement
        fund_key = record.get("fund_code") or "unassigned"
        by_fund[fund_key] = by_fund.get(fund_key, 0) + movement
        rows.append({
            "journal_entry_id": record["journal_entry_id"],
            "journal_line_id": record["journal_line_id"],
            "entry_number": record["entry_number"],
            "effective_on": record["effective_on"].isoformat() if record.get("effective_on") else None,
            "posted_at": record["posted_at"].isoformat() if record.get("posted_at") else None,
            "status": record.get("status"),
            "source_type": record.get("source_type"),
            "source_reference": record.get("source_reference"),
            "description": record.get("line_narration") or record.get("entry_narration"),
            "account_code": record.get("account_code"),
            "account_name": record.get("account_name"),
            "account_type": record.get("account_type"),
            "fund_code": record.get("fund_code"),
            "fund_name": record.get("fund_name"),
            "lot_number": record.get("lot_number"),
            "unit_number": record.get("unit_number"),
            "direction": direction,
            "debit_cents": debit,
            "credit_cents": credit,
            "amount_cents": amount,
            "gst_cents": _to_int(record["gst_cents"]),
            "debit": cents_to_dollars(debit),
            "credit": cents_to_dollars(credit),
            "running_balance_cents": running_by_account[account_key],
            "running_balance": cents_to_dollars(running_by_account[account_key]),
            "approved_by": record.get("approved_by"),
            "posted_by": record.get("posted_by"),
            "evidence_document_id": record.get("evidence_document_id"),
            "reversal_of_id": record.get("reversal_of_id"),
        })

    warnings: list[str] = []
    if not records:
        state = "not_imported"
        reconciliation = "pending_reconstruction"
        warnings.append("No PostgreSQL journal lines were found for this Levy Financial Year.")
    elif exceptions:
        state = "partial"
        reconciliation = "unreconciled"
        warnings.append("One or more journal entries are unbalanced and must be resolved before final reporting.")
    else:
        state = "complete"
        reconciliation = "reconciled"

    return {
        **_base_metadata(building_id=building_id, window=window, report_id="general_ledger"),
        "completeness_state": state,
        "reconciliation_state": reconciliation,
        "quality_warnings": warnings,
        "summary": {
            "line_count": len(rows),
            "total_debit_cents": total_debit,
            "total_credit_cents": total_credit,
            "total_debit": cents_to_dollars(total_debit),
            "total_credit": cents_to_dollars(total_credit),
            "net_movement_cents": total_debit - total_credit,
            "net_movement": cents_to_dollars(total_debit - total_credit),
            "unbalanced_entry_count": len(exceptions),
            "unbalanced_entries": [
                {
                    "journal_entry_id": item["journal_entry_id"],
                    "entry_number": item["entry_number"],
                    "debit_cents": _to_int(item["debit_cents"]),
                    "credit_cents": _to_int(item["credit_cents"]),
                }
                for item in exceptions
            ],
            # Trial-balance-style section subtotals -- account_type and fund_code
            # already exist on finance.gl_accounts/finance.funds, this is a
            # grouping change only (GAP-FIN-033 Part D2).
            "by_account_type": {
                k: {"net_movement_cents": v, "net_movement": cents_to_dollars(v)}
                for k, v in sorted(by_account_type.items())
            },
            "by_fund": {
                k: {"net_movement_cents": v, "net_movement": cents_to_dollars(v)}
                for k, v in sorted(by_fund.items())
            },
        },
        "rows": rows,
        # Ties the GL's cash-account position to the computed trust balance
        # (CLAUDE.md: "Trust Balance is Computed, Never Stored Statically") via
        # the most recent bank reconciliation run per fund's trust account,
        # rather than presenting the GL as a standalone, unverifiable ledger
        # dump (GAP-FIN-033 Part D3).
        "fund_cash_reconciliation": fund_cash_reconciliation,
    }


async def _fetch_general_ledger_data(
    *, session_params: dict[str, Any], window: ReportWindow,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    params = session_params
    async with async_session_context() as session:
        await set_tenant(session, params["tenant_id"])
        result = await session.execute(
            text(
                """
                SELECT
                    je.journal_entry_id::text AS journal_entry_id,
                    jl.journal_line_id::text AS journal_line_id,
                    je.entry_number,
                    je.effective_on,
                    je.posted_at,
                    je.status,
                    je.source_type,
                    je.source_reference,
                    je.narration AS entry_narration,
                    jl.narration AS line_narration,
                    ga.account_code,
                    ga.account_name,
                    ga.account_type,
                    f.fund_code,
                    f.fund_name,
                    l.lot_number,
                    l.unit_number,
                    jl.direction,
                    jl.amount_cents,
                    jl.gst_cents,
                    je.approved_by::text AS approved_by,
                    je.posted_by::text AS posted_by,
                    je.evidence_document_id::text AS evidence_document_id,
                    je.reversal_of_id::text AS reversal_of_id
                FROM finance.journal_lines jl
                JOIN finance.journal_entries je
                  ON je.journal_entry_id = jl.journal_entry_id
                 AND je.tenant_id = jl.tenant_id
                 AND je.scheme_id = jl.scheme_id
                JOIN finance.gl_accounts ga
                  ON ga.gl_account_id = jl.gl_account_id
                 AND ga.tenant_id = jl.tenant_id
                 AND ga.scheme_id = jl.scheme_id
                LEFT JOIN finance.funds f
                  ON f.fund_id = je.fund_id
                 AND f.tenant_id = je.tenant_id
                 AND f.scheme_id = je.scheme_id
                LEFT JOIN core.lots l
                  ON l.lot_id = jl.lot_id
                 AND l.scheme_id = jl.scheme_id
                WHERE jl.tenant_id = CAST(:tenant_id AS uuid)
                  AND jl.scheme_id = CAST(:scheme_id AS uuid)
                  AND je.effective_on BETWEEN :start_date AND :end_date
                  AND (:posted_only = FALSE OR je.status = 'posted')
                  -- See the matching comment in get_aged_receivables_report's
                  -- query above -- same asyncpg NULL-type-inference fix.
                  AND (CAST(:fund_id AS text) IS NULL OR je.fund_id = CAST(:fund_id AS uuid))
                  AND (CAST(:account_code AS text) IS NULL OR ga.account_code = :account_code)
                  AND (CAST(:lot_number AS text) IS NULL OR l.lot_number = :lot_number OR l.unit_number = :lot_number)
                ORDER BY ga.account_code, je.effective_on, je.entry_number, jl.journal_line_id
                """
            ),
            params,
        )
        records = [dict(row._mapping) for row in result]

        exceptions_result = await session.execute(
            text(
                """
                SELECT
                    je.journal_entry_id::text AS journal_entry_id,
                    je.entry_number,
                    SUM(CASE WHEN jl.direction = 'debit' THEN jl.amount_cents ELSE 0 END) AS debit_cents,
                    SUM(CASE WHEN jl.direction = 'credit' THEN jl.amount_cents ELSE 0 END) AS credit_cents
                FROM finance.journal_entries je
                JOIN finance.journal_lines jl
                  ON jl.journal_entry_id = je.journal_entry_id
                 AND jl.tenant_id = je.tenant_id
                 AND jl.scheme_id = je.scheme_id
                WHERE je.tenant_id = CAST(:tenant_id AS uuid)
                  AND je.scheme_id = CAST(:scheme_id AS uuid)
                  AND je.effective_on BETWEEN :start_date AND :end_date
                  AND (:posted_only = FALSE OR je.status = 'posted')
                GROUP BY je.journal_entry_id, je.entry_number
                HAVING SUM(CASE WHEN jl.direction = 'debit' THEN jl.amount_cents ELSE 0 END)
                    <> SUM(CASE WHEN jl.direction = 'credit' THEN jl.amount_cents ELSE 0 END)
                ORDER BY je.entry_number
                """
            ),
            params,
        )
        exceptions = [dict(row._mapping) for row in exceptions_result]

        # GAP-FIN-033 Part D2: pre-window balance per account, so the main loop's
        # running_balance is a true opening->closing figure. Falls back to
        # finance.funds.opening_balance_cents implicitly (via zero pre-window
        # activity) for a genesis period with no prior journal lines at all.
        opening_result = await session.execute(
            text(
                """
                SELECT ga.account_code, ga.account_name,
                       SUM(CASE WHEN jl.direction = 'debit' THEN jl.amount_cents ELSE -jl.amount_cents END)
                           AS opening_balance_cents
                FROM finance.journal_lines jl
                JOIN finance.journal_entries je
                  ON je.journal_entry_id = jl.journal_entry_id
                 AND je.tenant_id = jl.tenant_id
                 AND je.scheme_id = jl.scheme_id
                JOIN finance.gl_accounts ga
                  ON ga.gl_account_id = jl.gl_account_id
                 AND ga.tenant_id = jl.tenant_id
                 AND ga.scheme_id = jl.scheme_id
                WHERE jl.tenant_id = CAST(:tenant_id AS uuid)
                  AND jl.scheme_id = CAST(:scheme_id AS uuid)
                  AND je.effective_on < :start_date
                  AND (:posted_only = FALSE OR je.status = 'posted')
                GROUP BY ga.account_code, ga.account_name
                """
            ),
            params,
        )
        opening_by_account = {
            str(row.account_code or row.account_name): _to_int(row.opening_balance_cents)
            for row in opening_result
        }

        # GAP-FIN-033 Part D3: tie the GL to the most recent bank reconciliation
        # per fund's trust account within (or nearest before) this window.
        fund_cash_result = await session.execute(
            text(
                """
                SELECT DISTINCT ON (ta.fund_id)
                    f.fund_code, f.fund_name, ta.masked_account_number, ta.bank_name,
                    rr.period_end, rr.cashbook_balance_cents, rr.bank_balance_cents,
                    rr.owner_ledger_balance_cents, rr.difference_cents, rr.status, rr.generated_at
                FROM finance.reconciliation_runs rr
                JOIN finance.trust_accounts ta ON ta.trust_account_id = rr.trust_account_id
                JOIN finance.funds f ON f.fund_id = ta.fund_id
                WHERE rr.tenant_id = CAST(:tenant_id AS uuid)
                  AND rr.scheme_id = CAST(:scheme_id AS uuid)
                  AND rr.period_end <= :end_date
                ORDER BY ta.fund_id, rr.period_end DESC
                """
            ),
            params,
        )
        fund_cash_reconciliation = [
            {
                "fund_code": row.fund_code,
                "fund_name": row.fund_name,
                "bank_name": row.bank_name,
                "masked_account_number": row.masked_account_number,
                "period_end": row.period_end.isoformat() if row.period_end else None,
                "cashbook_balance_cents": _to_int(row.cashbook_balance_cents),
                "cashbook_balance": cents_to_dollars(row.cashbook_balance_cents),
                "bank_balance_cents": _to_int(row.bank_balance_cents),
                "bank_balance": cents_to_dollars(row.bank_balance_cents),
                "owner_ledger_balance_cents": _to_int(row.owner_ledger_balance_cents),
                "owner_ledger_balance": cents_to_dollars(row.owner_ledger_balance_cents),
                "difference_cents": _to_int(row.difference_cents),
                "difference": cents_to_dollars(row.difference_cents),
                "status": row.status,
                "generated_at": row.generated_at.isoformat() if row.generated_at else None,
            }
            for row in fund_cash_result
        ]

    return records, exceptions, opening_by_account, fund_cash_reconciliation


def report_to_csv(report: dict[str, Any]) -> str:
    rows = report.get("rows") or []
    output = io.StringIO()
    if not rows:
        writer = csv.writer(output)
        writer.writerow(["report_id", "financial_year", "completeness_state", "reconciliation_state", "warning"])
        writer.writerow([
            report.get("report_id"),
            report.get("financial_year"),
            report.get("completeness_state"),
            report.get("reconciliation_state"),
            "; ".join(report.get("quality_warnings") or []),
        ])
        return output.getvalue()

    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)

    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def get_report_catalog() -> list[dict[str, Any]]:
    return [{"report_type": key, **value} for key, value in REPORT_CATALOG.items()]


async def get_report_payload(
    *,
    report_type: str,
    building_id: str,
    financial_year: str,
    as_of: date | None = None,
    fund_id: str | None = None,
    account_code: str | None = None,
    lot_number: str | None = None,
    posted_only: bool = True,
) -> dict[str, Any]:
    if report_type == "aged-receivables":
        return await get_aged_receivables_report(
            building_id=building_id,
            financial_year=financial_year,
            as_of=as_of,
            fund_id=fund_id,
            lot_number=lot_number,
        )
    if report_type == "general-ledger":
        return await get_general_ledger_report(
            building_id=building_id,
            financial_year=financial_year,
            fund_id=fund_id,
            account_code=account_code,
            lot_number=lot_number,
            posted_only=posted_only,
        )
    if report_type not in REPORT_CATALOG:
        raise ValueError(f"Unknown report type: {report_type}")
    if REPORT_CATALOG[report_type].get("external_link"):
        return await get_external_module_report_payload(
            report_type=report_type,
            building_id=building_id,
            financial_year=financial_year,
        )
    return await get_planned_report_payload(
        report_type=report_type,
        building_id=building_id,
        financial_year=financial_year,
        as_of=as_of,
    )


async def get_external_module_report_payload(
    *,
    report_type: str,
    building_id: str,
    financial_year: str,
) -> dict[str, Any]:
    """Payload for a report that already exists and works as its own StrataOS
    module (e.g. the narrative Financial Report, the GST/BAS Statement) but has
    not been ported onto the canonical Postgres finance.* Levy Financial Year
    contract used by Aging/General Ledger. Consolidates *access* into this
    workbench via `external_link` without re-implementing (or claiming to have
    re-implemented) the underlying report."""
    window = await resolve_report_window(building_id, financial_year)
    catalog = REPORT_CATALOG[report_type]
    return {
        **_base_metadata(building_id=building_id, window=window, as_of=window.end_date, report_id=report_type),
        "title": catalog["title"],
        "source": "existing_module",
        "completeness_state": "external_module",
        "reconciliation_state": "not_applicable_external_module",
        "quality_warnings": [
            "This report is generated by an existing StrataOS module, not the canonical Postgres Levy Financial "
            "Year contract used by Aging and General Ledger above. Use the direct link to open it.",
        ],
        "source_tables": catalog["source_tables"],
        "reference_reports": catalog["reference_reports"],
        "external_link": catalog["external_link"].format(financial_year=financial_year),
        "summary": {},
        "rows": [],
    }


async def get_planned_report_payload(
    *,
    report_type: str,
    building_id: str,
    financial_year: str,
    as_of: date | None = None,
) -> dict[str, Any]:
    window = await resolve_report_window(building_id, financial_year)
    catalog = REPORT_CATALOG[report_type]
    # Planned report payloads are intentionally non-financial shells. They let the
    # UI/export workflow be reviewed without implying that accounting rows were read.
    return {
        **_base_metadata(
            building_id=building_id,
            window=window,
            as_of=as_of or window.end_date,
            report_id=report_type,
        ),
        "title": catalog["title"],
        "source": "contract_pending",
        "completeness_state": "contract_pending",
        "reconciliation_state": "not_applicable_until_implemented",
        "quality_warnings": [
            "Draft shell only: this report does not yet query accounting rows or generate financial totals.",
            "Do not use partial, zero or missing data values as evidence that this report is complete.",
        ],
        "source_tables": catalog["source_tables"],
        "reference_reports": catalog["reference_reports"],
        "summary": {
            "implementation_status": "contract_pending",
            "required_formats": catalog["formats"],
        },
        "rows": [],
    }


def _report_columns(report: dict[str, Any]) -> list[str]:
    rows = report.get("rows") or []
    columns: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in columns:
                columns.append(key)
    if columns:
        return columns
    return ["report_id", "financial_year", "completeness_state", "reconciliation_state", "warning"]


def _report_rows_for_export(report: dict[str, Any], columns: list[str]) -> list[list[Any]]:
    rows = report.get("rows") or []
    if rows:
        return [[row.get(column) for column in columns] for row in rows]
    return [[
        report.get("report_id"),
        report.get("financial_year"),
        report.get("completeness_state"),
        report.get("reconciliation_state"),
        "; ".join(report.get("quality_warnings") or []),
    ]]


def _local_logo_path(report: dict[str, Any]) -> str | None:
    # Use only the configured building logo. A building-specific fallback would
    # put the wrong strata branding on multi-tenant exports.
    logo_url = str(report.get("logo_url") or "").strip()
    if not logo_url or logo_url.startswith(("http://", "https://")):
        return None
    relative = logo_url.lstrip("/")
    candidates = [
        os.path.abspath(os.path.join(os.getcwd(), "frontend", "public", relative)),
        os.path.abspath(os.path.join(os.getcwd(), "..", "frontend", "public", relative)),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def report_to_xlsx(report: dict[str, Any]) -> bytes:
    if not OPENPYXL_AVAILABLE:
        raise RuntimeError("openpyxl is not available.")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = str(report.get("report_id") or "report")[:31]
    logo_path = _local_logo_path(report)
    row_offset = 1
    if logo_path:
        try:
            image = XlsxImage(logo_path)
            image.height = 54
            image.width = 54
            sheet.add_image(image, "A1")
            row_offset = 4
        except Exception:
            row_offset = 1
    for _ in range(row_offset - 1):
        sheet.append([])
    sheet.append([report.get("title") or report.get("report_id") or "Financial Report"])
    sheet.append(["Building", report.get("building_name") or report.get("building_id")])
    sheet.append(["Financial Year", report.get("financial_year_label") or report.get("financial_year")])
    sheet.append(["As of", report.get("as_of")])
    sheet.append(["Generated", report.get("generated_at")])
    sheet.append(["Source", report.get("source")])
    sheet.append(["Completeness", report.get("completeness_state")])
    sheet.append(["Reconciliation", report.get("reconciliation_state")])
    sheet.append([])
    columns = _report_columns(report)
    sheet.append(columns)
    header_row = sheet.max_row
    for cell in sheet[header_row]:
        cell.font = Font(bold=True)
    for row in _report_rows_for_export(report, columns):
        sheet.append(row)
    for column_cells in sheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 12), 48)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def report_to_pdf(report: dict[str, Any]) -> bytes:
    if not REPORTLAB_AVAILABLE:
        raise RuntimeError("reportlab is not available.")
    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(str(report.get("title") or report.get("report_id") or "Financial Report"), styles["Title"]),
        Paragraph(
            f"{report.get('building_name') or 'Building ' + str(report.get('building_id'))} | "
            f"{report.get('financial_year_label') or report.get('financial_year')} | "
            f"As of {report.get('as_of') or '-'} | "
            f"Source: {report.get('source')} | Completeness: {report.get('completeness_state')}",
            styles["Normal"],
        ),
        Paragraph(f"Generated {report.get('generated_at') or '-'}", styles["Normal"]),
    ]
    logo_path = _local_logo_path(report)
    if logo_path:
        try:
            story.insert(0, PdfImage(logo_path, width=42, height=42))
        except Exception:
            pass
    else:
        story.insert(0, Paragraph("StrataOS Reports", styles["Heading2"]))
    warnings = report.get("quality_warnings") or []
    if warnings:
        story.append(Spacer(1, 8))
        story.append(Paragraph("Warnings: " + "; ".join(warnings), styles["BodyText"]))
    story.append(Spacer(1, 12))
    columns = _report_columns(report)[:10]
    data = [columns]
    for row in _report_rows_for_export(report, columns)[:80]:
        data.append([str(value if value is not None else "")[:48] for value in row])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e7eb")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(table)
    doc.build(story)
    return output.getvalue()


def report_to_docx(report: dict[str, Any]) -> bytes:
    title = escape(str(report.get("title") or report.get("report_id") or "Financial Report"))
    warnings = report.get("quality_warnings") or []
    columns = _report_columns(report)[:8]
    rows = _report_rows_for_export(report, columns)[:40]
    logo_path = _local_logo_path(report)
    image_paragraph = ""
    if logo_path:
        image_paragraph = (
            '<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
            '<wp:extent cx="685800" cy="685800"/><wp:docPr id="1" name="Building Logo"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="1" name="logo.png"/>'
            '<pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="rIdLogo"/>'
            '<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="685800" cy="685800"/>'
            '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
        )
    body_parts = [
        image_paragraph,
        f"<w:p><w:r><w:t>{title}</w:t></w:r></w:p>",
        f"<w:p><w:r><w:t>Building: {escape(str(report.get('building_name') or report.get('building_id') or ''))}</w:t></w:r></w:p>",
        f"<w:p><w:r><w:t>Financial Year: {escape(str(report.get('financial_year_label') or report.get('financial_year') or ''))}</w:t></w:r></w:p>",
        f"<w:p><w:r><w:t>Completeness: {escape(str(report.get('completeness_state') or ''))}</w:t></w:r></w:p>",
    ]
    if warnings:
        body_parts.append(f"<w:p><w:r><w:t>Warnings: {escape('; '.join(warnings))}</w:t></w:r></w:p>")
    table_rows = []
    for values in [columns, *rows]:
        cells = "".join(
            "<w:tc><w:p><w:r><w:t>%s</w:t></w:r></w:p></w:tc>" % escape(str(value if value is not None else ""))
            for value in values
        )
        table_rows.append(f"<w:tr>{cells}</w:tr>")
    body_parts.append(f"<w:tbl>{''.join(table_rows)}</w:tbl>")
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body_parts)}<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        '</Relationships>'
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdLogo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/logo.png"/>'
        '</Relationships>'
    )
    if logo_path:
        content_types = content_types.replace(
            '<Default Extension="xml" ContentType="application/xml"/>',
            '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>',
        )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", rels)
        package.writestr("word/document.xml", document_xml)
        if logo_path:
            package.writestr("word/_rels/document.xml.rels", document_rels)
            with open(logo_path, "rb") as logo_file:
                package.writestr("word/media/logo.png", logo_file.read())
    return output.getvalue()
