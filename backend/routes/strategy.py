"""Strategy Builder routes — AI-powered trading strategy generation."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import os
import logging
from datetime import datetime, timezone

from services.auth_helpers import get_current_user, is_pro_user

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

db = None

def set_db(database):
    global db
    db = database


class StrategyRequest(BaseModel):
    description: str
    model: str = "gpt-5.2"


class StrategySaveRequest(BaseModel):
    strategy: dict
    description: str


# --- Generate Strategy ---
@router.post("/strategy/generate")
async def generate_strategy_endpoint(req: StrategyRequest, request: Request):
    """Generate a structured trading strategy from plain English."""
    user = await get_current_user(request)

    if not req.description.strip():
        raise HTTPException(status_code=400, detail="Please describe your trading strategy.")

    # Free users: GPT-5.2 only, basic strategies
    is_pro = is_pro_user(user)
    model = req.model if is_pro else "gpt-5.2"

    try:
        from services.strategy_service import generate_strategy
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        strategy = await generate_strategy(api_key, req.description, model=model)

        if strategy.get("error"):
            raise HTTPException(status_code=500, detail=strategy.get("summary", "Strategy generation failed"))

        return {
            "strategy": strategy,
            "is_pro": is_pro,
            "model_used": model,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Strategy generation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate strategy. Please try again.")


# --- Save Strategy ---
@router.post("/strategy/save")
async def save_strategy(req: StrategySaveRequest, request: Request):
    """Save a generated strategy to user's account."""
    user = await get_current_user(request)

    # Free users can save up to 3 strategies
    if not is_pro_user(user):
        count = await db.strategies.count_documents({"user_id": user["_id"]})
        if count >= 3:
            raise HTTPException(status_code=403, detail="Free users can save up to 3 strategies. Upgrade to Pro for unlimited.")

    doc = {
        "user_id": user["_id"],
        "name": req.strategy.get("name", "Untitled Strategy"),
        "description": req.description,
        "strategy": req.strategy,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.strategies.insert_one(doc)

    return {"message": "Strategy saved", "name": doc["name"]}


# --- List Strategies ---
@router.get("/strategy/list")
async def list_strategies(request: Request):
    """Get all saved strategies for the current user."""
    user = await get_current_user(request)

    strategies = []
    cursor = db.strategies.find(
        {"user_id": user["_id"]},
        {"_id": 0, "user_id": 0}
    ).sort("created_at", -1)

    async for doc in cursor:
        strategies.append(doc)

    return {"strategies": strategies, "count": len(strategies)}


# --- Delete Strategy ---
@router.delete("/strategy/{name}")
async def delete_strategy(name: str, request: Request):
    """Delete a saved strategy by name."""
    user = await get_current_user(request)

    result = await db.strategies.delete_one({"user_id": user["_id"], "name": name})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Strategy not found")

    return {"message": "Strategy deleted"}


# --- Code Quality Score (Admin) ---
@router.get("/admin/code-quality")
async def get_code_quality(request: Request):
    """Get code quality score for the admin panel."""
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    from services.strategy_service import get_code_quality_score
    return await get_code_quality_score(db)
