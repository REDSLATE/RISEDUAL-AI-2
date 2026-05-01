"""Fear & Greed Index service — historical + live data via alternative.me.

Why alternative.me over CNN
───────────────────────────
The previous implementation scraped CNN's `production.dataviz.cnn.io`
endpoint, which now returns HTTP 418 (their anti-bot defence) for any
request bearing the generic ``Mozilla/5.0`` User-Agent we were sending.
The scrape was failing silently (caught + logged at DEBUG only) which
made the dashboard fall through to a default ``{value: 50, source:
"default"}`` payload — the "stuck on neutral" bug the user reported.

alternative.me's free Fear & Greed API:
    * No auth, no rate limits, no User-Agent gymnastics
    * Returns up to ~ years of daily historical data in one call
    * Already used elsewhere in the codebase (`market_sentiment_service.get_fear_greed_index`)
    * Data is *crypto* F&G, not equity F&G — for the dashboard's
      "macro risk-on/risk-off" signal this is a reasonable proxy
      and consistent with the rest of the codebase

Auto-seeding
────────────
First call to ``get_full_summary`` (or to the dedicated ``ingest_history``
helper used by the scheduler) lazily upserts the most recent 730 days of
data into the ``fear_greed_index`` collection. On a cold-start prod DB
this guarantees the 7D / 30D averages compute correctly within seconds
of the first dashboard request, without requiring a one-shot manual
migration.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests
from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

# Bracket-style label thresholds (lower-inclusive, upper-exclusive).
LABELS = [
    (0, 25, "Extreme Fear"),
    (25, 45, "Fear"),
    (45, 55, "Neutral"),
    (55, 75, "Greed"),
    (75, 100, "Extreme Greed"),
]

FNG_API = "https://api.alternative.me/fng/"


def _label(value: float) -> str:
    for lo, hi, label in LABELS:
        if lo <= value < hi:
            return label
    return "Extreme Greed" if value >= 75 else "Neutral"


class FearGreedService:
    def __init__(self) -> None:
        self.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        self.db = self.client[os.environ.get("DB_NAME", "risedual_db")]
        self.col = self.db["fear_greed_index"]

    # ── Live + historical fetch ──────────────────────────────────────

    async def _fetch_alternative_me(self, limit: int = 1) -> list[dict[str, Any]]:
        """One-shot call to alternative.me. Returns raw entries or []."""
        try:
            resp = await asyncio.to_thread(
                requests.get,
                FNG_API,
                params={"limit": limit, "format": "json"},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(
                    f"alternative.me returned HTTP {resp.status_code}"
                )
                return []
            data = resp.json()
            return list(data.get("data") or [])
        except Exception as e:
            logger.warning(f"alternative.me fetch failed: {e}")
            return []

    @staticmethod
    def _entry_to_doc(entry: dict[str, Any]) -> dict[str, Any] | None:
        """Coerce an alternative.me entry into our Mongo schema."""
        try:
            ts = int(entry.get("timestamp") or 0)
            value = float(entry.get("value") or 0)
        except (TypeError, ValueError):
            return None
        if ts <= 0:
            return None
        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        return {"date": date_str, "index": value}

    async def get_current(self) -> dict[str, Any]:
        """Return the most recent reading (live API → DB → default)."""
        entries = await self._fetch_alternative_me(limit=1)
        if entries:
            doc = self._entry_to_doc(entries[0])
            if doc:
                # Best-effort upsert — keeps the historical store fresh
                # without forcing an extra ingestion call.
                try:
                    await self.col.update_one(
                        {"date": doc["date"]},
                        {"$set": doc},
                        upsert=True,
                    )
                except Exception as e:
                    logger.debug(f"Live upsert failed (non-critical): {e}")
                return {
                    "value": doc["index"],
                    "label": _label(doc["index"]),
                    "date": doc["date"],
                    "source": "alternative_me_live",
                }

        # Fallback to latest stored record.
        stored: dict | None = await self.col.find_one(
            {}, {"_id": 0}, sort=[("date", -1)]
        )
        if stored:
            return {
                "value": stored["index"],
                "label": _label(stored["index"]),
                "date": stored["date"],
                "source": "historical_db",
            }
        return {"value": 50, "label": "Neutral", "date": "", "source": "default"}

    # ── Historical ──────────────────────────────────────────────────

    async def get_history(self, days: int = 90) -> list[dict[str, Any]]:
        """Return up to N days of stored history, oldest → newest."""
        cursor = self.col.find({}, {"_id": 0}).sort("date", -1).limit(days)
        docs = await cursor.to_list(length=days)
        return list(reversed(docs))

    async def ingest_history(self, days: int = 730) -> int:
        """Pull N days of historical data from alternative.me and
        upsert into Mongo. Returns the number of rows written.

        Idempotent: re-running on a fully-populated store is cheap
        (every doc resolves to a no-op upsert).
        """
        entries = await self._fetch_alternative_me(limit=days)
        if not entries:
            return 0
        written = 0
        for e in entries:
            doc = self._entry_to_doc(e)
            if not doc:
                continue
            try:
                await self.col.update_one(
                    {"date": doc["date"]},
                    {"$set": doc},
                    upsert=True,
                )
                written += 1
            except Exception as exc:
                logger.debug(f"Upsert {doc.get('date')} failed: {exc}")
        if written:
            logger.info(f"Fear/Greed history: ingested/refreshed {written} rows")
        return written

    # ── Dashboard summary ───────────────────────────────────────────

    async def get_full_summary(self) -> dict[str, Any]:
        """Get current + historical data for the dashboard widget.

        Lazy-seeds the historical store on first call so a cold-start
        deployment populates within seconds of the first request.
        """
        # If the store is empty, seed before computing averages —
        # otherwise the user sees a "0 EXTREME FEAR" bug on day one.
        total = await self.col.estimated_document_count()
        if total == 0:
            try:
                await self.ingest_history(days=730)
            except Exception as e:
                logger.warning(f"Lazy F&G seed failed: {e}")

        current = await self.get_current()
        history = await self.get_history(90)

        # Compute 7-day and 30-day averages from the most-recent slice.
        # ``history`` is oldest → newest, so trailing windows live at
        # the *end* of the list.
        recent_7 = history[-7:] if len(history) >= 7 else history
        recent_30 = history[-30:] if len(history) >= 30 else history
        avg_7 = round(sum(d["index"] for d in recent_7) / len(recent_7), 1) if recent_7 else 0
        avg_30 = round(sum(d["index"] for d in recent_30) / len(recent_30), 1) if recent_30 else 0

        return {
            "current": current,
            "avg_7d": avg_7,
            "avg_7d_label": _label(avg_7),
            "avg_30d": avg_30,
            "avg_30d_label": _label(avg_30),
            "history": history,
            "total_records": await self.col.count_documents({}),
        }


# ── Module-level helper for the APScheduler daily refresh job ──────


async def refresh_fear_greed_history() -> int:
    """Daily-job entry point.

    Pulls the last 30 days from alternative.me and upserts them. We
    re-fetch a 30-day window (not just 1 day) because alternative.me
    occasionally backfills/corrects prior days, and overwriting our
    store with their canonical values is cheap and self-healing.
    """
    return await FearGreedService().ingest_history(days=30)
