"""Agent activity feed endpoint — read surface over
``services.agent_activity_service``.

Consumed by the frontend `AgentActivityFeed` panel (10s polling)
and the optional admin drill-down by symbol.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query, Request

from services.agent_activity_service import fetch_recent

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
