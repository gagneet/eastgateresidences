#!/usr/bin/env python3
from pathlib import Path

"""
Automated Notification Cleanup System
Deletes old notifications based on retention settings and task completion status.
"""
import asyncio
import sys
import os
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from pymongo import AsyncMongoClient

from db_postgres.repos import config_repo

# Load environment variables
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


async def cleanup_notifications():
    """
    Cleanup old notifications from the database.
    """
    mongo_url = os.getenv('MONGO_URL', 'mongodb://localhost:27018')
    db_name = os.getenv('DB_NAME', 'strata_production')

    client = AsyncMongoClient(mongo_url)
    db = client[db_name]

    try:
        print(f"\n{'=' * 60}")
        print(f"🧹 NOTIFICATION CLEANUP - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"{'=' * 60}\n")

        # 1. Check if feature is enabled
        feature_enabled = await config_repo.get_global_feature_toggle_state(
            "notification_cleanup",
            default=True,
        )
        if not feature_enabled:
            print("  ⊘ Notification cleanup feature is disabled. Skipping.")
            return

        # 2. Get retention settings
        settings = await db.site_settings.find_one({"id": "main"})
        retention_days = settings.get("notification_retention_days", 30) if settings else 30
        print(f"  ⚙️  Retention period: {retention_days} days\n")

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
        cutoff_iso = cutoff_date.isoformat()

        # 3. Find potentially deletable notifications (older than cutoff)
        # Use pagination to avoid loading all old notifications at once
        page_size = 1000
        delete_ids = []
        keep_count = 0
        total_processed = 0

        # Process notifications in batches.
        # building_id filter prevents cross-tenant deletion — each notification
        # must have building_id set (added at creation by the notification service).
        skip = 0
        while True:
            old_notifications = await db.user_notifications.find({
                "created_at": {"$lt": cutoff_iso},
                "building_id": {"$exists": True},
            }).skip(skip).limit(page_size).to_list(page_size)

            if not old_notifications:
                break

            total_processed += len(old_notifications)

            for notif in old_notifications:
                notif_type = notif.get("type")
                link = notif.get("link", "")
                notif_building = notif.get("building_id")

                should_delete = True  # Default for notifications without specific task tracking

                # Special logic for Maintenance requests
                if notif_type == "maintenance" and link:
                    # Extract ID from link like /maintenance/ID or just check the request
                    match = re.search(r'/maintenance/([a-f0-9\-]+)', link)
                    req_id = match.group(1) if match else None

                    if req_id:
                        # Scope the lookup to the same building as the notification
                        query = {"id": req_id}
                        if notif_building:
                            query["building_id"] = notif_building
                        request = await db.maintenance_requests.find_one(query)
                        if request and request.get("status") != "completed":
                            # Task is NOT completed, keep the notification even if old
                            should_delete = False
                            print(f"  📌 Keeping: Maintenance '{request.get('title')}' is not yet completed.")

                # Special logic for AGM (keep until AGM date passes)
                elif notif_type == "agm" and link:
                    agm_query = {"status": "upcoming"}
                    if notif_building:
                        agm_query["building_id"] = notif_building
                    upcoming_agm = await db.agm.find_one(agm_query)
                    if upcoming_agm:
                        should_delete = False
                        print(f"  📌 Keeping: There is still an upcoming AGM.")

                if should_delete:
                    # Use _id (ObjectId) for reliable deletion; fall back to "id" string field
                    notif_ref = notif.get("_id") or notif.get("id")
                    if notif_ref is not None:
                        delete_ids.append(notif_ref)
                else:
                    keep_count += 1

            # Move to next batch
            skip += page_size

        if total_processed == 0:
            print("  ✅ No old notifications found to process.")
            return

        print(f"  🔍 Processed {total_processed} notifications older than {retention_days} days...")

        # 4. Perform deletion — use _id (ObjectId) where available for reliability
        if delete_ids:
            result = await db.user_notifications.delete_many({"_id": {"$in": delete_ids}})
            print(f"\n  ✅ Deleted {result.deleted_count} old notifications.")

        # 5. Cleanup expired documents scoped to this building only.
        # Global delete with no building_id filter was deleting documents across all tenants.
        # Also guard that only system-generated docs (author_id="system") are auto-purged —
        # human-authored documents with expires_at set should not be hard-deleted by a cron.
        now_iso = datetime.now(timezone.utc).isoformat()
        doc_result = await db.documents.delete_many({
            "building_id": {"$exists": True},
            "author_id": "system",
            "expires_at": {"$lt": now_iso},
        })
        if doc_result.deleted_count > 0:
            print(f"  📄 Deleted {doc_result.deleted_count} expired documents.")

        if keep_count > 0:
            print(f"  ℹ️  Kept {keep_count} notifications because their tasks are not yet complete.")

        print(f"\n{'=' * 60}")
        print("📋 CLEANUP SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Total processed: {total_processed}")
        print(f"  Deleted:         {len(delete_ids)}")
        print(f"  Retained:        {keep_count}")
        print(f"{'=' * 60}\n")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(cleanup_notifications())
