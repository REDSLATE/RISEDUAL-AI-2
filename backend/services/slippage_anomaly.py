"""Broker slippage anomaly detector.

Reads the SQLite ``broker_comparison`` table and compares each broker's
recent-window slippage (default: last hour) against its own historical
baseline (default: prior 14 days). Emits an anomaly when the recent
p50 slippage crosses ``multiplier × historical p50``.

Detection contract
------------------
* Per-broker baseline (Public.com vs MooMoo — never cross-compare).
* Statistically honest: we require at least ``MIN_RECENT_SAMPLES``
  fills in the recent window AND ``MIN_BASELINE_SAMPLES`` fills in
  the historical window. Below those floors we return "insufficient
  data" instead of a false alarm.
* Result is intentionally cheap to compute on-demand — the endpoint
  can be called on every UI refresh without a background worker.

Storage
-------
Alerts are only stored when the caller explicitly asks (``record=True``),
so the endpoint is a pure-read by default. Recorded alerts live in a
compact Mongo collection ``broker_slippage_alerts`` — not the SQLite
hot store, because operator-facing rollups belong in Mongo.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from statistics import median
from typing import Any, Optional

from services import alpha_hot_store

logger = logging.getLogger(__name__)

# Trigger threshold — recent p50 must be at least MULTIPLIER × baseline p50.
DEFAULT_MULTIPLIER = 2.0
DEFAULT_RECENT_WINDOW_SEC = 3_600           # 1 hour
DEFAULT_BASELINE_WINDOW_SEC = 14 * 86_400    # 14 days
MIN_RECENT_SAMPLES = 5
MIN_BASELINE_SAMPLES = 20
_KNOWN_BROKERS = ("public", "moomoo")


def _percentile(values: list[float], q: float) -> Optional[float]:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return float(xs[lo] * (1.0 - frac) + xs[hi] * frac)


def _slippage_rows(broker: str, since_ns: int, until_ns: int) -> list[float]:
    """Return signed slippage_bps values for a broker in [since, until]."""
    try:
        alpha_hot_store.init()
        path = alpha_hot_store._path()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return []
    try:
        with sqlite3.connect(path, timeout=5.0) as con:
            try:
                rows = con.execute(
                    "SELECT slippage_bps FROM broker_comparison "
                    "WHERE broker=? AND ts_ns>=? AND ts_ns<? "
                    "AND slippage_bps IS NOT NULL",
                    (broker, int(since_ns), int(until_ns)),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [float(r[0]) for r in rows if r[0] is not None]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[slippage_alerts] read failed: %s", exc)
        return []


def evaluate_broker(
    broker: str,
    *,
    now_ns: Optional[int] = None,
    multiplier: float = DEFAULT_MULTIPLIER,
    recent_window_sec: int = DEFAULT_RECENT_WINDOW_SEC,
    baseline_window_sec: int = DEFAULT_BASELINE_WINDOW_SEC,
) -> dict:
    """Evaluate one broker's rolling slippage against its baseline."""
    ts = int(now_ns) if now_ns is not None else time.time_ns()
    recent_since = ts - int(recent_window_sec * 1e9)
    baseline_since = ts - int(baseline_window_sec * 1e9)
    recent_vals = _slippage_rows(broker, recent_since, ts)
    baseline_vals = _slippage_rows(broker, baseline_since, recent_since)

    recent_p50 = _percentile(recent_vals, 0.50)
    baseline_p50 = _percentile(baseline_vals, 0.50)
    threshold = None
    if baseline_p50 is not None and baseline_p50 > 0:
        threshold = round(baseline_p50 * float(multiplier), 3)

    reason = None
    triggered = False
    if len(recent_vals) < MIN_RECENT_SAMPLES:
        reason = f"insufficient_recent_samples({len(recent_vals)})"
    elif len(baseline_vals) < MIN_BASELINE_SAMPLES:
        reason = f"insufficient_baseline_samples({len(baseline_vals)})"
    elif baseline_p50 is None or baseline_p50 <= 0:
        # Baseline shows negative or zero p50 (execution has always been
        # in our favour). Anomaly detection isn't meaningful here.
        reason = "baseline_non_positive"
    elif recent_p50 is None:
        reason = "no_recent_slippage"
    elif recent_p50 >= (threshold or 0):
        triggered = True
        reason = "recent_p50_over_threshold"
    else:
        reason = "within_normal_range"

    return {
        "broker": broker,
        "recent_samples": len(recent_vals),
        "baseline_samples": len(baseline_vals),
        "recent_p50_bps": round(recent_p50, 3) if recent_p50 is not None else None,
        "baseline_p50_bps": round(baseline_p50, 3) if baseline_p50 is not None else None,
        "threshold_bps": threshold,
        "multiplier": float(multiplier),
        "recent_window_sec": int(recent_window_sec),
        "baseline_window_sec": int(baseline_window_sec),
        "triggered": bool(triggered),
        "reason": reason,
        "evaluated_at": datetime.fromtimestamp(ts / 1e9, tz=timezone.utc).isoformat(),
    }


async def evaluate_all(
    db: Any,
    *,
    multiplier: float = DEFAULT_MULTIPLIER,
    recent_window_sec: int = DEFAULT_RECENT_WINDOW_SEC,
    baseline_window_sec: int = DEFAULT_BASELINE_WINDOW_SEC,
    record: bool = False,
) -> dict:
    """Evaluate every known broker. When ``record=True``, persist any
    triggered alert to Mongo (rate-limited to one alert per broker per
    ``recent_window_sec``)."""
    now_ns = time.time_ns()
    results = [
        evaluate_broker(
            b, now_ns=now_ns, multiplier=multiplier,
            recent_window_sec=recent_window_sec,
            baseline_window_sec=baseline_window_sec,
        )
        for b in _KNOWN_BROKERS
    ]
    if record and db is not None:
        for r in results:
            if not r["triggered"]:
                continue
            try:
                cutoff = datetime.fromtimestamp(
                    (now_ns - int(recent_window_sec * 1e9)) / 1e9, tz=timezone.utc,
                ).isoformat()
                already = await db.broker_slippage_alerts.find_one(
                    {"broker": r["broker"], "evaluated_at": {"$gte": cutoff}},
                    {"_id": 1},
                )
                if already is None:
                    await db.broker_slippage_alerts.insert_one({**r})
            except Exception as exc:  # noqa: BLE001
                logger.debug("[slippage_alerts] record failed: %s", exc)
    return {
        "evaluated_at": datetime.fromtimestamp(now_ns / 1e9, tz=timezone.utc).isoformat(),
        "multiplier": float(multiplier),
        "brokers": results,
        "any_triggered": any(r["triggered"] for r in results),
    }


async def recent_alerts(db: Any, *, limit: int = 25) -> list[dict]:
    """Return the most recent recorded alerts, newest first."""
    if db is None:
        return []
    try:
        cur = db.broker_slippage_alerts.find({}, {"_id": 0}).sort("evaluated_at", -1).limit(int(max(1, min(200, limit))))
        return [row async for row in cur]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[slippage_alerts] recent_alerts failed: %s", exc)
        return []


async def ensure_indexes(db: Any) -> None:
    if db is None:
        return
    try:
        await db.broker_slippage_alerts.create_index([("broker", 1), ("evaluated_at", -1)])
        await db.broker_slippage_alerts.create_index([("evaluated_at", -1)])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[slippage_alerts] ensure_indexes failed: %s", exc)


__all__ = [
    "evaluate_broker",
    "evaluate_all",
    "recent_alerts",
    "ensure_indexes",
    "DEFAULT_MULTIPLIER",
    "DEFAULT_RECENT_WINDOW_SEC",
    "DEFAULT_BASELINE_WINDOW_SEC",
    "MIN_RECENT_SAMPLES",
    "MIN_BASELINE_SAMPLES",
]
