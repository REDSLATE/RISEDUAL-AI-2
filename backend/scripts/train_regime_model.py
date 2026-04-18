"""Training script for the RISEDUAL regime model.

Uses yfinance (free, no API key) to fetch daily closing prices for a
configurable ticker universe, fits a RegimeModel (HMM by default, KMeans
fallback), and saves a versioned artefact to MODELS_DIR.

Usage:
    cd /app/backend && python scripts/train_regime_model.py
    cd /app/backend && python scripts/train_regime_model.py --tickers SPY QQQ IWM --method kmeans
    cd /app/backend && python scripts/train_regime_model.py --lookback 730
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from risedual_core.ml.regime_model import RegimeConfig, RegimeModel

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

MODELS_DIR: Path = Path(os.getenv("MODELS_DIR", "models"))
_DEFAULT_TICKERS = ["SPY", "QQQ", "IWM"]
_DEFAULT_METHOD = os.getenv("REGIME_METHOD", "hmm")
_DEFAULT_LOOKBACK = int(os.getenv("REGIME_LOOKBACK", "730"))


def fetch_daily_closes(ticker: str, lookback_days: int) -> pd.Series:
    """Fetch daily closing prices using yfinance."""
    import yfinance as yf

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)

    try:
        df = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                         end=end.strftime("%Y-%m-%d"), progress=False)
        if df.empty:
            log.warning("[%s] No data from yfinance", ticker)
            return pd.Series(dtype=float)

        closes = df["Close"].squeeze()
        if isinstance(closes, pd.DataFrame):
            closes = closes.iloc[:, 0]
        closes = closes.dropna()
        closes.name = ticker
        log.info("[%s] Fetched %d daily closes.", ticker, len(closes))
        return closes
    except Exception as exc:
        log.warning("[%s] yfinance fetch failed: %s", ticker, exc)
        return pd.Series(dtype=float)


def print_regime_stats(ticker: str, price_series: pd.Series, model: RegimeModel) -> None:
    """Print per-regime occurrence counts and current regime."""
    labels = model.predict_series(price_series)
    if labels.empty:
        print(f"  {ticker}: insufficient data for regime statistics")
        return

    counts = labels.value_counts().sort_index()
    current = labels.iloc[-1]
    total = len(labels)

    print(f"\n  {ticker} ({total} days):")
    for regime, n in counts.items():
        bar = "=" * int(n / total * 30)
        print(f"    {regime:<10} {n:>4}d  {n/total:>5.1%}  {bar}")
    print(f"    -> current regime: {current.upper()}")


def _next_version(models_dir: Path) -> int:
    """Return next available regime model version number."""
    existing = list(models_dir.glob("regime_model_v*.joblib"))
    if not existing:
        return 1
    versions = []
    for p in existing:
        try:
            v = int(p.stem.split("_v")[-1])
            versions.append(v)
        except ValueError:
            pass
    return max(versions, default=0) + 1


async def main(
    tickers: list[str],
    method: str,
    lookback_days: int,
    models_dir: Path,
) -> None:
    """Fetch prices, train regime model, save artefact."""
    models_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print("  RISEDUAL REGIME MODEL TRAINING")
    print(f"{'='*60}")
    print(f"  Tickers   : {', '.join(tickers)}")
    print(f"  Method    : {method}")
    print(f"  Lookback  : {lookback_days} days")

    primary_ticker = tickers[0]
    price_series = fetch_daily_closes(primary_ticker, lookback_days)

    if price_series.empty or len(price_series) < 70:
        print(f"\n[ERROR] Not enough price data for {primary_ticker} "
              f"({len(price_series)} rows). Need at least 70 days.")
        return

    cfg = RegimeConfig(
        method=method,
        lookback_days=min(60, len(price_series) - 1),
        model_version=f"1.0.0-{primary_ticker}",
    )
    model = RegimeModel(config=cfg)

    try:
        model.fit(price_series)
    except ImportError as exc:
        if "hmmlearn" in str(exc) and method == "hmm":
            print("\n[WARNING] hmmlearn not installed — falling back to kmeans.")
            cfg = RegimeConfig(
                method="kmeans",
                lookback_days=min(60, len(price_series) - 1),
                model_version=f"1.0.0-{primary_ticker}-kmeans",
            )
            model = RegimeModel(config=cfg)
            model.fit(price_series)
        else:
            raise

    # Print stats for all tickers
    print("\n-- Regime Distribution ----------------------------------------")
    print_regime_stats(primary_ticker, price_series, model)

    for ticker in tickers[1:]:
        extra_prices = fetch_daily_closes(ticker, lookback_days)
        if extra_prices.empty:
            print(f"\n  {ticker}: fetch failed - skipping")
            continue
        try:
            print_regime_stats(ticker, extra_prices, model)
        except Exception as exc:
            print(f"\n  {ticker}: predict failed - {exc}")

    # Save artefact
    version = _next_version(models_dir)
    out_path = models_dir / f"regime_model_v{version}.joblib"
    model.save(out_path)

    print(f"\n{'='*60}")
    print(f"  Saved -> {out_path}")
    print(f"  Method : {model.config.method}")
    print(f"  Version: {model.config.model_version}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train RISEDUAL regime model")
    parser.add_argument("--tickers", nargs="+", default=_DEFAULT_TICKERS)
    parser.add_argument("--method", choices=["hmm", "kmeans"], default=_DEFAULT_METHOD)
    parser.add_argument("--lookback", type=int, default=_DEFAULT_LOOKBACK, dest="lookback_days")
    parser.add_argument("--models-dir", type=Path, default=MODELS_DIR)
    args = parser.parse_args()

    asyncio.run(main(
        tickers=args.tickers,
        method=args.method,
        lookback_days=args.lookback_days,
        models_dir=args.models_dir,
    ))
