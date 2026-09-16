"""Alpha Core v2 — broker port + Public adapter.

The broker is authoritative for money, positions, orders and fills. The port
is a narrow protocol so MooMoo can plug in later (Milestone 2) without the
engine changing. Milestone 1 ships Public only.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

from services.alpha_core_v2.contracts import (
    AccountState, OrderResult, PositionState,
)

logger = logging.getLogger(__name__)


class BrokerPort(Protocol):
    def get_account(self) -> AccountState: ...
    def get_positions(self) -> list[PositionState]: ...
    def submit(self, symbol: str, qty: float, side: str = "buy") -> OrderResult: ...
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
        return "rejected"
    return "unknown"


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
            bp = float(a.get("buying_power") or 0.0)
            eq = float(a.get("equity") or bp or 0.0)
            cash = float(a.get("cash") or bp or 0.0)
            return AccountState(equity=eq, buying_power=bp, cash=cash, ok=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[core-v2:public] get_account failed: %s", exc)
            return AccountState(0.0, 0.0, 0.0, ok=False, error=str(exc)[:160])

    def get_positions(self) -> list[PositionState]:
        # Raises on failure — the engine treats an unknown holdings state
        # as FAIL-CLOSED (never opens blind).
        raw = self._c.get_positions() or []
        out: list[PositionState] = []
        for p in raw:
            sym = (p.get("symbol") or p.get("instrument") or "").upper()
            if not sym:
                continue
            qty = float(p.get("qty") or p.get("quantity") or 0.0)
            side = (p.get("side") or "").lower() or ("long" if qty >= 0 else "short")
            out.append(PositionState(symbol=sym, qty=abs(qty), side=side))
        return out

    def submit(self, symbol: str, qty: float, side: str = "buy") -> OrderResult:
        try:
            resp = self._c.place_order(
                symbol=symbol, qty=qty, side=side, order_type="market",
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
        return _normalize_order(resp, requested_qty=0.0)


def _normalize_order(resp: dict, *, requested_qty: float) -> OrderResult:
    oid = resp.get("id") or resp.get("order_id") or resp.get("orderId")
    status = classify_status(resp.get("status") or resp.get("state") or "")
    filled = float(resp.get("filled_qty") or resp.get("filledQty") or 0.0)
    price = float(
        resp.get("fillPrice") or resp.get("fill_price")
        or resp.get("avgPrice") or resp.get("price") or 0.0
    )
    ok = status in ("filled", "partially_filled", "accepted") and bool(oid)
    return OrderResult(
        ok=ok, order_id=str(oid) if oid else None, status=status,
        filled_qty=filled, fill_price=price, requested_qty=requested_qty,
        raw=resp if isinstance(resp, dict) else {},
        error="" if ok else (f"broker_{status}" if oid else "broker_empty_response"),
    )
