# @featuretrace:marketplace — Cron script that scrapes property listings and inserts them into db.listings.
# Layer: cron
# Data flow: cron schedule / POST /api/listings/scrape → this script → db.listings (building-scoped).
# Related: backend/server.py #marketplace-routes (POST /listings/scrape triggers this as subprocess)
#           frontend/src/pages/public/MarketplacePage.jsx (public listings view, scope toggle)
#           frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx (schedule + settings UI)
# Collection: listings
# Output sentinel: emits "SCRAPER_RESULT: listings_created=N" for server.py to parse run count.
"""
East Gate Property Listings Scraper (Legal & Ethical)

Targets ONLY: East Gate, 14 Hoolihan Street, Denman Prospect ACT 2611

Uses Serper API (Google Search) — legal, respects robots.txt, provides
proper attribution to source listings on realestate.com.au, domain.com.au etc.
"""

import os
import re
import uuid
from datetime import datetime, timezone, timedelta

import asyncio
import httpx
import logging
from dotenv import load_dotenv
from pymongo import AsyncMongoClient
from typing import List, Dict, Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

MONGO_URL = os.getenv('MONGO_URL', 'mongodb://localhost:27018')
DB_NAME = os.getenv('DB_NAME', 'strata_production')
BUILDING_ID = os.getenv('BUILDING_ID')  # required at runtime; validated in __main__
SERPER_API_KEY = os.getenv('SERPER_API_KEY', '')

# TARGET: East Gate Residences, 14 Hoolihan Street only
EASTGATE_ADDRESS = "14 Hoolihan Street Denman Prospect ACT 2611"
EASTGATE_BUILDING = "East Gate Denman Prospect"

# Property websites to search across
PROPERTY_SITES = [
    'site:realestate.com.au',
    'site:domain.com.au',
    'site:allhomes.com.au',
]

LISTING_EXPIRY_DAYS = 60  # Keep listings for 60 days


def extract_price_from_text(text: str) -> Optional[str]:
    """Generated function header.

    Function: extract_price_from_text
    Path: backend/cron/cron_property_scraper.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    patterns = [
        r'\$[\d,]+(?:\.\d+)?[km]?',
        r'\$\d+\s*(?:per\s*week|pw|p/w)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def extract_property_details(snippet: str, title: str) -> Dict:
    """Generated function header.

    Function: extract_property_details
    Path: backend/cron/cron_property_scraper.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    details = {'bedrooms': None, 'bathrooms': None, 'parking': None, 'land_size': None}
    text = f"{title} {snippet}".lower()

    bed_match = re.search(r'(\d+)\s*(?:bed(?:room)?s?|br)', text)
    if bed_match:
        details['bedrooms'] = int(bed_match.group(1))

    bath_match = re.search(r'(\d+)\s*bath(?:room)?s?', text)
    if bath_match:
        details['bathrooms'] = int(bath_match.group(1))

    park_match = re.search(r'(\d+)\s*(?:car|garage|parking)', text)
    if park_match:
        details['parking'] = int(park_match.group(1))

    land_match = re.search(r'(\d+)\s*(?:m2|sqm|m²)', text)
    if land_match:
        details['land_size'] = f"{land_match.group(1)}m²"

    return details


def determine_listing_type(url: str, title: str, snippet: str) -> str:
    """Generated function header.

    Function: determine_listing_type
    Path: backend/cron/cron_property_scraper.py

    Note: Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
    """
    text = f"{url} {title} {snippet}".lower()
    rent_keywords = ['rent', 'rental', 'lease', 'per week', 'pw', 'p/w']
    sale_keywords = ['sale', 'buy', 'sold', 'for sale']
    rent_score = sum(1 for kw in rent_keywords if kw in text)
    sale_score = sum(1 for kw in sale_keywords if kw in text)
    return 'rent' if ('rent' in url or rent_score > sale_score) else 'sale'


def is_eastgate_listing(title: str, snippet: str, url: str) -> bool:
    """
    Filter: only include listings that are actually at 14 Hoolihan Street /
    East Gate Residences in Denman Prospect.
    """
    text = f"{title} {snippet} {url}".lower()
    address_signals = [
        'hoolihan',
        'east gate',
        'eastgate',
        '14 hoolihan',
        'denman prospect',
    ]
    # Must contain at least one address signal
    return any(signal in text for signal in address_signals)


async def search_eastgate_listings(client: httpx.AsyncClient) -> List[Dict]:
    """Search for East Gate listings using Serper API (Google Search)."""
    if not SERPER_API_KEY:
        logger.warning("SERPER_API_KEY not configured. Cannot search properties.")
        return []

    url = 'https://google.serper.dev/search'
    headers = {'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}

    listings = []

    for site in PROPERTY_SITES:
        for listing_type in ['sale', 'rent']:
            query = f'"{EASTGATE_ADDRESS}" OR "East Gate Denman Prospect" {listing_type} {site}'

            payload = {
                'q': query,
                'location': 'Australia',
                'gl': 'au',
                'hl': 'en',
                'num': 20,
            }

            try:
                logger.info(f"  Searching: {query}")
                response = await client.post(url, json=payload, headers=headers, timeout=30.0)
                response.raise_for_status()
                data = response.json()

                for result in data.get('organic', []):
                    listing_url = result.get('link', '')
                    title = result.get('title', '')
                    snippet = result.get('snippet', '')

                    # Filter to East Gate only
                    if not is_eastgate_listing(title, snippet, listing_url):
                        logger.info(f"    ⊘ Skipped (not East Gate): {title[:60]}")
                        continue

                    price = extract_price_from_text(f"{title} {snippet}")
                    details = extract_property_details(snippet, title)
                    lt = determine_listing_type(listing_url, title, snippet)

                    source = 'Unknown'
                    if 'realestate.com.au' in listing_url:
                        source = 'realestate.com.au'
                    elif 'domain.com.au' in listing_url:
                        source = 'domain.com.au'
                    elif 'allhomes.com.au' in listing_url:
                        source = 'allhomes.com.au'

                    listings.append({
                        'title': title,
                        'description': snippet,
                        'price': price or 'Contact agent',
                        'location': 'Denman Prospect ACT 2611',
                        'address': EASTGATE_ADDRESS,
                        'building': 'East Gate Residences',
                        'url': listing_url,
                        'source': source,
                        'listing_type': lt,
                        **details,
                    })
                    logger.info(f"    ✓ East Gate listing: {title[:60]}")

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Serper API error: {e}")
                continue

    return listings


async def scrape_property_listings():
    """Main scraper — deletes non-East-Gate listings, adds fresh East Gate ones."""
    mongo_client = AsyncMongoClient(MONGO_URL)
    db = mongo_client[DB_NAME]

    try:
        logger.info("=" * 60)
        logger.info("East Gate Property Listings Scraper")
        logger.info(f"Target: {EASTGATE_ADDRESS}")
        logger.info("=" * 60)

        if not SERPER_API_KEY:
            logger.error("SERPER_API_KEY not set. Add to .env file.")
            return

        # Step 1: Delete ALL existing auto-scraped property listings
        # (removes old multi-suburb listings)
        deleted = await db.listings.delete_many({
            "building_id": BUILDING_ID,
            "category": "property",
            "metadata.auto_scraped": True,
        })
        logger.info(f"Cleared {deleted.deleted_count} old auto-scraped listings")

        async with httpx.AsyncClient() as http_client:
            listings = await search_eastgate_listings(http_client)

        logger.info(f"Found {len(listings)} East Gate listings")

        # Get existing URLs to deduplicate
        existing_docs = await db.listings.find(
            {'category': 'property'},
            {'metadata.source_url': 1}
        ).to_list(None)
        existing_urls = {doc.get('metadata', {}).get('source_url', '') for doc in existing_docs}

        now = datetime.now(timezone.utc)
        new_count = 0
        last_sale_info = None  # Track most recent sale for market/pulse

        for listing in listings:
            if listing['url'] in existing_urls:
                continue

            listing_id = str(uuid.uuid4())
            expires_at = (now + timedelta(days=LISTING_EXPIRY_DAYS)).isoformat()

            description = listing['description']
            for label, val in [("Bedrooms", listing.get('bedrooms')),
                               ("Bathrooms", listing.get('bathrooms')),
                               ("Parking", listing.get('parking')),
                               ("Land", listing.get('land_size'))]:
                if val:
                    description += f"\n{label}: {val}"

            description += f"\n\n---\nListed on: {listing['source']}"
            description += f"\nView original: {listing['url']}"
            description += f"\n\n*East Gate Residences, {EASTGATE_ADDRESS}. Verify details with listing agent.*"

            doc = {
                "id": listing_id,
                "building_id": BUILDING_ID,
                "title": listing['title'],
                "description": description,
                "category": "property",
                "price": listing['price'],
                "location": listing['location'],
                "address": listing.get('address', EASTGATE_ADDRESS),
                "building": "East Gate Residences",
                "status": "active",
                "user_id": "system",
                "user_name": "Property Listings Bot",
                "contact_email": "admin@eastgateresidences.com.au",
                "contact_phone": "",
                "images": [],
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "expires_at": expires_at,
                "views": 0,
                "metadata": {
                    "source": listing['source'],
                    "source_url": listing['url'],
                    "listing_type": listing['listing_type'],
                    "suburb": "Denman Prospect",
                    "address": EASTGATE_ADDRESS,
                    "building": "East Gate Residences",
                    "bedrooms": listing.get('bedrooms'),
                    "bathrooms": listing.get('bathrooms'),
                    "parking": listing.get('parking'),
                    "land_size": listing.get('land_size'),
                    "auto_scraped": True,
                }
            }

            await db.listings.insert_one(doc)
            existing_urls.add(listing['url'])
            new_count += 1

            # Track last sale for market/pulse last_sale field
            if listing['listing_type'] == 'sale' and last_sale_info is None:
                last_sale_info = {
                    "price": listing['price'],
                    "title": listing['title'],
                    "url": listing['url'],
                    "source": listing['source'],
                    "scraped_at": now.isoformat(),
                    "address": EASTGATE_ADDRESS,
                }

        # Step 2: Update market_stats with last_sale info if we found one
        if last_sale_info:
            await db.market_stats.update_one(
                {"suburb": "Denman Prospect"},
                {"$set": {"last_sale": last_sale_info}},
                upsert=False
            )
            logger.info(f"Updated market_stats with last_sale: {last_sale_info['price']}")

        logger.info(f"✓ Scraper complete. Added {new_count} East Gate listings.")
        logger.info(f"SCRAPER_RESULT: listings_created={new_count}")

    finally:
        mongo_client.close()


if __name__ == "__main__":
    if not BUILDING_ID:
        raise RuntimeError(
            "BUILDING_ID environment variable is required. "
            "Set it to the target building's id (e.g. export BUILDING_ID=13195)"
        )
    asyncio.run(scrape_property_listings())
