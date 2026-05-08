"""FRED Economic Data routes.

GET /api/fred/indicators       — curated macro indicators (GDP, CPI, rates, etc.)
GET /api/fred/series/{id}      — detailed observations for any FRED series
GET /api/fred/release/{id}     — all series in a FRED release
GET /api/fred/search?q=...     — search FRED series
"""
import logging
from fastapi import APIRouter, HTTPException, Query

from services.fred_service import get_macro_indicators, get_series_detail, get_release_series, search_series, get_vintage_comparison, detect_revisions

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


@router.get("/snapshots")
async def fred_snapshots(limit: int = Query(30, le=365)):
    """Get stored daily FRED snapshots from MongoDB."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    cursor = db.fred_snapshots.find({}, {"_id": 0}).sort("date", -1).limit(limit)
    snapshots = []
    async for doc in cursor:
        snapshots.append(doc)
    return {"snapshots": snapshots, "count": len(snapshots)}


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


@router.get("/vintage/{series_id}")
async def fred_vintage(series_id: str, dates: str = Query(..., description="Comma-separated YYYY-MM-DD vintage dates")):
    """ALFRED vintage comparison — see how data looked on specific past dates vs. current revisions.

    Example: /api/fred/vintage/GDP?dates=2025-01-01,2025-07-01,2026-01-01
    """
    date_list = [d.strip() for d in dates.split(",") if d.strip()]
    if not date_list or len(date_list) > 10:
        raise HTTPException(status_code=400, detail="Provide 1-10 comma-separated dates (YYYY-MM-DD)")
    data = await get_vintage_comparison(series_id, date_list)
    if data.get("error"):
        raise HTTPException(status_code=503, detail=data["error"])
    return data


@router.get("/revisions")
async def fred_revisions():
    """Detect data revisions — compare current FRED values against last stored snapshot."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    revisions = await detect_revisions(db)
    return {
        "revisions": revisions,
        "count": len(revisions),
        "has_revisions": len(revisions) > 0,
    }
