"""0010 — Identity tables.

Revision: 0010
Previous: 0009

Establishes the application identity layer on top of the thin core.users
stub created in migration 0002.  Adds full authentication + authorisation
columns to core.users and creates five new tables:

  core.user_units           — user ↔ lot temporal link (1-to-many per user)
  core.user_role_assignments — per-scheme role grants + temp elevation
  core.user_sessions        — JWT session tracking + revocation
  core.user_invitations     — single-use claim-token invitation (Phase C)

And one SQL helper function:

  core.user_effective_role(p_user_id, p_scheme_id) → core.user_role

Role model
----------
The canonical top-level role is ``building_admin`` (formerly ``chairman``
before the EC reform).  ``chairman`` is now an EC *position* (sub-role
within ec_member) stored in ``ec_position`` on user_role_assignments.

The application's ``normalize_user_role()`` helper maps legacy ``chairman``
strings (from old JWT / Mongo documents) to ``building_admin`` at runtime.

EC positions: CHAIRMAN | TREASURER | SECRETARY | MEMBER
  — stored as ec_position TEXT on core.user_role_assignments.

Alters core.users
-----------------
  Renames:
    login_email  → email      (CITEXT, unique-per-tenant preserved)
    display_name → full_name  (TEXT NOT NULL)

  Adds:
    password_hash, first_name, last_name, phone,
    role, effective_role, default_scheme_id,
    is_active, is_approved, approved_at, approved_by, owner_approved_at,
    is_name_flagged, flag_reason,
    totp_secret_encrypted, totp_enabled,
    is_test_data, last_login_at, permission_overrides

  Kept (useful for OAuth/SSO future work):
    party_id, auth_subject, mfa_required, status
"""
from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ───────────────────────────────────────────────────── 1. user_role ENUM ──
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0010_identity_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
        CREATE TYPE core.user_role AS ENUM (
            'super_admin',
            'building_admin',
            'strata_manager',
            'ec_member',
            'admin_staff',
            'real_estate_agent',
            'service_provider',
            'owner',
            'tenant',
            'guest'
        )
    """)

    # ──────────────────────────────────── 2. ALTER core.users — rename cols ──
    # Rename thin columns to canonical names.
    # FK references point at user_id (UUID), never at these text columns,
    # so renaming is safe.  The unique index is updated automatically.
    op.execute("ALTER TABLE core.users RENAME COLUMN login_email  TO email")
    op.execute("ALTER TABLE core.users RENAME COLUMN display_name TO full_name")

    # full_name: was nullable — make NOT NULL (table is empty at this point)
    op.execute("ALTER TABLE core.users ALTER COLUMN full_name SET DEFAULT ''")
    op.execute("ALTER TABLE core.users ALTER COLUMN full_name SET NOT NULL")

    # ─────────────────────────────── 3. ADD new columns to core.users ──
    # Authentication credentials
    op.execute("""
               ALTER TABLE core.users
                   ADD COLUMN password_hash TEXT NOT NULL DEFAULT '',
            ADD COLUMN first_name    TEXT,
            ADD COLUMN last_name     TEXT,
            ADD COLUMN phone         TEXT
               """)

    # Roles — building_admin is the canonical replacement for the legacy
    # top-level 'chairman' role.  EC sub-positions (chairman, treasurer …)
    # live in core.user_role_assignments.ec_position.
    op.execute("""
               ALTER TABLE core.users
                   ADD COLUMN role core.user_role NOT NULL DEFAULT 'owner',
            ADD COLUMN effective_role core.user_role
               """)

    # Multi-building convenience: last selected / default scheme for the UI
    op.execute("""
               ALTER TABLE core.users
                   ADD COLUMN default_scheme_id UUID REFERENCES core.schemes (scheme_id)
               """)

    # Approval lifecycle
    op.execute("""
               ALTER TABLE core.users
                   ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true,
            ADD COLUMN is_approved       BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN approved_at       TIMESTAMPTZ,
            ADD COLUMN approved_by       UUID REFERENCES core.users(user_id),
            ADD COLUMN owner_approved_at TIMESTAMPTZ
               """)

    # Name-flag (PII review workflow)
    op.execute("""
               ALTER TABLE core.users
                   ADD COLUMN is_name_flagged BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN flag_reason     TEXT
               """)

    # TOTP 2FA — secret encrypted at rest (GAP-SEC-002, Fernet key)
    op.execute("""
               ALTER TABLE core.users
                   ADD COLUMN totp_secret_encrypted BYTEA,
            ADD COLUMN totp_enabled          BOOLEAN NOT NULL DEFAULT false
               """)

    # Test data + timestamps
    op.execute("""
               ALTER TABLE core.users
                   ADD COLUMN is_test_data BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN last_login_at TIMESTAMPTZ
               """)

    # Per-user permission overrides (dot-notation keys, e.g. 'workorder.approve')
    op.execute("""
               ALTER TABLE core.users
                   ADD COLUMN permission_overrides JSONB NOT NULL DEFAULT '{}'::jsonb
               """)

    # ──────────────────────── 4. Indexes on the altered core.users columns ──
    op.execute("""
               CREATE INDEX users_tenant_email_idx
                   ON core.users (tenant_id, email)
               """)
    op.execute("""
               CREATE INDEX users_default_scheme_idx
                   ON core.users (default_scheme_id) WHERE default_scheme_id IS NOT NULL
               """)
    op.execute("""
               CREATE INDEX users_role_idx
                   ON core.users (role)
               """)
    op.execute("""
               CREATE INDEX users_active_approved_idx
                   ON core.users (tenant_id, is_active, is_approved) WHERE is_active AND is_approved
               """)

    # ──────────────────────────────────────────────── 5. core.user_units ──
    op.execute("""
               CREATE TABLE core.user_units
               (
                   user_unit_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id    UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id    UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   user_id      UUID        NOT NULL REFERENCES core.users (user_id),
                   lot_id       UUID        NOT NULL REFERENCES core.lots (lot_id),
                   party_id     UUID REFERENCES core.parties (party_id),

                   -- owner | tenant | family | agent
                   relationship TEXT        NOT NULL
                       CHECK (relationship IN ('owner', 'tenant', 'family', 'agent')),

                   valid_from   DATE        NOT NULL,
                   valid_to     DATE, -- NULL = current occupant

                   is_test_data BOOLEAN     NOT NULL DEFAULT false,
                   created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

                   UNIQUE (user_id, lot_id, valid_from)
               )
               """)
    op.execute("CREATE INDEX uu_tenant_idx     ON core.user_units (tenant_id)")
    op.execute("CREATE INDEX uu_lot_idx        ON core.user_units (lot_id)")
    op.execute("""
               CREATE INDEX uu_user_active_idx
                   ON core.user_units (user_id, scheme_id) WHERE valid_to IS NULL
               """)

    # ─────────────────────────────── 6. core.user_role_assignments ──
    op.execute("""
               CREATE TABLE core.user_role_assignments
               (
                   assignment_id UUID PRIMARY KEY        DEFAULT gen_random_uuid(),
                   tenant_id     UUID           NOT NULL REFERENCES core.tenants (tenant_id),
                   user_id       UUID           NOT NULL REFERENCES core.users (user_id),
                   -- NULL scheme_id = global assignment (super_admin, multi-building manager)
                   scheme_id     UUID REFERENCES core.schemes (scheme_id),

                   role          core.user_role NOT NULL,

                   -- EC committee sub-position (only meaningful when role = 'ec_member').
                   -- Values: CHAIRMAN | TREASURER | SECRETARY | MEMBER
                   ec_position   TEXT CHECK (
                       ec_position IS NULL OR
                       ec_position IN ('CHAIRMAN', 'TREASURER', 'SECRETARY', 'MEMBER')
                       ),

                   granted_at    TIMESTAMPTZ    NOT NULL DEFAULT now(),
                   granted_by    UUID REFERENCES core.users (user_id),
                   -- Non-NULL expires_at = temp elevation (cleared by background job)
                   expires_at    TIMESTAMPTZ,
                   is_active     BOOLEAN        NOT NULL DEFAULT true
               )
               """)
    # Partial unique indexes instead of a table UNIQUE constraint so that
    # NULL scheme_id (global role) still enforces one-role-per-user globally.
    op.execute("""
               CREATE UNIQUE INDEX ura_user_scheme_role_unique
                   ON core.user_role_assignments (user_id, scheme_id, role) WHERE scheme_id IS NOT NULL
               """)
    op.execute("""
               CREATE UNIQUE INDEX ura_user_global_role_unique
                   ON core.user_role_assignments (user_id, role) WHERE scheme_id IS NULL
               """)
    op.execute("""
               CREATE INDEX ura_user_active_idx
                   ON core.user_role_assignments (user_id) WHERE is_active
               """)
    op.execute("""
               CREATE INDEX ura_scheme_role_idx
                   ON core.user_role_assignments (scheme_id, role) WHERE is_active
               """)
    op.execute("CREATE INDEX ura_tenant_idx ON core.user_role_assignments (tenant_id)")

    # ────────────────────────────────────────────── 7. core.user_sessions ──
    op.execute("""
               CREATE TABLE core.user_sessions
               (
                   session_id       UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id        UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   user_id          UUID        NOT NULL REFERENCES core.users (user_id),
                   jwt_jti          TEXT        NOT NULL UNIQUE,
                   issued_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                   expires_at       TIMESTAMPTZ NOT NULL,
                   revoked_at       TIMESTAMPTZ,
                   last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                   ip_address       INET,
                   user_agent       TEXT
               )
               """)
    op.execute("""
               CREATE INDEX us_user_active_idx
                   ON core.user_sessions (user_id) WHERE revoked_at IS NULL
               """)
    op.execute("""
               CREATE INDEX us_expiry_idx
                   ON core.user_sessions (expires_at) WHERE revoked_at IS NULL
               """)

    # ───────────────────────────────────────────── 8. core.user_invitations ──
    op.execute("""
               CREATE TABLE core.user_invitations
               (
                   invitation_id      UUID PRIMARY KEY        DEFAULT gen_random_uuid(),
                   tenant_id          UUID           NOT NULL REFERENCES core.tenants (tenant_id),
                   -- NULL scheme_id = org-level invite (no specific building yet)
                   scheme_id          UUID REFERENCES core.schemes (scheme_id),

                   email              CITEXT         NOT NULL,
                   invited_role       core.user_role NOT NULL,
                   invited_by         UUID           NOT NULL REFERENCES core.users (user_id),
                   invited_at         TIMESTAMPTZ    NOT NULL DEFAULT now(),

                   -- Single-use expiring claim token.
                   -- The raw secret is never stored; only its sha256 digest.
                   claim_token_sha256 BYTEA          NOT NULL,
                   expires_at         TIMESTAMPTZ    NOT NULL,
                   claimed_at         TIMESTAMPTZ,
                   claimed_user_id    UUID REFERENCES core.users (user_id),

                   -- Pre-filled data for the registration form
                   prefill_first_name TEXT,
                   prefill_last_name  TEXT,
                   prefill_phone      TEXT,
                   prefill_unit_id    UUID REFERENCES core.lots (lot_id),

                   cancelled_at       TIMESTAMPTZ,
                   cancelled_by       UUID REFERENCES core.users (user_id)
               )
               """)
    op.execute("""
               CREATE INDEX ui_active_idx
                   ON core.user_invitations (tenant_id, email) WHERE claimed_at IS NULL AND cancelled_at IS NULL
               """)
    op.execute("""
               CREATE INDEX ui_token_lookup_idx
                   ON core.user_invitations (claim_token_sha256) WHERE claimed_at IS NULL AND cancelled_at IS NULL
               """)

    # ─────────────────────── 9. core.user_effective_role() SQL function ──
    # Mirrors the application's _effective_role() helper so SQL queries and
    # stored procedures can resolve the effective role without a round-trip.
    #
    # Resolution order:
    #   1. Active temp-elevation (expires_at in future) — scheme-specific first
    #   2. Permanent active assignment for the specific scheme
    #   3. Permanent global assignment (scheme_id IS NULL — super_admin etc.)
    #   4. Fallback to user's base role column
    op.execute("""
               CREATE
               OR REPLACE FUNCTION core.user_effective_role(
            p_user_id   UUID,
            p_scheme_id UUID
        )
        RETURNS core.user_role LANGUAGE plpgsql STABLE AS $$
        DECLARE
               v_role core.user_role;
               BEGIN
            -- 1. Active temp-elevation for this scheme, then global elevation
               SELECT role
               INTO v_role
               FROM core.user_role_assignments
               WHERE user_id = p_user_id
                 AND (scheme_id = p_scheme_id OR scheme_id IS NULL)
                 AND is_active = true
                 AND expires_at IS NOT NULL
                 AND expires_at > now()
               ORDER BY (scheme_id = p_scheme_id) DESC NULLS LAST LIMIT 1;

               IF
               v_role IS NOT NULL THEN
                RETURN v_role;
               END IF;

            -- 2. Permanent active assignment for the specific scheme
               SELECT role
               INTO v_role
               FROM core.user_role_assignments
               WHERE user_id = p_user_id
                 AND scheme_id = p_scheme_id
                 AND is_active = true
                 AND expires_at IS NULL LIMIT 1;

               IF
               v_role IS NOT NULL THEN
                RETURN v_role;
               END IF;

            -- 3. Permanent global assignment (super_admin, multi-building manager)
               SELECT role
               INTO v_role
               FROM core.user_role_assignments
               WHERE user_id = p_user_id
                 AND scheme_id IS NULL
                 AND is_active = true
                 AND expires_at IS NULL LIMIT 1;

               IF
               v_role IS NOT NULL THEN
                RETURN v_role;
               END IF;

            -- 4. Fallback to user's base role column
               SELECT role
               INTO v_role
               FROM core.users
               WHERE user_id = p_user_id;

               RETURN COALESCE(v_role, 'guest'::core.user_role);
               END $$;
               """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0010_identity_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP FUNCTION IF EXISTS core.user_effective_role(UUID, UUID)")

    # Drop tables in reverse dependency order
    op.execute("DROP TABLE IF EXISTS core.user_invitations     CASCADE")
    op.execute("DROP TABLE IF EXISTS core.user_sessions        CASCADE")
    op.execute("DROP TABLE IF EXISTS core.user_role_assignments CASCADE")
    op.execute("DROP TABLE IF EXISTS core.user_units           CASCADE")

    # Drop indexes created on core.users
    for idx in (
            "users_active_approved_idx",
            "users_role_idx",
            "users_default_scheme_idx",
            "users_tenant_email_idx",
    ):
        op.execute(f"DROP INDEX IF EXISTS core.{idx}")

    # Remove all columns added in this migration
    for col in (
            "permission_overrides",
            "last_login_at",
            "is_test_data",
            "totp_enabled",
            "totp_secret_encrypted",
            "flag_reason",
            "is_name_flagged",
            "owner_approved_at",
            "approved_by",
            "approved_at",
            "is_approved",
            "is_active",
            "default_scheme_id",
            "effective_role",
            "role",
            "phone",
            "last_name",
            "first_name",
            "password_hash",
    ):
        op.execute(f"ALTER TABLE core.users DROP COLUMN IF EXISTS {col}")

    # Rename columns back to their original names
    op.execute("ALTER TABLE core.users RENAME COLUMN full_name TO display_name")
    op.execute("ALTER TABLE core.users RENAME COLUMN email     TO login_email")

    # Drop the enum last (nothing references it now)
    op.execute("DROP TYPE IF EXISTS core.user_role")
