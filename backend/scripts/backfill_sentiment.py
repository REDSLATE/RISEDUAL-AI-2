"""Backfill ``sentiment_score`` on every equity row in MongoDB.

Strategy — concurrent tickers + month-chunked fetches + disk cache
-------------------------------------------------------------------
Two layers of optimisation over the row-by-row baseline:

Layer 1 — Month chunking  (v11)
    One Finnhub request per ticker per calendar month instead of one per
    snapshot date.  Reduces requests from ~227,000 to ~10,800 (21×).

Layer 2 — Concurrent tickers  (v12)
    Multiple tickers fetch in parallel, all sharing a single token bucket.
    The bucket is the only rate-limit enforcer — every coroutine waits for
    a token before issuing its HTTP request, so the aggregate rate never
    exceeds 55 req/min regardless of concurrency.

    Worker count does NOT reduce total requests.  The speedup comes from
    overlapping I/O: while one ticker waits on an HTTP response or MongoDB
    write, other tickers can acquire tokens and issue their own requests.
    Without concurrency, each ticker stalls the whole pipeline on every
    individual roundtrip.

    Single key,  1 worker → ~196 min (purely sequential, v11 behaviour)
    Single key,  8 workers → ~157 min (~20% faster via I/O overlap)
    Two keys,    8 workers → ~79 min  (genuine 2× from doubled rate limit)

Disk cache
----------
Fetched months are written to ``~/.risedual/news_cache/<TICKER>/<YYYY-MM>.json``.
Cached months are skipped on resume — the script is fully restartable at
month granularity.  On a warm cache (all months fetched), runtime is
dominated by MongoDB writes, not HTTP.

Two-key mode
------------
Set ``FINNHUB_API_KEY_2`` to add a second Finnhub key.  The bucket capacity
doubles to 110 req/min and requests alternate between the two keys, giving
a genuine 2× throughput boost independent of worker count.

    Single key  + 8 workers → ~157 min
    Two keys    + 8 workers → ~79 min

Finnhub free-tier coverage note
--------------------------------
Free tier typically returns news for the past 1-2 years only.  Months
outside coverage return empty arrays and are cached as such (so no
re-fetch on resume).  Use ``--skip-empty`` to leave pre-coverage rows at
``None`` for a future paid-key run, then ``--force-fetch`` when upgrading.

Usage
-----
::

    # Default: 8 concurrent workers, skip-empty on
    FINNHUB_API_KEY=<key> DB_NAME=risedual_db \\
        nohup python scripts/backfill_sentiment.py > /tmp/sentiment.log 2>&1 &

    # Tune concurrency
    FINNHUB_API_KEY=<key> DB_NAME=risedual_db \\
        python scripts/backfill_sentiment.py --workers 4

    # Two-key mode (doubles throughput)
    FINNHUB_API_KEY=<key1> FINNHUB_API_KEY_2=<key2> DB_NAME=risedual_db \\
        python scripts/backfill_sentiment.py --workers 8

    # Resume after interruption (cached months skipped automatically)
    FINNHUB_API_KEY=<key> DB_NAME=risedual_db \\
        python scripts/backfill_sentiment.py

    # Re-fetch all months (e.g. after upgrading to paid tier)
    FINNHUB_API_KEY=<key> DB_NAME=risedual_db \\
        python scripts/backfill_sentiment.py --force-fetch

    # Single ticker dry-run for testing
    FINNHUB_API_KEY=<key> python scripts/backfill_sentiment.py --ticker AAPL --dry-run

Environment variables
---------------------
``FINNHUB_API_KEY``      Primary Finnhub API token (required).
``FINNHUB_API_KEY_2``    Optional second token — doubles throughput.
``DB_NAME``              MongoDB database name (default: ``risedual``).
``MONGO_URI``            MongoDB connection URI (default: localhost:27017).
``RISEDUAL_CACHE_DIR``   Override default cache directory.
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import itertools
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("backfill_sentiment")

# ── Configuration ─────────────────────────────────────────────────────────────

MONGO_URI: str = os.getenv("MONGO_URL", os.getenv("MONGO_URI", "mongodb://localhost:27017"))
DB_NAME: str = os.getenv("DB_NAME", "risedual_db")
COLLECTION: str = "features_snapshots"

FINNHUB_API_KEY: str | None = os.getenv("FINNHUB_API_KEY")
FINNHUB_BASE_URL: str = "https://finnhub.io/api/v1"

_DEFAULT_CACHE_DIR: Path = (
    Path(os.environ["RISEDUAL_CACHE_DIR"])
    if os.getenv("RISEDUAL_CACHE_DIR")
    else Path.home() / ".risedual" / "news_cache"
)

# Equity + ETF tickers (crypto excluded — Finnhub doesn't cover crypto news)
EQUITY_TICKERS: list[str] = [
    # S&P 50 (matching our DB tickers)
    "AAPL", "MSFT", "NVDA", "GOOG", "AMZN", "META", "TSLA", "AVGO", "ORCL",
    "JPM", "V", "MA", "BAC", "WFC", "GS", "AXP", "BLK",
    "UNH", "LLY", "JNJ", "TMO", "ABT", "SYK",
    "XOM", "CVX", "GE", "HON", "CAT", "UPS", "LMT", "RTX",
    "WMT", "HD", "COST", "TGT", "SBUX", "MCD", "NKE", "PG", "KO",
    # Sector / broad ETFs
    "SPY", "QQQ", "IWM", "DIA", "GLD", "TLT", "XLF", "XLK", "XLE",
]

# MongoDB bulk write batch size
BATCH_SIZE: int = 500

# ── VADER polarity scorer ─────────────────────────────────────────────────────

def _build_scorer() -> Any:
    """Return a callable ``(text: str) → float ∈ [-1, 1]``.

    Tries ``vaderSentiment`` first; falls back to a bag-of-words scorer.
    """
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer  # type: ignore[import-untyped]
        analyzer = SentimentIntensityAnalyzer()
        log.info("Sentiment scorer: vaderSentiment (VADER compound)")
        return lambda text: analyzer.polarity_scores(text)["compound"]
    except ImportError:
        log.warning(
            "vaderSentiment not installed — using bag-of-words fallback. "
            "For better accuracy: pip install vaderSentiment"
        )

    _POS = frozenset({
        "beat", "beats", "record", "growth", "profit", "gain", "gains",
        "surge", "surges", "rally", "rallied", "strong", "upgrade", "upgrades",
        "buy", "outperform", "positive", "bullish", "higher", "rise",
        "rises", "rose", "climbs", "momentum", "revenue", "exceeds", "exceeded",
        "dividend", "acquisition", "innovation", "breakthrough", "expansion",
    })
    _NEG = frozenset({
        "miss", "misses", "missed", "loss", "losses", "decline", "declines",
        "drop", "drops", "fell", "fall", "negative", "bearish", "downgrade",
        "downgrades", "sell", "underperform", "weak", "warning", "cut", "cuts",
        "layoff", "layoffs", "recall", "fraud", "investigation", "lawsuit",
        "fine", "fines", "slump", "crash", "default", "bankruptcy", "concern",
    })

    def _bow(text: str) -> float:
        words = text.lower().split()
        pos = sum(1 for w in words if w in _POS)
        neg = sum(1 for w in words if w in _NEG)
        total = pos + neg
        return 0.0 if total == 0 else (pos - neg) / total

    return _bow


_score_text = _build_scorer()


# ── Token bucket (shared across all concurrent workers) ───────────────────────

class _TokenBucket:
    """Async-safe token bucket.

    A single instance is shared across all concurrent ticker coroutines.
    Every ``await bucket.acquire()`` call blocks until a token is available,
    ensuring aggregate throughput never exceeds the configured rate regardless
    of worker count.
    """

    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self._capacity = capacity
        self._tokens = capacity
        self._refill = refill_per_sec
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume one."""
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._last) * self._refill,
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._refill
            # unreachable — loop exits via return
            await asyncio.sleep(wait)  # type: ignore[unreachable]


# ── Key rotator (two-key mode) ────────────────────────────────────────────────

class _KeyRotator:
    """Round-robin API key selector.

    When ``FINNHUB_API_KEY_2`` is set, alternates between the two keys so
    each carries roughly half the load.  With a single key it always returns
    the same value.
    """

    def __init__(self, *keys: str) -> None:
        self._cycle = itertools.cycle(keys)
        self._count = len(keys)
        log.info("Finnhub key rotator: %d key(s) active", self._count)

    def next_key(self) -> str:
        """Return the next key in rotation."""
        return next(self._cycle)

    @property
    def count(self) -> int:
        return self._count


# ── Disk cache ────────────────────────────────────────────────────────────────

def _cache_path(cache_dir: Path, ticker: str, year: int, month: int) -> Path:
    return cache_dir / ticker / f"{year:04d}-{month:02d}.json"


def _load_cached_month(
    cache_dir: Path, ticker: str, year: int, month: int
) -> list[dict[str, Any]] | None:
    """Return cached articles or ``None`` if not on disk."""
    path = _cache_path(cache_dir, ticker, year, month)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("[cache] Corrupt %s — will re-fetch: %s", path.name, exc)
        return None


def _save_cached_month(
    cache_dir: Path,
    ticker: str,
    year: int,
    month: int,
    articles: list[dict[str, Any]],
) -> None:
    """Write articles to disk atomically."""
    path = _cache_path(cache_dir, ticker, year, month)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(articles, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("[cache] Write failed %s: %s", path.name, exc)


# ── Finnhub fetch ─────────────────────────────────────────────────────────────

async def _fetch_month(
    client: httpx.AsyncClient,
    bucket: _TokenBucket,
    rotator: _KeyRotator,
    ticker: str,
    year: int,
    month: int,
    cache_dir: Path,
    force_fetch: bool = False,
) -> list[dict[str, Any]]:
    """Fetch (or load from cache) one calendar month of news for *ticker*.

    Acquires one token from the shared bucket before making any HTTP call.
    Cached months bypass the bucket entirely.

    Returns
    -------
    list[dict]
        Raw Finnhub news items for the month.  Empty list on error.
    """
    # Cache hit — no token needed
    if not force_fetch:
        cached = _load_cached_month(cache_dir, ticker, year, month)
        if cached is not None:
            return cached

    from_date = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    to_date = date(year, month, last_day)

    # Acquire token from shared bucket (blocks if rate limit is hit)
    await bucket.acquire()

    api_key = rotator.next_key()
    try:
        resp = await client.get(
            f"{FINNHUB_BASE_URL}/company-news",
            params={
                "symbol": ticker,
                "from": str(from_date),
                "to": str(to_date),
                "token": api_key,
            },
            timeout=20.0,
        )

        if resp.status_code == 429:
            log.warning("[finnhub] 429 for %s %04d-%02d — backing off 60s", ticker, year, month)
            await asyncio.sleep(60.0)
            return []   # not cached — will retry on next run

        if resp.status_code != 200:
            log.warning("[finnhub] HTTP %d for %s %04d-%02d", resp.status_code, ticker, year, month)
            return []

        data = resp.json()

        # Detect Finnhub 200-OK error payloads
        if isinstance(data, dict):
            for key in ("error", "Error Message", "Note", "Information"):
                if key in data:
                    log.warning("[finnhub] Error payload %s %04d-%02d: %s", ticker, year, month, data[key])
                    return []

        if not isinstance(data, list):
            return []

        _save_cached_month(cache_dir, ticker, year, month, data)
        return data

    except Exception as exc:  # noqa: BLE001
        log.warning("[finnhub] Request failed %s %04d-%02d: %s", ticker, year, month, exc)
        return []


# ── Sentiment helpers ─────────────────────────────────────────────────────────

def _articles_for_date(
    articles: list[dict[str, Any]], snap_date: date
) -> list[dict[str, Any]]:
    """Filter *articles* to those whose UTC date matches *snap_date*."""
    result: list[dict[str, Any]] = []
    for art in articles:
        ts = art.get("datetime")
        if not ts:
            continue
        try:
            if datetime.fromtimestamp(int(ts), tz=timezone.utc).date() == snap_date:
                result.append(art)
        except (ValueError, OSError, TypeError):
            result.append(art)  # include if timestamp unparseable
    return result


def _compute_sentiment(articles: list[dict[str, Any]]) -> float | None:
    """Return mean VADER compound score, or ``None`` if no articles."""
    if not articles:
        return None
    scores = [
        _score_text(f"{a.get('headline','')} {a.get('summary','')}".strip())
        for a in articles
        if f"{a.get('headline','')} {a.get('summary','')}".strip()
    ]
    return round(sum(scores) / len(scores), 6) if scores else None


def _month_range(start: date, end: date) -> list[tuple[int, int]]:
    """Return ``[(year, month), ...]`` from *start* month to *end* month inclusive."""
    months: list[tuple[int, int]] = []
    cur = date(start.year, start.month, 1)
    stop = date(end.year, end.month, 1)
    while cur <= stop:
        months.append((cur.year, cur.month))
        cur = (
            date(cur.year + 1, 1, 1) if cur.month == 12
            else date(cur.year, cur.month + 1, 1)
        )
    return months


# ── Per-ticker worker ─────────────────────────────────────────────────────────

async def _process_ticker(
    db: Any,
    client: httpx.AsyncClient,
    bucket: _TokenBucket,
    rotator: _KeyRotator,
    ticker: str,
    cache_dir: Path,
    force_fetch: bool,
    force_write: bool,
    skip_empty: bool,
    dry_run: bool,
    semaphore: asyncio.Semaphore,
    progress: "_Progress",
) -> None:
    """Fetch, score, and write sentiment for all rows of one *ticker*.

    Runs under *semaphore* to cap concurrent active workers.  The shared
    *bucket* ensures global throughput stays within the API rate limit.
    """
    async with semaphore:
        col = db[COLLECTION]

        # Pull only rows that need updating
        query: dict[str, Any] = {"ticker": ticker}
        if not force_write:
            query["sentiment_score"] = None

        rows = await col.find(query, {"_id": 1, "date": 1, "timestamp": 1}).to_list(length=None)
        if not rows:
            log.info("[%s] No rows to update.", ticker)
            progress.record(0, 0, 0)
            return

        # Resolve snapshot dates
        dated_rows: list[tuple[Any, date]] = []
        for row in rows:
            dt = row.get("date") or row.get("timestamp")
            if dt is None:
                continue
            if isinstance(dt, datetime):
                snap_date = dt.date()
            elif isinstance(dt, date):
                snap_date = dt
            else:
                try:
                    snap_date = datetime.fromisoformat(str(dt)).date()
                except ValueError:
                    continue
            dated_rows.append((row["_id"], snap_date))

        if not dated_rows:
            progress.record(0, 0, 0)
            return

        all_dates = [d for _, d in dated_rows]
        months = _month_range(min(all_dates), max(all_dates))
        log.info("[%s] %d rows  %d months", ticker, len(dated_rows), len(months))

        # Fetch all months for this ticker
        month_articles: dict[tuple[int, int], list[dict[str, Any]]] = {}
        months_fetched = 0
        for year, month in months:
            was_cached = not force_fetch and _load_cached_month(cache_dir, ticker, year, month) is not None
            articles = await _fetch_month(
                client, bucket, rotator, ticker, year, month, cache_dir, force_fetch
            )
            month_articles[(year, month)] = articles
            if not was_cached:
                months_fetched += 1

        # Score rows and queue bulk ops
        ops: list[UpdateOne] = []
        updated = 0

        for row_id, snap_date in dated_rows:
            day_arts = _articles_for_date(
                month_articles.get((snap_date.year, snap_date.month), []),
                snap_date,
            )
            score = _compute_sentiment(day_arts)

            if score is None and skip_empty and not force_write:
                continue

            if not dry_run:
                ops.append(
                    UpdateOne(
                        {"_id": row_id},
                        {"$set": {"sentiment_score": score, "sentiment_source": "finnhub"}},
                    )
                )
            updated += 1

            if len(ops) >= BATCH_SIZE:
                await col.bulk_write(ops, ordered=False)
                ops.clear()

        if ops:
            await col.bulk_write(ops, ordered=False)

        log.info("[%s] rows=%d  updated=%d  months_fetched=%d", ticker, len(dated_rows), updated, months_fetched)
        progress.record(len(dated_rows), updated, months_fetched)


# ── Progress tracker ──────────────────────────────────────────────────────────

class _Progress:
    """Thread-safe(ish) progress counter for concurrent workers."""

    def __init__(self, total: int, workers: int, keys: int) -> None:
        self._total = total
        self._workers = workers
        self._keys = keys
        self._done = 0
        self._rows = 0
        self._updated = 0
        self._fetches = 0
        self._start = time.monotonic()

    def record(self, rows: int, updated: int, fetches: int) -> None:
        self._done += 1
        self._rows += rows
        self._updated += updated
        self._fetches += fetches
        elapsed = time.monotonic() - self._start
        remaining = self._total - self._done
        rate = self._done / elapsed if elapsed > 0 else 0
        eta = f"{remaining / rate:.0f}s" if rate > 0 else "?"
        log.info(
            "Progress [%d/%d tickers]  rows_updated=%d  fetches=%d  "
            "elapsed=%.0fs  eta=%s  workers=%d  keys=%d",
            self._done, self._total, self._updated, self._fetches,
            elapsed, eta, self._workers, self._keys,
        )


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    """Entry point for the concurrent sentiment backfill script."""
    parser = argparse.ArgumentParser(
        description=(
            "Backfill sentiment_score — concurrent tickers, month-chunked, disk-cached.\n"
            "~10,800 requests. Runtime: ~25 min (8 workers, 1 key) / ~13 min (8 workers, 2 keys)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=8,
        help="Max concurrent tickers (default: 8). Does not increase request count.",
    )
    parser.add_argument(
        "--force-fetch", action="store_true",
        help="Bypass disk cache and re-fetch all months from Finnhub.",
    )
    parser.add_argument(
        "--force-write", action="store_true",
        help="Overwrite rows that already have sentiment_score set.",
    )
    parser.add_argument(
        "--skip-empty", action="store_true", default=True,
        help=(
            "Do not write None for months with no articles (default: on). "
            "Leaves rows untouched for a future paid-key run."
        ),
    )
    parser.add_argument(
        "--no-skip-empty", dest="skip_empty", action="store_false",
        help="Write None scores (overrides --skip-empty default).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Update disk cache but do not write to MongoDB.",
    )
    parser.add_argument(
        "--ticker", type=str, default=None,
        help="Limit to a single ticker (for testing).",
    )
    parser.add_argument(
        "--tickers-file", type=str, default=None,
        help="Path to newline-separated file of tickers.",
    )
    parser.add_argument(
        "--cache-dir", type=str, default=None,
        help=f"Disk cache directory (default: {_DEFAULT_CACHE_DIR}).",
    )
    args = parser.parse_args()

    if not FINNHUB_API_KEY:
        log.error(
            "FINNHUB_API_KEY not set.\n"
            "  risedual vault set FINNHUB_API_KEY"
        )
        sys.exit(1)

    # Build key rotator — collect FINNHUB_API_KEY, FINNHUB_API_KEY_2, _3, ... _N
    keys = [FINNHUB_API_KEY]
    for i in range(2, 20):
        extra = os.getenv(f"FINNHUB_API_KEY_{i}")
        if extra:
            keys.append(extra)
        else:
            break
    rotator = _KeyRotator(*keys)

    # Rate: 55 req/min per key; bucket capacity scales with key count
    rate_per_sec = (55.0 * len(keys)) / 60.0
    capacity = 55.0 * len(keys)
    bucket = _TokenBucket(capacity=capacity, refill_per_sec=rate_per_sec)
    log.info(
        "Rate limit: %.1f req/min (%d key(s) × 55)", capacity, len(keys)
    )

    cache_dir = Path(args.cache_dir) if args.cache_dir else _DEFAULT_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    log.info("Cache: %s", cache_dir)

    if args.ticker:
        tickers = [args.ticker.upper()]
    elif args.tickers_file:
        with open(args.tickers_file) as f:
            tickers = [l.strip().upper() for l in f if l.strip()]
    else:
        tickers = EQUITY_TICKERS

    # Estimate runtime
    months_per_ticker = 15 * 12  # 180
    total_months = months_per_ticker * len(tickers)
    # Wall clock: total_requests / rate — workers overlap I/O but share the bucket
    eta_min = total_months / (55.0 * len(keys)) * 0.8  # ~20% I/O overlap reduction
    log.info(
        "Starting — tickers=%d  workers=%d  keys=%d  "
        "max_requests=%d  estimated_runtime=%.1f min  "
        "skip_empty=%s  force_fetch=%s  dry_run=%s",
        len(tickers), args.workers, len(keys),
        total_months, eta_min,
        args.skip_empty, args.force_fetch, args.dry_run,
    )

    mongo_client: AsyncIOMotorClient = AsyncIOMotorClient(MONGO_URI)
    db = mongo_client[DB_NAME]
    semaphore = asyncio.Semaphore(args.workers)
    progress = _Progress(len(tickers), args.workers, len(keys))

    async with httpx.AsyncClient() as http_client:
        tasks = [
            asyncio.create_task(
                _process_ticker(
                    db=db,
                    client=http_client,
                    bucket=bucket,
                    rotator=rotator,
                    ticker=ticker,
                    cache_dir=cache_dir,
                    force_fetch=args.force_fetch,
                    force_write=args.force_write,
                    skip_empty=args.skip_empty,
                    dry_run=args.dry_run,
                    semaphore=semaphore,
                    progress=progress,
                )
            )
            for ticker in tickers
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    mongo_client.close()
    log.info("Sentiment backfill complete.")


if __name__ == "__main__":
    asyncio.run(main())
