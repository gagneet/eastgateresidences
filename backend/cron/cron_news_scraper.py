# @featuretrace:news — Cron script that fetches news articles and inserts them into db.blog_posts.
# Layer: cron
# Data flow: cron schedule / POST /api/blog/scrape → this script → db.blog_posts (building-scoped).
# Related: backend/server.py #news-routes (POST /blog/scrape triggers this as subprocess)
#           frontend/src/pages/public/BlogPage.jsx (public reader view)
#           frontend/src/pages/dashboard/BlogManagementPage.jsx (admin trigger + CRUD)
#           frontend/src/pages/dashboard/admin/ScraperSettingsPage.jsx (schedule + settings UI)
# Collection: blog_posts
# Output sentinel: emits "SCRAPER_RESULT: articles_created=N" for server.py to parse run count.
"""
Enhanced News Scraper with Multiple API Options

This script provides 3 different methods for fetching real news:
1. Serper API (Google Search) - Recommended, best results
2. NewsAPI - Dedicated news aggregator
3. RSS Feeds - Free, no API key required

Features:
- Fetches real article content and images
- Extracts full descriptions and excerpts
- Includes source attribution with links
- Smart deduplication based on title similarity
- Relevance scoring for keyword matching
- Automatic image downloading (optional)
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

# For RSS feeds (free option)
try:
    import feedparser

    RSS_AVAILABLE = True
except ImportError:
    RSS_AVAILABLE = False
    print("⚠️  feedparser not installed. RSS scraping disabled. Install: pip install feedparser")

# For HTML parsing and content extraction
try:
    from bs4 import BeautifulSoup

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    print("⚠️  beautifulsoup4 not installed. Content extraction limited. Install: pip install beautifulsoup4")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Configuration
MONGO_URL = os.getenv('MONGO_URL', 'mongodb://localhost:27018')
DB_NAME = os.getenv('DB_NAME', 'strata_production')
BUILDING_ID = os.getenv('BUILDING_ID')  # required at runtime; validated in __main__

# API Keys (add to .env file)
SERPER_API_KEY = os.getenv('SERPER_API_KEY', '')  # Get from https://serper.dev (free tier: 2500/month)
NEWSAPI_KEY = os.getenv('NEWSAPI_KEY', '')  # Get from https://newsapi.org (free tier: 100/day)

# Search Keywords (broader terms for better matching)
KEYWORDS = [
    'Canberra',  # Broad term to catch all Canberra news
    'ACT',
    'Denman Prospect',
    'Molonglo',
    'property',
    'apartment',
    'development',
    'community',
    'local',
    'suburb',
]

# RSS Feeds (FREE - No API key required) - EXPANDED
RSS_FEEDS = [
    # General Canberra News
    'https://www.canberratimes.com.au/rss.xml',
    'https://citynews.com.au/feed/',
    'https://the-riotact.com/feed',
    'https://www.canberraweekly.com.au/feed/',

    # Property & Development News
    'https://www.allhomes.com.au/news/act/rss.xml',
    'https://www.planning.act.gov.au/news-room/rss',

    # ABC Canberra
    'https://www.abc.net.au/news/feed/51120/rss.xml',

    # Business & Community
    'https://www.canberratimes.com.au/rss/business.xml',
    'https://www.canberratimes.com.au/rss/property.xml',
]

# Configuration - ENHANCED with user preferences
SCRAPING_METHOD = os.getenv('NEWS_SCRAPING_METHOD', 'rss')  # Options: 'serper', 'newsapi', 'rss'
MAX_ARTICLES_PER_KEYWORD = int(os.getenv('MAX_ARTICLES_PER_KEYWORD', '10'))  # Increased from 5
MIN_CONTENT_LENGTH = int(os.getenv('MIN_CONTENT_LENGTH', '150'))  # Adjusted for quality
RELEVANCE_THRESHOLD = float(os.getenv('RELEVANCE_THRESHOLD', '0.12'))  # Lowered for more articles


def calculate_relevance_score(text: str, keywords: List[str]) -> float:
    """Calculate how relevant an article is to our keywords."""
    text_lower = text.lower()
    score = 0.0
    total_keywords = len(keywords)

    for keyword in keywords:
        keyword_lower = keyword.lower()
        # Count occurrences
        count = text_lower.count(keyword_lower)
        if count > 0:
            score += min(count / 10, 1.0)  # Cap at 1.0 per keyword

    return score / total_keywords if total_keywords > 0 else 0.0


def extract_text_from_html(html: str) -> str:
    """Extract plain text from HTML content."""
    if not BS4_AVAILABLE:
        # Fallback: Simple regex
        text = re.sub('<.*?>', '', html)
        return text.strip()

    soup = BeautifulSoup(html, 'html.parser')

    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.decompose()

    text = soup.get_text()

    # Clean up whitespace
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = ' '.join(chunk for chunk in chunks if chunk)

    return text


async def fetch_from_serper(keyword: str, client: httpx.AsyncClient) -> List[Dict]:
    """
    Fetch news using Serper API (Google Search results)

    Cost: Free tier - 2500 searches/month
    Quality: Excellent - Google's search results
    Setup: Get API key from https://serper.dev
    """
    if not SERPER_API_KEY:
        logger.warning("SERPER_API_KEY not configured. Skipping Serper API.")
        return []

    url = 'https://google.serper.dev/news'
    headers = {
        'X-API-KEY': SERPER_API_KEY,
        'Content-Type': 'application/json'
    }

    payload = {
        'q': f'{keyword} news',
        'location': 'Australia',
        'gl': 'au',
        'hl': 'en',
        'num': MAX_ARTICLES_PER_KEYWORD,
        'autocorrect': True,
        'page': 1
    }

    try:
        response = await client.post(url, json=payload, headers=headers, timeout=30.0)
        response.raise_for_status()
        data = response.json()

        articles = []
        for item in data.get('news', [])[:MAX_ARTICLES_PER_KEYWORD]:
            # Serper provides rich data
            article = {
                'title': item.get('title', ''),
                'content': item.get('snippet', ''),
                'excerpt': item.get('snippet', '')[:200],
                'url': item.get('link', ''),
                'image_url': item.get('imageUrl', ''),
                'source': item.get('source', 'Unknown'),
                'published_date': item.get('date', ''),
                'tags': [keyword, 'News'],
            }

            # Calculate relevance
            full_text = f"{article['title']} {article['content']}"
            relevance = calculate_relevance_score(full_text, KEYWORDS)

            if relevance >= RELEVANCE_THRESHOLD:
                articles.append(article)
                logger.info(f"  ✓ Serper: {article['title']} (relevance: {relevance:.2f})")

        return articles

    except Exception as e:
        logger.error(f"Serper API error for '{keyword}': {e}")
        return []


async def fetch_from_newsapi(keyword: str, client: httpx.AsyncClient) -> List[Dict]:
    """
    Fetch news using NewsAPI

    Cost: Free tier - 100 requests/day, 1 month history
    Quality: Good - Dedicated news aggregator
    Setup: Get API key from https://newsapi.org
    """
    if not NEWSAPI_KEY:
        logger.warning("NEWSAPI_KEY not configured. Skipping NewsAPI.")
        return []

    url = 'https://newsapi.org/v2/everything'
    params = {
        'q': keyword,
        'apiKey': NEWSAPI_KEY,
        'language': 'en',
        'sortBy': 'publishedAt',
        'pageSize': MAX_ARTICLES_PER_KEYWORD,
        'searchIn': 'title,description',
    }

    try:
        response = await client.get(url, params=params, timeout=30.0)
        response.raise_for_status()
        data = response.json()

        articles = []
        for item in data.get('articles', [])[:MAX_ARTICLES_PER_KEYWORD]:
            # NewsAPI provides good structure
            article = {
                'title': item.get('title', ''),
                'content': item.get('description', '') or item.get('content', ''),
                'excerpt': (item.get('description', '') or '')[:200],
                'url': item.get('url', ''),
                'image_url': item.get('urlToImage', ''),
                'source': item.get('source', {}).get('name', 'Unknown'),
                'published_date': item.get('publishedAt', ''),
                'tags': [keyword, 'News'],
            }

            # Calculate relevance
            full_text = f"{article['title']} {article['content']}"
            relevance = calculate_relevance_score(full_text, KEYWORDS)

            if relevance >= RELEVANCE_THRESHOLD and len(article['content']) >= MIN_CONTENT_LENGTH:
                articles.append(article)
                logger.info(f"  ✓ NewsAPI: {article['title']} (relevance: {relevance:.2f})")

        return articles

    except Exception as e:
        logger.error(f"NewsAPI error for '{keyword}': {e}")
        return []


async def fetch_from_rss(client: httpx.AsyncClient) -> List[Dict]:
    """
    Fetch news from RSS feeds (FREE - No API key required)

    Cost: FREE
    Quality: Good - Direct from news sites
    Setup: None required
    """
    if not RSS_AVAILABLE:
        logger.warning("feedparser not installed. Cannot fetch RSS feeds.")
        return []

    articles = []

    for feed_url in RSS_FEEDS:
        try:
            logger.info(f"Fetching RSS feed: {feed_url}")

            # Fetch feed
            response = await client.get(feed_url, timeout=30.0, follow_redirects=True)
            feed = feedparser.parse(response.text)

            source_name = feed.feed.get('title', feed_url)

            for entry in feed.entries[:MAX_ARTICLES_PER_KEYWORD]:
                # Extract content
                content = ''
                if hasattr(entry, 'content'):
                    content = entry.content[0].value
                elif hasattr(entry, 'summary'):
                    content = entry.summary
                elif hasattr(entry, 'description'):
                    content = entry.description

                # Clean HTML from content
                content_text = extract_text_from_html(content)

                # Extract image
                image_url = ''
                if hasattr(entry, 'media_content'):
                    image_url = entry.media_content[0].get('url', '')
                elif hasattr(entry, 'media_thumbnail'):
                    image_url = entry.media_thumbnail[0].get('url', '')
                elif hasattr(entry, 'enclosures') and entry.enclosures:
                    if 'image' in entry.enclosures[0].get('type', ''):
                        image_url = entry.enclosures[0].get('href', '')

                article = {
                    'title': entry.get('title', ''),
                    'content': content_text,
                    'excerpt': content_text[:200] if content_text else '',
                    'url': entry.get('link', ''),
                    'image_url': image_url,
                    'source': source_name,
                    'published_date': entry.get('published', ''),
                    'tags': ['RSS', 'News'],
                }

                # Calculate relevance
                full_text = f"{article['title']} {article['content']}"
                relevance = calculate_relevance_score(full_text, KEYWORDS)

                if relevance >= RELEVANCE_THRESHOLD and len(article['content']) >= MIN_CONTENT_LENGTH:
                    articles.append(article)
                    logger.info(f"  ✓ RSS ({source_name}): {article['title']} (relevance: {relevance:.2f})")

        except Exception as e:
            logger.error(f"RSS feed error for {feed_url}: {e}")
            continue

    return articles


def is_duplicate(title: str, existing_titles: List[str]) -> bool:
    """Check if article is duplicate based on title similarity."""
    title_lower = title.lower()

    for existing_title in existing_titles:
        existing_lower = existing_title.lower()

        # Exact match
        if title_lower == existing_lower:
            return True

        # Very similar (more than 80% of words match)
        title_words = set(title_lower.split())
        existing_words = set(existing_lower.split())

        if len(title_words) > 0 and len(existing_words) > 0:
            intersection = title_words & existing_words
            similarity = len(intersection) / max(len(title_words), len(existing_words))

            if similarity > 0.8:
                return True

    return False


async def download_image(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    Download image and save locally (optional).
    Returns local path or original URL.
    """
    # For now, just return the URL
    # You can implement actual downloading if needed
    return url


async def search_and_create_news():
    """Main function to scrape news and create blog posts."""
    mongo_client = AsyncMongoClient(MONGO_URL)
    db = mongo_client[DB_NAME]

    try:
        logger.info("=" * 60)
        logger.info(f"Starting Enhanced News Scraper (Method: {SCRAPING_METHOD.upper()})")
        logger.info("=" * 60)

        async with httpx.AsyncClient() as http_client:
            all_articles = []

            # Fetch articles based on selected method
            if SCRAPING_METHOD == 'serper':
                logger.info("Using Serper API (Google Search)")
                for keyword in KEYWORDS:
                    logger.info(f"Searching for: {keyword}")
                    articles = await fetch_from_serper(keyword, http_client)
                    all_articles.extend(articles)
                    await asyncio.sleep(1)  # Rate limiting

            elif SCRAPING_METHOD == 'newsapi':
                logger.info("Using NewsAPI")
                for keyword in KEYWORDS:
                    logger.info(f"Searching for: {keyword}")
                    articles = await fetch_from_newsapi(keyword, http_client)
                    all_articles.extend(articles)
                    await asyncio.sleep(1)  # Rate limiting

            elif SCRAPING_METHOD == 'rss':
                logger.info("Using RSS Feeds (FREE)")
                all_articles = await fetch_from_rss(http_client)

            else:
                logger.error(f"Unknown scraping method: {SCRAPING_METHOD}")
                return

            logger.info(f"\nFound {len(all_articles)} relevant articles")

            # Get existing article titles for this building only — global dedup
            # was suppressing legitimate per-building articles that shared a title.
            existing_titles_cursor = db.blog_posts.find(
                {"building_id": BUILDING_ID}, {"title": 1}
            )
            existing_titles = set()
            async for doc in existing_titles_cursor:
                existing_titles.add(doc["title"])

            # Create blog posts
            new_items_count = 0

            for article in all_articles:
                # Skip duplicates (now O(1) lookup with set)
                if is_duplicate(article['title'], existing_titles):
                    logger.info(f"  ⊘ Duplicate: {article['title']}")
                    continue

                # Skip if no content
                if len(article.get('content', '')) < MIN_CONTENT_LENGTH:
                    logger.info(f"  ⊘ Too short: {article['title']}")
                    continue

                post_id = str(uuid.uuid4())
                now = datetime.now(timezone.utc)

                # Posts expire in 1 year
                expires_at = (now + timedelta(days=365)).isoformat()

                # Process image
                image_url = article.get('image_url', '')
                if image_url:
                    # Optionally download and store locally
                    # image_url = await download_image(image_url, http_client)
                    pass

                # Build content with source attribution
                content_with_source = article['content']
                if article.get('url'):
                    content_with_source += f"\n\n---\n\n**Source:** [{article['source']}]({article['url']})"
                    if article.get('published_date'):
                        content_with_source += f"\n\n**Published:** {article['published_date']}"

                post_doc = {
                    "id": post_id,
                    "building_id": BUILDING_ID,
                    "title": article['title'],
                    "content": content_with_source,
                    "excerpt": article['excerpt'],
                    "cover_image": image_url,
                    "tags": article.get('tags', ['News']),
                    "is_published": False,  # Create as draft for review
                    "is_draft": True,
                    "author_id": "system",
                    "author_name": "East Gate News Bot",
                    "views": 0,
                    "expires_at": expires_at,
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "source_url": article.get('url', ''),
                    "source_name": article.get('source', 'Unknown'),
                }

                await db.blog_posts.insert_one(post_doc)
                new_items_count += 1
                existing_titles.add(article['title'])  # Add to dedup list
                logger.info(f"  ✓ Created: {article['title']}")

            # Retire old news (older than 1 year)
            one_year_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
            retire_result = await db.blog_posts.delete_many({
                "building_id": BUILDING_ID,
                "created_at": {"$lt": one_year_ago},
                "author_id": "system"  # Only delete auto-scraped articles
            })

            if retire_result.deleted_count > 0:
                logger.info(f"\n✓ Retired {retire_result.deleted_count} articles older than 1 year")

            logger.info("\n" + "=" * 60)
            logger.info(f"✓ Scraper finished. Created {new_items_count} new articles.")
            logger.info(f"SCRAPER_RESULT: articles_created={new_items_count}")
            logger.info("=" * 60)

    finally:
        mongo_client.close()


if __name__ == "__main__":
    if not BUILDING_ID:
        raise RuntimeError(
            "BUILDING_ID environment variable is required. "
            "Set it to the target building's id (e.g. export BUILDING_ID=13195)"
        )
    asyncio.run(search_and_create_news())
