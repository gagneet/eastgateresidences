"""0018 — Create saga orchestration tables.

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-02

Adds two tables for durable sagas (multi-step workflows with idempotency):

1. core.saga_runs — one row per saga instance (e.g., one disbursement workflow)
   - run_id (UUID PK)
   - saga_type (TEXT) — e.g. 'disbursement', 'reconciliation'
   - status (saga_status enum) — draft, running, completed, failed, compensated
   - initiator_user_id (UUID) — who started the saga
   - initiated_at (TIMESTAMPTZ)
   - completed_at (TIMESTAMPTZ, nullable)
   - failed_reason (TEXT, nullable) — if status = failed

2. core.saga_steps — one row per step within a saga
   - step_id (UUID PK)
   - run_id (UUID FK to saga_runs)
   - step_name (TEXT) — e.g. 'validate_payment', 'post_journal', 'notify'
   - step_number (INT) — ordering within the saga
   - status (saga_status enum) — draft, running, completed, failed, compensated
   - idempotency_key (TEXT, nullable) — for exactly-once semantics
   - input_payload (JSONB) — the command passed to this step
   - output_result (JSONB, nullable) — the result if completed
   - error_details (JSONB, nullable) — if failed
   - started_at (TIMESTAMPTZ)
   - completed_at (TIMESTAMPTZ, nullable)
   - retry_count (INT) — how many times this step has been retried

Each saga is an atomic unit of work. Steps are executed in order. If a step
fails, the saga can be retried or compensated (rolled back). Idempotency keys
allow safe replay without duplicate side effects.

Quotas for Phase F use: multi-step disbursement validation, reconciliation
batch processing.
"""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Create saga_status enum
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: upgrade
    Path: backend/alembic/versions/0018_create_saga_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.execute("""
        CREATE TYPE core.saga_status AS ENUM (
            'draft',
            'running',
            'completed',
            'failed',
            'compensated'
        )
    """)

    # ──────────────────────────────────────────────────────────────────────
    # Create saga_runs table (saga instances)
    # ──────────────────────────────────────────────────────────────────────

    op.create_table(
        'saga_runs',
        sa.Column('run_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('scheme_id', sa.UUID(), nullable=True),  # Some sagas are tenant-level
        sa.Column('saga_type', sa.String(), nullable=False),
        sa.Column('status', sa.Enum('draft', 'running', 'completed', 'failed', 'compensated', name='saga_status',
                                    create_type=False), nullable=False, server_default='draft'),
        sa.Column('initiator_user_id', sa.UUID(), nullable=False),
        sa.Column('initiated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failed_reason', sa.String(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['tenant_id'], ['core.tenants.tenant_id']),
        sa.ForeignKeyConstraint(['initiator_user_id'], ['core.users.user_id']),
        sa.ForeignKeyConstraint(['scheme_id'], ['core.schemes.scheme_id']),
        sa.PrimaryKeyConstraint('run_id'),
        schema='core'
    )

    # ──────────────────────────────────────────────────────────────────────
    # Create saga_steps table (steps within a saga)
    # ──────────────────────────────────────────────────────────────────────

    op.create_table(
        'saga_steps',
        sa.Column('step_id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('run_id', sa.UUID(), nullable=False),
        sa.Column('step_name', sa.String(), nullable=False),
        sa.Column('step_number', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('draft', 'running', 'completed', 'failed', 'compensated', name='saga_status',
                                    create_type=False), nullable=False, server_default='draft'),
        sa.Column('idempotency_key', sa.String(), nullable=True),
        sa.Column('input_payload', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('output_result', sa.JSON(), nullable=True),
        sa.Column('error_details', sa.JSON(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['tenant_id'], ['core.tenants.tenant_id']),
        sa.ForeignKeyConstraint(['run_id'], ['core.saga_runs.run_id']),
        sa.PrimaryKeyConstraint('step_id'),
        sa.UniqueConstraint('run_id', 'step_number', name='uq_saga_steps_run_step_number'),
        schema='core'
    )

    # ──────────────────────────────────────────────────────────────────────
    # Indexes for saga queries
    # ──────────────────────────────────────────────────────────────────────

    op.create_index('saga_runs_tenant_type_idx', 'saga_runs', ['tenant_id', 'saga_type'], schema='core')
    op.create_index('saga_runs_status_idx', 'saga_runs', ['status'], schema='core')
    op.create_index('saga_runs_scheme_idx', 'saga_runs', ['scheme_id'], schema='core',
                    postgresql_where=sa.text('scheme_id IS NOT NULL'))

    op.create_index('saga_steps_run_idx', 'saga_steps', ['run_id'], schema='core')
    op.create_index('saga_steps_status_idx', 'saga_steps', ['status'], schema='core')
    op.create_index('saga_steps_idempotency_idx', 'saga_steps', ['idempotency_key'], schema='core',
                    postgresql_where=sa.text('idempotency_key IS NOT NULL'))


def downgrade() -> None:
    # ──────────────────────────────────────────────────────────────────────
    # Drop tables and enum
    # ──────────────────────────────────────────────────────────────────────

    """Generated function header.

    Function: downgrade
    Path: backend/alembic/versions/0018_create_saga_tables.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    op.drop_index('saga_steps_idempotency_idx', schema='core')
    op.drop_index('saga_steps_status_idx', schema='core')
    op.drop_index('saga_steps_run_idx', schema='core')

    op.drop_index('saga_runs_scheme_idx', schema='core')
    op.drop_index('saga_runs_status_idx', schema='core')
    op.drop_index('saga_runs_tenant_type_idx', schema='core')

    op.drop_table('saga_steps', schema='core')
    op.drop_table('saga_runs', schema='core')

    op.execute('DROP TYPE IF EXISTS core.saga_status CASCADE')
