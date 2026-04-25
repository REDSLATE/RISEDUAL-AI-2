"""Crypto Quote Wrapper — isolated from stock ``get_quote()``.

Thin async helper that the crypto bot lane uses for prices. It
delegates to the existing ``get_crypto_quote()`` in
``services/price_provider.py`` (the same source ``/api/crypto/prices``
uses), so we don't duplicate quote logic — but importantly, the
crypto bot only ever talks to THIS module, never to ``get_quote()``
directly. That keeps the equity quote path completely insulated:
even if a future change tweaks ``get_crypto_quote`` (e.g. swaps in
Coinbase or Alpaca crypto as primary), the equity path is untouched.

Kept deliberately tiny — one function, one job.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def get_crypto_quote(symbol: str) -> dict[str, Any]:
    """Return ``{"symbol": …, "price": float}`` for a crypto ticker.

    Falls back to ``{"symbol", "price": 0.0}`` when the upstream
    source can't return a price — callers (the bot worker) treat
    a non-positive price as ``quote_unavailable`` and skip the
    trade. Never raises.
    """
    sym = (symbol or "").strip().upper().replace("/USD", "").replace("-USD", "")
    if not sym:
        return {"symbol": symbol, "price": 0.0}

    try:
        # Reuse the existing crypto-only path in price_provider
        # (Alpha Vantage CURRENCY_EXCHANGE_RATE → yfinance ``-USD``
        # fallback → MongoDB cache). That is the same source
        # ``/api/crypto/prices`` already serves to the UI.
        from services.price_provider import get_crypto_quote as _provider_get_crypto_quote
        q = await _provider_get_crypto_quote(sym)
    except Exception as exc:
        logger.warning("[crypto_quotes] provider lookup failed for %s: %s", sym, exc)
        return {"symbol": sym, "price": 0.0}

    if not q:
        return {"symbol": sym, "price": 0.0}

    try:
        price = float(q.get("price") or 0.0)
    except (TypeError, ValueError):
        price = 0.0

    return {
        "symbol": sym,
        "price": price,
        "source": q.get("source", "crypto_quotes"),
    }
