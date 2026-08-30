"""
Suburb Radar cron — runs Sunday 17:00 AEST.
Aggregates suburb intelligence and sends digest to opted-in residents.
"""
import logging
import os
from datetime import datetime, timezone

import asyncio
import html as html_lib

logger = logging.getLogger(__name__)

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27018")
DB_NAME = os.environ.get("DB_NAME", "strata_production")
SUBURB = "Denman Prospect"


async def fetch_recent_sales(db) -> dict:
    """Generated function header.

    Function: fetch_recent_sales
    Path: backend/cron/cron_suburb_radar.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    snapshot = await db.market_snapshots.find_one(
        {"suburb": SUBURB},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    if not snapshot:
        return {}
    return {
        "median_price": snapshot.get("median_price"),
        "days_on_market": snapshot.get("days_on_market"),
        "rental_yield": snapshot.get("rental_yield"),
        "growth_yoy": snapshot.get("growth_yoy"),
    }


async def fetch_esa_incidents() -> list:
    """Generated function header.

    Function: fetch_esa_incidents
    Path: backend/cron/cron_suburb_radar.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            url = "https://esa.act.gov.au/api/incidents"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return [
                        {
                            "title": i.get("properties", {}).get("title", "Incident"),
                            "severity": i.get("properties", {}).get("severity", "advice"),
                            "url": i.get("properties", {}).get("url", "https://esa.act.gov.au"),
                        }
                        for i in data.get("features", [])[:3]
                    ]
    except Exception as exc:
        logger.warning("cron_suburb_radar: ESA incident fetch failed: %s", exc)
    return []


def build_radar_html(sales: dict, incidents: list, events: list) -> str:
    """Generated function header.

    Function: build_radar_html
    Path: backend/cron/cron_suburb_radar.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    sales_html = ""
    if sales.get("median_price"):
        growth = sales.get("growth_yoy") or 0
        growth_str = f' ({growth:+.1f}% YoY)' if growth else ""
        sales_html = (
            f"<p><strong>📊 Property Market</strong><br>"
            f"Denman Prospect median: <strong>${sales['median_price']:,.0f}</strong>{growth_str}<br>"
            f"Days on market: {sales.get('days_on_market', 'N/A')}</p>"
        )

    # Incident titles are scraped from a third-party feed and event titles are
    # user-entered, so both are escaped before going into the HTML digest.
    incidents_html = ""
    if incidents:
        items = "".join(
            f"<li>{html_lib.escape(str(i.get('title') or ''))} "
            f"({html_lib.escape(str(i.get('severity') or '').upper())})</li>"
            for i in incidents
        )
        incidents_html = f"<p><strong>🚨 ACT ESA Incidents</strong></p><ul>{items}</ul>"

    events_html = ""
    if events:
        items = "".join(
            f"<li>{html_lib.escape(str(e.get('title') or '')[:60])}</li>" for e in events
        )
        events_html = f"<p><strong>📅 Upcoming building events</strong></p><ul>{items}</ul>"

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:#2F4F4F;color:white;padding:24px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">Denman Prospect — This Week</h2>
        <p style="margin:4px 0 0;opacity:0.8;font-size:14px;">Your weekly local intelligence digest</p>
      </div>
      <div style="background:#f9f9f9;padding:24px;border-radius:0 0 8px 8px;">
        {sales_html}{incidents_html}{events_html}
        <div style="text-align:center;margin-top:24px;">
          <a href="https://eastgateresidences.com.au/dashboard"
             style="background:#2F4F4F;color:white;padding:12px 32px;border-radius:24px;
                    text-decoration:none;display:inline-block;">
            Open East Gate dashboard
          </a>
        </div>
      </div>
      <div style="text-align:center;color:#aaa;font-size:11px;padding:16px;">
        <a href="https://eastgateresidences.com.au/my-communications?tab=preferences"
           style="color:#aaa;">Unsubscribe from suburb radar</a>
      </div>
    </div>"""


async def send_suburb_radar(building_id: str | None = None):
    # sys.path fix must come BEFORE any local imports so utils.email resolves correctly.
    """Generated function header.

    Function: send_suburb_radar
    Path: backend/cron/cron_suburb_radar.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from pymongo import AsyncMongoClient
    from utils.email import send_email_async

    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]
    now = datetime.now(timezone.utc)

    # Fetch active buildings — send per-building using each building's users only.
    # Previously queried all active users globally, sending Eastgate-specific suburb
    # content to users from other buildings (cross-tenant email leak).
    buildings = await db.buildings.find(
        {"is_active": {"$ne": False}},
        {"building_id": 1, "plan_id": 1},
    ).to_list(100)
    building_ids = [
        b.get("building_id") or b.get("plan_id")
        for b in buildings
        if b.get("building_id") or b.get("plan_id")
    ]

    sales, incidents = await asyncio.gather(fetch_recent_sales(db), fetch_esa_incidents())

    sent = 0
    for bid in building_ids:
        # Fetch events scoped to this building — previously fetched globally, causing
        # East Gate calendar entries to appear in Sierra Gungahlin's digest.
        events = await db.events.find(
            {"building_id": bid, "event_date": {"$gte": now.isoformat()}},
            {"_id": 0, "title": 1, "event_date": 1},
        ).sort("event_date", 1).limit(3).to_list(3)

        html_content = build_radar_html(sales, incidents, events)

        users = await db.users.find(
            {"building_id": bid, "is_active": True, "is_approved": True},
            {"_id": 0, "id": 1, "email": 1, "full_name": 1},
        ).to_list(500)

        for user in users:
            prefs = await db.email_notification_preferences.find_one({"user_id": user["id"]})
            # Only skip if explicitly opted out — missing prefs means opted in (opt-out model)
            if prefs and prefs.get("suburb_radar_enabled") is False:
                continue
            if not user.get("email"):
                continue
            await send_email_async(
                to_email=user["email"],
                subject=f"Denman Prospect this week — {now.strftime('%d %B')}",
                html_content=html_content,
                context="suburb_radar",
            )
            sent += 1

    print(f"Suburb radar sent to {sent} users")
    client.close()


if __name__ == "__main__":
    asyncio.run(send_suburb_radar())
