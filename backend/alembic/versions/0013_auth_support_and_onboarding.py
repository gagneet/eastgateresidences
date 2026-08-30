"""auth_support_and_onboarding

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-07

Adds:
- core.users.last_login_ip TEXT column (security audit trail)
- core.find_user_for_auth(CITEXT) SECURITY DEFINER function
  Runs outside RLS so pre-auth paths (login, forgot-password, invite-claim)
  can look up a user by email BEFORE tenant_id is known.
- core.onboarding_sessions table for the multi-step building onboarding wizard
"""
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # 1. Security audit column on core.users
    # ──────────────────────────────────────────────────────────────────────
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0013_auth_support_and_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("ALTER TABLE core.users ADD COLUMN IF NOT EXISTS last_login_ip TEXT")

    # ──────────────────────────────────────────────────────────────────────
    # 2. SECURITY DEFINER bootstrap function
    #
    # Pre-auth paths (login, forgot-password, invite-claim) need to look up
    # a user by email BEFORE the caller knows the tenant_id — which means
    # they cannot call SET LOCAL app.tenant_id first.  RLS would normally
    # block this.  This SECURITY DEFINER function runs as the DB owner role
    # and performs the lookup without RLS restrictions.
    #
    # Returns at most ONE active row matching the email.  Callers must still
    # verify the password hash before trusting the returned credentials.
    # ──────────────────────────────────────────────────────────────────────
    op.execute("""
               CREATE
               OR REPLACE FUNCTION core.find_user_for_auth(p_email CITEXT)
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
    # Grant to the application role (PUBLIC is too broad for an auth-bypass function)
    op.execute("REVOKE ALL ON FUNCTION core.find_user_for_auth(CITEXT) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION core.find_user_for_auth(CITEXT) TO strataos_user")

    # ──────────────────────────────────────────────────────────────────────
    # 3. Onboarding sessions — tracks multi-step wizard state
    # ──────────────────────────────────────────────────────────────────────
    op.execute("""
               CREATE TABLE IF NOT EXISTS core.onboarding_sessions
               (
                   session_id
                   UUID
                   PRIMARY
                   KEY
                   DEFAULT
                   gen_random_uuid
               (
               ),
                   tenant_id UUID REFERENCES core.tenants
               (
                   tenant_id
               ),
                   scheme_id UUID REFERENCES core.schemes
               (
                   scheme_id
               ),
                   initiated_by UUID NOT NULL REFERENCES core.users
               (
                   user_id
               ),
                   status TEXT NOT NULL DEFAULT 'started'
                   CHECK
               (
                   status
                   IN
               (
                   'started',
                   'in_progress',
                   'completed',
                   'abandoned'
               )),
                   current_step INTEGER NOT NULL DEFAULT 1,
                   step_data JSONB NOT NULL DEFAULT '{}',
                   is_test_data BOOLEAN NOT NULL DEFAULT FALSE,
                   created_at TIMESTAMPTZ NOT NULL DEFAULT NOW
               (
               ),
                   updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW
               (
               )
                   )
               """)
    op.execute("""
               CREATE INDEX IF NOT EXISTS onboarding_sessions_tenant_idx
                   ON core.onboarding_sessions(tenant_id)
                   WHERE status NOT IN ('completed', 'abandoned')
               """)
    op.execute("""
               CREATE INDEX IF NOT EXISTS onboarding_sessions_initiator_idx
                   ON core.onboarding_sessions(initiated_by)
               """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0013_auth_support_and_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP INDEX IF EXISTS core.onboarding_sessions_initiator_idx")
    op.execute("DROP INDEX IF EXISTS core.onboarding_sessions_tenant_idx")
    op.execute("DROP TABLE IF EXISTS core.onboarding_sessions")
    op.execute("DROP FUNCTION IF EXISTS core.find_user_for_auth(CITEXT)")
    op.execute("ALTER TABLE core.users DROP COLUMN IF EXISTS last_login_ip")
