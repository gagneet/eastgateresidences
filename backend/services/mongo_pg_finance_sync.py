# @featuretrace:financial_core — close the Mongo->Postgres finance gap, through the one door.
# Layer: service
# Data flow: Mongo levy_payments (the store that currently SERVES finance) -> diff against
#            finance.receipts -> demo_bank_transactions candidates -> matching/approval ->
#            GL -> finance.* (building-scoped).
# Related: backend/workers/outbox_relay.py (the OPPOSITE direction: PG -> Mongo audit log)
#          backend/services/finance_metrics/portal_snapshot_pg_writer.py (dual-write precedent)
#          backend/integrations/demo_bank/ingestion.py (the intake path this feeds)
#          backend/scripts/data_repair/sync_mongo_payments_to_postgres.py (the runner)
# Collection: levy_payments (read), finance.receipts (read), demo_bank_transactions (write)
# Tests: tests/backend/test_mongo_pg_finance_sync.py
"""Detect and close the Mongo->Postgres finance drift, without bypassing Demo Bank.

The gap this exists to close
----------------------------
``workers/outbox_relay.py`` moves events **Postgres -> MongoDB**, and its own
header says so. It is an audit log, not a ledger sync. There has never been
anything going the other way. Meanwhile MongoDB is what actually *serves*
finance for East Gate (``core.domain_cutover_status`` has no ``finance_ledger``
row, so ``require_domain_source`` fails closed to Mongo per footgun #17), which
means **every live finance write lands in Mongo and nothing carries it back**.
Postgres therefore drifts by exactly the volume of live activity, silently.
Measured 2026-08-28: 42 of 87 lots disagreed with the operator's portal.

Why this does NOT mirror rows into ``finance.*``
------------------------------------------------
The obvious implementation — copy ``levy_payments`` into ``finance.receipts`` —
is the one thing that must not be built. CLAUDE.md rule 15: **Demo Bank is the
only door into finance.** A direct mirror would manufacture financial facts in
Postgres that never passed intake, which is precisely the failure already
recorded twice in this codebase: East Gate's two disconnected 2021-2025 expense
totals ($415,031.21 staged vs $1,502,451.24 GL-posted) diverged 3.6x because two
pipelines wrote the same facts without either checking the other.

So this module **diffs**, and emits what is missing as Demo Bank candidates.
Those candidates then travel the same intake -> matching -> approval -> GL route
as every other financial input. The sync closes the gap *through* the door, not
around it. Nothing here posts a journal entry, and nothing here writes
``finance.*``.

What "all writes are synced" means in practice
----------------------------------------------
Two halves, and the second is the one that was missing:

* **Closing** the gap — ``build_sync_plan`` finds Mongo payments with no
  Postgres counterpart and turns them into intake candidates.
* **Proving** it stays closed — ``measure_drift`` reports the per-lot position
  from both stores so divergence is a number someone can watch, not something
  discovered a month later against a portal scrape. Drift that is not measured
  is drift that is silent, which is how this gap reached $26,042.77.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# A Mongo payment is matched to a Postgres receipt on this triple. `id` is not
# usable: Mongo and Postgres rows for the same fact carry different identifiers
# (footgun #24), and reconstructed history was posted to the two stores by
# different scripts, so no shared surrogate key exists. Unit + cents + date is
# the strongest identity actually available on both sides.
MatchKey = tuple[str, int, str]


@dataclass
class SyncPlan:
    """What is present in Mongo, absent from Postgres, and worth emitting."""

    missing_in_pg: list[dict[str, Any]] = field(default_factory=list)
    already_present: int = 0
    skipped_unconfirmed: int = 0
    skipped_no_unit: int = 0

    @property
    def total_cents(self) -> int:
        return sum(int(p["amount_cents"]) for p in self.missing_in_pg)


def _to_cents(amount: Any) -> int:
    """Dollars -> integer cents at the boundary, exactly once.

    ``levy_payments.amount`` is a dollar FLOAT (a documented, still-current
    violation of the cents-only rule — CLAUDE.md rule 9). Converting here, at the
    adapter edge, is the established pattern; never re-derive it downstream and
    never let a float reach ``backend/domain``.
    """
    return int(round(float(amount or 0) * 100))


def _key(unit_number: str, amount_cents: int, on_date: Any) -> MatchKey:
    return (str(unit_number).strip().upper(), int(amount_cents), str(on_date)[:10])


def build_sync_plan(
    mongo_payments: list[dict[str, Any]],
    pg_receipts: list[dict[str, Any]],
) -> SyncPlan:
    """Diff Mongo payments against Postgres receipts on ``MatchKey``.

    Pure — no I/O — so the matching rule is testable without either database.
    The caller supplies both sides already scoped to one building.

    Counting is multiset, not set: two genuine payments of the same amount on the
    same day for the same unit are two facts, and PG holding one of them means
    one is still missing. Treating the key as a set would silently under-report
    exactly the duplicate-shaped drift this is meant to find.
    """
    from collections import Counter

    pg_counts: Counter[MatchKey] = Counter(
        _key(r["unit_number"], r["amount_cents"], r["received_on"])
        for r in pg_receipts
        if r.get("unit_number")
    )

    plan = SyncPlan()
    for payment in mongo_payments:
        # Only confirmed money. A pending or rejected Mongo row is not a
        # financial fact yet, and emitting it as intake would create one.
        if payment.get("status") not in (None, "confirmed"):
            plan.skipped_unconfirmed += 1
            continue

        unit = payment.get("unit_number")
        if not unit:
            plan.skipped_no_unit += 1
            continue

        cents = _to_cents(payment.get("amount"))
        key = _key(unit, cents, payment.get("payment_date"))

        if pg_counts.get(key, 0) > 0:
            pg_counts[key] -= 1
            plan.already_present += 1
            continue

        plan.missing_in_pg.append(
            {
                "unit_number": str(unit).strip().upper(),
                "amount_cents": cents,
                "payment_date": str(payment.get("payment_date"))[:10],
                "reference": payment.get("payment_reference") or payment.get("receipt_number"),
                "transaction_origin": payment.get("transaction_origin"),
                "mongo_payment_id": payment.get("id"),
            }
        )
    return plan


def to_demo_bank_candidates(
    plan: SyncPlan,
    building_id: str,
) -> list[dict[str, Any]]:
    """Shape the plan as Demo Bank intake rows.

    ``requires_review=True`` and ``sync_status="pending"`` deliberately: these are
    candidates for a human to approve on the matching page, never auto-posted
    money. ``source_type`` names this path so the rows are distinguishable from
    a real feed forever after — the codebase already carries four disjoint
    ``transaction_origin`` vocabularies and adding an ambiguous fifth would make
    provenance unanswerable.

    ``idempotency_key`` is derived from the match key, so re-running the sync
    cannot create a second candidate for the same fact even if the first is still
    awaiting review.
    """
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for item in plan.missing_in_pg:
        rows.append(
            {
                "building_id": building_id,
                "unit_number": item["unit_number"],
                "amount_cents": abs(int(item["amount_cents"])),
                # Demo Bank's signed-amount contract applies at the PROVIDER
                # boundary, not here: ingestion stores the absolute value and
                # `direction` carries the sign.
                "direction": "credit",
                "effective_date": item["payment_date"],
                "posted_date": item["payment_date"],
                "description": f"Mongo->PG sync: levy payment {item['unit_number']}",
                "reference": item["reference"],
                "source_type": "mongo_pg_backfill",
                "source_id": item["mongo_payment_id"],
                "provider": "mongo_pg_finance_sync",
                "status": "pending",
                "sync_status": "pending",
                "requires_review": True,
                "idempotency_key": (
                    f"mongo-pg-sync:{building_id}:{item['unit_number']}:"
                    f"{item['amount_cents']}:{item['payment_date']}"
                ),
                "created_at": now,
                "updated_at": now,
            }
        )
    return rows


@dataclass
class DriftReport:
    """Per-lot position from both stores, so divergence is a watchable number."""

    building_id: str
    lots_compared: int
    lots_diverged: int
    net_gap_cents: int
    per_lot: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return self.lots_diverged == 0


def measure_drift(
    building_id: str,
    mongo_by_unit: dict[str, int],
    pg_by_unit: dict[str, int],
) -> DriftReport:
    """Compare per-lot net positions. Pure; both sides already in cents.

    A unit missing from one side counts as diverged when the other side holds a
    non-zero position — missing and zero are different states, and collapsing
    them is the specific error that has already produced wrong finance figures
    here more than once.
    """
    units = set(mongo_by_unit) | set(pg_by_unit)
    per_lot: dict[str, tuple[int, int]] = {}
    diverged = 0
    net = 0
    for unit in sorted(units):
        m = int(mongo_by_unit.get(unit, 0))
        p = int(pg_by_unit.get(unit, 0))
        per_lot[unit] = (m, p)
        if m != p:
            diverged += 1
            net += m - p
    return DriftReport(
        building_id=building_id,
        lots_compared=len(units),
        lots_diverged=diverged,
        net_gap_cents=net,
        per_lot=per_lot,
    )


# ---------------------------------------------------------------------------
# In-app execution — the half that was missing
# ---------------------------------------------------------------------------
#
# Everything above is pure and was fully tested from the day it was written. It
# was also never once executed by the running application: the only caller was
# `scripts/data_repair/sync_mongo_payments_to_postgres.py`, a manual dry-run-by-
# default CLI. Nothing scheduled it, no worker invoked it, no route reached it —
# verified 2026-08-29 by grepping workers/, cron/, routers/ and server.py for
# either module name, which returns nothing.
#
# So the DR position it exists to protect was never actually measured. That is the
# gap this section closes: `run_finance_dr_sync` is callable from the scheduler
# (workers/scheduler.py::finance_dr_drift_check) and from the CLI, with the same
# matching rule and the same intake contract for both.
#
# It stays SAFE BY DEFAULT — `apply=False` measures and reports only. Emitting
# candidates is opt-in, and even then they land as `requires_review=True` Demo Bank
# rows that a human must approve. Nothing here posts a journal entry or writes
# `finance.*`; CLAUDE.md rule 15 holds — Demo Bank is the only door.


async def collect_sync_inputs(building_id: str) -> dict[str, Any] | None:
    """Read both stores for one building. Returns None when the scheme is unknown.

    Tenant context is set explicitly before touching `finance.*`: those tables have
    no RLS bypass clause, so without it every query returns zero rows and no error —
    which is indistinguishable from "Postgres is empty" and is precisely how a DR
    check could report a perfectly clean sync while reading nothing at all
    (footgun #8).
    """
    from sqlalchemy import text

    from database import db
    from db_postgres.repos.config_repo import resolve_scheme_context
    from db_postgres.session import async_session_context, set_tenant

    scheme = await resolve_scheme_context(building_id)
    if not scheme or not scheme.get("tenant_id"):
        logger.warning("finance DR sync: no scheme for building_id=%s", building_id)
        return None
    tenant_id = str(scheme["tenant_id"])

    mongo_payments = await db.levy_payments.find(
        {"building_id": building_id}, {"_id": 0}
    ).to_list(20000)

    mongo_by_unit: dict[str, int] = {}
    async for row in db.unit_levy_ledger.find(
        {"building_id": building_id}, {"_id": 0, "unit_number": 1, "net_balance": 1}
    ):
        unit = row.get("unit_number")
        if unit:
            mongo_by_unit[unit] = mongo_by_unit.get(unit, 0) + _to_cents(row.get("net_balance"))

    async with async_session_context() as session:
        await set_tenant(session, tenant_id)

        receipts_result = await session.execute(
            text(
                """
                SELECT l.unit_number, r.amount_cents, r.received_on
                  FROM finance.receipts r
                  JOIN core.lots l ON l.lot_id = r.lot_id
                 WHERE r.tenant_id = CAST(:tenant_id AS UUID)
                   AND r.retired_at IS NULL
                """
            ),
            {"tenant_id": tenant_id},
        )
        pg_receipts = [
            {"unit_number": r.unit_number, "amount_cents": r.amount_cents, "received_on": r.received_on}
            for r in receipts_result.fetchall()
        ]

        net_result = await session.execute(
            text(
                """
                SELECT l.unit_number,
                       COALESCE(SUM(li.principal_cents + li.gst_cents
                                  + li.interest_cents + li.recovery_costs_cents), 0)
                     - COALESCE(SUM(li.paid_cents), 0)
                     - COALESCE((SELECT SUM(o.available_cents)
                                   FROM finance.owner_credit_balances o
                                  WHERE o.lot_id = l.lot_id
                                    AND o.tenant_id = CAST(:tenant_id AS UUID)), 0) AS net
                  FROM core.lots l
                  LEFT JOIN finance.levy_items li
                         ON li.lot_id = l.lot_id
                        AND li.tenant_id = CAST(:tenant_id AS UUID)
                 WHERE l.tenant_id = CAST(:tenant_id AS UUID)
                 GROUP BY l.unit_number, l.lot_id
                """
            ),
            {"tenant_id": tenant_id},
        )
        pg_by_unit = {r.unit_number: int(r.net or 0) for r in net_result.fetchall()}

    return {
        "tenant_id": tenant_id,
        "mongo_payments": mongo_payments,
        "pg_receipts": pg_receipts,
        "mongo_by_unit": mongo_by_unit,
        "pg_by_unit": pg_by_unit,
    }


async def run_finance_dr_sync(building_id: str, *, apply: bool = False) -> dict[str, Any]:
    """Measure Mongo<->Postgres finance drift for one building; optionally emit intake.

    Returns a summary dict suitable for logging, a scheduler result, or an admin
    endpoint. ``apply=False`` (the default) writes nothing anywhere.
    """
    from database import db

    inputs = await collect_sync_inputs(building_id)
    if inputs is None:
        return {"building_id": building_id, "status": "no_scheme"}

    plan = build_sync_plan(inputs["mongo_payments"], inputs["pg_receipts"])
    drift = measure_drift(building_id, inputs["mongo_by_unit"], inputs["pg_by_unit"])

    summary: dict[str, Any] = {
        "building_id": building_id,
        "status": "clean" if (drift.is_clean and not plan.missing_in_pg) else "drift",
        "mongo_payments": len(inputs["mongo_payments"]),
        "pg_receipts": len(inputs["pg_receipts"]),
        "already_present": plan.already_present,
        "missing_in_pg": len(plan.missing_in_pg),
        "missing_cents": plan.total_cents,
        "skipped_unconfirmed": plan.skipped_unconfirmed,
        "skipped_no_unit": plan.skipped_no_unit,
        "lots_compared": drift.lots_compared,
        "lots_diverged": drift.lots_diverged,
        "net_gap_cents": drift.net_gap_cents,
        "candidates_created": 0,
        "applied": apply,
    }

    if apply and plan.missing_in_pg:
        created = 0
        for cand in to_demo_bank_candidates(plan, building_id):
            res = await db.demo_bank_transactions.update_one(
                {"idempotency_key": cand["idempotency_key"]},
                {"$setOnInsert": cand},
                upsert=True,
            )
            if res.upserted_id is not None:
                created += 1
        summary["candidates_created"] = created

    logger.info(
        "finance DR sync building=%s status=%s lots_diverged=%d/%d net_gap_cents=%d "
        "missing_in_pg=%d candidates_created=%d applied=%s",
        building_id, summary["status"], drift.lots_diverged, drift.lots_compared,
        drift.net_gap_cents, len(plan.missing_in_pg), summary["candidates_created"], apply,
    )
    return summary
