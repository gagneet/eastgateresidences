"""
Insurance seed data — demo policies, brokers, and a sample claim for each building.

Safe to run multiple times (upserts on policy_number + building_id).
East Gate (13195) gets realistic policies matching a 64-unit ACT building profile.
Sierra (16244) and Harbourview (18932) get lighter demo data.
"""
import asyncio
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _today() -> str:
    """Generated function header.

    Function: _today
    Path: backend/seeds/insurance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return date.today().isoformat()


def _expiry(months: int) -> str:
    """Generated function header.

    Function: _expiry
    Path: backend/seeds/insurance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    d = date.today() + timedelta(days=months * 30)
    return d.isoformat()


BROKERS = {
    "13195": {
        "id": "broker-chu-eastgate",
        "building_id": "13195",
        "broker_name": "Sharon Mitchell",
        "company_name": "Mitchell Strata Insurance Brokers",
        "licence_number": "IBL000456",
        "afsl_number": "234567",
        "email": "sharon@mitchellstrata.com.au",
        "phone": "02 6100 4567",
        "commission_disclosure": "Sharon Mitchell receives a 12% commission on building insurance premiums "
                                 "and 10% on public liability premiums from CHU Underwriting Agencies.",
        "is_active": True,
    },
    "16244": {
        "id": "broker-sci-sierra",
        "building_id": "16244",
        "broker_name": "James Okafor",
        "company_name": "Capital Region Strata Brokers",
        "licence_number": "IBL000789",
        "afsl_number": "345678",
        "email": "james@capitalstrata.com.au",
        "phone": "02 6100 7890",
        "commission_disclosure": "James Okafor receives a 12% commission from SCI Strata Community Insurance.",
        "is_active": True,
    },
    "18932": {
        "id": "broker-generic-harbourview",
        "building_id": "18932",
        "broker_name": "Demo Broker",
        "company_name": "Demo Insurance Services",
        "licence_number": "IBL000001",
        "afsl_number": "111111",
        "email": "demo@demoinsurance.com.au",
        "phone": "02 6100 0001",
        "commission_disclosure": "Demo commission disclosure.",
        "is_active": True,
    },
}

POLICIES = [
    # ── East Gate (13195) ────────────────────────────────────────────────────
    {
        "building_id": "13195",
        "policy_type": "building",
        "insurer_name": "CHU Underwriting Agencies",
        "policy_number": "CHU-2025-EGR-0012",
        "cover_amount": 18_500_000.00,
        "premium_annual": 42_800.00,
        "excess_amount": 5_000.00,
        "start_date": "2025-07-01",
        "expiry_date": _expiry(14),  # renews ~14 months out
        "broker_name": "Sharon Mitchell",
        "commission_percent": 12.0,
        "commission_amount": 5136.00,
        "connected_insurer": False,
        "quotes": [
            {"insurer_name": "CHU", "premium_amount": 42800, "cover_amount": 18_500_000, "recommended": True},
            {"insurer_name": "SCI", "premium_amount": 45200, "cover_amount": 18_500_000, "recommended": False},
            {"insurer_name": "AON", "premium_amount": 46100, "cover_amount": 18_000_000, "recommended": False},
        ],
        "status": "active",
        "compliance_status": "compliant",
    },
    {
        "building_id": "13195",
        "policy_type": "public_liability",
        "insurer_name": "CHU Underwriting Agencies",
        "policy_number": "CHU-2025-EGR-0013",
        "cover_amount": 20_000_000.00,
        "public_liability_amount": 20_000_000.00,
        "premium_annual": 4_200.00,
        "excess_amount": 1_000.00,
        "start_date": "2025-07-01",
        "expiry_date": _expiry(14),
        "broker_name": "Sharon Mitchell",
        "commission_percent": 10.0,
        "commission_amount": 420.00,
        "connected_insurer": False,
        "quotes": [
            {"insurer_name": "CHU", "premium_amount": 4200, "cover_amount": 20_000_000, "recommended": True},
            {"insurer_name": "SCI", "premium_amount": 4500, "cover_amount": 20_000_000, "recommended": False},
            {"insurer_name": "QBE", "premium_amount": 4350, "cover_amount": 20_000_000, "recommended": False},
        ],
        "status": "active",
        "compliance_status": "compliant",
    },
    {
        "building_id": "13195",
        "policy_type": "voluntary_workers",
        "insurer_name": "CHU Underwriting Agencies",
        "policy_number": "CHU-2025-EGR-0014",
        "cover_amount": 1_000_000.00,
        "premium_annual": 380.00,
        "excess_amount": 0.00,
        "start_date": "2025-07-01",
        "expiry_date": _expiry(14),
        "broker_name": "Sharon Mitchell",
        "commission_percent": 12.0,
        "commission_amount": 45.60,
        "connected_insurer": False,
        "quotes": [],
        "status": "active",
        "compliance_status": "compliant",
    },
    # ── Sierra (16244) ───────────────────────────────────────────────────────
    {
        "building_id": "16244",
        "policy_type": "building",
        "insurer_name": "SCI Strata Community Insurance",
        "policy_number": "SCI-2025-SRR-0087",
        "cover_amount": 12_000_000.00,
        "premium_annual": 28_500.00,
        "excess_amount": 5_000.00,
        "start_date": "2025-06-01",
        "expiry_date": _expiry(13),
        "broker_name": "James Okafor",
        "commission_percent": 12.0,
        "commission_amount": 3420.00,
        "connected_insurer": False,
        "quotes": [
            {"insurer_name": "SCI", "premium_amount": 28500, "cover_amount": 12_000_000, "recommended": True},
            {"insurer_name": "CHU", "premium_amount": 29800, "cover_amount": 12_000_000, "recommended": False},
            {"insurer_name": "QBE", "premium_amount": 30100, "cover_amount": 11_500_000, "recommended": False},
        ],
        "status": "active",
        "compliance_status": "compliant",
    },
    {
        "building_id": "16244",
        "policy_type": "public_liability",
        "insurer_name": "SCI Strata Community Insurance",
        "policy_number": "SCI-2025-SRR-0088",
        "cover_amount": 10_000_000.00,
        "public_liability_amount": 10_000_000.00,
        "premium_annual": 2_900.00,
        "excess_amount": 1_000.00,
        "start_date": "2025-06-01",
        "expiry_date": _expiry(13),
        "broker_name": "James Okafor",
        "commission_percent": 12.0,
        "commission_amount": 348.00,
        "connected_insurer": False,
        "quotes": [
            {"insurer_name": "SCI", "premium_amount": 2900, "cover_amount": 10_000_000, "recommended": True},
            {"insurer_name": "CHU", "premium_amount": 3100, "cover_amount": 10_000_000, "recommended": False},
            {"insurer_name": "QBE", "premium_amount": 3050, "cover_amount": 10_000_000, "recommended": False},
        ],
        "status": "active",
        "compliance_status": "compliant",
    },
    # ── Harbourview (18932) ──────────────────────────────────────────────────
]

SAMPLE_CLAIMS = [
    {
        "building_id": "13195",
        "id": "claim-eastgate-001",
        "claim_number": "CHU-CLM-2025-00341",
        "insurer": "CHU Underwriting Agencies",
        "policy_id": None,
        "incident_date": "2025-09-14",
        "description": "Water damage to Unit 12 ceiling caused by burst pipe in Unit 24 above. "
                       "Damage estimated at $8,400 including drying, plaster, and paint.",
        "location_description": "Unit 12 bedroom ceiling / Unit 24 bathroom",
        "estimated_cost_cents": 840000,
        "excess_cents": 500000,
        "status": "in_progress",
        "timeline": [
            {
                "milestone": "Incident reported to strata manager",
                "occurred_at": "2025-09-14T10:30:00+10:00",
                "recorded_by": "system",
                "notes": "Owner of Unit 12 reported ceiling damage.",
            },
            {
                "milestone": "Claim lodged with CHU",
                "occurred_at": "2025-09-15T09:00:00+10:00",
                "recorded_by": "system",
                "notes": "Claim number CHU-CLM-2025-00341 issued.",
            },
            {
                "milestone": "Loss assessor appointed — Jacobs & Partners",
                "occurred_at": "2025-09-18T14:00:00+10:00",
                "recorded_by": "system",
                "notes": "Inspection scheduled for 22 September.",
            },
        ],
        "linked_work_orders": [],
        "attachments": [],
        "reserve_amount_cents": 900000,
        "assessor_name": "Ben Jacobs",
        "assessor_contact": "ben@jacobspartners.com.au",
        "is_test_data": False,
    },
]


async def seed_insurance():
    """Generated function header.

    Function: seed_insurance
    Path: backend/seeds/insurance.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    from request_context import set_ctx_building_id
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    created_brokers, created_policies, created_claims = 0, 0, 0

    for building_id, broker in BROKERS.items():
        set_ctx_building_id(building_id)
        b = {**broker, "created_at": now, "updated_at": now, "created_by": "seed"}
        res = await db.insurance_brokers.update_one(
            {"id": broker["id"], "building_id": building_id},
            {"$setOnInsert": b},
            upsert=True,
        )
        if res.upserted_id:
            created_brokers += 1

    for policy in POLICIES:
        building_id = policy["building_id"]
        set_ctx_building_id(building_id)
        doc = {
            **policy,
            "quotes_count": len(policy.get("quotes", [])),
            "days_until_expiry": None,
            "created_at": now,
            "updated_at": now,
            "created_by": "seed",
            "renewal_reminder_days": 60,
        }
        res = await db.insurance_policies.update_one(
            {"policy_number": policy["policy_number"], "building_id": building_id},
            {"$setOnInsert": doc},
            upsert=True,
        )
        if res.upserted_id:
            created_policies += 1

    for claim in SAMPLE_CLAIMS:
        building_id = claim["building_id"]
        set_ctx_building_id(building_id)
        doc = {**claim, "created_at": now, "updated_at": now, "created_by": "seed"}
        res = await db.insurance_claims.update_one(
            {"id": claim["id"], "building_id": building_id},
            {"$setOnInsert": doc},
            upsert=True,
        )
        if res.upserted_id:
            created_claims += 1

    print(f"Insurance seed: {created_brokers} brokers, {created_policies} policies, {created_claims} claims created.")


if __name__ == "__main__":
    asyncio.run(seed_insurance())
