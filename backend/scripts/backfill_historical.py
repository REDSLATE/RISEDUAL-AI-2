"""Historical backfill script — populates MongoDB with 15+ years of labeled snapshots.

Downloads daily OHLCV data via ``yfinance`` (free, no API key required),
computes all technical indicators with ``pandas-ta``, runs the 8 RISEDUAL
pattern detectors, labels 1-day and 5-day forward outcomes, and bulk-inserts
into the ``features_snapshots`` MongoDB collection.

Expected output
---------------
    Top 50 S&P 500 tickers (50 × ~3,770 trading days)   ≈ 188,500 rows
    Major ETFs (10 × ~3,770 days)                         ≈  37,700 rows
    Top 10 crypto (daily data available ~5-8 years)       ≈  25,000 rows
    Total                                                 ≈ 250,000+ labeled rows

Usage
-----
    python phase1/scripts/backfill_historical.py
    python phase1/scripts/backfill_historical.py --tickers AAPL MSFT --years 5
    python phase1/scripts/backfill_historical.py --dry-run --tickers SPY

Environment variables
---------------------
    MONGO_URI       MongoDB connection string (default: mongodb://localhost:27017)
    DB_NAME         Database name            (default: risedual)
    BATCH_SIZE      MongoDB insert batch size (default: 500)
    YEARS_BACK      Years of history to download (default: 15)

Design notes
------------
- Uses ``upsert`` on (ticker, timestamp) so the script is safe to re-run.
- Processes tickers sequentially to stay inside yfinance rate limits.
- Pattern detection requires ≥ 20 bars — rows near the start of history are
  stored with all pattern booleans as False.
- Crypto tickers use the yfinance ``-USD`` suffix (e.g. ``BTC-USD``).
- The ``schema_version`` field is set to 3 for all backfill rows.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore[import-untyped]

from risedual_core.ml.patterns import _run_all_sync  # synchronous runner
from risedual_core.schemas.market import FeaturesSnapshot

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

# ── Config ────────────────────────────────────────────────────────────────────

MONGO_URI: str = os.getenv("MONGO_URL", os.getenv("MONGO_URI", "mongodb://localhost:27017"))
DB_NAME: str = os.getenv("DB_NAME", "risedual_db")
BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "500"))
YEARS_BACK: int = int(os.getenv("YEARS_BACK", "15"))
COLLECTION: str = "features_snapshots"

# ── Ticker universe ───────────────────────────────────────────────────────────

SP50_TICKERS: list[str] = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA",
    "AVGO", "ORCL",
    # Financials
    "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "BLK",
    # Healthcare
    "UNH", "LLY", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "ISRG", "SYK",
    # Industrials & Energy
    "XOM", "CVX", "COP", "NEE", "GE", "HON", "CAT", "UPS", "LMT", "RTX",
    # Consumer
    "WMT", "HD", "COST", "TGT", "LOW", "SBUX", "MCD", "NKE", "PG", "KO",
]

ETF_TICKERS: list[str] = [
    "SPY",   # S&P 500
    "QQQ",   # Nasdaq 100
    "IWM",   # Russell 2000
    "DIA",   # Dow Jones
    "GLD",   # Gold
    "TLT",   # 20yr Treasury
    "XLF",   # Financials
    "XLK",   # Technology
    "XLE",   # Energy
    "ARKK",  # Innovation
]

CRYPTO_TICKERS: list[str] = [
    "BTC-USD",   # Bitcoin
    "ETH-USD",   # Ethereum
    "BNB-USD",   # BNB
    "SOL-USD",   # Solana
    "XRP-USD",   # Ripple
    "DOGE-USD",  # Dogecoin
    "ADA-USD",   # Cardano
    "TRX-USD",   # Tron
    "AVAX-USD",  # Avalanche
    "LINK-USD",  # Chainlink
    "TON-USD",   # Toncoin
    "SHIB-USD",  # Shiba Inu
    "DOT-USD",   # Polkadot
    "BCH-USD",   # Bitcoin Cash
    "NEAR-USD",  # NEAR Protocol
    "UNI-USD",   # Uniswap
    "LTC-USD",   # Litecoin
    "ICP-USD",   # Internet Computer
    "APT-USD",   # Aptos
    "POL-USD",   # Polygon (formerly MATIC)
]

DEFAULT_UNIVERSE: list[str] = SP50_TICKERS + ETF_TICKERS + CRYPTO_TICKERS

# ── Outcome labeling thresholds ───────────────────────────────────────────────

_FLAT_BAND_1D: float = 0.005   # ±0.5% → flat for 1-day
_FLAT_BAND_5D: float = 0.010   # ±1.0% → flat for 5-day


def _label(pct: float, band: float) -> str:
    """Convert a forward return to 'up'/'down'/'flat'."""
    if pct > band:
        return "up"
    if pct < -band:
        return "down"
    return "flat"


# ── Indicator computation ─────────────────────────────────────────────────────


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all technical indicators needed for FeaturesSnapshot.

    Uses the ``ta`` library (bukosabino/ta) for computation.
    Falls back gracefully if any indicator fails (leaves column as NaN).

    Parameters
    ----------
    df:
        DataFrame with columns: open, high, low, close, volume.
        Must be sorted ascending by date.

    Returns
    -------
    pd.DataFrame
        Original df + indicator columns appended.
    """
    try:
        from ta.momentum import RSIIndicator
        from ta.trend import MACD, SMAIndicator
    except ImportError:
        log.warning("ta library not installed — indicators will be NaN. pip install ta")
        return df

    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    # RSI-14
    try:
        df["rsi_14"] = RSIIndicator(close=df["close"], window=14).rsi()
    except Exception:
        df["rsi_14"] = float("nan")

    # MACD (12, 26, 9)
    try:
        macd_ind = MACD(close=df["close"], window_slow=26, window_fast=12, window_sign=9)
        df["macd"] = macd_ind.macd()
        df["macd_signal"] = macd_ind.macd_signal()
    except Exception:
        df["macd"] = df["macd_signal"] = float("nan")

    # SMA-20 and SMA-50
    try:
        df["sma_20"] = SMAIndicator(close=df["close"], window=20).sma_indicator()
        df["sma_50"] = SMAIndicator(close=df["close"], window=50).sma_indicator()
    except Exception:
        df["sma_20"] = df["sma_50"] = float("nan")

    # Volume ratio (volume / 20-day avg volume)
    try:
        vol_ma = df["volume"].rolling(20).mean()
        df["volume_ratio"] = df["volume"] / vol_ma.where(vol_ma > 0, 1.0)
    except Exception:
        df["volume_ratio"] = float("nan")

    return df


def _compute_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Compute forward returns and outcome labels for each row.

    Outcome is computed from ``close`` prices:
    - 1-day: (close[t+1] - close[t]) / close[t]
    - 5-day: (close[t+5] - close[t]) / close[t]

    Rows near the end of the DataFrame where t+N doesn't exist get NaN.

    Parameters
    ----------
    df:
        DataFrame with a ``close`` column, sorted ascending.

    Returns
    -------
    pd.DataFrame
        With columns: return_1d, return_5d, outcome_1d, outcome_5d.
    """
    close = df["close"]
    df["return_1d"] = (close.shift(-1) - close) / close
    df["return_5d"] = (close.shift(-5) - close) / close

    df["outcome_1d"] = df["return_1d"].apply(
        lambda r: _label(r, _FLAT_BAND_1D) if pd.notna(r) else None
    )
    df["outcome_5d"] = df["return_5d"].apply(
        lambda r: _label(r, _FLAT_BAND_5D) if pd.notna(r) else None
    )
    return df


# ── Pattern detection (synchronous, row-by-row window) ───────────────────────


def _detect_patterns_for_window(window_df: pd.DataFrame) -> dict[str, bool]:
    """Run all 8 pattern detectors on a window of OHLCV bars.

    Parameters
    ----------
    window_df:
        Sliding window DataFrame (most recent ``n`` rows), columns:
        open, high, low, close, volume.

    Returns
    -------
    dict[str, bool]
        Keys: pattern_double_bottom, pattern_bullish_engulfing, etc.
    """
    empty = {
        "pattern_double_bottom": False,
        "pattern_bullish_engulfing": False,
        "pattern_bearish_engulfing": False,
        "pattern_bull_flag": False,
        "pattern_rsi_divergence": False,
        "pattern_macd_crossover": False,
        "pattern_volume_surge": False,
        "pattern_head_and_shoulders": False,
    }

    if len(window_df) < 20:
        return empty

    try:
        results = _run_all_sync(window_df)
        return {
            f"pattern_{name}": result.detected
            for name, result in results.items()
        }
    except Exception as exc:
        log.debug("Pattern detection failed on window: %s", exc)
        return empty


def _add_patterns(df: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """Vectorised pattern detection across the full price history.

    For each row at index ``i``, runs detectors on ``df[i-window:i+1]``.
    Uses a sliding window of ``window`` bars (default 60 trading days).

    This is O(n * window) but fully in-process — no I/O.  For 3,770 rows
    and window=60 it takes ~10-30 seconds per ticker.

    Parameters
    ----------
    df:
        Full OHLCV DataFrame for one ticker.
    window:
        Number of bars to pass to each detector.

    Returns
    -------
    pd.DataFrame
        With 8 pattern boolean columns appended.
    """
    pattern_cols = [
        "pattern_double_bottom", "pattern_bullish_engulfing",
        "pattern_bearish_engulfing", "pattern_bull_flag",
        "pattern_rsi_divergence", "pattern_macd_crossover",
        "pattern_volume_surge", "pattern_head_and_shoulders",
    ]
    # Pre-fill with False
    for col in pattern_cols:
        df[col] = False

    n = len(df)
    for i in range(window, n):
        w = df.iloc[i - window: i + 1][["open", "high", "low", "close", "volume"]]
        detected = _detect_patterns_for_window(w)
        for col, val in detected.items():
            df.at[df.index[i], col] = val

    return df


# ── yfinance download ─────────────────────────────────────────────────────────


def _download_ticker(ticker: str, years: int) -> pd.DataFrame | None:
    """Download daily OHLCV from yfinance for ``years`` years.

    Returns a DataFrame with lowercase columns: open, high, low, close,
    volume.  Returns None if download fails or data is empty.
    """
    try:
        import yfinance as yf  # type: ignore[import-untyped]
    except ImportError:
        log.error("yfinance not installed. Run: pip install yfinance")
        return None

    end = datetime.now(timezone.utc).date()
    start = (datetime.now(timezone.utc) - timedelta(days=years * 365)).date()

    try:
        raw = yf.download(
            ticker,
            start=str(start),
            end=str(end),
            interval="1d",
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        log.warning("[%s] yfinance download failed: %s", ticker, exc)
        return None

    if raw is None or raw.empty:
        log.warning("[%s] No data returned by yfinance.", ticker)
        return None

    # Flatten multi-level columns if present
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)

    raw.columns = [c.lower() for c in raw.columns]
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(raw.columns):
        log.warning("[%s] Missing required columns: %s", ticker, required - set(raw.columns))
        return None

    raw = raw[["open", "high", "low", "close", "volume"]].copy()
    raw.dropna(subset=["close"], inplace=True)
    raw.sort_index(inplace=True)

    # Ensure timezone-aware index
    if raw.index.tzinfo is None:
        raw.index = raw.index.tz_localize("UTC")
    else:
        raw.index = raw.index.tz_convert("UTC")

    log.info("[%s] Downloaded %d rows (%s → %s)", ticker,
             len(raw), raw.index[0].date(), raw.index[-1].date())
    return raw


# ── MongoDB document builder ──────────────────────────────────────────────────


def _row_to_doc(
    ticker: str,
    row: pd.Series,
    timestamp: datetime,
) -> dict[str, Any]:
    """Convert one enriched DataFrame row to a MongoDB document."""

    def _f(val: Any) -> float | None:
        """Safe float conversion — returns None for NaN."""
        if val is None:
            return None
        try:
            f = float(val)
            return None if math.isnan(f) or math.isinf(f) else f
        except (TypeError, ValueError):
            return None

    def _b(val: Any) -> bool:
        return bool(val) if val is not None else False

    def _s(val: Any) -> str | None:
        return str(val) if val is not None and not (isinstance(val, float) and math.isnan(val)) else None

    return {
        "ticker": ticker,
        "timestamp": timestamp,
        # Technical indicators
        "rsi_14": _f(row.get("rsi_14")),
        "macd": _f(row.get("macd")),
        "macd_signal": _f(row.get("macd_signal")),
        "sma_20": _f(row.get("sma_20")),
        "sma_50": _f(row.get("sma_50")),
        "volume_ratio": _f(row.get("volume_ratio")),
        "sentiment_score": None,   # not available in historical data
        "sector_momentum": None,   # computed separately if needed
        "price": _f(row.get("close")),
        # Regime — filled after regime model is trained
        "regime_label": None,
        # Pattern booleans
        "pattern_double_bottom": _b(row.get("pattern_double_bottom")),
        "pattern_bullish_engulfing": _b(row.get("pattern_bullish_engulfing")),
        "pattern_bearish_engulfing": _b(row.get("pattern_bearish_engulfing")),
        "pattern_bull_flag": _b(row.get("pattern_bull_flag")),
        "pattern_rsi_divergence": _b(row.get("pattern_rsi_divergence")),
        "pattern_macd_crossover": _b(row.get("pattern_macd_crossover")),
        "pattern_volume_surge": _b(row.get("pattern_volume_surge")),
        "pattern_head_and_shoulders": _b(row.get("pattern_head_and_shoulders")),
        # Outcomes
        "outcome_1d": _s(row.get("outcome_1d")),
        "outcome_5d": _s(row.get("outcome_5d")),
        "return_1d": _f(row.get("return_1d")),
        "return_5d": _f(row.get("return_5d")),
        # Backfill metadata
        "source": "yfinance",
        "schema_version": 3,
        # Legacy compatibility fields (live labeling pipeline reads these)
        "outcome": _s(row.get("outcome_1d")),   # alias for 1-day
        "captured_at": timestamp,
        "prediction_price": _f(row.get("close")),
        "labeled_at": timestamp,
    }


# ── Batch upsert ─────────────────────────────────────────────────────────────


async def _upsert_batch(
    coll: Any,
    docs: list[dict[str, Any]],
) -> int:
    """Upsert a batch of documents on (ticker, timestamp).

    Returns the number of documents upserted or modified.
    """
    from pymongo import UpdateOne  # type: ignore[import-untyped]

    ops = [
        UpdateOne(
            {"ticker": d["ticker"], "timestamp": d["timestamp"]},
            {"$setOnInsert": d},
            upsert=True,
        )
        for d in docs
    ]
    if not ops:
        return 0

    result = await coll.bulk_write(ops, ordered=False)
    return result.upserted_count + result.modified_count


# ── Per-ticker pipeline ───────────────────────────────────────────────────────


async def process_ticker(
    ticker: str,
    coll: Any,
    years: int = YEARS_BACK,
    pattern_window: int = 60,
    dry_run: bool = False,
    skip_patterns: bool = False,
) -> dict[str, Any]:
    """Full pipeline for one ticker: download → indicators → patterns → outcomes → upsert.

    Parameters
    ----------
    ticker:
        Ticker symbol.
    coll:
        Motor collection handle.
    years:
        Years of history to download.
    pattern_window:
        Sliding window length for pattern detection.
    dry_run:
        If True, compute everything but skip MongoDB writes.
    skip_patterns:
        If True, skip pattern detection (faster for large universes).

    Returns
    -------
    dict
        Summary: ticker, rows_downloaded, rows_inserted, elapsed_s, error.
    """
    t0 = time.perf_counter()
    summary: dict[str, Any] = {
        "ticker": ticker, "rows_downloaded": 0,
        "rows_inserted": 0, "elapsed_s": 0.0, "error": None,
    }

    try:
        # 1. Download
        df = _download_ticker(ticker, years)
        if df is None or df.empty:
            summary["error"] = "download_failed"
            return summary

        summary["rows_downloaded"] = len(df)

        # 2. Indicators
        df = _compute_indicators(df)

        # 3. Patterns (CPU-heavy — skip for speed if requested)
        if not skip_patterns:
            df = _add_patterns(df, window=pattern_window)
        else:
            for pcol in [
                "pattern_double_bottom", "pattern_bullish_engulfing",
                "pattern_bearish_engulfing", "pattern_bull_flag",
                "pattern_rsi_divergence", "pattern_macd_crossover",
                "pattern_volume_surge", "pattern_head_and_shoulders",
            ]:
                df[pcol] = False

        # 4. Outcomes
        df = _compute_outcomes(df)

        # 5. Build docs
        docs: list[dict[str, Any]] = []
        for ts, row in df.iterrows():
            if not isinstance(ts, pd.Timestamp):
                ts = pd.Timestamp(ts, tz="UTC")
            elif ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            doc = _row_to_doc(ticker, row, ts.to_pydatetime())
            docs.append(doc)

        # 6. Upsert in batches
        if not dry_run and docs:
            inserted = 0
            for i in range(0, len(docs), BATCH_SIZE):
                batch = docs[i: i + BATCH_SIZE]
                inserted += await _upsert_batch(coll, batch)
            summary["rows_inserted"] = inserted
        elif dry_run:
            summary["rows_inserted"] = len(docs)
            log.info("[%s] DRY RUN — would insert %d rows", ticker, len(docs))

    except Exception as exc:  # noqa: BLE001
        log.error("[%s] Pipeline failed: %s", ticker, exc, exc_info=True)
        summary["error"] = str(exc)

    summary["elapsed_s"] = round(time.perf_counter() - t0, 1)
    return summary


# ── Progress report ───────────────────────────────────────────────────────────


def _print_summary(summaries: list[dict[str, Any]]) -> None:
    """Print a final backfill summary table."""
    total_rows = sum(s["rows_inserted"] for s in summaries)
    total_downloaded = sum(s["rows_downloaded"] for s in summaries)
    failed = [s for s in summaries if s["error"]]
    succeeded = [s for s in summaries if not s["error"]]

    print(f"\n{'='*65}")
    print("  RISEDUAL HISTORICAL BACKFILL — COMPLETE")
    print(f"{'='*65}")
    print(f"  Tickers processed : {len(summaries)}")
    print(f"  Succeeded         : {len(succeeded)}")
    print(f"  Failed            : {len(failed)}")
    print(f"  Rows downloaded   : {total_downloaded:,}")
    print(f"  Rows upserted     : {total_rows:,}")
    total_t = sum(s["elapsed_s"] for s in summaries)
    print(f"  Total time        : {total_t:.0f}s ({total_t/60:.1f} min)")

    if failed:
        print(f"\n  Failed tickers:")
        for s in failed:
            print(f"    {s['ticker']:<12} {s['error']}")

    print(f"\n  Top performers by row count:")
    top = sorted(succeeded, key=lambda s: s["rows_inserted"], reverse=True)[:10]
    for s in top:
        print(f"    {s['ticker']:<12} {s['rows_inserted']:>6} rows  ({s['elapsed_s']:.0f}s)")
    print(f"{'='*65}\n")


# ── MongoDB index setup ───────────────────────────────────────────────────────


async def _ensure_indexes(coll: Any) -> None:
    """Create indexes for efficient training queries."""
    from pymongo import ASCENDING, DESCENDING  # type: ignore[import-untyped]

    await coll.create_index(
        [("ticker", ASCENDING), ("timestamp", ASCENDING)],
        unique=True,
        name="ticker_timestamp_unique",
    )
    await coll.create_index(
        [("outcome_1d", ASCENDING), ("ticker", ASCENDING)],
        name="outcome_1d_ticker",
    )
    await coll.create_index(
        [("outcome_5d", ASCENDING), ("ticker", ASCENDING)],
        name="outcome_5d_ticker",
    )
    await coll.create_index(
        [("schema_version", ASCENDING)],
        name="schema_version",
    )
    await coll.create_index(
        [("timestamp", DESCENDING)],
        name="timestamp_desc",
    )
    log.info("MongoDB indexes ensured on %s.", COLLECTION)


# ── Entrypoint ────────────────────────────────────────────────────────────────


async def main(
    tickers: list[str],
    years: int,
    dry_run: bool,
    skip_patterns: bool,
) -> None:
    """Download, enrich, and insert all tickers."""
    print(f"\n{'='*65}")
    print("  RISEDUAL HISTORICAL BACKFILL")
    print(f"{'='*65}")
    print(f"  Tickers       : {len(tickers)}")
    print(f"  Years back    : {years}")
    print(f"  Patterns      : {'SKIP (--skip-patterns)' if skip_patterns else 'ON'}")
    print(f"  Dry run       : {dry_run}")
    print(f"  MongoDB       : {MONGO_URI}/{DB_NAME}.{COLLECTION}")
    print(f"{'='*65}\n")

    if dry_run:
        print("  [DRY RUN] MongoDB writes are disabled.\n")

    client: AsyncIOMotorClient = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    coll = db[COLLECTION]

    if not dry_run:
        await _ensure_indexes(coll)

    summaries: list[dict[str, Any]] = []

    for i, ticker in enumerate(tickers, 1):
        log.info("Processing %s (%d/%d)…", ticker, i, len(tickers))
        summary = await process_ticker(
            ticker=ticker,
            coll=coll,
            years=years,
            dry_run=dry_run,
            skip_patterns=skip_patterns,
        )
        summaries.append(summary)

        status = "OK" if not summary["error"] else f"FAIL({summary['error']})"
        log.info(
            "[%s] %s — %d rows downloaded, %d upserted in %.1fs",
            ticker, status,
            summary["rows_downloaded"],
            summary["rows_inserted"],
            summary["elapsed_s"],
        )

        # Small delay to respect yfinance rate limits between tickers
        if i < len(tickers):
            await asyncio.sleep(0.5)

    client.close()
    _print_summary(summaries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill RISEDUAL MongoDB with 15 years of labeled market snapshots."
    )
    parser.add_argument(
        "--tickers", nargs="+", default=None,
        help="Override the default ticker universe.",
    )
    parser.add_argument(
        "--years", type=int, default=YEARS_BACK,
        help="Years of history to download (default: 15).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute everything but skip MongoDB writes.",
    )
    parser.add_argument(
        "--skip-patterns", action="store_true",
        help=(
            "Skip pattern detection. Much faster for initial import; "
            "run a second pass with --tickers-only to backfill patterns later."
        ),
    )
    parser.add_argument(
        "--equities-only", action="store_true",
        help="Skip crypto tickers (BTC-USD, ETH-USD, etc.).",
    )
    parser.add_argument(
        "--crypto-only", action="store_true",
        help="Only process crypto tickers.",
    )
    args = parser.parse_args()

    # Build universe
    if args.tickers:
        universe = args.tickers
    elif args.equities_only:
        universe = SP50_TICKERS + ETF_TICKERS
    elif args.crypto_only:
        universe = CRYPTO_TICKERS
    else:
        universe = DEFAULT_UNIVERSE

    asyncio.run(
        main(
            tickers=universe,
            years=args.years,
            dry_run=args.dry_run,
            skip_patterns=args.skip_patterns,
        )
    )
