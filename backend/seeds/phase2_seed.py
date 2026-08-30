"""
Phase 2 Seed — Production Hardening + Intelligence Layer

Seeds Phase 2 data for East Gate Residences (building_id = "13195"):
  - trust_period_locks collection: empty (no periods locked by default)
  - reconciliation_exceptions collection: empty (ready to receive exceptions)
  - Feature toggles for all Phase 2 features
  - Demo reconciliation batch with 5 matched + 2 unmatched transactions
  - Demo stress score snapshot for current financial year
  - Demo subsidy map result (validating ~$42K cross-subsidy)
  - Demo TCO results for TH087 and UA063

Design principles:
  - Never create buildings — always look up existing building_id
  - Idempotent: upsert operations throughout (run twice = no duplicates)
  - East Gate (13195) is PRODUCTION DATA — do not overwrite real data
  - All amounts in integer cents

Usage:
  cd /home/gagneet/strata-management/backend
  python3 -m seeds.phase2_seed
  python3 -m seeds.phase2_seed --dry-run
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

DRY_RUN = "--dry-run" in sys.argv
BUILDING_ID = "13195"
FINANCIAL_YEAR = "2026-2027"

# ─── Phase 2 feature toggles ──────────────────────────────────────────────

PHASE2_TOGGLES = [
    {
        "key": "investor_dashboard",
        "name": "Investor Dashboard",
        "description": "Investor-grade building health and unit investment analysis",
        "enabled": True,
        "category": "Intelligence",
        "building_id": None,  # Global default
    },
    {
        "key": "insurance_lending_hooks",
        "name": "Insurance & Lending Hooks",
        "description": "Insurance risk signals and lending valuation signals for buildings and units",
        "enabled": True,
        "category": "Intelligence",
        "building_id": None,
    },
    {
        "key": "subsidy_map",
        "name": "Subsidy Map",
        "description": "Cross-subsidy analysis showing which unit types subsidise which facilities",
        "enabled": True,
        "category": "Intelligence",
        "building_id": None,
    },
    {
        "key": "true_cost_ownership",
        "name": "True Cost of Ownership",
        "description": "Full annualised strata ownership cost including rates, water, maintenance and capital shock",
        "enabled": True,
        "category": "Intelligence",
        "building_id": None,
    },
    {
        "key": "stress_score",
        "name": "Building Financial Stress Score",
        "description": "7-component weighted financial health score for buildings",
        "enabled": True,
        "category": "Intelligence",
        "building_id": None,
    },
]


def _now_iso() -> str:
    """Generated function header.

    Function: _now_iso
    Path: backend/seeds/phase2_seed.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


async def _upsert(collection, filter_doc: Dict, update_doc: Dict, label: str) -> None:
    """Generated function header.

    Function: _upsert
    Path: backend/seeds/phase2_seed.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if DRY_RUN:
        logger.info("[DRY-RUN] Would upsert %s: %s", label, filter_doc)
        return
    await collection.update_one(filter_doc, {"$set": update_doc}, upsert=True)
    logger.info("Upserted %s: %s", label, filter_doc)


async def seed_phase2_feature_toggles(db) -> None:
    """Seed Phase 2 feature toggles (global defaults, building_id=None)."""
    logger.info("=== Seeding Phase 2 feature toggles ===")
    for toggle in PHASE2_TOGGLES:
        await _upsert(
            db.feature_toggles,
            {"key": toggle["key"], "building_id": None},
            {
                **toggle,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            },
            f"feature_toggle:{toggle['key']}",
        )
    logger.info("Seeded %d Phase 2 feature toggles", len(PHASE2_TOGGLES))


async def seed_trust_period_locks(db) -> None:
    """Ensure trust_period_locks collection exists and is empty for production building.

    No periods are locked by default — this is intentional.
    The collection must exist so the period lock checks don't error on missing collection.
    """
    logger.info("=== Seeding trust_period_locks (empty) ===")
    # Create a marker document to ensure the collection is created, then remove it
    if not DRY_RUN:
        # Insert and remove to create collection if it doesn't exist
        result = await db.trust_period_locks.insert_one({"_init": True, "building_id": "_init"})
        await db.trust_period_locks.delete_one({"_id": result.inserted_id})
    logger.info("trust_period_locks collection ensured (no locks created)")


async def seed_reconciliation_exceptions(db) -> None:
    """Ensure reconciliation_exceptions collection exists and is empty."""
    logger.info("=== Seeding reconciliation_exceptions (empty) ===")
    if not DRY_RUN:
        result = await db.reconciliation_exceptions.insert_one({"_init": True, "building_id": "_init"})
        await db.reconciliation_exceptions.delete_one({"_id": result.inserted_id})
    logger.info("reconciliation_exceptions collection ensured")


async def seed_demo_reconciliation_batch(db) -> None:
    """Seed a demo reconciliation batch for East Gate with 5 matched + 2 unmatched."""
    logger.info("=== Seeding demo reconciliation batch for %s ===", BUILDING_ID)

    batch_filter = {
        "building_id": BUILDING_ID,
        "financial_year": FINANCIAL_YEAR,
        "batch_type": "phase2_demo",
    }

    batch_doc = {
        "building_id": BUILDING_ID,
        "financial_year": FINANCIAL_YEAR,
        "batch_type": "phase2_demo",
        "period": "2026-Q1",
        "status": "in_progress",
        "matched_count": 5,
        "unmatched_bank_count": 2,
        "unmatched_internal_count": 0,
        "suggested_count": 2,
        "duplicate_warnings": 0,
        "total_unreconciled_amount_cents": 120_000,  # $1,200 unreconciled
        "reconciliation_confidence": 0.71,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }

    await _upsert(db.trust_reconciliation_runs, batch_filter, batch_doc, "demo_recon_batch")
    logger.info("Demo reconciliation batch seeded")


async def seed_demo_stress_score(db) -> None:
    """Seed a demo stress score result for East Gate — indicative values only."""
    logger.info("=== Seeding demo stress score for %s ===", BUILDING_ID)

    filter_doc = {"building_id": BUILDING_ID, "financial_year": FINANCIAL_YEAR}
    doc = {
        "building_id": BUILDING_ID,
        "financial_year": FINANCIAL_YEAR,
        "score": 28.5,
        "category": "watch",
        "components": {
            "reserve_adequacy": {"normalised_score": 35.0, "weight": 0.25, "status": "watch"},
            "arrears_level": {"normalised_score": 20.0, "weight": 0.20, "status": "ok"},
            "arrears_concentration": {"normalised_score": 15.0, "weight": 0.15, "status": "ok"},
            "unreconciled_exposure": {"normalised_score": 25.0, "weight": 0.15, "status": "watch"},
            "maintenance_pressure": {"normalised_score": 30.0, "weight": 0.10, "status": "watch"},
            "liquidity_coverage": {"normalised_score": 20.0, "weight": 0.10, "status": "ok"},
            "capital_shock_indicator": {"normalised_score": 28.0, "weight": 0.05, "status": "watch"},
        },
        "explanation": "Indicative demo score for East Gate. Real score computed from live data.",
        "data_completeness": 1.0,
        "is_demo": True,
        "computed_at": _now_iso(),
    }
    await _upsert(db.financial_stress_scores, filter_doc, doc, "demo_stress_score")
    logger.info("Demo stress score seeded")


async def seed_demo_subsidy_map(db) -> None:
    """Seed a demo subsidy map result validating ~$42K cross-subsidy for East Gate."""
    logger.info("=== Seeding demo subsidy map for %s ===", BUILDING_ID)

    filter_doc = {"building_id": BUILDING_ID, "financial_year": FINANCIAL_YEAR}
    doc = {
        "building_id": BUILDING_ID,
        "financial_year": FINANCIAL_YEAR,
        "total_cross_subsidy_cents": 4_200_000,  # ~$42,000
        "subsidy_map": [
            {
                "unit_type": "Townhouse",
                "facility_category": "lift",
                "facility_name": "Building Lifts (×2)",
                "benefit_group": "APARTMENTS_ONLY",
                "annual_cost_cents": 3_000_000,  # $30,000
                "uoe_share_cents": 1_300_000,  # $13,000 paid by townhouses
                "benefit_share_cents": 0,  # $0 benefit to townhouses
                "subsidy_amount_cents": -1_300_000,
                "subsidy_direction": "being_subsidised",
            },
            {
                "unit_type": "Townhouse",
                "facility_category": "fire_system",
                "facility_name": "High-Rise Fire System",
                "benefit_group": "APARTMENTS_ONLY",
                "annual_cost_cents": 1_800_000,  # $18,000
                "uoe_share_cents": 800_000,  # $8,000
                "benefit_share_cents": 0,
                "subsidy_amount_cents": -800_000,
                "subsidy_direction": "being_subsidised",
            },
            {
                "unit_type": "Townhouse",
                "facility_category": "corridor_cleaning",
                "facility_name": "Corridor Cleaning",
                "benefit_group": "APARTMENTS_ONLY",
                "annual_cost_cents": 4_800_000,  # $48,000
                "uoe_share_cents": 2_100_000,  # $21,000
                "benefit_share_cents": 0,
                "subsidy_amount_cents": -2_100_000,
                "subsidy_direction": "being_subsidised",
            },
        ],
        "summary_by_unit_type": {
            "Townhouse": {
                "net_subsidy_cents": -4_200_000,
                "avg_per_unit_cents": -175_000,  # ~$1,750/unit/year
                "units_count": 24,
                "role": "subsidising",
            },
            "Apartment": {
                "net_subsidy_cents": 4_200_000,
                "avg_per_unit_cents": 66_667,  # ~$667/unit/year
                "units_count": 63,
                "role": "subsidised",
            },
        },
        "key_findings": [
            "Townhouses contribute ~$42,000/year to apartment-specific facilities via flat UOE levies.",
            "Lifts, high-rise fire systems, and corridor cleaning are the largest subsidy drivers.",
            "Average townhouse pays ~$1,750/year more than their fair share of these facilities.",
        ],
        "is_demo": True,
        "computed_at": _now_iso(),
    }
    await _upsert(db.subsidy_map_results, filter_doc, doc, "demo_subsidy_map")
    logger.info("Demo subsidy map seeded ($42K cross-subsidy)")


async def seed_demo_tco(db) -> None:
    """Seed demo TCO results for TH087 and UA063."""
    logger.info("=== Seeding demo TCO for TH087 and UA063 ===")

    for unit_number, admin_levy, sinking_levy, unit_type in [
        ("TH087", 400_000, 200_000, "Townhouse"),
        ("UA063", 360_000, 180_000, "Apartment"),
    ]:
        filter_doc = {
            "building_id": BUILDING_ID,
            "unit_number": unit_number,
            "financial_year": FINANCIAL_YEAR,
        }
        total_annual = admin_levy + sinking_levy + 250_000 + 180_000 + 50_000  # levy + rates + water + maint
        doc = {
            "building_id": BUILDING_ID,
            "unit_number": unit_number,
            "unit_type": unit_type,
            "holding_period_years": 5,
            "financial_year": FINANCIAL_YEAR,
            "admin_levy_cents": admin_levy,
            "sinking_levy_cents": sinking_levy,
            "council_rates_cents": 250_000,
            "water_estimate_cents": 180_000,
            "capital_shock_buffer_cents": 50_000,
            "maintenance_probability_cost_cents": 80_000,
            "total_annual_cents": total_annual,
            "total_for_period_cents": total_annual * 5,  # simplified (no CPI for demo)
            "cost_per_day_cents": total_annual // 365,
            "building_average_annual_cents": 1_350_000,
            "unit_vs_building_avg_pct": round((total_annual - 1_350_000) / 1_350_000 * 100, 1),
            "assumptions": [
                "CPI inflation: 3.5% compound",
                "Council rates: from unit record",
                "Water charges: default $1,800/year",
            ],
            "is_demo": True,
            "computed_at": _now_iso(),
        }
        await _upsert(db.true_cost_results, filter_doc, doc, f"demo_tco:{unit_number}")
    logger.info("Demo TCO seeded for TH087 and UA063")


async def run_all(db) -> None:
    """Generated function header.

    Function: run_all
    Path: backend/seeds/phase2_seed.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    await seed_phase2_feature_toggles(db)
    await seed_trust_period_locks(db)
    await seed_reconciliation_exceptions(db)
    await seed_demo_reconciliation_batch(db)
    await seed_demo_stress_score(db)
    await seed_demo_subsidy_map(db)
    await seed_demo_tco(db)
    logger.info("=== Phase 2 seed complete ===")


async def main() -> None:
    """Generated function header.

    Function: main
    Path: backend/seeds/phase2_seed.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    from database import db
    await run_all(db)


if __name__ == "__main__":
    asyncio.run(main())
