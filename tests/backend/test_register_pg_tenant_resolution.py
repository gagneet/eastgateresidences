# @featuretrace:postgres-identity-foundation — Guards the register->core.users dual-write tenant resolution.
# Layer: test
# Data flow: /auth/register -> core.schemes lookup -> identity_repo.create_user_for_registration (building-scoped).
# Related: backend/routers/auth.py
#          backend/db_postgres/repos/identity_repo.py
"""Registration must resolve a REAL tenant, never derive one.

`/auth/register` used `uuid5(NAMESPACE_DNS, f"building-{building_id}")` as the Postgres
tenant. For East Gate that yields 928bd124-2840-57a6-9168-8991ccbe82ff, while the real
tenant is 9e9d75c2-bd92-4695-8487-1592018c3af9. `core.users.users_tenant_id_fkey`
references `core.tenants(tenant_id)`, no such row exists, so every insert failed, the
surrounding `except` swallowed it as a warning, and registration never created a
`core.users` row — while the login docstring asserted the opposite ("new accounts ...
are always in Postgres"). Login kept working only via its MongoDB fallback.

These are source-level guards on purpose: reproducing the failure needs a live Postgres
with a real tenant, and the bug's whole character was that it failed *silently*, so a
test that merely calls the endpoint and asserts a 200 would have passed throughout.
"""

import re
import uuid
from pathlib import Path

import pytest

AUTH = Path(__file__).resolve().parents[2] / "backend" / "routers" / "auth.py"
SOURCE = AUTH.read_text()

# The register block, isolated so these assertions cannot be satisfied by unrelated code.
REGISTER_PG_BLOCK = SOURCE.split("# Phase E: Postgres user creation")[1].split(
    "# ── Notification routing")[0]


def test_building_tenant_is_not_derived_from_a_uuid5_namespace():
    assert 'uuid5(_ns, f"building-{_building_id}")' not in REGISTER_PG_BLOCK, (
        "the derived-tenant bug is back: this id is not in core.tenants, so the "
        "users_tenant_id_fkey insert fails and is silently swallowed"
    )


def test_tenant_is_resolved_from_core_schemes():
    assert "get_scheme_by_number" in REGISTER_PG_BLOCK, (
        "registration must look the tenant up from core.schemes (building_id is the "
        "scheme_number), not compute one"
    )


def test_postgres_write_is_skipped_when_no_tenant_resolves():
    """Better to skip the write than to insert against a fabricated tenant id."""
    assert "if POSTGRES_AVAILABLE and _postgres_tenant_id:" in SOURCE, (
        "the create call must be gated on a resolved tenant"
    )


def test_the_phantom_tenant_really_does_differ_from_east_gates():
    """Pins the arithmetic behind the bug so the comment can't drift from reality."""
    derived = str(uuid.uuid5(uuid.NAMESPACE_DNS, "building-13195"))
    assert derived == "928bd124-2840-57a6-9168-8991ccbe82ff"
    assert derived != "9e9d75c2-bd92-4695-8487-1592018c3af9"


@pytest.mark.asyncio
async def test_scheme_lookup_returns_east_gates_real_tenant():
    """Integration check: skips when no live Postgres is configured."""
    try:
        from db_postgres.repos import identity_repo
        scheme = await identity_repo.get_scheme_by_number("13195")
    except Exception as exc:                      # pragma: no cover - env dependent
        pytest.skip(f"live Postgres unavailable: {exc}")
    if not scheme:
        pytest.skip("East Gate scheme 13195 not present in this database")
    assert str(scheme["tenant_id"]) == "9e9d75c2-bd92-4695-8487-1592018c3af9"
