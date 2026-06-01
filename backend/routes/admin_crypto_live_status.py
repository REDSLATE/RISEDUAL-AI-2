"""Admin route — Crypto live trading status (2026-06-01).

Read-only owner endpoint surfacing the EXACT live-crypto state of
the running pod:

* Master switches and notional / cap configuration in effect.
* Open live positions count vs cap.
* Daily live trade count vs cap.
* List of open positions with their SL/TP order IDs so the
  operator can cross-reference Kraken's UI without queries.
* Recent closed live trades for at-a-glance P&L review.

Purpose: provide a single JSON read that answers "are we live,
what's live, and what's the state of the bracket orders?" without
needing to query Mongo by hand or open the Kraken UI.

Doctrine pins
-------------
* **Owner-only.** Same auth shape as ``admin_runtime_stamp.py``.
* **Read-only.** No mutations, no Kraken writes, safe to poll.
* **Token values masked.** Never echo Kraken keys.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/admin/crypto-live",
    tags=["admin-crypto-live"],
)

db: Any = None


def set_db(database) -> None:
    global db
    db = database


async def _require_owner(request: Request):
    from routes.auth import get_current_user
    user = await get_current_user(request)
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


def _live_exec_enabled() -> bool:
    return os.environ.get("RISEDUAL_CRYPTO_LIVE_EXEC", "").strip() in (
        "1", "true", "True", "yes", "on",
    )


@router.get("/status")
async def crypto_live_status(request: Request) -> dict[str, Any]:
    """Single-shot live-crypto state read.

    Response shape::

        {
          "armed": bool,
          "config": {
            "notional_per_trade_usd": 25.0,
            "stop_loss_pct": 0.03,
            "take_profit_pct": 0.04,
            "symbol_allowlist": ["BTC", "ETH"],
            "max_open_positions": 3,
            "max_daily_trades": 10,
            "kraken_keys_set": bool,
          },
          "open_positions": {
            "count": int,
            "cap": int,
            "rows": [
              {
                "symbol": "BTC", "direction": "LONG",
                "entry_price": ..., "size": ..., "size_usd": ...,
                "kraken_order_id": ..., "stop_loss_order_id": ...,
                "stop_loss_price": ..., "stop_loss_placed": bool,
                "take_profit_order_id": ..., "take_profit_price": ...,
                "take_profit_placed": bool, "opened_at": iso,
              },
              ...
            ]
          },
          "daily_trades": {"count": int, "cap": int},
          "recent_closed": [...],
        }
    """
    await _require_owner(request)

    from services.crypto_live_executor import (
        HARD_STOP_LOSS_PCT,
        HARD_TAKE_PROFIT_PCT,
        LIVE_SYMBOL_ALLOWLIST,
        MAX_DAILY_LIVE_TRADES,
        MAX_OPEN_LIVE_POSITIONS,
        _resolve_notional_usd,
    )

    cfg = {
        "notional_per_trade_usd": _resolve_notional_usd(),
        "stop_loss_pct": HARD_STOP_LOSS_PCT,
        "take_profit_pct": HARD_TAKE_PROFIT_PCT,
        "symbol_allowlist": sorted(LIVE_SYMBOL_ALLOWLIST),
        "max_open_positions": MAX_OPEN_LIVE_POSITIONS,
        "max_daily_trades": MAX_DAILY_LIVE_TRADES,
        "kraken_keys_set": bool(
            os.environ.get("KRAKEN_API_KEY")
            and os.environ.get("KRAKEN_API_SECRET")
        ),
    }

    if db is None:
        return {
            "armed": _live_exec_enabled(),
            "config": cfg,
            "open_positions": {"count": 0, "cap": MAX_OPEN_LIVE_POSITIONS, "rows": []},
            "daily_trades": {"count": 0, "cap": MAX_DAILY_LIVE_TRADES},
            "recent_closed": [],
            "note": "db not set",
        }

    # Open positions — projection drops noisy fields, keeps the
    # operator-relevant identifiers.
    open_rows: list[dict[str, Any]] = []
    try:
        cursor = db.crypto_live_trades.find(
            {"status": "open"},
            {
                "_id": 0,
                "symbol": 1, "direction": 1,
                "entry_price": 1, "size": 1, "size_usd": 1,
                "live_notional_usd": 1,
                "kraken_order_id": 1, "kraken_pair": 1,
                "stop_loss_order_id": 1, "stop_loss_price": 1,
                "stop_loss_placed": 1, "stop_loss_pct": 1,
                "take_profit_order_id": 1, "take_profit_price": 1,
                "take_profit_placed": 1, "take_profit_pct": 1,
                "opened_at": 1, "confidence": 1,
            },
        ).sort("opened_at", -1).limit(MAX_OPEN_LIVE_POSITIONS * 2)
        async for row in cursor:
            if isinstance(row.get("opened_at"), datetime):
                row["opened_at"] = row["opened_at"].isoformat()
            open_rows.append(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto-live-status] open-rows read failed: %s", exc)

    # Daily count over the last 24h (calendar window, matches the
    # eligibility-check window in :func:`crypto_live_executor.is_eligible_for_live`).
    cutoff_24h = (
        datetime.now(timezone.utc) - timedelta(hours=24)
    ).replace(tzinfo=None)
    daily_count = 0
    try:
        daily_count = await db.crypto_live_trades.count_documents(
            {"opened_at": {"$gte": cutoff_24h}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto-live-status] daily-count read failed: %s", exc)

    # Last 10 closed live trades — for at-a-glance P&L review.
    recent_closed: list[dict[str, Any]] = []
    try:
        cursor = db.crypto_live_trades.find(
            {"status": "closed"},
            {
                "_id": 0,
                "symbol": 1, "direction": 1,
                "entry_price": 1, "exit_price": 1,
                "closed_reason": 1,
                "pnl_pct": 1, "pnl_usd": 1,
                "opened_at": 1, "closed_at": 1,
            },
        ).sort("closed_at", -1).limit(10)
        async for row in cursor:
            for k in ("opened_at", "closed_at"):
                if isinstance(row.get(k), datetime):
                    row[k] = row[k].isoformat()
            recent_closed.append(row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto-live-status] recent-closed read failed: %s", exc)

    return {
        "armed": _live_exec_enabled(),
        "config": cfg,
        "open_positions": {
            "count": len(open_rows),
            "cap": MAX_OPEN_LIVE_POSITIONS,
            "rows": open_rows,
        },
        "daily_trades": {
            "count": daily_count,
            "cap": MAX_DAILY_LIVE_TRADES,
        },
        "recent_closed": recent_closed,
    }


__all__ = ["router", "set_db"]
