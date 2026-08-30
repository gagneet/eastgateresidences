"""0028 — Onboarding workflow data model.

Revision ID: 0028
Revises: 0027
Create Date: 2026-05-03

Adds the schema needed for the SM-organisation + scheme onboarding
workflow specified in ``docs/architecture/onboarding/01_data_model_migration.md``:

1. Extends ``core.tenants`` with ``is_self_managed``, ``legal_name``, and
   ``created_from_trial_id`` columns.
2. Adds a partial unique index on ``core.tenants.abn`` that fires only
   while the row is active — duplicate ABNs across active tenants are
   blocked, but an ABN can be reused once its previous owner is archived.
3. Adds a partial unique index on ``core.schemes.tenant_id`` that fires
   only when the parent tenant is self-managed — enforcing the 1:1
   tenant↔scheme invariant for self-managed schemes at the DB level.
4. Creates ``core.trial_request_status`` enum and ``core.trial_requests``
   table, replacing the Mongo ``db.trial_requests`` collection so trial
   approval can be a single Postgres transaction.

The existing ``core.record_status`` enum (``draft|active|inactive|archived``)
is reused for tenant status — no new tenant-status enum is needed.
"""
from alembic import op

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────
    # 1. Extend core.tenants
    # ──────────────────────────────────────────────────────────────────
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0028_onboarding_workflow.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
               ALTER TABLE core.tenants
                   ADD COLUMN IF NOT EXISTS is_self_managed BOOLEAN NOT NULL DEFAULT FALSE,
                   ADD COLUMN IF NOT EXISTS legal_name TEXT,
                   ADD COLUMN IF NOT EXISTS created_from_trial_id UUID
               """)

    # ──────────────────────────────────────────────────────────────────
    # 2. Active-only ABN uniqueness on core.tenants
    # ──────────────────────────────────────────────────────────────────
    op.execute("""
               CREATE UNIQUE INDEX IF NOT EXISTS tenants_abn_unique_active
                   ON core.tenants (abn)
                   WHERE abn IS NOT NULL AND status = 'active'
               """)

    # ──────────────────────────────────────────────────────────────────
    # 3. Self-managed 1:1 tenant↔scheme invariant
    # ──────────────────────────────────────────────────────────────────
    # A subquery in a partial-index predicate is not allowed in
    # Postgres, so we materialise the predicate via a column reference
    # added by the trigger below. Simpler approach: use a deferred
    # CHECK via a BEFORE INSERT trigger that consults core.tenants.
    op.execute("""
               CREATE
               OR REPLACE FUNCTION core.assert_self_managed_one_scheme()
        RETURNS TRIGGER AS $$
        DECLARE
               v_self_managed BOOLEAN;
            v_existing
               INT;
               BEGIN
               SELECT is_self_managed
               INTO v_self_managed
               FROM core.tenants
               WHERE tenant_id = NEW.tenant_id;

               IF
               v_self_managed IS DISTINCT FROM TRUE THEN
                RETURN NEW;   -- not self-managed → no constraint
               END IF;

               SELECT COUNT(*)
               INTO v_existing
               FROM core.schemes
               WHERE tenant_id = NEW.tenant_id
                 AND status <> 'archived'
                 AND scheme_id <> COALESCE(NEW.scheme_id, gen_random_uuid());

               IF
               v_existing > 0 THEN
                RAISE EXCEPTION
                    'self-managed tenant % already has a scheme', NEW.tenant_id
                    USING ERRCODE = 'unique_violation';
               END IF;
               RETURN NEW;
               END;
        $$
               LANGUAGE plpgsql
               """)

    op.execute("""
        DROP TRIGGER IF EXISTS schemes_self_managed_one_per_tenant
        ON core.schemes
    """)
    op.execute("""
               CREATE TRIGGER schemes_self_managed_one_per_tenant
                   BEFORE INSERT OR
               UPDATE OF tenant_id
               ON core.schemes
                   FOR EACH ROW
                   EXECUTE FUNCTION core.assert_self_managed_one_scheme()
               """)

    # ──────────────────────────────────────────────────────────────────
    # 4. Trial requests — moves from Mongo db.trial_requests to PG
    # ──────────────────────────────────────────────────────────────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE core.trial_request_status AS ENUM (
                'submitted',
                'approved',
                'rejected',
                'expired'
            );
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
    """)

    op.execute("""
               CREATE TABLE IF NOT EXISTS core.trial_requests
               (
                   request_id
                   UUID
                   PRIMARY
                   KEY
                   DEFAULT
                   gen_random_uuid
               (
               ),
                   submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW
               (
               ),
                   status core.trial_request_status NOT NULL DEFAULT 'submitted',

                   org_name TEXT NOT NULL,
                   legal_name TEXT,
                   abn TEXT,
                   jurisdiction compliance.jurisdiction_code NOT NULL,
                   contact_first_name TEXT NOT NULL,
                   contact_last_name TEXT NOT NULL,
                   contact_email CITEXT NOT NULL,
                   contact_phone TEXT,
                   message TEXT,
                   source_ip INET,

                   reviewed_at TIMESTAMPTZ,
                   reviewed_by UUID REFERENCES core.users
               (
                   user_id
               ),
                   reject_reason TEXT,
                   notes TEXT,

                   created_tenant_id UUID REFERENCES core.tenants
               (
                   tenant_id
               ),
                   invitation_id UUID REFERENCES core.user_invitations
               (
                   invitation_id
               ),

                   expires_at TIMESTAMPTZ NOT NULL DEFAULT
               (
                   NOW
               (
               ) + INTERVAL '30 days')
                   )
               """)
    op.execute("""
               CREATE INDEX IF NOT EXISTS trial_requests_status_idx
                   ON core.trial_requests(status)
               """)
    op.execute("""
               CREATE INDEX IF NOT EXISTS trial_requests_email_idx
                   ON core.trial_requests(contact_email)
               """)
    # Backstop the "one open submission per email" idempotency rule from
    # the spec's POST /public/trial-request — a duplicate submission
    # while one is already pending becomes a unique-violation we can
    # translate to "return the existing request_id".
    op.execute("""
               CREATE UNIQUE INDEX IF NOT EXISTS trial_requests_email_open_unique
                   ON core.trial_requests (contact_email)
                   WHERE status = 'submitted'
               """)


def downgrade() -> None:
    # 4. Trial requests
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0028_onboarding_workflow.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP INDEX IF EXISTS core.trial_requests_email_open_unique")
    op.execute("DROP INDEX IF EXISTS core.trial_requests_email_idx")
    op.execute("DROP INDEX IF EXISTS core.trial_requests_status_idx")
    op.execute("DROP TABLE IF EXISTS core.trial_requests")
    op.execute("DROP TYPE  IF EXISTS core.trial_request_status")

    # 3. Self-managed trigger + function
    op.execute("DROP TRIGGER  IF EXISTS schemes_self_managed_one_per_tenant ON core.schemes")
    op.execute("DROP FUNCTION IF EXISTS core.assert_self_managed_one_scheme()")

    # 2. ABN uniqueness
    op.execute("DROP INDEX IF EXISTS core.tenants_abn_unique_active")

    # 1. core.tenants columns
    op.execute("""
               ALTER TABLE core.tenants
               DROP
               COLUMN IF EXISTS created_from_trial_id,
            DROP
               COLUMN IF EXISTS legal_name,
            DROP
               COLUMN IF EXISTS is_self_managed
               """)
