"""Regime backfill — labels regime_label on every features_snapshots row.

After running ``backfill_historical.py`` and ``train_regime_model.py``,
this script loads the trained RegimeModel and updates every document in
``features_snapshots`` that has ``regime_label=None``.

It processes tickers in batches: for each ticker it loads all price rows
chronologically, predicts the regime for each bar using the rolling window,
and bulk-updates MongoDB.

Usage
-----
    python scripts/backfill_regimes.py
    python scripts/backfill_regimes.py --tickers AAPL BTC-USD SPY
    python scripts/backfill_regimes.py --dry-run

Environment variables
---------------------
    MONGO_URI       MongoDB connection string (default: mongodb://localhost:27017)
    DB_NAME         Database name            (default: risedual)
    MODELS_DIR      Path to joblib artefacts  (default: ./models)
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore[import-untyped]

from risedual_core.ml.regime_model import RegimeModel

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

MONGO_URI: str  = os.getenv("MONGO_URL", os.getenv("MONGO_URI",   "mongodb://localhost:27017"))
DB_NAME: str    = os.getenv("DB_NAME",     "risedual_db")
MODELS_DIR: Path = Path(os.getenv("MODELS_DIR", "models"))
COLLECTION: str  = "features_snapshots"
BATCH_SIZE: int  = 500


# ── Model loading ─────────────────────────────────────────────────────────────


def _load_latest_regime_model() -> RegimeModel | None:
    """Load the most recently saved regime model artefact."""
    if not MODELS_DIR.exists():
        return None
    candidates = sorted(
        MODELS_DIR.glob("regime_model_v*.joblib"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        log.error(
            "No regime model found in %s. "
            "Run scripts/train_regime_model.py first.",
            MODELS_DIR,
        )
        return None
    try:
        model = RegimeModel.load(candidates[0])
        log.info("Loaded regime model from %s", candidates[0].name)
        return model
    except Exception as exc:
        log.error("Failed to load regime model: %s", exc)
        return None


# ── Regime prediction for a price series ─────────────────────────────────────


def _predict_regimes_for_ticker(
    price_rows: list[dict[str, Any]],
    model: RegimeModel,
) -> dict[str, str]:
    """Predict regime for each row in a ticker's price history.

    Parameters
    ----------
    price_rows:
        List of MongoDB docs for one ticker, sorted by timestamp ascending.
        Each doc must have ``_id`` and ``price`` fields.
    model:
        Fitted :class:`RegimeModel`.

    Returns
    -------
    dict
        Mapping of ``str(_id)`` → regime label string.
    """
    import pandas as pd

    if not price_rows:
        return {}

    # Build price series
    prices = []
    ids = []
    for row in price_rows:
        p = row.get("price")
        if p is not None:
            try:
                prices.append(float(p))
                ids.append(str(row["_id"]))
            except (TypeError, ValueError):
                pass

    if len(prices) < 2:
        return {}

    ts = pd.date_range(start="2000-01-01", periods=len(prices), freq="D")
    price_series = pd.Series(prices, index=ts, dtype=float)

    try:
        labels = model.predict_series(price_series)
    except Exception as exc:
        log.warning("predict_series failed for ticker: %s", exc)
        return {}

    return {doc_id: str(label) for doc_id, label in zip(ids, labels)}


# ── Bulk update helpers ───────────────────────────────────────────────────────


async def _bulk_update_regimes(
    coll: Any,
    regime_map: dict[str, str],
    dry_run: bool,
) -> int:
    """Bulk-update ``regime_label`` on a batch of documents.

    Returns the number of documents modified.
    """
    if not regime_map or dry_run:
        return len(regime_map)

    from bson import ObjectId  # type: ignore[import-untyped]
    from pymongo import UpdateOne  # type: ignore[import-untyped]

    ops = [
        UpdateOne(
            {"_id": ObjectId(doc_id)},
            {"$set": {"regime_label": regime}},
        )
        for doc_id, regime in regime_map.items()
    ]

    result = await coll.bulk_write(ops, ordered=False)
    return result.modified_count


# ── Per-ticker pipeline ───────────────────────────────────────────────────────


async def process_ticker(
    ticker: str,
    coll: Any,
    model: RegimeModel,
    dry_run: bool,
    force: bool,
) -> dict[str, Any]:
    """Load rows for one ticker, predict regimes, bulk-update MongoDB."""
    t0 = time.perf_counter()
    summary = {
        "ticker": ticker,
        "rows_loaded": 0,
        "rows_updated": 0,
        "elapsed_s": 0.0,
        "error": None,
    }

    try:
        query: dict[str, Any] = {"ticker": ticker}
        if not force:
            query["regime_label"] = None  # only unlabeled rows

        cursor = coll.find(
            query,
            {"_id": 1, "price": 1, "timestamp": 1},
        ).sort("timestamp", 1)
        rows = await cursor.to_list(length=None)
        summary["rows_loaded"] = len(rows)

        if not rows:
            log.debug("[%s] No rows needing regime labels.", ticker)
            return summary

        regime_map = _predict_regimes_for_ticker(rows, model)

        if not regime_map:
            log.debug("[%s] No regime predictions produced.", ticker)
            return summary

        # Update in batches
        total_updated = 0
        keys = list(regime_map.keys())
        for i in range(0, len(keys), BATCH_SIZE):
            batch = {k: regime_map[k] for k in keys[i: i + BATCH_SIZE]}
            total_updated += await _bulk_update_regimes(coll, batch, dry_run)

        summary["rows_updated"] = total_updated
        log.info(
            "[%s] %s regime labels on %d rows (%.1fs)",
            ticker,
            "Would update" if dry_run else "Updated",
            total_updated,
            time.perf_counter() - t0,
        )

    except Exception as exc:
        log.error("[%s] Regime backfill failed: %s", ticker, exc, exc_info=True)
        summary["error"] = str(exc)

    summary["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return summary


# ── Main ──────────────────────────────────────────────────────────────────────


async def main(
    tickers: list[str] | None,
    dry_run: bool,
    force: bool,
) -> None:
    model = _load_latest_regime_model()
    if model is None:
        sys.exit(1)

    client: AsyncIOMotorClient = AsyncIOMotorClient(MONGO_URI)
    db = client[DB_NAME]
    coll = db[COLLECTION]

    # Discover tickers if not specified
    if not tickers:
        tickers = await coll.distinct("ticker")
        log.info("Discovered %d tickers in %s", len(tickers), COLLECTION)

    print(f"\n{'='*60}")
    print("  RISEDUAL REGIME BACKFILL")
    print(f"{'='*60}")
    print(f"  Tickers  : {len(tickers)}")
    print(f"  Dry run  : {dry_run}")
    print(f"  Force    : {force} ({'re-label all rows' if force else 'skip already labeled'})")
    print(f"{'='*60}\n")

    summaries: list[dict[str, Any]] = []
    for i, ticker in enumerate(tickers, 1):
        log.info("Processing %s (%d/%d)…", ticker, i, len(tickers))
        s = await process_ticker(
            ticker=ticker,
            coll=coll,
            model=model,
            dry_run=dry_run,
            force=force,
        )
        summaries.append(s)

    client.close()

    # ── Summary ──────────────────────────────────────────────────────────────
    total_loaded   = sum(s["rows_loaded"]   for s in summaries)
    total_updated  = sum(s["rows_updated"]  for s in summaries)
    total_elapsed  = sum(s["elapsed_s"]     for s in summaries)
    failed         = [s for s in summaries if s["error"]]

    print(f"\n{'='*60}")
    print("  REGIME BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"  Tickers processed : {len(summaries)}")
    print(f"  Rows loaded       : {total_loaded:,}")
    print(f"  Rows updated      : {total_updated:,}")
    print(f"  Failed tickers    : {len(failed)}")
    print(f"  Total time        : {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    if failed:
        for s in failed:
            print(f"    {s['ticker']:<14} {s['error']}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill regime_label on features_snapshots")
    parser.add_argument("--tickers", nargs="+", default=None)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be updated without writing to MongoDB.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-label all rows, not just ones with regime_label=None.",
    )
    args = parser.parse_args()
    asyncio.run(main(
        tickers=args.tickers,
        dry_run=args.dry_run,
        force=args.force,
    ))
