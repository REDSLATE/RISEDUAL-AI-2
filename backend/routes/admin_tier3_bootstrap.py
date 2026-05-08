"""Tier-3 universe bootstrap admin route + signal-bot constants.

Extracted from ``routes/admin.py`` to keep the admin surface
domain-modular. URL unchanged — operators still hit
``POST /api/admin/bots/seed-tier3-universe``.

Re-exports the universe constants ``_TIER3_NEW_TICKERS`` and
``_TIER3_DAILY_CAP`` so existing imports in ``server.py`` resolve
without code edits.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/api/admin", tags=["admin-tier3-bootstrap"])
logger = logging.getLogger(__name__)

db = None  # noqa: E305 — module-level handle, set by route_registry.wire_db


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


# ── Tier 3 Universe ─────────────────────────────────────────────


# Universe expanded 2026-05-02. The original 5-bot setup
# (SPY, QQQ, AAPL, MSFT, NVDA) saturated its `max_trades_per_day`
# cap every day, throttling the ML pipeline's learning rate. The
# 15 new tickers below are picked across sectors so ensemble
# disagreement (the adversarial layer's primary suppressor)
# happens less often — broader coverage = more independent signals
# = more ML training samples per day.
_TIER3_NEW_TICKERS: tuple[str, ...] = (
    # High-vol mega-cap tech (different beta from existing AAPL/MSFT/NVDA)
    "GOOGL", "AMZN", "META", "TSLA", "AMD", "AVGO", "NFLX",
    # Momentum / high-vol non-mag-7
    "PLTR", "COIN", "SMCI",
    # Financials (rate-sensitive; different macro driver)
    "JPM", "BAC",
    # Energy (oil-price driven)
    "XOM",
    # Defensives (consumer staples + healthcare)
    "WMT", "UNH",
)


_TIER3_DAILY_CAP: int = 10  # was 5 — bumped to clear the saturation


@router.post("/bots/seed-tier3-universe")
async def seed_tier3_universe(request: Request):
    """One-shot: add the 15 new Tier-3 Accumulator signal bots and
    bump per-bot daily caps to 10.

    Idempotent — re-running is safe:
      * Bots that already exist (matched by name) are skipped, never
        duplicated.
      * Daily-cap bump runs on every Tier-3 Accumulator bot (existing
        and newly created) so the cap stays consistent.

    Returns a summary of what changed so the operator can audit.
    """
    user = await _require_owner(request)
    owner_id = user["_id"] if isinstance(user["_id"], str) else str(user["_id"])

    base_config = {
        "min_confidence": 70,
        "strategies": [],
        "side": "both",
        "qty": 1,
        "use_smart_order": True,
        "auto_sl_pct": 3,
        "auto_tp_pct": 6,
        "max_trades_per_day": _TIER3_DAILY_CAP,
        "trades_today": 0,
    }
    base_stats = {"trades": 0, "pnl": 0, "signals_received": 0, "signals_executed": 0}

    created: list[str] = []
    skipped: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for ticker in _TIER3_NEW_TICKERS:
        name = f"Tier3 Accumulator · {ticker}"
        existing = await db.trading_bots.find_one({"name": name}, {"_id": 1})
        if existing:
            skipped.append(ticker)
            continue
        await db.trading_bots.insert_one({
            "_id": str(uuid4()).replace("-", ""),
            "user_id": owner_id,
            "type": "signal",
            "name": name,
            "enabled": True,
            "mode": "paper",
            "config": {**base_config, "symbols": [ticker]},
            "stats": dict(base_stats),
            "created_at": now,
            "updated_at": now,
            "last_run": None,
        })
        created.append(ticker)

    # Bump the daily cap on every Tier-3 Accumulator bot. Resets
    # ``trades_today`` to 0 so the cap takes effect immediately
    # without waiting for the per-bot midnight rollover.
    cap_update = await db.trading_bots.update_many(
        {"name": {"$regex": "^Tier3 Accumulator · "}},
        {
            "$set": {
                "config.max_trades_per_day": _TIER3_DAILY_CAP,
                "config.trades_today": 0,
                "updated_at": now,
            }
        },
    )

    total_signal_bots = await db.trading_bots.count_documents(
        {"type": "signal", "enabled": True}
    )

    return {
        "created": created,
        "skipped_already_existed": skipped,
        "daily_cap_now": _TIER3_DAILY_CAP,
        "bots_with_cap_bumped": cap_update.modified_count,
        "total_enabled_signal_bots": total_signal_bots,
        "as_of": now,
    }
