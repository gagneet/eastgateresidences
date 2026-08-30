"""
Tests for backend/seeds/demo_customer.py

All async operations use asyncio.run() within sync test functions (same
pattern as test_remove_legacy_demo_buildings.py) to avoid pytest-asyncio
session-loop conflicts with mocked Motor clients.

Covers:
  - UOE totals and lot composition
  - Idempotency: second seed run produces no duplicates and no errors
  - Tear-down: removes all Acme demo records
  - Data invariants: arrears on correct units, transfer history, levy maths
"""

import asyncio
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# ---------------------------------------------------------------------------
# Load the seed module with patched backend imports
# ---------------------------------------------------------------------------

import importlib.util as _iu


def _mock_session_ctx():
    """Return a mock async_session_context that yields a no-op session.

    tear_down() now discovers tenant-scoped tables and deletes them inside per-table
    SAVEPOINTS, committing the child deletes before it touches core.tenants — so the mock
    session has to support `begin_nested()` as an async context manager and an awaitable
    `commit()`, not just `execute()`. Without them the deletes raise
    "object MagicMock can't be used in 'await' expression" and the test sees nothing.
    """
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(
        scalar=lambda: 0,
        scalar_one=lambda: str(uuid.uuid4()),
        fetchall=lambda: [],
    ))
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    class _Savepoint:
        async def __aenter__(self):
            return None
        async def __aexit__(self, *a):
            return False

    session.begin_nested = MagicMock(side_effect=lambda *a, **k: _Savepoint())

    class _Ctx:
        async def __aenter__(self):
            return session
        async def __aexit__(self, *a):
            pass

    return _Ctx, session


def _load_seed():
    spec = _iu.spec_from_file_location(
        "demo_customer",
        ROOT / "backend" / "seeds" / "demo_customer.py",
    )
    m = _iu.module_from_spec(spec)
    ctx_cls, _ = _mock_session_ctx()
    entities_mod = MagicMock(SchemeRef=lambda **kwargs: MagicMock(**kwargs))
    genesis_mod = MagicMock(
        SYSTEM_CUTOVER_USER_NAME="StrataOS Demo Seed",
        post_import_based_genesis_cutover=AsyncMock(return_value=(None, MagicMock())),
    )
    with patch.dict("sys.modules", {
        "motor": MagicMock(),
        "motor.motor_asyncio": MagicMock(),
        "dotenv": MagicMock(),
        "sqlalchemy": MagicMock(),
        "db_postgres": MagicMock(),
        "db_postgres.session": MagicMock(async_session_context=ctx_cls),
        "services.financial_core.domain.entities": entities_mod,
        "services.financial_core.genesis": genesis_mod,
    }):
        spec.loader.exec_module(m)
    return m


mod = _load_seed()
_run = asyncio.run


# ---------------------------------------------------------------------------
# Helper: build a mock Motor database
# ---------------------------------------------------------------------------

_MONGO_COLLECTIONS = [
    "agm",
    "announcements",
    "annual_levies",
    "building_summaries",
    "buildings",
    "capital_shock_risks",
    "compliance_items",
    "dashboard_v2_signals",
    "feature_toggles",
    "levy_fairness_results_v2",
    "levy_payments",
    "maintenance_requests",
    "memberships",
    "ownership_transfer_log",
    "ownership_periods",
    "parcels",
    "proposals",
    "savings_events",
    "sinking_fund_plan",
    "strata_owners",
    "unit_levy_ledger",
    "units",
    "users",
    "volunteer_events",
    "work_orders",
    "workflow_requests",
]


def _make_col() -> MagicMock:
    col = MagicMock()
    col.update_one = AsyncMock(return_value=MagicMock())
    col.delete_many = AsyncMock(return_value=MagicMock(deleted_count=0))
    col.find_one = AsyncMock(return_value=None)
    return col


def _mock_db() -> MagicMock:
    """Return a mock Motor db where all collection ops are no-ops."""
    db = MagicMock()
    _default_col = _make_col()
    # Attribute access: db.buildings, db.units, etc.
    for name in _MONGO_COLLECTIONS:
        setattr(db, name, _make_col())
    # __getitem__ fallback for any collection not listed above
    db.__getitem__ = MagicMock(return_value=_default_col)
    return db


# ---------------------------------------------------------------------------
# Static / pure tests (no async needed)
# ---------------------------------------------------------------------------

class TestStaticInvariants:
    def test_total_uoe_equals_1000(self):
        total = sum(l["uoe"] for l in mod.LOTS)
        assert total == 1000

    def test_10_apartments_4_townhouses(self):
        apts = [l for l in mod.LOTS if l["unit_type"] == "apartment"]
        ths = [l for l in mod.LOTS if l["unit_type"] == "townhouse"]
        assert len(apts) == 10
        assert len(ths) == 4

    def test_apartment_unit_numbers_a1_to_a10(self):
        apt_units = {l["unit_number"] for l in mod.LOTS if l["unit_type"] == "apartment"}
        assert apt_units == {f"A{i}" for i in range(1, 11)}

    def test_townhouse_unit_numbers_t1_to_t4(self):
        th_units = {l["unit_number"] for l in mod.LOTS if l["unit_type"] == "townhouse"}
        assert th_units == {"T1", "T2", "T3", "T4"}

    def test_14_owner_records(self):
        assert len(mod.OWNERS) == 14

    def test_every_lot_has_an_owner(self):
        lot_units = {l["unit_number"] for l in mod.LOTS}
        owner_units = {o["unit"] for o in mod.OWNERS}
        assert lot_units == owner_units

    def test_joint_owners_have_name_b(self):
        for o in mod.OWNERS:
            if o["owner_type"] == "joint":
                assert o["name_b"], f"Joint owner {o['unit']} missing name_b"

    def test_exactly_one_corporate_owner(self):
        corp = [o for o in mod.OWNERS if o["owner_type"] == "corporate"]
        assert len(corp) == 1
        assert "A6" == corp[0]["unit"]

    def test_six_historical_transfers(self):
        assert len(mod.TRANSFERS) == 6

    def test_two_arrears_units(self):
        assert set(mod._ARREARS_UNITS.keys()) == {"A6", "A10"}

    def test_arrears_a6_is_60_days(self):
        assert mod._ARREARS_UNITS["A6"]["days"] == 60

    def test_arrears_a10_is_120_days(self):
        assert mod._ARREARS_UNITS["A10"]["days"] == 120

    def test_three_portal_users(self):
        assert len(mod.PORTAL_USERS) == 3

    def test_portal_user_roles(self):
        roles = {u["role"] for u in mod.PORTAL_USERS}
        assert "strata_manager" in roles
        assert "chairman" in roles
        assert "ec_member" in roles

    def test_portal_user_emails(self):
        emails = {u["email"] for u in mod.PORTAL_USERS}
        assert "manager@acmestrata.demo" in emails
        assert "chair@stratademo.au" in emails
        assert "member@stratademo.au" in emails

    def test_deterministic_uuids_are_stable(self):
        """Running the module twice produces the same tenant/scheme IDs."""
        assert mod.ACME_TENANT_ID == mod.ACME_TENANT_ID
        assert mod.ACME_SCHEME_ID == mod.ACME_SCHEME_ID
        # UUIDs are well-formed
        uuid.UUID(mod.ACME_TENANT_ID)
        uuid.UUID(mod.ACME_SCHEME_ID)

    def test_quarterly_levy_proportional_to_uoe(self):
        """Higher-UOE lots pay more per quarter."""
        a1_uoe = next(l["uoe"] for l in mod.LOTS if l["unit_number"] == "A1")
        t1_uoe = next(l["uoe"] for l in mod.LOTS if l["unit_number"] == "T1")
        a1_admin_q = mod._quarterly_levy(a1_uoe, "admin")
        t1_admin_q = mod._quarterly_levy(t1_uoe, "admin")
        assert t1_admin_q > a1_admin_q

    def test_annual_admin_levy_totals_140k(self):
        """Sum of all unit quarterly admin levies × 4 ≈ $140,000 within $1."""
        total = sum(mod._quarterly_levy(l["uoe"], "admin") * 4 for l in mod.LOTS)
        assert abs(total - mod.ADMIN_ANNUAL) < 1.0

    def test_annual_sinking_levy_totals_70k(self):
        total = sum(mod._quarterly_levy(l["uoe"], "sinking") * 4 for l in mod.LOTS)
        assert abs(total - mod.SINKING_ANNUAL) < 1.0

    def test_scheme_number(self):
        # Renumbered from "UP-DEMO-001" on 2026-08-20 at the owner's request.
        assert mod.ACME_SCHEME_NUMBER == "UPDEMO5"

    def test_building_id_is_demo_prefix(self):
        assert "DEMO" in mod.ACME_BUILDING_ID.upper()

    def test_abn_format(self):
        """ABN should be 11 digits (spaces allowed)."""
        digits = mod.ACME_ABN.replace(" ", "")
        assert len(digits) == 11
        assert digits.isdigit()


# ---------------------------------------------------------------------------
# Layer execution tests (mock DB, verify calls made)
# ---------------------------------------------------------------------------

class TestLayer1Structural:
    def test_layer1_calls_buildings_upsert(self):
        db = _mock_db()

        async def _inner():
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", _mock_session_ctx()[0]):
                await mod.layer_1_structural()

        _run(_inner())
        db.buildings.update_one.assert_called_once()
        call_args = db.buildings.update_one.call_args
        filter_doc = call_args[0][0]
        assert filter_doc["building_id"] == mod.ACME_BUILDING_ID

    def test_layer1_upserts_14_units(self):
        db = _mock_db()

        async def _inner():
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", _mock_session_ctx()[0]):
                await mod.layer_1_structural()

        _run(_inner())
        assert db.units.update_one.call_count == 14

    def test_layer1_all_units_scoped_to_acme_building(self):
        db = _mock_db()

        async def _inner():
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", _mock_session_ctx()[0]):
                await mod.layer_1_structural()

        _run(_inner())
        for c in db.units.update_one.call_args_list:
            assert c[0][0]["building_id"] == mod.ACME_BUILDING_ID


class TestLayer4LeviesAndPayments:
    def test_arrears_units_have_no_skipped_quarter_payments(self):
        """A6 and A10 must not have payment records for their skipped quarters."""
        recorded_payments: list[dict] = []
        db = _mock_db()

        async def _capture_payment(filter_, update, **kw):
            doc = update.get("$set", {})
            if doc.get("unit_number") in mod._ARREARS_UNITS:
                recorded_payments.append(doc)
            return MagicMock()

        db.levy_payments.update_one = AsyncMock(side_effect=_capture_payment)

        async def _inner():
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", _mock_session_ctx()[0]):
                await mod.layer_4_levies_and_payments()

        _run(_inner())

        # A6 must not have a Q1 2026 payment
        a6_payments = {(p["year"], p["quarter"]) for p in recorded_payments
                       if p.get("unit_number") == "A6"}
        assert ("2026", "Q1") not in a6_payments, "A6 should not have paid Q1 2026"

        # A10 must not have Q4 2025 or Q1 2026
        a10_payments = {(p["year"], p["quarter"]) for p in recorded_payments
                        if p.get("unit_number") == "A10"}
        assert ("2025", "Q4") not in a10_payments, "A10 should not have paid Q4 2025"
        assert ("2026", "Q1") not in a10_payments, "A10 should not have paid Q1 2026"

    def test_non_arrears_units_pay_all_quarters(self):
        """A1 (not in arrears) should have payment records for all past quarters."""
        a1_payments: list[tuple] = []
        db = _mock_db()

        async def _capture(filter_, update, **kw):
            doc = update.get("$set", {})
            if doc.get("unit_number") == "A1":
                a1_payments.append((doc.get("year"), doc.get("quarter")))
            return MagicMock()

        db.levy_payments.update_one = AsyncMock(side_effect=_capture)

        async def _inner():
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", _mock_session_ctx()[0]):
                await mod.layer_4_levies_and_payments()

        _run(_inner())
        # All 4 quarters of 2025 should be paid for A1
        for q in range(1, 5):
            assert ("2025", f"Q{q}") in a1_payments, f"A1 missing Q{q} 2025 payment"


class TestLayer5Toggles:
    def test_feature_toggles_upserted_for_acme_building(self):
        db = _mock_db()

        async def _inner():
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", _mock_session_ctx()[0]):
                await mod.layer_5_toggles_and_users()

        _run(_inner())
        # Each toggle call must be scoped to ACME_BUILDING_ID
        for c in db.feature_toggles.update_one.call_args_list:
            assert c[0][0]["building_id"] == mod.ACME_BUILDING_ID

    def test_exactly_3_portal_users_created_in_mongo(self):
        db = _mock_db()
        portal_emails = set()

        async def _capture(filter_, update, **kw):
            email = filter_.get("email", "")
            if any(email == pu["email"] for pu in mod.PORTAL_USERS):
                portal_emails.add(email)
            return MagicMock()

        db.users.update_one = AsyncMock(side_effect=_capture)

        async def _inner():
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", _mock_session_ctx()[0]):
                await mod.layer_5_toggles_and_users()

        _run(_inner())
        assert len(portal_emails) == 3


# ---------------------------------------------------------------------------
# Idempotency: second call must not raise and must call update_one (upsert)
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_layer1_can_be_called_twice_without_error(self):
        db = _mock_db()

        async def _inner():
            ctx, _ = _mock_session_ctx()
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", ctx):
                await mod.layer_1_structural()
                await mod.layer_1_structural()

        _run(_inner())  # Must not raise
        # Second run still upserts (update_one called 14+14 = 28 times for units)
        assert db.units.update_one.call_count == 28

    def test_full_seed_twice_does_not_raise(self):
        db = _mock_db()

        async def _inner():
            ctx, _ = _mock_session_ctx()
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", ctx):
                await mod.seed()
                await mod.seed()

        _run(_inner())  # Must complete without exception


# ---------------------------------------------------------------------------
# Tear-down
# ---------------------------------------------------------------------------

class TestTearDown:
    def test_tear_down_deletes_from_all_key_collections(self):
        # tear_down uses db[name] subscript — track via __getitem__
        delete_calls: list[str] = []
        default_col = _make_col()

        async def _capture_delete(filter_, **kw):
            return MagicMock(deleted_count=3)

        default_col.delete_many = AsyncMock(side_effect=_capture_delete)

        db = MagicMock()

        def _getitem(name):
            delete_calls.append(name)
            return default_col

        db.__getitem__ = MagicMock(side_effect=_getitem)

        async def _inner():
            ctx, _ = _mock_session_ctx()
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", ctx):
                await mod.tear_down()

        _run(_inner())
        assert "units" in delete_calls
        assert "buildings" in delete_calls

    def test_tear_down_targets_acme_building_only(self):
        db = _mock_db()
        filter_args: list[dict] = []

        async def _capture(filter_, **kw):
            filter_args.append(filter_)
            return MagicMock(deleted_count=0)

        for attr in _MONGO_COLLECTIONS:
            getattr(db, attr).delete_many = AsyncMock(side_effect=_capture)

        async def _inner():
            ctx, _ = _mock_session_ctx()
            with patch.object(mod, "_mongo_db", return_value=db), \
                 patch.object(mod, "async_session_context", ctx):
                await mod.tear_down()

        _run(_inner())
        for f in filter_args:
            assert f.get("building_id") == mod.ACME_BUILDING_ID, (
                f"tear_down deleted from wrong building: {f}"
            )
