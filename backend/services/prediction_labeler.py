"""Prediction Labeler — APScheduler job that labels snapshots with outcomes.

Runs hourly. For each FeaturesSnapshot older than 4 hours without an outcome,
fetches the current price and computes up/down/flat with a ±1.5% band.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)
_COLLECTION = "features_snapshots"
_PREDICTIONS_COLLECTION = "predictions"
_LABELING_DELAY_HOURS = 4
_FLAT_THRESHOLD = 0.015  # ±1.5%


async def label_pending_snapshots(db: AsyncIOMotorDatabase) -> None:
    """Label FeaturesSnapshots that have no outcome yet and are >4h old."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_LABELING_DELAY_HOURS)
    cursor = db[_COLLECTION].find({"outcome": None, "captured_at": {"$lt": cutoff}})

    labeled_count = 0
    error_count = 0

    async for doc in cursor:
        snapshot_id = doc["_id"]
        ticker: str = doc.get("ticker", "")
        prediction_price: float | None = doc.get("prediction_price")
        prediction_id: str | None = doc.get("prediction_id")

        if not ticker or prediction_price is None:
            continue

        try:
            current_price = await _fetch_current_price(ticker, db)
        except Exception:
            logger.exception("prediction_labeler: price fetch failed for ticker=%s", ticker)
            error_count += 1
            await db[_COLLECTION].update_one(
                {"_id": snapshot_id},
                {"$set": {"outcome": "error", "labeled_at": datetime.now(timezone.utc)}},
            )
            continue

        outcome = _compute_outcome(prediction_price, current_price)
        now_utc = datetime.now(timezone.utc)

        await db[_COLLECTION].update_one(
            {"_id": snapshot_id},
            {"$set": {"outcome": outcome, "outcome_price": current_price, "labeled_at": now_utc}},
        )

        if prediction_id:
            await db[_PREDICTIONS_COLLECTION].update_one(
                {"_id": prediction_id},
                {"$set": {"outcome": outcome, "outcome_labeled_at": now_utc}},
            )

        labeled_count += 1

    logger.info("prediction_labeler: run complete — labeled=%d errors=%d", labeled_count, error_count)


def _compute_outcome(prediction_price: float, current_price: float) -> str:
    if prediction_price <= 0:
        return "flat"
    pct_change = (current_price - prediction_price) / prediction_price
    if pct_change > _FLAT_THRESHOLD:
        return "up"
    if pct_change < -_FLAT_THRESHOLD:
        return "down"
    return "flat"


async def _fetch_current_price(ticker: str, db: AsyncIOMotorDatabase) -> float:
    from services.price_provider import get_quote
    try:
        quote: dict[str, Any] = await get_quote(ticker)
        price = quote.get("price") or quote.get("c") or quote.get("close")
        if price is not None:
            return float(price)
    except Exception as exc:
        logger.warning("prediction_labeler: live price fetch raised %s for %s", exc, ticker)

    cached = await db["price_cache"].find_one(
        {"key": f"quote_{ticker.upper()}"}, sort=[("updated_at", -1)]
    )
    if cached and cached.get("data", {}).get("price"):
        return float(cached["data"]["price"])

    raise RuntimeError(f"No price available for {ticker}")
