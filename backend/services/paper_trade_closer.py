"""Paper-Trade Auto-Closer — APScheduler job that closes paper
trades opened by the AI paper trader after a fixed holding window.

DTD-tagged: writes execution-state to ``paper_trades``.

The 2026-04-25 audit found 5 paper_trades (all `direction='down'`,
all from 2026-04-16) sitting in `status: 'open'` for 9 days because
NO code path closed them. The `prediction_labeler` only updates
`features_snapshots` and `predictions` — it never touches
`paper_trades`. The result was a misleading "live days 2/30" Tier 3
metric and a stale paper-portfolio that didn't reflect any of the
underlying market moves.

This service fills that gap. It runs hourly. For every trade in
`paper_trades` that has been open for ≥ ``PAPER_TRADE_HOLD_HOURS``
(default 24h), it fetches the current quote, computes P&L
direction-aware (longs get `current - entry`, shorts get `entry -
current`), writes the close fields, and stamps the paper_positions
row.

Failure-safe
------------
* Per-trade try/except — one quote failure doesn't poison the
  batch.
* No raise from the scheduler entry point. The product still runs
  if the closer is broken; we just see "open" trades pile up
  again, which the new self-test tripwire (see future task)
  would surface.
* Idempotent — `update_one({status: 'open', ...})` ensures we
  never double-close.

Operator knobs
--------------
* ``PAPER_TRADE_HOLD_HOURS`` (env, default 24)
* ``PAPER_TRADE_CLOSER_DISABLED`` (env, default off — kill switch)
"""
from __future__ import annotations

__domain__ = "DTD"

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_HOLD_HOURS = 24


def _hold_hours() -> int:
    raw = os.environ.get("PAPER_TRADE_HOLD_HOURS")
    if not raw:
        return _DEFAULT_HOLD_HOURS
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        logger.warning(
            "[paper-closer] invalid PAPER_TRADE_HOLD_HOURS=%r — using default %d",
            raw, _DEFAULT_HOLD_HOURS,
        )
        return _DEFAULT_HOLD_HOURS


def _is_disabled() -> bool:
    return os.environ.get("PAPER_TRADE_CLOSER_DISABLED", "").lower() in (
        "1", "true", "yes", "on",
    )


def _compute_close(direction: str, entry: float, current: float,
                   shares: float) -> tuple[float, float, str]:
    """Direction-aware P&L. ``direction='down'`` = short — gains
    when price falls. Returns (pnl_usd, pnl_pct, outcome)."""
    if direction == "down":
        pnl_usd = (entry - current) * shares
        pnl_pct = (entry - current) / entry if entry else 0.0
    else:
        pnl_usd = (current - entry) * shares
        pnl_pct = (current - entry) / entry if entry else 0.0
    outcome = "win" if pnl_usd > 0 else ("loss" if pnl_usd < 0 else "flat")
    return round(pnl_usd, 2), round(pnl_pct, 4), outcome


async def _fetch_price(ticker: str, db: Any) -> float | None:
    """Live quote first, cached fallback second. Returns None if
    neither path produces a price — the trade stays open and the
    next hourly tick retries."""
    try:
        from services.price_provider import get_quote
        quote = await get_quote(ticker)
        price = quote.get("price") or quote.get("c") or quote.get("close")
        if price is not None:
            return float(price)
    except Exception as exc:
        logger.warning(
            "[paper-closer] live quote failed for %s: %s — trying cache",
            ticker, exc,
        )
    try:
        cached = await db["price_cache"].find_one(
            {"key": f"quote_{ticker.upper()}"},
            sort=[("updated_at", -1)],
        )
        if cached:
            data = cached.get("data") or {}
            cp = data.get("price")
            if cp is not None:
                return float(cp)
    except Exception as exc:
        logger.warning("[paper-closer] cache fallback failed for %s: %s", ticker, exc)
    return None


async def close_due_paper_trades(db: Any) -> dict:
    """Close every AI-driven paper trade past its hold window.

    Returns ``{closed, skipped, errors, holds}`` summary so the
    scheduler entry can log a one-liner.
    """
    if _is_disabled():
        logger.info("[paper-closer] disabled via env — skipping")
        return {"closed": 0, "skipped": 0, "errors": 0, "holds": 0,
                "disabled": True}

    if db is None:
        return {"closed": 0, "skipped": 0, "errors": 0, "holds": 0}

    hold_hours = _hold_hours()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hold_hours)
    now = datetime.now(timezone.utc)

    # Only close rows we know are AI-driven (Schema A from
    # `ml_paper_trader.py`). Manual UI buy/sell ticks (Schema B)
    # don't have a `status` field — they're transactions, not
    # positions, and shouldn't be closed by this service.
    cursor = db["paper_trades"].find(
        {"status": "open", "opened_at": {"$type": "date", "$lt": cutoff}},
        {"_id": 0},
    ).limit(500)

    closed = 0
    skipped = 0
    errors = 0
    holds = 0  # quote failures — stays open, retried next tick
    quote_cache: dict[str, float | None] = {}

    async for t in cursor:
        try:
            ticker = t["ticker"]
            entry = float(t.get("entry_price") or 0)
            shares = float(t.get("shares") or 0)
            direction = str(t.get("direction") or "up").lower()
            trade_id = t.get("trade_id")

            if not (ticker and trade_id and entry > 0 and shares > 0):
                logger.warning(
                    "[paper-closer] malformed trade row skipped: %s", trade_id,
                )
                skipped += 1
                continue

            if ticker not in quote_cache:
                quote_cache[ticker] = await _fetch_price(ticker, db)
            current = quote_cache[ticker]
            if current is None:
                holds += 1
                continue

            pnl_usd, pnl_pct, outcome = _compute_close(
                direction, entry, current, shares,
            )

            # Post-trade autopsy — pure overlay built from the
            # about-to-be-closed state. Stamped alongside the close
            # fields so a single $set lands both together.
            try:
                from services.post_trade_autopsy import (
                    build_post_trade_autopsy,
                )
                autopsy = build_post_trade_autopsy({
                    **row,
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "outcome": outcome,
                    "close_reason": f"hold_window_{hold_hours}h",
                    "auto_close_reason": f"hold_window_{hold_hours}h",
                })
            except Exception as _ap_exc:  # noqa: BLE001
                logger.debug(
                    "[paper-closer] autopsy build failed: %s", _ap_exc,
                )
                autopsy = None

            update_set = {
                "status": "closed",
                "closed_at": now,
                "exit_price": current,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "outcome": outcome,
                "auto_closed": True,
                "auto_close_reason": f"hold_window_{hold_hours}h",
            }
            if autopsy is not None:
                update_set["autopsy"] = autopsy

            res = await db["paper_trades"].update_one(
                {"trade_id": trade_id, "status": "open"},
                {"$set": update_set},
            )
            if res.modified_count:
                closed += 1
                # Sync paper_positions roster (best-effort)
                try:
                    await db["paper_positions"].update_many(
                        {"ticker": ticker, "status": "open"},
                        {"$set": {"status": "closed", "closed_at": now}},
                    )
                except Exception as exc:
                    logger.warning(
                        "[paper-closer] paper_positions sync failed for %s: %s",
                        ticker, exc,
                    )
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            logger.exception("[paper-closer] row failed: %s", exc)

    if closed or holds or errors:
        logger.info(
            "[paper-closer] hold=%dh closed=%d skipped=%d holds=%d errors=%d",
            hold_hours, closed, skipped, holds, errors,
        )

    return {
        "closed": closed,
        "skipped": skipped,
        "errors": errors,
        "holds": holds,
        "hold_hours": hold_hours,
    }
