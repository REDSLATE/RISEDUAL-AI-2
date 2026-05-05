"""Tier-3 paper-bot closer — pairs unmatched BUY fills with SELL exits.

The Tier-3 Accumulator bots route through ``paper_trading_service``
which writes BUY/SELL fill rows to ``paper_trades`` (no ``status``
field). The original ``paper_trade_closer.py`` only handles Schema-A
position rows (``status: open``) emitted by ``ml_paper_trader.py``,
so Tier-3 BUYs accumulate without ever closing.

This closer reconstructs open positions from the BUY/SELL ledger,
applies the same exit cascade we ship for crypto:

    SL → trail → max_hold

(hard TP off by default — trailing handles winners), then emits a
SELL via ``paper_trading_service.execute_trade`` so the same fill
ledger captures the close. Reuses crypto's env knobs:

    EQUITY_SL_PCT             default 1.0
    EQUITY_DISABLE_TP         default 1 (hard TP off)
    EQUITY_TP_PCT             default 6.0
    EQUITY_TRAIL_ENABLED      default 1
    EQUITY_TRAIL_TRIGGER_PCT  default 2.0
    EQUITY_TRAIL_GIVEBACK_PCT default 50.0
    EQUITY_HOLD_HOURS         default 36   (enough to span an RTH cycle)
    TIER3_PAPER_CLOSER_DISABLED default 0

Peak-price watermark is persisted on the latest BUY fill (per
position) so we don't need a schema migration. Idempotent: a
position closed in tick T won't be re-emitted in tick T+1 because
the SELL fill makes net qty == 0.

Owner-driven discovery: closer scans every distinct
``(user_id, symbol)`` with positive net qty in the last
``EQUITY_HOLD_HOURS × 3`` window — bounded so a stale position
from two months ago doesn't get force-closed at a price the user
no longer recognises.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from services.crypto_closer import (
    _check_trailing_exit,
    _update_peak_price,
)

logger = logging.getLogger(__name__)


def _is_disabled() -> bool:
    return os.environ.get(
        "TIER3_PAPER_CLOSER_DISABLED", "0",
    ).strip().lower() in {"1", "true", "yes", "on"}


def _f(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
        return max(0.0, v)
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if v == "":
        return default
    return v in {"1", "true", "yes", "on"}


def _resolve_equity_exit_config() -> dict[str, float | bool]:
    return {
        "sl_pct": _f("EQUITY_SL_PCT", 1.0),
        "tp_pct": _f("EQUITY_TP_PCT", 6.0),
        "tp_disabled": _b("EQUITY_DISABLE_TP", True),
        "trail_enabled": _b("EQUITY_TRAIL_ENABLED", True),
        "trail_trigger_pct": _f("EQUITY_TRAIL_TRIGGER_PCT", 2.0),
        "trail_giveback_pct": _f("EQUITY_TRAIL_GIVEBACK_PCT", 50.0),
        "hold_hours": int(_f("EQUITY_HOLD_HOURS", 36.0)),
    }


def _decide_equity_exit(
    *,
    entry_price: float,
    current_price: float,
    peak_price: float | None,
    opened_at: datetime,
    cfg: dict[str, float | bool],
    now: datetime | None = None,
) -> str | None:
    """Pure decision function — returns the exit reason or None.

    Cascade matches the crypto closer:
      1. stop_loss (hard 1%)
      2. take_profit (hard, off by default)
      3. trailing_stop (armed at +2%, fires at 50% giveback)
      4. hold_window_expired (safety floor)
    """
    if entry_price <= 0:
        return None
    now = now or datetime.now(timezone.utc)

    sl_floor = entry_price * (1 - float(cfg["sl_pct"]) / 100.0)
    if current_price <= sl_floor:
        return "stop_loss"

    if not cfg["tp_disabled"]:
        tp_ceiling = entry_price * (1 + float(cfg["tp_pct"]) / 100.0)
        if current_price >= tp_ceiling:
            return "take_profit"

    if cfg["trail_enabled"] and _check_trailing_exit(
        direction="LONG",
        entry_price=entry_price,
        current_price=current_price,
        peak_price=peak_price,
        trigger_pct=float(cfg["trail_trigger_pct"]),
        giveback_pct=float(cfg["trail_giveback_pct"]),
    ):
        return "trailing_stop"

    if (now - opened_at).total_seconds() >= int(cfg["hold_hours"]) * 3600:
        return "hold_window_expired"

    return None


async def _net_qty_and_anchor(db, user_id: str, symbol: str,
                              window_hours: int) -> dict | None:
    """Return ``{net_qty, anchor}`` where anchor is the most recent
    BUY fill that established the still-open portion of the position.

    Net qty: sum(BUY qty) − sum(SELL qty) within the window.
    Returns None when no anchor exists OR net qty is non-positive.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    pipeline = [
        {"$match": {
            "user_id": user_id, "symbol": symbol,
            "opened_at": {"$gte": since},
            "side": {"$in": ["BUY", "SELL"]},
        }},
        {"$group": {
            "_id": None,
            "buy_qty": {"$sum": {
                "$cond": [{"$eq": ["$side", "BUY"]}, "$qty", 0],
            }},
            "sell_qty": {"$sum": {
                "$cond": [{"$eq": ["$side", "SELL"]}, "$qty", 0],
            }},
        }},
    ]
    agg = await db.paper_trades.aggregate(pipeline).to_list(length=1)
    if not agg:
        return None
    buys = float(agg[0].get("buy_qty") or 0.0)
    sells = float(agg[0].get("sell_qty") or 0.0)
    net = buys - sells
    if net <= 0:
        return None

    anchor = await db.paper_trades.find_one(
        {"user_id": user_id, "symbol": symbol, "side": "BUY",
         "opened_at": {"$gte": since}},
        {"_id": 0},
        sort=[("opened_at", -1)],
    )
    if not anchor:
        return None
    return {"net_qty": net, "anchor": anchor}


async def _resolve_managed_symbol_universe(db) -> set[str]:
    """Return the union of all symbols owned by enabled Tier-3
    Accumulator signal bots. Any symbol NOT in this set is
    presumed manual / non-bot and is left untouched by the
    closer — protects manual user purchases (BTC, individual
    name buys, etc.) from getting force-exited at 1% SL.

    Empty set is treated as "no managed symbols" → closer
    no-ops cleanly. Cheap aggregation; safe to call every tick.
    """
    if db is None:
        return set()
    cursor = db.trading_bots.find(
        {
            "type": "signal", "enabled": True,
            "name": {"$regex": "^Tier3 Accumulator · "},
        },
        {"_id": 0, "config.symbols": 1},
    )
    out: set[str] = set()
    async for b in cursor:
        for s in (b.get("config") or {}).get("symbols") or []:
            if isinstance(s, str) and s.strip():
                out.add(s.strip().upper())
    return out


async def close_due_tier3_paper_trades(db: Any) -> dict:
    """Walk every distinct (user_id, symbol) with positive net qty
    in the rolling window and emit SELLs for the ones whose exit
    cascade fires.

    Symbol-gated: only positions whose symbol is owned by an
    enabled Tier-3 Accumulator signal bot are considered. Manual
    user buys (BTC, off-roster equities) are skipped entirely.

    Returns ``{positions_evaluated, closed, errors, reasons,
    skipped_unmanaged}`` so the scheduler entry can log a one-liner.
    """
    if _is_disabled():
        logger.info("[tier3-closer] disabled via env — skipping")
        return {"positions_evaluated": 0, "closed": 0, "errors": 0,
                "reasons": {}, "disabled": True,
                "skipped_unmanaged": 0}
    if db is None:
        return {"positions_evaluated": 0, "closed": 0, "errors": 0,
                "reasons": {}, "skipped_unmanaged": 0}

    managed = await _resolve_managed_symbol_universe(db)
    if not managed:
        logger.debug("[tier3-closer] no Tier-3 bots enabled — no-op")
        return {"positions_evaluated": 0, "closed": 0, "errors": 0,
                "reasons": {}, "skipped_unmanaged": 0}

    cfg = _resolve_equity_exit_config()
    window_hours = max(int(cfg["hold_hours"]) * 3, 24)
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    # Discover every distinct (user_id, symbol) that traded in the
    # window. Cheaper than scanning every fill — most users have
    # at most a handful of distinct symbols.
    pairs_pipeline = [
        {"$match": {
            "opened_at": {"$gte": since},
            "side": {"$in": ["BUY", "SELL"]},
            "symbol": {"$in": list(managed)},
        }},
        {"$group": {"_id": {"u": "$user_id", "s": "$symbol"}}},
    ]
    pairs = await db.paper_trades.aggregate(pairs_pipeline).to_list(
        length=5000,
    )

    from services.price_provider import get_quote
    from services.paper_trading_service import execute_trade

    positions_evaluated = 0
    closed = 0
    errors = 0
    reasons: dict[str, int] = {
        "stop_loss": 0, "take_profit": 0,
        "trailing_stop": 0, "hold_window_expired": 0,
    }

    for p in pairs:
        user_id = p["_id"]["u"]
        symbol = p["_id"]["s"]
        try:
            state = await _net_qty_and_anchor(db, user_id, symbol,
                                              window_hours)
            if state is None:
                continue
            positions_evaluated += 1
            anchor = state["anchor"]
            net_qty = state["net_qty"]
            entry_price = float(anchor.get("price") or 0.0)
            opened_at = anchor.get("opened_at")
            if not isinstance(opened_at, datetime):
                continue
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            peak = anchor.get("peak_price")

            quote = await get_quote(symbol)
            current_price = (quote or {}).get("price")
            if current_price is None:
                continue
            current_price = float(current_price)

            new_peak = _update_peak_price(
                "LONG", peak, current_price,
            )
            peak_changed = (peak is None
                            or abs(float(new_peak) - float(peak)) > 1e-9)

            exit_reason = _decide_equity_exit(
                entry_price=entry_price,
                current_price=current_price,
                peak_price=new_peak,
                opened_at=opened_at,
                cfg=cfg,
            )

            if exit_reason is None:
                # Still in window — persist peak watermark on the
                # anchor BUY row so the next tick's trailing-stop
                # check has the updated reference.
                if peak_changed:
                    try:
                        await db.paper_trades.update_one(
                            {"user_id": user_id, "symbol": symbol,
                             "side": "BUY",
                             "opened_at": anchor.get("opened_at")},
                            {"$set": {"peak_price": float(new_peak)}},
                        )
                    except Exception:
                        pass
                continue

            # Fire the SELL.
            res = await execute_trade(
                user_id=user_id, symbol=symbol, side="SELL",
                qty=float(net_qty),
            )
            if res.get("status") != "filled":
                errors += 1
                logger.warning(
                    "[tier3-closer] SELL rejected %s %s qty=%s: %s",
                    user_id, symbol, net_qty, res.get("error"),
                )
                continue

            # Stamp the close_reason + peak on the SELL fill so
            # downstream analytics can attribute the exit cause.
            try:
                await db.paper_trades.update_one(
                    {"user_id": user_id, "symbol": symbol, "side": "SELL",
                     "timestamp": res.get("timestamp")},
                    {"$set": {
                        "close_reason": exit_reason,
                        "peak_price": float(new_peak),
                        "exit_path": "tier3_paper_closer",
                    }},
                )
            except Exception:
                pass

            closed += 1
            reasons[exit_reason] = reasons.get(exit_reason, 0) + 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.exception(
                "[tier3-closer] failure on %s %s: %s",
                user_id, symbol, exc,
            )

    return {
        "positions_evaluated": positions_evaluated,
        "closed": closed,
        "errors": errors,
        "reasons": reasons,
        "managed_universe_size": len(managed),
    }
