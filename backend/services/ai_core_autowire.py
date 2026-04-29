"""Auto-wire AI Core ingestion from existing data sources.

Pulls resolved trades from `paper_trades` + verified `predictions`
into the AI Core LearningEngine without coupling those services to
the engine. Each trade gets a deterministic ``trade_key`` so re-runs
of the sweep ingest only the new resolutions.
"""
from __future__ import annotations

__domain__ = "PRD"

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from services.ai_core_engine import learning_engine
from services.prediction_tracker import normalize_confidence

logger = logging.getLogger(__name__)

# How far back the sweep looks when first run after a backend restart
# (ai_core_trades is the source of truth for what's already ingested
# via the unique index on trade_key — but a sane window keeps Mongo
# scans cheap on the very first run).
_LOOKBACK_DAYS = 30


async def _classify_asset(symbol: str) -> str:
    s = (symbol or "").upper()
    if "/" in s or s.endswith("USD") and len(s) <= 8:
        return "crypto"
    return "equity"


async def _ingest_paper_trades(db: Any, since_iso: str) -> int:
    if db is None:
        return 0
    cursor = db["paper_trades"].find(
        {
            "status": "closed",
            "outcome": {"$in": ["win", "loss", "flat"]},
            "closed_at": {"$exists": True},
        },
        {"_id": 0},
    ).limit(500)
    count = 0
    async for t in cursor:
        # paper_trades.closed_at is a datetime; normalise to iso
        closed_at = t.get("closed_at")
        if isinstance(closed_at, datetime):
            closed_iso = closed_at.replace(tzinfo=closed_at.tzinfo or timezone.utc).isoformat()
        else:
            closed_iso = str(closed_at) if closed_at else None
        if closed_iso and closed_iso < since_iso:
            continue
        symbol = (t.get("ticker") or "").upper()
        res = await learning_engine.record_trade({
            "source": "paper_trades",
            "source_id": t.get("trade_id") or "",
            "symbol": symbol,
            "direction": t.get("direction"),
            "confidence": normalize_confidence(t.get("confidence")),
            "regime": t.get("regime"),
            "agent": "paper_trader",
            "asset_type": await _classify_asset(symbol),
            "entry_price": t.get("entry_price"),
            "exit_price": t.get("exit_price"),
            "pnl_usd": t.get("pnl_usd"),
            "pnl_pct": t.get("pnl_pct"),
            "outcome": t.get("outcome"),
            "recorded_at": closed_iso,
        })
        if res.get("ok") and not res.get("dedup"):
            count += 1
    return count


async def _ingest_predictions(db: Any, since_iso: str) -> int:
    if db is None:
        return 0
    cursor = db["predictions"].find(
        {
            "verified_24h": {"$ne": None},
            "verified_24h.correct": {"$in": [True, False]},
            "verified_24h.verified_at": {"$gte": since_iso},
        },
        {"_id": 0},
    ).limit(500)
    count = 0
    async for p in cursor:
        v24 = p.get("verified_24h") or {}
        correct = v24.get("correct")
        outcome = "win" if correct is True else "loss" if correct is False else "flat"
        symbol = (p.get("symbol") or "").upper()
        res = await learning_engine.record_trade({
            "source": "predictions",
            "source_id": p.get("prediction_id") or "",
            "symbol": symbol,
            "direction": p.get("direction"),
            "confidence": normalize_confidence(p.get("confidence")),
            "regime": None,  # predictions don't carry regime today
            "agent": p.get("feature") or "unknown",
            "asset_type": await _classify_asset(symbol),
            "entry_price": p.get("price_at_prediction"),
            "exit_price": v24.get("price"),
            "outcome": outcome,
            "recorded_at": v24.get("verified_at"),
        })
        if res.get("ok") and not res.get("dedup"):
            count += 1
    return count


async def autowire_sweep(db: Any) -> dict:
    """Run one auto-wire pass. Returns counts per source."""
    if db is None:
        return {"ok": False, "reason": "db_unavailable"}
    since = (datetime.now(timezone.utc) - timedelta(days=_LOOKBACK_DAYS)).isoformat()
    paper_n = await _ingest_paper_trades(db, since)
    pred_n = await _ingest_predictions(db, since)
    logger.info(
        "[ai_core_autowire] swept paper_trades=%d predictions=%d",
        paper_n, pred_n,
    )
    return {
        "ok": True,
        "ingested": {"paper_trades": paper_n, "predictions": pred_n},
        "since": since,
    }
