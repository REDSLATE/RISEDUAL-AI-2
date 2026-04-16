"""FRED Economic Data routes.

GET /api/fred/indicators       — curated macro indicators (GDP, CPI, rates, etc.)
GET /api/fred/series/{id}      — detailed observations for any FRED series
GET /api/fred/release/{id}     — all series in a FRED release
GET /api/fred/search?q=...     — search FRED series
"""
import logging
from fastapi import APIRouter, HTTPException, Query

from services.fred_service import get_macro_indicators, get_series_detail, get_release_series, search_series

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/fred", tags=["fred"])

db = None

def set_db(database):
    global db
    db = database


@router.get("/indicators")
async def fred_indicators():
    """Get curated macro economic indicators with latest values and trends."""
    data = await get_macro_indicators()
    if data.get("error"):
        raise HTTPException(status_code=503, detail=data["error"])
    return data


@router.get("/series/{series_id}")
async def fred_series(series_id: str, limit: int = Query(60, le=500)):
    """Get detailed observations for a specific FRED series."""
    data = await get_series_detail(series_id.upper(), limit=limit)
    if data.get("error"):
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@router.get("/release/{release_id}")
async def fred_release(release_id: int, limit: int = Query(50, le=200)):
    """Get all series in a FRED release."""
    data = await get_release_series(release_id, limit=limit)
    if data.get("error"):
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@router.get("/search")
async def fred_search(q: str = Query(..., min_length=2), limit: int = Query(20, le=100)):
    """Search FRED series by keyword."""
    data = await search_series(q, limit=limit)
    if data.get("error"):
        raise HTTPException(status_code=503, detail=data["error"])
    return data
