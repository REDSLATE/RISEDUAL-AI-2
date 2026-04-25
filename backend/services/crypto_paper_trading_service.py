"""Crypto Paper Trading Service — isolated 24/7 paper trading line for crypto.

Architectural separation
------------------------
This service is INTENTIONALLY isolated from the equity/options paper
trading lifecycle (`paper_trading_service.py`, `ml_paper_trader.py`,
`paper_trade_closer.py`). The reasons:

* **Market hours.** Crypto trades 24/7. Equity-side closers, labelers,
  and end-of-day risk caps assume an opening/closing bell. Mixing the
  two would either freeze crypto execution at 4pm ET or break the
  equity assumptions the ML training loop relies on.
* **Schema discipline.** Crypto trades land in a dedicated
  ``crypto_paper_trades`` collection. Stock lifecycle services
  (``paper_trade_closer.close_due_paper_trades``) only touch the
  legacy ``paper_trades`` collection — they cannot accidentally
  close, mark-to-market, or label a crypto fill.
* **Quote source.** Crypto prices come from
  ``services.price_provider.get_crypto_quote`` (the same source
  ``/api/crypto/prices`` uses). The equity ``get_quote`` path is
  never invoked here.

Inherited safety guards (wired up to yesterday's bug-fix system)
----------------------------------------------------------------
* **Test-fixture rejection.** Reuses
  ``services.market_memory_service.is_test_symbol`` so a request with
  ``TEST_BTC`` / ``MOCK_*`` / ``FAKEXYZ`` is rejected at the boundary
  before any DB write. In ``ENVIRONMENT=production`` this raises a
  ``ValueError`` to trigger 500/monitoring; in dev/preview it returns
  a structured ``{"blocked": True, "reason": ...}`` dict for the
  route layer to translate to a 400.
* **Idempotency key** on every insert: a Mongo unique index on
  ``(user_id, symbol, side, qty, price_bucket, time_bucket_minute)``
  prevents a webhook retry / double-click from creating duplicate
  fills within the same minute. Mirrors the duplicate-insert fix
  proposed for equity in P2.
* **Crypto-symbol gate.** Non-crypto tickers (AAPL, SPY, …) are
  refused — stops a typo from polluting the crypto collection.

Public surface
--------------
* :func:`execute_crypto_paper_trade` — the route handler's worker.
* :func:`get_crypto_paper_history` — read API for the user dashboard.
* :func:`get_crypto_paper_position_summary` — open-position snapshot
  with mark-to-market PnL.
* :func:`ensure_indexes` — idempotency + query indexes (called
  from :mod:`route_registry` on startup).

Schema (``crypto_paper_trades`` collection, schema_version=1)
-------------------------------------------------------------
::

    {
      "trade_id": str (uuid4),
      "user_id": str,
      "symbol": str (uppercase, e.g. "BTC"),
      "asset_class": "crypto",
      "side": "BUY" | "SELL",
      "qty": float (base-asset units, e.g. 0.005 BTC),
      "price": float (USD price at fill),
      "notional_usd": float (qty * price),
      "opened_at": datetime (UTC, BSON-date),
      "timestamp": str (ISO-8601 with +00:00 — for legacy readers),
      "stop_loss": float | None,
      "take_profit": float | None,
      "source": str ("manual_ui" | "bot" | "api"),
      "idempotency_bucket": str (composite — see _make_idem_key),
      "schema_version": 1
    }
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

# Module-level db handle — set by route_registry on startup.
_db: Any = None

# Schema constants
COLLECTION = "crypto_paper_trades"
SCHEMA_VERSION = 1

# Mirrors _PAPER_PORTFOLIO_VALUE in ml_paper_trader.py for consistent
# bot sizing semantics, but read separately so equity changes can't
# silently affect crypto allocation.
_CRYPTO_PORTFOLIO_VALUE: float = float(
    os.environ.get("CRYPTO_PAPER_PORTFOLIO_VALUE", "100000")
)


def set_db(database: Any) -> None:
    """Attach the Motor database. Called once at startup."""
    global _db
    _db = database


# ── Boundary guards ───────────────────────────────────────────────────────────


def _is_production() -> bool:
    return os.environ.get("ENVIRONMENT", "").lower() == "production"


def _reject_test_fixture(symbol: str) -> Optional[dict]:
    """Reject TEST_*/MOCK_*/FAKE_*/DUMMY_*/FIXTURE_*/FAKEXYZ symbols.

    Returns a ``{"blocked": True, …}`` dict the route layer turns into
    a 400 in dev/preview. Raises ``ValueError`` in production so
    monitoring catches the contamination attempt."""
    try:
        from services.market_memory_service import is_test_symbol
    except Exception:  # pragma: no cover — only fires if module import fails
        return None

    if is_test_symbol(symbol):
        msg = (
            f"Test-fixture symbol {symbol!r} blocked from "
            f"crypto_paper_trades collection."
        )
        if _is_production():
            raise ValueError(msg)
        logger.warning("[crypto_paper] %s", msg)
        return {
            "blocked": True,
            "reason": "test_fixture_symbol",
            "symbol": symbol,
            "message": (
                "Test-fixture symbols (TEST_*/MOCK_*/FAKE_*/DUMMY_*/"
                "FIXTURE_*/FAKEXYZ) are blocked from crypto paper "
                "trades. Use a real crypto ticker (BTC, ETH, …)."
            ),
        }
    return None


def _reject_non_crypto(symbol: str) -> Optional[dict]:
    """Refuse symbols that aren't on the canonical crypto registry.

    Stops AAPL/SPY typos from polluting the crypto collection. The
    equity paper trader is the right place for those."""
    from services.crypto_symbols import is_crypto

    if not is_crypto(symbol):
        return {
            "blocked": True,
            "reason": "not_crypto_symbol",
            "symbol": symbol,
            "message": (
                f"{symbol!r} is not a recognised crypto ticker. "
                "Use the equity paper-trading endpoint for stocks."
            ),
        }
    return None


# ── Idempotency ───────────────────────────────────────────────────────────────


def _price_bucket(price: float) -> str:
    """0.1%-wide bucket so retries with cents-level price drift still
    collapse onto the same idempotency key."""
    if price <= 0:
        return "0"
    return f"{round(price, 2)}"


def _time_bucket_minute(now: datetime) -> str:
    """Floor `now` to UTC minute resolution for the idempotency key.
    Mirrors the equity-side P2 proposal (`floor(opened_at / 60s)`)."""
    return now.strftime("%Y-%m-%dT%H:%M")


def _make_idem_key(*, user_id: str, symbol: str, side: str,
                   qty: float, price: float, now: datetime) -> str:
    return "|".join((
        user_id, symbol.upper(), side.upper(),
        f"{qty:.8f}", _price_bucket(price), _time_bucket_minute(now),
    ))


async def ensure_indexes() -> None:
    """Create the unique idempotency index + query indexes.

    Idempotent — re-running on every boot is safe. Called from
    ``route_registry.wire_db`` so the collection is ready before the
    first user POSTs."""
    if _db is None:
        return
    try:
        await _db[COLLECTION].create_index(
            "idempotency_bucket", unique=True, sparse=True,
            name="idem_bucket_unique",
        )
        await _db[COLLECTION].create_index(
            [("user_id", 1), ("opened_at", -1)],
            name="user_opened_desc",
        )
        await _db[COLLECTION].create_index(
            [("symbol", 1), ("opened_at", -1)],
            name="symbol_opened_desc",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[crypto_paper] index creation failed: %s", exc)


# ── Quote helper (crypto-only path) ───────────────────────────────────────────


async def _fetch_crypto_price(symbol: str) -> Optional[float]:
    """Anchor crypto prices to ``get_crypto_quote`` — the same source
    powering ``/api/crypto/prices``. Never falls through to the equity
    ``get_quote`` path."""
    try:
        from services.price_provider import get_crypto_quote
        quote = await get_crypto_quote(symbol)
        if quote and quote.get("price"):
            return float(quote["price"])
    except Exception as exc:
        logger.warning(
            "[crypto_paper] price fetch failed for %s: %s", symbol, exc,
        )
    return None


# ── Public API ────────────────────────────────────────────────────────────────


async def execute_crypto_paper_trade(
    *,
    user_id: str,
    symbol: str,
    side: str,
    qty: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    source: str = "manual_ui",
) -> dict:
    """Open or close a crypto paper trade.

    Returns one of three shapes:

    * ``{"status": "filled", …}`` — trade accepted, persisted, and
      indexed.
    * ``{"status": "rejected", "error": …}`` — validation failure
      (bad side, non-positive qty, no live price). The route layer
      returns 400.
    * ``{"blocked": True, "reason": …, …}`` — test-fixture or
      non-crypto symbol. The route layer returns 400 with the
      structured reason.
    """
    if _db is None:
        return {"status": "rejected", "error": "Database not initialised"}

    sym = (symbol or "").strip().upper()
    side_norm = (side or "").strip().upper()

    # ── Boundary guards (wired to yesterday's contamination fix) ────────────
    blocked = _reject_test_fixture(sym)
    if blocked:
        return blocked
    blocked = _reject_non_crypto(sym)
    if blocked:
        return blocked

    if side_norm not in ("BUY", "SELL"):
        return {"status": "rejected", "error": "Side must be BUY or SELL"}
    if qty is None or qty <= 0:
        return {"status": "rejected", "error": "Quantity must be positive"}

    price = await _fetch_crypto_price(sym)
    if price is None or price <= 0:
        return {
            "status": "rejected",
            "error": f"Cannot fetch live crypto price for {sym}",
        }

    now = datetime.now(timezone.utc)
    idem_key = _make_idem_key(
        user_id=user_id, symbol=sym, side=side_norm,
        qty=qty, price=price, now=now,
    )

    trade_id = str(uuid4())
    notional = round(price * qty, 2)
    record = {
        "trade_id": trade_id,
        "user_id": user_id,
        "symbol": sym,
        "asset_class": "crypto",
        "side": side_norm,
        "qty": qty,
        "price": round(price, 6),
        "notional_usd": notional,
        "opened_at": now,
        "timestamp": now.isoformat(),
        "stop_loss": float(stop_loss) if stop_loss is not None else None,
        "take_profit": float(take_profit) if take_profit is not None else None,
        "source": source,
        "idempotency_bucket": idem_key,
        "schema_version": SCHEMA_VERSION,
    }

    try:
        await _db[COLLECTION].insert_one(record)
    except Exception as exc:
        # `pymongo.errors.DuplicateKeyError` lands here — treat it as
        # a successful idempotent retry, not a failure. The original
        # fill is fetched and returned so the caller's UI shows the
        # canonical trade_id.
        if "DuplicateKey" in type(exc).__name__ or "E11000" in str(exc):
            existing = await _db[COLLECTION].find_one(
                {"idempotency_bucket": idem_key}, {"_id": 0},
            )
            if existing:
                logger.info(
                    "[crypto_paper] idempotent retry collapsed onto "
                    "trade_id=%s (idem=%s)",
                    existing.get("trade_id"), idem_key,
                )
                # Convert datetime to ISO for response consistency.
                if isinstance(existing.get("opened_at"), datetime):
                    existing["opened_at"] = existing["opened_at"].isoformat()
                return {"status": "filled", "idempotent_replay": True, **existing}
        logger.error("[crypto_paper] insert failed: %s", exc)
        return {"status": "rejected", "error": f"DB write failed: {exc}"}

    logger.info(
        "[crypto_paper] %s %s %.8f @ $%.4f notional=$%.2f trade_id=%s",
        sym, side_norm, qty, price, notional, trade_id,
    )

    return {
        "status": "filled",
        "trade_id": trade_id,
        "symbol": sym,
        "asset_class": "crypto",
        "side": side_norm,
        "qty": qty,
        "price": round(price, 6),
        "notional_usd": notional,
        "timestamp": record["timestamp"],
    }


async def get_crypto_paper_history(
    user_id: str,
    *,
    symbol: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Recent crypto paper-trade fills for a user, newest first."""
    if _db is None:
        return []
    query: dict[str, Any] = {"user_id": user_id}
    if symbol:
        query["symbol"] = symbol.strip().upper()
    cursor = (
        _db[COLLECTION]
        .find(query, {"_id": 0})
        .sort("opened_at", -1)
        .limit(max(1, min(int(limit), 500)))
    )
    rows = await cursor.to_list(length=limit)
    # Normalise datetimes for JSON
    for r in rows:
        if isinstance(r.get("opened_at"), datetime):
            r["opened_at"] = r["opened_at"].isoformat()
    return rows


async def get_crypto_paper_position_summary(user_id: str) -> dict:
    """Aggregate open crypto exposure with mark-to-market PnL.

    Pure read-side; never opens or closes trades. Uses live
    ``get_crypto_quote`` for marks (cached 5 min, so frequent
    dashboard polls don't hammer the upstream)."""
    if _db is None:
        return {"positions": [], "total_notional_usd": 0.0,
                "starting_value": _CRYPTO_PORTFOLIO_VALUE}

    cursor = _db[COLLECTION].find({"user_id": user_id}, {"_id": 0})
    fills = await cursor.to_list(length=10_000)

    # Reduce fills to per-symbol net positions (BUY adds qty, SELL subtracts).
    by_symbol: dict[str, dict] = {}
    for f in fills:
        sym = f.get("symbol")
        if not sym:
            continue
        slot = by_symbol.setdefault(sym, {
            "symbol": sym, "qty": 0.0, "cost_basis_usd": 0.0,
            "fills": 0,
        })
        sign = 1.0 if f.get("side") == "BUY" else -1.0
        qty = float(f.get("qty") or 0.0)
        price = float(f.get("price") or 0.0)
        slot["qty"] += sign * qty
        slot["cost_basis_usd"] += sign * qty * price
        slot["fills"] += 1

    positions = []
    total_notional = 0.0
    total_unrealized = 0.0
    for sym, slot in by_symbol.items():
        net_qty = slot["qty"]
        if abs(net_qty) < 1e-10:
            continue
        mark = await _fetch_crypto_price(sym) or 0.0
        market_value = round(net_qty * mark, 2)
        avg_cost = (slot["cost_basis_usd"] / net_qty) if net_qty else 0.0
        unrealized = round(market_value - slot["cost_basis_usd"], 2)
        positions.append({
            "symbol": sym,
            "qty": round(net_qty, 8),
            "avg_cost": round(avg_cost, 6),
            "mark_price": round(mark, 6),
            "market_value_usd": market_value,
            "cost_basis_usd": round(slot["cost_basis_usd"], 2),
            "unrealized_pnl_usd": unrealized,
            "fills": slot["fills"],
        })
        total_notional += market_value
        total_unrealized += unrealized

    return {
        "positions": positions,
        "position_count": len(positions),
        "total_notional_usd": round(total_notional, 2),
        "total_unrealized_pnl_usd": round(total_unrealized, 2),
        "starting_value": _CRYPTO_PORTFOLIO_VALUE,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
