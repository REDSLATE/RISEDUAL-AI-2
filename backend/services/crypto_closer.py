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
from services.adversarial_logger import update_decision_outcome

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


def _check_exit_trigger(
    direction: str,
    current_price: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> Optional[str]:
    """Return ``"stop_loss"``, ``"take_profit"``, or None.

    Priority is SL → TP. If both are hit on the same bar (gappy
    crypto markets can do this), SL wins because we should
    assume the worst-case fill at scan time.

    Returns None when neither is hit (or when the field is unset),
    leaving the closer to fall back to the max-hold rule.
    """
    direction = (direction or "LONG").upper()
    if direction == "LONG":
        if stop_loss and current_price <= float(stop_loss):
            return "stop_loss"
        if take_profit and current_price >= float(take_profit):
            return "take_profit"
        return None
    if direction == "SHORT":
        if stop_loss and current_price >= float(stop_loss):
            return "stop_loss"
        if take_profit and current_price <= float(take_profit):
            return "take_profit"
        return None
    return None


async def close_expired_crypto_trades(
    db: Any,
    quote_provider: Optional[QuoteProvider] = None,
    hold_hours: int = DEFAULT_CRYPTO_HOLD_HOURS,
) -> dict[str, Any]:
    """Close every open crypto fill that has either:

    1. Hit its ``stop_loss`` (priority).
    2. Hit its ``take_profit``.
    3. Aged past ``hold_hours``.

    SL/TP are checked on EVERY open trade regardless of age, so a
    +4% target can fire at hour 3 instead of waiting for the 12h
    timeout. Each close hands the trade to
    :func:`write_crypto_trade_memory` for learning ingestion.
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
    reasons: dict[str, int] = {
        "stop_loss": 0, "take_profit": 0, "hold_window_expired": 0,
    }

    # Widened from the previous "aged-only" query: every open crypto
    # trade is a candidate because SL/TP can hit before max_hold.
    cursor = db.crypto_paper_trades.find({"status": "open"})

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
            take_profit = trade.get("take_profit")
            opened_at = trade.get("opened_at")

            # ── Exit decision (SL → TP → max_hold) ────────────────
            exit_reason = _check_exit_trigger(
                direction, exit_price, stop_loss, take_profit,
            )
            if exit_reason is None:
                # Fall back to max-hold expiry. ``opened_at`` lives
                # in Mongo as either a BSON datetime or ISO string;
                # tolerate both.
                if isinstance(opened_at, str):
                    try:
                        opened_at = datetime.fromisoformat(
                            opened_at.replace("Z", "+00:00")
                        )
                    except ValueError:
                        opened_at = None
                if not isinstance(opened_at, datetime):
                    skipped += 1
                    continue
                if opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=timezone.utc)
                if opened_at > cutoff:
                    # Still in the hold window — leave open
                    skipped += 1
                    continue
                exit_reason = "hold_window_expired"

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
                "close_reason": exit_reason,
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

            # Adversarial decision outcome attribution. ONLY runs if the
            # trade actually carries a decision_id (i.e. the
            # adversarial layer was active when the fill happened —
            # both gates open). Failure here must NEVER block the
            # close — wrapped + suppressed.
            # Adversarial decision outcome attribution. ONLY runs if the
            # trade actually carries a decision_id (i.e. the
            # adversarial layer was active when the fill happened —
            # both gates open). Failure here must NEVER block the
            # close — wrapped + suppressed.
            adv_id = closed_trade.get("adversarial_decision_id")
            if adv_id:
                try:
                    await update_decision_outcome(db, adv_id, r_multiple)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[crypto-closer] adversarial outcome update failed "
                        "for %s: %s", adv_id, exc,
                    )

            # ── Step 10: OUTCOME_VERIFIED proof block ────────────────
            # Closes the IP-contract proof chain for this trade by
            # linking entry chain (proof_chain_entity_id) to realized
            # P&L. Only fires when the entry-side guard ran (older
            # trades pre-IP-contract have no entity_id and are
            # legitimately skipped). Failure must never block the close.
            entity_id = closed_trade.get("proof_chain_entity_id")
            if entity_id:
                try:
                    from services.proof_chain import (
                        AsyncMongoProofChainStore,
                        ProofEvent,
                        ProofEventType,
                        async_append_proof_event,
                    )
                    proof_store = AsyncMongoProofChainStore(db)
                    await async_append_proof_event(
                        proof_store,
                        ProofEvent(
                            event_type=ProofEventType.OUTCOME_VERIFIED,
                            entity_id=entity_id,
                            actor="crypto_paper_closer",
                            payload={
                                "trade_id": closed_trade.get("trade_id"),
                                "symbol": symbol,
                                "direction": direction,
                                "entry_price": entry_price,
                                "exit_price": exit_price,
                                "quantity": quantity,
                                "pnl": pnl,
                                "r_multiple": r_multiple,
                                "close_reason": exit_reason,
                                "outcome": "win" if pnl > 0 else (
                                    "loss" if pnl < 0 else "flat"
                                ),
                            },
                        ),
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[crypto-closer] OUTCOME_VERIFIED proof append "
                        "failed for %s: %s", entity_id, exc,
                    )

            closed += 1
            reasons[exit_reason] = reasons.get(exit_reason, 0) + 1

            logger.info(
                "[crypto-closer] closed %s %s qty=%s entry=%.4f exit=%.4f "
                "pnl=%.2f r=%.3f reason=%s",
                symbol, direction, quantity, entry_price, exit_price,
                pnl, r_multiple, exit_reason,
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
        "reasons": reasons,
    }
