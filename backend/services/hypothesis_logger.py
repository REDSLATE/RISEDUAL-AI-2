"""Hypothesis Logger — captures FeaturesSnapshot after every prediction.

Called as a FastAPI BackgroundTask from get_hypothesis(). Persists a
point-in-time feature vector to MongoDB for ML training pipeline.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from risedual_core.schemas.market import FeaturesSnapshot

logger = logging.getLogger(__name__)
_COLLECTION = "features_snapshots"


async def log_hypothesis_snapshot(
    ticker: str,
    prediction_id: str,
    market_data_dict: dict[str, Any],
    db: AsyncIOMotorDatabase,
) -> FeaturesSnapshot:
    """Capture a point-in-time FeaturesSnapshot and persist it to MongoDB."""
    now_utc = datetime.now(timezone.utc)

    # Derive volume_ratio if not pre-computed
    volume_ratio: float | None = market_data_dict.get("volume_ratio")
    if volume_ratio is None:
        vol = market_data_dict.get("volume")
        avg_vol = market_data_dict.get("avg_volume_20d")
        if vol is not None and avg_vol and avg_vol > 0:
            volume_ratio = float(vol) / float(avg_vol)

    snapshot = FeaturesSnapshot(
        ticker=ticker,
        timestamp=now_utc,
        price=_to_float(market_data_dict.get("price")),
        rsi_14=_to_float(market_data_dict.get("rsi_14")),
        macd=_to_float(market_data_dict.get("macd")),
        macd_signal=_to_float(market_data_dict.get("macd_signal")),
        sma_20=_to_float(market_data_dict.get("sma_20")),
        sma_50=_to_float(market_data_dict.get("sma_50")),
        volume_ratio=_to_float(volume_ratio),
        sentiment_score=_to_float(market_data_dict.get("sentiment_score")),
        insider_activity=_to_float(market_data_dict.get("insider_activity")),
        sector_momentum=_to_float(market_data_dict.get("sector_momentum")),
        regime_label=market_data_dict.get("regime_label"),
    )

    doc: dict[str, Any] = snapshot.model_dump()
    doc["prediction_id"] = prediction_id
    doc["captured_at"] = now_utc
    doc["prediction_price"] = snapshot.price
    doc["outcome"] = None
    doc["outcome_price"] = None
    doc["labeled_at"] = None
    doc["schema_version"] = 1

    if isinstance(doc.get("timestamp"), datetime) and doc["timestamp"].tzinfo is None:
        doc["timestamp"] = doc["timestamp"].replace(tzinfo=timezone.utc)

    try:
        await db[_COLLECTION].insert_one(doc)
        logger.debug("hypothesis_logger: snapshot written for ticker=%s prediction_id=%s", ticker, prediction_id)
    except Exception:
        logger.exception("hypothesis_logger: failed to persist snapshot for ticker=%s prediction_id=%s", ticker, prediction_id)

    return snapshot


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
