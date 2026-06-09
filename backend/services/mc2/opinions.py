"""MC2 — local opinion ingestion.

Mirror of :mod:`services.mc2.intents` for the opinion side-channel.
Opinions are observations (never executions) and ALWAYS land with
``may_execute=False``. The wire-path emit fires on ALL verdicts
including HOLD; the local writer preserves that behaviour.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping

from services.mc2.state import get_db, now_utc_iso

logger = logging.getLogger(__name__)


async def post_opinion_local(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist an opinion payload to MC2's ``mc2_opinions`` collection.

    Returns ``{"ok": True, "opinion_id": "<uuid>"}`` on success;
    ``{"ok": False, "error": "<msg>"}`` on DB failure. Never raises.
    """
    db = get_db()
    if db is None:
        return {
            "ok": False,
            "error": "mc2 db handle not bound — call services.mc2.set_db(db)",
        }

    opinion_id = str(uuid.uuid4())
    doc = dict(payload)
    doc["opinion_id"] = opinion_id
    doc["may_execute"] = False
    doc["destination"] = "mc2_local"
    doc["recorded_at"] = now_utc_iso()

    try:
        await db.mc2_opinions.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[mc2] mc2_opinions write failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    logger.info(
        "[mc2] OPINION_ACCEPTED opinion_id=%s brain=%s symbol=%s verdict=%s",
        opinion_id,
        doc.get("brain_id") or doc.get("brain") or "?",
        doc.get("symbol") or "?",
        doc.get("verdict") or doc.get("action") or "?",
    )
    return {"ok": True, "opinion_id": opinion_id, "destination": "mc2_local"}


__all__ = ["post_opinion_local"]
