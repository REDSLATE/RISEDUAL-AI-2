"""Crypto Paper Trading Bot — isolated 24/7 lane.

This module is the crypto counterpart to ``ml_paper_trader.py``.
It runs on its own scheduler, writes to its own collection
(``crypto_paper_trades``), and does NOT import or call any of:

    * ``services/ml_paper_trader.py``
    * ``services/paper_trading_service.py``
    * ``services/paper_trade_closer.py``
    * ``services/price_provider.get_quote``  (equity quote path)

Architectural rule
------------------
Stocks and crypto trade on different clocks (NYSE 9:30–16:00 ET vs
crypto 24/7) and the ML labeling cron, paper-trade closer, and
risk caps for equities are tuned around the bell. Mixing crypto
fills into ``paper_trades`` was the bug we engineered around —
this lane keeps the two surfaces structurally disjoint.

Public surface
--------------
* :data:`CRYPTO_SYMBOLS` — canonical crypto tickers this bot trades.
* :func:`is_crypto_symbol` — guard for any crypto-aware call site.
* :func:`run_crypto_paper_bot` — the scheduled worker; returns a
  list of ``trade``-or-``skipped`` records so the route layer can
  surface what happened on a manual trigger.

The worker accepts a ``quote_provider`` parameter (default
:func:`services.crypto_quotes.get_crypto_quote`) so tests can inject
a deterministic stub without monkey-patching the price layer.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# Canonical crypto tickers this bot will trade. Kept inline (instead
# of importing from ``services.crypto_symbols``) so the bot module
# is self-contained and any future deviation in the bot's universe
# vs. the global registry is intentional.
CRYPTO_SYMBOLS: frozenset[str] = frozenset({
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK",
})


def is_crypto_symbol(symbol: str | None) -> bool:
    """Tolerant crypto check — accepts ``BTC``, ``BTC/USD``, ``btc-usd``."""
    if not symbol:
        return False
    s = str(symbol).strip().upper().replace("/USD", "").replace("-USD", "")
    return s in CRYPTO_SYMBOLS


# Type alias for the injected quote provider.
QuoteProvider = Callable[[str], Awaitable[dict]]


# ── Default position sizing ─────────────────────────────────────────────────
# Tiny notional — this is a paper bot, the goal is to GENERATE FILLS for
# the 30-day Tier 3 unlock, not to maximise simulated PnL. Real bots will
# tune these via config when wired through the dispatcher.
_DEFAULT_QTY_BY_SYMBOL: dict[str, float] = {
    "BTC": 0.001,
    "ETH": 0.01,
    "SOL": 0.5,
    "XRP": 50.0,
    "ADA": 50.0,
    "DOGE": 200.0,
    "AVAX": 0.5,
    "LINK": 1.0,
}

# Per-signal confidence floor — anything weaker is logged as
# ``skipped`` instead of opening a fill. Mirrors the ml_paper_trader
# gate of 0.60 for "no pattern" signals.
_MIN_CONFIDENCE = 0.60


def _default_qty(symbol: str) -> float:
    return _DEFAULT_QTY_BY_SYMBOL.get(symbol.upper(), 0.01)


async def run_crypto_paper_bot(
    db: Any,
    quote_provider: QuoteProvider,
    symbols: Optional[Iterable[str]] = None,
) -> list[dict]:
    """Run one pass of the crypto paper-trading bot.

    Parameters
    ----------
    db
        Motor ``AsyncIOMotorDatabase`` handle. Trades are written
        to ``db.crypto_paper_trades`` ONLY.
    quote_provider
        Async callable taking ``symbol`` and returning ``{"price": …}``.
        Production wiring uses :func:`services.crypto_quotes.get_crypto_quote`,
        tests inject stubs.
    symbols
        Universe to trade this pass. Defaults to ``["BTC", "ETH", "SOL"]``.
        Non-crypto symbols are silently filtered (architectural firewall).

    Returns
    -------
    list[dict]
        One record per symbol: either the persisted trade document
        (``status: "open"``) or a ``{"symbol", "skipped": True,
        "reason": …}`` entry. The route layer surfaces this so a
        manual trigger shows what fired and what didn't.
    """
    syms = list(symbols) if symbols is not None else ["BTC", "ETH", "SOL"]
    results: list[dict] = []

    for raw in syms:
        symbol = str(raw).strip().upper()

        # Architectural firewall — never let a non-crypto symbol
        # land in crypto_paper_trades.
        if not is_crypto_symbol(symbol):
            results.append({
                "symbol": symbol,
                "skipped": True,
                "reason": "not_crypto_symbol",
            })
            continue

        clean_symbol = symbol.replace("/USD", "").replace("-USD", "")

        # Quote fetch — failure surfaces as a skipped record so the
        # caller knows why no trade fired. Never raises.
        try:
            quote = await quote_provider(clean_symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[crypto_paper_bot] quote fetch failed for %s: %s",
                clean_symbol, exc,
            )
            results.append({
                "symbol": clean_symbol,
                "skipped": True,
                "reason": "quote_unavailable",
            })
            continue

        price = float((quote or {}).get("price") or 0.0)
        if price <= 0:
            results.append({
                "symbol": clean_symbol,
                "skipped": True,
                "reason": "quote_unavailable",
            })
            continue

        # Starter signal logic — replace later with the
        # crypto-flavoured Strategist/Auditor loop. For now we open
        # a small LONG every pass to seed the Tier 3 fill counter
        # at modest, traceable sizes.
        direction = "LONG"
        confidence = 0.70

        if confidence < _MIN_CONFIDENCE:
            results.append({
                "symbol": clean_symbol,
                "skipped": True,
                "reason": "low_confidence",
            })
            continue

        trade = {
            "trade_id": str(uuid4()),
            "asset_class": "crypto",
            "symbol": clean_symbol,
            "pair": f"{clean_symbol}/USD",
            "direction": direction,
            "entry_price": round(price, 6),
            "quantity": _default_qty(clean_symbol),
            "confidence": confidence,
            "status": "open",
            "opened_at": datetime.now(timezone.utc),
            "source": "crypto_paper_bot",
            "metadata": {
                "lane": "crypto",
                "bot_version": "crypto_v1",
            },
        }

        try:
            await db.crypto_paper_trades.insert_one(trade)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[crypto_paper_bot] insert failed for %s: %s",
                clean_symbol, exc,
            )
            results.append({
                "symbol": clean_symbol,
                "skipped": True,
                "reason": "db_write_failed",
            })
            continue

        # Strip the Mongo-mutated ``_id`` so the record stays JSON-safe
        # for the route layer's response payload.
        trade.pop("_id", None)
        # Convert datetime to ISO for downstream consumers.
        if isinstance(trade.get("opened_at"), datetime):
            trade["opened_at"] = trade["opened_at"].isoformat()
        results.append(trade)
        logger.info(
            "[crypto_paper_bot] opened %s LONG %s @ $%.4f (trade_id=%s)",
            clean_symbol, trade["quantity"], price, trade["trade_id"],
        )

    return results
