"""Broker-agnostic options adapter interface.

Multi-broker options trading lives behind this interface. Concrete
adapters (Alpaca, Tradier, TastyTrade, IBKR) implement the same
surface so the rest of the app never has to branch on broker identity
— users connect whatever broker they have, the router dispatches
based on `user.broker.provider`, and callers just see the same
`place_option_order` / `get_option_positions` / etc. contract.

Design notes:
  * Single-leg first. Multi-leg spreads are future work — the
    `legs: list` signature is deliberate so we don't have to change
    the interface when we add them.
  * Returns dataclasses, not raw dicts. Gives us typed access + a
    single place to normalize broker response shapes.
  * `is_options_enabled()` is mandatory pre-flight. Users without
    options privileges on their broker must never see an actionable
    live-order UI.
  * ODD acceptance is enforced at the route layer (DB flag), not
    here — this interface deals with broker semantics only.

Extend order types / order classes deliberately. Every new value is
a cross-cutting change that every adapter must handle, so keep the
enum tight.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class OrderSide(str, Enum):
    """OCC-standard order sides.

    `BUY_TO_OPEN` + `SELL_TO_CLOSE` are the long-option flows
    (covered by Alpaca Level 1+). The short flows require margin
    tracking and higher options levels; included here so adapters
    can declare support without us reshaping the enum later.
    """
    BUY_TO_OPEN = "buy_to_open"
    SELL_TO_CLOSE = "sell_to_close"
    SELL_TO_OPEN = "sell_to_open"
    BUY_TO_CLOSE = "buy_to_close"


class OptionType(str, Enum):
    CALL = "call"
    PUT = "put"


class OrderStatus(str, Enum):
    """Superset of broker-specific order states. Adapters are
    responsible for mapping their raw status strings onto these
    canonical values."""
    NEW = "new"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class OptionLeg:
    """A single option leg.

    `occ_symbol` is the pre-built 21-char OCC symbol. Adapters
    that need broker-specific symbology translate this at the
    adapter boundary — callers don't care.
    """
    occ_symbol: str
    qty: int
    side: OrderSide


@dataclass
class OptionsEnabledStatus:
    enabled: bool
    level: int                              # 0–3 in Alpaca terms
    provider: str                           # "alpaca", "tradier", …
    details: str = ""                       # human-readable message
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class OptionsBuyingPower:
    cash: float
    options_buying_power: float             # what's actually usable for options
    multiplier: int = 100                   # OCC contract multiplier
    currency: str = "USD"


@dataclass
class OptionPosition:
    occ_symbol: str
    underlying: str
    strike: float
    expiration: str                         # ISO date
    option_type: OptionType
    qty: float                              # positive = long, negative = short
    avg_fill_price: float
    unrealized_pnl: float = 0.0


@dataclass
class OptionOrder:
    order_id: str
    status: OrderStatus
    legs: list[dict[str, Any]]              # broker-verbatim for debug
    qty: int
    filled_qty: int
    limit_price: Optional[float]
    time_in_force: str
    created_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)


class BrokerOptionsAdapter(ABC):
    """Contract every broker adapter implements.

    Concrete subclasses live in `services.brokers.<provider>_options`.
    """
    provider: str                           # "alpaca", "tradier", …

    @abstractmethod
    async def is_options_enabled(self) -> OptionsEnabledStatus:
        """Pre-flight check. MUST be called before the UI shows a
        live-order action."""
        ...

    @abstractmethod
    async def get_options_buying_power(self) -> OptionsBuyingPower:
        """Live account buying power snapshot. Used for sizing +
        route-side margin pre-flight."""
        ...

    @abstractmethod
    async def get_option_positions(self) -> list[OptionPosition]:
        """Current open option legs. Returns an empty list if the
        account has none — never raises on empty."""
        ...

    @abstractmethod
    async def place_option_order(
        self,
        legs: list[OptionLeg],
        *,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None,
    ) -> OptionOrder:
        """Submit an options order. Single-leg today; multi-leg is
        additive via the same `legs` list."""
        ...

    @abstractmethod
    async def cancel_option_order(self, order_id: str) -> bool:
        """Best-effort cancel. Returns False if the broker refused
        (already filled, unknown id). MUST NOT raise on a normal
        race condition."""
        ...


class BrokerNotImplementedError(NotImplementedError):
    """Raised by stub adapters so the route layer can distinguish
    'not yet implemented' from runtime errors and return a clean
    501 to the client."""


class StubOptionsAdapter(BrokerOptionsAdapter):
    """Placeholder for brokers we haven't wired up yet (Tradier,
    TastyTrade, IBKR in Phase 1). Every method raises
    `BrokerNotImplementedError` so the route layer surfaces a
    stable 501 — matches how `/api/admin/kill-switch/reset` handles
    permission-gated routes.

    Why ship this as a concrete class: registers the provider in the
    broker registry so the UI can *show* it as a connectable broker
    (with an "Live options coming soon" badge) while we build out
    the real adapter.
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider

    def _not_impl(self, what: str) -> BrokerNotImplementedError:
        return BrokerNotImplementedError(
            f"{self.provider} options {what} not yet implemented "
            "(coming in a later phase)"
        )

    async def is_options_enabled(self) -> OptionsEnabledStatus:
        return OptionsEnabledStatus(
            enabled=False,
            level=0,
            provider=self.provider,
            details=f"{self.provider} options support coming soon",
        )

    async def get_options_buying_power(self) -> OptionsBuyingPower:
        raise self._not_impl("buying power")

    async def get_option_positions(self) -> list[OptionPosition]:
        raise self._not_impl("positions")

    async def place_option_order(
        self,
        legs: list[OptionLeg],
        *,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None,
    ) -> OptionOrder:
        raise self._not_impl("order placement")

    async def cancel_option_order(self, order_id: str) -> bool:
        raise self._not_impl("order cancel")
