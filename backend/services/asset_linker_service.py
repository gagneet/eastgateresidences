"""
Asset Linker Admin Tool

Logic for semi-automatic mapping of legacy work orders and invoices to assets.
"""

from typing import List, Dict, Any

from database import db


async def suggest_asset_mappings(building_id: str) -> List[Dict[str, Any]]:
    """
    Scans unresolved work orders and suggests asset links based on text matching.
    """
    # 1. Get all assets
    assets = await db.building_assets.find({"building_id": building_id}).to_list(1000)

    # 2. Get unlinked work orders
    unlinked_wos = await db.work_orders.find({
        "building_id": building_id,
        "asset_id": None
    }).to_list(500)

    suggestions = []
    for wo in unlinked_wos:
        title = wo.get("title", "").lower()
        desc = wo.get("description", "").lower()

        # Simple keyword matching
        best_match = None
        match_score = 0

        for asset in assets:
            name = asset["name"].lower()
            # Basic score based on name appearing in title/desc
            score = 0
            if name in title:
                score += 10
            if name in desc:
                score += 5

            if score > match_score:
                match_score = score
                best_match = asset

        if best_match:
            suggestions.append({
                "work_order_id": wo["id"],
                "work_order_title": wo["title"],
                "suggested_asset_id": best_match["id"],
                "suggested_asset_name": best_match["name"],
                "confidence_score": match_score
            })

    return suggestions


async def apply_asset_mappings(mappings: List[Dict[str, str]]):
    """
    Bulk apply confirmed mappings.
    mappings: list of {"work_order_id": "...", "asset_id": "..."}
    """
    for m in mappings:
        wo_id = m["work_order_id"]
        asset_id = m["asset_id"]

        # Get asset to inherit facility/zone/bg
        asset = await db.building_assets.find_one({"id": asset_id})
        if asset:
            await db.work_orders.update_one(
                {"id": wo_id},
                {"$set": {
                    "asset_id": asset_id,
                    "facility_id": asset.get("facility_id"),
                    "zone_id": asset.get("zone_id"),
                    "benefit_group_id": asset.get("benefit_group_id")
                }}
            )

            # Also update linked invoices if any
            await db.invoices.update_many(
                {"work_order_id": wo_id},
                {"$set": {
                    "asset_id": asset_id,
                    "facility_id": asset.get("facility_id"),
                    "benefit_group_id": asset.get("benefit_group_id")
                }}
            )
