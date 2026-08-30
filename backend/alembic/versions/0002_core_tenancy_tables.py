"""0002 — Core tenancy tables.

Revision: 0002
Previous: 0001

Creates:
- core.tenants           — top-level management company / tenant
- core.schemes           — strata scheme (unit plan)
- core.buildings         — physical building
- core.lots              — individual lot/unit within a scheme
- core.parties           — any person, company or trust
- core.users             — platform users linked to parties
- core.party_roles       — scoped role assignments
- core.ownership_periods — bitemporal lot ownership (btree_gist EXCLUDE)
- core.tenancy_periods   — rental occupancy tracking
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0002_core_tenancy_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
               CREATE TABLE core.tenants
               (
                   tenant_id    UUID PRIMARY KEY            DEFAULT gen_random_uuid(),
                   tenant_name  TEXT               NOT NULL,
                   trading_name TEXT,
                   abn          TEXT,
                   tenant_type  TEXT               NOT NULL DEFAULT 'strata_manager',
                   status       core.record_status NOT NULL DEFAULT 'active',
                   created_at   TIMESTAMPTZ        NOT NULL DEFAULT now(),
                   updated_at   TIMESTAMPTZ        NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE core.schemes
               (
                   scheme_id             UUID PRIMARY KEY                      DEFAULT gen_random_uuid(),
                   tenant_id             UUID                         NOT NULL REFERENCES core.tenants (tenant_id),
                   jurisdiction          compliance.jurisdiction_code NOT NULL,
                   scheme_number         TEXT                         NOT NULL,
                   scheme_name           TEXT                         NOT NULL,
                   legal_name            TEXT,
                   abn                   TEXT,
                   gst_registered        BOOLEAN                      NOT NULL DEFAULT false,
                   gst_rate_basis_points INT                          NOT NULL DEFAULT 1000,
                   management_start_date DATE,
                   management_end_date   DATE,
                   status                core.record_status           NOT NULL DEFAULT 'active',
                   created_at            TIMESTAMPTZ                  NOT NULL DEFAULT now(),
                   updated_at            TIMESTAMPTZ                  NOT NULL DEFAULT now(),
                   UNIQUE (tenant_id, jurisdiction, scheme_number)
               )
               """)

    op.execute("""
               CREATE TABLE core.buildings
               (
                   building_id       UUID PRIMARY KEY                      DEFAULT gen_random_uuid(),
                   tenant_id         UUID                         NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id         UUID                         NOT NULL REFERENCES core.schemes (scheme_id),
                   building_name     TEXT                         NOT NULL,
                   street_address    TEXT,
                   suburb            TEXT,
                   state             compliance.jurisdiction_code NOT NULL,
                   postcode          TEXT,
                   building_type     TEXT,
                   construction_year INT,
                   lot_count         INT,
                   asset_profile     JSONB                        NOT NULL DEFAULT '{}'::jsonb,
                   status            core.record_status           NOT NULL DEFAULT 'active',
                   created_at        TIMESTAMPTZ                  NOT NULL DEFAULT now(),
                   updated_at        TIMESTAMPTZ                  NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE core.lots
               (
                   lot_id            UUID PRIMARY KEY            DEFAULT gen_random_uuid(),
                   tenant_id         UUID               NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id         UUID               NOT NULL REFERENCES core.schemes (scheme_id),
                   building_id       UUID REFERENCES core.buildings (building_id),
                   lot_number        TEXT               NOT NULL,
                   unit_number       TEXT,
                   lot_use           TEXT               NOT NULL DEFAULT 'residential',
                   entitlement_units NUMERIC(18, 6),
                   floor_area_sqm    NUMERIC(12, 3),
                   status            core.record_status NOT NULL DEFAULT 'active',
                   created_at        TIMESTAMPTZ        NOT NULL DEFAULT now(),
                   updated_at        TIMESTAMPTZ        NOT NULL DEFAULT now(),
                   UNIQUE (scheme_id, lot_number)
               )
               """)

    op.execute("""
               CREATE TABLE core.parties
               (
                   party_id       UUID PRIMARY KEY            DEFAULT gen_random_uuid(),
                   tenant_id      UUID               NOT NULL REFERENCES core.tenants (tenant_id),
                   party_type     TEXT               NOT NULL,
                   legal_name     TEXT               NOT NULL,
                   preferred_name TEXT,
                   abn            TEXT,
                   acn            TEXT,
                   primary_email  CITEXT,
                   primary_mobile TEXT,
                   postal_address TEXT,
                   metadata       JSONB              NOT NULL DEFAULT '{}'::jsonb,
                   status         core.record_status NOT NULL DEFAULT 'active',
                   created_at     TIMESTAMPTZ        NOT NULL DEFAULT now(),
                   updated_at     TIMESTAMPTZ        NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE core.users
               (
                   user_id      UUID PRIMARY KEY            DEFAULT gen_random_uuid(),
                   tenant_id    UUID               NOT NULL REFERENCES core.tenants (tenant_id),
                   party_id     UUID REFERENCES core.parties (party_id),
                   login_email  CITEXT             NOT NULL,
                   display_name TEXT,
                   auth_subject TEXT,
                   mfa_required BOOLEAN            NOT NULL DEFAULT false,
                   status       core.record_status NOT NULL DEFAULT 'active',
                   created_at   TIMESTAMPTZ        NOT NULL DEFAULT now(),
                   updated_at   TIMESTAMPTZ        NOT NULL DEFAULT now(),
                   UNIQUE (tenant_id, login_email)
               )
               """)

    op.execute("""
               CREATE TABLE core.party_roles
               (
                   party_role_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id     UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   party_id      UUID        NOT NULL REFERENCES core.parties (party_id),
                   scope_type    TEXT        NOT NULL,
                   scope_id      UUID        NOT NULL,
                   role_code     TEXT        NOT NULL,
                   starts_on     DATE        NOT NULL DEFAULT CURRENT_DATE,
                   ends_on       DATE,
                   created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                   CHECK (ends_on IS NULL OR ends_on >= starts_on)
               )
               """)

    # Bitemporal ownership: EXCLUDE prevents overlapping valid periods for the same lot.
    # btree_gist extension (installed in 0001) is required for this constraint.
    op.execute("""
               CREATE TABLE core.ownership_periods
               (
                   ownership_period_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id           UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id           UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   lot_id              UUID        NOT NULL REFERENCES core.lots (lot_id),
                   owner_party_id      UUID        NOT NULL REFERENCES core.parties (party_id),
                   valid_from          DATE        NOT NULL,
                   valid_to            DATE,
                   recorded_from       TIMESTAMPTZ NOT NULL DEFAULT now(),
                   recorded_to         TIMESTAMPTZ,
                   source_document_id  TEXT,
                   notes               TEXT,
                   created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                   CHECK (valid_to IS NULL OR valid_to > valid_from),
                   CHECK (recorded_to IS NULL OR recorded_to > recorded_from),
                   EXCLUDE USING gist (
                lot_id WITH =,
                daterange(valid_from, COALESCE(valid_to, 'infinity'::date), '[)') WITH &&
            ) WHERE (recorded_to IS NULL)
               )
               """)

    op.execute("""
               CREATE TABLE core.tenancy_periods
               (
                   tenancy_period_id         UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id                 UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id                 UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   lot_id                    UUID        NOT NULL REFERENCES core.lots (lot_id),
                   tenant_party_id           UUID        NOT NULL REFERENCES core.parties (party_id),
                   property_manager_party_id UUID REFERENCES core.parties (party_id),
                   valid_from                DATE        NOT NULL,
                   valid_to                  DATE,
                   recorded_from             TIMESTAMPTZ NOT NULL DEFAULT now(),
                   recorded_to               TIMESTAMPTZ,
                   created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
                   CHECK (valid_to IS NULL OR valid_to > valid_from)
               )
               """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0002_core_tenancy_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP TABLE IF EXISTS core.tenancy_periods CASCADE")
    op.execute("DROP TABLE IF EXISTS core.ownership_periods CASCADE")
    op.execute("DROP TABLE IF EXISTS core.party_roles CASCADE")
    op.execute("DROP TABLE IF EXISTS core.users CASCADE")
    op.execute("DROP TABLE IF EXISTS core.parties CASCADE")
    op.execute("DROP TABLE IF EXISTS core.lots CASCADE")
    op.execute("DROP TABLE IF EXISTS core.buildings CASCADE")
    op.execute("DROP TABLE IF EXISTS core.schemes CASCADE")
    op.execute("DROP TABLE IF EXISTS core.tenants CASCADE")
