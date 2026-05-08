"""Shared primitives for autonomous trading agents.

All new agents (mean reversion, options paper, earnings watchdog)
open paper trades by calling :func:`record_paper_trade`, which:

  * Writes a row into ``learning_engine_trades`` tagged with
    ``strategy`` so the ML retrain can segment learning curves.
  * Narrates the decision into the agent activity feed via
    :mod:`services.agent_activity_service` so it shows up live
    in the "Agent Activity" panel.
  * Respects the global :class:`ai_core.kill_switch.KillSwitch`
    state — an active kill switch short-circuits every agent.

Agents are scheduled jobs (APScheduler) registered in
``server.py``; they don't hold state between invocations. Keep
them pure-functional where possible and idempotent across
restarts — a missed run cycle is fine, a double-trade is not.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_LE_TRADES = "learning_engine_trades"
_LE_STATS = "learning_engine_stats"
_LE_DOC = "global"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def kill_switch_blocks() -> bool:
    """Returns True if the global kill switch is active.

    Agents must call this before opening any position. A log line
    fires on the blocked path so the user can see *why* the agent
    went quiet in the activity feed.
    """
    try:
        from ai_core.kill_switch import kill_switch
        return bool(kill_switch.is_active())
    except Exception as e:
        logger.warning("[agents] kill_switch probe failed: %s", e)
        return False


async def record_paper_trade(
    db: Any,
    *,
    strategy: str,
    ticker: str,
    direction: str,
    entry: float,
    stop_loss: Optional[float],
    target: Optional[float],
    position_usd: float,
    confidence: float,
    thesis: str,
    why: Optional[list[dict]] = None,
    extra_metadata: Optional[dict] = None,
) -> Optional[str]:
    """Persist a paper trade + narrate. Returns trade_id on success.

    Contract:
      * Idempotent per (strategy, ticker, direction) within 4 hours.
        Agents are scheduled; this guard prevents a double-trade
        when the schedule interval is shorter than the trade
        horizon. Returns None on dedupe hit.
      * Activity narration is fire-and-forget; persistence failure
        rolls back (returns None).
    """
    if db is None:
        return None

    # Dedupe window — 4h. Short enough to not block a legitimate
    # re-entry next session, long enough to catch schedule overlap.
    try:
        from datetime import timedelta
        since = _now() - timedelta(hours=4)
        existing = await db[_LE_TRADES].find_one({
            "strategy": strategy,
            "asset": ticker.upper(),
            "direction": direction.upper(),
            "status": "pending",
            "logged_at": {"$gte": since.isoformat()},
        })
        if existing is not None:
            logger.debug(
                "[agents] dedupe: %s already has pending %s %s",
                strategy, ticker, direction,
            )
            return None
    except Exception as e:
        logger.warning("[agents] dedupe check failed: %s", e)

    trade_id = str(uuid4())
    doc = {
        "_id": trade_id,
        "trade_id": trade_id,
        "strategy": strategy,
        "asset": ticker.upper(),
        "direction": direction.upper(),
        "entry": round(float(entry), 4),
        "stop_loss": round(float(stop_loss), 4) if stop_loss else None,
        "target": round(float(target), 4) if target else None,
        "position_usd": round(float(position_usd), 2),
        "confidence": round(float(confidence), 4),
        "thesis": thesis.strip()[:280],
        "status": "pending",
        "win": None,
        "exit_price": None,
        "pnl": None,
        "r_multiple": None,
        "logged_at": _now().isoformat(),
        "resolved_at": None,
        "user_id": None,  # owner-less; ML learns aggregate signal
    }
    try:
        await db[_LE_TRADES].insert_one(doc)
    except Exception as e:
        logger.exception("[agents] insert failed: %s", e)
        return None

    # Increment pending counter on the stats doc.
    try:
        await db[_LE_STATS].update_one(
            {"_id": _LE_DOC},
            {"$inc": {"counts.pending": 1}},
            upsert=True,
        )
    except Exception as e:
        logger.warning("[agents] stats increment failed: %s", e)

    # Narrate — pass the strategy into metadata so the UI can
    # eventually filter by it.
    try:
        from services.agent_activity_service import log_event
        meta = {
            "strategy": strategy,
            "direction": direction,
            "entry": round(entry, 4),
            "stop_loss": stop_loss,
            "target": target,
            "position_usd": round(position_usd, 2),
            "confidence": round(confidence, 4),
            "thesis": thesis.strip()[:280],
            "trade_id": trade_id,
            "why": why or [],
        }
        if extra_metadata:
            meta.update(extra_metadata)
        await log_event(
            type="paper_trade_open",
            severity="success",
            title=f"[{strategy}] Opened {ticker} {direction.upper()} · ${position_usd:,.0f}",
            detail=thesis,
            symbol=ticker,
            metadata=meta,
        )
    except Exception as e:
        logger.warning("[agents] narration failed: %s", e)

    return trade_id


async def narrate_scan_skip(strategy: str, reason: str,
                            count_considered: int = 0) -> None:
    """Emit a scan-completion event so the user sees the agent ran
    even when nothing qualified. Quiet (info severity) so it doesn't
    pollute the feed during slow sessions."""
    try:
        from services.agent_activity_service import log_event
        await log_event(
            type="paper_scan_start",
            severity="info",
            title=f"[{strategy}] scan complete · {reason}",
            detail=f"considered {count_considered} candidates",
            metadata={"strategy": strategy, "count": count_considered},
        )
    except Exception:
        pass
