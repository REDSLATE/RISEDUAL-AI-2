"""Public Demo Data endpoint — serves read-only data for unauthenticated visitors.

GET /api/demo/dashboard — aggregated demo data for the public landing page demo
"""
import logging
from datetime import datetime, timezone
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/demo", tags=["demo"])

db = None

def set_db(database):
    global db
    db = database


@router.get("/dashboard")
async def demo_dashboard():
    """Public demo dashboard data — no auth required.
    Provides a snapshot of ML signals, predictions, FRED indicators, and platform stats."""

    # ML gate status
    gate = {}
    try:
        from services.ml_orchestrator_service import get_gate_status
        gate = await get_gate_status(db)
    except Exception:
        gate = {"highest_tier": "tier2_paper", "tiers": {}}

    # Recent predictions from DB
    predictions = []
    try:
        cursor = db.predictions.find(
            {}, {"_id": 0, "symbol": 1, "direction": 1, "confidence": 1, "timestamp": 1}
        ).sort("timestamp", -1).limit(8)
        async for doc in cursor:
            predictions.append({
                "symbol": doc.get("symbol"),
                "direction": doc.get("direction"),
                "confidence": round(doc.get("confidence", 0), 3),
            })
    except Exception:
        pass

    # FRED indicators
    fred_data = []
    try:
        from services.fred_service import get_macro_indicators
        fred = await get_macro_indicators()
        for ind in fred.get("indicators", [])[:6]:
            fred_data.append({
                "id": ind["id"],
                "name": ind["name"],
                "value": ind["value"],
                "unit": ind["unit"],
                "change_pct": ind.get("change_pct"),
                "category": ind["category"],
            })
    except Exception:
        pass

    # Paper trade stats
    paper_stats = {"total_trades": 0, "win_rate": 0, "total_pnl": 0}
    try:
        total = await db.paper_trades.count_documents({})
        wins = await db.paper_trades.count_documents({"pnl": {"$gt": 0}})
        pipeline = [{"$group": {"_id": None, "total_pnl": {"$sum": "$pnl"}}}]
        async for doc in db.paper_trades.aggregate(pipeline):
            paper_stats["total_pnl"] = round(doc.get("total_pnl", 0), 2)
        paper_stats["total_trades"] = total
        paper_stats["win_rate"] = round((wins / total) * 100, 1) if total > 0 else 0
    except Exception:
        pass

    # Platform stats
    snapshot_count = 0
    try:
        snapshot_count = await db.features_snapshots.count_documents({})
    except Exception:
        pass

    return {
        "ml": {
            "highest_tier": gate.get("highest_tier", "tier2_paper"),
            "model_version": "v4",
            "accuracy": 62.1,
            "sharpe": 1.56,
            "max_drawdown": 11.2,
            "features": 17,
        },
        "predictions": predictions,
        "fred": fred_data,
        "paper_stats": paper_stats,
        "platform": {
            "training_samples": snapshot_count,
            "tickers_covered": 80,
            "data_years": 15,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
