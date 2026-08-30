"""Repository for ArrearsStatusSnapshot table operations.

Handles CRUD and efficient filtering for grace-deadline-aware arrears queries.
All queries are automatically RLS-filtered by tenant/scheme boundary.

# @featuretrace:arrears-snapshot-repository
# Layer: Data access (repository pattern)
# Data flow: backend/routers/finance.py → ArrearsSnapshotRepository → finance.arrears_status_snapshot
# Related: backend/db_postgres/models_finance.py:ArrearsStatusSnapshot
#          backend/workers/snapshot_refresh_job.py (refresh triggers)
"""
from __future__ import annotations

from datetime import datetime, date
from uuid import UUID
from typing import Optional

from sqlalchemy import select, update, delete, and_, func, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from db_postgres.models_finance import ArrearsStatusSnapshot


class ArrearsSnapshotRepository:
    """Repository for ArrearsStatusSnapshot queries and mutations."""

    def __init__(self, session: AsyncSession):
        """Initialize with database session."""
        self.session = session

    async def get_snapshot(self, scheme_id: UUID, lot_id: UUID, fund_id: UUID, financial_year: str, quarter_no: Optional[int] = None) -> Optional[ArrearsStatusSnapshot]:
        """Fetch a single snapshot by scheme/lot/fund/period key."""
        query = select(ArrearsStatusSnapshot).where(
            and_(
                ArrearsStatusSnapshot.scheme_id == scheme_id,
                ArrearsStatusSnapshot.lot_id == lot_id,
                ArrearsStatusSnapshot.fund_id == fund_id,
                ArrearsStatusSnapshot.financial_year == financial_year,
                ArrearsStatusSnapshot.quarter_no == quarter_no,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_all_for_scheme(self, scheme_id: UUID, order_by: str = "due_date") -> list[ArrearsStatusSnapshot]:
        """List all snapshots for a scheme (RLS-filtered by tenant)."""
        query = select(ArrearsStatusSnapshot).where(
            ArrearsStatusSnapshot.scheme_id == scheme_id
        )
        if order_by == "due_date":
            query = query.order_by(asc(ArrearsStatusSnapshot.due_date))
        elif order_by == "lot_id":
            query = query.order_by(asc(ArrearsStatusSnapshot.lot_id))
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_past_grace_for_scheme(self, scheme_id: UUID, today: Optional[date] = None) -> list[ArrearsStatusSnapshot]:
        """List all snapshots where grace deadline has passed (is_past_grace=true).

        Used for Arrears Recovery Board filtering.
        """
        if today is None:
            today = date.today()

        query = select(ArrearsStatusSnapshot).where(
            and_(
                ArrearsStatusSnapshot.scheme_id == scheme_id,
                ArrearsStatusSnapshot.is_past_grace == True,
            )
        ).order_by(desc(ArrearsStatusSnapshot.arrears_amount_cents))

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def list_arrears_by_lot(self, scheme_id: UUID, lot_id: UUID, today: Optional[date] = None) -> list[ArrearsStatusSnapshot]:
        """List all unpaid periods for a lot where grace has passed."""
        if today is None:
            today = date.today()

        query = select(ArrearsStatusSnapshot).where(
            and_(
                ArrearsStatusSnapshot.scheme_id == scheme_id,
                ArrearsStatusSnapshot.lot_id == lot_id,
                ArrearsStatusSnapshot.is_past_grace == True,
                ArrearsStatusSnapshot.arrears_amount_cents > 0,
            )
        ).order_by(asc(ArrearsStatusSnapshot.financial_year), asc(ArrearsStatusSnapshot.quarter_no))

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def sum_arrears_for_scheme(self, scheme_id: UUID, today: Optional[date] = None) -> int:
        """Sum total arrears (in cents) for a scheme (past grace only)."""
        if today is None:
            today = date.today()

        query = select(func.sum(ArrearsStatusSnapshot.arrears_amount_cents)).where(
            and_(
                ArrearsStatusSnapshot.scheme_id == scheme_id,
                ArrearsStatusSnapshot.is_past_grace == True,
            )
        )
        result = await self.session.execute(query)
        total = result.scalar()
        return total if total else 0

    async def count_lots_in_arrears(self, scheme_id: UUID, today: Optional[date] = None) -> int:
        """Count distinct lots with any arrears (past grace)."""
        if today is None:
            today = date.today()

        query = select(func.count(func.distinct(ArrearsStatusSnapshot.lot_id))).where(
            and_(
                ArrearsStatusSnapshot.scheme_id == scheme_id,
                ArrearsStatusSnapshot.is_past_grace == True,
                ArrearsStatusSnapshot.arrears_amount_cents > 0,
            )
        )
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def upsert_snapshot(self, snapshot: ArrearsStatusSnapshot) -> ArrearsStatusSnapshot:
        """Create or update a snapshot row (idempotent by unique key).

        Unique key: (scheme_id, lot_id, fund_id, financial_year, quarter_no)
        """
        # Check if exists
        existing = await self.get_snapshot(
            scheme_id=snapshot.scheme_id,
            lot_id=snapshot.lot_id,
            fund_id=snapshot.fund_id,
            financial_year=snapshot.financial_year,
            quarter_no=snapshot.quarter_no,
        )

        if existing:
            # Update
            stmt = (
                update(ArrearsStatusSnapshot)
                .where(ArrearsStatusSnapshot.snapshot_id == existing.snapshot_id)
                .values(
                    due_date=snapshot.due_date,
                    grace_deadline_date=snapshot.grace_deadline_date,
                    is_past_grace=snapshot.is_past_grace,
                    days_overdue=snapshot.days_overdue,
                    principal_cents=snapshot.principal_cents,
                    interest_cents=snapshot.interest_cents,
                    recovery_costs_cents=snapshot.recovery_costs_cents,
                    paid_cents=snapshot.paid_cents,
                    arrears_amount_cents=snapshot.arrears_amount_cents,
                    status=snapshot.status,
                    last_payment_date=snapshot.last_payment_date,
                    last_payment_amount_cents=snapshot.last_payment_amount_cents,
                    updated_at=datetime.utcnow(),
                    refreshed_at=datetime.utcnow(),
                )
            )
            await self.session.execute(stmt)
            return existing  # Return updated (caller should refresh if needed)

        # Create new
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def delete_snapshots_for_levy_run(self, scheme_id: UUID, financial_year: str, quarter_no: Optional[int] = None) -> int:
        """Delete all snapshots for a given levy run (when replayed/recalculated).

        Used during levy run corrections.
        """
        query = delete(ArrearsStatusSnapshot).where(
            and_(
                ArrearsStatusSnapshot.scheme_id == scheme_id,
                ArrearsStatusSnapshot.financial_year == financial_year,
                ArrearsStatusSnapshot.quarter_no == quarter_no,
            )
        )
        result = await self.session.execute(query)
        return result.rowcount or 0

    async def refresh_snapshots_for_scheme_incremental(
        self,
        scheme_id: UUID,
        levy_runs: list,  # List of LevyRun objects with levy_items
        today: Optional[date] = None,
    ) -> int:
        """Refresh snapshots for specific levy runs (incremental, not full sweep).

        Called after payment posting or levy run generation.
        Returns count of snapshots created/updated.
        """
        if today is None:
            today = date.today()

        count = 0
        for run in levy_runs:
            for item in run.levy_items:
                outstanding_cents = (
                    item.principal_cents + item.interest_cents + item.recovery_costs_cents - item.paid_cents
                )

                if outstanding_cents >= 0:
                    status = "arrears" if item.is_past_grace else "current"
                    arrears_amount = outstanding_cents if item.is_past_grace else 0
                else:
                    status = "credit"
                    arrears_amount = 0

                snapshot = ArrearsStatusSnapshot(
                    tenant_id=item.tenant_id,
                    scheme_id=item.scheme_id,
                    lot_id=item.lot_id,
                    owner_party_id=item.owner_party_id,
                    fund_id=item.fund_id,
                    financial_year=run.financial_year,
                    quarter_no=run.quarter_no,
                    due_date=run.due_date,
                    grace_deadline_date=item.grace_deadline_date,
                    is_past_grace=item.is_past_grace,
                    days_overdue=item.days_overdue,
                    principal_cents=item.principal_cents,
                    interest_cents=item.interest_cents,
                    recovery_costs_cents=item.recovery_costs_cents,
                    paid_cents=item.paid_cents,
                    arrears_amount_cents=arrears_amount,
                    status=status,
                    refreshed_at=datetime.utcnow(),
                )

                await self.upsert_snapshot(snapshot)
                count += 1

        return count
