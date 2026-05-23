"""Sovereign Outcome Bridge — backend → sidecar inbox (2026-05-22).

The deterministic Sovereign core sitting in the alpha-sidecar process
keeps a list of recent outcomes that ride along with every
contribution to MC. Historically nothing populated this list, so MC
saw "empty payload" rows for months.

This module is the WRITER half of the bridge. It enqueues outcome
events into the ``sovereign_outcomes_inbox`` Mongo collection
whenever a paper trade resolves. The sidecar (separate process)
drains the inbox each tick and calls ``LocalState.add_outcome``
locally.

Doctrine:
* Each event is keyed by ``trade_id`` (idempotent — re-running the
  closer never duplicates an event).
* ``brain`` is tagged so a future multi-brain pod doesn't cross-
  contaminate state.
* The ``outcome`` field is the LocalState contract: ``1`` for a
  win, ``-1`` for a loss, ``0`` for flat.
* Observation-fill rows DO get enqueued — they're learning-eligible
  by design (the whole point of the observation rung).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


COLLECTION = "sovereign_outcomes_inbox"


def _outcome_int(label: str) -> int:
    """Translate the closer's outcome label into the LocalState
    integer contract."""
    s = (label or "").lower()
    if s == "win":
        return 1
    if s == "loss":
        return -1
    return 0


def _action_token(direction: str) -> str:
    """Normalize paper-trade direction tokens to LocalState's
    ALLOWED_ACTIONS (BUY / SELL). Routes through the centralised
    ``canonical_ai_dir`` so this module never owns its own
    direction alias table (CI invariant ``test_no_local_direction_tuples``)."""
    from services.prediction_tracker import canonical_ai_dir
    side = canonical_ai_dir(direction)
    if side == "LONG":
        return "BUY"
    if side == "SHORT":
        return "SELL"
    return "BUY"


async def enqueue_outcome(
    db: Any, *,
    brain: str,
    trade_id: str,
    symbol: str,
    direction: str,
    confidence: float,
    outcome_label: str,
    notional: float = 0.0,
    extras: Optional[Mapping[str, Any]] = None,
    # ── 2026-05-22 (Gap 2): provenance fields propagated to MC ─────
    # so the audit lineage (Sovereign decision → fill → outcome) is
    # visible end-to-end in MC's diagnostics. Optional — older
    # callers that don't have them still work; the sidecar / MC
    # client only emit non-None values.
    sovereign_decision_id: Optional[str] = None,
    prediction_id: Optional[str] = None,
    source_signal: Optional[str] = None,
) -> dict[str, Any]:
    """Idempotently enqueue an outcome event for the named brain.

    Returns ``{"ok": True, "deduped": False}`` on first insert,
    ``{"ok": True, "deduped": True}`` if a row with the same
    ``trade_id`` already exists, or ``{"ok": False, "reason": ...}``
    on validation/DB errors.
    """
    if not trade_id:
        return {"ok": False, "reason": "missing_trade_id"}
    if not brain:
        return {"ok": False, "reason": "missing_brain"}

    doc = {
        "trade_id": str(trade_id),
        "brain": str(brain),
        "symbol": str(symbol or ""),
        "action": _action_token(direction),
        "confidence": float(confidence or 0.0),
        "outcome": _outcome_int(outcome_label),
        "outcome_label": (outcome_label or "").lower(),
        "notional": float(notional or 0.0),
        "resolved_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
        "drained": False,
        "extras": dict(extras or {}),
        # Provenance — top-level so the drainer + sidecar can
        # forward them to MC without parsing nested extras.
        "sovereign_decision_id": (
            str(sovereign_decision_id) if sovereign_decision_id else None
        ),
        "prediction_id": (
            str(prediction_id) if prediction_id else None
        ),
        "source_signal": (
            str(source_signal) if source_signal else None
        ),
    }
    try:
        existing = await db[COLLECTION].find_one(
            {"trade_id": doc["trade_id"], "brain": doc["brain"]},
            {"_id": 0},
        )
        if existing:
            return {"ok": True, "deduped": True}
        await db[COLLECTION].insert_one(doc)
        logger.info(
            "[outcome_bridge] enqueued %s brain=%s symbol=%s outcome=%s",
            trade_id, brain, symbol, outcome_label,
        )
        return {"ok": True, "deduped": False}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[outcome_bridge] enqueue failed for %s: %s", trade_id, exc,
        )
        return {"ok": False, "reason": f"db_error:{type(exc).__name__}"}


__all__ = ["COLLECTION", "enqueue_outcome", "_outcome_int", "_action_token"]
