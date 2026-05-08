"""Web Intelligence Routes — Search War Room + single-engine endpoints."""
import logging
from fastapi import APIRouter, Request, Query
from services.auth_helpers import get_current_user, enforce_credits
from services import web_intelligence_service
from services.search_war_room.orchestrator import run_search
from services.search_war_room.schemas import SearchWarRoomRequest
from services.search_war_room.cache import cache_status

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/web-intel", tags=["web-intelligence"])

db = None


def set_db(database):
    global db
    db = database


@router.post("/war-room")
async def search_war_room(request: Request, payload: SearchWarRoomRequest):
    """Search War Room — fires all engines in parallel, returns unified brief."""
    user = await get_current_user(request)
    await enforce_credits(user, "web_search")
    return await run_search(query=payload.query, symbol=payload.symbol, mode=payload.mode)


@router.get("/search")
async def web_search(request: Request, q: str = Query(..., min_length=2), max_results: int = Query(default=8, le=20)):
    """Simple web search via DuckDuckGo."""
    user = await get_current_user(request)
    await enforce_credits(user, "web_search")
    return await web_intelligence_service.search(q, max_results)


@router.get("/news/{symbol}")
async def ticker_news(request: Request, symbol: str, max_results: int = Query(default=10, le=20)):
    """Get recent news for a specific ticker symbol."""
    await get_current_user(request)
    return await web_intelligence_service.search_ticker_news(symbol.upper(), max_results)


@router.get("/research")
async def research(request: Request, topic: str = Query(..., min_length=3), max_results: int = Query(default=8, le=15)):
    """Deep research on a financial topic."""
    user = await get_current_user(request)
    await enforce_credits(user, "web_research")
    return await web_intelligence_service.research_topic(topic, max_results)


@router.get("/cache/status")
async def war_room_cache():
    """Check Search War Room cache status."""
    return cache_status()


@router.get("/status")
async def web_intel_status():
    """Full status: registry providers + AI pool + market pool."""
    from services.search_war_room.registry import registry_status
    from services.ai_pool import ai_pool_status
    from services.market_data_pool import market_pool_status
    return {
        "war_room_registry": registry_status(),
        "ai_pool": ai_pool_status(),
        "market_data_pool": market_pool_status(),
    }
