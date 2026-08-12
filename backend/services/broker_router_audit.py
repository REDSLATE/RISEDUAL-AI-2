"""Broker Router Audit — record every per-bot broker switch.

Why this exists
---------------
Once Alpha routes through either Public.com or MooMoo, an operator
switch on a bot's ``broker`` field can silently change which venue
fills every subsequent order for that bot. If a later post-mortem
shows an unusual fill, we need a paper trail that says
"bot X moved from public → moomoo at 14:07 by operator@…" — otherwise
we can't tell whether Alpha's edge changed or the venue changed.

Storage
-------
Compact Mongo collection ``broker_router_audit`` — one doc per switch.
Not high-frequency, so no reason to route this through SQLite.

Schema
------
    {
      "bot_id":      "<mongo id string>",
      "user_id":     "<mongo id string>",
      "operator":    "<email or user id>",
      "from_broker": "public" | "moomoo" | null,
      "to_broker":   "public" | "moomoo",
      "reason":      "operator_switch",
      "created_at":  ISO 8601 UTC,
    }
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_COLLECTION = "broker_router_audit"


async def record_switch(
    db: Any,
    *,
    bot_id: str,
    user_id: str,
    operator: str,
    from_broker: Optional[str],
    to_broker: str,
    reason: str = "operator_switch",
) -> None:
    """Best-effort audit write. Never raises."""
    if db is None or not to_broker:
        return
    try:
        await db[_COLLECTION].insert_one({
            "bot_id": str(bot_id),
            "user_id": str(user_id or ""),
            "operator": str(operator or ""),
            "from_broker": (str(from_broker).lower() if from_broker else None),
            "to_broker": str(to_broker).lower(),
            "reason": reason or "operator_switch",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[broker_router_audit] write failed: %s", exc)


async def recent(db: Any, *, limit: int = 50, bot_id: Optional[str] = None) -> list[dict]:
    """Return the most recent switches, newest first. Safe on missing collection."""
    if db is None:
        return []
    try:
        q: dict = {}
        if bot_id:
            q["bot_id"] = str(bot_id)
        cur = db[_COLLECTION].find(q, {"_id": 0}).sort("created_at", -1).limit(int(max(1, min(500, limit))))
        return [row async for row in cur]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[broker_router_audit] read failed: %s", exc)
        return []


async def ensure_indexes(db: Any) -> None:
    """Idempotent index creation. Called on boot; safe to no-op."""
    if db is None:
        return
    try:
        await db[_COLLECTION].create_index([("bot_id", 1), ("created_at", -1)])
        await db[_COLLECTION].create_index([("created_at", -1)])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[broker_router_audit] ensure_indexes failed: %s", exc)


__all__ = ["record_switch", "recent", "ensure_indexes"]
