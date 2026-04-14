"""Web Intelligence Routes — Search endpoints for market research."""
import logging
from fastapi import APIRouter, Request, HTTPException, Query
from typing import Optional
from services.auth_helpers import get_current_user
from services import web_intelligence_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/web-intel", tags=["web-intelligence"])

db = None


def set_db(database):
    global db
    db = database


@router.get("/search")
async def web_search(request: Request, q: str = Query(..., min_length=2), max_results: int = Query(default=8, le=20)):
    """General web search — enriches AI context with fresh web data."""
    user = await get_current_user(request)

    from services.credit_service import deduct_credits, get_user_plan
    plan_key = get_user_plan(user)
    cr = await deduct_credits(str(user["_id"]), "web_search", plan_key)
    if not cr["allowed"]:
        raise HTTPException(status_code=402, detail=cr.get("error", "Not enough credits"))

    return await web_intelligence_service.search(q, max_results)


@router.get("/news/{symbol}")
async def ticker_news(request: Request, symbol: str, max_results: int = Query(default=10, le=20)):
    """Get recent news for a specific ticker symbol."""
    await get_current_user(request)
    return await web_intelligence_service.search_ticker_news(symbol.upper(), max_results)


@router.get("/research")
async def research(request: Request, topic: str = Query(..., min_length=3), max_results: int = Query(default=8, le=15)):
    """Deep research on a financial topic — returns AI-ready context."""
    user = await get_current_user(request)

    from services.credit_service import deduct_credits, get_user_plan
    plan_key = get_user_plan(user)
    cr = await deduct_credits(str(user["_id"]), "web_research", plan_key)
    if not cr["allowed"]:
        raise HTTPException(status_code=402, detail=cr.get("error", "Not enough credits"))

    return await web_intelligence_service.research_topic(topic, max_results)


@router.get("/status")
async def web_intel_status():
    """Check which web search providers are available."""
    return web_intelligence_service.is_configured()
