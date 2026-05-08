"""Agent activity feed endpoint — read surface over
``services.agent_activity_service``.

Consumed by the frontend `AgentActivityFeed` panel (10s polling)
and the optional admin drill-down by symbol.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, Request

from services.agent_activity_service import fetch_feature_stability, fetch_recent

router = APIRouter(prefix="/api/agent", tags=["agent"])


def _parse_since(raw: Optional[str]) -> Optional[datetime]:
    """Parse ``?since=ISO8601`` to a datetime; tolerate trailing Z."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


@router.get("/activity")
async def agent_activity(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    since: Optional[str] = Query(None, description="ISO8601 timestamp — only newer events"),
    symbol: Optional[str] = Query(None, description="Filter to a specific ticker"),
) -> dict:
    """Most recent agent activity events. Supports:

      * ``?limit=N`` — how many rows (default 50, max 200)
      * ``?since=...`` — incremental polling: pass the most recent
        timestamp you've already seen
      * ``?symbol=MRVL`` — per-ticker drill-down

    Requires auth in prod; dev routes this through the same middleware
    as other authenticated endpoints — no explicit guard here so the
    route registry's global middleware controls access.
    """
    rows = await fetch_recent(
        limit=limit,
        since=_parse_since(since),
        symbol=symbol,
    )
    return {
        "events": rows,
        "count": len(rows),
    }


@router.get("/feature-stability")
async def feature_stability(
    days: int = Query(7, ge=1, le=90),
    min_appearances: int = Query(3, ge=1, le=100),
    top_k: int = Query(15, ge=1, le=50),
) -> dict:
    """Per-feature rollup over a trailing window.

    Surfaces three things users can't see from raw events:
      * **Dominance** — which features keep showing up in the
        agent's top-3 reasoning.
      * **Drift** — signed ``avg_impact``. Sustained negative =
        the feature is pushing bearish more than bullish.
      * **Regime flip** — `bullish_frac` close to 0.5 means the
        feature is noisy / non-directional right now.

    Params:
      * ``days`` — window (default 7)
      * ``min_appearances`` — hide features seen fewer than N times
      * ``top_k`` — cap rows (UI renders compactly)
    """
    rows = await fetch_feature_stability(
        days=days, min_appearances=min_appearances, top_k=top_k,
    )
    return {
        "features": rows,
        "count": len(rows),
        "window_days": days,
    }
