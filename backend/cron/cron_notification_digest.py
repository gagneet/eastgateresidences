"""
Weekly notification digest — Sunday 18:00 AEST.
Summarises unread notifications from the past 7 days.
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27018")
DB_NAME = os.environ.get("DB_NAME", "strata_production")


async def send_weekly_digests(building_id: str | None = None):
    from pymongo import AsyncMongoClient
    from utils.email import send_email_async
    import html as _html  # stdlib — safe HTML escaping for user-supplied content

    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    # Fetch all active buildings so digest is per-tenant, not global.
    # Previously ran a single pipeline across all buildings, causing Sierra Gungahlin
    # users to receive "Your week at East Gate" branded emails.
    buildings = await db.buildings.find(
        {"is_active": {"$ne": False}},
        {"building_id": 1, "plan_id": 1, "name": 1},
    ).to_list(100)

    sent = 0
    for building in buildings:
        bid = building.get("building_id") or building.get("plan_id")
        if not bid:
            continue
        building_name = building.get("name", "Your Building")

        # Get user IDs for this building so the aggregation is scoped per-tenant.
        building_users = await db.users.find(
            {"building_id": bid, "is_active": True},
            {"_id": 0, "id": 1},
        ).to_list(2000)
        user_ids = [u["id"] for u in building_users if u.get("id")]
        if not user_ids:
            continue

        pipeline = [
            {
                "$match": {
                    "is_read": False,
                    "created_at": {"$gte": week_ago.isoformat()},
                    "user_id": {"$in": user_ids},
                }
            },
            {
                "$group": {
                    "_id": "$user_id",
                    "count": {"$sum": 1},
                    "notifications": {
                        "$push": {
                            "title": "$title",
                            "message": "$message",
                            "link": "$link",
                        }
                    },
                }
            },
        ]
        groups = await db.user_notifications.aggregate(pipeline).to_list(None)

        for group in groups:
            user_id = group["_id"]
            user = await db.users.find_one(
                {"id": user_id, "is_active": True},
                {"_id": 0, "email": 1, "full_name": 1, "role": 1, "building_id": 1},
            )
            if not user or not user.get("email"):
                continue

            prefs = await db.email_notification_preferences.find_one({"user_id": user_id})
            if prefs and prefs.get("digest_frequency") == "never":
                continue

            notifications = group["notifications"][:8]
            count = group["count"]

            # Escape all user-supplied fields before embedding in HTML (XSS prevention)
            items_html = "".join(
                f'<tr><td style="padding:8px 0;border-bottom:1px solid #eee;">'
                f"<strong>{_html.escape(str(n.get('title', '')))}</strong><br>"
                f'<span style="color:#666;font-size:13px;">'
                f"{_html.escape(str(n.get('message', ''))[:100])}</span></td>"
                f'<td style="padding:8px;width:80px;text-align:right;">'
                # link must be a safe relative path — strip any scheme to prevent javascript: injection.
                # n["link"] is already an absolute app path (e.g. "/admin/owner-transfers"), not a
                # /dashboard-relative fragment — do not prepend "/dashboard" here (that produced a
                # syntax error AND a double-prefixed dead link pre-existing before this fix; this
                # whole file could not even be imported before, so the bug was never exercised).
                f'<a href="{_html.escape(str(n.get("link", "")).split("?")[0][:80])}" '
                f'style="color:#2F4F4F;font-size:12px;">View →</a></td></tr>'
                for n in notifications
            )
            more_html = (
                f'<p style="color:#666;font-size:13px;">+ {count - 8} more notifications</p>'
                if count > 8
                else ""
            )

            # For managers, show auto-resolved count scoped to their building (not global)
            auto_resolved_html = ""
            if user.get("role") in ("strata_manager", "super_admin", "ec_member", "strata_admin"):
                building_resolved = await db.workflow_requests.count_documents(
                    {
                        "building_id": bid,
                        "auto_resolved": True,
                        "created_at": {"$gte": week_ago.isoformat()},
                        "is_test_data": {"$ne": True},
                    }
                )
                if building_resolved > 0:
                    auto_resolved_html = (
                        f'<p style="color:#16a34a;font-size:13px;">'
                        f"⚡ {building_resolved} request{'s' if building_resolved != 1 else ''} "
                        f"auto-resolved this week by the platform</p>"
                    )

            html_content = f"""
            <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
              <div style="background:#2F4F4F;color:white;padding:24px;border-radius:8px 8px 0 0;">
                <h2 style="margin:0;">Your week at {_html.escape(building_name)}</h2>
                <p style="margin:4px 0 0;opacity:0.8;font-size:14px;">
                  {count} update{'s' if count != 1 else ''} since you last checked in
                </p>
              </div>
              <div style="background:#f9f9f9;padding:24px;border-radius:0 0 8px 8px;">
                {auto_resolved_html}
                <table style="width:100%;border-collapse:collapse;">{items_html}</table>
                {more_html}
                <div style="text-align:center;margin-top:24px;">
                  <a href="https://eastgateresidences.com.au/notifications"
                     style="background:#2F4F4F;color:white;padding:12px 32px;border-radius:24px;
                            text-decoration:none;display:inline-block;font-weight:500;">
                    Open dashboard
                  </a>
                </div>
              </div>
              <div style="text-align:center;color:#aaa;font-size:11px;padding:16px;">
                <a href="https://eastgateresidences.com.au/my-communications?tab=preferences"
                   style="color:#aaa;">Manage notifications</a>
              </div>
            </div>"""

            await send_email_async(
                to_email=user["email"],
                subject=f"Your week at {building_name} — {count} update{'s' if count != 1 else ''}",
                html_content=html_content,
                context="weekly_digest",
            )
            sent += 1

    print(f"Digest sent to {sent} users")
    client.close()


if __name__ == "__main__":
    asyncio.run(send_weekly_digests())
