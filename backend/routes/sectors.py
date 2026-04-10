"""Sector rotation heatmap route."""
import os
import logging
from typing import Any, Dict, List
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/sectors", tags=["sectors"])
logger = logging.getLogger(__name__)
_db = None

def set_db(database):
    global _db
    _db = database


@router.get("/heatmap")
async def get_sector_heatmap_endpoint(force: bool = Query(False)) -> Dict[str, Any]:
    """Return sector rotation heatmap data with multi-period returns (cached 2 min).
    Pass ?force=true to bypass cache and fetch fresh data."""
    try:
        from services.cache import cache
        from services.sector_service import get_sector_heatmap
        if force:
            cache.invalidate("sector_heatmap")
            logger.info("Sector heatmap cache invalidated — fetching fresh data")
        return await cache.get_or_fetch("sector_heatmap", get_sector_heatmap, ttl=120)
    except Exception as e:
        logger.error(f"Sector heatmap error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching sector data")


@router.get("/sentiment")
async def get_sector_sentiment_endpoint(force: bool = Query(False)) -> Dict[str, Any]:
    """AI-powered sector sentiment analysis using multi-agent crew.
    Cached 15 min. Pass ?force=true to regenerate."""
    try:
        from services.cache import cache
        from services.sector_service import get_sector_heatmap
        from services.crew_definitions import run_sector_sentiment_crew

        api_key = os.environ.get("EMERGENT_LLM_KEY", "")
        if not api_key:
            raise HTTPException(status_code=500, detail="AI service not configured")

        if force:
            cache.invalidate("sector_sentiment")
            logger.info("Sector sentiment cache invalidated — regenerating")

        async def _generate():
            heatmap = await cache.get_or_fetch("sector_heatmap", get_sector_heatmap, ttl=120)
            sectors_data = heatmap.get("sectors", [])
            if not sectors_data:
                return {"error": "No sector data available"}
            result = await run_sector_sentiment_crew(sectors_data, api_key)

            # Log to history
            if _db is not None and result.get("sectors"):
                try:
                    snapshot = {
                        "sectors": result["sectors"],
                        "rotation_call": result.get("rotation_call", ""),
                        "risk_regime": result.get("risk_regime", "mixed"),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await _db.sentiment_history.insert_one(snapshot)
                    logger.info("Sentiment snapshot saved to history")
                except Exception as e:
                    logger.warning(f"Failed to save sentiment history: {e}")

            return result

        return await cache.get_or_fetch("sector_sentiment", _generate, ttl=900)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sector sentiment error: {e}")
        raise HTTPException(status_code=500, detail="Error generating sector sentiment")


@router.get("/sentiment/history")
async def get_sentiment_history(limit: int = Query(20, ge=1, le=100)) -> Dict[str, Any]:
    """Return historical sentiment snapshots for trend analysis."""
    if _db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    cursor = _db.sentiment_history.find(
        {}, {"_id": 0}
    ).sort("created_at", -1).limit(limit)

    snapshots = []
    async for doc in cursor:
        snapshots.append(doc)

    # Build per-sector timeseries (oldest first for charting)
    snapshots.reverse()
    sector_trends = {}
    for snap in snapshots:
        ts = snap.get("created_at", "")
        for sym, data in snap.get("sectors", {}).items():
            if sym not in sector_trends:
                sector_trends[sym] = []
            sector_trends[sym].append({
                "timestamp": ts,
                "score": data.get("score", 0),
                "heatmap_value": data.get("heatmap_value", 50),
                "label": data.get("label", "Neutral"),
            })

    return {
        "snapshots_count": len(snapshots),
        "sector_trends": sector_trends,
        "latest_rotation_call": snapshots[-1].get("rotation_call", "") if snapshots else "",
        "latest_risk_regime": snapshots[-1].get("risk_regime", "mixed") if snapshots else "mixed",
    }
