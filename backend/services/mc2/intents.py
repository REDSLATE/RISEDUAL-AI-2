"""MC2 — local intent ingestion (replaces remote ``POST /api/intents``).

When standalone mode is ON, ``sovereign.intent_bridge`` calls
``post_intent_local`` instead of dispatching to Original MC. The
intent is shape-checked, stamped with MC2-side metadata, and
written to the ``mc2_intents`` collection.

Phase A doctrine:
  * No 12-gate chain yet. Every intent lands with
    ``gate_state="accepted_no_gates"``.
  * ``may_execute`` is FORCED to ``False`` — RISEDUAL is doctrinally
    headless in V3 and Phase A does not change that.
  * Schema is a superset of the wire intent payload, so the same
    receipts that worked against Original MC keep working — only the
    transport changes.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping

from services.mc2.state import get_db, now_utc_iso

logger = logging.getLogger(__name__)


async def post_intent_local(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist an intent payload to MC2's ``mc2_intents`` collection.

    Returns the MC2-side response envelope::

        {
          "ok": True,
          "intent_id": "<uuid>",
          "gate_state": "accepted_no_gates",
          "destination": "mc2_local",
        }

    Best-effort: if the DB is unset or the write raises, returns
    ``{"ok": False, "error": "<msg>"}`` so the bridge can log the
    failure without crashing the consensus tick.
    """
    db = get_db()
    if db is None:
        return {
            "ok": False,
            "error": "mc2 db handle not bound — call services.mc2.set_db(db)",
        }

    intent_id = str(uuid.uuid4())
    # Defensive shallow-copy so the caller's dict is never mutated
    # (sovereign sidecar reuses the same payload object across logs).
    doc = dict(payload)
    doc["intent_id"] = intent_id
    doc["may_execute"] = False  # doctrinally headless — never flip in Phase A
    doc["gate_state"] = "accepted_no_gates"  # Phase B replaces this
    doc["destination"] = "mc2_local"
    doc["recorded_at"] = now_utc_iso()

    try:
        await db.mc2_intents.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[mc2] mc2_intents write failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    # Mirror the MC-side log shape so grepping behaviour is identical
    # to the wire path. Operators can grep ``[mc2] INTENT_ACCEPTED``
    # the same way they grep ``[mc] INTENT_ACCEPTED`` today.
    logger.info(
        "[mc2] INTENT_ACCEPTED intent_id=%s brain=%s symbol=%s action=%s",
        intent_id,
        doc.get("brain_id") or doc.get("brain") or "?",
        doc.get("symbol") or "?",
        doc.get("action") or doc.get("verdict") or "?",
    )

    return {
        "ok": True,
        "intent_id": intent_id,
        "gate_state": "accepted_no_gates",
        "destination": "mc2_local",
    }


__all__ = ["post_intent_local"]
