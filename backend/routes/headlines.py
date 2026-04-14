"""Headlines Pipeline routes — stats, manual trigger, recent headlines."""
from fastapi import APIRouter, Request, HTTPException, Query
from services.auth_helpers import get_current_user
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/headlines")
db = None


def set_db(database):
    global db
    db = database


@router.get("/stats")
async def headlines_stats(request: Request):
    """Get headline pipeline statistics (admin only)."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    from services.headlines_pipeline import HeadlinesPipeline
    pipeline = HeadlinesPipeline(db)
    return await pipeline.stats()


@router.post("/run")
async def run_pipeline(request: Request):
    """Manually trigger a headlines scrape cycle (admin only)."""
    user = await get_current_user(request)
    if user.get("role") not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    from services.headlines_pipeline import HeadlinesPipeline
    pipeline = HeadlinesPipeline(db)
    return await pipeline.run_cycle()


@router.get("/recent")
async def recent_headlines(
    request: Request,
    hours: int = Query(default=24, le=168),
    limit: int = Query(default=50, le=200),
    source: str = Query(default=None),
):
    """Fetch recent scraped headlines."""
    await get_current_user(request)
    from services.headlines_pipeline import HeadlinesPipeline
    pipeline = HeadlinesPipeline(db)
    headlines = await pipeline.get_recent(hours=hours, limit=limit, source=source)
    return {"headlines": headlines, "count": len(headlines)}
