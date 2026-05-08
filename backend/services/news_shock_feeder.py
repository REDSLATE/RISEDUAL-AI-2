"""
News Shock feeder — Benzinga → equity_telemetry bridge.

Purpose
-------
Connects ``benzinga_news_service.fetch_news`` to
``equity_telemetry.record_measurement`` so Patent M's dormant
``NEWS_SHOCK`` failure-mode branch gains a reliable data feed. After
enough samples accumulate, ``get_telemetry(current_news_count=…)``
returns a populated ``MarketTelemetry.news_volume_zscore`` and the
classifier's pre-existing threshold (3.0σ) triggers the block.

Design contract
---------------
* **No decisions.** This module reads news counts, computes the count,
  records the count. It does NOT compute sentiment, does NOT call
  Patent M, does NOT alter any trade. Same discipline as the Smart
  Money Verification service — a pure data lane.

* **Quota-aware.** Every call spends 1 Benzinga daily-ceiling unit.
  Schedulers that call this for many symbols must respect the
  per-process rate-limit lock inside ``benzinga_news_service``
  (serialized 2-second spacing) + the daily ceiling (default 500).

* **Silent on error.** Any upstream failure (disabled key, rate limit,
  network) records ``news_count=0`` for the window so the rolling
  buffer stays consistent. The ``meta`` block returned to the caller
  carries the error for observability.

* **Baseline-bootstrap safe.** A fresh-deploy with zero history
  records its first samples here; the classifier stays dormant until
  ``_baseline`` collects ≥ 3 samples (existing contract). No special
  handling needed.

Non-goal: sentiment
-------------------
``news_sentiment_abs`` is NOT populated here. Free-tier Benzinga
does not reliably return per-article sentiment; sentiment magnitude
needs either a paid Benzinga tier or an LLM pass over the titles.
When that's ready, a second feeder (``news_sentiment_feeder``) will
populate the complementary field.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Default window for "how many articles in the last N minutes" — sits
# alongside the 30-minute window inside ``MarketTelemetry`` contract.
# Tunable per-call, not per-deploy, so schedulers can choose.
DEFAULT_WINDOW_MINUTES = 30


async def fetch_and_record_news_telemetry(
    db: Any,
    symbol: str,
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    page_size: int = 50,
) -> dict[str, Any]:
    """Pull Benzinga news for ``symbol``, count the recent window,
    record into the rolling telemetry buffer.

    Returns a dict with:

    * ``symbol`` — upper-case
    * ``news_count`` — articles in the last ``window_minutes``
    * ``total_returned`` — articles Benzinga returned in total
    * ``recorded`` — True iff we persisted a sample
    * ``meta`` — raw meta block from the Benzinga client (disabled,
      rate_limited, error_code, daily_calls_used, …)

    On any upstream failure, records a sample with ``news_count=0``
    so the baseline stays time-contiguous. The caller can inspect
    ``meta.error_code`` to decide whether to alert.
    """
    from services.benzinga_news_service import (
        count_recent_articles,
        fetch_news,
    )
    from services import equity_telemetry

    sym = (symbol or "").upper().strip()
    if not sym:
        return {
            "symbol": "",
            "news_count": 0,
            "total_returned": 0,
            "recorded": False,
            "meta": {"error_code": "empty_symbol"},
        }

    fetch_result = await fetch_news(
        db,
        tickers=[sym],
        minutes=window_minutes,
        page_size=page_size,
        display_output="headline",
    )

    articles = fetch_result.get("articles") or []
    meta = fetch_result.get("meta") or {}

    # If the key is absent OR the daily ceiling tripped, DO NOT record
    # a sample — the "zero" reading would be meaningless (we don't
    # actually know how many articles there are). The caller gets a
    # clear error_code to act on. Only record when we got a real
    # (possibly empty) response from Benzinga.
    if meta.get("disabled") or meta.get("rate_limited"):
        return {
            "symbol": sym,
            "news_count": 0,
            "total_returned": 0,
            "recorded": False,
            "meta": meta,
        }

    # A networking / upstream error returns an empty list BUT we still
    # want to distinguish that from a legitimate zero-article window.
    # ``error_code`` is None when Benzinga responded with valid JSON
    # (even if empty); any other value is a failure mode and we skip
    # recording to avoid biasing the baseline.
    if meta.get("error_code") is not None:
        return {
            "symbol": sym,
            "news_count": 0,
            "total_returned": 0,
            "recorded": False,
            "meta": meta,
        }

    news_count = count_recent_articles(articles, minutes=window_minutes)

    try:
        await equity_telemetry.record_measurement(
            sym,
            news_count=news_count,
        )
        recorded = True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[news-shock-feeder] record failed for %s: %s", sym, exc,
        )
        recorded = False

    # Persist articles as ``catalyst_events`` for the Phase-C
    # statistical catalyst layer. One row per article, idempotent
    # via a stable ``benzinga:{id}`` key on ``event_id``. Failures
    # are best-effort — a broken persist can't block the telemetry
    # write above.
    try:
        await _persist_catalyst_events(db, sym, articles)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[news-shock-feeder] catalyst_events persist failed for %s: %s",
            sym, exc,
        )

    return {
        "symbol": sym,
        "news_count": news_count,
        "total_returned": len(articles),
        "recorded": recorded,
        "meta": meta,
    }


async def _persist_catalyst_events(
    db: Any, symbol: str, articles: list[dict[str, Any]],
) -> None:
    """Upsert Benzinga articles into ``catalyst_events``.

    Schema (matches ``news_shock_service.fetch_recent_events``)::

        {
            event_id: "benzinga:<id>",  # unique
            symbol: "AAPL",
            event_type: "NEWS",
            event_time: datetime (UTC),
            headline: "…",
            source: "benzinga",
            sentiment_score: None,       # free tier — not available
            raw: <full article dict>,
        }

    Benzinga free tier doesn't emit sentiment; the AV feeder's
    persistence pass fills the sentiment gap. When both feeders
    touch the same event window, sentiment-scored AV rows and
    volume-rich Benzinga rows co-exist and the compute step
    averages across what it finds.
    """
    if db is None or not articles:
        return
    from services.benzinga_news_service import _parse_benzinga_timestamp

    ops = []
    for a in articles:
        art_id = a.get("id")
        if art_id is None:
            continue
        event_time = _parse_benzinga_timestamp(
            a.get("created") or a.get("updated") or ""
        )
        if not event_time:
            continue
        event_id = f"benzinga:{art_id}"
        ops.append({
            "event_id": event_id,
            "symbol": symbol,
            "event_type": "NEWS",
            "event_time": event_time,
            "headline": (a.get("title") or "")[:300],
            "source": "benzinga",
            "sentiment_score": None,
            "url": a.get("url"),
        })

    # Sequential upserts — typical article counts are small (< 50)
    # and sequential keeps index contention off the hot path.
    coll = db.catalyst_events
    for doc in ops:
        try:
            await coll.update_one(
                {"event_id": doc["event_id"]},
                {"$set": doc},
                upsert=True,
            )
        except Exception:  # noqa: BLE001
            continue


async def batch_feed_symbols(
    db: Any,
    symbols: list[str],
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> dict[str, Any]:
    """Feed news-counts for a batch of symbols.

    Serialized by the internal Benzinga rate-limit lock — this call
    WILL take ``len(symbols) * BENZINGA_MIN_INTERVAL_SECONDS`` wall
    time. Callers scheduling this should pick the ``top_universe``
    subset (e.g., Tier A only) rather than every symbol, and cadence
    at market-hours intervals that respect the daily ceiling.

    Returns aggregate stats + per-symbol result for observability.
    """
    per_symbol: list[dict[str, Any]] = []
    fed = 0
    skipped = 0
    total_articles = 0

    for sym in symbols:
        result = await fetch_and_record_news_telemetry(
            db, sym, window_minutes=window_minutes,
        )
        per_symbol.append(result)
        if result.get("recorded"):
            fed += 1
        else:
            skipped += 1
        total_articles += int(result.get("news_count", 0) or 0)

        # Short-circuit the loop when the daily ceiling trips — no
        # point in serializing through 50 more calls that will all
        # bounce.
        meta = result.get("meta") or {}
        if meta.get("rate_limited") or meta.get("disabled"):
            break

    return {
        "fed": fed,
        "skipped": skipped,
        "total_articles": total_articles,
        "per_symbol": per_symbol,
    }
