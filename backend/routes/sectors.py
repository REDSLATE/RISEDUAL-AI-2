"""Sector rotation heatmap route."""
import os
import logging
from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/sectors", tags=["sectors"])
logger = logging.getLogger(__name__)


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
            # Get current sector price data first
            heatmap = await cache.get_or_fetch("sector_heatmap", get_sector_heatmap, ttl=120)
            sectors_data = heatmap.get("sectors", [])
            if not sectors_data:
                return {"error": "No sector data available"}
            return await run_sector_sentiment_crew(sectors_data, api_key)

        return await cache.get_or_fetch("sector_sentiment", _generate, ttl=900)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sector sentiment error: {e}")
        raise HTTPException(status_code=500, detail="Error generating sector sentiment")
