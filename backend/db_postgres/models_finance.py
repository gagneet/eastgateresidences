"""SQLAlchemy ORM model for finance.arrears_status_snapshot.

# @featuretrace:financial-postgres-models
# Layer: ORM models
# Data flow: backend/db_postgres/models_finance.py → backend/db_postgres/repos/ → backend/routers/finance.py
# Related: backend/alembic/versions/0077_arrears_grace_meta.py
#          backend/services/financial_core/adapters/db_postgres/models.py (PgLevyRun/PgLevyItem —
#          the canonical finance.levy_runs/finance.levy_items ORM models; migration 0077's new
#          grace-deadline columns on those two tables are mapped there, not duplicated here)

Note: this module previously also defined LevyRun/LevyItem classes re-mapping
finance.levy_runs/finance.levy_items. Removed 2026-08-03 — they duplicated
PgLevyRun/PgLevyItem (services/financial_core/adapters/db_postgres/models.py),
which already map those same tables onto the same Base.metadata registry;
having two declarative classes for one (schema, tablename) pair raises
sqlalchemy.exc.InvalidRequestError the moment both modules are imported in the
same process. The migration 0077 columns those duplicate classes carried are
now mapped on PgLevyRun/PgLevyItem instead.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from db_postgres.base import Base


class ArrearsStatusSnapshot(Base):
    """Precomputed arrears status snapshot, refreshed nightly or on-demand.

    Separates state (grace deadline, arrears amount, status) from transactional levy_items.
    Enables O(1) grace-deadline filtering without runtime calculation.
    RLS scoped by scheme_id / tenant_id.

    # @featuretrace:arrears-status-snapshot — grace-deadline-aware arrears tracking
    # Layer: data model
    # Data flow: finance.levy_items + finance.levy_runs → background job (nightly)
    #            → finance.arrears_status_snapshot → backend/routers/finance.py
    #            (get_levy_kpi, get_finance_summary, get_arrears_board)
    # Related: backend/workers/snapshot_refresh_job.py (or cron job)
    #          backend/db_postgres/repos/arrears_snapshot_repo.py
    # Refresh: UPSERT on demand or nightly via background job (incremental, not full sweep)
    """

    __tablename__ = "arrears_status_snapshot"
    __table_args__ = ({"schema": "finance"},)

    snapshot_id: Mapped[str] = mapped_column(PGUUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(PGUUID(as_uuid=False), ForeignKey("core.tenants.tenant_id", ondelete="CASCADE"), nullable=False)
    scheme_id: Mapped[str] = mapped_column(PGUUID(as_uuid=False), ForeignKey("core.schemes.scheme_id", ondelete="CASCADE"), nullable=False)
    lot_id: Mapped[str] = mapped_column(PGUUID(as_uuid=False), ForeignKey("core.lots.lot_id", ondelete="CASCADE"), nullable=False)
    owner_party_id: Mapped[str] = mapped_column(PGUUID(as_uuid=False), ForeignKey("core.parties.party_id", ondelete="CASCADE"), nullable=False)
    fund_id: Mapped[str] = mapped_column(PGUUID(as_uuid=False), ForeignKey("finance.funds.fund_id"), nullable=False)

    financial_year: Mapped[str] = mapped_column(nullable=False)
    quarter_no: Mapped[Optional[int]] = mapped_column(nullable=True)

    due_date: Mapped[date] = mapped_column(nullable=False)
    grace_deadline_date: Mapped[date] = mapped_column(nullable=False, comment="due_date + grace_period_days")
    is_past_grace: Mapped[bool] = mapped_column(nullable=False, default=False)
    days_overdue: Mapped[int] = mapped_column(nullable=False, default=0)

    principal_cents: Mapped[int] = mapped_column(nullable=False, default=0)
    interest_cents: Mapped[int] = mapped_column(nullable=False, default=0)
    recovery_costs_cents: Mapped[int] = mapped_column(nullable=False, default=0)
    paid_cents: Mapped[int] = mapped_column(nullable=False, default=0)

    arrears_amount_cents: Mapped[int] = mapped_column(nullable=False, default=0, comment="principal + interest + recovery - paid, only if is_past_grace")
    status: Mapped[str] = mapped_column(nullable=False, comment="'arrears' if is_past_grace, 'current', 'credit', 'paid_up'")

    last_payment_date: Mapped[Optional[date]] = mapped_column(nullable=True)
    last_payment_amount_cents: Mapped[Optional[int]] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    refreshed_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow, comment="When this snapshot was last computed")
