"""
Alpha — broker-account context service.

Purpose
-------
Read the SAME live broker account Alpha already trades through, normalize the
account into a small model-safe snapshot, and expose that snapshot to Alpha's
decision layer.

This module never submits/cancels orders and never receives broker secrets.
The caller supplies Alpha's already-authenticated broker object.

Expected broker capabilities (duck-typed; aliases are supported):
  - get_account() / account()
  - list_positions() / positions()
  - list_open_orders() / open_orders()

If Alpha's broker wrapper uses different method names, add them to the alias
tuples below rather than giving the LLM direct broker access.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class PositionView:
    symbol: str
    qty: float
    side: str
    avg_cost: float
    market_price: float
    market_value: float
    unrealized_pl: float
    unrealized_pct: float


@dataclass(frozen=True)
class OpenOrderView:
    symbol: str
    side: str
    qty: float
    notional: float
    status: str


@dataclass(frozen=True)
class AccountContext:
    broker: str
    captured_at_ms: int
    equity: float
    cash: float
    buying_power: float
    positions: tuple[PositionView, ...]
    open_orders: tuple[OpenOrderView, ...]

    def to_model_payload(self) -> dict[str, Any]:
        # Deliberately excludes account numbers, tokens, IDs, and credentials.
        return {
            "broker": self.broker,
            "captured_at_ms": self.captured_at_ms,
            "equity": round(self.equity, 2),
            "cash": round(self.cash, 2),
            "buying_power": round(self.buying_power, 2),
            "positions": [asdict(p) for p in self.positions],
            "open_orders": [asdict(o) for o in self.open_orders],
        }


ACCOUNT_METHODS = ("get_account", "account", "get_portfolio", "portfolio")
POSITION_METHODS = ("list_positions", "positions", "get_positions")
OPEN_ORDER_METHODS = ("list_open_orders", "open_orders", "get_open_orders")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _pick(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return default


async def _call_first(obj: Any, aliases: Iterable[str], default: Any) -> Any:
    for name in aliases:
        fn = getattr(obj, name, None)
        if fn is None:
            continue
        value = fn()
        if inspect.isawaitable(value):
            value = await value
        return value
    return default


def _normalize_account(raw: Any) -> tuple[float, float, float]:
    d = raw if isinstance(raw, dict) else {}
    equity = _f(_pick(
        d,
        "equity", "portfolio_value", "net_liquidation_value",
        "total_net_liquidation_value", "total_equity",
    ))
    cash = _f(_pick(
        d,
        "cash", "settled_cash", "cash_balance", "total_cash_balance",
    ))
    bp = _f(_pick(
        d,
        "buying_power", "buyingPower", "daytrade_buying_power",
        "day_trading_buying_power",
    ))
    if bp <= 0:
        bp = cash
    if equity <= 0:
        equity = max(cash, bp)
    return equity, cash, bp


def _normalize_position(raw: Any) -> Optional[PositionView]:
    if not isinstance(raw, dict):
        return None
    symbol = str(_pick(raw, "symbol", "ticker", "asset", default="")).upper().strip()
    if not symbol:
        return None

    qty = _f(_pick(raw, "qty", "quantity", "shares"))
    avg = _f(_pick(raw, "avg_entry_price", "avg_cost", "average_price", "costPrice", "avgPrice"))
    mv = _f(_pick(raw, "market_value", "marketValue", "value"))
    px = _f(_pick(raw, "current_price", "market_price", "price", "last_price"))
    if px <= 0 and qty:
        px = abs(mv / qty) if mv else 0.0
    if mv == 0 and qty and px:
        mv = qty * px

    upl = _f(_pick(raw, "unrealized_pl", "unrealized_pnl", "unrealizedPnL"))
    cost_basis = abs(qty) * avg
    upl_pct = _f(_pick(raw, "unrealized_plpc", "unrealized_pct", "unrealized_percent"))
    if upl_pct == 0 and cost_basis:
        upl_pct = upl / cost_basis

    side = str(_pick(raw, "side", default="")).lower()
    if side not in {"long", "short"}:
        side = "long" if qty >= 0 else "short"

    return PositionView(
        symbol=symbol,
        qty=qty,
        side=side,
        avg_cost=avg,
        market_price=px,
        market_value=mv,
        unrealized_pl=upl,
        unrealized_pct=upl_pct,
    )


def _normalize_open_order(raw: Any) -> Optional[OpenOrderView]:
    if not isinstance(raw, dict):
        return None
    symbol = str(_pick(raw, "symbol", "ticker", default="")).upper().strip()
    if not symbol:
        return None
    return OpenOrderView(
        symbol=symbol,
        side=str(_pick(raw, "side", "action", default="")).upper(),
        qty=_f(_pick(raw, "qty", "quantity")),
        notional=_f(_pick(raw, "notional", "notional_usd", "amount")),
        status=str(_pick(raw, "status", "state", default="open")).lower(),
    )


class AccountContextService:
    """
    Small in-process cache so Alpha can consult account state on every decision
    without hammering the broker API.

    `ttl_seconds=10` is intentionally much shorter than a research cycle.
    Force-refresh immediately after a fill/cancel if Alpha already has that hook.
    """

    def __init__(self, broker: Any, *, broker_name: str = "broker", ttl_seconds: float = 10.0):
        self._broker = broker
        self._broker_name = broker_name
        self._ttl = max(1.0, float(ttl_seconds))
        self._lock = asyncio.Lock()
        self._cached: Optional[AccountContext] = None
        self._cached_at = 0.0

    async def get(self, *, force: bool = False) -> AccountContext:
        now = time.monotonic()
        if not force and self._cached and (now - self._cached_at) < self._ttl:
            return self._cached

        async with self._lock:
            now = time.monotonic()
            if not force and self._cached and (now - self._cached_at) < self._ttl:
                return self._cached

            raw_account, raw_positions, raw_orders = await asyncio.gather(
                _call_first(self._broker, ACCOUNT_METHODS, {}),
                _call_first(self._broker, POSITION_METHODS, []),
                _call_first(self._broker, OPEN_ORDER_METHODS, []),
            )

            equity, cash, buying_power = _normalize_account(raw_account)
            positions = tuple(
                p for p in (_normalize_position(x) for x in (raw_positions or [])) if p
            )
            orders = tuple(
                o for o in (_normalize_open_order(x) for x in (raw_orders or [])) if o
            )

            snapshot = AccountContext(
                broker=self._broker_name,
                captured_at_ms=int(time.time() * 1000),
                equity=equity,
                cash=cash,
                buying_power=buying_power,
                positions=positions,
                open_orders=orders,
            )
            self._cached = snapshot
            self._cached_at = time.monotonic()
            return snapshot
