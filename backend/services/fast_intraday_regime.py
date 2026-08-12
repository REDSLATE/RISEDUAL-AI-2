"""Fast Intraday Regime — session-scale complement to the daily HMM.

The daily HMM (``services.market_regime``) gives us the slow regime.
It reads daily bars over ~180 days and tells us the *statistical*
state of the market. That signal is useful but too slow — a
choppy_meanrevert daily label can still contain a morning momentum
ignition or an afternoon volatility expansion that we want Alpha to
respond to.

This module computes a **fast intraday regime** from current-session
features on SPY:

* today's return vs. yesterday's close
* today's realized-vol proxy (today_range / atr20)
* today's volume run-rate vs. average
* body-to-range ratio (trendiness of today's session)
* distance-from-VWAP proxy ((close - open) / (high - low + eps))
  (as a simple index-momentum indicator)

We map these features to one of 4 rules-based fast labels — no HMM
here, since we only have a single point of intraday data per
scheduler tick (daily bars). When we wire true intraday bars later
we can upgrade this to a proper HMM.

Non-blocking guarantee is preserved: any missing input → ``UNKNOWN``.

The Edge Engine can then key on ``(pattern × slow_regime × fast_regime)``
so Alpha learns things like:
    * HOD_BREAK works in ``slow=choppy fast=momentum_ignition``
    * VWAP_RECLAIM works in ``slow=choppy fast=choppy``
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MARKET_PROXY = "SPY"


async def _fetch_bars() -> Optional[list[dict]]:
    try:
        from services.market_data_pool import market_daily
    except Exception:  # noqa: BLE001
        return None
    try:
        bars = await market_daily(_MARKET_PROXY, outputsize="compact")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(bars, list) or len(bars) < 25:
        return None
    return bars


def _atr20(bars: list[dict]) -> Optional[float]:
    ranges: list[float] = []
    for b in bars[-21:-1]:
        try:
            hi = float(b.get("high") or 0.0)
            lo = float(b.get("low") or 0.0)
            if hi > 0 and lo > 0:
                ranges.append(hi - lo)
        except (TypeError, ValueError):
            continue
    return statistics.mean(ranges) if ranges else None


def _avg_volume(bars: list[dict], n: int = 20) -> Optional[float]:
    vols: list[float] = []
    for b in bars[-(n + 1):-1]:
        try:
            v = float(b.get("volume") or 0.0)
            if v > 0:
                vols.append(v)
        except (TypeError, ValueError):
            continue
    return statistics.mean(vols) if vols else None


def _classify(today_return: float, vol_ratio: float, run_rate: float,
              body_ratio: float) -> tuple[str, dict]:
    """Rules-based classifier. Returns (label, features)."""
    features = {
        "today_return_pct": round(today_return * 100, 3),
        "vol_ratio": round(vol_ratio, 3),
        "volume_run_rate": round(run_rate, 3),
        "body_ratio": round(body_ratio, 3),
    }
    # Momentum ignition: strong directional move + volume expansion
    if abs(today_return) >= 0.008 and run_rate >= 1.2 and abs(body_ratio) >= 0.3:
        label = "momentum_ignition_up" if today_return > 0 else "momentum_ignition_down"
        return label, features
    # Volatility expansion: wide range, mixed body
    if vol_ratio >= 1.4 and abs(body_ratio) < 0.3:
        return "volatility_expansion", features
    # Risk off: negative return + high vol
    if today_return < -0.005 and vol_ratio >= 1.2:
        return "risk_off", features
    # Trend day: sustained direction, normal vol
    if abs(today_return) >= 0.004 and abs(body_ratio) >= 0.5 and vol_ratio < 1.4:
        return "trend_up" if today_return > 0 else "trend_down", features
    # Otherwise: session_chop
    return "session_chop", features


async def snapshot(db: Any) -> dict:
    """Compute the fast intraday regime and persist a compact doc."""
    bars = await _fetch_bars()
    if bars is None:
        result = {
            "label": "UNKNOWN",
            "features": {},
            "reason": "no_market_data",
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        await _persist(db, result)
        return result

    try:
        today = bars[-1]
        prev_close = float(bars[-2].get("close") or 0.0)
        open_ = float(today.get("open") or 0.0)
        high = float(today.get("high") or 0.0)
        low = float(today.get("low") or 0.0)
        close = float(today.get("close") or 0.0)
        volume = float(today.get("volume") or 0.0)
    except (TypeError, ValueError, IndexError):
        result = {"label": "UNKNOWN", "features": {}, "reason": "bad_bar_fields",
                  "computed_at": datetime.now(timezone.utc).isoformat()}
        await _persist(db, result)
        return result

    if prev_close <= 0 or open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
        result = {"label": "UNKNOWN", "features": {}, "reason": "zero_price",
                  "computed_at": datetime.now(timezone.utc).isoformat()}
        await _persist(db, result)
        return result

    today_return = (close - prev_close) / prev_close
    atr = _atr20(bars) or 1.0
    today_range = high - low
    vol_ratio = today_range / atr if atr > 0 else 1.0
    avg_vol = _avg_volume(bars, 20) or volume or 1.0
    run_rate = volume / avg_vol if avg_vol > 0 else 1.0
    rng = today_range if today_range > 0 else 1.0
    body_ratio = (close - open_) / rng
    label, features = _classify(today_return, vol_ratio, run_rate, body_ratio)
    result = {
        "label": label,
        "features": features,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    await _persist(db, result)
    return result


async def _persist(db: Any, result: dict) -> None:
    if db is None:
        return
    try:
        await db.alpha_fast_regime_state.update_one(
            {"_id": "current"},
            {"$set": {**result, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[fast_regime] persist failed: %s", exc)


async def get_current(db: Any) -> dict:
    if db is None:
        return {"label": "UNKNOWN", "reason": "no_db"}
    try:
        doc = await db.alpha_fast_regime_state.find_one({"_id": "current"})
    except Exception:  # noqa: BLE001
        doc = None
    if not doc:
        return {"label": "UNKNOWN", "reason": "not_computed_yet"}
    doc.pop("_id", None)
    if isinstance(doc.get("updated_at"), datetime):
        doc["updated_at"] = doc["updated_at"].isoformat()
    return doc


__all__ = ["snapshot", "get_current"]
