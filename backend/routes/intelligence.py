"""AI Intelligence routes — Stock Scoring, Pattern Recognition, Quick Briefs."""
from fastapi import APIRouter, HTTPException, Request
import os
import logging

from services.auth_helpers import get_current_user

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


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
