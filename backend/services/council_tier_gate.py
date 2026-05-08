"""Council Tier promotion gate — second-engine activation guard.

The Research Shadow framework lets Council shadow the active engine
on every cycle. Once Council has accumulated enough scored dissents
to prove its disagreement-conditional accuracy, the operator can flip
``COUNCIL_RISK_MODULATOR_ENABLED=true`` to let Council influence the
Commander's emitted ``risk_multiplier`` (never direction).

This module is the data gate. The other half (env flag + the actual
modulation table) lives in :mod:`services.council_risk_modulator`.
Both gates must be open for Council to influence anything.

Per-bucket discipline
---------------------
Council might be actionable on ``(council, crypto)`` but noise on
``(council, stock)``. A global gate either over-promotes Council on
equities or locks it out of crypto. The framework is bucketed by
``(shadow_engine, asset_type)`` for a reason — the gate has to be
too. Every Commander cycle therefore asks: "is Council's tier open
**for THIS asset_type**?"

Caching
-------
``fetch_shadow_stats`` is a Mongo aggregation that scans up to 100k
rows. Calling it on every Commander cycle adds latency to the fill
path. We wrap it in a 60s TTL cache — Council's win rate doesn't
move minute-to-minute, and the staleness window is well under the
typical bot cadence (5-15 min).

Default-closed on failure
-------------------------
Any exception fetching stats returns an empty dict, which makes
:func:`council_tier_open_for_bucket` return False. A logging or
DB hiccup can never accidentally OPEN the gate; only successful
data passes through.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# Promotion thresholds. All env-overridable for tuning without
# code redeploys. Defaults pinned conservatively — 30 dissents is
# the same maturity guardrail as `MIN_DISSENT_SAMPLES`, 0.55 win
# rate has clear positive expectancy after fill costs, and the
# total_delta_usd > 0 floor prevents Council from being promoted
# on a "barely-better-than-coin-flip-but-net-positive-by-luck"
# sample.
MIN_COUNCIL_DISSENTS: int = int(os.getenv("COUNCIL_MIN_DISSENTS", "30"))
MIN_COUNCIL_WIN_RATE: float = float(os.getenv("COUNCIL_MIN_WIN_RATE", "0.55"))
MIN_COUNCIL_TOTAL_DELTA: float = float(
    os.getenv("COUNCIL_MIN_TOTAL_DELTA_USD", "0"),
)


# Module-level TTL cache. Single-process; the trading bot loop is a
# single asyncio process so cross-process invalidation isn't a
# concern. If we ever go multi-worker, replace with a Mongo TTL
# document or Redis cell.
_CACHE: Dict[str, Optional[Any]] = {"ts": None, "data": None}
_CACHE_TTL_SECONDS: int = 60


def council_tier_open_for_bucket(
    stats: Dict[str, Any],
    *,
    engine: str,
    asset_type: str,
) -> bool:
    """Pure-function gate check against a single ``(engine, asset_type)``
    bucket inside a stats payload.

    Defaults closed on:
        - Empty / missing ``stats``
        - No matching bucket for the requested engine/asset_type
        - Bucket below ANY of the three thresholds

    All three thresholds must pass — joint test prevents a noisy
    sample with one extreme reading from accidentally opening the
    gate.
    """
    if not stats:
        return False

    bucket = next(
        (
            b for b in stats.get("buckets", [])
            if b.get("shadow_engine") == engine
            and b.get("asset_type") == asset_type
        ),
        None,
    )
    if not bucket:
        return False

    return (
        int(bucket.get("dissent_count") or 0) >= MIN_COUNCIL_DISSENTS
        and float(bucket.get("win_rate") or 0.0) > MIN_COUNCIL_WIN_RATE
        and float(bucket.get("total_delta_usd") or 0.0) > MIN_COUNCIL_TOTAL_DELTA
    )


async def get_cached_council_stats(
    db: Any, *, hours: int = 72,
) -> Dict[str, Any]:
    """Fetch shadow stats with a 60s TTL cache.

    Returns ``{}`` on any error so the gate-side check defaults
    closed. Callers should not raise from here — Council's promotion
    flag must never block the active fill path.
    """
    now = datetime.now(timezone.utc)
    cached_ts = _CACHE.get("ts")
    if cached_ts is not None:
        try:
            if (now - cached_ts).total_seconds() < _CACHE_TTL_SECONDS:
                cached = _CACHE.get("data")
                return cached if isinstance(cached, dict) else {}
        except Exception:  # noqa: BLE001
            # Bad timestamp shape — invalidate and refetch.
            _CACHE["ts"] = None
            _CACHE["data"] = None

    try:
        from services.research_shadow_stats import fetch_shadow_stats
        fresh = await fetch_shadow_stats(db, hours=hours)
        if not isinstance(fresh, dict):
            fresh = {}
        _CACHE["ts"] = now
        _CACHE["data"] = fresh
        return fresh
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[council-tier] stats fetch failed, defaulting closed: %s", exc,
        )
        return {}


async def council_tier_open_cached(
    db: Any,
    *,
    engine: str = "council",
    asset_type: str,
    hours: int = 72,
) -> bool:
    """Async wrapper combining the cache fetch + per-bucket gate
    check. This is the function the Commander hook calls every
    cycle — cheap, default-closed, never raises.
    """
    stats = await get_cached_council_stats(db, hours=hours)
    return council_tier_open_for_bucket(
        stats, engine=engine, asset_type=asset_type,
    )


def _reset_cache_for_tests() -> None:
    """Test helper — clears the module-level cache between cases."""
    _CACHE["ts"] = None
    _CACHE["data"] = None
