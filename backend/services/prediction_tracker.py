"""
Prediction Accuracy Tracker — Logs predictions, verifies outcomes, calculates hit rates.

Flow:
1. After each AI analysis, log the prediction (symbol, direction, price, timestamp)
2. Background task checks 24h and 1-week outcomes via Alpha Vantage
3. Auto-classifies failure mode when predictions are wrong
4. API returns rolling accuracy stats per feature (Pro only)
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import uuid4

from services.price_provider import get_quote_sync

logger = logging.getLogger(__name__)

# Direction classification constants
DIRECTION_BULLISH = {"BUY", "BULLISH", "LONG", "UP"}
DIRECTION_BEARISH = {"SELL", "BEARISH", "SHORT", "DOWN"}
DIRECTION_NEUTRAL = {"HOLD", "NEUTRAL", "WAIT"}

# ── Failure Mode Classification ──
FAILURE_MODES = {
    "TECH_FAKEOUT": "Indicators were bullish but price reversed immediately (Stop-loss hunt).",
    "MACRO_SHOCK": "Unexpected news/data (CPI, Fed, etc.) invalidated the setup.",
    "LIQUIDITY_GAP": "Low volume caused slippage or erratic price spikes.",
    "REGIME_SHIFT": "Market shifted from trending to range-bound unexpectedly.",
    "UNKNOWN": "Price moved against prediction without clear technical or news trigger.",
}


def _classify_failure(direction: str, price_at: float, price_now: float,
                      volume_ratio: float = None) -> str:
    """Auto-classify why a prediction failed based on price action heuristics.

    Returns one of: TECH_FAKEOUT, LIQUIDITY_GAP, REGIME_SHIFT, UNKNOWN.
    (MACRO_SHOCK requires external news data and is set manually or via AI.)
    """
    if price_at <= 0 or price_now <= 0:
        return "UNKNOWN"

    pct_change = abs((price_now - price_at) / price_at * 100)
    direction_upper = direction.upper()

    # Large, violent move (>=5%) — likely a macro shock or liquidity gap
    if pct_change >= 5.0:
        if volume_ratio is not None and volume_ratio < 0.5:
            return "LIQUIDITY_GAP"
        return "MACRO_SHOCK"

    # HOLD/NEUTRAL predicted stability but price moved significantly (>2%)
    if direction_upper in DIRECTION_NEUTRAL and pct_change > 2.0:
        return "MACRO_SHOCK"

    # Small move (<1%) but wrong direction — regime shift (range-bound market)
    if pct_change < 1.0:
        return "REGIME_SHIFT"

    # Moderate reversal (1-5%) — classic technical fakeout
    if direction_upper in DIRECTION_BULLISH and price_now < price_at:
        return "TECH_FAKEOUT"
    if direction_upper in DIRECTION_BEARISH and price_now > price_at:
        return "TECH_FAKEOUT"

    return "UNKNOWN"


def _get_current_price(symbol: str) -> Optional[float]:
    """Fetch current price using smart price provider (AV → yfinance → cache)."""
    quote = get_quote_sync(symbol)
    return quote["price"] if quote else None


def _evaluate_prediction(direction: str, price_at_prediction: float,
                         price_now: float) -> bool:
    """
    Determine if a prediction was correct.
    BUY/BULLISH: price went up
    SELL/BEARISH: price went down
    HOLD/NEUTRAL: price moved < 2%
    """
    if price_at_prediction <= 0 or price_now <= 0:
        return False
    pct_change = (price_now - price_at_prediction) / price_at_prediction * 100
    direction_upper = direction.upper()

    if direction_upper in DIRECTION_BULLISH:
        return pct_change > 0
    elif direction_upper in DIRECTION_BEARISH:
        return pct_change < 0
    elif direction_upper in DIRECTION_NEUTRAL:
        return abs(pct_change) < 2.0
    return False


async def log_prediction(db, feature: str, symbol: str, direction: str,
                         confidence: float, score: float = None,
                         user_id: str = None) -> str:
    """Log a new prediction after AI analysis. Returns prediction_id."""
    price = await asyncio.to_thread(_get_current_price, symbol)
    prediction_id = str(uuid4())[:12]

    doc = {
        "prediction_id": prediction_id,
        "feature": feature,
        "symbol": symbol.upper(),
        "direction": direction.upper(),
        "confidence": confidence,
        "score": score,
        "price_at_prediction": price or 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "verified_24h": None,
        "verified_1w": None,
    }
    await db.predictions.insert_one(doc)
    logger.info(f"Logged prediction: {feature}/{symbol} {direction} @ ${price}")
    return prediction_id


async def log_market_prediction(db, direction: str, confidence: float,
                                user_id: str = None, symbol: str = None) -> str:
    """Log a market-wide prediction (defaults to SPY as proxy, or specific ticker)."""
    target_symbol = symbol or "SPY"
    return await log_prediction(
        db, "market_prediction", target_symbol, direction, confidence, user_id=user_id
    )


async def verify_pending_predictions(db):
    """Check and verify predictions that have passed 24h or 1 week. Run as background task."""
    now = datetime.now(timezone.utc)
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    cutoff_1w = (now - timedelta(weeks=1)).isoformat()

    # Find predictions needing 24h verification
    pending_24h = db.predictions.find({
        "verified_24h": None,
        "timestamp": {"$lte": cutoff_24h},
        "price_at_prediction": {"$gt": 0},
    }, {"_id": 0}).limit(20)

    async for pred in pending_24h:
        price_now = await asyncio.to_thread(_get_current_price, pred["symbol"])
        if price_now is None:
            continue
        correct = _evaluate_prediction(pred["direction"], pred["price_at_prediction"], price_now)

        # Classify failure mode if prediction was wrong
        failure_reason = "N/A"
        failure_code = None
        if not correct:
            failure_code = _classify_failure(
                pred["direction"], pred["price_at_prediction"], price_now
            )
            failure_reason = FAILURE_MODES.get(failure_code, FAILURE_MODES["UNKNOWN"])

        await db.predictions.update_one(
            {"prediction_id": pred["prediction_id"]},
            {"$set": {"verified_24h": {
                "price": price_now,
                "correct": correct,
                "verified_at": now.isoformat(),
                "failure_code": failure_code,
                "failure_reason": failure_reason,
            }}}
        )
        logger.info(
            f"Verified 24h: {pred['symbol']} {pred['direction']} — "
            f"{'CORRECT' if correct else f'WRONG ({failure_code})'}"
        )

        # Push to SSE stream
        try:
            from routes.stream import push_event
            push_event("new_verification", {
                "ticker": pred["symbol"],
                "direction": pred["direction"],
                "confidence": pred.get("confidence", 0),
                "correct": correct,
                "failure_code": failure_code,
                "price_at": pred["price_at_prediction"],
                "price_now": price_now,
            })
        except Exception:
            pass

        # Auto-save verified prediction to vector memory
        try:
            from services.market_memory_service import save_regime, _collection
            if _collection is not None:
                regime = {
                    "symbol": pred["symbol"],
                    "date": pred.get("timestamp", "")[:10],
                    "price": pred["price_at_prediction"],
                    "prediction": pred["direction"],
                    "confidence": pred.get("confidence", 0),
                    "actual_result": f"{'rose' if price_now > pred['price_at_prediction'] else 'fell'} to ${price_now:.2f}",
                    "outcome": "hit" if correct else "miss",
                    "failure_code": failure_code if not correct else None,
                    "failure_reason": failure_reason if not correct else None,
                }
                await save_regime(regime)
        except Exception as e:
            logger.warning(f"Memory save skipped for {pred['symbol']}: {e}")

        # Run AI post-mortem for wrong predictions (upgrades heuristic with news context)
        if not correct and failure_code:
            try:
                from services.post_mortem_service import run_and_update_post_mortem
                await run_and_update_post_mortem(db, pred, price_now, failure_code)
            except Exception as e:
                logger.warning(f"AI post-mortem skipped for {pred['symbol']}: {e}")

    # Find predictions needing 1-week verification
    pending_1w = db.predictions.find({
        "verified_1w": None,
        "verified_24h": {"$ne": None},
        "timestamp": {"$lte": cutoff_1w},
        "price_at_prediction": {"$gt": 0},
    }, {"_id": 0}).limit(20)

    async for pred in pending_1w:
        price_now = await asyncio.to_thread(_get_current_price, pred["symbol"])
        if price_now is None:
            continue
        correct = _evaluate_prediction(pred["direction"], pred["price_at_prediction"], price_now)

        # Classify failure mode if prediction was wrong
        failure_reason = "N/A"
        failure_code = None
        if not correct:
            failure_code = _classify_failure(
                pred["direction"], pred["price_at_prediction"], price_now
            )
            failure_reason = FAILURE_MODES.get(failure_code, FAILURE_MODES["UNKNOWN"])

        await db.predictions.update_one(
            {"prediction_id": pred["prediction_id"]},
            {"$set": {"verified_1w": {
                "price": price_now,
                "correct": correct,
                "verified_at": now.isoformat(),
                "failure_code": failure_code,
                "failure_reason": failure_reason,
            }}}
        )
        logger.info(
            f"Verified 1w: {pred['symbol']} {pred['direction']} — "
            f"{'CORRECT' if correct else f'WRONG ({failure_code})'}"
        )


async def get_accuracy_stats(db, feature: Optional[str] = None) -> dict:
    """Calculate rolling accuracy stats. Optionally filter by feature."""
    match = {}
    if feature:
        match["feature"] = feature

    # 24h accuracy
    match_24h = {**match, "verified_24h": {"$ne": None}}
    total_24h = await db.predictions.count_documents(match_24h)
    correct_24h = await db.predictions.count_documents({**match_24h, "verified_24h.correct": True})

    # 1w accuracy
    match_1w = {**match, "verified_1w": {"$ne": None}}
    total_1w = await db.predictions.count_documents(match_1w)
    correct_1w = await db.predictions.count_documents({**match_1w, "verified_1w.correct": True})

    # Pending
    pending = await db.predictions.count_documents({**match, "verified_24h": None})

    return {
        "accuracy_24h": round((correct_24h / total_24h * 100), 1) if total_24h > 0 else None,
        "total_24h": total_24h,
        "correct_24h": correct_24h,
        "accuracy_1w": round((correct_1w / total_1w * 100), 1) if total_1w > 0 else None,
        "total_1w": total_1w,
        "correct_1w": correct_1w,
        "pending": pending,
        "feature": feature or "all",
    }


async def get_all_feature_stats(db) -> dict:
    """Get accuracy stats for all features + overall."""
    features = ["war_room", "hypothesis", "market_prediction"]
    stats = {}
    for f in features:
        stats[f] = await get_accuracy_stats(db, f)
    stats["overall"] = await get_accuracy_stats(db)
    return stats


async def get_recent_predictions(db, feature: Optional[str] = None,
                                  limit: int = 20) -> list[dict]:
    """Get recent predictions with verification status."""
    match = {}
    if feature:
        match["feature"] = feature
    cursor = db.predictions.find(match, {"_id": 0}).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)
