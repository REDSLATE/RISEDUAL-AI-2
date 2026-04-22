"""Paper Trading Routes — simulated buy/sell with real prices.

Two asset classes share the same endpoint (`/api/paper/trade`) with
a discriminator payload:

    {"symbol": "AAPL", "side": "BUY", "qty": 10}
        → equity trade (legacy, unchanged)

    {"symbol": "ASHR", "side": "buy_to_open", "qty": 1,
     "option": {"strike": 38, "expiry": "2026-06-18",
                "type": "call", "iv_percent": 18.92}}
        → option trade; routes to paper_options_service

The shape is deliberately conservative — equity callers see no
behavioural change. Only clients that send the nested `option`
object hit the options path.
"""
import logging
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from services.auth_helpers import get_current_user
from services import paper_trading_service as pts
from services import paper_options_service as pos_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/paper", tags=["paper-trading"])

_db = None


def set_db(database):
    global _db
    _db = database
    pts.set_db(database)
    pos_svc.set_db(database)


class OptionLeg(BaseModel):
    """Options sub-payload. Only present on options trades —
    equity trades omit this field entirely."""
    strike: float = Field(gt=0, description="Strike price, positive USD")
    expiry: str = Field(description="ISO expiry date, e.g. '2026-06-18'")
    type: str = Field(pattern="^(call|put)$", description="'call' or 'put'")
    iv_percent: Optional[float] = Field(
        default=None,
        description=(
            "Implied vol as percent (e.g. 18.92 for 18.92%). "
            "When omitted the pricer uses a 30% default."
        ),
    )


class TradeRequest(BaseModel):
    symbol: str
    side: str = Field(
        description=(
            "Equity: BUY / SELL. "
            "Options: buy_to_open / sell_to_close."
        ),
    )
    qty: float = Field(gt=0)
    option: Optional[OptionLeg] = None


@router.get("/portfolio")
async def get_portfolio(request: Request):
    """Get paper portfolio snapshot with live prices."""
    user = await get_current_user(request)
    snapshot = await pts.get_portfolio_snapshot(user["_id"])
    return snapshot


@router.post("/trade")
async def execute_trade(request: Request, trade: TradeRequest):
    """Execute a paper trade — equity or option based on payload shape.

    Returns the status-tagged dict from the underlying service.
    A `status: "rejected"` result is surfaced as HTTP 400 so the
    client can toast the error verbatim.
    """
    user = await get_current_user(request)

    if trade.option is not None:
        # Options path. `qty` for options = whole contracts; coerce
        # to int here so callers can continue to post `qty: 1` as a
        # number without needing a separate payload field.
        try:
            int_qty = int(trade.qty)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Options qty must be integer contracts")
        result = await pos_svc.execute_option_trade(
            user_id=user["_id"],
            symbol=trade.symbol,
            strike=trade.option.strike,
            expiry=trade.option.expiry,
            option_type=trade.option.type,
            side=trade.side,
            qty=int_qty,
            iv_percent=trade.option.iv_percent,
        )
    else:
        result = await pts.execute_trade(
            user["_id"], trade.symbol, trade.side, trade.qty
        )

    if result.get("status") == "rejected" or "error" in result:
        raise HTTPException(
            status_code=400,
            detail=result.get("error", "trade rejected"),
        )
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
