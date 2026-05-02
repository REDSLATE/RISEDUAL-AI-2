"""
Alpha Vantage News Sentiment feeder — populates the second half of
Patent M's ``NEWS_SHOCK`` gate.

Context
-------
Free-tier Benzinga reliably emits article counts but NOT sentiment
scores. Alpha Vantage's paid ``NEWS_SENTIMENT`` endpoint does — every
article carries an ``overall_sentiment_score`` in ``[-1.0, 1.0]``.
Taking the magnitude of the aggregated score gives us
``news_sentiment_abs`` in ``[0.0, 1.0]``, matching the contract of
``MarketTelemetry.news_sentiment_abs``.

Gate interaction
----------------
Patent M's ``NEWS_SHOCK`` branch fires when BOTH:

    ``news_sentiment_abs >= 0.80`` AND ``news_volume_zscore >= 3.0``

Benzinga's feeder populates the volume leg; this feeder populates the
sentiment leg. With both populated, the dormant branch becomes a real
``block_trade=False, risk_multiplier_cap=0.50`` gate that throttles
sizing on a spike of strongly-sentimental coverage.

Design contract
---------------
1. **Same quota-aware discipline** as the Benzinga feeder — a per-UTC-day
   Mongo counter (``alpha_vantage_news_call_stats``) + a configurable
   ceiling + a strict serialization lock prevents us from blowing AV's
   125-req/min paid tier (used conservatively because the War Room +
   backtest layers share the same key).
2. **Aggregation**: articles in the last ``window_minutes`` are
   averaged by their ``overall_sentiment_score``; magnitude is
   ``abs(avg_score)`` in [0, 1]. Articles outside the window are
   dropped (so a 4-hour-old blockbuster doesn't keep inflating the
   spike signal hours later).
3. **Silent on error**: same fail-safe shape as the Benzinga path —
   ``recorded=False`` on any upstream failure, baseline never biased
   toward zero by outages.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.alphavantage.co/query"
_FUNCTION = "NEWS_SENTIMENT"

# Paid AV tier is typically 75-150 req/min, shared across the app.
# 600 calls/day is a conservative default for the sentiment feeder
# alone — 30-minute cadence over 20 Tier-A symbols = ~320/day, leaves
# headroom for the War Room + other callers.
_DEFAULT_DAILY_CEILING = 600
_DEFAULT_MIN_INTERVAL_SECONDS = 1.5

_rate_lock = asyncio.Lock()
_last_call_monotonic: float = 0.0

DEFAULT_WINDOW_MINUTES = 30


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _api_key() -> str:
    return (
        os.environ.get("ALPHA_VANTAGE_API_KEY")
        or os.environ.get("ALPHAVANTAGEAPIKEY")
        or ""
    ).strip()


def _daily_ceiling() -> int:
    return max(0, _env_int("AV_NEWS_SENTIMENT_DAILY_CEILING", _DEFAULT_DAILY_CEILING))


def _min_interval_seconds() -> float:
    return max(0.0, _env_float("AV_NEWS_SENTIMENT_MIN_INTERVAL_SECONDS", _DEFAULT_MIN_INTERVAL_SECONDS))


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _daily_call_count(db: Any) -> int:
    if db is None:
        return 0
    try:
        doc = await db.alpha_vantage_news_call_stats.find_one(
            {"_id": _today_key()}, {"count": 1}
        )
        return int((doc or {}).get("count", 0))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[av-news] daily count read failed (fail-safe to 0): %s", exc)
        return 0


async def _increment_daily_count(db: Any) -> None:
    if db is None:
        return
    try:
        await db.alpha_vantage_news_call_stats.update_one(
            {"_id": _today_key()},
            {
                "$inc": {"count": 1},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[av-news] daily count increment failed: %s", exc)


async def _rate_limit_wait() -> None:
    global _last_call_monotonic
    async with _rate_lock:
        elapsed = time.monotonic() - _last_call_monotonic
        wait = _min_interval_seconds() - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_monotonic = time.monotonic()


def _parse_av_timestamp(raw: str | None) -> datetime | None:
    """AV emits ``time_published`` as ``"20260502T193000"`` (UTC, no
    separator, no tz). Strict parse → tz-aware UTC datetime or None."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def compute_sentiment_magnitude(
    feed: list[dict[str, Any]],
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> tuple[float, int]:
    """Aggregate sentiment magnitude over recent articles.

    Returns ``(magnitude, article_count)`` where:

    * ``magnitude`` = ``abs(avg(overall_sentiment_score))`` in [0.0, 1.0],
      clamped. Empty window → 0.0.
    * ``article_count`` = number of articles that fell inside the window
      (useful for confidence signaling downstream).

    Articles with unparseable / missing timestamps are treated as
    out-of-window — same fail-safe discipline as
    ``benzinga_news_service.count_recent_articles``.
    """
    if not feed:
        return 0.0, 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(window_minutes)))
    scores: list[float] = []
    for row in feed:
        ts = _parse_av_timestamp(row.get("time_published"))
        if not ts or ts < cutoff:
            continue
        raw = row.get("overall_sentiment_score")
        try:
            score = float(raw)
        except (TypeError, ValueError):
            continue
        # AV's score is in [-1, 1]; clamp defensively.
        scores.append(max(-1.0, min(1.0, score)))
    if not scores:
        return 0.0, 0
    avg = sum(scores) / len(scores)
    return min(1.0, abs(avg)), len(scores)


async def fetch_and_record_sentiment(
    db: Any,
    symbol: str,
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    limit: int = 50,
) -> dict[str, Any]:
    """Pull AV sentiment-scored news for ``symbol``, compute magnitude
    over the window, record into ``equity_telemetry`` rolling buffer.

    Returns::

        {
            "symbol": "AAPL",
            "sentiment_abs": 0.32,
            "article_count": 8,
            "total_returned": 50,
            "recorded": True,
            "meta": {
                "disabled": False,
                "rate_limited": False,
                "error_code": None | "missing_key" | "daily_ceiling_hit" |
                              "rate_limited" | "upstream" | "network",
                "daily_calls_used": int,
                "fetched_at": ISO UTC,
            },
        }
    """
    from services import equity_telemetry

    now_iso = datetime.now(timezone.utc).isoformat()
    sym = (symbol or "").upper().strip()
    if not sym:
        return {
            "symbol": "",
            "sentiment_abs": 0.0,
            "article_count": 0,
            "total_returned": 0,
            "recorded": False,
            "meta": {"error_code": "empty_symbol", "fetched_at": now_iso},
        }

    if not _api_key():
        return {
            "symbol": sym,
            "sentiment_abs": 0.0,
            "article_count": 0,
            "total_returned": 0,
            "recorded": False,
            "meta": {
                "disabled": True,
                "error_code": "missing_key",
                "fetched_at": now_iso,
            },
        }

    ceiling = _daily_ceiling()
    used = await _daily_call_count(db)
    if ceiling > 0 and used >= ceiling:
        logger.warning(
            "[av-news] daily ceiling hit (%d/%d) — declining call",
            used, ceiling,
        )
        return {
            "symbol": sym,
            "sentiment_abs": 0.0,
            "article_count": 0,
            "total_returned": 0,
            "recorded": False,
            "meta": {
                "disabled": False,
                "rate_limited": True,
                "error_code": "daily_ceiling_hit",
                "daily_calls_used": used,
                "fetched_at": now_iso,
            },
        }

    await _rate_limit_wait()

    params = {
        "function": _FUNCTION,
        "tickers": sym,
        "limit": max(1, min(200, int(limit))),
        "apikey": _api_key(),
    }

    error_code: str | None = None
    feed: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.get(_BASE_URL, params=params)

        if resp.status_code != 200:
            error_code = "upstream"
        else:
            try:
                data = resp.json()
            except ValueError:
                error_code = "non_json_response"
                data = {}
            # AV signals quota / throttle errors in-body, not via HTTP status.
            if "Note" in data or "Information" in data:
                logger.info(
                    "[av-news] rate_limited by AV: %s",
                    (data.get("Note") or data.get("Information") or "")[:200],
                )
                error_code = "rate_limited"
            else:
                feed = data.get("feed") or []
    except (httpx.NetworkError, httpx.TimeoutException) as exc:
        logger.info("[av-news] network error for %s: %s", sym, exc)
        error_code = "network"

    await _increment_daily_count(db)

    # Only record a sample on a CLEAN response. Upstream failures skip
    # recording so the rolling baseline isn't biased toward zero.
    if error_code is not None:
        return {
            "symbol": sym,
            "sentiment_abs": 0.0,
            "article_count": 0,
            "total_returned": len(feed),
            "recorded": False,
            "meta": {
                "disabled": False,
                "rate_limited": error_code == "rate_limited",
                "error_code": error_code,
                "daily_calls_used": used + 1,
                "fetched_at": now_iso,
            },
        }

    magnitude, article_count = compute_sentiment_magnitude(
        feed, window_minutes=window_minutes,
    )

    try:
        await equity_telemetry.record_measurement(
            sym,
            news_sentiment_abs=magnitude,
        )
        recorded = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[av-news] record failed for %s: %s", sym, exc)
        recorded = False

    return {
        "symbol": sym,
        "sentiment_abs": round(magnitude, 4),
        "article_count": article_count,
        "total_returned": len(feed),
        "recorded": recorded,
        "meta": {
            "disabled": False,
            "rate_limited": False,
            "error_code": None,
            "daily_calls_used": used + 1,
            "fetched_at": now_iso,
        },
    }


async def batch_feed_sentiment(
    db: Any,
    symbols: list[str],
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> dict[str, Any]:
    """Batch feeder — short-circuits on ceiling hit (same shape as
    the Benzinga batch feeder)."""
    per_symbol: list[dict[str, Any]] = []
    fed = 0
    skipped = 0
    total_sentiment_sum = 0.0

    for sym in symbols:
        result = await fetch_and_record_sentiment(
            db, sym, window_minutes=window_minutes,
        )
        per_symbol.append(result)
        if result.get("recorded"):
            fed += 1
            total_sentiment_sum += float(result.get("sentiment_abs", 0.0) or 0.0)
        else:
            skipped += 1

        meta = result.get("meta") or {}
        if meta.get("rate_limited") or meta.get("disabled"):
            break

    return {
        "fed": fed,
        "skipped": skipped,
        "avg_sentiment_abs": round(total_sentiment_sum / fed, 4) if fed else 0.0,
        "per_symbol": per_symbol,
    }
