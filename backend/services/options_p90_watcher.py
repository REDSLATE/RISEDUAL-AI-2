"""p90 spread-widening detector for the options universe.

Runs inline after each ``warm_options_universe`` cycle. Writes a thin
per-symbol history row to ``option_universe_p90_history`` (one row per
warm per symbol) and scans the last ~15 min for the pattern:

    p90 rising sharply while avg stays roughly flat

which indicates market makers are pulling quotes on a subset of strikes
— a real volatility-forward signal that surfaces 5–10 min before
standard vol-of-vol detectors react to realized moves.

Alert contract
--------------
* **Trigger**: last 3 warm cycles (≈15 min at the 5-min cadence) contain
  a p90 rise ≥ 50% while avg rose ≤ 20%.
* **Dedupe**: same-symbol alerts suppressed for 30 min so a multi-tick
  episode produces one notification, not six.
* **Persistence**: alerts land in ``option_universe_p90_alerts`` with
  full before/after metrics for audit.
* **Slack**: best-effort; silent no-op when ``SLACK_WEBHOOK_URL`` unset.

Follows the same "never-raises" pattern as other telemetry hooks —
detector failures must never break the warm cycle.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

P90_HISTORY_COLLECTION = "option_universe_p90_history"
P90_ALERTS_COLLECTION = "option_universe_p90_alerts"

# Detector thresholds (tuned against Phase 2a field observations where
# healthy intraday drift stays under ±15% for both stats).
MIN_HISTORY_POINTS = 3
P90_RISE_THRESHOLD = 0.50   # ≥ 50% increase triggers
AVG_DRIFT_CAP = 0.20        # … only when avg rose ≤ 20%
WINDOW_MINUTES = 20         # how far back to look for baseline
DEDUPE_MINUTES = 30


async def record_p90_history(db: Any, run_ts: datetime, universe: list[dict]) -> int:
    """Append one history row per symbol after each successful warm.

    Only records symbols that carry both ``avg_spread_bps`` and
    ``p90_spread_bps`` (skips under-covered symbols cleanly). Returns
    the number of rows written — useful for the warm telemetry log.
    """
    if not universe:
        return 0
    rows: list[dict] = []
    for entry in universe:
        agg = entry.get("aggregate") or {}
        avg_bps = agg.get("avg_spread_bps")
        p90_bps = agg.get("p90_spread_bps")
        if avg_bps is None or p90_bps is None:
            continue
        rows.append({
            "symbol": entry.get("symbol"),
            "ts": run_ts.isoformat(),
            "avg_spread_bps": float(avg_bps),
            "p90_spread_bps": float(p90_bps),
            "flow_imbalance": agg.get("flow_imbalance"),
            "has_hot_flow": bool(entry.get("has_hot_flow")),
            "total_volume": agg.get("total_volume"),
        })
    if not rows:
        return 0
    try:
        await db[P90_HISTORY_COLLECTION].insert_many(rows)
    except Exception as exc:
        logger.warning("[p90_watcher] history insert failed: %s", exc)
        return 0
    return len(rows)


async def _fetch_recent_history(
    db: Any, symbol: str, now: datetime, window_minutes: int = WINDOW_MINUTES,
) -> list[dict]:
    """Pull recent history rows for ``symbol``, newest-first."""
    cutoff = (now - timedelta(minutes=window_minutes)).isoformat()
    try:
        cursor = (
            db[P90_HISTORY_COLLECTION]
            .find(
                {"symbol": symbol, "ts": {"$gte": cutoff}},
                {"_id": 0},
            )
            .sort("ts", -1)
            .limit(10)
        )
        return await cursor.to_list(length=10)
    except Exception as exc:
        logger.debug("[p90_watcher] history read failed for %s: %s", symbol, exc)
        return []


def detect_p90_spike(history: list[dict]) -> dict | None:
    """Pure detector: return an alert payload when the p90-widens-while-
    avg-stable pattern is present, otherwise None.

    ``history`` is newest-first. Needs at least ``MIN_HISTORY_POINTS`` rows;
    below that the function is a deliberate no-op (not an alert suppression).
    """
    if len(history) < MIN_HISTORY_POINTS:
        return None
    latest = history[0]
    baseline = history[-1]  # oldest within the window
    try:
        p90_now = float(latest["p90_spread_bps"])
        p90_base = float(baseline["p90_spread_bps"])
        avg_now = float(latest["avg_spread_bps"])
        avg_base = float(baseline["avg_spread_bps"])
    except (KeyError, TypeError, ValueError):
        return None
    if p90_base <= 0 or avg_base <= 0:
        return None

    p90_change = (p90_now - p90_base) / p90_base
    avg_change = (avg_now - avg_base) / avg_base

    if p90_change < P90_RISE_THRESHOLD:
        return None
    # The differentiating signal: p90 moved but avg didn't. If both
    # spread stats climb together it's a general liquidity event, not
    # the tail-stress precursor this detector targets.
    if avg_change > AVG_DRIFT_CAP:
        return None

    return {
        "p90_change_pct": round(p90_change * 100, 1),
        "avg_change_pct": round(avg_change * 100, 1),
        "p90_now": round(p90_now, 2),
        "p90_baseline": round(p90_base, 2),
        "avg_now": round(avg_now, 2),
        "avg_baseline": round(avg_base, 2),
        "window_minutes": WINDOW_MINUTES,
        "points_considered": len(history),
    }


async def _recently_alerted(db: Any, symbol: str, now: datetime) -> bool:
    cutoff = (now - timedelta(minutes=DEDUPE_MINUTES)).isoformat()
    try:
        recent = await db[P90_ALERTS_COLLECTION].find_one(
            {"symbol": symbol, "fired_at": {"$gte": cutoff}},
            {"_id": 0, "fired_at": 1},
        )
    except Exception:
        return False
    return recent is not None


async def _notify_slack_p90_spike(symbol: str, detail: dict) -> None:
    """Best-effort Slack push. Silent no-op when webhook unset or disabled."""
    if (os.environ.get("P90_SPIKE_SLACK_ENABLED", "true").lower()
            not in ("1", "true", "yes")):
        return
    webhook_url = (os.environ.get("SLACK_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        return
    try:
        import httpx
        fields = [
            {"type": "mrkdwn", "text": f"*p90 Δ*\n`+{detail['p90_change_pct']}%`"},
            {"type": "mrkdwn", "text": f"*avg Δ*\n`{detail['avg_change_pct']:+.1f}%`"},
            {"type": "mrkdwn", "text": f"*p90 now*\n`{detail['p90_now']} bps`"},
            {"type": "mrkdwn", "text": f"*p90 base*\n`{detail['p90_baseline']} bps`"},
            {"type": "mrkdwn", "text": f"*avg now*\n`{detail['avg_now']} bps`"},
            {"type": "mrkdwn", "text": f"*window*\n`{detail['window_minutes']}m`"},
        ]
        payload = {
            "text": f"RISEDUAL · p90 spread widening — {symbol}",
            "blocks": [
                {"type": "header", "text": {
                    "type": "plain_text",
                    "text": f"RISEDUAL · p90 spread widening — {symbol}"[:150]}},
                {"type": "section", "text": {
                    "type": "mrkdwn",
                    "text": (f"*{symbol}* options p90 spread rose "
                             f"*+{detail['p90_change_pct']}%* while avg moved "
                             f"*{detail['avg_change_pct']:+.1f}%* — "
                             "tail-stress precursor pattern.")}},
                {"type": "section", "fields": fields},
                {"type": "context", "elements": [
                    {"type": "mrkdwn",
                     "text": "alert_type: `p90_spread_widening`"},
                ]},
            ],
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if not (200 <= resp.status_code < 300):
                logger.warning(
                    "[p90_watcher] slack webhook status=%d body=%s",
                    resp.status_code, resp.text[:200],
                )
    except Exception as exc:
        logger.warning("[p90_watcher] slack notify crashed: %s", exc)


async def scan_for_p90_spikes(db: Any, run_ts: datetime, universe: list[dict]) -> int:
    """Check every symbol in this warm's universe for the spike pattern,
    persist alerts, fire Slack notifications. Returns alert count."""
    alerts_fired = 0
    for entry in universe:
        symbol = entry.get("symbol")
        if not symbol:
            continue
        history = await _fetch_recent_history(db, symbol, run_ts)
        detail = detect_p90_spike(history)
        if detail is None:
            continue
        if await _recently_alerted(db, symbol, run_ts):
            continue
        alert_row = {
            "symbol": symbol,
            "fired_at": run_ts.isoformat(),
            "alert_type": "p90_spread_widening",
            "detail": detail,
        }
        try:
            await db[P90_ALERTS_COLLECTION].insert_one(alert_row.copy())
            alerts_fired += 1
        except Exception as exc:
            logger.warning(
                "[p90_watcher] alert persist failed for %s: %s", symbol, exc,
            )
            continue
        logger.info(
            "[p90_watcher] %s spike: p90 %+.1f%% while avg %+.1f%%",
            symbol, detail["p90_change_pct"], detail["avg_change_pct"],
        )
        await _notify_slack_p90_spike(symbol, detail)
    return alerts_fired


async def get_recent_alerts(db: Any, limit: int = 50) -> list[dict]:
    """Newest-first list for the admin endpoint."""
    limit = max(1, min(int(limit), 200))
    try:
        cursor = (
            db[P90_ALERTS_COLLECTION]
            .find({}, {"_id": 0})
            .sort("fired_at", -1)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)
    except Exception as exc:
        logger.debug("[p90_watcher] alerts read failed: %s", exc)
        return []
