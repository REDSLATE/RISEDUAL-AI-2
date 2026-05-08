"""
NEWS_SHOCK service — Phase C statistical catalyst layer.

Converts the product spec (written in sync pymongo style) to the
app's native **async Motor** I/O. The math + thresholds + shock
classifier stay 1:1 with the spec so the operating rules remain:

    * NEWS_SHOCK may annotate, reduce size, and slightly move
      conviction — but it may NEVER create a trade, flip direction,
      or override hard vetoes (LOW_RR / LIQUIDITY_TRAP /
      CIRCUIT_BREAKER).
    * No z-score fires with fewer than ``MIN_BASELINE_SAMPLES`` (20)
      observations in the rolling window.

Collection contract (source of truth)
-------------------------------------
* ``catalyst_events`` — one row per ingested news / analyst event
  with ``{symbol, event_type, event_time, headline, source,
  sentiment_score, raw}``. Populated by ``benzinga_news_service`` and
  ``av_sentiment_feeder`` (persist hooks added in this phase).
* ``news_telemetry`` — one row per shock computation
  (``symbol, window_minutes, news_volume, sentiment_score,
  created_at``). Baseline volumes read from here.
* ``catalyst_snapshots`` — one doc per symbol
  (``{symbol, news_shock, event_risk, updated_at}``). Read by the IP
  contract, conviction service, Commander narrative builder, and
  Terminal aggregator.

All three collections are independent of ``equity_telemetry_baselines``
— that buffer carries atr/spread/volume/dollar_volume + the older
``news_count``/``news_sentiment_abs`` fields still used by Patent M's
pre-existing NEWS_SHOCK branch. The catalyst layer is a NEW,
complementary path. Both can fire a NEWS_SHOCK label.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


MIN_BASELINE_SAMPLES = 20
NEWS_LOOKBACK_MINUTES = 60
BASELINE_LOOKBACK_DAYS = 30

NEWS_SHOCK_Z_THRESHOLD = 2.5
NEWS_SHOCK_HIGH_Z_THRESHOLD = 4.0

SENTIMENT_MAGNITUDE_THRESHOLD = 0.35


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    variance = sum((x - avg) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def zscore(value: float, baseline_values: list[float]) -> float | None:
    """Return the z-score of ``value`` against ``baseline_values``, or
    ``None`` when the baseline is too small to be statistically
    meaningful. ``MIN_BASELINE_SAMPLES=20`` pins the no-false-positive
    window — a fresh-deploy symbol produces ``None`` for ~20 trading
    windows, after which the classifier starts firing."""
    if len(baseline_values) < MIN_BASELINE_SAMPLES:
        return None
    baseline_mean = mean(baseline_values)
    baseline_std = stddev(baseline_values)
    if baseline_std <= 0:
        return None
    return (value - baseline_mean) / baseline_std


def classify_sentiment(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= SENTIMENT_MAGNITUDE_THRESHOLD:
        return "bullish"
    if score <= -SENTIMENT_MAGNITUDE_THRESHOLD:
        return "bearish"
    return "neutral"


def classify_news_shock(news_z: float | None, sentiment_score: float | None) -> str:
    """Shock state ladder:
      * ``not_ready`` — baseline too small
      * ``normal`` — z-score below elevated threshold
      * ``elevated`` — z-score ≥ 2.5
      * ``high`` — z-score ≥ 4.0 AND sentiment magnitude ≥ 0.35

    A volume spike without a sentiment signal stays at ``elevated``;
    the ``high`` label requires directional conviction in the
    coverage itself so we don't restrict trading on neutral chatter."""
    if news_z is None:
        return "not_ready"
    sentiment_abs = abs(safe_float(sentiment_score, 0.0))
    if news_z >= NEWS_SHOCK_HIGH_Z_THRESHOLD and sentiment_abs >= SENTIMENT_MAGNITUDE_THRESHOLD:
        return "high"
    if news_z >= NEWS_SHOCK_Z_THRESHOLD:
        return "elevated"
    return "normal"


def compute_sentiment_score(events: list[dict[str, Any]]) -> float | None:
    """Average ``sentiment_score`` (signed) across events. Tolerates
    AV's ``overall_sentiment_score`` field name. Returns ``None`` when
    no event carries a sentiment reading — distinct from ``0.0``
    (explicitly neutral) so downstream can classify as ``unknown``."""
    scores: list[float] = []
    for event in events:
        raw = event.get("sentiment_score")
        if raw is None:
            raw = event.get("overall_sentiment_score")
        if raw is None:
            continue
        scores.append(safe_float(raw))
    if not scores:
        return None
    return round(mean(scores), 4)


def compute_news_volume(events: list[dict[str, Any]]) -> int:
    return len(events)


async def fetch_recent_events(db: Any, symbol: str, now: datetime) -> list[dict[str, Any]]:
    """Pull news / analyst events for ``symbol`` in the last
    ``NEWS_LOOKBACK_MINUTES`` minutes. Async Motor version of the
    spec's pymongo call."""
    since = now - timedelta(minutes=NEWS_LOOKBACK_MINUTES)
    cursor = db.catalyst_events.find(
        {
            "symbol": symbol,
            "event_type": {"$in": ["NEWS", "ANALYST"]},
            "event_time": {"$gte": since, "$lte": now},
        },
        {"_id": 0},
    ).sort("event_time", 1)
    return await cursor.to_list(length=500)


async def fetch_baseline_volumes(db: Any, symbol: str, now: datetime) -> list[float]:
    """Pull rolling baseline volumes from prior telemetry rows.
    30-day lookback keeps the baseline sensitive to regime shifts
    without capturing multi-quarter history."""
    since = now - timedelta(days=BASELINE_LOOKBACK_DAYS)
    cursor = db.news_telemetry.find(
        {
            "symbol": symbol,
            "window_minutes": NEWS_LOOKBACK_MINUTES,
            "created_at": {"$gte": since, "$lt": now},
        },
        {"_id": 0, "news_volume": 1},
    )
    rows = await cursor.to_list(length=2000)
    return [safe_float(row.get("news_volume")) for row in rows]


async def record_news_telemetry(
    db: Any,
    symbol: str,
    news_volume: int,
    sentiment_score: float | None,
    now: datetime,
) -> None:
    await db.news_telemetry.insert_one({
        "symbol": symbol,
        "window_minutes": NEWS_LOOKBACK_MINUTES,
        "news_volume": news_volume,
        "sentiment_score": sentiment_score,
        "created_at": now,
    })


async def compute_news_shock_for_symbol(
    db: Any,
    symbol: str,
    now: datetime | None = None,
    record_telemetry: bool = True,
) -> dict[str, Any]:
    """End-to-end: pull recent events + baseline → compute shock
    state → optionally append the telemetry row. Returns the full
    shock dict for downstream consumers.

    ``record_telemetry=False`` lets callers (e.g., admin status
    probes, unit tests) read without polluting the baseline. The
    scheduler always records."""
    now = now or utcnow()
    symbol = symbol.upper().strip()

    events = await fetch_recent_events(db, symbol, now)
    baseline = await fetch_baseline_volumes(db, symbol, now)

    news_volume = compute_news_volume(events)
    sentiment_score = compute_sentiment_score(events)
    sentiment_label = classify_sentiment(sentiment_score)

    news_z = zscore(float(news_volume), baseline)
    shock_state = classify_news_shock(news_z, sentiment_score)

    if record_telemetry:
        try:
            await record_news_telemetry(
                db=db,
                symbol=symbol,
                news_volume=news_volume,
                sentiment_score=sentiment_score,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001
            # Telemetry write failure must never block the classifier
            # from returning a current shock state — operators still
            # get the signal; the baseline just doesn't grow this tick.
            logger.warning("[news-shock] telemetry insert failed for %s: %s", symbol, exc)

    return {
        "symbol": symbol,
        "window_minutes": NEWS_LOOKBACK_MINUTES,
        "news_volume": news_volume,
        "baseline_samples": len(baseline),
        "zscore_ready": len(baseline) >= MIN_BASELINE_SAMPLES,
        "news_zscore": round(news_z, 4) if news_z is not None else None,
        "sentiment_score": sentiment_score,
        "sentiment_label": sentiment_label,
        "shock_state": shock_state,
        "latest_headline": events[-1].get("headline") if events else None,
        "latest_source": events[-1].get("source") if events else None,
        "computed_at": now,
    }


async def compute_news_shock_batch(
    db: Any,
    symbols: list[str],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Batch — sequential (serialization keeps the baseline-insert
    order deterministic per symbol, cheap compared to the upstream
    Benzinga/AV calls that already pace the outer loop)."""
    now = now or utcnow()
    results = []
    for symbol in symbols:
        results.append(
            await compute_news_shock_for_symbol(
                db=db, symbol=symbol, now=now, record_telemetry=True,
            )
        )
    return results
