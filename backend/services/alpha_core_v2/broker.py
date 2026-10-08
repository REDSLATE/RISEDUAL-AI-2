"""Alpha Core v2 — broker port + Public adapter.

The broker is authoritative for money, positions, orders, fills AND the
execution-time quote. The port is a narrow protocol so MooMoo can plug in
later without the engine changing. Milestone 1 ships Public only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Protocol

from services.alpha_core_v2.contracts import (
    AccountState, ExecutionQuote, OrderResult, PositionState,
)

logger = logging.getLogger(__name__)


class BrokerPort(Protocol):
    def get_account(self) -> AccountState: ...
    def get_positions(self) -> list[PositionState]: ...
    def get_execution_quote(self, symbol: str) -> Optional[ExecutionQuote]: ...
    def get_open_orders(self, symbol: str) -> list[dict]: ...
    def submit(self, symbol: str, qty: float, side: str = "buy", *,
               client_order_id: str | None = None, close: bool = False) -> OrderResult: ...
    def get_order(self, order_id: str) -> OrderResult: ...


_FILLED = {"filled", "filled_all"}
_PARTIAL = {"partially_filled", "partial"}
_ACCEPTED = {"accepted", "new", "pending_new", "pending", "submitted", "open", "working"}
_DEAD = {"rejected", "canceled", "cancelled", "expired", "failed"}


def classify_status(raw_status: str) -> str:
    s = (raw_status or "").strip().lower()
    if s in _FILLED:
        return "filled"
    if s in _PARTIAL:
        return "partially_filled"
    if s in _ACCEPTED:
        return "accepted"
    if s in _DEAD:
        return "cancelled" if s == "canceled" else s
    return "unknown"


def _parse_ts(raw: Any) -> Optional[datetime]:
    """Best-effort parse of a broker quote timestamp → aware datetime.

    Returns ``None`` when the broker gives nothing parseable — the caller
    then treats freshness as UNKNOWN and BLOCKS, rather than silently
    assuming a zero-age quote (which would no-op the freshness interlock).
    """
    if raw is None or raw == "":
        return None
    # epoch seconds / milliseconds
    if isinstance(raw, (int, float)):
        val = float(raw)
        if val > 1e12:  # ms
            val /= 1000.0
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(raw).strip()
    try:
        if s.isdigit():
            val = float(s)
            if val > 1e12:
                val /= 1000.0
            return datetime.fromtimestamp(val, tz=timezone.utc)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OSError):
        return None


class PublicBroker:
    """Adapter over ``services.broker_service.PublicTradingService``."""

    def __init__(self, client: Any):
        self._c = client

    @classmethod
    async def from_db(cls, db: Any) -> Optional["PublicBroker"]:
        from services.public_equity_live_executor import (
            _aresolve_connect_creds, _public_client,
        )
        creds = await _aresolve_connect_creds(db)
        if not creds:
            return None
        client = _public_client(creds[0], creds[1])
        return cls(client) if client is not None else None

    def get_account(self) -> AccountState:
        try:
            a = self._c.get_account() or {}
            if not a:
                raise RuntimeError("public_account_unavailable")
            bp = float(a.get("buying_power") or 0.0)
            eq = float(a.get("equity") or bp or 0.0)
            cash = float(a.get("cash") or bp or 0.0)
            return AccountState(equity=eq, buying_power=bp, cash=cash, ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[core-v2:public] get_account failed: %s", exc)
            return AccountState(0.0, 0.0, 0.0, ok=False, error=str(exc)[:160])

    def get_positions(self) -> list[PositionState]:
        # Raises on failure — the engine treats an unknown holdings state as
        # FAIL-CLOSED (never opens blind).
        raw = self._c.get_positions(strict=True)
        out: list[PositionState] = []
        for p in raw:
            sym = (p.get("symbol") or p.get("instrument") or "").upper()
            if not sym:
                continue
            qty = float(p.get("qty") or p.get("quantity") or 0.0)
            side = (p.get("side") or "").lower() or ("long" if qty >= 0 else "short")
            out.append(PositionState(symbol=sym, qty=abs(qty), side=side))
        return out

    def get_execution_quote(self, symbol: str) -> Optional[ExecutionQuote]:
        """Fresh Public quote at execution time. None if missing/unusable."""
        try:
            q = self._c.get_quote(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[core-v2:public] get_quote %s failed: %s", symbol, exc)
            return None
        if not q:
            return None
        try:
            price = Decimal(str(q.get("price") or q.get("last") or "0"))
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not price.is_finite() or price <= 0:
            return None
        return ExecutionQuote(
            symbol=symbol.upper(), price=price,
            timestamp=_parse_ts(q.get("timestamp")),
            source=str(q.get("source") or "public"),
        )

    def get_open_orders(self, symbol: str) -> list[dict]:
        orders = self._c.get_orders(status="open", strict=True)
        return [o for o in orders
                if (o.get("instrument", {}).get("symbol") or o.get("symbol") or "").upper()
                == symbol.upper()]

    def submit(self, symbol: str, qty: float, side: str = "buy", *,
               client_order_id: str | None = None, close: bool = False) -> OrderResult:
        # Core routes and the scheduled exit worker must obey the same
        # hardware/session interlocks as the legacy executor.
        try:
            from services import alpha_hardware_kill_switch as hw
            from services.public_equity_live_executor import (
                _rth_only_enabled, _in_regular_session,
            )
            tripped, reason = hw.check()
            if tripped:
                return OrderResult(False, status="rejected", requested_qty=qty,
                                   error=f"hw_kill_switch_tripped:{reason}")
            if _rth_only_enabled() and not _in_regular_session():
                return OrderResult(False, status="rejected", requested_qty=qty,
                                   error="market_closed")
        except Exception as exc:
            return OrderResult(False, status="rejected", requested_qty=qty,
                               error=f"execution_interlock_unknown:{str(exc)[:120]}")
        try:
            resp = self._c.place_order(
                symbol=symbol, qty=qty, side=side, order_type="market",
                client_order_id=client_order_id,
                open_close_indicator="CLOSE" if close else "OPEN",
                use_margin=True if close and side.lower() == "buy" else None,
            )
        except Exception as exc:  # noqa: BLE001
            return OrderResult(ok=False, requested_qty=qty,
                               error=f"order_exception:{str(exc)[:140]}")
        if not resp:
            return OrderResult(ok=False, requested_qty=qty,
                               error="broker_empty_response")
        return _normalize_order(resp, requested_qty=qty)

    def get_order(self, order_id: str) -> OrderResult:
        try:
            resp = self._c.get_order(order_id)
        except Exception as exc:  # noqa: BLE001
            return OrderResult(ok=False, order_id=order_id,
                               error=f"get_order_exception:{str(exc)[:140]}")
        if not resp:
            return OrderResult(ok=False, order_id=order_id, error="order_not_found")
        result = _normalize_order(resp, requested_qty=0.0)
        result.order_id = result.order_id or order_id
        return result


def _normalize_order(resp: dict, *, requested_qty: float) -> OrderResult:
    oid = resp.get("id") or resp.get("order_id") or resp.get("orderId")
    status = classify_status(resp.get("status") or resp.get("state") or "")
    # A broker order id means the order WAS accepted. If the status vocabulary
    # is unrecognized but an id came back, treat it as accepted (lock + later
    # reconcile) — NOT failed — so a sibling cycle can't resubmit it.
    if status == "unknown" and oid:
        status = "accepted"
    filled = float(
        resp.get("filled_qty") or resp.get("filledQty")
        or resp.get("filledQuantity") or 0.0
    )
    price = float(
        resp.get("fillPrice") or resp.get("fill_price") or resp.get("avgPrice")
        or resp.get("averagePrice") or resp.get("price") or 0.0
    )
    ok = status in ("filled", "partially_filled", "accepted") and bool(oid) and not resp.get("error")
    return OrderResult(
        ok=ok, order_id=str(oid) if oid else None, status=status,
        filled_qty=filled, fill_price=price, requested_qty=requested_qty,
        raw=resp if isinstance(resp, dict) else {},
        error="" if ok else (f"broker_{status}" if oid else "broker_empty_response"),
    )
