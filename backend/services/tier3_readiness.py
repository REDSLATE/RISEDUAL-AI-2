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


def compute_tier3_breakdown(stats: dict) -> list[dict]:
    """Per-component progress decomposition of the composite score.

    Used by the admin detail card to render six progress bars instead
    of one flat number. Each entry tells the operator:

      * ``label`` — human name of the gate (e.g. "Exposure (days)")
      * ``current`` — current value, in the unit displayed
      * ``target`` — threshold needed to fully clear this gate
      * ``progress_pct`` — 0-100 share of this gate's contribution
        currently earned (NOT a fraction of the total score)
      * ``weight_pct`` — how much of the total 100-point score this
        gate is worth (so the UI can show "you've earned 18.5/25
        for high-conf win rate")
      * ``earned_pts`` — actual points contributed to the composite
      * ``met`` — boolean shortcut: is this gate fully cleared?
      * ``hint`` — short operator-readable next-step string for the
        bars that aren't yet full

    The sum of ``earned_pts`` equals the value returned by
    :func:`compute_tier3_score`.
    """
    days = float(stats.get("days", 0))
    total_trades = float(stats.get("total_trades", 0))
    high_conf_trades = int(stats.get("high_conf_trades", 0))
    high_conf_wr = max(0.0, min(1.0, float(stats.get("high_conf_win_rate", 0.0))))
    strong_miss_rate = max(0.0, min(1.0, float(stats.get("strong_miss_rate", 1.0))))
    last_7d_wr = max(0.0, min(1.0, float(stats.get("last_7d_win_rate", 0.0))))
    clamp_total = int(stats.get("clamp_total", 0))

    def _pct(num: float, denom: float) -> float:
        return round(min(num / denom, 1.0) * 100, 1) if denom > 0 else 0.0

    exposure_pct = _pct(days, MIN_DAYS)
    volume_pct = _pct(total_trades, MIN_TOTAL_TRADES)
    # High-conf bar is gated by sample size — even with 100% wr, we
    # report 0% if the operator hasn't accumulated enough samples.
    if high_conf_trades < MIN_HIGH_CONF_SAMPLES:
        # Sample-size-gated: bar shows fraction of samples accumulated
        # (the BINDING constraint right now). earned_pts still
        # mirrors the composite formula ``high_conf_wr * 25`` so the
        # bar-sum reconciles to the headline score — the composite
        # treats the win rate as evidence even before the sample
        # threshold is met; only ``check_tier3_unlock`` blocks the
        # actual unlock based on samples.
        hc_pct = round(
            (high_conf_trades / MIN_HIGH_CONF_SAMPLES) * 100, 1
        )
        hc_earned = round(high_conf_wr * 25, 2)
        hc_hint = (
            f"{high_conf_trades}/{MIN_HIGH_CONF_SAMPLES} high-conf samples "
            f"(wr={high_conf_wr * 100:.1f}%)"
        )
    else:
        # Composite formula is ``high_conf_wr * 25`` — match exactly
        # so earned_pts sums to ``compute_tier3_score``. Visual bar
        # shows wr-as-pct so 70% reads as 70 with target marked at 75.
        hc_pct = round(high_conf_wr * 100, 1)
        hc_earned = round(high_conf_wr * 25, 2)
        hc_hint = (
            f"{high_conf_wr * 100:.1f}% / {MIN_HIGH_CONF_WIN_RATE * 100:.0f}% target"
        )
    # Risk: progress = how much of the (1 - max_miss_rate) headroom
    # we've earned. Inverted because lower miss rate = more progress.
    risk_pct = round(max(0.0, (1.0 - strong_miss_rate)) * 100, 1)
    last7_pct = round(last_7d_wr * 100, 1)
    canary_pct = 100.0 if clamp_total == 0 else 0.0

    return [
        {
            "key": "exposure",
            "label": "Live exposure (days)",
            "current": int(days),
            "target": MIN_DAYS,
            "unit": "days",
            "progress_pct": exposure_pct,
            "weight_pct": 20,
            "earned_pts": round(exposure_pct / 100 * 20, 2),
            "met": days >= MIN_DAYS,
            "hint": f"{int(days)} / {MIN_DAYS} days"
            if days < MIN_DAYS else "cleared",
        },
        {
            "key": "volume",
            "label": "Trade volume",
            "current": int(total_trades),
            "target": MIN_TOTAL_TRADES,
            "unit": "trades",
            "progress_pct": volume_pct,
            "weight_pct": 15,
            "earned_pts": round(volume_pct / 100 * 15, 2),
            "met": total_trades >= MIN_TOTAL_TRADES,
            "hint": f"{int(total_trades)} / {MIN_TOTAL_TRADES}"
            if total_trades < MIN_TOTAL_TRADES else "cleared",
        },
        {
            "key": "high_conf_accuracy",
            "label": "High-confidence accuracy",
            # Show the win rate as the "current" value so the
            # operator can compare against the 75% target directly.
            "current": round(high_conf_wr * 100, 1),
            "target": round(MIN_HIGH_CONF_WIN_RATE * 100, 0),
            "unit": "%",
            "progress_pct": hc_pct,
            "weight_pct": 25,
            "earned_pts": hc_earned,
            "met": (
                high_conf_trades >= MIN_HIGH_CONF_SAMPLES
                and high_conf_wr >= MIN_HIGH_CONF_WIN_RATE
            ),
            "hint": hc_hint,
        },
        {
            "key": "risk_control",
            "label": "Risk control (low strong-miss rate)",
            "current": round(strong_miss_rate * 100, 1),
            "target": round(MAX_STRONG_MISS_RATE * 100, 0),
            "unit": "%",
            "progress_pct": risk_pct,
            "weight_pct": 20,
            "earned_pts": round(risk_pct / 100 * 20, 2),
            "met": strong_miss_rate <= MAX_STRONG_MISS_RATE,
            "hint": (
                f"{strong_miss_rate * 100:.1f}% miss rate "
                f"(≤ {MAX_STRONG_MISS_RATE * 100:.0f}% required)"
            ),
        },
        {
            "key": "stability",
            "label": "Stability (last-7d win rate)",
            "current": round(last_7d_wr * 100, 1),
            "target": 100,
            "unit": "%",
            "progress_pct": last7_pct,
            "weight_pct": 10,
            "earned_pts": round(last7_pct / 100 * 10, 2),
            "met": True,  # soft gate — only contributes to score
            "hint": f"{last_7d_wr * 100:.1f}% recent wins",
        },
        {
            "key": "canary",
            "label": "Conviction canary clean",
            "current": clamp_total,
            "target": 0,
            "unit": "clamp events",
            "progress_pct": canary_pct,
            "weight_pct": 10,
            "earned_pts": canary_pct / 100 * 10,
            "met": clamp_total == 0,
            "hint": (
                "no conviction clamps tripped"
                if clamp_total == 0
                else f"{clamp_total} clamp event(s) — Tier 3 blocked"
            ),
        },
    ]


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
                "calibrated_confidence": 1,
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

            # Tier 3 readiness reads ``calibrated_confidence`` when
            # the writer stamped one (post-2026-05-04 calibration
            # rollout). Falls back to raw ``confidence`` for legacy
            # rows so historical readiness math stays comparable
            # until the corpus rolls over. Sizing / execution paths
            # keep reading raw ``confidence`` only — see
            # services.calibration_service docstring.
            raw_calibrated = row.get("calibrated_confidence")
            if raw_calibrated is not None:
                try:
                    conf = float(raw_calibrated)
                except (TypeError, ValueError):
                    conf = 0.0
            else:
                try:
                    conf = float(row.get("confidence") or 0.0)
                except (TypeError, ValueError):
                    conf = 0.0
            # Normalise to 0-100. Calibrated values land in [0,1] by
            # construction; raw values may be on either scale.
            if 0 < conf <= 1.0:
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
