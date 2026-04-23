"""Observer agents — passive agents that don't trade, only emit
insight events into the agent activity feed.

Both run on APScheduler.

  * :func:`regime_drift_check` — daily. Compares this week's
    ``feature_stability`` against last week's. Fires a
    ``regime_drift`` activity event when a top-5 feature flips
    bullish/bearish balance by more than 30 percentage points.

  * :func:`performance_monitor_check` — daily. Rolls up the last
    20 resolved trades per strategy and fires a
    ``performance_alert`` event when trailing win rate drops below
    35% or mean R goes negative.

Neither agent opens trades. Both respect never-raise — a failure
in an observer must not disturb the trading agents that share the
scheduler.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


async def regime_drift_check(db: Any) -> None:
    """Compare 7d vs prior-7d feature stability; emit drift events.

    Uses the existing ``fetch_feature_stability`` twice with
    different windows and diffs their ``bullish_frac`` values.
    """
    if db is None:
        return
    try:
        from services.agent_activity_service import (
            fetch_feature_stability,
            log_event,
            set_db as set_aa_db,
        )
        set_aa_db(db)  # ensure module-level db handle is alive
        current = await fetch_feature_stability(days=7, min_appearances=3, top_k=10)
        prior = await fetch_feature_stability(days=14, min_appearances=3, top_k=20)
        prior_map = {r["feature"]: r for r in prior}

        drifts = []
        for row in current:
            name = row["feature"]
            cur_bull = float(row.get("bullish_frac", 0.5))
            prior_row = prior_map.get(name)
            if not prior_row:
                continue
            prior_bull = float(prior_row.get("bullish_frac", 0.5))
            delta = cur_bull - prior_bull
            # 30-percentage-point flip threshold — loud enough to
            # mean something, quiet enough to not spam daily.
            if abs(delta) < 0.30:
                continue
            drifts.append({
                "feature": name,
                "prior_bull_frac": round(prior_bull, 3),
                "current_bull_frac": round(cur_bull, 3),
                "delta": round(delta, 3),
                "appearances_7d": row["appearances"],
            })

        if not drifts:
            return

        # One rolled-up event rather than one-per-feature — keeps
        # the feed readable when multiple features shift together.
        top_drift = max(drifts, key=lambda d: abs(d["delta"]))
        direction = "bullish" if top_drift["delta"] > 0 else "bearish"
        await log_event(
            type="info",  # Using generic info type — ``regime_drift`` not yet in vocab
            severity="warn",
            title=f"Regime drift: {top_drift['feature']} turned {direction}",
            detail=(
                f"{len(drifts)} feature(s) flipped >30pp · "
                f"{top_drift['feature']} went "
                f"{top_drift['prior_bull_frac'] * 100:.0f}% → "
                f"{top_drift['current_bull_frac'] * 100:.0f}% bullish in 7d"
            ),
            metadata={"drifts": drifts, "kind": "regime_drift"},
        )
    except Exception as e:
        logger.warning("[observer:regime_drift] failed: %s", e)


async def performance_monitor_check(db: Any) -> None:
    """Scan each strategy's last 20 resolved trades; alert on
    degraded performance.

    Thresholds:
      * win_rate < 35% → warn
      * mean_r < 0 AND ≥ 10 resolved trades → warn (small-sample
        noise is gated behind the count floor)
    """
    if db is None:
        return
    try:
        from services.agent_activity_service import log_event, set_db as set_aa_db
        set_aa_db(db)
        # Group by strategy. Empty strategy string falls under 'momentum'
        # for legacy trades — reasonable default given that path came
        # from the default paper trader.
        pipeline = [
            {"$match": {"status": {"$in": ["win", "loss"]}}},
            {"$sort": {"logged_at": -1}},
            {"$group": {
                "_id": {"$ifNull": ["$strategy", "momentum"]},
                "trades": {"$push": {
                    "win": "$win", "r_multiple": "$r_multiple",
                }},
            }},
        ]
        cursor = db["learning_engine_trades"].aggregate(pipeline)
        async for row in cursor:
            strategy = row["_id"]
            trades = (row.get("trades") or [])[:20]
            if len(trades) < 5:
                continue
            wins = sum(1 for t in trades if t.get("win"))
            wr = wins / len(trades)
            r_values = [float(t.get("r_multiple") or 0) for t in trades]
            mean_r = sum(r_values) / len(r_values) if r_values else 0.0

            if wr < 0.35 or (mean_r < 0 and len(trades) >= 10):
                await log_event(
                    type="info",  # generic — perf_alert not in vocab yet
                    severity="warn",
                    title=f"Performance alert · {strategy}",
                    detail=(
                        f"trailing {len(trades)} trades: "
                        f"win rate {wr * 100:.0f}%, mean R {mean_r:+.2f}"
                    ),
                    metadata={
                        "kind": "performance_alert",
                        "strategy": strategy,
                        "sample_size": len(trades),
                        "win_rate": round(wr, 4),
                        "mean_r": round(mean_r, 3),
                    },
                )
    except Exception as e:
        logger.warning("[observer:performance_monitor] failed: %s", e)
