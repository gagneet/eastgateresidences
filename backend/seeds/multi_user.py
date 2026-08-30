"""
Multi-User System Seed Data
Creates user_units relationships and by_laws acknowledgments
"""

from datetime import datetime, timezone, timedelta

from bson import ObjectId


def get_user_units_seed_data(users: list, units: list) -> list:
    """
    Create user_units entries linking users to their units.

    Logic:
    - super_admin/chairman/ec_member/owner → role_at_unit: 'owner', no expiration
    - tenant → role_at_unit: 'tenant', expiration in 366 days
    - guest → role_at_unit: 'guest', end_date required (handled in registration)

    Args:
        users: List of user documents from database
        units: List of unit documents from database

    Returns:
        List of user_units documents ready for insertion
    """

    user_units = []
    now = datetime.now(timezone.utc)

    # Create unit lookup by unit_number
    units_dict = {unit['unit_number']: unit for unit in units}

    for user in users:
        # Skip users without unit assignment
        if not user.get('unit_number'):
            continue

        unit_number = user['unit_number']
        unit = units_dict.get(unit_number)

        if not unit:
            print(f"⚠️  Warning: Unit {unit_number} not found for user {user['email']}")
            continue

        # Determine role_at_unit based on user role
        user_role = user.get('role', 'tenant')

        if user_role in ['super_admin', 'chairman', 'strata_admin', 'ec_member', 'owner']:
            role_at_unit = 'owner'
            end_date = None
            auto_expire_enabled = False
        elif user_role == 'tenant':
            role_at_unit = 'tenant'
            # Tenants expire in 366 days
            end_date = now + timedelta(days=366)
            auto_expire_enabled = True
        else:  # guest
            role_at_unit = 'guest'
            # Guests should have end_date set during registration
            # For seed data, set 30 days from now as default
            end_date = now + timedelta(days=30)
            auto_expire_enabled = False

        user_unit = {
            "_id": ObjectId(),
            "user_id": str(user['_id']),
            "unit_id": str(unit['_id']),
            "unit_number": unit_number,
            "role_at_unit": role_at_unit,
            "start_date": user.get('created_at', now),
            "end_date": end_date,
            "is_primary": True,  # Seed users have primary occupancy
            "auto_expire_enabled": auto_expire_enabled,
            "created_at": now,
            "updated_at": now
        }

        user_units.append(user_unit)

    return user_units


def get_by_laws_acknowledgments_seed_data(users: list) -> list:
    """
    Create by_laws acknowledgments for tenants and guests.

    Only tenants and guests require by-laws acknowledgment in seed data.
    Owners/EC/Admin don't need initial acknowledgment.

    Args:
        users: List of user documents from database

    Returns:
        List of by_laws_acknowledgment documents ready for insertion
    """

    acknowledgments = []
    now = datetime.now(timezone.utc)

    for user in users:
        user_role = user.get('role', 'tenant')

        # Only create acknowledgments for tenants and guests
        if user_role not in ['tenant', 'guest']:
            continue

        # Skip if no unit assigned
        if not user.get('unit_number'):
            continue

        acknowledgment = {
            "_id": ObjectId(),
            "user_id": str(user['_id']),
            "version": "2024-2025",
            "acknowledged_at": user.get('created_at', now),
            "ip_address": "127.0.0.1",  # Seed data placeholder
            "user_agent": "seed_script",
            "created_at": now
        }

        acknowledgments.append(acknowledgment)

    return acknowledgments


if __name__ == "__main__":
    """
    Standalone execution for testing
    """
    import sys
    import os
    from pymongo import MongoClient
    from dotenv import load_dotenv

    # Add parent directory to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    load_dotenv()

    client = MongoClient(os.getenv('MONGO_URL'))
    db = client[os.getenv('DB_NAME')]

    # Get users and units
    users_list = list(db.users.find({}))
    units_list = list(db.units.find({}))

    if not users_list:
        print("❌ No users found. Run main seed_database.py first.")
        sys.exit(1)

    if not units_list:
        print("❌ No units found. Run main seed_database.py first.")
        sys.exit(1)

    # Seed user_units
    existing_user_units = db.user_units.count_documents({})
    if existing_user_units == 0:
        user_units = get_user_units_seed_data(users_list, units_list)
        if user_units:
            db.user_units.insert_many(user_units)
            print(f"✓ Created {len(user_units)} user-unit relationships")
        else:
            print("⊘ No user-unit relationships to create")
    else:
        print(f"⊘ User-unit relationships already exist ({existing_user_units} entries)")

    # Seed by_laws acknowledgments
    existing_acks = db.by_laws_acknowledgments.count_documents({})
    if existing_acks == 0:
        acknowledgments = get_by_laws_acknowledgments_seed_data(users_list)
        if acknowledgments:
            db.by_laws_acknowledgments.insert_many(acknowledgments)
            print(f"✓ Created {len(acknowledgments)} by-laws acknowledgments")
        else:
            print("⊘ No by-laws acknowledgments to create")
    else:
        print(f"⊘ By-laws acknowledgments already exist ({existing_acks} entries)")

    client.close()
