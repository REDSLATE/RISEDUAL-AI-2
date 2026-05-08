"""Alpha daily mandate — behavioural forcing function.

Goals:
  * ≥20 round-trips per day (equity-only, paper account scope)
  * Pressure curve: confidence threshold 0.70 → 0.60 across the
    session as the count lags target
  * 15:30 ET no-open: do not open new positions after 15:30 ET
  * 15:55 ET force-flat: close all open positions

This module is a pure logic helper. It does NOT call the broker —
the executor / scheduler does, this module just answers
"should I open right now?" / "should I force flat?".

State is computed against MongoDB collection
``alpha_round_trip_log`` (one doc per round trip). Lookup is scoped
to ``today (US/Eastern)``.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

ROUND_TRIP_COLLECTION = "alpha_round_trip_log"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


# ET → UTC offset is timezone-aware via zoneinfo on production. For
# the helper math we accept either explicit ET-aware datetimes or
# fall back to assuming the server clock is UTC and shifting -5h
# (DST not corrected — operator must tune envs in summer).
def _et_now(now: Optional[datetime] = None) -> datetime:
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # Fallback: naive UTC-5
        from datetime import timedelta
        return (now - timedelta(hours=5)).replace(tzinfo=None)


@dataclass
class MandatePressure:
    completed: int
    target: int
    fraction_complete: float
    adjusted_threshold: float
    raw_floor: float
    raw_ceiling: float
    minutes_remaining: int


def _market_minutes_remaining(et_now: datetime) -> int:
    """Approximate equity-session minutes until 16:00 ET close.

    Returns 0 outside of regular session hours. Used as the pressure
    denominator.
    """
    open_time = dtime(9, 30)
    close_time = dtime(16, 0)
    cur = et_now.time()
    if cur < open_time:
        # Whole session ahead.
        return (close_time.hour - open_time.hour) * 60
    if cur >= close_time:
        return 0
    end_min = close_time.hour * 60 + close_time.minute
    cur_min = cur.hour * 60 + cur.minute
    return max(0, end_min - cur_min)


def compute_pressure(
    completed: int,
    *,
    now: Optional[datetime] = None,
) -> MandatePressure:
    """Return current pressure-adjusted confidence threshold.

    Curve: at session open, threshold = ceiling (0.70 default). As the
    session progresses, the threshold linearly decays toward ``floor``
    (0.60) IF the operator is behind pace. If the operator is on /
    ahead of pace, the threshold stays at ceiling (no need to lower
    the bar).
    """
    target = _env_int("ALPHA_DAILY_TARGET_ROUND_TRIPS", 20)
    floor = _env_float("ALPHA_THRESHOLD_FLOOR", 0.60)
    ceiling = _env_float("ALPHA_THRESHOLD_CEILING", 0.70)

    et = _et_now(now)
    mins_left = _market_minutes_remaining(et)

    fraction = completed / target if target > 0 else 1.0
    if fraction >= 1.0:
        adj = ceiling
    else:
        # Time fraction completed ∈ [0,1]
        total_session_min = 6.5 * 60
        time_fraction = max(0.0, min(1.0, 1.0 - mins_left / total_session_min))
        # Behind pace if time_fraction > completion_fraction.
        deficit = max(0.0, time_fraction - fraction)
        # deficit ∈ [0,1]; deficit=1 means we're 100% behind → use floor.
        adj = ceiling - (ceiling - floor) * min(1.0, deficit)
        adj = max(floor, min(ceiling, adj))

    return MandatePressure(
        completed=completed,
        target=target,
        fraction_complete=fraction,
        adjusted_threshold=adj,
        raw_floor=floor,
        raw_ceiling=ceiling,
        minutes_remaining=mins_left,
    )


def should_block_new_open(now: Optional[datetime] = None) -> bool:
    """Returns True when the no-open window is active (≥15:30 ET)."""
    et = _et_now(now)
    cutoff = dtime(15, 30)
    return et.time() >= cutoff


def should_force_flat(now: Optional[datetime] = None) -> bool:
    """Returns True when 15:55 ET force-flat trigger fires."""
    et = _et_now(now)
    cutoff = dtime(15, 55)
    return et.time() >= cutoff


# ── DB helpers ───────────────────────────────────────────────────


async def count_round_trips_today(db, *, now: Optional[datetime] = None) -> int:
    """Count completed round trips so far today (US/Eastern)."""
    if db is None:
        return 0
    et = _et_now(now)
    et_midnight = et.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        from zoneinfo import ZoneInfo
        utc_floor = et_midnight.astimezone(ZoneInfo("UTC"))
    except Exception:
        from datetime import timedelta
        utc_floor = et_midnight + timedelta(hours=5)
    try:
        return int(await db[ROUND_TRIP_COLLECTION].count_documents({
            "completed_at": {"$gte": utc_floor},
        }))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpha_mandate] count_round_trips_today failed: %s", exc)
        return 0


async def ensure_indexes(db) -> None:
    if db is None:
        return
    try:
        await db[ROUND_TRIP_COLLECTION].create_index(
            [("completed_at", -1)], name="alpha_rt_completed_ts",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpha_mandate] ensure_indexes failed: %s", exc)
