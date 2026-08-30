"""
Seed script for common strata asset templates.
"""

try:
    from database import db
except ImportError:
    db = None

ASSET_TEMPLATES = [
    {
        "name": "Lift Motor",
        "category": "vertical_transport",
        "expected_lifespan_years": 25,
        "maintenance_frequency_months": 12,
        "replacement_cost_estimate": 65000
    },
    {
        "name": "Lift Control Panel",
        "category": "vertical_transport",
        "expected_lifespan_years": 15,
        "maintenance_frequency_months": 12,
        "replacement_cost_estimate": 25000
    },
    {
        "name": "Fire Alarm Panel",
        "category": "fire_safety",
        "expected_lifespan_years": 15,
        "maintenance_frequency_months": 6,
        "replacement_cost_estimate": 12000
    },
    {
        "name": "Sprinkler System",
        "category": "fire_safety",
        "expected_lifespan_years": 40,
        "maintenance_frequency_months": 12,
        "replacement_cost_estimate": 150000
    },
    {
        "name": "Garage Door Motor",
        "category": "access_control",
        "expected_lifespan_years": 12,
        "maintenance_frequency_months": 6,
        "replacement_cost_estimate": 4500
    },
    {
        "name": "Ventilation Fan",
        "category": "hvac",
        "expected_lifespan_years": 15,
        "maintenance_frequency_months": 12,
        "replacement_cost_estimate": 8000
    },
    {
        "name": "Roof Membrane",
        "category": "structure",
        "expected_lifespan_years": 25,
        "maintenance_frequency_months": 24,
        "replacement_cost_estimate": 120000
    },
    {
        "name": "Common Lighting System",
        "category": "electrical",
        "expected_lifespan_years": 15,
        "maintenance_frequency_months": 12,
        "replacement_cost_estimate": 35000
    },
    {
        "name": "CCTV System",
        "category": "security",
        "expected_lifespan_years": 10,
        "maintenance_frequency_months": 6,
        "replacement_cost_estimate": 18000
    },
    {
        "name": "Intercom System",
        "category": "security",
        "expected_lifespan_years": 12,
        "maintenance_frequency_months": 12,
        "replacement_cost_estimate": 22000
    },
    {
        "name": "Hot Water Central Plant",
        "category": "plumbing",
        "expected_lifespan_years": 20,
        "maintenance_frequency_months": 12,
        "replacement_cost_estimate": 85000
    }
]


async def seed_asset_templates():
    """Generated function header.

    Function: seed_asset_templates
    Path: backend/seeds/seed_asset_templates.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    if db is None:
        print("Database not available for seeding asset templates.")
        return

    print("Seeding asset templates...")
    for template in ASSET_TEMPLATES:
        await db.asset_templates.update_one(
            {"name": template["name"]},
            {"$set": template},
            upsert=True
        )
    print(f"Seeded {len(ASSET_TEMPLATES)} templates.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(seed_asset_templates())
