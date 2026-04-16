"""Hypothesis Logger — captures FeaturesSnapshot after every prediction.

Called as a background task from get_hypothesis(). Persists a point-in-time
feature vector to MongoDB for ML training. Phase 2: enriches with pattern
detection from OHLCV data.
"""
from __future__ import annotations

import logging
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

    # Phase 2: Enrich with pattern detection
    snapshot = await _enrich_with_patterns(snapshot, ticker, db)

    doc: dict[str, Any] = snapshot.model_dump()
    doc["prediction_id"] = prediction_id
    doc["captured_at"] = now_utc
    doc["prediction_price"] = snapshot.price
    doc["outcome"] = None
    doc["outcome_price"] = None
    doc["labeled_at"] = None
    doc["schema_version"] = 2

    if isinstance(doc.get("timestamp"), datetime) and doc["timestamp"].tzinfo is None:
        doc["timestamp"] = doc["timestamp"].replace(tzinfo=timezone.utc)

    try:
        await db[_COLLECTION].insert_one(doc)
        detected = [k for k in [
            "pattern_double_bottom", "pattern_bullish_engulfing", "pattern_bearish_engulfing",
            "pattern_bull_flag", "pattern_rsi_divergence", "pattern_macd_crossover",
            "pattern_volume_surge", "pattern_head_and_shoulders",
        ] if getattr(snapshot, k, None)]
        logger.info(
            "hypothesis_logger: snapshot for %s (prediction=%s) patterns=%s",
            ticker, prediction_id, detected or "none",
        )
    except Exception:
        logger.exception("hypothesis_logger: failed to persist snapshot for %s", ticker)

    return snapshot


async def _enrich_with_patterns(
    snapshot: FeaturesSnapshot,
    ticker: str,
    db: AsyncIOMotorDatabase,
) -> FeaturesSnapshot:
    """Fetch OHLCV history and run all 8 pattern detectors."""
    try:
        from services.price_provider import get_daily_history
        daily = await get_daily_history(ticker, "compact")
        if not daily or len(daily) < 2:
            return snapshot

        import pandas as pd
        rows = []
        for bar in daily:
            rows.append({
                "date": bar.get("date", ""),
                "open": float(bar.get("open", 0)),
                "high": float(bar.get("high", 0)),
                "low": float(bar.get("low", 0)),
                "close": float(bar.get("close", 0)),
                "volume": int(bar.get("volume", 0)),
            })

        ohlcv_df = pd.DataFrame(rows)
        if "date" in ohlcv_df.columns:
            ohlcv_df = ohlcv_df.sort_values("date").reset_index(drop=True)

        if len(ohlcv_df) < 2:
            return snapshot

        from risedual_core.ml.patterns import detect_all_patterns
        pattern_results = await detect_all_patterns(ohlcv_df)

        update = {field: result.detected for field, result in pattern_results.items()}
        snapshot = snapshot.model_copy(update=update)

    except Exception as exc:
        logger.warning("hypothesis_logger: pattern enrichment failed for %s: %s", ticker, exc)

    return snapshot


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
