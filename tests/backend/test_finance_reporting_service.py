from datetime import date
from decimal import Decimal
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import zipfile

import pytest
from fastapi import HTTPException

import services.finance_reporting_service as finance_reporting_service
from routers.finance_reports import finance_report_catalog, finance_report_export, finance_report_payload
from routers.finance_reports import _require_report_access
from services.finance_reporting_service import (
    AGING_BUCKETS,
    REPORT_CATALOG,
    ReportWindow,
    aging_bucket_key,
    cents_to_dollars,
    get_aged_receivables_report,
    get_report_payload,
    _scheme_context_or_payload,
    _local_logo_path,
    report_to_csv,
    report_to_docx,
    report_to_pdf,
    report_to_xlsx,
)


def test_aging_bucket_key_uses_due_date_not_current_data_totals():
    as_of = date(2026, 8, 2)

    assert aging_bucket_key(date(2026, 8, 3), as_of) == "current"
    assert aging_bucket_key(date(2026, 8, 2), as_of) == "current"
    assert aging_bucket_key(date(2026, 7, 15), as_of) == "days_1_30"
    assert aging_bucket_key(date(2026, 6, 15), as_of) == "days_31_60"
    assert aging_bucket_key(date(2026, 5, 15), as_of) == "days_61_90"
    assert aging_bucket_key(date(2026, 3, 1), as_of) == "days_91_180"
    assert aging_bucket_key(date(2025, 12, 31), as_of) == "days_181_plus"


def test_aging_buckets_keep_six_tiers_not_collapsed_to_reference_five():
    """Deliberately six 30-day-wide tiers, not the reference PDF's five-column
    Current/30+/60+/90+/120+ scheme -- a 91-180-day debt must stay
    distinguishable from a 181+-day one for arrears/recovery decisions. Do not
    "simplify" this back to match the sample; that would lose information for
    no reason beyond looking more like the copied artefact."""
    labels = dict(AGING_BUCKETS)
    assert labels == {
        "current": "Current",
        "days_1_30": "1-30 Days Overdue",
        "days_31_60": "31-60 Days Overdue",
        "days_61_90": "61-90 Days Overdue",
        "days_91_180": "91-180 Days Overdue",
        "days_181_plus": "181+ Days Overdue",
    }


class _FakeAgingSessionCtx:
    """Mimics `async with async_session_context() as session:` without a real DB."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _aging_row(**overrides):
    row = {
        "levy_item_id": "li-1", "levy_run_id": "lr-1", "financial_year": "2026",
        "quarter_no": 1, "issue_date": date(2026, 1, 1), "due_date": date(2026, 3, 31),
        "status": "posted", "lot_number": "5", "unit_number": "TH005",
        "fund_code": "ADM", "fund_name": "Administrative Fund",
        "principal_cents": 50000, "gst_cents": 0, "interest_cents": 0, "recovery_costs_cents": 0,
        "current_paid_cents": 0, "paid_as_of_cents": 0, "outstanding_cents": 50000,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_aged_receivables_lot_summary_rolls_up_signed_per_owner(monkeypatch):
    """Reproduces the reference "Aged Balance Report" row grain -- one row per
    owner/lot with a signed balance (credit lowers it, not stripped out) and
    per-bucket totals -- rather than the per-levy-item detail rows alone."""
    rows = [
        _aging_row(levy_item_id="li-1", due_date=date(2026, 3, 31), outstanding_cents=50000),
        _aging_row(
            levy_item_id="li-2", fund_code="SNK", fund_name="Sinking Fund",
            due_date=date(2025, 1, 1), outstanding_cents=-2000,
        ),
    ]

    class _FakeResult:
        def __init__(self, mappings):
            self._mappings = mappings

        def __iter__(self):
            return iter(MagicMock(_mapping=m) for m in self._mappings)

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=_FakeResult(rows))

    monkeypatch.setattr(
        finance_reporting_service,
        "resolve_scheme_context",
        AsyncMock(return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "scheme_id": "22222222-2222-2222-2222-222222222222",
        }),
    )
    monkeypatch.setattr(
        finance_reporting_service,
        "async_session_context",
        MagicMock(return_value=_FakeAgingSessionCtx(fake_session)),
    )
    monkeypatch.setattr(finance_reporting_service, "set_tenant", AsyncMock())
    monkeypatch.setattr(
        finance_reporting_service,
        "get_all_unit_owners",
        AsyncMock(return_value={"TH005": {"owner_name": "Jane Owner", "co_owner_name": "John Owner"}}),
    )
    monkeypatch.setattr(
        finance_reporting_service,
        "resolve_report_window",
        AsyncMock(return_value=ReportWindow(
            financial_year="2026", label="FY 2026",
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            start_month=1, building_name="Test Building",
        )),
    )

    report = await get_aged_receivables_report(
        building_id="TEST-001", financial_year="2026", as_of=date(2026, 4, 5),
    )

    assert report["lot_summary"] == [
        {
            "lot_number": "5",
            "unit_number": "TH005",
            "owner_name": "Jane Owner & John Owner",
            "balance_cents": 48000,
            "balance": 480.0,
            "last_charge_due_date": "2026-03-31",
            "buckets": {
                "current": {"label": "Current", "amount_cents": 0, "amount": 0.0},
                "days_1_30": {"label": "1-30 Days Overdue", "amount_cents": 50000, "amount": 500.0},
                "days_31_60": {"label": "31-60 Days Overdue", "amount_cents": 0, "amount": 0.0},
                "days_61_90": {"label": "61-90 Days Overdue", "amount_cents": 0, "amount": 0.0},
                "days_91_180": {"label": "91-180 Days Overdue", "amount_cents": 0, "amount": 0.0},
                "days_181_plus": {"label": "181+ Days Overdue", "amount_cents": -2000, "amount": -20.0},
            },
            # _aging_row() doesn't set fund_type, so both rows fall into "other" --
            # see test_aged_receivables_lot_summary_splits_by_fund_type below for
            # the actual Admin-vs-Sinking split this key exists for.
            "fund_balances": {
                "other": {
                    "balance_cents": 48000,
                    "balance": 480.0,
                    "buckets": {
                        "current": {"label": "Current", "amount_cents": 0, "amount": 0.0},
                        "days_1_30": {"label": "1-30 Days Overdue", "amount_cents": 50000, "amount": 500.0},
                        "days_31_60": {"label": "31-60 Days Overdue", "amount_cents": 0, "amount": 0.0},
                        "days_61_90": {"label": "61-90 Days Overdue", "amount_cents": 0, "amount": 0.0},
                        "days_91_180": {"label": "91-180 Days Overdue", "amount_cents": 0, "amount": 0.0},
                        "days_181_plus": {"label": "181+ Days Overdue", "amount_cents": -2000, "amount": -20.0},
                    },
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_aged_receivables_lot_summary_splits_by_fund_type(monkeypatch):
    """GAP-FIN-033 Part D1: Admin Fund and Sinking/Capital Works Fund arrears are
    legally distinct debts -- lot_summary's overall balance_cents/buckets stay
    commingled (that's the right figure for "does this lot owe anything"), but
    fund_balances must split the same figures per fund_type so a strata manager
    can see each fund's arrears separately."""
    rows = [
        _aging_row(levy_item_id="li-1", fund_type="admin", due_date=date(2026, 3, 31), outstanding_cents=50000),
        _aging_row(
            levy_item_id="li-2", fund_code="SNK", fund_name="Sinking Fund", fund_type="sinking",
            due_date=date(2026, 3, 31), outstanding_cents=15000,
        ),
    ]

    class _FakeResult:
        def __init__(self, mappings):
            self._mappings = mappings

        def __iter__(self):
            return iter(MagicMock(_mapping=m) for m in self._mappings)

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=_FakeResult(rows))

    monkeypatch.setattr(
        finance_reporting_service,
        "resolve_scheme_context",
        AsyncMock(return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "scheme_id": "22222222-2222-2222-2222-222222222222",
        }),
    )
    monkeypatch.setattr(
        finance_reporting_service,
        "async_session_context",
        MagicMock(return_value=_FakeAgingSessionCtx(fake_session)),
    )
    monkeypatch.setattr(finance_reporting_service, "set_tenant", AsyncMock())
    monkeypatch.setattr(
        finance_reporting_service,
        "get_all_unit_owners",
        AsyncMock(return_value={"TH005": {"owner_name": "Jane Owner", "co_owner_name": "John Owner"}}),
    )
    monkeypatch.setattr(
        finance_reporting_service,
        "resolve_report_window",
        AsyncMock(return_value=ReportWindow(
            financial_year="2026", label="FY 2026",
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            start_month=1, building_name="Test Building",
        )),
    )

    report = await get_aged_receivables_report(
        building_id="TEST-001", financial_year="2026", as_of=date(2026, 4, 5),
    )

    fund_balances = report["lot_summary"][0]["fund_balances"]
    assert fund_balances["admin"]["balance_cents"] == 50000
    assert fund_balances["sinking"]["balance_cents"] == 15000
    # The overall (commingled) balance must still be the sum of both funds.
    assert report["lot_summary"][0]["balance_cents"] == 65000


def test_cents_to_dollars_preserves_cents_boundary_for_presentation_only():
    assert cents_to_dollars(123456) == 1234.56
    assert cents_to_dollars(Decimal("99")) == 0.99
    assert cents_to_dollars(None) == 0.0


def test_report_to_csv_serializes_empty_quality_state_instead_of_clean_zero_report():
    csv_text = report_to_csv({
        "report_id": "general_ledger",
        "financial_year": "2025",
        "completeness_state": "not_imported",
        "reconciliation_state": "source_unavailable",
        "quality_warnings": ["No PostgreSQL journal lines were found for this Levy Financial Year."],
        "rows": [],
    })

    assert "report_id,financial_year,completeness_state,reconciliation_state,warning" in csv_text
    assert "general_ledger,2025,not_imported,source_unavailable" in csv_text
    assert "No PostgreSQL journal lines" in csv_text


def test_report_to_csv_uses_row_headers_from_report_payload():
    csv_text = report_to_csv({
        "report_id": "aged_receivables",
        "financial_year": "2026",
        "rows": [
            {"lot_number": "1", "bucket": "current", "outstanding_cents": 1000},
            {"lot_number": "2", "bucket": "days_1_30", "outstanding_cents": 2500},
        ],
    })

    assert "lot_number,bucket,outstanding_cents" in csv_text
    assert "1,current,1000" in csv_text
    assert "2,days_1_30,2500" in csv_text


def test_report_export_generators_create_standard_file_signatures_without_live_data():
    report = {
        "report_id": "agm-pack",
        "title": "AGM Pack",
        "building_id": "TEST-001",
        "building_name": "Test Building",
        "financial_year": "2026",
        "financial_year_label": "FY 2026",
        "source": "contract_pending",
        "completeness_state": "contract_pending",
        "reconciliation_state": "not_applicable_until_implemented",
        "quality_warnings": ["Draft shell only."],
        "rows": [],
    }

    assert report_to_xlsx(report).startswith(b"PK")
    assert report_to_pdf(report).startswith(b"%PDF")
    docx_bytes = report_to_docx(report)
    assert docx_bytes.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as package:
        assert "[Content_Types].xml" in package.namelist()
        assert "word/document.xml" in package.namelist()
        document_xml = package.read("word/document.xml").decode("utf-8")
    assert "AGM Pack" in document_xml
    assert "Draft shell only." in document_xml


def test_report_logo_uses_building_configuration_without_east_gate_fallback():
    assert _local_logo_path({"logo_url": None}) is None
    assert _local_logo_path({"logo_url": ""}) is None


def test_planned_catalog_entries_do_not_claim_unverified_wildcard_tables():
    planned_entries = [
        report
        for report in REPORT_CATALOG.values()
        if report["status"] == "planned"
    ]

    assert planned_entries
    assert all("finance.*" not in report["source_tables"] for report in planned_entries)
    assert all("governance.*" not in report["source_tables"] for report in planned_entries)


@pytest.mark.asyncio
async def test_malformed_scheme_context_returns_quality_state(monkeypatch):
    async def malformed_scheme_context(_building_id: str):
        return {"tenant_id": "tenant-only"}

    monkeypatch.setattr(
        "services.finance_reporting_service.resolve_scheme_context",
        malformed_scheme_context,
    )

    window = ReportWindow(
        financial_year="2026",
        label="FY 2026",
        start_date=date(2025, 7, 1),
        end_date=date(2026, 6, 30),
        start_month=7,
        building_name="Test Building",
    )

    scheme, payload = await _scheme_context_or_payload(
        "TEST-001",
        window,
        "general_ledger",
    )

    assert scheme is None
    assert payload["completeness_state"] == "not_imported"
    assert payload["reconciliation_state"] == "source_unavailable"
    assert payload["quality_warnings"] == [
        "PostgreSQL scheme context is missing tenant_id or scheme_id."
    ]


def test_general_ledger_fund_filter_uses_journal_entry_fund_only():
    service_source = Path("backend/services/finance_reporting_service.py").read_text()

    assert "COALESCE(je.fund_id, f.fund_id)" not in service_source
    # CAST(:fund_id AS text) IS NULL, not bare ":fund_id IS NULL" -- GAP-FIN-033
    # Part D addendum: the bare form crashes with asyncpg.AmbiguousParameterError
    # on every real call when fund_id is None (asyncpg can't infer a NULL bind
    # param's type when it's only ever compared inside CAST(... AS uuid)).
    assert "(CAST(:fund_id AS text) IS NULL OR je.fund_id = CAST(:fund_id AS uuid))" in service_source


class _FakeMappingResult:
    """Rows consumed via dict(row._mapping) -- the main GL rows/exceptions queries."""

    def __init__(self, mappings):
        self._mappings = mappings

    def __iter__(self):
        return iter(MagicMock(_mapping=m) for m in self._mappings)


class _FakeAttrResult:
    """Rows consumed via attribute access (row.account_code, ...) -- matches
    real SQLAlchemy Row objects, used by the opening-balance and
    fund-cash-reconciliation queries."""

    def __init__(self, rows):
        self._rows = [SimpleNamespace(**r) for r in rows]

    def __iter__(self):
        return iter(self._rows)


def _gl_row(**overrides):
    row = {
        "journal_entry_id": "je-1", "journal_line_id": "jl-1", "entry_number": 1,
        "effective_on": date(2026, 2, 1), "posted_at": None, "status": "posted",
        "source_type": "payment_receipt", "source_reference": None,
        "entry_narration": "Payment", "line_narration": None,
        "account_code": "1100", "account_name": "Accounts Receivable", "account_type": "asset",
        "fund_code": "ADM", "fund_name": "Administrative Fund",
        "lot_number": "5", "unit_number": "TH005",
        "direction": "credit", "amount_cents": 10000, "gst_cents": 0,
        "approved_by": None, "posted_by": None,
        "evidence_document_id": None, "reversal_of_id": None,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_general_ledger_seeds_opening_balance_not_zero(monkeypatch):
    """GAP-FIN-033 Part D2: running_balance must start from the account's real
    pre-window position, not zero -- a $500 opening AR balance plus a $100
    in-window credit must close at $400, not -$100."""
    main_rows = [_gl_row()]
    exceptions_rows = []
    opening_rows = [{"account_code": "1100", "account_name": "Accounts Receivable", "opening_balance_cents": 50000}]
    fund_cash_rows = []

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=[
        _FakeMappingResult(main_rows),
        _FakeMappingResult(exceptions_rows),
        _FakeAttrResult(opening_rows),
        _FakeAttrResult(fund_cash_rows),
    ])

    monkeypatch.setattr(
        finance_reporting_service, "resolve_scheme_context",
        AsyncMock(return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "scheme_id": "22222222-2222-2222-2222-222222222222",
        }),
    )
    monkeypatch.setattr(
        finance_reporting_service, "async_session_context",
        MagicMock(return_value=_FakeAgingSessionCtx(fake_session)),
    )
    monkeypatch.setattr(finance_reporting_service, "set_tenant", AsyncMock())
    monkeypatch.setattr(
        finance_reporting_service, "resolve_report_window",
        AsyncMock(return_value=ReportWindow(
            financial_year="2026", label="FY 2026",
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            start_month=1, building_name="Test Building",
        )),
    )

    report = await finance_reporting_service.get_general_ledger_report(
        building_id="TEST-001", financial_year="2026",
    )

    assert report["rows"][0]["running_balance_cents"] == 40000  # 50000 opening - 10000 credit


@pytest.mark.asyncio
async def test_general_ledger_includes_fund_cash_reconciliation_and_degrades_on_db_error(monkeypatch):
    """GAP-FIN-033 Part D3/D4: the GL report surfaces the latest bank
    reconciliation per fund (ties GL to the computed trust balance), and a DB
    failure degrades to source_unavailable rather than raising past the router."""
    main_rows = [_gl_row()]
    fund_cash_rows = [{
        "fund_code": "ADM", "fund_name": "Administrative Fund",
        "bank_name": "Demo Bank", "masked_account_number": "****1234",
        "period_end": date(2026, 6, 30),
        "cashbook_balance_cents": 100000, "bank_balance_cents": 100000,
        "owner_ledger_balance_cents": 95000, "difference_cents": 0,
        "status": "reconciled", "generated_at": None,
    }]

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(side_effect=[
        _FakeMappingResult(main_rows),
        _FakeMappingResult([]),
        _FakeAttrResult([]),
        _FakeAttrResult(fund_cash_rows),
    ])

    monkeypatch.setattr(
        finance_reporting_service, "resolve_scheme_context",
        AsyncMock(return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "scheme_id": "22222222-2222-2222-2222-222222222222",
        }),
    )
    monkeypatch.setattr(
        finance_reporting_service, "async_session_context",
        MagicMock(return_value=_FakeAgingSessionCtx(fake_session)),
    )
    monkeypatch.setattr(finance_reporting_service, "set_tenant", AsyncMock())
    monkeypatch.setattr(
        finance_reporting_service, "resolve_report_window",
        AsyncMock(return_value=ReportWindow(
            financial_year="2026", label="FY 2026",
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            start_month=1, building_name="Test Building",
        )),
    )

    report = await finance_reporting_service.get_general_ledger_report(
        building_id="TEST-001", financial_year="2026",
    )
    assert report["fund_cash_reconciliation"] == [{
        "fund_code": "ADM", "fund_name": "Administrative Fund",
        "bank_name": "Demo Bank", "masked_account_number": "****1234",
        "period_end": "2026-06-30",
        "cashbook_balance_cents": 100000, "cashbook_balance": 1000.0,
        "bank_balance_cents": 100000, "bank_balance": 1000.0,
        "owner_ledger_balance_cents": 95000, "owner_ledger_balance": 950.0,
        "difference_cents": 0, "difference": 0.0,
        "status": "reconciled", "generated_at": None,
    }]

    # Now confirm graceful degradation: a DB error must not raise past this
    # function -- it must return a source_unavailable payload instead.
    fake_session_failing = MagicMock()
    fake_session_failing.execute = AsyncMock(side_effect=RuntimeError("connection lost"))
    monkeypatch.setattr(
        finance_reporting_service, "async_session_context",
        MagicMock(return_value=_FakeAgingSessionCtx(fake_session_failing)),
    )

    failed_report = await finance_reporting_service.get_general_ledger_report(
        building_id="TEST-001", financial_year="2026",
    )
    assert failed_report["reconciliation_state"] == "source_unavailable"
    assert failed_report["rows"] == []


def test_aged_receivables_uses_receipt_allocations_as_of_not_current_paid_snapshot():
    service_source = Path("backend/services/finance_reporting_service.py").read_text()

    assert "finance.receipt_allocations" in service_source
    assert "r.received_on <= :as_of" in service_source
    assert "li.paid_cents AS current_paid_cents" in service_source
    assert "- COALESCE(alloc.allocated_cents, 0)" in service_source
    assert " - li.paid_cents) AS outstanding_cents" not in service_source


def test_aged_receivables_excludes_future_issued_runs_but_keeps_current_bucket():
    service_source = Path("backend/services/finance_reporting_service.py").read_text()

    assert "lr.due_date <= :as_of" not in service_source
    assert "(lr.issue_date IS NULL OR lr.issue_date <= :as_of)" in service_source


@pytest.mark.parametrize("role", ["super_admin", "strata_admin", "strata_manager", "admin_staff", "ec_member"])
def test_report_access_allows_canonical_management_and_committee_roles(role):
    _require_report_access({"role": role})


@pytest.mark.parametrize("role", ["owner", "tenant", "real_estate_agent", "service_provider", "guest", "building_manager"])
def test_report_access_denies_unscoped_or_noncanonical_roles(role):
    with pytest.raises(HTTPException) as exc_info:
        _require_report_access({"role": role})

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "expected_external_reports"),
    [
        ("admin_staff", set()),
        ("ec_member", {"financial-statement"}),
        ("strata_manager", {"financial-statement", "gst-bas-statement"}),
    ],
)
async def test_report_catalog_filters_external_modules_to_their_real_route_access(
    monkeypatch,
    role,
    expected_external_reports,
):
    async def feature_enabled(_user, _feature_key):
        return True

    monkeypatch.setattr("routers.finance_reports.get_effective_feature_access", feature_enabled)

    catalog = await finance_report_catalog(
        building_id="TEST-001",
        current_user={"role": role, "building_id": "TEST-001", "is_approved": True},
    )
    report_types = {report["report_type"] for report in catalog["reports"]}

    assert {"aged-receivables", "general-ledger"}.issubset(report_types)
    assert expected_external_reports.issubset(report_types)
    assert ("financial-statement" in report_types) is ("financial-statement" in expected_external_reports)
    assert ("gst-bas-statement" in report_types) is ("gst-bas-statement" in expected_external_reports)


@pytest.mark.asyncio
async def test_report_catalog_filters_financial_statement_when_target_feature_disabled(monkeypatch):
    async def feature_disabled(_user, _feature_key):
        return False

    monkeypatch.setattr("routers.finance_reports.get_effective_feature_access", feature_disabled)

    catalog = await finance_report_catalog(
        building_id="TEST-001",
        current_user={"role": "ec_member", "building_id": "TEST-001", "is_approved": True},
    )
    report_types = {report["report_type"] for report in catalog["reports"]}

    assert "financial-statement" not in report_types


@pytest.mark.asyncio
async def test_report_catalog_filters_financial_statement_when_building_scope_denied(monkeypatch):
    async def feature_enabled(_user, _feature_key):
        return True

    monkeypatch.setattr("routers.finance_reports.get_effective_feature_access", feature_enabled)

    catalog = await finance_report_catalog(
        building_id="TEST-001",
        current_user={"role": "ec_member", "building_id": "OTHER-001", "is_approved": True},
    )
    report_types = {report["report_type"] for report in catalog["reports"]}

    assert "financial-statement" not in report_types


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "report_type"),
    [
        ("admin_staff", "financial-statement"),
        ("admin_staff", "gst-bas-statement"),
        ("ec_member", "gst-bas-statement"),
    ],
)
async def test_report_payload_rejects_hidden_external_module_links(monkeypatch, role, report_type):
    async def feature_enabled(_user, _feature_key):
        return True

    monkeypatch.setattr("routers.finance_reports.get_effective_feature_access", feature_enabled)

    with pytest.raises(HTTPException) as exc_info:
        await finance_report_payload(
            report_type=report_type,
            financial_year="2026",
            building_id="TEST-001",
            current_user={"role": role, "building_id": "TEST-001", "is_approved": True},
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_export_route_rejects_format_not_enabled_for_report_type():
    with pytest.raises(HTTPException) as exc_info:
        await finance_report_export(
            report_type="agm-pack",
            export_format="csv",
            financial_year="2026",
            building_id="TEST-001",
            current_user={"role": "strata_manager"},
        )

    assert exc_info.value.status_code == 400
    assert "CSV export is not enabled for AGM Pack" in exc_info.value.detail


@pytest.mark.asyncio
async def test_financial_statement_payload_links_to_existing_module_not_a_reimplementation(monkeypatch):
    """financial-statement/gst-bas-statement are existing, working StrataOS modules
    (report_service.generate_full_report / gst_bas._generate_bas_pdf) -- the workbench
    must link to them, not silently fall through to the contract_pending shell path."""
    monkeypatch.setattr(
        finance_reporting_service,
        "resolve_report_window",
        AsyncMock(return_value=ReportWindow(
            financial_year="2026", label="FY 2026",
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            start_month=1, building_name="Test Building",
        )),
    )

    report = await get_report_payload(
        report_type="financial-statement", building_id="TEST-001", financial_year="2026",
    )

    assert report["completeness_state"] == "external_module"
    assert report["source"] == "existing_module"
    assert report["external_link"] == "/finance/report/2026"
    assert report["rows"] == []


@pytest.mark.asyncio
async def test_export_route_rejects_external_module_report_types(monkeypatch):
    """CSV/XLSX/PDF export for financial-statement/gst-bas-statement must not run the
    generic rows-based exporters -- there are no rows, only a link to the real module."""
    async def feature_enabled(_user, _feature_key):
        return True

    monkeypatch.setattr("routers.finance_reports.get_effective_feature_access", feature_enabled)

    with pytest.raises(HTTPException) as exc_info:
        await finance_report_export(
            report_type="financial-statement",
            export_format="pdf",
            financial_year="2026",
            building_id="TEST-001",
            current_user={"role": "strata_manager", "building_id": "TEST-001", "is_approved": True},
        )

    assert exc_info.value.status_code == 400
    assert "existing StrataOS module" in exc_info.value.detail
