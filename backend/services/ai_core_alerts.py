"""AI Core date-bucketed alert writer.

A separate alert collection from `notifications` (which carries
verdict-flip + toxic-spike alerts to end users). This one is purely
operational — daily summaries the operator reads in the admin
panel — so the data shapes, retention, and dedup rules are
allowed to differ.

Dedup rule
----------
Every alert id = ``f"{type}:{date_bucket}"`` where ``date_bucket`` is
the alert's *intended* date (typically today's UTC YYYY-MM-DD).
A unique index on ``id`` makes a same-day re-fire a no-op insert,
which is exactly what we want when the nightly cron runs twice
(e.g., manual trigger + scheduled run).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_db: Any = None


def set_db(db: Any) -> None:
    global _db
    _db = db


async def ensure_indexes() -> None:
    if _db is None:
        return
    try:
        await _db["ai_core_alerts"].create_index(
            "id", unique=True, name="ai_core_alert_id_unique",
        )
        await _db["ai_core_alerts"].create_index([("created_at", -1)])
    except Exception as e:  # noqa: BLE001
        logger.warning("[ai_core_alerts] index ensure failed: %s", e)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def emit(
    alert_type: str,
    *,
    title: str,
    message: str,
    metadata: Optional[dict] = None,
    date_bucket: Optional[str] = None,
) -> dict:
    """Insert an alert; same (type, date_bucket) twice = no-op.

    Returns ``{ok, deduped, id}``.
    """
    if _db is None:
        return {"ok": False, "reason": "db_unavailable"}
    bucket = date_bucket or _today_utc()
    alert_id = f"{alert_type}:{bucket}"
    doc = {
        "id": alert_id,
        "type": alert_type,
        "title": title,
        "message": message,
        "metadata": metadata or {},
        "date_bucket": bucket,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await _db["ai_core_alerts"].insert_one(doc.copy())
        return {"ok": True, "deduped": False, "id": alert_id}
    except Exception as e:  # noqa: BLE001
        # DuplicateKeyError lives in pymongo.errors; importing here to
        # keep the module decoupled from the driver type.
        msg = str(e).lower()
        if "duplicate" in msg or "e11000" in msg:
            return {"ok": True, "deduped": True, "id": alert_id}
        logger.warning("[ai_core_alerts] emit failed: %s", e)
        return {"ok": False, "reason": str(e)}


async def list_alerts(limit: int = 50) -> list[dict]:
    if _db is None:
        return []
    cursor = _db["ai_core_alerts"].find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cursor.to_list(length=limit)
