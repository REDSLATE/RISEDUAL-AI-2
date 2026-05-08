"""Backfill sector_momentum on every features_snapshots row.

Uses the ETF price history already in the collection to compute a 20-day
rolling return for each ticker's sector ETF, then bulk-updates every row.

Sector mapping:
  - Each stock is mapped to a GICS sector ETF (XLK, XLF, XLE, etc.)
  - Crypto tickers use BTC-USD as the "sector" proxy
  - ETFs map to themselves
  - Anything unmapped falls back to SPY (broad market)

The 20-day return is: (price_today / price_20_days_ago) - 1

Usage:
    cd /app/backend && python scripts/backfill_sector_momentum.py
    cd /app/backend && python scripts/backfill_sector_momentum.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
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
LOOKBACK = 20  # 20 trading days for sector momentum

# ── Sector ETF mapping ────────────────────────────────────────────────────────

TICKER_TO_SECTOR_ETF: dict[str, str] = {
    # Technology → XLK
    "AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "AVGO": "XLK",
    "GOOG": "XLK", "META": "XLK", "ORCL": "XLK", "CRM": "XLK",
    "AMD": "XLK", "TSLA": "XLK", "INTC": "XLK",
    # Financials → XLF
    "JPM": "XLF", "BAC": "XLF", "GS": "XLF", "WFC": "XLF",
    "BLK": "XLF", "AXP": "XLF", "V": "XLF", "MA": "XLF",
    # Energy → XLE
    "XOM": "XLE", "CVX": "XLE",
    # Healthcare → SPY (no XLV in our ETF set, use broad market)
    "UNH": "SPY", "JNJ": "SPY", "LLY": "SPY", "ABT": "SPY",
    "TMO": "SPY", "SYK": "SPY",
    # Consumer → SPY (no XLY/XLP in our set)
    "AMZN": "SPY", "HD": "SPY", "MCD": "SPY", "COST": "SPY",
    "SBUX": "SPY", "NKE": "SPY", "WMT": "SPY", "PG": "SPY",
    "KO": "SPY", "TGT": "SPY",
    # Industrials → SPY
    "CAT": "SPY", "HON": "SPY", "UPS": "SPY", "RTX": "SPY",
    "LMT": "SPY", "GE": "SPY",
    # ETFs map to themselves
    "SPY": "SPY", "QQQ": "QQQ", "IWM": "IWM", "DIA": "DIA",
    "XLK": "XLK", "XLF": "XLF", "XLE": "XLE",
    "TLT": "TLT", "GLD": "GLD",
    # Crypto → BTC-USD as sector proxy
    "BTC-USD": "BTC-USD", "ETH-USD": "BTC-USD", "BNB-USD": "BTC-USD",
    "SOL-USD": "BTC-USD", "XRP-USD": "BTC-USD", "ADA-USD": "BTC-USD",
    "DOGE-USD": "BTC-USD", "DOT-USD": "BTC-USD", "AVAX-USD": "BTC-USD",
    "LTC-USD": "BTC-USD", "LINK-USD": "BTC-USD", "MATIC-USD": "BTC-USD",
    "UNI-USD": "BTC-USD", "SHIB-USD": "BTC-USD", "ATOM-USD": "BTC-USD",
    "BCH-USD": "BTC-USD", "FIL-USD": "BTC-USD", "TRX-USD": "BTC-USD",
    "POL-USD": "BTC-USD", "TON-USD": "BTC-USD",
}

# Fallback for any ticker not in the map
DEFAULT_SECTOR_ETF = "SPY"


async def load_etf_prices(db: Any) -> dict[str, pd.Series]:
    """Load daily price series for all sector ETFs from MongoDB."""
    etf_tickers = set(TICKER_TO_SECTOR_ETF.values())
    etf_prices: dict[str, pd.Series] = {}

    for etf in sorted(etf_tickers):
        cursor = db[COLLECTION].find(
            {"ticker": etf, "price": {"$ne": None}},
            {"_id": 0, "timestamp": 1, "price": 1},
        ).sort("timestamp", 1)
        docs = await cursor.to_list(length=None)
        if not docs:
            log.warning("No price data for ETF %s", etf)
            continue

        series = pd.Series(
            [float(d["price"]) for d in docs],
            index=pd.DatetimeIndex([d["timestamp"] for d in docs]),
            dtype=float,
        )
        # Deduplicate by date (keep last)
        series = series[~series.index.duplicated(keep="last")]
        series.sort_index(inplace=True)
        etf_prices[etf] = series
        log.info("Loaded %s: %d prices (%s → %s)",
                 etf, len(series),
                 series.index[0].strftime("%Y-%m-%d"),
                 series.index[-1].strftime("%Y-%m-%d"))

    return etf_prices


def compute_sector_momentum(etf_prices: pd.Series) -> pd.Series:
    """Compute 20-day rolling return: (price / price_20d_ago) - 1."""
    return etf_prices.pct_change(LOOKBACK)


async def process_ticker(
    ticker: str,
    coll: Any,
    momentum_series: dict[str, pd.Series],
    dry_run: bool,
) -> dict[str, Any]:
    """Update sector_momentum for all rows of one ticker."""
    t0 = time.perf_counter()
    summary = {"ticker": ticker, "rows_updated": 0, "error": None}

    sector_etf = TICKER_TO_SECTOR_ETF.get(ticker, DEFAULT_SECTOR_ETF)
    mom_series = momentum_series.get(sector_etf)

    if mom_series is None or mom_series.empty:
        summary["error"] = f"no momentum data for sector ETF {sector_etf}"
        return summary

    # Load all rows for this ticker
    cursor = coll.find(
        {"ticker": ticker},
        {"_id": 1, "timestamp": 1},
    ).sort("timestamp", 1)
    docs = await cursor.to_list(length=None)

    if not docs:
        return summary

    # Build date→momentum lookup (normalize to date only)
    mom_lookup: dict[str, float] = {}
    for ts, val in mom_series.items():
        if pd.notna(val):
            date_key = ts.strftime("%Y-%m-%d")
            mom_lookup[date_key] = round(float(val), 6)

    # Match and build update ops
    ops = []
    for doc in docs:
        ts = doc.get("timestamp")
        if ts is None:
            continue
        date_key = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
        sm = mom_lookup.get(date_key)
        if sm is not None:
            ops.append(UpdateOne(
                {"_id": doc["_id"]},
                {"$set": {"sector_momentum": sm}},
            ))

    if ops and not dry_run:
        for i in range(0, len(ops), 2000):
            batch = ops[i:i + 2000]
            result = await coll.bulk_write(batch, ordered=False)
            summary["rows_updated"] += result.modified_count
    elif ops and dry_run:
        summary["rows_updated"] = len(ops)

    elapsed = time.perf_counter() - t0
    log.info("[%s] → %s: updated %d/%d rows (%.1fs)",
             ticker, sector_etf, summary["rows_updated"], len(docs), elapsed)

    return summary


async def main(dry_run: bool) -> None:
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    coll = db[COLLECTION]

    print(f"\n{'='*60}")
    print("  RISEDUAL SECTOR MOMENTUM BACKFILL")
    print(f"{'='*60}")
    print(f"  Lookback     : {LOOKBACK} trading days")
    print(f"  Sector ETFs  : {len(set(TICKER_TO_SECTOR_ETF.values()))} unique")
    print(f"  Dry run      : {dry_run}")

    # Step 1: Load all ETF price series
    print("\n  Loading ETF prices...")
    etf_prices = await load_etf_prices(db)
    print(f"  Loaded {len(etf_prices)} ETF price series")

    # Step 2: Compute 20-day momentum for each ETF
    momentum_series: dict[str, pd.Series] = {}
    for etf, prices in etf_prices.items():
        mom = compute_sector_momentum(prices)
        momentum_series[etf] = mom
        valid = mom.dropna()
        if not valid.empty:
            print(f"    {etf}: {len(valid)} momentum values, "
                  f"range [{valid.min():.3f}, {valid.max():.3f}], "
                  f"current {valid.iloc[-1]:.4f}")

    # Step 3: Process all tickers
    all_tickers = await coll.distinct("ticker")
    print(f"\n  Processing {len(all_tickers)} tickers...")
    print(f"{'='*60}\n")

    summaries = []
    for i, ticker in enumerate(sorted(all_tickers), 1):
        s = await process_ticker(ticker, coll, momentum_series, dry_run)
        summaries.append(s)

    client.close()

    total_updated = sum(s["rows_updated"] for s in summaries)
    failed = [s for s in summaries if s["error"]]

    print(f"\n{'='*60}")
    print("  SECTOR MOMENTUM BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"  Tickers processed   : {len(summaries)}")
    print(f"  Rows updated        : {total_updated:,}")
    print(f"  Failed tickers      : {len(failed)}")
    if failed:
        for s in failed:
            print(f"    {s['ticker']:<14} {s['error']}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill sector_momentum on features_snapshots")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
