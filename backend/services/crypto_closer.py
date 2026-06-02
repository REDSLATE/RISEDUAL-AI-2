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
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from services.crypto_memory_writer import write_crypto_trade_memory
from services.adversarial_logger import update_decision_outcome

logger = logging.getLogger(__name__)

DEFAULT_CRYPTO_HOLD_HOURS = 12

QuoteProvider = Callable[[str], Awaitable[dict]]


def _resolve_trail_config() -> dict[str, float | bool]:
    """Read trailing-stop knobs from env. Tunable via:

    * ``CRYPTO_TRAIL_ENABLED``       (default ``1`` → on)
    * ``CRYPTO_TRAIL_TRIGGER_PCT``   (default ``2.0`` → arms once
      unrealized > +2% from entry)
    * ``CRYPTO_TRAIL_GIVEBACK_PCT``  (default ``50.0`` → exit when
      price falls back by 50% of the peak gain)

    Disabled trails fall through to the legacy SL → TP → max_hold
    cascade.
    """
    def _f(name: str, default: float) -> float:
        try:
            v = float(os.environ.get(name, "") or default)
            return max(0.0, v)
        except ValueError:
            return default

    enabled_raw = os.environ.get("CRYPTO_TRAIL_ENABLED", "1").strip().lower()
    return {
        "enabled": enabled_raw in {"1", "true", "yes", "on"},
        "trigger_pct": _f("CRYPTO_TRAIL_TRIGGER_PCT", 2.0),
        "giveback_pct": _f("CRYPTO_TRAIL_GIVEBACK_PCT", 50.0),
    }


def _check_trailing_exit(
    direction: str,
    entry_price: float,
    current_price: float,
    peak_price: Optional[float],
    trigger_pct: float,
    giveback_pct: float,
) -> bool:
    """Return True iff the trailing-stop should fire NOW.

    Logic (LONG; SHORT mirrors):
      1. peak_price is the highest favourable mark since entry
         (closer updates this every tick).
      2. peak_gain_pct = (peak - entry) / entry × 100. If <
         trigger_pct, trail isn't armed yet.
      3. Once armed, fire when price falls back to:
            entry + peak_gain × (1 - giveback_pct/100)
         i.e., 50% of the peak gain from entry by default.
    """
    if peak_price is None or entry_price <= 0:
        return False

    direction = (direction or "LONG").upper()
    if direction == "LONG":
        peak_gain_pct = (peak_price - entry_price) / entry_price * 100
        if peak_gain_pct < trigger_pct:
            return False
        keep = peak_gain_pct * (1 - giveback_pct / 100.0)
        floor = entry_price * (1 + keep / 100.0)
        return current_price <= floor

    if direction == "SHORT":
        # For SHORT, "favourable" is a falling price → peak is the
        # LOW since entry. peak_gain_pct = (entry - peak) / entry × 100.
        peak_gain_pct = (entry_price - peak_price) / entry_price * 100
        if peak_gain_pct < trigger_pct:
            return False
        keep = peak_gain_pct * (1 - giveback_pct / 100.0)
        ceiling = entry_price * (1 - keep / 100.0)
        return current_price >= ceiling

    return False


def _update_peak_price(
    direction: str,
    current_peak: Optional[float],
    current_price: float,
) -> float:
    """Return the new peak/trough mark. LONG tracks the highest
    price seen, SHORT tracks the lowest. Initialised to
    ``current_price`` when peak is missing."""
    if current_peak is None:
        return float(current_price)
    direction = (direction or "LONG").upper()
    if direction == "SHORT":
        return min(float(current_peak), float(current_price))
    return max(float(current_peak), float(current_price))


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
        "stop_loss": 0, "take_profit": 0, "trailing_stop": 0,
        "hold_window_expired": 0,
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
            stored_peak = trade.get("peak_price")

            # ── Trailing-stop bookkeeping ─────────────────────────
            # Update the peak/trough watermark every tick BEFORE
            # the exit decision so a single-tick spike-and-revert
            # still arms the trail (peak captured) and triggers it
            # (price now back below the floor) in one pass.
            new_peak = _update_peak_price(direction, stored_peak, exit_price)
            peak_changed = (
                stored_peak is None or abs(float(new_peak) - float(stored_peak)) > 1e-9
            )
            trail_cfg = _resolve_trail_config()
            trail_armed = trail_cfg["enabled"] and _check_trailing_exit(
                direction=direction,
                entry_price=entry_price,
                current_price=exit_price,
                peak_price=new_peak,
                trigger_pct=trail_cfg["trigger_pct"],
                giveback_pct=trail_cfg["giveback_pct"],
            )

            # ── Exit decision (SL → TP → trail → max_hold) ────────
            # Detection MUST use mid (or last) — TP/SL levels were
            # configured against unbiased prices. Realised fill is
            # computed AFTER the trigger fires, when we know the
            # bot is sending a market order.
            exit_reason = _check_exit_trigger(
                direction, exit_price, stop_loss, take_profit,
            )
            if exit_reason is None and trail_armed:
                exit_reason = "trailing_stop"
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
                    # Still in the hold window — leave open, but
                    # persist the updated peak watermark so the
                    # next tick's trailing-stop check has the
                    # correct reference point. Cheap update; only
                    # writes when the watermark actually moved.
                    if peak_changed:
                        try:
                            await db.crypto_paper_trades.update_one(
                                {"trade_id": trade.get("trade_id"),
                                 "status": "open"},
                                {"$set": {"peak_price": float(new_peak)}},
                            )
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "[crypto-closer] peak_price persist "
                                "failed for %s: %s",
                                trade.get("trade_id"), exc,
                            )
                    skipped += 1
                    continue
                exit_reason = "hold_window_expired"

            # ── Exit slippage (close-LONG sells at bid /
            #     close-SHORT buys at ask) ────────────────────────
            from services.slippage_simulator import apply_exit_slippage
            _xslip = apply_exit_slippage(quote, direction)
            # Use slippage-adjusted fill price as realised exit_price
            # when bid/ask are present. ``mid_only`` keeps the
            # mid-priced exit_price unchanged; ``unknown`` would
            # have been blocked above by the ``exit_price <= 0`` gate.
            if _xslip.method in ("ask_fill", "bid_fill"):
                exit_price = _xslip.fill_price

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
                "peak_price": float(new_peak),
                # Exit-side slippage stamp — mirrors entry stamp.
                "exit_quote_bid": _xslip.bid,
                "exit_quote_ask": _xslip.ask,
                "exit_quote_mid": _xslip.mid,
                "exit_slippage_bps": _xslip.slippage_bps,
                "exit_slippage_method": _xslip.method,
                "exit_quote_source": (quote or {}).get("source"),
            }

            # Post-trade autopsy — pure overlay describing why this
            # trade won or lost. Stamped alongside the close fields.
            try:
                from services.post_trade_autopsy import (
                    build_post_trade_autopsy,
                )
                update["autopsy"] = build_post_trade_autopsy({
                    **trade, **update,
                })
            except Exception as _ap_exc:  # noqa: BLE001
                logger.debug(
                    "[crypto-closer] autopsy build failed: %s", _ap_exc,
                )

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

            # ── 2026-06 (Gap 2): enqueue crypto outcome to the
            # Sovereign sidecar's inbox so MC's recent_outcomes
            # snapshot accumulates crypto closes. Without this,
            # MC's Scorecard sees `total_resolved=0` for Alpha —
            # equity closes were already wired (see
            # paper_trade_closer.py), but crypto closes were not.
            try:
                from services.sovereign_outcome_bridge import (
                    enqueue_outcome,
                )
                outcome_label = (
                    "win" if pnl > 0 else ("loss" if pnl < 0 else "flat")
                )
                await enqueue_outcome(
                    db,
                    brain="alpha",
                    trade_id=str(
                        closed_trade.get("trade_id")
                        or closed_trade.get("id")
                        or trade.get("trade_id")
                        or trade.get("_id")
                        or ""
                    ),
                    symbol=symbol,
                    direction=direction,
                    confidence=float(trade.get("confidence") or 0.0),
                    outcome_label=outcome_label,
                    notional=float(
                        trade.get("position_usd")
                        or trade.get("size_usd")
                        or 0.0
                    ),
                    extras={
                        "lane": "crypto",
                        "receipt_type": "paper",
                        "r_multiple": r_multiple,
                        "close_reason": exit_reason,
                    },
                    sovereign_decision_id=trade.get("sovereign_decision_id"),
                    prediction_id=trade.get("prediction_id"),
                    source_signal=trade.get("source_signal"),
                )
            except Exception as _bridge_exc:  # noqa: BLE001
                logger.debug(
                    "[crypto-closer] outcome bridge enqueue failed: %s",
                    _bridge_exc,
                )

            # Patent M (Alpha) — Shelly observation-side ingestion.
            # Behind ``LEARNING_CORE_INGEST_ENABLED`` (default off).
            # Best-effort: never raises, never affects the close.
            try:
                from services.shelly_ingest_adapter import (
                    feed_shelly_from_closed_trade,
                )
                await feed_shelly_from_closed_trade(db, closed_trade)
            except Exception as _shelly_exc:
                logger.debug(
                    "[crypto-closer] shelly ingest failed (non-critical): %s",
                    _shelly_exc,
                )

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
