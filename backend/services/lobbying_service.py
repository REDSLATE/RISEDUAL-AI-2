"""Lobbying data service — queries imported lobbying disclosure data from MongoDB."""
import os
import logging

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)


class LobbyingService:
    def __init__(self):
        self.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        self.db = self.client[os.environ.get("DB_NAME", "risedual_db")]
        self.col = self.db["lobbying_data"]

    async def get_recent_lobbying(self, limit: int = 25) -> list[dict]:
        """Get most recent lobbying disclosures."""
        cursor = self.col.find(
            {"amount": {"$gt": 0}},
            {"_id": 0},
        ).sort("date", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_lobbying_by_ticker(self, ticker: str, limit: int = 20) -> list[dict]:
        """Get lobbying activity for a specific ticker."""
        cursor = self.col.find(
            {"ticker": ticker.upper(), "amount": {"$gt": 0}},
            {"_id": 0},
        ).sort("date", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_top_spenders(self, limit: int = 15) -> list[dict]:
        """Get top lobbying spenders by total amount."""
        pipeline = [
            {"$match": {"amount": {"$gt": 0}}},
            {"$group": {
                "_id": "$ticker",
                "total_amount": {"$sum": "$amount"},
                "filing_count": {"$sum": 1},
                "client": {"$first": "$client"},
                "latest_issue": {"$first": "$issue"},
                "latest_date": {"$max": "$date"},
            }},
            {"$sort": {"total_amount": -1}},
            {"$limit": limit},
        ]
        results = await self.col.aggregate(pipeline).to_list(length=limit)
        return [
            {
                "ticker": r["_id"],
                "total_amount": r["total_amount"],
                "filing_count": r["filing_count"],
                "client": r["client"],
                "latest_issue": r["latest_issue"],
                "latest_date": r["latest_date"],
            }
            for r in results
        ]

    async def get_lobbying_by_issue(self, keyword: str, limit: int = 20) -> list[dict]:
        """Search lobbying by issue keyword (e.g., 'tariff', 'healthcare')."""
        cursor = self.col.find(
            {"issue": {"$regex": keyword, "$options": "i"}, "amount": {"$gt": 0}},
            {"_id": 0},
        ).sort("amount", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_summary(self) -> dict:
        """Get aggregate lobbying summary for dashboard display."""
        total_docs = await self.col.count_documents({"amount": {"$gt": 0}})
        top = await self.get_top_spenders(10)
        recent = await self.get_recent_lobbying(10)

        # Top issues
        issue_pipeline = [
            {"$match": {"amount": {"$gt": 0}}},
            {"$group": {"_id": "$issue", "total": {"$sum": "$amount"}, "count": {"$sum": 1}}},
            {"$sort": {"total": -1}},
            {"$limit": 5},
        ]
        top_issues = await self.col.aggregate(issue_pipeline).to_list(length=5)

        return {
            "total_filings": total_docs,
            "top_spenders": top,
            "recent_filings": recent,
            "top_issues": [
                {"issue": i["_id"][:80], "total_amount": i["total"], "count": i["count"]}
                for i in top_issues
            ],
        }
