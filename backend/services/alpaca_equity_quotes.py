"""
Alpaca Equity Quotes — primary real-time US equity quote source.

Mirror of ``services.kraken_crypto_quotes`` for the equity lane.

Why this exists
───────────────
Before this module, the equity quote chain was:

    cache → market_data_pool (AV → Finnhub → TwelveData) → yfinance → Mongo

All multi-vendor, mostly aggregated, none with native bid/ask spread.

Alpaca provides FREE real-time US equity market data on the paper
account we already have (IEX feed):

* ``/v2/stocks/{symbol}/quotes/latest`` — single live quote
* ``/v2/stocks/snapshots?symbols=…`` — batched snapshots (live
  quote + latest trade + minute bar + daily bar in one hop)
* ``/v2/stocks/{symbol}/bars?timeframe=1Day`` — daily history

Same auth as the existing Tier-3 broker: ``APCA-API-KEY-ID`` +
``APCA-API-SECRET-KEY``. Already configured in ``backend/.env``.

What this module gives the system
────────────────────────────────
* Per-symbol ``{price, bid, ask, last, spread_bps, source, ts}``.
  Mid when bid+ask are populated, last-trade fallback when one
  side is zero (common after market close on the IEX feed).
* Batch fetch helper — one Alpaca hop for the full universe via
  ``/v2/stocks/snapshots``.
* Short-TTL in-process cache (2s) — same dedupe discipline as the
  Kraken module so a tick doesn't hammer Alpaca with N requests
  for the same symbol.
* Deterministic fallback — any failure returns ``None`` so the
  caller (the existing ``market_data_pool`` chain) keeps the AV →
  Finnhub → TwelveData → yfinance → Mongo path intact.

Asset scope: US equities only. Crypto stays on Kraken (separate
module). Options stay on Alpaca's options data feed (separate
again — handled by ``options_universe_service``).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Alpaca's market-data subdomain is independent of the trading
# base URL — the same data endpoint serves both paper and live
# accounts. Keep it env-tunable so a future operator can pin it.
ALPACA_DATA_BASE: str = os.environ.get(
    "ALPACA_DATA_BASE", "https://data.alpaca.markets",
)

ALPACA_QUOTE_TIMEOUT_SEC: float = float(
    os.environ.get("ALPACA_QUOTE_TIMEOUT_SEC", "5.0"),
)

# Per-symbol cache TTL. Short by design — the whole point of
# Alpaca-primary is real-time quotes. 2s dedupes bursts within a
# single tick while still feeling live.
QUOTE_CACHE_TTL_SEC: float = float(
    os.environ.get("ALPACA_QUOTE_CACHE_TTL_SEC", "2.0"),
)


def _alpaca_headers() -> dict[str, str] | None:
    """Lazy header build — never raises when keys are missing,
    returns ``None`` so callers can short-circuit cleanly."""
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        return None
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
    }


# In-process short-TTL cache. Keyed by canonical symbol.
_quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = asyncio.Lock()


def _now() -> float:
    return time.time()


def _normalise_symbol(symbol: str) -> str:
    """Equity symbols are upper-case, no exchange prefix."""
    return (symbol or "").strip().upper()


def _parse_quote_block(
    *, latest_quote: dict | None, latest_trade: dict | None,
) -> dict[str, Any] | None:
    """Build the canonical quote shape from Alpaca's snapshot
    components. Returns ``None`` if neither side has anything
    usable.

    Robust to the after-hours pattern where Alpaca returns a
    ``latestQuote`` with ``ap=0`` but a valid ``latestTrade.p``.
    Mid is preferred when both bid+ask are populated; falls
    through to last-trade otherwise.
    """
    bid = 0.0
    ask = 0.0
    last = 0.0

    if latest_quote:
        try:
            bid = float(latest_quote.get("bp") or 0)
            ask = float(latest_quote.get("ap") or 0)
        except (TypeError, ValueError):
            bid = ask = 0.0
    if latest_trade:
        try:
            last = float(latest_trade.get("p") or 0)
        except (TypeError, ValueError):
            last = 0.0

    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
        spread_bps = ((ask - bid) / mid) * 10_000.0 if mid > 0 else 0.0
        price = mid
    elif last > 0:
        # After-hours / one-sided book: fall back to last trade.
        # Spread is unknown; report None so consumers don't
        # mistake "no spread" for "zero spread".
        price = last
        spread_bps = None  # type: ignore[assignment]
    else:
        return None

    return {
        "price": round(price, 4),
        "bid": round(bid, 4) if bid > 0 else None,
        "ask": round(ask, 4) if ask > 0 else None,
        "last": round(last, 4) if last > 0 else None,
        "spread_bps": (
            round(spread_bps, 2) if spread_bps is not None else None
        ),
        "source": "alpaca",
        "ts": _now(),
    }


async def fetch_alpaca_equity_quotes_batch(
    symbols: list[str],
    *,
    client: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Single-hop batched snapshot for any list of US equity
    symbols. Symbols Alpaca doesn't recognise are silently dropped
    from the response — callers must treat a missing key as "not
    available on this provider" and fall back.

    Returns ``{canonical_symbol: quote_dict}``. Never raises.
    """
    if not symbols:
        return {}

    headers = _alpaca_headers()
    if headers is None:
        logger.debug("[alpaca_equity_quotes] credentials not configured")
        return {}

    wanted = sorted({_normalise_symbol(s) for s in symbols if s and s.strip()})
    if not wanted:
        return {}

    # Lazy httpx import — keeps cold-start tests independent.
    owns_client = False
    if client is None:
        import httpx
        client = httpx.AsyncClient(timeout=ALPACA_QUOTE_TIMEOUT_SEC)
        owns_client = True

    try:
        resp = await client.get(
            f"{ALPACA_DATA_BASE}/v2/stocks/snapshots",
            params={"symbols": ",".join(wanted)},
            headers=headers,
        )
        if resp.status_code >= 400:
            logger.warning(
                "[alpaca_equity_quotes] HTTP %s: %s",
                resp.status_code, resp.text[:200],
            )
            return {}
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[alpaca_equity_quotes] fetch failed: %s", exc)
        return {}
    finally:
        if owns_client:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    # Snapshot response is a flat dict ``{SYMBOL: {latestQuote,
    # latestTrade, minuteBar, dailyBar, prevDailyBar}}``.
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(body, dict):
        return out
    for sym, snap in body.items():
        if not isinstance(snap, dict):
            continue
        parsed = _parse_quote_block(
            latest_quote=snap.get("latestQuote"),
            latest_trade=snap.get("latestTrade"),
        )
        if parsed is not None:
            parsed["symbol"] = sym
            out[sym] = parsed
    return out


async def get_alpaca_equity_quote(
    symbol: str, *, bypass_cache: bool = False,
) -> dict[str, Any] | None:
    """Single-symbol live quote with short TTL cache.

    Returns the canonical quote shape or ``None`` when Alpaca
    can't provide it (unknown symbol, network failure, parse
    error, missing credentials). The caller is expected to fall
    back to the legacy ``market_data_pool`` chain on ``None``.
    """
    canonical = _normalise_symbol(symbol)
    if not canonical:
        return None

    now = _now()
    if not bypass_cache:
        async with _cache_lock:
            hit = _quote_cache.get(canonical)
            if hit is not None and (now - hit[0]) < QUOTE_CACHE_TTL_SEC:
                return dict(hit[1])  # defensive copy

    batch = await fetch_alpaca_equity_quotes_batch([canonical])
    quote = batch.get(canonical)
    if quote is None:
        return None

    async with _cache_lock:
        _quote_cache[canonical] = (now, dict(quote))
    return quote


# ── Daily bars / history ──────────────────────────────────────────


async def get_alpaca_equity_history(
    symbol: str, *, lookback_bars: int = 90,
    timeframe: str = "1Day",
    client: Any | None = None,
) -> list[float]:
    """Daily close history via ``/v2/stocks/{symbol}/bars``.

    Returns a list of closes newest-last, length ≤ ``lookback_bars``.
    Empty list on any failure so callers (Strategist) treat it as
    ``insufficient_history`` exactly like the yfinance path.
    """
    canonical = _normalise_symbol(symbol)
    if not canonical:
        return []

    headers = _alpaca_headers()
    if headers is None:
        return []

    owns_client = False
    if client is None:
        import httpx
        client = httpx.AsyncClient(timeout=ALPACA_QUOTE_TIMEOUT_SEC * 2)
        owns_client = True

    try:
        resp = await client.get(
            f"{ALPACA_DATA_BASE}/v2/stocks/{canonical}/bars",
            params={"timeframe": timeframe, "limit": max(1, lookback_bars)},
            headers=headers,
        )
        if resp.status_code >= 400:
            return []
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[alpaca_equity_quotes] bars fetch failed for %s: %s",
            canonical, exc,
        )
        return []
    finally:
        if owns_client:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    bars = body.get("bars") if isinstance(body, dict) else None
    if not isinstance(bars, list):
        return []

    closes: list[float] = []
    for bar in bars:
        try:
            c = float(bar.get("c") or 0)
            if c > 0:
                closes.append(c)
        except (TypeError, ValueError):
            continue

    if lookback_bars > 0:
        return closes[-lookback_bars:]
    return closes


def invalidate_cache() -> None:
    """Test / admin helper — wipes the in-process quote cache."""
    _quote_cache.clear()
