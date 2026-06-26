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


def _live_confidence_floor() -> float:
    """Minimum signal confidence for live execution.

    2026-06-18: With peer-brain veto severed (MC2 standalone), the
    operator wants a hard confidence floor as the cheapest substitute.
    Default 0.65 — refuse to put real money on signals where Alpha's
    own conviction is below this line. Operator can tighten via
    ``PUBLIC_LIVE_CONFIDENCE_FLOOR=0.7`` etc.
    """
    raw = (os.environ.get("PUBLIC_LIVE_CONFIDENCE_FLOOR") or "").strip()
    if not raw:
        return 0.65
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.65
    # Bound to [0, 0.95] — anything ≥0.95 collides with the
    # toxic-spike saturation cap and silently disables live trading.
    if v < 0.0:
        return 0.0
    if v > 0.95:
        return 0.95
    return v


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

    Called from Alpha's equity decision path. Best-effort by doctrine
    — returns ``None`` for every skip case below and NEVER raises out:

      * ``RISEDUAL_PUBLIC_LIVE_EXEC`` unset
      * Operator hasn't connected Public yet
      * Symbol not in allowlist (when set)
      * Quote provider unavailable
      * Public.com auth fails (PublicTradingService swallows + returns None)
      * Duplicate open row for this symbol (idempotency)

    Two-sided routing (2026-06-26 — Alpaca removed from the stack):

      * **BUY/LONG** signal + no current position → OPEN long
      * **BUY/LONG** signal + already long → idempotency skip (dedup)
      * **SELL/SHORT** signal + currently long → CLOSE long (sell qty)
      * **SELL/SHORT** signal + no position → SKIP (Public is a cash
        broker — opening new shorts requires margin/locate which the
        $194 cash buying-power account doesn't support today)

    On success, returns the inserted ``equity_live_trades`` row dict
    (with the Public order id) so the orchestrator can log lineage.
    """
    if not _live_exec_enabled():
        return None

    symbol = (intent.get("symbol") or "").upper()
    direction = (intent.get("direction") or intent.get("action") or "").upper()
    if not symbol:
        return None

    # Direction classification → side + intent_kind.
    if direction in ("BUY", "LONG", "STRONG_BUY", "WEAK_BUY", "UP", "BULLISH"):
        intent_kind = "open_long"
    elif direction in ("SELL", "SHORT", "STRONG_SELL", "WEAK_SELL",
                       "DOWN", "BEARISH"):
        intent_kind = "close_long"
    else:
        logger.info(
            "[public-live] symbol=%s SKIPPED — non-directional signal: %r",
            symbol, direction,
        )
        return None

    # 2026-06-18: Confidence floor gate. Refuses live execution when
    # Alpha's own conviction is below ``PUBLIC_LIVE_CONFIDENCE_FLOOR``
    # (default 0.65). With the multi-brain peer veto severed in
    # standalone mode, this floor is the cheapest substitute. The 95%
    # saturation cap from earlier today caps the upper end; this
    # floor sets the lower end.
    confidence = float(intent.get("confidence") or 0.0)
    floor = _live_confidence_floor()
    if confidence < floor:
        logger.info(
            "[public-live] symbol=%s SKIPPED — confidence %.2f below floor %.2f",
            symbol, confidence, floor,
        )
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

    # 2026-06-18: Pre-trade cash check. Refuses live execution when
    # account doesn't have enough settled buying power. Prevents
    # noisy place_order rejections from Public.com when Alpha's
    # signal fans out faster than settled cash. Best-effort — if
    # the account-fetch fails for any reason we fall through to the
    # broker's own rejection (worst case = a logged 4xx).
    notional = _fixed_notional_usd()
    try:
        client_pre = _public_client(secret_key, account_id)
        if client_pre is not None:
            acct = client_pre.get_account()
            if acct is not None:
                bp = float(acct.get("buying_power") or 0.0)
                if bp < notional:
                    logger.warning(
                        "[public-live] symbol=%s SKIPPED — buying_power $%.2f "
                        "< notional $%.2f", symbol, bp, notional,
                    )
                    return None
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[public-live] pre-trade account check failed (non-fatal): %s",
            exc,
        )

    # Idempotency / direction-aware position check.
    # For OPEN_LONG: refuse to open a duplicate live row.
    # For CLOSE_LONG: require an actual open long position to close
    # (either tracked by us in ``equity_live_trades`` OR live on the
    # broker — operator may have manually bought a position).
    current_qty = 0.0
    existing_row = None
    if db is not None:
        try:
            existing_row = await db.equity_live_trades.find_one(
                {"symbol": symbol, "status": "open", "broker_id": "public"},
                {"_id": 1, "size": 1, "trade_id": 1},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[public-live] idempotency check failed: %s", exc)

    if intent_kind == "open_long" and existing_row is not None:
        logger.info(
            "[public-live] symbol=%s already has open live row — skip dupe",
            symbol,
        )
        return None

    if intent_kind == "close_long":
        # Ask the broker for the current position. Don't rely on the
        # Mongo row alone — the operator may have an untracked
        # position from before tracking started (SPCX/VRPX-style).
        try:
            client_pos = _public_client(secret_key, account_id)
            if client_pos is not None:
                positions = client_pos.get_positions() or []
                for p in positions:
                    if (p.get("symbol") or "").upper() == symbol:
                        try:
                            current_qty = float(p.get("qty") or 0.0)
                        except (TypeError, ValueError):
                            current_qty = 0.0
                        break
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[public-live] positions lookup failed (non-fatal): %s", exc,
            )
        if current_qty <= 0:
            logger.info(
                "[public-live] symbol=%s SKIPPED — SELL signal but no "
                "long position to close (Public cash account; opening "
                "new shorts not supported)", symbol,
            )
            return None

    # Sizing.
    # OPEN_LONG: use $1 notional → qty = notional / mark.
    # CLOSE_LONG: sell the full current position (qty already known
    # from the broker positions check above).
    mark = await _fetch_mark_price(symbol)
    if not mark or mark <= 0:
        logger.warning("[public-live] symbol=%s SKIPPED — no mark price", symbol)
        return None
    if intent_kind == "close_long":
        qty = current_qty
    else:
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
    order_side = "buy" if intent_kind == "open_long" else "sell"
    try:
        resp = client.place_order(
            symbol=symbol, qty=qty, side=order_side, order_type="market",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[public-live] CRITICAL — place_order raised symbol=%s "
            "kind=%s: %s", symbol, intent_kind, exc,
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
    if intent_kind == "close_long":
        # CLOSE: update the existing open row (if any) to ``closed``.
        # If there was no Mongo row but the position was real, still
        # log a synthetic close row so PnL can be reconciled later.
        close_doc = {
            "closed_at": datetime.now(timezone.utc),
            "close_price": mark,
            "close_order_id": order_id,
            "status": "closed",
            "close_reason": "alpha_sell_signal",
        }
        if db is not None and existing_row is not None:
            try:
                await db.equity_live_trades.update_one(
                    {"_id": existing_row["_id"]},
                    {"$set": close_doc},
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "[public-live] CRITICAL — sell order placed but Mongo "
                    "close-update failed symbol=%s order_id=%s: %s",
                    symbol, order_id, exc,
                )
        logger.info(
            "[public-live] CLOSE symbol=%s qty=%.6f @ $%.2f order_id=%s "
            "(SELL signal — closed long position)",
            symbol, qty, mark, order_id,
        )
        return {
            "trade_id": existing_row.get("trade_id") if existing_row else trade_id,
            "broker_id": "public",
            "symbol": symbol,
            "direction": "LONG",
            "side": "SELL",
            "intent_kind": "close_long",
            "size": qty,
            "close_price": mark,
            "status": "closed",
            "broker_order_id": order_id,
            "closed_at": datetime.now(timezone.utc),
            "confidence": float(intent.get("confidence") or 0.0),
            "source_signal": intent.get("source_signal"),
        }

    # OPEN_LONG path — original behaviour preserved.
    row = {
        "trade_id": trade_id,
        "broker_id": "public",
        "symbol": symbol,
        "direction": "LONG",
        "side": "BUY",
        "intent_kind": "open_long",
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
