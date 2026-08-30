"""
Seed Units with 2026 Levy Data

Parses Excel file EastGate_Units13195_2025_FULL_UPDATED_WITH_2026_UOE_SPLIT_COMPARISON.xlsx
and seeds 87 units with owner names, property details, and 2026 levies.

Usage:
    python3 seeds/units_2026.py
"""

import os
from datetime import datetime, timezone
from pathlib import Path

import asyncio
import pandas as pd
from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from utils.unit_number import format_unit_display

# A SEED DECLARES the building's layout, so it is the one place a prefix rule may be
# written down — but it must still APPLY that rule through the shared formatter, never
# by gluing a prefix onto a number inline. Anything else drifts from
# db.settings type="unit_display", which is what every reader resolves against.
# See utils/unit_number.py and tests/backend/test_unit_identity_single_source.py.
_UNIT_DISPLAY_RULES = [
    {"prefix": "UA", "min": 1, "max": 70, "pad": 3},
    {"prefix": "TH", "min": 71, "max": 87, "pad": 3},
]


ROOT_DIR = Path(__file__).parent.parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncMongoClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Excel file path
EXCEL_PATH = ROOT_DIR.parent / 'EastGate_Units13195_2025_FULL_UPDATED_WITH_2026_UOE_SPLIT_COMPARISON.xlsx'


async def seed_units_2026():
    """Seed units with 2026 levy data from Excel."""
    print("Reading Excel file...")

    if not EXCEL_PATH.exists():
        print(f"❌ Excel file not found: {EXCEL_PATH}")
        return

    # Read Excel file
    df = pd.read_excel(EXCEL_PATH)
    print(f"✓ Loaded {len(df)} units from Excel")

    # Clear existing units
    result = await db.units.delete_many({})
    print(f"✓ Cleared {result.deleted_count} existing units")

    # Track unit numbers by type for sequential numbering
    apartment_counter = 1
    townhouse_counter = 1

    units_data = []
    for _, row in df.iterrows():
        lot = int(row['Lot'])

        # Property type mapping
        prop_type = row['Property Type'].lower()
        if prop_type == 'apartment':
            unit_type = 'apartment'
            unit_number = format_unit_display(apartment_counter, _UNIT_DISPLAY_RULES)
            apartment_counter += 1
        else:
            unit_type = 'townhouse'
            unit_number = format_unit_display(townhouse_counter, _UNIT_DISPLAY_RULES)
            townhouse_counter += 1

        # Calculate quarterly levies (annual / 4)
        admin_annual = float(row['2026 Admin Annual'])
        sinking_annual = float(row['2026 Sinking Annual'])
        admin_quarterly = round(admin_annual / 4, 2)
        sinking_quarterly = round(sinking_annual / 4, 2)

        # Parse bedrooms (handle ranges like '3-4')
        def parse_int_or_range(value):
            """Parse integer or range like '3-4', returning first number."""
            if pd.notna(value):
                value_str = str(value)
                if '-' in value_str:
                    return int(value_str.split('-')[0])
                else:
                    return int(float(value_str))  # Handle float strings like '2.0'
            return None

        bedrooms = parse_int_or_range(row['Bedrooms'])
        bathrooms = parse_int_or_range(row['Bathrooms'])
        car_spaces = parse_int_or_range(row['Garages'])

        unit_doc = {
            'lot_number': f'LOT{lot}',
            'unit_number': unit_number,  # Already formatted as UA001-UA070 or TH071-TH087
            'unit_type': unit_type,
            'owner_name': str(row['Owner Name']) if pd.notna(row['Owner Name']) else None,
            'entitlement': float(row['UOE']),
            'bedrooms': bedrooms,
            'bathrooms': bathrooms,
            'car_spaces': car_spaces,

            # Quarterly breakdown (from Excel Q1-Q4 columns)
            'q1_total': float(row['2026 Q1 Total']),
            'q2_total': float(row['2026 Q2 Total']),
            'q3_total': float(row['2026 Q3 Total']),
            'q4_total': float(row['2026 Q4 Total']),

            # 2025 comparison data (for reference)
            'admin_levy_2025': float(row['2025 Admin Levied']) if pd.notna(row['2025 Admin Levied']) else None,
            'sinking_levy_2025': float(row['2025 Sinking Levied']) if pd.notna(row['2025 Sinking Levied']) else None,
            'levy_increase_2026': float(row['Increase ($)']) if pd.notna(row['Increase ($)']) else None,
            'levy_increase_percent_2026': float(row['Increase (%)']) if pd.notna(row['Increase (%)']) else None,

            'created_at': datetime.now(timezone.utc),
            'updated_at': datetime.now(timezone.utc)
        }

        units_data.append(unit_doc)

    # Insert all units
    if units_data:
        result = await db.units.insert_many(units_data)
        print(f"✓ Inserted {len(result.inserted_ids)} units")

        # Summary statistics — levy totals derived from entitlement × rates, not stored fields
        total_uoe = sum(u['entitlement'] for u in units_data)
        # admin_annual and sinking_annual are loop-local vars; recompute from q1-q4 totals if needed
        total_annual = sum(
            u.get('q1_total', 0) + u.get('q2_total', 0) + u.get('q3_total', 0) + u.get('q4_total', 0) for u in
            units_data)

        apartments = [u for u in units_data if u['unit_type'] == 'apartment']
        townhouses = [u for u in units_data if u['unit_type'] == 'townhouse']

        print(f"\n=== 2026 Levy Summary ===")
        print(f"Total Units: {len(units_data)}")
        print(f"  - Apartments: {len(apartments)}")
        print(f"  - Townhouses: {len(townhouses)}")
        print(f"Total UOE: {total_uoe}")
        print(f"Total Annual Levies (2026 Q totals): ${total_annual:,.2f}")
        print(f"Average Quarterly Levy: ${total_annual / 4 / len(units_data):,.2f}")
    else:
        print("❌ No units data to insert")


async def main():
    """Main entry point."""
    try:
        await seed_units_2026()
        print("\n✅ Units seeded successfully with 2026 levy data!")
    except Exception as e:
        print(f"\n❌ Error seeding units: {e}")
        import traceback
        traceback.print_exc()
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
