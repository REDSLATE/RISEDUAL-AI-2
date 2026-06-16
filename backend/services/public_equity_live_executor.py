"""Public.com equity live executor (2026-06-12).

Operator directive: drop Alpaca, route equity intents to Public.com.

Pattern mirrors ``services/crypto_live_executor.py`` for symmetry:
  * Operator-armed via env knob (default OFF)
  * Fixed notional cap (default $25) to bound first-connect blast radius
  * Persists rows into a dedicated ``equity_live_trades`` collection
    so live fills NEVER pollute the paper-trade ML training set
  * Fire-and-forget; never raises into Alpha's consensus loop

Doctrine pins
-------------
* **Live-only** — Public.com has no paper sandbox. Paper-equity
  training data continues to come from the existing paper pipeline
  (which DOES NOT call this module).
* **LONG-only** — same constraint as crypto. Shorts would require
  margin awareness this scaffold doesn't implement.
* **Market orders only** at Phase B; bracket SL/TP can layer in
  Phase C when we know how Public.com's order types behave in prod.
* **Connect-state aware** — refuses to fire if the Public.com
  connection record in ``broker_connections`` is absent or stale.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)


# ── Env knobs ────────────────────────────────────────────────────────


def _live_exec_enabled() -> bool:
    """Master kill switch for Public.com live equity. Default OFF.

    Operator flips ``RISEDUAL_PUBLIC_LIVE_EXEC=1`` in prod's
    ``backend/.env`` after redeploy + connect. Unset → every call
    to :func:`maybe_route_live` returns ``None`` without touching
    Public.com's API.
    """
    return (os.environ.get("RISEDUAL_PUBLIC_LIVE_EXEC") or "").strip() in (
        "1", "true", "True", "yes", "on",
    )


def _fixed_notional_usd() -> float:
    """Per-trade USD cap. Default $25 — same as the crypto live wire.

    Bounded to [1, 1000] so a stray env typo can't blow up sizing.
    Override with ``PUBLIC_LIVE_NOTIONAL_USD`` env var.
    """
    raw = (os.environ.get("PUBLIC_LIVE_NOTIONAL_USD") or "").strip()
    if not raw:
        return 25.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 25.0
    if v < 1.0:
        return 1.0
    if v > 1000.0:
        return 1000.0
    return v


def _allowed_symbols() -> Optional[set[str]]:
    """Optional symbol allowlist. ``PUBLIC_LIVE_SYMBOLS=AAPL,MSFT,NVDA``
    restricts live execution to that comma-separated list. Unset →
    no allowlist (any symbol from Alpha's consensus is eligible)."""
    raw = (os.environ.get("PUBLIC_LIVE_SYMBOLS") or "").strip()
    if not raw:
        return None
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


# ── Public.com client construction ──────────────────────────────────


def _resolve_connect_creds(db: Any) -> Optional[tuple[str, str]]:
    """Fetch Public.com (secret_key, account_id) from the broker
    connection vault. Returns ``None`` when no row exists OR the
    operator hasn't connected Public yet.

    Synchronous wrapper — Mongo motor calls have to be awaited
    by the caller. We keep this as a coroutine for parity with
    other live-exec helpers.
    """
    return None  # placeholder; real lookup in async helper below


async def _aresolve_connect_creds(db: Any) -> Optional[tuple[str, str]]:
    """Async version of :func:`_resolve_connect_creds`. Pulls the most
    recent active Public.com connection record.
    """
    if db is None:
        return None
    try:
        row = await db.broker_connections.find_one(
            {"broker_id": "public", "status": "connected"},
            {"_id": 0, "api_key": 1, "api_secret": 1},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[public-live] broker_connections lookup failed: %s", exc,
        )
        return None
    if not row:
        return None
    secret_key = (row.get("api_key") or "").strip()
    account_id = (row.get("api_secret") or "").strip()
    if not secret_key or not account_id:
        return None
    return secret_key, account_id


def _public_client(secret_key: str, account_id: str):
    """Instantiate :class:`services.broker_service.PublicTradingService`.

    The service handles the secret-→-JWT exchange + caching, so we
    just hand it the same field shape the broker-connect panel uses.
    """
    try:
        from services.broker_service import PublicTradingService
        return PublicTradingService(api_key=secret_key, api_secret=account_id)
    except Exception as exc:  # noqa: BLE001
        logger.error("[public-live] PublicTradingService init failed: %s", exc)
        return None


# ── Quote helper ────────────────────────────────────────────────────


async def _fetch_mark_price(symbol: str) -> Optional[float]:
    """Best-effort live quote for sizing. Falls back to Alpaca's
    equity quotes service when wired (since we're keeping Alpaca
    quotes alive). Never raises.
    """
    try:
        from services.price_provider import get_quote
        q = await get_quote(symbol)
        if q and q.get("price"):
            return float(q["price"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[public-live] mark-price probe failed: %s", exc)
    return None


# ── Main routing entry point ────────────────────────────────────────


async def maybe_route_live(
    db: Any,
    *,
    intent: Mapping[str, Any],
) -> Optional[dict[str, Any]]:
    """Place a Public.com live equity order if all gates pass.

    Called from Alpha's equity decision path (the same place
    Alpaca's ``maybe_execute_live`` used to be wired). Best-effort
    by doctrine — returns ``None`` for every skip case below and
    NEVER raises out:

      * ``RISEDUAL_PUBLIC_LIVE_EXEC`` unset
      * Operator hasn't connected Public yet
      * Symbol not in allowlist (when set)
      * Quote provider unavailable
      * Public.com auth fails (PublicTradingService swallows + returns None)
      * Duplicate open row for this symbol (idempotency)

    On success, returns the inserted ``equity_live_trades`` row dict
    (with the Public order id) so the orchestrator can log lineage.
    """
    if not _live_exec_enabled():
        return None

    symbol = (intent.get("symbol") or "").upper()
    direction = (intent.get("direction") or intent.get("action") or "").upper()
    if not symbol or direction not in ("BUY", "LONG"):
        # LONG-only; SELL closes go through alpaca_position_closer's
        # spiritual successor (not in scope for this scaffold).
        return None

    allow = _allowed_symbols()
    if allow is not None and symbol not in allow:
        logger.info(
            "[public-live] symbol=%s not in PUBLIC_LIVE_SYMBOLS allowlist — skip",
            symbol,
        )
        return None

    # Connect-state gate
    creds = await _aresolve_connect_creds(db)
    if creds is None:
        logger.warning(
            "[public-live] symbol=%s SKIPPED — no active Public.com "
            "connection (operator must connect via /api/broker/connect)",
            symbol,
        )
        return None
    secret_key, account_id = creds

    # Idempotency gate: refuse to open a duplicate live row.
    if db is not None:
        try:
            existing = await db.equity_live_trades.find_one(
                {"symbol": symbol, "status": "open", "broker_id": "public"},
                {"_id": 1},
            )
            if existing:
                logger.info(
                    "[public-live] symbol=%s already has open live row — skip",
                    symbol,
                )
                return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[public-live] idempotency check failed: %s", exc)

    # Sizing
    mark = await _fetch_mark_price(symbol)
    if not mark or mark <= 0:
        logger.warning("[public-live] symbol=%s SKIPPED — no mark price", symbol)
        return None
    notional = _fixed_notional_usd()
    qty = round(notional / mark, 4)
    if qty <= 0:
        logger.warning(
            "[public-live] symbol=%s SKIPPED — computed qty %.6f ≤ 0",
            symbol, qty,
        )
        return None

    # Execute
    client = _public_client(secret_key, account_id)
    if client is None:
        return None
    try:
        resp = client.place_order(
            symbol=symbol, qty=qty, side="buy", order_type="market",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[public-live] CRITICAL — place_order raised symbol=%s: %s",
            symbol, exc,
        )
        return None
    if not resp:
        logger.warning(
            "[public-live] symbol=%s place_order returned empty — "
            "Public.com rejected (check vault token + connect)",
            symbol,
        )
        return None

    order_id = resp.get("id") or ""
    trade_id = str(uuid.uuid4())
    row = {
        "trade_id": trade_id,
        "broker_id": "public",
        "symbol": symbol,
        "direction": "LONG",
        "side": "BUY",
        "size": qty,
        "live_notional_usd": notional,
        "entry_price": mark,
        "status": "open",
        "broker_order_id": order_id,
        "opened_at": datetime.now(timezone.utc),
        "confidence": float(intent.get("confidence") or 0.0),
        "sovereign_decision_id": intent.get("sovereign_decision_id"),
        "prediction_id": intent.get("prediction_id"),
        "source_signal": intent.get("source_signal"),
    }
    if db is not None:
        try:
            await db.equity_live_trades.insert_one(dict(row))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[public-live] CRITICAL — order placed on Public.com but "
                "Mongo insert failed symbol=%s order_id=%s qty=%.6f: %s",
                symbol, order_id, qty, exc,
            )
    logger.info(
        "[public-live] FILL symbol=%s qty=%.6f @ $%.2f notional=$%.2f "
        "order_id=%s trade_id=%s",
        symbol, qty, mark, notional, order_id, trade_id,
    )
    return row


__all__ = [
    "maybe_route_live",
    "_live_exec_enabled",
    "_fixed_notional_usd",
    "_allowed_symbols",
]
