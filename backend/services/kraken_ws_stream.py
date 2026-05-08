"""
Kraken WebSocket Stream — push-based crypto quotes.

Why this exists
───────────────
``services.kraken_crypto_quotes`` polls REST every time the bot or
the Spread Watch tile asks for a quote. Even with a 2s in-process
TTL cache, that's still N requests per tick. Kraken's WebSocket v2
``ticker`` channel pushes every BBO change in real time — once a
single subscription is open, every consumer gets sub-100ms freshness
for free.

Architecture
────────────
* One background ``asyncio.Task`` per backend process maintains a
  single open connection to ``wss://ws.kraken.com/v2``.
* Inbound ticker events update an in-memory snapshot dict keyed by
  canonical symbol (BTC / ETH / SOL / etc.).
* ``get_streamed_quote(symbol)`` returns the most recent snapshot
  if it's fresher than ``MAX_STALENESS_SEC`` (default 30s),
  otherwise ``None`` so the caller falls through to REST.
* On disconnect / error, exponential backoff reconnect (1s → 2s →
  4s → 8s, capped at 30s) with subscription replay.

Lifecycle
─────────
* ``start_kraken_ws_stream(symbols)`` — idempotent. Spawns the
  background task once; subsequent calls are no-ops.
* ``stop_kraken_ws_stream()`` — sets the cancel flag + closes the
  socket. Used at server shutdown.
* Gated by ``KRAKEN_WS_STREAM_ENABLED`` (default ON). Set to "0"
  to fall back to REST-only behaviour without code changes.

Resilience invariants
─────────────────────
* The stream NEVER raises into the calling code. A bad payload or
  socket drop logs at WARNING + retries silently.
* The stream NEVER blocks the bot. ``get_streamed_quote`` is a
  zero-await dict lookup.
* Stale snapshots (older than ``MAX_STALENESS_SEC``) are treated
  as missing — protects against silent socket-dead scenarios.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


KRAKEN_WS_URL: str = os.environ.get(
    "KRAKEN_WS_URL", "wss://ws.kraken.com/v2",
)
MAX_STALENESS_SEC: float = float(
    os.environ.get("KRAKEN_WS_MAX_STALENESS_SEC", "30.0"),
)
RECONNECT_BACKOFF_BASE_SEC: float = float(
    os.environ.get("KRAKEN_WS_BACKOFF_BASE_SEC", "1.0"),
)
RECONNECT_BACKOFF_MAX_SEC: float = float(
    os.environ.get("KRAKEN_WS_BACKOFF_MAX_SEC", "30.0"),
)


# Canonical symbol → Kraken ticker pair name (WS uses ``XBT/USD``
# format with the slash, distinct from REST's ``XBTUSD``).
_CANONICAL_TO_WS_PAIR: dict[str, str] = {
    "BTC": "BTC/USD",
    "ETH": "ETH/USD",
    "SOL": "SOL/USD",
    "BNB": "BNB/USD",
    "XRP": "XRP/USD",
    "ADA": "ADA/USD",
    "AVAX": "AVAX/USD",
    "LINK": "LINK/USD",
    "DOGE": "DOGE/USD",
    "DOT": "DOT/USD",
    "MATIC": "POL/USD",  # Polygon rebrand
}

# Reverse map for inbound updates — Kraken WS sends back the
# subscribed pair name verbatim, so this covers both directions.
_WS_PAIR_TO_CANONICAL: dict[str, str] = {
    v: k for k, v in _CANONICAL_TO_WS_PAIR.items()
}


# In-process snapshot store. Keyed by canonical symbol. Each entry:
# (received_at, {price, bid, ask, last, spread_bps, source, ts}).
_snapshot: dict[str, tuple[float, dict[str, Any]]] = {}
_snapshot_lock = asyncio.Lock()

_stream_task: asyncio.Task | None = None
_stream_stop = asyncio.Event()


def _now() -> float:
    return time.time()


def _build_canonical_quote(
    *, canonical: str, bid: float, ask: float, last: float,
) -> dict[str, Any]:
    mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else (last or 0.0)
    spread_bps = (
        ((ask - bid) / mid) * 10_000.0 if (bid > 0 and ask > 0 and mid > 0) else None
    )
    return {
        "symbol": canonical,
        "price": round(mid, 8) if mid > 0 else 0.0,
        "bid": round(bid, 8) if bid > 0 else None,
        "ask": round(ask, 8) if ask > 0 else None,
        "last": round(last, 8) if last > 0 else None,
        "spread_bps": round(spread_bps, 2) if spread_bps is not None else None,
        "source": "kraken_ws",
        "ts": _now(),
    }


async def _process_ticker_msg(msg: dict[str, Any]) -> None:
    """Apply one WS ticker update to the snapshot store.

    Kraken v2 ticker payload shape::

        {"channel": "ticker",
         "type": "snapshot" | "update",
         "data": [
             {"symbol": "BTC/USD", "bid": 80000.1, "ask": 80000.2,
              "last": 80000.15, ...},
         ]}
    """
    if not isinstance(msg, dict):
        return
    if msg.get("channel") != "ticker":
        return
    data = msg.get("data") or []
    if not isinstance(data, list):
        return
    now = _now()
    async with _snapshot_lock:
        for row in data:
            try:
                pair_name = row.get("symbol")
                canonical = _WS_PAIR_TO_CANONICAL.get(pair_name)
                if canonical is None:
                    continue
                bid = float(row.get("bid") or 0)
                ask = float(row.get("ask") or 0)
                last = float(row.get("last") or 0)
                quote = _build_canonical_quote(
                    canonical=canonical, bid=bid, ask=ask, last=last,
                )
                if quote["price"] > 0:
                    _snapshot[canonical] = (now, quote)
            except (TypeError, ValueError, KeyError) as exc:
                logger.debug("[kraken_ws] malformed row skipped: %s", exc)


async def _run_stream(symbols: list[str]) -> None:
    """Background loop. Reconnects with exponential backoff on any
    disconnect. Replays the subscription on every reconnect."""
    pairs = [
        _CANONICAL_TO_WS_PAIR[s] for s in symbols
        if s in _CANONICAL_TO_WS_PAIR
    ]
    if not pairs:
        logger.warning("[kraken_ws] no recognisable symbols — stream not starting")
        return

    backoff = RECONNECT_BACKOFF_BASE_SEC

    while not _stream_stop.is_set():
        try:
            import websockets  # lazy import — keeps test isolation clean
        except ImportError:
            logger.warning("[kraken_ws] websockets package not installed")
            return

        try:
            async with websockets.connect(
                KRAKEN_WS_URL, open_timeout=10, close_timeout=5,
                ping_interval=20, ping_timeout=15,
            ) as ws:
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "params": {"channel": "ticker", "symbol": pairs},
                }))
                logger.info(
                    "[kraken_ws] connected; subscribed to %d pair(s)",
                    len(pairs),
                )
                backoff = RECONNECT_BACKOFF_BASE_SEC  # reset on success
                while not _stream_stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    except asyncio.TimeoutError:
                        # No message in 60s — let the loop's ping
                        # frame handle keep-alive. Continue.
                        continue
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    await _process_ticker_msg(msg)
        except asyncio.CancelledError:
            logger.info("[kraken_ws] stream cancelled")
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[kraken_ws] stream loop error: %s — reconnect in %.1fs",
                exc, backoff,
            )

        if _stream_stop.is_set():
            return
        try:
            await asyncio.wait_for(
                _stream_stop.wait(), timeout=backoff,
            )
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_SEC)


def start_kraken_ws_stream(symbols: list[str]) -> bool:
    """Spawn the stream task once. Idempotent — subsequent calls
    are no-ops while a task is alive. Returns True if the task was
    started by this call, False otherwise."""
    if os.environ.get(
        "KRAKEN_WS_STREAM_ENABLED", "1",
    ).strip().lower() in ("0", "false", "off", "no"):
        return False
    global _stream_task
    if _stream_task is not None and not _stream_task.done():
        return False
    _stream_stop.clear()
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return False
    _stream_task = loop.create_task(_run_stream(list(symbols)))
    return True


async def stop_kraken_ws_stream() -> None:
    global _stream_task
    _stream_stop.set()
    if _stream_task is not None:
        _stream_task.cancel()
        try:
            await _stream_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _stream_task = None


async def get_streamed_quote(symbol: str) -> dict[str, Any] | None:
    """Lookup the freshest streamed snapshot for a canonical symbol.

    Returns ``None`` when no snapshot exists OR when the most
    recent snapshot is stale (older than ``MAX_STALENESS_SEC``).
    Callers fall through to REST.
    """
    canonical = (symbol or "").strip().upper()
    if not canonical:
        return None
    async with _snapshot_lock:
        hit = _snapshot.get(canonical)
    if hit is None:
        return None
    received_at, quote = hit
    if (_now() - received_at) > MAX_STALENESS_SEC:
        return None
    return dict(quote)


def stream_status() -> dict[str, Any]:
    """Inform-only diagnostic snapshot for the admin probe."""
    alive = _stream_task is not None and not _stream_task.done()
    return {
        "alive": alive,
        "snapshot_count": len(_snapshot),
        "tracked_symbols": sorted(_snapshot.keys()),
        "max_staleness_sec": MAX_STALENESS_SEC,
        "ws_url": KRAKEN_WS_URL,
        "enabled": os.environ.get(
            "KRAKEN_WS_STREAM_ENABLED", "1",
        ).strip().lower() not in ("0", "false", "off", "no"),
    }


def _reset_for_tests() -> None:
    """Test helper — never call from production paths."""
    global _stream_task
    _snapshot.clear()
    _stream_task = None
    _stream_stop.clear()
