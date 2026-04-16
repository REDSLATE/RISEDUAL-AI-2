"""Backfill ``insider_activity`` on equity rows in features_snapshots.

Uses Finnhub's ``GET /stock/insider-transactions`` endpoint to fetch insider
trading data (SEC Form 4 filings).  For each ticker, computes a normalized
insider activity score per month:

    insider_activity = (buy_shares - sell_shares) / (buy_shares + sell_shares)
                     ∈ [-1.0, 1.0]

    +1.0 = all insider transactions are purchases (strong bullish signal)
    -1.0 = all insider transactions are sales (bearish signal)
     0.0 = balanced or no transactions (neutral)

The score is assigned to all snapshot rows for that ticker within the
transaction's month.  Months with no insider transactions get None (not 0.0),
preserving the distinction between "no data" and "neutral activity".

Disk cache
----------
Fetched insider data is cached to ``~/.risedual/insider_cache/<TICKER>.json``.
On resume, cached tickers are loaded from disk — no re-fetch.

Rate limiting
-------------
Shares the same token bucket pattern as backfill_sentiment.py.
Each ticker = 1 Finnhub request.  49 equity tickers = 49 requests total.
At 165 req/min (3 keys) this completes in ~18 seconds.

Usage:
    FINNHUB_API_KEY=<key> python scripts/backfill_insider_activity.py
    FINNHUB_API_KEY=<key> python scripts/backfill_insider_activity.py --ticker AAPL
    FINNHUB_API_KEY=<key> python scripts/backfill_insider_activity.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("backfill_insider")

# ── Configuration ─────────────────────────────────────────────────────────────

MONGO_URI = os.getenv("MONGO_URL", os.getenv("MONGO_URI", "mongodb://localhost:27017"))
DB_NAME = os.getenv("DB_NAME", "risedual_db")
COLLECTION = "features_snapshots"

FINNHUB_API_KEY: str | None = os.getenv("FINNHUB_API_KEY")
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

_DEFAULT_CACHE_DIR: Path = (
    Path(os.environ["RISEDUAL_CACHE_DIR"])
    if os.getenv("RISEDUAL_CACHE_DIR")
    else Path.home() / ".risedual" / "insider_cache"
)

EQUITY_TICKERS: list[str] = [
    "AAPL", "MSFT", "NVDA", "GOOG", "AMZN", "META", "TSLA", "AVGO", "ORCL",
    "JPM", "V", "MA", "BAC", "WFC", "GS", "AXP", "BLK",
    "UNH", "LLY", "JNJ", "TMO", "ABT", "SYK",
    "XOM", "CVX", "GE", "HON", "CAT", "UPS", "LMT", "RTX",
    "WMT", "HD", "COST", "TGT", "SBUX", "MCD", "NKE", "PG", "KO",
]

BATCH_SIZE = 500

# Transaction codes: P = purchase, S = sale
BUY_CODES = {"P", "A", "M", "C", "G"}   # Purchase, Award, Conversion, Grant
SELL_CODES = {"S", "D", "F"}              # Sale, Disposition, Tax withholding


# ── Token bucket ──────────────────────────────────────────────────────────────

class _TokenBucket:
    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self._capacity = capacity
        self._tokens = capacity
        self._refill = refill_per_sec
        self._last = time.monotonic()

    def _refill_tokens(self) -> None:
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._refill)
        self._last = now

    async def acquire(self) -> None:
        while True:
            self._refill_tokens()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            await asyncio.sleep((1.0 - self._tokens) / self._refill)


# ── Key rotator ──────────────────────────────────────────────────────────────

class _KeyRotator:
    def __init__(self, *keys: str) -> None:
        self._keys = list(keys)
        self._idx = 0

    def next(self) -> str:
        key = self._keys[self._idx % len(self._keys)]
        self._idx += 1
        return key

    def __len__(self) -> int:
        return len(self._keys)


# ── Disk cache ────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: Path, ticker: str) -> Path:
    return cache_dir / f"{ticker}.json"


def _load_cache(cache_dir: Path, ticker: str) -> list[dict] | None:
    path = _cache_path(cache_dir, ticker)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(cache_dir: Path, ticker: str, data: list[dict]) -> None:
    path = _cache_path(cache_dir, ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


# ── Finnhub fetch ─────────────────────────────────────────────────────────────

async def fetch_insider_transactions(
    client: httpx.AsyncClient,
    ticker: str,
    bucket: _TokenBucket,
    rotator: _KeyRotator,
    cache_dir: Path,
    force_fetch: bool = False,
) -> list[dict]:
    """Fetch insider transactions for a ticker (cached)."""
    if not force_fetch:
        cached = _load_cache(cache_dir, ticker)
        if cached is not None:
            log.debug("[%s] Loaded %d transactions from cache", ticker, len(cached))
            return cached

    await bucket.acquire()
    key = rotator.next()
    try:
        resp = await client.get(
            f"{FINNHUB_BASE_URL}/stock/insider-transactions",
            params={"symbol": ticker, "token": key},
            timeout=15.0,
        )
        if resp.status_code == 429:
            log.warning("[%s] 429 — backing off 60s", ticker)
            await asyncio.sleep(60)
            return []
        if resp.status_code != 200:
            log.warning("[%s] HTTP %d", ticker, resp.status_code)
            return []

        data = resp.json()
        txns = data.get("data", [])
        _save_cache(cache_dir, ticker, txns)
        log.info("[%s] Fetched %d insider transactions", ticker, len(txns))
        return txns

    except Exception as exc:
        log.warning("[%s] Fetch failed: %s", ticker, exc)
        return []


# ── Score computation ─────────────────────────────────────────────────────────

def compute_monthly_insider_score(txns: list[dict]) -> dict[str, float]:
    """Compute normalized insider activity score per month.

    Returns dict of "YYYY-MM" → score ∈ [-1.0, 1.0].
    """
    monthly_buys: dict[str, float] = defaultdict(float)
    monthly_sells: dict[str, float] = defaultdict(float)

    for txn in txns:
        date_str = txn.get("transactionDate") or txn.get("filingDate")
        if not date_str:
            continue
        month_key = date_str[:7]  # "YYYY-MM"

        code = (txn.get("transactionCode") or "").upper()
        change = abs(float(txn.get("change", 0)))

        if code in BUY_CODES or (txn.get("change", 0) > 0 and code not in SELL_CODES):
            monthly_buys[month_key] += change
        elif code in SELL_CODES or txn.get("change", 0) < 0:
            monthly_sells[month_key] += change

    # Compute score per month
    all_months = set(monthly_buys.keys()) | set(monthly_sells.keys())
    scores: dict[str, float] = {}

    for month in all_months:
        buys = monthly_buys.get(month, 0)
        sells = monthly_sells.get(month, 0)
        total = buys + sells
        if total > 0:
            scores[month] = round((buys - sells) / total, 6)
        else:
            scores[month] = 0.0

    return scores


# ── Per-ticker backfill ───────────────────────────────────────────────────────

async def process_ticker(
    ticker: str,
    db: Any,
    client: httpx.AsyncClient,
    bucket: _TokenBucket,
    rotator: _KeyRotator,
    cache_dir: Path,
    force_fetch: bool,
    force_write: bool,
    dry_run: bool,
) -> tuple[int, int]:
    """Fetch insider data, compute scores, update MongoDB."""
    coll = db[COLLECTION]

    txns = await fetch_insider_transactions(
        client, ticker, bucket, rotator, cache_dir, force_fetch
    )

    if not txns:
        log.info("[%s] No insider transactions available", ticker)
        return 0, 0

    monthly_scores = compute_monthly_insider_score(txns)
    if not monthly_scores:
        return 0, 0

    # Load snapshot rows
    query: dict[str, Any] = {"ticker": ticker}
    if not force_write:
        query["insider_activity"] = None

    rows = await coll.find(query, {"_id": 1, "timestamp": 1}).to_list(length=None)
    if not rows:
        return 0, 0

    # Match rows to monthly scores
    ops: list[UpdateOne] = []
    for row in rows:
        ts = row.get("timestamp")
        if ts is None:
            continue
        month_key = ts.strftime("%Y-%m") if hasattr(ts, "strftime") else str(ts)[:7]
        score = monthly_scores.get(month_key)
        if score is None:
            continue

        ops.append(UpdateOne(
            {"_id": row["_id"]},
            {"$set": {"insider_activity": score, "insider_source": "finnhub_sec"}},
        ))

    updated = 0
    if ops and not dry_run:
        for i in range(0, len(ops), BATCH_SIZE):
            batch = ops[i:i + BATCH_SIZE]
            result = await coll.bulk_write(batch, ordered=False)
            updated += result.modified_count
    elif ops:
        updated = len(ops)

    log.info(
        "[%s] %d transactions → %d monthly scores → %d/%d rows updated",
        ticker, len(txns), len(monthly_scores), updated, len(rows),
    )
    return len(rows), updated


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill insider_activity from Finnhub insider transactions."
    )
    parser.add_argument("--force-fetch", action="store_true")
    parser.add_argument("--force-write", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ticker", type=str, default=None)
    args = parser.parse_args()

    if not FINNHUB_API_KEY:
        log.error("FINNHUB_API_KEY not set.")
        sys.exit(1)

    # Build key rotator
    keys = [FINNHUB_API_KEY]
    for i in range(2, 20):
        extra = os.getenv(f"FINNHUB_API_KEY_{i}")
        if extra:
            keys.append(extra)
        else:
            break

    rotator = _KeyRotator(*keys)
    rate_per_sec = (55.0 * len(keys)) / 60.0
    capacity = 55.0 * len(keys)
    bucket = _TokenBucket(capacity=capacity, refill_per_sec=rate_per_sec)

    tickers = [args.ticker.upper()] if args.ticker else EQUITY_TICKERS
    cache_dir = _DEFAULT_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    log.info(
        "Starting insider_activity backfill — tickers=%d  keys=%d  rate=%.0f req/min",
        len(tickers), len(keys), capacity,
    )

    mongo = AsyncIOMotorClient(MONGO_URI)
    db = mongo[DB_NAME]
    total_rows = 0
    total_updated = 0

    async with httpx.AsyncClient() as client:
        for i, ticker in enumerate(tickers, 1):
            log.info("[%d/%d] %s", i, len(tickers), ticker)
            rows, updated = await process_ticker(
                ticker, db, client, bucket, rotator, cache_dir,
                force_fetch=args.force_fetch,
                force_write=args.force_write,
                dry_run=args.dry_run,
            )
            total_rows += rows
            total_updated += updated

    mongo.close()

    print(f"\n{'='*60}")
    print("  INSIDER ACTIVITY BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"  Tickers processed : {len(tickers)}")
    print(f"  Rows found        : {total_rows:,}")
    print(f"  Rows updated      : {total_updated:,}")
    print(f"  Dry run           : {args.dry_run}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
