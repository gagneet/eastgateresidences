"""0011 — Config tables.

Revision: 0011
Previous: 0010

Creates the feature-toggle and building-settings infrastructure:

  core.feature_toggles         — global feature registry        (NO RLS — global)
  core.feature_toggle_overrides — per-scheme feature overrides  (tenant-scoped)
  core.building_settings        — key-value building config      (tenant-scoped)
  core.role_permissions         — global role → permission map   (NO RLS — global)

And one SQL helper:

  core.feature_toggle_resolved(p_scheme_id, p_feature_key) → BOOLEAN

Feature-toggle 3-tier resolution (per CLAUDE.md and the D-prime spec):
  1. User-level:   core.users.permission_overrides JSONB (from migration 0010)
                   resolved at application layer, not here.
  2. Scheme-level: core.feature_toggle_overrides row for (scheme_id, feature_key)
  3. Global:       core.feature_toggles row for feature_key
  First match wins.  core.feature_toggle_resolved() covers tiers 2 + 3.

Global tables (no RLS):
  core.feature_toggles  — the global registry is the same for every tenant
  core.role_permissions — role → permission mapping is global (same for all)
  RLS is applied only in migration 0012 for the tenant-scoped tables.
"""
from __future__ import annotations

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────── 1. core.feature_toggles (global registry) ──
    # One row per feature key.  Seeded by seeds/feature_toggles.py (134 entries).
    # Only super_admin may change the global default.
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0011_config_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
               CREATE TABLE core.feature_toggles
               (
                   toggle_id        UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   feature_key      TEXT        NOT NULL UNIQUE,
                   feature_name     TEXT        NOT NULL,
                   description      TEXT,
                   category         TEXT        NOT NULL,          -- finance | governance | …
                   is_enabled       BOOLEAN     NOT NULL DEFAULT false,
                   routes           TEXT[]  NOT NULL DEFAULT '{}',
                   allowed_roles    TEXT[]  NOT NULL DEFAULT '{}', -- core.user_role values
                   depends_on       TEXT[]  NOT NULL DEFAULT '{}', -- other feature_keys
                   seeded_version   INT         NOT NULL DEFAULT 1,
                   last_modified_by UUID REFERENCES core.users (user_id),
                   last_modified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                   created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)
    op.execute("CREATE INDEX ft_category_idx ON core.feature_toggles (category)")
    op.execute("CREATE INDEX ft_enabled_idx  ON core.feature_toggles (is_enabled)")

    # ─────────────────────── 2. core.feature_toggle_overrides (per-scheme) ──
    # Strata managers can override the global default for their scheme.
    op.execute("""
               CREATE TABLE core.feature_toggle_overrides
               (
                   override_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id   UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id   UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   feature_key TEXT        NOT NULL REFERENCES core.feature_toggles (feature_key)
                       ON UPDATE CASCADE,
                   is_enabled  BOOLEAN     NOT NULL,
                   set_by      UUID        NOT NULL REFERENCES core.users (user_id),
                   set_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                   reason      TEXT,
                   UNIQUE (scheme_id, feature_key)
               )
               """)
    op.execute("CREATE INDEX fto_scheme_idx ON core.feature_toggle_overrides (scheme_id)")
    op.execute("CREATE INDEX fto_tenant_idx ON core.feature_toggle_overrides (tenant_id)")

    # ───────────────────────── 3. core.building_settings (per-scheme K/V) ──
    # Structured key-value store for per-scheme configuration.
    # Keys use dot-notation (e.g. 'finance.fy_start_month', 'building.abn').
    # Values are JSONB to support scalars, arrays, and nested objects.
    op.execute("""
               CREATE TABLE core.building_settings
               (
                   setting_id    UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id     UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id     UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   setting_key   TEXT        NOT NULL,
                   setting_value JSONB       NOT NULL,
                   set_by        UUID REFERENCES core.users (user_id),
                   set_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                   UNIQUE (scheme_id, setting_key)
               )
               """)
    op.execute("CREATE INDEX bs_scheme_idx ON core.building_settings (scheme_id)")
    op.execute("CREATE INDEX bs_tenant_idx ON core.building_settings (tenant_id)")

    # ──────────────────────── 4. core.role_permissions (global — no RLS) ──
    # Canonical role → permission-key grant table.
    # Seeded by seeds/role_permissions.py (new file, Phase B).
    # Mirrors the DEFAULT_PERMISSIONS dict in backend/models/user.py.
    op.execute("""
               CREATE TABLE core.role_permissions
               (
                   role           TEXT    NOT NULL, -- core.user_role value
                   permission_key TEXT    NOT NULL, -- e.g. 'can_view_finances'
                   is_granted     BOOLEAN NOT NULL DEFAULT true,
                   PRIMARY KEY (role, permission_key)
               )
               """)
    op.execute("CREATE INDEX rp_role_idx ON core.role_permissions (role)")

    # ─────────────────── 5. core.feature_toggle_resolved() SQL function ──
    # Covers tiers 2 (scheme override) and 3 (global default).
    # Tier 1 (user permission_overrides) is handled at the application layer.
    op.execute("""
               CREATE
               OR REPLACE FUNCTION core.feature_toggle_resolved(
            p_scheme_id   UUID,
            p_feature_key TEXT
        )
        RETURNS BOOLEAN LANGUAGE plpgsql STABLE AS $$
        DECLARE
               v_override BOOLEAN;
            v_default
               BOOLEAN;
               BEGIN
            -- Tier 2: scheme-level override (strata manager editable)
               SELECT is_enabled
               INTO v_override
               FROM core.feature_toggle_overrides
               WHERE scheme_id = p_scheme_id
                 AND feature_key = p_feature_key;

               IF
               v_override IS NOT NULL THEN
                RETURN v_override;
               END IF;

            -- Tier 3: global default (super_admin / seed)
               SELECT is_enabled
               INTO v_default
               FROM core.feature_toggles
               WHERE feature_key = p_feature_key;

               RETURN COALESCE(v_default, false);
               END $$;
               """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0011_config_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP FUNCTION IF EXISTS core.feature_toggle_resolved(UUID, TEXT)")
    op.execute("DROP TABLE IF EXISTS core.building_settings          CASCADE")
    op.execute("DROP TABLE IF EXISTS core.feature_toggle_overrides   CASCADE")
    op.execute("DROP TABLE IF EXISTS core.role_permissions           CASCADE")
    op.execute("DROP TABLE IF EXISTS core.feature_toggles            CASCADE")
