"""Crypto Paper Trade Closer — isolated from the equity closer.

The equity ``paper_trade_closer.py`` runs on a 24-hour hold window
keyed to NYSE bell semantics. Crypto trades 24/7, so it gets its
own closer with crypto-tuned hold windows + hourly tick.

What this closer does
---------------------
For every ``crypto_paper_trades`` document with ``status="open"``:

1. Computes age in hours from ``opened_at``.
2. Fetches a fresh mark from :func:`services.crypto_quotes.get_crypto_quote`.
3. Applies exit rules in priority order:
   * **Stop-loss hit** (when the doc has ``stop_loss``).
   * **Take-profit hit** (when the doc has ``take_profit``).
   * **Max hold reached** (default 12h, env-tunable).
4. On exit, computes direction-aware PnL and updates the doc:
   ``status="closed"``, ``closed_at``, ``exit_price``, ``pnl_usd``,
   ``pnl_pct``, ``exit_reason`` (sl|tp|max_hold).

What this closer DOES NOT do
----------------------------
* Touch the legacy ``paper_trades`` collection — that's the equity
  closer's job (``services/paper_trade_closer.py``).
* Touch ``ml_paper_trader``, ``paper_trading_service``, or
  ``price_provider.get_quote``. The architectural firewall holds.

Returns a structured summary the scheduler logs and the admin
dashboard surfaces.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# Env-tunable — crypto bots run more aggressively than equity, so a
# 12h default cycles fills faster without burning thousands of
# trades a day. Override via env for backtesting longer holds.
_DEFAULT_MAX_HOLD_HOURS = float(
    os.environ.get("CRYPTO_PAPER_MAX_HOLD_HOURS", "12")
)


QuoteProvider = Callable[[str], Awaitable[dict]]


def _calc_pnl(direction: str, entry: float, exit_px: float,
              qty: float) -> tuple[float, float]:
    """Return ``(pnl_usd, pnl_pct)`` direction-aware.

    LONG profits when exit > entry, SHORT profits when exit < entry."""
    if entry <= 0:
        return 0.0, 0.0
    sign = 1.0 if str(direction).upper() == "LONG" else -1.0
    pnl_usd = round(sign * (exit_px - entry) * qty, 4)
    pnl_pct = round(sign * (exit_px - entry) / entry * 100.0, 4)
    return pnl_usd, pnl_pct


def _hit_stop(direction: str, entry: float, mark: float,
              stop_loss: Optional[float]) -> bool:
    if stop_loss is None or stop_loss <= 0:
        return False
    if str(direction).upper() == "LONG":
        return mark <= stop_loss
    return mark >= stop_loss


def _hit_target(direction: str, entry: float, mark: float,
                take_profit: Optional[float]) -> bool:
    if take_profit is None or take_profit <= 0:
        return False
    if str(direction).upper() == "LONG":
        return mark >= take_profit
    return mark <= take_profit


async def close_due_crypto_trades(
    db: Any,
    quote_provider: Optional[QuoteProvider] = None,
    *,
    max_hold_hours: float = _DEFAULT_MAX_HOLD_HOURS,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Close open crypto paper trades that have hit SL, TP, or max hold.

    Parameters
    ----------
    db
        Motor handle. Reads/writes ``db.crypto_paper_trades`` only.
    quote_provider
        Async callable returning ``{"price": …}`` for a symbol.
        Defaults to :func:`services.crypto_quotes.get_crypto_quote`.
    max_hold_hours
        Force-close any trade older than this. Env-tunable via
        ``CRYPTO_PAPER_MAX_HOLD_HOURS`` (default 12h).
    now
        Override "now" for deterministic tests.

    Returns
    -------
    dict
        ``{"closed": int, "skipped": int, "scanned": int,
           "reasons": {...}, "max_hold_hours": float}``
    """
    if quote_provider is None:
        from services.crypto_quotes import get_crypto_quote
        quote_provider = get_crypto_quote

    now = now or datetime.now(timezone.utc)
    cursor = db.crypto_paper_trades.find({"status": "open"})
    open_trades = await cursor.to_list(length=10_000)

    summary: dict[str, Any] = {
        "scanned": len(open_trades),
        "closed": 0,
        "skipped": 0,
        "reasons": {"stop_loss": 0, "take_profit": 0, "max_hold": 0},
        "max_hold_hours": max_hold_hours,
        "errors": 0,
    }

    for trade in open_trades:
        trade_id = trade.get("trade_id")
        symbol = trade.get("symbol")
        direction = trade.get("direction") or "LONG"
        entry = float(trade.get("entry_price") or 0.0)
        qty = float(trade.get("quantity") or 0.0)
        opened_at = trade.get("opened_at")
        sl = trade.get("stop_loss")
        tp = trade.get("take_profit")

        # Belt-and-suspenders firewall — if a stock somehow landed
        # in this collection (shouldn't be possible, but log + skip
        # rather than execute against the equity closer's domain).
        if (trade.get("asset_class") or "crypto") != "crypto":
            summary["skipped"] += 1
            logger.warning(
                "[crypto-closer] non-crypto asset_class in crypto collection: "
                "trade_id=%s asset_class=%s — skipping",
                trade_id, trade.get("asset_class"),
            )
            continue

        if not symbol or entry <= 0 or qty <= 0:
            summary["skipped"] += 1
            continue

        # Age check (works whether opened_at is datetime or ISO str)
        if isinstance(opened_at, str):
            try:
                opened_at = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
            except ValueError:
                summary["skipped"] += 1
                continue
        if not isinstance(opened_at, datetime):
            summary["skipped"] += 1
            continue
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        age_hours = (now - opened_at).total_seconds() / 3600.0

        # Fetch live mark
        try:
            quote = await quote_provider(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[crypto-closer] quote fetch failed for %s: %s — leaving open",
                symbol, exc,
            )
            summary["errors"] += 1
            summary["skipped"] += 1
            continue

        mark = float((quote or {}).get("price") or 0.0)
        if mark <= 0:
            summary["errors"] += 1
            summary["skipped"] += 1
            continue

        # Decide
        exit_reason: Optional[str] = None
        if _hit_stop(direction, entry, mark, sl):
            exit_reason = "stop_loss"
        elif _hit_target(direction, entry, mark, tp):
            exit_reason = "take_profit"
        elif age_hours >= max_hold_hours:
            exit_reason = "max_hold"

        if exit_reason is None:
            summary["skipped"] += 1
            continue

        pnl_usd, pnl_pct = _calc_pnl(direction, entry, mark, qty)

        try:
            await db.crypto_paper_trades.update_one(
                {"trade_id": trade_id, "status": "open"},
                {"$set": {
                    "status": "closed",
                    "closed_at": now,
                    "exit_price": round(mark, 6),
                    "exit_reason": exit_reason,
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "hold_hours": round(age_hours, 3),
                }},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[crypto-closer] update failed for trade_id=%s: %s",
                trade_id, exc,
            )
            summary["errors"] += 1
            summary["skipped"] += 1
            continue

        summary["closed"] += 1
        summary["reasons"][exit_reason] += 1
        logger.info(
            "[crypto-closer] closed %s %s qty=%s entry=%.4f exit=%.4f "
            "reason=%s pnl=%.2f (%.2f%%) age=%.1fh",
            symbol, direction, qty, entry, mark, exit_reason,
            pnl_usd, pnl_pct, age_hours,
        )

    return summary
