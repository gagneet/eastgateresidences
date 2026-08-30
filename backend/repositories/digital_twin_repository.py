"""
Digital Twin Repository — MongoDB management for physical assets and facilities.

Responsibilities:
- Index creation for Digital Twin collections.
- CRUD helper operations for BuildingAsset, Facility, Zone, and BenefitGroup.
- Aggregating intelligence data for precomputed layers.
"""

from datetime import datetime, timezone

from typing import List, Dict, Any

from database import db


def _now() -> str:
    """Generated function header.

    Function: _now
    Path: backend/repositories/digital_twin_repository.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    return datetime.now(timezone.utc).isoformat()


async def ensure_digital_twin_indexes():
    """Create indexes for digital twin collections."""
    await db.building_assets.create_index([("building_id", 1), ("id", 1)], unique=True)
    await db.building_assets.create_index([("facility_id", 1)])
    await db.building_assets.create_index([("zone_id", 1)])

    await db.facilities.create_index([("building_id", 1), ("id", 1)], unique=True)
    await db.zones.create_index([("building_id", 1), ("id", 1)], unique=True)
    await db.benefit_groups.create_index([("building_id", 1), ("id", 1)], unique=True)

    # Intelligence storage indexes
    await db.maintenance_forecasts.create_index([("building_id", 1), ("year", 1)], unique=True)
    await db.asset_health_scores.create_index([("asset_id", 1), ("building_id", 1)], unique=True)
    await db.intelligence_summary.create_index([("building_id", 1)], unique=True)
    await db.locks.create_index("key", unique=True)
    await db.maintenance_anomalies.create_index([("building_id", 1), ("asset_id", 1)])
    await db.levy_fairness_results.create_index([("building_id", 1)], unique=True)
    await db.capital_shock_risks.create_index([("building_id", 1)], unique=True)


# Helper functions for maintenance intelligence

async def upsert_intelligence_summary(summary: Dict[str, Any]):
    """Upsert building-wide intelligence summary."""
    summary["updated_at"] = _now()
    await db.intelligence_summary.update_one(
        {"building_id": summary["building_id"]},
        {"$set": summary},
        upsert=True
    )


async def upsert_asset_health_score(score_data: Dict[str, Any]):
    """Store/Update latest asset health metric."""
    score_data["updated_at"] = _now()
    await db.asset_health_scores.update_one(
        {"asset_id": score_data["asset_id"], "building_id": score_data["building_id"]},
        {"$set": score_data},
        upsert=True
    )


async def upsert_maintenance_forecast(forecast: Dict[str, Any]):
    """Store 12-month maintenance cost forecast."""
    forecast["updated_at"] = _now()
    await db.maintenance_forecasts.update_one(
        {"building_id": forecast["building_id"], "year": forecast["year"]},
        {"$set": forecast},
        upsert=True
    )


# Benefit-group attribution on a capital row decides which lots fund it (see
# services/levy_fairness_service.py). Callers that only know about costs and years
# -- the Capital Works Planner PUT, and the maintenance forecast regeneration --
# do not carry these, so they are re-attached from the row they replace rather
# than dropped. Without this, every save flattens the whole plan to ALL_LOTS.
_CAPITAL_TAG_FIELDS = ("benefit_group_id", "facility_id")


def _capital_row_key(row: Dict[str, Any]) -> tuple:
    """Identity of a capital row across a replace, for carrying tags forward.

    Prefers asset_id, which survives a rename; falls back to the asset name so
    manually-entered rows (which have no asset_id) still match.
    """
    year = row.get("replacement_year")
    asset_id = row.get("asset_id")
    if asset_id:
        return ("asset_id", str(asset_id), year)
    return ("asset_name", str(row.get("asset_name") or "").strip().lower(), year)


async def update_capital_schedule(building_id: str, schedule: List[Dict[str, Any]]):
    """Replace the capital schedule for a building, preserving benefit-group tags.

    A tag is inherited only when the incoming row leaves the field absent or
    None. To deliberately clear one, send an empty string.
    """
    existing = await db.capital_replacement_schedule.find(
        {"building_id": building_id}, {"_id": 0}
    ).to_list(5000)

    prior_tags: Dict[tuple, Dict[str, Any]] = {}
    for row in existing:
        tags = {f: row[f] for f in _CAPITAL_TAG_FIELDS if row.get(f)}
        if tags:
            prior_tags.setdefault(_capital_row_key(row), {}).update(tags)

    await db.capital_replacement_schedule.delete_many({"building_id": building_id})
    if schedule:
        for s in schedule:
            for field, value in prior_tags.get(_capital_row_key(s), {}).items():
                if s.get(field) is None:
                    s[field] = value
            s["updated_at"] = _now()
        await db.capital_replacement_schedule.insert_many(schedule)
