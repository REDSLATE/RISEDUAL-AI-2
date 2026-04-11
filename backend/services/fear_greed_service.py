"""Fear & Greed Index service — historical data from MongoDB + live CNN scraping."""
import os
import logging
import asyncio
import requests
from typing import Dict, List
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

LABELS = [
    (0, 25, "Extreme Fear"),
    (25, 45, "Fear"),
    (45, 55, "Neutral"),
    (55, 75, "Greed"),
    (75, 100, "Extreme Greed"),
]


def _label(value: float) -> str:
    for lo, hi, label in LABELS:
        if lo <= value < hi:
            return label
    return "Extreme Greed" if value >= 75 else "Neutral"


class FearGreedService:
    def __init__(self):
        self.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        self.db = self.client[os.environ.get("DB_NAME", "risedual_db")]
        self.col = self.db["fear_greed_index"]

    async def get_current(self) -> Dict:
        """Get the most recent Fear & Greed reading (from DB or live scrape)."""
        live = await self._scrape_live()
        if live:
            return live

        # Fallback to latest DB record
        doc = await self.col.find_one(
            {}, {"_id": 0}, sort=[("date", -1)]
        )
        if doc:
            return {
                "value": doc["index"],
                "label": _label(doc["index"]),
                "date": doc["date"],
                "source": "historical_db",
            }
        return {"value": 50, "label": "Neutral", "date": "", "source": "default"}

    async def _scrape_live(self) -> Dict | None:
        """Scrape current Fear & Greed value from CNN."""
        try:
            url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = await asyncio.to_thread(
                requests.get, url, headers=headers, timeout=8
            )
            if resp.status_code == 200:
                data = resp.json()
                score = data.get("fear_and_greed", {}).get("score")
                if score is not None:
                    val = round(float(score), 2)
                    return {
                        "value": val,
                        "label": _label(val),
                        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        "source": "cnn_live",
                    }
        except Exception as e:
            logger.debug(f"CNN Fear & Greed scrape failed (non-critical): {e}")
        return None

    async def get_history(self, days: int = 90) -> List[Dict]:
        """Get historical Fear & Greed readings (latest N days)."""
        cursor = self.col.find(
            {}, {"_id": 0}
        ).sort("date", -1).limit(days)
        docs = await cursor.to_list(length=days)
        return list(reversed(docs))

    async def get_full_summary(self) -> Dict:
        """Get current + historical data for the dashboard widget."""
        current = await self.get_current()
        history = await self.get_history(90)

        # Compute 7-day and 30-day averages
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
