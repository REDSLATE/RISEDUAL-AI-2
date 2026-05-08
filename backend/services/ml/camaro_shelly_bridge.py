"""Camaro → Shelly bridge.

Reads closed day-trade rows (Camaro = the day_trade_scanner that
populates ``paper_trades`` + ``crypto_paper_trades`` with
``source="day_trade_scanner"``) and persists them into Shelly's
ChromaDB market memory tagged ``source="camaro"``.

Why source labels matter
────────────────────────
The toxic-spike scanner tracks per-source organic vs backfill ratios.
Camaro day-trade outcomes are a third class — neither organic ML
pipeline output nor historical backfill, but a parallel signal source
the operator wants Shelly to learn from.

Idempotent
──────────
Each closed day-trade row carries a ``trade_id`` which we hash into
the regime ``prediction_id``. Re-runs of the bridge are no-ops on
already-ingested rows.

Schedule (suggested):
  * Run every 30 minutes during the equity session
  * Run hourly off-session for crypto
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_BRIDGE_AUDIT_COLLECTION = "camaro_shelly_bridge_log"
_DEFAULT_LOOKBACK_HOURS = 24


def _classify_outcome(trade: Dict[str, Any]) -> str:
    """Map a closed paper-trade to a Shelly outcome label.

    ``positive`` when realised P&L > 0, ``negative`` when < 0,
    ``neutral`` when ≈ 0 or undeterminable.
    """
    pnl = trade.get("realized_pnl_usd") or trade.get("pnl_usd") or trade.get("pnl")
    if pnl is None:
        # Fall back to entry vs close price if available.
        entry = trade.get("entry_price") or trade.get("entry") or 0.0
        close = trade.get("close_price") or trade.get("exit_price") or 0.0
        side = (trade.get("side") or "BUY").upper()
        if entry > 0 and close > 0:
            delta = close - entry
            if side == "SELL" or side == "SHORT":
                delta = -delta
            if abs(delta) / entry < 0.001:
                return "neutral"
            return "positive" if delta > 0 else "negative"
        return "neutral"
    try:
        v = float(pnl)
    except (TypeError, ValueError):
        return "neutral"
    if abs(v) < 0.01:
        return "neutral"
    return "positive" if v > 0 else "negative"


def _build_regime_payload(
    trade: Dict[str, Any], *, lane: str,
) -> Dict[str, Any]:
    """Translate a closed paper-trade into the shape
    :func:`services.market_memory_service.save_regime` expects.
    """
    symbol = trade.get("symbol") or trade.get("ticker") or "UNKNOWN"
    outcome = _classify_outcome(trade)
    pnl = trade.get("realized_pnl_usd") or trade.get("pnl_usd") or trade.get("pnl") or 0.0
    side = (trade.get("side") or trade.get("direction") or "BUY").upper()
    closed_at = trade.get("closed_at") or trade.get("close_time") or datetime.now(timezone.utc)

    return {
        "symbol": symbol,
        "lane": lane,
        "side": side,
        "outcome": outcome,
        "pnl_usd": float(pnl) if isinstance(pnl, (int, float)) else 0.0,
        "close_reason": trade.get("close_reason"),
        "max_hold_until": str(trade.get("max_hold_until") or ""),
        # Source label (the canonical rule the user asked for).
        "source": "camaro",
        "schema_version": 1,
        # Stable id so re-runs are idempotent.
        "prediction_id": f"camaro::{trade.get('trade_id') or symbol}::{closed_at}",
        "first_seen_at": (
            closed_at.isoformat() if hasattr(closed_at, "isoformat") else str(closed_at)
        ),
    }


async def ingest_one_lane(
    db,
    *,
    collection: str,
    lane: str,
    lookback_hours: int = _DEFAULT_LOOKBACK_HOURS,
) -> Dict[str, int]:
    """Ingest closed day-trade rows from a single lane collection.

    NEVER raises. Returns counts keyed by outcome.
    """
    counts = {"seen": 0, "ingested": 0, "skipped": 0, "errors": 0,
              "positive": 0, "negative": 0, "neutral": 0}
    if db is None:
        return counts

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    try:
        cursor = db[collection].find(
            {
                "source": "day_trade_scanner",
                "status": "closed",
                "closed_at": {"$gte": cutoff},
            },
            projection={"_id": 0},
        ).limit(1000)
        rows: List[Dict[str, Any]] = await cursor.to_list(length=1000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[camaro_bridge] %s read failed: %s", collection, exc)
        counts["errors"] += 1
        return counts

    counts["seen"] = len(rows)

    try:
        from services import market_memory_service as mms
    except Exception as exc:  # noqa: BLE001
        logger.warning("[camaro_bridge] market_memory_service unavailable: %s", exc)
        counts["errors"] += 1
        return counts

    for trade in rows:
        try:
            payload = _build_regime_payload(trade, lane=lane)
            outcome = payload["outcome"]
            counts[outcome] = counts.get(outcome, 0) + 1

            saver = getattr(mms, "save_regime", None)
            if not callable(saver):
                counts["errors"] += 1
                continue

            res = saver(payload)
            if hasattr(res, "__await__"):
                await res
            counts["ingested"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("[camaro_bridge] save_regime failed: %s", exc)
            counts["errors"] += 1

    # Audit row so the admin endpoint can show last-run state.
    try:
        await db[_BRIDGE_AUDIT_COLLECTION].insert_one({
            "ran_at": datetime.now(timezone.utc),
            "collection": collection,
            "lane": lane,
            "lookback_hours": lookback_hours,
            "counts": counts,
            "schema_version": 1,
        })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[camaro_bridge] audit insert failed: %s", exc)

    return counts


async def run_bridge(
    db,
    *,
    lookback_hours: int = _DEFAULT_LOOKBACK_HOURS,
) -> Dict[str, Any]:
    """Run the bridge for both equity and crypto Camaro outputs.
    NEVER raises."""
    eq = await ingest_one_lane(
        db, collection="paper_trades", lane="equity",
        lookback_hours=lookback_hours,
    )
    cr = await ingest_one_lane(
        db, collection="crypto_paper_trades", lane="crypto",
        lookback_hours=lookback_hours,
    )
    return {
        "equity": eq,
        "crypto": cr,
        "lookback_hours": lookback_hours,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_bridge_status(db, *, limit: int = 10) -> Dict[str, Any]:
    """Read-only — last N bridge runs from the audit collection."""
    if db is None:
        return {"runs": [], "count": 0}
    try:
        cursor = db[_BRIDGE_AUDIT_COLLECTION].find(
            {}, projection={"_id": 0},
        ).sort("ran_at", -1).limit(limit)
        runs = []
        async for doc in cursor:
            ra = doc.get("ran_at")
            if hasattr(ra, "isoformat"):
                doc["ran_at"] = ra.isoformat()
            runs.append(doc)
        return {"runs": runs, "count": len(runs)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[camaro_bridge] status read failed: %s", exc)
        return {"runs": [], "count": 0, "error": str(exc)}


async def ensure_indexes(db) -> None:
    if db is None:
        return
    try:
        await db[_BRIDGE_AUDIT_COLLECTION].create_index(
            [("ran_at", -1)], name="camaro_bridge_audit_ts",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[camaro_bridge] ensure_indexes failed: %s", exc)
