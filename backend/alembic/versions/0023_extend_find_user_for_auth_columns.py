"""0023 — Extend core.find_user_for_auth() to return created_at and last login fields.

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-02

The SECURITY DEFINER function from migration 0014 returned only identity, role,
and MFA columns. ``utils/permissions.user_to_response()`` treats ``created_at``
as a required field, so login responses for users that exist only in Postgres
crashed with ``KeyError: 'created_at'``.

This migration drops and recreates the function with three additional return
columns: ``created_at``, ``last_login_at``, ``last_login_ip``. The RLS bypass
sentinel logic from 0014 is preserved verbatim — without it, the pre-auth
lookup returns zero rows because ``app.tenant_id`` is unset.
"""
from alembic import op

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None

_BYPASS_UUID = '00000000-0000-0000-0000-000000000000'


def upgrade() -> None:
    # Postgres rejects CREATE OR REPLACE when the RETURNS TABLE shape changes;
    # drop first, then recreate.
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0023_extend_find_user_for_auth_columns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP FUNCTION IF EXISTS core.find_user_for_auth(CITEXT)")
    op.execute(f"""
        CREATE FUNCTION core.find_user_for_auth(p_email CITEXT)
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
            permission_overrides JSONB,
            created_at           TIMESTAMPTZ,
            last_login_at        TIMESTAMPTZ,
            last_login_ip        TEXT
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
                u.permission_overrides,
                u.created_at,
                u.last_login_at,
                u.last_login_ip
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
    # Restore the 0014 shape (no created_at / last_login_* columns)
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0023_extend_find_user_for_auth_columns.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP FUNCTION IF EXISTS core.find_user_for_auth(CITEXT)")
    op.execute(f"""
        CREATE FUNCTION core.find_user_for_auth(p_email CITEXT)
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
            prev_tenant := current_setting('app.tenant_id', true);
            PERFORM set_config('app.tenant_id', '{_BYPASS_UUID}', true);

            RETURN QUERY
            SELECT
                u.user_id, u.tenant_id, u.email, u.password_hash, u.status,
                u.role, u.full_name, u.first_name, u.last_name, u.is_active,
                u.is_approved, u.default_scheme_id, u.totp_enabled,
                u.mfa_required, u.permission_overrides
            FROM core.users u
            WHERE u.email = p_email
              AND u.is_active = TRUE
            LIMIT 1;

            PERFORM set_config('app.tenant_id', coalesce(prev_tenant, ''), true);
        END;
        $$;
    """)
    op.execute("REVOKE ALL ON FUNCTION core.find_user_for_auth(CITEXT) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION core.find_user_for_auth(CITEXT) TO strataos_user")
