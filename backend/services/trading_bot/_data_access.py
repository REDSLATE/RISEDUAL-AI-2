"""Data-access helpers for the trading-bot service.

Step 4C extraction (2026-05-09). Verbatim moves of the bot-CRUD
helpers from ``services/trading_bot_service.py``. Pure data access
only — no decision logic, no broker calls, no sizing, no telemetry
mutation. Every Mongo query, update shape, and returned document
shape is byte-equivalent to the pre-split implementation.

Functions moved:
  * ``create_bot``        — insert new bot doc (defaults OFF)
  * ``_build_config``     — per-bot-type config validator/builder
  * ``toggle_bot``        — flip ``enabled`` flag
  * ``update_bot_config`` — merge config patch onto existing doc
  * ``delete_bot``        — delete by (user_id, bot_id)
  * ``get_user_bots``     — list user's bots (with bot_id re-attached)

DB resolution
-------------
Each helper calls ``_resolve_db_for_shadow()`` at the top to fetch
the live ``_db`` from ``trading_bot_service``. Deferred (function-
local) import sidesteps the circular dependency. This is the same
pattern already used by ``_telemetry.fire_equity_shadow`` (4B).

The getter call introduces no behaviour change — it just returns
the same module-level handle the inline ``_db.X`` calls used to
read directly. Mongo queries, projections, sort orders, and return
shapes are unchanged.
"""
import secrets
from datetime import datetime, timezone
from typing import Any

from services.trading_bot._constants import BOT_TYPES


def _get_db() -> Any:
    """Deferred fetch of the trading-bot DB handle.

    Function-local import prevents a circular dependency at module
    load time. The wrapper is internal-only; callers within this
    module use it; the original ``_resolve_db_for_shadow`` accessor
    remains in ``trading_bot_service`` for the telemetry helper.
    """
    from services.trading_bot_service import _resolve_db_for_shadow
    return _resolve_db_for_shadow()


async def create_bot(user_id: str, bot_data: dict) -> dict:
    """Create a new trading bot (defaults to OFF)."""
    bot_type = bot_data.get("type")
    if bot_type not in BOT_TYPES:
        return {"error": f"Invalid bot type. Must be: {', '.join(BOT_TYPES)}"}

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "user_id": user_id,
        "type": bot_type,
        "name": bot_data.get("name", f"{bot_type.title()} Bot"),
        "enabled": False,  # Always starts OFF
        "mode": bot_data.get("mode", "paper"),  # paper or live
        "config": _build_config(bot_type, bot_data.get("config", {})),
        "stats": {"trades": 0, "pnl": 0, "signals_received": 0, "signals_executed": 0},
        "created_at": now,
        "updated_at": now,
        "last_run": None,
    }

    # Generate webhook secret for webhook bots
    if bot_type == "webhook":
        doc["webhook_secret"] = secrets.token_urlsafe(24)

    db = _get_db()
    result = await db.trading_bots.insert_one(doc)
    doc.pop("_id", None)
    doc["bot_id"] = str(result.inserted_id)
    return doc


def _build_config(bot_type: str, cfg: dict) -> dict:
    """Build validated config per bot type."""
    if cfg is None:
        cfg = {}
    if bot_type == "grid":
        return {
            "symbol": cfg.get("symbol", "BTC").upper(),
            "upper_price": float(cfg.get("upper_price", 0)),
            "lower_price": float(cfg.get("lower_price", 0)),
            "grid_levels": int(cfg.get("grid_levels", 5)),
            "qty_per_grid": float(cfg.get("qty_per_grid", 0.01)),
            "active_orders": [],
        }
    elif bot_type == "signal":
        return {
            "min_confidence": int(cfg.get("min_confidence", 70)),
            "strategies": cfg.get("strategies", []),  # empty = all strategies
            "symbols": cfg.get("symbols", []),  # empty = all symbols
            "side": cfg.get("side", "both"),  # buy, sell, both
            "qty": float(cfg.get("qty", 1)),
            "use_smart_order": bool(cfg.get("use_smart_order", True)),
            "auto_sl_pct": float(cfg.get("auto_sl_pct", 3)),
            "auto_tp_pct": float(cfg.get("auto_tp_pct", 6)),
            # Safety cap: caps number of trades a bot can fire per UTC day.
            # Prevents a runaway scanner from spamming the paper book when
            # conditions line up on many scan passes in a row. 0 disables
            # the cap. See run_signal_bot_dispatcher() for enforcement.
            "max_trades_per_day": int(cfg.get("max_trades_per_day", 5)),
            "trades_today": int(cfg.get("trades_today", 0)),
            "last_trade_date": cfg.get("last_trade_date"),
        }
    elif bot_type == "webhook":
        return {
            "accepted_actions": cfg.get("accepted_actions", ["buy", "sell"]),
            "default_qty": float(cfg.get("default_qty", 1)),
            "require_symbol": bool(cfg.get("require_symbol", True)),
            "max_trades_per_day": int(cfg.get("max_trades_per_day", 10)),
            "trades_today": 0,
            "last_trade_date": None,
        }
    return {}


async def toggle_bot(user_id: str, bot_id: str, enabled: bool) -> dict:
    """Toggle bot on/off."""
    from bson import ObjectId
    db = _get_db()
    result = await db.trading_bots.update_one(
        {"_id": ObjectId(bot_id), "user_id": user_id},
        {"$set": {"enabled": enabled, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    if result.matched_count == 0:
        return {"error": "Bot not found"}
    return {"bot_id": bot_id, "enabled": enabled}


async def update_bot_config(user_id: str, bot_id: str, config: dict) -> dict:
    """Update bot configuration."""
    from bson import ObjectId
    db = _get_db()
    bot = await db.trading_bots.find_one({"_id": ObjectId(bot_id), "user_id": user_id})
    if not bot:
        return {"error": "Bot not found"}
    merged = {**bot.get("config", {}), **config}
    await db.trading_bots.update_one(
        {"_id": ObjectId(bot_id)},
        {"$set": {"config": merged, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"bot_id": bot_id, "config": merged}


async def delete_bot(user_id: str, bot_id: str) -> dict:
    """Delete a bot."""
    from bson import ObjectId
    db = _get_db()
    result = await db.trading_bots.delete_one({"_id": ObjectId(bot_id), "user_id": user_id})
    if result.deleted_count == 0:
        return {"error": "Bot not found"}
    return {"status": "deleted", "bot_id": bot_id}


async def get_user_bots(user_id: str) -> list[dict]:
    """Get all bots for a user."""
    db = _get_db()
    cursor = db.trading_bots.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1)
    bots = await cursor.to_list(length=20)
    # Re-attach bot_id from a second query
    cursor2 = db.trading_bots.find({"user_id": user_id}).sort("created_at", -1)
    raw = await cursor2.to_list(length=20)
    for i, bot in enumerate(bots):
        if i < len(raw):
            bot["bot_id"] = str(raw[i]["_id"])
    return bots
