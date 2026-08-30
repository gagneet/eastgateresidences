"""Postgres identity repository.

All user identity reads and writes go through this module.
MongoDB collections (db.users, db.memberships) are never touched here.

Pattern
-------
- Pre-auth lookups (login, invite-claim) call ``find_user_for_auth`` which
  invokes the ``core.find_user_for_auth(CITEXT)`` SECURITY DEFINER function
  — no tenant context required.
- All other queries require ``tenant_id`` and call ``set_tenant`` to satisfy
  the RLS policy on ``core.users``.
- Return dicts use the same key names as the legacy MongoDB user documents so
  downstream route guards need zero changes:
      ``id``, ``role``, ``email``, ``is_active``, ``is_approved``,
      ``building_id``, ``full_name``, etc.
  New keys: ``tenant_id``, ``user_uuid`` (raw UUID object).
"""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from db_postgres.session import async_session_context, set_tenant
from utils.test_data_flag import under_pytest

# RLS bypass sentinel UUID used in SECURITY DEFINER functions and pre-auth lookups.
# When app.tenant_id is set to this value, RLS policies permit cross-tenant queries.
# This is an explicit, named bypass; application code must never set this value.
_BYPASS_UUID = '00000000-0000-0000-0000-000000000000'


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    """Convert a SQLAlchemy Row (or asyncpg Record) to a plain dict."""
    return dict(row._mapping) if hasattr(row, '_mapping') else dict(row)


def _iso(value):
    """Coerce datetime → ISO string for downstream consumers.

    Mongo stored these as ISO strings; Postgres returns ``datetime`` objects.
    ``UserResponse`` types ``created_at`` etc. as ``str``, so callers passing
    raw rows need this on every datetime field.
    """
    return value.isoformat() if hasattr(value, "isoformat") else (value or None)


def _normalise_user(raw: dict) -> dict:
    """Map Postgres column names → legacy user dict shape for downstream compat."""
    from models.user import normalize_user_role  # local import to avoid circular

    role = normalize_user_role(str(raw.get("role", "guest")))
    status = str(raw.get("status", "active"))
    user_id = raw.get("user_id")

    return {
        # Primary identity
        "id": str(user_id) if user_id else None,
        "user_uuid": user_id,
        "tenant_id": str(raw["tenant_id"]) if raw.get("tenant_id") else None,
        "email": str(raw.get("email", "")),
        "full_name": raw.get("full_name") or "",
        "first_name": raw.get("first_name"),
        "last_name": raw.get("last_name"),
        "phone": raw.get("phone"),
        # Auth flags
        "role": role,
        "ec_position": raw.get("ec_position"),
        "is_active": bool(raw.get("is_active", True)),
        "is_approved": bool(raw.get("is_approved", False)),
        "status": status,
        # Building context — scheme_id UUID string (replaces legacy string like "13195")
        "building_id": str(raw["default_scheme_id"]) if raw.get("default_scheme_id") else None,
        # TOTP / MFA
        "totp_enabled": bool(raw.get("totp_enabled", False)),
        "mfa_required": bool(raw.get("mfa_required", False)),
        # Permissions
        "permission_overrides": raw.get("permission_overrides") or {},
        # Auth — included so the login handler can call verify_password directly.
        # Never exposed to the frontend (user_to_response strips it).
        "password_hash": raw.get("password_hash") or "",
        "created_at": _iso(raw.get("created_at")),
        "last_login_at": _iso(raw.get("last_login_at")),
        "last_login_ip": raw.get("last_login_ip"),
        # Rendered as "public (local)" by the dashboard; str() because asyncpg
        # returns inet columns as ipaddress objects, which are not JSON-serialisable.
        "last_login_public_ip": str(raw["last_login_public_ip"]) if raw.get("last_login_public_ip") else None,
        "last_login_local_ip": str(raw["last_login_local_ip"]) if raw.get("last_login_local_ip") else None,
        # Session revocation. This mapping is a whitelist, so a new core.users column is
        # invisible downstream until it is named here — get_current_user() cannot enforce
        # a revocation it never receives.
        "sessions_invalidated_at": _iso(raw.get("sessions_invalidated_at")),
        "session_keep_jti": raw.get("session_keep_jti"),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Pre-auth lookup (bypasses RLS via SECURITY DEFINER function)
# ──────────────────────────────────────────────────────────────────────────────

async def is_test_data_account(email: str) -> bool:
    """True when this email belongs to an ``is_test_data`` account.

    Defence in depth for the login path. ``core.find_user_for_auth()`` does not
    return ``is_test_data`` (changing its signature means a migration on a
    production auth path), so this is a separate, strictly additive lookup.

    Background: the pytest sweep never cleaned ``core.users``, so test accounts
    accumulated in production — 2,155 of 2,160 rows by 2026-08-25, 1,772 of them
    active super_admins sharing a constant password committed in the repo. The
    ``is_test_data`` flag was only ever a cleanup marker, so nothing stopped those
    rows authenticating. This makes the flag an auth gate as well.

    Reads through the RLS bypass sentinel because a test account can belong to any
    tenant, and the caller has no tenant context at login time.

    PERFORMANCE — known and accepted, revisit if core.users grows large.
    ``core.users`` is indexed on ``(tenant_id, email)``; this predicate is
    ``lower(email)`` with no tenant constraint, so neither index applies and the
    planner chooses a sequential scan (verified with EXPLAIN: cost 1206 at 2,160
    rows). That is deliberate for now: once the leaked test rows are purged the
    table holds ~5 rows, and adding a functional index means a migration on the
    production authentication path — more risk today than the scan costs.

    Revisit when ``core.users`` reaches a few thousand REAL rows: add
    ``CREATE INDEX CONCURRENTLY ... ON core.users (lower(email))`` and this
    becomes an index scan with no code change.
    """
    async with async_session_context() as session:
        await session.execute(
            text("SET LOCAL app.tenant_id = '00000000-0000-0000-0000-000000000000'")
        )
        found = await session.execute(
            text("SELECT is_test_data FROM core.users WHERE lower(email) = :email LIMIT 1"),
            {"email": email.strip().lower()},
        )
        row = found.first()
        return bool(row and row[0])


async def set_requires_activation(email: str, value: bool) -> bool:
    """Set (or clear) the activation gate on an account.

    Postgres is what ``/auth/login`` consults, so this flag — not the MongoDB
    is_active/is_approved pair — is what actually stops a sign-in. Returns False when
    the address is Mongo-only, which is a legitimate state rather than an error.
    """
    async with async_session_context() as session:
        await session.execute(
            text("SET LOCAL app.tenant_id = '00000000-0000-0000-0000-000000000000'")
        )
        result = await session.execute(
            text("""UPDATE core.users
                       SET requires_activation = :flag, updated_at = NOW()
                     WHERE lower(email) = :email"""),
            {"flag": bool(value), "email": email.strip().lower()},
        )
        await session.commit()
        return result.rowcount > 0


async def set_password_hash(email: str, password_hash: str) -> bool:
    """Propagate a new password hash to core.users.

    Login resolves Postgres FIRST (``find_user_for_auth``) and only falls back to
    MongoDB when the address is absent there. Every password-change path in
    ``routers/auth.py`` — reset, self-service change, and the admin bulk reset — wrote
    the new hash to MongoDB alone, and ``_PG_PROFILE_KEYS`` in the profile dual-write
    deliberately excludes password_hash. So for any user who exists in core.users, a
    password change updated a record that authentication never consults: the old
    password kept working and the new one did not.

    That had not surfaced because the accounts exercising login day to day had never
    reset a password. It surfaces immediately with owner activation, where setting a
    password IS the flow.

    Returns False when the address is not in core.users, which is the legitimate
    Mongo-only case, not an error.
    """
    async with async_session_context() as session:
        await session.execute(
            text("SET LOCAL app.tenant_id = '00000000-0000-0000-0000-000000000000'")
        )
        result = await session.execute(
            text("""UPDATE core.users
                       SET password_hash = :pw, updated_at = NOW()
                     WHERE lower(email) = :email"""),
            {"pw": password_hash, "email": email.strip().lower()},
        )
        await session.commit()
        return result.rowcount > 0


async def activation_state(email: str) -> dict | None:
    """Activation state for an account, or None when the address is unknown.

    Separate from ``find_user_for_auth()`` for the same reason as
    ``is_test_data_account()`` above: that function returns a fixed column list, and
    widening it means redefining a SECURITY DEFINER function on the production
    authentication path. This is strictly additive.

    Why the gate exists at all. Restoring East Gate's owners surfaced a split between
    the stores: MongoDB held 106 accounts as is_active=False with no password_hash,
    while PostgreSQL held the same accounts as is_active=TRUE, is_approved=TRUE, WITH
    their pre-purge password hashes. Login resolves Postgres first, so the Mongo state
    was decorative — an owner's old password still opened an account nobody had claimed.

    Reads under the RLS bypass sentinel because login has no tenant context yet.
    """
    async with async_session_context() as session:
        await session.execute(
            text("SET LOCAL app.tenant_id = '00000000-0000-0000-0000-000000000000'")
        )
        found = await session.execute(
            text("""SELECT requires_activation, activated_at
                    FROM core.users WHERE lower(email) = :email LIMIT 1"""),
            {"email": email.strip().lower()},
        )
        row = found.first()
        if not row:
            return None
        return {"requires_activation": bool(row[0]), "activated_at": row[1]}


async def mark_activated(email: str) -> bool:
    """Clear the activation gate once the owner has set their own password.

    Idempotent: re-running on an already-activated account is a no-op that still
    reports success, so a retried activation cannot look like a failure.
    """
    async with async_session_context() as session:
        await session.execute(
            text("SET LOCAL app.tenant_id = '00000000-0000-0000-0000-000000000000'")
        )
        result = await session.execute(
            text("""UPDATE core.users
                       SET requires_activation = FALSE,
                           activated_at = COALESCE(activated_at, NOW())
                     WHERE lower(email) = :email"""),
            {"email": email.strip().lower()},
        )
        await session.commit()
        return result.rowcount > 0


async def find_user_for_auth(email: str) -> dict | None:
    """Look up a user by email for authentication.

    Uses the ``core.find_user_for_auth()`` SECURITY DEFINER function so
    no tenant context (SET LOCAL) is required.  Returns None if not found.

    Always constant-time (caller must still call verify_password regardless
    of whether this returns a row, to prevent timing attacks).
    """
    async with async_session_context() as session:
        result = await session.execute(
            text("SELECT * FROM core.find_user_for_auth(:email)"),
            {"email": email.strip().lower()},
        )
        row = result.fetchone()
        if row is None:
            return None
        user = _normalise_user(_row_to_dict(row))
        if user.get("tenant_id"):
            await set_tenant(session, user["tenant_id"])
            user["ec_position"] = await _get_user_scheme_ec_position(
                session,
                user["id"],
                user.get("building_id"),
            )
        return user


# ──────────────────────────────────────────────────────────────────────────────
# Tenant-scoped reads
# ──────────────────────────────────────────────────────────────────────────────

async def get_user_by_id(user_id: str, tenant_id: str) -> dict | None:
    """Fetch a user by UUID within their tenant.  Returns None if not found."""
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text("""
                 SELECT u.*,
                        core.user_effective_role(u.user_id, u.default_scheme_id) AS computed_effective_role,
                        (
                            SELECT ura.ec_position
                            FROM core.user_role_assignments ura
                            WHERE ura.user_id = u.user_id
                              AND ura.role = CAST('ec_member' AS core.user_role)
                              AND ura.is_active = TRUE
                              AND (
                                    (u.default_scheme_id IS NOT NULL AND ura.scheme_id = u.default_scheme_id)
                                    OR (u.default_scheme_id IS NULL AND ura.scheme_id IS NULL)
                                  )
                            ORDER BY ura.granted_at DESC
                            LIMIT 1
                        ) AS ec_position
                 FROM core.users u
                 WHERE u.user_id = :uid
                 """),
            {"uid": str(user_id)},
        )
        row = result.fetchone()
        if row is None:
            return None
        d = _normalise_user(_row_to_dict(row))
        # Honour computed effective role (temp elevation lives in user_role_assignments)
        if row._mapping.get("computed_effective_role"):
            from models.user import normalize_user_role
            d["effective_role"] = normalize_user_role(str(row._mapping["computed_effective_role"]))
        return d


async def _get_user_scheme_ec_position(session, user_id: str | None, scheme_id: str | None) -> str | None:
    """Return the user's active EC office-bearer position for a scheme."""
    if not user_id:
        return None

    result = await session.execute(
        text("""
             SELECT ec_position
             FROM core.user_role_assignments
             WHERE user_id = CAST(:uid AS UUID)
               AND role = CAST('ec_member' AS core.user_role)
               AND is_active = TRUE
               AND scheme_id IS NOT DISTINCT FROM CAST(:sid AS UUID)
             ORDER BY granted_at DESC
             LIMIT 1
             """),
        {"uid": str(user_id), "sid": str(scheme_id) if scheme_id else None},
    )
    return result.scalar()


async def get_user_effective_role(user_id: str, scheme_id: str | None, tenant_id: str) -> str:
    """Call the DB-side user_effective_role() function and return the role string."""
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text("SELECT core.user_effective_role(:uid, :sid)"),
            {"uid": str(user_id), "sid": str(scheme_id) if scheme_id else None},
        )
        return str(result.scalar() or "guest")


# ──────────────────────────────────────────────────────────────────────────────
# Super admin cross-tenant lookup (bypasses RLS for SA building-switch)
# ──────────────────────────────────────────────────────────────────────────────

async def find_user_by_email_for_admin(email: str) -> dict | None:
    """Cross-tenant lookup of a user by email — for super-admin / IP-protection checks.

    Uses the RLS bypass sentinel that ``core.users.tenant_isolation`` honours
    (added in migration 0014). Unlike ``find_user_for_auth``, this helper does
    not filter on ``is_active`` so the IP-protection logic can still detect
    the protected admin even if their account has been deactivated.

    Returns the same shape as the other identity helpers (passes through
    ``_normalise_user``).
    """
    if not email:
        return None
    async with async_session_context() as session:
        # is_local=true (txn-scoped). The bypass MUST NOT survive past
        # commit — SQLAlchemy's default pool_reset_on_return='rollback'
        # does NOT clear SESSION-level GUCs, so set_config(..., false)
        # here would leak the bypass to the next user of this pooled
        # connection.  Audited and changed from false → true on
        # 2026-05-03 along with the parallel get_scheme_by_*() fix.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        result = await session.execute(
            text("""
                 SELECT u.user_id,
                        u.tenant_id,
                        u.email,
                        u.password_hash,
                        u.status,
                        u.role,
                        u.full_name,
                        u.first_name,
                        u.last_name,
                        u.is_active,
                        u.is_approved,
                        u.default_scheme_id,
                        u.totp_enabled,
                        u.mfa_required,
                        u.permission_overrides,
                        u.created_at,
                        u.last_login_at,
                        u.last_login_ip,
                        u.last_login_public_ip,
                        u.last_login_local_ip
                 FROM core.users u
                 WHERE u.email = :email LIMIT 1
                 """),
            {"email": email.strip().lower()},
        )
        row = result.fetchone()
        return _normalise_user(_row_to_dict(row)) if row else None


async def find_user_by_id_for_admin(user_id: str) -> dict | None:
    """Cross-tenant lookup of a user by UUID — for super-admin / IP-protection checks.

    Uses the RLS bypass sentinel. Returns None for non-UUID input so callers
    don't crash on legacy Mongo string ids.
    """
    if not user_id:
        return None
    try:
        UUID(str(user_id))
    except (ValueError, AttributeError):
        return None
    async with async_session_context() as session:
        # is_local=true: bypass scoped to this txn only.
        # See find_user_by_email_for_admin() for the full rationale.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        result = await session.execute(
            text("""
                 SELECT u.user_id,
                        u.tenant_id,
                        u.email,
                        u.password_hash,
                        u.status,
                        u.role,
                        u.full_name,
                        u.first_name,
                        u.last_name,
                        u.is_active,
                        u.is_approved,
                        u.default_scheme_id,
                        u.totp_enabled,
                        u.mfa_required,
                        u.permission_overrides,
                        u.created_at,
                        u.last_login_at,
                        u.last_login_ip,
                        u.last_login_public_ip,
                        u.last_login_local_ip
                 FROM core.users u
                 WHERE u.user_id = :uid LIMIT 1
                 """),
            {"uid": str(user_id)},
        )
        row = result.fetchone()
        return _normalise_user(_row_to_dict(row)) if row else None


async def upsert_protected_admin(
        email: str,
        password_hash: str,
        full_name: str,
        tenant_id: str,
) -> str:
    """Idempotently ensure the IP-protected super-admin exists in core.users.

    Used by the startup hook in ``server.py`` to translate the obfuscated
    credentials from ``_get_auth_admin()`` into a Postgres user. On every
    restart this rewrites the password hash so the obfuscated source remains
    the single source of truth.

    Returns the user's UUID as a string.
    """
    async with async_session_context() as session:
        await session.execute(
            text(f"SET LOCAL app.tenant_id = '{tenant_id}'")
        )
        result = await session.execute(
            text("""
                 INSERT INTO core.users
                 (tenant_id, email, full_name, password_hash, role,
                  is_active, is_approved)
                 VALUES (:tid, :email, :name, :pw_hash,
                         CAST('super_admin' AS core.user_role),
                         TRUE, TRUE) ON CONFLICT (tenant_id, email) DO
                 UPDATE
                     SET password_hash = EXCLUDED.password_hash,
                     full_name = EXCLUDED.full_name,
                     role = CAST ('super_admin' AS core.user_role),
                     is_active = TRUE,
                     is_approved = TRUE,
                     updated_at = NOW()
                     RETURNING CAST (user_id AS TEXT)
                 """),
            {"tid": tenant_id, "email": email.strip().lower(),
             "name": full_name, "pw_hash": password_hash},
        )
        return result.scalar()


async def get_scheme_by_number(scheme_number: str) -> dict | None:
    """Look up a scheme by plan_number (e.g. '13195').

    Used by super_admin X-Building-ID header resolution and by the
    POST /auth/switch-building cross-tenant fallback.

    Sets the RLS bypass sentinel (``app.tenant_id =
    '00000000-0000-0000-0000-000000000000'``) before the SELECT so the
    ``tenant_isolation_core_schemes`` policy from migration 0026 returns
    schemes belonging to OTHER tenants — that's the whole point of this
    helper. Without the explicit bypass, the SELECT silently returns
    no rows because the GUC is unset on a fresh pool connection,
    causing the caller to 404 on a scheme that demonstrably exists.
    Same fix applied to ``get_scheme_by_id`` below.
    """
    async with async_session_context() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        result = await session.execute(
            text("""
                 SELECT scheme_id, tenant_id, scheme_number, scheme_name, status
                 FROM core.schemes
                 WHERE scheme_number = :num
                   AND status = 'active'
                   AND COALESCE(is_test_data, FALSE) = FALSE LIMIT 1
                 """),
            {"num": str(scheme_number)},
        )
        row = result.fetchone()
        return _row_to_dict(row) if row else None


async def get_scheme_by_id(scheme_id: str) -> dict | None:
    """Look up a scheme by UUID.  Cross-tenant admin path.

    Returns None for non-UUID input (e.g. a legacy plan-number like ``"13195"``)
    so the calling fallback chain can try ``get_scheme_by_number`` next without
    asyncpg raising a parameter-binding error against the ``uuid`` column.

    Sets the RLS bypass sentinel before the SELECT for the same reason
    documented on ``get_scheme_by_number`` above.
    """
    if not scheme_id:
        return None
    try:
        UUID(str(scheme_id))
    except (ValueError, AttributeError):
        return None
    async with async_session_context() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        result = await session.execute(
            text("""
                 SELECT scheme_id, tenant_id, scheme_number, scheme_name, status
                 FROM core.schemes
                 WHERE scheme_id = :sid
                   AND status = 'active'
                   AND COALESCE(is_test_data, FALSE) = FALSE LIMIT 1
                 """),
            {"sid": str(scheme_id)},
        )
        row = result.fetchone()
        return _row_to_dict(row) if row else None


async def list_all_active_schemes() -> list[dict]:
    """Cross-tenant list of every active scheme — for super-admin platform view.

    Uses the RLS bypass sentinel that ``core.schemes.tenant_isolation``
    honours (added in migration 0026). Sorted by tenant name then scheme
    name so the SA's building switcher groups schemes by Strata Management
    Organisation.
    """
    async with async_session_context() as session:
        # is_local=true: bypass scoped to this txn only.
        # See find_user_by_email_for_admin() for the full rationale.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        result = await session.execute(
            text("""
                 SELECT s.scheme_id::TEXT, s.scheme_number,
                        s.scheme_name,
                        s.tenant_id::TEXT, t.tenant_name,
                        s.jurisdiction::TEXT, s.is_demo,
                        (s.status = 'active') AS is_active
                 FROM core.schemes s
                          JOIN core.tenants t ON t.tenant_id = s.tenant_id
                 WHERE s.status = 'active'
                   AND COALESCE(s.is_test_data, FALSE) = FALSE
                   AND COALESCE(t.is_test_data, FALSE) = FALSE
                 ORDER BY t.tenant_name, s.scheme_name
                 """)
        )
        return [_row_to_dict(row) for row in result.fetchall()]


async def list_schemes_for_user(user_id: str) -> list[dict]:
    """Every active scheme this user has a role assignment for, across orgs.

    Cross-tenant read via the bypass sentinel — supports the multi-unit /
    multi-building owner case (one login, units across multiple Strata
    Management Organisations) and the Strata Manager whose tenant context
    is the same tenant their schemes live in.
    """
    if not user_id:
        return []
    async with async_session_context() as session:
        # is_local=true: bypass scoped to this txn only.
        # See find_user_by_email_for_admin() for the full rationale.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        result = await session.execute(
            text("""
                 SELECT DISTINCT s.scheme_id::TEXT, s.scheme_number,
                                 s.scheme_name,
                                 s.tenant_id::TEXT, t.tenant_name,
                                 s.jurisdiction::TEXT, s.is_demo,
                                 (s.status = 'active') AS is_active
                 FROM core.user_role_assignments ura
                          JOIN core.schemes s ON s.scheme_id = ura.scheme_id
                          JOIN core.tenants t ON t.tenant_id = s.tenant_id
                 WHERE ura.user_id = :uid
                   AND ura.is_active = TRUE
                   AND ura.scheme_id IS NOT NULL
                   AND s.status = 'active'
                   AND COALESCE(s.is_test_data, FALSE) = FALSE
                   AND COALESCE(t.is_test_data, FALSE) = FALSE
                 ORDER BY t.tenant_name, s.scheme_name
                 """),
            {"uid": str(user_id)},
        )
        return [_row_to_dict(row) for row in result.fetchall()]


async def get_user_scheme_ids(user_id: str, tenant_id: str) -> list[str]:
    """Return list of active scheme_id UUIDs the user has a role assignment for."""
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text("""
                 SELECT DISTINCT scheme_id::TEXT
                 FROM core.user_role_assignments
                 WHERE user_id = :uid
                   AND is_active = TRUE
                   AND scheme_id IS NOT NULL
                 """),
            {"uid": str(user_id)},
        )
        return [row[0] for row in result.fetchall()]


async def is_user_in_scheme(user_id: str, scheme_id: str, tenant_id: str) -> bool:
    """Return True if user has an active role assignment in the given scheme."""
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text("""
                 SELECT 1
                 FROM core.user_role_assignments
                 WHERE user_id = :uid
                   AND scheme_id = :sid
                   AND is_active = TRUE LIMIT 1
                 """),
            {"uid": str(user_id), "sid": str(scheme_id)},
        )
        return result.fetchone() is not None


async def list_active_users_for_scheme(scheme_number: str) -> list[dict]:
    """Return every active core.users row linked to the scheme (by scheme_number).

    A user is "linked" if either:
      - core.user_role_assignments has an active row for the scheme, OR
      - core.user_units has an active (valid_to IS NULL) row whose scheme_id
        matches.

    The lot_number array surfaces the user's current unit(s). For owners with
    party_id=NULL (partner-of-owner pattern), the array still reflects their
    user_units row — consistent with the "owner-permissions" read path.

    Used by GET /users to surface Postgres-only users that haven't yet been
    backfilled into the legacy Mongo memberships/users collections during the
    Mongo→Postgres cutover.

    RLS pattern: core.user_units and core.lots have NO bypass clause — only
    core.schemes/core.users honour the bypass sentinel. We therefore look up
    the scheme via bypass, then switch GUC to the resolved tenant_id before
    joining user_units/lots so RLS returns the real rows. Done in a single
    transaction so a pooled connection cannot leak the bypass state.
    """
    if not scheme_number:
        return []
    async with async_session_context() as session:
        # Step 1: resolve scheme → tenant_id via bypass (cross-tenant safe).
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": _BYPASS_UUID},
        )
        scheme_row = (await session.execute(
            text("""
                 SELECT scheme_id::TEXT AS scheme_id, tenant_id::TEXT AS tenant_id
                 FROM core.schemes
                 WHERE scheme_number = :num
                   AND status = 'active'
                   AND COALESCE(is_test_data, FALSE) = FALSE
                 LIMIT 1
                 """),
            {"num": str(scheme_number)},
        )).fetchone()
        if scheme_row is None:
            return []
        scheme_id = scheme_row._mapping["scheme_id"]
        tenant_id = scheme_row._mapping["tenant_id"]

        # Step 2: switch GUC to the scheme's tenant so core.user_units and
        # core.lots return rows (they have no bypass clause).
        await session.execute(
            text("SELECT set_config('app.tenant_id', :u, true)"),
            {"u": tenant_id},
        )
        result = await session.execute(
            text("""
                SELECT
                    u.user_id::TEXT          AS id,
                    u.email::TEXT            AS email,
                    COALESCE(NULLIF(u.full_name, ''),
                             NULLIF(TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.last_name,'')), ''),
                             '')             AS full_name,
                    u.role::TEXT             AS role,
                    u.effective_role::TEXT   AS effective_role,
                    u.is_active              AS is_active,
                    u.is_approved            AS is_approved,
                    u.status::TEXT           AS status,
                    u.is_test_data           AS is_test_data,
                    u.phone                  AS phone,
                    u.created_at             AS created_at,
                    u.last_login_at          AS last_login_at,
                    u.last_login_ip          AS last_login_ip,
                    u.last_login_public_ip   AS last_login_public_ip,
                    u.last_login_local_ip    AS last_login_local_ip,
                    u.is_name_flagged        AS is_name_flagged,
                    u.flag_reason            AS flag_reason,
                    u.totp_enabled           AS totp_enabled,
                    u.tenant_id::TEXT        AS tenant_id,
                    (
                        SELECT ura.ec_position
                        FROM core.user_role_assignments ura
                        WHERE ura.user_id = u.user_id
                          AND ura.scheme_id = :sid
                          AND ura.role = CAST('ec_member' AS core.user_role)
                          AND ura.is_active = TRUE
                        ORDER BY ura.granted_at DESC
                        LIMIT 1
                    )                        AS ec_position,
                    COALESCE(
                        ARRAY_AGG(DISTINCT l.lot_number ORDER BY l.lot_number)
                            FILTER (WHERE l.lot_number IS NOT NULL),
                        ARRAY[]::TEXT[]
                    )                        AS lot_numbers,
                    -- Prefer the strongest entitlement when a user has
                    -- multiple user_units rows with mixed relationships:
                    -- owner > tenant > family > agent. MIN() alone would
                    -- pick 'agent' lexicographically — the opposite of
                    -- what callers expect.
                    CASE
                        WHEN BOOL_OR(uu.relationship = 'owner')  THEN 'owner'
                        WHEN BOOL_OR(uu.relationship = 'tenant') THEN 'tenant'
                        WHEN BOOL_OR(uu.relationship = 'family') THEN 'family'
                        WHEN BOOL_OR(uu.relationship = 'agent')  THEN 'agent'
                        ELSE NULL
                    END                      AS relationship
                FROM core.users u
                LEFT JOIN core.user_units uu
                    ON uu.user_id = u.user_id
                   AND uu.scheme_id = :sid
                   AND uu.valid_to IS NULL
                   AND COALESCE(uu.is_test_data, FALSE) = FALSE
                LEFT JOIN core.lots l
                    ON l.lot_id = uu.lot_id
                WHERE u.tenant_id = :tid
                  AND COALESCE(u.is_test_data, FALSE) = FALSE
                  AND u.status = 'active'
                  AND (
                        uu.user_unit_id IS NOT NULL
                        OR EXISTS (
                            SELECT 1 FROM core.user_role_assignments ura
                            WHERE ura.user_id = u.user_id
                              AND ura.scheme_id = :sid
                              AND ura.is_active = TRUE
                        )
                  )
                GROUP BY u.user_id
                """),
            {"sid": scheme_id, "tid": tenant_id},
        )
        rows = result.fetchall()
        return [_row_to_dict(row) for row in rows]


# ──────────────────────────────────────────────────────────────────────────────
# Writes
# ──────────────────────────────────────────────────────────────────────────────

async def revoke_other_sessions(user_id: str, tenant_id: str, keep_jti: str | None) -> bool:
    """Invalidate every JWT for this user except the one identified by ``keep_jti``.

    Sets the revocation instant to now(); get_current_user() then rejects any token
    whose ``iat`` is at or before it, apart from ``keep_jti``. See migration
    0101_session_revocation for why the surviving token is named rather than inferred
    from a timestamp.

    Returns True when a row was updated. False means no such user in this tenant — the
    caller must treat that as a failure, not a silent success: reporting "signed out
    everywhere" while nothing was revoked is worse than an error.
    """
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text("""
                 UPDATE core.users
                    SET sessions_invalidated_at = now(),
                        session_keep_jti = :jti,
                        updated_at = now()
                  WHERE user_id = :uid
                 """),
            {"uid": str(user_id), "jti": str(keep_jti) if keep_jti else None},
        )
        await session.commit()
        return (result.rowcount or 0) > 0


async def update_user_profile(user_id: str, fields: dict) -> bool:
    """Dual-write path: keep core.users in sync when the MongoDB user document is updated.

    Called by PUT /users/{user_id} immediately after the MongoDB write so the
    Postgres side stays consistent.  Idempotent — safe to call even when the user
    has not yet been migrated to Postgres (returns False and logs, never raises).

    Uses the RLS bypass sentinel so no tenant_id is required — the caller (server.py
    update_user) already verified the user's building membership before reaching this
    point.

    Only the keys present in *fields* are updated; unrecognised keys are silently
    ignored so callers can pass the raw update_dict without filtering.

    Updatable fields (mirrors columns in core.users):
        full_name, first_name, last_name, phone, email, role,
        is_active, is_approved, status,
        is_name_flagged, flag_reason, totp_enabled, totp_verified_at

    Returns:
        True  — row found and updated in Postgres
        False — user_id not a valid UUID or row not found in Postgres (Mongo-only user)
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    if not user_id:
        return False
    try:
        UUID(str(user_id))
    except (ValueError, AttributeError):
        # Legacy Mongo string id (not a UUID) — skip silently
        return False

    # Map of incoming keys → Postgres column names (only whitelisted columns).
    _ALLOWED: dict[str, str] = {
        "full_name": "full_name",
        "first_name": "first_name",
        "last_name": "last_name",
        "phone": "phone",
        "phone_home": "phone",        # consolidate into phone for now
        "email": "email",
        "role": "role",
        "is_active": "is_active",
        "is_approved": "is_approved",
        "status": "status",
        "is_name_flagged": "is_name_flagged",
        "flag_reason": "flag_reason",
        "totp_enabled": "totp_enabled",
    }

    pg_fields: dict[str, object] = {}
    for src_key, pg_col in _ALLOWED.items():
        if src_key in fields and fields[src_key] is not None:
            pg_fields[pg_col] = fields[src_key]

    if not pg_fields:
        if "ec_position" not in fields:
            return False  # nothing to update

    # Build parameterised SET clause
    assignments = ", ".join(f"{col} = :{col}" for col in pg_fields)
    params = dict(pg_fields)
    params["uid"] = str(user_id)

    # role must be cast to the enum type
    if "role" in params:
        assignments = assignments.replace(
            "role = :role", "role = CAST(:role AS core.user_role)"
        )
    # status must be cast to the enum type. That type is core.record_status
    # (draft|active|inactive|archived) — there is no core.user_status, and casting
    # to it raised UndefinedObjectError inside the broad except below, which logged
    # "non-fatal Postgres sync error" and returned False. Every status change
    # therefore stayed Mongo-only, including archiving a user, so an archived
    # account went on being served by list_active_users_for_scheme (which filters
    # u.status = 'active'). Fixed 2026-08-27.
    if "status" in params:
        assignments = assignments.replace(
            "status = :status", "status = CAST(:status AS core.record_status)"
        )

    sql = text(f"""
        UPDATE core.users
           SET {assignments},
               updated_at = NOW()
         WHERE user_id = :uid
    """) if pg_fields else None

    try:
        async with async_session_context() as session:
            # Use RLS bypass so super_admin can update any user regardless of tenant.
            await session.execute(
                text("SELECT set_config('app.tenant_id', :u, true)"),
                {"u": _BYPASS_UUID},
            )
            updated = False
            if sql is not None:
                result = await session.execute(sql, params)
                updated = result.rowcount > 0
            if "ec_position" in fields:
                ec_result = await session.execute(
                    text("""
                         UPDATE core.user_role_assignments
                         SET ec_position = :ec_position
                         WHERE user_id = CAST(:uid AS UUID)
                           AND role = CAST('ec_member' AS core.user_role)
                           AND is_active = TRUE
                         """),
                    {"uid": str(user_id), "ec_position": fields["ec_position"]},
                )
                updated = updated or ec_result.rowcount > 0
            if not updated:
                _log.debug(
                    "update_user_profile: user_id=%s not found in core.users (Mongo-only user)",
                    user_id,
                )
            return updated
    except Exception as exc:
        # Non-fatal: Postgres may be unavailable or the user may genuinely not
        # exist in core.users yet (pre-migration).  Log and continue; MongoDB
        # write already succeeded.
        _log.warning(
            "update_user_profile: non-fatal Postgres sync error for user_id=%s: %s",
            user_id,
            exc,
        )
        return False


def _under_pytest() -> bool:
    """Thin forwarder to the canonical owner in ``utils/test_data_flag.py``.

    Kept so this module's existing call sites read unchanged. The concept moved out
    on 2026-08-29 when ``cutover_status_service.record_shadow_diff`` became a third
    consumer — see docs/architecture/canonical_owners.yaml (concept: test-data-flag)
    for why a second copy is the thing being prevented.
    """
    return under_pytest()


async def create_user(data: dict, tenant_id: str) -> str:
    """Insert a new user into core.users.  Returns the new user_id as a string.

    ``data`` keys: email, full_name, password_hash, role, [first_name],
    [last_name], [phone], [is_approved], [default_scheme_id], [is_test_data]
    """
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text("""
                 INSERT INTO core.users
                 (tenant_id, email, full_name, first_name, last_name, phone,
                  password_hash, role, is_active, is_approved, default_scheme_id, is_test_data)
                 VALUES (:tenant_id, :email, :full_name, :first_name, :last_name, :phone,
                         :password_hash, CAST(:role AS core.user_role), TRUE, :is_approved,
                         :default_scheme_id, :is_test_data) RETURNING user_id::TEXT
                 """),
            {
                "tenant_id": str(tenant_id),
                "email": str(data["email"]).strip().lower(),
                "full_name": data.get("full_name") or "",
                "first_name": data.get("first_name"),
                "last_name": data.get("last_name"),
                "phone": data.get("phone"),
                "password_hash": data.get("password_hash") or "",
                "role": str(data.get("role", "owner")),
                "is_approved": bool(data.get("is_approved", False)),
                "default_scheme_id": str(data["default_scheme_id"]) if data.get("default_scheme_id") else None,
                "is_test_data": bool(data.get("is_test_data", False)) or _under_pytest(),
            },
        )
        return result.scalar()


async def create_user_for_registration(
        email: str,
        password_hash: str,
        full_name: str,
        role: str,
        tenant_id: str,
        phone: str | None = None,
        is_approved: bool = False,
        is_test_data: bool = False,
) -> str:
    """Create a user for public registration.

    Idempotent: if email already exists for this tenant, returns existing user_id.

    Args:
        email: User email (lowercased, normalized)
        password_hash: Hashed password
        full_name: Full name
        role: User role (owner, tenant, guest, etc.)
        tenant_id: Tenant/scheme UUID
        phone: Optional phone number
        is_approved: Whether user is pre-approved (default False = pending)
        is_test_data: Mark as test data for cleanup

    Returns:
        user_id as string
    """
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        email_normalized = str(email).strip().lower()

        # Check if user already exists for this email + tenant
        existing = await session.execute(
            text("""
                 SELECT user_id::TEXT
                 FROM core.users
                 WHERE tenant_id = :tenant_id
                   AND email = :email LIMIT 1
                 """),
            {"tenant_id": str(tenant_id), "email": email_normalized},
        )
        existing_id = existing.scalar()
        if existing_id:
            return existing_id  # Idempotent: return existing user

        # Insert new user
        result = await session.execute(
            text("""
                 INSERT INTO core.users
                 (tenant_id, email, full_name, phone, password_hash, role, is_active, is_approved, is_test_data)
                 VALUES (:tenant_id, :email, :full_name, :phone, :password_hash, CAST(:role AS core.user_role), TRUE,
                         :is_approved, :is_test_data) RETURNING user_id::TEXT
                 """),
            {
                "tenant_id": str(tenant_id),
                "email": email_normalized,
                "full_name": str(full_name) or "",
                "phone": str(phone) if phone else None,
                "password_hash": str(password_hash),
                "role": str(role),
                "is_approved": bool(is_approved),
                # See _under_pytest(): a test exercising the real registration
                # handler reaches this write without ever calling it directly.
                "is_test_data": bool(is_test_data) or _under_pytest(),
            },
        )
        return result.scalar()


async def add_role_assignment(
        user_id: str,
        role: str,
        tenant_id: str,
        scheme_id: str | None = None,
        granted_by: str | None = None,
        ec_position: str | None = None,
) -> None:
    """Upsert a role assignment for the user."""
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            text("""
                 INSERT INTO core.user_role_assignments
                 (tenant_id, user_id, scheme_id, role, ec_position, granted_by, is_active)
                 VALUES (:tenant_id, :user_id, :scheme_id, CAST(:role AS core.user_role), :ec_position, :granted_by,
                         TRUE) ON CONFLICT (user_id, scheme_id, role)
                 WHERE scheme_id IS NOT NULL
                     DO
                 UPDATE SET is_active = TRUE,
                            granted_at = NOW(),
                            ec_position = COALESCE(EXCLUDED.ec_position, core.user_role_assignments.ec_position)
                 """),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "scheme_id": str(scheme_id) if scheme_id else None,
                "role": str(role),
                "ec_position": ec_position,
                "granted_by": str(granted_by) if granted_by else None,
            },
        )


async def set_scheme_role(
        user_id: str,
        tenant_id: str,
        scheme_id: str,
        role: str,
        ec_position: str | None = None,
        granted_by: str | None = None,
) -> None:
    """Make ``role`` the user's single active role for ``scheme_id``.

    Unlike :func:`add_role_assignment` (which only inserts/reactivates and leaves
    any prior role active), this deactivates the user's *other* active role rows
    for the scheme and then upserts the target role active — so a role *change*
    propagated from the admin UI results in exactly one active role, matching what
    the admin set. Used by the identity write-cutover (GAP-IDENTITY-UI-DB-001) so
    ``core.user_effective_role()`` (which ``/auth/me`` reads) reflects the change.

    Atomic: both statements run in one transaction, so if the upsert fails the
    deactivation rolls back — the user can never be left with no active role.
    ``role`` must be a valid ``core.user_role`` enum value (the 10 UserRole
    strings); an invalid value raises inside the transaction and nothing commits.
    """
    async with async_session_context() as session:
        await set_tenant(session, str(tenant_id))
        await session.execute(
            text("""
                 UPDATE core.user_role_assignments
                    SET is_active = FALSE
                  WHERE user_id = :uid
                    AND scheme_id = :sid
                    AND role <> CAST(:role AS core.user_role)
                    AND is_active = TRUE
                 """),
            {"uid": str(user_id), "sid": str(scheme_id), "role": str(role)},
        )
        await session.execute(
            text("""
                 INSERT INTO core.user_role_assignments
                 (tenant_id, user_id, scheme_id, role, ec_position, granted_by, is_active)
                 VALUES (:tenant_id, :uid, :sid, CAST(:role AS core.user_role), :ec_position, :granted_by,
                         TRUE) ON CONFLICT (user_id, scheme_id, role)
                 WHERE scheme_id IS NOT NULL
                     DO
                 UPDATE SET is_active = TRUE,
                            granted_at = NOW(),
                            ec_position = COALESCE(EXCLUDED.ec_position, core.user_role_assignments.ec_position)
                 """),
            {
                "tenant_id": str(tenant_id),
                "uid": str(user_id),
                "sid": str(scheme_id),
                "role": str(role),
                "ec_position": ec_position,
                "granted_by": str(granted_by) if granted_by else None,
            },
        )


async def update_last_login(
    user_id: str,
    tenant_id: str,
    ip: str | None = None,
    *,
    public_ip: str | None = None,
    local_ip: str | None = None,
) -> None:
    """Update last_login_at, last_login_ip, and the public/local pair.

    ``ip`` keeps its COALESCE behaviour so a login that could not resolve an
    address does not erase the last known one.

    The public/local pair is written UNCONDITIONALLY, including NULL. That
    difference is deliberate: these two columns describe *this* login, and
    leaving a stale public address behind after a login that established none
    would show the dashboard an address the session never used. NULL here means
    "no public address was established", which is the diagnosis worth surfacing
    (migration 0094).

    Cast to ``inet`` explicitly — the columns are ``inet`` and asyncpg would
    otherwise bind a Python ``str`` as text.
    """
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            text("""
                 UPDATE core.users
                 SET last_login_at        = NOW(),
                     last_login_ip        = COALESCE(:ip, last_login_ip),
                     last_login_public_ip = CAST(NULLIF(:public_ip, '') AS INET),
                     last_login_local_ip  = CAST(NULLIF(:local_ip, '') AS INET),
                     updated_at           = NOW()
                 WHERE user_id = :uid
                 """),
            {
                "uid": str(user_id),
                "ip": ip,
                "public_ip": public_ip or "",
                "local_ip": local_ip or "",
            },
        )


# ──────────────────────────────────────────────────────────────────────────────
# Invitation helpers
# ──────────────────────────────────────────────────────────────────────────────

def generate_invite_token() -> tuple[str, bytes]:
    """Generate a raw invite token and its SHA-256 hash.

    Returns:
        (raw_token, sha256_hash_bytes)
    The raw_token is sent in the email; only the hash is stored in the DB.
    """
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).digest()
    return raw, digest


async def create_invitation(
        tenant_id: str,
        scheme_id: str | None,
        email: str,
        invited_role: str,
        invited_by: str,
        ttl_hours: int = 72,
        prefill: dict | None = None,
) -> tuple[str, str]:
    """Create a user_invitation row.

    Returns:
        (invitation_id, raw_token)  — raw_token must be sent in the email,
        only the SHA-256 hash is persisted.
    """
    raw_token, token_hash = generate_invite_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    p = prefill or {}

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text("""
                 INSERT INTO core.user_invitations
                 (tenant_id, scheme_id, email, invited_role, invited_by,
                  claim_token_sha256, expires_at,
                  prefill_first_name, prefill_last_name, prefill_phone)
                 VALUES (:tenant_id, :scheme_id, :email, CAST(:role AS core.user_role), :invited_by,
                         :token_hash, :expires_at,
                         :first_name, :last_name, :phone) RETURNING invitation_id::TEXT
                 """),
            {
                "tenant_id": str(tenant_id),
                "scheme_id": str(scheme_id) if scheme_id else None,
                "email": email.strip().lower(),
                "role": str(invited_role),
                "invited_by": str(invited_by),
                "token_hash": token_hash,
                "expires_at": expires_at,
                "first_name": p.get("first_name"),
                "last_name": p.get("last_name"),
                "phone": p.get("phone"),
            },
        )
        inv_id = result.scalar()
    return inv_id, raw_token


async def find_invitation_by_token(raw_token: str) -> dict | None:
    """Find an unclaimed, unexpired invitation by raw token.

    Hashes the raw token and looks up ``claim_token_sha256``.
    Returns None if not found, expired, or already claimed.

    Uses the RLS bypass sentinel (same as find_user_for_auth) since
    the tenant_id is unknown before the invitation is claimed.
    """
    digest = hashlib.sha256(raw_token.encode()).digest()
    async with async_session_context() as session:
        # Set bypass sentinel so RLS permits cross-tenant lookup
        await session.execute(
            text("SELECT set_config('app.tenant_id', :bypass_uuid, true)"),
            {"bypass_uuid": _BYPASS_UUID},
        )
        result = await session.execute(
            text("""
                 SELECT invitation_id,
                        tenant_id,
                        scheme_id,
                        email,
                        invited_role,
                        invited_by,
                        expires_at,
                        prefill_first_name,
                        prefill_last_name,
                        prefill_phone
                 FROM core.user_invitations
                 WHERE claim_token_sha256 = :hash
                   AND claimed_at IS NULL
                   AND cancelled_at IS NULL
                   AND expires_at > NOW() LIMIT 1
                 """),
            {"hash": digest},
        )
        row = result.fetchone()
        return _row_to_dict(row) if row else None


async def claim_invitation(invitation_id: str, claimed_user_id: str, tenant_id: str) -> None:
    """Mark invitation as claimed."""
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        await session.execute(
            text("""
                 UPDATE core.user_invitations
                 SET claimed_at      = NOW(),
                     claimed_user_id = :user_id
                 WHERE invitation_id = :inv_id
                 """),
            {"inv_id": str(invitation_id), "user_id": str(claimed_user_id)},
        )


async def refresh_invitation_token(invitation_id: str, tenant_id: str, ttl_hours: int = 72) -> str:
    """Generate and persist a fresh token for an unclaimed invitation.

    Overwrites ``claim_token_sha256`` and extends ``expires_at``.
    Returns the raw (unhashed) token to embed in the resend email.
    Raises HTTPException 404 if the invitation is not found or already claimed.
    """
    from fastapi import HTTPException  # local import to avoid circular
    raw_token, token_hash = generate_invite_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text("""
                 UPDATE core.user_invitations
                 SET claim_token_sha256 = :hash,
                     expires_at         = :expires_at
                 WHERE invitation_id = :inv_id
                   AND claimed_at   IS NULL
                   AND cancelled_at IS NULL
                 RETURNING invitation_id
                 """),
            {"hash": token_hash, "expires_at": expires_at, "inv_id": str(invitation_id)},
        )
        if not result.fetchone():
            raise HTTPException(status_code=404, detail="Invitation not found, already claimed, or cancelled.")
    return raw_token


async def find_invitation_by_id(invitation_id: str, tenant_id: str) -> dict | None:
    """Fetch invitation metadata by primary key (for resend flow)."""
    async with async_session_context() as session:
        await set_tenant(session, tenant_id)
        result = await session.execute(
            text("""
                 SELECT invitation_id, tenant_id, scheme_id, email,
                        invited_role, invited_by, expires_at,
                        prefill_first_name, prefill_last_name
                 FROM core.user_invitations
                 WHERE invitation_id = :inv_id
                   AND claimed_at   IS NULL
                   AND cancelled_at IS NULL
                 LIMIT 1
                 """),
            {"inv_id": str(invitation_id)},
        )
        row = result.fetchone()
        return _row_to_dict(row) if row else None
