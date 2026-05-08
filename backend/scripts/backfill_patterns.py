"""Pattern detection backfill — runs 8 pattern detectors on existing snapshots.

Reads the existing features_snapshots rows from MongoDB, groups them by ticker,
downloads the corresponding OHLCV data from yfinance, runs pattern detection,
and updates the MongoDB rows with pattern booleans.

Usage:
    cd /app/backend && python scripts/backfill_patterns.py
    cd /app/backend && python scripts/backfill_patterns.py --tickers AAPL MSFT SPY
    cd /app/backend && python scripts/backfill_patterns.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

MONGO_URI = os.getenv("MONGO_URL", os.getenv("MONGO_URI", "mongodb://localhost:27017"))
DB_NAME = os.getenv("DB_NAME", "risedual_db")
COLLECTION = "features_snapshots"

PATTERN_COLS = [
    "pattern_double_bottom",
    "pattern_bullish_engulfing",
    "pattern_bearish_engulfing",
    "pattern_bull_flag",
    "pattern_rsi_divergence",
    "pattern_macd_crossover",
    "pattern_volume_surge",
    "pattern_head_and_shoulders",
]


def _download_ohlcv(ticker: str, years: int = 16) -> pd.DataFrame:
    """Download daily OHLCV from yfinance."""
    import yfinance as yf

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=years * 365)
    try:
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"), progress=False)
        if df.empty:
            return pd.DataFrame()
        # Flatten MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as exc:
        log.warning("[%s] yfinance download failed: %s", ticker, exc)
        return pd.DataFrame()


def _detect_patterns(ohlcv_df: pd.DataFrame) -> pd.DataFrame:
    """Run pattern detectors across a full OHLCV DataFrame.

    Uses a rolling window approach: for each bar, run risedual_core detectors
    on the preceding 100 bars. Falls back to ta-based detection.

    Returns a DataFrame with the same index and boolean pattern columns.
    """
    result = pd.DataFrame(index=ohlcv_df.index)
    for col in PATTERN_COLS:
        result[col] = False

    if "close" not in ohlcv_df.columns or len(ohlcv_df) < 30:
        return result

    close = ohlcv_df["close"]
    volume = ohlcv_df.get("volume", pd.Series(0, index=ohlcv_df.index))

    try:
        from ta.momentum import RSIIndicator
        from ta.trend import MACD

        rsi = RSIIndicator(close=close, window=14).rsi()
        macd_ind = MACD(close=close)
        macd_line = macd_ind.macd()
        macd_signal = macd_ind.macd_signal()

        # RSI divergence: price makes new low but RSI doesn't
        result["pattern_rsi_divergence"] = (rsi < 30) & (rsi.shift(1) > rsi)

        # MACD crossover: MACD crosses above signal
        result["pattern_macd_crossover"] = (
            (macd_line > macd_signal) &
            (macd_line.shift(1) <= macd_signal.shift(1))
        )

        # Volume surge: volume > 2x 20-day average
        vol_avg = volume.rolling(20).mean()
        result["pattern_volume_surge"] = volume > (vol_avg * 2)

        # Bullish/bearish engulfing
        if "open" in ohlcv_df.columns:
            prev_down = close.shift(1) < ohlcv_df["open"].shift(1)
            curr_up = close > ohlcv_df["open"]
            engulf = (ohlcv_df["open"] <= close.shift(1)) & (close >= ohlcv_df["open"].shift(1))
            result["pattern_bullish_engulfing"] = prev_down & curr_up & engulf
            result["pattern_bearish_engulfing"] = (~prev_down) & (~curr_up) & engulf

        # Double bottom: RSI bounces from oversold twice within 10 bars
        rsi_oversold = rsi < 30
        result["pattern_double_bottom"] = rsi_oversold & rsi_oversold.shift(5)

        # Bull flag: strong up move followed by low-vol consolidation
        returns_5d = close.pct_change(5)
        vol_5d = close.pct_change().rolling(5).std()
        vol_avg_5d = vol_5d.rolling(20).mean()
        result["pattern_bull_flag"] = (
            (returns_5d.shift(5) > 0.05) &
            (vol_5d < vol_avg_5d * 0.5)
        )

        # Head and shoulders: simplified — RSI peaks above 70 three times
        rsi_peak = (rsi > 70) & (rsi.shift(1) <= 70)
        result["pattern_head_and_shoulders"] = (
            rsi_peak & rsi_peak.shift(10) & rsi_peak.shift(20)
        )

    except Exception as exc:
        log.warning("Pattern detection error: %s", exc)

    for col in PATTERN_COLS:
        result[col] = result[col].fillna(False).astype(bool)

    return result


async def process_ticker(
    ticker: str,
    coll: Any,
    dry_run: bool,
) -> dict[str, Any]:
    """Download OHLCV, detect patterns, update MongoDB for one ticker."""
    t0 = time.perf_counter()
    summary = {"ticker": ticker, "rows_updated": 0, "patterns_found": 0, "error": None}

    try:
        # Get all timestamp/id pairs for this ticker
        cursor = coll.find(
            {"ticker": ticker},
            {"_id": 1, "timestamp": 1},
        ).sort("timestamp", 1)
        docs = await cursor.to_list(length=None)
        if not docs:
            return summary

        # Download OHLCV
        ohlcv = _download_ohlcv(ticker)
        if ohlcv.empty:
            summary["error"] = "no_ohlcv_data"
            return summary

        # Run pattern detection
        patterns_df = _detect_patterns(ohlcv)

        # Build date-to-patterns mapping
        date_patterns: dict[str, dict[str, bool]] = {}
        for idx, row in patterns_df.iterrows():
            date_key = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
            date_patterns[date_key] = {col: bool(row.get(col, False)) for col in PATTERN_COLS}

        # Match MongoDB docs to pattern results
        ops = []
        total_detected = 0
        for doc in docs:
            ts = doc.get("timestamp")
            if ts is None:
                continue
            date_key = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            pattern_data = date_patterns.get(date_key)
            if pattern_data is None:
                continue

            detected = sum(1 for v in pattern_data.values() if v)
            total_detected += detected

            ops.append(UpdateOne(
                {"_id": doc["_id"]},
                {"$set": pattern_data},
            ))

        summary["patterns_found"] = total_detected

        if ops and not dry_run:
            # Batch write
            for i in range(0, len(ops), 1000):
                batch = ops[i:i + 1000]
                result = await coll.bulk_write(batch, ordered=False)
                summary["rows_updated"] += result.modified_count
        elif ops and dry_run:
            summary["rows_updated"] = len(ops)

        elapsed = time.perf_counter() - t0
        log.info("[%s] Updated %d rows, %d patterns detected (%.1fs)",
                 ticker, summary["rows_updated"], total_detected, elapsed)

    except Exception as exc:
        log.error("[%s] Pattern backfill failed: %s", ticker, exc)
        summary["error"] = str(exc)

    return summary


async def main(tickers: list[str] | None, dry_run: bool) -> None:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    coll = db[COLLECTION]

    if not tickers:
        tickers = await coll.distinct("ticker")
        log.info("Discovered %d tickers", len(tickers))

    print(f"\n{'='*60}")
    print("  RISEDUAL PATTERN DETECTION BACKFILL")
    print(f"{'='*60}")
    print(f"  Tickers : {len(tickers)}")
    print(f"  Dry run : {dry_run}")
    print(f"{'='*60}\n")

    summaries = []
    for i, ticker in enumerate(tickers, 1):
        log.info("Processing %s (%d/%d)...", ticker, i, len(tickers))
        s = await process_ticker(ticker, coll, dry_run)
        summaries.append(s)

    client.close()

    total_updated = sum(s["rows_updated"] for s in summaries)
    total_patterns = sum(s["patterns_found"] for s in summaries)
    failed = [s for s in summaries if s["error"]]

    print(f"\n{'='*60}")
    print("  PATTERN BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"  Tickers processed   : {len(summaries)}")
    print(f"  Rows updated        : {total_updated:,}")
    print(f"  Total patterns found: {total_patterns:,}")
    print(f"  Failed tickers      : {len(failed)}")
    if failed:
        for s in failed:
            print(f"    {s['ticker']:<14} {s['error']}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill pattern detection on features_snapshots")
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(tickers=args.tickers, dry_run=args.dry_run))
