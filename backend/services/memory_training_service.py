"""Market Memory Training — Bulk-ingest historical market regimes into ChromaDB.

Fetches 2 years of daily data for 30+ major symbols via yfinance, calculates
technical indicators (RSI, trend, momentum), creates regime snapshots every 5
trading days, and stores them with the actual 5-day forward outcome.

Run as a background task — takes 2-5 minutes to complete.
"""
import logging
import asyncio
import numpy as np
import yfinance as yf
from datetime import datetime, timezone


from services.market_memory_service import save_regime, _collection, init_memory

logger = logging.getLogger(__name__)

# Symbols to train on — diversified across sectors, market cap, and asset classes
TRAINING_SYMBOLS = {
    # Mega-cap tech
    "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology",
    "AMZN": "Consumer Discretionary", "NVDA": "Technology", "META": "Technology",
    "TSLA": "Consumer Discretionary",
    # Financials
    "JPM": "Financials", "V": "Financials", "GS": "Financials",
    # Healthcare
    "UNH": "Healthcare", "JNJ": "Healthcare", "PFE": "Healthcare",
    # Consumer / Industrial
    "PG": "Consumer Staples", "HD": "Consumer Discretionary", "CAT": "Industrials",
    # Energy / Materials
    "XOM": "Energy", "CVX": "Energy", "LIN": "Materials",
    # Sector ETFs
    "XLK": "Tech ETF", "XLF": "Financial ETF", "XLV": "Healthcare ETF",
    "XLI": "Industrial ETF", "XLE": "Energy ETF", "XLY": "Disc. ETF",
    "XLP": "Staples ETF", "XLU": "Utilities ETF", "XLB": "Materials ETF",
    "XLC": "Comms ETF", "XLRE": "Real Estate ETF",
    # Market ETFs
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq ETF", "IWM": "Russell 2000 ETF",
}

SNAPSHOT_INTERVAL = 5  # Create a regime snapshot every N trading days
FORWARD_WINDOW = 5     # Look forward N days to determine outcome
RSI_PERIOD = 14
SMA_SHORT = 20
SMA_LONG = 50


def _calc_rsi(closes: np.ndarray, period: int = 14) -> float:
    """Calculate RSI from an array of closes (latest at end)."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _classify_trend(closes: np.ndarray) -> str:
    """Classify trend based on SMA crossover."""
    if len(closes) < SMA_LONG:
        return "Insufficient data"
    sma_short = np.mean(closes[-SMA_SHORT:])
    sma_long = np.mean(closes[-SMA_LONG:])
    price = closes[-1]

    if price > sma_short > sma_long:
        return "Strong uptrend"
    elif price > sma_long:
        return "Uptrend"
    elif price < sma_short < sma_long:
        return "Strong downtrend"
    elif price < sma_long:
        return "Downtrend"
    else:
        return "Consolidation"


def _classify_volume(volumes: np.ndarray) -> tuple:
    """Classify volume relative to average. Returns (signal_str, vol_delta_float)."""
    if len(volumes) < 20:
        return "Normal", 0.0
    avg_vol = np.mean(volumes[-20:])
    recent_vol = np.mean(volumes[-3:])
    ratio = recent_vol / avg_vol if avg_vol > 0 else 1
    delta = round(ratio - 1.0, 3)  # +0.5 = 50% above average
    if ratio > 1.5:
        return "High volume surge", delta
    elif ratio > 1.2:
        return "Above average volume", delta
    elif ratio < 0.6:
        return "Low volume", delta
    return "Normal volume", delta


def _classify_outcome(current_price: float, future_price: float) -> tuple:
    """Classify what happened in the forward window."""
    if future_price <= 0 or current_price <= 0:
        return "unknown", "N/A"
    change_pct = ((future_price - current_price) / current_price) * 100
    if change_pct >= 3:
        return "hit", f"rose {change_pct:+.1f}% (strong bullish)"
    elif change_pct >= 1:
        return "hit", f"rose {change_pct:+.1f}% (mild bullish)"
    elif change_pct >= -1:
        return "neutral", f"flat {change_pct:+.1f}% (sideways)"
    elif change_pct >= -3:
        return "miss", f"fell {change_pct:+.1f}% (mild bearish)"
    else:
        return "miss", f"fell {change_pct:+.1f}% (strong bearish)"


def _process_symbol(symbol: str, sector: str, fg_history: dict = None) -> list[dict]:
    """Process a single symbol: fetch 2yr data, compute technicals, build enriched regime snapshots."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2y")
        if hist.empty or len(hist) < SMA_LONG + FORWARD_WINDOW + 10:
            logger.warning(f"Skipping {symbol}: insufficient data ({len(hist)} bars)")
            return []

        closes = hist["Close"].values
        volumes = hist["Volume"].values
        dates = hist.index

        regimes = []
        start = SMA_LONG
        end = len(closes) - FORWARD_WINDOW

        for i in range(start, end, SNAPSHOT_INTERVAL):
            price = round(float(closes[i]), 2)
            date_str = dates[i].strftime("%Y-%m-%d")

            change_1d = round(((closes[i] - closes[i - 1]) / closes[i - 1]) * 100, 2) if closes[i - 1] > 0 else 0
            change_5d = round(((closes[i] - closes[i - 5]) / closes[i - 5]) * 100, 2) if i >= 5 and closes[i - 5] > 0 else 0

            rsi = _calc_rsi(closes[:i + 1], RSI_PERIOD)
            trend = _classify_trend(closes[:i + 1])
            vol_signal, vol_delta = _classify_volume(volumes[:i + 1])

            # Forward outcome
            future_price = float(closes[i + FORWARD_WINDOW])
            outcome, actual_result = _classify_outcome(price, future_price)

            # Derive prediction from indicators
            if rsi > 70 and "uptrend" in trend.lower():
                prediction = "BULLISH (overbought momentum)"
            elif rsi > 60 and "uptrend" in trend.lower():
                prediction = "BULLISH"
            elif rsi < 30 and "downtrend" in trend.lower():
                prediction = "BEARISH (oversold weakness)"
            elif rsi < 40 and "downtrend" in trend.lower():
                prediction = "BEARISH"
            else:
                prediction = "NEUTRAL"

            confidence = 50
            if rsi > 60 and "uptrend" in trend.lower():
                confidence = min(90, 50 + int(rsi - 50))
            elif rsi < 40 and "downtrend" in trend.lower():
                confidence = min(90, 50 + int(50 - rsi))

            # Fear & Greed enrichment (lookup by date)
            fg_value = 50
            fg_label = "Neutral"
            if fg_history:
                fg_value = fg_history.get(date_str, 50)
                if fg_value <= 25:
                    fg_label = "Extreme Fear"
                elif fg_value <= 40:
                    fg_label = "Fear"
                elif fg_value <= 60:
                    fg_label = "Neutral"
                elif fg_value <= 75:
                    fg_label = "Greed"
                else:
                    fg_label = "Extreme Greed"

            regime = {
                "symbol": symbol,
                "ticker": symbol,
                "date": date_str,
                "price": price,
                "sector": sector,
                # Structured metrics
                "metrics": {
                    "rsi": rsi,
                    "change_1d": change_1d,
                    "change_1w": change_5d,
                    "vol_delta": vol_delta,
                    "trend": trend,
                    "volume_signal": vol_signal,
                },
                # Structured sentiment
                "sentiment": {
                    "fg_index": fg_value,
                    "fg_label": fg_label,
                },
                # Flat copies for backward compat
                "change_1d": change_1d,
                "change_1w": change_5d,
                "rsi": rsi,
                "trend": trend,
                "vol_delta": vol_delta,
                "volume_signal": vol_signal,
                "fg_index": fg_value,
                "fg_label": fg_label,
                # Prediction & outcome
                "prediction": prediction,
                "confidence": confidence,
                "actual_result": actual_result,
                "outcome": outcome,
            }
            regimes.append(regime)

        return regimes
    except Exception as e:
        logger.error(f"Error processing {symbol}: {e}")
        return []


async def run_memory_training(mongo_db=None, progress_callback=None) -> dict:
    """Run the full memory training pipeline. Returns stats on completion.

    This is designed to run as a background task (takes 2-5 minutes).
    """
    if not _collection:
        if mongo_db:
            init_memory(mongo_db)
        else:
            return {"error": "Market Memory not initialized"}

    # Fetch historical Fear & Greed data for enrichment
    from services.market_sentiment_service import get_fear_greed_historical
    fg_history = await asyncio.to_thread(get_fear_greed_historical, 730)
    logger.info(f"Loaded {len(fg_history)} days of Fear & Greed history for enrichment")

    start_time = datetime.now(timezone.utc)
    total_symbols = len(TRAINING_SYMBOLS)
    total_regimes = 0
    failed_symbols = []
    processed = 0

    logger.info(f"Memory Training started: {total_symbols} symbols, ~2yr history each")

    for symbol, sector in TRAINING_SYMBOLS.items():
        try:
            regimes = await asyncio.to_thread(_process_symbol, symbol, sector, fg_history)

            if not regimes:
                failed_symbols.append(symbol)
                processed += 1
                continue

            for regime in regimes:
                await save_regime(regime)
                total_regimes += 1

            processed += 1
            logger.info(f"Training [{processed}/{total_symbols}] {symbol}: {len(regimes)} regimes saved")

            if progress_callback:
                await progress_callback({
                    "symbol": symbol,
                    "regimes_added": len(regimes),
                    "progress": f"{processed}/{total_symbols}",
                    "total_so_far": total_regimes,
                })

        except Exception as e:
            logger.error(f"Training failed for {symbol}: {e}")
            failed_symbols.append(symbol)
            processed += 1

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()

    result = {
        "status": "complete",
        "symbols_processed": processed,
        "symbols_failed": len(failed_symbols),
        "failed_symbols": failed_symbols,
        "total_regimes_ingested": total_regimes,
        "elapsed_seconds": round(elapsed, 1),
        "episodes_per_second": round(total_regimes / elapsed, 1) if elapsed > 0 else 0,
        "fear_greed_days_loaded": len(fg_history),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info(
        f"Memory Training complete: {total_regimes} regimes from {processed} symbols "
        f"in {elapsed:.0f}s ({result['episodes_per_second']} eps/s)"
    )

    if mongo_db is not None:
        try:
            await mongo_db.memory_training_runs.insert_one({
                **result,
                "training_symbols": list(TRAINING_SYMBOLS.keys()),
            })
        except Exception as e:
            logger.warning(f"Failed to log training run: {e}")

    return result
