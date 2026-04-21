"""Risk/Reward Calculator Routes — Position sizing, R:R analysis, Kelly criterion."""
import logging
import math
import random
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from dataclasses import dataclass
from typing import Optional
from services.auth_helpers import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/risk-calc", tags=["risk-calculator"])


# ─── Circuit-breaker thresholds ──────────────────────────────────────────────
# When a user's recent trade history looks rough, we auto-halve the requested
# risk per trade. These thresholds are deliberately simple and conservative;
# tune by watching the `risk_adjustment.reason` field in the response.
LOSING_STREAK_THRESHOLD = 3       # consecutive wrong verified predictions
DRAWDOWN_THRESHOLD = 0.10         # 10% down from peak equity
RISK_REDUCTION_FACTOR = 0.5       # halve risk_pct when either threshold hit
STREAK_LOOKBACK = 10              # only scan the last N verified predictions

# ─── Trade guards ────────────────────────────────────────────────────────────
# Hard R:R floor keeps an obvious footgun (1:1 setups, worse) out of sizing
# unless the user explicitly asks for it. Exploration is an optional
# epsilon-greedy knob for paper-mode users who want to intentionally sample
# "outlier" trades the rules would normally veto — useful for ML training
# data diversity, never for live money.
DEFAULT_MIN_RR = 1.5              # recommended floor when `min_rr` omitted
EXPLORATION_RATE = 0.10           # 10% of opt-in calls bypass the veto

# ─── Conviction score weights ────────────────────────────────────────────────
# Weights + tiers live in `services.conviction_service` so prediction-
# logging, the risk calculator, and any future caller all share one
# source of truth. Re-exported here for backwards compatibility with
# any external tooling that still imports them from this module.
from services.conviction_service import CONVICTION_WEIGHTS, CONVICTION_TIERS  # noqa: E402,F401


@dataclass
class SizingConfig:
    method: str = "risk_pct"
    risk_pct: float = 2.0
    fixed_dollar_risk: Optional[float] = None
    win_rate: Optional[float] = None
    avg_wl_ratio: Optional[float] = None
    rr_ratio: float = 0

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
    # Optional trade guards
    min_rr: Optional[float] = None        # hard R:R floor (default = DEFAULT_MIN_RR when omitted)
    explore: bool = False                  # opt-in ε-greedy exploration for paper-mode users
    mode: str = "paper"                    # "paper" or "live" — exploration is paper-only
    # Optional conviction inputs (all degrade gracefully when absent)
    confidence: Optional[float] = None    # 0.0-1.0 from the signal model
    regime_match: Optional[bool] = None   # True if signal direction agrees with HMM regime


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
    min_rr: Optional[float] = None
    explore: bool = False
    mode: str = "paper"
    confidence: Optional[float] = None
    regime_match: Optional[bool] = None


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


async def _compute_risk_context(user_id: str, account_value: float) -> dict:
    """Derive a circuit-breaker view of the user's current risk posture.

    Returns a dict with::

        {
          "losing_streak": int,        # most-recent consecutive wrong calls
          "current_drawdown": float,   # fraction drop from peak_equity (0.0 - 1.0)
          "peak_equity": float,
          "risk_reduced": bool,        # True if either threshold tripped
          "reduction_factor": float,   # multiplier applied to risk_pct (1.0 = no cut)
          "reason": str | None,        # human-readable explanation
        }

    Streak is read from `predictions.verified_24h.correct` — the same field
    the nightly labeler updates. Drawdown is computed against a running
    `peak_equity` value that we upsert on this very call, so no separate
    scheduled job is needed to keep it fresh.
    """
    ctx = {
        "losing_streak": 0,
        "recent_losses_24h": 0,
        "current_drawdown": 0.0,
        "peak_equity": account_value,
        "risk_reduced": False,
        "reduction_factor": 1.0,
        "reason": None,
    }

    if _db is None:
        return ctx

    # ── Losing streak — walk backwards through verified predictions ──
    try:
        cursor = _db.predictions.find(
            {"user_id": user_id, "verified_24h.correct": {"$exists": True}},
            {"_id": 0, "verified_24h.correct": 1, "created_at": 1},
        ).sort("created_at", -1).limit(STREAK_LOOKBACK)
        streak = 0
        async for row in cursor:
            correct = (row.get("verified_24h") or {}).get("correct")
            if correct is False:
                streak += 1
            else:
                break
        ctx["losing_streak"] = streak
    except Exception as e:
        logger.warning(f"[risk-ctx] streak lookup failed for {user_id}: {e}")

    # ── Recent losses bridge — labeler runs hourly and sets verified_24h
    # on a 24-hour delay, so there's a window where genuinely losing
    # paper trades from the past 24h aren't yet reflected in the streak
    # above. Count closed paper trades with realized PnL < 0 in the
    # last 24h to bridge that lag. Added to `losing_streak` (capped at
    # STREAK_LOOKBACK so a bad day can't produce a factor-of-five jump).
    try:
        from datetime import timedelta
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        # `paper_trades` is append-only; closed trades record the realized
        # P&L in `realized_pnl` (negative = loss). Sells that close a long
        # position are the definitive loss events.
        recent_losses = await _db.paper_trades.count_documents({
            "user_id": user_id,
            "executed_at": {"$gte": since.isoformat()},
            "realized_pnl": {"$lt": 0},
        })
        ctx["recent_losses_24h"] = recent_losses
        ctx["losing_streak"] = min(ctx["losing_streak"] + recent_losses, STREAK_LOOKBACK)
    except Exception as e:
        logger.warning(f"[risk-ctx] recent-loss bridge failed for {user_id}: {e}")

    # ── Drawdown — update peak_equity then compute drop ──
    try:
        doc = await _db.paper_portfolios.find_one(
            {"user_id": user_id}, {"_id": 0, "peak_equity": 1}
        ) or {}
        peak = max(float(doc.get("peak_equity") or 0.0), account_value)
        if peak != doc.get("peak_equity"):
            # Upsert the new peak so we don't recompute forever
            await _db.paper_portfolios.update_one(
                {"user_id": user_id},
                {"$set": {"peak_equity": peak, "peak_equity_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )
        ctx["peak_equity"] = round(peak, 2)
        ctx["current_drawdown"] = round((peak - account_value) / peak, 4) if peak > 0 else 0.0
    except Exception as e:
        logger.warning(f"[risk-ctx] drawdown lookup failed for {user_id}: {e}")

    # ── Apply circuit-breaker rules ──
    reasons = []
    if ctx["losing_streak"] >= LOSING_STREAK_THRESHOLD:
        reasons.append(f"losing streak: {ctx['losing_streak']} in a row")
    if ctx["current_drawdown"] >= DRAWDOWN_THRESHOLD:
        reasons.append(f"drawdown {ctx['current_drawdown']*100:.1f}% from peak")
    if reasons:
        ctx["risk_reduced"] = True
        ctx["reduction_factor"] = RISK_REDUCTION_FACTOR
        ctx["reason"] = "; ".join(reasons)

    return ctx


async def _evaluate_trade_guards(
    *,
    user_id: str,
    symbol: str,
    rr_ratio: float,
    min_rr: Optional[float],
    explore_requested: bool,
    mode: str,
    risk_ctx: dict,
) -> dict:
    """Evaluate the hard R:R veto and guarded ε-greedy exploration.

    Returns a dict that gets embedded in the API response so the caller
    (UI or bot) can honour or log the advisory decision.

    Rules:
      * veto fires when rr_ratio < effective_min_rr (defaults to
        `DEFAULT_MIN_RR`). Vetos are advisory — we don't 400 the request;
        the caller decides whether to block execution.
      * exploration fires only when ALL of: `explore_requested=True`,
        `mode == "paper"`, and the circuit breaker is NOT active. A live
        dice-roll that bypasses veto is never what you want.
      * exploration can only OVERRIDE a veto (the whole point is sampling
        trades the rules would normally reject). It cannot re-veto a good
        trade.
      * every exploration decision is persisted to `risk_exploration_log`
        with a trace id so performance can be analysed later
        ('how did my exploration trades do vs normal ones?').
    """
    effective_min_rr = DEFAULT_MIN_RR if min_rr is None else min_rr

    veto = False
    veto_reason: Optional[str] = None
    if rr_ratio < effective_min_rr:
        veto = True
        veto_reason = f"R:R {rr_ratio:.2f} below min {effective_min_rr:.2f}"

    exploration_active = False
    exploration_reason: Optional[str] = None
    exploration_blocked_reason: Optional[str] = None

    if explore_requested:
        if mode != "paper":
            exploration_blocked_reason = "exploration only allowed in paper mode"
        elif risk_ctx.get("risk_reduced"):
            exploration_blocked_reason = (
                "circuit breaker active — exploration paused"
            )
        elif veto and random.random() < EXPLORATION_RATE:
            exploration_active = True
            exploration_reason = (
                f"ε-greedy sample ({int(EXPLORATION_RATE * 100)}% rate) — veto overridden"
            )
            veto = False  # the whole point of exploration

    # Persist the decision (best-effort — never break the response on log fail)
    if _db is not None and (veto or exploration_active or exploration_blocked_reason):
        try:
            await _db.risk_exploration_log.insert_one({
                "user_id": user_id,
                "symbol": symbol,
                "rr_ratio": rr_ratio,
                "min_rr": effective_min_rr,
                "mode": mode,
                "veto": veto,
                "veto_reason": veto_reason,
                "explore_requested": explore_requested,
                "exploration_active": exploration_active,
                "exploration_reason": exploration_reason,
                "exploration_blocked_reason": exploration_blocked_reason,
                "risk_reduced_active": bool(risk_ctx.get("risk_reduced")),
                "logged_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.warning(f"[risk-guards] log write failed: {e}")

    return {
        "min_rr": effective_min_rr,
        "rr_ratio": rr_ratio,
        "veto": veto,
        "veto_reason": veto_reason,
        "exploration_requested": explore_requested,
        "exploration_active": exploration_active,
        "exploration_reason": exploration_reason,
        "exploration_blocked_reason": exploration_blocked_reason,
        "mode": mode,
    }


async def _compute_conviction(
    *,
    user_id: str,
    asset: str,
    direction: Optional[str],
    confidence: Optional[float],
    regime_match: Optional[bool],
    risk_ctx: dict,
) -> dict:
    """Thin wrapper around `services.conviction_service.compute_conviction`.

    The scorer used to live inline here; it was extracted so prediction
    logging (and any future non-risk-calc caller) can tag rows with the
    same composite score without a circular route→route import. Behaviour
    is identical — risk-calc responses still include the same score,
    tier, size_multiplier, weights, breakdown, and inputs.
    """
    from services.conviction_service import compute_conviction
    return await compute_conviction(
        _db,
        user_id=user_id,
        asset=asset,
        direction=direction,
        confidence=confidence,
        regime_match=regime_match,
        risk_ctx=risk_ctx,
    )


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


def _calculate_position_size(account_value: float, risk_per_share: float, cfg: SizingConfig):
    """Calculate position size and dollar risk based on sizing method."""
    if cfg.method == "fixed_dollar":
        dollar_risk = cfg.fixed_dollar_risk or (account_value * cfg.risk_pct / 100)
    elif cfg.method == "kelly":
        wr = (cfg.win_rate or 55) / 100
        wl = cfg.avg_wl_ratio or cfg.rr_ratio
        kelly_pct = (wr * wl - (1 - wr)) / wl if wl > 0 else 0
        kelly_pct = max(0, min(kelly_pct, 0.25))
        dollar_risk = account_value * kelly_pct
    else:
        dollar_risk = account_value * cfg.risk_pct / 100
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
    risk_ctx = await _compute_risk_context(user_id, account_value)
    guards = await _evaluate_trade_guards(
        user_id=user_id,
        symbol=symbol,
        rr_ratio=rr_ratio,
        min_rr=calc.min_rr,
        explore_requested=calc.explore,
        mode=calc.mode,
        risk_ctx=risk_ctx,
    )

    # Apply circuit-breaker reduction before the sizing calc so every
    # downstream number (position size, dollar risk, max loss) reflects
    # the de-risked amount. Only touches `risk_pct` method — Kelly and
    # fixed-dollar paths stay deterministic per user request.
    effective_risk_pct = calc.risk_pct * risk_ctx["reduction_factor"]

    position_size, dollar_risk = _calculate_position_size(
        account_value, risk_per_share,
        SizingConfig(method=calc.sizing_method, risk_pct=effective_risk_pct,
                     fixed_dollar_risk=calc.fixed_dollar_risk, win_rate=calc.win_rate,
                     avg_wl_ratio=calc.avg_win_loss_ratio, rr_ratio=rr_ratio),
    )

    # Conviction modulates sizing INSIDE the already-approved risk budget —
    # never outside it. Strong = 100%, moderate = 50%, weak = 0%. Hard
    # gates above stay authoritative (R:R veto, circuit breaker).
    conviction = await _compute_conviction(
        user_id=user_id,
        asset=symbol,
        direction=("up" if side == "buy" else "down"),
        confidence=calc.confidence,
        regime_match=calc.regime_match,
        risk_ctx=risk_ctx,
    )
    pre_conviction_size = position_size
    position_size = round(position_size * conviction["size_multiplier"], 4)
    dollar_risk = round(dollar_risk * conviction["size_multiplier"], 2)

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
        "risk_adjustment": {
            "risk_reduced": risk_ctx["risk_reduced"],
            "reduction_factor": risk_ctx["reduction_factor"],
            "requested_risk_pct": calc.risk_pct,
            "applied_risk_pct": round(effective_risk_pct, 3),
            "reason": risk_ctx["reason"],
            "losing_streak": risk_ctx["losing_streak"],
            "current_drawdown_pct": round(risk_ctx["current_drawdown"] * 100, 2),
            "peak_equity": risk_ctx["peak_equity"],
        },
        "trade_guards": guards,
        "conviction": {**conviction, "pre_conviction_size": pre_conviction_size},
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
    risk_ctx = await _compute_risk_context(user_id, account_value)
    effective_risk_pct = calc.risk_pct * risk_ctx["reduction_factor"]

    position_size, _ = _calculate_position_size(
        account_value, risk_per_share,
        SizingConfig(method=calc.sizing_method, risk_pct=effective_risk_pct,
                     fixed_dollar_risk=calc.fixed_dollar_risk),
    )
    total_cost = round(entry * position_size, 2)
    max_loss = round(risk_per_share * position_size, 2)

    tp_details, total_weighted_reward = _compute_tp_details(
        calc.take_profit_prices, calc.take_profit_pcts, position_size, entry, side,
    )

    rr_ratio = round(total_weighted_reward / max_loss, 2) if max_loss > 0 else 0

    guards = await _evaluate_trade_guards(
        user_id=user_id,
        symbol=symbol,
        rr_ratio=rr_ratio,
        min_rr=calc.min_rr,
        explore_requested=calc.explore,
        mode=calc.mode,
        risk_ctx=risk_ctx,
    )

    # Apply conviction to the multi-TP sizing.
    conviction = await _compute_conviction(
        user_id=user_id,
        asset=symbol,
        direction=("up" if side == "buy" else "down"),
        confidence=calc.confidence,
        regime_match=calc.regime_match,
        risk_ctx=risk_ctx,
    )
    pre_conviction_size = position_size
    position_size = round(position_size * conviction["size_multiplier"], 4)
    total_cost = round(entry * position_size, 2)
    max_loss = round(risk_per_share * position_size, 2)
    tp_details, total_weighted_reward = _compute_tp_details(
        calc.take_profit_prices, calc.take_profit_pcts, position_size, entry, side,
    )

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
        "risk_adjustment": {
            "risk_reduced": risk_ctx["risk_reduced"],
            "reduction_factor": risk_ctx["reduction_factor"],
            "requested_risk_pct": calc.risk_pct,
            "applied_risk_pct": round(effective_risk_pct, 3),
            "reason": risk_ctx["reason"],
            "losing_streak": risk_ctx["losing_streak"],
            "current_drawdown_pct": round(risk_ctx["current_drawdown"] * 100, 2),
            "peak_equity": risk_ctx["peak_equity"],
        },
        "trade_guards": guards,
        "conviction": {**conviction, "pre_conviction_size": pre_conviction_size},
    }
