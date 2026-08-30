"""
cron_trust_interest.py — Monthly Trust Account Interest Accrual

# @featuretrace:trust-interest-accrual — Monthly interest posting to trust_accounts_v2, pro-rata split by fund balance. Mongo-only, no Postgres path, independent of the V1 journal (trust_ledger_entries).
# Layer: cron
# Data flow: systemd timer (0 6 1 * *) → run_interest_accrual() → trust_accounts_v2 → trust_transactions_v2 + trust_interest_postings (building-scoped).
# Related: backend/routers/trust_phase1.py (trust_accounts_v2 / trust_transactions_v2 owner)
#           backend/routers/trust_accounting.py (separate V1 system, not touched by this cron)
# Collection: trust_accounts_v2, trust_transactions_v2, trust_interest_postings, trust_levy_schedules_v2

Runs on the 1st of each month (or can be triggered manually).
For each building:
  1. Find all active trust accounts with interest_rate_pa > 0.
  2. For each account, calculate interest earned in the previous calendar month:
       interest = balance × rate × days_in_month / 365
  3. Skip if interest already posted for that period (idempotent).
  4. Split interest proportionally between admin_fund and sinking_fund.
  5. Post two trust transactions (one per fund) and record a posting entry.

Also processes advance levy payments whose due date has now passed:
  - For each levy schedule marked as_advance that has become due,
    ensure the advance interest has been calculated and optionally auto-posted.

Usage:
  python3 -m backend.cron.cron_trust_interest [--dry-run] [--month YYYY-MM] [--building-id ID]

Schedule (via cron or systemd timer):
  0 6 1 * *  /path/to/venv/bin/python3 -m backend.cron.cron_trust_interest
"""
from __future__ import annotations

import calendar
import os
import sys
from datetime import date, datetime, timezone

import argparse
import asyncio
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId

logger = logging.getLogger("cron_trust_interest")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/cron/cron_trust_interest.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


def format_aud(cents: int) -> str:
    """Generated function header.

    Function: format_aud
    Path: backend/cron/cron_trust_interest.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    negative = cents < 0
    abs_cents = abs(cents)
    dollars = abs_cents // 100
    pence = abs_cents % 100
    formatted = f"${dollars:,}.{pence:02d}"
    return f"-{formatted}" if negative else formatted


async def run_interest_accrual(
        dry_run: bool = False,
        target_month: str | None = None,  # "YYYY-MM"
        target_building_id: str | None = None,
        _raw_db=None,  # injectable for testing; defaults to db._db
) -> dict:
    """Main accrual function. Returns a summary dict."""
    if _raw_db is None:
        from database import db
        _raw_db = db._db

    # Determine the period: default to previous calendar month
    today = date.today()
    if target_month:
        year, month = map(int, target_month.split("-"))
    else:
        # Previous month
        first_of_month = today.replace(day=1)
        last_month_end = first_of_month - __import__("datetime").timedelta(days=1)
        year, month = last_month_end.year, last_month_end.month

    days_in_month = calendar.monthrange(year, month)[1]
    period_start = date(year, month, 1).isoformat()
    period_end = date(year, month, days_in_month).isoformat()

    logger.info("Interest accrual for period %s to %s (dry_run=%s)", period_start, period_end, dry_run)

    summary = {
        "period_start": period_start,
        "period_end": period_end,
        "dry_run": dry_run,
        "buildings_processed": 0,
        "accounts_processed": 0,
        "postings_created": 0,
        "postings_skipped": 0,
        "total_interest_cents": 0,
        "advance_payments_processed": 0,
        "errors": [],
    }

    # Build pipeline to get distinct buildings with interest-earning accounts
    pipeline = [
        {"$match": {"is_active": True, "interest_rate_pa": {"$gt": 0}}},
        {"$group": {"_id": "$building_id"}},
    ]
    if target_building_id:
        pipeline.insert(0, {"$match": {"building_id": ObjectId(target_building_id)}})

    building_groups = await _raw_db.trust_accounts_v2.aggregate(pipeline).to_list(length=200)

    for bg in building_groups:
        building_oid = bg["_id"]
        building_id_str = str(building_oid)
        summary["buildings_processed"] += 1

        try:
            # Load all active accounts for this building with interest > 0
            accounts = await _raw_db.trust_accounts_v2.find({
                "building_id": building_oid,
                "is_active": True,
                "interest_rate_pa": {"$gt": 0},
            }).to_list(length=20)

            # For interest distribution, we need balances of both fund types
            all_accounts = await _raw_db.trust_accounts_v2.find(
                {"building_id": building_oid, "is_active": True}
            ).to_list(length=20)
            admin_bal = sum(
                a.get("current_balance_cents", 0) for a in all_accounts if a.get("account_type") == "admin_fund")
            sinking_bal = sum(
                a.get("current_balance_cents", 0) for a in all_accounts if a.get("account_type") == "sinking_fund")
            total_bal = admin_bal + sinking_bal or 1

            for account in accounts:
                account_id = str(account["_id"])
                balance_cents = account.get("current_balance_cents", 0)
                rate_pa = account.get("interest_rate_pa", 0.0)

                # Calculate interest
                interest_cents = round(balance_cents * rate_pa * days_in_month / 365)
                if interest_cents <= 0:
                    logger.debug("  Account %s: zero interest, skipping", account_id)
                    continue

                summary["accounts_processed"] += 1

                # Idempotency: skip if already posted for this period
                existing = await _raw_db.trust_interest_postings.find_one({
                    "building_id": building_oid,
                    "account_id": ObjectId(account_id),
                    "period_start": period_start,
                    "period_end": period_end,
                })
                if existing:
                    logger.info("  Account %s: already posted for %s-%s, skipping", account_id, period_start,
                                period_end)
                    summary["postings_skipped"] += 1
                    continue

                # Split proportionally
                admin_interest = round(interest_cents * admin_bal / total_bal)
                sinking_interest = interest_cents - admin_interest

                logger.info(
                    "  Building %s Account %s (%s): %s interest (admin=%s sinking=%s)",
                    building_id_str, account_id, account.get("account_name"),
                    format_aud(interest_cents), format_aud(admin_interest), format_aud(sinking_interest),
                )

                if dry_run:
                    summary["postings_created"] += 1
                    summary["total_interest_cents"] += interest_cents
                    continue

                now = _now_iso()
                ref = f"AUTO-INT-{period_start[:7].replace('-', '')}"
                description = f"Bank interest income {period_start} to {period_end} (auto-posted)"

                # Find fund accounts
                admin_acc = next((a for a in all_accounts if a.get("account_type") == "admin_fund"), None)
                sinking_acc = next((a for a in all_accounts if a.get("account_type") == "sinking_fund"), None)

                admin_tx_id = None
                sinking_tx_id = None

                if admin_interest > 0 and admin_acc:
                    tx = {
                        "building_id": building_oid,
                        "trust_account_id": admin_acc["_id"],
                        "type": "interest",
                        "amount_cents": admin_interest,
                        "gst_cents": 0,
                        "description": description + " (admin fund share)",
                        "reference": ref,
                        "payment_method": "bank_credit",
                        "is_reconciled": False,
                        "requires_approval": False,
                        "created_by": "cron_trust_interest",
                        "created_at": now,
                        "metadata": {"interest_posting": True, "period_start": period_start, "period_end": period_end,
                                     "auto_posted": True},
                    }
                    res = await _raw_db.trust_transactions_v2.insert_one(tx)
                    admin_tx_id = str(res.inserted_id)
                    await _raw_db.trust_accounts_v2.update_one(
                        {"_id": admin_acc["_id"]},
                        {"$inc": {"current_balance_cents": admin_interest},
                         "$set": {"updated_at": now, "balance_snapshot_at": now}},
                    )

                if sinking_interest > 0 and sinking_acc:
                    tx = {
                        "building_id": building_oid,
                        "trust_account_id": sinking_acc["_id"],
                        "type": "interest",
                        "amount_cents": sinking_interest,
                        "gst_cents": 0,
                        "description": description + " (capital works fund share)",
                        "reference": ref,
                        "payment_method": "bank_credit",
                        "is_reconciled": False,
                        "requires_approval": False,
                        "created_by": "cron_trust_interest",
                        "created_at": now,
                        "metadata": {"interest_posting": True, "period_start": period_start, "period_end": period_end,
                                     "auto_posted": True},
                    }
                    res = await _raw_db.trust_transactions_v2.insert_one(tx)
                    sinking_tx_id = str(res.inserted_id)
                    await _raw_db.trust_accounts_v2.update_one(
                        {"_id": sinking_acc["_id"]},
                        {"$inc": {"current_balance_cents": sinking_interest},
                         "$set": {"updated_at": now, "balance_snapshot_at": now}},
                    )

                # Store posting record
                posting_doc = {
                    "building_id": building_oid,
                    "account_id": ObjectId(account_id),
                    "period_start": period_start,
                    "period_end": period_end,
                    "total_interest_cents": interest_cents,
                    "admin_interest_cents": admin_interest,
                    "sinking_interest_cents": sinking_interest,
                    "admin_transaction_id": admin_tx_id,
                    "sinking_transaction_id": sinking_tx_id,
                    "reference": ref,
                    "posted_by": "cron_trust_interest",
                    "posted_at": now,
                    "is_auto_posted": True,
                }
                await _raw_db.trust_interest_postings.insert_one(posting_doc)

                # Update account metadata
                await _raw_db.trust_accounts_v2.update_one(
                    {"_id": ObjectId(account_id)},
                    {"$set": {
                        "last_interest_posted_at": now,
                        "last_interest_period_end": period_end,
                        "updated_at": now,
                    }, "$inc": {"interest_ytd_cents": interest_cents}},
                )

                summary["postings_created"] += 1
                summary["total_interest_cents"] += interest_cents

        except Exception as exc:
            logger.error("  Error processing building %s: %s", building_id_str, exc)
            summary["errors"].append({"building_id": building_id_str, "error": str(exc)})

    # ── Process advance levy payments whose due date has passed ───────────────
    today_str = date.today().isoformat()
    advance_query: dict = {
        "is_advance_payment": True,
        "advance_interest_posted": False,
        "advance_interest_earned_cents": {"$gt": 0},
        "due_date": {"$lt": today_str},
    }
    if target_building_id:
        advance_query["building_id"] = ObjectId(target_building_id)
    overdue_advances = await _raw_db.trust_levy_schedules_v2.find(advance_query).to_list(length=500)

    for sched in overdue_advances:
        building_oid = sched.get("building_id")
        building_id_str = str(building_oid)
        schedule_id = str(sched["_id"])
        interest = sched.get("advance_interest_earned_cents", 0)

        logger.info(
            "  Advance payment due. Schedule %s building %s — interest %s",
            schedule_id, building_id_str, format_aud(interest),
        )
        summary["advance_payments_processed"] += 1

        if not dry_run and interest > 0:
            # Find admin and sinking accounts for this building
            all_accs = await _raw_db.trust_accounts_v2.find(
                {"building_id": building_oid, "is_active": True}
            ).to_list(length=20)
            admin_bal = sum(
                a.get("current_balance_cents", 0) for a in all_accs if a.get("account_type") == "admin_fund")
            sinking_bal = sum(
                a.get("current_balance_cents", 0) for a in all_accs if a.get("account_type") == "sinking_fund")
            total_bal_adv = admin_bal + sinking_bal or 1
            admin_share = round(interest * admin_bal / total_bal_adv)
            sinking_share = interest - admin_share

            admin_acc = next((a for a in all_accs if a.get("account_type") == "admin_fund"), None)
            sinking_acc = next((a for a in all_accs if a.get("account_type") == "sinking_fund"), None)
            now = _now_iso()
            payment_date = (sched.get("advance_payment_date") or "")[:10]
            due_date = (sched.get("due_date") or "")[:10]
            ref = f"ADV-INT-{schedule_id[-6:]}"
            desc = f"Advance levy interest (Unit {sched.get('unit_number')} {sched.get('quarter')}, paid {payment_date}, due {due_date})"

            if admin_share > 0 and admin_acc:
                await _raw_db.trust_transactions_v2.insert_one({
                    "building_id": building_oid,
                    "trust_account_id": admin_acc["_id"],
                    "trust_levy_schedule_id": sched["_id"],
                    "type": "interest",
                    "amount_cents": admin_share,
                    "gst_cents": 0,
                    "description": desc + " (admin fund)",
                    "reference": ref,
                    "payment_method": "bank_credit",
                    "is_reconciled": False,
                    "requires_approval": False,
                    "created_by": "cron_trust_interest",
                    "created_at": now,
                    "metadata": {"advance_payment_interest": True},
                })
                await _raw_db.trust_accounts_v2.update_one(
                    {"_id": admin_acc["_id"]},
                    {"$inc": {"current_balance_cents": admin_share},
                     "$set": {"updated_at": now, "balance_snapshot_at": now}},
                )

            if sinking_share > 0 and sinking_acc:
                await _raw_db.trust_transactions_v2.insert_one({
                    "building_id": building_oid,
                    "trust_account_id": sinking_acc["_id"],
                    "trust_levy_schedule_id": sched["_id"],
                    "type": "interest",
                    "amount_cents": sinking_share,
                    "gst_cents": 0,
                    "description": desc + " (capital works fund)",
                    "reference": ref,
                    "payment_method": "bank_credit",
                    "is_reconciled": False,
                    "requires_approval": False,
                    "created_by": "cron_trust_interest",
                    "created_at": now,
                    "metadata": {"advance_payment_interest": True},
                })
                await _raw_db.trust_accounts_v2.update_one(
                    {"_id": sinking_acc["_id"]},
                    {"$inc": {"current_balance_cents": sinking_share},
                     "$set": {"updated_at": now, "balance_snapshot_at": now}},
                )

            # Mark schedule advance interest as posted
            await _raw_db.trust_levy_schedules_v2.update_one(
                {"_id": sched["_id"]},
                {"$set": {
                    "advance_interest_posted": True,
                    "advance_interest_posted_at": now,
                    "updated_at": now,
                }},
            )

    logger.info(
        "Accrual complete: %d buildings, %d postings created, %d skipped, total %s, %d advance payments processed, %d errors",
        summary["buildings_processed"],
        summary["postings_created"],
        summary["postings_skipped"],
        format_aud(summary["total_interest_cents"]),
        summary["advance_payments_processed"],
        len(summary["errors"]),
    )
    return summary


async def _async_main(dry_run: bool, month: str | None, building_id: str | None) -> None:
    """Generated function header.

    Function: _async_main
    Path: backend/cron/cron_trust_interest.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from pymongo import AsyncMongoClient
    # Use the canonical env var names (MONGO_URL / DB_NAME) that the web app and all other
    # crons use. MONGO_URI / MONGO_DB were non-standard defaults that caused this cron to
    # write to "eastgate_db" while routers read from "strata_production" (different DB).
    mongo_uri = os.environ.get("MONGO_URL", os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    client = AsyncMongoClient(mongo_uri)
    db_name = os.environ.get("DB_NAME", os.environ.get("MONGO_DB", "strata_production"))

    # Patch database module db object so helpers work
    import database
    database.db._db = client[db_name]

    result = await run_interest_accrual(dry_run=dry_run, target_month=month, target_building_id=building_id)

    if result["errors"]:
        logger.error("Errors encountered: %s", result["errors"])
        sys.exit(1)

    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monthly trust account interest accrual")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write to DB")
    parser.add_argument("--month", help="Target month YYYY-MM (default: previous month)")
    parser.add_argument("--building-id", help="Process a specific building only")
    args = parser.parse_args()

    asyncio.run(_async_main(args.dry_run, args.month, args.building_id))
