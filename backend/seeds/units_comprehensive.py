"""
Comprehensive Unit Seed Data - Combines owner names and financial data
"""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# Excel file paths
OWNERS_FILE = Path(__file__).parent.parent.parent / 'scripts/finances/EastGate_Units13195_2025_FULL.xlsx'
FINANCE_FILE = Path(__file__).parent.parent.parent / 'scripts/finances/EastGate_Units13195_2026_with_UOE.xlsx'


def get_comprehensive_units():
    """
    Load units with owner names and financial data from Excel files
    """
    units = []

    try:
        # Load owner names
        owners_df = pd.read_excel(OWNERS_FILE)
        print(f"✓ Loaded {len(owners_df)} owners from Excel")

        # Load financial data
        finance_df = pd.read_excel(FINANCE_FILE)
        print(f"✓ Loaded {len(finance_df)} units from finance Excel")

        # Merge data on Lot/Unit
        merged = owners_df.merge(finance_df, on=['Lot', 'Unit'], how='outer')

        for _, row in merged.iterrows():
            # Skip rows with non-numeric lot/unit (notes, headers, etc.)
            try:
                lot = int(float(row['Lot'])) if pd.notna(row['Lot']) else 0
                unit_num = int(float(row['Unit'])) if pd.notna(row['Unit']) else 0
            except (ValueError, TypeError):
                continue

            if lot == 0 or unit_num == 0 or lot > 87:
                continue

            # Determine unit type (apartments 1-68, townhouses 69-87)
            unit_type = 'apartment' if lot <= 68 else 'townhouse'

            # Get financial data
            uoe = float(row['UOE']) if pd.notna(row.get('UOE')) else 110.0
            annual_2026 = float(row['2026 Proposed Total Annual']) if pd.notna(
                row.get('2026 Proposed Total Annual')) else 0.0
            quarterly = round(annual_2026 / 4, 2)

            # Estimate bedrooms/bathrooms based on UOE and unit type
            if unit_type == 'apartment':
                if uoe < 100:
                    bedrooms, bathrooms = 1, 1
                elif uoe < 130:
                    bedrooms, bathrooms = 2, 1
                else:
                    bedrooms, bathrooms = 2, 2
                car_spaces = 1
            else:  # townhouse
                bedrooms, bathrooms = 3, 2
                car_spaces = 2

            unit_doc = {
                'id': str(uuid.uuid4()),
                'lot_number': f'LOT{lot}',
                'unit_number': f'U{unit_num:03d}',
                'unit_type': unit_type,
                'owner_name': str(row['Owner Name']) if pd.notna(row.get('Owner Name')) else None,
                'owner_email': None,
                'entitlement': uoe,
                'bedrooms': bedrooms,
                'bathrooms': bathrooms,
                'car_spaces': car_spaces,
                'balance_owing': 0.0,
                'balance_credit': 0.0,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat()
            }

            units.append(unit_doc)

        print(f"✓ Created {len(units)} comprehensive unit records")
        return units

    except Exception as e:
        print(f"⚠️  Error loading Excel files: {e}")
        print("   Using fallback unit data")
        return get_fallback_units()


def get_fallback_units():
    """Fallback unit data if Excel files not available"""
    units = []

    for i in range(1, 88):
        lot_num = f"LOT{i}"
        unit_num = f"U{i:03d}"

        # Determine unit type
        if i <= 68:
            unit_type = 'apartment'
            entitlement = 110.0
            admin_quarterly = 945.0
            sinking_quarterly = 275.0
            bedrooms, bathrooms, car_spaces = 2, 1, 1
        else:
            unit_type = 'townhouse'
            entitlement = 145.0
            admin_quarterly = 1240.0
            sinking_quarterly = 362.0
            bedrooms, bathrooms, car_spaces = 3, 2, 2

        units.append({
            'id': str(uuid.uuid4()),
            'lot_number': lot_num,
            'unit_number': unit_num,
            'unit_type': unit_type,
            'owner_name': None,
            'owner_email': None,
            'entitlement': entitlement,
            'bedrooms': bedrooms,
            'bathrooms': bathrooms,
            'car_spaces': car_spaces,
            'balance_owing': 0.0,
            'balance_credit': 0.0,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        })

    return units


if __name__ == '__main__':
    """
    Standalone script to reseed units with comprehensive data
    Run with: cd backend && python3 seeds/units_comprehensive.py
    """
    import asyncio
    import sys
    from pathlib import Path

    # Add parent directory to path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from pymongo import AsyncMongoClient
    from dotenv import load_dotenv
    import os

    load_dotenv()


    async def seed():
        # Connect to database
        """Generated function header.

        Function: seed
        Path: backend/seeds/units_comprehensive.py

        Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
        """
        mongo_url = os.getenv('MONGO_URL', 'mongodb://127.0.0.1:27018/')
        db_name = os.getenv('DB_NAME', 'strata_production')

        client = AsyncMongoClient(mongo_url)
        db = client[db_name]

        print("🏢 Seeding comprehensive unit data...")

        # Get comprehensive unit data
        units_data = get_comprehensive_units()

        # Clear existing units
        result = await db.units.delete_many({})
        print(f"  Deleted {result.deleted_count} existing units")

        # Insert new units
        if units_data:
            await db.units.insert_many(units_data)
            print(f"  ✓ Inserted {len(units_data)} units with owner names and financial data")

        # Verify
        count = await db.units.count_documents({})
        apartments = await db.units.count_documents({'unit_type': 'apartment'})
        townhouses = await db.units.count_documents({'unit_type': 'townhouse'})
        with_owners = await db.units.count_documents({'owner_name': {'$ne': None}})

        print(f"\n✅ Units seeding complete!")
        print(f"  Total: {count} units")
        print(f"  Apartments: {apartments}")
        print(f"  Townhouses: {townhouses}")
        print(f"  With owner names: {with_owners}")

        # Close connection
        client.close()


    # Run async function
    asyncio.run(seed())
