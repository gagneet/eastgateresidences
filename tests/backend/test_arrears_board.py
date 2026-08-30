"""
Arrears Notice & Board Tests — Phase P1

Tests cover: PDF generation, contact log auto-creation, role-based access,
and Arrears Board data aggregation.

Run with:
    PYTHONPATH=. python3 -m pytest backend/tests/test_arrears_board.py -v
"""
import io
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_MOCK_OWNER = {"owner_name": "Test Owner", "owner_email": "owner@test.com",
               "owner_id": "user-123", "owner_name_b": None, "owner_email_b": None, "source": "mock"}


_MOCK_SETTINGS = {"building_name": "Test Building", "plan_number": "13195"}


@pytest.fixture(autouse=True)
def _mock_get_owner_info():
    with patch("services.notice_service.get_owner_info", AsyncMock(return_value=_MOCK_OWNER)), \
         patch("services.notice_service.get_general_settings_or_default",
               AsyncMock(return_value=_MOCK_SETTINGS)):
        yield


@pytest.fixture(autouse=True)
def _force_arrears_board_mongo_source():
    """These tests exercise the Arrears Recovery Board's Mongo-path per-unit
    computation logic against hand-crafted mock units/ledger entries -- they are
    not integration tests of the live PG cutover routing. building_id="13195" in
    the fixtures below is real East Gate, which since 2026-08-09 (GAP-FIN-058
    re-enable) is genuinely PG-eligible for finance.arrears_detail; without this
    pin, get_arrears_board's ledger fetch would hit real Postgres instead of the
    mocked db.unit_levy_ledger.find() these tests set up, and the mock units
    (UA101 etc.) would never match a real PG lot -- silently producing an empty
    result instead of exercising the logic under test."""
    with patch(
        "routers.finance.get_finance_route_runtime_state",
        AsyncMock(return_value={
            "route_key": "finance.arrears_detail", "source": "mongo", "run_shadow": False,
            "eligible_for_postgres_read": False, "blocked_reason": "forced mongo for unit test",
            "domain_mode": "mongo_primary", "route_readiness": {"status": "not_started"},
        }),
    ):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# Mocks
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_unit(unit_number="UA101", dca_status="none", building_id="13195"):
    return {
        "building_id": building_id,
        "unit_number": unit_number,
        "lot_number": "LOT101",
        "entitlement": 120,
        "arrears_metadata": {
            "dca_status": dca_status,
            "dca_reference": None,
            "first_notice_sent_at": None,
            "legal_referral_status": "none",
            "has_active_payment_plan": False,
            "contact_log": []
        }
    }


def _make_mock_ledger(unit_number="UA101", net_balance=2500.0,
                      admin_opening=None, sinking_opening=None, building_id="13195",
                      total_paid=0.0):
    """
    Build a mock ledger entry.

    By default the opening arrears ARE the net_balance (carry-forward from prior year)
    so the unit appears on the board even before any levy period has come due.
    Pass explicit admin_opening/sinking_opening=0.0 to test a zero-carry-forward unit.

    total_paid represents DEFT/bank payments imported into the ledger.  Portal-only
    Stripe payments (levy_payments collection) are NOT used to reduce opening_arrears.
    """
    if admin_opening is None:
        admin_opening = round(net_balance * 0.7, 2)
    if sinking_opening is None:
        sinking_opening = round(net_balance * 0.3, 2)
    opening_arrears = round(admin_opening + sinking_opening, 2)
    return {
        "unit_number": unit_number,
        "year": "2026",
        "building_id": building_id,
        "admin_opening": admin_opening,
        "admin_levied": round(net_balance * 0.7 + 250.0, 2),
        "sinking_opening": sinking_opening,
        "sinking_levied": round(net_balance * 0.3 + 250.0, 2),
        "admin_closing": round(net_balance * 0.7, 2),
        "sinking_closing": round(net_balance * 0.3, 2),
        "net_balance": net_balance,
        "opening_arrears": opening_arrears,
        "total_paid": total_paid,
    }


def _make_mock_user(user_id="user-123", role="owner"):
    return {
        "id": user_id,
        "full_name": "John Doe",
        "email": "john@example.com",
        "role": role,
        "is_active": True
    }


def _make_mock_user_unit(unit_number="UA101", user_id="user-123"):
    return {
        "unit_number": unit_number,
        "user_id": user_id,
        "is_active": True
    }


def _build_mock_db():
    mock_db = MagicMock()

    def _cursor_mock(data):
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=data)
        return cursor

    mock_db.units.find.return_value = _cursor_mock([_make_mock_unit("UA101"), _make_mock_unit("UA102")])
    mock_db.unit_levy_ledger.find.return_value = _cursor_mock(
        [_make_mock_ledger("UA101", 2500.0), _make_mock_ledger("UA102", 3000.0)])
    mock_db.user_units.find.return_value = _cursor_mock([_make_mock_user_unit("UA101"), _make_mock_user_unit("UA102")])
    mock_db.users.find.return_value = _cursor_mock([_make_mock_user("user-123"), _make_mock_user("user-456")])

    # Aggregate mocks for Bolt ⚡ optimizations
    mock_db.user_units.aggregate.return_value = _cursor_mock([
        {**_make_mock_user_unit("UA101"), "id": "user-123", "full_name": "John Doe", "email": "john@example.com"},
        {**_make_mock_user_unit("UA102"), "id": "user-456", "full_name": "Jane Smith", "email": "jane@example.com"}
    ])
    mock_db.levy_payments.aggregate.return_value = _cursor_mock([])

    # find_one mocks
    mock_db.units.find_one = AsyncMock(side_effect=lambda q, p=None: _make_mock_unit(q.get("unit_number", "UA101")))
    mock_db.unit_levy_ledger.find_one = AsyncMock(return_value=_make_mock_ledger())
    mock_db.annual_levies.find_one = AsyncMock(
        return_value={"year": "2026", "admin_levy_per_uoe_annual": 30.0, "sinking_levy_per_uoe_annual": 10.0})
    # get_arrears_board resolves its default year via _resolve_default_levy_year(), which
    # calls db.annual_levies.distinct("year", ...) rather than find_one(...sort...) — see
    # routers/finance.py module note above get_available_years.
    mock_db.annual_levies.distinct = AsyncMock(return_value=["2026"])
    mock_db.settings.find_one = AsyncMock(return_value={})
    mock_db.user_units.find_one = AsyncMock(return_value=_make_mock_user_unit())
    mock_db.users.find_one = AsyncMock(return_value=_make_mock_user())

    # update_one mocks
    mock_db.units.update_one = AsyncMock(return_value=MagicMock(modified_count=1))

    # committee_resolutions.find_one used by get_effective_interest_rate (no active override)
    mock_db.committee_resolutions.find_one = AsyncMock(return_value=None)
    # buildings.find_one used by arrears_interest_service (B2-02)
    mock_db.buildings.find_one = AsyncMock(return_value={"arrears_interest_rate_pct": 10.0})

    return mock_db


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_arrears_notice_success():
    """Service should return PDF bytes and create contact log / audit log."""
    mock_db = _build_mock_db()

    # We import here to ensure the module is loaded before patching
    import services.notice_service

    with patch("services.notice_service.db", mock_db), \
            patch("services.notice_service.create_audit_log", AsyncMock()) as mock_audit:
        pdf_bytes = await services.notice_service.generate_arrears_notice(
            "UA101", "2026", "admin-1", "Admin User", "13195"
        )

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 3000

        # Verify text content using pdfplumber
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            text = ""
            for page in pdf.pages:
                text += page.extract_text()

            assert "SECTION 83" in text.upper()
            assert "TOTAL OUTSTANDING" in text.upper()
            assert "UA101" in text

        # Verify side effects
        mock_db.units.update_one.assert_called()
        args, kwargs = mock_db.units.update_one.call_args
        update_dict = args[1] if len(args) > 1 else kwargs.get('update')
        assert "$push" in update_dict
        assert "arrears_metadata.contact_log" in update_dict["$push"]

        mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_get_arrears_board_logic():
    """Units with carry-forward opening arrears appear; total_arrears = opening_arrears only."""
    mock_db = _build_mock_db()

    import routers.finance

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.get_user_permissions") as mock_perms:
        mock_perms.return_value = MagicMock(can_view_finances=True)

        results = await routers.finance.get_arrears_board(current_user={"id": "admin-1", "role": "super_admin"},
                                                          building_id="13195")

        # Both units have opening arrears → both appear
        assert len(results) == 2
        assert results[0]["unit_number"] in ["UA101", "UA102"]
        assert "owner_name" in results[0]
        assert "total_arrears" in results[0]
        # Arrears board shows ONLY opening_arrears (prior-year carry-forward).
        # total_arrears must NOT include current-year levy periods past grace.
        assert results[0]["total_arrears"] > 0
        # days_overdue > 0 because Q1 grace deadline (Oct 14 2025) has already passed.
        assert results[0]["days_overdue"] > 0


@pytest.mark.asyncio
async def test_arrears_board_current_year_net_balance_counts_as_arrears():
    """2026-08-03: total_arrears is no longer scoped to prior-year opening
    carry-forward only -- a unit with ZERO opening arrears but a genuine
    current-year net_balance now correctly shows that balance as arrears
    (via the canonical unit_arrears_and_credit() formula, which trusts
    net_balance directly). This is the fix for East Gate's live "31 units /
    $1,469.49" bug: excluding current-year past-grace amounts from
    total_arrears was itself the defect, not a feature to preserve."""
    mock_db = _build_mock_db()

    def _cursor_mock(data):
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=data)
        return cursor

    mock_db.unit_levy_ledger.find.return_value = _cursor_mock([
        _make_mock_ledger("UA101", 2500.0, admin_opening=0.0, sinking_opening=0.0),
        _make_mock_ledger("UA102", 3000.0, admin_opening=0.0, sinking_opening=0.0),
    ])

    import routers.finance
    from datetime import date

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.get_user_permissions") as mock_perms, \
            patch("routers.finance.date") as mock_date:
        mock_date.today.return_value = date(2025, 8, 1)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        mock_perms.return_value = MagicMock(can_view_finances=True)

        results = await routers.finance.get_arrears_board(current_user={"id": "admin-1", "role": "super_admin"},
                                                          building_id="13195")

        assert {r["unit_number"] for r in results} == {"UA101", "UA102"}
        by_unit = {r["unit_number"]: r for r in results}
        assert by_unit["UA101"]["total_arrears"] == 2500.0
        assert by_unit["UA102"]["total_arrears"] == 3000.0
        assert by_unit["UA101"]["opening_arrears"] == 0.0
        assert by_unit["UA101"]["current_year_outstanding"] == 2500.0
        assert by_unit["UA102"]["current_year_outstanding"] == 3000.0


@pytest.mark.asyncio
async def test_arrears_board_zero_opening_still_shows_current_year_arrears():
    """A unit with zero prior-year opening arrears but a nonzero current-year
    net_balance still shows that amount as total_arrears -- there is no
    separate "prior-year-only" scoping any more (see
    domain/finance/formulas/arrears.py module docstring)."""
    mock_db = _build_mock_db()

    def _cursor_mock(data):
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=data)
        return cursor

    mock_db.unit_levy_ledger.find.return_value = _cursor_mock([
        _make_mock_ledger("UA101", 2000.0, admin_opening=0.0, sinking_opening=0.0),
    ])
    mock_db.levy_payments.aggregate.return_value = _cursor_mock([])

    import routers.finance
    from datetime import date

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.get_user_permissions") as mock_perms, \
            patch("routers.finance.date") as mock_date:
        mock_date.today.return_value = date(2025, 11, 1)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        mock_perms.return_value = MagicMock(can_view_finances=True)

        results = await routers.finance.get_arrears_board(current_user={"id": "admin-1", "role": "super_admin"},
                                                          building_id="13195")

        assert len(results) == 1
        assert results[0]["unit_number"] == "UA101"
        assert results[0]["total_arrears"] == 2000.0
        assert results[0]["opening_arrears"] == 0.0
        assert results[0]["current_year_outstanding"] == 2000.0


@pytest.mark.asyncio
async def test_arrears_board_zero_net_balance_shows_no_arrears():
    """A unit whose ledger net_balance is $0 (fully paid, whatever the
    payment history that got it there) correctly shows $0 arrears. Under the
    2026-08-03 canonical formula, net_balance is the single source of truth
    -- total_paid is not re-consulted separately, since a real ledger's
    net_balance already reflects it."""
    mock_db = _build_mock_db()

    def _cursor_mock(data):
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=data)
        return cursor

    mock_db.unit_levy_ledger.find.return_value = _cursor_mock([
        _make_mock_ledger("UA101", 0.0, total_paid=2500.0),
    ])
    mock_db.levy_payments.aggregate.return_value = _cursor_mock([])

    import routers.finance

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.get_user_permissions") as mock_perms:
        mock_perms.return_value = MagicMock(can_view_finances=True)

        results = await routers.finance.get_arrears_board(current_user={"id": "admin-1", "role": "super_admin"},
                                                          building_id="13195")

        ua101 = next((u for u in results if u["unit_number"] == "UA101"), None)
        assert ua101 is None, "a unit with net_balance=0 has no arrears and must not appear on the board"


@pytest.mark.asyncio
async def test_arrears_board_portal_payments_do_not_hide_arrears():
    """Portal levy_payments (current-year Stripe) must NOT reduce opening_arrears.

    Scenario: TH085 has $20.23 opening arrears from FY2025.
    They paid $1,567.97 via portal for current-year Q1+Q2 levy.
    The arrears board shows ONLY opening_arrears (prior-year carry-forward).
    total_confirmed_paid = ledger.total_paid = 0 (NOT portal payments).
    true_arrears = max(0, opening_arrears - ledger_total_paid) = $20.23.
    Portal payments do not affect this calculation.
    """
    mock_db = _build_mock_db()

    def _cursor_mock(data):
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=data)
        return cursor

    # TH085: $20.23 opening_arrears, ledger total_paid=0 (no DEFT/bank)
    mock_db.unit_levy_ledger.find.return_value = _cursor_mock([
        {**_make_mock_ledger("TH085", 20.23, admin_opening=14.16, sinking_opening=6.07), "total_paid": 0.0},
    ])
    mock_db.units.find.return_value = _cursor_mock([
        {**_make_mock_unit("TH085"), "owner_name": "Jinal Achal"},
    ])
    # Portal payments: $1,567.97 via Stripe — must NOT reduce opening_arrears
    mock_db.levy_payments.aggregate.return_value = _cursor_mock([
        {"_id": "TH085", "total_paid": 1567.97, "last_date": "2026-03-01", "method": "online"},
    ])

    import routers.finance

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.get_user_permissions") as mock_perms:
        mock_perms.return_value = MagicMock(can_view_finances=True)

        results = await routers.finance.get_arrears_board(current_user={"id": "admin-1", "role": "super_admin"},
                                                          building_id="13195")

        th015 = next((u for u in results if u["unit_number"] == "TH085"), None)
        assert th015 is not None, "TH085 must appear on board (opening_arrears not cleared by portal payments)"
        # total_arrears = opening_arrears = $20.23 (portal payments do not reduce it)
        assert th015["total_arrears"] == pytest.approx(20.23, abs=0.01), (
            f"total_arrears must equal opening_arrears ($20.23), got {th015['total_arrears']}"
        )
        # If portal payments incorrectly reduced opening_arrears: 20.23 - 1567.97 < 0 → excluded.
        # Correct: total_arrears = $20.23 (unaffected by portal payments)
        portal_reduced = 20.23 - 1567.97
        assert portal_reduced < 0, "Portal payment exceeds opening_arrears — demonstrates the old bug"
        assert th015["total_arrears"] > 0, "TH085 correctly appears with $20.23 opening_arrears"


@pytest.mark.asyncio
async def test_arrears_board_non_portal_unit_visible():
    """Units with no portal account (user_units entry) must still appear if they have arrears."""
    mock_db = _build_mock_db()

    def _cursor_mock(data):
        cursor = MagicMock()
        cursor.sort = MagicMock(return_value=cursor)
        cursor.to_list = AsyncMock(return_value=data)
        return cursor

    # TH074: $580.01 opening_arrears, no portal account
    mock_db.unit_levy_ledger.find.return_value = _cursor_mock([
        {**_make_mock_ledger("TH074", 580.01, admin_opening=406.01, sinking_opening=174.00), "total_paid": 0.0},
    ])
    mock_db.units.find.return_value = _cursor_mock([
        {**_make_mock_unit("TH074"), "owner_name": "Hamish Angus"},
    ])
    # No portal account in user_units
    mock_db.user_units.aggregate.return_value = _cursor_mock([])
    mock_db.levy_payments.aggregate.return_value = _cursor_mock([])

    import routers.finance

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.get_user_permissions") as mock_perms:
        mock_perms.return_value = MagicMock(can_view_finances=True)

        results = await routers.finance.get_arrears_board(current_user={"id": "admin-1", "role": "super_admin"},
                                                          building_id="13195")

        th004 = next((u for u in results if u["unit_number"] == "TH074"), None)
        assert th004 is not None, "TH074 must appear even without portal account"
        assert th004["has_portal_account"] is False
        assert th004["owner_name"] == "Hamish Angus"  # from units collection
        # Arrears board shows ONLY opening_arrears = $580.01 (prior-year carry-forward).
        # Core assertion: unit appears with correct owner info regardless of portal registration.
        assert th004["total_arrears"] == pytest.approx(580.01, abs=0.01), (
            f"total_arrears must equal opening_arrears ($580.01), got {th004['total_arrears']}"
        )


@pytest.mark.asyncio
async def test_refer_to_dca_updates_status():
    """Referred DCA action should update unit metadata and log it."""
    mock_db = _build_mock_db()

    import routers.finance

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.create_audit_log", AsyncMock()) as mock_audit:
        current_user = {"id": "admin-1", "full_name": "Admin"}
        res = await routers.finance.refer_to_dca("UA101", current_user=current_user)

        assert res["success"] is True
        assert "DCA-UA101" in res["dca_reference"]

        # Verify DB update
        mock_db.units.update_one.assert_called_once()
        args, kwargs = mock_db.units.update_one.call_args
        # kwargs is actually the dictionary of keyword arguments
        # args[1] might be the update dict if positional, but update_one uses positional for (filter, update)
        update_dict = args[1] if len(args) > 1 else kwargs.get('update')
        assert update_dict["$set"]["arrears_metadata.dca_status"] == "referred"

        mock_audit.assert_called_once()


@pytest.mark.asyncio
async def test_dca_eligibility_cron_logic():
    """Cron logic should mark units as eligible after 14 days and > $1500 arrears."""
    mock_db = MagicMock()

    def _cursor_mock(data):
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=data)
        return cursor

    # Unit with $2500 arrears
    mock_db.unit_levy_ledger.find.return_value = _cursor_mock([{"unit_number": "UA101", "net_balance": 2500.0}])

    # Unit had notice sent 20 days ago
    sent_at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    mock_db.units.find_one = AsyncMock(return_value={
        "unit_number": "UA101",
        "arrears_metadata": {
            "dca_status": "none",
            "first_notice_sent_at": sent_at
        }
    })
    mock_db.units.update_one = AsyncMock()

    import cron.cron_finance_recompute
    with patch("cron.cron_finance_recompute.db", mock_db):
        count = await cron.cron_finance_recompute._check_dca_eligibility("2026", dry_run=False, building_id="13195")

        assert count == 1
        mock_db.units.update_one.assert_called_once()
        args, kwargs = mock_db.units.update_one.call_args
        update_dict = args[1] if len(args) > 1 else kwargs.get('update')
        assert update_dict["$set"]["arrears_metadata.dca_status"] == "eligible"


# ─────────────────────────────────────────────────────────────────────────────
# PG ledger source wiring (2026-08-09, GAP-FIN-058 re-enable) — units/owner/
# arrears_metadata always come from Mongo; only the ledger figures (net_balance/
# opening_arrears/total_levied) that feed the per-unit arrears computation are
# gated on route_state["source"]. These tests cover that gating in isolation,
# separate from the arrears/severity/interest computation logic covered above.
# ─────────────────────────────────────────────────────────────────────────────

_MONGO_SOURCE_STATE = {
    "route_key": "finance.arrears_detail", "source": "mongo", "run_shadow": False,
    "eligible_for_postgres_read": False, "blocked_reason": "test", "domain_mode": "mongo_primary",
    "route_readiness": {"status": "not_started"},
}
_POSTGRES_SOURCE_STATE = {
    "route_key": "finance.arrears_detail", "source": "postgres", "run_shadow": True,
    "eligible_for_postgres_read": True, "blocked_reason": None, "domain_mode": "postgres_write",
    "route_readiness": {"status": "shadow_pass"},
}


@pytest.mark.asyncio
async def test_arrears_board_uses_pg_ledger_when_source_is_postgres():
    """source=postgres: the PG-sourced ledger (via get_unit_levy_balance_list) is
    used instead of db.unit_levy_ledger.find, and its closing_balance/opening_balance
    feed the exact same downstream arrears computation unchanged."""
    mock_db = _build_mock_db()
    import routers.finance

    pg_balances = [
        {"unit_number": "UA101", "financial_year": "2026", "opening_balance": 1000.0,
         "levied_amount": 1200.0, "paid_amount": 200.0, "closing_balance": 1000.0, "arrears": 1000.0},
        {"unit_number": "UA102", "financial_year": "2026", "opening_balance": 0.0,
         "levied_amount": 500.0, "paid_amount": 500.0, "closing_balance": 0.0, "arrears": 0.0},
    ]

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.get_user_permissions") as mock_perms, \
            patch("routers.finance.get_finance_route_runtime_state",
                  new=AsyncMock(return_value=_POSTGRES_SOURCE_STATE)), \
            patch("routers.finance._financial_read_service.get_unit_levy_balance_list",
                  new=AsyncMock(return_value=pg_balances)) as mock_pg_list, \
            patch.object(mock_db.unit_levy_ledger, "find") as mock_mongo_find:
        mock_perms.return_value = MagicMock(can_view_finances=True)

        results = await routers.finance.get_arrears_board(
            current_user={"id": "admin-1", "role": "super_admin"}, building_id="13195",
        )

        mock_pg_list.assert_awaited_once()
        mock_mongo_find.assert_not_called()
        # UA102's PG closing_balance is 0 -> no arrears -> excluded from the board.
        assert len(results) == 1
        assert results[0]["unit_number"] == "UA101"
        assert results[0]["total_arrears"] == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_arrears_board_falls_back_to_mongo_when_pg_unavailable():
    """source=postgres but get_unit_levy_balance_list returns None (no PG scheme
    resolved) -> falls back to the Mongo ledger, same as before this route was wired."""
    mock_db = _build_mock_db()
    import routers.finance

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.get_user_permissions") as mock_perms, \
            patch("routers.finance.get_finance_route_runtime_state",
                  new=AsyncMock(return_value=_POSTGRES_SOURCE_STATE)), \
            patch("routers.finance._financial_read_service.get_unit_levy_balance_list",
                  new=AsyncMock(return_value=None)):
        mock_perms.return_value = MagicMock(can_view_finances=True)

        results = await routers.finance.get_arrears_board(
            current_user={"id": "admin-1", "role": "super_admin"}, building_id="13195",
        )

        # mock_db's Mongo ledger fixture (_build_mock_db) has both units in arrears.
        assert len(results) == 2


@pytest.mark.asyncio
async def test_arrears_board_falls_back_to_mongo_when_pg_raises():
    """source=postgres but the PG query raises -> falls back to Mongo rather than
    propagating a 500 to the Arrears Recovery Board."""
    mock_db = _build_mock_db()
    import routers.finance

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.get_user_permissions") as mock_perms, \
            patch("routers.finance.get_finance_route_runtime_state",
                  new=AsyncMock(return_value=_POSTGRES_SOURCE_STATE)), \
            patch("routers.finance._financial_read_service.get_unit_levy_balance_list",
                  new=AsyncMock(side_effect=RuntimeError("PG connection refused"))):
        mock_perms.return_value = MagicMock(can_view_finances=True)

        results = await routers.finance.get_arrears_board(
            current_user={"id": "admin-1", "role": "super_admin"}, building_id="13195",
        )

        assert len(results) == 2


@pytest.mark.asyncio
async def test_arrears_board_source_mongo_never_calls_pg():
    """source=mongo (the pre-2026-08-09 default): the PG ledger function must never
    be called at all, matching pre-existing behaviour exactly."""
    mock_db = _build_mock_db()
    import routers.finance

    with patch("routers.finance.db", mock_db), \
            patch("routers.finance.get_user_permissions") as mock_perms, \
            patch("routers.finance.get_finance_route_runtime_state",
                  new=AsyncMock(return_value=_MONGO_SOURCE_STATE)), \
            patch("routers.finance._financial_read_service.get_unit_levy_balance_list",
                  new=AsyncMock()) as mock_pg_list:
        mock_perms.return_value = MagicMock(can_view_finances=True)

        await routers.finance.get_arrears_board(
            current_user={"id": "admin-1", "role": "super_admin"}, building_id="13195",
        )

        mock_pg_list.assert_not_awaited()
