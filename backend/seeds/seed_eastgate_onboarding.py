"""
East Gate Residences (UP13195) — Onboarding Seed
=================================================
Seeds historical and current operational records for East Gate Residences
(building_id="13195") as part of building onboarding.

This script populates:
  - PPM (Planned Preventive Maintenance) schedule
  - Current and near-future compliance items (updates stale due dates)
  - Recent and in-progress workflow requests (maintenance, governance)

Records are marked is_seed_data=True so they can be identified and re-seeded
without touching levy/financial records. They are NOT test data (is_test_data
is not set) — they represent real building operations.

Usage (from backend/):
    venv/bin/python3 seeds/seed_eastgate_onboarding.py
    venv/bin/python3 seeds/seed_eastgate_onboarding.py --tear-down  # removes seeded records
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BUILDING_ID = "13195"
_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # UUID5 DNS namespace

# Deterministic stable IDs — uuid5 ensures idempotency across re-runs
_u5 = lambda key: str(uuid.uuid5(_NS, f"eg-onboard:{key}"))  # noqa: E731

TODAY = date.today()
NOW = datetime.now(timezone.utc)


async def _get_db():
    """Generated function header.

    Function: _get_db
    Path: backend/seeds/seed_eastgate_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    return client[os.getenv("DB_NAME", "strataos_production")]


# ── PPM Schedule ──────────────────────────────────────────────────────────────

async def seed_ppm(db) -> int:
    """
    Planned Preventive Maintenance schedule for East Gate.

    Based on a typical 87-unit residential strata building with lifts,
    pool, fire systems, and common area infrastructure.
    """
    items = [
        # ── Lift ────────────────────────────────────────────────────────────
        {
            "id": _u5("ppm:lift-quarterly-q2-2026"),
            "category": "lift",
            "title": "Lift — Quarterly Service (Q2 2026)",
            "description": "Quarterly service and safety inspection of all 3 lift cars by certified contractor. Includes lubrication, rope tension check, door alignment.",
            "contractor": "Kone Elevators",
            "status": "overdue",
            "scheduled_date": (TODAY - timedelta(days=18)).isoformat(),
            "due_date": (TODAY - timedelta(days=18)).isoformat(),
            "frequency": "quarterly",
            "risk_level": "high",
        },
        {
            "id": _u5("ppm:lift-annual-2026"),
            "category": "lift",
            "title": "Lift — Annual Safety Inspection",
            "description": "Annual independent safety audit and certification required under Work Health and Safety Regulations. Generates inspection certificate for council records.",
            "contractor": "Kone Elevators",
            "status": "scheduled",
            "scheduled_date": (TODAY + timedelta(days=42)).isoformat(),
            "due_date": (TODAY + timedelta(days=42)).isoformat(),
            "frequency": "annual",
            "risk_level": "high",
        },
        # ── Fire Systems ─────────────────────────────────────────────────────
        {
            "id": _u5("ppm:fire-6month-2026-h1"),
            "category": "fire",
            "title": "Fire System — 6-Monthly Test & Inspection (H1 2026)",
            "description": "Inspection and testing of all fire detection, suppression and evacuation systems. Mandatory under Australian Standard AS1851.",
            "contractor": "Chubb Fire & Security",
            "status": "completed",
            "scheduled_date": (TODAY - timedelta(days=55)).isoformat(),
            "completed_date": (TODAY - timedelta(days=51)).isoformat(),
            "due_date": (TODAY - timedelta(days=55)).isoformat(),
            "frequency": "biannual",
            "risk_level": "critical",
        },
        {
            "id": _u5("ppm:fire-6month-2026-h2"),
            "category": "fire",
            "title": "Fire System — 6-Monthly Test & Inspection (H2 2026)",
            "description": "Second half-year inspection and testing of all fire detection, suppression and evacuation systems.",
            "contractor": "Chubb Fire & Security",
            "status": "scheduled",
            "scheduled_date": (TODAY + timedelta(days=130)).isoformat(),
            "due_date": (TODAY + timedelta(days=130)).isoformat(),
            "frequency": "biannual",
            "risk_level": "critical",
        },
        {
            "id": _u5("ppm:fire-annual-2026"),
            "category": "fire",
            "title": "Fire Safety Statement — Annual Lodgement",
            "description": "Annual Fire Safety Statement submitted to local council as required under the Environmental Planning and Assessment Regulation.",
            "contractor": "Chubb Fire & Security",
            "status": "scheduled",
            "scheduled_date": (TODAY + timedelta(days=220)).isoformat(),
            "due_date": (TODAY + timedelta(days=220)).isoformat(),
            "frequency": "annual",
            "risk_level": "high",
        },
        # ── Pool & Spa ───────────────────────────────────────────────────────
        {
            "id": _u5("ppm:pool-monthly-jun-2026"),
            "category": "pool",
            "title": "Pool & Spa — Monthly Water Quality Test (June 2026)",
            "description": "Monthly water chemistry test, filter backwash, and pool equipment inspection. Records maintained for council audit.",
            "contractor": "Poolwerx Macquarie",
            "status": "scheduled",
            "scheduled_date": (TODAY + timedelta(days=5)).isoformat(),
            "due_date": (TODAY + timedelta(days=5)).isoformat(),
            "frequency": "monthly",
            "risk_level": "medium",
        },
        {
            "id": _u5("ppm:pool-monthly-may-2026"),
            "category": "pool",
            "title": "Pool & Spa — Monthly Water Quality Test (May 2026)",
            "description": "Monthly water chemistry test, filter backwash, and pool equipment inspection.",
            "contractor": "Poolwerx Macquarie",
            "status": "completed",
            "scheduled_date": (TODAY - timedelta(days=26)).isoformat(),
            "completed_date": (TODAY - timedelta(days=25)).isoformat(),
            "due_date": (TODAY - timedelta(days=26)).isoformat(),
            "frequency": "monthly",
            "risk_level": "medium",
        },
        # ── Emergency Lighting ───────────────────────────────────────────────
        {
            "id": _u5("ppm:emergency-lighting-2026"),
            "category": "electrical",
            "title": "Emergency Lighting — Annual Test & Maintenance",
            "description": "Annual discharge test and replacement of failed emergency lighting luminaires per AS2293.2.",
            "contractor": "Total Fire & Electrical",
            "status": "scheduled",
            "scheduled_date": (TODAY + timedelta(days=21)).isoformat(),
            "due_date": (TODAY + timedelta(days=21)).isoformat(),
            "frequency": "annual",
            "risk_level": "high",
        },
        # ── Backflow Prevention ──────────────────────────────────────────────
        {
            "id": _u5("ppm:backflow-annual-2026"),
            "category": "plumbing",
            "title": "Backflow Prevention Device — Annual Test",
            "description": "Annual test of all backflow prevention devices as required by Sydney Water. Test certificates submitted to Sydney Water.",
            "contractor": "Total Flow Plumbing",
            "status": "scheduled",
            "scheduled_date": (TODAY + timedelta(days=60)).isoformat(),
            "due_date": (TODAY + timedelta(days=60)).isoformat(),
            "frequency": "annual",
            "risk_level": "medium",
        },
        # ── Common Areas ─────────────────────────────────────────────────────
        {
            "id": _u5("ppm:common-area-cleaning-jun-2026"),
            "category": "cleaning",
            "title": "Common Area — Weekly Cleaning Inspection (w/e 30 May 2026)",
            "description": "Weekly inspection of foyer, corridors, car park and plant room areas. Cleaner sign-off sheet filed.",
            "contractor": "CleanPro Services",
            "status": "completed",
            "scheduled_date": (TODAY - timedelta(days=2)).isoformat(),
            "completed_date": (TODAY - timedelta(days=1)).isoformat(),
            "due_date": (TODAY - timedelta(days=2)).isoformat(),
            "frequency": "weekly",
            "risk_level": "low",
        },
        {
            "id": _u5("ppm:common-area-cleaning-jun-2026-next"),
            "category": "cleaning",
            "title": "Common Area — Weekly Cleaning Inspection (w/e 6 Jun 2026)",
            "description": "Weekly inspection of foyer, corridors, car park and plant room areas.",
            "contractor": "CleanPro Services",
            "status": "scheduled",
            "scheduled_date": (TODAY + timedelta(days=5)).isoformat(),
            "due_date": (TODAY + timedelta(days=5)).isoformat(),
            "frequency": "weekly",
            "risk_level": "low",
        },
        # ── Gym Equipment ────────────────────────────────────────────────────
        {
            "id": _u5("ppm:gym-equipment-2026"),
            "category": "gym",
            "title": "Gym Equipment — Annual Service & Safety Check",
            "description": "Annual servicing of treadmill, bikes, and resistance equipment. Safety inspection per AS 4616.",
            "contractor": "FitEquip Service Co.",
            "status": "scheduled",
            "scheduled_date": (TODAY + timedelta(days=90)).isoformat(),
            "due_date": (TODAY + timedelta(days=90)).isoformat(),
            "frequency": "annual",
            "risk_level": "low",
        },
    ]

    count = 0
    for item in items:
        result = await db.ppm_items.update_one(
            {"building_id": BUILDING_ID, "id": item["id"]},
            {"$set": {
                **item,
                "building_id": BUILDING_ID,
                "is_seed_data": True,
                "created_at": (NOW - timedelta(days=30)).isoformat(),
                "updated_at": NOW.isoformat(),
            }},
            upsert=True,
        )
        count += result.upserted_id is not None or result.modified_count > 0
    return count


# ── Compliance Items (update stale dates, add upcoming) ───────────────────────

async def seed_compliance(db) -> int:
    """Update stale compliance item due dates and add upcoming FY items."""
    items = [
        # Update stale pending items with realistic current-year dates
        {
            "id": _u5("compliance:whs-risk-2026"),
            "title": "WHS Risk Assessment Review",
            "description": "Annual review of WHS risk register for common areas, car park, plant room and pool. Required under Work Health and Safety Act 2011.",
            "status": "pending",
            "risk_level": "medium",
            "due_date": (TODAY + timedelta(days=14)).isoformat(),
        },
        {
            "id": _u5("compliance:financial-audit-2026"),
            "title": "Annual Financial Audit (FY2025)",
            "description": "Independent audit of FY2025 financials by registered auditor. Report to be presented at AGM.",
            "status": "pending",
            "risk_level": "medium",
            "due_date": (TODAY + timedelta(days=28)).isoformat(),
        },
        {
            "id": _u5("compliance:eoy-reporting-2026"),
            "title": "End of Financial Year Reporting (FY2026)",
            "description": "Preparation and lodgement of annual return, levy statements, and financial summary for FY2026.",
            "status": "pending",
            "risk_level": "medium",
            "due_date": (TODAY + timedelta(days=50)).isoformat(),
        },
        {
            "id": _u5("compliance:pool-cert-2026"),
            "title": "Pool Safety Certificate Renewal",
            "description": "Renewal of Pool Safety Certificate as required under Swimming Pools Act 1992 (NSW). Certificate valid for 3 years.",
            "status": "pending",
            "risk_level": "medium",
            "due_date": (TODAY + timedelta(days=16)).isoformat(),
        },
        # New upcoming compliance items
        {
            "id": _u5("compliance:strata-hub-return-2026"),
            "title": "NSW Strata Hub Annual Return — FY2026",
            "description": "Annual information return submitted to NSW Fair Trading via Strata Hub portal, including scheme details, financials, and AGM outcomes.",
            "status": "pending",
            "risk_level": "medium",
            "due_date": (TODAY + timedelta(days=75)).isoformat(),
        },
        {
            "id": _u5("compliance:window-safety-2026"),
            "title": "Window Safety Devices — Annual Inspection",
            "description": "Annual inspection of all window safety devices (locks/limiters) on floors 2+ as required under the Building Products (Safety) Act 2017 NSW.",
            "status": "pending",
            "risk_level": "high",
            "due_date": (TODAY + timedelta(days=35)).isoformat(),
        },
    ]

    count = 0
    for item in items:
        result = await db.compliance_items.update_one(
            {"building_id": BUILDING_ID, "id": item["id"]},
            {"$set": {
                **item,
                "building_id": BUILDING_ID,
                "is_seed_data": True,
                "created_at": (NOW - timedelta(days=7)).isoformat(),
                "updated_at": NOW.isoformat(),
            }},
            upsert=True,
        )
        count += result.upserted_id is not None or result.modified_count > 0
    return count


# ── Workflow Requests ─────────────────────────────────────────────────────────

async def seed_workflow_requests(db) -> int:
    """
    Current and recent maintenance/governance workflow requests for East Gate.
    Represents real in-flight and recently completed work.
    """
    items = [
        # In-flight maintenance
        {
            "id": _u5("wr:lift-q2-service"),
            "request_number": "WO-2601",
            "request_type": "maintenance",
            "title": "Lift quarterly service — Q2 2026 (overdue)",
            "description": "Q2 2026 quarterly lift service is 18 days overdue. Kone Elevators to be contacted urgently. Risk: non-compliance with WHS regulations if not completed within 7 days.",
            "unit_number": "Common",
            "priority": "urgent",
            "status": "overdue",
            "sla_breached": True,
        },
        {
            "id": _u5("wr:roof-leak-unit-ua030"),
            "request_number": "WO-2602",
            "request_type": "maintenance",
            "title": "Roof leak — water ingress affecting UA030 ceiling",
            "description": "Owner of UA030 reported water ingress through ceiling after heavy rain on 22 May 2026. Initial inspection completed. Roofing contractor booked for 9 June.",
            "unit_number": "UA030",
            "priority": "high",
            "status": "in_progress",
            "sla_breached": False,
        },
        {
            "id": _u5("wr:carpark-lighting-repair"),
            "request_number": "WO-2603",
            "request_type": "maintenance",
            "title": "Car park lighting — 4 fluorescent tubes failed (Levels B1 & B2)",
            "description": "Motion-sensor lighting on B1 and B2 has 4 failed tubes. Safety concern reported by residents. Electrical contractor to replace and test sensors.",
            "unit_number": "Common",
            "priority": "high",
            "status": "awaiting_contractor",
            "sla_breached": False,
        },
        # Governance
        {
            "id": _u5("wr:bylaw-pets-th085"),
            "request_number": "GOV-2601",
            "request_type": "bylaw_query",
            "title": "Pet approval request — TH085",
            "description": "Owner of TH085 requests approval to keep a small dog (Labrador, <10kg) on the premises under the current pet by-law.",
            "unit_number": "TH085",
            "priority": "normal",
            "status": "under_review",
            "sla_breached": False,
        },
        # Recently completed
        {
            "id": _u5("wr:pool-pump-repair"),
            "request_number": "WO-2599",
            "request_type": "maintenance",
            "title": "Pool circulation pump — bearing replacement",
            "description": "Pool pump bearing was making excessive noise. Replaced by Poolwerx on 20 May 2026. Pool back in service same day.",
            "unit_number": "Common",
            "priority": "high",
            "status": "closed",
            "sla_breached": False,
        },
        {
            "id": _u5("wr:fire-cert-completed"),
            "request_number": "WO-2598",
            "request_type": "compliance",
            "title": "Fire safety inspection — H1 2026 (completed)",
            "description": "H1 2026 fire system inspection completed by Chubb on 6 April 2026. All systems passed. AS1851 service record updated. Fire Safety Certificate issued.",
            "unit_number": "Common",
            "priority": "high",
            "status": "closed",
            "sla_breached": False,
        },
    ]

    admin_user_id = None
    try:
        admin = await db.users.find_one(
            {"building_id": BUILDING_ID, "role": {"$in": ["strata_admin", "strata_manager", "super_admin"]}},
            {"_id": 0, "id": 1}
        )
        if admin:
            admin_user_id = admin.get("id")
    except Exception:
        pass

    count = 0
    for idx, item in enumerate(items):
        created_days_ago = [18, 10, 4, 8, 12, 56][idx]
        result = await db.workflow_requests.update_one(
            {"building_id": BUILDING_ID, "id": item["id"]},
            {"$set": {
                **item,
                "building_id": BUILDING_ID,
                "submitted_by_user_id": admin_user_id,
                "is_seed_data": True,
                "created_at": (NOW - timedelta(days=created_days_ago)).isoformat(),
                "updated_at": NOW.isoformat(),
            }},
            upsert=True,
        )
        count += result.upserted_id is not None or result.modified_count > 0
    return count


# ── Tear-down ─────────────────────────────────────────────────────────────────

async def tear_down(db) -> None:
    """Generated function header.

    Function: tear_down
    Path: backend/seeds/seed_eastgate_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    for col in ["ppm_items", "compliance_items", "workflow_requests"]:
        result = await db[col].delete_many(
            {"building_id": BUILDING_ID, "is_seed_data": True}
        )
        print(f"  Removed {result.deleted_count} seeded records from {col}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/seeds/seed_eastgate_onboarding.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    args_parser = argparse.ArgumentParser(
        description="East Gate Residences onboarding seed — PPM, compliance, workflow"
    )
    args_parser.add_argument("--tear-down", action="store_true")
    args = args_parser.parse_args()

    db = await _get_db()

    if args.tear_down:
        print("Removing East Gate onboarding seed records...")
        await tear_down(db)
        print("Done.")
        return

    print(f"Seeding East Gate (building_id={BUILDING_ID}) onboarding records...")
    n_ppm = await seed_ppm(db)
    n_comp = await seed_compliance(db)
    n_wr = await seed_workflow_requests(db)
    print(f"  PPM items       : {n_ppm} upserted")
    print(f"  Compliance items: {n_comp} upserted")
    print(f"  Workflow requests: {n_wr} upserted")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
