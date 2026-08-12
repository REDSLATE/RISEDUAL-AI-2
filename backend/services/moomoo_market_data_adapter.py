"""MoomooMarketDataAdapter — normalized market-data facade over OpenD.

Design constraints
------------------
* Alpha never calls MooMoo directly. This adapter exists so Alpha's
  ``Level2Confirmation`` can consume a normalized ``Level2Snapshot``
  regardless of the underlying broker.
* Connection to OpenD is lazy. When OpenD is unavailable (secret not
  set, host down, entitlement missing), every method returns ``None``
  and callers fall back to their existing neutral path.
* Symbols are translated to MooMoo format (``US.AAPL``) internally so
  the rest of the codebase keeps using bare tickers.
* Raw high-frequency market data does NOT go to MongoDB. Callers that
  want telemetry write compact summaries to the SQLite hot store.

Secrets model
-------------
No secrets live here. The adapter reads:
    ``MOOMOO_OPEND_HOST`` (default ``moomoo-opend``)
    ``MOOMOO_OPEND_PORT`` (default 11111)
Actual MooMoo account/login/unlock credentials are held only by the
OpenD process itself (in ``OpenD.xml`` on the deployment target).
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_ctx: Any = None


def _host() -> str:
    return (os.environ.get("MOOMOO_OPEND_HOST") or "moomoo-opend").strip()


def _port() -> int:
    try:
        return int(os.environ.get("MOOMOO_OPEND_PORT") or "11111")
    except (TypeError, ValueError):
        return 11111


def _to_moomoo_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    return s if s.startswith("US.") else f"US.{s}"


def _from_moomoo_symbol(symbol: str) -> str:
    s = str(symbol or "").upper()
    return s[3:] if s.startswith("US.") else s


def _get_quote_ctx() -> Any:
    """Return a shared OpenQuoteContext, or None when OpenD is unreachable."""
    global _ctx
    with _LOCK:
        if _ctx is not None:
            return _ctx
        try:
            from moomoo import OpenQuoteContext
            _ctx = OpenQuoteContext(host=_host(), port=_port())
            logger.info("[moomoo_md] OpenD quote context opened %s:%d", _host(), _port())
        except Exception as exc:  # noqa: BLE001
            logger.warning("[moomoo_md] OpenD quote context failed: %s", exc)
            _ctx = None
        return _ctx


def close() -> None:
    """Close the shared quote context. Safe to call from a shutdown hook."""
    global _ctx
    with _LOCK:
        if _ctx is None:
            return
        try:
            _ctx.close()
        except Exception:  # noqa: BLE001
            pass
        _ctx = None


# ─── normalized DTOs ─────────────────────────────────────────────


@dataclass
class QuoteSnapshot:
    symbol: str
    last: float
    bid: float
    ask: float
    volume: float
    ts: str


@dataclass
class OrderBookSnapshot:
    symbol: str
    bid_levels: list[tuple[float, float]]  # [(price, size), ...]
    ask_levels: list[tuple[float, float]]
    ts: str


# ─── operations ──────────────────────────────────────────────────


def _row_first(df: Any) -> Optional[dict]:
    try:
        rows = df.to_dict("records")
    except Exception:  # noqa: BLE001
        return None
    return rows[0] if rows else None


def snapshot_quote(symbol: str) -> Optional[QuoteSnapshot]:
    ctx = _get_quote_ctx()
    if ctx is None:
        return None
    sym = _to_moomoo_symbol(symbol)
    try:
        from moomoo import RET_OK, SubType
        ret, _err = ctx.subscribe([sym], [SubType.QUOTE], subscribe_push=False)
        if ret != RET_OK:
            return None
        ret, data = ctx.get_stock_quote([sym])
        if ret != RET_OK:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[moomoo_md] snapshot_quote failed: %s", exc)
        return None
    row = _row_first(data)
    if not row:
        return None
    try:
        return QuoteSnapshot(
            symbol=_from_moomoo_symbol(row.get("code") or sym),
            last=float(row.get("last_price") or 0.0),
            bid=float(row.get("bid_price") or 0.0),
            ask=float(row.get("ask_price") or 0.0),
            volume=float(row.get("volume") or 0.0),
            ts=str(row.get("data_time") or ""),
        )
    except (TypeError, ValueError):
        return None


def snapshot_order_book(symbol: str) -> Optional[OrderBookSnapshot]:
    """Return L2 depth if the account is entitled. None otherwise."""
    ctx = _get_quote_ctx()
    if ctx is None:
        return None
    sym = _to_moomoo_symbol(symbol)
    try:
        from moomoo import RET_OK, SubType
        ret, _err = ctx.subscribe([sym], [SubType.ORDER_BOOK], subscribe_push=False)
        if ret != RET_OK:
            return None
        ret, data = ctx.get_order_book(sym)
        if ret != RET_OK:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[moomoo_md] snapshot_order_book failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    try:
        bids = [(float(p), float(v)) for (p, v, *_rest) in (data.get("Bid") or [])]
        asks = [(float(p), float(v)) for (p, v, *_rest) in (data.get("Ask") or [])]
    except (TypeError, ValueError):
        return None
    return OrderBookSnapshot(
        symbol=_from_moomoo_symbol(data.get("code") or sym),
        bid_levels=bids,
        ask_levels=asks,
        ts=str(data.get("svr_recv_time_bid") or data.get("svr_recv_time_ask") or ""),
    )


def to_level2_snapshot(symbol: str) -> Optional[dict]:
    """Adapt MooMoo depth into the dict shape Alpha's Level2Confirmation
    expects. Returns None when depth is not available so callers keep
    the neutral 0.50 fallback in place.
    """
    ob = snapshot_order_book(symbol)
    if ob is None or not ob.bid_levels or not ob.ask_levels:
        return None
    top_bid_size = ob.bid_levels[0][1]
    top_ask_size = ob.ask_levels[0][1]
    total = top_bid_size + top_ask_size
    imbalance = (top_bid_size - top_ask_size) / total if total > 0 else 0.0
    depth_bid = sum(sz for _, sz in ob.bid_levels[:5])
    depth_ask = sum(sz for _, sz in ob.ask_levels[:5])
    return {
        "symbol": ob.symbol,
        "bid_size": top_bid_size,
        "ask_size": top_ask_size,
        "book_imbalance": imbalance,
        "tape_delta": 0.0,   # tape requires TICKER subscription; V1 leaves this at 0
        "cancel_rate_bid": 0.0,
        "cancel_rate_ask": 0.0,
        "imbalance_persistence_ms": 0,
        "depth_bid_5": depth_bid,
        "depth_ask_5": depth_ask,
        "source": "moomoo",
    }


def market_state(symbol: str) -> Optional[dict]:
    ctx = _get_quote_ctx()
    if ctx is None:
        return None
    sym = _to_moomoo_symbol(symbol)
    try:
        from moomoo import RET_OK
        ret, data = ctx.get_market_state([sym])
        if ret != RET_OK:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[moomoo_md] market_state failed: %s", exc)
        return None
    row = _row_first(data)
    if not row:
        return None
    return {"symbol": _from_moomoo_symbol(row.get("code") or sym),
            "state": str(row.get("market_state") or "unknown")}


def entitlements() -> Optional[dict]:
    """Query current subscription usage. Exposes ``remain`` / ``total_used``
    so the operator can see if we're near the MooMoo quota cap."""
    ctx = _get_quote_ctx()
    if ctx is None:
        return None
    try:
        from moomoo import RET_OK
        ret, data = ctx.query_subscription(is_all_conn=True)
        if ret != RET_OK:
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[moomoo_md] entitlements failed: %s", exc)
        return None
    if isinstance(data, dict):
        return {k: data.get(k) for k in ("remain", "total_used", "sub_list", "own_conn_used")}
    return {"raw": str(data)[:400]}


def status() -> dict:
    """Non-secret operational status for the admin UI."""
    return {
        "opend_host": _host(),
        "opend_port": _port(),
        "connected": _ctx is not None,
        "sdk": "moomoo-api",
        "market": "US",
        "security_firm": "FUTUINC",
    }


__all__ = [
    "QuoteSnapshot", "OrderBookSnapshot",
    "snapshot_quote", "snapshot_order_book",
    "to_level2_snapshot", "market_state", "entitlements",
    "status", "close",
]
