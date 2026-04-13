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


def _validate_trade_direction(side: str, entry: float, sl: float, tp: float):
    """Validate and compute per-share risk/reward based on trade direction."""
    if side == "buy":
        if sl >= entry:
            raise HTTPException(status_code=400, detail="Stop loss must be below entry for a buy")
        if tp <= entry:
            raise HTTPException(status_code=400, detail="Take profit must be above entry for a buy")
        return entry - sl, tp - entry
    else:
        if sl <= entry:
            raise HTTPException(status_code=400, detail="Stop loss must be above entry for a sell")
        if tp >= entry:
            raise HTTPException(status_code=400, detail="Take profit must be below entry for a sell")
        return sl - entry, entry - tp


def _calculate_position_size(method: str, account_value: float, risk_per_share: float,
                             risk_pct: float, fixed_dollar_risk: float = None,
                             win_rate: float = None, avg_wl_ratio: float = None, rr_ratio: float = 0):
    """Calculate position size and dollar risk based on sizing method."""
    if method == "fixed_dollar":
        dollar_risk = fixed_dollar_risk or (account_value * risk_pct / 100)
    elif method == "kelly":
        wr = (win_rate or 55) / 100
        wl = avg_wl_ratio or rr_ratio
        kelly_pct = (wr * wl - (1 - wr)) / wl if wl > 0 else 0
        kelly_pct = max(0, min(kelly_pct, 0.25))
        dollar_risk = account_value * kelly_pct
    else:
        dollar_risk = account_value * risk_pct / 100
    position_size = dollar_risk / risk_per_share if risk_per_share > 0 else 0
    return math.floor(position_size * 100) / 100, dollar_risk


def _compute_tp_details(tp_prices: list, tp_pcts: list, position_size: float,
                        entry: float, side: str):
    """Calculate weighted reward across multiple take-profit levels."""
    details = []
    total_reward = 0.0
    for tp_price, tp_pct in zip(tp_prices, tp_pcts):
        tp_qty = position_size * tp_pct / 100
        reward = ((tp_price - entry) if side == "buy" else (entry - tp_price)) * tp_qty
        total_reward += reward
        details.append({
            "price": round(tp_price, 4),
            "pct_of_position": tp_pct,
            "qty": round(tp_qty, 4),
            "projected_profit": round(reward, 2),
        })
    return details, total_reward



@router.post("/calculate")
async def calculate_risk(request: Request, calc: RiskCalcRequest):
    """Calculate risk/reward ratio, position size, and max loss for a trade."""
    user = await get_current_user(request)
    user_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])
    symbol = calc.symbol.upper()

    entry = calc.entry_price
    if not entry:
        entry = await _get_current_price(symbol)
        if not entry:
            raise HTTPException(status_code=400, detail=f"Cannot get price for {symbol}")

    side = calc.side.lower()
    risk_per_share, reward_per_share = _validate_trade_direction(side, entry, calc.stop_loss_price, calc.take_profit_price)

    rr_ratio = round(reward_per_share / risk_per_share, 2) if risk_per_share > 0 else 0
    account_value = await _get_account_value(user_id)

    position_size, dollar_risk = _calculate_position_size(
        calc.sizing_method, account_value, risk_per_share, calc.risk_pct,
        calc.fixed_dollar_risk, calc.win_rate, calc.avg_win_loss_ratio, rr_ratio,
    )

    total_cost = round(entry * position_size, 2)
    max_loss = round(risk_per_share * position_size, 2)
    max_profit = round(reward_per_share * position_size, 2)

    return {
        "symbol": symbol,
        "side": side,
        "entry_price": round(entry, 4),
        "stop_loss_price": round(calc.stop_loss_price, 4),
        "take_profit_price": round(calc.take_profit_price, 4),
        "risk_per_share": round(risk_per_share, 4),
        "reward_per_share": round(reward_per_share, 4),
        "risk_reward_ratio": rr_ratio,
        "position_size": position_size,
        "total_cost": total_cost,
        "max_loss": max_loss,
        "max_profit": max_profit,
        "dollar_risk": round(dollar_risk, 2),
        "risk_pct_of_account": round((max_loss / account_value) * 100, 2) if account_value > 0 else 0,
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

    side = calc.side.lower()
    risk_per_share = (entry - calc.stop_loss_price) if side == "buy" else (calc.stop_loss_price - entry)
    if risk_per_share <= 0:
        raise HTTPException(status_code=400, detail="Invalid SL placement")

    account_value = await _get_account_value(user_id)
    position_size, _ = _calculate_position_size(
        calc.sizing_method, account_value, risk_per_share, calc.risk_pct, calc.fixed_dollar_risk,
    )
    total_cost = round(entry * position_size, 2)
    max_loss = round(risk_per_share * position_size, 2)

    tp_details, total_weighted_reward = _compute_tp_details(
        calc.take_profit_prices, calc.take_profit_pcts, position_size, entry, side,
    )

    rr_ratio = round(total_weighted_reward / max_loss, 2) if max_loss > 0 else 0

    return {
        "symbol": symbol,
        "side": side,
        "entry_price": round(entry, 4),
        "stop_loss_price": round(calc.stop_loss_price, 4),
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
