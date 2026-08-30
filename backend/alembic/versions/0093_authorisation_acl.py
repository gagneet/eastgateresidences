"""0093 — Authorisation ACL: resource grants, authorities, delegations, policy versions.

## Why

`services/capability_registry` ships two capabilities that declare
``required_authority``:

    building.finance.payment.execute       requires a 'resolution'
    building.management.delegated.execute  requires a 'delegation'

`services/authorisation_context.hydrate_authorisation_claims()` returns empty
``active_resolution_ids`` / ``active_delegation_ids`` because the tables that
would answer do not exist. Both capabilities therefore deny unconditionally —
correct fail-closed behaviour, and also why treasurer payment execution cannot
be enabled for anyone. These tables are what make the ACT authority model
expressible.

See ``tasks/GAP-SEC-004`` and
``docs/security/acl_information_access_implementation_plan.md`` §3-§5.

## Separation of duty is enforced in the schema, not only in code

Two CHECK constraints encode rules that would otherwise depend on every call
site remembering them:

* ``authorities_recorder_not_grantee_chk`` — whoever RECORDS an authority may
  not be the person it is granted to. The settled process is that the Secretary,
  Strata Manager or EC Chairman records the EC resolution and the **treasurer**
  exercises it (ACT s 43). This constraint makes the collapse of those two roles
  a database error rather than a policy someone can forget.
* ``delegations_grantor_not_grantee_chk`` — nobody delegates a power to
  themselves.

## Tenancy

The three tenant-owned tables carry ``tenant_id NOT NULL`` with RLS **enabled
and forced** and a ``tenant_isolation_*`` policy, matching every other
tenant-owned table after 0091/0092. ``tests/backend/test_rls_coverage.py``
sweeps for exactly this, so a missing policy here fails CI.

``core.policy_versions`` is deliberately NOT tenant-scoped: it is a global
catalogue of policy rulesets, and the plan requires global policy definitions to
be clearly separated from tenant-owned assignments. Having no ``tenant_id``, it
is outside the RLS sweep by construction rather than by exemption.

## Money

``amount_limit_cents`` is an integer-cents BIGINT, never a float, per the ledger
precision rule. A NULL limit means the authority carries no monetary cap — it
does NOT mean zero.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0093_authorisation_acl"
down_revision = "0092_force_tenant_rls"
branch_labels = None
depends_on = None


_TENANT_OWNED = [
    ("core", "resource_access_grants"),
    ("governance", "authorities"),
    ("governance", "delegations"),
]


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("is_test_data", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    """Create the four authorisation tables, their indexes and their RLS."""

    # ── core.resource_access_grants ──────────────────────────────────────────
    # Generalises documents.document_access_grants beyond documents, keeping the
    # same column shape deliberately so the two can converge later.
    op.create_table(
        "resource_access_grants",
        sa.Column("resource_access_grant_id", sa.UUID(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.UUID(), nullable=False),
        # Exactly one grantee dimension. A grant to "everyone" is not expressible
        # here on purpose — that is a role capability, not an ACL entry.
        sa.Column("grantee_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("grantee_role", sa.Text(), nullable=True),
        sa.Column("grantee_party_id", sa.UUID(), sa.ForeignKey("core.parties.party_id"), nullable=True),
        sa.Column("permission_level", sa.Text(), nullable=False),
        # Columns withheld even when the grant otherwise allows the read. This is
        # how a decision's obligations are populated (plan §5).
        sa.Column("field_mask", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("granted_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=False),
        # Why this grant exists. NOT NULL because an ACL entry nobody can explain
        # is an ACL entry nobody can safely revoke.
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(
            "(grantee_user_id IS NOT NULL)::int + (grantee_role IS NOT NULL)::int "
            "+ (grantee_party_id IS NOT NULL)::int = 1",
            name="resource_access_grants_one_grantee_chk",
        ),
        sa.CheckConstraint(
            "permission_level IN ('view', 'comment', 'edit', 'approve')",
            name="resource_access_grants_permission_level_chk",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > starts_at",
            name="resource_access_grants_window_chk",
        ),
        schema="core",
    )
    op.create_index("resource_access_grants_tenant_scheme_idx", "resource_access_grants",
                    ["tenant_id", "scheme_id"], schema="core")
    op.create_index("resource_access_grants_resource_idx", "resource_access_grants",
                    ["resource_type", "resource_id"], schema="core")
    op.create_index("resource_access_grants_grantee_user_idx", "resource_access_grants",
                    ["grantee_user_id"], schema="core")

    # ── governance.authorities ───────────────────────────────────────────────
    # An EC/OC resolution as a structured authority object rather than free text,
    # so a capability check can resolve against it.
    op.create_table(
        "authorities",
        sa.Column("authority_id", sa.UUID(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("authority_type", sa.Text(), nullable=False),
        # Provenance: the decision register entry this authority came from.
        sa.Column("decision_id", sa.UUID(), sa.ForeignKey("governance.decisions.decision_id"), nullable=True),
        sa.Column("granted_to_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=True),
        sa.Column("granted_to_office", sa.Text(), nullable=True),
        # The capability name this authority permits, e.g.
        # 'building.finance.payment.execute'.
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.UUID(), nullable=True),
        # Integer cents. NULL means "no monetary cap", NOT zero.
        sa.Column("amount_limit_cents", sa.BigInteger(), nullable=True),
        sa.Column("conditions", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("decided_on", sa.Date(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("source_document_id", sa.UUID(), nullable=True),
        # Who entered this authority into the system. See the separation-of-duty
        # constraint below.
        sa.Column("recorded_by", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(
            "authority_type IN ('resolution', 'delegation', 'contract_function')",
            name="authorities_type_chk",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'exhausted', 'revoked', 'expired')",
            name="authorities_status_chk",
        ),
        sa.CheckConstraint(
            "(granted_to_user_id IS NOT NULL) OR (granted_to_office IS NOT NULL)",
            name="authorities_grantee_chk",
        ),
        sa.CheckConstraint(
            "amount_limit_cents IS NULL OR amount_limit_cents >= 0",
            name="authorities_amount_limit_chk",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name="authorities_window_chk",
        ),
        # Separation of duty, enforced by the database: the person who records an
        # authority cannot be the person it empowers. ACT s 43 puts payment
        # approval with the treasurer and the authorising decision with the EC;
        # the Secretary/Manager/Chairman records it. Collapsing those is a
        # constraint violation, not a policy someone can forget.
        sa.CheckConstraint(
            "granted_to_user_id IS NULL OR granted_to_user_id <> recorded_by",
            name="authorities_recorder_not_grantee_chk",
        ),
        schema="governance",
    )
    op.create_index("authorities_tenant_scheme_idx", "authorities",
                    ["tenant_id", "scheme_id"], schema="governance")
    op.create_index("authorities_lookup_idx", "authorities",
                    ["scheme_id", "action", "status"], schema="governance")
    op.create_index("authorities_grantee_idx", "authorities",
                    ["granted_to_user_id"], schema="governance")

    # ── governance.delegations ───────────────────────────────────────────────
    # s 44 and ss 50(2)/52/58 written delegations. Kept separate from
    # authorities because a delegation has a GRANTOR who must themselves have
    # held the power being delegated.
    op.create_table(
        "delegations",
        sa.Column("delegation_id", sa.UUID(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), sa.ForeignKey("core.tenants.tenant_id"), nullable=False),
        sa.Column("scheme_id", sa.UUID(), sa.ForeignKey("core.schemes.scheme_id"), nullable=False),
        sa.Column("grantor_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=False),
        sa.Column("grantee_user_id", sa.UUID(), sa.ForeignKey("core.users.user_id"), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("scope_resource_type", sa.Text(), nullable=True),
        sa.Column("scope_resource_id", sa.UUID(), nullable=True),
        sa.Column("conditions", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("restrictions", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("ends_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("source_document_id", sa.UUID(), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(
            "grantor_user_id <> grantee_user_id",
            name="delegations_grantor_not_grantee_chk",
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name="delegations_window_chk",
        ),
        schema="governance",
    )
    op.create_index("delegations_tenant_scheme_idx", "delegations",
                    ["tenant_id", "scheme_id"], schema="governance")
    op.create_index("delegations_grantee_idx", "delegations",
                    ["grantee_user_id", "capability"], schema="governance")

    # ── core.policy_versions (GLOBAL — no tenant_id, deliberately) ───────────
    op.create_table(
        "policy_versions",
        sa.Column("policy_version", sa.Text(), primary_key=True),
        sa.Column("jurisdiction", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema="core",
    )
    # Seed the version the evaluator already stamps on every Decision, so an
    # audited decision references a real row from day one.
    op.execute("""
        INSERT INTO core.policy_versions (policy_version, jurisdiction, effective_from, notes)
        VALUES ('act-r25-1', 'ACT', DATE '2026-06-26',
                'Unit Titles (Management) Act 2011 (ACT) republication R25. Baseline ruleset '
                'for services/capability_registry.py; see docs/security/act_authorisation_model.md.')
        ON CONFLICT (policy_version) DO NOTHING
    """)

    # ── RLS on the three tenant-owned tables ─────────────────────────────────
    for schema, table in _TENANT_OWNED:
        policy = f"tenant_isolation_{schema}_{table}"
        op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
        # FORCE matters: the app connects as the table owner, and an owner
        # bypasses RLS without it (see 0092).
        op.execute(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {schema}.{table}")
        op.execute(
            f"CREATE POLICY {policy} ON {schema}.{table} "
            f"USING (tenant_id = core.current_tenant_id())"
        )


def downgrade() -> None:
    """Drop the four tables. Reverses cleanly: nothing else references them yet."""
    for schema, table in reversed(_TENANT_OWNED):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{schema}_{table} ON {schema}.{table}")
    op.drop_table("policy_versions", schema="core")
    op.drop_table("delegations", schema="governance")
    op.drop_table("authorities", schema="governance")
    op.drop_table("resource_access_grants", schema="core")
