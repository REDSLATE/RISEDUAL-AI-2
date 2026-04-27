"""Mongo-async writer + outcome updater for the adversarial decision log.

Why this exists
---------------
:mod:`services.adversarial_core` builds Bull/Bear/Commander decisions
as pure dicts. This module persists them and later patches each row
with the realised trade outcome so we can score the agents.

Schema (``crypto_adversarial_decision_log``)
--------------------------------------------
::

    {
        "_id": ObjectId(...),
        "decision_id": "uuid4",          # exposed so trade row can FK to it
        "trade_id": str | None,          # back-reference once a fill happens
        "timestamp": datetime,
        "symbol": str,
        "regime": str | None,
        "phase": "shadow" | "risk_only" | "veto" | "full",
        "decision": "LONG" | "SHORT_OR_AVOID" | "NO_TRADE",
        "edge_gap": float,
        "risk_multiplier": float,
        "bull_case": {side, confidence, expected_r, thesis, invalidations},
        "bear_case": {...},
        "bull_score": float,
        "bear_score": float,

        # Patched on close by ``update_decision_outcome``:
        "final_result_r": float | None,
        "winner": "bull" | "bear" | "neutral" | None,
        "loser": "bull" | "bear" | "neutral" | None,
        "closed_at": datetime | None,
    }

Failure semantics
-----------------
Every Mongo call is wrapped in try/except — these writes are
observability data, never on the critical fill path. A logging
failure must NEVER block a live trade.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

DECISION_COLLECTION = "crypto_adversarial_decision_log"


async def ensure_indexes(db: Any) -> None:
    """Idempotent index creation. Called from server.py on startup."""
    if db is None:
        return
    try:
        await db[DECISION_COLLECTION].create_index(
            [("timestamp", -1)], name="timestamp_desc",
        )
        await db[DECISION_COLLECTION].create_index(
            [("symbol", 1), ("timestamp", -1)], name="symbol_timestamp",
        )
        await db[DECISION_COLLECTION].create_index(
            [("decision_id", 1)], name="decision_id_unique", unique=True,
        )
        await db[DECISION_COLLECTION].create_index(
            [("trade_id", 1)], name="trade_id_lookup", sparse=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adversarial-log] index creation failed: %s", exc)


async def log_adversarial_decision(
    db: Any,
    decision: dict[str, Any],
    *,
    trade_id: Optional[str] = None,
) -> Optional[str]:
    """Persist a Bull/Bear/Commander decision.

    Returns the ``decision_id`` (uuid string) on success, or ``None`` on
    any failure — so the caller can attach it to the trade row only
    when persistence actually succeeded.
    """
    if db is None or not isinstance(decision, dict):
        return None
    decision_id = str(uuid4())

    # Rebuild the timestamp as a real datetime (decision came in as
    # ISO string from adversarial_core for JSON-friendliness; Mongo
    # prefers native datetime for sorting).
    ts = datetime.now(timezone.utc)

    doc = {
        **decision,
        "decision_id": decision_id,
        "trade_id": trade_id,
        "timestamp": ts,
        "final_result_r": None,
        "winner": None,
        "loser": None,
        "closed_at": None,
    }

    try:
        await db[DECISION_COLLECTION].insert_one(doc)
        # Strip Mongo's injected ObjectId so `doc` stays JSON-safe
        # for any caller that consumes it after the await.
        doc.pop("_id", None)
        return decision_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adversarial-log] insert failed: %s", exc)
        return None


def derive_winner(decision: str, final_r: float) -> tuple[str, str]:
    """Pure-function winner/loser derivation. Returned as a tuple
    so the caller can choose how to store it.

    Rules:
      * decision="LONG" + final_r > 0 → bull won
      * decision="LONG" + final_r ≤ 0 → bull lost (bear right to disagree)
      * decision="SHORT_OR_AVOID" + final_r > 0 → bear lost (bull was right)
      * decision="SHORT_OR_AVOID" + final_r ≤ 0 → bear won
      * decision="NO_TRADE" — handled by phase:
          In ``shadow`` phase (default until promoted), the trade
          fires regardless of Commander's vote, so we DO have a
          realised r_multiple and can attribute. r > 0 means Bull
          was right to want it; r ≤ 0 means Bear was right to skip.
          In ``veto``/``full`` phases, NO_TRADE blocks the fill
          entirely and update_decision_outcome never gets called
          (no decision_id on a non-existent trade row). So this
          branch only ever runs in shadow phase, where the
          attribution is well-defined.
      * Anything unrecognised falls through to neutral so a future
        decision-type rename can't silently misattribute history.
    """
    d = (decision or "").upper()
    if d == "LONG":
        return ("bull", "bear") if final_r > 0 else ("bear", "bull")
    if d == "SHORT_OR_AVOID":
        return ("bear", "bull") if final_r <= 0 else ("bull", "bear")
    if d == "NO_TRADE":
        return ("bull", "bear") if final_r > 0 else ("bear", "bull")
    return ("neutral", "neutral")


async def update_decision_outcome(
    db: Any,
    decision_id: str,
    final_r: float,
) -> bool:
    """Patch a logged decision with the realised R-multiple after the
    trade closes. Returns ``True`` on success.

    Idempotent — re-running with the same ``decision_id`` overwrites
    the outcome fields, which is fine for closer reruns.
    """
    if db is None or not decision_id:
        return False
    try:
        existing = await db[DECISION_COLLECTION].find_one(
            {"decision_id": decision_id},
            {"_id": 0, "decision": 1},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adversarial-log] lookup failed: %s", exc)
        return False
    if not existing:
        return False

    winner, loser = derive_winner(existing.get("decision") or "", final_r)
    try:
        result = await db[DECISION_COLLECTION].update_one(
            {"decision_id": decision_id},
            {"$set": {
                "final_result_r": float(final_r),
                "winner": winner,
                "loser": loser,
                "closed_at": datetime.now(timezone.utc),
            }},
        )
        return result.matched_count == 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adversarial-log] outcome update failed: %s", exc)
        return False
