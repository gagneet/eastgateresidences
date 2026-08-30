"""0006 — Modules/plugin registry tables.

Revision: 0006
Previous: 0005

Creates:
- modules.modules          — available plugin modules (system registry)
- modules.module_versions  — individual releases of each module
- modules.tenant_modules   — per-tenant module activations (4-tier feature flag support)
- modules.module_hooks     — declared hook contracts per module version

All plugin code is read-only with respect to these tables; only financial_core
service (via modules.tenant_modules) resolves which plugins are active.
"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0006_modules_plugin_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
               CREATE TABLE modules.modules
               (
                   module_id         UUID PRIMARY KEY            DEFAULT gen_random_uuid(),
                   module_code       TEXT               NOT NULL UNIQUE,
                   module_name       TEXT               NOT NULL,
                   description       TEXT,
                   jurisdiction_code TEXT,
                   module_type       TEXT               NOT NULL DEFAULT 'plugin',
                   status            core.record_status NOT NULL DEFAULT 'active',
                   created_at        TIMESTAMPTZ        NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE modules.module_versions
               (
                   module_version_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   module_id         UUID        NOT NULL REFERENCES modules.modules (module_id),
                   semver            TEXT        NOT NULL,
                   release_notes     TEXT,
                   is_latest         BOOLEAN     NOT NULL DEFAULT false,
                   effective_from    DATE        NOT NULL,
                   effective_to      DATE,
                   manifest_json     JSONB       NOT NULL DEFAULT '{}'::jsonb,
                   created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                   UNIQUE (module_id, semver)
               )
               """)

    # tenant_modules = per-tenant activation of a module version.
    # When financial_core service resolves the plugin chain for a scheme,
    # it loads only modules where (tenant_id, scheme_id) match and active=true.
    op.execute("""
               CREATE TABLE modules.tenant_modules
               (
                   tenant_module_id  UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id         UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id         UUID REFERENCES core.schemes (scheme_id),
                   module_version_id UUID        NOT NULL
                       REFERENCES modules.module_versions (module_version_id),
                   is_active         BOOLEAN     NOT NULL DEFAULT true,
                   config_json       JSONB       NOT NULL DEFAULT '{}'::jsonb,
                   activated_by      UUID REFERENCES core.users (user_id),
                   activated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                   UNIQUE (tenant_id, scheme_id, module_version_id)
               )
               """)

    # Declared hook slots per module version. Allows the registry to validate
    # that a plugin claims a hook before dispatching to it.
    op.execute("""
               CREATE TABLE modules.module_hooks
               (
                   module_hook_id    UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   module_version_id UUID        NOT NULL
                       REFERENCES modules.module_versions (module_version_id),
                   hook_name         TEXT        NOT NULL,
                   hook_description  TEXT,
                   is_blocking       BOOLEAN     NOT NULL DEFAULT false,
                   created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                   UNIQUE (module_version_id, hook_name)
               )
               """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0006_modules_plugin_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP TABLE IF EXISTS modules.module_hooks CASCADE")
    op.execute("DROP TABLE IF EXISTS modules.tenant_modules CASCADE")
    op.execute("DROP TABLE IF EXISTS modules.module_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS modules.modules CASCADE")
