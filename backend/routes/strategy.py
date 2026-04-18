"""Strategy Builder routes — AI-powered trading strategy generation + Marketplace."""
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel
import os
import logging
import uuid
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


class BacktestRequest(BaseModel):
    strategy: dict
    symbol: str
    years: int = 3


class PublishRequest(BaseModel):
    strategy: dict
    description: str
    backtest_symbol: str
    backtest_years: int
    backtest_metrics: dict


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


# --- Backtest Strategy ---
@router.post("/strategy/backtest")
async def backtest_strategy(req: BacktestRequest, request: Request):
    """Run a backtest simulation on a strategy with historical price data."""
    await get_current_user(request)  # auth check

    if not req.symbol.strip():
        raise HTTPException(status_code=400, detail="Symbol is required")
    if req.years < 1 or req.years > 5:
        raise HTTPException(status_code=400, detail="Timeframe must be 1-5 years")

    try:
        from services.backtester_service import run_backtest
        api_key = os.environ.get("EMERGENT_LLM_KEY")
        result = await run_backtest(api_key, req.strategy, req.symbol.strip(), req.years)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Backtest error: {e}")
        raise HTTPException(status_code=500, detail="Backtest simulation failed. Please try again.")


# --- Code Quality Score (Admin) ---
@router.get("/admin/code-quality")
async def get_code_quality(request: Request):
    """Get code quality score for the admin panel."""
    user = await get_current_user(request)
    if user.get("role") not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Admin access required")

    from services.strategy_service import get_code_quality_score
    return await get_code_quality_score(db)


# ═══════════════════════════════════════════════
# STRATEGY MARKETPLACE
# ═══════════════════════════════════════════════

@router.post("/marketplace/publish")
async def publish_to_marketplace(req: PublishRequest, request: Request):
    """Publish a strategy with backtest results to the marketplace. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required to publish strategies.")

    strategy_id = str(uuid.uuid4())[:12]
    doc = {
        "strategy_id": strategy_id,
        "author_id": user["_id"],
        "author_name": user.get("name") or user.get("email", "").split("@")[0],
        "strategy": req.strategy,
        "description": req.description,
        "backtest": {
            "symbol": req.backtest_symbol.upper(),
            "years": req.backtest_years,
            "metrics": req.backtest_metrics,
        },
        "views": 0,
        "clones": 0,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.marketplace_strategies.insert_one(doc)
    return {"message": "Strategy published to marketplace", "strategy_id": strategy_id}


@router.get("/marketplace/list")
async def list_marketplace(
    request: Request,
    sort: str = Query("newest", regex="^(newest|win_rate|pnl|clones)$"),
    limit: int = Query(30, ge=1, le=100),
    skip: int = Query(0, ge=0),
):
    """Browse marketplace strategies. Public endpoint (no auth required)."""
    sort_map = {
        "newest": ("published_at", -1),
        "win_rate": ("backtest.metrics.win_rate", -1),
        "pnl": ("backtest.metrics.total_pnl", -1),
        "clones": ("clones", -1),
    }
    sort_field, sort_dir = sort_map.get(sort, ("published_at", -1))

    strategies = []
    cursor = db.marketplace_strategies.find(
        {}, {"_id": 0, "author_id": 0}
    ).sort(sort_field, sort_dir).skip(skip).limit(limit)

    async for doc in cursor:
        strategies.append(doc)

    total = await db.marketplace_strategies.count_documents({})
    return {"strategies": strategies, "total": total}


@router.get("/marketplace/{strategy_id}")
async def get_marketplace_strategy(strategy_id: str, request: Request):
    """Get full details of a marketplace strategy. Increments view count."""
    doc = await db.marketplace_strategies.find_one(
        {"strategy_id": strategy_id},
        {"_id": 0, "author_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Strategy not found")

    await db.marketplace_strategies.update_one(
        {"strategy_id": strategy_id},
        {"$inc": {"views": 1}}
    )
    return doc


@router.post("/marketplace/{strategy_id}/clone")
async def clone_marketplace_strategy(strategy_id: str, request: Request):
    """Clone a marketplace strategy into the user's saved strategies. Pro only."""
    user = await get_current_user(request)
    if not is_pro_user(user):
        raise HTTPException(status_code=403, detail="Pro subscription required to clone strategies.")

    source = await db.marketplace_strategies.find_one({"strategy_id": strategy_id})
    if not source:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # Save to user's strategies
    doc = {
        "user_id": user["_id"],
        "name": source["strategy"].get("name", "Cloned Strategy"),
        "description": source.get("description", ""),
        "strategy": source["strategy"],
        "cloned_from": strategy_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.strategies.insert_one(doc)

    # Increment clone counter
    await db.marketplace_strategies.update_one(
        {"strategy_id": strategy_id},
        {"$inc": {"clones": 1}}
    )

    return {"message": "Strategy cloned to your account", "name": doc["name"]}
