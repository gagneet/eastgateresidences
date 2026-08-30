"""Unit tests for C4 — onboarding CSV import endpoints.

Tests:
  - test_import_historical_financials_round_trips
  - test_import_owner_transfers_creates_bitemporal_records
  - test_import_owner_transfers_rejects_overlapping_periods
  - test_import_opening_balances_persists_to_step_data
  - test_finalize_runs_imports_in_order_when_data_present

All DB and MongoDB interactions are mocked so no live database is required.
"""
from __future__ import annotations

import io
import json
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport

import server
from server import app, get_current_user

# ──────────────────────────────────────────────────────────────────────────────
# Fixtures and helpers
# ──────────────────────────────────────────────────────────────────────────────

SESSION_ID = str(uuid.uuid4())
SCHEME_ID = str(uuid.uuid4())
TENANT_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
SCHEME_NUMBER = "13195"


def _sa_user() -> dict:
    return {
        "id": USER_ID,
        "full_name": "Test SA",
        "role": "super_admin",
        "tenant_id": TENANT_ID,
        "is_approved": True,
    }


def _csv_bytes(header: str, rows: list[str]) -> bytes:
    return ("\n".join([header] + rows) + "\n").encode("utf-8")


def _make_pg_session(session_row=None, step_data: dict | None = None):
    """Build a fake async Postgres session context that returns `session_row`
    for the SELECT query and succeeds for UPDATE/INSERT.
    """
    step_data = step_data or {}
    default_session_row = (
        SESSION_ID, SCHEME_ID, TENANT_ID, "in_progress", step_data, SCHEME_NUMBER
    )
    row = session_row or default_session_row

    fetch_result = MagicMock()
    fetch_result.fetchone = MagicMock(return_value=row)
    fetch_result.fetchall = MagicMock(return_value=[])
    fetch_result.scalar = MagicMock(return_value=None)

    pg = AsyncMock()
    pg.execute = AsyncMock(return_value=fetch_result)
    pg.__aenter__ = AsyncMock(return_value=pg)
    pg.__aexit__ = AsyncMock(return_value=False)
    return pg


# ──────────────────────────────────────────────────────────────────────────────
# Minimal CSV data mirrors the East Gate import package format
# ──────────────────────────────────────────────────────────────────────────────

QUARTERLY_LEVIES_CSV = _csv_bytes(
    "year,quarter,due_date,period_start,period_end,lot_number,unit_number,"
    "uoe,total_uoe,admin_levy_amount,sinking_levy_amount,total_levy_amount,"
    "admin_levy_amount_excl_gst,sinking_levy_amount_excl_gst,notes",
    [
        "2025,Q1,2025-03-31,2025-01-01,2025-03-31,71,TH071,"
        "160,10000,1500.00,500.00,2000.00,1363.64,454.55,",
        "2025,Q1,2025-03-31,2025-01-01,2025-03-31,72,TH072,"
        "160,10000,1500.00,500.00,2000.00,1363.64,454.55,",
    ],
)

ADMIN_FUND_CSV = _csv_bytes(
    "year,period_start,period_end,budget_proposed,levy_income,other_income,"
    "total_income,total_expenses_audited,total_expenses_transactions,"
    "reconciliation_note,opening_balance,closing_balance,surplus_deficit,"
    "data_origin,notes",
    [
        "2025,2025-01-01,2025-12-31,140000.0,3000.00,0.0,3000.00,"
        "2800.00,0.0,no_transaction_data,0.0,200.00,200.00,user_provided,",
    ],
)

SINKING_FUND_CSV = _csv_bytes(
    "year,period_start,period_end,budget_proposed,levy_income,other_income,"
    "total_income,total_expenses_audited,total_expenses_transactions,"
    "reconciliation_note,opening_balance,closing_balance,surplus_deficit,"
    "data_origin,notes",
    [
        "2025,2025-01-01,2025-12-31,70000.0,1000.00,0.0,1000.00,"
        "800.00,0.0,no_transaction_data,0.0,200.00,200.00,user_provided,",
    ],
)

ARREARS_CSV = _csv_bytes(
    "lot_number,unit_number,admin_arrears,sinking_arrears,total_arrears,"
    "admin_closing_balance,sinking_closing_balance,data_source,notes",
    [
        "71,TH071,1580.71,0.0,1580.71,-1580.71,0.0,ledger,",
        "72,TH072,0.0,0.0,0.0,0.0,0.0,ledger,",
    ],
)

OUTSTANDING_CSV = _csv_bytes(
    "lot_number,unit_number,admin_outstanding,sinking_outstanding,"
    "total_outstanding,admin_levy_q1_2026,sinking_levy_q1_2026,as_at_date,"
    "data_source,notes",
    [
        "71,TH071,0.0,0.0,0.0,0.0,0.0,2026-06-01,nil,",
        "72,TH072,0.0,0.0,0.0,0.0,0.0,2026-06-01,nil,",
    ],
)

TRANSFERS_CSV = _csv_bytes(
    "lot_number,unit_number,effective_from,effective_to,new_owner_name,"
    "previous_owner_name,transfer_date,sold_price_aud,source,confidence,notes",
    [
        "71,TH071,2023-03-27,,Lorna Stansfield,Jessamy Perkins,"
        "2023-03-27,731000,public_transfer_records_verified,High,",
        "72,TH072,2025-07-04,,Jake Paul Hope & Tess Tiatia,"
        "Tejas Joshi & Salas Kodape,2025-07-04,750000,public,High,",
    ],
)

TRANSFERS_OVERLAPPING_CSV = _csv_bytes(
    "lot_number,unit_number,effective_from,effective_to,new_owner_name,"
    "previous_owner_name,transfer_date,sold_price_aud,source,confidence,notes",
    [
        # Both rows have open-ended effective_to for the SAME lot → overlap
        "71,TH071,2023-01-01,,Owner A,Prev Owner,2023-01-01,500000,test,High,",
        "71,TH071,2024-01-01,,Owner B,Owner A,2024-01-01,600000,test,High,",
        # ^ 2023-01-01 to null overlaps with 2024-01-01 to null
    ],
)

OPENING_BALANCES_CSV = _csv_bytes(
    "fund_type,opening_balance_amount,as_at_date,bsb,account_number,"
    "account_name,evidence_source,notes",
    [
        "Administrative Fund,17778.11,2026-04-06,182-266,260611108,"
        "The Proprietors,strata_web_statement_manual,",
        "Sinking Fund,156351.48,2026-04-06,182-266,260611108,"
        "The Proprietors,strata_web_statement_manual,",
    ],
)


# ──────────────────────────────────────────────────────────────────────────────
# Test 1 — import-historical-financials round-trips
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_import_historical_financials_round_trips():
    """POST import-historical-financials writes to MongoDB and patches step_data."""
    app.dependency_overrides[get_current_user] = lambda: _sa_user()

    mock_pg = _make_pg_session()

    # Build a mock MongoDB db with async collection methods
    mock_collection = MagicMock()
    mock_collection.delete_many = AsyncMock(return_value=None)
    mock_collection.insert_many = AsyncMock(return_value=None)

    mock_mongo_db = SimpleNamespace(
        historical_annual_levies=mock_collection,
        historical_levy_issuances=mock_collection,
        historical_financial_snapshots=mock_collection,
    )

    try:
        with (
            patch("routers.onboarding.async_session_context", return_value=mock_pg),
            patch("routers.onboarding.set_tenant", new_callable=AsyncMock),
            patch("routers.onboarding.db", mock_mongo_db),
            patch("routers.onboarding.set_ctx_building_id"),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    f"/api/onboarding/scheme/{SESSION_ID}/import-historical-financials",
                    files={
                        "quarterly_levies": ("06.csv", QUARTERLY_LEVIES_CSV, "text/csv"),
                        "admin_fund_summary": ("07.csv", ADMIN_FUND_CSV, "text/csv"),
                        "sinking_fund_summary": ("08.csv", SINKING_FUND_CSV, "text/csv"),
                        "arrears": ("09.csv", ARREARS_CSV, "text/csv"),
                        "outstanding": ("10.csv", OUTSTANDING_CSV, "text/csv"),
                    },
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["building_id"] == SCHEME_NUMBER
    assert data["annual_levy_docs"] == 2  # 1 admin + 1 sinking for 2025
    assert data["levy_issuance_docs"] == 2  # 2 lot rows for Q1 2025
    assert data["arrears_docs"] == 2
    assert data["outstanding_docs"] == 2
    # Verify MongoDB writes were called
    assert mock_collection.insert_many.call_count >= 4


# ──────────────────────────────────────────────────────────────────────────────
# Test 2 — import-owner-transfers creates bitemporal records
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_import_owner_transfers_creates_bitemporal_records():
    """import-owner-transfers writes ownership_periods for each transfer + predecessor."""
    app.dependency_overrides[get_current_user] = lambda: _sa_user()

    # Lots lookup returns lot_id for lot_number 71 and 72
    lot_71 = str(uuid.uuid4())
    lot_72 = str(uuid.uuid4())

    lots_fetch_result = MagicMock()
    lots_fetch_result.fetchall = MagicMock(return_value=[("71", lot_71), ("72", lot_72)])

    party_result = MagicMock()
    party_result.fetchone = MagicMock(return_value=None)  # party not found → create
    party_result.scalar = MagicMock(return_value=str(uuid.uuid4()))

    # Sequence of execute calls: session fetch, lots fetch, then multiple party / period inserts
    execute_responses = [
        # 1. _resolve_session query
        _make_pg_session().execute.return_value,
    ]

    call_count = 0

    async def _mock_execute(sql_text, params=None):
        nonlocal call_count
        call_count += 1
        sql = str(sql_text) if hasattr(sql_text, "text") else str(sql_text)
        # Session resolve SELECT
        if "onboarding_sessions" in sql and call_count == 1:
            r = MagicMock()
            r.fetchone = MagicMock(return_value=(
                SESSION_ID, SCHEME_ID, TENANT_ID, "in_progress", {}, SCHEME_NUMBER
            ))
            return r
        # Lots lookup
        if "core.lots" in sql and "SELECT" in sql:
            r = MagicMock()
            r.fetchall = MagicMock(return_value=[("71", lot_71), ("72", lot_72)])
            return r
        # Party lookup (find)
        if "core.parties" in sql and "lower(legal_name)" in sql:
            r = MagicMock()
            r.fetchone = MagicMock(return_value=None)
            return r
        # Party insert
        if "core.parties" in sql and "INSERT" in sql:
            r = MagicMock()
            r.scalar = MagicMock(return_value=str(uuid.uuid4()))
            return r
        # Ownership period insert + step_data patch + set_config
        r = MagicMock()
        r.fetchone = MagicMock(return_value=None)
        r.scalar = MagicMock(return_value=None)
        return r

    mock_pg = AsyncMock()
    mock_pg.execute = _mock_execute
    mock_pg.__aenter__ = AsyncMock(return_value=mock_pg)
    mock_pg.__aexit__ = AsyncMock(return_value=False)

    try:
        with (
            patch("routers.onboarding.async_session_context", return_value=mock_pg),
            patch("routers.onboarding.set_tenant", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    f"/api/onboarding/scheme/{SESSION_ID}/import-owner-transfers",
                    files={"transfers_file": ("05.csv", TRANSFERS_CSV, "text/csv")},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # 2 transfers = 2 new owners + 2 predecessor records = 4 ownership_periods inserted
    assert data["ownership_periods_inserted"] == 4
    assert data["lots_skipped"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# Test 3 — import-owner-transfers rejects overlapping periods
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_import_owner_transfers_rejects_overlapping_periods():
    """Overlapping date periods for the same lot must return 422."""
    app.dependency_overrides[get_current_user] = lambda: _sa_user()

    mock_pg = _make_pg_session()

    try:
        with (
            patch("routers.onboarding.async_session_context", return_value=mock_pg),
            patch("routers.onboarding.set_tenant", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    f"/api/onboarding/scheme/{SESSION_ID}/import-owner-transfers",
                    files={"transfers_file": ("05.csv", TRANSFERS_OVERLAPPING_CSV, "text/csv")},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422, resp.text
    body = resp.json()
    msg = body.get("detail") or body.get("error", {}).get("message", "")
    assert "Overlapping" in msg


# ──────────────────────────────────────────────────────────────────────────────
# Test 4 — import-opening-balances persists to step_data
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_import_opening_balances_persists_to_step_data():
    """import-opening-balances returns parsed balances and patches step_data."""
    app.dependency_overrides[get_current_user] = lambda: _sa_user()

    patched_data: dict = {}

    async def _mock_execute(sql_text, params=None):
        sql = str(sql_text)
        if "onboarding_sessions" in sql and "SELECT" in sql:
            r = MagicMock()
            r.fetchone = MagicMock(return_value=(
                SESSION_ID, SCHEME_ID, TENANT_ID, "in_progress", {}, SCHEME_NUMBER
            ))
            return r
        if "UPDATE core.onboarding_sessions" in sql and "step_data" in sql:
            # Capture what was written
            if params and "patch" in params:
                patched_data.update(json.loads(params["patch"]))
        r = MagicMock()
        r.fetchone = MagicMock(return_value=None)
        return r

    mock_pg = AsyncMock()
    mock_pg.execute = _mock_execute
    mock_pg.__aenter__ = AsyncMock(return_value=mock_pg)
    mock_pg.__aexit__ = AsyncMock(return_value=False)

    try:
        with (
            patch("routers.onboarding.async_session_context", return_value=mock_pg),
            patch("routers.onboarding.set_tenant", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    f"/api/onboarding/scheme/{SESSION_ID}/import-opening-balances",
                    files={"opening_balances_file": ("11.csv", OPENING_BALANCES_CSV, "text/csv")},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["funds_captured"] == 2

    balances = data["opening_balances"]
    fund_types = {b["fund_type"] for b in balances}
    assert fund_types == {"admin", "sinking"}

    admin = next(b for b in balances if b["fund_type"] == "admin")
    assert admin["opening_balance_amount"] == pytest.approx(17778.11)
    assert admin["opening_balance_cents"] == 1777811
    assert admin["as_at_date"] == "2026-04-06"

    # Verify step_data was patched with opening_balances key
    assert "opening_balances" in patched_data
    assert len(patched_data["opening_balances"]) == 2


# ──────────────────────────────────────────────────────────────────────────────
# Test 5 — finalize runs imports in order when data present
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_runs_imports_in_order_when_data_present():
    """finalize with import step_data returns import_based=True and defers genesis journals."""
    app.dependency_overrides[get_current_user] = lambda: _sa_user()

    opening_balances = [
        {
            "fund_type": "admin",
            "opening_balance_cents": 1777811,
            "opening_balance_amount": 17778.11,
            "as_at_date": "2026-04-06",
        },
        {
            "fund_type": "sinking",
            "opening_balance_cents": 15635148,
            "opening_balance_amount": 156351.48,
            "as_at_date": "2026-04-06",
        },
    ]
    historical_financials_summary = {
        "annual_levy_docs": 2,
        "levy_issuance_docs": 10,
        "imported_at": "2026-05-04T00:00:00+00:00",
    }
    step_data_with_imports = {
        "opening_balances": opening_balances,
        "historical_financials_summary": historical_financials_summary,
    }

    scheme_name = "East Gate Residences"
    scheme_number = "UP13195"
    call_count = 0

    async def _mock_execute(sql_text, params=None):
        nonlocal call_count
        call_count += 1
        sql = str(sql_text)
        # Bypass SET for cross-tenant lookup
        if "set_config" in sql:
            r = MagicMock(); r.fetchone = MagicMock(return_value=None); return r
        # Session + scheme join SELECT
        if "onboarding_sessions" in sql and "scheme_name" in sql:
            r = MagicMock()
            r.fetchone = MagicMock(return_value=(
                SESSION_ID, str(uuid.uuid4()), TENANT_ID,
                step_data_with_imports, scheme_name, scheme_number,
            ))
            return r
        # Final UPDATE to mark completed
        if "UPDATE core.onboarding_sessions" in sql and "'completed'" in sql:
            r = MagicMock()
            r.fetchone = MagicMock(return_value=(SESSION_ID, str(uuid.uuid4()), TENANT_ID))
            return r
        r = MagicMock()
        r.fetchone = MagicMock(return_value=None)
        r.fetchall = MagicMock(return_value=[])
        return r

    mock_session = AsyncMock()
    mock_session.execute = _mock_execute
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    cutover_record = {
        "canonical_cutover_enabled": True,
        "cutover_recorded_at": "2026-05-04T00:00:00+00:00",
        "building_id": "13195",
        "enabled_feature_keys": ["financial_integration_layer_v2", "financial_pg_writes_enabled"],
        "funds": [],
    }

    try:
        with (
            patch("routers.onboarding.async_session_context", return_value=mock_session),
            patch("routers.onboarding.set_tenant", new_callable=AsyncMock),
            patch(
                "routers.onboarding.post_import_based_genesis_cutover",
                new=AsyncMock(return_value=([MagicMock(), MagicMock()], cutover_record)),
            ) as mock_post_cutover,
            patch("routers.onboarding.record_cutover_in_session_step_data", new=AsyncMock()) as mock_record_cutover,
            patch("routers.onboarding.send_email_async", new_callable=AsyncMock),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    f"/api/onboarding/scheme/{SESSION_ID}/finalize",
                    json={"send_owner_invites": False, "send_ec_invites": False},
                )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["genesis_journals_posted"] == 2
    assert data["import_based"] is True
    assert data["opening_balances_captured"] == 2
    assert "historical_financials_summary" in data
    assert "canonical financial cutover toggles enabled" in data.get("note", "").lower()
    assert data["cutover"] == cutover_record
    mock_post_cutover.assert_awaited_once()
    mock_record_cutover.assert_awaited_once()


# ── XLSX intake (added with the CSV/XLSX upload boundary) ─────────────────────

class TestXlsxOnboardingUploads:
    """The onboarding import endpoints accept .xlsx as well as .csv.

    Managers export from Excel far more often than they hand-write CSV; before
    the shared tabular boundary an .xlsx was rejected twice over (the real MIME
    type was off the allowlist, and the parser did ``content.decode("utf-8")``).
    """

    @staticmethod
    def _xlsx(rows):
        import io as _io

        from openpyxl import Workbook

        wb = Workbook()
        for row in rows:
            wb.active.append(row)
        buf = _io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    async def test_parse_csv_helper_reads_an_xlsx_upload(self):
        from routers.onboarding import _parse_csv

        payload = self._xlsx([
            ["fund_type", "opening_balance_amount", "as_at_date"],
            ["admin", 59325.76, "2026-01-01"],
        ])

        class _Upload:
            filename = "opening_balances.xlsx"
            content_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            async def read(self):
                return payload

        rows = await _parse_csv(
            _Upload(), required_columns=["fund_type", "opening_balance_amount"]
        )
        assert rows == [{
            "fund_type": "admin",
            "opening_balance_amount": "59325.76",
            "as_at_date": "2026-01-01",
        }]

    async def test_missing_required_column_still_422s_for_xlsx(self):
        from fastapi import HTTPException

        from routers.onboarding import _parse_csv

        payload = self._xlsx([["fund_type"], ["admin"]])

        class _Upload:
            filename = "wrong.xlsx"
            content_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            async def read(self):
                return payload

        with pytest.raises(HTTPException) as exc:
            await _parse_csv(
                _Upload(), required_columns=["fund_type", "opening_balance_amount"]
            )
        assert exc.value.status_code == 422
        assert "opening_balance_amount" in str(exc.value.detail)
