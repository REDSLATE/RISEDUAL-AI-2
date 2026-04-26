"""Crypto Trading Routes — manual triggers + dashboard.

All endpoints sit on ``/api/crypto`` and write/read ONLY:
    * crypto_paper_trades
    * crypto_signal_audit_log
    * crypto_model_adaptations
    * crypto_trade_memory

The admin gate (`_is_admin`) is enforced on every WRITE / cron-trigger
endpoint so random users can't spam the bot's fill stream. Read-only
endpoints (``/dashboard``, ``/tier3-contribution``,
``/strategist-stats``) require auth but don't require admin.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from services.auth_helpers import get_current_user
from services.crypto_paper_trader import run_crypto_paper_bot
from services.crypto_quotes import get_crypto_quote, get_crypto_history
from services.crypto_closer import close_expired_crypto_trades
from services.crypto_adaptation_service import detect_crypto_adaptations
from services.crypto_signal_audit import get_strategist_stats

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/crypto", tags=["crypto-trading"])

# Module-level db handle — set by route_registry on startup.
_db: Any = None


def set_db(database: Any) -> None:
    global _db
    _db = database


def _is_admin(user: dict) -> bool:
    role = (user.get("role") or "").lower()
    return role in ("admin", "owner")


def _require_db() -> None:
    if _db is None:
        raise HTTPException(status_code=503, detail="Database not initialised")


# ── Manual bot triggers (admin) ───────────────────────────────────────────────


@router.post("/paper-bot/run")
async def run_crypto_bot_once(
    request: Request,
    symbols: str = "BTC,ETH,SOL",
) -> dict:
    """Run one pass of the crypto paper bot."""
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")

    universe = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not universe:
        raise HTTPException(
            status_code=400,
            detail="At least one symbol required (e.g. ?symbols=BTC,ETH)",
        )

    result = await run_crypto_paper_bot(
        db=_db,
        quote_provider=get_crypto_quote,
        history_provider=get_crypto_history,
        symbols=universe,
    )
    return {"status": "ok", **result}


@router.post("/paper-trades/close")
async def close_crypto_trades_once(request: Request) -> dict:
    """Manual trigger for the crypto closer (default 12h hold)."""
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await close_expired_crypto_trades(
        db=_db, quote_provider=get_crypto_quote, hold_hours=12,
    )
    return {"status": "ok", **result}


@router.post("/adaptations/detect")
async def detect_crypto_adaptation_rules(request: Request) -> dict:
    """Manual trigger for the adaptation detector."""
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")

    created = await detect_crypto_adaptations(_db)
    return {"status": "ok", "created": len(created), "adaptations": created}


# ── Read-only history surface (any authed user) ───────────────────────────────


@router.get("/paper-trades")
async def get_crypto_paper_history(
    request: Request,
    symbol: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Most recent crypto paper-trade fills."""
    _require_db()
    await get_current_user(request)
    query: dict[str, Any] = {}
    if symbol:
        query["symbol"] = symbol.strip().upper()
    rows = await _db.crypto_paper_trades.find(
        query, {"_id": 0},
    ).sort("opened_at", -1).limit(max(1, min(int(limit), 500))).to_list(
        length=limit,
    )
    # Normalise datetimes for JSON
    from datetime import datetime as _dt
    for r in rows:
        for k in ("opened_at", "closed_at"):
            v = r.get(k)
            if isinstance(v, _dt):
                r[k] = v.isoformat()
    return {"trades": rows, "count": len(rows)}


# ── Dashboard tile (any authed user) ──────────────────────────────────────────


@router.get("/dashboard")
async def crypto_dashboard(request: Request) -> dict:
    """Aggregate stats for the Crypto Paper Bots tile.

    Returns open/closed counts, total PnL, average R-multiple, win
    rate, active adaptations, and the 10 most recent fills."""
    _require_db()
    await get_current_user(request)

    open_count = await _db.crypto_paper_trades.count_documents({"status": "open"})
    closed_count = await _db.crypto_paper_trades.count_documents({"status": "closed"})

    pipeline = [
        {"$match": {"status": "closed"}},
        {
            "$group": {
                "_id": None,
                "total_pnl": {"$sum": "$pnl"},
                "avg_r": {"$avg": "$r_multiple"},
                "wins": {"$sum": {"$cond": [{"$gt": ["$r_multiple", 0]}, 1, 0]}},
                "losses": {"$sum": {"$cond": [{"$lte": ["$r_multiple", 0]}, 1, 0]}},
            }
        },
    ]
    agg = await _db.crypto_paper_trades.aggregate(pipeline).to_list(length=1)
    stats = agg[0] if agg else {"total_pnl": 0, "avg_r": 0, "wins": 0, "losses": 0}

    total_closed = int(stats.get("wins", 0)) + int(stats.get("losses", 0))
    win_rate = (stats.get("wins", 0) / total_closed) if total_closed else 0

    adaptations = await _db.crypto_model_adaptations.find(
        {"active": True}, {"_id": 0},
    ).to_list(length=20)

    recent = await _db.crypto_paper_trades.find(
        {}, {"_id": 0},
    ).sort("opened_at", -1).limit(10).to_list(length=10)

    # Normalise datetimes
    from datetime import datetime as _dt
    for r in recent:
        for k in ("opened_at", "closed_at"):
            v = r.get(k)
            if isinstance(v, _dt):
                r[k] = v.isoformat()
    for a in adaptations:
        for k in ("created_at", "expires_at"):
            v = a.get(k)
            if isinstance(v, _dt):
                a[k] = v.isoformat()

    return {
        "status": "ok",
        "open_trades": open_count,
        "closed_trades": closed_count,
        "total_pnl": round(float(stats.get("total_pnl", 0) or 0), 2),
        "avg_r": round(float(stats.get("avg_r", 0) or 0), 4),
        "win_rate": round(win_rate, 4),
        "active_adaptations": adaptations,
        "recent_trades": recent,
    }


@router.get("/tier3-contribution")
async def crypto_tier3_contribution(request: Request) -> dict:
    """Crypto-only paper-volume summary.

    Distinct ``opened_day`` counts cumulative bot activity. Note
    that crypto is isolated from the equity Tier 3 unlock unless an
    explicit gate decision includes it."""
    _require_db()
    await get_current_user(request)

    distinct_days = await _db.crypto_paper_trades.distinct(
        "opened_day", {"source": "crypto_paper_bot"},
    )
    total = await _db.crypto_paper_trades.count_documents(
        {"source": "crypto_paper_bot"},
    )
    closed = await _db.crypto_paper_trades.count_documents(
        {"source": "crypto_paper_bot", "status": "closed"},
    )

    return {
        "asset_class": "crypto",
        "paper_days": len(distinct_days),
        "total_trades": total,
        "closed_trades": closed,
        "note": (
            "Crypto is isolated. Include in Tier 3 only if a "
            "crypto-specific gate allows it."
        ),
    }


# ── Veto-rate calibration tile (admin) ────────────────────────────────────────


@router.get("/strategist-stats")
async def strategist_stats(
    request: Request,
    hours: int = 24,
    symbol: Optional[str] = None,
) -> dict:
    """Strategist→Auditor agreement rate over a rolling window.

    Use to monitor calibration. Target band: ``veto_rate ∈ [20%, 45%]``.
    """
    _require_db()
    user = await get_current_user(request)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    return await get_strategist_stats(_db, hours=hours, symbol=symbol)
