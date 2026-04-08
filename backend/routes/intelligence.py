"""AI Intelligence routes — Stock Scoring, Pattern Recognition, Quick Briefs, Watchlist Intelligence."""
from fastapi import APIRouter, HTTPException, Request, Query
import os
import logging

from services.auth_helpers import get_current_user

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

# Module-level db reference, set by server.py on startup
db = None

def set_db(database):
    global db
    db = database


@router.get("/intelligence/score/{symbol}")
async def ai_score(symbol: str, request: Request):
    """AI Stock Score (1-10) with technical, fundamental, sentiment breakdown."""
    await get_current_user(request)
    try:
        from services.ai_intelligence_service import generate_ai_score
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        return await generate_ai_score(api_key, symbol.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"AI Score error for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate AI score. Try again.")


@router.get("/intelligence/patterns/{symbol}")
async def pattern_recognition(symbol: str, request: Request):
    """Detect chart patterns on a ticker."""
    await get_current_user(request)
    try:
        from services.ai_intelligence_service import detect_patterns
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        return await detect_patterns(api_key, symbol.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Pattern detection error for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Failed to detect patterns. Try again.")


@router.get("/intelligence/brief/{symbol}")
async def quick_brief(symbol: str, request: Request):
    """30-second quick stock brief with verdict."""
    await get_current_user(request)
    try:
        from services.ai_intelligence_service import generate_quick_brief
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        return await generate_quick_brief(api_key, symbol.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Quick brief error for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate brief. Try again.")


@router.get("/intelligence/watchlist")
async def watchlist_intelligence(request: Request, refresh: bool = Query(False)):
    """Generate AI intelligence summary for the user's entire watchlist."""
    user = await get_current_user(request)
    try:
        # Get user's watchlist
        wl = await db.watchlists.find_one({"user_id": user["_id"]}, {"_id": 0, "tickers": 1})
        tickers = wl.get("tickers", []) if wl else []

        # Also check localStorage-synced watchlist from query if empty
        if not tickers:
            return {
                "tickers": [],
                "summary": {"headline": "Empty Watchlist", "outlook": "Add tickers to your watchlist to get AI intelligence.", "health_score": 0},
                "top_movers": [],
                "alerts": [],
                "generated_at": None,
            }

        # Clear cache if refresh requested
        if refresh and db is not None:
            await db.watchlist_intelligence.delete_one({"cache_key": f"wl_intel_{user['_id']}"})

        from services.watchlist_intelligence_service import generate_watchlist_summary
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        return await generate_watchlist_summary(api_key, tickers, db=db, user_id=user["_id"])
    except Exception as e:
        logger.error(f"Watchlist intelligence error: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate watchlist intelligence. Try again.")
