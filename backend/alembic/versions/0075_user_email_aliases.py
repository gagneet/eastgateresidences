"""core.user_email_aliases: secondary login emails that resolve to an
existing core.users account, plus core.find_user_for_auth() updated to
match on an alias email as well as the primary one.

Revision ID: 0075_user_email_aliases
Revises: 0074_harden_recon_batches
Create Date: 2026-07-25 00:00:00.000000

# @featuretrace:identity_core — Secondary/alias login emails for a single
#   canonical core.users account (e.g. an operational shared mailbox and a
#   personal company email both logging into the same identity).
# Layer: migration
# Data flow: core.user_email_aliases (new) -> core.find_user_for_auth() ->
#            db_postgres/repos/identity_repo.py:find_user_for_auth() ->
#            routers/auth.py:login().
# Related: backend/alembic/versions/0023_extend_find_user_for_auth_columns.py
#          backend/db_postgres/repos/identity_repo.py
#          backend/routers/auth.py
#          tasks/GAP-CUTOVER-001-east-gate-pg-write-primary.md (East Gate
#          Strata Manager duplicate-account merge, 2026-07-25)
# Table: core.user_email_aliases

Real-world trigger: East Gate's Strata Manager (Jessica Minichiello) had two
Mongo user records for the same person -- manager@eastgate.com (actively
used) and jessica@civium.com.au (never activated, duplicate seed data).
Per explicit user direction, these were merged into one canonical account
with the second email kept as a login alias for the same password, rather
than migrating two separate identities into Postgres. This table is the
general mechanism for that; the East Gate alias row itself is inserted by
a one-off data script, not by this migration.
"""
from __future__ import annotations

from alembic import op

revision = "0075_user_email_aliases"
down_revision = "0074_harden_recon_batches"
branch_labels = None
depends_on = None

_TENANT_BYPASS_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE core.user_email_aliases (
            alias_id UUID PRIMARY KEY,
            tenant_id UUID NOT NULL,
            user_id UUID NOT NULL REFERENCES core.users(user_id),
            alias_email CITEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_by UUID,
            reason TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX user_email_aliases_user_idx ON core.user_email_aliases (user_id)"
    )
    op.execute("ALTER TABLE core.user_email_aliases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE core.user_email_aliases FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON core.user_email_aliases
        USING (
            tenant_id = core.current_tenant_id()
            OR current_setting('app.tenant_id', true) = '{_TENANT_BYPASS_ID}'
        )
        """
    )

    # A primary-email match must still win over an alias match if (in theory)
    # both ever resolved rows -- UNION with primary first, then LIMIT 1 on
    # the combined result, keeps that precedence without changing behaviour
    # for the overwhelmingly common no-alias case.
    op.execute(
        f"""
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
            prev_tenant := current_setting('app.tenant_id', true);
            PERFORM set_config('app.tenant_id', '{_TENANT_BYPASS_ID}', true);

            RETURN QUERY
            SELECT u.user_id, u.tenant_id, u.email, u.password_hash, u.status, u.role,
                   u.full_name, u.first_name, u.last_name, u.is_active, u.is_approved,
                   u.default_scheme_id, u.totp_enabled, u.mfa_required, u.permission_overrides,
                   u.created_at, u.last_login_at, u.last_login_ip
            FROM core.users u
            WHERE u.email = p_email
              AND u.is_active = TRUE
            UNION ALL
            SELECT u.user_id, u.tenant_id, u.email, u.password_hash, u.status, u.role,
                   u.full_name, u.first_name, u.last_name, u.is_active, u.is_approved,
                   u.default_scheme_id, u.totp_enabled, u.mfa_required, u.permission_overrides,
                   u.created_at, u.last_login_at, u.last_login_ip
            FROM core.user_email_aliases a
            JOIN core.users u ON u.user_id = a.user_id
            WHERE a.alias_email = p_email
              AND u.is_active = TRUE
              AND NOT EXISTS (SELECT 1 FROM core.users u2 WHERE u2.email = p_email)
            LIMIT 1;

            PERFORM set_config('app.tenant_id', coalesce(prev_tenant, ''), true);
        END;
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION core.find_user_for_auth(CITEXT) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION core.find_user_for_auth(CITEXT) TO strataos_user")


def downgrade() -> None:
    # Restore the 0023 shape (no alias lookup)
    op.execute("DROP FUNCTION IF EXISTS core.find_user_for_auth(CITEXT)")
    op.execute(
        f"""
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
            prev_tenant := current_setting('app.tenant_id', true);
            PERFORM set_config('app.tenant_id', '{_TENANT_BYPASS_ID}', true);

            RETURN QUERY
            SELECT u.user_id, u.tenant_id, u.email, u.password_hash, u.status, u.role,
                   u.full_name, u.first_name, u.last_name, u.is_active, u.is_approved,
                   u.default_scheme_id, u.totp_enabled, u.mfa_required, u.permission_overrides,
                   u.created_at, u.last_login_at, u.last_login_ip
            FROM core.users u
            WHERE u.email = p_email
              AND u.is_active = TRUE
            LIMIT 1;

            PERFORM set_config('app.tenant_id', coalesce(prev_tenant, ''), true);
        END;
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION core.find_user_for_auth(CITEXT) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION core.find_user_for_auth(CITEXT) TO strataos_user")
    op.execute("DROP TABLE IF EXISTS core.user_email_aliases")
