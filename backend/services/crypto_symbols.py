"""Canonical crypto symbol registry.

Single source of truth for "is this symbol crypto?" across the codebase.
Previously CRYPTO_TICKERS was redefined in paper_trading_service,
trading_bot_service, scanner_service, ml_paper_trader — three of those
had drifted apart (one had SHIB, another didn't, another had BNB only
sometimes). Any new crypto support PR should edit this file once.

The set covers Alpaca's tradeable crypto pairs (BTC/USD, ETH/USD,
SOL/USD, etc.) plus the marquee names users will paste into bot
configs. yfinance accepts the `{TICKER}-USD` form for all of these.
Alpha Vantage's `DIGITAL_CURRENCY_DAILY` accepts the bare ticker.
"""
from __future__ import annotations

# Frozenset for cheap `in` checks; lower-case lookups handled by the helper.
CRYPTO_SYMBOLS: frozenset[str] = frozenset({
    "BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "AVAX",
    "DOT", "SHIB", "LINK", "BNB", "MATIC", "LTC", "BCH",
    "UNI", "ATOM", "ETC", "XLM", "ALGO", "AAVE", "FIL",
    "NEAR", "ICP", "APT", "ARB", "OP", "INJ", "GRT",
    "SAND", "MANA", "USDT", "USDC",
})


def is_crypto(symbol: str | None) -> bool:
    """Return True if *symbol* is a known crypto ticker.

    Tolerates None, lower-case, and the `BTC-USD` / `BTC/USD` forms
    Alpaca and yfinance use, so callers don't need to normalise first.
    """
    if not symbol:
        return False
    sym = symbol.strip().upper()
    # Strip the broker quote suffixes — `BTC/USD` and `BTC-USD` should
    # both register as crypto. Anything before the separator is the
    # base asset.
    for sep in ("/", "-"):
        if sep in sym:
            sym = sym.split(sep, 1)[0]
            break
    return sym in CRYPTO_SYMBOLS


def to_yf_crypto(symbol: str) -> str:
    """Normalise a crypto symbol to yfinance's `{TICKER}-USD` form.

    Accepts the bare ticker (`BTC`), Alpaca's `BTC/USD`, or the
    already-normalised `BTC-USD`. Always returns `{TICKER}-USD`.
    """
    sym = symbol.strip().upper()
    for sep in ("/", "-"):
        if sep in sym:
            sym = sym.split(sep, 1)[0]
            break
    return f"{sym}-USD"


def to_alpaca_crypto(symbol: str) -> str:
    """Normalise a crypto symbol to Alpaca's `BTC/USD` form.

    Used by future live execution paths (paper or live) that POST to
    Alpaca's `/v2/orders` with the slash form.
    """
    sym = symbol.strip().upper()
    for sep in ("/", "-"):
        if sep in sym:
            sym = sym.split(sep, 1)[0]
            break
    return f"{sym}/USD"
