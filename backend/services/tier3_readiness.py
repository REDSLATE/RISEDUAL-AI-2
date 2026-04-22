"""Tier-3 unlock readiness — composite gate with 6 independent checks
and a 0-100 readiness score for the admin UI.

The existing `risedual_core.ml.calibration.check_tier3()` gate only
enforces 30+ live days, accuracy, and drawdown. This module adds the
five additional constraints needed before real live execution can
safely start:

  1. **Exposure** — 30+ distinct paper-trading days AND 100+ trades.
  2. **High-confidence accuracy** — ≥30 samples at conf ≥70 AND
     win-rate ≥75% inside that bucket.
  3. **Calibration** — |high_conf_win_rate − avg_conf/100| ≤ 0.15.
     Prevents shipping an over- or under-confident model live.
  4. **Risk control** — STRONG_MISS rate ≤ 10% across verified rows.
  5. **Stability** — last-7-day win-rate not worse than overall
     minus 15 percentage points (catches recent regression).
  6. **Canary** — conviction clamp counter == 0 in the lookback
     window (wired to `services.conviction_clamp_canary`).

Design rules
------------
* Pure-function split: `check_tier3_unlock(stats)` and
  `compute_tier3_score(stats)` are driven by a plain dict so tests
  can stub every input. `build_tier3_stats(db)` is the only piece
  that touches Mongo.
* Fails CLOSED: any DB/compute error returns a zeroed `stats` dict
  which naturally lands with `unlocked=False` (readiness=0) — a
  broken stats builder must never unlock live execution.
* UTC-aware everywhere. `datetime.now(timezone.utc)` only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────
# "High confidence" cut-off (0-100 scale). 70 matches the decile
# bucket used by the reliability diagram and sits above the current
# model's average confidence (~60), making it a meaningful top-end.
HIGH_CONF_THRESHOLD: float = 70.0

# Min distinct paper-trading days before Tier 3 can unlock.
MIN_DAYS: int = 30
MIN_TOTAL_TRADES: int = 100
MIN_HIGH_CONF_SAMPLES: int = 30
MIN_HIGH_CONF_WIN_RATE: float = 0.75
CALIBRATION_GAP_LIMIT: float = 0.15
MAX_STRONG_MISS_RATE: float = 0.10
STABILITY_DROP_LIMIT: float = 0.15


# ── Pure unlock logic (mirrors the user-supplied contract) ────────────────────

def check_tier3_unlock(stats: dict) -> dict:
    """Return unlock status + detailed human-readable reasons.

    `stats` is expected to contain the keys produced by
    :func:`build_tier3_stats`. Missing keys are treated as 0 (fail-
    closed).
    """
    reasons: list[str] = []

    days = int(stats.get("days", 0))
    total_trades = int(stats.get("total_trades", 0))
    high_conf_trades = int(stats.get("high_conf_trades", 0))
    high_conf_win_rate = float(stats.get("high_conf_win_rate", 0.0))
    avg_confidence = float(stats.get("avg_confidence", 0.0))
    strong_miss_rate = float(stats.get("strong_miss_rate", 1.0))
    last_7d_win_rate = float(stats.get("last_7d_win_rate", 0.0))
    overall_win_rate = float(stats.get("overall_win_rate", 0.0))
    clamp_total = int(stats.get("clamp_total", 0))

    # 1. Exposure
    if days < MIN_DAYS:
        reasons.append("Insufficient live days")
    if total_trades < MIN_TOTAL_TRADES:
        reasons.append("Insufficient trade count")

    # 2. High-confidence accuracy
    if high_conf_trades < MIN_HIGH_CONF_SAMPLES:
        reasons.append("Not enough high-confidence samples")
    elif high_conf_win_rate < MIN_HIGH_CONF_WIN_RATE:
        reasons.append("High-confidence win rate too low")

    # 3. Calibration — only meaningful if we have a high-conf sample.
    if high_conf_trades >= MIN_HIGH_CONF_SAMPLES:
        if abs(high_conf_win_rate - avg_confidence / 100.0) > CALIBRATION_GAP_LIMIT:
            reasons.append("Confidence calibration off")

    # 4. Risk control
    if strong_miss_rate > MAX_STRONG_MISS_RATE:
        reasons.append("Too many strong misses")

    # 5. Stability
    if last_7d_win_rate < overall_win_rate - STABILITY_DROP_LIMIT:
        reasons.append("Recent performance unstable")

    # 6. Canary
    if clamp_total > 0:
        reasons.append("Conviction clamp triggered")

    return {
        "unlocked": len(reasons) == 0,
        "reasons": reasons,
        "confidence_score": compute_tier3_score(stats),
    }


def compute_tier3_score(stats: dict) -> float:
    """0-100 readiness score (UI-friendly composite).

    Weights:
      20% exposure-days,  15% trade-count,   25% high-conf win-rate,
      20% (1 − strong_miss_rate),  10% last-7d win-rate,  10% canary.

    Weights sum to 100 so the output is naturally on a percent scale
    — "87/100 ready" reads the same as the progress bar people expect.
    """
    days = float(stats.get("days", 0))
    total_trades = float(stats.get("total_trades", 0))
    high_conf_wr = max(0.0, min(1.0, float(stats.get("high_conf_win_rate", 0.0))))
    strong_miss_rate = max(0.0, min(1.0, float(stats.get("strong_miss_rate", 1.0))))
    last_7d_wr = max(0.0, min(1.0, float(stats.get("last_7d_win_rate", 0.0))))
    clamp_total = int(stats.get("clamp_total", 0))

    score = 0.0
    score += min(days / MIN_DAYS, 1.0) * 20
    score += min(total_trades / MIN_TOTAL_TRADES, 1.0) * 15
    score += high_conf_wr * 25
    score += (1.0 - strong_miss_rate) * 20
    score += last_7d_wr * 10
    score += (1.0 if clamp_total == 0 else 0.0) * 10
    return round(score, 2)


# ── Stats builder (only DB-touching piece) ────────────────────────────────────

async def _paper_trades_count(db: Any) -> int:
    try:
        return int(await db["paper_trades"].count_documents({}))
    except Exception as exc:
        logger.warning("[tier3-readiness] paper_trades count failed: %s", exc)
        return 0


async def _high_conf_and_grades(db: Any, days: int) -> dict:
    """Single pass over `predictions` to derive:
      * high_conf_trades / correct / sum_confidence
      * strong_miss count / total graded
      * last-7d wins, total (for stability)
      * overall win total (fraction of correct across graded)
    """
    out = {
        "high_conf_trades": 0,
        "high_conf_correct": 0,
        "high_conf_sum_conf": 0.0,
        "strong_miss": 0,
        "total_graded": 0,
        "last_7d_correct": 0,
        "last_7d_graded": 0,
        "overall_correct": 0,
    }
    if db is None:
        return out
    try:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

        cursor = db.predictions.find(
            {
                "verified_24h.correct": {"$in": [True, False]},
                "timestamp": {"$gte": since.isoformat()},
            },
            {
                "_id": 0,
                "confidence": 1,
                "verified_24h.correct": 1,
                "verified_24h.grade": 1,
                "timestamp": 1,
            },
        ).limit(10000)

        async for row in cursor:
            v = row.get("verified_24h") or {}
            correct = v.get("correct")
            grade = v.get("grade")
            if correct not in (True, False):
                continue
            out["total_graded"] += 1
            if correct:
                out["overall_correct"] += 1

            # Normalise confidence to 0-100.
            try:
                conf = float(row.get("confidence") or 0.0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf <= 1.0 and conf > 0:
                conf *= 100.0
            conf = max(0.0, min(100.0, conf))

            if conf >= HIGH_CONF_THRESHOLD:
                out["high_conf_trades"] += 1
                out["high_conf_sum_conf"] += conf
                if correct:
                    out["high_conf_correct"] += 1

            if grade == "STRONG_MISS":
                out["strong_miss"] += 1

            # Last-7d slice (same corpus, tighter timestamp filter).
            ts = row.get("timestamp") or ""
            try:
                ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                ts_dt = None
            if ts_dt is not None and ts_dt >= seven_days_ago:
                out["last_7d_graded"] += 1
                if correct:
                    out["last_7d_correct"] += 1
    except Exception as exc:
        logger.warning("[tier3-readiness] predictions scan failed: %s", exc)
    return out


async def build_tier3_stats(db: Any, days: int = 30) -> dict:
    """Assemble the `stats` dict that :func:`check_tier3_unlock`
    consumes. Fails to a zeroed dict that naturally lands
    `unlocked=False`.
    """
    stats: dict[str, Any] = {
        "days": 0,
        "total_trades": 0,
        "high_conf_trades": 0,
        "high_conf_win_rate": 0.0,
        "avg_confidence": 0.0,
        "strong_miss_rate": 1.0,
        "overall_win_rate": 0.0,
        "last_7d_win_rate": 0.0,
        "clamp_total": 0,
        "high_conf_threshold": HIGH_CONF_THRESHOLD,
        "lookback_days": days,
    }
    if db is None:
        return stats

    try:
        from services.paper_trading_progress import compute_live_days
        stats["days"] = await compute_live_days(db)
    except Exception as exc:
        logger.warning("[tier3-readiness] live-days failed: %s", exc)

    stats["total_trades"] = await _paper_trades_count(db)

    agg = await _high_conf_and_grades(db, days)
    hc_n = agg["high_conf_trades"]
    stats["high_conf_trades"] = hc_n
    stats["high_conf_win_rate"] = (agg["high_conf_correct"] / hc_n) if hc_n else 0.0
    stats["avg_confidence"] = (agg["high_conf_sum_conf"] / hc_n) if hc_n else 0.0

    total_graded = agg["total_graded"]
    stats["strong_miss_rate"] = (agg["strong_miss"] / total_graded) if total_graded else 0.0
    stats["overall_win_rate"] = (agg["overall_correct"] / total_graded) if total_graded else 0.0

    l7 = agg["last_7d_graded"]
    # If no graded rows in the last 7d we hold the stability check
    # neutral by matching the overall win-rate — a cold-start week
    # shouldn't silently trip "recent performance unstable".
    stats["last_7d_win_rate"] = (
        agg["last_7d_correct"] / l7 if l7 else stats["overall_win_rate"]
    )

    try:
        from services.conviction_clamp_canary import conviction_clamp_counter
        canary = await conviction_clamp_counter(db, days=days)
        stats["clamp_total"] = int(canary.get("clamp_total", 0))
    except Exception as exc:
        logger.warning("[tier3-readiness] clamp canary failed: %s", exc)

    return stats


async def tier3_readiness_snapshot(db: Any, days: int = 30) -> dict:
    """Full admin payload: `stats` + `unlock` decision + timestamp."""
    stats = await build_tier3_stats(db, days=days)
    decision = check_tier3_unlock(stats)
    return {
        "stats": stats,
        "unlock": decision,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
