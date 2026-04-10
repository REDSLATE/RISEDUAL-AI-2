"""
Prediction Accuracy Tracker — Logs predictions, verifies outcomes, calculates hit rates.

Flow:
1. After each AI analysis, log the prediction (symbol, direction, price, timestamp)
2. Background task checks 24h and 1-week outcomes via Alpha Vantage
3. API returns rolling accuracy stats per feature (Pro only)
"""

import os
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from uuid import uuid4

from services.price_provider import get_quote, get_quote_sync

logger = logging.getLogger(__name__)


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
                                user_id: str = None) -> str:
    """Log a market-wide prediction (no specific symbol — uses SPY as proxy)."""
    return await log_prediction(
        db, "market_prediction", "SPY", direction, confidence, user_id=user_id
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
        await db.predictions.update_one(
            {"prediction_id": pred["prediction_id"]},
            {"$set": {"verified_24h": {
                "price": price_now,
                "correct": correct,
                "verified_at": now.isoformat(),
            }}}
        )
        logger.info(f"Verified 24h: {pred['symbol']} {pred['direction']} — {'CORRECT' if correct else 'WRONG'}")

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
        await db.predictions.update_one(
            {"prediction_id": pred["prediction_id"]},
            {"$set": {"verified_1w": {
                "price": price_now,
                "correct": correct,
                "verified_at": now.isoformat(),
            }}}
        )
        logger.info(f"Verified 1w: {pred['symbol']} {pred['direction']} — {'CORRECT' if correct else 'WRONG'}")


async def get_accuracy_stats(db, feature: Optional[str] = None) -> Dict:
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


async def get_all_feature_stats(db) -> Dict:
    """Get accuracy stats for all features + overall."""
    features = ["war_room", "hypothesis", "market_prediction"]
    stats = {}
    for f in features:
        stats[f] = await get_accuracy_stats(db, f)
    stats["overall"] = await get_accuracy_stats(db)
    return stats


async def get_recent_predictions(db, feature: Optional[str] = None,
                                  limit: int = 20) -> List[Dict]:
    """Get recent predictions with verification status."""
    match = {}
    if feature:
        match["feature"] = feature
    cursor = db.predictions.find(match, {"_id": 0}).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)
