"""Trading Bot Service — Grid Bot, Signal Bot, TradingView Webhook Bot.

All bots default to OFF. Each has an independent enabled toggle.
Grid Bot runs on the APScheduler interval. Signal Bot triggers from scanner results.
Webhook Bot receives external POST requests.
"""
import logging
import secrets
from datetime import datetime, timezone


logger = logging.getLogger(__name__)

_db = None

def set_db(database):
    global _db
    _db = database


BOT_TYPES = {"grid", "signal", "webhook"}


# ── Bot CRUD ──

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

    result = await _db.trading_bots.insert_one(doc)
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
    result = await _db.trading_bots.update_one(
        {"_id": ObjectId(bot_id), "user_id": user_id},
        {"$set": {"enabled": enabled, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    if result.matched_count == 0:
        return {"error": "Bot not found"}
    return {"bot_id": bot_id, "enabled": enabled}


async def update_bot_config(user_id: str, bot_id: str, config: dict) -> dict:
    """Update bot configuration."""
    from bson import ObjectId
    bot = await _db.trading_bots.find_one({"_id": ObjectId(bot_id), "user_id": user_id})
    if not bot:
        return {"error": "Bot not found"}
    merged = {**bot.get("config", {}), **config}
    await _db.trading_bots.update_one(
        {"_id": ObjectId(bot_id)},
        {"$set": {"config": merged, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    return {"bot_id": bot_id, "config": merged}


async def delete_bot(user_id: str, bot_id: str) -> dict:
    """Delete a bot."""
    from bson import ObjectId
    result = await _db.trading_bots.delete_one({"_id": ObjectId(bot_id), "user_id": user_id})
    if result.deleted_count == 0:
        return {"error": "Bot not found"}
    return {"status": "deleted", "bot_id": bot_id}


async def get_user_bots(user_id: str) -> list[dict]:
    """Get all bots for a user."""
    cursor = _db.trading_bots.find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1)
    bots = await cursor.to_list(length=20)
    # Re-attach bot_id from a second query
    cursor2 = _db.trading_bots.find({"user_id": user_id}).sort("created_at", -1)
    raw = await cursor2.to_list(length=20)
    for i, bot in enumerate(bots):
        if i < len(raw):
            bot["bot_id"] = str(raw[i]["_id"])
    return bots


# ── Grid Bot Engine ──

async def run_grid_bots():
    """Background: Check all enabled grid bots and place/fill orders."""
    if _db is None:
        return

    bots = await _db.trading_bots.find({"type": "grid", "enabled": True}).to_list(length=50)
    if not bots:
        return

    from services.price_provider import get_quote, get_crypto_quote
    CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "DOT", "SHIB", "LINK", "BNB"}

    now = datetime.now(timezone.utc).isoformat()
    for bot in bots:
        try:
            cfg = bot.get("config", {})
            symbol = cfg.get("symbol", "")
            if not symbol:
                continue

            q = await get_crypto_quote(symbol) if symbol in CRYPTO else await get_quote(symbol)
            if not q or not q.get("price"):
                continue
            price = float(q["price"])

            upper = cfg.get("upper_price", 0)
            lower = cfg.get("lower_price", 0)
            levels = cfg.get("grid_levels", 5)
            qty = cfg.get("qty_per_grid", 0.01)

            if upper <= lower or levels < 2:
                continue

            step = (upper - lower) / (levels - 1)
            active = cfg.get("active_orders", [])

            # Initialize grid if empty
            if not active:
                active = []
                for i in range(levels):
                    grid_price = round(lower + step * i, 6)
                    side = "buy" if grid_price < price else "sell"
                    active.append({
                        "grid_price": grid_price,
                        "side": side,
                        "qty": qty,
                        "filled": False,
                        "filled_at": None,
                    })
                cfg["active_orders"] = active

            # Check fills
            trades_made = 0
            for order in active:
                if order["filled"]:
                    continue
                if order["side"] == "buy" and price <= order["grid_price"]:
                    result = await _execute_bot_trade(bot, symbol, "buy", qty, order["grid_price"])
                    if result and result.get("error"):
                        logger.warning(f"Grid bot {bot.get('name')} buy failed: {result['error']}")
                        continue
                    order["filled"] = True
                    order["filled_at"] = now
                    order["side"] = "sell"  # Flip to sell at next grid above
                    trades_made += 1
                elif order["side"] == "sell" and price >= order["grid_price"]:
                    result = await _execute_bot_trade(bot, symbol, "sell", qty, order["grid_price"])
                    if result and result.get("error"):
                        logger.warning(f"Grid bot {bot.get('name')} sell failed: {result['error']}")
                        continue
                    order["filled"] = True
                    order["filled_at"] = now
                    order["side"] = "buy"  # Flip to buy at next grid below
                    trades_made += 1

            # Reset filled orders for continuous grid
            for order in active:
                if order["filled"]:
                    order["filled"] = False
                    order["filled_at"] = None

            if trades_made > 0:
                stats = bot.get("stats", {})
                stats["trades"] = stats.get("trades", 0) + trades_made
                await _db.trading_bots.update_one(
                    {"_id": bot["_id"]},
                    {"$set": {"config": cfg, "stats": stats, "last_run": now, "updated_at": now}}
                )
        except Exception as e:
            logger.debug(f"Grid bot error {bot.get('name')}: {e}")


# ── Signal Bot Engine ──

async def process_signal_for_bots(user_id: str, signal: dict) -> list[dict]:
    """Process a scanner signal through all enabled signal bots for a user.
    Returns list of executed trades."""
    if _db is None:
        return []

    bots = await _db.trading_bots.find({
        "user_id": user_id, "type": "signal", "enabled": True,
    }).to_list(length=10)

    results = []
    for bot in bots:
        cfg = bot.get("config", {})
        min_conf = cfg.get("min_confidence", 70)
        allowed_strategies = cfg.get("strategies", [])
        allowed_symbols = cfg.get("symbols", [])
        side_filter = cfg.get("side", "both")

        # Check filters
        confidence = signal.get("ai_confidence", 0)
        if confidence < min_conf:
            continue

        strategy = signal.get("strategy_id", "")
        if allowed_strategies and strategy not in allowed_strategies:
            continue

        symbol = signal.get("symbol", "")
        if allowed_symbols and symbol not in allowed_symbols:
            continue

        # Determine side from verdict
        verdict = signal.get("ai_verdict", "hold")
        if verdict in ("strong_buy", "buy"):
            side = "buy"
        elif verdict in ("strong_sell", "sell"):
            side = "sell"
        else:
            continue

        if side_filter != "both" and side != side_filter:
            continue

        # Per-bot daily-trade cap — zeros (or missing cap) short-circuit
        # the check entirely. The cap resets at UTC day rollover by
        # comparing `last_trade_date` to today's date string. We check
        # before execution so a busy scanner pass can't burst past the
        # cap on a single sweep.
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        last_trade_date = cfg.get("last_trade_date")
        trades_today = int(cfg.get("trades_today", 0))
        if last_trade_date != today_str:
            trades_today = 0  # new UTC day — reset counter before check
        max_per_day = int(cfg.get("max_trades_per_day", 0) or 0)
        if max_per_day > 0 and trades_today >= max_per_day:
            logger.info(
                f"[signal-bot] {bot.get('name')} skipped {symbol} — "
                f"daily cap reached ({trades_today}/{max_per_day})"
            )
            continue

        # Execute via Smart Order or direct paper trade
        now = datetime.now(timezone.utc).isoformat()
        qty = cfg.get("qty", 1)

        if cfg.get("use_smart_order"):
            from services.smart_order_service import create_smart_order
            price = signal.get("price", 0)
            sl_pct = cfg.get("auto_sl_pct", 3) / 100
            tp_pct = cfg.get("auto_tp_pct", 6) / 100
            sl_price = round(price * (1 - sl_pct), 4) if side == "buy" else round(price * (1 + sl_pct), 4)
            tp_price = round(price * (1 + tp_pct), 4) if side == "buy" else round(price * (1 - tp_pct), 4)

            order_result = await create_smart_order(user_id, {
                "symbol": symbol, "side": side, "qty": qty,
                "mode": bot.get("mode", "paper"), "order_type": "market",
                "stop_loss": {"price": sl_price, "trailing": True, "trailing_pct": cfg.get("auto_sl_pct", 3)},
                "take_profits": [{"price": tp_price, "pct_of_qty": 100}],
            })
            results.append({"bot": bot.get("name"), "symbol": symbol, "side": side, "result": "smart_order", "order": order_result})
        else:
            await _execute_bot_trade(bot, symbol, side, qty, signal.get("price", 0))
            results.append({"bot": bot.get("name"), "symbol": symbol, "side": side, "result": "paper_trade"})

        # Update stats + daily cap counters. We persist cap state on the
        # config (not stats) so it survives `update_bot_config` merges
        # and is visible in the admin bot list without an extra field.
        stats = bot.get("stats", {})
        stats["signals_received"] = stats.get("signals_received", 0) + 1
        stats["signals_executed"] = stats.get("signals_executed", 0) + 1
        stats["trades"] = stats.get("trades", 0) + 1

        new_cfg = {**cfg,
                   "trades_today": trades_today + 1,
                   "last_trade_date": today_str}
        await _db.trading_bots.update_one(
            {"_id": bot["_id"]},
            {"$set": {"stats": stats, "config": new_cfg,
                      "last_run": now, "updated_at": now}}
        )

    return results


async def run_signal_bot_dispatcher():
    """Fan scanner output into all enabled signal bots. Scheduled job.

    Runs every ~5 min via APScheduler. Aggregates the distinct symbol +
    strategy whitelist across every enabled signal bot in the system,
    runs one consolidated `scan_symbols()` pass (so we don't pay the
    technicals cost per-user), then for each strategy match builds a
    normalised signal dict and hands it to `process_signal_for_bots`
    for every user who has a matching bot.

    Design notes:
      * We scan ONCE for the union of symbols, even if multiple users
        watch the same name. Per-bot filters inside
        `process_signal_for_bots` already handle the fan-out.
      * Strategies with `signal="neutral"` (bollinger_squeeze,
        volume_spike) are skipped — no verdict to act on.
      * `strength` (0-100) is mapped into `ai_confidence`. Strength ≥80
        upgrades the verdict to strong_buy / strong_sell so the bot's
        side filter sees the magnitude.
      * Dispatcher is idempotent per-bot via the daily cap — a signal
        that matches the same bot twice in one dispatch pass still
        respects `max_trades_per_day`.
    """
    if _db is None:
        logger.warning("[signal-dispatcher] DB handle missing, skipping run")
        return {"skipped": True, "reason": "no_db"}

    # Collect every enabled signal bot's symbol + strategy whitelists,
    # grouped by user so we can fan the signals back out correctly.
    cursor = _db.trading_bots.find(
        {"type": "signal", "enabled": True},
        {"_id": 0, "user_id": 1, "config": 1, "name": 1},
    )
    bots_by_user: dict[str, list[dict]] = {}
    union_symbols: set[str] = set()
    union_strategies: set[str] = set()
    async for b in cursor:
        uid = b.get("user_id")
        if not uid:
            continue
        bots_by_user.setdefault(uid, []).append(b)
        cfg = b.get("config") or {}
        for s in cfg.get("symbols") or []:
            if s:
                union_symbols.add(s.upper())
        for sid in cfg.get("strategies") or []:
            if sid:
                union_strategies.add(sid)

    if not union_symbols:
        logger.debug("[signal-dispatcher] no enabled signal bots with symbols, skipping")
        return {"dispatched": 0, "reason": "no_symbols"}

    from services.scanner_service import scan_symbols, STRATEGIES
    strategies = sorted(union_strategies) if union_strategies else None
    scan = await scan_symbols(sorted(union_symbols), strategies=strategies)

    # Map strength → verdict using each strategy's inherent bias.
    # Neutral strategies (squeeze, volume spike) are skipped — the
    # signal bot can't pick a side from a "something's coming" cue.
    dispatched = 0
    for sid, block in (scan.get("strategies") or {}).items():
        meta = STRATEGIES.get(sid) or {}
        bias = meta.get("signal", "neutral")
        if bias == "neutral":
            continue
        for m in block.get("matches") or []:
            strength = float(m.get("strength") or 0)
            if strength <= 0:
                continue
            if bias == "bullish":
                verdict = "strong_buy" if strength >= 80 else "buy"
            else:
                verdict = "strong_sell" if strength >= 80 else "sell"
            signal = {
                "ai_confidence": strength,  # 0-100 to match bot cfg gate
                "ai_verdict": verdict,
                "strategy_id": sid,
                "symbol": m.get("symbol", "").upper(),
                "price": m.get("price") or 0,
                "source": "signal_bot_dispatcher",
                "detail": m.get("detail", ""),
            }
            # Fan out to every user who has a bot — filters inside
            # `process_signal_for_bots` dedupe via per-bot whitelists.
            for uid in bots_by_user:
                try:
                    results = await process_signal_for_bots(uid, signal)
                    dispatched += len(results or [])
                except Exception as e:
                    logger.warning(
                        f"[signal-dispatcher] fan-out failed for user {uid} "
                        f"on {signal['symbol']}/{sid}: {e}"
                    )

    logger.info(
        f"[signal-dispatcher] scanned {len(union_symbols)} symbols, "
        f"fanned to {len(bots_by_user)} users, {dispatched} trades executed"
    )
    return {
        "symbols_scanned": len(union_symbols),
        "strategies_checked": len((scan.get("strategies") or {})),
        "users": len(bots_by_user),
        "dispatched": dispatched,
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Webhook Bot Engine ──

async def process_webhook(user_id: str, bot_id: str, webhook_secret: str, payload: dict) -> dict:
    """Process an incoming TradingView webhook."""
    from bson import ObjectId
    bot = await _db.trading_bots.find_one({"_id": ObjectId(bot_id), "user_id": user_id, "type": "webhook"})
    if not bot:
        return {"error": "Bot not found"}
    if not bot.get("enabled"):
        return {"error": "Bot is disabled"}
    if bot.get("webhook_secret") != webhook_secret:
        return {"error": "Invalid webhook secret"}

    cfg = bot.get("config", {})
    now = datetime.now(timezone.utc)

    # Rate limit
    today = now.strftime("%Y-%m-%d")
    if cfg.get("last_trade_date") != today:
        cfg["trades_today"] = 0
        cfg["last_trade_date"] = today
    if cfg["trades_today"] >= cfg.get("max_trades_per_day", 10):
        return {"error": "Daily trade limit reached"}

    # Parse webhook payload
    action = payload.get("action", "").lower()
    symbol = payload.get("symbol", payload.get("ticker", "")).upper()
    qty = float(payload.get("qty", payload.get("quantity", cfg.get("default_qty", 1))))

    if action not in cfg.get("accepted_actions", ["buy", "sell"]):
        return {"error": f"Action '{action}' not accepted"}
    if cfg.get("require_symbol") and not symbol:
        return {"error": "Symbol required"}

    # Execute
    user_id_str = bot["user_id"]
    if cfg.get("use_smart_order", False):
        from services.smart_order_service import create_smart_order
        order_result = await create_smart_order(user_id_str, {
            "symbol": symbol, "side": action, "qty": qty,
            "mode": bot.get("mode", "paper"), "order_type": "market",
        })
        result = {"status": "executed", "type": "smart_order", "order": order_result}
    else:
        await _execute_bot_trade(bot, symbol, action, qty, 0)
        result = {"status": "executed", "type": "paper_trade", "symbol": symbol, "side": action, "qty": qty}

    # Update stats
    cfg["trades_today"] = cfg.get("trades_today", 0) + 1
    stats = bot.get("stats", {})
    stats["signals_received"] = stats.get("signals_received", 0) + 1
    stats["signals_executed"] = stats.get("signals_executed", 0) + 1
    stats["trades"] = stats.get("trades", 0) + 1
    await _db.trading_bots.update_one(
        {"_id": bot["_id"]},
        {"$set": {"config": cfg, "stats": stats, "last_run": now.isoformat(), "updated_at": now.isoformat()}}
    )

    return result


async def _apply_bot_risk_guards(bot: dict, user_id: str, qty: float) -> tuple[float, dict]:
    """Pre-flight risk check for bot-initiated trades.

    Mirrors the circuit breaker the `/api/risk-calc` endpoint already
    returns to the UI so human-initiated trades and bot-initiated trades
    obey the same auto-de-risk rules. If the user's recent prediction
    outcomes indicate a losing streak >= 3 or drawdown >= 10%, bot
    position size is halved before hitting the broker.

    Returns `(adjusted_qty, context_dict)`. Never raises — on any lookup
    error we fall through to the original qty so the bot keeps trading
    (fail-open is the correct default: we never want a bot frozen by a
    Mongo hiccup, only by a real losing streak).
    """
    ctx = {"risk_reduced": False, "original_qty": qty, "adjusted_qty": qty, "reason": None}
    if _db is None:
        return qty, ctx
    try:
        from routes.risk_calculator import _compute_risk_context, _get_account_value

        account_value = await _get_account_value(user_id)
        risk_ctx = await _compute_risk_context(user_id, account_value)

        if risk_ctx.get("risk_reduced"):
            new_qty = max(1.0, round(qty * risk_ctx.get("reduction_factor", 0.5), 4))
            ctx.update({
                "risk_reduced": True,
                "adjusted_qty": new_qty,
                "reason": risk_ctx.get("reason"),
                "losing_streak": risk_ctx.get("losing_streak"),
                "current_drawdown": risk_ctx.get("current_drawdown"),
            })
            logger.warning(
                f"[bot-guard] {bot.get('name')} qty {qty} → {new_qty} "
                f"(reason: {risk_ctx.get('reason')})"
            )
            # Record the de-risk for audit (not a veto — just a reduction).
            try:
                from services.rejection_log import log_rejected
                await log_rejected(
                    asset=(bot.get("symbol") or "").upper(),
                    direction=None,
                    reason=f"bot qty reduced {qty} → {new_qty}: {risk_ctx.get('reason')}",
                    source="risk_circuit_breaker",
                    meta={
                        "bot_name": bot.get("name"),
                        "bot_type": bot.get("type"),
                        "mode": bot.get("mode"),
                        "reduction_factor": risk_ctx.get("reduction_factor"),
                    },
                    user_id=user_id,
                )
            except Exception:
                pass
            return new_qty, ctx
    except Exception as e:
        logger.warning(f"[bot-guard] check failed (failing-open): {e}")
    return qty, ctx


async def _execute_bot_trade(bot: dict, symbol: str, side: str, qty: float, price: float):
    """Execute a trade for a bot.

    - mode=paper: routes through paper_trading_service
    - mode=live:  routes through the user's connected broker (Alpaca for
                  equities, Kraken for crypto). Mirrors the live path used
                  by smart_order_service._execute_fill() so behaviour stays
                  consistent between grid/signal/webhook bots and Smart
                  Orders. Returns the broker order id on success so the
                  caller can reconcile fills.

    Pre-flight: runs the same circuit-breaker the UI risk-calc respects.
    When streak/drawdown thresholds are tripped the bot's qty is halved
    (never skipped entirely — grid/DCA strategies need continuity).
    """
    mode = bot.get("mode", "paper")
    user_id = bot["user_id"]

    # Circuit-breaker pre-flight — fails open on any DB/lookup error.
    qty, guard_ctx = await _apply_bot_risk_guards(bot, user_id, qty)

    if mode == "paper":
        from services.paper_trading_service import execute_trade
        return await execute_trade(user_id, symbol, side.upper(), qty)

    if mode == "live":
        try:
            if _db is None:
                logger.error("Live bot trade attempted with no DB handle")
                return {"error": "DB unavailable"}
            broker_conn = await _db.broker_connections.find_one(
                {"user_id": user_id}, {"_id": 0, "broker_id": 1},
            )
            if not broker_conn:
                return {"error": "No broker connected for live trading"}

            from routes.broker import _get_or_refresh_client, _get_user_broker
            import asyncio as _asyncio

            conn = await _get_user_broker(user_id, broker_conn["broker_id"])
            client = await _get_or_refresh_client(user_id, broker_conn["broker_id"], conn)
            result = await _asyncio.to_thread(
                client.place_order,
                symbol=symbol, qty=qty, side=side.lower(),
                order_type="market", time_in_force="day",
            )
            if not result:
                return {"error": "Broker rejected order"}
            logger.info(
                f"Live bot trade filled: {bot.get('name')} {side} {qty} {symbol} "
                f"(broker_order_id={result.get('id')})"
            )
            return {"status": "filled", "broker_order_id": result.get("id"),
                    "symbol": symbol, "side": side, "qty": qty}
        except Exception as e:
            logger.error(f"Live bot trade failed ({bot.get('name')} {symbol}): {e}")
            return {"error": f"Live execution failed: {e}"}

    return {"error": f"Unknown bot mode: {mode}"}
