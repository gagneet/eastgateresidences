"""
Trust Accounting Seed — Multi-Building Configuration

Seeds trust accounting data for the three real buildings:
  1. East Gate Residences (13195) — SACRED: only sets trust_config, never touches units/levies
  2. Sierra (16244) — demo trust config + accounts + sample levy schedules + transactions
  3. Harbourview Residences (18932) — demo trust config + accounts + sample levy schedules + transactions

Design principles:
  - Never create buildings — always look up by `id` field; skip if not found
  - East Gate: ONLY updates trust_config on the building document
  - Sierra / Harbourview: seed trust_config, trust_accounts_v2, levy schedules (from existing units),
    sample transactions and audit logs
  - Idempotent: upsert operations throughout

Usage:
  python3 -m seeds.trust_accounting
  python3 -m seeds.trust_accounting --dry-run
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import asyncio
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

DRY_RUN = "--dry-run" in sys.argv


# ─── Luhn check digit (mirrors frontend lib/trust/deft.ts) ──────────────────

def _luhn_check_digit(digits: str) -> int:
    """Generated function header.

    Function: _luhn_check_digit
    Path: backend/seeds/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    nums = list(reversed([int(c) for c in digits]))
    total = 0
    for idx, d in enumerate(nums):
        if idx % 2 == 1:
            doubled = d * 2
            total += (doubled - 9) if doubled > 9 else doubled
        else:
            total += d
    return (10 - (total % 10)) % 10


def _generate_deft_crn(lot_number: int, quarter: str, biller_code: str) -> str:
    """Generated function header.

    Function: _generate_deft_crn
    Path: backend/seeds/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    lot_padded = str(lot_number).zfill(4)
    quarter_code = quarter.replace("-", "")
    base_for_luhn = "".join(c for c in (biller_code + lot_padded + quarter_code) if c.isdigit())
    check_digit = _luhn_check_digit(base_for_luhn)
    return f"{biller_code}-{lot_padded}-{quarter_code}-{check_digit}"


# ─── Proportional split (mirrors frontend lib/trust/money.ts) ─────────────

def _proportional_split(total_cents: int, weights: List[int]) -> List[int]:
    """Generated function header.

    Function: _proportional_split
    Path: backend/seeds/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if not weights:
        return []
    total_weight = sum(weights)
    if total_weight == 0:
        return [0] * len(weights)
    exact = [(total_cents * w) / total_weight for w in weights]
    floored = [int(x) for x in exact]
    remainders = [e - f for e, f in zip(exact, floored)]
    distributed = sum(floored)
    extra = total_cents - distributed
    indices = sorted(range(len(remainders)), key=lambda i: remainders[i], reverse=True)
    result = list(floored)
    for i in range(extra):
        result[indices[i]] += 1
    return result


def _format_aud(cents: int) -> str:
    """Generated function header.

    Function: _format_aud
    Path: backend/seeds/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    neg = cents < 0
    abs_c = abs(cents)
    formatted = f"${abs_c // 100:,}.{abs_c % 100:02d}"
    return f"-{formatted}" if neg else formatted


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/seeds/trust_accounting.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


# ─── Seed configuration objects ──────────────────────────────────────────────
# These are the ONLY place where real per-building values appear.
# Application code itself must never contain such values.

EAST_GATE_SEED_CONFIG: Dict[str, Any] = {
    "building_id": "13195",
    "building_name": "East Gate Residences",
    # SACRED: East Gate only updates trust_config — never touches units or levy schedules
    "east_gate_config_only": True,
    "trust_config": {
        "current_financial_year": "2026-27",
        # Budgets in integer cents — stored ex-GST. Admin $309,882 ex-GST, Sinking $90,459 ex-GST.
        # Inc-GST: Admin=$340,870.20, Sinking=$99,504.90. GST added at API/display layer only.
        "admin_fund_annual_budget_cents": 34087020,  # inc-GST levy billing target: $340,870.20
        "sinking_fund_annual_budget_cents": 9950490,  # inc-GST levy billing target: $99,504.90
        "quarterly_due_dates": ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"],
        "grace_period_days": 14,
        "arrears_interest_rate": 0.10,  # ACT statutory rate
        "deft_biller_code_admin": "MOCK-EG-452301",
        "deft_biller_code_sinking": "MOCK-EG-452302",
        "bank_name": "Macquarie Bank",
        "manager_alert_email": "admin@eastgateresidences.com.au",
        "arrears_reminder_days": 14,
        "arrears_formal_notice_days": 21,
        "arrears_interest_charge_days": 30,
        "arrears_legal_flag_days": 60,
        "is_trust_configured": True,
    },
    "trust_accounts": [
        {
            "account_type": "admin_fund",
            "bsb": "182-266",
            "account_number_masked": "****1108",
            "bank_name": "Macquarie Bank",
            "account_name": "The Proprietors Of Units Plan 13195",
            # opening_balance = bank reconciliation value (no static current_balance stored)
            "opening_balance_cents": 1777811,  # $17,778.11 as at 2026-04-06
            "last_reconciled_at": "2026-04-06T00:00:00Z",
            "last_reconciled_balance_cents": 1777811,  # $17,778.11 (external bank statement)
        },
        {
            "account_type": "sinking_fund",
            "bsb": "182-266",
            "account_number_masked": "****1108",
            "bank_name": "Macquarie Bank",
            "account_name": "The Proprietors Of Units Plan 13195 — Capital Works Fund",
            "opening_balance_cents": 15635148,  # $156,351.48 as at 2026-04-06
            "last_reconciled_at": "2026-04-06T00:00:00Z",
            "last_reconciled_balance_cents": 15635148,  # $156,351.48 (external bank statement)
        },
    ],
    # East Gate seed transactions only — no unit seeding, no levy schedules
    "seed_transactions": [
        {"type": "disbursement", "amount_cents": 741200, "description": "Management Fee Q1",
         "reference": "INV-2026-001", "payee_payer": "Strata Manager"},
        {"type": "disbursement", "amount_cents": 669200, "description": "Insurance Premium",
         "reference": "INV-2026-002", "payee_payer": "Insurer"},
        {"type": "disbursement", "amount_cents": 452100, "description": "Cleaning Services",
         "reference": "INV-2026-003", "payee_payer": "Cleaner Co"},
        {"type": "disbursement", "amount_cents": 306700, "description": "Building Repairs", "reference": "INV-2026-004",
         "payee_payer": "Trades Co"},
        {"type": "disbursement", "amount_cents": 285000, "description": "Electricity Common Areas",
         "reference": "INV-2026-005", "payee_payer": "AGL"},
        {"type": "disbursement", "amount_cents": 195000, "description": "CCTV System Upgrade",
         "reference": "INV-2026-006", "payee_payer": "Security Co"},
        {"type": "disbursement", "amount_cents": 178500, "description": "Electrical Repairs",
         "reference": "INV-2026-007", "payee_payer": "Electrician"},
        {"type": "bank_charge", "amount_cents": 1500, "description": "Bank Service Fee Jan", "reference": None,
         "payee_payer": "Macquarie Bank"},
        {"type": "bank_charge", "amount_cents": 1500, "description": "Bank Service Fee Feb", "reference": None,
         "payee_payer": "Macquarie Bank"},
    ],
}

SIERRA_SEED_CONFIG: Dict[str, Any] = {
    "building_id": "16244",
    "building_name": "Sierra",
    "trust_config": {
        "current_financial_year": "2026-27",
        "admin_fund_annual_budget_cents": 18000000,  # $180,000/yr
        "sinking_fund_annual_budget_cents": 6000000,  # $60,000/yr
        "quarterly_due_dates": ["2026-03-01", "2026-06-01", "2026-09-01", "2026-12-01"],
        "grace_period_days": 14,
        "arrears_interest_rate": 0.10,
        "deft_biller_code_admin": "MOCK-SG-456001",
        "deft_biller_code_sinking": "MOCK-SG-456002",
        "bank_name": "Westpac",
        "manager_alert_email": "manager@sierra.test",
        "arrears_reminder_days": 14,
        "arrears_formal_notice_days": 21,
        "arrears_interest_charge_days": 30,
        "arrears_legal_flag_days": 60,
        "is_trust_configured": True,
    },
    "trust_accounts": [
        {
            "account_type": "admin_fund",
            "bsb": "033-001",
            "account_number_masked": "****7801",
            "bank_name": "Westpac",
            "account_name": "Sierra OC Admin Fund",
            "current_balance_cents": 3250000,  # $32,500.00
            "last_reconciled_at": "2026-02-28T00:00:00Z",
            "last_reconciled_balance_cents": 3250000,
        },
        {
            "account_type": "sinking_fund",
            "bsb": "033-001",
            "account_number_masked": "****7802",
            "bank_name": "Westpac",
            "account_name": "Sierra OC Sinking Fund",
            "current_balance_cents": 8750000,  # $87,500.00
            "last_reconciled_at": "2026-02-28T00:00:00Z",
            "last_reconciled_balance_cents": 8750000,
        },
    ],
    "dummy_units": [
        {"unit_number": "U001", "lot_number": 1, "unit_type": "apartment", "uoe": 100},
        {"unit_number": "U002", "lot_number": 2, "unit_type": "apartment", "uoe": 100},
        {"unit_number": "U003", "lot_number": 3, "unit_type": "apartment", "uoe": 100},
        {"unit_number": "U004", "lot_number": 4, "unit_type": "apartment", "uoe": 100},
        {"unit_number": "U005", "lot_number": 5, "unit_type": "apartment", "uoe": 100},
    ],
    "quarter": "2026-Q1",
    "financial_year": "2026-27",
    "due_date": "2026-03-01",
    "levy_q1_status_distribution": {
        "paid": 3, "pending": 1, "overdue": 1, "partial": 0,
    },
    "seed_transactions": [
        {"type": "disbursement", "amount_cents": 320000, "description": "Management Fee Q1",
         "reference": "SG-INV-2026-001", "payee_payer": "Strata Manager"},
        {"type": "disbursement", "amount_cents": 185000, "description": "Insurance Premium",
         "reference": "SG-INV-2026-002", "payee_payer": "Insurer"},
        {"type": "disbursement", "amount_cents": 95000, "description": "Cleaning Services",
         "reference": "SG-INV-2026-003", "payee_payer": "Cleaner Co"},
        {"type": "bank_charge", "amount_cents": 1200, "description": "Bank Service Fee", "reference": None,
         "payee_payer": "Westpac"},
    ],
}

HARBOURVIEW_SEED_CONFIG: Dict[str, Any] = {
    "building_id": "18932",
    "building_name": "Harbourview Residences",
    "trust_config": {
        "current_financial_year": "2026-27",
        "admin_fund_annual_budget_cents": 32000000,  # $320,000/yr
        "sinking_fund_annual_budget_cents": 10000000,  # $100,000/yr
        "quarterly_due_dates": ["2026-03-15", "2026-06-15", "2026-09-15", "2026-12-15"],
        "grace_period_days": 21,
        "arrears_interest_rate": 0.10,
        "deft_biller_code_admin": "MOCK-HV-789001",
        "deft_biller_code_sinking": "MOCK-HV-789002",
        "bank_name": "ANZ",
        "manager_alert_email": "manager@harbourview.test",
        "arrears_reminder_days": 14,
        "arrears_formal_notice_days": 21,
        "arrears_interest_charge_days": 30,
        "arrears_legal_flag_days": 60,
        "is_trust_configured": True,
    },
    "trust_accounts": [
    ],
    "dummy_units": [
        {"unit_number": "U001", "lot_number": 1, "unit_type": "apartment", "uoe": 100},
        {"unit_number": "U002", "lot_number": 2, "unit_type": "apartment", "uoe": 100},
        {"unit_number": "U003", "lot_number": 3, "unit_type": "apartment", "uoe": 100},
        {"unit_number": "U004", "lot_number": 4, "unit_type": "apartment", "uoe": 100},
        {"unit_number": "U005", "lot_number": 5, "unit_type": "apartment", "uoe": 100},
    ],
    "quarter": "2026-Q1",
    "financial_year": "2026-27",
    "due_date": "2026-03-15",
    "levy_q1_status_distribution": {
        "paid": 3, "pending": 2, "overdue": 0, "partial": 0,
    },
    "seed_transactions": [
        {"type": "disbursement", "amount_cents": 580000, "description": "Management Fee Q1",
         "reference": "HV-INV-2026-001", "payee_payer": "Strata Manager"},
        {"type": "disbursement", "amount_cents": 320000, "description": "Insurance Premium",
         "reference": "HV-INV-2026-002", "payee_payer": "Insurer"},
        {"type": "disbursement", "amount_cents": 145000, "description": "Cleaning Services",
         "reference": "HV-INV-2026-003", "payee_payer": "Cleaner Co"},
        {"type": "bank_charge", "amount_cents": 1500, "description": "Bank Service Fee", "reference": None,
         "payee_payer": "ANZ"},
    ],
}


# ─── East Gate trust_config only (SACRED) ────────────────────────────────────

async def seed_east_gate_trust_config(config: Dict[str, Any], admin_user_id: str, db: Any) -> None:
    """
    For East Gate ONLY: update trust_config and trust_accounts on the existing building.
    Never touches units or levy schedules — those are real production data.
    """
    log_prefix = "[seed:trust:East Gate]"

    building = await db.buildings.find_one({"id": "13195"})
    if not building:
        logger.warning("%s Building not found (id='13195') — skipping", log_prefix)
        return

    logger.info("%s Building found: %s", log_prefix, building.get("name"))

    if DRY_RUN:
        logger.info("%s DRY RUN — would update trust_config only", log_prefix)
        return

    # Use string building_id (not ObjectId) — all trust collections store building_id as string
    building_id_str = config["building_id"]  # "13195"
    building_oid = building["_id"]  # ObjectId for buildings._id lookups

    # Update trust_config
    trust_config = config["trust_config"].copy()
    trust_config["trust_configured_at"] = _now_iso()
    trust_config["trust_configured_by"] = admin_user_id

    await db.buildings.update_one(
        {"_id": building_oid},
        {"$set": {"trust_config": trust_config}},
    )
    logger.info("%s trust_config updated", log_prefix)

    # Upsert trust accounts — use $set (not $setOnInsert) so values get updated
    biller_code_admin = trust_config.get("deft_biller_code_admin")
    for acc_config in config["trust_accounts"]:
        await db.trust_accounts_v2.update_one(
            {"building_id": building_id_str, "account_type": acc_config["account_type"]},
            {"$set": {
                "building_id": building_id_str,
                "account_type": acc_config["account_type"],
                "bsb": acc_config["bsb"],
                "account_number_masked": acc_config["account_number_masked"],
                "bank_name": acc_config["bank_name"],
                "account_name": acc_config["account_name"],
                "deft_biller_code": trust_config.get(
                    f"deft_biller_code_{acc_config['account_type']}", biller_code_admin
                ),
                # Store opening_balance (not static current_balance — computed from transactions)
                "opening_balance_cents": acc_config.get("opening_balance_cents", 0),
                "last_reconciled_at": acc_config.get("last_reconciled_at"),
                "last_reconciled_balance_cents": acc_config.get("last_reconciled_balance_cents", 0),
                "is_active": True,
                "updated_at": _now_iso(),
            }, "$setOnInsert": {
                "created_by": admin_user_id,
                "created_at": _now_iso(),
            }},
            upsert=True,
        )
    logger.info("%s Trust accounts upserted", log_prefix)

    # Seed transactions (idempotent by reference)
    admin_account = await db.trust_accounts_v2.find_one({
        "building_id": building_id_str, "account_type": "admin_fund"
    })
    for tx in config.get("seed_transactions", []):
        if tx.get("reference"):
            existing_tx = await db.trust_transactions_v2.find_one({
                "building_id": building_id_str,
                "reference": tx["reference"],
            })
            if existing_tx:
                continue

        tx_doc = {
            "building_id": building_id_str,
            "trust_account_id": admin_account["_id"] if admin_account else None,
            "type": tx["type"],
            "amount_cents": tx["amount_cents"],
            "gst_cents": 0,
            "description": tx["description"],
            "reference": tx.get("reference"),
            "payee_payer": tx.get("payee_payer"),
            "payment_method": "eft",
            "is_reconciled": True,
            "is_reversed": False,
            "requires_approval": False,
            "created_by": admin_user_id,
            "created_at": _now_iso(),
        }
        await db.trust_transactions_v2.insert_one(tx_doc)
        await db.trust_audit_logs.insert_one({
            "building_id": building_id,
            "action": "transaction_created",
            "entity_type": "TrustTransaction",
            "entity_id": str(tx_doc.get("reference", "seed")),
            "performed_by": admin_user_id,
            "performer_role": "super_admin",
            "performer_email": "seed@system",
            "metadata": {"seeded": True, "type": tx["type"]},
            "timestamp": _now_iso(),
        })
    logger.info("%s Seed transactions upserted", log_prefix)
    logger.info("%s Complete (trust_config only — units/levies untouched)", log_prefix)


# ─── Full seed for Sierra and Harbourview ────────────────────────────────────

async def seed_trust_for_building(config: Dict[str, Any], admin_user_id: str, db: Any) -> None:
    """
    Seed trust accounting data for Sierra or Harbourview.

    Finds the building by `id` field. If not found, skips (never creates buildings).
    Uses existing units from db.units; falls back to dummy units if none exist.
    Idempotent: uses upsert operations throughout.
    """
    building_id_str = config["building_id"]
    building_name = config["building_name"]
    log_prefix = f"[seed:trust:{building_name}]"

    # Find building — never create
    building = await db.buildings.find_one({"id": building_id_str})
    if not building:
        logger.warning("%s Building not found (id='%s') — skipping", log_prefix, building_id_str)
        return

    logger.info("%s Building found: %s", log_prefix, building.get("name"))

    if DRY_RUN:
        logger.info("%s DRY RUN — no writes will occur", log_prefix)
        return

    building_id = building["_id"]

    # ── Step 1: Update trust_config ──────────────────────────────────────────
    trust_config = config["trust_config"].copy()
    trust_config["trust_configured_at"] = _now_iso()
    trust_config["trust_configured_by"] = admin_user_id

    await db.buildings.update_one(
        {"_id": building_id},
        {"$set": {"trust_config": trust_config}},
    )
    logger.info("%s trust_config updated", log_prefix)

    # ── Step 2: Resolve units — prefer existing, fall back to dummies ─────────
    units = await db.units.find(
        {"building_id": building_id_str, "is_active": True}
    ).sort("lot_number", 1).to_list(500)

    if not units:
        # Also try ObjectId form
        units = await db.units.find(
            {"building_id": building_id, "is_active": True}
        ).sort("lot_number", 1).to_list(500)

    if not units:
        logger.info("%s No existing units found — seeding 5 dummy units", log_prefix)
        dummy_units_config = config.get("dummy_units", [
            {"unit_number": f"U{str(i).zfill(3)}", "lot_number": i, "unit_type": "apartment", "uoe": 100}
            for i in range(1, 6)
        ])
        for du in dummy_units_config:
            await db.units.update_one(
                {"building_id": building_id_str, "lot_number": du["lot_number"]},
                {"$setOnInsert": {
                    "building_id": building_id_str,
                    "unit_number": du["unit_number"],
                    "lot_number": du["lot_number"],
                    "unit_type": du["unit_type"],
                    "uoe": du["uoe"],
                    "is_active": True,
                    "owner_id": None,
                    "floor": None,
                    "bedrooms": None,
                }},
                upsert=True,
            )
        units = await db.units.find(
            {"building_id": building_id_str, "is_active": True}
        ).sort("lot_number", 1).to_list(500)

    # Cache total_uoe on building
    total_uoe = sum(u.get("uoe", 0) for u in units)
    await db.buildings.update_one(
        {"_id": building_id},
        {"$set": {"trust_config.total_uoe": total_uoe}},
    )
    logger.info("%s Resolved %d units, total_uoe=%d", log_prefix, len(units), total_uoe)

    # ── Step 3: Upsert trust accounts ─────────────────────────────────────────
    biller_code_admin = trust_config.get("deft_biller_code_admin")
    for acc_config in config["trust_accounts"]:
        await db.trust_accounts_v2.update_one(
            {"building_id": building_id, "account_type": acc_config["account_type"]},
            {"$setOnInsert": {
                "building_id": building_id,
                "account_type": acc_config["account_type"],
                "bsb": acc_config["bsb"],
                "account_number_masked": acc_config["account_number_masked"],
                "bank_name": acc_config["bank_name"],
                "account_name": acc_config["account_name"],
                "deft_biller_code": trust_config.get(
                    f"deft_biller_code_{acc_config['account_type']}", biller_code_admin
                ),
                "current_balance_cents": acc_config["current_balance_cents"],
                "last_reconciled_at": acc_config.get("last_reconciled_at"),
                "last_reconciled_balance_cents": acc_config.get("last_reconciled_balance_cents", 0),
                "is_active": True,
                "created_by": admin_user_id,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }},
            upsert=True,
        )
    logger.info("%s Trust accounts upserted", log_prefix)

    # ── Step 4: Generate Q1 levy schedules ────────────────────────────────────
    admin_annual = trust_config["admin_fund_annual_budget_cents"]
    sinking_annual = trust_config["sinking_fund_annual_budget_cents"]
    admin_quarterly = admin_annual // 4
    sinking_quarterly = sinking_annual // 4

    uoe_list = [u.get("uoe", 0) for u in units]
    admin_splits = _proportional_split(admin_quarterly, uoe_list)
    sinking_splits = _proportional_split(sinking_quarterly, uoe_list)

    quarter = config["quarter"]
    financial_year = config["financial_year"]
    due_date = config["due_date"]
    grace_period_days = trust_config["grace_period_days"]

    status_dist = config.get("levy_q1_status_distribution", {})
    status_cycle = []
    for status, count in status_dist.items():
        status_cycle.extend([status] * count)
    while len(status_cycle) < len(units):
        status_cycle.append("pending")

    schedules_created = 0
    for i, unit in enumerate(units):
        existing = await db.trust_levy_schedules_v2.find_one({
            "building_id": building_id,
            "unit_id": unit["_id"],
            "quarter": quarter,
        })
        if existing:
            continue

        admin_cents = admin_splits[i]
        sinking_cents = sinking_splits[i]
        total_cents = admin_cents + sinking_cents

        status = status_cycle[i] if i < len(status_cycle) else "pending"
        paid_cents = total_cents if status == "paid" else (total_cents // 2 if status == "partial" else 0)
        outstanding_cents = total_cents - paid_cents

        crn = _generate_deft_crn(
            unit.get("lot_number", i + 1),
            quarter,
            biller_code_admin or "MOCK-DEFAULT",
        )

        await db.trust_levy_schedules_v2.insert_one({
            "building_id": building_id,
            "unit_id": unit["_id"],
            "unit_number": unit.get("unit_number", ""),
            "lot_number": unit.get("lot_number", i + 1),
            "uoe_snapshot": unit.get("uoe", 0),
            "total_uoe_snapshot": total_uoe,
            "admin_budget_cents_snapshot": admin_quarterly,
            "sinking_budget_cents_snapshot": sinking_quarterly,
            "quarter": quarter,
            "financial_year": financial_year,
            "due_date": due_date,
            "grace_period_days": grace_period_days,
            "admin_fund_cents": admin_cents,
            "sinking_fund_cents": sinking_cents,
            "special_levy_cents": 0,
            "total_cents": total_cents,
            "deft_crn": crn,
            "status": status,
            "paid_cents": paid_cents,
            "outstanding_cents": outstanding_cents,
            "paid_at": _now_iso() if status == "paid" else None,
            "overdue_since": _now_iso() if status == "overdue" else None,
            "interest_accrued_cents": 0,
            "drb_stage": "none",
            "notes": None,
            "generated_by": admin_user_id,
            "generated_at": _now_iso(),
            "updated_at": _now_iso(),
        })
        schedules_created += 1

    admin_total = sum(admin_splits)
    sinking_total = sum(sinking_splits)
    logger.info(
        "%s Generated %d levy schedules — admin: %s, sinking: %s",
        log_prefix, schedules_created,
        _format_aud(admin_total), _format_aud(sinking_total),
    )

    # ── Step 5: Upsert seed transactions ──────────────────────────────────────
    admin_account = await db.trust_accounts_v2.find_one({
        "building_id": building_id, "account_type": "admin_fund"
    })

    for tx in config.get("seed_transactions", []):
        if tx.get("reference"):
            existing_tx = await db.trust_transactions_v2.find_one({
                "building_id": building_id,
                "reference": tx["reference"],
            })
            if existing_tx:
                continue

        tx_doc = {
            "building_id": building_id,
            "trust_account_id": admin_account["_id"] if admin_account else None,
            "type": tx["type"],
            "amount_cents": tx["amount_cents"],
            "gst_cents": 0,
            "description": tx["description"],
            "reference": tx.get("reference"),
            "payee_payer": tx.get("payee_payer"),
            "payment_method": "eft",
            "is_reconciled": True,
            "is_reversed": False,
            "requires_approval": False,
            "created_by": admin_user_id,
            "created_at": _now_iso(),
        }
        await db.trust_transactions_v2.insert_one(tx_doc)
        await db.trust_audit_logs.insert_one({
            "building_id": building_id,
            "action": "transaction_created",
            "entity_type": "TrustTransaction",
            "entity_id": str(tx_doc.get("reference", "seed")),
            "performed_by": admin_user_id,
            "performer_role": "super_admin",
            "performer_email": "seed@system",
            "metadata": {"seeded": True, "type": tx["type"]},
            "timestamp": _now_iso(),
        })

    logger.info("%s Seed transactions upserted", log_prefix)
    logger.info("%s Complete ✓", log_prefix)


async def seed_trust_accounting(db: Any) -> None:
    """Seed trust accounting for all configured buildings."""
    admin_user = await db.users.find_one({"role": "super_admin"})
    admin_user_id = str(admin_user["_id"]) if admin_user else "seed_admin"

    # East Gate: trust_config + accounts + transactions only (NEVER touches units/levy schedules)
    await seed_east_gate_trust_config(EAST_GATE_SEED_CONFIG, admin_user_id, db)

    # Sierra and Harbourview: full seed (trust_config, accounts, levy schedules, transactions)
    await seed_trust_for_building(SIERRA_SEED_CONFIG, admin_user_id, db)
    await seed_trust_for_building(HARBOURVIEW_SEED_CONFIG, admin_user_id, db)

    logger.info("[seed:trust] Complete — 3 buildings processed")


if __name__ == "__main__":
    """Run standalone with: python3 -m seeds.trust_accounting [--dry-run]"""
    import os
    from dotenv import load_dotenv
    from pathlib import Path
    from pymongo import AsyncMongoClient

    load_dotenv(Path(__file__).parent.parent / ".env")


    async def main():
        """Generated function header.

        Function: main
        Path: backend/seeds/trust_accounting.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        logging.basicConfig(level=logging.INFO)
        mongo_uri = os.environ["MONGO_URL"]
        db_name = os.environ["DB_NAME"]
        client = AsyncMongoClient(mongo_uri)
        db = client[db_name]
        await seed_trust_accounting(db)
        client.close()


    asyncio.run(main())
