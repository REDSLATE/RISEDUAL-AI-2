"""Crypto Closer — isolated 24/7 paper-trade lifecycle terminator.

Closes ``crypto_paper_trades`` rows whose ``opened_at`` is older
than ``hold_hours`` (default 12). On close it:

1. Marks the row ``status="closed"`` with ``pnl``, ``r_multiple``,
   ``exit_price``, ``closed_at``, ``close_reason``.
2. Hands the closed trade to :func:`write_crypto_trade_memory`
   which routes it into the isolated ``crypto_trade_memory``
   learning surface.

Architecture rule
-----------------
Reads/writes ONLY:

    * crypto_paper_trades   (lifecycle terminator)
    * crypto_trade_memory   (via write_crypto_trade_memory)

Never touches:

    * paper_trades          (equity)
    * ml_paper_trader       (equity)
    * paper_trade_closer    (equity)
    * price_provider.get_quote (equity quote path)

Quote path is dependency-injected (default
``services.crypto_quotes.get_crypto_quote``) so tests can stub it
without monkey-patching the live price layer.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from services.crypto_memory_writer import write_crypto_trade_memory

logger = logging.getLogger(__name__)

DEFAULT_CRYPTO_HOLD_HOURS = 12

QuoteProvider = Callable[[str], Awaitable[dict]]


def compute_crypto_pnl(
    direction: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
) -> float:
    """Direction-aware dollar PnL. SHORTs profit on falling marks."""
    if direction.upper() == "SHORT":
        return round((entry_price - exit_price) * quantity, 2)
    return round((exit_price - entry_price) * quantity, 2)


def compute_crypto_r_multiple(
    direction: str,
    entry_price: float,
    exit_price: float,
    stop_loss: Optional[float],
) -> float:
    """R-multiple = realised move / pre-defined risk. 0.0 when SL
    is missing (we can't know the risk denominator)."""
    if not stop_loss:
        return 0.0

    risk = abs(entry_price - stop_loss)
    if risk <= 0:
        return 0.0

    if direction.upper() == "SHORT":
        pnl_per_unit = entry_price - exit_price
    else:
        pnl_per_unit = exit_price - entry_price

    return round(pnl_per_unit / risk, 4)


async def close_expired_crypto_trades(
    db: Any,
    quote_provider: Optional[QuoteProvider] = None,
    hold_hours: int = DEFAULT_CRYPTO_HOLD_HOURS,
) -> dict[str, Any]:
    """Close every open crypto fill older than ``hold_hours``.

    Each close runs through :func:`write_crypto_trade_memory` so the
    learning surface stays in sync with the lifecycle table.
    """
    if db is None:
        return {"closed": 0, "skipped": 0, "errors": 0, "reason": "db_missing"}

    if quote_provider is None:
        from services.crypto_quotes import get_crypto_quote
        quote_provider = get_crypto_quote

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hold_hours)

    closed = 0
    skipped = 0
    errors = 0

    cursor = db.crypto_paper_trades.find({
        "status": "open",
        "opened_at": {"$lte": cutoff},
    })

    async for trade in cursor:
        try:
            symbol = trade.get("symbol")
            if not symbol:
                skipped += 1
                continue

            # Belt-and-suspenders firewall — never run crypto exit
            # logic against a row from another asset class.
            if (trade.get("asset_class") or "crypto") != "crypto":
                logger.warning(
                    "[crypto-closer] non-crypto asset_class in crypto "
                    "collection: trade_id=%s — skipping",
                    trade.get("trade_id"),
                )
                skipped += 1
                continue

            quote = await quote_provider(symbol)
            exit_price = float((quote or {}).get("price") or 0.0)
            if exit_price <= 0:
                skipped += 1
                errors += 1
                continue

            entry_price = float(trade["entry_price"])
            quantity = float(trade.get("quantity", 0))
            direction = trade.get("direction", "LONG")
            stop_loss = trade.get("stop_loss")

            pnl = compute_crypto_pnl(
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
            )

            r_multiple = compute_crypto_r_multiple(
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                stop_loss=stop_loss,
            )

            update = {
                "status": "closed",
                "exit_price": exit_price,
                "closed_at": now,
                "pnl": pnl,
                "r_multiple": r_multiple,
                "close_reason": "hold_window_expired",
            }

            result = await db.crypto_paper_trades.update_one(
                {"_id": trade["_id"], "status": "open"},
                {"$set": update},
            )

            if result.modified_count == 0:
                skipped += 1
                continue

            closed_trade = {**trade, **update}
            # _id isn't needed downstream and shouldn't be carried
            # into the memory writer's denormalised doc.
            closed_trade.pop("_id", None)

            await write_crypto_trade_memory(db, closed_trade)
            closed += 1

            logger.info(
                "[crypto-closer] closed %s %s qty=%s entry=%.4f exit=%.4f "
                "pnl=%.2f r=%.3f reason=hold_window_expired",
                symbol, direction, quantity, entry_price, exit_price,
                pnl, r_multiple,
            )

        except Exception as exc:  # noqa: BLE001
            logger.error("[crypto-closer] error closing trade: %s", exc)
            errors += 1
            continue

    return {
        "closed": closed,
        "skipped": skipped,
        "errors": errors,
        "hold_hours": hold_hours,
    }
