"""Failure Loop routes — trade idea memory, review, patterns, and warnings."""
import logging
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Literal

from services.auth_helpers import get_current_user
from services import failure_loop_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/failure-loop", tags=["failure-loop"])
db = None


def set_db(database):
    global db
    db = database
    failure_loop_service.set_db(database)


class TradeIdeaCreate(BaseModel):
    symbol: str
    direction: Literal["long", "short"]
    thesis: str
    confidence: float = Field(ge=0, le=1)
    source: str = "ai"
    tags: list = Field(default_factory=list)


class TradeOutcomeReview(BaseModel):
    idea_id: str
    outcome: Literal["win", "loss", "mixed", "invalid"]
    pnl: float = 0
    reason_tags: list = Field(default_factory=list)
    notes: Optional[str] = None
    approved_for_learning: bool = False


@router.post("/ideas")
async def post_trade_idea(payload: TradeIdeaCreate, request: Request):
    """Store a new trade idea."""
    user = await get_current_user(request)
    user_id = str(user["_id"])
    idea = await failure_loop_service.create_trade_idea(
        user_id, payload.symbol, payload.direction, payload.thesis,
        payload.confidence, payload.source, payload.tags,
    )
    return idea


@router.get("/ideas")
async def get_ideas(request: Request, status: Optional[str] = None, limit: int = 20):
    """Get user's trade ideas."""
    user = await get_current_user(request)
    return {"ideas": await failure_loop_service.get_user_ideas(str(user["_id"]), status, limit)}


@router.post("/review")
async def post_review(payload: TradeOutcomeReview, request: Request):
    """Review a trade outcome."""
    user = await get_current_user(request)
    try:
        result = await failure_loop_service.review_trade_outcome(
            str(user["_id"]), payload.idea_id, payload.outcome,
            payload.pnl, payload.reason_tags, payload.notes, payload.approved_for_learning,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/timeline")
async def get_timeline(request: Request, limit: int = 30):
    """Get user's event timeline."""
    user = await get_current_user(request)
    return {"events": await failure_loop_service.get_user_timeline(str(user["_id"]), limit)}


@router.get("/patterns")
async def get_patterns(request: Request):
    """Get summarized failure patterns."""
    user = await get_current_user(request)
    return {"patterns": await failure_loop_service.summarize_failure_patterns(str(user["_id"]))}


@router.get("/warnings")
async def get_warnings(request: Request):
    """Get memory warnings for chat injection."""
    user = await get_current_user(request)
    return {"warnings": await failure_loop_service.build_memory_warnings(str(user["_id"]))}


@router.get("/reason-tags")
async def get_reason_tags():
    """Get available reason tags."""
    return {"tags": failure_loop_service.REASON_TAGS}
