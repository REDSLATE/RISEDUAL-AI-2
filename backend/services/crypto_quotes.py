"""Crypto Quote Wrapper — isolated from stock ``get_quote()``.

Thin async helper that the crypto bot lane uses for prices. Pricing
chain (2026-05-04 onwards):

1. **Kraken Public REST** via ``kraken_crypto_quotes`` — real-time
   bid/ask from the actual exchange, ~250ms for the whole universe,
   no API key. This is the new primary.
2. **Alpha Vantage ``CURRENCY_EXCHANGE_RATE``** via
   ``price_provider.get_crypto_quote`` — reference rate (not a
   tradeable bid/ask). Kept as a fallback on Kraken failure / unknown
   symbol.
3. **yfinance ``{TICKER}-USD``** — scraped, minute-bar delay. Bottom
   of the chain.
4. **MongoDB cache** — final stale fallback inside
   ``price_provider``.

The response shape gained ``bid``, ``ask``, ``spread_bps``, and
``last`` on 2026-05-04 so the paper-trader can simulate realistic
slippage. Legacy callers that only read ``price`` are unaffected —
mid is still returned there.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_KRAKEN_PRIMARY_ENABLED = os.environ.get(
    "KRAKEN_CRYPTO_PRIMARY_ENABLED", "1",
).strip().lower() not in ("0", "false", "off", "no")


async def get_crypto_quote(symbol: str) -> dict[str, Any]:
    """Return ``{"symbol", "price", "bid", "ask", "spread_bps",
    "last", "source"}`` for a crypto ticker.

    ``price`` is the mid when Kraken-primary serves the quote; falls
    back to last-trade / AV reference rate for downstream providers.
    ``bid`` / ``ask`` / ``spread_bps`` / ``last`` are ``None`` when
    the provider doesn't expose them.

    Falls back to ``{"symbol", "price": 0.0}`` when every source
    fails — callers treat a non-positive price as
    ``quote_unavailable`` and skip the trade. Never raises.
    """
    sym = (symbol or "").strip().upper().replace("/USD", "").replace("-USD", "")
    if not sym:
        return {"symbol": symbol, "price": 0.0}

    # 1. Kraken primary — real bid/ask, native exchange.
    if _KRAKEN_PRIMARY_ENABLED:
        try:
            from services.kraken_crypto_quotes import get_kraken_crypto_quote
            k_quote = await get_kraken_crypto_quote(sym)
            if k_quote is not None:
                return {
                    "symbol": sym,
                    "price": float(k_quote.get("price") or 0.0),
                    "bid": k_quote.get("bid"),
                    "ask": k_quote.get("ask"),
                    "spread_bps": k_quote.get("spread_bps"),
                    "last": k_quote.get("last"),
                    "source": "kraken",
                }
        except Exception as exc:
            logger.warning(
                "[crypto_quotes] kraken primary failed for %s: %s", sym, exc,
            )

    # 2-4. Legacy chain: AV → yfinance → Mongo cache. Kept intact so
    # a Kraken outage never takes the bot down.
    try:
        from services.price_provider import get_crypto_quote as _provider_get_crypto_quote
        q = await _provider_get_crypto_quote(sym)
    except Exception as exc:
        logger.warning("[crypto_quotes] provider lookup failed for %s: %s", sym, exc)
        return {"symbol": sym, "price": 0.0, "source": "none"}

    if not q:
        return {"symbol": sym, "price": 0.0, "source": "none"}

    try:
        price = float(q.get("price") or 0.0)
    except (TypeError, ValueError):
        price = 0.0

    return {
        "symbol": sym,
        "price": price,
        # Legacy providers don't carry live bid/ask — leave None
        # rather than fabricating a spread the caller might rely on.
        "bid": None,
        "ask": None,
        "spread_bps": None,
        "last": None,
        "source": q.get("source", "crypto_quotes_legacy"),
    }


async def get_crypto_history(symbol: str, lookback_bars: int = 60) -> list[float]:
    """Return the last ``lookback_bars`` daily closes for a crypto symbol.

    Pricing chain (2026-05-04 onwards):
    1. Kraken OHLC (daily interval) — native exchange data.
    2. yfinance ``{TICKER}-USD`` — scraped fallback.

    Returns an empty list on any failure — callers (Strategist)
    treat empty as ``insufficient_history`` and emit HOLD.
    """
    sym = (symbol or "").strip().upper().replace("/USD", "").replace("-USD", "")
    if not sym:
        return []

    if _KRAKEN_PRIMARY_ENABLED:
        try:
            from services.kraken_crypto_quotes import get_kraken_crypto_history
            closes = await get_kraken_crypto_history(
                sym, lookback_bars=lookback_bars,
            )
            if closes:
                return closes
        except Exception as exc:
            logger.warning(
                "[crypto_quotes] kraken history failed for %s: %s", sym, exc,
            )

    try:
        import asyncio
        import yfinance as yf

        def _fetch() -> list[float]:
            ticker = yf.Ticker(f"{sym}-USD")
            # 3 months of daily bars covers Strategist's 30-bar minimum
            # plus headroom for future longer-window indicators.
            hist = ticker.history(period="3mo")
            if hist is None or hist.empty:
                return []
            closes = [float(x) for x in hist["Close"].tolist() if x and x > 0]
            return closes[-lookback_bars:] if lookback_bars > 0 else closes

        return await asyncio.to_thread(_fetch)
    except Exception as exc:
        logger.warning("[crypto_quotes] history fetch failed for %s: %s", sym, exc)
        return []
