"""Backfill Signal Matcher (2026-05-22) — extracted from the
Alpaca reconciliation script so it stays under the 400-line script
ceiling AND becomes reusable by any future broker backfill.

Given an Alpaca fill (symbol, side, timestamp), walk the local
``sovereign_decisions`` and ``predictions`` collections for the
signal that fired the order. Returns a labelling dict with
provenance + confidence, or ``None`` for unattributed fills.

Operator hard rule: every backfilled fill MUST carry a label —
labelled rows get the matched signal's ID + confidence, unmatched
rows are explicitly tagged ``unattributed_real_fill`` (never a
synthetic confidence). Downstream code reads the
``labelled``/``source_signal`` flags to decide what to count.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger("backfill_signal_matcher")


# Window the matcher looks back for a signal that produced the
# fill. 10 minutes covers the slowest dispatch path
# (ml_paper_trader runs on a 60s cadence; live broker submit +
# Alpaca queueing typically adds <2min). Larger windows risk
# false matches against an unrelated later prediction; smaller
# windows lose legit matches.
SIGNAL_MATCH_WINDOW = timedelta(minutes=10)


def direction_matches(signal_dir: str, alpaca_side: str) -> bool:
    """``signal_dir`` may be ``up/down``, ``long/short``,
    ``buy/sell``, or the LocalState ``BUY/SELL`` tokens.
    ``alpaca_side`` is Alpaca's lower-case ``buy`` / ``sell``.

    Routes through the centralised ``canonical_ai_dir`` so this
    module never owns its own direction alias table (CI invariant
    ``test_no_local_direction_tuples``)."""
    from services.prediction_tracker import canonical_ai_dir
    side = canonical_ai_dir(signal_dir)
    a = (alpaca_side or "").lower()
    if a == "buy":
        return side == "LONG"
    if a == "sell":
        return side == "SHORT"
    return False


async def match_signal(
    db: Any, *, symbol: str, side: str, when: datetime,
) -> Optional[dict[str, Any]]:
    """Return the closest-in-time prediction or sovereign_decision
    for this fill, or ``None``.

    Search order:
      1. ``sovereign_decisions``  (richest provenance)
      2. ``predictions``          (older ML signal layer)

    Returned dict carries the canonical labelling fields stamped
    onto the paper_trades row.
    """
    sym = (symbol or "").upper()
    window_start = when - SIGNAL_MATCH_WINDOW
    window_end = when

    # ── 1) Sovereign decisions (preferred) ──────────────────────
    try:
        cursor = db["sovereign_decisions"].find(
            {
                "symbol": sym,
                "created_at": {"$gte": window_start, "$lte": window_end},
            },
            {"_id": 0, "decision_id": 1, "action": 1, "direction": 1,
             "confidence": 1, "conviction_tier": 1, "created_at": 1,
             "feature_snapshot": 1},
        ).sort("created_at", -1).limit(10)
        async for row in cursor:
            if direction_matches(
                row.get("action") or row.get("direction"), side,
            ):
                return {
                    "source_signal": "sovereign_decision",
                    "sovereign_decision_id": row.get("decision_id"),
                    "confidence": float(row.get("confidence") or 0.0),
                    "conviction_tier": row.get("conviction_tier"),
                    "signal_at": row.get("created_at"),
                    "regime": (row.get("feature_snapshot") or {}).get("regime"),
                    "labelled": True,
                }
    except Exception as exc:  # noqa: BLE001
        logger.debug("sovereign_decisions lookup failed: %s", exc)

    # ── 2) Predictions (legacy ML signal layer) ─────────────────
    # ``predictions.timestamp`` is stored as ISO string in this stack.
    try:
        cursor = db["predictions"].find(
            {
                "symbol": sym,
                "timestamp": {
                    "$gte": window_start.isoformat(),
                    "$lte": window_end.isoformat(),
                },
            },
            {"_id": 0, "prediction_id": 1, "direction": 1, "confidence": 1,
             "timestamp": 1, "feature": 1},
        ).sort("timestamp", -1).limit(10)
        async for row in cursor:
            if direction_matches(row.get("direction"), side):
                conf_raw = float(row.get("confidence") or 0.0)
                # Predictions store conf 0-100; normalize to 0-1.
                conf_unit = conf_raw / 100.0 if conf_raw > 1.5 else conf_raw
                return {
                    "source_signal": "prediction",
                    "prediction_id": row.get("prediction_id"),
                    "confidence": conf_unit,
                    "signal_at": row.get("timestamp"),
                    "feature": row.get("feature"),
                    "labelled": True,
                }
    except Exception as exc:  # noqa: BLE001
        logger.debug("predictions lookup failed: %s", exc)

    return None


__all__ = ["SIGNAL_MATCH_WINDOW", "direction_matches", "match_signal"]
