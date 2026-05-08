"""Smart Order Service — Advanced order types: ladder entries, trailing SL/TP, break-even, multi-TP.

Supports 3 execution modes:
  - paper: Executes against paper trading portfolio
  - live: Executes through connected broker (owner-only)
  - simulate: Previews order behavior without execution

Smart orders are stored in MongoDB and monitored by a background price checker.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_db: Any = None

def set_db(database: Any) -> None:
    global _db
    _db = database


# ── Smart Order Types ──

ORDER_TYPES = {"market", "limit", "ladder", "smart"}
SIDES = {"buy", "sell"}
MODES = {"paper", "live", "simulate"}


async def create_smart_order(user_id: str, order: dict) -> dict:
    """Create a smart order with advanced features.
    
    Order fields:
      symbol, side, qty, order_type, mode,
      entry_price (limit/ladder),
      stop_loss: {price, trailing, trailing_pct, cooldown_seconds, emergency_price},
      take_profits: [{price, pct_of_qty, trailing}],
      break_even: {enabled, trigger_tp_index},
      ladder: {levels, range_low, range_high, distribution},
    """
    symbol = order["symbol"].upper()
    side = order["side"].lower()
    mode = order.get("mode", "paper")
    qty = float(order["qty"])

    if side not in SIDES:
        return {"error": "Side must be buy or sell"}
    if qty <= 0:
        return {"error": "Quantity must be positive"}
    if mode not in MODES:
        return {"error": f"Mode must be one of: {', '.join(MODES)}"}

    # Get current price
    from services.price_provider import get_quote, get_crypto_quote
    CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "DOT", "SHIB", "LINK", "BNB"}
    q = await get_crypto_quote(symbol) if symbol in CRYPTO else await get_quote(symbol)
    if not q or not q.get("price"):
        return {"error": f"Cannot get price for {symbol}"}
    current_price = float(q["price"])

    now = datetime.now(timezone.utc).isoformat()

    # Build ladder legs if ladder order
    legs = []
    ladder_cfg = order.get("ladder")
    if ladder_cfg and int(ladder_cfg.get("levels", 1)) > 1:
        legs = _build_ladder_legs(
            side, qty, current_price,
            float(ladder_cfg.get("range_low", current_price * 0.97)),
            float(ladder_cfg.get("range_high", current_price * 1.03)),
            int(ladder_cfg["levels"]),
            ladder_cfg.get("distribution", "equal"),
        )
    else:
        entry_price = float(order.get("entry_price") or current_price)
        legs = [{"price": round(entry_price, 6), "qty": qty, "filled": False, "filled_at": None, "fill_price": None}]

    # Build take-profit chain
    tp_chain = []
    take_profits_list = order.get("take_profits") or []
    for i, tp in enumerate(take_profits_list):
        tp_chain.append({
            "index": i,
            "price": float(tp["price"]),
            "pct_of_qty": float(tp.get("pct_of_qty", 100 if i == len(take_profits_list) - 1 else 25)),
            "trailing": bool(tp.get("trailing", False)),
            "trailing_pct": float(tp.get("trailing_pct", 1.0)),
            "highest_seen": None,
            "triggered": False,
            "triggered_at": None,
        })

    # Build stop-loss config
    sl_cfg = order.get("stop_loss") or {}
    stop_loss = None
    if sl_cfg and sl_cfg.get("price"):
        stop_loss = {
            "price": float(sl_cfg["price"]),
            "original_price": float(sl_cfg["price"]),
            "trailing": bool(sl_cfg.get("trailing", False)),
            "trailing_pct": float(sl_cfg.get("trailing_pct", 2.0)),
            "cooldown_seconds": int(sl_cfg.get("cooldown_seconds", 0)),
            "emergency_price": float(sl_cfg["emergency_price"]) if sl_cfg.get("emergency_price") else None,
            "lowest_seen": current_price if side == "sell" else None,
            "highest_seen": current_price if side == "buy" else None,
            "triggered": False,
            "triggered_at": None,
            "moved_to_break_even": False,
        }

    # Break-even config
    be_cfg = order.get("break_even") or {}
    break_even = {
        "enabled": bool(be_cfg.get("enabled", False)),
        "trigger_tp_index": int(be_cfg.get("trigger_tp_index", 0)),
        "activated": False,
    }

    # Simulate mode — return preview without saving
    if mode == "simulate":
        return _build_simulation_preview(
            symbol, side, qty, current_price, legs, tp_chain, stop_loss, break_even
        )

    # Build order document
    doc = {
        "user_id": user_id,
        "symbol": symbol,
        "side": side,
        "total_qty": qty,
        "filled_qty": 0,
        "mode": mode,
        "order_type": order.get("order_type", "smart"),
        "legs": legs,
        "take_profits": tp_chain,
        "stop_loss": stop_loss,
        "break_even": break_even,
        "status": "pending",  # pending | partially_filled | filled | stopped | cancelled
        "current_price": current_price,
        "avg_fill_price": None,
        "realized_pnl": 0,
        "created_at": now,
        "updated_at": now,
        "fills": [],
        # IP-contract entity_id from the manual-order guard (if the
        # caller supplied one). Persisted so the cancel/close path can
        # append OUTCOME_VERIFIED to the same proof chain.
        "proof_chain_entity_id": order.get("proof_chain_entity_id"),
    }

    # For market orders, execute entry immediately
    if order.get("order_type") == "market" or (not ladder_cfg and not order.get("entry_price")):
        fill_result = await _execute_fill(doc, current_price, qty, "entry")
        if fill_result.get("error"):
            return fill_result
        doc["status"] = "filled"
        doc["avg_fill_price"] = current_price
        doc["filled_qty"] = qty
        for leg in doc["legs"]:
            leg["filled"] = True
            leg["fill_price"] = current_price
            leg["filled_at"] = now

    result = await _db.smart_orders.insert_one(doc)
    doc.pop("_id", None)
    doc["order_id"] = str(result.inserted_id)
    return doc


def _build_ladder_legs(side: str, total_qty: float, current_price: float,
                       range_low: float, range_high: float, levels: int,
                       distribution: str = "equal") -> list[dict]:
    """Build ladder entry legs spread across a price range."""
    levels = max(2, min(levels, 10))
    prices = []
    step = (range_high - range_low) / (levels - 1) if levels > 1 else 0
    for i in range(levels):
        prices.append(round(range_low + step * i, 6))

    # Distribution: equal, weighted_bottom, weighted_top
    if distribution == "weighted_bottom":
        weights = [levels - i for i in range(levels)]
    elif distribution == "weighted_top":
        weights = [i + 1 for i in range(levels)]
    else:
        weights = [1] * levels

    total_weight = sum(weights)
    legs = []
    remaining_qty = total_qty
    for i, (price, weight) in enumerate(zip(prices, weights)):
        if i == levels - 1:
            qty = remaining_qty
        else:
            qty = round(total_qty * weight / total_weight, 6)
            remaining_qty -= qty
        legs.append({
            "price": price,
            "qty": max(qty, 0.000001),
            "filled": False,
            "filled_at": None,
            "fill_price": None,
        })
    return legs


def _build_simulation_preview(symbol: str, side: str, qty: float, current_price: float,
                              legs: list[dict], tp_chain: list[dict],
                              stop_loss: dict | None, break_even: dict) -> dict:
    """Preview a smart order without executing."""
    entry_cost = sum(leg["price"] * leg["qty"] for leg in legs)
    avg_entry = entry_cost / qty if qty > 0 else current_price

    # Calculate R:R
    total_tp_value = 0
    if tp_chain:
        for tp in tp_chain:
            tp_qty = qty * tp["pct_of_qty"] / 100
            if side == "buy":
                total_tp_value += (tp["price"] - avg_entry) * tp_qty
            else:
                total_tp_value += (avg_entry - tp["price"]) * tp_qty

    risk = 0
    if stop_loss:
        if side == "buy":
            risk = (avg_entry - stop_loss["price"]) * qty
        else:
            risk = (stop_loss["price"] - avg_entry) * qty

    rr_ratio = round(total_tp_value / risk, 2) if risk > 0 else 0

    return {
        "mode": "simulate",
        "symbol": symbol,
        "side": side,
        "total_qty": qty,
        "current_price": current_price,
        "avg_entry_price": round(avg_entry, 4),
        "total_entry_cost": round(entry_cost, 2),
        "legs": legs,
        "take_profits": tp_chain,
        "stop_loss": stop_loss,
        "break_even": break_even,
        "projected_reward": round(total_tp_value, 2),
        "projected_risk": round(risk, 2),
        "risk_reward_ratio": rr_ratio,
        "leg_count": len(legs),
        "tp_count": len(tp_chain),
    }


async def _execute_fill(order_doc: dict, price: float, qty: float, fill_type: str) -> dict:
    """Execute a fill against paper trading or live broker."""
    mode = order_doc["mode"]
    user_id = order_doc["user_id"]
    symbol = order_doc["symbol"]
    side = order_doc["side"]

    if mode == "paper":
        from services.paper_trading_service import execute_trade
        result = await execute_trade(user_id, symbol, side.upper(), qty)
        if "error" in result:
            return result
        return {"status": "filled", "price": price, "qty": qty, "fill_type": fill_type}

    elif mode == "live":
        # Live execution through connected broker
        try:
            broker_conn = await _db.broker_connections.find_one({"user_id": user_id}, {"_id": 0, "broker_id": 1})
            if not broker_conn:
                return {"error": "No broker connected for live trading"}
            # Execute through broker route logic
            from routes.broker import _get_or_refresh_client, _get_user_broker
            conn = await _get_user_broker(user_id, broker_conn["broker_id"])
            client = await _get_or_refresh_client(user_id, broker_conn["broker_id"], conn)
            import asyncio
            result = await asyncio.to_thread(
                client.place_order, symbol=symbol, qty=qty, side=side,
                order_type="market", time_in_force="day",
            )
            if not result:
                return {"error": "Broker rejected order"}
            return {"status": "filled", "price": price, "qty": qty, "fill_type": fill_type, "broker_order_id": result.get("id")}
        except Exception as e:
            logger.error(f"Live execution failed: {e}")
            return {"error": f"Live execution failed: {str(e)}"}

    return {"error": "Unknown mode"}


async def get_smart_orders(user_id: str, status: Optional[str] = None, limit: int = 50) -> list[dict]:
    """Get smart orders for a user."""
    query = {"user_id": user_id}
    if status:
        query["status"] = status
    cursor = _db.smart_orders.find(query, {"_id": 0}).sort("created_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def cancel_smart_order(user_id: str, order_id: str) -> dict:
    """Cancel a pending/active smart order.

    On manual cancel of a filled order, append an OUTCOME_VERIFIED
    proof block to the IP chain (step 10). The cancel is treated as
    the close event with the current_price as exit. Failure to write
    the proof block must NEVER block the cancel.
    """
    from bson import ObjectId
    oid = ObjectId(order_id)
    # Capture the order pre-update so we have the entity_id + entry data
    pre = await _db.smart_orders.find_one({"_id": oid, "user_id": user_id})
    result = await _db.smart_orders.update_one(
        {"_id": oid, "user_id": user_id, "status": {"$in": ["pending", "partially_filled", "filled"]}},
        {"$set": {"status": "cancelled", "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    if result.matched_count == 0:
        return {"error": "Order not found or already completed"}

    # Step 10 — append OUTCOME_VERIFIED for filled orders only. Pending/
    # partially_filled never had an entry, so there's no realized P&L to
    # log. Only fires when the entry-side guard wrote an entity_id.
    try:
        entity_id = (pre or {}).get("proof_chain_entity_id")
        if entity_id and (pre or {}).get("avg_fill_price"):
            from services.manual_order_guard import record_manual_order_outcome
            entry_px = float(pre["avg_fill_price"])
            exit_px = float(pre.get("current_price") or entry_px)
            qty = float(pre.get("filled_qty") or pre.get("total_qty") or 0)
            side = (pre.get("side") or "").upper()
            sign = 1 if side in ("BUY", "LONG") else -1
            pnl = (exit_px - entry_px) * qty * sign
            await record_manual_order_outcome(
                _db,
                proof_chain_entity_id=entity_id,
                trade_id=str(order_id),
                symbol=pre.get("symbol", ""),
                direction=side,
                asset_class="equity",
                entry_price=entry_px,
                exit_price=exit_px,
                quantity=qty,
                pnl=pnl,
                close_reason="manual_cancel",
                actor=f"user:{user_id}",
            )
    except Exception as e:  # noqa: BLE001 — never block the cancel
        logger.warning(
            "[smart_orders] OUTCOME_VERIFIED append failed for %s: %s",
            order_id, e,
        )

    return {"status": "cancelled", "order_id": order_id}


async def check_smart_orders() -> None:
    """Background task: Check all active smart orders against live prices.
    Handles: ladder fills, stop-loss triggers, take-profit triggers, trailing updates, break-even moves.
    """
    if _db is None:
        return

    active = await _db.smart_orders.find(
        {"status": {"$in": ["pending", "partially_filled", "filled"]}},
    ).to_list(length=500)

    if not active:
        return

    from services.price_provider import get_quote, get_crypto_quote
    CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX", "DOT", "SHIB", "LINK", "BNB"}

    # Batch fetch prices for all unique symbols
    symbols = list({o["symbol"] for o in active})
    prices = {}
    for sym in symbols:
        try:
            q = await get_crypto_quote(sym) if sym in CRYPTO else await get_quote(sym)
            if q and q.get("price"):
                prices[sym] = float(q["price"])
        except Exception:
            pass

    now = datetime.now(timezone.utc).isoformat()

    for order in active:
        oid = order["_id"]
        sym = order["symbol"]
        if sym not in prices:
            continue
        price = prices[sym]
        side = order["side"]
        updates = {"current_price": price, "updated_at": now}
        fills_to_add = []
        changed = False

        # 1. Check ladder legs for fills
        if order["status"] in ("pending", "partially_filled"):
            for i, leg in enumerate(order.get("legs", [])):
                if leg["filled"]:
                    continue
                should_fill = (side == "buy" and price <= leg["price"]) or (side == "sell" and price >= leg["price"])
                if should_fill:
                    fill_result = await _execute_fill(order, leg["price"], leg["qty"], "ladder_entry")
                    if not fill_result.get("error"):
                        order["legs"][i]["filled"] = True
                        order["legs"][i]["filled_at"] = now
                        order["legs"][i]["fill_price"] = price
                        order["filled_qty"] = order.get("filled_qty", 0) + leg["qty"]
                        fills_to_add.append({"type": "entry", "leg": i, "price": price, "qty": leg["qty"], "at": now})
                        changed = True

            all_filled = all(leg["filled"] for leg in order.get("legs", []))
            if all_filled and order["status"] != "filled":
                updates["status"] = "filled"
                filled_legs = [leg for leg in order["legs"] if leg["fill_price"]]
                if filled_legs:
                    updates["avg_fill_price"] = round(sum(leg["fill_price"] * leg["qty"] for leg in filled_legs) / sum(leg["qty"] for leg in filled_legs), 6)
                changed = True
            elif order["filled_qty"] > 0 and not all_filled:
                updates["status"] = "partially_filled"
                changed = True

        # 2. Check stop-loss (only if we have a position)
        sl = order.get("stop_loss")
        if sl and not sl["triggered"] and order.get("filled_qty", 0) > 0:
            # Update trailing stop
            if sl.get("trailing"):
                if side == "buy":
                    if sl.get("highest_seen") is None or price > sl["highest_seen"]:
                        sl["highest_seen"] = price
                        new_sl = round(price * (1 - sl["trailing_pct"] / 100), 6)
                        if new_sl > sl["price"]:
                            sl["price"] = new_sl
                            changed = True
                else:
                    if sl.get("lowest_seen") is None or price < sl["lowest_seen"]:
                        sl["lowest_seen"] = price
                        new_sl = round(price * (1 + sl["trailing_pct"] / 100), 6)
                        if new_sl < sl["price"]:
                            sl["price"] = new_sl
                            changed = True

            # Check trigger
            sl_hit = (side == "buy" and price <= sl["price"]) or (side == "sell" and price >= sl["price"])
            emergency_hit = sl.get("emergency_price") and (
                (side == "buy" and price <= sl["emergency_price"]) or
                (side == "sell" and price >= sl["emergency_price"])
            )

            if sl_hit or emergency_hit:
                remaining_qty = order.get("filled_qty", 0) - sum(
                    tp["pct_of_qty"] / 100 * order["total_qty"]
                    for tp in order.get("take_profits", []) if tp["triggered"]
                )
                if remaining_qty > 0:
                    close_side = "sell" if side == "buy" else "buy"
                    temp_order = {**order, "side": close_side}
                    fill_result = await _execute_fill(temp_order, price, remaining_qty, "stop_loss")
                    if not fill_result.get("error"):
                        sl["triggered"] = True
                        sl["triggered_at"] = now
                        updates["status"] = "stopped"
                        avg_entry = order.get("avg_fill_price", order["legs"][0]["price"])
                        pnl = (price - avg_entry) * remaining_qty if side == "buy" else (avg_entry - price) * remaining_qty
                        updates["realized_pnl"] = round(order.get("realized_pnl", 0) + pnl, 2)
                        fills_to_add.append({"type": "stop_loss", "price": price, "qty": remaining_qty, "at": now})
                        changed = True

        # 3. Check take-profits
        if order.get("filled_qty", 0) > 0 and order.get("status") != "stopped":
            for i, tp in enumerate(order.get("take_profits", [])):
                if tp["triggered"]:
                    continue

                # Trailing TP update
                if tp.get("trailing"):
                    if side == "buy":
                        if tp.get("highest_seen") is None or price > tp["highest_seen"]:
                            tp["highest_seen"] = price
                        trail_trigger = tp["highest_seen"] * (1 - tp["trailing_pct"] / 100)
                        if price <= trail_trigger and price > tp["price"]:
                            tp["price"] = round(trail_trigger, 6)

                tp_hit = (side == "buy" and price >= tp["price"]) or (side == "sell" and price <= tp["price"])
                if tp_hit:
                    tp_qty = round(order["total_qty"] * tp["pct_of_qty"] / 100, 6)
                    close_side = "sell" if side == "buy" else "buy"
                    temp_order = {**order, "side": close_side}
                    fill_result = await _execute_fill(temp_order, price, tp_qty, f"take_profit_{i}")
                    if not fill_result.get("error"):
                        tp["triggered"] = True
                        tp["triggered_at"] = now
                        avg_entry = order.get("avg_fill_price", order["legs"][0]["price"])
                        pnl = (price - avg_entry) * tp_qty if side == "buy" else (avg_entry - price) * tp_qty
                        updates["realized_pnl"] = round(order.get("realized_pnl", 0) + pnl, 2)
                        fills_to_add.append({"type": f"take_profit_{i}", "price": price, "qty": tp_qty, "at": now})
                        changed = True

                        # Break-even: move SL to entry after TP trigger
                        be = order.get("break_even", {})
                        if be.get("enabled") and not be.get("activated") and i >= be.get("trigger_tp_index", 0):
                            if sl and not sl["triggered"]:
                                sl["price"] = order.get("avg_fill_price", order["legs"][0]["price"])
                                sl["moved_to_break_even"] = True
                                be["activated"] = True
                                changed = True

        # 4. Check if all TPs triggered — mark completed
        if order.get("take_profits") and all(tp["triggered"] for tp in order["take_profits"]):
            if order.get("status") not in ("stopped", "cancelled"):
                updates["status"] = "completed"
                changed = True

        # Save updates
        if changed or fills_to_add:
            updates["legs"] = order.get("legs", [])
            updates["take_profits"] = order.get("take_profits", [])
            updates["stop_loss"] = sl
            updates["break_even"] = order.get("break_even", {})
            updates["filled_qty"] = order.get("filled_qty", 0)
            set_ops = {"$set": updates}
            if fills_to_add:
                set_ops["$push"] = {"fills": {"$each": fills_to_add}}
            await _db.smart_orders.update_one({"_id": oid}, set_ops)

    logger.debug(f"Smart order check: {len(active)} orders, {len(prices)} prices fetched")
