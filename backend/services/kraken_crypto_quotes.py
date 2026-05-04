"""
Kraken Crypto Quotes — primary real-time quote source.

Why this exists
───────────────
Before this module, the crypto-bot quote chain was:

    cache → Alpha Vantage (CURRENCY_EXCHANGE_RATE) → yfinance ``-USD``

Both upstreams are aggregated / delayed. Alpha Vantage's
``CURRENCY_EXCHANGE_RATE`` is a reference rate, not a tradeable
bid/ask. yfinance is scraped minute-bar data. For a bot that makes
real fill decisions, the delta matters.

Kraken Public REST ``/0/public/Ticker`` returns:
  * ``c`` — last-trade price
  * ``b`` — best bid + whole-lot volume
  * ``a`` — best ask + whole-lot volume
  * ``v`` — 24h rolling volume

All free, no API key, ~1 req/sec rate-limit. A single batched call
with 11 majors returns in ~250ms with native exchange mid + spread.

What this module gives the bot
──────────────────────────────
* Per-symbol ``{price, bid, ask, spread_bps, source, ts}`` —
  ``price`` is mid when bid/ask are present, last-trade otherwise.
* Batch fetch helper — one Kraken hop for the whole universe.
* Symbol mapping that handles Kraken's historical 4-letter
  prefix quirks (XXBT, XETH, XDG, XXRP) via an explicit canonical
  table. Response keys are normalised back to our canonical names
  before we return.
* Deterministic fallback behaviour — any failure returns ``None``
  so the caller (the existing chain in ``crypto_quotes``) keeps
  working with AV / yfinance as before.

Asset scope: crypto ONLY. The equity xStocks path is a separate
module (``kraken_equity_shadow_service``) and is still geo-blocked.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

KRAKEN_API_BASE: str = os.environ.get(
    "KRAKEN_API_BASE", "https://api.kraken.com",
)
KRAKEN_QUOTE_TIMEOUT_SEC: float = float(
    os.environ.get("KRAKEN_QUOTE_TIMEOUT_SEC", "5.0"),
)
# Per-symbol cache TTL. Short by design — the whole point of
# Kraken-primary is real-time quotes. 2s dedupes bursts within a
# single tick while still feeling live.
QUOTE_CACHE_TTL_SEC: float = float(
    os.environ.get("KRAKEN_QUOTE_CACHE_TTL_SEC", "2.0"),
)

# Canonical symbol → Kraken pair code we SEND.
# Kraken accepts either ``XBTUSD`` or ``XXBTZUSD`` as the query
# value; we use the shorter form and let the response normaliser
# handle the X/Z prefixed keys.
_CANONICAL_TO_KRAKEN: dict[str, str] = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "BNB": "BNBUSD",
    "XRP": "XRPUSD",
    "ADA": "ADAUSD",
    "AVAX": "AVAXUSD",
    "LINK": "LINKUSD",
    "DOGE": "XDGUSD",        # Kraken uses XDG for Dogecoin, not DOGE
    "DOT": "DOTUSD",
    # Polygon rebranded MATIC → POL on Kraken in 2024. We keep
    # the canonical symbol "MATIC" inside our universe (that's what
    # the bot / ticker abandonment / prediction rows use) but route
    # it to ``POLUSD`` on the wire.
    "MATIC": "POLUSD",
    "POL": "POLUSD",
}

# Kraken response key → canonical symbol. Covers the historical
# X/Z prefixed pair names Kraken returns on the majors.
_KRAKEN_RESP_TO_CANONICAL: dict[str, str] = {
    "XXBTZUSD": "BTC",
    "XBTUSD": "BTC",
    "XETHZUSD": "ETH",
    "ETHUSD": "ETH",
    "XXRPZUSD": "XRP",
    "XRPUSD": "XRP",
    "XDGUSD": "DOGE",
    "DOGEUSD": "DOGE",
    "SOLUSD": "SOL",
    "BNBUSD": "BNB",
    "ADAUSD": "ADA",
    "AVAXUSD": "AVAX",
    "LINKUSD": "LINK",
    "DOTUSD": "DOT",
    "MATICUSD": "MATIC",
    "POLUSD": "MATIC",
}


# In-process short-TTL cache. Keyed by canonical symbol. Cleared
# implicitly via TTL — no explicit invalidation needed.
_quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = asyncio.Lock()


def _now() -> float:
    return time.time()


def _normalise_canonical(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    # Strip quote currency suffixes callers may pass ("BTC/USD",
    # "BTC-USD", "BTCUSDT"), normalise to bare base.
    for tail in ("/USD", "-USD", "USDT", "USDC", "USD"):
        if s.endswith(tail) and len(s) > len(tail):
            s = s[: -len(tail)]
            break
    return s


def _to_kraken_pair(canonical: str) -> str | None:
    return _CANONICAL_TO_KRAKEN.get(canonical)


def _from_kraken_pair(kraken_pair: str) -> str | None:
    return _KRAKEN_RESP_TO_CANONICAL.get(kraken_pair)


def _parse_ticker_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Extract our canonical shape from one Kraken Ticker row.

    Returns ``None`` on malformed input so the caller can skip
    silently without raising.
    """
    try:
        bid = float(row.get("b", [0])[0])
        ask = float(row.get("a", [0])[0])
        last = float(row.get("c", [0])[0])
    except (KeyError, IndexError, TypeError, ValueError):
        return None

    if bid <= 0 or ask <= 0 or last <= 0:
        return None

    mid = (bid + ask) / 2.0
    spread_bps = ((ask - bid) / mid) * 10_000.0 if mid > 0 else 0.0

    return {
        "price": round(mid, 8),
        "bid": round(bid, 8),
        "ask": round(ask, 8),
        "last": round(last, 8),
        "spread_bps": round(spread_bps, 2),
        "source": "kraken",
        "ts": _now(),
    }


async def fetch_kraken_quotes_batch(
    symbols: list[str],
    *,
    client: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Single-hop batched quote for any subset of the supported
    crypto majors. Symbols unknown to Kraken are silently dropped
    from the response — callers must treat a missing key as "not
    available on this provider" and fall back.

    Returns ``{canonical_symbol: quote_dict}``. Never raises.
    """
    if not symbols:
        return {}

    # Resolve each canonical symbol to its Kraken pair code. Drop
    # unsupported symbols here so the request itself stays valid.
    wanted: list[tuple[str, str]] = []
    for s in symbols:
        canonical = _normalise_canonical(s)
        k = _to_kraken_pair(canonical)
        if k is not None:
            wanted.append((canonical, k))

    if not wanted:
        return {}

    pair_query = ",".join(k for _, k in wanted)

    # Lazy httpx import — keeps cold-start tests that stub this
    # module independent of the HTTP layer.
    owns_client = False
    if client is None:
        import httpx
        client = httpx.AsyncClient(timeout=KRAKEN_QUOTE_TIMEOUT_SEC)
        owns_client = True

    try:
        resp = await client.get(
            f"{KRAKEN_API_BASE}/0/public/Ticker",
            params={"pair": pair_query},
        )
        if resp.status_code >= 400:
            logger.warning(
                "[kraken_crypto_quotes] HTTP %s: %s",
                resp.status_code, resp.text[:200],
            )
            return {}
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[kraken_crypto_quotes] fetch failed: %s", exc)
        return {}
    finally:
        if owns_client:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    rows = body.get("result") or {}
    if not rows:
        # ``error`` list may be set (e.g., unknown pair) even when
        # some pairs resolved — this is OK because ``result`` will
        # contain the ones that did.
        return {}

    out: dict[str, dict[str, Any]] = {}
    for k_pair, row in rows.items():
        canonical = _from_kraken_pair(k_pair)
        if canonical is None:
            continue
        parsed = _parse_ticker_row(row)
        if parsed is not None:
            parsed["symbol"] = canonical
            out[canonical] = parsed
    return out


async def get_kraken_crypto_quote(
    symbol: str, *, bypass_cache: bool = False,
) -> dict[str, Any] | None:
    """Single-symbol live quote with short TTL cache.

    Returns the canonical quote shape or ``None`` when Kraken can't
    provide it (unknown symbol, network failure, parse error). The
    caller is expected to fall back to the legacy AV / yfinance
    chain on ``None``.
    """
    canonical = _normalise_canonical(symbol)
    if not canonical:
        return None
    if _to_kraken_pair(canonical) is None:
        return None

    # WebSocket-fed snapshot — sub-100ms freshness when the stream
    # is alive. Falls through to REST below on miss / stale entry.
    if not bypass_cache:
        try:
            from services.kraken_ws_stream import get_streamed_quote
            ws_quote = await get_streamed_quote(canonical)
            if ws_quote is not None:
                return ws_quote
        except Exception as exc:  # noqa: BLE001
            logger.debug("[kraken_crypto_quotes] ws lookup failed: %s", exc)

    now = _now()
    if not bypass_cache:
        async with _cache_lock:
            hit = _quote_cache.get(canonical)
            if hit is not None and (now - hit[0]) < QUOTE_CACHE_TTL_SEC:
                return dict(hit[1])  # defensive copy

    batch = await fetch_kraken_quotes_batch([canonical])
    quote = batch.get(canonical)
    if quote is None:
        return None

    async with _cache_lock:
        _quote_cache[canonical] = (now, dict(quote))
    return quote


# ── OHLC / history ────────────────────────────────────────────────


# Kraken OHLC interval in minutes. 1440 = daily (matches the
# existing yfinance ``period="3mo"`` daily cadence).
_DEFAULT_OHLC_INTERVAL_MIN: int = int(
    os.environ.get("KRAKEN_OHLC_INTERVAL_MIN", "1440"),
)


async def get_kraken_crypto_history(
    symbol: str, *, lookback_bars: int = 60,
    interval_min: int = _DEFAULT_OHLC_INTERVAL_MIN,
    client: Any | None = None,
) -> list[float]:
    """Daily (by default) close history via ``/0/public/OHLC``.

    Returns a list of closes newest-last, length ≤ ``lookback_bars``.
    Empty list on any failure so the caller (Strategist) treats it
    as ``insufficient_history`` exactly like the yfinance path.
    """
    canonical = _normalise_canonical(symbol)
    if not canonical:
        return []
    kpair = _to_kraken_pair(canonical)
    if kpair is None:
        return []

    owns_client = False
    if client is None:
        import httpx
        client = httpx.AsyncClient(timeout=KRAKEN_QUOTE_TIMEOUT_SEC * 2)
        owns_client = True

    try:
        resp = await client.get(
            f"{KRAKEN_API_BASE}/0/public/OHLC",
            params={"pair": kpair, "interval": interval_min},
        )
        if resp.status_code >= 400:
            return []
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[kraken_crypto_quotes] OHLC fetch failed for %s: %s",
            canonical, exc,
        )
        return []
    finally:
        if owns_client:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    rows = body.get("result") or {}
    # The dict has one OHLC key + a ``last`` int. Filter out ``last``.
    ohlc_rows: list[list] = []
    for k, v in rows.items():
        if k == "last":
            continue
        if isinstance(v, list):
            ohlc_rows = v
            break

    # Each row: [time, open, high, low, close, vwap, volume, count]
    closes: list[float] = []
    for row in ohlc_rows:
        try:
            c = float(row[4])
            if c > 0:
                closes.append(c)
        except (TypeError, ValueError, IndexError):
            continue

    if lookback_bars > 0:
        return closes[-lookback_bars:]
    return closes


def invalidate_cache() -> None:
    """Test / admin helper — wipes the in-process quote cache."""
    _quote_cache.clear()
