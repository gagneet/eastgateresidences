"""0005 — Ops, compliance, ACT 2026 reform, and core approval/audit tables.

Revision: 0005
Previous: 0004

Creates:
- ops.vendors                      — approved supplier registry
- ops.work_requests                — maintenance requests
- ops.work_orders                  — work orders (linked to vendors)
- ops.quote_requests / vendor_quotes
- ops.vendor_invoices              — AP invoices (three-way match)
- core.approval_policies / requests / steps — dual-control approvals
- core.audit_events                — hash-chained immutable audit log
- compliance.entitlement_schedules / lot_entitlements
- compliance.rule_packs            — jurisdiction rule pack registry
- compliance.disclosure_events     — general disclosure record
- compliance.generated_artifacts   — levy notices, s55 reports etc.
- compliance.manager_licences      — ACT 2026 reform: manager licence evidence
- compliance.ec_training_records   — ACT 2026 reform: EC training register
- compliance.conflict_disclosures  — ACT 2026 reform: hash-chained disclosures
- compliance.manager_contracts     — ACT 2026 reform: bitemporal contracts
- compliance.tenant_meeting_rights — ACT 2026 reform: tenant AGM eligibility
- compliance.acat_dispute_events   — ACT 2026 reform: ACAT dispute evidence
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ----------------------------------------------------------------- ops.*
    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0005_ops_compliance_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
               CREATE TABLE ops.vendors
               (
                   vendor_id                UUID PRIMARY KEY            DEFAULT gen_random_uuid(),
                   tenant_id                UUID               NOT NULL REFERENCES core.tenants (tenant_id),
                   party_id                 UUID               NOT NULL REFERENCES core.parties (party_id),
                   trade_categories         TEXT[] NOT NULL DEFAULT '{}',
                   abn_status               TEXT,
                   gst_registered           BOOLEAN            NOT NULL DEFAULT false,
                   insurance_expiry         DATE,
                   licence_expiry           DATE,
                   preferred_payment_method TEXT,
                   bank_account_token       TEXT,
                   status                   core.record_status NOT NULL DEFAULT 'active',
                   created_at               TIMESTAMPTZ        NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE ops.work_requests
               (
                   work_request_id      UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id            UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id            UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   lot_id               UUID REFERENCES core.lots (lot_id),
                   reported_by_party_id UUID REFERENCES core.parties (party_id),
                   category             TEXT        NOT NULL,
                   priority             TEXT        NOT NULL DEFAULT 'normal',
                   status               TEXT        NOT NULL DEFAULT 'new',
                   summary              TEXT        NOT NULL,
                   details              TEXT,
                   created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE ops.work_orders
               (
                   work_order_id         UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id             UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id             UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   work_request_id       UUID REFERENCES ops.work_requests (work_request_id),
                   vendor_id             UUID REFERENCES ops.vendors (vendor_id),
                   gl_account_id         UUID REFERENCES finance.gl_accounts (gl_account_id),
                   status                TEXT        NOT NULL DEFAULT 'draft',
                   approved_budget_cents BIGINT,
                   created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE ops.quote_requests
               (
                   quote_request_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id        UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id        UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   work_order_id    UUID        NOT NULL REFERENCES ops.work_orders (work_order_id),
                   scope_text       TEXT        NOT NULL,
                   close_at         TIMESTAMPTZ,
                   status           TEXT        NOT NULL DEFAULT 'open',
                   created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE ops.vendor_quotes
               (
                   vendor_quote_id  UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id        UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id        UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   quote_request_id UUID        NOT NULL
                       REFERENCES ops.quote_requests (quote_request_id),
                   vendor_id        UUID        NOT NULL REFERENCES ops.vendors (vendor_id),
                   quoted_cents     BIGINT      NOT NULL CHECK (quoted_cents >= 0),
                   gst_cents        BIGINT      NOT NULL DEFAULT 0,
                   valid_until      DATE,
                   status           TEXT        NOT NULL DEFAULT 'submitted',
                   created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE ops.vendor_invoices
               (
                   vendor_invoice_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id         UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id         UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   vendor_id         UUID        NOT NULL REFERENCES ops.vendors (vendor_id),
                   work_order_id     UUID REFERENCES ops.work_orders (work_order_id),
                   invoice_number    TEXT        NOT NULL,
                   invoice_date      DATE        NOT NULL,
                   due_date          DATE,
                   gross_cents       BIGINT      NOT NULL CHECK (gross_cents >= 0),
                   gst_cents         BIGINT      NOT NULL DEFAULT 0,
                   invoice_hash      TEXT,
                   status            TEXT        NOT NULL DEFAULT 'received',
                   journal_entry_id  UUID REFERENCES finance.journal_entries (journal_entry_id),
                   created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                   UNIQUE (scheme_id, vendor_id, invoice_number)
               )
               """)

    # ------------------------------------------------------------ core.approvals
    op.execute("""
               CREATE TABLE core.approval_policies
               (
                   approval_policy_id UUID PRIMARY KEY            DEFAULT gen_random_uuid(),
                   tenant_id          UUID               NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id          UUID REFERENCES core.schemes (scheme_id),
                   policy_code        TEXT               NOT NULL,
                   threshold_cents    BIGINT,
                   required_roles     TEXT[] NOT NULL,
                   requires_webauthn  BOOLEAN            NOT NULL DEFAULT false,
                   status             core.record_status NOT NULL DEFAULT 'active',
                   created_at         TIMESTAMPTZ        NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE core.approval_requests
               (
                   approval_request_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id           UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id           UUID REFERENCES core.schemes (scheme_id),
                   entity_type         TEXT        NOT NULL,
                   entity_id           UUID        NOT NULL,
                   approval_policy_id  UUID
                       REFERENCES core.approval_policies (approval_policy_id),
                   status              TEXT        NOT NULL DEFAULT 'pending',
                   requested_by        UUID REFERENCES core.users (user_id),
                   created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE core.approval_steps
               (
                   approval_step_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                   approval_request_id     UUID NOT NULL
                       REFERENCES core.approval_requests (approval_request_id),
                   sequence_no             INT  NOT NULL,
                   approver_user_id        UUID REFERENCES core.users (user_id),
                   required_role           TEXT,
                   decision                TEXT,
                   decided_at              TIMESTAMPTZ,
                   webauthn_assertion_hash TEXT,
                   UNIQUE (approval_request_id, sequence_no)
               )
               """)

    # Hash-chained audit events (ADR-017): prev_event_hash + event_hash form
    # a per-scheme chain. The financial_core service computes hashes; the DB
    # stores them immutably.
    op.execute("""
               CREATE TABLE core.audit_events
               (
                   audit_event_id  UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id       UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id       UUID REFERENCES core.schemes (scheme_id),
                   entity_type     TEXT        NOT NULL,
                   entity_id       UUID,
                   action          TEXT        NOT NULL,
                   actor_user_id   UUID REFERENCES core.users (user_id),
                   ip_address      INET,
                   user_agent      TEXT,
                   event_payload   JSONB       NOT NULL DEFAULT '{}'::jsonb,
                   prev_event_hash TEXT,
                   event_hash      TEXT,
                   created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    # ------------------------------------------------------------ compliance.*
    op.execute("""
               CREATE TABLE compliance.entitlement_schedules
               (
                   entitlement_schedule_id UUID PRIMARY KEY        DEFAULT gen_random_uuid(),
                   tenant_id               UUID           NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id               UUID           NOT NULL REFERENCES core.schemes (scheme_id),
                   schedule_type           TEXT           NOT NULL,
                   effective_from          DATE           NOT NULL,
                   effective_to            DATE,
                   total_units             NUMERIC(18, 6) NOT NULL,
                   source_document_id      TEXT,
                   created_at              TIMESTAMPTZ    NOT NULL DEFAULT now(),
                   UNIQUE (scheme_id, schedule_type, effective_from)
               )
               """)

    op.execute("""
               CREATE TABLE compliance.lot_entitlements
               (
                   lot_entitlement_id      UUID PRIMARY KEY        DEFAULT gen_random_uuid(),
                   tenant_id               UUID           NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id               UUID           NOT NULL REFERENCES core.schemes (scheme_id),
                   lot_id                  UUID           NOT NULL REFERENCES core.lots (lot_id),
                   entitlement_schedule_id UUID           NOT NULL
                       REFERENCES compliance.entitlement_schedules (entitlement_schedule_id),
                   entitlement_units       NUMERIC(18, 6) NOT NULL,
                   created_at              TIMESTAMPTZ    NOT NULL DEFAULT now(),
                   UNIQUE (entitlement_schedule_id, lot_id)
               )
               """)

    op.execute("""
               CREATE TABLE compliance.rule_packs
               (
                   rule_pack_id   UUID PRIMARY KEY                      DEFAULT gen_random_uuid(),
                   jurisdiction   compliance.jurisdiction_code NOT NULL,
                   pack_code      TEXT                         NOT NULL,
                   version        TEXT                         NOT NULL,
                   effective_from DATE                         NOT NULL,
                   effective_to   DATE,
                   rules_json     JSONB                        NOT NULL DEFAULT '{}'::jsonb,
                   created_at     TIMESTAMPTZ                  NOT NULL DEFAULT now(),
                   UNIQUE (jurisdiction, pack_code, version)
               )
               """)

    op.execute("""
               CREATE TABLE compliance.disclosure_events
               (
                   disclosure_event_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id           UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id           UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   disclosure_type     TEXT        NOT NULL,
                   related_party_id    UUID REFERENCES core.parties (party_id),
                   related_entity_type TEXT,
                   related_entity_id   UUID,
                   disclosure_text     TEXT        NOT NULL,
                   disclosed_to        TEXT        NOT NULL,
                   disclosed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
                   document_id         TEXT,
                   audit_event_id      UUID REFERENCES core.audit_events (audit_event_id)
               )
               """)

    op.execute("""
               CREATE TABLE compliance.generated_artifacts
               (
                   generated_artifact_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id             UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id             UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   artifact_type         TEXT        NOT NULL,
                   period_start          DATE,
                   period_end            DATE,
                   storage_key           TEXT        NOT NULL,
                   content_hash          TEXT        NOT NULL,
                   generated_by          UUID REFERENCES core.users (user_id),
                   generated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
                   metadata              JSONB       NOT NULL DEFAULT '{}'::jsonb
               )
               """)

    # ------------------------------------------ ACT 2026 reform compliance tables
    # These tables support the ACT inquiry recommendations (ABC News, 30 Apr 2026):
    # mandatory manager licensing, EC training, conflict/commission disclosure,
    # no-cause termination, tenant AGM attendance, and ACAT dispute evidence.

    op.execute("""
               CREATE TABLE compliance.manager_licences
               (
                   manager_licence_id   UUID PRIMARY KEY                      DEFAULT gen_random_uuid(),
                   tenant_id            UUID                         NOT NULL REFERENCES core.tenants (tenant_id),
                   party_id             UUID                         NOT NULL REFERENCES core.parties (party_id),
                   jurisdiction_code    compliance.jurisdiction_code NOT NULL,
                   licence_number       TEXT,
                   licence_class        TEXT,
                   issued_on            DATE,
                   expires_on           DATE,
                   status               TEXT                         NOT NULL DEFAULT 'pending',
                   evidence_document_id UUID,
                   created_at           TIMESTAMPTZ                  NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE compliance.ec_training_records
               (
                   ec_training_record_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                   tenant_id             UUID NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id             UUID NOT NULL REFERENCES core.schemes (scheme_id),
                   user_id               UUID NOT NULL REFERENCES core.users (user_id),
                   training_type         TEXT NOT NULL,
                   provider              TEXT,
                   completed_on          DATE,
                   expires_on            DATE,
                   evidence_document_id  UUID,
                   status                TEXT NOT NULL    DEFAULT 'current'
               )
               """)

    # Hash-chained — amount_cents and percentage capture commission/related-party value.
    op.execute("""
               CREATE TABLE compliance.conflict_disclosures
               (
                   conflict_disclosure_id UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id              UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id              UUID REFERENCES core.schemes (scheme_id),
                   party_id               UUID        NOT NULL REFERENCES core.parties (party_id),
                   related_party_id       UUID REFERENCES core.parties (party_id),
                   disclosure_type        TEXT        NOT NULL,
                   description            TEXT        NOT NULL,
                   amount_cents           BIGINT,
                   percentage             NUMERIC(9, 4),
                   effective_from         DATE,
                   effective_to           DATE,
                   disclosed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
                   disclosed_by           UUID REFERENCES core.users (user_id),
                   evidence_document_id   UUID,
                   entry_hash             TEXT        NOT NULL,
                   prev_entry_hash        TEXT
               )
               """)

    # Manager contracts with no-cause termination tracking.
    op.execute("""
               CREATE TABLE compliance.manager_contracts
               (
                   manager_contract_id               UUID PRIMARY KEY     DEFAULT gen_random_uuid(),
                   tenant_id                         UUID        NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id                         UUID        NOT NULL REFERENCES core.schemes (scheme_id),
                   manager_party_id                  UUID        NOT NULL REFERENCES core.parties (party_id),
                   starts_on                         DATE        NOT NULL,
                   ends_on                           DATE,
                   termination_without_cause_allowed BOOLEAN     NOT NULL DEFAULT false,
                   termination_notice_days           INTEGER,
                   restrictive_terms_jsonb           JSONB       NOT NULL DEFAULT '{}'::jsonb,
                   status                            TEXT        NOT NULL DEFAULT 'active',
                   contract_document_id              UUID,
                   created_at                        TIMESTAMPTZ NOT NULL DEFAULT now()
               )
               """)

    op.execute("""
               CREATE TABLE compliance.tenant_meeting_rights
               (
                   tenant_meeting_right_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                   tenant_id               UUID    NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id               UUID    NOT NULL REFERENCES core.schemes (scheme_id),
                   lot_id                  UUID REFERENCES core.lots (lot_id),
                   tenant_party_id         UUID    NOT NULL REFERENCES core.parties (party_id),
                   meeting_id              UUID,
                   attendance_allowed      BOOLEAN NOT NULL DEFAULT false,
                   voting_allowed          BOOLEAN NOT NULL DEFAULT false,
                   invitation_sent_at      TIMESTAMPTZ,
                   attended_at             TIMESTAMPTZ,
                   notes                   TEXT
               )
               """)

    op.execute("""
               CREATE TABLE compliance.acat_dispute_events
               (
                   acat_dispute_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                   tenant_id             UUID NOT NULL REFERENCES core.tenants (tenant_id),
                   scheme_id             UUID NOT NULL REFERENCES core.schemes (scheme_id),
                   case_id               UUID,
                   dispute_type          TEXT NOT NULL,
                   filed_on              DATE,
                   status                TEXT NOT NULL    DEFAULT 'open',
                   next_action_due_on    DATE,
                   outcome_summary       TEXT,
                   evidence_document_id  UUID
               )
               """)


def downgrade() -> None:
    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0005_ops_compliance_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("DROP TABLE IF EXISTS compliance.acat_dispute_events CASCADE")
    op.execute("DROP TABLE IF EXISTS compliance.tenant_meeting_rights CASCADE")
    op.execute("DROP TABLE IF EXISTS compliance.manager_contracts CASCADE")
    op.execute("DROP TABLE IF EXISTS compliance.conflict_disclosures CASCADE")
    op.execute("DROP TABLE IF EXISTS compliance.ec_training_records CASCADE")
    op.execute("DROP TABLE IF EXISTS compliance.manager_licences CASCADE")
    op.execute("DROP TABLE IF EXISTS compliance.generated_artifacts CASCADE")
    op.execute("DROP TABLE IF EXISTS compliance.disclosure_events CASCADE")
    op.execute("DROP TABLE IF EXISTS compliance.rule_packs CASCADE")
    op.execute("DROP TABLE IF EXISTS compliance.lot_entitlements CASCADE")
    op.execute("DROP TABLE IF EXISTS compliance.entitlement_schedules CASCADE")
    op.execute("DROP TABLE IF EXISTS core.audit_events CASCADE")
    op.execute("DROP TABLE IF EXISTS core.approval_steps CASCADE")
    op.execute("DROP TABLE IF EXISTS core.approval_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS core.approval_policies CASCADE")
    op.execute("DROP TABLE IF EXISTS ops.vendor_invoices CASCADE")
    op.execute("DROP TABLE IF EXISTS ops.vendor_quotes CASCADE")
    op.execute("DROP TABLE IF EXISTS ops.quote_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS ops.work_orders CASCADE")
    op.execute("DROP TABLE IF EXISTS ops.work_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS ops.vendors CASCADE")
