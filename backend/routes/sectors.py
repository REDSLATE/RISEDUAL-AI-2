"""Sector rotation heatmap route."""
from fastapi import APIRouter, HTTPException
import logging

router = APIRouter(prefix="/api/sectors", tags=["sectors"])
logger = logging.getLogger(__name__)


@router.get("/heatmap")
async def get_sector_heatmap():
    """Return sector rotation heatmap data with multi-period returns."""
    try:
        from services.sector_service import get_sector_heatmap
        return await get_sector_heatmap()
    except Exception as e:
        logger.error(f"Sector heatmap error: {e}")
        raise HTTPException(status_code=500, detail="Error fetching sector data")
