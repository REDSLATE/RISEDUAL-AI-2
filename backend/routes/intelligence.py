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


async def _check_credits(user, action: str):
    """Check and deduct credits. Raises HTTPException on insufficient credits."""
    from services.credit_service import deduct_credits, get_user_plan
    user_id = str(user["_id"])
    plan_key = get_user_plan(user)
    result = await deduct_credits(user_id, action, plan_key)
    if not result["allowed"]:
        raise HTTPException(
            status_code=402,
            detail=result.get("error", "Not enough credits"),
        )
    return result


@router.get("/intelligence/score/{symbol}")
async def ai_score(symbol: str, request: Request):
    """AI Stock Score (1-10) with technical, fundamental, sentiment breakdown."""
    user = await get_current_user(request)
    await _check_credits(user, "intelligence")
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
    user = await get_current_user(request)
    await _check_credits(user, "intelligence")
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



@router.get("/intelligence/war-room/{symbol}")
async def war_room(symbol: str, request: Request):
    """AI War Room — unified command center analysis for a single stock."""
    user = await get_current_user(request)
    # Credit check for War Room (Pro gets FREE)
    await _check_credits(user, "war_room")
    try:
        from services.war_room_service import generate_war_room
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        result = await generate_war_room(symbol.upper(), api_key)

        # Log prediction for accuracy tracking
        composite = result.get("composite", {})
        if composite.get("verdict") and db is not None:
            try:
                from services.prediction_tracker import log_prediction
                await log_prediction(
                    db, "war_room", symbol.upper(),
                    composite["verdict"],
                    composite.get("confidence", 0),
                    composite.get("score", 0),
                    user_id=str(user.get("_id", ""))
                )
            except Exception as track_err:
                logger.warning(f"Prediction tracking failed: {track_err}")

        # Cache for 5 minutes
        if db is not None:
            await db.war_room_cache.update_one(
                {"symbol": symbol.upper()},
                {"$set": {**result, "cached_at": result["generated_at"]}},
                upsert=True,
            )

        return result
    except Exception as e:
        logger.error(f"War Room error for {symbol}: {e}")
        raise HTTPException(status_code=500, detail="War Room analysis failed. Try again.")
