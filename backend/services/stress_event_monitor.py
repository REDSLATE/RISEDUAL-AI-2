"""
Cross-Asset Stress Event Monitor.

Why this exists
───────────────
The Live Spread Watch tile already detects per-symbol liquidity
stress (spread > threshold). What it doesn't do is *react* — and
it doesn't preserve the fact that a stress event happened so you
can replay it post-hoc.

This module does both:

1. **Fire a ``stress_events`` row** when ``stressed_count`` exceeds
   the cross-asset threshold (default 3 symbols simultaneously).
   Includes a snapshot of every row that was stressed at the time
   so the operator can audit which symbols moved together.

2. **Auto-flatten** all open paper positions when the auto-flatten
   gate is set (env-flagged, OFF by default). This is the safety
   mechanism that lets you sleep through a flash event without
   carrying open delta. Closes both lanes, stamps the close
   reason as ``stress_auto_flatten``.

Cooldown
────────
Sustained stress would otherwise spam ``stress_events`` rows and
re-flatten on every tick. A per-process cooldown timer (default
10 min) gates re-firing. The cooldown is tracked on the
``stress_events`` row itself (``cooldown_until``) so a process
restart still respects it.

Read invariants
───────────────
* Spread snapshot fetched via the same ``/spread-watch`` code path
  the UI uses — single source of truth.
* Auto-flatten writes use the existing closer code paths — same
  ``autopsy`` stamp + ``close_reason`` discipline as the regular
  closers. Stress-flattened trades are clearly tagged for
  post-event review.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

COLLECTION: str = "stress_events"

# Defaults are conservative — operator can tune via env.
STRESS_SYMBOL_THRESHOLD: int = int(
    os.environ.get("STRESS_SYMBOL_THRESHOLD", "3"),
)
STRESS_COOLDOWN_MIN: int = int(
    os.environ.get("STRESS_COOLDOWN_MIN", "10"),
)
STRESS_AUTO_FLATTEN_ENABLED_DEFAULT: str = "0"


def _auto_flatten_enabled() -> bool:
    return os.environ.get(
        "STRESS_AUTO_FLATTEN_ENABLED", STRESS_AUTO_FLATTEN_ENABLED_DEFAULT,
    ).strip().lower() not in ("0", "false", "off", "no")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _last_event(db: Any) -> dict | None:
    if db is None:
        return None
    try:
        return await db[COLLECTION].find_one(
            {}, {"_id": 0}, sort=[("fired_at", -1)],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[stress] last_event read failed: %s", exc)
        return None


async def _in_cooldown(db: Any) -> bool:
    last = await _last_event(db)
    if last is None:
        return False
    cu = last.get("cooldown_until")
    if cu is None:
        return False
    if isinstance(cu, str):
        try:
            cu = datetime.fromisoformat(cu.replace("Z", "+00:00"))
        except ValueError:
            return False
    return _now() < cu


async def _flatten_open_paper_trades(db: Any) -> dict[str, int]:
    """Close all open positions in BOTH lanes with a
    ``stress_auto_flatten`` reason. Reuses the existing closer
    update shape so the autopsy stamp + outcome computation
    happen consistently."""
    counts = {"equity_closed": 0, "crypto_closed": 0, "errors": 0}
    if db is None:
        return counts

    now = _now()

    # Equity — uses ``paper_trades`` collection.
    try:
        from services.post_trade_autopsy import build_post_trade_autopsy
        async for trade in db.paper_trades.find({"status": "open"}, {"_id": 0}):
            try:
                entry = float(trade.get("entry_price") or 0)
                shares = float(trade.get("shares") or 0)
                exit_price = entry  # flat-at-entry; no live quote
                pnl_usd = 0.0
                pnl_pct = 0.0
                outcome = "flat"
                doc = {
                    **trade,
                    "exit_price": exit_price,
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "outcome": outcome,
                    "close_reason": "stress_auto_flatten",
                }
                update_set = {
                    "status": "closed",
                    "closed_at": now,
                    "exit_price": exit_price,
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "outcome": outcome,
                    "auto_closed": True,
                    "auto_close_reason": "stress_auto_flatten",
                    "autopsy": build_post_trade_autopsy(doc),
                }
                res = await db.paper_trades.update_one(
                    {"trade_id": trade.get("trade_id"), "status": "open"},
                    {"$set": update_set},
                )
                if res.modified_count:
                    counts["equity_closed"] += 1
                    # Mirror entry_price * shares left intact;
                    # autopsy slippage block reads from entry stamp.
                    if entry > 0 and shares > 0:
                        pass  # noqa: B015
            except Exception as exc:  # noqa: BLE001
                counts["errors"] += 1
                logger.warning("[stress] equity flatten row failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        counts["errors"] += 1
        logger.warning("[stress] equity flatten lane failed: %s", exc)

    # Crypto — uses ``crypto_paper_trades`` collection.
    try:
        from services.post_trade_autopsy import build_post_trade_autopsy
        async for trade in db.crypto_paper_trades.find({"status": "open"}, {"_id": 0}):
            try:
                entry = float(trade.get("entry_price") or 0)
                exit_price = entry
                pnl = 0.0
                doc = {
                    **trade, "exit_price": exit_price,
                    "pnl": pnl, "close_reason": "stress_auto_flatten",
                }
                update = {
                    "status": "closed",
                    "exit_price": exit_price,
                    "closed_at": now,
                    "pnl": pnl,
                    "r_multiple": 0.0,
                    "close_reason": "stress_auto_flatten",
                    "autopsy": build_post_trade_autopsy(doc),
                }
                res = await db.crypto_paper_trades.update_one(
                    {"trade_id": trade.get("trade_id"), "status": "open"},
                    {"$set": update},
                )
                if res.modified_count:
                    counts["crypto_closed"] += 1
            except Exception as exc:  # noqa: BLE001
                counts["errors"] += 1
                logger.warning("[stress] crypto flatten row failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        counts["errors"] += 1
        logger.warning("[stress] crypto flatten lane failed: %s", exc)

    return counts


async def _gather_spread_snapshot() -> dict[str, Any] | None:
    """Same code path as the admin endpoint, called inline so the
    monitor stays consistent with the UI's sense of "stress"."""
    try:
        import asyncio
        from services.kraken_crypto_quotes import fetch_kraken_quotes_batch
        from services.alpaca_equity_quotes import fetch_alpaca_equity_quotes_batch

        crypto_list = ["BTC", "ETH", "SOL", "DOGE", "XRP"]
        equity_list = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]
        crypto_out, equity_out = await asyncio.gather(
            fetch_kraken_quotes_batch(crypto_list),
            fetch_alpaca_equity_quotes_batch(equity_list),
        )

        # Reuse session label from admin route — duplicate here to
        # avoid a circular import.
        now = _now()
        if now.weekday() >= 5:
            session = "closed"
        else:
            minutes = now.hour * 60 + now.minute
            if 13 * 60 + 30 <= minutes < 20 * 60:
                session = "rth"
            elif 8 * 60 <= minutes < 13 * 60 + 30:
                session = "pre"
            elif 20 * 60 <= minutes:
                session = "post"
            else:
                session = "closed"

        threshold_bps = float(os.environ.get("STRESS_SPREAD_THRESHOLD_BPS", "25"))
        rows: list[dict] = []
        for sym in crypto_list:
            q = crypto_out.get(sym.upper())
            if q is None:
                continue
            spread = q.get("spread_bps")
            stressed = spread is not None and spread > threshold_bps
            rows.append({
                "lane": "crypto", "symbol": sym, "spread_bps": spread,
                "stressed": bool(stressed),
            })
        for sym in equity_list:
            q = equity_out.get(sym.upper())
            if q is None:
                continue
            spread = q.get("spread_bps")
            # Equity stress only fires during RTH — wide AH spreads
            # are normal and must NOT trigger an event.
            stressed = (
                spread is not None
                and spread > threshold_bps
                and session == "rth"
            )
            rows.append({
                "lane": "equity", "symbol": sym, "spread_bps": spread,
                "stressed": bool(stressed),
            })
        return {
            "rows": rows,
            "stressed_count": sum(1 for r in rows if r["stressed"]),
            "session": session,
            "threshold_bps": threshold_bps,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[stress] spread snapshot failed: %s", exc)
        return None


async def run_stress_check(db: Any) -> dict[str, Any]:
    """One-shot evaluation. Safe to call from APScheduler or admin
    button. Returns a JSON-friendly dict describing the action
    taken — no exceptions propagate."""
    snapshot = await _gather_spread_snapshot()
    if snapshot is None:
        return {"status": "snapshot_failed", "fired": False}

    stressed_count = snapshot["stressed_count"]
    if stressed_count < STRESS_SYMBOL_THRESHOLD:
        return {
            "status": "calm",
            "fired": False,
            "stressed_count": stressed_count,
            "threshold": STRESS_SYMBOL_THRESHOLD,
            "session": snapshot["session"],
        }

    if await _in_cooldown(db):
        return {
            "status": "cooldown",
            "fired": False,
            "stressed_count": stressed_count,
            "session": snapshot["session"],
        }

    fired_at = _now()
    cooldown_until = fired_at + timedelta(minutes=STRESS_COOLDOWN_MIN)

    flatten_result: dict[str, int] | None = None
    auto_flatten = _auto_flatten_enabled()
    if auto_flatten:
        flatten_result = await _flatten_open_paper_trades(db)

    event_doc = {
        "fired_at": fired_at,
        "cooldown_until": cooldown_until,
        "session": snapshot["session"],
        "stressed_count": stressed_count,
        "threshold": STRESS_SYMBOL_THRESHOLD,
        "spread_threshold_bps": snapshot["threshold_bps"],
        "stressed_rows": [
            r for r in snapshot["rows"] if r["stressed"]
        ],
        "auto_flatten_enabled": auto_flatten,
        "flatten_result": flatten_result,
    }

    if db is not None:
        try:
            await db[COLLECTION].insert_one(dict(event_doc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[stress] event write failed: %s", exc)

    # Strip the ``_id`` mutation Mongo just added so callers get a
    # clean JSON-friendly dict.
    event_doc.pop("_id", None)
    event_doc["fired_at"] = fired_at.isoformat()
    event_doc["cooldown_until"] = cooldown_until.isoformat()
    event_doc["status"] = "fired"
    event_doc["fired"] = True
    return event_doc
