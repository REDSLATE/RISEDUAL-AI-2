"""
Brain-agnostic intent enrichment with normalized market snapshot.

Doctrine
--------
Every directional intent posted from a brain to MC MUST carry a
``snapshot`` block with these EXACT 7 normalized field names:

    bid                         — best bid price (USD or quote ccy)
    ask                         — best ask price
    spread_bps                  — (ask - bid) / mid * 10,000 ; 9999 = unknown
    volume_24h_usd              — rolling 24h notional volume in USD
    volatility_1h               — stddev of last-hour returns (fraction)
    trend_strength              — directional momentum proxy in [-1, 1]
    exchange_liquidity_score    — venue-specific liquidity score [0, 1]

Do not invent your own names. ``spread`` ≠ ``spread_bps``. ``volume``
≠ ``volume_24h_usd``. MC's classifier reads the normalized keys; any
deviation produces missing-field defaults (``spread_bps`` → 9999)
which collapse the doctrine score on the MC side.

Wiring
------
Brains call::

    from services.intent_enrichment import enrich_intent_with_snapshot
    intent = await enrich_intent_with_snapshot(intent)

Before POSTing to ``/api/ingest/intent`` (or whatever MC endpoint the
brain uses). The helper is idempotent: if ``intent["snapshot"]``
already exists and is non-empty, this function does NOT overwrite —
brains that already build their own snapshot stay untouched.

Failure behavior
----------------
Network / parse failures produce a snapshot with sentinel values
(``spread_bps=9999``, others ``None``) and a ``snapshot_status``
sub-key explaining why. MC's classifier can then route the intent
into the right "missing data" bucket instead of treating an empty
``{}`` as a confidence signal.

Portability
-----------
Same module shape Camaro / Chevelle / REDEYE can drop in. The helper
references this brain's ``kraken_crypto_quotes`` and
``alpaca_equity_quotes`` adapters — if a fork doesn't have those,
swap the fetchers but KEEP THE FIELD NAMES.
"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ─── Canonical snapshot keys — doctrine, do not edit ────────────────
SNAPSHOT_KEYS = (
    "bid",
    "ask",
    "spread_bps",
    "volume_24h_usd",
    "volatility_1h",
    "trend_strength",
    "exchange_liquidity_score",
)

# Sentinel used when spread cannot be computed. MC reads this as
# "unknown" and routes the intent into the missing-data bucket.
SPREAD_BPS_UNKNOWN = 9999.0


def compute_spread_bps(bid: float, ask: float) -> float:
    """Canonical spread formula. Returns ``SPREAD_BPS_UNKNOWN`` for
    any input the math can't trust — never raises, never returns NaN.
    """
    try:
        b = float(bid)
        a = float(ask)
    except (TypeError, ValueError):
        return SPREAD_BPS_UNKNOWN
    if b <= 0 or a <= 0 or not math.isfinite(b) or not math.isfinite(a):
        return SPREAD_BPS_UNKNOWN
    mid = (b + a) / 2.0
    if mid <= 0:
        return SPREAD_BPS_UNKNOWN
    return round(((a - b) / mid) * 10_000.0, 2)


def _empty_snapshot(status: str) -> Dict[str, Any]:
    """Return a fully-keyed snapshot with sentinel values + a status
    string explaining why. Never returns ``{}`` — the empty-dict
    failure mode is the bug we're closing."""
    return {
        "bid": None,
        "ask": None,
        "spread_bps": SPREAD_BPS_UNKNOWN,
        "volume_24h_usd": None,
        "volatility_1h": None,
        "trend_strength": None,
        "exchange_liquidity_score": None,
        "snapshot_status": status,
    }


# ─── Crypto fetcher ─────────────────────────────────────────────────


async def fetch_crypto_snapshot(symbol: str) -> Dict[str, Any]:
    """Build a normalized crypto snapshot. Uses the existing Kraken
    public-ticker adapter for bid/ask/spread/volume, and the existing
    history adapter for the 1h volatility + trend.

    Returns the seven canonical fields. NEVER returns ``{}`` — failure
    modes return a snapshot with sentinel values + a status string.
    """
    if not symbol:
        return _empty_snapshot("missing_symbol")

    try:
        from services.kraken_crypto_quotes import (
            fetch_kraken_quotes_batch,
            _normalise_canonical,
        )
    except ImportError:
        return _empty_snapshot("kraken_adapter_missing")

    try:
        canonical = _normalise_canonical(symbol)
        quotes = await fetch_kraken_quotes_batch([canonical])
        q = quotes.get(canonical)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[snapshot] kraken fetch failed for %s: %s", symbol, exc)
        return _empty_snapshot(f"kraken_fetch_error:{type(exc).__name__}")

    if not q:
        return _empty_snapshot("kraken_no_quote")

    bid = q.get("bid")
    ask = q.get("ask")
    spread_bps = q.get("spread_bps")
    if spread_bps is None and bid and ask:
        spread_bps = compute_spread_bps(bid, ask)
    if spread_bps is None:
        spread_bps = SPREAD_BPS_UNKNOWN

    # 24h USD volume — Kraken's per-pair volume comes out in BASE
    # units; multiply by mid price for a notional USD figure. If we
    # don't have mid + base volume, leave it None (sentinel).
    # 2026-02-23 (P2 enrichment): the Alpaca equity adapter now
    # surfaces ``volume_24h_usd`` directly from the snapshot's
    # ``dailyBar``. Prefer that pre-computed value when present so
    # MC's classifier sees the same USD-volume figure across
    # crypto + equities.
    volume_24h_usd: Optional[float] = q.get("volume_24h_usd")
    if volume_24h_usd is None:
        mid = q.get("price")
        base_volume = q.get("volume_24h_base") or q.get("v24h") or q.get("volume_24h")
        if mid and base_volume:
            try:
                volume_24h_usd = round(float(mid) * float(base_volume), 2)
            except (TypeError, ValueError):
                volume_24h_usd = None

    # 1h volatility + trend strength — derived from short history.
    # If the history adapter isn't available or fails, leave None.
    volatility_1h, trend_strength = await _derive_volatility_and_trend(canonical)

    # Exchange liquidity score — Kraken majors get a flat 0.85 for
    # now (they're top-3 venues globally). A proper score would
    # weight by order-book depth + maker/taker pair-specific.
    liquidity = 0.85

    snap = {
        "bid": bid,
        "ask": ask,
        "spread_bps": spread_bps,
        "volume_24h_usd": volume_24h_usd,
        "volatility_1h": volatility_1h,
        "trend_strength": trend_strength,
        "exchange_liquidity_score": liquidity,
        "snapshot_status": "ok",
    }
    return snap


async def _derive_volatility_and_trend(canonical: str) -> tuple[Optional[float], Optional[float]]:
    """Pull a short history and compute 1h volatility (stddev of
    log-returns) and trend strength (signed momentum scaled to
    [-1, 1])."""
    try:
        from services.crypto_quotes import get_crypto_history
        bars = await get_crypto_history(canonical, lookback_bars=60)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[snapshot] history fetch failed for %s: %s", canonical, exc)
        return None, None

    if not bars or len(bars) < 5:
        return None, None

    # Log returns.
    rets = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        if prev <= 0 or cur <= 0:
            continue
        rets.append(math.log(cur / prev))

    if not rets:
        return None, None

    # 1h volatility = stddev of last 60 minute-bars worth of returns.
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    vol = round(math.sqrt(var), 6)

    # Trend strength: sign-of-net-move × abs-move-bps capped at 1.
    net = (bars[-1] / bars[0]) - 1.0 if bars[0] > 0 else 0.0
    trend = max(-1.0, min(1.0, round(net * 10.0, 4)))  # ±10% = ±1

    return vol, trend


# ─── Equity fetcher ─────────────────────────────────────────────────


async def fetch_equity_snapshot(symbol: str) -> Dict[str, Any]:
    """Build a normalized equity snapshot via the existing Alpaca
    quote adapter."""
    if not symbol:
        return _empty_snapshot("missing_symbol")

    try:
        from services.alpaca_equity_quotes import get_alpaca_equity_quote
    except ImportError:
        return _empty_snapshot("alpaca_adapter_missing")

    try:
        q = await get_alpaca_equity_quote(symbol)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[snapshot] alpaca fetch failed for %s: %s", symbol, exc)
        return _empty_snapshot(f"alpaca_fetch_error:{type(exc).__name__}")

    if not q:
        return _empty_snapshot("alpaca_no_quote")

    bid = q.get("bid")
    ask = q.get("ask")
    spread_bps = q.get("spread_bps")
    if spread_bps is None and bid and ask:
        spread_bps = compute_spread_bps(bid, ask)
    if spread_bps is None:
        spread_bps = SPREAD_BPS_UNKNOWN

    volatility_1h, trend_strength = await _derive_equity_volatility_and_trend(symbol)

    # NYSE/NASDAQ-listed names get a flat 0.95 — top-tier regulated
    # venues. Refinement would weight by ADV vs daily volume.
    liquidity = 0.95

    return {
        "bid": bid,
        "ask": ask,
        "spread_bps": spread_bps,
        "volume_24h_usd": q.get("volume_24h_usd"),
        "volatility_1h": volatility_1h,
        "trend_strength": trend_strength,
        "exchange_liquidity_score": liquidity,
        "snapshot_status": "ok",
    }


async def _derive_equity_volatility_and_trend(symbol: str) -> tuple[Optional[float], Optional[float]]:
    """Same derivation as crypto but reading from the Alpaca equity
    bars adapter when available. Returns ``(None, None)`` on any
    failure so the snapshot keeps shipping with sentinel values."""
    try:
        from services.alpaca_equity_quotes import get_alpaca_equity_history
        # Hourly bars give us a more responsive 1h vol than daily.
        bars = await get_alpaca_equity_history(
            symbol, lookback_bars=60, timeframe="1Hour",
        )
    except Exception:  # noqa: BLE001
        return None, None

    if not bars or len(bars) < 5:
        return None, None

    rets = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        if prev <= 0 or cur <= 0:
            continue
        rets.append(math.log(cur / prev))

    if not rets:
        return None, None

    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    vol = round(math.sqrt(var), 6)
    net = (bars[-1] / bars[0]) - 1.0 if bars[0] > 0 else 0.0
    trend = max(-1.0, min(1.0, round(net * 10.0, 4)))
    return vol, trend


# ─── Top-level enrichment helper ────────────────────────────────────


async def enrich_intent_with_snapshot(intent: Dict[str, Any]) -> Dict[str, Any]:
    """Attach a normalized market snapshot to an intent dict in-place.

    The snapshot lives on the outgoing body under the key
    ``doctrine_snapshot`` (NOT ``snapshot``). Per the 2026-05-21 MC
    contract, MC re-mounts the wire field ``doctrine_snapshot`` at
    ``shared_intents.<doc>.snapshot`` and ``doctrine_sidecars.<row>.snapshot``
    — gate 7 reads ``intent.snapshot.spread_bps`` and fail-closes if
    the value is missing.

    Idempotent: if ``intent["doctrine_snapshot"]`` is already populated
    (has at least one of the canonical keys), leaves it alone. Same
    for any legacy ``intent["snapshot"]`` — we read either, but we
    always WRITE to ``doctrine_snapshot``.

    Always logs a one-line completeness summary before returning so
    the operator can grep ``SNAPSHOT_ENRICHED`` to confirm intents
    aren't going out empty.
    """
    existing = intent.get("doctrine_snapshot") or intent.get("snapshot")
    if isinstance(existing, dict) and any(k in existing for k in SNAPSHOT_KEYS):
        # Ensure it's also written under the canonical key.
        intent["doctrine_snapshot"] = existing
        return intent

    lane = (intent.get("lane") or "").lower()
    symbol = intent.get("symbol", "")
    trace_id = intent.get("trace_id", "--------")

    if lane == "crypto":
        snapshot = await fetch_crypto_snapshot(symbol)
    elif lane == "equity":
        snapshot = await fetch_equity_snapshot(symbol)
    else:
        snapshot = _empty_snapshot(f"unknown_lane:{lane!r}")

    # ── 2026-05-21 contract additions ─────────────────────────────
    # MC's gate chain + sizing path also read ``price`` (mid) from
    # the snapshot. Add it here so the operator doesn't have to
    # wait on a follow-up patch to populate it.
    bid = snapshot.get("bid")
    ask = snapshot.get("ask")
    if (snapshot.get("price") is None and isinstance(bid, (int, float))
            and isinstance(ask, (int, float)) and bid > 0 and ask > 0):
        snapshot["price"] = round((float(bid) + float(ask)) / 2.0, 6)

    intent["doctrine_snapshot"] = snapshot

    populated = sum(1 for k in SNAPSHOT_KEYS if snapshot.get(k) not in (None,))
    logger.info(
        "[%s] SNAPSHOT_ENRICHED lane=%s symbol=%s populated=%d/7 status=%s "
        "spread_bps=%s",
        trace_id, lane, symbol, populated, snapshot.get("snapshot_status"),
        snapshot.get("spread_bps"),
    )

    return intent


__all__ = [
    "SNAPSHOT_KEYS",
    "SPREAD_BPS_UNKNOWN",
    "compute_spread_bps",
    "enrich_intent_with_snapshot",
    "fetch_crypto_snapshot",
    "fetch_equity_snapshot",
]
