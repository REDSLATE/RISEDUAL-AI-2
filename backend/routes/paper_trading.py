"""Paper Trading Routes — simulated buy/sell with real prices."""
import logging
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from services.auth_helpers import get_current_user
from services import paper_trading_service as pts

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/paper", tags=["paper-trading"])

_db = None


def set_db(database):
    global _db
    _db = database
    pts.set_db(database)


class TradeRequest(BaseModel):
    symbol: str
    side: str  # BUY or SELL
    qty: float = Field(gt=0)


@router.get("/portfolio")
async def get_portfolio(request: Request):
    """Get paper portfolio snapshot with live prices."""
    user = await get_current_user(request)
    snapshot = await pts.get_portfolio_snapshot(user["_id"])
    return snapshot


@router.post("/trade")
async def execute_trade(request: Request, trade: TradeRequest):
    """Execute a paper trade."""
    user = await get_current_user(request)
    result = await pts.execute_trade(user["_id"], trade.symbol, trade.side, trade.qty)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/trades")
async def get_trades(request: Request, symbol: str = None, limit: int = 50):
    """Get paper trade history."""
    user = await get_current_user(request)
    trades = await pts.get_trade_history(user["_id"], symbol=symbol, limit=limit)
    return trades


@router.post("/reset")
async def reset_portfolio(request: Request):
    """Reset paper portfolio to starting state."""
    user = await get_current_user(request)
    result = await pts.reset_portfolio(user["_id"])
    return result
