# @featuretrace:financial_integration_v2 — Shared manifest/approve/apply
#   orchestration for historical-reconstruction backfill scripts.
# Layer: service
# Data flow: finance.reconstruction_execution_batches/_items (building-scoped).
# Related: backend/scripts/east_gate_2025_expense_reconstruction.py
#          backend/scripts/east_gate_lot_fee_charge_backfill.py
#          backend/alembic/versions/0073_accounting_period_integrity.py
#          backend/alembic/versions/0074_harden_recon_batches.py
#          backend/db_postgres/repos/identity_repo.py
"""Shared batch/manifest orchestration for the two East Gate 2025
historical-reconstruction scripts (expense + lot-fee).

A reconstruction batch is an immutable header plus immutable batch-item
snapshots (enforced at the DB level by migration 0074's triggers). This
module never re-derives eligibility from a fresh query at --apply time — it
always reads the frozen `reconstruction_execution_batch_items` rows written
at --create-manifest time.

Batch-item completion is the CALLING SCRIPT's responsibility, not this
module's — a lot-fee item needs two journals (cash-expense + recharge) and
must only be marked `applied` after BOTH succeed, in the same transaction as
both journal posts. `mark_item_applied()` here is a thin, single-purpose
write; the orchestration (deciding when it's safe to call it) belongs to the
caller.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import text

from db_postgres.session import set_tenant

_RESUMABLE_STATUSES = {"approved", "applying", "partially_applied", "failed"}

# Every function below that touches a tenant-scoped table calls set_tenant()
# defensively at its own start, rather than trusting the caller to have done
# so already in the same session. This is not redundant: set_tenant() uses
# `SET LOCAL`, which is scoped to the current transaction — any session.commit()
# (several functions here commit internally, as their own atomic unit) starts
# a NEW implicit transaction with NO tenant context. Under FORCE ROW LEVEL
# SECURITY this doesn't error, it just makes every subsequent query on this
# session match zero rows — e.g. begin_apply() committing internally, then a
# caller-held reference to the same session calling load_pending_items()
# right after, would silently see 0 pending items and report a clean
# posted=0 failed=0 "success" while doing nothing. Found via live re-audit
# 2026-07-24, not caught by any mocked test (mocks don't model RLS at all).


class BatchOrchestrationError(RuntimeError):
    """Raised for any batch-lifecycle precondition failure (not found, wrong
    status, dual-control violation, identity validation failure)."""


def compute_manifest_hash(items: list[dict]) -> str:
    """Canonical hash over the ordered item snapshot fields — this is what
    --apply re-checks the live manifest rows against, not just a count/total."""
    canonical = [
        {
            "sequence_number": it["sequence_number"],
            "bank_transaction_id": str(it["bank_transaction_id"]),
            "transaction_date": it["transaction_date"].isoformat(),
            "description": it["description"],
            "amount_cents": it["amount_cents"],
            "item_type": it["item_type"],
        }
        for it in items
    ]
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def compute_item_snapshot_hash(transaction_date: date, description: str, amount_cents: int) -> str:
    blob = json.dumps(
        {"transaction_date": transaction_date.isoformat(), "description": description, "amount_cents": amount_cents},
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def sanitize_error(exc: Exception) -> str:
    """Never store raw exception text verbatim — it can carry connection
    strings or SQL parameter values. Type name + truncated, redacted message."""
    msg = f"{type(exc).__name__}: {exc}"
    msg = re.sub(r"(postgresql|postgres)://\S+", "[REDACTED_DSN]", msg, flags=re.I)
    msg = re.sub(r"password\s*=\s*\S+", "password=[REDACTED]", msg, flags=re.I)
    return msg[:500]


async def create_manifest_batch(
        session, *, tenant_id: str, scheme_id: str, batch_type: str, target_period_label: str,
        description: str, created_by: str, items: list[dict],
) -> str:
    """Insert an immutable batch header + its item snapshots, status
    'manifest_pending'. Caller must have already run population-gate
    assertions (expected count/total, disjointness, sign, classification) —
    this function only persists what it's given."""
    await set_tenant(session, tenant_id)
    batch_id = str(uuid.uuid4())
    # Assign sequence_number BEFORE hashing — compute_manifest_hash() reads
    # it from each item dict, and the caller-supplied items never carry one
    # (an earlier version of this function called compute_manifest_hash()
    # on the raw input first and only assigned sequence_number afterwards in
    # the INSERT loop below, which raised KeyError on every real call —
    # caught only by a live re-audit, since no prior test exercised this
    # function against unmodified caller-shaped item dicts).
    sequenced_items = [{**it, "sequence_number": seq} for seq, it in enumerate(items, start=1)]
    manifest_hash = compute_manifest_hash(sequenced_items)
    expected_row_count = len(sequenced_items)
    expected_amount_cents = sum(abs(it["amount_cents"]) for it in sequenced_items)

    await session.execute(
        text(
            """
            INSERT INTO finance.reconstruction_execution_batches
                (batch_id, tenant_id, scheme_id, batch_type, target_period_label, description,
                 manifest_hash, expected_row_count, expected_amount_cents, created_by)
            VALUES (:bid, :tid, :sid, :btype, :tpl, :desc, :hash, :erc, :eac, :cby)
            """
        ),
        {
            "bid": batch_id, "tid": tenant_id, "sid": scheme_id, "btype": batch_type,
            "tpl": target_period_label, "desc": description, "hash": manifest_hash,
            "erc": expected_row_count, "eac": expected_amount_cents, "cby": created_by,
        },
    )
    for it in sequenced_items:
        await session.execute(
            text(
                """
                INSERT INTO finance.reconstruction_execution_batch_items
                    (batch_item_id, batch_id, tenant_id, scheme_id, sequence_number, bank_transaction_id,
                     transaction_date, description, amount_cents, category_name, lot_number, item_type,
                     source_snapshot_hash)
                VALUES (:iid, :bid, :tid, :sid, :seq, :btid, :tdate, :desc, :amt, :cat, :lot, :itype, :hash)
                """
            ),
            {
                "iid": str(uuid.uuid4()), "bid": batch_id, "tid": tenant_id, "sid": scheme_id,
                "seq": it["sequence_number"],
                "btid": it["bank_transaction_id"], "tdate": it["transaction_date"], "desc": it["description"],
                "amt": it["amount_cents"], "cat": it.get("category_name"), "lot": it.get("lot_number"),
                "itype": it["item_type"],
                "hash": compute_item_snapshot_hash(it["transaction_date"], it["description"], it["amount_cents"]),
            },
        )
    await session.commit()
    return batch_id


async def approve_batch(session, *, tenant_id: str, batch_id: str, approved_by: str, approval_reference: str) -> None:
    """Approval-time check: approved_by must be a real user of this tenant
    and differ from created_by. executed_by is NOT checked here — it isn't
    known until --apply."""
    from db_postgres.repos.identity_repo import get_user_by_id

    await set_tenant(session, tenant_id)
    row = (
        await session.execute(
            text("SELECT created_by::text, status FROM finance.reconstruction_execution_batches WHERE batch_id = :bid"),
            {"bid": batch_id},
        )
    ).fetchone()
    if row is None:
        raise BatchOrchestrationError(f"batch {batch_id} not found")
    created_by, status = row
    if status != "manifest_pending":
        raise BatchOrchestrationError(f"batch {batch_id} has status={status!r}, expected 'manifest_pending'")
    if created_by == approved_by:
        raise BatchOrchestrationError("approved_by must differ from created_by (dual control)")

    user = await get_user_by_id(approved_by, tenant_id)
    if user is None:
        raise BatchOrchestrationError(f"approved_by={approved_by} is not a valid user of this tenant")

    await session.execute(
        text(
            """
            UPDATE finance.reconstruction_execution_batches
            SET status = 'approved', approved_by = :aby, approved_at = now(), approval_reference = :aref
            WHERE batch_id = :bid AND status = 'manifest_pending'
            """
        ),
        {"aby": approved_by, "aref": approval_reference, "bid": batch_id},
    )
    await session.commit()


async def validate_executor(tenant_id: str, approved_by: str, executed_by: str) -> None:
    """Apply-time re-check: executed_by must be a real tenant user and
    differ from approved_by. HistoricalPostingAuthorisation.__post_init__
    already enforces the "differ" part structurally — this adds the
    "resolves to a real tenant user" check the dataclass can't do on its own."""
    from db_postgres.repos.identity_repo import get_user_by_id

    if approved_by == executed_by:
        raise BatchOrchestrationError("executed_by must differ from approved_by (dual control)")
    user = await get_user_by_id(executed_by, tenant_id)
    if user is None:
        raise BatchOrchestrationError(f"executed_by={executed_by} is not a valid user of this tenant")


async def begin_apply(session, tenant_id: str, batch_id: str) -> dict:
    """Load the batch header, require a resumable status, mark it 'applying'.
    Returns the batch row as a dict (includes approved_by/manifest_hash for
    the caller to build HistoricalPostingAuthorisation from)."""
    await set_tenant(session, tenant_id)
    row = (
        await session.execute(
            text(
                """
                SELECT batch_id::text, tenant_id::text, scheme_id::text, batch_type, target_period_label,
                       description, status, manifest_hash, approved_by::text, approval_reference,
                       expected_row_count
                FROM finance.reconstruction_execution_batches WHERE batch_id = :bid
                """
            ),
            {"bid": batch_id},
        )
    ).fetchone()
    if row is None:
        raise BatchOrchestrationError(f"batch {batch_id} not found")
    batch = dict(row._mapping)
    if batch["status"] not in _RESUMABLE_STATUSES:
        raise BatchOrchestrationError(
            f"batch {batch_id} has status={batch['status']!r}, not resumable "
            f"(expected one of {sorted(_RESUMABLE_STATUSES)})"
        )
    await session.execute(
        text("UPDATE finance.reconstruction_execution_batches SET status = 'applying' WHERE batch_id = :bid"),
        {"bid": batch_id},
    )
    await session.commit()
    return batch


async def load_pending_items(session, tenant_id: str, batch_id: str) -> list[dict]:
    """Only 'pending' or 'failed' items — 'applied' items are validated and
    skipped by the caller, never reprocessed. Reads the frozen manifest rows
    only, never a fresh eligibility query."""
    await set_tenant(session, tenant_id)
    rows = (
        await session.execute(
            text(
                """
                SELECT batch_item_id::text, sequence_number, bank_transaction_id::text, transaction_date,
                       description, amount_cents, category_name, lot_number, item_type, source_snapshot_hash
                FROM finance.reconstruction_execution_batch_items
                WHERE batch_id = :bid AND status IN ('pending', 'failed')
                ORDER BY sequence_number
                """
            ),
            {"bid": batch_id},
        )
    ).fetchall()
    return [dict(r._mapping) for r in rows]


async def assert_pending_load_not_silently_empty(
        session, tenant_id: str, batch_id: str, expected_row_count: int, loaded_pending_count: int,
) -> None:
    """Defense-in-depth guard against the exact silent-zero-items failure
    mode found in the 2026-07-24 deep-dive audit: begin_apply()'s internal
    commit dropped SET LOCAL app.tenant_id, so load_pending_items() (called
    right after, in the same session) silently matched zero rows under
    FORCE ROW LEVEL SECURITY — no exception, just an empty list — and
    --apply would report posted=0 failed=0 and exit 0, a clean-looking
    no-op, while the batch stayed stuck at 'applying' forever.

    That root cause is fixed (every function here now re-establishes tenant
    context defensively). This is a SEPARATE, independent check on top of
    the fix, not a substitute for it: if load_pending_items() ever returns
    empty again, re-verify — via a fresh query, after an explicit
    set_tenant() call of its own — that every one of the batch's expected
    items has actually reached a terminal state. If not, raise loudly rather
    than let the caller treat an empty list as "nothing to do"."""
    if loaded_pending_count > 0:
        return
    await set_tenant(session, tenant_id)
    terminal_count = (
        await session.execute(
            text(
                "SELECT count(*) FROM finance.reconstruction_execution_batch_items "
                "WHERE batch_id = :bid AND status IN ('applied', 'skipped')"
            ),
            {"bid": batch_id},
        )
    ).scalar()
    if terminal_count < expected_row_count:
        raise BatchOrchestrationError(
            f"load_pending_items() returned 0 items for batch {batch_id}, but only "
            f"{terminal_count}/{expected_row_count} of its items have reached a terminal "
            f"('applied'/'skipped') state. Refusing to treat this as a completed batch — "
            f"this is the exact silent-zero-items failure mode found in GAP-FIN-018's "
            f"2026-07-24 deep-dive audit. Do not report success without investigating."
        )


async def check_item_drift(session, tenant_id: str, item: dict) -> Optional[str]:
    """Re-fetch the live finance.bank_transactions row for this item and
    compare against its frozen source_snapshot_hash. Returns a description
    of the drift if any, else None. Does not raise — the caller decides how
    to record a drifted item (as a failure, not a silent skip)."""
    await set_tenant(session, tenant_id)
    row = (
        await session.execute(
            text(
                "SELECT transaction_date, description, amount_cents FROM finance.bank_transactions "
                "WHERE bank_transaction_id = :btid"
            ),
            {"btid": item["bank_transaction_id"]},
        )
    ).fetchone()
    if row is None:
        return f"source bank_transaction_id={item['bank_transaction_id']} no longer exists"
    live_hash = compute_item_snapshot_hash(row.transaction_date, row.description, row.amount_cents)
    if live_hash != item["source_snapshot_hash"]:
        return (
            f"source bank_transaction_id={item['bank_transaction_id']} has drifted since manifest creation "
            f"(live: date={row.transaction_date} amount_cents={row.amount_cents})"
        )
    return None


async def mark_item_applied(
        session, tenant_id: str, batch_item_id: str, *,
        expense_journal_entry_id: Optional[str], recharge_journal_entry_id: Optional[str],
) -> None:
    """Part of the SAME transaction as the journal post(s) it records —
    caller commits everything together."""
    await set_tenant(session, tenant_id)
    await session.execute(
        text(
            """
            UPDATE finance.reconstruction_execution_batch_items
            SET status = 'applied', expense_journal_entry_id = :eje, recharge_journal_entry_id = :rje,
                applied_at = now()
            WHERE batch_item_id = :iid
            """
        ),
        {"iid": batch_item_id, "eje": expense_journal_entry_id, "rje": recharge_journal_entry_id},
    )


async def mark_item_failed(session, tenant_id: str, batch_item_id: str, error: Exception) -> None:
    """Phase-2 write — caller must open a FRESH session/transaction for this
    (the phase-1 posting transaction that failed has already been rolled
    back and cannot be reused)."""
    await set_tenant(session, tenant_id)
    await session.execute(
        text(
            "UPDATE finance.reconstruction_execution_batch_items "
            "SET status = 'failed', error_message = :err WHERE batch_item_id = :iid"
        ),
        {"iid": batch_item_id, "err": sanitize_error(error)},
    )
    await session.commit()


async def finalize_batch_status(session, tenant_id: str, batch_id: str, *, applied_by: str) -> str:
    """applied = every item applied; partially_applied = >=1 applied and
    >=1 not (pending/failed); failed = 0 applied and >=1 failed."""
    await set_tenant(session, tenant_id)
    rows = (
        await session.execute(
            text("SELECT status, amount_cents FROM finance.reconstruction_execution_batch_items WHERE batch_id = :bid"),
            {"bid": batch_id},
        )
    ).fetchall()
    total = len(rows)
    applied_rows = [r for r in rows if r.status == "applied"]
    failed_rows = [r for r in rows if r.status == "failed"]
    applied_count = len(applied_rows)
    failed_count = len(failed_rows)
    actual_amount_cents = sum(abs(r.amount_cents) for r in applied_rows)

    if total and applied_count == total:
        final_status = "applied"
    elif applied_count > 0:
        final_status = "partially_applied"
    elif failed_count > 0:
        final_status = "failed"
    else:
        final_status = "applying"  # nothing processed — shouldn't normally happen at finalize time

    await session.execute(
        text(
            """
            UPDATE finance.reconstruction_execution_batches
            SET status = :st, applied_by = :aby, applied_at = now(),
                actual_row_count = :arc, actual_amount_cents = :aac, failure_count = :fc
            WHERE batch_id = :bid
            """
        ),
        {"st": final_status, "aby": applied_by, "arc": applied_count, "aac": actual_amount_cents,
         "fc": failed_count, "bid": batch_id},
    )
    await session.commit()
    return final_status
