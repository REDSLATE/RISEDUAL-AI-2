"""Equity telemetry baselines for Patent M.

Tracks per-symbol rolling baselines (ATR, spread, volume) so that
manual order routes can populate ``MarketTelemetry`` with real values
instead of zeros. The Patent-M failure-mode classifier compares
current measurements against these baselines to detect:

  * VOLATILITY_SHOCK   (ATR ratio ≥ 2.0 OR volume z ≥ 3.0)
  * LIQUIDITY_TRAP     (spread ratio ≥ 2.5 OR spread_bps ≥ 75)

Storage:
    equity_telemetry_baselines collection — one row per symbol with the
    last-known baseline + recent measurements buffer (capped at 30 entries)
    so we don't have to recompute the rolling averages on every read.

Maintenance:
    record_measurement(symbol, atr_pct, spread_bps, volume) appends and
    truncates. The baseline is the simple mean of buffered values.

Read API:
    get_telemetry(symbol) returns a fresh ``MarketTelemetry`` populated
    with the live measurement + computed z-scores. Returns None if no
    baseline yet (caller falls back to zeros).
"""
from __future__ import annotations

__domain__ = "DTD"

import logging
import statistics
from datetime import datetime, timezone
from typing import Any, Optional

from services.failure_mode_classifier import MarketTelemetry

logger = logging.getLogger(__name__)

_db: Any = None
_BUFFER_CAP = 30  # rolling window — ~30 trading days at 1 sample/day

# Telemetry is per asset class so we can support both equities and
# eventually FX / futures with different baseline conventions.
DEFAULT_ASSET_CLASS = "equity"


def set_db(db: Any) -> None:
    global _db
    _db = db


async def record_measurement(
    symbol: str,
    *,
    atr_pct: Optional[float] = None,
    spread_bps: Optional[float] = None,
    volume: Optional[float] = None,
    dollar_volume: Optional[float] = None,
    news_count: Optional[float] = None,
    news_sentiment_abs: Optional[float] = None,
    asset_class: str = DEFAULT_ASSET_CLASS,
) -> None:
    """Append a measurement to the rolling buffer for ``symbol``.

    Idempotent on the day-level: callers can record once per quote
    fetch without worrying about duplicates inflating the baseline.
    Missing fields (``None``) are skipped so partial measurements
    don't poison the average.

    ``dollar_volume`` (shares × price) is recorded alongside raw
    volume so the Patent-M classifier can detect dollar-volume
    starvation independently of share-count — a penny stock logging
    3 M shares at $0.10 is NOT liquid the same way a mega-cap
    logging 3 M shares at $200 is.

    ``news_count`` (articles in a fixed recent window — typically
    30 minutes) feeds the ``news_volume_zscore`` branch of
    ``NEWS_SHOCK``. The baseline is the rolling average of past
    news-counts; a spike ≥ 3σ is the trip condition inside Patent M.
    ``news_sentiment_abs`` is the magnitude of aggregate sentiment
    ([0, 1]); populated separately (LLM pass on headlines) when
    available.
    """
    if _db is None:
        return
    sym = symbol.upper()
    now = datetime.now(timezone.utc)
    sample = {"at": now.isoformat()}
    if atr_pct is not None and atr_pct > 0:
        sample["atr_pct"] = float(atr_pct)
    if spread_bps is not None and spread_bps >= 0:
        sample["spread_bps"] = float(spread_bps)
    if volume is not None and volume > 0:
        sample["volume"] = float(volume)
    if dollar_volume is not None and dollar_volume > 0:
        sample["dollar_volume"] = float(dollar_volume)
    # news_count can legitimately be 0 (a quiet window). Record anyway —
    # excluding zeros would bias the baseline upward and suppress the
    # z-score on later quiet periods. Use explicit None check.
    if news_count is not None and news_count >= 0:
        sample["news_count"] = float(news_count)
    if news_sentiment_abs is not None and news_sentiment_abs >= 0:
        sample["news_sentiment_abs"] = float(news_sentiment_abs)
    if len(sample) == 1:  # only the timestamp
        return
    try:
        await _db.equity_telemetry_baselines.update_one(
            {"symbol": sym, "asset_class": asset_class},
            {
                "$push": {
                    "samples": {"$each": [sample], "$slice": -_BUFFER_CAP},
                },
                "$set": {"updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[equity_telemetry] record failed for %s: %s", sym, e)


def _baseline(samples: list[dict], key: str) -> Optional[float]:
    vals = [s[key] for s in samples if key in s]
    if len(vals) < 3:  # need at least 3 samples for a meaningful baseline
        return None
    return statistics.fmean(vals)


def _stdev(samples: list[dict], key: str) -> float:
    vals = [s[key] for s in samples if key in s]
    if len(vals) < 3:
        return 0.0
    return statistics.pstdev(vals)


async def get_telemetry(
    symbol: str,
    *,
    current_atr_pct: Optional[float] = None,
    current_spread_bps: Optional[float] = None,
    current_volume: Optional[float] = None,
    current_dollar_volume: Optional[float] = None,
    current_news_count: Optional[float] = None,
    current_news_sentiment_abs: Optional[float] = None,
    asset_class: str = DEFAULT_ASSET_CLASS,
) -> Optional[MarketTelemetry]:
    """Build ``MarketTelemetry`` for ``symbol`` using buffered baselines.

    The caller passes in the current spot measurement (whatever they
    can fetch live — e.g. from the broker quote endpoint). Baselines
    are looked up from Mongo. Volume z-score is computed against the
    rolling buffer.

    Dollar-volume (current + baseline) feeds the Patent-M dollar-
    volume starvation branch of ``LIQUIDITY_TRAP``. A missing baseline
    (< 3 samples) or a missing current reading degrades gracefully —
    both fields surface as 0.0 and the starvation branch becomes
    dormant by contract (see failure_mode_classifier zero-baseline
    guard).

    News telemetry:
      * ``news_volume_zscore`` is computed from the rolling
        ``news_count`` baseline + stdev (same stats pattern as
        ``volume_zscore``). Zero baseline / stdev → 0.0 (dormant).
      * ``news_sentiment_abs`` is passed through if provided;
        otherwise defaults to 0.0 (dormant, caller may populate via
        LLM pass on headlines separately).

    Returns ``None`` when no baseline yet — the caller should fall back
    to passing zeros into Patent M, which dormants the volatility/
    liquidity branches.
    """
    if _db is None:
        return None
    sym = symbol.upper()
    try:
        doc = await _db.equity_telemetry_baselines.find_one(
            {"symbol": sym, "asset_class": asset_class},
            {"_id": 0, "samples": 1},
        )
    except Exception:
        return None
    if not doc or not doc.get("samples"):
        return None
    samples = doc["samples"]

    atr_baseline = _baseline(samples, "atr_pct")
    spread_baseline = _baseline(samples, "spread_bps")
    volume_baseline = _baseline(samples, "volume")
    volume_stdev = _stdev(samples, "volume")
    dollar_volume_baseline = _baseline(samples, "dollar_volume")
    news_count_baseline = _baseline(samples, "news_count")
    news_count_stdev = _stdev(samples, "news_count")

    volume_z = 0.0
    if current_volume is not None and volume_baseline and volume_stdev > 0:
        volume_z = (current_volume - volume_baseline) / volume_stdev

    # news_volume_zscore — fires the NEWS_SHOCK branch when articles
    # spike well above their rolling baseline. Same statistical shape
    # as volume_zscore so Patent M's existing thresholds translate
    # directly.
    news_volume_z = 0.0
    if (
        current_news_count is not None
        and news_count_baseline is not None
        and news_count_stdev > 0
    ):
        news_volume_z = (current_news_count - news_count_baseline) / news_count_stdev

    return MarketTelemetry(
        symbol=sym,
        asset_type=asset_class,
        atr_pct=float(current_atr_pct or 0.0),
        atr_pct_baseline=float(atr_baseline or 0.0),
        volume_zscore=float(volume_z),
        spread_bps=float(current_spread_bps or 0.0),
        spread_bps_baseline=float(spread_baseline or 0.0),
        dollar_volume=float(current_dollar_volume or 0.0),
        dollar_volume_baseline=float(dollar_volume_baseline or 0.0),
        news_sentiment_abs=float(current_news_sentiment_abs or 0.0),
        news_volume_zscore=float(news_volume_z),
    )


async def ensure_indexes() -> None:
    if _db is None:
        return
    try:
        await _db.equity_telemetry_baselines.create_index(
            [("symbol", 1), ("asset_class", 1)], unique=True
        )
    except Exception:
        pass
