"""rls_auth_bypass

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-07

Fixes the pre-auth lookup problem introduced in 0013.

Root cause: ``strataos_user`` is both the table owner and the sole app
query role.  ``FORCE ROW LEVEL SECURITY`` (set in 0012) forces the owner
to go through RLS.  The ``find_user_for_auth`` SECURITY DEFINER function
is also owned by ``strataos_user`` so it is also subject to RLS.  Since
``app.tenant_id`` is not set before the function is called, the RLS
policy filters ALL rows.

Fix:
1.  Drop and recreate the ``core.users`` RLS policy to also permit rows
    when ``app.tenant_id = '00000000-0000-0000-0000-000000000000'`` (the
    bypass sentinel UUID — deliberately a null UUID that is never a real
    tenant).
2.  Update ``core.find_user_for_auth`` to set the sentinel before
    querying, then reset it so the bypass cannot persist beyond the
    function call.

This is an explicit, named bypass for SECURITY DEFINER functions only.
Normal application code must never set the sentinel — the application
layer enforces this; any such code would be a programming error.
"""
from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None

_BYPASS_UUID = '00000000-0000-0000-0000-000000000000'


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # 1.  Drop old RLS policy on core.users and recreate with bypass clause
    # ──────────────────────────────────────────────────────────────────────
    # Policy name used in 0012 — must match
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0014_rls_auth_bypass.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON core.users")
    op.execute(f"""
        CREATE POLICY tenant_isolation ON core.users
        USING (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '{_BYPASS_UUID}'
        )
    """)

    # ──────────────────────────────────────────────────────────────────────
    # 2.  Recreate find_user_for_auth using the bypass sentinel
    # ──────────────────────────────────────────────────────────────────────
    op.execute(f"""
        CREATE OR REPLACE FUNCTION core.find_user_for_auth(p_email CITEXT)
        RETURNS TABLE (
            user_id              UUID,
            tenant_id            UUID,
            email                CITEXT,
            password_hash        TEXT,
            status               core.record_status,
            role                 core.user_role,
            full_name            TEXT,
            first_name           TEXT,
            last_name            TEXT,
            is_active            BOOLEAN,
            is_approved          BOOLEAN,
            default_scheme_id    UUID,
            totp_enabled         BOOLEAN,
            mfa_required         BOOLEAN,
            permission_overrides JSONB
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = core, public
        AS $$
        DECLARE
            prev_tenant TEXT;
        BEGIN
            -- Save current GUC value and set bypass sentinel so the RLS
            -- policy permits cross-tenant lookup.
            prev_tenant := current_setting('app.tenant_id', true);
            PERFORM set_config('app.tenant_id', '{_BYPASS_UUID}', true);

            RETURN QUERY
            SELECT
                u.user_id,
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
                u.permission_overrides
            FROM core.users u
            WHERE u.email = p_email
              AND u.is_active = TRUE
            LIMIT 1;

            -- Restore previous tenant context (may be empty for a pre-auth call)
            PERFORM set_config('app.tenant_id', coalesce(prev_tenant, ''), true);
        END;
        $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION core.find_user_for_auth(CITEXT) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION core.find_user_for_auth(CITEXT) TO strataos_user")


def downgrade() -> None:
    # Restore the original strict policy (breaks pre-auth lookup — intentional,
    # downgrade is forward-only per ADR-009)
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0014_rls_auth_bypass.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON core.users")
    op.execute("""
        CREATE POLICY tenant_isolation ON core.users
        USING (tenant_id = core.current_tenant_id())
    """)
    # Restore original function without bypass sentinel
    op.execute("""
               CREATE
               OR REPLACE FUNCTION core.find_user_for_auth(p_email CITEXT)
        RETURNS TABLE (
            user_id UUID, tenant_id UUID, email CITEXT, password_hash TEXT,
            status core.record_status, role core.user_role, full_name TEXT,
            first_name TEXT, last_name TEXT, is_active BOOLEAN, is_approved BOOLEAN,
            default_scheme_id UUID, totp_enabled BOOLEAN, mfa_required BOOLEAN,
            permission_overrides JSONB
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = core, public
        AS $$
               BEGIN
               RETURN QUERY
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
                      u.permission_overrides
               FROM core.users u
               WHERE u.email = p_email
                 AND u.is_active = TRUE LIMIT 1;
               END;
        $$;
               """)
