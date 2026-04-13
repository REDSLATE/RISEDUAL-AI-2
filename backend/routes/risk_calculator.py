"""Risk/Reward Calculator Routes — Position sizing, R:R analysis, Kelly criterion."""
import logging
import math
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/risk-calc", tags=["risk-calculator"])

_db = None

def set_db(database):
    global _db
    _db = database


class RiskCalcRequest(BaseModel):
    symbol: str
    side: str = "buy"
    entry_price: Optional[float] = None  # auto-fetches if None
    stop_loss_price: float
    take_profit_price: float
    # Position sizing method
    sizing_method: str = "risk_pct"  # risk_pct, fixed_dollar, kelly
    risk_pct: float = Field(default=2.0, ge=0.1, le=50)
    fixed_dollar_risk: Optional[float] = None
    # Optional: for Kelly criterion
    win_rate: Optional[float] = None  # 0-100
    avg_win_loss_ratio: Optional[float] = None


class MultiTpCalcRequest(BaseModel):
    symbol: str
    side: str = "buy"
    entry_price: Optional[float] = None
    stop_loss_price: float
    take_profit_prices: list[float]
    take_profit_pcts: list[float]  # % of position at each TP
    sizing_method: str = "risk_pct"
    risk_pct: float = Field(default=2.0, ge=0.1, le=50)
    fixed_dollar_risk: Optional[float] = None


async def _get_account_value(user_id: str) -> float:
    """Get total account value from paper portfolio."""
    if _db is None:
        return 100000.0
    doc = await _db.paper_portfolios.find_one({"user_id": user_id}, {"_id": 0, "cash": 1, "positions": 1})
    if not doc:
        return 100000.0
    cash = doc.get("cash", 100000.0)
    # Add position values at cost basis
    positions_value = sum(p.get("qty", 0) * p.get("avg_cost", 0) for p in doc.get("positions", []))
    return cash + positions_value


async def _get_current_price(symbol: str) -> Optional[float]:
    """Get current market price."""
    from services.price_provider import get_quote, get_crypto_quote
    CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "DOT", "SHIB", "LINK", "BNB"}
    q = await get_crypto_quote(symbol) if symbol in CRYPTO else await get_quote(symbol)
    if q and q.get("price"):
        return float(q["price"])
    return None


@router.post("/calculate")
async def calculate_risk(request: Request, calc: RiskCalcRequest):
    """Calculate risk/reward ratio, position size, and max loss for a trade."""
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    symbol = calc.symbol.upper()

    # Get entry price
    entry = calc.entry_price
    if not entry:
        entry = await _get_current_price(symbol)
        if not entry:
            raise HTTPException(status_code=400, detail=f"Cannot get price for {symbol}")

    sl = calc.stop_loss_price
    tp = calc.take_profit_price
    side = calc.side.lower()

    # Validate direction
    if side == "buy":
        if sl >= entry:
            raise HTTPException(status_code=400, detail="Stop loss must be below entry for a buy")
        if tp <= entry:
            raise HTTPException(status_code=400, detail="Take profit must be above entry for a buy")
        risk_per_share = entry - sl
        reward_per_share = tp - entry
    else:
        if sl <= entry:
            raise HTTPException(status_code=400, detail="Stop loss must be above entry for a sell")
        if tp >= entry:
            raise HTTPException(status_code=400, detail="Take profit must be below entry for a sell")
        risk_per_share = sl - entry
        reward_per_share = entry - tp

    rr_ratio = round(reward_per_share / risk_per_share, 2) if risk_per_share > 0 else 0
    account_value = await _get_account_value(user_id)

    # Position sizing
    if calc.sizing_method == "fixed_dollar":
        dollar_risk = calc.fixed_dollar_risk or (account_value * calc.risk_pct / 100)
        position_size = dollar_risk / risk_per_share if risk_per_share > 0 else 0
    elif calc.sizing_method == "kelly":
        win_rate = (calc.win_rate or 55) / 100
        wl_ratio = calc.avg_win_loss_ratio or rr_ratio
        kelly_pct = (win_rate * wl_ratio - (1 - win_rate)) / wl_ratio if wl_ratio > 0 else 0
        kelly_pct = max(0, min(kelly_pct, 0.25))  # Cap at 25%
        dollar_risk = account_value * kelly_pct
        position_size = dollar_risk / risk_per_share if risk_per_share > 0 else 0
    else:  # risk_pct
        dollar_risk = account_value * calc.risk_pct / 100
        position_size = dollar_risk / risk_per_share if risk_per_share > 0 else 0

    position_size = math.floor(position_size * 100) / 100  # Floor to 2 decimal
    total_cost = round(entry * position_size, 2)
    max_loss = round(risk_per_share * position_size, 2)
    max_profit = round(reward_per_share * position_size, 2)
    risk_pct_of_account = round((max_loss / account_value) * 100, 2) if account_value > 0 else 0

    return {
        "symbol": symbol,
        "side": side,
        "entry_price": round(entry, 4),
        "stop_loss_price": round(sl, 4),
        "take_profit_price": round(tp, 4),
        "risk_per_share": round(risk_per_share, 4),
        "reward_per_share": round(reward_per_share, 4),
        "risk_reward_ratio": rr_ratio,
        "position_size": position_size,
        "total_cost": total_cost,
        "max_loss": max_loss,
        "max_profit": max_profit,
        "dollar_risk": round(dollar_risk, 2),
        "risk_pct_of_account": risk_pct_of_account,
        "account_value": round(account_value, 2),
        "sizing_method": calc.sizing_method,
        "can_afford": total_cost <= account_value,
    }


@router.post("/multi-tp")
async def calculate_multi_tp(request: Request, calc: MultiTpCalcRequest):
    """Calculate R:R for multi take-profit setups (matches Smart Orders)."""
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    symbol = calc.symbol.upper()

    entry = calc.entry_price
    if not entry:
        entry = await _get_current_price(symbol)
        if not entry:
            raise HTTPException(status_code=400, detail=f"Cannot get price for {symbol}")

    sl = calc.stop_loss_price
    side = calc.side.lower()
    account_value = await _get_account_value(user_id)

    if side == "buy":
        risk_per_share = entry - sl
    else:
        risk_per_share = sl - entry

    if risk_per_share <= 0:
        raise HTTPException(status_code=400, detail="Invalid SL placement")

    dollar_risk = account_value * calc.risk_pct / 100
    position_size = math.floor((dollar_risk / risk_per_share) * 100) / 100
    total_cost = round(entry * position_size, 2)
    max_loss = round(risk_per_share * position_size, 2)

    # Calculate weighted reward across TPs
    tp_details = []
    total_weighted_reward = 0
    for tp_price, tp_pct in zip(calc.take_profit_prices, calc.take_profit_pcts):
        tp_qty = position_size * tp_pct / 100
        if side == "buy":
            reward = (tp_price - entry) * tp_qty
        else:
            reward = (entry - tp_price) * tp_qty
        total_weighted_reward += reward
        tp_details.append({
            "price": round(tp_price, 4),
            "pct_of_position": tp_pct,
            "qty": round(tp_qty, 4),
            "projected_profit": round(reward, 2),
        })

    rr_ratio = round(total_weighted_reward / max_loss, 2) if max_loss > 0 else 0

    return {
        "symbol": symbol,
        "side": side,
        "entry_price": round(entry, 4),
        "stop_loss_price": round(sl, 4),
        "position_size": position_size,
        "total_cost": total_cost,
        "max_loss": max_loss,
        "total_projected_profit": round(total_weighted_reward, 2),
        "risk_reward_ratio": rr_ratio,
        "take_profit_details": tp_details,
        "account_value": round(account_value, 2),
        "risk_pct_of_account": round((max_loss / account_value) * 100, 2) if account_value > 0 else 0,
        "can_afford": total_cost <= account_value,
    }
