"""MC2 — local outcome ingestion + scorecard primitives.

``record_outcome_local`` is the standalone-mode mirror of MC's
remote ``recent_outcomes`` ingest path. Called from
``services.sovereign_outcome_bridge.enqueue_outcome`` when MC2 is
the destination. This is the collection that finally unblocks
the ``total_resolved=0`` Scorecard pain — no remote dependency.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping

from services.mc2.state import get_db, now_utc_iso

logger = logging.getLogger(__name__)


# Valid labels mirror MC's published taxonomy. Anything else is
# coerced to ``flat`` so the scorecard rollup never has to defend
# against typos / drift.
_VALID_LABELS = frozenset({"win", "loss", "flat"})


async def record_outcome_local(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist an outcome payload to MC2's ``mc2_outcomes`` collection.

    Returns ``{"ok": True, "outcome_id": "<uuid>"}`` on success;
    ``{"ok": False, "error": "<msg>"}`` on DB failure. Never raises —
    the close-trade path swallows exceptions anyway and we don't
    want a Mongo blip to break paper-close accounting.
    """
    db = get_db()
    if db is None:
        return {
            "ok": False,
            "error": "mc2 db handle not bound — call services.mc2.set_db(db)",
        }

    outcome_id = str(uuid.uuid4())
    doc = dict(payload)
    doc["outcome_id"] = outcome_id
    doc["destination"] = "mc2_local"
    doc["recorded_at"] = now_utc_iso()

    label = (doc.get("outcome_label") or doc.get("label") or "").lower()
    if label not in _VALID_LABELS:
        label = "flat"
    doc["outcome_label"] = label

    try:
        await db.mc2_outcomes.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[mc2] mc2_outcomes write failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    logger.info(
        "[mc2] OUTCOME_RECORDED outcome_id=%s brain=%s symbol=%s label=%s",
        outcome_id,
        doc.get("brain") or doc.get("brain_id") or "?",
        doc.get("symbol") or "?",
        label,
    )
    return {"ok": True, "outcome_id": outcome_id, "destination": "mc2_local"}


__all__ = ["record_outcome_local"]
