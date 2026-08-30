"""
Chat Groups Seed Data
Creates 3 system chat groups with auto-join logic
"""

from datetime import datetime, timezone

from bson import ObjectId


def get_chat_groups_seed_data(admin_user_id: str) -> list:
    """
    Returns 3 system chat groups that users auto-join based on role/unit type.

    Groups:
    1. General - All approved users (type: general)
    2. EC Members - super_admin/chairman/ec_member roles (type: role_based)
    3. Townhouse Residents - Unit types T01-T17 (type: unit_based)

    Args:
        admin_user_id: ObjectId of admin user as string

    Returns:
        List of chat group documents ready for insertion
    """

    now = datetime.now(timezone.utc)

    groups = [
        {
            "_id": ObjectId(),
            "name": "General",
            "description": "Community-wide announcements and discussions",
            "type": "general",
            "is_system": True,
            "created_by": admin_user_id,
            "created_at": now,
            "updated_at": now,
            "members": [],  # Populated by auto-join logic on user login
            "settings": {
                "auto_join_criteria": {
                    "type": "all_approved",  # All users with is_approved=true
                },
                "who_can_post": ["all"],
                "who_can_delete": ["admin", "chairman", "strata_admin"],
                "notifications_enabled": True
            },
            "message_count": 0,
            "last_message_at": None
        },
        {
            "_id": ObjectId(),
            "name": "EC Members",
            "description": "Private group for Executive Committee members",
            "type": "role_based",
            "is_system": True,
            "created_by": admin_user_id,
            "created_at": now,
            "updated_at": now,
            "members": [],  # Populated by auto-join logic
            "settings": {
                "auto_join_criteria": {
                    "type": "role",
                    "roles": ["super_admin", "chairman", "strata_admin", "ec_member"]
                },
                "who_can_post": ["member"],  # Only group members
                "who_can_delete": ["admin", "chairman", "strata_admin"],
                "notifications_enabled": True
            },
            "message_count": 0,
            "last_message_at": None
        },
        {
            "_id": ObjectId(),
            "name": "Townhouse Residents",
            "description": "Discussion group for townhouse residents",
            "type": "unit_based",
            "is_system": True,
            "created_by": admin_user_id,
            "created_at": now,
            "updated_at": now,
            "members": [],  # Populated by auto-join logic
            "settings": {
                "auto_join_criteria": {
                    "type": "unit_type",
                    "unit_pattern": "^T\\d+$"  # Matches T01, T02, etc.
                },
                "who_can_post": ["member"],
                "who_can_delete": ["admin", "chairman", "strata_admin"],
                "notifications_enabled": True
            },
            "message_count": 0,
            "last_message_at": None
        }
    ]

    return groups


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

    # Get admin user ID
    admin_user = db.users.find_one({"email": "admin@eastgate.com"})
    if not admin_user:
        print("❌ Admin user not found. Run main seed_database.py first.")
        sys.exit(1)

    admin_user_id = str(admin_user['_id'])

    # Check if groups already exist
    existing_count = db.chat_groups.count_documents({})
    if existing_count > 0:
        print(f"⊘ Chat groups already exist ({existing_count} groups)")
        sys.exit(0)

    # Insert groups
    groups = get_chat_groups_seed_data(admin_user_id)
    db.chat_groups.insert_many(groups)

    print(f"✓ Created {len(groups)} system chat groups:")
    for group in groups:
        print(f"  - {group['name']} ({group['type']})")

    client.close()
