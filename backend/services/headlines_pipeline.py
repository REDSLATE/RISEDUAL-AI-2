"""Headlines Pipeline — Scrape → Clean → Store to MongoDB.

Adapted from user-provided data pipeline pattern. Runs as a background task
to continuously collect, deduplicate, and store financial headlines for the
AI prediction and research engines.

Usage:
    pipeline = HeadlinesPipeline(db)
    await pipeline.run_cycle()          # One scrape cycle
    recent = await pipeline.get_recent(hours=24)  # Fetch stored headlines
    stats = await pipeline.stats()
"""
import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# Configurable sources: (name, url, headline_selector)
DEFAULT_SOURCES = [
    ("CNBC", "https://www.cnbc.com/markets/", "div.Card-titleContainer a"),
    ("Reuters", "https://www.reuters.com/markets/", "a[data-testid='Heading']"),
    ("MarketWatch", "https://www.marketwatch.com/latest-news", "h3.article__headline a"),
    ("Fox Business", "https://www.foxbusiness.com/markets", "h2.title a"),
    ("WSJ", "https://www.wsj.com/news/markets", "h3 a"),
    ("Bloomberg", "https://www.bloomberg.com/markets", "article a"),
    ("Yahoo Finance", "https://finance.yahoo.com/", "h3 a"),
    ("Investing.com", "https://www.investing.com/news/stock-market-news", "a.title"),
]

COLLECTION = "headlines"
TTL_DAYS = 7


def _content_hash(text: str) -> str:
    """Deterministic hash for deduplication."""
    normalized = re.sub(r'\s+', ' ', text.lower().strip())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _clean_text(raw: str) -> str:
    """Clean and normalize headline text."""
    text = raw.strip()
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[^\w\s\-\.\,\!\?\$\%\&\'\"\:\;\/\@\#\+\=\(\)]', '', text)
    return text


def _scrape_headlines(url: str, selector: str) -> List[str]:
    """Scrape headlines from a URL using a CSS selector."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return []
        soup = BeautifulSoup(response.content, 'html.parser')
        elements = soup.select(selector)[:15]
        headlines = []
        for el in elements:
            text = el.get_text(strip=True)
            if text and len(text) > 10:
                headlines.append(text)
        return headlines
    except Exception as e:
        logger.debug(f"Scrape failed for {url}: {e}")
        return []


class HeadlinesPipeline:
    def __init__(self, db, sources: Optional[List[tuple]] = None):
        self.db = db
        self.sources = sources or DEFAULT_SOURCES

    async def run_cycle(self) -> Dict:
        """Execute one full scrape → clean → store cycle across all sources."""
        if self.db is None:
            return {"error": "No database connection"}

        total_scraped = 0
        total_new = 0
        total_dupes = 0
        source_stats = {}
        now = datetime.now(timezone.utc)

        for source_name, url, selector in self.sources:
            try:
                raw = await asyncio.to_thread(_scrape_headlines, url, selector)
                scraped = len(raw)
                total_scraped += scraped

                new_count = 0
                for headline in raw:
                    cleaned = _clean_text(headline)
                    if len(cleaned) < 10:
                        continue
                    content_hash = _content_hash(cleaned)

                    # Upsert: skip if hash already exists
                    result = await self.db[COLLECTION].update_one(
                        {"content_hash": content_hash},
                        {"$setOnInsert": {
                            "content_hash": content_hash,
                            "raw_text": headline,
                            "clean_text": cleaned,
                            "source": source_name,
                            "url": url,
                            "scraped_at": now,
                            "expires_at": now + timedelta(days=TTL_DAYS),
                        }},
                        upsert=True,
                    )
                    if result.upserted_id:
                        new_count += 1

                dupes = scraped - new_count
                total_new += new_count
                total_dupes += dupes
                source_stats[source_name] = {"scraped": scraped, "new": new_count, "dupes": dupes}

            except Exception as e:
                logger.warning(f"Headlines pipeline failed for {source_name}: {e}")
                source_stats[source_name] = {"scraped": 0, "new": 0, "error": str(e)[:100]}

        logger.info(f"Headlines pipeline: {total_scraped} scraped, {total_new} new, {total_dupes} dupes")
        return {
            "total_scraped": total_scraped,
            "total_new": total_new,
            "total_dupes": total_dupes,
            "sources": source_stats,
            "timestamp": now.isoformat(),
        }

    async def get_recent(self, hours: int = 24, limit: int = 200, source: Optional[str] = None) -> List[Dict]:
        """Fetch recent cleaned headlines from the store."""
        if self.db is None:
            return []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = {"scraped_at": {"$gt": cutoff}}
        if source:
            query["source"] = source
        cursor = self.db[COLLECTION].find(
            query,
            {"_id": 0, "content_hash": 0, "expires_at": 0},
        ).sort("scraped_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_for_prediction(self, hours: int = 6) -> str:
        """Get a formatted headline digest for the prediction engine."""
        headlines = await self.get_recent(hours=hours, limit=50)
        if not headlines:
            return ""
        lines = [f"=== RECENT HEADLINES (last {hours}h, {len(headlines)} articles) ==="]
        for h in headlines:
            lines.append(f"[{h.get('source', '?')}] {h.get('clean_text', '')}")
        return "\n".join(lines)

    async def stats(self) -> Dict:
        """Get pipeline statistics."""
        if self.db is None:
            return {"total": 0, "sources": {}}
        now = datetime.now(timezone.utc)
        total = await self.db[COLLECTION].count_documents({})
        last_24h = await self.db[COLLECTION].count_documents(
            {"scraped_at": {"$gt": now - timedelta(hours=24)}}
        )
        last_hour = await self.db[COLLECTION].count_documents(
            {"scraped_at": {"$gt": now - timedelta(hours=1)}}
        )

        # Per-source counts
        pipeline = [
            {"$group": {"_id": "$source", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        source_counts = {}
        async for doc in self.db[COLLECTION].aggregate(pipeline):
            source_counts[doc["_id"]] = doc["count"]

        return {
            "total": total,
            "last_24h": last_24h,
            "last_hour": last_hour,
            "sources": source_counts,
            "configured_sources": len(self.sources),
        }

    async def cleanup_expired(self) -> int:
        """Remove expired headlines."""
        if self.db is None:
            return 0
        result = await self.db[COLLECTION].delete_many(
            {"expires_at": {"$lt": datetime.now(timezone.utc)}}
        )
        return result.deleted_count
