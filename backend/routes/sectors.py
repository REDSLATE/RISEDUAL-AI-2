"""Sector rotation heatmap route."""
from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Query
import logging

router = APIRouter(prefix="/api/sectors", tags=["sectors"])
logger = logging.getLogger(__name__)


@router.get("/heatmap")
async def get_sector_heatmap_endpoint(force: bool = Query(False)) -> Dict[str, Any]:
    """Return sector rotation heatmap data with multi-period returns (cached 2 min).
    Pass ?force=true to bypass cache and fetch fresh Alpha Vantage data."""
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
