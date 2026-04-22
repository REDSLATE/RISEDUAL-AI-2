"""
Prediction Accuracy Tracker — Logs predictions, verifies outcomes, calculates hit rates.

Flow:
1. After each AI analysis, log the prediction (symbol, direction, price, timestamp)
2. Background task checks 24h and 1-week outcomes via Alpha Vantage
3. Auto-classifies failure mode when predictions are wrong
4. API returns rolling accuracy stats per feature (Pro only)
"""

import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import uuid4

from services.price_provider import get_quote_sync

logger = logging.getLogger(__name__)

# Direction classification constants
DIRECTION_BULLISH = {"BUY", "BULLISH", "LONG", "UP"}
DIRECTION_BEARISH = {"SELL", "BEARISH", "SHORT", "DOWN"}
DIRECTION_NEUTRAL = {"HOLD", "NEUTRAL", "WAIT"}

# ── Failure Mode Classification ──
FAILURE_MODES = {
    "TECH_FAKEOUT": "Indicators were bullish but price reversed immediately (Stop-loss hunt).",
    "MACRO_SHOCK": "Unexpected news/data (CPI, Fed, etc.) invalidated the setup.",
    "LIQUIDITY_GAP": "Low volume caused slippage or erratic price spikes.",
    "REGIME_SHIFT": "Market shifted from trending to range-bound unexpectedly.",
    "UNKNOWN": "Price moved against prediction without clear technical or news trigger.",
}


def _classify_failure(direction: str, price_at: float, price_now: float,
                      volume_ratio: float = None) -> str:
    """Auto-classify why a prediction failed based on price action heuristics.

    Returns one of: TECH_FAKEOUT, LIQUIDITY_GAP, REGIME_SHIFT, UNKNOWN.
    (MACRO_SHOCK requires external news data and is set manually or via AI.)
    """
    if price_at <= 0 or price_now <= 0:
        return "UNKNOWN"

    pct_change = abs((price_now - price_at) / price_at * 100)
    direction_upper = direction.upper()

    # Large, violent move (>=5%) — likely a macro shock or liquidity gap
    if pct_change >= 5.0:
        if volume_ratio is not None and volume_ratio < 0.5:
            return "LIQUIDITY_GAP"
        return "MACRO_SHOCK"

    # HOLD/NEUTRAL predicted stability but price moved significantly (>2%)
    if direction_upper in DIRECTION_NEUTRAL and pct_change > 2.0:
        return "MACRO_SHOCK"

    # Small move (<1%) but wrong direction — regime shift (range-bound market)
    if pct_change < 1.0:
        return "REGIME_SHIFT"

    # Moderate reversal (1-5%) — classic technical fakeout
    if direction_upper in DIRECTION_BULLISH and price_now < price_at:
        return "TECH_FAKEOUT"
    if direction_upper in DIRECTION_BEARISH and price_now > price_at:
        return "TECH_FAKEOUT"

    return "UNKNOWN"


def _get_current_price(symbol: str) -> Optional[float]:
    """Fetch current price using smart price provider (AV → yfinance → cache)."""
    quote = get_quote_sync(symbol)
    return quote["price"] if quote else None


# NEUTRAL tolerance fallbacks when we can't compute an ATR-based dynamic band
# (e.g. provider outage, new IPO with no history). NEUTRAL/HOLD means "no
# conviction either way" — the band has to be wide enough that normal
# volatility doesn't mark every flat call wrong. Dynamic bands are preferred
# and set per-symbol via _neutral_tolerance().
NEUTRAL_TOLERANCE_24H_DEFAULT = 2.0
NEUTRAL_TOLERANCE_1W_DEFAULT = 5.0
NEUTRAL_TOLERANCE_FLOOR = 2.0   # can't be tighter than 2% even for ultra-stable names
NEUTRAL_TOLERANCE_CEIL = 10.0   # can't exceed 10% even for meme stocks — that's a direction call

# Process-level ATR cache: { (symbol, window) -> (tolerance_pct, expires_utc_iso) }
# Cached for 12 hours. We recompute once the cache expires so the band adapts
# to volatility regime shifts (e.g. earnings week) without hitting daily bars
# on every verification.
_atr_cache: dict[tuple[str, str], tuple[float, str]] = {}


def _compute_atr_pct(bars: list[dict], period: int = 10) -> Optional[float]:
    """Return ATR as % of latest close. None if not enough data.

    Standard Wilder ATR on daily bars. `bars` must be ordered newest-first
    (matching `get_daily_history`).
    """
    if not bars or len(bars) < period + 1:
        return None
    try:
        ordered = list(reversed(bars[: period + 1]))  # oldest-first
        trs: list[float] = []
        prev_close = ordered[0]["close"]
        for b in ordered[1:]:
            tr = max(
                b["high"] - b["low"],
                abs(b["high"] - prev_close),
                abs(b["low"] - prev_close),
            )
            trs.append(tr)
            prev_close = b["close"]
        if not trs:
            return None
        atr = sum(trs) / len(trs)
        latest_close = ordered[-1]["close"]
        if latest_close <= 0:
            return None
        return (atr / latest_close) * 100.0
    except Exception:
        return None


async def _neutral_tolerance(symbol: str, window: str = "24h") -> float:
    """Dynamic NEUTRAL tolerance based on the symbol's realised volatility.

    Rule: tolerance = 1.5 × 10-day ATR% for 24h calls, 3.0 × ATR% for 1w
    calls (ATR is daily, so stretch it for weekly horizons). Clamped to
    [NEUTRAL_TOLERANCE_FLOOR, NEUTRAL_TOLERANCE_CEIL]. Falls back to
    static defaults on data failure.
    """
    from datetime import datetime, timezone, timedelta as _td
    key = (symbol.upper(), window)
    now_utc = datetime.now(timezone.utc)
    cached = _atr_cache.get(key)
    if cached:
        tol, expires = cached
        try:
            if datetime.fromisoformat(expires) > now_utc:
                return tol
        except ValueError:
            pass

    fallback = NEUTRAL_TOLERANCE_1W_DEFAULT if window == "1w" else NEUTRAL_TOLERANCE_24H_DEFAULT

    try:
        from services.price_provider import get_daily_history
        bars = await get_daily_history(symbol, outputsize="compact")
    except Exception:
        bars = None

    atr_pct = _compute_atr_pct(bars) if bars else None
    if atr_pct is None or atr_pct <= 0:
        tolerance = fallback
    else:
        multiplier = 3.0 if window == "1w" else 1.5
        tolerance = max(NEUTRAL_TOLERANCE_FLOOR, min(NEUTRAL_TOLERANCE_CEIL, atr_pct * multiplier))

    _atr_cache[key] = (tolerance, (now_utc + _td(hours=12)).isoformat())
    return tolerance


def _evaluate_prediction(direction: str, price_at_prediction: float,
                         price_now: float, window: str = "24h",
                         neutral_tolerance: Optional[float] = None) -> bool:
    """Determine if a prediction was correct (boolean contract preserved).

    Thin wrapper around `grade_prediction()` that collapses the 5-tier
    grade into the existing `correct: bool` contract for backwards
    compatibility with every caller that already stores
    `verified_24h.correct`. New callers should use `grade_prediction()`
    directly to get the full grade (STRONG_HIT, WEAK_HIT, NEUTRAL,
    WEAK_MISS, STRONG_MISS).

    Bool mapping — NEUTRAL is critical here: a prediction that lands
    inside the volatility tolerance band is NO LONGER counted as a miss.
    Previously `pct_change > 0` forced every flat/noise day on a BUY
    call to count as wrong, which was the "easy tickers keep getting
    flagged toxic" bug.
    """
    grade = grade_prediction(direction, price_at_prediction, price_now,
                             window=window, neutral_tolerance=neutral_tolerance)
    if grade in ("STRONG_HIT", "WEAK_HIT"):
        return True
    if grade in ("STRONG_MISS", "WEAK_MISS"):
        return False
    # NEUTRAL → explicitly NOT a hit, but ALSO not a miss. Callers that
    # only look at True/False lose the neutral state; they'll see False
    # here, but downstream verification writes `correct = None` for the
    # neutral grade path (see verify_pending_predictions).
    return False


# 5-tier prediction grade — superset of the legacy correct:bool signal.
# Used by new callers (conviction calibration, learning engine) for
# richer accuracy tracking. Order matters for UI sorting.
OUTCOME_GRADES = ("STRONG_HIT", "WEAK_HIT", "NEUTRAL", "WEAK_MISS", "STRONG_MISS")

# Tolerance config for the grading system. `ATR_MULTIPLIER` scales the
# ATR-derived tolerance; `MIN_TOLERANCE_PCT` is the absolute floor (as
# a percentage) so super-low-volatility names don't collapse to a
# zero-tolerance grader. The floor (0.25%) deliberately matches the
# user-supplied patch spec.
ATR_MULTIPLIER = 0.5
MIN_TOLERANCE_PCT = 0.25  # 0.25% floor — prevents zero-tolerance collapse


def grade_prediction(direction: str, price_at_prediction: float,
                     price_now: float, window: str = "24h",
                     neutral_tolerance: Optional[float] = None) -> str:
    """Return one of OUTCOME_GRADES based on volatility-aware tolerance.

    `neutral_tolerance` is supplied by the async `_neutral_tolerance()`
    helper when available (ATR-derived, per-symbol, windowed). Sync
    callers can pass None and we'll fall back to the 0.25% floor.

    Grade ladder relative to the tolerance band T:
        pct_change > +2T  → STRONG_HIT  (BUY) / STRONG_MISS (SELL)
        pct_change > +T   → WEAK_HIT    (BUY) / WEAK_MISS   (SELL)
        |pct_change| <= T → NEUTRAL
        pct_change < -T   → WEAK_MISS   (BUY) / WEAK_HIT    (SELL)
        pct_change < -2T  → STRONG_MISS (BUY) / STRONG_HIT  (SELL)

    For declared-NEUTRAL predictions: a TIGHT landing (|Δ| ≤ T) is
    STRONG_HIT; a modest move (T < |Δ| ≤ 2T) is WEAK_HIT; larger is
    a MISS scaled by magnitude.
    """
    if price_at_prediction <= 0 or price_now <= 0:
        return "STRONG_MISS"  # data hygiene fail → conservative miss

    pct_change = (price_now - price_at_prediction) / price_at_prediction * 100
    direction_upper = direction.upper()

    # Derive the tolerance band — we honor an explicit override first,
    # then the ATR-derived band (passed via `neutral_tolerance` from
    # the async caller), then the floor.
    if neutral_tolerance is not None and neutral_tolerance > 0:
        tol = max(float(neutral_tolerance), MIN_TOLERANCE_PCT)
    else:
        # Sync fallback: static window-sized tolerance × ATR_MULTIPLIER,
        # clamped to the floor.
        fallback = (NEUTRAL_TOLERANCE_1W_DEFAULT if window == "1w"
                    else NEUTRAL_TOLERANCE_24H_DEFAULT)
        tol = max(fallback * ATR_MULTIPLIER, MIN_TOLERANCE_PCT)

    if direction_upper in DIRECTION_BULLISH:
        if pct_change > 2 * tol:
            return "STRONG_HIT"
        if pct_change > tol:
            return "WEAK_HIT"
        if pct_change < -2 * tol:
            return "STRONG_MISS"
        if pct_change < -tol:
            return "WEAK_MISS"
        return "NEUTRAL"

    if direction_upper in DIRECTION_BEARISH:
        if pct_change < -2 * tol:
            return "STRONG_HIT"
        if pct_change < -tol:
            return "WEAK_HIT"
        if pct_change > 2 * tol:
            return "STRONG_MISS"
        if pct_change > tol:
            return "WEAK_MISS"
        return "NEUTRAL"

    if direction_upper in DIRECTION_NEUTRAL:
        # Declared NEUTRAL: the prediction is a bet on a tight band.
        # Tighter landing → stronger hit.
        abs_change = abs(pct_change)
        if abs_change <= tol:
            return "STRONG_HIT"
        if abs_change <= 2 * tol:
            return "WEAK_HIT"
        # Beyond 2× tolerance → the neutral call was wrong. Strong
        # vs weak depends on how far past 2T it went.
        if abs_change >= 4 * tol:
            return "STRONG_MISS"
        return "WEAK_MISS"

    return "STRONG_MISS"  # unknown direction → conservative


def normalize_confidence(confidence: float | int | None) -> float:
    """Canonicalise confidence to 0-100 scale.

    Mixed-scale writes were the second leg of the "toxic alerts 3x" bug
    — `memory_training_service` saved on 0-100 while
    `verify_pending_predictions` saved on 0-1, so the nightly_cleanup's
    `confidence > 80` query only ever matched one half of chroma.

    Heuristic: any value in (0, 1] is treated as a fraction and scaled
    up. Anything >1 is already on the 0-100 scale. `None` degrades to
    0 so downstream math doesn't crash.
    """
    if confidence is None:
        return 0.0
    c = float(confidence)
    if c <= 1.0:
        return round(c * 100, 2)
    return round(c, 2)


# Sliding cache / dedup windows make the prices embedded in predictions
# potentially stale compared to the live tape. Surface this on every API
# response that reports prediction-linked numbers so downstream UIs can
# render an honest caveat instead of implying tick-by-tick freshness.
PRICING_DISCLAIMER = (
    "Prices shown are anchored to the sliding cache and prediction dedup "
    "windows: quotes may be up to ~15 min stale, and predictions may be "
    "anchored to a price up to ~30 min old. Use for trend/accuracy "
    "evaluation, not for live execution pricing."
)
PRICING_FRESHNESS = {
    "quote_cache_ttl_seconds": 300,
    "quote_cache_max_lifetime_seconds": 900,    # 5 min × 3 touches
    "prediction_dedup_ttl_seconds": 900,
    "prediction_dedup_max_lifetime_seconds": 1800,  # 15 min × 2 touches
    "disclaimer": PRICING_DISCLAIMER,
}


# Maximum number of sliding-window extensions a prediction can accumulate
# before a repeat firing is treated as a new prediction instead of deduped
# onto the existing record. 1 → initial creation + 1 extension = ~30 min
# max lifetime under continuous polling. See `log_prediction()`.
MAX_DEDUP_HITS = 1


async def log_prediction(db, feature: str, symbol: str, direction: str,
                         confidence: float, score: float = None,
                         user_id: str = None,
                         model_version: str = None,
                         conviction: Optional[dict] = None) -> str:
    """Log a new prediction after AI analysis. Returns prediction_id.

    `model_version` lets us correlate prediction quality with a specific
    deployed ML artefact version (e.g. "signal_model.v0.1.0"). When a retrain
    regresses accuracy we can filter the post-mortem view to that version and
    quickly attribute failures. Optional until all callers are updated.

    Sliding 15-minute dedup window with a hard reset cap: if a prediction
    for the same (feature, symbol, direction, user_id) was logged or *last
    seen* within the last 15 minutes at essentially the same price (<0.2%
    drift), the existing prediction is reused — we return its prediction_id,
    bump `last_seen_at` to now, and increment `dedup_count`. The window
    "resets" on every repeat hit, so while the same signal keeps firing at
    least once every 15 min it stays as a single prediction record.

    Cap: `dedup_count` is capped at `MAX_DEDUP_HITS` (1). That gives each
    prediction a maximum effective lifetime of about 30 min (one initial
    firing + one extension). Any subsequent identical firing after the cap
    falls through and creates a fresh prediction record, which is then
    verified against the *current* price — catching price drift that a
    perpetually-sticky signal would otherwise hide.

    Important: the original `timestamp` and `price_at_prediction` are pinned
    to the first firing — the verification scheduler (24h / 1w outcome
    checks) runs against those anchors, not against the sliding last_seen.
    That way we never accidentally delay verification of a long-running
    signal just because it keeps repeating.
    """
    price = await asyncio.to_thread(_get_current_price, symbol)
    price = price or 0.0

    # ── Sliding 15-min dedup window (capped at MAX_DEDUP_HITS resets) ──
    now = datetime.now(timezone.utc)
    if price > 0:
        cutoff = (now - timedelta(minutes=15)).isoformat()
        existing = await db.predictions.find_one(
            {
                "feature": feature,
                "symbol": symbol.upper(),
                "direction": direction.upper(),
                "user_id": user_id,
                # Accept either fresh `timestamp` OR recently-refreshed
                # `last_seen_at` — records created before this field existed
                # will fall back to `timestamp`.
                "$or": [
                    {"last_seen_at": {"$gte": cutoff}},
                    {"timestamp": {"$gte": cutoff}},
                ],
                # Respect the reset cap — once a record has slid its window
                # MAX_DEDUP_HITS times it's no longer a dedup target; the
                # next identical firing creates a fresh prediction.
                "dedup_count": {"$lt": MAX_DEDUP_HITS},
            },
            {"_id": 0, "prediction_id": 1, "price_at_prediction": 1,
             "dedup_count": 1},
            sort=[("timestamp", -1)],
        )
        if existing:
            prev_price = existing.get("price_at_prediction", 0) or 0
            if prev_price > 0 and abs(price - prev_price) / prev_price < 0.002:
                # Refresh the sliding window on this record, don't insert new
                await db.predictions.update_one(
                    {"prediction_id": existing["prediction_id"]},
                    {
                        "$set": {"last_seen_at": now.isoformat()},
                        "$inc": {"dedup_count": 1},
                    },
                )
                logger.debug(
                    f"Dedup sliding hit: {existing['prediction_id']} "
                    f"dedup_count {existing.get('dedup_count',0)+1}/{MAX_DEDUP_HITS}"
                )
                return existing["prediction_id"]

    prediction_id = str(uuid4())[:12]
    doc = {
        "prediction_id": prediction_id,
        "feature": feature,
        "symbol": symbol.upper(),
        "direction": direction.upper(),
        "confidence": confidence,
        "score": score,
        "price_at_prediction": price,
        "timestamp": now.isoformat(),
        "last_seen_at": now.isoformat(),
        "dedup_count": 0,
        "user_id": user_id,
        "model_version": model_version or _current_model_version(),
        "verified_24h": None,
        "verified_1w": None,
    }
    # Auto-compute conviction if the caller didn't supply one. Every
    # prediction carries a score that the Conviction Calibration admin
    # panel can bucket — without requiring every call site to construct
    # the dict manually. The service fails safe (never raises) so a
    # computation error here can't block the insert.
    if conviction is None:
        try:
            from services.conviction_service import compute_conviction
            conviction = await compute_conviction(
                db,
                user_id=user_id,
                asset=symbol,
                direction=direction,
                confidence=confidence,
                regime_match=None,  # not available at prediction-log time
                risk_ctx=None,      # no risk-manager context here
            )
        except Exception as e:
            logger.warning(f"[prediction] conviction auto-compute failed: {e}")
            conviction = None
    if conviction is not None:
        doc["conviction"] = conviction
    await db.predictions.insert_one(doc)
    logger.info(f"Logged prediction: {feature}/{symbol} {direction} @ ${price}")
    return prediction_id


def _current_model_version() -> str:
    """Best-effort model version tag.

    Reads from env var `SIGNAL_MODEL_VERSION` set at deploy time. Falls back to
    `"unversioned"` so old data stays distinguishable from tracked data.
    """
    import os
    return os.environ.get("SIGNAL_MODEL_VERSION", "unversioned")


async def log_market_prediction(db, direction: str, confidence: float,
                                user_id: str = None, symbol: str = None) -> str:
    """Log a market-wide prediction (defaults to SPY as proxy, or specific ticker)."""
    target_symbol = symbol or "SPY"
    return await log_prediction(
        db, "market_prediction", target_symbol, direction, confidence, user_id=user_id
    )


async def verify_pending_predictions(db):
    """Check and verify predictions that have passed 24h or 1 week. Run as background task."""
    now = datetime.now(timezone.utc)
    cutoff_24h = (now - timedelta(hours=24)).isoformat()
    cutoff_1w = (now - timedelta(weeks=1)).isoformat()

    # Find predictions needing 24h verification
    pending_24h = db.predictions.find({
        "verified_24h": None,
        "timestamp": {"$lte": cutoff_24h},
        "price_at_prediction": {"$gt": 0},
    }, {"_id": 0}).limit(20)

    async for pred in pending_24h:
        price_now = await asyncio.to_thread(_get_current_price, pred["symbol"])
        if price_now is None:
            continue
        tolerance_24h = await _neutral_tolerance(pred["symbol"], window="24h")
        # New 5-tier grading: compute the rich label first, derive the
        # legacy boolean from it. NEUTRAL outcomes are preserved so
        # downstream learning/calibration skips them instead of
        # counting noise-day drift as misses (the "toxic alerts on
        # easy tickers" bug).
        grade = grade_prediction(
            pred["direction"], pred["price_at_prediction"], price_now,
            window="24h", neutral_tolerance=tolerance_24h,
        )
        correct = _evaluate_prediction(
            pred["direction"], pred["price_at_prediction"], price_now,
            window="24h", neutral_tolerance=tolerance_24h,
        )
        # For NEUTRAL grade we store `correct: None` so the calibration
        # queries (which filter on {"correct": {"$exists": true}}) can
        # optionally include or exclude — legacy dashboards keep
        # working because they read `correct` as True/False/None now
        # instead of strictly True/False.
        stored_correct = None if grade == "NEUTRAL" else bool(correct)

        # Classify failure mode only on real misses (WEAK_MISS or
        # STRONG_MISS). NEUTRAL and HITs get None so we don't pollute
        # the failure-mode histograms with noise-day "failures".
        failure_reason = "N/A"
        failure_code = None
        if grade in ("WEAK_MISS", "STRONG_MISS"):
            failure_code = _classify_failure(
                pred["direction"], pred["price_at_prediction"], price_now
            )
            failure_reason = FAILURE_MODES.get(failure_code, FAILURE_MODES["UNKNOWN"])

        await db.predictions.update_one(
            {"prediction_id": pred["prediction_id"]},
            {"$set": {"verified_24h": {
                "price": price_now,
                "correct": stored_correct,
                "grade": grade,
                "verified_at": now.isoformat(),
                "failure_code": failure_code,
                "failure_reason": failure_reason,
                "neutral_tolerance_used": round(tolerance_24h, 2),
            }}}
        )
        logger.info(
            f"Verified 24h: {pred['symbol']} {pred['direction']} → "
            f"{grade}"
        )

        # Push to SSE stream
        try:
            from routes.stream import push_event
            push_event("new_verification", {
                "ticker": pred["symbol"],
                "direction": pred["direction"],
                "confidence": pred.get("confidence", 0),
                "correct": stored_correct,
                "grade": grade,
                "failure_code": failure_code,
                "price_at": pred["price_at_prediction"],
                "price_now": price_now,
            })
        except Exception:
            pass

        # NEUTRAL-graded predictions are NOT resolved against the
        # learning engine — the trade is still "open" conceptually
        # (price within tolerance, outcome undecided). Let it expire
        # naturally or resolve on the next verification window. This
        # is the single line that prevents the "easy tickers flagged
        # toxic" regression from re-introducing itself in the LE.
        if grade == "NEUTRAL":
            continue

        # Resolve the pending LearningEngine record for this trade so
        # the fleet-wide stats tick over from `pending` → `win`/`loss`.
        # We match on `(asset, direction, user_id, status=pending)` and
        # mark the most recent open trade resolved. Missing match is
        # a silent no-op (prediction from a non-trade source is fine).
        try:
            from ai_core.learning_engine import _TRADES as _LE_TRADES, _STATS as _LE_STATS, _STATS_DOC_ID as _LE_DOC
            direction = (pred.get("direction") or "").upper()
            ai_dir = "LONG" if direction in ("BUY", "LONG", "BULLISH") else "SHORT"
            pnl = (price_now - pred["price_at_prediction"]) if ai_dir == "LONG" else (pred["price_at_prediction"] - price_now)
            # r_multiple: when we know a stop_loss was attached to the
            # trade record (ai_core stamps it on log_trade). Fall back
            # to a percentage-based denominator so analytics still work.
            pending = await db[_LE_TRADES].find_one(
                {"asset": pred["symbol"], "direction": ai_dir,
                 "user_id": pred.get("user_id"), "status": "pending"},
                sort=[("logged_at", 1)],  # oldest pending first — FIFO resolution
            )
            if pending is not None:
                entry = float(pending.get("entry") or pred["price_at_prediction"])
                # Denominator: prefer the stored stop_loss if present.
                risk = 0.0
                sl_stored = pending.get("stop_loss")
                if sl_stored:
                    risk = abs(entry - float(sl_stored))
                if risk <= 0:
                    # No SL on record — synthesise a 2% risk floor so
                    # r_multiple stays a usable magnitude rather than
                    # exploding on near-zero denominators.
                    risk = max(entry * 0.02, 0.01)
                r_mult = round(pnl / risk, 3)
                status_new = "win" if correct else "loss"
                await db[_LE_TRADES].update_one(
                    {"_id": pending["_id"]},
                    {"$set": {
                        "status": status_new,
                        "win": bool(correct),
                        "exit_price": round(price_now, 4),
                        "pnl": round(pnl, 4),
                        "r_multiple": r_mult,
                        "resolved_at": now.isoformat(),
                    }},
                )
                # Roll-up counters: decrement pending, increment resolved.
                await db[_LE_STATS].update_one(
                    {"_id": _LE_DOC},
                    {"$inc": {
                        "counts.pending": -1,
                        f"counts.{status_new}": 1,
                        "counts.total_resolved": 1,
                        "running.r_multiple_sum": r_mult,
                        "running.pnl_sum": round(pnl, 4),
                    }, "$set": {"last_update": now.isoformat()}},
                    upsert=True,
                )
        except Exception as e:
            logger.warning(f"[learning-engine] resolve failed for {pred['symbol']}: {e}")

        # Auto-save verified prediction to vector memory
        try:
            from services.market_memory_service import save_regime, _collection
            if _collection is not None:
                regime = {
                    "symbol": pred["symbol"],
                    "date": pred.get("timestamp", "")[:10],
                    "price": pred["price_at_prediction"],
                    "prediction": pred["direction"],
                    "confidence": pred.get("confidence", 0),
                    "actual_result": f"{'rose' if price_now > pred['price_at_prediction'] else 'fell'} to ${price_now:.2f}",
                    "outcome": "hit" if correct else "miss",
                    "failure_code": failure_code if not correct else None,
                    "failure_reason": failure_reason if not correct else None,
                }
                await save_regime(regime)
        except Exception as e:
            logger.warning(f"Memory save skipped for {pred['symbol']}: {e}")

        # Run AI post-mortem for wrong predictions (upgrades heuristic with news context)
        if not correct and failure_code:
            try:
                from services.post_mortem_service import run_and_update_post_mortem
                await run_and_update_post_mortem(db, pred, price_now, failure_code)
            except Exception as e:
                logger.warning(f"AI post-mortem skipped for {pred['symbol']}: {e}")

    # Find predictions needing 1-week verification
    pending_1w = db.predictions.find({
        "verified_1w": None,
        "verified_24h": {"$ne": None},
        "timestamp": {"$lte": cutoff_1w},
        "price_at_prediction": {"$gt": 0},
    }, {"_id": 0}).limit(20)

    async for pred in pending_1w:
        price_now = await asyncio.to_thread(_get_current_price, pred["symbol"])
        if price_now is None:
            continue
        tolerance_1w = await _neutral_tolerance(pred["symbol"], window="1w")
        correct = _evaluate_prediction(
            pred["direction"], pred["price_at_prediction"], price_now,
            window="1w", neutral_tolerance=tolerance_1w,
        )

        # Classify failure mode if prediction was wrong
        failure_reason = "N/A"
        failure_code = None
        if not correct:
            failure_code = _classify_failure(
                pred["direction"], pred["price_at_prediction"], price_now
            )
            failure_reason = FAILURE_MODES.get(failure_code, FAILURE_MODES["UNKNOWN"])

        await db.predictions.update_one(
            {"prediction_id": pred["prediction_id"]},
            {"$set": {"verified_1w": {
                "price": price_now,
                "correct": correct,
                "verified_at": now.isoformat(),
                "failure_code": failure_code,
                "failure_reason": failure_reason,
                "neutral_tolerance_used": round(tolerance_1w, 2),
            }}}
        )
        logger.info(
            f"Verified 1w: {pred['symbol']} {pred['direction']} — "
            f"{'CORRECT' if correct else f'WRONG ({failure_code})'}"
        )


async def reevaluate_neutral_predictions(db, window: str = "both") -> dict:
    """Retroactively re-score NEUTRAL/HOLD predictions under the current
    dynamic tolerance rule.

    Why: Older records were scored at the moment of verification using
    whatever static tolerance was in effect then (historically 2%). When we
    move to a per-symbol dynamic band, the existing `correct` flag is stale.
    This walks every stored NEUTRAL prediction and recomputes only the
    neutral-band decision (directional calls are unaffected — their
    correctness doesn't depend on tolerance).

    window: "24h", "1w", or "both".
    Returns dict of counts updated.
    """
    updated_24h = 0
    updated_1w = 0
    scanned = 0

    if window in ("24h", "both"):
        cursor = db.predictions.find({
            "direction": {"$in": list(DIRECTION_NEUTRAL)},
            "verified_24h": {"$ne": None},
            "price_at_prediction": {"$gt": 0},
        }, {"_id": 0})
        async for pred in cursor:
            scanned += 1
            p0 = pred["price_at_prediction"]
            p1 = (pred.get("verified_24h") or {}).get("price", 0)
            if p0 <= 0 or p1 <= 0:
                continue
            tol = await _neutral_tolerance(pred["symbol"], window="24h")
            new_correct = _evaluate_prediction(
                pred["direction"], p0, p1, window="24h", neutral_tolerance=tol,
            )
            prev_correct = pred["verified_24h"].get("correct")
            if new_correct != prev_correct:
                await db.predictions.update_one(
                    {"prediction_id": pred["prediction_id"]},
                    {"$set": {
                        "verified_24h.correct": new_correct,
                        "verified_24h.neutral_tolerance_used": round(tol, 2),
                        "verified_24h.rescored_at": datetime.now(timezone.utc).isoformat(),
                    }},
                )
                updated_24h += 1

    if window in ("1w", "both"):
        cursor = db.predictions.find({
            "direction": {"$in": list(DIRECTION_NEUTRAL)},
            "verified_1w": {"$ne": None},
            "price_at_prediction": {"$gt": 0},
        }, {"_id": 0})
        async for pred in cursor:
            scanned += 1
            p0 = pred["price_at_prediction"]
            p1 = (pred.get("verified_1w") or {}).get("price", 0)
            if p0 <= 0 or p1 <= 0:
                continue
            tol = await _neutral_tolerance(pred["symbol"], window="1w")
            new_correct = _evaluate_prediction(
                pred["direction"], p0, p1, window="1w", neutral_tolerance=tol,
            )
            prev_correct = pred["verified_1w"].get("correct")
            if new_correct != prev_correct:
                await db.predictions.update_one(
                    {"prediction_id": pred["prediction_id"]},
                    {"$set": {
                        "verified_1w.correct": new_correct,
                        "verified_1w.neutral_tolerance_used": round(tol, 2),
                        "verified_1w.rescored_at": datetime.now(timezone.utc).isoformat(),
                    }},
                )
                updated_1w += 1

    return {"scanned": scanned, "updated_24h": updated_24h, "updated_1w": updated_1w}


async def get_accuracy_stats(db, feature: Optional[str] = None,
                              window_days: int = 7) -> dict:
    """Calculate rolling accuracy stats. Optionally filter by feature.

    Args:
        db: Motor MongoDB handle.
        feature: Optional feature filter (war_room/hypothesis/market_prediction).
        window_days: Rolling window for the "1w" stat in days. Defaults to 7.
            Previously this function counted every verified_1w record ever made,
            which silently turned "accuracy_1w" into "accuracy_all_time" once
            the dataset grew past a week. Fixed 2026-02-18.

    Returns dict with:
        accuracy_24h, total_24h, correct_24h          — last 24h (no change)
        accuracy_1w, total_1w, correct_1w             — last `window_days`
        accuracy_1w_directional, total_1w_directional — excludes NEUTRAL/HOLD
        pending                                       — still-unverified 24h
    """
    from datetime import timedelta
    match = {}
    if feature:
        match["feature"] = feature

    # 24h accuracy — rolling last 24h only, not all-time
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    match_24h = {
        **match,
        "verified_24h": {"$ne": None},
        "timestamp": {"$gte": cutoff_24h},
    }
    total_24h = await db.predictions.count_documents(match_24h)
    correct_24h = await db.predictions.count_documents(
        {**match_24h, "verified_24h.correct": True}
    )

    # 1w accuracy — rolling last `window_days` days
    cutoff_1w = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    match_1w = {
        **match,
        "verified_1w": {"$ne": None},
        "timestamp": {"$gte": cutoff_1w},
    }
    total_1w = await db.predictions.count_documents(match_1w)
    correct_1w = await db.predictions.count_documents(
        {**match_1w, "verified_1w.correct": True}
    )

    # Directional-only 1w accuracy — excludes NEUTRAL/HOLD calls so the headline
    # isn't dragged down by the narrower-than-weekly-vol 2% neutral tolerance.
    # Pass the user this alongside the all-inclusive number.
    directional_filter = {
        **match_1w,
        "direction": {"$nin": list(DIRECTION_NEUTRAL)},
    }
    total_1w_dir = await db.predictions.count_documents(directional_filter)
    correct_1w_dir = await db.predictions.count_documents(
        {**directional_filter, "verified_1w.correct": True}
    )

    # Pending — any still-unverified 24h (no time filter; these accumulate
    # fast enough that a window here would be misleading).
    pending = await db.predictions.count_documents(
        {**match, "verified_24h": None}
    )

    return {
        "accuracy_24h": round((correct_24h / total_24h * 100), 1) if total_24h > 0 else None,
        "total_24h": total_24h,
        "correct_24h": correct_24h,
        "accuracy_1w": round((correct_1w / total_1w * 100), 1) if total_1w > 0 else None,
        "total_1w": total_1w,
        "correct_1w": correct_1w,
        "accuracy_1w_directional": round((correct_1w_dir / total_1w_dir * 100), 1) if total_1w_dir > 0 else None,
        "total_1w_directional": total_1w_dir,
        "correct_1w_directional": correct_1w_dir,
        "pending": pending,
        "window_days": window_days,
        "feature": feature or "all",
        "pricing_freshness": PRICING_FRESHNESS,
    }


async def get_all_feature_stats(db) -> dict:
    """Get accuracy stats for all features + overall."""
    features = ["war_room", "hypothesis", "market_prediction"]
    stats = {}
    for f in features:
        stats[f] = await get_accuracy_stats(db, f)
    stats["overall"] = await get_accuracy_stats(db)
    # Top-level disclaimer in addition to per-feature — clients that only
    # read the envelope still get the caveat without digging into nesting.
    stats["pricing_freshness"] = PRICING_FRESHNESS
    return stats


async def get_recent_predictions(db, feature: Optional[str] = None,
                                  limit: int = 20) -> list[dict]:
    """Get recent predictions with verification status.

    Note: caller should treat `price_at_prediction` as anchored to the
    sliding prediction-dedup window (up to ~30 min old at emission time).
    The route wrapper attaches `pricing_freshness` alongside this list.
    """
    match = {}
    if feature:
        match["feature"] = feature
    cursor = db.predictions.find(match, {"_id": 0}).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)
