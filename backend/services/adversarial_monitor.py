"""
Adversarial Cores monitor — 24h rollup for the admin Terminal chip.

Operator needs an at-a-glance read of "are Bull/Bear/Commander
actually learning?" without switching to the full Adversarial admin
tab. This module aggregates ``crypto_adversarial_decision_log`` over
the last 24 hours into a single compact dict.

Schema of each log row (written by ``adversarial_logger``)::

    {
      "decision_id": str,
      "symbol": str,
      "phase": "shadow" | "risk_only" | "veto" | "full",
      "decision": "LONG" | "SHORT_OR_AVOID" | "NO_TRADE",
      "bull_case": {"confidence": float, ...},
      "bear_case": {"confidence": float, ...},
      "edge_gap": float,
      "created_at": datetime,
      # After close (patched by update_decision_outcome):
      "final_result_r": float | None,
      "winner": "bull" | "bear" | "neutral" | None,
      "loser": "bull" | "bear" | "neutral" | None,
      "closed_at": datetime | None,
    }
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION = "crypto_adversarial_decision_log"
ENV_ENABLE = "CRYPTO_ADVERSARIAL_ENABLED"
ENV_PHASE = "CRYPTO_ADVERSARIAL_PHASE"


def _phase() -> str:
    raw = (os.environ.get(ENV_PHASE) or "shadow").lower()
    if raw not in ("shadow", "risk_only", "veto", "full"):
        return "shadow"
    return raw


def _enabled() -> bool:
    return os.environ.get(ENV_ENABLE) == "1"


async def summarize_24h(db: Any) -> dict[str, Any]:
    """One-call summary the admin Terminal chip consumes.

    Returns a stable shape even on cold-start / no rows / db=None so
    the frontend never needs to null-check every field.
    """
    enabled = _enabled()
    phase = _phase()
    base = {
        "enabled": enabled,
        "phase": phase,
        "decisions_24h": 0,
        "closed_24h": 0,
        "decision_counts": {"LONG": 0, "SHORT_OR_AVOID": 0, "NO_TRADE": 0},
        "wins": {"bull": 0, "bear": 0, "neutral": 0},
        "avg_edge_gap": 0.0,
        "avg_bull_confidence": 0.0,
        "avg_bear_confidence": 0.0,
        "bull_win_rate": None,  # None = not enough closed rows to compute
        "bear_win_rate": None,
        "last_decision_at": None,
        "collection": COLLECTION,
    }
    if db is None:
        return base

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        cursor = db[COLLECTION].find(
            {"created_at": {"$gte": since}},
            {"_id": 0},
        ).sort("created_at", -1)
        rows = await cursor.to_list(length=10_000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[adversarial-monitor] read failed: %s", exc)
        return base

    if not rows:
        return base

    counts = {"LONG": 0, "SHORT_OR_AVOID": 0, "NO_TRADE": 0}
    wins = {"bull": 0, "bear": 0, "neutral": 0}
    edge_gap_sum = 0.0
    bull_conf_sum = 0.0
    bear_conf_sum = 0.0
    closed = 0

    for r in rows:
        d = (r.get("decision") or "").upper()
        if d in counts:
            counts[d] += 1
        edge_gap_sum += float(r.get("edge_gap") or 0.0)
        bull_conf_sum += float(((r.get("bull_case") or {}).get("confidence") or 0.0))
        bear_conf_sum += float(((r.get("bear_case") or {}).get("confidence") or 0.0))
        winner = r.get("winner")
        if winner in wins:
            wins[winner] += 1
            closed += 1

    n = len(rows)
    # Win rates — only meaningful once we have closed rows. Computed
    # per-side against the total closed rows so bull_wr + bear_wr ≤ 1
    # (neutral eats the difference when both agreed with the outcome).
    bull_wr = round(wins["bull"] / closed, 4) if closed > 0 else None
    bear_wr = round(wins["bear"] / closed, 4) if closed > 0 else None

    last_at = rows[0].get("created_at")
    if isinstance(last_at, datetime):
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        last_at_iso = last_at.isoformat()
    else:
        last_at_iso = None

    return {
        "enabled": enabled,
        "phase": phase,
        "decisions_24h": n,
        "closed_24h": closed,
        "decision_counts": counts,
        "wins": wins,
        "avg_edge_gap": round(edge_gap_sum / n, 4),
        "avg_bull_confidence": round(bull_conf_sum / n, 4),
        "avg_bear_confidence": round(bear_conf_sum / n, 4),
        "bull_win_rate": bull_wr,
        "bear_win_rate": bear_wr,
        "last_decision_at": last_at_iso,
        "collection": COLLECTION,
    }
