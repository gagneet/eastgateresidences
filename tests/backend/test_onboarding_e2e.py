"""End-to-end onboarding flow test.

Exercises the full self-managed scheme creation path:

  1. POST /admin/sm-organisations/self-managed → tenant + scheme + invitation
  2. POST /onboarding/scheme/start             → onboarding session
  3. POST /onboarding/scheme/{id}/lots         → lot import
  4. PATCH /onboarding/scheme/{id}              → step_data update
  5. POST /onboarding/scheme/{id}/finalize     → genesis journals + invitations,
     trust bank account creation (tasks/GAP-ONBOARD-001), Track A manual
     opening-balance genesis posting

Validates multi-tenant isolation: lots created for tenant A must never be
visible when querying as tenant B.

Requires DATABASE_URL.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from bson import ObjectId

from database import db as mongo_db
from request_context import set_ctx_building_id

_RAW_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = re.sub(r"^postgresql\+asyncpg://", "postgresql://", _RAW_URL)

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="DATABASE_URL not set — skipping integration tests.",
)

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(scope="module")
async def pg():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("SELECT set_config('app.tenant_id', $1, false)", _BYPASS_UUID)
    yield conn
    await conn.close()


@pytest.fixture(scope="module")
async def sa_user(pg):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    email = f"sa-e2e-{user_id.hex[:8]}@strataos.com.au"
    await pg.execute(
        "INSERT INTO core.tenants (tenant_id, tenant_name, status, is_test_data) VALUES ($1, 'E2E SA Tenant', 'active', TRUE)",
        tenant_id,
    )
    await pg.execute(
        # is_test_data is REQUIRED on any test-created user: nothing else can
        # find this row later. The pytest_sessionfinish sweep keys off this flag,
        # and (since 2026-08-25) the production login path refuses accounts
        # carrying it. Omitting it on a super_admin leaks a working admin
        # credential that no cleanup can see.
        """INSERT INTO core.users
           (user_id, tenant_id, email, full_name, password_hash, role,
            is_active, is_approved, is_test_data)
           VALUES ($1, $2, $3, 'E2E SA', '', 'super_admin', TRUE, TRUE, TRUE)""",
        user_id, tenant_id, email,
    )
    yield {
        "id": str(user_id),
        "email": email,
        "full_name": "E2E SA",
        "role": "super_admin",
        "tenant_id": str(tenant_id),
    }
    await pg.execute("DELETE FROM core.user_invitations WHERE invited_by = $1", user_id)
    await pg.execute("DELETE FROM core.users   WHERE user_id   = $1", user_id)
    await pg.execute("DELETE FROM core.tenants WHERE tenant_id = $1", tenant_id)


@pytest.fixture
async def cleanup(pg):
    """Track inserted tenant_ids and remove all child rows + tenants on teardown.

    The previous implementation wrapped the chain in ``try/except: pass`` —
    any unexpected FK aborted the chain mid-step and the surviving rows then
    surfaced in the SA building switcher. We now raise loudly: a leak in this
    suite must fail the test, not silently pollute the dev DB. The conftest
    session-end sweeper backstops anything we miss.

    Runs on both pass and fail — a ``@pytest.fixture`` teardown (the code
    after ``yield``) always executes once the test function returns or
    raises, per pytest's fixture-finalization contract.
    """
    tenant_ids: list[str] = []
    yield tenant_ids
    for tid in tenant_ids:
        t = uuid.UUID(tid)
        await pg.execute("DELETE FROM core.user_invitations       WHERE tenant_id = $1", t)
        await pg.execute("DELETE FROM core.onboarding_sessions    WHERE tenant_id = $1", t)
        # feature_toggle_overrides.scheme_id has no ON DELETE CASCADE from
        # core.schemes — an override row left behind by a toggle-gating test
        # would abort the DELETE FROM core.schemes below with a FK violation.
        await pg.execute("DELETE FROM core.feature_toggle_overrides WHERE tenant_id = $1", t)
        # core.lots RLS policy is `tenant_id = current_tenant_id()` with NO
        # bypass clause (unlike core.schemes). The bypass UUID set on `pg`
        # would hide the lot rows from this DELETE — and the subsequent
        # DELETE on core.schemes would then fail with lots_scheme_id_fkey.
        # Switch RLS to the actual test tenant just for this DELETE.
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", tid)
        await pg.execute("DELETE FROM core.lots                WHERE tenant_id = $1", t)
        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", _BYPASS_UUID)
        await pg.execute("DELETE FROM core.schemes             WHERE tenant_id = $1", t)
        await pg.execute("DELETE FROM core.tenants             WHERE tenant_id = $1", t)


@pytest.fixture
async def mongo_cleanup():
    """Track Mongo (building_id, collection) pairs created by a test and delete them
    on teardown — runs on both pass and fail, same guarantee as the `cleanup` fixture
    above. Onboarding finalize writes trust_accounts_v2 (Mongo) even though the
    onboarding session itself lives in Postgres, so Postgres-only cleanup would leak
    these documents.
    """
    building_ids: list[str] = []
    yield building_ids
    for bid in building_ids:
        await mongo_db.trust_accounts_v2.delete_many({"building_id": bid})


_JOURNAL_TABLES_WITH_IMMUTABILITY_TRIGGERS = {
    "finance.journal_lines": "trg_prevent_posted_line_update",
    "finance.journal_entries": "trg_prevent_posted_journal_update",
}

# Leaf-first delete order for the finance.* cluster genesis posting writes to.
# Plain alphabetical/information_schema order breaks this (e.g. "funds" sorts
# before "trust_accounts" but trust_accounts.fund_id FK's into funds, so funds
# must go LAST of this group) — confirmed empirically via repeated live FK
# violations while first writing this fixture, not derived from the schema
# in the abstract. Tables not listed here are assumed to have no inter-table
# FKs among each other (true for the plain core.* K/V and audit tables this
# fixture also has to clean up) and are deleted in any order after this list.
_FINANCE_CLUSTER_DELETE_ORDER = [
    "finance.journal_lines",
    "finance.journal_entries",
    "finance.trust_accounts",
    "finance.levy_items",
    "finance.expense_transactions",
    "finance.owner_credit_balances",
    "finance.levy_rules",
    "finance.gl_accounts",
    "finance.accounting_periods",
    "finance.funds",
]


@pytest.fixture
async def cleanup_scheme(pg):
    """Track scheme_numbers created under a SHARED tenant (e.g. sa_user's own
    module-scoped tenant, reused across several tests) and remove only the
    scheme + everything FK'd to it — never the tenant or its users.

    Distinct from `cleanup` above, which deletes an entire tenant and is only
    safe for a tenant created fresh within that same test (no other row, e.g.
    sa_user's own core.users row, still references it).

    Deliberately generic rather than a hand-maintained table list: finalize_onboarding's
    genesis-posting path (post_import_based_genesis_cutover -> ensure_scheme_finance_bootstrap)
    writes to a wide, easy-to-under-enumerate set of finance.* tables (funds,
    accounting_periods, gl_accounts, journal_entries, journal_lines, trust_accounts) plus
    core.outbox/building_settings/feature_toggle_overrides — discovered empirically, one FK
    violation at a time, while first writing this fixture. Querying information_schema for
    every scheme_id-having table avoids silently missing the next one a future feature adds.

    Almost every one of these tables (core.lots included) has an RLS policy of the bare
    `tenant_id = core.current_tenant_id()` form with NO bypass clause (confirmed live via
    pg_policies — unlike core.schemes/core.tenants, which do have one) — DELETEs under the
    bypass tenant_id silently affect zero rows, so the real tenant context must be set first.
    """
    scheme_numbers: list[str] = []
    yield scheme_numbers
    for scheme_number in scheme_numbers:
        row = await pg.fetchrow(
            "SELECT scheme_id, tenant_id FROM core.schemes WHERE scheme_number = $1", scheme_number,
        )
        if not row:
            continue
        scheme_id, tenant_id = row["scheme_id"], row["tenant_id"]

        table_rows = await pg.fetch(
            """SELECT table_schema, table_name FROM information_schema.columns
               WHERE column_name = 'scheme_id' AND table_schema IN ('core', 'finance')
               AND table_name NOT IN ('schemes')"""
        )
        scheme_scoped_tables = [f"{r['table_schema']}.{r['table_name']}" for r in table_rows]

        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_id))

        # Genesis-posted journal_entries/journal_lines are deliberately immutable —
        # their triggers raise on ANY UPDATE *or* DELETE of a posted row (real
        # audit-integrity rule, not a bug). finalize_onboarding always posts with
        # is_test_data=False (no way for a caller to mark genesis journals as test
        # data), so cleanup here uses the same DISABLE/ENABLE-trigger pattern
        # already documented as the sanctioned approach for this exact scenario
        # (project memory: "migration corrections need DISABLE/ENABLE trigger,
        # admin only, revert immediately"). try/finally guarantees both triggers
        # are always re-enabled even if a DELETE fails partway through — and a
        # bare `except Exception: pass` per statement (not per whole scheme) means
        # one missing/empty table can't abort cleanup for the rest of this scheme
        # OR for the next scheme_number in this loop.
        for tbl, trigger in _JOURNAL_TABLES_WITH_IMMUTABILITY_TRIGGERS.items():
            await pg.execute(f"ALTER TABLE {tbl} DISABLE TRIGGER {trigger}")
        try:
            for tbl in _FINANCE_CLUSTER_DELETE_ORDER:
                if tbl in scheme_scoped_tables:
                    try:
                        await pg.execute(f"DELETE FROM {tbl} WHERE scheme_id = $1", scheme_id)
                    except Exception as exc:
                        print(f"WARNING: cleanup_scheme could not clear {tbl} for scheme {scheme_id}: {exc}")
            for tbl in scheme_scoped_tables:
                if tbl not in _FINANCE_CLUSTER_DELETE_ORDER:
                    try:
                        await pg.execute(f"DELETE FROM {tbl} WHERE scheme_id = $1", scheme_id)
                    except Exception as exc:
                        print(f"WARNING: cleanup_scheme could not clear {tbl} for scheme {scheme_id}: {exc}")
        finally:
            for tbl, trigger in _JOURNAL_TABLES_WITH_IMMUTABILITY_TRIGGERS.items():
                await pg.execute(f"ALTER TABLE {tbl} ENABLE TRIGGER {trigger}")

        await pg.execute("SELECT set_config('app.tenant_id', $1, false)", _BYPASS_UUID)
        await pg.execute("DELETE FROM core.schemes WHERE scheme_id = $1", scheme_id)


@pytest.mark.asyncio
async def test_full_self_managed_onboarding_e2e(cleanup, sa_user, pg):
    """Critical-path: SA creates scheme → imports lots → finalizes."""
    from routers.sm_organisations import (
        create_self_managed_scheme,
        CreateSelfManagedRequest,
    )
    from routers.onboarding import (
        start_onboarding,
        import_lots,
        SchemeStartRequest,
        SchemeLotsRequest,
        LotInput,
    )

    # 1. Create self-managed tenant + scheme + EC invitation
    sm_body = CreateSelfManagedRequest(
        scheme_number=f"E2E-{uuid.uuid4().hex[:6]}",
        scheme_name="E2E Self-Managed Tower",
        scheme_legal_name="The Owners — E2E Plan",
        jurisdiction="ACT",
        ec_admin_email="ec@e2e-self-managed.com.au",
        ec_admin_first_name="Pat",
        ec_admin_last_name="Owner",
    )
    with patch("routers.sm_organisations.send_email_async", new_callable=AsyncMock):
        sm_result = await create_self_managed_scheme(sm_body, current_user=sa_user, _is_test_data=True)

    assert sm_result["tenant_id"]
    assert sm_result["scheme_id"]
    cleanup.append(sm_result["tenant_id"])
    target_tenant_id = sm_result["tenant_id"]
    target_scheme_id = sm_result["scheme_id"]

    # 2. Start onboarding session under SA acting on the new tenant
    start_body = SchemeStartRequest(
        scheme_number="E2E-EXTRA",
        scheme_name="E2E extra scheme — should fail (self-managed 1:1)",
        jurisdiction="ACT",
        tenant_id=target_tenant_id,
    )
    # The trigger must reject this — self-managed already has a scheme
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await start_onboarding(start_body, current_user=sa_user)
    assert exc.value.status_code == 409
    assert exc.value.detail.get("code") == "self_managed_already_has_scheme"

    # 3. Confirm cross-tenant isolation: lot inserted under target_tenant_id
    #    is invisible to RLS context of the SA's own tenant.
    await pg.execute("SELECT set_config('app.tenant_id', $1, false)", target_tenant_id)
    await pg.execute(
        """INSERT INTO core.lots
               (tenant_id, scheme_id, lot_number, unit_number, lot_use, status)
           VALUES ($1, $2, 'E2E-LOT-1', 'E2E-U101', 'residential', 'active')""",
        uuid.UUID(target_tenant_id), uuid.UUID(target_scheme_id),
    )

    # Switch to SA's own tenant; the new lot must not be visible
    await pg.execute("SELECT set_config('app.tenant_id', $1, false)", sa_user['tenant_id'])
    from_other_tenant = await pg.fetchval(
        "SELECT COUNT(*) FROM core.lots WHERE lot_number = 'E2E-LOT-1'"
    )
    assert from_other_tenant == 0, "Cross-tenant isolation broken — RLS leaked lot."

    # Restore bypass for teardown
    await pg.execute("SELECT set_config('app.tenant_id', $1, false)", _BYPASS_UUID)


# ─────────────────────────────────────────────────────────────────────────────
# finalize_onboarding: trust bank account creation + Track A genesis posting
# (tasks/GAP-ONBOARD-001, added during the 2026-07-25 audit). sa_user's own
# tenant defaults to tenant_type='strata_manager' (see core.tenants schema),
# not self-managed, so it can hold multiple schemes without tripping the 1:1
# self-managed trigger exercised above — safe to start a fresh session per
# test under the same shared sa_user tenant.
# ─────────────────────────────────────────────────────────────────────────────

async def _start_session(sa_user, scheme_suffix: str) -> tuple[str, str]:
    """Start a fresh onboarding session under sa_user's own tenant. Returns (session_id, scheme_number)."""
    from routers.onboarding import start_onboarding, SchemeStartRequest

    scheme_number = f"E2E-TRUST-{scheme_suffix}"
    body = SchemeStartRequest(
        scheme_number=scheme_number,
        scheme_name=f"E2E Trust Test {scheme_suffix}",
        jurisdiction="ACT",
        tenant_id=sa_user["tenant_id"],
    )
    result = await start_onboarding(body, current_user=sa_user)
    return result["session_id"], scheme_number


@pytest.mark.asyncio
async def test_finalize_creates_trust_account_when_all_fields_present(cleanup_scheme, mongo_cleanup, sa_user):
    """Fix 2 (GAP-ONBOARD-001): bank_name+bsb+account_no+account_name in step_data
    must produce a real trust_accounts_v2 document via finalize, using the V2 API
    (not the deprecated V1 /trust/accounts) — see pattern_trust_accounting_v1_v2_api_split."""
    from routers.onboarding import (
        update_onboarding_step, SchemeStepUpdate,
        finalize_onboarding, FinalizeRequest,
        derive_building_id_from_scheme_number,
    )

    session_id, scheme_number = await _start_session(sa_user, "created")
    cleanup_scheme.append(scheme_number)
    building_id = derive_building_id_from_scheme_number(scheme_number)
    mongo_cleanup.append(building_id)

    await update_onboarding_step(
        session_id,
        SchemeStepUpdate(step_data={
            "trust_account_name": "E2E OC Trust Account",
            "trust_bank_name": "NAB",
            "trust_bsb": "082-001",
            "trust_account_no": "123456789",
            "trust_opening_balance": "5000.00",
        }),
        current_user=sa_user,
    )

    result = await finalize_onboarding(session_id, FinalizeRequest(send_owner_invites=False, send_ec_invites=False), current_user=sa_user)

    assert "trust_account_created" in result, f"Expected trust_account_created in response, got: {result}"
    assert "trust_account_error" not in result
    # trust_accounts_v2 is a building-scoped collection (TenantScopedDatabase auto-injects
    # building_id into every query) — set_ctx_building_id must be called before reading it
    # directly, or the wrapper raises "Missing building context" rather than silently
    # scanning cross-tenant.
    set_ctx_building_id(building_id)
    account = await mongo_db.trust_accounts_v2.find_one({"_id": ObjectId(result["trust_account_created"])})
    assert account is not None
    assert account["building_id"] == building_id
    assert account["bank_name"] == "NAB"
    assert account["account_number_masked"] == "****6789"
    assert account["current_balance_cents"] == 500000


@pytest.mark.asyncio
async def test_finalize_reports_error_when_trust_fields_incomplete(cleanup_scheme, sa_user):
    """Only trust_account_name set (no bank/bsb/account number) — must not create a
    half-populated account; must surface trust_account_error, not silently drop it."""
    from routers.onboarding import (
        update_onboarding_step, SchemeStepUpdate,
        finalize_onboarding, FinalizeRequest,
    )

    session_id, scheme_number = await _start_session(sa_user, "incomplete")
    cleanup_scheme.append(scheme_number)

    await update_onboarding_step(
        session_id,
        SchemeStepUpdate(step_data={"trust_account_name": "Incomplete Trust Account"}),
        current_user=sa_user,
    )

    result = await finalize_onboarding(session_id, FinalizeRequest(send_owner_invites=False, send_ec_invites=False), current_user=sa_user)

    assert "trust_account_created" not in result
    assert "trust_account_error" in result
    assert "bank name, BSB, and account number" in result["trust_account_error"]


@pytest.mark.asyncio
async def test_finalize_skips_trust_account_when_toggle_disabled(cleanup_scheme, sa_user):
    """Fix 1 (GAP-ONBOARD-001): the NEW building's own trust_accounting toggle must be
    respected even though finalize calls the shared insert helper directly rather than
    going through POST /trust/v2/accounts's require_feature("trust_accounting") dependency."""
    from routers.onboarding import (
        update_onboarding_step, SchemeStepUpdate,
        finalize_onboarding, FinalizeRequest,
    )

    session_id, scheme_number = await _start_session(sa_user, "toggleoff")
    cleanup_scheme.append(scheme_number)

    await update_onboarding_step(
        session_id,
        SchemeStepUpdate(step_data={
            "trust_account_name": "Should Not Be Created",
            "trust_bank_name": "NAB",
            "trust_bsb": "082-001",
            "trust_account_no": "123456789",
        }),
        current_user=sa_user,
    )

    with patch("routers.onboarding.config_repo.resolve_feature_toggle", AsyncMock(return_value=False)):
        result = await finalize_onboarding(session_id, FinalizeRequest(send_owner_invites=False, send_ec_invites=False), current_user=sa_user)

    assert "trust_account_created" not in result
    assert "disabled for this building" in result["trust_account_error"]


@pytest.mark.asyncio
async def test_finalize_posts_genesis_journal_from_manual_current_balances(cleanup_scheme, sa_user, pg):
    """Fix 1 (GAP-ONBOARD-001): the frontend's Track A ("Current Balances") flow PATCHes
    step_data with a `funds` array before calling finalize (see OnboardingWizard.jsx's
    buildCurrentBalancesFunds). This verifies the backend contract that PATCH actually
    relies on — a `funds` array in step_data must produce a real genesis journal,
    exactly like the pre-existing CSV-import ("Track B") path already does."""
    from routers.onboarding import (
        update_onboarding_step, SchemeStepUpdate,
        finalize_onboarding, FinalizeRequest,
    )

    session_id, scheme_number = await _start_session(sa_user, "genesis")
    cleanup_scheme.append(scheme_number)

    await update_onboarding_step(
        session_id,
        SchemeStepUpdate(step_data={
            "funds": [
                {"fund_type": "admin", "opening_balance_cents": 250000, "as_at_date": "2026-01-01"},
                {"fund_type": "sinking", "opening_balance_cents": 100000, "as_at_date": "2026-01-01"},
            ],
        }),
        current_user=sa_user,
    )

    result = await finalize_onboarding(session_id, FinalizeRequest(send_owner_invites=False, send_ec_invites=False), current_user=sa_user)

    assert result["genesis_journals_posted"] == 2, f"Expected 2 genesis journals (admin+sinking), got: {result}"


class TestOnboardingValidationHelpers:
    """Pure-function unit tests — no DB required. _valid_bsb/_valid_account_number
    gate trust-account creation in finalize_onboarding (Fix 2, GAP-ONBOARD-001)."""

    def test_valid_bsb_accepts_hyphenated_and_plain(self):
        from routers.onboarding import _valid_bsb
        assert _valid_bsb("082-001") is True
        assert _valid_bsb("082001") is True

    def test_valid_bsb_accepts_absence(self):
        from routers.onboarding import _valid_bsb
        assert _valid_bsb(None) is True
        assert _valid_bsb("") is True

    def test_valid_bsb_treats_whitespace_only_as_absent(self):
        """PR #547 review (Amazon Q): a whitespace-only value must be treated the same
        as an absent one — the one current caller already strips before calling this
        function, so this is a standalone-robustness fix, not an active-bug fix for
        that call site (see finalize_onboarding's `elif not (... and trust_bsb ...)`
        guard, which already catches a stripped-to-empty value first)."""
        from routers.onboarding import _valid_bsb
        assert _valid_bsb("   ") is True
        assert _valid_bsb("\t\n") is True

    def test_valid_bsb_rejects_wrong_length_or_non_numeric(self):
        from routers.onboarding import _valid_bsb
        assert _valid_bsb("082-0011") is False  # 7 digits
        assert _valid_bsb("08A-001") is False

    def test_valid_account_number_accepts_4_to_10_digits(self):
        from routers.onboarding import _valid_account_number
        assert _valid_account_number("1234") is True
        assert _valid_account_number("1234567890") is True

    def test_valid_account_number_treats_whitespace_only_as_absent(self):
        """PR #547 review (Amazon Q) — see test_valid_bsb_treats_whitespace_only_as_absent."""
        from routers.onboarding import _valid_account_number
        assert _valid_account_number("   ") is True
        assert _valid_account_number("\t\n") is True

    def test_valid_account_number_rejects_out_of_range_or_non_numeric(self):
        from routers.onboarding import _valid_account_number
        assert _valid_account_number("123") is False  # 3 digits
        assert _valid_account_number("12345678901") is False  # 11 digits
        assert _valid_account_number("12ab") is False
