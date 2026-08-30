"""
backend/domain/recurring_bills.py — Recurring bill template engine.

# @featuretrace:ap_automation — nightly instantiation of scheduled supplier invoices.
# Layer: domain
# Data flow: recurring_bill_templates → instantiate_recurring_bills() →
#            invoice_documents (draft state).
# Related: backend/routers/ap_approval.py
#           backend/domain/invoice_lifecycle.py
# Scope: (building-scoped)

Template types:
  fixed_monthly         — management fee, cleaning, CCTV (predictable amount)
  variable_cadence      — utilities (predictable timing, variable amount;
                          forecast = 3-period trailing median)
  annual_with_instalments — insurance premium funding (split into N instalments)
  one_off               — repairs, insurance claims (single future invoice)

Idempotency: each instantiated invoice carries a deterministic
  idempotency_key = sha256(template_id + period_label) so re-running the
  nightly job never creates duplicates.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _period_label(template_id: str, reference_date: date) -> str:
    """Deterministic label for a (template, period) pair."""
    return f"{template_id}:{reference_date.isoformat()}"


def _idempotency_key(template_id: str, reference_date: date) -> str:
    """Generated function header.

    Function: _idempotency_key
    Path: backend/domain/recurring_bills.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    label = _period_label(template_id, reference_date)
    return hashlib.sha256(label.encode()).hexdigest()


def _trailing_median(amounts: list[int]) -> int:
    """3-period trailing median. Returns last amount if fewer than 3 periods."""
    if not amounts:
        return 0
    window = sorted(amounts[-3:])
    mid = len(window) // 2
    return window[mid]


def _next_monthly_date(reference: date) -> date:
    """First day of the month following reference."""
    if reference.month == 12:
        return date(reference.year + 1, 1, 1)
    return date(reference.year, reference.month + 1, 1)


# ── Core instantiation ────────────────────────────────────────────────────────

async def instantiate_recurring_bills(
        building_id: str,
        *,
        db,
        as_of: Optional[date] = None,
        lookahead_days: int = 7,
) -> list[str]:
    """
    Instantiate draft invoices from active recurring bill templates.

    Called nightly. Returns list of inserted invoice _ids (as strings).
    Already-instantiated periods are skipped via idempotency_key check.
    """
    today = as_of or date.today()
    horizon = today + timedelta(days=lookahead_days)

    templates = await db.recurring_bill_templates.find(
        {"is_active": True}
    ).to_list(500)

    inserted_ids: list[str] = []

    for tmpl in templates:
        tmpl_type = tmpl.get("template_type", "fixed_monthly")
        tmpl_id = str(tmpl["_id"])

        try:
            new_ids = await _instantiate_template(
                tmpl, tmpl_type, tmpl_id, building_id,
                today, horizon, db=db,
            )
            inserted_ids.extend(new_ids)
        except Exception as exc:
            logger.error("Failed to instantiate template %s: %s", tmpl_id, exc)

    return inserted_ids


async def _instantiate_template(
        tmpl: dict,
        tmpl_type: str,
        tmpl_id: str,
        building_id: str,
        today: date,
        horizon: date,
        *,
        db,
) -> list[str]:
    """Dispatch to the correct template handler."""
    if tmpl_type == "fixed_monthly":
        return await _fixed_monthly(tmpl, tmpl_id, building_id, today, horizon, db=db)
    if tmpl_type == "variable_cadence":
        return await _variable_cadence(tmpl, tmpl_id, building_id, today, horizon, db=db)
    if tmpl_type == "annual_with_instalments":
        return await _annual_with_instalments(tmpl, tmpl_id, building_id, today, horizon, db=db)
    if tmpl_type == "one_off":
        return await _one_off(tmpl, tmpl_id, building_id, today, db=db)
    logger.warning("Unknown template_type %r for template %s", tmpl_type, tmpl_id)
    return []


async def _fixed_monthly(
        tmpl: dict, tmpl_id: str, building_id: str,
        today: date, horizon: date, *, db,
) -> list[str]:
    """Instantiate the next monthly invoice if its due date falls within the horizon."""
    # due_day: day of month the invoice is due (e.g., 1 = 1st of month)
    due_day = tmpl.get("due_day", 1)
    target_month_first = _next_monthly_date(today)
    try:
        due_date = target_month_first.replace(day=min(due_day, 28))
    except ValueError:
        due_date = target_month_first

    if due_date > horizon:
        return []

    ikey = _idempotency_key(tmpl_id, due_date)
    return await _insert_draft_if_new(tmpl, tmpl_id, building_id, due_date,
                                      tmpl.get("amount_cents", 0), ikey, db=db)


async def _variable_cadence(
        tmpl: dict, tmpl_id: str, building_id: str,
        today: date, horizon: date, *, db,
) -> list[str]:
    """Instantiate next invoice using 3-period trailing median as forecast amount."""
    due_day = tmpl.get("due_day", 1)
    target_month_first = _next_monthly_date(today)
    try:
        due_date = target_month_first.replace(day=min(due_day, 28))
    except ValueError:
        due_date = target_month_first

    if due_date > horizon:
        return []

    # Compute trailing median from last 3 paid invoices for this template
    history = await db.invoice_documents.find(
        {"template_id": tmpl_id, "state": "reconciled"},
        sort=[("issue_date", -1)],
    ).to_list(3)
    past_amounts = [h.get("total_cents", 0) for h in history]
    forecast = _trailing_median(past_amounts) if past_amounts else tmpl.get("amount_cents", 0)

    ikey = _idempotency_key(tmpl_id, due_date)
    return await _insert_draft_if_new(tmpl, tmpl_id, building_id, due_date,
                                      forecast, ikey, db=db, is_forecast=True)


async def _annual_with_instalments(
        tmpl: dict, tmpl_id: str, building_id: str,
        today: date, horizon: date, *, db,
) -> list[str]:
    """Generate instalment invoices for annual bills (e.g. insurance)."""
    num_instalments = tmpl.get("num_instalments", 12)
    annual_cents = tmpl.get("amount_cents", 0)
    instalment_cents = annual_cents // num_instalments
    start_date_str = tmpl.get("start_date", today.isoformat())

    try:
        start = date.fromisoformat(start_date_str)
    except ValueError:
        start = today

    inserted: list[str] = []
    for i in range(num_instalments):
        # Monthly instalments from start_date
        month_offset = i % 12
        year_offset = i // 12
        try:
            due = date(start.year + year_offset,
                       ((start.month - 1 + month_offset) % 12) + 1,
                       min(start.day, 28))
        except ValueError:
            continue

        if due < today or due > horizon:
            continue

        ikey = _idempotency_key(f"{tmpl_id}:inst{i}", due)
        ids = await _insert_draft_if_new(
            tmpl, tmpl_id, building_id, due, instalment_cents,
            ikey, db=db,
            description=f"{tmpl.get('description', 'Invoice')} — instalment {i + 1}/{num_instalments}",
        )
        inserted.extend(ids)

    return inserted


async def _one_off(
        tmpl: dict, tmpl_id: str, building_id: str,
        today: date, *, db,
) -> list[str]:
    """Instantiate a single one-off invoice if its due date is today or in the past."""
    due_date_str = tmpl.get("due_date")
    if not due_date_str:
        return []
    try:
        due = date.fromisoformat(due_date_str)
    except ValueError:
        return []

    if due > today:
        return []  # not yet due

    ikey = _idempotency_key(tmpl_id, due)
    ids = await _insert_draft_if_new(tmpl, tmpl_id, building_id, due,
                                     tmpl.get("amount_cents", 0), ikey, db=db)

    # Deactivate one-off template after instantiation
    if ids:
        await db.recurring_bill_templates.update_one(
            {"_id": tmpl["_id"]},
            {"$set": {"is_active": False}},
        )

    return ids


async def _insert_draft_if_new(
        tmpl: dict,
        tmpl_id: str,
        building_id: str,
        due_date: date,
        amount_cents: int,
        idempotency_key: str,
        *,
        db,
        description: Optional[str] = None,
        is_forecast: bool = False,
) -> list[str]:
    """Insert a draft invoice if the idempotency_key doesn't already exist."""
    existing = await db.invoice_documents.find_one(
        {"tenant_id": building_id, "idempotency_key": idempotency_key}
    )
    if existing:
        return []  # already instantiated

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "tenant_id": building_id,
        "building_id": building_id,
        "template_id": tmpl_id,
        "idempotency_key": idempotency_key,
        "state": "draft",
        "vendor_name": tmpl.get("vendor_name"),
        "vendor_abn": tmpl.get("vendor_abn"),
        "invoice_number": None,  # to be filled by actual invoice on arrival
        "issue_date": None,
        "due_date": due_date.isoformat(),
        "total_cents": amount_cents,
        "is_forecast_amount": is_forecast,
        "description": description or tmpl.get("description"),
        "category": tmpl.get("category"),
        "transitions": [],
        "content_sha256": None,
        "abn_valid": None,
        "created_at": now,
        "updated_at": now,
        "is_test_data": tmpl.get("is_test_data", False),
    }
    result = await db.invoice_documents.insert_one(doc)
    logger.info(
        "Instantiated recurring invoice from template %s: due=%s amount=%d building=%s",
        tmpl_id, due_date, amount_cents, building_id,
    )
    return [str(result.inserted_id)]
