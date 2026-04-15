"""Signal endpoint — returns ML-based directional signals.

GET /api/signal/{ticker} — loads trained model from disk (hot-reloads),
fetches current market data, runs inference, returns SignalResult.
Returns {"status": "no_model"} gracefully when no model exists yet.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from risedual_core.ml.signal_model import SignalModel
from risedual_core.schemas.market import FeaturesSnapshot, SignalResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/signal", tags=["signal"])

MODELS_DIR: Path = Path(os.environ.get("MODELS_DIR", "/app/backend/models"))
db = None


def set_db(database):
    global db
    db = database


def _latest_model_path() -> Path | None:
    candidates = list(MODELS_DIR.glob("signal_model_v*.joblib"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# In-memory model cache
_cached_model: SignalModel | None = None
_cached_mtime: float = -1.0
_cached_path: str = ""
_cached_trained_at: str = ""


def _load_model() -> SignalModel | None:
    global _cached_model, _cached_mtime, _cached_path, _cached_trained_at
    model_path = _latest_model_path()
    if model_path is None:
        return None

    disk_mtime = model_path.stat().st_mtime
    if _cached_model is not None and disk_mtime <= _cached_mtime:
        return _cached_model

    try:
        new_model = SignalModel.load(str(model_path))
        _cached_model = new_model
        _cached_mtime = disk_mtime
        _cached_path = str(model_path)
        _cached_trained_at = datetime.fromtimestamp(disk_mtime, tz=timezone.utc).isoformat()
        logger.info("signal endpoint: loaded model from %s", model_path)
        return new_model
    except Exception:
        logger.exception("signal endpoint: failed to load model from %s", model_path)
        return None


@router.get("/{ticker}", summary="Get ML signal for a ticker")
async def get_ai_signal(ticker: str, request: Request):
    ticker = ticker.upper().strip()

    model = _load_model()
    if model is None or not model.is_trained:
        return {
            "status": "no_model",
            "message": "Signal model not yet trained. Collecting data. Run train_signal_model.py once 100+ labeled snapshots exist.",
        }

    market_data = await _fetch_market_data(ticker)
    snapshot = _build_snapshot(ticker, market_data)

    try:
        result: SignalResult = model.predict(snapshot)
    except Exception as exc:
        logger.exception("signal endpoint: model.predict() raised for ticker=%s", ticker)
        raise HTTPException(status_code=500, detail=f"Model inference failed: {exc}") from exc

    payload = result.model_dump()
    payload["trained_at"] = _cached_trained_at
    return payload


async def _fetch_market_data(ticker: str) -> dict[str, Any]:
    from services.price_provider import get_quote
    data: dict[str, Any] = {}

    try:
        quote = await get_quote(ticker)
        if quote:
            data["price"] = quote.get("price") or quote.get("c") or quote.get("close")
            data["volume"] = quote.get("volume") or quote.get("v")
    except Exception as exc:
        logger.exception("signal endpoint: quote fetch failed for %s", ticker)
        raise HTTPException(status_code=502, detail=f"Market data unavailable for {ticker}: {exc}") from exc

    # Technical indicators — best effort via Alpha Vantage
    try:
        from services.market_data_pool import get_technical_indicators
        indicators = await get_technical_indicators(ticker)
        if indicators:
            data.update({
                "rsi_14": indicators.get("rsi_14") or indicators.get("rsi"),
                "macd": indicators.get("macd"),
                "macd_signal": indicators.get("macd_signal") or indicators.get("signal"),
                "sma_20": indicators.get("sma_20"),
                "sma_50": indicators.get("sma_50"),
            })
    except Exception:
        logger.warning("signal endpoint: indicator fetch failed for %s", ticker)

    # Sentiment from cache
    if db is not None:
        try:
            sent_doc = await db["headline_sentiment"].find_one(
                {"ticker": ticker}, sort=[("created_at", -1)]
            )
            if sent_doc:
                data["sentiment_score"] = sent_doc.get("overall_sentiment_score")
        except Exception:
            pass

    # Volume ratio
    vol = data.get("volume")
    avg_vol = data.get("avg_volume_20d")
    if vol is not None and avg_vol and float(avg_vol) > 0:
        data["volume_ratio"] = float(vol) / float(avg_vol)

    return data


def _build_snapshot(ticker: str, market_data: dict[str, Any]) -> FeaturesSnapshot:
    def _f(key: str) -> float | None:
        v = market_data.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return FeaturesSnapshot(
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        price=_f("price"),
        rsi_14=_f("rsi_14"),
        macd=_f("macd"),
        macd_signal=_f("macd_signal"),
        sma_20=_f("sma_20"),
        sma_50=_f("sma_50"),
        volume_ratio=_f("volume_ratio"),
        sentiment_score=_f("sentiment_score"),
        insider_activity=_f("insider_activity"),
        sector_momentum=_f("sector_momentum"),
        regime_label=market_data.get("regime_label"),
    )
