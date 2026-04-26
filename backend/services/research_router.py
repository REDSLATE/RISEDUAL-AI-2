"""Cost-aware research router + cache for the crypto shadow lane.

Why a router exists
-------------------
Tavily's premium tier and the LLM stance classifier both cost money per
call. The crypto bot tick fires every few minutes across 8 symbols, so
calling research on every tick would be wasteful.

This router gates the call with two layers:

1. **Trigger gate** (:func:`should_run_web_research`) — only fire on
   high-conviction or narrative-sensitive setups. The exact rule set is
   captured in ``_GATE_RULES``.

2. **Time-bucketed cache** (Mongo collection ``web_research_cache``) —
   the same symbol/regime combination won't be re-queried more than once
   per ``CACHE_TTL_SECONDS``. Prevents tick-storm cost spikes when the
   bot wakes up with sticky signals.

Operational kill-switch
-----------------------
Set ``CRYPTO_SHADOW_RESEARCH_DISABLED=1`` in the env to short-circuit
:func:`fetch_or_skip` (returns ``None`` immediately, no Tavily / LLM
spend, no DB writes). Used by the test suite and as an ops escape
hatch if the Tavily account hits a billing alert.

Architectural rule (SHADOW ONLY)
--------------------------------
This module decides WHEN to fetch + cache narrative context. The verdict
itself is built by :mod:`services.web_research_service`. Neither layer
may ever feed back into trade direction or confidence — verdict is logged
into ``crypto_signal_audit_log`` for later evaluation only.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

CACHE_COLLECTION = "web_research_cache"
CACHE_TTL_SECONDS = 600  # 10 min — same as the search-war-room adapter.

# Gating thresholds — kept inline so the rules can be unit-tested
# without hitting Mongo or Tavily.
HIGH_CONVICTION_CONFIDENCE = 0.70
NARRATIVE_REGIMES = frozenset({"parabolic", "overbought", "oversold"})


def should_run_web_research(signal: dict[str, Any]) -> tuple[bool, str]:
    """Return ``(run, reason)`` — whether this signal qualifies for a
    Tavily/LLM shadow call.

    Rules (any one triggers a fire):
    * Direction must be LONG or SHORT (HOLD signals add no narrative
      value — there's no bias to confirm or contradict).
    * Combined confidence ≥ HIGH_CONVICTION_CONFIDENCE.
    * OR the regime is in ``NARRATIVE_REGIMES`` (parabolic / overbought /
      oversold) where macro headlines often dominate the next bar's
      direction.

    Returns ``(False, reason)`` for skips so the caller can log them.
    """
    direction = (signal.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return False, "direction_not_actionable"

    try:
        conf = float(signal.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0

    regime = (signal.get("regime") or "").lower()

    if conf >= HIGH_CONVICTION_CONFIDENCE:
        return True, "high_conviction"
    if regime in NARRATIVE_REGIMES:
        return True, f"narrative_regime:{regime}"
    return False, f"low_conviction_conf={conf:.2f}_regime={regime or 'neutral'}"


async def get_cached_verdict(
    db: Any,
    symbol: str,
    *,
    ttl_seconds: int = CACHE_TTL_SECONDS,
) -> Optional[dict[str, Any]]:
    """Lookup a cached verdict for ``symbol``.

    Returns ``None`` when:
    * no DB handle was provided,
    * no cache entry exists, or
    * the entry is older than ``ttl_seconds``.
    """
    if db is None:
        return None
    try:
        doc = await db[CACHE_COLLECTION].find_one(
            {"symbol": symbol.upper()},
            {"_id": 0},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research_router] cache read failed: %s", exc)
        return None
    if not doc:
        return None
    cached_at = doc.get("cached_at")
    if not isinstance(cached_at, datetime):
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    if cached_at < cutoff:
        return None
    verdict = doc.get("verdict")
    if not isinstance(verdict, dict):
        return None
    # Mark the served verdict as a cache hit so downstream logs are
    # honest about provenance.
    return {**verdict, "cached": True}


async def set_cached_verdict(
    db: Any,
    symbol: str,
    verdict: dict[str, Any],
) -> None:
    """Idempotent upsert of the cache entry. Failures are swallowed."""
    if db is None or not isinstance(verdict, dict):
        return
    try:
        await db[CACHE_COLLECTION].update_one(
            {"symbol": symbol.upper()},
            {
                "$set": {
                    "symbol": symbol.upper(),
                    "verdict": verdict,
                    "cached_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research_router] cache write failed: %s", exc)


async def ensure_indexes(db: Any) -> None:
    """Create the unique symbol index. Called once at startup."""
    if db is None:
        return
    try:
        await db[CACHE_COLLECTION].create_index(
            [("symbol", 1)], name="symbol_unique", unique=True,
        )
        await db[CACHE_COLLECTION].create_index(
            [("cached_at", -1)], name="cached_at_desc",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research_router] index creation failed: %s", exc)


async def fetch_or_skip(
    db: Any,
    symbol: str,
    signal: dict[str, Any],
    *,
    fetcher,
) -> Optional[dict[str, Any]]:
    """High-level orchestrator used by the crypto bot.

    ``fetcher`` is an injected awaitable
    (``async def fetcher(symbol, signal) -> dict``) — defaults to the
    real ``web_research_service.get_shadow_verdict`` in production but
    can be overridden in tests.

    Returns the verdict dict on a fire, or ``None`` when the gate or
    error suppression filtered the call out. Never raises.
    """
    # Operational kill-switch — short-circuits before any gate / cache
    # / LLM work happens. Used by the test suite and by ops if Tavily
    # billing trips.
    if os.environ.get("CRYPTO_SHADOW_RESEARCH_DISABLED") == "1":
        return None

    fire, reason = should_run_web_research(signal)
    if not fire:
        logger.debug("[research_router] skip %s: %s", symbol, reason)
        return None

    cached = await get_cached_verdict(db, symbol)
    if cached is not None:
        return cached

    try:
        verdict = await fetcher(symbol, signal)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[research_router] fetcher raised for %s: %s", symbol, exc)
        return None

    if not isinstance(verdict, dict):
        return None
    verdict.setdefault("trigger_reason", reason)
    await set_cached_verdict(db, symbol, verdict)
    return verdict
