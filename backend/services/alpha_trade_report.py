"""Alpha Resolved Trade Report.

Purpose: give the operator a single readable view of every real Alpha
trade that has closed with fill economics attached. Every field is a
UNIQUE resolved setup (dedup enforced at setup-creation time, so
repeated observations of one market move never produce two rows).

Sources
-------
* ``alpha_outcomes`` (Mongo) — one doc per resolved setup, includes
  regime, buckets, edge_state at entry, and the fill economics that
  ``alpha_fill_writer`` stamps on close.
* SQLite hot store — latency samples per phase (signal_to_trigger,
  trigger_to_intent, intent_to_broker, broker_to_fill).

Never duplicates the full lifecycle into Mongo. Reads latency from
SQLite on demand for the compact per-row report.

Edge Agreement
--------------
For each resolved trade we record whether the Edge Engine's classification
at entry time turned out to match the trade result:

    edge_state       realized_r      agreement
    POSITIVE         > 0             AGREE
    POSITIVE         <= 0            DISAGREE
    NEGATIVE         < 0             AGREE
    NEGATIVE         >= 0            DISAGREE
    FLAT / DISCOVERING               N/A

This is *observation only* — nothing gates on this. It just lets us
measure whether the regime-conditioned edge survives real execution.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _edge_agreement(edge_state: Optional[str], realized_r: Optional[float]) -> str:
    if edge_state is None or realized_r is None:
        return "N/A"
    if edge_state == "POSITIVE":
        return "AGREE" if realized_r > 0 else "DISAGREE"
    if edge_state == "NEGATIVE":
        return "AGREE" if realized_r < 0 else "DISAGREE"
    return "N/A"


def _latency_summary(setup_id: str) -> dict:
    """Return per-phase latency samples from SQLite hot store.

    Returns a dict of phase → {min, max, median} in ms. Missing phases
    are absent from the returned dict (no fake zeros).
    """
    try:
        from services import alpha_hot_store
        samples = alpha_hot_store.latency_samples(setup_id)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    for phase, values in samples.items():
        if not values:
            continue
        out[phase] = {
            "min_ms": int(min(values)),
            "max_ms": int(max(values)),
            "median_ms": int(statistics.median(values)),
            "samples": len(values),
        }
    return out


def _serialize(row: dict) -> dict:
    row = dict(row)
    row.pop("_id", None)
    for k in ("created_at", "resolved_at"):
        if isinstance(row.get(k), datetime):
            row[k] = row[k].isoformat()

    setup_id = str(row.get("setup_id") or "")
    edge_state = row.get("edge_state")
    realized_r = row.get("realized_r")
    row["edge_agreement"] = _edge_agreement(edge_state, realized_r)
    row["latency_ms"] = _latency_summary(setup_id)
    # Convenience: intent → broker single-value for the panel header.
    itb = row["latency_ms"].get("intent_to_broker") or {}
    row["intent_to_broker_ms"] = itb.get("median_ms")
    return row


async def resolved_report(
    db: Any,
    *,
    limit: int = 50,
    only_measured: bool = True,
) -> dict:
    """Return the most recent resolved Alpha trades with fill economics.

    ``only_measured=True`` (default) filters to rows that have
    ``realized_r`` — i.e. the trade actually closed through the broker
    and the fill writer computed real economics. Set to False to also
    surface intent-only outcomes (not-executed / rejected paths).
    """
    if db is None:
        return {"trades": [], "totals": {}, "since": None}

    query: dict = {}
    if only_measured:
        query["realized_r"] = {"$exists": True, "$ne": None}

    cursor = db.alpha_outcomes.find(query, {"_id": 0}).sort("resolved_at", -1).limit(int(limit))
    trades: list[dict] = []
    async for row in cursor:
        trades.append(_serialize(row))

    # Roll up small totals for the report header. Only measured rows
    # contribute — a fill-less row would poison the P&L totals.
    measured = [t for t in trades if isinstance(t.get("realized_r"), (int, float))]
    total_pnl = sum(float(t.get("realized_pnl_usd") or 0.0) for t in measured)
    win_r = [t["realized_r"] for t in measured if t["realized_r"] > 0]
    loss_r = [t["realized_r"] for t in measured if t["realized_r"] < 0]
    agree = sum(1 for t in measured if t.get("edge_agreement") == "AGREE")
    disagree = sum(1 for t in measured if t.get("edge_agreement") == "DISAGREE")
    totals = {
        "count": len(measured),
        "wins": len(win_r),
        "losses": len(loss_r),
        "win_rate": round(len(win_r) / len(measured), 3) if measured else None,
        "gross_pnl_usd": round(total_pnl, 2),
        "avg_realized_r": round(statistics.mean([t["realized_r"] for t in measured]), 3) if measured else None,
        "median_entry_slippage_bps": round(
            statistics.median([t["entry_slippage_bps"] for t in measured
                                 if isinstance(t.get("entry_slippage_bps"), (int, float))]),
            2,
        ) if any(isinstance(t.get("entry_slippage_bps"), (int, float)) for t in measured) else None,
        "edge_agreement_ratio": (
            round(agree / (agree + disagree), 3) if (agree + disagree) > 0 else None
        ),
    }
    return {"trades": trades, "totals": totals,
            "generated_at": datetime.now().isoformat()}


__all__ = ["resolved_report"]
