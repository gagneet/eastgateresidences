"""End-to-end tests for SM organisation + self-managed scheme endpoints.

Covers happy paths and the documented error codes from
``docs/architecture/onboarding/02_endpoint_contracts.md`` §2.1.

Each test inserts via the FastAPI router (not direct SQL) so the auth
guards, RLS, and JSON serialisation paths are all exercised.

Cleanup: every test wraps its writes in a fixture that removes the
created tenants/schemes/invitations after the test runs.

Requires DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

_RAW_URL = os.environ.get("DATABASE_URL", "")
DATABASE_URL = re.sub(r"^postgresql\+asyncpg://", "postgresql://", _RAW_URL)

pytestmark = pytest.mark.skipif(
    not _RAW_URL,
    reason="DATABASE_URL not set — skipping Postgres-backed router tests.",
)

_BYPASS_UUID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(scope="module")
async def pg():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(f"SET app.tenant_id = '{_BYPASS_UUID}'")
    yield conn
    await conn.close()


@pytest.fixture(scope="module")
async def sa_user(pg):
    """Seed a real SA user + tenant in Postgres so invitation FKs resolve.

    Cleaned up at module teardown.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    email = f"sa-test-{user_id.hex[:8]}@strataos.com.au"

    await pg.execute(
        """INSERT INTO core.tenants (tenant_id, tenant_name, status, is_test_data)
           VALUES ($1, 'SA Test Platform Tenant', 'active', TRUE)""",
        tenant_id,
    )
    await pg.execute(
        # is_test_data is REQUIRED — see the note in test_onboarding_e2e.py.
        # A super_admin row without it is invisible to the session-end sweep.
        """INSERT INTO core.users
           (user_id, tenant_id, email, full_name, password_hash, role,
            is_active, is_approved, is_test_data)
           VALUES ($1, $2, $3, 'SA Test', '', 'super_admin', TRUE, TRUE, TRUE)""",
        user_id, tenant_id, email,
    )
    user = {
        "id": str(user_id),
        "email": email,
        "full_name": "SA Test",
        "role": "super_admin",
        "tenant_id": str(tenant_id),
    }
    yield user

    # Teardown in dependency order
    await pg.execute("DELETE FROM core.user_invitations WHERE invited_by = $1", user_id)
    await pg.execute("DELETE FROM core.users   WHERE user_id   = $1", user_id)
    await pg.execute("DELETE FROM core.tenants WHERE tenant_id = $1", tenant_id)


@pytest.fixture
async def cleanup(pg):
    """Track inserted tenant_ids; remove all child rows + tenants on teardown.

    Ordered DELETEs in FK dependency order. The previous version wrapped the
    whole chain in ``try/except: pass`` so any FK we hadn't accounted for
    silently aborted the cleanup mid-chain — the leftover rows then surfaced
    in the SA building switcher. Now the cleanup raises loudly on any error,
    so test runs catch leaks immediately.

    Any test that reaches a path where ``cleanup.append()`` is never called
    (e.g. early raise) is still backstopped by the ``conftest`` session-end
    sweeper which targets every ``is_test_data=TRUE`` row, regardless of
    whether the test fixture tracked it.
    """
    tenant_ids: list[str] = []
    yield tenant_ids
    for tid in tenant_ids:
        await pg.execute("DELETE FROM core.user_invitations WHERE tenant_id = $1", uuid.UUID(tid))
        await pg.execute("DELETE FROM core.onboarding_sessions WHERE tenant_id = $1", uuid.UUID(tid))
        await pg.execute("DELETE FROM core.lots WHERE tenant_id = $1", uuid.UUID(tid))
        await pg.execute("DELETE FROM core.schemes WHERE tenant_id = $1", uuid.UUID(tid))
        await pg.execute("DELETE FROM core.tenants WHERE tenant_id = $1", uuid.UUID(tid))


# ──────────────────────────────────────────────────────────────────────────
# Happy paths
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_self_managed_scheme_happy_path(cleanup, sa_user):
    from routers.sm_organisations import (
        create_self_managed_scheme,
        CreateSelfManagedRequest,
    )

    body = CreateSelfManagedRequest(
        scheme_number=f"TEST-{uuid.uuid4().hex[:6]}",
        scheme_name="Test Self-Managed Tower",
        scheme_legal_name="The Owners — Test Plan",
        jurisdiction="ACT",
        ec_admin_email="ec@test-self-managed.com.au",
        ec_admin_first_name="Pat",
        ec_admin_last_name="Owner",
        invitation_role="strata_admin",
        invitation_expires_days=14,
    )

    with patch("routers.sm_organisations.send_email_async", new_callable=AsyncMock):
        result = await create_self_managed_scheme(body, current_user=sa_user, _is_test_data=True)

    assert "tenant_id" in result
    assert "scheme_id" in result
    assert "invitation_id" in result
    cleanup.append(result["tenant_id"])


@pytest.mark.asyncio
async def test_create_sm_organisation_happy_path(cleanup, sa_user):
    from routers.sm_organisations import create_sm_organisation, CreateSmOrgRequest

    body = CreateSmOrgRequest(
        tenant_name="Test SM Co Pty Ltd",
        legal_name="Test SM Management Pty Ltd",
        jurisdiction="ACT",
        primary_admin_email="admin@test-sm-co.com.au",
        primary_admin_first_name="Jane",
        primary_admin_last_name="Doe",
        invitation_role="strata_admin",
    )
    with patch("routers.sm_organisations.send_email_async", new_callable=AsyncMock):
        result = await create_sm_organisation(body, current_user=sa_user, _is_test_data=True)

    assert result["tenant_id"]
    assert result["invitation_id"]
    cleanup.append(result["tenant_id"])


@pytest.mark.asyncio
async def test_list_sm_organisations_excludes_test_data(cleanup, pg, sa_user):
    from routers.sm_organisations import list_sm_organisations

    visible_tenant_id = uuid.uuid4()
    hidden_tenant_id = uuid.uuid4()
    cleanup.extend([str(visible_tenant_id), str(hidden_tenant_id)])

    await pg.execute(
        """INSERT INTO core.tenants
           (tenant_id, tenant_name, is_self_managed, status, is_test_data)
           VALUES ($1, 'Visible SM Org', FALSE, 'active', FALSE)""",
        visible_tenant_id,
    )
    await pg.execute(
        """INSERT INTO core.tenants
           (tenant_id, tenant_name, is_self_managed, status, is_test_data)
           VALUES ($1, 'Hidden Test SM Org', FALSE, 'active', TRUE)""",
        hidden_tenant_id,
    )

    result = await list_sm_organisations(
        include_self_managed=True,
        status="active",
        current_user=sa_user,
    )
    names = {item["tenant_name"] for item in result["items"]}

    assert "Visible SM Org" in names
    assert "Hidden Test SM Org" not in names


# ──────────────────────────────────────────────────────────────────────────
# Error paths
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_self_managed_invariant_rejects_existing_scheme(cleanup, pg):
    """A second scheme under a self-managed tenant must fail.

    Created via direct SQL (the spec doesn't expose a 'create scheme under
    existing self-managed tenant' endpoint — the trigger is the guard).
    """
    tenant_id = uuid.uuid4()
    cleanup.append(str(tenant_id))

    await pg.execute(
        """INSERT INTO core.tenants (tenant_id, tenant_name, is_self_managed, status, is_test_data)
           VALUES ($1, 'Existing Self-Managed', TRUE, 'active', TRUE)""",
        tenant_id,
    )
    await pg.execute(
        """INSERT INTO core.schemes
           (tenant_id, jurisdiction, scheme_number, scheme_name, status, is_test_data)
           VALUES ($1, 'ACT', 'TEST-EXIST-1', 'Existing', 'active', TRUE)""",
        tenant_id,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await pg.execute(
            """INSERT INTO core.schemes
               (tenant_id, jurisdiction, scheme_number, scheme_name, status, is_test_data)
               VALUES ($1, 'ACT', 'TEST-EXIST-2', 'Second-blocked', 'active', TRUE)""",
            tenant_id,
        )


@pytest.mark.asyncio
async def test_create_self_managed_invalid_jurisdiction_rejected():
    from routers.sm_organisations import CreateSelfManagedRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateSelfManagedRequest(
            scheme_number="X",
            scheme_name="X",
            jurisdiction="ZZ",
            ec_admin_email="x@x.com.au",
            ec_admin_first_name="X",
            ec_admin_last_name="Y",
        )


@pytest.mark.asyncio
async def test_create_self_managed_invalid_role_rejected():
    from routers.sm_organisations import CreateSelfManagedRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CreateSelfManagedRequest(
            scheme_number="X",
            scheme_name="X",
            ec_admin_email="x@x.com.au",
            ec_admin_first_name="X",
            ec_admin_last_name="Y",
            invitation_role="owner",  # not in allowed set
        )


@pytest.mark.asyncio
async def test_non_super_admin_rejected_with_403(sa_user):
    from fastapi import HTTPException
    from routers.sm_organisations import (
        create_self_managed_scheme,
        CreateSelfManagedRequest,
    )

    body = CreateSelfManagedRequest(
        scheme_number="X-403",
        scheme_name="403 test",
        ec_admin_email="x@403.com.au",
        ec_admin_first_name="X",
        ec_admin_last_name="Y",
    )
    non_sa = {**sa_user, "role": "strata_admin", "effective_role": "strata_admin"}
    with pytest.raises(HTTPException) as exc:
        await create_self_managed_scheme(body, current_user=non_sa)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_active_abn_rejected_with_409(cleanup, sa_user):
    from fastapi import HTTPException
    from routers.sm_organisations import create_sm_organisation, CreateSmOrgRequest

    abn = f"TEST{uuid.uuid4().hex[:7]}"
    body = CreateSmOrgRequest(
        tenant_name="First org",
        abn=abn,
        primary_admin_email="first@dup-abn.com.au",
        primary_admin_first_name="F",
        primary_admin_last_name="L",
    )
    with patch("routers.sm_organisations.send_email_async", new_callable=AsyncMock):
        first = await create_sm_organisation(body, current_user=sa_user)
    cleanup.append(first["tenant_id"])

    body2 = CreateSmOrgRequest(
        tenant_name="Second org with same ABN",
        abn=abn,
        primary_admin_email="second@dup-abn.com.au",
        primary_admin_first_name="F",
        primary_admin_last_name="L",
    )
    with patch("routers.sm_organisations.send_email_async", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as exc:
            await create_sm_organisation(body2, current_user=sa_user)
    assert exc.value.status_code == 409
    assert exc.value.detail.get("code") == "abn_already_active"
